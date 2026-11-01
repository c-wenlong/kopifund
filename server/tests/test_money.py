"""G17: one way dollars become cents (server/finance/money.py), and the converters
that used to round their own way now all go through it."""
import isolate  # noqa: F401  first: no test writes the real record or cache
import unittest
from decimal import Decimal

from analytics import growth, income
from analytics.stats import forward_view
from finance import money, recurring, suggestions


class ToCentsTest(unittest.TestCase):
    def test_halves_round_up_away_from_zero(self):
        for dollars, cents in ((0.125, 13), (1.005, 101), (2.675, 268), (0.5, 50), (0.005, 1),
                               (-0.125, -13), (-1.005, -101), (-0.005, -1), (-0.004, 0)):
            with self.subTest(dollars=dollars):
                self.assertEqual(money.to_cents(dollars), cents)

    def test_a_float_is_read_as_it_prints_not_as_its_binary_value(self):
        # 0.1 + 0.2 prints 0.30000000000000004; 1.005 is 1.00499999... in binary
        self.assertEqual(money.to_cents(0.1 + 0.2), 30)
        self.assertEqual(money.to_cents(1.005), 101)
        self.assertEqual(money.to_cents(1234.56), 123456)
        self.assertEqual(money.to_cents(19.99 * 3), 5997)

    def test_ints_decimals_and_text(self):
        self.assertEqual(money.to_cents(12), 1200)
        self.assertEqual(money.to_cents(-3), -300)
        self.assertEqual(money.to_cents(Decimal("2.675")), 268)
        self.assertEqual(money.to_cents("1,234.5"), 123450)
        self.assertEqual(money.to_cents(" 6000.50 "), 600050)
        self.assertEqual(money.to_cents("-0.125"), -13)
        self.assertIsInstance(money.to_cents(1.5), int)

    def test_what_is_not_an_amount_is_refused(self):
        for bad in (True, False, None, [1], {"a": 1}):
            with self.subTest(bad=bad), self.assertRaises(TypeError):
                money.to_cents(bad)
        for bad in (float("nan"), float("inf"), float("-inf"), Decimal("NaN"), "", "abc", "NaN", "Infinity",
                    "1e999999999"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                money.to_cents(bad)

    def test_from_cents_is_exact_and_takes_only_whole_cents(self):
        self.assertEqual(money.from_cents(123456), Decimal("1234.56"))
        self.assertEqual(str(money.from_cents(-5)), "-0.05")
        self.assertEqual(money.to_cents(money.from_cents(98765)), 98765)
        for bad in (1.5, "100", True, None):
            with self.subTest(bad=bad), self.assertRaises(TypeError):
                money.from_cents(bad)


class ConvertersAgreeTest(unittest.TestCase):
    """The converters T3 named give the same cent for the same dollars. G20 line 6:
    the ones that turned a stat's float dollars into cents are gone, since a stat
    writes cents; the plan's dollars still go through `to_cents`."""

    CASES = (0.125, 1.005, 2.675, 0.1 + 0.2, 1234.565, 19.99, 0.0)

    def test_every_converter_rounds_the_same_way(self):
        for dollars in self.CASES:
            want = money.to_cents(dollars)
            with self.subTest(dollars=dollars):
                # G20: a stat's dollars are never read, only its cents
                self.assertIsNone(money.stat_cents({"x": dollars}, "x"))
                self.assertEqual(money.stat_cents({"x_minor": want}, "x"), want)
                self.assertEqual(recurring.usual_cents({"median_amount": dollars}), 0)
                self.assertEqual(recurring.usual_cents({"median_amount_minor": want}), abs(want))
                self.assertEqual(recurring.usual_cents({"median_amount_minor": -want}), abs(want))
                self.assertEqual(income.to_cents(dollars), want)

    def test_none_stays_none_where_it_did(self):
        self.assertIsNone(money.stat_cents({"x": None}, "x"))
        # a series with no usual amount has always been read as 0 cents
        self.assertEqual(recurring.usual_cents({"median_amount": None, "avg_amount": None}), 0)
        self.assertFalse(hasattr(growth, "to_cents"))
        self.assertFalse(hasattr(suggestions, "_cents"))

    def test_the_forward_view_sends_each_month_in_cents_only(self):
        # G20: it counts in cents, so a month carries its cents and no dollars beside them
        months = forward_view.project({**forward_view.DEFAULTS, "to": "2026-03", "spend_minor": 100_013,
                                       "other_outflow_minor": 0}, "2025-12", 500_000)
        self.assertEqual([m["month"] for m in months], ["2026-01", "2026-02", "2026-03"])
        for m in months:
            for k in forward_view.MONTH_MONEY:
                self.assertNotIn(k, m)
                self.assertIs(type(m[f"{k}_minor"]), int, k)
        self.assertEqual(months[0]["spend_minor"], 100_013)
        self.assertEqual([m["cash_minor"] for m in months], [399_987, 299_974, 199_961])
        self.assertEqual(forward_view.low_point(months)["cash_minor"], months[-1]["cash_minor"])


class FxMinorTest(unittest.TestCase):
    """G20: a foreign amount in SGD cents by the record's one rule."""

    def test_half_a_cent_rounds_away_from_zero(self):
        e9 = money.rate_e9(1.25)
        self.assertEqual(e9, 1_250_000_000)
        self.assertEqual(money.fx_minor(333, e9), 416)          # 4.1625
        self.assertEqual(money.fx_minor(334, e9), 418)          # 4.175, half up
        self.assertEqual(money.fx_minor(-334, e9), -418)
        self.assertEqual(money.fx_minor(0, e9), 0)

    def test_it_is_the_record_s_v_txn_rule(self):
        """The same answer as v_txn's `round(amount_minor * (fx_to_sgd_e9 / 1e9))`."""
        import random
        import sqlite3
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        rng = random.Random(20)
        for _ in range(2000):
            minor = rng.randint(-10 ** 9, 10 ** 9)
            e9 = money.rate_e9(rng.uniform(0.005, 5))
            want = conn.execute("SELECT CAST(round(? * (? / 1e9)) AS INTEGER)", (minor, e9)).fetchone()[0]
            self.assertEqual(money.fx_minor(minor, e9), want, (minor, e9))

    def test_the_record_scales_rates_with_the_same_function(self):
        from finance import db
        self.assertIs(db.rate_e9, money.rate_e9)
        self.assertIsNone(money.rate_e9(None))
        self.assertIsNone(money.rate_e9(""))

    def test_a_float_is_refused(self):
        for bad in ((12.5, 10 ** 9), (100, 1.25), (True, 10 ** 9)):
            with self.subTest(bad=bad), self.assertRaises(TypeError):
                money.fx_minor(*bad)


if __name__ == "__main__":
    unittest.main()
