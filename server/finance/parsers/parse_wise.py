#!/usr/bin/env python3
"""Extract Wise (TransferWise) "all accounts" statement PDFs into transaction rows.

Usage:
    python3 -m finance.parsers.parse_wise [PATH ...] [--out DIR] [--wallet CCY] [--no-text]

PATH may be a PDF or a directory (searched recursively); it defaults to
data/wise/. Each PDF's text is written beside it as <name>.txt, then parsed.
With --out, transactions.csv and period_balances.csv are written there with the
same columns as parse_statements.py, so every feed loads the same way.

Wise's "Statements" export writes one PDF per currency balance rather than one
file with a section per wallet: statement_12345678_SGD_2024-01-01_2025-12-31.pdf
and its SEK and USD siblings are one export set, all covering the same range and
all ending on the day they were generated. They sit flat in data/wise/ the way
the Revolut export sits flat in data/revolut/, and a re-export replaces the set;
two sets whose blocks cover the same wallet-month stop the run with a
duplicate-period error rather than loading a month twice.

Each file opens with a "XXX statement" heading that names the wallet, the range
line, "Generated on: <date>", and an account block whose fields differ per
currency (SGD prints an account number and bank code, USD an account number and
a routing number, SEK an IBAN). One Wise account holds every balance, so the
SGD file's account number is the account_no for all of them; a set with no SGD
file borrows the first number printed and says so in the block note.

Then "XXX on <date> [GMT+08:00]    0.00 XXX" gives the closing balance, and the
table follows, NEWEST FIRST, two lines per row:

    Converted 100.00 SEK to 12.50 SGD (fee: 0.55 SEK)       -99.45        0.00
    3 March 2025 | Transaction: BALANCE-1000000001

The columns are Description, Incoming, Outgoing, Amount, assigned by nearest
column right-edge read off the header; Outgoing already carries its minus sign
and Amount is the running balance after the row. The second line carries the
date and Wise's transaction id, which goes in the reference column because it
is the one thing about a row that never changes. Rows are reversed into oldest
first, keeping the statement's within-day order reversed too, so the printed
running balances replay in the order Wise applied them.

Row kinds and how they are typed:

    "Topped up account"                  Wise Top-up         (Internal - Wise)
    "Converted 100.00 SEK to 12.50 SGD"  Wise Conversion In / Out (Internal -
    "... (fee: 0.55 SEK)"                Wise); the side is decided by which
                                         currency is the wallet's, the other
                                         side goes to fx_original, and the fee
                                         is charged as its own Wise Charges row
    "Wise Charges for: BALANCE-..."      Wise Fee            (External,
                                         counterparty Wise)
    "Received money from NAME with reference <text>"
    "Sent money to NAME"                 Wise Transfer       (External; the name
                                         or account number is the counterparty)
    "Card transaction of 12.00 SGD issued by MERCHANT City"
                                         Wise Card Payment   (External; the city
                                         names the country). NOT SEEN in this
                                         export, which has no card rows; the
                                         branch is here so one does not fall
                                         through to Wise Other.
    anything else                        Wise Other          (External), with a
                                         warning naming the description

Wise prints no opening balance, so it is derived twice and the two are checked
against each other: closing minus the sum of the rows, and the oldest row's
printed running balance minus its own amount. A balance with no rows opens and
closes at the printed closing. The printed closing is required: a file without
one stops the run before anything is written, since its rows could otherwise
only be checked against balances derived from themselves. A statement is then split into one period block
per calendar month from the first month with rows to the month the export was
generated in, opening at the running balance and closing at the last printed
balance of the month; an idle month carries a block with no rows and the
balance unchanged, so every month is covered and the account's coverage ends
on the export date.

Foreign-wallet blocks carry fx_to_sgd, taken from the latest conversion up to
that month that has SGD on one side: the description prints both amounts, so
the SGD and the foreign file agree on the rate. Money can sit in a balance for
months before it is converted, so a block earlier than every conversion takes
the first rate after it and the note says so. A wallet that has never touched
SGD borrows the rate through the currency it was converted from.
"""
import argparse
import calendar
import csv
import re
import shutil
import sys
from collections import Counter, defaultdict
from pathlib import Path

from .parse_statements import (statement_folders, BAL_COLS, MONTHS, TXN_COLS, add_keys,
                              balance_summary, collect_pdfs, direction, money,
                              pdf_to_text, report_duplicate_periods, warn)
from . import places  # (the city table and country split every feed shares)
from .wallet_months import every_month, month_end, split_months
from .layouts import CHECKED, Bank, Layout, Seen, by_download_day, register, register_bank

ACCOUNT = "Wise wallet"
AMT = r"-?[0-9][0-9,]*\.[0-9]{2}"
COL_AMOUNT = re.compile(r"(?P<a>" + AMT + r")")

STMT = re.compile(r"^\s*(?P<c>[A-Z]{3}) statement\s*$")
RANGE = re.compile(r"^\s*(?P<a>\d{1,2} [A-Z][a-z]+ \d{4}) \[[^\]]*\] - "
                   r"(?P<b>\d{1,2} [A-Z][a-z]+ \d{4}) \[[^\]]*\]\s*$")
GENERATED = re.compile(r"Generated on:\s*(?P<d>\d{1,2} [A-Z][a-z]+ \d{4})")
HOLDER = re.compile(r"^\s*Account Holder\s{2,}\S")
CLOSING = re.compile(r"^\s*(?P<c>[A-Z]{3}) on (?P<d>\d{1,2} [A-Z][a-z]+ \d{4}) \[[^\]]*\]"
                     r"\s{2,}(?P<a>" + AMT + r") (?P=c)\s*$")
HEADER = re.compile(r"^\s*Description\s+Incoming\s+Outgoing\s+Amount\s*$")
DATED = re.compile(r"^\s*(?P<d>\d{1,2} [A-Z][a-z]+ \d{4})\s*\|\s*Transaction:\s*(?P<id>\S+)\s*$")
FOOTER = re.compile(r"Wise Asia-Pacific Pte\. Ltd\. is a company|Monetary Authority of Singapore|"
                    r"Need help\? Visit|^ref:[0-9a-f-]+|payment institution")

# the type table: the first pattern that matches the description names the row
TOPUP = re.compile(r"^Topped up account\b")
CONVERT = re.compile(r"^Converted (?P<a>[0-9][0-9,]*\.[0-9]{2}) (?P<ac>[A-Z]{3}) to "
                     r"(?P<b>[0-9][0-9,]*\.[0-9]{2}) (?P<bc>[A-Z]{3})"
                     r"(?: \(fee: (?P<fee>[0-9][0-9,]*\.[0-9]{2}) (?P<fc>[A-Z]{3})\))?\s*$")
CHARGES = re.compile(r"^Wise Charges for:\s*(?P<of>\S+)\s*$")
RECEIVED = re.compile(r"^Received money from (?P<who>.+?)(?: with reference(?P<ref>.*))?$")
SENT = re.compile(r"^Sent money to (?P<who>.+)$")
CARD = re.compile(r"^Card transaction of (?P<a>[0-9][0-9,]*\.[0-9]{2}) (?P<ac>[A-Z]{3}) "
                  r"issued by (?P<who>.+)$")
TYPES = ((TOPUP, "Wise Top-up"), (CONVERT, "Wise Conversion"), (CHARGES, "Wise Fee"),
         (RECEIVED, "Wise Transfer"), (SENT, "Wise Transfer"), (CARD, "Wise Card Payment"))
INTERNAL = ("Wise Top-up", "Wise Conversion In", "Wise Conversion Out")

# Wise prints the month in full on every date line
MONTH_NO = {**MONTHS, **{calendar.month_name[i]: i for i in range(1, 13)}}


def iso(d):
    dd, mon, yr = d.split()
    return f"{int(yr):04d}-{MONTH_NO[mon]:02d}-{int(dd):02d}"


def plain(num):
    return num.replace(",", "")


def statement_currency(text):
    """The wallet named by the "XXX statement" heading, or None."""
    for ln in text.splitlines()[:40]:
        if m := STMT.match(ln):
            return m.group("c")
    return None


def account_fields(header, values):
    """The account block reads as columns: the labels line up over their values,
    so slice the value line at the label starts."""
    starts = [m.start() for m in re.finditer(r"(?<=\s{2})\S", header)]
    out, bounds = {}, starts + [len(header) + 200]
    for i, s in enumerate(starts):
        label = header[s:bounds[i + 1]].strip()
        if label:
            out[label] = values[s:bounds[i + 1]].strip()
    return out


# ---------------------------------------------------------------- extraction

def parse(text, source, ccy):
    """Return (rows, statement) for one currency balance's statement."""
    stmt = dict(source=source, account=ACCOUNT, account_no=None, local_account=None,
                currency=ccy, header_ccy=None, generated=None, period_start=None,
                period_end=None, opening=None, closing=None, owner=None)
    rows, edges, pending, lines = [], None, None, text.splitlines()
    for i, ln in enumerate(lines):
        if (m := STMT.match(ln)) and stmt["header_ccy"] is None:
            stmt["header_ccy"] = m.group("c")
            continue
        if (m := RANGE.match(ln)) and stmt["period_start"] is None:
            stmt.update(period_start=iso(m.group("a")), period_end=iso(m.group("b")))
            continue
        if (m := GENERATED.search(ln)) and stmt["generated"] is None:
            stmt["generated"] = iso(m.group("d"))
            continue
        if HOLDER.match(ln) and stmt["local_account"] is None and i + 1 < len(lines):
            f = account_fields(ln, lines[i + 1])
            stmt["owner"] = f.get("Account Holder") or None
            stmt["local_account"] = f.get("Account number") or f.get("IBAN") or None
            if f.get("Account number"):
                stmt["account_no"] = f["Account number"]
            continue
        if (m := CLOSING.match(ln)) and stmt["closing"] is None and m.group("c") == ccy:
            stmt["closing"] = money(m.group("a"))
            continue
        if HEADER.match(ln):
            edges = [ln.find(k) + len(k) for k in ("Incoming", "Outgoing", "Amount")]
            pending = None
            continue
        if edges is None:
            continue
        if FOOTER.search(ln):
            break                            # the table ends at the licence block
        if not ln.strip():
            continue
        if m := DATED.match(ln):
            if pending is None:              # a date line with no description above it
                warn(source, f"date line with no description above it: {ln.strip()}")
                continue
            pending.update(date=iso(m.group("d")), reference=m.group("id"))
            rows.append(pending)
            pending = None
            continue
        (inc, out, bal), spans = split_amounts(ln, edges)
        title = re.sub(r"\s{2,}", " ", strip_spans(ln, spans)).strip()
        if pending is None:
            pending = dict(source=source, account=ACCOUNT, account_no=None, currency=ccy,
                           date=None, title=title, incoming=inc, outgoing=out,
                           balance_after=bal, reference=None)
        else:                                # a description that wrapped onto a second line
            pending["title"] = f"{pending['title']} {title}".strip()
            for k, v in (("incoming", inc), ("outgoing", out), ("balance_after", bal)):
                if v is not None:
                    pending[k] = v
    if pending is not None:
        warn(source, f"description with no date line under it: {pending['title']}")
    rows.reverse()                           # the statement lists newest first
    rows.sort(key=lambda r: r["date"])       # stable, so a day keeps its reversed order
    return rows, stmt


def split_amounts(ln, edges):
    """((incoming, outgoing, balance), spans) by nearest column right-edge. The
    description carries amounts of its own ("Converted 100.00 SEK to ..."), so a
    number is only a column value when it ends under one; the spans say which
    ones were taken, and the rest of the line is the description."""
    out, spans = [None, None, None], []
    for m in COL_AMOUNT.finditer(ln):
        if m.start() < min(edges) - 30:
            continue
        i = min(range(3), key=lambda k: abs(m.end() - edges[k]))
        if abs(m.end() - edges[i]) > 8:
            continue
        out[i] = money(m.group("a"))
        spans.append(m.span())
    return out, spans


def strip_spans(ln, spans):
    """The line with the column amounts blanked out."""
    out = list(ln)
    for a, b in spans:
        out[a:b] = " " * (b - a)
    return "".join(out)


def classify(title):
    """(type name, match) from the type table, or (None, None)."""
    for rx, name in TYPES:
        if m := rx.match(title):
            return name, m
    return None, None


def enrich(r):
    """Turn the description into the shared column set."""
    ccy, title = r["currency"], r["title"]
    r["merchant"] = r["counterparty"] = r["memo"] = None
    r["merchant_country"] = r["purchase_date"] = r["card_last4"] = r["fx_original"] = None
    r["fee"] = r["conv"] = None
    kind, m = classify(title)

    if kind == "Wise Conversion":
        a, ac = float(plain(m.group("a"))), m.group("ac")
        b, bc = float(plain(m.group("b"))), m.group("bc")
        r["conv"] = (ac, a, bc, b)
        side = "In" if bc == ccy else "Out" if ac == ccy else None
        if side is None:
            warn(r["source"], f"conversion touches neither side of the {ccy} balance: {title}")
            r["conv"] = kind = None       # not a rate this balance can vouch for
        else:
            r["bank_txn_type"] = f"Wise Conversion {side}"
            other = (ac, a) if side == "In" else (bc, b)
            r["fx_original"] = f"{other[0]} {other[1]:.2f}"
            notes = [title]
            if m.group("fee"):
                r["fee"] = float(plain(m.group("fee")))
                notes.append(f"fee {m.group('fee')} {m.group('fc')} charged as its own row")
            r["memo"] = "; ".join(notes)
    elif kind == "Wise Top-up":
        r["bank_txn_type"] = kind
        r["memo"] = title
    elif kind == "Wise Fee":
        r["bank_txn_type"] = kind
        r["counterparty"] = "Wise"
        r["memo"] = f"charges for {m.group('of')}"
    elif kind == "Wise Transfer":
        r["bank_txn_type"] = kind
        r["counterparty"] = m.group("who").strip()
        r["memo"] = title
    elif kind == "Wise Card Payment":
        r["bank_txn_type"] = kind
        fx = m.group("ac") if m.group("ac") != ccy else None
        if fx:
            r["fx_original"] = f"{fx} {plain(m.group('a'))}"
        merchant, country = places.split_country(m.group("who"), fx, ccy)
        r["merchant"], r["merchant_country"] = merchant, country
        r["memo"] = title
    if kind is None:
        r["bank_txn_type"] = "Wise Other"
        r["counterparty"] = title
        r["memo"] = title
        warn(r["source"], f"description matched no known Wise row kind, typed Wise Other: "
                          f"{r['date']} {title}")
    r["flow_class"] = "Internal - Wise" if r["bank_txn_type"] in INTERNAL else "External"
    return r


def backfill_account_no(stmts):
    """One Wise account holds every balance, but each file prints only its own
    local details: SGD an account number, USD another, SEK an IBAN. The SGD
    number is the account, so every balance takes it."""
    sgd = next((s["account_no"] for s in stmts
                if s["currency"] == "SGD" and s["account_no"]), None)
    first = sgd or next((s["account_no"] for s in stmts if s["account_no"]), None)
    if first is None:
        warn("", "no account number printed by any file; account_no is 'unknown'")
    for s in stmts:
        if s["account_no"] != first:
            s["note_acct"] = ("account number taken from the SGD balance" if sgd else
                              "no SGD balance in this export; account number taken "
                              "from another balance")
        s["account_no"] = first or "unknown"


def enrich_all(rows, stmts):
    by_src = {s["source"]: s for s in stmts}
    for r in rows:
        r["account_no"] = by_src[r["source"]]["account_no"]
        r["amount"] = round((r["incoming"] or 0) + (r["outgoing"] or 0), 2)
        r["direction"] = direction(r["amount"])
        enrich(r)
    no_amount = [r for r in rows if r["incoming"] is None and r["outgoing"] is None]
    return [r for r in rows if r not in no_amount], no_amount


class MissingClosing(ValueError):
    """A balance's statement printed no closing balance.

    Wise prints no opening either, so the closing is the one figure the rows
    are checked against. Without it every month would reconcile against
    balances derived from its own rows, so the parser stops instead of writing
    rows nothing has checked."""


def derive_opening(stmt, rows):
    """Wise prints no opening balance. Take it twice: from the closing less every
    row, and from the oldest row's own printed running balance. A statement with
    no printed closing raises MissingClosing: nothing else can check its rows."""
    mine = [r for r in rows if r["source"] == stmt["source"]]
    if stmt["closing"] is None:
        raise MissingClosing(
            f"{stmt['source']}: no closing balance printed, so the {stmt['currency']} "
            f"balance chain cannot be checked and nothing was written; check the "
            f"statement's .txt dump for its \"{stmt['currency']} on <date>\" line")
    back = round(stmt["closing"] - sum(r["amount"] for r in mine), 2) + 0.0
    first = next((r for r in mine if r["balance_after"] is not None), None)
    if first is not None:
        front = round(first["balance_after"] - first["amount"], 2)
        if abs(front - back) > 0.005:
            warn(stmt["source"],
                 f"opening balance {back:,.2f} from the closing against {front:,.2f} from "
                 f"the oldest row's printed balance; the rows do not add up to the closing")
    return back


# ---------------------------------------------------------------- blocks

def period_name(b):
    return f"{b['account_no']} · {b['currency']} · {b['period_end'][:7]}"


def sgd_points(rows):
    """(date, ccy, sgd per unit) from every conversion with SGD on one side. The
    description prints both amounts, so the two files agree on the rate."""
    pts = []
    for r in rows:
        if not r.get("conv"):
            continue
        ac, a, bc, b = r["conv"]
        if ac == "SGD" and bc != "SGD":
            pts.append((r["date"], bc, a / b))
        elif bc == "SGD" and ac != "SGD":
            pts.append((r["date"], ac, b / a))
    return sorted(set(pts))


def cross_points(rows, direct):
    """A balance with no SGD leg borrows the rate through its conversion partner:
    100.00 SEK -> 95.00 NOK and SEK's own rate that day give NOK's."""
    pts = []
    for r in rows:
        if not r.get("conv"):
            continue
        ac, a, bc, b = r["conv"]
        if "SGD" in (ac, bc):
            continue
        for this, this_amt, other, other_amt in ((ac, a, bc, b), (bc, b, ac, a)):
            partner = [p for p in direct.get(other, []) if p[0] <= r["date"]]
            if partner:
                pts.append((r["date"], this, other_amt / this_amt * partner[-1][2]))
    return sorted(set(pts))


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
        d = x = how = None
        for source, label in ((direct, "conversion"), (borrowed, "conversion partner's")):
            pts = source.get(b["currency"], [])
            upto = [p for p in pts if p[0] <= b["period_end"]]
            if upto:
                d, _, x = upto[-1]
                how = f"{label} rate"
                break
            if pts:
                # money can sit in a balance for months before it is converted;
                # the first rate after the block is better than no SGD value
                d, _, x = pts[0]
                how = f"{label} rate, the first one after this month"
                break
        if x is None:
            continue
        b["fx_to_sgd"] = round(x, 6)
        b["closing_sgd"] = round(b["closing"] * x, 2) if b["closing"] is not None else None
        b["note"] = "; ".join(n for n in (b["note"], f"fx_to_sgd from the {d} {how}") if n)


# ---------------------------------------------------------------- main

def run(sources):
    """Parse (source, text, currency) triples, one per currency balance;
    return (rows, blocks, stmts, bad, no_amount)."""
    rows, stmts = [], []
    for src, text, ccy in sources:
        r, s = parse(text, src, ccy)
        rows += r
        stmts.append(s)
    backfill_account_no(stmts)
    rows, no_amount = enrich_all(rows, stmts)
    for s in stmts:
        s["opening"] = derive_opening(s, rows)
    blocks = []
    for s in stmts:
        blocks += split_months(rows, s, ACCOUNT)
    report_duplicate_periods(blocks, period_name)
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
    add_keys(rows)
    apply_fx(rows, blocks)
    return rows, blocks, stmts, bad, no_amount


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="*", default=None,
                    help="PDFs or directories to parse (default: the wise-* folders in the data folder)")
    ap.add_argument("--out", metavar="DIR",
                    help="also write transactions.csv and period_balances.csv here")
    ap.add_argument("--wallet", metavar="CCY", help="only this currency balance")
    ap.add_argument("--no-text", action="store_true",
                    help="do not save the .txt dump beside each PDF")
    args = ap.parse_args()

    if not shutil.which("pdftotext"):
        sys.exit("pdftotext not found (brew install poppler)")
    pdfs = collect_pdfs(args.paths or statement_folders("wise-"))
    if not pdfs:
        sys.exit("no PDFs found")
    sources = []
    for pdf in pdfs:
        text = pdf_to_text(pdf, write=not args.no_text)
        ccy = statement_currency(text)
        if ccy is None:
            warn(pdf, "no 'XXX statement' heading found; the file is not parsed")
            continue
        if args.wallet and ccy != args.wallet.upper():
            continue
        sources.append((str(pdf), text, ccy))
    if not sources:
        sys.exit("no statement sections found")
    try:
        rows, blocks, stmts, bad, no_amount = run(sources)
    except MissingClosing as e:
        sys.exit(f"error: {e}")              # before a single CSV is written

    ordered = sorted(blocks, key=lambda b: (b["currency"] != "SGD", b["currency"], b["period_end"]))
    print(f"{len(pdfs)} PDFs -> {len(rows)} rows, {len(blocks)} wallet-months\n")
    fmt_num = lambda x: f"{x:>10,.2f}" if x is not None else f"{'-':>10}"
    print(f"  {'period':30s} {'opening':>10} {'closing':>10}  rows reconciled")
    for b in ordered:
        print(f"  {period_name(b):30s} {fmt_num(b['opening'])} {fmt_num(b['closing'])} "
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
                            "closing_sgd": b.get("closing_sgd",
                                                 b["closing"] if b["currency"] == "SGD" else None)})
        print(f"\nwrote {out/'transactions.csv'} and {out/'period_balances.csv'}")

    print()
    for s in stmts:
        bs = [b for b in blocks if b["source"] == s["source"]]
        print(f"{s['currency']}: {s['period_start']}..{s['generated'] or s['period_end']}, "
              f"{sum(b['n_txns'] for b in bs)} rows in {len(bs)} months, "
              f"{sum(b['ok'] for b in bs)}/{len(bs)} reconciled, "
              f"opening {s['opening']:,.2f}, closing {s['closing']:,.2f}")

    failed = [b for b in blocks if not b["ok"]]
    tail = balance_summary(blocks)
    if failed or bad:
        sys.exit(f"\n{len(failed)} wallet-month(s) unreconciled; {tail}")
    print(f"\nEvery balance chain reconciles; {tail}")


# ---------------------------------------------------------------- the layout (G21)

def _sees(text):
    ccy = statement_currency(text)
    if not ccy or not GENERATED.search(text) or "Wise" not in "\n".join(text.splitlines()[:12]):
        return None
    lines = text.splitlines()
    number = ""
    for i, ln in enumerate(lines[:60]):
        if HOLDER.match(ln) and i + 1 < len(lines):
            f = account_fields(ln, lines[i + 1])
            # each balance prints its own local details: an account number or an IBAN
            number = f.get("Account number") or f.get("IBAN") or ""
            break
    return Seen(currency=ccy, numbers=(number,))


register_bank(Bank("wise", r"\bwise\b", order=80, name="Wise"))
register(Layout(
    name="wise", parser="wise", module=__spec__.name, order=80, kind="wallet",
    label="a Wise {ccy} statement", title="Wise balance",
    folder="wise-{ccy}", setup="Wise {ccy} balance", csv="wise",
    currencies=("SGD", "USD"),
    test=_sees, bank="wise", folder_title="Wise", name_file=by_download_day, export="a Wise statement",
    # of a Wise account only the SGD balance prints the account's number; the
    # others print their own local details
    number_in_config=("SGD",),
    new_types=("Wise Card Payment", "Wise Other"), status=CHECKED,
    detected_by="an `XXX statement` heading at the top of the file, which also names the balance",
    balance_check="every printed running balance, plus the opening derived two ways and the two compared"))


if __name__ == "__main__":
    main()
