"""Export the record (G9): the ledger and every stat result, as CSV and JSON.

Each export is a dated folder under the data folder's exports/, so nothing is
overwritten and an older export stays as it was. Money goes out twice in every
CSV: as the record's integer cents (`*_minor`) and as a decimal string for a
spreadsheet. A value the record does not know (an SGD amount with no FX rate)
stays empty rather than becoming 0.

G20: a stat's rows go out as the stat wrote them, in cents. A money column in
cents (`<name>_minor`) gets its dollars beside it under `<name>`, as the
record's columns do. A result from before G20 is not exported: the results
cache holds it back as no result (`money.in_cents`).
"""
import csv
import json
import re
from datetime import datetime
from pathlib import Path

from .money import MINOR
from .paths import data_root

STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}-\d{6}(-\d{1,3})?$")
FILE = re.compile(r"^[a-z0-9-]+\.(csv|json|txt)$")

TXN_COLUMNS = ("txn_key", "txn_date", "month", "account_name", "account_no", "currency",
               "amount_minor", "amount_sgd_minor", "direction", "bank_txn_type", "flow_class",
               "merchant_raw", "merchant_country", "counterparty", "memo", "reference",
               "payee_name", "category_name", "need_class", "trip_name", "card_last4",
               "is_synthetic", "period_name")


def exports_dir():
    return data_root() / "exports"


def _decimal(minor):
    return "" if minor is None else f"{minor / 100:.2f}"


NUMBER = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")


def _cell(value):
    """One CSV cell, safe to open in a spreadsheet: text that a spreadsheet
    would run as a formula (=, +, -, @, tab, carriage return first) gets a
    leading apostrophe. Numbers, including negative amounts, stay numbers."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r") \
            and not NUMBER.match(value):
        return "'" + value
    return value


def _write_csv(path, header, rows):
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([_cell(h) for h in header])
        w.writerows([_cell(v) for v in row] for row in rows)


def stat_rows(body):
    """The rows a stat result's card shows: the list its chart hint names,
    else the first list of objects in its data."""
    data = (body or {}).get("data") or {}
    source = ((body or {}).get("chart") or {}).get("source")
    if source and isinstance(data.get(source), list):
        return data[source]
    if isinstance(data.get("rows"), list):
        return data["rows"]
    return next((v for v in data.values()
                 if isinstance(v, list) and v and all(isinstance(x, dict) for x in v)), [])


def _whole(v):
    return isinstance(v, int) and not isinstance(v, bool)


def stat_table(rows):
    """(header, rows of cells) for a stat's rows: every key any row has, in
    order, and after each `<name>_minor` that has no `<name>` column of its
    own, that money in dollars (G20). A list or a dict goes out as its JSON."""
    keys = list(dict.fromkeys(k for r in rows for k in r))
    have = set(keys)
    columns = []
    for k in keys:
        columns.append((k, None))
        base = k[:-len(MINOR)] if k.endswith(MINOR) else ""
        if base and base not in have:
            columns.append((base, k))

    def cell(r, name, minor):
        if minor is not None:
            return _decimal(r.get(minor)) if _whole(r.get(minor)) else ""
        v = r.get(name)
        return json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v

    return [c for c, _ in columns], ([cell(r, c, m) for c, m in columns] for r in rows)


def export(conn, results, now=None):
    """Write one export folder; returns {folder, files}."""
    base = (now or datetime.now()).strftime("%Y-%m-%d-%H%M%S")
    exports_dir().mkdir(parents=True, exist_ok=True)
    for n in range(1000):
        # two exports in one second each get their own folder; none is reused
        stamp = base if n == 0 else f"{base}-{n + 1}"
        out = exports_dir() / stamp
        try:
            out.mkdir()
            break
        except FileExistsError:
            continue
    else:
        raise RuntimeError("too many exports in one second")
    out.chmod(0o700)

    txns = [dict(r) for r in conn.execute(
        f"SELECT {', '.join(TXN_COLUMNS)} FROM v_txn ORDER BY txn_date, txn_key")]
    header = list(TXN_COLUMNS[:7]) + ["amount", "amount_sgd_minor", "amount_sgd"] + list(TXN_COLUMNS[8:])
    _write_csv(out / "transactions.csv", header, (
        [t[c] for c in TXN_COLUMNS[:7]] + [_decimal(t["amount_minor"]), t["amount_sgd_minor"],
                                           _decimal(t["amount_sgd_minor"])]
        + [t[c] for c in TXN_COLUMNS[8:]] for t in txns))
    (out / "transactions.json").write_text(json.dumps(txns, indent=1, ensure_ascii=False) + "\n",
                                           encoding="utf-8")

    periods = [dict(r) for r in conn.execute(
        """SELECT name, account_name, account_no, currency, period_end, opening_minor,
                  closing_minor, total_in_minor, total_out_minor, fx_to_sgd_e9,
                  closing_sgd_minor, reconciled FROM v_period ORDER BY name""")]
    _write_csv(out / "balances.csv", list(periods[0].keys()) if periods else ["name"],
               ([p[k] for k in p] for p in periods))
    budgets = [dict(r) for r in conn.execute(
        "SELECT category, month, planned_minor, actual_minor FROM v_budget ORDER BY month, category")]
    _write_csv(out / "budgets.csv", ["category", "month", "planned_minor", "planned", "actual_minor", "actual"],
               ([b["category"], b["month"], b["planned_minor"], _decimal(b["planned_minor"]),
                 b["actual_minor"], _decimal(b["actual_minor"])] for b in budgets))
    from .db import trip_countries
    codes = trip_countries(conn)
    trips = [{**dict(r), "countries": ",".join(codes.get(r["id"], []))} for r in conn.execute(
        "SELECT id, name, start_date, end_date, notes FROM trip ORDER BY start_date")]
    _write_csv(out / "trips.csv", ["name", "start_date", "end_date", "countries", "notes"],
               ([t[k] for k in ("name", "start_date", "end_date", "countries", "notes")] for t in trips))
    for stat_id, body in sorted((results or {}).items()):
        if re.fullmatch(r"[a-z0-9-]+", stat_id):
            (out / f"stat-{stat_id}.json").write_text(json.dumps(body, indent=1, ensure_ascii=False) + "\n",
                                                      encoding="utf-8")
            header, cells = stat_table([r for r in stat_rows(body) if isinstance(r, dict)])
            _write_csv(out / f"stat-{stat_id}.csv", header or ["(no rows)"], cells)
    (out / "readme.txt").write_text(
        "Kopifund export. Money columns ending _minor are integer cents; the column\n"
        "beside each is the same value in dollars. An empty amount_sgd means the\n"
        "statement printed no FX rate to SGD for that block.\n", encoding="utf-8")
    files = sorted(p.name for p in out.iterdir())
    for name in files:
        (out / name).chmod(0o600)
    return {"folder": str(out), "stamp": stamp, "files": files, "transactions": len(txns)}


def listing():
    d = exports_dir()
    if not d.is_dir():
        return []
    return [{"stamp": p.name, "files": sorted(f.name for f in p.iterdir() if f.is_file())}
            for p in sorted(d.iterdir(), reverse=True) if p.is_dir() and STAMP.match(p.name)]


def path_of(stamp, name):
    """The file for a download link, or None when the name is not one an
    export wrote (no paths, no traversal)."""
    if not STAMP.match(stamp or "") or not FILE.match(name or ""):
        return None
    p = exports_dir() / stamp / name
    return p if p.is_file() else None
