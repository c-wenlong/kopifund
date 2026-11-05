#!/usr/bin/env python3
"""The whole monthly run, end to end, with no agent in the loop.

These tests drive the real parsers over a temporary copy of the real statement
tree, the real rules, and the real categorise / doctor / build_plan / upsert /
verify code, against a FakeNotion. Nothing in the repository is touched: every
scenario gets its own tree under a temp directory, and the parsers write their
.txt dumps there rather than beside the checked-in PDFs.

What each scenario is for
-------------------------
Each one is a thing that has actually gone wrong, or will, on a month boundary:

  NewMonth          the ordinary case: one statement arrives, one block and its
                    rows are created, and running twice writes nothing
  DuplicateStatement the same statement saved twice ("7 (1).pdf")
  UnknownLayout     a PDF that is not a statement lands in a statement folder
  NewSelectValue    a statement prints a Bank txn type Notion has no option for
  NewCard           a card the config has no Cards page for
  NewAccountFolder  a statement folder whose account is not in [accounts]
  KeySchemeChange   a parser change rewrites txn_key, so every row looks new
  Reclassification  one row's Flow class changed; one patch of one property
  RemovedStatement  a statement left the tree, so its rows are orphans
  CsvReuse          `--csv DIR` reuses an earlier parser run

The property under test throughout is the one the runbook promises: a run that
doctor is unhappy about writes nothing at all, and a run with nothing to do
issues no API calls.
"""
import isolate  # first: no test writes the real record or cache
import argparse
import contextlib
import importlib.util
import io
import shutil
import tempfile
import unittest
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from fake_notion import FakeNotion

from finance import notion, sync
from finance.model import (SELECT_CHECKS, Config,
                           ConfigMissing, Folder, load_config)

REPO = Path(__file__).resolve().parent.parent

# End to end over the owner's own statement tree, so this module needs both the
# real config and the data folder's statements, neither of which a clone has. A module-level SkipTest
# makes unittest skip the module rather than error on import. Audit: the run sees the real data
# folder only when it asks (KOPIFUND_TEST_REAL_DATA=1, server/tests/isolate.py), and only here.
from finance.paths import statements_dir  # noqa: E402
with isolate.real_data():
    if not statements_dir().is_dir():
        raise unittest.SkipTest("no statements in the data folder; the end-to-end run needs them")
    try:
        REAL = load_config()
    except ConfigMissing as e:
        raise unittest.SkipTest(str(e))
    DATA = REAL.root
    RULES = sync.default_rules(REAL)

# the DBS parser's key function is borrowed so the key-scheme scenario changes keys the way a real parser change
# would rather than by inventing strings.
from finance.parsers import parse_statements                                         # noqa: E402


def _load_cli():
    """finance.py, which cannot be imported by name: the `finance` package next
    to it wins. plan_blockers lives there, and it decides whether a plan is
    built at all, so the run helper below has to use the real one."""
    spec = importlib.util.spec_from_file_location("finance_cli", REPO / "finance.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CLI = _load_cli()


# ---------------------------------------------------------------- workspaces

# One temporary statement tree and one parse per shape, shared by every test
# that needs it: the full tree takes about two seconds to parse and several
# scenarios only differ in what they do to the parsed rows afterwards.
TREES = {}
PARSES = {}
TEMPS = []

PAYLAH = "statements/dbs-paylah"
NEW_WALLET = "statements/dbs-newwallet"
AUGUST_PDF = "statements/posb-everyday/2026/2026-08.pdf"
# the owner's account numbers come from their own config, never from this file
POSB_ACCOUNT = next(f.account_no for f in REAL.folders if f.path.endswith("posb-everyday"))
AUGUST_PERIOD = f"{POSB_ACCOUNT} · SGD · 2026-08"
PAYLAH_ACCOUNT = next(f.account_no for f in REAL.folders if f.path == PAYLAH)
# a real PDF that is not a statement: the first one under documents/, so the
# pick is stable and none of the owner's file names is written here
_DOCUMENTS = sorted((DATA / "documents").rglob("*.pdf"))
NOT_A_STATEMENT = str(_DOCUMENTS[0].relative_to(DATA)) if _DOCUMENTS else None
ALIEN_PDF = "statements/posb-everyday/2026/not-a-statement.pdf"

SHAPES = {
    # the whole tree, as the owner keeps it
    "full": dict(),
    # everything Notion already holds: the tree as it was before August
    "july": dict(exclude=[AUGUST_PDF]),
    # the same statement downloaded twice, the way a browser names a second
    # download: a fresh download differs in its bytes (here one trailing byte),
    # so only the period collides
    "duplicate": dict(extra=[("statements/posb-everyday/2026/2026-07.pdf",
                              "statements/posb-everyday/2026/2026-07 (1).pdf")],
                      touch=["statements/posb-everyday/2026/2026-07 (1).pdf"]),
    # the very same file copied under a second name, byte for byte
    "exact-copy": dict(extra=[("statements/posb-everyday/2026/2026-07.pdf",
                               "statements/posb-everyday/2026/2026-07 (1).pdf")]),
    # a PDF that is not a statement at all, dropped into a statement folder
    "alien": dict(extra=[(NOT_A_STATEMENT, ALIEN_PDF)]),
    # a folder for an account the config has no Accounts page for
    "new-folder": dict(exclude=[PAYLAH], extra=[(PAYLAH, NEW_WALLET)]),
}


def _copy(src, dst):
    ignore = shutil.ignore_patterns("*.txt", ".*")
    if src.is_dir():
        shutil.copytree(src, dst, ignore=ignore)
    else:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def tree(shape):
    """The temporary statement tree for one shape, built once."""
    if shape in TREES:
        return TREES[shape]
    spec = SHAPES[shape]
    root = Path(tempfile.mkdtemp(prefix=f"finance_e2e_{shape}_"))
    TEMPS.append(root)
    for f in REAL.folders:
        if (DATA / f.path).exists():
            _copy(DATA / f.path, root / f.path)
    for rel in spec.get("exclude", ()):
        target = root / rel
        shutil.rmtree(target) if target.is_dir() else target.unlink()
    for src, rel in spec.get("extra", ()):
        _copy(DATA / src, root / rel)
    for rel in spec.get("touch", ()):
        with open(root / rel, "ab") as f:
            f.write(b"\n")
    TREES[shape] = root
    return root


def folders_for(shape):
    """The [[folder]] entries the config would carry for this tree."""
    if shape != "new-folder":
        return list(REAL.folders)
    out = []
    for f in REAL.folders:
        out.append(Folder(path=NEW_WALLET, parser=f.parser, account_no=f.account_no,
                          per_currency_subfolders=f.per_currency_subfolders)
                   if f.path == PAYLAH else f)
    return out


def config_for(shape, accounts=None):
    """The real config, pointed at a temporary tree."""
    return Config(root=tree(shape), version=REAL.version, token_env="", token_command="",
                  data_sources=dict(REAL.data_sources),
                  accounts=dict(REAL.accounts if accounts is None else accounts),
                  cards=dict(REAL.cards), folders=folders_for(shape))


def parse(shape):
    """(config, Parsed) for one shape: the real parsers, then the real rules."""
    if shape not in PARSES:
        cfg = config_for(shape)
        parsed = sync.parse(cfg, echo=False)
        sync.categorise(parsed, RULES)
        if parsed.csv_dir:
            TEMPS.append(parsed.csv_dir)
        PARSES[shape] = (cfg, parsed)
    cfg, parsed = PARSES[shape]
    return cfg, clone(parsed)


def clone(parsed):
    """A Parsed a test may edit without disturbing the cached parse."""
    return sync.Parsed(blocks=[dict(b) for b in parsed.blocks],
                       rows=[dict(r) for r in parsed.rows],
                       problems=list(parsed.problems), skipped=set(parsed.skipped),
                       failed_roots=set(parsed.failed_roots), csv_dir=parsed.csv_dir)


_REAL_UP, _REAL_DOWN = isolate.real_data_for_module()


def setUpModule():
    _REAL_UP()


def tearDownModule():
    _REAL_DOWN()
    for path in TEMPS:
        shutil.rmtree(path, ignore_errors=True)


# ---------------------------------------------------------------- the run

def schema_options(cfg, parsed):
    """The select options Notion already has, taken from a parse: every value
    the statements print today. A value outside this set is what the
    new-select-value scenario introduces."""
    out = {}
    for ds_name, prop, column in SELECT_CHECKS:
        rows = parsed.blocks if ds_name == "period_balances" else parsed.rows
        out[(cfg.ds(ds_name), prop)] = sorted({r[column] for r in rows if r.get(column)})
    return out


def notion_with_schema(cfg, parsed):
    """An empty Notion that already has the select options, the Payees rows and
    the Categories rows the rules ask for. Those are made by other commands
    (add-options, payees), so a sync that had to create them would be testing
    the wrong thing."""
    client = FakeNotion(options=schema_options(cfg, parsed))
    for ds_name, column in (("payees", "payee"), ("categories", "category")):
        for name in sorted({r[column] for r in parsed.rows if r.get(column)}):
            client.create(cfg.ds(ds_name), notion.to_api({"Name": name}, {"Name": "title"}))
    client.writes = 0
    client.calls.clear()
    return client


@dataclass
class Run:
    """One `finance.py sync`, as far as it got."""
    plan: object = None
    problems: list = field(default_factory=list)
    done: object = None
    verify_problems: list = None
    writes: int = 0
    calls: list = field(default_factory=list)

    @property
    def codes(self):
        return Counter(p.code for p in self.problems)

    @property
    def wrote(self):
        return self.done is not None

    def problem(self, code):
        return next(p for p in self.problems if p.code == code)


def monthly_run(cfg, parsed, client, force=False, archive_orphans=False):
    """The agent-free run, in finance.py cmd_sync's order and with its rules:
    read Notion, build the plan unless something makes a diff impossible, run
    every doctor check, and write only when doctor is silent."""
    before, calls = client.writes, len(client.calls)
    state = sync.read_state(cfg, client)
    plan = None if CLI.plan_blockers(cfg, parsed) else sync.build_plan(cfg, parsed, state)
    problems = sync.doctor(cfg, parsed, state, plan, force=force,
                           archive_orphans=archive_orphans)
    if problems:
        return Run(plan=plan, problems=problems, writes=client.writes - before,
                   calls=client.calls[calls:])
    done = sync.upsert(cfg, client, parsed, state, plan,
                       archive_orphans=archive_orphans, echo=False)
    _, verify_problems, _ = sync.verify(cfg, client, parsed, archived=archive_orphans)
    return Run(plan=plan, problems=problems, done=done, verify_problems=verify_problems,
               writes=client.writes - before, calls=client.calls[calls:])


def loaded(shape="july", schema_shape="full"):
    """(client, parsed) for a Notion already holding everything one shape
    parses to: the state a monthly run starts from.

    The select options and the relation rows come from `schema_shape` so that
    the months a scenario adds later are not themselves a schema change.
    """
    schema_cfg, schema_parsed = parse(schema_shape)
    cfg, parsed = parse(shape)
    client = notion_with_schema(schema_cfg, schema_parsed)
    state = sync.read_state(cfg, client)
    plan = sync.build_plan(cfg, parsed, state)
    problems = sync.doctor(cfg, parsed, state, plan)
    assert not problems, problems
    sync.upsert(cfg, client, parsed, state, plan, echo=False)
    client.writes = 0
    client.calls.clear()
    return client, parsed


# ---------------------------------------------------------------- scenarios

class SeedTest(unittest.TestCase):
    """The state every other scenario starts from has to be worth starting from."""

    def test_the_real_tree_parses_and_loads_clean(self):
        cfg, parsed = parse("full")
        self.assertEqual(parsed.problems, [])
        client = notion_with_schema(cfg, parsed)
        run = monthly_run(cfg, parsed, client)
        self.assertEqual(run.problems, [])
        self.assertEqual(run.verify_problems, [])
        self.assertEqual(run.writes, len(parsed.rows) + len(parsed.blocks))
        self.assertEqual({c[0] for c in run.calls}, {"create"})

    def test_a_second_run_over_the_same_tree_writes_nothing(self):
        client, parsed = loaded("full")
        cfg, _ = parse("full")
        run = monthly_run(cfg, parsed, client)
        self.assertEqual(run.problems, [])
        self.assertEqual(run.plan.writes, 0)
        self.assertEqual(run.writes, 0)
        self.assertEqual(client.calls, [])
        self.assertEqual(run.plan.same_txns, len(parsed.rows))
        self.assertEqual(run.plan.same_periods, len(parsed.blocks))


class NewMonthTest(unittest.TestCase):
    """The ordinary month: Notion holds everything up to July, the August POSB
    statement arrives, and exactly that block and its rows are created."""

    def setUp(self):
        self.client, self.july = loaded("july")
        self.cfg, self.august = parse("full")

    def test_the_new_month_is_the_only_difference(self):
        new_blocks = ({b["period"] for b in self.august.blocks}
                      - {b["period"] for b in self.july.blocks})
        self.assertEqual(new_blocks, {AUGUST_PERIOD})
        self.assertEqual(self.july.keys - self.august.keys, set())

    def test_doctor_is_clean_and_the_plan_is_exactly_the_new_month(self):
        run = monthly_run(self.cfg, self.august, self.client)
        self.assertEqual(run.problems, [])
        self.assertEqual([b["period"] for b, _ in run.plan.create_periods], [AUGUST_PERIOD])
        self.assertEqual(run.plan.patch_periods, [])
        self.assertEqual(run.plan.patch_txns, [])
        self.assertEqual(run.plan.orphan_txns, [])
        self.assertEqual(run.plan.orphan_periods, [])
        new_keys = self.august.keys - self.july.keys
        self.assertEqual({r["txn_key"] for r, _ in run.plan.create_txns}, new_keys)
        self.assertTrue(all(r["period"] == AUGUST_PERIOD for r, _ in run.plan.create_txns))

    def test_upsert_creates_exactly_the_new_block_and_its_rows(self):
        expected = len(self.august.keys - self.july.keys) + 1
        run = monthly_run(self.cfg, self.august, self.client)
        self.assertEqual(run.writes, expected)
        self.assertEqual([c[0] for c in run.calls], ["create"] * expected)
        self.assertEqual(run.verify_problems, [])
        page = next(p for p in self.client.query_all(self.cfg.ds("period_balances"))
                    if notion.plain(p, "Name") == AUGUST_PERIOD)
        block = next(b for b in self.august.blocks if b["period"] == AUGUST_PERIOD)
        self.assertEqual(notion.plain(page, "Closing balance"), float(block["closing"]))
        rows = [p for p in self.client.query_all(self.cfg.ds("transactions"))
                if notion.plain(p, "Period") == [page["id"]]]
        self.assertEqual(len(rows), int(block["n_txns"]))

    def test_the_second_run_of_the_same_month_writes_nothing(self):
        monthly_run(self.cfg, self.august, self.client)
        self.client.calls.clear()
        run = monthly_run(self.cfg, self.august, self.client)
        self.assertEqual(run.problems, [])
        self.assertEqual(run.plan.writes, 0)
        self.assertEqual(run.calls, [])


class ExactCopyTest(unittest.TestCase):
    """The same file copied byte for byte under a second name: one problem, not
    a duplicate period beside it, since both say the same thing (G13)."""

    def test_a_byte_copy_is_one_same_file_problem(self):
        cfg, parsed = parse("exact-copy")
        problems = sync.doctor(cfg, parsed, None, None)
        self.assertEqual(dict(Counter(p.code for p in problems)), {"same-file-two-places": 1})
        p = problems[0]
        self.assertIn("2026-07.pdf", p.subject)
        self.assertIn("2026-07 (1).pdf", p.subject)
        self.assertEqual(set(p.paths), {"statements/posb-everyday/2026/2026-07.pdf",
                                        "statements/posb-everyday/2026/2026-07 (1).pdf"})
        self.assertIn("never delete", p.fix)


class DuplicateStatementTest(unittest.TestCase):
    """The same statement saved twice, as "7 (1).pdf" beside "7.pdf"."""

    def test_the_run_stops_and_writes_nothing(self):
        client, _ = loaded("july")
        cfg, parsed = parse("duplicate")
        run = monthly_run(cfg, parsed, client)
        self.assertTrue(run.problems)
        self.assertFalse(run.wrote)
        self.assertEqual(run.writes, 0)
        self.assertEqual(run.calls, [])
        self.assertIsNone(run.plan)

    def test_the_duplicate_period_is_the_only_problem(self):
        # The DBS parser exits non-zero on a duplicate period before it writes
        # any CSV, so no DBS statement in that run produced anything. The one
        # finding worth reading is which two files collided (F1): the parser's
        # stderr is read for it, and the other 48 PDFs it never got to are not
        # reported as pdf-not-parsed on top.
        cfg, parsed = parse("duplicate")
        problems = sync.doctor(cfg, parsed, None, None)
        self.assertEqual(dict(Counter(p.code for p in problems)), {"duplicate-period": 1})
        self.assertEqual([p.code for p in CLI.plan_blockers(cfg, parsed)],
                         ["duplicate-period"])
        self.assertTrue(all(p.fix for p in problems))

    def test_the_problem_names_both_files_and_the_period(self):
        cfg, parsed = parse("duplicate")
        subject = sync.doctor(cfg, parsed, None, None)[0].subject
        self.assertIn("statements/posb-everyday/2026/2026-07.pdf", subject)
        self.assertIn("statements/posb-everyday/2026/2026-07 (1).pdf", subject)
        self.assertIn(POSB_ACCOUNT, subject)

    def test_the_dbs_pdfs_it_never_reached_are_not_reported_one_by_one(self):
        cfg, parsed = parse("duplicate")
        dbs = [f for f in cfg.folders if f.parser == "dbs"]
        self.assertTrue(len(list(dbs[0].pdfs(cfg.root))) > 1)
        self.assertEqual(parsed.failed_roots, {f.path for f in dbs})
        codes = Counter(p.code for p in sync.check_statements(cfg, parsed))
        self.assertNotIn("pdf-not-parsed", codes)


class UnknownLayoutTest(unittest.TestCase):
    """A PDF that is not a statement, dropped into a statement folder."""

    ALIEN = ALIEN_PDF

    def setUp(self):
        if NOT_A_STATEMENT is None:
            self.skipTest("no PDF under the data folder's documents/ to drop in")

    def test_doctor_names_the_file_and_nothing_is_written(self):
        # two findings, one from each side: the parser warns that the file
        # matched no layout (F2, its stderr reaches doctor), and doctor's own
        # sweep of the tree sees a PDF that produced neither rows nor a block
        client, _ = loaded("july")
        cfg, parsed = parse("alien")
        run = monthly_run(cfg, parsed, client)
        self.assertEqual(dict(run.codes), {"no-rows-parsed": 1, "pdf-not-parsed": 1})
        problem = run.problem("pdf-not-parsed")
        self.assertEqual(problem.subject, self.ALIEN)
        self.assertIn("move it out of the statement tree", problem.fix)
        self.assertEqual(run.problem("no-rows-parsed").subject, self.ALIEN)
        self.assertFalse(run.wrote)
        self.assertEqual(run.writes, 0)
        self.assertEqual(run.calls, [])

    def test_the_rest_of_the_tree_still_parsed(self):
        # the alien file costs nothing but the run: every real statement is
        # still there, so the fix is to move one file and rerun
        _, alien = parse("alien")
        _, full = parse("full")
        self.assertEqual(alien.keys, full.keys)


class NewSelectValueTest(unittest.TestCase):
    """A statement prints a Bank txn type Notion has no option for."""

    def setUp(self):
        self.client, _ = loaded("july")
        self.cfg, self.parsed = parse("full")
        self.parsed.rows[0]["bank_txn_type"] = "Crypto Wizardry"

    def test_doctor_reports_the_option_with_the_command_that_adds_it(self):
        run = monthly_run(self.cfg, self.parsed, self.client)
        self.assertEqual(dict(run.codes), {"unknown-option": 1})
        problem = run.problem("unknown-option")
        self.assertEqual(problem.subject, "Bank txn type = 'Crypto Wizardry'")
        self.assertIn("finance.py add-options", problem.fix)

    def test_nothing_is_written(self):
        run = monthly_run(self.cfg, self.parsed, self.client)
        self.assertFalse(run.wrote)
        self.assertEqual(run.writes, 0)
        self.assertEqual(run.calls, [])

    def test_the_plan_was_still_built_so_the_report_can_show_it(self):
        # an unknown option does not stop a diff, only a write: the owner should
        # see what the month would have added while they add the option
        run = monthly_run(self.cfg, self.parsed, self.client)
        self.assertIsNotNone(run.plan)
        self.assertEqual([b["period"] for b, _ in run.plan.create_periods], [AUGUST_PERIOD])


class NewCardTest(unittest.TestCase):
    """A card number the config has no Cards page for."""

    def setUp(self):
        self.client, _ = loaded("july")
        self.cfg, self.parsed = parse("full")
        carded = next(r for r in self.parsed.rows if r["card_last4"])
        carded["card_last4"] = "9999"
        self.row = carded

    def test_doctor_reports_the_card(self):
        run = monthly_run(self.cfg, self.parsed, self.client)
        self.assertEqual(dict(run.codes), {"unknown-card": 1})
        problem = run.problem("unknown-card")
        self.assertEqual(problem.subject, "9999")
        self.assertIn("[cards] in the data folder's config.toml", problem.fix)

    def test_no_plan_is_built_and_nothing_is_written(self):
        # there is no Cards page to point the relation at, so the row has no
        # page body to compare against and the diff cannot be built at all
        run = monthly_run(self.cfg, self.parsed, self.client)
        self.assertIsNone(run.plan)
        self.assertEqual(run.writes, 0)
        self.assertEqual(run.calls, [])
        with self.assertRaises(KeyError):
            sync.build_plan(self.cfg, self.parsed, sync.read_state(self.cfg, self.client))


class NewAccountFolderTest(unittest.TestCase):
    """A statement folder whose account has no page in [accounts]: the PayLah
    folder, as it would look the month it was first added."""

    def setUp(self):
        accounts = {k: v for k, v in REAL.accounts.items() if k != PAYLAH_ACCOUNT}
        self.cfg = config_for("new-folder", accounts=accounts)
        _, self.parsed = parse("new-folder")
        schema_cfg, schema_parsed = parse("full")
        self.client = notion_with_schema(schema_cfg, schema_parsed)

    def test_the_new_folder_was_parsed(self):
        sources = {r["source"] for r in self.parsed.rows}
        self.assertTrue(any(s.startswith(NEW_WALLET) for s in sources))
        self.assertIn(PAYLAH_ACCOUNT, {r["account_no"] for r in self.parsed.rows})

    def test_doctor_reports_the_account_before_anything_is_written(self):
        run = monthly_run(self.cfg, self.parsed, self.client)
        self.assertEqual(dict(run.codes), {"unknown-account": 1})
        problem = run.problem("unknown-account")
        self.assertEqual(problem.subject, PAYLAH_ACCOUNT)
        self.assertIn("[accounts] in the data folder's config.toml", problem.fix)
        self.assertIsNone(run.plan)
        self.assertEqual(run.writes, 0)
        self.assertEqual(run.calls, [])

    def test_adding_the_account_to_the_config_is_the_whole_fix(self):
        run = monthly_run(config_for("new-folder"), self.parsed, self.client)
        self.assertEqual(run.problems, [])
        self.assertEqual(run.verify_problems, [])


class KeySchemeChangeTest(unittest.TestCase):
    """F1: a parser change rewrites txn_key, so every touched row looks new and
    its Notion page looks orphaned. The insert-size check is the only thing
    between that and a second copy of every row."""

    CHANGED = 200

    def setUp(self):
        self.client, _ = loaded("full")
        self.cfg, self.parsed = parse("full")
        self.before = {r["txn_key"] for r in self.parsed.rows[:self.CHANGED]}
        for row in self.parsed.rows[:self.CHANGED]:
            row["merchant"] = f"{row['merchant']} PTE LTD"
        parse_statements.add_keys(self.parsed.rows[:self.CHANGED])
        self.after = {r["txn_key"] for r in self.parsed.rows[:self.CHANGED]}

    def test_the_keys_really_changed(self):
        self.assertEqual(len(self.after), self.CHANGED)
        self.assertEqual(self.before & self.after, set())

    def test_doctor_refuses_and_says_which_two_things_it_saw(self):
        run = monthly_run(self.cfg, self.parsed, self.client)
        self.assertEqual(dict(run.codes),
                         {"implausible-insert": 1, "orphan-rows": 1})
        insert = run.problem("implausible-insert")
        self.assertIn(f"{self.CHANGED} new rows", insert.subject)
        self.assertIn("--force", insert.fix)
        orphans = run.problem("orphan-rows")
        self.assertIn(f"{self.CHANGED} transaction(s)", orphans.subject)
        self.assertIn("--archive-orphans", orphans.fix)
        self.assertEqual(run.writes, 0)
        self.assertEqual(run.calls, [])

    def test_force_alone_still_leaves_the_orphans_reported(self):
        run = monthly_run(self.cfg, self.parsed, self.client, force=True)
        self.assertEqual(dict(run.codes), {"orphan-rows": 1})
        self.assertEqual(run.writes, 0)

    def test_both_flags_rewrite_the_rows_rather_than_duplicating_them(self):
        run = monthly_run(self.cfg, self.parsed, self.client,
                          force=True, archive_orphans=True)
        self.assertEqual(run.problems, [])
        self.assertEqual(Counter(c[0] for c in run.calls),
                         Counter(create=self.CHANGED, archive=self.CHANGED))
        self.assertEqual(run.verify_problems, [])
        live = {notion.plain(p, "Txn key")
                for p in self.client.query_all(self.cfg.ds("transactions"))}
        self.assertEqual(len(live), len(self.parsed.rows))
        self.assertTrue(self.after <= live)
        self.assertEqual(self.before & live, set())

    def test_a_normal_month_is_nowhere_near_the_limit(self):
        # the guard has to stay quiet on the case it shares a code path with
        client, _ = loaded("july")
        cfg, august = parse("full")
        run = monthly_run(cfg, august, client)
        self.assertEqual(run.problems, [])


class CsvReuseTest(unittest.TestCase):
    """F3: `--csv DIR` skips the parsers, so everything doctor needs from them
    has to be in the directory. The skip list is the part that is not in either
    CSV: without it the two card statements the account statements already
    cover come back as pdf-not-parsed."""

    def test_the_run_really_skips_some_card_statements(self):
        _, parsed = parse("full")
        self.assertTrue(parsed.skipped)
        self.assertTrue(all(name.endswith(".pdf") for name in parsed.skipped))

    def test_the_skip_list_is_written_beside_the_csvs(self):
        _, parsed = parse("full")
        self.assertEqual(sync.read_skipped(parsed.csv_dir), parsed.skipped)

    def test_reusing_the_csv_dir_reports_nothing_new(self):
        cfg, parsed = parse("full")
        again = sync.parse(cfg, csv_dir=parsed.csv_dir, echo=False)
        self.assertEqual(again.skipped, parsed.skipped)
        self.assertEqual(again.keys, parsed.keys)
        self.assertEqual(sync.check_statements(cfg, again), [])

    def test_a_csv_dir_with_no_skip_list_is_what_used_to_go_wrong(self):
        cfg, parsed = parse("full")
        stripped = sync.parse(cfg, csv_dir=parsed.csv_dir, echo=False)
        stripped.skipped = set()
        codes = Counter(p.code for p in sync.check_statements(cfg, stripped))
        self.assertEqual(dict(codes), {"pdf-not-parsed": len(parsed.skipped)})


class ReclassificationTest(unittest.TestCase):
    """The rules or the parser reclassify one row: one patch of one property."""

    def setUp(self):
        self.client, _ = loaded("full")
        self.cfg, self.parsed = parse("full")
        self.row = next(r for r in self.parsed.rows if r["flow_class"] == "External")
        self.row["flow_class"] = "Internal - own account"

    def test_the_plan_is_one_property_on_one_page(self):
        run = monthly_run(self.cfg, self.parsed, self.client)
        self.assertEqual(run.problems, [])
        self.assertEqual(len(run.plan.patch_txns), 1)
        page, row, _, changed = run.plan.patch_txns[0]
        self.assertEqual(changed, ["Flow class"])
        self.assertEqual(row["txn_key"], self.row["txn_key"])
        self.assertEqual(run.plan.create_txns, [])
        self.assertEqual(run.plan.same_txns, len(self.parsed.rows) - 1)

    def test_upsert_issues_exactly_one_patch(self):
        run = monthly_run(self.cfg, self.parsed, self.client)
        self.assertEqual(run.writes, 1)
        self.assertEqual([c[0] for c in run.calls], ["patch"])
        page = next(p for p in self.client.query_all(self.cfg.ds("transactions"))
                    if notion.plain(p, "Txn key") == self.row["txn_key"])
        self.assertEqual(notion.plain(page, "Flow class"), "Internal - own account")
        self.assertEqual(notion.plain(page, "Merchant raw"), self.row["merchant"])
        self.assertEqual(run.verify_problems, [])

    def test_the_run_after_it_writes_nothing(self):
        monthly_run(self.cfg, self.parsed, self.client)
        self.client.calls.clear()
        run = monthly_run(self.cfg, self.parsed, self.client)
        self.assertEqual(run.plan.writes, 0)
        self.assertEqual(run.calls, [])


class RemovedStatementTest(unittest.TestCase):
    """A statement left the tree after Notion was loaded from it."""

    def setUp(self):
        self.client, self.full = loaded("full")
        self.cfg, self.july = parse("july")
        self.gone = self.full.keys - self.july.keys

    def test_doctor_reports_the_orphan_keys_and_writes_nothing(self):
        run = monthly_run(self.cfg, self.july, self.client)
        self.assertEqual(dict(run.codes), {"orphan-rows": 1, "orphan-blocks": 1})
        problem = run.problem("orphan-rows")
        self.assertIn(f"{len(self.gone)} transaction(s)", problem.subject)
        self.assertIn("--archive-orphans", problem.fix)
        self.assertEqual(run.writes, 0)
        self.assertEqual(run.calls, [])
        self.assertEqual({notion.plain(p, "Txn key") for p in run.plan.orphan_txns},
                         self.gone)

    def test_upsert_does_not_archive_without_the_flag(self):
        state = sync.read_state(self.cfg, self.client)
        plan = sync.build_plan(self.cfg, self.july, state)
        sync.upsert(self.cfg, self.client, self.july, state, plan, echo=False)
        self.assertNotIn("archive", [c[0] for c in self.client.calls])
        live = {notion.plain(p, "Txn key")
                for p in self.client.query_all(self.cfg.ds("transactions"))}
        self.assertTrue(self.gone <= live)

    def test_doctor_reports_the_blocks_orphan_page_too(self):
        run = monthly_run(self.cfg, self.july, self.client)
        problem = run.problem("orphan-blocks")
        self.assertEqual(problem.subject, f"1 period page(s): {AUGUST_PERIOD}")
        self.assertIn("--archive-orphans", problem.fix)
        self.assertEqual([notion.plain(p, "Name") for p in run.plan.orphan_periods],
                         [AUGUST_PERIOD])

    def test_the_flag_archives_exactly_those_keys_and_their_block(self):
        run = monthly_run(self.cfg, self.july, self.client, archive_orphans=True)
        self.assertEqual(run.problems, [])
        self.assertEqual(Counter(c[0] for c in run.calls),
                         Counter(archive=len(self.gone) + 1))
        live = {notion.plain(p, "Txn key")
                for p in self.client.query_all(self.cfg.ds("transactions"))}
        self.assertEqual(live, self.july.keys)
        self.assertEqual(run.verify_problems, [])

    def test_the_rows_are_archived_before_the_block_that_held_them(self):
        # a Period page trashed while its rows still point at it would leave
        # them without the relation verify insists on
        run = monthly_run(self.cfg, self.july, self.client, archive_orphans=True)
        block_page = run.done.orphan_periods[0]["id"]
        archived = [c[1] for c in run.calls if c[0] == "archive"]
        self.assertEqual(archived[-1], block_page)
        self.assertEqual(len(archived), len(self.gone) + 1)

    def test_the_blocks_orphan_page_is_reported_and_then_gone(self):
        run = monthly_run(self.cfg, self.july, self.client, archive_orphans=True)
        self.assertEqual([notion.plain(p, "Name") for p in run.done.orphan_periods],
                         [AUGUST_PERIOD])
        names = {notion.plain(p, "Name")
                 for p in self.client.query_all(self.cfg.ds("period_balances"))}
        self.assertNotIn(AUGUST_PERIOD, names)
        text = sync.report(self.cfg, self.july, run.done, run.problems, wrote=True)
        self.assertIn(AUGUST_PERIOD, text)

    def test_verify_flags_a_live_block_page_the_parser_no_longer_covers(self):
        state = sync.read_state(self.cfg, self.client)
        plan = sync.build_plan(self.cfg, self.july, state)
        sync.upsert(self.cfg, self.client, self.july, state, plan,
                    archive_orphans=False, echo=False)
        _, problems, _ = sync.verify(self.cfg, self.client, self.july)
        problem = next(p for p in problems if p.code == "verify-orphan-blocks")
        self.assertIn(AUGUST_PERIOD, problem.subject)


class SnapshotAfterTheRunTest(unittest.TestCase):
    """A real run ends by writing today's analytics snapshot from the pages it
    already holds, so `finance.py analyse` reuses it instead of reading the
    eight databases a second time. `finance.py sync` is driven here in full,
    rollup and verify included, because the snapshot has to be built from the
    state those steps leave behind.
    """

    def setUp(self):
        from finance import snapshot as snap
        self.snap = snap
        self.cache = Path(tempfile.mkdtemp(prefix="finance_e2e_cache_"))
        TEMPS.append(self.cache)
        self.client, _ = loaded("july")
        self.cfg, self.august = parse("full")
        # the cache and the offline categories copy both default to paths in
        # the repository, and a test may not write to either
        self.patch(snap, "CACHE", self.cache)
        self.patch(snap, "CATEGORIES_FALLBACK", self.cache / "categories.json")
        self.patch(CLI, "client_for", lambda cfg: self.client)

    def patch(self, target, name, value):
        was = getattr(target, name)
        setattr(target, name, value)
        self.addCleanup(setattr, target, name, was)

    def args(self, **over):
        return argparse.Namespace(**{"dry_run": False, "force": False,
                                     "archive_orphans": False, "report": None,
                                     "rules": [str(r) for r in RULES],
                                     "csv": str(self.august.csv_dir), **over})

    def run_sync(self, **over):
        """`finance.py sync` itself, with the output kept out of the report."""
        said = io.StringIO()
        with contextlib.redirect_stdout(said):
            code = CLI.cmd_sync(self.cfg, self.args(**over))
        return code, said.getvalue()

    def snapshot_path(self):
        return self.cache / self.snap.snapshot_name()

    def test_a_real_run_leaves_todays_snapshot_behind(self):
        code, said = self.run_sync()
        self.assertEqual(code, CLI.CLEAN)
        self.assertTrue(self.snapshot_path().exists())
        self.assertIn(f"snapshot: wrote {self.snapshot_path().name}", said)
        self.assertIn("analyse will reuse it", said)

    def test_the_line_counts_the_transactions_in_the_file(self):
        _, said = self.run_sync()
        written = self.snap.load(self.snapshot_path())
        self.assertIn(f"({len(written['transactions'])} transactions)", said)
        self.assertEqual(len(written["transactions"]), len(self.august.rows))

    def test_it_is_what_a_fresh_read_of_the_same_notion_would_have_built(self):
        self.run_sync()
        written = self.snap.load(self.snapshot_path())
        del written["meta"]["file"]
        fresh = self.snap.build(self.cfg, source="notion", client=self.client)
        written["meta"]["generated_at"] = fresh["meta"]["generated_at"] = "fixed"
        self.assertEqual(self.snap.dumps(written), self.snap.dumps(fresh))

    def test_it_carries_the_rows_the_run_has_just_created(self):
        new_keys = self.august.keys - {r["txn_key"] for r in self.august.rows
                                       if r["period"] != AUGUST_PERIOD}
        self.run_sync()
        written = self.snap.load(self.snapshot_path())
        self.assertTrue(new_keys <= {r["key"] for r in written["transactions"]})

    def test_analyse_reuses_it_rather_than_reading_notion_again(self):
        from analytics import run as analytics_run
        self.run_sync()
        said = []
        s, path = analytics_run.build_or_reuse(self.cfg, cache=self.cache,
                                               echo=said.append)
        self.assertEqual(path, self.snapshot_path())
        self.assertTrue(any("reusing" in line for line in said))
        self.assertFalse(any("building" in line for line in said))
        self.assertEqual(len(s["transactions"]), len(self.august.rows))

    def test_a_dry_run_writes_no_snapshot(self):
        code, said = self.run_sync(dry_run=True)
        self.assertEqual(code, CLI.CLEAN)
        self.assertFalse(self.snapshot_path().exists())
        self.assertEqual(list(self.cache.iterdir()), [])
        self.assertNotIn("snapshot:", said)

    def test_rollup_on_its_own_writes_the_same_snapshot(self):
        """`finance.py rollup` reads everything too, and it patches Trip and
        Dates on the pages it read, so its snapshot has to come out the same as
        a fresh read of the Notion it just finished writing to."""
        said = io.StringIO()
        with contextlib.redirect_stdout(said):
            self.assertEqual(CLI.cmd_sync(self.cfg, self.args()), CLI.CLEAN)
            self.snapshot_path().unlink()
            self.assertEqual(CLI.cmd_rollup(self.cfg, self.args()), CLI.CLEAN)
        self.assertTrue(self.snapshot_path().exists())
        written = self.snap.load(self.snapshot_path())
        del written["meta"]["file"]
        fresh = self.snap.build(self.cfg, source="notion", client=self.client)
        written["meta"]["generated_at"] = fresh["meta"]["generated_at"] = "fixed"
        self.assertEqual(self.snap.dumps(written), self.snap.dumps(fresh))

    def test_a_dry_run_rollup_writes_no_snapshot(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(CLI.cmd_rollup(self.cfg, self.args(dry_run=True)),
                             CLI.CLEAN)
        self.assertFalse(self.snapshot_path().exists())

    def test_a_snapshot_that_cannot_be_written_is_a_warning_not_a_failed_sync(self):
        """The run has already written everything it set out to write by the
        time this step happens, so nothing here may change its exit code."""
        def boom(*a, **kw):
            raise OSError("read-only file system")
        self.patch(self.snap, "save", boom)
        code, said = self.run_sync()
        self.assertEqual(code, CLI.CLEAN)
        self.assertIn("snapshot: not written (OSError: read-only file system)", said)
        self.assertFalse(self.snapshot_path().exists())


if __name__ == "__main__":
    unittest.main()
