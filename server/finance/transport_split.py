"""Split Transport into Transport (public) and Transport (private), once.

The shipped list and the shared rules moved to the two new categories, so a
load files every row a shared rule names (Grab, Bus/MRT, ...) in one of them
on its own. What a load cannot move is the user's own: personal rules that
still say Transport, and the record's rows those rules set. This does both:

1. Each personal rule whose category is Transport is classified from its
   pattern and payee (a key rule from the row it pins) and pointed at the
   public or the private category. A rule the classifier cannot place stays
   Transport for the user to decide.
2. Each live transaction in Transport is run through the whole rule set as a
   load would run it, with those rules already rewritten, and moves to the
   category that gives. A row that still comes out as Transport stays there.
3. It prints a table of what moved and what it left.

`--dry-run` (python3 server/finance.py split-transport --dry-run) does all of it
in memory and writes nothing. Without it, the record is copied to backups/
first; then, in one transaction, the rows move and the personal rules are
rewritten through rules_app, the path every Rules page edit takes (the set
must load first, each file is replaced in one step, records it does not touch
keep their bytes, and the write lands in the Rules page's undo history). A
rule set that would not load writes nothing, to the files or the record.
Running it again after a full split finds nothing to move.
"""
import re
import sqlite3
import tempfile
from collections import defaultdict
from fnmatch import fnmatchcase
from pathlib import Path

from . import db, rules_app
from .backup import backup_record
from .paths import personal_rules_dir
from .rules_app import categorise

OLD = "Transport"
PUBLIC = "Transport (public)"
PRIVATE = "Transport (private)"

# Tried in order; the first that matches the text wins. Each is (pattern,
# category, why). The text is a rule's pattern and payee, or a row's printed
# text and payee, upper-cased.
CLASSES = [
    # GrabPay top-ups: the wallet is not read, and Grab rides are most of what
    # it pays for, so they sit with Grab (data/rules/30-merchants.csv says the same)
    (r"\bGPC-", PRIVATE, "GrabPay top-up"),
    (r"\b(GRAB|GOJEK|GOPAY|TADA|RYDE|UBER|UBR|LYFT|BOLT|WAYMO|DIDI|CAREEM|CABIFY|FREENOW)\b",
     PRIVATE, "ride hailing"),
    (r"\b(COMFORT ?DELG\w*|COMFORT TAX\w*|CDG|CITYCAB|TRANS-?CAB|PREMIER TAXI|STRIDES|ZIG|TAXI|CAB)\b",
     PRIVATE, "taxi"),
    (r"\b(HELLO ?RIDE|LIME|VOI|BIRD|TIER|ANYWHEEL|SG ?BIKE|NEURON|BEAM|DOTT|CITI ?BIKE)\b",
     PRIVATE, "bike or scooter share"),
    (r"\b(HERTZ|AVIS|SIXT|EUROPCAR|ENTERPRISE RENT|BUDGET RENT|GETGO|BLUE ?SG|CAR RENTAL|CAR RENT)\b",
     PRIVATE, "car rental"),
    (r"\b(SHELL|ESSO|CALTEX|SPC|SINOPEC|PETRONAS|PETROL|FUEL|CIRCLE K|BP|ARAL|TOTAL ?ENERGIES|"
     r"Q8|OMV|CHEVRON|EXXON|MOBIL)\b", PRIVATE, "petrol"),
    (r"\b(PARKING|CAR ?PARK|ERP)\b", PRIVATE, "parking or ERP"),
    (r"(\bBUS/MRT\b|\bMRT/BUS\b|\b(MRT|LRT|SMRT|SBS TRANSIT|SIMPLYGO|EZ-?LINK|TRANSITLINK|CAUSEWAY ?LINK\w*)\b)",
     PUBLIC, "Singapore public transport"),
    # public transport abroad: the generic words in a few languages, then the
    # operators and fare cards a statement prints most often
    (r"\b(TRANSIT|METRO|SUBWAY|RAIL\w*|TRAIN\w*|BAHN\w*|TRAM\w*|BUS|FERRY|FERRIES|STATION|"
     r"TRANSPORT\w*|TRANSPO|BILLETT\w*|NADRAZI|OFFENTLIGE|TRAFIK\w*|"
     r"MTA|NYCT|MBTA|WMATA|SEPTA|METRA|LIRR|PATH|CALTRAIN|MUNI|BART|AMTRAK|CLIPPER|VENTRA|NJ ?TRANSIT|"
     r"PRESTO|TRIMET|OYSTER|TFL|SNCF|RATP|RENFE|TMB|TRENITALIA|ATAC|ACTV|VAPORETTO|SBB|OBB|DB|BVG|MVV|HVV|"
     r"NS|REIZIGERS|GVB|SNCB|NMBS|STIB|DSB|DOT|SL|VASTTRAFIK|SKANETRAFIKEN|ARLANDA|FLYTOGET|RUTER\w*|"
     r"SKYSS|ENTUR|VY|HSL|DPP|PKP|MAV|BKK|OCTOPUS|MTR|SUICA|PASMO|JR|KORAIL|MYKI|OPAL|RAPID ?KL)\b",
     PUBLIC, "public transport"),
]
_CLASSES = [(re.compile(p), cat, why) for p, cat, why in CLASSES]


def classify(*texts):
    """(category, why) for the first class whose pattern the texts match, or
    (None, "") when none does."""
    text = " ".join(" ".join((t or "").replace("*", " ").split()).upper() for t in texts if t)
    for pattern, cat, why in _CLASSES:
        if pattern.search(text):
            return cat, why
    return None, ""


# ---------------------------------------------------------------- planning

def _text(r):
    return r.get("merchant") or r.get("counterparty") or r.get("memo") or r.get("bank_txn_type") or ""


def _pinned(rows_by_key, pattern):
    """Every row a key rule pins, matched as the categoriser matches it: case
    and spacing ignored, and the pattern a glob. `rows_by_key` is keyed by
    categorise.norm(txn_key)."""
    glob = categorise.norm(pattern)
    if glob in rows_by_key:
        return [rows_by_key[glob]]
    return [r for k, r in rows_by_key.items() if fnmatchcase(k, glob)]


def _classify_key_rule(rows, payee):
    """(category, why) for a key rule from the rows it pins. A glob that pins
    rows the classifier files differently (a bus fare and a taxi) has no one
    answer, so the rule stays Transport."""
    if not rows:
        return classify("", payee)
    seen = {classify(_text(r), payee) for r in rows}
    if len({to for to, _ in seen}) > 1:
        return None, "its rows differ"
    return min(seen, key=lambda s: s[1])


def _rules_plan(rows_by_key):
    """What happens to each personal rule that says Transport. Returns (changes,
    files): changes is one dict per such rule, `to` None when it stays; files
    is the rules_app.RulesFiles that change, not yet written."""
    changes, files = [], []
    d = personal_rules_dir()
    for path in sorted(d.glob("*.csv")) if d.is_dir() else []:
        f = rules_app.RulesFile(path.name)
        changed = False
        for line in range(2, len(f.records) + 1):     # the record numbers the Rules page lists
            rule = f.rule(line)
            if not rule or rule["category"] != OLD:
                continue
            texts = [rule["pattern"], rule["payee"]]
            if rule["match_on"] == "key":
                # a key rule's pattern is a txn_key: what it pins is the row's text
                to, why = _classify_key_rule(_pinned(rows_by_key, rule["pattern"]), rule["payee"])
            else:
                to, why = classify(*texts)
            changes.append({"file": f.name, "line": line, "rule": f"{rule['match_on']}:{rule['pattern']}",
                            "payee": rule["payee"], "to": to, "why": why})
            if to:
                f.replace(line, {**rule, "category": to})
                changed = True
        if changed:
            files.append(f)
    return changes, files


def _names(conn):
    """Every category name a rule may use against this record: the shipped
    list, the record's own, the old names it maps, and the two new ones."""
    from .parsers import categorise
    names = [*categorise.KNOWN_CATEGORIES, PUBLIC, PRIVATE]
    names += [r[0] for r in conn.execute("SELECT name FROM category")]
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'category_alias'").fetchone():
        names += db.category_alias_names(conn)
    return list(dict.fromkeys(names))


def _rows_plan(conn, files):
    """Each live Transport row, run through the whole rule set as the next
    load will run it, with the personal rules as `files` leaves them. Returns
    (moves, stays): moves is [(row, new category, rule)], stays is
    [(row, why)]. The trial set is read from the system's temp folder, so a
    dry run writes nothing in the data folder."""
    from .parsers import categorise
    from .paths import REPO_RULES
    rows = [r for r in rules_app.db_rows(conn) if r["category"] == OLD]
    new = {f.name: f.data() for f in files}
    d = personal_rules_dir()
    with tempfile.TemporaryDirectory(prefix="kopifund-split-") as tmp:
        personal = sorted(d.glob("*.csv")) if d.is_dir() else []
        for f in personal:
            (Path(tmp) / f.name).write_bytes(new[f.name] if f.name in new else f.read_bytes())
        try:
            rules = categorise.load_rules([REPO_RULES] + ([Path(tmp)] if personal else []), _names(conn))
        except SystemExit as e:
            raise SplitRefused(f"The rules would not load: {e}") from None
    trial = categorise.categorise_rows([dict(r) for r in rows], rules)
    moves, stays = [], []
    for old, new in zip(rows, trial):
        if new["category"] in (PUBLIC, PRIVATE):
            moves.append((old, new["category"], new["rule"]))
        elif new["category"] == OLD:
            stays.append((old, f"{new['rule']} still says {OLD}"))
        elif new["category"]:
            stays.append((old, f"{new['rule']} says {new['category']}; this command leaves it, and make load files it there"))
        else:
            stays.append((old, "no rule matches it now; this command leaves it, and make load leaves it with no category"))
    return moves, stays


class SplitRefused(Exception):
    """The split cannot go ahead, with the reason."""


# ---------------------------------------------------------------- running

def run(record, dry_run=True, out=print):
    """Plan the split and, unless `dry_run`, carry it out. `record` is the
    record's path; the personal rules are the data folder's rules/. Prints the
    report through `out` and returns a summary dict."""
    record = Path(record)
    if not record.exists():
        raise SplitRefused(f"No record at {record}: load your statements first")
    if dry_run:
        # read only: a dry run never writes, not even the schema upgrade
        conn = sqlite3.connect(f"file:{record}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
    else:
        conn = db.connect(record)
    try:
        rows_by_key = {categorise.norm(r["txn_key"]): r for r in rules_app.db_rows(conn)}
        changes, files = _rules_plan(rows_by_key)
        moves, stays = _rows_plan(conn, files)
        budgets = conn.execute("""SELECT COUNT(*) FROM budget b JOIN category c ON c.id = b.category_id
                                  WHERE c.name = ? AND b.planned_minor IS NOT NULL""", (OLD,)).fetchone()[0]
        backups = []
        if not dry_run and (moves or files):
            backups = _apply(conn, record, files, moves)
        summary = {"rules_moved": sum(1 for c in changes if c["to"]),
                   "rules_left": sum(1 for c in changes if not c["to"]),
                   "rows_moved": len(moves), "rows_left": len(stays), "budgets": budgets,
                   "backups": [str(b) for b in backups]}
        report(changes, moves, stays, budgets, dry_run, backups, out)
        return summary
    finally:
        conn.close()


def _apply(conn, record, files, moves):
    """Copy the record, then in one transaction move the rows and write the
    rules through rules_app (checked, atomic, in the undo history). A rule set
    that does not load raises before any file is written, and the record rolls
    back with it."""
    backups = [backup_record(conn, record, "before-split-transport")]
    # the rules lock before the record's write lock, the order every rules
    # write takes them in (rules_app._commit): the app's server may be saving
    # a rule on the same data folder, and with the order reversed each would
    # hold one lock and wait on the other until SQLite gave up
    with rules_app._RULES_LOCK, db.transaction(conn):
        db.seed_categories(conn)                    # the two new categories, in Getting around
        ids = db.category_ids(conn)
        for row, to, _ in moves:
            conn.execute("UPDATE txn SET category_id = ? WHERE txn_key = ?", (ids[to], row["txn_key"]))
        if files:
            rules_app.commit(conn, files, "split-transport",
                             f"Split Transport: your Transport rules moved to {PUBLIC} or {PRIVATE}")
    return backups


# ---------------------------------------------------------------- report

def _money(cents):
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    return f"{sign}{cents // 100:,}.{cents % 100:02d}"


def _table(head, rows):
    widths = [max(len(str(x)) for x in col) for col in zip(head, *rows)]
    line = lambda r: "  ".join(str(x).ljust(w) for x, w in zip(r, widths)).rstrip()  # noqa: E731
    return [line(head), line(["-" * w for w in widths]), *(line(r) for r in rows)]


def report(changes, moves, stays, budgets, dry_run, backups, out=print):
    out(f"Transport split{' (dry run: nothing written)' if dry_run else ''}")
    out("")
    out(f"Personal rules that say {OLD}: {len(changes)}")
    if changes:
        for ln in _table(["file", "line", "rule", "payee", "moves to", "why"],
                         [[c["file"], c["line"], c["rule"], c["payee"], c["to"] or f"stays {OLD}",
                           c["why"] or "not classified"] for c in changes]):
            out("  " + ln)
    out("")
    out(f"Transactions in {OLD}: {len(moves) + len(stays)}")
    groups = defaultdict(lambda: [0, 0])
    for row, to, _ in moves:
        g = groups[(to, row["payee"] or _text(row)[:28])]
        g[0] += 1
        g[1] += -(row["amount_sgd_minor"] or 0)
    if moves:
        for ln in _table(["moves to", "payee or text", "rows", "SGD out"],
                         [[to, label, n, _money(v)] for (to, label), (n, v) in
                          sorted(groups.items(), key=lambda kv: (kv[0][0], -kv[1][0], kv[0][1]))]):
            out("  " + ln)
        for to in (PUBLIC, PRIVATE):
            out(f"  {sum(1 for _, t, _ in moves if t == to)} to {to}")
    if stays:
        out("")
        out(f"Not classified, left in {OLD}: {len(stays)}")
        left = defaultdict(lambda: [0, 0])
        for row, why in stays:
            g = left[(row["payee"] or _text(row)[:28], why)]
            g[0] += 1
            g[1] += -(row["amount_sgd_minor"] or 0)
        for ln in _table(["payee or text", "rows", "SGD out", "why"],
                         [[label, n, _money(v), why] for (label, why), (n, v) in
                          sorted(left.items(), key=lambda kv: (-kv[1][0], kv[0][0]))]):
            out("  " + ln)
    if budgets:
        out("")
        out(f"{budgets} month(s) of {OLD} budget stay on {OLD}; plan the two new categories on Plan.")
    out("")
    if dry_run:
        out("Nothing was written. Run it without --dry-run to do this: the record is copied to "
            "backups/ first, and the rules change can be undone on the Rules page.")
    else:
        for b in backups:
            out(f"backup: {b}")
        if changes and any(c["to"] for c in changes):
            out("The rules change is in the Rules page's undo history.")
        if stays or any(not c["to"] for c in changes):
            out(f"Decide the rows and rules left in {OLD}, then delete {OLD} on the Categories "
                "page and move what is left to the category you choose.")
        else:
            out(f"{OLD} is empty now: delete it on the Categories page whenever you like.")
        out("The record has the split now, but the stat pages show it only after the stats rerun: "
            "run python3 server/finance.py analyse (make load does it too).")
