import isolate  # noqa: F401  first: no test writes the real record or cache
import io
import json
import os
import tempfile
import threading
import unittest
from unittest import mock
import urllib.error
import urllib.request
from pathlib import Path

import dev_reload
import serve


class ServeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        dist = Path(cls.tmp.name) / "dist"
        (dist / "assets").mkdir(parents=True)
        (dist / "index.html").write_text("<div id=root></div>")
        (dist / "assets" / "app.js").write_text("console.log(1)")
        (Path(cls.tmp.name) / "secret.txt").write_text("outside dist")
        cls.server = serve.make_server(0, dist)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def get(self, path, method="GET"):
        req = urllib.request.Request(self.base + path, method=method)
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, r.headers, r.read().decode()
        except urllib.error.HTTPError as e:
            with e:
                return e.code, e.headers, ""

    def status_with_host(self, host, method="GET"):
        from http.client import HTTPConnection
        conn = HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=10)
        try:
            conn.putrequest(method, "/api/health", skip_host=True)
            conn.putheader("Host", host)
            if method == "POST":
                conn.putheader("X-Kopifund", "1")
                conn.putheader("Content-Length", "0")
            conn.endheaders()
            return conn.getresponse().status
        finally:
            conn.close()

    def test_a_foreign_host_is_refused(self):
        port = self.server.server_address[1]
        self.assertEqual(self.status_with_host(f"evil.example:{port}"), 421)
        self.assertEqual(self.status_with_host(f"evil.example:{port}", "POST"), 421)

    def test_this_machine_by_name_is_answered(self):
        port = self.server.server_address[1]
        for host in (f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"):
            self.assertEqual(self.status_with_host(host), 200, host)

    def test_a_name_in_kopifund_hosts_is_answered(self):
        # Docker: the owner opens the page on a home server under its own name
        port = self.server.server_address[1]
        with mock.patch.dict(os.environ, {"KOPIFUND_HOSTS": "homeserver.local, 192.168.1.20"}):
            self.assertEqual(self.status_with_host(f"homeserver.local:{port}"), 200)
            self.assertEqual(self.status_with_host(f"192.168.1.20:{port}"), 200)
            self.assertEqual(self.status_with_host(f"evil.example:{port}"), 421)
            self.assertTrue(serve._local_origin("http://homeserver.local:8770"))
            self.assertFalse(serve._local_origin("http://evil.example"))
        self.assertEqual(self.status_with_host(f"homeserver.local:{port}"), 421)

    def test_binds_loopback_only(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")

    def test_health(self):
        status, headers, body = self.get("/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["ok"], True)
        self.assertEqual(headers["Cache-Control"], "no-store")

    def test_setup_providers_name_a_wallet_currency_where_there_is_one(self):
        status, _, body = self.get("/api/setup")
        self.assertEqual(status, 200)
        providers = {p["id"]: p for p in json.loads(body)["providers"]}
        self.assertEqual(providers["youtrip-usd"]["wallet"], "USD")
        self.assertEqual(providers["wise-sgd"]["wallet"], "SGD")
        self.assertNotIn("wallet", providers["posb-everyday"])

    def test_the_countries_come_without_a_record(self):
        status, headers, body = self.get("/api/countries")
        self.assertEqual(status, 200)
        rows = json.loads(body)["countries"]
        self.assertEqual({"code": "MYS", "alpha2": "MY", "flag": "\U0001F1F2\U0001F1FE"},
                         next(r for r in rows if r["code"] == "MYS"))
        self.assertEqual([r["code"] for r in rows], sorted(r["code"] for r in rows))

    def test_unknown_api_is_404_json(self):
        status, _, _ = self.get("/api/nope")
        self.assertEqual(status, 404)

    def test_app_pages_fall_back_to_index(self):
        for path in ("/", "/settings", "/net-worth"):
            status, _, body = self.get(path)
            self.assertEqual((status, body), (200, "<div id=root></div>"), path)

    def test_assets_are_served(self):
        status, headers, body = self.get("/assets/app.js")
        self.assertEqual((status, body), (200, "console.log(1)"))
        self.assertIn("javascript", headers["Content-Type"])

    def test_missing_asset_is_404(self):
        self.assertEqual(self.get("/assets/missing.js")[0], 404)

    def test_cannot_walk_out_of_dist(self):
        self.assertEqual(self.get("/..%2fsecret.txt")[0], 404)
        self.assertEqual(self.get("/%2e%2e/secret.txt")[0], 404)

    def test_a_pin_without_the_app_header_is_refused(self):
        req = urllib.request.Request(self.base + "/api/rules/pin", method="POST", data=b"{}")
        with self.assertRaises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req)
        with e.exception:
            self.assertEqual(e.exception.code, 403)

    def test_rule_edits_need_the_app_header_and_this_machine(self):
        for path in ("/api/rules/save", "/api/rules/undo", "/api/rules/remove"):
            for headers in ({}, {"X-Kopifund": "1", "Origin": "https://evil.example"}):
                req = urllib.request.Request(self.base + path, method="POST", data=b"{}", headers=headers)
                with self.assertRaises(urllib.error.HTTPError, msg=(path, headers)) as e:
                    urllib.request.urlopen(req)
                self.assertEqual(e.exception.code, 403, (path, headers))
                e.exception.close()

    def test_writes_are_refused(self):
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            self.assertEqual(self.get("/api/health", method)[0], 405, method)



class DevPageTest(unittest.TestCase):
    """Under make dev, --page sends every page to Vite, so apps/web/dist's old build
    is never what the browser shows; the API still answers here."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        dist = Path(cls.tmp.name) / "dist"
        dist.mkdir()
        (dist / "index.html").write_text("an old build")
        cls.server = serve.make_server(0, dist, page="http://localhost:5173/")
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def ask(self, path):
        from http.client import HTTPConnection
        conn = HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request("GET", path)
            r = conn.getresponse()
            return r.status, r.getheader("Location"), r.read().decode()
        finally:
            conn.close()

    def test_a_page_is_sent_to_vite_with_its_path_and_query(self):
        self.assertEqual(self.ask("/net-worth?x=1")[:2], (307, "http://localhost:5173/net-worth?x=1"))
        self.assertEqual(self.ask("/")[:2], (307, "http://localhost:5173/"))

    def test_an_encoded_path_keeps_its_meaning(self):
        self.assertEqual(self.ask("/example%3Fname?x=1")[1], "http://localhost:5173/example%3Fname?x=1")

    def test_the_old_build_is_never_served(self):
        self.assertNotIn("an old build", self.ask("/")[2])

    def test_the_api_still_answers_here(self):
        status, location, _ = self.ask("/api/health")
        self.assertEqual(status, 200)
        self.assertIsNone(location)


class ReloadWatchTest(unittest.TestCase):
    """--reload restarts on any change to the files python_files() returns."""

    def test_sees_an_edit_an_added_file_and_a_removed_one(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "finance").mkdir()
            (root / "serve.py").write_text("x = 1\n")
            mod = root / "finance" / "a.py"
            mod.write_text("y = 1\n")
            (root / "finance" / "notes.txt").write_text("not python\n")
            first = dev_reload.python_files(root)
            self.assertEqual(set(first), {root / "serve.py", mod})

            os.utime(mod, (first[mod] + 5, first[mod] + 5))
            self.assertNotEqual(dev_reload.python_files(root), first)

            (root / "finance" / "b.py").write_text("z = 1\n")
            self.assertIn(root / "finance" / "b.py", dev_reload.python_files(root))

            mod.unlink()
            self.assertNotIn(mod, dev_reload.python_files(root))


class ReloadLoopTest(unittest.TestCase):
    """reload_forever swaps the child on a change and stops it on the way out."""

    def run_loop(self, snapshots, children, sleeps, port_taken=False):
        popen = mock.Mock(side_effect=children)
        # the port check is mocked: a real server on 8770 (`make serve` in
        # another checkout) must not change what these tests see
        with mock.patch("dev_reload.python_files", side_effect=snapshots), \
                mock.patch.object(dev_reload.subprocess, "Popen", popen), \
                mock.patch.object(dev_reload.time, "sleep", side_effect=sleeps), \
                mock.patch("dev_reload.port_answers", return_value=port_taken):
            dev_reload.reload_forever(8770)
        return popen

    def child(self, alive=True):
        c = mock.Mock()
        c.poll.return_value = None if alive else 1
        return c

    def test_a_change_replaces_the_child_and_shutdown_stops_the_new_one(self):
        first, second = self.child(), self.child()
        popen = self.run_loop([{"a": 1}, {"a": 1}, {"a": 2}], [first, second],
                              [None, None, KeyboardInterrupt])
        self.assertEqual(popen.call_count, 2)
        self.assertEqual(popen.call_args.args[0][-2:], ["--port", "8770"])
        first.terminate.assert_called_once()
        second.terminate.assert_called_once()

    def test_the_page_url_reaches_every_child(self):
        popen = mock.Mock(side_effect=[self.child(), self.child()])
        with mock.patch("dev_reload.python_files", side_effect=[{"a": 1}, {"a": 2}]), \
                mock.patch.object(dev_reload.subprocess, "Popen", popen), \
                mock.patch.object(dev_reload.time, "sleep", side_effect=[None, KeyboardInterrupt]):
            dev_reload.reload_forever(8770, page="http://localhost:5173")
        self.assertEqual(popen.call_count, 2)          # the first child and the one after the change
        for call in popen.call_args_list:
            self.assertEqual(call.args[0][-2:], ["--page", "http://localhost:5173"])

    def test_a_crashed_child_is_started_again_on_the_next_change(self):
        crashed, fixed = self.child(alive=False), self.child()
        popen = self.run_loop([{"a": 1}, {"a": 2}], [crashed, fixed], [None, KeyboardInterrupt])
        self.assertEqual(popen.call_count, 2)
        fixed.terminate.assert_called_once()

    def test_a_child_that_cannot_take_the_port_stops_make_dev(self):
        # another checkout's server holds 8770: Vite would forward /api to it,
        # so the loop stops with an error instead of carrying on without one
        lost = self.child(alive=False)
        with self.assertRaises(SystemExit) as stop, \
                mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            self.run_loop([{"a": 1}, {"a": 1}], [lost], [None, KeyboardInterrupt], port_taken=True)
        self.assertEqual(stop.exception.code, 1)
        self.assertIn("8770", err.getvalue())

    def test_a_port_taken_after_a_crash_still_stops_make_dev(self):
        crashed = self.child(alive=False)
        popen = mock.Mock(side_effect=[crashed])
        # free on the first tick, taken by another checkout on the second
        with mock.patch("dev_reload.python_files", side_effect=[{"a": 1}, {"a": 1}, {"a": 1}]), \
                mock.patch.object(dev_reload.subprocess, "Popen", popen), \
                mock.patch.object(dev_reload.time, "sleep", side_effect=[None, None, KeyboardInterrupt]), \
                mock.patch("dev_reload.port_answers", side_effect=[False, True]), \
                mock.patch("sys.stderr", new_callable=io.StringIO), \
                self.assertRaises(SystemExit):
            dev_reload.reload_forever(8770)

    def test_a_crashed_child_on_a_free_port_waits_for_the_next_change(self):
        crashed = self.child(alive=False)
        popen = self.run_loop([{"a": 1}, {"a": 1}, {"a": 1}], [crashed], [None, None, KeyboardInterrupt])
        self.assertEqual(popen.call_count, 1)

    def test_a_child_that_will_not_stop_is_killed(self):
        stuck, fresh = self.child(), self.child()
        stuck.wait.side_effect = [dev_reload.subprocess.TimeoutExpired("serve.py", 5), 0]
        self.run_loop([{"a": 1}, {"a": 2}], [stuck, fresh], [None, KeyboardInterrupt])
        stuck.kill.assert_called_once()

    def test_the_signal_handlers_are_put_back(self):
        before = dev_reload.signal.getsignal(dev_reload.signal.SIGTERM)
        self.run_loop([{"a": 1}], [self.child()], [KeyboardInterrupt])
        self.assertIs(dev_reload.signal.getsignal(dev_reload.signal.SIGTERM), before)



if __name__ == "__main__":
    unittest.main()
