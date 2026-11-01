"""A bank that does not exist, as one module, for server/tests/test_layout_registry.py
(G21: a new layout is one module).

It is everything a real parser module under server/finance/parsers holds: the layout
declaration, its layout test, how an upload of it is named, and a command line
the run starts as `python3 -m fake_layout <folder> ... --out DIR`. Its
"statement" is plain invented text in a file named .pdf, so no pdftotext is
needed:

    FAKEBANK STATEMENT OF ACCOUNT 2026-03
    Account FB-0042
    OPENING 100.00
    2026-03-05 COFFEE -4.50
    CLOSING 95.50

The test registers LAYOUT itself (the real modules call register() at import),
so no other test ever sees this bank.
"""
import argparse
import csv
import re
from pathlib import Path

from finance.parsers.layouts import Layout, Seen, by_months
from finance.parsers.parse_statements import BAL_COLS, TXN_COLS, add_keys

HEADING = re.compile(r"^FAKEBANK STATEMENT OF ACCOUNT (\d{4}-\d{2})$", re.M)
ACCOUNT = re.compile(r"^Account (FB-\d+)$", re.M)
ROW = re.compile(r"^(\d{4}-\d{2}-\d{2}) (\S+) (-?\d+\.\d{2})$", re.M)


def sees(text):
    if not HEADING.search(text):
        return None
    m = ACCOUNT.search(text)
    return Seen(numbers=(m.group(1) if m else "",))


def name_upload(pdf, read_text, wallet, today):
    m = HEADING.search(Path(pdf).read_text())
    return by_months([m.group(1) if m else ""])


LAYOUT = Layout(
    name="fakebank", parser="fakebank", module="fake_layout", order=55, kind="bank account",
    label="a Fakebank statement", title="Fakebank account",
    folder="fakebank-savings", setup="Fakebank savings account", csv="fakebank",
    test=sees, name_file=name_upload, new_types=("Fakebank Other",),
    detected_by="`FAKEBANK STATEMENT OF ACCOUNT` on the first line",
    balance_check="opening + in - out = printed closing")


def parse(text, source):
    month = HEADING.search(text).group(1)
    account_no = ACCOUNT.search(text).group(1)
    opening = float(re.search(r"^OPENING (\S+)$", text, re.M).group(1))
    closing = float(re.search(r"^CLOSING (\S+)$", text, re.M).group(1))
    rows = [{"date": d, "account": "Fakebank", "account_no": account_no, "currency": "SGD",
             "bank_txn_type": "Fakebank Other", "direction": "Out" if float(a) < 0 else "In",
             "amount": float(a), "flow_class": "External", "merchant": what, "merchant_country": "",
             "purchase_date": "", "card_last4": "", "fx_original": "", "counterparty": "",
             "reference": "", "memo": "", "balance_after": "", "source": source}
            for d, what, a in ROW.findall(text)]
    add_keys(rows)
    assert round(opening + sum(r["amount"] for r in rows), 2) == closing, "the balance chain drifts"
    block = {"period": f"{account_no} · SGD · {month}", "period_end": f"{month}-31",
             "account": "Fakebank", "account_no": account_no, "currency": "SGD",
             "opening": opening, "closing": closing, "total_in": 0, "total_out": 0,
             "fx_to_sgd": "", "closing_sgd": "", "n_txns": len(rows), "reconciled": True,
             "note": "", "source": source}
    return rows, block


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    rows, blocks = [], []
    for folder in args.paths:
        for pdf in sorted(Path(folder).rglob("*.pdf")):
            r, b = parse(pdf.read_text(), str(pdf.with_suffix("")))
            rows += r
            blocks.append(b)
    out = Path(args.out)
    with (out / "transactions.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=TXN_COLS)
        w.writeheader()
        w.writerows(rows)
    with (out / "period_balances.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=BAL_COLS)
        w.writeheader()
        w.writerows(blocks)
    print(f"{len(blocks)} statement(s) -> {len(rows)} transactions")


if __name__ == "__main__":
    main()
