"""server/finance/pending.py: the phone's rows into the record (G25).

An approved alert on the phone posts to /api/link/transactions and lands in
pending_txn keyed by import_id; confirm() pairs it with the statement row
that is the same spend, on amount, currency and date with a payee that reads
alike after normalising. The fixtures are invented rows on the record
test_app_writes.record builds: k1 is a SGD 12.50 Grab spend on 2026-08-03,
k2 a SGD 17.00 spend at KOPITIAM 0123 AMK the same day with no payee named.
"""
import isolate  # noqa: F401  first: no test writes the real record or cache
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import date
from pathlib import Path

from finance import db, home, pending
from finance.errors import NotFound, Refused
from test_app_writes import record
from test_db import row
import support


def spend(import_id="imp1", date="2026-08-03", amount=-1250, currency="SGD", payee="Grab", **kw):
    return {"import_id": import_id, "date": date, "amount_minor": amount,
            "currency": currency, "payee": payee, **kw}


class PendingTest(unittest.TestCase):
    def setUp(self):
        self.conn = record()
        self.addCleanup(self.conn.close)

    def states(self):
        return {r["import_id"]: r["state"] for r in
                self.conn.execute("SELECT import_id, state FROM pending_txn")}

    def test_a_row_waits_until_a_statement_covers_it(self):
        out = pending.post_rows(self.conn, "dev_1", [spend(payee="NOBODY KNOWS")])
        self.assertEqual(out, {"accepted": 1, "matched": 0})
        self.assertEqual(self.states(), {"imp1": "waiting"})
        (listed,) = pending.rows(self.conn, {"dev_1": "Test iPhone"})
        self.assertEqual((listed["device"], listed["date"], listed["amount_minor"], listed["currency"],
                          listed["payee"], listed["state"], listed["matched_txn_key"]),
                         ("Test iPhone", "2026-08-03", -1250, "SGD", "NOBODY KNOWS", "waiting", None))

    def test_a_retry_never_doubles_a_row(self):
        pending.post_rows(self.conn, "dev_1", [spend()])
        out = pending.post_rows(self.conn, "dev_1", [spend()])
        self.assertEqual(out["accepted"], 0)          # same fields: nothing new
        self.assertEqual(list(self.states()), ["imp1"])

    def test_a_retry_on_a_waiting_row_corrects_it(self):
        pending.post_rows(self.conn, "dev_1", [spend(amount=-9999)])
        pending.post_rows(self.conn, "dev_1", [spend()])
        self.assertEqual(pending.rows(self.conn)[0]["amount_minor"], -1250)

    def test_a_retry_on_a_matched_row_leaves_it(self):
        pending.post_rows(self.conn, "dev_1", [spend(payee="zzz")])   # no match yet
        pending.post_rows(self.conn, "dev_1", [spend(payee="zzz", date="2026-08-04")])
        self.assertEqual(self.states()["imp1"], "waiting")
        # put the date back and let it match k1, then retry with a new payee
        pending.post_rows(self.conn, "dev_1", [spend()])
        self.assertEqual(self.states()["imp1"], "matched")
        pending.post_rows(self.conn, "dev_1", [spend(payee="changed")])
        self.assertEqual(pending.rows(self.conn)[0]["payee"], "Grab")

    def test_a_row_confirms_against_the_spend_it_is(self):
        out = pending.post_rows(self.conn, "dev_1", [spend()])
        self.assertEqual(out, {"accepted": 1, "matched": 1})
        r = pending.rows(self.conn)[0]
        self.assertEqual((r["state"], r["matched_txn_key"]), ("matched", "k1"))

    def test_the_payee_must_read_alike(self):
        # money and day agree on k1 but the names do not
        self.assertEqual(pending.post_rows(self.conn, "d", [spend(payee="Starbucks")])["matched"], 0)
        # and they do when either the payee or the raw merchant reads alike
        self.assertEqual(pending.post_rows(self.conn, "d", [spend("i2", amount=-1700, payee="kopitiam")])
                         ["matched"], 1)   # k2's merchant_raw, no payee named

    def test_a_recipient_named_row_matches_by_counterparty(self):
        # a PayNow To: name lands in counterparty, not merchant_raw, and the
        # payee may be unnamed: the same printed line the API sends the phone
        self.conn.execute("UPDATE txn SET merchant_raw = '', payee_id = NULL, "
                          "counterparty = 'TO: KOPITIAM UNCLE' WHERE txn_key = 'k1'")
        out = pending.post_rows(self.conn, "d", [spend(payee="Kopitiam Uncle")])
        self.assertEqual(out["matched"], 1)

    def test_a_date_in_another_iso_form_still_matches(self):
        # fromisoformat also takes "20260803" and week dates; stored raw they
        # would never equal a statement's YYYY-MM-DD, so _row stores the
        # parsed form
        out = pending.post_rows(self.conn, "d", [spend(date="20260803")])
        self.assertEqual(out["matched"], 1)
        self.assertEqual(pending.rows(self.conn)[0]["date"], "2026-08-03")

    def test_money_and_day_must_agree_exactly(self):
        for change in ({"amount": -1251}, {"date": "2026-08-04"}, {"currency": "MYR"}):
            out = pending.post_rows(self.conn, "d", [spend(**change)])
            self.assertEqual(out["matched"], 0, change)
            self.assertEqual(self.states()["imp1"], "waiting")
            self.conn.execute("DELETE FROM pending_txn")

    def test_one_record_row_covers_at_most_one_pending_row(self):
        pending.post_rows(self.conn, "d", [spend("a"), spend("b")])
        self.assertEqual(self.states(), {"a": "matched", "b": "waiting"})

    def test_a_pair_the_owner_refused_is_never_reoffered(self):
        pending.post_rows(self.conn, "d", [spend()])
        pending.restore(self.conn, "imp1")            # owner: not that row
        self.assertEqual(self.states()["imp1"], "waiting")
        pending.confirm(self.conn)
        self.assertEqual(self.states()["imp1"], "waiting")
        rejected = self.conn.execute(
            "SELECT rejected FROM pending_txn WHERE import_id = 'imp1'").fetchone()[0]
        self.assertEqual(rejected, '["k1"]')

    def test_a_dismissed_row_never_matches_but_restores(self):
        pending.post_rows(self.conn, "d", [spend()])
        self.assertEqual(self.states()["imp1"], "matched")     # matched at post
        with self.assertRaises(Refused):
            pending.dismiss(self.conn, "imp1")                 # not waiting
        pending.restore(self.conn, "imp1")                     # back to waiting, k1 rejected
        pending.dismiss(self.conn, "imp1")
        self.assertEqual(self.states()["imp1"], "dismissed")
        self.assertEqual(pending.waiting_count(self.conn), 0)
        with self.assertRaises(NotFound):
            pending.dismiss(self.conn, "imp_x")
        pending.restore(self.conn, "imp1")
        self.assertEqual(self.states()["imp1"], "waiting")

    def test_a_waiting_row_can_be_dismissed(self):
        pending.post_rows(self.conn, "d", [spend(payee="zzz")])
        pending.dismiss(self.conn, "imp1")
        self.assertEqual(self.states()["imp1"], "dismissed")
        pending.restore(self.conn, "imp1")                   # a wrong dismiss undoes too
        self.assertEqual(self.states()["imp1"], "waiting")
        with self.assertRaises(NotFound):
            pending.restore(self.conn, "imp_x")

    def test_a_synthetic_row_is_no_spend(self):
        # a made-up filler row at the same money and day is no spend: only
        # the synthetic matches this row's payee, and it must not
        conn = record(row("syn", "-88.00", bank_txn_type=db.SYNTHETIC_TYPE,
                          merchant="", payee="", category="", rule=""))
        self.addCleanup(conn.close)
        out = pending.post_rows(conn, "d", [spend(amount=-8800, payee="grab")])
        self.assertEqual(out["matched"], 0)

    def test_a_matched_row_stays_claimed_when_its_key_changes(self):
        pending.post_rows(self.conn, "d", [spend()])
        self.conn.execute("UPDATE txn SET txn_key = 'k1-new' WHERE txn_key = 'k1'")
        self.conn.execute("INSERT INTO txn_key_alias(old_key, txn_id) "
                          "SELECT 'k1', id FROM txn WHERE txn_key = 'k1-new'")
        pending.post_rows(self.conn, "d", [spend("other")])
        self.assertEqual(self.states()["other"], "waiting")    # k1-new still claimed

    def test_the_eye_lists_waiting_rows_only(self):
        pending.post_rows(self.conn, "d", [spend(payee="zzz"), spend("i2", payee="nobody", amount=-1)])
        eye = home.needs_your_eye(self.conn, {}, {"rows": [], "month": "2026-08"})
        self.assertEqual(eye["pending"], 2)
        line = next(l for l in eye["lines"] if l["kind"] == "pending")
        self.assertEqual((line["text"], line["to"], line["tone"]),
                         ("2 spends from the phone wait for a statement", "/settings?phone_rows=waiting", "warn"))
        pending.dismiss(self.conn, "imp1")
        eye = home.needs_your_eye(self.conn, {}, {"rows": [], "month": "2026-08"})
        self.assertEqual(eye["pending"], 1)
        line = next(l for l in eye["lines"] if l["kind"] == "pending")
        self.assertEqual(line["text"], "1 spend from the phone waits for a statement")


class ExpiryTest(unittest.TestCase):
    """A waiting row expires `expire_days` after its date (or after it last
    became waiting, when later), at a load or a read, never on a timer."""

    def setUp(self):
        self.conn = record()
        self.addCleanup(self.conn.close)
        # posted on the day of the spend, as the phone posts it
        with support.today_is(date(2026, 8, 3)):
            pending.post_rows(self.conn, "d", [spend(payee="NOBODY")])
        self.conn.execute("UPDATE pending_txn SET state_changed_at = '2026-08-03T02:00:00+00:00'")

    def state(self):
        return self.conn.execute("SELECT state, state_changed_at FROM pending_txn").fetchone()

    def test_a_row_waits_sixty_days_then_expires(self):
        self.assertEqual(pending.EXPIRE_DAYS, 60)
        self.assertEqual(pending.expire(self.conn, date(2026, 10, 1)), 0)      # day 59
        self.assertEqual(self.state()["state"], "waiting")
        self.assertEqual(pending.rows(self.conn)[0]["expires_on"], "2026-10-02")
        self.assertEqual(pending.waiting_count(self.conn, date(2026, 10, 1)), 1)
        # day 60: Home stops counting it even before anything writes the state
        self.assertEqual(pending.waiting_count(self.conn, date(2026, 10, 2)), 0)
        self.assertEqual(self.state()["state"], "waiting")
        self.assertEqual(pending.expire(self.conn, date(2026, 10, 2)), 1)
        st = self.state()
        self.assertEqual(st["state"], "expired")
        self.assertIsNone(pending.rows(self.conn)[0]["expires_on"])
        self.assertNotEqual(st["state_changed_at"], "2026-08-03T02:00:00+00:00")
        self.assertEqual(pending.expire(self.conn, date(2026, 10, 2)), 0)      # once

    def test_the_wait_is_the_records_setting(self):
        self.conn.execute("INSERT INTO setting(key, value) VALUES (?, ?)", (pending.EXPIRE_KEY, json.dumps(10)))
        self.assertEqual(pending.expire_days(self.conn), 10)
        self.assertEqual(pending.expire(self.conn, date(2026, 8, 13)), 1)
        for bad in ("0", "true", '"30"', "12.5", "not json"):
            self.conn.execute("UPDATE setting SET value = ? WHERE key = ?", (bad, pending.EXPIRE_KEY))
            self.assertEqual(pending.expire_days(self.conn), 60, bad)

    def test_an_expired_row_still_matches_a_late_statement(self):
        pending.expire(self.conn, date(2026, 12, 1))
        self.conn.execute("UPDATE pending_txn SET payee = 'Grab'")      # k1 is the same spend
        self.assertEqual(pending.confirm(self.conn), 1)
        r = pending.rows(self.conn)[0]
        self.assertEqual((r["state"], r["matched_txn_key"]), ("matched", "k1"))

    def test_put_back_gives_an_expired_row_a_full_wait(self):
        pending.expire(self.conn, date(2026, 12, 1))
        with support.today_is(date(2026, 12, 1)):
            pending.restore(self.conn, "imp1")
            self.assertEqual(self.state()["state"], "waiting")
            self.assertEqual(pending.expire(self.conn), 0)
            self.assertEqual(pending.waiting_count(self.conn), 1)
        # the clock runs from the put back, a local day
        back = pending._local_day(self.state()["state_changed_at"])
        self.assertEqual(pending.expire(self.conn, back + (date(2026, 10, 2) - date(2026, 8, 3))), 1)

    def test_a_load_matches_first_then_expires_what_is_due(self):
        from types import SimpleNamespace
        from unittest import mock
        from finance import local
        from finance import snapshot as snap
        from test_db import block, parsed
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.dict(os.environ, {"KOPIFUND_DATA": d}), \
                mock.patch.object(snap, "read_portfolio", return_value=None), \
                mock.patch.object(snap, "read_cpf", return_value=None), \
                closing(db.connect(":memory:")) as conn:
            cfg = SimpleNamespace(root=Path(d), folders=[], wallet_accounts=lambda: set())
            for i, payee in (("late", "Grab"), ("never", "NOBODY")):
                conn.execute("INSERT INTO pending_txn(import_id, txn_date, amount_minor, currency, payee, "
                             "created_at, state_changed_at) VALUES (?, '2026-08-03', -1250, 'SGD', ?, ?, ?)",
                             (i, payee, *("2026-08-03T02:00:00+00:00",) * 2))
            with support.today_is(date(2026, 12, 1)):
                counts = local.load(cfg, conn, parsed([block()], [row("k1", "-12.50")]))
            self.assertEqual((counts["phone rows matched"], counts["phone rows expired"]), (1, 1))
            self.assertEqual(dict(conn.execute("SELECT import_id, state FROM pending_txn").fetchall()),
                             {"late": "matched", "never": "expired"})

    def test_the_history_lists_every_row_newest_first_with_when_it_changed(self):
        with support.today_is(date(2026, 8, 20)):
            pending.post_rows(self.conn, "d", [spend("new", date="2026-08-19", payee="zzz"),
                                               spend("old", date="2026-08-01", payee="yyy")])
        pending.dismiss(self.conn, "old")
        out = pending.rows(self.conn)
        self.assertEqual([r["import_id"] for r in out], ["new", "imp1", "old"])
        self.assertEqual([r["state"] for r in out], ["waiting", "waiting", "dismissed"])
        for r in out:
            self.assertTrue(r["state_changed_at"] and r["posted_at"], r)
        self.assertGreater(out[2]["state_changed_at"], out[2]["posted_at"][:10])


    def test_a_row_put_back_meanwhile_is_not_expired_by_a_stale_look(self):
        # the first look sees the row due; a Put back lands before the write lock
        late = date(2026, 12, 1)
        real = pending.expire_days
        calls = []

        def days_then_put_back(conn):
            calls.append(1)
            if len(calls) == 2:            # between the look and the write
                self.conn.execute("UPDATE pending_txn SET state_changed_at = ? ", (f"{late}T02:00:00+00:00",))
            return real(conn)
        from unittest import mock
        with mock.patch.object(pending, "expire_days", days_then_put_back):
            self.assertEqual(pending.expire(self.conn, late), 0)
        self.assertEqual(self.state()["state"], "waiting")

    def test_a_day_past_the_calendar_is_refused_and_one_stored_never_expires(self):
        with self.assertRaises(ValueError):
            pending.post_rows(self.conn, "d", [spend("far", date="9999-12-31")])
        self.conn.execute("INSERT INTO pending_txn(import_id, txn_date, amount_minor, currency, created_at, "
                          "state_changed_at) VALUES ('far', '9999-12-31', -1, 'SGD', 'x', 'x')")
        self.assertEqual(pending.expire(self.conn, date(2026, 12, 1)), 1)          # imp1 only
        far = next(r for r in pending.rows(self.conn) if r["import_id"] == "far")
        self.assertEqual((far["state"], far["expires_on"]), ("waiting", "9999-12-31"))
        self.assertEqual(pending.waiting_count(self.conn, date(2026, 12, 1)), 1)


    def test_a_correction_to_an_expired_row_lands_and_waits_again(self):
        pending.expire(self.conn, date(2026, 12, 1))
        # an unchanged retry leaves it expired
        self.assertEqual(pending.post_rows(self.conn, "d", [spend(payee="NOBODY")])["accepted"], 0)
        self.assertEqual(self.state()["state"], "expired")
        out = pending.post_rows(self.conn, "d", [spend(payee="NOBODY", amount=-1300)])
        self.assertEqual(out["accepted"], 1)
        r = pending.rows(self.conn)[0]
        self.assertEqual((r["state"], r["amount_minor"]), ("waiting", -1300))


class UpgradeTest(unittest.TestCase):
    def test_a_version_7_row_takes_its_posted_stamp_and_the_new_state(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "k.db"
            db.connect(path).close()
            with closing(sqlite3.connect(path)) as c:      # pending_txn as version 7 built it
                c.executescript("""
DROP TABLE pending_txn;
CREATE TABLE pending_txn (
  id INTEGER PRIMARY KEY,
  import_id TEXT NOT NULL UNIQUE,
  device_id TEXT NOT NULL DEFAULT '',
  txn_date TEXT NOT NULL CHECK (date(txn_date) = txn_date),
  amount_minor INTEGER NOT NULL,
  currency TEXT NOT NULL CHECK (currency GLOB '[A-Z][A-Z][A-Z]'),
  payee TEXT NOT NULL DEFAULT '',
  state TEXT NOT NULL DEFAULT 'waiting' CHECK (state IN ('waiting', 'matched', 'dismissed')),
  matched_txn_key TEXT,
  rejected TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL
) STRICT;
INSERT INTO pending_txn(import_id, txn_date, amount_minor, currency, created_at)
  VALUES ('i', '2026-08-03', -1, 'SGD', '2026-08-03T01:00:00+00:00');
PRAGMA user_version = 7;
""")
                with self.assertRaises(sqlite3.IntegrityError):
                    c.execute("UPDATE pending_txn SET state = 'expired'")
            with closing(db.connect(path)) as conn:
                self.assertEqual(conn.execute("SELECT state_changed_at FROM pending_txn").fetchone()[0],
                                 "2026-08-03T01:00:00+00:00")
                conn.execute("UPDATE pending_txn SET state = 'expired'")


class ValidationTest(unittest.TestCase):
    def setUp(self):
        self.conn = record()
        self.addCleanup(self.conn.close)

    def test_rows_must_be_a_list_of_objects(self):
        with self.assertRaises(ValueError):
            pending.post_rows(self.conn, "d", "x")
        with self.assertRaises(ValueError):
            pending.post_rows(self.conn, "d", ["x"])

    def test_every_field_is_checked(self):
        good = spend()
        for change in ({"import_id": ""}, {"import_id": 5}, {"import_id": "x" * 201},
                       {"date": "03 Aug"}, {"date": "2026-13-01"}, {"date": 5},
                       {"amount_minor": "1250"}, {"amount_minor": 12.5}, {"amount_minor": True},
                       {"currency": "sgd"}, {"currency": "SG"}, {"currency": "SGDD"},
                       {"payee": 5}):
            with self.assertRaises(ValueError, msg=change):
                pending.post_rows(self.conn, "d", [{**good, **change}])

    def test_a_bad_row_writes_nothing(self):
        with self.assertRaises(ValueError):
            pending.post_rows(self.conn, "d", [spend(), spend("bad", amount="x")])
        # the first row was written in its own transaction before the bad one
        self.assertEqual([r[0] for r in self.conn.execute("SELECT import_id FROM pending_txn")],
                         ["imp1"])


class ApiTest(unittest.TestCase):
    """The routes the card and the phone call, through serve.write/answer."""

    def setUp(self):
        self.conn = record()                          # also warms the throwaway record path
        self.addCleanup(self.conn.close)
        # serve.write opens the record at KOPIFUND_DB (the suite's throwaway
        # file, shared across the suite): make its schema and leave no row behind
        conn = db.connect(os.environ["KOPIFUND_DB"])
        self.addCleanup(conn.close)
        self.addCleanup(conn.commit)
        self.addCleanup(conn.execute, "DELETE FROM pending_txn")

    def test_post_dismiss_restore_through_the_routes(self):
        out = support.write("/api/link/transactions", {},
                          b'{"rows": [{"import_id": "i1", "date": "2026-08-03", "amount_minor": -1250,'
                          b' "currency": "SGD", "payee": "NOBODY"}]}')
        self.assertEqual(out, {"accepted": 1, "matched": 0})
        listed = support.answer("/api/link/pending", {})
        self.assertEqual([(r["import_id"], r["state"]) for r in listed["rows"]], [("i1", "waiting")])
        self.assertEqual(listed["expire_days"], 60)
        out = support.write("/api/link/pending/dismiss", {}, b'{"import_id": "i1"}')
        self.assertEqual(out["rows"][0]["state"], "dismissed")
        out = support.write("/api/link/pending/restore", {}, b'{"import_id": "i1"}')
        self.assertEqual(out["rows"][0]["state"], "waiting")
        with self.assertRaises(NotFound):
            support.write("/api/link/pending/dismiss", {}, b'{"import_id": "nope"}')

    def test_the_read_expires_a_row_whose_wait_is_over(self):
        support.write("/api/link/transactions", {},
                      b'{"rows": [{"import_id": "i1", "date": "2026-08-03", "amount_minor": -1250,'
                      b' "currency": "SGD", "payee": "NOBODY"}]}')
        with support.today_is(date(2027, 3, 1)):
            rows = support.answer("/api/link/pending", {})["rows"]
        self.assertEqual(rows[0]["state"], "expired")
        out = support.write("/api/link/pending/restore", {}, b'{"import_id": "i1"}')
        self.assertEqual(out["rows"][0]["state"], "waiting")


if __name__ == "__main__":
    unittest.main()
