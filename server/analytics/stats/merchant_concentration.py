#!/usr/bin/env python3
"""Who the money actually goes to, and how few of them it takes.

The Pareto count is the answer to "how many payees make 80 per cent of my
spending". A small number means a handful of decisions move the total; a large
one means the spending is diffuse and no single cut helps.

data:
  top_by_spend  [{rank, payee, category, rows, spend_minor, share, share_clamped,
                  cumulative_share, first_seen, last_seen}]
  top_by_count  the same rows ordered by row count
  pareto        {payees, total_minor, base_label, p50, p80, p90, share_top10}

G20: money is integer cents under `<name>_minor`; the shares stay fractions.
"""
import sys

from . import common as c

ID = "merchant-concentration"
TITLE = "Merchant concentration"
CHART = {"type": "bar", "x": "payee", "series": ["spend_minor"], "source": "top_by_spend",
         "unit": "sgd_minor"}

# G13: a bar opens the spending rows of that payee, named as the explorer names it
LINKS = {"spend_minor": {"payee": "{x}", "spend": "1"}}

TOP_N = 25


def run(snapshot, benchmarks=None):
    rows = c.spend_rows(snapshot)
    total, base_label = c.spend_base_minor(snapshot, rows=rows,
                                           months=c.month_range(snapshot))
    payees = []
    # a payee is the name the rules gave, else what the statement printed, else
    # its counterparty: the explorer and Transactions' payee= filter name it so
    for payee, prows in c.by(rows, lambda r: r["payee"] or r["merchant_raw"]
                             or r["counterparty"] or "(no name)").items():
        cats = c.by(prows, lambda r: r["category"])
        first, last = c.date_span(prows)
        payees.append({"payee": payee,
                       "category": max(cats, key=lambda k: len(cats[k])),
                       "rows": len(prows), "spend_minor": c.spend_minor(prows),
                       "first_seen": first, "last_seen": last})
    payees.sort(key=lambda p: -p["spend_minor"])

    cum = 0
    for i, p in enumerate(payees, 1):
        cum += p["spend_minor"]
        p["rank"] = i
        p["share"] = c.share(p["spend_minor"], total)
        p["share_clamped"] = c.clamp_share(p["share"])
        p["cumulative_share"] = c.share(cum, total)

    def cut(threshold):
        for p in payees:
            if p["cumulative_share"] >= threshold:
                return p["rank"]
        return len(payees)

    pareto = {"payees": len(payees), "total_minor": total, "base_label": base_label,
              "p50": cut(0.5), "p80": cut(0.8), "p90": cut(0.9),
              "share_top10": c.share(c.sum_cents(p["spend_minor"] for p in payees[:10]), total)}

    by_count = sorted(payees, key=lambda p: (-p["rows"], -p["spend_minor"]))[:TOP_N]

    findings = []
    if payees:
        findings.append(
            f"{pareto['p80']} payees of {pareto['payees']} make 80 per cent "
            f"{base_label}; the top ten alone are "
            f"{pareto['share_top10'] * 100:.0f} per cent.")
        top = payees[0]
        findings.append(f"The largest is {top['payee']} ({top['category']}) at "
                        f"{c.label(top['spend_minor'])} over {top['rows']} rows, "
                        f"{top['share'] * 100:.0f} per cent {base_label}.")
        busiest = by_count[0]
        findings.append(f"The most frequent is {busiest['payee']} with "
                        f"{busiest['rows']} rows totalling "
                        f"{c.label(busiest['spend_minor'])}.")
        findings.append(f"Half of that base goes to just {pareto['p50']} payee(s).")

    caveats = [
        f"Every share here is {base_label}: the payee totals span every month in "
        f"the record, so the base does too.",
        "A payee only exists where a rule names it; unmatched rows fall back to "
        "their raw merchant string and so look more fragmented than they are.",
        "Transfer-bucket payees, including people paid back or sent money, "
        "are not here: they are money moved, not money spent.",
        snapshot["meta"]["fx_note"],
    ]
    return c.result(sys.modules[__name__], snapshot,
                    [c.source("Transactions", len(rows), c.SPEND_FILTER)],
                    {"top_n": TOP_N, "currency": "SGD"},
                    {"top_by_spend": payees[:TOP_N], "top_by_count": by_count,
                     "pareto": pareto},
                    findings, caveats, links=LINKS)
