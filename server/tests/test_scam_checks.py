"""server/finance/scam_checks.py: G15's flags on transfers that look like a scam."""
import isolate  # noqa: F401  first: no test writes the real record or cache
import unittest

from finance import scam_checks as sc
from test_app_writes import record
from test_db import row

# invented rows. The record (test_app_writes.record) holds two card-free
# August rows; an early row here makes it go back far enough to call a payee new.
MAY = dict(date="2026-05-02", period="111-11111-1 · SGD · 2026-05", source="statements/posb/2026/2026-05.pdf")


def sent(key, amount, day, to="SCAMMER PTE", **kw):
    """A PayNow transfer out on 2026-08-<day> to `to`, named by no rule."""
    month = kw.pop("month", "08")
    out = dict(date=f"2026-{month}-{day:02d}", period=f"111-11111-1 · SGD · 2026-{month}",
               source=f"statements/posb/2026/2026-{month}.pdf", bank_txn_type="PayNow", merchant="",
               counterparty=to, payee="", category="", rule="")
    out.update(kw)
    return row(key, amount, **out)


def card(key, amount, day, at="SHOP*NEW", month="08", **kw):
    """A card payment on 2026-<month>-<day> at `at`, named by no rule."""
    out = dict(date=f"2026-{month}-{day:02d}", period=f"111-11111-1 · SGD · 2026-{month}",
               source=f"statements/posb/2026/2026-{month}.pdf", bank_txn_type="Debit Card Transaction",
               merchant=at, counterparty="", card_last4="1234", payee="", category="", rule="")
    out.update(kw)
    return row(key, amount, **out)


def codes(conn):
    return {f["key"]: [r["code"] for r in f["reasons"]] for f in sc.flags(conn)}


class Flags(unittest.TestCase):
    def conn(self, *rows):
        conn = record(row("early", "-5.00", **MAY), *rows)
        self.addCleanup(conn.close)
        return conn

    def test_a_big_transfer_to_a_new_payee(self):
        conn = self.conn(sent("t1", "-800.00", 10))
        f = sc.flags(conn)
        self.assertEqual([x["key"] for x in f], ["t1"])
        self.assertEqual(f[0]["amount"], 80000)
        self.assertEqual(f[0]["reasons"][0]["text"], "S$800 to a new payee, the first payment to them")

    def test_under_the_threshold_is_not_flagged(self):
        self.assertEqual(codes(self.conn(sent("t1", "-499.99", 10))), {})

    def test_a_payee_paid_long_before_is_not_new(self):
        conn = self.conn(sent("old", "-20.00", 2, month="05"), sent("t1", "-900.00", 10))
        self.assertEqual(codes(conn), {})

    def test_a_big_payment_soon_after_a_small_first_one(self):
        conn = self.conn(sent("t0", "-5.00", 1), sent("t1", "-900.00", 9))
        f = sc.flags(conn)
        self.assertEqual([x["key"] for x in f], ["t1"])
        self.assertIn("first paid 8 days before", f[0]["reasons"][0]["text"])

    def test_repeated_transfers_flag_every_row_of_the_run(self):
        conn = self.conn(sent("t1", "-100.00", 3), sent("t2", "-200.00", 6), sent("t3", "-300.00", 9))
        self.assertEqual(codes(conn), {"t1": ["repeated"], "t2": ["repeated"], "t3": ["repeated"]})
        self.assertEqual(sc.flags(conn)[0]["reasons"][0]["text"],
                         "3 transfers to a new payee in 7 days, S$600 in all, each larger than the last")

    def test_repeated_small_transfers_that_add_up_to_little_are_left_alone(self):
        conn = self.conn(*(sent(f"t{i}", "-10.00", i) for i in (1, 2, 3, 4)))
        self.assertEqual(codes(conn), {})

    def test_repeats_spread_wider_than_the_window_are_left_alone(self):
        conn = self.conn(sent("t1", "-200.00", 1), sent("t2", "-200.00", 10), sent("t3", "-200.00", 20))
        self.assertEqual(codes(conn), {})

    def test_both_reasons_on_one_row(self):
        conn = self.conn(sent("t1", "-100.00", 3), sent("t2", "-100.00", 4), sent("t3", "-600.00", 5))
        self.assertEqual(codes(conn)["t3"], ["new_payee", "repeated"])

    def test_the_record_s_first_weeks_flag_nothing(self):
        conn = record(sent("t1", "-900.00", 10))     # no earlier row: everyone is new
        self.addCleanup(conn.close)
        self.assertEqual(codes(conn), {})

    def test_card_payments_and_money_in_are_not_transfers_out(self):
        conn = self.conn(sent("card", "-900.00", 10, card_last4="1234"),
                         sent("in", "900.00", 11, direction="In"))
        self.assertEqual(codes(conn), {})

    def test_a_card_purchase_with_no_card_number_is_not_a_transfer(self):
        # Wise and YouTrip print no card number on a card purchase
        conn = self.conn(sent("wise", "-900.00", 10, to="SHOP ONE", bank_txn_type="Wise Card Payment"),
                         sent("yt", "-900.00", 11, to="SHOP TWO", bank_txn_type="YouTrip Payment"))
        self.assertEqual(codes(conn), {})

    def test_reference_numbers_in_the_memo_do_not_make_a_new_payee(self):
        conn = self.conn(sent("old", "-20.00", 2, month="05", to="", memo="PAYNOW TRANSFER 6102847 JOHN"),
                         sent("t1", "-900.00", 10, to="", memo="PAYNOW TRANSFER 3850216 JOHN"))
        self.assertEqual(codes(conn), {})

    def test_a_counterparty_that_is_only_a_number_is_a_payee_and_shown_masked(self):
        # a PayLah! transfer to someone no rule names prints only their phone number
        conn = self.conn(sent("t1", "-900.00", 10, to="SEND MONEY TO 80000002"),
                         sent("other", "-900.00", 11, to="SEND MONEY TO 80000003"))
        f = {x["key"]: x for x in sc.flags(conn)}
        self.assertEqual(set(f), {"t1", "other"})          # two people, each new
        self.assertEqual(f["t1"]["payee"], "SEND MONEY TO •• 0002")

    def test_a_person_a_people_rule_names_is_known(self):
        conn = self.conn(sent("t1", "-900.00", 10, rule="counterparty:SCAMMER*"))
        conn.execute("INSERT INTO payee(name, kind) VALUES ('Sam Lee', 'Person')")
        conn.execute("UPDATE txn SET payee_id = (SELECT id FROM payee WHERE name = 'Sam Lee') "
                     "WHERE txn_key = 't1'")
        self.assertEqual(codes(conn), {})
        # the same person named by a merchant rule is not one a people rule knows
        conn.execute("UPDATE txn SET rule = 'merchant:SCAMMER*' WHERE txn_key = 't1'")
        self.assertEqual(codes(conn), {"t1": ["new_payee"]})

    def test_a_row_marked_fine_trusts_its_payee(self):
        conn = self.conn(sent("t1", "-900.00", 10), sent("t2", "-900.00", 12))
        sc.mark_fine(conn, "t1")
        self.assertEqual(codes(conn), {})
        sc.mark_fine(conn, "t1", fine=False)
        self.assertEqual(set(codes(conn)), {"t1", "t2"})

    def test_the_page_lists_rows_marked_fine_so_one_can_be_taken_back(self):
        conn = self.conn(sent("t1", "-900.00", 10))
        sc.mark_fine(conn, "t1")
        p = sc.page(conn)
        self.assertEqual((p["flags"], p["marked_fine"]),
                         ([], [{"key": "t1", "date": "2026-08-10", "payee": "SCAMMER PTE", "amount": 90000}]))

    def test_a_mark_follows_a_re_key_and_can_be_taken_back_by_the_new_key(self):
        conn = self.conn(sent("t1", "-900.00", 10))
        sc.mark_fine(conn, "t1")
        conn.execute("UPDATE txn SET txn_key = 't1-new' WHERE txn_key = 't1'")
        conn.execute("INSERT INTO txn_key_alias(old_key, txn_id) SELECT 't1', id FROM txn WHERE txn_key = 't1-new'")
        self.assertEqual(codes(conn), {})
        self.assertEqual([r["key"] for r in sc.page(conn)["marked_fine"]], ["t1-new"])
        sc.mark_fine(conn, "t1-new", fine=False)
        self.assertEqual(codes(conn), {"t1-new": ["new_payee"]})

    def test_a_payee_stays_trusted_after_the_row_marked_fine_gets_a_category(self):
        conn = self.conn(sent("t1", "-900.00", 10), sent("t2", "-900.00", 20))
        sc.mark_fine(conn, "t1")
        # the marked row is now a spending row, out of the rows the checks flag
        conn.execute("UPDATE txn SET category_id = (SELECT id FROM category WHERE name = 'Transport') "
                     "WHERE txn_key = 't1'")
        self.assertEqual(codes(conn), {})

    def test_a_payee_paid_before_on_a_spending_row_is_not_new(self):
        conn = self.conn(sent("old", "-20.00", 2, month="06"), sent("t1", "-900.00", 10))
        conn.execute("UPDATE txn SET category_id = (SELECT id FROM category WHERE name = 'Transport') "
                     "WHERE txn_key = 'old'")
        self.assertEqual(codes(conn), {})

    def test_a_person_is_known_when_any_row_of_theirs_is_named_by_a_people_rule(self):
        conn = self.conn(sent("t1", "-900.00", 10, rule="key:t1"), sent("t2", "-900.00", 12, rule="memo:SCAM*"))
        conn.execute("INSERT INTO payee(name, kind) VALUES ('Sam Lee', 'Person')")
        conn.execute("UPDATE txn SET payee_id = (SELECT id FROM payee WHERE name = 'Sam Lee') "
                     "WHERE txn_key IN ('t1', 't2')")
        self.assertEqual(codes(conn), {})

    def test_marking_an_unknown_key_is_refused(self):
        with self.assertRaises(sc.SettingsRefused):
            sc.mark_fine(self.conn(), "nope")

    def test_newest_first(self):
        conn = self.conn(sent("a", "-900.00", 10, to="ONE"), sent("b", "-900.00", 12, to="TWO"))
        self.assertEqual([f["key"] for f in sc.flags(conn)], ["b", "a"])


class CardChecks(unittest.TestCase):
    """The checks on card and shop payments: abroad with no trip, a test
    charge, a payment twice, a new subscription."""

    def conn(self, *rows):
        conn = record(row("early", "-5.00", **MAY), *rows)
        self.addCleanup(conn.close)
        return conn

    def test_a_charge_abroad_at_a_new_merchant_with_no_trip(self):
        conn = self.conn(card("c1", "-80.00", 10, merchant_country="USA"))
        f = sc.flags(conn)
        self.assertEqual(codes(conn), {"c1": ["abroad"]})
        self.assertEqual(f[0]["reasons"][0]["text"], "S$80 at a new merchant in USA with no trip then")

    def test_a_charge_abroad_on_a_trip_or_under_the_floor_or_at_home_is_fine(self):
        conn = self.conn(card("trip", "-80.00", 10, at="SHOP*A", merchant_country="USA"),
                         card("small", "-19.99", 11, at="SHOP*B", merchant_country="USA"),
                         card("home", "-80.00", 12, at="SHOP*C", merchant_country="SGP"))
        conn.execute("UPDATE txn SET trip_id = (SELECT id FROM trip LIMIT 1) WHERE txn_key = 'trip'")
        self.assertEqual(codes(conn), {})

    def test_a_foreign_shop_paid_for_months_is_not_new(self):
        conn = self.conn(card("old", "-30.00", 3, at="NETFLIX.COM", month="05", merchant_country="NLD"),
                         card("c1", "-30.00", 3, at="NETFLIX.COM", month="08", merchant_country="NLD"))
        self.assertEqual(codes(conn), {})

    def test_a_small_first_charge_then_a_larger_one(self):
        conn = self.conn(card("t0", "-1.00", 10), card("t1", "-250.00", 12))
        f = {x["key"]: x["reasons"][0]["text"] for x in sc.flags(conn)}
        self.assertEqual(f, {"t0": "A S$1 first charge, then S$250 within 7 days",
                             "t1": "After a S$1 first charge on 10 Aug 2026"})

    def test_a_merchant_a_rule_names_is_known_abroad_and_after_a_small_first_charge(self):
        conn = self.conn(card("t0", "-1.00", 10, at="GRAB*A1"), card("t1", "-250.00", 12, at="GRAB*A1"),
                         card("a1", "-80.00", 14, at="GRAB*MY", merchant_country="MYS"))
        conn.execute("UPDATE txn SET payee_id = (SELECT id FROM payee WHERE name = 'Grab') "
                     "WHERE txn_key IN ('t0', 't1', 'a1')")
        self.assertEqual(codes(conn), {})

    def test_a_small_charge_the_same_day_as_a_larger_one_whatever_their_key_order(self):
        # keys are hashes, not times: the larger charge's key sorts first here
        conn = self.conn(card("a-big", "-250.00", 10), card("z-small", "-1.00", 10))
        self.assertEqual(codes(conn), {"a-big": ["test_charge"], "z-small": ["test_charge"]})

    def test_a_test_charge_keeps_its_own_window_when_new_payees_are_shorter(self):
        conn = self.conn(card("t0", "-1.00", 10), card("t1", "-250.00", 13))
        sc.save_settings(conn, {"new_payee_days": 1, "test_days": 7})
        self.assertEqual(codes(conn), {"t0": ["test_charge"], "t1": ["test_charge"]})

    def test_wise_card_charges_at_one_shop_are_one_payee_whatever_their_memo(self):
        # the Wise parser puts the amount in the memo of a card payment
        conn = self.conn(card("w0", "-1.00", 10, at="DEMO SHOP", card_last4="", bank_txn_type="Wise Card Payment",
                              memo="charges for 1.00 SGD"),
                         card("w1", "-250.00", 11, at="DEMO SHOP", card_last4="", bank_txn_type="Wise Card Payment",
                              memo="charges for 250.00 SGD"))
        self.assertEqual(codes(conn), {"w0": ["test_charge"], "w1": ["test_charge"]})

    def test_a_small_first_charge_alone_or_with_a_late_follow_up_is_fine(self):
        conn = self.conn(card("a0", "-1.00", 1, at="SHOP*A"),
                         card("b0", "-1.00", 1, at="SHOP*B"), card("b1", "-250.00", 20, at="SHOP*B"))
        self.assertEqual(codes(conn), {})

    def test_the_same_amount_twice_the_same_day(self):
        conn = self.conn(card("d1", "-45.90", 10, at="SHOP*OLD", month="05"),
                         card("d2", "-45.90", 10, at="SHOP*OLD", month="05", purchase_date=""),
                         card("x", "-45.90", 11, at="SHOP*OLD", month="05"))
        self.assertEqual(codes(conn), {"d1": ["twice"], "d2": ["twice"]})
        self.assertEqual(sc.flags(conn)[0]["reasons"][0]["text"], "Charged S$45.90 twice the same day")

    def test_twice_under_the_floor_is_left_alone(self):
        conn = self.conn(card("d1", "-2.10", 10, at="BUS/MRT"), card("d2", "-2.10", 10, at="BUS/MRT"))
        self.assertEqual(codes(conn), {})

    def test_a_new_monthly_charge(self):
        conn = self.conn(card("s1", "-15.98", 1, at="STREAM*TV", month="07"),
                         card("s2", "-15.98", 31, at="STREAM*TV", month="07"))
        self.assertEqual(codes(conn), {"s1": ["new_subscription"]})
        self.assertEqual(sc.flags(conn)[0]["reasons"][0]["text"], "A new monthly payment, first charged S$15.98")

    def test_a_monthly_charge_first_paid_long_ago_is_not_new(self):
        conn = self.conn(card("s1", "-15.98", 1, at="STREAM*TV", month="06"),
                         card("s2", "-15.98", 1, at="STREAM*TV", month="07"),
                         card("s3", "-15.98", 1, at="STREAM*TV", month="08"), card("late", "-1.00", 30, at="X", month="09"))
        self.assertEqual(codes(conn), {})

    def test_a_new_monthly_transfer_to_a_person_is_not_a_subscription(self):
        conn = self.conn(sent("r1", "-300.00", 1, to="NEW LANDLORD", month="07"),
                         sent("r2", "-300.00", 31, to="NEW LANDLORD", month="07"))
        self.assertNotIn("new_subscription", [c for cs in codes(conn).values() for c in cs])

    def test_a_trusted_merchant_is_never_flagged(self):
        conn = self.conn(card("c1", "-80.00", 10, merchant_country="USA"))
        sc.mark_fine(conn, "c1")
        self.assertEqual(codes(conn), {})


class Settings(unittest.TestCase):
    def setUp(self):
        self.conn = record(row("early", "-5.00", **MAY), sent("t1", "-300.00", 10))
        self.addCleanup(self.conn.close)

    def test_defaults_until_saved(self):
        self.assertEqual(sc.settings(self.conn), sc.DEFAULTS)

    def test_a_saved_threshold_changes_what_is_flagged(self):
        self.assertEqual(codes(self.conn), {})
        out = sc.save_settings(self.conn, {"big_transfer": 25000})
        self.assertEqual(out["big_transfer"], 25000)
        self.assertEqual(out["repeat_days"], sc.DEFAULTS["repeat_days"])
        self.assertEqual(codes(self.conn), {"t1": ["new_payee"]})

    def test_bad_settings_are_refused(self):
        for data in ({"big_transfer": -1}, {"big_transfer": 1.5}, {"repeat_count": 1},
                     {"repeat_days": True}, {"nope": 3}):
            with self.assertRaises(sc.SettingsRefused, msg=data):
                sc.save_settings(self.conn, data)
        self.assertEqual(sc.settings(self.conn), sc.DEFAULTS)


class Pages(unittest.TestCase):
    """Where the flags show: Transactions' filter and reasons, Home's line, the API."""

    def setUp(self):
        self.conn = record(row("early", "-5.00", **MAY), sent("t1", "-900.00", 10))
        self.addCleanup(self.conn.close)

    def test_transactions_filter_to_the_flagged_rows_each_with_its_reasons(self):
        from finance import api
        out = api.transactions(self.conn, {"flag": "scam"})
        self.assertEqual([r["key"] for r in out["rows"]], ["t1"])
        self.assertEqual(out["rows"][0]["flags"][0]["code"], "new_payee")
        every = {r["key"]: r["flags"] for r in api.transactions(self.conn, {})["rows"]}
        self.assertEqual((every["k1"], len(every["t1"])), ([], 1))

    def test_the_filter_with_nothing_flagged_lists_nothing(self):
        from finance import api
        sc.mark_fine(self.conn, "t1")
        self.assertEqual(api.transactions(self.conn, {"flag": "scam"})["total"], 0)

    def test_home_names_the_newest_flagged_transfer(self):
        from finance import home
        self.assertEqual(home.scam_line(self.conn), {
            "count": 1, "scams": 1, "payee": "SCAMMER PTE", "date": "2026-08-10", "amount": 90000,
            "code": "new_payee", "reason": "S$900 to a new payee, the first payment to them"})
        sc.mark_fine(self.conn, "t1")
        self.assertIsNone(home.scam_line(self.conn))

    def test_home_names_a_scam_before_a_newer_flag_that_is_no_scam(self):
        # a new subscription after the transfer: it is counted, but the transfer is the one named
        from finance import home
        conn = record(row("early", "-5.00", **MAY), sent("t1", "-900.00", 10),
                      card("s1", "-15.98", 12, at="STREAM*TV"), card("s2", "-15.98", 12, at="STREAM*TV", month="09"))
        self.addCleanup(conn.close)
        got = home.scam_line(conn)
        self.assertEqual((got["count"], got["scams"], got["payee"], got["code"]), (2, 1, "SCAMMER PTE", "new_payee"))

    def test_home_counts_no_scam_when_every_flag_is_an_ordinary_kind(self):
        from finance import home
        conn = record(row("early", "-5.00", **MAY), card("s1", "-15.98", 1, at="STREAM*TV", month="07"),
                      card("s2", "-15.98", 31, at="STREAM*TV", month="07"))
        self.addCleanup(conn.close)
        self.assertEqual(home.scam_line(conn), {
            "count": 1, "scams": 0, "payee": "STREAM*TV", "date": "2026-07-01", "amount": 1598,
            "code": "new_subscription", "reason": "A new monthly payment, first charged S$15.98"})

    def test_only_money_sent_to_a_new_payee_or_spent_abroad_reads_as_a_scam(self):
        self.assertEqual(sc.SCAMS, ("new_payee", "repeated", "abroad"))
        self.assertLessEqual(set(sc.SCAMS), set(sc.REASONS))

    def test_the_endpoints_read_and_write_through_serve(self):
        import json
        from unittest import mock
        import serve
        from finance import db

        class Kept:
            """The test's record, which serve's `closing` must not close."""
            def __init__(self, conn):
                self.conn = conn

            def __getattr__(self, name):
                return getattr(self.conn, name)

            def close(self):
                pass

        with mock.patch("finance.api.open_record", lambda: self.conn):
            self.assertEqual([f["key"] for f in serve.answer("/api/scam-checks", {})["flags"]], ["t1"])
        self.assertTrue(serve.cached("/api/scam-checks"))
        with mock.patch.object(db, "connect", lambda: Kept(self.conn)):
            out = serve.write("/api/scam-checks/fine", {}, json.dumps({"key": "t1"}).encode())
            self.assertEqual((out["flags"], [r["key"] for r in out["marked_fine"]]), ([], ["t1"]))
            out = serve.write("/api/scam-checks/fine", {}, json.dumps({"key": "t1", "fine": False}).encode())
            self.assertEqual(out["marked_fine"], [])
            out = serve.write("/api/scam-checks/settings", {}, b'{"big_transfer": 100000}')
            self.assertEqual((out["settings"]["big_transfer"], out["flags"]), (100000, []))
            for path, body in (("/api/scam-checks/settings", b'{"repeat_count": 0}'),
                               ("/api/scam-checks/fine", b'{"key": "nope"}')):
                with self.assertRaises(serve.Refused, msg=path):
                    serve.write(path, {}, body)


class Upgrade(unittest.TestCase):
    def test_a_version_3_record_gains_the_two_tables_and_keeps_its_rows(self):
        import sqlite3
        import tempfile
        from contextlib import closing
        from pathlib import Path
        from finance import db
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "kopifund.db"
            db.connect(path).close()
            with closing(sqlite3.connect(path)) as c:
                c.execute("DROP TABLE setting")
                c.execute("DROP TABLE txn_marked_fine")
                c.execute("PRAGMA user_version = 3")
                c.commit()
            with closing(db.connect(path)) as conn:
                self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], db.SCHEMA_VERSION)
                self.assertEqual(sc.settings(conn), sc.DEFAULTS)
                self.assertEqual(sc.marked_fine(conn), set())


if __name__ == "__main__":
    unittest.main()
