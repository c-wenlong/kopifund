"""The plan's income rows month by month, in integer cents (G14).

One reading of pay, shared by the Net worth projection (server/analytics/growth.py)
and the forward view (server/analytics/stats/forward_view.py), so the Plan page and
the Net worth page count the same money in the same month:

  * each row earns its gross a month, prorated by the calendar days of a month
    it covers part of; a month no row covers earns nothing (a break between
    two jobs), and rows that overlap add up;
  * a row with no end date runs to the horizon. An employment row with no end
    date grows by the salary growth rate once a year, counted from the later
    of its start and this month; a dated row is paid as typed;
  * an employment row's bonus is its months of pay (at that month's gross)
    landing in its bonus month each year the row covers any of that month;
  * CPF-liable pay is the Ordinary Wage of every row marked CPF yes, and the
    bonus of those rows is its Additional Wage, capped each calendar year at
    the annual salary ceiling less the year's Ordinary Wages subject to CPF;
  * side income with CPF no is net trade income, which self-employed MediSave
    is assessed on.

The rows come in as the plan holds them (dollars, server/finance/planning.py) and
are converted to cents here, once.
"""
import calendar
from datetime import date
from decimal import Decimal, InvalidOperation

from finance import cpf_rules as rules
from finance import planning
from finance.dates import add_months, months_apart  # noqa: F401  G19: finance's month arithmetic
from finance.money import to_cents




def rows(plan):
    """The plan's income rows in cents with real dates, oldest first. A row the
    plan page would refuse is skipped, so an old record still projects."""
    out = []
    for r in planning.income_rows(plan):
        try:
            start = date.fromisoformat(str(r["from"])[:10])
            end = date.fromisoformat(str(r["to"])[:10]) if r["to"] else None
            gross = to_cents(r["gross"])
            bonus = Decimal(str(r["bonus_months"])) if r.get("bonus_months") not in (None, "") else None
            bonus_month = int(r["bonus_month"]) if bonus else None
        except (KeyError, TypeError, ValueError, ArithmeticError, InvalidOperation):
            continue
        out.append({"name": r["name"], "type": r["type"], "from": start, "to": end, "gross": gross,
                    "cpf": r["cpf"], "bonus": bonus, "bonus_month": bonus_month})
    return sorted(out, key=lambda r: (r["from"], r["to"] or date.max))


class Pay:
    """What the rows earn in any month, with each calendar year's Additional
    Wage capped once, so a bonus in December sees the whole year's pay."""

    def __init__(self, income_rows, salary_growth_pct="0", today_month=None):
        self.rows = income_rows
        self.growth = 1 + Decimal(str(salary_growth_pct)) / 100
        self.today = today_month or date.today().strftime("%Y-%m")
        self._years = {}

    def gross(self, row, month):
        """The row's gross a month in `month`, before proration: grown once a
        year when it is an open-ended employment row."""
        if row["type"] != "employment" or row["to"] is not None or self.growth == 1:
            return row["gross"]
        anchor = max(row["from"].strftime("%Y-%m"), self.today)
        n = months_apart(anchor, month)
        return rules.cents(row["gross"] * self.growth ** (n // 12)) if n >= 12 else row["gross"]

    def _month(self, month):
        y, m = int(month[:4]), int(month[5:7])
        days = calendar.monthrange(y, m)[1]
        first, last = date(y, m, 1), date(y, m, days)
        out = {"month": month, "employment": 0, "side": 0, "bonus": 0, "ow": 0, "aw": 0, "trade": 0}
        for row in self.rows:
            a, b = max(first, row["from"]), min(last, row["to"] or last)
            if a > b:
                continue
            gross = self.gross(row, month)
            part = rules.cents(Decimal(gross) * ((b - a).days + 1) / days)
            out["employment" if row["type"] == "employment" else "side"] += part
            if row["cpf"]:
                out["ow"] += part
            elif row["type"] == "side":
                out["trade"] += part
            if row["type"] == "employment" and row["bonus"] and row["bonus_month"] == m:
                bonus = rules.cents(gross * row["bonus"])
                out["bonus"] += bonus
                if row["cpf"]:
                    out["aw"] += bonus
        out["income"] = out["employment"] + out["side"] + out["bonus"]
        return out

    def year(self, y):
        """Each month of calendar year `y`, with `aw_liable`: the bonus that
        attracts CPF once the year's AW ceiling is applied, first come first."""
        if y not in self._years:
            months = [self._month(f"{y:04d}-{m:02d}") for m in range(1, 13)]
            room = rules.aw_ceiling(sum(min(x["ow"], rules.OW_CEILING) for x in months))
            for x in months:
                x["aw_liable"] = min(x["aw"], room)
                room -= x["aw_liable"]
            self._years[y] = months
        return self._years[y]

    def at(self, month):
        return self.year(int(month[:4]))[int(month[5:7]) - 1]
