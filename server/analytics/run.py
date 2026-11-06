#!/usr/bin/env python3
"""Build or reuse today's snapshot, run every stat, write the cache.

This is what `python3 server/finance.py analyse` calls. It writes only inside
`data/cache/`; nothing here touches Notion except to read it.
"""
import json
from datetime import date
from pathlib import Path

from finance import snapshot as snap
from finance.atomic import write_private
from finance.money import in_cents
from . import stats


class UnknownStat(ValueError):
    """`--only` named a stat that does not exist.

    Raised rather than ignored: `--only forward_view` used to run every stat
    and `--only forwards-view` used to run none of them, and both printed the
    same cheerful summary as a full pass.
    """


def check_only(only):
    """Every id in `--only` has to be a stat. Checked before the snapshot is
    built, so a typo does not cost a pull of eight databases."""
    known = {m.ID for m in stats.modules()}
    unknown = [i for i in dict.fromkeys(only) if i not in known]
    if unknown:
        raise UnknownStat(
            f"--only names {len(unknown)} stat(s) that do not exist: "
            f"{', '.join(unknown)}. The ids are: {', '.join(sorted(known))}")


def build_or_reuse(cfg, source="notion", csv_dir=None, refresh=False,
                   cache=snap.CACHE, day=None, echo=print):
    """Today's snapshot: the one on disk unless it is missing or --refresh.
    Only a Notion snapshot is reused. A snapshot of kopifund.db is always
    rebuilt: the record is local and quick to read, and a load since the last
    snapshot must show. A CSV snapshot is always rebuilt too: the folder given
    is the input, and it has to pass build_from_csv's block checks every time,
    whatever folder or check today's snapshot on disk came from."""
    day = day or date.today().isoformat()
    existing = snap.today_snapshot(cache, day)
    if existing and not refresh and source == "notion":
        # a snapshot saved before accounts carried labels gets them now (G13)
        s = snap.labelled(snap.load(existing), cfg)
        # G20: one saved before its amounts were cents is built again
        if s["meta"]["source"] == source and snap.in_cents(s):
            echo(f"snapshot: reusing {existing.name} "
                 f"({len(s['transactions'])} transactions, source {source})")
            return s, existing
    echo(f"snapshot: building from {source}")
    s = snap.build(cfg, source=source, csv_dir=csv_dir)
    path = snap.save(s, cache, day)
    s["meta"]["file"] = path.name
    echo(f"snapshot: wrote {path.name} ({len(s['transactions'])} transactions)")
    if source == "notion":
        snap.write_categories_fallback(s)
    return s, path


def run_stats(s, out_dir, only=(), benchmarks=None, day=None, echo=print):
    """Run every stat (or the ones named) and write one result JSON each.

    Returns {stat id: result}. A stat that raises is reported and skipped: one
    broken analysis must not cost the other twelve.
    """
    check_only(only)
    out_dir = Path(out_dir)
    (out_dir / "results").mkdir(parents=True, exist_ok=True)
    day = day or date.today().isoformat()
    if benchmarks is None:
        benchmarks = stats.load_benchmarks()
    chosen = [m for m in stats.modules() if not only or m.ID in only]
    results, written = {}, {}
    for module in chosen:
        try:
            res = module.run(s, benchmarks)
        except Exception as e:                                # noqa: BLE001
            echo(f"  {module.ID:24s} FAILED: {type(e).__name__}: {e}")
            continue
        name = f"{module.ID}-{day}.json"
        write_private(out_dir / "results" / name, snap.dumps(res))
        results[module.ID] = res
        written[module.ID] = name
        first = res["findings"][0] if res["findings"] else "(no finding)"
        echo(f"  {module.ID:24s} {first}")
    latest = snap.read_latest(out_dir)
    latest.setdefault("results", {}).update(written)
    snap.write_latest(latest, out_dir)
    return results


def cached_results(out_dir, stale=None):
    """{stat id: result} for every result latest.json points at, read back.

    The hub page's Metrics rows are numbered across every stat, so a run of
    `--only` publishes from these, with its own fresh results already in them,
    rather than from the subset, which would number its rows from one again.
    A file latest.json names but that is gone is skipped, and so is a result
    from before its stat wrote cents (G20, money.in_cents): the hub page would
    otherwise be handed dollars it no longer reads. Its rows on the page are
    left as the last publish wrote them, as a stat with no result is; the ids
    of those results go into `stale` when a list is given, so `analyse` can
    say so."""
    out_dir = Path(out_dir)
    out = {}
    for stat_id, name in sorted(snap.read_latest(out_dir).get("results", {}).items()):
        path = out_dir / "results" / name
        if path.exists():
            body = json.loads(path.read_text(encoding="utf-8"))
            if in_cents(body):
                out[stat_id] = body
            elif stale is not None:
                stale.append(stat_id)
    return out


def analyse(cfg, source="notion", csv_dir=None, refresh=False, only=(),
            out_dir=snap.CACHE, day=None, echo=print):
    """The whole pass. Returns (snapshot, {stat id: result})."""
    check_only(only)
    s, _ = build_or_reuse(cfg, source=source, csv_dir=csv_dir, refresh=refresh,
                          cache=out_dir, day=day, echo=echo)
    benchmarks = stats.load_benchmarks()
    if benchmarks is None:
        echo("benchmarks: server/analytics/benchmarks.json not found; the stats that use "
             "it say so in their caveats")
    results = run_stats(s, out_dir, only=only, benchmarks=benchmarks, day=day,
                        echo=echo)
    return s, results


def rebuild(cfg):
    """Rerun every stat over the record into the cache, quietly: what a load and
    an edit in the app end with. serve.py hands it to server/finance/monthly.py
    (`rebuild_stats_with`), whose `analyse` runs one rebuild at a time (G19)."""
    s, results = analyse(cfg, source="sqlite", refresh=True, out_dir=snap.CACHE,
                         echo=lambda *_: None)
    return {"stats": len(results), "transactions": len(s["transactions"])}
