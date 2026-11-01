#!/usr/bin/env python3
"""The config file, the property model, and the small value types.

One place decides what a Notion page for a parsed row or block should look
like, so the writer and the checker can never disagree: `txn_properties` and
`period_properties` return a *canonical* property dict, meaning every property
the loader owns is present, with a comparable Python value (see `canonical`).
The same shape comes back out of a live page through `page_properties`, so a
diff is a plain dict comparison.
"""
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from . import notion
from .paths import SERVER, config_path, current_statements, data_root

ROOT = SERVER   # the code; the data lives in paths.data_root()

# Property name -> Notion API type, for the data sources the loader writes to.
# These are exactly the loader-owned properties: anything not listed here is
# the owner's or Notion's, and the sync never touches or compares it.
TXN_TYPES = {"Name": "title", "Txn date": "date", "Purchase date": "date", "Amount": "number",
             "Balance after": "number", "Currency": "select", "Direction": "select",
             "Bank txn type": "select", "Flow class": "select", "Merchant country": "select",
             "Card": "relation", "Merchant raw": "rich_text", "Reference": "rich_text",
             "Counterparty": "rich_text", "Memo": "rich_text", "FX original": "rich_text",
             "FX rate": "number", "Txn key": "rich_text", "Account": "relation", "Period": "relation",
             "Payee": "relation", "Category": "relation", "Trip": "relation"}
PERIOD_TYPES = {"Name": "title", "Period end": "date", "Currency": "select",
                "Opening balance": "number", "Closing balance": "number",
                "Total in": "number", "Total out": "number", "FX rate to SGD": "number",
                "Account": "relation"}
PAYEE_TYPES = {"Name": "title", "Kind": "select", "Country": "select",
               "Raw patterns": "rich_text", "Account no.": "rich_text",
               "Recurring": "checkbox", "Default category": "relation"}
PAYEE_FIELDS = ("Kind", "Country", "Raw patterns", "Account no.", "Recurring",
                "Default category")

# A Categories row as the mirror creates it, for a category the record holds
# and Notion lacks (G13). The mirror only creates these rows: one already in
# Notion is never patched.
CATEGORY_TYPES = {"Name": "title", "Bucket": "select", "Group": "select",
                  "Need class": "select", "Sort order": "number"}

# The rollup step's three targets. Budgets.Planned is the owner's and is
# deliberately absent: the loader must never be able to write it.
BUDGET_TYPES = {"Name": "title", "Category": "relation", "Month": "rich_text",
                "Need class": "select", "Actual": "number", "Current": "checkbox"}
# Trips Expenses is the exception to the comment above: of these the rollup
# writes only Dates, copied from the trip planner. The rest are the owner's and
# are listed because the map doubles as the shape of a Trips row, which the
# tests build from. Countries is a rollup over the planner in Notion, so the
# type here is what a row would carry, not something the loader may write.
TRIP_TYPES = {"Name": "title", "Dates": "date_range", "Countries": "multi_select",
              "Notes": "rich_text", "Extra keys": "rich_text"}
ACCOUNT_ROLLUP_TYPES = {"Statements from": "date", "Statements to": "date",
                        "Data coverage": "select"}

def reachable_options():
    """Values a parser can produce that no statement has produced yet. Notion
    rejects an unknown select value, so the first row of a kind the wallets
    have never printed would stop a monthly run at unknown-option;
    `add-options` adds these ahead of time so it cannot. G21: each layout
    declares its own (`new_types` in server/finance/parsers/layouts.py), beside the
    TYPE_RULES and type branches that emit them."""
    from .parsers import layouts
    return {("transactions", "Bank txn type"): tuple(
        t for lay in layouts.every() for t in lay.new_types)}

# Select properties whose parsed values must already exist as options in Notion.
SELECT_CHECKS = (("period_balances", "Currency", "currency"),
                 ("transactions", "Currency", "currency"),
                 ("transactions", "Direction", "direction"),
                 ("transactions", "Bank txn type", "bank_txn_type"),
                 ("transactions", "Flow class", "flow_class"),
                 ("transactions", "Merchant country", "merchant_country"))


@dataclass(frozen=True)
class Problem:
    """One thing doctor found wrong. `subject` names what is wrong (a file, a
    period, a value); `fix` says what the owner should do about it."""
    code: str
    subject: str
    fix: str
    # G13: the statement files the problem is about, root-relative, when it is
    # about files (same-file-two-places, duplicate-period); never shown, matched on
    paths: tuple = ()

    def line(self):
        return f"  {self.code:22s} {self.subject}\n      fix: {self.fix}"


@dataclass(frozen=True)
class Folder:
    """One statement folder from the config, relative to the data folder."""
    path: str
    parser: str
    account_no: str = ""
    per_currency_subfolders: tuple = ()
    multi_currency: bool = False     # one export holds every currency wallet
    wallet: str = ""                 # this folder is one currency wallet (YouTrip, Wise)

    def pdfs(self, root):
        return current_statements((root / self.path).rglob("*.pdf"))


@dataclass
class Config:
    """The data folder's config.toml, read once."""
    root: Path
    version: str
    token_env: str
    token_command: str
    pace: float = 0.35               # seconds between API requests, all threads together
    workers: int = 4                 # threads for independent calls; 1 is sequential
    data_sources: dict = field(default_factory=dict)
    accounts: dict = field(default_factory=dict)
    cards: dict = field(default_factory=dict)
    folders: list = field(default_factory=list)
    hub: dict = field(default_factory=dict)          # [hub]: status_block id
    analytics: dict = field(default_factory=dict)    # [analytics]: names and paths
    ai: dict | None = None    # [ai]: the payee-suggestion provider (G12); None when the section is absent

    def ds(self, name):
        return self.data_sources[name]

    def folders_for(self, parser):
        return [f for f in self.folders if f.parser == parser]

    def wallet_accounts(self):
        """The account numbers whose statements come per currency wallet,
        one file per wallet (YouTrip, Wise) or one export holding every
        wallet (Revolut). A row on one of those in a currency other than SGD was
        spent abroad, whatever the merchant string says about the country."""
        return {f.account_no for f in self.folders
                if f.per_currency_subfolders or f.multi_currency or f.wallet}


def all_pdfs(cfg):
    """Every statement PDF under a configured folder, superseded ones included
    (the record keeps a row for each), dot-files not."""
    return [p for f in cfg.folders for p in sorted((cfg.root / f.path).rglob("*.pdf"))
            if not p.name.startswith(".")]


def pdf_parsers(cfg):
    """G17 (T10): {statement PDF: the parser of the folder it sits in}, for
    statement_file.parser. A file under two folders takes the first one's."""
    out = {}
    for f in cfg.folders:
        for p in (cfg.root / f.path).rglob("*.pdf"):
            out.setdefault(p, f.parser)
    return out


class ConfigMissing(Exception):
    """No config.toml in the data folder, or one that is not valid TOML."""


def load_config(path=None, root=None):
    """Read the data folder's config.toml into a Config.

    `path` defaults to config.toml in the data folder and `root`, which every
    folder path is relative to, to the data folder itself (server/finance/paths.py).

    Raises ConfigMissing, rather than a traceback, when the file is absent or
    malformed: a new data folder has no config.toml, and the message says how
    to make one."""
    path = Path(path) if path is not None else config_path()
    root = Path(root) if root is not None else data_root()
    if not path.is_file():
        raise ConfigMissing(
            f"no {path}\n"
            "run `python3 server/finance.py migrate --from <old finance folder>`, or copy "
            "server/finance/config.example.toml there and fill it in")
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise ConfigMissing(f"{path} is not valid TOML: {e}") from e
    n = raw.get("notion", {})
    return Config(root=Path(root), version=n.get("version", "2025-09-03"),
                  token_env=n.get("token_env", "NOTION_TOKEN"),
                  token_command=n.get("token_command", ""),
                  pace=float(n.get("pace", 0.35)), workers=int(n.get("workers", 4)),
                  data_sources=raw.get("data_sources", {}),
                  accounts=raw.get("accounts", {}), cards=raw.get("cards", {}),
                  hub=raw.get("hub", {}), analytics=raw.get("analytics", {}),
                  ai=raw.get("ai"),     # an empty [ai] is {} and means on, with provider auto
                  folders=[Folder(path=f["path"], parser=f["parser"],
                                  account_no=f.get("account_no", ""),
                                  per_currency_subfolders=tuple(
                                      f.get("per_currency_subfolders", ())),
                                  multi_currency=bool(f.get("multi_currency", False)),
                                  wallet=str(f.get("wallet", "")).upper())
                           for f in raw.get("folder", [])])


# ---------------------------------------------------------------- values

def canonical(value, kind):
    """One comparable Python value per property type: '' for empty text, None
    for an empty number, select or date, and a sorted tuple of dashless page
    ids for a relation. Numbers are rounded to six decimals so that a float
    that has been through JSON compares equal to the one that went in.

    A checkbox is a bool, a multi_select is a sorted tuple of option names, and
    a date_range is a (start, end) pair where a range with no end keeps None,
    so that a one-day trip and a range ending on its own start day stay
    distinguishable.
    """
    if kind in ("title", "rich_text"):
        return str(value) if value not in (None, "") else ""
    if kind == "number":
        return None if value in (None, "") else round(float(value), 6)
    if kind in ("select", "date"):
        return value or None
    if kind == "checkbox":
        return bool(value)
    if kind == "multi_select":
        return tuple(sorted(str(v) for v in (value or ())))
    if kind == "date_range":
        if not value:
            return None
        start, end = value if isinstance(value, (tuple, list)) else (value, None)
        return (start or None, end or None)
    if kind == "relation":
        return tuple(sorted(str(v).replace("-", "") for v in (value or ())))
    raise ValueError(f"unknown property type {kind!r}")


def canonicalise(values, types):
    """{name: raw value} -> the canonical dict, with every property present.
    Accepts the "date:NAME:start" keys the earlier loader used to emit."""
    flat = {}
    for name, val in values.items():
        if name.startswith("date:"):
            _, base, part = name.split(":", 2)
            if part == "start":
                flat[base] = val
            continue
        flat[name] = val
    return {name: canonical(flat.get(name), kind) for name, kind in types.items()}


def page_properties(page, types):
    """The canonical dict of a live Notion page."""
    return {name: canonical(notion.plain_range(page, name) if kind == "date_range"
                            else notion.plain(page, name), kind)
            for name, kind in types.items()}


def diff(want, have):
    """Property names where two canonical dicts differ, in `want` order."""
    return [k for k in want if want[k] != have.get(k)]


def num(x):
    return float(x) if x not in ("", None) else None


# ---------------------------------------------------------------- page bodies

def period_properties(cfg, block):
    """The canonical Period balances page for one parser block."""
    account = cfg.accounts[block["account_no"]]
    return canonicalise({"Name": block["period"],
                         "Period end": block["period_end"],
                         "Currency": block["currency"],
                         "Opening balance": num(block["opening"]),
                         "Closing balance": num(block["closing"]),
                         "Total in": num(block["total_in"]),
                         "Total out": num(block["total_out"]),
                         "FX rate to SGD": num(block.get("fx_to_sgd")),
                         "Account": [account]}, PERIOD_TYPES)


def txn_properties(cfg, row, period_id, payee_ids=(), category_ids=(), trip_ids=(),
                   fx_rate=None):
    """The canonical Transactions page for one parsed row.

    `period_id` is the Period balances page the row belongs to; the payee and
    category relations come from the rules and the trip from the Trips rows
    (see sync.trip_targets), so that Trip diffs like every other loader-owned
    property rather than needing a check of its own. `fx_rate` is the block's
    fx_to_sgd copied onto the row (None for SGD blocks): the Amount SGD formula
    reads it from the row because a rollup cannot see a formula that depends on
    another rollup, which is what the Trips Spend SGD rollup needs.
    """
    amount = float(row["amount"])
    label = row["merchant"] or row["counterparty"] or row["bank_txn_type"]
    card = [cfg.cards[row["card_last4"]]] if row["card_last4"] else []
    return canonicalise({"Name": f"{row['date']} · {label} · {amount:+,.2f}",
                         "Txn date": row["date"],
                         "Purchase date": row["purchase_date"] or None,
                         "Amount": amount,
                         "Balance after": num(row["balance_after"]),
                         "Currency": row["currency"],
                         "Direction": row["direction"],
                         "Bank txn type": row["bank_txn_type"],
                         "Flow class": row["flow_class"],
                         "Merchant country": row["merchant_country"] or None,
                         "Card": card,
                         "Merchant raw": row["merchant"],
                         "Reference": row["reference"],
                         "Counterparty": row["counterparty"],
                         "Memo": row["memo"],
                         "FX original": row["fx_original"],
                         "FX rate": num(fx_rate),
                         "Txn key": row["txn_key"],
                         "Account": [cfg.accounts[row["account_no"]]],
                         "Period": [period_id],
                         "Payee": list(payee_ids),
                         "Category": list(category_ids),
                         "Trip": list(trip_ids)}, TXN_TYPES)
