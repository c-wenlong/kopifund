"""Every stat, against a fixture built to break them.

The fixture (server/tests/analytics_fixture.py) carries a recurring series that stops,
a refund inside a spend category, a synthetic row, foreign-currency rows with
and without an FX rate, a trip, an uncategorisable row and internal legs. Each
test below names the thing it is holding the stat to, so a failure says what
broke rather than which number moved.
"""
import isolate  # noqa: F401  first: no test writes the real record or cache
import contextlib
import copy
import json
import unittest
from pathlib import Path

from analytics import stats
from analytics.stats import common as c
from finance import settings as finance_settings
from finance.money import label, to_cents
from analytics_fixture import (BENCHMARKS, MONTHS, NO_RATE_MONTH, NO_SALARY,
                               PLAN, TRIP, settings, snapshot)

SNAP = snapshot()

# The forward view reads the owner's [plan] from server/finance/config.toml; the
# suite must not depend on it, so point it at the fixture config (no [plan]).
from analytics.stats import forward_view as _fv  # noqa: E402
_fv.PLAN_CONFIG = Path(__file__).resolve().parent / "fixtures" / "config.toml"

# The [analytics] settings come from the fixture too, never from the data
# folder of whoever runs the suite (G10).
_SETTINGS = contextlib.ExitStack()


def setUpModule():
    _SETTINGS.enter_context(settings())


def tearDownModule():
    _SETTINGS.close()


def stat(stat_id, benchmarks=None):
    return stats.by_id()[stat_id].run(SNAP, benchmarks)


def planned(snap=SNAP, **over):
    """The forward view on the fixture's invented plan."""
    return _fv.run(snap, None, {**PLAN, **over})


class TheResultEnvelope(unittest.TestCase):
    """Every module returns the same shape, and the renderer reads that shape."""

    def test_every_stat_in_the_spec_is_there(self):
        ids = [m.ID for m in stats.modules()]
        self.assertEqual(sorted(ids), sorted([
            "monthly-cashflow", "category-breakdown", "needs-wants-savings",
            "recurring-detector", "eating-out", "merchant-concentration",
            "one-offs", "income-stability", "savings-runway", "portfolio",
            "cpf", "travel", "benchmarks-sg", "forward-view", "data-quality"]))
        self.assertEqual(len(ids), len(set(ids)))

    def test_every_result_carries_the_documented_keys(self):
        for module in stats.modules():
            with self.subTest(module.ID):
                res = module.run(SNAP, None)
                for key in ("id", "title", "generated_at", "snapshot", "chart",
                            "sources", "params", "data", "findings", "caveats"):
                    self.assertIn(key, res)
                self.assertEqual(res["id"], module.ID)
                self.assertEqual(res["snapshot"], SNAP["meta"]["file"])
                self.assertTrue(res["findings"], "a stat with no finding says nothing")
                for line in res["findings"]:
                    self.assertNotIn("—", line, "no em dashes in a finding")
                for src in res["sources"]:
                    self.assertEqual(sorted(src), ["database", "filter", "rows_used"])

    def test_every_result_is_json_and_the_chart_points_at_real_rows(self):
        for module in stats.modules():
            with self.subTest(module.ID):
                res = module.run(SNAP, None)
                json.dumps(res, allow_nan=False)          # raises if it is not
                chart = res["chart"]
                self.assertIn(chart["type"],
                              ("bar", "line", "stacked", "table", "scatter",
                               "versus"))
                rows = res["data"]
                for part in chart["source"].split("."):
                    rows = rows[part]
                self.assertIsInstance(rows, list)
                if rows:
                    self.assertIn(chart["x"], rows[0])
                    for name in chart["series"]:
                        self.assertIn(name, rows[0])


class TheConventions(unittest.TestCase):
    """The filters every stat shares, tested once."""

    def test_internal_legs_and_transfers_are_not_spend(self):
        spend = c.spend_rows(SNAP)
        self.assertTrue(all(r["flow_class"] == "External" for r in spend))
        self.assertTrue(all(r["bucket"] == "Spend" for r in spend))
        self.assertNotIn("Person to person", {r["category"] for r in spend})

    def test_a_synthetic_row_is_never_spend(self):
        self.assertTrue(any(r["synthetic"] for r in SNAP["transactions"]))
        self.assertFalse(any(r["synthetic"] for r in c.spend_rows(SNAP)))

    def test_spend_is_signed_so_a_refund_subtracts(self):
        shopping = [r for r in c.spend_rows(SNAP) if r["category"] == "Shopping"]
        self.assertEqual(c.spend_minor(shopping), 34_500)    # 500 + 45 - 200
        self.assertNotEqual(c.spend_minor(shopping),
                            sum(abs(c.sgd_minor(r)) for r in shopping))

    def test_the_month_with_no_wallet_rate_is_still_covered(self):
        cov = c.coverage(SNAP)
        self.assertEqual([m for m in MONTHS if cov[m]["partial"]], [])

    def test_a_wallet_that_printed_one_month_leaves_the_others_covered(self):
        """Wallets are funded from the bank accounts, so a wallet with no
        statement in a month is a wallet that was not used, not a hole."""
        import copy
        snap = copy.deepcopy(SNAP)
        wallets = c.wallet_accounts(snap)
        self.assertTrue(wallets)
        keep = MONTHS[-1]
        snap["periods"] = [b for b in snap["periods"]
                           if b["account_no"] not in wallets or c.period_month(b) == keep]
        cov = c.coverage(snap)
        self.assertEqual([m for m in MONTHS if cov[m]["partial"]], [])

    def test_a_bank_account_that_skipped_a_month_makes_it_partial(self):
        import copy
        snap = copy.deepcopy(SNAP)
        banks = {a["account_no"] for a in snap["accounts"] if a.get("subclass") == "Cash"}
        gap = MONTHS[-2]
        snap["periods"] = [b for b in snap["periods"]
                           if not (b["account_no"] in banks and c.period_month(b) == gap)]
        cov = c.coverage(snap)
        self.assertTrue(cov[gap]["partial"])
        self.assertEqual([m for m in MONTHS if cov[m]["partial"]], [gap])


class MonthlyCashflow(unittest.TestCase):

    def setUp(self):
        self.res = stat("monthly-cashflow")

    def test_one_row_per_month_with_the_phases_split(self):
        self.assertEqual([m["month"] for m in self.res["data"]["months"]], MONTHS)
        names = [p["name"] for p in self.res["data"]["phases"]]
        self.assertEqual(names, ["Before the move", "After the move"])
        self.assertEqual(names, [p["name"] for p in c.phases()])

    def test_income_investing_and_net_add_up(self):
        january = self.res["data"]["months"][0]
        self.assertEqual(january["income_minor"], 300000)
        self.assertEqual(january["investing_minor"], 100000)
        self.assertEqual(january["net_minor"], january["income_minor"] - january["spend_minor"])
        self.assertEqual(january["net_after_investing_minor"], january["net_minor"] - 100000)

    def test_the_phase_averages_are_whole_cents_half_up(self):
        for p in self.res["data"]["phases"]:
            self.assertEqual(p["spend_per_month_minor"], c.div_cents(p["spend_minor"], p["months"]))
            self.assertEqual(p["net_after_investing_per_month_minor"],
                             c.div_cents(p["income_minor"] - p["spend_minor"] - p["investing_minor"],
                                         p["months"]))
            self.assertIs(type(p["income_per_month_minor"]), int)

    def test_the_refund_month_is_lighter_than_the_charge_month(self):
        months = {m["month"]: m for m in self.res["data"]["months"]}
        self.assertLess(months["2025-08"]["spend_minor"], months["2025-07"]["spend_minor"])


class CategoryBreakdown(unittest.TestCase):

    def setUp(self):
        self.res = stat("category-breakdown")

    def test_the_refund_nets_off_inside_its_category(self):
        by_name = {x["category"]: x for x in self.res["data"]["categories"]}
        self.assertEqual(by_name["Shopping"]["total_minor"], 34500)

    def test_shares_sum_to_one(self):
        total = sum(x["share"] for x in self.res["data"]["categories"])
        self.assertAlmostEqual(total, 1.0, places=2)

    def test_a_stopped_category_shows_a_fall(self):
        by_name = {x["category"]: x for x in self.res["data"]["categories"]}
        self.assertEqual(by_name["Fitness"]["last3_per_month_minor"], 0)
        self.assertLess(by_name["Fitness"]["change_minor"], 0)


class NeedsWantsSavings(unittest.TestCase):

    def setUp(self):
        self.res = stat("needs-wants-savings")

    def test_the_three_columns_only_hold_their_own_need_class(self):
        january = self.res["data"]["months"][0]
        self.assertEqual(january["need_minor"], 88000)      # rent 800 + bus 20 + 60
        self.assertEqual(january["savings_minor"], 100000)  # the investing transfer
        self.assertGreater(january["want_minor"], 0)

    def test_the_summary_compares_against_the_rule(self):
        summary = self.res["data"]["summary"]
        self.assertEqual(summary["targets"], {"need": 0.5, "want": 0.3,
                                              "savings": 0.2})
        self.assertAlmostEqual(summary["gaps"]["need"],
                               round(summary["need_share"] - 0.5, 4))


class RecurringDetector(unittest.TestCase):

    def setUp(self):
        self.res = stat("recurring-detector")
        self.series = {s["payee"]: s for s in self.res["data"]["series"]}

    def test_a_monthly_series_is_found_with_its_cadence(self):
        self.assertIn("Kind Fund", self.series)
        self.assertEqual(self.series["Kind Fund"]["cadence"], "monthly")
        self.assertEqual(self.series["Kind Fund"]["avg_amount_minor"], 1800)
        self.assertEqual(self.series["Kind Fund"]["occurrences"], 12)
        self.assertFalse(self.series["Kind Fund"]["stopped"])

    def test_the_series_that_stops_is_reported_stopped(self):
        gym = self.series["Gym Co"]
        self.assertTrue(gym["stopped"])
        self.assertEqual(gym["last_seen"], "2025-05-05")
        self.assertIn("Gym Co", [s["payee"] for s in self.res["data"]["stopped"]])

    def test_the_investing_transfer_is_committed_but_not_fixed_spend(self):
        totals = self.res["data"]["totals"]
        self.assertEqual(totals["monthly_committed_minor"] - totals["monthly_fixed_cost_minor"], 100000)

    def test_a_payee_ticked_recurring_with_no_rows_is_named(self):
        self.assertIn("Ghost Sub",
                      self.res["data"]["flag_mismatches"]["flagged_not_detected"])

    def test_the_flag_findings_name_no_source(self):
        """The flag comes from the rules' recurring column, or from Notion's
        checkbox when the snapshot is built from Notion, so the findings say
        "marked recurring" and name neither."""
        flags = [f for f in self.res["findings"] if "marked recurring" in f]
        self.assertTrue(any("not marked recurring" in f for f in flags))
        for f in self.res["findings"]:
            with self.subTest(f):
                self.assertNotIn("Notion", f)

    def test_fast_and_giro_rows_are_listed_as_possible_auto_bills(self):
        names = {a["payee"] for a in self.res["data"]["possible_auto_bills"]}
        self.assertIn("Telco", names)
        self.assertIn("Tiger", names)


class EatingOut(unittest.TestCase):

    def setUp(self):
        self.res = stat("eating-out")

    def test_meals_are_counted_by_charge_not_by_row(self):
        summary = self.res["data"]["summary"]
        self.assertEqual(summary["count"], 12 * 4 + 2)      # plus the two abroad
        self.assertGreater(summary["avg_ticket_minor"], 0)

    def test_both_weekday_buckets_are_present(self):
        buckets = {b["bucket"]: b for b in self.res["data"]["weekday"]}
        self.assertEqual(sorted(buckets), ["weekday", "weekend"])
        self.assertEqual(buckets["weekday"]["count"] + buckets["weekend"]["count"],
                         self.res["data"]["summary"]["count"])

    def test_groceries_are_the_comparison(self):
        self.assertEqual(self.res["data"]["summary"]["groceries_minor"], 72000)
        self.assertIsNotNone(self.res["data"]["summary"]["ratio"])

    def test_at_most_fifteen_places(self):
        self.assertLessEqual(len(self.res["data"]["top_places"]), 15)


class MerchantConcentration(unittest.TestCase):

    def setUp(self):
        self.res = stat("merchant-concentration")

    def test_the_pareto_cut_is_inside_the_payee_list(self):
        pareto = self.res["data"]["pareto"]
        self.assertLessEqual(pareto["p50"], pareto["p80"])
        self.assertLessEqual(pareto["p80"], pareto["payees"])

    def test_cumulative_share_reaches_one(self):
        rows = self.res["data"]["top_by_spend"]
        self.assertAlmostEqual(rows[-1]["cumulative_share"], 1.0, places=1)

    def test_transfer_payees_are_absent(self):
        self.assertNotIn("Lender", {p["payee"] for p in
                                    self.res["data"]["top_by_spend"]})


class OneOffs(unittest.TestCase):

    def setUp(self):
        self.res = stat("one-offs")

    def test_the_outsized_fare_is_found_and_the_ordinary_ones_are_not(self):
        found = {x["payee"]: x for x in self.res["data"]["one_offs"]}
        self.assertIn("Airline", found)
        self.assertEqual(found["Airline"]["amount_minor"], 120000)
        self.assertEqual(len([x for x in self.res["data"]["one_offs"]
                              if x["category"] == "Travel"]), 1)

    def test_a_refund_is_listed_separately_and_never_as_a_one_off(self):
        refunds = self.res["data"]["refunds"]
        self.assertEqual([r["amount_minor"] for r in refunds], [20000])
        self.assertTrue(all(x["amount_minor"] > 0 for x in self.res["data"]["one_offs"]))


class IncomeStability(unittest.TestCase):

    def setUp(self):
        self.res = stat("income-stability")

    def test_the_months_with_no_salary_are_named(self):
        vol = self.res["data"]["volatility"]
        self.assertEqual(vol["months_without_salary"], len(NO_SALARY))
        self.assertEqual(sorted(vol["no_salary_months"]), sorted(NO_SALARY))

    def test_volatility_is_a_coefficient_of_variation(self):
        vol = self.res["data"]["volatility"]
        self.assertAlmostEqual(vol["cv"], round(vol["stdev_minor"] / vol["mean_minor"], 2))
        self.assertIs(type(vol["stdev_minor"]), int)

    def test_the_employer_shows_up(self):
        self.assertEqual(self.res["data"]["employers"][0]["payee"], "Acme")


class SavingsRunway(unittest.TestCase):

    def setUp(self):
        self.res = stat("savings-runway")

    def test_cash_is_the_sum_of_the_last_closing_balance_per_account(self):
        summary = self.res["data"]["summary"]
        self.assertEqual(summary["cash_minor"], 210000 + 13000)

    def test_an_account_with_no_block_this_month_keeps_its_balance(self):
        june = next(m for m in self.res["data"]["months"]
                    if m["month"] == NO_RATE_MONTH)
        # named by its label: the record's name with the number taken out
        self.assertIn("Travel wallet", june["carried"])

    def test_runway_is_cash_over_the_spend_rate(self):
        summary = self.res["data"]["summary"]
        self.assertAlmostEqual(summary["runway_6m"],
                               round(summary["cash_minor"] / summary["spend_avg_6m_minor"], 1))


class Travel(unittest.TestCase):

    def setUp(self):
        self.res = stat("travel")

    def test_the_trip_is_priced_by_its_own_rows(self):
        trip = next(t for t in self.res["data"]["trips"] if t["name"] == TRIP)
        self.assertEqual(trip["days"], 3)
        self.assertEqual(trip["spend_minor"], 130000)       # 1200 fare + 100 meal
        self.assertEqual(trip["spend_per_day_minor"], 43333)

    def test_rows_off_the_trip_are_not_counted(self):
        self.assertEqual(self.res["data"]["summary"]["rows"], 2)


class BenchmarksSG(unittest.TestCase):

    def test_without_the_file_it_still_reports_and_says_why(self):
        res = stat("benchmarks-sg", None)
        self.assertEqual(res["data"]["comparisons"], [])
        self.assertNotIn("benchmarks", res)
        self.assertTrue(any("benchmarks.json" in x for x in res["caveats"]))
        self.assertTrue(res["data"]["metrics"])

    def test_with_the_file_each_comparison_carries_its_source(self):
        res = stat("benchmarks-sg", BENCHMARKS)
        self.assertTrue(res["data"]["comparisons"])
        for row in res["data"]["comparisons"]:
            self.assertTrue(row["url"])
            self.assertTrue(row["as_of"])
            self.assertIn(row["direction"], ("above", "below", "about the same as"))
        self.assertEqual(len(res["benchmarks"]), len(BENCHMARKS["items"]))

    def test_a_reference_nothing_matches_is_listed_unmatched(self):
        res = stat("benchmarks-sg", BENCHMARKS)
        self.assertIn("unmatchable-reference",
                      {x["id"] for x in res["data"]["unmatched"]})

    def test_a_reference_is_matched_only_when_its_unit_is_comparable(self):
        res = stat("benchmarks-sg", BENCHMARKS)
        matched = {x["metric"] for x in res["data"]["comparisons"]}
        self.assertEqual(matched, {"monthly_spend", "monthly_salary",
                                   "cash_months"})

    def test_an_amount_with_no_period_in_its_unit_is_never_matched(self):
        """The renderer reads a period-less amount as monthly, so a yearly
        figure written as plain SGD would land on the monthly axis. The unit
        gate has to keep it out rather than guess."""
        from analytics.stats import benchmarks_sg
        for unit in ("SGD", "SGD per year", "SGD per year, including GST",
                     "SGD per person"):
            with self.subTest(unit):
                self.assertIsNone(benchmarks_sg.match(
                    {"id": "x-monthly-expenditure-spend", "unit": unit,
                     "value": 1.0, "population": "anybody"}))
        self.assertEqual(benchmarks_sg.match(
            {"id": "x-monthly-expenditure", "unit": "SGD per month",
             "value": 1.0, "population": "anybody"}), "monthly_spend")

    def test_the_headline_holds_one_reference_per_metric(self):
        res = stat("benchmarks-sg", BENCHMARKS)
        metrics = [x["metric"] for x in res["data"]["headline"]]
        self.assertEqual(len(metrics), len(set(metrics)))
        for row in res["data"]["headline_money"]:
            self.assertTrue(row["unit"].lower().startswith("sgd"))

    # G20: the reference file's dollars turn to cents once, where it is read

    def test_the_file_s_sgd_figures_are_read_as_cents(self):
        from analytics.stats import benchmarks_sg as b
        cents = b.in_cents(BENCHMARKS)
        by_id = {x["id"]: x for x in cents["items"]}
        self.assertEqual(by_id["hes-2023-monthly-expenditure"]["value_minor"], 432_200)
        self.assertNotIn("value", by_id["hes-2023-monthly-expenditure"])
        self.assertEqual(by_id["moneysense-emergency-fund-months"]["value"], 6)     # months stay
        self.assertEqual(b.in_cents(cents), cents)                                   # once only
        self.assertEqual(BENCHMARKS["items"][0]["value"], 4322)                      # the file is left as it is
        self.assertIsNone(b.in_cents(None))

    def test_load_benchmarks_hands_the_stats_cents(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "benchmarks.json"
            path.write_text(json.dumps(BENCHMARKS))
            loaded = stats.load_benchmarks(path)
        self.assertEqual(loaded["items"][0]["value_minor"], 432_200)

    def test_cents_or_dollars_in_the_same_matches_and_rejections_out(self):
        """Decision 16 is unchanged by the cents: the same matches, the same
        rejections, the same ratios, whether the stat is handed the file's
        dollars or the loader's cents."""
        from analytics.stats import benchmarks_sg as b
        a, z = stat("benchmarks-sg", BENCHMARKS), stat("benchmarks-sg", b.in_cents(BENCHMARKS))
        for key in ("comparisons", "headline", "rejected", "unmatched", "metrics"):
            self.assertEqual(a["data"][key], z["data"][key], key)
        self.assertEqual({x["metric"] for x in z["data"]["comparisons"]},
                         {"monthly_spend", "monthly_salary", "cash_months"})

    def test_money_is_cents_and_months_and_percents_keep_their_scale(self):
        res = stat("benchmarks-sg", BENCHMARKS)
        for m in res["data"]["metrics"]:
            with self.subTest(metric=m["key"]):
                if m["unit"].lower().startswith("sgd"):
                    self.assertIs(type(m["value_minor"]), int)
                    self.assertNotIn("value", m)
                else:
                    self.assertNotIn("value_minor", m)
        rows = {x["metric"]: x for x in res["data"]["comparisons"]}
        spend = rows["monthly_spend"]
        self.assertIs(type(spend["owner_minor"]), int)
        self.assertEqual(spend["benchmark_minor"], 432_200)
        self.assertNotIn("owner", spend)
        self.assertEqual(spend["ratio"], round(spend["owner_minor"] / 432_200, 2))
        cash = rows["cash_months"]
        self.assertEqual(cash["benchmark"], 6)
        self.assertNotIn("benchmark_minor", cash)

    def test_the_findings_print_amounts_as_the_app_does(self):
        res = stat("benchmarks-sg", BENCHMARKS)
        line = [f for f in res["findings"] if f.startswith("Monthly spending:")][0]
        self.assertIn("against S$4,322.00 (SGD per household per month)", line)
        self.assertTrue(stat("benchmarks-sg", None)["findings"][0].startswith(
            "No benchmark file, so these are the owner's figures alone: S$"))

    # G13: the card's flags. Every figure here is the fixture's invented one.

    HOUSING = {"id": "hes-2023-1person-housing-utilities", "value": 900,
               "unit": "SGD per household per month", "as_of": "2023",
               "population": "one-person resident households", "source": "s",
               "url": "https://example.invalid/"}

    def with_housing(self, snap=SNAP):
        given = {"items": BENCHMARKS["items"] + [self.HOUSING]}
        return stats.by_id()["benchmarks-sg"].run(snap, given)

    def test_a_figure_billed_every_month_is_not_thin(self):
        res = self.with_housing()
        housing = next(x for x in res["data"]["headline"]
                       if x["metric"] == "housing_spend")
        self.assertFalse(housing["thin"])
        self.assertIsNone(housing["thin_reason"])

    def test_a_spending_figure_on_fewer_rows_than_months_is_thin(self):
        """Rent billed in two months of the phase is not what housing costs."""
        snap = copy.deepcopy(SNAP)
        rent = [r for r in snap["transactions"] if r["category"] == "Rent"]
        keep = {id(r) for r in rent[-2:]}
        snap["transactions"] = [r for r in snap["transactions"]
                                if r["category"] != "Rent" or id(r) in keep]
        res = self.with_housing(snap)
        metrics = {m["key"]: m for m in res["data"]["metrics"]}
        self.assertTrue(metrics["housing_spend"]["thin"])
        self.assertIn("rests on 2 rows", metrics["housing_spend"]["thin_reason"])
        self.assertFalse(metrics["food_spend"]["thin"])
        housing = next(x for x in res["data"]["headline"]
                       if x["metric"] == "housing_spend")
        self.assertTrue(housing["thin"])
        self.assertEqual(housing["thin_reason"],
                         metrics["housing_spend"]["thin_reason"])
        self.assertFalse(housing["concern"])

    def test_only_balances_and_rows_averages_are_judged_for_thinness(self):
        from analytics.stats import benchmarks_sg as b
        for key in ("cash_months", "savings_rate", "monthly_income"):
            with self.subTest(key):
                self.assertIsNone(b.thin_reason(
                    {"key": key, "label": key, "rows": 0}, 12))
        self.assertIsNone(b.thin_reason(
            {"key": "food_spend", "label": "Food", "rows": 0}, 0))

    def test_cash_below_the_reference_is_the_only_concern(self):
        """The fixture's cash covers far fewer than six months of expenses, so
        cash_months is below the reference and flagged; spending lines never
        are, whichever side of the reference they land on."""
        res = self.with_housing()
        rows = {x["metric"]: x for x in res["data"]["headline"]}
        self.assertEqual(rows["cash_months"]["direction"], "below")
        self.assertTrue(rows["cash_months"]["concern"])
        for key, row in rows.items():
            if key != "cash_months":
                with self.subTest(key):
                    self.assertFalse(row["concern"])
        self.assertEqual({m["key"]: m["worse_when"] for m in res["data"]["metrics"]
                          if m["worse_when"]}, {"cash_months": "below"})

    def test_the_card_lists_the_headline_on_short_labels_in_order(self):
        res = self.with_housing()
        chart = res["chart"]
        self.assertEqual(chart["type"], "versus")
        self.assertEqual(chart["source"], "headline")
        self.assertEqual(chart["order"][:3],
                         ["cash_months", "savings_rate", "monthly_spend"])
        shorts = {x["metric"]: x["short"] for x in res["data"]["headline"]}
        self.assertEqual(shorts["cash_months"], "Months of cash")
        self.assertEqual(shorts["monthly_spend"], "All spending")
        self.assertEqual(shorts["housing_spend"], "Housing")


class ForwardView(unittest.TestCase):

    def setUp(self):
        self.res = planned()
        self.months = {m["month"]: m for m in self.res["data"]["months"]}

    def test_cpf_takes_a_fifth_of_gross_below_the_ceiling(self):
        from analytics.stats import forward_view
        self.assertEqual(forward_view.cpf_employee(300_000, 0.20, 800_000), 60_000)
        self.assertEqual(self.months["2026-06"]["take_home_minor"], 208_000)
        # G20: a rate times whole cents, half up once: 0.17 of S$12.50 is 212.5 cents
        self.assertEqual(forward_view.cpf_employer(1_250, 0.17, 800_000), 213)
        # the bonus attracts the rate on top of the capped Ordinary Wage, rounded with it
        self.assertEqual(forward_view.cpf_employee(900_000, 0.20, 800_000, additional=12_345), 162_469)

    def test_the_ceiling_caps_the_contribution(self):
        from analytics.stats import forward_view
        self.assertEqual(forward_view.cpf_employee(1_000_000, 0.20, 800_000), 160_000)
        # G20: the ceiling is server/finance/cpf_rules.py's cents
        self.assertEqual(forward_view.DEFAULTS["cpf_ow_ceiling_minor"], 800_000)

    def test_the_gap_between_two_pay_segments_earns_nothing_and_is_named(self):
        self.assertEqual(self.months["2026-08"]["gross_minor"], 260_000)
        self.assertEqual(self.months["2026-09"]["gross_minor"], 0)
        self.assertEqual(self.months["2026-10"]["gross_minor"], 520_000)
        self.assertEqual(self.res["params"]["zero_income_months"], ["2026-09"])
        self.assertTrue(any(f.startswith("The month with no income costs "
                                         f"{label(-self.months['2026-09']['net_minor'])}")
                            for f in self.res["findings"]))

    def test_the_end_month_and_the_investing_transfer_come_from_the_plan(self):
        self.assertEqual(self.res["data"]["months"][-1]["month"], "2027-03")
        self.assertEqual(self.res["title"], "Forward view to Mar 2027")
        self.assertTrue(any("and Mar 2027 ends at" in f for f in self.res["findings"]))
        self.assertTrue(any(f.startswith("Pausing the S$250 investing transfer")
                            for f in self.res["findings"]))
        self.assertEqual(self.months["2026-05"]["investing_minor"], 25_000)

    def test_dated_pay_segments_prorate_by_calendar_days(self):
        from analytics.stats import forward_view
        segs = [{"from": "2026-03-01", "to": "2026-04-13", "gross_minor": 200_000, "cpf": True},
                {"from": "2026-04-14", "to": "2026-06-30", "gross_minor": 250_000, "cpf": True},
                {"from": "2026-08-11", "to": "2026-12-31", "gross_minor": 600_000, "cpf": False}]
        self.assertEqual(forward_view.month_gross("2026-03", segs), (200_000, 200_000))
        self.assertEqual(forward_view.month_gross("2026-04", segs), (228_334, 228_334))
        self.assertEqual(forward_view.month_gross("2026-07", segs), (0, 0))
        self.assertEqual(forward_view.month_gross("2026-08", segs), (406_452, 0))
        self.assertEqual(forward_view.month_gross("2026-09", segs), (600_000, 0))

    def test_segments_drive_the_projection_when_given(self):
        from analytics.stats import forward_view
        segs = [{"from": "2026-03-01", "to": "2026-06-30", "gross_minor": 250_000, "cpf": True},
                {"from": "2026-08-11", "to": "2027-06-30", "gross_minor": 600_000, "cpf": True}]
        months = {m["month"]: m for m in
                  forward_view.run(SNAP, None, {"income_segments": segs})["data"]["months"]}
        self.assertEqual(months["2026-06"]["gross_minor"], 250_000)
        self.assertEqual(months["2026-06"]["take_home_minor"], 200_000)
        self.assertEqual(months["2026-07"]["gross_minor"], 0)
        self.assertEqual(months["2026-08"]["gross_minor"], 406_452)
        self.assertEqual(months["2026-09"]["take_home_minor"], 480_000)

    def test_planned_trips_cost_days_times_rate_plus_flights_in_their_month(self):
        from analytics.stats import forward_view
        trips = [{"name": "A", "from": "2026-12-26", "to": "2027-01-09", "per_day_minor": 6_000,
                  "flights_minor": 5_000, "flights_month": "2026-12"},
                 {"name": "B", "from": "2026-12-14", "to": "2026-12-23", "per_day_minor": None,
                  "flights_minor": 60_000, "flights_month": "2026-08"}]
        dec, parts = forward_view.month_trip_cost("2026-12", trips, 4_000)
        self.assertEqual(dec, 6 * 6_000 + 5_000 + 10 * 4_000)
        self.assertEqual(parts, [{"name": "A", "days": 6, "cost_minor": 41_000},
                                 {"name": "B", "days": 10, "cost_minor": 40_000}])
        self.assertEqual(forward_view.month_trip_cost("2027-01", trips, 4_000)[0], 9 * 6_000)
        self.assertEqual(forward_view.month_trip_cost("2026-08", trips, 4_000)[0], 60_000)
        self.assertEqual(forward_view.month_trip_cost("2026-10", trips, 4_000), (0, []))

    def test_one_off_payments_land_in_their_month_only(self):
        from analytics.stats import forward_view
        one_offs = [{"month": "2026-09", "amount_minor": 321_040, "label": "course fee"},
                    {"month": "2026-09", "amount_minor": 20_000, "label": "deposit"},
                    {"month": "2027-02", "amount_minor": 99_900, "label": "laptop"}]
        sep, parts = forward_view.month_one_offs("2026-09", one_offs)
        self.assertEqual(sep, 341_040)
        self.assertEqual(parts, [{"label": "course fee", "amount_minor": 321_040},
                                 {"label": "deposit", "amount_minor": 20_000}])
        self.assertEqual(forward_view.month_one_offs("2026-10", one_offs), (0, []))
        self.assertEqual(forward_view.month_one_offs("2027-02", None), (0, []))

    def test_a_one_off_in_the_plan_lowers_that_month_and_every_month_after(self):
        base = planned(spend_rate="home")
        res = planned(spend_rate="home", one_offs=[
            {"month": "2026-12", "amount_minor": 100_000, "label": "bill"}])
        before = {m["month"]: m for m in base["data"]["months"]}
        after = {m["month"]: m for m in res["data"]["months"]}
        self.assertEqual(after["2026-12"]["one_offs_minor"], 100_000)
        self.assertEqual(after["2026-12"]["one_off_parts"], [{"label": "bill", "amount_minor": 100_000}])
        self.assertEqual(after["2026-11"]["one_offs_minor"], 0)
        self.assertEqual(after["2026-11"]["cash_minor"], before["2026-11"]["cash_minor"])
        self.assertEqual(after["2026-12"]["cash_minor"], before["2026-12"]["cash_minor"] - 100_000)
        self.assertEqual(after["2027-03"]["cash_minor"], before["2027-03"]["cash_minor"] - 100_000)
        names = {s["name"]: s for s in res["data"]["scenarios"]}
        self.assertEqual(names["without the one-off payments"]["ending_cash_minor"],
                         names["as stated"]["ending_cash_minor"] + 100_000)
        self.assertTrue(any("bill S$1,000.00 in Dec 2026" in f for f in res["findings"]))

    def test_plan_params_reads_the_one_offs_table(self):
        import tempfile
        from analytics.stats import forward_view
        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as fh:
            fh.write('[plan]\nto = "2027-06"\n[[plan.one_offs]]\nmonth = "2026-09"\n'
                     'amount = 3210.40\nlabel = "course fee"\n')
        p = forward_view.plan_params(fh.name)
        # G20: the plan's dollars are cents from here on, turned once
        self.assertEqual(p["one_offs"], [{"month": "2026-09", "amount_minor": 321_040, "label": "course fee"}])
        self.assertNotIn("one_offs", forward_view.plan_params(_fv.PLAN_CONFIG))

    def test_the_base_rate_and_trips_come_from_the_plan(self):
        from analytics.stats import forward_view
        res = forward_view.run(SNAP, None, {"spend_rate": "home", "trips": [
            {"name": "X", "from": "2026-12-14", "to": "2026-12-23", "per_day_minor": 10_000,
             "flights_minor": 0, "flights_month": "2026-12"}]})
        p, months = res["params"], {m["month"]: m for m in res["data"]["months"]}
        self.assertEqual(p["spend_minor"], p["spend_ex_all_trips_minor"])
        self.assertEqual(months["2026-12"]["trips_minor"], 100_000)
        self.assertEqual(months["2026-11"]["trips_minor"], 0)
        names = {s["name"]: s for s in res["data"]["scenarios"]}
        self.assertGreater(names["without the planned trips"]["ending_cash_minor"],
                           names["as stated"]["ending_cash_minor"])

    def test_cash_walks_forward_from_the_runway_stat(self):
        start = self.res["data"]["starting_cash_minor"]
        first = self.res["data"]["months"][0]
        self.assertEqual(first["cash_minor"], start + first["net_minor"])
        self.assertEqual(start, stat("savings-runway")["data"]["summary"]["cash_minor"])
        # nothing compounds: each month's cash is the last month's plus its net
        months = self.res["data"]["months"]
        for prev, m in zip(months, months[1:]):
            self.assertEqual(m["cash_minor"], prev["cash_minor"] + m["net_minor"])

    def test_pausing_the_investing_transfer_ends_higher(self):
        by_name = {s["name"]: s for s in self.res["data"]["scenarios"]}
        self.assertGreater(by_name["investing paused"]["ending_cash_minor"],
                           by_name["as stated"]["ending_cash_minor"])

    def test_the_trip_free_rates_take_the_trip_rows_out(self):
        """When several of the window months were spent away, the median is a
        travelling rate. Both trip-free variants are here:
        one without every trip row, one that keeps a stay of more than sixty
        days because living somewhere is still living."""
        from analytics.stats import forward_view
        snap = copy.deepcopy(SNAP)
        snap["trips"].append({"name": "Long stay", "start": "2025-08-01",
                              "end": "2025-11-30", "countries": ["JPN"]})
        for r in snap["transactions"]:
            if r["category"] == "Rent" and r["month"] == "2025-09":
                r["trip"] = "Long stay"
            if r["category"] == "Rent" and r["month"] == "2025-10":
                r["trip"] = TRIP
        p = forward_view.run(snap, None)["params"]
        self.assertEqual(p["trip_months_in_window"], ["2025-09", "2025-10"])
        self.assertEqual(sorted(p["trips_in_window"]), ["Long stay", TRIP])
        self.assertEqual(p["spend_ex_trips_minor"], p["spend_ex_all_trips_minor"])
        self.assertLessEqual(p["spend_ex_short_trips_minor"], p["spend_median_minor"])
        # the home rate reads its own window: the long stay covers August to
        # November whole, so none of those months is a month at home
        self.assertFalse(set(p["spend_home_window"])
                         & {"2025-08", "2025-09", "2025-10", "2025-11"})
        self.assertEqual(forward_view.home_months(snap, p["spend_home_window"]),
                         p["spend_home_window"])

    def test_the_repayments_are_split_out_and_not_projected(self):
        """They are money paid back, not a monthly cost, so they are a
        parameter that defaults to zero rather than part of other_outflow."""
        from analytics.stats import forward_view
        self.assertEqual(forward_view.repayment_payee(), "Lender")
        res = forward_view.run(SNAP, None)
        p = res["params"]
        observed = p["other_outflow_observed"]
        self.assertGreater(observed["repayments_minor"], 0)
        self.assertEqual(p["repayments_minor"], 0)
        self.assertEqual(p["other_outflow_minor"], observed["other_minor"])
        self.assertEqual(observed["total_minor"], observed["repayments_minor"] + observed["other_minor"])
        self.assertEqual(res["data"]["months"][0]["repayments_minor"], 0)
        names = {x["name"]: x for x in res["data"]["scenarios"]}
        self.assertLess(
            names["with the repayments resuming"]["ending_cash_minor"],
            names["as stated"]["ending_cash_minor"])

    def test_the_rates_the_scenarios_offer_and_where_the_floor_comes_from(self):
        p = self.res["params"]
        names = [x["name"] for x in self.res["data"]["scenarios"]]
        self.assertIn("spend at the home rate", names)
        self.assertIn(f"spend at the After the move "
                      f"{p['spend_phase_months']}-month rate", names)
        phase = stat("monthly-cashflow")["data"]["phases"][-1]
        self.assertEqual(p["spend_phase_rate_minor"], phase["spend_per_month_minor"])
        self.assertEqual(p["spend_phase_months"], phase["months"])
        rates = {x["name"]: x["value_minor"] for x in self.res["data"]["spend_rates"]}
        self.assertEqual(len(rates), 5)
        self.assertEqual(rates["window median"], p["spend_median_minor"])
        self.assertEqual(rates["window mean"], p["spend_mean_minor"])
        for value in rates.values():
            self.assertIs(type(value), int)

    def test_the_low_point_is_data_in_cents_for_the_chart_to_mark(self):
        # G13: the chart marks the lowest month, so it reads a field, not a finding
        low = min(self.res["data"]["months"], key=lambda m: m["cash_minor"])
        point = self.res["data"]["low_point"]
        self.assertEqual(point["month"], low["month"])
        self.assertIsInstance(point["cash_minor"], int)
        self.assertEqual(point["cash_minor"], low["cash_minor"])
        scenario = {s["name"]: s for s in self.res["data"]["scenarios"]}["as stated"]
        self.assertEqual(point["month"], scenario["lowest_month"])

    def test_the_months_below_zero_are_listed_in_order(self):
        self.assertEqual(self.res["data"]["below_zero"],
                         [m["month"] for m in self.res["data"]["months"] if m["cash_minor"] < 0])
        # a one-off big enough to take the line under zero from its month on
        start = self.res["data"]["starting_cash_minor"]
        res = planned(one_offs=[{"month": "2026-12", "amount_minor": start + 10_000_000,
                                 "label": "invented"}])
        below = res["data"]["below_zero"]
        self.assertEqual(below[0], "2026-12")
        self.assertEqual(below, [m["month"] for m in res["data"]["months"] if m["month"] >= "2026-12"])
        self.assertLess(res["data"]["low_point"]["cash_minor"], 0)

    def test_a_tie_marks_the_first_month_and_nothing_projected_marks_none(self):
        from analytics.stats import forward_view
        months = [{"month": "2026-05", "cash_minor": 1_000}, {"month": "2026-06", "cash_minor": -7},
                  {"month": "2026-07", "cash_minor": -7}]
        self.assertEqual(forward_view.low_point(months), {"month": "2026-06", "cash_minor": -7})
        self.assertIsNone(forward_view.low_point([]))

    def test_the_insurance_premium_is_a_parameter_that_defaults_to_zero(self):
        from analytics.stats import forward_view
        self.assertEqual(forward_view.DEFAULTS["insurance_premium_minor"], 0)
        with_premium = planned(insurance_premium_minor=20_000)
        self.assertEqual(with_premium["data"]["months"][0]["insurance_minor"], 20_000)
        self.assertEqual(with_premium["data"]["months"][-1]["cash_minor"],
                         self.res["data"]["months"][-1]["cash_minor"] - 20_000 * len(self.res["data"]["months"]))


class UnderSettings(unittest.TestCase):
    """Run each test under its own config.toml text instead of the suite's."""

    SETTINGS = ""

    def setUp(self):
        stack = contextlib.ExitStack()
        stack.enter_context(settings(self.SETTINGS))
        self.addCleanup(stack.close)


class NeutralDefaults(UnderSettings):
    """G10: a new user's config.toml has no [analytics] life details at all.
    Every stat still runs, and nothing in the output is somebody else's life:
    no phases, no repayment payee, no age band, no pay, no extra income."""

    def test_no_phase_means_the_whole_record_is_one_phase(self):
        self.assertEqual(c.configured_phases(), ())
        self.assertEqual(c.phases(), (c.WHOLE_RECORD,))
        phases = stat("monthly-cashflow")["data"]["phases"]
        self.assertEqual([(p["name"], p["from"], p["to"]) for p in phases],
                         [("Whole record", MONTHS[0], MONTHS[-1])])
        cats = stat("category-breakdown")["data"]["categories"]
        self.assertTrue(all("whole_record_per_month_minor" in x for x in cats))
        self.assertFalse(any(m["before_phases"] for m in c.coverage(SNAP).values()))

    def test_every_stat_runs_on_an_empty_a_missing_and_a_broken_config(self):
        for text in ("", "[analytics]\n", "this is = not [valid toml"):
            with settings(text):
                for module in stats.modules():
                    with self.subTest(config=text, stat=module.ID):
                        res = module.run(SNAP, BENCHMARKS)
                        self.assertIsInstance(res["findings"], list)
        before = finance_settings.SETTINGS_FILE
        finance_settings.SETTINGS_FILE = Path(__file__).parent / "no-such-config.toml"
        try:
            self.assertEqual(c.settings(), {})
            self.assertEqual(c.phases(), (c.WHOLE_RECORD,))
        finally:
            finance_settings.SETTINGS_FILE = before

    def test_the_forward_view_without_a_plan_earns_nothing_for_twelve_months(self):
        res = _fv.run(SNAP, None)
        months = res["data"]["months"]
        self.assertEqual(len(months), 12)
        self.assertEqual(months[0]["month"], c.add_months(res["params"]["starting_month"], 1))
        self.assertEqual(res["title"], f"Forward view to {c.month_label(months[-1]['month'])}")
        self.assertTrue(all(m["gross_minor"] == 0 and m["investing_minor"] == 0 for m in months))
        self.assertEqual(res["params"]["zero_income_months"], [])
        names = [s["name"] for s in res["data"]["scenarios"]]
        self.assertNotIn("investing paused", names)
        self.assertNotIn("with the repayments resuming", names)
        self.assertFalse([n for n in names if n.startswith("with ")])
        self.assertEqual(res["params"]["other_outflow_observed"]["repayments_minor"], 0)
        for finding in res["findings"]:
            self.assertNotIn("month with no income", finding)
            self.assertNotIn("Pausing", finding)
            self.assertNotIn("repayment", finding)

    def test_a_long_stay_is_over_sixty_days(self):
        from analytics.stats import travel
        self.assertEqual(travel.long_stay_days(), 60)
        self.assertEqual(stat("travel")["params"]["long_stay_days"], 60)

    def test_no_reference_is_preferred_for_an_age_band(self):
        from analytics.stats import benchmarks_sg as b
        self.assertEqual(b.age_band(), "")
        self.assertEqual(b.derived_specs(), b.DERIVED)
        aged = {"benchmark_id": "hes-2023-age-40-44-food", "component_of": None}
        plain = {"benchmark_id": "hes-2023-all-x-food", "component_of": None}
        self.assertEqual(b.closeness(aged)[0], b.closeness(plain)[0])


class ConfiguredSettings(UnderSettings):
    """G10: what a user puts in [analytics] is what the stats use. Every value
    here is invented."""

    SETTINGS = """
[analytics]
repayment_payee = "Lender"
long_stay_days = 2
age_band = "40-44"
phases = [
  { name = "Up north", from = "2025-03", to = "2025-08" },
  { name = "", from = "2025-01" },
  { name = "Bad month", from = "2025-13" },
  { name = "Ends before it starts", from = "2025-09", to = "2025-02" },
  "not a table",
  { name = "Down south", from = "2025-09" },
  { name = "Down-South", from = "2025-11" },
]
income_scenarios = [
  { label = "a side job", monthly = 420.5 },
  { label = "not a number", monthly = nan },
  { label = "too much", monthly = inf },
  { label = "", monthly = 10 },
  { label = "nothing", monthly = -1 },
]
"""

    def test_the_named_phases_are_used_and_the_broken_ones_skipped(self):
        self.assertEqual([p["name"] for p in c.phases()], ["Up north", "Down south"])
        self.assertEqual(c.phase_of("2025-01"), "Before Mar 2025")
        self.assertEqual(c.phase_of("2025-10"), "Down south")
        cov = c.coverage(SNAP)
        self.assertEqual([m for m in MONTHS if cov[m]["before_phases"]],
                         ["2025-01", "2025-02"])
        self.assertTrue(cov["2025-01"]["partial"])
        cats = stat("category-breakdown")["data"]["categories"]
        self.assertTrue(all("up_north_per_month_minor" in x and "down_south_per_month_minor" in x
                            for x in cats))

    def test_the_long_stay_limit_is_the_configured_one(self):
        res = stat("travel")
        self.assertEqual(res["params"]["long_stay_days"], 2)
        trip = next(t for t in res["data"]["trips"] if t["name"] == TRIP)
        self.assertEqual(trip["days"], 3)
        self.assertTrue(trip["long_stay"])
        self.assertTrue(res["findings"][0].startswith("Leaving out the stays over 2 days"))

    def test_a_long_stay_limit_that_is_not_a_positive_whole_number_falls_back(self):
        from analytics.stats import travel
        for value in ('"sixty"', "0", "-5", "true", "12.5"):
            with self.subTest(value=value), settings(f"[analytics]\nlong_stay_days = {value}\n"):
                self.assertEqual(travel.long_stay_days(), 60)

    def test_the_age_band_is_preferred_and_gets_its_food_total(self):
        from analytics.stats import benchmarks_sg as b
        self.assertEqual(b.age_band(), "40-44")
        self.assertIn("hes-2023-age-40-44-food-total",
                      [s["id"] for s in b.derived_specs()])
        aged = {"benchmark_id": "hes-2023-age-40-44-food", "component_of": None}
        other = {"benchmark_id": "hes-2023-age-20-24-food", "component_of": None}
        self.assertGreater(b.closeness(aged), b.closeness(other))
        items = [{"id": "hes-2023-age-40-44-food-services", "value": 300.0,
                  "unit": "SGD per household per month", "source": "s", "url": "u"},
                 {"id": "hes-2023-age-40-44-food-at-home", "value": 200.0,
                  "unit": "SGD per household per month", "source": "s", "url": "u"}]
        total = next(x for x in b.derive(items) if x.get("derived_from"))
        self.assertEqual(total["value"], 500.0)
        # G20: as the stat sees them, read in cents, the total is cents too
        cents = b.in_cents({"items": items})["items"]
        total = next(x for x in b.derive(cents) if x.get("derived_from"))
        self.assertEqual(total["value_minor"], 50_000)
        self.assertIn("(S$300.00) plus", total["notes"])

    def test_an_extra_income_scenario_is_priced_and_the_broken_ones_skipped(self):
        res = planned()
        by_name = {s["name"]: s for s in res["data"]["scenarios"]}
        extra = [n for n in by_name if n.startswith("with ") and "repayments" not in n]
        self.assertEqual(extra, ["with a side job at S$420.50 a month"])
        months = len(res["data"]["months"])
        self.assertEqual(by_name[extra[0]]["ending_cash_minor"],
                         by_name["as stated"]["ending_cash_minor"] + 42_050 * months)

    def test_the_repayment_payee_alone_matches_when_no_category_is_set(self):
        self.assertEqual(_fv.repayment_category(), "")
        observed = _fv.run(SNAP, None)["params"]["other_outflow_observed"]
        self.assertGreater(observed["repayments_minor"], 0)
        with settings('[analytics]\nrepayment_payee = "Lender"\n'
                      'repayment_category = "Groceries"\n'):
            observed = _fv.run(SNAP, None)["params"]["other_outflow_observed"]
            self.assertEqual(observed["repayments_minor"], 0)


class DataQuality(unittest.TestCase):

    def setUp(self):
        self.res = stat("data-quality")
        self.counts = self.res["data"]["counts"]

    def test_the_synthetic_row_is_counted_and_listed(self):
        self.assertEqual(self.counts["synthetic"], 1)
        self.assertEqual(len(self.res["data"]["synthetic"]), 1)

    def test_only_the_row_on_the_rateless_block_counts_as_unconverted(self):
        self.assertEqual(self.counts["unconverted"], 1)
        self.assertEqual(self.res["data"]["unconverted"][0]["period"],
                         f"Travel wallet · NOK · {NO_RATE_MONTH}")

    def test_the_fix_list_is_sorted_by_size_and_says_why(self):
        fix = self.res["data"]["fix_list"]
        self.assertEqual(self.counts["uncategorised"], len(fix))
        self.assertEqual([abs(x["amount_sgd_minor"]) for x in fix],
                         sorted((abs(x["amount_sgd_minor"]) for x in fix), reverse=True))
        self.assertIn("no merchant text printed", {x["reason"] for x in fix})

    def test_the_transfer_bucket_trap_is_named(self):
        self.assertEqual(self.counts["transfer_bucket_external"], 13)
        self.assertTrue(any("Transfer bucket" in f for f in self.res["findings"]))


if __name__ == "__main__":
    unittest.main()


class SpendExcludeKeys(unittest.TestCase):
    """[plan] spend_exclude_keys: a purchase the owner calls a one-off is out
    of every spending rate, not just the headline one."""

    def rates(self, exclude=()):
        from analytics.stats import forward_view
        return forward_view.run(SNAP, None, {"spend_exclude_keys": list(exclude)})

    def one_off_key(self):
        """The largest single spending row in the window, off any trip, so
        the trip-free rates are affected by it too."""
        p = self.rates()["params"]
        window = set(p["spend_window_all"])
        rows = [r for r in SNAP["transactions"]
                if r["month"] in window and r["bucket"] == "Spend"
                and r["flow_class"] == "External" and r["amount_sgd_minor"] < 0
                and not r["trip"]]
        return min(rows, key=lambda r: r["amount_sgd_minor"])

    def test_naming_a_row_takes_it_out_of_every_rate(self):
        row = self.one_off_key()
        from analytics.stats import forward_view
        before, after = self.rates()["params"], self.rates([row["key"]])["params"]
        self.assertEqual(after["spend_exclude_keys"], [row["key"]])
        # the mean falls by the row spread over the window, to within the cent
        # each of the two means rounds to
        self.assertLess(after["spend_mean_minor"], before["spend_mean_minor"])
        self.assertLessEqual(abs(before["spend_mean_minor"] - after["spend_mean_minor"]
                                 - (-row["amount_sgd_minor"]) / len(before["spend_window_all"])), 1)
        # and its own month falls by the whole row in every rate, the
        # trip-free ones included, whether or not the median moves with it
        window, limit = before["spend_window_all"], before["refund_limit_minor"]
        skip = forward_view.excluded([row["key"]])
        for drop in (None, forward_view.on_a_trip(SNAP),
                     forward_view.on_a_trip(SNAP, short_only=True)):
            test = forward_view.either(skip, drop)
            plain = {x["month"]: x["spend_minor"] for x in
                     forward_view.spend_window(SNAP, window, limit, drop=drop)}
            less = {x["month"]: x["spend_minor"] for x in
                    forward_view.spend_window(SNAP, window, limit, drop=test)}
            self.assertEqual(plain[row["month"]] - less[row["month"]], -row["amount_sgd_minor"])
            self.assertEqual([m for m in window if m != row["month"] and plain[m] != less[m]], [])

    def test_a_key_that_matches_nothing_changes_nothing(self):
        self.assertEqual(self.rates(["no-such-key"])["params"]["spend_mean_minor"],
                         self.rates()["params"]["spend_mean_minor"])

    def test_nothing_is_excluded_unless_the_plan_says_so(self):
        self.assertEqual(self.rates()["params"]["spend_exclude_keys"], [])

    def test_the_caveat_says_whether_any_row_is_out(self):
        row = self.one_off_key()
        self.assertTrue(any("1 row(s)" in x and "one-off" in x
                            for x in self.rates([row["key"]])["caveats"]))
        self.assertTrue(any("No row is excluded" in x for x in self.rates()["caveats"]))

    def test_the_config_reader_keeps_the_key_and_drops_the_note(self):
        from analytics.stats import forward_view
        import tempfile, textwrap
        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as f:
            f.write(textwrap.dedent("""
                [plan]
                spend_exclude_keys = ["abc123  120.00 kettle, 2026-01-05", "def456"]
            """))
        self.assertEqual(forward_view.plan_params(f.name)["spend_exclude_keys"],
                         ["abc123", "def456"])


class HomeRate(unittest.TestCase):
    """The home rate is what a month in Singapore costs: the months spent
    mostly at home, with trip rows and Travel bookings taken out."""

    def fv(self):
        from analytics.stats import forward_view
        return forward_view

    def snap(self, trips):
        return {"trips": trips}

    def test_a_day_covered_by_two_trips_counts_once(self):
        fv = self.fv()
        trips = [{"name": "Long stay", "start": "2026-05-01", "end": "2026-05-31"},
                 {"name": "Side trip", "start": "2026-05-10", "end": "2026-05-12"}]
        self.assertEqual(fv.away_days(self.snap(trips)), {"2026-05": 31})

    def test_a_trip_across_a_month_end_splits_its_days(self):
        fv = self.fv()
        trips = [{"name": "Long stay", "start": "2023-05-11", "end": "2023-07-28"}]
        self.assertEqual(fv.away_days(self.snap(trips)),
                         {"2023-05": 21, "2023-06": 30, "2023-07": 28})

    def test_a_month_is_home_when_fewer_than_half_its_days_are_away(self):
        fv = self.fv()
        trips = [{"name": "Long stay", "start": "2023-05-11", "end": "2023-07-28"},
                 {"name": "Short trip", "start": "2022-12-26", "end": "2022-12-28"},
                 {"name": "Half", "start": "2023-04-01", "end": "2023-04-15"},
                 {"name": "Undated", "start": None, "end": None}]
        months = ["2022-12", "2023-04", "2023-05", "2023-06", "2023-07", "2023-08"]
        # April: 15 of 30 days away is exactly half, which is not fewer than half
        self.assertEqual(fv.home_months(self.snap(trips), months), ["2022-12", "2023-08"])

    def test_a_travel_booking_is_never_home_spending(self):
        fv = self.fv()
        self.assertTrue(fv.is_travel({"category": "Travel"}))
        self.assertFalse(fv.is_travel({"category": "Eating out"}))
        self.assertFalse(fv.is_travel({}))

    def test_the_home_rate_reads_its_own_window_and_says_which(self):
        res = stat("forward-view")
        p = res["params"]
        fv = self.fv()
        self.assertTrue(p["spend_home_window"])
        home = fv.home_months(SNAP, p["spend_home_window"])
        self.assertEqual(home, p["spend_home_window"], "every month in it is a home month")
        rows = fv.spend_window(SNAP, p["spend_home_window"], p["refund_limit_minor"],
                               drop=fv.either(fv.excluded(p["spend_exclude_keys"]),
                                              fv.on_a_trip(SNAP), fv.is_travel))
        self.assertEqual(p["spend_ex_all_trips_minor"], c.median_cents([x["spend_minor"] for x in rows]))
