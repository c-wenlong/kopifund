"""Net worth over time, and the growth projection the Net worth page draws (G8, G14).

Two answers, computed so the page only draws:

  * `net_worth(results)` (server/finance/net_worth.py, which Home shares): cash, investments and CPF month by month, from what
    the savings-runway, portfolio and cpf stats already hold. Each part is
    carried forward from its last measured date (a month end for cash, a
    statement's last day for Tiger Brokers, 31 December for CPF), and each row
    names those dates. The months start where every part the record holds has
    been measured once, so a part never appears from nowhere.
  * `project(...)`: net worth drawn forward month by month from the owner's
    own inputs, saved in the plan (server/finance/planning.py, G14): the income rows,
    the investing steps, spending a month and the assumptions. Three parts:
      - cash, from the last complete month's balance: take-home (employment
        pay and bonuses less the employee's CPF, plus side income) less the
        estimated income tax (server/analytics/tax_rules.py), less self-employed
        MediSave on side income, less spending, less investing;
      - Tiger Brokers, the last statement's value compounding monthly at three
        annual returns plus the investing steps, with the money put in beside
        it;
      - CPF, month by month on the rules in server/finance/cpf_rules.py from the
        latest yearly statement, fed the employment rows and their bonuses
        and the MediSave on side income. The CPF line stops at 65, where CPF
        LIFE payouts begin, which this does not model.
    The total is the three added up, month by month, with tiles for today,
    five and ten years on, and the month a milestone is first reached.

Projections describe and estimate; nothing here says where money should go.
Money is integer cents; rates are Decimals. The stats' results are cents
already (G20), and the plan's dollars are converted once, on the way in.
"""
import calendar
from datetime import date
from decimal import Decimal

from analytics import income as pay_rows
from analytics import tax_rules
from finance import account_names, planning
from finance import cpf_rules as rules
from finance.dates import add_months, months_apart  # noqa: F401
# G19: the net worth series is finance's (Home draws it too, and finance never
# imports analytics); `growth.net_worth` is the same function
from finance.net_worth import month_of, months_between, net_worth  # noqa: F401
# G20: a stat's money in cents, as the stat wrote it
from finance.money import stat_cents

HORIZONS = {"10y": 120, "20y": 240, "30y": 360}
RATE_KEYS = tuple(planning.RATES)


# ------------------------------------------------------------ the inputs

def read_assumptions(query, plan):
    """The saved projection settings (planning.projection_settings), with any
    of them overridden by the query string, checked as the plan page checks
    them. Rates come back as Decimals, money as cents or None."""
    over = {k: query[k] for k in (*RATE_KEYS, "horizon", "dollars") if query.get(k) not in (None, "")}
    raw = {**((plan or {}).get("projection") or {}), **over}
    try:
        checked = planning.check_projection(raw)
    except planning.PlanRefused as e:
        raise ValueError(str(e)) from None
    s = planning.projection_settings({"projection": checked})
    a = {k: Decimal(s[k]) if s[k] is not None else None for k in RATE_KEYS}
    if a["spend_growth_pct"] is None:
        a["spend_growth_pct"] = a["inflation_pct"]
    a["spend_growth_follows_inflation"] = s["spend_growth_pct"] is None
    a["horizon"], a["dollars"] = s["horizon"], s["dollars"]
    a["spend"] = pay_rows.to_cents(s["spend"]) if s["spend"] is not None else None
    a["milestone"] = pay_rows.to_cents(s["milestone"]) if s["milestone"] is not None else None
    return a


def investing(plan):
    """A function from month to the investing transfer that month, in cents:
    the latest step that has started, an empty-from step from the start."""
    steps = [(s["from"], pay_rows.to_cents(s["amount"])) for s in planning.investing_steps(plan)
             if s.get("amount") not in (None, "")]

    def at(month):
        amount = 0
        for start, cents in steps:
            if start is None or start <= month:
                amount = cents
        return amount
    return at, steps


def horizon_end(a, today_month, birth):
    if a["horizon"] == "65":
        if birth is None:
            raise ValueError("the horizon to 65 needs a date of birth in Your profile")
        end = rules.birthday_month(birth, 65)
        if end <= today_month:
            raise ValueError("65 is already past, so pick 10, 20 or 30 years")
        return end
    return add_months(today_month, HORIZONS[a["horizon"]])


def deflator(a, today_month):
    """A function that puts a month's cents in today's dollars when asked to."""
    if a["dollars"] == "future":
        return lambda cents, month: cents
    rate = 1 + a["inflation_pct"] / 100

    def real(cents, month):
        n = max(0, months_apart(today_month, month))
        return rules.cents(Decimal(cents) / rate ** (Decimal(n) / 12))
    return real


def age_in(birth, month):
    """Whole years on the last day of `month`, or None without a date of birth."""
    if birth is None:
        return None
    y, m = int(month[:4]), int(month[5:7])
    return rules.age_on(birth, date(y, m, calendar.monthrange(y, m)[1]))


# ------------------------------------------------------------ tax and CPF out of pay

class Deductions:
    """The employee's CPF, the estimated income tax and self-employed MediSave,
    month by month. Tax and MediSave are worked out on the whole calendar year
    (every row's pay in it, months before the projection starts included) and
    set aside in each month in proportion to what that month earns, so a month
    with no income sets nothing aside and a bonus month sets aside more. The
    months before cash starts have their shares taken in its first month
    (`cash`)."""

    def __init__(self, pay, birth):
        self.pay, self.birth, self._years = pay, birth, {}

    def employee_cpf(self, month):
        p = self.pay.at(month)
        return rules.employee_contribution(p["ow"], month, self.birth, p["aw_liable"])

    def year(self, y):
        if y not in self._years:
            months = self.pay.year(y)
            earned = sum(m["income"] for m in months)
            trade = sum(m["trade"] for m in months)
            cpf = sum(self.employee_cpf(m["month"]) for m in months)
            medisave = rules.self_employed_medisave(trade, y, self.birth)
            age = rules.age_on(self.birth, date(y, 12, 31)) if self.birth else None
            self._years[y] = {**tax_rules.year(earned, cpf + medisave, age), "year": y,
                              "cpf_employee": cpf, "medisave": medisave, "trade": trade}
        return self._years[y]

    def month(self, month):
        """{cpf_employee, tax, medisave} set aside in `month`, in cents."""
        p, y = self.pay.at(month), self.year(int(month[:4]))
        tax = rules.cents(Decimal(y["tax"]) * p["income"] / y["assessable"]) if y["assessable"] else 0
        medisave = rules.cents(Decimal(y["medisave"]) * p["trade"] / y["trade"]) if y["trade"] else 0
        return {"cpf_employee": self.employee_cpf(month), "tax": tax, "medisave": medisave}


# ------------------------------------------------------------ the three parts

def broker_name(results):
    """The brokerage's name as the upload picker names its folder, read from
    the loaded portfolio statement's own folder (statements/tiger), so the page
    never carries a broker's name of its own. None when the folder is not a
    known one."""
    folder = ((results.get("portfolio") or {}).get("params") or {}).get("folder")
    slug = str(folder or "").rstrip("/").rsplit("/", 1)[-1]
    return account_names.folder_titles().get(slug)


def tiger(results, a, end, real, invest, birth=None, unmeasured_from=None):
    """Monthly compounding of the last statement's value at each annual
    return, plus the month's investing step, with the money put in beside it.

    With no statement, and investing steps that move money out of cash, the
    balance starts from nothing in `unmeasured_from` (the month cash starts
    from), so a transfer moves money between the parts rather than out of net
    worth; `measured` says which it is."""
    data = (results.get("portfolio") or {}).get("data") or {}
    summary = data.get("summary") or {}
    as_of = summary.get("as_of") if (summary.get("statements") or data.get("periods")) else None
    if as_of:
        start, measured = month_of(as_of), True
    elif unmeasured_from and any(invest(m) for m in months_between(add_months(unmeasured_from, 1), end)):
        start, measured, summary = unmeasured_from, False, {}
    else:
        return None
    value = stat_cents(summary, "value_sgd") or 0
    put_in = stat_cents(summary, "contributed_sgd") or 0
    names = ("pessimistic", "expected", "optimistic")
    rates = {n: a[f"{n}_pct"] / 100 / 12 for n in names}
    values = dict.fromkeys(names, value)
    rows = []
    for i, m in enumerate(months_between(start, end)):
        if i:
            step = invest(m)
            for n in names:
                values[n] = values[n] + rules.cents(values[n] * rates[n]) + step
            put_in += step
        rows.append({"month": m, "age": age_in(birth, m), **{n: real(values[n], m) for n in names},
                     "put_in": real(put_in, m)})
    return {"as_of": as_of, "measured": measured, "start": value, "broker": broker_name(results) if measured else None,
            "contributed": stat_cents(summary, "contributed_sgd") or 0, "rows": rows}


def cpf(results, a, end, real, birth, pay, deductions):
    """The CPF accounts month by month from the latest yearly statement, to the
    horizon or the 65th birthday, whichever is first. Each month shows the
    interest accrued so far that year, which CPF credits in December."""
    data = (results.get("cpf") or {}).get("data") or {}
    summary = data.get("summary") or {}
    if not summary.get("as_of"):
        return None
    if birth is None:
        return {"as_of": summary["as_of"], "needs": "birth_date", "rows": []}
    if month_of(summary["as_of"]) >= rules.birthday_month(birth, 55):
        # from 55 a statement holds a Retirement Account, which the cpf stat
        # does not read: starting from OA, SA and MA alone would open an RA a
        # second time and move money that already moved
        return {"as_of": summary["as_of"], "needs": "before_55", "rows": []}
    acc = rules.Accounts(oa=stat_cents(summary, "oa") or 0, sa=stat_cents(summary, "sa") or 0,
                         ma=stat_cents(summary, "ma") or 0)
    start = month_of(summary["as_of"])
    stop = min(end, rules.birthday_month(birth, 65))
    growth = a["sums_growth_pct"] / 100
    cohort_year = birth.year + 55
    cohort_frs = rules.frs(cohort_year, growth)
    rows, nominal = [], 0
    for i, m in enumerate(months_between(start, stop)):
        if i:
            p = pay.at(m)
            rules.step(acc, m, p["ow"], birth, growth, additional=p["aw_liable"],
                       medisave=deductions.month(m)["medisave"])
        oa, sa, ra, ma = (acc.worth(k) for k in ("oa", "sa", "ra", "ma"))
        nominal = oa + sa + ra + ma
        rows.append({"month": m, "age": age_in(birth, m),
                     "oa": real(oa, m), "sa": None if acc.ra_open else real(sa, m),
                     "ra": real(ra, m) if acc.ra_open else None, "ma": real(ma, m),
                     "total": real(oa + sa + ra + ma, m),
                     # the cohort FRS beside the RA, in the same dollars as the
                     # balances: flat in future dollars, falling in today's
                     "frs": real(cohort_frs, m) if acc.ra_open else None})
    return {"as_of": summary["as_of"], "needs": None, "rows": rows,
            # the last balance in future dollars, so the total can hold it past
            # 65 and still put each later month in that month's dollars
            "last_nominal": nominal,
            "frs": {"year": cohort_year, "month": rules.birthday_month(birth, 55),
                    "value": cohort_frs, "assumed": cohort_year != rules.SUMS_YEAR}}


def starting_cash(results):
    """(month, cents) of the last complete month's cash, or None."""
    runway = (results.get("savings-runway") or {}).get("data") or {}
    done = [(m["month"], stat_cents(m, "cash")) for m in runway.get("months") or ()
            if m.get("month") and stat_cents(m, "cash") is not None and m.get("complete", True)]
    return max(done) if done else None


def spender(a, base, today_month):
    """A function from month to spending in cents: `base` this month, grown
    smoothly by the spending growth rate a year after it."""
    rate = 1 + a["spend_growth_pct"] / 100

    def at(month):
        n = max(0, months_apart(today_month, month))
        return rules.cents(Decimal(base) * rate ** (Decimal(n) / 12)) if n else base
    return at


def cash(results, end, real, pay, deductions, invest, spend, today_month):
    """Cash month by month from the last complete month: what comes in less
    what CPF, tax, MediSave, spending and investing take. Each row's figures
    are in the chart's dollars, the balance carried in nominal cents.

    A year's tax and MediSave are worked out on all twelve of its months, but
    the measured balance already holds the pay of that year's months up to it,
    with nothing set aside for them. The first projected month takes their
    share as well (`before_start`), so the year's whole estimate comes off
    cash. A balance of nothing to start from holds no pay, so it takes none."""
    start = starting_cash(results)
    measured = start is not None
    month, balance = start if measured else (today_month, 0)
    opening = balance
    rows = [{"month": month, "cash": real(balance, month), "income": 0, "employment": 0, "bonus": 0,
             "side": 0, "cpf_employee": 0, "tax": 0, "medisave": 0, "spend": 0, "investing": 0, "net": 0}]
    first = add_months(month, 1)
    before = None
    if measured:
        elapsed = months_between(f"{month[:4]}-01", month)
        shares = [deductions.month(m) for m in elapsed]
        before = {"from": elapsed[0], "to": month,
                  "tax": sum(s["tax"] for s in shares), "medisave": sum(s["medisave"] for s in shares)}
    for m in months_between(first, end):
        p, d = pay.at(m), deductions.month(m)
        if before and m == first:
            d = {**d, "tax": d["tax"] + before["tax"], "medisave": d["medisave"] + before["medisave"]}
        out = {"spend": spend(m), "investing": invest(m)}
        net = p["income"] - d["cpf_employee"] - d["tax"] - d["medisave"] - out["spend"] - out["investing"]
        balance += net
        rows.append({"month": m, "cash": real(balance, m), "net": real(net, m),
                     **{k: real(p[k], m) for k in ("income", "employment", "bonus", "side")},
                     **{k: real(v, m) for k, v in d.items()},
                     **{k: real(v, m) for k, v in out.items()}})
    return {"as_of": month if measured else None, "start": opening, "measured": measured, "rows": rows,
            "before_start": before and {**before, "tax": real(before["tax"], first),
                                        "medisave": real(before["medisave"], first)}}


# ------------------------------------------------------------ the total

def runs(months):
    """Consecutive months as [{from, to}] runs, in order."""
    out = []
    for m in months:
        if out and add_months(out[-1]["to"], 1) == m:
            out[-1]["to"] = m
        else:
            out.append({"from": m, "to": m})
    return out


def total(today_month, end, birth, cash_part, tiger_part, cpf_part, milestone, real=lambda c, m: c):
    """Cash, Tiger Brokers (expected, with the band) and CPF added up month by
    month from this month to the horizon. A part with no row in a month
    carries its last one: CPF after 65 is held at its last projected balance,
    in future dollars, put in each later month's dollars by `real`."""
    def lookup(rows):
        by = {r["month"]: r for r in rows}
        first = rows[0] if rows else None

        def at(m):
            if m in by:
                return by[m], False
            earlier = [r for r in rows if r["month"] < m]
            return (earlier[-1], True) if earlier else (first, False)
        return at

    parts = ["cash"]
    cash_at = lookup(cash_part["rows"])
    tiger_at = lookup(tiger_part["rows"]) if tiger_part and tiger_part["rows"] else None
    cpf_at = lookup(cpf_part["rows"]) if cpf_part and cpf_part.get("rows") else None
    if tiger_at:
        parts.append("tiger")
    if cpf_at:
        parts.append("cpf")
    rows = []
    for m in months_between(today_month, end):
        c = cash_at(m)[0]["cash"]
        t = tiger_at(m)[0] if tiger_at else None
        p, held = cpf_at(m) if cpf_at else (None, False)
        tiger_v = t["expected"] if t else 0
        cpf_v = (real(cpf_part["last_nominal"], m) if held else p["total"]) if p else 0
        rows.append({"month": m, "age": age_in(birth, m), "cash": c,
                     "tiger": tiger_v if t else None, "cpf": cpf_v if p else None,
                     "cpf_held": bool(p and held),
                     "total": c + tiger_v + cpf_v,
                     "low": c + (t["pessimistic"] if t else 0) + cpf_v,
                     "high": c + (t["optimistic"] if t else 0) + cpf_v})
    by = {r["month"]: r for r in rows}

    def tile(n):
        m = add_months(today_month, n)
        return {"month": m, "total": by[m]["total"]} if m in by else None

    reached = next((r for r in rows if milestone is not None and r["total"] >= milestone), None)
    below = [r["month"] for r in rows if r["cash"] < 0]
    low = min(rows, key=lambda r: r["cash"]) if rows else None
    return {"parts": parts, "rows": rows,
            "tiles": {"today": tile(0), "in_5y": tile(60), "in_10y": tile(120)},
            "milestone": None if milestone is None else {
                "value": milestone, "month": reached["month"] if reached else None,
                "total": reached["total"] if reached else None},
            "below_zero": runs(below),
            "low": {"month": low["month"], "cash": low["cash"]} if low else None}


# ------------------------------------------------------------ the answer

def inputs(plan):
    """The editor's view of the plan: income rows, investing steps and the
    settings, each in the G14 shape however old the stored plan is, with
    their money in cents under the `_minor` names (G17, planning.plan_out)."""
    return planning.plan_out({"income": planning.income_rows(plan), "investing": planning.investing_steps(plan),
                              "projection": planning.projection_settings(plan)})


def project(results, plan, birth_date, query=None, today=None, spend_hint=None):
    """Everything the Growth card draws, for one plan and its settings.

    `spend_hint` is the record's own spending a month ({per_month, from, to,
    months}, server/finance/spending.py), used when the owner has typed none."""
    today = today or date.today()
    today_month = month_of(today.isoformat())
    birth = rules.parse_birth(birth_date)
    a = read_assumptions(query or {}, plan)
    end = horizon_end(a, today_month, birth)
    real = deflator(a, today_month)
    pay = pay_rows.Pay(pay_rows.rows(plan), a["salary_growth_pct"], today_month)
    deductions = Deductions(pay, birth)
    invest, _ = investing(plan)
    hint = (spend_hint or {}).get("per_month")
    base = a["spend"] if a["spend"] is not None else (hint or 0)
    spend = spender(a, base, today_month)
    cash_part = cash(results, end, real, pay, deductions, invest, spend, today_month)
    tiger_part = tiger(results, a, end, real, invest, birth, unmeasured_from=cash_part["rows"][0]["month"])
    cpf_part = cpf(results, a, end, real, birth, pay, deductions)
    markers = ([{"age": n, "month": rules.birthday_month(birth, n)} for n in (55, 65)]
               if birth else [])
    shown = {k: (str(v) if isinstance(v, Decimal) else v) for k, v in a.items()}
    years = sorted({int(m[:4]) for m in months_between(today_month, end)})
    return {
        "today": today.isoformat(),
        "end": end,
        "birth_date": birth.isoformat() if birth else None,
        "age": rules.age_on(birth, today) if birth else None,
        "inputs": inputs(plan),
        "spend_hint": spend_hint,
        "assumptions": {**shown, "spend": base,
                        "spend_from": "typed" if a["spend"] is not None else ("average" if hint else "none"),
                        "investing_now": invest(today_month)},
        "can_reach_65": bool(birth and rules.birthday_month(birth, 65) > today_month),
        "markers": markers,
        "rules": {"as_of": rules.AS_OF, "source": rules.SOURCE, "ow_ceiling": rules.OW_CEILING,
                  "bhs": rules.BHS, "frs": rules.FRS, "sums_year": rules.SUMS_YEAR,
                  "tax": {"as_of": tax_rules.AS_OF, "source": tax_rules.SOURCE},
                  "medisave": {"as_of": rules.MEDISAVE_SE_AS_OF, "source": rules.MEDISAVE_SE_SOURCE[0]}},
        "tax_years": [{k: deductions.year(y)[k] for k in
                       ("year", "assessable", "reliefs", "chargeable", "tax", "cpf_employee", "medisave")}
                      for y in years[:3]],
        "tiger": tiger_part,
        "cpf": cpf_part,
        "cash": cash_part,
        "total": total(today_month, end, birth, cash_part, tiger_part, cpf_part, a["milestone"], real),
    }
