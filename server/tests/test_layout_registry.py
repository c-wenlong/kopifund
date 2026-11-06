"""The layout registry (G21, server/finance/parsers/layouts.py): every layout is
declared once, in its parser module, and everything that lists layouts reads
it. A new layout is one module: server/tests/fake_layout.py, registered here, reaches
Setup, the Upload page's sniffing, the upload's name, the run and the parsing
doc's table with no other edit."""
import isolate  # noqa: F401  first: no test writes the real record or cache
import ast
import importlib
import os
import subprocess
import sys
import tomllib
import unittest
from pathlib import Path
from unittest import mock

import fake_layout
from finance import api, model, monthly, setup, sniff, sync
from finance.parsers import layouts
from support import DataFolder

ROOT = Path(__file__).resolve().parent.parent
TESTS = Path(__file__).resolve().parent

# docs/gen_parsing.py lives outside server/; load it by path, as the suite
# does for finance.py
_spec = importlib.util.spec_from_file_location(
    "gen_parsing", Path(__file__).resolve().parents[2] / "docs" / "gen_parsing.py")
gen_parsing = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen_parsing)

STATEMENT = ("%PDF-1.4\nFAKEBANK STATEMENT OF ACCOUNT 2026-03\nAccount FB-0042\n"
             "OPENING 100.00\n2026-03-05 COFFEE -4.50\nCLOSING 95.50\n")


class TheRegistry(unittest.TestCase):
    def test_every_parser_module_declares_its_layouts(self):
        modules = sorted(f"finance.parsers.{p.stem}" for p in (ROOT / "finance" / "parsers").glob("parse_*.py"))
        self.assertEqual(sorted({lay.module for lay in layouts.every()}), modules)

    def test_a_fresh_interpreter_finds_every_layout_by_itself(self):
        # in this process the tests have imported the parsers already; a new
        # one has imported none of them when it first asks
        code = ("import sys\nfrom finance.parsers import layouts\n"
                "assert not [m for m in sys.modules if m.startswith('finance.parsers.parse_')]\n"
                "print(' '.join(f'{lay.name}:{lay.module}' for lay in layouts.every()))\n")
        out = subprocess.run([sys.executable, "-P", "-c", code], cwd=ROOT, capture_output=True, text=True,
                             env={**os.environ, "PYTHONPATH": str(ROOT)})
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.split(), [f"{lay.name}:{lay.module}" for lay in layouts.every()
                                              if lay.name != "fakebank"])
        self.assertEqual([pair.split(":")[0] for pair in out.stdout.split()],
                         ["consolidated", "posb", "paylah", "card", "ocbc", "uob-one", "uob-savings", "youtrip",
                          "revolut", "wise", "tiger", "cpf"])

    def test_the_dbs_module_reads_four_layouts(self):
        self.assertEqual([lay.name for lay in layouts.of_parser("dbs")], ["consolidated", "posb", "paylah", "card"])
        self.assertEqual({lay.module for lay in layouts.of_parser("dbs")}, {"finance.parsers.parse_statements"})

    def test_every_layout_is_whole(self):
        names = [lay.name for lay in layouts.every()]
        self.assertEqual(len(names), len(set(names)))
        for lay in layouts.every():
            with self.subTest(lay.name):
                self.assertIn(lay.kind, layouts.KINDS)
                self.assertTrue(lay.label.startswith(("a ", "an ")))
                self.assertEqual(lay.per_currency, bool(lay.currencies))
                # a layout with no rows is a position, never a bank, wallet or card
                self.assertEqual(lay.csv is None, lay.kind in ("brokerage", "CPF"))
                if lay.csv is not None:
                    self.assertTrue(lay.title and lay.detected_by and lay.balance_check)
                self.assertIsNone(lay.test("An invoice\nnothing to see here\n"))

    def test_a_second_module_cannot_take_a_layout_name(self):
        other = fake_layout.LAYOUT.__class__(**{**fake_layout.LAYOUT.__dict__, "name": "wise"})
        with self.assertRaises(ValueError):
            layouts.register(other)

    def test_no_consumer_names_a_layout_or_a_parser(self):
        """The modules that list layouts read the registry: none of them holds
        a layout's or a parser's name as a literal, so no per-layout branch is
        left outside the parser modules."""
        names = {lay.name for lay in layouts.every()} | {lay.parser for lay in layouts.every()}
        for rel in ("finance/sniff.py", "finance/sync.py", "finance/monthly.py", "finance/setup.py",
                    "finance/model.py"):
            tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
            found = sorted({n.value for n in ast.walk(tree)
                            if isinstance(n, ast.Constant) and n.value in names})
            self.assertEqual(found, [], f"{rel} names {found}")


class ANewLayoutIsOneModule(DataFolder):
    def setUp(self):
        super().setUp()
        layouts.register(fake_layout.LAYOUT)
        self.addCleanup(fake_layout.unregister, "fakebank")
        # the run starts the parser as `python3 -m fake_layout`, found on this path
        p = mock.patch.dict(os.environ, {"PYTHONPATH": str(TESTS)})
        p.start()
        self.addCleanup(p.stop)

    def test_setup_offers_it(self):
        offered = setup.providers()
        ids = [p["id"] for p in offered]
        self.assertIn({"id": "fakebank-savings", "label": "Fakebank savings account", "parser": "fakebank",
                       "status": layouts.SAMPLE,
                       "bank": "other", "title": ""}, offered)
        # its order puts it after OCBC and before the wallets
        self.assertEqual(ids.index("fakebank-savings"), ids.index("ocbc-360") + 1)
        self.assertIn("fakebank-savings", [p["id"] for p in api.get_setup(None)["providers"]])

    def test_sniff_reads_it(self):
        [found] = sniff.sniff(STATEMENT)
        self.assertEqual((found.parser, found.layout, found.numbers), ("fakebank", "fakebank", frozenset({"FB0042"})))
        self.assertEqual(found.label(), "a Fakebank statement")
        self.assertEqual(sniff.sniff("An invoice\n"), [])

    def test_the_doc_table_lists_it(self):
        self.assertIn("| Fakebank account | `statements/fakebank-savings/` | `FAKEBANK STATEMENT OF ACCOUNT` "
                      "on the first line | opening + in - out = printed closing | from a public sample |",
                      gen_parsing.render())

    def test_its_new_types_are_offered_to_notion(self):
        self.assertIn("Fakebank Other", model.reachable_options()[("transactions", "Bank txn type")])

    def test_set_up_uploaded_and_run(self):
        # Setup makes its folder, the Upload page files a statement there by
        # its own text and names it, and the run parses it
        setup.create(["fakebank-savings"])
        written = tomllib.loads((self.root / "config.toml").read_text())
        self.assertEqual(written["folder"], [{"path": "statements/fakebank-savings", "parser": "fakebank",
                                              "account_no": ""}])
        cfg = model.load_config()
        data = STATEMENT.encode()
        with mock.patch.object(monthly, "_text", return_value=STATEMENT):
            self.assertEqual(monthly.find_folder(cfg, data),
                             {"folder": "statements/fakebank-savings", "kind": "a Fakebank statement"})
            saved = monthly.upload(cfg, "statements/fakebank-savings", data)
        self.assertEqual((saved["status"], saved["path"]), ("added", "statements/fakebank-savings/2026/2026-03.pdf"))
        parsed = sync.parse(cfg, echo=False)
        self.assertEqual(parsed.problems, [])
        self.assertEqual([(r["merchant"], r["amount"], r["period"]) for r in parsed.rows],
                         [("COFFEE", "-4.5", "FB-0042 · SGD · 2026-03")])
        self.assertTrue((parsed.csv_dir / "fakebank" / "transactions.csv").is_file())


class WhatTheModulesDeclare(unittest.TestCase):
    """The registry holds what the consumers' own tables held before G21."""

    def test_each_layout_says_whether_a_real_statement_has_checked_it(self):
        # the owner's own statements have parsed and balanced through these; OCBC and
        # UOB come from public samples only
        self.assertEqual({lay.name: lay.status for lay in layouts.every()}, {
            **{n: layouts.CHECKED for n in ("consolidated", "posb", "paylah", "card", "youtrip", "revolut", "wise",
                                            "tiger", "cpf")},
            **{n: layouts.SAMPLE for n in ("ocbc", "uob-one", "uob-savings")}})
        self.assertEqual(layouts.STATUSES, ("checked on a real statement", "from a public sample"))

    def test_a_new_layout_starts_from_a_public_sample_and_no_third_status_is_taken(self):
        self.assertEqual(fake_layout.LAYOUT.status, layouts.SAMPLE)
        odd = fake_layout.LAYOUT.__class__(**{**fake_layout.LAYOUT.__dict__, "name": "oddbank", "status": "unverified"})
        with self.assertRaises(ValueError):
            layouts.register(odd)
        self.assertIsNone(layouts.get("oddbank"))

    def test_setup_and_the_parsing_doc_use_the_registrys_words(self):
        for p in setup.providers():
            self.assertIn(p["status"], layouts.STATUSES)
            self.assertNotIn("unverified", p["label"])
        table = gen_parsing.render()
        for lay in layouts.every():
            if lay.csv is not None:
                self.assertIn(f"| {lay.balance_check} | {lay.status} |", table)

    def test_the_banks_a_statement_no_layout_knows_is_told_about(self):
        banks, soon = layouts.supported()
        self.assertEqual([(b["bank"], b["name"]) for b in banks], [
            ("dbs", "DBS"), ("posb", "POSB"), ("paylah", "DBS PayLah!"), ("ocbc", "OCBC"), ("uob", "UOB"),
            ("youtrip", "YouTrip"), ("revolut", "Revolut"), ("wise", "Wise"), ("tiger", "Tiger Brokers"),
            ("cpf", "CPF")])
        self.assertEqual({b["bank"] for b in banks if b["status"] == layouts.SAMPLE}, {"ocbc", "uob"})
        self.assertEqual(soon, [{"bank": "trust", "name": "Trust Bank"}])
        # every bank the registry names has a name of its own
        self.assertTrue(all(b.name for b in layouts.banks()))

    def test_setup_lists_the_providers_in_their_order(self):
        self.assertEqual([(p["id"], p.get("wallet", "")) for p in setup.providers()], [
            ("dbs-multiplier", ""), ("posb-everyday", ""), ("dbs-paylah", ""), ("dbs-debit-cards", ""),
            ("ocbc-360", ""), ("uob-one", ""), ("uob-savings", ""), ("youtrip-sgd", "SGD"), ("youtrip-usd", "USD"), ("youtrip-eur", "EUR"),
            ("youtrip-myr", "MYR"), ("revolut", ""), ("wise-sgd", "SGD"), ("wise-usd", "USD"),
            ("tiger", ""), ("cpf", "")])

    def test_the_run_starts_each_parser_once(self):
        self.assertEqual(layouts.runs(), [
            ("dbs", "finance.parsers.parse_statements", ""), ("ocbc", "finance.parsers.parse_ocbc", "ocbc"),
            ("uob", "finance.parsers.parse_uob", "uob"),
            ("youtrip", "finance.parsers.parse_youtrip", "youtrip"),
            ("revolut", "finance.parsers.parse_revolut", "revolut"),
            ("wise", "finance.parsers.parse_wise", "wise")])
        for _, module, _ in layouts.runs():
            self.assertTrue(callable(importlib.import_module(module).main))

    def test_wallet_folders_and_config_numbers(self):
        self.assertEqual([lay.name for lay in layouts.every() if lay.per_currency], ["youtrip", "wise"])
        self.assertFalse(layouts.get("card").prints_config_number())
        self.assertTrue(layouts.get("wise").prints_config_number("SGD"))
        self.assertFalse(layouts.get("wise").prints_config_number("USD"))
        self.assertTrue(layouts.get("youtrip").prints_config_number("USD"))


if __name__ == "__main__":
    unittest.main()
