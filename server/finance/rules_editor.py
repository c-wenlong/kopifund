"""The Rules page's editor (G7).

Every rule, shared and personal, with what it does to the record: the rows it
matches, the rows it wins (first match wins, across every tier), the lint's
findings beside the rules they name, and the rules grouped by payee. An edit
is previewed against the whole rule set as it would load with the edit made,
then saved into the personal file its tier decides (rules_app.TIER_FILE).
Shared rules in the repo are never written: editing one adds a personal rule,
which loads before the shared file of its tier and so wins.
"""
from collections import Counter, defaultdict
from pathlib import Path

from . import rules_app
from .paths import REPO_RULES, personal_rules_dir
from .rules_app import RuleRefused, RulesFile, categorise, describe

TIER_ORDER = {t: i for i, t in enumerate(categorise.MATCH_ON)}

# each lint check as the page names it, and what it means, in a few words
PROBLEMS = {
    "rules that never win": "Never wins",
    "duplicate (tier, pattern)": "Duplicate",
    "shadowed within a tier": "Covered",
    "unknown category": "Unknown category",
    "payee with more than one kind": "Two kinds",
    "payee with more than one country": "Two countries",
}
WHY = {
    "Duplicate": "The same pattern twice in one tier: only the first counts.",
    "Covered": "An earlier rule in this tier catches every text this one would.",
    "Unknown category": "Not one of the shipped categories.",
    "Two kinds": "This payee's rules give it different kinds; the first one is used.",
    "Two countries": "This payee's rules name different countries. For a chain, mark every rule [chain].",
    "Too broad": f"Matches over {categorise.BROAD} different texts. Mark it [ids] if the text carries an id.",
}
SAMPLE = 8


def _problem(check, line):
    label = PROBLEMS.get(check) or ("Too broad" if check.startswith("pattern matching") else check)
    if label == "Never wins":
        why = line.split(" -> ", 1)[-1].removeprefix("never wins: ")
        why = f"Never wins: {why}."
    else:
        why = WHY.get(label, line)
    return {"check": label, "why": why, "detail": line}


def _shared_names():
    return {f.name for f in REPO_RULES.glob("*.csv")}


def _text(path):
    """A rules file's text with its line endings as they are, as RulesFile
    reads it, so a record's revision is the same whichever reads it."""
    return path.read_bytes().decode("utf-8")


def _record_numbers(rules):
    """{id(rule): (record number, revision)}: the loader numbers a rule by the
    line it ends on, the page and every write by its record, header = 1; the
    revision lets a write tell the record has not changed since."""
    shared = _shared_names()
    by_file = {}
    for name in {r["file"] for r in rules}:
        path = (REPO_RULES if name in shared else personal_rules_dir()) / name
        try:
            text = _text(path)
        except OSError:
            text = ""
        by_file[name] = {line: (n, rules_app.revision(raw))
                         for n, (raw, _, line) in enumerate(rules_app.split_records(text), start=1)}
    return {id(r): by_file[r["file"]].get(r["line"], (r["line"], "")) for r in rules}


class Evaluation:
    """Every row run through the rules as the categoriser runs them: the rule
    that wins each row (first match, tiers in order), and the rows each rule
    matches."""

    def __init__(self, rows, rules, matcher=None):
        matcher = matcher or categorise.Matcher(rules)
        self.winners = []
        self.won = Counter()
        self.rows_of = defaultdict(list)
        for i, row in enumerate(rows):
            win = None
            for _, _, hits in matcher.matches(row):
                for r in hits:
                    self.rows_of[id(r)].append(i)
                if hits and win is None:
                    win = hits[0]
            self.winners.append(win)
            if win is not None:
                self.won[id(win)] += 1


def _namer(conn):
    """The category name the record stores a rule's category under, decided
    as a load decides it (db.category_ids): the category's own name, the one
    a name the user renamed or deleted on the Categories page now points at,
    or a renamed shipped category's new name."""
    from . import db
    ids = db.category_ids(conn)
    names = {r[1]: r[0] for r in conn.execute("SELECT name, id FROM category")}
    renamed = categorise.RENAMED_CATEGORIES

    def namer(name):
        if name in ids:
            return names.get(ids[name], name)
        return renamed.get(name, name)
    return namer


def _outcome(rule, namer):
    """(payee, category) a winning rule gives a row, as the record stores it."""
    if rule is None:
        return ("", "")
    return (rule["payee"], namer(rule["category"]))


def _row_out(row):
    return {"key": row["txn_key"], "date": row["date"], "amount": row["amount_minor"],
            "currency": row["currency"], "text": rules_app._text(row)}


def overview(conn):
    """Every rule in the order the categoriser tries them, the payee groups,
    the lint's problems beside the rules they name, how many rows the record
    holds differently from what the rules now say (an Apply rules away), and
    the writes that can be undone."""
    out = {"history": rules_app.history(), "tiers": list(categorise.MATCH_ON),
           "tier_files": rules_app.TIER_FILE, "rules": [], "groups": [], "pending": 0, "rules_error": None}
    try:
        rules = rules_app._load_all(conn)
    except RuleRefused as e:
        return {**out, "rules_error": str(e)}
    rows = rules_app.db_rows(conn)
    matcher = categorise.Matcher(rules)
    ev = Evaluation(rows, rules, matcher)
    problems = defaultdict(list)
    for check, items in categorise.lint_items(rows, rules, matcher).items():
        for line, named in items:
            p = _problem(check, line)
            for r in named:
                problems[id(r)].append(p)
    numbers = _record_numbers(rules)
    shared = _shared_names()
    listed = []
    for n, r in enumerate(sorted(rules, key=lambda r: TIER_ORDER[r["match_on"]]), start=1):
        line, rev = numbers[id(r)]
        listed.append({
            "id": f"{r['file']}:{line}", "file": r["file"], "line": line, "revision": rev,
            "shared": r["file"] in shared, "order": n, "tier": r["match_on"],
            **{k: r[k] for k in ("pattern", "payee", "kind", "country", "category", "notes")},
            "recurring": r["recurring"].lower() == "yes",
            "matched": len(ev.rows_of[id(r)]), "wins": ev.won[id(r)], "problems": problems[id(r)],
            "pins": [_row_out(rows[i]) for i in ev.rows_of[id(r)][:5]] if r["match_on"] == "key" else []})
    first = {}
    for r in rules:   # the first rule naming a payee describes it, in load order
        if r["payee"]:
            first.setdefault(r["payee"], r)
    out["rules"], out["groups"] = listed, group(listed, first)
    namer = _namer(conn)
    out["pending"] = sum(1 for row, win in zip(rows, ev.winners)
                         if _outcome(win, namer) != (row["payee"] or "", row["category"] or ""))
    # G13: whether Apply rules has anything to do: the rows above, a row whose
    # rule text alone is behind (a pattern edited by hand), or a payee whose
    # kind, country, recurring or account the rules now give otherwise
    out["stale"] = bool(out["pending"]) or any(
        (row["rule"] or "") != (f"{win['match_on']}:{win['pattern']}" if win else "")
        for row, win in zip(rows, ev.winners)) or _payees_behind(conn, rules)
    return out


def _payees_behind(conn, rules):
    """Whether the payee table holds any payee the rules name otherwise than
    a load would write it (db.upsert_payees)."""
    from . import snapshot as snap
    from . import db
    held = {r["name"]: list(r)[1:] for r in conn.execute(
        "SELECT name, kind, country, recurring, account_no FROM payee")}
    return any(held.get(p["name"]) != list(db.payee_values(p)) for p in snap.payees_from_rules(rules, []))


def group(listed, first=None):
    """One group per payee, and one per category for the rules that name no
    payee (type and flow rules, pins with no payee), most rows first."""
    first = first or {}
    groups = {}
    for r in listed:
        gid = f"payee:{r['payee']}" if r["payee"] else f"category:{r['category']}"
        g = groups.get(gid)
        if g is None:
            lead = first.get(r["payee"], r)
            g = groups[gid] = {"id": gid, "payee": r["payee"], "kind": lead["kind"] if r["payee"] else "",
                               "country": lead["country"] if r["payee"] else "", "categories": Counter(),
                               "patterns": 0, "pins": 0, "rows": 0, "problems": 0, "shared": 0, "rules": []}
        g["rules"].append(r["id"])
        g["pins" if r["tier"] == "key" else "patterns"] += 1
        g["rows"] += r["wins"]
        g["categories"][r["category"]] += r["wins"]
        g["problems"] += bool(r["problems"])
        g["shared"] += r["shared"]
    out = []
    for g in groups.values():
        cats = [c for c, _ in g["categories"].most_common()]
        out.append({**g, "categories": cats, "category": cats[0]})
    return sorted(out, key=lambda g: (-g["rows"], (g["payee"] or g["category"]).lower()))


# ---------------------------------------------------------------- edit and add

EDITABLE = ("match_on", "pattern", "payee", "kind", "country", "category", "recurring")


def _clean(rule):
    """A rule from the page, tidied, or RuleRefused. The loader checks the
    rest (country, category) when the set is loaded with it."""
    if not isinstance(rule, dict):
        raise RuleRefused("No rule")
    out = {k: str(rule.get(k) or "").strip() for k in EDITABLE}
    if out["match_on"] not in categorise.MATCH_ON:
        raise RuleRefused(f"The tier is one of {', '.join(categorise.MATCH_ON)}")
    out["country"] = out["country"].upper()
    out["recurring"] = "yes" if out["recurring"].lower() in ("yes", "true", "1") else ""
    if not out["pattern"]:
        raise RuleRefused("Type a pattern")
    if not out["category"]:
        raise RuleRefused("Choose a category")
    if not out["payee"]:
        out["kind"] = ""
    elif out["kind"] not in categorise.KINDS:
        raise RuleRefused(f"kind is one of {', '.join(categorise.KINDS)}")
    if "notes" in rule:
        out["notes"] = " ".join(str(rule.get("notes") or "").split())
    return out


class Plan:
    """What a save would write: the changed files, and where the rule lands."""

    def __init__(self, files, target, line, override, action, summary):
        self.files, self.file, self.line = files, target.name, line
        self.override, self.action, self.summary = override, action, summary
        data = target.data() or b""
        # the line number the loader gives the rule once saved
        self.loader_line = rules_app.split_records(data.decode("utf-8"))[line - 1][2]

    def changes(self):
        return {f.name: f.data() for f in self.files if f.data() != f.before}


def _plan(data):
    """The files a save of `data` ({rule, edit?}) would write. `edit` names the
    rule being changed ({file, line, revision?, pattern?}); a record on that
    line that no longer reads as the page saw it means the file changed since
    the page loaded, and the save is refused rather than overwrite it."""
    data = data if isinstance(data, dict) else {}
    rule = _clean(data.get("rule"))
    edit = data.get("edit") or None
    target = rules_app.TIER_FILE[rule["match_on"]]
    if not edit:
        f = RulesFile(target)
        return Plan([f], f, f.insert(rule), None, "add", f"Added {describe(rule)} to {target}")
    name = Path(str(edit.get("file") or "")).name
    stale = RuleRefused(f"{name or 'that file'} has changed since the page loaded: reload and try again")
    if name in _shared_names():
        old, raw = _shared_rule(name, edit.get("line"))
        if not rules_app.unchanged(edit, old, raw):
            raise stale
        # the note says what the rule overrides, so the personal file explains itself
        mark = f"overrides {name} {old['pattern']}"
        notes = rule.get("notes", "")
        rule["notes"] = notes if notes.startswith(mark) else f"{mark}: {notes}" if notes else mark
        f = RulesFile(target)
        return Plan([f], f, f.insert(rule), name, "override",
                    f"Overrode {name} {old['pattern']} with {describe(rule)} in {target}")
    src = RulesFile(name)
    old = src.rule(edit.get("line"))
    if src.before is None or not rules_app.unchanged(edit, old, old and src.records[int(edit["line"]) - 1][0]):
        raise stale
    if old["recurring"].lower() == "no" and not rule["recurring"]:
        rule["recurring"] = old["recurring"]
    merged = {**old, **rule}
    line = int(edit["line"])
    if target == src.name:
        src.replace(line, merged)
        return Plan([src], src, line, None, "edit", f"Edited {describe(merged)} in {target}")
    src.delete(line)
    dst = RulesFile(target)
    return Plan([src, dst], dst, dst.insert(merged), None, "edit",
                f"Moved {describe(merged)} from {src.name} to {target}")


def _shared_rule(name, line):
    """(rule, its record's text) on one line of a shared file in the repo,
    read only; (None, "") when that line holds no rule."""
    path = REPO_RULES / name
    records = rules_app.split_records(_text(path))
    try:
        i = int(line) - 1
    except (TypeError, ValueError):
        return None, ""
    if not 1 <= i < len(records):
        return None, ""
    header = [h.strip() for h in records[0][1]]
    raw, cells, _ = records[i]
    if not any(c.strip() for c in cells) or cells[0].lstrip().startswith("#"):
        return None, ""
    out = {k: "" for k in categorise.COLUMNS}
    out.update({k: cells[j].strip() for j, k in enumerate(header) if j < len(cells)})
    return out, raw


def preview(conn, data):
    """What saving `data` would do, judged by the whole rule set with the
    change made: the rows the rule matches, the rows it would win (an earlier
    tier or rule may win the rest, named in `beaten_by`), how many of those
    already have its payee and category, and every row whose payee or
    category would change, a few listed. A rule that would not load says why
    in `error`."""
    try:
        plan = _plan(data)
        before = rules_app._load_all(conn)
        after = rules_app._trial(conn, plan.changes())
    except RuleRefused as e:
        return {"error": str(e)}
    new = next((r for r in after if r["file"] == plan.file and r["line"] == plan.loader_line), None)
    if new is None:
        return {"error": f"The rule would not load from {plan.file}"}
    rows = rules_app.db_rows(conn)
    was, now = Evaluation(rows, before), Evaluation(rows, after)
    mine = now.rows_of[id(new)]
    wins = [i for i in mine if now.winners[i] is new]
    beaten = Counter(id(now.winners[i]) for i in mine if now.winners[i] is not new)
    rules_by_id = {id(r): r for r in after}
    namer = _namer(conn)

    def outcome(rule):
        return _outcome(rule, namer)

    changed = [i for i in range(len(rows)) if outcome(was.winners[i]) != outcome(now.winners[i])]
    changed.sort(key=lambda i: (rows[i]["date"], rows[i]["txn_key"]), reverse=True)
    return {
        "error": None, "file": plan.file, "override": plan.override,
        "matches": len(mine), "wins": len(wins),
        "already": sum(1 for i in wins if outcome(was.winners[i]) == outcome(new)),
        "changes": len(changed),
        "sample": [{**_row_out(rows[i]),
                    "before": dict(zip(("payee", "category"), outcome(was.winners[i]))),
                    "after": dict(zip(("payee", "category"), outcome(now.winners[i])))}
                   for i in changed[:SAMPLE]],
        "beaten_by": [{**{k: rules_by_id[rid][k] for k in ("file", "pattern", "payee", "category")},
                       "tier": rules_by_id[rid]["match_on"], "rows": n} for rid, n in beaten.most_common(3)],
    }


def save(conn, data):
    """Write one added or edited rule into its personal file, once the whole
    set loads with it, and relink the rows it decides differently at once
    (G13); the write, rows included, can be undone."""
    with rules_app._RULES_LOCK:
        plan = _plan(data)
        if not plan.changes():
            return {"unchanged": True, "file": plan.file, "line": plan.line,
                    "relinked": {"changed": 0, "written": 0}}
        linked = {}
        rules_app._commit(conn, plan.files, plan.action, plan.summary, linked=linked)
        # the undo history's token for this write, so an Undo can name it (G13)
        change = rules_app.latest_change()
    return {"file": plan.file, "line": plan.line, "override": plan.override, "summary": plan.summary,
            "change": change, "relinked": linked}
