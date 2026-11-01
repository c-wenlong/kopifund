#!/usr/bin/env python3
"""Write the layouts table in docs/parsing.md from the layout registry.

G21: every statement layout is declared once, in its parser module
(server/finance/parsers/layouts.py). This script writes the table of the layouts that
produce rows, in the registry's order, between the two markers in
docs/parsing.md:

    python3 -m docs.gen_parsing            # rewrite the table in docs/parsing.md
    python3 -m docs.gen_parsing --check    # exit 1 if it is out of date (make check)

Run from the repository root, as a module; the script puts server/ on the
path so `finance` imports.

Stdlib only; nothing is read from or written to a data folder.
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))   # where the finance package lives now
DOC = ROOT / "docs" / "parsing.md"
BEGIN = "<!-- layouts: begin, written by docs/gen_parsing.py -->"
END = "<!-- layouts: end -->"


def _cell(text):
    return str(text).replace("|", "\\|")


def render(layouts=None):
    """The table, markers included. `layouts` defaults to the registry's."""
    if layouts is None:
        from finance.parsers import layouts as registry
        layouts = registry.every()
    lines = [BEGIN, "", "| Layout | Folder | Detected by | Balance check | Status |",
             "| --- | --- | --- | --- | --- |"]
    for lay in layouts:
        if lay.csv is None:               # Tiger and CPF: covered at the end, no rows
            continue
        folder = "statements/" + lay.folder.replace("{ccy}", "<ccy>") + "/"
        # G21: the status in the registry's own words, the ones Setup shows
        lines.append(f"| {_cell(lay.title or lay.name)} | `{folder}` | {_cell(lay.detected_by)} | "
                     f"{_cell(lay.balance_check)} | {_cell(lay.status)} |")
    lines += ["", END]
    return "\n".join(lines)


def replaced(doc, part):
    start, end = doc.index(BEGIN), doc.index(END) + len(END)
    return doc[:start] + part + doc[end:]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="exit 1 if docs/parsing.md is out of date")
    args = ap.parse_args(argv)
    doc = DOC.read_text(encoding="utf-8")
    want = replaced(doc, render())
    if args.check:
        if want != doc:
            print("docs/parsing.md is out of date: run python3 -m docs.gen_parsing", file=sys.stderr)
            return 1
        print("docs/parsing.md matches the layout registry")
        return 0
    DOC.write_text(want, encoding="utf-8")
    print("wrote the layouts table into docs/parsing.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
