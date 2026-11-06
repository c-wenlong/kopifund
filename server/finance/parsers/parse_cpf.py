#!/usr/bin/env python3
"""Extract CPF yearly statement PDFs into one dict per statement.

Usage:
    python3 -m finance.parsers.parse_cpf [FOLDER] [--json OUT] [--no-text]

FOLDER defaults to the data folder's statements/cpf and is searched recursively for PDFs;
each one is a yearly statement laid out as <year>/<year>.pdf. Text comes from
`pdftotext -layout`, the same way the Tiger and DBS parsers get it, and is saved
beside the PDF as <name>.txt unless --no-text. The run prints one line per year
and, with --json, writes the parsed dicts.

The Central Provident Fund is Singapore's mandatory savings scheme. Twenty per
cent of gross pay is deducted and the employer adds seventeen on top, and the
37 per cent lands in three accounts: Ordinary (OA), Special (SA) and MediSave
(MA). It is locked savings rather than cash, and the statement is the only
record of it.

CPF has changed the statement's layout over the years, so this reads three
layouts:

  * 2016 to 2019, the "personalised summary": every figure is a label and an
    amount on one printed line, two labels to a line ("Employment $ 1,000.00"
    on the left, "Interest $ 12.34" on the right). Only the combined employment
    contribution is printed, never the split between the member and the
    employer, so `employee` and `employer` are None for those years and only
    `employment` is filled in. Base and extra interest are not split either.

  * 2020, a one-off: the same statement of account at the back, but the front
    page lists "You / Employer / Government / Base Interest / Extra Interest /
    Government support", again one label and one amount per line.

  * 2021 to 2025, "Yearly Statement of Account": section 2.1 prints the inflow
    the same way ("Employee contributions", "Base interest"), so the same
    same-line reader handles 2020 onwards. The outflow is a doughnut chart
    instead, with each category's name on one line and its amount two lines
    below at the same left margin, which is how the outflow is read in every
    layout: 2016 to 2020 print three categories across one line with the three
    amounts on the line below, aligned the same way.

Balances come from the transactions table rather than from any of those front
pages, because the `01 JAN BAL` and `31 DEC BAL` rows are printed in every year
and in the same shape. The table's columns are read positionally, as in
parse_statements.py and parse_tiger.py: a 2020 contribution that only touched
MediSave prints one amount and nothing else, so "250.00" is the MediSave figure
only because of where it sits. The header is re-read every time it is printed,
so a page header cutting the table in half is read with its own header, and any
line that is not a transaction row is ignored, which is what makes the page
furniture harmless.

Nothing is netted or converted here. Amounts are floats signed as printed, so an
outflow is negative in `transactions` and positive in `outflow`.
"""
import argparse
import json
import re
import sys
from pathlib import Path

# the data folder's layout (server/finance/paths.py) decides where text dumps go
from finance.paths import current_statements, statements_dir
from finance.pdftext import pdf_to_text, require as require_pdftotext

from .common import header_spans
from .layouts import CHECKED, Bank, Layout, Seen, register, register_bank

MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
          "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
MONTH_NO = {m: i + 1 for i, m in enumerate(MONTHS)}

# an amount as CPF prints it: "1,234.56", "$500.00", "-$18.00"
AMOUNT = r"-?\$?-?[0-9][0-9,]*\.[0-9]{2}"
MONEY = re.compile(AMOUNT)

# a transaction row: two digits, a month, then the three-letter code
ROW = re.compile(r"^\s*(?P<day>\d{2})\s+(?P<mon>" + "|".join(MONTHS) +
                 r")\s+(?P<code>[A-Z]{2,4})\b")
FOR_MONTH = re.compile(r"\b(" + "|".join(MONTHS) + r")\s+(\d{4})\b")
HEADER = re.compile(r"^\s*(DATE|Date)\s+(CODE|Code)\b")

# "REF A : EXAMPLE AGENCY", then "     B : ACME LABS PTE. LTD." under it
REF_FIRST = re.compile(r"^\s*REF\s+([A-Z])\s*:\s*(\S.*?)\s*$")
REF_MORE = re.compile(r"^\s+([A-Z])\s*:\s*(\S.*?)\s*$")

YEAR_NEW = re.compile(r"Yearly Statement of Account for (\d{4})")
YEAR_OLD = re.compile(r"Statement Period\s*:\s*Jan to Dec (\d{4})")
ACCOUNT = re.compile(r"CPF Account Number:\s*([A-Z0-9]+)|"
                     r"Account No\.\s*:\s*([A-Z0-9]+)")

# the printed total across all three accounts, in each of the three layouts
TOTALS = [re.compile(r"You have \$(" + AMOUNT + r") in your CPF accounts"),
          re.compile(r"all your CPF accounts is\s+\$(" + AMOUNT + r")"),
          re.compile(r"\(as at 31 December \d{4}\)\s+\$(" + AMOUNT + r")")]

OUTFLOW_KEYS = ["housing", "healthcare", "retirement",
                "education", "investment", "others"]
# the outflow doughnut and the 2016-to-2020 tiles use the same words
OUTFLOW_WORDS = {k: re.compile(r"\b" + k + r"\b", re.I) for k in OUTFLOW_KEYS}


def money(s):
    """A printed amount as a float. The minus sign sits outside the dollar
    sign on the newer statements ("-$18.00") and inside on nothing at all,
    but both are accepted."""
    s = s.replace(",", "").replace("$", "")
    return -float(s[1:]) if s.startswith("-") else float(s)


def cents(x):
    return round(x, 2) + 0.0


# ---------------------------------------------------------------- table layout

class Columns:
    """The columns of the transactions table, read off its header block.

    A heading is spread over two to four printed lines ("ORDINARY" above
    "ACCOUNT" above "(OA)" above "$"), so the header's words are clustered by
    horizontal overlap and each cluster becomes one column."""

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
        """The (start, end) of the first column whose heading holds every key."""
        for label, s, e in self.cols:
            if all(k in label for k in keys):
                return s, e
        return None

    def accounts(self):
        """The OA, SA and MA columns, whatever the layout called them. 2016 to
        2020 print "ORDINARY ACCOUNT (OA) $", 2021 to 2025 "Ordinary Account
        ($)", so the account is matched on its own word."""
        by_word = [self.find("ORDINARY") or self.find("Ordinary"),
                   self.find("SPECIAL") or self.find("Special"),
                   self.find("MEDISAVE") or self.find("MediSave")]
        if all(by_word):
            return by_word
        # nothing recognisable: fall back to the last three columns in order
        return [(s, e) for _, s, e in self.cols[-3:]] if len(self.cols) >= 3 else []

    @staticmethod
    def overlap(span, start, end):
        """How much of a printed amount sits inside the column at `span`.

        Overlap is what decides which account an amount belongs to, and there
        is always some on the real statements seen so far. The caller falls back to the
        nearest right edge when there is none, because CPF right-aligns every
        figure."""
        return min(end, span[1]) - max(start, span[0])


def is_blank(ln):
    return not ln.strip()


def header_block(lines, i):
    """The printed header around line `i`, which is the run of non-blank lines
    it sits in. The old layout puts "DATE CODE ..." first and the account names
    under it; the new one puts "Ordinary Special MediSave" above "Date Code"."""
    top = i
    while top > 0 and not is_blank(lines[top - 1]):
        top -= 1
    bottom = i
    while bottom + 1 < len(lines) and not is_blank(lines[bottom + 1]):
        bottom += 1
    return lines[top:bottom + 1]


def parse_transactions(lines):
    """Every row of the "CPF transactions" table, the two BAL rows apart.

    Returns (rows, opening, closing). A row is one printed line; anything that
    is not one is skipped, so the page header and footer that interrupt the
    table on a long year cost nothing. The header is re-read wherever it is
    printed again."""
    cols, rows = None, []
    opening = closing = None
    for i, ln in enumerate(lines):
        if HEADER.match(ln):
            cols = Columns(header_block(lines, i))
            continue
        m = ROW.match(ln)
        if not m or cols is None:
            continue
        spans = cols.accounts()
        if len(spans) != 3:
            continue
        amounts = [None, None, None]
        for hit in MONEY.finditer(ln):
            s, e = hit.start(), hit.end()
            if e <= spans[0][0] - 4:            # printed left of the OA column
                continue
            overlaps = [cols.overlap(sp, s, e) for sp in spans]
            best = max(range(3), key=lambda k: overlaps[k])
            if overlaps[best] <= 0:             # right-align tie-break
                best = min(range(3), key=lambda k: abs(e - spans[k][1]))
            if amounts[best] is None:
                amounts[best] = money(hit.group())
        oa, sa, ma = (0.0 if a is None else a for a in amounts)

        code = m.group("code")
        rest = ln[m.end():]
        cut = min((h.start() for h in MONEY.finditer(rest)), default=len(rest))
        head = rest[:cut]
        fm = FOR_MONTH.search(head)
        for_month = f"{fm.group(2)}-{MONTH_NO[fm.group(1)]:02d}" if fm else None
        ref = next((t for t in re.findall(r"\b([A-Z])\b", head)), "")

        if code == "BAL":
            row = {"oa": oa, "sa": sa, "ma": ma,
                   "total": cents(oa + sa + ma)}
            if opening is None:
                opening = row
            closing = row
            continue
        rows.append({"date": f"{m.group('day')} {m.group('mon')}", "code": code,
                     "for_month": for_month, "ref": ref,
                     "oa": oa, "sa": sa, "ma": ma})
    return rows, opening, closing


def parse_refs(lines):
    """The "REF A : ..." legend under the table, and the bare "B : ..." lines
    that continue it. An empty dict when the year printed none."""
    out = {}
    for i, ln in enumerate(lines):
        m = REF_FIRST.match(ln)
        if not m:
            continue
        out.setdefault(m.group(1), m.group(2))
        for more in lines[i + 1:]:
            if is_blank(more):
                continue
            m2 = REF_MORE.match(more)
            if not m2:
                break
            out.setdefault(m2.group(1), m2.group(2))
    return out


# ---------------------------------------------------------------- front pages

def labelled(text, pattern, default=None):
    """The amount printed immediately after `pattern` on the same line.

    Every figure on the 2016-to-2020 front pages and in the 2021-to-2025 inflow
    details is printed this way, two of them to a line on the oldest layout
    ("Employment $ 1,000.00" on the left, "Interest $ 12.34" on the right). Only
    blanks and a stray dollar sign may sit between the label and its amount,
    which is what keeps "You $ 0.00" apart from the prose line "You have
    $1,234.56 in your CPF accounts" that the same statement prints above it."""
    m = re.search(pattern + r"[ \t]*\$?[ \t]*(" + AMOUNT + r")", text)
    return money(m.group(1)) if m else default


def window(lines, start, stop):
    """The lines between the first line matching `start` and the next line
    matching `stop`, which is how each front-page section is fenced off from
    the ones printed around it."""
    top = next((i for i, ln in enumerate(lines) if start.search(ln)), None)
    if top is None:
        return []
    for i in range(top + 1, len(lines)):
        if stop.search(lines[i]):
            return lines[top:i]
    return lines[top:]


def stacked(lines, keys):
    """{key: amount} for a block that prints a category's name on one line and
    its amount below, at the same left margin.

    2016 to 2020 print three names across one line and their three amounts on
    the next; 2021 to 2025 print one name per line with a blank line under it.
    Both are read the same way, by matching left edges. The first printing of a
    name wins, because the prose under the tiles repeats the words ("CPF
    Investment Scheme transactions" sits below the Investment amount)."""
    out = {}
    for i, ln in enumerate(lines):
        heads = [(k, m.start()) for k, pat in keys.items()
                 for m in [pat.search(ln)] if m and k not in out]
        if not heads:
            continue
        for below in lines[i + 1:i + 4]:
            found = [(h.start(), money(h.group())) for h in MONEY.finditer(below)]
            if not found:
                continue
            for k, at in heads:
                near = min(found, key=lambda f: abs(f[0] - at))
                if abs(near[0] - at) <= 2:
                    out[k] = near[1]
            break
    return out


def parse_outflow(lines, layout):
    """What left the accounts, by category. 2021 and 2022 print no breakdown at
    all, only the total, so every category stays at zero there."""
    if layout == "yearly":
        box = window(lines, re.compile(r"^Outflow\s*$"),
                     re.compile(r"^\s*\d+(\.\d+)?\.\s+\S"))
        total = labelled("\n".join(box), r"\bYou used\b", 0.0)
    else:
        box = window(lines, re.compile(r"was debited|You used"),
                     re.compile(r"FINANCIAL TIP|Financial Tip"))
        total = labelled("\n".join(box), r"\bYou used\b")
        if total is None:
            # 2016 to 2019 print "$ 0.00" on the line above "was debited^ from
            # your CPF accounts in 2016 for your following needs", not after it
            top = next((i for i, ln in enumerate(lines) if "was debited" in ln), 0)
            hits = MONEY.findall("\n".join(lines[max(0, top - 2):top + 1]))
            total = money(hits[-1]) if hits else 0.0
    got = stacked(box, OUTFLOW_WORDS)
    out = {k: got.get(k, 0.0) for k in OUTFLOW_KEYS}
    out["total"] = cents(total)
    return out


def parse_inflow(text, layout):
    """What went in, split the way the year printed it.

    2016 to 2019 print one combined "Employment" figure and no split between
    the member and the employer, so `employee` and `employer` are None there
    and only `employment` is filled in; the same years print one "Interest"
    figure and never its base and extra halves, so those are None too. From
    2020 the Government line is the whole of what the Government put in,
    interest included, which is why `government` is 0 on the older statements
    that earned interest."""
    if layout == "summary":
        employment = labelled(text, r"\bEmployment\b", 0.0)
        interest = labelled(text, r"\bInterest\b", 0.0)
        support = labelled(text, r"\bGovernment support\b", 0.0)
        voluntary = labelled(text, r"\bVoluntary top-ups\b", 0.0)
        return {"employee": None, "employer": None, "employment": cents(employment),
                "government": cents(support), "base_interest": None,
                "extra_interest": None, "interest": cents(interest),
                "government_support": cents(support), "voluntary": cents(voluntary),
                "total": cents(employment + support + interest + voluntary)}

    if layout == "yearly":
        employee = labelled(text, r"\bEmployee contributions\b", 0.0)
        employer = labelled(text, r"\bEmployer contributions\b", 0.0)
        government = labelled(text, r"contributions by the Government in \d{4}", 0.0)
        base = labelled(text, r"\bBase interest\b", 0.0)
        extra = labelled(text, r"\bExtra interest\b", 0.0)
    else:                                        # the 2020 one-off
        employee = labelled(text, r"\bYou\b", 0.0)
        employer = labelled(text, r"\bEmployer\b", 0.0)
        government = labelled(text, r"\bGovernment\b", 0.0)
        base = labelled(text, r"\bBase Interest\b", 0.0)
        extra = labelled(text, r"\bExtra Interest\b", 0.0)
    support = labelled(text, r"\bGovernment support\b", 0.0)
    return {"employee": cents(employee), "employer": cents(employer),
            "employment": cents(employee + employer), "government": cents(government),
            "base_interest": cents(base), "extra_interest": cents(extra),
            "interest": cents(base + extra), "government_support": cents(support),
            "voluntary": 0.0,
            "total": cents(employee + employer + government)}


def printed_total(text):
    """The total across the three accounts as the front page prints it, which
    is what the closing balances off the table are checked against."""
    for pat in TOTALS:
        if m := pat.search(text):
            return money(m.group(1))
    return None


# ---------------------------------------------------------------- one statement

def layout_of(text):
    if "Yearly Statement of Account" in text:
        return "yearly"                          # 2021 to 2025
    if re.search(r"^\s*Employment\s+\$", text, re.M):
        return "summary"                         # 2016 to 2019
    return "2020"


def parse_text(text, file):
    """One statement dict. `file` is the path the statement came from, relative
    to the folder it sits in, and is carried through untouched."""
    lines = text.splitlines()
    layout = layout_of(text)

    m = YEAR_NEW.search(text) or YEAR_OLD.search(text)
    year = int(m.group(1)) if m else int(re.search(r"(\d{4})", file).group(1))

    m = ACCOUNT.search(text)
    account = (m.group(1) or m.group(2)) if m else ""

    rows, opening, closing = parse_transactions(lines)
    zero = {"oa": 0.0, "sa": 0.0, "ma": 0.0, "total": 0.0}
    opening, closing = opening or dict(zero), closing or dict(zero)
    for row in rows:
        day, mon = row.pop("date").split()
        row["date"] = f"{year}-{MONTH_NO[mon]:02d}-{day}"

    # the front page prints the same closing total in words; a statement that
    # disagrees with itself is worth stopping on rather than reporting
    shown = printed_total(text)
    if shown is not None and abs(shown - closing["total"]) > 0.005:
        raise ValueError(f"{file}: 31 DEC BAL totals {closing['total']:,.2f} but the "
                         f"statement says {shown:,.2f}")

    return {
        "file": file,
        "account": account,
        "year": year,
        "as_of": f"{year}-12-31",
        "opening": opening,
        "closing": closing,
        "inflow": parse_inflow(text, layout),
        "outflow": parse_outflow(lines, layout),
        "transactions": rows,
        "refs": parse_refs(lines),
    }


def reconcile(statement):
    """{account: difference} between the opening balance plus every printed
    transaction and the closing balance. Zeroes when the year adds up, which it
    does on every real statement seen so far."""
    out = {}
    for key in ("oa", "sa", "ma"):
        moved = sum(t[key] for t in statement["transactions"])
        out[key] = cents(statement["opening"][key] + moved - statement["closing"][key])
    return out


# ---------------------------------------------------------------- files

def parse_pdf(path, folder, write_text=True):
    """One statement, named by its path relative to the folder it sits in."""
    path, folder = Path(path), Path(folder)
    try:
        name = path.resolve().relative_to(folder.resolve()).as_posix()
    except ValueError:
        name = path.name
    return parse_text(pdf_to_text(path, write=write_text), name)


def split_reconciled(statements):
    """(balanced, rejected): the statements whose year adds up, and one
    {year, file, off} per statement that does not, `off` being reconcile()'s
    difference per account."""
    balanced, rejected = [], []
    for s in statements:
        off = reconcile(s)
        if any(off.values()):
            rejected.append({"year": s["year"], "file": s["file"], "off": off})
        else:
            balanced.append(s)
    return balanced, rejected


def parse_folder(folder, write_text=True, rejected=None):
    """Every PDF under the folder whose year reconciles, oldest year first.

    This is the path the analytics and the record read CPF through, so the
    balance check runs here rather than only in main(): a statement whose
    opening plus transactions is not its closing is never returned. Each one
    left out is appended to `rejected` ({year, file, off}) when a list is
    given, so the caller can say which year failed."""
    folder = Path(folder)
    if not folder.is_dir():
        return []
    out = [parse_pdf(pdf, folder, write_text=write_text)
           for pdf in current_statements(folder.rglob("*.pdf"))]
    out.sort(key=lambda s: (s["year"], s["file"]))
    balanced, failed = split_reconciled(out)
    if rejected is not None:
        rejected.extend(failed)
    return balanced


def summary(s):
    n = lambda x: f"{x:>10,.2f}"
    split = lambda x: "       n/a" if x is None else n(x)
    i = s["inflow"]
    return (f"  {s['year']}  total {n(s['closing']['total'])}"
            f"  OA {n(s['closing']['oa'])}  SA {n(s['closing']['sa'])}"
            f"  MA {n(s['closing']['ma'])}"
            f"  employee {split(i['employee'])}  employer {split(i['employer'])}"
            f"  government {n(i['government'])}  interest {n(i['interest'])}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", nargs="?", default=str(statements_dir() / "cpf"),
                    help="folder of statement PDFs (default: statements/cpf in the data folder)")
    ap.add_argument("--json", metavar="OUT", help="write the parsed statements here")
    ap.add_argument("--no-text", action="store_true",
                    help="do not save the .txt dump beside each PDF")
    args = ap.parse_args()

    require_pdftotext()
    folder = Path(args.folder)
    if not folder.is_dir():
        sys.exit(f"not a directory: {folder}")

    bad = []
    statements = parse_folder(folder, write_text=not args.no_text, rejected=bad)
    if not statements and not bad:
        sys.exit(f"no PDFs found under {folder}")

    print(f"{len(statements)} statement(s) from {folder}\n")
    for s in statements:
        print(summary(s))

    if statements:
        last = statements[-1]
        print(f"\n  {sum(len(s['transactions']) for s in statements)} transaction(s) "
              f"over {len(statements)} year(s), "
              f"{last['closing']['total']:,.2f} SGD as at {last['as_of']}")
    if bad:
        for r in bad:
            print(f"  {r['year']} ({r['file']}) does not reconcile: {r['off']}")
        sys.exit(f"{len(bad)} year(s) do not reconcile")
    print("  every year reconciles: opening plus transactions equals closing")

    if args.json:
        out = Path(args.json)
        if str(out.parent) not in ("", "."):
            out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(statements, indent=2) + "\n")
        print(f"\nwrote {out}")


# ---------------------------------------------------------------- the layout (G21)

def _sees(text):
    if not (YEAR_NEW.search(text) or YEAR_OLD.search(text)) or "CPF" not in text:
        return None
    if not any(HEADER.match(ln) for ln in text.splitlines()):
        return None
    m = ACCOUNT.search(text)
    return Seen(numbers=((m.group(1) or m.group(2)) if m else "",))


def name_upload(pdf, read_text, wallet, today):
    """(year folder, file name) for an uploaded statement: its year."""
    st = parse_pdf(pdf, Path(pdf).parent, write_text=False)
    return str(st["year"]), f"{st['year']}.pdf"


# a yearly position, never transaction rows: no CSV, and its folder is
# `[analytics] cpf_folder` rather than a [[folder]]
register_bank(Bank("cpf", r"\bcpf\b", order=91, name="CPF"))
register(Layout(
    name="cpf", parser="cpf", module=__spec__.name, order=100, kind="CPF",
    label="a CPF yearly statement", folder="cpf", setup="CPF yearly statement",
    test=_sees, bank="cpf", folder_title="CPF", name_file=name_upload, status=CHECKED))


if __name__ == "__main__":
    main()
