"""G20, the cents contract, with no dollars left (line 6).

A money field in a stat result is integer cents under `<name>_minor`, like the
record's columns. Every stat writes its money so (`MOVED`, all fifteen), and
nothing outside the stats reads a dollar: `server/finance/money.py` `stat_cents`
reads `<name>_minor` only, and a result from before G20, in dollars, is no
result (`money.in_cents`) until the stats run again. The stats count with one
rounding rule (server/analytics/stats/common.py).

The guard: every stat runs on the invented fixture, and a walk over every
result fails on any number held under a money name that is not whole cents
under `_minor`. A number is money unless its key says it is a count, a day, a
month, a share, a ratio or a setting (`NOT_MONEY`); the plain numbers a stat
keeps in their own units are named per stat in `EXCEPTIONS`, each with its
reason, and each holds only where its reason does, so it cannot hide a leak.
"""
import isolate  # noqa: F401  first: no test writes the real record or cache
import contextlib
import copy
import json
import random
import shutil
import tempfile
import unittest
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from unittest import mock

import analytics_fixture as fixture
from fake_notion import CFG, FakeNotion

from analytics import growth, publish, stats
from analytics.stats import common as c
from analytics.stats import forward_view as _fv
from analytics.stats.benchmarks_sg import is_money
from finance import db, export, home, metrics, money, net_worth, results_cache
from finance.errors import NotFound
from finance.money import from_cents, stat_cents, to_cents
from test_app_writes import record

FIXTURE_CONFIG = Path(__file__).resolve().parent / "fixtures" / "config.toml"

# G20: the stats that write cents themselves; line 6 finished the list
MOVED = {"monthly-cashflow", "savings-runway", "one-offs", "data-quality", "income-stability",
         "category-breakdown", "needs-wants-savings", "merchant-concentration", "eating-out", "travel",
         "recurring-detector", "forward-view", "portfolio", "cpf", "benchmarks-sg"}
TODAY = date(2025, 6, 20)        # the fixture's one-off is in June 2025
_SETTINGS = contextlib.ExitStack()
RESULTS = {}       # as the stats write them


def setUpModule():
    # the fixture's config, never the suite runner's plan; put back after this file
    _SETTINGS.enter_context(mock.patch.object(_fv, "PLAN_CONFIG", FIXTURE_CONFIG))
    _SETTINGS.enter_context(fixture.settings())
    snap = fixture.snapshot(portfolio=fixture.portfolio(), cpf=fixture.cpf())
    for m in stats.modules():
        RESULTS[m.ID] = (_fv.run(snap, None, fixture.PLAN) if m.ID == "forward-view"
                         else m.run(snap, fixture.BENCHMARKS))


def tearDownModule():
    _SETTINGS.close()


# A number in a result is money unless its key says it is a count, a day, a
# month, a share, a ratio or a setting, so a column named for an account or a
# category is money too. A money field that can be null is named here, since a
# null says nothing of its kind. Inside NOT_MONEY_WITHIN nothing is money:
# data-quality's counts keyed by each reason's words, needs-wants-savings'
# 50/30/20 targets and gaps, the detector's settings keyed by cadence.
NOT_MONEY = {
    # cash stats (line 2)
    "months", "count", "charges", "rows", "share", "share_of_month", "share_of_month_clamped",
    "share_of_spend", "multiple", "threshold", "threshold_multiple", "cv", "salary_cv",
    "months_without_salary", "longest_gap_without_salary", "months_present",
    "runway_months", "runway_all", "runway_6m", "rule_low", "rule_high", "rate_window_months",
    "accounts", "fix_limit", "fix_list_total", "external", "uncategorised", "no_payee",
    "synthetic", "unconverted", "partial_months", "transfer_bucket_external",
    # spending stats and the recurring detector (line 3)
    "change_pct", "covered_months", "share_clamped", "per_calendar_week", "per_week", "places",
    "ratio", "top_n", "weeks", "weeks_with_a_meal", "cumulative_share", "p50", "p80", "p90",
    "payees", "rank", "share_top10", "need_share", "want_share", "savings_share", "want_to_need",
    "days", "limit_days", "long_stay_days", "share_of_all_spend", "trip_days_summed", "trips",
    "active", "paused", "stopped", "recent", "occurrences", "cadence_days", "days_since_last",
    "fit", "amount_band", "tight_amount_band", "max_gap_factor", "merge_days", "min_occurrences",
    "pause_factor", "pause_floor_days", "stop_factor", "stop_floor_days",
    # holdings and references (line 4): counts, years, shares of a total, FX rates
    "years", "year", "stale_months", "statements", "unmatched_deposits", "match_days",
    "employer_share_last", "locked_share", "gain_pct", "quantity", "sgd_per_base", "fx_to_sgd_e9",
    "benchmark_items", "benchmark_items_derived",
    # forward-view (line 5): its CPF shares, its counts of months and a bonus's months
    "cpf_employee_rate", "cpf_employer_rate", "spend_window_months", "spend_phase_months",
    "bonus_months", "bonus_month"}
NOT_MONEY_WITHIN = {"reasons", "targets", "gaps", "min_span_days", "flagged_occurrences"}
NULLABLE_MONEY = {"trend_6m", "spend_per_day", "per_day", "ending_cash", "lowest_cash", "other_outflow",
                  "employee", "employer"}
# a name one stat counts with and another spends: forward-view's month costs its
# planned trips (`trips_minor`), where travel's `trips` is a count
MONEY_IN = {"forward-view": {"trips"}}


def _plain_figure(row):
    """A benchmarks-sg figure is in its own unit when the row's unit is not SGD."""
    return not is_money(row.get("unit"))


# Plain numbers a stat keeps under a name that looks like money, by stat, each
# with its reason and the test that it holds for this row. Nothing else is let off.
EXCEPTIONS = {
    "benchmarks-sg": {
        name: ("a reference figure, or the owner's figure set against it, in the row's own unit "
               "(months of cash, a per cent of income): not money, so not cents. A figure in SGD is "
               f"`{name}_minor`, so this holds only on a row whose unit is not SGD", _plain_figure)
        for name in ("owner", "benchmark", "value")},
    "portfolio": {
        name: ("a holding's price per share, as the statement prints it to four places or more: a "
               "price, not an amount of money held (server/analytics/SPEC.md, Holdings and references)",
               lambda row: True)
        for name in ("cost_price", "close_price")},
}


def excepted(stat_id, key, row):
    rule = EXCEPTIONS.get(stat_id, {}).get(key)
    return bool(rule) and rule[1](row)


def leaks(stat_id, result):
    """Every money field in a result that is not whole cents under `_minor`:
    (path, value) for each number under a money name, and for each `_minor`
    that is not an int or None."""
    out = []
    money_too = MONEY_IN.get(stat_id, frozenset())

    def walk(o, path):
        if isinstance(o, list):
            for i, x in enumerate(o):
                walk(x, f"{path}[{i}]")
        elif isinstance(o, dict):
            for k, v in o.items():
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    if k.endswith(money.MINOR):
                        if type(v) is not int:
                            out.append((f"{path}.{k}", v))
                    elif (k not in NOT_MONEY or k in money_too) and not excepted(stat_id, k, o):
                        out.append((f"{path}.{k}", v))
                elif v is not None and k.endswith(money.MINOR):
                    out.append((f"{path}.{k}", v))      # text, a list or a dict where cents belong
                if k not in NOT_MONEY_WITHIN:
                    walk(v, f"{path}.{k}")

    walk(result.get("data"), "data")
    walk(result.get("params"), "params")
    return out


def _rewrite_moved(o, stat_id, seen=None):
    """A result in dollars back in cents, by the guard's rule."""
    seen = set() if seen is None else seen
    if id(o) in seen:
        return
    seen.add(id(o))
    money_too = MONEY_IN.get(stat_id, frozenset())
    if isinstance(o, list):
        for x in o:
            _rewrite_moved(x, stat_id, seen)
    elif isinstance(o, dict):
        for k in [k for k in o if (k not in NOT_MONEY or k in money_too) and not excepted(stat_id, k, o)]:
            v = o[k]
            if (v is None and k in NULLABLE_MONEY) or (isinstance(v, (int, float)) and not isinstance(v, bool)):
                del o[k]
                o[k + money.MINOR] = None if v is None else to_cents(v)
        for k, v in o.items():
            if k not in NOT_MONEY_WITHIN:
                _rewrite_moved(v, stat_id, seen)


def in_cents(results):
    out = copy.deepcopy(results)
    for stat_id, r in out.items():
        _rewrite_moved(r.get("data"), stat_id)
        _rewrite_moved(r.get("params"), stat_id)
    return out


def _unwrite(o, seen=None):
    seen = set() if seen is None else seen
    if id(o) in seen:
        return
    seen.add(id(o))
    if isinstance(o, list):
        for x in o:
            _unwrite(x, seen)
    elif isinstance(o, dict):
        for k in [k for k in o if k.endswith(money.MINOR) and k[:-len(money.MINOR)]]:
            v = o[k]
            if v is None or (isinstance(v, int) and not isinstance(v, bool)):
                del o[k]
                o[k[:-len(money.MINOR)]] = None if v is None else float(from_cents(v))
        for v in o.values():
            _unwrite(v, seen)


def before_g20(result):
    """A result as its stat wrote it before G20: each `<name>_minor` as float
    dollars under `<name>`, and a chart hint in dollars (unit `sgd`)."""
    out = copy.deepcopy(result)
    _unwrite(out.get("data"))
    _unwrite(out.get("params"))
    chart = out.setdefault("chart", {})
    if chart.get("unit") == money.CENTS_UNIT:
        chart["unit"] = "sgd"
    chart["series"] = [s[:-len(money.MINOR)] if s.endswith(money.MINOR) else s for s in chart.get("series", ())]
    return out


def minor_fields(o):
    """Every `<name>_minor` value anywhere in `o`."""
    if isinstance(o, list):
        return [v for x in o for v in minor_fields(x)]
    if isinstance(o, dict):
        return [v for k, v in o.items() if k.endswith(money.MINOR)] \
            + [f for v in o.values() for f in minor_fields(v)]
    return []


class TheGuard(unittest.TestCase):
    """No result holds money that is not whole cents under `_minor`."""

    def test_every_stat_has_moved(self):
        self.assertEqual(MOVED, {m.ID for m in stats.modules()})
        self.assertEqual(set(RESULTS), MOVED)

    def test_no_result_holds_a_dollar(self):
        for stat_id in sorted(MOVED):
            with self.subTest(stat=stat_id):
                self.assertEqual(leaks(stat_id, RESULTS[stat_id]), [])
                self.assertTrue(money.in_cents(RESULTS[stat_id]))

    def test_every_money_stat_writes_cents_and_charts_them(self):
        for stat_id in sorted(MOVED):
            with self.subTest(stat=stat_id):
                body = [RESULTS[stat_id].get("data"), RESULTS[stat_id].get("params")]
                cents = minor_fields(body)
                self.assertTrue(cents)
                self.assertTrue(all(v is None or type(v) is int for v in cents), cents)
                self.assertEqual(RESULTS[stat_id]["chart"].get("unit"), "sgd_minor"
                                 if stat_id != "data-quality" else "count")
                # every plotted series that is money is a `_minor` field
                for series in RESULTS[stat_id]["chart"].get("series", ()):
                    self.assertTrue(series.endswith(money.MINOR) or series in (
                        "runway_months", "uncategorised", "synthetic", "owner", "benchmark"), series)

    def test_the_exceptions_are_used_and_hold_only_where_their_reason_does(self):
        bench = RESULTS["benchmarks-sg"]["data"]
        plain = [r for part in ("comparisons", "headline", "metrics", "unmatched") for r in bench[part]
                 if any(k in r for k in ("owner", "benchmark", "value"))]
        self.assertTrue(plain)                              # the exception is not dead
        self.assertTrue(all(not is_money(r["unit"]) for r in plain))
        self.assertTrue([r for r in bench["comparisons"] if "owner_minor" in r and is_money(r["unit"])])
        self.assertTrue([h for h in RESULTS["portfolio"]["data"]["holdings"] if "close_price" in h])
        for stat_id, names in EXCEPTIONS.items():
            for name, (reason, _) in names.items():
                self.assertGreater(len(reason), 40, (stat_id, name))

    def test_a_dollar_planted_in_a_result_fails_the_guard(self):
        planted = {
            "savings-runway": lambda d: d["data"]["summary"].update(cash=1234.56),
            "monthly-cashflow": lambda d: d["data"]["months"][0].update(spend=12.5),
            "forward-view": lambda d: d["params"].update(spend_median=2954.47),
            "cpf": lambda d: d["data"]["years"][0].update(oa=251.4),
            "portfolio": lambda d: d["data"]["summary"].update(value_sgd_minor=1524050.0),
            # an SGD row of benchmarks-sg is money: its exception does not cover it
            "benchmarks-sg": lambda d: next(r for r in d["data"]["comparisons"] if is_money(r["unit"])).update(
                owner=812.3),
            # a whole dollar is still dollars under a money name
            "travel": lambda d: d["data"]["summary"].update(spend=7),
            # a container or text where cents belong
            "one-offs": lambda d: d["data"]["summary"].update(total_minor=[]),
            "eating-out": lambda d: d["data"]["summary"].update(spend_minor={}),
            "income-stability": lambda d: d["data"]["volatility"].update(mean_minor="1250"),
        }
        for stat_id, plant in planted.items():
            with self.subTest(stat=stat_id):
                bad = copy.deepcopy(RESULTS[stat_id])
                plant(bad)
                # benchmarks-sg lists one comparison in its headline too, so one plant can show twice
                self.assertTrue(leaks(stat_id, bad))
                self.assertEqual(len({repr(v) for _, v in leaks(stat_id, bad)}), 1, leaks(stat_id, bad))

    def test_turned_back_and_forth_a_result_is_itself(self):
        # the guard's rule, read the other way, finds every money field a stat wrote
        dollars = {k: before_g20(v) for k, v in RESULTS.items()}
        back = in_cents(dollars)
        for stat_id in sorted(MOVED):
            with self.subTest(stat=stat_id):
                self.assertEqual({"data": back[stat_id].get("data"), "params": back[stat_id].get("params")},
                                 {"data": RESULTS[stat_id].get("data"), "params": RESULTS[stat_id].get("params")})


class StatCents(unittest.TestCase):
    def test_cents_under_minor_and_never_the_dollars(self):
        self.assertEqual(stat_cents({"cash_minor": 123456}, "cash"), 123456)
        self.assertEqual(stat_cents({"cash_minor": -5}, "cash"), -5)
        # G20 line 6: the bare name is a result from before G20, and its dollars are not read
        self.assertIsNone(stat_cents({"cash": 1234.56}, "cash"))
        self.assertIsNone(stat_cents({"cash": 7}, "cash"))
        self.assertEqual(stat_cents({"cash": 1.0, "cash_minor": 101}, "cash"), 101)
        self.assertFalse(hasattr(money, "stat_dollars"))

    def test_absent_or_null_is_none(self):
        for obj in ({}, {"cash": None}, {"cash_minor": None}, {"cash_minor": None, "cash": 5.0}, None, [], "x"):
            with self.subTest(obj=obj):
                self.assertIsNone(stat_cents(obj, "cash"))

    def test_cents_that_are_not_whole_break_the_contract(self):
        for bad in (12.5, 12.0, "1250", True):
            with self.subTest(bad=bad), self.assertRaises(TypeError):
                stat_cents({"cash_minor": bad}, "cash")


class InCents(unittest.TestCase):
    """money.in_cents: which results may be read. The chart hint says, never a value's size."""

    def test_a_result_in_cents(self):
        self.assertTrue(money.in_cents({"chart": {"unit": "sgd_minor"}, "data": {"months": [{"cash_minor": 5}]}}))
        # a stat with nothing to show yet is still in cents
        self.assertTrue(money.in_cents({"chart": {"unit": "sgd_minor"}, "data": {}}))

    def test_a_result_in_dollars(self):
        self.assertFalse(money.in_cents({"chart": {"unit": "sgd"}, "data": {"months": [{"cash": 5.0}]}}))
        self.assertFalse(money.in_cents({"chart": {}, "data": {"months": [{"cash": 5.0}]}}))
        self.assertFalse(money.in_cents({"data": {"months": [{"cash": 5.0}]}}))
        # forward-view between G17 and G20: its months had `_minor` twins, its chart was in dollars
        self.assertFalse(money.in_cents({"chart": {"unit": "sgd", "series": ["cash", "net"]},
                                         "data": {"months": [{"cash": 5.0, "cash_minor": 500}]}}))
        for bad in (None, [], "x"):
            self.assertFalse(money.in_cents(bad))

    def test_a_stat_whose_chart_is_not_money(self):
        # data-quality counts rows; its money is in cents once it has moved
        self.assertTrue(money.in_cents({"chart": {"unit": "count"}, "data": {"counts": {"uncategorised_out_minor": 0}}}))
        self.assertFalse(money.in_cents({"chart": {"unit": "count"}, "data": {"counts": {"uncategorised_out": 0.0}}}))
        self.assertTrue(money.in_cents({"chart": {"unit": "count"}, "params": {"floor_minor": 1000}}))

    def test_every_result_before_g20_is_in_dollars(self):
        for stat_id in sorted(MOVED):
            with self.subTest(stat=stat_id):
                self.assertFalse(money.in_cents(before_g20(RESULTS[stat_id])))

def reference(total, n):
    """`total` cents over `n`, half up away from zero, in Decimal: the rule to_cents uses."""
    return int((Decimal(total) / Decimal(n)).quantize(Decimal(1), rounding=ROUND_HALF_UP))


class CentsArithmetic(unittest.TestCase):
    """server/analytics/stats/common.py: sum, mean and median of integer cents."""

    def setUp(self):
        self.rng = random.Random(20)

    def lists(self, n=400):
        for _ in range(n):
            size = self.rng.randint(1, 25)
            top = self.rng.choice((9, 999, 10 ** 9))
            yield [self.rng.randint(-top, top) for _ in range(size)]

    def test_against_a_decimal_reference(self):
        for xs in self.lists():
            with self.subTest(xs=xs):
                self.assertEqual(c.sum_cents(xs), sum(xs))
                self.assertEqual(c.mean_cents(xs), reference(sum(xs), len(xs)))
                s = sorted(xs)
                mid = len(s) // 2
                want = s[mid] if len(s) % 2 else reference(s[mid - 1] + s[mid], 2)
                self.assertEqual(c.median_cents(xs), want)

    def test_the_same_rounding_as_to_cents(self):
        for xs in self.lists(200):
            with self.subTest(xs=xs):
                exact = Decimal(sum(xs)) / Decimal(len(xs)) / 100
                self.assertEqual(c.mean_cents(xs), to_cents(exact))

    def test_half_a_cent_rounds_away_from_zero(self):
        self.assertEqual(c.median_cents([1, 2]), 2)
        self.assertEqual(c.median_cents([-1, -2]), -2)
        self.assertEqual(c.median_cents([10, 3, 1, 4]), 4)          # 3.5
        self.assertEqual(c.mean_cents([1, 2]), 2)
        self.assertEqual(c.mean_cents([-1, -2]), -2)
        self.assertEqual(c.mean_cents([0, 0, 1]), 0)                # a third of a cent
        self.assertEqual(c.mean_cents([0, 1, 1]), 1)                # two thirds
        self.assertEqual(c.div_cents(-5, 2), -3)
        self.assertEqual(c.div_cents(5, 2), 3)
        self.assertEqual(c.div_cents(7, 4), 2)                      # 1.75

    def test_answers_are_ints(self):
        for xs in self.lists(50):
            for f in (c.sum_cents, c.mean_cents, c.median_cents):
                self.assertIs(type(f(xs)), int)

    def test_empty_is_zero(self):
        self.assertEqual((c.sum_cents([]), c.mean_cents([]), c.median_cents([]), c.div_cents(100, 0)), (0, 0, 0, 0))

    def test_a_float_is_refused(self):
        for f in (c.sum_cents, c.mean_cents, c.median_cents):
            for bad in ([1, 2.5], [True], [None]):
                with self.subTest(f=f.__name__, bad=bad), self.assertRaises(TypeError):
                    f(bad)
        with self.assertRaises(TypeError):
            c.div_cents(1.5, 2)
        with self.assertRaises(TypeError):
            c.div_cents(100, -1)

    def test_a_generator_is_read_once(self):
        self.assertEqual(c.mean_cents(x for x in (100, 201)), 151)




class EveryReaderReadsTheCents(unittest.TestCase):
    """Home, Net worth, the projection, the hub page and the export read the stats'
    cents as they are: each figure is the stat's own `_minor` field."""

    def test_home(self):
        conn = record()
        self.addCleanup(conn.close)
        o = home.overview(conn, RESULTS, today=TODAY)
        h, runway = o["headline"], RESULTS["savings-runway"]["data"]["summary"]
        self.assertEqual(h["cash"], runway["cash_minor"])
        self.assertEqual(h["net_worth_incl_cpf"], RESULTS["cpf"]["data"]["summary"]["net_worth_total_minor"])
        self.assertIsNotNone(h["cash_change"])
        self.assertEqual({x["label"]: x["value"] for x in o["holdings"]},
                         {"Cash": runway["cash_minor"],
                          "Investments": RESULTS["portfolio"]["data"]["summary"]["value_sgd_minor"],
                          "CPF": RESULTS["cpf"]["data"]["summary"]["total_minor"]})
        eye = o["needs_your_eye"]
        self.assertEqual(eye["cash_trend_6m"], runway["trend_6m_minor"])
        june = [x for x in RESULTS["one-offs"]["data"]["one_offs"] if x["month"] == "2025-06"]
        self.assertEqual(eye["one_off"]["amount"], max(x["amount_minor"] for x in june))

    def test_net_worth(self):
        nw = net_worth.net_worth(RESULTS)
        self.assertEqual(nw["parts"], ["cash", "investments", "cpf"])
        last = nw["months"][-1]
        self.assertEqual(last["total"], last["cash"] + last["investments"] + last["cpf"])
        self.assertTrue(all(type(m[p]) is int for m in nw["months"] for p in nw["parts"]))

    def test_the_net_worth_projection(self):
        plan = {"dca": 500.0, "income": [{"from": "2025-07-01", "to": "2026-06-30", "gross": 6000.0, "cpf": True}]}
        for birth in (None, "1996-03-15"):
            with self.subTest(birth=birth):
                p = growth.project(RESULTS, plan, birth, {}, today=TODAY)
                self.assertTrue(p["cash"] and p["tiger"] and p["cpf"] and p["total"])
        runway = RESULTS["savings-runway"]["data"]["months"]
        self.assertEqual(growth.starting_cash(RESULTS)[1],
                         [m for m in runway if m.get("complete", True)][-1]["cash_minor"])

    def test_the_hub_page_rows_are_the_stats_cents(self):
        rows = {r["Name"]: r for r in metrics.metrics_from(RESULTS, {"budgets": []}, TODAY)}
        s = RESULTS["savings-runway"]["data"]["summary"]
        self.assertEqual(rows["Cash, all accounts"]["Value_minor"], s["cash_minor"])
        self.assertEqual(rows["CPF, all three accounts"]["Value_minor"],
                         RESULTS["cpf"]["data"]["summary"]["total_minor"])
        self.assertEqual(rows["Investments, market value"]["Value_minor"],
                         RESULTS["portfolio"]["data"]["summary"]["value_sgd_minor"])
        self.assertEqual(rows["Home rate, six-month median"]["Value_minor"],
                         RESULTS["forward-view"]["params"]["spend_ex_all_trips_minor"])
        self.assertEqual(rows["Runway, months"]["Value"], s["runway_6m"])
        months = publish.months_from(RESULTS)
        flow = RESULTS["monthly-cashflow"]["data"]["months"]
        self.assertEqual([m["Income_minor"] for m in months], [m["income_minor"] for m in flow])
        self.assertEqual([m["Net_minor"] for m in months], [m["net_after_investing_minor"] for m in flow])

    def test_the_hub_page_writes_the_dollars_of_the_cents_in_one_place(self):
        # into the fake Notion only: each Number field holds from_cents of the stat's cents
        cfg = copy.copy(CFG)
        cfg.data_sources = {**CFG.data_sources, "metrics": "ds-metrics", "months": "ds-months"}
        client = FakeNotion()
        first = publish.publish(cfg, client, RESULTS, {"budgets": []}, echo=None, today=TODAY)
        self.assertTrue(first["metrics"]["created"] and first["months"]["created"])
        from finance import notion
        by_name = {notion.plain(p, "Name"): p for p in client.query_all("ds-metrics")}
        for row in metrics.metrics_from(RESULTS, {"budgets": []}, TODAY):
            want = float(from_cents(row["Value_minor"])) if "Value_minor" in row else row["Value"]
            self.assertEqual(notion.plain(by_name[row["Name"]], "Value"), want, row["Name"])
        writes = client.writes
        again = publish.publish(cfg, client, RESULTS, {"budgets": []}, echo=None, today=TODAY)
        self.assertEqual((again["metrics"]["updated"], again["months"]["updated"]), (0, 0))
        self.assertEqual(client.writes, writes)

    def test_the_export_puts_exact_dollars_beside_every_cents_column(self):
        for stat_id, body in RESULTS.items():
            with self.subTest(stat=stat_id):
                rows = [r for r in export.stat_rows(body) if isinstance(r, dict)]
                header, cells = export.stat_table(rows)
                cells = [dict(zip(header, r)) for r in cells]
                self.assertEqual(len(cells), len(rows))
                have = {k for r in rows for k in r}
                for src, out in zip(rows, cells):
                    for k, v in src.items():
                        base = k[:-len(money.MINOR)] if k.endswith(money.MINOR) else ""
                        if base and base not in have:
                            self.assertEqual(out[base], "" if v is None else str(from_cents(v)), k)

    def test_an_export_puts_the_dollars_beside_the_cents(self):
        rows = [{"month": "2026-01", "cash_minor": 1250, "net_minor": None},
                {"month": "2026-02", "cash_minor": -5, "net_minor": 300}]
        header, cells = export.stat_table(rows)
        self.assertEqual(header, ["month", "cash_minor", "cash", "net_minor", "net"])
        self.assertEqual(list(cells), [["2026-01", 1250, "12.50", None, ""], ["2026-02", -5, "-0.05", 300, "3.00"]])


# the stats an old cache holds in dollars in the tests below
STALE = ("savings-runway", "monthly-cashflow", "portfolio", "cpf", "benchmarks-sg", "forward-view")


class AResultFromBeforeG20(unittest.TestCase):
    """A cache that still holds results in dollars: each shows as no result until the
    stats run again, is never read as cents, and no page breaks over it."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="kopifund-stale-cache-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        (self.root / "results").mkdir()
        names = {}
        for stat_id, body in RESULTS.items():
            saved = before_g20(body) if stat_id in STALE else body
            names[stat_id] = f"{stat_id}-2025-06-20.json"
            (self.root / "results" / names[stat_id]).write_text(json.dumps(saved), encoding="utf-8")
        (self.root / "latest.json").write_text(json.dumps({"results": names}), encoding="utf-8")
        self.cache = results_cache.Cache(self.root)
        results_cache._RESULTS_MEMO.clear()
        patch = mock.patch.object(results_cache, "_cache", lambda: self.cache)
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(results_cache._RESULTS_MEMO.clear)

    def test_the_cache_leaves_them_out(self):
        got, _ = self.cache.results_all()
        self.assertEqual(set(got), MOVED - set(STALE))
        self.assertEqual(set(results_cache.results()), MOVED - set(STALE))
        from analytics import run
        stale = []
        self.assertEqual(set(run.cached_results(self.root, stale)), MOVED - set(STALE))
        self.assertEqual(sorted(stale), sorted(STALE))      # analyse names them under "hub page"

    def test_a_stat_card_answers_no_result_yet(self):
        import serve
        for stat_id in STALE:
            with self.subTest(stat=stat_id), self.assertRaises(NotFound):
                serve.answer(f"/api/results/{stat_id}", {})
        self.assertEqual(serve.answer("/api/results/travel", {})["id"], "travel")

    def test_no_page_breaks_and_none_reads_a_dollar(self):
        results = results_cache.results()
        conn = record()
        self.addCleanup(conn.close)
        o = home.overview(conn, results, today=TODAY)
        self.assertEqual((o["headline"]["cash"], o["headline"]["net_worth_incl_cpf"], o["holdings"]),
                         (None, None, []))
        self.assertEqual(net_worth.net_worth(results), {"parts": [], "months": []})
        self.assertTrue(growth.project(results, {"dca": 500.0}, None, {}, today=TODAY))
        rows = [r["Name"] for r in metrics.metrics_from(results, {"budgets": []}, TODAY)]
        self.assertFalse({"Cash, all accounts", "CPF, all three accounts", "Investments, market value",
                          "Spending, last full month", "Cash by 2027-03, on the plan"} & set(rows))
        self.assertEqual(publish.months_from(results), [])
        for stat_id, body in results.items():
            export.stat_table([r for r in export.stat_rows(body) if isinstance(r, dict)])

    def test_the_next_run_brings_them_back(self):
        for stat_id in STALE:
            (self.root / "results" / f"{stat_id}-2025-06-20.json").write_text(
                json.dumps(RESULTS[stat_id]), encoding="utf-8")
        got, _ = self.cache.results_all()
        self.assertEqual(set(got), MOVED)


class SnapshotRowsHoldCentsOnly(unittest.TestCase):
    def test_the_record_s_own_cents_and_no_dollars(self):
        conn = record()
        self.addCleanup(conn.close)
        rows = db.snapshot(conn)["transactions"]
        self.assertTrue(rows)
        want = {r["txn_key"]: (r["amount_minor"], r["amount_sgd_minor"])
                for r in conn.execute("SELECT txn_key, amount_minor, amount_sgd_minor FROM v_txn")}
        for t in rows:
            self.assertEqual((t["amount_minor"], t["amount_sgd_minor"]), want[t["key"]])
            self.assertIs(type(t["amount_sgd_minor"]), int)
            self.assertFalse({"amount", "amount_sgd"} & set(t))

    def test_a_period_block_carries_its_balances_in_cents(self):
        conn = record()
        self.addCleanup(conn.close)
        blocks = db.snapshot(conn)["periods"]
        want = {r["name"]: (r["closing_minor"], r["closing_sgd_minor"])
                for r in conn.execute("SELECT name, closing_minor, closing_sgd_minor FROM v_period")}
        self.assertTrue([b for b in blocks if b["closing_sgd_minor"] is not None])
        for b in blocks:
            self.assertEqual((b["closing_minor"], b["closing_sgd_minor"]), want[b["name"]])
            self.assertFalse({"opening", "closing", "total_in", "total_out", "closing_sgd"} & set(b))

    def test_a_snapshot_saved_in_dollars_is_built_again(self):
        from finance import snapshot as snap
        conn = record()
        self.addCleanup(conn.close)
        s = db.snapshot(conn)
        self.assertTrue(s["budgets"])
        self.assertTrue(snap.in_cents(s))
        for part, minor, dollars in (("transactions", "amount_sgd_minor", "amount_sgd"),
                                     ("periods", "closing_minor", "closing"),
                                     ("budgets", "planned_minor", "planned")):
            with self.subTest(part=part):
                old = copy.deepcopy(s)
                for x in old[part]:
                    x[dollars] = (x.pop(minor) or 0) / 100
                self.assertFalse(snap.in_cents(old))
        # an old snapshot with nothing but its budgets in dollars is not reused either
        self.assertFalse(snap.in_cents({"transactions": [], "periods": [],
                                        "budgets": [{"category": "Food", "month": "2026-09", "planned": 600.0}]}))


if __name__ == "__main__":
    unittest.main()
