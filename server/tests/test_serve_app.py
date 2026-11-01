import isolate  # noqa: F401  first: no test writes the real record or cache
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

import serve
from finance import app_icon, monthly
from finance.paths import cache_dir

ROOT = Path(__file__).resolve().parent.parent


class AppSidecarTest(unittest.TestCase):
    """G23: serve.py --app as the desktop shell runs it."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # an empty data folder: the sidecar never sees the owner's record
        self.env = {**os.environ, "KOPIFUND_DATA": self.tmp.name}

    def start(self, *args):
        child = subprocess.Popen([sys.executable, "serve.py", "--app", *args], cwd=ROOT, env=self.env,
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 text=True)
        self.addCleanup(lambda: child.poll() is None and child.kill())
        self.addCleanup(child.stdout.close)
        self.addCleanup(child.stderr.close)
        return child

    def test_the_fixed_port_is_clear_of_make_serve(self):
        self.assertEqual(serve.APP_PORT, 8771)
        self.assertNotEqual(serve.APP_PORT, serve.PORT)

    def test_prints_ready_once_listening_and_stops_when_stdin_closes(self):
        child = self.start("--port", "0")
        word, address = child.stdout.readline().split()
        self.assertEqual(word, serve.READY)
        self.assertRegex(address, r"^http://127\.0\.0\.1:\d+$")
        with urllib.request.urlopen(f"{address}/api/health", timeout=5) as r:
            self.assertEqual(r.status, 200)
        child.stdin.close()
        self.assertEqual(child.wait(timeout=10), 0)

    def test_prints_the_app_icon_as_it_starts_and_after_every_pick(self):
        self.env["KOPIFUND_DB"] = str(Path(self.tmp.name) / "kopifund.db")
        reg = app_icon.registry()
        other = next(s["name"] for s in reg["sets"] if s["name"] != reg["default"])
        child = self.start("--port", "0")
        address = child.stdout.readline().split()[1]
        self.assertEqual(child.stdout.readline().split(), [serve.ICON, reg["default"]])
        pick = urllib.request.Request(f"{address}/api/app-icon", data=json.dumps({"name": other}).encode(),
                                      headers={"Content-Type": "application/json", "X-Kopifund": "1"})
        with urllib.request.urlopen(pick, timeout=5) as r:
            self.assertEqual(json.load(r)["chosen"], other)
        self.assertEqual(child.stdout.readline().split(), [serve.ICON, other])
        child.stdin.close()
        self.assertEqual(child.wait(timeout=10), 0)

    def test_a_taken_port_is_named_and_the_server_exits(self):
        with socket.socket() as taken:
            taken.bind(("127.0.0.1", 0))
            taken.listen()
            port = taken.getsockname()[1]
            child = self.start("--port", str(port))
            self.assertEqual(child.wait(timeout=10), 1)
            self.assertEqual(child.stdout.read(), "")
            self.assertIn(f"127.0.0.1:{port}", child.stderr.read())


HOLD = """
import fcntl, sys
f = open(sys.argv[1], "a")
fcntl.flock(f, fcntl.LOCK_EX)
print("held", flush=True)
sys.stdin.read()
"""


class SharedRebuildLockTest(unittest.TestCase):
    """G23: a rebuild waits while another server, another process, rebuilds."""

    def setUp(self):
        old = monthly._REBUILD[0]
        self.addCleanup(monthly.rebuild_stats_with, old)

    def test_a_rebuild_waits_for_the_lock_another_process_holds(self):
        ran = threading.Event()
        monthly.rebuild_stats_with(lambda cfg: ran.set())
        cache_dir().mkdir(parents=True, exist_ok=True)
        holder = subprocess.Popen([sys.executable, "-c", HOLD, str(cache_dir() / monthly.REBUILD_LOCK)],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        self.addCleanup(lambda: holder.poll() is None and holder.kill())
        self.addCleanup(holder.stdout.close)
        self.assertEqual(holder.stdout.readline().strip(), "held")
        worker = threading.Thread(target=monthly.analyse, args=({},), daemon=True)
        worker.start()
        self.assertFalse(ran.wait(0.5))
        holder.stdin.close()
        self.assertTrue(ran.wait(5))
        worker.join(5)
        holder.wait(5)


if __name__ == "__main__":
    unittest.main()
