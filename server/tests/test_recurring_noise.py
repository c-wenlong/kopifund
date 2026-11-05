"""Audit: the recurring detector against seeded, made-up charge streams at one payee
outside the categories one buys in. Purchases on random days at random amounts (spread
evenly, spread on a log scale, or from a short price list) must make no series, however
many there are; real bills (monthly, fortnightly, weekly, yearly, a price that drifts, a
skipped month, a price rise) must still make exactly one each, alone and among those
purchases. Each history is checked for what must hold whatever its shape."""
import isolate  # noqa: F401  first: no test writes the real record or cache
import random
import unittest
from datetime import date, timedelta

from analytics.stats import recurring_detector as rd
from finance import cadence, recurring

START = date(2024, 1, 1)
DAYS = 730                       # two years of record
END = START + timedelta(days=DAYS - 1)
SEEDS = 12
PAYEE = "Bigshop"                # invented throughout


def row(day, cents, i, account="A", category="Shopping"):
    return {"key": f"k{i}-{day}", "date": day.isoformat(), "amount_sgd_minor": -cents, "payee": PAYEE,
            "category": category, "bucket": "Spend", "need_class": "Want", "account_no": account}


def purchases(rnd, count, kind):
    """`count` purchases on random days of the two years, at amounts that are
    `uniform` (S$15 to S$300), `log` (S$12.60 to S$500 on a log scale) or `prices`
    (one of six shelf prices)."""
    shelf = [1290, 1590, 2490, 3990, 5990, 8990]
    amount = {"uniform": lambda: rnd.randrange(1500, 30000),
              "log": lambda: int(10 ** rnd.uniform(3.1, 4.7)),
              "prices": lambda: rnd.choice(shelf)}[kind]
    return [row(START + timedelta(days=rnd.randrange(DAYS)), amount(), i) for i in range(count)]


def month_day(i, day):
    y, m = divmod(i, 12)
    return date(y, m + 1, day)


def bill(rnd, shape):
    """(rows, cadence, usual cents) of one real bill that runs to the end of the record."""
    cents = rnd.choice([1599, 2490, 4800, 9900, 15000])
    if shape in ("weekly", "fortnightly"):
        every = 7 if shape == "weekly" else 14
        first = END - timedelta(days=every * rnd.randint(12, 40))
        days = [first + timedelta(days=every * k) for k in range((END - first).days // every + 1)]
    elif shape == "yearly":
        day = rnd.randint(1, 28)
        days = [d for d in (date(y, END.month, day) for y in range(END.year - 3, END.year + 1)) if d <= END]
    else:
        day, last = rnd.randint(1, 28), END.year * 12 + END.month - 1
        count = rnd.randint(8, 22)
        days = [month_day(m, day) for m in range(last - count + 1, last + 1)]
        days = [d if d <= END else d - timedelta(days=31) for d in days]
        days = sorted(set(days))
        if shape == "skipped":
            del days[rnd.randint(2, len(days) - 3)]
        # a bank moves a debit to the next working day: a day or two late now and then
        days = [d + timedelta(days=rnd.choice([0, 0, 0, 1, 2])) for d in days]
        days = [min(d, END) for d in days]
    amounts = [cents] * len(days)
    if shape == "drift":
        # a bill whose amount moves a little every time (a utility)
        amounts = [round(cents * rnd.uniform(0.85, 1.15)) for _ in days]
    if shape == "rise":
        cut = rnd.randint(3, len(days) - 3)
        amounts = [cents] * cut + [round(cents * 1.4)] * (len(days) - cut)
        cents = amounts[-1]
    cad = {"weekly": "weekly", "fortnightly": "fortnightly", "yearly": "yearly"}.get(shape, "monthly")
    return [row(d, a, f"b{i}", category="Subscriptions") for i, (d, a) in enumerate(zip(days, amounts))], cad, cents


def detect(rows):
    return cadence.detect_group("payee", PAYEE, rows, False, set(cadence.TIGHT_CATEGORIES), {}, END.isoformat())


class RandomPurchases(unittest.TestCase):
    """Purchases at one payee on random days are no bill, at any density."""

    def test_random_purchases_make_no_series(self):
        found = []
        for kind in ("uniform", "log", "prices"):
            for count in (50, 100, 150, 300):
                for seed in range(SEEDS):
                    rnd = random.Random(f"{kind}-{count}-{seed}")
                    for s in detect(purchases(rnd, count, kind)):
                        found.append((kind, count, seed, s["cadence"], s["status"], s["occurrences"],
                                      s["median_amount_minor"]))
        self.assertEqual(found, [])

    def test_the_audit_probe_finds_nothing(self):
        # the audit's own case: 150 purchases, S$15 to S$300, 20 seeds; seed 10 once read as
        # a weekly bill of S$112.83 that added S$490.65 a month to the fixed cost
        found = []
        for seed in range(20):
            random.seed(seed)
            rows = [{"key": f"k{i}", "date": (START + timedelta(days=random.randrange(DAYS))).isoformat(),
                     "amount_sgd_minor": -random.randrange(1500, 30000), "payee": "P", "category": "Shopping",
                     "bucket": "Spend", "need_class": "Want", "account_no": "A"} for i in range(150)]
            out = cadence.detect_group("payee", "P", rows, False, set(cadence.TIGHT_CATEGORIES), {}, END.isoformat())
            found += [(seed, s["cadence"], s["status"]) for s in out]
        self.assertEqual(found, [])


class DroppedRuns(unittest.TestCase):

    def test_a_run_under_the_floor_does_not_hide_the_purchases_beside_it(self):
        """Greptile on #54: S$100 purchases a month or so apart drift off any day of the
        month; S$1 charges between them make the payee crowded. The S$1 run is under the
        floor, so it is not another series, and the S$100 run is purchases."""
        days = [date(2025, 1, 1), date(2025, 2, 5), date(2025, 3, 10)]
        small = [date(2025, 1, 15), date(2025, 2, 19), date(2025, 3, 24)]
        rows = [row(d, 10000, f"a{i}") for i, d in enumerate(days)] + [row(d, 100, f"s{i}") for i, d in enumerate(small)]
        out = cadence.detect_group("payee", PAYEE, rows, False, set(cadence.TIGHT_CATEGORIES), {}, "2025-03-31")
        self.assertEqual([(s["cadence"], s["median_amount_minor"]) for s in out], [])


class RealBills(unittest.TestCase):
    """A real bill is found, alone or among a busy payee's purchases."""

    SHAPES = ("monthly", "skipped", "drift", "rise", "fortnightly", "weekly", "yearly")

    def check(self, shape, seed, noise):
        rnd = random.Random(f"{shape}-{seed}-{noise}")
        rows, cad, cents = bill(rnd, shape)
        if noise:
            # purchases at other amounts than the bill's: one within a quarter of its price
            # breaks its level's spacing, and one within twice or half of it can be read as a
            # price change (cadence.PRICE_CHANGE), which no detector of levels can see past
            # (drift moves the amount, so only a quiet payee is fair to it)
            prices = {-r["amount_sgd_minor"] for r in rows}
            rows += [r for r in purchases(rnd, noise, rnd.choice(["uniform", "log", "prices"]))
                     if all(not 1 / cadence.PRICE_CHANGE <= -r["amount_sgd_minor"] / p <= cadence.PRICE_CHANGE
                            for p in prices)]
        # one series, the bill's, and nothing beside it that counts: a bill alone is the
        # only series, and among purchases nothing else is live. Three purchases at one
        # shelf price on one day of three months in a row read exactly as a bill that
        # ended, so a stopped series there (no cost, in no total) is allowed
        found = detect(rows)
        mine = [s for s in found if s["cadence"] == cad and s["status"] == "active"]
        others = [s for s in found if s not in mine and (not noise or s["status"] != "stopped")]
        self.assertEqual((len(mine), len(others)), (1, 0),
                         (shape, seed, noise, [(s["cadence"], s["status"], s["median_amount_minor"]) for s in found]))
        low, high = (0.85 * cents, 1.15 * cents) if shape == "drift" else (0.99 * cents, 1.01 * cents)
        self.assertTrue(low <= mine[0]["median_amount_minor"] <= high, (shape, seed, mine[0]["median_amount_minor"], cents))

    def test_a_bill_alone_is_one_series(self):
        failed = []
        for shape in self.SHAPES:
            for seed in range(SEEDS):
                try:
                    self.check(shape, seed, 0)
                except AssertionError as e:
                    failed.append(str(e)[:200])
        self.assertEqual(failed, [])

    def test_a_bill_among_purchases_is_one_series(self):
        failed = []
        for shape in self.SHAPES:
            if shape == "drift":
                continue
            for seed in range(SEEDS):
                try:
                    self.check(shape, seed, (40, 100)[seed % 2])
                except AssertionError as e:
                    failed.append(str(e)[:200])
        self.assertEqual(failed, [])


class ClosedAccount(unittest.TestCase):
    """A bill on a card that closed stops with the card's statements; one on a card
    whose statements only lag a month or two does not."""

    def snapshot(self, card_ends):
        bills = [row(month_day(2024 * 12 + k, 10), 6000, f"g{k}", account="C") for k in range(22)
                 if month_day(2024 * 12 + k, 10).isoformat() <= card_ends]
        other = [row(month_day(2024 * 12 + k, 3), 2000, f"o{k}", account="B") | {"payee": "Grocer"}
                 for k in range(22)]
        tx = [{**r, "month": r["date"][:7], "flow_class": "External", "synthetic": False, "currency": "SGD",
               "amount_minor": r["amount_sgd_minor"], "merchant_raw": r["payee"].upper(), "counterparty": "",
               "memo": "", "bank_txn_type": "POS"} for r in bills + other]
        return {"meta": {}, "transactions": sorted(tx, key=lambda r: r["date"]), "categories": [],
                "payees": [{"name": p, "recurring": False} for p in (PAYEE, "Grocer")],
                "periods": [{"account_no": "C", "period_end": card_ends}, {"account_no": "B", "period_end": "2025-10-31"}]}

    def series(self, card_ends):
        return [s for s in rd.run(self.snapshot(card_ends))["data"]["series"] if s["payee"] == PAYEE]

    def test_a_bill_on_a_closed_card_stops(self):
        # the card's last statement is March 2025; the record runs to October
        [s] = self.series("2025-03-31")
        self.assertEqual((s["status"], s["last_seen"], s["as_of"]), ("stopped", "2025-03-10", "2025-10-03"))

    def test_a_bill_on_a_card_a_statement_behind_is_still_active(self):
        [s] = self.series("2025-08-31")
        self.assertEqual((s["status"], s["as_of"]), ("active", "2025-08-31"))

    def test_judged_for_reads_a_closed_account_on_the_record_day(self):
        pay = recurring.Payment(date(2025, 3, 10), 6000, "", True, account="C")
        ref = date(2025, 10, 3)
        self.assertEqual(recurring.judged_for([pay], {"C": date(2025, 3, 31)}, date(2026, 1, 1), ref), ref)
        self.assertEqual(recurring.judged_for([pay], {"C": date(2025, 8, 31)}, date(2026, 1, 1), ref), date(2025, 8, 31))


if __name__ == "__main__":
    unittest.main()
