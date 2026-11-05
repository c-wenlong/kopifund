#!/usr/bin/env python3
"""The monthly sync: parse, categorise, doctor, upsert, verify, report.

The order is fixed and each step refuses to guess:

  parse      run every parser, showing everything they print
  categorise apply the rules file (or rules directory) to every row
  doctor     every preflight check, no writes; each finding is a Problem
  upsert     create what is missing, patch what differs, report orphans
  rollup     Transactions.Trip, Budgets actuals, account coverage, Payees.Recurring
  verify     read Notion back and confirm nothing is left to write
  report     one plain-text run report
  snapshot   today's analytics snapshot, written from the pages verify read, so
             that `finance.py analyse` reuses it instead of reading Notion again

`sync` writes nothing when doctor found a problem, so a bad statement, an
unknown card or a select value Notion does not have yet costs nothing but a
rerun.
"""
import csv
import os
import re
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from . import notion
from .money import fx_minor, rate_e9, to_cents
from .paths import REPO_RULES, SERVER
from .model import (ACCOUNT_ROLLUP_TYPES, BUDGET_TYPES, CATEGORY_TYPES, PAYEE_TYPES,
                    PERIOD_TYPES, SELECT_CHECKS, TRIP_TYPES, TXN_TYPES, Problem,
                    all_pdfs, canonicalise, diff, num, page_properties, period_properties,
                    txn_properties)

# G19: the parsers are the finance.parsers package. Each runs as its own
# process (`python3 -m finance.parsers.<name>`, the module its layouts name in
# server/finance/parsers/layouts.py) from the data folder, so the repository root goes
# on the child's PYTHONPATH, and -P keeps the data folder itself off its
# sys.path: nothing in there can shadow a module.
from .parsers import layouts

# How much bigger than the missing blocks the set of new rows may be before the
# sync assumes the keys have changed under it rather than that a statement was
# added. F1: txn_key hashes parser output, so a parser change can rewrite every
# key and create a second copy of every row.
INSERT_FACTOR = 3
INSERT_ALLOWANCE = 20


# ---------------------------------------------------------------- parse

# A card statement whose rows are all carried by an account statement too is
# dropped whole by parse_statements.py, which prints it as SKIP. Those PDFs
# produce neither rows nor a block on purpose, so doctor has to know about them.
SKIPPED_PDF = re.compile(r"^\s*SKIP\s*(\S+?)(?:\.pdf)?\s")

# The skip list is printed, not written to the CSVs, so a `--csv DIR` rerun would
# lose it and report every skipped card statement as pdf-not-parsed (F3). parse()
# saves it beside the CSVs under this name and reads it back.
SKIPPED_FILE = "skipped.txt"

# parse_statements.report_duplicate_periods prints this to stderr and then exits
# before writing a single CSV, so the whole run produced nothing (F1). Both
# parsers use it: parse_youtrip.py imports the same function.
#
#   error: 2 files parse to the same period 130-24680-5 · SGD · 2026-07:
#       data/dbs-statements/posb-everyday/2026/7 (1).pdf
#       data/dbs-statements/posb-everyday/2026/7.pdf
DUPLICATE_PERIOD = re.compile(
    r"^error: \d+ files parse to the same period (?P<name>.+):$"
    r"(?P<files>(?:\n[ \t]+\S.*)+)", re.M)

# The parsers print `warning: <source>: no transactions parsed` (F2). A file name
# can hold spaces ("7 (1).pdf"), so the source is taken up to the last colon of
# the line rather than as a run of non-space characters.
NO_ROWS = re.compile(r"^\s*warning: (?P<source>.+?): no transactions parsed\s*$", re.M)


@dataclass
class Parsed:
    """What the parsers produced, plus anything wrong with the run itself."""
    blocks: list = field(default_factory=list)
    rows: list = field(default_factory=list)
    problems: list = field(default_factory=list)
    skipped: set = field(default_factory=set)
    failed_roots: set = field(default_factory=set)  # folders whose parser exited non-zero
    rules: list = field(default_factory=list)      # what categorise() loaded
    csv_dir: Path = None

    @property
    def keys(self):
        return {r["txn_key"] for r in self.rows}

    def under_failed_parser(self, rel):
        """True when this PDF sat under a folder whose parser exited non-zero.

        A parser that stops writes no CSV at all, so every PDF it was pointed at
        produced nothing. Reporting each of them as pdf-not-parsed buries the one
        finding that matters, so they are suppressed and the parser's own problem
        is the whole story (F1).
        """
        return any(rel == root or rel.startswith(root.rstrip("/") + "/")
                   for root in self.failed_roots)


def parse(cfg, csv_dir=None, echo=True):
    """Run every parser (or read an earlier run's CSVs from `csv_dir`).

    Everything the parsers print is shown: their stdout is the only report of
    what each statement contained, and their stderr carries the warnings that
    used to be swallowed, including "no transactions parsed from X", which is a
    doctor problem rather than silence (F2).
    """
    out = Parsed()
    if csv_dir:
        out.csv_dir = Path(csv_dir)
        out.skipped = read_skipped(out.csv_dir)
    else:
        out.csv_dir = Path(tempfile.mkdtemp(prefix="finance_sync_"))
        # G21: one run per parser that produces rows, as the layouts it reads
        # declare them (server/finance/parsers/layouts.py), over its configured folders
        for parser, module, sub in layouts.runs():
            paths = [f.path for f in cfg.folders_for(parser)]
            if paths:
                (out.csv_dir / sub).mkdir(exist_ok=True)
                _run_parser(cfg, module, paths, out.csv_dir / sub, out, echo)
        write_skipped(out.csv_dir, out.skipped)
    out.blocks, out.rows = _read_csvs(out.csv_dir)
    # one statement is one block, except a multi-currency all-time export
    # (Revolut, Wise), which the parser splits into one block per calendar
    # month: there the row's own month picks the block
    by_src = defaultdict(list)
    for b in out.blocks:
        by_src[(b["account_no"], b["currency"], b["source"])].append(b)
    for r in out.rows:
        blocks = by_src.get((r["account_no"], r["currency"], r["source"]), [])
        if len(blocks) > 1:
            blocks = [b for b in blocks if b["period_end"][:7] == (r["date"] or "")[:7]]
        r["period"] = blocks[0]["period"] if blocks else ""
        if not r["period"]:
            out.problems.append(Problem("row-without-block", f"{r['txn_key']} {r['source']}",
                                        "the row's account, currency and source match no "
                                        "block in period_balances.csv; rerun the parser "
                                        "over the whole tree"))
    return out


def _run_parser(cfg, module, paths, out_dir, parsed, echo):
    """One parser subprocess (`module`, a layout's, run with -m), with its
    output shown and its warnings kept. A folder with no PDF yet (a new
    account, a wallet not used this year) is left out, so an empty folder never
    fails the run; nothing to parse at all runs nothing."""
    def empty(p):
        d = Path(cfg.root) / p
        return d.is_dir() and not any(not f.name.startswith(".") for f in d.rglob("*.pdf"))
    paths = [p for p in paths if not empty(p)]
    if not paths:
        return
    script = module.rsplit(".", 1)[-1] + ".py"      # what a problem calls it
    cmd = [sys.executable, "-P", "-m", module, *paths, "--out", str(out_dir)]
    if echo:
        print(f"$ python3 -m {' '.join(cmd[3:])}")
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(
        p for p in (str(SERVER), os.environ.get("PYTHONPATH", "")) if p)}
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(cfg.root), env=env)
    if echo:
        if r.stdout:
            print(r.stdout, end="" if r.stdout.endswith("\n") else "\n")
        if r.stderr:
            print(r.stderr, end="" if r.stderr.endswith("\n") else "\n", file=sys.stderr)
    for line in (r.stdout or "").splitlines():
        m = SKIPPED_PDF.match(line)
        if m:
            parsed.skipped.add(m.group(1) + ".pdf")
    for m in NO_ROWS.finditer(r.stderr or ""):
        parsed.problems.append(Problem(
            "no-rows-parsed", m.group("source").strip(),
            "the statement matched no known layout, or is in the wrong folder; "
            "open its .txt dump and check, or move the PDF out of the tree"))
    if r.returncode:
        parsed.failed_roots.update(str(x) for x in paths)
        dups = duplicate_periods_in(r.stderr)
        if dups:
            parsed.problems.append(Problem(
                "duplicate-period", duplicate_subject(dups),
                "two files parse to the same account-month, so the parser stopped "
                "before writing anything and nothing else in the run was checked. "
                "Move the extra PDF out of statements/ (into documents/superseded/, say) "
                "and run again; never delete a statement",
                paths=tuple(f for _, files in dups for f in files)))
        else:
            parsed.problems.append(Problem(
                "parser-failed", failing_subject(cfg, paths, r.stdout, r.stderr),
                f"{script} exited {r.returncode}. Nothing is written until it exits 0"))


# parse_statements.py marks a block that fails its balance check in stdout:
#   BAD  /data/statements/posb-everyday/2026/7 SGD  12 txn  open ...
BAD_BLOCK = re.compile(r"^\s*BAD\s+(?P<stem>\S.*?)\s+[A-Z]{3}\s+\d+ txn", re.M)


def failing_subject(cfg, paths, stdout, stderr):
    """Where a parser that exited non-zero failed: the statement files its output
    names (a block marked BAD, or a PDF path in the error), as paths inside the
    data folder, else the account folders it was given. Never the script's own
    name, which tells the owner nothing they can act on."""
    root = str(cfg.root).rstrip("/") + "/"

    def inside(path):
        path = path.strip()
        return path[len(root):] if path.startswith(root) else path

    found = [inside(m.group("stem")) + ".pdf" for m in BAD_BLOCK.finditer(stdout or "")]
    prefixes = [re.escape(root)] + [re.escape(str(p).rstrip("/") + "/") for p in paths]
    for m in re.finditer(rf"(?:{'|'.join(prefixes)})[^'\"\n]*?\.pdf", stderr or ""):
        found.append(inside(m.group(0)))
    return "; ".join(dict.fromkeys(found)) or "; ".join(str(p) for p in paths)


def duplicate_periods_in(stderr):
    """[(period name, [files])] read out of the parser's duplicate-period block."""
    out = []
    for m in DUPLICATE_PERIOD.finditer(stderr or ""):
        files = [line.strip() for line in m.group("files").splitlines() if line.strip()]
        out.append((m.group("name").strip(), files))
    return out


def duplicate_subject(dups):
    """The files that collided, named, because deleting one of them is the fix."""
    return "; ".join(f"{name}: {', '.join(files)}" for name, files in dups)


def read_skipped(csv_dir):
    """The card statements the parser dropped whole, saved by an earlier run."""
    path = Path(csv_dir) / SKIPPED_FILE
    if not path.exists():
        return set()
    with path.open(encoding="utf-8") as f:
        return {line.strip() for line in f if line.strip()}


def write_skipped(csv_dir, skipped):
    """Save the skip list beside the CSVs so `--csv DIR` can read it back."""
    d = Path(csv_dir)
    if not d.is_dir():
        return
    with (d / SKIPPED_FILE).open("w", encoding="utf-8") as f:
        for name in sorted(skipped):
            f.write(f"{name}\n")


def _read_csvs(d):
    """transactions.csv and period_balances.csv from each parser's CSV folder
    (the DBS parser's at the top, the others' in their own sub-folder)."""
    blocks, rows = [], []
    for sub in dict.fromkeys(csv_dir for _, _, csv_dir in layouts.runs()):
        base = d / sub if sub else d
        if (base / "transactions.csv").exists():
            with (base / "transactions.csv").open(newline="") as f:
                rows += list(csv.DictReader(f))
        if (base / "period_balances.csv").exists():
            with (base / "period_balances.csv").open(newline="") as f:
                blocks += list(csv.DictReader(f))
    return blocks, rows


def write_rules_input(cfg):
    """Parse the configured folders, the way a run does, into the data
    folder's cache: parsed/ (the CSVs `doctor --csv` reuses) and
    rules-input.csv (every row, for `categorise.py --lint` and `--why`)."""
    import shutil
    from .paths import cache_dir
    parsed = parse(cfg, echo=False)
    failed = [p for p in parsed.problems
              if p.code in ("parser-failed", "duplicate-period", "no-rows-parsed")]
    if failed or parsed.failed_roots:
        # a partial set of rows would make valid rules look unused; keep the
        # last complete cache and say what stopped
        shutil.rmtree(parsed.csv_dir, ignore_errors=True)
        lines = [p.line() for p in failed] or [f"a parser failed on {', '.join(sorted(parsed.failed_roots))}"]
        raise SystemExit("rules input not rebuilt, the last one is kept:\n" + "\n".join(lines))
    out = cache_dir()
    out.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(out / "parsed", ignore_errors=True)
    shutil.copytree(parsed.csv_dir, out / "parsed")
    shutil.rmtree(parsed.csv_dir, ignore_errors=True)
    # a keys.csv rule on a key a row had before matches it in --lint and --why
    # too: the old keys go in one column, space separated (categorise.tier_values)
    attach_former_keys(parsed.rows)
    for r in parsed.rows:
        if "_former_keys" in r:
            r["_former_keys"] = " ".join(r["_former_keys"])
    fields = list(dict.fromkeys(k for r in parsed.rows for k in r))
    with (out / "rules-input.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(parsed.rows)
    return out / "rules-input.csv"


# ---------------------------------------------------------------- categorise

def default_rules(cfg):
    """The rules a run reads: the shared tiers tracked in the repo's data/rules/,
    then the personal tiers (keys.csv, types.csv, people.csv, merchants.csv) in the data folder's rules/
    when it has one. categorise.load_rules takes the list as is."""
    personal = Path(cfg.root) / "rules"
    return [REPO_RULES] + ([personal] if any(personal.glob("*.csv")) else [])


def record_categories():
    """The shipped category names plus every category kopifund.db holds and
    every old name it maps (category_alias), when there is a record; the
    shipped names alone otherwise."""
    from .parsers import categorise as rules_module
    names = list(rules_module.KNOWN_CATEGORIES)
    try:
        from . import db
        if db.db_path().exists():
            conn = db.connect()
            try:
                names += [r[0] for r in conn.execute("SELECT name FROM category")]
                # a renamed or deleted category's old name still loads: the
                # record sends its rows to where the name now points
                names += db.category_alias_names(conn)
            finally:
                conn.close()
    except Exception:  # an unreadable record leaves the shipped list, never breaks a run
        pass
    return list(dict.fromkeys(names))


def categorise(parsed, rules_path, categories=None):
    """Add payee, category and rule to every row.

    A rule's category must be a shipped one or one the record holds
    (`record_categories`), so a category the user added is accepted.

    `rules_path` may be a file or a directory of CSVs; load_rules in
    server/finance/parsers/categorise.py owns both cases, so the path goes through untouched.
    A rules file that does not load is a doctor problem, not a crash: the rows
    keep an empty payee and category and the run stops before writing.
    """
    from .parsers import categorise as rules_module
    try:
        names = categories if categories is not None else record_categories()
        rules = rules_module.load_rules(rules_path, names)
    except (SystemExit, OSError) as e:
        for r in parsed.rows:
            r["payee"] = r["category"] = r["rule"] = ""
        parsed.problems.append(Problem("rules-invalid", rules_module._label(rules_path),
                                       f"the rules did not load: {e}"))
        return []
    attach_former_keys(parsed.rows)
    rules_module.categorise_rows(parsed.rows, rules)
    parsed.rules = rules
    return rules


def attach_former_keys(rows):
    """Give each row the keys the record loaded it under before
    (db.former_keys), as `_former_keys`, so a keys.csv rule saved on an old
    key still matches it, on the load that re-keys it too. Without a record,
    or with one that does not open, the rows carry none."""
    try:
        from . import db
        if not db.db_path().exists():
            return
        conn = db.connect()
        try:
            former = db.former_keys(conn, rows)
        finally:
            conn.close()
    except Exception:  # an unreadable record only means no old keys, never a broken run
        return
    for r in rows:
        if r.get("txn_key") in former:
            r["_former_keys"] = former[r["txn_key"]]


def check_pinned_rows(conn, parsed):
    """Audit: rows the owner saved something on that this load would archive.
    A trip pin, a "marked fine", a series edit, a matched phone row (G25) or a
    keys.csv rule names a row by its txn_key; a row the parser re-keys keeps them all (db.rekeys and
    txn_key_alias), so one that would still go has left the tree or changed
    in a way that cannot be paired. The Notion path's orphan check (check_orphans)
    needs a plan; this one reads the record, so a load with Notion off has it
    too. Also a key another row once had (an alias) that this run gives to a
    different row while the first stays: the load would hand what was saved on
    it to the wrong payment. `--archive-orphans` lets the CLI load past both."""
    from . import db
    from .parsers import categorise as rules_module
    keys = {r["txn_key"] for r in parsed.rows}
    moved = {tid: new for tid, _, new in db.rekeys(conn, parsed.rows)}
    live = {t["id"]: t["txn_key"] for t in conn.execute("SELECT id, txn_key FROM txn WHERE archived_at IS NULL")}
    leaving = {tid: k for tid, k in live.items() if k not in keys and tid not in moved}
    aliases = conn.execute("SELECT old_key, txn_id FROM txn_key_alias").fetchall()
    by_key = {k: tid for tid, k in leaving.items()}
    by_key.update({old: tid for old, tid in aliases if tid in leaving})
    # an alias the run produces for another row, while its own row stays live
    taken = {old: tid for old, tid in aliases
             if old in keys and tid in live and tid not in leaving and moved.get(tid, live[tid]) != old}
    if not by_key and not taken:
        return []
    saved = defaultdict(set)
    for what, sql in (("a trip pin", "SELECT txn_key FROM trip_pin"),
                      ("marked fine", "SELECT txn_key FROM txn_marked_fine"),
                      ("a series edit", "SELECT txn_key FROM series_override_txn"),
                      # G25: a phone row matched to it counts nowhere once the row goes
                      ("a phone match", "SELECT matched_txn_key FROM pending_txn "
                                        "WHERE state = 'matched' AND matched_txn_key IS NOT NULL")):
        for (k,) in conn.execute(sql):
            if k in by_key or k in taken:
                saved[k].add(what)
    key_rules = [r for r in (getattr(parsed, "rules", None) or []) if r["match_on"] == "key"]
    if key_rules:
        matcher = rules_module.Matcher(key_rules)
        for k in {*by_key, *taken}:
            if matcher.first("key", rules_module.norm(k)):
                saved[k].add("a key rule")
    problems = []
    gone = defaultdict(set)
    for k, what in saved.items():
        if k in by_key:
            gone[by_key[k]] |= what
    if gone:
        sample = ", ".join(f"{leaving[tid]} ({', '.join(sorted(gone[tid]))})" for tid in sorted(gone)[:5])
        problems.append(Problem("pinned-rows-gone", f"{len(gone)} row(s): {sample}",
                                "this load would archive rows you saved something on: their statement left the "
                                "tree or the parser now reads them differently. Put the statement back, or take "
                                "what you saved off the rows, then load again (the command line loads past it "
                                "with --archive-orphans)"))
    reused = sorted(k for k in saved if k in taken)
    if reused:
        sample = ", ".join(f"{k} ({', '.join(sorted(saved[k]))})" for k in reused[:5])
        problems.append(Problem("pinned-key-reused", f"{len(reused)} key(s): {sample}",
                                "the parser now gives a key you saved something on to a different row than the "
                                "one it was saved for, so this load would move it to the wrong payment. Take what "
                                "you saved off that key, then load again and save it on the right row (the "
                                "command line loads past it with --archive-orphans)"))
    return problems


def check_record_pins(parsed):
    """check_pinned_rows against the data folder's record, when there is one."""
    from . import db
    if not db.db_path().exists():
        return []
    conn = db.connect()
    try:
        return check_pinned_rows(conn, parsed)
    finally:
        conn.close()


# ---------------------------------------------------------------- Notion state

@dataclass
class State:
    """What Notion holds right now, read once per run."""
    periods: dict = field(default_factory=dict)        # name -> page
    txns: list = field(default_factory=list)           # pages
    payees: dict = field(default_factory=dict)         # name -> page id (no dashes)
    categories: dict = field(default_factory=dict)     # name -> page id (no dashes)
    category_need: dict = field(default_factory=dict)  # name -> Need class or None
    trips: list = field(default_factory=list)          # Trip, earliest first
    trip_pages: list = field(default_factory=list)     # the Trips Expenses pages
    plan_spans: dict = field(default_factory=dict)     # trip page id -> (start, end) from the planner
    options: dict = field(default_factory=dict)        # (ds, property) -> [names]
    pages: dict = field(default_factory=dict)          # data source name -> the pages read
    # G13: Categories rows the mirror will create (name -> NewCategory), each
    # standing in `categories` under a PENDING id until create_categories runs
    new_categories: dict = field(default_factory=dict)
    created_categories: list = field(default_factory=list)  # the NewCategory rows created
    category_schema: dict = field(default_factory=dict)  # Categories' properties, read when planning

    def period_ids(self):
        return {name: p["id"].replace("-", "") for name, p in self.periods.items()}


def read_state(cfg, client, with_options=True):
    """Read every page and option the sync compares against."""
    st = State()
    # the five reads are independent, so they share the pool; Transactions is
    # the big one and is itself split into date shards
    planner = cfg.data_sources.get("trip_plans")
    reads = [lambda: client.query_all(cfg.ds("period_balances")),
             lambda: read_transactions(cfg, client),
             lambda: client.query_all(cfg.ds("payees")),
             lambda: client.query_all(cfg.ds("categories")),
             lambda: client.query_all(cfg.ds("trips")),
             lambda: client.query_all(planner) if planner else []]
    periods, txns, payees, categories, trips, plans = client.map(lambda f: f(), reads)
    st.periods = {notion.plain(p, "Name"): p for p in periods}
    st.txns = txns
    st.payees = {notion.plain(p, "Name"): p["id"].replace("-", "") for p in payees}
    for p in categories:
        name = notion.plain(p, "Name")
        st.categories[name] = p["id"].replace("-", "")
        st.category_need[name] = notion.plain(p, "Need class")
    st.trip_pages = trips
    # the five lists as they came back, for the snapshot step: it flattens
    # Transactions, Period balances, Categories and Trips from here rather than
    # reading those four databases a second time
    st.pages = {"period_balances": periods, "transactions": txns, "payees": payees,
                "categories": categories, "trips": trips}
    st.plan_spans = plan_spans(trips, plans)
    st.trips = trips_from_pages(trips, warn=lambda m: print(f"  note: {m}", file=sys.stderr),
                                spans=st.plan_spans)
    if with_options:
        for ds_name, prop, _ in SELECT_CHECKS:
            st.options[(ds_name, prop)] = client.select_options(cfg.ds(ds_name), prop)
    return st


def read_transactions(cfg, client):
    """Every Transactions page, read in quarter shards on Txn date so the pages
    of different quarters download at the same time."""
    return client.query_sharded(cfg.ds("transactions"), "Txn date", notion.quarter_edges())


# ---------------------------------------------------------------- categories

# G13: the app owns the categories (SPEC.md, "The app owns what the user
# types"), so one added there, Transport (public) say, reaches Notion through
# the mirror: a sync creates the Categories row its rows need when the record
# holds the category. A name the record does not hold is still missing-category,
# most likely a typo in a rule.

# The stand-in page id of a category planned but not created yet. It lets the
# plan, doctor and a dry run's rollup treat the row as there; create_categories
# swaps it for the real id before anything is written that links to it.
PENDING = "pending:"

# record column -> Categories property
CATEGORY_FIELDS = (("bucket", "Bucket"), ("grp", "Group"), ("need_class", "Need class"),
                   ("sort_order", "Sort order"))


@dataclass
class NewCategory:
    """One Categories row the mirror will create."""
    name: str
    properties: dict                                   # canonical, CATEGORY_TYPES
    left_out: list = field(default_factory=list)       # why a field the record has is not sent


def record_category_fields(named=()):
    """{name: {record column: value}} for every category the record holds once
    the next load has run: what kopifund.db holds now, plus what
    db.seed_categories adds before each load (every shipped category, and a
    retired one such as Transport while `named` names it), with a blank field
    filled from the shipped list the same way. A name the user renamed or
    deleted (category_alias) is not among them: its rows go to the Notion row
    of that old name, which the mirror does not create.

    The record is opened only to read it, and an unreadable one counts as
    holding nothing of its own, so the shipped categories alone."""
    from . import db
    held, gone = {}, set()
    try:
        if db.db_path().exists():
            conn = db.connect()
            try:
                held = {r["name"]: {k: r[k] for k, _ in CATEGORY_FIELDS} for r in conn.execute(
                    "SELECT name, bucket, grp, need_class, sort_order FROM category")}
                gone = set(db.category_alias_names(conn))
            finally:
                conn.close()
    except Exception:  # like record_categories: never breaks a run
        held, gone = {}, set()
    for old, new in db.RENAMED_CATEGORIES.items():
        # the shipped rename seed_categories applies first
        if old in held and new not in held and not {old, new} & gone:
            held[new] = held.pop(old)
    named = set(named)
    shipped = db.shipped_categories() + [c for c in db.retired_categories()
                                         if c["name"] in named]
    for c in shipped:
        if c["name"] in gone:
            continue
        have = held.setdefault(c["name"], {k: None for k, _ in CATEGORY_FIELDS})
        for column, _ in CATEGORY_FIELDS:
            if have.get(column) is None:
                have[column] = c.get("group" if column == "grp" else column)
    return held


def category_properties(name, fields, schema):
    """(canonical properties, notes) for one new Categories row. A field goes
    only to a property Categories has with the type the mirror expects, and a
    select value only when it is one of that property's options: the mirror
    adds no property and no option to the owner's database."""
    props, left_out = {"Name": name}, []
    for column, prop in CATEGORY_FIELDS:
        value = fields.get(column)
        if value in (None, ""):
            continue
        kind = CATEGORY_TYPES[prop]
        have = schema.get(prop)
        if not have or have.get("type") != kind:
            left_out.append(f"{prop} {value!r} not set: Categories has no {kind} property {prop!r}")
            continue
        if kind == "select":
            options = [o.get("name") for o in (have.get("select") or {}).get("options", [])]
            if value not in options:
                left_out.append(f"{prop} {value!r} not set: it is not an option on Categories")
                continue
        props[prop] = value
    types = {k: CATEGORY_TYPES[k] for k in props}
    return canonicalise(props, types), left_out


def plan_categories(cfg, client, state, names, held=None):
    """Plan the Categories rows Notion lacks for `names` (the categories a run's
    rows, or the payees' defaults, link to) that the record holds. Each one
    stands in `state.categories` under a PENDING id, with the need class it
    will carry in `state.category_need`, so the plan, doctor and a dry run see
    it as there. A name the record does not hold is left out, and doctor
    reports it as missing-category. Returns the planned names.

    `held` is record_category_fields() and defaults to it; the record is read
    only when Notion lacks something."""
    missing = sorted({n for n in names if n} - set(state.categories))
    if not missing:
        return []
    held = record_category_fields(missing) if held is None else held
    new = [n for n in missing if n in held]
    if not new:
        return []
    state.category_schema = (client.schema(cfg.ds("categories")) or {}).get("properties", {})
    for name in new:
        props, left_out = category_properties(name, held[name], state.category_schema)
        state.new_categories[name] = NewCategory(name, props, left_out)
        state.categories[name] = PENDING + name
        state.category_need[name] = props.get("Need class")
    return new


def category_lines(state):
    """What the run would do, or did, to Categories: one line naming the rows,
    plus one per field a new row goes without. Nothing when there is none."""
    if state is None:
        return []
    if state.new_categories:
        verb, items = "would create", [state.new_categories[n] for n in sorted(state.new_categories)]
    elif state.created_categories:
        verb, items = "created", state.created_categories
    else:
        return []
    out = [f"{verb} {len(items)} {'category' if len(items) == 1 else 'categories'} "
           f"in Notion: {', '.join(c.name for c in items)}"]
    out += [f"  {c.name}: {note}" for c in items for note in c.left_out]
    return out


def create_categories(cfg, client, state, dry_run=False, echo=True, held=None):
    """Create the Categories rows plan_categories planned, before any row or
    budget links to them, and swap each PENDING id for the new page's. A dry run
    writes nothing and leaves them planned. Returns the names created (or, on a
    dry run, the names that would be).

    The fields are read from the record again first (`held` defaults to
    record_category_fields): the load that ran since the plan may have changed
    them, putting a shipped category new to the record last in its bucket, and
    Notion should get what the record holds now."""
    items = [state.new_categories[n] for n in sorted(state.new_categories)]
    if not items or dry_run:
        return [c.name for c in items]
    held = record_category_fields([c.name for c in items]) if held is None else held
    for c in items:
        if c.name in held:
            c.properties, c.left_out = category_properties(c.name, held[c.name],
                                                           state.category_schema)
            state.category_need[c.name] = c.properties.get("Need class")
    pages = client.map(lambda c: client.create(
        cfg.ds("categories"),
        notion.to_api(c.properties, {k: CATEGORY_TYPES[k] for k in c.properties})), items)
    for c, page in zip(items, pages):
        state.categories[c.name] = page["id"].replace("-", "")
        state.pages.setdefault("categories", []).append(page)
        if echo:
            print(f"  + category {c.name}")
            for note in c.left_out:
                print(f"      {note}")
    state.created_categories = items
    state.new_categories = {}
    return [c.name for c in items]


def no_pending_categories(state, step):
    """Refuse to write while a PENDING id stands in for a category: a link to it
    would point at no page."""
    if state is not None and state.new_categories:
        raise RuntimeError(f"{step}: the Categories rows "
                           f"{', '.join(sorted(state.new_categories))} are not created yet; "
                           f"call create_categories first")


# ---------------------------------------------------------------- trips


# Days after a trip ends during which a row is still the trip's: card rows post
# one to three days after the purchase, and the wallet statements print no
# purchase date to fall back on.
SETTLEMENT_DAYS = 3

# The Trips Database names countries in full; the parsers speak ISO alpha-3.
COUNTRY_ISO = {
    "Australia": "AUS", "Austria": "AUT", "Belgium": "BEL", "Cambodia": "KHM",
    "China": "CHN", "Croatia": "HRV", "Czechia": "CZE", "Czech Republic": "CZE",
    "Denmark": "DNK", "Estonia": "EST", "Finland": "FIN", "France": "FRA",
    "Germany": "DEU", "Greece": "GRC", "Hong Kong": "HKG", "Hungary": "HUN",
    "Iceland": "ISL", "India": "IND", "Indonesia": "IDN", "Ireland": "IRL",
    "Italy": "ITA", "Japan": "JPN", "Laos": "LAO", "Latvia": "LVA",
    "Lithuania": "LTU", "Malaysia": "MYS", "Netherlands": "NLD", "New Zealand": "NZL",
    "Norway": "NOR", "Philippines": "PHL", "Poland": "POL", "Portugal": "PRT",
    "Singapore": "SGP", "South Korea": "KOR", "Spain": "ESP", "Sweden": "SWE",
    "Switzerland": "CHE", "Taiwan": "TWN", "Thailand": "THA", "Turkey": "TUR",
    "Türkiye": "TUR", "United Arab Emirates": "ARE", "United Kingdom": "GBR",
    "United States": "USA", "Vietnam": "VNM"}


def country_codes(names, warn=None):
    """Country names (or codes already) as ISO alpha-3, in order, without
    repeats. A name the table does not know is dropped and reported through
    `warn`, so the trip still claims rows on the other evidence."""
    out = []
    for name in names or ():
        code = COUNTRY_ISO.get(name, name if len(name) == 3 and name.isupper() else None)
        if code is None:
            if warn:
                warn(name)
            continue
        if code not in out:
            out.append(code)
    return tuple(out)


@dataclass(frozen=True)
class Trip:
    """One Trips Expenses row, reduced to what the assignment rule needs."""
    page_id: str
    name: str
    start: str                       # yyyy-mm-dd, inclusive
    end: str                         # yyyy-mm-dd, inclusive
    countries: tuple = ()            # ISO alpha-3
    keys: frozenset = frozenset()    # txn keys pinned to the trip whatever their date

    @property
    def days(self):
        """Length in days, used to prefer the narrower of two overlapping trips."""
        return (date.fromisoformat(self.end) - date.fromisoformat(self.start)).days

    @property
    def settled_end(self):
        """The last day a row can post and still be this trip's."""
        return (date.fromisoformat(self.end) + timedelta(days=SETTLEMENT_DAYS)).isoformat()

    def covers(self, day):
        return bool(day) and self.start <= day <= self.settled_end


def read_trips(cfg, client):
    """Every Trips row with a start date, earliest first."""
    return trips_from_pages(client.query_all(cfg.ds("trips")))


def plan_spans(trip_pages, plan_pages):
    """{Trips Expenses page id: (start, end)} from the planner rows each
    Trip plan points at: earliest start to latest end, a planner day trip
    (no end) counting as one day. A trip whose plan rows have no dates, or
    that has no plan, is absent."""
    plans = {p["id"].replace("-", ""): p for p in plan_pages}
    out = {}
    for page in trip_pages:
        starts, ends = [], []
        for pid in notion.plain(page, "Trip plan") or ():
            span = notion.plain_range(plans[pid], "Dates") if pid in plans else None
            if span and span[0]:
                starts.append(span[0][:10])
                ends.append((span[1] or span[0])[:10])
        if starts:
            out[page["id"].replace("-", "")] = (min(starts), max(ends))
    return out


def trips_from_pages(pages, warn=None, spans=None):
    """The Trip rows among a Trips Expenses read. Countries roll up from the
    Trip plan relation and Dates is copied from the planner by the rollup;
    `spans` (plan_spans) is what the planner says now, used ahead of the copy
    so that one run reads and writes consistently. A row with no dates from
    either cannot claim a transaction and is skipped rather than reported: a
    draft is a normal thing to have in the database."""
    out = []
    unknown = set()
    for page in pages:
        span = ((spans or {}).get(page["id"].replace("-", ""))
                or notion.plain_range(page, "Dates") or (None, None))
        start = (span[0] or "")[:10]
        if not start:
            continue
        out.append(Trip(page_id=page["id"].replace("-", ""),
                        name=notion.plain(page, "Name") or "",
                        start=start, end=(span[1] or span[0])[:10],
                        countries=country_codes(notion.plain(page, "Countries"), unknown.add),
                        keys=extra_keys(notion.plain(page, "Extra keys"))))
    if unknown and warn:
        warn("trip countries not in COUNTRY_ISO, ignored: " + ", ".join(sorted(unknown)))
    return sorted(out, key=lambda t: (t.start, t.end, t.name))


def extra_keys(text):
    """The txn keys typed into a trip's Extra keys, separated by commas,
    spaces or newlines."""
    return frozenset(k for k in re.split(r"[\s,;]+", text or "") if k)


def trip_for(row, trips, wallet_accounts):
    """The Trip a parsed row belongs to, or None.

    A row is on a trip when its Txn date falls inside the trip's dates and
    something about the row says it was spent away from Singapore:

      * the merchant country is a country other than SGP, or
      * the row is on a non-SGD wallet of a per-currency account and does not
        name Singapore itself (a foreign-currency wallet is normally only spent
        abroad; leftover foreign balances can also be spent at home after a
        trip, and those rows print Singapore as the country), or
      * the trip's Countries list the row's own country, which is how a trip
        inside Singapore or a day trip home claims its rows.

    A row with no merchant country at all is left alone: an empty country is
    missing evidence, not evidence of being abroad, and a months-long stay
    abroad would otherwise swallow every salary and rent row it spans.
    Only External, non-synthetic rows are ever linked: wallet exchanges,
    top-ups and balance-jump fillers are not trip spending, and linking them
    made every trip's Spend rollup net to zero.

    Trips nest (short side trips can sit inside a long stay abroad), so when
    several trips cover the day the one that names the row's country wins,
    then the shorter trip, then the earlier one.

    A key listed in a trip's Extra keys belongs to that trip whatever its date:
    that is how a booking paid weeks ahead (an event ticket, a flight) is
    counted with the trip it was for. At the other end a trip keeps claiming
    for SETTLEMENT_DAYS after it ends, because the dates come from the trip
    planner (travel days) and card rows post a day or three later.
    """
    if row.get("flow_class") != "External" or row.get("bank_txn_type") == "YouTrip Unprinted":
        return None
    for trip in trips:
        if row["txn_key"] in trip.keys:
            return trip
    day = (row.get("date") or "")[:10]
    country = (row.get("merchant_country") or "").strip()
    abroad = bool(country) and country != "SGP"
    foreign_wallet = (row.get("account_no") in wallet_accounts
                      and row.get("currency") != "SGD" and country != "SGP")
    best = best_rank = None
    for trip in trips:
        if not trip.covers(day):
            continue
        named = bool(country) and country in trip.countries
        if not (abroad or foreign_wallet or named):
            continue
        rank = (0 if named else 1, trip.days, trip.start)
        if best_rank is None or rank < best_rank:
            best, best_rank = trip, rank
    return best


def trip_targets(cfg, rows, trips):
    """{txn_key: Trip or None} for every parsed row."""
    wallets = cfg.wallet_accounts()
    return {r["txn_key"]: trip_for(r, trips, wallets) for r in rows}


# ---------------------------------------------------------------- plan

@dataclass
class Plan:
    """What the upsert would do. Empty everywhere means Notion already matches."""
    create_periods: list = field(default_factory=list)     # (block, props)
    patch_periods: list = field(default_factory=list)      # (page, name, props, changed)
    same_periods: int = 0
    create_txns: list = field(default_factory=list)        # (row, props)
    patch_txns: list = field(default_factory=list)         # (page, row, props, changed)
    same_txns: int = 0
    orphan_txns: list = field(default_factory=list)        # pages
    orphan_periods: list = field(default_factory=list)     # pages

    @property
    def writes(self):
        return (len(self.create_periods) + len(self.patch_periods)
                + len(self.create_txns) + len(self.patch_txns))


def rule_targets(rows, payees, categories):
    """{txn_key: (payee ids, category ids)} the rules ask for. A name Notion
    does not have maps to nothing here; doctor reports it separately."""
    out = {}
    for r in rows:
        p = payees.get(r.get("payee"))
        c = categories.get(r.get("category"))
        out[r["txn_key"]] = ([p] if p else [], [c] if c else [])
    return out


def build_plan(cfg, parsed, state):
    """Compare every loader-owned property of every block and every row."""
    plan = Plan()
    period_ids = state.period_ids()
    for b in parsed.blocks:
        want = period_properties(cfg, b)
        page = state.periods.get(b["period"])
        if not page:
            plan.create_periods.append((b, want))
            continue
        have = page_properties(page, PERIOD_TYPES)
        changed = diff(want, have)
        if changed:
            plan.patch_periods.append((page, b["period"], want, changed))
        else:
            plan.same_periods += 1
    want_periods = {b["period"] for b in parsed.blocks}
    plan.orphan_periods = [p for name, p in state.periods.items() if name not in want_periods]

    targets = rule_targets(parsed.rows, state.payees, state.categories)
    trips = trip_targets(cfg, parsed.rows, state.trips)
    fx = {b["period"]: b.get("fx_to_sgd") for b in parsed.blocks}
    by_key = {}
    for p in state.txns:
        by_key.setdefault(notion.plain(p, "Txn key"), p)
    for r in parsed.rows:
        payee_ids, category_ids = targets[r["txn_key"]]
        trip = trips[r["txn_key"]]
        pid = period_ids.get(r["period"])
        want = txn_properties(cfg, r, pid or "", payee_ids, category_ids,
                              [trip.page_id] if trip else [], fx.get(r["period"]))
        page = by_key.get(r["txn_key"])
        if not page:
            plan.create_txns.append((r, want))
            continue
        have = page_properties(page, TXN_TYPES)
        if pid is None:
            want["Period"] = have["Period"]      # the period page does not exist yet
        changed = diff(want, have)
        if changed:
            plan.patch_txns.append((page, r, want, changed))
        else:
            plan.same_txns += 1
    keys = parsed.keys
    plan.orphan_txns = [p for p in state.txns if notion.plain(p, "Txn key") not in keys]
    return plan


# ---------------------------------------------------------------- doctor

def doctor(cfg, parsed, state, plan, force=False, archive_orphans=False):
    """Every preflight check. Returns a list of Problem; an empty list means the
    upsert may write."""
    problems = list(parsed.problems)
    problems += check_environment(cfg)
    problems += check_statements(cfg, parsed)
    problems += check_statement_files(cfg)
    problems += check_blocks(parsed)
    problems += check_config_coverage(cfg, parsed)
    if state is not None:
        problems += check_options(cfg, parsed, state)
        problems += check_relations(parsed, state)
    if plan is not None:
        problems += check_insert_size(parsed, state, plan, force)
        if not archive_orphans:
            problems += check_orphans(plan)
    if not archive_orphans:
        problems += check_record_pins(parsed)
    return one_problem_per_copy(problems)


def check_environment(cfg):
    """pdftotext on PATH, and every configured folder present."""
    import shutil
    out = []
    if not shutil.which("pdftotext"):
        out.append(Problem("pdftotext-missing", "PATH",
                           "brew install poppler (the parsers shell out to pdftotext)"))
    for f in cfg.folders:
        if not (cfg.root / f.path).is_dir():
            out.append(Problem("folder-missing", f.path,
                               "create the folder or remove its [[folder]] entry from "
                               "config.toml in the data folder"))
    return out


def check_statements(cfg, parsed):
    """Every PDF under a configured folder produced rows, or is an explicit
    empty statement (a block with no transactions and a note saying so)."""
    out = []
    # a multi-currency export is one PDF parsed as several sections, each
    # named "<pdf>#<currency>"; the PDF counts as parsed when any section did
    sources = {r["source"].split("#")[0] for r in parsed.rows} | {b["source"].split("#")[0] for b in parsed.blocks}
    for f in cfg.folders:
        root = cfg.root / f.path
        if not root.is_dir():
            continue
        for pdf in f.pdfs(cfg.root):
            rel = str(pdf.relative_to(cfg.root))
            if (rel not in sources and rel not in parsed.skipped
                    and not parsed.under_failed_parser(rel)):
                out.append(Problem("pdf-not-parsed", rel,
                                   "no rows and no block came out of this PDF; check the "
                                   "layout, or move it out of the statement tree"))
            if f.per_currency_subfolders:
                wallet = pdf.relative_to(root).parts[0]
                if wallet not in f.per_currency_subfolders:
                    out.append(Problem("unknown-wallet-folder", rel,
                                       f"{wallet!r} is not one of "
                                       f"{', '.join(f.per_currency_subfolders)}; add it to "
                                       f"per_currency_subfolders in the data folder's config.toml"))
    return out


def check_statement_files(cfg, recorded=None):
    """G13: the same bytes must not sit under two names, and a statement must not
    have changed account folders since the record last saw it. The record keeps
    one row per file content (statement_file.sha256 is unique), so either would
    otherwise stop a load half way with a database error. `recorded` is
    {path: sha256} as the record holds it; None reads it from the record."""
    import hashlib
    from . import db
    if recorded is None:
        recorded = db.recorded_statement_files()
    on_disk = {}
    for pdf in all_pdfs(cfg):
        rel = str(pdf.relative_to(cfg.root))
        on_disk[rel] = hashlib.sha256(pdf.read_bytes()).hexdigest()
    by_sha = defaultdict(list)
    for rel, sha in sorted(on_disk.items()):
        by_sha[sha].append(rel)
    where = {sha: path for path, sha in recorded.items()}
    folders = sorted((f.path for f in cfg.folders), key=len, reverse=True)

    def folder_of(rel):
        return next((f for f in folders if rel == f or rel.startswith(f + "/")), None)

    out = []
    for sha, paths in sorted(by_sha.items(), key=lambda kv: kv[1]):
        if len(paths) > 1:
            names = ", ".join(paths[:-1]) + " and " + paths[-1]
            out.append(Problem("same-file-two-places", names,
                               "these paths hold the same file. Move the extra copy out of "
                               "statements/ (into documents/superseded/, say); never delete a statement",
                               paths=tuple(paths)))
            continue
        here, was = paths[0], where.get(sha)
        if was is None or was == here:
            continue
        # a rename inside one account folder keeps its rows; a move to another
        # account's folder would hand them to that account
        if folder_of(was) is not None and folder_of(here) is not None and folder_of(was) != folder_of(here):
            out.append(Problem("statement-moved", here,
                               f"the record has this file at {was}, in another account's folder. "
                               f"Move it back to {was}"))
    return out


def one_problem_per_copy(problems):
    """G13: a byte-identical copy of a statement also parses to the same month,
    so it raised duplicate-period beside same-file-two-places: two cards for one
    mistake. A duplicate-period whose files are all, by exact root-relative path,
    among the reported copies is left out; one naming any other file, in this
    account or another, stays. Only the problems' `paths` are compared, never
    their display text."""
    copies = {path for p in problems if p.code == "same-file-two-places" for path in p.paths}
    if not copies:
        return problems
    return [p for p in problems
            if not (p.code == "duplicate-period" and p.paths and set(p.paths) <= copies)]


def check_blocks(parsed):
    """One period name per block, one page per key, and every block either
    reconciles or says in its note why it carries no closing balance (F10)."""
    out = []
    seen = defaultdict(list)
    for b in parsed.blocks:
        seen[b["period"]].append(b["source"])
    for name, sources in sorted(seen.items()):
        if len(sources) > 1:
            out.append(Problem("duplicate-period", name,
                               "two statements produced the same period: "
                               + ", ".join(sources) + ". Move the duplicate PDF out of statements/ "
                               "(into documents/superseded/, say); never delete a statement",
                               paths=tuple(sources)))
    keys = defaultdict(list)
    for r in parsed.rows:
        keys[r["txn_key"]].append(r["source"])
    for key, sources in sorted(keys.items()):
        if len(sources) > 1:
            out.append(Problem("duplicate-key", key,
                               "two parsed rows share a txn_key: " + ", ".join(sources)))
    for b in parsed.blocks:
        if b.get("reconciled") not in ("True", "true", True):
            out.append(Problem("block-not-reconciled", b["period"],
                               f"the balance chain in {b['source']} does not close; the "
                               f"parser output above says where"))
        if not b.get("closing") and not (b.get("note") or "").strip():
            out.append(Problem("balance-unexplained", b["period"],
                               f"{b['source']} prints no closing balance and the block "
                               f"carries no note saying why"))
    return out


def check_config_coverage(cfg, parsed):
    """Every parsed account number and card has a page in the config (F8)."""
    out = []
    for acc in sorted({r["account_no"] for r in parsed.rows}
                      | {b["account_no"] for b in parsed.blocks}):
        if acc and acc not in cfg.accounts:
            out.append(Problem("unknown-account", acc,
                               "create the Accounts row in Notion and add its page id to "
                               "[accounts] in the data folder's config.toml"))
    for card in sorted({r["card_last4"] for r in parsed.rows if r["card_last4"]}):
        if card not in cfg.cards:
            out.append(Problem("unknown-card", card,
                               "create the Cards row in Notion and add its page id to "
                               "[cards] in the data folder's config.toml"))
    return out


def check_options(cfg, parsed, state):
    """Every parsed select value already exists as an option in Notion."""
    out = []
    for ds_name, prop, column in SELECT_CHECKS:
        have = set(state.options.get((ds_name, prop), ()))
        rows = parsed.blocks if ds_name == "period_balances" else parsed.rows
        for value in sorted({r.get(column) for r in rows if r.get(column)}):
            if value not in have:
                where = "Period balances " if ds_name == "period_balances" else ""
                out.append(Problem("unknown-option", f"{where}{prop} = {value!r}",
                                   "add the option first: python3 server/finance.py add-options"))
    return out


def check_relations(parsed, state):
    """Every payee and category the rules name exists in Notion. A category
    plan_categories planned counts as there: the sync creates it first."""
    out = []
    for name in sorted({r.get("payee") for r in parsed.rows if r.get("payee")}):
        if name not in state.payees:
            out.append(Problem("missing-payee", name,
                               "run: python3 server/finance.py payees"))
    for name in sorted({r.get("category") for r in parsed.rows if r.get("category")}):
        if name not in state.categories:
            out.append(Problem("missing-category", name,
                               "create the Categories row in Notion (and check the spelling "
                               "in the rules)"))
    return out


def check_insert_size(parsed, state, plan, force):
    """Refuse an implausible number of new rows (F1). New rows should belong to
    the blocks Notion does not have yet; many more than that means the keys
    changed under us and the run would duplicate rows rather than add them."""
    if not plan.create_txns or force:
        return []
    missing = {b["period"]: int(b["n_txns"] or 0) for b, _ in plan.create_periods}
    budget = INSERT_FACTOR * sum(missing.values()) + INSERT_ALLOWANCE
    if len(plan.create_txns) <= budget:
        return []
    return [Problem("implausible-insert", f"{len(plan.create_txns)} new rows for "
                    f"{sum(missing.values())} rows in {len(missing)} new block(s)",
                    "a parser change can rewrite every txn_key, which would duplicate "
                    "rows rather than add them. Check a few of the new keys against "
                    "Notion, then rerun with --force if they really are new")]


def check_orphans(plan):
    """Keys and period names Notion holds that the parser no longer produces."""
    out = []
    if plan.orphan_txns:
        sample = ", ".join(notion.plain(p, "Txn key") or "(no key)"
                           for p in plan.orphan_txns[:5])
        out.append(Problem("orphan-rows", f"{len(plan.orphan_txns)} transaction(s): {sample}",
                           "the parser no longer produces these keys. Either the statement "
                           "left the tree or a key changed; rerun with --archive-orphans "
                           "once you have checked them"))
    if plan.orphan_periods:
        sample = ", ".join(notion.plain(p, "Name") or "(no name)"
                           for p in plan.orphan_periods[:5])
        out.append(Problem("orphan-blocks",
                           f"{len(plan.orphan_periods)} period page(s): {sample}",
                           "no parsed block carries these account-months. Either the "
                           "statement left the tree or its period name changed; rerun with "
                           "--archive-orphans once you have checked them"))
    return out


# ---------------------------------------------------------------- upsert

def upsert(cfg, client, parsed, state, plan, archive_orphans=False, echo=True):
    """Create what is missing and patch what differs. Returns the plan actually
    carried out (rebuilt for transactions once the new periods have ids)."""
    no_pending_categories(state, "upsert")
    period_ids = state.period_ids()
    for block, props in plan.create_periods:
        page = client.create(cfg.ds("period_balances"), notion.to_api(props, PERIOD_TYPES))
        state.periods[block["period"]] = page
        period_ids[block["period"]] = page["id"].replace("-", "")
        if echo:
            print(f"  + period {block['period']}")
    for page, name, props, changed in plan.patch_periods:
        client.patch(page["id"], notion.to_api({k: props[k] for k in changed}, PERIOD_TYPES))
        if echo:
            print(f"  ~ period {name}: {', '.join(changed)}")

    # the new period pages have ids now, so the transaction plan is rebuilt.
    # Rows are independent of each other, so each phase runs on the pool; the
    # phases themselves stay in order.
    fresh = build_plan(cfg, parsed, state)

    def progress(label):
        def show(done, total):
            if echo and (done % 100 == 0 or done == total):
                print(f"  {label} {done}/{total} transactions")
        return show

    client.map(lambda rp: client.create(cfg.ds("transactions"),
                                        notion.to_api(rp[1], TXN_TYPES)),
               fresh.create_txns, progress("+"))
    client.map(lambda t: client.patch(t[0]["id"],
                                      notion.to_api({k: t[2][k] for k in t[3]}, TXN_TYPES)),
               fresh.patch_txns, progress("~"))
    if archive_orphans:
        # transactions first: a Period page archived while its rows still point at
        # it would leave them without the relation verify insists on
        client.map(lambda page: client.archive(page["id"]), fresh.orphan_txns)
        if echo and fresh.orphan_txns:
            print(f"  - {len(fresh.orphan_txns)} orphan transactions archived")
        client.map(lambda page: client.archive(page["id"]), plan.orphan_periods)
        if echo and plan.orphan_periods:
            print(f"  - {len(plan.orphan_periods)} orphan period page(s) archived")
    fresh.orphan_periods = plan.orphan_periods
    return fresh


# ---------------------------------------------------------------- rollup


@dataclass
class Rollup:
    """What the rollup step did, counted per target."""
    trips: Counter = field(default_factory=Counter)        # assigned/cleared/unchanged
    trip_rows: Counter = field(default_factory=Counter)    # trip name -> rows on it
    trip_dates: Counter = field(default_factory=Counter)   # updated/unchanged/no plan
    budgets: Counter = field(default_factory=Counter)      # created/updated/unchanged
    accounts: Counter = field(default_factory=Counter)     # updated/unchanged
    payees: Counter = field(default_factory=Counter)       # set/cleared/unchanged
    dry_run: bool = False

    @property
    def writes(self):
        return (self.trips["assigned"] + self.trips["cleared"]
                + self.trip_dates["updated"]
                + self.budgets["created"] + self.budgets["updated"]
                + self.accounts["updated"]
                + self.payees["set"] + self.payees["cleared"])


def rollup(cfg, client, parsed, rules=(), state=None, dry_run=False, echo=True):
    """The analytics pass: everything derived from rows that are already in
    Notion. It runs after the upsert so that a row created this run gets its
    Trip and is counted in its month's budget, and before verify so that verify
    sees the finished state.

    Notion is read again here rather than reusing the plan's state: the upsert
    has just created pages, and every one of them needs a Trip.
    """
    if state is None:
        state = read_state(cfg, client, with_options=False)
    if not dry_run:
        no_pending_categories(state, "rollup")
    out = Rollup(dry_run=dry_run)
    out.trip_dates = rollup_trip_dates(cfg, client, state, dry_run, echo)
    out.trips, out.trip_rows = rollup_trips(cfg, client, parsed, state, dry_run, echo)
    out.budgets = rollup_budgets(cfg, client, parsed, state, dry_run, echo)
    out.accounts = rollup_accounts(cfg, client, parsed, dry_run, echo)
    out.payees = rollup_recurring(cfg, client, rules, dry_run, echo)
    return out


def rollup_trip_dates(cfg, client, state, dry_run=False, echo=True):
    """Trips Expenses.Dates from the planner rows its Trip plan names, when
    config names a planner. A trip with no plan keeps whatever is typed."""
    counts = Counter()
    if not cfg.data_sources.get("trip_plans"):
        return counts
    patches = []
    for page in state.trip_pages:
        span = state.plan_spans.get(page["id"].replace("-", ""))
        if span is None:
            counts["no plan"] += 1
            continue
        have = notion.plain_range(page, "Dates") or (None, None)
        if ((have[0] or "")[:10], (have[1] or have[0] or "")[:10]) == span:
            counts["unchanged"] += 1
            continue
        counts["updated"] += 1
        patches.append((page, notion.to_api({"Dates": span}, TRIP_TYPES)))
    if not dry_run:
        client.map(lambda pr: client.patch(pr[0]["id"], pr[1]), patches)
        # the state goes on to the snapshot step, so the pages carry what was
        # just written rather than what was read before it
        for page, props in patches:
            notion.apply_patch(page, props)
    if echo and counts["updated"]:
        print(f"  trip dates: {counts['updated']} copied from the planner")
    return counts


def rollup_trips(cfg, client, parsed, state, dry_run=False, echo=True):
    """Set Transactions.Trip from the Trips rows, and clear it on a row that is
    on no trip. Returns (counts, rows per trip)."""
    want = trip_targets(cfg, parsed.rows, state.trips)
    counts, per_trip = Counter(), Counter()
    by_key = {}
    for page in state.txns:
        by_key.setdefault(notion.plain(page, "Txn key"), page)
    patches = []
    for key, trip in want.items():
        if trip:
            per_trip[trip.name] += 1
        page = by_key.get(key)
        if page is None:
            counts["not in Notion"] += 1
            continue
        target = (trip.page_id,) if trip else ()
        if tuple(sorted(notion.plain(page, "Trip") or ())) == target:
            counts["unchanged"] += 1
            continue
        counts["assigned" if trip else "cleared"] += 1
        patches.append((page, notion.to_api({"Trip": list(target)}, TXN_TYPES)))
    if not dry_run:
        client.map(lambda pr: client.patch(pr[0]["id"], pr[1]), patches)
        for page, props in patches:
            notion.apply_patch(page, props)
    if echo and (counts["assigned"] or counts["cleared"]):
        print(f"  trips: {counts['assigned']} assigned, {counts['cleared']} cleared")
    return counts, per_trip


def sgd_minor(r, rate):
    """A parsed row's amount in SGD cents, by the record's rule (v_txn): an SGD
    row is its own amount, a foreign row is its amount at the block's FX rate,
    and a foreign row whose block has no rate has no SGD value (None). SPEC: "A
    block with no FX rate has no SGD value"; counting it one to one would add
    NOK or SEK to an SGD total as if it were dollars."""
    amount = to_cents(r["amount"] or 0)
    if rate:
        return fx_minor(amount, rate_e9(rate))
    return amount if r.get("currency") == "SGD" else None


def unpriced_rows(parsed):
    """The External, categorised rows budget_actuals leaves out because their
    block has no FX rate to SGD: what a run says it could not count."""
    fx = {b["period"]: num(b.get("fx_to_sgd")) for b in parsed.blocks}
    return [r for r in parsed.rows if r.get("flow_class") == "External" and r.get("category")
            and sgd_minor(r, fx.get(r.get("period"))) is None]


def budget_actuals(parsed, income_categories):
    """{(category, month): Actual} for every category and month with External rows.

    Actual is the sum of the rows' SGD values (sgd_minor), counted in cents: a
    row at its block's FX rate, an SGD row as it is, and a foreign row whose
    block prints no rate not at all (unpriced_rows lists those; a category
    and month with only those still gets its key, at 0). The sign is
    flipped so that Planned and Actual compare like for like: on a spending
    category Actual is positive when money went out, and on an income category
    (Need class = Income) it is positive when money came in. Returned in
    dollars, the unit Notion's number property holds.
    """
    fx = {b["period"]: num(b.get("fx_to_sgd")) for b in parsed.blocks}
    totals = defaultdict(int)
    for r in parsed.rows:
        if r.get("flow_class") != "External" or not r.get("category"):
            continue
        sgd = sgd_minor(r, fx.get(r.get("period")))
        key = (r["category"], (r["date"] or "")[:7])
        if sgd is None:
            # the month stays, at zero: an Actual an older run counted one to
            # one is then corrected rather than left as it was
            totals[key] += 0
            continue
        totals[key] += (
            sgd if r["category"] in income_categories else -sgd)
    return {k: v / 100 for k, v in totals.items()}


def rollup_budgets(cfg, client, parsed, state, dry_run=False, echo=True, today=None):
    """One Budgets row per (category, month) with External rows: create it when
    it is missing and refresh Actual when it moved. Planned is not in
    BUDGET_TYPES, so no code path here can write or clear it. `Current` is
    ticked on the rows of the month the run happens in (a seeded month ahead
    included) and cleared everywhere else, so the hub view can filter on it."""
    this_month = (today or date.today()).strftime("%Y-%m")
    income = {name for name, kind in state.category_need.items() if kind == "Income"}
    actuals = budget_actuals(parsed, income)
    have = {notion.plain(p, "Name"): p for p in client.query_all(cfg.ds("budgets"))}
    counts = Counter()
    writes = []                       # (page or None, API-shaped properties)
    for (category, month), actual in sorted(actuals.items()):
        page_id = state.categories.get(category)
        if not page_id:
            counts["no category page"] += 1
            continue
        name = f"{category} · {month}"
        want = canonicalise({"Name": name, "Category": [page_id], "Month": month,
                             "Need class": state.category_need.get(category),
                             "Actual": actual, "Current": month == this_month}, BUDGET_TYPES)
        page = have.get(name)
        if page is None:
            counts["created"] += 1
            writes.append((None, notion.to_api(want, BUDGET_TYPES)))
            continue
        changed = diff(want, page_properties(page, BUDGET_TYPES))
        if not changed:
            counts["unchanged"] += 1
            continue
        counts["updated"] += 1
        writes.append((page, notion.to_api({k: want[k] for k in changed}, BUDGET_TYPES)))
    # rows with no actuals this run (a seeded month ahead, a month whose
    # statements left the tree) still need Current kept right
    stale = set(have) - {f"{c} · {m}" for c, m in actuals}
    for name in sorted(stale):
        page = have[name]
        is_current = (notion.plain(page, "Month") or "") == this_month
        if bool(notion.plain(page, "Current")) != is_current:
            counts["current flag"] += 1
            writes.append((page, notion.to_api({"Current": is_current}, BUDGET_TYPES)))
    if not dry_run:
        client.map(lambda w: client.patch(w[0]["id"], w[1]) if w[0]
                   else client.create(cfg.ds("budgets"), w[1]), writes)
    if stale:
        counts["no rows left"] = len(stale)
    if unpriced := unpriced_rows(parsed):
        counts["no SGD value"] = len(unpriced)
        if echo:
            print(f"  budgets: {len(unpriced)} row(s) sit in a block with no FX rate to SGD, so "
                  "their SGD value is unknown and Actual leaves them out")
    if echo and (counts["created"] or counts["updated"]):
        print(f"  budgets: {counts['created']} created, {counts['updated']} refreshed")
    return counts


def rollup_accounts(cfg, client, parsed, dry_run=False, echo=True):
    """Statements from, Statements to and Data coverage on every account the
    config names. An account in Notion with no config entry is a placeholder
    for a statement source that is not loaded yet and is left untouched."""
    ends = defaultdict(list)
    for b in parsed.blocks:
        if b.get("period_end"):
            ends[b["account_no"]].append(b["period_end"])
    pages = {p["id"].replace("-", ""): p for p in client.query_all(cfg.ds("accounts"))}
    counts = Counter()
    for account_no, page_id in cfg.accounts.items():
        page = pages.get(str(page_id).replace("-", ""))
        if page is None:
            counts["no page"] += 1
            continue
        days = sorted(ends.get(account_no, ()))
        want = canonicalise({"Statements from": days[0] if days else None,
                             "Statements to": days[-1] if days else None,
                             "Data coverage": "Full" if days else "None"},
                            ACCOUNT_ROLLUP_TYPES)
        changed = diff(want, page_properties(page, ACCOUNT_ROLLUP_TYPES))
        if not changed:
            counts["unchanged"] += 1
            continue
        counts["updated"] += 1
        if echo:
            print(f"  account {account_no}: {', '.join(changed)}")
        if not dry_run:
            client.patch(page["id"], notion.to_api({k: want[k] for k in changed},
                                                   ACCOUNT_ROLLUP_TYPES))
    return counts


def rollup_recurring(cfg, client, rules=(), dry_run=False, echo=True):
    """Payees.Recurring from the rules' recurring column. A payee no rule marks
    recurring gets the box cleared, so deleting the yes is enough to undo it."""
    from .parsers import categorise as rules_module
    want = rules_module.recurring_payees(rules) if rules else set()
    counts = Counter()
    patches = []
    for page in client.query_all(cfg.ds("payees")):
        target = notion.plain(page, "Name") in want
        if bool(notion.plain(page, "Recurring")) == target:
            counts["unchanged"] += 1
            continue
        counts["set" if target else "cleared"] += 1
        patches.append((page["id"], notion.to_api({"Recurring": target}, PAYEE_TYPES)))
    if not dry_run:
        client.map(lambda pr: client.patch(*pr), patches)
    if echo and (counts["set"] or counts["cleared"]):
        print(f"  payees: Recurring set on {counts['set']}, cleared on {counts['cleared']}")
    return counts


# ---------------------------------------------------------------- snapshot

# The four data sources a finished run has no pages for. Accounts and Cards it
# never reads whole, Budgets it reads inside the rollup and writes to after,
# and Payees it keeps only as name -> page id, with Recurring written on it
# after that. All four are small: reading them is a second or so, against the
# twenty-five a second full pull of the eight costs.
SNAPSHOT_READS = ("payees", "accounts", "cards", "budgets")

# The four the run already holds, whole and post-write.
SNAPSHOT_FROM_STATE = ("transactions", "period_balances", "categories", "trips")


def snapshot_pages(cfg, client, state):
    """The eight lists of pages `snapshot.build_from_pages` flattens.

    `state` has to be the state after the run's writes for the four it
    supplies: in `sync` that is verify's own read, which happens after both the
    upsert and the rollup, and in `rollup` it is the state the rollup patched
    in place as it wrote.
    """
    pages = {name: state.pages[name] for name in SNAPSHOT_FROM_STATE}
    read = client.map(lambda name: client.query_all(cfg.ds(name)), SNAPSHOT_READS)
    pages.update(zip(SNAPSHOT_READS, read))
    return pages


def write_snapshot(cfg, client, state, cache=None, day=None, categories_path=None):
    """Build today's analytics snapshot from the pages in hand and save it.
    Returns (path, transaction count).

    The snapshot module is imported here rather than at the top of the file
    because it imports this module.
    """
    from . import snapshot as analytics_snapshot
    s = analytics_snapshot.build_from_pages(cfg, snapshot_pages(cfg, client, state))
    path = analytics_snapshot.save(s, cache or analytics_snapshot.CACHE, day)
    # the refresh `analyse` does after every Notion build. With the sync
    # writing the snapshot, `analyse` reuses it and never gets there itself, so
    # the offline copy of the Categories rows would otherwise stop being kept.
    analytics_snapshot.write_categories_fallback(
        s, categories_path or analytics_snapshot.CATEGORIES_FALLBACK)
    return path, len(s["transactions"])


def snapshot_step(cfg, client, state, cache=None, day=None, categories_path=None,
                  echo=print):
    """Write that snapshot at the end of a real run. Never raises.

    `analyse` reuses today's snapshot if it finds one, so writing it here is
    what takes the second read of the eight databases out of the monthly run.
    It is the last thing a successful run does and it writes nothing to Notion,
    so a failure here is a warning and not a failed sync: everything the run
    set out to write is already written, and `finance.py analyse` builds the
    snapshot itself when it finds none.
    """
    try:
        path, rows = write_snapshot(cfg, client, state, cache=cache, day=day,
                                    categories_path=categories_path)
    except Exception as e:                                    # noqa: BLE001
        echo(f"snapshot: not written ({type(e).__name__}: {e}); "
             f"`python3 server/finance.py analyse` will build it")
        return None
    echo(f"snapshot: wrote {path.name} ({rows} transactions), analyse will reuse it")
    return path


# ---------------------------------------------------------------- hub status

def hub_status_text(parsed, problems, wrote, verify_problems=(), writes=0, when=None):
    """One line for the hub page's callout: when, what is loaded, whether the
    run was clean, what still needs a rule, and how far each account is
    loaded."""
    when = when or date.today().isoformat()
    unmatched = sum(1 for r in parsed.rows if r.get("flow_class") == "External"
                    and not r.get("category"))
    if problems:
        state = f"{len(problems)} problem(s), nothing written"
    elif verify_problems:
        state = f"written, verify found {len(verify_problems)} problem(s)"
    elif wrote:
        state = f"clean, {writes} write(s)"
    else:
        state = "clean"
    per_account = {}
    for b in parsed.blocks:
        per_account.setdefault(b["account"], []).append(b["period_end"][:7])
    cover = ", ".join(f"{short_account(a)} to {max(m)}" for a, m in sorted(per_account.items()))
    return (f"Last sync {when} · {len(parsed.rows):,} rows in {len(parsed.blocks)} blocks · "
            f"{state} · {unmatched} row(s) need a rule · {cover}")


def short_account(name):
    """'DBS Multiplier Account' -> 'DBS Multiplier': the first two words, or
    the first when there is only one, so two DBS accounts stay apart."""
    words = [w for w in name.split() if w.lower() not in ("account", "savings", "wallet", "·")]
    return " ".join(words[:2]) or name


def write_hub_status(cfg, client, text, dry_run=False):
    """Rewrite the hub callout named by [hub] status_block, if configured."""
    block = (cfg.hub or {}).get("status_block")
    if not block or dry_run:
        return False
    client.update_block(block, {"callout": {"rich_text": [
        {"type": "text", "text": {"content": text}}]}})
    return True


# ---------------------------------------------------------------- budget seed

# The one place Planned is written. It is kept out of BUDGET_TYPES so that
# sync and rollup cannot reach it; `finance.py budget --seed` is the explicit
# exception, and even it only fills a Planned that is empty.
SEED_TYPES = {"Planned": "number"}
SEED_CLASSES = ("Need", "Want")
SEED_ALL_CLASSES = ("Need", "Want", "Savings", "Investment")


def months_before(month, n):
    """The n calendar months before YYYY-MM, earliest first."""
    y, m = int(month[:4]), int(month[5:7])
    out = []
    for _ in range(n):
        m -= 1
        if m == 0:
            y, m = y - 1, 12
        out.append(f"{y:04d}-{m:02d}")
    return out[::-1]


def seed_budgets(cfg, client, month, window=6, everything=False, dry_run=False, echo=True,
                 today=None):
    """Budgets rows for `month`, one per spending category, with Planned set
    to the median of the category's Actual over the last `window` months
    before it that the sync has covered (months with any Budgets row at all;
    a month whose statements are not in yet is skipped, not counted as 0).

    Within those months a category with no row counts as 0. A median of 0
    leaves Planned empty but still creates the row, so there is somewhere to
    type. A row whose Planned is already set is left alone, whatever the
    median. Returns a Counter of created / filled / kept and the lines printed.
    """
    import statistics
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month or ""):
        raise ValueError(f"month must be YYYY-MM, got {month!r}")
    classes = SEED_ALL_CLASSES if everything else SEED_CLASSES
    state = read_state(cfg, client, with_options=False)
    have = {notion.plain(p, "Name"): p for p in client.query_all(cfg.ds("budgets"))}
    covered = sorted({n.rsplit(" · ", 1)[1] for n in have if " · " in n})
    span = [m for m in covered if m < month][-window:]
    if not span:
        raise ValueError(f"no Budgets rows before {month} to take a median from; "
                         "run the sync first")
    counts, lines, writes = Counter(), [], []
    for category in sorted(c for c, k in state.category_need.items() if k in classes):
        actuals = [notion.plain(have[f"{category} · {m}"], "Actual") or 0.0
                   if f"{category} · {m}" in have else 0.0 for m in span]
        median = int(statistics.median(actuals) + 0.5)      # half rounds up, not to even
        planned = median if median > 0 else None
        name = f"{category} · {month}"
        page = have.get(name)
        if page is None:
            counts["created"] += 1
            props = notion.to_api(canonicalise({"Name": name, "Category": [state.categories[category]],
                                                "Month": month, "Actual": 0.0,
                                                "Need class": state.category_need.get(category),
                                                "Current": month == (today or date.today()).strftime("%Y-%m")},
                                               BUDGET_TYPES), BUDGET_TYPES)
            if planned is not None:
                props.update(notion.to_api({"Planned": float(planned)}, SEED_TYPES))
            writes.append((None, props))
            lines.append(f"  create  {category:24s} Planned "
                         + (str(planned) if planned is not None else "(empty, median 0)"))
        elif notion.plain(page, "Planned") not in (None, ""):
            counts["kept"] += 1
            lines.append(f"  keep    {category:24s} Planned {notion.plain(page, 'Planned'):g} already set")
        elif planned is None:
            counts["kept"] += 1
            lines.append(f"  keep    {category:24s} Planned empty, median 0")
        else:
            counts["filled"] += 1
            writes.append((page, notion.to_api({"Planned": float(planned)}, SEED_TYPES)))
            lines.append(f"  fill    {category:24s} Planned {planned}")
    if not dry_run:
        client.map(lambda w: client.patch(w[0]["id"], w[1]) if w[0]
                   else client.create(cfg.ds("budgets"), w[1]), writes)
    if echo:
        print(f"Budgets for {month} from the median of {span[0]}..{span[-1]}")
        for line in lines:
            print(line)
        print(f"{counts['created']} created, {counts['filled']} filled, {counts['kept']} kept"
              + ("  (dry run, nothing written)" if dry_run else ""))
    return counts, lines


# ---------------------------------------------------------------- verify

def verify(cfg, client, parsed, archived=False):
    """Read Notion back and confirm there is nothing left to write, then run the
    block checks: row count, control totals and balances per block, no duplicate
    key, no row without an Account or a Period."""
    state = read_state(cfg, client, with_options=False)
    plan = build_plan(cfg, parsed, state)
    problems = []
    if plan.create_periods or plan.create_txns:
        problems.append(Problem("verify-missing",
                                f"{len(plan.create_periods)} block(s) and "
                                f"{len(plan.create_txns)} transaction(s) still missing",
                                "rerun the sync; if it repeats, the create call is failing"))
    # a row whose category has no Categories row links to nothing, which the
    # property diff cannot see: both sides are empty
    absent = sorted({r.get("category") for r in parsed.rows if r.get("category")}
                    - set(state.categories))
    if absent:
        problems.append(Problem("verify-missing",
                                f"{len(absent)} Categories row(s) still missing: "
                                + ", ".join(absent[:5]),
                                "rerun the sync; if it repeats, the create call is failing"))
    if plan.patch_periods or plan.patch_txns:
        names = Counter(k for _, _, _, ch in plan.patch_txns for k in ch)
        problems.append(Problem("verify-differs",
                                f"{len(plan.patch_txns)} transaction(s) and "
                                f"{len(plan.patch_periods)} block(s) still differ "
                                f"({', '.join(f'{k} x{v}' for k, v in names.most_common(5))})",
                                "the write did not take; check the API errors above"))
    if archived and plan.orphan_txns:
        problems.append(Problem("verify-orphans", f"{len(plan.orphan_txns)} orphan(s) left",
                                "the archive call did not take"))
    if plan.orphan_periods:
        problems.append(Problem(
            "verify-orphan-blocks",
            f"{len(plan.orphan_periods)} live period page(s) no block covers: "
            + ", ".join(notion.plain(p, "Name") or "(no name)"
                        for p in plan.orphan_periods[:5]),
            "the account-month is not in the parser output; put the statement back or "
            "rerun with --archive-orphans"))
    problems += block_checks(parsed, state)
    return plan, problems, state


def block_checks(parsed, state):
    """The checks that do not follow from the property diff: what Notion's own
    rows add up to per block, and the two global integrity checks."""
    out = []
    ids = {p["id"].replace("-", ""): name for name, p in state.periods.items()}
    agg = defaultdict(lambda: dict(n=0, tin=0.0, tout=0.0))
    for t in state.txns:
        amount = notion.plain(t, "Amount") or 0.0
        for pid in notion.plain(t, "Period") or ["(none)"]:
            a = agg[ids.get(pid, "(none)")]
            a["n"] += 1
            if amount > 0:
                a["tin"] += amount
            else:
                a["tout"] -= amount
    for b in parsed.blocks:
        a = agg.get(b["period"], dict(n=0, tin=0.0, tout=0.0))
        want_n = int(b["n_txns"] or 0)
        if a["n"] != want_n:
            out.append(Problem("block-row-count", b["period"],
                               f"Notion holds {a['n']} rows, the statement has {want_n}"))
            continue
        for label, got, want in (("in", a["tin"], float(b["total_in"] or 0)),
                                 ("out", a["tout"], float(b["total_out"] or 0))):
            if abs(got - want) > 0.005:
                out.append(Problem("block-total", b["period"],
                                   f"total {label}: Notion {got:,.2f}, statement {want:,.2f}"))
    dup = [k for k, n in Counter(notion.plain(t, "Txn key") for t in state.txns).items() if n > 1]
    if dup:
        out.append(Problem("duplicate-page", f"{len(dup)} key(s) on more than one page: "
                           + ", ".join(sorted(dup)[:5]),
                           "archive the extra pages; a key must name one page"))
    for label, prop in (("account", "Account"), ("period", "Period")):
        n = sum(1 for t in state.txns if not notion.plain(t, prop))
        if n:
            out.append(Problem(f"row-without-{label}", f"{n} transaction(s)",
                               f"every transaction needs its {prop} relation; rerun the sync"))
    return out


# ---------------------------------------------------------------- report

def report(cfg, parsed, plan, problems, state=None, dry_run=False, wrote=False,
           verify_problems=None, rules_path=None, rollup_result=None):
    """The run report: what was parsed, what doctor said, what changed, what is
    left over, what the rollup refreshed, what still needs a rule, and how far
    each account is covered."""
    L = []
    add = L.append
    add("=" * 72)
    add(f"finance sync{' (dry run)' if dry_run else ''}")
    add("=" * 72)

    add("")
    add("parsed")
    if parsed.skipped:
        add(f"  {len(parsed.skipped)} card statement(s) fully covered by an account "
            f"statement and skipped")
    add(f"  {len(parsed.rows)} transactions in {len(parsed.blocks)} account-month blocks "
        f"from {len({r['source'] for r in parsed.rows} | {b['source'] for b in parsed.blocks})} "
        f"statements")
    if rules_path:
        from .parsers import categorise as rules_module
        add(f"  rules: {rules_module._label(rules_path)}")
    matched = sum(1 for r in parsed.rows if r.get("rule"))
    add(f"  {matched} of {len(parsed.rows)} rows matched a rule")

    add("")
    add(f"doctor: {len(problems)} problem(s)" if problems else "doctor: clean")
    for p in problems:
        add(p.line())

    if category_lines(state):
        add("")
        add("categories")
        for line in category_lines(state):
            add("  " + line)

    add("")
    add("added" if wrote else "to add")
    if plan is None:
        add("  (not computed)")
    else:
        for block, _ in sorted(plan.create_periods, key=lambda x: x[0]["period"]):
            add(f"  block  {block['period']}  ({block['n_txns']} rows)")
        per_block = Counter(r["period"] for r, _ in plan.create_txns)
        for name, n in sorted(per_block.items()):
            add(f"  {n:5d} rows  {name}")
        if not plan.create_periods and not plan.create_txns:
            add("  nothing")

        add("")
        add("changed" if wrote else "to change")
        for _, name, _, changed in sorted(plan.patch_periods, key=lambda x: x[1]):
            add(f"  block  {name}: {', '.join(changed)}")
        props = Counter(k for _, _, _, ch in plan.patch_txns for k in ch)
        for name, n in props.most_common():
            add(f"  {n:5d} rows  {name}")
        if not plan.patch_periods and not plan.patch_txns:
            add("  nothing")

        add("")
        add(f"unchanged: {plan.same_txns} transactions, {plan.same_periods} blocks")

        add("")
        add("orphans")
        if plan.orphan_txns:
            add(f"  {len(plan.orphan_txns)} transaction(s) in Notion the parser no longer "
                f"produces")
            for page in plan.orphan_txns[:10]:
                add(f"    {notion.plain(page, 'Txn key')}  {notion.plain(page, 'Name')}")
            if len(plan.orphan_txns) > 10:
                add(f"    ... and {len(plan.orphan_txns) - 10} more")
        if plan.orphan_periods:
            add(f"  {len(plan.orphan_periods)} block(s) in Notion the parser no longer "
                f"produces: " + ", ".join(notion.plain(p, "Name") for p in plan.orphan_periods[:5]))
        if not plan.orphan_txns and not plan.orphan_periods:
            add("  none")

    add("")
    add("budgets refreshed")
    for line in budget_lines(rollup_result):
        add("  " + line)

    add("")
    add("trips assigned")
    for line in trip_lines(rollup_result):
        add("  " + line)

    add("")
    add("needs a rule")
    for line in needs_rule_lines(parsed.rows):
        add("  " + line)

    add("")
    add("coverage per account")
    for line in coverage_lines(cfg, parsed):
        add("  " + line)

    if verify_problems is not None:
        add("")
        add(f"verify: {len(verify_problems)} problem(s)" if verify_problems else "verify: clean")
        for p in verify_problems:
            add(p.line())
    add("")
    return "\n".join(L)


def budget_lines(rollup_result):
    """What the rollup did to Budgets, and what it left alone."""
    if rollup_result is None:
        return ["(the rollup has not run)"]
    c = rollup_result.budgets
    out = [f"{c['created']} row(s) created, {c['updated']} Actual(s) refreshed, "
           f"{c['unchanged']} unchanged"
           + ("  (dry run, nothing written)" if rollup_result.dry_run else "")]
    if c["no rows left"]:
        out.append(f"{c['no rows left']} row(s) whose category and month no longer "
                   f"have External transactions; Actual left as it was")
    if c["no category page"]:
        out.append(f"{c['no category page']} (category, month) pair(s) skipped: no "
                   f"Categories row of that name")
    a = rollup_result.accounts
    out.append(f"account coverage: {a['updated']} updated, {a['unchanged']} unchanged")
    r = rollup_result.payees
    out.append(f"payees recurring: set on {r['set']}, cleared on {r['cleared']}, "
               f"{r['unchanged']} unchanged")
    return out


def trip_lines(rollup_result, limit=25):
    """Rows per trip, biggest first, and what the rollup changed."""
    if rollup_result is None:
        return ["(the rollup has not run)"]
    c = rollup_result.trips
    out = [f"{c['assigned']} row(s) assigned, {c['cleared']} cleared, "
           f"{c['unchanged']} already right"
           + ("  (dry run, nothing written)" if rollup_result.dry_run else "")]
    if c["not in Notion"]:
        out.append(f"{c['not in Notion']} parsed row(s) are not in Notion yet")
    d = rollup_result.trip_dates
    if d["updated"] or d["no plan"]:
        out.append(f"trip dates: {d['updated']} copied from the planner, "
                   f"{d['unchanged']} unchanged, {d['no plan']} trip(s) with no plan")
    if not rollup_result.trip_rows:
        out.append("no parsed row falls inside a trip")
    for name, n in rollup_result.trip_rows.most_common(limit):
        out.append(f"{n:5d} rows  {name}")
    return out


def needs_rule_lines(rows, limit=15):
    """The unmatched External rows, grouped by whatever text they print, biggest
    money first: the queue for the next rules edit."""
    groups = {}
    for r in rows:
        if r.get("rule") or r["flow_class"] != "External":
            continue
        stem = (r["merchant"] or r["counterparty"] or r["memo"] or r["bank_txn_type"])[:44]
        g = groups.setdefault(stem, dict(n=0, amount=0.0, first=r["date"], last=r["date"]))
        g["n"] += 1
        g["amount"] += abs(float(r["amount"] or 0))
        g["first"], g["last"] = min(g["first"], r["date"]), max(g["last"], r["date"])
    if not groups:
        return ["every External row matched a rule"]
    out = [f"{len(groups)} unmatched stem(s), "
           f"{sum(g['n'] for g in groups.values())} rows; biggest first:"]
    for stem, g in sorted(groups.items(), key=lambda kv: -kv[1]["amount"])[:limit]:
        out.append(f"  {g['n']:4d} rows  {g['amount']:>10,.2f}  {g['first']}..{g['last']}  {stem}")
    if len(groups) > limit:
        out.append(f"  ... and {len(groups) - limit} more stems")
    return out


def coverage_lines(cfg, parsed):
    """One line per configured account: months covered and rows parsed."""
    out = []
    by_account = defaultdict(list)
    for b in parsed.blocks:
        by_account[b["account_no"]].append(b)
    rows = Counter(r["account_no"] for r in parsed.rows)
    for account in cfg.accounts:
        blocks = by_account.get(account, [])
        if not blocks:
            out.append(f"{account:20s} no statements")
            continue
        ends = sorted(b["period_end"] for b in blocks)
        months = {e[:7] for e in ends}
        span = _months_between(ends[0][:7], ends[-1][:7])
        gap = "" if len(months) == span else f", {span - len(months)} month(s) with no statement"
        out.append(f"{account:20s} {ends[0][:7]}..{ends[-1][:7]}  {len(blocks):3d} blocks  "
                   f"{rows.get(account, 0):5d} rows{gap}")
    return out


def _months_between(first, last):
    """Inclusive month count between two YYYY-MM strings."""
    fy, fm = (int(x) for x in first.split("-"))
    ly, lm = (int(x) for x in last.split("-"))
    return (ly - fy) * 12 + (lm - fm) + 1
