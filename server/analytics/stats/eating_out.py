#!/usr/bin/env python3
"""Eating out: how often, how much a time, when, where, and against groceries.

Coffee is counted beside eating out rather than inside it, and groceries is the
comparison that makes the number mean something: a high restaurant bill next to
a high grocery bill is a food budget, next to no grocery bill it is a kitchen
that is never used.

The weekly series is every calendar week from the first meal to the last, so a
fortnight away shows as two weeks at zero rather than closing up into a flat
line. `per_week` stays what it always was, meals divided by the weeks that had
one, with the calendar figure beside it.

data:
  weeks       [{week, end, count, spend_minor, avg_ticket_minor}] every week, gaps
              included; `week` is the Monday and `end` the Sunday after it
  months      [{month, count, spend_minor, groceries_minor, avg_ticket_minor, ratio}]
  weekday     [{bucket, count, spend_minor, avg_ticket_minor}]     weekday / weekend
  top_places  [{payee, count, spend_minor, avg_ticket_minor, first_seen, last_seen}]
  summary     {count, spend_minor, avg_ticket_minor, weeks, weeks_with_a_meal, per_week,
               per_calendar_week, spend_per_week_minor, groceries_minor, coffee_minor,
               ratio, places}

G20: money is integer cents under `<name>_minor`, a ticket the mean of the
charges in cents (common.mean_cents); counts, meals a week and the ratio to
groceries are not money and keep their scales.
"""
import json
import sys
from datetime import date, timedelta

from . import common as c

ID = "eating-out"
TITLE = "Eating out"
CHART = {"type": "line", "x": "week", "series": ["spend_minor"], "source": "weeks",
         "unit": "sgd_minor"}

CATEGORY = "Eating out"
GROCERIES = "Groceries"
COFFEE = "Coffee"


def run(snapshot, benchmarks=None):
    spend = c.spend_rows(snapshot)
    # by name, following a rename on the Categories page
    meals = c.dated([r for r in spend if r["category"] in c.category_names(snapshot, CATEGORY)])
    groceries = [r for r in spend if r["category"] in c.category_names(snapshot, GROCERIES)]
    coffee = [r for r in spend if r["category"] in c.category_names(snapshot, COFFEE)]
    charges = [r for r in meals if c.sgd_minor(r) < 0]      # a refund is not a meal

    def ticket(rows):
        """The mean charge of these rows, in cents; a refund is not a ticket."""
        return c.mean_cents(-c.sgd_minor(r) for r in rows if c.sgd_minor(r) < 0)

    by_week = c.by(meals, lambda r: c.week_of(r["date"]))
    first, last = c.date_span(meals)
    calendar = c.weeks_between(first, last) if first else []
    weeks = [{"week": w, "end": (date.fromisoformat(w) + timedelta(days=6)).isoformat(), "count": len([r for r in by_week.get(w, [])
                                       if c.sgd_minor(r) < 0]),
              "spend_minor": c.spend_minor(by_week.get(w, [])),
              "avg_ticket_minor": ticket(by_week.get(w, []))}
             for w in calendar]

    months = []
    for m in c.month_range(snapshot):
        mrows = [r for r in meals if r["month"] == m]
        g = c.spend_minor([r for r in groceries if r["month"] == m])
        s = c.spend_minor(mrows)
        months.append({"month": m, "count": len([r for r in mrows if c.sgd_minor(r) < 0]),
                       "spend_minor": s, "groceries_minor": g,
                       "avg_ticket_minor": ticket(mrows),
                       "ratio": round(s / g, 2) if g > 0 else None})

    weekday = []
    for name, pick in (("weekday", lambda d: not c.is_weekend(d)),
                       ("weekend", c.is_weekend)):
        rows = [r for r in meals if pick(r["date"])]      # meals are dated rows
        hits = [r for r in rows if c.sgd_minor(r) < 0]
        weekday.append({"bucket": name, "count": len(hits), "spend_minor": c.spend_minor(rows),
                        "avg_ticket_minor": ticket(hits)})

    places = []
    for payee, rows in c.by(meals, lambda r: r["payee"] or r["merchant_raw"]
                            or "(no name)").items():
        hits = [r for r in rows if c.sgd_minor(r) < 0]
        seen_first, seen_last = c.date_span(rows)
        places.append({"payee": payee, "count": len(hits), "spend_minor": c.spend_minor(rows),
                       "avg_ticket_minor": ticket(hits),
                       "first_seen": seen_first, "last_seen": seen_last})
    places.sort(key=lambda p: -p["spend_minor"])
    top_places = places[:15]

    # G13: a week's point opens the rows it adds up: eating out (the category as the record names
    # it now), spending only, from the Monday to the Sunday
    names = json.dumps(sorted(c.category_names(snapshot, CATEGORY)), ensure_ascii=False, separators=(",", ":"))
    links = {"spend_minor": {"from": "{x}", "to": "{end}", "spend": "1", "categories": names}}

    total = c.spend_minor(meals)
    grocery_total = c.spend_minor(groceries)
    with_a_meal = len(by_week) or 1
    n_weeks = len(weeks) or 1
    summary = {"count": len(charges), "spend_minor": total,
               "avg_ticket_minor": ticket(charges),
               "weeks": len(weeks), "weeks_with_a_meal": len(by_week),
               "per_week": round(len(charges) / with_a_meal, 2),
               "per_calendar_week": round(len(charges) / n_weeks, 2),
               "spend_per_week_minor": c.div_cents(total, with_a_meal),
               "groceries_minor": grocery_total, "coffee_minor": c.spend_minor(coffee),
               "ratio": round(total / grocery_total, 1) if grocery_total > 0 else None,
               "places": len(places)}

    findings = [
        f"{summary['count']} meals out in the {summary['weeks_with_a_meal']} weeks "
        f"that had one, {c.label(total)} in total, averaging "
        f"{c.label(summary['avg_ticket_minor'])} a ticket and {summary['per_week']} meals in "
        f"each of those weeks, {summary['per_calendar_week']} across the "
        f"{summary['weeks']} calendar weeks they span.",
    ]
    if summary["ratio"]:
        findings.append(f"Eating out is {summary['ratio']} times groceries "
                        f"({c.label(total)} against {c.label(grocery_total)}).")
    if all(w["count"] for w in weekday):
        wd, we = weekday
        findings.append(
            f"Weekday tickets average {c.label(wd['avg_ticket_minor'])} against "
            f"{c.label(we['avg_ticket_minor'])} at the weekend, on {wd['count']} and "
            f"{we['count']} meals.")
    if top_places:
        p = top_places[0]
        findings.append(f"The biggest single place is {p['payee']} at "
                        f"{c.label(p['spend_minor'])} over {p['count']} visits.")

    caveats = [
        "A meal paid in cash, by somebody else, or on a card outside these "
        "statements does not appear.",
        "Coffee is its own category and is reported beside eating out, not inside "
        "it.",
        "A row with no merchant text falls into the same bucket as any other "
        "unnamed row, so the place list is only as good as the rules.",
        "The weekly series runs across every calendar week between the first meal "
        "and the last, so a week away is a zero rather than a missing point. Meals "
        "a week is still over the weeks that had a meal.",
    ]
    return c.result(sys.modules[__name__], snapshot,
                    [c.source("Transactions", len(meals),
                              f"{c.SPEND_FILTER} and category = {CATEGORY}"),
                     c.source("Transactions", len(groceries),
                              f"{c.SPEND_FILTER} and category = {GROCERIES}")],
                    {"category": CATEGORY, "compared_with": GROCERIES,
                     "top_n": 15, "weeks": len(weeks),
                     "weeks_with_a_meal": len(by_week), "currency": "SGD"},
                    {"weeks": weeks, "months": months, "weekday": weekday,
                     "top_places": top_places, "summary": summary},
                    findings, caveats, links=links)
