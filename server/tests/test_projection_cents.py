"""G20 line 5: forward-view and the Net worth projection count in cents.

The forward view used to project in float dollars, rounding each figure with
`round(x, 2)`. It now projects in integer cents. The old float run is kept here,
as it was, as the reference: over random plans the cents run must agree with it
to the cent wherever no rounding is involved, and where a CPF share is rounded
the two may differ by one cent in that month. The only drift is those cents
carried down the cash line: nothing compounds in the forward view (cash is the
running sum of each month's net), so after n months the cash can be at most n
cents away, and the gap is exactly the sum of the months' CPF differences.

The Net worth projection already counted in cents (G14). Its growth and
interest round to the cent once a month, half up as `to_cents` does: the
brokerage's monthly return on the month's opening balance, and each CPF
account's monthly interest, accrued as whole cents and credited in December.

Every pay figure, balance, rate and date here is invented.
"""
import isolate  # noqa: F401  first: no test writes the real record or cache
import calendar
import contextlib
import random
import unittest
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from unittest import mock

from analytics import growth, income
from analytics.stats import common as c
from analytics.stats import forward_view as fv
from analytics_fixture import settings
from finance import cpf_rules as rules
from finance.money import from_cents, to_cents

FIXTURE_CONFIG = Path(__file__).resolve().parent / "fixtures" / "config.toml"
BIRTH = date(1990, 5, 20)


# ------------------------------------------------------------ the old float run, as it was

def old_pay_for(p):
    return income.Pay(income.rows({"income": p.get("income_segments") or []}),
                      p.get("salary_growth_pct") or "0")


def old_investing_at(p, month):
    if not p["dca"]:
        return 0.0
    steps = p.get("investing_steps")
    if not steps:
        return p["dca_amount"]
    cents = 0
    for st in sorted(steps, key=lambda x: x["from"] or ""):
        if st["from"] is None or st["from"] <= month:
            cents = st["amount_minor"]
    return cents / 100


def old_month_trip_cost(month, trips, per_day_default):
    y, m = int(month[:4]), int(month[5:7])
    first, last = date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1])
    total, parts = 0.0, []
    for t in trips or ():
        a = max(first, date.fromisoformat(t["from"][:10]))
        b = min(last, date.fromisoformat(t["to"][:10]))
        days = (b - a).days + 1 if a <= b else 0
        rate = t.get("per_day") if t.get("per_day") is not None else per_day_default
        cost = round(days * rate, 2)
        if t.get("flights_month", t["from"][:7]) == month:
            cost = round(cost + float(t.get("flights") or 0.0), 2)
        if cost:
            total += cost
            parts.append((t["name"], days, cost))
    return round(total, 2), parts


def old_month_one_offs(month, one_offs):
    parts = [(o.get("label", "one-off"), round(float(o["amount"]), 2))
             for o in one_offs or () if str(o["month"])[:7] == month]
    return round(sum(a for _, a in parts), 2), parts


def old_cpf_share(gross, rate, ceiling):
    return round(min(gross, ceiling) * rate, 2)


def old_project(p, start_month, cash):
    """forward_view.project before G20 line 5, in float dollars."""
    months, accrued = [], 0.0
    if not start_month or not p.get("to"):
        return months
    pay = old_pay_for(p)
    for m in c.months_between(c.add_months(start_month, 1), p["to"]):
        pm = pay.at(m)
        gross, ow, aw = pm["income"] / 100, pm["ow"] / 100, pm["aw_liable"] / 100
        cpf = round(old_cpf_share(ow, p["cpf_employee_rate"], p["cpf_ow_ceiling"])
                    + aw * p["cpf_employee_rate"], 2)
        employer = round(old_cpf_share(ow, p.get("cpf_employer_rate", 0.0), p["cpf_ow_ceiling"])
                         + aw * p.get("cpf_employer_rate", 0.0), 2)
        cpf_total = round(cpf + employer, 2)
        accrued = round(accrued + cpf_total, 2)
        take_home = round(gross - cpf, 2)
        investing = old_investing_at(p, m)
        trips, _ = old_month_trip_cost(m, p.get("trips"), p.get("trip_per_day", 0.0))
        one_offs, _ = old_month_one_offs(m, p.get("one_offs"))
        net = round(take_home + p["other_income"] - p["spend"] - trips - one_offs - investing
                    - p["insurance_premium"] - p["other_outflow"] - p["repayments"], 2)
        cash = round(cash + net, 2)
        months.append({"month": m, "gross": gross, "cpf_employee": cpf, "cpf_employer": employer,
                       "cpf_total": cpf_total, "cpf_accrued": accrued, "take_home": take_home,
                       "other_income": p["other_income"], "spend": p["spend"], "trips": trips,
                       "one_offs": one_offs, "investing": investing, "insurance": p["insurance_premium"],
                       "other_outflow": p["other_outflow"], "repayments": p["repayments"],
                       "net": net, "cash": cash})
    return months


# ------------------------------------------------------------ random plans

def dollars(cents):
    return float(from_cents(cents))


def random_day(rng, first, last):
    a, b = date.fromisoformat(first).toordinal(), date.fromisoformat(last).toordinal()
    return date.fromordinal(rng.randint(a, b)).isoformat()


def random_plan(rng):
    """(start month, starting cash in cents, the plan in the new cents shape, the
    same plan in the old dollar shape)."""
    start = f"{rng.randint(2025, 2027)}-{rng.randint(1, 12):02d}"
    end = c.add_months(start, rng.randint(24, 48))
    lo, hi = f"{c.add_months(start, -6)}-01", f"{end}-28"
    segs = []
    for i in range(rng.randint(0, 3)):
        kind = rng.choice(("employment", "employment", "side"))
        a = random_day(rng, lo, hi)
        b = rng.choice((None, random_day(rng, a, hi)))
        bonus = rng.choice((None, None, "1", "1.5", "2.25", "0.5")) if kind == "employment" else None
        segs.append({"name": f"row {i}", "type": kind, "from": a, "to": b,
                     "gross_minor": rng.randint(0, 1_600_000), "cpf": rng.random() < 0.7 if kind == "side" else
                     rng.random() < 0.9, "bonus_months": bonus,
                     "bonus_month": rng.randint(1, 12) if bonus else None})
    steps = [{"from": rng.choice((None, c.add_months(start, rng.randint(-3, 30)))),
              "amount_minor": rng.randint(0, 300_000)} for _ in range(rng.randint(0, 3))]
    trips = []
    for i in range(rng.randint(0, 3)):
        a = random_day(rng, f"{start}-01", hi)
        b = random_day(rng, a, min(hi, date.fromordinal(date.fromisoformat(a).toordinal() + 90).isoformat()))
        trips.append({"name": f"trip {i}", "from": a, "to": b,
                      "per_day_minor": rng.choice((None, rng.randint(0, 40_000))),
                      "flights_minor": rng.choice((0, rng.randint(0, 250_000))),
                      "flights_month": rng.choice((a[:7], c.add_months(a[:7], -2))), "note": ""})
    one_offs = [{"month": c.add_months(start, rng.randint(1, 30)), "amount_minor": rng.randint(-50_000, 900_000),
                 "label": f"one-off {i}"} for i in range(rng.randint(0, 3))]
    money = {k: rng.choice((0, rng.randint(0, 400_000))) for k in
             ("other_income", "insurance_premium", "other_outflow", "repayments")}
    new = {**fv.DEFAULTS, "to": end, "income_segments": segs,
           "salary_growth_pct": rng.choice(("0", "3", "2.5")),
           "cpf_employee_rate": rng.choice((0.20, 0.20, 0.17, 0.075, 0.125, 0.05)),
           "cpf_employer_rate": rng.choice((0.17, 0.17, 0.13, 0.155, 0.075, 0.0)),
           "investing_steps": steps, "dca": any(s["amount_minor"] > 0 for s in steps),
           "dca_amount_minor": steps[0]["amount_minor"] if steps else 0,
           "spend_minor": rng.randint(0, 600_000), "trip_per_day_minor": rng.randint(0, 30_000),
           "trips": trips, "one_offs": one_offs, **{f"{k}_minor": v for k, v in money.items()}}
    old = {"to": end, "salary_growth_pct": new["salary_growth_pct"],
           "income_segments": [{**{k: v for k, v in s.items() if k != "gross_minor"},
                                "gross": dollars(s["gross_minor"]), "note": s["name"]} for s in segs],
           "cpf_employee_rate": new["cpf_employee_rate"], "cpf_employer_rate": new["cpf_employer_rate"],
           "cpf_ow_ceiling": 8000.0, "investing_steps": steps, "dca": new["dca"],
           "dca_amount": new["dca_amount_minor"] / 100, "spend": dollars(new["spend_minor"]),
           "trip_per_day": dollars(new["trip_per_day_minor"]),
           "trips": [{"name": t["name"], "from": t["from"], "to": t["to"],
                      "per_day": None if t["per_day_minor"] is None else dollars(t["per_day_minor"]),
                      "flights": dollars(t["flights_minor"]), "flights_month": t["flights_month"]} for t in trips],
           "one_offs": [{"month": o["month"], "amount": dollars(o["amount_minor"]), "label": o["label"]}
                        for o in one_offs],
           **{k: dollars(v) for k, v in money.items()}}
    return start, rng.randint(-1_000_000, 10_000_000), new, old


# a CPF share is a rate times the pay, rounded: the old run rounded the Ordinary
# Wage's share and then the bonus's, by Python's round on a float; the cents run
# rounds the two together once, half up
ROUNDED = ("cpf_employee", "cpf_employer", "cpf_total", "take_home", "net")


class TheForwardViewAgainstTheOldFloatRun(unittest.TestCase):
    RUNS = 300

    def setUp(self):
        self.rng = random.Random(2005)

    def runs(self):
        for _ in range(self.RUNS):
            start, cash, new, old = random_plan(self.rng)
            yield start, cash, fv.project(new, start, cash), old_project(old, start, dollars(cash))

    def test_the_cents_run_holds_whole_cents_and_no_dollars(self):
        for start, cash, months, _ in self.runs():
            for m in months:
                self.assertEqual({k for k in m if k in fv.MONTH_MONEY}, set(), m)
                for k in fv.MONTH_MONEY:
                    self.assertIs(type(m[f"{k}_minor"]), int, k)
                for part in m["trip_parts"]:
                    self.assertIs(type(part["cost_minor"]), int)
                for part in m["one_off_parts"]:
                    self.assertIs(type(part["amount_minor"]), int)

    def test_the_first_month_is_within_a_cent_of_the_old_run(self):
        seen = 0
        for start, cash, months, old in self.runs():
            self.assertEqual([m["month"] for m in months], [m["month"] for m in old])
            if not months:
                continue
            seen += 1
            for k in fv.MONTH_MONEY:
                with self.subTest(start=start, field=k):
                    self.assertLessEqual(abs(months[0][f"{k}_minor"] - to_cents(old[0][k])), 1)
        self.assertGreater(seen, self.RUNS // 2)

    def test_every_month_s_flows_are_exact_or_a_cent_off_where_cpf_rounds(self):
        moved = 0
        for start, cash, months, old in self.runs():
            for new_m, old_m in zip(months, old):
                for k in ("gross", "other_income", "spend", "trips", "one_offs", "investing", "insurance",
                          "other_outflow", "repayments"):
                    self.assertEqual(new_m[f"{k}_minor"], to_cents(old_m[k]), (new_m["month"], k))
                for k in ROUNDED:
                    d = new_m[f"{k}_minor"] - to_cents(old_m[k])
                    self.assertLessEqual(abs(d), 1 if k != "cpf_total" else 2, (new_m["month"], k))
                    moved += bool(d)
                # take-home is gross less the employee's CPF in both runs, so it moves only with it
                self.assertEqual(new_m["take_home_minor"], new_m["gross_minor"] - new_m["cpf_employee_minor"])
        # half a cent does come up: the comparison is not vacuous
        self.assertGreater(moved, 0)

    def test_the_drift_is_the_cpf_cents_carried_down_and_bounded_by_a_cent_a_month(self):
        largest = 0
        for start, cash, months, old in self.runs():
            self.assertGreaterEqual(len(months), 24 if months else 0)
            carried = accrued = 0
            for i, (new_m, old_m) in enumerate(zip(months, old), 1):
                carried += new_m["net_minor"] - to_cents(old_m["net"])
                accrued += new_m["cpf_total_minor"] - to_cents(old_m["cpf_total"])
                drift = new_m["cash_minor"] - to_cents(old_m["cash"])
                # nothing compounds: the gap in the cash is exactly the months' net differences
                self.assertEqual(drift, carried, new_m["month"])
                self.assertEqual(new_m["cpf_accrued_minor"] - to_cents(old_m["cpf_accrued"]), accrued)
                self.assertLessEqual(abs(drift), i)
                largest = max(largest, abs(drift))
        # over 24 to 48 months the cash does move, but by a few cents at most
        # (3 on these plans: a bonus month's two shares rounded apart, three times)
        self.assertGreater(largest, 0)
        self.assertLessEqual(largest, 48)

    def test_at_the_twenty_percent_default_nothing_moves_at_all(self):
        # 20 per cent of a whole number of cents is never half a cent, and with no
        # bonus there is one share to round, so the employee's CPF agrees exactly
        for _ in range(100):
            start, cash, new, old = random_plan(self.rng)
            for s in new["income_segments"]:
                s["bonus_months"] = s["bonus_month"] = None
            for s in old["income_segments"]:
                s["bonus_months"] = s["bonus_month"] = None
            new["cpf_employee_rate"] = old["cpf_employee_rate"] = 0.20
            for new_m, old_m in zip(fv.project(new, start, cash), old_project(old, start, dollars(cash))):
                self.assertEqual(new_m["cash_minor"], to_cents(old_m["cash"]), new_m["month"])


class TheSamePlanInBoth(unittest.TestCase):
    """Decision 29: one set of inputs, so each month's gross and investing are the
    same cents in the forward view and the Net worth projection, on random plans."""

    def setUp(self):
        stack = contextlib.ExitStack()
        stack.enter_context(settings())
        stack.enter_context(mock.patch.object(fv, "PLAN_CONFIG", FIXTURE_CONFIG))
        self.addCleanup(stack.close)
        self.rng = random.Random(29)

    def test_gross_and_investing_agree_month_by_month(self):
        today = date.today()
        this = today.strftime("%Y-%m")
        for _ in range(60):
            rows = []
            for i in range(self.rng.randint(1, 3)):
                kind = self.rng.choice(("employment", "side"))
                a = random_day(self.rng, f"{c.add_months(this, -3)}-01", f"{c.add_months(this, 20)}-28")
                bonus = self.rng.choice((None, "1", "1.5")) if kind == "employment" else None
                rows.append({"name": f"r{i}", "type": kind, "from": a,
                             "to": self.rng.choice((None, random_day(self.rng, a, f"{c.add_months(this, 30)}-28"))),
                             "gross": str(from_cents(self.rng.randint(0, 1_200_000))),
                             "cpf": kind == "employment", "bonus_months": bonus,
                             "bonus_month": self.rng.randint(1, 12) if bonus else None})
            plan = {"income": rows, "to": c.add_months(this, 24),
                    "investing": [{"from": None, "amount": str(from_cents(self.rng.randint(0, 200_000)))},
                                  {"from": c.add_months(this, self.rng.randint(1, 12)),
                                   "amount": str(from_cents(self.rng.randint(0, 200_000)))}],
                    "projection": {"salary_growth_pct": self.rng.choice(("0", "3")), "dollars": "future",
                                   "inflation_pct": "0"}}
            params = fv.params_from_plan(plan)
            months = fv.project({**fv.DEFAULTS, "spend_minor": 0, "trip_per_day_minor": 0, "other_outflow_minor": 0,
                                 **params}, this, 0)
            nw = {r["month"]: r for r in growth.project({}, plan, None, {}, today=today)["cash"]["rows"][1:]}
            for m in months:
                if m["month"] in nw:
                    self.assertEqual(m["gross_minor"], nw[m["month"]]["income"], m["month"])
                    self.assertEqual(m["investing_minor"], nw[m["month"]]["investing"], m["month"])


# ------------------------------------------------------------ the Net worth projection

def half_up(x):
    return int(Decimal(x).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def floats_in(o, path="$"):
    """Every float anywhere in `o`, with where it is."""
    if isinstance(o, float):
        return [path]
    if isinstance(o, dict):
        return [f for k, v in o.items() for f in floats_in(v, f"{path}.{k}")]
    if isinstance(o, list):
        return [f for i, v in enumerate(o) for f in floats_in(v, f"{path}[{i}]")]
    return []


class TheNetWorthProjectionInCents(unittest.TestCase):
    def setUp(self):
        self.rng = random.Random(14)

    def test_the_brokerage_grows_by_its_monthly_return_rounded_once_half_up(self):
        for _ in range(200):
            value = self.rng.randint(0, 50_000_000)
            pct = str(Decimal(self.rng.randint(-300, 1500)) / 100)
            a = growth.read_assumptions({"pessimistic_pct": min(pct, "0", key=Decimal), "expected_pct": pct,
                                         "optimistic_pct": max(pct, "15", key=Decimal), "dollars": "future"}, {})
            step = self.rng.randint(0, 300_000)
            invest, _ = growth.investing({"investing": [{"from": None, "amount": str(from_cents(step))}]})
            r = {"portfolio": {"data": {"summary": {"statements": 1, "as_of": "2026-01-31",
                                                    "value_sgd_minor": value, "contributed_sgd_minor": 0}}}}
            rows = growth.tiger(r, a, "2028-12", growth.deflator(a, "2026-01"), invest)["rows"]
            want = value
            rate = Decimal(pct) / 100 / 12
            for prev, row in zip(rows, rows[1:]):
                self.assertIs(type(row["expected"]), int)
                want = want + half_up(Decimal(want) * rate) + step
                self.assertEqual(row["expected"], want, row["month"])
                self.assertEqual(prev["expected"] + half_up(Decimal(prev["expected"]) * rate) + step,
                                 row["expected"])

    def test_each_cpf_account_s_monthly_interest_rounds_to_the_cent_once(self):
        # OA 12,000.60 earns 2.5 per cent: S$25.00125 a month, so S$25.00 each month
        # and S$300.00 credited in December, never 12 times the fraction (S$300.02)
        acc = rules.Accounts(oa=1_200_060, sa=0, ma=0)
        for mo in range(1, 13):
            rules.step(acc, f"2027-{mo:02d}", 0, BIRTH, Decimal("0.035"))
            for k, v in acc.accrued.items():
                self.assertEqual(v, int(v), (mo, k))
        self.assertEqual(acc.oa, 1_200_060 + 12 * 2_500)

    def test_a_month_s_cpf_interest_is_each_account_s_share_rounded_half_up(self):
        for _ in range(300):
            acc = rules.Accounts(oa=self.rng.randint(0, 30_000_000), sa=self.rng.randint(0, 30_000_000),
                                 ma=self.rng.randint(0, 8_000_000))
            before = dict(acc.accrued)
            opening = (acc.oa, acc.sa, acc.ma)
            rules.accrue(acc)
            extra = rules.extra_interest(*opening, 0, False)
            self.assertEqual(acc.accrued["oa"] - before["oa"], half_up(Decimal(opening[0]) * rules.OA_RATE / 12))
            self.assertEqual(acc.accrued["ma"] - before["ma"], half_up(Decimal(opening[2]) * rules.SMRA_RATE / 12))
            self.assertEqual(acc.accrued["sa"] - before["sa"],
                             half_up(Decimal(opening[1]) * rules.SMRA_RATE / 12) + half_up(extra))

    def test_no_float_anywhere_in_the_answer(self):
        results = {"savings-runway": {"data": {"months": [{"month": "2026-09", "cash_minor": 1_234_567,
                                                           "complete": True}]}},
                   "portfolio": {"data": {"summary": {"statements": 1, "as_of": "2026-08-31",
                                                      "value_sgd_minor": 2_345_678, "contributed_sgd_minor": 2_000_000}}},
                   "cpf": {"data": {"summary": {"as_of": "2025-12-31", "oa_minor": 3_456_789, "sa_minor": 1_234_501,
                                                "ma_minor": 2_345_603}}}}
        for _ in range(20):
            plan = {"income": [{"name": "Job", "type": "employment", "from": "2026-01-01", "to": None,
                                "gross": str(from_cents(self.rng.randint(100_000, 1_500_000))), "cpf": True,
                                "bonus_months": "1.5", "bonus_month": 12},
                               {"name": "Side", "type": "side", "from": "2026-06-01", "to": "2029-01-31",
                                "gross": str(from_cents(self.rng.randint(0, 400_000))), "cpf": False}],
                    "investing": [{"from": None, "amount": str(from_cents(self.rng.randint(0, 300_000)))}],
                    "projection": {"spend": str(from_cents(self.rng.randint(0, 400_000))),
                                   "dollars": self.rng.choice(("today", "future")), "horizon": "30y"}}
            out = growth.project(results, plan, "1990-05-20", {}, today=date(2026, 10, 18),
                                 spend_hint={"per_month": 250_050, "from": "2026-01", "to": "2026-09", "months": 9})
            self.assertEqual(floats_in(out), [])


if __name__ == "__main__":
    unittest.main()
