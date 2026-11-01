"""G17: schema version 5. The upgrade from version 4 (a table rebuild with
foreign keys off, idempotent, refusing rather than changing data), the
ON DELETE links, the CHECKs built from the Python lists, trip_country and
trip_pin, period.statement_file_id, category.bucket kept by triggers, and a
bucket's icon and colour."""
import isolate  # noqa: F401  first: no test writes the real record or cache
import hashlib
import json
import os
import re
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

from finance import api, categories, db, local, planning, spending, sync
from test_db import SNAP, block, parsed, row

REPO = Path(__file__).resolve().parents[2]
V4 = (REPO / "server" / "tests" / "fixtures" / "record-v4.sql").read_text(encoding="utf-8")

# invented rows in the version 4 shape: two trips (one with two countries in
# the order the owner gave them, and two pinned keys), a period whose source
# names a part of its file, an upload that kept that file, a budget, and a
# custom bucket with no shipped icon
V4_ROWS = """
INSERT INTO currency(code) VALUES ('SGD');
INSERT INTO account(id, name, account_no) VALUES (1, 'POSB', '111-11111-1');
INSERT INTO category_group(id, name, kind, sort_order) VALUES
  (1, 'Food', 'spend', 10), (2, 'Pets', 'spend', 15), (3, 'Other', 'spend', 90),
  (4, 'Income', 'income', 100), (5, 'Transfers', 'transfer', 110);
INSERT INTO category(id, name, bucket, grp, need_class, sort_order, group_id) VALUES
  (1, 'Eating out', 'Spend', 'Variable', 'Want', 10, 1),
  (2, 'Vet', 'Spend', 'Variable', 'Need', 20, 2),
  (3, 'Salary', 'Income', 'Fixed', 'Income', 30, 4),
  (4, 'Savings pot', 'Transfer', 'Savings', 'Savings', 40, 5);
INSERT INTO budget(category_id, month, planned_minor) VALUES (1, '2026-08', 30000), (2, '2026-08', 5000);
INSERT INTO trip(id, name, start_date, end_date, countries, extra_keys, notes) VALUES
  (1, 'Seoul', '2026-08-01', '2026-08-07', 'KOR,JPN', 'k2,k1', 'n'),
  (2, 'Staycation', '2026-09-01', '2026-09-02', '', '', '');
INSERT INTO statement_file(id, path, sha256, first_seen) VALUES
  (1, 'statements/posb/2026/2026-08.pdf', 'aa11', '2026-09-01T00:00:00+00:00');
INSERT INTO period(id, name, account_id, account_no, currency, source) VALUES
  (1, '111-11111-1 · SGD · 2026-08', 1, '111-11111-1', 'SGD', 'statements/posb/2026/2026-08.pdf#SGD'),
  (2, '111-11111-1 · SGD · 2026-09', 1, '111-11111-1', 'SGD', 'statements/posb/2026/2026-09.pdf');
INSERT INTO payee(id, name, kind) VALUES (1, 'Grab', 'Merchant');
INSERT INTO txn(id, txn_key, period_id, account_id, account_no, currency, txn_date, amount_minor,
                flow_class, payee_id, category_id, trip_id, first_seen, last_seen) VALUES
  (1, 'k1', 1, 1, '111-11111-1', 'SGD', '2026-08-03', -1250, 'External', 1, 1, 1, 'x', 'x'),
  (2, 'k2', 1, 1, '111-11111-1', 'SGD', '2026-08-04', -500, 'External', 1, 2, 1, 'x', 'x'),
  (3, 'k3', 1, 1, '111-11111-1', 'SGD', '2026-08-05', 900000, 'External', NULL, 3, NULL, 'x', 'x');
INSERT INTO upload_attempt(id, origin, started_at) VALUES (1, 'upload', 'x');
INSERT INTO upload_step(attempt_id, step, started_at, finished_at, status, input) VALUES
  (1, 'upload', 'x', 'x', 'ok', '{"file": "eStatement Aug.pdf", "sha256": "aa11"}');
INSERT INTO mirror_map(entity, local_id, notion_page_id) VALUES ('txn', 1, 'p1');
PRAGMA user_version = 4;
"""


class DataFolder(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.path = self.root / "kopifund.db"
        p = mock.patch.dict(os.environ, {"KOPIFUND_DATA": str(self.root), "KOPIFUND_DB": str(self.path)})
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)

    def v4_record(self, extra=""):
        with closing(sqlite3.connect(self.path)) as c:
            c.executescript(V4 + V4_ROWS + extra)

    def raw(self):
        return closing(sqlite3.connect(self.path))

    def version(self):
        with self.raw() as c:
            return c.execute("PRAGMA user_version").fetchone()[0]

    def content(self):
        """A hash of every row of every table, and the schema. SQLite quotes
        the name of a table a rebuild renamed into place, which is the same
        table: that quoting is left out."""
        h = hashlib.sha256()
        with self.raw() as c:
            for name, sql in c.execute("SELECT name, sql FROM sqlite_master ORDER BY type, name"):
                h.update(f"{name}{re.sub(r'^CREATE TABLE \"(\w+)\"', r'CREATE TABLE \1', sql or '')}".encode())
            for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"):
                for r in c.execute(f'SELECT * FROM "{t}" ORDER BY 1, 2'):
                    h.update(json.dumps([t, *r]).encode())
        return h.hexdigest()

    def backups(self):
        return sorted((self.root / "backups").glob("kopifund-*.db"))


class UpgradeTest(DataFolder):
    def test_a_version_4_record_keeps_every_row_and_gains_the_new_shape(self):
        self.v4_record()
        with self.raw() as c:
            before = {t: c.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for (t,) in c.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'") if t != "mirror_map"}
        with closing(db.connect()) as conn:
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], db.SCHEMA_VERSION)
            # SQLite's procedure: foreign keys back on, and nothing points nowhere
            self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            for t, n in before.items():
                self.assertEqual(conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0], n, t)
            # T9: the comma-separated columns became rows, the countries in order
            self.assertEqual(db.trip_countries(conn), {1: ["KOR", "JPN"]})
            self.assertEqual(db.trip_pins(conn), {1: ["k1", "k2"]})
            self.assertFalse({"countries", "extra_keys"} & db._columns(conn, "trip"))
            # T12: mirror_map is gone
            self.assertNotIn("mirror_map", db._tables(conn))
            # T10: each period points at its file; one with no recorded file at nothing
            self.assertEqual(dict(conn.execute("SELECT id, statement_file_id FROM period")), {1: 1, 2: None})
            # T6: every bucket is its group's; U1: shipped buckets get their icon, a custom one none
            self.assertEqual(dict(conn.execute("SELECT name, bucket FROM category")),
                             {"Eating out": "Spend", "Vet": "Spend", "Salary": "Income", "Savings pot": "Transfer"})
            self.assertEqual(dict(conn.execute("SELECT name, icon FROM category_group")),
                             {"Food": "utensils", "Pets": None, "Other": "shapes", "Income": "wallet",
                              "Transfers": "arrow-up-down"})
            self.assertEqual(conn.execute("SELECT trip_id FROM txn WHERE txn_key = 'k1'").fetchone()[0], 1)
        # a copy of the version 4 record is left beside it
        [copy] = self.backups()
        self.assertIn(f"v4-before-v{db.SCHEMA_VERSION}", copy.name)
        with closing(sqlite3.connect(copy)) as c:
            self.assertEqual(c.execute("PRAGMA user_version").fetchone()[0], 4)
            self.assertEqual(c.execute("SELECT countries FROM trip WHERE id = 1").fetchone()[0], "KOR,JPN")

    def test_the_upgrade_is_idempotent(self):
        self.v4_record()
        db.connect().close()
        once = self.content()
        db.connect().close()                       # at the version: nothing written
        self.assertEqual(self.content(), once)
        with self.raw() as c:                      # run it again on a record already in the new shape
            c.execute("PRAGMA user_version = 4")
            c.commit()
        db.connect().close()
        self.assertEqual(self.version(), db.SCHEMA_VERSION)
        self.assertEqual(self.content(), once)

    def test_a_value_outside_a_list_stops_the_upgrade_and_changes_nothing(self):
        self.v4_record("UPDATE category SET need_class = 'Luxury' WHERE id = 2;"
                       "UPDATE payee SET kind = 'Company';")
        before = self.content()
        with self.assertRaises(db.UpgradeRefused) as e:
            db.connect()
        self.assertIn("category.need_class holds 'Luxury'", str(e.exception))
        self.assertIn("payee.kind holds 'Company'", str(e.exception))
        self.assertEqual(self.content(), before)
        self.assertEqual(self.version(), 4)
        # put right by hand, the next open upgrades it
        with self.raw() as c:
            c.execute("UPDATE category SET need_class = 'Need' WHERE id = 2")
            c.execute("UPDATE payee SET kind = 'Merchant'")
            c.commit()
        db.connect().close()
        self.assertEqual(self.version(), db.SCHEMA_VERSION)

    def test_a_country_that_is_not_a_code_stops_the_upgrade(self):
        self.v4_record("UPDATE trip SET countries = 'Japan' WHERE id = 2;")
        with self.assertRaisesRegex(db.UpgradeRefused, "trip.countries holds 'Japan'"):
            db.connect()
        self.assertEqual(self.version(), 4)

    def test_a_link_to_a_row_that_is_not_there_stops_the_upgrade(self):
        # foreign keys are off in a plain sqlite3 connection, which is how such a row could get in
        self.v4_record("UPDATE txn SET trip_id = 99 WHERE id = 3;")
        before = self.content()
        with self.assertRaisesRegex(db.UpgradeRefused, r"txn -> trip"):
            db.connect()
        self.assertEqual(self.content(), before)

    def test_a_currency_the_rows_use_is_listed(self):
        self.v4_record("DELETE FROM currency;")
        with closing(db.connect()) as conn:
            self.assertEqual([r[0] for r in conn.execute("SELECT code FROM currency")], ["SGD"])


def record():
    """A version 5 record in memory with the test_db rows: one trip (MYS,
    pinned k9), a budget, two transactions."""
    conn = db.connect(":memory:")
    db.import_user_tables(conn, SNAP)
    db.load_parsed(conn, parsed([block()], [row("k1", "-12.50"), row("k2", "-5.00", category="Salary")]),
                   payees=[{"name": "Grab", "kind": "Merchant"}], trips_by_key={"k1": "Trip A"})
    return conn


class OnDeleteTest(unittest.TestCase):
    def setUp(self):
        self.conn = record()
        self.addCleanup(self.conn.close)

    def test_a_deleted_trip_lets_its_rows_go_and_takes_its_countries_and_pins(self):
        tid = self.conn.execute("SELECT id FROM trip").fetchone()[0]
        self.assertEqual(self.conn.execute("SELECT trip_id FROM txn WHERE txn_key = 'k1'").fetchone()[0], tid)
        self.conn.execute("DELETE FROM trip WHERE id = ?", (tid,))
        self.assertIsNone(self.conn.execute("SELECT trip_id FROM txn WHERE txn_key = 'k1'").fetchone()[0])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM trip_country").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM trip_pin").fetchone()[0], 0)

    def test_a_deleted_category_takes_its_budgets_and_lets_its_rows_go(self):
        cid = self.conn.execute("SELECT id FROM category WHERE name = 'Transport'").fetchone()[0]
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM budget WHERE category_id = ?", (cid,)).fetchone()[0], 1)
        self.conn.execute("DELETE FROM category WHERE id = ?", (cid,))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM budget WHERE category_id = ?", (cid,)).fetchone()[0], 0)
        self.assertIsNone(self.conn.execute("SELECT category_id FROM txn WHERE txn_key = 'k1'").fetchone()[0])

    def test_a_statement_file_gone_lets_its_periods_go(self):
        self.conn.execute("INSERT INTO statement_file(id, path, sha256, first_seen) "
                          "VALUES (7, 'statements/posb/2026/2026-08.pdf', 'aa', 'x')")
        db.link_statement_files(self.conn)
        self.assertEqual(self.conn.execute("SELECT statement_file_id FROM period").fetchone()[0], 7)
        self.conn.execute("DELETE FROM statement_file WHERE id = 7")
        self.assertIsNone(self.conn.execute("SELECT statement_file_id FROM period").fetchone()[0])

    def test_a_link_with_no_on_delete_still_refuses(self):
        # a payee rows point at is not deleted out from under them
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("DELETE FROM payee WHERE name = 'Grab'")

    def test_removing_a_trip_still_answers_what_an_undo_needs(self):
        out = planning.remove_trip(self.conn, "Trip A")
        self.assertEqual((out["restore"]["trip"]["countries"], out["restore"]["trip"]["extra_keys"],
                          out["restore"]["txn_keys"]), (["MYS"], ["k9"], ["k1"]))
        self.assertIsNone(self.conn.execute("SELECT trip_id FROM txn WHERE txn_key = 'k1'").fetchone()[0])
        back = planning.restore_trip(self.conn, out["restore"])
        self.assertEqual((db.trip_countries(self.conn, back["id"]), db.trip_pins(self.conn, back["id"])),
                         (["MYS"], ["k9"]))
        self.assertEqual(self.conn.execute("SELECT trip_id FROM txn WHERE txn_key = 'k1'").fetchone()[0], back["id"])

    def test_deleting_a_category_still_counts_and_moves_its_budgets(self):
        with mock.patch.object(categories, "_follow_rules", return_value=(0, "")):
            out = categories.delete_category(self.conn, "Transport", "Salary")
        self.assertEqual((out["transactions"], out["budgets"]), (1, 1))
        self.assertEqual([tuple(r) for r in self.conn.execute(
            "SELECT c.name, b.planned_minor FROM budget b JOIN category c ON c.id = b.category_id")],
            [("Salary", 5000)])


# the lists version 5's CHECKs were built with. A record keeps the CHECKs it
# was built with, so a value added to one of these tuples is refused by
# every record until a new SCHEMA_VERSION rebuilds its tables: change this
# only together with SCHEMA_VERSION.
V5_LISTS = {
    "group_kind": ("spend", "income", "transfer"),
    "bucket": ("Spend", "Income", "Transfer"),
    "cost_type": ("Fixed", "Variable", "Savings"),
    "need_class": ("Need", "Want", "Savings", "Investment", "Income", "Transfer"),
    "payee_kind": ("Merchant", "Person", "Employer", "Institution"),
    "origin": ("upload", "rules", "transactions", "app"),
    "step": ("upload", "check", "load", "mirror"),
    "step_status": ("ok", "duplicate", "refused", "problems", "blocked", "failed", "error"),
}
# G16: and version 6's, with series_override's
V6_LISTS = {**V5_LISTS, "series_group": ("payee", "text", "amount"),
            "cadence": ("weekly", "fortnightly", "twice-monthly", "monthly", "two-monthly", "quarterly",
                        "half-yearly", "yearly")}
# G25: version 7 kept version 6's lists; pending_txn's states are an inline
# CHECK, not a Python list
V7_LISTS = V6_LISTS


class CheckTest(unittest.TestCase):
    def setUp(self):
        self.conn = record()
        self.addCleanup(self.conn.close)
        self.conn.execute("INSERT INTO upload_attempt(id, started_at) VALUES (1, 'x')")
        self.conn.execute("INSERT INTO upload_step(id, attempt_id, step, started_at, finished_at, status) "
                          "VALUES (1, 1, 'load', 'x', 'x', 'ok')")
        # G16 (version 6): a series' edit
        self.conn.execute("INSERT INTO series_override(id, series_id, group_kind, group_name, saved_at) "
                          "VALUES (1, 'payee:Gym:5000', 'payee', 'Gym', 'x')")

    # a write that puts `value` into each checked column
    WRITES = {
        ("category_group", "kind"): "INSERT INTO category_group(name, kind) VALUES ('Probe', ?)",
        ("category", "bucket"): "UPDATE category SET bucket = ? WHERE name = 'Transport'",
        ("category", "grp"): "UPDATE category SET grp = ? WHERE name = 'Transport'",
        ("category", "need_class"): "UPDATE category SET need_class = ? WHERE name = 'Transport'",
        ("payee", "kind"): "UPDATE payee SET kind = ? WHERE name = 'Grab'",
        ("upload_attempt", "origin"): "UPDATE upload_attempt SET origin = ? WHERE id = 1",
        ("upload_step", "step"): "UPDATE upload_step SET step = ? WHERE id = 1",
        ("upload_step", "status"): "UPDATE upload_step SET status = ? WHERE id = 1",
        ("series_override", "group_kind"): "UPDATE series_override SET group_kind = ? WHERE id = 1",
        ("series_override", "cadence"): "UPDATE series_override SET cadence = ? WHERE id = 1",
    }

    def test_every_listed_column_refuses_a_value_outside_its_list_and_takes_every_one_in_it(self):
        lists = db.enums()
        self.assertEqual({(t, c) for t, c, _ in db.CHECKED}, set(self.WRITES))
        for table, column, key in db.CHECKED:
            sql = self.WRITES[(table, column)]
            with self.subTest(f"{table}.{column}"):
                with self.assertRaisesRegex(sqlite3.IntegrityError, "CHECK constraint failed"):
                    self.conn.execute(sql, ("Not a value",))
                for v in lists[key]:
                    self.conn.execute("SAVEPOINT probe")
                    self.conn.execute(sql, (v,))
                    self.conn.execute("ROLLBACK TO probe")
                    self.conn.execute("RELEASE probe")

    def test_the_checks_are_built_from_the_python_lists(self):
        from finance import planning as plan, upload_log
        from finance.rules_app import categorise
        lists = db.enums()
        self.assertIs(lists["need_class"], plan.NEED_CLASSES)
        self.assertIs(lists["payee_kind"], categorise.KINDS)
        self.assertIs(lists["step_status"], upload_log.STATUSES)
        self.assertIs(lists["group_kind"], categories.KINDS)
        self.assertEqual(set(db.BUCKET_OF_KIND), set(categories.KINDS))
        sql = self.conn.execute("SELECT sql FROM sqlite_master WHERE name = 'upload_step'").fetchone()[0]
        self.assertIn("'duplicate'", sql)

    def test_the_lists_are_the_ones_version_7_was_built_with(self):
        # G25: version 7 kept version 6's lists and added pending_txn
        self.assertEqual(db.SCHEMA_VERSION, 7, "a new version: copy its lists into a V8_LISTS")
        self.assertEqual(db.enums(), V7_LISTS,
                         "a record built at version 7 refuses a value added since: bump SCHEMA_VERSION "
                         "so the upgrade rebuilds the tables with the new list")


class BucketFollowsTheGroupTest(unittest.TestCase):
    def setUp(self):
        self.conn = record()
        self.addCleanup(self.conn.close)

    def bucket(self, name):
        return self.conn.execute("SELECT bucket FROM category WHERE name = ?", (name,)).fetchone()[0]

    def test_a_new_category_takes_its_groups_bucket(self):
        categories.add_category(self.conn, "Side gig", "Income")
        self.assertEqual(self.bucket("Side gig"), "Income")

    def test_a_bucket_written_by_hand_is_put_back(self):
        self.conn.execute("UPDATE category SET bucket = 'Spend' WHERE name = 'Salary'")
        self.assertEqual(self.bucket("Salary"), "Income")

    def test_a_move_and_a_change_of_kind_carry_the_bucket(self):
        categories.move_category(self.conn, "Transport", "Transfers")
        self.assertEqual(self.bucket("Transport"), "Transfer")
        gid = self.conn.execute("SELECT group_id FROM category WHERE name = 'Transport'").fetchone()[0]
        self.conn.execute("UPDATE category_group SET kind = 'income' WHERE id = ?", (gid,))
        self.assertEqual(self.bucket("Transport"), "Income")

    def test_a_notion_import_still_moves_a_category_to_the_kind_it_names(self):
        db.import_user_tables(self.conn, {"categories": [
            {"name": "Transport", "bucket": "Transfer", "group": "Variable", "need_class": "Transfer"}]})
        self.assertEqual(self.bucket("Transport"), "Transfer")
        self.assertEqual(self.conn.execute(
            """SELECT g.name FROM category c JOIN category_group g ON g.id = c.group_id
               WHERE c.name = 'Transport'""").fetchone()[0], "Transfers")


class PinsFollowAReKeyTest(unittest.TestCase):
    def test_a_pinned_key_the_parser_changed_stays_on_its_trip(self):
        conn = record()
        self.addCleanup(conn.close)
        tid = conn.execute("SELECT id FROM trip").fetchone()[0]
        db.set_trip_links(conn, tid, ["MYS"], ["old-k2"])
        conn.execute("INSERT INTO txn_key_alias(old_key, txn_id) SELECT 'old-k2', id FROM txn WHERE txn_key = 'k2'")
        self.assertEqual(db.trip_pins(conn, tid), ["k2"])
        self.assertEqual(db.trip_pins(conn, tid, follow=False), ["old-k2"])
        [trip] = local.trips_from_db(conn)
        self.assertIn("k2", trip.keys)
        # a load places the row by the pin, whatever its date
        r = dict(row("k2", "-5.00", date="2026-12-25"))
        self.assertEqual(sync.trip_for(r, [trip], set()).name, "Trip A")
        self.assertEqual(api.trips(conn, long_stay=60)["trips"][0]["extra_keys"], ["k2"])


class StatementFileTest(DataFolder):
    def test_a_load_writes_the_parser_and_the_uploaded_name_and_links_the_periods(self):
        folder = self.root / "statements" / "posb" / "2026"
        folder.mkdir(parents=True)
        aug, sep = folder / "2026-08.pdf", folder / "2026-09.pdf"
        aug.write_bytes(b"%PDF aug")
        sep.write_bytes(b"%PDF sep")
        with closing(db.connect()) as conn:
            db.load_parsed(conn, parsed([block(source="statements/posb/2026/2026-08.pdf#SGD"),
                                         block(period="111-11111-1 · SGD · 2026-09",
                                               source="statements/posb/2026/2026-09.pdf")], []))
            attempt = conn.execute("INSERT INTO upload_attempt(started_at) VALUES ('x')").lastrowid
            conn.execute("INSERT INTO upload_step(attempt_id, step, started_at, finished_at, status, input) "
                         "VALUES (?, 'upload', 'x', 'x', 'ok', ?)",
                         (attempt, json.dumps({"file": "eStatement.pdf",
                                               "sha256": hashlib.sha256(b"%PDF aug").hexdigest()})))
            db.load_statements_meta(conn, self.root, [aug, sep], {aug: "dbs", sep: "dbs"})
            files = {r["path"]: r for r in conn.execute("SELECT * FROM statement_file")}
            self.assertEqual({p: (f["parser"], f["original_name"]) for p, f in files.items()},
                             {"statements/posb/2026/2026-08.pdf": ("dbs", "eStatement.pdf"),
                              "statements/posb/2026/2026-09.pdf": ("dbs", "2026-09.pdf")})
            linked = dict(conn.execute("SELECT name, statement_file_id FROM period"))
            self.assertEqual(linked, {"111-11111-1 · SGD · 2026-08": files["statements/posb/2026/2026-08.pdf"]["id"],
                                      "111-11111-1 · SGD · 2026-09": files["statements/posb/2026/2026-09.pdf"]["id"]})
            # renamed on disk: the row follows, its original name and the period's link with it
            moved = folder / "posb-aug.pdf"
            aug.rename(moved)
            conn.execute("UPDATE period SET source = 'statements/posb/2026/posb-aug.pdf' "
                         "WHERE name = '111-11111-1 · SGD · 2026-08'")
            db.load_statements_meta(conn, self.root, [moved, sep], {moved: "dbs", sep: "dbs"})
            row_ = conn.execute("SELECT id, original_name FROM statement_file "
                                "WHERE path = 'statements/posb/2026/posb-aug.pdf'").fetchone()
            self.assertEqual(row_["original_name"], "eStatement.pdf")
            self.assertEqual(conn.execute("SELECT statement_file_id FROM period "
                                          "WHERE name = '111-11111-1 · SGD · 2026-08'").fetchone()[0], row_["id"])


class BucketStyleTest(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        self.addCleanup(self.conn.close)

    def groups(self):
        return {g["name"]: g for g in categories.overview(self.conn)["groups"]}

    def test_every_shipped_bucket_has_an_icon_from_the_list_and_the_default_colour(self):
        shipped = db.shipped_category_groups()
        self.assertTrue(all(g.get("icon") in categories.BUCKET_ICONS for g in shipped))
        self.assertEqual(len({g["icon"] for g in shipped}), len(shipped), "two shipped buckets share an icon")
        got = self.groups()
        for g in shipped:
            self.assertEqual((got[g["name"]]["icon"], got[g["name"]]["colour"]), (g["icon"], None))

    def test_a_bucket_with_no_icon_shows_its_kinds(self):
        categories.add_group(self.conn, "Pets")
        self.assertEqual(self.groups()["Pets"]["icon"], categories.DEFAULT_ICON["spend"])

    def test_a_style_is_set_checked_and_put_back(self):
        food = self.groups()["Food"]
        out = categories.set_group_style(self.conn, "Food", food["id"], icon="coffee", colour="c3")
        self.assertEqual((out["icon"], out["colour"]), ("coffee", "c3"))
        categories.set_group_style(self.conn, group_id=food["id"], colour=None)    # the icon is kept
        self.assertEqual((self.groups()["Food"]["icon"], self.groups()["Food"]["colour"]), ("coffee", None))
        for bad in ({"icon": "rocket-ship"}, {"colour": "c13"}, {"colour": "red"}):
            with self.subTest(bad), self.assertRaises(categories.CategoryRefused):
                categories.set_group_style(self.conn, group_id=food["id"], **bad)
        with self.assertRaises(categories.CategoryRefused):
            categories.set_group_style(self.conn, "Snacks", food["id"], icon="gift")   # not its name

    def test_spending_sends_each_spend_buckets_style(self):
        conn = record()
        self.addCleanup(conn.close)
        gid = conn.execute("SELECT id FROM category_group WHERE name = 'Getting around'").fetchone()[0]
        categories.set_group_style(conn, group_id=gid, colour="c7")
        out = spending.explore(conn, {"from": "2026-08", "to": "2026-08"})
        self.assertEqual(list(out["bucket_style"]), out["bucket_order"])
        self.assertEqual(out["bucket_style"]["Getting around"], {"icon": "bus", "colour": "c7", "shown": "c7"})
        # G18: Food keeps its place's colour; the bucket whose place gave c7 took Getting around's old one
        self.assertEqual(out["bucket_style"]["Food"], {"icon": "utensils", "colour": None, "shown": "c1"})

    def test_the_colours_are_the_series_tokens_and_the_icons_exist(self):
        css = (REPO / "apps" / "web" / "src" / "index.css").read_text(encoding="utf-8")
        root = css[css.index(":root"):]
        root = root[:root.index("}")]
        self.assertEqual(tuple(f"c{n}" for n in sorted({int(m) for m in re.findall(r"--c(\d+):", root)})),
                         categories.SERIES_COLOURS)
        icons = REPO / "apps" / "web" / "node_modules" / "lucide-react" / "dist" / "esm" / "icons"
        if not icons.is_dir():
            self.skipTest("apps/web/node_modules is not installed (make install)")
        missing = [n for n in categories.BUCKET_ICONS if not (icons / f"{n}.js").is_file()]
        self.assertEqual(missing, [])


class StyleEndpointTest(DataFolder):
    def test_the_style_write_names_the_bucket_by_id_and_refuses_what_is_not_listed(self):
        import serve
        db.connect().close()
        with closing(db.connect()) as conn:
            gid = conn.execute("SELECT id FROM category_group WHERE name = 'Giving'").fetchone()[0]
        out = serve.write("/api/category-groups/style", {},
                          json.dumps({"id": gid, "name": "Giving", "colour": "c11"}).encode())
        self.assertEqual(out, {"id": gid, "name": "Giving", "icon": "gift", "colour": "c11", "shown": "c11",
                               "swapped": "Transfers"})
        with self.assertRaises(serve.Refused):
            serve.write("/api/category-groups/style", {}, json.dumps({"id": gid, "icon": "rocket-ship"}).encode())
        with self.assertRaises(ValueError):
            serve.write("/api/category-groups/style", {}, json.dumps({"id": gid}).encode())


if __name__ == "__main__":
    unittest.main()
