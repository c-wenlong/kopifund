#!/usr/bin/env python3
"""The monthly statement sync.

Usage:
    python3 server/finance.py sync   [--dry-run] [--force] [--archive-orphans]
                              [--report PATH] [--rules PATH] [--csv DIR]
    python3 server/finance.py doctor [--rules PATH] [--csv DIR]
    python3 server/finance.py report [--report PATH] [--rules PATH] [--csv DIR]
    python3 server/finance.py verify [--rules PATH] [--csv DIR]
    python3 server/finance.py rollup [--dry-run] [--rules PATH] [--csv DIR]
    python3 server/finance.py payees [--dry-run] [--rules PATH] [--csv DIR]
    python3 server/finance.py add-options [--dry-run] [--rules PATH] [--csv DIR]
    python3 server/finance.py analyse [--from-csv DIR] [--refresh] [--only ID ...]
                              [--out DIR] [--no-publish] [--dry-run]
    python3 server/finance.py split-transport [--dry-run]
    python3 server/finance.py sample <pdf> [--out PATH]

sync parses every statement folder in the data folder's config.toml, categorises the rows
with the rules, runs every doctor check, creates what Notion is missing and
patches what differs, and then rolls up the analytics. A Categories row the
rows link to that Notion lacks and the record holds is created first (G13);
a category the record does not hold is a doctor problem. It writes nothing when
doctor found a problem, and a month with no change issues no writes at all. A
run that verified clean ends by writing today's analytics snapshot into
cache/ in the data folder from the pages it has already read, so the `analyse` that follows
reuses it rather than reading the eight databases a second time.

  --dry-run          do everything except write
  --force            allow an implausibly large insert (see doctor)
  --archive-orphans  move transactions the parser no longer produces to the
                     trash; without it they are only reported
  --rules PATH       rules file or directory (default: the repo's data/rules/, plus
                     the data folder's rules/ when it holds any)
  --csv DIR          reuse an earlier parser run instead of reparsing
  --report PATH      also write the run report here

doctor runs the checks and nothing else. report prints the full run report
without writing. verify reads Notion back and compares it with the parsers.
rollup redoes the analytics pass on its own: Transactions.Trip from the Trips
rows, one Budgets row per category and month with its Actual, Statements from /
to and Data coverage on every account, and Payees.Recurring from the rules. It
never touches Budgets.Planned, and it writes the same snapshot sync does.
payees rebuilds the Payees rows from the rules.
migrate --from DIR copies a finance-tracking folder into the data folder
(~/.kopifund, or KOPIFUND_DATA): statements renamed to the new layout, every copy
checked by sha256, the old folder only read. See SPEC.md, "The data folder".

add-options adds the select options the statements need but Notion does not
have yet.

split-transport moves your own Transport rules and rows to Transport (public)
and Transport (private), once: each personal rule that says Transport is
classified from its pattern and payee, each Transport row follows the rules as
the next load would, and a table shows what moved and what was left for you.
With --dry-run it writes nothing; without, the record is copied to backups/ first
and the rules change lands in the Rules page's undo history.

analyse is the analytics pass. It builds today's snapshot of the eight
databases under cache/ in the data folder (or reuses the one already there, which after a sync
is the one the sync wrote), runs every module in server/analytics/stats/, writes one
result JSON each, and prints the first finding of each. The one thing it writes
back are the hub page's Metrics and Months tables, which --no-publish skips.

  --from-csv DIR  build the snapshot from an earlier parser run plus the rules
                  and server/analytics/categories.json instead of from Notion
  --refresh       rebuild today's snapshot even if it is already on disk
  --only ID       run only these stats (repeatable, or space separated). An id
                  that is not a stat stops the run with exit code 3 rather than
                  quietly running all of them or none
  --out DIR       write the cache somewhere else (default cache/ in the data folder)

sample <pdf> writes a statement's text, as the parsers read it, with names,
street addresses, postcodes, NRIC and FIN numbers, emails, phone numbers and
account and card numbers replaced by placeholders of the same shape, for
whoever builds a layout for a bank Kopifund does not read yet (G21). It writes
<name>.sample.txt beside the PDF, or the path --out gives, never inside this
repository, and prints how many of each it replaced. The replacement is best
effort: read the file before you share it. It needs no config and sends
nothing anywhere; the PDF is only read.

Exit codes: 0 clean, 1 doctor found problems and nothing was written,
2 something was written and verify failed, 3 environment or API failure.
"""
import argparse
import sys
from collections import OrderedDict, defaultdict
from datetime import date
from pathlib import Path

from finance import notion, sync
from finance.model import (PAYEE_FIELDS, PAYEE_TYPES, SELECT_CHECKS, ConfigMissing,
                           Problem, canonicalise, diff, load_config, page_properties,
                           reachable_options)

CLEAN, DOCTOR_FAILED, VERIFY_FAILED, ENVIRONMENT = 0, 1, 2, 3


def client_for(cfg):
    """A NotionClient with the token from the environment, the data folder's .env, or the keychain."""
    return notion.NotionClient(notion.find_token(cfg), cfg.version,
                               pace=cfg.pace, workers=cfg.workers)


def prepare(cfg, args, echo=True):
    """Parse and categorise. Returns (parsed, rules path)."""
    rules = [args.rules] if isinstance(args.rules, str) else (args.rules or [])
    rules_path = ([Path(r) for r in rules] if len(rules) > 1 else Path(rules[0])) if rules \
        else sync.default_rules(cfg)
    parsed = sync.parse(cfg, args.csv, echo=echo)
    sync.categorise(parsed, rules_path)
    return parsed, rules_path


def plan_blockers(cfg, parsed):
    """Problems that make a diff impossible: without an Accounts or Cards page
    there is no page body to compare against."""
    fatal = ("parser-failed", "duplicate-period", "row-without-block", "rules-invalid")
    return (sync.check_config_coverage(cfg, parsed)
            + [p for p in parsed.problems if p.code in fatal])


def balance_blockers(parsed):
    """Problems that mean a row has not passed its balance check: a parser
    that failed (a duplicate period is how one that stopped on two statements
    for the same month is reported), a row with no block, and the block checks
    a sync applies.

    A parser writes its CSVs before it exits on a failed balance check, so
    rows reused with `--csv DIR` are put through sync.check_blocks again
    rather than trusted as they stand. A parser stopped by a duplicate period
    writes nothing, so only its problem says the run is partial."""
    out = [p for p in parsed.problems
           if p.code in ("parser-failed", "duplicate-period", "row-without-block")]
    return out + [p for p in sync.check_blocks(parsed) if p not in out]


def refuse(blockers):
    """Print the problems that stop a command, and the exit code it returns."""
    for p in blockers:
        print(p.line())
    print(f"\n{len(blockers)} problem(s); nothing written.")
    return DOCTOR_FAILED


def assess(cfg, client, args, force=False, archive_orphans=False):
    """Everything up to but not including a write: parse, categorise, read
    Notion, build the plan, run doctor."""
    parsed, rules_path = prepare(cfg, args)
    state = sync.read_state(cfg, client)
    # G13: a category the record holds and Notion lacks is not a problem: the
    # sync creates its Categories row before the first row links to it
    sync.plan_categories(cfg, client, state, {r.get("category") for r in parsed.rows})
    plan = None if plan_blockers(cfg, parsed) else sync.build_plan(cfg, parsed, state)
    problems = sync.doctor(cfg, parsed, state, plan, force=force,
                           archive_orphans=archive_orphans)
    return parsed, rules_path, state, plan, problems


def emit(text, path=None):
    print(text)
    if path:
        Path(path).write_text(text, encoding="utf-8")
        print(f"report written to {path}")


# ---------------------------------------------------------------- commands

def cmd_sync(cfg, args):
    """Parse, check, then create and patch what Notion needs."""
    client = client_for(cfg)
    parsed, rules_path, state, plan, problems = assess(
        cfg, client, args, force=args.force, archive_orphans=args.archive_orphans)
    if problems:
        emit(sync.report(cfg, parsed, plan, problems, state=state, dry_run=args.dry_run,
                         rules_path=rules_path), args.report)
        print(f"\n{len(problems)} problem(s); nothing written.")
        return DOCTOR_FAILED
    if args.dry_run:
        rolled = sync.rollup(cfg, client, parsed, parsed.rules, state=state,
                             dry_run=True, echo=False)
        emit(sync.report(cfg, parsed, plan, problems, state=state, dry_run=True,
                         rules_path=rules_path, rollup_result=rolled), args.report)
        new = len(state.new_categories)
        print(f"\ndry run: {new + plan.writes + rolled.writes} write(s) would be made ("
              + (f"{new} {'category' if new == 1 else 'categories'}, " if new else "")
              + f"{plan.writes} upsert, {rolled.writes} rollup).")
        return CLEAN
    if plan.writes or (args.archive_orphans and plan.orphan_txns):
        print(f"\nwriting: {len(plan.create_periods)} + {len(plan.create_txns)} to create, "
              f"{len(plan.patch_periods)} + {len(plan.patch_txns)} to patch")
    # SPEC: write the record, then mirror; the app owns the user tables (G8)
    from finance import db, local
    record = db.connect()
    try:
        local.load(cfg, record, parsed, kind="sync")
    finally:
        record.close()
    # G13: the Categories rows the rows link to, before the first link
    sync.create_categories(cfg, client, state)
    done = sync.upsert(cfg, client, parsed, state, plan,
                       archive_orphans=args.archive_orphans)
    rolled = sync.rollup(cfg, client, parsed, parsed.rules)
    _, verify_problems, after = sync.verify(cfg, client, parsed,
                                            archived=args.archive_orphans)
    emit(sync.report(cfg, parsed, done, problems, state=state, wrote=True,
                     verify_problems=verify_problems, rules_path=rules_path,
                     rollup_result=rolled), args.report)
    print(f"\n{client.writes} write(s) made.")
    sync.write_hub_status(cfg, client, sync.hub_status_text(
        parsed, problems, wrote=True, verify_problems=verify_problems, writes=client.writes))
    if verify_problems:
        print(f"{len(verify_problems)} problem(s) after writing.")
        print("snapshot: not written while verify has problems; "
              "`python3 server/finance.py analyse` builds it")
        return VERIFY_FAILED
    # verify has just read four of the eight databases back, after every write
    # this run makes, so today's snapshot costs four small reads rather than a
    # second full pull in `analyse`
    sync.snapshot_step(cfg, client, after)
    return CLEAN


def cmd_doctor(cfg, args):
    """Every preflight check, no writes."""
    from finance import local
    if not local.notion_enabled(cfg):
        # G13: with Notion off (a user without it, or an agent's copy of the
        # data folder) doctor runs every check that needs no Notion; the mirror's
        # own checks (options, relations, the plan) have nothing to compare with
        parsed, _ = prepare(cfg, args)
        problems = sync.doctor(cfg, parsed, None, None)
        print()
        if not problems:
            print(f"doctor: clean. {len(parsed.rows)} rows in {len(parsed.blocks)} blocks; "
                  "Notion is off, so the mirror checks were skipped.")
            return CLEAN
        print(f"doctor: {len(problems)} problem(s)")
        for p in problems:
            print(p.line())
        return DOCTOR_FAILED
    client = client_for(cfg)
    parsed, rules_path, state, plan, problems = assess(cfg, client, args)
    print()
    if not problems:
        print(f"doctor: clean. {len(parsed.rows)} rows in {len(parsed.blocks)} blocks; "
              + (f"{plan.writes} write(s) pending." if plan else "no plan built."))
        for line in sync.category_lines(state):
            print(f"  {line}")
        return CLEAN
    print(f"doctor: {len(problems)} problem(s)")
    for p in problems:
        print(p.line())
    return DOCTOR_FAILED


def cmd_report(cfg, args):
    """The full run report, nothing written."""
    client = client_for(cfg)
    parsed, rules_path, state, plan, problems = assess(cfg, client, args)
    rolled = sync.rollup(cfg, client, parsed, parsed.rules, state=state,
                         dry_run=True, echo=False)
    emit(sync.report(cfg, parsed, plan, problems, state=state, dry_run=True,
                     rules_path=rules_path, rollup_result=rolled), args.report)
    return CLEAN


def cmd_verify(cfg, args):
    """Read Notion back and compare it with the parsers, property by property.

    Notion is compared only with rows that passed the block checks: a row no
    balance has checked is no reference for what Notion should hold."""
    parsed, rules_path = prepare(cfg, args)
    blockers = plan_blockers(cfg, parsed)
    blockers += [p for p in balance_blockers(parsed) if p not in blockers]
    if blockers:
        return refuse(blockers)
    client = client_for(cfg)
    plan, problems, state = sync.verify(cfg, client, parsed)
    print()
    if problems:
        print(f"verify: {len(problems)} problem(s)")
        for p in problems:
            print(p.line())
        return VERIFY_FAILED
    print(f"verify: Notion matches the parsers on every property of "
          f"{plan.same_txns} transactions and {plan.same_periods} blocks.")
    return CLEAN


def cmd_rollup(cfg, args):
    """The analytics pass on its own: trips, budgets, account coverage, recurring.

    It reads Notion rather than the plan, so it is safe to run against a
    database the sync has not touched this session; nothing here creates or
    patches a transaction.

    The rows it rolls up pass the same block checks a sync applies first
    (balance_blockers).
    """
    parsed, rules_path = prepare(cfg, args)
    blockers = plan_blockers(cfg, parsed)
    blockers += [p for p in balance_blockers(parsed) if p not in blockers]
    if blockers:
        return refuse(blockers)
    client = client_for(cfg)
    # the state is read here rather than inside rollup so that the snapshot
    # step can have it: the rollup writes Trip and Dates onto those same pages
    state = sync.read_state(cfg, client, with_options=False)
    rolled = sync.rollup(cfg, client, parsed, parsed.rules, state=state,
                         dry_run=args.dry_run)
    print()
    print("budgets refreshed")
    for line in sync.budget_lines(rolled):
        print("  " + line)
    print("trips assigned")
    for line in sync.trip_lines(rolled):
        print("  " + line)
    print(f"\n{rolled.writes} write(s) {'would be made' if args.dry_run else 'made'}.")
    if not args.dry_run:
        sync.snapshot_step(cfg, client, state)
    return CLEAN


def cmd_budget(cfg, args):
    """Seed a month's Budgets rows with Planned from the trailing median."""
    if not args.seed:
        print("usage: finance.py budget --seed YYYY-MM [--window 6] [--all] [--dry-run]",
              file=sys.stderr)
        return ENVIRONMENT
    client = client_for(cfg)
    try:
        sync.seed_budgets(cfg, client, args.seed, window=args.window, everything=args.all,
                          dry_run=args.dry_run)
    except ValueError as e:
        print(f"\n{e}", file=sys.stderr)
        return ENVIRONMENT
    return CLEAN


def cmd_add_options(cfg, args):
    """Add the select options the statements need but Notion does not have yet.

    Notion rejects an unknown select value, and a PATCH of a property replaces
    its whole definition, so the full option list and the existing description
    both go back with the change.

    It adds two kinds of value: the ones this run's statements carry, and the
    ones a parser can produce but no statement has produced yet
    (model.reachable_options). Without the second kind the first row of an unseen
    kind, a YouTrip refund say, would stop a monthly run at unknown-option.

    The values come only from rows that passed the block checks
    (balance_blockers), so an unchecked row never adds an option.
    """
    parsed, rules_path = prepare(cfg, args)
    blockers = balance_blockers(parsed)
    if blockers:
        return refuse(blockers)
    client = client_for(cfg)
    state = sync.read_state(cfg, client)
    total = 0
    reachable = reachable_options()
    for ds_name, prop, column in SELECT_CHECKS:
        rows = parsed.blocks if ds_name == "period_balances" else parsed.rows
        have = set(state.options.get((ds_name, prop), ()))
        want = {r.get(column) for r in rows if r.get(column)}
        want |= set(reachable.get((ds_name, prop), ()))
        missing = sorted(want - have)
        if not missing:
            continue
        total += len(missing)
        print(f"  {ds_name}.{prop}: add {', '.join(missing)}")
        if not args.dry_run:
            client.add_select_options(cfg.ds(ds_name), prop, missing)
    print(f"{total} option(s) {'to add' if args.dry_run else 'added'}")
    return CLEAN


def stale_payees(have, want):
    """Payee rows left behind by a rename: no rule names them any more, and no
    transaction links to them.

    A renamed payee gets a new row and the old one stays, so these accumulate
    silently. A row with rows still linked is never stale however it was named:
    that is a rule that stopped matching, which is a rules problem to look at,
    not a page to throw away.
    """
    out = []
    for name, page in have.items():
        if name in want:
            continue
        prop = page["properties"].get("Transactions") or {}
        if prop.get("relation"):
            continue
        out.append(name)
    return sorted(out)


def cmd_payees(cfg, args):
    """One Payees row per distinct payee in the rules.

    Default category is the category carrying most of that payee's matched rows
    (ties: larger amount, then the first rule). Existing rows are patched only
    where a field differs. The default category is counted only over rows
    that passed the block checks (balance_blockers).
    """
    from finance.parsers import categorise as rules_module
    parsed, rules_path = prepare(cfg, args)
    blockers = balance_blockers(parsed)
    if blockers:
        return refuse(blockers)
    client = client_for(cfg)
    per_payee = defaultdict(lambda: defaultdict(lambda: [0, 0.0]))     # rows, abs amount
    for r in parsed.rows:
        if r.get("payee"):
            c = per_payee[r["payee"]][r["category"]]
            c[0] += 1
            c[1] += abs(float(r["amount"] or 0))
    want = OrderedDict()
    # G13: the same names the sync accepts, so a rule naming a category the
    # owner added in the app loads here too
    for r in rules_module.load_rules(rules_path, sync.record_categories()):
        if not r["payee"]:
            continue
        p = want.setdefault(r["payee"], dict(kind=r["kind"], country=r["country"],
                                             category=r["category"], patterns=[],
                                             account_no=r.get("account_no", "")))
        if r["pattern"] not in p["patterns"]:
            p["patterns"].append(r["pattern"])
        if r.get("account_no") and r["account_no"] not in p["account_no"]:
            p["account_no"] = " | ".join(x for x in (p["account_no"], r["account_no"]) if x)
        for k in ("kind", "country"):
            if r[k] != p[k]:
                print(f"  warning: line {r['line']} gives {r['payee']!r} {k} {r[k]!r}, "
                      f"first rule said {p[k]!r}; keeping the first", file=sys.stderr)
    for name, p in want.items():
        counts = per_payee.get(name)
        if counts:
            p["category"] = max(counts.items(),
                                key=lambda kv: (kv[1][0], kv[1][1], kv[0] == p["category"]))[0]
    state = sync.read_state(cfg, client, with_options=False)
    # G13: a default category the record holds gets its Categories row the
    # way a sync's would, so a new payee in a new category needs no hand edit
    sync.plan_categories(cfg, client, state, {p["category"] for p in want.values()})
    cats = state.categories
    missing = sorted({p["category"] for p in want.values()} - set(cats))
    if missing:
        for name in missing:
            print(Problem("missing-category", name,
                          "create the Categories row in Notion first").line())
        return DOCTOR_FAILED
    if args.dry_run:
        for line in sync.category_lines(state):
            print(f"  {line}")
    sync.create_categories(cfg, client, state, dry_run=args.dry_run)
    have = {notion.plain(p, "Name"): p for p in client.query_all(cfg.ds("payees"))}
    stale = stale_payees(have, want)
    print(f"{len(want)} payees in the rules, {len(have)} in Notion"
          + (f" ({len(set(have) - set(want))} of them not in the rules"
             + (f", {len(stale)} with no transactions)" if stale else ")") if have else ""))
    counts = dict(created=0, updated=0, unchanged=0)
    recurring = rules_module.recurring_payees(parsed.rules)
    writes = []                       # (page or None, API-shaped properties)
    for name, p in want.items():
        props = canonicalise({"Name": name, "Kind": p["kind"], "Country": p["country"],
                              "Raw patterns": " | ".join(p["patterns"]),
                              "Account no.": p["account_no"],
                              "Recurring": name in recurring,
                              "Default category": [cats[p["category"]]]}, PAYEE_TYPES)
        page = have.get(name)
        if not page:
            counts["created"] += 1
            print(f"  create  {name}: {p['kind']} {p['country'] or '-'} {p['category']}"
                  + ("  recurring" if props["Recurring"] else ""))
            writes.append((None, notion.to_api(props, PAYEE_TYPES)))
            continue
        was = page_properties(page, PAYEE_TYPES)
        changed = [k for k in diff(props, was) if k in PAYEE_FIELDS]
        if not changed:
            counts["unchanged"] += 1
            continue
        counts["updated"] += 1
        print(f"  update  {name}: " + "; ".join(
            f"{k} {was[k]!r} -> {props[k]!r}" for k in changed))
        writes.append((page, notion.to_api({k: props[k] for k in changed}, PAYEE_TYPES)))
    if not args.dry_run:
        client.map(lambda w: client.patch(w[0]["id"], w[1]) if w[0]
                   else client.create(cfg.ds("payees"), w[1]), writes)
    if stale:
        for name in stale:
            print(f"  {'prune ' if args.prune else 'stale '} {name}: "
                  f"no rule names it and no transaction links to it"
                  + ("" if args.prune else "; --prune trashes it"))
        if args.prune and not args.dry_run:
            client.map(lambda name: client.archive(have[name]["id"]), stale)
        counts["pruned" if args.prune else "stale"] = len(stale)
    print("  ".join(f"{k}={v}" for k, v in counts.items())
          + ("  (dry run, nothing written)" if args.dry_run else ""))
    return CLEAN


def cmd_analyse(cfg, args):
    """Build or reuse today's snapshot and run every stat. Reads only."""
    from analytics import run as analytics_run
    from finance import snapshot as analytics_snapshot
    out_dir = Path(args.out) if args.out else analytics_snapshot.CACHE
    from finance import db
    source = "csv" if args.from_csv else ("sqlite" if db.db_path().exists() else "notion")
    only = tuple(args.only or ())
    try:
        snap, results = analytics_run.analyse(cfg, source=source,
                                              csv_dir=args.from_csv,
                                              refresh=args.refresh, only=only,
                                              out_dir=out_dir)
    except analytics_run.UnknownStat as e:
        print(f"\n{e}", file=sys.stderr)
        return ENVIRONMENT
    except analytics_snapshot.UncheckedStatements as e:
        # --from-csv: the CSVs failed the checks a normal run applies
        print(f"\n{e}", file=sys.stderr)
        return DOCTOR_FAILED
    print(f"\n{len(results)} stat(s) written to {out_dir}")
    if not args.no_publish and (cfg.data_sources.get("metrics") or cfg.data_sources.get("months")):
        from analytics import publish as hub_publish
        print("hub page")
        # the Metrics rows are numbered across every stat, so publish from all
        # the cached results with this run's on top: an --only subset would
        # number its rows from one again and collide with the others
        stale = []
        merged = {**analytics_run.cached_results(out_dir, stale), **results}
        # G20: a cached result from before the stats wrote cents is not read; say whose rows stand
        old = sorted(set(stale) - set(results))
        if old:
            print(f"  {len(old)} cached result(s) from before G20 ({', '.join(old)}) are not read, so their "
                  "rows stay as the last publish wrote them; run analyse without --only to refresh them")
        hub_publish.publish(cfg, client_for(cfg), merged, snapshot=snap, dry_run=args.dry_run)
    return CLEAN


def cmd_load(cfg, args):
    """Parse, check and write kopifund.db; no Notion at all (G4). Refuses on any
    problem a run would refuse on."""
    from finance import db, local
    parsed, rules_path = prepare(cfg, args)
    problems = (list(parsed.problems) + sync.check_environment(cfg) + sync.check_statements(cfg, parsed)
                + sync.check_statement_files(cfg) + sync.check_blocks(parsed))
    if problems:
        for p in problems:
            print(p.line())
        print(f"\n{len(problems)} problem(s); nothing written.")
        return DOCTOR_FAILED
    if args.dry_run:
        print(f"dry run: {len(parsed.rows)} rows from "
              f"{len({r['source'] for r in parsed.rows})} statements would be loaded")
        return CLEAN
    conn = db.connect()
    counts = local.load(cfg, conn, parsed)
    print(f"kopifund.db: {counts['inserted']} added, {counts['updated']} updated, "
          f"{counts['archived']} archived, {counts['trips assigned']} rows on a trip")
    return CLEAN


def cmd_import_notion(cfg, args):
    """Read the fields typed in Notion (categories, budgets, trips, accounts,
    cards) and the config's [plan] into kopifund.db: the one-time import when
    the record starts. The app owns these fields from then on (G8), so running
    it again replaces what the app has edited."""
    from finance import db, local
    if not local.notion_enabled(cfg):
        print("Notion is not configured in this data folder; nothing to import.", file=sys.stderr)
        return ENVIRONMENT
    plan = local.config_plan()
    counts = local.read_back(cfg, client_for(cfg), db.connect(), plan=plan)
    print("imported: " + ", ".join(f"{v} {k}" for k, v in counts.items()))
    return CLEAN


def cmd_split_transport(cfg, args):
    """Move the user's own Transport rules and rows to Transport (public) and
    Transport (private), once (server/finance/transport_split.py). --dry-run prints
    what would move and writes nothing."""
    from finance import db, transport_split
    try:
        transport_split.run(db.db_path(), dry_run=args.dry_run)
    except transport_split.SplitRefused as e:
        print(f"\n{e}", file=sys.stderr)
        return DOCTOR_FAILED
    return CLEAN


COMMANDS = {"sync": cmd_sync, "analyse": cmd_analyse, "load": cmd_load,
            "split-transport": cmd_split_transport,
            "import-notion": cmd_import_notion, "doctor": cmd_doctor, "report": cmd_report,
            "verify": cmd_verify, "status": cmd_report, "payees": cmd_payees,
            "rollup": cmd_rollup, "add-options": cmd_add_options,
            "budget": cmd_budget}


def cmd_migrate(args):
    """Copy a finance-tracking folder into the data folder (server/finance/migrate.py)."""
    from finance import migrate
    if not args.source:
        print("migrate needs --from <finance-tracking folder>", file=sys.stderr)
        return ENVIRONMENT
    p = migrate.plan(args.source)
    result = None if p.problems else migrate.run(p, dry_run=args.dry_run)
    text = migrate.report(p, result, dry_run=args.dry_run)
    print(text)
    if not args.dry_run and not p.problems:
        out = p.new / f"migrate-report-{date.today().isoformat()}.txt"
        out.write_text(text + "\n", encoding="utf-8")
        out.chmod(0o600)
        print(f"\nreport saved to {out}")
    return DOCTOR_FAILED if p.problems else CLEAN


def cmd_sample(args):
    """A statement's text with what identifies its holder replaced, for
    whoever builds its layout (G21, server/finance/sample.py). Needs no config."""
    from finance import sample, sniff
    if not args.pdf:
        print("sample needs the statement PDF: python3 server/finance.py sample <pdf> [--out PATH]", file=sys.stderr)
        return ENVIRONMENT
    try:
        path, counts, lines = sample.write(args.pdf, args.out)
    except sample.SampleRefused as e:
        print(f"\n{e}", file=sys.stderr)
        return ENVIRONMENT
    print(f"wrote {path} ({lines} lines)")
    print(f"replaced: {sample.report(counts)}")
    known = sniff.sniff(path.read_text(encoding="utf-8"))
    if len(known) == 1:
        print(f"Kopifund already reads this as {known[0].label()}; upload it instead.")
    print("The replacement is best effort: read the file before you share it.")
    return CLEAN


def cmd_link(args):
    """G25: the phone link's setup checklist (server/finance/link_setup.py).
    About the machine, not the data folder, so it needs no config."""
    from finance import link_setup
    rows = link_setup.checklist(args.port)
    width = max(len(name) for name, _, _ in rows)
    misses = 0
    print(f"phone link: what tailscale serve needs to carry the phone to 127.0.0.1:{args.port}\n")
    for name, detail, fix in rows:
        if detail:
            print(f"  {name:<{width}}  ok        {detail}")
        else:
            misses += 1
            print(f"  {name:<{width}}  missing   {fix}")
    print(f"\n{'link ready' if not misses else f'{misses} to fix'}")
    return CLEAN if not misses else DOCTOR_FAILED


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=sorted([*COMMANDS, "migrate", "sample", "link"]))
    ap.add_argument("pdf", nargs="?", help="sample: the statement PDF to write a sample text of")
    ap.add_argument("--dry-run", action="store_true", help="do everything except write")
    ap.add_argument("--force", action="store_true",
                    help="allow an implausibly large insert")
    ap.add_argument("--archive-orphans", action="store_true",
                    help="trash transactions the parser no longer produces")
    ap.add_argument("--report", metavar="PATH", help="also write the run report here")
    ap.add_argument("--rules", metavar="PATH", action="append",
                    help="rules file or directory, repeatable (default: the repo's data/rules/ "
                         "and the data folder's rules/)")
    ap.add_argument("--csv", metavar="DIR", help="reuse an earlier parser run")
    ap.add_argument("--from-csv", metavar="DIR",
                    help="analyse: build the snapshot from parser CSVs, not Notion")
    ap.add_argument("--refresh", action="store_true",
                    help="analyse: rebuild today's snapshot even if it exists")
    ap.add_argument("--only", metavar="ID", nargs="*",
                    help="analyse: run only these stats")
    ap.add_argument("--out", metavar="DIR",
                    help="analyse: write the cache here (default cache/ in the data folder); "
                         "sample: the text file to write (default <name>.sample.txt beside the PDF)")
    ap.add_argument("--no-publish", action="store_true",
                    help="analyse: skip writing the hub page's Metrics and Months tables")
    ap.add_argument("--prune", action="store_true",
                    help="payees: trash rows no rule names and no transaction links to")
    ap.add_argument("--seed", metavar="YYYY-MM",
                    help="budget: the month to create Budgets rows for")
    ap.add_argument("--port", metavar="N", type=int, default=8770,
                    help="link: the hub's port, which tailscale serve must proxy to")
    ap.add_argument("--window", type=int, default=6,
                    help="budget: months of Actual the median is taken over (6)")
    ap.add_argument("--all", action="store_true",
                    help="budget: also seed Savings and Investment categories")
    ap.add_argument("--from", dest="source", metavar="DIR",
                    help="migrate: the finance-tracking folder to copy from (only read)")
    args = ap.parse_args(argv)
    if args.command == "migrate":
        return cmd_migrate(args)              # makes the config, so cannot need one
    if args.command == "sample":
        return cmd_sample(args)               # reads one PDF, no data folder at all
    if args.command == "link":
        return cmd_link(args)                 # G25: the machine's checklist, no config either
    if args.pdf:
        ap.error(f"{args.command} takes no file argument")
    try:
        cfg = load_config()
    except ConfigMissing as e:
        print(f"\n{e}", file=sys.stderr)
        return ENVIRONMENT
    try:
        return COMMANDS[args.command](cfg, args)
    except notion.NotionError as e:
        print(f"\n{e}", file=sys.stderr)
        return ENVIRONMENT


if __name__ == "__main__":
    sys.exit(main())
