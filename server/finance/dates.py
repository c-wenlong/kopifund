"""G13: dates in prose, the same way the app shows them (apps/web/src/dates.ts).

A day reads "3 Oct 2026", a month "Oct 2026", a range "3 Oct to 9 Oct 2026"
(both ends in full when the years differ). Only sentences use these: data
fields, CSV exports, file names and API values stay ISO.

Text shaped like an ISO day or month that names no real one ("2026-02-31",
"2026-13") is a ValueError, in the labels and in the month arithmetic: it is a
misread upstream, never a date to print. Other text comes back as it is.
"""

import re
from datetime import date

MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_ISO = re.compile(r"([0-9]{4})-([0-9]{2})(?:-([0-9]{2}))?")
_MONTH = re.compile(r"([0-9]{4})-([0-9]{2})")


def _parts(value):
    """(year, month, day or None) of an ISO day or month (a str, a date or a
    datetime), or None for text of another shape. ValueError for one that does
    not exist."""
    if isinstance(value, date):
        return value.year, value.month, value.day
    hit = _ISO.fullmatch(str(value)) if value is not None else None
    if not hit:
        return None
    y, m, d = int(hit[1]), int(hit[2]), int(hit[3]) if hit[3] else None
    try:
        date(y, m, 1 if d is None else d)
    except ValueError:
        raise ValueError(f"{value!r} is not a real {'month' if d is None else 'day'}") from None
    return y, m, d


def day_label(value):
    """`2026-10-03` as `3 Oct 2026`. Anything that is not an ISO day comes back as
    text, except a day that does not exist, a ValueError."""
    p = _parts(value)
    if not p or p[2] is None:
        return "" if value is None else str(value)
    return f"{p[2]} {MONTHS[p[1] - 1]} {p[0]}"


def month_label(value):
    """`2028-02` (or a day in it) as `Feb 2028`. Anything else comes back as text."""
    p = _parts(value)
    return f"{MONTHS[p[1] - 1]} {p[0]}" if p else ("" if value is None else str(value))


def date_label(value):
    """An ISO day as a day, an ISO month as a month, anything else as text."""
    p = _parts(value)
    if not p:
        return "" if value is None else str(value)
    return day_label(value) if p[2] is not None else month_label(value)


def range_label(start, end):
    """Two ISO days or two ISO months as one range: `3 Oct to 9 Oct 2026`,
    `Oct to Dec 2026`, `28 Dec 2025 to 3 Jan 2026`. The same date twice is that date."""
    a, b = _parts(start), _parts(end)
    if not a or not b or (a[2] is None) != (b[2] is None) or a[0] != b[0]:
        return f"{date_label(start)} to {date_label(end)}"
    if a == b:
        return date_label(start)
    head = MONTHS[a[1] - 1] if a[2] is None else f"{a[2]} {MONTHS[a[1] - 1]}"
    return f"{head} to {date_label(end)}"


def months_label(values):
    """ISO months as a list in prose: `Jan 2026, Mar 2026`."""
    return ", ".join(month_label(v) for v in values)


def _month(value):
    """(year, month) of a YYYY-MM month. TypeError for what is not text,
    ValueError for text that is not a real month."""
    if not isinstance(value, str):
        raise TypeError(f"{value!r} is not a month, YYYY-MM")
    hit = _MONTH.fullmatch(value)
    if not hit or not 1 <= int(hit[1]) or not 1 <= int(hit[2]) <= 12:
        raise ValueError(f"{value!r} is not a month, YYYY-MM")
    return int(hit[1]), int(hit[2])


def add_months(month, n):
    """The month n months after `month` (YYYY-MM); n may be negative. ValueError
    when `month` is not a real month or the answer falls outside years 1 to 9999."""
    if isinstance(n, bool) or not isinstance(n, int):
        raise TypeError(f"{n!r} is not a whole number of months")
    y, m = _month(month)
    y, m = divmod(y * 12 + m - 1 + n, 12)
    if not 1 <= y <= 9999:
        raise ValueError(f"{n} months after {month} is outside the calendar")
    return f"{y:04d}-{m + 1:02d}"


def months_apart(a, b):
<<<<<<< HEAD
    """How many months b is after a (both YYYY-MM)."""
    return (int(b[:4]) - int(a[:4])) * 12 + int(b[5:7]) - int(a[5:7])


def months_between(first, last):
    """Every month from `first` to `last` (YYYY-MM), both ends included, with no
    gaps; empty when `last` is before `first`."""
    return [add_months(first, n) for n in range(months_apart(first, last) + 1)]
=======
    """How many months b is after a (both YYYY-MM): negative when b is before a."""
    (ya, ma), (yb, mb) = _month(a), _month(b)
    return (yb - ya) * 12 + mb - ma
>>>>>>> origin/main
