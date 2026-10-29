"""`python3 server/serve.py --reload` (make dev): the server in a child process,
started afresh whenever a Python file it runs changes (G19: moved out of
serve.py).
"""

from __future__ import annotations

import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent


# what --reload watches: the server and every package it imports
WATCHED = ("serve.py", "finance.py", "finance", "analytics")


def python_files(root: Path = ROOT) -> dict[Path, float]:
    """Every watched Python file with its modification time."""
    out = {}
    for name in WATCHED:
        p = root / name
        for f in ([p] if p.is_file() else p.rglob("*.py") if p.is_dir() else []):
            try:
                out[f] = f.stat().st_mtime
            except OSError:              # removed between the walk and the stat
                pass
    return out


def port_answers(port: int) -> bool:
    """Whether something accepts a connection on 127.0.0.1:port."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def reload_forever(port: int, every: float = 1.0, page: str | None = None) -> None:
    """Run the server in a child process and start a fresh one whenever a
    watched file is added, removed or changed, so an edit or a `git pull`
    shows up without stopping anything by hand. A child that crashes (a
    syntax error mid-edit) is started again on the next change.

    A child that has stopped while something else answers on the port lost
    the port to another server, `make serve` in another checkout say. Vite
    would forward /api to that server and show this checkout's page against
    its Python, so the loop stops with an error (exit 1) and `make dev` with
    it."""
    argv = [sys.executable, str(ROOT / "serve.py"), "--port", str(port)] + (["--page", page] if page else [])

    def stop(signum, frame):
        raise KeyboardInterrupt
    # `make dev` stops its jobs with SIGTERM, and a shell's background job
    # ignores SIGINT, so both end the loop and take the child with them
    before = {sig: signal.signal(sig, stop) for sig in (signal.SIGTERM, signal.SIGINT)}
    seen = python_files()
    child = subprocess.Popen(argv)
    try:
        reported = False
        while True:
            time.sleep(every)
            code = child.poll()
            # checked on every tick the child is down, not once: another
            # checkout can take the port while this one waits for a fix
            if code is not None and port_answers(port):
                print(f"The API server stopped (exit {code}) and another program answers on port {port}, "
                      f"probably `make serve` in another checkout: /api would reach that server, so make dev "
                      f"stops. Stop it, or run make dev PORT=<another port>.", file=sys.stderr, flush=True)
                raise SystemExit(1)
            if code is not None and not reported:
                print(f"The API server stopped (exit {code}); it starts again on the next change",
                      file=sys.stderr, flush=True)
                reported = True
            now = python_files()
            if now == seen:
                continue
            seen = now
            print("Python files changed: restarting the server", flush=True)
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
            child = subprocess.Popen(argv)
            reported = False
    except KeyboardInterrupt:
        pass
    finally:
        # Ctrl-C reaches the whole group and the Makefile's trap sends SIGTERM
        # after it: the second signal must not cut short the wait for the child
        for sig in before:
            signal.signal(sig, signal.SIG_IGN)
        if child.poll() is None:
            child.terminate()
            child.wait()
        for sig, handler in before.items():
            signal.signal(sig, handler)
