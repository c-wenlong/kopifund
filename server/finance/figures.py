"""Grouping and rounding over rows, the way the stats count: in integer cents
(G20), with one rounding rule.

G19: the stats (server/analytics/stats/common.py re-exports these) and the recurring
engine (server/finance/cadence.py) share them, and finance never imports analytics.
"""
from datetime import date

from finance.money import stat_cents


def by(rows, key):
    """{key value: [row]}, insertion-ordered by first appearance."""
    out = {}
    for r in rows:
        out.setdefault(key(r), []).append(r)
    return out


def days_between(a, b):
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


# ---------------------------------------------------------------- cents (G20)
#
# Integer cents with one rounding rule: a quotient that is not a whole cent
# rounds half up, away from zero, exactly as server/finance/money.py's `to_cents`
# rounds dollars, so 2.5 cents is 3 and -2.5 is -3. Every input and every
# answer is an int; a float or a bool is refused, because a float here is a
# dollar figure that was never converted. An empty list is 0.

def sgd_minor(row):
    """A row's SGD amount in integer cents, its `amount_sgd_minor`; 0 where it
    is null (no FX rate), so the row adds nothing to an SGD total."""
    cents = stat_cents(row, "amount_sgd")
    return 0 if cents is None else cents


def _cents_list(xs):
    xs = list(xs)
    for x in xs:
        if isinstance(x, bool) or not isinstance(x, int):
            raise TypeError(f"{x!r} is not a whole number of cents")
    return xs


def div_cents(total, n):
    """`total` cents over a whole number `n` (a count of months, of rows), to
    the cent, half up away from zero. 0 when `n` is 0, as `per_month` is."""
    (total,) = _cents_list([total])
    if isinstance(n, bool) or not isinstance(n, int) or n < 0:
        raise TypeError(f"{n!r} is not a count")
    if n == 0:
        return 0
    q, r = divmod(abs(total), n)
    if 2 * r >= n:          # half or more of a cent left over: up, away from zero
        q += 1
    return q if total >= 0 else -q


def sum_cents(xs):
    """The sum of some cents."""
    return sum(_cents_list(xs))


def mean_cents(xs):
    """The mean of some cents, to the cent (`div_cents`)."""
    xs = _cents_list(xs)
    return div_cents(sum(xs), len(xs))


def median_cents(xs):
    """The median of some cents: the middle one, or for an even count the
    mean of the two middle ones, to the cent (`div_cents`)."""
    xs = sorted(_cents_list(xs))
    if not xs:
        return 0
    mid = len(xs) // 2
    return xs[mid] if len(xs) % 2 else div_cents(xs[mid - 1] + xs[mid], 2)
