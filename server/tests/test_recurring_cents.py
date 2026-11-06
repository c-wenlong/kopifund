"""G20: the recurring detector in cents finds what it found in dollars.

server/tests/fixtures/recurring/cents_golden.json was written by the dollar engine
(origin/main before G20) over every input the detector and Recurring tests hand
it, and over the seeded histories of server/tests/test_recurring_properties.py. Each is
run again here: the same series ids, names, statuses, cadences, occurrences and
dates, and each usual amount and monthly cost within a cent of the old dollars.
Then a few properties the cents engine must keep whatever the rows: a result in
cents only and the monthly cost to the cent of the dollar formula. Invented rows throughout.

G20 line 6: the readers take the cents result only; the same result in
dollars, as one from before G20, is no result (server/finance/money.py `in_cents`),
so the results cache never hands it to them."""
import isolate  # noqa: F401  first: no test writes the real record or cache
import json
import random
import re
import unittest
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from analytics.stats import recurring_detector as rd
from finance.money import label, to_cents
from support import snapshot_txn

GOLDEN = json.loads((Path(__file__).parent / "fixtures" / "recurring" / "cents_golden.json").read_text())
EXACT = ("id", "status", "cadence", "occurrences", "rows", "first_seen", "last_seen", "amount_varies")


def snapshot(case):
    cols = GOLDEN["row_columns"]
    rows = []
    for values in case["rows"]:
        r = dict(zip(cols, values))
        cents = r.pop("cents")
        rows.append({**r, "amount_sgd_minor": cents})
    return {"meta": {}, "transactions": rows, "categories": [],
            "payees": [{"name": n, "recurring": f} for n, f in case["payees"]],
            "periods": [{"account_no": a, "period_end": e} for a, e in case["periods"]],
            "category_aliases": case["aliases"]}


def found(data):
    return sorted(({**{k: s[k] for k in EXACT}, "name": s["name"], "median": s["median_amount_minor"],
                    "monthly": s["monthly_cost_minor"]} for s in data["series"]), key=lambda s: s["id"])


def golden(series):
    return [dict(zip(GOLDEN["series_columns"], s)) for s in series]


class SameSeries(unittest.TestCase):
    def compare(self, now, before, where):
        self.assertEqual([{k: s[k] for k in EXACT} for s in now], [{k: s[k] for k in EXACT} for s in before], where)
        for a, b in zip(now, before):
            # a median of two that ends in half a cent rounds half up now, where the float
            # rounded it either way, so the amount may move a cent, and the name that prints it
            self.assertLessEqual(abs(a["median"] - b["median"]), 1, (where, a["id"]))
            self.assertLessEqual(abs(a["monthly"] - b["monthly"]), 1, (where, a["id"]))
            self.assertEqual(a["name"].split(" (S$")[0], b["name"].split(" (S$")[0], (where, a["id"]))
            if a["name"] != b["name"]:
                self.assertEqual(a["name"], f"{b['name'].split(' (S$')[0]} ({label(a['median'])})", where)

    def test_every_input_the_detector_tests_give_finds_the_same_series(self):
        self.assertGreater(len(GOLDEN["cases"]), 100)
        for n, case in enumerate(GOLDEN["cases"]):
            with self.subTest(case=n):
                self.compare(found(rd.run(snapshot(case))["data"]), golden(case["series"]), n)

    def test_every_seeded_history_finds_the_same_series(self):
        import test_recurring_properties as props
        from test_recurring import detected
        self.assertEqual(len(GOLDEN["seeds"]), props.SEEDS)
        for seed in range(props.SEEDS):
            with self.subTest(seed=seed):
                conn, res = detected(*props.history(seed)[0])
                conn.close()
                self.compare(found(res["recurring-detector"]["data"]), golden(GOLDEN["seeds"][str(seed)]), seed)


def in_dollars(o):
    """A result as the detector wrote it before G20: every `<name>_minor` as float dollars under `<name>`."""
    if isinstance(o, list):
        return [in_dollars(x) for x in o]
    if isinstance(o, dict):
        return {(k[:-6] if k.endswith("_minor") else k): (v / 100 if k.endswith("_minor") and isinstance(v, int)
                                                           else in_dollars(v)) for k, v in o.items()}
    return o


class TheReadersTakeTheCentsResult(unittest.TestCase):
    """Recurring, a series' own view, Home's Coming up and Plan's suggestions read the
    cents result; the same result in dollars, as one from before G20, is no result."""

    def test_the_cents_result_is_read_and_its_dollar_twin_is_no_result(self):
        import test_recurring_properties as props
        from test_recurring import detected
        from finance import money, recurring, suggestions
        checked = 0
        for seed in range(0, props.SEEDS, 10):
            rows, _, end = props.history(seed)
            conn, cents = detected(*rows)
            try:
                result = cents["recurring-detector"]
                dollars = {**result, "chart": {**result["chart"], "unit": "sgd"}, "data": in_dollars(result["data"])}
                self.assertNotIn('"median_amount"', json.dumps(cents))
                self.assertIn('"median_amount"', json.dumps(dollars))
                with self.subTest(seed=seed):
                    self.assertTrue(money.in_cents(result))
                    self.assertFalse(money.in_cents(dollars))
                    page = recurring.page(conn, cents, today=end)
                    self.assertEqual(page, recurring.page(conn, cents, today=end))
                    recurring.coming_up(conn, cents, today=end)
                    for s in result["data"]["series"]:
                        self.assertEqual(recurring.series_view(conn, cents, s["id"], today=end)["id"], s["id"])
                        checked += 1
                    every = recurring.live(conn, cents, end)["active"]
                    standing = suggestions.standing(every, re.compile("."))
                    if standing:
                        self.assertIs(type(standing["per_month"]), int)
                    # with no record, suggestions read the result itself
                    suggestions.standing(suggestions.active_series(None, cents, end), re.compile("."))
            finally:
                conn.close()
        self.assertGreater(checked, 20)

    def test_an_owners_usual_amount_is_cents_in_the_series_and_its_monthly_cost(self):
        from finance import recurring
        s = {"id": "payee:X:1000", "cadence": "monthly", "median_amount_minor": 1000, "avg_amount_minor": 1000,
             "monthly_cost_minor": 1000}
        e = {"row": 1, "recurring": None, "amount": 1234, "cadence": "quarterly", "day": None, "ended_on": None}
        out = recurring._with_edit(s, e)
        self.assertEqual((out["median_amount_minor"], out["avg_amount_minor"]), (1234, 1234))
        self.assertEqual(out["monthly_cost_minor"], rd.monthly_cost_minor(1234, rd.NOMINAL["quarterly"]))
        self.assertEqual(out["detected"]["amount"], 1000)
        # and a result from before G20 loses its dollars, so no reader takes the detected figure
        old = recurring._with_edit({"id": "payee:X:1000", "cadence": "monthly", "median_amount": 10.0,
                                    "avg_amount": 10.0, "monthly_cost": 10.0}, e)
        self.assertFalse({"median_amount", "avg_amount", "monthly_cost"} & set(old))
        self.assertEqual(old["monthly_cost_minor"], out["monthly_cost_minor"])


def tx(day, cents, payee, category="Subscriptions"):
    return snapshot_txn(f"{payee}-{day}-{cents}", day, -cents, payee, category=category)


def random_rows(rnd):
    """A payee or two billed monthly at a price that may move, some odd charges beside."""
    rows, start = [], date(2025, 1, 1)
    for p in range(rnd.randint(1, 2)):
        price = rnd.randint(900, 9000)
        day = rnd.randint(1, 27)
        for m in range(rnd.randint(3, 18)):
            if rnd.random() < 0.15:
                price = max(1, round(price * rnd.choice([0.8, 1.2, 1.25, 1.5])))
            d = date(2025 + m // 12, m % 12 + 1, day) + timedelta(days=rnd.choice([0, 0, 1, -1, 2]))
            rows.append(tx(d.isoformat(), price + rnd.choice([0, 0, 0, 1, -1, 99]), f"Payee{p}"))
        for _ in range(rnd.randint(0, 4)):
            d = start + timedelta(days=rnd.randint(0, 540))
            rows.append(tx(d.isoformat(), rnd.randint(1, 30000), f"Payee{p}"))
    rows.append(tx("2026-08-31", 1200, "Club", "Shopping"))
    return rows


class Properties(unittest.TestCase):
    def setUp(self):
        self.cases = [random_rows(random.Random(seed)) for seed in range(150)]

    def run_on(self, rows):
        return rd.run({"meta": {}, "transactions": sorted(rows, key=lambda r: r["date"]), "categories": [],
                       "payees": [{"name": n, "recurring": False} for n in sorted({r["payee"] for r in rows})],
                       "periods": []})

    def test_every_money_field_is_whole_cents_and_no_dollars_are_left(self):
        for rows in self.cases:
            res = self.run_on(rows)
            for s in res["data"]["series"] + res["data"]["possible_auto_bills"]:
                for k, v in s.items():
                    self.assertFalse(k in ("avg_amount", "median_amount", "total", "monthly_cost"), k)
                    if k.endswith("_minor"):
                        self.assertIs(type(v), int, (k, v))
            for k in ("monthly_fixed_cost_minor", "monthly_committed_minor"):
                self.assertIs(type(res["data"]["totals"][k]), int)
            self.assertEqual(res["chart"]["unit"], "sgd_minor")

    def test_monthly_cost_is_the_dollar_formula_to_the_cent(self):
        for rows in self.cases:
            for s in self.run_on(rows)["data"]["series"]:
                exact = Decimal(s["median_amount_minor"]) * Decimal("30.44") / Decimal(str(s["cadence_days"]))
                self.assertLessEqual(abs(s["monthly_cost_minor"] - exact), Decimal("0.5"), s["id"])
                self.assertEqual(s["monthly_cost_minor"],
                                 rd.monthly_cost_minor(s["median_amount_minor"], s["cadence_days"]))

    def test_the_usual_amount_is_one_the_series_charged_or_between_two(self):
        for rows in self.cases:
            for s in self.run_on(rows)["data"]["series"]:
                paid = sorted(-r["amount_sgd_minor"] for r in rows if r["payee"] == s["payee"])
                self.assertTrue(paid[0] <= s["median_amount_minor"] <= paid[-1], s["id"])
                self.assertGreaterEqual(s["median_amount_minor"], rd.MIN_AMOUNT_MINOR)

    def test_the_series_id_names_the_first_charge_in_cents(self):
        for rows in self.cases:
            for s in self.run_on(rows)["data"]["series"]:
                key = s["id"].rsplit(":", 1)[-1]
                self.assertTrue(key == "all" or key.isdigit(), s["id"])

    def test_the_name_of_one_of_several_series_prints_its_amount_by_label(self):
        rows = ([tx(f"2025-{m:02d}-05", 1000, "Twofer") for m in range(1, 13)]
                + [tx(f"2025-{m:02d}-20", 4550, "Twofer") for m in range(1, 13)] + [tx("2025-12-28", 1200, "Club", "Shopping")])
        names = sorted(s["name"] for s in self.run_on(rows)["data"]["series"] if s["payee"] == "Twofer")
        self.assertEqual(names, ["Twofer (S$10.00)", "Twofer (S$45.50)"])

    def test_cents_and_to_cents_agree_on_the_floor(self):
        self.assertEqual(rd.MIN_AMOUNT_MINOR, to_cents(10.0))


if __name__ == "__main__":
    unittest.main()
