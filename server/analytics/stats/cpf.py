#!/usr/bin/env python3
"""What is locked away in CPF, who put it there, and what it does to net worth.

Singapore's Central Provident Fund is a mandatory savings scheme. It is not a
debt and not a deduction that disappears: for a member aged 55 or under, 20 per
cent of gross salary up to the Ordinary Wage ceiling is taken out of pay and
the employer adds 17 per cent on top, so 37 per cent of gross lands in three
accounts in the member's own name. Ordinary Account (OA) pays for housing and
education, Special Account (SA) is retirement, MediSave Account (MA) is
healthcare. The money earns guaranteed interest (2.5 per cent on OA, 4 per cent
on SA and MA, with extra interest on the first 60,000) and cannot be spent on
daily life. So it belongs on the balance sheet as locked savings, counted apart
from cash and apart from the brokerage.

The figures come from the yearly statements themselves, parsed out of
`statements/cpf` in the data folder into `snapshot["cpf"]`. Three things follow from the
statement being yearly.

  * The balance is the balance **on 31 December of the statement's year**, not
    today. Contributions since then are real and are not here, so the as-of
    date goes in the headline and a statement more than fifteen months behind
    the snapshot asks to be replaced.
  * The Government column on a 2020-onwards CPF statement carries the interest
    as well as any support scheme, because the interest is credited by the
    Government, while the 2016 to 2019 layouts print the interest outside that
    column. So `government` here is always the support plus the interest and
    means the same thing in every year, `interest` is the base plus extra
    interest on its own, and a finding never adds the two together.
  * The 2016 to 2019 layouts print one "Employment" figure rather than an
    employee and an employer half, so `employee` and `employer` are null in
    those years and only `employment` is known. `employer_share_last` is null
    whenever the latest year is one of those.

Net worth here is cash plus investments plus CPF, and each of the three was
last measured on a different day, so every sentence that adds them names all
three dates. Cash and investments come from the portfolio stat's own summary
rather than from a second reading of the balances, so the two cards can never
disagree; when there is no brokerage statement to read, the portfolio stat
knows no investments figure and this stat states no net worth at all rather
than quietly calling cash plus CPF the whole of it.

G20: every figure is SGD in integer cents under a `_minor` name, each
statement figure turned to cents once, as it is read; the two shares
(`employer_share_last`, `locked_share`) are fractions.

data:
  years    [{year, as_of, oa_minor, sa_minor, ma_minor, total_minor,
             employee_minor, employer_minor, employment_minor,
             government_minor, interest_minor, outflow_minor, growth_minor}]
  summary  {total_minor, oa_minor, sa_minor, ma_minor, as_of, years,
            contributed_employee_all_minor, contributed_employer_all_minor,
            interest_all_minor, government_all_minor, growth_last_minor,
            employer_share_last, cash_sgd_minor, cash_as_of,
            investments_sgd_minor, investments_as_of, net_worth_total_minor,
            locked_share}
"""
import sys

from finance.money import label, stat_cents, to_cents

from . import common as c, portfolio

ID = "cpf"
TITLE = "CPF savings"
CHART = {"type": "stacked", "x": "year", "series": ["oa_minor", "sa_minor", "ma_minor"],
         "source": "years", "unit": "sgd_minor"}

# The three accounts, in the order every statement prints them.
ACCOUNTS = ("oa", "sa", "ma")

# How far behind the snapshot the newest statement may fall before the stat
# asks for a new download. CPF publishes a year's statement early in the
# following year, so fifteen months is one missed publication rather than a
# statement that is merely not yet out.
STALE_MONTHS = 15


def plural(n, word):
    """`1 statement`, `10 statements`: a count with its noun."""
    return f"{n} {word}" + ("" if n == 1 else "s")


def statements(snapshot):
    """The parsed statements, oldest first. Empty when the key is absent, which
    is what every snapshot written before CPF was loaded looks like."""
    block = snapshot.get("cpf") or {}
    return sorted(block.get("statements") or [], key=lambda st: st.get("year") or 0)


def amount(block, key):
    """One figure out of a statement's balance or flow block, in cents."""
    value = (block or {}).get(key)
    return 0 if value is None else to_cents(value)


def optional(block, key):
    """One figure that a layout is allowed not to print, kept null when it did
    not. The 2016 to 2019 statements do not split employment into the two
    halves, and a zero there would read as "the employer paid nothing"."""
    value = (block or {}).get(key)
    return None if value is None else to_cents(value)


def years(rows):
    """One row per statement, in the shape the chart and the table read."""
    out = []
    for st in rows:
        closing = st.get("closing") or {}
        opening = st.get("opening") or {}
        inflow = st.get("inflow") or {}
        outflow = st.get("outflow") or {}
        employee, employer = optional(inflow, "employee"), optional(inflow, "employer")
        employment = optional(inflow, "employment")
        if employment is None:
            employment = (employee or 0) + (employer or 0)
        # The Government's whole share, support plus the interest it credited,
        # so the column means the same thing in a 2016 layout as in a 2025 one.
        interest = amount(inflow, "interest")
        scheme = optional(inflow, "government_support")
        if scheme is None:
            scheme = max(amount(inflow, "government") - interest, 0)
        out.append({
            "year": st.get("year"),
            "as_of": st.get("as_of") or "",
            "oa_minor": amount(closing, "oa"), "sa_minor": amount(closing, "sa"),
            "ma_minor": amount(closing, "ma"), "total_minor": amount(closing, "total"),
            "employee_minor": employee, "employer_minor": employer,
            "employment_minor": employment,
            "government_minor": scheme + interest,
            "interest_minor": interest,
            "outflow_minor": amount(outflow, "total"),
            "growth_minor": amount(closing, "total") - amount(opening, "total"),
        })
    return out


def support(row):
    """The Government's own money in a year, its interest aside: the top-ups
    and support schemes on their own."""
    return row["government_minor"] - row["interest_minor"]


def wages_ahead_from(rows):
    """The earliest year from which the running total of wages stays ahead of
    the running total of the Government's money, or None while it never does.

    Until a member earns properly, a CPF balance is mostly interest and
    Government schemes rather than pay. Asking when wages first passed them
    can name an early year with a tiny balance that the schemes then overtook
    again; the year worth naming is the one the balance has been carried by
    pay ever since.
    """
    wages = other = 0
    running = []
    for row in rows:
        wages += row["employment_minor"] or 0
        other += row["government_minor"]
        running.append((row["year"], wages > other))
    first = None
    for year, ahead in reversed(running):
        if not ahead:
            break
        first = year
    return first


def months_old(day, today):
    """How many whole months lie between a statement's last day and today."""
    if not day or not today:
        return 0
    return len(c.months_between(min(day[:7], today[:7]), max(day[:7], today[:7]))) - 1


def latest_date(*days):
    """The latest of some dates, ignoring the ones that are not there."""
    have = [d for d in days if d]
    return max(have) if have else None


def empty_summary():
    return {"total_minor": 0, "oa_minor": 0, "sa_minor": 0, "ma_minor": 0, "as_of": None,
            "years": 0, "contributed_employee_all_minor": 0,
            "contributed_employer_all_minor": 0, "interest_all_minor": 0,
            "government_all_minor": 0, "growth_last_minor": 0,
            "employer_share_last": None, "cash_sgd_minor": None, "cash_as_of": None,
            "investments_sgd_minor": None, "investments_as_of": None,
            "net_worth_total_minor": None, "locked_share": None}


CAVEATS = [
    "CPF is mandated savings rather than a liability: 20 per cent of gross pay "
    "is deducted and the employer adds 17 per cent on top, and the money sits "
    "in the member's own three accounts. It is counted apart from cash because "
    "it cannot be spent on daily life, not because it is owed to anybody.",
    "The balance is the one printed on 31 December of the statement's year, so "
    "it is as old as the newest statement: everything contributed since is real "
    "and is not in this figure.",
    "The three accounts are not one pot. OA can go to housing and education, MA "
    "only to healthcare and SA only to retirement, so the total overstates what "
    "any single need can draw on.",
    "The Government figure is its support schemes plus the interest it "
    "credited. A 2020-onwards statement prints those in one column and a 2016 "
    "to 2019 one prints the interest outside it, so the interest is added back "
    "here and the column means the same thing in every year; interest is also "
    "reported on its own, and the two are never added together.",
    "The 2016 to 2019 statements print one combined employment figure, so the "
    "employee and employer halves are unknown in those years rather than zero.",
    "Nothing here projects the interest ahead, the Retirement Account that is "
    "formed at 55, or any withdrawal, and no housing or MediSave claim in the "
    "record is netted off.",
]


def rejected_findings(block):
    """One sentence per statement the parser left out because its year does
    not reconcile, naming the year and the gap in each account."""
    out = []
    for r in sorted(block.get("rejected") or [], key=lambda r: (r["year"], r["file"])):
        gaps = ", ".join(f"{k.upper()} {label(abs(to_cents(v)))}"
                         for k, v in (r.get("off") or {}).items() if v)
        out.append(
            f"The {r['year']} statement ({r['file']}) does not reconcile: its opening "
            f"plus its transactions misses its closing by {gaps}, so it is left out "
            f"and every figure here is from the years that do add up. Open its .txt "
            f"dump and check the transactions table.")
    return out


def run(snapshot, benchmarks=None):
    block = snapshot.get("cpf") or {}
    folder = block.get("folder") or "statements/cpf"
    rows = statements(snapshot)
    rejected = rejected_findings(block)
    today = (snapshot.get("meta", {}).get("generated_at") or "")[:10] \
        or c.last_date(snapshot) or ""
    params = {"folder": folder, "accounts": list(ACCOUNTS),
              "stale_months": STALE_MONTHS, "currency": "SGD"}
    sources = [c.source("CPF yearly statements", len(rows),
                        f"one statement of account per year in {folder}")]

    if not rows:
        return c.result(sys.modules[__name__], snapshot, sources, params,
                        {"years": [], "summary": empty_summary()},
                        rejected +
                        [f"No CPF yearly statements were found in {folder}, so no "
                         f"CPF savings are counted here and the net worth on this "
                         f"page is cash and investments only. Download the yearly "
                         f"statement of account from the CPF website and save it as "
                         f"{folder}/<year>/<year>.pdf."],
                        ["CPF is mandated savings rather than a liability, so a "
                         "record with no statement in it understates what the owner "
                         "has rather than what the owner owes."])

    table = years(rows)
    last = table[-1]
    employee_all = c.sum_cents(r["employee_minor"] or 0 for r in table)
    employer_all = c.sum_cents(r["employer_minor"] or 0 for r in table)
    employment_all = c.sum_cents(r["employment_minor"] or 0 for r in table)
    interest_all = c.sum_cents(r["interest_minor"] for r in table)
    government_all = c.sum_cents(r["government_minor"] for r in table)
    share_last = (round(c.share(last["employer_minor"], last["employment_minor"]), 4)
                  if last["employer_minor"] is not None and last["employment_minor"] else None)

    pf = (portfolio.run(snapshot, benchmarks)["data"]["summary"] or {})
    known = bool(pf.get("statements"))
    cash_sgd = (stat_cents(pf, "cash_sgd") or 0) if known else None
    cash_as_of = pf.get("cash_as_of") if known else None
    investments = (stat_cents(pf, "value_sgd") or 0) if known else None
    investments_as_of = pf.get("as_of") if known else None
    total = last["total_minor"]
    net_worth = cash_sgd + investments + total if known else None
    locked = round(c.share(total, net_worth), 4) if net_worth else None

    summary = {"total_minor": total, "oa_minor": last["oa_minor"], "sa_minor": last["sa_minor"],
               "ma_minor": last["ma_minor"], "as_of": last["as_of"], "years": len(table),
               "contributed_employee_all_minor": employee_all,
               "contributed_employer_all_minor": employer_all,
               "interest_all_minor": interest_all, "government_all_minor": government_all,
               "growth_last_minor": last["growth_minor"], "employer_share_last": share_last,
               "cash_sgd_minor": cash_sgd, "cash_as_of": cash_as_of,
               "investments_sgd_minor": investments,
               "investments_as_of": investments_as_of,
               "net_worth_total_minor": net_worth, "locked_share": locked}

    findings = rejected + [
        f"CPF held {label(total)} at {c.date_label(last['as_of'])}, split into "
        f"OA {label(last['oa_minor'])} for housing and education, SA {label(last['sa_minor'])} "
        f"for retirement and MA {label(last['ma_minor'])} for healthcare, over "
        f"{plural(len(table), 'yearly statement')}.",
    ]

    support_last = support(last)
    if last["employee_minor"] is not None:
        wages = (f"{label(last['employment_minor'])} of wages went in, "
                 f"{label(last['employee_minor'])} out of the member's pay and "
                 f"{label(last['employer_minor'])} added by the employer on top")
        if share_last:
            wages += f", the employer's share being {round(share_last * 100)} per cent"
    else:
        wages = (f"{label(last['employment_minor'])} of wages went in, which this "
                 f"layout does not split between the member and the employer")
    givers = (f"the Government put in {label(last['government_minor'])}, of which "
              f"{label(last['interest_minor'])} was interest and {label(support_last)} "
              f"was support"
              if support_last else
              f"the Government credited {label(last['interest_minor'])} of interest "
              f"and no support scheme paid in")
    findings.append(f"In {last['year']} {wages}, and {givers}. The balance grew by "
                    f"{label(last['growth_minor'])} over the year.")

    overtook = wages_ahead_from(table)
    whole = (f"Across the whole record {label(employment_all)} came in as "
             f"wages and {label(government_all)} from the Government, of "
             f"which {label(interest_all)} was interest.")
    if employee_all or employer_all:
        whole += (f" The wages that are split name {label(employee_all)} from the "
                  f"member and {label(employer_all)} from the employer.")
    whole += (f" Interest and Government money were the larger half until "
              f"{overtook - 1}; wages have carried the balance since {overtook}."
              if overtook and overtook > table[0]["year"] else
              (" Wages have been the larger half from the first statement on."
               if overtook else
               " Interest and Government money are still the larger half, so "
               "this balance has been grown more by the scheme than by pay."))
    findings.append(whole)

    if net_worth:
        findings.append(
            f"Cash of {label(cash_sgd)} at {c.date_label(cash_as_of)}, investments of "
            f"{label(investments)} at {c.date_label(investments_as_of)} and CPF of "
            f"{label(total)} at {c.date_label(last['as_of'])} is a net worth of "
            f"{label(net_worth)}, of which {round(locked * 100, 1)} per cent is "
            f"the CPF: savings that are real but locked for retirement, housing "
            f"and healthcare, on three dates that are each the last time that "
            f"side was measured.")
    else:
        findings.append(
            f"No brokerage statement has been loaded, so investments are unknown "
            f"and no net worth including CPF is stated here; the CPF side of it "
            f"is {label(total)} at {c.date_label(last['as_of'])}.")

    age = months_old(last["as_of"], today)
    if last["as_of"] and today and last["as_of"] < today and age > STALE_MONTHS:
        findings.append(
            f"The newest statement is for {last['year']} and stops at "
            f"{c.date_label(last['as_of'])}, {age} months before this snapshot, so the balance "
            f"above is that old and a whole year of contributions is missing from "
            f"it: download the next yearly statement of account.")

    return c.result(sys.modules[__name__], snapshot, sources,
                    {**params, "net_worth_as_of": latest_date(
                        last["as_of"], cash_as_of, investments_as_of)},
                    {"years": table, "summary": summary}, findings, CAVEATS)
