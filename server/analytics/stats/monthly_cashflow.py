#!/usr/bin/env python3
"""Income, spend, net and investing per month, plus the two phase averages.

G20: every amount is integer cents, added from the rows' `amount_sgd_minor`
and averaged over a phase's months with `common.div_cents` (half up).

data:
  months  [{month, income_minor, spend_minor, net_minor, investing_minor,
            net_after_investing_minor, transfers_out_minor, partial}]
  phases  [{name, from, to, months, income_minor, spend_minor, net_minor,
            investing_minor, income_per_month_minor, spend_per_month_minor,
            net_per_month_minor, investing_per_month_minor,
            net_after_investing_per_month_minor}]
  totals  {income_minor, spend_minor, net_minor, investing_minor, months}
"""
import sys

from . import common as c

ID = "monthly-cashflow"
TITLE = "Monthly cash flow"
CHART = {"type": "bar", "x": "month", "series": ["income_minor", "spend_minor", "net_minor"],
         "source": "months", "unit": "sgd_minor"}

# G13: each bar opens the rows it adds up. Net is income less spending, so its
# rows are the income rows and the spending rows together. Investing is not
# drawn, but a dashboard widget plots it (G26, server/finance/dashboard.py
# reads these links): the Investment transfers, which Transactions hides
# unless told not to.
LINKS = {"income_minor": {"month": "{x}", "income": "1"},
         "spend_minor": {"month": "{x}", "spend": "1"},
         "net_minor": {"month": "{x}", "income": "1", "spend": "1"},
         "investing_minor": {"month": "{x}", "transfer": "1", "need": "Investment", "hide_transfers": "0"}}


def run(snapshot, benchmarks=None):
    cov = c.coverage(snapshot)
    inc = c.by(c.income_rows(snapshot), lambda r: r["month"])
    spd = c.by(c.spend_rows(snapshot), lambda r: r["month"])
    inv = c.by(c.investing_rows(snapshot), lambda r: r["month"])
    tra = c.by(c.transfer_rows(snapshot), lambda r: r["month"])

    months = []
    for m in c.month_range(snapshot):
        income = c.income_minor(inc.get(m, []))
        spend = c.spend_minor(spd.get(m, []))
        investing = c.spend_minor(inv.get(m, []))
        months.append({"month": m, "income_minor": income, "spend_minor": spend,
                       "net_minor": income - spend, "investing_minor": investing,
                       "net_after_investing_minor": income - spend - investing,
                       "transfers_out_minor": c.spend_minor(tra.get(m, [])),
                       "partial": cov[m]["partial"]})

    phases = []
    for p in c.phases():
        rows = [x for x in months if c.in_phase(x["month"], p)]
        if not rows:
            continue
        n = len(rows)
        income = c.sum_cents(x["income_minor"] for x in rows)
        spend = c.sum_cents(x["spend_minor"] for x in rows)
        investing = c.sum_cents(x["investing_minor"] for x in rows)
        phases.append({"name": p["name"], "from": rows[0]["month"],
                       "to": rows[-1]["month"], "months": n,
                       "income_minor": income, "spend_minor": spend,
                       "net_minor": income - spend, "investing_minor": investing,
                       "income_per_month_minor": c.div_cents(income, n),
                       "spend_per_month_minor": c.div_cents(spend, n),
                       "net_per_month_minor": c.div_cents(income - spend, n),
                       "investing_per_month_minor": c.div_cents(investing, n),
                       "net_after_investing_per_month_minor":
                           c.div_cents(income - spend - investing, n)})

    counted = [x for x in months if not x["partial"]]
    totals = {"income_minor": c.sum_cents(x["income_minor"] for x in counted),
              "spend_minor": c.sum_cents(x["spend_minor"] for x in counted),
              "investing_minor": c.sum_cents(x["investing_minor"] for x in counted),
              "months": len(counted)}
    totals["net_minor"] = totals["income_minor"] - totals["spend_minor"]

    findings = []
    if phases:
        last = phases[-1]
        findings.append(
            f"{last['name']} ({c.range_label(last['from'], last['to'])}, {last['months']} months) "
            f"averages {c.label(last['income_per_month_minor'])} in, "
            f"{c.label(last['spend_per_month_minor'])} out and "
            f"{c.label(last['investing_per_month_minor'])} invested, leaving "
            f"{c.label(last['net_after_investing_per_month_minor'])} a month.")
    if len(phases) == 2:
        a, b = phases
        findings.append(
            f"Spending went from {c.label(a['spend_per_month_minor'])} a month in "
            f"{a['name']} to {c.label(b['spend_per_month_minor'])} in {b['name']}, income "
            f"from {c.label(a['income_per_month_minor'])} to "
            f"{c.label(b['income_per_month_minor'])}.")
    if counted:
        worst = min(counted, key=lambda x: x["net_minor"])
        best = max(counted, key=lambda x: x["net_minor"])
        findings.append(f"The worst month was {c.month_label(worst['month'])} at "
                        f"{c.label(worst['net_minor'])} net; the best was "
                        f"{c.month_label(best['month'])} at {c.label(best['net_minor'])}.")
    negative = [x for x in counted if x["net_after_investing_minor"] < 0]
    findings.append(f"{len(negative)} of {len(counted)} fully covered months ended "
                    f"below zero once the investing transfer is counted.")

    partial = [x["month"] for x in months if x["partial"]]
    caveats = [
        "Spend is a signed sum, so a refund landing in a spend category reduces "
        "that month rather than adding to it.",
        "Investing is the Investments category, a Transfer bucket, so it is not "
        "part of spend; net_after_investing subtracts it separately.",
        snapshot["meta"]["fx_note"],
    ]
    if partial:
        caveats.append(f"{len(partial)} month(s) have incomplete statement coverage "
                       f"({c.range_label(partial[0], partial[-1])}) and are left out of the "
                       f"totals; they are flagged in the table.")
    used = (len(c.income_rows(snapshot)) + len(c.spend_rows(snapshot))
            + len(c.investing_rows(snapshot)))
    return c.result(sys.modules[__name__], snapshot,
                    [c.source("Transactions", used,
                              "flow_class = External and bucket in (Income, Spend), "
                              "plus the Investments category")],
                    {"from": months[0]["month"] if months else None,
                     "to": months[-1]["month"] if months else None,
                     "currency": "SGD"},
                    {"months": months, "phases": phases, "totals": totals},
                    findings, caveats, links=LINKS)
