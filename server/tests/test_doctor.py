#!/usr/bin/env python3
"""Every doctor check, each fired by a plan crafted to break it.

A check that never fires is worse than no check, so each one gets a case that
trips it and the clean fixture proves it stays quiet otherwise.
"""
import isolate  # noqa: F401  first: no test writes the real record or cache
import contextlib
import gc
import io
import tempfile
import unittest
import warnings
from pathlib import Path
from unittest import mock

from finance.parsers import parse_statements

from fake_notion import CFG, FakeNotion, block, row

from finance import sync
from finance.model import SELECT_CHECKS, Config, Folder, Problem

CLEAN_ROWS = [row()]
CLEAN_BLOCKS = [block()]


def parsed_of(rows=None, blocks=None, problems=()):
    return sync.Parsed(blocks=list(blocks if blocks is not None else CLEAN_BLOCKS),
                       rows=list(rows if rows is not None else CLEAN_ROWS),
                       problems=list(problems), csv_dir=Path("."))


def codes(problems):
    return [p.code for p in problems]


def state_with(**over):
    """A State that satisfies the clean fixture unless a test changes it."""
    st = sync.State()
    st.payees = {"Kopitiam": "p" * 32}
    st.categories = {"Eating out": "c" * 32}
    for ds_name, prop, _ in SELECT_CHECKS:
        st.options[(ds_name, prop)] = FakeNotion().select_options(CFG.ds(ds_name), prop)
    for k, v in over.items():
        setattr(st, k, v)
    return st


class EnvironmentTest(unittest.TestCase):

    def test_pdftotext_missing(self):
        with mock.patch("shutil.which", return_value=None):
            self.assertIn("pdftotext-missing", codes(sync.check_environment(CFG)))

    def test_configured_folder_missing(self):
        cfg = Config(root=Path("/nowhere"), version="v", token_env="", token_command="",
                     folders=[Folder(path="ghost-statements", parser="dbs")])
        with mock.patch("shutil.which", return_value="/usr/bin/pdftotext"):
            self.assertEqual(codes(sync.check_environment(cfg)), ["folder-missing"])

    def test_clean_environment_is_quiet(self):
        if not all((CFG.root / f.path).is_dir() for f in CFG.folders):
            self.skipTest("the fixture config's folders are not in this data folder")
        with mock.patch("shutil.which", return_value="/usr/bin/pdftotext"):
            self.assertEqual(sync.check_environment(CFG), [])


class StatementTest(unittest.TestCase):
    """A PDF that produced neither rows nor a block, and a wallet folder that
    is not in the config."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "statements" / "2026").mkdir(parents=True)
        (self.root / "statements" / "2026" / "1.pdf").write_bytes(b"%PDF-1.4")
        self.addCleanup(self.tmp.cleanup)

    def cfg(self, **folder):
        return Config(root=self.root, version="v", token_env="", token_command="",
                      accounts=dict(CFG.accounts), cards=dict(CFG.cards),
                      folders=[Folder(path="statements", parser="dbs", **folder)])

    def test_pdf_that_produced_nothing(self):
        problems = sync.check_statements(self.cfg(), parsed_of(rows=[], blocks=[]))
        self.assertEqual(codes(problems), ["pdf-not-parsed"])
        self.assertIn("2026/1.pdf", problems[0].subject)

    def test_pdf_with_only_an_empty_block_is_accepted(self):
        empty = block(source="statements/2026/1.pdf", n_txns="0", closing="",
                      note="no transactions; statement prints no balances")
        problems = sync.check_statements(self.cfg(), parsed_of(rows=[], blocks=[empty]))
        self.assertEqual(problems, [])

    def test_pdf_the_parser_reported_as_skipped_is_accepted(self):
        parsed = parsed_of(rows=[], blocks=[])
        parsed.skipped.add("statements/2026/1.pdf")
        self.assertEqual(sync.check_statements(self.cfg(), parsed), [])

    def test_skip_line_is_read_from_the_parser_output(self):
        parsed = sync.Parsed()
        line = ("  SKIPdata/dbs-statements/dbs-debit-cards/2024/12  SGD   0 txn  open  - "
                "-> close  -  card statement, no balances\n")
        with mock.patch("finance.sync.subprocess.run",
                        return_value=mock.Mock(returncode=0, stdout=line, stderr="")):
            sync._run_parser(CFG, "finance.parsers.parse_statements", ["data/dbs-statements"], Path("/tmp"),
                             parsed, echo=False)
        self.assertEqual(parsed.skipped, {"data/dbs-statements/dbs-debit-cards/2024/12.pdf"})
        self.assertEqual(parsed.problems, [])

    def test_wallet_folder_not_in_the_config(self):
        cfg = self.cfg(per_currency_subfolders=("sgd-main",))
        good = block(source="statements/2026/1.pdf")
        self.assertEqual(codes(sync.check_statements(cfg, parsed_of(rows=[], blocks=[good]))),
                         ["unknown-wallet-folder"])


class BlockTest(unittest.TestCase):

    def test_clean_blocks_are_quiet(self):
        self.assertEqual(sync.check_blocks(parsed_of()), [])

    def test_two_statements_with_the_same_period(self):
        twice = [block(), block(source="data/dbs-statements/posb-everyday/2026/1 (1).pdf")]
        self.assertEqual(codes(sync.check_blocks(parsed_of(blocks=twice))), ["duplicate-period"])

    def test_two_rows_with_the_same_key(self):
        twice = [row(), row(date="2026-01-06")]
        self.assertEqual(codes(sync.check_blocks(parsed_of(rows=twice))), ["duplicate-key"])

    def test_block_that_does_not_reconcile(self):
        self.assertEqual(codes(sync.check_blocks(parsed_of(blocks=[block(reconciled="False")]))),
                         ["block-not-reconciled"])

    def test_missing_closing_balance_needs_a_note(self):
        silent = block(closing="", note="")
        self.assertEqual(codes(sync.check_blocks(parsed_of(blocks=[silent]))),
                         ["balance-unexplained"])
        explained = block(closing="", note="card statement, no balances")
        self.assertEqual(sync.check_blocks(parsed_of(blocks=[explained])), [])


class ConfigCoverageTest(unittest.TestCase):

    def test_account_without_a_page(self):
        unknown = parsed_of(rows=[row(account_no="999-99999-9")],
                            blocks=[block(account_no="999-99999-9")])
        self.assertEqual(codes(sync.check_config_coverage(CFG, unknown)), ["unknown-account"])

    def test_card_without_a_page(self):
        self.assertEqual(codes(sync.check_config_coverage(CFG, parsed_of(rows=[row(card_last4="9999")]))),
                         ["unknown-card"])

    def test_clean_coverage_is_quiet(self):
        self.assertEqual(sync.check_config_coverage(CFG, parsed_of()), [])


class OptionTest(unittest.TestCase):

    def test_select_value_notion_does_not_have(self):
        parsed = parsed_of(rows=[row(bank_txn_type="Crypto Wizardry")])
        problems = sync.check_options(CFG, parsed, state_with())
        self.assertEqual(codes(problems), ["unknown-option"])
        self.assertIn("Crypto Wizardry", problems[0].subject)

    def test_block_currency_is_checked_too(self):
        parsed = parsed_of(rows=[], blocks=[block(currency="JPY")])
        problems = sync.check_options(CFG, parsed, state_with())
        self.assertIn("Period balances Currency", problems[0].subject)

    def test_known_values_are_quiet(self):
        self.assertEqual(sync.check_options(CFG, parsed_of(), state_with()), [])


class RelationTest(unittest.TestCase):

    def test_payee_not_in_notion(self):
        parsed = parsed_of(rows=[row(payee="Kopitiam", category="Eating out"),
                                 row(txn_key="dddd", payee="Nowhere Cafe",
                                     category="Eating out")])
        self.assertEqual(codes(sync.check_relations(parsed, state_with())), ["missing-payee"])

    def test_category_not_in_notion(self):
        parsed = parsed_of(rows=[row(payee="Kopitiam", category="Astrology")])
        self.assertEqual(codes(sync.check_relations(parsed, state_with())), ["missing-category"])


class InsertSizeTest(unittest.TestCase):
    """F1: a parser change can rewrite every txn_key, and the run would then
    duplicate every row rather than add anything."""

    def plan_with(self, new_rows, new_blocks):
        plan = sync.Plan()
        plan.create_txns = [(row(txn_key=f"k{i}"), {}) for i in range(new_rows)]
        plan.create_periods = [(block(period=f"b{i}", n_txns="30"), {}) for i in range(new_blocks)]
        return plan

    def test_new_rows_far_beyond_the_new_blocks(self):
        plan = self.plan_with(new_rows=500, new_blocks=1)
        problems = sync.check_insert_size(parsed_of(), None, plan, force=False)
        self.assertEqual(codes(problems), ["implausible-insert"])

    def test_force_overrides_it(self):
        plan = self.plan_with(new_rows=500, new_blocks=1)
        self.assertEqual(sync.check_insert_size(parsed_of(), None, plan, force=True), [])

    def test_a_normal_month_passes(self):
        plan = self.plan_with(new_rows=30, new_blocks=1)
        self.assertEqual(sync.check_insert_size(parsed_of(), None, plan, force=False), [])

    def test_a_handful_of_new_rows_in_old_blocks_passes(self):
        plan = self.plan_with(new_rows=5, new_blocks=0)
        self.assertEqual(sync.check_insert_size(parsed_of(), None, plan, force=False), [])


class OrphanTest(unittest.TestCase):

    def test_orphans_block_the_run_until_they_are_dealt_with(self):
        client = FakeNotion()
        parsed = parsed_of()
        state = sync.read_state(CFG, client)
        sync.upsert(CFG, client, parsed, state, sync.build_plan(CFG, parsed, state), echo=False)
        gone = parsed_of(rows=[], blocks=[block(n_txns="0")])
        state = sync.read_state(CFG, client)
        plan = sync.build_plan(CFG, gone, state)
        self.assertEqual(codes(sync.check_orphans(plan)), ["orphan-rows"])
        self.assertNotIn("orphan-rows",
                         codes(sync.doctor(CFG, gone, state, plan, archive_orphans=True)))


def run_parser(returncode=0, stderr="", stdout="", paths=("data/dbs-statements",),
               module="finance.parsers.parse_statements"):
    """One mocked parser subprocess, with whatever it printed."""
    parsed = sync.Parsed()
    fake = mock.Mock(returncode=returncode, stderr=stderr, stdout=stdout)
    with mock.patch("finance.sync.subprocess.run", return_value=fake):
        sync._run_parser(CFG, module, list(paths), Path("/tmp"), parsed, echo=False)
    return parsed


def parser_warning(source, message):
    """The warning line the parser really prints, printed by the parser itself,
    so a test fails if the wording and the regex ever part company again (F2)."""
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        parse_statements.warn(source, message)
    return buf.getvalue()


class ParserOutputTest(unittest.TestCase):
    """F2: the parsers' warnings and exit codes must reach doctor."""

    SOURCE = "data/dbs-statements/wise/2026/1.pdf"

    def test_the_wording_is_the_parsers_own(self):
        self.assertEqual(parser_warning(self.SOURCE, "no transactions parsed"),
                         f"warning: {self.SOURCE}: no transactions parsed\n")

    def test_no_transactions_parsed_warning_becomes_a_problem(self):
        parsed = run_parser(stderr=parser_warning(self.SOURCE, "no transactions parsed"))
        self.assertEqual(codes(parsed.problems), ["no-rows-parsed"])
        self.assertEqual(parsed.problems[0].subject, self.SOURCE)

    def test_a_file_name_with_spaces_is_kept_whole(self):
        source = "data/dbs-statements/posb-everyday/2026/7 (1).pdf"
        parsed = run_parser(stderr=parser_warning(source, "no transactions parsed"))
        self.assertEqual([p.subject for p in parsed.problems], [source])

    def test_another_warning_about_the_same_file_is_left_alone(self):
        other = parser_warning(self.SOURCE, "no closing balance printed; the balance "
                                            "chain could not be checked")
        self.assertEqual(run_parser(stderr=other).problems, [])

    def test_non_zero_exit_becomes_a_problem(self):
        parsed = run_parser(returncode=1, module="finance.parsers.parse_youtrip",
                            paths=("data/youtrip-statements",))
        self.assertEqual(codes(parsed.problems), ["parser-failed"])

    def test_a_clean_run_adds_nothing(self):
        self.assertEqual(run_parser(stdout="47 PDFs -> 2114 transactions").problems, [])

    def test_a_reader_crash_names_the_file_not_the_script(self):
        root = str(CFG.root)
        stderr = ("subprocess.CalledProcessError: Command '['pdftotext', '-layout', "
                  f"'{root}/data/dbs-statements/posb-everyday/2026/7 (1).pdf', '-']' "
                  "returned non-zero exit status 1.\n")
        parsed = run_parser(returncode=1, stderr=stderr)
        self.assertEqual(codes(parsed.problems), ["parser-failed"])
        self.assertEqual(parsed.problems[0].subject,
                         "data/dbs-statements/posb-everyday/2026/7 (1).pdf")
        self.assertNotIn("output above", parsed.problems[0].fix)

    def test_a_block_that_fails_its_balance_names_its_file(self):
        root = str(CFG.root)
        stdout = (f"  OK  {root}/data/dbs-statements/posb-everyday/2026/6 SGD  3 txn  open 1.00\n"
                  f"  BAD {root}/data/dbs-statements/posb-everyday/2026/7 SGD  3 txn  open 1.00\n")
        parsed = run_parser(returncode=1, stdout=stdout,
                            stderr="\n1 block(s) and 0 row(s) failed the balance check")
        self.assertEqual(parsed.problems[0].subject,
                         "data/dbs-statements/posb-everyday/2026/7.pdf")

    def test_with_no_file_named_the_account_folder_is_the_subject(self):
        parsed = run_parser(returncode=1, module="finance.parsers.parse_youtrip",
                            paths=("data/youtrip-statements",))
        self.assertEqual(parsed.problems[0].subject, "data/youtrip-statements")


class DuplicatePeriodStderrTest(unittest.TestCase):
    """F1: the parser stops on a duplicate period before it writes a single CSV,
    so the run produced nothing and the one thing worth saying is which files
    collided. Both parsers use parse_statements.report_duplicate_periods."""

    FILES = ["data/dbs-statements/posb-everyday/2026/7 (1).pdf",
             "data/dbs-statements/posb-everyday/2026/7.pdf"]

    def parser_stderr(self):
        """What report_duplicate_periods really prints, then exits with."""
        blocks = [dict(account_no="130-24680-5", currency="SGD",
                       statement_date="31 Jul 2026", source=src) for src in self.FILES]
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf), self.assertRaises(SystemExit) as exit_:
            parse_statements.report_duplicate_periods(blocks)
        return f"{buf.getvalue()}{exit_.exception}\n"

    def test_the_block_is_read_back_into_one_problem(self):
        parsed = run_parser(returncode=1, stderr=self.parser_stderr())
        self.assertEqual(codes(parsed.problems), ["duplicate-period"])

    def test_the_subject_names_the_period_and_both_files(self):
        parsed = run_parser(returncode=1, stderr=self.parser_stderr())
        subject = parsed.problems[0].subject
        self.assertIn("130-24680-5 · SGD · 2026-07", subject)
        for name in self.FILES:
            self.assertIn(name, subject)

    def test_the_folders_it_never_reached_are_remembered(self):
        parsed = run_parser(returncode=1, stderr=self.parser_stderr())
        self.assertEqual(parsed.failed_roots, {"data/dbs-statements"})
        self.assertTrue(parsed.under_failed_parser("data/dbs-statements/posb-everyday/2026/1.pdf"))
        self.assertFalse(parsed.under_failed_parser("data/youtrip-statements/nok/2025/1.pdf"))

    def test_the_pdfs_it_never_reached_are_not_reported_one_by_one(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "statements" / "2026").mkdir(parents=True)
        (root / "statements" / "2026" / "1.pdf").write_bytes(b"%PDF-1.4")
        cfg = Config(root=root, version="v", token_env="", token_command="",
                     accounts=dict(CFG.accounts), cards=dict(CFG.cards),
                     folders=[Folder(path="statements", parser="dbs")])
        parsed = parsed_of(rows=[], blocks=[])
        self.assertEqual(codes(sync.check_statements(cfg, parsed)), ["pdf-not-parsed"])
        parsed.failed_roots.add("statements")
        self.assertEqual(sync.check_statements(cfg, parsed), [])

    def test_any_other_failure_is_still_parser_failed(self):
        parsed = run_parser(returncode=1,
                            stderr="3 block(s) and 0 row(s) failed the balance check")
        self.assertEqual(codes(parsed.problems), ["parser-failed"])


class SkipListTest(unittest.TestCase):
    """F3: the skipped card statements are only in the parser's stdout, so a
    `--csv DIR` rerun loses them unless the run writes the list down."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_round_trip_through_the_csv_dir(self):
        names = {"data/dbs-statements/dbs-debit-cards/2024/12.pdf",
                 "data/dbs-statements/dbs-debit-cards/2025/1.pdf"}
        sync.write_skipped(self.dir, names)
        self.assertEqual(sync.read_skipped(self.dir), names)

    def test_a_dir_with_no_skip_file_reads_as_empty(self):
        self.assertEqual(sync.read_skipped(self.dir), set())

    def test_parse_from_a_csv_dir_reads_the_list_back(self):
        names = {"data/dbs-statements/dbs-debit-cards/2024/12.pdf"}
        sync.write_skipped(self.dir, names)
        parsed = sync.parse(CFG, csv_dir=self.dir, echo=False)
        self.assertEqual(parsed.skipped, names)
        self.assertEqual(parsed.rows, [])


class RulesInputTest(unittest.TestCase):
    def test_a_failed_parser_keeps_the_last_rules_input(self):
        from unittest import mock
        failed = sync.Parsed()
        failed.csv_dir = Path(tempfile.mkdtemp())
        failed.problems.append(sync.Problem("parser-failed", "parse_wise.py", "it stopped"))
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(sync, "parse", return_value=failed), \
                mock.patch("finance.paths.cache_dir", return_value=Path(d)):
            (Path(d) / "rules-input.csv").write_text("last good\n")
            with self.assertRaises(SystemExit) as e:
                sync.write_rules_input(None)
            self.assertIn("parse_wise.py", str(e.exception))
            self.assertEqual((Path(d) / "rules-input.csv").read_text(), "last good\n")


class ReadCsvsTest(unittest.TestCase):
    """F5: _read_csvs left a file object open per CSV, which the test suite saw
    as a ResourceWarning and a long run would see as a file descriptor leak."""

    def test_reading_the_csvs_leaves_no_file_open(self):
        with tempfile.TemporaryDirectory() as name:
            d = Path(name)
            (d / "transactions.csv").write_text("txn_key,source\naaaa1111,x.pdf\n")
            (d / "period_balances.csv").write_text("period,source\np,x.pdf\n")
            gc.collect()  # garbage from earlier tests (an in-memory database) is not this test's
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                blocks, rows = sync._read_csvs(d)
                gc.collect()
        self.assertEqual([r["txn_key"] for r in rows], ["aaaa1111"])
        self.assertEqual([b["period"] for b in blocks], ["p"])
        self.assertEqual([str(w.message) for w in caught
                          if issubclass(w.category, ResourceWarning)], [])


class DoctorTest(unittest.TestCase):
    """The whole check, end to end, on a workspace that already matches."""

    def test_clean_workspace_has_no_problems(self):
        # the same account and card pages as the real config, but no statement
        # folders, so this is the property diff and the checks around it
        cfg = Config(root=CFG.root, version=CFG.version, token_env="", token_command="",
                     data_sources=dict(CFG.data_sources), accounts=dict(CFG.accounts),
                     cards=dict(CFG.cards), folders=[])
        client = FakeNotion()
        parsed = parsed_of()
        state = sync.read_state(cfg, client)
        plan = sync.build_plan(cfg, parsed, state)
        with mock.patch("shutil.which", return_value="/usr/bin/pdftotext"):
            self.assertEqual(sync.doctor(cfg, parsed, state, plan), [])

    def test_every_problem_carries_a_fix(self):
        p = Problem("code", "subject", "fix")
        self.assertIn("fix: fix", p.line())


if __name__ == "__main__":
    unittest.main()


class ReachableOptionsTest(unittest.TestCase):
    """reachable_options(): values a parser can produce before any statement has.

    Notion rejects an unknown select value, so a row of a kind the wallets have
    never printed would stop a monthly run at unknown-option. add-options adds
    these ahead of time, which is only worth anything while the list matches
    what the parsers can actually emit.
    """

    def test_every_listed_value_is_one_a_parser_can_emit(self):
        # G21: a layout lists its own in its module, so the value must be in
        # that module's code as well as in the list
        import importlib
        from pathlib import Path
        from finance.model import reachable_options
        from finance.parsers import layouts
        listed = []
        for lay in layouts.every():
            src = Path(importlib.import_module(lay.module).__file__).read_text()
            for value in lay.new_types:
                listed.append(value)
                self.assertGreaterEqual(src.count(f'"{value}"'), 2, f"{value} is in no branch of {lay.module}")
        self.assertEqual(list(reachable_options()[("transactions", "Bank txn type")]), listed)
        self.assertIn("YouTrip Refund", listed)

    def test_it_names_the_property_the_select_check_covers(self):
        from finance.model import SELECT_CHECKS, reachable_options
        checked = {(ds, prop) for ds, prop, _ in SELECT_CHECKS}
        for key in reachable_options():
            self.assertIn(key, checked, f"{key} is not a checked select")
