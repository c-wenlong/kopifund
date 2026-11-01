"""The stat results cache, read-only: where the app reads the stat results from.

`analyse` writes `latest.json`, a snapshot and one dated result file per stat
into the data folder's `cache/`. This module only reads them back, in the order
the cards read best. It never writes a file and never talks to Notion. The app
(`serve.py`, G5) is the one place they are shown; there is no second viewer.
G19: this was server/analytics/server.py; it lives in finance, beside the endpoint
answers that read it, since finance never imports analytics.

A path that escapes the cache directory is a `NotFound` (server/finance/errors.py,
the same one the app answers 404 with), never an error that would confirm
what is outside.
"""
import copy
import json
import re
import threading
from pathlib import Path
from urllib.parse import unquote

from .errors import NotFound
from .money import in_cents

# <stat-id>-<YYYY-MM-DD>.json, the only file name shape the results directory has.
RESULT_FILE = re.compile(r"^(?P<id>[a-z0-9]+(?:-[a-z0-9]+)*)-(?P<date>\d{4}-\d{2}-\d{2})\.json$")
STAT_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

# The order the cards read best in: money in and out first, then where it goes,
# then who it goes to, then the outliers, then the forward-looking ones, and
# the caveats about the data itself last. Anything not listed sorts after.
DISPLAY_ORDER = [
    "monthly-cashflow", "needs-wants-savings", "category-breakdown",
    "recurring-detector", "eating-out", "merchant-concentration", "one-offs",
    "income-stability", "savings-runway", "portfolio", "cpf", "travel",
    "benchmarks-sg",
    "forward-view", "data-quality",
]


def safe_path(base, relative):
    """`base / relative`, or None if that would leave `base`.

    Rejects absolute paths, `..` in any position, NUL bytes and symlinks that
    point outside, by resolving both sides and asking whether the result is
    still underneath. Returns None rather than raising so the caller can answer
    404 and reveal nothing about what is out there.
    """
    relative = unquote(relative or "")
    if not relative or relative.startswith("/") or "\x00" in relative:
        return None
    parts = [p for p in relative.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        return None
    base = base.resolve()
    target = base.joinpath(*parts)
    try:
        target = target.resolve()
    except OSError:
        return None
    return target if target == base or base in target.parents else None


class Cache:
    """The cache directory, read-only. Nothing here writes or creates."""

    def __init__(self, root):
        self.root = Path(root).resolve()

    @property
    def results_dir(self):
        return self.root / "results"

    def read(self, path):
        """(parsed JSON, mtime) for a file inside the cache."""
        if not path or not path.is_file():
            raise NotFound(str(path))
        try:
            with path.open("rb") as fh:
                return json.load(fh), path.stat().st_mtime
        except (OSError, ValueError) as e:
            raise NotFound(f"{path}: {e}") from e

    def latest(self):
        return self.read(self.root / "latest.json")

    def files_for(self, stat_id):
        """Every dated file for one stat, newest date first."""
        if not STAT_ID.match(stat_id or ""):
            return []
        out = []
        for path in self.results_dir.glob(f"{stat_id}-*.json"):
            m = RESULT_FILE.match(path.name)
            if m and m.group("id") == stat_id:
                out.append((m.group("date"), path))
        return [p for _, p in sorted(out, key=lambda dp: dp[0], reverse=True)]

    def current_file(self, stat_id):
        """The file latest.json points at, falling back to the newest dated one."""
        try:
            latest, _ = self.latest()
        except NotFound:
            latest = {}
        named = (latest.get("results") or {}).get(stat_id)
        if named:
            path = safe_path(self.results_dir, named)
            if path and path.is_file():
                return path
        files = self.files_for(stat_id)
        if not files:
            raise NotFound(f"no result files for {stat_id!r}")
        return files[0]

    def results_all(self):
        """Every stat's current result body, keyed by stat id, the shape
        `metrics_from` reads, plus the newest mtime among the files used.

        G20: a result from before its stat wrote cents (money.in_cents) is
        left out, so every page shows it as no result until the stats run
        again, and nothing reads its dollars as cents."""
        latest, _ = self.latest()
        out, newest = {}, 0.0
        for stat_id in (latest.get("results") or {}):
            if not STAT_ID.match(stat_id):
                continue
            try:
                path = self.current_file(stat_id)
                body, mtime = self.read(path)
            except NotFound:
                continue
            if not in_cents(body):
                continue
            out[stat_id] = body
            newest = max(newest, mtime)
        return out, newest


def _cache():
    from .paths import cache_dir
    return Cache(cache_dir())


# G13: Home, Recurring, Net worth, the projection and every stat card read the
# results, and masking them costs most of each answer, so they are worked out
# once per data version and each caller gets its own copy to change.
_RESULTS_MEMO: dict = {}
_RESULTS_LOCK = threading.Lock()


def results(req=None):
    """Every stat's current result, or {} before the first analysis, with any
    account number the record knows masked to its last four digits. Masking
    reads the request's connection when it has to work them out, so that
    costs the request no second one."""
    from . import api, page_cache
    version = page_cache.data_version()
    with _RESULTS_LOCK:                  # one works them out, the others wait for it
        results = _RESULTS_MEMO.get(version)
        if results is None:
            try:
                results, _ = _cache().results_all()
            except NotFound:
                results = {}
            results = api.masked(results, conn=req.maybe_record() if req else None) if results else {}
            _RESULTS_MEMO.clear()
            _RESULTS_MEMO[version] = results
        return copy.deepcopy(results)
