"""server/finance/transport_split.py: Transport into Transport (public) and Transport
(private), on an invented data folder."""
import isolate  # noqa: F401  first: no test writes the real record or cache
import hashlib
import io
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import closing, redirect_stdout
from pathlib import Path
from unittest import mock

from finance.parsers import categorise
from finance import db, rules_app, setup, transport_split
from finance.transport_split import PRIVATE, PUBLIC, classify
from test_db import block, parsed, row

MERCHANTS = (b"match_on,pattern,payee,kind,country,category,notes\r\n"
             b"merchant,LIME*,Lime,Merchant,SGP,Transport,\r\n"
             b"merchant,SL APP*,SL,Merchant,SWE,Transport,Stockholm\r\n"
             b"merchant,SHELL*,Shell,Merchant,SGP,Transport,\"petrol, the one near home\"\r\n"
             b"merchant,MYSTERY RIDE*,Mystery,Merchant,SGP,Transport,\r\n"
             b"# merchant,OLD*,Old,Merchant,SGP,Transport,a comment stays as it is\r\n"
             b"merchant,TOAST BOX*,Toast Box,Merchant,SGP,Coffee,\r\n")
PEOPLE = (b"match_on,pattern,payee,kind,country,category,notes\n"
          b"counterparty,PARKING.SG*,Parking.sg,Institution,SGP,Transport,\n")
KEYS = (b"match_on,pattern,payee,kind,country,category,notes\n"
        b"key,k-taxi,,,,Transport,a one-off\n")

# (key, merchant, counterparty): every one of these sits in Transport before the split
ROWS = [("k-grab", "GRAB* A-1XYZ1", ""), ("k-bus", "BUS/MRT 123456", ""), ("k-lime", "LIME*RIDE ABC", ""),
        ("k-sl", "SL APP STOCKHOLM", ""), ("k-shell", "SHELL KHATIB", ""), ("k-myst", "MYSTERY RIDE 9", ""),
        ("k-taxi", "CITYCAB 1234", ""), ("k-park", "", "PARKING.SG HDB")]
WANT = {"k-grab": PRIVATE, "k-bus": PUBLIC, "k-lime": PRIVATE, "k-sl": PUBLIC, "k-shell": PRIVATE,
        "k-myst": "Transport", "k-taxi": PRIVATE, "k-park": PRIVATE, "k-coffee": "Coffee"}


class ClassifyTest(unittest.TestCase):
    def test_public_and_private(self):
        for text, want in (("BUS/MRT*", PUBLIC), ("MRT/BUS Total Fare", PUBLIC), ("SIMPLYGO", PUBLIC),
                           ("EZ-LINK TOP UP", PUBLIC), ("MTA*NYCT PAYGO", PUBLIC), ("CLIPPER TRANSIT", PUBLIC),
                           ("NS Reizigers", PUBLIC), ("Skyss Billett", PUBLIC), ("CAUSEWAYLINK-HQ", PUBLIC),
                           ("GRAB* A-8XYZ", PRIVATE), ("GRAB* GPC-1234", PRIVATE), ("GOPAY-GOJEK", PRIVATE),
                           ("TADA MOBILITY", PRIVATE), ("CDG ZIG", PRIVATE), ("COMFORT TAXI", PRIVATE),
                           ("UBR* PENDING.UBER.COM", PRIVATE), ("HERTZ RENT A CAR", PRIVATE),
                           ("SPC 98 UPP THOMSON", PRIVATE), ("ESSO", PRIVATE), ("PARKING.SG", PRIVATE),
                           ("LTA ERP", PRIVATE), ("HELLORIDE SINGAPORE", PRIVATE), ("LIME*PASS", PRIVATE),
                           ("CIRCLE K 0042", PRIVATE), ("CALTRAIN GO", PUBLIC), ("BVG TICKET", PUBLIC),
                           ("TRAMVAJ 22", PUBLIC)):
            self.assertEqual(classify(text)[0], want, text)

    def test_what_it_cannot_place_it_leaves(self):
        for text in ("MYSTERY RIDE 9", "127 - UNIVERSITY", ""):
            self.assertEqual(classify(text), (None, ""), text)

    def test_the_payee_helps(self):
        self.assertEqual(classify("A-1234", "Bus/MRT")[0], PUBLIC)


class SeedTest(unittest.TestCase):
    def test_a_fresh_record_whose_rules_still_say_transport_gets_it(self):
        conn = db.connect(":memory:")
        db.seed_categories(conn)
        self.assertIsNone(conn.execute("SELECT 1 FROM category WHERE name = 'Transport'").fetchone())
        db.seed_categories(conn, named={"Transport", "Coffee"})
        got = conn.execute("""SELECT c.bucket, c.need_class, g.name FROM category c
                              JOIN category_group g ON g.id = c.group_id WHERE c.name = 'Transport'""").fetchone()
        self.assertEqual(tuple(got), ("Spend", "Need", "Getting around"))

    def test_a_new_category_goes_last_in_its_bucket_in_a_record_the_user_ordered(self):
        from finance import categories
        conn = db.connect(":memory:")
        db.seed_categories(conn, [c for c in db.shipped_categories() if c["name"] != PRIVATE])
        categories.move_category(conn, "Groceries", "Food", 0)      # the page renumbers everything
        db.seed_categories(conn)                                     # Transport (private) arrives
        order = [r[0] for r in conn.execute("SELECT name FROM category ORDER BY sort_order")]
        self.assertEqual(order[order.index(PUBLIC) + 1], PRIVATE)


    def test_a_category_the_shipped_rename_reaches_keeps_its_place(self):
        from finance import categories
        conn = db.connect(":memory:")
        old = [{**c, "name": "Tuition"} if c["name"] == "Education" else c for c in db.shipped_categories()]
        db.seed_categories(conn, old)
        categories.move_category(conn, "Tuition", "Growth", 0)      # the user put it first
        before = conn.execute("SELECT sort_order FROM category WHERE name = 'Tuition'").fetchone()[0]
        db.seed_categories(conn)                                     # the rename to Education runs
        self.assertEqual(conn.execute("SELECT sort_order FROM category WHERE name = 'Education'").fetchone()[0],
                         before)


class KeyRuleTest(unittest.TestCase):
    ROWS = {categorise.norm(k): {"merchant": m} for k, m in
            (("k-bus", "BUS/MRT 1"), ("k-taxi", "CITYCAB 1"), ("k-tada", "TADA MOBILITY 1"))}

    def test_a_glob_whose_rows_all_file_one_way_moves(self):
        rows = transport_split._pinned(self.ROWS, "k-ta*")
        self.assertEqual(len(rows), 2)
        self.assertEqual(transport_split._classify_key_rule(rows, "")[0], PRIVATE)

    def test_a_glob_whose_rows_differ_stays(self):
        rows = transport_split._pinned(self.ROWS, "k-*")
        self.assertEqual(transport_split._classify_key_rule(rows, ""), (None, "its rows differ"))


class SplitTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        p = mock.patch.dict(os.environ, {"KOPIFUND_DATA": str(self.root),
                                         "KOPIFUND_DB": str(self.root / "kopifund.db")})
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)
        setup.create(["posb-everyday"])
        self.record = self.root / "kopifund.db"
        self.rules = self.root / "rules"
        self.rules.mkdir(exist_ok=True)
        (self.rules / "merchants.csv").write_bytes(MERCHANTS)
        (self.rules / "people.csv").write_bytes(PEOPLE)
        (self.rules / "keys.csv").write_bytes(KEYS)
        with closing(db.connect()) as conn:
            # the record as it was before the split: Transport, in Getting around
            conn.execute("INSERT INTO category(name, bucket, grp, need_class, sort_order) "
                         "VALUES ('Transport', 'Spend', 'Variable', 'Need', 110)")
            db.seed_categories(conn)
            rows = [row(k, "-3.00", merchant=m, counterparty=cp, payee="", category="Transport", rule="r")
                    for k, m, cp in ROWS]
            rows.append(row("k-coffee", "-4.00", merchant="TOAST BOX 1", payee="", category="Coffee"))
            db.load_parsed(conn, parsed([block()], rows))
            conn.execute("INSERT INTO budget(category_id, month, planned_minor) "
                         "SELECT id, '2026-08', 12000 FROM category WHERE name = 'Transport'")
            conn.commit()

    def run_split(self, dry_run):
        out = io.StringIO()
        with redirect_stdout(out):
            summary = transport_split.run(self.record, dry_run=dry_run)
        return summary, out.getvalue()

    def categories(self):
        with closing(sqlite3.connect(self.record)) as c:
            return dict(c.execute("SELECT txn_key, category_name FROM v_txn"))

    def digest(self):
        return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                for p in [self.record, *sorted(self.rules.glob("*.csv"))]}

    def test_the_old_record_keeps_transport_in_getting_around(self):
        with closing(db.connect()) as conn:
            got = conn.execute("""SELECT g.name FROM category c JOIN category_group g ON g.id = c.group_id
                                  WHERE c.name = 'Transport'""").fetchone()[0]
        self.assertEqual(got, "Getting around")

    def test_a_dry_run_writes_nothing_and_says_what_would_move(self):
        before = self.digest()
        summary, text = self.run_split(dry_run=True)
        self.assertEqual(self.digest(), before)
        self.assertFalse((self.root / "backups").exists() and any((self.root / "backups").iterdir()))
        self.assertEqual((summary["rules_moved"], summary["rules_left"]), (5, 1))
        self.assertEqual((summary["rows_moved"], summary["rows_left"]), (7, 1))
        self.assertIn("dry run: nothing written", text)
        self.assertIn("merchant:MYSTERY RIDE*", text)
        self.assertIn("stays Transport", text)
        self.assertIn("1 month(s) of Transport budget", text)

    def test_the_split_moves_rules_and_rows_and_keeps_backups(self):
        summary, text = self.run_split(dry_run=False)
        self.assertEqual(self.categories(), WANT)
        merchants = (self.rules / "merchants.csv").read_bytes()
        self.assertIn(b"merchant,LIME*,Lime,Merchant,SGP,Transport (private),\r\n", merchants)
        self.assertIn(b"merchant,SL APP*,SL,Merchant,SWE,Transport (public),Stockholm\r\n", merchants)
        self.assertIn(b'"petrol, the one near home"', merchants)
        self.assertIn(b"merchant,MYSTERY RIDE*,Mystery,Merchant,SGP,Transport,\r\n", merchants)
        self.assertIn(b"# merchant,OLD*,Old,Merchant,SGP,Transport,a comment stays as it is\r\n", merchants)
        self.assertIn(b"key,k-taxi,,,,Transport (private),a one-off\n", (self.rules / "keys.csv").read_bytes())
        self.assertIn(b"Transport (private)", (self.rules / "people.csv").read_bytes())
        backups = self.root / "backups"
        self.assertEqual(len(list(backups.glob("kopifund-before-split-transport-*.db"))), 1)
        self.assertIn("Decide the rows and rules left in Transport", text)
        # G13: saves apply at once, but this command runs outside the app: it says
        # what is true, that the stats rerun on `analyse` or a load, not "the next load"
        self.assertIn("The record has the split now, but the stat pages show it only after the stats rerun", text)
        self.assertNotIn("next load", text)
        # the rules change is one write in the Rules page's undo history
        self.assertTrue(rules_app.history()[0]["summary"].startswith("Split Transport"))
        rules_app.undo()
        self.assertEqual((self.rules / "merchants.csv").read_bytes(), MERCHANTS)
        self.assertEqual((self.rules / "keys.csv").read_bytes(), KEYS)
        self.run_split(dry_run=False)
        # a second run finds nothing more to move
        again, _ = self.run_split(dry_run=False)
        self.assertEqual((again["rows_moved"], again["rules_moved"]), (0, 0))

    def test_the_next_load_puts_every_row_where_the_split_did(self):
        self.run_split(dry_run=False)
        with closing(db.connect()) as conn:
            rules = categorise.load_rules(rules_app.rule_paths(), rules_app.categories(conn))
            rows = rules_app.db_rows(conn)
            ids = db.category_ids(conn)
            names = {v: k for k, v in ids.items() if k in rules_app.categories(conn)}
            for r in categorise.categorise_rows([dict(r) for r in rows], rules):
                self.assertEqual(names[ids[r["category"]]], WANT[r["txn_key"]], r["txn_key"])

    def test_a_rule_set_that_will_not_load_writes_nothing(self):
        before = self.digest()
        with mock.patch.object(rules_app, "_trial", side_effect=rules_app.RuleRefused("bad set")):
            with self.assertRaises(rules_app.RuleRefused), redirect_stdout(io.StringIO()):
                transport_split.run(self.record, dry_run=False)
        after = self.digest()
        self.assertEqual({k: v for k, v in after.items() if k != "kopifund.db"},
                         {k: v for k, v in before.items() if k != "kopifund.db"})
        self.assertEqual(self.categories()["k-grab"], "Transport")

    def test_a_rules_file_saved_meanwhile_is_not_overwritten(self):
        # the Rules page (another process) saves merchants.csv after the split read it
        real = rules_app.RulesFile

        class SavedMeanwhile(real):
            def __init__(self, name):
                super().__init__(name)
                if name == "merchants.csv":
                    (self_dir / name).write_bytes(MERCHANTS + b"merchant,NEW*,New,Merchant,SGP,Coffee,\r\n")
        self_dir = self.rules
        with mock.patch.object(rules_app, "RulesFile", SavedMeanwhile):
            with self.assertRaisesRegex(rules_app.RuleRefused, "changed since it was read"), \
                    redirect_stdout(io.StringIO()):
                transport_split.run(self.record, dry_run=False)
        self.assertIn(b"NEW*", (self.rules / "merchants.csv").read_bytes())
        self.assertEqual(self.categories()["k-grab"], "Transport")

    def test_a_split_in_another_process_waits_for_a_rules_save_without_holding_the_record(self):
        """The app saves a rule (rules lock, then a record write) while the
        split runs from the command line on the same data folder. The split
        waits for the rules lock before it takes the record's write lock, so
        the save's record write goes through, and then the split runs."""
        code = ("import sys\n"
                "from finance import transport_split\n"
                "print('ready', flush=True)\n"
                "transport_split.run(sys.argv[1], dry_run=False)\n")
        server = Path(__file__).resolve().parent.parent
        with rules_app._RULES_LOCK:
            child = subprocess.Popen([sys.executable, "-c", code, str(self.record)], cwd=server,
                                     stdout=subprocess.PIPE, text=True)
            self.addCleanup(child.wait)
            self.addCleanup(child.stdout.close)
            self.assertEqual(child.stdout.readline().strip(), "ready")
            time.sleep(1.0)
            self.assertIsNone(child.poll())          # held off by the rules lock
            # the save's own record write, as rules_app._commit makes it
            with closing(db.connect(self.record)) as conn, db.transaction(conn):
                conn.execute("INSERT INTO setting(key, value) VALUES ('saved', '1')")
        child.stdout.read()
        self.assertEqual(child.wait(timeout=60), 0)
        self.assertEqual(self.categories(), WANT)
        with closing(db.connect(self.record)) as conn:
            self.assertEqual(conn.execute("SELECT value FROM setting WHERE key = 'saved'").fetchone()[0], "1")

    def test_a_key_rule_in_another_case_still_finds_its_row(self):
        (self.rules / "keys.csv").write_bytes(KEYS.replace(b"k-taxi", b"K-TAXI"))
        summary, _ = self.run_split(dry_run=True)
        self.assertEqual(summary["rules_moved"], 5)

    def test_the_command_line(self):
        import finance as cli  # noqa: F401  the package; the script is finance.py
        import importlib.util
        spec = importlib.util.spec_from_file_location("finance_cli", Path(__file__).resolve().parent.parent / "finance.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(mod.main(["split-transport", "--dry-run"]), 0)
        self.assertIn("Transactions in Transport: 8", out.getvalue())


if __name__ == "__main__":
    unittest.main()
