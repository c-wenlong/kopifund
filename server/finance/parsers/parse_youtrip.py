#!/usr/bin/env python3
"""Extract YouTrip monthly wallet statement PDFs into transaction rows.

Usage:
    python3 -m finance.parsers.parse_youtrip [PATH ...] [--out DIR] [--wallet CCY] [--no-text]

PATH may be a PDF or a directory (searched recursively); it defaults to
data/youtrip-statements/. The wallet is taken from the folder the PDF sits in
(data/youtrip-statements/<wallet>/<year>/<month>.pdf: sgd-main, sek, usd, eur, myr, aud)
and must agree with the "My XXX Statement" line inside the PDF. Each PDF's text
is written beside it as <name>.txt, then parsed. With --out, transactions.csv
and period_balances.csv are written there with the same columns as
parse_statements.py, so both feeds load the same way. --wallet restricts the
run to one wallet folder.

One statement covers one calendar month of one wallet. The table has five
columns: Completed Date (time on the next line), Description, Money Out, Money
In, Balance. A description can be spread over three lines: the line above the
date, the date line itself, and the tail of the time line. The header row fixes
the right edge of each money column and every amount on a date line goes to
the nearest edge, as in parse_statements.py. Amounts carry the wallet's symbol
($, kr, €, RM); the symbol and thousands separators are stripped.

Row kinds seen in the statements and how they are typed:

    "Top up with PayNow"                      YouTrip Top-up   (money in)
    "SmartExchange" / "Exchange" + "$X SGD to kr Y SEK"
                                              YouTrip Exchange Out / In; the
                                              other wallet's side goes to
                                              fx_original, its code to memo, the
                                              printed rate to reference
    "MERCHANT,ADDRESS,CITY (kr150.00 NOK)"    YouTrip Payment  (card spend; the
                                              parenthesis is the foreign amount)
    "MRT/BUS Total Fare"                      YouTrip Payment
    "To Mobile no. ending NNNN" / "From ..."  YouTrip Other    (P2P transfer)
    "P2P-NNNNNNN-payer"                       YouTrip Other
    (nothing)                                 YouTrip Unprinted (see below)

No refund, fee or cashback row appears in the statements to date; the types are
still mapped so a future one lands in the right bucket.

The YouTrip PDF export drops rows at page breaks. Every statement's balance
chain is replayed against the printed opening, intermediate and closing
balances, and wherever the printed balance jumps past what the rows explain,
one "YouTrip Unprinted" row is synthesised for the jump, dated on the next
printed row. A month whose opening differs from the previous statement's
closing gets the same treatment with a row dated the first of the month; the
period's opening is then the previous closing so that opening + in - out still
equals the printed closing. The synthetic row is flagged Internal - YouTrip
when its size is a known top-up size or matches an exchange printed in another
wallet over the same days, else External. The script exits non-zero only when
a chain still fails to close after synthesis. Statements that print "No
transactions" carry no balances and are kept as empty periods with a note.

Foreign-wallet periods carry fx_to_sgd, the SGD value of one wallet unit taken
from the latest SGD exchange printed in that wallet up to that month, and
closing_sgd = closing x fx_to_sgd.
"""
import argparse
import csv
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

from finance.pdftext import pdf_to_text, require as require_pdftotext
from .parse_statements import (statement_folders, BAL_COLS, BALANCE_STATES, MONTHS,
                              TXN_COLS, add_keys, balance_summary,
                              collect_pdfs, direction, duplicate_periods, money,
                              report_duplicate_periods, validate, warn)
# the city and country tables live in places.py, which the Revolut and Wise
# parsers import too; re-exported here so places.split_country is still reachable
# as parse_youtrip.split_country
from .places import (ALPHA3, CCY_COUNTRY, CITY_COUNTRY, CITY_TABLE,  # noqa: F401
                    MOJIBAKE, WALLET_COUNTRY, city_country, norm_city,
                    split_country)
from .layouts import CHECKED, Bank, Layout, Seen, by_months, register, register_bank

WALLETS = {"sgd-main": "SGD", "sgd": "SGD", "sek": "SEK", "usd": "USD", "eur": "EUR", "myr": "MYR",
           "aud": "AUD", "nok": "NOK", "dkk": "DKK",
           # the data folder's names: one account folder per wallet
           "youtrip-sgd": "SGD", "youtrip-sek": "SEK", "youtrip-usd": "USD",
           "youtrip-eur": "EUR", "youtrip-myr": "MYR", "youtrip-aud": "AUD",
           "youtrip-nok": "NOK", "youtrip-dkk": "DKK"}
SYMBOL = {"SGD": r"\$", "USD": r"\$", "AUD": r"\$", "SEK": "kr", "NOK": "kr", "DKK": "kr",
          "EUR": "€", "MYR": "RM"}
ACCOUNT = "YouTrip wallet"
NUM = r"-?[0-9][0-9,]*(?:\.[0-9]+)?"
ANY_MONEY = re.compile(r"-?(?:kr|€|RM|\$)-?[0-9][0-9,]*\.[0-9]{2}")

STMT = re.compile(r"My ([A-Z]{3}) Statement")
ACCT = re.compile(r"^\s*(Y-\d{10})\s*$")
PERIOD = re.compile(r"^\s*(?P<a>\d{1,2} [A-Z][a-z]{2} \d{4}) to (?P<b>\d{1,2} [A-Z][a-z]{2} \d{4})\s*$")
HEADER = re.compile(r"Completed Date.*Money Out.*Money In.*Balance")
DATE = re.compile(r"^\s{1,8}(?P<d>\d{1,2} [A-Z][a-z]{2} \d{4})(?P<rest>\s.*)?$")
TIME = re.compile(r"^\s+\d{1,2}:\d{2} [AP]M(?P<rest>\s.*)?$")
FXRATE = re.compile(r"FX rate:\s*(?P<a>[^\d\s]*)(?P<x>" + NUM + r")\s+(?P<c1>[A-Z]{3})\s*=\s*"
                    r"(?P<b>[^\d\s]*)(?P<y>" + NUM + r")\s+(?P<c2>[A-Z]{3})")
# "$100.00 SGD to kr750.00 SEK", "€10.00 EUR to $14.50 SGD", "Ft3,000 HUF"
XCHG = re.compile(r"(?P<s1>[^\d\s]*)(?P<a1>" + NUM + r")\s+(?P<c1>[A-Z]{3})\s+to\s+"
                  r"(?P<s2>[^\d\s]*)(?P<a2>" + NUM + r")\s+(?P<c2>[A-Z]{3})")
# trailing "(kr150.00 NOK)" on a card row
CARD_FX = re.compile(r"\(\s*(?P<s>[^\d\s]*)(?P<a>" + NUM + r")\s+(?P<c>[A-Z]{3})\s*\)\s*$")
MOBILE = re.compile(r"^(?P<dir>To|From) Mobile no\. ending (?P<n>\d+)$")
NO_TXNS = re.compile(r"^\s*No transactions\s*$")
NOISE = re.compile(r"Completed Date|\(in SGT\)|^\s*Remarks:|^\s*[12]\. |page \d+ of \d+|"
                   r"^\s*Transactions|My [A-Z]{3} Statement|Account summary|Money Out \(Total\)|"
                   r"No transactions|^\s*\$0\b")

TYPE_RULES = [                     # description prefix -> bank txn type
    (re.compile(r"^Top up\b", re.I), "YouTrip Top-up"),
    (re.compile(r"^(Smart)?Exchange", re.I), "YouTrip Exchange"),
    # a merchant string carries a comma-separated city, a YouTrip charge does not
    (re.compile(r"^(?!.*,).*\brefund\b", re.I), "YouTrip Refund"),
    (re.compile(r"^(?!.*,).*\bfee\b", re.I), "YouTrip Fee"),
    (re.compile(r"^(?!.*,).*\bcashback\b", re.I), "YouTrip Cashback"),
    (re.compile(r"^(To|From) Mobile no\."), "YouTrip Other"),
    (re.compile(r"^P2P-\d+"), "YouTrip Other"),
]
INTERNAL = ("YouTrip Top-up", "YouTrip Exchange")


def wallet_of(pdf):
    """Wallet currency from the folder the PDF sits in, or None."""
    for p in Path(pdf).parents:
        if p.name.lower() in WALLETS:
            return WALLETS[p.name.lower()]
    return None


def money_re(ccy):
    """Column-amount pattern for a wallet: '$-4.05', 'kr1,234.56', '-€3.00'."""
    return re.compile(r"(?P<neg>-?)" + SYMBOL[ccy] + r"(?P<a>-?[0-9][0-9,]*\.[0-9]{2})")


def col_edges(ln):
    """Right-edge x of the Money Out / Money In / Balance columns."""
    return [ln.find(k) + len(k) for k in ("Money Out", "Money In", "Balance")]


def split_amounts(ln, edges, rx):
    """Return (money_out, money_in, balance) by nearest column right-edge."""
    out = [None, None, None]
    for m in rx.finditer(ln):
        if m.start() < min(edges) - 25:      # still inside the description column
            continue
        i = min(range(3), key=lambda k: abs(m.end() - edges[k]))
        v = money(m.group("a"))
        out[i] = -v if m.group("neg") else v
    return out


def desc_part(s):
    """The description text of a date or time line, without its amounts."""
    if not s:
        return ""
    s = ANY_MONEY.sub("", s)
    return re.sub(r"\s{2,}", " ", s).strip()


def iso(d):
    dd, mon, yr = d.split()
    return f"{int(yr):04d}-{MONTHS[mon]:02d}-{int(dd):02d}"


def plain(num):
    """'1,234.56' -> '1234.56'; leaves the printed precision alone."""
    return num.replace(",", "")


# ---------------------------------------------------------------- extraction

def parse(text, source, ccy):
    """Return (rows, block) for one statement of the given wallet."""
    lines = text.splitlines()
    accts, period = [], None
    rx = money_re(ccy)
    block = dict(source=source, account=ACCOUNT, account_no=None, currency=ccy,
                 header_ccy=None, opening=None, closing=None, period_start=None,
                 period_end=None, empty_statement=False)
    rows, edges, cur = [], None, None

    for i, ln in enumerate(lines):
        if m := ACCT.match(ln):
            if m.group(1) not in accts:
                accts.append(m.group(1))
            continue
        if (m := STMT.search(ln)) and block["header_ccy"] is None:
            block["header_ccy"] = m.group(1)
            continue
        if (m := PERIOD.match(ln)) and period is None:
            period = (m.group("a"), m.group("b"))
            block.update(period_start=iso(period[0]), period_end=iso(period[1]))
            continue
        if HEADER.search(ln):
            edges = col_edges(ln)
            cur = None
            continue
        if NO_TXNS.match(ln):
            # the layout says outright that it has nothing to print, which is
            # also why it carries no opening or closing balance
            block["empty_statement"] = True
            cur = None
            continue
        if edges is None:
            continue

        if m := DATE.match(ln):
            o, n, b = split_amounts(ln, edges, rx)
            desc = desc_part(m.group("rest"))
            if desc == "Opening Balance":
                block["opening"] = b
                cur = None
                continue
            if desc == "Closing Balance":
                block["closing"] = b
                cur = None
                continue
            pre = lines[i - 1] if i else ""
            pre = "" if (NOISE.search(pre) or TIME.match(pre) or "FX rate" in pre) else pre.strip()
            cur = dict(source=source, account=ACCOUNT, currency=ccy,
                       date=iso(m.group("d")), date_raw=m.group("d"), time=None,
                       lines=[x for x in (pre, desc) if x], fx_rate=None,
                       withdrawal=o, deposit=n, balance_after=b, synthetic=False)
            rows.append(cur)
            continue
        if cur is not None and (m := TIME.match(ln)):
            cur["time"] = ln.strip().split("  ")[0]
            # the time line carries no column amounts, so keep "$100.00 SGD to ..."
            if tail := re.sub(r"\s{2,}", " ", m.group("rest") or "").strip():
                cur["lines"].append(tail)
            continue
        if cur is not None and "FX rate" in ln:
            cur["fx_rate"] = re.sub(r"\s{2,}", " ", ln.strip())
            continue

    if accts:
        block["account_no"] = accts[0]
        block["account_nos"] = accts
    return rows, block


# ---------------------------------------------------------------- enrichment

def txn_type(desc):
    for rx, t in TYPE_RULES:
        if rx.search(desc):
            return t
    return "YouTrip Payment"


def enrich(r):
    """Turn the gathered description lines into the shared column set."""
    ccy = r["currency"]
    desc = " ".join(r["lines"]).strip()
    desc = re.sub(r"\s+", " ", desc)
    r["merchant"] = r["counterparty"] = r["reference"] = r["memo"] = None
    r["merchant_country"] = r["purchase_date"] = r["card_last4"] = r["fx_original"] = None
    r["bank_txn_type"] = t = txn_type(desc)
    r["flow_class"] = "Internal - YouTrip" if t in INTERNAL else "External"

    if t == "YouTrip Exchange":
        r["bank_txn_type"] = t = f"YouTrip Exchange {r['direction']}"
        if m := XCHG.search(desc):
            # the other side is whichever leg is not this wallet
            if m.group("c1") == ccy:
                oc, amt = m.group("c2"), m.group("a2")
            else:
                oc, amt = m.group("c1"), m.group("a1")
            r["fx_original"] = f"{oc} {plain(amt)}"
            r["memo"] = oc
        else:
            r["memo"] = desc
        if r["fx_rate"] and (m := FXRATE.search(r["fx_rate"])):
            r["reference"] = f"{m.group('x')} {m.group('c1')} = {m.group('y')} {m.group('c2')}"
        return r
    if t == "YouTrip Top-up":
        r["counterparty"] = desc.replace("Top up with ", "").strip() or None
        r["memo"] = desc
        return r
    if m := MOBILE.match(desc):
        r["counterparty"] = f"Mobile no. ending {m.group('n')}"
        r["memo"] = desc
        return r
    if t == "YouTrip Other":
        r["counterparty"] = desc
        r["memo"] = desc
        return r

    # card spend (and any refund / fee / cashback that carries a merchant)
    fx_ccy = None
    if m := CARD_FX.search(desc):
        fx_ccy = m.group("c")
        r["fx_original"] = f"{fx_ccy} {plain(m.group('a'))}"
        desc = desc[:m.start()].strip()
    if r["fx_rate"] and (m := FXRATE.search(r["fx_rate"])):
        r["memo"] = f"{m.group('x')} {m.group('c1')} = {m.group('y')} {m.group('c2')}"
    r["merchant"], r["merchant_country"] = split_country(desc, fx_ccy, ccy)
    return r


def sgd_rate(r):
    """SGD per one wallet unit implied by an exchange row, or None."""
    ccy = r["currency"]
    if ccy == "SGD" or not r["bank_txn_type"].startswith("YouTrip Exchange") or r["memo"] != "SGD":
        return None
    if r["fx_rate"] and (m := FXRATE.search(r["fx_rate"])):
        x, y = float(plain(m.group("x"))), float(plain(m.group("y")))
        if m.group("c1") == "SGD" and m.group("c2") == ccy and y:
            return x / y
        if m.group("c1") == ccy and m.group("c2") == "SGD" and x:
            return y / x
    if r["fx_original"] and abs(r["amount"]) > 0:
        return float(r["fx_original"].split()[1]) / abs(r["amount"])
    return None


# ---------------------------------------------------------------- hidden rows

def synth_row(b, jump, date, prev_date, next_date, balance_after):
    a = round(jump, 2)
    return dict(source=b["source"], account=ACCOUNT, account_no=b["account_no"],
                currency=b["currency"], statement_date=b["period_end"], date=date,
                date_raw=None, time=None, lines=[], fx_rate=None, synthetic=True,
                withdrawal=-a if a < 0 else None, deposit=a if a > 0 else None,
                balance_after=round(balance_after, 2), amount=a,
                direction=direction(a), bank_txn_type="YouTrip Unprinted",
                flow_class="External", merchant=None, merchant_country=None,
                purchase_date=None, card_last4=None, fx_original=None, counterparty=None,
                reference=None, balance_note=None,
                memo=f"not printed by the YouTrip export; balance jump between {prev_date} and {next_date}")


def classify_synthetic(s, prev_date, next_date, topups, exchanges):
    """Internal when the jump is a top-up size or mirrors another wallet's
    printed exchange over the same days; the reason lands in reference."""
    want = f"{s['currency']} {abs(s['amount']):.2f}"
    for r in exchanges:
        if r["currency"] == s["currency"] or not (prev_date <= r["date"] <= next_date):
            continue
        if r["fx_original"] == want and r["direction"] != s["direction"]:
            s["flow_class"] = "Internal - YouTrip"
            s["reference"] = f"mirrors {r['currency']} wallet exchange of {r['date']}"
            return
    if s["amount"] > 0 and round(s["amount"], 2) in topups:
        s["flow_class"] = "Internal - YouTrip"
        s["reference"] = "known top-up size"


def read_in_the_wrong_column(run, r):
    """True when the row's own amount, with its sign turned over, is what
    takes the running balance to the printed one: the amount was read into
    Money In for Money Out or the other way round (split_amounts picks the
    nearest column edge). That gap is the parser's own misread, twice the
    row's amount, and never a row the export left out."""
    return bool(r["amount"]) and abs(run - r["amount"] - r["balance_after"]) <= 0.005


def synthesise(b, rs, topups, exchanges):
    """Replay one statement; return (rows with synthetic ones inserted, gaps).

    A gap that a row read in the wrong column explains is not filled: the
    row is listed in b["misreads"] and left to fail the balance check
    (validate), so the run stops rather than loading the misread beside a
    synthetic row that hides it."""
    out, gaps = [], []
    b["misreads"] = []
    run = b["opening"] if b["opening"] is not None else 0.0
    prev_date = b["period_start"]
    for r in rs:
        if r["balance_after"] is not None and abs(run + r["amount"] - r["balance_after"]) > 0.005:
            if read_in_the_wrong_column(run, r):
                b["misreads"].append(r)
                run = r["balance_after"]
                out.append(r)
                prev_date = r["date"]
                continue
            jump = r["balance_after"] - (run + r["amount"])
            s = synth_row(b, jump, r["date"], prev_date, r["date"], run + jump)
            classify_synthetic(s, prev_date, r["date"], topups, exchanges)
            out.append(s)
            gaps.append(s)
            run += jump
        run += r["amount"]
        if r["balance_after"] is not None:
            run = r["balance_after"]
        out.append(r)
        prev_date = r["date"]
    if b["closing"] is not None and abs(run - b["closing"]) > 0.005:
        jump = b["closing"] - run
        s = synth_row(b, jump, b["period_end"], prev_date, b["period_end"], b["closing"])
        classify_synthetic(s, prev_date, b["period_end"], topups, exchanges)
        out.append(s)
        gaps.append(s)
    return out, gaps


def backfill_account_no(blocks):
    """Early statements print no Y-number; borrow the one every later one carries."""
    first = next((b["account_no"] for b in sorted(blocks, key=lambda b: b["period_end"])
                  if b["account_no"]), None)
    for b in blocks:
        if not b["account_no"]:
            b["account_no"] = first or "unknown"
            b["note_acct"] = "account number not printed; taken from later statements"


def enrich_all(rows, blocks):
    """Amount, direction and the structured fields. Returns (rows, dropped rows);
    a row the export printed with no amount at all is not a transaction."""
    by_src = {b["source"]: b for b in blocks}
    for r in rows:
        b = by_src[r["source"]]
        r["account_no"] = b["account_no"]
        r["statement_date"] = b["period_end"]
        r["amount"] = round((r["deposit"] or 0) - (r["withdrawal"] or 0), 2)
        r["direction"] = direction(r["amount"])
        enrich(r)
    no_amount = [r for r in rows if r["withdrawal"] is None and r["deposit"] is None]
    return [r for r in rows if r not in no_amount], no_amount


def reconcile_openings(blocks, rows_by_src, topups, exchanges):
    """A month whose printed opening differs from the previous statement's
    closing lost a movement between the two exports: synthesise it on the first
    of the month and take the previous closing as the opening, so that
    opening + in - out still equals the printed closing."""
    for ccy in sorted({b["currency"] for b in blocks}):
        prev = None
        for b in sorted((b for b in blocks if b["currency"] == ccy), key=lambda b: b["period_end"]):
            if b["opening"] is not None and prev is not None \
                    and abs(prev["closing"] - b["opening"]) > 0.005:
                jump = b["opening"] - prev["closing"]
                s = synth_row(b, jump, b["period_start"], prev["period_end"], b["period_start"],
                              b["opening"])
                classify_synthetic(s, prev["period_end"], b["period_start"], topups, exchanges)
                rows_by_src[b["source"]].insert(0, s)
                b["opening_printed"], b["opening"] = b["opening"], prev["closing"]
                b["note_open"] = (f"printed opening {b['opening_printed']:,.2f} differs from the "
                                  f"{prev['period_end'][:7]} closing {prev['closing']:,.2f}; "
                                  f"opening set to the latter and {jump:+,.2f} synthesised on "
                                  f"{b['period_start']}")
            if b["closing"] is not None:
                prev = b


def synthesise_all(rows, blocks):
    """Fill the export's hidden rows: first the gaps between statements, then
    the ones inside each statement."""
    topups = {round(r["amount"], 2) for r in rows if r["bank_txn_type"] == "YouTrip Top-up"}
    exchanges = [r for r in rows if r["bank_txn_type"].startswith("YouTrip Exchange")]
    rows_by_src = defaultdict(list)
    for r in rows:
        rows_by_src[r["source"]].append(r)
    reconcile_openings(blocks, rows_by_src, topups, exchanges)
    out = []
    for b in blocks:
        rs, gaps = synthesise(b, rows_by_src[b["source"]], topups, exchanges)
        b["gaps"] = gaps
        out += rs
    out.sort(key=lambda r: (r["date"], r["source"]))   # stable: statement order within a day
    return out


# ---------------------------------------------------------------- entry point

def period_name(b):
    return f"{b['account_no']} · {b['currency']} · {b['period_end'][:7]}"


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="*", default=None,
                    help="PDFs or directories to parse (default: the youtrip-* folders in the data folder)")
    ap.add_argument("--out", metavar="DIR",
                    help="also write transactions.csv and period_balances.csv here")
    ap.add_argument("--wallet", metavar="CCY",
                    help="only this wallet folder (sgd-main, sek, usd, eur, myr, aud)")
    ap.add_argument("--no-text", action="store_true",
                    help="do not save the .txt dump beside each PDF")
    args = ap.parse_args()

    require_pdftotext()
    pdfs = collect_pdfs(args.paths or statement_folders("youtrip-"))
    if args.wallet:
        want = WALLETS.get(args.wallet.lower(), args.wallet.upper())
        pdfs = [p for p in pdfs if wallet_of(p) == want]
    if not pdfs:
        sys.exit("no PDFs found")

    rows, blocks, unparsed = [], [], []
    for pdf in pdfs:
        ccy = wallet_of(pdf)
        if ccy is None:
            sys.exit(f"{pdf}: not inside a wallet folder ({', '.join(WALLETS)})")
        text = pdf_to_text(pdf, write=not args.no_text)
        r, b = parse(text, str(pdf), ccy)
        if b["header_ccy"] != ccy:
            sys.exit(f"{pdf}: folder says {ccy} but the statement says "
                     f"{b['header_ccy'] or 'nothing'}")
        if b["period_end"] is None:
            unparsed.append((str(pdf), "no statement period line found"))
            warn(pdf, "no statement period line found; the file is not parsed")
            continue
        rows += r
        blocks.append(b)

    backfill_account_no(blocks)
    report_duplicate_periods(blocks, period_name)
    rows, no_amount = enrich_all(rows, blocks)
    rows = synthesise_all(rows, blocks)

    bad = validate(rows, blocks)
    bad_src = {x[0] for x in bad}
    for b in blocks:
        notes = []
        if b.get("note_acct"):
            notes.append(b["note_acct"])
        if b["balance_state"] in ("empty", "missing"):
            notes.append(BALANCE_STATES[b["balance_state"]])
            if b["balance_state"] == "missing":
                warn(b["source"], BALANCE_STATES["missing"])
        if b.get("note_open"):
            notes.append(b["note_open"])
        if g := [s for s in b["gaps"]]:
            notes.append(f"{len(g)} movement(s) not printed: "
                         + ", ".join(f"{s['amount']:+,.2f} on {s['date']}" for s in g))
        b["n_synth"] = len([r for r in rows if r["source"] == b["source"] and r["synthetic"]])
        b["note"] = "; ".join(notes)
        if b["computed_closing"] == 0:
            b["computed_closing"] = 0.0          # no "-0.00"
        b["ok"] = b["ok"] and b["source"] not in bad_src
    add_keys(rows)

    # SGD value of a foreign wallet: latest SGD exchange printed in that wallet so far
    for ccy in sorted({b["currency"] for b in blocks} - {"SGD"}):
        rate = None
        for b in sorted((b for b in blocks if b["currency"] == ccy), key=lambda b: b["period_end"]):
            for r in rows:
                if r["source"] == b["source"] and (x := sgd_rate(r)):
                    rate = (x, r["date"])
            if rate:
                b["fx_to_sgd"] = round(rate[0], 6)
                b["closing_sgd"] = round(b["closing"] * rate[0], 2) if b["closing"] is not None else None
                b["note"] = "; ".join(x for x in (b["note"], f"fx_to_sgd from the {rate[1]} exchange rate") if x)

    ordered = sorted(blocks, key=lambda b: (b["currency"] != "SGD", b["currency"], b["period_end"]))
    print(f"{len(pdfs)} PDFs -> {len(rows)} rows ({sum(r['synthetic'] for r in rows)} synthetic), "
          f"{len(blocks)} statement-months\n")
    fmt_num = lambda x: f"{x:>10,.2f}" if x is not None else f"{'-':>10}"
    print(f"  {'period':34s} {'opening':>10} {'closing':>10}  rows synth reconciled")
    for b in ordered:
        print(f"  {period_name(b):34s} {fmt_num(b['opening'])} {fmt_num(b['closing'])} "
              f"{b['n_txns'] - b['n_synth']:5d} {b['n_synth']:5d} {'yes' if b['ok'] else 'NO':>10}"
              + (f"  {b['note']}" if b.get("note") else ""))
    for src, why in unparsed:
        print(f"  FAIL {src}: {why}")

    for b in blocks:
        for r in b.get("misreads", ()):
            warn(b["source"], f"{r['date']} {r['currency']} {r['amount']:+,.2f} ({' '.join(r['lines'])[:40]}) "
                              "balances only with its sign turned over: the amount was read in the wrong "
                              "column, so no row is synthesised for it")
    for x in bad:
        warn(x[0], f"mid-chain balance mismatch left after synthesis: {x[1]} {x[2]} "
                   f"{x[3]} computed {x[4]:,.2f} against printed {x[5]:,.2f}")
    for r in no_amount:
        warn(r["source"], f"row carried no amount and was dropped: {r['date']} "
                          f"{' '.join(r['lines'])}")

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
    for ccy in sorted({b["currency"] for b in blocks}, key=lambda c: (c != "SGD", c)):
        bs = [b for b in blocks if b["currency"] == ccy]
        n_s = sum(b["n_synth"] for b in bs)
        n_p = sum(b["n_txns"] for b in bs) - n_s
        print(f"{ccy}: {len(bs)} statements, {n_p} printed + {n_s} synthetic rows, "
              f"{sum(b['ok'] for b in bs)}/{len(bs)} reconciled")

    failed = [b for b in blocks if not b["ok"]]
    tail = balance_summary(blocks)
    if failed or bad or unparsed:
        sys.exit(f"\n{len(failed)} statement(s) still unreconciled after synthesis, "
                 f"{len(unparsed)} unparsed; {tail}")
    print(f"\nEvery balance chain reconciles; {tail}")


# ---------------------------------------------------------------- the layout (G21)

def _sees(text):
    head = [ln for ln in text.splitlines() if ln.strip()][:12]
    m = next((m for ln in head if (m := STMT.search(ln))), None)
    if m is None or "Completed Date" not in text:
        return None
    return Seen(currency=m.group(1),
                numbers=tuple(a.group(1) for ln in text.splitlines() if (a := ACCT.match(ln))))


def name_upload(pdf, read_text, wallet, today):
    """(year folder, file name) for an uploaded statement: its month."""
    _, block = parse(read_text(pdf), Path(pdf).name, wallet)
    return by_months([block.get("period_end") or ""])


register_bank(Bank("youtrip", r"youtrip", order=60, name="YouTrip"))
register(Layout(
    name="youtrip", parser="youtrip", module=__spec__.name, order=60, kind="wallet",
    label="a YouTrip {ccy} statement", title="YouTrip wallet",
    folder="youtrip-{ccy}", setup="YouTrip {ccy} wallet", csv="youtrip",
    currencies=("SGD", "USD", "EUR", "MYR"),
    test=_sees, bank="youtrip", folder_title="YouTrip", name_file=name_upload, status=CHECKED,
    new_types=("YouTrip Refund", "YouTrip Fee", "YouTrip Cashback"),
    detected_by="`My XXX Statement`, which must agree with the folder",
    balance_check="opening + in - out = printed closing, after synthesis"))


if __name__ == "__main__":
    main()
