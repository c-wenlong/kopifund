"""Rules from the browser (G7).

Rules stay ordered CSV tiers (SPEC.md): the shared tiers ship in the repo's
rules/, and everything the app adds goes into the data folder's rules/ as a
personal tier, where it wins ties over the shipped merchant rules. Saving a
rule loads the whole rule set with the change, exactly as a run would, before
anything is written; a change that would break the set is refused.

Every write goes through `_commit`: each file is replaced atomically, records
the write does not touch keep their bytes, and the files' previous content is
kept in the data folder's rules-history/ so the Rules page can undo it.
"""
import csv
import hashlib
import io
import json
import os
import re
import secrets
import sqlite3
import tempfile
import threading
from datetime import datetime
from fnmatch import fnmatchcase
from pathlib import Path

try:
    import fcntl
except ImportError:                      # not on Windows: one process's thread lock only
    fcntl = None

from . import account_names, dates, money
from .atomic import write_private
from .paths import REPO_RULES, cache_dir, data_root, personal_rules_dir, rules_history_dir
from .parsers import categorise

# which personal file each kind of rule the app can add goes into
PERSONAL_FILE = {"merchant": "merchants.csv", "counterparty": "people.csv",
                 "memo": "people.csv", "reference": "people.csv", "key": "keys.csv"}
# which personal file the rule editor writes each tier into: the tier decides
TIER_FILE = {**PERSONAL_FILE, "type": "types.csv", "flow": "types.csv"}
# how many writes the undo history keeps
HISTORY_KEEP = 50


class _RulesLock:
    """Every read-write-check-restore of a personal rules file, and the undo
    history id it takes, happens under this one lock, so two writes cannot
    drop each other's rule. The server answers requests on threads, so it is
    a thread lock; the app and `make serve` (or a finance.py run) may share a
    data folder, so it is also a file lock (`flock`) on the data folder's
    `.rules.lock`, as the `.env` (keys._locked) and the stats rebuild
    (monthly._rebuild_lock) are. The lock file sits beside rules/, not in
    it, so the rules folder holds only the owner's files. Not reentrant:
    nothing under it takes it again."""

    def __init__(self):
        self._thread = threading.Lock()
        self._fd = None

    def __enter__(self):
        self._thread.acquire()
        try:
            d = data_root()
            d.mkdir(parents=True, exist_ok=True)
            fd = os.open(d / ".rules.lock", os.O_RDWR | os.O_CREAT, 0o600)
            try:
                if fcntl is not None:
                    fcntl.flock(fd, fcntl.LOCK_EX)
            except BaseException:
                os.close(fd)
                raise
            self._fd = fd
        except BaseException:
            self._thread.release()
            raise
        return self

    def __exit__(self, *exc):
        fd, self._fd = self._fd, None
        try:
            os.close(fd)                 # closing it lets the flock go
        finally:
            self._thread.release()

    def locked(self):
        return self._thread.locked()


_RULES_LOCK = _RulesLock()


class RuleRefused(Exception):
    """The rule would not load, with the loader's own reason."""


def rule_paths():
    personal = personal_rules_dir()
    return [REPO_RULES] + ([personal] if any(personal.glob("*.csv")) else [])


def categories(conn):
    names = [r[0] for r in conn.execute("SELECT name FROM category ORDER BY COALESCE(sort_order, 1e9), name")]
    return names or list(categorise.KNOWN_CATEGORIES)


def rule_categories():
    """[(file, is personal, category)] for every rule in the shared and the
    personal tiers, comments left out: what a category change would touch."""
    personal = personal_rules_dir().resolve()
    out = []
    for f in categorise.rule_files(rule_paths()):
        records = _records(f)
        header = [h.strip() for h in records[0]] if records else []
        if "category" not in header:
            continue
        i = header.index("category")
        for rec in records[1:]:
            if rec and len(rec) > i and not rec[0].lstrip().startswith("#") and rec[i].strip():
                out.append((f.name, f.resolve().parent == personal, rec[i].strip()))
    return out


def recategorised(mapping):
    """The personal rules files with every rule whose category is a key of
    `mapping` pointed at its value instead, as RulesFiles not yet written
    (only the files that change). A record that does not change keeps its
    bytes; a comment stays a comment."""
    d = personal_rules_dir()
    out = []
    for path in sorted(d.glob("*.csv")) if d.is_dir() else []:
        f = RulesFile(path.name)
        changed = False
        for line in range(2, len(f.records) + 1):
            rule = f.rule(line)
            if rule and rule["category"] in mapping:
                f.replace(line, {**rule, "category": mapping[rule["category"]]})
                changed = True
        if changed:
            out.append(f)
    return out


def rewrite_categories(conn, mapping, summary):
    """Point every personal rule whose category is a key of `mapping` at its
    value instead (a renamed or deleted category). It is one write through
    `_commit`, like any edit on the Rules page: the set must load with it
    first, each file is replaced in one step, and the old content goes to the
    undo history, so the Rules page can take it back. The shared rules in the
    repo are never written: the record's category_alias sends their old name
    to the same place. Returns how many files changed; RuleRefused when the
    set would not load, and nothing is written then."""
    with _RULES_LOCK:
        files = recategorised(mapping)
        if files:
            _commit(conn, files, "recategorise", summary)
        return len(files)


def commit(conn, files, action, summary):
    """`_commit` for a caller outside this module (server/finance/transport_split.py):
    the same checks, atomic writes and undo history. The caller holds
    _RULES_LOCK, taken before its record transaction: every rules write takes
    the rules lock first and the record's write lock second, so a Rules page
    save in the server and this caller in another process never hold one
    each while waiting on the other.
    The caller read `files` earlier, maybe in another process than the app's
    server, so each file must still hold what was read (RulesFile.before):
    one the Rules page saved since is refused rather than overwritten, and
    its edit kept."""
    d = personal_rules_dir()
    for f in files:
        path = d / f.name
        now = path.read_bytes() if path.is_file() else None
        if now != f.before:
            raise RuleRefused(f"{f.name} changed since it was read (saved on the Rules page?): "
                              "nothing was written; run it again")
    return _commit(conn, files, action, summary)


def db_rows(conn, key=None):
    """The record's live rows in the shape the categoriser reads; with `key`,
    only that row. Each carries the keys it was loaded under before
    (`_former_keys`), so a keys.csv rule on an old key previews as a load
    applies it."""
    from . import db
    former = db.former_keys(conn)
    out = [{"txn_key": r["txn_key"], "date": r["txn_date"], "currency": r["currency"],
            "amount": f"{r['amount_minor'] / 100:.2f}", "amount_minor": r["amount_minor"],
            "amount_sgd_minor": r["amount_sgd_minor"], "account": r["account_name"] or r["account_no"],
            "merchant": r["merchant_raw"],
            "counterparty": r["counterparty"], "memo": r["memo"], "reference": r["reference"],
            "bank_txn_type": r["bank_txn_type"], "flow_class": r["flow_class"],
            "account_no": r["account_no"], "payee": r["payee_name"] or "",
            "category": r["category_name"] or "", "rule": r["rule"], "synthetic": r["is_synthetic"]}
           for r in (conn.execute("SELECT * FROM v_txn WHERE txn_key = ?", (key,)) if key is not None
                     else conn.execute("SELECT * FROM v_txn"))]
    for r in out:
        if r["txn_key"] in former:
            r["_former_keys"] = former[r["txn_key"]]
    return out


NEEDS_SHOWN = 60   # the needs-a-rule groups GET /api/rules sends, biggest first


def needs_rule(conn, limit=NEEDS_SHOWN):
    """Unmatched External rows grouped by the text they print, biggest first,
    each with a suggested rule. `amount` is SGD cents; a row whose statement
    printed no FX rate adds nothing to it."""
    return needs_queue(conn, limit)[0]


def needs_queue(conn, limit=NEEDS_SHOWN):
    """(needs_rule's list, how many groups there are in all), from one pass
    over the record: what GET /api/rules sends as `needs` and `needs_total`."""
    groups = _needs_groups(conn)
    out = sorted(groups.values(), key=lambda g: -g["amount"])[:limit]
    for g in out:
        g["suggestion"] = (suggest(g["text"], g["field"]) if g["field"] != "key" else
                           {"match_on": "key", "pattern": g["keys"][0], "payee": "", "kind": "Merchant"})
    return out, len(groups)


def needs_rule_count(conn):
    """G18: how many groups the Needs a rule queue holds in all, before
    needs_rule's cut: the Rules page's total (`needs_total`) and Needs your
    eye's count, so the two always agree."""
    return len(_needs_groups(conn))


def _needs_groups(conn):
    """{group key: group} for every unmatched row, as needs_rule lists them."""
    groups = {}
    for r in db_rows(conn):
        if not _unmatched(r):
            continue
        field, stem = _group_of(r)
        if not field:
            # a statement line that prints no text can only be pinned by its key
            g = groups.setdefault("key " + r["txn_key"], {
                "text": f"{r.get('bank_txn_type') or 'no text'}, {dates.day_label(r['date'])}", "field": "key", "rows": 1,
                "amount": abs(r["amount_sgd_minor"] or 0), "first": r["date"], "last": r["date"],
                "keys": [r["txn_key"]]})
            continue
        g = groups.setdefault(stem, {"text": stem, "field": field, "rows": 0, "amount": 0,
                                     "first": r["date"], "last": r["date"], "keys": []})
        g["rows"] += 1
        g["amount"] += abs(r["amount_sgd_minor"] or 0)
        g["first"], g["last"] = min(g["first"], r["date"]), max(g["last"], r["date"])
        if len(g["keys"]) < 5:
            g["keys"].append(r["txn_key"])
    return groups


def _unmatched(r):
    return not r["rule"] and r["flow_class"] == "External" and not r["synthetic"]


def _group_of(r):
    """(field, text) the needs-rule queue files a row under: the first text the
    statement prints, cut to 44 characters, or ("", "") when it prints none."""
    field = next((f for f in ("merchant", "counterparty", "memo") if r.get(f)), "")
    return field, (r[field][:44] if field else "")


def group_rows(conn, text="", key="", limit=50, folders=()):
    """The rows behind one needs-rule group, newest first, so one of them (a
    gift, a one-off payment) can be set on its own by its key without a
    pattern that would also catch the rest. A group is its text, whichever
    field printed it, as both queues group; a row that prints no text is in
    the group of its bank code, as the upload check groups it. `key` asks for
    that one row, matched or not. Each row carries the key rule that sets it
    (the first that matches, as the categoriser tries them) and whether a load
    has applied it yet. `folders` (the config's) name an account the record
    knows only by its number, as Your profile names it."""
    if key:
        hits = db_rows(conn, key)
    elif text:
        hits = [r for r in db_rows(conn) if _unmatched(r)
                and (_group_of(r)[1] or (r.get("bank_txn_type") or "")[:44]) == text]
    else:
        raise RuleRefused("Ask for a key, or a group's text")
    hits.sort(key=lambda r: (r["date"], r["txn_key"]), reverse=True)
    # a set that will not load says why here, before a save would fail on it
    keyed = [r for r in _load_all(conn) if r["match_on"] == "key"]
    shown = account_names.record_names(conn, folders)
    # G18: each row's bank, for its mark beside the account, as Transactions finds it
    known = [dict(a) for a in conn.execute("SELECT name, account_no, institution FROM account ORDER BY name")]
    banks = {a["account_no"] or a["name"]: d["bank"] for a, d in zip(known, account_names.describe(known, folders))}
    rows = []
    for r in hits[:limit]:
        pin = _key_rule(keyed, r)
        field = _group_of(r)[0] or "bank_txn_type"
        rows.append({"key": r["txn_key"], "date": r["date"], "amount": r["amount_minor"],
                     "currency": r["currency"], "amount_sgd": r["amount_sgd_minor"],
                     # G13: the name it is shown by, never the full number
                     "account": (shown.get(r["account_no"])
                                 or account_names.label(r["account"], r["account_no"], None, folders)),
                     "bank": (banks.get(r["account_no"]) or banks.get(r["account"] or "")
                              or account_names.bank(r["account"], r["account_no"], None, folders)),
                     "text": _text(r), "field": field,
                     "payee": r["payee"], "category": r["category"],
                     # G13: the pattern rule "Apply to similar rows" starts from
                     "similar": similar(r),
                     "pinned": pin and {**{k: pin[k] for k in ("payee", "category", "file", "line", "kind", "country")},
                                        "applied": categorise.norm(r["rule"]) == categorise.norm(f"key:{pin['pattern']}")}})
    return {"rows": rows, "total": len(hits), "categories": categories(conn), "kinds": list(categorise.KINDS),
            "tiers": list(categorise.MATCH_ON), "tier_files": TIER_FILE}


def similar(r):
    """A pattern rule that would catch the rows like this one (G13): the
    needs-rule queue's suggestion from the first text the row prints. None
    for a row that prints only a bank code: a type rule on it would also
    catch every row of that type that names a merchant, since the type tier
    is tried first."""
    field = _group_of(r)[0]
    return suggest(r[field][:44], field) if field else None


def _key_rule(rules, row):
    """The key rule the categoriser gives a record row: the first that matches
    its txn_key, then any key it was loaded under before (`_former_keys`)."""
    return next((r for v in categorise.tier_values(row, "key") for r in rules
                 if r["match_on"] == "key" and fnmatchcase(v, r["glob"])), None)


def _text(r):
    return r.get("merchant") or r.get("counterparty") or r.get("memo") or r.get("bank_txn_type") or ""


def pin(conn, data):
    """Set one row's category, and a payee when given, as a key rule in the
    personal keys.csv: a wedding gift or a donation is filed without a pattern
    rule that would also catch the next transfer to that person. The note
    records the row as the statement prints it, and why."""
    data = data if isinstance(data, dict) else {}
    key = str(data.get("key") or "").strip()
    row = next(iter(db_rows(conn, key)), None) if key else None
    if row is None:
        raise RuleRefused("No row with that key in the record: load the statement first")
    payee = str(data.get("payee") or "").strip()
    cents = abs(row["amount_minor"])
    amount = (f"{'+' if row['amount_minor'] > 0 else ''}"
              f"{'' if row['currency'] == 'SGD' else row['currency'] + ' '}{cents // 100}.{cents % 100:02d}")
    why = " ".join(str(data.get("note") or "").split()) or "this row only, set from the app"
    rule = {"match_on": "key", "pattern": key, "payee": payee, "kind": data.get("kind") if payee else "",
            "category": data.get("category"), "notes": f"{amount} {_text(row)} {row['date']}: {why}"}
    with _RULES_LOCK:
        if not rule["category"]:
            raise RuleRefused("Choose a category")
        rules = _load_all(conn)
        held = _key_rule(rules, row)
        # G13: the row's own pin in keys.csv (set from the app, this key exactly,
        # or a key the row had before a re-key) is replaced, so the category can
        # be picked again in one tap; any other key rule that sets the row stays
        # the Rules page's to change
        mine = {categorise.norm(k) for k in (key, *row.get("_former_keys", ()))}
        own = held if held and held["file"] == PERSONAL_FILE["key"] and \
            categorise.norm(held["pattern"]) in mine else None
        # keys.csv loads first, and the first rule naming a payee sets its kind,
        # country and account (snapshot.payees_from_rules): a payee that other
        # rules already name keeps them. The pin being replaced is not one of
        # them, so a kind chosen under More takes effect
        known = next((r for r in rules if payee and r["payee"] == payee and r is not own), None)
        if known:
            rule.update({k: known.get(k) or "" for k in ("kind", "country", "account_no")})
        elif payee and not rule["kind"]:
            # a pick that names no kind keeps the one the row's pin gave its payee
            rule["kind"] = own["kind"] if own and own["payee"] == payee and own["kind"] else "Merchant"
        shown = money.label(row["amount_minor"], row["currency"], plus=True)
        summary = f"Set the {dates.day_label(row['date'])} row {shown} to {rule['category']}"
        if not held:
            return {**_add(conn, rule, "pin", summary), "pinned": _pinned(rule)}
        f = RulesFile(PERSONAL_FILE["key"])
        line = _own_pin(f, own["pattern"]) if own else None
        if line is None:
            raise RuleRefused(f"{held['file']} line {held['line']} already sets this row; "
                              "remove that rule in Rules first")
        old = f.rule(line)
        if "note" not in data:
            # a pick of the category alone keeps the note the row's pin was saved with
            rule["notes"] = old["notes"] or rule["notes"]
        # what the pick does not set (recurring, marked on the Rules page; a country or
        # account no other rule gives the payee) stays as the pin had it
        for k in ("recurring", "country", "account_no"):
            if not rule.get(k) and old.get(k) and old["payee"] == rule["payee"]:
                rule[k] = old[k]
        f.replace(line, _row(rule)[1])
        # a pin on another key the row had before would win again after a later
        # re-key (former keys are tried in a fixed order), so the row keeps one
        for k in row.get("_former_keys", ()):
            while (extra := _own_pin(f, k)) is not None:
                f.delete(extra)
        if f.data() == f.before:
            return {"file": f.name, "unchanged": True, "change": None, "relinked": {"changed": 0, "written": 0},
                    "pinned": _pinned(rule)}
        linked = {}
        _commit(conn, [f], "pin", summary, linked=linked)
        return {"file": f.name, "change": latest_change(), "relinked": linked, "pinned": _pinned(rule)}


def _pinned(rule):
    """What a pin set, as the row's `pinned` reads: the page builds the rule
    for the rows like it from this without asking for the row again (G13)."""
    return {k: rule.get(k) or "" for k in ("payee", "kind", "country", "category")}


def _own_pin(f, key):
    """The record number of the first rule in `f` that pins `key` itself, or None."""
    for line in range(2, len(f.records) + 1):
        r = f.rule(line)
        if r and r["match_on"] == "key" and categorise.norm(r["pattern"]) == categorise.norm(key):
            return line
    return None


def suggest(text, field):
    """A first guess at a rule for one unmatched stem: the leading words before
    any digits or ids, as a pattern and a payee name."""
    words = []
    for w in re.split(r"[\s*]+", categorise.norm(text)):
        # stop at an id (a digit) or a separator with no letters ("-", "@")
        if not w or re.search(r"\d", w) or not re.search(r"[A-Z]", w) or len(words) >= 3:
            break
        words.append(w)
    if not words:
        words = [categorise.norm(text)[:12]]
    pattern = " ".join(words) + "*"
    payee = " ".join(w.capitalize() if w.isalpha() else w for w in words)
    kind = "Person" if field in ("counterparty",) else "Merchant"
    return {"match_on": field, "pattern": pattern, "payee": payee, "kind": kind}


def _records(path):
    """A rules file as CSV records (a quoted note may span lines), header first."""
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.reader(fh))


def split_records(text):
    """A rules file's text as [(raw, cells, line)], one per CSV record (a quoted
    note may span lines), header first. Joining the raws gives the text back,
    and `line` is the line number the loader gives the record: where it ends."""
    lines = list(io.StringIO(text, newline=""))
    read = 0

    def feed():
        nonlocal read
        for ln in lines:
            read += 1
            yield ln

    out, start = [], 0
    for cells in csv.reader(feed()):
        out.append(("".join(lines[start:read]), cells, read))
        start = read
    return out


def describe(rule):
    """One rule in a few words, for the undo history."""
    who = f" ({rule['payee']})" if rule.get("payee") else ""
    return f"{rule['match_on']} {rule['pattern']}{who}"


def revision(raw):
    """A short fingerprint of one record as the file holds it. The page sends
    back the one it read with an edit or a delete, and a record that no longer
    reads the same (another tab saved it) is refused rather than overwritten."""
    return hashlib.sha256(raw.rstrip("\r\n").encode("utf-8", "surrogateescape")).hexdigest()[:16]


def unchanged(edit, rule, raw):
    """Whether the record a page read, named by `edit` ({revision?,
    pattern?}), is still `rule` on that line: every fingerprint given must
    agree, and at least one must be given."""
    if rule is None or not isinstance(edit, dict):
        return False
    given = [k for k in ("revision", "pattern") if edit.get(k) is not None]
    return bool(given) and all(
        (revision(raw) if k == "revision" else rule["pattern"]) == edit[k] for k in given)


class RulesFile:
    """One personal rules file changed in memory before `_commit` writes it.
    A record the change does not touch keeps its bytes, so a hand-kept file
    stays as its owner wrote it. `line` is a record number with the header as
    1, as the Rules page counts (rules_editor.overview)."""

    def __init__(self, name):
        self.name = Path(name).name
        path = personal_rules_dir() / self.name
        self.before = path.read_bytes() if path.is_file() else None
        text = self.before.decode("utf-8") if self.before else ""
        self.records = [[raw, cells] for raw, cells, _ in split_records(text)]
        if not self.records:
            self.records = [[",".join(categorise.COLUMNS) + "\n", list(categorise.COLUMNS)]]
        self.eol = "\r\n" if self.records[0][0].endswith("\r\n") else "\n"

    def header(self):
        return [h.strip() for h in self.records[0][1]]

    def rule(self, line):
        """The rule at record `line` as a dict, or None for the header, a blank
        line, a comment or a number past the end."""
        try:
            i = int(line) - 1
        except (TypeError, ValueError):
            return None
        if not 1 <= i < len(self.records):
            return None
        cells = self.records[i][1]
        if not any(c.strip() for c in cells) or cells[0].lstrip().startswith("#"):
            return None
        out = {k: "" for k in categorise.COLUMNS}
        out.update({k: cells[j].strip() for j, k in enumerate(self.header()) if j < len(cells)})
        return out

    def _record(self, rule):
        """[raw, cells] for a rule, in this file's columns; an optional column
        the rule needs and the header lacks is added to the header."""
        header = self.header()
        missing = [k for k in categorise.OPTIONAL_COLUMNS if rule.get(k) and k not in header]
        if missing:
            header += missing
            self.records[0] = [self._line(header), header]
        cells = [str(rule.get(k, "") or "") for k in header]
        return [self._line(cells), cells]

    def _line(self, cells):
        buf = io.StringIO()
        csv.writer(buf, lineterminator=self.eol).writerow(cells)
        return buf.getvalue()

    def replace(self, line, rule):
        record = self._record(rule)
        self.records[int(line) - 1] = record

    def delete(self, line):
        del self.records[int(line) - 1]

    def insert(self, rule):
        """Add a rule after this file's last rule for the same payee, so one
        payee's rules stay together, else at the end. Returns its line."""
        record = self._record(rule)
        at = len(self.records)
        if rule.get("payee"):
            at = next((i + 1 for i in range(len(self.records) - 1, 0, -1)
                       if (self.rule(i + 1) or {}).get("payee") == rule["payee"]), at)
        if not self.records[at - 1][0].endswith(("\n", "\r")):
            self.records[at - 1][0] += self.eol
        self.records.insert(at, record)
        return at + 1

    def data(self):
        """The file's new bytes, or None for a file that did not exist and
        still holds no rule."""
        if self.before is None and len(self.records) == 1:
            return None
        return "".join(raw for raw, _ in self.records).encode("utf-8")


def remove(file, line, pattern=None, revision=None, conn=None):
    """Take one personal rule out; with `conn`, the record's rows follow at once."""
    with _RULES_LOCK:
        return _remove(file, line, pattern, revision, conn)


def add_many(conn, rules):
    """Add several rules (accepted AI suggestions, G12) and load the set once:
    all of them go in, or, when the set would not load, none do."""
    if not isinstance(rules, list) or not rules:
        raise RuleRefused("No rules to add")
    with _RULES_LOCK:
        rows = [_row(r) for r in rules]
        files = {}
        for match_on, row in rows:
            name = PERSONAL_FILE[match_on]
            files.setdefault(name, RulesFile(name)).insert(row)
        summary = f"Added {describe(rows[0][1])}" if len(rows) == 1 else f"Added {len(rows)} rules"
        linked = {}
        rules_loaded = _commit(conn, list(files.values()), "add", summary, linked=linked)
        findings = categorise.lint(db_rows(conn), rules_loaded)
        return {"added": len(rows), "lint": {k: v[:10] for k, v in findings.items()},
                "change": latest_change(), "relinked": linked}


def _row(rule):
    """(match_on, csv row) for one rule from the app, or RuleRefused."""
    match_on = rule.get("match_on", "") if isinstance(rule, dict) else ""
    if match_on not in PERSONAL_FILE:
        raise RuleRefused(f"A rule from the app matches on one of {', '.join(PERSONAL_FILE)}")
    row = {k: str(rule.get(k, "") or "").strip() for k in categorise.COLUMNS}
    row["match_on"] = match_on
    if not row["pattern"]:
        raise RuleRefused("The pattern is empty")
    if not row["category"]:
        raise RuleRefused("Choose a category")
    if row["payee"] and row["kind"] not in categorise.KINDS:
        raise RuleRefused(f"kind is one of {', '.join(categorise.KINDS)}")
    return match_on, row


def _add(conn, rule, action="add", summary=None):
    """Add one rule to its personal file, once the whole set loads with it
    (a pin, G13); RuleRefused says why it would not. The caller holds
    _RULES_LOCK."""
    match_on, row = _row(rule)
    f = RulesFile(PERSONAL_FILE[match_on])
    f.insert(row)
    linked = {}
    _commit(conn, [f], action, summary or f"Added {describe(row)}", linked=linked)
    # G13: the token an Undo sends back, and what the record changed by
    return {"file": f.name, "change": latest_change(), "relinked": linked}


def _remove(file, line, pattern=None, revision=None, conn=None):
    """Take one personal rule out by file and line number (as the Rules page
    lists it, rules_editor.overview). With `pattern` or `revision`, the record on that line must
    still read as the page saw it, so a file changed since the page loaded
    loses the right rule or none."""
    f = RulesFile(file)
    if f.before is None:
        raise RuleRefused(f"No personal rules file {file!r}")
    try:
        index = int(line) - 1
    except (TypeError, ValueError):
        raise RuleRefused("No such rule") from None
    # the record number counts CSV records, so a quoted note that spans lines
    # is one record and stays whole
    if not 1 <= index < len(f.records) or not any(c.strip() for c in f.records[index][1]):
        raise RuleRefused("No such rule")
    held = f.rule(line)
    seen = {"pattern": pattern, "revision": revision}
    if (pattern is not None or revision is not None) and not unchanged(seen, held, f.records[index][0]):
        raise RuleRefused(f"{f.name} has changed since the page loaded: reload and try again")
    f.delete(line)
    # taking a rule out can only fix a set that will not load, never break one
    linked = {} if conn is not None else None
    _commit(conn, [f], "delete", f"Deleted {describe(held) if held else 'a line'} from {f.name}", check=False,
            linked=linked)
    return {"removed": True, "change": latest_change(),   # the token an Undo sends back
            "relinked": linked or {"changed": 0, "written": 0}}


def _load_all(conn):
    """The whole rule set, checked the way a run checks it: against the
    shipped category list, plus any category the user has added, plus the old
    names of ones they renamed or deleted (which the record still maps)."""
    return _load_from(rule_paths(), conn)


def _load_from(paths, conn):
    from .db import category_alias_names
    names = list(dict.fromkeys([*categorise.KNOWN_CATEGORIES,
                                *(categories(conn) + category_alias_names(conn) if conn else [])]))
    try:
        return categorise.load_rules(paths, names)
    except SystemExit as e:
        raise RuleRefused(str(e)) from None


# ---------------------------------------------------------------- writes and undo

def _trial(conn, changes):
    """The whole rule set as it would load with `changes` ({file: bytes or
    None}) made to the personal rules, read from a scratch copy in the cache,
    so nothing real is written until it loads. RuleRefused when it would not."""
    scratch = cache_dir()
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="rules-trial-", dir=scratch) as tmp:
        t = Path(tmp)
        d = personal_rules_dir()
        for f in (d.glob("*.csv") if d.is_dir() else []):
            if f.name not in changes:
                (t / f.name).write_bytes(f.read_bytes())
        for name, data in changes.items():
            if data is not None:
                (t / name).write_bytes(data)
        return _load_from([REPO_RULES] + ([t] if any(t.glob("*.csv")) else []), conn)


def _write_atomic(path, data):
    """Replace a file in one step, so a crash leaves the old file or the new
    one and never half of each; None removes it."""
    if data is None:
        path.unlink(missing_ok=True)
        return
    write_private(path, data)


def _commit(conn, files, action, summary, check=True, linked=None):
    """Write the RulesFiles that changed and keep their old content for undo.
    With `check` the whole set must load with the change first, and the set
    as loaded is returned. The caller holds _RULES_LOCK.

    G13: `linked`, a dict, asks for the record to follow in the same step: the
    rows the new set decides differently from the old one are relinked
    (server/finance/relink.py) as a load would link them, what they held is kept in
    the write's history entry so its Undo puts them back too, and `linked` is
    filled with what changed ({changed, written}). Files and record land
    together or not at all."""
    from . import db, relink
    changes = {f.name: f.data() for f in files if f.data() != f.before}
    if not changes:
        if linked is not None:
            linked.update(changed=0, written=0)
        return _load_all(conn) if check else None
    old_rules = None
    if linked is not None:
        try:
            old_rules = _load_all(conn)
        except RuleRefused:
            pass                         # a set that did not load: every row follows the new one
    rules = _trial(conn, changes) if check else None
    before = {f.name: f.before for f in files if f.name in changes}
    d = personal_rules_dir()
    d.mkdir(parents=True, exist_ok=True)
    done = []
    try:
        for name, data in changes.items():
            _write_atomic(d / name, data)
            done.append(name)
        new_rules = None
        if linked is not None:
            new_rules = rules
            if new_rules is None:
                try:
                    new_rules = _load_all(conn)
                except RuleRefused:
                    pass                 # still a set that will not load: the record waits for one that does
        if new_rules is None:
            _remember(action, summary, before, changes)
            if linked is not None:
                linked.update(changed=0, written=0)
        else:
            # the rows and the history entry commit together: a failure to
            # keep the entry rolls the rows back with the files
            kept = None
            try:
                with db.transaction(conn):
                    record = relink.rules(conn, old_rules, new_rules)
                    kept = _remember(action, summary, before, changes, record=record.data())
            except BaseException:
                # the rows rolled back: an entry for them would undo a write that never landed
                if kept is not None:
                    kept.unlink(missing_ok=True)
                raise
            linked.update(record.summary())
    except (OSError, sqlite3.Error) as e:
        for name in done:
            try:
                _write_atomic(d / name, before[name])
            except OSError:
                pass
        raise RuleRefused(f"Could not write the rules: {e}") from e
    return rules


def _sha(data):
    return None if data is None else hashlib.sha256(data).hexdigest()


def _history():
    """[(id, path)] of the kept writes, oldest first."""
    h = rules_history_dir()
    return sorted((int(p.stem), p) for p in (h.glob("*.json") if h.is_dir() else []) if p.stem.isdigit())


def _remember(action, summary, before, after, record=None):
    """One history entry: each file's content before the write, and a hash of
    it after, so undo can tell when a file was changed since by hand; and,
    when the record followed the write (G13), what its rows held before
    (relink.Change.data), so undo puts them back."""
    h = rules_history_dir()
    h.mkdir(mode=0o700, parents=True, exist_ok=True)
    kept = _history()
    n = kept[-1][0] + 1 if kept else 1
    # an id comes back once its entry is undone; the token never does, so an
    # Undo that names a write (G13) cannot take back a later one with its id
    entry = {"id": n, "token": secrets.token_hex(8),
             "at": datetime.now().isoformat(timespec="seconds"), "action": action, "summary": summary,
             "files": {name: {"before": None if before[name] is None
                              else before[name].decode("utf-8", "surrogateescape"),
                              "after": _sha(data)} for name, data in after.items()}}
    if record and (record.get("rows") or record.get("payees")):
        entry["record"] = record
    _write_atomic(h / f"{n:06d}.json", json.dumps(entry, indent=1).encode("utf-8"))
    # the write and its entry are kept now; an old entry that will not go
    # away is only clutter, so it never undoes the write
    for _, old in kept[:max(0, len(kept) + 1 - HISTORY_KEEP)]:
        try:
            old.unlink(missing_ok=True)
        except OSError:
            pass
    return h / f"{n:06d}.json"


def history(limit=20):
    """The newest writes that can be undone, newest first."""
    out = []
    for _, p in reversed(_history()):
        if len(out) >= limit:
            break
        try:
            e = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        out.append({k: e.get(k) for k in ("id", "at", "action", "summary")})
    return out


def latest_change():
    """The token of the newest kept write, or None; the caller holds
    _RULES_LOCK right after its own write, so it is that write's."""
    kept = _history()
    try:
        return json.loads(kept[-1][1].read_text(encoding="utf-8")).get("token") if kept else None
    except (OSError, ValueError):
        return None


def undo(change=None, conn=None):
    """Put the files of the newest write back as they were before it. A file
    changed since (by hand, or by a run of an older version) is not touched.
    With `change` (the token a save returned), only that write is undone: one
    that is no longer the newest is refused, so an Undo beside a saved rule
    never takes back another tab's newer write. With `conn`, the rows the
    write relinked get back what they held (G13), files and rows together."""
    from . import db, relink
    with _RULES_LOCK:
        kept = _history()
        if not kept:
            raise RuleRefused("Nothing to undo")
        _, path = kept[-1]
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise RuleRefused(f"The last change cannot be read back: {e}") from e
        if change is not None and entry.get("token") != str(change):
            raise RuleRefused("The rules have changed since this save, so it cannot be undone here: "
                              "undo it on the Rules page")
        d = personal_rules_dir()
        files = {Path(name).name: f for name, f in entry["files"].items()}
        now = {name: (d / name).read_bytes() if (d / name).is_file() else None for name in files}
        for name, f in files.items():
            if _sha(now[name]) != f["after"]:
                raise RuleRefused(f"{name} has changed since \"{entry['summary']}\", so that cannot be undone here")
        done = []
        back = {"changed": 0, "written": 0}
        try:
            for name, f in files.items():
                _write_atomic(d / name, None if f["before"] is None
                              else f["before"].encode("utf-8", "surrogateescape"))
                done.append(name)
            if conn is not None and entry.get("record"):
                # the rows go back in a transaction that commits only once the
                # entry is gone, so a failure leaves the rows as the files are
                # the entry is set aside first and comes back if the rows
                # do not commit, so a failed Undo can be tried again
                held = path.with_name(path.name + ".undoing")
                os.replace(path, held)
                try:
                    with db.transaction(conn):
                        back = relink.revert(conn, entry["record"])
                except BaseException:
                    os.replace(held, path)
                    raise
                try:
                    held.unlink()
                except OSError:
                    pass                 # not a *.json: the history never reads it
            else:
                path.unlink()
        except (OSError, sqlite3.Error) as e:
            # all the files go back, or none: a half undone write could never
            # be undone again, its first file no longer matching the entry
            for name in done:
                try:
                    _write_atomic(d / name, now[name])
                except OSError:
                    pass
            raise RuleRefused(f"Could not undo: {e}") from e
        return {"undone": entry["summary"], "relinked": back}


def apply(conn, cfg=None):
    """G13: Apply rules. Every row the record holds differently from what the
    rules (and, with `cfg`, the trips) say now is relinked, as a load would
    link it: after a rules file was changed by hand, or a record loaded with
    other rules. No statement is read."""
    from . import local, relink
    with _RULES_LOCK:
        changes = [relink.rules(conn, None, _load_all(conn))]
    if cfg is not None:
        changes.append(relink.trips(conn, cfg, None, local.trips_from_db(conn)))
    # a row both passes moved is one row changed
    return {"relinked": {"changed": len(set().union(*(c.changed_keys() for c in changes))),
                         "written": sum(c.summary()["written"] for c in changes)}}
