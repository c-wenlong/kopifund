#!/usr/bin/env python3
"""The owner's own numbers beside Singapore reference figures.

`server/analytics/benchmarks.json` is written by a separate pass and is optional. When
it is missing this stat still reports every figure it computes, and says in a
caveat that there is nothing to compare them with. When it is there, each item
is matched to one of the owner's metrics by the words in its id, and the item's
source and url travel with the comparison so the reader can check it.

G20: a figure in SGD is integer cents under a `_minor` name, and any other
figure (months, a percent) keeps its bare name: a metric has `value_minor` or
`value`, a comparison `owner_minor` and `benchmark_minor` or `owner` and
`benchmark`, and `unit` says what either is measured in. The reference file
holds dollars, as published; `in_cents` turns an SGD item's `value` into
`value_minor` once, where the file is read (`stats.load_benchmarks`).

data:
  metrics     [{key, label, short, value_minor | value, unit, window, note, rows,
                thin, thin_reason, worse_when}]
  comparisons [{metric, label, short, owner_minor | owner,
                benchmark_minor | benchmark, unit, ratio, direction, thin,
                thin_reason, concern, benchmark_id, source, url, as_of,
                population, notes}]
  headline    one comparison per metric, the closest fitting reference
  headline_money the same rows restricted to the ones measured in SGD
  unmatched   the benchmark items no metric claimed
"""
import re
import sys

from finance.money import MINOR, label, stat_cents, to_cents

from . import common as c

ID = "benchmarks-sg"
TITLE = "Against Singapore references"
# G13: the card is a short list of the owner's figure against the typical one,
# a row per headline metric in this order, rather than a grouped bar chart that
# could only carry the rows measured in SGD. Metrics not named here follow.
ORDER = ("cash_months", "savings_rate", "monthly_spend", "food_spend",
         "transport_spend", "housing_spend")
CHART = {"type": "versus", "x": "short", "series": ["owner", "benchmark"],
         "source": "headline", "unit_field": "unit",
         "order_field": "metric", "order": list(ORDER),
         # G20: a row's money is cents under owner_minor and benchmark_minor; its
         # other figures are in the unit its unit_field names
         "unit": "sgd_minor"}

# What the card calls a metric whose label is longer than a row has room for.
SHORT = {"cash_months": "Months of cash", "monthly_spend": "All spending",
         "monthly_spend_6m": "All spending, last 6 months"}

# The one place this stat says which side of a reference is the worse one:
# fewer months of expenses in cash than the reference is a thinner buffer.
# Spending lines carry no such judgement; more or less than a survey average
# is a difference, not a verdict.
WORSE_WHEN = {"cash_months": "below"}

# The figures that are a monthly average of spending rows. With fewer rows than
# covered months, the average is what happened to be billed to these accounts
# (rent paid from an account Kopifund never sees, say), not what it cost.
ROW_AVERAGES = ("monthly_spend", "monthly_spend_6m", "housing_spend",
                "food_spend", "transport_spend")

# A benchmark item belongs to one of the owner's metrics only when its unit is
# comparable and its id says what it measures. Matching on words alone pulls in
# household counts, survey percentages and returns, none of which can sit beside
# a monthly figure in SGD, so the unit gate comes first and the specific
# categories are tried before the general spending line.
# The period has to be in the unit string. A bare "SGD", or "SGD per year", is
# rejected rather than guessed at, because the renderer reads a period-less
# amount as monthly and a yearly figure would land on the monthly axis.
MONTHLY_SGD = ("sgd per household per month", "sgd per household member per month",
               "sgd per month")

# The owner's savings rate is the investing transfer over everything that
# arrived. A reference quoted against take-home pay or after-tax income is a
# different denominator, and 27 per cent of one is not 27 per cent of the
# other, so the unit has to match exactly rather than merely be a percentage.
SAVINGS_RATE_UNIT = "per cent of monthly income"
# what the survey's transport covers: both halves of the split, and Transport
# itself in a record not yet split (finance.py split-transport)
TRANSPORT = ("Transport (public)", "Transport (private)", "Transport")

MATCHERS = (
    ("housing_spend", MONTHLY_SGD,
     ("housing", "rentals", "accommodation", "mortgage", "utilities")),
    ("food_spend", MONTHLY_SGD,
     ("food", "grocer", "hawker", "dining", "eating", "restaurant")),
    ("transport_spend", MONTHLY_SGD, ("transport", "commute", "mrt", "fare")),
    ("cash_months", ("months of expenses",), ("emergency", "expenses", "buffer")),
    ("savings_rate", (SAVINGS_RATE_UNIT,), ("saving", "save", "invest")),
    ("monthly_salary", ("sgd per month",),
     ("salary", "graduate", "median-income", "earnings", "starting-pay")),
    ("monthly_spend", MONTHLY_SGD,
     ("expenditure", "spend", "cost-of-living", "cost of living")),
)

# Metrics whose unit has to be identical, not merely of the same kind.
EXACT_UNIT = ("savings_rate",)

# Metrics that are what reached the bank. A gross reference is a different
# number by the whole of CPF and tax, so it is rejected rather than compared.
NET_METRICS = ("monthly_salary", "monthly_income")

# Two survey lines added together, because the owner's food figure is eating
# out plus groceries plus coffee and the survey splits that in two. Without
# this the headline held the owner's whole food bill against restaurant meals
# alone. The one-person total is always tried; the age band's own total only
# when the user has set an age band (`derived_specs`).
DERIVED = (
    {"id": "hes-2023-1person-food-total", "label": "Food, eating out and at home",
     "parts": ("hes-2023-1person-food-services", "hes-2023-1person-food-at-home")},
)

_BAND = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


# ---------------------------------------------------------------- cents (G20)

def is_money(unit):
    """Whether a figure in `unit` is money in SGD: its unit starts with SGD
    ("SGD per month", "SGD per household per month", "SGD per year")."""
    return str(unit or "").lower().startswith("sgd")


def in_cents(benchmarks):
    """The reference file with each SGD item's `value`, the published dollars,
    as integer cents under `value_minor`; every other item as it is. An item
    already in cents is left alone, so the stat passes whatever it is handed
    through here too and a caller's own dollar list reads the same."""
    if not benchmarks:
        return benchmarks

    def item(x):
        v = x.get("value")
        if ("value" + MINOR) in x or not is_money(x.get("unit")) \
                or isinstance(v, bool) or not isinstance(v, (int, float)):
            return x
        return {("value" + MINOR if k == "value" else k): (to_cents(v) if k == "value" else w)
                for k, w in x.items()}

    return {**benchmarks, "items": [item(x) for x in benchmarks.get("items") or []]}


def figure(obj, name="value"):
    """(the number, whether it is cents) of a metric's, a reference's or one
    side of a comparison's figure: `<name>_minor` when the dict has it, else
    `<name>`; (None, False) when it is not a number."""
    if name + MINOR in obj:
        v = obj[name + MINOR]
        return (v, True) if isinstance(v, int) and not isinstance(v, bool) else (None, False)
    v = obj.get(name)
    return (v, False) if isinstance(v, (int, float)) else (None, False)


def put(row, name, value, cents):
    """`row[name]`, or `row[name + "_minor"]` when the figure is cents."""
    row[name + MINOR if cents else name] = value
    return row


def shown(value, cents):
    """A figure for a finding: money as the app writes it (money.label), any
    other number as before."""
    return label(value) if cents else c.fmt(value)


def spend_cents(rows):
    """Positive money out in cents, signed: a refund inside a spend category
    subtracts. The record's own cents (`amount_sgd_minor`)."""
    return -c.sum_cents(stat_cents(r, "amount_sgd") or 0 for r in rows)


def income_cents(rows):
    return c.sum_cents(stat_cents(r, "amount_sgd") or 0 for r in rows)


def age_band():
    """[analytics] age_band in config.toml, the band the reference ids spell
    (`30-34` matches `hes-2023-age-30-34-...`), or "" for none. G10: the band is
    the user's, never a constant here; with none set, no reference is preferred
    for its age band."""
    band = c.text_setting("age_band").lower()
    return band if _BAND.match(band) else ""


def derived_specs(band=None):
    """DERIVED plus the age band's food total when a band is set."""
    band = age_band() if band is None else band
    if not band:
        return DERIVED
    return DERIVED + (
        {"id": f"hes-2023-age-{band}-food-total",
         "label": "Food, eating out and at home",
         "parts": (f"hes-2023-age-{band}-food-services",
                   f"hes-2023-age-{band}-food-at-home")},)


def basis_of(unit):
    """Whether a reference is quoted before or after the deductions.

    The owner's salary is what landed in the account. Every MOM and GES figure
    is gross, and gross is a fifth larger at these salaries before any tax.
    """
    return "gross" if "gross" in str(unit).lower() else "net"


def derive(items):
    """The reference list plus the totals the survey splits and this owner does
    not. Each derived item says what it was made of and carries its parts'
    source, so nothing is quoted that cannot be checked."""
    by_id = {x.get("id"): x for x in items}
    out = list(items)
    for spec in derived_specs():
        parts = [by_id.get(pid) for pid in spec["parts"]]
        if not all(parts):
            continue
        figures = [figure(x) for x in parts]
        # the parts are added only on one scale: all cents or all plain numbers
        if any(v is None for v, _ in figures) or len({cents for _, cents in figures}) > 1:
            continue
        cents = figures[0][1]
        total = sum(v for v, _ in figures)
        first = parts[0]
        out.append(put({"id": spec["id"]}, "value", total if cents else round(total, 2), cents) | {
            "unit": first.get("unit"), "population": first.get("population"),
            "as_of": first.get("as_of"), "source": first.get("source"),
            "url": first.get("url"), "derived_from": list(spec["parts"]),
            "notes": "Added here from " + " plus ".join(
                f"{x['id']} ({label(v) if cents else v})" for x, (v, _) in zip(parts, figures))
            + ", because the owner's food figure covers both."})
    return out


def owner_metrics(snapshot):
    """The figures a Singapore reference could sensibly be held against."""
    phase = c.latest_phase()
    months = [m for m in c.month_range(snapshot) if c.in_phase(m, phase)]
    cov = c.coverage(snapshot)
    months = [m for m in months if not cov[m]["partial"]] or months
    n = len(months) or 1
    spend = [r for r in c.spend_rows(snapshot) if r["month"] in months]
    income = [r for r in c.income_rows(snapshot) if r["month"] in months]
    invest = [r for r in c.investing_rows(snapshot) if r["month"] in months]

    def cat(*names):
        names = c.category_names(snapshot, *names)      # a renamed category still counts
        return c.div_cents(spend_cents([r for r in spend if r["category"] in names]), n)

    def cat_rows(*names):
        names = c.category_names(snapshot, *names)
        return len([r for r in spend if r["category"] in names])

    salary = c.category_names(snapshot, "Salary", "Freelance income")

    # G20: every SGD figure is cents a month, a total over the covered months
    total_spend = c.div_cents(spend_cents(spend), n)
    total_income = c.div_cents(income_cents(income), n)
    investing = c.div_cents(spend_cents(invest), n)
    last6 = months[-6:]
    spend6 = c.div_cents(spend_cents([r for r in spend if r["month"] in last6]),
                         len(last6) or 1)

    from . import savings_runway
    runway = savings_runway.run(snapshot)["data"]["summary"]

    window = f"{months[0]} to {months[-1]}" if months else ""
    return flag([
        {"key": "monthly_spend", "label": "Monthly spending", "value_minor": total_spend,
         "unit": "SGD per month", "window": window, "basis": "actual",
         "rows": len(spend),
         "note": f"{phase['name']} phase, {n} covered months, signed sum"},
        {"key": "monthly_spend_6m", "label": "Monthly spending, last 6 months",
         "value_minor": spend6, "unit": "SGD per month", "basis": "actual",
         "rows": len([r for r in spend if r["month"] in last6]),
         "window": f"{last6[0]} to {last6[-1]}" if last6 else "", "note": ""},
        {"key": "monthly_income", "label": "Monthly income", "value_minor": total_income,
         "unit": "SGD per month", "window": window, "basis": "net",
         "rows": len(income),
         "note": "everything that arrived, so net of anything deducted before it"},
        {"key": "monthly_salary", "label": "Monthly salary",
         "value_minor": cat_income(income, salary, n),
         "unit": "SGD per month", "window": window, "basis": "net",
         "rows": len([r for r in income if r["category"] in salary]),
         "note": "salary and freelance only, what reached the bank, averaged over "
                 "every month"},
        {"key": "housing_spend", "label": "Housing", "value_minor": cat("Rent"),
         "unit": "SGD per month", "window": window, "basis": "actual",
         "rows": cat_rows("Rent"),
         "note": f"only what is billed to these accounts: {cat_rows('Rent')} rows"},
        {"key": "food_spend", "label": "Food",
         "value_minor": cat("Eating out", "Groceries", "Coffee"),
         "unit": "SGD per month", "window": window, "basis": "actual",
         "rows": cat_rows("Eating out", "Groceries", "Coffee"),
         "note": "eating out, groceries and coffee together, which is what the "
                 "survey's food services plus food at home covers"},
        {"key": "transport_spend", "label": "Transport", "value_minor": cat(*TRANSPORT),
         "unit": "SGD per month", "window": window, "basis": "actual",
         "rows": cat_rows(*TRANSPORT), "note": "public and private together"},
        {"key": "savings_rate", "label": "Savings rate",
         "value": round(c.share(investing, total_income) * 100, 2),
         "unit": SAVINGS_RATE_UNIT, "window": window, "basis": "net",
         "rows": len(invest),
         "note": "the investing transfer over everything that arrived, so a "
                 "reference quoted against take-home or after-tax pay is a "
                 "different denominator and is not matched"},
        {"key": "cash_months", "label": "Months of expenses in cash",
         "value": runway["runway_6m"], "unit": "months", "basis": "actual",
         "rows": 0, "window": runway["as_of"] or "",
         "note": "at the last six months' rate, on the cash at the last month "
                 "every bank account printed"},
    ], {"monthly_spend_6m": len(last6)}, len(months))


def cat_income(rows, names, n):
    return c.div_cents(income_cents([r for r in rows if r["category"] in names]), n)


def thin_reason(metric, months):
    """Why a monthly average rests on too little to hold against a reference,
    or None.

    G13: a spending figure taken over fewer rows than it has covered months is
    not a monthly cost, so the card greys it out rather than comparing it.
    Only the averages of rows are judged (ROW_AVERAGES); months of cash and the
    savings rate come from balances and transfers.
    """
    if metric["key"] not in ROW_AVERAGES or not months or metric["rows"] >= months:
        return None
    return (f"rests on {metric['rows']} rows over {months} covered months, so it "
            f"is what these accounts were billed rather than what "
            f"{metric['label'].lower()} cost")


def flag(metrics, spans, months):
    """Stamp `short`, `thin`, `thin_reason` and `worse_when` on each metric.
    `spans` names a metric taken over other than the phase's `months`."""
    for m in metrics:
        m["short"] = SHORT.get(m["key"], m["label"])
        m["thin_reason"] = thin_reason(m, spans.get(m["key"], months))
        m["thin"] = m["thin_reason"] is not None
        m["worse_when"] = WORSE_WHEN.get(m["key"])
    return metrics


def match(item):
    """The metric key a benchmark item belongs to, or None.

    Two gates: the unit has to be the same kind of number, and the id or the
    population has to name the thing. Notes are deliberately not searched; they
    mention everything. A metric in EXACT_UNIT wants the same unit rather than
    the same kind of unit.
    """
    return claim(item)[0]


def claim(item):
    """(metric key or None, the reason it was refused).

    Kept apart from `match` so a reference that named a metric and was then
    turned away can say why, rather than sinking into the unmatched list beside
    the ones nothing here measures.
    """
    if figure(item)[0] is None:
        return None, "the value is not a number"
    unit = str(item.get("unit") or "").lower()
    text = f"{item.get('id', '')} {item.get('population', '')}".lower()
    for key, units, words in MATCHERS:
        if not any(w in text for w in words):
            continue
        if key in EXACT_UNIT:
            if unit != units[0]:
                return None, (f"{key} is measured in {units[0]} and this is in "
                              f"{item.get('unit')}")
            return key, ""
        if not any(unit.startswith(u) for u in units):
            continue
        if key in NET_METRICS and basis_of(unit) == "gross":
            return None, (f"{key} here is what reached the bank and this "
                          f"reference is gross")
        return key, ""
    return None, ""


def closeness(row, band=None):
    """How well a reference item fits one person, in their age band when one
    is set ([analytics] age_band).

    A total always beats one of its own components: restaurant meals are part
    of food services, food services is part of food, and holding the owner's
    whole food bill against one slice of the survey's is what made food look
    far above the reference. After that a one-person household is the
    closest thing the survey has to this owner, then the owner's age band when
    there is one, and among equally close items the broadest one wins.
    """
    band = age_band() if band is None else band
    key = (row["benchmark_id"] or "").lower()
    return ((row.get("component_of") is None) * 8
            + ("1person" in key) * 4 + bool(band and f"age-{band}" in key) * 3
            + ("median" in key) * 2 + ("per-member" in key) * 1,
            -len(key.split("-")))


def mark_components(comparisons):
    """Say, on each row, which other reference it is a slice of.

    Two ways to be a component: the id extends another matched id (`...-food-
    services-hawker` inside `...-food-services`), or a derived total names it
    as one of its parts.
    """
    part_of = {}
    for spec in derived_specs():
        for pid in spec["parts"]:
            part_of[pid] = spec["id"]
    by_metric = {}
    for row in comparisons:
        by_metric.setdefault(row["metric"], []).append(row)
    for rows in by_metric.values():
        ids = {r["benchmark_id"] for r in rows}
        for row in rows:
            key = row["benchmark_id"] or ""
            whole = part_of.get(key)
            if whole not in ids:
                whole = next((other for other in ids
                              if other and other != key
                              and key.startswith(other + "-")), None)
            row["component_of"] = whole
    return comparisons


def drop_household_rows(comparisons):
    """(kept, dropped): a multi-person household reference has no business beside
    one person once a one-person one has matched the same metric."""
    kept, dropped = [], []
    by_metric = {}
    for row in comparisons:
        by_metric.setdefault(row["metric"], []).append(row)
    for rows in by_metric.values():
        one_person = [r for r in rows if "1person" in (r["benchmark_id"] or "")]
        for row in rows:
            household = "household" in str(row["unit"]).lower()
            if one_person and household and row not in one_person:
                dropped.append(row)
            else:
                kept.append(row)
    kept.sort(key=lambda x: (x["label"], x["benchmark_id"] or ""))
    return kept, dropped


def pick_headline(comparisons):
    """One reference per metric, the closest fit, for the chart."""
    best, band = {}, age_band()
    for row in comparisons:
        current = best.get(row["metric"])
        if current is None or closeness(row, band) > closeness(current, band):
            best[row["metric"]] = row
    return sorted(best.values(), key=lambda x: x["label"])


def run(snapshot, benchmarks=None):
    # G20: dollars as published, unless the file was read in cents already
    given = list((in_cents(benchmarks) or {}).get("items", []))
    items = derive(given) if given else []
    metrics = owner_metrics(snapshot)
    by_key = {m["key"]: m for m in metrics}

    comparisons, unmatched, rejected = [], [], []
    for item in items:
        key, why = claim(item)
        metric = by_key.get(key)
        owner, owner_cents = figure(metric) if metric else (None, False)
        if not metric or owner is None:
            if why:
                rejected.append({"benchmark_id": item.get("id"),
                                 "unit": item.get("unit"), "reason": why})
            unmatched.append(item)
            continue
        value, value_cents = figure(item)
        # a ratio of like with like: cents over cents, or a number over a number
        ratio = (round((owner / (100 if owner_cents else 1)) / (value / (100 if value_cents else 1)), 2)
                 if value else None)
        direction = (None if ratio is None else
                     "above" if ratio > 1.05 else
                     "below" if ratio < 0.95 else "about the same as")
        comparisons.append(put(put({
            "metric": key, "label": metric["label"], "short": metric["short"]},
            "owner", owner, owner_cents), "benchmark", value, value_cents) | {
            "unit": item.get("unit") or metric["unit"],
            "basis": basis_of(item.get("unit")),
            "owner_basis": metric.get("basis", "net"),
            "ratio": ratio, "direction": direction,
            # G13: the card greys a thin figure and colours a concern; both are
            # decided here so the app never reads the findings' wording
            "thin": metric["thin"], "thin_reason": metric["thin_reason"],
            "concern": bool(direction and direction == metric["worse_when"]
                            and not metric["thin"]),
            "benchmark_id": item.get("id"), "source": item.get("source"),
            "url": item.get("url"), "as_of": item.get("as_of"),
            "population": item.get("population"), "notes": item.get("notes"),
            "derived_from": item.get("derived_from")})
    comparisons, household = drop_household_rows(comparisons)
    for row in household:
        rejected.append({"benchmark_id": row["benchmark_id"], "unit": row["unit"],
                         "reason": f"{row['metric']} already has a one-person "
                                   f"household reference and this one is not"})
        unmatched.append(put({"id": row["benchmark_id"], "unit": row["unit"]},
                             "value", *figure(row, "benchmark")) | {
                          "source": row["source"],
                          "url": row["url"], "as_of": row["as_of"],
                          "population": row["population"], "notes": row["notes"]})
    mark_components(comparisons)
    headline = pick_headline(comparisons)
    # The money rows alone, as the old grouped bar chart drew them. The card now
    # lists every headline row on its own scale (CHART), so this is kept for
    # whatever still reads it rather than for the app.
    money_rows = [x for x in headline if is_money(x["unit"])]

    def said(key):
        return shown(*figure(by_key[key]))

    findings = []
    if not items:
        findings.append(
            f"No benchmark file, so these are the owner's figures alone: "
            f"{said('monthly_spend')} a month of spending, "
            f"{said('monthly_income')} of income and "
            f"{by_key['savings_rate']['value']:.0f} per cent going to "
            f"investing.")
        findings.append(
            f"Housing is {said('housing_spend')} a month and "
            f"food {said('food_spend')}, which is the shape to "
            f"hold any Singapore reference against.")
    else:
        findings.append(f"{len(comparisons)} of {len(given)} reference numbers could "
                        f"be matched to a figure in this record, on a like-for-like "
                        f"unit, basis and population.")
        for x in headline[:4]:
            if x["direction"]:
                findings.append(
                    f"{x['label']}: {shown(*figure(x, 'owner'))} against "
                    f"{shown(*figure(x, 'benchmark'))} "
                    f"({x['unit']}), {x['direction']} {x['population']}, "
                    f"{x['source']} {x['as_of']}.")
    if unmatched:
        findings.append(f"{len(unmatched)} reference number(s) had no figure here to "
                        f"compare against and are listed unmatched.")
    if rejected:
        findings.append(f"{len(rejected)} reference number(s) named a figure here but "
                        f"were turned away for not being like for like, starting "
                        f"with {rejected[0]['benchmark_id']}: "
                        f"{rejected[0]['reason']}.")
    housing = by_key["housing_spend"]
    findings.append(f"Housing of {shown(*figure(housing))} a month rests on "
                    f"{housing['rows']} rows in the whole {c.latest_phase()['name']} "
                    f"phase, so it is what this account was billed rather than what "
                    f"housing cost.")

    caveats = list(c.no_benchmarks_caveat(given)) + [
        "Singapore reference figures are usually per household or per resident and "
        "this is one person, so a ratio is a rough indication rather than a gap.",
        "Where housing or food is not billed to these accounts, the owner's "
        "figures for them sit far below any national number for reasons that have "
        "nothing to do with frugality.",
        f"The owner's salary is what reached the bank and every MOM and GES figure "
        f"is gross, so those are listed as rejected rather than compared. Each row "
        f"carries the basis on both sides.",
        f"A one-person household reference wins over an age band one, and a total "
        f"wins over its own components, so the headline holds food against food "
        f"rather than against restaurant meals.",
    ]
    if len(items) > len(given):
        caveats.append(
            f"{len(items) - len(given)} reference number(s) are added up here rather "
            f"than published as such: " + ", ".join(
                x["id"] for x in items if x.get("derived_from")) + ". Each says in "
            "its notes what it was made of.")
    phase_rows = phase_row_count(snapshot)
    return c.result(sys.modules[__name__], snapshot,
                    [c.source("Transactions", phase_rows,
                              f"{c.SPEND_FILTER}, {c.latest_phase()['name']} phase, "
                              f"covered months only")],
                    {"phase": c.latest_phase()["name"], "currency": "SGD",
                     "benchmark_items": len(given),
                     "benchmark_items_derived": len(items) - len(given),
                     "savings_rate_unit": SAVINGS_RATE_UNIT},
                    {"metrics": metrics, "comparisons": comparisons,
                     "headline": headline, "headline_money": money_rows,
                     "unmatched": unmatched, "rejected": rejected},
                    findings, caveats,
                    benchmarks=[dict(x) for x in items] if items else None)


def phase_row_count(snapshot):
    """The rows the owner's figures are actually taken over."""
    phase = c.latest_phase()
    cov = c.coverage(snapshot)
    months = [m for m in c.month_range(snapshot) if c.in_phase(m, phase)]
    months = [m for m in months if not cov[m]["partial"]] or months
    return len([r for r in c.spend_rows(snapshot) if r["month"] in set(months)])
