"""Saves that reach the record at once (G13, server/finance/relink.py).

A saved rule, a single row's category or a trip relinks the rows it decides
differently straight away, and the record must then be exactly what a full
load (parse, categorise, local.load) with the same rules and trips writes:
every row's payee, category, rule and trip, and the payee table. Each write's
Undo puts back exactly what it wrote. Every row and rule here is invented,
and every write goes into a temporary data folder."""
import isolate  # noqa: F401  first: no test writes the real record or cache
import json
from types import SimpleNamespace
from unittest import mock

from finance.parsers import categorise
from finance import snapshot as snap
from finance import db, local, page_cache, planning, relink, rules_app, rules_editor, stats_rebuild, sync
from support import DataFolder
from test_db import block, parsed, row

HEADER = "match_on,pattern,payee,kind,country,category,notes,account_no,recurring\n"
WALLET = "999-00000-1"
JAPAN = {"name": "Japan", "start": "2026-08-14", "end": "2026-08-18", "countries": ["JPN"]}
OSAKA = {"name": "Osaka", "start": "2026-08-20", "end": "2026-08-21", "countries": ["JPN"]}


def statement_rows():
    """What the parsers give for one invented month: a shipped merchant, two
    shops one pattern catches, a transfer to a person, rows abroad and on a
    yen wallet, a salary. The payee, category and rule the helper fills are
    overwritten by the categorise step."""
    return [
        row("g1", "-12.50", merchant="GRAB*123"),
        row("c1", "-4.20", date="2026-08-04", merchant="KOPI CORNER 12 AMK"),
        row("c2", "-5.10", date="2026-08-09", merchant="KOPI CORNER 98 TPY"),
        row("n1", "-9.00", date="2026-08-10", merchant="NOODLE HOUSE 3"),
        row("p1", "-50.00", date="2026-08-12", merchant="", counterparty="A FRIEND", bank_txn_type="FAST"),
        row("j1", "-30.00", date="2026-08-15", merchant="SHOP TOKYO", merchant_country="JPN"),
        row("j2", "-2000", date="2026-08-16", currency="JPY", account_no=WALLET, merchant="KONBINI 7",
            merchant_country="", source="wallet/2026/2026-08.pdf"),
        row("j3", "-8.00", date="2026-08-21", merchant="SHOP OSAKA", merchant_country="JPN"),
        row("s1", "2500.00", date="2026-08-25", merchant="", memo="SALARY PAYMENT", bank_txn_type="FAST",
            direction="In"),
    ]


def links(conn):
    """Each live row's payee, category, rule and trip, by name, as the pages read them."""
    return {r["txn_key"]: (r["payee_name"] or "", r["category_name"] or "", r["rule"], r["trip_name"] or "")
            for r in conn.execute("SELECT * FROM v_txn")}


def payees(conn):
    return sorted(tuple(r) for r in conn.execute("SELECT name, kind, country, recurring, account_no FROM payee"))


def trips(conn):
    codes, pins = db.trip_countries(conn), db.trip_pins(conn, follow=False)
    return sorted((r["name"], r["start_date"], r["end_date"], ",".join(codes.get(r["id"], [])),
                   ",".join(pins.get(r["id"], [])), r["notes"])
                  for r in conn.execute("SELECT id, name, start_date, end_date, notes FROM trip"))


class Relink(DataFolder):
    def setUp(self):
        super().setUp()
        self.dir = self.root / "rules"
        self.cfg = SimpleNamespace(
            root=self.root, wallet_accounts=lambda: set(),
            folders=[SimpleNamespace(path="wallet", wallet="JPY", multi_currency=False, per_currency_subfolders=False)])
        self.conn = self.full_load()
        self.addCleanup(lambda: self.conn.close())

    def full_load(self, conn=None, path=":memory:"):
        """`finance.py load` into `conn` (a new record by default): the
        parsed rows through the rules as they are on disk now, then
        local.load, which places every row on the record's trips."""
        if conn is None:
            conn = db.connect(path)
            self.addCleanup(conn.close)
            db.seed_categories(conn)
        p = parsed([block(), block(period=f"{WALLET} · JPY · 2026-08", account_no=WALLET, currency="JPY",
                                   source="wallet/2026/2026-08.pdf")], statement_rows())
        sync.categorise(p, rules_app.rule_paths(), categories=list(categorise.KNOWN_CATEGORIES))
        with mock.patch.object(snap, "read_portfolio", return_value=None), \
                mock.patch.object(snap, "read_cpf", return_value=None):
            local.load(self.cfg, conn, p)
        return conn

    def same_as_a_load(self, write, plain=None):
        """Run `write` (a save, which relinks at once) on the record, and on a
        copy of the record as it was, `plain` (the same trip write with no
        relink; a rule write's files are already on disk) followed by a full
        load. The two records must hold the same links and payees."""
        copy = db.connect(":memory:")
        self.addCleanup(copy.close)
        self.conn.backup(copy)
        out = write()
        if plain:
            plain(copy)
        self.full_load(copy)
        self.assertEqual(links(self.conn), links(copy))
        self.assertEqual(payees(self.conn), payees(copy))
        self.assertEqual(trips(self.conn), trips(copy))
        return out

    def assert_as_loaded(self):
        """A load now changes nothing: the record holds what the rules and trips say."""
        self.same_as_a_load(lambda: None)

    def state(self):
        return links(self.conn), payees(self.conn), trips(self.conn)

    def kopi(self, **kw):
        return {"match_on": "merchant", "pattern": "KOPI CORNER*", "payee": "Kopi Corner", "kind": "Merchant",
                "country": "SGP", "category": "Eating out", "recurring": "", **kw}

    def listed(self, pattern):
        return next(r for r in rules_editor.overview(self.conn)["rules"] if r["pattern"] == pattern)

    def edit_of(self, rule):
        return {k: rule[k] for k in ("file", "line", "pattern", "revision")}


class SameAsALoadTest(Relink):
    """Every save leaves the record as a full load with the same rules and trips would."""

    def test_each_rule_save_in_turn_matches_a_full_load(self):
        self.assert_as_loaded()
        before = links(self.conn)
        out = self.same_as_a_load(lambda: rules_editor.save(self.conn, {"rule": self.kopi()}))
        self.assertEqual(out["relinked"]["changed"], 2)
        self.assertEqual(links(self.conn)["c1"][:3], ("Kopi Corner", "Eating out", "merchant:KOPI CORNER*"))
        self.assertEqual({k for k in before if before[k] != links(self.conn)[k]}, {"c1", "c2"})

        self.same_as_a_load(lambda: rules_app.pin(self.conn, {"key": "n1", "category": "Shopping",
                                                              "payee": "Noodle House", "kind": "Merchant"}))
        self.assertEqual(links(self.conn)["n1"][:3], ("Noodle House", "Shopping", "key:n1"))

        # an edit moves its rows again; a new kind for the payee reaches the payee table
        self.same_as_a_load(lambda: rules_editor.save(self.conn, {
            "rule": self.kopi(category="Groceries", kind="Institution"),
            "edit": self.edit_of(self.listed("KOPI CORNER*"))}))
        self.assertEqual(links(self.conn)["c2"][1], "Groceries")
        self.assertIn(("Kopi Corner", "Institution", "SGP", 0, ""), payees(self.conn))

        rule = self.listed("KOPI CORNER*")
        self.same_as_a_load(lambda: rules_app.remove(rule["file"], rule["line"], rule["pattern"], rule["revision"],
                                                     conn=self.conn))
        self.assertEqual(links(self.conn)["c1"][:3], ("", "", ""))

        self.same_as_a_load(lambda: rules_app.add_many(self.conn, [{
            "match_on": "counterparty", "pattern": "A FRIEND", "payee": "A Friend", "kind": "Person",
            "category": "Donations"}]))
        self.assertEqual(links(self.conn)["p1"][:2], ("A Friend", "Donations"))

    def trip(self, t, old_name=None):
        """Save a trip as the app does, beside the same save with no relink on the copy."""
        return self.same_as_a_load(lambda: planning.save_trip(self.conn, t, old_name=old_name, cfg=self.cfg),
                                   lambda copy: planning.save_trip(copy, t, old_name=old_name))

    def test_trips_saved_nested_renamed_and_removed_match_a_full_load(self):
        self.trip(JAPAN)
        got = links(self.conn)
        self.assertEqual({k for k, v in got.items() if v[3] == "Japan"}, {"j1", "j2", "j3"})   # j3 in its settling days

        self.trip(OSAKA)                                              # a side trip takes its own day
        self.assertEqual(links(self.conn)["j3"][3], "Osaka")

        out = self.trip({**JAPAN, "name": "Japan 2026"}, old_name="Japan")
        self.assertEqual(out["relinked"]["changed"], 0)                 # a new name moves no row
        self.assertEqual(links(self.conn)["j1"][3], "Japan 2026")

        self.trip({**JAPAN, "name": "Japan 2026", "start": "2026-08-16"})
        self.assertEqual((links(self.conn)["j1"][3], links(self.conn)["j2"][3]), ("", "Japan 2026"))

        out = self.same_as_a_load(lambda: planning.remove_trip(self.conn, "Osaka", cfg=self.cfg),
                                  lambda copy: planning.remove_trip(copy, "Osaka"))
        self.assertEqual(links(self.conn)["j3"][3], "Japan 2026")       # in its settling days again

    def test_a_removed_side_trip_hands_its_rows_to_the_longer_one(self):
        self.trip(JAPAN)
        self.trip(OSAKA)
        japan = self.conn.execute("SELECT id FROM trip WHERE name = 'Japan'").fetchone()[0]
        out = self.same_as_a_load(lambda: planning.remove_trip(self.conn, "Osaka", cfg=self.cfg),
                                  lambda copy: planning.remove_trip(copy, "Osaka"))
        self.assertEqual((out["restore"]["txn_keys"], out["restore"]["moved"]), (["j3"], {"j3": japan}))
        self.assertEqual(links(self.conn)["j3"][3], "Japan")
        self.same_as_a_load(lambda: planning.restore_trip(self.conn, out["restore"]),
                            lambda copy: planning.restore_trip(copy, out["restore"]))
        self.assertEqual(links(self.conn)["j3"][3], "Osaka")

    def test_apply_rules_relinks_a_rules_file_changed_by_hand(self):
        self.dir.mkdir()
        (self.dir / "merchants.csv").write_text(HEADER + "merchant,NOODLE HOUSE*,Noodle House,Merchant,SGP,"
                                                         "Eating out,,,\n", encoding="utf-8")
        planning.save_trip(self.conn, JAPAN)                           # no config: the rows wait, as before G13
        self.assertEqual(rules_editor.overview(self.conn)["pending"], 1)
        out = self.same_as_a_load(lambda: rules_app.apply(self.conn, self.cfg))
        self.assertEqual(out["relinked"]["changed"], 4)                # the noodle row and three on the trip
        self.assertEqual(rules_editor.overview(self.conn)["pending"], 0)

    def test_a_save_leaves_rows_held_differently_for_another_reason_to_apply_rules(self):
        self.conn.execute("UPDATE txn SET category_id = NULL WHERE txn_key = 'g1'")   # as an older load left it
        rules_editor.save(self.conn, {"rule": self.kopi()})
        self.assertEqual(links(self.conn)["g1"][1], "")
        self.assertEqual(rules_editor.overview(self.conn)["pending"], 1)

    def test_a_rule_naming_a_category_no_longer_shipped_brings_it_back_as_a_load_does(self):
        self.same_as_a_load(lambda: rules_editor.save(self.conn, {"rule": self.kopi(category="Transport")}))
        self.assertEqual(links(self.conn)["c1"][1], "Transport")


class UndoTest(Relink):
    """Each write's Undo puts back exactly what it wrote, and nothing a later write changed."""

    def test_a_rule_save_and_its_undo(self):
        before = self.state()
        out = rules_editor.save(self.conn, {"rule": self.kopi()})
        self.assertIn(("Kopi Corner", "Merchant", "SGP", 0, ""), payees(self.conn))
        back = rules_app.undo(out["change"], conn=self.conn)
        self.assertEqual(back["relinked"]["changed"], 2)
        self.assertEqual(self.state(), before)                        # the new payee is gone too

    def test_an_edit_and_its_undo_put_the_payee_back(self):
        rules_editor.save(self.conn, {"rule": self.kopi()})
        before = self.state()
        out = rules_editor.save(self.conn, {"rule": self.kopi(category="Groceries", kind="Institution"),
                                            "edit": self.edit_of(self.listed("KOPI CORNER*"))})
        self.assertNotEqual(self.state(), before)
        rules_app.undo(out["change"], conn=self.conn)
        self.assertEqual(self.state(), before)

    def test_a_pin_and_its_undo(self):
        before = self.state()
        out = rules_app.pin(self.conn, {"key": "n1", "category": "Shopping"})
        self.assertEqual((out["relinked"]["changed"], bool(out["change"])), (1, True))
        rules_app.undo(out["change"], conn=self.conn)
        self.assertEqual(self.state(), before)

    def test_a_delete_and_its_undo(self):
        rules_editor.save(self.conn, {"rule": self.kopi()})
        before = self.state()
        rule = self.listed("KOPI CORNER*")
        out = rules_app.remove(rule["file"], rule["line"], rule["pattern"], rule["revision"], conn=self.conn)
        self.assertEqual(out["relinked"]["changed"], 2)
        rules_app.undo(out["change"], conn=self.conn)
        self.assertEqual(self.state(), before)
        self.assert_as_loaded()

    def test_a_batch_and_its_undo(self):
        before = self.state()
        out = rules_app.add_many(self.conn, [self.kopi(), {"match_on": "counterparty", "pattern": "A FRIEND",
                                                           "payee": "", "category": "Donations"}])
        self.assertEqual(out["relinked"]["changed"], 3)
        rules_app.undo(out["change"], conn=self.conn)
        self.assertEqual(self.state(), before)

    def test_the_rules_page_undo_with_no_token_puts_the_rows_back_too(self):
        before = self.state()
        rules_editor.save(self.conn, {"rule": self.kopi()})
        rules_app.undo(conn=self.conn)
        self.assertEqual(self.state(), before)

    def test_an_undo_leaves_a_row_a_later_write_changed(self):
        out = rules_editor.save(self.conn, {"rule": self.kopi()})
        shopping = db.category_ids(self.conn)["Shopping"]
        self.conn.execute("UPDATE txn SET category_id = ? WHERE txn_key = 'c2'", (shopping,))
        rules_app.undo(out["change"], conn=self.conn)
        got = links(self.conn)
        self.assertEqual((got["c1"][:3], got["c2"][1]), (("", "", ""), "Shopping"))

    def test_a_new_trip_and_its_undo(self):
        before = self.state()
        out = planning.save_trip(self.conn, JAPAN, cfg=self.cfg)
        self.assertEqual((out["relinked"]["changed"], out["undo"]["before"]), (3, None))
        back = planning.undo_trip_save(self.conn, json.loads(json.dumps(out["undo"])))   # as the page sends it back
        self.assertEqual(back["relinked"]["changed"], 3)
        self.assertEqual(self.state(), before)

    def test_an_edited_and_renamed_trip_and_its_undo(self):
        planning.save_trip(self.conn, JAPAN, cfg=self.cfg)
        before = self.state()
        out = planning.save_trip(self.conn, {**JAPAN, "name": "Japan 2026", "end": "2026-08-15", "notes": "n"},
                                 old_name="Japan", cfg=self.cfg)
        self.assertEqual(out["relinked"]["changed"], 1)                # j3 is past its settling days now
        planning.undo_trip_save(self.conn, out["undo"])
        self.assertEqual(self.state(), before)
        self.assert_as_loaded()

    def test_a_trip_undo_leaves_a_row_a_later_write_moved_and_refuses_a_trip_edited_since(self):
        out = planning.save_trip(self.conn, JAPAN, cfg=self.cfg)
        self.conn.execute("UPDATE txn SET trip_id = NULL WHERE txn_key = 'j1'")
        osaka = planning.save_trip(self.conn, OSAKA)                   # a later save, no relink
        self.assertEqual(osaka["relinked"], {"changed": 0, "written": 0})
        back = planning.undo_trip_save(self.conn, out["undo"])
        self.assertEqual(back["relinked"]["changed"], 2)             # j2 and j3; j1 was moved since
        self.assertIsNone(self.conn.execute("SELECT 1 FROM trip WHERE name = 'Japan'").fetchone())
        again = planning.save_trip(self.conn, JAPAN, cfg=self.cfg)
        planning.save_trip(self.conn, {**JAPAN, "notes": "changed in another tab"}, cfg=self.cfg)
        with self.assertRaises(planning.PlanRefused):
            planning.undo_trip_save(self.conn, again["undo"])
        with self.assertRaises(planning.PlanRefused):
            planning.undo_trip_save(self.conn, None)

    def test_a_revert_never_points_a_row_at_a_trip_that_is_gone(self):
        planning.save_trip(self.conn, JAPAN, cfg=self.cfg)
        change = relink.Change(relink.TRIP_LINKS)
        now = self.conn.execute("SELECT trip_id FROM txn WHERE txn_key = 'j1'").fetchone()[0]
        change.rows.append(["j1", [987654], [now]])                     # a trip id no trip has
        self.assertEqual(relink.revert(self.conn, change.data()), {"changed": 0, "written": 0})
        self.assertEqual(links(self.conn)["j1"][3], "Japan")
        self.assertEqual(relink.revert(self.conn, {"cols": ["amount_minor"], "rows": [["j1", [0], [0]]]}),
                         {"changed": 0, "written": 0})


class ServeTest(Relink):
    """Through the server: the answer says what changed and the stats rerun in the background."""

    def write(self, path, body):
        import serve
        with mock.patch("finance.api.config", lambda: self.cfg), \
                mock.patch.object(db, "connect", lambda *a: self.conn), \
                mock.patch("finance.stats_rebuild.refresh_stats") as refresh:
            out = serve.write(path, {}, json.dumps(body).encode())
        return out, refresh.call_count

    def test_a_rule_save_answers_with_the_rows_changed_and_reruns_the_stats(self):
        out, reruns = self.write("/api/rules/save", {"rule": self.kopi()})
        self.assertEqual((out["relinked"]["changed"], out["stats"], reruns), (2, "rebuilding", 1))
        out, reruns = self.write("/api/rules/undo", {"change": out["change"]})
        self.assertEqual((out["relinked"]["changed"], out["stats"], reruns), (2, "rebuilding", 1))

    def test_a_save_that_changes_nothing_reruns_nothing(self):
        self.write("/api/rules/save", {"rule": self.kopi()})
        out, reruns = self.write("/api/rules/save", {"rule": self.kopi(pattern="KOPI CORNER 12*")})
        self.assertEqual((out["relinked"], "stats" in out, reruns), ({"changed": 0, "written": 0}, False, 0))

    def test_trips_save_undo_remove_and_restore(self):
        out, reruns = self.write("/api/trips", JAPAN)
        self.assertEqual((out["relinked"]["changed"], reruns), (3, 1))
        out, reruns = self.write("/api/trips/undo", {"undo": out["undo"]})
        self.assertEqual((out["removed"], out["relinked"]["changed"], reruns), ("Japan", 3, 1))
        self.write("/api/trips", JAPAN)
        out, reruns = self.write("/api/trips/remove", {"name": "Japan"})
        # every row it counted lost it, so the stats rerun though no row went to another trip
        self.assertEqual((out["relinked"]["changed"], out["restore"]["moved"], reruns), (3, {}, 1))
        out, reruns = self.write("/api/trips/restore", out["restore"])
        self.assertEqual((out["relinked"]["changed"], reruns), (3, 1))
        self.assert_as_loaded()

    def test_undoing_a_side_trip_returns_its_rows_to_a_stay_renamed_meanwhile(self):
        self.write("/api/trips", JAPAN)
        side, _ = self.write("/api/trips", OSAKA)                      # takes j3 from Japan
        self.write("/api/trips", {**JAPAN, "name": "Japan 2026", "old_name": "Japan"})
        out, _ = self.write("/api/trips/undo", {"undo": side["undo"]})
        self.assertEqual((out["removed"], out["relinked"]["changed"]), ("Osaka", 1))
        self.assertEqual(links(self.conn)["j3"][3], "Japan 2026")
        self.assert_as_loaded()

    def test_restoring_a_side_trip_never_follows_a_reused_trip_id(self):
        self.write("/api/trips", JAPAN)
        self.write("/api/trips", OSAKA)
        removed, _ = self.write("/api/trips/remove", {"name": "Osaka"})          # j3 goes to Japan
        japan = self.conn.execute("SELECT id FROM trip WHERE name = 'Japan'").fetchone()[0]
        self.assertEqual(removed["restore"]["moved"], {"j3": japan})
        self.write("/api/trips/remove", {"name": "Japan"})
        self.write("/api/trips", {**JAPAN, "name": "Somewhere new", "start": "2026-08-15"})
        self.assertEqual(self.conn.execute("SELECT id FROM trip WHERE name = 'Somewhere new'").fetchone()[0], japan)
        out, _ = self.write("/api/trips/restore", removed["restore"])
        # Osaka takes back its own day, as a load would; the new trip keeps the rest
        self.assertEqual({k: v[3] for k, v in links(self.conn).items() if v[3]},
                         {"j1": "Somewhere new", "j2": "Somewhere new", "j3": "Osaka"})
        self.assertEqual(out["relinked"]["changed"], 1)
        self.assert_as_loaded()

    def test_undoing_an_edit_puts_the_trip_and_its_rows_back(self):
        self.write("/api/trips", JAPAN)
        before = self.state()
        edit, _ = self.write("/api/trips", {**JAPAN, "end": "2026-08-15", "old_name": "Japan"})
        self.assertEqual(edit["relinked"]["changed"], 1)              # j3 is past its settling days
        out, _ = self.write("/api/trips/undo", {"undo": edit["undo"]})
        self.assertEqual((out["name"], out["relinked"]["changed"], self.state()), ("Japan", 1, before))

    def test_a_trip_save_that_moves_no_row_still_reruns_the_stats(self):
        # the travel stat's saved results name the trip and its dates
        self.write("/api/trips", JAPAN)
        out, reruns = self.write("/api/trips", {**JAPAN, "name": "Japan 2026", "old_name": "Japan"})
        self.assertEqual((out["relinked"]["changed"], out["stats"], reruns), (0, "rebuilding", 1))

    def test_a_plan_save_reruns_the_stats_for_the_forward_view(self):
        out, reruns = self.write("/api/plan", {"dca": 100, "to": "2027-06"})
        self.assertEqual(("stats" in out, reruns), (False, 1))      # the answer is the plan the page keeps

    def test_apply_rules(self):
        out, reruns = self.write("/api/rules/apply", {})
        self.assertEqual((out["relinked"], reruns), ({"changed": 0, "written": 0}, 0))

    def test_a_relink_moves_the_data_version(self):
        import serve
        self.conn = self.full_load(path=db.db_path())
        self.addCleanup(self.conn.close)
        seen = page_cache.data_version()
        planning.save_trip(self.conn, JAPAN, cfg=self.cfg)
        self.assertNotEqual(page_cache.data_version(), seen)


class RulesPageTest(Relink):
    """What the Rules page is told about Apply rules, and writes that cannot finish."""

    def test_apply_rules_shows_for_a_payee_edited_by_hand(self):
        o = rules_editor.overview(self.conn)
        self.assertEqual((o["pending"], o["stale"]), (0, False))
        self.dir.mkdir()
        # the same payee and category as the shipped rule; only the payee's country differs
        (self.dir / "merchants.csv").write_text(HEADER + "merchant,GRAB*,Grab,Merchant,MYS,Transport (private),,,\n",
                                                encoding="utf-8")
        o = rules_editor.overview(self.conn)
        self.assertEqual((o["pending"], o["stale"]), (0, True))
        rules_app.apply(self.conn, self.cfg)
        self.assertFalse(rules_editor.overview(self.conn)["stale"])
        self.assert_as_loaded()

    def test_an_undo_that_cannot_drop_its_entry_leaves_files_and_rows_as_they_were(self):
        from pathlib import Path
        out = rules_editor.save(self.conn, {"rule": self.kopi()})
        before = self.state()
        kept = (self.dir / "merchants.csv").read_bytes()
        with mock.patch.object(Path, "unlink", side_effect=OSError("disk says no")):
            with self.assertRaises(rules_app.RuleRefused):
                rules_app.undo(out["change"], conn=self.conn)
        self.assertEqual(((self.dir / "merchants.csv").read_bytes(), self.state()), (kept, before))
        rules_app.undo(out["change"], conn=self.conn)               # and it can still be undone
        self.assertEqual(links(self.conn)["c1"][:3], ("", "", ""))

    def test_a_save_whose_history_cannot_be_kept_writes_neither_files_nor_rows(self):
        before = self.state()
        with mock.patch.object(rules_app, "_remember", side_effect=OSError("disk says no")):
            with self.assertRaises(rules_app.RuleRefused):
                rules_editor.save(self.conn, {"rule": self.kopi()})
        self.assertEqual(self.state(), before)
        self.assertFalse((self.dir / "merchants.csv").exists())

    def test_an_undo_whose_rows_do_not_commit_keeps_its_entry_to_try_again(self):
        import sqlite3
        out = rules_editor.save(self.conn, {"rule": self.kopi()})
        before, kept = self.state(), (self.dir / "merchants.csv").read_bytes()
        with mock.patch.object(relink, "revert", side_effect=sqlite3.OperationalError("disk I/O error")):
            with self.assertRaises(rules_app.RuleRefused):
                rules_app.undo(out["change"], conn=self.conn)
        self.assertEqual(((self.dir / "merchants.csv").read_bytes(), self.state()), (kept, before))
        self.assertEqual(rules_app.history()[0]["summary"], out["summary"])
        rules_app.undo(out["change"], conn=self.conn)
        self.assertEqual(links(self.conn)["c1"][:3], ("", "", ""))
        self.assertEqual(list((self.root / "rules-history").glob("*.undoing")), [])

    def test_a_save_whose_rows_do_not_commit_leaves_no_entry_behind(self):
        import sqlite3
        from contextlib import contextmanager
        real = db.transaction

        @contextmanager
        def commit_fails(conn):
            # the outer transaction, the one the entry is written in, fails as it commits
            outer = not conn.in_transaction
            with real(conn):
                yield conn
                if outer:
                    raise sqlite3.OperationalError("disk I/O error")
        before = self.state()
        with mock.patch.object(db, "transaction", commit_fails):
            with self.assertRaises(rules_app.RuleRefused):
                rules_editor.save(self.conn, {"rule": self.kopi()})
        self.assertEqual((self.state(), rules_app.history()), (before, []))
        self.assertFalse((self.dir / "merchants.csv").exists())

    def test_apply_counts_a_row_both_passes_move_once(self):
        self.dir.mkdir()
        (self.dir / "merchants.csv").write_text(HEADER + "merchant,SHOP TOKYO*,Tokyo Shop,Merchant,JPN,Shopping,,,\n",
                                                encoding="utf-8")
        planning.save_trip(self.conn, JAPAN)                           # no config: its rows wait
        out = self.same_as_a_load(lambda: rules_app.apply(self.conn, self.cfg))
        self.assertEqual(out["relinked"]["changed"], 3)               # j1 (both), j2 and j3


class TripIdTest(Relink):
    def test_a_revert_never_puts_a_row_on_a_new_trip_given_an_old_ones_id(self):
        planning.save_trip(self.conn, JAPAN)
        gone = self.conn.execute("SELECT id FROM trip WHERE name = 'Japan'").fetchone()[0]
        self.conn.execute("UPDATE txn SET trip_id = ? WHERE txn_key = 'j1'", (gone,))
        change = relink.Change(relink.TRIP_LINKS)
        with self.conn:
            relink._write(self.conn, change, next(r for r in relink.record_rows(self.conn) if r["txn_key"] == "j1"),
                          {"trip_id": None})
        planning.remove_trip(self.conn, "Japan")
        planning.save_trip(self.conn, {**OSAKA, "name": "Somewhere new"})
        self.assertEqual(self.conn.execute("SELECT id FROM trip WHERE name = 'Somewhere new'").fetchone()[0], gone)
        self.assertEqual(relink.revert(self.conn, change.data())["changed"], 0)
        self.assertEqual(links(self.conn)["j1"][3], "")


class StatsDelayTest(Relink):
    def test_saves_in_quick_succession_share_one_stats_run(self):
        import threading
        import serve
        from finance import monthly
        runs, done = [], threading.Event()

        def analyse(cfg):
            runs.append(cfg)
            done.set()
        with mock.patch.object(monthly, "analyse", side_effect=analyse), mock.patch("finance.stats_rebuild.STATS_DELAY", 0.2), \
                mock.patch("finance.page_cache.wrote"):
            for _ in range(3):                          # three saves while the rebuild waits to start
                stats_rebuild.refresh_stats("cfg")
            self.assertTrue(done.wait(5))
            for _ in range(200):
                if not stats_rebuild._STATS["running"]:
                    break
                threading.Event().wait(0.01)
        self.assertEqual((len(runs), stats_rebuild._STATS), (1, {"running": False, "again": False}))

