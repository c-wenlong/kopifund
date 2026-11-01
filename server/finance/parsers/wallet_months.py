#!/usr/bin/env python3
"""Splitting an all-time wallet export into one period block per month.

Revolut and Wise both export a single statement covering everything from the
first transaction to the day it was generated, so neither has a natural
statement month. Both are cut the same way, and the cut is here rather than in
either parser because the two copies had already started to drift.

The analytics count a month as covered only when every account has a block in
it, so a month with no rows still gets one, carrying the balance unchanged.
"""
import calendar
from collections import defaultdict


def every_month(first, last):
    """['2024-06', '2024-07', ...] inclusive both ends."""
    out, y, m = [], int(first[:4]), int(first[5:7])
    while f"{y:04d}-{m:02d}" <= last:
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def month_end(ym):
    y, m = int(ym[:4]), int(ym[5:7])
    return f"{ym}-{calendar.monthrange(y, m)[1]:02d}"


def split_months(rows, stmt, account):
    """One block per calendar month from the first month with rows to the
    month the export was generated in, idle months included, each opening at
    the running balance: a wallet that printed nothing this month still holds
    its balance, and the analytics count a month as covered only when every
    account has a block in it."""
    blocks = []
    mine = [r for r in rows if r["source"] == stmt["source"]]
    run = stmt["opening"] if stmt["opening"] is not None else 0.0
    by_month = defaultdict(list)
    for r in mine:
        by_month[r["date"][:7]].append(r)
    last = (stmt["generated"] or stmt["period_end"] or "")[:7]
    months = every_month(min(by_month) if by_month else last, last)
    for ym in months:
        rs = by_month.get(ym, [])
        closing = run
        for r in rs:
            closing = r["balance_after"] if r["balance_after"] is not None else round(closing + r["amount"], 2)
        end = month_end(ym)
        if stmt["generated"] and stmt["generated"][:7] == ym:
            end = stmt["generated"]
        b = dict(source=stmt["source"], account=account, account_no=stmt["account_no"],
                 currency=stmt["currency"], period_end=end, period_start=f"{ym}-01",
                 statement_date=end, opening=round(run, 2), closing=round(closing, 2),
                 month=ym, note="; ".join(n for n in (
                     stmt.get("note_acct", ""),
                     "" if rs else "no activity this month" + ("; block closes the export" if ym == last else "")) if n))
        for r in rs:
            r["statement_date"] = end
            r["block"] = ym
        blocks.append(b)
        run = closing
    return blocks

