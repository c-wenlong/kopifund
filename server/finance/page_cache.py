"""G13: the cached GETs' data version and the answers already worked out (G19:
moved out of serve.py, which keeps the list of cached routes, CACHED).

`data_version` sums up everything the cached GETs read in one short string,
which serve.py sends as the ETag: a request carrying it in If-None-Match gets
a 304, and `cached_answer` sends an answer already worked out for the same
path, query and version again without working it out. `wrote` is called once
a write has finished, so no answer from before it is current.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from collections import OrderedDict
from datetime import date
from pathlib import Path
from typing import Callable

from .errors import NotFound

# A new process starts a new version, so an answer from code that has since
# changed is never taken as current. _WRITES counts this process's writes: a
# write through the app moves the version once it is done, before it answers,
# whatever its files' times say.
_BOOT = f"{os.getpid():x}.{time.time_ns():x}"
_WRITES = {"n": 0}
_VERSION_LOCK = threading.Lock()


def wrote() -> None:
    """A write has finished: no answer worked out before it is current."""
    with _VERSION_LOCK:
        _WRITES["n"] += 1
    with _MEMO_LOCK:                     # they can no longer be asked for; this only frees them
        _MEMO.clear()


def _stat(path) -> str:
    try:
        s = os.stat(path)
    except OSError:
        return "-"
    return f"{s.st_mtime_ns}:{s.st_size}:{s.st_ino}"


def _stat_wal(path) -> str:
    """The record's write-ahead log, where a commit lands first. SQLite makes
    an empty one when a connection opens and removes it when the last one
    closes, so missing and empty are the same; a checkpoint into the record
    moves the record's own time."""
    try:
        s = os.stat(path)
    except OSError:
        return "0"
    return f"{s.st_mtime_ns}:{s.st_size}" if s.st_size else "0"


def _stat_dir(path) -> list[str]:
    """Each entry of one folder with its time and size: a file replaced in
    place, added or removed shows."""
    try:
        with os.scandir(path) as it:
            return sorted(f"{e.name}={_stat(e.path)}" for e in it)
    except OSError:
        return ["-"]


def data_version(statements: bool = False) -> str:
    """What the cached GETs read, in one short string that changes whenever
    any of it does: this process and its writes, today's date (Home's month,
    what is due, the projection's start), the record and its write-ahead log
    (a commit lands there first), the config, about you, the places, the stat
    results and latest.json, the personal and shipped rules, and the rules'
    undo history. A `make load` or `make analyse` run outside the app moves
    it, since it rewrites those files. `statements` adds every statement
    folder's time and the names in it, so a PDF added or removed moves it:
    Your profile counts them. A folder's time alone is not enough, since
    ext4 moves it on a clock tick of a few milliseconds and a PDF added within
    one leaves it as it was. A stat of each, no file is read; well under a
    millisecond."""
    from . import db, profile
    from .paths import (REPO_RULES, cache_dir, config_path, personal_rules_dir, places_path,
                        rules_history_dir, statements_dir)
    record = db.db_path()
    cache = cache_dir()
    parts = [_BOOT, str(_WRITES["n"]), date.today().isoformat(),
             _stat(record), _stat_wal(f"{record}-wal"), _stat(config_path()), _stat(profile.path()),
             _stat(places_path()), _stat(cache / "latest.json"), _stat(cache / "categories.json"),
             *_stat_dir(cache / "results"), *_stat_dir(personal_rules_dir()), *_stat_dir(rules_history_dir()),
             *_stat_dir(REPO_RULES)]
    if statements:
        # a folder or file named in bytes the filesystem cannot decode walks
        # in as surrogates, which the encode below would raise on: the reprs
        # ('...' and ['...']) escape them
        parts += [f"{d!r}={_stat(d)}:{sorted(names)}"
                  for root in _statement_roots(statements_dir()) for d, _, names in os.walk(root)]
    return hashlib.sha1("\n".join(parts).encode()).hexdigest()[:20]


def _statement_roots(default) -> list:
    """Every folder Your profile counts PDFs in: the config's statement
    folders (monthly.folders, Tiger and CPF included), wherever they are, and
    the default statements folder. A folder walked twice changes nothing."""
    from . import api
    roots = [default]
    try:
        from . import monthly
        cfg = api.config()
        roots += [Path(cfg.root) / f["path"] for f in monthly.folders(cfg)]
    except NotFound:
        pass
    return roots


def etag_matches(header: str | None, etag: str) -> bool:
    if not header:
        return False
    return any(t.strip() in ("*", etag, f"W/{etag}") for t in header.split(","))


# Answers already worked out, as the bytes sent, by (path, query, version).
# The version is worked out before the answer, so an answer read while a write
# lands is filed under the version before it, which no later request asks for.
_MEMO: OrderedDict[tuple, bytes] = OrderedDict()
_MEMO_SIZE = 64
_MEMO_LOCK = threading.Lock()


def _memo_put(key: tuple, body: bytes) -> None:
    with _MEMO_LOCK:
        _MEMO[key] = body
        _MEMO.move_to_end(key)
        while len(_MEMO) > _MEMO_SIZE:
            _MEMO.popitem(last=False)


class _Flight:
    """One answer being worked out; requests for the same key wait for it."""

    def __init__(self) -> None:
        self.done = threading.Event()
        self.body: bytes | None = None
        self.error: BaseException | None = None


_FLIGHTS: dict[tuple, _Flight] = {}


def cached_answer(path: str, query: dict, version: str, answer: Callable[[str, dict], object]) -> bytes:
    """The JSON for one cached GET as the bytes sent, worked out once per
    version by `answer` (serve.answer). `version` is data_version taken
    before this is called. Two
    requests for the same key at once (two tabs opening a page) share one
    working out; answers for other keys go ahead meanwhile, since no lock is
    held while one is worked out."""
    key = (path, tuple(sorted(query.items())), version)
    with _MEMO_LOCK:
        body = _MEMO.get(key)
        if body is not None:
            _MEMO.move_to_end(key)
            return body
        flight = _FLIGHTS.get(key)
        mine = flight is None
        if mine:
            flight = _FLIGHTS[key] = _Flight()
    if not mine:
        flight.done.wait()
        if flight.error is not None:
            raise flight.error           # the same refusal (404, 400) for each
        return flight.body
    try:
        flight.body = json.dumps(answer(path, query)).encode()
        _memo_put(key, flight.body)
        return flight.body
    except BaseException as e:
        flight.error = e
        raise
    finally:
        with _MEMO_LOCK:
            _FLIGHTS.pop(key, None)
        flight.done.set()

