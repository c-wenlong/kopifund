"""The Rules page's editor (G7): rules grouped by payee, the lint beside them,
the preview of an edit, saving into the right personal file, overriding a
shared rule, and undo. Every rule here is invented, and every write goes into
a temporary data folder."""
import isolate  # noqa: F401  first: no test writes the real record or cache
import csv
import json
import os
from unittest import mock

from finance.parsers import categorise
from finance import rules_app, rules_editor
from finance.paths import REPO_RULES, rules_history_dir
from test_app_writes import DataFolder, record
from test_db import row

HEADER = "match_on,pattern,payee,kind,country,category,notes,account_no,recurring\n"


def pay(key, date):
    """A salary credit whose bank line reads the same for every employer."""
    return row(key, "2500.00", date=date, merchant="", memo="SALARY PAYMENT", bank_txn_type="FAST",
               payee="", category="", rule="")


class Editor(DataFolder):
    def setUp(self):
        super().setUp()
        # k1 GRAB*123 (a shipped rule names it), k2 and k3 two unmatched coffee shops
        self.conn = record(pay("s1", "2026-08-25"), pay("s2", "2026-07-25"),
                           row("k3", "-4.20", date="2026-08-20", merchant="KOPITIAM 0456 TPY",
                               payee="", category="", rule=""))
        self.dir = self.root / "rules"
        self.dir.mkdir()
        self.put("keys.csv", "key,s1,Acme Co,Employer,SGP,Salary,August pay,,\n"
                             "key,s2,Acme Co,Employer,SGP,Salary,July pay,,\n")

    def put(self, name, body, header=HEADER):
        (self.dir / name).write_text(header + body, encoding="utf-8")

    def read(self, name):
        with (self.dir / name).open(newline="", encoding="utf-8") as fh:
            return list(csv.DictReader(fh))

    def overview(self):
        return rules_editor.overview(self.conn)

    def rule(self, **kw):
        return {"match_on": "merchant", "pattern": "KOPITIAM*", "payee": "Kopitiam", "kind": "Merchant",
                "country": "SGP", "category": "Eating out", "recurring": "", **kw}

    def save(self, edit=None, **kw):
        return rules_editor.save(self.conn, {"rule": self.rule(**kw), "edit": edit})

    def preview(self, edit=None, **kw):
        return rules_editor.preview(self.conn, {"rule": self.rule(**kw), "edit": edit})

    def find(self, overview, **kw):
        return next(r for r in overview["rules"] if all(r[k] == v for k, v in kw.items()))


class GroupingTest(Editor):
    def test_one_payee_is_one_group_with_its_pins(self):
        o = self.overview()
        acme = next(g for g in o["groups"] if g["payee"] == "Acme Co")
        self.assertEqual((acme["pins"], acme["patterns"], acme["rows"], acme["kind"], acme["category"]),
                         (2, 0, 2, "Employer", "Salary"))
        self.assertEqual(acme["rules"], ["keys.csv:2", "keys.csv:3"])
        pins = [self.find(o, id=i)["pins"] for i in acme["rules"]]
        self.assertEqual([(p[0]["date"], p[0]["amount"], p[0]["currency"]) for p in pins],
                         [("2026-08-25", 250000, "SGD"), ("2026-07-25", 250000, "SGD")])

    def test_a_shared_rule_joins_its_payee_and_rules_with_no_payee_group_by_category(self):
        self.save(pattern="GRAB FOOD*", payee="Grab", category="Eating out")
        o = self.overview()
        grab = next(g for g in o["groups"] if g["payee"] == "Grab")
        self.assertEqual((grab["patterns"], grab["shared"], grab["rows"]), (2, 1, 1))
        self.assertEqual(grab["categories"], ["Transport (private)", "Eating out"])
        top_up = next(g for g in o["groups"] if g["id"] == "category:Wallet top-up")
        self.assertEqual((top_up["payee"], top_up["rows"]), ("", 0))
        self.assertTrue(all(self.find(o, id=i)["tier"] in ("type", "flow") for i in top_up["rules"]))

    def test_rules_are_listed_in_the_order_they_are_tried(self):
        rules = self.overview()["rules"]
        tiers = [r["tier"] for r in rules]
        self.assertEqual(tiers, sorted(tiers, key=categorise.MATCH_ON.index))
        self.assertEqual([r["order"] for r in rules], list(range(1, len(rules) + 1)))
        self.assertEqual(rules[0]["id"], "keys.csv:2")

    def test_the_lint_shows_beside_the_rules_it_names(self):
        self.put("merchants.csv", "merchant,KOPITIAM 0123*,Kopitiam,Merchant,SGP,Eating out,,,\n"
                                  "merchant,KOPITIAM 0456*,Kopitiam,Merchant,MYS,Eating out,,,\n"
                                  "merchant,NOWHERE*,Nowhere,Merchant,SGP,Shopping,,,\n")
        o = self.overview()
        self.assertEqual([p["check"] for p in self.find(o, pattern="KOPITIAM 0456*")["problems"]],
                         ["Two countries"])
        dead = self.find(o, pattern="NOWHERE*")["problems"][0]
        self.assertEqual((dead["check"], dead["why"]), ("Never wins", "Never wins: matches no row."))
        self.assertEqual(next(g for g in o["groups"] if g["payee"] == "Kopitiam")["problems"], 2)

    def test_pending_counts_rows_the_record_holds_differently_from_the_rules(self):
        # the pins, not loaded yet, and the Grab row the record holds under Transport,
        # which the shared rule now files in Transport (private)
        self.assertEqual(self.overview()["pending"], 3)
        self.save()
        # G13: a save relinks the rows it decides differently at once, so both coffee
        # shop rows are filed now and only the three from before are still to apply
        self.assertEqual(self.overview()["pending"], 3)
        got = {r["txn_key"]: (r["payee"], r["category"]) for r in rules_app.db_rows(self.conn)}
        self.assertEqual((got["k2"], got["k3"]), (("Kopitiam", "Eating out"), ("Kopitiam", "Eating out")))

    def test_rules_that_will_not_load_are_reported_not_raised(self):
        self.put("people.csv", "memo,X*,,,,Not a category,,,\n")
        o = self.overview()
        self.assertIn("unknown category", o["rules_error"])
        self.assertEqual(o["rules"], [])


class PreviewTest(Editor):
    def test_a_rule_an_earlier_tier_shadows_wins_no_row(self):
        out = rules_editor.preview(self.conn, {"rule": {
            "match_on": "memo", "pattern": "SALARY PAYMENT", "payee": "", "category": "Freelance income"}})
        self.assertEqual((out["error"], out["matches"], out["wins"], out["changes"]), (None, 2, 0, 0))
        self.assertEqual([(b["tier"], b["pattern"], b["rows"]) for b in out["beaten_by"]],
                         [("key", "s1", 1), ("key", "s2", 1)])

    def test_a_new_rule_lists_the_rows_it_would_change_newest_first(self):
        out = self.preview()
        self.assertEqual((out["file"], out["matches"], out["wins"], out["already"], out["changes"]),
                         ("merchants.csv", 2, 2, 0, 2))
        first = out["sample"][0]
        self.assertEqual((first["key"], first["date"], first["text"], first["amount"]),
                         ("k3", "2026-08-20", "KOPITIAM 0456 TPY", -420))
        self.assertEqual((first["before"], first["after"]),
                         ({"payee": "", "category": ""}, {"payee": "Kopitiam", "category": "Eating out"}))

    def test_a_rule_that_changes_nothing_counts_what_already_matches(self):
        self.save()
        out = self.preview(pattern="KOPITIAM 0456*")
        self.assertEqual((out["wins"], out["already"], out["changes"]), (0, 0, 0))
        self.assertEqual(out["beaten_by"][0]["pattern"], "KOPITIAM*")
        edit = {"file": "merchants.csv", "line": 2, "pattern": "KOPITIAM*"}
        out = self.preview(edit=edit)
        self.assertEqual((out["wins"], out["already"], out["changes"]), (2, 2, 0))

    def test_editing_a_pin_counts_the_row_it_moves(self):
        edit = {"file": "keys.csv", "line": 2, "pattern": "s1"}
        out = self.preview(edit=edit, match_on="key", pattern="s1", payee="Acme Co", kind="Employer",
                           category="Freelance income")
        self.assertEqual((out["wins"], out["already"], out["changes"]), (1, 0, 1))
        self.assertEqual(out["sample"][0]["after"]["category"], "Freelance income")

    def test_a_renamed_category_is_named_as_the_record_names_it(self):
        self.assertEqual(rules_editor._namer(self.conn)("Tuition"), "Education")
        self.conn.execute("INSERT INTO category(name, bucket, grp, need_class, sort_order) "
                          "VALUES ('Tuition', 'Spend', 'Variable', 'Want', 500)")
        self.assertEqual(rules_editor._namer(self.conn)("Tuition"), "Tuition")
        self.put("merchants.csv", "merchant,KOPITIAM*,Kopitiam,Merchant,SGP,Tuition,,,\n")
        out = self.preview(edit={"file": "merchants.csv", "line": 2, "pattern": "KOPITIAM*"}, category="Education")
        self.assertEqual((out["changes"], out["sample"][0]["before"]["category"]), (2, "Tuition"))

    def test_a_rule_that_would_not_load_says_why(self):
        self.assertIn("ISO alpha-3", self.preview(country="SG")["error"])
        self.assertIn("pattern", self.preview(pattern=" ")["error"])
        self.assertIn("kind", self.preview(kind="Robot")["error"])
        self.assertFalse((self.dir / "merchants.csv").exists())

    def test_the_preview_writes_nothing(self):
        before = {f.name: f.read_bytes() for f in self.dir.iterdir()}
        self.preview()
        self.assertEqual({f.name: f.read_bytes() for f in self.dir.iterdir()}, before)
        self.assertFalse(rules_history_dir().exists())


class SaveTest(Editor):
    def test_the_tier_decides_the_file(self):
        for tier, name in (("merchant", "merchants.csv"), ("counterparty", "people.csv"),
                           ("memo", "people.csv"), ("type", "types.csv"), ("flow", "types.csv"),
                           ("key", "keys.csv")):
            out = self.save(match_on=tier, pattern=f"{tier.upper()} X*", payee="", category="Shopping")
            self.assertEqual(out["file"], name, tier)
            self.assertIn(f"{tier.upper()} X*", [r["pattern"] for r in self.read(name)])
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()),
                         ["keys.csv", "merchants.csv", "people.csv", "types.csv"])
        self.assertEqual(oct(os.stat(self.dir / "types.csv").st_mode & 0o777), "0o600")

    def test_an_edit_in_place_keeps_every_other_record_byte_for_byte(self):
        body = ('key,s1,Acme Co,Employer,SGP,Salary,"two\r\nlines",,\r\n'
                "# a comment the owner keeps\r\n"
                "key,s2,Acme Co,Employer,SGP,Salary,July pay,,\r\n")
        (self.dir / "keys.csv").write_bytes((HEADER.rstrip("\n") + "\r\n" + body).encode())
        # the page's fingerprints read the CRLF lines as the writer does
        rules = self.overview()["rules"]
        for line in (2, 4):
            seen = self.find({"rules": rules}, id=f"keys.csv:{line}")
            edit = {"file": "keys.csv", "line": line, "revision": seen["revision"]}
            self.assertIsNone(self.preview(edit=edit, match_on="key", pattern=seen["pattern"], payee="Acme Co",
                                           kind="Employer", category="Salary")["error"], line)
        edit = {"file": "keys.csv", "line": 4, "pattern": "s2", "revision": self.find({"rules": rules}, id="keys.csv:4")["revision"]}
        out = self.save(edit=edit, match_on="key", pattern="s2",
                        payee="Acme Co", kind="Employer", category="Freelance income")
        self.assertEqual(out["line"], 4)
        got = (self.dir / "keys.csv").read_bytes().decode()
        self.assertEqual(got, HEADER.rstrip("\n") + "\r\n" + body.replace("SGP,Salary,July", "SGP,Freelance income,July"))

    def test_a_new_rule_lands_beside_its_payee(self):
        self.put("keys.csv", "key,s1,Acme Co,Employer,SGP,Salary,,,\nkey,k9,,,,Shopping,,,\n")
        out = self.save(match_on="key", pattern="s2", payee="Acme Co", kind="Employer", category="Salary")
        self.assertEqual(out["line"], 3)
        self.assertEqual([r["pattern"] for r in self.read("keys.csv")], ["s1", "s2", "k9"])

    def test_a_new_tier_moves_the_rule_to_its_file(self):
        self.save()
        out = self.save(edit={"file": "merchants.csv", "line": 2, "pattern": "KOPITIAM*"},
                        match_on="counterparty", notes="moved")
        self.assertEqual(out["file"], "people.csv")
        self.assertEqual(self.read("merchants.csv"), [])
        self.assertEqual([(r["match_on"], r["pattern"], r["notes"]) for r in self.read("people.csv")],
                         [("counterparty", "KOPITIAM*", "moved")])

    def test_an_edit_keeps_what_the_page_does_not_show(self):
        self.put("people.csv", "counterparty,J TAN*,J Tan,Person,SGP,Person to person,a friend,123-4,\n")
        self.save(edit={"file": "people.csv", "line": 2, "pattern": "J TAN*"}, match_on="counterparty",
                  pattern="J TAN*", payee="J Tan", kind="Person", category="Family support")
        got = self.read("people.csv")[0]
        self.assertEqual((got["category"], got["notes"], got["account_no"]), ("Family support", "a friend", "123-4"))

    def test_a_stale_edit_is_refused(self):
        with self.assertRaisesRegex(rules_app.RuleRefused, "changed since the page loaded"):
            self.save(edit={"file": "keys.csv", "line": 2, "pattern": "s2"}, match_on="key", pattern="s2")
        with self.assertRaises(rules_app.RuleRefused):
            self.save(edit={"file": "keys.csv", "line": 99, "pattern": "s2"})

    def test_a_page_that_read_an_older_rule_cannot_overwrite_a_newer_one(self):
        seen = self.find(self.overview(), id="keys.csv:2")
        edit = {"file": "keys.csv", "line": 2, "pattern": "s1", "revision": seen["revision"]}
        pin = {"match_on": "key", "pattern": "s1", "payee": "Acme Co", "kind": "Employer"}
        self.save(edit=edit, **pin, category="Freelance income")          # one tab
        with self.assertRaisesRegex(rules_app.RuleRefused, "changed since the page loaded"):
            self.save(edit=edit, **pin, category="Shopping")              # another, same pattern
        self.assertEqual(self.read("keys.csv")[0]["category"], "Freelance income")
        with self.assertRaisesRegex(rules_app.RuleRefused, "changed since the page loaded"):
            rules_app.remove("keys.csv", 2, "s1", seen["revision"])
        rules_app.remove("keys.csv", 2, "s1", self.find(self.overview(), id="keys.csv:2")["revision"])
        self.assertEqual([r["pattern"] for r in self.read("keys.csv")], ["s2"])

    def test_a_rule_that_breaks_the_set_writes_nothing(self):
        before = (self.dir / "keys.csv").read_bytes()
        with self.assertRaises(rules_app.RuleRefused):
            self.save(edit={"file": "keys.csv", "line": 2, "pattern": "s1"}, match_on="key", pattern="s1",
                      category="Not a category")
        self.assertEqual((self.dir / "keys.csv").read_bytes(), before)
        self.assertEqual(rules_app.history(), [])

    def test_a_failed_write_leaves_the_file_whole(self):
        before = (self.dir / "keys.csv").read_bytes()
        with mock.patch("os.replace", side_effect=OSError("disk full")), \
                self.assertRaisesRegex(rules_app.RuleRefused, "Could not write"):
            self.save(match_on="key", pattern="k3", payee="", category="Shopping")
        self.assertEqual((self.dir / "keys.csv").read_bytes(), before)
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ["keys.csv"])
        self.assertEqual(rules_app.history(), [])

    def test_saving_an_unchanged_rule_writes_nothing(self):
        out = self.save(edit={"file": "keys.csv", "line": 2, "pattern": "s1"}, match_on="key", pattern="s1",
                        payee="Acme Co", kind="Employer", category="Salary")
        self.assertTrue(out["unchanged"])
        self.assertEqual(rules_app.history(), [])


class OverrideTest(Editor):
    def shared_grab(self):
        return self.find(self.overview(), file="30-merchants.csv", pattern="GRAB*")

    def test_editing_a_shared_rule_adds_a_personal_rule_that_wins(self):
        grab = self.shared_grab()
        self.assertTrue(grab["shared"])
        shared_before = (REPO_RULES / "30-merchants.csv").read_bytes()
        edit = {"file": "30-merchants.csv", "line": grab["line"], "pattern": "GRAB*", "revision": grab["revision"]}
        with self.assertRaisesRegex(rules_app.RuleRefused, "changed since the page loaded"):
            self.save(edit={**edit, "revision": "0" * 16}, pattern="GRAB*", payee="Grab", category="Shopping")
        seen = self.preview(edit=edit, pattern="GRAB*", payee="Grab", category="Shopping")
        self.assertEqual((seen["override"], seen["file"], seen["wins"], seen["changes"]),
                         ("30-merchants.csv", "merchants.csv", 1, 1))
        out = self.save(edit=edit, pattern="GRAB*", payee="Grab", category="Shopping")
        self.assertEqual((out["override"], out["file"]), ("30-merchants.csv", "merchants.csv"))
        self.assertEqual((REPO_RULES / "30-merchants.csv").read_bytes(), shared_before)
        mine = self.read("merchants.csv")[0]
        self.assertEqual((mine["pattern"], mine["category"], mine["notes"]),
                         ("GRAB*", "Shopping", "overrides 30-merchants.csv GRAB*"))
        rows = categorise.categorise_rows(rules_app.db_rows(self.conn), rules_app._load_all(self.conn))
        self.assertEqual(next(r for r in rows if r["txn_key"] == "k1")["category"], "Shopping")


class UndoTest(Editor):
    def snapshot(self):
        return {p.name: p.read_bytes() for p in self.dir.iterdir()}

    def test_every_write_can_be_undone_newest_first(self):
        start = self.snapshot()
        self.save()                                                          # a new file
        self.save(edit={"file": "keys.csv", "line": 3, "pattern": "s2"}, match_on="key", pattern="s2",
                  payee="Acme Co", kind="Employer", category="Freelance income")   # an edit
        rules_app.remove("keys.csv", 2, "s1")                                # a delete
        rules_app.pin(self.conn, {"key": "k3", "category": "Shopping"})       # a pin
        self.assertEqual([h["action"] for h in rules_app.history()], ["pin", "delete", "edit", "add"])
        self.assertIn("Deleted key s1 (Acme Co) from keys.csv", rules_app.history()[1]["summary"])
        for _ in range(4):
            rules_app.undo()
        self.assertEqual(self.snapshot(), start)
        self.assertFalse((self.dir / "merchants.csv").exists())
        with self.assertRaisesRegex(rules_app.RuleRefused, "Nothing to undo"):
            rules_app.undo()

    def test_batches_and_app_rules_are_undone_too(self):
        start = self.snapshot()
        rules_app.add_many(self.conn, [self.rule()])
        rules_app.add_many(self.conn, [self.rule(pattern="TOAST*", payee="Toast"),
                                       self.rule(match_on="counterparty", pattern="J TAN*", payee="J Tan",
                                                 kind="Person", category="Person to person")])
        self.assertEqual(rules_app.history()[0]["summary"], "Added 2 rules")
        rules_app.undo()
        self.assertEqual([r["pattern"] for r in self.read("merchants.csv")], ["KOPITIAM*"])
        self.assertFalse((self.dir / "people.csv").exists())
        rules_app.undo()
        self.assertEqual(self.snapshot(), start)

    def test_a_file_changed_by_hand_since_is_left_alone(self):
        self.save()
        (self.dir / "merchants.csv").write_text(HEADER + "merchant,MINE*,,,,Shopping,,,\n", encoding="utf-8")
        with self.assertRaisesRegex(rules_app.RuleRefused, "merchants.csv has changed since"):
            rules_app.undo()
        self.assertIn("MINE*", (self.dir / "merchants.csv").read_text())

    def test_the_history_keeps_the_newest_writes_in_the_data_folder(self):
        self.assertGreaterEqual(rules_app.HISTORY_KEEP, 20)
        self.assertTrue(rules_history_dir().is_relative_to(self.root))
        with mock.patch.object(rules_app, "HISTORY_KEEP", 3):
            for n in range(5):
                rules_app.add_many(self.conn, [self.rule(pattern=f"SHOP {n}*", payee=f"Shop {n}")])
        kept = sorted(p.name for p in rules_history_dir().iterdir())
        self.assertEqual(kept, ["000003.json", "000004.json", "000005.json"])
        self.assertEqual(oct(os.stat(rules_history_dir() / kept[0]).st_mode & 0o777), "0o600")
        self.assertEqual(json.loads((rules_history_dir() / kept[-1]).read_text())["action"], "add")
        self.assertEqual([h["id"] for h in rules_app.history()], [5, 4, 3])

    def test_an_undo_that_cannot_write_every_file_writes_none(self):
        self.save()
        self.save(edit={"file": "merchants.csv", "line": 2, "pattern": "KOPITIAM*"}, match_on="counterparty")
        moved = self.snapshot()    # the rule now in people.csv; undo touches both files
        real = rules_app._write_atomic

        def write(path, data):
            if path.name == "people.csv":
                raise PermissionError("read-only")
            real(path, data)
        with mock.patch.object(rules_app, "_write_atomic", write), \
                self.assertRaisesRegex(rules_app.RuleRefused, "Could not undo"):
            rules_app.undo()
        self.assertEqual(self.snapshot(), moved)
        rules_app.undo()                                 # and it can still be undone after
        self.assertEqual([r["pattern"] for r in self.read("merchants.csv")], ["KOPITIAM*"])
        self.assertFalse((self.dir / "people.csv").exists())

    def test_an_old_entry_that_will_not_go_away_keeps_the_write(self):
        from pathlib import Path
        real = Path.unlink

        def unlink(path, missing_ok=False):
            if path.parent == rules_history_dir() and path.suffix == ".json":
                raise PermissionError("busy")
            return real(path, missing_ok=missing_ok)
        with mock.patch.object(rules_app, "HISTORY_KEEP", 1):
            self.save()
            with mock.patch.object(Path, "unlink", unlink):
                self.save(pattern="TOAST*", payee="Toast")
        self.assertIn("TOAST*", [r["pattern"] for r in self.read("merchants.csv")])
        self.assertEqual(rules_app.undo()["undone"], "Added merchant TOAST* (Toast) to merchants.csv")
        self.assertEqual([r["pattern"] for r in self.read("merchants.csv")], ["KOPITIAM*"])

    def test_a_stale_delete_is_refused(self):
        with self.assertRaisesRegex(rules_app.RuleRefused, "changed since the page loaded"):
            rules_app.remove("keys.csv", 2, "s2")
        self.assertEqual(len(self.read("keys.csv")), 2)


class RouteTest(Editor):
    """The routes the Rules page calls, through serve."""

    def test_read_preview_save_and_undo(self):
        import serve
        from finance import db
        with mock.patch("finance.api.open_record", lambda: self.conn):
            page = serve.answer("/api/rules", {})
            self.assertIn("groups", page)
            self.assertIn("needs", page)
            seen = serve.answer("/api/rules/preview", {k: str(v) for k, v in self.rule().items()})
            self.assertEqual(seen["wins"], 2)
            seen = serve.answer("/api/rules/preview", {**self.rule(match_on="key", pattern="s1", payee="Acme Co",
                                                                    kind="Employer", category="Salary"),
                                                       "edit_file": "keys.csv", "edit_line": "2", "edit_pattern": "s1"})
            self.assertEqual((seen["wins"], seen["already"]), (1, 1))
        with mock.patch("finance.api.config", lambda: None), mock.patch.object(db, "connect", lambda: self.conn):
            body = json.dumps({"rule": self.rule(), "edit": None}).encode()
            self.assertEqual(serve.write("/api/rules/save", {}, body)["file"], "merchants.csv")
            self.assertEqual(serve.write("/api/rules/undo", {}, b"{}")["undone"],
                             "Added merchant KOPITIAM* (Kopitiam) to merchants.csv")
            with self.assertRaises(serve.Refused):
                serve.write("/api/rules/undo", {}, b"{}")
            with self.assertRaises(serve.Refused):
                serve.write("/api/rules/remove", {}, b'{"file": "keys.csv", "line": 2, "pattern": "nope"}')
            with self.assertRaises(ValueError):
                serve.write("/api/rules/save", {}, b"[]")
