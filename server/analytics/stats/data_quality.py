#!/usr/bin/env python3
"""Everything a chart above could be quietly wrong about.

Five things break an analysis of this data, and each one is counted here rather
than left to be discovered: rows with no category, rows with no payee, the
synthetic YouTrip fillers, non-SGD rows whose period block prints no FX rate,
and months whose statements do not cover every bank account (wallets never count).

The `fix_list` at the end is the ad hoc queue: uncategorised External rows
sorted by size, largest first, because that is the order in which fixing one
changes a number.

G20: every amount is integer cents, from the rows' `amount_sgd_minor`.

data:
  counts     {rows, external, uncategorised, uncategorised_out_minor,
              uncategorised_in_minor, no_payee, synthetic, unconverted,
              partial_months, transfer_bucket_external, transfer_bucket_out_minor,
              spend_base_minor, spend_base_label}
  fix_list   [{date, amount_sgd_minor, currency, account, bank_txn_type, merchant_raw,
               counterparty, memo, reason, key}]
  coverage   [{month, accounts, expected, missing, card_only, partial}]
  Every account here is named by its label (common.account_labels), and a
  period by that label and its currency and month: never a full number.
  unconverted [{period, currency, rows, amount_minor}]
  synthetic  [{date, account, currency, amount_sgd_minor, flow_class, memo}]
  categories [{category, rows}]        rows the rules never reached, by bucket
"""
import sys

from . import common as c

ID = "data-quality"
TITLE = "Data quality"
CHART = {"type": "bar", "x": "month", "series": ["uncategorised", "synthetic"],
         "source": "by_month", "unit": "count"}

# G13: each bar opens the rows it counts. Uncategorised is the External rows with
# no category, stand-in rows included; synthetic is every made-up row, the
# wallet's legs between own accounts among them, so it asks for transfers too.
LINKS = {"uncategorised": {"month": "{x}", "external": "1", "category": "(none)"},
         "synthetic": {"month": "{x}", "synthetic": "1", "hide_transfers": "0"}}

FIX_LIMIT = 60


def run(snapshot, benchmarks=None):
    rows = snapshot["transactions"]
    ext = c.external(snapshot)
    uncategorised = [r for r in ext if not r["category"]]
    no_payee = [r for r in ext if not r["payee"]]
    synthetic = [r for r in rows if r["synthetic"]]

    labels = c.account_labels(snapshot)
    rated = {b["name"] for b in snapshot["periods"] if b["fx_to_sgd"]}
    unconverted_rows = [r for r in rows if r["currency"] != "SGD"
                        and r["period"] not in rated]
    unconverted = []
    for period, prows in sorted(c.by(unconverted_rows, lambda r: r["period"]).items()):
        unconverted.append({"period": labels.period(period), "currency": prows[0]["currency"],
                            "rows": len(prows),
                            "amount_minor": c.sum_cents(abs(c.sgd_minor(r)) for r in prows)})

    cov = c.coverage(snapshot)
    coverage_rows = [{"month": m, **cov[m],
                      **{k: sorted(labels.of(a) for a in cov[m][k])
                         for k in ("accounts", "expected", "missing")}}
                     for m in c.month_range(snapshot)]
    partial = [x for x in coverage_rows if x["partial"]]

    transfer_ext = [r for r in ext if r["bucket"] == "Transfer"]
    base, base_label = c.spend_base_minor(snapshot, months=c.month_range(snapshot))

    fix = []
    for r in sorted(uncategorised, key=lambda r: -abs(c.sgd_minor(r))):
        fix.append({"date": r["date"], "amount_sgd_minor": c.sgd_minor(r),
                    "currency": r["currency"],
                    "account": labels.of(r.get("account_no"), r["account"]),
                    "bank_txn_type": r["bank_txn_type"],
                    "merchant_raw": r["merchant_raw"],
                    "counterparty": r["counterparty"], "memo": r["memo"],
                    "key": r["key"],
                    "reason": ("synthetic balance jump" if r["synthetic"] else
                               "no merchant text printed" if not r["merchant_raw"]
                               and not r["counterparty"] else
                               "no rule matches")})

    by_month = []
    for m in c.month_range(snapshot):
        by_month.append({"month": m,
                         "rows": sum(1 for r in rows if r["month"] == m),
                         "uncategorised": sum(1 for r in uncategorised
                                              if r["month"] == m),
                         "synthetic": sum(1 for r in synthetic if r["month"] == m),
                         "partial": cov[m]["partial"]})

    reasons = {}
    for x in fix:
        reasons[x["reason"]] = reasons.get(x["reason"], 0) + 1

    counts = {"rows": len(rows), "external": len(ext),
              "uncategorised": len(uncategorised),
              "uncategorised_out_minor": c.spend_minor([r for r in uncategorised
                                                         if c.sgd_minor(r) < 0]),
              "uncategorised_in_minor": c.income_minor([r for r in uncategorised
                                                        if c.sgd_minor(r) > 0]),
              "no_payee": len(no_payee), "synthetic": len(synthetic),
              "unconverted": len(unconverted_rows),
              "partial_months": len(partial),
              "transfer_bucket_external": len(transfer_ext),
              "transfer_bucket_out_minor": c.spend_minor(transfer_ext),
              "spend_base_minor": base, "spend_base_label": base_label}

    findings = [
        f"{counts['uncategorised']} of {counts['external']} External rows have no "
        f"category: {c.label(counts['uncategorised_out_minor'])} out and "
        f"{c.label(counts['uncategorised_in_minor'])} in, "
        f"{c.share(counts['uncategorised_out_minor'], base) * 100:.1f} "
        f"per cent {base_label}.",
        f"{counts['synthetic']} synthetic YouTrip rows stand in for balance jumps "
        f"the export omitted; every spend and income figure excludes them.",
    ]
    if unconverted:
        findings.append(
            f"{counts['unconverted']} non-SGD row(s) sit on {len(unconverted)} period "
            f"block(s) with no FX rate, so "
            f"{'they carry no SGD value and are left out of every total' if all(r.get('amount_sgd_minor') is None for r in unconverted_rows) else 'they are counted at face value'}: "
            f"{', '.join(x['period'] for x in unconverted[:3])}.")
    else:
        findings.append("Every non-SGD row sits on a period block that prints an FX "
                        "rate, so nothing is counted at face value.")
    if partial:
        findings.append(
            f"{len(partial)} month(s) have incomplete statement coverage, "
            f"{c.range_label(partial[0]['month'], partial[-1]['month'])}; every average in the "
            f"other stats leaves them out.")
    findings.append(
        f"{counts['transfer_bucket_external']} External rows are in a Transfer "
        f"bucket, {c.label(counts['transfer_bucket_out_minor'])} out. A view that "
        f"filters External and forgets the bucket counts all of it as spending.")
    if reasons:
        findings.append("The fix queue splits as " + ", ".join(
            f"{v} {k}" for k, v in sorted(reasons.items(), key=lambda kv: -kv[1])) + ".")

    caveats = [
        "A row with no merchant text printed can only be fixed by a key rule, which "
        "pins that one row; the Rules page suggests one for each such row.",
        "Synthetic rows change whenever a YouTrip month is re-exported, so their "
        "count is not stable across re-exports.",
        "Coverage is judged per account from its own first and last statement, so an "
        "account that simply had no activity looks the same as one whose statement "
        "is missing.",
    ]
    return c.result(sys.modules[__name__], snapshot,
                    [c.source("Transactions", len(rows), "all"),
                     c.source("Period balances", len(snapshot["periods"]), "all")],
                    {"fix_limit": FIX_LIMIT, "currency": "SGD"},
                    {"counts": counts, "fix_list": fix[:FIX_LIMIT],
                     "fix_list_total": len(fix), "coverage": coverage_rows,
                     "unconverted": unconverted, "by_month": by_month,
                     "reasons": reasons,
                     "synthetic": [{"date": r["date"],
                                    "account": labels.of(r.get("account_no"), r["account"]),
                                    "currency": r["currency"],
                                    "amount_sgd_minor": c.sgd_minor(r),
                                    "flow_class": r["flow_class"], "memo": r["memo"]}
                                   for r in synthetic]},
                    findings, caveats, links=LINKS)
