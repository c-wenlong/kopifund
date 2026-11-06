"""G25: the paired phones' registry, `~/.kopifund/devices.json`.

The phone reaches the API through `tailscale serve`, which forwards to
loopback with `X-Forwarded-For` set; serve.py's link path then asks this
module who the bearer belongs to (SPEC.md, "Phone link (G25)"). The file
holds two kinds of secret as hashes only: the one-time pair tokens
Settings mints behind a QR (`mint_pair_token`), and each paired device's
bearer (`redeem`). A pair token's spend and the device row's write are one
`flock` critical section, so a token spends exactly once even raced.
KOPIFUND_DEVICES moves the file for tests.
"""
import fcntl
import hashlib
import hmac
import json
import os
import secrets
import subprocess
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from . import paths
from .atomic import write_private
from .errors import NotFound, Refused

PAIR_LIFE = 600          # a pair token lives ten minutes
PAIR_PREFIX = "kfp_"
DEVICE_PREFIX = "kfd_"


def devices_path():
    """The registry: KOPIFUND_DEVICES when set, else devices.json in the
    data folder (SPEC.md, "The data folder")."""
    override = os.environ.get("KOPIFUND_DEVICES")
    return Path(override).expanduser() if override else paths.data_root() / "devices.json"


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def _parse(text):
    try:
        data = json.loads(text)
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("pair_tokens", [])
    data.setdefault("devices", [])
    return data


@contextmanager
def _locked(exclusive):
    """A flock on a stable side file: os.replace swaps the registry's inode,
    so locking the registry itself would not hold off a second writer."""
    lock = devices_path().with_name(devices_path().name + ".lock")
    lock.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
    with os.fdopen(fd) as f:
        fcntl.flock(f, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def _read():
    """The registry under a shared lock; an absent file is an empty one."""
    path = devices_path()
    with _locked(exclusive=False):
        if not path.is_file():
            return _parse("")
        return _parse(path.read_text(encoding="utf-8"))


def _update(fn):
    """Read-modify-write the registry under an exclusive flock: `fn` gets
    the parsed dict, mutates it, and its return is this call's. The write
    lands through write_private, so a crash mid-write cannot
    leave a truncated registry for _parse to treat as a new one."""
    path = devices_path()
    with _locked(exclusive=True):
        data = _parse(path.read_text(encoding="utf-8")) if path.is_file() else _parse("")
        out = fn(data)
        write_private(path, json.dumps(data, indent=2) + "\n")
        return out


def _iso(ts):
    return datetime.fromtimestamp(ts).isoformat(timespec="seconds")


# ---------------------------------------------------------------- pair tokens

def mint_pair_token():
    """A single-use pair token with a ten-minute life, saved hashed; the
    plaintext goes into the QR and is never written down. Stale tokens are
    swept as each new one is minted; a spent one stays until its life ends,
    so the phone that spent it can still retry (redeem's nonce)."""
    token = PAIR_PREFIX + secrets.token_hex(16)
    expires = time.time() + PAIR_LIFE

    def add(data):
        data["pair_tokens"] = [t for t in data["pair_tokens"] if t["expires"] > time.time()]
        data["pair_tokens"].append({"hash": _hash(token), "expires": expires, "used": False})
    _update(add)
    return token, expires


def redeem(token, name="", nonce=""):
    """Spend a pair token for a device bearer. Refused when the token is
    unknown, spent or stale; the consumption and the device row's write are
    one critical section so a token spends exactly once.

    `nonce` is a random the phone makes once per pairing and sends again on
    a retry. With one, the bearer is derived from the token and the nonce,
    so a retry after a lost reply (the token already spent, still in its
    life, the same nonce) answers the very bearer of the device the first
    try made, whichever of the two requests lands first: a lost reply
    leaves no second, orphan device, and no late request can change a
    bearer the phone already saved. A token seen in a QR is still no use
    to anyone else once spent: they would need the phone's nonce too."""
    if not token:
        raise Refused("A pair token is needed")
    if nonce and not 16 <= len(nonce) <= 200:
        raise ValueError("nonce is 16 to 200 characters")
    now = time.time()
    holder = {}

    def spend(data):
        for t in data["pair_tokens"]:
            if t.get("expires", 0) <= now or not hmac.compare_digest(t.get("hash", ""), _hash(token)):
                continue
            bearer = DEVICE_PREFIX + (hashlib.sha256(f"{token}:{nonce}".encode()).hexdigest()[:48]
                                      if nonce else secrets.token_hex(24))
            if not t.get("used"):
                t["used"] = True
                device = {"id": "dev_" + secrets.token_hex(8), "name": name or "iPhone",
                          "token_hash": _hash(bearer), "paired_at": _iso(now), "revoked": False}
                data["devices"].append(device)
                if nonce:
                    t.update(nonce_hash=_hash(nonce), device_id=device["id"])
                holder.update(bearer=bearer, device=device)
                return
            if nonce and t.get("nonce_hash") and hmac.compare_digest(t["nonce_hash"], _hash(nonce)):
                for device in data["devices"]:
                    if device["id"] == t.get("device_id") and not device.get("revoked"):
                        holder.update(bearer=bearer, device=device)
                        return
        raise Refused("That pair token is spent or stale: mint a new one on the Mac")
    _update(spend)
    return holder["bearer"], holder["device"]


def pair_payload(url, token):
    """The QR's text: a kopifund://pair deep link carrying the tailnet URL
    and the one-time token."""
    return f"kopifund://pair?url={quote(url, safe='')}&token={quote(token, safe='')}"


# ---------------------------------------------------------------- devices

def device_for(bearer):
    """The paired device a bearer names, or None: missing, wrong, or on a
    revoked row all answer None."""
    if not bearer or not bearer.startswith(DEVICE_PREFIX):
        return None
    hashed = _hash(bearer)
    for d in _read()["devices"]:
        if not d.get("revoked") and hmac.compare_digest(d.get("token_hash", ""), hashed):
            return d
    return None


def list_devices():
    """The registry as the API sends it: the hash never leaves the file."""
    return [{"id": d.get("id", ""), "name": d.get("name", ""), "paired_at": d.get("paired_at", ""),
             "revoked": bool(d.get("revoked"))}
            for d in _read()["devices"]]


def revoke(device_id):
    """Flag a device row: a lost phone loses its access without a token
    round. NotFound when no row has the id."""
    def flag(data):
        for d in data["devices"]:
            if d["id"] == device_id:
                d["revoked"] = True
                return True
        return False
    if not _update(flag):
        raise NotFound(f"no device {device_id}")


# ---------------------------------------------------------------- the tailnet

def tailnet_url():
    """This machine's tailnet name as an https URL, or "" when tailscale
    cannot say: not installed, not signed in, or no MagicDNS name."""
    try:
        out = subprocess.run(["tailscale", "status", "--json"],
                             capture_output=True, text=True, timeout=5)
        name = (json.loads(out.stdout or "{}").get("Self") or {}).get("DNSName", "")
        return f"https://{name.rstrip('.')}" if name else ""
    except (OSError, ValueError, subprocess.SubprocessError):
        return ""
