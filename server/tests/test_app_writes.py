"""The app's write paths: planning (G8), rules (G7), export (G9), setup (G10)."""
import isolate  # noqa: F401  first: no test writes the real record or cache
import csv
import os
import subprocess
import sys
import tempfile
import time
import tomllib
import unittest
from decimal import Decimal
from pathlib import Path
from unittest import mock

from finance.parsers import categorise
from finance import db, export, planning, rules_app, rules_editor, setup
from test_db import SNAP, block, parsed, row


def record(*more):
    conn = db.connect(":memory:")
    db.import_user_tables(conn, {k: [dict(x) for x in v] for k, v in SNAP.items()},
                          plan={"dca": 100, "to": "2027-06"})
    db.load_parsed(conn, parsed([block()], [
        row("k1", "-12.50"), row("k2", "-17.00", merchant="KOPITIAM 0123 AMK", payee="",
                                 category="", rule=""), *more]),
        payees=[{"name": "Grab", "kind": "Merchant", "country": "SGP"}])
    return conn


class DataFolder(unittest.TestCase):
    """A temporary KOPIFUND_DATA for the test, so nothing touches the real one."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        p = mock.patch.dict(os.environ, {"KOPIFUND_DATA": str(self.root),
                                         "KOPIFUND_DB": str(self.root / "kopifund.db")})
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)


class PlanningTest(unittest.TestCase):
    def setUp(self):
        self.conn = record()

    def test_budgets_show_planned_and_actual_in_cents(self):
        b = planning.budgets(self.conn, "2026-08")
        transport = next(r for r in b["rows"] if r["category"] == "Transport")
        self.assertEqual((transport["planned"], transport["actual"]), (5000, 1250))

    def test_set_and_clear_a_budget(self):
        planning.set_budget(self.conn, "Transport", "2026-09", 8000)
        self.assertEqual(planning.budgets(self.conn, "2026-09")["rows"][0]["planned"] or
                         next(r["planned"] for r in planning.budgets(self.conn, "2026-09")["rows"]
                              if r["category"] == "Transport"), 8000)
        planning.set_budget(self.conn, "Transport", "2026-09", None)
        self.assertIsNone(next(r["planned"] for r in planning.budgets(self.conn, "2026-09")["rows"]
                               if r["category"] == "Transport"))

    def test_bad_budgets_are_refused(self):
        for args in (("Transport", "2026-9", 1), ("Transport", "2026-09", -1),
                     ("Transport", "2026-09", 1.5), ("Nope", "2026-09", 1)):
            with self.assertRaises(planning.PlanRefused, msg=args):
                planning.set_budget(self.conn, *args)

    def test_copy_fills_only_empty_months(self):
        planning.set_budget(self.conn, "Salary", "2026-09", 1)
        self.assertEqual(planning.copy_budgets(self.conn, "2026-08", "2026-09")["copied"], 1)

    def test_the_plan_round_trips_with_money_stored_as_cents(self):
        planning.set_plan(self.conn, {"dca": 250.5, "to": "2027-03",
                                      "one_offs": [{"month": "2027-01", "amount": 1200, "label": "x"}]})
        raw = dict(self.conn.execute("SELECT key, value FROM plan").fetchall())
        self.assertEqual(raw["dca"], "25050")
        self.assertEqual(planning.get_plan(self.conn)["one_offs"][0]["amount"], 1200.0)

    def test_the_plan_crosses_the_wire_in_cents_and_back_exactly(self):
        # G17: every money field leaves as whole cents under its _minor name, and comes back the same
        planning.set_plan(self.conn, {
            "dca": "250.05", "insurance": "0.1", "to": "2027-03",
            "income": [{"name": "Job", "from": "2026-01-01", "gross": "6000.07"}],
            "investing": [{"from": "2026-05", "amount": "300.30"}],
            "trips": [{"from": "2027-01-01", "to": "2027-01-03", "per_day": "80.80", "flights": "410"}],
            "one_offs": [{"month": "2027-01", "amount": "1200.99", "label": "x"}],
            "projection": {"spend": "2500.25", "milestone": "1000000"}})
        stored = planning.get_plan(self.conn)
        out = planning.plan_out(stored)
        self.assertEqual((out["dca_minor"], out["insurance_minor"]), (25_005, 10))
        self.assertEqual(out["income"][0]["gross_minor"], 600_007)
        self.assertEqual(out["investing"], [{"from": "2026-05", "amount_minor": 30_030}])
        self.assertEqual((out["trips"][0]["per_day_minor"], out["trips"][0]["flights_minor"]), (8_080, 41_000))
        self.assertEqual(out["one_offs"][0]["amount_minor"], 120_099)
        self.assertEqual(out["projection"], {"horizon": "10y", "dollars": "today", "spend_minor": 250_025,
                                             "milestone_minor": 100_000_000})

        def money(v, where="$"):
            if isinstance(v, dict):
                return [p for k, x in v.items() for p in money(x, f"{where}.{k}")]
            if isinstance(v, list):
                return [p for i, x in enumerate(v) for p in money(x, f"{where}[{i}]")]
            return [where] if isinstance(v, (float, Decimal)) else []
        self.assertEqual(money(out), [])            # no dollars, no float, anywhere
        self.assertEqual(planning.plan_in(out), stored)
        planning.set_plan(self.conn, planning.plan_in(out))
        self.assertEqual(planning.get_plan(self.conn), stored)

    def test_a_field_sent_as_cents_and_as_dollars_is_refused(self):
        for bad in ({"dca_minor": 100, "dca": 1}, {"one_offs": [{"month": "2027-01", "amount": 1, "amount_minor": 100}]},
                    {"dca_minor": 1.0}, {"dca_minor": "100"}):
            with self.assertRaises(planning.PlanRefused, msg=bad):
                planning.plan_in(bad)
        self.assertEqual(planning.plan_in({"dca_minor": None, "to": "2027-01"}), {"dca": None, "to": "2027-01"})

    def test_a_bad_plan_is_refused_and_the_old_one_kept(self):
        for bad in ({"to": "next year"}, {"dca": -5}, {"spend_rate": "fast"},
                    {"income": [{"from": "2027-01", "to": "2026-01", "gross": 1}]},
                    {"one_offs": [{"month": "soon", "amount": 1}]}):
            with self.assertRaises(planning.PlanRefused, msg=bad):
                planning.set_plan(self.conn, bad)
        self.assertEqual(planning.get_plan(self.conn)["dca"], 100.0)

    def test_dates_must_be_real_days(self):
        for bad in ({"income": [{"from": "2027-01", "to": "2027-03-31", "gross": 1}]},
                    {"income": [{"from": "2027-02-01", "to": "2027-02-31", "gross": 1}]}):
            with self.assertRaises(planning.PlanRefused, msg=bad):
                planning.set_plan(self.conn, bad)
        with self.assertRaises(planning.PlanRefused):
            planning.save_trip(self.conn, {"name": "X", "start": "2026-02-30", "end": "2026-03-01"})

    def test_a_planned_trip_with_no_daily_cost_uses_the_default(self):
        planning.set_plan(self.conn, {"trips": [{"name": "T", "from": "2027-01-01", "to": "2027-01-03",
                                                 "per_day": None, "flights": None}]})
        self.assertNotIn("per_day", planning.get_plan(self.conn)["trips"][0])

    def test_amounts_are_exact_cents(self):
        planning.set_plan(self.conn, {"dca": "250.10"})
        for bad in ("1.005", "nan", True, "x"):
            with self.assertRaises(planning.PlanRefused, msg=bad):
                planning.set_plan(self.conn, {"dca": bad})

    def test_a_need_class_cannot_be_cleared(self):
        with self.assertRaises(planning.PlanRefused):
            planning.set_need_class(self.conn, "Transport", None)

    def test_need_class_and_trips(self):
        planning.set_need_class(self.conn, "Transport", "Want")
        self.assertEqual(self.conn.execute("SELECT need_class FROM category WHERE name='Transport'").fetchone()[0], "Want")
        with self.assertRaises(planning.PlanRefused):
            planning.set_need_class(self.conn, "Transport", "Luxury")
        planning.save_trip(self.conn, {"name": "Trip B", "start": "2026-09-01", "end": "2026-09-03",
                                       "countries": ["jpn"]})
        tid = self.conn.execute("SELECT id FROM trip WHERE name='Trip B'").fetchone()[0]
        self.assertEqual(db.trip_countries(self.conn, tid), ["JPN"])
        with self.assertRaises(planning.PlanRefused):
            planning.save_trip(self.conn, {"name": "Bad", "start": "2026-09-03", "end": "2026-09-01"})
        planning.remove_trip(self.conn, "Trip B")
        self.assertIsNone(self.conn.execute("SELECT 1 FROM trip WHERE name='Trip B'").fetchone())


    def test_a_removed_trip_comes_back_with_its_rows(self):
        planning.save_trip(self.conn, {"name": "Trip B", "start": "2026-09-01", "end": "2026-09-03",
                                       "countries": ["jpn", "mys"], "extra_keys": ["k2"], "notes": "n"})
        tid = self.conn.execute("SELECT id FROM trip WHERE name='Trip B'").fetchone()[0]
        self.conn.execute("UPDATE txn SET trip_id = ? WHERE txn_key IN ('k1', 'k2')", (tid,))
        out = planning.remove_trip(self.conn, "Trip B")
        self.assertEqual(out["restore"]["txn_keys"], ["k1", "k2"])
        self.assertEqual(out["restore"]["trip"]["countries"], ["JPN", "MYS"])
        self.assertIsNone(self.conn.execute("SELECT trip_id FROM txn WHERE txn_key='k1'").fetchone()[0])
        # k2 joined another trip meanwhile: the undo does not take it back
        planning.save_trip(self.conn, {"name": "Trip C", "start": "2026-10-01", "end": "2026-10-02"})
        other = self.conn.execute("SELECT id FROM trip WHERE name='Trip C'").fetchone()[0]
        self.conn.execute("UPDATE txn SET trip_id = ? WHERE txn_key='k2'", (other,))
        planning.restore_trip(self.conn, out["restore"])
        back = self.conn.execute("SELECT * FROM trip WHERE name='Trip B'").fetchone()
        self.assertEqual((back["start_date"], back["end_date"], db.trip_countries(self.conn, back["id"]),
                          db.trip_pins(self.conn, back["id"]), back["notes"]),
                         ("2026-09-01", "2026-09-03", ["JPN", "MYS"], [], "n"))   # k2 is Trip C's now: not pinned again
        trips = {r[0]: r[1] for r in self.conn.execute("SELECT txn_key, trip_id FROM txn")}
        self.assertEqual(trips["k1"], back["id"])
        self.assertEqual(trips["k2"], other)

    def test_a_restored_trip_keeps_the_pins_of_rows_nobody_took(self):
        planning.save_trip(self.conn, {"name": "Trip B", "start": "2026-09-01", "end": "2026-09-03",
                                       "extra_keys": ["k1", "k2"]})
        out = planning.remove_trip(self.conn, "Trip B")
        planning.save_trip(self.conn, {"name": "Trip C", "start": "2026-10-01", "end": "2026-10-02"})
        other = self.conn.execute("SELECT id FROM trip WHERE name='Trip C'").fetchone()[0]
        self.conn.execute("UPDATE txn SET trip_id = ? WHERE txn_key='k2'", (other,))
        planning.restore_trip(self.conn, out["restore"])
        tid = self.conn.execute("SELECT id FROM trip WHERE name='Trip B'").fetchone()[0]
        self.assertEqual(db.trip_pins(self.conn, tid), ["k1"])

    def test_restoring_never_overwrites_a_trip_saved_in_between(self):
        planning.save_trip(self.conn, {"name": "Trip B", "start": "2026-09-01", "end": "2026-09-03", "notes": "old"})
        out = planning.remove_trip(self.conn, "Trip B")
        planning.save_trip(self.conn, {"name": "Trip B", "start": "2026-11-01", "end": "2026-11-03", "notes": "new"})
        with self.assertRaises(planning.PlanRefused):
            planning.restore_trip(self.conn, out["restore"])
        row = self.conn.execute("SELECT start_date, notes FROM trip WHERE name='Trip B'").fetchone()
        self.assertEqual((row[0], row[1]), ("2026-11-01", "new"))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM trip WHERE name='Trip B'").fetchone()[0], 1)

    def test_a_trip_is_not_restored_over_one_of_the_same_name(self):
        planning.save_trip(self.conn, {"name": "Trip B", "start": "2026-09-01", "end": "2026-09-03"})
        out = planning.remove_trip(self.conn, "Trip B")
        planning.save_trip(self.conn, {"name": "Trip B", "start": "2026-11-01", "end": "2026-11-03"})
        with self.assertRaises(planning.PlanRefused):
            planning.restore_trip(self.conn, out["restore"])
        with self.assertRaises(planning.PlanRefused):
            planning.restore_trip(self.conn, {})
        self.assertEqual(self.conn.execute("SELECT start_date FROM trip WHERE name='Trip B'").fetchone()[0], "2026-11-01")


class RulesTest(DataFolder):
    def setUp(self):
        super().setUp()
        self.conn = record()

    def test_unmatched_rows_come_with_a_suggestion(self):
        needs = rules_app.needs_rule(self.conn)
        self.assertEqual(needs[0]["text"], "KOPITIAM 0123 AMK")
        self.assertEqual(needs[0]["suggestion"]["pattern"], "KOPITIAM*")

    def personal(self):
        """The personal rules as the Rules page lists them."""
        return [r for r in rules_editor.overview(self.conn)["rules"] if not r["shared"]]

    def test_a_rule_lands_in_the_personal_file_and_matches(self):
        out = rules_app.add_many(self.conn, [{"match_on": "merchant", "pattern": "KOPITIAM*",
                                              "payee": "Kopitiam", "kind": "Merchant", "category": "Transport"}])
        self.assertEqual(out["added"], 1)
        # G13: the row it catches is filed at once, so none it matches is left unmatched
        self.assertEqual(out["relinked"]["changed"], 1)
        self.assertEqual(rules_app.needs_rule(self.conn), [])
        with (self.root / "rules" / "merchants.csv").open() as f:
            self.assertEqual(list(csv.DictReader(f))[0]["payee"], "Kopitiam")
        listed = self.personal()
        self.assertEqual([(r["file"], r["pattern"], r["matched"]) for r in listed],
                         [("merchants.csv", "KOPITIAM*", 1)])
        out = rules_app.remove("merchants.csv", listed[0]["line"])
        self.assertEqual(self.personal(), [])
        # the answer carries the token that undoes this delete and no other write
        self.assertTrue(out["change"])
        rules_app.undo(out["change"])
        self.assertEqual([r["pattern"] for r in self.personal()], ["KOPITIAM*"])

    def test_removing_a_rule_keeps_a_multiline_note_whole(self):
        d = self.root / "rules"
        d.mkdir()
        (d / "merchants.csv").write_text(
            "match_on,pattern,payee,kind,country,category,notes\n"
            'merchant,ALPHA*,Alpha,Merchant,SGP,Transport,"two\nlines"\n'
            "merchant,BETA*,Beta,Merchant,SGP,Transport,\n", encoding="utf-8")
        listed = self.personal()
        self.assertEqual([r["pattern"] for r in listed], ["ALPHA*", "BETA*"])
        rules_app.remove("merchants.csv", listed[1]["line"])
        with (d / "merchants.csv").open(newline="") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual([(r["pattern"], r["notes"]) for r in rows], [("ALPHA*", "two\nlines")])

    def test_a_rule_that_breaks_the_set_is_taken_back(self):
        with self.assertRaises(rules_app.RuleRefused):
            rules_app.add_many(self.conn, [{"match_on": "merchant", "pattern": "X*", "payee": "X",
                                            "kind": "Merchant", "category": "Not a category"}])
        self.assertFalse((self.root / "rules" / "merchants.csv").exists())

    def test_a_row_that_prints_no_text_gets_a_key_rule(self):
        self.conn.execute("UPDATE txn SET merchant_raw = '', counterparty = '', memo = '', "
                          "rule = '' WHERE txn_key = 'k1'")
        needs = {g["field"]: g for g in rules_app.needs_rule(self.conn)}
        self.assertEqual(needs["key"]["suggestion"], {"match_on": "key", "pattern": "k1", "payee": "",
                                                     "kind": "Merchant"})

    def test_rule_writes_take_the_lock(self):
        self.assertFalse(rules_app._RULES_LOCK.locked())
        held = []

        def commit(*a, **kw):
            held.append(rules_app._RULES_LOCK.locked())
            raise rules_app.RuleRefused("stop here")
        with mock.patch.object(rules_app, "_commit", side_effect=commit), \
                self.assertRaises(rules_app.RuleRefused):
            rules_app.add_many(self.conn, [{"match_on": "merchant", "pattern": "X*", "category": "Transport"}])
        self.assertEqual(held, [True])
        self.assertFalse(rules_app._RULES_LOCK.locked())

    def test_a_save_in_another_process_waits_for_the_lock(self):
        """The app and `make serve` on one data folder: a rule save in the
        other process waits for this one's, then adds its rule to the file
        this one wrote, and each write keeps its own undo entry."""
        code = ("from finance import db, rules_app\n"
                "conn = db.connect(':memory:')\n"
                "print('ready', flush=True)\n"
                "rules_app.add_many(conn, [{'match_on': 'merchant', 'pattern': 'TOAST*', 'payee': 'Toast',"
                " 'kind': 'Merchant', 'category': 'Transport'}])\n")
        server = Path(__file__).resolve().parent.parent
        with rules_app._RULES_LOCK:
            # this save has read the file and is about to write it
            f = rules_app.RulesFile("merchants.csv")
            f.insert({"match_on": "merchant", "pattern": "KOPITIAM*", "payee": "Kopitiam",
                      "kind": "Merchant", "category": "Transport"})
            child = subprocess.Popen([sys.executable, "-c", code], cwd=server, stdout=subprocess.PIPE, text=True)
            self.addCleanup(child.wait)
            self.addCleanup(child.stdout.close)
            self.assertEqual(child.stdout.readline().strip(), "ready")
            time.sleep(0.5)
            self.assertIsNone(child.poll())          # held off by the flock
            rules_app._commit(self.conn, [f], "add", "Added KOPITIAM*")
        self.assertEqual(child.wait(timeout=30), 0)
        with (self.root / "rules" / "merchants.csv").open() as fh:
            self.assertEqual([r["pattern"] for r in csv.DictReader(fh)], ["KOPITIAM*", "TOAST*"])
        self.assertEqual([(h["id"], h["summary"]) for h in rules_app.history()],
                         [(2, "Added merchant TOAST* (Toast)"), (1, "Added KOPITIAM*")])
        self.assertEqual(list((self.root / "rules").glob(".*")), [])   # no lock or temp file left in rules/

    def test_a_write_never_shares_a_temp_file(self):
        """Another writer's temp file beside the rules file is left alone."""
        d = self.root / "rules"
        d.mkdir()
        theirs = d / ".merchants.csv.tmp"
        theirs.write_bytes(b"half written by someone else")
        rules_app._write_atomic(d / "merchants.csv", b"mine\n")
        self.assertEqual((d / "merchants.csv").read_bytes(), b"mine\n")
        self.assertEqual(theirs.read_bytes(), b"half written by someone else")

    def test_the_queue_totals_sgd_cents(self):
        needs = rules_app.needs_rule(self.conn)
        self.assertEqual(needs[0]["amount"], 1700)

    def test_only_personal_tiers_can_be_added(self):
        with self.assertRaises(rules_app.RuleRefused):
            rules_app.add_many(self.conn, [{"match_on": "type", "pattern": "X", "category": "Transport"}])


class PinTest(DataFolder):
    """One row set on its own by a key rule: a gift, a donation, a one-off."""

    def setUp(self):
        super().setUp()
        # a second row printing the same text, which a pin must leave alone
        self.conn = record(row("k3", "-40.00", date="2026-08-20", merchant="KOPITIAM 0123 AMK",
                               payee="", category="", rule=""))

    def pin(self, **kw):
        return rules_app.pin(self.conn, {"key": "k2", "category": "Transport", **kw})

    def keys_csv(self):
        with (self.root / "rules" / "keys.csv").open(newline="") as f:
            return list(csv.DictReader(f))

    def test_a_group_lists_its_rows_newest_first(self):
        out = rules_app.group_rows(self.conn, text="KOPITIAM 0123 AMK")
        self.assertEqual([r["key"] for r in out["rows"]], ["k3", "k2"])
        self.assertEqual((out["rows"][1]["amount"], out["rows"][1]["currency"], out["total"]), (-1700, "SGD", 2))
        self.assertIn("Transport", out["categories"])
        self.assertIsNone(out["rows"][0]["pinned"])

    def test_one_row_by_key_matched_or_not(self):
        out = rules_app.group_rows(self.conn, key="k1")
        self.assertEqual([(r["key"], r["category"]) for r in out["rows"]], [("k1", "Transport")])
        with self.assertRaises(rules_app.RuleRefused):
            rules_app.group_rows(self.conn)

    def test_each_row_carries_a_rule_for_the_rows_like_it(self):
        # G13: Transactions' "Apply to similar rows" starts from this, in the editor's tiers
        out = rules_app.group_rows(self.conn, key="k2")
        self.assertEqual(out["rows"][0]["similar"],
                         {"match_on": "merchant", "pattern": "KOPITIAM*", "payee": "Kopitiam", "kind": "Merchant"})
        self.assertEqual((out["tiers"][0], out["tier_files"]["merchant"]), ("key", "merchants.csv"))

    def test_a_row_with_only_a_bank_code_has_no_similar_rule(self):
        # a type rule would also catch every card payment that names a merchant
        self.conn.execute("UPDATE txn SET merchant_raw = '', counterparty = '', memo = '' WHERE txn_key = 'k2'")
        self.assertIsNone(rules_app.group_rows(self.conn, key="k2")["rows"][0]["similar"])

    def test_the_similar_rule_saves_through_the_editor_and_can_be_undone(self):
        from finance import rules_editor
        self.pin(payee="Kopitiam", kind="Merchant")
        similar = rules_app.group_rows(self.conn, key="k2")["rows"][0]["similar"]
        draft = {**similar, "payee": "Kopitiam", "category": "Transport", "country": "", "recurring": ""}
        p = rules_editor.preview(self.conn, {"rule": draft})
        # both rows match; the pinned one is won by its key rule, the other changes
        self.assertEqual((p["matches"], p["wins"], p["changes"]), (2, 1, 1))
        self.assertEqual(p["sample"][0]["key"], "k3")
        saved = rules_editor.save(self.conn, {"rule": draft})
        self.assertEqual(saved["file"], "merchants.csv")
        self.assertEqual(rules_app.history()[0]["action"], "add")
        rules_app.undo(saved["change"])
        self.assertNotIn("KOPITIAM*", (self.root / "rules" / "merchants.csv").read_text(encoding="utf-8")
                         if (self.root / "rules" / "merchants.csv").exists() else "")

    def test_an_undo_named_by_its_save_leaves_a_newer_write_alone(self):
        from finance import rules_editor
        rule = {"match_on": "merchant", "pattern": "KOPITIAM*", "payee": "", "category": "Transport"}
        saved = rules_editor.save(self.conn, {"rule": rule})
        self.pin()                                   # another tab writes after it
        with self.assertRaisesRegex(rules_app.RuleRefused, "changed since this save"):
            rules_app.undo(saved["change"])
        self.assertEqual(len(self.keys_csv()), 1)    # the newer write is still there

    def test_an_undone_saves_name_is_not_reused_by_the_next_write(self):
        from finance import rules_editor
        rule = {"match_on": "merchant", "pattern": "KOPITIAM*", "payee": "", "category": "Transport"}
        first = rules_editor.save(self.conn, {"rule": rule})
        rules_app.undo()                             # another tab undoes it
        second = rules_editor.save(self.conn, {"rule": {**rule, "pattern": "KOPI*"}})
        self.assertNotEqual(first["change"], second["change"])
        with self.assertRaisesRegex(rules_app.RuleRefused, "changed since this save"):
            rules_app.undo(first["change"])
        self.assertIn("KOPI*", (self.root / "rules" / "merchants.csv").read_text(encoding="utf-8"))

    def test_a_row_that_prints_no_text_is_found_by_its_bank_code(self):
        self.conn.execute("UPDATE txn SET merchant_raw = '', counterparty = '', memo = '' WHERE txn_key = 'k2'")
        out = rules_app.group_rows(self.conn, text="Debit Card")
        self.assertEqual([r["key"] for r in out["rows"]], ["k2"])

    def test_a_group_is_its_text_whichever_field_printed_it(self):
        # the queue groups by text alone, so a counterparty printing the same text is in the group
        self.conn.execute("UPDATE txn SET counterparty = merchant_raw, merchant_raw = '' WHERE txn_key = 'k3'")
        self.assertEqual(rules_app.needs_rule(self.conn)[0]["rows"], 2)
        out = rules_app.group_rows(self.conn, text="KOPITIAM 0123 AMK")
        self.assertEqual([(r["key"], r["field"]) for r in out["rows"]], [("k3", "counterparty"), ("k2", "merchant")])

    def test_a_known_payee_keeps_its_kind_and_country(self):
        self.pin(payee="Grab", kind="Person")
        rule = self.keys_csv()[0]
        self.assertEqual((rule["payee"], rule["kind"], rule["country"]), ("Grab", "Merchant", "SGP"))
        # G13: and the pin says so, for the rule "Apply to similar rows" starts from
        pinned = rules_app.group_rows(self.conn, key="k2")["rows"][0]["pinned"]
        self.assertEqual((pinned["kind"], pinned["country"]), ("Merchant", "SGP"))

    def test_a_category_only_pick_keeps_the_pins_note(self):
        self.pin(note="a wedding gift")
        self.pin(category="Shopping")                  # no note sent: the category alone
        self.assertEqual([(r["category"], r["notes"].endswith("a wedding gift")) for r in self.keys_csv()],
                         [("Shopping", True)])
        self.pin(category="Shopping", note="")       # an emptied note is a new one
        self.assertTrue(self.keys_csv()[0]["notes"].endswith("this row only, set from the app"))

    def test_a_kind_chosen_under_more_replaces_the_pins_own(self):
        # the pin is the only rule naming this payee, so nothing else describes it
        self.pin(payee="Kopitiam", kind="Merchant")
        self.pin(payee="Kopitiam", kind="Person", note="")
        self.assertEqual(self.keys_csv()[0]["kind"], "Person")
        self.pin(payee="Kopitiam", category="Shopping")           # a quick pick names no kind
        self.assertEqual((self.keys_csv()[0]["kind"], self.keys_csv()[0]["category"]), ("Person", "Shopping"))

    def test_a_category_only_pick_keeps_what_the_rules_page_marked(self):
        self.pin(payee="Kopitiam", kind="Merchant")
        f = rules_app.RulesFile("keys.csv")
        f.replace(2, {**f.rule(2), "recurring": "yes", "country": "SGP"})    # marked on the Rules page
        (self.root / "rules" / "keys.csv").write_bytes(f.data())
        self.pin(payee="Kopitiam", kind="Merchant", category="Shopping")
        rule = self.keys_csv()[0]
        self.assertEqual((rule["category"], rule["recurring"], rule["country"]), ("Shopping", "yes", "SGP"))

    def test_a_wildcard_key_rule_counts_as_setting_the_row(self):
        d = self.root / "rules"
        d.mkdir()
        (d / "keys.csv").write_text("match_on,pattern,payee,kind,country,category,notes\n"
                                    "key,k*,,,,Shopping,\n", encoding="utf-8")
        got = rules_app.group_rows(self.conn, key="k2")["rows"][0]["pinned"]
        self.assertEqual((got["category"], got["line"], got["applied"]), ("Shopping", 2, False))
        with self.assertRaisesRegex(rules_app.RuleRefused, "keys.csv line 2"):
            self.pin()

    def test_a_rule_set_that_will_not_load_is_reported_when_listing(self):
        d = self.root / "rules"
        d.mkdir()
        (d / "keys.csv").write_text("match_on,pattern,payee,kind,country,category,notes\n"
                                    "key,k2,,,,Not a category,\n", encoding="utf-8")
        with self.assertRaises(rules_app.RuleRefused):
            rules_app.group_rows(self.conn, key="k2")

    def test_pinning_writes_a_key_rule_with_a_note(self):
        self.pin(payee="Kopitiam", kind="Merchant", note="a one-off  treat")
        rule = self.keys_csv()[0]
        self.assertEqual((rule["match_on"], rule["pattern"], rule["payee"], rule["kind"], rule["category"]),
                         ("key", "k2", "Kopitiam", "Merchant", "Transport"))
        self.assertEqual(rule["notes"], "17.00 KOPITIAM 0123 AMK 2026-08-03: a one-off treat")
        self.assertFalse((self.root / "rules" / "merchants.csv").exists())

    def test_the_pinned_row_is_filed_and_its_neighbours_stay_unmatched(self):
        self.pin()
        rows = categorise.categorise_rows(rules_app.db_rows(self.conn), rules_app._load_all(self.conn))
        got = {r["txn_key"]: (r["category"], r["rule"]) for r in rows}
        self.assertEqual(got["k2"], ("Transport", "key:k2"))
        self.assertEqual(got["k3"], ("", ""))
        # G13: the pinned row is filed in the record at once, so it leaves the queue
        pinned = rules_app.group_rows(self.conn, text="KOPITIAM 0123 AMK")["rows"]
        self.assertEqual([(r["key"], bool(r["pinned"])) for r in pinned], [("k3", False)])
        mine = rules_app.group_rows(self.conn, key="k2")["rows"][0]
        self.assertEqual((mine["category"], mine["pinned"]["category"], mine["pinned"]["applied"]),
                         ("Transport", "Transport", True))
        self.conn.execute("UPDATE txn SET rule = '' WHERE txn_key = 'k2'")   # as a record loaded before it
        self.assertFalse(rules_app.group_rows(self.conn, key="k2")["rows"][0]["pinned"]["applied"])

    def test_the_pin_summary_prints_the_amount_the_apps_way(self):
        # S$, a true minus, and a foreign amount with its code and thousands commas
        self.pin()
        self.assertEqual(rules_app.history()[0]["summary"], "Set the 3 Aug 2026 row −S$17.00 to Transport")
        foreign = record(row("k4", "-1234.50", currency="USD", date="2026-09-30", merchant="OVERSEAS CAFE"))
        rules_app.pin(foreign, {"key": "k4", "category": "Transport"})
        self.assertEqual(rules_app.history()[0]["summary"],
                         "Set the 30 Sep 2026 row −USD 1,234.50 to Transport")

    def test_a_payee_less_pin_leaves_kind_empty(self):
        self.pin(kind="Merchant")
        self.assertEqual((self.keys_csv()[0]["payee"], self.keys_csv()[0]["kind"]), ("", ""))

    def test_pinning_a_row_again_replaces_its_own_pin(self):
        # G13: the category is picked again in one tap; the row keeps one pin
        first = self.pin()
        again = self.pin(category="Shopping")
        self.assertEqual([r["category"] for r in self.keys_csv()], ["Shopping"])
        self.assertEqual(rules_app.group_rows(self.conn, key="k2")["rows"][0]["category"], "Shopping")
        self.assertEqual(again["relinked"]["changed"], 1)
        self.assertNotEqual(again["change"], first["change"])
        # the same pick twice writes nothing, and names no write an Undo could take back
        self.assertEqual((self.pin(category="Shopping")["change"], len(rules_app.history())), (None, 2))
        rules_app.undo(again["change"], conn=self.conn)
        self.assertEqual([r["category"] for r in self.keys_csv()], ["Transport"])
        self.assertEqual(rules_app.group_rows(self.conn, key="k2")["rows"][0]["category"], "Transport")

    def test_bad_pins_are_refused_and_nothing_is_written(self):
        for bad in ({"key": "nope", "category": "Transport"}, {"key": "k2", "category": ""},
                    {"key": "k2", "category": "Not a category"}, {"key": "k2", "category": "Transport",
                                                                  "payee": "X", "kind": "Robot"}):
            with self.assertRaises(rules_app.RuleRefused, msg=bad):
                rules_app.pin(self.conn, bad)
        self.assertFalse((self.root / "rules" / "keys.csv").exists())

    def test_the_endpoints_read_and_write_through_serve(self):
        import serve
        with mock.patch("finance.api.open_record", lambda: self.conn):
            out = serve.answer("/api/rules/rows", {"text": "KOPITIAM 0123 AMK"})
            self.assertEqual(out["total"], 2)
            with self.assertRaises(ValueError):
                serve.answer("/api/rules/rows", {})
        with mock.patch("finance.api.config", lambda: None), mock.patch.object(db, "connect", lambda: self.conn):
            self.assertEqual(serve.write("/api/rules/pin", {}, b'{"key": "k3", "category": "Transport"}')["file"],
                             "keys.csv")
            with self.assertRaises(serve.Refused):
                serve.write("/api/rules/pin", {}, b'{"key": "k3", "category": ""}')
            self.assertTrue(serve.write("/api/rules/pin", {}, b'{"key": "k3", "category": "Transport"}')["unchanged"])


class ExportTest(DataFolder):
    def test_an_export_is_a_dated_folder_of_csv_and_json(self):
        out = export.export(record(), {"travel": {"id": "travel"}})
        folder = Path(out["folder"])
        self.assertTrue(folder.is_relative_to(self.root / "exports"))
        self.assertIn("transactions.csv", out["files"])
        self.assertIn("stat-travel.json", out["files"])
        with (folder / "transactions.csv").open() as f:
            first = next(csv.DictReader(f))
        self.assertEqual((first["amount_minor"], first["amount"]), ("-1250", "-12.50"))
        self.assertEqual(oct(os.stat(folder / "transactions.csv").st_mode & 0o777), "0o600")

    def test_every_stat_also_exports_its_rows_as_csv(self):
        results = {"travel": {"id": "travel", "chart": {"source": "trips"},
                              "data": {"trips": [{"name": "A", "spend": 12.5}], "other": [{"x": 1}]}}}
        out = export.export(record(), results)
        with (Path(out["folder"]) / "stat-travel.csv").open() as f:
            self.assertEqual(list(csv.DictReader(f)), [{"name": "A", "spend": "12.5"}])

    def test_text_that_a_spreadsheet_would_run_is_escaped(self):
        self.assertEqual(export._cell("=1+1"), "'=1+1")
        self.assertEqual(export._cell("@SUM(A1)"), "'@SUM(A1)")
        self.assertEqual(export._cell("-12.50"), "-12.50")
        self.assertEqual(export._cell(-1250), -1250)
        self.assertEqual(export._cell("GRAB*RIDE"), "GRAB*RIDE")

    def test_two_exports_in_one_second_get_two_folders(self):
        from datetime import datetime
        when = datetime(2026, 10, 1, 12, 0, 0)
        a, b = export.export(record(), {}, now=when), export.export(record(), {}, now=when)
        self.assertNotEqual(a["folder"], b["folder"])
        self.assertIsNotNone(export.path_of(b["stamp"], "transactions.csv"))

    def test_download_paths_cannot_leave_the_export(self):
        out = export.export(record(), {})
        self.assertIsNotNone(export.path_of(out["stamp"], "transactions.csv"))
        for stamp, name in ((out["stamp"], "../config.toml"), ("..", "x.csv"), (out["stamp"], "a/b.csv")):
            self.assertIsNone(export.path_of(stamp, name))


class SetupTest(DataFolder):
    def test_a_new_data_folder_has_folders_config_and_categories(self):
        self.assertTrue(setup.needed())
        out = setup.create(["posb-everyday", "youtrip-usd", "cpf"])
        self.assertFalse(setup.needed())
        cfg = tomllib.loads((self.root / "config.toml").read_text())
        paths = {f["path"]: f for f in cfg["folder"]}
        self.assertEqual(set(paths), {"statements/posb-everyday", "statements/youtrip-usd"})
        self.assertEqual(paths["statements/youtrip-usd"]["wallet"], "USD")
        self.assertEqual(cfg["analytics"]["cpf_folder"], "statements/cpf")
        self.assertEqual(cfg["analytics"]["tiger_folder"], "")
        self.assertEqual(oct(os.stat(self.root).st_mode & 0o777), "0o700")
        conn = db.connect(self.root / "kopifund.db")
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM category").fetchone()[0], out["categories"])

    def test_it_never_overwrites_an_existing_setup(self):
        (self.root / "config.toml").write_text("# mine\n")
        with self.assertRaises(setup.SetupRefused):
            setup.create(["posb-everyday"])
        self.assertEqual((self.root / "config.toml").read_text(), "# mine\n")

    def test_nothing_chosen_is_refused(self):
        with self.assertRaises(setup.SetupRefused):
            setup.create([])


class FirstRunTest(DataFolder):
    """A data folder straight out of setup: empty folders must not fail a run."""

    def setUp(self):
        super().setUp()
        setup.create(["posb-everyday", "youtrip-sgd"])
        from finance.model import load_config
        self.cfg = load_config()

    def test_an_empty_personal_rules_folder_is_not_read(self):
        from finance import sync
        from finance.paths import REPO_RULES
        self.assertEqual(sync.default_rules(self.cfg), [REPO_RULES])
        self.assertEqual(rules_app.rule_paths(), [REPO_RULES])

    def test_folders_with_no_pdf_run_no_parser(self):
        from finance import sync
        parsed = sync.Parsed()
        with mock.patch("subprocess.run") as run:
            sync._run_parser(self.cfg, "finance.parsers.parse_youtrip", ["statements/youtrip-sgd"],
                             self.root, parsed, echo=False)
        run.assert_not_called()
        self.assertEqual((parsed.problems, parsed.failed_roots), ([], set()))


class UploadTest(DataFolder):
    def setUp(self):
        super().setUp()
        setup.create(["posb-everyday"])
        from finance.model import load_config
        from finance import monthly
        self.cfg, self.monthly = load_config(), monthly
        p = mock.patch.object(monthly, "canonical_name", return_value=("2026", "2026-03.pdf"))
        p.start()
        self.addCleanup(p.stop)
        self.target = self.root / "statements/posb-everyday/2026/2026-03.pdf"

    def upload(self, data):
        return self.monthly.upload(self.cfg, "statements/posb-everyday", data)

    def test_a_statement_is_kept_once_and_never_replaced(self):
        self.assertEqual(self.upload(b"%PDF one")["status"], "added")
        self.assertEqual(oct(os.stat(self.target).st_mode & 0o777), "0o600")
        self.assertEqual(self.upload(b"%PDF one")["status"], "already-there")
        with self.assertRaises(self.monthly.UploadRefused):
            self.upload(b"%PDF two")
        self.assertEqual(self.target.read_bytes(), b"%PDF one")

    def test_a_pdf_pdftotext_cannot_read_is_refused_in_plain_words(self):
        import subprocess
        err = subprocess.CalledProcessError(1, ["pdftotext", "-layout", str(self.root / "cache/inbox/x.pdf"), "-"])
        with mock.patch.object(self.monthly, "canonical_name", side_effect=err):
            with self.assertRaises(self.monthly.UploadRefused) as cm:
                self.upload(b"%PDF broken")
        # the page shows this to the owner: no command line, no path in the data folder
        self.assertIn("could not be read as a PDF", str(cm.exception))
        self.assertNotIn("pdftotext", str(cm.exception))
        self.assertNotIn(str(self.root), str(cm.exception))
        self.assertFalse(self.target.exists())

    def test_a_racing_upload_does_not_overwrite_the_first(self):
        real_rglob = Path.rglob

        def first_lands_meanwhile(path, pattern):
            if not self.target.exists():
                self.target.parent.mkdir(parents=True, exist_ok=True)
                self.target.write_bytes(b"%PDF first")
            return real_rglob(path, pattern)

        with mock.patch.object(Path, "rglob", first_lands_meanwhile):
            with self.assertRaises(self.monthly.UploadRefused):
                self.upload(b"%PDF second")
        self.assertEqual(self.target.read_bytes(), b"%PDF first")


if __name__ == "__main__":
    unittest.main()
