"""A statement's text with what identifies its holder replaced, for whoever
builds a layout for it (G21: `finance.py sample <pdf>`).

A bank Kopifund has no layout for (Trust, GXS and MariBank among them) gets
one only from a statement's text: its headings, columns, dates and balance
lines. This turns a PDF into that text the way the parsers read it
(`pdftotext -layout`), with every name, street address, postcode, NRIC or FIN
number, email, phone number and account or card number replaced by a
placeholder of the same length and shape: a digit becomes 0, a capital X, a
small letter x, and everything else stays. Every line keeps its length, so the
columns stay where the statement printed them. Amounts and dates are never
replaced, even inside a span that is.

It is best effort, a set of patterns rather than a guarantee, so the command
prints what it replaced, kind by kind, and the owner reads the text before
sharing it (docs/parsing.md, "Trust, GXS and MariBank: no layout yet").

Stdlib only. It reads the PDF and writes one text file; it never changes or
moves the PDF, never writes into the repository and sends nothing anywhere.
"""
import re
import subprocess
from pathlib import Path

from .atomic import write_private
from .paths import REPO
from .pdftext import pdf_text

# the kinds, in the order the report names them
KINDS = ("name", "address", "postcode", "id", "email", "phone", "account")
LABELS = {"name": ("name", "names"), "address": ("street address", "street addresses"),
          "postcode": ("postcode", "postcodes"), "id": ("NRIC or FIN number", "NRIC or FIN numbers"),
          "email": ("email", "emails"), "phone": ("phone number", "phone numbers"),
          "account": ("account or card number", "account and card numbers")}

# what is never replaced: an amount (1,234.56, -12.40, 0.00 SGD) and a date
# (2026-03-01, 01/03/2026, 1 Mar 2026, 01 MAR, March 3, 2026)
AMOUNT = re.compile(r"(?<![\d,.])-?(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2}(?!\d)")
_DAY, _MONTH = r"(?:0?[1-9]|[12]\d|3[01])", r"(?:0?[1-9]|1[0-2])"
_MON = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
DATE = re.compile(rf"(?<!\d)(?:(?:19|20)\d{{2}}-{_MONTH}-{_DAY}|{_DAY}[/-]{_MONTH}[/-](?:19|20)?\d{{2}})(?!\d)")
TEXT_DATE = re.compile(rf"(?i)(?<![\w]){_DAY}\s+{_MON}(?:\s+(?:19|20)\d{{2}})?(?![a-z])"
                       rf"|(?<![\w]){_MON}\s+{_DAY},?\s+(?:19|20)\d{{2}}(?!\d)")

# a domain ends in letters, so a rate printed as SGD@1.281713 is not an email
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}(?![A-Za-z0-9])")
NRIC = re.compile(r"(?<![A-Za-z0-9])[STFGMstfgm](?:\d{7}|[X*•]{4}\d{3})[A-Za-z](?![A-Za-z0-9])")
IBAN = re.compile(r"(?<![A-Za-z0-9])[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,4})?(?![A-Za-z0-9])")
# a Singapore number: +65 6123 4567, (65) 9123-4567, 6591234567, 81234567
PHONE = re.compile(r"(?<![\d.,])(?:\+65[ -]?|\(65\)[ -]?|65)?[3689]\d{3}[ -]?\d{4}(?!\d)(?![.,]\d)")
CARD = re.compile(r"(?<![\d.,])\d{4}([ -]?)\d{4}\1\d{4}\1\d{1,4}(?!\d)(?![.,]\d)")
# a masked card or account: XXXX-XXXX-XXXX-1234, xx-4242, 123456******1234, REVOLUT**0000*, *1234
MASKED = re.compile(r"(?<![A-Za-z0-9])\d*(?:[*Xx•]{2,}[ -]?)+\d{2,4}(?!\d)"
                    r"|(?<!\d)\d*(?:[*•]{2,}[ -]?)+\d{2,4}(?!\d)|(?<![\w*])\*\d{4}(?!\d)")
ENDING = re.compile(r"(?i)\bending(?: in| with)?\s+(\d{3,4})\b")
# any other run of seven or more digits, hyphens allowed between them: 120-345678-9,
# Y-1029384756, 885100012345678, a reference
DIGITS = re.compile(r"(?<!\d)(?<!\d[.,])\d+(?:-\d+)*(?!\d)(?![.,]\d)")
LONE_NUMBER = re.compile(r"[A-Z]{0,3}-?\d[\d-]{5,}\d")   # a cell that is one account number: Y-1029384756
POSTCODE = re.compile(r"(?:(?i:\bsingapore\b)[ ,]*|\bS\s?\(?)(\d{6})(?!\d)|^\s*(\d{6})\s*$")
UNIT = re.compile(r"#\d{1,3}\s?-\s?\d{1,5}[A-Za-z]?\b")
BLOCK = re.compile(r"(?i)\b(?:blk|block)\s+\d+[a-z]?\b")
_ROAD = (r"(?:road|rd|street|st|avenue|ave|drive|dr|lane|ln|crescent|cres|close|walk|way|place|pl|terrace|"
         r"park|rise|central|link|view|hill|grove|gardens?|boulevard|blvd|jalan|jln|lorong|lor|loop|green|"
         r"vale|heights|ring|circle|square|quay|parkway|north|south|east|west)")
# 100A ALPHA RD 1, 2 Tanjong Katong Road, ...; or, when the block number sits in
# a column of its own, ANG MO KIO AVE 3
STREET = re.compile(rf"(?i)^\d+[a-z]?,?\s+(?:[a-z][a-z'.-]*\s+){{0,5}}{_ROAD}\b"
                    rf"|^(?:[a-z][a-z'.-]*\s+){{1,5}}{_ROAD}\s+\d+[a-z]?$")
BLOCK_NO = re.compile(r"\d+[A-Za-z]?")
TITLE = re.compile(r"\b(?:MR|MRS|MS|MISS|MDM|DR\.|Mr|Mrs|Ms|Miss|Mdm|Dr\.)\.?\s+([A-Z][A-Za-z'-]+(?:\s[A-Z][A-Za-z'-]+){0,4})")
# a label whose cell, or the cell under it, is a name
HOLDER = re.compile(r"(?i)^(?:account holder|account name|customer name|card ?holder|name)\s*:?\s*(.*)$")
# a counterparty: the rest of the cell after these words
PARTY = re.compile(
    r"(?i)\b(?:to|from)\s*:\s*([a-z].*)$|"
    r"\b(?:transfer to|transfer from|payment to|payment from|paid to|paid by|received from|sent to|"
    r"paynow to|paynow from|"
    r"received money from|sent money to|send money to|receive money from)\s+([a-z].*)$")
# not a person: a name-shaped line holding one of these is a heading, a bank or a company
NOT_A_NAME = set("""
BANK LTD LIMITED PTE PRIVATE CO CORP INC BHD LLC PLC GROUP STATEMENT STATEMENTS ACCOUNT ACCOUNTS SUMMARY
PAGE TOTAL BALANCE DETAILS CONTACT CALL HOTLINE CENTRE CENTER TOWER BUILDING PLAZA BRANCH REG NO DATE
PERIOD CURRENCY DEPOSIT DEPOSITS SAVINGS WALLET CARD CARDS DEBIT CREDIT TRANSACTION TRANSACTIONS SINGAPORE
DBS POSB OCBC UOB PAYLAH YOUTRIP REVOLUT WISE TIGER CPF TRUST GXS MARI MARIBANK OF AS AT FOR THE AND US
JAN FEB MAR APR MAY JUN JUL AUG SEP SEPT OCT NOV DEC JANUARY FEBRUARY MARCH APRIL JUNE JULY AUGUST
SEPTEMBER OCTOBER NOVEMBER DECEMBER INTEREST INVOICE TAX GST ADVICE OVERVIEW PRODUCT HOLDER NAME
FUND BOARD PROVIDENT BROKERS SECURITIES CENTRAL NOMINATION REPORT MONTHLY HISTORY CONSOLIDATED NEW
MAIN POT POTS POCKET POCKETS
""".split())
# a row that moves money to or from a person
TRANSFER = re.compile(r"(?i)\b(?:paynow|fast|transfer|giro|funds|remittance|ibg|interbank)\b")
# the owner's own words after "to" or "from": a move between their own accounts
OWN = re.compile(r"(?i)^(?:my|own|your|self)\b")
WORD = re.compile(r"[A-Za-z][A-Za-z'.-]*")
CELL = re.compile(r"\S+(?: \S+)*")         # text between runs of two or more spaces: one column's cell


def mask(text):
    """The same length and shape: a digit 0, a capital X, a small letter x."""
    return "".join("0" if c.isdigit() else "X" if c.isupper() else "x" if c.isalpha() else c for c in text)


def _name_shaped(cell):
    words = cell.split()
    return (2 <= len(words) <= 6 and all(WORD.fullmatch(w) for w in words)
            and not any(w.strip(".'-").upper() in NOT_A_NAME for w in words))


def _cells(line):
    return [(m.start(), m.end(), m.group(0)) for m in CELL.finditer(line)]


def scrub(text):
    """(the text with every match replaced, {kind: how many}). Every line
    keeps its length; amounts and dates are never touched."""
    lines = text.split("\n")
    starts, at = [], 0
    for ln in lines:
        starts.append(at)
        at += len(ln) + 1
    spans = []                               # (start, end, kind), offsets into text; earlier wins

    def add(start, end, kind):
        if end > start:
            spans.append((start, end, kind))

    def every(rx, kind, group=0):
        for m in rx.finditer(text):
            add(m.start(group), m.end(group), kind)

    def street(cell):
        return bool(BLOCK.search(cell) or (STREET.search(cell) and not TEXT_DATE.match(cell)))

    def address(cell):
        return street(cell) or bool(UNIT.search(cell) or POSTCODE.search(cell))

    every(EMAIL, "email")
    every(NRIC, "id")
    every(IBAN, "account")
    every(CARD, "account")
    every(MASKED, "account")
    every(ENDING, "account", 1)
    for i, ln in enumerate(lines):
        at = starts[i]
        for m in PHONE.finditer(ln):
            add(at + m.start(), at + m.end(), "phone")
        for m in DIGITS.finditer(ln):
            if sum(c.isdigit() for c in m.group(0)) >= 7 and not DATE.fullmatch(m.group(0)):
                add(at + m.start(), at + m.end(), "account")
        for m in POSTCODE.finditer(ln):
            g = 1 if m.group(1) else 2
            add(at + m.start(g), at + m.end(g), "postcode")
        for m in UNIT.finditer(ln):
            add(at + m.start(), at + m.end(), "address")

    # street lines, and the names above them: a holder's block is a name or
    # two, then the address, in one column
    names = set()
    anchors = []                             # (line, column) of an address or email cell
    page_line = 0                            # non-blank lines since the page began
    detail = 0                               # detail lines left under a transfer row
    for i, ln in enumerate(lines):
        if "\f" in ln:
            page_line = 0
        cells = _cells(ln)
        page_line += bool(cells)
        # a transfer's detail lines often name who it went to or came from, alone
        # in the description column: UOB's PAYNOW-FAST, then NG AH LAN, or Funds
        # Transfer, its reference, then LIM AH KOW. The first that reads as a name is one
        if len(cells) == 1 and detail:
            detail -= 1
            if PARTY.search(cells[0][2]):
                detail = 0
            elif _name_shaped(cells[0][2]):
                names.add(cells[0][2])
                add(starts[i] + cells[0][0], starts[i] + cells[0][1], "name")
                detail = 0
        elif cells:
            detail = 3 if TRANSFER.search(ln) else 0
        for n, (s, e, cell) in enumerate(cells):
            col = s                          # where the address starts, for the names above it
            if street(cell):
                add(starts[i] + s, starts[i] + e, "address")
                # a block number printed in a column of its own, just before the street
                if n and BLOCK_NO.fullmatch(cells[n - 1][2]) and s - cells[n - 1][1] <= 8:
                    col = cells[n - 1][0]
                    add(starts[i] + col, starts[i] + cells[n - 1][1], "address")
            # a name sits over its address, its email, or an account number in a
            # column of its own (YouTrip prints the holder over the wallet's number)
            if address(cell) or EMAIL.fullmatch(cell) or LONE_NUMBER.fullmatch(cell):
                anchors.append((i, col, e))
            # the holder's name alone at the left of a line near the top of a
            # page, in capitals, as most statements print it over the address
            if (n == 0 and s <= 12 and page_line <= 15 and cell.isupper() and _name_shaped(cell)
                    and len(cell.split()) <= 5):
                names.add(cell)
                add(starts[i] + s, starts[i] + e, "name")
            holder = HOLDER.match(cell)
            if holder:
                rest = holder.group(1).strip()
                if rest and _name_shaped(rest):
                    names.add(rest)
                elif not rest:
                    below = next((j for j in range(i + 1, min(i + 4, len(lines))) if lines[j].strip()), None)
                    if below is not None:
                        for bs, be, bcell in _cells(lines[below]):
                            if abs(bs - s) <= 3 and _name_shaped(bcell):
                                names.add(bcell)
                                add(starts[below] + bs, starts[below] + be, "name")
            for m in PARTY.finditer(cell):
                g = 1 if m.group(1) else 2
                if OWN.match(m.group(g)):
                    continue
                # a person or a business prints in capitals, up to the statement's
                # own words: "Amita Roy" of "Amita Roy with reference", and nothing
                # of "received from selling securities"
                end = m.start(g)
                for w in re.finditer(r"\S+", m.group(g)):
                    if w.group(0)[0].islower():
                        break
                    end = m.start(g) + w.end()
                if end > m.start(g):
                    add(starts[i] + s + m.start(g), starts[i] + s + end, "name")
                    who = cell[m.start(g):end]
                    if _name_shaped(who):
                        names.add(who)
            for m in TITLE.finditer(cell):
                names.add(m.group(1))
    for i, col, end in anchors:
        for j in range(i - 1, max(i - 6, -1), -1):
            # in the same column: starting where it starts, or ending near where it
            # ends when the column is set against the right margin
            above = next(((s, e, c) for s, e, c in _cells(lines[j])
                          if abs(s - col) <= 2 or (s > col - 8 and abs(e - end) <= 6)), None)
            if above is None:
                break
            s, e, c = above
            if _name_shaped(c):
                names.add(c)
                add(starts[j] + s, starts[j] + e, "name")
            elif not address(c):
                break

    # every name found, wherever else it is printed, in any case and spacing
    for name in sorted(names, key=len, reverse=True):
        rx = re.compile(r"(?i)(?<![A-Za-z])" + r"\s+".join(map(re.escape, name.split())) + r"(?![A-Za-z])")
        for m in rx.finditer(text):
            add(m.start(), m.end(), "name")

    # resolve: each character goes to the first span over it, in the order the
    # kinds were found; a kind is counted once per span that replaced anything
    keep = [False] * len(text)
    for rx in (AMOUNT, DATE, TEXT_DATE):
        for m in rx.finditer(text):
            for k in range(m.start(), m.end()):
                keep[k] = True
    owner = [None] * len(text)
    counts = {k: 0 for k in KINDS}
    for start, end, kind in spans:
        took = False
        for k in range(start, end):
            if owner[k] is None and not keep[k] and text[k].isalnum():
                owner[k] = kind
                took = True
        if took:
            counts[kind] += 1
    out = "".join(mask(c) if owner[k] else c for k, c in enumerate(text))
    return out, counts


class SampleRefused(Exception):
    """A sample this will not write; the message says why."""


def target(pdf, out=None):
    """Where the sample goes: `out`, else `<name>.sample.txt` beside the PDF.
    Never inside the repository, and never the PDF itself."""
    pdf = Path(pdf)
    # the path that is checked is the path that is written: ~ expanded, links followed
    to = (Path(out).expanduser() if out else pdf.with_name(f"{pdf.stem}.sample.txt")).resolve()
    same = to.exists() and pdf.exists() and to.samefile(pdf)      # a hard link to the PDF, too
    if same or to.suffix.lower() == ".pdf":
        raise SampleRefused(f"{to} is a PDF; give a path for the text, such as {pdf.stem}.sample.txt")
    if to.is_dir():
        raise SampleRefused(f"{to} is a folder; give a file name for the text, such as {pdf.stem}.sample.txt")
    if to.is_relative_to(REPO.resolve()):
        raise SampleRefused(f"{to} is inside the Kopifund repository, which never holds a statement's "
                            "text; give a path outside it with --out")
    return to


def write(pdf, out=None, read=None):
    """Write the sample and return (path, counts, lines). The PDF is only read;
    `read` (pdf_text by default) is what turns it into text."""
    pdf = Path(pdf)
    if not pdf.is_file():
        raise SampleRefused(f"{pdf} is not a file")
    to = target(pdf, out)
    try:
        text = (read or pdf_text)(pdf)
    except FileNotFoundError as e:
        raise SampleRefused("The pdftotext command is not installed (brew install poppler)") from e
    except subprocess.CalledProcessError as e:
        raise SampleRefused(f"The pdftotext command could not read {pdf.name}; it may be damaged or not a PDF") from e
    scrubbed, counts = scrub(text)
    # a new file, private until the owner has read it, put in place in one step:
    # an existing file's looser mode is never kept, and a file linked to another
    # name (the PDF included) is replaced, never written through
    try:
        to.parent.mkdir(parents=True, exist_ok=True)
        write_private(to, scrubbed)
    except OSError as e:
        raise SampleRefused(f"Could not write {to}: {e.strerror or e}") from e
    return to, counts, scrubbed.count("\n") + 1


def report(counts):
    """`3 names, 1 email, ...`, every kind, so a zero is seen too."""
    return ", ".join(f"{counts.get(k, 0)} {LABELS[k][0] if counts.get(k, 0) == 1 else LABELS[k][1]}"
                     for k in KINDS)
