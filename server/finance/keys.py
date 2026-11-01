"""Where Settings keeps an AI provider's API key (G22).

The AI card on Settings takes a key and hands it here; nothing else in the app
ever holds it, and the page only ever gets back where it is kept and its last
four characters (server/finance/ai.py, `key_view`). Two places, picked by the machine:

- the macOS Keychain, as a generic password with service
  `kopifund.ai.<provider>` and account `kopifund`. The key is written through
  `security -i`, which reads its command from standard input, so the key is
  never in a process list the way an argument would be;
- with no keychain (another system, or no `security` binary), a
  `<PROVIDER>_API_KEY=` line in `.env` in the data folder, mode 600, which the
  key lookup in server/finance/ai.py already reads and backups never include.

The backend is injectable (`use`), and `KOPIFUND_NO_KEYCHAIN` makes the
Keychain backend refuse to run the real `security` binary at all: the test
suite sets it (server/tests/isolate.py), so no test can write to the owner's
Keychain. Nothing here logs or returns a key except `read`.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path

try:
    import fcntl
except ImportError:              # not on Windows: the thread lock alone keeps one server's saves apart
    fcntl = None

from . import dotenv
from .paths import data_root

ACCOUNT = "kopifund"
# what a pasted key may hold: printable ASCII with no space or quote, which
# every provider's keys are, and which `security -i` reads back unchanged
_KEY = re.compile(r"[!#-&(-\[\]-~]+")
MIN_LENGTH, MAX_LENGTH = 8, 400


class KeyStoreError(Exception):
    """The key could not be saved, read or removed, in words for the app."""


def service(provider):
    return f"kopifund.ai.{provider}"


def env_name(provider):
    """The `.env` variable a provider's key is saved under."""
    return re.sub(r"[^A-Z0-9]", "_", provider.upper()) + "_API_KEY"


def check(key):
    """A key as typed, trimmed, or KeyStoreError saying why it is not one."""
    key = (key or "").strip() if isinstance(key, str) else ""
    if not key:
        raise KeyStoreError("Paste the API key first")
    if len(key) < MIN_LENGTH:
        raise KeyStoreError("That is too short to be an API key")
    if len(key) > MAX_LENGTH:
        raise KeyStoreError("That is too long to be an API key")
    if not _KEY.fullmatch(key):
        raise KeyStoreError("An API key has no spaces, quotes or line breaks; paste it again")
    return key


def last4(key):
    return key[-4:] if key else None


# ---------------------------------------------------------------- the Keychain

def _security(args, stdin=None):
    """Run the real `security` binary. G22: refused outright while
    KOPIFUND_NO_KEYCHAIN is set, which the test suite always sets."""
    if os.environ.get("KOPIFUND_NO_KEYCHAIN"):
        raise KeyStoreError("The Keychain is off here (KOPIFUND_NO_KEYCHAIN)")
    exe = shutil.which("security")
    if not exe:
        raise KeyStoreError("This Mac has no `security` command to reach the Keychain")
    try:
        return subprocess.run([exe, *args], input=stdin, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise KeyStoreError(f"The Keychain did not answer: {e.__class__.__name__}") from None


class Keychain:
    """The macOS Keychain through `security`. `run(args, stdin)` returns a
    CompletedProcess; tests pass a fake, so the real binary never runs."""
    place = "keychain"

    def __init__(self, run=None):
        self.run = run or _security

    def save(self, provider, key):
        key = check(key)
        # -U updates an existing item in place; the command, key and all, goes on stdin
        command = f'add-generic-password -U -a {ACCOUNT} -s {service(provider)} -l "Kopifund {provider} API key" ' \
                  f'-w "{key}"\n'
        r = self.run(["-i"], command)
        if r.returncode != 0 or self.read(provider) != key:
            raise KeyStoreError("The Keychain did not keep the key; try again, or unlock the Keychain")

    def read(self, provider):
        r = self.run(["find-generic-password", "-a", ACCOUNT, "-s", service(provider), "-w"], None)
        if r.returncode != 0:
            return None
        return (r.stdout or "").strip() or None

    def remove(self, provider):
        r = self.run(["delete-generic-password", "-a", ACCOUNT, "-s", service(provider)], None)
        # 44: no such item, which is what a remove wants
        if r.returncode not in (0, 44):
            raise KeyStoreError("The Keychain did not remove the key")


# ---------------------------------------------------------------- .env

def env_file():
    """The data folder's `.env`. KOPIFUND_ENV_FILE moves it, which the test
    suite does so that no test writes a real data folder's file."""
    override = os.environ.get("KOPIFUND_ENV_FILE")
    return Path(override).expanduser() if override else data_root() / ".env"


def _assigns(line, name):
    stripped = line.strip()
    if stripped.startswith("export ") or stripped.startswith("export\t"):
        stripped = stripped[len("export"):].lstrip()
    return stripped.partition("=")[0].strip() == name and "=" in stripped


# G22: one update of a `.env` at a time: a thread lock in this server, and a
# flock on a sibling lock file across servers (`make serve` and the desktop app)
_ENV_LOCK = threading.Lock()


@contextmanager
def _locked(path):
    """Hold the `.env` update lock for `path` from its read to its replace,
    so two saves (two Settings tabs, two providers) never drop each other's line."""
    with _ENV_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path.parent / f".{path.name}.lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)                 # closing it lets the flock go


class EnvFile:
    """`<PROVIDER>_API_KEY=` lines in a `.env` file kept at mode 600. A save
    replaces the provider's line (every copy of it) and keeps every other line;
    each update holds the lock from its read to its write (_locked)."""
    place = "env_file"

    def __init__(self, path=None):
        self._path = Path(path) if path else None

    @property
    def path(self):
        return self._path or env_file()

    def _lines(self):
        try:
            return self.path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return []
        except (OSError, UnicodeDecodeError) as e:
            raise KeyStoreError(f"Could not read {self.path.name}: {e.__class__.__name__}") from None

    def _write(self, lines):
        path = self.path
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".env-", suffix=".tmp")   # mkstemp makes it 600
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write("".join(line + "\n" for line in lines))
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
        os.chmod(path, 0o600)

    def save(self, provider, key):
        key = check(key)
        name = env_name(provider)
        with _locked(self.path):
            lines, placed = [], False
            for line in self._lines():
                if _assigns(line, name):
                    if not placed:
                        lines.append(f"{name}={key}")
                        placed = True
                    continue
                lines.append(line)
            if not placed:
                lines.append(f"{name}={key}")
            self._write(lines)

    def read(self, provider):
        return dotenv.read(self.path).get(env_name(provider)) or None

    def remove(self, provider):
        with _locked(self.path):
            lines = self._lines()
            kept = [line for line in lines if not _assigns(line, env_name(provider))]
            if kept != lines:
                self._write(kept)


# ---------------------------------------------------------------- which one

_OVERRIDE = None


def use(store):
    """Put `store` in place of the machine's own (None puts that back); returns
    the one it replaced. Tests use it to hand in a fake."""
    global _OVERRIDE
    before, _OVERRIDE = _OVERRIDE, store
    return before


def keychain_available():
    return sys.platform == "darwin" and bool(shutil.which("security")) and not os.environ.get("KOPIFUND_NO_KEYCHAIN")


def store():
    """Where Settings keeps keys on this machine."""
    if _OVERRIDE is not None:
        return _OVERRIDE
    return Keychain() if keychain_available() else EnvFile()


def save(provider, key):
    store().save(provider, key)


def read(provider):
    """(key, place) for the key Settings saved for `provider`, or (None, None)."""
    s = store()
    try:
        key = s.read(provider)
    except KeyStoreError:
        return None, None
    return (key, s.place) if key else (None, None)


def remove(provider):
    """Take the provider's key out of where Settings keeps it, and out of the
    data folder's `.env`, the other place Settings writes, so Remove leaves no
    saved copy behind."""
    s = store()
    s.remove(provider)
    if not isinstance(s, EnvFile):
        EnvFile().remove(provider)
