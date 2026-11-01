"""G19: serve.py's route tables, its one body parser and its one connection per request."""
import isolate  # noqa: F401  first: no test writes the real record or cache
import inspect
import json
import os
import re
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

import serve
from finance import db, page_cache, results_cache, setup

ROOT = Path(__file__).resolve().parents[2]     # the repository root
TABLES = {"GET": serve.GET_ROUTES, "POST": serve.POST_ROUTES}


def documented():
    """Every endpoint serve.py's docstring lists, as (method, path)."""
    return set(re.findall(r"^    (GET|POST) (/api/[^\s?]+)", serve.__doc__, re.M))


def web_calls():
    """Every /api/ path the web app asks for, as (path, file, how): `how` is
    "post" after postJSON or a fetch that says method POST, "get" after useApi, getJSON or fetch (and in
    routes.tsx, which lists pages' GETs to ask ahead), else "any". A template's
    ${...} is a segment, and a query string is cut off. api.ts, and since
    G17 types.gen.ts it takes them from, also hold CACHED and NO_CHANGE,
    which are lists, not calls: only api.ts's fetch and getJSON count."""
    literal = re.compile(r"""["'`](/api/[^"'`\s]*)""")
    found = []
    for f in sorted((ROOT / "apps" / "web" / "src").rglob("*")):
        if f.suffix not in (".ts", ".tsx") or ".test." in f.name:
            continue
        text = f.read_text()
        for m in literal.finditer(text):
            before = text[max(0, m.start() - 60):m.start()]
            after = text[m.end():m.end() + 200]
            how = ("post" if re.search(r"postJSON\s*(<[^()]*>)?\(\s*$", before)
                   # G22: a fetch that says method POST (the copilot's streamed answer) is a write
                   else "post" if re.search(r"fetch\s*\(\s*$", before) and re.search(r"method:\s*[\"']POST", after)
                   else "get" if f.name == "routes.tsx" or re.search(r"(useApi|getJSON|fetch)\s*(<[^()]*>)?\(\s*$", before)
                   else "any")
            if f.name in ("api.ts", "types.gen.ts") and how == "any":
                continue
            path = re.sub(r"\$\{[^}]*\}", "x", m.group(1).split("?")[0])
            found.append((path, f.name, how))
    return found


class RouteTableTest(unittest.TestCase):
    def test_every_route_has_its_own_handler_that_takes_the_request(self):
        handlers = []
        for method, table in TABLES.items():
            for path, route in table.items():
                self.assertTrue(path.startswith("/api/"), path)
                self.assertTrue(callable(route.handler), (method, path))
                self.assertEqual(len(inspect.signature(route.handler).parameters), 1, (method, path))
                handlers.append(route.handler)
        # a handler copied onto a second path would answer it with the first one's work
        self.assertEqual(len(set(handlers)), len(handlers))

    def test_the_docstring_lists_exactly_the_routes(self):
        routes = {(method, path) for method, table in TABLES.items() for path in table}
        self.assertEqual(documented() - routes, set(), "documented, but not routed")
        self.assertEqual(routes - documented(), set(), "routed, but not documented")

    def test_every_path_the_web_app_calls_has_a_route(self):
        calls = web_calls()
        self.assertGreater(len({p for p, _, _ in calls}), 40)     # the scan found the app's calls
        self.assertIn(("/api/rules/save", "RuleEditor.tsx", "post"), calls)
        self.assertIn(("/api/exports/x/x", "Settings.tsx", "any"), calls)
        for path, file, how in calls:
            if how == "post":
                self.assertIsNotNone(serve.resolve("POST", path), f"{file} posts {path}: no such route")
            elif how == "get":
                self.assertIsNotNone(serve.resolve("GET", path), f"{file} gets {path}: no such route")
            else:
                self.assertTrue(serve.resolve("GET", path) or serve.resolve("POST", path),
                                f"{file} asks for {path}: no such route")

    def test_a_pattern_matches_one_segment_per_name(self):
        route, args = serve.resolve("GET", "/api/uploads/7")
        self.assertEqual((route.handler, args), (serve.GET_ROUTES["/api/uploads/<id>"].handler, {"id": "7"}))
        self.assertEqual(serve.resolve("GET", "/api/results/forward-view")[1], {"id": "forward-view"})
        self.assertEqual(serve.resolve("GET", "/api/exports/2026-10-17-120000/transactions.csv")[1],
                         {"stamp": "2026-10-17-120000", "file": "transactions.csv"})
        # a plain path is found by name, so it is not taken for a pattern's value
        self.assertIs(serve.resolve("GET", "/api/uploads")[0], serve.GET_ROUTES["/api/uploads"])
        self.assertIs(serve.resolve("POST", "/api/uploads/start")[0], serve.POST_ROUTES["/api/uploads/start"])
        for path in ("/api/uploads/", "/api/uploads/1/2", "/api/results/", "/api/exports/x", "/api/exports/a/b/c"):
            self.assertIsNone(serve.resolve("GET", path), path)

    def test_a_typo_is_not_found_and_a_method_has_only_its_own_routes(self):
        for method, path in (("GET", "/api/overveiw"), ("POST", "/api/rules/sav"), ("POST", "/api/overview"),
                             ("GET", "/api/rules/save"), ("PUT", "/api/overview")):
            self.assertIsNone(serve.resolve(method, path), (method, path))
        with self.assertRaises(serve.NotFound):
            serve.write("/api/rules/sav", {}, b"{}")
        with self.assertRaises(serve.NotFound):
            serve.answer("/api/overveiw", {})

    def test_the_families_are_what_the_separate_lists_held(self):
        self.assertEqual(serve.PLANNING_WRITES, (
            "/api/budgets", "/api/budgets/copy", "/api/plan", "/api/categories",
            "/api/trips", "/api/trips/remove", "/api/trips/restore", "/api/trips/undo"))
        self.assertEqual(serve.RULE_WRITES, (
            "/api/rules/remove", "/api/rules/save", "/api/rules/undo", "/api/rules/apply",
            "/api/rules/batch", "/api/rules/pin"))
        self.assertEqual(serve.CATEGORY_WRITES, (
            "/api/categories/add", "/api/categories/rename", "/api/categories/move", "/api/categories/delete",
            "/api/category-groups/add", "/api/category-groups/rename", "/api/category-groups/reorder",
            "/api/category-groups/delete", "/api/category-groups/style"))

    def test_a_tag_names_a_refusal_that_exists(self):
        tags = {r.tag for r in serve.POST_ROUTES.values() if r.tag}
        self.assertEqual(tags, set(serve._REFUSALS))              # no tag without one, no one without a tag
        for tag in tags:
            self.assertTrue(issubclass(serve._refusal(tag), Exception), tag)
        self.assertEqual({r.tag for r in serve.GET_ROUTES.values()}, {None})

    def test_what_the_cache_and_the_app_hold_is_still_routed(self):
        # CACHED and NO_CHANGE reach the app through types.gen.ts, so each names a real route
        self.assertEqual(serve.NO_CHANGE, {"/api/run/check", "/api/rules/suggest", "/api/uploads/start",
                                           "/api/growth/preview", "/api/copilot/ask", "/api/copilot/threads/remove",
                                           "/api/ai/test",
                                           # G25: the device registry is no page's data
                                           "/api/link/pair-token", "/api/pair/redeem",
                                           "/api/link/devices/revoke"})
        for path in serve.NO_CHANGE:
            self.assertIn(path, serve.POST_ROUTES)
        # "/api/results", the list, lost its route (RT3), and the generated copy lets it go too
        for path in serve.CACHED:
            self.assertIn(path, serve.GET_ROUTES)

    def test_the_two_endpoints_nothing_called_are_gone(self):
        self.assertNotIn("/api/results", serve.GET_ROUTES)
        self.assertNotIn("/api/rules", serve.POST_ROUTES)
        self.assertIn("/api/rules", serve.GET_ROUTES)             # the Rules page's own read stays
        for method, path in (("GET", "/api/results"), ("POST", "/api/rules")):
            self.assertNotIn(path, {p for m, p in documented() if m == method})
        for path, file, _ in web_calls():
            self.assertNotEqual(path, "/api/results", file)       # nothing asks for the list
        self.assertFalse([f for p, f, how in web_calls() if p == "/api/rules" and how == "post"])


class Server(unittest.TestCase):
    """The server on a data folder of its own, with a record and a config."""

    @classmethod
    def setUpClass(cls):
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
        results_cache._RESULTS_MEMO.clear()
        page_cache._MEMO.clear()
        setup.create(["posb-everyday"])
        db.connect().close()

    def ask(self, method, path, body=None, headers=None):
        data = body if isinstance(body, bytes) or body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"X-Kopifund": "1", **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, r.headers, r.read()
        except urllib.error.HTTPError as e:
            with e:
                return e.code, e.headers, e.read()

    def get(self, path):
        return self.ask("GET", path)

    def post(self, path, body):
        status, _, raw = self.ask("POST", path, body)
        return status, json.loads(raw)


class BodyTest(Server):
    """R6: a JSON body that is not an object is a 400 on every write that reads one."""

    def test_every_write_that_reads_a_body_refuses_a_list_in_words(self):
        reading = [p for p, r in serve.POST_ROUTES.items() if r.json_body]
        self.assertGreater(len(reading), 25)
        for path in reading:
            for body in (b"[]", b'"text"', b"3", b"null"):
                status, answer = self.post(path, body)
                self.assertEqual((status, answer), (400, {"error": "The body is a JSON object"}), (path, body))
            status, answer = self.post(path, b"{nope")
            self.assertEqual((status, answer), (400, {"error": "The body is not JSON"}), path)

    def test_the_seven_that_dropped_the_connection_answer(self):
        # the audit's sites: budgets, copy, need class, trips, trips/undo, suggest, setup, upload start
        for path in ("/api/budgets", "/api/budgets/copy", "/api/categories", "/api/trips", "/api/trips/undo",
                     "/api/rules/suggest", "/api/setup", "/api/uploads/start"):
            self.assertEqual(self.post(path, b"[]")[0], 400, path)

    def test_a_write_that_reads_no_body_does_not_ask_for_one(self):
        # the statements are bytes, and a check, load or export takes none
        for path in ("/api/run/check", "/api/export"):
            self.assertEqual(self.ask("POST", path, b"[]")[0], 200, path)
        self.assertEqual(self.ask("POST", "/api/statements/auto?name=a.pdf", b"[]")[0], 422)

    def test_an_empty_body_is_an_empty_object(self):
        self.assertEqual(self.ask("POST", "/api/uploads/start", b"")[0], 200)
        self.assertEqual(self.post("/api/rules/pin", b"")[0], 422)

    def test_a_refusal_keeps_its_422_and_a_bad_value_its_400(self):
        self.assertEqual(self.post("/api/budgets", {})[0], 422)
        self.assertEqual(self.post("/api/categories/move", {"name": ["x"]})[0], 400)
        self.assertEqual(self.post("/api/rules/save", {})[0], 422)
        self.assertEqual(self.post("/api/setup", {"providers": []})[0], 422)
        self.assertEqual(self.post("/api/scam-checks/fine", {"key": "nope"})[0], 422)


class Counted:
    """A record connection that counts its closing."""

    def __init__(self, conn, count):
        self.conn = conn
        self.count = count
        count["opened"] += 1

    def __getattr__(self, name):
        return getattr(self.conn, name)

    def __enter__(self):
        return self.conn.__enter__()

    def __exit__(self, *exc):
        return self.conn.__exit__(*exc)

    def close(self):
        self.count["closed"] += 1
        self.conn.close()


class ConnectionTest(Server):
    """R7: one connection to the record a request, closed when it ends."""

    def setUp(self):
        super().setUp()
        self.count = {"opened": 0, "closed": 0}
        real = db.connect
        patch = mock.patch.object(db, "connect", lambda *a, **k: Counted(real(*a, **k), self.count))
        patch.start()
        self.addCleanup(patch.stop)
        refresh = mock.patch("finance.stats_rebuild.refresh_stats")     # no stats rebuild in the background
        refresh.start()
        self.addCleanup(refresh.stop)

    def seen(self, method, path, body=None):
        """(opened, closed) by one request."""
        self.count.update(opened=0, closed=0)
        try:
            self.ask(method, path, body)
        except OSError:                  # a route that blows up drops the connection, as it always did
            pass
        return self.count["opened"], self.count["closed"]

    def test_a_read_opens_one_and_closes_it(self):
        for path in ("/api/uploads", "/api/run/status", "/api/rules", "/api/rules/preview", "/api/plan",
                     "/api/budgets", "/api/categories", "/api/trips", "/api/scam-checks", "/api/profile",
                     "/api/suggestions", "/api/transactions", "/api/overview", "/api/recurring",
                     "/api/spending", "/api/growth", "/api/rules/rows?key=k"):
            self.assertEqual(self.seen("GET", path), (1, 1), path)

    def test_masking_the_stat_results_uses_the_requests_connection(self):
        cache = self.root / "data" / "cache"
        (cache / "results").mkdir(parents=True)
        # G20: a result whose chart says cents, so the results cache hands it out (money.in_cents)
        (cache / "results" / "travel-2026-10-17.json").write_text(json.dumps(
            {"id": "travel", "chart": {"unit": "sgd_minor"}, "data": {"n": 1}}))
        (cache / "latest.json").write_text(json.dumps({"results": {"travel": "travel-2026-10-17.json"}}))
        # net worth reads no table of its own: its one connection is the masking's
        self.assertEqual(self.seen("GET", "/api/net-worth"), (1, 1))
        self.assertEqual(self.seen("GET", "/api/results/travel"), (0, 0))     # worked out once a version
        for memo in (results_cache._RESULTS_MEMO, page_cache._MEMO):
            memo.clear()
        self.assertEqual(self.seen("GET", "/api/results/travel"), (1, 1))
        for memo in (results_cache._RESULTS_MEMO, page_cache._MEMO):
            memo.clear()
        self.assertEqual(self.seen("GET", "/api/overview"), (1, 1))           # its own read and the masking's: one

    def test_a_read_that_needs_no_record_opens_none(self):
        for path in ("/api/health", "/api/countries", "/api/setup", "/api/stats/rebuild", "/api/exports",
                     "/api/results/nothing-yet", "/api/nope", "/api/uploads/x/y"):
            self.assertEqual(self.seen("GET", path), (0, 0), path)

    def test_a_read_that_finds_no_record_opens_none_and_says_so(self):
        (self.root / "data" / "kopifund.db").unlink()
        for ext in ("-wal", "-shm"):
            (self.root / "data" / f"kopifund.db{ext}").unlink(missing_ok=True)
        self.count.update(opened=0, closed=0)
        status, _, raw = self.get("/api/trips")
        self.assertEqual((status, self.count["opened"]), (404, 0))
        self.assertIn("kopifund.db", json.loads(raw)["error"])

    def test_a_write_opens_one_and_closes_it_whether_it_works_or_is_refused(self):
        works = [("/api/uploads/start", {"origin": "upload"}), ("/api/export", None),
                 ("/api/growth/preview", {}), ("/api/growth/inputs", {}), ("/api/plan", {}),
                 ("/api/rules/apply", {}), ("/api/rules/suggest", {"groups": []})]
        refused = [("/api/budgets", {}), ("/api/budgets/copy", {}), ("/api/categories", {}),
                   ("/api/categories/add", {}), ("/api/categories/move", {}), ("/api/category-groups/reorder", {}),
                   ("/api/trips", {}), ("/api/trips/remove", {}), ("/api/trips/restore", {}), ("/api/trips/undo", {}),
                   ("/api/rules/pin", {}), ("/api/rules/save", {}), ("/api/rules/remove", {}),
                   ("/api/rules/batch", {}), ("/api/rules/undo", {}),
                   ("/api/scam-checks/fine", {"key": "nope"}), ("/api/scam-checks/settings", {"repeat_count": 0})]
        for path, body in works + refused:
            self.assertEqual(self.seen("POST", path, body), (1, 1), path)

    def test_a_write_that_reads_the_projection_afterwards_still_uses_that_one(self):
        # growth/inputs saves, then draws the projection from the record it saved into
        self.assertEqual(self.seen("POST", "/api/growth/inputs", {}), (1, 1))

    def test_a_write_turned_away_before_its_route_opens_none(self):
        for path, body in (("/api/budgets", b"[]"), ("/api/nope", b"{}"), ("/api/rules", b"{}"),
                           ("/api/health", b"{}")):
            self.assertEqual(self.seen("POST", path, body), (0, 0), path)
        self.assertEqual(self.seen("POST", "/api/rules/pin", {})[0], 1)
        self.count.update(opened=0, closed=0)
        self.ask("POST", "/api/rules/pin", {}, {"Origin": "https://evil.example"})
        self.assertEqual(self.count["opened"], 0)

    def test_a_route_that_raises_closes_its_connection_too(self):
        with mock.patch.object(self.server, "handle_error"):             # the traceback it would print
            with mock.patch("finance.rules_app.categories", side_effect=RuntimeError("boom")):
                self.assertEqual(self.seen("GET", "/api/rules"), (1, 1))
            with mock.patch("finance.planning.set_budget", side_effect=RuntimeError("boom")):
                self.assertEqual(self.seen("POST", "/api/budgets", {"category": "x"}), (1, 1))

    def test_a_direct_call_leaves_the_record_to_its_caller(self):
        # the tests of the routes hand over their own record through _conn or db.connect and go on using it
        self.count.update(opened=0, closed=0)
        serve.answer("/api/trips", {})
        serve.answer("/api/trips", {})
        self.assertEqual((self.count["opened"], self.count["closed"]), (2, 0))


class DownloadTest(Server):
    """An export file is a route that answers a download in place of JSON."""

    def setUp(self):
        super().setUp()
        folder = self.root / "data" / "exports" / "2026-10-17-120000"
        folder.mkdir(parents=True)
        (folder / "transactions.csv").write_text("key,amount\nk1,-100\n")
        (folder / "stat-travel.json").write_text('{"id": "travel"}')

    def test_a_file_is_sent_as_a_download(self):
        status, headers, body = self.get("/api/exports/2026-10-17-120000/transactions.csv")
        self.assertEqual((status, body), (200, b"key,amount\nk1,-100\n"))
        self.assertEqual(headers["Content-Type"], "text/csv; charset=utf-8")
        self.assertEqual(headers["Content-Disposition"], 'attachment; filename="kopifund-2026-10-17-120000-transactions.csv"')
        self.assertEqual((headers["Content-Length"], headers["Cache-Control"]), (str(len(body)), "no-store"))
        status, headers, body = self.get("/api/exports/2026-10-17-120000/stat-travel.json")
        self.assertEqual((status, headers["Content-Type"]), (200, "application/json; charset=utf-8"))

    def test_a_head_asks_for_the_headers_only(self):
        status, headers, body = self.ask("HEAD", "/api/exports/2026-10-17-120000/transactions.csv")
        self.assertEqual((status, body, headers["Content-Length"]), (200, b"", "19"))

    def test_a_file_that_is_not_there_or_not_an_export_is_a_404_in_words(self):
        for path in ("/api/exports/2026-10-17-120000/missing.csv", "/api/exports/not-a-stamp/transactions.csv"):
            status, headers, body = self.get(path)
            self.assertEqual((status, json.loads(body)), (404, {"error": "No such export file"}), path)
        # a path that is not stamp/file is no route at all
        for path in ("/api/exports/2026-10-17-120000", "/api/exports/2026-10-17-120000/..%2fconfig.toml"):
            self.assertEqual(self.get(path)[0], 404, path)

    def test_the_listing_names_what_can_be_downloaded(self):
        status, _, body = self.get("/api/exports")
        self.assertEqual(status, 200)
        listed = json.loads(body)["exports"]
        self.assertEqual([e["stamp"] for e in listed], ["2026-10-17-120000"])


class RemovedTest(Server):
    def test_the_list_of_stat_results_and_the_plain_rule_add_answer_404(self):
        self.assertEqual(self.get("/api/results")[0], 404)
        self.assertEqual(self.post("/api/rules", {"match_on": "merchant"})[0], 404)
        self.assertEqual(self.get("/api/rules")[0], 200)
        self.assertEqual(self.get("/api/results/nothing-yet")[0], 404)


if __name__ == "__main__":
    unittest.main()
