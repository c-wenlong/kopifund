"""G13: figures the record already knows, offered beside the Plan and Net worth
inputs the owner otherwise types from memory.

Everything here is computed from the record, the stat results and the profile
(date of birth); nothing about any one person is written into the code. The
page shows each as a chip and fills nothing until the chip is tapped. Money
leaves as integer cents, a rate as a fraction.

  * income: the salary and other income credits, grouped by who paid them. A
    group is offered while it is still paying (its last credit is at most
    two months behind the record's last month) and has paid in at least two
    months. Its typical credit is the middle of its last three ordinary
    months; a month that came to at least one and a half times the middle one
    is a bonus month, and a spike in one calendar month is read as a yearly
    bonus. A statement shows take-home. With a date of birth the employee's
    CPF is added back (server/finance/cpf_rules.py) so the figure fits the row's
    gross; without one the take-home is offered as it stands, on a row that
    pays no CPF, so it is not taken off twice.
  * investing: the deposits the brokerage statements print, over the last
    twelve months they cover.
  * cpf_employer_rate: the employer's share of the contribution rate for the
    owner's age band this month.
  * insurance and repayments: the active recurring series in the categories
    of that name, each at its monthly cost, as Recurring reads them
    (server/finance/recurring.py, `live`): a series the owner marked not recurring,
    or whose end has passed, is not one, and one whose usual amount or cadence
    they set costs what they set (G16).
"""
import re
from collections import defaultdict
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from . import cpf_rules as rules
from . import recurring, settings
from finance.money import fx_minor, rate_e9, stat_cents

# the recurring detector stat's ID, the key its result is under: finance reads the
# result and never imports the stat (G19; server/tests/test_suggestions.py checks the two agree)
RECURRING_ID = "recurring-detector"

ACTIVE_WITHIN = 2             # months a payer's last credit may trail the record's last month
TYPICAL_MONTHS = 3            # ordinary months the typical credit is read from
BONUS_AT = 1.5                # a month this many times the middle one is a bonus month
INVEST_WINDOW = 12            # months of deposits read
INVEST_MIN_MONTHS = 3         # months with a deposit before a monthly amount is offered
EMPLOYMENT = re.compile(r"salary|wage", re.I)
SIDE = re.compile(r"income", re.I)
INSURANCE = re.compile(r"insurance|premium", re.I)
REPAYMENTS = re.compile(r"repay|loan|mortgage|instal", re.I)

CREDITS = """SELECT txn_date, month, amount_sgd_minor AS got, category_name,
                    COALESCE(NULLIF(payee_name, ''), NULLIF(counterparty, ''), NULLIF(merchant_raw, '')) AS who
             FROM v_txn
             WHERE flow_class = 'External' AND is_synthetic = 0 AND amount_sgd_minor > 0 AND bucket = 'Income'
             ORDER BY txn_date, txn_key"""


def _shift(month, n):
    i = int(month[:4]) * 12 + int(month[5:7]) - 1 + n
    return f"{i // 12:04d}-{i % 12 + 1:02d}"


def _median(xs):
    xs = sorted(xs)
    mid = len(xs) // 2
    return xs[mid] if len(xs) % 2 else (xs[mid - 1] + xs[mid]) // 2


def gross_from_take_home(take_home, month, birth):
    """The gross a month that leaves `take_home` after the employee's CPF, in
    whole dollars of cents. Only the wage up to the ceiling attracts CPF, so
    above the take-home that ceiling leaves the rest is added as it stands."""
    rate = rules.employee_rate(month, birth)
    below = Decimal(rules.OW_CEILING) * (1 - rate)
    gross = Decimal(take_home) / (1 - rate) if take_home <= below else Decimal(take_home) + rules.OW_CEILING * rate
    return int((gross / 100).quantize(Decimal(1), rounding=ROUND_HALF_UP)) * 100


def bonus_gross(take_home, gross, month, birth):
    """The bonus, in cents, that leaves `take_home` after the employee's CPF on
    it. Only the Additional Wage under the year's ceiling (the annual ceiling
    less the year's Ordinary Wages subject to CPF) attracts CPF, so a bonus
    above that is added back only for the part under it."""
    rate = rules.employee_rate(month, birth)
    room = rules.aw_ceiling(12 * rules.liable(gross))
    below = Decimal(room) * (1 - rate)
    return Decimal(take_home) / (1 - rate) if take_home <= below else Decimal(take_home) + room * rate


def income(conn, birth):
    """The pay still arriving, as rows ready for the Income table: [{name, type,
    from, last, take_home, gross, cpf, basis, bonus_months, bonus_month}], the
    biggest first. `basis` says what `gross` is: "gross" (take-home with the
    employee's CPF added back), "take_home" (the statement's figure, no date of
    birth to add CPF back with) or "side" (side income, paid as it arrives)."""
    last_month = conn.execute("SELECT MAX(month) FROM v_txn").fetchone()[0]
    if not last_month:
        return []
    groups = defaultdict(lambda: {"months": defaultdict(int), "first": None, "last": None})
    for r in conn.execute(CREDITS):
        name = r["category_name"] or ""
        kind = "employment" if EMPLOYMENT.search(name) else "side" if SIDE.search(name) else None
        if not kind:
            continue
        g = groups[(kind, r["who"] or name)]
        g["months"][r["month"]] += r["got"]
        g["first"] = g["first"] or r["txn_date"]
        g["last"] = r["txn_date"]
    out = []
    for (kind, who), g in groups.items():
        months = sorted(g["months"])
        if len(months) < 2 or months[-1] < _shift(last_month, -ACTIVE_WITHIN):
            continue
        totals = [g["months"][m] for m in months]
        middle = _median(totals)
        spikes = [m for m in months if g["months"][m] >= BONUS_AT * middle]
        ordinary = [g["months"][m] for m in months if m not in spikes] or totals
        typical = _median(ordinary[-TYPICAL_MONTHS:])
        if kind == "employment" and birth:
            gross, basis = gross_from_take_home(typical, months[-1], birth), "gross"
        else:
            gross, basis = typical, "take_home" if kind == "employment" else "side"
        bonus_months = bonus_month = None
        if spikes and len({m[5:7] for m in spikes}) == 1 and kind == "employment":
            # the projection pays months of gross pay, so a take-home spike is first added back to a gross bonus
            extra = max(g["months"][m] for m in spikes) - typical
            bonus = bonus_gross(extra, gross, months[-1], birth) if basis == "gross" else Decimal(extra)
            bonus_months = round(float(bonus / gross) * 2) / 2 or None
            bonus_month = int(spikes[-1][5:7]) if bonus_months else None
        out.append({"name": who, "type": kind, "from": g["first"], "last": g["last"], "take_home": typical,
                    "gross": gross, "cpf": basis == "gross", "basis": basis,
                    "bonus_months": bonus_months, "bonus_month": bonus_month})
    return sorted(out, key=lambda r: (-r["gross"], r["name"]))


def investing(results):
    """What was deposited at the brokerage a month, over the last twelve
    months the statements cover (to the latest statement's last month):
    {per_month, months, from, to} in cents (the middle of the months that had
    a deposit), or None with fewer than three such months. A deposit in US dollars, the statement's base, is converted
    at its period's own rate; one in any other currency is left out."""
    data = (results.get("portfolio") or {}).get("data") or {}
    # G20: a period's rate to SGD scaled by 10^9, the portfolio stat's own, or
    # its six-place `sgd_per_base` in a result from before
    rates = [(p["period_from"], p["period_to"],
              p.get("fx_to_sgd_e9") or rate_e9(p.get("sgd_per_base")))
             for p in data.get("periods") or ()]
    by_month = defaultdict(int)
    for d in data.get("deposits") or ():
        amount = stat_cents(d, "amount")       # the deposit's own currency, in cents
        if amount is None or not d.get("date"):
            continue
        if d.get("currency") == "SGD":
            by_month[d["date"][:7]] += amount
            continue
        # the statement's base currency is US dollars (server/analytics/stats/portfolio.py); another currency has no rate here
        rate = next((r for lo, hi, r in rates if r and lo <= d["date"] <= hi), None) if d.get("currency") == "USD" else None
        if rate:
            by_month[d["date"][:7]] += fx_minor(amount, rate)     # the record's rule, as the stat converts
    if not by_month:
        return None
    # the window ends where the statements do, so deposits that stopped long ago are not offered as current
    ends = [p["period_to"][:7] for p in data.get("periods") or () if p.get("period_to")]
    last = max([*ends, *by_month])
    window = [m for m in by_month if _shift(last, -(INVEST_WINDOW - 1)) <= m <= last and by_month[m] > 0]
    if len(window) < INVEST_MIN_MONTHS:
        return None
    return {"per_month": _median([by_month[m] for m in window]), "months": len(window),
            "from": min(window), "to": last}


def employer_cpf_rate(birth, today):
    """The employer's share of the CPF contribution rate for the owner's age
    band in `today`'s month, as a fraction (0.17), or None with no date of birth."""
    if not birth:
        return None
    month = today.strftime("%Y-%m")
    return float(rules.contribution_rate(month, birth) - rules.employee_rate(month, birth))


def active_series(conn, results, today):
    """The active recurring series: as Recurring reads them, the owner's edits
    over what was detected (G16), else, with no record, the detector's own."""
    if conn:
        return recurring.live(conn, results, today).get("active") or ()
    series = ((results.get(RECURRING_ID) or {}).get("data") or {}).get("series") or ()
    return [s for s in series if not s.get("stopped") and not s.get("paused")]


def standing(series, pattern, payee="", category=""):
    """{per_month, series} in cents: the active `series` whose category
    matches `pattern`, or whose payee (and category, when given) is the
    configured one, at their monthly cost; None when there are none."""
    mine = [s for s in series
            if stat_cents(s, "monthly_cost")
            and (pattern.search(s.get("category") or "")
                 or (payee and s.get("payee") == payee and (not category or s.get("category") == category)))]
    if not mine:
        return None
    # G20: the detector's monthly cost in cents, `monthly_cost_minor`
    return {"per_month": sum(stat_cents(s, "monthly_cost") for s in mine), "series": len(mine)}


def suggestions(conn, results, birth_date, today=None):
    """Every suggestion in one answer. `conn` is None before a record exists;
    `birth_date` is the profile's ISO date or None."""
    today = today or date.today()
    birth = date.fromisoformat(birth_date) if birth_date else None
    active = active_series(conn, results, today)
    return {
        "income": income(conn, birth) if conn else [],
        "investing": investing(results),
        "cpf_employer_rate": employer_cpf_rate(birth, today),
        "insurance": standing(active, INSURANCE),
        "repayments": standing(active, REPAYMENTS, settings.text_setting("repayment_payee"),
                               settings.text_setting("repayment_category")),
    }
