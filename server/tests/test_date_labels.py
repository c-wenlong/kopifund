"""G13: dates in prose read the way the app shows them (server/finance/dates.py)."""
import isolate  # noqa: F401  first: no test writes the real record or cache
import unittest
from datetime import date


from finance import dates


class DateLabels(unittest.TestCase):
    def test_day(self):
        self.assertEqual(dates.day_label("2026-10-03"), "3 Oct 2026")
        self.assertEqual(dates.day_label(date(2026, 1, 31)), "31 Jan 2026")

    def test_month(self):
        self.assertEqual(dates.month_label("2028-02"), "Feb 2028")
        self.assertEqual(dates.month_label("2028-02-14"), "Feb 2028")

    def test_either_and_other_text(self):
        self.assertEqual(dates.date_label("2026-10-03"), "3 Oct 2026")
        self.assertEqual(dates.date_label("2026-10"), "Oct 2026")
        self.assertEqual(dates.date_label("2026-13"), "2026-13")
        self.assertEqual(dates.date_label("Grab"), "Grab")
        self.assertEqual(dates.date_label(None), "")

    def test_range(self):
        self.assertEqual(dates.range_label("2026-10-03", "2026-10-09"), "3 Oct to 9 Oct 2026")
        self.assertEqual(dates.range_label("2025-12-28", "2026-01-03"), "28 Dec 2025 to 3 Jan 2026")
        self.assertEqual(dates.range_label("2026-01", "2026-06"), "Jan to Jun 2026")
        self.assertEqual(dates.range_label("2025-11", "2026-02"), "Nov 2025 to Feb 2026")
        self.assertEqual(dates.range_label("2026-10-03", "2026-10-03"), "3 Oct 2026")

    def test_months(self):
        self.assertEqual(dates.months_label(["2026-01", "2026-03"]), "Jan 2026, Mar 2026")


if __name__ == "__main__":
    unittest.main()
