"""server/finance/db.py and server/finance/local.py: the SQLite record."""
import isolate  # noqa: F401  first: no test writes the real record or cache
import unittest
from contextlib import closing

from finance import db, local, sync


def block(**kw):
    out = dict(period="111-11111-1 · SGD · 2026-08", period_end="2026-08-31", account="POSB",
               account_no="111-11111-1", currency="SGD", opening="100.00", closing="70.50",
               total_in="0.00", total_out="29.50", fx_to_sgd="", closing_sgd="70.50",
               n_txns="2", reconciled="True", note="", source="statements/posb/2026/2026-08.pdf")
    out.update(kw)
    return out


def row(key, amount, **kw):
    out = dict(txn_key=key, date="2026-08-03", account="POSB", account_no="111-11111-1",
               currency="SGD", bank_txn_type="Debit Card", direction="Out", amount=amount,
               flow_class="External", merchant="GRAB*123", merchant_country="SGP",
               purchase_date="", card_last4="", fx_original="", counterparty="", reference="",
               memo="", balance_after="", source="statements/posb/2026/2026-08.pdf",
               period="111-11111-1 · SGD · 2026-08", payee="Grab", category="Transport", rule="r1")
    out.update(kw)
    return out


def parsed(blocks, rows):
    p = sync.Parsed()
    p.blocks, p.rows = blocks, rows
    return p


SNAP = {"categories": [{"name": "Transport", "bucket": "Spend", "group": "Variable",
                        "need_class": "Need", "sort_order": 100},
                       {"name": "Salary", "bucket": "Income", "group": "Fixed",
                        "need_class": "Income", "sort_order": 10}],
        "accounts": [{"name": "POSB", "account_no": "111-11111-1", "institution": "DBS",
                      "class": "Asset", "subclass": "Cash", "base_currency": "SGD"}],
        "cards": [{"name": "Debit", "last4": "4242", "network": "Visa", "type": "Debit",
                   "state": "Active"}],
        "budgets": [{"category": "Transport", "month": "2026-08", "planned_minor": 5000}],
        "trips": [{"name": "Trip A", "start": "2026-08-01", "end": "2026-08-05",
                   "countries": ["MYS"], "keys": ["k9"]}]}


class ValuesTest(unittest.TestCase):
    def test_money_is_integer_minor_units(self):
        self.assertEqual(db.to_minor("-12.50"), -1250)
        self.assertEqual(db.to_minor(0.1 + 0.2), 30)
        self.assertIsNone(db.to_minor(""))

    def test_a_fraction_of_a_cent_is_refused(self):
        with self.assertRaises(ValueError):
            db.to_minor("1.005")

    def test_rates_are_scaled_by_a_billion(self):
        self.assertEqual(db.rate_e9("0.7867"), 786_700_000)
        self.assertIsNone(db.rate_e9(None))


class RecordTest(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        db.import_user_tables(self.conn, {k: [dict(x) for x in v] for k, v in SNAP.items()},
                              plan={"dca": 100})

    def load(self, rows, blocks=None, **kw):
        return db.load_parsed(self.conn, parsed(blocks or [block()], rows),
                              payees=[{"name": "Grab", "kind": "Merchant", "country": "SGP"}], **kw)

    def test_rows_land_with_ids_not_names(self):
        self.load([row("k1", "-12.50"), row("k2", "-17.00")])
        r = self.conn.execute("SELECT * FROM v_txn WHERE txn_key='k1'").fetchone()
        self.assertEqual(r["amount_minor"], -1250)
        self.assertEqual((r["payee_name"], r["category_name"], r["account_name"]),
                         ("Grab", "Transport", "POSB"))
        self.assertIsInstance(r["category_id"], int)

    def test_a_second_identical_load_changes_nothing(self):
        self.load([row("k1", "-12.50")])
        counts = self.load([row("k1", "-12.50")])
        self.assertEqual(counts, {"inserted": 0, "updated": 0, "archived": 0, "rekeyed": 0})

    def test_a_changed_row_is_updated_in_place(self):
        self.load([row("k1", "-12.50")])
        counts = self.load([row("k1", "-12.50", category="Salary")])
        self.assertEqual(counts["updated"], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM txn").fetchone()[0], 1)

    def test_a_row_the_parser_stops_producing_is_archived_not_deleted(self):
        self.load([row("k1", "-12.50"), row("k2", "-17.00")])
        counts = self.load([row("k1", "-12.50")])
        self.assertEqual(counts["archived"], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM txn").fetchone()[0], 2)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM v_txn").fetchone()[0], 1)

    def test_an_incomplete_run_archives_nothing(self):
        self.load([row("k1", "-12.50"), row("k2", "-17.00")])
        counts = self.load([row("k1", "-12.50")], complete=False)
        self.assertEqual(counts["archived"], 0)

    def test_a_failing_load_leaves_the_record_as_it_was(self):
        self.load([row("k1", "-12.50")])
        with self.assertRaises(ValueError):
            self.load([row("k1", "-12.50"), row("k3", "1.005")])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM txn").fetchone()[0], 1)

    def test_a_foreign_block_without_a_rate_has_no_sgd_value(self):
        b = block(period="X · AUD · 2026-08", currency="AUD", account_no="X", fx_to_sgd="")
        self.load([row("k1", "-10.00", currency="AUD", account_no="X", period="X · AUD · 2026-08")],
                  blocks=[b])
        r = self.conn.execute("SELECT amount_sgd_minor FROM v_txn").fetchone()
        self.assertIsNone(r["amount_sgd_minor"])
        s = db.snapshot(self.conn)
        self.assertIsNone(s["transactions"][0]["amount_sgd_minor"])      # G20: unknown, in cents
        self.assertNotIn("amount_sgd", s["transactions"][0])
        self.assertIn("no FX rate", s["meta"]["notes"][0])

    def test_a_foreign_block_with_a_rate_converts(self):
        b = block(period="X · USD · 2026-08", currency="USD", account_no="X", fx_to_sgd="1.35")
        self.load([row("k1", "-10.00", currency="USD", account_no="X", period="X · USD · 2026-08")],
                  blocks=[b])
        self.assertEqual(self.conn.execute("SELECT amount_sgd_minor FROM v_txn").fetchone()[0], -1350)
        # G20: the snapshot row carries the record's cents, and no dollars
        t = db.snapshot(self.conn)["transactions"][0]
        self.assertEqual((t["amount_minor"], t["amount_sgd_minor"]), (-1000, -1350))
        self.assertFalse({"amount", "amount_sgd"} & set(t))

    def test_reconciled_uses_the_statement_control_totals(self):
        self.load([row("k1", "-12.50"), row("k2", "-17.00")])
        p = self.conn.execute("SELECT reconciled, closing_sgd_minor FROM v_period").fetchone()
        self.assertEqual((p["reconciled"], p["closing_sgd_minor"]), (1, 7050))

    def test_budget_actual_is_external_spend_signed_positive(self):
        self.load([row("k1", "-12.50"), row("k2", "-17.00"),
                   row("k3", "-5.00", flow_class="Internal - own account")])
        b = self.conn.execute("SELECT * FROM v_budget").fetchone()
        self.assertEqual((b["planned_minor"], b["actual_minor"]), (5000, 2950))

    def test_the_snapshot_has_the_shape_the_stats_read(self):
        self.load([row("k1", "-12.50")])
        s = db.snapshot(self.conn)
        for key in ("meta", "transactions", "periods", "payees", "categories", "accounts",
                    "cards", "budgets", "trips", "portfolio", "cpf"):
            self.assertIn(key, s)
        t = s["transactions"][0]
        self.assertEqual((t["amount_minor"], t["amount_sgd_minor"], t["need_class"], t["month"]),
                         (-1250, -1250, "Need", "2026-08"))
        # G20: no amount anywhere in a snapshot is a float in dollars
        b = s["periods"][0]
        self.assertEqual((b["closing_minor"], b["closing_sgd_minor"]), (7050, 7050))
        self.assertIs(type(b["opening_minor"]), int)
        for part in ("transactions", "periods", "budgets"):
            for r in s[part]:
                self.assertFalse([k for k, v in r.items() if isinstance(v, float) and k != "fx_to_sgd"], (part, r))
        self.assertEqual(s["budgets"][0]["planned_minor"], 5000)
        self.assertEqual(s["meta"]["source"], "sqlite")
        self.assertEqual(s["payees"][0]["default_category"], "Transport")

    def test_user_tables_survive_a_load(self):
        self.load([row("k1", "-12.50")])
        self.assertEqual(self.conn.execute("SELECT planned_minor FROM budget").fetchone()[0], 5000)
        self.assertEqual(db.load_plan(self.conn), {"dca": 100})


class TripsTest(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        db.import_user_tables(self.conn, {k: [dict(x) for x in v] for k, v in SNAP.items()})

    def test_trips_come_back_as_the_sync_s_trip_objects(self):
        trips = local.trips_from_db(self.conn)
        self.assertEqual(trips[0].name, "Trip A")
        self.assertEqual(trips[0].countries, ("MYS",))
        self.assertEqual(trips[0].keys, frozenset({"k9"}))

    def test_assignment_is_the_sync_s_rule(self):
        trips = local.trips_from_db(self.conn)
        abroad = row("k1", "-10.00", merchant_country="MYS")
        home = row("k2", "-10.00", merchant_country="SGP")
        pinned = row("k9", "-99.00", date="2026-07-01")
        self.assertEqual(sync.trip_for(abroad, trips, set()).name, "Trip A")
        self.assertIsNone(sync.trip_for(home, trips, set()))
        self.assertEqual(sync.trip_for(pinned, trips, set()).name, "Trip A")


class SeedCategoriesTest(unittest.TestCase):
    def test_blanks_are_filled_and_set_values_kept(self):
        conn = db.connect(":memory:")
        conn.execute("INSERT INTO category(name) VALUES ('Transport')")
        conn.execute("INSERT INTO category(name, bucket, grp, need_class, sort_order) "
                     "VALUES ('Groceries', 'Spend', 'Fixed', 'Want', 1)")
        db.seed_categories(conn, [
            {"name": "Transport", "bucket": "Spend", "group": "Variable", "need_class": "Need", "sort_order": 5},
            {"name": "Groceries", "bucket": "Spend", "group": "Variable", "need_class": "Need", "sort_order": 2},
            {"name": "Salary", "bucket": "Income", "group": "Fixed", "need_class": "Income", "sort_order": 9}])
        got = {r[0]: r[1:] for r in conn.execute("SELECT name, bucket, grp, need_class, sort_order FROM category")}
        self.assertEqual(got["Transport"], ("Spend", "Variable", "Need", 5))
        self.assertEqual(got["Groceries"], ("Spend", "Fixed", "Want", 1))
        self.assertEqual(got["Salary"], ("Income", "Fixed", "Income", 9))

    def test_a_renamed_shipped_category_keeps_its_rows(self):
        conn = db.connect(":memory:")
        conn.execute("INSERT INTO category(name, bucket, grp, need_class, sort_order) "
                     "VALUES ('Tuition', 'Spend', 'Fixed', 'Need', 90)")
        old_id = conn.execute("SELECT id FROM category WHERE name='Tuition'").fetchone()[0]
        db.seed_categories(conn, [{"name": "Education", "bucket": "Spend", "group": "Fixed",
                                   "need_class": "Need", "sort_order": 90}])
        names = {r[0]: r[1] for r in conn.execute("SELECT name, id FROM category")}
        self.assertEqual(names, {"Education": old_id})

    def test_a_record_that_has_both_names_is_left_alone(self):
        conn = db.connect(":memory:")
        conn.executemany("INSERT INTO category(name) VALUES (?)", [("Tuition",), ("Education",)])
        db.seed_categories(conn, [])
        self.assertEqual({r[0] for r in conn.execute("SELECT name FROM category")}, {"Tuition", "Education"})

    def test_a_row_whose_rule_says_the_old_name_lands_under_the_new_one(self):
        conn = db.connect(":memory:")
        education = conn.execute("INSERT INTO category(name) VALUES ('Education')").lastrowid
        self.assertEqual(db.category_ids(conn)["Tuition"], education)
        tuition = conn.execute("INSERT INTO category(name) VALUES ('Tuition')").lastrowid
        self.assertEqual(db.category_ids(conn)["Tuition"], tuition)   # kept alongside: left alone
        self.assertNotIn("Groceries", db.category_ids(conn))

    def test_the_rules_and_the_record_rename_the_same_categories(self):
        from finance.parsers import categorise
        self.assertEqual(categorise.RENAMED_CATEGORIES, db.RENAMED_CATEGORIES)

    def test_the_shipped_list_has_a_bucket_for_every_category(self):
        self.assertTrue(all(c.get("bucket") for c in db.shipped_categories()))


class RecordCategoriesTest(unittest.TestCase):
    def test_a_category_the_user_added_is_accepted_by_the_rules(self):
        import os
        import tempfile
        from pathlib import Path
        from unittest import mock
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"KOPIFUND_DATA": d, "KOPIFUND_DB": d + "/kopifund.db"}):
            conn = db.connect()
            conn.execute("INSERT INTO category(name) VALUES ('Pets')")
            conn.commit()
            conn.close()
            self.assertIn("Pets", sync.record_categories())
            rules = Path(d) / "keys.csv"
            rules.write_text("match_on,pattern,payee,kind,country,category,notes\n"
                             "key,abc123,Vet,Merchant,SGP,Pets,\n")
            parsed = sync.Parsed()
            sync.categorise(parsed, rules)
            self.assertEqual(parsed.problems, [])

    def test_without_a_record_the_shipped_list_is_used(self):
        import os
        import tempfile
        from unittest import mock
        from finance.parsers import categorise
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"KOPIFUND_DATA": d, "KOPIFUND_DB": d + "/kopifund.db"}):
            self.assertEqual(sync.record_categories(), list(categorise.KNOWN_CATEGORIES))


class ImportReviewTest(unittest.TestCase):
    """The one-time import (and the read-back until G8), as #5's review asked."""

    def setUp(self):
        self.conn = db.connect(":memory:")
        db.import_user_tables(self.conn, {k: [dict(x) for x in v] for k, v in SNAP.items()})

    def snap(self, **changes):
        out = {k: [dict(x) for x in v] for k, v in SNAP.items()}
        out.update(changes)
        return out

    def test_a_renamed_account_updates_its_row(self):
        renamed = [{**SNAP["accounts"][0], "name": "POSB Savings"}]
        db.import_user_tables(self.conn, self.snap(accounts=renamed))
        self.assertEqual([r[0] for r in self.conn.execute("SELECT name FROM account")], ["POSB Savings"])

    def test_a_trip_gone_from_the_import_stops_claiming_rows(self):
        db.load_parsed(self.conn, parsed([block()], [row("k1", "-12.50")]), trips_by_key={"k1": "Trip A"})
        db.import_user_tables(self.conn, self.snap(trips=[]))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM trip").fetchone()[0], 0)
        self.assertIsNone(self.conn.execute("SELECT trip_id FROM txn").fetchone()[0])

    def test_trip_notes_are_carried(self):
        db.import_user_tables(self.conn, self.snap(trips=[{**SNAP["trips"][0], "notes": "with family"}]))
        self.assertEqual(self.conn.execute("SELECT notes FROM trip").fetchone()[0], "with family")


class LoadReviewTest(unittest.TestCase):
    def setUp(self):
        import os
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace
        from unittest import mock
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"KOPIFUND_DATA": self.tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.root = Path(self.tmp.name)
        self.cfg = SimpleNamespace(root=self.root, folders=[], wallet_accounts=lambda: set())
        self.conn = db.connect(":memory:")
        self.addCleanup(self.conn.close)

    def test_a_failure_after_the_rows_writes_nothing(self):
        from unittest import mock
        from finance import snapshot as snap
        with mock.patch.object(snap, "read_portfolio", return_value=None), \
                mock.patch.object(snap, "read_cpf", return_value=None), \
                mock.patch.object(db, "load_broker_and_cpf", side_effect=RuntimeError("cpf")):
            with self.assertRaises(RuntimeError):
                local.load(self.cfg, self.conn, parsed([block()], [row("k1", "-12.50")]))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM txn").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM sync_run").fetchone()[0], 0)

    def test_a_load_leaves_the_app_owned_plan_alone(self):
        from unittest import mock
        from finance import snapshot as snap
        db.save_plan(self.conn, {"dca": 100})
        (self.root / "config.toml").write_text('[plan]\nto = "2027-03"\ndca = 250\n')
        with mock.patch.object(snap, "read_portfolio", return_value=None), \
                mock.patch.object(snap, "read_cpf", return_value=None):
            local.load(self.cfg, self.conn, parsed([block()], [row("k1", "-12.50")]))
        self.assertEqual(db.load_plan(self.conn)["dca"], 100.0)


class SqliteSnapshotTest(unittest.TestCase):
    def test_a_sqlite_snapshot_is_rebuilt_every_time(self):
        import tempfile
        from pathlib import Path
        from unittest import mock
        from analytics import run as analytics_run
        from finance import snapshot as snap
        with tempfile.TemporaryDirectory() as d:
            old = Path(d) / "snapshot-2026-10-01.json"
            old.write_text('{"meta": {"source": "sqlite"}, "transactions": []}')
            fresh = {"meta": {"source": "sqlite"}, "transactions": [1]}
            with mock.patch.object(snap, "today_snapshot", return_value=old), \
                    mock.patch.object(snap, "build", return_value=fresh) as build, \
                    mock.patch.object(snap, "save", return_value=old):
                s, _ = analytics_run.build_or_reuse(None, source="sqlite", cache=Path(d),
                                                    echo=lambda *_: None)
        build.assert_called_once()
        self.assertEqual(s["transactions"], [1])


class SchemaVersionTest(unittest.TestCase):
    """connect() reads the record's user_version before it writes anything."""

    def setUp(self):
        import tempfile
        from pathlib import Path
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "kopifund.db"

    def tearDown(self):
        self.tmp.cleanup()

    def version(self):
        import sqlite3
        with closing(sqlite3.connect(self.path)) as c:
            return c.execute("PRAGMA user_version").fetchone()[0]

    def test_a_new_record_gets_the_version(self):
        db.connect(self.path).close()
        self.assertEqual(self.version(), db.SCHEMA_VERSION)

    def test_a_matching_record_is_opened_without_a_write(self):
        import sqlite3
        db.connect(self.path).close()
        with closing(sqlite3.connect(self.path)) as watcher:
            # data_version moves when another connection commits to the file
            before = watcher.execute("PRAGMA data_version").fetchone()[0]
            conn = db.connect(self.path)
            conn.execute("SELECT count(*) FROM txn").fetchone()
            conn.close()
            after = watcher.execute("PRAGMA data_version").fetchone()[0]
        self.assertEqual(before, after)

    def test_an_older_record_is_brought_up_to_the_version(self):
        from unittest import mock
        db.connect(self.path).close()
        with mock.patch.object(db, "SCHEMA_VERSION", db.SCHEMA_VERSION + 1):
            db.connect(self.path).close()
            self.assertEqual(self.version(), db.SCHEMA_VERSION)

    def test_a_newer_record_is_refused(self):
        import sqlite3
        db.connect(self.path).close()
        with closing(sqlite3.connect(self.path)) as c:
            c.execute(f"PRAGMA user_version = {db.SCHEMA_VERSION + 1}")
        with self.assertRaisesRegex(db.RecordTooNew, "update Kopifund"):
            db.connect(self.path)
        self.assertEqual(self.version(), db.SCHEMA_VERSION + 1)

    def test_a_refused_record_keeps_its_journal_mode(self):
        import sqlite3
        with closing(sqlite3.connect(self.path)) as c:
            c.execute(f"PRAGMA user_version = {db.SCHEMA_VERSION + 1}")
        with self.assertRaises(db.RecordTooNew):
            db.connect(self.path)
        with closing(sqlite3.connect(self.path)) as c:
            self.assertEqual(c.execute("PRAGMA journal_mode").fetchone()[0], "delete")

    def test_the_schema_does_not_change_without_its_version(self):
        # A record already at SCHEMA_VERSION skips the schema script, so an edit
        # to SCHEMA alone would never reach it. Bump SCHEMA_VERSION (with any
        # migration the change needs), then update this pair. G17: the script
        # as a record runs it, every enum's list filled in, so a value added to
        # one of those tuples fails here too.
        import hashlib
        self.assertEqual(
            (db.SCHEMA_VERSION, hashlib.sha256(db.schema().encode()).hexdigest()),
            (7, "adb75a3f512944a80b2eb28588719aff10d2f6f8ce0d0d7e7e3d3708ef8b6624"))

    def test_a_version_1_record_gains_the_upload_history(self):
        import sqlite3
        db.connect(self.path).close()
        with closing(sqlite3.connect(self.path)) as c:      # the record as version 1 left it
            c.executescript("DROP TABLE upload_step; DROP TABLE upload_attempt; PRAGMA user_version = 1;")
        conn = db.connect(self.path)
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        conn.close()
        self.assertLessEqual({"upload_attempt", "upload_step"}, names)
        self.assertEqual(self.version(), db.SCHEMA_VERSION)


if __name__ == "__main__":
    unittest.main()
