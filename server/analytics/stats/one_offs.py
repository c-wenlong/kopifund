#!/usr/bin/env python3
"""The single charges big enough to move a month on their own.

A one-off is measured against its own category's median charge rather than
against a flat figure, so a 300 dollar flight is ordinary and a 300 dollar
coffee is not. Refunds are excluded from the medians and listed separately: a
large credit is worth seeing, but it is not an expense.

G20: every amount is integer cents from the rows' `amount_sgd_minor`; a
median of an even count is the mean of the two middle charges, half up
(`common.median_cents`).

data:
  one_offs   [{date, month, payee, category, amount_minor, multiple, median_minor,
               merchant_raw, memo, trip, account}]; account is the label
               (common.account_labels), never a full number
  by_month   [{month, count, spend_minor, month_spend_minor, share_of_month,
               share_of_month_clamped}]
  refunds    [{date, payee, category, amount_minor, memo}]
  medians    [{category, median_minor, charges}]
  summary    {threshold, floor_minor, count, spend_minor, share_of_spend,
              base_minor, base_label}
"""
import sys

from . import common as c

ID = "one-offs"
TITLE = "One-off charges"
# A scatter of size against how far above the category's median it sits: the
# outliers are the point, and a table hides them among their neighbours.
CHART = {"type": "scatter", "x": "multiple", "series": ["amount_minor"],
         "source": "one_offs", "label": "payee", "unit": "sgd_minor"}

THRESHOLD = 5.0        # times the category median
FLOOR = 15000          # and at least S$150.00 (in cents), so a 5x coffee is not news
REFUND_FLOOR = 10000   # S$100.00


def run(snapshot, benchmarks=None):
    rows = c.spend_rows(snapshot)
    labels = c.account_labels(snapshot)
    charges = [r for r in rows if c.sgd_minor(r) < 0]
    medians = {}
    for cat, crows in c.by(charges, lambda r: r["category"]).items():
        medians[cat] = {"category": cat,
                        "median_minor": c.median_cents(-c.sgd_minor(r) for r in crows),
                        "charges": len(crows)}

    one_offs = []
    for r in charges:
        med = medians[r["category"]]["median_minor"]
        amount = -c.sgd_minor(r)
        if med <= 0 or amount < FLOOR or amount < med * THRESHOLD:
            continue
        one_offs.append({"date": r["date"], "month": r["month"],
                         "payee": r["payee"] or r["merchant_raw"] or "(no name)",
                         "category": r["category"], "amount_minor": amount,
                         "median_minor": med, "multiple": round(amount / med, 1),
                         "merchant_raw": r["merchant_raw"], "memo": r["memo"],
                         "trip": r["trip"],
                         "account": labels.of(r.get("account_no"), r["account"])})
    one_offs.sort(key=lambda x: -x["amount_minor"])

    by_month = []
    for m, mrows in sorted(c.by(one_offs, lambda x: x["month"]).items()):
        month_spend = c.spend_minor([r for r in rows if r["month"] == m])
        total = c.sum_cents(x["amount_minor"] for x in mrows)
        by_month.append({"month": m, "count": len(mrows), "spend_minor": total,
                         "month_spend_minor": month_spend,
                         "share_of_month": c.share(total, month_spend),
                         "share_of_month_clamped":
                             c.clamp_share(c.share(total, month_spend))})

    refunds = sorted(({"date": r["date"], "payee": r["payee"] or r["merchant_raw"],
                       "category": r["category"], "amount_minor": c.sgd_minor(r),
                       "memo": r["memo"]}
                      for r in rows if c.sgd_minor(r) >= REFUND_FLOOR),
                     key=lambda x: -x["amount_minor"])

    total = c.sum_cents(x["amount_minor"] for x in one_offs)
    all_spend, base_label = c.spend_base_minor(snapshot, rows=rows,
                                               months=c.month_range(snapshot))
    summary = {"threshold": THRESHOLD, "floor_minor": FLOOR, "count": len(one_offs),
               "spend_minor": total, "share_of_spend": c.share(total, all_spend),
               "base_minor": all_spend, "base_label": base_label}

    findings = [
        f"{len(one_offs)} charges are at least {THRESHOLD:.0f} times their "
        f"category's median and above {c.label(FLOOR)}; together they are "
        f"{c.label(total)}, {summary['share_of_spend'] * 100:.0f} per cent "
        f"{base_label}.",
    ]
    if one_offs:
        top = one_offs[0]
        findings.append(f"The largest is {c.label(top['amount_minor'])} to {top['payee']} "
                        f"on {c.date_label(top['date'])} ({top['category']}), "
                        f"{top['multiple']} times that category's median.")
        cats = c.by(one_offs, lambda x: x["category"])
        worst = max(cats, key=lambda k: c.sum_cents(x["amount_minor"] for x in cats[k]))
        findings.append(f"{worst} carries the most one-off money: "
                        f"{c.label(c.sum_cents(x['amount_minor'] for x in cats[worst]))} over "
                        f"{len(cats[worst])} charges.")
    if by_month:
        heavy = max(by_month, key=lambda x: x["share_of_month"])
        # A share above one is not a bug: the month's other charges were more
        # than cancelled by a refund, so one charge is larger than the net.
        findings.append(
            f"{c.month_label(heavy['month'])} was the most one-off driven month: "
            f"{c.label(heavy['spend_minor'])} of outliers against a net "
            f"{c.label(heavy['month_spend_minor'])} of spending, "
            + ("more than the whole month, because a refund cancelled the rest."
               if heavy["share_of_month"] > 1 else
               f"{heavy['share_of_month'] * 100:.0f} per cent of it."))
    if refunds:
        findings.append(f"{len(refunds)} credits of {c.label(REFUND_FLOOR)} or more "
                        f"landed inside spend categories, the largest being "
                        f"{c.label(refunds[0]['amount_minor'])} from {refunds[0]['payee']}; "
                        f"they reduce their month rather than adding to it.")

    caveats = [
        f"The share above is {base_label}, every month in the record rather than "
        f"the covered ones, because these charges span the whole of it.",
        "The median is taken over charges only, so a category with many refunds "
        "still has a sensible baseline.",
        "A large payment that is genuinely annual (a flight, a deposit) looks like "
        "an outlier here; the recurring stat is where a yearly cadence shows up.",
        "Transfer-bucket outflows are not here, so money sent to people does not "
        "appear however large a single payment is.",
    ]
    caveats.extend(note for note in
                   (c.share_note_minor(x["spend_minor"], x["month_spend_minor"],
                                       f"The one-off share of {c.month_label(x['month'])}")
                    for x in by_month) if note)
    return c.result(sys.modules[__name__], snapshot,
                    [c.source("Transactions", len(charges),
                              f"{c.SPEND_FILTER} and amount_sgd < 0")],
                    {"threshold_multiple": THRESHOLD, "floor_sgd_minor": FLOOR,
                     "refund_floor_sgd_minor": REFUND_FLOOR, "currency": "SGD"},
                    {"one_offs": one_offs, "by_month": by_month, "refunds": refunds,
                     "medians": sorted(medians.values(), key=lambda x: -x["median_minor"]),
                     "summary": summary},
                    findings, caveats)
