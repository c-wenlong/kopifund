"""The headline figures, one row each, from the stats' results.

`metrics_from(results)` is what the hub page's Metrics table holds
(server/analytics/publish.py writes it to Notion) and what Home reads its net worth,
cash and holdings from. G19: it lives in finance because finance never imports
analytics; server/analytics/publish.py re-exports these names.

G20: every money figure is read in cents (money.stat_cents) and a money row
carries it in cents as `Value_minor`; a row that is not money (months, a
percentage, a count of rows) carries `Value`. Home reads the cents as they
are, and the hub page turns them into the dollars its Number fields hold in
one place (server/analytics/publish.py, `notion_row`). A note's amounts are text,
printed from the cents.
"""
from datetime import date

from .money import MINOR, from_cents, stat_cents as _c


def _amount(cents):
    """Cents as the hub page's notes print an amount: "1,234.56" (0 for none)."""
    return f"{from_cents(cents or 0):,.2f}"


def later(*days):
    """The latest of some ISO dates or months, ignoring the empty ones.

    A month and a day compare correctly as strings here because both start
    YYYY-MM, so 2026-08-31 is after 2026-07 without parsing either.
    """
    return max((d for d in days if d), default="")


def _data(results, stat_id):
    r = results.get(stat_id) or {}
    return r.get("data") or {}, r.get("params") or {}


# Every Metrics row the page can hold, in page order. A row's Order is its
# place here, fixed whatever else is published beside it: the upsert leaves a
# row it is not given alone, so an Order counted over only the rows of this
# run (an `--only` subset, a result file gone from the cache) would collide
# with the ones left standing. A row with no figure leaves a gap in the
# numbering, never a shift. Ids, not names: two names carry a year or a month.
METRIC_ORDER = ("cash", "runway", "investments", "net-worth", "cpf", "net-worth-cpf",
                "spending", "income", "invested", "home-rate", "plan-cash", "eating-out",
                "wants", "needs-rule", "budget")


def metrics_from(results, snapshot=None, today=None):
    """The Metrics rows, in page order. Each is a plain dict in the shape of
    server/analytics/publish.py's METRIC_TYPES; a figure the results cannot supply is
    skipped, not written as 0. Order comes from METRIC_ORDER, so it does not
    depend on which results are present."""
    today = today or date.today()
    rows = []

    def add(slot, name, value, unit, as_of, note, stat):
        """A row with a figure that is not money: `Value`, to two places."""
        if value is None:
            return
        rows.append({"Name": name, "Value": round(float(value), 2), "Unit": unit,
                     "As of": str(as_of or ""), "Note": note, "Stat": stat,
                     "Order": METRIC_ORDER.index(slot) + 1})

    def money(slot, name, cents, as_of, note, stat):
        """A row whose figure is money: SGD cents as `Value_minor` (G20)."""
        if cents is None:
            return
        rows.append({"Name": name, "Value" + MINOR: cents, "Unit": "SGD",
                     "As of": str(as_of or ""), "Note": note, "Stat": stat,
                     "Order": METRIC_ORDER.index(slot) + 1})

    runway, _ = _data(results, "savings-runway")
    s = runway.get("summary") or {}
    money("cash", "Cash, all accounts", _c(s, "cash"), s.get("as_of"),
          "the last month every bank account printed a statement", "savings-runway")
    add("runway", "Runway, months", s.get("runway_6m"), "months", s.get("as_of"),
        f"cash over the last six months' spending rate of {_amount(_c(s, 'spend_avg_6m'))}",
        "savings-runway")

    portfolio, _ = _data(results, "portfolio")
    pf = portfolio.get("summary") or {}
    if pf.get("statements"):
        money("investments", "Investments, market value", _c(pf, "value_sgd"), pf.get("as_of"),
              "Tiger Brokers, statement value in USD at the statement's SGD rate; "
              f"contributed {_amount(_c(pf, 'contributed_sgd'))}, "
              f"gain {_amount(_c(pf, 'gain_sgd'))}", "portfolio")
        money("net-worth", "Net worth, cash plus investments", _c(pf, "net_worth_sgd"),
              later(pf.get("as_of"), pf.get("cash_as_of")),
              f"cash at {pf.get('cash_as_of')} plus investments at {pf.get('as_of')}",
              "portfolio")

    cpf, _ = _data(results, "cpf")
    cs = cpf.get("summary") or {}
    if cs.get("years"):
        money("cpf", "CPF, all three accounts", _c(cs, "total"), cs.get("as_of"),
              f"OA {_amount(_c(cs, 'oa'))}, SA {_amount(_c(cs, 'sa'))}, "
              f"MA {_amount(_c(cs, 'ma'))}; locked savings, not cash", "cpf")
        money("net-worth-cpf", "Net worth, including CPF", _c(cs, "net_worth_total"),
              later(cs.get("as_of"), cs.get("cash_as_of"), cs.get("investments_as_of")),
              f"cash at {cs.get('cash_as_of')} plus investments at "
              f"{cs.get('investments_as_of')} plus CPF at {cs.get('as_of')}", "cpf")

    cashflow, _ = _data(results, "monthly-cashflow")
    full = [m for m in cashflow.get("months", []) if not m.get("partial")]
    last = full[-1] if full else None
    if last:
        money("spending", "Spending, last full month", _c(last, "spend"), last["month"],
              "External rows, transfers out", "monthly-cashflow")
        money("income", "Income, last full month", _c(last, "income"), last["month"],
              "everything that came in, salary or not", "monthly-cashflow")
        year = str(today.year)
        invested = sum(_c(m, "investing") or 0 for m in cashflow.get("months", [])
                       if m["month"].startswith(year))
        money("invested", f"Invested in {year}", invested, cashflow["months"][-1]["month"],
              "the monthly investing transfer, partial months included", "monthly-cashflow")

    _, fv_params = _data(results, "forward-view")
    money("home-rate", "Home rate, six-month median", _c(fv_params, "spend_ex_all_trips"),
          (fv_params.get("spend_window_all") or [None])[-1],
          "the window median with every row on a trip taken out: a month in Singapore",
          "forward-view")
    fv, _ = _data(results, "forward-view")
    if fv.get("months"):
        end = fv["months"][-1]
        low = min(fv["months"], key=lambda m: _c(m, "cash"))
        money("plan-cash", f"Cash by {end['month']}, on the plan", _c(end, "cash"),
              fv_params.get("starting_month"),
              f"[plan] in config.toml; low point {_amount(_c(low, 'cash'))} in {low['month']}", "forward-view")

    eating, _ = _data(results, "eating-out")
    if last:
        em = [m for m in eating.get("months", []) if m["month"] == last["month"]]
        if em:
            money("eating-out", "Eating out, last full month", _c(em[0], "spend"), last["month"],
                  f"{em[0].get('count', 0)} meals", "eating-out")

    nws, _ = _data(results, "needs-wants-savings")
    summ = nws.get("summary") or {}
    if summ.get("want_share") is not None:
        add("wants", "Wants, share of income", round(summ["want_share"] * 100, 1), "per cent",
            f"{summ.get('months', 0)} covered months", "the 50/30/20 rule says 30", "needs-wants-savings")

    dq, _ = _data(results, "data-quality")
    counts = dq.get("counts") or {}
    if counts.get("uncategorised") is not None:
        add("needs-rule", "Rows needing a rule", counts["uncategorised"], "rows",
            (fv_params.get("spend_window_all") or [None])[-1] or "",
            "External rows with no category; make plan lists them", "data-quality")

    if snapshot is not None:
        month = today.strftime("%Y-%m")
        planned = actual = 0
        # G20: the snapshot's budgets carry the record's cents
        for b in snapshot.get("budgets", []):
            if b.get("month") == month and b.get("planned_minor"):
                planned += b["planned_minor"]
                actual += b.get("actual_minor") or 0
        if planned:
            add("budget", "Budget used, this month", round(actual / planned * 100, 1),
                "per cent", month,
                f"Actual {_amount(actual)} over Planned {_amount(planned)}, categories with a plan", "budgets")
    return rows
