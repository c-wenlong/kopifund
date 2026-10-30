"""Audit: the server answers every request, and no other site can frame it or
read through it. A handler that raises still answers JSON (no stack trace or
path in it), a bad Content-Length is a 400, every response forbids framing,
and an /api/ GET another site starts is refused."""
import isolate  # noqa: F401  first: no test writes the real record or cache
import contextlib
import io
import json
import sqlite3
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from pathlib import Path
from unittest import mock

import serve


class AnswersTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        dist = Path(cls.tmp.name) / "dist"
        (dist / "assets").mkdir(parents=True)
        (dist / "index.html").write_text("<div id=root></div>")
        cls.server = serve.make_server(0, dist)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def send(self, method, path, headers=None, body=b""):
        """(status, headers, body) for one request; headers are sent as given."""
        conn = HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.putrequest(method, path, skip_accept_encoding=True)
            for name, value in (headers or {}).items():
                conn.putheader(name, value)
            conn.endheaders(body or None)
            r = conn.getresponse()
            return r.status, r.headers, r.read()
        finally:
            conn.close()

    def post(self, path, body=b"{}", **extra):
        headers = {"X-Kopifund": "1", "Content-Type": "application/json", "Content-Length": str(len(body))}
        headers.update(extra)
        return self.send("POST", path, headers, body)

    def quietly(self):
        """The server prints the failure to stderr; keep the test output clean
        and hand back what it printed."""
        return contextlib.redirect_stderr(io.StringIO())

    # ------------------------------------------------- a failure still answers

    def test_a_write_that_raises_anything_answers_500_json(self):
        boom = TypeError("unhashable type in /Users/someone/.kopifund/rules")
        with mock.patch.object(serve, "write", side_effect=boom), self.quietly() as err:
            status, headers, body = self.post("/api/budgets/copy", b'{"from": 5}')
        self.assertEqual(status, 500)
        self.assertEqual(headers["Content-Type"], "application/json")
        error = json.loads(body)["error"]
        self.assertNotIn("Traceback", error)
        self.assertNotIn("/Users/", error)
        self.assertNotIn("unhashable", error)
        self.assertIn("Traceback", err.getvalue())      # logged on the server's side

    def test_a_read_that_raises_anything_answers_500_json(self):
        with mock.patch.object(serve, "answer", side_effect=AttributeError("x")), self.quietly():
            status, _, body = self.send("GET", "/api/setup")
        self.assertEqual(status, 500)
        self.assertIn("error", json.loads(body))

    def test_a_busy_record_answers_503(self):
        locked = sqlite3.OperationalError("database is locked")
        with mock.patch.object(serve, "write", side_effect=locked), self.quietly():
            status, _, body = self.post("/api/rules/save")
        self.assertEqual(status, 503)
        self.assertIn("busy", json.loads(body)["error"])

    def test_the_server_keeps_answering_after_a_failure(self):
        with mock.patch.object(serve, "write", side_effect=KeyError("k")), self.quietly():
            self.post("/api/trips")
        status, _, _ = self.send("GET", "/api/health")
        self.assertEqual(status, 200)

    def test_a_nul_in_a_page_path_is_404(self):
        status, _, _ = self.send("GET", "/%00")
        self.assertEqual(status, 404)

    # ----------------------------------------------------------- the body's size

    def test_a_length_that_is_not_a_number_is_400(self):
        status, _, body = self.send("POST", "/api/trips", {"X-Kopifund": "1", "Content-Length": "abc"})
        self.assertEqual(status, 400)
        self.assertIn("error", json.loads(body))

    def test_a_negative_length_is_400_at_once(self):
        status, _, _ = self.send("POST", "/api/uploads/start", {"X-Kopifund": "1", "Content-Length": "-1"})
        self.assertEqual(status, 400)

    def test_a_length_over_the_limit_is_still_413(self):
        status, _, _ = self.send("POST", "/api/uploads/start",
                                 {"X-Kopifund": "1", "Content-Length": str(serve.MAX_BODY + 1)})
        self.assertEqual(status, 413)

    # ------------------------------------------------------------- never framed

    def assert_unframeable(self, headers, what):
        self.assertEqual(headers["X-Frame-Options"], "DENY", what)
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"], what)
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff", what)

    def test_every_kind_of_response_forbids_framing(self):
        self.assert_unframeable(self.send("GET", "/")[1], "a page")
        self.assert_unframeable(self.send("GET", "/settings")[1], "an app page")
        self.assert_unframeable(self.send("GET", "/api/health")[1], "JSON")
        self.assert_unframeable(self.send("GET", "/assets/missing.js")[1], "a missing asset")
        self.assert_unframeable(self.send("GET", "/api/no-such-route")[1], "an unknown route")
        self.assert_unframeable(self.post("/api/trips", Origin="https://evil.example")[1], "a refused write")
        with mock.patch.object(serve, "write", side_effect=TypeError("x")), self.quietly():
            self.assert_unframeable(self.post("/api/trips")[1], "a failure")

    # ------------------------------------------------- another site's GETs

    def test_an_api_get_another_site_starts_is_refused(self):
        for site in ("cross-site", "same-site"):
            with mock.patch.object(serve, "answer") as answer:
                status, _, body = self.send("GET", "/api/ai", {"Sec-Fetch-Site": site})
            self.assertEqual(status, 403, site)
            self.assertIn("error", json.loads(body))
            answer.assert_not_called()                   # nothing ran

    def test_the_app_and_other_clients_still_read(self):
        # same-origin is the app (and Vite's proxy, which passes the header on),
        # none is a typed address or a download, absent is curl or the phone
        for headers in ({"Sec-Fetch-Site": "same-origin"}, {"Sec-Fetch-Site": "none"}, {}):
            status, _, _ = self.send("GET", "/api/health", headers)
            self.assertEqual(status, 200, headers)

    def test_a_page_opened_from_another_site_still_loads(self):
        status, _, _ = self.send("GET", "/settings", {"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(status, 200)


if __name__ == "__main__":
    unittest.main()
