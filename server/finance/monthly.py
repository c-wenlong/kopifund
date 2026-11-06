"""The monthly run, as the app drives it (G6).

Three steps, each its own endpoint, so the browser can show what each found
before the next one runs:

  upload   a statement PDF into its account folder, renamed to its canonical
           name from what the statement itself prints. The folder is the one
           the statement's own text names (server/finance/sniff.py), or the one the
           owner picked when the text cannot tell;
  check    parse every statement, apply the rules and run the checks that need
           no Notion: what would be added, changed and archived, and what needs
           a rule (it writes no transactions; the upload history notes it ran);
  load     write that into kopifund.db and rerun the stats.

Mirroring to Notion stays `finance.py sync`, which the app can start but does
not reimplement. Nothing here deletes or overwrites a statement.
"""
import fcntl
import hashlib
from collections import Counter
import os
import subprocess
import tempfile
import threading
from contextlib import contextmanager
from datetime import date
from pathlib import Path

from . import account_names, db, local, sync
from .parsers import layouts as registry
from .paths import cache_dir
from .pdftext import pdf_text as _text   # one name, so a test can stand in for pdftotext

INBOX = "inbox"
MAX_PDF_BYTES = 20 * 1024 * 1024


class UploadRefused(Exception):
    """The file is not something this run should keep."""


# ---------------------------------------------------------------- folders

def folders(cfg):
    """The account folders a statement can go into, with their parser and, for
    a currency wallet (YouTrip, Wise), its currency. `title` and `bank` are
    how the app names and marks the account kept in it (G21: from the layout
    registry, through server/finance/account_names.py), so no page keeps a list of
    banks of its own."""
    from . import account_names as names
    from . import snapshot as snap

    def entry(path, parser, wallet):
        f = {"path": path, "name": Path(path).name, "parser": parser, "wallet": wallet}
        return {**f, "title": names.folder_name(f), "bank": names.folder_bank(f)}
    out = [entry(f.path, f.parser, f.wallet) for f in cfg.folders]
    # a layout with no rows (Tiger, CPF) keeps its folder in [analytics]
    for lay in registry.every():
        if lay.csv is None:
            folder = snap.analytics_folder(cfg, lay.config_key, f"statements/{lay.folder}")
            if folder:
                out.append(entry(folder, lay.parser, ""))
    return out


# ---------------------------------------------------------------- upload

def canonical_name(parser, pdf, wallet="", today=None):
    """(year folder, file name) for a statement, read from its own text by
    the parser's layout (server/finance/parsers/layouts.py): its month or months, an
    export's download day, a brokerage export's period, a CPF statement's year."""
    today = today or date.today()
    its = registry.of_parser(parser)
    if not its:
        raise UploadRefused("This PDF is not a statement this account can read")
    try:
        # _text is looked up here, at the call, so a test can stand in for pdftotext
        return its[0].name_file(pdf, _text, wallet, today)
    except registry.NotThisLayout as e:
        raise UploadRefused(str(e)) from None


UNREADABLE = "This file could not be read as a PDF; it may be damaged, so download it again"


def _export(parser):
    """What a PDF must read as for a parser that names its file by the day it
    is downloaded and so reads nothing of it (Revolut, Wise); "" otherwise."""
    return next((lay.export for lay in registry.of_parser(parser) if lay.export), "")


def _plain_pdf(data):
    if not data.startswith(b"%PDF"):
        raise UploadRefused("That file is not a PDF")
    if len(data) > MAX_PDF_BYTES:
        raise UploadRefused("That PDF is larger than any statement should be")


def _stage(data):
    """The upload as a file of its own in the cache's inbox, for pdftotext and
    the parsers to read; the caller removes it."""
    inbox = cache_dir() / INBOX
    inbox.mkdir(parents=True, exist_ok=True)
    fd, staged_name = tempfile.mkstemp(prefix=f"{hashlib.sha256(data).hexdigest()[:12]}-",
                                       suffix=".pdf", dir=inbox)
    with os.fdopen(fd, "wb") as fh:          # one staged file per request
        fh.write(data)
    return Path(staged_name)


def _readable_text(pdf):
    try:
        return _text(pdf)
    except (OSError, subprocess.CalledProcessError):
        return None


def find_folder(cfg, data):
    """G13: the account folder an uploaded PDF belongs to, read from its own
    text (server/finance/sniff.py), keeping nothing. Returns {folder, kind} when
    exactly one folder fits, else {folder: None, kind, why, candidates}: the
    owner chooses, and the file comes again with that folder. G21: a PDF no
    layout matches comes back with `supported` False, and there is nothing to
    choose. Raises UploadRefused for a file that is not a readable PDF."""
    from . import sniff
    _plain_pdf(data)
    staged = _stage(data)
    try:
        text = _readable_text(staged)
    finally:
        staged.unlink(missing_ok=True)
    if text is None:
        raise UploadRefused(UNREADABLE)
    r = sniff.place(cfg, text)
    kind = r.found.label() if r.found else ""
    if r.folder:
        return {"folder": r.folder, "kind": kind}
    return {"folder": None, "kind": kind, "currency": r.found.currency if r.found else "",
            "why": r.why, "candidates": r.candidates, "supported": r.known}


def _check_folder(cfg, folder, text):
    """G13: refuse a statement whose own text says it is for another kind of
    account than the folder chosen for it: another parser, another wallet
    currency, or a DBS layout the folder does not hold. Revolut and Wise name
    a file by the day and read nothing else, so a PDF that does not read as
    theirs is refused. Any other PDF the layout tests cannot place is left to
    its parser, which refuses what it cannot read."""
    from . import sniff
    found = sniff.sniff(text) if text is not None else []
    if not found:
        export = _export(folder["parser"])
        if export:
            raise UploadRefused(UNREADABLE if text is None else f"This PDF is not {export}")
        return
    # a PDF that reads as several statements is the owner's to place, but at
    # least one of them must be one this folder takes
    kinds = " and ".join(f.label() for f in found)
    fits = [f for f in found if f.parser == folder["parser"]]
    if not fits:
        raise UploadRefused(f"This is {kinds}, which this account does not take")
    wallet = folder.get("wallet") or ""
    in_wallet = [f for f in fits if not sniff.per_currency(f.layout) or f.currency == wallet]
    if not in_wallet:
        raise UploadRefused(f"This is {kinds}, and this folder is for {wallet or 'more than one currency'}")
    place = next((p for p in sniff.places(cfg, read=folder["parser"]) if p.path == folder["path"]), None)
    if place is not None and place.layouts() is not None and not any(f.layout in place.layouts() for f in in_wallet):
        raise UploadRefused(f"This is {kinds}, which this account does not take")


def upload(cfg, folder_path, data, today=None):
    """Keep one uploaded PDF in `folder_path`. Returns {status, path, message};
    status is 'added' or 'already-there'. Raises UploadRefused for anything
    else, a statement that says it is for another kind of account included."""
    known = {f["path"]: f for f in folders(cfg)}
    if folder_path not in known:
        raise UploadRefused(f"{folder_path!r} is not one of your statement folders")
    _plain_pdf(data)
    folder = known[folder_path]
    sha = hashlib.sha256(data).hexdigest()
    staged = _stage(data)
    try:
        _check_folder(cfg, folder, _readable_text(staged))
        wallet = next((f.wallet for f in cfg.folders if f.path == folder_path), "")
        try:
            year, name = canonical_name(folder["parser"], staged, wallet=wallet, today=today)
        except UploadRefused:
            raise
        except subprocess.CalledProcessError as e:
            # G13: pdftotext's own message is a command line with a path in the
            # data folder; the Upload page shows this to the owner, so say it plainly
            raise UploadRefused(UNREADABLE) from e
        except Exception as e:  # a parser that cannot read it says so in its own words
            raise UploadRefused(f"The {folder['parser']} parser could not read it: {e}") from e
        target = Path(cfg.root) / folder_path / year / name
        rel = str(target.relative_to(cfg.root))
        if target.exists():
            if hashlib.sha256(target.read_bytes()).hexdigest() == sha:
                return {"status": "already-there", "path": rel,
                        "message": "You already have this statement"}
            raise UploadRefused(f"{rel} already holds a different file; nothing was replaced")
        for existing in (Path(cfg.root) / folder_path).rglob("*.pdf"):
            if hashlib.sha256(existing.read_bytes()).hexdigest() == sha:
                return {"status": "already-there",
                        "path": str(existing.relative_to(cfg.root)),
                        "message": "You already have this statement under another name"}
        target.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(target.parent, 0o700)
        try:
            # created exclusively: of two uploads racing for one name, the
            # second finds the first's file and never overwrites it
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            if hashlib.sha256(target.read_bytes()).hexdigest() == sha:
                return {"status": "already-there", "path": rel,
                        "message": "You already have this statement"}
            raise UploadRefused(f"{rel} already holds a different file; nothing was replaced") from None
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        return {"status": "added", "path": rel, "message": f"Saved as {rel}"}
    finally:
        staged.unlink(missing_ok=True)


# ---------------------------------------------------------------- check

def check(cfg, conn):
    """Parse and check everything; compare with the record. Writes nothing to
    the record (the parsers do refresh the text dumps in cache/)."""
    parsed = sync.parse(cfg, echo=False)
    sync.categorise(parsed, sync.default_rules(cfg))
    problems = list(parsed.problems) + sync.check_environment(cfg) + \
        sync.check_statements(cfg, parsed) + \
        sync.check_statement_files(cfg, dict(conn.execute("SELECT path, sha256 FROM statement_file"))) + \
        sync.check_blocks(parsed) + sync.check_pinned_rows(conn, parsed)
    problems = sync.one_problem_per_copy(problems)
    have = {r["txn_key"]: r for r in conn.execute(
        "SELECT txn_key, amount_minor, category_id, payee_id, archived_at FROM txn")}
    # a row the load will give a new key (db.rekeys) stays: neither added nor archived
    moved = db.rekeys(conn, parsed.rows)
    moved_new, moved_old = {n for _, _, n in moved}, {o for _, o, _ in moved}
    new = [r for r in parsed.rows if r["txn_key"] not in have and r["txn_key"] not in moved_new]
    shown = account_names.record_names(conn, cfg.folders)
    keys = parsed.keys
    gone = [k for k, r in have.items() if r["archived_at"] is None and k not in keys and k not in moved_old]
    cats = db.category_ids(conn)          # a renamed category's old name included
    changed = [r for r in parsed.rows if r["txn_key"] in have
               and cats.get(r.get("category")) != have[r["txn_key"]]["category_id"]]
    # G13: a problem names a block by its period name, which starts with the
    # account number, and a row's text can print one (a transfer's memo); the
    # app never shows one in full. The statements just parsed are the owner's
    # own, so a first check, before the record or the config knows their
    # numbers, is masked too.
    own = account_names.own_numbers(conn, cfg) | statement_numbers(parsed)
    return parsed, {
        "problems": [account_names.scrub_tree({"code": p.code, "subject": p.subject, "fix": p.fix}, own)
                     for p in problems],
        "statements": len({r["source"] for r in parsed.rows} | {b["source"] for b in parsed.blocks}),
        "rows": len(parsed.rows),
        "to_add": account_names.scrub_tree([_row(r, shown, cfg.folders) for r in sorted(
            new, key=lambda r: r["date"], reverse=True)[:200]], own),
        "to_add_count": len(new),
        "recategorised_count": len(changed),
        "to_archive_count": len(gone),
        "rekeyed_count": len(moved),
        "needs_rule": needs_rule(parsed.rows),
        # what each statement file gave, for the upload history
        "rows_by_statement": account_names.scrub_tree(
            dict(sorted(Counter(str(r["source"]) for r in parsed.rows).items())), own),
    }


def statement_numbers(parsed):
    """The account numbers the statements just parsed print as their own: the
    owner's, though the record may not know them yet."""
    return {str(x["account_no"]) for x in (*parsed.rows, *parsed.blocks) if x.get("account_no")}


NOT_BLOCKING = ("pdftotext-missing",)


def blocking(report):
    """The problems that stop a load."""
    return [p for p in report["problems"] if p["code"] not in NOT_BLOCKING]


def _row(r, shown=None, folders=()):
    # G13: an account by the name it is shown by, never its full number
    no = r.get("account_no") or ""
    account = (shown or {}).get(no) or account_names.label(r.get("account"), no, None, folders)
    return {"key": r["txn_key"], "date": r["date"], "amount": db.to_minor(r["amount"]),
            "currency": r["currency"], "account": account,
            "text": r.get("merchant") or r.get("counterparty") or r.get("memo") or r.get("bank_txn_type"),
            "payee": r.get("payee") or "", "category": r.get("category") or ""}


def needs_rule(rows, limit=50):
    """Unmatched External rows grouped by the text they print, biggest money
    first: the queue for the next rule (sync.needs_rule_lines, as data)."""
    groups = {}
    for r in rows:
        if r.get("rule") or r.get("flow_class") != "External":
            continue
        field = next((f for f in ("merchant", "counterparty", "memo") if r.get(f)), "bank_txn_type")
        stem = (r.get(field) or "")[:44]
        g = groups.setdefault(stem, {"text": stem, "field": field, "rows": 0, "amount": 0,
                                     "first": r["date"], "last": r["date"], "keys": []})
        g["rows"] += 1
        g["amount"] += abs(db.to_minor(r["amount"]) or 0)
        g["first"], g["last"] = min(g["first"], r["date"]), max(g["last"], r["date"])
        if len(g["keys"]) < 5:
            g["keys"].append(r["txn_key"])
    return sorted(groups.values(), key=lambda g: -g["amount"])[:limit]


# ---------------------------------------------------------------- load

def load(cfg, conn):
    """Check, refuse on a problem, then load and rerun the stats."""
    parsed, report = check(cfg, conn)
    if blocking(report):
        return {"loaded": False, "report": report}
    counts = local.load(cfg, conn, parsed)
    analysed = analyse(cfg)
    return {"loaded": True, "report": report, "counts": counts, "stats": analysed}


# One rebuild at a time: a load's and a category edit's
# (server/finance/stats_rebuild.py) both read the record and write the same day's results, so
# two at once could let the one that read earlier write last. In turn, the
# later one always reads the record as the earlier one left it.
# G23: the desktop app and `make serve` can run on one data folder, so the
# thread lock is backed by a file lock in the cache that a second server waits
# on too.
_ANALYSE_LOCK = threading.Lock()
REBUILD_LOCK = "rebuild.lock"


@contextmanager
def _rebuild_lock():
    with _ANALYSE_LOCK:
        folder = Path(cache_dir())
        folder.mkdir(parents=True, exist_ok=True)
        with open(folder / REBUILD_LOCK, "a") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)


# G19: the stats belong to analytics, which imports finance and never the
# reverse, so the app that holds both hands the rebuild in once, at start-up
# (serve.py: monthly.rebuild_stats_with(analytics.run.rebuild)).
_REBUILD = [None]


def rebuild_stats_with(rebuild):
    """Use `rebuild(cfg)` for every stats rebuild: analytics.run.rebuild, which
    reruns every stat over the record into the cache and returns the counts."""
    _REBUILD[0] = rebuild


def analyse(cfg):
    """Rerun every stat over the record into the cache."""
    rebuild = _REBUILD[0]
    if rebuild is None:
        raise RuntimeError("no stats rebuild was handed in: call "
                           "monthly.rebuild_stats_with(analytics.run.rebuild) first")
    with _rebuild_lock():
        return rebuild(cfg)


def mirror(server_dir):
    """`finance.py sync` as a subprocess: the Notion mirror, unchanged.
    Returns (exit code, output)."""
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "report.txt"
        r = subprocess.run(["python3", str(Path(server_dir) / "finance.py"), "sync",
                            "--report", str(report)], capture_output=True, text=True)
        return r.returncode, (report.read_text() if report.exists() else r.stdout + r.stderr)
