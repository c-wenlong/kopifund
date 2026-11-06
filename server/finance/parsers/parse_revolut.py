#!/usr/bin/env python3
"""Extract Revolut "all accounts" statement PDFs into transaction rows.

Usage:
    python3 -m finance.parsers.parse_revolut [PATH ...] [--out DIR] [--wallet CCY] [--no-text]

PATH may be a PDF or a directory (searched recursively); it defaults to
data/revolut/. Each PDF's text is written beside it as <name>.txt, then
parsed. With --out, transactions.csv and period_balances.csv are written there
with the same columns as parse_statements.py, so every feed loads the same way.

Revolut's "Export all accounts" produces one PDF with a section per currency
wallet (NOK, SEK, SGD, ...), each section a statement of its own: an "XXX
Statement" heading on every page, a balance summary, the transaction table.
The folder holds the "All time" export, from the first transaction to the day
it was generated, and a re-export replaces that file. The parser splits the
PDF into sections by the currency each page names, so a single-wallet export
parses the same way; two files whose sections cover the same months stop the
run with a duplicate-period error rather than loading a month twice.

The table has five columns: Date, Description, Money out, Money in, Balance,
with the header repeated on every page and the column right-edges taken from
each header (the layout narrows after the first page). A row is the date line
plus the indented detail lines that follow it up to the next blank line:

    Reference: <text>                      the sender's or recipient's note
    To: <name or merchant, city>           who was paid, or the card acquirer
    From: <name or card>                   who paid in
    Card: 400012******1234                 the row is card spend
    Fee: <fee>            <net>            the column amount includes the fee
    Revolut Rate 1.00 SEK = S$0.12 (...)   <foreign amount>  on foreign card spend
    <amount in another currency>           the other side of an exchange or a
                                           transfer sent in another currency

Row kinds and how they are typed:

    "Apple Pay top-up by *NNNN", "Top-up by *NNNN", "Payment from <owner>"
                                         Revolut Top-up      (Internal - Revolut)
    "Exchanged to XXX"                   Revolut Exchange In / Out (Internal -
                                         Revolut); the other side goes to
                                         fx_original, its code to memo
    "<owner name>" with no detail lines  Revolut Withdrawal  (Internal - Revolut;
                                         money sent back to the owner's bank)
    "Transfer to NAME" / "Transfer from NAME"
                                         Revolut Transfer    (External; the name
                                         is the counterparty, the Reference line
                                         the reference, a foreign amount the
                                         fx_original)
    anything with a Card: line           Revolut Payment     (External; the To:
                                         line is the merchant, its city the
                                         country, the foreign amount fx_original)

Every row prints its balance, so the balance chain is replayed row by row and
each section's rows are checked against its own balance summary (which counts
a fee taken off incoming money as money in and money out both). A section is
split into one period block per calendar month from the first month with rows
to the month the export was generated in, opening at the running balance and
closing at the last printed balance of the month; an idle month carries a
block with no rows and the balance unchanged, so every month is covered and
the account's coverage ends on the export date. Only the SGD section prints
the account number; the other sections borrow it. The balance summary is
required: a section without one stops the run before anything is written,
since its rows would open at zero and could only be checked against balances
derived from themselves.

Foreign-wallet blocks carry fx_to_sgd, the SGD value of one wallet unit from
the latest row up to that month that shows both sides in SGD and the wallet's
currency (an exchange, or card spend with a printed S$ amount); a wallet that
has never touched SGD borrows the rate through the currency it was exchanged
from, and a month before the wallet's first rate takes the first one after
it. closing_sgd = closing x fx_to_sgd.
"""
import argparse
import calendar
import csv
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

from finance.pdftext import pdf_to_text, require as require_pdftotext
from .common import period_name, plain
from .parse_statements import (statement_folders, BAL_COLS, MONTHS, TXN_COLS, add_keys,
                              balance_summary, collect_pdfs, direction, money,
                              report_duplicate_periods, warn)
from . import places  # (the city table and country split every feed shares)
from .wallet_months import every_month, month_end, split_months
from .layouts import CHECKED, Bank, Layout, Seen, by_download_day, register, register_bank

ACCOUNT = "Revolut wallet"
OWNER_TOPUP = re.compile(r"^Payment from (?P<who>.+)$")
NUM = r"-?[0-9][0-9,]*(?:\.[0-9]+)?"
# amounts in the column currency: "S$1,234.56", "€3.70", "$12.34" (not "S$"),
# "1,234.56 SEK"
SYMBOL = {"SGD": r"S\$", "EUR": "€", "USD": r"(?<!S)\$", "GBP": "£", "JPY": "¥"}
ANY_SYMBOL = {"S$": "SGD", "€": "EUR", "$": "USD", "£": "GBP", "¥": "JPY"}
SYM_AMOUNT = re.compile(r"(?P<s>S\$|€|£|¥|(?<![A-Z])\$)(?P<a>" + NUM + ")")
CODE_AMOUNT = re.compile(r"(?<![\w$])(?P<a>" + NUM + r") (?P<c>[A-Z]{3})\b")

STMT = re.compile(r"^\s*([A-Z]{3}) Statement\s*$")
GENERATED = re.compile(r"Generated on the (?P<d>\d{1,2} [A-Z][a-z]{2,8}\.? \d{4})")
ACCT = re.compile(r"Account Number\s+(?P<n>\d{6,})")
PERIOD = re.compile(r"Account transactions from (?P<a>\d{1,2} [A-Z][a-z]+ \d{4}) to "
                    r"(?P<b>\d{1,2} [A-Z][a-z]+ \d{4})")
SUMMARY = re.compile(r"^\s*Account \(E-Money\)\s")
HEADER = re.compile(r"^\s*Date\s+Description\s+Money out\s+Money in\s+Balance\s*$")
DATE = re.compile(r"^\s{0,3}(?P<d>\d{1,2} [A-Z][a-z]{2,4} \d{4})\s{2,}(?P<rest>\S.*)$")
DETAIL = re.compile(r"^\s{8,}(?P<t>\S.*)$")
FOOTER = re.compile(r"Report lost or stolen card|Revolut Technologies|Get help directly|"
                    r"Scan the QR code|^\s*©|Page \d+ of \d+|registered address|licence no|"
                    r"insurance|^\s*\+65 ")
CARD = re.compile(r"^Card:\s*(?P<pan>[\d*]+)$")
FEE = re.compile(r"^Fee:\s*(?P<fee>\S+(?: [A-Z]{3})?)(?:\s{2,}(?P<net>.+))?$")
RATE = re.compile(r"^Revolut Rate (?P<rate>.+?) \(ECB rate\*.*?\)(?:\s{2,}(?P<fx>.+))?$")
TOPUP = re.compile(r"top-up by (?P<from>\*\d+)$", re.I)
TRANSFER = re.compile(r"^Transfer (?P<dir>to|from) (?P<who>.+)$")
EXCHANGE = re.compile(r"^Exchanged to (?P<ccy>[A-Z]{3})$")
INTERNAL = ("Revolut Top-up", "Revolut Exchange In", "Revolut Exchange Out", "Revolut Withdrawal")

# Revolut prints "Sept"; the period line prints the month in full
MONTH_NO = {**MONTHS, "Sept": 9,
            **{calendar.month_name[i]: i for i in range(1, 13)}}


def sections(text):
    """Split a PDF's text into (currency, text) runs of consecutive pages that
    name the same currency in their "XXX Statement" heading."""
    out = []
    for page in text.split("\f"):
        ccy = next((m.group(1) for ln in page.splitlines()[:6] if (m := STMT.match(ln))), None)
        if ccy is None:
            if out:
                out[-1][1].append(page)
            continue
        if out and out[-1][0] == ccy:
            out[-1][1].append(page)
        else:
            out.append((ccy, [page]))
    return [(c, "\f".join(pages)) for c, pages in out]


def money_re(ccy):
    """Column-amount pattern for a wallet: 'S$1,234.56', '€3.70', '1,234.56 SEK'."""
    if ccy in SYMBOL:
        return re.compile(SYMBOL[ccy] + r"(?P<a>-?[0-9][0-9,]*\.[0-9]{2})")
    return re.compile(r"(?P<a>-?[0-9][0-9,]*\.[0-9]{2}) " + ccy + r"\b")


def iso(d):
    dd, mon, yr = d.replace(".", "").split()
    return f"{int(yr):04d}-{MONTH_NO[mon]:02d}-{int(dd):02d}"


def fee_amount(text, wallet):
    """The fee as a number when it is in the wallet's currency, else None."""
    if m := money_re(wallet).search(text):
        return money(m.group("a"))
    return None


def foreign_amount(text, wallet):
    """The first amount in a currency other than the wallet's, as (ccy, '123.45')."""
    for m in SYM_AMOUNT.finditer(text):
        ccy = ANY_SYMBOL[m.group("s")]
        if ccy != wallet:
            return ccy, plain(m.group("a"))
    for m in CODE_AMOUNT.finditer(text):
        if m.group("c") != wallet:
            return m.group("c"), plain(m.group("a"))
    return None


# ---------------------------------------------------------------- extraction

def parse(text, source, ccy):
    """Return (rows, statement) for one all-time export of the given wallet."""
    rx = money_re(ccy)
    stmt = dict(source=source, account=ACCOUNT, account_no=None, currency=ccy,
                header_ccy=None, generated=None, period_start=None, period_end=None,
                opening=None, closing=None, summary_in=None, summary_out=None)
    rows, edges, cur = [], None, None
    for ln in text.splitlines():
        if (m := STMT.match(ln)) and stmt["header_ccy"] is None:
            stmt["header_ccy"] = m.group(1)
            continue
        if (m := GENERATED.search(ln)) and stmt["generated"] is None:
            stmt["generated"] = iso(m.group("d"))
            continue
        if (m := ACCT.search(ln)) and stmt["account_no"] is None:
            stmt["account_no"] = m.group("n")
            continue
        if (m := PERIOD.search(ln)) and stmt["period_start"] is None:
            stmt.update(period_start=iso(m.group("a")), period_end=iso(m.group("b")))
            continue
        if SUMMARY.match(ln) and stmt["opening"] is None:
            amts = [money(m.group("a")) for m in rx.finditer(ln)]
            if len(amts) == 4:
                stmt.update(opening=amts[0], summary_out=amts[1], summary_in=amts[2],
                            closing=amts[3])
            continue
        if HEADER.match(ln):
            edges = [ln.find(k) + len(k) for k in ("Money out", "Money in", "Balance")]
            cur = None
            continue
        if edges is None:
            continue
        if not ln.strip():
            cur = None                      # a blank line ends the row's details
            continue
        if m := DATE.match(ln):
            out = [None, None, None]
            for a in rx.finditer(ln):
                if a.start() < min(edges) - 30:
                    continue
                i = min(range(3), key=lambda k: abs(a.end() - edges[k]))
                out[i] = money(a.group("a"))
            desc = rx.sub("", m.group("rest"))
            desc = re.sub(r"\s{2,}", " ", desc).strip()
            cur = dict(source=source, account=ACCOUNT, account_no=None, currency=ccy,
                       date=iso(m.group("d")), title=desc, detail=[],
                       withdrawal=out[0], deposit=out[1], balance_after=out[2],
                       synthetic=False)
            rows.append(cur)
            continue
        if cur is not None and (m := DETAIL.match(ln)) and not FOOTER.search(ln):
            cur["detail"].append(re.sub(r"\s{2,}", "  ", m.group("t")).strip())
    return rows, stmt


def enrich(r, owner):
    """Turn the title and detail lines into the shared column set."""
    ccy = r["currency"]
    title = r["title"]
    r["merchant"] = r["counterparty"] = r["reference"] = r["memo"] = None
    r["merchant_country"] = r["purchase_date"] = r["card_last4"] = r["fx_original"] = None
    r["fee"] = None
    to = frm = card = fee = rate = None
    loose = []
    for d in r["detail"]:
        if d.startswith("Reference:"):
            r["reference"] = d[len("Reference:"):].strip() or None
        elif d.startswith("To:"):
            to = d[3:].strip()
        elif d.startswith("From:"):
            frm = d[5:].strip()
        elif m := CARD.match(d):
            card = m.group("pan")[-4:]
        elif m := FEE.match(d):
            fee = m.group("fee")
            r["fee"] = fee_amount(fee, ccy)
            if m.group("net"):
                loose.append(m.group("net"))
        elif m := RATE.match(d):
            rate = m.group("rate")
            if m.group("fx"):
                loose.append(m.group("fx"))
        else:
            loose.append(d)
    fx = foreign_amount("  ".join(loose), ccy)
    if fx:
        r["fx_original"] = f"{fx[0]} {fx[1]}"
    notes = []
    if fee:
        notes.append(f"fee {fee}")

    if m := EXCHANGE.match(title):
        side = "In" if m.group("ccy") == ccy else "Out"
        r["bank_txn_type"] = f"Revolut Exchange {side}"
        r["memo"] = fx[0] if fx else title
        r["reference"] = f"{title}; {'; '.join(notes)}" if notes else title
    elif TOPUP.search(title) or OWNER_TOPUP.match(title) or (frm and title.lower().startswith("top")):
        r["bank_txn_type"] = "Revolut Top-up"
        r["counterparty"] = frm or (TOPUP.search(title) or OWNER_TOPUP.match(title)).group(1)
        r["memo"] = title
    elif m := TRANSFER.match(title):
        r["bank_txn_type"] = "Revolut Transfer"
        r["counterparty"] = (to if m.group("dir") == "to" else frm) or m.group("who")
        r["memo"] = "; ".join([title] + notes)
    elif card:
        r["bank_txn_type"] = "Revolut Payment"
        r["card_last4"] = card
        name = to or title
        # first what the printed text says, with no wallet fallback: a city
        # that was recognised is kept even when it is the wallet's country
        merchant, country = places.split_country(name, None, None)
        if country is None:
            # not recognised: the charged currency says where, else the wallet
            merchant, country = places.split_country(name, fx[0] if fx else None, ccy)
        if country is None and fx and fx[0] in places.CCY_COUNTRY:
            country = places.CCY_COUNTRY[fx[0]]
        r["merchant"], r["merchant_country"] = merchant, country
        memo = [f"Revolut name: {title}"] if title and title != merchant else []
        if rate:
            memo.append(rate)
        r["memo"] = "; ".join(memo + notes) or None
    elif not r["detail"] and owner and title.upper() == owner.upper() and r["withdrawal"]:
        r["bank_txn_type"] = "Revolut Withdrawal"
        r["counterparty"] = title
        r["memo"] = "sent to the owner's bank account"
    else:
        r["bank_txn_type"] = "Revolut Other"
        r["counterparty"] = to or frm or title
        r["memo"] = "; ".join([title] + notes)
    r["flow_class"] = "Internal - Revolut" if r["bank_txn_type"] in INTERNAL else "External"
    return r


def owner_name(rows):
    """The account holder, as the 'Payment from NAME' top-up prints it."""
    for r in rows:
        if m := OWNER_TOPUP.match(r["title"]):
            return m.group("who").strip()
    return None


def backfill_account_no(stmts):
    """A wallet with no local account details (NOK, SEK) prints no account
    number; every wallet belongs to the one Revolut account, so borrow it."""
    first = next((s["account_no"] for s in stmts if s["account_no"]), None)
    for s in stmts:
        if not s["account_no"]:
            s["account_no"] = first or "unknown"
            s["note_acct"] = "account number not printed; taken from another wallet"


def enrich_all(rows, stmts):
    by_src = {s["source"]: s for s in stmts}
    owner = owner_name(rows)
    for r in rows:
        s = by_src[r["source"]]
        r["account_no"] = s["account_no"]
        r["amount"] = round((r["deposit"] or 0) - (r["withdrawal"] or 0), 2)
        r["direction"] = direction(r["amount"])
        enrich(r, owner)
    no_amount = [r for r in rows if r["withdrawal"] is None and r["deposit"] is None]
    return [r for r in rows if r not in no_amount], no_amount


# ---------------------------------------------------------------- blocks

def sgd_points(rows):
    """(date, ccy, sgd per unit) from every row that shows both sides."""
    pts = []
    for r in rows:
        ccy = r["currency"]
        if ccy == "SGD" or not r["fx_original"] or not r["amount"]:
            continue
        oc, amt = r["fx_original"].split()
        if oc == "SGD":
            pts.append((r["date"], ccy, float(amt) / abs(r["amount"])))
    return sorted(pts)


def cross_points(rows, direct):
    """A wallet with no SGD leg borrows the rate through its exchange partner:
    1,100.00 SEK -> 100.00 EUR and SEK's own rate that day give EUR's."""
    pts = []
    for r in rows:
        ccy = r["currency"]
        if ccy == "SGD" or not r["bank_txn_type"].startswith("Revolut Exchange"):
            continue
        if not r["fx_original"] or not r["amount"]:
            continue
        oc, amt = r["fx_original"].split()
        if oc == "SGD" or oc not in direct:
            continue
        partner = [p for p in direct[oc] if p[0] <= r["date"]]
        if not partner:
            continue
        per_unit = float(amt) / abs(r["amount"])       # partner units per wallet unit
        pts.append((r["date"], ccy, per_unit * partner[-1][2]))
    return sorted(pts)


def apply_fx(rows, blocks):
    direct = defaultdict(list)
    for d, c, x in sgd_points(rows):
        direct[c].append((d, c, x))
    borrowed = defaultdict(list)
    for d, c, x in cross_points(rows, direct):
        borrowed[c].append((d, c, x))
    for b in blocks:
        if b["currency"] == "SGD":
            continue
        pts = [p for p in direct.get(b["currency"], []) if p[0] <= b["period_end"]]
        how = "row"
        if not pts:
            pts = [p for p in borrowed.get(b["currency"], []) if p[0] <= b["period_end"]]
            how = "exchange partner's"
        if not pts:
            # a wallet funded from outside before its first SGD leg (money
            # received one month, exchanged the next): the nearest later rate
            # beats none
            after_direct = [p for p in direct.get(b["currency"], []) if p[0] > b["period_end"]]
            after_borrowed = [p for p in borrowed.get(b["currency"], []) if p[0] > b["period_end"]]
            later = sorted(after_direct + after_borrowed)
            pts = later[:1]
            # say which kind of rate it was, so the note cannot claim a row rate
            # for one borrowed through the exchange partner
            kind = "row" if pts and pts[0] in after_direct else "exchange partner's"
            how = f"{kind} rate, the first one after this month"
        if not pts:
            continue
        d, _, x = pts[-1]
        b["fx_to_sgd"] = round(x, 6)
        b["closing_sgd"] = round(b["closing"] * x, 2) if b["closing"] is not None else None
        label = how if how.endswith("month") else f"{how} rate"
        b["note"] = "; ".join(n for n in (b["note"], f"fx_to_sgd from the {d} {label}") if n)


# ---------------------------------------------------------------- main

class MissingSummary(ValueError):
    """A wallet section printed no balance summary line, or one the parser
    could not read four amounts from.

    The summary is the only place Revolut prints the opening and the closing.
    Without it the section would open at zero and every month would reconcile
    against balances derived from its own rows, so the parser stops instead of
    writing rows nothing has checked."""


def require_summary(stmt):
    if stmt["opening"] is None or stmt["closing"] is None:
        raise MissingSummary(
            f"{stmt['source']}: no balance summary read, so the {stmt['currency']} "
            f"balance chain cannot be checked and nothing was written; check the "
            f"statement's .txt dump for its \"Account (E-Money)\" line")


def check_summary(stmt, rows):
    """The statement's own totals against the rows it printed. The summary
    counts a fee taken off money coming in as money in and money out both,
    while the column (and the balance) carry the net; add the fee back."""
    mine = [r for r in rows if r["source"] == stmt["source"]]
    fees_in = sum(r["fee"] or 0 for r in mine if r["amount"] > 0)
    tin = round(sum(max(r["amount"], 0) for r in mine) + fees_in, 2)
    tout = round(sum(max(-r["amount"], 0) for r in mine) + fees_in, 2)
    problems = []
    if stmt["summary_in"] is not None and abs(tin - stmt["summary_in"]) > 0.005:
        problems.append(f"money in {tin:,.2f} against the summary's {stmt['summary_in']:,.2f}")
    if stmt["summary_out"] is not None and abs(tout - stmt["summary_out"]) > 0.005:
        problems.append(f"money out {tout:,.2f} against the summary's {stmt['summary_out']:,.2f}")
    return problems


def run(sources):
    """Parse (source, text, currency) triples, one per statement section;
    return (rows, blocks, stmts, bad, no_amount). A section with no balance
    summary raises MissingSummary: nothing else can check its rows."""
    rows, stmts = [], []
    for src, text, ccy in sources:
        r, s = parse(text, src, ccy)
        require_summary(s)
        rows += r
        stmts.append(s)
    backfill_account_no(stmts)
    rows, no_amount = enrich_all(rows, stmts)
    blocks = []
    for s in stmts:
        blocks += split_months(rows, s, ACCOUNT)
    report_duplicate_periods(blocks, period_name)
    # validate keys rows to blocks by source; a source has many blocks here, so
    # replay each month on its own rows
    bad = []
    for b in blocks:
        rs = [r for r in rows if r["source"] == b["source"] and r.get("block") == b["month"]]
        run_, tin, tout = b["opening"], 0.0, 0.0
        drifts = len(bad)
        for r in rs:
            run_ += r["amount"]
            tin += max(r["amount"], 0)
            tout += max(-r["amount"], 0)
            if r["balance_after"] is not None and abs(run_ - r["balance_after"]) > 0.005:
                bad.append((b["source"], b["currency"], r["date"], r["bank_txn_type"],
                            round(run_, 2), r["balance_after"]))
                run_ = r["balance_after"]
        b.update(computed_closing=round(run_, 2), computed_in=round(tin, 2),
                 computed_out=round(tout, 2), total_in=round(tin, 2), total_out=round(tout, 2),
                 n_txns=len(rs), n_synth=0, balance_state="checked",
                 # a drift resynced onto the printed balance still fails the
                 # block, so its CSV's `reconciled` stops a reuse (sync.check_blocks)
                 ok=abs(run_ - b["closing"]) < 0.005 and len(bad) == drifts)
    for s in stmts:
        for p in check_summary(s, rows):
            warn(s["source"], p)
            for b in blocks:
                if b["source"] == s["source"]:
                    b["ok"] = False
    add_keys(rows)
    apply_fx(rows, blocks)
    return rows, blocks, stmts, bad, no_amount


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="*", default=None,
                    help="PDFs or directories to parse (default: statements/revolut in the data folder)")
    ap.add_argument("--out", metavar="DIR",
                    help="also write transactions.csv and period_balances.csv here")
    ap.add_argument("--wallet", metavar="CCY", help="only this currency's section")
    ap.add_argument("--no-text", action="store_true",
                    help="do not save the .txt dump beside each PDF")
    args = ap.parse_args()

    require_pdftotext()
    pdfs = collect_pdfs(args.paths or statement_folders("revolut"))
    if not pdfs:
        sys.exit("no PDFs found")
    sources = []
    for pdf in pdfs:
        parts = sections(pdf_to_text(pdf, write=not args.no_text))
        if not parts:
            warn(pdf, "no 'XXX Statement' heading found; the file is not parsed")
            continue
        for ccy, text in parts:
            if args.wallet and ccy != args.wallet.upper():
                continue
            sources.append((f"{pdf}#{ccy}" if len(parts) > 1 else str(pdf), text, ccy))
    if not sources:
        sys.exit("no statement sections found")
    try:
        rows, blocks, stmts, bad, no_amount = run(sources)
    except MissingSummary as e:
        sys.exit(f"error: {e}")              # before a single CSV is written

    ordered = sorted(blocks, key=lambda b: (b["currency"] != "SGD", b["currency"], b["period_end"]))
    print(f"{len(pdfs)} PDFs -> {len(rows)} rows, {len(blocks)} wallet-months\n")
    fmt_num = lambda x: f"{x:>10,.2f}" if x is not None else f"{'-':>10}"
    print(f"  {'period':34s} {'opening':>10} {'closing':>10}  rows reconciled")
    for b in ordered:
        print(f"  {period_name(b):34s} {fmt_num(b['opening'])} {fmt_num(b['closing'])} "
              f"{b['n_txns']:5d} {'yes' if b['ok'] else 'NO':>10}"
              + (f"  {b['note']}" if b.get("note") else ""))
    for x in bad:
        warn(x[0], f"balance mismatch: {x[1]} {x[2]} {x[3]} computed {x[4]:,.2f} "
                   f"against printed {x[5]:,.2f}")
    for r in no_amount:
        warn(r["source"], f"row carried no amount and was dropped: {r['date']} {r['title']}")

    print("\nBank txn type x direction:")
    tally = Counter((r["currency"], r["bank_txn_type"], r["direction"]) for r in rows)
    for (c, t, d), n in sorted(tally.items()):
        gross = sum(abs(r["amount"]) for r in rows
                    if (r["currency"], r["bank_txn_type"], r["direction"]) == (c, t, d))
        print(f"  {c} {t:22s} {d:3s} {n:4d} rows  {gross:>12,.2f}")

    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        with (out / "transactions.csv").open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=TXN_COLS, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
        with (out / "period_balances.csv").open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=BAL_COLS, extrasaction="ignore")
            w.writeheader()
            for b in ordered:
                w.writerow({**b, "period": period_name(b), "reconciled": b["ok"],
                            "fx_to_sgd": b.get("fx_to_sgd"),
                            "closing_sgd": b.get("closing_sgd", b["closing"] if b["currency"] == "SGD" else None)})
        print(f"\nwrote {out/'transactions.csv'} and {out/'period_balances.csv'}")

    print()
    for s in stmts:
        bs = [b for b in blocks if b["source"] == s["source"]]
        print(f"{s['currency']}: {s['period_start']}..{s['generated'] or s['period_end']}, "
              f"{sum(b['n_txns'] for b in bs)} rows in {len(bs)} months, "
              f"{sum(b['ok'] for b in bs)}/{len(bs)} reconciled, closing {s['closing']:,.2f}")

    failed = [b for b in blocks if not b["ok"]]
    tail = balance_summary(blocks)
    if failed or bad:
        sys.exit(f"\n{len(failed)} wallet-month(s) unreconciled; {tail}")
    print(f"\nEvery balance chain reconciles; {tail}")


# ---------------------------------------------------------------- the layout (G21)

def _sees(text):
    lines = text.splitlines()
    if not sections(text) or not any(HEADER.match(ln) for ln in lines):
        return None
    if not any(SUMMARY.match(ln) for ln in lines):
        return None
    return Seen(numbers=tuple(m.group("n") for m in ACCT.finditer(text)))


register_bank(Bank("revolut", r"revolut", order=70, name="Revolut"))
register(Layout(
    name="revolut", parser="revolut", module=__spec__.name, order=70, kind="wallet",
    label="a Revolut export", title="Revolut wallet",
    folder="revolut", setup="Revolut (all-accounts export)", csv="revolut", multi_currency=True,
    test=_sees, bank="revolut", folder_title="Revolut", name_file=by_download_day, export="a Revolut export",
    new_types=("Revolut Other",), status=CHECKED,
    detected_by="an `XXX Statement` heading on the page, which also names the wallet",
    balance_check="every printed row balance, then each wallet's rows against its own balance summary"))


if __name__ == "__main__":
    main()
