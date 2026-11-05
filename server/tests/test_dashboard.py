"""G26: the dashboard's widget query layer, its scenario preview, and the
state it keeps in the record (server/finance/dashboard.py, routed in serve.py)."""
import isolate  # noqa: F401  first: no test writes the real record or cache
import json
import os
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

import serve
from analytics.stats import monthly_cashflow, needs_wants_savings, savings_runway
from finance import dashboard, db
from finance.errors import NotFound
from test_db import SNAP, block, parsed, row

# SNAP plus one Transfer-bucket category, so a `transfers` metric has rows
SNAP2 = dict(SNAP, categories=SNAP["categories"] + [
    {"name": "Savings move", "bucket": "Transfer", "group": "Savings",
     "need_class": "Transfer", "sort_order": 300}])


def snapshot():
    return {k: [dict(x) for x in v] for k, v in SNAP2.items()}


def record(rows, **kw):
    """A small invented record: two months of rows, an income, a trip, a move."""
    conn = db.connect(":memory:")
    db.import_user_tables(conn, snapshot())
    db.load_parsed(conn, parsed([block()], rows),
                   payees=[{"name": "Grab", "kind": "Merchant", "country": "SGP"}], **kw)
    return conn


def rows():
    return [row("k1", "-12.50", date="2026-08-03", category="Transport", payee="Grab"),
            row("k2", "-5.00", date="2026-08-04", category="Transport", merchant="KOPITIAM", payee=""),
            row("k3", "-20.00", date="2026-07-11", category="Transport", payee="Grab"),
            row("k4", "-30.00", date="2026-08-11", category="Transport",
                merchant="WARUNG MAKAN", merchant_country="MYS"),
            row("s1", "2500.00", date="2026-08-25", direction="In", category="Salary",
                merchant="", bank_txn_type="FAST", payee="", rule=""),
            row("t1", "-400.00", date="2026-08-20", category="Savings move",
                merchant="", bank_txn_type="FAST", payee="", rule="")]


# A stat's cached result, invented: two months of the monthly cash flow,
# a runway, a CPF year, all in cents under `<name>_minor` as G20 writes them.
# A stat's points link through its own chart.links, as `common.result` writes them.
RESULTS = {
    "monthly-cashflow": {"chart": {"links": monthly_cashflow.LINKS}, "data": {"months": [
        {"month": "2026-07", "income_minor": 250_000, "spend_minor": 200_000,
         "investing_minor": 10_000, "net_minor": 40_000},
        {"month": "2026-08", "income_minor": 250_000, "spend_minor": 42_500,
         "investing_minor": 10_000, "net_minor": 197_500}]}},
    "savings-runway": {"chart": {"links": savings_runway.LINKS}, "data": {"months": [
        {"month": "2026-07", "cash_minor": 900_000, "spend_minor": 200_000, "runway_months": 4.5,
         "carried_amount_minor": 0, "complete": True},
        {"month": "2026-08", "cash_minor": 910_000, "spend_minor": 42_500, "runway_months": 5.2,
         "carried_amount_minor": 0, "complete": True}],
        "summary": {"runway_6m": 5.2}}},
    "cpf": {"data": {"years": [
        {"year": 2024, "as_of": "2024-12-31", "oa_minor": 10_000, "sa_minor": 2_000,
         "ma_minor": 3_000, "total_minor": 15_000},
        {"year": 2025, "as_of": "2025-12-31", "oa_minor": 20_000, "sa_minor": 4_000,
         "ma_minor": 6_000, "total_minor": 30_000}]}},
    "portfolio": {"data": {"holdings": [
        {"symbol": "VT", "name": "Vanguard Total World", "value_minor": 1_000_000,
         "unrealized_pl_minor": 100_000}],
        "periods": [{"period_from": "2026-01-01", "period_to": "2026-06-30",
                     "value_sgd_minor": 1_000_000, "contributed_sgd_minor": 900_000,
                     "deposits_sgd_minor": 400_000, "value_base_minor": 700_000}]}},
    "needs-wants-savings": {"chart": {"links": needs_wants_savings.LINKS}, "data": {"months": [
        {"month": "2026-08", "income_minor": 250_000, "need_minor": 100_000,
         "want_minor": 60_000, "savings_minor": 50_000,
         "need_share": 0.4, "want_share": 0.24, "savings_share": 0.2, "unclassified": 0.0}]}},
    "category-breakdown": {"data": {"categories": [
        {"category": "Rent", "total_minor": 230_000, "monthly_avg_minor": 50_000,
         "last3_avg_minor": 45_000, "prior6_avg_minor": 55_000, "share_pct": 47.9, "trend_pct": 5.8}]}},
    "forward-view": {"data": {"months": [
        {"month": "2026-09", "cash_minor": 950_000, "net_minor": 40_000, "spend_minor": 42_500,
         "take_home_minor": 210_000},
        {"month": "2026-10", "cash_minor": 990_000, "net_minor": 40_000, "spend_minor": 42_500,
         "take_home_minor": 210_000}]}},
}


def minor_ints(value, path="$"):
    """Every dollar-shaped number in an answer: keys named `_minor` and the
    `values_minor` list must hold whole cents. Beside test_cents_contract.py."""
    bad = []
    if isinstance(value, dict):
        for k, v in value.items():
            if k == "values_minor" and isinstance(v, list):
                bad += [f"{path}.{k}[{i}]: {x!r}" for i, x in enumerate(v)
                        if x is not None and (isinstance(x, bool) or not isinstance(x, int))]
            elif k.endswith("_minor"):
                if v is not None and (isinstance(v, bool) or not isinstance(v, int)):
                    bad.append(f"{path}.{k}: {v!r}")
            else:
                bad += minor_ints(v, f"{path}.{k}")
    elif isinstance(value, list):
        for i, v in enumerate(value):
            bad += minor_ints(v, f"{path}[{i}]")
    return bad


class QueryTest(unittest.TestCase):
    """parse_query: a bad query is refused in words, never a blank chart."""

    def test_an_unknown_source_metric_or_dimension_is_named(self):
        with self.assertRaises(dashboard.DashboardRefused) as e:
            dashboard.parse_query({"source": "nonsense", "metric": "spend"})
        self.assertIn("spending", str(e.exception))
        with self.assertRaises(dashboard.DashboardRefused) as e:
            dashboard.parse_query({"source": "spending", "metric": "nonsense"})
        self.assertIn("spend", str(e.exception))
        with self.assertRaises(dashboard.DashboardRefused) as e:
            dashboard.parse_query({"source": "cpf", "metric": "total", "dimension": "month"})
        self.assertIn("year", str(e.exception))
        with self.assertRaises(dashboard.DashboardRefused) as e:
            dashboard.parse_query({"source": "portfolio", "metric": "value_sgd",
                                   "dimension": "holding"})
        self.assertIn("value", str(e.exception))

    def test_a_bad_range_split_or_filter_is_refused(self):
        base = {"source": "spending", "metric": "spend", "dimension": "month"}
        for extra in ({"range": {"from": "2026-13"}}, {"range": {"from": "x"}},
                      {"range": {"from": "2026-09", "to": "2026-08"}},
                      {"range": "2026-08"}, {"split": "weekday"},
                      {"filters": {"nonsense": "1"}}, {"filters": {"scope": "mars"}},
                      {"filters": {"categories": "not-json"}},
                      {"filters": {"account": "POSB"}}):       # accounts are ids
            with self.assertRaises(dashboard.DashboardRefused, msg=extra):
                dashboard.parse_query({**base, **extra})
        # a split needs a spending query by month
        with self.assertRaises(dashboard.DashboardRefused):
            dashboard.parse_query({**base, "split": "category", "dimension": "bucket"})
        with self.assertRaises(dashboard.DashboardRefused):
            dashboard.parse_query({"source": "monthly-cashflow", "metric": "income",
                                   "split": "category"})
        # filters and a range on the wrong dimension are refused, not dropped
        with self.assertRaises(dashboard.DashboardRefused):
            dashboard.parse_query({"source": "monthly-cashflow", "metric": "income",
                                   "filters": {"need": "Want"}})
        with self.assertRaises(dashboard.DashboardRefused):
            dashboard.parse_query({"source": "category-breakdown", "metric": "total",
                                   "range": {"from": "2026-01"}})
        # but a range on a stat's month, year or period dimension applies
        for source, metric, dim in (("monthly-cashflow", "income", "month"),
                                    ("cpf", "total", "year"),
                                    ("portfolio", "value_sgd", "period")):
            q = dashboard.parse_query({"source": source, "metric": metric, "dimension": dim,
                                       "range": {"from": "2026-01", "to": "2026-08"}})
            self.assertEqual(q["range"], {"from": "2026-01", "to": "2026-08"})

    def test_sole_dimension_and_metric_defaults_apply(self):
        q = dashboard.parse_query({"source": "cpf", "metric": "total"})
        self.assertEqual(q["dimension"], "year")
        q = dashboard.parse_query({"source": "net-worth", "dimension": "part"})
        self.assertEqual(q["metric"], "value")
        self.assertEqual(dashboard.parse_query({"source": "spending", "metric": "spend"})["dimension"],
                         "month")


class SpendingSourceTest(unittest.TestCase):
    """The `spending` source over an invented record (issue #6's example)."""

    def setUp(self):
        self.conn = record(rows(), trips_by_key={"k4": "Trip A"})
        self.addCleanup(self.conn.close)

    def test_spend_by_bucket_this_month(self):
        a = dashboard.resolve(self.conn, {}, {"source": "spending", "metric": "spend",
                                              "dimension": "bucket",
                                              "range": {"from": "2026-08", "to": "2026-08"}})
        self.assertEqual(a["unit"], "sgd_minor")
        self.assertEqual(a["labels"], ["Getting around"])
        self.assertEqual(a["series"], [{"name": "spend", "values_minor": [1750]}])
        self.assertEqual(a["label_links"][0]["bucket"], "Getting around")
        self.assertEqual(minor_ints(a), [])

    def test_spend_by_month_gap_filled_and_signed(self):
        # the trip row is out under the explorer's sg scope, the default here
        a = dashboard.resolve(self.conn, {}, {"source": "spending", "metric": "spend",
                                              "dimension": "month"})
        self.assertEqual(a["labels"], ["2026-07", "2026-08"])
        self.assertEqual(a["series"][0]["values_minor"], [2000, 1750])
        # income in, transfers out, net the signed total of the three
        for metric, vals in (("income", [0, 250_000]), ("transfers", [0, 40_000]),
                             ("net", [-2000, 250_000 - 1750 - 40_000])):
            a = dashboard.resolve(self.conn, {}, {"source": "spending", "metric": metric})
            self.assertEqual(a["series"][0]["values_minor"], vals, metric)

    def test_a_month_query_split_by_category(self):
        a = dashboard.resolve(self.conn, {}, {"source": "spending", "metric": "spend",
                                              "split": "category"})
        self.assertEqual([s["name"] for s in a["series"]], ["Transport"])
        self.assertEqual(a["series"][0]["values_minor"], [2000, 1750])
        self.assertEqual(a["series"][0]["link"], {"spend": "1", "home": "1", "category": "Transport"})

    def test_scope_all_counts_the_trip_row_and_the_link_says_so(self):
        all_rows = dashboard.resolve(self.conn, {}, {"source": "spending", "metric": "spend",
                                                     "dimension": "month",
                                                     "filters": {"scope": "all"}})
        self.assertEqual(all_rows["series"][0]["values_minor"], [2000, 4750])
        self.assertNotIn("home", all_rows["label_links"][0])
        away = dashboard.resolve(self.conn, {}, {"source": "spending", "metric": "spend",
                                                 "filters": {"trip": "Trip A"}})
        self.assertEqual(away["series"][0]["values_minor"], [0, 3000])
        payee = dashboard.resolve(self.conn, {}, {"source": "spending", "metric": "spend",
                                                  "filters": {"payee": "Grab"}})
        self.assertEqual(payee["series"][0]["values_minor"], [2000, 1250])

    def test_the_other_dimensions_and_their_links(self):
        rng = {"from": "2026-08"}
        for dim, labels, values in (("category", ["Transport"], [1750]),
                                    ("need", ["Need"], [1750]),
                                    ("payee", ["Grab", "KOPITIAM"], [1250, 500])):
            a = dashboard.resolve(self.conn, {}, {"source": "spending", "metric": "spend",
                                                  "dimension": dim, "range": rng})
            self.assertEqual((a["labels"], a["series"][0]["values_minor"]), (labels, values), dim)
        a = dashboard.resolve(self.conn, {}, {"source": "spending", "metric": "spend",
                                              "dimension": "account"})
        self.assertEqual(a["labels"], ["POSB"])
        account_id = self.conn.execute("SELECT id FROM account WHERE name = 'POSB'").fetchone()[0]
        self.assertEqual(a["label_links"][0]["account"], str(account_id))
        self.assertEqual(a["label_links"][0]["home"], "1")

    def test_a_day_end_resolves_to_its_month_and_an_empty_record_answers_empty(self):
        a = dashboard.resolve(self.conn, {}, {"source": "spending", "metric": "spend",
                                              "range": {"from": "2026-08-01", "to": "2026-08-31"}})
        self.assertEqual(a["labels"], ["2026-08"])
        empty = db.connect(":memory:")
        db.import_user_tables(empty, snapshot())
        a = dashboard.resolve(empty, {}, {"source": "spending", "metric": "spend"})
        self.assertEqual((a["labels"], a["series"]), ([], []))
        empty.close()


class StatSourceTest(unittest.TestCase):
    """The sources that read the cached stat results."""

    def resolve(self, query):
        return dashboard.resolve(None, RESULTS, query)

    def test_a_month_metric_of_a_stat(self):
        a = self.resolve({"source": "monthly-cashflow", "metric": "income"})
        self.assertEqual(a["labels"], ["2026-07", "2026-08"])
        self.assertEqual(a["series"][0]["values_minor"], [250_000, 250_000])
        self.assertEqual(a["label_links"][0], {"from": "2026-07", "to": "2026-07", "income": "1"})
        self.assertEqual(minor_ints(a), [])
        # net is income less spending, so its rows are those two and never the transfers
        a = self.resolve({"source": "monthly-cashflow", "metric": "net"})
        self.assertEqual(a["label_links"][1], {"from": "2026-08", "to": "2026-08", "income": "1", "spend": "1"})
        # a stat with no link for a metric (a balance) gives its points none
        self.assertNotIn("label_links", self.resolve({"source": "savings-runway", "metric": "cash"}))

    def test_ranges_clamp_by_the_dimensions_grain(self):
        a = self.resolve({"source": "monthly-cashflow", "metric": "income",
                          "range": {"from": "2026-08"}})
        self.assertEqual(a["labels"], ["2026-08"])
        a = self.resolve({"source": "cpf", "metric": "total", "range": {"to": "2025-06"}})
        self.assertEqual((a["labels"], a["series"][0]["values_minor"]),
                         (["2024", "2025"], [15_000, 30_000]))
        a = self.resolve({"source": "portfolio", "metric": "value_sgd", "dimension": "period",
                          "range": {"from": "2026-07"}})
        self.assertEqual(a["labels"], [])

    def test_units_other_than_cents_keep_their_names(self):
        a = self.resolve({"source": "savings-runway", "metric": "runway_months"})
        self.assertEqual((a["unit"], a["series"][0]["values"]), ("months", [4.5, 5.2]))
        a = self.resolve({"source": "needs-wants-savings", "metric": "want_share"})
        self.assertEqual((a["unit"], a["series"][0]["values"]), ("fraction", [0.24]))
        a = self.resolve({"source": "category-breakdown", "metric": "share"})
        self.assertEqual((a["unit"], a["series"][0]["values"]), ("percent", [47.9]))

    def test_holdings_years_and_the_parts_of_net_worth(self):
        a = self.resolve({"source": "portfolio", "metric": "value", "dimension": "holding"})
        self.assertEqual((a["labels"], a["series"][0]["values_minor"]), (["VT"], [1_000_000]))
        a = self.resolve({"source": "net-worth", "dimension": "part"})
        self.assertEqual(a["labels"], ["cash", "investments", "cpf"])
        self.assertEqual(a["series"][0]["values_minor"], [910_000, 1_000_000, 30_000])
        a = self.resolve({"source": "net-worth", "metric": "total", "range": {"from": "2026-01"}})
        # the series starts when every part has been measured once: cash's
        # first complete month here (net_worth.py carries each part forward)
        self.assertEqual(a["labels"], ["2026-07", "2026-08"])
        self.assertEqual(a["series"][0]["values_minor"], [900_000 + 1_000_000 + 30_000,
                                                         910_000 + 1_000_000 + 30_000])

    def test_a_stat_not_run_yet_is_a_404_and_a_projection_carries_its_note(self):
        with self.assertRaises(NotFound):
            dashboard.resolve(None, {}, {"source": "monthly-cashflow", "metric": "income"})
        a = self.resolve({"source": "forward-view", "metric": "cash"})
        self.assertIn("projection", a["note"])
        self.assertEqual(a["series"][0]["values_minor"], [950_000, 990_000])


class ScenarioTest(unittest.TestCase):
    def setUp(self):
        self.conn = record(rows(), trips_by_key={"k4": "Trip A"})
        self.addCleanup(self.conn.close)

    def test_the_preview_extends_by_months_on_the_trailing_average(self):
        a = dashboard.scenario_preview(self.conn, {}, {
            "query": {"source": "spending", "metric": "spend"}, "months": 2, "delta_minor": 500})
        self.assertEqual(a["projected_from"], 2)
        self.assertEqual(a["labels"][-2:], ["2026-09", "2026-10"])
        # the trailing monthly average of (2000, 1750) is 1875, plus 500
        self.assertEqual(a["series"][0]["values_minor"][-2:], [2375, 2375])
        self.assertIn("estimate", a["note"])

    def test_it_writes_nothing_anywhere(self):
        plan = self.conn.execute("SELECT COUNT(*) FROM plan").fetchone()[0]
        dashboard.scenario_preview(self.conn, {}, {"query": {"source": "spending", "metric": "spend"}})
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM plan").fetchone()[0], plan)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM setting").fetchone()[0], 0)

    def test_what_it_refuses(self):
        good = {"query": {"source": "spending", "metric": "spend"}}
        for body in ({"query": {"source": "savings-runway", "metric": "runway_months"}},
                     {"query": {"source": "spending", "metric": "spend", "dimension": "bucket"}},
                     {**good, "months": 0}, {**good, "months": 37}, {**good, "months": 2.5},
                     {**good, "delta_minor": 1.5}, {"query": {"source": "nope"}}):
            with self.assertRaises(dashboard.DashboardRefused, msg=body):
                dashboard.scenario_preview(self.conn, RESULTS, body)


class EndpointTest(unittest.TestCase):
    """The routes end to end through serve.answer and serve.write."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        env = mock.patch.dict(os.environ, {"KOPIFUND_DATA": str(root),
                                           "KOPIFUND_DB": str(root / "kopifund.db"),
                                           "KOPIFUND_CACHE": str(root / "cache")})
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(self.tmp.cleanup)
        results = mock.patch("finance.results_cache._shared", lambda req=None: RESULTS)
        results.start()
        self.addCleanup(results.stop)
        with closing(db.connect()) as conn:
            db.import_user_tables(conn, snapshot())
            db.load_parsed(conn, parsed([block()], rows()), trips_by_key={"k4": "Trip A"},
                           payees=[{"name": "Grab", "kind": "Merchant", "country": "SGP"}])

    def get(self, path, query=None):
        return serve.answer(path, query or {})

    def post(self, path, body):
        return serve.write(path, {}, json.dumps(body).encode())

    QUERY = {"source": "spending", "metric": "spend", "dimension": "bucket",
             "range": {"from": "2026-08", "to": "2026-08"}}

    # ---- layouts

    def test_a_layout_document_round_trips_with_its_widgets(self):
        body = {"layouts": [{"id": "main", "name": "Home",
                             "widgets": [{"id": "w-pie", "type": "pie", "title": "Buckets",
                                          "layout": {"x": 0, "y": 0, "w": 4, "h": 3},
                                          "query": self.QUERY,
                                          "options": {"legend": False}}]}],
                "active": "main"}
        saved = self.post("/api/dashboard/layouts", body)
        got = self.get("/api/dashboard/layouts")
        self.assertEqual(saved["layouts"][0]["name"], "Home")
        self.assertEqual(got["active"], "main")
        widget = got["layouts"][0]["widgets"][0]
        self.assertEqual(widget["query"], self.QUERY)
        self.assertEqual(widget["layout"], {"x": 0, "y": 0, "w": 4, "h": 3})
        self.assertEqual(minor_ints(got), [])
        self.assertEqual(self.get("/api/dashboard/layouts/main")["name"], "Home")
        # the single-layout write upserts under the id its path names
        self.post("/api/dashboard/layouts/side", {"name": "Side", "widgets": []})
        self.assertEqual([d["id"] for d in self.get("/api/dashboard/layouts")["layouts"]],
                         ["main", "side"])

    def test_layout_writes_are_checked(self):
        for body in ({"layouts": [{"id": "Main", "name": "x"}]},                 # ids are slugs
                     {"layouts": [{"id": "a", "name": ""}]},                     # a name is text
                     {"layouts": [{"id": "a", "name": "x"}, {"id": "a", "name": "y"}]},
                     {"layouts": [{"id": "a", "name": "x",
                                   "widgets": [{"id": "w", "type": "pie", "type2": 1}]}]},
                     {"layouts": [{"id": "a", "name": "x",
                                   "widgets": [{"id": "w", "type": "pie"},
                                               {"id": "w", "type": "kpi"}]}]},
                     {"layouts": [{"id": "a", "name": "x",
                                   "widgets": [{"id": "w", "type": "pie",
                                                "query": {"source": "nope"}}]}]},
                     {"layouts": [], "active": "ghost"}):
            with self.assertRaises(serve.Refused, msg=body):
                self.post("/api/dashboard/layouts", body)
        with self.assertRaises(serve.NotFound):
            self.get("/api/dashboard/layouts/ghost")
        with self.assertRaises(ValueError):                      # bad q JSON is a 400
            self.get("/api/dashboard/query", {"q": "{nope"})

    def test_a_save_stamps_its_own_time_and_an_unchanged_layout_keeps_its_stamp(self):
        with mock.patch("finance.dashboard._now", return_value="2026-11-01T00:00:00+00:00"):
            first = self.post("/api/dashboard/layouts/main", {"name": "Home", "widgets": []})
            self.post("/api/dashboard/layouts/side", {"name": "Side", "widgets": []})
        with mock.patch("finance.dashboard._now", return_value="2026-11-02T00:00:00+00:00"):
            # the client echoes the stamp it read; the server's own time wins
            again = self.post("/api/dashboard/layouts/main", {**first, "name": "Home 2"})
            self.assertEqual(again["updated_at"], "2026-11-02T00:00:00+00:00")
            # a list write (a board switch) moves only the layouts that changed
            by = {d["id"]: d for d in self.get("/api/dashboard/layouts")["layouts"]}
            out = self.post("/api/dashboard/layouts", {
                "layouts": [by["main"], {**by["side"], "name": "Side 2"}], "active": "main"})
        stamps = {d["id"]: d["updated_at"] for d in out["layouts"]}
        self.assertEqual(stamps, {"main": "2026-11-02T00:00:00+00:00", "side": "2026-11-02T00:00:00+00:00"})
        with mock.patch("finance.dashboard._now", return_value="2026-11-03T00:00:00+00:00"):
            out = self.post("/api/dashboard/layouts", {"layouts": out["layouts"], "active": "side"})
        self.assertEqual({d["id"]: d["updated_at"] for d in out["layouts"]}, stamps)

    def test_the_state_writes_read_and_write_in_one_transaction(self):
        # a read outside the write's transaction lets another tab's save land between them, lost
        seen = []
        real = dashboard._doc

        def doc(conn, key, default):
            seen.append((key, conn.in_transaction))
            return real(conn, key, default)

        with mock.patch("finance.dashboard._doc", doc):
            for path, body in (("/api/dashboard/layouts/main", {"name": "Home", "widgets": []}),
                               ("/api/dashboard/layouts", {"layouts": [{"id": "main", "name": "Home"}]}),
                               ("/api/dashboard/goals", {"name": "Aside", "target_minor": 100}),
                               ("/api/dashboard/goals", {"id": "g-1", "name": "Aside 2"}),
                               ("/api/dashboard/goals/remove", {"id": "g-1"})):
                seen.clear()
                self.post(path, body)
                # the read the write builds on is inside its transaction (the answer's reads after need not be)
                self.assertEqual(seen[0][1], True, path)
        # and the list and the active id land together or not at all
        real_put = dashboard._put

        def put(conn, key, value):
            if key == dashboard.ACTIVE_KEY:
                raise OSError("disk full")
            real_put(conn, key, value)

        with mock.patch("finance.dashboard._put", put), self.assertRaises(OSError):
            self.post("/api/dashboard/layouts", {"layouts": [{"id": "other", "name": "Other"}],
                                                 "active": "other"})
        self.assertEqual([d["id"] for d in self.get("/api/dashboard/layouts")["layouts"]], ["main"])

    # ---- the query endpoints

    def test_post_and_get_resolve_the_same_query(self):
        got = self.get("/api/dashboard/query", {"q": json.dumps(self.QUERY)})
        posted = self.post("/api/dashboard/query", self.QUERY)
        self.assertEqual(got, posted)
        self.assertEqual(got["series"], [{"name": "spend", "values_minor": [1750]}])
        self.assertEqual(minor_ints(got), [])
        with self.assertRaises(serve.Refused):
            self.post("/api/dashboard/query", {"source": "nonsense"})

    # ---- goals

    def test_goals_create_patch_and_remove(self):
        got = self.post("/api/dashboard/goals", {
            "name": "Six months aside", "target_minor": 5_000_000, "month": "2027-06",
            "milestones": [{"month": "2026-12", "target_minor": 1_000_000}],
            "source": "net-worth", "metric": "total", "note": "keep it boring"})
        goal = got["goals"][0]
        self.assertEqual(goal["id"], "g-1")
        self.assertEqual(goal["binding"], {"source": "net-worth", "metric": "total",
                                           "dimension": "month"})
        # its binding resolves: net worth's last figure over the invented results
        self.assertEqual(goal["current_minor"], 910_000 + 1_000_000 + 30_000)
        self.assertAlmostEqual(goal["progress"], goal["current_minor"] / 5_000_000)
        got = self.post("/api/dashboard/goals", {"id": "g-1", "name": "Renamed"})
        self.assertEqual((got["goals"][0]["name"], got["goals"][0]["binding"]["metric"]),
                         ("Renamed", "total"))                    # a patch keeps the binding
        self.assertEqual(self.get("/api/dashboard/goals")["goals"][0]["milestones"],
                         [{"month": "2026-12", "target_minor": 1_000_000}])
        self.assertEqual(self.post("/api/dashboard/goals/remove", {"id": "g-1"})["goals"], [])
        for body in ({"name": "x", "target_minor": -1}, {"name": "x", "target_minor": 1.5},
                     {"name": "x"}, {"name": "x", "target_minor": 100, "month": "2027-13"},
                     {"name": "x", "target_minor": 100, "source": "net-worth"},
                     {"name": "x", "target_minor": 100, "source": "savings-runway",
                      "metric": "runway_months"},                  # a goal is an amount
                     # a balance, not a flow whose last month is still running
                     {"name": "x", "target_minor": 100, "source": "spending", "metric": "spend"},
                     {"name": "x", "target_minor": 100, "source": "monthly-cashflow", "metric": "net"},
                     # not a slice, and not the projection's future
                     {"name": "x", "target_minor": 100, "source": "net-worth", "metric": "value",
                      "dimension": "part"},
                     {"name": "x", "target_minor": 100, "source": "forward-view", "metric": "cash"},
                     {"id": "g-9", "name": "x", "target_minor": 100},
                     {"name": "x", "target_minor": 100,
                      "milestones": [{"month": "2027-13", "target_minor": 1}]}):
            with self.assertRaises(serve.Refused, msg=body):
                self.post("/api/dashboard/goals", body)
        with self.assertRaises(serve.Refused):
            self.post("/api/dashboard/goals/remove", {"id": "g-9"})

    def test_a_goal_saved_with_a_binding_from_before_reads_unbound_and_still_edits(self):
        with closing(db.connect()) as conn:
            dashboard._put(conn, dashboard.GOALS_KEY, [{
                "id": "g-1", "name": "Old", "target_minor": 100, "month": None, "note": "", "milestones": [],
                "binding": {"source": "spending", "metric": "spend", "dimension": "month"}}])
        goal = self.get("/api/dashboard/goals")["goals"][0]
        self.assertNotIn("binding", goal)
        self.assertIsNone(goal["current_minor"])
        got = self.post("/api/dashboard/goals", {"id": "g-1", "name": "Renamed"})["goals"][0]
        self.assertEqual((got["name"], got.get("binding")), ("Renamed", None))
        got = self.post("/api/dashboard/goals", {"id": "g-1", "source": "net-worth", "metric": "total"})["goals"][0]
        self.assertEqual(got["binding"], {"source": "net-worth", "metric": "total", "dimension": "month"})

    # ---- scenario preview

    def test_the_preview_is_ephemeral(self):
        a = self.post("/api/dashboard/scenario/preview",
                      {"query": self.QUERY | {"dimension": "month"}, "months": 2})
        self.assertEqual(a["projected_from"], len(a["labels"]) - 2)
        self.assertTrue(all(isinstance(v, int) for v in a["series"][0]["values_minor"]))
        with self.assertRaises(serve.Refused):
            self.post("/api/dashboard/scenario/preview", {"query": self.QUERY, "months": 99})

    # ---- allocation and the game pot

    def test_the_allocation_template_and_a_months_actuals(self):
        got = self.post("/api/dashboard/allocation",
                        {"buckets": [{"bucket": "Getting around", "planned_minor": 10_000}]})
        bucket = next(b for b in got["buckets"] if b["bucket"] == "Getting around")
        # the month's actuals count every External spend row, trips included,
        # as planning.budgets does: 1750 at home + 3000 away
        self.assertEqual((bucket["planned_minor"], bucket["actual_minor"]), (10_000, 4750))
        got = self.get("/api/dashboard/allocation", {"month": "2026-08"})
        self.assertEqual(got["income_minor"], 250_000)
        self.assertEqual(got["unallocated_minor"], 240_000)
        self.assertAlmostEqual(next(b for b in got["buckets"]
                                    if b["bucket"] == "Getting around")["share"], 4750 / 250_000)
        self.assertEqual(minor_ints(got), [])
        with self.assertRaises(serve.Refused):
            self.post("/api/dashboard/allocation", {"buckets": [{"bucket": "Nope",
                                                                 "planned_minor": 1}]})
        with self.assertRaises(serve.Refused):
            self.post("/api/dashboard/allocation", {"buckets": [{"bucket": "Food",
                                                                 "planned_minor": -1}]})

    def test_the_game_pot(self):
        got = self.get("/api/dashboard/game-pot")
        self.assertEqual((got["on"], got["account_ids"]), (False, []))
        account_id = got["accounts"][0]["id"]
        got = self.post("/api/dashboard/game-pot",
                        {"name": "Punt", "benchmark": "VWRA", "account_ids": [account_id]})
        self.assertEqual((got["on"], got["benchmark"], got["account_ids"]),
                         (True, "VWRA", [account_id]))
        for body in ({"name": ""}, {"name": "x", "account_ids": [99999]},
                     {"name": "x", "account_ids": ["1"]}):
            with self.assertRaises(serve.Refused, msg=body):
                self.post("/api/dashboard/game-pot", body)

    # ---- before the record has the months asked

    def test_empty_answers_for_a_range_the_record_has_not_seen(self):
        self.assertEqual(self.get("/api/dashboard/layouts"), {"layouts": [], "active": None})
        self.assertEqual(self.get("/api/dashboard/goals"), {"goals": []})
        a = self.get("/api/dashboard/query", {"q": json.dumps(
            {**self.QUERY, "range": {"from": "2027-01", "to": "2027-02"}})})
        self.assertEqual((a["labels"], a["series"]), ([], []))
