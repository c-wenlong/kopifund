"""The CPF rules, the growth projection and net worth over time (G8).

Every balance, wage and date of birth here is invented.
"""
import isolate  # noqa: F401  first: no test writes the real record or cache
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
from analytics import growth

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "analytics"

# turns 35 in March 2031, 55 in March 2051, 65 in March 2061
BIRTH = date(1996, 3, 15)


def accounts(oa=0, sa=0, ma=0, ra=0, ra_open=False):
    return rules.Accounts(oa=oa, sa=sa, ma=ma, ra=ra, ra_open=ra_open)


class Tables(unittest.TestCase):
    def test_each_band_allocates_the_whole_contribution(self):
        for last, shares in rules.ALLOCATION:
            with self.subTest(band=last):
                self.assertEqual(sum(shares), Decimal(1))

    def test_the_bands_run_upwards_and_end_open(self):
        for table in (rules.ALLOCATION, rules.CONTRIBUTION_RATES):
            lasts = [b for b, _ in table]
            self.assertIsNone(lasts[-1])
            self.assertEqual(lasts[:-1], sorted(lasts[:-1]))

    def test_a_band_changes_the_month_after_the_birthday_month(self):
        self.assertEqual(rules.contribution_rate("2051-03", BIRTH), Decimal("0.37"))
        self.assertEqual(rules.contribution_rate("2051-04", BIRTH), Decimal("0.34"))
        self.assertEqual(rules.contribution_rate("2056-04", BIRTH), Decimal("0.25"))
        self.assertEqual(rules.contribution_rate("2061-04", BIRTH), Decimal("0.165"))
        self.assertEqual(rules.contribution_rate("2066-04", BIRTH), Decimal("0.125"))
        self.assertEqual(rules.allocation("2031-03", BIRTH)[0], Decimal("0.6217"))
        self.assertEqual(rules.allocation("2031-04", BIRTH)[0], Decimal("0.5677"))

    def test_the_source_is_dated(self):
        self.assertEqual(rules.AS_OF, "2026-10-01")
        self.assertIn("cpf.gov.sg", rules.SOURCE)


class Contributions(unittest.TestCase):
    def test_pay_above_the_ordinary_wage_ceiling_attracts_nothing_more(self):
        at_ceiling = rules.contribution(800_000, "2027-01", BIRTH)
        above = rules.contribution(1_250_000, "2027-01", BIRTH)
        self.assertEqual(at_ceiling, above)
        self.assertEqual(sum(above), 296_000)              # 37 per cent of S$8,000

    def test_the_three_shares_add_up_to_the_contribution(self):
        oa, sa, ma = rules.contribution(512_345, "2027-01", BIRTH)
        self.assertEqual(oa + sa + ma, rules.cents(Decimal(512_345) * Decimal("0.37")))
        self.assertEqual((sa, ma), (rules.cents(Decimal(oa + sa + ma) * Decimal("0.1621")),
                                    rules.cents(Decimal(oa + sa + ma) * Decimal("0.2162"))))

    def test_no_wage_no_contribution(self):
        self.assertEqual(rules.contribution(0, "2027-01", BIRTH), (0, 0, 0))


class Overflow(unittest.TestCase):
    def test_medisave_above_the_bhs_goes_to_the_sa(self):
        acc = accounts(oa=100, sa=500_000, ma=rules.BHS + 2_000)
        rules.overflow_ma(acc, rules.BHS, rules.FRS)
        self.assertEqual((acc.ma, acc.sa, acc.oa), (rules.BHS, 502_000, 100))

    def test_then_to_the_oa_once_the_sa_holds_the_frs(self):
        acc = accounts(oa=100, sa=rules.FRS - 500, ma=rules.BHS + 2_000)
        rules.overflow_ma(acc, rules.BHS, rules.FRS)
        self.assertEqual((acc.ma, acc.sa, acc.oa), (rules.BHS, rules.FRS, 1_600))

    def test_from_55_it_goes_to_the_ra(self):
        acc = accounts(oa=0, ma=rules.BHS + 1_000, ra=1_000_000, ra_open=True)
        rules.overflow_ma(acc, rules.BHS, rules.FRS)
        self.assertEqual((acc.ma, acc.ra, acc.sa), (rules.BHS, 1_001_000, 0))

    def test_the_bhs_stops_growing_at_65(self):
        g = Decimal("0.035")
        self.assertEqual(rules.bhs(2026, BIRTH, g), rules.BHS)
        self.assertEqual(rules.bhs(2061, BIRTH, g), rules.bhs(2070, BIRTH, g))
        self.assertGreater(rules.bhs(2061, BIRTH, g), rules.bhs(2060, BIRTH, g))


class Interest(unittest.TestCase):
    def test_extra_interest_below_55_counts_at_most_20000_of_oa_on_60000(self):
        # OA 30,000 counts as 20,000; with SA 10,000 and MA 50,000 the 60,000 is full
        self.assertEqual(rules.extra_interest(3_000_000, 1_000_000, 5_000_000, 0, False),
                         Decimal(6_000_000) * Decimal("0.01") / 12)
        # OA 30,000, SA 5,000, MA 10,000: 35,000 earns it
        self.assertEqual(rules.extra_interest(3_000_000, 500_000, 1_000_000, 0, False),
                         Decimal(3_500_000) * Decimal("0.01") / 12)

    def test_extra_interest_from_55_is_2_then_1_per_cent(self):
        # RA 40,000, OA 5,000, MA 25,000: 60,000 counts, 30,000 at 2 and 30,000 at 1
        self.assertEqual(rules.extra_interest(500_000, 0, 2_500_000, 4_000_000, True),
                         (Decimal(3_000_000) * Decimal("0.02") + Decimal(3_000_000) * Decimal("0.01")) / 12)
        # RA 20,000 alone: all of it at 2
        self.assertEqual(rules.extra_interest(0, 0, 0, 2_000_000, True),
                         Decimal(2_000_000) * Decimal("0.02") / 12)

    def test_extra_interest_goes_to_the_sa_and_all_of_it_lands_in_december(self):
        acc = accounts(oa=3_000_000, sa=1_000_000, ma=2_000_000)
        for m in ("2027-01", "2027-02", "2027-03", "2027-04", "2027-05", "2027-06",
                  "2027-07", "2027-08", "2027-09", "2027-10", "2027-11"):
            rules.step(acc, m, 0, BIRTH, Decimal("0.035"))
        self.assertEqual((acc.oa, acc.sa, acc.ma), (3_000_000, 1_000_000, 2_000_000))
        rules.step(acc, "2027-12", 0, BIRTH, Decimal("0.035"))
        # a year: OA at 2.5, SA and MA at 4, and 1 per cent on 20,000 + 10,000 + 20,000 into the SA.
        # G20: each month's interest rounds to the cent once, half up: the MA earns
        # S$66.67 (66.666...) a month, the SA S$33.33 (33.333...) and the extra
        # interest S$41.67, so the year credits twelve of each, not the exact year
        self.assertEqual(acc.oa, 3_000_000 + 75_000)
        self.assertEqual(acc.ma, 2_000_000 + 12 * 6_667)
        self.assertEqual(acc.sa, 1_000_000 + 12 * 3_333 + 12 * 4_167)

    def test_worth_includes_the_interest_accrued_so_far(self):
        acc = accounts(oa=1_200_000)
        rules.step(acc, "2027-01", 0, BIRTH, Decimal("0.035"))
        self.assertEqual(acc.oa, 1_200_000)
        self.assertEqual(acc.worth("oa"), 1_200_000 + 2_500)


class TurningFiftyFive(unittest.TestCase):
    def test_the_sa_then_the_oa_fill_the_ra_up_to_the_cohort_frs(self):
        acc = accounts(oa=15_000_000, sa=10_000_000, ma=7_000_000)
        rules.open_ra(acc, 22_040_000)
        self.assertEqual((acc.ra, acc.sa, acc.oa, acc.ma), (22_040_000, 0, 2_960_000, 7_000_000))
        self.assertTrue(acc.ra_open)

    def test_sa_above_the_frs_goes_to_the_oa(self):
        acc = accounts(oa=1_000_000, sa=25_000_000)
        rules.open_ra(acc, 22_040_000)
        self.assertEqual((acc.ra, acc.sa, acc.oa), (22_040_000, 0, 1_000_000 + 2_960_000))

    def test_the_ra_opens_in_the_birthday_month_at_the_cohort_frs(self):
        acc = accounts(oa=60_000_000, sa=1_000_000, ma=1_000_000)
        g = Decimal("0.035")
        rules.step(acc, "2051-02", 0, BIRTH, g)
        self.assertFalse(acc.ra_open)
        rules.step(acc, "2051-03", 0, BIRTH, g)
        self.assertTrue(acc.ra_open)
        self.assertEqual(acc.ra, rules.frs(2051, g))
        self.assertEqual(rules.frs(2026, g), rules.FRS)

    def test_after_55_the_sa_share_of_a_contribution_goes_to_the_ra(self):
        acc = accounts(ra=1_000_000, ra_open=True)
        rules.step(acc, "2052-01", 600_000, BIRTH, Decimal("0.035"))
        oa, mid, ma = rules.contribution(600_000, "2052-01", BIRTH)
        self.assertEqual((acc.oa, acc.ra, acc.ma, acc.sa), (oa, 1_000_000 + mid, ma, 0))
        self.assertEqual(oa + mid + ma, rules.cents(Decimal(600_000) * Decimal("0.34")))


def results():
    """The invented fixtures every stat test reads, keyed by stat id."""
    latest = json.loads((FIXTURES / "latest.json").read_text())
    return {k: json.loads((FIXTURES / "results" / v).read_text())
            for k, v in latest["results"].items()}


class NetWorth(unittest.TestCase):
    def test_parts_carry_forward_from_their_last_measured_date(self):
        r = {
            "savings-runway": {"data": {"months": [{"month": "2026-01", "cash_minor": 10_000},
                                                   {"month": "2026-02", "cash_minor": 15_050},
                                                   {"month": "2026-03", "cash_minor": 17_500}]}},
            "portfolio": {"data": {"periods": [{"period_to": "2025-12-31", "value_sgd_minor": 100_000},
                                               {"period_to": "2026-02-28", "value_sgd_minor": 120_000}]}},
            "cpf": {"data": {"years": [{"as_of": "2024-12-31", "total_minor": 500_000},
                                       {"as_of": "2025-12-31", "total_minor": 700_000}]}},
        }
        out = growth.net_worth(r)
        self.assertEqual(out["parts"], ["cash", "investments", "cpf"])
        self.assertEqual([m["month"] for m in out["months"]], ["2026-01", "2026-02", "2026-03"])
        last = out["months"][-1]
        self.assertEqual((last["cash"], last["investments"], last["cpf"]), (17_500, 120_000, 700_000))
        self.assertEqual((last["cash_as_of"], last["investments_as_of"], last["cpf_as_of"]),
                         ("2026-03", "2026-02-28", "2025-12-31"))
        self.assertEqual(out["months"][0]["investments_as_of"], "2025-12-31")
        self.assertEqual(last["total"], 17_500 + 120_000 + 700_000)

    def test_it_starts_once_every_part_has_been_measured(self):
        r = {"savings-runway": {"data": {"months": [{"month": "2026-03", "cash_minor": 100}]}},
             "cpf": {"data": {"years": [{"as_of": "2016-12-31", "total_minor": 200}]}}}
        out = growth.net_worth(r)
        self.assertEqual(out["parts"], ["cash", "cpf"])
        self.assertEqual([m["month"] for m in out["months"]], ["2026-03"])

    def test_a_month_with_a_bank_missing_is_not_a_fresh_cash_measurement(self):
        r = {"savings-runway": {"data": {"months": [
            {"month": "2026-01", "cash_minor": 10_000, "complete": True},
            {"month": "2026-02", "cash_minor": 9_000, "complete": False},
            {"month": "2026-03", "cash_minor": 12_000, "complete": True}]}}}
        feb = growth.net_worth(r)["months"][1]
        self.assertEqual((feb["cash"], feb["cash_as_of"]), (10_000, "2026-01"))

    def test_nothing_measured_is_nothing_drawn(self):
        self.assertEqual(growth.net_worth({}), {"parts": [], "months": []})

    def test_the_fixtures_draw(self):
        out = growth.net_worth(results())
        self.assertTrue(out["months"])
        self.assertTrue(all(isinstance(m["total"], int) for m in out["months"]))


PLAN = {"dca": 500.0, "income": [{"from": "2026-11-01", "to": "2027-06-30", "gross": 6000.0, "cpf": True}]}
TODAY = date(2026, 10, 12)


class Projection(unittest.TestCase):
    def test_defaults_set_the_assumptions(self):
        a = growth.read_assumptions({}, PLAN)
        self.assertEqual((a["pessimistic_pct"], a["expected_pct"], a["optimistic_pct"]),
                         (Decimal(3), Decimal(6), Decimal(9)))
        self.assertEqual((a["inflation_pct"], a["salary_growth_pct"], a["sums_growth_pct"]),
                         (Decimal("2.5"), Decimal(0), Decimal("3.5")))
        self.assertEqual(a["spend_growth_pct"], Decimal("2.5"))          # follows inflation
        self.assertEqual((a["horizon"], a["dollars"], a["spend"], a["milestone"]), ("10y", "today", None, None))

    def test_the_saved_settings_set_them_and_the_query_overrides(self):
        plan = {"projection": {"expected_pct": "7", "horizon": "20y", "spend": 2500.5, "milestone": 250000}}
        a = growth.read_assumptions({}, plan)
        self.assertEqual((a["expected_pct"], a["horizon"], a["spend"], a["milestone"]),
                         (Decimal(7), "20y", 250_050, 25_000_000))
        self.assertEqual(growth.read_assumptions({"horizon": "10y"}, plan)["horizon"], "10y")

    def test_nonsense_is_refused(self):
        for bad in ({"expected_pct": "six"}, {"inflation_pct": "99"}, {"horizon": "40y"},
                    {"dollars": "euros"}, {"pessimistic_pct": "8", "expected_pct": "6"}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                growth.read_assumptions(bad, PLAN)

    def test_tiger_compounds_monthly_with_the_investing_step_on_top(self):
        r = {"portfolio": {"data": {"summary": {"statements": 1, "as_of": "2026-08-31",
                                                "value_sgd_minor": 1_000_000,
                                                "contributed_sgd_minor": 800_000}}}}
        a = growth.read_assumptions({"pessimistic_pct": "0", "expected_pct": "12", "optimistic_pct": "12",
                                     "dollars": "future"}, {})
        invest, _ = growth.investing({"investing": [{"from": None, "amount": 100}]})
        out = growth.tiger(r, a, "2026-10", growth.deflator(a, "2026-10"), invest)
        rows = out["rows"]
        self.assertEqual([x["month"] for x in rows], ["2026-08", "2026-09", "2026-10"])
        self.assertEqual(rows[0]["expected"], 1_000_000)
        self.assertEqual(rows[1]["expected"], 1_000_000 + 10_000 + 10_000)      # 1 per cent, then S$100
        self.assertEqual(rows[2]["expected"], 1_020_000 + 10_200 + 10_000)
        self.assertEqual(rows[2]["pessimistic"], 1_020_000)
        self.assertEqual([x["put_in"] for x in rows], [800_000, 810_000, 820_000])

    def test_the_broker_is_named_from_the_loaded_statement_folder_never_a_literal(self):
        summary = {"statements": 1, "as_of": "2026-08-31", "value_sgd_minor": 1_000_000,
                   "contributed_sgd_minor": 800_000}
        a = growth.read_assumptions({}, {})
        invest, _ = growth.investing({})
        for folder, name in (("statements/tiger", "Tiger Brokers"), ("statements/some-new-broker", None), (None, None)):
            with self.subTest(folder=folder):
                r = {"portfolio": {"params": {"folder": folder}, "data": {"summary": summary}}}
                out = growth.tiger(r, a, "2026-10", growth.deflator(a, "2026-10"), invest)
                self.assertEqual(out["broker"], name)
        # no statement, so no name: the part starts from nothing and holds the investing steps
        invest, _ = growth.investing({"investing": [{"from": None, "amount": 100}]})
        out = growth.tiger({"portfolio": {"params": {"folder": "statements/tiger"}, "data": {}}}, a, "2026-10",
                           growth.deflator(a, "2026-10"), invest, unmeasured_from="2026-08")
        self.assertIsNone(out["broker"])

    def test_todays_dollars_take_inflation_out_after_this_month(self):
        a = growth.read_assumptions({"inflation_pct": "10"}, {})
        real = growth.deflator(a, "2026-10")
        self.assertEqual(real(110_000, "2026-09"), 110_000)
        self.assertEqual(real(110_000, "2026-10"), 110_000)
        self.assertEqual(real(110_000, "2027-10"), 100_000)

    def test_to_65_needs_a_date_of_birth(self):
        with self.assertRaises(ValueError):
            growth.project(results(), PLAN, None, {"horizon": "65"}, today=TODAY)

    def test_no_date_of_birth_still_draws_tiger_and_asks_for_it_for_cpf(self):
        out = growth.project(results(), PLAN, None, {}, today=TODAY)
        self.assertTrue(out["tiger"]["rows"])
        self.assertEqual(out["cpf"]["needs"], "birth_date")
        self.assertEqual(out["cpf"]["rows"], [])
        self.assertEqual(out["markers"], [])
        self.assertEqual(out["total"]["parts"], ["cash", "tiger"])

    def test_cpf_runs_from_the_statement_to_65_with_the_markers(self):
        out = growth.project(results(), PLAN, BIRTH.isoformat(), {"horizon": "65", "dollars": "future"},
                             today=TODAY)
        rows = out["cpf"]["rows"]
        self.assertEqual(rows[0]["month"], "2025-12")
        self.assertEqual(rows[-1]["month"], "2061-03")
        self.assertEqual(out["end"], "2061-03")
        self.assertEqual([m["month"] for m in out["markers"]], ["2051-03", "2061-03"])
        before, at = (next(r for r in rows if r["month"] == m) for m in ("2051-02", "2051-03"))
        self.assertIsNone(before["ra"])
        self.assertIsNone(at["sa"])
        self.assertGreater(at["ra"], 0)
        self.assertEqual(out["cpf"]["frs"]["year"], 2051)
        self.assertTrue(out["cpf"]["frs"]["assumed"])
        self.assertTrue(all(r["total"] == r["oa"] + (r["sa"] or 0) + (r["ra"] or 0) + r["ma"] for r in rows))
        self.assertGreater(rows[-1]["total"], rows[0]["total"])
        # the cohort FRS rides beside the RA from 55, nominal in future dollars
        self.assertIsNone(before["frs"])
        self.assertEqual(at["frs"], rules.frs(2051, Decimal("0.035")))
        self.assertEqual(rows[-1]["frs"], at["frs"])

    def test_in_todays_dollars_the_cohort_frs_falls_with_inflation(self):
        out = growth.project(results(), PLAN, BIRTH.isoformat(), {"horizon": "65"}, today=TODAY)
        rows = [r for r in out["cpf"]["rows"] if r["frs"] is not None]
        self.assertGreater(rows[0]["frs"], rows[-1]["frs"])

    def test_a_statement_from_55_on_is_not_projected(self):
        # the fixture's statement is at the end of 2025: a member who turned 55 in 2025
        out = growth.project(results(), PLAN, "1970-06-01", {}, today=TODAY)
        self.assertEqual(out["cpf"]["needs"], "before_55")
        self.assertEqual(out["cpf"]["rows"], [])
        self.assertTrue(out["tiger"]["rows"])

    def test_a_ten_year_horizon_ends_ten_years_from_this_month(self):
        out = growth.project(results(), PLAN, BIRTH.isoformat(), {}, today=TODAY)
        self.assertEqual(out["end"], "2036-10")
        self.assertEqual(out["tiger"]["rows"][-1]["month"], "2036-10")
        self.assertEqual(out["cpf"]["rows"][-1]["month"], "2036-10")
        self.assertEqual(out["total"]["rows"][-1]["month"], "2036-10")
        self.assertEqual(out["total"]["rows"][0]["month"], "2026-10")


class Endpoints(unittest.TestCase):
    """GET /api/net-worth and /api/growth over a temporary data folder holding
    a copy of the fixtures as its cache."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        shutil.copytree(FIXTURES, root / "cache")
        env = mock.patch.dict(os.environ, {"KOPIFUND_DATA": str(root), "KOPIFUND_DB": str(root / "kopifund.db"),
                                           "KOPIFUND_CACHE": str(root / "cache")})
        env.start()
        self.addCleanup(env.stop)
        self.root = root

    def test_net_worth(self):
        out = serve.answer("/api/net-worth", {})
        self.assertEqual(out["parts"], ["cash", "investments", "cpf"])

    def test_growth_without_a_record_or_a_date_of_birth(self):
        out = serve.answer("/api/growth", {})
        self.assertEqual(out["assumptions"]["investing_now"], 0)
        self.assertEqual(out["inputs"]["income"], [])
        self.assertIsNone(out["spend_hint"])
        self.assertEqual(out["cpf"]["needs"], "birth_date")
        self.assertFalse((self.root / "kopifund.db").exists())

    def test_growth_reads_the_date_of_birth_from_the_profile(self):
        (self.root / "profile.json").write_text(json.dumps({"birth_date": BIRTH.isoformat()}))
        out = serve.answer("/api/growth", {"horizon": "20y"})
        self.assertEqual(out["birth_date"], BIRTH.isoformat())
        self.assertTrue(out["cpf"]["rows"])

    def test_a_bad_assumption_is_a_400(self):
        server = serve.make_server(0, self.root)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        url = f"http://127.0.0.1:{server.server_address[1]}/api/growth?expected_pct=lots"
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(url)
        with caught.exception as reply:
            self.assertEqual(reply.code, 400)
            self.assertIn("expected return", json.loads(reply.read())["error"])


if __name__ == "__main__":
    unittest.main()
