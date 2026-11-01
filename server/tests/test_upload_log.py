"""The upload history (server/finance/upload_log.py) and the endpoints that feed it."""
import isolate  # noqa: F401  first: no test writes the real record or cache
import json
from contextlib import closing
import unittest
from unittest import mock

import serve
from finance import db, monthly, sync, upload_log
from test_app_writes import DataFolder

REPORT = {"problems": [], "statements": 2, "rows": 1200, "to_add": [{"key": "k1"}], "to_add_count": 1,
          "recategorised_count": 0, "to_archive_count": 0, "needs_rule": [],
          "rows_by_statement": {"statements/dbs-multiplier/2026/2026-09.pdf": 40}}
PROBLEM = {"code": "balance-chain", "subject": "dbs 2026-09", "fix": "re-download it"}


class LogTest(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        self.addCleanup(self.conn.close)

    def test_an_attempt_lists_its_steps_and_keeps_their_input_and_output(self):
        a = upload_log.start(self.conn)
        upload_log.record(self.conn, a, "upload", upload_log.now(), "ok", "saved", {"file": "s.pdf"}, {"path": "x"})
        upload_log.record(self.conn, a, "check", upload_log.now(), "problems", "1 problem", {}, {"problems": [PROBLEM]})
        [listed] = upload_log.attempts(self.conn)["attempts"]
        self.assertEqual([(s["step"], s["status"]) for s in listed["steps"]], [("upload", "ok"), ("check", "problems")])
        self.assertNotIn("output", listed["steps"][0])
        full = upload_log.attempt(self.conn, a)
        self.assertEqual(full["steps"][0]["input"], {"file": "s.pdf"})
        self.assertEqual(full["steps"][1]["output"]["problems"], [PROBLEM])

    def test_an_attempt_with_no_steps_is_not_history(self):
        upload_log.start(self.conn)
        self.assertEqual(upload_log.attempts(self.conn), {"attempts": [], "next": None, "total": 0})

    def test_every_used_attempt_can_be_paged_to_and_empty_ones_cost_nothing(self):
        used = []
        for i in range(7):
            a = upload_log.start(self.conn)
            if i % 2 == 0:
                upload_log.record(self.conn, a, "check", upload_log.now(), "ok")
                used.append(a)
            upload_log.start(self.conn)            # a visit that ran nothing
        seen, before = [], None
        while True:
            page = upload_log.attempts(self.conn, before, limit=2)
            self.assertLessEqual(len(page["attempts"]), 2)
            seen += [a["id"] for a in page["attempts"]]
            if page["next"] is None:
                break
            before = page["next"]
        self.assertEqual(seen, used[::-1])

    def test_pages_by_offset_carry_the_total(self):
        used = []
        for _ in range(5):
            a = upload_log.start(self.conn)
            upload_log.record(self.conn, a, "check", upload_log.now(), "ok")
            used.append(a)
            upload_log.start(self.conn)            # a visit that ran nothing
        first = upload_log.attempts(self.conn, limit=2)
        last = upload_log.attempts(self.conn, limit=2, offset=4)
        self.assertEqual(first["total"], 5)
        self.assertEqual([a["id"] for a in first["attempts"]], used[::-1][:2])
        self.assertEqual(last["total"], 5)
        self.assertEqual([a["id"] for a in last["attempts"]], used[:1])
        self.assertEqual(upload_log.attempts(self.conn, limit=2, offset=40)["attempts"], [])

    def test_a_step_whose_summary_fails_is_still_recorded(self):
        a = upload_log.start(self.conn)
        def describe(_):
            raise KeyError("counts")
        self.assertEqual(upload_log.logged(self.conn, a, "load", {}, lambda: {"loaded": True}, describe),
                         {"loaded": True})
        [s] = upload_log.attempt(self.conn, a)["steps"]
        self.assertEqual(s["status"], "error")
        self.assertIn("the load finished", s["summary"])

    def test_a_step_names_an_attempt_that_exists_or_gets_a_new_one(self):
        a = upload_log.start(self.conn)
        self.assertEqual(upload_log.attempt_for(self.conn, str(a)), a)
        b = upload_log.attempt_for(self.conn, "999", "rules")
        self.assertNotEqual(a, b)
        origin = self.conn.execute("SELECT origin FROM upload_attempt WHERE id = ?", (b,)).fetchone()[0]
        self.assertEqual(origin, "rules")
        self.assertNotEqual(upload_log.attempt_for(self.conn, "not a number"), a)

    def test_an_unknown_origin_is_filed_as_app(self):
        a = upload_log.start(self.conn, "<script>")
        self.assertEqual(self.conn.execute("SELECT origin FROM upload_attempt WHERE id = ?", (a,)).fetchone()[0], "app")


class EndpointTest(DataFolder):
    """The four steps through serve.write, with the pipeline itself stubbed."""

    def setUp(self):
        super().setUp()
        p = mock.patch("finance.api.config", return_value=mock.Mock(folders=[], accounts={}))
        p.start()
        self.addCleanup(p.stop)
        self.attempt = serve.write("/api/uploads/start", {}, b'{"origin": "upload"}')["attempt"]

    def steps(self):
        with closing(db.connect()) as conn:
            return upload_log.attempt(conn, self.attempt)["steps"]

    def go(self, path, query=None, body=b""):
        return serve.write(path, {"attempt": str(self.attempt), **(query or {})}, body)

    def test_a_check_returns_its_attempt_and_is_recorded_whole(self):
        with mock.patch.object(monthly, "folders", return_value=[{"path": "statements/dbs"}]), \
                mock.patch.object(monthly, "check", return_value=(sync.Parsed(), REPORT)):
            out = self.go("/api/run/check")
        self.assertEqual(out["attempt"], self.attempt)
        [s] = self.steps()
        self.assertEqual((s["step"], s["status"]), ("check", "ok"))
        self.assertEqual(s["input"], {"folders": ["statements/dbs"]})
        self.assertEqual(s["output"]["rows_by_statement"], REPORT["rows_by_statement"])
        self.assertIn("1,200 rows: 1 to add", s["summary"])

    def test_a_refused_upload_is_recorded_then_refused(self):
        with mock.patch.object(monthly, "upload", side_effect=monthly.UploadRefused("that file is not a PDF")):
            with self.assertRaises(serve.Refused):
                self.go("/api/statements", {"folder": "statements/dbs", "name": "notes.txt"}, b"hello")
        [s] = self.steps()
        self.assertEqual((s["step"], s["status"], s["summary"]), ("upload", "refused", "that file is not a PDF"))
        self.assertEqual(s["input"]["file"], "notes.txt")
        self.assertEqual(s["input"]["bytes"], 5)

    def test_a_blocked_load_keeps_the_problems(self):
        blocked = {"loaded": False, "report": {**REPORT, "problems": [PROBLEM]}}
        with mock.patch.object(monthly, "load", return_value=blocked):
            self.assertFalse(self.go("/api/run/load")["loaded"])
        [s] = self.steps()
        self.assertEqual((s["status"], s["summary"]), ("blocked", "not loaded: 1 problem"))
        self.assertEqual(s["output"]["problems"], [PROBLEM])

    def test_a_load_keeps_what_it_added(self):
        done = {"loaded": True, "report": REPORT, "counts": {"inserted": 1, "updated": 0, "archived": 0},
                "stats": {"stats": 15, "transactions": 1201}}
        with mock.patch.object(monthly, "load", return_value=done):
            self.go("/api/run/load")
        [s] = self.steps()
        self.assertEqual(s["summary"], "added 1, updated 0, archived 0; 15 stats rerun")
        self.assertEqual(s["output"]["added"], [{"key": "k1"}])

    def test_a_step_that_crashes_is_recorded_as_an_error_and_still_raises(self):
        with mock.patch.object(monthly, "load", side_effect=RuntimeError("parser fell over")):
            with self.assertRaises(RuntimeError):
                self.go("/api/run/load")
        [s] = self.steps()
        self.assertEqual((s["status"], s["output"]), ("error", {"error": "RuntimeError: parser fell over"}))

    def test_a_failed_mirror_keeps_its_output(self):
        with mock.patch.object(monthly, "mirror", return_value=(1, "token missing")):
            self.assertEqual(self.go("/api/run/mirror")["code"], 1)
        [s] = self.steps()
        self.assertEqual((s["status"], s["output"]["output"]), ("failed", "token missing"))

    def test_a_load_from_another_page_gets_its_own_attempt(self):
        done = {"loaded": True, "report": REPORT, "counts": {"inserted": 0, "updated": 2, "archived": 0}}
        with mock.patch.object(monthly, "load", return_value=done):
            out = serve.write("/api/run/load", {"from": "rules"}, b"")
        self.assertNotEqual(out["attempt"], self.attempt)
        listed = serve.answer("/api/uploads", {})["attempts"]
        self.assertIsNone(serve.answer("/api/uploads", {})["next"])
        self.assertEqual([(a["id"], a["origin"]) for a in listed], [(out["attempt"], "rules")])

    def test_the_list_takes_a_limit_and_an_offset_and_says_how_many_there_are(self):
        with mock.patch.object(monthly, "folders", return_value=[]), \
                mock.patch.object(monthly, "check", return_value=(sync.Parsed(), REPORT)):
            self.go("/api/run/check")
        page = serve.answer("/api/uploads", {"limit": "1", "offset": "1"})
        self.assertEqual((page["attempts"], page["total"]), ([], 1))
        self.assertEqual(serve.answer("/api/uploads", {"limit": "1"})["total"], 1)
        with self.assertRaises(ValueError):
            serve.answer("/api/uploads", {"limit": "lots"})

    def test_the_history_endpoints(self):
        with mock.patch.object(monthly, "folders", return_value=[]), \
                mock.patch.object(monthly, "check", return_value=(sync.Parsed(), REPORT)):
            self.go("/api/run/check")
        detail = serve.answer(f"/api/uploads/{self.attempt}", {})
        self.assertEqual(detail["steps"][0]["output"]["to_add_count"], 1)
        json.dumps(detail)
        for bad in ("/api/uploads/999", "/api/uploads/x"):
            with self.assertRaises(serve.NotFound):
                serve.answer(bad, {})


if __name__ == "__main__":
    unittest.main()
