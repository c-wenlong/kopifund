#!/usr/bin/env python3
"""Assign a payee and a category to every parsed transaction from the rules files.

Usage:
    python3 -m finance.parsers.categorise TRANSACTIONS_CSV [--rules rules/]
                                          [--categories FILE]
                                          [--out OUT_CSV] [--review REVIEW_MD]
    python3 -m finance.parsers.categorise --lint TRANSACTIONS_CSV [--rules rules/]
    python3 -m finance.parsers.categorise --why TXN_KEY TRANSACTIONS_CSV [--rules rules/]

TRANSACTIONS_CSV is the parser output (python3 -m finance.parsers.parse_statements --out DIR).
The rules come from rules/ unless --rules says otherwise; --rules takes either a
directory (every *.csv in it, concatenated in sorted filename order) or a single
file. OUT_CSV is the input plus three columns: payee, category, rule; it
overwrites the input when --out is omitted. REVIEW_MD (default: review.md beside
OUT_CSV) lists the External rows that no rule matched, grouped by merchant,
counterparty, memo or bank transaction type, biggest money first, so the rules
can be extended.

--lint reports rules that never win, duplicate and shadowed rules, unknown
categories, payees whose kind or country disagree between rules, and patterns
that match more than 25 distinct raw strings. It exits 1 when it finds anything.
Two of those checks can be waved off per rule by a marker in its notes, because
the statements themselves break the assumption the check makes: "[chain]" on
every rule of an international chain, whose country is the branch's rather than
the payee's, and "[ids]" on a pattern whose merchant string carries a
per-transaction id, which makes the distinct-string count meaningless.
--why prints, for one txn_key, every rule that matches the row in every tier and
which one wins.

Rules file columns: match_on, pattern, payee, kind, country, category, notes,
account_no (optional), recurring (optional).

  match_on   key | type | flow | merchant | counterparty | memo | reference. Names
             the column the pattern is tested against: txn_key, flow_class,
             bank_txn_type, merchant, counterparty, memo, reference. "key" pins one
             row by its txn_key, for statement lines that print no text at all.
  pattern    fnmatch glob, case-insensitive. Both the pattern and the field are
             uppercased and their whitespace collapsed before matching, so
             "grab*" matches "GRAB* GPC-..." and "BUS/MRT" is an exact match.
  payee      display name; may be empty for flow and type rules.
  kind       Merchant | Person | Employer | Institution; required iff payee set.
  country    ISO alpha-3 or empty.
  category   exact name of a Categories row in Notion (KNOWN_CATEGORIES below is
             the offline copy; --categories FILE overrides it with one name per
             line, and the sync code calls check_categories() against the live
             Notion list).
  notes      free text, plus the optional lint markers "[chain]" and "[ids]"
             described above.
  account_no the payee's bank account number, if a statement shows it (copied
             to the Payees row; optional, column may be absent).
  recurring  "yes" on a payee billed on a schedule, blank otherwise; copied to
             Payees.Recurring (optional, column may be absent).

Each tier lives in its own file, and a rule in the wrong file is a load error:

    rules/00-keys.csv        key
    data/rules/10-types.csv       type, flow
    rules/20-people.csv      counterparty, memo, reference
    data/rules/30-merchants.csv   merchant

The data folder's rules/ holds the personal tiers under plain names: keys.csv,
types.csv, people.csv and merchants.csv. types.csv and merchants.csv hold the
type and merchant rules only one user meets and load just before 10-types.csv
and 30-merchants.csv.

Tiers are tried in the fixed order key, type, flow, merchant, counterparty,
memo, reference (type before flow so a wallet's own row types can override
its internal flow class);
inside a tier the first matching rule in file order wins, and the first match
across tiers sets both payee and category. A row whose field is empty for a
tier skips that tier. Lines whose first cell starts with # are comments.

Importable: load_rules(path) -> list of rule dicts (path may be a directory),
categorise_rows(rows, rules) adds payee / category / rule to every row dict in
place and returns the list, check_categories(rules, names) validates the rules'
categories against a list such as the live Notion one, lint(rows, rules) returns
the lint findings (lint_items(rows, rules) the same with the rules each names),
and Matcher(rules) answers which rules match a value, compiled once.
"""
import argparse
import csv
import re
import sys
from collections import OrderedDict, defaultdict
from fnmatch import fnmatchcase, translate
from pathlib import Path

MATCH_ON = ("key", "type", "flow", "merchant", "counterparty", "memo", "reference")
FIELD = {"key": "txn_key", "flow": "flow_class", "type": "bank_txn_type", "merchant": "merchant",
         "counterparty": "counterparty", "memo": "memo", "reference": "reference"}
KINDS = ("Merchant", "Person", "Employer", "Institution")
REQUIRED_COLUMNS = ("match_on", "pattern", "payee", "kind", "country", "category", "notes")
OPTIONAL_COLUMNS = ("account_no", "recurring")
COLUMNS = REQUIRED_COLUMNS + OPTIONAL_COLUMNS
ADDED = ("payee", "category", "rule")

# which match_on values each file of a rules directory may hold; a file whose
# name is not listed accepts any tier
FILE_TIERS = {"00-keys.csv": ("key",),
              "10-types.csv": ("type", "flow"),
              "20-people.csv": ("counterparty", "memo", "reference"),
              "30-merchants.csv": ("merchant",),
              # the personal tiers as they are named in the data folder's rules/
              "keys.csv": ("key",),
              "types.csv": ("type", "flow"),
              "people.csv": ("counterparty", "memo", "reference"),
              "merchants.csv": ("merchant",)}
# the order files load in when rules come from more than one folder: the same
# order the numbered names give inside one folder
# merchants.csv (the user's own merchants, and rules added from the app) loads
# before the shipped merchant rules, so a personal rule wins a tie inside the
# merchant tier; types.csv (a bank type only one user meets, such as the
# credits of their own school) does the same before the shipped type rules
FILE_ORDER = {"00-keys.csv": 0, "keys.csv": 0, "types.csv": 0.5, "10-types.csv": 1, "20-people.csv": 2,
              "people.csv": 2, "merchants.csv": 2.5, "30-merchants.csv": 3}

# a pattern matching more than this many distinct raw strings is probably too broad
BROAD = 25

# Two lint checks assume something about the data that is not always true, so a
# rule can say in its notes that the assumption does not hold for it. Both are
# claims about the statements, not opinions about the rule, so each is written
# out here with what it asserts. A marker is a bracketed word anywhere in the
# notes column, which stays free text otherwise.
#
#   [chain]  the payee is an international chain, so the country on a rule is
#            the country of that branch and its rules legitimately name several.
#            Every rule for that payee has to carry it, so a genuine typo in one
#            of them still shows up rather than hiding behind a marker on
#            another.
#   [ids]    the merchant string carries a per-transaction id (a ride, a fare, an
#            order), so counting distinct raw strings measures how often the
#            payee was used, not how broad the pattern is.
CHAIN_MARKER = "[chain]"
IDS_MARKER = "[ids]"

# exact Name of every row in the Notion Categories database; a rule naming
# anything else is rejected at load time so the typo never reaches Notion
# a shipped category that was renamed: a rule still naming the old one loads
# (the record stores its rows under the new name); server/finance/db.py holds the
# same map for the record
RENAMED_CATEGORIES = {"Tuition": "Education"}

KNOWN_CATEGORIES = (
    "Transport (public)", "Transport (private)",
    # Transport was split into the two above (`finance.py split-transport`);
    # it is no longer shipped, but a record that still has it keeps it, and a
    # personal rule not yet moved still loads, here and in an offline --lint
    "Transport",
    "Fees and charges", "Shopping", "Education", "Eating out", "Fitness",
    "Salary", "Freelance income", "Refunds and cashback", "Entertainment",
    "Wallet top-up", "Person to person", "Travel", "Donations",
    "Software and tools", "Between own accounts", "Coffee", "Family support", "Rent",
    "Groceries", "Interest", "Phone and utilities", "Health", "Investments",
    "Financial aid", "Grants and awards", "Insurance", "Courses and licences",
    "Unidentified",
)


def norm(s):
    """Uppercase and collapse whitespace; what both sides of a match see."""
    return " ".join((s or "").split()).upper()


# ---------------------------------------------------------------- rules

def rule_files(path):
    """The files load_rules reads for `path`: one file, a directory's *.csv in
    sorted filename order, or, for a list of either, every file they name in
    tier order (FILE_ORDER), so the repo's shared rules and the data folder's
    personal ones load as if they were one folder."""
    if isinstance(path, (list, tuple)):
        files = [f for p in path for f in rule_files(p)]
        return sorted(files, key=lambda f: (FILE_ORDER.get(f.name, 9), f.name))
    path = Path(path)
    if path.is_dir():
        files = sorted(path.glob("*.csv"), key=lambda p: p.name)
        if not files:
            sys.exit(f"no *.csv in rules directory {path}")
        return files
    if not path.exists():
        sys.exit(f"rules file not found: {path}")
    return [path]


def load_rules(path, categories=None):
    """Read and validate the rules. `path` is a rules directory or a single file.
    `categories` defaults to KNOWN_CATEGORIES, the offline copy of the Notion list.
    Exits 1 listing every bad line."""
    names = tuple(categories) if categories is not None else KNOWN_CATEGORIES
    rules, errors = [], []
    for f in rule_files(path):
        allowed = FILE_TIERS.get(f.name, MATCH_ON)
        rules += _read_file(f, allowed, names, errors)
    if errors:
        sys.exit(f"{len(errors)} bad rule(s)\n" + "\n".join(errors))
    if not rules:
        sys.exit(f"{_label(path)}: no rules")
    return rules


def _label(path):
    """A rules path, or a list of them, as one string for a message."""
    if isinstance(path, (list, tuple)):
        return " + ".join(str(p) for p in path)
    return str(path)


def _read_file(path, allowed, names, errors):
    """One rules file; appends "  FILE line N: ..." strings to `errors`."""
    out = []
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = [h.strip() for h in next(reader, None) or []]
        extra = header[len(REQUIRED_COLUMNS):]
        if header[:len(REQUIRED_COLUMNS)] != list(REQUIRED_COLUMNS) or \
                len(set(extra)) != len(extra) or \
                any(h not in OPTIONAL_COLUMNS for h in extra):
            sys.exit(f"{path}: header must be {','.join(REQUIRED_COLUMNS)} followed by "
                     f"any of {','.join(OPTIONAL_COLUMNS)}; got {','.join(header)}")
        for cells in reader:
            n = reader.line_num
            if not any(c.strip() for c in cells) or cells[0].lstrip().startswith("#"):
                continue
            cells += [""] * (len(header) - len(cells))
            r = {k: "" for k in COLUMNS}
            r.update({k: cells[i].strip() for i, k in enumerate(header)})
            r["file"], r["line"] = path.name, n
            bad = []
            if r["match_on"] not in MATCH_ON:
                bad.append(f"match_on {r['match_on']!r} not in {'|'.join(MATCH_ON)}")
            elif r["match_on"] not in allowed:
                bad.append(f"match_on {r['match_on']!r} does not belong in {path.name} "
                           f"(it takes {'|'.join(allowed)})")
            if not r["pattern"]:
                bad.append("empty pattern")
            if r["payee"] and not r["kind"]:
                bad.append("payee without kind")
            if r["kind"] and not r["payee"]:
                bad.append("kind without payee")
            if r["kind"] and r["kind"] not in KINDS:
                bad.append(f"kind {r['kind']!r} not in {'|'.join(KINDS)}")
            if r["country"] and not (len(r["country"]) == 3 and r["country"].isalpha()
                                     and r["country"].isupper()):
                bad.append(f"country {r['country']!r} is not ISO alpha-3")
            if r["recurring"] and r["recurring"].lower() not in ("yes", "no"):
                bad.append(f"recurring {r['recurring']!r} is not yes or blank")
            if not r["category"]:
                bad.append("empty category")
            elif r["category"] not in names and RENAMED_CATEGORIES.get(r["category"]) not in names:
                bad.append(f"unknown category {r['category']!r}")
            if bad:
                errors.append(f"  {path.name} line {n}: " + "; ".join(bad))
            r["glob"] = norm(r["pattern"])
            out.append(r)
    return out


def load_categories(path):
    """One category name per line; blank lines and # comments ignored."""
    p = Path(path)
    if not p.exists():
        sys.exit(f"categories file not found: {p}")
    names = [ln.strip() for ln in p.read_text(encoding="utf-8").splitlines()]
    names = [n for n in names if n and not n.startswith("#")]
    if not names:
        sys.exit(f"{p}: no category names")
    return names


def check_categories(rules, names):
    """Every rule whose category is not in `names`, as printable strings. The sync
    code passes the live Notion Categories names here; load_rules already checked
    the rules against KNOWN_CATEGORIES or --categories."""
    names = set(names)
    return [f"{r['file']} line {r['line']}: unknown category {r['category']!r} "
            f"({r['match_on']}:{r['pattern']})"
            for r in rules if r["category"] not in names]


def recurring_payees(rules):
    """Payee names any rule marks recurring."""
    return {r["payee"] for r in rules
            if r["payee"] and r["recurring"].lower() == "yes"}


def categorise_rows(rows, rules):
    """Add payee, category and rule to every row dict (in place) and return rows.
    The first rule of a tier that matches a value is worked out once per value
    (Matcher.first): rows repeat their texts, and the app runs every row
    through the rules twice for each save it applies at once (G13)."""
    matcher = Matcher(rules)
    for row in rows:
        row["payee"] = row["category"] = row["rule"] = ""
        for tier in MATCH_ON:
            val = norm(row.get(FIELD[tier]))
            if not val:
                continue
            hit = matcher.first(tier, val)
            if hit:
                row["payee"], row["category"] = hit["payee"], hit["category"]
                row["rule"] = f"{tier}:{hit['pattern']}"
                break
    return rows


# ---------------------------------------------------------------- lint and why

def by_tier(rules):
    """{tier: [rule]} in file order."""
    out = defaultdict(list)
    for r in rules:
        out[r["match_on"]].append(r)
    return out


class Matcher:
    """Every rule's glob compiled once, and which rules of a tier match a value
    remembered per value: rows repeat their texts, so checking a whole record
    against every rule (the lint, the app's rule editor) stays quick. It
    matches exactly as fnmatchcase does."""

    def __init__(self, rules):
        self.tiers = {tier: [(r, re.compile(translate(r["glob"])).match) for r in rs]
                      for tier, rs in by_tier(rules).items()}
        self.memo = {}
        self.firsts = {}

    def first(self, tier, val):
        """The first rule of `tier`, in file order, whose glob matches the
        normalised `val`, or None: the one categorise_rows gives the row."""
        key = (tier, val)
        if key not in self.firsts:
            self.firsts[key] = next((r for r, match in self.tiers.get(tier, ()) if match(val)), None)
        return self.firsts[key]

    def hits(self, tier, val):
        """Every rule of `tier` whose glob matches the normalised `val`, in file order."""
        got = self.memo.get((tier, val))
        if got is None:
            got = self.memo[(tier, val)] = [r for r, match in self.tiers.get(tier, ()) if match(val)]
        return got

    def matches(self, row):
        """As matches(row, rules)."""
        out = []
        for tier in MATCH_ON:
            val = norm(row.get(FIELD[tier]))
            if val:
                out.append((tier, val, self.hits(tier, val)))
        return out


def matches(row, rules):
    """[(tier, value, [rule])] for every tier the row has a value for, tried in
    tier order, listing every rule of that tier whose glob matches."""
    return Matcher(rules).matches(row)


def rid(r):
    """How a rule is named in a report."""
    return f"{r['file']} line {r['line']}  {r['match_on']}:{r['pattern']}"


def marked(rule, marker):
    """Whether one rule carries a lint marker in its notes."""
    return marker in (rule["notes"] or "").lower()


def chain_payees(rules):
    """Payees every one of whose rules is marked a chain. A half-marked payee is
    not one, so adding a rule for a chain and forgetting the marker still
    reports the country split rather than silently joining the exemption."""
    by_payee = defaultdict(list)
    for r in rules:
        if r["payee"]:
            by_payee[r["payee"]].append(r)
    return {p for p, rs in by_payee.items()
            if all(marked(r, CHAIN_MARKER) for r in rs)}


def lint(rows, rules):
    """Findings as {check: [line]}; an empty dict means the rules are clean."""
    return OrderedDict((check, [line for line, _ in items])
                       for check, items in lint_items(rows, rules).items())


def lint_items(rows, rules, matcher=None):
    """The findings as {check: [(line, [rule])]}: each line as lint() prints
    it, with the rules it names, so the app can show a finding beside them."""
    matcher = matcher or Matcher(rules)
    stats = {id(r): dict(rule=r, matched=0, won=0, lost_tier=0, lost_rule=0, values=set())
             for r in rules}
    for row in rows:
        won = False
        for tier, val, hits in matcher.matches(row):
            for i, r in enumerate(hits):
                st = stats[id(r)]
                st["matched"] += 1
                st["values"].add(val)
                if won:
                    st["lost_tier"] += 1
                elif i:
                    st["lost_rule"] += 1
                else:
                    st["won"] += 1
            if hits:
                won = True

    out = OrderedDict()

    dead = []
    for r in rules:
        st = stats[id(r)]
        if st["won"]:
            continue
        if not st["matched"]:
            why = "matches no row"
        elif st["lost_tier"] and not st["lost_rule"]:
            why = f"lost to an earlier tier on all {st['lost_tier']} rows it matched"
        elif st["lost_rule"] and not st["lost_tier"]:
            why = f"lost to an earlier rule in the same tier on all {st['lost_rule']} rows"
        else:
            why = (f"never wins: {st['lost_tier']} rows lost to an earlier tier, "
                   f"{st['lost_rule']} to an earlier rule in the same tier")
        dead.append((f"{rid(r)} -> {why}", [r]))
    if dead:
        out["rules that never win"] = dead

    seen = defaultdict(list)
    for r in rules:
        seen[(r["match_on"], r["glob"])].append(r)
    dup = [(f"{tier}:{rs[0]['pattern']} in " + ", ".join(f"{x['file']} line {x['line']}"
                                                         for x in rs), rs)
           for (tier, _), rs in seen.items() if len(rs) > 1]
    if dup:
        out["duplicate (tier, pattern)"] = dup

    shadow = []
    for tier, rs in by_tier(rules).items():
        for j, later in enumerate(rs):
            for earlier in rs[:j]:
                if earlier["glob"] != later["glob"] and \
                        fnmatchcase(later["glob"], earlier["glob"]):
                    shadow.append((f"{rid(later)} is covered by the earlier "
                                   f"{earlier['match_on']}:{earlier['pattern']} "
                                   f"({earlier['file']} line {earlier['line']})", [later]))
    if shadow:
        out["shadowed within a tier"] = shadow

    strays = [r for r in rules if r["category"] not in KNOWN_CATEGORIES]
    unknown = list(zip(check_categories(strays, KNOWN_CATEGORIES), ([r] for r in strays)))
    if unknown:
        out["unknown category"] = unknown

    # a payee's kind is a fact about the payee, so it never legitimately differs.
    # Its country is a fact about the branch the rule matches, so a chain marked
    # with [chain] is allowed to name one country per branch.
    chains = chain_payees(rules)
    for field, title in (("kind", "kind"), ("country", "country")):
        seen_val = defaultdict(lambda: defaultdict(list))
        for r in rules:
            if r["payee"] and not (field == "country" and r["payee"] in chains):
                seen_val[r["payee"]][r[field]].append(r)
        bad = []
        for payee, vals in sorted(seen_val.items()):
            if len(vals) > 1:
                bad.append((f"{payee}: " + "; ".join(
                    f"{v or '(blank)'} in " + ", ".join(f"{x['file']} line {x['line']}"
                                                        for x in rs)
                    for v, rs in sorted(vals.items())), [x for rs in vals.values() for x in rs]))
        if bad:
            out[f"payee with more than one {title}"] = bad

    broad = [(f"{rid(r)} matches {len(stats[id(r)]['values'])} distinct raw strings", [r])
             for r in rules
             if len(stats[id(r)]["values"]) > BROAD and not marked(r, IDS_MARKER)]
    if broad:
        out[f"pattern matching more than {BROAD} distinct raw strings"] = broad
    return out


def lint_lines(findings, rules_path):
    lines = [f"lint {_label(rules_path)}"]
    if not findings:
        lines.append("  clean")
        return lines
    for check, items in findings.items():
        lines.append(f"  {check} ({len(items)})")
        lines += [f"    {i}" for i in items]
    return lines


def print_why(rows, rules, key):
    """Explain one row; False when the key is not in the file."""
    row = next((r for r in rows if r["txn_key"] == key), None)
    if row is None:
        print(f"no row with txn_key {key}", file=sys.stderr)
        return False
    print(f"{key}  {row['date']}  {row['account_no']}  "
          f"{row['currency']} {row['amount']}  {row['bank_txn_type']}")
    for tier in MATCH_ON:
        print(f"  {tier} ({FIELD[tier]}) = {row.get(FIELD[tier]) or '(empty)'}")
    print()
    winner = None
    for tier, val, hits in matches(row, rules):
        print(f"  {tier}: {len(hits)} matching rule(s) for {val!r}")
        for i, r in enumerate(hits):
            mark = "  <- wins" if winner is None and i == 0 else ""
            print(f"    {rid(r)} -> payee {r['payee'] or '(none)'}, "
                  f"category {r['category']}{mark}")
        if hits and winner is None:
            winner = (tier, hits[0])
    print()
    if winner:
        tier, r = winner
        print(f"winner: {rid(r)}")
        print(f"payee: {r['payee'] or '(none)'}   category: {r['category']}   "
              f"rule: {tier}:{r['pattern']}")
    else:
        print("no rule matches this row")
    return True


# ---------------------------------------------------------------- review

def sgd_rates(csv_path):
    """{(account_no, currency, source): rate to SGD} from period_balances.csv
    beside the transactions file, if it is there. SGD rows never need it."""
    p = Path(csv_path).with_name("period_balances.csv")
    if not p.exists():
        return {}
    out = {}
    for b in csv.DictReader(p.open(newline="", encoding="utf-8")):
        if b.get("fx_to_sgd"):
            out[(b["account_no"], b["currency"], b["source"])] = float(b["fx_to_sgd"])
    return out


def summarise(rows, rates):
    """Counts and totals for the report header; returns (dict, unmatched External rows)."""
    per_tier = OrderedDict((t, 0) for t in MATCH_ON)
    ext_out = unm_out = 0.0
    unmatched_ext, unmatched_int, unconverted = [], 0, 0
    for r in rows:
        if r["rule"]:
            per_tier[r["rule"].split(":", 1)[0]] += 1
        sgd, ok = to_sgd(r, rates)
        unconverted += not ok
        if r["flow_class"] != "External":
            unmatched_int += not r["rule"]
            continue
        if sgd < 0:
            ext_out -= sgd
        if not r["rule"]:
            unmatched_ext.append(r)
            if sgd < 0:
                unm_out -= sgd
    return dict(rows=len(rows), matched=sum(per_tier.values()), per_tier=per_tier,
                unmatched_external=len(unmatched_ext), unmatched_internal=unmatched_int,
                external_out=ext_out, unmatched_out=unm_out,
                share=(unm_out / ext_out if ext_out else 0.0),
                unconverted=unconverted), unmatched_ext


def to_sgd(r, rates):
    """(amount in SGD, converted?): non-SGD rows without a rate stay at face value."""
    amt = float(r["amount"])
    if r["currency"] == "SGD":
        return amt, True
    rate = rates.get((r["account_no"], r["currency"], r["source"]))
    return (amt * rate, True) if rate else (amt, False)


def summary_lines(s):
    tiers = ", ".join(f"{t} {n}" for t, n in s["per_tier"].items())
    lines = [f"{s['rows']} rows; matched {s['matched']} ({tiers}); "
             f"unmatched {s['unmatched_external']} External rows = SGD {s['unmatched_out']:,.2f} "
             f"of {s['external_out']:,.2f} External outflow ({s['share']:.1%})"]
    if s["unmatched_internal"]:
        lines.append(f"{s['unmatched_internal']} Internal rows unmatched (not listed; "
                     f"add a flow rule)")
    if s["unconverted"]:
        lines.append(f"{s['unconverted']} non-SGD rows counted at face value "
                     f"(no period_balances.csv beside the input)")
    return lines


def review_md(rows, rates, rules_path):
    s, unmatched = summarise(rows, rates)
    groups = {"merchant": {}, "counterparty": {}, "memo": {}, "bank_txn_type": {}}
    for r in unmatched:
        col = next((c for c in ("merchant", "counterparty", "memo") if r[c]), "bank_txn_type")
        g = groups[col].setdefault(r[col], dict(n=0, out=0.0, in_=0.0, first=r["date"],
                                                 last=r["date"], example=""))
        sgd, _ = to_sgd(r, rates)
        g["n"] += 1
        g["out" if sgd < 0 else "in_"] += abs(sgd)
        g["first"], g["last"] = min(g["first"], r["date"]), max(g["last"], r["date"])
        if not g["example"]:
            g["example"] = " | ".join(x for x in (r["reference"], r["memo"])
                                      if x and x != r[col])
    out = ["# Categorisation review", "",
           f"Rules: `{_label(rules_path)}`", ""]
    out += [f"- {ln}" for ln in summary_lines(s)]
    out.append("")
    if not unmatched:
        out.append("Every External row matched a rule.")
    for col, title in (("merchant", "Merchant"), ("counterparty", "Counterparty"),
                       ("memo", "Memo"), ("bank_txn_type", "Bank txn type")):
        if not groups[col]:
            continue
        items = sorted(groups[col].items(), key=lambda kv: -(kv[1]["out"] + kv[1]["in_"]))
        total = sum(g["out"] + g["in_"] for _, g in items)
        out += [f"## Unmatched by {title} ({len(items)} keys, {sum(g['n'] for _, g in items)} rows, "
                f"SGD {total:,.2f})", "",
                "| key | rows | total SGD out | total SGD in | first date | last date | example reference/memo |",
                "| --- | ---: | ---: | ---: | --- | --- | --- |"]
        for key, g in items:
            out.append(f"| {_cell(key)} | {g['n']} | {g['out']:,.2f} | {g['in_']:,.2f} | "
                       f"{g['first']} | {g['last']} | {_cell(g['example'])} |")
        out.append("")
    return "\n".join(out) + "\n", s


def _cell(s):
    return (s or "").replace("|", "\\|").replace("\n", " ")


# ---------------------------------------------------------------- cli

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("transactions", metavar="TRANSACTIONS_CSV")
    ap.add_argument("--rules", action="append", metavar="RULES_DIR_OR_CSV",
                    help="repeatable; default: the repo's data/rules/ plus the data folder's rules/")
    ap.add_argument("--categories", metavar="FILE",
                    help="one category name per line; default: KNOWN_CATEGORIES")
    ap.add_argument("--lint", action="store_true",
                    help="report unused, duplicate, shadowed and over-broad rules; "
                         "exits 1 when it finds anything")
    ap.add_argument("--why", metavar="TXN_KEY",
                    help="explain how one row was categorised and exit")
    ap.add_argument("--out", metavar="OUT_CSV", help="default: overwrite the input")
    ap.add_argument("--review", metavar="REVIEW_MD", help="default: review.md beside OUT_CSV")
    a = ap.parse_args()

    if not a.rules:
        from finance.paths import REPO_RULES, personal_rules_dir
        a.rules = [REPO_RULES] + ([personal_rules_dir()] if personal_rules_dir().is_dir() else [])
    elif len(a.rules) == 1:
        a.rules = a.rules[0]
    rules = load_rules(a.rules, load_categories(a.categories) if a.categories else None)
    src = Path(a.transactions)
    with src.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fields = [c for c in reader.fieldnames if c not in ADDED]
        rows = list(reader)
    categorise_rows(rows, rules)

    if a.why:
        sys.exit(0 if print_why(rows, rules, a.why) else 1)
    if a.lint:
        findings = lint(rows, rules)
        print("\n".join(lint_lines(findings, a.rules)))
        sys.exit(1 if findings else 0)

    out = Path(a.out) if a.out else src
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields + list(ADDED), extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    md, s = review_md(rows, sgd_rates(src), a.rules)
    review = Path(a.review) if a.review else out.with_name("review.md")
    review.write_text(md, encoding="utf-8")
    for ln in summary_lines(s):
        print(ln)
    print(f"wrote {out} and {review}")


if __name__ == "__main__":
    main()
