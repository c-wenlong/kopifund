"""Dates, where the year is the part the statements are careless about.

Two forms appear. The consolidated layout prints dd/mm/yyyy and carries its own
year (F7: it used to be thrown away and the statement's used instead, so a
December row on a January statement landed a year late). The POSB and card
layouts print "28 Dec" with no year at all, so a month later than the
statement's month must belong to the year before.
"""
import isolate  # noqa: F401  first: no test writes the real record or cache
import unittest

import parse_support as sup
from parse_support import ps


class ConsolidatedPrintedYear(unittest.TestCase):
    """dd/mm/yyyy carries its own year, so it is taken at face value."""

    def test_december_row_on_a_january_statement(self):
        self.assertEqual("2025-12-28", ps.iso_date("28/12/2025", "31 Jan 2026"))

    def test_january_row_on_a_january_statement(self):
        self.assertEqual("2026-01-03", ps.iso_date("03/01/2026", "31 Jan 2026"))

    def test_same_month_row(self):
        self.assertEqual("2026-07-15", ps.iso_date("15/07/2026", "31 Jul 2026"))

    def test_the_statement_year_is_not_used(self):
        """Whatever year the statement was issued in, the row keeps its own."""
        for statement in ("31 Jan 2026", "31 Dec 2030", "01 Mar 1999"):
            with self.subTest(statement):
                self.assertEqual("2025-12-28", ps.iso_date("28/12/2025", statement))

    def test_every_fixture_row_keeps_its_printed_year(self):
        parsed = sup.run_statements(["dbs-consolidated.txt"])
        for r in parsed.rows:
            day, month, year = r["date_raw"].split("/")
            self.assertEqual(f"{year}-{month}-{day}", r["date"])


class PosbRollover(unittest.TestCase):
    """"28 Dec" has no year: a row cannot post after the statement that prints it."""

    def test_december_row_on_a_february_statement_rolls_back(self):
        self.assertEqual("2024-12-01", ps.iso_date("01 Dec", "28 Feb 2025"))

    def test_same_month_row_keeps_the_statement_year(self):
        self.assertEqual("2025-02-28", ps.iso_date("28 Feb", "28 Feb 2025"))

    def test_an_earlier_month_keeps_the_statement_year(self):
        self.assertEqual("2025-01-15", ps.iso_date("15 Jan", "28 Feb 2025"))

    def test_december_row_on_a_january_statement_rolls_back(self):
        self.assertEqual("2024-12-31", ps.iso_date("31 Dec", "09 Jan 2025"))

    def test_the_fixture_spans_the_year_boundary(self):
        parsed = sup.run_statements(["posb-deposit-dec-jan.txt"])
        self.assertEqual("28 Feb 2025", parsed.block()["statement_date"])
        self.assertEqual(["2024-12", "2025-01", "2025-02"],
                         sorted({r["date"][:7] for r in parsed.rows}))
        self.assertEqual("2024-12-01", parsed.rows[0]["date"])
        self.assertEqual("01 Dec", parsed.rows[0]["date_raw"])
        self.assertEqual("2025-02-28", parsed.rows[-1]["date"])


class PurchaseDates(unittest.TestCase):
    """Card rows print the purchase date as "29MAY", separate from the posting
    date, and with no year of its own."""

    def test_december_purchase_on_a_january_posting(self):
        self.assertEqual("2025-12-31", ps.purchase_date("2026-01-03", "31DEC"))

    def test_same_month_purchase(self):
        self.assertEqual("2026-07-15", ps.purchase_date("2026-07-16", "15JUL"))

    def test_previous_month_purchase_inside_one_year(self):
        self.assertEqual("2026-06-29", ps.purchase_date("2026-07-01", "29JUN"))

    def test_no_tag_means_no_purchase_date(self):
        self.assertIsNone(ps.purchase_date("2026-07-01", None))

    def test_the_card_fixture_carries_purchase_dates(self):
        parsed = sup.run_statements(["card-only-month.txt"])
        pairs = [(r["date"], r["purchase_date"]) for r in parsed.rows]
        self.assertTrue(all(p is not None for _, p in pairs))
        self.assertTrue(all(p <= d for d, p in pairs),
                        "a purchase cannot happen after it posts")


class PeriodNaming(unittest.TestCase):

    def test_account_period_is_the_statement_month(self):
        block = sup.run_statements(["posb-deposit.txt"]).block()
        self.assertEqual("130-24680-5 · SGD · 2026-06", ps.period_name(block))
        self.assertEqual("2026-06-30", ps.period_end(block))

    def test_card_period_is_the_month_before_the_as_at_date(self):
        block = sup.run_statements(["card-only-month.txt"]).block()
        self.assertEqual("130-24680-5 · SGD · 2024-10 · card", ps.period_name(block))
        self.assertEqual("2024-11-09", ps.period_end(block))

    def test_a_january_card_statement_rolls_back_a_year(self):
        block = dict(account_no="130-24680-5", currency="SGD",
                     statement_date="09 Jan 2025", card_statement=True)
        self.assertEqual("130-24680-5 · SGD · 2024-12 · card", ps.period_name(block))


if __name__ == "__main__":
    unittest.main()
