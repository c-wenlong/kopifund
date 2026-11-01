"""Copies of the record taken before Kopifund changes it in bulk.

The data folder's backups/ (SPEC.md, "The data folder") holds dated copies.
Two things write one here, each before it changes anything:

* opening a record older than the code (server/finance/db.py, `connect`) copies the
  whole record before the schema upgrade;
* deleting a category on the Categories page (server/finance/categories.py) copies it
  before the category's transactions and budgets move.

A record is copied with SQLite's own backup, so a copy taken while the app has
the record open is still whole. Copies are private: 600, in a folder of 700.
The personal rules files are not copied here: every write to them goes
through rules_app, which keeps their old content in rules-history/ for undo.
"""
import os
import sqlite3
from datetime import datetime
from pathlib import Path


def stamp():
    return datetime.now().strftime("%Y-%m-%d-%H%M%S")


def _folder(path):
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(0o700)
    return path


def _claim(path):
    """Create `path` (or the next free -2, -3, ... name) exclusively, empty,
    mode 600, and return it: two copies taken at once never get one name, and
    no copy ever replaces another."""
    path = Path(path)
    n = 1
    while True:
        out = path if n == 1 else path.with_name(f"{path.stem}-{n}{path.suffix}")
        try:
            os.close(os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
            return out
        except FileExistsError:
            n += 1


def backup_record(conn, path, reason):
    """Copy the open record `conn` (the file at `path`) into backups/ beside
    it, as kopifund-<reason>-<stamp>.db. Returns the copy's path. Call it
    outside a write transaction on `conn`: SQLite's backup waits for the
    write lock that connection holds."""
    folder = _folder(Path(path).parent / "backups")
    dest = _claim(folder / f"kopifund-{reason}-{stamp()}.db")
    out = sqlite3.connect(dest)
    try:
        conn.backup(out)
    finally:
        out.close()
    dest.chmod(0o600)
    return dest
