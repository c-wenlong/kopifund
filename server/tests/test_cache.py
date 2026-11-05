"""The stat cache reader, and the sample results the app is shaped against.

`server/finance/results_cache.py` reads `latest.json` and the dated result files back out of
the cache; `serve.py` hands them to the app. `server/tests/fixtures/analytics` holds a full
set of invented results, one per stat, laid out like a real cache, so the
contract the app relies on (the result fields, the chart hint, the rows it
points at) is checked here without Notion, a token or a snapshot.
"""
import isolate  # noqa: F401  first: no test writes the real record or cache
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from analytics import publish
from finance import results_cache
from finance.results_cache import Cache, NotFound

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "analytics"

# The stats SPEC.md asks for. The fixtures carry one result for each, so a stat
# that loses its fixture (or an id that drifts) fails here rather than showing
# up as a missing card in the app.
SPEC_STATS = ["monthly-cashflow", "category-breakdown", "needs-wants-savings",
              "recurring-detector", "eating-out", "merchant-concentration",
              "one-offs", "income-stability", "savings-runway", "portfolio",
              "cpf", "travel", "benchmarks-sg", "forward-view", "data-quality"]

# The chart types the app draws, read from apps/web/src/stats/hint.ts itself
# (`export const KNOWN = new Set([...])`), so a change there cannot drift
# away from this check.
def _known_chart_types():
    import re
    text = (ROOT.parent / "apps" / "web" / "src" / "stats" / "hint.ts").read_text(encoding="utf-8")
    m = re.search(r"export const KNOWN = new Set\(\[([^\]]*)\]\)", text)
    return set(re.findall(r'"([a-z]+)"', m.group(1))) if m else set()


CHART_TYPES = _known_chart_types()


class TestFixtureCache(unittest.TestCase):

    def test_the_app_chart_types_were_read(self):
        self.assertIn("table", CHART_TYPES)
        self.assertGreater(len(CHART_TYPES), 1)

    @classmethod
    def setUpClass(cls):
        cls.cache = Cache(FIXTURES)
        cls.results, _ = cls.cache.results_all()

    def test_latest_names_files_that_exist(self):
        latest, _ = self.cache.latest()
        self.assertTrue(latest["snapshot"].startswith("snapshot-"))
        self.assertEqual(sorted(latest["results"]), sorted(SPEC_STATS))
        for stat_id, name in latest["results"].items():
            self.assertTrue((FIXTURES / "results" / name).is_file(), name)
            self.assertTrue(name.startswith(stat_id + "-"), (stat_id, name))

    def test_results_all_has_every_stat(self):
        self.assertEqual(sorted(self.results), sorted(SPEC_STATS))

    def test_display_order_names_every_stat(self):
        self.assertEqual(sorted(results_cache.DISPLAY_ORDER), sorted(SPEC_STATS))
        self.assertEqual(results_cache.DISPLAY_ORDER[0], "monthly-cashflow")
        self.assertEqual(results_cache.DISPLAY_ORDER[-1], "data-quality")

    def test_every_stat_has_the_spec_fields(self):
        for stat_id, body in self.results.items():
            with self.subTest(stat=stat_id):
                for field in ("id", "title", "generated_at", "snapshot",
                              "sources", "params", "data", "findings", "caveats"):
                    self.assertIn(field, body)
                self.assertEqual(body["id"], stat_id)
                self.assertTrue(body["snapshot"].startswith("snapshot-"))
                self.assertTrue(body["findings"], "findings must not be empty")
                self.assertTrue(body["caveats"], "caveats must not be empty")
                self.assertIsInstance(body["data"], dict)

    def test_every_source_names_a_database_rows_and_filter(self):
        for stat_id, body in self.results.items():
            with self.subTest(stat=stat_id):
                self.assertTrue(body["sources"])
                for src in body["sources"]:
                    self.assertTrue(src["database"])
                    self.assertIsInstance(src["rows_used"], int)
                    self.assertTrue(src["filter"])

    def test_every_stat_carries_a_chart_hint_the_app_knows(self):
        for stat_id, body in self.results.items():
            with self.subTest(stat=stat_id):
                chart = body["chart"]
                self.assertIn(chart["type"], CHART_TYPES)
                self.assertTrue(chart["x"])
                self.assertIsInstance(chart["series"], list)

    def test_chart_source_resolves_to_rows_carrying_x_and_series(self):
        for stat_id, body in self.results.items():
            with self.subTest(stat=stat_id):
                chart = body["chart"]
                rows = body["data"][chart["source"]]
                self.assertTrue(rows, chart["source"])
                self.assertIn(chart["x"], rows[0])
                for name in chart["series"]:
                    self.assertTrue(any(isinstance(r.get(name), (int, float))
                                        for r in rows), name)

    def test_benchmarks_stat_carries_sourced_benchmarks(self):
        body = self.results["benchmarks-sg"]
        self.assertTrue(body["benchmarks"])
        for item in body["benchmarks"]:
            self.assertTrue(item["url"].startswith("https://"))
            self.assertTrue(item["as_of"])

    def test_current_file_follows_latest(self):
        self.assertEqual(self.cache.current_file("monthly-cashflow").name,
                         "monthly-cashflow-2026-09-04.json")

    def test_files_for_lists_every_run_newest_first(self):
        names = [p.name for p in self.cache.files_for("monthly-cashflow")]
        self.assertEqual(names, sorted(names, reverse=True))
        self.assertEqual(len(names), 3)
        self.assertEqual(len(self.cache.files_for("travel")), 1)

    def test_files_for_never_crosses_stats(self):
        for stat_id in SPEC_STATS:
            for path in self.cache.files_for(stat_id):
                self.assertRegex(path.name, rf"^{stat_id}-\d{{4}}-\d{{2}}-\d{{2}}\.json$")

    def test_unknown_stat_is_not_found(self):
        self.assertEqual(self.cache.files_for("no-such-stat"), [])
        with self.assertRaises(NotFound):
            self.cache.current_file("no-such-stat")

    def test_an_id_that_is_not_an_id_matches_nothing(self):
        for bad in ("../latest", "Monthly_Cashflow", "monthly cashflow", "", None):
            with self.subTest(bad=bad):
                self.assertEqual(self.cache.files_for(bad), [])

    def test_a_missing_cache_is_not_found(self):
        with self.assertRaises(NotFound):
            Cache("/no/such/cache/dir").latest()

    def test_reading_writes_nothing(self):
        before = {p: p.stat().st_mtime for p in FIXTURES.rglob("*.json")}
        Cache(FIXTURES).results_all()
        after = {p: p.stat().st_mtime for p in FIXTURES.rglob("*.json")}
        self.assertEqual(before, after)


class TestResultsView(unittest.TestCase):
    """G26: a widget's question copies only the stat results it reads."""

    def test_a_view_copies_a_result_when_read_and_never_shares_the_memo(self):
        shared = {"monthly-cashflow": {"data": {"months": [1]}}, "cpf": {"data": {"years": [2]}}}
        with mock.patch.object(results_cache, "_shared", lambda req=None: shared), \
                mock.patch.object(results_cache.copy, "deepcopy", wraps=results_cache.copy.deepcopy) as deep:
            view = results_cache.results_view()
            self.assertEqual(deep.call_count, 0)
            self.assertEqual(sorted(view), ["cpf", "monthly-cashflow"])
            got = view["cpf"]
            self.assertEqual(deep.call_count, 1)
            self.assertIs(view.get("cpf"), got)                  # read twice, copied once
            self.assertIsNone(view.get("travel"))
            got["data"]["years"].append(3)                       # a caller's change stays its own
            self.assertEqual(shared["cpf"]["data"]["years"], [2])
            # the whole-copy reader still copies everything
            self.assertEqual(results_cache.results(), shared)
            self.assertIsNot(results_cache.results()["cpf"], shared["cpf"])


class TestMetrics(unittest.TestCase):
    """The hub page's Metrics rows over the fixtures and over a plain cache."""

    def test_rows_over_the_fixtures(self):
        cache = Cache(FIXTURES)
        results, _ = cache.results_all()
        snapshot, _ = cache.read(FIXTURES / "snapshot-2026-09-04.json")
        rows = publish.metrics_from(results, snapshot)
        self.assertTrue(rows)
        for row in rows:
            # G20: a money row holds its cents, `Value_minor`; any other row its `Value`
            value = "Value_minor" if row["Unit"] == "SGD" else "Value"
            self.assertEqual(sorted(row), sorted(["As of", "Name", "Note", "Order", "Stat", "Unit", value]))
            if value == "Value_minor":
                self.assertIs(type(row[value]), int, row["Name"])
            self.assertTrue(row["Name"])
            self.assertIn(row["Stat"], SPEC_STATS + ["budgets"])
        # Order is each metric's fixed place in publish.METRIC_ORDER: a metric
        # with no figure leaves a gap, and the rows still come in that order
        from analytics.publish import METRIC_ORDER
        orders = [r["Order"] for r in rows]
        self.assertEqual(orders, sorted(set(orders)))
        self.assertTrue(set(orders) <= set(range(1, len(METRIC_ORDER) + 1)))

    def test_rows_from_a_plain_cache_directory(self):
        tmp = Path(tempfile.mkdtemp(prefix="analytics_metrics_cache_"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / "results").mkdir()
        result = {"id": "savings-runway", "title": "Cash and runway",
                  "generated_at": "2026-09-01T09:05:00+08:00",
                  "snapshot": "snapshot-2026-09-01.json", "chart": {"unit": "sgd_minor"},
                  "sources": [], "params": {},
                  "data": {"summary": {"cash_minor": 1234567, "as_of": "2026-08",
                                       "runway_6m": 4.2, "spend_avg_6m_minor": 200000}},
                  "findings": ["a finding"], "caveats": ["a caveat"]}
        (tmp / "results" / "savings-runway-2026-09-01.json").write_text(json.dumps(result))
        (tmp / "latest.json").write_text(json.dumps({
            "snapshot": "snapshot-2026-09-01.json",
            "results": {"savings-runway": "savings-runway-2026-09-01.json"}}))
        results, _ = Cache(tmp).results_all()
        by_name = {r["Name"]: r for r in publish.metrics_from(results)}
        self.assertEqual(by_name["Cash, all accounts"]["Value_minor"], 1234567)
        self.assertEqual(by_name["Cash, all accounts"]["Stat"], "savings-runway")
        self.assertEqual(by_name["Runway, months"]["Value"], 4.2)

    def test_a_result_from_before_g20_is_no_result(self):
        # its dollars are neither read as cents nor read at all: the stat waits for the next run
        tmp = Path(tempfile.mkdtemp(prefix="analytics_metrics_cache_"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / "results").mkdir()
        old = {"id": "savings-runway", "chart": {"type": "line", "series": ["cash"], "unit": "sgd"},
               "data": {"summary": {"cash": 12345.67, "as_of": "2026-08", "runway_6m": 4.2}}}
        (tmp / "results" / "savings-runway-2026-09-01.json").write_text(json.dumps(old))
        (tmp / "latest.json").write_text(json.dumps({"results": {"savings-runway": "savings-runway-2026-09-01.json"}}))
        results, _ = Cache(tmp).results_all()
        self.assertEqual(results, {})
        self.assertEqual(publish.metrics_from(results), [])


class TestSafePath(unittest.TestCase):

    def test_escapes_are_rejected(self):
        for bad in ("../server.py", "/etc/passwd", "..", "./..", "a/../../x", "",
                    "%2e%2e/server.py", "sub/../../server.py", "x\x00y"):
            with self.subTest(bad=bad):
                self.assertIsNone(results_cache.safe_path(FIXTURES, bad))

    def test_a_file_inside_resolves(self):
        self.assertEqual(results_cache.safe_path(FIXTURES, "latest.json"),
                         FIXTURES / "latest.json")

    def test_latest_naming_a_file_outside_falls_back_to_the_newest(self):
        tmp = Path(tempfile.mkdtemp(prefix="analytics_escape_"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / "results").mkdir()
        (tmp / "secret.json").write_text("{}")
        (tmp / "results" / "travel-2026-09-01.json").write_text("{}")
        (tmp / "latest.json").write_text(json.dumps(
            {"results": {"travel": "../secret.json"}}))
        self.assertEqual(Cache(tmp).current_file("travel").name, "travel-2026-09-01.json")


if __name__ == "__main__":
    unittest.main()
