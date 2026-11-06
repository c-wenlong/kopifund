"""Helpers more than one test module uses: a throwaway data folder, a small
invented record built from test_db's rows, a fixed today, serve's answer and
write closing the record they open, and a transaction in the shape
`db.snapshot` gives the stats.

Imports nothing that reads the data folder when it loads, and imports isolate
first itself, so a module may import it before or after its own isolate line.
"""
import isolate  # noqa: F401  first: no test writes the real record or cache
import importlib
import os
import pkgutil
import sys
import tempfile
import unittest
from contextlib import ExitStack, contextmanager
from datetime import date
from pathlib import Path
from unittest import mock

from finance import db
from test_db import SNAP, block, parsed


class DataFolder(unittest.TestCase):
    """A temporary data folder (`self.root`) holding the record (`self.path`),
    with KOPIFUND_DATA and KOPIFUND_DB pointed at them for the test, so
    nothing touches the real ones."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.path = self.root / "kopifund.db"
        p = mock.patch.dict(os.environ, {"KOPIFUND_DATA": str(self.root), "KOPIFUND_DB": str(self.path)})
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)


def user_tables(**lists):
    """A fresh copy of test_db.SNAP (the user tables every small record starts
    from), with any of its lists replaced by a copy of the one given."""
    out = {k: [dict(x) for x in v] for k, v in SNAP.items()}
    out.update({k: [dict(x) for x in v] for k, v in lists.items()})
    return out


def payees(names, kind="Merchant", country="SGP"):
    """The payees a load is told about, one per name."""
    return [{"name": n, "kind": kind, "country": country} for n in names]


def build_record(rows, *, user=None, blocks=None, payees=(), plan=None, path=":memory:", **load):
    """A record at `path` (in memory by default) holding `user` (user_tables()
    unless given) and one load of `rows` (test_db.row dicts) in `blocks` (one
    test_db.block unless given). `load` goes on to db.load_parsed
    (trips_by_key, complete, kind). The caller closes it."""
    conn = db.connect(path)
    db.import_user_tables(conn, user_tables() if user is None else user, plan)
    db.load_parsed(conn, parsed(blocks or [block()], list(rows)), payees=list(payees), **load)
    return conn


@contextmanager
def today_is(day):
    """`date.today()` answers `day` in every finance and analytics module (and
    serve.py) inside the block, for a test whose answer would otherwise move
    with the real clock. Each module that took `date` from datetime gets a
    `date` whose today is fixed; nothing else about dates changes."""
    class Fixed(date):
        @classmethod
        def today(cls):
            return cls(day.year, day.month, day.day)
    for package in ("finance", "analytics"):
        root = importlib.import_module(package)
        for info in pkgutil.walk_packages(root.__path__, f"{package}."):
            importlib.import_module(info.name)
    importlib.import_module("serve")
    with ExitStack() as stack:
        for name, module in list(sys.modules.items()):
            if name.split(".")[0] in ("finance", "analytics", "serve") and getattr(module, "date", None) is date:
                stack.enter_context(mock.patch.object(module, "date", Fixed))
        yield


def answer(path, query):
    """serve.answer as the HTTP handler calls it: the request closes the
    record it opened. Only for a test that hands in no record of its own
    (a patched `api.open_record`), which this would close too."""
    import serve
    with serve._owning():
        return serve.answer(path, query)


def write(path, query, body, device=None):
    """serve.write as the HTTP handler calls it; the same caveat as `answer`."""
    import serve
    with serve._owning():
        return serve.write(path, query, body, device=device)


def snapshot_txn(key, day, cents, payee="", *, text=None, category="Subscriptions", account="111-1",
                 bucket="Spend"):
    """One transaction as `db.snapshot` gives it to the stats: an SGD card
    payment of `cents` (negative out) on `day` (ISO or a date), its bank text
    `text`, else the payee in capitals."""
    day = day.isoformat() if isinstance(day, date) else day
    return {"key": key, "date": day, "month": day[:7], "amount_minor": cents, "amount_sgd_minor": cents,
            "currency": "SGD", "flow_class": "External", "synthetic": False, "bucket": bucket,
            "need_class": "Want", "payee": payee, "category": category,
            "merchant_raw": text if text is not None else payee.upper(), "counterparty": "", "memo": "",
            "account_no": account, "bank_txn_type": "POS"}
