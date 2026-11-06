"""Every statement layout Kopifund reads, declared once (G21).

A layout is one kind of statement PDF: the DBS consolidated statement, the
YouTrip wallet statement, the CPF yearly statement. Each parser module in this
package declares the layouts it reads with `register(Layout(...))` at import:
the parser that reads it, the Upload page's words for it, its layout test (and
with it the account number and currency the statement prints), its statement
folder, its kind and how an upload of it is named. The DBS module reads four
layouts and declares four.

Everything that used to keep its own list of layouts reads this one instead:
the Upload page's sniffing (server/finance/sniff.py), the run (server/finance/sync.py), the
upload's file names and folder checks (server/finance/monthly.py), Setup's provider
list (server/finance/setup.py), the select values a parser can emit before any
statement has (server/finance/model.py) and the layouts table in docs/parsing.md
(docs/gen_parsing.py). A new layout is one module: a `parse_<name>.py` here that
registers its layouts, and nothing else to edit.

A module also registers the banks its layouts name, with `register_bank`: the
key of the mark the app draws and the words that give the bank away in an
account's name. With each layout's `bank`, `folder_title` and
`folder_pattern`, those are how the app names and marks an account
(server/finance/account_names.py), which folders hold a wallet
(server/finance/snapshot.py) and what Setup and the upload picker draw, so a new bank
is named and marked with no list to edit outside its module.

Each layout says how far it has been checked, in `status`: CHECKED once a real
statement has parsed and balanced through it, SAMPLE while it is built from a
public sample only. A new layout starts at SAMPLE. Setup shows those words
beside each provider and docs/parsing.md's table prints them, so both say it
the same way.

`every()` imports every `parse_*` module of the package the first time it is
asked, so a module is found by its name alone. Stdlib only; nothing here reads
a data folder.
"""
import importlib
import pkgutil
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

# what a statement of the layout is to the owner. A brokerage or CPF statement
# is a position, never transaction rows (SPEC.md, "Supported statements")
KINDS = ("bank account", "wallet", "card", "brokerage", "CPF")

# how far a layout has been checked, in the words Setup and docs/parsing.md show
CHECKED = "checked on a real statement"
SAMPLE = "from a public sample"
STATUSES = (CHECKED, SAMPLE)


class NotThisLayout(ValueError):
    """An upload is not a statement of the layout its folder reads. The message
    is shown to the owner as it is."""


@dataclass(frozen=True)
class Seen:
    """What a layout test read off a statement it matched."""
    currency: str = ""                # a currency wallet's currency, else ""
    numbers: tuple = ()               # the account numbers it prints as its own


@dataclass(frozen=True)
class Layout:
    name: str            # "consolidated": what sniff calls it
    parser: str          # "dbs": the `parser =` a [[folder]] in config.toml names
    module: str          # run as `python3 -m <module> <folder> ... --out <dir>`
    order: int           # where it sits in Setup's list, the doc's table and every loop over layouts
    kind: str            # one of KINDS
    label: str           # the Upload page's words: "a DBS consolidated statement", "{ccy}" for a wallet's currency
    folder: str          # its statement folder under statements/: "dbs-multiplier", or "youtrip-{ccy}" per currency
    setup: str           # Setup's words for that folder, "{ccy}" filled per currency
    test: Callable       # the layout test: text -> Seen, or None when the text is not this layout
    name_file: Callable  # (pdf, read_text, wallet, today) -> (year folder, file name) for an upload
    title: str = ""      # docs/parsing.md: the layout's name in the table
    detected_by: str = ""     # docs/parsing.md: what the layout test looks for
    balance_check: str = ""   # docs/parsing.md: what its balance check compares
    csv: str | None = None    # where the run puts its CSVs, under the run's folder ("" the top); None: it produces no rows
    currencies: tuple = ()    # a per-currency folder: the currencies Setup offers
    multi_currency: bool = False   # one export holds every currency wallet
    number_in_config: bool | tuple = True  # does it print the number the config names its folder by: yes, never, or for these currencies only
    export: str = ""     # an export named by the day it is downloaded: what a PDF must read as before it is kept
    new_types: tuple = ()     # Bank txn type values the parser can emit before any statement has printed one
    bank: str = ""            # the Bank (below) whose mark its accounts carry: "dbs", "posb"
    folder_title: str = ""    # the app's name for an account kept in its folder: "DBS Multiplier", "YouTrip"
    folder_pattern: str = ""  # a parser with several layouts: what in a folder's own name says it holds this one
    status: str = SAMPLE      # one of STATUSES: CHECKED once a real statement has parsed and balanced

    @property
    def per_currency(self):
        """One folder per currency (YouTrip, Wise): the folder gives the
        parser its currency, or must agree with the statement's heading."""
        return "{ccy}" in self.folder

    @property
    def config_key(self):
        """A layout with no rows has its folder in `[analytics]`, under this key."""
        return f"{self.parser}_folder"

    def prints_config_number(self, currency=""):
        n = self.number_in_config
        return n if isinstance(n, bool) else currency in n


@dataclass(frozen=True)
class Bank:
    """A bank, wallet or provider whose mark the app draws beside an account
    (apps/web/src/components/BankMark.tsx). A parser module registers the banks its
    layouts name with `register_bank`, beside its layouts."""
    key: str             # "uob": the mark's key, a Bank in apps/web/src/types.gen.ts
    words: str           # a pattern that gives it away in an account's name or institution
    order: int = 50      # the order the words are tried in: PayLah and POSB before DBS, both DBS products
    name: str = ""       # the bank as the owner names it: "DBS", "Tiger Brokers", "Trust Bank"


# Banks the app names and marks before it reads their statements (the Upload
# page shows them as coming soon). A module that registers a bank of the same
# key takes its place.
COMING_SOON = (Bank("trust", r"\btrust\b", order=95, name="Trust Bank"),)

_LAYOUTS = {}
_BANKS = {}
_LOCK = threading.Lock()          # around _LAYOUTS only, never held while importing
_LOAD_LOCK = threading.Lock()     # one thread imports the modules; the rest wait
_loaded = False


def register(layout):
    """Add one layout to the registry; a parser module calls this at import.
    A second layout of the same name from another module is a mistake."""
    if layout.kind not in KINDS:
        raise ValueError(f"layout {layout.name}: kind {layout.kind!r} is not one of {KINDS}")
    if layout.status not in STATUSES:
        raise ValueError(f"layout {layout.name}: status {layout.status!r} is not one of {STATUSES}")
    with _LOCK:
        had = _LAYOUTS.get(layout.name)
        if had is not None and had.module != layout.module:
            raise ValueError(f"layout {layout.name} is registered by {had.module} already")
        _LAYOUTS[layout.name] = layout
    return layout


def register_bank(bank):
    """Add one bank to the registry; a parser module calls this at import,
    before the layouts that name it."""
    with _LOCK:
        had = _BANKS.get(bank.key)
        if had is not None and had != bank:
            raise ValueError(f"bank {bank.key} is registered already, as {had}")
        _BANKS[bank.key] = bank
    return bank


def _load():
    """Import every parse_* module once, so each registers its layouts."""
    global _loaded
    if _loaded:
        return
    with _LOAD_LOCK:
        if _loaded:
            return
        for m in pkgutil.iter_modules([str(Path(__file__).parent)]):
            if m.name.startswith("parse_"):
                importlib.import_module(f"{__package__}.{m.name}")
        _loaded = True


def every():
    """Every layout, in order."""
    _load()
    with _LOCK:
        return tuple(sorted(_LAYOUTS.values(), key=lambda lay: (lay.order, lay.name)))


def get(name):
    """The layout called `name`, or None."""
    _load()
    with _LOCK:
        return _LAYOUTS.get(name)


def of_parser(parser):
    """The layouts one parser reads, in order."""
    return tuple(lay for lay in every() if lay.parser == parser)


def banks():
    """Every bank, registered or coming soon, in the order their words are tried."""
    _load()
    with _LOCK:
        have = dict(_BANKS)
    for b in COMING_SOON:
        have.setdefault(b.key, b)
    return tuple(sorted(have.values(), key=lambda b: (b.order, b.key)))


def supported():
    """What the Upload page lists for a statement no layout knows (G21): the
    banks whose statements Kopifund reads, in the order of their first layout,
    each {bank, name, status}, CHECKED when a real statement has checked any
    of its layouts and SAMPLE otherwise; then the banks coming soon, each
    {bank, name}."""
    named = {b.key: b.name or b.key for b in banks()}
    reads = {}
    for lay in every():
        if lay.bank:
            reads[lay.bank] = CHECKED if CHECKED in (reads.get(lay.bank), lay.status) else SAMPLE
    return ([{"bank": key, "name": named.get(key, key), "status": status} for key, status in reads.items()],
            [{"bank": b.key, "name": b.name} for b in COMING_SOON if b.key not in reads])


def of_folder(parser, name):
    """The layout a statement folder of `parser` holds, by the folder's own
    name (`name`, less any wallet currency): its parser's one layout, else the
    one whose `folder_pattern` the name matches, else the one Setup names that
    folder for, else the parser's first. None for a parser with no layout."""
    its = of_parser(parser)
    if len(its) <= 1:
        return its[0] if its else None
    name = str(name or "")
    return (next((lay for lay in its if lay.folder_pattern and re.search(lay.folder_pattern, name, re.I)), None)
            or next((lay for lay in its if lay.folder == name), None)
            or its[0])


def runs():
    """(parser, module, csv folder) for every parser that produces rows, once
    each, in order: what the run starts."""
    out = {}
    for lay in every():
        if lay.csv is not None:
            out.setdefault(lay.parser, (lay.parser, lay.module, lay.csv))
    return list(out.values())


# ---------------------------------------------------------------- naming an upload

def by_months(names):
    """(year folder, file name) from the period names a statement parsed to:
    its month, or first and last month, `2026-03.pdf` or `2026-01_2026-03.pdf`."""
    months = sorted({m.group(0) for n in names
                     for m in [re.search(r"\d{4}-\d{2}(?=$|\D)", n.split(" · ")[2] if " · " in n else n)] if m})
    if not months:
        raise NotThisLayout("This PDF is not a statement this account can read")
    name = months[0] if len(months) == 1 else f"{months[0]}_{months[-1]}"
    return months[-1][:4], f"{name}.pdf"


def by_download_day(pdf, read_text, wallet, today):
    """An all-time export: named for the day it was exported, which is the day
    the owner downloads it."""
    return str(today.year), f"export-{today.isoformat()}.pdf"
