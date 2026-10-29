"""Money in one place: dollars to integer cents and back (G17), and an amount
in prose the way the app shows it (G13, apps/web/src/money.ts).

`to_cents` is the one way dollars become cents anywhere in Python, for money
that arrives in dollars: the plan's dollars a user typed, a statement's printed
figures, Notion's number fields, a published reference figure. It reads the
number as it prints (0.1 + 0.2 is 0.30000000000000004, so 30 cents) and
rounds half up, away from zero, so 1.005 is 101 cents and -1.005 is -101, on
every page alike. `from_cents` is the way back, exact.

`stat_cents` is how anything outside the stats reads a stat result's money
(G20): a money field is integer cents under `<name>_minor`, like the record's
columns. There is no dollar reading: a result from before G20 carries dollars
under the bare name, and `in_cents` says whether a result is one of those, so
the results cache shows it as no result until the stats run again rather than
read a dollar as a cent. Cents become dollars only at an edge that holds
dollars, through `from_cents`: the hub page's Notion number fields
(server/analytics/publish.py) and the export's dollar columns.

`label` is for sentences only: S$ for Singapore dollars and the code in front
for any other currency, thousands commas, two decimals, and a true minus
(U+2212) for a negative amount. Data fields, CSV exports and API values stay
plain numbers.
"""
import re
from decimal import Decimal

MINUS = "−"
NBSP = " "
# A number's text as a person or a statement writes it: a sign, digits with
# thousands commas only between groups of three ("1,234,567", never "1,2,3"),
# and a decimal point. No exponent, no currency, no bracket or trailing sign.
_TEXT = re.compile(r"[+-]?(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]*)?|[+-]?\.[0-9]+")
# Cents of 28 digits or more are no amount of money; it was the ceiling of
# Decimal's default precision, which the rounding no longer goes through
_CENTS_LIMIT = 10 ** 28


def to_cents(x):
    """Dollars as integer cents, rounded half up (away from zero).

    Takes an int, a float, a Decimal or a number's text ("1,234.5": thousands
    commas between groups of three, spaces around it allowed). A float is read
    as it prints, never as its binary value. The rounding is exact integer
    arithmetic, whatever the decimal context. Raises TypeError for a bool, None
    or anything else that is not a number, and ValueError for text that is not
    a plain number ("1,2,3", "(12.00)", "1e2"), NaN, an infinity, or 10^26
    dollars and more. A caller that wants a size without its sign takes `abs`
    of the answer itself."""
    if isinstance(x, bool) or not isinstance(x, (int, float, Decimal, str)):
        raise TypeError(f"{x!r} is not an amount of dollars")
    if isinstance(x, int):
        if abs(x) * 100 >= _CENTS_LIMIT:
            raise ValueError(f"{x!r} is too large to be an amount of dollars")
        return x * 100
    if isinstance(x, str):
        text = x.strip()
        if not _TEXT.fullmatch(text):
            raise ValueError(f"{x!r} is not an amount of dollars")
        d = Decimal(text.replace(",", ""))
    else:
        d = Decimal(repr(x)) if isinstance(x, float) else x
    if not d.is_finite():
        raise ValueError(f"{x!r} is not an amount of dollars")
    negative, digits, exp = d.as_tuple()
    n, exp = int("".join(map(str, digits))), exp + 2          # in cents
    if exp >= 0:
        if len(digits) + exp > 28:
            raise ValueError(f"{x!r} is too large to be an amount of dollars")
        cents = n * 10 ** exp
    elif -exp > len(digits):                                  # under a tenth of a cent
        cents = 0
    else:
        cents, rest = divmod(n, 10 ** -exp)
        if 2 * rest >= 10 ** -exp:                            # half a cent or more
            cents += 1
    if cents >= _CENTS_LIMIT:
        raise ValueError(f"{x!r} is too large to be an amount of dollars")
    return -cents if negative else cents


def from_cents(cents):
    """Integer cents as exact dollars: 123456 as Decimal("1234.56")."""
    if isinstance(cents, bool) or not isinstance(cents, int):
        raise TypeError(f"{cents!r} is not a whole number of cents")
    return Decimal(cents).scaleb(-2)


MINOR = "_minor"
# G20: the chart hint's unit for money in cents; "sgd" was dollars, before G20
CENTS_UNIT = "sgd_minor"


def stat_cents(obj, name):
    """G20: the money field `name` of a stat result's dict (a row, a summary,
    its params) as integer cents: `<name>_minor`, as it is.

    None when the field is absent or null, or `obj` is not a dict. Dollars
    under the bare name are never read: a result that holds them is from
    before G20, and the results cache does not hand it out (`in_cents`). A
    `_minor` value that is not a whole number is a TypeError: it is a result
    that broke the contract."""
    if not isinstance(obj, dict):
        return None
    cents = obj.get(name + MINOR)
    if cents is None:
        return None
    if isinstance(cents, bool) or not isinstance(cents, int):
        raise TypeError(f"{name}{MINOR} is {cents!r}, not a whole number of cents")
    return cents


def _holds_minor(o):
    if isinstance(o, dict):
        return any(k.endswith(MINOR) for k in o) or any(_holds_minor(v) for v in o.values())
    if isinstance(o, list):
        return any(_holds_minor(v) for v in o)
    return False


def in_cents(result):
    """G20: whether a stat result holds its money in cents, so it may be read.

    The result's chart hint says, never the size of a value: unit `sgd_minor`
    is a result in cents, and unit `sgd`, or no unit (which meant `sgd`), is a
    result from before G20 in dollars. A stat whose chart is not money
    (data-quality counts rows) is in cents when it holds a `<name>_minor`
    field. A result from before G20 shows as no result until the stats run
    again, as a forward-view result from before G17 did (server/analytics/SPEC.md,
    decision 26)."""
    if not isinstance(result, dict):
        return False
    unit = (result.get("chart") or {}).get("unit")
    if unit == CENTS_UNIT:
        return True
    if unit in (None, "sgd"):
        return False
    return _holds_minor(result.get("data")) or _holds_minor(result.get("params"))


E9 = 1_000_000_000


def rate_e9(x):
    """An FX rate as an integer scaled by 10^9, or None: how the record holds
    one (`period.fx_to_sgd_e9`, server/finance/db.py)."""
    if x is None or x == "":
        return None
    return int(round(float(x) * E9))


def fx_minor(minor, e9):
    """`minor` units of one currency in another's minor units, at a rate scaled
    by 10^9 (`rate_e9`). G20: the record's one rule for a foreign amount in SGD
    (v_txn's `amount_sgd_minor`, `round(amount_minor * fx_to_sgd_e9 / 1e9)`),
    done exactly in integers and rounded half up, away from zero."""
    for x in (minor, e9):
        if isinstance(x, bool) or not isinstance(x, int):
            raise TypeError(f"{x!r} is not a whole number")
    q, r = divmod(abs(minor) * abs(e9), E9)
    if 2 * r >= E9:
        q += 1
    return q if (minor < 0) == (e9 < 0) else -q


def label(cents, currency="SGD", plus=False):
    """`-241` cents SGD as `-S$2.41` (with the true minus), `123456` cents USD
    as `USD 1,234.56`. `plus` signs a positive amount, for a credit."""
    sign = MINUS if cents < 0 else "+" if plus and cents > 0 else ""
    text = f"{abs(cents) // 100:,}.{abs(cents) % 100:02d}"
    return f"{sign}S${text}" if currency == "SGD" else f"{sign}{currency}{NBSP}{text}"


def whole(cents):
    """SGD rounded to the dollar, as the app's figures show it (formatSgd with
    no decimals): `123456` cents as `S$1,235`, `-5000` as `−S$50`. G18: the
    lines of Needs your eye."""
    dollars = (abs(cents) + 50) // 100
    return f"{MINUS if cents < 0 and dollars else ''}S${dollars:,}"
