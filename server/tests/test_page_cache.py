"""G13: the cached GETs (serve.py, server/finance/page_cache.py): an ETag that is the
data version, a 304 for a tag still current, an answer worked out once per
version, and every way the record can change moving the version."""
import isolate  # noqa: F401  first: no test writes the real record or cache
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from contextlib import closing
from datetime import date
from pathlib import Path
from unittest import mock

import serve
from finance import db, page_cache, results_cache, setup, stats_rebuild

TRIP = {"name": "Coast", "start": "2026-03-01", "end": "2026-03-04", "countries": ["MYS"]}


class PageCacheTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # one server for the class: it reads the data folder from the
        # environment on every request, and each test points that at its own
        cls.dist = tempfile.TemporaryDirectory()
        (Path(cls.dist.name) / "index.html").write_text("<div id=root></div>")
        cls.server = serve.make_server(0, Path(cls.dist.name))
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.dist.cleanup()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        env = mock.patch.dict(os.environ, {"KOPIFUND_DATA": str(self.root / "data"),
                                           "KOPIFUND_DB": str(self.root / "data" / "kopifund.db"),
                                           "KOPIFUND_CACHE": str(self.root / "data" / "cache")})
        env.start()
        self.addCleanup(env.stop)
        setup.create(["posb-everyday"])

    def get(self, path, etag=None):
        req = urllib.request.Request(self.base + path, headers={"If-None-Match": etag} if etag else {})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, r.headers, r.read()
        except urllib.error.HTTPError as e:
            with e:
                return e.code, e.headers, e.read()

    def post(self, path, body):
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode(), method="POST",
                                     headers={"X-Kopifund": "1", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status
        except urllib.error.HTTPError as e:
            with e:
                return e.code

    def trips(self, etag=None):
        status, headers, body = self.get("/api/trips", etag)
        return status, headers.get("ETag"), (json.loads(body) if body else None)

    # ------------------------------------------------------------ ETag and 304

    def test_a_cached_get_carries_its_version_and_a_current_tag_gets_a_304(self):
        status, etag, page = self.trips()
        self.assertEqual((status, page["trips"]), (200, []))
        self.assertRegex(etag, r'^"[0-9a-f]{20}"$')
        status, headers, body = self.get("/api/trips", etag)
        self.assertEqual((status, body, headers["ETag"]), (304, b"", etag))
        self.assertEqual(headers["Cache-Control"], "no-store")
        # a list of tags, or the weak form, matches too
        self.assertEqual(self.get("/api/trips", f'"other", W/{etag}')[0], 304)
        self.assertEqual(self.get("/api/trips", '"other"')[0], 200)

    def test_a_live_get_has_no_tag_and_is_answered_every_time(self):
        for path in ("/api/health", "/api/run/status", "/api/stats/rebuild", "/api/setup", "/api/exports",
                     "/api/uploads", "/api/rules/preview"):
            status, headers, _ = self.get(path, '"anything"')
            self.assertNotEqual(status, 304, path)
            self.assertIsNone(headers.get("ETag"), path)

    def test_an_error_is_never_tagged_or_kept(self):
        status, headers, _ = self.get("/api/results/nothing-yet")
        self.assertEqual((status, headers.get("ETag")), (404, None))
        self.assertFalse(any(k[0] == "/api/results/nothing-yet" for k in page_cache._MEMO))

    # ------------------------------------------------------------ what moves the version

    def test_a_write_through_the_app_moves_the_version_before_it_answers(self):
        _, etag, _ = self.trips()
        self.assertEqual(self.post("/api/trips", TRIP), 200)
        status, new, page = self.trips(etag)
        self.assertEqual((status, [t["name"] for t in page["trips"]]), (200, ["Coast"]))
        self.assertNotEqual(new, etag)

    def test_a_refused_write_moves_it_too(self):
        before = page_cache.data_version()
        self.assertEqual(self.post("/api/trips", {"name": ""}), 422)
        self.assertNotEqual(page_cache.data_version(), before)

    def test_a_write_that_changes_nothing_a_get_reads_leaves_it(self):
        with mock.patch("analytics.api._growth", return_value={}):
            before = page_cache._WRITES["n"]
            self.assertEqual(self.post("/api/growth/preview", {}), 200)
            self.assertEqual(page_cache._WRITES["n"], before)

    def test_a_load_outside_the_app_moves_it(self):
        # `make load` is another process writing the record: here, another connection
        _, etag, _ = self.trips()
        with closing(db.connect()) as conn:
            conn.execute("INSERT INTO trip(name, start_date, end_date) "
                         "VALUES ('Hills', '2026-05-01', '2026-05-02')")
            conn.commit()
        status, _, page = self.trips(etag)
        self.assertEqual((status, [t["name"] for t in page["trips"]]), (200, ["Hills"]))

    def test_an_analysis_outside_the_app_moves_it(self):
        results = Path(os.environ["KOPIFUND_CACHE"]) / "results"
        results.mkdir(parents=True)
        before = page_cache.data_version()
        (results / "travel-2026-10-17.json").write_text("{}")
        after = page_cache.data_version()
        self.assertNotEqual(after, before)
        time.sleep(0.01)
        (results / "travel-2026-10-17.json").write_text('{"id": "travel"}')   # rewritten in place, same day
        self.assertNotEqual(page_cache.data_version(), after)

    def test_each_file_the_cached_gets_read_moves_it(self):
        from finance import paths, profile
        for path in (paths.config_path(), profile.path(), paths.places_path(),
                     paths.personal_rules_dir() / "merchants.csv", paths.rules_history_dir() / "000001.json",
                     Path(os.environ["KOPIFUND_CACHE"]) / "latest.json"):
            before = page_cache.data_version()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(path.read_text() + "\n" if path.exists() else "x\n")
            self.assertNotEqual(page_cache.data_version(), before, path.name)

    def test_a_new_day_moves_it(self):
        before = page_cache.data_version()
        with mock.patch("finance.page_cache.date") as day:
            day.today.return_value = date(2099, 1, 1)
            self.assertNotEqual(page_cache.data_version(), before)

    def test_a_statement_added_moves_only_your_profiles_version(self):
        folder = self.root / "data" / "statements" / "posb-everyday" / "2026"
        folder.mkdir(parents=True, exist_ok=True)
        plain, profile = page_cache.data_version(), page_cache.data_version(statements=True)
        (folder / "2026-09.pdf").write_bytes(b"%PDF")
        self.assertEqual(page_cache.data_version(), plain)
        self.assertNotEqual(page_cache.data_version(statements=True), profile)

    def test_a_statement_added_inside_one_stat_tick_still_moves_it(self):
        # ext4 moves a folder's time on a clock tick of a few milliseconds, so
        # a PDF added inside one left the stat as it was: the names carry it
        folder = self.root / "data" / "statements" / "posb-everyday" / "2026"
        folder.mkdir(parents=True)
        with mock.patch("finance.page_cache._stat", return_value="tick"):
            before = page_cache.data_version(statements=True)
            (folder / "2026-09.pdf").write_bytes(b"%PDF")
            added = page_cache.data_version(statements=True)
            self.assertNotEqual(added, before)
            (folder / "2026-09.pdf").unlink()
            self.assertNotEqual(page_cache.data_version(statements=True), added)

    def test_a_statement_named_in_bytes_the_filesystem_cannot_decode_still_versions(self):
        # Linux hands such a name over as surrogates, which a plain str
        # cannot encode; the version's repr of the names escapes them
        folder = self.root / "data" / "statements" / "posb-everyday"
        folder.mkdir(parents=True, exist_ok=True)
        walk = [(str(folder / "2026"), [], ["ok.pdf", "bad-\udcff.pdf"])]
        with mock.patch("os.walk", return_value=iter(walk)):
            self.assertIsInstance(page_cache.data_version(statements=True), str)

    def test_a_statement_added_to_a_folder_outside_the_statements_folder_moves_it(self):
        # a config may name a folder anywhere under the data folder; Your profile counts it
        config = self.root / "data" / "config.toml"
        config.write_text(config.read_text().replace('path = "statements/posb-everyday"',
                                                       'path = "elsewhere/posb-everyday"'))
        folder = self.root / "data" / "elsewhere" / "posb-everyday"
        folder.mkdir(parents=True)
        before = page_cache.data_version(statements=True)
        (folder / "2026-09.pdf").write_bytes(b"%PDF")
        self.assertNotEqual(page_cache.data_version(statements=True), before)

    def test_a_new_process_starts_a_new_version(self):
        before = page_cache.data_version()
        with mock.patch("finance.page_cache._BOOT", "another"):
            self.assertNotEqual(page_cache.data_version(), before)

    # ------------------------------------------------------------ the memo

    def test_an_answer_is_worked_out_once_per_version(self):
        real = serve.answer
        with mock.patch.object(serve, "answer", side_effect=real) as answer:
            self.trips()
            self.trips()
            self.get("/api/trips?x=1")             # another query is another answer
            self.assertEqual(answer.call_count, 2)
            self.post("/api/trips", TRIP)
            _, _, page = self.trips()
            self.assertEqual(answer.call_count, 3)
            self.assertEqual([t["name"] for t in page["trips"]], ["Coast"])

    def test_two_requests_for_one_answer_at_once_work_it_out_once(self):
        started, release = threading.Event(), threading.Event()
        calls = []

        def slow(path, query):
            calls.append(path)
            if path == "/api/trips":
                started.set()
                release.wait(5)
            return {"path": path}
        out = []
        with mock.patch.object(serve, "answer", side_effect=slow):
            def ask():
                out.append(page_cache.cached_answer("/api/trips", {}, "v", serve.answer))
            first, second = threading.Thread(target=ask), threading.Thread(target=ask)
            first.start()
            self.assertTrue(started.wait(5))
            second.start()
            # another answer is not held up behind the slow one
            self.assertEqual(page_cache.cached_answer("/api/plan", {}, "v", serve.answer), b'{"path": "/api/plan"}')
            time.sleep(0.1)                      # the second is waiting on the first by now
            release.set()
            first.join(5)
            second.join(5)
        self.assertEqual(calls, ["/api/trips", "/api/plan"])
        self.assertEqual(out, [b'{"path": "/api/trips"}'] * 2)
        self.assertEqual(page_cache._FLIGHTS, {})

    def test_a_refusal_reaches_every_request_waiting_on_it_and_is_not_kept(self):
        started, release = threading.Event(), threading.Event()

        def refuse(path, query):
            started.set()
            release.wait(5)
            raise serve.NotFound("no result yet")
        errors = []

        def ask():
            try:
                page_cache.cached_answer("/api/results/x", {}, "v", serve.answer)
            except serve.NotFound as e:
                errors.append(str(e))
        with mock.patch.object(serve, "answer", side_effect=refuse) as answer:
            first = threading.Thread(target=ask)
            first.start()
            self.assertTrue(started.wait(5))
            second = threading.Thread(target=ask)
            second.start()
            time.sleep(0.1)                      # the second is waiting on the first by now
            release.set()
            first.join(5)
            second.join(5)
        self.assertEqual(errors, ["no result yet"] * 2)
        self.assertEqual(answer.call_count, 1)
        self.assertNotIn(("/api/results/x", (), "v"), page_cache._MEMO)
        self.assertEqual(page_cache._FLIGHTS, {})

    def test_the_stat_results_are_read_once_per_version_and_each_caller_gets_a_copy(self):
        cache = Path(os.environ["KOPIFUND_CACHE"])
        (cache / "results").mkdir(parents=True)
        # G20: a result whose chart says cents, so the results cache hands it out (money.in_cents)
        cents = {"unit": "sgd_minor"}
        (cache / "results" / "travel-2026-10-17.json").write_text(json.dumps({"id": "travel", "chart": cents,
                                                                              "data": {"n": 1}}))
        (cache / "latest.json").write_text(json.dumps({"results": {"travel": "travel-2026-10-17.json"}}))
        first = results_cache.results()
        self.assertEqual(first["travel"]["data"], {"n": 1})
        first["travel"]["data"]["n"] = 99
        with mock.patch("finance.results_cache._cache") as read:
            self.assertEqual(results_cache.results()["travel"]["data"], {"n": 1})
            read.assert_not_called()
        time.sleep(0.01)
        (cache / "results" / "travel-2026-10-17.json").write_text(json.dumps({"id": "travel", "chart": cents,
                                                                              "data": {"n": 2}}))
        self.assertEqual(results_cache.results()["travel"]["data"], {"n": 2})

    def test_the_stats_rebuild_moves_the_version_before_it_says_it_is_done(self):
        seen = {}
        done = threading.Event()

        def analyse(cfg):
            seen["during"] = page_cache.data_version()

        real_wrote = page_cache.wrote

        def wrote():
            real_wrote()
            seen["running_when_moved"] = stats_rebuild._STATS["running"]
            done.set()
        with mock.patch("finance.monthly.analyse", side_effect=analyse), mock.patch("finance.page_cache.wrote", wrote):
            stats_rebuild.refresh_stats(None)
            self.assertTrue(done.wait(5))
        for _ in range(100):
            if not stats_rebuild._STATS["running"]:
                break
            time.sleep(0.01)
        self.assertTrue(seen["running_when_moved"])
        self.assertNotEqual(page_cache.data_version(), seen["during"])


if __name__ == "__main__":
    unittest.main()
