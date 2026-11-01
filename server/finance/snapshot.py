#!/usr/bin/env python3
"""One JSON file holding everything the stats read.

It lives in server/finance/ because finance builds it (a sync, a load, the upload)
and analytics only reads it: analytics imports finance, never the reverse (G19).

`build(cfg, source="notion")` reads the eight Notion data sources and flattens
them into the shape SPEC.md documents: relations resolved to names, `Amount
SGD` taken from the Notion formula, and `Need class` / `Bucket` / `Group`
copied onto every transaction from its category.

G20: every amount in a snapshot is integer cents under `<name>_minor`
(`amount_minor`, `amount_sgd_minor`, a block's `opening_minor` to
`closing_sgd_minor`, a budget's `planned_minor` and `actual_minor`), as the
record holds it, and no amount is a float in dollars. A Notion number and a
parsed CSV amount arrive in dollars and become cents once, as they are read
(`cents`); a CSV row's SGD amount is the record's own rule for a foreign row
(`money.fx_minor`). The brokerage and CPF statements are the parsers' own
output, figures as the statements print them, which their stats turn into
cents as they read them.

That read and that flattening are two functions, `read_pages` and
`build_from_pages`. A monthly run has just read the same eight databases, so
`finance.py sync` hands its own pages to `build_from_pages` and writes today's
snapshot without a second pull.

`build(cfg, source="csv", csv_dir=DIR)` produces the same shape from an earlier
parser run plus the rules plus `server/analytics/categories.json`, so a snapshot can be
built with no network at all. The two agree on everything the parsers own and
differ only in what a human typed in Notion (trips, budgets, account metadata).

Both also carry `portfolio`, the Tiger Brokers annual exports parsed out of
`statements/tiger` in the data folder, and `cpf`, the CPF yearly statements parsed out of
`statements/cpf`. Neither is one of the eight data sources and neither
ever reaches Notion: they are read from disk on every build, so the investments
figure is as fresh as the last export dropped in the folder and the CPF figure
as fresh as the last yearly statement.

Both are deterministic: rows are sorted, dict key order is fixed by
construction, and two builds over the same data are byte-identical apart from
`meta.generated_at`.
"""
import json
import os
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from finance import notion, sync
from finance.model import num
from finance.money import fx_minor, rate_e9, to_cents
from finance.paths import cache_dir

ROOT = Path(__file__).resolve().parent.parent
CACHE = cache_dir()
# the offline copy of the Categories rows: refreshed into the data folder's
# cache, with the repo's copy (the shipped default list) when there is none yet
CATEGORIES_FALLBACK = cache_dir() / "categories.json"
REPO_CATEGORIES = ROOT / "analytics" / "categories.json"

SGT = timezone(timedelta(hours=8))

# The bank_txn_type the YouTrip parser gives a balance jump it invented. There
# is no Synthetic checkbox in Notion, so this string is the only marker, and it
# is the same one in the CSVs and in the database.
SYNTHETIC_TYPE = "YouTrip Unprinted"

FX_NOTE = ("Amount SGD uses the period's FX rate to SGD (latest exchange seen in "
           "that wallet); approximate for non-SGD wallets")

# The eight data sources, in the order meta.databases lists them.
DATABASES = ("transactions", "period_balances", "payees", "categories",
             "accounts", "cards", "budgets", "trips")

# Where the Tiger Brokers annual exports live, relative to the repo root, when
# [analytics] tiger_folder does not say otherwise. An empty string there
# switches the read off, which is what a clone with no brokerage wants.
TIGER_FOLDER = "statements/tiger"

# Where the CPF yearly statements live, relative to the repo root, when
# [analytics] cpf_folder does not say otherwise. An empty string there switches
# the read off, which is what a clone outside Singapore wants.
CPF_FOLDER = "statements/cpf"


# ---------------------------------------------------------------- readers

def formula(page, name):
    """A formula property's value, whatever type it carries."""
    p = page["properties"].get(name)
    if not p or p["type"] != "formula":
        return None
    f = p["formula"] or {}
    return f.get(f.get("type"), None)


def rollup_number(page, name):
    """A rollup property's number, or None."""
    p = page["properties"].get(name)
    if not p or p["type"] != "rollup":
        return None
    return (p["rollup"] or {}).get("number")


def relation_name(page, prop, names):
    """The first related page's title, or "" when the relation is empty."""
    for pid in notion.plain(page, prop) or ():
        if pid in names:
            return names[pid]
    return ""


def cents(x):
    """G20: an amount that arrives in dollars (a Notion number, a parsed CSV
    amount) as integer cents, half up (`money.to_cents`), or None. A snapshot
    built from the record takes the record's own cents instead."""
    return None if x is None or x == "" else to_cents(x)


# ---------------------------------------------------------------- notion

def read_pages(cfg, client=None):
    """The eight data sources, read into {data source name: [page]}."""
    client = client or notion.NotionClient(notion.find_token(cfg), cfg.version,
                                           pace=cfg.pace, workers=cfg.workers)
    # the eight reads are independent; Transactions is split into date shards
    def read(name):
        if name == "transactions":
            return client.query_sharded(cfg.ds(name), "Txn date", notion.quarter_edges())
        return client.query_all(cfg.ds(name))
    return dict(zip(DATABASES, client.map(read, DATABASES)))


def build_from_notion(cfg, client=None):
    """The eight data sources, read and flattened."""
    return build_from_pages(cfg, read_pages(cfg, client))


def build_from_pages(cfg, pages):
    """The same snapshot from pages that are already in hand.

    `pages` is {data source name: [page]} for all eight of `DATABASES`, in the
    API's own shape. `build_from_notion` reads them itself; `finance.py sync`
    passes the ones its own run already read, so the monthly run does not pay
    for a second pull of the same eight databases. Both call this, so there is
    one flattening and the two snapshots cannot drift apart.

    Nothing here depends on the order the pages arrive in: every list the
    snapshot carries is sorted before it is returned.
    """
    def titles(name):
        return {p["id"].replace("-", ""): notion.plain(p, "Name") or ""
                for p in pages[name]}

    account_names = titles("accounts")
    account_nos = {p["id"].replace("-", ""): notion.plain(p, "Account no.") or ""
                   for p in pages["accounts"]}
    card_last4 = {p["id"].replace("-", ""): notion.plain(p, "Last 4") or ""
                  for p in pages["cards"]}
    category_names = titles("categories")
    payee_names = titles("payees")
    trip_names = titles("trips")
    period_names = titles("period_balances")

    categories = sorted(
        ({"name": notion.plain(p, "Name") or "",
          "bucket": notion.plain(p, "Bucket"),
          "group": notion.plain(p, "Group"),
          "need_class": notion.plain(p, "Need class"),
          "sort_order": notion.plain(p, "Sort order")}
         for p in pages["categories"]),
        key=lambda c: (c["sort_order"] if c["sort_order"] is not None else 1e9, c["name"]))
    categories = with_category_groups(categories)
    by_category = {c["name"]: c for c in categories}

    transactions = []
    for p in pages["transactions"]:
        category = relation_name(p, "Category", category_names)
        cat = by_category.get(category, {})
        day = notion.plain(p, "Txn date") or ""
        bank_txn_type = notion.plain(p, "Bank txn type") or ""
        account_id = (notion.plain(p, "Account") or [""])[0]
        transactions.append({
            "key": notion.plain(p, "Txn key") or "",
            "date": day[:10],
            "month": day[:7],
            "amount_minor": cents(notion.plain(p, "Amount")),
            "amount_sgd_minor": cents(formula(p, "Amount SGD")),
            "currency": notion.plain(p, "Currency") or "",
            "account": account_names.get(account_id, ""),
            "account_no": account_nos.get(account_id, ""),
            "bank_txn_type": bank_txn_type,
            "direction": notion.plain(p, "Direction") or "",
            "flow_class": notion.plain(p, "Flow class") or "",
            "merchant_raw": notion.plain(p, "Merchant raw") or "",
            "merchant_country": notion.plain(p, "Merchant country") or "",
            "counterparty": notion.plain(p, "Counterparty") or "",
            "memo": notion.plain(p, "Memo") or "",
            "reference": notion.plain(p, "Reference") or "",
            "payee": relation_name(p, "Payee", payee_names),
            "category": category,
            "need_class": cat.get("need_class"),
            "bucket": cat.get("bucket"),
            "group": cat.get("group"),
            "category_group": cat.get("category_group"),
            "card": relation_name(p, "Card", card_last4),
            "trip": relation_name(p, "Trip", trip_names),
            "synthetic": bank_txn_type == SYNTHETIC_TYPE,
            "period": relation_name(p, "Period", period_names),
        })

    periods = sorted(({
        "name": notion.plain(p, "Name") or "",
        "account": relation_name(p, "Account", account_names),
        "account_no": relation_name(p, "Account", account_nos),
        "period_end": (notion.plain(p, "Period end") or "")[:10],
        "currency": notion.plain(p, "Currency") or "",
        "opening_minor": cents(notion.plain(p, "Opening balance")),
        "closing_minor": cents(notion.plain(p, "Closing balance")),
        "total_in_minor": cents(notion.plain(p, "Total in")),
        "total_out_minor": cents(notion.plain(p, "Total out")),
        "fx_to_sgd": notion.plain(p, "FX rate to SGD"),
        "closing_sgd_minor": cents(formula(p, "Closing SGD")),
        "reconciled": bool(formula(p, "Reconciled")),
        "has_balances": bool(formula(p, "Has balances")),
    } for p in pages["period_balances"]), key=lambda b: b["name"])

    payees = sorted(({
        "name": notion.plain(p, "Name") or "",
        "kind": notion.plain(p, "Kind"),
        "country": notion.plain(p, "Country"),
        "default_category": relation_name(p, "Default category", category_names),
        "recurring": bool(notion.plain(p, "Recurring")),
        "txn_count": rollup_number(p, "Txn count"),
        "account_no": notion.plain(p, "Account no.") or "",
    } for p in pages["payees"]), key=lambda x: x["name"])

    accounts = sorted(({
        "name": notion.plain(p, "Name") or "",
        "account_no": notion.plain(p, "Account no.") or "",
        "institution": notion.plain(p, "Institution"),
        "class": notion.plain(p, "Class"),
        "subclass": notion.plain(p, "Subclass"),
        "base_currency": notion.plain(p, "Base currency"),
        "statements_from": notion.plain(p, "Statements from"),
        "statements_to": notion.plain(p, "Statements to"),
        "data_coverage": notion.plain(p, "Data coverage"),
        "txn_count": rollup_number(p, "Txn count"),
    } for p in pages["accounts"]), key=lambda x: x["name"])

    cards = sorted(({
        "name": notion.plain(p, "Name") or "",
        "last4": notion.plain(p, "Last 4") or "",
        "network": notion.plain(p, "Network"),
        "type": notion.plain(p, "Type"),
        "state": notion.plain(p, "State"),
    } for p in pages["cards"]), key=lambda x: (x["last4"], x["name"]))

    budgets = sorted(({
        "category": relation_name(p, "Category", category_names),
        "month": notion.plain(p, "Month") or "",
        "planned_minor": cents(notion.plain(p, "Planned")),
        "actual_minor": cents(notion.plain(p, "Actual")),
    } for p in pages["budgets"]), key=lambda b: (b["month"], b["category"]))

    trips = []
    for p in pages["trips"]:
        span = notion.plain_range(p, "Dates") or (None, None)
        start = (span[0] or "")[:10]
        trips.append({"name": notion.plain(p, "Name") or "", "start": start,
                      "end": ((span[1] or span[0]) or "")[:10],
                      "countries": sorted(notion.plain(p, "Countries") or ())})
    trips.sort(key=lambda t: (t["start"], t["end"], t["name"]))

    databases = [{"name": pretty(name), "data_source_id": cfg.ds(name),
                  "rows": len(pages[name]), "filter": "all"} for name in DATABASES]
    # labelled here rather than in build: `finance.py sync` saves this
    # snapshot itself, and a later analyse reuses it
    return labelled(assemble("notion", databases, transactions, periods, payees, categories,
                             accounts, cards, budgets, trips,
                             portfolio=read_portfolio(cfg), cpf=read_cpf(cfg),
                             category_groups=shipped_category_groups()), cfg)


def pretty(ds_name):
    """The database's display name, as meta.databases prints it."""
    return {"period_balances": "Period balances"}.get(ds_name, ds_name.capitalize())


# ---------------------------------------------------------------- csv

def load_categories_fallback(path=None):
    """The offline copy of the Categories rows: categories.json in the cache,
    else the repo's server/analytics/categories.json."""
    if path is None:
        path = CATEGORIES_FALLBACK if Path(CATEGORIES_FALLBACK).exists() else REPO_CATEGORIES
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return list(raw["categories"])


def shipped_category_groups():
    """The buckets that ship in the repo's categories.json, in order."""
    raw = json.loads(REPO_CATEGORIES.read_text(encoding="utf-8"))
    return list(raw.get("category_groups", []))


def retired_categories():
    """Categories the shipped list used to have, with what they were
    (Transport, split into public and private)."""
    raw = json.loads(REPO_CATEGORIES.read_text(encoding="utf-8"))
    return list(raw.get("retired_categories", []))


def with_category_groups(categories):
    """Each category with its bucket (`category_group`). Notion has no such
    property and an older offline copy lacks it, so a category without one
    takes the shipped list's, by name; one the shipped list does not know
    stays None. Only the record (kopifund.db) holds the user's own buckets."""
    raw = json.loads(REPO_CATEGORIES.read_text(encoding="utf-8"))
    shipped = {c["name"]: c.get("category_group")
               for c in raw.get("retired_categories", []) + raw["categories"]}
    return [{**c, "category_group": c.get("category_group") or shipped.get(c["name"])}
            for c in categories]


def write_categories_fallback(snapshot, path=None):
    """Refresh the offline copy from a Notion-built snapshot.

    A run that found the same categories rewrites nothing, so the file does not
    turn up in `git status` after every build with only its timestamp moved.
    Returns the path when it wrote, None when there was nothing to change.
    """
    path = Path(path if path is not None else CATEGORIES_FALLBACK)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and load_categories_fallback(path) == snapshot["categories"]:
        return None
    body = {"note": ("The Categories rows, copied out of Notion so a --from-csv "
                     "snapshot can classify without the network. Refresh it with "
                     "`python3 server/finance.py analyse --refresh`."),
            "generated_at": now(),
            "categories": snapshot["categories"]}
    write_atomic(path, json.dumps(body, indent=2, ensure_ascii=False) + "\n")
    return path


class UncheckedStatements(ValueError):
    """Parser CSVs that fail the block checks a normal run applies: a block
    that does not reconcile, a duplicate period or key, a row with no block.
    `problems` is the list of finance.model.Problem."""

    def __init__(self, problems):
        self.problems = list(problems)
        super().__init__(f"{len(self.problems)} problem(s) in the parser CSVs; nothing "
                         f"was built:\n" + "\n".join(p.line() for p in self.problems))


def wallet_folders(cfg):
    """The configured statement folders that hold a wallet's statements: a
    currency wallet's, or one whose layout is a wallet's (G21: read from the
    layout registry, server/finance/parsers/layouts.py). Money reaches a wallet only
    from the bank accounts. PayLah! is read by the DBS parser, so its folder is
    told apart by its name (setup.py names it dbs-paylah)."""
    from finance.parsers import layouts

    def is_wallet(f):
        lay = layouts.of_folder(f.parser, Path(f.path).name)
        return lay is not None and lay.kind == "wallet"
    return [f for f in getattr(cfg, "folders", ())
            if f.wallet or f.multi_currency or f.per_currency_subfolders or is_wallet(f)]


def wallet_account_nos(cfg, blocks=()):
    """Account numbers whose statements are a wallet's: the account_no a wallet
    folder names, plus the account of every block whose source PDF sits in one
    (a new user's folders name no account number) or that the DBS parser
    labels as PayLah. `blocks` are dicts with account_no and source, and
    optionally account."""
    folders = wallet_folders(cfg)
    out = {f.account_no for f in folders if f.account_no}
    base = Path(getattr(cfg, "root", ROOT))
    # a parser names its sources as it was given them: relative to the data
    # folder in a run, absolute when it was pointed at a full path
    roots = [p for f in folders
             for p in (f.path.rstrip("/") + "/", str(base / f.path).rstrip("/") + "/")]
    for b in blocks:
        src = str(b.get("source") or "")
        if b.get("account_no") and (any(src.startswith(r) for r in roots)
                                    or "PayLah" in str(b.get("account") or "")):
            out.add(b["account_no"])
    return out


def build_from_csv(cfg, csv_dir, rules_path=None, categories=None):
    """The same shape from the parser CSVs, the rules and categories.json.

    Trips and budgets are typed in Notion, so a CSV snapshot carries no trip on
    any row and no Budgets.Planned; `meta.notes` says so and the stats that care
    add a caveat.

    A parser writes its CSVs before it exits on a failed balance check, so the
    CSVs are put through the same block checks a normal run applies before a
    row is used; any problem raises UncheckedStatements and nothing is built.
    """
    parsed = sync.parse(cfg, csv_dir, echo=False)
    problems = list(parsed.problems) + sync.check_blocks(parsed)
    if problems:
        raise UncheckedStatements(problems)
    rules = sync.categorise(parsed, rules_path or sync.default_rules(cfg))
    categories = with_category_groups(
        list(categories if categories is not None else load_categories_fallback()))
    # a category the rows name that the copy lacks takes the shipped list's
    # values: a copy in the cache older than a new shipped category (Transport
    # (public) and (private)), or a category no longer shipped (Transport,
    # before split-transport) that a personal rule still names. A value the
    # copy has is never replaced.
    named = {r.get("category") for r in parsed.rows}
    have = {c["name"] for c in categories}
    raw = json.loads(REPO_CATEGORIES.read_text(encoding="utf-8"))
    categories += [{k: v for k, v in c.items() if k != "note"}
                   for c in raw["categories"] + raw.get("retired_categories", [])
                   if c["name"] in named and c["name"] not in have]
    by_category = {c["name"]: c for c in categories}

    fx = {b["period"]: num(b.get("fx_to_sgd")) for b in parsed.blocks}
    account_names = {}
    for b in parsed.blocks:
        if b.get("account") and b.get("account_no"):
            account_names.setdefault(b["account_no"], b["account"])

    transactions = []
    for r in parsed.rows:
        cat = by_category.get(r.get("category") or "", {})
        rate = fx.get(r.get("period"))
        amount = cents(r["amount"]) or 0
        transactions.append({
            "key": r["txn_key"],
            "date": r["date"],
            "month": (r["date"] or "")[:7],
            "amount_minor": amount,
            # the record's rule for a foreign row (v_txn): the block's rate, in integers
            "amount_sgd_minor": fx_minor(amount, rate_e9(rate)) if rate else amount,
            "currency": r["currency"],
            "account": account_names.get(r["account_no"], r.get("account") or ""),
            "account_no": r["account_no"],
            "bank_txn_type": r["bank_txn_type"],
            "direction": r["direction"],
            "flow_class": r["flow_class"],
            "merchant_raw": r["merchant"],
            "merchant_country": r["merchant_country"],
            "counterparty": r["counterparty"],
            "memo": r["memo"],
            "reference": r["reference"],
            "payee": r.get("payee") or "",
            "category": r.get("category") or "",
            "need_class": cat.get("need_class"),
            "bucket": cat.get("bucket"),
            "group": cat.get("group"),
            "category_group": cat.get("category_group"),
            "card": r.get("card_last4") or "",
            "trip": "",
            "synthetic": r["bank_txn_type"] == SYNTHETIC_TYPE,
            "period": r.get("period") or "",
        })

    periods = sorted(({
        "name": b["period"],
        "account": account_names.get(b["account_no"], b.get("account") or ""),
        "account_no": b["account_no"],
        "period_end": b["period_end"],
        "currency": b["currency"],
        "opening_minor": cents(num(b.get("opening"))),
        "closing_minor": cents(num(b.get("closing"))),
        "total_in_minor": cents(num(b.get("total_in"))),
        "total_out_minor": cents(num(b.get("total_out"))),
        "fx_to_sgd": num(b.get("fx_to_sgd")),
        "closing_sgd_minor": cents(num(b.get("closing_sgd"))),
        "reconciled": str(b.get("reconciled")).lower() in ("true", "1", "yes"),
        "has_balances": b.get("closing") not in ("", None),
    } for b in parsed.blocks), key=lambda b: b["name"])

    payees = payees_from_rules(rules, transactions)

    wallets = wallet_account_nos(cfg, parsed.blocks)
    accounts = sorted(({
        "name": name, "account_no": account_no, "institution": None, "class": None,
        # the CSVs carry no Accounts row, but the config's folders know which
        # statements are wallets, and coverage needs that to tell an idle
        # wallet from a missing bank statement
        "subclass": "Wallet" if account_no in wallets else None, "base_currency": None,
        "statements_from": min((b["period_end"] for b in periods
                                if b["account_no"] == account_no), default=None),
        "statements_to": max((b["period_end"] for b in periods
                              if b["account_no"] == account_no), default=None),
        "data_coverage": None,
        "txn_count": sum(1 for t in transactions if t["account_no"] == account_no),
    } for account_no, name in sorted(account_names.items())), key=lambda x: x["name"])

    cards = sorted(({"name": "", "last4": last4, "network": None, "type": None,
                     "state": None} for last4 in cfg.cards), key=lambda x: x["last4"])

    income = {c["name"] for c in categories if c.get("need_class") == "Income"}
    actuals = sync.budget_actuals(parsed, income)
    budgets = sorted(({"category": c, "month": m, "planned_minor": None,
                       "actual_minor": cents(v)} for (c, m), v in actuals.items()),
                     key=lambda b: (b["month"], b["category"]))

    databases = [{"name": pretty(n), "data_source_id": None,
                  "rows": rows, "filter": "parser output"}
                 for n, rows in (("transactions", len(transactions)),
                                 ("period_balances", len(periods)),
                                 ("payees", len(payees)),
                                 ("categories", len(categories)),
                                 ("accounts", len(accounts)),
                                 ("cards", len(cards)),
                                 ("budgets", len(budgets)),
                                 ("trips", 0))]
    return assemble("csv", databases, transactions, periods, payees, categories,
                    accounts, cards, budgets, [],
                    notes=["Trips and Budgets.Planned are typed in Notion, so a csv "
                           "snapshot carries no trip on any row and no planned budget.",
                           f"Categories came from {CATEGORIES_FALLBACK.name}."],
                    portfolio=read_portfolio(cfg), cpf=read_cpf(cfg),
                    category_groups=shipped_category_groups())


def payees_from_rules(rules, transactions):
    """One payee per distinct name in the rules, with the counts the rows give.

    The default category is the one carrying most of that payee's rows, ties to
    the larger absolute amount, which is the rule `finance.py payees` uses.
    """
    from finance.parsers import categorise as rules_module
    recurring = rules_module.recurring_payees(rules)
    first = {}
    for r in rules:
        if r["payee"]:
            first.setdefault(r["payee"], r)
    counts, amounts, seen = {}, {}, {}
    for t in transactions:
        if not t["payee"]:
            continue
        seen[t["payee"]] = seen.get(t["payee"], 0) + 1
        pair = (t["payee"], t["category"])
        counts[pair] = counts.get(pair, 0) + 1
        amounts[pair] = amounts.get(pair, 0) + abs(t["amount_sgd_minor"] or 0)
    out = []
    for name, rule in sorted(first.items()):
        mine = [(c, counts[(p, c)], amounts[(p, c)]) for (p, c) in counts if p == name]
        default = (max(mine, key=lambda x: (x[1], x[2], x[0]))[0] if mine
                   else rule["category"])
        out.append({"name": name, "kind": rule["kind"] or None,
                    "country": rule["country"] or None, "default_category": default,
                    "recurring": name in recurring, "txn_count": seen.get(name, 0),
                    "account_no": rule.get("account_no") or ""})
    return out


# ---------------------------------------------------------------- portfolio

def tiger_folder(cfg):
    """Where the exports live: `[analytics] tiger_folder`, the default when the
    key is absent, and "" when it is set empty, which switches the read off.

    `Config` answers this itself; a stand-in config object that predates the
    method is read directly so a test fixture does not have to grow one.
    """
    return analytics_folder(cfg, "tiger_folder", TIGER_FOLDER)


def analytics_folder(cfg, key, default):
    """A statement folder kept in `[analytics]` under `key` (G21: a layout with
    no rows, server/finance/parsers/layouts.py), `default` when the key is absent and
    "" when it is set empty. A config object with a method of that name
    answers itself."""
    if hasattr(cfg, key):
        return getattr(cfg, key)()
    section = getattr(cfg, "analytics", None) or {}
    value = section.get(key, default)
    return "" if value is None else str(value).strip()


def read_portfolio(cfg):
    """The Tiger Brokers annual exports, parsed, as `snapshot["portfolio"]`.

    The parser is in server/finance/parsers/ and is imported here rather than at the
    top of the module, the way `payees_from_rules` imports categorise: a
    snapshot must still build on a machine that has no brokerage statements and
    no parser, so a missing folder, an empty folder or a missing parse_tiger all
    give `statements: []` rather than an error. A parse that fails is not
    caught: a statement that cannot be read is a problem to fix, not a zero.
    """
    folder = tiger_folder(cfg)
    out = {"source": "tiger", "folder": folder, "statements": []}
    if not folder:
        return out
    path = Path(folder)
    if not path.is_absolute():
        path = Path(getattr(cfg, "root", ROOT)) / path
    if not path.is_dir():
        return out
    try:
        from finance.parsers import parse_tiger
    except ImportError:
        return out
    out["statements"] = sorted(parse_tiger.parse_folder(path),
                               key=lambda st: st["period"]["from"])
    return out


# ---------------------------------------------------------------- cpf

def cpf_folder(cfg):
    """Where the CPF yearly statements live: `[analytics] cpf_folder`, the
    default when the key is absent, and "" when it is set empty, which switches
    the read off.

    Read the same way as `tiger_folder`, so a stand-in config object that
    carries only an `analytics` dict still answers.
    """
    return analytics_folder(cfg, "cpf_folder", CPF_FOLDER)


def read_cpf(cfg):
    """The CPF yearly statements, parsed, as `snapshot["cpf"]`.

    The parser is in server/finance/parsers/ and is imported here rather than at the
    top of the module, exactly as `read_portfolio` imports parse_tiger: a
    snapshot must still build on a machine with no CPF statements and no
    parser, so a missing folder, an empty folder or a missing parse_cpf all give
    `statements: []` rather than an error. A parse that fails is not caught: a
    statement that cannot be read is a problem to fix, not a zero.

    A statement whose year does not reconcile (opening plus transactions is
    not the closing) is left out of `statements` by parse_folder and listed
    under `rejected` as {year, file, off}, so the cpf stat can say which year
    failed instead of counting figures nothing has checked.
    """
    folder = cpf_folder(cfg)
    out = {"source": "cpf", "folder": folder, "statements": [], "rejected": []}
    if not folder:
        return out
    path = Path(folder)
    if not path.is_absolute():
        path = Path(getattr(cfg, "root", ROOT)) / path
    if not path.is_dir():
        return out
    try:
        from finance.parsers import parse_cpf
    except ImportError:
        return out
    rejected = []
    out["statements"] = sorted(parse_cpf.parse_folder(path, rejected=rejected),
                               key=lambda st: st["year"])
    out["rejected"] = sorted(rejected, key=lambda r: (r["year"], r["file"]))
    return out


def checked_cpf(statements, folder="statements/cpf", rejected=()):
    """A cpf block from statements already parsed (kopifund.db stores them),
    put through the same balance check parse_folder applies, so a year stored
    before that check existed cannot reach a snapshot either. `rejected` is
    the years a load already turned away ({year, file, off}), listed with the
    ones this check turns away."""
    from finance.parsers import parse_cpf
    balanced, failed = parse_cpf.split_reconciled(
        sorted(statements, key=lambda st: (st["year"], st["file"])))
    return {"source": "cpf", "folder": folder, "statements": balanced,
            "rejected": sorted([*rejected, *failed], key=lambda r: (r["year"], r["file"]))}


# ---------------------------------------------------------------- assembly

def now():
    return datetime.now(SGT).replace(microsecond=0).isoformat()


def assemble(source, databases, transactions, periods, payees, categories,
             accounts, cards, budgets, trips, notes=(), portfolio=None,
             cpf=None, category_groups=()):
    """The snapshot dict, with every list in its sorted order.

    `category_groups` is the buckets the categories sit in ([{name, kind,
    sort_order}], in display order); each category and transaction names its
    own in `category_group`.

    `portfolio` is the brokerage block `read_portfolio` built and `cpf` the CPF
    block `read_cpf` built. Neither is one of the eight data sources, so
    `meta.databases` never mentions them; the files they were read from are
    listed in `meta.portfolio_files` and `meta.cpf_files` instead. A snapshot
    written before either block existed has neither key, so every reader asks
    for them with `snapshot.get("portfolio")` and `snapshot.get("cpf")`.
    """
    transactions = sorted(transactions, key=lambda r: (r["date"], r["key"]))
    portfolio = portfolio or {"source": "tiger", "folder": "", "statements": []}
    cpf = cpf or {"source": "cpf", "folder": "", "statements": []}
    cpf = {**cpf, "rejected": list(cpf.get("rejected") or [])}
    return {"meta": {"generated_at": now(), "source": source,
                     "databases": databases, "fx_note": FX_NOTE,
                     "notes": list(notes),
                     "portfolio_files": [st["file"] for st in portfolio["statements"]],
                     "cpf_files": [st["file"] for st in cpf["statements"]]},
            "transactions": transactions, "periods": periods, "payees": payees,
            "categories": categories, "category_groups": list(category_groups),
            "accounts": accounts, "cards": cards,
            "budgets": budgets, "trips": trips, "portfolio": portfolio,
            "cpf": cpf}


def build(cfg, source="notion", csv_dir=None, client=None, rules_path=None):
    """The snapshot dict. `source` is "sqlite" (kopifund.db, the record),
    "notion" or "csv"; "csv" needs `csv_dir`."""
    if source == "sqlite":
        from finance import db
        conn = db.connect()
        try:
            blocks = [dict(r) for r in conn.execute(
                "SELECT account_no, source FROM period WHERE archived_at IS NULL")]
            return labelled(db.snapshot(conn, wallets=wallet_account_nos(cfg, blocks)), cfg)
        finally:
            conn.close()
    if source == "csv":
        if not csv_dir:
            raise ValueError("source='csv' needs csv_dir")
        return labelled(build_from_csv(cfg, csv_dir, rules_path=rules_path), cfg)
    if source != "notion":
        raise ValueError(f"unknown source {source!r}")
    return build_from_notion(cfg, client=client)


def labelled(snapshot, cfg):
    """Each account with `label`, the name the stats print, and `bank`
    (server/finance/account_names.py). Made here because the config's statement
    folders name an account the record knows only by its number. G13: no stat
    prints a full account number."""
    from finance import account_names
    accounts = snapshot.get("accounts") or []
    for a, d in zip(accounts, account_names.describe(accounts, getattr(cfg, "folders", ()))):
        a["label"], a["bank"] = d["label"], d["bank"]
    return snapshot


# ---------------------------------------------------------------- cache

def dumps(obj):
    """The one JSON spelling the cache uses, so a rebuild diffs cleanly."""
    return json.dumps(obj, indent=2, ensure_ascii=False, allow_nan=False) + "\n"


def snapshot_name(day=None):
    return f"snapshot-{day or date.today().isoformat()}.json"


def write_atomic(path, text):
    """Write `text` to `path` so a reader sees the old file or the new one,
    never half of either: a temp file in the same folder, then os.replace. The
    app reads these files while `analyse` writes them."""
    path = Path(path)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return path


def read_latest(cache=CACHE):
    """cache/latest.json, or an empty pointer when there is none."""
    path = Path(cache) / "latest.json"
    if not path.exists():
        return {"snapshot": None, "results": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def write_latest(latest, cache=CACHE):
    Path(cache).mkdir(parents=True, exist_ok=True)
    write_atomic(Path(cache) / "latest.json", dumps(latest))


def save(snapshot, cache=CACHE, day=None):
    """Write the snapshot and point latest.json at it. Returns the path."""
    cache = Path(cache)
    (cache / "results").mkdir(parents=True, exist_ok=True)
    name = snapshot_name(day)
    write_atomic(cache / name, dumps(snapshot))
    latest = read_latest(cache)
    latest["snapshot"] = name
    latest.setdefault("results", {})
    write_latest(latest, cache)
    return cache / name


def load(path):
    """Read a snapshot back, stamping `meta.file` with the name it was read
    from. The stats put that name in every result's `snapshot` field, and it is
    not in the file itself so that the bytes stay identical on a rebuild."""
    path = Path(path)
    out = json.loads(path.read_text(encoding="utf-8"))
    out["meta"]["file"] = path.name
    return out


def in_cents(snapshot):
    """G20: whether a snapshot holds its amounts in cents only: every row
    carries `amount_minor` and no `amount_sgd`, every block `closing_minor`
    and no `closing`, every budget `planned_minor` and no `planned`. A
    snapshot saved before that has its amounts in dollars, which nothing
    reads, so it is built again rather than reused."""
    def cents(part, minor, dollars):
        return all(minor in x and dollars not in x for x in snapshot.get(part) or [])
    return (cents("transactions", "amount_minor", "amount_sgd") and cents("periods", "closing_minor", "closing")
            and cents("budgets", "planned_minor", "planned"))


def today_snapshot(cache=CACHE, day=None):
    """The path of today's snapshot if it is already built, else None."""
    path = Path(cache) / snapshot_name(day)
    return path if path.exists() else None
