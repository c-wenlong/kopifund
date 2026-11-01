#!/usr/bin/env python3
"""Standing payments found by their spacing, not by a flag.

Rows are grouped by payee, and by their normalised bank text when the payee is
empty (never by amount alone, which lumps different merchants together). A
group's charges are then split into amount levels (G16): the densest amount
first, everything within the amount band of it on a log scale, then the
densest of what is left, and so on. Levels whose charges together keep to one
cadence, with no occurrence holding a charge of both, are one series whose price
changed (up, down, back again, or alternating), so a price change continues the
series instead of stopping it. Levels billed on their own schedules side by side
(two charges a period) stay apart, so one payee can hold several series (a
subscription and its separate add-on, two plans) and each is detected alone.

A series is recurring when its charges keep to one cadence:

    weekly 5 to 9 days, fortnightly 11 to 18, twice a month (two fixed days),
    monthly 26 to 35, every two months 55 to 68, quarterly 80 to 100,
    half-yearly 170 to 195, yearly 340 to 390

Each payment is scored against the occurrence the schedule expected (the last
on-time payment plus one cadence), so one late payment costs one gap, not two.

A series is paused when nothing has come in for one and a half cadences before
the last day its own account's statements cover, and stopped after three.

data:
  as_of             the snapshot's last day, which the page reads to tell rows loaded since
  series            [{id, name, payee, category, bucket, cadence, cadence_days, occurrences,
                      rows, amount_varies, first_seen, last_seen, avg_amount_minor,
                      median_amount_minor, total_minor, monthly_cost_minor, as_of,
                      days_since_last, status, paused, stopped, flagged_recurring,
                      grouped_by, group}]
  active / paused / stopped  the same rows split by status
  possible_auto_bills [{payee, bank_txn_type, rows, total_minor, first_seen, last_seen}]
  totals            {monthly_fixed_cost_minor, monthly_committed_minor, active, paused, stopped}

G20: every money field is integer cents under `<name>_minor`, worked out in
cents by server/finance/cadence.py.
  flag_mismatches   {flagged_not_detected, detected_not_flagged}
"""
import sys

# G19: the engine is finance's (server/finance/recurring.py runs on it too, and finance
# never imports analytics); every name is re-exported so rd.X still works
from finance.cadence import (  # noqa: F401
    AMOUNT_BAND, AUTO_BILL_TYPES, BANDS, CADENCES, DAY_STEP, FIT, FLAGGED_NEED, Grid,
    HABIT_CADENCES, MAX_GAP_FACTOR, MERGE_DAYS, MIN_AMOUNT_MINOR, MIN_OCCURRENCES, MIN_SPAN,
    MONTH_STEP, NOMINAL, PAUSE_FACTOR, PAUSE_FLOOR, PREFER, PRICE_CHANGE, RECENT, SHORT_RUN,
    STATES, STOP_FACTOR, STOP_FLOOR, TIGHT_BAND, TIGHT_CATEGORIES, TWICE, TWICE_FIT,
    TWICE_TOLERANCE, _apart, _found, _levels, _merged, _series_row, _together, _twice, _walk,
    _log, account_ends, amount_band, assign_loners, by_habit, cadence_for, detect_group,
    dominant_category, group_key, month_day, month_of, monthly_cost_minor, name_series, nearest_series, normalise,
    on_schedule, partition, row_text, runs_at, score, spread_varies, state_after, tolerance,
    usual, usual_pair)

from . import common as c

ID = "recurring-detector"
TITLE = "Recurring payments"
CHART = {"type": "bar", "x": "name", "series": ["monthly_cost_minor"],
         "source": "active", "unit": "sgd_minor"}


def eligible(snapshot):
    """The rows the detector reads: External outflows that are not synthetic
    fillers, not income and not a transfer between the owner's own pockets."""
    return c.dated([r for r in snapshot["transactions"]
                    if r["flow_class"] == "External" and not r["synthetic"]
                    and c.sgd_minor(r) < 0 and r["bucket"] != "Income"
                    and r["need_class"] != "Transfer"])


def run(snapshot, benchmarks=None):
    outflow = eligible(snapshot)
    end = c.last_date(snapshot)
    flagged = {p["name"] for p in snapshot["payees"] if p["recurring"]}
    tight = c.category_names(snapshot, *TIGHT_CATEGORIES)
    ends = account_ends(snapshot, end) if end else {}

    series = []
    groups = c.by([r for r in outflow if group_key(r)], group_key)
    for (kind, key), group in sorted(groups.items()):
        series += detect_group(kind, key, group, kind == "payee" and key in flagged, tight, ends, end)
    series = name_series(series)
    series.sort(key=lambda s: (-s["monthly_cost_minor"], s["id"]))
    active = [s for s in series if s["status"] == "active"]
    paused = [s for s in series if s["status"] == "paused"]
    stopped = [s for s in series if s["status"] == "stopped"]

    auto = []
    pulled = [r for r in snapshot["transactions"] if c.sgd_minor(r) < 0
              and r["flow_class"] == "External" and not r["synthetic"]
              and any(t in r["bank_txn_type"] for t in AUTO_BILL_TYPES)]
    for payee, rows in c.by(pulled, lambda r: r["payee"] or r["merchant_raw"]
                            or r["counterparty"] or "(no name)").items():
        first, last = c.date_span(rows)
        auto.append({"payee": payee, "bank_txn_type": rows[0]["bank_txn_type"],
                     "rows": len(rows), "total_minor": c.spend_minor(rows),
                     "first_seen": first, "last_seen": last,
                     "detected": any(s["payee"] == payee for s in series)})
    auto.sort(key=lambda a: -a["total_minor"])

    monthly_fixed = c.sum_cents(s["monthly_cost_minor"] for s in active
                                if s["bucket"] == "Spend")
    monthly_committed = c.sum_cents(s["monthly_cost_minor"] for s in active)
    detected = {s["payee"] for s in series if s["grouped_by"] == "payee"}
    mismatches = {"flagged_not_detected": sorted(flagged - detected),
                  "detected_not_flagged": sorted({
                      s["payee"] for s in active
                      if s["grouped_by"] == "payee" and not s["flagged_recurring"]})}

    findings = [
        f"{len(active)} live recurring series cost {c.label(monthly_fixed)} a "
        f"month in spending, {c.label(monthly_committed)} once the investing "
        f"transfer is counted.",
    ]
    if paused:
        findings.append(f"{len(paused)} series paused: nothing for one and a half "
                        f"cadences, but not long enough to call stopped.")
    if stopped:
        worst = max(stopped, key=lambda s: s["monthly_cost_minor"])
        findings.append(
            f"{len(stopped)} series stopped, the largest being {worst['name']} at "
            f"{c.label(worst['median_amount_minor'])} {worst['cadence']}, last seen "
            f"{c.date_label(worst['last_seen'])}.")
    if mismatches["detected_not_flagged"]:
        findings.append(
            f"{len(mismatches['detected_not_flagged'])} payee(s) repeat on a "
            f"schedule but are not marked recurring, starting with "
            f"{mismatches['detected_not_flagged'][0]}.")
    if mismatches["flagged_not_detected"]:
        findings.append(
            f"{len(mismatches['flagged_not_detected'])} payee(s) are marked "
            f"recurring but show no regular spacing: "
            f"{', '.join(mismatches['flagged_not_detected'][:5])}.")
    if auto:
        findings.append(f"{len(auto)} payee(s) are collected by FAST or GIRO, which "
                        f"means somebody else pulls the money; the largest is "
                        f"{auto[0]['payee']} at {c.label(auto[0]['total_minor'])}.")

    caveats = [
        "A cadence is inferred from spacing alone, so a shop visited monthly by "
        "habit looks the same as a subscription.",
        f"A repeat under {c.label(MIN_AMOUNT_MINOR)}, and a weekly run of fewer than six "
        f"charges or shorter than eight weeks, is treated as habit rather than a "
        f"commitment, so a cheap thing bought often is not here, unless the rules "
        f"mark its payee recurring.",
        f"A hole longer than {MAX_GAP_FACTOR} times the cadence band ends a series; "
        f"what comes after it is measured on its own and the run with the most "
        f"payments on schedule is the one reported.",
        "A series billed to a card or an account outside these statements is "
        "invisible; only the payments that crossed these accounts are here.",
        f"Paused means nothing arrived within {PAUSE_FACTOR} cadences (and a month), "
        f"and stopped within {STOP_FACTOR} (and two months), before the last day the series' own account's "
        f"statements cover, not today.",
    ]
    return c.result(sys.modules[__name__], snapshot,
                    [c.source("Transactions", len(outflow),
                              "flow_class = External, outflow, bucket != Income, "
                              "not synthetic"),
                     c.source("Payees", len(snapshot["payees"]), "all"),
                     c.source("Transactions", len(pulled),
                              "flow_class = External, outflow, not synthetic, "
                              "bank_txn_type collected by FAST or GIRO")],
                    {"min_occurrences": MIN_OCCURRENCES, "fit": FIT,
                     "pause_factor": PAUSE_FACTOR, "stop_factor": STOP_FACTOR,
                     "pause_floor_days": PAUSE_FLOOR, "stop_floor_days": STOP_FLOOR,
                     "amount_band": AMOUNT_BAND, "tight_amount_band": TIGHT_BAND,
                     "tight_band_categories": list(TIGHT_CATEGORIES),
                     "max_gap_factor": MAX_GAP_FACTOR, "min_span_days": MIN_SPAN,
                     "merge_days": MERGE_DAYS, "min_amount_minor": MIN_AMOUNT_MINOR, "recent": RECENT,
                     "flagged_occurrences": FLAGGED_NEED,
                     "cadences": {n: [lo, hi, need]
                                  for n, lo, hi, _, need in CADENCES},
                     "as_of": end, "currency": "SGD"},
                    {"as_of": end, "series": series, "active": active, "paused": paused, "stopped": stopped,
                     "possible_auto_bills": auto,
                     "totals": {"monthly_fixed_cost_minor": monthly_fixed,
                                "monthly_committed_minor": monthly_committed,
                                "active": len(active), "paused": len(paused),
                                "stopped": len(stopped)},
                     "flag_mismatches": mismatches},
                    findings, caveats)
