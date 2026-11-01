"""Where Kopifund keeps a user's data.

Everything personal lives in one folder outside the repo, `~/.kopifund` by
default or wherever KOPIFUND_DATA points (SPEC.md, "The data folder"). The repo
holds code and the rules that name no one; nothing here ever writes into it.

These are functions rather than constants so a test, or a second run with a
different KOPIFUND_DATA, sees the current value.
"""
import os
import re
from pathlib import Path

SERVER = Path(__file__).resolve().parents[1]   # server/: the code
REPO = SERVER.parent                            # the repository root
REPO_RULES = REPO / "data" / "rules"            # the shipped rules (the personal ones are in the data folder)


def data_root():
    """The data folder: KOPIFUND_DATA when set, else ~/.kopifund."""
    return Path(os.environ.get("KOPIFUND_DATA") or Path.home() / ".kopifund").expanduser()


def config_path():
    return data_root() / "config.toml"


def statements_dir():
    return data_root() / "statements"


def personal_rules_dir():
    return data_root() / "rules"


def rules_history_dir():
    """Where every write to the personal rules keeps the files' previous
    content, so the Rules page can undo it."""
    return data_root() / "rules-history"


def places_path():
    """The data folder's own towns, read after the shipped city table
    (server/finance/parsers/places.py)."""
    return data_root() / "places.csv"


def cache_dir():
    """The derived cache. KOPIFUND_CACHE moves it, which the test suite does so
    that no test ever writes into a real data folder's cache."""
    override = os.environ.get("KOPIFUND_CACHE")
    return Path(override).expanduser() if override else data_root() / "cache"


def text_dir():
    """Where the pdftotext dump of each statement goes: under cache/, so the
    statement tree holds only the original PDFs."""
    return cache_dir() / "text"


def text_dump_path(pdf):
    """The .txt dump for `pdf`. A PDF inside the data folder's statements/ gets
    a mirrored path under cache/text/; anything else (a test's temp file, a PDF
    passed by hand) keeps the old place beside the PDF."""
    pdf = Path(pdf).resolve()
    stmts = statements_dir().resolve()
    if pdf.is_relative_to(stmts):
        out = text_dir() / pdf.relative_to(stmts).with_suffix(".txt")
        out.parent.mkdir(parents=True, exist_ok=True)
        return out
    return pdf.with_suffix(".txt")


# ---------------------------------------------------------------- statement files

_EXPORT = re.compile(r"^export-(\d{4}-\d{2}-\d{2})\b.*\.pdf$", re.I)
_RANGE = re.compile(r"^(\d{4}-\d{2})_(\d{4}-\d{2})\.pdf$", re.I)


def _account_dir(pdf):
    """The account folder a statement belongs to: the parent, or the
    grandparent when the parent is a year folder."""
    parent = pdf.parent
    return parent.parent if parent.name.isdigit() else parent


def current_statements(pdfs):
    """The PDFs a run should read, oldest name first.

    Nothing in the data folder is overwritten (SPEC.md, "The data folder"), so a
    folder can hold an export and the re-export that replaced it. Two rules pick
    the one that counts, by file name alone:

    * `export-YYYY-MM-DD*.pdf`: an all-time export (Revolut, Wise). Only the
      newest per account folder is current.
    * `YYYY-MM_YYYY-MM.pdf`: a range. Of two ranges in one account folder that
      start in the same month (a year-to-date export redone later), only the
      one that ends last is current.

    Dot-files are never statements. Everything else passes through.
    """
    pdfs = [Path(p) for p in pdfs if not Path(p).name.startswith(".")]
    newest_export, longest_range = {}, {}
    for p in pdfs:
        m = _EXPORT.match(p.name)
        if m:
            k = _account_dir(p)
            if k not in newest_export or m.group(1) > newest_export[k][0]:
                newest_export[k] = (m.group(1), p)
            continue
        m = _RANGE.match(p.name)
        if m:
            k = (_account_dir(p), m.group(1))
            if k not in longest_range or m.group(2) > longest_range[k][0]:
                longest_range[k] = (m.group(2), p)
    keep = {p for _, p in newest_export.values()} | {p for _, p in longest_range.values()}
    return sorted(p for p in pdfs
                  if p in keep or not (_EXPORT.match(p.name) or _RANGE.match(p.name)))


def superseded_statements(pdfs):
    """The PDFs current_statements leaves out, other than dot-files."""
    pdfs = [Path(p) for p in pdfs if not Path(p).name.startswith(".")]
    keep = set(current_statements(pdfs))
    return sorted(p for p in pdfs if p not in keep)
