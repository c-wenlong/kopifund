"""G13: dates in prose, the same way the app shows them (apps/web/src/dates.ts).

A day reads "3 Oct 2026", a month "Oct 2026", a range "3 Oct to 9 Oct 2026"
(both ends in full when the years differ). Only sentences use these: data
fields, CSV exports, file names and API values stay ISO.
"""

import re

MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_ISO = re.compile(r"(\d{4})-(\d{2})(?:-(\d{2}))?")


def _parts(value):
    """(year, month, day or None) of an ISO day or month (a str or a date), or None."""
    hit = _ISO.fullmatch(str(value)) if value is not None else None
    if not hit:
        return None
    y, m, d = int(hit[1]), int(hit[2]), int(hit[3]) if hit[3] else None
    if not 1 <= m <= 12 or (d is not None and not 1 <= d <= 31):
        return None
    return y, m, d


def day_label(value):
    """`2026-10-03` as `3 Oct 2026`. Anything that is not an ISO day comes back as text."""
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
    if str(start) == str(end):
        return date_label(start)
    head = MONTHS[a[1] - 1] if a[2] is None else f"{a[2]} {MONTHS[a[1] - 1]}"
    return f"{head} to {date_label(end)}"


def months_label(values):
    """ISO months as a list in prose: `Jan 2026, Mar 2026`."""
    return ", ".join(month_label(v) for v in values)


def add_months(month, n):
    """The month n months after `month` (YYYY-MM); n may be negative."""
    y, m = int(month[:4]), int(month[5:7]) - 1 + n
    return f"{y + m // 12:04d}-{m % 12 + 1:02d}"


def months_apart(a, b):
    """How many months b is after a (both YYYY-MM)."""
    return (int(b[:4]) - int(a[:4])) * 12 + int(b[5:7]) - int(a[5:7])
