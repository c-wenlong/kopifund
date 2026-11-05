"""The snapshot builder, over the real parser output.

The parsers run once for the whole module, into a temporary directory, the way
the end-to-end tests do it. Everything below then builds snapshots from that
directory, so nothing here touches Notion and nothing depends on a network.

The last class is the exception: it drives a FakeNotion, because the two doors
into a Notion snapshot (read it yourself, or flatten pages someone else read)
can only be compared against each other with pages in hand.
"""
import isolate  # first: no test writes the real record or cache
import argparse
import contextlib
import importlib.util
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from analytics import run as analytics_run
from finance import snapshot as snap
from analytics import stats
from finance import notion, sync
from finance.model import ConfigMissing, load_config
from finance.money import fx_minor, rate_e9

REPO = Path(__file__).resolve().parent.parent

# The parsers run over the owner's own statement tree, so this module needs
# both the real config and data/, neither of which a clone has. A module-level
# SkipTest makes unittest skip the module rather than error on import.
# Audit: the real data folder only when the run asks (KOPIFUND_TEST_REAL_DATA=1, server/tests/isolate.py).
from finance.paths import statements_dir  # noqa: E402
with isolate.real_data():
    if not statements_dir().is_dir():
        raise unittest.SkipTest("no statements in the data folder; the snapshot tests parse them")
    try:
        CFG = load_config()
    except ConfigMissing as e:
        raise unittest.SkipTest(str(e))

RULES = sync.default_rules(CFG)
TEMPS = []
_PARSED = {}


def cli():
    """finance.py, which cannot be imported by name: the `finance` package next
    to it wins the import."""
    if "cli" not in _PARSED:
        spec = importlib.util.spec_from_file_location(
            "finance_cli", Path(__file__).resolve().parent.parent / "finance.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _PARSED["cli"] = module
    return _PARSED["cli"]


def csv_dir():
    """The parser CSVs, produced once and reused by every test here."""
    if "dir" not in _PARSED:
        parsed = sync.parse(CFG, echo=False)
        TEMPS.append(parsed.csv_dir)
        _PARSED["dir"] = parsed.csv_dir
    return _PARSED["dir"]


_REAL_UP, _REAL_DOWN = isolate.real_data_for_module()


def setUpModule():
    _REAL_UP()


def tearDownModule():
    _REAL_DOWN()
    for path in TEMPS:
        shutil.rmtree(path, ignore_errors=True)


class TheCsvSnapshot(unittest.TestCase):
    """`--from-csv` must produce the same shape as a Notion build, with no
    network and no token."""

    @classmethod
    def setUpClass(cls):
        cls.snapshot = snap.build(CFG, source="csv", csv_dir=csv_dir(),
                                  rules_path=RULES)

    def test_it_carries_every_documented_section(self):
        for key in ("meta", "transactions", "periods", "payees", "categories",
                    "accounts", "cards", "budgets", "trips", "portfolio", "cpf"):
            self.assertIn(key, self.snapshot)
        self.assertEqual(self.snapshot["meta"]["source"], "csv")
        self.assertEqual(len(self.snapshot["meta"]["databases"]), 8)

    def test_every_transaction_has_every_documented_field(self):
        # G20: amounts in cents only
        want = {"key", "date", "month", "amount_minor", "amount_sgd_minor", "currency",
                "account", "account_no", "bank_txn_type", "direction",
                "flow_class", "merchant_raw", "merchant_country", "counterparty",
                "memo", "reference", "payee", "category", "need_class", "bucket",
                "group", "category_group", "card", "trip", "synthetic", "period"}
        rows = self.snapshot["transactions"]
        self.assertTrue(rows)
        for row in rows[:50] + rows[-50:]:
            self.assertEqual(set(row), want)

    def test_rows_are_sorted_so_a_rebuild_cannot_reorder_them(self):
        keys = [(r["date"], r["key"]) for r in self.snapshot["transactions"]]
        self.assertEqual(keys, sorted(keys))

    def test_the_categories_fallback_supplies_bucket_and_need_class(self):
        categorised = [r for r in self.snapshot["transactions"] if r["category"]]
        self.assertTrue(categorised)
        self.assertTrue(all(r["bucket"] for r in categorised))
        # a personal rule may still name a retired category (Transport, before split-transport)
        fallback = {c["name"] for c in snap.load_categories_fallback() + snap.retired_categories()}
        self.assertTrue({r["category"] for r in categorised} <= fallback)

    def test_synthetic_rows_are_flagged_from_the_bank_txn_type(self):
        rows = self.snapshot["transactions"]
        self.assertEqual({r["synthetic"] for r in rows}, {True, False})
        for r in rows:
            self.assertEqual(r["synthetic"],
                             r["bank_txn_type"] == snap.SYNTHETIC_TYPE)

    def test_a_non_sgd_row_is_converted_with_its_own_period_rate(self):
        rated = {b["name"]: b["fx_to_sgd"] for b in self.snapshot["periods"]
                 if b["fx_to_sgd"]}
        converted = [r for r in self.snapshot["transactions"]
                     if r["currency"] != "SGD" and r["period"] in rated]
        self.assertTrue(converted)
        # G20: the record's rule for a foreign row, in integers, at the block's rate
        for row in converted:
            rate = rated[row["period"]]
            self.assertEqual(row["amount_sgd_minor"], fx_minor(row["amount_minor"], rate_e9(rate)))
            self.assertLessEqual(abs(row["amount_sgd_minor"] - row["amount_minor"] * rate), 0.5 + 1e-6)

    def test_a_non_sgd_row_with_no_rate_keeps_its_face_value(self):
        rated = {b["name"] for b in self.snapshot["periods"] if b["fx_to_sgd"]}
        bare = [r for r in self.snapshot["transactions"]
                if r["currency"] != "SGD" and r["period"] not in rated]
        for row in bare:
            self.assertEqual(row["amount_sgd_minor"], row["amount_minor"])

    def test_it_says_what_a_csv_snapshot_cannot_know(self):
        notes = " ".join(self.snapshot["meta"]["notes"])
        self.assertIn("Trips", notes)
        self.assertEqual(self.snapshot["trips"], [])
        self.assertTrue(all(r["trip"] == "" for r in self.snapshot["transactions"]))


class ItIsDeterministic(unittest.TestCase):
    """Two builds over the same parser output differ only in the timestamp,
    otherwise a cached snapshot could never be compared with a fresh one."""

    def test_two_builds_are_byte_identical_apart_from_generated_at(self):
        first = snap.build(CFG, source="csv", csv_dir=csv_dir(), rules_path=RULES)
        second = snap.build(CFG, source="csv", csv_dir=csv_dir(), rules_path=RULES)
        first["meta"]["generated_at"] = second["meta"]["generated_at"] = "fixed"
        self.assertEqual(snap.dumps(first), snap.dumps(second))

    def test_the_written_file_round_trips(self):
        out = Path(tempfile.mkdtemp(prefix="analytics_cache_"))
        TEMPS.append(out)
        built = snap.build(CFG, source="csv", csv_dir=csv_dir(), rules_path=RULES)
        path = snap.save(built, out, day="2026-01-02")
        self.assertEqual(path.name, "snapshot-2026-01-02.json")
        read = snap.load(path)
        self.assertEqual(read["meta"]["file"], path.name)
        del read["meta"]["file"]
        self.assertEqual(read, built)


class ThePortfolioBlock(unittest.TestCase):
    """The Tiger Brokers statements, which are read off disk rather than Notion.

    The parser itself is faked here: this module is about what the snapshot does
    with the statements, and server/finance/parsers/parse_tiger.py has its own tests.
    """

    def cfg_for(self, folder):
        import copy
        cfg = copy.copy(CFG)
        cfg.analytics = {**(getattr(CFG, "analytics", None) or {}),
                         "tiger_folder": str(folder)}
        return cfg

    @contextlib.contextmanager
    def fake_parser(self, statements):
        # G19: the snapshot imports the parser as finance.parsers.parse_tiger,
        # so the fake replaces parse_folder on that module, the one it reads
        from unittest import mock
        from finance.parsers import parse_tiger
        with mock.patch.object(parse_tiger, "parse_folder",
                               lambda folder: list(statements)):
            yield

    def test_a_folder_that_is_not_there_reads_as_no_statements(self):
        block = snap.read_portfolio(self.cfg_for("data/no-such-brokerage"))
        self.assertEqual(block, {"source": "tiger", "folder": "data/no-such-brokerage",
                                 "statements": []})

    def test_an_empty_setting_switches_the_read_off(self):
        with self.fake_parser([{"file": "x.pdf", "period": {"from": "2026-01-01"}}]):
            self.assertEqual(snap.read_portfolio(self.cfg_for(""))["statements"], [])

    def test_a_folder_with_nothing_in_it_reads_as_no_statements(self):
        empty = Path(tempfile.mkdtemp(prefix="tiger_empty_"))
        TEMPS.append(empty)
        block = snap.read_portfolio(self.cfg_for(empty))
        self.assertEqual(block["statements"], [])
        self.assertEqual(block["folder"], str(empty))

    def test_the_statements_come_back_oldest_first(self):
        folder = Path(tempfile.mkdtemp(prefix="tiger_folder_"))
        TEMPS.append(folder)
        newer = {"file": "2026/1-8.pdf", "period": {"from": "2026-01-01"}}
        older = {"file": "2025/1-12.pdf", "period": {"from": "2025-01-01"}}
        with self.fake_parser([newer, older]):
            block = snap.read_portfolio(self.cfg_for(folder))
        self.assertEqual([st["file"] for st in block["statements"]],
                         ["2025/1-12.pdf", "2026/1-8.pdf"])

    def test_a_built_snapshot_carries_the_block_and_lists_the_files(self):
        folder = Path(tempfile.mkdtemp(prefix="tiger_build_"))
        TEMPS.append(folder)
        rows = [{"file": "2025/1-12.pdf", "period": {"from": "2025-01-01"}}]
        with self.fake_parser(rows):
            built = snap.build(self.cfg_for(folder), source="csv",
                               csv_dir=csv_dir(), rules_path=RULES)
        self.assertEqual(built["portfolio"]["statements"], rows)
        self.assertEqual(built["portfolio"]["folder"], str(folder))
        self.assertEqual(built["meta"]["portfolio_files"], ["2025/1-12.pdf"])

    def test_a_build_with_no_statements_still_carries_an_empty_block(self):
        built = snap.build(self.cfg_for("data/no-such-brokerage"), source="csv",
                           csv_dir=csv_dir(), rules_path=RULES)
        self.assertEqual(built["portfolio"]["statements"], [])
        self.assertEqual(built["meta"]["portfolio_files"], [])


class TheAnalysePass(unittest.TestCase):
    """What `finance.py analyse --from-csv` does, without the argument parsing."""

    @classmethod
    def setUpClass(cls):
        cls.out = Path(tempfile.mkdtemp(prefix="analytics_run_"))
        TEMPS.append(cls.out)
        cls.snapshot, cls.results = analytics_run.analyse(
            CFG, source="csv", csv_dir=csv_dir(), out_dir=cls.out,
            day="2026-01-03", echo=lambda *a: None)

    def test_every_stat_ran_and_wrote_a_dated_file(self):
        self.assertEqual(len(self.results), len(stats.MODULE_NAMES))
        for stat_id in self.results:
            path = self.out / "results" / f"{stat_id}-2026-01-03.json"
            self.assertTrue(path.exists(), stat_id)

    def test_latest_points_at_bare_filenames(self):
        latest = json.loads((self.out / "latest.json").read_text())
        self.assertEqual(latest["snapshot"], "snapshot-2026-01-03.json")
        self.assertEqual(sorted(latest["results"]), sorted(self.results))
        for name in latest["results"].values():
            self.assertNotIn("/", name)
            self.assertTrue((self.out / "results" / name).exists())

    def test_a_second_csv_pass_rebuilds_rather_than_reusing(self):
        # the CSV folder is the input and has to pass the block checks every
        # time, so today's CSV snapshot on disk is never taken in its place
        calls = []
        analytics_run.analyse(CFG, source="csv", csv_dir=csv_dir(),
                              out_dir=self.out, day="2026-01-03",
                              echo=calls.append)
        self.assertFalse(any("reusing" in line for line in calls))
        self.assertTrue(any("building from csv" in line for line in calls))

    def test_only_runs_the_named_stats(self):
        out = Path(tempfile.mkdtemp(prefix="analytics_only_"))
        TEMPS.append(out)
        _, results = analytics_run.analyse(
            CFG, source="csv", csv_dir=csv_dir(), out_dir=out, day="2026-01-04",
            only=("data-quality",), echo=lambda *a: None)
        self.assertEqual(list(results), ["data-quality"])

    def test_an_unknown_only_id_stops_the_run_before_anything_is_built(self):
        """A typo used to run every stat, or none, and say nothing."""
        out = Path(tempfile.mkdtemp(prefix="analytics_unknown_"))
        TEMPS.append(out)
        with self.assertRaises(analytics_run.UnknownStat) as caught:
            analytics_run.analyse(CFG, source="csv", csv_dir=csv_dir(),
                                  out_dir=out, day="2026-01-05",
                                  only=("data-quality", "forward_view"),
                                  echo=lambda *a: None)
        self.assertIn("forward_view", str(caught.exception))
        self.assertFalse(list(out.iterdir()))

    def test_the_command_turns_that_into_exit_code_three(self):
        args = argparse.Namespace(from_csv=None, refresh=False, out=str(self.out),
                                  only=["not-a-stat"])
        said = io.StringIO()
        with contextlib.redirect_stderr(said):
            self.assertEqual(cli().cmd_analyse(CFG, args), cli().ENVIRONMENT)
        self.assertIn("not-a-stat", said.getvalue())

    def test_the_stats_agree_with_the_snapshot_they_were_given(self):
        quality = self.results["data-quality"]["data"]["counts"]
        self.assertEqual(quality["rows"], len(self.snapshot["transactions"]))
        cashflow = self.results["monthly-cashflow"]["data"]["months"]
        self.assertEqual(cashflow[-1]["month"],
                         max(r["month"] for r in self.snapshot["transactions"]))


class TheTwoDoorsIntoOneSnapshot(unittest.TestCase):
    """`build_from_notion` reads the eight databases and flattens them.
    `build_from_pages` flattens pages that were read elsewhere, which is how
    `finance.py sync` writes today's snapshot out of its own run. There is one
    flattening under both, so the two have to agree on every byte.
    """

    # (data source, {property: type}) for the seed below, one page each
    TYPES = {
        "categories": {"Name": "title", "Bucket": "select", "Group": "select",
                       "Need class": "select", "Sort order": "number"},
        "payees": {"Name": "title", "Kind": "select", "Country": "select",
                   "Default category": "relation", "Recurring": "checkbox",
                   "Account no.": "rich_text"},
        "accounts": {"Name": "title", "Account no.": "rich_text",
                     "Institution": "select", "Class": "select", "Subclass": "select",
                     "Base currency": "select", "Statements from": "date",
                     "Statements to": "date", "Data coverage": "select"},
        "cards": {"Name": "title", "Last 4": "rich_text", "Network": "select",
                  "Type": "select", "State": "select"},
        "period_balances": {"Name": "title", "Account": "relation",
                            "Period end": "date", "Currency": "select",
                            "Opening balance": "number", "Closing balance": "number",
                            "Total in": "number", "Total out": "number",
                            "FX rate to SGD": "number"},
        "trips": {"Name": "title", "Dates": "date_range", "Countries": "multi_select"},
        "budgets": {"Name": "title", "Category": "relation", "Month": "rich_text",
                    "Actual": "number"},
        "transactions": {"Txn key": "rich_text", "Txn date": "date", "Amount": "number",
                         "Currency": "select", "Account": "relation",
                         "Bank txn type": "select", "Direction": "select",
                         "Flow class": "select", "Merchant raw": "rich_text",
                         "Merchant country": "select", "Counterparty": "rich_text",
                         "Memo": "rich_text", "Reference": "rich_text",
                         "Payee": "relation", "Category": "relation",
                         "Card": "relation", "Trip": "relation", "Period": "relation"},
    }

    @classmethod
    def setUpClass(cls):
        """One page in each of the eight, related the way the real rows are: a
        transaction pointing at its account, period, payee, category, card and
        trip."""
        from fake_notion import CFG as FIXTURE_CFG
        from fake_notion import FakeNotion
        cls.cfg = FIXTURE_CFG
        cls.client = FakeNotion()
        cls.pages = {}
        cls.add("categories", {"Name": "Groceries", "Bucket": "Living", "Group": "Food",
                               "Need class": "Need", "Sort order": 3.0})
        cls.add("accounts", {"Name": "POSB Everyday", "Account no.": "222-22222-2",
                             "Institution": "POSB", "Class": "Cash",
                             "Subclass": "Savings", "Base currency": "SGD",
                             "Statements from": "2026-01-01",
                             "Statements to": "2026-01-31", "Data coverage": "Full"})
        cls.add("cards", {"Name": "DBS Mastercard Debit", "Last 4": "1234",
                          "Network": "Mastercard", "Type": "Debit", "State": "Active"})
        cls.add("trips", {"Name": "Kuala Lumpur, Jan 2026",
                          "Dates": ("2026-01-10", "2026-01-12"),
                          "Countries": ["Malaysia"]})
        ids = {name: page["id"] for name, page in cls.pages.items()}
        cls.add("payees", {"Name": "FairPrice", "Kind": "Merchant", "Country": "SGP",
                           "Default category": [ids["categories"]],
                           "Recurring": False, "Account no.": ""})
        cls.add("period_balances",
                {"Name": "222-22222-2 · SGD · 2026-01", "Account": [ids["accounts"]],
                 "Period end": "2026-01-31", "Currency": "SGD",
                 "Opening balance": 1001.0, "Closing balance": 988.7,
                 "Total in": 0.0, "Total out": 12.3, "FX rate to SGD": None})
        cls.add("budgets", {"Name": "Groceries · 2026-01",
                            "Category": [ids["categories"]], "Month": "2026-01",
                            "Actual": 12.3})
        cls.add("transactions",
                {"Txn key": "aaaa1111", "Txn date": "2026-01-05", "Amount": -12.3,
                 "Currency": "SGD", "Account": [ids["accounts"]],
                 "Bank txn type": "Debit Card Transaction", "Direction": "Out",
                 "Flow class": "External", "Merchant raw": "FAIRPRICE",
                 "Merchant country": "SGP", "Counterparty": "", "Memo": "",
                 "Reference": "MT2600001", "Category": [ids["categories"]],
                 "Card": [ids["cards"]], "Trip": [ids["trips"]],
                 "Payee": [cls.pages["payees"]["id"]],
                 "Period": [cls.pages["period_balances"]["id"]]})

    @classmethod
    def add(cls, ds_name, props):
        cls.pages[ds_name] = cls.client.create(
            cls.cfg.ds(ds_name), notion.to_api(props, cls.TYPES[ds_name]))
        return cls.pages[ds_name]

    def fixed(self, snapshot):
        snapshot["meta"]["generated_at"] = "fixed"
        return snap.dumps(snapshot)

    def test_read_pages_returns_all_eight_lists(self):
        pages = snap.read_pages(self.cfg, self.client)
        self.assertEqual(sorted(pages), sorted(snap.DATABASES))
        self.assertEqual({name: len(rows) for name, rows in pages.items()},
                         {name: 1 for name in snap.DATABASES})

    def test_the_two_doors_produce_the_same_bytes(self):
        read = self.fixed(snap.build_from_notion(self.cfg, client=self.client))
        given = self.fixed(snap.build_from_pages(
            self.cfg, snap.read_pages(self.cfg, self.client)))
        self.assertEqual(read, given)

    def test_the_order_the_pages_arrive_in_does_not_reach_the_file(self):
        """The sync hands over pages in its own read order, which is not the
        order a fresh read gives, so nothing in the file may depend on it."""
        from fake_notion import FakeNotion
        spare = FakeNotion()                       # so the shared seed is untouched
        extra = spare.create(self.cfg.ds("transactions"), notion.to_api(
            {"Txn key": "bbbb2222", "Txn date": "2026-01-04", "Amount": -1.0,
             "Currency": "SGD"}, self.TYPES["transactions"]))
        pages = snap.read_pages(self.cfg, self.client)
        pages["transactions"] = [extra, self.pages["transactions"]]
        forwards = self.fixed(snap.build_from_pages(self.cfg, pages))
        pages["transactions"] = list(reversed(pages["transactions"]))
        self.assertEqual(forwards, self.fixed(snap.build_from_pages(self.cfg, pages)))

    def test_the_sync_writes_the_same_snapshot_from_the_state_it_holds(self):
        """What `finance.py sync` leaves behind has to be what `analyse` would
        have built for itself from the same Notion."""
        out = Path(tempfile.mkdtemp(prefix="sync_snapshot_"))
        TEMPS.append(out)
        state = sync.read_state(self.cfg, self.client, with_options=False)
        path, rows = sync.write_snapshot(self.cfg, self.client, state, cache=out,
                                         day="2026-01-06",
                                         categories_path=out / "categories.json")
        self.assertEqual(path.name, "snapshot-2026-01-06.json")
        self.assertEqual(rows, 1)
        written = snap.load(path)
        del written["meta"]["file"]
        self.assertEqual(self.fixed(written),
                         self.fixed(snap.build_from_notion(self.cfg, client=self.client)))

    def test_it_reads_only_the_four_databases_the_run_does_not_hold(self):
        state = sync.read_state(self.cfg, self.client, with_options=False)
        ids = {self.cfg.ds(name): name for name in snap.DATABASES}
        read = []
        client = self.client

        class Counting:
            def __getattr__(self, name):
                return getattr(client, name)

            def query_all(self, ds_id, filter_=None):
                read.append(ids[ds_id])
                return client.query_all(ds_id, filter_)

        sync.snapshot_pages(self.cfg, Counting(), state)
        self.assertEqual(sorted(read), sorted(sync.SNAPSHOT_READS))

    def test_the_offline_categories_copy_is_still_refreshed(self):
        """`analyse` used to be the only pass that saw the Categories rows, and
        it refreshed server/analytics/categories.json from them. It reuses the sync's
        snapshot now, so the sync has to do it instead."""
        out = Path(tempfile.mkdtemp(prefix="sync_categories_"))
        TEMPS.append(out)
        fallback = out / "categories.json"
        state = sync.read_state(self.cfg, self.client, with_options=False)
        sync.write_snapshot(self.cfg, self.client, state, cache=out, day="2026-01-07",
                            categories_path=fallback)
        self.assertEqual([c["name"] for c in snap.load_categories_fallback(fallback)],
                         ["Groceries"])


if __name__ == "__main__":
    unittest.main()
