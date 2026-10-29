import isolate  # noqa: F401  first: no test writes the real record or cache
import unittest

from finance import spending
from test_app_writes import record
from test_db import row


def explore(conn, **q):
    return spending.explore(conn, q)


class Explorer(unittest.TestCase):
    def setUp(self):
        # k1 Grab -12.50 in Aug (from the helper), plus a July ride, an August ride
        # on a trip, and a refund that lowers August
        self.conn = record(
            row("k3", "-100.00", date="2026-07-10", period="111-11111-1 · SGD · 2026-07",
                source="statements/posb/2026/2026-07.pdf", merchant="GRAB*456"),
            row("k4", "-40.00", date="2026-08-02", merchant="GRAB*MY", merchant_country="MYS"),
            row("k5", "5.00", date="2026-08-20", direction="In", merchant="GRAB*REFUND"))
        self.addCleanup(self.conn.close)
        self.conn.execute("UPDATE txn SET trip_id = (SELECT id FROM trip LIMIT 1) WHERE txn_key = 'k4'")

    def test_singapore_only_leaves_out_trip_rows_and_counts_them_as_overseas(self):
        d = explore(self.conn)
        self.assertEqual(d["months"], ["2026-07", "2026-08"])
        self.assertEqual(d["totals"]["spend"], 10000 + 1250 - 500)
        self.assertEqual(d["totals"]["overseas"], 4000)
        self.assertEqual(d["totals"]["per_month"], (10000 + 1250 - 500) // 2)
        everywhere = explore(self.conn, scope="all")
        self.assertEqual(everywhere["totals"]["spend"], 10000 + 1250 - 500 + 4000)

    def test_by_bucket_draws_every_bucket_in_its_own_order(self):
        d = explore(self.conn)
        self.assertEqual([s["name"] for s in d["series"]], ["Getting around"])
        self.assertEqual(d["series"][0]["values"], [10000, 1250 - 500])
        self.assertIn("Getting around", d["bucket_order"])
        self.assertEqual(d["buckets"][0]["categories"][0]["name"], "Transport")

    def test_range_and_payees(self):
        d = explore(self.conn, **{"from": "2026-08", "to": "2026-08"})
        self.assertEqual(d["months"], ["2026-08"])
        self.assertEqual(d["totals"]["spend"], 750)
        by_payee = explore(self.conn, by="payee")
        self.assertEqual(by_payee["payees"][0], {"name": "Grab", "total": 10750, "count": 3,
                                                 "link": {"payee": "Grab"}})
        self.assertEqual(by_payee["biggest"][0]["amount"], 10000)

    def test_the_range_runs_to_the_record_s_last_month_even_without_spending(self):
        self.conn.execute("""UPDATE txn SET txn_date = '2026-10-01'
                             WHERE txn_key = (SELECT txn_key FROM txn WHERE txn_key = 'k2')""")
        d = explore(self.conn)
        self.assertEqual(d["months"][-1], "2026-10")
        self.assertEqual(d["series"][0]["values"][-1], 0)

    def test_transactions_filter_like_the_explorer(self):
        from finance import api
        page = api.transactions(self.conn, {"from": "2026-08", "to": "2026-08", "home": "1", "spend": "1"})
        self.assertEqual(sorted(r["key"] for r in page["rows"]), ["k1", "k5"])
        self.assertEqual(page["sum_sgd"], -(1250 - 500))
        with self.assertRaises(ValueError):
            api.transactions(self.conn, {"from": "2026-8"})

    def test_the_monthly_average_counts_only_full_months(self):
        """The Net worth projection's spending a month: the record's last month is
        still partial (a bank account has not printed it), so it is left out rather
        than counted as a whole month of a few days' spending."""
        everything = spending.monthly_average(self.conn)
        self.assertEqual(everything, {"per_month": (10000 + 4000 + 1250 - 500 + 1) // 2,
                                      "from": "2026-07", "to": "2026-08", "months": 2})
        results = {"monthly-cashflow": {"data": {"months": [
            {"month": "2026-07", "partial": False}, {"month": "2026-08", "partial": True}]}}}
        self.assertEqual(spending.monthly_average(self.conn, results=results),
                         {"per_month": 10000, "from": "2026-07", "to": "2026-07", "months": 1})

    def test_the_monthly_average_skips_a_partial_month_inside_the_window(self):
        self.conn.execute("UPDATE txn SET txn_date = '2026-09-02' WHERE txn_key = 'k5'")
        results = {"monthly-cashflow": {"data": {"months": [
            {"month": "2026-07", "partial": False}, {"month": "2026-08", "partial": True},
            {"month": "2026-09", "partial": False}]}}}
        self.assertEqual(spending.monthly_average(self.conn, results=results),
                         {"per_month": (10000 - 500 + 1) // 2, "from": "2026-07", "to": "2026-09", "months": 2})

    def test_with_no_full_month_the_average_falls_back_to_every_month(self):
        results = {"monthly-cashflow": {"data": {"months": [
            {"month": "2026-07", "partial": True}, {"month": "2026-08", "partial": True}]}}}
        self.assertEqual(spending.monthly_average(self.conn, results=results),
                         spending.monthly_average(self.conn))

    def test_refuses_bad_queries(self):
        for q in ({"by": "account"}, {"scope": "moon"}, {"from": "2026-13"}, {"from": "2026-09", "to": "2026-08"}):
            with self.subTest(q=q), self.assertRaises(spending.SpendingRefused):
                spending.explore(self.conn, q)

    def test_an_empty_record_says_so(self):
        self.conn.execute("DELETE FROM txn")
        d = explore(self.conn)
        self.assertIsNone(d["available"])
        self.assertEqual(d["series"], [])


if __name__ == "__main__":
    unittest.main()
