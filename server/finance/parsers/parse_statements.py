#!/usr/bin/env python3
"""Extract DBS/POSB statement PDFs into transaction rows.

Usage:
    python3 -m finance.parsers.parse_statements [PATH ...] [--out DIR] [--no-text]

PATH may be a PDF or a directory (searched recursively); it defaults to
data/dbs-statements/. Each PDF's text is written beside it as <name>.txt, then
parsed. With --out, transactions.csv and period_balances.csv are written there.

Four statement layouts are recognised, each detected from the text itself
rather than the filename: the DBS consolidated statement (Multiplier), the POSB
deposit-account statement, the PayLah! wallet statement, and the debit-card
statement. Debit-card statements list the same card transactions that the
deposit-account statement carries, so card rows dated inside a month that an
account statement already covers are dropped as duplicates (the count is
printed). Card statements carry no balances; their only check is the printed
GRAND TOTAL.

Two things are worth knowing about the parsing:

  * Column assignment is positional. pdftotext flattens a row to one line, so
    "18.00" carries no meaning on its own - only its horizontal position says
    whether it is a withdrawal, a deposit or the running balance. The header row
    fixes each column's right edge and every number goes to the nearest one. DBS
    shifts those columns between pages, so the header is re-read on every page.

  * Every run replays each account-month's balance chain against the printed
    opening, closing and intermediate balances. A misparse yields plausible rows,
    so this check is the only thing that makes the output trustworthy. The script
    exits non-zero if any chain drifts.
"""
import argparse
import csv
import functools
import hashlib
import json
import re
import shutil
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

# the data folder's layout (server/finance/paths.py) decides where text dumps go
from finance.paths import current_statements, statements_dir, text_dump_path

from .layouts import CHECKED, Bank, Layout, Seen, by_months, register, register_bank

AMT = r"[0-9][0-9,]*\.[0-9]{2}"
MONEY = re.compile(AMT)
MONTHS = {m: i for i, m in enumerate(
    "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(), 1)}
MONTHS_UP = {m.upper() for m in MONTHS}
CCY_CODE = {"SINGAPORE DOLLAR": "SGD", "UNITED STATES DOLLAR": "USD"}

HEADER = re.compile(r"(WITHDRAWAL\(\$\)|Withdrawal \(-\))")
DATE_CON = re.compile(r"^\s*(\d{2}/\d{2}/\d{4})\s")
DATE_POSB = re.compile(r"^\s*(\d{2} [A-Z][a-z]{2})\s")

CON_ACCT = re.compile(r"(?P<name>DBS [A-Za-z ]+Account)\s+Account No\.\s+(?P<no>[\d-]+)")
POSB_ACCT = re.compile(r"Details of Your (?P<name>.+?Account)\s+Account No\.:\s+(?P<no>[\d-]+)")
AS_AT = re.compile(r"[Aa]s at (?P<d>\d{2} [A-Za-z]{3} \d{4})")
CCY_LINE = re.compile(r"CURRENCY:\s+(?P<c>[A-Z ]+?)\s*$")
BF = re.compile(rf"Balance Brought Forward\s+(?:(?P<ccy>[A-Z]{{3}})\s+)?(?P<amt>{AMT})")
CF_CON = re.compile(rf"Total Balance Carried Forward in (?P<ccy>[A-Z]{{3}}):\s+"
                    rf"(?P<w>{AMT})\s+(?P<d>{AMT})\s+(?P<bal>{AMT})")
CF_POSB = re.compile(rf"Balance Carried Forward\s+(?P<amt>{AMT})")
TOTAL_POSB = re.compile(rf"^\s*Total\s+(?P<w>{AMT})\s+(?P<d>{AMT})\s*$")
FX = re.compile(r"Indicative in SGD @ (?P<rate>[0-9.]+)\s+(?P<sgd>[0-9,.]+)")

# PayLah! wallet statement
PL_HEAD = re.compile(r"^\s*(?P<d>\d{2} [A-Z][a-z]{2} \d{4})\s+(?P<mobile>\d{8,})\s+(?P<wallet>\d{16})")
PL_OPEN = re.compile(rf"PREVIOUS BALANCE\s+(?P<amt>{AMT})\s*(?P<sign>CR|DB)?")
PL_CLOSE = re.compile(rf"^\s*Total(?: Balance Carried Forward)?\s*:\s+(?P<amt>{AMT})\s*(?P<sign>CR|DB)?\s*$")
PL_ROW = re.compile(rf"^\s*(?P<d>\d{{2}} [A-Z][a-z]{{2}})\s+(?P<desc>\S.*?)\s+(?P<amt>{AMT})\s+(?P<sign>DB|CR)\s*$")
PL_REF = re.compile(r"REF NO:\.?\s*(?P<ref>\S+)")

# Debit-card statement
CD_ACCT = re.compile(r"for ACCOUNT:\s+(?P<no>[\d-]+)")
CD_CARD = re.compile(r"CARD NO\.:\s+(?P<pan>\d{4}-\d{4}-\d{4}-\d{4})")
CD_ROW = re.compile(rf"^\s*(?P<d>\d{{2}} [A-Z][a-z]{{2}})\s+(?P<desc>\S.*?)\s+\$\s+(?P<amt>{AMT})\s*(?P<cr>CR)?\s*$")
CD_REF = re.compile(r"REF NO\.:\s*(?P<ref>\S+)(?:\s+(?P<ccy>[A-Z]{3})\s+(?P<orig>[0-9,.]+))?")
CD_TOTAL = re.compile(rf"GRAND TOTAL\s+\$\s+(?P<amt>{AMT})\s*(?P<cr>CR)?")
# "GIANT HYPER 123   SINGAPORE   SGP 15 MAY" -> merchant+city, country slot, purchase date
CD_MERCHANT = re.compile(r"^(?P<name>.+?)\s+(?P<cc>\S{2,3})\s+(?P<d>\d{2} [A-Z]{3})$")

# the country slot on card statements is a 3-letter code on some, a 2-letter
# code on others, a US state for US merchants, and "065" (the IDD code) for some
# Singapore merchants. Normalise to ISO alpha-3 so one select value covers each.
ALPHA2 = {"SG": "SGP", "MY": "MYS", "GB": "GBR", "IE": "IRL", "UA": "UKR", "CZ": "CZE",
          "SE": "SWE", "US": "USA", "VN": "VNM", "ID": "IDN", "TH": "THA", "JP": "JPN",
          "AU": "AUS", "DE": "DEU", "FR": "FRA", "NL": "NLD", "PT": "PRT", "ES": "ESP",
          "IT": "ITA", "HK": "HKG", "KR": "KOR", "TW": "TWN", "CN": "CHN", "IN": "IND",
          "PH": "PHL", "CH": "CHE", "AT": "AUT", "BE": "BEL", "DK": "DNK", "NO": "NOR",
          "FI": "FIN", "PL": "POL", "NZ": "NZL", "AE": "ARE", "TR": "TUR", "GR": "GRC",
          "HU": "HUN", "LU": "LUX", "EE": "EST", "LV": "LVA", "LT": "LTU", "KH": "KHM"}
NOT_COUNTRY = {"LTD", "PTE", "INC", "LLC", "CO", "BHD", "SDN", "GMBH", "AB", "AG"}
US_STATES = set("AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO "
                "MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC".split())

NOISE = re.compile(
    r"Page \d+ of \d+|SG40020000876|DBS Co\. Reg|PDS_|CMTHE_|Message|"
    r"For Your Information|DEPOSIT INSURANCE|Transaction Details|Account Summary|"
    r"^\s*(Date|DATE)\s+(Description|DETAILS)|CURRENCY:|Indicative in SGD")

CARD_PAN = re.compile(r"^\d{4}-\d{4}-\d{4}-\d{4}")
CARD_FX = re.compile(r"^(\d{4}-\d{4}-\d{4}-\d{4})\s+([A-Z]{3})([0-9.,]+)$")
# "GRAB* A-1A2B3C4D5E6F SI SGP 28APR" -> merchant, country, purchase date
MERCHANT = re.compile(r"^(?P<name>.+?)\s+(?P<cc>[A-Z]{3})\s+(?P<d>\d{2}[A-Z]{3})$")

# The two shapes a Revolut load prints on the bank side, and nothing else says a
# row is one. A card top-up prints the acquirer string "REVOLUT**NNNN*", where
# the digits are the acquirer's own id and have changed more than once, so the
# pattern takes any run of them. A PayNow load prints the counterparty "REVOLUT TECHNOLOGIES SINGAPORE".
# The bare word on its own is not enough: a person-to-person memo saying
# "revolut" is somebody's note about the app, not money leaving for the wallet,
# and it stays External. Same tightness as the YouTrip test below it.
REVOLUT_LOAD = re.compile(r"REVOLUT\*\*\d+\*|REVOLUT TECHNOLOGIES")

# DBS prefixes some consolidated-statement rows with "Advice"; same underlying type
TYPE_MAP = {
    "Advice Funds Transfer": "Funds Transfer",
    "Advice FAST Payment / Receipt": "FAST Payment / Receipt",
    "Advice Inward Telegraphic Transfer": "Inward Telegraphic Transfer",
    "Advice Inward Telegraphic Transfer Comm &": "Inward Telegraphic Transfer",
    # a bare "Advice" is the bank posting something by advice only: a remittance,
    # a fee, a foreign-currency card row. It is not an inward transfer, so the
    # word is kept as printed rather than guessed into a type
    "Advice": "Advice",
}

# A card statement prints only "for ACCOUNT: <no>", never the account's name, so
# the display name is taken from whichever account statement in the same run
# reports the same number. This seed keeps a card-only run working when there is
# no account statement to learn the name from.
ACCOUNT_NAMES = {
    "130-24680-5": "POSB Everyday Savings Account",
    "120-345678-9": "DBS Multiplier Account",
}

# How a block's closing balance was checked. "card" and "empty" are the two
# layouts that legitimately print no closing; "missing" is a block whose closing
# simply is not there, which is a failure rather than a silent pass.
BALANCE_STATES = {
    "checked": "",
    "card": "card statement, no closing balance printed by this layout",
    "empty": "no transactions; no closing balance printed by this layout",
    "missing": "no closing balance printed; the balance chain could not be checked",
}

WARNINGS = []


def warn(source, msg):
    """Every advisory goes to stderr, prefixed and named, so the sync wrapper
    can surface it. The count lands in the final summary line."""
    line = f"warning: {source}: {msg}" if source else f"warning: {msg}"
    WARNINGS.append(line)
    print(line, file=sys.stderr)


def money(s):
    return float(s.replace(",", "")) if s else None


def col_edges(ln):
    """Right-edge x of the withdrawal / deposit / balance columns in a header row."""
    keys = (["WITHDRAWAL($)", "DEPOSIT($)", "BALANCE($)"] if "WITHDRAWAL($)" in ln
            else ["Withdrawal (-)", "Deposit (+)", "Balance"])
    return [ln.find(k) + len(k) for k in keys]


def split_amounts(ln, edges):
    """Return (withdrawal, deposit, balance) by nearest column right-edge."""
    out = [None, None, None]
    for m in MONEY.finditer(ln):
        if m.start() < min(edges) - 25:      # still inside the description column
            continue
        i = min(range(3), key=lambda k: abs(m.end() - edges[k]))
        out[i] = money(m.group())
    return out


# ---------------------------------------------------------------- extraction

def pdf_to_text(pdf, write=True):
    """Return the layout-preserved text of a PDF, saving a dump of it: under
    cache/text/ for a statement in the data folder, beside the PDF otherwise."""
    txt = text_dump_path(pdf)
    out = subprocess.run(["pdftotext", "-layout", str(pdf), "-"],
                         capture_output=True, text=True, check=True).stdout
    if write:
        txt.write_text(out)
    return out


def detect_format(lines):
    head = "\n".join(lines[:4])
    if "PayLah!" in head:
        return "paylah"
    if "Debit Cards" in head:
        return "card"
    return "account"


def parse(text, source):
    lines = text.splitlines()
    fmt = detect_format(lines)
    if fmt == "paylah":
        return parse_paylah(lines, source)
    if fmt == "card":
        return parse_card(lines, source)
    return parse_account(lines, source)


def parse_paylah(lines, source):
    """PayLah! wallet statement: one SGD block, rows carry DB/CR instead of columns."""
    stmt_date = wallet = None
    rows, block, cur = [], None, None
    for ln in lines:
        if m := PL_HEAD.match(ln):
            stmt_date, wallet = m.group("d"), m.group("wallet")
            continue
        if (m := PL_OPEN.search(ln)) and block is None:
            amt = money(m.group("amt")) * (-1 if m.group("sign") == "DB" else 1)
            block = dict(source=source, account="DBS PayLah! wallet", account_no=wallet,
                         statement_date=stmt_date, currency="SGD", opening=amt)
            continue
        if (m := PL_CLOSE.match(ln)) and block is not None:
            block["closing"] = money(m.group("amt")) * (-1 if m.group("sign") == "DB" else 1)
            cur = None
            continue
        if m := PL_ROW.match(ln):
            amt = money(m.group("amt"))
            cur = dict(source=source, account="DBS PayLah! wallet", account_no=wallet,
                       statement_date=stmt_date, currency="SGD", date_raw=m.group("d"),
                       txn_type=re.sub(r"\s{2,}", " ", m.group("desc")).strip(),
                       withdrawal=amt if m.group("sign") == "DB" else None,
                       deposit=amt if m.group("sign") == "CR" else None,
                       balance_after=None, detail=[])
            rows.append(cur)
            continue
        if cur is not None and (m := PL_REF.search(ln)):
            cur["detail"].append(m.group("ref"))
            cur = None
    return rows, [block] if block else []


def parse_card(lines, source):
    """Debit-card statement: card rows only, no balances, a printed GRAND TOTAL."""
    stmt_date = acct_no = card = None
    rows, cur = [], None
    block = dict(source=source, account=None, account_no=None,
                 statement_date=None, currency="SGD", opening=None, closing=None,
                 card_statement=True)
    for ln in lines:
        if m := AS_AT.search(ln):
            stmt_date = stmt_date or m.group("d")
            block["statement_date"] = stmt_date
        if m := CD_ACCT.search(ln):
            block["account_no"] = acct_no = m.group("no")
            block["account"] = ACCOUNT_NAMES.get(acct_no)
        if m := CD_CARD.search(ln):
            card = m.group("pan")[-4:]
            continue
        if m := CD_TOTAL.search(ln):
            block["grand_total"] = money(m.group("amt")) * (-1 if m.group("cr") else 1)
            continue
        if m := CD_ROW.match(ln):
            amt = money(m.group("amt"))
            cur = dict(source=source, account=block["account"], account_no=acct_no,
                       statement_date=stmt_date, currency="SGD", date_raw=m.group("d"),
                       txn_type="Debit Card Transaction",
                       withdrawal=None if m.group("cr") else amt,
                       deposit=amt if m.group("cr") else None,
                       balance_after=None, card_last4=card,
                       detail=[re.sub(r"\s{2,}", " ", m.group("desc")).strip()])
            rows.append(cur)
            continue
        if cur is not None and (m := CD_REF.search(ln)):
            cur["detail"].append(m.group("ref"))
            if m.group("ccy"):
                cur["fx_original"] = f'{m.group("ccy")} {m.group("orig")}'
            cur = None
    for r in rows:
        r["account_no"] = acct_no
        r["account"] = block["account"]
    return rows, [block]


def resolve_card_accounts(rows, blocks):
    """Give every card row and block the display name that the account
    statements in this run use for the same account number."""
    learnt = {b["account_no"]: b["account"] for b in blocks
              if not b.get("card_statement") and b["account_no"] and b["account"]}
    unknown = []
    for x in rows + blocks:
        if not (x.get("card_statement") or x.get("statement_format") == "card"):
            continue
        name = learnt.get(x["account_no"]) or ACCOUNT_NAMES.get(x["account_no"])
        if name:
            x["account"] = name
        elif x.get("card_statement"):
            unknown.append((x["source"], x["account_no"]))
    return unknown


def parse_account(lines, source):
    edges = None
    # detect the format from the statement itself, not the filename: only the
    # consolidated layout carries the multi-currency column header
    consolidated = any("Withdrawal (-)" in ln for ln in lines)
    datere = DATE_CON if consolidated else DATE_POSB
    acctre = CON_ACCT if consolidated else POSB_ACCT

    stmt_date = acct_name = acct_no = None
    ccy = "SGD"
    rows, blocks, cur, block = [], [], None, None
    seen_blocks = {}

    for ln in lines:
        if HEADER.search(ln):
            edges = col_edges(ln)
            cur = None
            continue
        if m := AS_AT.search(ln):
            stmt_date = stmt_date or m.group("d")
        if m := acctre.search(ln):
            acct_name, acct_no = m.group("name").strip(), m.group("no")
        if m := CCY_LINE.search(ln):
            ccy = CCY_CODE.get(m.group("c").strip(), m.group("c").strip())
            cur = None
            continue
        if m := BF.search(ln):
            # every page repeats a brought-forward line; only the first one per
            # account+currency is the statement's true opening balance
            key = (acct_no, m.group("ccy") or ccy)
            if key not in seen_blocks:
                block = dict(source=source, account=acct_name, account_no=acct_no,
                             statement_date=stmt_date, currency=m.group("ccy") or ccy,
                             opening=money(m.group("amt")))
                blocks.append(block)
                seen_blocks[key] = block
            block = seen_blocks[key]
            cur = None
            continue
        if m := CF_CON.search(ln):
            block.update(closing=money(m.group("bal")), total_out=money(m.group("w")),
                         total_in=money(m.group("d")))
            cur = None
            continue
        if m := TOTAL_POSB.search(ln):
            block.update(total_out=money(m.group("w")), total_in=money(m.group("d")))
            cur = None
            continue
        if (m := CF_POSB.search(ln)) and not consolidated:
            block["closing"] = money(m.group("amt"))
            cur = None
            continue
        if m := FX.search(ln):
            block.update(fx_to_sgd=float(m.group("rate")),
                         closing_sgd=money(m.group("sgd")))
            continue

        if (m := datere.match(ln)) and edges:
            w, d, b = split_amounts(ln, edges)
            desc = MONEY.sub("", ln[m.end():].strip()).strip()
            cur = dict(source=source, account=acct_name, account_no=acct_no,
                       statement_date=stmt_date, currency=ccy, date_raw=m.group(1),
                       txn_type=desc, withdrawal=w, deposit=d, balance_after=b,
                       detail=[])
            rows.append(cur)
            continue

        if cur is not None and ln.strip() and not NOISE.search(ln):
            body = ln.strip()
            if re.fullmatch(r"\d{3,4}", body):        # stray page artefact
                continue
            if body.startswith("Balance Carried Forward") or acctre.search(ln):
                cur = None                              # page furniture, not detail
                continue
            cur["detail"].append(body)
        # a blank line does not close a transaction: a page break can fall
        # between the dated line and its detail, or between two detail lines,
        # with four blank lines and the page footer in between. Only the next
        # dated line, a column header, a balance line or an account header
        # closes it, and the footer itself is NOISE

    # rows with no movement at all are page artefacts, not transactions
    rows = [r for r in rows if r["txn_type"]
            and (r["withdrawal"] is not None or r["deposit"] is not None)]
    return rows, blocks


# ---------------------------------------------------------------- enrichment

def split_merchant(raw):
    """Split a card merchant string into (name, country, purchase-date tag)."""
    if not raw:
        return None, None, None
    m = MERCHANT.match(raw)
    if not m:
        return raw, None, None
    # a trailing 3-letter month is part of the merchant name, not a country
    # ("DBS VISA DEBIT CASHBACK DEC 23JAN" -> name keeps DEC, no country, still 23JAN)
    if m.group("cc") in MONTHS_UP or m.group("cc") in NOT_COUNTRY:
        return f'{m.group("name")} {m.group("cc")}'.strip(), None, m.group("d")
    return m.group("name").strip(), m.group("cc"), m.group("d")


PL_TYPES = [                       # description prefix -> Bank txn type
    (re.compile(r"^PAYNOW\b"), "PayNow"),
    (re.compile(r"^SEND MONEY TO\b"), "PayLah Send Money"),
    # "FAST TRANSFER TO MY WALLET" is money someone else sent by FAST: it never
    # matches a top-up on the bank side, so it is a receipt, not an own transfer
    (re.compile(r"^(RECEIVE (MONEY FROM|EGIFT)|FAST TRANSFER TO MY WALLET)\b"), "PayLah Receive Money"),
    (re.compile(r"^TOP UP WALLET FROM MY ACCOUNT"), "PayLah Top-up"),
    (re.compile(r"^CASHBACK\b"), "Cashback"),
]


def paylah_type(desc):
    for rx, t in PL_TYPES:
        if rx.search(desc):
            return t
    return "PayLah Payment"                       # QR / merchant payment


def enrich_paylah(r):
    """Split a PayLah description into counterparty, merchant and memo."""
    desc = r["txn_type"]
    r["bank_txn_type"] = t = paylah_type(desc)
    r["reference"] = r["detail"][0] if r["detail"] else None
    if t == "PayNow":
        body = re.sub(r"^PAYNOW (TO )?", "", desc)
        if "..." in body:                          # "ALI'S PRAT... 201912345A01ABC0001"
            name, _, rest = body.partition("...")
            r["counterparty"], rest = name.strip() + "...", rest.strip()
        else:
            parts = body.rsplit(None, 1)
            if len(parts) == 2 and re.search(r"\d", parts[1]) and len(parts[1]) >= 6:
                r["counterparty"], rest = parts[0].strip(), parts[1]
            else:
                r["counterparty"], rest = body.strip(), ""
        rest = re.sub(r"^PAYNOW TRANSFER$", "", rest).strip()
        if rest:
            r["memo"] = rest
    elif t in ("PayLah Send Money", "PayLah Receive Money"):
        m = re.search(r"(\d{8,})$", desc)
        r["counterparty"] = m.group(1) if m else None
        if not m:
            r["memo"] = desc
    elif t == "PayLah Top-up":
        pass
    elif t == "Cashback":
        r["memo"] = desc
    else:
        r["merchant"] = desc
    return r


def enrich_card(r):
    """Card-statement rows: merchant, country slot and purchase date on one line."""
    r["merchant"] = r["detail"][0] if r["detail"] else None
    r["reference"] = r["detail"][1] if len(r["detail"]) > 1 else None
    r["bank_txn_type"] = r["txn_type"]
    if not r["merchant"]:
        return r
    m = CD_MERCHANT.match(r["merchant"])
    if not m:
        return r
    name, cc, tag = m.group("name").strip(), m.group("cc"), m.group("d").replace(" ", "")
    if cc in MONTHS_UP or cc in NOT_COUNTRY:      # "DBS VISA DEBIT CASHBACK AUG 25 OCT"
        name, cc = f"{name} {cc}", None
    elif cc.isdigit():                             # "SINGAPORE 065"
        cc = "SGP" if "SINGAPORE" in name else None
    elif len(cc) == 2:
        cc = "USA" if cc in US_STATES else ALPHA2.get(cc, cc)
    r["merchant"], r["merchant_country"] = name, cc
    r["purchase_date"] = purchase_date(r["date"], tag)
    return r


def enrich(r):
    """Pull structured fields out of the free-text detail block."""
    d = r["detail"]
    r.setdefault("card_last4", None)
    r.setdefault("fx_original", None)
    r["merchant"] = r["counterparty"] = r["reference"] = r["memo"] = None
    r["merchant_country"] = r["purchase_date"] = None

    if r.get("statement_format") == "paylah":
        return enrich_paylah(r)
    if r.get("statement_format") == "card":
        return enrich_card(r)

    if r["txn_type"] == "Debit Card Transaction" and d:
        r["merchant"] = re.sub(r"\s{2,}", " ", d[0]).strip()
        for ln in d[1:]:
            if m := CARD_FX.match(ln):
                r["card_last4"] = m.group(1)[-4:]
                r["fx_original"] = f"{m.group(2)} {m.group(3)}"
            elif CARD_PAN.match(ln):
                r["card_last4"] = ln[:19][-4:]
    else:
        for ln in d:
            if m := re.match(r"(?:To|From):\s*(.+)", ln, re.I):
                r["counterparty"] = m.group(1).strip()
            elif re.fullmatch(r"[A-Z0-9]{10,}|FT\d+\w*|\d{3}-\d{5}-\d:IB|"
                              r"(?:Incoming )?PayNow (?:Transfer|Ref) \d+|"
                              r"PAYNOW TRANSFER \d+", ln.strip()):
                r["reference"] = ((r["reference"] + " ") if r["reference"] else "") + ln.strip()
            elif ln.strip() not in ("Other", "PAYNOW TRANSFER", "Transfer - Mobile"):
                r["memo"] = ((r["memo"] + " | ") if r["memo"] else "") + ln.strip()

    name, cc, tag = split_merchant(r["merchant"])
    r["merchant"], r["merchant_country"] = name, cc
    r["purchase_date"] = purchase_date(r["date"], tag)
    return r


def iso_date(raw, statement_date):
    """A dd/mm/yyyy row carries its own year and is taken at face value; the POSB
    "28 Dec" form has none, so a month later than the statement's rolls back a
    year (a row cannot post after the statement that prints it)."""
    yr = int(statement_date.split()[-1])
    if "/" in raw:
        d, m, y = raw.split("/")
        return f"{int(y):04d}-{m}-{d}"
    dd, mon = raw.split()
    mm = MONTHS[mon]
    if mm > MONTHS[statement_date.split()[1]]:   # a row cannot post after its statement
        yr -= 1
    return f"{yr:04d}-{mm:02d}-{int(dd):02d}"


def purchase_date(txn_date, tag):
    """Card rows print the purchase date as '29MAY', separate from the posting date."""
    if not tag:
        return None
    dd, mm = int(tag[:2]), MONTHS[tag[2:].capitalize()]
    yr = int(txn_date[:4])
    if mm == 12 and int(txn_date[5:7]) == 1:
        yr -= 1
    return f"{yr:04d}-{mm:02d}-{dd:02d}"


def direction(amount):
    """A zero-amount row moves money neither way, so it gets no direction."""
    if amount == 0:
        return ""
    return "In" if amount > 0 else "Out"


def classify_flow(r, own_accounts):
    """External money, a move between the owner's accounts, or a PayLah / YouTrip / Revolut top-up."""
    if r.get("statement_format") == "paylah":
        return "Internal - PayLah" if r["bank_txn_type"] == "PayLah Top-up" else "External"
    blob = " ".join(r["detail"]).upper()
    if "PAYLAH" in blob:
        return "Internal - PayLah"
    if "YOU TECHNOLOGIES" in blob or "M9496" in blob:   # YouTrip loads: PayNow or FAST Collection
        return "Internal - YouTrip"
    if REVOLUT_LOAD.search(blob):                      # Revolut loads: card top-ups or PayNow
        return "Internal - Revolut"
    if any(a in blob for a in own_accounts):
        return "Internal - own account"
    return "External"


def add_keys(rows):
    """Stable per-row key so a re-import updates rather than duplicates."""
    seen = defaultdict(int)
    for r in rows:
        base = "|".join(str(x) for x in (
            r["date"], r["account_no"], r["currency"], r["amount"],
            r["reference"] or "", r["merchant"] or r["counterparty"] or "",
            r["bank_txn_type"]))
        seen[base] += 1
        r["txn_key"] = hashlib.sha1(f"{base}#{seen[base]}".encode()).hexdigest()[:12]


# ---------------------------------------------------------------- validation

def validate(rows, blocks):
    """Replay each block's balance chain; return the drifts found. A block
    with a drift is not ok even when the replay, resynced onto each printed
    balance, still reaches the closing: its `reconciled` column is what a
    reused CSV is checked by (sync.check_blocks), and a row the chain
    disagreed with must never load."""
    bad = []
    by_block = defaultdict(list)
    for r in rows:
        by_block[(r["source"], r["account_no"], r["currency"])].append(r)
    for b in blocks:
        b.setdefault("closing", None)   # a layout that printed none still has the key
        rs = by_block.get((b["source"], b["account_no"], b["currency"]), [])
        run, tin, tout = (b["opening"] if b["opening"] is not None else 0.0), 0.0, 0.0
        drifts = len(bad)
        for r in rs:
            run += r["amount"]
            tin += max(r["amount"], 0)
            tout += max(-r["amount"], 0)
            if r["balance_after"] is not None and abs(run - r["balance_after"]) > 0.005:
                bad.append((b["source"], b["currency"], r["date"], r["bank_txn_type"],
                            round(run, 2), r["balance_after"]))
                run = r["balance_after"]        # resync so one error is not N errors
        drifted = len(bad) > drifts
        b.update(computed_closing=round(run, 2) if b["opening"] is not None else None,
                 computed_in=round(tin, 2), computed_out=round(tout, 2), n_txns=len(rs))
        if b.get("card_statement"):
            b["balance_state"] = "card"
            gt = b.get("grand_total")
            b["ok"] = gt is not None and abs((tout - tin) - gt) < 0.005
            b["note"] = (f"card statement, no balances; GRAND TOTAL {gt:,.2f} "
                         f"{'matches' if b['ok'] else 'DOES NOT MATCH'} the rows")
            b["total_in"], b["total_out"] = round(tin, 2), round(tout, 2)
        else:
            if b.get("empty_statement"):
                # a layout that states it has nothing to print: no rows, no
                # balances, and that is the whole of the check
                b["balance_state"] = "empty"
                b["ok"] = not rs and b.get("closing") is None and b["opening"] is None
            elif b.get("closing") is None:
                # not a recognised balance-less layout, so this is a failure and
                # never a silent pass
                b["balance_state"] = "missing"
                b["ok"] = False
            else:
                b["balance_state"] = "checked"
                b["ok"] = abs(run - b["closing"]) < 0.005 and not drifted
            b.setdefault("total_in", round(tin, 2))
            b.setdefault("total_out", round(tout, 2))
            b.setdefault("note", BALANCE_STATES[b["balance_state"]])
    return bad


def duplicate_periods(blocks, name_of=None):
    """Two files that parse to the same account, currency and period month.
    Loading both double-counts the month, so a run that hits one must stop."""
    name_of = name_of or period_name
    by_name = defaultdict(list)
    for b in blocks:
        if b.get("statement_date") or b.get("period_end"):
            by_name[name_of(b)].append(b["source"])
    return sorted((name, sorted(set(srcs))) for name, srcs in by_name.items()
                  if len(set(srcs)) > 1)


def drop_card_duplicates(rows, blocks):
    """Card rows inside a month an account statement covers are already loaded
    through that statement; keep only the months with no account statement."""
    covered = set()
    for r in rows:
        if r.get("statement_format") == "account":
            covered.add((r["account_no"], r["date"][:7]))
    for b in blocks:
        if not b.get("card_statement") and b["account_no"]:
            d, mon, yr = b["statement_date"].split()
            covered.add((b["account_no"], f"{yr}-{MONTHS[mon]:02d}"))
    kept, dropped = [], Counter()
    by_src = {b["source"]: b for b in blocks if b.get("card_statement")}
    for r in rows:
        if r.get("statement_format") == "card" and (r["account_no"], r["date"][:7]) in covered:
            dropped[r["source"]] += 1
            b = by_src[r["source"]]         # block totals describe the rows that are kept
            if r["amount"] < 0:
                b["total_out"] = round(b["total_out"] + r["amount"], 2)
            else:
                b["total_in"] = round(b["total_in"] - r["amount"], 2)
        else:
            kept.append(r)
    return kept, dropped


# ---------------------------------------------------------------- entry point

TXN_COLS = ["txn_key", "date", "account", "account_no", "currency", "bank_txn_type",
            "direction", "amount", "flow_class", "merchant", "merchant_country",
            "purchase_date", "card_last4", "fx_original", "counterparty",
            "reference", "memo", "balance_after", "source"]

BAL_COLS = ["period", "period_end", "account", "account_no", "currency", "opening",
            "closing", "total_in", "total_out", "fx_to_sgd", "closing_sgd", "n_txns",
            "reconciled", "note", "source"]


def period_end(b):
    d, mon, yr = b["statement_date"].split()
    return f"{yr}-{MONTHS[mon]:02d}-{d}"


def period_name(b):
    """Notion Period row title: '<account no> · <ccy> · <yyyy-mm>[ · card]'.
    A card statement closes on the 9th, so it is labelled by the month it mostly
    covers, which is the one before its as-at date. That matches the file names."""
    d, mon, yr = b["statement_date"].split()
    yr, mm = int(yr), MONTHS[mon]
    if b.get("card_statement"):
        yr, mm = (yr - 1, 12) if mm == 1 else (yr, mm - 1)
        return f"{b['account_no']} · {b['currency']} · {yr:04d}-{mm:02d} · card"
    return f"{b['account_no']} · {b['currency']} · {yr:04d}-{mm:02d}"


def parse_all(sources):
    """Parse every (source, text) pair, tagging each row and block with the
    layout it came from and giving the card rows their account's name."""
    rows, blocks = [], []
    for source, text in sources:
        fmt = detect_format(text.splitlines())
        r, b = parse(text, source)
        if not r:
            warn(source, "no transactions parsed")
        for x in r + b:
            x["statement_format"] = fmt
        rows += r
        blocks += b
    for src, no in resolve_card_accounts(rows, blocks):
        warn(src, f"card statement is for account {no}, which no statement in this "
                  f"run names; the account column is left empty")
    return rows, blocks


def report_duplicate_periods(blocks, name_of=None):
    """Stop the run when two files cover the same account-month."""
    dups = duplicate_periods(blocks, name_of)
    if not dups:
        return
    for name, srcs in dups:
        print(f"error: {len(srcs)} files parse to the same period {name}:",
              file=sys.stderr)
        for s in srcs:
            print(f"    {s}", file=sys.stderr)
    sys.exit(f"{len(dups)} duplicate period(s); remove the extra file(s) "
             f"and run again")


def enrich_all(rows, blocks):
    """Date, amount, direction, structured fields and flow class, in row order."""
    # an account is "ours" if some statement in this run reports it
    own_accounts = {b["account_no"] for b in blocks if b["account_no"]}
    for r in rows:
        r["date"] = iso_date(r["date_raw"], r["statement_date"])
        r["bank_txn_type"] = TYPE_MAP.get(r["txn_type"], r["txn_type"])
        r["amount"] = round((r["deposit"] or 0) - (r["withdrawal"] or 0), 2)
        r["direction"] = direction(r["amount"])
        enrich(r)                      # may refine bank_txn_type (PayLah)
        r["flow_class"] = classify_flow(r, own_accounts)
    rows.sort(key=lambda r: (r["date"], r["account_no"], r["currency"]))
    return rows


def finish(rows, blocks):
    """Replay every chain, drop the card rows an account statement already
    carries, and key what is left. Returns (rows, blocks to write, dropped, drifts)."""
    bad = validate(rows, blocks)       # check every statement in full first
    rows, dropped = drop_card_duplicates(rows, blocks)
    for b in blocks:
        if b.get("card_statement"):
            b["n_txns"] -= dropped.get(b["source"], 0)
            if dropped.get(b["source"]):
                b["note"] += (f"; {dropped[b['source']]} rows dropped as duplicates of "
                              f"the account statement")
            b["skipped"] = b["n_txns"] == 0
    blocks_out = [b for b in blocks if not b.get("skipped")]
    add_keys(rows)
    return rows, blocks_out, dropped, bad


def balance_summary(blocks):
    """The tail of the final line: how many blocks printed no closing balance,
    split into the layouts that never do and the ones that should have."""
    n_layout = sum(1 for b in blocks if b.get("balance_state") in ("card", "empty"))
    n_missing = sum(1 for b in blocks if b.get("balance_state") == "missing")
    return (f"{n_layout} block(s) print no closing balance by layout, "
            f"{n_missing} unchecked; {len(WARNINGS)} warning(s)")


def statement_folders(*prefixes):
    """The data folder's statement folders whose names start with one of
    `prefixes`: what a parser reads when it is given no path."""
    d = statements_dir()
    if not d.is_dir():
        return []
    return sorted(str(p) for p in d.iterdir() if p.is_dir() and p.name.startswith(prefixes))


def collect_pdfs(paths):
    pdfs = []
    for p in paths:
        p = Path(p)
        if p.is_dir():
            pdfs += current_statements(p.rglob("*.pdf"))
        elif p.suffix.lower() == ".pdf":
            pdfs.append(p)
        else:
            sys.exit(f"not a PDF or directory: {p}")
    return sorted(set(pdfs))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="*", default=None,
                    help="PDFs or directories to parse (default: the dbs-* and posb-* folders in the data folder)")
    ap.add_argument("--out", metavar="DIR",
                    help="also write transactions.csv and period_balances.csv here")
    ap.add_argument("--no-text", action="store_true",
                    help="do not save the .txt dump beside each PDF")
    args = ap.parse_args()

    if not shutil.which("pdftotext"):
        sys.exit("pdftotext not found (brew install poppler)")

    pdfs = collect_pdfs(args.paths or statement_folders("dbs-", "posb-"))
    if not pdfs:
        sys.exit("no PDFs found")

    sources = [(str(pdf), pdf_to_text(pdf, write=not args.no_text)) for pdf in pdfs]
    rows, blocks = parse_all(sources)
    report_duplicate_periods(blocks)
    rows = enrich_all(rows, blocks)
    rows, blocks_out, dropped, bad = finish(rows, blocks)

    print(f"{len(pdfs)} PDFs -> {len(rows)} transactions, "
          f"{len(blocks_out)} account-month blocks"
          + (f" ({len(blocks) - len(blocks_out)} card statements skipped, fully "
               f"covered by account statements)" if len(blocks) != len(blocks_out) else "")
          + "\n")
    fmt_num = lambda x: f"{x:>10,.2f}" if x is not None else f"{'-':>10}"
    for b in sorted(blocks, key=lambda b: (b["source"], b["currency"])):
        flag = "SKIP" if b.get("skipped") else ("OK  " if b["ok"] else "BAD ")
        print(f"  {flag}{Path(b['source']).parent}/"
              f"{Path(b['source']).stem:<3} {b['currency']} {b['n_txns']:3d} txn"
              f"  open {fmt_num(b['opening'])} -> close {fmt_num(b.get('closing'))}"
              f"  (computed {fmt_num(b['computed_closing'])})"
              + (f"  {b['note']}" if b.get("note") else ""))

    for b in blocks_out:
        if b["balance_state"] == "missing":
            warn(b["source"], BALANCE_STATES["missing"])
    for x in bad:
        warn(x[0], f"mid-chain balance mismatch: {x[1]} {x[2]} {x[3]} "
                   f"computed {x[4]:,.2f} against printed {x[5]:,.2f}")

    print("\nFlow class x direction (rows and gross are both per currency):")
    tally = Counter((r["flow_class"], r["direction"], r["currency"]) for r in rows)
    for (f, d, c), n in sorted(tally.items()):
        gross = sum(abs(r["amount"]) for r in rows
                    if (r["flow_class"], r["direction"], r["currency"]) == (f, d, c))
        print(f"  {f:24s} {d:3s} {c}  {n:4d} rows  {gross:>10,.2f}")
    print("  Filter to External for real spending and income.")

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
            for b in blocks_out:
                w.writerow({**b, "period_end": period_end(b), "period": period_name(b),
                            "reconciled": b["ok"]})
        print(f"\nwrote {out/'transactions.csv'} and {out/'period_balances.csv'}")

    failed = [b for b in blocks if not b["ok"]]
    tail = balance_summary(blocks_out)
    if failed or bad:
        sys.exit(f"\n{len(failed)} block(s) and {len(bad)} row(s) failed the "
                 f"balance check; {tail}")
    print(f"\nEvery balance chain reconciles; {tail}")


# ---------------------------------------------------------------- the four layouts (G21)

# parse() picks one layout for a whole file from the top of it. The layout
# tests below take each layout on its own, page by page, so a file that holds
# two of them reads as two and is the owner's to place, rather than being filed
# as the first and losing the second's rows.

def _pages(text, fmt):
    return [ln for page in text.split("\f") if detect_format(page.splitlines()) == fmt
            for ln in page.splitlines()]


def _sees_paylah(text):
    _, blocks = parse_paylah(_pages(text, "paylah"), "upload")
    if blocks and blocks[0].get("statement_date") and blocks[0].get("account_no"):
        return Seen(numbers=(blocks[0]["account_no"],))
    return None


def _sees_card(text):
    lines = _pages(text, "card")
    if not lines:
        return None
    _, blocks = parse_card(lines, "upload")
    b = blocks[0] if blocks else {}
    if b.get("statement_date") and b.get("account_no"):
        # a card statement prints the number of the account its cards draw on
        return Seen(numbers=(b["account_no"],))
    return None


@functools.lru_cache(maxsize=4)
def _account_layout(text):
    """(layout, numbers) for the two account layouts, read once for both of
    their tests. Both must show their own column header, an account line and
    a brought-forward balance."""
    lines = _pages(text, "account")
    if not any(HEADER.search(ln) for ln in lines):
        return None
    _, blocks = parse_account(lines, "upload")
    blocks = [b for b in blocks if b.get("account_no") and b.get("statement_date")]
    if not blocks:
        return None
    layout = "consolidated" if any("Withdrawal (-)" in ln for ln in lines) else "posb"
    return layout, tuple(b["account_no"] for b in blocks)


def _sees_account(layout):
    def test(text):
        found = _account_layout(text)
        return Seen(numbers=found[1]) if found and found[0] == layout else None
    return test


def name_upload(pdf, read_text, wallet, today):
    """(year folder, file name) for an uploaded statement, by the period names
    its blocks file under (a card statement is the month before its "As at"
    date)."""
    _, blocks = parse(read_text(pdf), Path(pdf).name)
    return by_months([period_name(b) for b in blocks])


# __spec__.name rather than __name__: under `python3 -m` this module is __main__
_DBS = {"parser": "dbs", "module": __spec__.name, "name_file": name_upload, "csv": "", "status": CHECKED}

# PayLah! and POSB are DBS products with marks of their own, so their words are tried before DBS's
register_bank(Bank("paylah", r"paylah", order=10, name="DBS PayLah!"))
register_bank(Bank("posb", r"\bposb\b", order=20, name="POSB"))
register_bank(Bank("dbs", r"\bdbs\b", order=30, name="DBS"))

register(Layout(
    name="consolidated", order=10, kind="bank account", **_DBS,
    label="a DBS consolidated statement", title="DBS consolidated",
    folder="dbs-multiplier", setup="DBS Multiplier account", bank="dbs", folder_title="DBS Multiplier",
    test=_sees_account("consolidated"),
    detected_by="the `Withdrawal (-)` column header",
    balance_check="opening + in - out = printed closing, per currency"))
register(Layout(
    name="posb", order=20, kind="bank account", **_DBS,
    label="a POSB account statement", title="POSB deposit account",
    folder="posb-everyday", setup="POSB Everyday Savings account", bank="posb", folder_title="POSB Everyday",
    folder_pattern=r"^posb",
    test=_sees_account("posb"),
    detected_by="an account statement with no `Withdrawal (-)` header; its columns come off `WITHDRAWAL($)`",
    balance_check="the same, against the printed `Balance Carried Forward`"))
register(Layout(
    name="paylah", order=30, kind="wallet", **_DBS,
    label="a DBS PayLah! statement", title="PayLah! wallet",
    folder="dbs-paylah", setup="DBS PayLah! wallet", bank="paylah", folder_title="DBS PayLah!",
    folder_pattern=r"paylah",
    test=_sees_paylah,
    detected_by="`PayLah!` in the first four lines",
    balance_check="`PREVIOUS BALANCE` + CR - DB = the printed `Total :` line"))
register(Layout(
    name="card", order=40, kind="card", **_DBS,
    label="a DBS debit card statement", title="Debit card",
    folder="dbs-debit-cards", setup="DBS debit card statements", bank="dbs", folder_title="DBS Debit Cards",
    test=_sees_card,
    # it prints the account its cards draw on, never the card folder's own number
    number_in_config=False,
    detected_by="`Debit Cards` in the first four lines",
    balance_check="the rows sum to the printed `GRAND TOTAL`; no balances exist"))


if __name__ == "__main__":
    main()
