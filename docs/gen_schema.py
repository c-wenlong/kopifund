#!/usr/bin/env python3
"""Write the record's part of docs/schema.md from the schema itself.

G17 (T13): the SQLite schema lives in server/finance/db.py. This script builds an
empty record in memory with that code and describes what SQLite then holds,
table by table, from PRAGMA table_info and PRAGMA foreign_key_list, plus the
views and triggers by name, between the two markers in docs/schema.md:

    python3 -m docs.gen_schema            # rewrite that part of docs/schema.md
    python3 -m docs.gen_schema --check    # exit 1 if it is out of date (make check)

Run from the repository root, as a module; the script puts server/ on the
path so `finance` imports (G19).

Stdlib only; nothing is read from or written to a data folder.
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))   # where the finance package lives now
DOC = ROOT / "docs" / "schema.md"
BEGIN = "<!-- sqlite-schema: begin, written by docs/gen_schema.py -->"
END = "<!-- sqlite-schema: end -->"


def _cell(v):
    return "" if v in (None, "", 0) else str(v).replace("|", "\\|")


def render():
    from finance import db
    conn = db.connect(":memory:")
    try:
        lines = [BEGIN, "", f"Schema version {db.SCHEMA_VERSION} (`PRAGMA user_version`). Every table is STRICT.", ""]
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY rowid")]
        lists = db.enums()
        values = {(t, c): ", ".join(lists[k]) for t, c, k in db.CHECKED}
        for t in tables:
            links = {r["from"]: r for r in conn.execute(f"PRAGMA foreign_key_list({t})")}
            lines += [f"### `{t}`", "",
                      "| Column | Type | Not null | Default | Key | Links to | On delete | One of |",
                      "| --- | --- | --- | --- | --- | --- | --- | --- |"]
            for c in conn.execute(f"PRAGMA table_info({t})"):
                fk = links.get(c["name"])
                to = f"`{fk['table']}.{fk['to']}`" if fk else ""
                on_delete = fk["on_delete"] if fk and fk["on_delete"] != "NO ACTION" else ""
                lines.append(f"| `{c['name']}` | {c['type']} | {'yes' if c['notnull'] else ''} | "
                             f"{_cell(c['dflt_value'])} | {_cell(c['pk'])} | {to} | {on_delete} | "
                             f"{values.get((t, c['name']), '')} |")
            lines.append("")
        for kind, label in (("view", "Views"), ("trigger", "Triggers"), ("index", "Indexes")):
            names = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = ? AND sql IS NOT NULL ORDER BY name", (kind,))]
            lines += [f"{label}: " + ", ".join(f"`{n}`" for n in names) + ".", ""]
        lines.append(END)
        return "\n".join(lines)
    finally:
        conn.close()


def replaced(doc, part):
    start, end = doc.index(BEGIN), doc.index(END) + len(END)
    return doc[:start] + part + doc[end:]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="exit 1 if docs/schema.md is out of date")
    args = ap.parse_args(argv)
    doc = DOC.read_text(encoding="utf-8")
    want = replaced(doc, render())
    if args.check:
        if want != doc:
            print("docs/schema.md is out of date: run python3 -m docs.gen_schema", file=sys.stderr)
            return 1
        print("docs/schema.md matches the record's schema")
        return 0
    DOC.write_text(want, encoding="utf-8")
    print("wrote the record's schema into docs/schema.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
