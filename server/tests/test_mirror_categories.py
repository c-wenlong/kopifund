#!/usr/bin/env python3
"""G13: the Notion mirror creates the Categories rows the record holds.

The app owns the categories, so one added there (Transport (public) after the
split, or one the owner made up) reaches Notion through the sync rather than by
hand. A category the record does not hold is still missing-category: a typo in
a rule, not a category. Everything runs against FakeNotion and a throwaway
record; nothing here reaches the Notion API or the data folder.
"""
import isolate  # noqa: F401  first: no test writes the real record or cache
import argparse
import contextlib
import importlib.util
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fake_notion import CFG, FakeNotion, block, row

from finance import snapshot as snap
from finance import db, notion, sync
from finance.model import CATEGORY_TYPES, TXN_TYPES, page_properties

REPO = Path(__file__).resolve().parent.parent


def _load_cli():
    """finance.py, loaded by path: the `finance` package next to it wins the
    import by name."""
    spec = importlib.util.spec_from_file_location("finance_cli_categories", REPO / "finance.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CLI = _load_cli()

# a category the owner added in the app, and one the shipped list added
OWN = "Hawker"
SHIPPED = "Transport (public)"


def rows(own=OWN):
    return [row(category=own),
            row(txn_key="bbbb2222", date="2026-01-07", amount="-4.50", merchant="BUS/MRT",
                card_last4="", purchase_date="", balance_after="984.20", category=SHIPPED)]


def blocks():
    return [block(n_txns="2", total_out="16.80", closing="984.20")]


class Fixture(unittest.TestCase):
    """A record holding OWN (and, seeded, every shipped category), a Notion
    whose Categories has neither OWN nor SHIPPED, and finance.py with its
    parse, its client and the snapshot pointed at those."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        env = mock.patch.dict(os.environ, {"KOPIFUND_DB": str(self.dir / "kopifund.db"),
                                           "KOPIFUND_DATA": str(self.dir)})
        env.start()
        self.addCleanup(env.stop)
        conn = db.connect()
        try:
            db.seed_categories(conn)
            conn.execute("INSERT INTO category(name, bucket, grp, need_class, sort_order) "
                         "VALUES (?, 'Spend', 'Variable', 'Want', 155)", (OWN,))
            conn.commit()
        finally:
            conn.close()
        self.client = FakeNotion()
        self.client.create(CFG.ds("categories"),
                           notion.to_api({"Name": "Groceries"}, {"Name": "title"}))
        self.client.writes = 0
        self.client.calls.clear()
        self.rows = rows()
        self.rules_path = None
        for name, value in (("check_environment", lambda cfg: []),
                            ("snapshot_step", lambda *a, **kw: None)):
            p = mock.patch.object(sync, name, value)
            p.start()
            self.addCleanup(p.stop)
        for p in (mock.patch.object(snap, "read_portfolio", return_value=None),
                  mock.patch.object(snap, "read_cpf", return_value=None),
                  mock.patch.object(CLI, "client_for", lambda cfg: self.client),
                  mock.patch.object(CLI, "prepare", lambda cfg, args, echo=True: (
                      sync.Parsed(blocks=blocks(), rows=[dict(r) for r in self.rows],
                                  csv_dir=Path(".")), self.rules_path))):
            p.start()
            self.addCleanup(p.stop)

    def run_cli(self, command="cmd_sync", **over):
        args = argparse.Namespace(**{"dry_run": False, "force": False,
                                     "archive_orphans": False, "report": None,
                                     "rules": None, "csv": None, "prune": False, **over})
        said = io.StringIO()
        with contextlib.redirect_stdout(said):
            code = getattr(CLI, command)(CFG, args)
        return code, said.getvalue()

    def categories(self):
        return {notion.plain(p, "Name"): p for p in self.client.query_all(CFG.ds("categories"))}

    def page_id(self, name):
        return self.categories()[name]["id"].replace("-", "")


class SyncCreatesCategoriesTest(Fixture):

    def test_a_category_the_record_holds_is_created_and_the_rows_link_to_it(self):
        code, said = self.run_cli()
        self.assertEqual(code, CLI.CLEAN, said)
        self.assertIn("verify: clean", said)
        self.assertIn(f"created 2 categories in Notion: {OWN}, {SHIPPED}", said)
        cats = self.categories()
        self.assertEqual(page_properties(cats[OWN], CATEGORY_TYPES),
                         {"Name": OWN, "Bucket": "Spend", "Group": "Variable",
                          "Need class": "Want", "Sort order": 155.0})
        self.assertEqual(notion.plain(cats[SHIPPED], "Need class"), "Need")
        by_key = {notion.plain(p, "Txn key"): p
                  for p in self.client.query_all(CFG.ds("transactions"))}
        self.assertEqual(page_properties(by_key["aaaa1111"], TXN_TYPES)["Category"],
                         (self.page_id(OWN),))
        self.assertEqual(page_properties(by_key["bbbb2222"], TXN_TYPES)["Category"],
                         (self.page_id(SHIPPED),))

    def test_the_categories_come_before_anything_that_links_to_them(self):
        self.run_cli()
        creates = [ds for verb, ds in self.client.calls if verb == "create"]
        self.assertEqual(creates[:2], [CFG.ds("categories")] * 2)
        self.assertNotIn(CFG.ds("categories"), creates[2:])

    def test_a_shipped_category_new_to_the_record_carries_the_order_the_load_gave_it(self):
        # the load puts a shipped category the record lacks last in its
        # bucket, so the shipped sort order is not what the record ends with
        conn = db.connect()
        try:
            conn.execute("DELETE FROM category WHERE name = ?", (SHIPPED,))
            conn.commit()
        finally:
            conn.close()
        self.assertEqual(self.run_cli()[0], CLI.CLEAN)
        conn = db.connect()
        try:
            order = conn.execute("SELECT sort_order FROM category WHERE name = ?",
                                 (SHIPPED,)).fetchone()[0]
        finally:
            conn.close()
        shipped = next(c["sort_order"] for c in db.shipped_categories() if c["name"] == SHIPPED)
        self.assertNotEqual(order, shipped)
        self.assertEqual(notion.plain(self.categories()[SHIPPED], "Sort order"), order)

    def test_the_budgets_link_to_the_new_rows_too(self):
        self.run_cli()
        budgets = {notion.plain(p, "Name"): notion.plain(p, "Category")
                   for p in self.client.query_all(CFG.ds("budgets"))}
        self.assertEqual([i.replace("-", "") for i in budgets[f"{OWN} · 2026-01"]],
                         [self.page_id(OWN)])

    def test_a_second_run_creates_nothing(self):
        self.assertEqual(self.run_cli()[0], CLI.CLEAN)
        self.client.writes = 0
        self.client.calls.clear()
        code, said = self.run_cli()
        self.assertEqual(code, CLI.CLEAN, said)
        self.assertEqual(self.client.writes, 0)
        self.assertNotIn("categories in Notion", said)
        self.assertEqual(sorted(self.categories()), sorted(["Groceries", OWN, SHIPPED]))

    def test_a_dry_run_says_what_it_would_create_and_writes_nothing(self):
        code, said = self.run_cli(dry_run=True)
        self.assertEqual(code, CLI.CLEAN, said)
        self.assertIn(f"would create 2 categories in Notion: {OWN}, {SHIPPED}", said)
        self.assertIn("(2 categories, 3 upsert,", said)
        self.assertEqual(self.client.writes, 0)
        self.assertEqual(self.client.calls, [])
        self.assertEqual(list(self.categories()), ["Groceries"])

    def test_a_category_the_record_does_not_hold_still_stops_the_run(self):
        self.rows = rows(own="Hawkr")
        code, said = self.run_cli()
        self.assertEqual(code, CLI.DOCTOR_FAILED)
        self.assertIn("missing-category", said)
        self.assertIn("Hawkr", said)
        self.assertIn("create the Categories row in Notion (and check the spelling "
                      "in the rules)", said)
        self.assertNotIn(f"  missing-category       {SHIPPED}", said)
        self.assertEqual(self.client.writes, 0)
        self.assertEqual(list(self.categories()), ["Groceries"])

    def test_doctor_counts_a_category_it_can_create_as_no_problem(self):
        code, said = self.run_cli("cmd_doctor")
        self.assertEqual(code, CLI.CLEAN, said)
        self.assertIn(f"would create 2 categories in Notion: {OWN}, {SHIPPED}", said)
        self.assertEqual(self.client.writes, 0)


class OfflineDoctorTest(Fixture):
    """G13: with Notion off (no data sources configured) doctor still runs every
    check that needs no Notion, and never asks for a Notion client."""

    def offline(self):
        import dataclasses
        return dataclasses.replace(CFG, data_sources={})

    def run_offline(self):
        args = argparse.Namespace(dry_run=False, force=False, archive_orphans=False, report=None,
                                  rules=None, csv=None, prune=False)
        said = io.StringIO()
        with mock.patch.object(CLI, "client_for", side_effect=AssertionError("no Notion client offline")), \
                contextlib.redirect_stdout(said):
            code = CLI.cmd_doctor(self.offline(), args)
        return code, said.getvalue()

    def test_a_clean_record_is_clean_without_notion(self):
        code, said = self.run_offline()
        self.assertEqual(code, CLI.CLEAN, said)
        self.assertIn("doctor: clean. 2 rows in 1 blocks; Notion is off", said)

    def test_a_local_problem_still_stops_it(self):
        self.rows = rows() + [dict(rows()[0])]  # the same txn_key twice
        code, said = self.run_offline()
        self.assertEqual(code, CLI.DOCTOR_FAILED, said)
        self.assertIn("duplicate-key", said)


class PayeesCreateCategoriesTest(Fixture):
    """`finance.py payees` links each payee to its default category, so a new
    payee in a new category would otherwise stop at missing-category while the
    sync stops at missing-payee, and neither could go first."""

    def setUp(self):
        super().setUp()
        self.rules_path = self.dir / "merchants.csv"
        self.rules_path.write_text("match_on,pattern,payee,kind,country,category,notes\n"
                                   f"merchant,BUS/MRT*,Bus/MRT,Merchant,SGP,{SHIPPED},\n",
                                   encoding="utf-8")
        self.rows = [{**r, "payee": "Bus/MRT"} for r in rows() if r["category"] == SHIPPED]

    def test_payees_creates_the_default_category_first(self):
        code, said = self.run_cli("cmd_payees")
        self.assertEqual(code, CLI.CLEAN, said)
        payee = self.client.query_all(CFG.ds("payees"))[0]
        self.assertEqual([i.replace("-", "") for i in notion.plain(payee, "Default category")],
                         [self.page_id(SHIPPED)])

    def test_a_dry_run_of_payees_creates_nothing(self):
        code, said = self.run_cli("cmd_payees", dry_run=True)
        self.assertEqual(code, CLI.CLEAN, said)
        self.assertIn(f"would create 1 category in Notion: {SHIPPED}", said)
        self.assertEqual(self.client.writes, 0)

    def own_rule(self, category):
        self.rules_path.write_text("match_on,pattern,payee,kind,country,category,notes\n"
                                   f"merchant,KOPI*,Kopi stall,Merchant,SGP,{category},\n",
                                   encoding="utf-8")
        self.rows = []

    def test_payees_loads_a_rule_naming_a_category_only_the_record_holds(self):
        self.own_rule(OWN)
        code, said = self.run_cli("cmd_payees")
        self.assertEqual(code, CLI.CLEAN, said)
        payee = self.client.query_all(CFG.ds("payees"))[0]
        self.assertEqual(notion.plain(payee, "Name"), "Kopi stall")
        self.assertEqual([i.replace("-", "") for i in notion.plain(payee, "Default category")],
                         [self.page_id(OWN)])

    def test_payees_still_rejects_a_misspelled_category(self):
        self.own_rule("Hawkr")
        with self.assertRaises(SystemExit) as stop:
            self.run_cli("cmd_payees")
        self.assertIn("unknown category 'Hawkr'", str(stop.exception))
        self.assertEqual(self.client.writes, 0)


class CategoryFieldsTest(unittest.TestCase):

    def schema(self, client):
        return client.schema(CFG.ds("categories"))["properties"]

    def test_a_property_or_option_notion_lacks_is_left_out_not_invented(self):
        client = FakeNotion(schemas={CFG.ds("categories"): {
            "Name": "title", "Group": "select", "Need class": "select"}},
            options={(CFG.ds("categories"), "Group"): ["Fixed"]})
        props, left_out = sync.category_properties(
            OWN, {"bucket": "Spend", "grp": "Variable", "need_class": "Want",
                  "sort_order": 155}, self.schema(client))
        self.assertEqual({k: v for k, v in props.items() if v is not None},
                         {"Name": OWN, "Need class": "Want"})
        self.assertEqual(len(left_out), 3)
        self.assertTrue(any("Bucket" in n for n in left_out))
        self.assertTrue(any("not an option" in n for n in left_out))

    def test_the_record_as_the_next_load_leaves_it(self):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.dict(os.environ, {"KOPIFUND_DB": str(Path(d) / "kopifund.db")}):
            conn = db.connect()
            try:
                conn.execute("INSERT INTO category(name, need_class) VALUES (?, 'Want')", (OWN,))
                conn.execute("INSERT INTO category(name) VALUES ('Snacks')")
                conn.execute("INSERT INTO category_alias(name, category_id) "
                             "SELECT 'Coffee', id FROM category WHERE name = 'Snacks'")
                conn.commit()
            finally:
                conn.close()
            held = sync.record_category_fields()
            with_transport = sync.record_category_fields({"Transport"})
        self.assertEqual(held[OWN]["need_class"], "Want")
        self.assertIn(SHIPPED, held)                     # the load seeds it
        self.assertNotIn("Coffee", held)                 # the user renamed it away
        self.assertNotIn("Transport", held)              # retired, and no row names it
        self.assertIn("Transport", with_transport)

    def test_no_record_means_the_shipped_categories(self):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.dict(os.environ, {"KOPIFUND_DB": str(Path(d) / "kopifund.db")}):
            held = sync.record_category_fields()
            self.assertFalse((Path(d) / "kopifund.db").exists())
        self.assertEqual(set(held), {c["name"] for c in db.shipped_categories()})

    def test_a_pending_category_refuses_a_write(self):
        state = sync.State()
        state.new_categories[OWN] = sync.NewCategory(OWN, {"Name": OWN})
        with self.assertRaises(RuntimeError):
            sync.upsert(CFG, FakeNotion(), sync.Parsed(), state, sync.Plan(), echo=False)


if __name__ == "__main__":
    unittest.main()
