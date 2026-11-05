"""G26: the dashboard's widget query layer, and the app state it keeps.

A widget asks one question, a WidgetQuery (server/finance/types.py): a source,
a metric, a dimension to spread it over, a range and optional filters named
the way Transactions' links name them. `resolve` answers with `labels` and
`series` -- money as integer cents under `values_minor` (unit `sgd_minor`),
anything else under `values` with its own `unit` ("months", "fraction",
"percent") -- plus `label_links`, the Transactions filters a point lists, the
G13 chart.links idea carried to widgets. An unknown source, metric or
dimension is a DashboardRefused, a 4xx, never a blank chart.

Sources: `spending` reads the record's v_txn with the Spending explorer's own
WHEREs (server/finance/spending.py); the others read the cached stat results
(`savings-runway`, `monthly-cashflow`, ...), and `net-worth` reuses
server/finance/net_worth.py over them, so no widget re-runs a stat.

The dashboard's own state -- layouts, goals, the allocation template and the
game pot's settings -- lives in the record's `setting` table as JSON, the way
the app icon and the AI settings do (server/finance/app_icon.py). A preview
(scenario_preview) writes nothing: no apply endpoint exists yet, on purpose.
"""
import json
import re
from datetime import datetime, timezone

from . import db, net_worth, spending
from .dates import add_months
from .errors import NotFound
from .money import stat_cents


class DashboardRefused(ValueError):
    """A widget query or a dashboard write the server will not take: 4xx."""


# --------------------------------------------------------------- the catalogue

def _minor(name):
    return (f"{name}_minor", "sgd_minor")


# What a source can be asked: per dimension, the result's list of rows and the
# field its label comes from, and per metric the field read and its unit.
# `sgd_minor` fields are integer cents under `<name>_minor` (money.stat_cents);
# every other unit reads the field it is named. A stat source's points link to
# the rows they count through the stat's own `chart.links` (_stat_link), so
# the stat stays the one place that says which rows a figure adds up.
_CATALOGUE = {
    "spending": {
        "dims": {
            "month": {"metrics": {m: _minor(m) for m in ("spend", "income", "transfers", "net")}},
            "bucket": {"metrics": {m: _minor(m) for m in ("spend", "income", "transfers", "net")}},
            "category": {"metrics": {m: _minor(m) for m in ("spend", "income", "transfers", "net")}},
            "payee": {"metrics": {m: _minor(m) for m in ("spend", "income", "transfers", "net")}},
            "account": {"metrics": {m: _minor(m) for m in ("spend", "income", "transfers", "net")}},
            "need": {"metrics": {m: _minor(m) for m in ("spend", "income", "transfers", "net")}},
        },
        "filters": True,
        "split": True,
    },
    "monthly-cashflow": {
        "dims": {"month": {"rows": "months", "x": "month", "grain": "month",
                           "metrics": {m: _minor(m) for m in ("income", "spend", "net", "investing")}}},
    },
    "needs-wants-savings": {
        "dims": {"month": {"rows": "months", "x": "month", "grain": "month",
                           "metrics": {"income": _minor("income"), "need": _minor("need"),
                                       "want": _minor("want"), "savings": _minor("savings"),
                                       "need_share": ("need_share", "fraction"),
                                       "want_share": ("want_share", "fraction"),
                                       "savings_share": ("savings_share", "fraction")}}},
    },
    "savings-runway": {
        "dims": {"month": {"rows": "months", "x": "month", "grain": "month",
                           "metrics": {"cash": _minor("cash"), "spend": _minor("spend"),
                                       "carried": _minor("carried_amount"),
                                       "runway_months": ("runway_months", "months")}}},
    },
    "category-breakdown": {
        "dims": {"category": {"rows": "categories", "x": "category", "grain": "none",
                              "metrics": {"total": _minor("total"), "monthly_avg": _minor("monthly_avg"),
                                          "last3_avg": _minor("last3_avg"),
                                          "prior6_avg": _minor("prior6_avg"),
                                          "share": ("share_pct", "percent"),
                                          "trend": ("trend_pct", "percent")}}},
    },
    "portfolio": {
        "dims": {
            "holding": {"rows": "holdings", "x": "symbol", "grain": "none",
                        "metrics": {"value": _minor("value"), "unrealized_pl": _minor("unrealized_pl")}},
            "period": {"rows": "periods", "x": "period_to", "grain": "period",
                       "metrics": {"value_sgd": _minor("value_sgd"), "value_base": _minor("value_base"),
                                   "contributed_sgd": _minor("contributed_sgd"),
                                   "deposits_sgd": _minor("deposits_sgd")}},
        },
    },
    "cpf": {
        "dims": {"year": {"rows": "years", "x": "year", "grain": "year",
                          "metrics": {"oa": _minor("oa"), "sa": _minor("sa"),
                                      "ma": _minor("ma"), "total": _minor("total")}}},
    },
    "net-worth": {
        # net_worth.py's rows name their cents plain (cash, total), no _minor
        "dims": {"month": {"metrics": {m: (m, "sgd_minor") for m in ("total", "cash", "investments", "cpf")}},
                 "part": {"metrics": {"value": ("value", "sgd_minor")}}},
    },
    "forward-view": {
        "dims": {"month": {"rows": "months", "x": "month", "grain": "month",
                           "metrics": {m: _minor(m) for m in
                                       ("cash", "net", "spend", "take_home", "gross", "investing",
                                        "insurance", "other_income", "repayments", "trips",
                                        "one_offs", "cpf_total", "cpf_accrued")}}},
        "projection": True,
    },
}

SOURCES = tuple(_CATALOGUE)
"""The data sources a WidgetQuery names, in catalogue order."""
DIMENSIONS = ("month", "bucket", "category", "payee", "account", "need", "part", "year", "period",
              "holding")
"""The dimensions a WidgetQuery may spread a metric over (a source keeps its own list)."""
UNITS = ("sgd_minor", "months", "fraction", "percent")
"""The unit a resolved series is in: integer cents, or the number it says."""
SPLITS = ("bucket", "category", "payee", "account", "need")
"""What a `spending` query may split a month dimension into series by."""

# the Transactions filter names a WidgetQuery's `filters` takes (api.transactions);
# `scope` is the Spending explorer's sg/all, which a link carries as `home=1`
FILTERS = ("home", "trip", "need", "bucket", "categories", "not_categories", "not_payees",
           "payee", "account", "scope")

# a month or a day a range end may be (Transactions' own range ends)
def _is_month(v):
    return isinstance(v, str) and len(v) == 7 and v[4] == "-" and (v[:4] + v[5:]).isdigit() \
        and 1 <= int(v[5:]) <= 12


def _is_day(v):
    return isinstance(v, str) and len(v) == 10 and v[4] == "-" and v[7] == "-" \
        and _is_month(v[:7]) and v[8:].isdigit()


def parse_query(data):
    """A WidgetQuery body checked against the catalogue, as a plain dict."""
    if not isinstance(data, dict):
        raise DashboardRefused("A widget query is an object")
    source = data.get("source")
    if source not in _CATALOGUE:
        raise DashboardRefused(f"Source is one of {', '.join(_CATALOGUE)}")
    spec = _CATALOGUE[source]
    dims = spec["dims"]
    dimension = data.get("dimension") or (next(iter(dims)) if len(dims) == 1 else "month")
    if dimension not in dims:
        raise DashboardRefused(f"For {source}, dimension is one of {', '.join(dims)}")
    metrics = dims[dimension]["metrics"]
    metric = data.get("metric") or (next(iter(metrics)) if len(metrics) == 1 else None)
    if metric not in metrics:
        raise DashboardRefused(f"For {source} by {dimension}, metric is one of {', '.join(metrics)}")
    rng = data.get("range")
    if rng is not None:
        if not isinstance(rng, dict):
            raise DashboardRefused("Range is an object: {from, to}")
        frm, to = rng.get("from"), rng.get("to")
        for name, v in (("from", frm), ("to", to)):
            if v is not None and not (_is_month(v) or _is_day(v)):
                raise DashboardRefused(f"Range.{name} is a month, YYYY-MM, or a day, YYYY-MM-DD")
        if frm and to and frm > to:
            raise DashboardRefused("Range.from is after range.to")
        rng = {k: v for k, v in (("from", frm), ("to", to)) if v}
        if rng and not spec.get("filters") and dimension not in ("month", "year", "period"):
            raise DashboardRefused(f"A range does not apply to {source} by {dimension}")
    split = data.get("split")
    if split is not None:
        if not spec.get("split") or dimension != "month":
            raise DashboardRefused("Split applies to a spending query by month")
        if split not in SPLITS:
            raise DashboardRefused(f"Split is one of {', '.join(SPLITS)}")
    filters = data.get("filters")
    if filters:
        if not isinstance(filters, dict):
            raise DashboardRefused("Filters is an object of Transactions filter names")
        unknown = sorted(set(filters) - set(FILTERS))
        if unknown:
            raise DashboardRefused(f"Filters takes: {', '.join(FILTERS)} (not {', '.join(unknown)})")
        if not spec.get("filters"):
            raise DashboardRefused(f"Filters apply to the spending source, not {source}")
        filters = {k: str(v) for k, v in filters.items()}
        _filter_where(filters)         # a bad value is refused now, not mid-resolve
    out = {"source": source, "metric": metric, "dimension": dimension}
    if rng:
        out["range"] = rng
    if split:
        out["split"] = split
    if filters:
        out["filters"] = filters
    return out


def query_from_get(query):
    """The GET spelling of a WidgetQuery: `?q=` holds it as JSON."""
    raw = query.get("q")
    if raw is None:
        raise DashboardRefused("The q parameter is a WidgetQuery as JSON")
    try:
        return json.loads(raw)
    except ValueError:
        raise DashboardRefused("The q parameter is a WidgetQuery as JSON") from None


# ------------------------------------------------------------------ spending

# The metric's rows: the explorer's WHEREs (spending.py) plus `net`, the signed
# total of every External row (income in, spending and transfers out). Positive
# means out for `spend` and `transfers`, in for `income`, signed for `net`.
_METRIC_WHERE = {
    "spend": spending.SPEND_WHERE,
    "income": spending.INCOME_WHERE,
    "transfers": spending.TRANSFER_ROWS_WHERE,
    "net": ("flow_class = 'External' AND bucket IN ('Income', 'Spend', 'Transfer') "
            "AND is_synthetic = 0 AND amount_sgd_minor IS NOT NULL"),
}
_METRIC_VALUE = {"spend": "-amount_sgd_minor", "income": "amount_sgd_minor",
                 "transfers": "-amount_sgd_minor", "net": "amount_sgd_minor"}
# The Transactions flags that list a metric's rows. Transactions hides the
# Transfer bucket unless told not to, so a metric that counts transfers says so.
_METRIC_FLAG = {"spend": {"spend": "1"}, "income": {"income": "1"},
                "transfers": {"transfer": "1", "hide_transfers": "0"},
                "net": {"income": "1", "spend": "1", "transfer": "1", "hide_transfers": "0"}}

# a dimension's value in v_txn, named the way the explorer names it. An
# account groups by its id, as Transactions' `account` filter names it, and
# its name is only the label
_DIM_EXPR = {
    "bucket": "COALESCE(category_group, 'Other')",
    "category": "COALESCE(category_name, '(uncategorised)')",
    "payee": spending.PAYEE_OF,
    "account": "account_id",
    "need": "COALESCE(need_class, '(none)')",
}
# a slice's Transactions link: the filter that lists exactly the rows behind
# it (None where no filter names it, e.g. a need class of none)
_DIM_LINK = {
    "bucket": lambda name, shown: {"bucket": name},
    "category": lambda name, shown: ({"not_categories": _names(shown)} if name == spending.OTHER
                                     else {"category": "(none)" if name == "(uncategorised)" else name}),
    "payee": lambda name, shown: ({"not_payees": _names(shown)} if name == spending.OTHER
                                  else {"payee": name}),
    "account": lambda name, shown: {"account": str(name)},
    "need": lambda name, shown: (None if name == "(none)" else {"need": name}),
}


def _slice_link(base, dimension, key, shown):
    """One slice's Transactions link: the query's own link plus the slice's
    filter, or None where no filter names the slice. The folded slice's
    exclusions join any the query's filters already made, so it lists only
    rows the query counted."""
    if key is None:
        return None
    extra = _DIM_LINK[dimension](key, shown)
    if extra is None:
        return None
    out = {**base, **extra}
    for name in ("not_categories", "not_payees"):
        if base.get(name) and name in extra:     # an empty filter is no filter
            out[name] = _names(dict.fromkeys(json.loads(base[name]) + json.loads(extra[name])))
    return out


def _names(values):
    """A JSON list of names, Transactions' way of passing several."""
    return json.dumps(list(values), ensure_ascii=False, separators=(",", ":"))


def _effective_scope(filters):
    """The scope a spending query counts under: `sg`, the explorer's own
    default; `all` when asked, or when a `trip` filter asks for a trip's rows."""
    scope = filters.get("scope") or ("all" if filters.get("trip") else "sg")
    if scope not in spending.SCOPES:
        raise DashboardRefused(f"Filters.scope is one of {', '.join(spending.SCOPES)}")
    return scope


def _filter_where(filters):
    """A WidgetQuery's `filters` as a WHERE over v_txn, the transactions
    filters' own semantics (server/finance/api.py, `transactions`)."""
    where, args = [], []
    if _effective_scope(filters) == "sg" or filters.get("home") == "1":
        where.append("trip_id IS NULL")
    if filters.get("trip"):
        where.append("trip_name = ?")
        args.append(filters["trip"])
    if filters.get("need"):
        needs = [n for n in filters["need"].split(",") if n]
        where.append(f"need_class IN ({', '.join('?' * len(needs))})")
        args += needs
    if filters.get("bucket"):
        where.append("COALESCE(category_group, 'Other') = ?")
        args.append(filters["bucket"])
    for key, column, negate in (("categories", "category_name", False),
                                ("not_categories", "category_name", True),
                                ("not_payees", spending.PAYEE_OF, True)):
        if filters.get(key):
            try:
                names = json.loads(filters[key])
            except ValueError:
                raise DashboardRefused(f"Filters.{key} is a JSON list of names") from None
            if not isinstance(names, list):
                raise DashboardRefused(f"Filters.{key} is a JSON list of names")
            where.append(f"COALESCE({column}, '') {'NOT ' if negate else ''}IN "
                         f"({', '.join('?' * len(names))})")
            args += names
    if filters.get("payee"):
        where.append(f"{spending.PAYEE_OF} = ?")
        args.append(filters["payee"])
    if filters.get("account"):
        # as Transactions' `account` filter: the account's id, not a name
        if not filters["account"].isdigit():
            raise DashboardRefused("Filters.account is an account's id")
        where.append("account_id = ?")
        args.append(int(filters["account"]))
    return where, args


def _filters_link(filters):
    """The filters as Transactions link params, so a point's link lists the
    rows the query counted (scope=sg, the default, is `home=1`)."""
    out = {k: v for k, v in filters.items() if k != "scope"}
    if _effective_scope(filters) == "sg":
        out["home"] = "1"
    return out


def _spend_series(conn, q):
    """A spending query: the metric's rows over v_txn, spread over the
    dimension (or, by month, split into a series per `split` name)."""
    first, last = (None, None)
    if conn is not None:
        r = conn.execute("SELECT MIN(month) AS first, MAX(month) AS last FROM v_txn").fetchone()
        first, last = r["first"], r["last"]
    rng = q.get("range") or {}
    # a day end resolves to its month: the ledger aggregates whole months
    to = (rng.get("to") or last or "")[:7] or None
    frm = (rng.get("from") or "")[:7] or (max(first, add_months(to, -11)) if to else None)
    empty = {"source": "spending", "metric": q["metric"], "dimension": q["dimension"],
             "unit": "sgd_minor", "labels": [], "series": [], "range": None}
    if not first or (to or "") < first or (frm or "") > last:
        return empty
    to = min(to, last)
    frm = max(frm, first)
    if frm > to:
        raise DashboardRefused("Range.from is after range.to")
    months = net_worth.months_between(frm, to)
    fwhere, fargs = _filter_where(q.get("filters") or {})
    where = f"{_METRIC_WHERE[q['metric']]} AND month BETWEEN ? AND ?"
    args = [frm, to] + fargs
    value = _METRIC_VALUE[q["metric"]]
    base_link = {"from": frm, "to": to, **_METRIC_FLAG[q["metric"]], **_filters_link(q.get("filters") or {})}

    if q["dimension"] == "month":
        return _spend_by_month(conn, q, where, fwhere, args, months)

    expr = _DIM_EXPR[q["dimension"]]
    rows = conn.execute(f"SELECT {expr} AS k, SUM({value}) AS v FROM v_txn "
                        f"WHERE {where}{' AND ' + ' AND '.join(fwhere) if fwhere else ''} "
                        "GROUP BY k ORDER BY v DESC", args).fetchall()
    ranked = [(r["k"], r["v"]) for r in rows]
    fold = q["dimension"] in ("category", "payee")
    cut = spending.TOP if fold else len(ranked)
    shown, rest = ranked[:cut], ranked[cut:]
    shown_keys = [k for k, _ in shown]
    keys, values = list(shown_keys), [v for _, v in shown]
    if rest:
        keys.append(spending.OTHER)
        values.append(sum(v for _, v in rest))
    label_of = _dim_label(conn, q["dimension"])
    labels = [label_of(k) for k in keys]
    label_links = [_slice_link(base_link, q["dimension"], k, shown_keys) for k in keys]
    return {"source": "spending", "metric": q["metric"], "dimension": q["dimension"],
            "unit": "sgd_minor", "labels": labels, "label_links": label_links,
            "series": [{"name": q["metric"], "values_minor": values}],
            "range": {"from": frm, "to": to}}


def _dim_label(conn, dimension):
    """The label a dimension's group key shows: an account's name for its id,
    every other key the name it is."""
    if dimension != "account":
        return str
    names = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM account")}
    return lambda i: names.get(i, "(unknown)")


def _spend_by_month(conn, q, where, fwhere, args, months):
    """dimension `month`: one series of the metric, or one per `split` name."""
    label_links = [{"from": m, "to": m, **_METRIC_FLAG[q["metric"]],
                    **_filters_link(q.get("filters") or {})} for m in months]
    if not q.get("split"):
        rows = conn.execute(f"SELECT month, SUM({_METRIC_VALUE[q['metric']]}) AS v FROM v_txn "
                            f"WHERE {where}{' AND ' + ' AND '.join(fwhere) if fwhere else ''} "
                            "GROUP BY month", args).fetchall()
        per = {r["month"]: r["v"] for r in rows}
        return {"source": "spending", "metric": q["metric"], "dimension": "month",
                "unit": "sgd_minor", "labels": months, "label_links": label_links,
                "series": [{"name": q["metric"], "values_minor": [per.get(m, 0) for m in months]}],
                "range": {"from": months[0], "to": months[-1]}}
    expr = _DIM_EXPR[q["split"]]
    rows = conn.execute(f"SELECT month, {expr} AS k, SUM({_METRIC_VALUE[q['metric']]}) AS v FROM v_txn "
                        f"WHERE {where}{' AND ' + ' AND '.join(fwhere) if fwhere else ''} "
                        "GROUP BY month, k", args).fetchall()
    per, totals = {}, {}
    for r in rows:
        per.setdefault(r["k"], {})[r["month"]] = r["v"]
        totals[r["k"]] = totals.get(r["k"], 0) + r["v"]
    ranked = sorted(totals, key=lambda k: -abs(totals[k]))
    fold = q["split"] in ("category", "payee")
    cut = spending.TOP if fold else len(ranked)
    shown, rest = ranked[:cut], ranked[cut:]
    label_of = _dim_label(conn, q["split"])
    filters = _filters_link(q.get("filters") or {})
    flags = {**_METRIC_FLAG[q["metric"]], **filters}
    series = [{"name": label_of(k), "values_minor": [per.get(k, {}).get(m, 0) for m in months],
               "link": _slice_link(flags, q["split"], k, shown)}
              for k in shown]
    if rest:
        folded = [sum(per.get(k, {}).get(m, 0) for k in rest) for m in months]
        series.append({"name": spending.OTHER, "values_minor": folded,
                       "link": _slice_link(flags, q["split"], spending.OTHER, shown)})
    return {"source": "spending", "metric": q["metric"], "dimension": "month",
            "unit": "sgd_minor", "labels": months, "label_links": label_links,
            "series": series, "range": {"from": months[0], "to": months[-1]}}


# ------------------------------------------------------------- the stat results

def _in_range(labels, grain, rng):
    """The labels a `range` of months keeps: month labels compare directly,
    dates by their month, a year by its own four digits."""
    if not rng:
        return labels
    lo, hi = rng.get("from"), rng.get("to")
    if grain == "year":
        lo, hi = (lo or "")[:4] or None, (hi or "")[:4] or None
        return [y for y in labels if (lo is None or y >= lo) and (hi is None or y <= hi)]
    if grain == "period":
        return [d for d in labels if (lo is None or d[:7] >= lo[:7]) and (hi is None or d[:7] <= hi[:7])]
    if grain == "month":
        return [m for m in labels if (lo is None or m >= lo[:7]) and (hi is None or m <= hi[:7])]
    return labels


def _stat_series(results, q):
    """A query on a stat's cached result: one of its list of rows, spread by
    the row's own x field."""
    result = results.get(q["source"])
    if result is None:
        raise NotFound(f"no result for {q['source']!r} yet: run `python3 server/finance.py analyse`")
    spec = _CATALOGUE[q["source"]]["dims"][q["dimension"]]
    rows = (result.get("data") or {}).get(spec["rows"]) or []
    field, unit = spec["metrics"][q["metric"]]
    by_x = {str(r.get(spec["x"])): r for r in rows}
    labels = _in_range(list(by_x), spec["grain"], q.get("range"))
    values = [by_x[y].get(field) for y in labels]
    if unit == "sgd_minor":
        series = {"name": q["metric"], "values_minor": [stat_cents(by_x[y], field[:-6]) for y in labels]}
    else:
        series = {"name": q["metric"], "values": values}
    out = {"source": q["source"], "metric": q["metric"], "dimension": q["dimension"],
           "unit": unit, "labels": labels, "series": [series], "range": q.get("range") or None}
    link = ((result.get("chart") or {}).get("links") or {}).get(field)
    if spec["x"] == "month" and link:
        out["label_links"] = [_stat_link(link, by_x[m], spec["x"]) for m in labels]
    if _CATALOGUE[q["source"]].get("projection"):
        out["note"] = "a projection: what the forward view expects, not a promise"
    return out


def _stat_link(spec, row, x):
    """A stat's own link for one row (its `chart.links` entry, G13): `{x}` is
    the row's `x` field and `{name}` another field of it, as the stat pages
    fill them (hint.ts, barLink). A month is written as its range, from and
    to, the way every other widget link names its month."""
    def fill(value):
        return re.sub(r"\{(\w+)\}", lambda m: str(row.get(x if m[1] == "x" else m[1], m[0])), value)
    link = {k: fill(v) for k, v in spec.items()}
    month = link.pop("month", None)
    return {"from": month, "to": month, **link} if month else link


def _net_worth_series(results, q):
    """Net worth over months (net_worth.py over the stats), or the latest
    value of each part."""
    nw = net_worth.net_worth(results)
    if q["dimension"] == "part":
        labels = nw["parts"]
        # the last row carries every part (each is carried forward from its last measured date)
        latest = [nw["months"][-1][p] if nw["months"] else None for p in labels]
        return {"source": "net-worth", "metric": "value", "dimension": "part",
                "unit": "sgd_minor", "labels": labels,
                "series": [{"name": "net worth", "values_minor": latest}], "range": None}
    rows = nw["months"]
    labels = _in_range([r["month"] for r in rows], "month", q.get("range"))
    by = {r["month"]: r for r in rows}
    return {"source": "net-worth", "metric": q["metric"], "dimension": "month",
            "unit": "sgd_minor", "labels": labels,
            "series": [{"name": q["metric"],
                        "values_minor": [by[m].get(q["metric"]) for m in labels]}],
            "range": q.get("range") or None}


def resolve(conn, results, query):
    """The WidgetAnswer for a WidgetQuery (raw dict; parse_query checks it)."""
    q = parse_query(query)
    if q["source"] == "spending":
        return _spend_series(conn, q)
    if q["source"] == "net-worth":
        return _net_worth_series(results, q)
    return _stat_series(results, q)


# ------------------------------------------------------------ scenario preview

def scenario_preview(conn, results, data):
    """A WidgetQuery's series drawn `months` ahead on its trailing monthly
    average plus `delta_minor` a month: an estimate, nothing written. G26's
    preview is deliberately simple -- the plan's own projection
    (/api/growth/preview) is the detailed one."""
    if not isinstance(data, dict):
        raise DashboardRefused("A scenario preview is an object")
    base = resolve(conn, results, data.get("query") or {})
    if base["dimension"] != "month" or base["unit"] != "sgd_minor":
        raise DashboardRefused("A scenario needs a money series by month")
    months = data.get("months", 6)
    if isinstance(months, bool) or not isinstance(months, int) or not 1 <= months <= 36:
        raise DashboardRefused("months is a whole number from 1 to 36")
    delta = data.get("delta_minor", 0)
    if isinstance(delta, bool) or not isinstance(delta, int):
        raise DashboardRefused("delta_minor is a whole number of cents")
    labels = list(base["labels"])
    last = labels[-1] if labels else datetime.now(timezone.utc).strftime("%Y-%m")
    labels += [add_months(last, i + 1) for i in range(months)]
    series = []
    for s in base["series"]:
        seen = [v for v in s["values_minor"] if v is not None]
        tail = seen[-3:] or [0]
        step = spending.per_month(sum(tail), len(tail)) + delta
        series.append({"name": s["name"], "values_minor": list(s["values_minor"]) + [step] * months})
    return {**base, "labels": labels, "series": series,
            "projected_from": len(base["labels"]), "range": None,
            "note": "a projection: the trailing monthly average plus delta_minor, an estimate only"}


# ------------------------------------------------- the state the dashboard keeps
# JSON documents in the record's `setting` table (app_icon.py's pattern). The
# layouts' and goals' lists are small, edited whole, and owned by the owner.

LAYOUTS_KEY = "dashboard.layouts"
ACTIVE_KEY = "dashboard.active_layout"
GOALS_KEY = "dashboard.goals"
ALLOCATION_KEY = "dashboard.allocation"
GAME_POT_KEY = "dashboard.game_pot"

_ID_SAFE = set("abcdefghijklmnopqrstuvwxyz0123456789-_")


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _doc(conn, key, default):
    row = conn.execute("SELECT value FROM setting WHERE key = ?", (key,)).fetchone() if conn else None
    if row is None:
        return default
    try:
        return json.loads(row["value"])
    except ValueError:
        return default


def _put(conn, key, value):
    with db.transaction(conn):
        conn.execute("INSERT INTO setting(key, value) VALUES (?, ?) "
                     "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                     (key, json.dumps(value)))


def _check_id(value, what):
    if not isinstance(value, str) or not value or len(value) > 64 or set(value) - _ID_SAFE:
        raise DashboardRefused(f"A {what} id is 1-64 lowercase letters, digits, '-' or '_'")
    return value


def _check_month_text(value, name):
    if value is None:
        return None
    if not _is_month(value):
        raise DashboardRefused(f"{name} is YYYY-MM")
    return value


# ---- layouts

def _check_widget(w):
    if not isinstance(w, dict):
        raise DashboardRefused("A widget is an object")
    unknown = sorted(set(w) - {"id", "type", "title", "query", "layout", "options"})
    if unknown:
        raise DashboardRefused(f"A widget's fields are id, type, title, query, layout, options "
                               f"(not {', '.join(unknown)})")
    out = {"id": _check_id(w.get("id"), "widget"),
           "type": w.get("type")}
    if not isinstance(out["type"], str) or not out["type"]:
        raise DashboardRefused("A widget's type names its kind, e.g. 'kpi', 'line', 'pie'")
    if w.get("title") is not None:
        if not isinstance(w["title"], str):
            raise DashboardRefused("A widget's title is text")
        out["title"] = w["title"]
    if w.get("query") is not None:
        out["query"] = parse_query(w["query"])
    if w.get("layout") is not None:
        g = w["layout"]
        if not isinstance(g, dict):
            raise DashboardRefused("A widget's layout is {x, y, w, h}")
        out["layout"] = {}
        for k in ("x", "y", "w", "h"):
            v = g.get(k, 0)
            if isinstance(v, bool) or not isinstance(v, int) or v < 0:
                raise DashboardRefused("A widget's layout x, y, w, h are whole numbers")
            out["layout"][k] = v
    if w.get("options") is not None:
        if not isinstance(w["options"], dict):
            raise DashboardRefused("A widget's options is an object")
        out["options"] = w["options"]
    return out


def _check_layout(d, layout_id=None):
    if not isinstance(d, dict):
        raise DashboardRefused("A layout is an object")
    unknown = sorted(set(d) - {"id", "name", "widgets", "updated_at"})
    if unknown:
        raise DashboardRefused(f"A layout's fields are id, name, widgets (not {', '.join(unknown)})")
    lid = _check_id(layout_id if layout_id is not None else d.get("id"), "layout")
    name = d.get("name")
    if not isinstance(name, str) or not name.strip():
        raise DashboardRefused("A layout's name is text")
    widgets = [_check_widget(w) for w in d.get("widgets") or []]
    seen = set()
    for w in widgets:
        if w["id"] in seen:
            raise DashboardRefused(f"Two widgets named {w['id']!r} in one layout")
        seen.add(w["id"])
    # the server stamps the save: an echoed updated_at is the time of the save
    # it came from, and would freeze the stamp at the layout's first one
    return {"id": lid, "name": name.strip(), "widgets": widgets, "updated_at": _now()}


def _layouts_doc(conn):
    docs = _doc(conn, LAYOUTS_KEY, [])
    return docs if isinstance(docs, list) else []


def layouts(conn):
    return {"layouts": _layouts_doc(conn), "active": _doc(conn, ACTIVE_KEY, None)}


def layout(conn, layout_id):
    for d in _layouts_doc(conn):
        if d["id"] == layout_id:
            return d
    raise NotFound(f"no dashboard layout {layout_id!r}")


def save_layouts(conn, data):
    """Replace the whole list (the layout document, idempotent)."""
    if not isinstance(data, dict):
        raise DashboardRefused("{layouts: [...]}")
    docs = [_check_layout(d) for d in data.get("layouts") or []]
    ids = [d["id"] for d in docs]
    if len(set(ids)) != len(ids):
        raise DashboardRefused("Two layouts share an id")
    active = data.get("active")
    if active is not None and active not in ids:
        raise DashboardRefused("Active names one of the layouts")
    # one transaction: the list and the active id land together, and a save
    # from another tab waits rather than interleaving
    with db.transaction(conn):
        # a layout the list sends back as it was keeps the stamp of its last change
        before = {d["id"]: d for d in _layouts_doc(conn)}
        for d in docs:
            was = before.get(d["id"])
            if was and (was.get("name"), was.get("widgets")) == (d["name"], d["widgets"]):
                d["updated_at"] = was.get("updated_at") or d["updated_at"]
        _put(conn, LAYOUTS_KEY, docs)
        _put(conn, ACTIVE_KEY, active)
    return {"layouts": docs, "active": active}


def save_layout(conn, layout_id, data):
    """Upsert one layout under the id its URL names."""
    if not isinstance(data, dict):
        raise DashboardRefused("A layout is an object")
    doc = _check_layout({k: v for k, v in data.items() if k != "active"}, layout_id)
    # read and write in one transaction, so a save of another layout from
    # another tab cannot land between them and be lost
    with db.transaction(conn):
        docs = [d for d in _layouts_doc(conn) if d["id"] != layout_id]
        docs.append(doc)
        _put(conn, LAYOUTS_KEY, docs)
        if data.get("active"):
            _put(conn, ACTIVE_KEY, layout_id)
    return doc


# ---- goals

def _check_milestones(raw):
    out = []
    for m in raw or []:
        if not isinstance(m, dict) or not _is_month(m.get("month") or "") \
                or isinstance(m.get("target_minor"), bool) or not isinstance(m.get("target_minor"), int):
            raise DashboardRefused("A milestone is {month: YYYY-MM, target_minor: cents}")
        out.append({"month": m["month"], "target_minor": m["target_minor"]})
    return sorted(out, key=lambda m: m["month"])


# What a goal's binding may name: a balance the record measures over time, so
# its last figure is how far the goal has got. A flow (a month's spending, its
# income) has no such figure: its last month is usually still running, and a
# slice (a bucket, a payee) is no amount the goal reaches. A projection's last
# figure is the future, not the present.
GOAL_LEVELS = {
    "net-worth": {"month": ("total", "cash", "investments", "cpf")},
    "savings-runway": {"month": ("cash",)},
    "cpf": {"year": ("total", "oa", "sa", "ma")},
    "portfolio": {"period": ("value_sgd", "value_base")},
}


def _check_goal(g, goal_id=None):
    if not isinstance(g, dict):
        raise DashboardRefused("A goal is an object")
    unknown = sorted(set(g) - {"id", "name", "target_minor", "month", "note", "milestones",
                               "binding", "source", "metric", "dimension",
                               "current_minor", "progress"})   # a read may echo the last two
    if unknown:
        raise DashboardRefused(f"A goal's fields are name, target_minor, month, note, milestones "
                               f"and its binding (not {', '.join(unknown)})")
    name = g.get("name")
    if not isinstance(name, str) or not name.strip():
        raise DashboardRefused("A goal's name is text")
    target = g.get("target_minor")
    if isinstance(target, bool) or not isinstance(target, int) or target <= 0:
        raise DashboardRefused("target_minor is a whole number of cents above 0")
    month = _check_month_text(g.get("month"), "month")
    # the optional progress binding: the widget query whose last figure is the
    # goal's `current` (a money metric -- a goal is an amount). A patch leaves
    # out `source`/`metric` to keep what is saved; `binding` holds them flat
    binding = g.get("binding") or {}
    source = g.get("source", binding.get("source"))
    metric = g.get("metric", binding.get("metric"))
    dimension = g.get("dimension", binding.get("dimension")) or "month"
    if (source is None) != (metric is None):
        raise DashboardRefused("A goal's binding names source and metric together, or neither")
    if source is not None:
        q = parse_query({"source": source, "metric": metric, "dimension": dimension})
        if _CATALOGUE[q["source"]]["dims"][q["dimension"]]["metrics"][q["metric"]][1] != "sgd_minor":
            raise DashboardRefused("A goal's binding is a money metric")
        if q["metric"] not in GOAL_LEVELS.get(q["source"], {}).get(q["dimension"], ()):
            raise DashboardRefused("A goal's binding is a balance over time: "
                                   + "; ".join(f"{s} {', '.join(m)} by {d}"
                                               for s, dims in GOAL_LEVELS.items()
                                               for d, m in dims.items()))
        dimension = q["dimension"]
    out = {"id": goal_id or _check_id(g.get("id"), "goal"), "name": name.strip(),
           "target_minor": target, "month": month, "note": str(g.get("note") or ""),
           "milestones": _check_milestones(g.get("milestones"))}
    if source is not None:
        out["binding"] = {"source": source, "metric": metric, "dimension": dimension}
    return out


def _is_level(binding):
    """True for a binding GOAL_LEVELS names (a goal saved before them may hold another)."""
    b = binding or {}
    return b.get("metric") in GOAL_LEVELS.get(b.get("source"), {}).get(b.get("dimension") or "month", ())


def _without_old_binding(g):
    """A saved goal with a binding GOAL_LEVELS does not name (saved before
    them) read as unbound, so it shows no figure that is not its progress and
    can still be edited; a patch that names a new binding sets one."""
    if not g.get("binding") or _is_level(g["binding"]):
        return g
    return {k: v for k, v in g.items() if k != "binding"}


def _goal_current(conn, results, g):
    """A goal's progress: the last figure its binding resolves to, as cents."""
    b = g.get("binding")
    if not _is_level(b):
        return None
    try:
        answer = resolve(conn, results, {"source": b["source"], "metric": b["metric"],
                                         "dimension": b["dimension"]})
    except (DashboardRefused, NotFound):
        return None
    if not answer["series"]:
        return None
    for v in reversed(answer["series"][0].get("values_minor") or []):
        if v is not None:
            return v
    return None


def goals(conn, results):
    out = []
    for g in _doc(conn, GOALS_KEY, []) if conn else []:
        g = dict(_without_old_binding(g))
        current = _goal_current(conn, results, g)
        g["current_minor"] = current
        g["progress"] = (current / g["target_minor"]
                         if current is not None and g.get("target_minor") else None)
        out.append(g)
    return {"goals": out}


def save_goal(conn, results, data):
    """Create (no `id`) or patch (the fields sent, beside `id`) one goal."""
    if not isinstance(data, dict):
        raise DashboardRefused("A goal is an object")
    with db.transaction(conn):        # read and write together: no save lands between
        _save_goal(conn, data)
    return goals(conn, results)


def _save_goal(conn, data):
    docs = list(_doc(conn, GOALS_KEY, []))
    if data.get("id"):
        for i, g in enumerate(docs):
            if g["id"] == data["id"]:
                docs[i] = _check_goal({**_without_old_binding(g), **{k: v for k, v in data.items() if k != "id"}},
                                      g["id"])
                break
        else:
            raise DashboardRefused(f"No goal {data['id']!r}")
    else:
        n = 1 + max((int(g["id"].split("-")[1]) for g in docs
                     if g.get("id", "").startswith("g-") and g["id"].split("-")[1].isdigit()),
                    default=0)
        docs.append(_check_goal(data, f"g-{n}"))
    _put(conn, GOALS_KEY, docs)


def remove_goal(conn, results, data):
    if not isinstance(data, dict):
        raise DashboardRefused("{id} names the goal to drop")
    with db.transaction(conn):
        docs = _doc(conn, GOALS_KEY, [])
        keep = [g for g in docs if g["id"] != data.get("id")]
        if len(keep) == len(docs):
            raise DashboardRefused(f"No goal {data.get('id')!r}")
        _put(conn, GOALS_KEY, keep)
    return goals(conn, results)


# ---- the allocation template and a month's actuals

def allocation(conn, month=None):
    """The saved template beside one month's actuals: income in, each spend
    bucket's planned cents and what the month really spent in it."""
    template = _doc(conn, ALLOCATION_KEY, {"buckets": []})
    if not isinstance(template, dict) or not isinstance(template.get("buckets"), list):
        template = {"buckets": []}
    months, income, actual = [], 0, {}
    groups = []
    month = _check_month_text(month, "month")
    if conn is not None:
        first, last = conn.execute("SELECT MIN(month), MAX(month) FROM v_txn").fetchone()
        months = net_worth.months_between(first, last) if first else []
        month = month or last
        if month:
            income = conn.execute("SELECT COALESCE(SUM(amount_sgd_minor), 0) FROM v_txn WHERE "
                                  + spending.INCOME_WHERE + " AND month = ?", (month,)).fetchone()[0]
            actual = {r["bucket"]: r["v"] for r in conn.execute(
                "SELECT COALESCE(category_group, 'Other') AS bucket, "
                "SUM(-amount_sgd_minor) AS v FROM v_txn WHERE " + spending.SPEND_WHERE
                + " AND month = ? GROUP BY bucket", (month,))}
        groups = [r["name"] for r in conn.execute(
            "SELECT name FROM category_group WHERE kind = 'spend' "
            "ORDER BY COALESCE(sort_order, 1e9), name")]
    planned = {b["bucket"]: b["planned_minor"] for b in template["buckets"]}
    buckets = [{"bucket": g, "planned_minor": planned.get(g),
                "actual_minor": actual.get(g, 0),
                "share": (actual.get(g, 0) / income) if income else None}
               for g in groups]
    for b in template["buckets"]:
        if b["bucket"] not in groups:
            buckets.append({"bucket": b["bucket"], "planned_minor": b["planned_minor"],
                            "actual_minor": actual.get(b["bucket"], 0), "share": None})
    allocated = sum(p for p in planned.values() if p is not None)
    return {"month": month, "months": months, "income_minor": income,
            "template": template["buckets"], "buckets": buckets,
            "unallocated_minor": (income - allocated) if template["buckets"] and income else None}


def save_allocation(conn, data):
    """The template: each spend bucket's planned cents a month."""
    if not isinstance(data, dict) or not isinstance(data.get("buckets"), list):
        raise DashboardRefused("{buckets: [{bucket, planned_minor}]}")
    groups = {r["name"] for r in conn.execute("SELECT name FROM category_group WHERE kind = 'spend'")}
    out = []
    for b in data["buckets"]:
        if not isinstance(b, dict) or b.get("bucket") not in groups:
            raise DashboardRefused("A template row is a spend bucket the record holds")
        p = b.get("planned_minor")
        if isinstance(p, bool) or not isinstance(p, int) or p < 0:
            raise DashboardRefused("planned_minor is a whole number of cents")
        out.append({"bucket": b["bucket"], "planned_minor": p})
    _put(conn, ALLOCATION_KEY, {"buckets": out})
    return allocation(conn, data.get("month"))


# ---- the game pot's settings

def game_pot(conn):
    saved = _doc(conn, GAME_POT_KEY, None)
    doc = {"name": "", "benchmark": "", "note": "", "account_ids": []}
    accounts = []
    if conn is not None:
        accounts = [{"id": r["id"], "name": r["name"]} for r in
                    conn.execute("SELECT id, name FROM account ORDER BY name")]
    if isinstance(saved, dict):
        doc.update({k: saved.get(k, v) for k, v in doc.items()})
    return {**doc, "accounts": accounts, "on": saved is not None}


def save_game_pot(conn, data):
    """The pot's metadata: a name, the benchmark it plays against, and the
    accounts it links. The tracking itself comes with its own issue."""
    if not isinstance(data, dict):
        raise DashboardRefused("{name, benchmark?, note?, account_ids?}")
    name, note = data.get("name"), data.get("note", "")
    benchmark = data.get("benchmark", "")
    if not isinstance(name, str) or not name.strip():
        raise DashboardRefused("The game pot's name is text")
    if not isinstance(benchmark, str) or not isinstance(note, str):
        raise DashboardRefused("Benchmark and note are text")
    ids = data.get("account_ids") or []
    known = {r["id"] for r in conn.execute("SELECT id FROM account")}
    if not isinstance(ids, list) or any(isinstance(i, bool) or not isinstance(i, int) for i in ids) \
            or set(ids) - known:
        raise DashboardRefused("account_ids are ids of the record's accounts")
    ids = list(dict.fromkeys(ids))
    _put(conn, GAME_POT_KEY, {"name": name.strip(), "benchmark": benchmark,
                              "note": note, "account_ids": ids})
    return game_pot(conn)
