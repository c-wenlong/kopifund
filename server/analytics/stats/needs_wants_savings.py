#!/usr/bin/env python3
"""How each month's income splits into Need, Want, Savings and Transfer.

The 50/30/20 rule is half of take-home to needs, 30 per cent to wants and 20
per cent to savings. It is a household rule, and a record whose housing or food
is not billed to these accounts is not that household, so the split is read
descriptively: the useful number is wants against needs, not the distance to 50.

data:
  months  [{month, income_minor, need_minor, want_minor, savings_minor, transfer_minor,
            unclassified_minor, need_share, want_share, savings_share, partial}]
  summary {months, income_minor, need_minor, want_minor, savings_minor, transfer_minor,
           unclassified_minor, need_per_month_minor, want_per_month_minor,
           savings_per_month_minor, income_per_month_minor,
           need_share, want_share, savings_share, targets, gaps, want_to_need}

G20: money is integer cents under `<name>_minor`; the shares, the targets and
the gaps stay fractions, and `want_to_need` a ratio.
"""
import sys

from . import common as c

ID = "needs-wants-savings"
TITLE = "Needs, wants and savings"
CHART = {"type": "stacked", "x": "month", "series": ["need_minor", "want_minor", "savings_minor"],
         "source": "months", "unit": "sgd_minor"}

TARGETS = {"need": 0.50, "want": 0.30, "savings": 0.20}

# G13: each bar opens the rows it adds up. Needs and wants are spending rows by
# their category's need class; savings are the Investment and Savings transfers,
# which Transactions hides unless told not to. Income is not drawn, but a
# dashboard widget plots it (G26, server/finance/dashboard.py reads these links).
LINKS = {"income_minor": {"month": "{x}", "income": "1"},
         "need_minor": {"month": "{x}", "spend": "1", "need": "Need"},
         "want_minor": {"month": "{x}", "spend": "1", "need": "Want"},
         "savings_minor": {"month": "{x}", "transfer": "1", "need": "Investment,Savings", "hide_transfers": "0"}}

MONEY = ("income", "need", "want", "savings", "transfer", "unclassified")


def run(snapshot, benchmarks=None):
    cov = c.coverage(snapshot)
    months = c.month_range(snapshot)
    spend = c.spend_rows(snapshot)
    inc = c.income_rows(snapshot)
    save = [r for r in c.transfer_rows(snapshot)
            if r["need_class"] in ("Investment", "Savings")]
    other_transfer = [r for r in c.transfer_rows(snapshot)
                      if r["need_class"] not in ("Investment", "Savings")]

    def month_sum(rows, m):
        return c.spend_minor([r for r in rows if r["month"] == m])

    rows_out = []
    for m in months:
        income = c.income_minor([r for r in inc if r["month"] == m])
        need = month_sum([r for r in spend if r["need_class"] == "Need"], m)
        want = month_sum([r for r in spend if r["need_class"] == "Want"], m)
        unclassified = month_sum([r for r in spend if r["need_class"] not in
                                  ("Need", "Want")], m)
        savings = month_sum(save, m)
        rows_out.append({"month": m, "income_minor": income, "need_minor": need, "want_minor": want,
                         "savings_minor": savings,
                         "transfer_minor": month_sum(other_transfer, m),
                         "unclassified_minor": unclassified,
                         "need_share": c.share(need, income),
                         "want_share": c.share(want, income),
                         "savings_share": c.share(savings, income),
                         "partial": cov[m]["partial"]})

    counted = [x for x in rows_out if not x["partial"]]
    tot = {k: c.sum_cents(x[f"{k}_minor"] for x in counted) for k in MONEY}
    summary = {"months": len(counted), **{f"{k}_minor": v for k, v in tot.items()},
               "need_per_month_minor": c.div_cents(tot["need"], len(counted)),
               "want_per_month_minor": c.div_cents(tot["want"], len(counted)),
               "savings_per_month_minor": c.div_cents(tot["savings"], len(counted)),
               "income_per_month_minor": c.div_cents(tot["income"], len(counted)),
               "need_share": c.share(tot["need"], tot["income"]),
               "want_share": c.share(tot["want"], tot["income"]),
               "savings_share": c.share(tot["savings"], tot["income"]),
               "targets": TARGETS}
    summary["gaps"] = {k: round(summary[f"{k}_share"] - v, 4)
                       for k, v in TARGETS.items()}
    summary["want_to_need"] = (round(tot["want"] / tot["need"], 2)
                               if tot["need"] else None)

    findings = [
        f"Over {summary['months']} covered months the split of income is "
        f"{summary['need_share'] * 100:.0f} per cent needs, "
        f"{summary['want_share'] * 100:.0f} per cent wants and "
        f"{summary['savings_share'] * 100:.0f} per cent savings, against the "
        f"50/30/20 rule.",
    ]
    if summary["want_to_need"]:
        findings.append(
            f"Wants run {c.label(summary['want_per_month_minor'])} a month against "
            f"{c.label(summary['need_per_month_minor'])} of billed needs, a ratio of "
            f"{summary['want_to_need']} to 1.")
    over = [k for k, v in summary["gaps"].items() if v > 0.05]
    under = [k for k, v in summary["gaps"].items() if v < -0.05]
    if over:
        findings.append("Above the rule: " + ", ".join(sorted(over)) + ".")
    if under:
        findings.append("Below the rule: " + ", ".join(sorted(under)) + ".")

    caveats = [
        "The three shares are of income and do not add to one: what is left is what "
        "stayed in the bank. Only the share of spending is a split of what was "
        "spent, and it is the larger of the two.",
        f"`unclassified_minor` is {c.label(summary['unclassified_minor'])}: spending in a "
        f"category with no Need class. Unidentified is deliberately one of those, "
        f"because a row whose statement printed no text says nothing about "
        f"whether it was a need or a want.",
        "Shares are of income, not of take-home pay: the record has no payslip in "
        "it, so gross and net cannot be told apart.",
        "Needs read low wherever housing or food is not billed to these accounts, "
        "and a budget with no housing line is not a 50/30/20 household.",
        "Savings is the Investment and Savings need classes on Transfer-bucket "
        "categories, the regular investing transfer; money simply left in the bank "
        "is not counted as saved.",
    ]
    used = len(spend) + len(inc) + len(save) + len(other_transfer)
    return c.result(sys.modules[__name__], snapshot,
                    [c.source("Transactions", used,
                              "flow_class = External, split by the category's "
                              "Need class")],
                    {"from": months[0] if months else None,
                     "to": months[-1] if months else None,
                     "rule": "50/30/20", "currency": "SGD"},
                    {"months": rows_out, "summary": summary},
                    findings, caveats, links=LINKS)
