"""The statement parsers and the categoriser, one module per feed (G19).

Each module is imported normally (`from finance.parsers import parse_statements`)
and each keeps its command line, run from the repository root:

    python3 -m finance.parsers.parse_statements statements/posb-everyday --out /tmp/sync
    python3 -m finance.parsers.categorise --lint ~/.kopifund/cache/rules-input.csv

docs/parsing.md describes the layouts each one reads. Each `parse_*` module
declares those layouts in layouts.py's registry (G21), which everything that
lists layouts reads.
"""
