"""Shared plumbing for the parser tests.

The fixtures under server/tests/fixtures/statements/ are scrubbed copies of the
pdftotext dumps of real statements, one per layout. Every personal identifier
(names, account and card numbers, addresses, phone numbers, emails, references)
is replaced by a consistent fake of the same width, so the column layout still
parses. Merchants and towns that would say where someone lived, studied or
travelled are invented too, at the same width, and the foreign wallets are NOK
and DKK. Amounts are kept wherever a balance chain runs through them, so every
chain still reconciles.
Every helper below runs the same steps in the same order as the script's own
main(), so a test sees what a real run produces.

The parsers here see the shipped city table only (places.load(None)), so a
data folder's places.csv cannot change what a test resolves.
"""
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures" / "statements"   # scrubbed statement dumps, tracked

from finance.parsers import parse_statements as ps        # noqa: E402
from finance.parsers import parse_youtrip as yt           # noqa: E402
from finance.parsers import parse_revolut as rv           # noqa: E402
from finance.parsers import parse_wise as ws             # noqa: E402
from finance.parsers import places                       # noqa: E402

places.load(None)

# fixture -> the wallet folder a YouTrip statement would sit in
WALLET = {"youtrip-sgd.txt": "SGD", "youtrip-sgd-gap.txt": "SGD",
          "youtrip-sgd-boundary.txt": "SGD", "youtrip-nok.txt": "NOK",
          "youtrip-empty.txt": "EUR"}


class Run:
    """One parser run: the rows kept, every block, the blocks that would be
    written, the card rows dropped per file and the mid-chain drifts found."""

    def __init__(self, rows, blocks, blocks_out, dropped, bad, no_amount=()):
        self.rows = rows
        self.blocks = blocks
        self.blocks_out = blocks_out
        self.dropped = dropped
        self.bad = bad
        self.no_amount = list(no_amount)

    def block(self, currency=None, source=None):
        """The one block matching the given currency and/or source file."""
        found = [b for b in self.blocks
                 if (currency is None or b["currency"] == currency)
                 and (source is None or b["source"] == source)]
        assert len(found) == 1, f"expected one block, got {len(found)}"
        return found[0]

    def rows_from(self, source):
        return [r for r in self.rows if r["source"] == source]

    def keys(self):
        return {r["txn_key"] for r in self.rows}


def text(name):
    path = FIXTURES / name
    if not path.exists():
        import unittest
        raise unittest.SkipTest(f"fixture {name} is missing from {FIXTURES}")
    return path.read_text()


def run_statements(names, lines=None):
    """parse_statements over the named fixtures. `lines` replaces one fixture's
    text, so a test can bend a printed number and watch the check catch it."""
    ps.WARNINGS.clear()
    sources = [(n, (lines or {}).get(n) or text(n)) for n in names]
    rows, blocks = ps.parse_all(sources)
    rows = ps.enrich_all(rows, blocks)
    rows, blocks_out, dropped, bad = ps.finish(rows, blocks)
    return Run(rows, blocks, blocks_out, dropped, bad)


def run_youtrip(names, lines=None):
    """parse_youtrip over the named fixtures, each with its wallet currency."""
    ps.WARNINGS.clear()
    rows, blocks = [], []
    for name in names:
        r, b = yt.parse((lines or {}).get(name) or text(name), name, WALLET[name])
        rows += r
        blocks.append(b)
    yt.backfill_account_no(blocks)
    rows, no_amount = yt.enrich_all(rows, blocks)
    rows = yt.synthesise_all(rows, blocks)
    bad = yt.validate(rows, blocks)
    yt.add_keys(rows)
    return Run(rows, blocks, blocks, Counter(), bad, no_amount)


def run_revolut(names, lines=None):
    """parse_revolut over the named fixtures, each split into its currency
    sections the way main() splits a PDF."""
    ps.WARNINGS.clear()
    sources = []
    for name in names:
        parts = rv.sections((lines or {}).get(name) or text(name))
        for ccy, part in parts:
            sources.append((f"{name}#{ccy}" if len(parts) > 1 else name, part, ccy))
    rows, blocks, stmts, bad, no_amount = rv.run(sources)
    run = Run(rows, blocks, blocks, Counter(), bad, no_amount)
    run.stmts = stmts
    return run


def run_wise(names, lines=None):
    """parse_wise over the named fixtures, each one currency balance, with the
    wallet read off its own "XXX statement" heading the way main() reads it."""
    ps.WARNINGS.clear()
    sources = []
    for name in names:
        body = (lines or {}).get(name) or text(name)
        sources.append((name, body, ws.statement_currency(body)))
    rows, blocks, stmts, bad, no_amount = ws.run(sources)
    run = Run(rows, blocks, blocks, Counter(), bad, no_amount)
    run.stmts = stmts
    return run


def edit_line(name, contains, old, new):
    """The fixture's text with `old` replaced by `new` on the one line that
    contains every fragment in `contains`. Line ends are kept as they are, so
    the form feed between pages (a Revolut section break) survives."""
    out, hits = [], 0
    for ln in text(name).splitlines(keepends=True):
        if all(c in ln for c in contains) and old in ln:
            ln = ln.replace(old, new)
            hits += 1
        out.append(ln)
    assert hits == 1, f"{contains} matched {hits} lines in {name}, expected 1"
    return "".join(out)


def drop_line(name, contains):
    """The fixture's text without the one line containing every fragment,
    every other line end (form feeds included) kept as it is."""
    out, hits = [], 0
    for ln in text(name).splitlines(keepends=True):
        if all(c in ln for c in contains):
            hits += 1
            continue
        out.append(ln)
    assert hits == 1, f"{contains} matched {hits} lines in {name}, expected 1"
    return "".join(out)
