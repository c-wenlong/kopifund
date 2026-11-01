"""G13: the stats rebuild a write starts in the background (G19: moved out of
serve.py). A write that changes what the stats count or call things reruns
them here, and answers at once; GET /api/stats/rebuild says whether a rerun is
still going, and the app reloads what it shows once it is not.
"""

import sys
import threading
import time

from . import page_cache

# A category edit changes what the stats count (a move into Transfers leaves
# spending) or what they call things (a rename, a bucket), and the pages read
# the saved stat results. So every category edit rebuilds them, in the
# background since a rebuild takes seconds: the edit answers at once and the
# stat pages show the new figures once it is done. Edits that land while one
# runs are covered by one more run after it.
_STATS = {"running": False, "again": False}
_STATS_LOCK = threading.Lock()
# G13: a rebuild waits this long before it starts, so the page's next asks
# after a save (the rule preview behind "Apply to N similar rows", the rows it
# redraws) are answered first, and saves in quick succession share one run
STATS_DELAY = 1.5


def refresh_stats(cfg) -> None:
    with _STATS_LOCK:
        if _STATS["running"]:
            _STATS["again"] = True
            return
        _STATS["running"] = True

    def work() -> None:
        from finance import monthly
        time.sleep(STATS_DELAY)
        with _STATS_LOCK:
            _STATS["again"] = False      # the run about to start reads every write so far
        while True:
            try:
                monthly.analyse(cfg)
            except Exception as e:  # noqa: BLE001  the old results stay; the next load rebuilds them
                print(f"rebuilding the stats after a category edit failed: {e}", file=sys.stderr)
            # G13: before it says it is done, so the reload the app then asks
            # for never gets an answer worked out from the old results
            page_cache.wrote()
            with _STATS_LOCK:
                if not _STATS["again"]:
                    _STATS["running"] = False
                    return
                _STATS["again"] = False

    threading.Thread(target=work, name="kopifund-stats", daemon=True).start()


def running() -> bool:
    """Whether a rebuild is still going (GET /api/stats/rebuild)."""
    with _STATS_LOCK:
        return _STATS["running"]


def followed(cfg, out, always=False):
    """G13: a write that relinked rows reruns the stats in the background, as
    a load does, and says so, so the app watches for the new figures.
    `always` reruns them whatever moved: a trip's name and dates are in the
    travel stat's saved results even when no row changes trip."""
    moved = isinstance(out, dict) and (out.get("relinked") or {}).get("written")
    if isinstance(out, dict) and (moved or always) and cfg is not None:
        refresh_stats(cfg)
        return {**out, "stats": "rebuilding"}
    return out


def rebuilding(req, out):
    """A write the stats follow: they rerun in the background, and the answer says so."""
    refresh_stats(req.cfg)
    return {**out, "stats": "rebuilding"}
