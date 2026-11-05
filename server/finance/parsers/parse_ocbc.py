#!/usr/bin/env python3
"""Extract OCBC deposit-account statement PDFs (the 360 Account and its
statement-savings siblings) into transaction rows.

Usage:
    python3 -m finance.parsers.parse_ocbc [PATH ...] [--out DIR] [--no-text]

PATH defaults to the data folder's ocbc-* statement folders. With --out,
transactions.csv and period_balances.csv are written there, in the same columns
as every other parser.

G11: the layout comes from a public sample of an OCBC 360 statement, not from a
statement of the owner's, so treat it as unverified until one real statement
has parsed and balanced:

  * a "STATEMENT OF ACCOUNT" page with "<d> <MON> <yyyy> TO <d> <MON> <yyyy>";
  * "Account No. <digits>" under the product name;
  * columns Transaction Date, Value Date, Description, Cheque, Withdrawal,
    Deposit, Balance, the two date headings split over two lines;
  * dates as "DD MON" with no year, taken from the statement period;
  * "BALANCE B/F" opens the chain and "BALANCE C/F" closes it, and a long
    description wraps onto lines with no date.

As with the DBS parser, a number's column is decided by its position against
the header row (re-read on every page), and every row's running balance is
replayed against the printed one. A chain that drifts stops the run.
"""
import argparse
import csv
import re
import shutil
import sys
from pathlib import Path

from . import common, parse_statements as ps
from .common import period_name
from .layouts import SAMPLE, Bank, Layout, NotThisLayout, Seen, by_months, register, register_bank

ACCOUNT = "OCBC Account"
PERIOD = re.compile(r"(\d{1,2}) ([A-Z]{3}) (\d{4}) TO (\d{1,2}) ([A-Z]{3}) (\d{4})")
ACCT_NO = re.compile(r"Account No\.\s*(?P<no>[\d-]+)")
PRODUCT = re.compile(r"^\s*(?P<name>[A-Z0-9][A-Z0-9 +&-]*ACCOUNT)\s*$")
HEADER = re.compile(r"Withdrawal\s+Deposit\s+Balance")
ROW = re.compile(r"^(?P<td>\d{2} [A-Z]{3})\s+(?P<vd>\d{2} [A-Z]{3})\s+(?P<rest>.*)$")
BF = re.compile(r"^\s+BALANCE B/F\b")
CF = re.compile(r"^\s+BALANCE C/F\b")
MON = {m.upper(): i for m, i in ps.MONTHS.items()}
PAGE_LINE = re.compile(r"^\s*(STATEMENT OF ACCOUNT|Account No\.|Transaction\b|Date\b|Page \d)")
HEADINGS = ("Withdrawal", "Deposit", "Balance")   # the money columns, by their right edges

# the first description line names the kind of row
INTERNAL_HINTS = ("OWN ACCOUNT",)


def detect(text):
    head = "\n".join(text.splitlines()[:15])
    return "STATEMENT OF ACCOUNT" in head and bool(PERIOD.search(text)) and "BALANCE B/F" in text


def parse(text, source):
    """(rows, block) for one statement."""
    lines = text.splitlines()
    pm = PERIOD.search(text)
    if not pm:
        return [], None
    start = (int(pm.group(3)), MON[pm.group(2)], int(pm.group(1)))
    end = (int(pm.group(6)), MON[pm.group(5)], int(pm.group(4)))
    acct = ACCT_NO.search(text)
    product = next((m.group("name").title() for ln in lines
                    if (m := PRODUCT.match(ln)) and "STATEMENT" not in ln), ACCOUNT)
    block = dict(source=source, account=f"OCBC {product}", account_no=acct.group("no") if acct else "",
                 currency="SGD", opening=None, closing=None,
                 period_start=f"{start[0]:04d}-{start[1]:02d}-{start[2]:02d}",
                 period_end=f"{end[0]:04d}-{end[1]:02d}-{end[2]:02d}")
    rows, edges, cur = [], None, None
    for ln in lines:
        if HEADER.search(ln):
            edges = common.heading_edges(ln, HEADINGS)
            cur = None
            continue
        if edges is None:
            continue
        if BF.match(ln):
            block["_opening_c"] = common.amounts(ln, edges)[2]
            continue
        if CF.match(ln):
            block["_closing_c"] = common.amounts(ln, edges)[2]
            cur = None
            continue
        m = ROW.match(ln)
        if m:
            wd, dep, bal = common.amounts(ln, edges)
            cols = common.column_amounts(ln, edges)
            cut = min((a.start() for a, _ in cols), default=len(ln))
            desc = ln[m.start("rest"):cut].strip()
            cents = (dep or 0) - (wd or 0)
            cur = dict(date=common.date_in_period(m.group("td"), start, end, MON), account=block["account"],
                       account_no=block["account_no"], currency="SGD",
                       bank_txn_type=f"OCBC {desc.title()}", direction="In" if cents > 0 else "Out",
                       amount=cents / 100, flow_class="External", merchant="", merchant_country="",
                       purchase_date="", card_last4="", fx_original="", counterparty="",
                       reference="", memo="", balance_after=None if bal is None else bal / 100,
                       source=source, _cents=cents, _balance_c=bal, _detail=[])
            rows.append(cur)
            continue
        # a wrapped description line sits in the description column with no
        # date and no amount in a money column; a page's own heading lines
        # are not one
        if (cur is not None and ln.strip() and ln.startswith(" " * 10)
                and not common.column_amounts(ln, edges) and not PAGE_LINE.match(ln)):
            cur["_detail"].append(ln.strip())
    for k in ("opening", "closing"):
        c = block.pop(f"_{k}_c", None)
        block[f"_{k}_c"] = c
        block[k] = None if c is None else c / 100
    for r in rows:
        _detail(r)
    return rows, block


def _detail(r):
    """Spread the wrapped description lines into the usual fields."""
    detail = r.pop("_detail")
    kind = r["bank_txn_type"].upper()
    if detail and "PURCHASE" in kind:
        m = re.match(r"xx-(\d{4})\s+(.*)$", detail[0])
        if m:
            r["card_last4"], text = m.group(1), m.group(2)
        else:
            text = detail[0]
        cc = re.search(r"\s([A-Z]{2})$", text)
        r["merchant"] = text
        if cc and cc.group(1) in ps.ALPHA2:
            r["merchant_country"] = ps.ALPHA2[cc.group(1)]
        r["memo"] = " ".join(detail[1:])
    elif detail:
        who = re.sub(r"^(TO|FROM):\s*", "", detail[0])
        if "GIRO" in kind or "SALARY" in kind:
            r["counterparty"] = who
        else:
            r["counterparty"] = who
        r["memo"] = " ".join(detail[1:])
    if any(h in " ".join(detail).upper() for h in INTERNAL_HINTS):
        r["flow_class"] = "Internal - own account"


def validate(rows, block):
    """Replay the chain in integer cents: every row must print its running
    balance and match it, then the closing. A row whose balance did not come
    through is a failure, never skipped."""
    bad = []
    if block.get("_opening_c") is None:
        bad.append(("", "BALANCE B/F", None, "no opening balance printed"))
    run = block.get("_opening_c") or 0
    for r in rows:
        run += r["_cents"]
        if r["_balance_c"] is None:
            bad.append((r["date"], r["bank_txn_type"], run / 100, "no running balance printed"))
        elif run != r["_balance_c"]:
            bad.append((r["date"], r["bank_txn_type"], run / 100, r["_balance_c"] / 100))
    closing = block.get("_closing_c")
    ok = not bad and closing is not None and run == closing
    block.update(computed_closing=run / 100, ok=ok, n_txns=len(rows),
                 total_in=sum(max(r["_cents"], 0) for r in rows) / 100,
                 total_out=sum(max(-r["_cents"], 0) for r in rows) / 100,
                 fx_to_sgd="", closing_sgd=block["closing"], note="")
    return bad


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="*", default=None,
                    help="PDFs or directories (default: the ocbc-* folders in the data folder)")
    ap.add_argument("--out", metavar="DIR")
    ap.add_argument("--no-text", action="store_true")
    args = ap.parse_args()
    if not shutil.which("pdftotext"):
        sys.exit("pdftotext not found (brew install poppler)")
    pdfs = ps.collect_pdfs(args.paths or ps.statement_folders("ocbc-"))
    if not pdfs:
        sys.exit("no PDFs found")
    rows, blocks, bad = [], [], []
    for pdf in pdfs:
        text = ps.pdf_to_text(pdf, write=not args.no_text)
        if not detect(text):
            ps.warn(str(pdf), "no transactions parsed")
            continue
        r, b = parse(text, str(pdf))
        bad += [(str(pdf), *x) for x in validate(r, b)]
        rows += r
        blocks.append(b)
    ps.add_keys(rows)
    for b in blocks:
        print(f"  {'OK  ' if b['ok'] else 'BAD '}{b['source']} {b['n_txns']:3d} txn  "
              f"open {b['opening']:,.2f} -> close {b['closing'] or 0:,.2f} "
              f"(computed {b['computed_closing']:,.2f})")
    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        with (out / "transactions.csv").open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=ps.TXN_COLS, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
        with (out / "period_balances.csv").open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=ps.BAL_COLS, extrasaction="ignore")
            w.writeheader()
            for b in blocks:
                w.writerow({**b, "period": period_name(b), "reconciled": b["ok"]})
    failed = [b for b in blocks if not b["ok"]]
    if failed or bad:
        sys.exit(f"{len(failed)} block(s) and {len(bad)} row(s) failed the balance check")
    print(f"{len(pdfs)} PDFs -> {len(rows)} transactions; every balance chain reconciles")


# ---------------------------------------------------------------- the layout (G21)

def _sees(text):
    if not detect(text):
        return None
    m = ACCT_NO.search(text)
    return Seen(numbers=(m.group("no") if m else "",))


def name_upload(pdf, read_text, wallet, today):
    """(year folder, file name) for an uploaded statement: its period's month."""
    text = read_text(pdf)
    if not detect(text):
        raise NotThisLayout("This PDF is not an OCBC account statement")
    _, block = parse(text, Path(pdf).name)
    return by_months([(block or {}).get("period_end") or ""])


register_bank(Bank("ocbc", r"\bocbc\b", order=92, name="OCBC"))
register(Layout(
    name="ocbc", parser="ocbc", module=__spec__.name, order=50, kind="bank account",
    label="an OCBC account statement", title="OCBC 360 account",
    folder="ocbc-360", setup="OCBC 360 account", csv="ocbc",
    test=_sees, name_file=name_upload, bank="ocbc", folder_title="OCBC 360", status=SAMPLE,
    detected_by="`STATEMENT OF ACCOUNT` with an OCBC product line",
    balance_check="`BALANCE B/F` + deposits - withdrawals = `BALANCE C/F`, every running balance replayed"))


if __name__ == "__main__":
    main()
