#!/usr/bin/env python3
"""Cash month by month from the last complete month to the plan's end month.

Arithmetic on stated plans, not a forecast. What goes in:

  * pay as the owner states it in the plan (kopifund.db, or [plan] income in
    the data folder's config.toml): the income rows the Net worth page edits
    (G14), read month by month by server/analytics/income.py, the same reading the
    Net worth projection uses. Each row is prorated by calendar days inside a
    partial month, a month no row covers earns nothing, a row with no end date
    runs to the end month (growing by the plan's salary growth once a year when
    it is employment), a bonus lands in its month, and side income counts as
    income. With no plan there is no pay at all, so the projection shows what
    the cash does with nothing coming in;
  * employee CPF at 20 per cent of the CPF-liable pay, capped at the Ordinary
    Wage ceiling, plus the same rate on a bonus up to the year's Additional
    Wage ceiling; side income with CPF no pays none. Below the ceiling
    take-home is gross times 0.8. The employer's 17 per
    cent on the same
    liable wage is carried beside it as `cpf_employer`, and the two together as
    `cpf_total`, accumulating down the projection as `cpf_accrued`. None of the
    employer share touches cash, so the cash line is exactly what it was before
    those columns existed: it is savings the cash line never shows;
  * spending at the **median** of the last six covered months, not the mean.
    A month in that window can net a small fraction of a normal month because
    a large refund landed in it, and a mean lets that one month pull the whole
    projection down by thousands by the end. A month whose refunds beat `refund_limit` is
    restated at its gross charges as well, so the refund cannot flatter it
    twice;
  * when several of those six months were spent on a long trip, the
    median is a travelling rate. `spend_ex_short_trips` is the same median
    with the short trips' rows taken out. `spend_ex_all_trips`, the "home"
    rate, is what a month at home costs: the median of the last six
    covered months spent mostly at home (fewer than half their days on a
    trip), with every trip row and every Travel booking taken out. Months
    abroad are left out of it rather than counted as nearly free, and a
    flight or hotel paid weeks before its trip is left out too, because the
    planned trips are costed on top of this rate and would otherwise be paid
    twice;
  * everything else that leaves the bank: wallet moves and rows in no bucket
    ran at a steady monthly amount over the same window and were projected as zero. That
    is `other_outflow`. The repayments to the repayment payee ([analytics]
    repayment_payee, optionally narrowed by repayment_category) inside that
    figure are split out as `repayments` and default to zero, because they are
    not a standing cost. With no payee set nothing is split out;
  * trips the owner has planned ([plan.trips]): days times a per-day rate,
    the record's own short-trip rate unless the trip says otherwise, spread
    over the months the trip touches, plus flights as a one-off in the month
    they are paid. They sit on top of the base rate, and the base rate can
    be the trip-free "home" median rather than the window median
    ([plan] spend_rate);
  * the monthly investing transfer: the plan's investing steps (G14), each
    from its month on, or the old single `dca_amount`; none unless the plan
    states one;
  * an insurance premium, zero until the real number is known.

The projection runs to the plan's `to` month, or twelve months past the start
without one. Starting cash is the savings-runway closing balance at the last
month where every bank account printed a statement, so the projection never
starts from a balance that is five weeks old.

Every finding is built from the plan values actually present: a month with no
income is named only when the plan has pay with a gap in it, the investing
transfer only when there is one. The what-if extra income scenarios are the
user's own ([[analytics.income_scenarios]]); there are none by default.

G20: money is integer cents from end to end, under `<name>_minor` in the
params and the data, with no dollars beside it. The plan's dollars turn to
cents once, as the plan is read (`to_cents`); the window's rates are cents
sums, medians and means of the rows' `amount_sgd_minor` (`median_cents`,
`mean_cents`, `div_cents`, half up); a CPF share is its rate times the month's
liable pay in cents, rounded half up once. Nothing compounds: each month's
cash is the month before plus its net. The CPF shares stay fractions, and the
Ordinary Wage ceiling is server/finance/cpf_rules.py's.

data:
  months     [{month, gross_minor, cpf_employee_minor, cpf_employer_minor,
               cpf_total_minor, cpf_accrued_minor, take_home_minor,
               other_income_minor, spend_minor, trips_minor, trip_parts,
               one_offs_minor, one_off_parts, investing_minor, insurance_minor,
               other_outflow_minor, repayments_minor, net_minor, cash_minor}],
             with trip_parts [{name, days, cost_minor}] and one_off_parts
             [{label, amount_minor}]; the Plan page draws them
  spend_by_month  the window month by month ({month, signed_minor,
             gross_minor, refunds_minor, restated, spend_minor, basis}): which
             figure each month contributed
  spend_rates     [{name, value_minor, note}] every rate the scenarios use
  scenarios  [{name, ending_cash_minor, lowest_cash_minor, lowest_month}]
  starting_cash_minor, starting_month  where the line starts
  low_point  {month, cash_minor} the as-stated line's lowest month and its cash
             in integer cents (the earliest month on a tie), or None when
             nothing is projected; the chart marks it
  below_zero [month] the as-stated months whose cash is under zero, in order
"""
import calendar
import math
import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from finance import cpf_rules
from finance.money import from_cents, label, to_cents

from . import common as c, savings_runway, travel

ID = "forward-view"
TITLE = "Forward view"           # the result's title adds the plan's end month
CHART = {"type": "line", "x": "month", "series": ["cash_minor", "net_minor"],
         "source": "months", "unit": "sgd_minor"}

# G10: the neutral plan. Everything that is one person's (pay, the end month,
# the investing transfer) comes from their plan, never from here. G20: every
# amount is integer cents; the two CPF shares are fractions.
DEFAULTS = {
    "to": None,                     # None means twelve months past the start
    "income_segments": [],          # no pay unless the plan states it
    "cpf_employee_rate": 0.20,
    "cpf_employer_rate": 0.17,
    "cpf_ow_ceiling_minor": cpf_rules.OW_CEILING,
    "dca": False,
    "dca_amount_minor": 0,
    "insurance_premium_minor": 0,
    "other_income_minor": 0,
    "other_outflow_minor": None,    # None means the window mean, computed below
    "repayments_minor": 0,
    "spend_window_months": 6,
    "refund_limit_minor": 50_000,
    "spend_exclude_keys": [],       # rows named in [plan] spend_exclude_keys
}
DEFAULT_MONTHS = 12


# The rows that are repayment rather than cost: money sent back to a person,
# matched by payee name and, when one is set, by category. Both are personal,
# so they live in the data folder's config.toml under [analytics]
# repayment_payee and repayment_category, never in code.

def repayment_payee():
    return c.text_setting("repayment_payee")


def repayment_category():
    return c.text_setting("repayment_category")


def income_scenarios():
    """[[analytics.income_scenarios]]: what-if extra monthly income the user
    wants priced beside the plan, each `{label, monthly_minor}`; the config
    holds `monthly` in dollars, turned to cents here. None by default; an
    entry without a label or a positive amount is skipped."""
    raw = c.setting("income_scenarios", [])
    out = []
    for s in raw if isinstance(raw, list) else ():
        if not isinstance(s, dict):
            continue
        name, monthly = s.get("label"), s.get("monthly")
        if (not isinstance(name, str) or not name.strip() or isinstance(monthly, bool)
                or not isinstance(monthly, (int, float)) or not math.isfinite(monthly)
                or monthly <= 0):
            continue
        cents = to_cents(monthly)
        if cents > 0:
            out.append({"label": name.strip(), "monthly_minor": cents})
    return out


def amount_words(cents):
    """250000 cents as `S$2,500` and 31250 as `S$312.50`: a whole amount
    without its cents, anything else as `label` writes it."""
    if cents < 0 or cents % 100:
        return label(cents)
    return f"S${cents // 100:,}"


_COUNTS = ("no", "one", "two", "three", "four", "five", "six", "seven", "eight",
           "nine", "ten", "eleven", "twelve")


def count_words(n):
    return _COUNTS[n] if 0 <= n < len(_COUNTS) else str(n)


# Where the owner's plan is read from. Tests point this at a config without a
# [plan] section so the defaults above stay the fixture's ground truth.
from finance.paths import config_path  # noqa: E402
PLAN_CONFIG = config_path()
_DEFAULT_PLAN_CONFIG = PLAN_CONFIG


def _plan_from_db():
    """The plan kopifund.db holds, or None when there is no record or no plan."""
    try:
        from finance import db
        if not db.db_path().exists():
            return None
        conn = db.connect()
        try:
            return db.load_plan(conn) or None
        finally:
            conn.close()
    except Exception:  # an unreadable record falls back to the config, never breaks a stat
        return None


def plan_params(path=None):
    """The plan as parameter overrides, or {}: from kopifund.db when it holds
    one (G4; the app edits it from G8), else [plan] in config.toml. A test
    passes `path` to read one config file and nothing else."""
    import tomllib
    plan = None
    if path is None and PLAN_CONFIG == _DEFAULT_PLAN_CONFIG:
        plan = _plan_from_db()
    if plan is None:
        cfg = Path(path or PLAN_CONFIG)
        try:
            plan = tomllib.loads(cfg.read_text()).get("plan")
        except (OSError, tomllib.TOMLDecodeError):
            plan = None
    return params_from_plan(plan)


def _cents_or_none(x):
    return None if x is None or x == "" else to_cents(x)


def params_from_plan(plan):
    """A plan, in dollars as `db.load_plan` or [plan] holds it, as parameter
    overrides in cents, or {}. G20: each amount turns to cents here, once."""
    if not plan:
        return {}
    out = {}
    if "to" in plan:
        out["to"] = plan["to"]
    from finance import planning
    steps = [{"from": st["from"], "amount_minor": to_cents(st["amount"])}
             for st in planning.investing_steps(plan) if st.get("amount") not in (None, "")]
    if steps:
        out["investing_steps"] = steps
        out["dca"] = any(st["amount_minor"] > 0 for st in steps)
        out["dca_amount_minor"] = steps[0]["amount_minor"]
    settings = planning.projection_settings(plan)
    out["salary_growth_pct"] = settings["salary_growth_pct"]
    for src, dst in (("insurance", "insurance_premium_minor"), ("other_income", "other_income_minor"),
                     ("repayments", "repayments_minor")):
        if src in plan:
            out[dst] = to_cents(plan[src])
    if "cpf_employer_rate" in plan:
        out["cpf_employer_rate"] = float(plan["cpf_employer_rate"])
    if "spend_rate" in plan:
        out["spend_rate"] = str(plan["spend_rate"])
    if plan.get("spend_exclude_keys"):
        out["spend_exclude_keys"] = [str(k).split()[0] for k in plan["spend_exclude_keys"]]
    if plan.get("trips"):
        out["trips"] = [
            {"name": t.get("name", "trip"), "from": str(t["from"]), "to": str(t["to"]),
             "per_day_minor": _cents_or_none(t.get("per_day")),
             "flights_minor": to_cents(t.get("flights") or 0),
             "flights_month": str(t.get("flights_month", str(t["from"])[:7])),
             "note": t.get("note", "")}
            for t in plan["trips"]]
    if plan.get("one_offs"):
        out["one_offs"] = [
            {"month": str(o["month"])[:7], "amount_minor": to_cents(o["amount"]),
             "label": o.get("label", "one-off")}
            for o in plan["one_offs"]]
    if plan.get("income"):
        # G14: the rows in the shape the Net worth page saves; an old segment
        # reads as employment with its note as its name
        out["income_segments"] = [
            {**{k: v for k, v in row.items() if k != "gross"},
             "from": str(row["from"]), "to": str(row["to"]) if row["to"] else None,
             "gross_minor": to_cents(row["gross"]), "note": row["name"]}
            for row in planning.income_rows(plan)]
    return out


def pay_for(p):
    """The plan's pay as server/analytics/income.py reads it, shared with the Net
    worth projection so the two pages count the same money. A segment's
    `gross_minor` goes back to exact dollars only because that is the shape a
    plan's row has; income.py turns it into the same cents again."""
    from analytics import income
    rows = [{**{k: v for k, v in s.items() if k != "gross_minor"}, "gross": from_cents(s["gross_minor"])}
            for s in p.get("income_segments") or ()]
    return income.Pay(income.rows({"income": rows}), p.get("salary_growth_pct") or "0")


def investing_at(p, month):
    """The investing transfer in `month`, in cents: the latest step that has
    started, or the single `dca_amount_minor` when the plan has no steps."""
    if not p["dca"]:
        return 0
    steps = p.get("investing_steps")
    if not steps:
        return p["dca_amount_minor"]
    cents = 0
    for st in sorted(steps, key=lambda x: x["from"] or ""):
        if st["from"] is None or st["from"] <= month:
            cents = st["amount_minor"]
    return cents


def month_trip_cost(month, trips, per_day_default):
    """What the planned trips cost in a month, in cents: each trip's days that
    fall in the month times its per-day rate (`per_day_minor`, else the
    `per_day_default` cents), plus its flights (`flights_minor`) when this is
    the month they are paid. Returns (total, [{name, days, cost_minor}])."""
    y, m = int(month[:4]), int(month[5:7])
    first, last = date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1])
    total, parts = 0, []
    for t in trips or ():
        a = max(first, date.fromisoformat(t["from"][:10]))
        b = min(last, date.fromisoformat(t["to"][:10]))
        days = (b - a).days + 1 if a <= b else 0
        rate = t.get("per_day_minor") if t.get("per_day_minor") is not None else per_day_default
        cost = days * rate
        if t.get("flights_month", t["from"][:7]) == month:
            cost += t.get("flights_minor") or 0
        if cost:
            total += cost
            parts.append({"name": t["name"], "days": days, "cost_minor": cost})
    return total, parts


def month_one_offs(month, one_offs):
    """What the one-off payments in the plan cost in a month, in cents: a bill,
    a deposit, a purchase that is neither the base rate nor a trip. Returns
    (total, [{label, amount_minor}])."""
    parts = [{"label": o.get("label", "one-off"), "amount_minor": o["amount_minor"]}
             for o in one_offs or () if str(o["month"])[:7] == month]
    return sum(x["amount_minor"] for x in parts), parts


def month_gross(month, segments):
    """(gross, cpf-liable gross) in cents a month earns from the plan's income
    rows, each prorated by the calendar days of the month it covers, bonuses
    and side income included. Rows that overlap add up, which is how a raise
    mid-month is expressed as two. G14: read through server/analytics/income.py."""
    pm = pay_for({"income_segments": segments}).at(month)
    return pm["income"], pm["ow"] + pm["aw_liable"]


# What each parameter means, keyed by its name without `_minor`: a note is
# text, never money, so it never sits under a money field's name.
NOTES = {
    "income_segments": "the plan's dated pay; a month no segment covers earns "
                       "nothing, and with no segments nothing is earned at all",
    "insurance_premium": "zero until a policy premium and frequency are set in the "
                         "plan; nothing in this data sizes it",
    "cpf_employee_rate": "the employee share for a member aged 55 or under",
    "cpf_employer_rate": "the employer share for a member aged 55 or under, paid "
                         "on top of gross rather than out of it, so it never "
                         "touches the cash line and is savings the cash line "
                         "never shows",
    "cpf_ow_ceiling": "the Ordinary Wage ceiling (server/finance/cpf_rules.py); gross "
                      "above it attracts no further CPF",
    "other_income": "income outside the pay segments is deliberately not "
                    "assumed; set this to include it",
    "other_outflow": "everything leaving the bank that is neither spend nor the "
                     "investing transfer: wallet moves, small person to person "
                     "payments and rows still in no bucket, at the window's mean, "
                     "with the repayments to the repayment payee taken out",
    "repayments": "zero: money to the configured repayment payee is not "
                  "treated as a monthly cost. Set this to the monthly figure if "
                  "repayment resumes",
    "spend": "the median of the window, so one refunded month cannot set the rate",
    "spend_ex_all_trips": "what a month at home costs: the median of the last six "
                          "months spent mostly at home, with every trip row "
                          "and every Travel booking removed",
    "spend_ex_short_trips": "the same median with only the short trips removed, "
                            "so a long stay abroad still counts as living",
    "spend_exclude_keys": "rows the owner has named as one-off purchases, left out "
                          "of every rate above; which purchase was a one-off is a "
                          "judgement the record cannot make",
}


def _share(gross, rate, ceiling, additional):
    """`rate`, a fraction read as it prints, of the Ordinary Wage `gross` up to
    the `ceiling` plus the Additional Wage, all cents, rounded half up once."""
    liable = min(max(gross, 0), ceiling) + max(additional, 0)
    return cpf_rules.cents(Decimal(liable) * Decimal(str(rate)))


def cpf_employee(gross, rate, ceiling, additional=0):
    """The employee's own CPF contribution in cents, on the Ordinary Wage
    `gross` up to the `ceiling` plus the bonus `additional` the year's AW
    ceiling has already capped, all cents. Only wages up to the ceiling attract
    it, which is the whole of the ceiling logic at these salary levels."""
    return _share(gross, rate, ceiling, additional)


def cpf_employer(gross, rate, ceiling, additional=0):
    """The employer's CPF contribution on the same liable wage and the same
    ceiling, in cents. It is paid on top of gross rather than taken out of it,
    so it changes no cash figure here: it is only ever added to the CPF columns."""
    return _share(gross, rate, ceiling, additional)


def trip_lengths(snapshot):
    """{trip name: days}, so a stay can be told from a holiday."""
    return {t["name"]: (c.days_between(t["start"], t["end"]) + 1
                        if t["start"] and t["end"] else None)
            for t in snapshot["trips"]}


def excluded(keys):
    """A test for the rows the owner has named as one-off purchases.

    A month's spending is read as what living costs, so a single large thing
    bought once (an appliance, a course) makes the rate say a month costs
    more than it does. The keys live in the data folder's config.toml under [plan]
    spend_exclude_keys, never in code: which purchase was a one-off is the
    owner's judgement, and the record cannot tell.
    """
    keys = set(keys or ())
    def test(row):
        return bool(keys) and row.get("key") in keys
    return test


def either(*tests):
    """True when any of the tests is: how the excluded keys ride along with
    the trip filter on the trip-free rates."""
    tests = [t for t in tests if t is not None]
    return lambda row: any(t(row) for t in tests)


def away_days(snapshot):
    """{month: days on a trip}, each day counted once however many trips
    cover it: side trips can sit inside a long stay abroad."""
    days = set()
    for t in snapshot["trips"]:
        if not t.get("start") or not t.get("end"):
            continue
        d, end = date.fromisoformat(t["start"]), date.fromisoformat(t["end"])
        while d <= end:
            days.add(d)
            d += timedelta(days=1)
    out = {}
    for d in days:
        m = d.strftime("%Y-%m")
        out[m] = out.get(m, 0) + 1
    return out


def home_months(snapshot, months):
    """The months in `months` spent mostly at home: fewer than half their days
    on a trip. A month abroad holds almost none of what living at home costs,
    so counting it would read as a cheap month at home."""
    away = away_days(snapshot)
    return [m for m in months
            if away.get(m, 0) * 2 < calendar.monthrange(int(m[:4]), int(m[5:7]))[1]]


def is_travel(row, names=("Travel",)):
    """A booking in the Travel category: a flight, a hotel, a ferry. It is
    never what a month at home costs, whether or not it is pinned to a trip.
    `names` is what the record calls Travel now (c.category_names)."""
    return row.get("category") in names


def on_a_trip(snapshot, short_only=False):
    """A test for rows that were spent away from home.

    `short_only` keeps the long stays in: a stay of a couple of months abroad
    is somewhere lived in, and the food and transport of living there is the
    going rate for those months whatever else is true.
    """
    days = trip_lengths(snapshot)
    limit = travel.long_stay_days()
    def test(row):
        name = row["trip"]
        if not name:
            return False
        length = days.get(name)
        if short_only:
            return length is not None and length <= limit
        return True
    return test


def spend_window(snapshot, months, limit, drop=None):
    """The window month by month, in cents: what it netted, what it charged,
    what is used. `limit` is cents too.

    A month is used at its signed total, which is what it cost. A month whose
    refunds add up to more than `limit` is used at its gross charges instead:
    a refund that size is almost always a reversal of something bought in
    another month, and letting it stand makes the month look like a quarter of
    what living cost.

    `drop` takes rows out before any of that, which is how the trip-free rates
    are built: the refunds are then recomputed over what is left, so a refund
    on a trip cannot restate a month it is no longer part of.
    """
    rows = c.spend_rows(snapshot)
    if drop is not None:
        rows = [r for r in rows if not drop(r)]
    out = []
    for m in months:
        mrows = [r for r in rows if r["month"] == m]
        signed = c.spend_minor(mrows)
        gross = sum(-c.sgd_minor(r) for r in mrows if c.sgd_minor(r) < 0)
        refunds = sum(c.sgd_minor(r) for r in mrows if c.sgd_minor(r) > 0)
        restated = refunds > limit
        out.append({"month": m, "signed_minor": signed, "gross_minor": gross,
                    "refunds_minor": refunds, "restated": restated,
                    "spend_minor": gross if restated else signed,
                    "basis": "gross charges" if restated else "signed total"})
    return out


def other_outflow(snapshot, months):
    """(repayments, other, rows): the mean monthly outflow in cents that is
    neither spend nor the investing transfer, split into what was repayment and
    what was not.

    External, not synthetic, money out, and either in no bucket at all or in a
    Transfer bucket that is not an investment. It is real money leaving the
    bank and the projection used to treat all of it as zero. The repayments to
    the configured repayment payee (in the configured repayment category, when
    one is set) are the least like a monthly cost, so they are counted apart
    and default to zero in the projection. With no payee set, none are.
    """
    if not months:
        return 0, 0, []
    keep = set(months)
    rows = [r for r in c.external(snapshot)
            if r["month"] in keep and not r["synthetic"] and c.sgd_minor(r) < 0
            and (r["bucket"] is None
                 or (r["bucket"] == c.TRANSFER_BUCKET
                     and r["need_class"] != "Investment"))]
    payee, category = repayment_payee(), repayment_category()
    repaid = [r for r in rows if payee and r["payee"] == payee
              and (not category or r["category"] == category)]
    other = [r for r in rows if r not in repaid]
    return (c.div_cents(c.spend_minor(repaid), len(months)),
            c.div_cents(c.spend_minor(other), len(months)), rows)


def phase_rate(snapshot):
    """(rate, months): the latest phase's own spending average, in cents.

    The same figure the monthly-cashflow stat prints, partial months included,
    over the whole phase rather than six months. It is the floor of the range: a rate
    taken over long enough to have the quiet months in it as well.
    """
    phase = c.latest_phase()
    months = [m for m in c.month_range(snapshot) if c.in_phase(m, phase)]
    rows = [r for r in c.spend_rows(snapshot) if r["month"] in set(months)]
    return c.div_cents(c.spend_minor(rows), len(months)), len(months)


# the money figures of a projected month, each in cents as `<name>_minor`
MONTH_MONEY = ("gross", "cpf_employee", "cpf_employer", "cpf_total", "cpf_accrued", "take_home", "other_income",
               "spend", "trips", "one_offs", "investing", "insurance", "other_outflow", "repayments", "net", "cash")


def project(p, start_month, cash):
    """The month-by-month cash line for one set of parameters, from `cash`
    cents at the end of `start_month`. Every figure is integer cents: a CPF
    share rounds half up once, nothing else rounds, and nothing compounds."""
    months = []
    accrued = 0
    if not start_month or not p.get("to"):
        return months
    pay = pay_for(p)
    employee_rate, employer_rate = p["cpf_employee_rate"], p.get("cpf_employer_rate", 0.0)
    ceiling = p["cpf_ow_ceiling_minor"]
    for m in c.months_between(c.add_months(start_month, 1), p["to"]):
        pm = pay.at(m)
        gross, ow, aw = pm["income"], pm["ow"], pm["aw_liable"]
        # the Ordinary Wage up to its ceiling, and the bonus up to the AW ceiling
        cpf = cpf_employee(ow, employee_rate, ceiling, aw)
        employer = cpf_employer(ow, employer_rate, ceiling, aw)
        accrued += cpf + employer
        take_home = gross - cpf
        investing = investing_at(p, m)
        trips, trip_parts = month_trip_cost(m, p.get("trips"), p.get("trip_per_day_minor", 0))
        one_offs, one_off_parts = month_one_offs(m, p.get("one_offs"))
        net = (take_home + p["other_income_minor"] - p["spend_minor"] - trips - one_offs - investing
               - p["insurance_premium_minor"] - p["other_outflow_minor"] - p["repayments_minor"])
        cash += net
        months.append({"month": m, "gross_minor": gross, "cpf_employee_minor": cpf,
                       "cpf_employer_minor": employer, "cpf_total_minor": cpf + employer,
                       "cpf_accrued_minor": accrued,
                       "trips_minor": trips, "trip_parts": trip_parts,
                       "one_offs_minor": one_offs, "one_off_parts": one_off_parts,
                       "take_home_minor": take_home, "other_income_minor": p["other_income_minor"],
                       "spend_minor": p["spend_minor"], "investing_minor": investing,
                       "insurance_minor": p["insurance_premium_minor"],
                       "other_outflow_minor": p["other_outflow_minor"],
                       "repayments_minor": p["repayments_minor"], "net_minor": net,
                       "cash_minor": cash})
    return months


def low_point(months):
    """The projection's lowest month as {month, cash_minor}, or None. The
    earliest month wins a tie, so a flat line marks where it first got there.
    G13: the chart marks this point, so the figure is data, never read out of
    a finding."""
    if not months:
        return None
    low = min(months, key=lambda x: x["cash_minor"])
    return {"month": low["month"], "cash_minor": low["cash_minor"]}


def run(snapshot, benchmarks=None, params=None):
    p = dict(DEFAULTS)
    p.update(plan_params())
    p.update(params or {})

    runway = savings_runway.run(snapshot)["data"]["summary"]
    start_month = (runway["as_of_complete"] or runway["as_of"]
                   or c.last_month(snapshot))
    # G20: savings-runway's cash, in cents
    cash = c.stat_cents(runway, "cash") or 0
    if not p.get("to") and start_month:
        p["to"] = c.add_months(start_month, DEFAULT_MONTHS)
    payee = repayment_payee()

    phase = c.latest_phase()
    cov = c.coverage(snapshot)
    in_reach = [m for m in c.month_range(snapshot)
                if c.in_phase(m, phase) and (not start_month or m <= start_month)]
    # With no statement coverage at all, every month is partial and a strict
    # filter leaves no window and a spend rate of zero, which projects a rising
    # cash line out of nothing. Fall back to the months themselves.
    covered = [m for m in in_reach if not cov[m]["partial"]] or in_reach
    window = covered[-p["spend_window_months"]:]
    skip = excluded(p["spend_exclude_keys"])
    limit = p["refund_limit_minor"]
    by_month = spend_window(snapshot, window, limit, drop=skip)
    values = [x["spend_minor"] for x in by_month]

    ex_short = spend_window(snapshot, window, limit,
                            drop=either(skip, on_a_trip(snapshot, short_only=True)))
    ex_trips = spend_window(snapshot, window, limit,
                            drop=either(skip, on_a_trip(snapshot)))
    trip_months = [x["month"] for x, y in zip(by_month, ex_trips)
                   if x["spend_minor"] != y["spend_minor"]]
    # the home rate reads the months spent at home, not the last six whatever
    # they were, and leaves out every trip row and every Travel booking
    home = home_months(snapshot, covered)[-p["spend_window_months"]:] or window
    travel_names = c.category_names(snapshot, "Travel")
    ex_all = spend_window(snapshot, home, limit,
                          drop=either(skip, on_a_trip(snapshot), lambda row: is_travel(row, travel_names)))
    trips_in_window = sorted({r["trip"] for r in c.spend_rows(snapshot)
                              if r["month"] in set(window) and r["trip"]})

    p["spend_median_minor"] = c.median_cents(values)
    p["spend_mean_minor"] = c.mean_cents(values)
    p["spend_ex_all_trips_minor"] = c.median_cents([x["spend_minor"] for x in ex_all])
    p["spend_ex_short_trips_minor"] = c.median_cents([x["spend_minor"] for x in ex_short])
    p["spend_ex_trips_minor"] = p["spend_ex_all_trips_minor"]
    p["spend_home_window"] = home
    p["trip_months_in_window"] = trip_months
    p["trips_in_window"] = trips_in_window
    phase_spend, phase_months = phase_rate(snapshot)
    p["spend_phase_rate_minor"] = phase_spend
    p["spend_phase_months"] = phase_months
    # `spend_window` names the months taken at their signed total; a month whose
    # refunds forced the gross figure is named separately, so a reader can see
    # which figure each month contributed rather than assuming all six netted.
    p["spend_window"] = [x["month"] for x in by_month if not x["restated"]]
    p["spend_window_restated"] = [x for x in by_month if x["restated"]]
    p["spend_window_all"] = [x["month"] for x in by_month]
    repaid_rate, other_rate, other_rows = other_outflow(snapshot, window)
    p["other_outflow_observed"] = {"repayments_minor": repaid_rate, "other_minor": other_rate,
                                   "total_minor": repaid_rate + other_rate}
    if p["other_outflow_minor"] is None:
        p["other_outflow_minor"] = other_rate
    p["starting_cash_minor"] = cash
    p["starting_month"] = start_month
    # the base rate the owner asked for, and the per-day rate planned trips
    # default to: the record's own short-trip figure
    base = {"window": p["spend_median_minor"], "mean": p["spend_mean_minor"],
            "home": p["spend_ex_all_trips_minor"], "phase": phase_spend}
    p["spend_rate"] = p.get("spend_rate") or "window"
    p["spend_minor"] = base.get(p["spend_rate"], p["spend_median_minor"])
    short = travel.run(snapshot)["data"]["summary"].get("excluding_long_stays") or {}
    # G20: travel's rate a day, in cents
    p["trip_per_day_minor"] = c.stat_cents(short, "spend_per_day") or 0
    p.setdefault("trips", [])
    p.setdefault("one_offs", [])
    p["one_offs_total_minor"] = sum(o["amount_minor"] for o in p["one_offs"])
    p["trips_planned_total_minor"] = sum(
        month_trip_cost(m, p["trips"], p["trip_per_day_minor"])[0]
        for m in c.months_between(c.add_months(start_month, 1), p["to"])) if start_month else 0

    months = project(p, start_month, cash)
    # The months with nothing coming in, read off the projection rather than
    # assumed: named only when the plan has pay and these are the gaps in it.
    earning = [m for m in months if m["gross_minor"] or m["other_income_minor"]]
    p["zero_income_months"] = ([m["month"] for m in months
                                if not m["gross_minor"] and not m["other_income_minor"]]
                               if earning else [])
    # What the job puts into CPF over the same months. The employee half is
    # already off the cash line as take-home; the employer half never touched
    # the cash line at all, so neither figure changes a balance here and both
    # are savings the projection would otherwise not show.
    p["cpf_employee_total_minor"] = sum(m["cpf_employee_minor"] for m in months)
    p["cpf_employer_total_minor"] = sum(m["cpf_employer_minor"] for m in months)
    p["cpf_accrued_total_minor"] = months[-1]["cpf_accrued_minor"] if months else 0

    rates = [
        {"name": "window median", "value_minor": p["spend_median_minor"],
         "note": f"the {len(window)} month window, refunded months restated"},
        {"name": "window mean", "value_minor": p["spend_mean_minor"], "note": "the same window"},
        {"name": "home: months in Singapore, no trips or bookings",
         "value_minor": p["spend_ex_all_trips_minor"],
         "note": f"the {len(home)} months {c.months_label(home)}"},
        {"name": "window median excluding short trips only",
         "value_minor": p["spend_ex_short_trips_minor"],
         "note": f"a stay over {travel.long_stay_days()} days still counts as living"},
        {"name": f"{phase['name']} {phase_months} month rate",
         "value_minor": phase_spend, "note": "the whole phase, partial months included"},
    ]

    # Each scenario is offered only when the plan or the settings give it
    # something to change: pausing an investing transfer there is none of
    # changes nothing, and an extra income is the user's own what-if.
    what_ifs = [("as stated", {}),
                ("without the planned trips", {"trips": []}),
                ("without the one-off payments", {"one_offs": []}),
                # the window rates already contain travelling, so the
                # planned trips are not stacked on top of them
                ("spend at window median", {"spend_minor": p["spend_median_minor"], "trips": []}),
                ("spend at window mean", {"spend_minor": p["spend_mean_minor"], "trips": []}),
                ("spend at the home rate",
                 {"spend_minor": p["spend_ex_all_trips_minor"]}),
                (f"spend at the {phase['name']} {phase_months}-month "
                 f"rate", {"spend_minor": phase_spend})]
    if p["dca"] and p["dca_amount_minor"]:
        what_ifs.append(("investing paused", {"dca": False}))
    if payee:
        what_ifs.append(("with the repayments resuming",
                         {"repayments_minor": p["other_outflow_observed"]["repayments_minor"]}))
    for extra in income_scenarios():
        what_ifs.append((f"with {extra['label']} at {label(extra['monthly_minor'])} a month",
                         {"other_income_minor": p["other_income_minor"] + extra["monthly_minor"]}))
    scenarios = []
    for name, over in what_ifs:
        alt = months if not over else project({**p, **over}, start_month,
                                              p["starting_cash_minor"])
        low = min(alt, key=lambda x: x["cash_minor"]) if alt else None
        scenarios.append({"name": name,
                          "ending_cash_minor": alt[-1]["cash_minor"] if alt else None,
                          "lowest_cash_minor": low["cash_minor"] if low else None,
                          "lowest_month": low["month"] if low else None})

    findings = []
    if not months:
        findings.append("This snapshot carries no dated transactions and no closing "
                        "balance, so there is nothing to project forward from.")
    else:
        by_name = {x["name"]: x for x in scenarios}
        three = [("at the home rate",
                  "spend at the home rate",
                  p["spend_ex_all_trips_minor"]),
                 ("at the window's mean", "spend at window mean", p["spend_mean_minor"]),
                 ("at its median", "spend at window median", p["spend_median_minor"])]
        three.sort(key=lambda x: -by_name[x[1]]["ending_cash_minor"])
        findings.append(
            f"Starting from {label(p['starting_cash_minor'])} at {c.month_label(start_month)}, cash "
            f"ends by {c.month_label(p['to'])} at "
            + ", ".join(f"{label(by_name[key]['ending_cash_minor'])} {words} "
                        f"({label(rate)} a month)" for words, key, rate in three)
            + ".")
        if trip_months:
            findings.append(
                f"{len(trip_months)} of the {len(window)} window months were spent "
                f"away: {c.months_label(trip_months)} carried "
                f"{', '.join(trips_in_window)}. The median of the window is "
                f"therefore a travelling rate, and the trip-free median is "
                f"{label(p['spend_ex_all_trips_minor'])}; the "
                f"{phase['name']} phase's own {phase_months} month rate is "
                f"{label(phase_spend)}, which is the floor of that range.")
        low = min(months, key=lambda x: x["cash_minor"])
        findings.append(f"As stated (the {p['spend_rate']} rate of {label(p['spend_minor'])}"
                        + (f" plus {len(p['trips'])} planned trip(s) costing "
                           f"{label(p['trips_planned_total_minor'])} in all" if p["trips"] else "")
                        + (f" plus {len(p['one_offs'])} one-off payment(s) of "
                           f"{label(p['one_offs_total_minor'])} in all" if p["one_offs"] else "")
                        + f") the low point is {label(low['cash_minor'])} in {c.month_label(low['month'])} "
                        f"and {c.month_label(p['to'])} ends at "
                        f"{label(months[-1]['cash_minor'])}.")
        if p["trips"]:
            findings.append("Planned trips, on top of the base rate: " + "; ".join(
                f"{t['name']} {c.range_label(t['from'], t['to'])} at "
                f"{label(t['per_day_minor'] if t.get('per_day_minor') is not None else p['trip_per_day_minor'])} a day"
                + (f" plus {label(t['flights_minor'])} flights in {c.month_label(t['flights_month'])}"
                   if t.get("flights_minor") else "")
                for t in p["trips"]) + ".")
        if p["one_offs"]:
            findings.append("One-off payments, on top of the base rate: " + "; ".join(
                f"{o['label']} {label(o['amount_minor'])} in {c.month_label(o['month'])}"
                for o in p["one_offs"]) + ".")
        first = months[0] if months else None
        if first:
            findings.append(
                f"In {c.month_label(first['month'])}, on {label(first['gross_minor'])} gross, the take-home "
                f"after {p['cpf_employee_rate'] * 100:.0f} per cent CPF is "
                f"{label(first['take_home_minor'])}, against spending of {label(p['spend_minor'])}, "
                f"{label(p['other_outflow_minor'])} of other outflow and a "
                f"{label(first['investing_minor'])} investing transfer, so the month runs "
                f"{label(first['net_minor'])}.")
        if p["cpf_accrued_total_minor"]:
            findings.append(
                f"Over the same months the job puts "
                f"{label(p['cpf_accrued_total_minor'])} into CPF "
                f"({label(p['cpf_employee_total_minor'])} from pay, "
                f"{label(p['cpf_employer_total_minor'])} from the employer), savings the "
                f"cash line never shows.")
        observed = p["other_outflow_observed"]
        if observed["repayments_minor"]:
            findings.append(
                f"The window's non-spend outflow was "
                f"{label(observed['total_minor'])} a month, of which "
                f"{label(observed['repayments_minor'])} was paid back to "
                f"the repayment payee. Only the other {label(observed['other_minor'])} is projected; "
                f"resuming the repayments ends at "
                f"{label(by_name['with the repayments resuming']['ending_cash_minor'])}.")
        dec = [x for x in months if x["month"] in p["zero_income_months"]]
        if dec:
            share = (f"{abs(dec[0]['net_minor']) / p['starting_cash_minor'] * 100:.0f} per cent "
                     f"of the starting buffer"
                     if p["starting_cash_minor"] else "more than the starting buffer, "
                     "which is zero")
            findings.append(f"The month with no income costs "
                            f"{label(-dec[0]['net_minor'])}, which is {share}.")
        paused = next((s for s in scenarios if s["name"] == "investing paused"),
                      None)
        if paused:
            findings.append(f"Pausing the {amount_words(p['dca_amount_minor'])} investing "
                            f"transfer ends at "
                            f"{label(paused['ending_cash_minor'])} instead, a difference of "
                            f"{label(paused['ending_cash_minor'] - months[-1]['cash_minor'])}.")

    caveats = [
        (f"Pay is the plan's {len(p['income_segments'])} income row(s) as stated, side "
         f"income included; a month none of them covers earns nothing."
         if p.get("income_segments") else
         "No pay is set in the plan, so every projected month earns nothing from "
         "work; the plan's income segments are where it goes."),
        "Insurance is zero here: " + NOTES["insurance_premium"] + ".",
        f"Spending is the median of the {len(window)} month window, not its mean. "
        f"The mean is {label(p['spend_mean_minor'])} and is kept in the parameters; the "
        f"two differ because one month in the window carried a large refund.",
        (f"{len(p['spend_exclude_keys'])} row(s) the owner named as one-off "
         f"purchases are out of every rate above. Taking a purchase out is a "
         f"judgement, not a measurement: if one of them turns out to repeat, the "
         f"rate here is too low by its monthly share."
         if p["spend_exclude_keys"] else
         "No row is excluded from the rates; a single large purchase inside the "
         "window therefore raises the rate as though it happened every month."),
        f"Other outflow of {label(p['other_outflow_minor'])} a month is wallet moves, "
        f"small person to person payments and rows in no bucket. It is real money "
        f"leaving the bank, but some of it comes back, so it is the least certain "
        f"line here."
        + (" The repayments to the repayment payee are not in it: they are a "
           "parameter and default to zero." if payee else ""),
        (f"{len(trip_months)} of the {len(window)} window months were spent away "
         f"({c.months_label(trip_months)}, on {', '.join(trips_in_window)}), so the "
         f"default rate is what living cost while travelling. The next "
         f"{count_words(len(months))} months "
         f"hold no trip anybody has told this record about, which is why the "
         f"trip-free median and the phase rate are scenarios beside it."
         if trip_months else
         "No month in the window carried a trip, so the rate is what living at "
         "home cost."),
        "No income outside the plan's income rows is assumed, even where some arrived "
        "in the record; add a side income row (or set other_income) to include it.",
        "Housing is projected only as far as the record bills it; a cost the "
        "record has never carried is not in these figures.",
        "CPF is modelled as flat employee and employer shares of gross with the "
        "Ordinary Wage ceiling applied; it assumes the pay is employment income at "
        "all, which no payslip in this record confirms.",
        f"The employer's {p['cpf_employer_rate'] * 100:.0f} per cent is paid on top "
        f"of gross rather than out of it, so it is in the CPF columns and in no "
        f"cash figure on this page. Neither CPF column is spendable money: it is "
        f"locked for retirement, housing and healthcare.",
    ]
    return c.result(sys.modules[__name__], snapshot,
                    [c.source("Transactions",
                              len([r for r in c.spend_rows(snapshot)
                                   if r["month"] in set(window)]),
                              f"{c.SPEND_FILTER}, last {len(window)} covered months"),
                     c.source("Transactions", len(other_rows),
                              "flow_class = External, outflow, not synthetic, "
                              "bucket is empty or Transfer other than Investments"
                              + (", split into repayments to the repayment payee and "
                                 "the rest" if payee else "")),
                     c.source("Trips", len(trips_in_window),
                              "the trips any window month's rows sit on"),
                     c.source("Period balances", 1,
                              "starting cash from the savings-runway stat, at the "
                              "last month every bank account printed")],
                    {**p, "notes": NOTES, "currency": "SGD"},
                    {"months": months, "scenarios": scenarios,
                     "spend_by_month": by_month, "spend_rates": rates,
                     "spend_by_month_ex_all_trips": ex_all,
                     "spend_by_month_ex_short_trips": ex_short,
                     "starting_cash_minor": p["starting_cash_minor"],
                     "starting_month": start_month,
                     "low_point": low_point(months),
                     "below_zero": [m["month"] for m in months if m["cash_minor"] < 0]},
                    findings, caveats,
                    title=(f"{TITLE} to {c.month_label(p['to'])}" if p.get("to")
                           else TITLE))
