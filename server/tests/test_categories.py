"""Buckets and the Categories page: the version 3 migration, the shipped
buckets, and server/finance/categories.py's edits (rename, move, delete with what
moves along)."""
import isolate  # noqa: F401  first: no test writes the real record or cache
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

from finance import categories, db, planning, rules_app, stats_rebuild
from test_db import block, parsed, row

# the category table as version 2 left it, with the categories it held
V2_CATEGORY = """CREATE TABLE category (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE,
                 bucket TEXT, grp TEXT, need_class TEXT, sort_order REAL) STRICT;"""


class DataFolder(unittest.TestCase):
    """A temporary data folder holding the record, so nothing touches the real one."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.path = self.root / "kopifund.db"
        p = mock.patch.dict(os.environ, {"KOPIFUND_DATA": str(self.root), "KOPIFUND_DB": str(self.path)})
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)

    def v2_record(self, rows):
        with closing(sqlite3.connect(self.path)) as c:
            c.executescript(V2_CATEGORY)
            c.executemany("INSERT INTO category(name, bucket, grp, need_class, sort_order) VALUES (?,?,?,?,?)",
                          rows)
            c.execute("PRAGMA user_version = 2")
            c.commit()

    def backups(self):
        return sorted((self.root / "backups").glob("kopifund-*.db"))


def shipped_rows():
    return [(c["name"], c["bucket"], c["group"], c["need_class"], c["sort_order"])
            for c in db.shipped_categories()]


class MigrationTest(DataFolder):
    def test_a_version_2_record_gets_the_shipped_buckets_and_a_backup_first(self):
        self.v2_record(shipped_rows() + [("Pets", "Spend", "Variable", "Want", 300),
                                         ("Side gig", "Income", "Variable", "Income", 310),
                                         ("Savings pot", "Transfer", "Savings", "Savings", 320)])
        conn = db.connect()
        self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], db.SCHEMA_VERSION)
        placed = {r[0]: r[1] for r in conn.execute(
            "SELECT c.name, g.name FROM category c JOIN category_group g ON g.id = c.group_id")}
        conn.close()
        shipped = {c["name"]: c["category_group"] for c in db.shipped_categories()}
        for name, group in shipped.items():
            self.assertEqual(placed[name], group, name)
        # unknown categories go by what the stats already count them as
        self.assertEqual((placed["Pets"], placed["Side gig"], placed["Savings pot"]),
                         ("Other", "Income", "Transfers"))
        [copy] = self.backups()
        self.assertIn(f"v2-before-v{db.SCHEMA_VERSION}", copy.name)
        with closing(sqlite3.connect(copy)) as c:
            self.assertEqual(c.execute("PRAGMA user_version").fetchone()[0], 2)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM category").fetchone()[0], len(shipped_rows()) + 3)
            self.assertNotIn("group_id", {r[1] for r in c.execute("PRAGMA table_info(category)")})
        self.assertEqual(oct(copy.stat().st_mode & 0o777), "0o600")

    def test_the_migration_never_changes_what_the_stats_count(self):
        # a shipped category the owner had made a transfer stays a transfer
        rows = [r if r[0] != "Coffee" else ("Coffee", "Transfer", "Variable", "Transfer", 160)
                for r in shipped_rows()]
        self.v2_record(rows)
        with closing(db.connect()) as conn:
            got = conn.execute("""SELECT c.bucket, g.name, g.kind FROM category c
                                  JOIN category_group g ON g.id = c.group_id WHERE c.name = 'Coffee'""").fetchone()
        self.assertEqual(tuple(got), ("Transfer", "Transfers", "transfer"))

    def test_a_failed_upgrade_leaves_the_record_at_version_2(self):
        self.v2_record(shipped_rows())
        with mock.patch.object(db, "assign_category_groups", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                db.connect()
        with closing(sqlite3.connect(self.path)) as c:
            self.assertEqual(c.execute("PRAGMA user_version").fetchone()[0], 2)
            self.assertNotIn("group_id", {r[1] for r in c.execute("PRAGMA table_info(category)")})
            tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        self.assertNotIn("category_group", tables)
        # and the next open tries again, from the same backup-first start
        with closing(db.connect()) as conn:
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], db.SCHEMA_VERSION)
        self.assertEqual(len(self.backups()), 2)

    def test_a_backup_that_fails_stops_the_upgrade(self):
        self.v2_record(shipped_rows())
        with mock.patch("finance.backup.backup_record", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                db.connect()
        with closing(sqlite3.connect(self.path)) as c:
            self.assertEqual(c.execute("PRAGMA user_version").fetchone()[0], 2)

    def test_requests_that_open_the_old_record_at_once_copy_and_upgrade_it_once(self):
        import threading
        self.v2_record(shipped_rows())
        errors, start = [], threading.Barrier(4)

        def open_it():
            try:
                start.wait()
                db.connect().close()
            except Exception as e:          # noqa: BLE001  any failure fails the test
                errors.append(e)
        threads = [threading.Thread(target=open_it) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        self.assertEqual(errors, [])
        self.assertEqual(len(self.backups()), 1)
        with closing(db.connect()) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM category_group").fetchone()[0],
                             len(db.shipped_category_groups()))

    def test_a_new_record_is_not_backed_up(self):
        db.connect().close()
        self.assertEqual(self.backups(), [])

    def test_the_shipped_list_puts_every_category_in_a_shipped_bucket(self):
        groups = {g["name"]: g["kind"] for g in db.shipped_category_groups()}
        for c in db.shipped_categories():
            self.assertIn(c["category_group"], groups, c["name"])
            self.assertEqual(db.BUCKET_OF_KIND[groups[c["category_group"]]], c["bucket"], c["name"])
        self.assertEqual([k for k in groups.values() if k != "spend"], ["income", "transfer"])


def record():
    """A record with the shipped categories and buckets, two Coffee rows, one
    Eating out row, budgets for both, in the data folder."""
    conn = db.connect()
    db.seed_categories(conn)
    db.load_parsed(conn, parsed([block()], [
        row("k1", "-4.50", category="Coffee", payee="", merchant="TOAST BOX"),
        row("k2", "-5.00", category="Coffee", payee="", merchant="TOAST BOX", date="2026-08-09"),
        row("k3", "-20.00", category="Eating out", payee="", merchant="DIN TAI FUNG")]))
    planning.set_budget(conn, "Coffee", "2026-08", 3000)
    planning.set_budget(conn, "Eating out", "2026-08", 20000)
    planning.set_budget(conn, "Coffee", "2026-09", 2500)
    return conn


class EditTest(DataFolder):
    def setUp(self):
        super().setUp()
        self.conn = record()
        self.addCleanup(self.conn.close)
        self.rules = self.root / "rules"
        self.rules.mkdir()
        (self.rules / "merchants.csv").write_bytes(
            b"match_on,pattern,payee,kind,country,category,notes\r\n"
            b"merchant,TOAST BOX*,Toast Box,Merchant,SGP,Coffee,\r\n"
            b"merchant,YA KUN*,Ya Kun,Merchant,SGP,Coffee,\"a note, with a comma\"\r\n"
            b"merchant,DIN TAI*,Din Tai Fung,Merchant,SGP,Eating out,\r\n")

    def page(self):
        return categories.overview(self.conn)

    def group_of(self, name):
        return next(g for g in self.page()["groups"] if any(c["name"] == name for c in g["categories"]))

    def cat(self, name):
        return next(c for g in self.page()["groups"] for c in g["categories"] if c["name"] == name)

    def test_the_page_lists_buckets_in_order_with_what_each_category_touches(self):
        page = self.page()
        self.assertEqual([g["name"] for g in page["groups"]][:3], ["Food", "Home and bills", "Getting around"])
        self.assertEqual([g["fixed"] for g in page["groups"] if g["kind"] != "spend"], [True, True])
        coffee = self.cat("Coffee")
        self.assertEqual((coffee["transactions"], coffee["monthly"], coffee["budgets"], coffee["personal_rules"]),
                         (2, 950, 2, 2))
        self.assertEqual(page["need_classes"]["spend"], ["Need", "Want"])

    def test_need_classes_follow_the_bucket_kind(self):
        categories.set_need_class(self.conn, "Coffee", "Need")
        self.assertEqual(self.cat("Coffee")["need_class"], "Need")
        for name, bad in (("Coffee", "Income"), ("Salary", "Want"), ("Investments", "Need"), ("Coffee", None)):
            with self.assertRaises(categories.CategoryRefused, msg=(name, bad)):
                categories.set_need_class(self.conn, name, bad)
        categories.set_need_class(self.conn, "Investments", "Savings")

    def test_add_rename_and_delete_a_bucket(self):
        categories.add_group(self.conn, "Pets")
        names = [g["name"] for g in self.page()["groups"]]
        self.assertLess(names.index("Pets"), names.index("Income"))
        categories.add_category(self.conn, "Vet", "Pets")
        categories.rename_group(self.conn, "Pets", "Animals")
        with self.assertRaisesRegex(categories.CategoryRefused, "choose the bucket"):
            categories.delete_group(self.conn, "Animals")
        out = categories.delete_group(self.conn, "Animals", move_to="Lifestyle")
        self.assertEqual(out["moved"], 1)
        self.assertEqual(self.group_of("Vet")["name"], "Lifestyle")

    def test_income_and_transfers_cannot_be_deleted_or_doubled(self):
        for name in ("Income", "Transfers"):
            with self.assertRaisesRegex(categories.CategoryRefused, "cannot be deleted"):
                categories.delete_group(self.conn, name, move_to="Food")
        for bad in ("Food", "food", "", "(none)", "x" * 61):
            with self.assertRaises(categories.CategoryRefused, msg=bad):
                categories.add_group(self.conn, bad)

    def test_reorder_buckets_and_categories(self):
        names = [g["name"] for g in self.page()["groups"]]
        categories.reorder_groups(self.conn, [names[1], names[0], *names[2:]])
        self.assertEqual([g["name"] for g in self.page()["groups"]][:2], ["Home and bills", "Food"])
        with self.assertRaises(categories.CategoryRefused):
            categories.reorder_groups(self.conn, names[:-1])
        categories.move_category(self.conn, "Coffee", "Food", 0)
        self.assertEqual([c["name"] for c in self.group_of("Coffee")["categories"]],
                         ["Coffee", "Groceries", "Eating out"])
        # every list in the app sorts on sort_order, so it follows the page
        order = [r[0] for r in self.conn.execute("SELECT name FROM category ORDER BY sort_order")]
        self.assertEqual(order[:5], ["Rent", "Phone and utilities", "Insurance", "Fees and charges", "Coffee"])

    def test_moving_into_another_kind_changes_what_the_stats_count(self):
        categories.move_category(self.conn, "Coffee", "Transfers")
        c = self.conn.execute("SELECT bucket, need_class FROM category WHERE name = 'Coffee'").fetchone()
        self.assertEqual(tuple(c), ("Transfer", "Transfer"))
        categories.move_category(self.conn, "Coffee", "Food")
        c = self.conn.execute("SELECT bucket, need_class FROM category WHERE name = 'Coffee'").fetchone()
        self.assertEqual(tuple(c), ("Spend", "Want"))
        self.assertEqual(db.snapshot(self.conn)["transactions"][0]["category_group"], "Food")

    def test_rename_keeps_rows_budgets_and_rewrites_the_personal_rules(self):
        out = categories.rename_category(self.conn, "Coffee", "Kopi")
        self.assertEqual(out["rule_files"], 1)
        self.assertEqual(self.cat("Kopi")["transactions"], 2)
        self.assertEqual(self.conn.execute("""SELECT COUNT(*) FROM budget b JOIN category c
                                              ON c.id = b.category_id WHERE c.name = 'Kopi'""").fetchone()[0], 2)
        text = (self.rules / "merchants.csv").read_bytes()
        self.assertEqual(text.count(b",Kopi,"), 2)
        self.assertIn(b'"a note, with a comma"\r\n', text)       # quoting and line ends kept
        self.assertIn(b"merchant,DIN TAI*,Din Tai Fung,Merchant,SGP,Eating out,\r\n", text)
        # one write in the Rules page's undo history, and undo puts the old name back
        self.assertEqual(rules_app.history()[0]["summary"], "Renamed category Coffee to Kopi")
        rules_app.undo()
        self.assertEqual((self.rules / "merchants.csv").read_bytes().count(b",Coffee,"), 2)
        rules_app._load_all(self.conn)            # the old name still loads: it is an alias
        # a shared rule still says Coffee: it lands on Kopi, and the seed does not bring Coffee back
        self.assertEqual(db.category_ids(self.conn)["Coffee"], db.category_ids(self.conn)["Kopi"])
        db.seed_categories(self.conn)
        self.assertIsNone(self.conn.execute("SELECT 1 FROM category WHERE name = 'Coffee'").fetchone())
        self.assertIn("Coffee", rules_app.categories(self.conn) + db.category_alias_names(self.conn))

    def test_the_rules_page_sees_nothing_pending_after_a_rename(self):
        from finance import rules_editor
        before = rules_editor.overview(self.conn)["pending"]
        categories.rename_category(self.conn, "Coffee", "Kopi")
        # a shared rule may still say Coffee: the record files its rows in Kopi
        self.assertEqual(rules_editor._namer(self.conn)("Coffee"), "Kopi")
        self.assertEqual(rules_editor.overview(self.conn)["pending"], before)

    def test_a_reload_after_a_rename_keeps_the_rows_on_the_renamed_category(self):
        categories.rename_category(self.conn, "Coffee", "Kopi")
        db.load_parsed(self.conn, parsed([block()], [
            row("k1", "-4.50", category="Coffee", payee="", merchant="TOAST BOX")]), complete=False)
        got = self.conn.execute("SELECT category_name FROM v_txn WHERE txn_key = 'k1'").fetchone()[0]
        self.assertEqual(got, "Kopi")

    def test_delete_moves_rows_budgets_and_rules_to_the_chosen_category(self):
        with self.assertRaisesRegex(categories.CategoryRefused, "Choose the category"):
            categories.delete_category(self.conn, "Coffee", None)
        with self.assertRaises(categories.CategoryRefused):
            categories.delete_category(self.conn, "Coffee", "Coffee")
        out = categories.delete_category(self.conn, "Coffee", "Eating out")
        self.assertEqual((out["transactions"], out["budgets"], out["rule_files"]), (2, 2, 1))
        self.assertEqual(self.cat("Eating out")["transactions"], 3)
        budgets = dict(self.conn.execute("""SELECT b.month, b.planned_minor FROM budget b JOIN category c
                                            ON c.id = b.category_id WHERE c.name = 'Eating out'"""))
        self.assertEqual(budgets, {"2026-08": 23000, "2026-09": 2500})
        self.assertEqual((self.rules / "merchants.csv").read_bytes().count(b",Eating out,"), 3)
        self.assertEqual(len(self.backups()), 1)              # the record, before the delete
        db.seed_categories(self.conn)
        self.assertIsNone(self.conn.execute("SELECT 1 FROM category WHERE name = 'Coffee'").fetchone())
        # adding the name again makes it a category of its own once more
        categories.add_category(self.conn, "Coffee", "Food")
        self.assertNotEqual(db.category_ids(self.conn)["Coffee"], db.category_ids(self.conn)["Eating out"])

    def test_a_category_nothing_points_at_is_deleted_without_a_place_to_move_to(self):
        categories.add_category(self.conn, "Pets", "Food")
        out = categories.delete_category(self.conn, "Pets", None)
        self.assertEqual((out["deleted"], out["transactions"], out["budgets"]), ("Pets", 0, 0))
        self.assertIsNone(self.conn.execute("SELECT 1 FROM category WHERE name = 'Pets'").fetchone())
        self.assertEqual(len(self.backups()), 1)
        # the name stays as an alias, so the shipped list never brings back a shipped one deleted this way
        rows = (self.conn.execute("SELECT * FROM category WHERE name = ?", (c["name"],)).fetchone() for c in db.shipped_categories())
        shipped = next(r["name"] for r in rows if r and categories._nothing_to_move(self.conn, r))
        categories.delete_category(self.conn, shipped, None)
        db.seed_categories(self.conn)
        self.assertIsNone(self.conn.execute("SELECT 1 FROM category WHERE name = ?", (shipped,)).fetchone())
        # one with rows, budgets or a rule still has to say where they go
        with self.assertRaisesRegex(categories.CategoryRefused, "Choose the category"):
            categories.delete_category(self.conn, "Coffee", None)
        self.assertIsNotNone(self.conn.execute("SELECT 1 FROM category WHERE name = 'Coffee'").fetchone())

    def test_the_shipped_rename_never_undoes_the_user_s_own(self):
        categories.rename_category(self.conn, "Education", "Tuition")
        db.seed_categories(self.conn)
        self.assertEqual(self.cat("Tuition")["name"], "Tuition")
        self.assertIsNone(self.conn.execute("SELECT 1 FROM category WHERE name = 'Education'").fetchone())

    def test_a_reorder_never_changes_what_the_stats_count(self):
        # a category with no bucket column (a Notion import without one) sits in
        # Other, and G17 (T6): its bucket is then Other's, so the stats count what
        # the page shows; moving it among spending buckets keeps it Spend
        self.conn.execute("INSERT INTO category(name, need_class) VALUES ('Mystery', NULL)")
        db.assign_category_groups(self.conn)
        self.assertEqual(self.group_of("Mystery")["name"], "Other")
        categories.move_category(self.conn, "Mystery", "Other", 0)
        categories.move_category(self.conn, "Mystery", "Lifestyle")
        self.assertEqual(self.conn.execute("SELECT bucket FROM category WHERE name = 'Mystery'").fetchone()[0],
                         "Spend")

    def test_an_import_that_brings_a_deleted_category_back_ends_its_alias(self):
        categories.delete_category(self.conn, "Coffee", "Eating out")
        db.import_user_tables(self.conn, {"categories": [
            {"name": "Coffee", "bucket": "Spend", "group": "Variable", "need_class": "Want", "sort_order": 160}]})
        self.assertNotIn("Coffee", db.category_alias_names(self.conn))
        self.assertEqual(self.group_of("Coffee")["name"], "Food")

    def test_rules_that_will_not_load_are_put_back_and_the_old_name_keeps_working(self):
        before = (self.rules / "merchants.csv").read_bytes()
        with mock.patch.object(rules_app, "_trial", side_effect=rules_app.RuleRefused("bad set")):
            out = categories.delete_category(self.conn, "Coffee", "Eating out")
        self.assertEqual((self.rules / "merchants.csv").read_bytes(), before)
        self.assertEqual(out["rule_files"], 0)
        self.assertIn("still say Coffee", out["rules_note"])
        # the record has the change, and the rules that still say Coffee load and land there
        self.assertEqual(self.cat("Eating out")["transactions"], 3)
        rules = rules_app._load_all(self.conn)
        self.assertIn("Coffee", {r["category"] for r in rules if r["file"] == "merchants.csv"})
        self.assertEqual(db.category_ids(self.conn)["Coffee"], db.category_ids(self.conn)["Eating out"])

    def test_a_check_between_the_commit_and_the_rules_rewrite_still_loads(self):
        # a category of the user's own (no shipped name to fall back on), named by a rule
        categories.add_category(self.conn, "Kopi", "Food")
        with (self.rules / "merchants.csv").open("ab") as fh:
            fh.write(b"merchant,YA KUN KAYA*,Ya Kun,Merchant,SGP,Kopi,\r\n")
        rules_app._load_all(self.conn)
        # the record has committed the rename, the files still say the old name
        with mock.patch.object(categories, "_follow_rules", return_value=(0, "")):
            categories.rename_category(self.conn, "Kopi", "Kopi O")
        self.assertIn(b",Kopi,", (self.rules / "merchants.csv").read_bytes())
        rules_app._load_all(self.conn)                       # no RuleRefused: Kopi is an alias

    def test_names_are_checked(self):
        with self.assertRaisesRegex(categories.CategoryRefused, "already"):
            categories.rename_category(self.conn, "Coffee", "eating out")
        with self.assertRaisesRegex(categories.CategoryRefused, "already"):
            categories.add_category(self.conn, "groceries", "Food")
        with self.assertRaisesRegex(categories.CategoryRefused, "No bucket"):
            categories.add_category(self.conn, "Pets", "Nope")
        with self.assertRaisesRegex(categories.CategoryRefused, "one of"):
            categories.add_category(self.conn, "Bonus", "Income", "Want")
        self.assertEqual(categories.add_category(self.conn, "Bonus", "Income")["name"], "Bonus")
        self.assertEqual(self.cat("Bonus")["need_class"], "Income")

    def test_budgets_come_grouped_by_bucket(self):
        b = planning.budgets(self.conn, "2026-08")
        self.assertEqual([g["name"] for g in b["groups"]][-1], "Income")
        self.assertNotIn("Transfers", [g["name"] for g in b["groups"]])
        food = [r["category"] for r in b["rows"] if r["category_group"] == "Food"]
        self.assertEqual(food, ["Groceries", "Eating out", "Coffee"])

    def test_the_endpoints(self):
        import serve
        with mock.patch("finance.api.open_record", lambda: self.conn):
            self.assertIn("groups", serve.answer("/api/categories", {}))
        with mock.patch("finance.api.config", lambda: None), \
                mock.patch("finance.stats_rebuild.refresh_stats") as refresh:
            out = serve.write("/api/categories/move", {}, json.dumps(
                {"name": "Coffee", "group": "Lifestyle", "index": 0}).encode())
            self.assertEqual((out["index"], out["stats"]), (0, "rebuilding"))
            self.assertEqual(refresh.call_count, 1)       # the stat results follow the edit
            serve.write("/api/categories", {}, b'{"name": "Coffee", "need_class": "Need"}')
            self.assertEqual(refresh.call_count, 2)
            with self.assertRaises(serve.Refused):
                serve.write("/api/category-groups/delete", {}, b'{"name": "Income"}')
            for bad in (b'{"name": "Coffee", "group": "Food", "index": "1"}', b'{"name": ["Coffee"], "group": "Food"}'):
                with self.assertRaises(ValueError, msg=bad):
                    serve.write("/api/categories/move", {}, bad)
            with self.assertRaises(ValueError):
                serve.write("/api/category-groups/reorder", {}, b'{"names": "Food"}')
        with closing(db.connect()) as conn:
            self.assertEqual(conn.execute("""SELECT g.name FROM category c JOIN category_group g
                                             ON g.id = c.group_id WHERE c.name = 'Coffee'""").fetchone()[0],
                             "Lifestyle")


class StatsRefreshTest(unittest.TestCase):
    def test_edits_while_a_rebuild_runs_cost_one_more_rebuild(self):
        import threading
        import serve
        from finance import monthly
        release, calls, done, started = threading.Event(), [], threading.Event(), threading.Event()

        def slow(cfg):
            calls.append(cfg)
            started.set()
            release.wait(10)
            if len(calls) == 2:
                done.set()
        # G13: edits before the rebuild starts share its run (stats_rebuild.STATS_DELAY); these land while it runs
        with mock.patch.object(monthly, "analyse", side_effect=slow), mock.patch("finance.stats_rebuild.STATS_DELAY", 0):
            stats_rebuild.refresh_stats("cfg")
            self.assertTrue(started.wait(10))
            for _ in range(2):
                stats_rebuild.refresh_stats("cfg")
            release.set()
            self.assertTrue(done.wait(10))
            for _ in range(100):                       # the worker clears its flag after the second run
                if not stats_rebuild._STATS["running"]:
                    break
                threading.Event().wait(0.01)
        self.assertEqual(len(calls), 2)
        self.assertEqual(stats_rebuild._STATS, {"running": False, "again": False})


class OneRebuildAtATimeTest(unittest.TestCase):
    def test_a_load_s_rebuild_and_an_edit_s_never_overlap(self):
        import threading
        from analytics import run as analytics_run
        from finance import monthly
        inside, most = [0], [0]
        lock = threading.Lock()

        def slow(*_, **__):
            with lock:
                inside[0] += 1
                most[0] = max(most[0], inside[0])
            threading.Event().wait(0.05)
            with lock:
                inside[0] -= 1
            return {"transactions": []}, {}
        monthly.rebuild_stats_with(analytics_run.rebuild)     # what serve.py does at start-up
        with mock.patch.object(analytics_run, "analyse", side_effect=slow):
            threads = [threading.Thread(target=monthly.analyse, args=(None,)) for _ in range(3)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(10)
        self.assertEqual(most[0], 1)

    def test_the_app_hands_finance_the_stats_rebuild(self):
        """G19: finance never imports analytics, so serve.py wires the rebuild in."""
        from analytics import run as analytics_run
        from finance import monthly
        import serve  # noqa: F401  importing it is the wiring
        self.assertIs(monthly._REBUILD[0], analytics_run.rebuild)

    def test_with_no_rebuild_handed_in_it_says_so(self):
        from finance import monthly
        with mock.patch.object(monthly, "_REBUILD", [None]), self.assertRaises(RuntimeError) as e:
            monthly.analyse(None)
        self.assertIn("rebuild_stats_with", str(e.exception))


class RenamedStatsTest(DataFolder):
    """The stats that look for a shipped category by name follow a rename,
    and a deleted one is gone rather than counted twice."""

    def setUp(self):
        super().setUp()
        self.conn = record()
        self.addCleanup(self.conn.close)

    def summary(self):
        from analytics.stats import eating_out
        return eating_out.run(db.snapshot(self.conn))["data"]["summary"]

    def test_a_renamed_category_keeps_its_figures(self):
        before = self.summary()
        categories.rename_category(self.conn, "Eating out", "Dining")
        self.assertEqual(db.snapshot(self.conn)["category_aliases"], {"Eating out": "Dining"})
        after = self.summary()
        self.assertEqual((after["count"], after["spend_minor"]), (before["count"], before["spend_minor"]))
        self.assertGreater(after["count"], 0)

    def test_a_deleted_category_is_not_counted_as_the_one_it_went_into(self):
        categories.delete_category(self.conn, "Coffee", "Eating out")
        self.assertEqual(db.snapshot(self.conn)["category_aliases"], {})
        s = self.summary()
        self.assertEqual(s["coffee_minor"], 0)
        self.assertEqual(s["count"], 3)              # the two coffees are meals now


class SnapshotTest(unittest.TestCase):
    def test_category_breakdown_carries_each_category_s_bucket(self):
        from analytics.stats import category_breakdown
        conn = db.connect(":memory:")
        db.seed_categories(conn)
        db.load_parsed(conn, parsed([block()], [row("k1", "-4.50", category="Coffee")]))
        snap = db.snapshot(conn)
        self.assertEqual(snap["category_groups"][0]["name"], "Food")
        res = category_breakdown.run(snap)
        coffee = next(c for c in res["data"]["categories"] if c["category"] == "Coffee")
        self.assertEqual(coffee["category_group"], "Food")


if __name__ == "__main__":
    unittest.main()
