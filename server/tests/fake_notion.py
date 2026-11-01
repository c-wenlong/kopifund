#!/usr/bin/env python3
"""A Notion stand-in for the sync tests, plus row and block builders.

FakeNotion implements the handful of methods server/finance/sync.py uses and records
every write, so a test can assert not just the end state but how many calls it
took to get there. Pages are stored in the API's own shape, so
finance.notion.plain reads them exactly as it reads a live page.
"""
from collections import defaultdict
from pathlib import Path

from finance.model import CATEGORY_TYPES, ConfigMissing, load_config

# server/tests/fixtures/config.toml, not the owner's server/finance/config.toml: every id and
# account number in it is invented, so the suite runs in a fresh clone and does
# not depend on one machine. Its root is still the repository, so the doctor's
# folder checks look at the real statement tree when there is one.
FIXTURE_CONFIG = Path(__file__).resolve().parent / "fixtures" / "config.toml"

# What the tests' rows and blocks use; a select value outside this list is what
# doctor's unknown-option check is for.
DEFAULT_OPTIONS = {
    "Currency": ["SGD", "USD", "NOK"],
    "Direction": ["In", "Out"],
    "Bank txn type": ["Debit Card Transaction", "Funds Transfer", "Salary"],
    "Flow class": ["External", "Internal - own account", "Internal - PayLah",
                   "Internal - YouTrip", "Internal - Revolut", "Internal - Wise"],
    "Merchant country": ["SGP", "USA"],
    # the Categories selects (docs/schema.md, section 5)
    "Bucket": ["Income", "Spend", "Transfer"],
    "Group": ["Fixed", "Variable", "Savings"],
    "Need class": ["Need", "Want", "Savings", "Investment", "Income", "Transfer"],
}

# The properties `schema` reports for a data source no test has described:
# the Categories row's, the one data source the sync reads a schema of.
DEFAULT_SCHEMA = dict(CATEGORY_TYPES)


class FakeNotion:
    """Enough of NotionClient to drive a sync."""

    def __init__(self, options=None, schemas=None):
        self.pages = defaultdict(list)          # data source id -> [page]
        self.options = dict(options or {})      # (data source id, property) -> [name]
        self.schemas = dict(schemas or {})      # data source id -> {property: type}
        self.calls = []                         # (verb, target) in order
        self.writes = 0
        self._n = 0

    # -------------------------------------------------------- reads

    def query_all(self, ds_id, filter_=None):
        return [p for p in self.pages[ds_id] if not p.get("in_trash")]

    def query_sharded(self, ds_id, prop, edges):
        return self.query_all(ds_id)

    def map(self, fn, items, progress=None):
        """Sequential, so the recorded call order is deterministic."""
        out = []
        items = list(items)
        for i, x in enumerate(items, 1):
            out.append(fn(x))
            if progress:
                progress(i, len(items))
        return out

    def select_options(self, ds_id, prop):
        return self.options.get((ds_id, prop), DEFAULT_OPTIONS.get(prop, []))

    def schema(self, ds_id):
        """The data source object, in the API's shape: each property with its
        type and, for a select, its options."""
        props = {}
        for name, kind in self.schemas.get(ds_id, DEFAULT_SCHEMA).items():
            body = ({"options": [{"name": o} for o in self.select_options(ds_id, name)]}
                    if kind in ("select", "multi_select") else {})
            props[name] = {"type": kind, kind: body}
        return {"id": ds_id, "properties": props}

    # -------------------------------------------------------- writes

    def create(self, ds_id, properties):
        self._n += 1
        self.writes += 1
        page = {"id": f"fakepage{self._n:024d}", "properties": _typed(properties)}
        self.pages[ds_id].append(page)
        self.calls.append(("create", ds_id))
        return page

    def patch(self, page_id, properties):
        self.writes += 1
        page = self.page(page_id)
        page["properties"].update(_typed(properties))
        self.calls.append(("patch", page_id))
        return page

    def update_block(self, block_id, body):
        self.writes += 1
        self.blocks = getattr(self, "blocks", {})
        self.blocks[block_id] = body
        self.calls.append(("block", block_id))
        return body

    def archive(self, page_id):
        self.writes += 1
        self.page(page_id)["in_trash"] = True
        self.calls.append(("archive", page_id))

    def add_select_options(self, ds_id, prop, values):
        have = list(self.select_options(ds_id, prop))
        added = [v for v in values if v not in have]
        self.options[(ds_id, prop)] = have + added
        self.writes += 1
        self.calls.append(("options", f"{ds_id}.{prop}"))
        return added

    # -------------------------------------------------------- helpers

    def page(self, page_id):
        for pages in self.pages.values():
            for p in pages:
                if p["id"] == page_id:
                    return p
        raise KeyError(page_id)

    def verbs(self):
        return [c[0] for c in self.calls]


def _typed(properties):
    """Add the "type" key the API returns and to_api does not send."""
    return {name: {"type": next(iter(body)), **body} for name, body in properties.items()}


# ---------------------------------------------------------------- fixtures

try:
    CFG = load_config(FIXTURE_CONFIG)
except ConfigMissing as e:                    # skips every module that imports this
    import unittest
    raise unittest.SkipTest(str(e))

ACCOUNT = "222-22222-2"                       # the POSB account of the fixture config
CARD = "1234"                                 # the Mastercard of the fixture config

ROW = dict(txn_key="aaaa1111", date="2026-01-05", account="POSB Everyday Savings Account",
           account_no=ACCOUNT, currency="SGD", bank_txn_type="Debit Card Transaction",
           direction="Out", amount="-12.30", flow_class="External", merchant="KOPITIAM SGP",
           merchant_country="SGP", purchase_date="2026-01-04", card_last4=CARD,
           fx_original="", counterparty="", reference="MT2600001", memo="",
           balance_after="988.70", source="data/dbs-statements/posb-everyday/2026/1.pdf",
           period=f"{ACCOUNT} · SGD · 2026-01", payee="", category="", rule="")

BLOCK = dict(period=f"{ACCOUNT} · SGD · 2026-01", period_end="2026-01-31",
             account="POSB Everyday Savings Account", account_no=ACCOUNT, currency="SGD",
             opening="1001.00", closing="988.70", total_in="0.00", total_out="12.30",
             fx_to_sgd="", closing_sgd="988.70", n_txns="1", reconciled="True", note="",
             source="data/dbs-statements/posb-everyday/2026/1.pdf")


def row(**over):
    """One parsed transaction row, in the parser's CSV shape."""
    return {**ROW, **over}


def block(**over):
    """One parsed account-month block, in the parser's CSV shape."""
    return {**BLOCK, **over}
