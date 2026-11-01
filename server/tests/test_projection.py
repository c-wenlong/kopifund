"""G14: the Net worth projection's inputs, the tax and MediSave rules, the cash
flow month by month, and the forward view agreeing with it.

Every pay figure, date, balance and date of birth here is invented.
"""
import isolate  # noqa: F401  first: no test writes the real record or cache
import contextlib
import json
import os
import shutil
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest import mock

import serve
from finance import cpf_rules as rules
from analytics import growth, income, tax_rules
from analytics_fixture import settings, snapshot
from finance import db, planning

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "analytics"
BIRTH = "1996-03-15"                 # 30 in 2026, below every band edge in these years
TODAY = date(2026, 10, 12)


def conn():
    c = db.connect(":memory:")
    c.execute("DELETE FROM plan")
    return c


# ------------------------------------------------------------ the plan

class OldPlans(unittest.TestCase):
    def test_a_segment_from_before_g14_reads_as_employment_named_by_its_note(self):
        c = conn()
        planning.set_plan(c, {"dca": 300, "income": [
            {"from": "2026-01-01", "to": "2026-12-31", "gross": 4000, "cpf": True, "note": "first job"}]})
        rows = planning.income_rows(planning.get_plan(c))
        self.assertEqual(rows, [{"name": "first job", "type": "employment", "from": "2026-01-01",
                                 "to": "2026-12-31", "gross": 4000.0, "cpf": True,
                                 "bonus_months": None, "bonus_month": None}])

    def test_the_old_dca_is_the_first_step_and_is_not_rewritten_on_read(self):
        c = conn()
        planning.set_plan(c, {"dca": 300})
        self.assertEqual(planning.investing_steps(planning.get_plan(c)), [{"from": None, "amount": 300.0}])
        raw = dict(c.execute("SELECT key, value FROM plan").fetchall())
        self.assertEqual(raw, {"dca": "30000"})

    def test_saving_the_inputs_retires_the_dca(self):
        c = conn()
        planning.set_plan(c, {"dca": 300, "to": "2027-06"})
        planning.save_inputs(c, {"investing": [{"from": "", "amount": "300"}, {"from": "2027-01", "amount": "450.50"}]})
        plan = planning.get_plan(c)
        self.assertNotIn("dca", plan)
        self.assertEqual(plan["to"], "2027-06")               # the rest of the plan stays
        self.assertEqual(planning.investing_steps(plan),
                         [{"from": None, "amount": 300.0}, {"from": "2027-01", "amount": 450.5}])
        raw = json.loads(dict(c.execute("SELECT key, value FROM plan").fetchall())["investing"])
        self.assertEqual([s["amount"] for s in raw], [30000, 45050])

    def test_a_segment_with_no_cpf_flag_still_pays_cpf(self):
        self.assertTrue(planning.income_rows({"income": [{"from": "2026-01-01", "to": "2026-02-28",
                                                          "gross": 1}]})[0]["cpf"])

    def test_the_plan_page_round_trips_the_new_keys(self):
        c = conn()
        planning.save_inputs(c, {"income": [{"name": "A", "type": "side", "from": "2026-01-01", "to": "",
                                             "gross": "1200"}],
                                 "investing": [{"from": None, "amount": "100"}],
                                 "projection": {"expected_pct": "6.5", "spend": "2400", "milestone": "250000"}})
        before = planning.get_plan(c)
        planning.set_plan(c, before)                 # what the Plan page's Save sends back
        self.assertEqual(planning.get_plan(c), before)
        self.assertEqual(before["projection"]["spend"], 2400.0)
        self.assertEqual(before["income"][0]["cpf"], False)     # side income defaults to no CPF


class PlanPageSave(unittest.TestCase):
    def test_an_older_copy_from_the_plan_page_cannot_put_old_rows_back(self):
        c = conn()
        planning.set_plan(c, {"dca": 100, "insurance": 10,
                              "income": [{"from": "2026-01-01", "to": "2026-06-30", "gross": 1000, "note": "old"}]})
        stale = planning.get_plan(c)                          # what a Plan tab loaded
        planning.save_inputs(c, {"income": [{"name": "new", "from": "2026-01-01", "to": None, "gross": "2000"}],
                                 "investing": [{"from": None, "amount": "300"}]})
        planning.save_plan_page(c, {**stale, "insurance": 20})
        plan = planning.get_plan(c)
        self.assertEqual(plan["insurance"], 20.0)
        self.assertEqual([r["name"] for r in plan["income"]], ["new"])
        self.assertEqual(plan["investing"], [{"from": None, "amount": 300.0}])
        self.assertNotIn("dca", plan)


class NewFields(unittest.TestCase):
    def test_a_full_row_is_stored_in_its_shape(self):
        p = planning.clean_plan({"income": [{"name": " Acme ", "type": "employment", "from": "2026-01-01",
                                             "to": None, "gross": "6,000.50", "bonus_months": "1.5",
                                             "bonus_month": "12"}]})
        self.assertEqual(p["income"], [{"name": "Acme", "type": "employment", "from": "2026-01-01", "to": None,
                                        "gross": "6000.50", "cpf": True, "bonus_months": "1.5",
                                        "bonus_month": 12}])

    def test_settings_are_stored_as_exact_text(self):
        p = planning.clean_plan({"projection": {"inflation_pct": "2.50", "spend_growth_pct": "",
                                                "horizon": "30y", "dollars": "future"}})
        self.assertEqual(p["projection"], {"inflation_pct": "2.5", "horizon": "30y", "dollars": "future"})
        self.assertIsNone(planning.projection_settings(p)["spend_growth_pct"])

    def test_every_bad_field_is_refused_by_name(self):
        row = {"name": "Acme", "from": "2026-01-01", "to": "2026-12-31", "gross": 1000}
        cases = [
            ({"income": [{**row, "type": "freelance"}]}, "Income row 1 (Acme): the type"),
            ({"income": [{**row, "from": "2026-02-30"}]}, "the from date"),
            ({"income": [{**row, "to": "2025-12-31"}]}, "before the from date"),
            ({"income": [{**row, "gross": "lots"}]}, "gross a month is a number"),
            ({"income": [{**row, "gross": -1}]}, "gross a month is zero or more"),
            ({"income": [{**row, "gross": "1.005"}]}, "no finer"),
            ({"income": [{**row, "cpf": "yes"}]}, "CPF is yes or no"),
            ({"income": [{**row, "name": 7}]}, "the name is text"),
            ({"income": [{**row, "type": "side", "bonus_months": 1, "bonus_month": 12}]},
             "a bonus is for employment rows only"),
            ({"income": [{**row, "bonus_months": 1, "bonus_month": 13}]}, "the bonus month"),
            ({"income": [{**row, "bonus_months": 30, "bonus_month": 12}]}, "at most 24"),
            ({"income": [row, {"from": "2026-01-01", "to": "x", "gross": 1}]}, "Income row 2: the to date"),
            ({"investing": [{"from": "2026-13", "amount": 1}]}, "Investing step 1: from is a month"),
            ({"investing": [{"from": "2026-01", "amount": 1}, {"from": "2026-01", "amount": 2}]},
             "Investing step 2: another step already starts in 2026-01"),
            ({"investing": [{"from": None, "amount": ""}]}, "the amount a month is a number"),
            ({"projection": {"expected_pct": "abc"}}, "The expected return is a number"),
            ({"projection": {"inflation_pct": "25"}}, "Inflation is between -10 and 20"),
            ({"projection": {"pessimistic_pct": "7", "expected_pct": "6"}}, "lowest first"),
            ({"projection": {"horizon": "40y"}}, "The horizon"),
            ({"projection": {"spend": "-1"}}, "Spending a month is zero or more"),
            ({"projection": {"milestone": "a lot"}}, "The milestone is a number"),
            ({"income": "6000"}, "income is a list"),
        ]
        for plan, words in cases:
            with self.subTest(plan=plan):
                with self.assertRaises(planning.PlanRefused) as caught:
                    planning.clean_plan(plan)
                self.assertIn(words, str(caught.exception))

    def test_a_refused_save_keeps_the_old_plan(self):
        c = conn()
        planning.set_plan(c, {"dca": 100})
        with self.assertRaises(planning.PlanRefused):
            planning.save_inputs(c, {"investing": [{"from": "soon", "amount": 1}]})
        self.assertEqual(planning.get_plan(c), {"dca": 100.0})


# ------------------------------------------------------------ the rules

class Tax(unittest.TestCase):
    def test_the_brackets_on_invented_incomes(self):
        # chargeable income in dollars: the tax worked out by hand from the IRAS table
        for chargeable, want in ((0, 0), (20_000, 0), (30_000, 200), (40_000, 550), (47_000, 1_040),
                                 (80_000, 3_350), (120_000, 7_950), (73_160, Decimal("2871.20")),
                                 (500_000, 84_150), (1_500_000, 319_150)):
            with self.subTest(chargeable=chargeable):
                self.assertEqual(tax_rules.tax(chargeable * 100), int(Decimal(want) * 100))

    def test_earned_income_relief_by_age_and_never_more_than_the_income(self):
        self.assertEqual(tax_rules.earned_income_relief(54, 5_000_000), 100_000)
        self.assertEqual(tax_rules.earned_income_relief(55, 5_000_000), 600_000)
        self.assertEqual(tax_rules.earned_income_relief(60, 5_000_000), 800_000)
        self.assertEqual(tax_rules.earned_income_relief(None, 5_000_000), 100_000)
        self.assertEqual(tax_rules.earned_income_relief(58, 400_000), 400_000)

    def test_a_year_takes_the_reliefs_off_then_the_brackets(self):
        # S$5,000 a month all year, below 55: S$60,000 less S$12,000 CPF and S$1,000 relief
        y = tax_rules.year(6_000_000, 1_200_000, 30)
        self.assertEqual((y["reliefs"], y["chargeable"], y["tax"]), (1_300_000, 4_700_000, 104_000))

    def test_reliefs_are_capped_at_80000(self):
        self.assertEqual(tax_rules.year(50_000_000, 9_000_000, 30)["reliefs"], 8_000_000)

    def test_the_source_is_dated(self):
        self.assertEqual(tax_rules.AS_OF, "2026-10-01")
        self.assertIn("iras.gov.sg", tax_rules.SOURCE)


class EmployeeCpf(unittest.TestCase):
    def test_the_employee_share_by_age_band(self):
        b = date(1996, 3, 15)
        self.assertEqual(rules.employee_rate("2051-03", b), Decimal("0.20"))
        self.assertEqual(rules.employee_rate("2051-04", b), Decimal("0.18"))
        self.assertEqual(rules.employee_rate("2056-04", b), Decimal("0.125"))
        self.assertEqual(rules.employee_rate("2026-10", None), Decimal("0.20"))

    def test_it_stops_at_the_ordinary_wage_ceiling_and_counts_the_capped_bonus(self):
        self.assertEqual(rules.employee_contribution(1_000_000, "2026-10", None), 160_000)
        self.assertEqual(rules.employee_contribution(500_000, "2026-10", None, additional=500_000), 200_000)

    def test_the_bonus_attracting_cpf_is_capped_by_the_annual_ceiling(self):
        # S$10,000 a month all year: S$96,000 of OW attracts CPF, so S$6,000 of the bonus does
        pay = income.Pay(income.rows({"income": [
            {"name": "A", "from": "2026-01-01", "to": None, "gross": 10_000, "bonus_months": 2,
             "bonus_month": 12}]}), "0", "2026-01")
        dec = pay.at("2026-12")
        self.assertEqual((dec["bonus"], dec["aw"], dec["aw_liable"]), (2_000_000, 2_000_000, 600_000))
        self.assertEqual(rules.aw_ceiling(9_600_000), 600_000)


class SelfEmployedMedisave(unittest.TestCase):
    def test_the_table_by_income_and_age(self):
        young, older = date(1996, 3, 15), date(1985, 6, 1)     # 30 and 40 on 1 Jan 2026
        self.assertEqual(rules.self_employed_medisave(600_000, 2026, young), 0)
        self.assertEqual(rules.self_employed_medisave(1_000_000, 2026, young), 40_000)      # 4%
        self.assertEqual(rules.self_employed_medisave(1_500_000, 2026, young), 90_000)      # 6%, halfway
        self.assertEqual(rules.self_employed_medisave(2_000_000, 2026, older), 180_000)     # 9%
        self.assertEqual(rules.self_employed_medisave(20_000_000, 2025, young), 710_400)    # IRAS's 2025 maximum
        self.assertEqual(rules.self_employed_medisave(20_000_000, 2026, young), 768_000)    # 8% of S$96,000

    def test_the_source_is_dated(self):
        self.assertEqual(rules.MEDISAVE_SE_AS_OF, "2026-10-01")
        self.assertTrue(all("gov.sg" in s for s in rules.MEDISAVE_SE_SOURCE))


# ------------------------------------------------------------ the cash flow

# A job to the end of 2026 with a one-month bonus in December, a month with no
# job, then a second job with no end date; side income all along.
PLAN = {
    "income": [
        {"name": "Job A", "type": "employment", "from": "2026-10-01", "to": "2026-12-31", "gross": 5000,
         "bonus_months": 1, "bonus_month": 12},
        {"name": "Job B", "type": "employment", "from": "2027-02-01", "to": None, "gross": 6000},
        {"name": "Tutoring", "type": "side", "from": "2026-10-01", "to": None, "gross": 1000},
    ],
    "investing": [{"from": None, "amount": 200}, {"from": "2027-03", "amount": 500}],
    "projection": {"inflation_pct": "0", "dollars": "future", "spend": 2000, "milestone": 20000},
}
RESULTS = {"savings-runway": {"data": {"months": [
    {"month": "2026-09", "cash_minor": 1_000_000, "complete": True}]}}}


def run(plan=PLAN, results=RESULTS, birth=BIRTH, **kw):
    return growth.project(results, plan, birth, {}, today=TODAY, **kw)


class CashFlow(unittest.TestCase):
    def setUp(self):
        self.out = run()
        self.m = {r["month"]: r for r in self.out["cash"]["rows"]}

    def test_it_starts_from_the_last_complete_month(self):
        self.assertEqual(self.out["cash"]["as_of"], "2026-09")
        self.assertEqual(self.m["2026-09"]["cash"], 1_000_000)

    def test_employment_pays_cpf_and_side_income_does_not(self):
        oct_ = self.m["2026-10"]
        self.assertEqual((oct_["employment"], oct_["side"], oct_["bonus"]), (500_000, 100_000, 0))
        self.assertEqual(oct_["cpf_employee"], 100_000)            # 20 per cent of S$5,000, none on the side

    def test_the_break_month_has_no_income_from_work(self):
        jan = self.m["2027-01"]
        self.assertEqual((jan["employment"], jan["bonus"], jan["side"]), (0, 0, 100_000))
        self.assertEqual(jan["cpf_employee"], 0)

    def test_a_bonus_lands_in_its_month_with_cpf_on_it(self):
        self.assertEqual(self.m["2026-11"]["bonus"], 0)
        self.assertEqual(self.m["2026-12"]["bonus"], 500_000)
        self.assertEqual(self.m["2026-12"]["cpf_employee"], 200_000)
        self.assertEqual(self.m["2027-12"]["bonus"], 0)            # Job B has none

    def test_investing_steps_change_on_their_month(self):
        self.assertEqual([self.m[k]["investing"] for k in ("2026-10", "2027-02", "2027-03", "2027-04")],
                         [20_000, 20_000, 50_000, 50_000])

    def test_tax_by_hand(self):
        years = {y["year"]: y for y in self.out["tax_years"]}
        # 2026: S$23,000 earned, S$4,000 CPF and S$1,000 relief leave S$18,000: no tax
        self.assertEqual((years[2026]["assessable"], years[2026]["cpf_employee"], years[2026]["tax"]),
                         (2_300_000, 400_000, 0))
        # 2027: S$66,000 pay and S$12,000 side; S$13,200 CPF, S$480 MediSave (4 per cent of
        # S$12,000) and S$1,000 relief leave S$63,320, taxed S$550 plus 7 per cent of S$23,320
        y = years[2027]
        self.assertEqual((y["assessable"], y["cpf_employee"], y["medisave"], y["chargeable"], y["tax"]),
                         (7_800_000, 1_320_000, 48_000, 6_332_000, 218_240))
        # set aside in proportion to each month's income: January earns S$1,000 of S$78,000
        self.assertEqual(self.m["2027-01"]["tax"], 2_798)
        self.assertEqual(self.m["2027-01"]["medisave"], 4_000)

    def test_every_month_nets_to_the_change_in_cash(self):
        rows = self.out["cash"]["rows"]
        for prev, r in zip(rows, rows[1:]):
            with self.subTest(month=r["month"]):
                self.assertEqual(r["net"], r["income"] - r["cpf_employee"] - r["tax"] - r["medisave"]
                                 - r["spend"] - r["investing"])
                self.assertEqual(r["cash"], prev["cash"] + r["net"])

    def test_spending_grows_by_its_own_rate(self):
        out = run({**PLAN, "projection": {**PLAN["projection"], "spend_growth_pct": "10"}})
        m = {r["month"]: r for r in out["cash"]["rows"]}
        self.assertEqual(m["2026-10"]["spend"], 200_000)
        self.assertEqual(m["2027-10"]["spend"], 220_000)

    def test_no_spending_typed_spends_the_record_average(self):
        projection = {k: v for k, v in PLAN["projection"].items() if k != "spend"}
        out = run({**PLAN, "projection": projection}, spend_hint={"per_month": 123_456, "from": "2025-10",
                                                                  "to": "2026-09", "months": 12})
        self.assertEqual(out["assumptions"]["spend_from"], "average")
        self.assertEqual(out["cash"]["rows"][1]["spend"], 123_456)

    def test_an_open_ended_job_grows_once_a_year_from_this_month(self):
        out = run({**PLAN, "projection": {**PLAN["projection"], "salary_growth_pct": "10"}})
        m = {r["month"]: r for r in out["cash"]["rows"]}
        self.assertEqual(m["2027-09"]["employment"], 600_000)      # Job B from Feb 2027
        self.assertEqual(m["2028-02"]["employment"], 660_000)      # a full year after it starts
        self.assertEqual(m["2026-12"]["employment"], 500_000)      # a dated job is paid as typed


class TaxBeforeTheStart(unittest.TestCase):
    """A year's tax and MediSave cover all twelve months, but the balance cash
    starts from holds the pay of the months before it with nothing set aside."""

    PLAN = {
        "income": [
            {"name": "Job", "type": "employment", "from": "2026-01-01", "to": "2026-12-31", "gross": 8000},
            {"name": "Tutoring", "type": "side", "from": "2026-01-01", "to": "2026-12-31", "gross": 1000},
        ],
        "projection": {"inflation_pct": "0", "dollars": "future", "spend": 0},
    }

    def test_the_months_before_cash_starts_have_their_share_taken_in_the_first_month(self):
        out = run(self.PLAN)
        m = {r["month"]: r for r in out["cash"]["rows"]}
        # S$96,000 pay and S$12,000 side; S$19,200 CPF, S$480 MediSave and S$1,000 relief
        # leave S$87,320, taxed S$3,350 plus 11.5 per cent of S$7,320
        y = {t["year"]: t for t in out["tax_years"]}[2026]
        self.assertEqual((y["chargeable"], y["tax"], y["medisave"]), (8_732_000, 419_180, 48_000))
        # every month earns the same, so each sets aside a twelfth: S$349.32 and S$40.00.
        # Cash starts from September's balance, so October also takes January to September's
        self.assertEqual(out["cash"]["before_start"],
                         {"from": "2026-01", "to": "2026-09", "tax": 9 * 34_932, "medisave": 9 * 4_000})
        self.assertEqual((m["2026-10"]["tax"], m["2026-10"]["medisave"]), (10 * 34_932, 10 * 4_000))
        self.assertEqual((m["2026-11"]["tax"], m["2026-12"]["tax"]), (34_932, 34_932))
        # so the year's whole tax comes off cash, to the rounding of a twelfth
        taken = sum(r["tax"] for k, r in m.items() if k.startswith("2026"))
        self.assertLessEqual(abs(taken - y["tax"]), 12)
        self.assertEqual(sum(r["medisave"] for k, r in m.items() if k.startswith("2026")), y["medisave"])

    def test_the_first_month_still_nets_to_the_change_in_cash(self):
        rows = run(self.PLAN)["cash"]["rows"]
        oct_ = rows[1]
        self.assertEqual(oct_["net"], oct_["income"] - oct_["cpf_employee"] - oct_["tax"] - oct_["medisave"]
                         - oct_["spend"] - oct_["investing"])
        self.assertEqual(oct_["cash"], rows[0]["cash"] + oct_["net"])

    def test_a_december_start_takes_the_whole_year_in_january(self):
        results = {"savings-runway": {"data": {"months": [
            {"month": "2026-12", "cash_minor": 1_000_000, "complete": True}]}}}
        out = growth.project(results, self.PLAN, BIRTH, {}, today=date(2027, 1, 5))
        self.assertEqual(out["cash"]["before_start"]["tax"], 12 * 34_932)
        self.assertEqual(out["cash"]["rows"][1]["tax"], 12 * 34_932)  # 2027 earns nothing of its own

    def test_with_no_balance_to_start_from_nothing_is_taken(self):
        out = run(self.PLAN, {})
        self.assertFalse(out["cash"]["measured"])
        self.assertIsNone(out["cash"]["before_start"])
        self.assertEqual(out["cash"]["rows"][1]["tax"], 34_932)       # November's own share only


class Total(unittest.TestCase):
    def results(self):
        latest = json.loads((FIXTURES / "latest.json").read_text())
        return {k: json.loads((FIXTURES / "results" / v).read_text()) for k, v in latest["results"].items()}

    def test_the_total_is_the_parts_added_up(self):
        out = growth.project(self.results(), PLAN, BIRTH, {"dollars": "today", "inflation_pct": "2.5"},
                             today=TODAY)
        self.assertEqual(out["total"]["parts"], ["cash", "tiger", "cpf"])
        for r in out["total"]["rows"]:
            self.assertEqual(r["total"], r["cash"] + r["tiger"] + r["cpf"])
            self.assertLessEqual(r["low"], r["total"])
            self.assertGreaterEqual(r["high"], r["total"])

    def test_the_tiles_and_the_milestone(self):
        out = growth.project(self.results(), PLAN, BIRTH, {}, today=TODAY)
        t, rows = out["total"], {r["month"]: r for r in out["total"]["rows"]}
        self.assertEqual(t["tiles"]["today"], {"month": "2026-10", "total": rows["2026-10"]["total"]})
        self.assertEqual(t["tiles"]["in_5y"]["month"], "2031-10")
        self.assertEqual(t["tiles"]["in_10y"]["month"], "2036-10")
        first = next(r for r in out["total"]["rows"] if r["total"] >= 2_000_000)
        self.assertEqual(t["milestone"], {"value": 2_000_000, "month": first["month"], "total": first["total"]})

    def test_a_milestone_never_reached_says_so(self):
        out = run({**PLAN, "projection": {**PLAN["projection"], "milestone": 100_000_000}})
        self.assertIsNone(out["total"]["milestone"]["month"])

    def test_investing_with_no_tiger_statement_stays_in_net_worth(self):
        flat = {**PLAN["projection"], "pessimistic_pct": "0", "expected_pct": "0", "optimistic_pct": "0"}
        out = run({**PLAN, "projection": flat})
        none = run({**PLAN, "projection": flat, "investing": []})
        self.assertFalse(out["tiger"]["measured"])
        self.assertEqual(out["tiger"]["rows"][0], {"month": "2026-09", "age": 30, "pessimistic": 0, "expected": 0,
                                                    "optimistic": 0, "put_in": 0})
        self.assertIsNone(none["tiger"])                       # nothing invested, nothing to draw
        for a, b in zip(out["total"]["rows"], none["total"]["rows"]):
            self.assertEqual(a["total"], b["total"])           # a transfer moves money, it does not lose it
        self.assertEqual(out["total"]["parts"], ["cash", "tiger"])

    def test_cpf_held_past_65_keeps_its_balance_not_its_buying_power(self):
        born = "1975-06-01"                                   # 65 in June 2040, inside 30 years
        res = self.results()
        future = growth.project(res, PLAN, born, {"horizon": "30y", "dollars": "future"}, today=TODAY)
        today = growth.project(res, PLAN, born, {"horizon": "30y", "dollars": "today", "inflation_pct": "2.5"},
                               today=TODAY)
        last = future["cpf"]["rows"][-1]["month"]
        self.assertEqual(last, "2040-06")
        held = [r for r in future["total"]["rows"] if r["month"] > last]
        self.assertTrue(held and all(r["cpf_held"] for r in held))
        self.assertEqual({r["cpf"] for r in held}, {future["cpf"]["last_nominal"]})
        real = {r["month"]: r["cpf"] for r in today["total"]["rows"] if r["month"] > last}
        self.assertGreater(real["2041-06"], real["2051-06"])  # inflation keeps eating a held balance

    def test_cash_under_zero_is_named_in_runs(self):
        out = run({**PLAN, "projection": {**PLAN["projection"], "spend": 9000}})
        runs = out["total"]["below_zero"]
        self.assertTrue(runs)
        self.assertEqual(runs[0]["from"], next(r["month"] for r in out["total"]["rows"] if r["cash"] < 0))
        self.assertLess(out["total"]["low"]["cash"], 0)


# ------------------------------------------------------------ the forward view

class ForwardViewAgrees(unittest.TestCase):
    def setUp(self):
        stack = contextlib.ExitStack()
        stack.enter_context(settings())
        from analytics.stats import forward_view
        stack.enter_context(mock.patch.object(forward_view, "PLAN_CONFIG",
                                              Path(__file__).resolve().parent / "fixtures" / "config.toml"))
        self.addCleanup(stack.close)
        self.fv = forward_view

    def test_the_same_rows_give_the_same_pay_and_investing(self):
        # the plan as the record would hold it, read the way the stat reads it
        with mock.patch.object(self.fv, "_plan_from_db", return_value=PLAN), \
                mock.patch.object(self.fv, "_DEFAULT_PLAN_CONFIG", self.fv.PLAN_CONFIG):
            params = self.fv.plan_params()
        self.assertEqual(params["investing_steps"], [{"from": None, "amount_minor": 20_000},
                                                     {"from": "2027-03", "amount_minor": 50_000}])
        res = self.fv.run(snapshot(), None, {**params, "to": "2027-12"})
        fv = {m["month"]: m for m in res["data"]["months"]}
        nw = {r["month"]: r for r in run()["cash"]["rows"][1:]}     # the first row is the starting balance
        common = sorted(set(fv) & set(nw))
        self.assertIn("2027-01", common)
        for m in common:
            with self.subTest(month=m):
                # G20: both count in cents, so the two are the same integers
                self.assertEqual(fv[m]["gross_minor"], nw[m]["income"])
                self.assertEqual(fv[m]["investing_minor"], nw[m]["investing"])
        self.assertEqual(fv["2027-01"]["gross_minor"], 100_000)     # the break month: side income only
        self.assertEqual(fv["2026-12"]["cpf_employee_minor"], 200_000)   # the bonus pays CPF there too


# ------------------------------------------------------------ the endpoints

class Endpoints(unittest.TestCase):
    """Preview and save over a temporary data folder with the fixtures as its cache."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        shutil.copytree(FIXTURES, root / "cache")
        env = mock.patch.dict(os.environ, {"KOPIFUND_DATA": str(root), "KOPIFUND_DB": str(root / "kopifund.db"),
                                           "KOPIFUND_CACHE": str(root / "cache")})
        env.start()
        self.addCleanup(env.stop)
        with contextlib.closing(db.connect()) as c:
            planning.set_plan(c, {"dca": 250})
        self.server = serve.make_server(0, root)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def post(self, path, body):
        req = urllib.request.Request(f"http://127.0.0.1:{self.server.server_address[1]}{path}",
                                     data=json.dumps(body).encode(), method="POST",
                                     headers={"X-Kopifund": "1", "Content-Type": "application/json"})
        with urllib.request.urlopen(req) as reply:
            return json.loads(reply.read())

    def saved(self):
        with contextlib.closing(db.connect()) as c:
            return planning.get_plan(c)

    def test_a_preview_projects_the_unsaved_inputs_and_writes_nothing(self):
        out = self.post("/api/growth/preview", {"investing": [{"from": None, "amount": "900"}]})
        self.assertEqual(out["assumptions"]["investing_now"], 90_000)
        self.assertEqual(self.saved(), {"dca": 250.0})

    def test_save_stores_the_inputs_and_answers_the_new_projection(self):
        out = self.post("/api/growth/inputs", {"investing": [{"from": None, "amount_minor": 90_050}],
                                               "projection": {"expected_pct": "7", "spend_minor": 250_000}})
        self.assertEqual(out["assumptions"]["expected_pct"], "7")
        # G17: the inputs come back in cents, never as dollars
        self.assertEqual(out["inputs"]["investing"], [{"from": None, "amount_minor": 90_050}])
        self.assertEqual(out["inputs"]["projection"]["spend_minor"], 250_000)
        self.assertNotIn("spend", out["inputs"]["projection"])
        self.assertEqual(out["assumptions"]["spend"], 250_000)
        self.assertNotIn("dca", self.saved())
        self.assertEqual(self.saved()["investing"], [{"from": None, "amount": Decimal("900.50")}])

    def test_a_page_from_before_g17_still_saves_dollars(self):
        # a draft kept in a browser posts the old shape; the key says it is dollars
        out = self.post("/api/growth/inputs", {"investing": [{"from": None, "amount": "900.50"}],
                                               "projection": {"spend": 2500}})
        self.assertEqual(out["inputs"]["investing"], [{"from": None, "amount_minor": 90_050}])
        self.assertEqual(out["inputs"]["projection"]["spend_minor"], 250_000)
        # a small figure is still dollars when its key says so, and cents when its key says that
        out = self.post("/api/growth/preview", {"investing": [{"from": None, "amount": 5}]})
        self.assertEqual(out["assumptions"]["investing_now"], 500)
        out = self.post("/api/growth/preview", {"investing": [{"from": None, "amount_minor": 5}]})
        self.assertEqual(out["assumptions"]["investing_now"], 5)

    def test_cents_are_whole_and_a_field_is_sent_one_way(self):
        for body, says in (({"investing": [{"from": None, "amount_minor": 900.5}]}, "Investing step 1: the amount a month is a whole number of cents"),
                           ({"investing": [{"from": None, "amount_minor": "900"}]}, "whole number of cents"),
                           ({"investing": [{"from": None, "amount_minor": True}]}, "whole number of cents"),
                           ({"income": [{"name": "Acme", "from": "2026-01-01", "gross_minor": 1, "gross": "1"}]},
                            "Income row 1 (Acme): gross a month is sent twice"),
                           ({"projection": {"spend_minor": 1.5}}, "Spending a month is a whole number of cents")):
            for path in ("/api/growth/preview", "/api/growth/inputs"):
                with self.subTest(body=body, path=path):
                    with self.assertRaises(urllib.error.HTTPError) as caught:
                        self.post(path, body)
                    with caught.exception as reply:
                        self.assertEqual(reply.code, 422)
                        self.assertIn(says, json.loads(reply.read())["error"])
        self.assertEqual(self.saved(), {"dca": 250.0})

    def test_the_plan_page_reads_and_saves_cents(self):
        # this folder has no config, and the stats rerun a save sets off is not this test's subject
        self.enterContext(mock.patch("finance.api.config", lambda: None))
        self.enterContext(mock.patch("finance.stats_rebuild.refresh_stats"))
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.post("/api/plan", {"insurance_minor": 12_050.5})
        with caught.exception as reply:
            self.assertEqual(reply.code, 422)
        out =self.post("/api/plan", {"insurance_minor": 12_050, "one_offs": [{"month": "2027-01", "amount_minor": 99}],
                                      "trips": [{"name": "T", "from": "2027-01-01", "to": "2027-01-02",
                                                 "per_day_minor": None, "flights_minor": 40_000}]})
        self.assertEqual(out["insurance_minor"], 12_050)
        self.assertEqual(out["one_offs"], [{"month": "2027-01", "amount_minor": 99}])
        self.assertEqual(out["trips"], [{"name": "T", "from": "2027-01-01", "to": "2027-01-02", "flights_minor": 40_000}])
        self.assertEqual(out["dca_minor"], 25_000)                 # the Net worth page's to write; it stands
        self.assertEqual(self.saved()["insurance"], Decimal("120.50"))
        # the old shape still saves, for one release
        self.assertEqual(self.post("/api/plan", {"insurance": "99.99"})["insurance_minor"], 9_999)

    def test_a_refused_input_is_a_422_naming_the_field(self):
        for path in ("/api/growth/preview", "/api/growth/inputs"):
            with self.subTest(path=path):
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    self.post(path, {"income": [{"name": "Acme", "from": "2026-01-01", "gross": "x"}]})
                with caught.exception as reply:
                    self.assertEqual(reply.code, 422)
                    self.assertIn("Income row 1 (Acme): gross a month", json.loads(reply.read())["error"])
        self.assertEqual(self.saved(), {"dca": 250.0})


if __name__ == "__main__":
    unittest.main()
