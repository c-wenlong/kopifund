#!/usr/bin/env python3
"""Spend by category: per month, per phase, share of total, and the trend.

The trend is the last three full months against the six full months before
them, both as a monthly average, so a category that stopped shows a fall of 100
per cent and a category that only just started shows a rise with a small base.
A month that has only just begun (or any other partial month) is in neither
window: a few days of it would read as every category falling. A phase's
figure a month divides the phase's full months' spending by their count
(`common.average_months`).

data:
  categories [{category, category_group, group, need_class, rows, total_minor,
               per_month_minor, share, share_clamped, last3_per_month_minor,
               prior6_per_month_minor, change_minor, change_share, <phase>_per_month_minor}]
  months     [{month, total_minor, <category>_minor: spend, ...}]
  phases     [{name, from, to, months, full_months, total_minor, per_month_minor}]
  total_minor, covered_months

G20: money is integer cents under `<name>_minor`, added and averaged in cents
(common.spend_minor, div_cents); `share` and `change_share` stay fractions
(0.25 is a rise of 25 per cent).
"""
import sys

from . import common as c

ID = "category-breakdown"
TITLE = "Spend by category"
CHART = {"type": "bar", "x": "category", "series": ["per_month_minor"],
         "source": "categories", "unit": "sgd_minor"}

RISE = 2000    # S$20.00 a month: a change smaller than this is not called a rise or a fall


def run(snapshot, benchmarks=None):
    rows = c.spend_rows(snapshot)
    months = c.month_range(snapshot)
    counted = c.full_months(snapshot)
    by_cat = c.by(rows, lambda r: r["category"] or "(uncategorised)")
    meta = {x["name"]: x for x in snapshot["categories"]}

    # the last three full months against the six full months before them
    last3, prior6 = counted[-3:], counted[-9:-3]
    # each phase's months, and the full ones among them its averages divide by
    in_phase = {c.phase_key(p): [m for m in months if c.in_phase(m, p)] for p in c.phases()}
    averaged = {k: c.average_months(pm, counted) for k, pm in in_phase.items()}

    total_all, base_label = c.spend_base_minor(snapshot, rows=rows, months=counted)
    categories = []
    for name, crows in by_cat.items():
        kept = [r for r in crows if r["month"] in counted]
        total = c.spend_minor(kept)
        entry = {"category": name,
                 "category_group": meta.get(name, {}).get("category_group"),
                 "group": meta.get(name, {}).get("group"),
                 "need_class": meta.get(name, {}).get("need_class"),
                 "rows": len(kept), "total_minor": total,
                 "per_month_minor": c.div_cents(total, len(counted)),
                 "share": c.share(total, total_all),
                 "share_clamped": c.clamp_share(c.share(total, total_all)),
                 "last3_per_month_minor": c.div_cents(
                     c.spend_minor([r for r in crows if r["month"] in last3]), len(last3)),
                 "prior6_per_month_minor": c.div_cents(
                     c.spend_minor([r for r in crows if r["month"] in prior6]), len(prior6))}
        entry["change_minor"] = entry["last3_per_month_minor"] - entry["prior6_per_month_minor"]
        entry["change_share"] = (c.share(entry["change_minor"], entry["prior6_per_month_minor"])
                                 if entry["prior6_per_month_minor"] else None)
        for key, pm in averaged.items():
            ptotal = c.spend_minor([r for r in crows if r["month"] in pm])
            entry[f"{key}_per_month_minor"] = c.div_cents(ptotal, len(pm))
        categories.append(entry)
    categories.sort(key=lambda x: -x["total_minor"])

    top = [x["category"] for x in categories[:8]]
    month_rows = []
    for m in months:
        row = {"month": m, "total_minor": c.spend_minor([r for r in rows if r["month"] == m])}
        for name in top:
            row[f"{name}_minor"] = c.spend_minor([r for r in by_cat.get(name, []) if r["month"] == m])
        month_rows.append(row)

    phases = []
    for p in c.phases():
        pm, avg = in_phase[c.phase_key(p)], averaged[c.phase_key(p)]
        if not pm:
            continue
        phases.append({"name": p["name"], "from": pm[0], "to": pm[-1],
                       "months": len(pm), "full_months": len(set(pm) & set(counted)),
                       "total_minor": c.spend_minor([r for r in rows if r["month"] in pm]),
                       "per_month_minor": c.div_cents(
                           c.spend_minor([r for r in rows if r["month"] in avg]), len(avg))})

    findings = []
    if categories:
        first = categories[0]
        findings.append(f"{first['category']} is the largest category at "
                        f"{c.label(first['per_month_minor'])} a month, "
                        f"{first['share'] * 100:.0f} per cent {base_label}.")
        cum, n = 0, 0
        for x in categories:
            cum += x["total_minor"]
            n += 1
            if cum >= 0.8 * total_all:
                break
        findings.append(f"{n} categories carry 80 per cent {base_label}; the other "
                        f"{len(categories) - n} together are the remaining fifth.")
        risers = [x for x in categories
                  if x["change_share"] is not None and x["change_minor"] > RISE]
        risers.sort(key=lambda x: -x["change_minor"])
        if risers:
            r = risers[0]
            findings.append(f"{r['category']} is up the most lately: "
                            f"{c.label(r['last3_per_month_minor'])} a month over the last "
                            f"three full months against {c.label(r['prior6_per_month_minor'])} in the six "
                            f"before.")
        fallers = [x for x in categories
                   if x["change_share"] is not None and x["change_minor"] < -RISE]
        fallers.sort(key=lambda x: x["change_minor"])
        if fallers:
            f = fallers[0]
            findings.append(f"{f['category']} fell the most: "
                            f"{c.label(f['last3_per_month_minor'])} a month against "
                            f"{c.label(f['prior6_per_month_minor'])}.")

    caveats = [f"Every share here is {base_label}, the {len(counted)} months whose "
               f"statements cover every open bank account; a partial month never dilutes "
               f"a category's average, its trend or a phase's figure a month.",
               "A category with a refund larger than its charges in a window shows a "
               "negative figure; that is the signed sum working, not an error.",
               snapshot["meta"]["fx_note"]]
    caveats.extend(note for note in
                   (c.share_note_minor(x["total_minor"], total_all, f"{x['category']}'s share")
                    for x in categories) if note)
    return c.result(sys.modules[__name__], snapshot,
                    [c.source("Transactions", len(rows), c.SPEND_FILTER)],
                    {"from": months[0] if months else None,
                     "to": months[-1] if months else None,
                     "trend_window": {"recent": last3, "prior": prior6},
                     "currency": "SGD"},
                    {"categories": categories, "months": month_rows, "phases": phases,
                     "total_minor": total_all, "covered_months": len(counted)},
                    findings, caveats)
