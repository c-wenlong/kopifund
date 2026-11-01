#!/usr/bin/env python3
"""One module per analysis, and the registry that runs them.

Every stat module exposes `ID`, `TITLE`, `CHART` and `run(snapshot, benchmarks)`
and returns the result dict SPEC.md documents. `MODULES` is the order the runner
and the renderer list them in: the shape of the money first, then the detail,
then the forward look, then the health of the data itself.
"""
import importlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
BENCHMARKS = HERE.parent / "benchmarks.json"

MODULE_NAMES = (
    "monthly_cashflow",
    "category_breakdown",
    "needs_wants_savings",
    "recurring_detector",
    "eating_out",
    "merchant_concentration",
    "one_offs",
    "income_stability",
    "savings_runway",
    "portfolio",
    "cpf",
    "travel",
    "benchmarks_sg",
    "forward_view",
    "data_quality",
)


def modules():
    """Every stat module, in the documented order."""
    return [importlib.import_module(f"{__name__}.{name}") for name in MODULE_NAMES]


def by_id():
    return {m.ID: m for m in modules()}


def benchmarks_path():
    """`benchmarks.json` in the data folder when the user keeps one there, else
    the generic server/analytics/benchmarks.json that ships with the repo (G10: a list
    of references picked for one person's school or age is that person's, so it
    lives with their data)."""
    from finance.paths import data_root
    own = data_root() / "benchmarks.json"
    return own if own.is_file() else BENCHMARKS


def load_benchmarks(path=None):
    """The reference numbers (`benchmarks_path`), or None when there are none.

    Stats treat a missing file as "no reference numbers", never as an error.
    G20: the file holds dollars, as its sources publish them; each SGD item is
    turned to integer cents here, once, as it is read (benchmarks_sg.in_cents).
    """
    path = Path(path) if path else benchmarks_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    from .benchmarks_sg import in_cents
    return in_cents(data) if isinstance(data, dict) else data
