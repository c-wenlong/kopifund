"""G13: dates in prose read the way the app shows them (server/finance/dates.py),
and the month arithmetic beside them. A date shaped like ISO that does not exist
(31 Feb, month 13) is a ValueError, never a label or a month."""
import isolate  # noqa: F401  first: no test writes the real record or cache
import random
import unittest
from datetime import date, datetime, timedelta

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

    def test_a_datetime_is_its_day(self):
        self.assertEqual(dates.day_label(datetime(2026, 10, 3, 12, 30)), "3 Oct 2026")
        self.assertEqual(dates.month_label(datetime(2026, 10, 3, 12, 30)), "Oct 2026")


class ImpossibleDates(unittest.TestCase):
    """A date that does not exist is a misread upstream: it raises rather than
    reading as a real day in a sentence."""

    BAD = ("2026-02-31", "2025-02-29", "2026-04-31", "2026-06-31", "2026-02-30", "2026-01-32",
           "2026-01-00", "2026-00-10", "2026-13-01", "0000-01-01", "2026-13", "2026-00")

    def test_every_label_refuses_them(self):
        for bad in self.BAD:
            for fn in (dates.day_label, dates.month_label, dates.date_label):
                with self.subTest(bad=bad, fn=fn.__name__), self.assertRaises(ValueError):
                    fn(bad)
            with self.subTest(bad=bad, fn="range_label"), self.assertRaises(ValueError):
                dates.range_label(bad, "2026-12-31" if len(bad) == 10 else "2026-12")
            with self.subTest(bad=bad, fn="months_label"), self.assertRaises(ValueError):
                dates.months_label(["2026-01", bad])

    def test_month_ends_and_leap_days_that_exist_still_read(self):
        self.assertEqual(dates.day_label("2024-02-29"), "29 Feb 2024")
        self.assertEqual(dates.day_label("2000-02-29"), "29 Feb 2000")
        self.assertEqual(dates.day_label("2026-04-30"), "30 Apr 2026")
        self.assertEqual(dates.day_label("2026-12-31"), "31 Dec 2026")
        with self.assertRaises(ValueError):
            dates.day_label("1900-02-29")          # a century is a leap year only every 400

    def test_text_that_is_not_shaped_like_a_date_still_comes_back(self):
        for text in ("Grab", "", "2026", "2026-1", "2026-10-3", "26-10-03", "2026/10/03", " 2026-10-03"):
            with self.subTest(text=text):
                self.assertEqual(dates.date_label(text), text)
                self.assertEqual(dates.day_label(text), text)

    def test_digits_that_are_not_ascii_are_text(self):
        self.assertEqual(dates.day_label("\u0662\u0660\u0662\u0666-10-03"), "\u0662\u0660\u0662\u0666-10-03")

    def test_random_days_read_iff_they_exist(self):
        rng = random.Random(13)
        for _ in range(3000):
            y, m, d = rng.randint(1, 9999), rng.randint(1, 12), rng.randint(1, 31)
            text = f"{y:04d}-{m:02d}-{d:02d}"
            try:
                real = date(y, m, d)
            except ValueError:
                real = None
            with self.subTest(text=text):
                if real is None:
                    self.assertRaises(ValueError, dates.day_label, text)
                else:
                    self.assertEqual(dates.day_label(text), f"{d} {dates.MONTHS[m - 1]} {y}")
                    self.assertEqual(dates.day_label(real), dates.day_label(text))


def _index(month):
    """A month as a count of months, by datetime, for checking the arithmetic."""
    d = date.fromisoformat(month + "-01")
    return d.year * 12 + d.month - 1


class AddMonths(unittest.TestCase):

    def test_across_the_year_boundary_both_ways(self):
        self.assertEqual(dates.add_months("2026-12", 1), "2027-01")
        self.assertEqual(dates.add_months("2026-01", -1), "2025-12")
        self.assertEqual(dates.add_months("2026-01", -13), "2024-12")
        self.assertEqual(dates.add_months("2026-01", -12), "2025-01")
        self.assertEqual(dates.add_months("2026-11", 14), "2028-01")

    def test_zero_and_whole_years(self):
        self.assertEqual(dates.add_months("2026-10", 0), "2026-10")
        self.assertEqual(dates.add_months("2024-02", 12), "2025-02")
        self.assertEqual(dates.add_months("2024-02", 48), "2028-02")
        self.assertEqual(dates.add_months("2026-10", -120), "2016-10")

    def test_the_year_keeps_four_digits(self):
        self.assertEqual(dates.add_months("0999-12", 1), "1000-01")
        self.assertEqual(dates.add_months("0001-02", -1), "0001-01")
        self.assertEqual(dates.add_months("9999-11", 1), "9999-12")

    def test_past_the_calendar_is_refused(self):
        for month, n in (("0001-01", -1), ("9999-12", 1), ("2026-10", 10 ** 6)):
            with self.subTest(month=month, n=n), self.assertRaises(ValueError):
                dates.add_months(month, n)

    def test_what_is_not_a_month_is_refused(self):
        for bad in ("2026-13", "2026-00", "2026-1", "2026", "", "2026-10-03", "0000-05", " 2026-10", "Oct 2026"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                dates.add_months(bad, 1)
        for bad in (None, 202610, date(2026, 10, 1)):
            with self.subTest(bad=bad), self.assertRaises(TypeError):
                dates.add_months(bad, 1)
        for n in (1.0, "1", True, None):
            with self.subTest(n=n), self.assertRaises(TypeError):
                dates.add_months("2026-10", n)

    def test_random_months_agree_with_the_calendar(self):
        rng = random.Random(64)
        for _ in range(3000):
            a = f"{rng.randint(1, 9999):04d}-{rng.randint(1, 12):02d}"
            n = rng.randint(-2400, 2400)
            want = _index(a) + n
            with self.subTest(a=a, n=n):
                if not 12 <= want < 10000 * 12:
                    self.assertRaises(ValueError, dates.add_months, a, n)
                    continue
                b = dates.add_months(a, n)
                self.assertEqual(_index(b), want)
                self.assertEqual(dates.add_months(b, -n), a)
                self.assertEqual(dates.months_apart(a, b), n)


class MonthsApart(unittest.TestCase):

    def test_same_month_is_zero(self):
        self.assertEqual(dates.months_apart("2026-10", "2026-10"), 0)

    def test_across_the_year_boundary(self):
        self.assertEqual(dates.months_apart("2025-12", "2026-01"), 1)
        self.assertEqual(dates.months_apart("2025-01", "2026-12"), 23)
        self.assertEqual(dates.months_apart("2024-02", "2025-02"), 12)

    def test_reversed_order_is_negative(self):
        self.assertEqual(dates.months_apart("2026-01", "2025-12"), -1)
        self.assertEqual(dates.months_apart("2026-10", "2016-10"), -120)

    def test_a_thousand_years(self):
        self.assertEqual(dates.months_apart("1026-10", "2026-10"), 12_000)
        self.assertEqual(dates.months_apart("0001-01", "9999-12"), 9999 * 12 - 1)

    def test_what_is_not_a_month_is_refused(self):
        for a, b in (("2026-13", "2026-01"), ("2026-01", "2026-00"), ("2026-1", "2026-02"),
                     ("2026-01", "2026-02-03"), ("", "2026-01")):
            with self.subTest(a=a, b=b), self.assertRaises(ValueError):
                dates.months_apart(a, b)

    def test_it_counts_whole_months_whatever_the_days(self):
        """A month is a month: 31 Jan to 28 Feb and 1 Jan to 28 Feb are both one
        month apart, so the day never enters."""
        rng = random.Random(7)
        start = date(1990, 1, 1)
        for _ in range(2000):
            a = start + timedelta(days=rng.randint(0, 20000))
            b = start + timedelta(days=rng.randint(0, 20000))
            with self.subTest(a=a, b=b):
                self.assertEqual(dates.months_apart(a.isoformat()[:7], b.isoformat()[:7]),
                                 (b.year - a.year) * 12 + b.month - a.month)


if __name__ == "__main__":
    unittest.main()
