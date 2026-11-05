#!/usr/bin/env python3
"""What each trip cost, per day and by category.

A trip's rows are the ones the rollup assigned in Notion, which is a date range
plus evidence of being away from home. A long stay abroad can overlap the
side trips taken from it, so the side trips take their rows and the long stay
keeps the rest; that is why its per-day figure is a baseline rather than a
holiday cost.

Days are the union of the trip date ranges, not the sum of their lengths.
Side trips sit inside the long stay, so summing counts the shared days twice
and prices every day too low. The sum is kept as
`trip_days_summed`, which is the right number for "how many trip-days did I
book".

data:
  trips       [{name, start, end, days, long_stay, countries, rows, spend_minor,
                spend_per_day_minor, categories: [{category, spend_minor, share,
                share_clamped}], top_payees: [{payee, spend_minor}]}]
  by_category [{category, spend_minor, rows, share, share_clamped}]
  by_category_excluding_rent  the same, without the rent paid during a long stay
  summary     {trips, days, trip_days_summed, spend_minor, spend_per_day_minor,
               share_of_all_spend, base_minor, base_label, rows,
               excluding_long_stays: the same figures, short trips only}

G20: money is integer cents under `<name>_minor`; the shares stay fractions.
"""
import sys

# G10: a stay longer than LONG_STAY_DAYS ([analytics] long_stay_days) is somewhere
# lived in, not travelled to; server/finance/settings.py reads it for the Trips page too
from finance.settings import LONG_STAY_DAYS, long_stay_days

from . import common as c

ID = "travel"
TITLE = "Trips"
CHART = {"type": "bar", "x": "name", "series": ["spend_minor", "spend_per_day_minor"],
         "source": "trips", "unit": "sgd_minor"}

# G13: a trip's spend opens that trip's spending rows. A rate a day is spend over
# days, an average, so its bar does not link.
LINKS = {"spend_minor": {"trip": "{x}", "spend": "1"}}

RENT = "Rent"


def day_rate(total, days):
    """`total` cents over `days` as cents a day, rounded half away from zero
    (common.div_cents): the rule the Trips page uses too (server/finance/api.py, `trips`),
    so a half cent rounds the same way on the page and in this stat."""
    return c.div_cents(total, days)


def priced(row):
    """True when the row carries an SGD value (`amount_sgd_minor`)."""
    return c.stat_cents(row, "amount_sgd") is not None


def union_days(trips):
    """The distinct calendar days these trips cover between them."""
    days = set()
    for t in trips:
        if t.get("start") and t.get("end"):
            days.update(c.days_in(t["start"], t["end"]))
    return len(days)


def run(snapshot, benchmarks=None):
    rows = c.spend_rows(snapshot)
    # G13: a trip's rows are its spending exactly as the Trips page and the
    # Spending explorer count it (server/finance/spending.py, SPEND_WHERE), so the
    # table and this stat agree. A row with no SGD value has no figure to add
    # and is left out rather than counted as a row worth nothing.
    unpriced = [r for r in rows if r["trip"] and not priced(r)]
    on_trip = [r for r in rows if r["trip"] and priced(r)]
    rent = c.category_names(snapshot, RENT)          # following a rename
    all_spend, base_label = c.spend_base_minor(snapshot, rows=rows)
    by_trip = c.by(on_trip, lambda r: r["trip"])
    limit = long_stay_days()

    trips = []
    for t in snapshot["trips"]:
        trips.append(build(t, by_trip.get(t["name"], []), limit))
    for name in sorted(set(by_trip) - {t["name"] for t in snapshot["trips"]}):
        trips.append(build({"name": name, "start": "", "end": "", "countries": []},
                           by_trip[name], limit))
    trips.sort(key=lambda t: (t["start"] or "9999", t["name"]))

    trip_total = c.spend_minor(on_trip)

    def split_by_category(these):
        out = [{"category": cat, "spend_minor": c.spend_minor(crows), "rows": len(crows)}
               for cat, crows in c.by(these, lambda r: r["category"]).items()]
        total = c.spend_minor(these)
        for x in out:
            x["share"] = c.share(x["spend_minor"], total)
            x["share_clamped"] = c.clamp_share(x["share"])
        out.sort(key=lambda x: -x["spend_minor"])
        return out

    cats = split_by_category(on_trip)
    cats_no_rent = split_by_category([r for r in on_trip
                                      if r["category"] not in rent])

    # Days are the union of the ranges: side trips can sit inside a long stay,
    # so adding their lengths counts the shared days twice.
    days = union_days(trips)
    summed = sum(t["days"] for t in trips if t["days"])
    short = [t for t in trips if not t["long_stay"]]
    long_names = [t["name"] for t in trips if t["long_stay"]]
    short_rows = [r for r in on_trip if r["trip"] in {t["name"] for t in short}]
    short_days = union_days(short)
    short_total = c.spend_minor(short_rows)
    summary = {"trips": len(trips), "days": days, "trip_days_summed": summed,
               "spend_minor": trip_total,
               "spend_per_day_minor": day_rate(trip_total, days) if days else None,
               "share_of_all_spend": c.share(trip_total, all_spend),
               "base_minor": all_spend, "base_label": base_label,
               "rows": len(on_trip),
               "excluding_long_stays": {
                   "limit_days": limit, "dropped": long_names,
                   "trips": len(short), "days": short_days,
                   "spend_minor": short_total,
                   "spend_per_day_minor": (day_rate(short_total, short_days)
                                           if short_days else None),
                   "share_of_all_spend": c.share(short_total, all_spend),
                   "rows": len(short_rows)}}

    findings = []
    short_summary = summary["excluding_long_stays"]
    if not snapshot["trips"]:
        findings.append("This snapshot carries no trips, so nothing can be attributed "
                        "to travel; add trips on the Trips page.")
    else:
        findings.append(
            f"Leaving out the stays over {limit} days"
            + (f" ({', '.join(long_names)}), " if long_names else ", ")
            + f"{short_summary['trips']} trips covering {short_summary['days']} "
            f"days account for {c.label(short_summary['spend_minor'])}, "
            f"{short_summary['share_of_all_spend'] * 100:.0f} per cent "
            f"{base_label}.")
        findings.append(
            f"Counting the long stays as well, {summary['trips']} trips cover "
            f"{days} distinct days ({summed} if the overlapping trips are added "
            f"up) and {c.label(trip_total)}, "
            f"{summary['share_of_all_spend'] * 100:.0f} per cent {base_label}.")
        rated = [t for t in trips if t["spend_per_day_minor"] is not None and t["rows"]]
        if rated:
            dearest = max(rated, key=lambda t: t["spend_per_day_minor"])
            findings.append(f"The most expensive day rate is {dearest['name']} at "
                            f"{c.label(dearest['spend_per_day_minor'])} a day over "
                            f"{dearest['days']} days.")
            biggest = max(rated, key=lambda t: t["spend_minor"])
            findings.append(f"The largest total is {biggest['name']} at "
                            f"{c.label(biggest['spend_minor'])} over {biggest['rows']} rows.")
        empty = [t["name"] for t in trips if not t["rows"]]
        if empty:
            findings.append(f"{len(empty)} trip(s) claimed no spending at all: "
                            f"{', '.join(empty[:4])}.")
    if cats:
        findings.append(f"On trips the money goes mostly to {cats[0]['category']} "
                        f"({cats[0]['share'] * 100:.0f} per cent), then "
                        f"{cats[1]['category'] if len(cats) > 1 else 'nothing else'}.")
    if cats_no_rent:
        findings.append(f"Without the rent paid on trips, the trip total is "
                        f"{c.label(c.spend_minor([r for r in on_trip if r['category'] not in rent]))} "
                        f"and the largest category is {cats_no_rent[0]['category']} "
                        f"at {cats_no_rent[0]['share'] * 100:.0f} per cent of it.")

    caveats = [
        "A row is on a trip only when its date falls inside the trip and something "
        "says it was spent away from home, so a flight bought months ahead sits in "
        "the month it was paid, not in the trip.",
        "Nested trips take precedence over the long stay that spans them, so the "
        "long stay's figure is the baseline of living there rather than a holiday.",
        f"Days are the distinct calendar days the trips cover. Adding the trip "
        f"lengths up gives {summed}, which counts the days a side trip shares with "
        f"a long stay twice; that sum is kept as trip_days_summed.",
        f"The trip total spans every month in the record while the base is "
        f"{base_label}, so the share is of a smaller denominator than the "
        f"numerator's window.",
        "A csv-built snapshot has no trips at all: they live in the record.",
        snapshot["meta"]["fx_note"],
    ]
    if unpriced:
        caveats.append(f"{len(unpriced)} trip row(s) have no SGD value (their statement "
                       f"printed no FX rate) and are left out, as on the Spending page.")
    return c.result(sys.modules[__name__], snapshot,
                    [c.source("Transactions", len(on_trip),
                              f"{c.SPEND_FILTER} and trip is not empty "
                              f"and amount_sgd is not empty"),
                     c.source("Trips", len(snapshot["trips"]), "all")],
                    {"currency": "SGD", "long_stay_days": limit},
                    {"trips": trips, "by_category": cats,
                     "by_category_excluding_rent": cats_no_rent,
                     "summary": summary},
                    findings, caveats, links=LINKS)


def build(trip, rows, limit=LONG_STAY_DAYS):
    days = (c.days_between(trip["start"], trip["end"]) + 1
            if trip["start"] and trip["end"] else None)
    total = c.spend_minor(rows)
    cats = [{"category": cat, "spend_minor": c.spend_minor(crows),
             "share": c.share(c.spend_minor(crows), total),
             "share_clamped": c.clamp_share(c.share(c.spend_minor(crows), total))}
            for cat, crows in c.by(rows, lambda r: r["category"]).items()]
    cats.sort(key=lambda x: -x["spend_minor"])
    payees = [{"payee": p, "spend_minor": c.spend_minor(prows)}
              for p, prows in c.by(rows, lambda r: r["payee"]
                                   or r["merchant_raw"] or "(no name)").items()]
    payees.sort(key=lambda x: -x["spend_minor"])
    return {"name": trip["name"], "start": trip["start"], "end": trip["end"],
            "days": days, "long_stay": bool(days and days > limit),
            "countries": list(trip["countries"]), "rows": len(rows),
            "spend_minor": total,
            "spend_per_day_minor": day_rate(total, days) if days else None,
            "categories": cats, "top_payees": payees[:5]}
