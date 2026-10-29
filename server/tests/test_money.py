"""G17: one way dollars become cents (server/finance/money.py), and the converters
that used to round their own way now all go through it."""
import isolate  # noqa: F401  first: no test writes the real record or cache
import decimal
import random
import unittest
from decimal import Decimal
from fractions import Fraction

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

    def test_thousands_commas_must_group_by_three(self):
        for text, cents in (("1,234", 123400), ("12,345.67", 1234567), ("1,234,567.891", 123456789),
                            ("-1,000", -100000), ("+5", 500), ("999", 99900), ("1234567", 123456700)):
            with self.subTest(text=text):
                self.assertEqual(money.to_cents(text), cents)
        for bad in ("1,2,3", "1,23", "12,34.56", "1234,567", ",123", "1,", "1,,234", "1,234,56", "1.234,5",
                    "1_000", "1 234.50", "1,234 .5"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                money.to_cents(bad)

    def test_text_with_no_number_rules_written_in_is_refused(self):
        """Only a plain signed decimal: no currency, no bracket or trailing sign for
        a negative, no digits outside 0 to 9, and an exponent only without commas."""
        for bad in ("$12.00", "S$12.00", "12.00 SGD", "(12.00)", "12.00-", "12.00 CR", "12.00DR", "\u221212.00",
                    "1,000e2", "1e", "e2", "1e+", "0x10", "--5", "+-5", "- 5", ".", "-", "+", "\u0661\u0662", "\uff11\uff12",
                    "12.5.0", "inf", "-Infinity", "sNaN"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                money.to_cents(bad)

    def test_short_forms_and_signed_zero(self):
        for text, cents in (("12.", 1200), (".5", 50), ("-.5", -50), ("-0", 0), ("-0.00", 0), ("+0.004", 0),
                            ("0.005", 1), ("\t7.25\n", 725)):
            with self.subTest(text=text):
                self.assertEqual(money.to_cents(text), cents)
        self.assertEqual(money.to_cents(float("-0.0")), 0)
        self.assertEqual(money.to_cents(Decimal("-0")), 0)

    def test_a_decimal_with_an_exponent_is_a_number(self):
        self.assertEqual(money.to_cents(Decimal("1E+2")), 10000)
        self.assertEqual(money.to_cents(Decimal("1.2345E+3")), 123450)
        self.assertEqual(money.to_cents(Decimal("5E-3")), 1)
        self.assertEqual(money.to_cents(Decimal("4.9999E-3")), 0)
        self.assertEqual(money.to_cents(Decimal("1E-999999")), 0)
        self.assertEqual(money.to_cents(Decimal("0E+26")), 0)          # zero, whatever its exponent
        self.assertEqual(money.to_cents("0." + "1" * 5000), 11)
        self.assertEqual(money.to_cents("0.00" + "5" * 5000), 1)
        self.assertEqual(money.to_cents(10 ** 15), 10 ** 17)
        self.assertEqual(money.to_cents(1e15), 10 ** 17)

    def test_a_decimal_s_own_text_reads_as_the_decimal(self):
        """str() of a Decimal can carry an exponent ("1E+3", "0E-7"): the plan's
        checked text is that, so it reads the same as the Decimal itself."""
        for text, cents in (("1E+3", 100000), ("1e3", 100000), ("-1.5E+2", -15000), ("0E-7", 0), ("2.5e-2", 3),
                            (".5E1", 500), ("0E+26", 0), ("-0E+999", 0)):
            with self.subTest(text=text):
                self.assertEqual(money.to_cents(text), cents)
        rng = random.Random(41)
        for _ in range(3000):
            d = Decimal(rng.randint(-10 ** 9, 10 ** 9)).scaleb(rng.randint(-12, 12))
            with self.subTest(d=d):
                self.assertEqual(money.to_cents(str(d)), money.to_cents(d))

    def test_the_plan_s_checked_amounts_all_read(self):
        from finance import planning
        plan = planning.clean_plan({"dca": "1e3", "income": [
            {"name": "Job", "type": "employment", "gross": "1e3", "from": "2026-01-01"}]})
        out = planning.plan_out(plan)
        self.assertEqual(out["dca_minor"], 100000)
        self.assertEqual(out["income"][0]["gross_minor"], 100000)

    def test_more_than_two_places_round_exactly_half_up(self):
        for text, cents in (("0.125", 13), ("0.1249999999999999999999999", 12), ("0.1250000000000000000000001", 13),
                            ("2.675", 268), ("-2.675", -268), ("-0.0049", 0), ("-0.005", -1),
                            ("1.0049999999", 100), ("99999999999999.995", 10 ** 16)):
            with self.subTest(text=text):
                self.assertEqual(money.to_cents(text), cents)

    def test_far_past_any_real_figure_is_still_exact_or_refused(self):
        """26 digits of dollars read exactly, half up; past that it is not money."""
        self.assertEqual(money.to_cents("12345678901234567890123456.785"),
                         1234567890123456789012345679)
        self.assertEqual(money.to_cents(Decimal("-12345678901234567890123456.785")),
                         -1234567890123456789012345679)
        self.assertEqual(money.to_cents(10 ** 26 - 1), 10 ** 28 - 100)
        for bad in ("1" + "0" * 26, Decimal("1E+26"), 1e26, 1e300, Decimal("1E+999999999"), 10 ** 26, -10 ** 30,
                    "1e99999999999999999999999", "1e-99999999999999999999999", "9" * 5000, "99999999999999999999999999.995"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                money.to_cents(bad)

    def test_the_answer_does_not_depend_on_the_decimal_context(self):
        with decimal.localcontext() as ctx:
            ctx.prec, ctx.rounding = 3, decimal.ROUND_FLOOR
            self.assertEqual(money.to_cents("1,234.565"), 123457)
            self.assertEqual(money.to_cents(Decimal("-2.675")), -268)
            self.assertEqual(money.to_cents(1234.565), 123457)

    def test_random_amounts_round_trip_and_round_like_exact_fractions(self):
        rng = random.Random(17)
        for _ in range(3000):
            cents = rng.randint(-10 ** 14, 10 ** 14)
            dollars = money.from_cents(cents)
            grouped = f"{dollars:,.2f}"
            with self.subTest(cents=cents):
                self.assertEqual(money.to_cents(dollars), cents)
                self.assertEqual(money.to_cents(str(dollars)), cents)
                self.assertEqual(money.to_cents(grouped), cents)
            places = rng.randint(0, 8)
            raw = rng.randint(-10 ** 12, 10 ** 12)
            text = str(Decimal(raw).scaleb(-places))
            f = Fraction(raw, 10 ** places) * 100
            want = int(abs(f) + Fraction(1, 2)) * (1 if f >= 0 else -1)
            with self.subTest(text=text):
                self.assertEqual(money.to_cents(text), want)
                self.assertEqual(money.to_cents(Decimal(text)), want)

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
