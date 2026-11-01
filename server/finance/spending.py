"""The Spending explorer: where the money went over a chosen range of months.

Spending is what every stat counts as spending (server/analytics/stats/common.py,
`spend_rows`): External rows in a Spend-bucket category that are not synthetic
fillers, signed so a refund inside a spend category lowers its month. Amounts
are integer cents in SGD.

"Singapore only" leaves out every row linked to a trip. A row is put on a trip
when its date falls in the trip and something about it says it was spent away
(a foreign merchant country or a foreign-currency wallet; server/finance/sync.py,
`trip_for`), so the split needs no guessing: adding the trip tags its rows at
once (G13, server/finance/relink.py).
"""
import json
from collections import defaultdict

BY = ("bucket", "category", "payee")
SCOPES = ("sg", "all")
TOP = 7                     # series drawn in the chart before the rest become "Other"
OTHER = "Everything else"     # not "Other", which is also a bucket name


# The one definition of spending in SQL over v_txn, shared by the explorer,
# Transactions' spend=1 and the Trips page. A payment with no SGD value (its
# statement had no FX rate) has no figure to sit in, so it is left out.
SPEND_WHERE = ("flow_class = 'External' AND bucket = 'Spend' AND is_synthetic = 0 "
               "AND amount_sgd_minor IS NOT NULL")

# G13: the same rule for the other two buckets the stats add up. Income is what
# the monthly cash flow and the income stat count; a transfer is what the
# needs, wants and savings stat counts as savings (the Investment and Savings
# need classes, with Transactions' need= filter). Links from a stat's bar use
# these so the list holds exactly the rows the bar adds.
INCOME_WHERE = ("flow_class = 'External' AND bucket = 'Income' AND is_synthetic = 0 "
                "AND amount_sgd_minor IS NOT NULL")
TRANSFER_ROWS_WHERE = ("flow_class = 'External' AND bucket = 'Transfer' AND is_synthetic = 0 "
                       "AND amount_sgd_minor IS NOT NULL")


# A row's payee as the explorer, the stats and Transactions' `payee=` filter name
# it: the payee the rules gave it, else the text its statement printed
PAYEE_OF = ("COALESCE(NULLIF(payee_name, ''), NULLIF(merchant_raw, ''), NULLIF(counterparty, ''), "
            "'(no name)')")


class SpendingRefused(ValueError):
    pass


def _month_list(first, last):
    y, m = map(int, first.split("-"))
    ly, lm = map(int, last.split("-"))
    out = []
    while (y, m) <= (ly, lm):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _shift(month, n):
    y, m = map(int, month.split("-"))
    i = y * 12 + (m - 1) + n
    return f"{i // 12:04d}-{i % 12 + 1:02d}"


def per_month(cents, months):
    """An average a month in whole cents, rounded half away from zero, in
    integer arithmetic so no money passes through a float."""
    q, r = divmod(abs(cents), months)
    q += 2 * r >= months
    return q if cents >= 0 else -q


def _check_month(v, name):
    if v is None:
        return None
    s = str(v)
    if len(s) != 7 or s[4] != "-" or not (s[:4] + s[5:]).isdigit() or not 1 <= int(s[5:]) <= 12:
        raise SpendingRefused(f"{name} is a month, YYYY-MM")
    return s


def monthly_average(conn, months=12):
    """G14: spending a month over the last `months` months the record holds,
    trips included, in whole cents ({per_month, from, to, months}), or None
    with no rows. It is the Net worth page's hint for spending a month, and
    the figure the projection spends when the owner types none."""
    first, last = conn.execute("SELECT MIN(month), MAX(month) FROM v_txn").fetchone()
    if not first:
        return None
    frm = max(first, _shift(last, -(months - 1)))
    n = len(_month_list(frm, last))
    total = conn.execute("SELECT COALESCE(SUM(-amount_sgd_minor), 0) FROM v_txn WHERE " + SPEND_WHERE
                         + " AND month BETWEEN ? AND ?", (frm, last)).fetchone()[0]
    return {"per_month": per_month(total, n), "from": frm, "to": last, "months": n}


def _category_link(name):
    """Transactions' filter for a category as the explorer names it."""
    return {"category": "(none)" if name == "(uncategorised)" else name}


def _series_link(by, name, shown, folded):
    """G13: what Transactions needs, beside the range's own filters (`link` of
    the answer) and a month, to list exactly the rows one series of the chart
    adds up. The folded series is everything the named ones are not."""
    names = json.dumps(shown, ensure_ascii=False, separators=(",", ":"))
    if by == "bucket":
        return {"bucket": name}
    if by == "category":
        return {"not_categories": names} if folded else _category_link(name)
    return {"not_payees": names} if folded else {"payee": name}


def explore(conn, query):
    """{months, series, buckets, payees, biggest, totals} for one range.

    query: `from` and `to` (YYYY-MM, default the last 12 months the record
    holds), `scope` (sg or all, default sg), `by` (bucket, category or payee)."""
    by = query.get("by") or "bucket"
    scope = query.get("scope") or "sg"
    if by not in BY:
        raise SpendingRefused(f"by is one of {', '.join(BY)}")
    if scope not in SCOPES:
        raise SpendingRefused(f"scope is one of {', '.join(SCOPES)}")
    # the record's own months, not just the months with spending: a last month
    # of only income still ends the range, and "All" averages over every month
    first, last = conn.execute("SELECT MIN(month), MAX(month) FROM v_txn").fetchone()
    empty = {"by": by, "bucket_order": [], "bucket_style": {}, "scope": scope, "available": None,
             "months": [], "series": [], "buckets": [],
             "payees": [], "biggest": [], "link": {}, "totals": {"spend": 0, "per_month": 0, "overseas": 0}}
    if not first:
        return empty
    to = _check_month(query.get("to"), "to") or last
    frm = _check_month(query.get("from"), "from") or max(first, _shift(to, -11))
    if frm > to:
        raise SpendingRefused("from is after to")
    months = _month_list(frm, to)
    rows = conn.execute(
        """SELECT txn_key, month, txn_date, -amount_sgd_minor AS spend, trip_id IS NOT NULL AS away,
                  COALESCE(category_name, '(uncategorised)') AS category,
                  COALESCE(category_group, 'Other') AS bucket,
                  COALESCE(payee_name, '') AS payee, merchant_raw, counterparty
           FROM v_txn
           WHERE """ + SPEND_WHERE + " AND month BETWEEN ? AND ?", (frm, to)).fetchall()
    overseas = sum(r["spend"] for r in rows if r["away"])
    if scope == "sg":
        rows = [r for r in rows if not r["away"]]

    def payee_of(r):
        return r["payee"] or r["merchant_raw"] or r["counterparty"] or "(no name)"

    key = {"bucket": lambda r: r["bucket"], "category": lambda r: r["category"], "payee": payee_of}[by]
    per = defaultdict(lambda: defaultdict(int))
    for r in rows:
        per[key(r)][r["month"]] += r["spend"]
    ranked = sorted(per, key=lambda k: -sum(per[k].values()))
    # buckets are few and each keeps its colour, so all of them are drawn
    cut = len(ranked) if by == "bucket" else TOP
    shown, rest = ranked[:cut], ranked[cut:]
    series = [{"name": k, "total": sum(per[k].values()), "values": [per[k].get(m, 0) for m in months]}
              for k in shown]
    if rest:
        series.append({"name": OTHER, "total": sum(sum(per[k].values()) for k in rest),
                       "values": [sum(per[k].get(m, 0) for k in rest) for m in months],
                       "folded": len(rest)})
    n = len(months)
    names = [k for k in shown]
    for s in series:
        s["per_month"] = per_month(s["total"], n)
        s["link"] = _series_link(by, s["name"], names, folded="folded" in s)

    # the ranked list: buckets with their categories, whatever the chart is split by
    cats = defaultdict(lambda: defaultdict(int))
    for r in rows:
        cats[r["bucket"]][r["category"]] += r["spend"]
    buckets = sorted(({"name": b, "total": sum(c.values()), "per_month": per_month(sum(c.values()), n),
                       "categories": sorted(({"name": k, "total": v, "per_month": per_month(v, n),
                                              "link": _category_link(k)}
                                             for k, v in c.items()), key=lambda x: -x["total"])}
                      for b, c in cats.items()), key=lambda x: -x["total"])

    pay = defaultdict(lambda: [0, 0])
    for r in rows:
        p = pay[payee_of(r)]
        p[0] += r["spend"]
        p[1] += 1
    payees = [{"name": k, "total": v[0], "count": v[1], "link": {"payee": k}}
              for k, v in sorted(pay.items(), key=lambda kv: -kv[1][0])[:10]]
    # each of the biggest carries its row's key, so its link lists that one payment
    biggest = [{"key": r["txn_key"], "date": r["txn_date"], "payee": payee_of(r), "category": r["category"],
                "amount": r["spend"]}
               for r in sorted(rows, key=lambda r: -r["spend"])[:5] if r["spend"] > 0]
    total = sum(r["spend"] for r in rows)
    # the spend buckets in their own order, so a page can give each a colour
    # that stays put whatever the range ranks first
    groups = conn.execute("SELECT * FROM category_group WHERE kind = 'spend' "
                          "ORDER BY COALESCE(sort_order, 1e9), name").fetchall()
    order = [g["name"] for g in groups]
    # G17 (U1): each spend bucket's icon and colour; G18: `shown`, the colour it is drawn in, is worked out
    # over every bucket (income and transfers too), so it is the colour the Categories page shows it in
    from .categories import styles
    every = styles(conn)
    return {"by": by, "bucket_order": order, "bucket_style": {name: every[name] for name in order},
            "scope": scope, "available": {"from": first, "to": last}, "months": months,
            "series": series, "buckets": buckets, "payees": payees, "biggest": biggest,
            "link": {"from": months[0], "to": months[-1], "spend": "1", **({"home": "1"} if scope == "sg" else {})},
            "totals": {"spend": total, "per_month": per_month(total, n), "overseas": overseas}}
