#!/usr/bin/env python3
"""Where income comes from, how steady it is, and how often it does not arrive.

Volatility is the coefficient of variation, the population standard deviation
over the mean, taken across fully covered months. The months here are the whole
record rather than a sample of a longer one, which is why the divisor is n and
not n minus 1; a spreadsheet's STDEV gives a slightly larger number.

A salary that arrives every month at the same size scores near zero; income
that lands in three lumps a year scores above one.

G20: every amount is integer cents from the rows' `amount_sgd_minor`; a mean
and a spread are taken in cents and rounded to the cent, half up.

data:
  months     [{month, total_minor, salary_minor, other_minor,
               <category>_minor: amount, has_salary, partial}]
  sources    [{category, total_minor, per_month_minor, share, months_present,
               largest_month}]
  employers  [{payee, total_minor, rows, first_seen, last_seen}]
  volatility {mean_minor, stdev_minor, cv, salary_mean_minor, salary_stdev_minor,
              salary_cv, months, months_without_salary, no_salary_months,
              longest_gap_without_salary, last6_mean_minor,
              last12_salary_mean_minor}
"""
import json
import statistics
import sys
from decimal import ROUND_HALF_UP, Decimal

from . import common as c

ID = "income-stability"
TITLE = "Income stability"
CHART = {"type": "stacked", "x": "month", "series": ["salary_minor", "other_minor"],
         "source": "months", "unit": "sgd_minor"}

SALARY = ("Salary", "Freelance income")


def stdev_minor(xs):
    """The population standard deviation of some cents (`common.stdev`), to
    the cent, half up."""
    xs = list(xs)
    if len(xs) < 2:
        return 0
    return int(Decimal(repr(statistics.pstdev(xs))).quantize(Decimal(1), ROUND_HALF_UP))


def run(snapshot, benchmarks=None):
    rows = c.income_rows(snapshot)
    salary_names = c.category_names(snapshot, *SALARY)   # following a rename
    cov = c.coverage(snapshot)
    months = c.month_range(snapshot)
    cats = sorted({r["category"] for r in rows})

    month_rows = []
    for m in months:
        mrows = [r for r in rows if r["month"] == m]
        salary = c.income_minor([r for r in mrows if r["category"] in salary_names])
        total = c.income_minor(mrows)
        entry = {"month": m, "total_minor": total, "salary_minor": salary,
                 "other_minor": total - salary,
                 "has_salary": salary > 0, "partial": cov[m]["partial"]}
        for cat in cats:
            entry[f"{cat}_minor"] = c.income_minor([r for r in mrows if r["category"] == cat])
        month_rows.append(entry)

    counted = [x for x in month_rows if not x["partial"]]
    totals = [x["total_minor"] for x in counted]
    salaries = [x["salary_minor"] for x in counted]
    mean = c.mean_cents(totals)
    salary_mean = c.mean_cents(salaries)
    stdev, salary_stdev = stdev_minor(totals), stdev_minor(salaries)

    no_salary = [x["month"] for x in counted if not x["has_salary"]]
    longest, run_len = 0, 0
    for x in counted:
        run_len = 0 if x["has_salary"] else run_len + 1
        longest = max(longest, run_len)

    volatility = {"mean_minor": mean, "stdev_minor": stdev,
                  "cv": round(stdev / mean, 2) if mean else None,
                  "salary_mean_minor": salary_mean, "salary_stdev_minor": salary_stdev,
                  "salary_cv": (round(salary_stdev / salary_mean, 2)
                                if salary_mean else None),
                  "months": len(counted), "months_without_salary": len(no_salary),
                  "no_salary_months": no_salary,
                  "longest_gap_without_salary": longest,
                  "last6_mean_minor": c.mean_cents(x["total_minor"] for x in counted[-6:]),
                  "last12_salary_mean_minor":
                      c.mean_cents(x["salary_minor"] for x in counted[-12:])}

    total_all = c.sum_cents(totals)
    covered = {x["month"] for x in counted}
    sources = []
    for cat in cats:
        crows = [r for r in rows if r["category"] == cat and r["month"] in covered]
        total = c.income_minor(crows)
        present = sorted({r["month"] for r in crows})
        biggest = (max(counted, key=lambda x: x[f"{cat}_minor"])["month"]
                   if counted else None)
        sources.append({"category": cat, "total_minor": total,
                        "per_month_minor": c.div_cents(total, len(counted)),
                        "share": c.share(total, total_all),
                        "months_present": len(present),
                        "largest_month": biggest})
    sources.sort(key=lambda s: -s["total_minor"])

    employers = []
    for payee, prows in c.by([r for r in rows if r["category"] in salary_names],
                             lambda r: r["payee"] or r["counterparty"]
                             or r["merchant_raw"] or "(no name)").items():
        first, last = c.date_span(prows)
        employers.append({"payee": payee, "total_minor": c.income_minor(prows),
                          "rows": len(prows),
                          "first_seen": first, "last_seen": last})
    employers.sort(key=lambda e: -e["total_minor"])

    # G13: each bar opens the rows it adds up: the salary categories as the record
    # names them now, and every other income row
    names = json.dumps(sorted(salary_names), ensure_ascii=False, separators=(",", ":"))
    links = {"salary_minor": {"month": "{x}", "income": "1", "categories": names},
             "other_minor": {"month": "{x}", "income": "1", "not_categories": names}}

    findings = [
        f"Income averages {c.label(mean)} a month across {len(counted)} covered "
        f"months with a population standard deviation of "
        f"{c.label(stdev)}, a coefficient of variation of "
        f"{volatility['cv']}.",
        f"Salary arrived in {len(counted) - len(no_salary)} of {len(counted)} months "
        f"and averaged {c.label(salary_mean)}; the longest run without any was "
        f"{longest} month(s).",
    ]
    if sources:
        top = sources[0]
        findings.append(f"The largest source is {top['category']} at "
                        f"{c.label(top['per_month_minor'])} a month, "
                        f"{top['share'] * 100:.0f} per cent of income.")
        non_salary = [s for s in sources if s["category"] not in salary_names]
        share = c.share(c.sum_cents(s["total_minor"] for s in non_salary), total_all)
        findings.append(f"{share * 100:.0f} per cent of income is not salary: "
                        f"every other income category, refunds included.")
    if employers:
        findings.append(f"{len(employers)} employer(s) paid salary, the largest being "
                        f"{employers[0]['payee']} at "
                        f"{c.label(employers[0]['total_minor'])}.")

    caveats = [
        "The spread is the population standard deviation, divided by the number of "
        "months rather than one fewer: these months are the whole record, not a "
        "sample of it. A spreadsheet's STDEV is the other one.",
        "The record has no payslip in it, so every figure is what reached the bank; "
        "gross pay and CPF cannot be seen.",
        "Refunds and cashback are an income category here, which flatters income "
        "slightly: they are money coming back rather than money arriving.",
        "Time-limited or one-off income, such as aid or a grant, is not a salary "
        "substitute even where the monthly average suggests one.",
    ]
    return c.result(sys.modules[__name__], snapshot,
                    [c.source("Transactions", len(rows), c.INCOME_FILTER)],
                    {"from": months[0] if months else None,
                     "to": months[-1] if months else None,
                     "salary_categories": list(SALARY), "spread": "population "
                     "standard deviation", "currency": "SGD"},
                    {"months": month_rows, "sources": sources,
                     "employers": employers, "volatility": volatility},
                    findings, caveats, links=links)
