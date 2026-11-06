"""Which of the owner's accounts a statement PDF belongs to, read from its own
text (G13).

The Upload page takes PDFs from any bank at once. Each one's text goes through
every layout's own test (server/finance/parsers/layouts.py, the registry each parser
declares its layouts in; docs/parsing.md, "The layouts"): what matched says the
parser, the layout and, for a currency wallet, the currency, and the account
numbers the statement prints say which account it is. The numbers are compared
with the ones each statement folder is known by: its `account_no` in the
config and the numbers its statements already print.

Nothing is guessed. A file that matches more than one layout, or more than one
folder this cannot tell apart, comes back with the folders it could be, and
the owner chooses; nothing is saved for it until then. A folder is only ever
chosen when exactly one fits. A file that matches no layout at all is a
statement Kopifund does not read (G21): its Route says so in `known`, and the
Upload page names it as not supported and keeps nothing.

Stdlib only. This reads statements and never writes, moves or renames one.
"""
import re
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path

# every layout a parser declares (G21); Place has a method called layouts
from .parsers import layouts as registry
from .pdftext import pdf_text as read_pdf


@dataclass(frozen=True)
class Found:
    """What one statement says it is."""
    parser: str
    layout: str
    currency: str = ""                    # a wallet statement's currency, else ""
    numbers: frozenset = field(default_factory=frozenset)   # the account numbers it prints as its own

    def label(self):
        """The statement as the owner would name it, for the Upload page."""
        return registry.get(self.layout).label.format(ccy=self.currency)


def per_currency(layout):
    """True for a currency wallet's layout (YouTrip, Wise): one folder per
    currency, and the folder gives the parser its currency or must agree with
    the heading."""
    found = registry.get(layout)
    return bool(found and found.per_currency)


def norm(number):
    """One spelling of an account number to compare: letters and digits only,
    upper case. 130-24680-5 and 130246805 are one number."""
    return re.sub(r"[^0-9A-Z]", "", str(number or "").upper())


def _numbers(*values):
    return frozenset(n for n in (norm(v) for v in values) if n)


# ---------------------------------------------------------------- one statement

def sniff(text):
    """Every layout the text passes, as a list of Found. One entry is a
    statement this can place; none, or more than one, is for the owner."""
    out = []
    for layout in registry.every():
        try:
            seen = layout.test(text)
        except Exception:                 # a layout test that cannot read it has not matched
            seen = None
        if seen is not None:
            out.append(Found(layout.parser, layout.name, seen.currency, _numbers(*seen.numbers)))
    return out


# ---------------------------------------------------------------- the folders

def pdf_text(pdf):
    """A statement's text: the dump the parsers left in the cache when it is
    as new as the PDF, else pdftotext's, which writes nothing."""
    from .paths import statements_dir, text_dir
    pdf = Path(pdf)
    try:
        dump = text_dir() / pdf.resolve().relative_to(statements_dir().resolve()).with_suffix(".txt")
        if dump.is_file() and dump.stat().st_mtime >= pdf.stat().st_mtime:
            return dump.read_text(errors="replace")
    except ValueError:                    # not under statements/: no dump of its own
        pass
    return read_pdf(pdf)


# what each statement already in a folder says it is, by (path, size, mtime):
# a statement is never rewritten, so the answer for one file never changes
_SEEN = {}
_SEEN_LOCK = threading.Lock()


def seen(pdf):
    """sniff() of a statement on disk, remembered. A file that cannot be read
    says nothing."""
    p = Path(pdf)
    try:
        st = p.stat()
    except OSError:
        return []
    key = (str(p.resolve()), st.st_size, st.st_mtime_ns)
    with _SEEN_LOCK:
        if key in _SEEN:
            return _SEEN[key]
    try:
        found = sniff(pdf_text(p))
    except (OSError, subprocess.CalledProcessError):
        found = []
    with _SEEN_LOCK:
        _SEEN[key] = found
    return found


@dataclass
class Place:
    """One statement folder as routing sees it."""
    path: str                             # relative to the data folder
    parser: str
    wallet: str = ""
    account_no: str = ""                  # from the config
    learnt: list = field(default_factory=list)   # Found of the statements already in it

    def layouts(self):
        """The layouts it holds: what its statements say, else what its name
        says, else None (could be any of its parser's)."""
        its = registry.of_parser(self.parser)
        if len(its) == 1:
            return {its[0].name}
        mine = {f.layout for f in self.learnt if f.parser == self.parser}
        if mine:
            return mine
        # the folder Setup makes for each of the parser's layouts
        slug = self.path.rstrip("/").rsplit("/", 1)[-1]
        named = {lay.name for lay in its if lay.folder == slug}
        return named or None

    def numbers(self, found):
        """The numbers this folder is known by, for a statement like `found`:
        the ones its statements of that layout print, and the config's number.
        The config names the account, which a card statement never prints (it
        prints the account the cards draw on), and of a Wise account only the
        SGD balance prints it (the others print their own local details)."""
        out = set()
        for f in self.learnt:
            if f.layout == found.layout and (not per_currency(found.layout) or f.currency == self.wallet):
                out |= f.numbers
        config_counts = registry.get(found.layout).prints_config_number(found.currency)
        if config_counts and norm(self.account_no):
            out.add(norm(self.account_no))
        return out


def places(cfg, read=None):
    """Every statement folder; the ones of parser `read` with what the
    statements already in them say."""
    from . import monthly
    config_no = {f.path: f.account_no for f in cfg.folders}
    out = []
    for f in monthly.folders(cfg):
        p = Place(f["path"], f["parser"], f.get("wallet") or "", config_no.get(f["path"], ""))
        if p.parser == read:
            root = Path(cfg.root) / p.path
            for pdf in sorted(root.rglob("*.pdf")) if root.is_dir() else ():
                if not pdf.name.startswith("."):
                    p.learnt.extend(seen(pdf))
        out.append(p)
    return out


@dataclass
class Route:
    """Where one statement goes: `folder` when exactly one fits, else None and
    `candidates`, the folders it could be (possibly none), and `why` in words."""
    found: Found | None
    folder: str | None
    candidates: list
    why: str
    known: bool = True                    # False: no layout matched, a statement Kopifund does not read (G21)


def route(found_list, folders):
    """Pick the one folder for a statement, or say why not. `found_list` is
    sniff()'s answer for it and `folders` the Place of every folder."""
    if not found_list:
        return Route(None, None, [], "Kopifund could not tell which bank this statement is from", known=False)
    if len(found_list) > 1:
        return Route(None, None, [], "This reads as more than one kind of statement")
    found = found_list[0]
    fits = [p for p in folders if p.parser == found.parser
            and (not per_currency(found.layout) or p.wallet == found.currency)
            and (p.layouts() is None or found.layout in p.layouts())]
    label = found.label()
    if not fits:
        return Route(found, None, [], f"This is {label}, and no account set up here takes it")
    if found.numbers:
        named = [p for p in fits if p.numbers(found) & found.numbers]
        if len(named) == 1:
            return Route(found, named[0].path, [], "")
        if len(named) > 1:
            return Route(found, None, [p.path for p in named],
                         f"This is {label}, and more than one of your accounts prints its number")
        # no folder knows its number. The one folder that knows no number of
        # this layout yet, when its layout is sure, is where a first statement
        # goes; a second such folder, or one whose layout is not sure, could be
        # it as well, so then the owner says
        fresh = [p for p in fits if not p.numbers(found)]
        if len(fresh) == 1 and fresh[0].layouts() is not None:
            return Route(found, fresh[0].path, [], "")
        return Route(found, None, [p.path for p in fits],
                     f"This is {label}, and its account number is not one Kopifund knows")
    sure = [p for p in fits if p.layouts() is not None]
    if len(sure) == 1 and len(fits) == 1:
        return Route(found, sure[0].path, [], "")
    return Route(found, None, [p.path for p in fits],
                 f"This is {label}, and it prints no account number to tell your accounts apart")


def place(cfg, text):
    """route() for one statement's text against the config's folders."""
    found = sniff(text)
    return route(found, places(cfg, read=found[0].parser if len(found) == 1 else None))
