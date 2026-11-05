"""Helpers more than one parser reads a statement with.

Each lived as a copy in two to five parsers; a fix to one copy (how far a
figure may sit from its column, how a December date in a January statement
gets its year) now reaches every parser that reads that way.
"""
import re
from decimal import Decimal

AMT = r"[0-9][0-9,]*\.[0-9]{2}"
MONEY = re.compile(AMT)

# how far an amount may sit from its column heading's right edge (OCBC, UOB)
ALIGN = 3


def money(s):
    """A printed number as a float, or None when the cell was empty (DBS, Tiger)."""
    return float(s.replace(",", "")) if s else None


def cents(text):
    """'1,234.56' as integer cents, exactly: money never passes through a float."""
    return int((Decimal(text.replace(",", "")) * 100).to_integral_value())


def plain(num):
    """'1,234.56' -> '1234.56'; leaves the printed precision alone."""
    return num.replace(",", "")


def heading_edges(line, headings):
    """The right edge of each of `headings` on a table's header line."""
    return [line.index(h) + len(h) for h in headings]


def column_amounts(line, edges):
    """[(match, column)] for the amounts that sit in one of the three money
    columns (withdrawal, deposit, balance): right-aligned, within ALIGN, to
    that heading's right edge. A number anywhere else is description text
    (OTHR RENT 250.00) and stays text."""
    out = []
    for m in MONEY.finditer(line):
        i = min(range(3), key=lambda k: abs(m.end() - edges[k]))
        if abs(m.end() - edges[i]) <= ALIGN:
            out.append((m, i))
    return out


def amounts(line, edges):
    """[withdrawal, deposit, balance] in cents, None where the column is empty."""
    out = [None, None, None]
    for m, i in column_amounts(line, edges):
        out[i] = cents(m.group())
    return out


def date_in_period(ddmon, start, end, months):
    """'05 Mar' as an ISO date inside the statement period, `start` and `end`
    as (year, month, day) and `months` the month names as printed: a statement
    that runs over the new year carries December and January dates."""
    d, mon = ddmon.split()
    m = months[mon]
    year = end[0] if m <= end[1] else start[0]
    if start[0] != end[0] and m >= start[1]:
        year = start[0]
    return f"{year:04d}-{m:02d}-{int(d):02d}"


def period_name(block):
    """A block's name: '<account no> · <ccy> · <yyyy-mm>'."""
    return f"{block['account_no']} · {block['currency']} · {block['period_end'][:7]}"


def header_spans(ln):
    """The words of a header line, grouped so a single space keeps them
    together: "Ordinary Account ($)" is one heading, two spaces start the next."""
    return [(m.start(), m.end(), m.group()) for m in re.finditer(r"\S+(?: \S+)*", ln)]
