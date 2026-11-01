"""Net worth over time, in cents, from what the stats already hold (G8).

`net_worth(results)`: cash, investments and CPF month by month, from the
savings-runway, portfolio and cpf results. Each part is carried forward from
its last measured date (a month end for cash, a statement's last day for Tiger
Brokers, 31 December for CPF), and each row names those dates. The months start
where every part the record holds has been measured once, so a part never
appears from nowhere.

G19: the Net worth page (server/analytics/growth.py) and Home both draw it, so it
lives in finance, which analytics imports and never the reverse.
"""
from .dates import add_months
from .money import stat_cents


def month_of(day):
    return str(day)[:7]


def months_between(a, b):
    """Every month from a to b inclusive; empty when b is before a."""
    out, m = [], a
    while m <= b:
        out.append(m)
        m = add_months(m, 1)
    return out


# ------------------------------------------------------------ net worth

def _parts(results):
    """{part: [(month, cents, as_of)]}, oldest first, for each part the record
    holds: cash at each month end, investments at each statement's last day,
    CPF at each 31 December.

    Cash counts only the months where every bank account printed a statement
    (savings-runway's `complete`). A month with a gap in one bank carries that
    bank's older balance, so it is not a fresh measurement: the last complete
    month is carried over it instead, and its date says so."""
    out = {}
    runway = (results.get("savings-runway") or {}).get("data") or {}
    # G20: each figure in cents, as the stat wrote it (money.stat_cents)
    cash = [(m["month"], stat_cents(m, "cash"), m["month"]) for m in runway.get("months") or ()
            if m.get("month") and stat_cents(m, "cash") is not None and m.get("complete", True)]
    if cash:
        out["cash"] = cash
    folio = (results.get("portfolio") or {}).get("data") or {}
    invest = [(month_of(p["period_to"]), stat_cents(p, "value_sgd"), p["period_to"])
              for p in folio.get("periods") or () if p.get("period_to") and stat_cents(p, "value_sgd") is not None]
    if invest:
        out["investments"] = invest
    cpf = (results.get("cpf") or {}).get("data") or {}
    years = [(month_of(y["as_of"]), stat_cents(y, "total"), y["as_of"])
             for y in cpf.get("years") or () if y.get("as_of") and stat_cents(y, "total") is not None]
    if years:
        out["cpf"] = years
    return {k: sorted(v) for k, v in out.items()}


def net_worth(results):
    """{parts, months}: one row per month with each part carried forward from
    its last measurement and the date of that measurement, in cents."""
    parts = _parts(results)
    if not parts:
        return {"parts": [], "months": []}
    start = max(v[0][0] for v in parts.values())
    end = max(v[-1][0] for v in parts.values())
    rows = []
    for m in months_between(start, end):
        row = {"month": m, "total": 0}
        for name, points in parts.items():
            last = [p for p in points if p[0] <= m][-1]
            row[name] = last[1]
            row[f"{name}_as_of"] = last[2]
            row["total"] += last[1]
        rows.append(row)
    return {"parts": [p for p in ("cash", "investments", "cpf") if p in parts], "months": rows}
