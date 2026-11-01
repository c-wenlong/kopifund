"""G13: an amount in prose is S$ with thousands commas and a true minus, a foreign one with its code."""
import isolate  # noqa: F401  first: no test writes the real record or cache
import unittest

from finance import money


class MoneyLabelTest(unittest.TestCase):
    def test_singapore_dollars_group_thousands_and_use_a_true_minus(self):
        self.assertEqual(money.label(-1700), "−S$17.00")
        self.assertEqual(money.label(-123456789), "−S$1,234,567.89")
        self.assertEqual(money.label(5), "S$0.05")
        self.assertEqual(money.label(0), "S$0.00")

    def test_a_foreign_amount_carries_its_code_and_a_no_break_space(self):
        self.assertEqual(money.label(941205, "SEK"), "SEK 9,412.05")
        self.assertEqual(money.label(-1200, "USD"), "−USD 12.00")

    def test_a_credit_is_signed_only_when_asked(self):
        self.assertEqual(money.label(2400), "S$24.00")
        self.assertEqual(money.label(2400, plus=True), "+S$24.00")
        self.assertEqual(money.label(0, plus=True), "S$0.00")


if __name__ == "__main__":
    unittest.main()
