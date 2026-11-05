#!/usr/bin/env python3
"""Cash at each month end, and how many months of spending it covers.

Cash comes from the Period balances closing figures in SGD, one block per
account, month and currency, so a wallet holding four currencies contributes
four blocks that are added together. A balance is carried per account **and
currency**: a wallet that printed only its Malaysian ringgit block this month
keeps the dollars it printed last month rather than losing them. An account
with no block at all in a month keeps its last known balance, which is what a
bank statement means: no statement is not a zero balance.

The headline stops at the last month every open bank account covers
(`common.coverage`, the one rule for it): an account that closed stops
counting after its last statement, and one that prints half-yearly covers the
months its statement spans. A wallet (PayLah, YouTrip, Revolut, Wise) that
printed nothing simply carries its last balance, since it is only ever funded
from the bank accounts and an unused wallet prints no statement. The month
after it can be most of a balance five weeks old,
and a five-week-old number is not what somebody asking "how much have I got"
means. The tail is kept, under `provisional`, with the stale amount named.

Runway is cash divided by the trailing spend rate. Both the whole-phase rate and
the last six months are shown, because they differ enough to change the answer.

G20: every amount is integer cents. A balance is the block's
`closing_sgd_minor` (the record's own cents), the spending is added from the
rows' `amount_sgd_minor`, and a spend rate is a mean in cents (`mean_cents`,
half up).

data:
  months      [{month, cash_minor, <account>_minor: balance, spend_minor,
                runway_months, carried, carried_amount_minor, not_started,
                complete, partial}]
  provisional the same rows for the months after the last complete one
  accounts    [{account, account_no, last_balance_minor, as_of}]; account_no is
              masked to its last four digits and every account is named by
              its label (common.account_labels), never by a full number
  summary     {cash_minor, as_of, as_of_complete, carried, carried_amount_minor,
               latest_month, latest_cash_minor, spend_avg_all_minor,
               spend_avg_6m_minor, runway_all, runway_6m, rule_low, rule_high,
               trend_6m_minor, accounts}
"""
import sys

from finance.account_names import mask, unique

from . import common as c

ID = "savings-runway"
TITLE = "Cash and runway"
CHART = {"type": "line", "x": "month", "series": ["cash_minor", "runway_months"],
         "source": "months", "unit": "sgd_minor",
         "units": {"runway_months": "months"}}

RULE_LOW, RULE_HIGH = 3, 6

# G13: cash and runway are a balance and a ratio, so their line does not link.
# A month's spending is a sum of rows, which a dashboard widget plots (G26,
# server/finance/dashboard.py reads these links).
LINKS = {"spend_minor": {"month": "{x}", "spend": "1"}}

# a month row's own fields, bare (an account's column is its name plus
# `_minor`, so a name here would land on the row's own figure): no account's
# column may take one of these names
ROW_FIELDS = ("month", "cash", "spend", "carried", "carried_amount", "not_started",
              "complete", "partial", "runway_months")


def closing_minor(block):
    """A block's SGD closing balance in cents, or None when it has none: its
    `closing_sgd_minor`, else the closing itself (`closing_minor`) for an SGD
    block."""
    value = c.stat_cents(block, "closing_sgd")
    if value is None and block["currency"] == "SGD":
        value = c.stat_cents(block, "closing")
    return value


def closing_by_month(snapshot):
    """{month: {(account_no, currency): SGD closing in cents}} from the blocks
    that print balances. Keeping the currency in the key is what lets a wallet
    carry the pockets it did not print this month."""
    out = {}
    for b in snapshot["periods"]:
        if c.is_card_block(b) or not b["has_balances"]:
            continue
        value = closing_minor(b)
        if value is None:
            continue
        m, key = c.period_month(b), (b["account_no"], b["currency"])
        out.setdefault(m, {}).setdefault(key, 0)
        out[m][key] += value
    return out


def printed_by_month(closings):
    """{month: {account_no}} and each account's first month, from the same
    blocks the balances came from. A block that prints no figure this stat can
    use is not a statement for this purpose: its balance is carried too."""
    printed, first = {}, {}
    for m, held in closings.items():
        for account_no, _currency in held:
            printed.setdefault(m, set()).add(account_no)
            first[account_no] = min(first.get(account_no, m), m)
    return printed, first


def runway(cash, rate):
    """Months of spending `cash` covers at `rate`, both in cents, or None."""
    return round(cash / rate, 1) if rate else None


def run(snapshot, benchmarks=None):
    # balances, not flows: a wallet export runs to its export month and an
    # account can print later than the last transaction, and both have to be
    # walked into or their balance is never carried there
    months = c.balance_month_range(snapshot)
    closings = closing_by_month(snapshot)
    labels = c.account_labels(snapshot)
    spend_rows = c.spend_rows(snapshot)
    cov = c.coverage(snapshot)
    # an account that closed holds nothing after its last statement, so it is
    # not carried (`common.statement_spans`)
    closed = {a: s["to"] for a, s in c.statement_spans(snapshot).items() if s["to"]}

    printed, started = printed_by_month(closings)
    wallets = c.wallet_accounts(snapshot)
    # each account's name in this stat, from its label: one per account even
    # where two labels meet, and never one of a month row's own fields, so a
    # balance never lands in another account's column or in the cash total
    name = unique({a: labels.of(a) for a in started}, reserved=ROW_FIELDS)

    # the months each account's rows fall in: a balance carried past a month the
    # account had rows in is stale, whatever statement those rows came on
    moved = {}
    for r in snapshot["transactions"]:
        if r.get("account_no") and r.get("date"):
            moved.setdefault(r["account_no"], set()).add(r["date"][:7])

    def stale(a, m):
        """True when account `a`'s carried balance at month `m` is out of date: it
        printed a statement this month with no balance the stat can read, or it
        has rows after the month its balance was last read."""
        return a in cov[m]["accounts"] or any(read_at[a] < x <= m for x in moved.get(a, ()))

    held, rows, read_at = {}, [], {}
    for m in months:
        for key, value in sorted(closings.get(m, {}).items()):
            held[key] = value
        # an account that closed holds nothing from its last statement on, even
        # one whose final zero printed no SGD rate (closing_by_month skips it)
        for key in [k for k in held if k[0] in closed and m >= closed[k[0]]]:
            held[key] = 0
        here = printed.get(m, set())
        read_at.update((a, m) for a in here)
        carried = sorted(a for a in {k[0] for k in held}
                         if a not in here and not (a in closed and m >= closed[a]))
        by_account = {}
        for (account_no, _currency), value in held.items():
            by_account[account_no] = by_account.get(account_no, 0) + value
        row = {"month": m, "cash_minor": c.sum_cents(held.values()),
               "spend_minor": c.spend_minor([r for r in spend_rows if r["month"] == m]),
               "carried": [name[a] for a in carried],
               "carried_amount_minor": c.sum_cents(by_account[a] for a in carried),
               "not_started": sorted(name[a] for a, start in started.items()
                                     if m < start),
               # a month is complete when some open bank account covers it,
               # none is missing from it, and no bank account's carried figure
               # is stale (`stale`). A carried wallet balance does not make it
               # incomplete, since a wallet that printed nothing was not used
               # and the last balance it printed is the balance it holds; nor
               # does an idle account between its half-yearly statements,
               # which has no rows since and holds what it last printed
               "complete": (bool(cov[m]["expected"]) and not cov[m]["missing"]
                            and not [a for a in carried if a not in wallets and stale(a, m)]),
               "partial": cov[m]["partial"]}
        # one column per account, in the order of the names shown
        for account_no, value in sorted(by_account.items(), key=lambda kv: name[kv[0]]):
            row[f"{name[account_no]}_minor"] = value
        rows.append(row)

    # The cash line stops at the last month every open bank account covers
    # (wallets carry). What follows it is a real number for some accounts and
    # a five-week-old one for the rest, so it is reported apart.
    complete = [x for x in rows if x["complete"]]
    as_of_complete = complete[-1]["month"] if complete else None
    provisional = ([x for x in rows if x["month"] > as_of_complete]
                   if as_of_complete else list(rows))
    if as_of_complete:
        rows = [x for x in rows if x["month"] <= as_of_complete]

    # the spend rates run over the flow months only: a month the walk reaches
    # through a balance alone has no rows, and its zero spending would pull
    # both averages down and stretch the runway
    flow_months = set(c.month_range(snapshot))
    counted = [x for x in rows if not x["partial"] and x["month"] in flow_months]
    all_rate = c.mean_cents(x["spend_minor"] for x in counted)
    last6 = counted[-6:]
    rate6 = c.mean_cents(x["spend_minor"] for x in last6)
    for x in rows + provisional:
        x["runway_months"] = runway(x["cash_minor"], rate6 or all_rate)

    cash = rows[-1]["cash_minor"] if rows else 0
    latest = provisional[-1] if provisional else (rows[-1] if rows else None)
    trend = (rows[-1]["cash_minor"] - rows[-7]["cash_minor"]
             if len(rows) >= 7 else None)
    summary = {"cash_minor": cash, "as_of": as_of_complete,
               "as_of_complete": as_of_complete,
               "carried": latest["carried"] if latest else [],
               "carried_amount_minor": latest["carried_amount_minor"] if latest else 0,
               "latest_month": latest["month"] if latest else None,
               "latest_cash_minor": latest["cash_minor"] if latest else 0,
               "spend_avg_all_minor": all_rate, "spend_avg_6m_minor": rate6,
               "runway_all": runway(cash, all_rate),
               "runway_6m": runway(cash, rate6),
               "rule_low": RULE_LOW, "rule_high": RULE_HIGH,
               "trend_6m_minor": trend,
               "accounts": len({a for a, _ in held})}

    last_seen, by_account = {}, {}
    for m in months:
        for (account_no, _currency) in sorted(closings.get(m, {})):
            last_seen[account_no] = m
    for (account_no, _currency), value in held.items():
        by_account[account_no] = by_account.get(account_no, 0) + value
    accounts = [{"account": name[a], "account_no": mask(a),
                 "last_balance_minor": v, "as_of": last_seen.get(a)}
                for a, v in sorted(by_account.items(), key=lambda kv: -kv[1])]

    findings = [
        f"Cash across {summary['accounts']} accounts is {c.label(cash)} at "
        f"{c.month_label(as_of_complete)}, the last month every open bank account's statements cover, "
        f"which is {summary['runway_6m']} months at the last six months' spending "
        f"rate of {c.label(rate6)} and {summary['runway_all']} months at the "
        f"{len(counted)}-month rate of {c.label(all_rate)}.",
    ]
    if summary["carried"]:
        findings.append(
            f"The record runs to {c.month_label(summary['latest_month'])}, where the balances add "
            f"to {c.label(summary['latest_cash_minor'])}, but "
            f"{c.label(summary['carried_amount_minor'])} of that is "
            f"{', '.join(summary['carried'])} still showing an earlier month's "
            f"closing balance, so the headline stays at {c.month_label(as_of_complete)}.")
    if summary["runway_6m"] is not None:
        where = ("below" if summary["runway_6m"] < RULE_LOW else
                 "inside" if summary["runway_6m"] <= RULE_HIGH else "above")
        findings.append(f"That is {where} the usual {RULE_LOW} to {RULE_HIGH} month "
                        f"emergency-fund range.")
    if trend is not None:
        findings.append(f"Cash has {'risen' if trend >= 0 else 'fallen'} by "
                        f"{c.label(abs(trend))} over the last six months.")
    if accounts:
        findings.append(f"{accounts[0]['account']} holds the most at "
                        f"{c.label(accounts[0]['last_balance_minor'])}, as at "
                        f"{c.date_label(accounts[0]['as_of'])}.")

    caveats = [
        "Balances are statement closing figures, so an account whose statement "
        "stops early keeps its last printed balance rather than dropping to zero.",
        "A balance is carried per account and currency, so a wallet that printed "
        "only some of its currencies this month keeps the rest of its pockets "
        "rather than losing them.",
        "The months before an account's first statement are marked: the cash line "
        "is lower there because that account did not exist in this record yet, not "
        "because the money was not held.",
        "Investments held at Tiger Brokers are not cash and are not counted here; "
        "the money that went into them left this balance.",
        "Runway divides cash by spending only, so it assumes no income at all, "
        "which is the point of the measure and not a forecast.",
        snapshot["meta"]["fx_note"],
    ]
    return c.result(sys.modules[__name__], snapshot,
                    [c.source("Period balances", len(snapshot["periods"]),
                              "closing balances in SGD, deposit blocks only"),
                     c.source("Transactions", len(spend_rows), c.SPEND_FILTER)],
                    {"rule_low": RULE_LOW, "rule_high": RULE_HIGH,
                     "rate_window_months": 6, "as_of_complete": as_of_complete,
                     "currency": "SGD"},
                    {"months": rows, "provisional": provisional,
                     "accounts": accounts, "summary": summary},
                    findings, caveats, links=LINKS)
