#!/usr/bin/env python3
"""Extract UOB deposit-account statement PDFs (the UOB One Account and the
savings accounts, Stash and Uniplus) into transaction rows.

Usage:
    python3 -m finance.parsers.parse_uob [PATH ...] [--out DIR] [--no-text]

PATH defaults to the data folder's uob-* statement folders. With --out,
transactions.csv and period_balances.csv are written there, in the same columns
as every other parser.

G21: the layout comes from a public sample, not from a statement of the
owner's, so treat it as unverified until one real statement has parsed and
balanced. UOB publishes no sample deposit statement. The sample is a set of
synthetic previews on a statement converter's page, one each for the One,
Uniplus and Stash accounts, marked there as made up for demonstration:

  https://bankstatemently.com/banks/sg/uob/bank-statement

The One and the savings previews print the same "Statement of Account", so one
parse reads both; the registry still declares two layouts, told apart by the
product the statement names, so Setup makes a folder for each. What the sample
shows:

  * "Statement of Account" and "Period: 01 Mar 2026 to 31 Mar 2026" on the
    first page, with an account overview;
  * an "Account Transaction Details" page per account, the product and its
    number (123-456-789-0) on the bar under it;
  * columns Date, Description, Withdrawals, Deposits, Balance, each money
    heading with "SGD" under it;
  * dates as "DD Mon" with no year, taken from the statement period;
  * "BALANCE B/F" opens the chain on the period's first day, and a description
    runs on over lines with no date.

The same page says, in words rather than in a preview, that the statements
repeat a balance brought forward and a balance carried forward on every page;
the sample shows no last page, so the closing `BALANCE C/F` and the `Total`
line of withdrawals and deposits are taken on that word. A brought-forward line
after the first is checked against the running balance, never read as a new
opening. The closing must also be the balance the first page's overview prints
for the account (the Uniplus preview shows it), so a statement that has lost its
last page is refused rather than ending at an earlier page's carried balance.

As with the OCBC parser, a number's column is decided by its position against
the header row (re-read on every page), and every row's running balance is
replayed against the printed one in integer cents. A chain that drifts stops
the run.
"""
import argparse
import csv
import re
import shutil
import sys
from decimal import Decimal
from pathlib import Path

from . import parse_statements as ps
from .layouts import SAMPLE, Bank, Layout, NotThisLayout, Seen, by_months, register, register_bank

DATE = r"\d{2} [A-Z][a-z]{2}"
PERIOD = re.compile(rf"Period:\s*({DATE} \d{{4}})\s+to\s+({DATE} \d{{4}})")
DETAILS = re.compile(r"^\s*Account Transaction Details\s*$")
# the bar under the heading: the product, then its number
BAR = re.compile(r"^\s*(?P<product>[A-Za-z][A-Za-z0-9 &+'!-]*?)\s+(?P<no>\d{3}-\d{3}-\d{3}-\d)\s*$")
HEADER = re.compile(r"Withdrawals\s+Deposits\s+Balance")
ROW = re.compile(rf"^(?P<date>{DATE})(?: \d{{4}})?\s+(?P<rest>\S.*)$")
BF = re.compile(rf"^(?:{DATE}(?: \d{{4}})?)?\s*(?:BALANCE B/F|Balance brought forward)\b", re.I)
CF = re.compile(rf"^(?:{DATE}(?: \d{{4}})?)?\s*(?:BALANCE C/F|Balance carried forward)\b", re.I)
TOTAL = re.compile(r"^\s+Total\s+[0-9]")
OVERVIEW = re.compile(r"^\s*(Statement of Account|Account Overview)\b")
PAGE_LINE = re.compile(r"^\s*(Page \d+ of \d+|United Overseas Bank|Account Transaction Details|-{5,}|SGD\s+SGD)")
CARD_DATE = re.compile(rf"^(?P<date>{DATE})\s+(?P<ref>\S+)$")
# the first page's overview: an account's number, its currency and figures, its balance last
SUMMARY = re.compile(r"^\s*(?P<no>\d{3}-\d{3}-\d{3}-\d)\s+SGD\b.*?(?P<bal>[0-9][0-9,]*\.[0-9]{2})\s*$")
MON = ps.MONTHS

# how far an amount may sit from its column heading's right edge
ALIGN = 3


def _bars(text):
    """[(product, account number)] for every account the transaction pages
    name, once each, in order."""
    lines = text.splitlines()
    out = []
    for i, ln in enumerate(lines):
        if DETAILS.match(ln):
            nxt = next((x for x in lines[i + 1:i + 4] if x.strip()), "")
            m = BAR.match(nxt)
            if m and (m.group("product").strip(), m.group("no")) not in out:
                out.append((m.group("product").strip(), m.group("no")))
    return out


def detect(text):
    head = "\n".join(text.splitlines()[:20])
    return "Statement of Account" in head and bool(PERIOD.search(text)) and bool(_bars(text))


def is_one(product):
    """The UOB One Account, against the savings accounts (Stash, Uniplus)."""
    return bool(re.search(r"\bOne\b", product))


def _edges(line):
    """Right edges of the Withdrawals, Deposits and Balance headings."""
    return [line.index(h) + len(h) for h in ("Withdrawals", "Deposits", "Balance")]


def _cents(text):
    """'1,234.56' as integer cents, exactly: money never passes through a float."""
    return int((Decimal(text.replace(",", "")) * 100).to_integral_value())


def _column_amounts(line, edges):
    """[(match, column)] for the amounts right-aligned to the Withdrawals,
    Deposits or Balance heading. A number anywhere else is description text."""
    out = []
    for m in ps.MONEY.finditer(line):
        i = min(range(3), key=lambda k: abs(m.end() - edges[k]))
        if abs(m.end() - edges[i]) <= ALIGN:
            out.append((m, i))
    return out


def _amounts(line, edges):
    """[withdrawal, deposit, balance] in cents, None where the column is empty."""
    out = [None, None, None]
    for m, i in _column_amounts(line, edges):
        out[i] = _cents(m.group())
    return out


def _ymd(printed):
    d, mon, y = printed.split()
    return int(y), MON[mon], int(d)


def _date(ddmon, start, end):
    """'05 Mar' as an ISO date inside the statement period (a statement that
    runs over the new year carries December and January dates)."""
    d, mon = ddmon.split()
    m = MON[mon]
    year = end[0] if m <= end[1] else start[0]
    if start[0] != end[0] and m >= start[1]:
        year = start[0]
    return f"{year:04d}-{m:02d}-{int(d):02d}"


def parse(text, source):
    """(rows, blocks) for one statement: a block per account it details."""
    pm = PERIOD.search(text)
    if not pm:
        return [], []
    start, end = _ymd(pm.group(1)), _ymd(pm.group(2))
    blocks, rows, summary = {}, [], {}
    block = edges = cur = None
    expect_bar = False
    for ln in text.splitlines():
        if OVERVIEW.match(ln):
            # a statement's overview pages: nothing here is an account's row
            block = edges = cur = None
            continue
        sm = SUMMARY.match(ln)
        if sm and block is None:
            summary[sm.group("no")] = _cents(sm.group("bal"))
            continue
        if DETAILS.match(ln):
            expect_bar, edges, cur = True, None, None
            continue
        if expect_bar and ln.strip():
            expect_bar = False
            m = BAR.match(ln)
            if m:
                no, product = m.group("no"), m.group("product").strip()
                block = blocks.setdefault(no, dict(
                    source=source, account=f"UOB {product}", account_no=no, currency="SGD",
                    product=product, period_start=f"{start[0]:04d}-{start[1]:02d}-{start[2]:02d}",
                    period_end=f"{end[0]:04d}-{end[1]:02d}-{end[2]:02d}",
                    _opening_c=None, _closing_c=None, _marks=[], _total=None, _rows=[]))
            continue
        if HEADER.search(ln):
            edges = _edges(ln)
            cur = None
            continue
        if edges is None or block is None:
            continue
        if BF.match(ln) or CF.match(ln):
            bal = _amounts(ln, edges)[2]
            kind = "bf" if BF.match(ln) else "cf"
            if kind == "bf" and block["_opening_c"] is None and not block["_rows"]:
                block["_opening_c"] = bal
            else:
                # a page's carried or brought forward balance: a check on the chain
                block["_marks"].append((len(block["_rows"]), kind, bal))
            if kind == "cf":
                block["_closing_c"] = bal
            cur = None
            continue
        if TOTAL.match(ln) and _column_amounts(ln, edges):
            wd, dep, _ = _amounts(ln, edges)
            block["_total"] = (wd or 0, dep or 0)
            cur = None
            continue
        m = ROW.match(ln)
        if m:
            wd, dep, bal = _amounts(ln, edges)
            cols = _column_amounts(ln, edges)
            cut = min((a.start() for a, _ in cols), default=len(ln))
            desc = ln[m.start("rest"):cut].strip()
            cents = (dep or 0) - (wd or 0)
            cur = dict(date=_date(m.group("date"), start, end), account=block["account"],
                       account_no=block["account_no"], currency="SGD",
                       bank_txn_type=f"UOB {desc}", direction="In" if cents > 0 else "Out",
                       amount=cents / 100, flow_class="External", merchant="", merchant_country="",
                       purchase_date="", card_last4="", fx_original="", counterparty="",
                       reference="", memo="", balance_after=None if bal is None else bal / 100,
                       source=source, _cents=cents, _balance_c=bal, _detail=[])
            block["_rows"].append(cur)
            rows.append(cur)
            continue
        # a description runs on in the description column, with no date and
        # no amount in a money column; a page's own heading and footer lines
        # are not part of it
        if (cur is not None and ln.strip() and ln.startswith(" " * 10)
                and not _column_amounts(ln, edges) and not PAGE_LINE.match(ln)):
            cur["_detail"].append(ln.strip())
    out = []
    for b in blocks.values():
        b.pop("_rows")
        b["_summary_c"] = summary.get(b["account_no"])
        for k in ("opening", "closing"):
            c = b[f"_{k}_c"]
            b[k] = None if c is None else c / 100
        out.append(b)
    for r in rows:
        _detail(r, start, end)
    return rows, out


def _on_or_before(ddmon, posted):
    """'31 Dec' as the last such day on or before the ISO date `posted`: a
    card purchase is posted on its day or after, so one posted on 2 Jan
    2026 was bought on 31 Dec 2025."""
    d, mon = ddmon.split()
    year, m = int(posted[:4]), MON[mon]
    if f"{m:02d}-{int(d):02d}" > posted[5:]:
        year -= 1
    return f"{year:04d}-{m:02d}-{int(d):02d}"


def _detail(r, start, end):
    """Spread the run-on description lines into the usual fields. A debit card
    row prints the day of the purchase and a reference, then the merchant with
    its city and country."""
    detail = r.pop("_detail")
    if not detail:
        return
    if "DEBIT CARD" in r["bank_txn_type"].upper():
        m = CARD_DATE.match(detail[0])
        if m:
            r["purchase_date"] = _on_or_before(m.group("date"), r["date"])
            r["reference"] = m.group("ref")
            detail = detail[1:]
        if detail:
            # the merchant, its city and its country sit apart, a wide gap between each
            parts = re.split(r"\s{2,}", detail[0])
            if len(parts) > 1 and parts[-1] in ps.ALPHA2:
                r["merchant_country"] = ps.ALPHA2[parts[-1]]
            r["merchant"] = parts[0]
            r["memo"] = " ".join(detail[1:])
        return
    # a transfer's first line is its reference when it reads as one (mBK-...)
    if re.fullmatch(r"[A-Za-z]{2,4}-\d{6,}", detail[0]):
        r["reference"], detail = detail[0], detail[1:]
    if detail:
        r["counterparty"] = detail[0]
        r["memo"] = " ".join(detail[1:])


def validate(rows, blocks):
    """Replay each account's chain in integer cents: every row must print its
    running balance and match it, every page's carried and brought forward
    balance must match it, and the last carried forward balance is the
    closing, which must be the balance the first page's overview prints for
    the account: a statement missing its last page ends at an earlier page's
    carried balance, and is caught there. A printed Total of withdrawals and
    deposits must match the rows. A balance that did not come through is a
    failure, never skipped. Each account is judged on its own failures."""
    every = []
    for b in blocks:
        bad = []
        mine = [r for r in rows if r["account_no"] == b["account_no"]]
        if b["_opening_c"] is None:
            bad.append(("", "BALANCE B/F", None, "no opening balance printed"))
        run = b["_opening_c"] or 0
        marks = list(b["_marks"])
        for i, r in enumerate(mine + [None]):
            while marks and marks[0][0] == i:
                _, kind, bal = marks.pop(0)
                if bal != run:
                    bad.append(("", "BALANCE C/F" if kind == "cf" else "BALANCE B/F", run / 100,
                                None if bal is None else bal / 100))
            if r is None:
                break
            run += r["_cents"]
            if r["_balance_c"] is None:
                bad.append((r["date"], r["bank_txn_type"], run / 100, "no running balance printed"))
            elif run != r["_balance_c"]:
                bad.append((r["date"], r["bank_txn_type"], run / 100, r["_balance_c"] / 100))
        total_in = sum(max(r["_cents"], 0) for r in mine)
        total_out = sum(max(-r["_cents"], 0) for r in mine)
        if b["_total"] is not None and b["_total"] != (total_out, total_in):
            bad.append(("", "Total", f"{total_out / 100:.2f} / {total_in / 100:.2f}",
                        f"{b['_total'][0] / 100:.2f} / {b['_total'][1] / 100:.2f}"))
        closing = b["_closing_c"]
        if b["_summary_c"] is None:
            bad.append(("", "Account Overview", run / 100, "no overview balance printed"))
        elif closing is not None and closing != b["_summary_c"]:
            bad.append(("", "Account Overview", closing / 100, b["_summary_c"] / 100))
        ok = not bad and closing is not None and run == closing
        b.update(computed_closing=run / 100, ok=ok, n_txns=len(mine),
                 total_in=total_in / 100, total_out=total_out / 100,
                 fx_to_sgd="", closing_sgd=b["closing"], note="")
        every += bad
    return every


def period_name(block):
    return f"{block['account_no']} · {block['currency']} · {block['period_end'][:7]}"


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="*", default=None,
                    help="PDFs or directories (default: the uob-* folders in the data folder)")
    ap.add_argument("--out", metavar="DIR")
    ap.add_argument("--no-text", action="store_true")
    args = ap.parse_args()
    if not shutil.which("pdftotext"):
        sys.exit("pdftotext not found (brew install poppler)")
    pdfs = ps.collect_pdfs(args.paths or ps.statement_folders("uob-"))
    if not pdfs:
        sys.exit("no PDFs found")
    rows, blocks, bad = [], [], []
    for pdf in pdfs:
        text = ps.pdf_to_text(pdf, write=not args.no_text)
        if not detect(text):
            ps.warn(str(pdf), "no transactions parsed")
            continue
        r, bs = parse(text, str(pdf))
        bad += [(str(pdf), *x) for x in validate(r, bs)]
        rows += r
        blocks += bs
    ps.add_keys(rows)
    for b in blocks:
        print(f"  {'OK  ' if b['ok'] else 'BAD '}{b['source']} {b['n_txns']:3d} txn  "
              f"open {b['opening'] or 0:,.2f} -> close {b['closing'] or 0:,.2f} "
              f"(computed {b['computed_closing']:,.2f})")
    for x in bad:
        print(f"  BAD  {x[0]} {x[1]} {x[2]}: computed {x[3]}, printed {x[4]}")
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


# ---------------------------------------------------------------- the layouts (G21)

def _sees(one):
    """The layout test for the One Account (`one`) or the savings accounts:
    a UOB statement that details an account of that product."""
    def sees(text):
        if not detect(text):
            return None
        numbers = tuple(no for product, no in _bars(text) if is_one(product) == one)
        return Seen(numbers=numbers) if numbers else None
    return sees


def name_upload(pdf, read_text, wallet, today):
    """(year folder, file name) for an uploaded statement: its period's month."""
    text = read_text(pdf)
    if not detect(text):
        raise NotThisLayout("This PDF is not a UOB account statement")
    _, blocks = parse(text, Path(pdf).name)
    return by_months([b["period_end"] for b in blocks])


register_bank(Bank("uob", r"\buob\b|united overseas bank", order=93, name="UOB"))

_UOB = dict(parser="uob", module=__spec__.name, kind="bank account", csv="uob", name_file=name_upload,
            bank="uob", status=SAMPLE,
            balance_check="`BALANCE B/F` + deposits - withdrawals = the last `BALANCE C/F` = the overview's "
                          "balance, every running balance and every page's carried balance replayed, and the "
                          "printed `Total`")
register(Layout(
    name="uob-one", order=56, **_UOB,
    label="a UOB One account statement", title="UOB One account",
    folder="uob-one", setup="UOB One account", folder_title="UOB One",
    folder_pattern=r"\bone\b", test=_sees(True),
    detected_by="`Statement of Account` with an `Account Transaction Details` bar naming the One Account"))
register(Layout(
    name="uob-savings", order=57, **_UOB,
    label="a UOB savings account statement", title="UOB savings account",
    folder="uob-savings", setup="UOB savings account, Stash or Uniplus",
    folder_title="UOB Savings", folder_pattern=r"sav|stash|uniplus", test=_sees(False),
    detected_by="the same, naming any other UOB product (Stash, Uniplus)"))


if __name__ == "__main__":
    main()
