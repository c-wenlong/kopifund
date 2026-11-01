"""CPF: the stat, the forward view's employer share and the two hub rows.

The fixture's snapshot carries no `cpf` key by default, which is what every
snapshot written before the yearly statements were loaded looks like, so the
first test here is that the stat still produces a result from one. The three
invented statements in `analytics_fixture.cpf_statements` cover a year that is
split into an employee and an employer half, a year that prints no split at all
(the 2016 to 2019 shape), and a year with an outflow in it.

The forward view's job here is narrow: the employer's 17 per cent is savings,
not cash, so the cash line must be identical whether the employer share is
modelled or not. That is the assertion this file exists for.
"""
import isolate  # noqa: F401  first: no test writes the real record or cache
import copy
import unittest
from decimal import Decimal
from pathlib import Path


import analytics_fixture as fixture
from analytics import publish
from analytics.stats import cpf, forward_view
from finance import cpf_rules as rules
from finance.money import label, to_cents

# The forward view reads the owner's [plan] from server/finance/config.toml; this
# suite must not depend on it, so point it at the fixture config (no [plan]).
forward_view.PLAN_CONFIG = Path(__file__).resolve().parent / "fixtures" / "config.toml"


def snapshot(statements=None, with_portfolio=True):
    """The fixture snapshot with a CPF block, and a brokerage one by default so
    the net worth has all three sides to add."""
    return fixture.snapshot(
        portfolio=fixture.portfolio() if with_portfolio else None,
        cpf=fixture.cpf(statements))


class ASnapshotWithNoStatements(unittest.TestCase):

    def test_a_snapshot_with_no_cpf_key_still_produces_a_result(self):
        res = cpf.run(fixture.snapshot(), None)
        self.assertEqual(res["id"], "cpf")
        self.assertEqual(res["data"]["years"], [])
        self.assertEqual(res["data"]["summary"]["total_minor"], 0)
        self.assertEqual(len(res["findings"]), 1)
        self.assertIn("statements/cpf", res["findings"][0])

    def test_an_empty_folder_says_what_to_download_rather_than_zero(self):
        res = cpf.run(snapshot(statements=[]), None)
        self.assertIsNone(res["data"]["summary"]["as_of"])
        self.assertIsNone(res["data"]["summary"]["net_worth_total_minor"])
        self.assertIn("statement of account", res["findings"][0])

    def test_the_empty_result_is_still_a_valid_envelope(self):
        res = cpf.run(fixture.snapshot(), None)
        for key in ("id", "title", "generated_at", "snapshot", "chart",
                    "sources", "params", "data", "findings", "caveats"):
            self.assertIn(key, res)
        self.assertTrue(res["caveats"])


class TheYearsTable(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.rows = cpf.run(snapshot(), None)["data"]["years"]

    def test_one_row_per_statement_oldest_first(self):
        self.assertEqual([r["year"] for r in self.rows], [2023, 2024, 2025])

    def test_the_closing_balance_is_the_three_accounts(self):
        last = self.rows[-1]
        self.assertEqual(last["oa_minor"], 166_147)
        self.assertEqual(last["sa_minor"], 48_620)
        self.assertEqual(last["ma_minor"], 202_466)
        self.assertEqual(last["total_minor"], 417_233)
        self.assertEqual(last["oa_minor"] + last["sa_minor"] + last["ma_minor"],
                         last["total_minor"])

    def test_a_layout_that_prints_no_split_leaves_the_halves_null(self):
        row = [r for r in self.rows if r["year"] == 2024][0]
        self.assertIsNone(row["employee_minor"])
        self.assertIsNone(row["employer_minor"])
        self.assertEqual(row["employment_minor"], 0)

    def test_the_government_figure_is_its_support_plus_the_interest(self):
        row = [r for r in self.rows if r["year"] == 2023][0]
        self.assertEqual(row["interest_minor"], 9_585)
        self.assertEqual(row["government_minor"], 59_585)
        self.assertEqual(cpf.support(row), 50_000)

    def test_growth_is_the_closing_total_less_the_opening_total(self):
        self.assertEqual(self.rows[-1]["growth_minor"], 236_676)
        for row, st in zip(self.rows, fixture.cpf_statements()):
            self.assertEqual(row["growth_minor"],
                             to_cents(st["closing"]["total"]) - to_cents(st["opening"]["total"]))

    def test_the_outflow_is_carried_positive(self):
        self.assertEqual(self.rows[-1]["outflow_minor"], 2_000)
        self.assertEqual(self.rows[0]["outflow_minor"], 0)

    def test_the_chart_series_are_on_every_row(self):
        self.assertEqual(cpf.CHART["source"], "years")
        self.assertEqual(cpf.CHART["unit"], "sgd_minor")
        for row in self.rows:
            for name in cpf.CHART["series"]:
                self.assertIs(type(row[name]), int)
            self.assertIsInstance(row[cpf.CHART["x"]], int)


class TheSummary(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.summary = cpf.run(snapshot(), None)["data"]["summary"]

    def test_the_headline_is_the_latest_statement(self):
        self.assertEqual(self.summary["total_minor"], 417_233)
        self.assertEqual(self.summary["as_of"], "2025-12-31")
        self.assertEqual(self.summary["years"], 3)

    def test_the_whole_record_totals_add_the_years_up(self):
        self.assertEqual(self.summary["contributed_employee_all_minor"], 136_200)
        self.assertEqual(self.summary["contributed_employer_all_minor"], 116_300)
        self.assertEqual(self.summary["interest_all_minor"], 42_733)
        self.assertEqual(self.summary["government_all_minor"], 122_733)

    def test_the_employer_share_of_the_last_year(self):
        self.assertAlmostEqual(self.summary["employer_share_last"], 0.4610, places=4)

    def test_a_last_year_with_no_split_has_no_employer_share(self):
        two = fixture.cpf_statements()[:2]
        summary = cpf.run(snapshot(two), None)["data"]["summary"]
        self.assertIsNone(summary["employer_share_last"])

    def test_net_worth_is_cash_plus_investments_plus_cpf(self):
        s = self.summary
        self.assertEqual(s["net_worth_total_minor"],
                         s["cash_sgd_minor"] + s["investments_sgd_minor"] + s["total_minor"])
        self.assertAlmostEqual(s["locked_share"],
                               round(s["total_minor"] / s["net_worth_total_minor"], 4), places=4)

    def test_cash_and_investments_are_the_portfolio_stat_s_own(self):
        from analytics.stats import portfolio
        pf = portfolio.run(snapshot(), None)["data"]["summary"]
        self.assertEqual(self.summary["cash_sgd_minor"], pf["cash_sgd_minor"])
        self.assertEqual(self.summary["investments_sgd_minor"], pf["value_sgd_minor"])

    def test_without_a_brokerage_statement_no_net_worth_is_claimed(self):
        s = cpf.run(snapshot(with_portfolio=False), None)["data"]["summary"]
        self.assertIsNone(s["cash_sgd_minor"])
        self.assertIsNone(s["investments_sgd_minor"])
        self.assertIsNone(s["net_worth_total_minor"])
        self.assertIsNone(s["locked_share"])


class TheFindings(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.findings = cpf.run(snapshot(), None)["findings"]

    def test_the_first_finding_names_the_balance_and_the_split(self):
        first = self.findings[0]
        self.assertIn("CPF held S$4,172.33", first)
        self.assertIn("OA S$1,661.47", first)
        self.assertIn("SA S$486.20", first)
        self.assertIn("MA S$2,024.66", first)

    def test_the_last_year_names_the_member_the_employer_and_the_interest(self):
        line = self.findings[1]
        self.assertIn("1,210.00", line)
        self.assertIn("1,035.00", line)
        self.assertIn("141.76", line)
        self.assertIn("2,366.76", line)

    def test_the_whole_record_says_when_wages_took_over(self):
        line = self.findings[2]
        self.assertIn("2,525.00", line)
        self.assertIn("until 2024", line)
        self.assertIn("since 2025", line)

    def test_the_net_worth_finding_names_all_three_dates(self):
        line = [f for f in self.findings if "net worth" in f][0]
        self.assertIn("at 31 Dec 2025", line)
        self.assertIn("at Dec 2025,", line)
        self.assertIn("per cent", line)

    def test_without_investments_the_net_worth_sentence_says_so(self):
        findings = cpf.run(snapshot(with_portfolio=False), None)["findings"]
        line = [f for f in findings if "brokerage" in f][0]
        self.assertIn("no net worth including CPF is stated", line)

    def test_a_statement_more_than_fifteen_months_old_asks_for_the_next_one(self):
        snap = copy.deepcopy(snapshot())
        snap["meta"]["generated_at"] = "2027-06-01T00:00:00+08:00"
        stale = [f for f in cpf.run(snap, None)["findings"] if "download" in f]
        self.assertEqual(len(stale), 1)
        self.assertIn("stops at 31 Dec 2025,", stale[0])

    def test_a_fresh_statement_asks_for_nothing(self):
        self.assertFalse([f for f in self.findings if "download" in f])

    def test_no_finding_or_caveat_uses_an_em_dash(self):
        res = cpf.run(snapshot(), None)
        for line in res["findings"] + res["caveats"]:
            self.assertNotIn("—", line)

    def test_the_caveats_say_cpf_is_savings_rather_than_a_liability(self):
        caveats = cpf.run(snapshot(), None)["caveats"]
        self.assertTrue(any("not a liability" in x or "rather than a liability" in x
                            for x in caveats))


class TheForwardViewsEmployerShare(unittest.TestCase):
    """The employer's 17 per cent is savings, so it may not move the cash line."""

    SNAP = fixture.snapshot()

    def setUp(self):
        # the fixture's settings and its invented plan, never the data folder's
        settings = fixture.settings()
        settings.__enter__()
        self.addCleanup(settings.__exit__, None, None, None)

    def run_plan(self, **over):
        return forward_view.run(self.SNAP, None, {**fixture.PLAN, **over})

    def months(self, **over):
        return self.run_plan(**over)["data"]["months"]

    def test_the_default_employer_rate_is_seventeen_per_cent(self):
        self.assertEqual(forward_view.DEFAULTS["cpf_employer_rate"], 0.17)

    def test_the_employer_share_is_the_rate_on_the_liable_wage(self):
        self.assertEqual(forward_view.cpf_employer(300_000, 0.17, 800_000), 51_000)

    def test_the_ordinary_wage_ceiling_caps_the_employer_share_too(self):
        self.assertEqual(forward_view.cpf_employer(1_000_000, 0.17, 800_000), 136_000)

    def test_every_month_carries_the_three_new_columns(self):
        for row in self.months():
            self.assertEqual(row["cpf_employer_minor"],
                             rules.cents(Decimal(min(row["gross_minor"], rules.OW_CEILING)) * Decimal("0.17")))
            self.assertEqual(row["cpf_total_minor"], row["cpf_employee_minor"] + row["cpf_employer_minor"])

    def test_the_accrual_is_the_running_total_of_the_two_shares(self):
        running = 0
        for row in self.months():
            running += row["cpf_total_minor"]
            self.assertEqual(row["cpf_accrued_minor"], running)

    def test_the_cash_line_is_the_same_with_the_employer_share_switched_off(self):
        with_it = [(m["month"], m["net_minor"], m["cash_minor"], m["take_home_minor"])
                   for m in self.months()]
        without = [(m["month"], m["net_minor"], m["cash_minor"], m["take_home_minor"])
                   for m in self.months(cpf_employer_rate=0.0)]
        self.assertEqual(with_it, without)

    def test_the_totals_are_in_the_params(self):
        p = self.run_plan()["params"]
        months = self.months()
        self.assertEqual(p["cpf_employee_total_minor"], sum(m["cpf_employee_minor"] for m in months))
        self.assertEqual(p["cpf_employer_total_minor"], sum(m["cpf_employer_minor"] for m in months))
        self.assertEqual(p["cpf_accrued_total_minor"], months[-1]["cpf_accrued_minor"])
        self.assertEqual(p["cpf_accrued_total_minor"],
                         p["cpf_employee_total_minor"] + p["cpf_employer_total_minor"])

    def test_the_finding_names_the_two_halves(self):
        res = self.run_plan()
        line = [f for f in res["findings"] if "into CPF" in f]
        self.assertEqual(len(line), 1)
        p = res["params"]
        self.assertIn(label(p["cpf_accrued_total_minor"]), line[0])
        self.assertIn(label(p["cpf_employee_total_minor"]), line[0])
        self.assertIn(label(p["cpf_employer_total_minor"]), line[0])
        self.assertIn("the cash line never shows", line[0])

    def test_the_rate_has_a_note_the_page_can_print(self):
        self.assertIn("cpf_employer_rate", forward_view.NOTES)
        self.assertIn("on top of gross", forward_view.NOTES["cpf_employer_rate"])

    def test_the_plan_section_can_set_the_rate(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as fh:
            fh.write('[plan]\ncpf_employer_rate = 0.13\n')
        self.assertEqual(forward_view.plan_params(fh.name)["cpf_employer_rate"], 0.13)
        self.assertNotIn("cpf_employer_rate",
                         forward_view.plan_params(forward_view.PLAN_CONFIG))


class TheHubRows(unittest.TestCase):

    RESULTS = {
        # G20: every money figure in a stat result is cents under `<name>_minor`
        "savings-runway": {"data": {"summary": {"cash_minor": 481_236, "as_of": "2026-08",
                                                "runway_6m": 2.5,
                                                "spend_avg_6m_minor": 192_540}}},
        "portfolio": {"data": {"summary": {"value_sgd_minor": 1_524_050, "as_of": "2026-08-31",
                                           "contributed_sgd_minor": 1_200_000,
                                           "gain_sgd_minor": 324_050, "statements": 3,
                                           "cash_sgd_minor": 481_236, "cash_as_of": "2026-08",
                                           "net_worth_sgd_minor": 2_005_286}}},
        "cpf": {"data": {"summary": {"total_minor": 417_233, "oa_minor": 166_147, "sa_minor": 48_620,
                                     "ma_minor": 202_466, "as_of": "2025-12-31", "years": 3,
                                     "cash_sgd_minor": 481_236, "cash_as_of": "2026-08",
                                     "investments_sgd_minor": 1_524_050,
                                     "investments_as_of": "2026-08-31",
                                     "net_worth_total_minor": 2_422_519,
                                     "locked_share": 0.172}}},
    }

    def rows(self, results=None):
        return publish.metrics_from(results or self.RESULTS, None, None)

    def test_the_two_rows_follow_the_cash_plus_investments_net_worth(self):
        names = [r["Name"] for r in self.rows()]
        self.assertEqual(names[2:6], ["Investments, market value",
                                      "Net worth, cash plus investments",
                                      "CPF, all three accounts",
                                      "Net worth, including CPF"])

    def test_the_cpf_row_names_the_three_accounts_and_says_it_is_not_cash(self):
        row = {r["Name"]: r for r in self.rows()}["CPF, all three accounts"]
        self.assertEqual(row["Value_minor"], 417_233)
        self.assertEqual(row["Unit"], "SGD")
        self.assertEqual(row["As of"], "2025-12-31")
        self.assertIn("OA 1,661.47", row["Note"])
        self.assertIn("SA 486.20", row["Note"])
        self.assertIn("MA 2,024.66", row["Note"])
        self.assertIn("locked savings, not cash", row["Note"])
        self.assertEqual(row["Stat"], "cpf")

    def test_the_net_worth_row_is_dated_at_the_latest_of_the_three(self):
        row = {r["Name"]: r for r in self.rows()}["Net worth, including CPF"]
        self.assertEqual(row["Value_minor"], 2_422_519)
        self.assertEqual(row["As of"], "2026-08-31")
        self.assertIn("cash at 2026-08", row["Note"])
        self.assertIn("investments at 2026-08-31", row["Note"])
        self.assertIn("CPF at 2025-12-31", row["Note"])

    def test_no_statements_writes_neither_row(self):
        results = {**self.RESULTS,
                   "cpf": {"data": {"summary": {"years": 0, "total_minor": 0,
                                                "net_worth_total_minor": None}}}}
        names = [r["Name"] for r in self.rows(results)]
        self.assertNotIn("CPF, all three accounts", names)
        self.assertNotIn("Net worth, including CPF", names)

    def test_an_unknown_net_worth_still_writes_the_cpf_row(self):
        results = {**self.RESULTS,
                   "cpf": {"data": {"summary": {**self.RESULTS["cpf"]["data"]["summary"],
                                                "net_worth_total_minor": None}}}}
        names = [r["Name"] for r in self.rows(results)]
        self.assertIn("CPF, all three accounts", names)
        self.assertNotIn("Net worth, including CPF", names)

    def test_the_order_column_stays_a_run_of_integers(self):
        rows = self.rows()
        self.assertEqual([r["Order"] for r in rows], list(range(1, len(rows) + 1)))


class InCents(unittest.TestCase):
    """G20: every CPF figure is SGD in integer cents; the two shares stay fractions."""

    def walk(self, o):
        if isinstance(o, dict):
            return list(o.items()) + [x for v in o.values() for x in self.walk(v)]
        if isinstance(o, list):
            return [x for v in o for x in self.walk(v)]
        return []

    def test_every_figure_is_cents_and_the_shares_are_fractions(self):
        for snap in (snapshot(), snapshot(with_portfolio=False), snapshot(statements=[])):
            data = cpf.run(snap, None)["data"]
            for k, v in self.walk(data):
                if isinstance(v, (dict, list)) or isinstance(v, str) or v is None:
                    continue
                with self.subTest(key=k):
                    if k in ("employer_share_last", "locked_share"):
                        self.assertIsInstance(v, float)
                    elif k not in ("year", "years"):
                        self.assertTrue(k.endswith("_minor") and type(v) is int, (k, v))

    def test_a_rejected_year_names_its_gaps_as_the_app_writes_money(self):
        block = fixture.cpf()
        block["rejected"] = [{"year": 2022, "file": "2022/2022.pdf",
                              "off": {"oa": -10.5, "sa": 0.0, "ma": 0.0}}]
        first = cpf.run(fixture.snapshot(cpf=block), None)["findings"][0]
        self.assertIn("by OA S$10.50,", first)


if __name__ == "__main__":
    unittest.main()
