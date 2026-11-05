#!/usr/bin/env python3
"""Extract Tiger Brokers annual statement PDFs into one dict per statement.

Usage:
    python3 -m finance.parsers.parse_tiger [FOLDER] [--json OUT] [--no-text]

FOLDER defaults to the data folder's statements/tiger and is searched recursively for PDFs; each
one is an annual (or year-to-date) export laid out as <year>/<from>-<to>.pdf.
Text comes from `pdftotext -layout`, the same way the DBS parser gets it, and is
saved beside the PDF as <name>.txt unless --no-text. The run prints one line per
statement and, with --json, writes the parsed dicts.

The statement is a stack of small tables (Account Overview, Cash Report, Trades,
Deposits & Withdrawals, Holdings, Dividends, Base Currency Exchange Rate) whose
column positions move between years and even between pages of one file. Three
things about the layout drive the way this parses:

  * Column assignment is positional, as in parse_statements.py. A cell carries
    no meaning on its own: "-0.09" is the GST only because of where it sits.
    Each table's header fixes the columns, and it is re-read every time it is
    printed, so a page that shifts them is read with its own header. A heading
    can be spread over three lines ("Accrued Interest in" above "Trade"), so
    the header's words are clustered by horizontal overlap into one column
    each, and a printed cell goes to the column it overlaps.

  * A row is a block of consecutive lines, not one line. Tiger wraps a cell
    over two or three lines ("Vanguard Total World" / "Stock ETF" / "(VT)"), so the
    cells of one block are collected per column and then read. Blank lines
    separate rows; the page footer and the repeated page header are dropped
    first so that a page break just looks like a taller gap. A block that ends
    with no figure in it is the top half of a row the page break tore apart
    (2025 leaves an instrument name and a trade time at the foot of one page),
    so it is held over and merged into the next block.

  * Every Forex and Stock trade is printed twice, once with its symbol and once
    without. Every year from 2022 does it, not only the newest layout. The
    symbol-less copy inherits the symbol of the row above it, and the trades
    are then de-duplicated on date, symbol, quantity and price.

Nothing here is replayed against a balance chain the way the bank statements
are. The two figures the analytics side reports, Deposits "Total(In Base)" and
Holdings "Total(In Base)", are read back off the statement rather than summed
from the parsed rows, and no currency is converted by this script.
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

# the data folder's layout (server/finance/paths.py) decides where text dumps go
from finance.paths import current_statements, statements_dir, text_dump_path

from .common import header_spans, money
from .layouts import CHECKED, Bank, Layout, Seen, register, register_bank

NUM = re.compile(r"^-?[0-9][0-9,]*(?:\.[0-9]+)?$")
ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")
PERIOD = re.compile(r"Statement Period:\s*(\d{4}-\d{2}-\d{2})\s*-\s*(\d{4}-\d{2}-\d{2})")

# the page footer and the page header interrupt a table anywhere; dropping them
# turns a page break into a gap between rows instead of a line inside one
NOISE = re.compile(r"This statement also serves as a Tax Invoice|"
                   r"Tiger Brokers \(Singapore\) PTE\.LTD\.|"
                   r"Address:1 Raffles Place|GST registration number:")

# a line carrying nothing but one of these names opens a section
SECTIONS = {
    "Account Information", "Account Overview", "Cash Report", "Transfer",
    "Trades", "Deposits & Withdrawals", "Holdings", "Withholding Tax",
    "Dividends", "Others", "GST Summary", "Allowance",
    "Financial Instrument Information", "Base Currency Exchange Rate",
}
# "Forex" and "Stock" open a subsection under Trades, under Holdings and again
# under Financial Instrument Information, so both names have to be qualified
SUBSECTIONS = {"Forex", "Stock"}

# every table is closed by Tiger's footnotes, which are prose rather than rows
NOTE = re.compile(r"^(Notes?:|\d+\.\s)")

# Cash Report rows that are a cost of holding or trading the account. The
# contract names Commissions, Platform Fees, GST, Other, Settlement Fees and
# Custody; 2022 also prints Clearing Fees and Exchange Fees, so this is the
# union of every label the real exports seen so far print. Allowance is a commission
# rebate, which is income, and stays out.
FEE_LABELS = {
    "Commissions", "Commission", "Platform Fees", "GST", "Other", "Other Fees",
    "Settlement Fees", "Clearing Fees", "Exchange Fees", "Custody", "Custody Fees",
}

SIDES = {"Open", "Close", "Buy", "Sell"}

OVERVIEW_ROWS = {"Beginning Of The Period": "begin", "End Of The Period": "end"}
# the Account Overview columns, in printed order; the middle two are read past
OVERVIEW_KEYS = ["cash", "stock", None, None, "dividend_accruals", "total"]


def total_of(numbers):
    return round(sum(numbers), 4) if numbers else 0.0


# ---------------------------------------------------------------- table layout

def word_spans(ln):
    return [(m.start(), m.end(), m.group()) for m in re.finditer(r"\S+", ln)]


class Columns:
    """The columns of one table, read off its header block.

    A heading can be spread over three lines, so the header's words are
    clustered by horizontal overlap and each cluster becomes one column,
    labelled with its own words in printed order."""

    def __init__(self, header_lines):
        spans = []
        for i, ln in enumerate(header_lines):
            spans += [(s, e, t, i) for s, e, t in header_spans(ln)]
        clusters = []
        for s, e, t, i in sorted(spans):
            for c in clusters:
                if s < c["end"] and e > c["start"]:
                    c["start"], c["end"] = min(c["start"], s), max(c["end"], e)
                    c["words"].append((i, s, t))
                    break
            else:
                clusters.append({"start": s, "end": e, "words": [(i, s, t)]})
        self.cols = [(" ".join(t for _, _, t in sorted(c["words"])), c["start"], c["end"])
                     for c in sorted(clusters, key=lambda c: c["start"])]

    def find(self, *keys):
        """The label of the first column whose heading contains every key."""
        for label, _, _ in self.cols:
            if all(k in label for k in keys):
                return label
        return None

    def column_of(self, start, end, token):
        """The column a printed token belongs to.

        Overlap decides it whenever there is any. Failing that a number goes to
        the nearest right edge, because Tiger right-aligns every figure, and
        text to the nearest left edge, because it left-aligns every name."""
        best, widest = None, 0
        for label, s, e in self.cols:
            overlap = min(end, e) - max(start, s)
            if overlap > widest:
                best, widest = label, overlap
        if best:
            return best
        if NUM.match(token):
            return min(self.cols, key=lambda c: abs(end - c[2]))[0]
        return min(self.cols, key=lambda c: abs(start - c[1]))[0]

    def cells(self, lines):
        """{column label: [token, ...]} for one row block, in printed order."""
        out = {label: [] for label, _, _ in self.cols}
        for ln in lines:
            for s, e, t in word_spans(ln):
                out[self.column_of(s, e, t)].append(t)
        return out


def text_of(cells, label):
    return " ".join(cells.get(label) or [])


def numbers_of(cells, label):
    return [money(t) for t in (cells.get(label) or []) if NUM.match(t)]


def one_number(cells, label, default=None):
    got = numbers_of(cells, label)
    return got[0] if got else default


def symbol_of(raw):
    """Split a Symbol column into (symbol, name). 2022 to 2025 print the bare
    symbol; 2026 prints the instrument's name and puts "(SYMBOL)" on the next
    line. The name is "" when only a symbol was printed."""
    raw = " ".join(raw.split())
    if m := re.search(r"\(([^()\s]+)\)\s*$", raw):
        return m.group(1), raw[:m.start()].strip()
    return raw, ""


# ---------------------------------------------------------------- row blocks

def blocks(text):
    """The statement as (section, subsection, lines) blocks: one header row or
    one table row each. Section and subsection titles are lines that carry
    nothing else; the page furniture goes first, so a table broken over a page
    still reads as one run of blocks."""
    section = subsection = None
    out, buf = [], []

    def flush():
        if buf:
            out.append((section, subsection, list(buf)))
            buf.clear()

    for ln in text.splitlines():
        if NOISE.search(ln):
            continue
        body = ln.strip()
        if not body:
            flush()
        elif body in SECTIONS:
            flush()
            section, subsection = body, None
        elif body in SUBSECTIONS:
            flush()
            subsection = body
        elif body.startswith("Currency:"):
            flush()
            subsection = body[len("Currency:"):].strip()
        else:
            buf.append(ln)
    flush()
    return out


def is_header(lines, *keys):
    joined = " ".join(lines)
    return all(k in joined for k in keys)


def is_note(lines):
    return bool(NOTE.match(lines[0].strip()))


def is_total(cells, first_label):
    """Tiger closes every table with Total / Total <SYMBOL> / Total(In Base)
    rows. 2022 wraps the last of those over two lines as "Total(In" / "Base)"."""
    head = text_of(cells, first_label).strip()
    return head.startswith("Total") or head.startswith("Base)")


def rejoin(pending, cells):
    """One row's cells with the half a page break left on the previous page put
    back in front of them. The header is reprinted after the break, so the two
    halves arrive as separate blocks over the same columns."""
    if not pending:
        return cells
    out = dict(cells)
    for label, words in pending.items():
        out[label] = words + out.get(label, [])
    return out


def trade_date(cells, cols):
    """The trade date, not the settlement date. Both sit in the row, but Tiger
    wraps the settlement date over two lines ("2026-01-" then "06") while the
    trade date always prints whole, in the Trade Time column."""
    for t in cells.get(cols.find("Trade Time")) or []:
        if ISO.match(t):
            return t
    for label, _, _ in cols.cols:
        for t in cells.get(label) or []:
            if ISO.match(t):
                return t
    return ""


# ---------------------------------------------------------------- sections

def parse_account(recs):
    """Account Information: the one row under the header."""
    cols, account, currency = None, "", ""
    for _, _, lines in recs:
        if is_header(lines, "Account", "Base Currency"):
            cols = Columns(lines)
        elif cols is not None:
            cells = cols.cells(lines)
            account = text_of(cells, cols.find("Account")).strip() or account
            currency = text_of(cells, cols.find("Base Currency")).strip() or currency
    return account, currency


def parse_overview(recs):
    """Account Overview: the opening and closing position, in base currency.
    Funds in Transit and Interest Accruals sit between the columns that are
    read and have been 0.00 on every statement so far, so they are skipped."""
    keys = [k for k in OVERVIEW_KEYS if k]
    out = {"begin": dict.fromkeys(keys, 0.0), "end": dict.fromkeys(keys, 0.0)}
    for _, _, lines in recs:
        for ln in lines:
            body = ln.strip()
            for label, which in OVERVIEW_ROWS.items():
                if not body.startswith(label):
                    continue
                got = [money(t) for _, _, t in word_spans(body[len(label):])
                       if NUM.match(t)]
                out[which].update({k: v for k, v in zip(OVERVIEW_KEYS, got) if k})
    return out


CASH_ROW = re.compile(r"^\s*(?P<label>[A-Za-z][^\d]*?)\s{2,}"
                      r"(?P<total>-?[0-9][0-9,]*(?:\.[0-9]+)?)(?:\s|$)")


def parse_cash_report(recs):
    """Cash Report, Base Currency Summary only: every row it prints, verbatim
    label to the Total column. The per-currency blocks below repeat the same
    labels in their own currency and are deliberately not read."""
    out = {}
    for _, sub, lines in recs:
        if sub != "Base Currency Summary":
            continue
        for ln in lines:
            if (m := CASH_ROW.match(ln)) and m.group("label").strip() != "Total":
                out.setdefault(m.group("label").strip(), money(m.group("total")))
    return out


def parse_deposits(recs):
    """Deposits & Withdrawals: one row per movement, then Total(In Base)."""
    cols, deposits, withdrawals, base = None, [], [], 0.0
    for _, _, lines in recs:
        if is_header(lines, "Date", "Description", "Amount", "Currency"):
            cols = Columns(lines)
            continue
        if cols is None or is_note(lines):
            continue
        cells = cols.cells(lines)
        date = text_of(cells, cols.find("Date")).strip()
        what = text_of(cells, cols.find("Description")).strip()
        amount = one_number(cells, cols.find("Amount"))
        if what.startswith("Total(In Base)"):
            base = base if amount is None else amount
        elif ISO.match(date) and amount is not None:
            row = {"date": date, "amount": abs(amount),
                   "currency": text_of(cells, cols.find("Currency")).strip()}
            (withdrawals if "Withdraw" in what else deposits).append(row)
    return deposits, withdrawals, base


def parse_holdings(recs):
    """Holdings, Stock: the position at the end of the period, plus the
    Total(In Base) value that analytics reports as the portfolio's worth."""
    cols, rows, base, pending = None, [], 0.0, {}
    for _, sub, lines in recs:
        if sub != "Stock":
            continue
        if is_header(lines, "Symbol", "Cost Price", "Close Price", "Value"):
            cols = Columns(lines)
            continue
        if cols is None:
            continue
        if is_note(lines):
            pending = {}
            continue
        cells, pending = rejoin(pending, cols.cells(lines)), {}
        first = cols.find("Symbol")
        head = text_of(cells, first).strip()
        value = one_number(cells, cols.find("Value"))
        if head.startswith("Total(In") or head.startswith("Base)"):
            base = base if value is None else value
        elif is_total(cells, first):
            continue
        elif value is None:
            pending = cells if head else {}
        else:
            symbol, name = symbol_of(head)
            rows.append({
                "symbol": symbol,
                "name": name,
                "quantity": one_number(cells, cols.find("Quantity"), 0.0),
                "cost_price": one_number(cells, cols.find("Cost Price"), 0.0),
                "close_price": one_number(cells, cols.find("Close Price"), 0.0),
                "value": value,
                "unrealized_pl": one_number(cells, cols.find("Unrealized"), 0.0),
                "currency": text_of(cells, cols.find("Currency")).strip(),
            })
    return rows, base


def parse_dividends(recs):
    """Dividends: the cash actually paid. Tiger also prints the accrual moving
    in and back out ("Dividend Accruals Increase" / "Reduction"), which is the
    same money a second and a third time, so only the Paid phase is kept."""
    cols, rows, pending = None, [], {}
    for _, _, lines in recs:
        if is_header(lines, "Phase", "Shares", "Fees & Tax"):
            cols = Columns(lines)
            continue
        if cols is None:
            continue
        if is_note(lines):
            pending = {}
            continue
        cells, pending = rejoin(pending, cols.cells(lines)), {}
        date = "".join(cells.get(cols.find("Date")) or [])
        phase = text_of(cells, cols.find("Phase")).strip()
        net_label = cols.find("Net Cash")
        if not phase and not numbers_of(cells, net_label):
            pending = cells                       # half a row, cut by a page break
        elif phase == "Paid" and ISO.match(date):
            symbol, _ = symbol_of(text_of(cells, cols.find("Symbol")))
            rows.append({
                "date": date,
                "symbol": symbol,
                "gross": one_number(cells, cols.find("Cash", "Dividends"), 0.0),
                "tax": one_number(cells, cols.find("Fees & Tax"), 0.0),
                "net": one_number(cells, net_label, 0.0),
                "currency": text_of(cells, cols.find("Currency")).strip(),
                "phase": phase,
            })
    return rows


def parse_forex(recs):
    """Trades, Forex: one row per currency conversion."""
    cols, rows, last, pending = None, [], "", {}
    for section, sub, lines in recs:
        if section != "Trades" or sub != "Forex":
            continue
        if is_header(lines, "Symbol(Base.Quote)", "Quantity(Base)"):
            cols = Columns(lines)
            continue
        if cols is None:
            continue
        if is_note(lines):
            pending = {}
            continue
        cells, pending = rejoin(pending, cols.cells(lines)), {}
        first = cols.find("Symbol(Base.Quote)")
        if is_total(cells, first):
            continue
        amount = one_number(cells, cols.find("Amount(Quote)"))
        if amount is None:
            pending = cells
            continue
        last = pair = text_of(cells, first).strip() or last
        rows.append({
            "date": trade_date(cells, cols),
            "pair": pair,
            "side": text_of(cells, cols.find("Activity Type")).strip(),
            "quantity": one_number(cells, cols.find("Quantity(Base)"), 0.0),
            "price": one_number(cells, cols.find("Trade Price"), 0.0),
            "amount": amount,
            "currency": text_of(cells, cols.find("Currency")).strip(),
        })
    return dedupe(rows, "pair")


def parse_trades(recs):
    """Trades, Stock: one row per fill. Comm/Fee holds up to five separate
    charges (commission, platform, settlement, clearing, exchange), each on its
    own line, so the fee is their sum and matches Tiger's own Total row."""
    cols, rows, last, pending = None, [], "", {}
    for section, sub, lines in recs:
        if section != "Trades" or sub != "Stock":
            continue
        if is_header(lines, "Symbol", "Comm/Fee", "GST"):
            cols = Columns(lines)
            continue
        if cols is None:
            continue
        if is_note(lines):
            pending = {}
            continue
        cells, pending = rejoin(pending, cols.cells(lines)), {}
        first = cols.find("Symbol")
        if is_total(cells, first):
            continue
        amount = one_number(cells, cols.find("Amount"))
        if amount is None:
            pending = cells
            continue
        symbol, _ = symbol_of(text_of(cells, first))
        last = symbol = symbol or last
        rows.append({
            "date": trade_date(cells, cols),
            "symbol": symbol,
            "side": next((t for t in cells.get(cols.find("Activity Type")) or []
                          if t in SIDES), ""),
            "quantity": one_number(cells, cols.find("Quantity"), 0.0),
            "price": one_number(cells, cols.find("Trade Price"), 0.0),
            "amount": amount,
            "fee": total_of(numbers_of(cells, cols.find("Comm/Fee"))),
            "gst": one_number(cells, cols.find("GST"), 0.0),
            "currency": text_of(cells, cols.find("Currency")).strip(),
        })
    return dedupe(rows, "symbol")


def dedupe(rows, key):
    """Drop the second print of a trade. Once the symbol-less copy has taken
    the symbol of the row above it the two are identical, so the first of each
    (date, symbol, quantity, price) is kept and the rest dropped."""
    seen, out = set(), []
    for r in rows:
        k = (r["date"], r[key], r["quantity"], r["price"])
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out


FX_ROW = re.compile(r"^\s*(?P<ccy>[A-Z]{3})\s+(?P<rate>[0-9][0-9,]*(?:\.[0-9]+)?)\s*$")
FX_DATE = re.compile(r"^\s*Date\s+(?P<date>\d{4}-\d{2}-\d{2})\s*$")


def parse_fx(recs):
    """Base Currency Exchange Rate: one unit of each currency in the base
    currency, as of the last day of the period."""
    rates, date = {}, ""
    for section, _, lines in recs:
        if section != "Base Currency Exchange Rate":
            continue
        for ln in lines:
            if m := FX_DATE.match(ln):
                date = m.group("date")
            elif m := FX_ROW.match(ln):
                rates[m.group("ccy")] = money(m.group("rate"))
    return rates, date


# ---------------------------------------------------------------- one statement

def parse_text(text, file):
    """One statement dict. `file` is the path the statement came from, relative
    to the folder it sits in, and is carried through untouched."""
    recs = blocks(text)
    of = lambda name: [r for r in recs if r[0] == name]

    m = PERIOD.search(text)
    period = {"from": m.group(1), "to": m.group(2)} if m else {"from": "", "to": ""}
    account, base_currency = parse_account(of("Account Information"))
    cash = parse_cash_report(of("Cash Report"))
    deposits, withdrawals, deposits_base = parse_deposits(of("Deposits & Withdrawals"))
    holdings, holdings_base = parse_holdings(of("Holdings"))
    fx, fx_date = parse_fx(recs)

    return {
        "file": file,
        "account": account,
        "base_currency": base_currency,
        "period": period,
        "overview": parse_overview(of("Account Overview")),
        "cash_report": cash,
        "deposits": deposits,
        "withdrawals": withdrawals,
        "deposits_base": deposits_base,
        "dividends": parse_dividends(of("Dividends")),
        "dividends_base": cash.get("Dividends", 0.0),
        "withholding_tax_base": cash.get("Withholding Tax", 0.0),
        "fees_base": total_of([v for k, v in cash.items() if k in FEE_LABELS]),
        "holdings": holdings,
        "holdings_value_base": holdings_base,
        "trades": parse_trades(recs),
        "forex": parse_forex(recs),
        "fx_to_base": fx,
        "fx_date": fx_date or period["to"],
    }


# ---------------------------------------------------------------- files

def pdf_to_text(pdf, write=True):
    """The layout-preserved text of a PDF, the same call the DBS parser makes,
    saved as a dump (finance.paths.text_dump_path) so a later run can be
    checked against it."""
    out = subprocess.run(["pdftotext", "-layout", str(pdf), "-"],
                         capture_output=True, text=True, check=True).stdout
    if write:
        text_dump_path(pdf).write_text(out)
    return out


def parse_pdf(path, folder, write_text=True):
    """One statement, named by its path relative to the folder it sits in."""
    path, folder = Path(path), Path(folder)
    try:
        name = path.resolve().relative_to(folder.resolve()).as_posix()
    except ValueError:
        name = path.name
    return parse_text(pdf_to_text(path, write=write_text), name)


def parse_folder(folder, write_text=True):
    """Every PDF under the folder, oldest statement first: by the period's
    start, then its end, so of two exports that start on the same day the
    longer comes last whatever the files are called ("2025/1-12.pdf" sorts
    before "2025/1-8.pdf" by name)."""
    folder = Path(folder)
    if not folder.is_dir():
        return []
    out = [parse_pdf(pdf, folder, write_text=write_text)
           for pdf in current_statements(folder.rglob("*.pdf"))]
    return sorted(out, key=lambda s: (s["period"]["from"], s["period"]["to"], s["file"]))


def summary(s):
    n = lambda x: f"{x:>11,.2f}"
    deposited = sum(d["amount"] for d in s["deposits"])
    ccy = s["deposits"][0]["currency"] if s["deposits"] else ""
    return (f"  {s['file']:<14} {s['period']['from']} .. {s['period']['to']}"
            f"  begin {n(s['overview']['begin']['total'])}"
            f"  end {n(s['overview']['end']['total'])}"
            f"  deposits {n(deposited)} {ccy:<3}"
            f"  holdings {n(s['holdings_value_base'])} {s['base_currency']}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", nargs="?", default=str(statements_dir() / "tiger"),
                    help="folder of statement PDFs (default: statements/tiger in the data folder)")
    ap.add_argument("--json", metavar="OUT", help="write the parsed statements here")
    ap.add_argument("--no-text", action="store_true",
                    help="do not save the .txt dump beside each PDF")
    args = ap.parse_args()

    if not shutil.which("pdftotext"):
        sys.exit("pdftotext not found (brew install poppler)")
    folder = Path(args.folder)
    if not folder.is_dir():
        sys.exit(f"not a directory: {folder}")

    statements = parse_folder(folder, write_text=not args.no_text)
    if not statements:
        sys.exit(f"no PDFs found under {folder}")

    print(f"{len(statements)} statement(s) from {folder}\n")
    for s in statements:
        print(summary(s))

    last = statements[-1]
    sgd = last["fx_to_base"].get("SGD")
    print(f"\n  {len(last['holdings'])} holding(s) at {last['period']['to']}, "
          f"{last['holdings_value_base']:,.2f} {last['base_currency']}"
          + (f", {1 / sgd:,.4f} SGD per {last['base_currency']}"
             f" = {last['holdings_value_base'] / sgd:,.2f} SGD" if sgd else ""))
    print(f"  {sum(len(s['deposits']) for s in statements)} deposits, "
          f"{sum(len(s['withdrawals']) for s in statements)} withdrawals, "
          f"{sum(len(s['trades']) for s in statements)} stock trades, "
          f"{sum(len(s['forex']) for s in statements)} forex trades, "
          f"{sum(len(s['dividends']) for s in statements)} dividends")

    if args.json:
        out = Path(args.json)
        if str(out.parent) not in ("", "."):
            out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(statements, indent=2) + "\n")
        print(f"\nwrote {out}")


# ---------------------------------------------------------------- the layout (G21)

def _sees(text):
    if not PERIOD.search(text) or "Tiger Brokers" not in text:
        return None
    account, _ = parse_account([r for r in blocks(text) if r[0] == "Account Information"])
    return Seen(numbers=(account,))


def name_upload(pdf, read_text, wallet, today):
    """(year folder, file name) for an uploaded export: the months it covers."""
    st = parse_pdf(pdf, Path(pdf).parent, write_text=False)
    a, b = st["period"]["from"][:7], st["period"]["to"][:7]
    return a[:4], f"{a}_{b}.pdf"


# a valuation on one day, never transaction rows: no CSV, and its folder is
# `[analytics] tiger_folder` rather than a [[folder]]
register_bank(Bank("tiger", r"\btiger\b", order=90, name="Tiger Brokers"))
register(Layout(
    name="tiger", parser="tiger", module=__spec__.name, order=90, kind="brokerage",
    label="a Tiger Brokers statement", folder="tiger", setup="Tiger Brokers yearly statement",
    test=_sees, bank="tiger", folder_title="Tiger Brokers", name_file=name_upload, status=CHECKED))


if __name__ == "__main__":
    main()
