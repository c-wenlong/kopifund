#!/usr/bin/env python3
"""Write the hub page's two tables from the analytics results.

The finance hub page in Notion shows numbers, not databases.
Notion on the free plan draws no charts, so the numbers are rows:

  Metrics   one row per headline figure: Value, Unit, As of, Note, and the
            stat id it came from. Rows with a Stat are the loader's and are
            rewritten on every `finance.py analyse`; a row you add by hand
            (no Stat) is left alone. Cash and investments are measured on
            different days, so the two rows that mix them name both dates.
  Months    one row per month the stats cover: income, spending, the
            investing transfer, what was left, cash at month end, and whether
            the month's statements were complete. The same series the charts
            are drawn from, so the page has the trend without a chart.

Both are upserts keyed on Name, the way the rollup treats Budgets: a second
run with nothing new issues zero writes.

G20: the rows are built in cents (`Value_minor`, `Income_minor`, ...), as the
stats write them, and Notion's number fields hold dollars, so the cents
become dollars in one place, `notion_row`, by `money.from_cents`, as the rows
go out.
"""
from collections import Counter

from finance import notion
# G19: the Metrics rows are finance's (Home reads its headline figures from
# them, and finance never imports analytics); publish writes them as before
from finance.metrics import METRIC_ORDER, _data, later, metrics_from  # noqa: F401
from finance.model import canonicalise, diff, page_properties
from finance.money import MINOR, from_cents, stat_cents

METRIC_TYPES = {"Name": "title", "Value": "number", "Unit": "select", "As of": "rich_text",
                "Note": "rich_text", "Stat": "rich_text", "Order": "number"}
MONTH_TYPES = {"Name": "title", "Income": "number", "Spending": "number",
               "Investing": "number", "Net": "number", "Cash": "number",
               "Partial": "checkbox", "Phase": "select"}


def notion_row(row):
    """G20: a Metrics or Months row as Notion holds it, the one place the hub
    page turns cents into dollars: each `<field>_minor` becomes `<field>`, its
    cents as the exact dollars `from_cents` gives, as the float a Notion
    number takes (None stays None). Every other field goes as it is."""
    out = {}
    for k, v in row.items():
        if k.endswith(MINOR):
            out[k[:-len(MINOR)]] = None if v is None else float(from_cents(v))
        else:
            out[k] = v
    return out


def months_from(results):
    """The Months rows, oldest first, their money in cents (`notion_row`
    writes it as dollars)."""
    from analytics.stats import common as c
    cashflow, _ = _data(results, "monthly-cashflow")
    runway, _ = _data(results, "savings-runway")
    cash = {m["month"]: stat_cents(m, "cash") for m in runway.get("months", [])}
    rows = []
    for m in cashflow.get("months", []):
        phase = next((p["name"] for p in c.configured_phases() if c.in_phase(m["month"], p)), None)
        rows.append({"Name": m["month"], "Income" + MINOR: stat_cents(m, "income"),
                     "Spending" + MINOR: stat_cents(m, "spend"), "Investing" + MINOR: stat_cents(m, "investing"),
                     "Net" + MINOR: stat_cents(m, "net_after_investing"),
                     "Cash" + MINOR: cash.get(m["month"]), "Partial": bool(m.get("partial")),
                     "Phase": phase})
    return rows


def _upsert(client, ds_id, rows, types, owned, dry_run, echo, label):
    """Create or patch rows keyed on Name; rows `owned` does not claim are
    left alone. Returns a Counter."""
    have = {notion.plain(p, "Name"): p for p in client.query_all(ds_id)}
    counts = Counter()
    writes = []
    for r in rows:
        want = canonicalise(notion_row(r), types)
        page = have.get(want["Name"])
        if page is None:
            counts["created"] += 1
            writes.append((None, notion.to_api(want, types)))
            continue
        if not owned(page):
            counts["yours"] += 1
            continue
        changed = diff(want, page_properties(page, types))
        if not changed:
            counts["unchanged"] += 1
            continue
        counts["updated"] += 1
        writes.append((page, notion.to_api({k: want[k] for k in changed}, types)))
    if not dry_run:
        client.map(lambda w: client.patch(w[0]["id"], w[1]) if w[0]
                   else client.create(ds_id, w[1]), writes)
    if echo:
        echo(f"  {label}: {counts['created']} created, {counts['updated']} updated, "
             f"{counts['unchanged']} unchanged"
             + (f", {counts['yours']} of yours left alone" if counts["yours"] else "")
             + ("  (dry run, nothing written)" if dry_run else ""))
    return counts


def publish(cfg, client, results, snapshot=None, dry_run=False, echo=print, today=None):
    """Write Metrics and Months when config names them. Returns {table: Counter}."""
    out = {}
    if cfg.data_sources.get("metrics"):
        out["metrics"] = _upsert(client, cfg.ds("metrics"), metrics_from(results, snapshot, today),
                                 METRIC_TYPES, lambda p: bool(notion.plain(p, "Stat")),
                                 dry_run, echo, "metrics")
    if cfg.data_sources.get("months"):
        if months_consistent(results):
            out["months"] = _upsert(client, cfg.ds("months"), months_from(results), MONTH_TYPES,
                                    lambda p: True, dry_run, echo, "months")
        else:
            echo("  months: not written, monthly-cashflow and savings-runway come from different "
                 "snapshots; run analyse without --only to refresh both")
    return out


def months_consistent(results):
    """True when the two stats a Months row joins were computed from the same
    snapshot: an --only run over a newer record must not pair fresh cash with
    old income and spending."""
    a = (results.get("monthly-cashflow") or {}).get("snapshot")
    b = (results.get("savings-runway") or {}).get("snapshot")
    return a == b
