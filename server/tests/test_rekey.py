"""A row the parser gives a new key stays the same row (audit: txn_key_alias).

txn_key hashes parsed text, so a town added to places.csv, or a parser change
to merchant splitting, renames a row the statement prints just as before. The
load keeps the row's id, records the old key in txn_key_alias, and everything
saved on the old key (a trip pin, a mark, a series edit, a keys.csv rule)
follows. A row that would leave the record carrying any of those stops the run.
All rows here are invented."""
import isolate  # noqa: F401  first: no test writes the real record or cache
import os
import tempfile
import unittest
from pathlib import Path

from finance import db, relink, scam_checks as sc, sync
from finance.parsers import categorise, places, parse_youtrip as yt
from finance.parsers.parse_statements import add_keys
from test_db import SNAP, block, parsed, row


def wallet_row():
    """One NOK YouTrip card payment, before enrich."""
    return dict(source="s", account="YouTrip wallet", currency="NOK", date="2025-07-01",
                lines=["NARVESEN,STORGATA 1,BODO"], fx_rate=None, withdrawal=10.0, deposit=None,
                balance_after=90.0, synthetic=False, account_no="Y-1", amount=-10.0, direction="Out")


class Record(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        self.addCleanup(self.conn.close)
        db.import_user_tables(self.conn, {k: [dict(x) for x in v] for k, v in SNAP.items()})

    def load(self, rows, **kw):
        return db.load_parsed(self.conn, parsed([block()], rows), **kw)

    def ids(self):
        return {r["txn_key"]: (r["id"], r["archived_at"]) for r in self.conn.execute("SELECT * FROM txn")}


class ReKeyTest(Record):
    def test_a_re_keyed_row_keeps_its_id_and_its_old_key_becomes_an_alias(self):
        self.load([row("old", "-12.50", merchant="NARVESEN,STORGATA 1,BODO")])
        tid = self.ids()["old"][0]
        counts = self.load([row("new", "-12.50", merchant="NARVESEN,STORGATA 1")])
        self.assertEqual((counts["inserted"], counts["archived"], counts["rekeyed"]), (0, 0, 1))
        self.assertEqual(self.ids(), {"new": (tid, None)})
        self.assertEqual(dict(self.conn.execute("SELECT old_key, txn_id FROM txn_key_alias")), {"old": tid})
        self.assertEqual(self.conn.execute("SELECT merchant_raw FROM txn").fetchone()[0], "NARVESEN,STORGATA 1")

    def test_a_trip_pin_and_a_mark_follow_the_re_key(self):
        self.load([row("old", "-12.50")])
        tid = self.conn.execute("SELECT id FROM trip").fetchone()[0]
        db.set_trip_links(self.conn, tid, ["MYS"], ["old"])
        sc.mark_fine(self.conn, "old")
        self.load([row("new", "-12.50", merchant="GRAB*456")])
        self.assertEqual(db.trip_pins(self.conn, tid), ["new"])
        self.assertEqual([r["key"] for r in sc.page(self.conn)["marked_fine"]], ["new"])

    def test_a_chain_of_re_keys_ends_on_the_row(self):
        self.load([row("a", "-12.50")])
        tid = self.ids()["a"][0]
        self.load([row("b", "-12.50", merchant="X")])
        self.load([row("c", "-12.50", merchant="Y")])
        self.assertEqual(dict(self.conn.execute("SELECT old_key, txn_id FROM txn_key_alias")),
                         {"a": tid, "b": tid})
        self.assertEqual(db.former_keys(self.conn), {"c": ["a", "b"]})

    def test_a_key_that_comes_back_is_no_longer_its_own_alias(self):
        self.load([row("a", "-12.50")])
        self.load([row("b", "-12.50", merchant="X")])
        self.load([row("a", "-12.50")])
        self.assertEqual(dict(self.conn.execute("SELECT old_key, txn_id FROM txn_key_alias")),
                         {"b": self.ids()["a"][0]})

    def test_identical_rows_pair_in_order(self):
        self.load([row("a1", "-3.00"), row("a2", "-3.00")])
        first, second = self.ids()["a1"][0], self.ids()["a2"][0]
        counts = self.load([row("b1", "-3.00", merchant="X"), row("b2", "-3.00", merchant="X")])
        self.assertEqual(counts["rekeyed"], 2)
        self.assertEqual({k: v[0] for k, v in self.ids().items()}, {"b1": first, "b2": second})

    def test_a_different_amount_or_date_is_a_different_row(self):
        self.load([row("a", "-12.50"), row("d", "-1.00")])
        counts = self.load([row("b", "-12.60"), row("e", "-1.00", date="2026-08-04")])
        self.assertEqual((counts["rekeyed"], counts["inserted"], counts["archived"]), (0, 2, 2))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM txn_key_alias").fetchone()[0], 0)

    def test_an_uneven_group_is_not_guessed(self):
        self.load([row("a1", "-3.00"), row("a2", "-3.00")])
        counts = self.load([row("b1", "-3.00", merchant="X")])
        self.assertEqual((counts["rekeyed"], counts["inserted"], counts["archived"]), (0, 1, 2))

    def test_a_town_added_to_places_re_keys_a_wallet_row_and_its_pin_follows(self):
        # the audit's case: BODO in places.csv moves the city out of the merchant
        places.load(None)
        self.addCleanup(places.load, None)
        before = yt.enrich(wallet_row())
        add_keys([before])
        path = Path(tempfile.mkdtemp()) / "places.csv"
        path.write_text("place,country,match\nBODO,NOR,prefix\n", encoding="utf-8")
        places.load(str(path))
        after = yt.enrich(wallet_row())
        add_keys([after])
        self.assertNotEqual(before["txn_key"], after["txn_key"])
        nok = dict(period="Y-1 · NOK · 2025-07", account="YouTrip wallet", account_no="Y-1", currency="NOK",
                   source="s", period_end="2025-07-31")
        for r in (before, after):
            r.update(period=nok["period"], bank_txn_type=r.get("bank_txn_type") or "YouTrip Payment",
                     purchase_date="", card_last4="", fx_original="", counterparty="", reference="", memo="")
        db.load_parsed(self.conn, parsed([block(**nok)], [before]))
        tid = self.conn.execute("SELECT id FROM trip").fetchone()[0]
        db.set_trip_links(self.conn, tid, ["NOR"], [before["txn_key"]])
        db.load_parsed(self.conn, parsed([block(**nok)], [after]))
        self.assertEqual(db.trip_pins(self.conn, tid), [after["txn_key"]])


class KeyRulesFollowTest(Record):
    RULES = [{"match_on": "key", "pattern": "old", "glob": "OLD", "payee": "Grab", "kind": "Merchant",
              "country": "SGP", "category": "Transport", "notes": "", "account_no": "", "recurring": "",
              "file": "keys.csv", "line": 2}]

    def test_a_key_rule_matches_a_row_by_a_key_it_had_before(self):
        r = row("new", "-12.50", payee="", category="", rule="", _former_keys=["old"])
        categorise.categorise_rows([r], self.RULES)
        self.assertEqual((r["payee"], r["category"], r["rule"]), ("Grab", "Transport", "key:old"))

    def test_the_load_that_re_keys_a_row_already_knows_its_old_key(self):
        self.load([row("old", "-12.50")])
        fresh = row("new", "-12.50", merchant="X")
        self.assertEqual(db.former_keys(self.conn, [fresh]), {"new": ["old"]})

    def test_a_relink_reads_the_record_rows_with_their_old_keys(self):
        self.load([row("old", "-12.50")])
        self.load([row("new", "-12.50", merchant="X")])
        [r] = relink.record_rows(self.conn)
        self.assertEqual(r["_former_keys"], ["old"])
        self.assertEqual(relink.categorised([r], self.RULES), [("Grab", "Transport", "key:old")])

    def test_the_lint_does_not_call_the_rule_dead(self):
        r = row("new", "-12.50", _former_keys=["old"])
        found = categorise.lint([r], self.RULES)
        self.assertFalse([line for lines in found.values() for line in lines if "matches no row" in line], found)


class PinnedRowsGuardTest(Record):
    def run_of(self, *rows, rules=()):
        p = parsed([block()], list(rows))
        p.rules = list(rules)
        return p

    def test_a_pinned_row_that_would_leave_stops_the_load(self):
        self.load([row("k1", "-12.50"), row("k2", "-5.00")])
        tid = self.conn.execute("SELECT id FROM trip").fetchone()[0]
        db.set_trip_links(self.conn, tid, ["MYS"], ["k2"])
        sc.mark_fine(self.conn, "k2")
        [p] = sync.check_pinned_rows(self.conn, self.run_of(row("k1", "-12.50")))
        self.assertEqual(p.code, "pinned-rows-gone")
        self.assertEqual(p.subject, "1 row(s): k2 (a trip pin, marked fine)")

    def test_a_row_with_nothing_saved_on_it_may_leave(self):
        self.load([row("k1", "-12.50"), row("k2", "-5.00")])
        self.assertEqual(sync.check_pinned_rows(self.conn, self.run_of(row("k1", "-12.50"))), [])

    def test_a_re_keyed_pinned_row_is_not_leaving(self):
        self.load([row("k1", "-12.50")])
        tid = self.conn.execute("SELECT id FROM trip").fetchone()[0]
        db.set_trip_links(self.conn, tid, ["MYS"], ["k1"])
        self.assertEqual(sync.check_pinned_rows(self.conn, self.run_of(row("k9", "-12.50", merchant="X"))), [])

    def test_a_pin_saved_under_an_old_key_still_counts(self):
        self.load([row("k1", "-12.50")])
        self.load([row("k2", "-12.50", merchant="X")])
        oid = self.conn.execute("""INSERT INTO series_override(group_kind, group_name, recurring, saved_at)
                                   VALUES ('payee', 'Grab', 1, 'now')""").lastrowid
        self.conn.execute("INSERT INTO series_override_txn(override_id, txn_key) VALUES (?, 'k1')", (oid,))
        [p] = sync.check_pinned_rows(self.conn, self.run_of())
        self.assertEqual(p.subject, "1 row(s): k2 (a series edit)")

    def test_a_key_rule_on_a_leaving_row_counts(self):
        self.load([row("k1", "-12.50"), row("k2", "-5.00")])
        [p] = sync.check_pinned_rows(self.conn, self.run_of(row("k1", "-12.50"),
                                                            rules=[dict(KeyRulesFollowTest.RULES[0], pattern="k2", glob="K2")]))
        self.assertEqual(p.subject, "1 row(s): k2 (a key rule)")


class RecordGuardTest(unittest.TestCase):
    def test_the_guard_reads_the_data_folders_record(self):
        path = db.db_path()
        if path.exists():
            os.remove(path)
        self.addCleanup(lambda: path.exists() and os.remove(path))
        conn = db.connect()
        db.import_user_tables(conn, {k: [dict(x) for x in v] for k, v in SNAP.items()})
        db.load_parsed(conn, parsed([block()], [row("k1", "-12.50"), row("k2", "-5.00")]))
        tid = conn.execute("SELECT id FROM trip").fetchone()[0]
        with db.transaction(conn):
            db.set_trip_links(conn, tid, ["MYS"], ["k2"])
        conn.close()
        run = parsed([block()], [row("k1", "-12.50")])
        self.assertIn("pinned-rows-gone", [p.code for p in sync.check_record_pins(run)])


if __name__ == "__main__":
    unittest.main()
