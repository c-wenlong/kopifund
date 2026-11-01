"""`python3 server/serve.py --reload` (make dev): the server in a child process,
started afresh whenever a Python file it runs changes (G19: moved out of
serve.py).
"""

from __future__ import annotations

import signal
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


def reload_forever(port: int, every: float = 1.0, page: str | None = None) -> None:
    """Run the server in a child process and start a fresh one whenever a
    watched file is added, removed or changed, so an edit or a `git pull`
    shows up without stopping anything by hand. A child that crashes (a
    syntax error mid-edit) is started again on the next change."""
    argv = [sys.executable, str(ROOT / "serve.py"), "--port", str(port)] + (["--page", page] if page else [])

    def stop(signum, frame):
        raise KeyboardInterrupt
    # `make dev` stops its jobs with SIGTERM, and a shell's background job
    # ignores SIGINT, so both end the loop and take the child with them
    before = {sig: signal.signal(sig, stop) for sig in (signal.SIGTERM, signal.SIGINT)}
    seen = python_files()
    child = subprocess.Popen(argv)
    try:
        while True:
            time.sleep(every)
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
    except KeyboardInterrupt:
        pass
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait()
        for sig, handler in before.items():
            signal.signal(sig, handler)
