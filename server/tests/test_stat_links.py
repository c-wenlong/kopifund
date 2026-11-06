"""G13: a link from a bar into Transactions lists exactly the rows its figure counts.

A bar used to open every row of its month or its category, which differs from
the figure whenever the stat leaves rows out: transfers, made-up rows, a
refund's other side, overseas rows, rows with no SGD value, or a bucket. Each
stat now says, per plotted series, the Transactions filters that list its rows
(`chart.links`, server/analytics/stats/common.py, `result`), and the explorer says the
same for its series (server/finance/spending.py, `link`).

The record below holds invented rows built to tell a right filter from a near
one: a refund, a made-up filler, a row with no SGD value, an overseas row, a
move between own accounts, a wallet top-up, a payee known only by its
counterparty, and a salary in two categories. Every test follows a bar the way
the page does (its `{x}` filled in, the address's filters sent to the same
function the page asks) and holds the bar's figure to the rows that come back.
"""
import isolate  # noqa: F401  first: no test writes the real record or cache
import json
import unittest

from analytics import stats
from analytics.stats import common as c
from analytics_fixture import settings, snapshot as fixture_snapshot
from finance import api, dashboard, db, spending
from finance.db import SYNTHETIC_TYPE
import support
from test_db import block, row

EMERGENCY = {"name": "Emergency fund", "bucket": "Transfer", "group": "Savings",
             "need_class": "Savings", "sort_order": 235}
FOREIGN = block(period="X · USD · 2026-07", currency="USD", account_no="X", fx_to_sgd="")


def _rows():
    """(key, day, amount, extras): every row the tests count, each there for a reason."""
    internal = "Internal - own account"
    in_ = {"direction": "In"}
    return [
        # income: a salary in two categories, interest, a refund category, and a made-up salary filler
        ("i1", "2026-05-25", "3000.00", {"category": "Salary", "payee": "Acme", **in_}),
        ("i2", "2026-06-25", "3000.00", {"category": "Salary", "payee": "Acme", **in_}),
        ("i3", "2026-06-26", "400.00", {"category": "Freelance income", "payee": "Acme", **in_}),
        ("i4", "2026-07-25", "3100.00", {"category": "Salary", "payee": "Acme", **in_}),
        ("i5", "2026-07-30", "12.00", {"category": "Interest", "payee": "", **in_}),
        ("i6", "2026-07-31", "50.00", {"category": "Refunds and cashback", "payee": "", **in_}),
        ("i7", "2026-06-27", "7.00", {"category": "Salary", "payee": "Acme", "bank_txn_type": SYNTHETIC_TYPE, **in_}),
        # spending: needs, wants, a refund inside a spend category, a payee known by its counterparty
        ("s1", "2026-05-02", "-900.00", {"category": "Rent", "payee": "Landlord"}),
        ("s2", "2026-05-10", "-60.40", {"category": "Groceries", "payee": "Market"}),
        ("s3", "2026-05-12", "-25.00", {"category": "Eating out", "payee": "Noodle Bar"}),
        ("s4", "2026-05-13", "-4.00", {"category": "Coffee", "payee": "Bean"}),
        ("s5", "2026-06-02", "-900.00", {"category": "Rent", "payee": "Landlord"}),
        ("s6", "2026-06-14", "-33.00", {"category": "Eating out", "payee": "Noodle Bar"}),
        ("s7", "2026-06-15", "10.00", {"category": "Eating out", "payee": "Noodle Bar", **in_}),
        ("s8", "2026-06-20", "-45.00", {"category": "Shopping", "payee": "Gadget"}),
        ("s9", "2026-07-03", "-70.00", {"category": "Groceries", "payee": "Market"}),
        ("s10", "2026-07-05", "-18.00", {"category": "Eating out", "payee": "", "merchant": "", "counterparty": "ALICE"}),
        ("s11", "2026-07-06", "-22.00", {"category": "Eating out", "payee": "", "merchant": "STALL 9"}),
        ("s12", "2026-07-07", "-15.00", {"category": "Unidentified", "payee": "Mystery"}),
        ("s13", "2026-07-10", "-120.00", {"category": "Travel", "payee": "Hostel"}),
        ("s14", "2026-07-11", "-30.00", {"category": "Eating out", "payee": "Noodle Bar"}),
        ("s17", "2026-05-21", "-20.00", {"category": "Health", "payee": "Clinic"}),
        ("s18", "2026-06-09", "-40.00", {"category": "Fitness", "payee": "Gym"}),
        # a spending row whose statement printed no rate, and a made-up one: neither has a figure to sit in
        ("s15", "2026-07-12", "-10.00", {"category": "Eating out", "payee": "Noodle Bar", "currency": "USD",
                                         "account_no": "X", "period": "X · USD · 2026-07"}),
        ("s16", "2026-06-18", "-9.00", {"category": "Groceries", "payee": "Market", "bank_txn_type": SYNTHETIC_TYPE}),
        # transfers: investing and saving count as savings; a wallet top-up and a move between own accounts do not
        ("t1", "2026-05-15", "-500.00", {"category": "Investments", "payee": "Broker"}),
        ("t2", "2026-06-15", "-500.00", {"category": "Investments", "payee": "Broker"}),
        ("t3", "2026-07-15", "-200.00", {"category": "Emergency fund", "payee": ""}),
        ("t4", "2026-06-16", "-80.00", {"category": "Wallet top-up", "payee": ""}),
        ("t5", "2026-06-17", "-300.00", {"category": "Between own accounts", "payee": "", "flow_class": internal}),
        # no category: two real ones, one on a move between own accounts, one made up (External), one made up (internal)
        ("u1", "2026-05-20", "-12.00", {"category": "", "payee": ""}),
        ("u2", "2026-06-21", "-8.00", {"category": "", "payee": ""}),
        ("u3", "2026-06-22", "-6.00", {"category": "", "payee": "", "flow_class": internal}),
        ("u4", "2026-07-02", "5.00", {"category": "", "payee": "", "bank_txn_type": SYNTHETIC_TYPE, **in_}),
        ("u5", "2026-06-23", "-4.00", {"category": "", "payee": "", "bank_txn_type": SYNTHETIC_TYPE,
                                       "flow_class": internal}),
    ]


def record():
    keyed = []
    for key, day, amount, extra in _rows():
        extra = dict(extra)
        payee = extra.pop("payee", "")
        keyed.append(row(key, amount, date=day, payee=payee, merchant=extra.pop("merchant", payee.upper() or f"SHOP {key}"),
                         rule="r1", **extra))
    conn = support.build_record(
        keyed, user=support.user_tables(categories=[*db.shipped_categories(), EMERGENCY]), blocks=[block(), FOREIGN],
        payees=support.payees(("Acme", "Landlord", "Market", "Noodle Bar", "Bean", "Gadget", "Mystery", "Hostel",
                               "Broker", "Clinic", "Gym")))
    # the trip: the hostel and one meal are overseas
    conn.execute("UPDATE txn SET trip_id = (SELECT id FROM trip LIMIT 1) WHERE txn_key IN ('s13', 's14', 's15')")
    return conn


def sgd(x):
    return round(x * 100)


def cents_of(series, figure):
    """G20: a plotted figure in cents: a `_minor` series is cents already, any other dollars."""
    return figure if series.endswith("_minor") else sgd(figure)


def listed(conn, params, hide="1"):
    """What Transactions lists for these filters, as the page asks (transfers hidden unless the link says not)."""
    page = api.transactions(conn, {"hide_transfers": hide, "limit": "500", **{k: str(v) for k, v in params.items()}})
    return page["total"], page["sum_sgd"], page


def filled(spec, x):
    return {k: v.replace("{x}", str(x)) for k, v in spec.items()}


_SETTINGS = None


def setUpModule():
    global _SETTINGS
    _SETTINGS = settings()
    _SETTINGS.__enter__()


def tearDownModule():
    _SETTINGS.__exit__(None, None, None)


class StatBars(unittest.TestCase):
    """For each stat, every linked bar's figure equals what its link lists; a bar with no link is not a row sum."""

    @classmethod
    def setUpClass(cls):
        cls.conn = record()
        cls.snap = db.snapshot(cls.conn)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def run_stat(self, stat_id):
        return stats.by_id()[stat_id].run(self.snap, None)

    def follow(self, res, signs):
        """{series: [(x, figure in cents, listed in cents or rows)]}: every bar followed the way the page follows it."""
        chart = res["chart"]
        out = {}
        for series, sign in signs.items():
            spec = chart["links"][series]
            bars = []
            for r in res["data"][chart["source"]]:
                n, total, _ = listed(self.conn, filled(spec, r[chart["x"]]))
                bars.append((r[chart["x"]], r[series], n if sign == "count" else sign * total / 100))
            out[series] = bars
        return out

    def assert_bars(self, res, signs, minimum=2):
        followed = self.follow(res, signs)
        for series, bars in followed.items():
            with self.subTest(series=series):
                self.assertGreaterEqual(sum(1 for _, figure, _ in bars if figure), minimum, f"{series} has no figures to hold")
                for x, figure, got in bars:
                    self.assertEqual(cents_of(series, figure) if signs[series] != "count" else figure,
                                     sgd(got) if signs[series] != "count" else got, f"{series} at {x}")

    def test_monthly_cash_flow_income_spend_and_net(self):
        res = self.run_stat("monthly-cashflow")
        self.assert_bars(res, {"income_minor": 1, "spend_minor": -1, "net_minor": 1,
                               "investing_minor": -1}, minimum=2)
        # investing is the Investment transfers only: not the emergency fund, not a wallet top-up
        _, _, page = listed(self.conn, filled(res["chart"]["links"]["investing_minor"], "2026-06"))
        self.assertEqual(sorted(r["key"] for r in page["rows"]), ["t2"])
        # a month's rows alone are not any of the three: a refund, a filler, a transfer and a row with no rate sit in it
        n, total, _ = listed(self.conn, {"month": "2026-06"})
        june = next(m for m in res["data"]["months"] if m["month"] == "2026-06")
        self.assertNotEqual(june["spend_minor"], -total)
        self.assertNotEqual(june["net_minor"], total)

    def test_needs_wants_and_savings(self):
        res = self.run_stat("needs-wants-savings")
        self.assert_bars(res, {"income_minor": 1, "need_minor": -1, "want_minor": -1, "savings_minor": -1},
                         minimum=3)
        # savings are the investing and saving transfers: not a wallet top-up, not a move between own accounts
        _, _, page = listed(self.conn, filled(res["chart"]["links"]["savings_minor"], "2026-06"))
        self.assertEqual(sorted(r["key"] for r in page["rows"]), ["t2"])
        _, _, page = listed(self.conn, filled(res["chart"]["links"]["want_minor"], "2026-06"))
        self.assertEqual(sorted(r["key"] for r in page["rows"]), ["s18", "s6", "s7", "s8"])

    def test_a_months_spending_beside_cash_and_runway(self):
        res = self.run_stat("savings-runway")
        self.assert_bars(res, {"spend_minor": -1}, minimum=3)
        self.assertEqual(set(res["chart"]["links"]), {"spend_minor"})    # cash is a balance, runway a ratio

    def test_salary_and_other_income(self):
        res = self.run_stat("income-stability")
        self.assert_bars(res, {"salary_minor": 1, "other_minor": 1}, minimum=1)
        # salary is every salary category, the made-up salary filler is neither
        _, _, page = listed(self.conn, filled(res["chart"]["links"]["salary_minor"], "2026-06"))
        self.assertEqual(sorted(r["key"] for r in page["rows"]), ["i2", "i3"])
        _, _, page = listed(self.conn, filled(res["chart"]["links"]["other_minor"], "2026-07"))
        self.assertEqual(sorted(r["key"] for r in page["rows"]), ["i5", "i6"])

    def test_a_payees_spending(self):
        res = self.run_stat("merchant-concentration")
        self.assert_bars(res, {"spend_minor": -1}, minimum=5)
        payees = {p["payee"] for p in res["data"]["top_by_spend"]}
        # a payee the rules did not name is named by what the statement printed, else its counterparty
        self.assertTrue({"STALL 9", "ALICE", "Noodle Bar"} <= payees)
        _, _, page = listed(self.conn, filled(res["chart"]["links"]["spend_minor"], "Noodle Bar"))
        self.assertEqual(sorted(r["key"] for r in page["rows"]), ["s14", "s3", "s6", "s7"])   # not the row with no rate

    def test_a_trips_spending_and_not_its_rate_a_day(self):
        res = self.run_stat("travel")
        self.assert_bars(res, {"spend_minor": -1}, minimum=1)
        self.assertNotIn("spend_per_day_minor", res["chart"]["links"])
        _, _, page = listed(self.conn, filled(res["chart"]["links"]["spend_minor"], "Trip A"))
        self.assertEqual(sorted(r["key"] for r in page["rows"]), ["s13", "s14"])   # not the row with no rate

    def test_data_quality_counts_rows(self):
        res = self.run_stat("data-quality")
        self.assert_bars(res, {"uncategorised": "count", "synthetic": "count"}, minimum=2)
        by_month = {m["month"]: m for m in res["data"]["by_month"]}
        # the move between own accounts with no category is not counted, the made-up External one is
        self.assertEqual((by_month["2026-06"]["uncategorised"], by_month["2026-07"]["uncategorised"]), (1, 1))
        # every made-up row, the internal one too, and the page's default of hiding transfers does not hide it
        self.assertEqual(by_month["2026-06"]["synthetic"], 3)
        _, _, page = listed(self.conn, filled(res["chart"]["links"]["synthetic"], "2026-06"))
        self.assertEqual(sorted(r["key"] for r in page["rows"]), ["i7", "s16", "u5"])

    def test_every_week_of_eating_out_lists_the_rows_it_adds_up(self):
        res = self.run_stat("eating-out")
        weeks = res["data"]["weeks"]
        spec = res["chart"]["links"]["spend_minor"]

        def link_of(w):
            # the page fills {x} with the point's week and {name} with another field of its row
            return {k: v.replace("{x}", w["week"]).replace("{end}", w["end"]) for k, v in spec.items()}

        counted = 0
        for w in weeks:
            n, total, page = listed(self.conn, link_of(w))
            with self.subTest(week=w["week"]):
                self.assertEqual(-total, w["spend_minor"])
                counted += bool(w["spend_minor"])
                # a week with no meal lists nothing, and no row sits outside its seven days
                self.assertEqual(n == 0, not w["spend_minor"] and not w["count"])
                self.assertTrue(all(w["week"] <= r["date"] <= w["end"] for r in page["rows"]))
        self.assertGreaterEqual(counted, 3)
        self.assertGreater(len(weeks), counted)    # the weeks away are there, at zero
        # a Sunday's meal is in its own week and the refund on the Monday after is in the next
        by_week = {w["week"]: w for w in weeks}
        sunday, monday = by_week["2026-06-08"], by_week["2026-06-15"]
        self.assertEqual((sunday["end"], sunday["spend_minor"], monday["spend_minor"]), ("2026-06-14", 3300, -1000))
        self.assertEqual([r["key"] for r in listed(self.conn, link_of(sunday))[2]["rows"]], ["s6"])
        self.assertEqual([r["key"] for r in listed(self.conn, link_of(monday))[2]["rows"]], ["s7"])
        # the weeks add up to the stat's total, so no meal is missing from every week's list
        self.assertEqual(sum(w["spend_minor"] for w in weeks), res["data"]["summary"]["spend_minor"])

    def test_a_stat_whose_bar_is_not_a_sum_of_rows_has_no_link_for_it(self):
        """An average, an estimate or a balance cannot be a list of rows, so its bar does not link."""
        snap = fixture_snapshot()
        for module in stats.modules():
            chart = module.CHART
            if chart["type"] not in ("bar", "stacked"):
                continue
            res = module.run(snap, None)
            linked = set(res["chart"].get("links", {}))
            unlinked = {s for s in chart["series"] if s not in linked}
            with self.subTest(stat=module.ID):
                # a link past the drawn series names a field of the same rows, which a dashboard widget plots
                rows = res["data"][chart["source"]]
                self.assertLessEqual(linked - set(chart["series"]), set(rows[0]) if rows else set())
                self.assertEqual(unlinked, UNLINKED.get(module.ID, set()))


# the series of a bar or stacked chart that are an average, an estimate or a share, per stat
UNLINKED = {
    "category-breakdown": {"per_month_minor"},      # a total over the covered months, divided by them
    "recurring-detector": {"monthly_cost_minor"},   # a typical charge scaled to a month
    "travel": {"spend_per_day_minor"},              # a trip's spend over its days
    "cpf": {"oa_minor", "sa_minor", "ma_minor"},    # balances
}


class Explorer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.conn = record()

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def explore(self, **q):
        return spending.explore(self.conn, q)

    def test_every_segment_opens_the_rows_it_adds_up(self):
        seen_folded = 0
        for scope in ("sg", "all"):
            for by in ("bucket", "category", "payee"):
                d = self.explore(scope=scope, by=by)
                for s in d["series"]:
                    for m, value in zip(d["months"], s["values"]):
                        with self.subTest(scope=scope, by=by, series=s["name"], month=m):
                            _, total, _ = listed(self.conn, {**d["link"], **s["link"], "month": m})
                            self.assertEqual(-total, value)
                    seen_folded += "folded" in s
        self.assertEqual(seen_folded, 4)    # by category and by payee, in both scopes, had more than seven

    def test_a_month_opens_the_rows_of_every_segment_together(self):
        for scope in ("sg", "all"):
            d = self.explore(scope=scope, by="payee")
            for i, m in enumerate(d["months"]):
                with self.subTest(scope=scope, month=m):
                    self.assertEqual(-listed(self.conn, {**d["link"], "month": m})[1], sum(s["values"][i] for s in d["series"]))

    def test_the_range_and_where_it_went_and_top_payees(self):
        d = self.explore(scope="sg", by="bucket")
        for b in d["buckets"]:
            for cat in b["categories"]:
                with self.subTest(category=cat["name"]):
                    self.assertEqual(-listed(self.conn, {**d["link"], **cat["link"]})[1], cat["total"])
        for p in d["payees"]:
            with self.subTest(payee=p["name"]):
                n, total, _ = listed(self.conn, {**d["link"], **p["link"]})
                self.assertEqual((-total, n), (p["total"], p["count"]))

    def test_singapore_only_leaves_the_overseas_rows_out_of_every_link(self):
        sg = self.explore(scope="sg")
        self.assertEqual(sg["link"].get("home"), "1")
        self.assertNotIn("home", self.explore(scope="all")["link"])
        keys = {r["key"] for r in listed(self.conn, {**sg["link"], "month": "2026-07"})[2]["rows"]}
        self.assertTrue(keys.isdisjoint({"s13", "s14"}))

    def test_a_series_name_with_a_comma_still_finds_its_rows(self):
        self.conn.execute("UPDATE category SET name = 'Eating, out' WHERE name = 'Eating out'")
        d = self.explore(by="category", scope="all")
        for s in d["series"]:
            for m, value in zip(d["months"], s["values"]):
                self.assertEqual(-listed(self.conn, {**d["link"], **s["link"], "month": m})[1], value)


# G26: a dashboard metric's sign against what Transactions sums: money out is
# counted positive, so its rows sum to minus the figure
OUT = {"spend", "transfers", "investing", "need", "want", "savings"}


class DashboardPoints(unittest.TestCase):
    """G26: every point a dashboard widget links opens exactly the rows its figure counts, to the cent."""

    @classmethod
    def setUpClass(cls):
        cls.conn = record()
        snap = db.snapshot(cls.conn)
        cls.results = {i: stats.by_id()[i].run(snap, None)
                       for i in ("monthly-cashflow", "needs-wants-savings", "savings-runway")}

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def follow(self, query):
        """(points followed, points with no link): each point's figure held to the rows its link lists,
        following it the way the widgets do (pointHref: the label's link, then the series')."""
        a = dashboard.resolve(self.conn, self.results, query)
        sign = -1 if query["metric"] in OUT else 1
        followed, unlinked = 0, 0
        for s in a["series"]:
            for i, figure in enumerate(s["values_minor"]):
                base = (a.get("label_links") or [None] * len(a["labels"]))[i]
                link = None if s.get("link", {}) is None or not (base or s.get("link")) \
                    else {**(base or {}), **(s.get("link") or {})}
                if link is None:
                    unlinked += 1
                    continue
                with self.subTest(query=query, series=s["name"], label=a["labels"][i]):
                    self.assertEqual(sign * listed(self.conn, link)[1], figure)
                followed += 1
        return followed, unlinked

    def test_every_spending_point_lists_its_rows(self):
        followed = 0
        ranges = [None, {"from": "2026-05", "to": "2026-05"}, {"from": "2026-06", "to": "2026-06"},
                  {"from": "2026-07", "to": "2026-07"}]
        for metric in ("spend", "income", "transfers", "net"):
            for scope in ("sg", "all"):
                for rng in ranges:
                    base = {"source": "spending", "metric": metric, "filters": {"scope": scope}}
                    if rng:
                        base["range"] = rng
                    queries = [dict(base, dimension="month")]
                    queries += [dict(base, dimension="month", split=s) for s in dashboard.SPLITS]
                    queries += [dict(base, dimension=d) for d in ("bucket", "category", "payee", "account", "need")]
                    for q in queries:
                        followed += self.follow(q)[0]
        self.assertGreater(followed, 500)

    def test_a_query_filter_that_already_excludes_stays_in_the_folded_slice(self):
        for dim, filters in (("category", {"not_categories": json.dumps(["Coffee"])}),
                             ("payee", {"not_payees": json.dumps(["Bean"])})):
            q = {"source": "spending", "metric": "spend", "dimension": dim, "filters": {"scope": "all", **filters}}
            a = dashboard.resolve(self.conn, {}, q)
            self.assertEqual(a["labels"][-1], spending.OTHER, dim)
            self.follow(q)
            self.assertNotIn("s4", {r["key"] for r in listed(self.conn, a["label_links"][-1])[2]["rows"]})
        # an empty exclusion is no exclusion: the folded slice still answers and lists its rows
        for dim, key in (("category", "not_categories"), ("payee", "not_payees")):
            q = {"source": "spending", "metric": "spend", "dimension": dim, "filters": {"scope": "all", key: ""}}
            self.follow(q)
            self.follow({**q, "dimension": "month", "split": dim})

    def test_every_stat_point_lists_its_rows_through_the_stats_own_links(self):
        linked = {}
        for source, metrics in (("monthly-cashflow", ("income", "spend", "net", "investing")),
                                ("needs-wants-savings", ("income", "need", "want", "savings")),
                                ("savings-runway", ("cash", "spend", "carried"))):
            for metric in metrics:
                followed, unlinked = self.follow({"source": source, "metric": metric})
                if followed:
                    self.assertEqual(unlinked, 0, (source, metric))
                    linked.setdefault(source, set()).add(metric)
        # a balance links nowhere; every sum of rows does
        self.assertEqual(linked, {"monthly-cashflow": {"income", "spend", "net", "investing"},
                                  "needs-wants-savings": {"income", "need", "want", "savings"},
                                  "savings-runway": {"spend"}})

    def test_a_stat_link_names_its_month_as_a_range(self):
        a = dashboard.resolve(self.conn, self.results, {"source": "needs-wants-savings", "metric": "savings"})
        self.assertEqual(a["label_links"][0], {"from": a["labels"][0], "to": a["labels"][0], "transfer": "1",
                                               "need": "Investment,Savings", "hide_transfers": "0"})


class TripCards(unittest.TestCase):
    def test_a_trips_total_and_rows_are_what_its_link_lists(self):
        conn = record()
        self.addCleanup(conn.close)
        trips = api.trips(conn)["trips"]
        self.assertTrue(any(t["rows"] for t in trips))
        for t in trips:
            with self.subTest(trip=t["name"]):
                n, total, _ = listed(conn, {"trip": t["name"], "spend": "1"})
                self.assertEqual((-total, n), (t["spend_sgd"], t["rows"]))


class Filters(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.conn = record()

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def keys(self, **q):
        return sorted(r["key"] for r in listed(self.conn, q)[2]["rows"])

    def test_a_range_of_days_includes_both_ends_and_a_range_of_months_still_works(self):
        meals = dict(spend="1", categories='["Eating out"]')
        self.assertEqual(self.keys(**{"from": "2026-06-14", "to": "2026-06-15"}, **meals), ["s6", "s7"])
        self.assertEqual(self.keys(**{"from": "2026-06-14", "to": "2026-06-14"}, **meals), ["s6"])
        self.assertEqual(self.keys(**{"from": "2026-06-15"}, month="2026-06", **meals), ["s7"])
        self.assertEqual(self.keys(**{"from": "2026-06", "to": "2026-06"}, **meals), ["s6", "s7"])
        # a month end and a day end can sit in one range
        self.assertEqual(self.keys(**{"from": "2026-06", "to": "2026-06-14"}, **meals), ["s6"])

    def test_a_range_that_is_not_a_month_or_a_real_day_is_refused(self):
        for bad in ("2026-06-31", "2026-13-01", "June", "2026-6-1", "2026-06-1"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.keys(**{"from": bad})

    def test_income_spend_and_transfer_are_the_rows_the_stats_add_up(self):
        self.assertEqual(self.keys(income="1", month="2026-06"), ["i2", "i3"])
        self.assertIn("s7", self.keys(spend="1", month="2026-06"))
        self.assertEqual(self.keys(transfer="1", month="2026-06", hide_transfers="0"), ["t2", "t4"])
        # two together list the rows of either, which is what a net figure adds
        both = self.keys(income="1", spend="1", month="2026-05")
        self.assertEqual(both, sorted(self.keys(income="1", month="2026-05") + self.keys(spend="1", month="2026-05")))

    def test_need_bucket_categories_and_the_rest(self):
        self.assertEqual(self.keys(spend="1", need="Need", month="2026-05"), ["s1", "s17", "s2"])
        self.assertEqual(self.keys(spend="1", need="Need,Want", month="2026-05"), ["s1", "s17", "s2", "s3", "s4"])
        self.assertEqual(self.keys(spend="1", bucket="Food", month="2026-05"), ["s2", "s3", "s4"])
        self.assertEqual(self.keys(spend="1", month="2026-05", categories=json.dumps(["Rent", "Coffee"])), ["s1", "s4"])
        self.assertEqual(self.keys(spend="1", month="2026-05", not_categories=json.dumps(["Rent", "Coffee"])),
                         ["s17", "s2", "s3"])
        self.assertEqual(self.keys(spend="1", month="2026-07", not_payees=json.dumps(["Market", "Noodle Bar"])),
                         ["s10", "s11", "s12", "s13"])

    def test_external_and_synthetic(self):
        self.assertEqual(self.keys(external="1", category="(none)", month="2026-06", hide_transfers="0"), ["u2"])
        self.assertEqual(self.keys(category="(none)", month="2026-06", hide_transfers="0"), ["u2", "u3", "u5"])
        self.assertEqual(self.keys(synthetic="1", month="2026-07", hide_transfers="0"), ["u4"])
        self.assertNotIn("u4", self.keys(synthetic="0", month="2026-07"))

    def test_a_list_of_names_must_be_a_json_list(self):
        for bad in ("Rent", "[1]", '{"a": 1}'):
            with self.assertRaises(ValueError):
                api.transactions(self.conn, {"categories": bad})


if __name__ == "__main__":
    unittest.main()
