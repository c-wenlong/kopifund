#!/usr/bin/env python3
"""Print the "when it says X do Y" table from the Problem calls in the code.

Every doctor, verify and parse failure is a `Problem(code, subject, fix)`, and
the fix string is written for the person reading the run report. This script
reads those calls out of the source with `ast` rather than importing anything,
so the table in docs/runbook.md is a copy of the code and can be regenerated
after any change:

    python3 docs/gen_runbook.py            # markdown table on stdout
    python3 docs/gen_runbook.py --check    # exit 1 if runbook.md is out of date

An f-string placeholder stays as `{expr}`, because that is what varies between
runs; everything around it is the literal text the tool prints.
"""
import argparse
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCES = ("server/finance/sync.py", "server/finance.py")

# The function a Problem is raised in says which step of the run found it.
STAGE = {"parse": "parse", "_run_parser": "parse", "categorise": "parse",
         "check_environment": "doctor", "check_statements": "doctor", "check_statement_files": "doctor",
         "check_blocks": "doctor", "check_config_coverage": "doctor",
         "check_options": "doctor", "check_relations": "doctor",
         "check_insert_size": "doctor", "check_orphans": "doctor", "check_pinned_rows": "doctor",
         "verify": "verify", "block_checks": "verify", "cmd_payees": "payees"}


def render(node, loose=False):
    """A string literal, an f-string or a concatenation of them, with every
    `{expr}` left in place: what varies between runs stays visible as a
    placeholder. With `loose`, an expression that is none of those (a join, a
    slice) renders as its own source in braces rather than failing."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(str(part.value) if isinstance(part, ast.Constant)
                       else "{" + ast.unparse(part.value) + "}"
                       for part in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = render(node.left, loose), render(node.right, loose)
        return None if left is None or right is None else left + right
    return "{" + ast.unparse(node) + "}" if loose else None


def problems(path):
    """Every Problem(code, subject, fix) in one file, in source order."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    where = {}
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for node in ast.walk(fn):
                where[id(node)] = fn.name
    found = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "Problem" and len(node.args) == 3):
            continue
        code, fix = render(node.args[0]), render(node.args[2], loose=True)
        if code is None or fix is None:
            print(f"warning: skipped a Problem at {path.name}:{node.lineno}",
                  file=sys.stderr)
            continue
        found.append(dict(code=code, fix=" ".join(fix.split()),
                          stage=STAGE.get(where.get(id(node)), "run"),
                          where=where.get(id(node), "?")))
    return found


def rows():
    """All of them, deduplicated on (code, fix), sorted by stage then code."""
    order = {"parse": 0, "doctor": 1, "verify": 2, "payees": 3, "run": 4}
    seen, out = set(), []
    for name in SOURCES:
        for p in problems(ROOT / name):
            key = (p["code"], p["fix"])
            if key not in seen:
                seen.add(key)
                out.append(p)
    return sorted(out, key=lambda p: (order.get(p["stage"], 9), p["code"]))


def table(items):
    lines = ["| Step | It says | Do this |", "| --- | --- | --- |"]
    for p in items:
        code = p["code"].replace("{label}", "<account or period>")
        lines.append(f"| {p['stage']} | `{code}` | {p['fix']} |")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true",
                    help="exit 1 if docs/runbook.md does not contain this table")
    args = ap.parse_args(argv)
    items = rows()
    text = table(items)
    if args.check:
        runbook = (ROOT / "docs" / "runbook.md").read_text(encoding="utf-8")
        if text not in runbook:
            print("docs/runbook.md is out of date; paste the output of "
                  "python3 docs/gen_runbook.py", file=sys.stderr)
            return 1
        print(f"docs/runbook.md matches the code ({len(items)} rows)")
        return 0
    print(text)
    print(f"\n{len(items)} rows", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
