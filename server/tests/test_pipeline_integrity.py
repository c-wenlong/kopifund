"""Every path checks the balance before it uses a row.

Each class here is one way a row, a statement or a cache file used to reach
the analytics without the check a normal run applies. Every value is invented.
"""
import isolate  # noqa: F401  first: no test writes the real record or cache
import argparse
import contextlib
import copy
import csv
import dataclasses
import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent

import analytics_fixture as fx                                  # noqa: E402
from fake_notion import CFG                                     # noqa: E402
from finance.parsers.parse_statements import BAL_COLS, TXN_COLS                 # noqa: E402

from analytics import publish                                   # noqa: E402
from analytics import run as analytics_run                      # noqa: E402
from finance import snapshot as snap                          # noqa: E402
from analytics.stats import common, savings_runway             # noqa: E402
from analytics.stats import cpf as cpf_stat                     # noqa: E402
from finance import atomic, db, sync                            # noqa: E402

REPO = HERE.parent
POSB, WISE, PAYLAH = "222-22222-2", "666-66666-6", "3333333333333333"


def cli():
    """finance.py, loaded by path: the `finance` package next to it wins the
    import by name."""
    spec = importlib.util.spec_from_file_location("finance_cli", REPO / "finance.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@contextlib.contextmanager
def private_data_folder():
    """A temporary KOPIFUND_DATA, so nothing here reads or writes the real one."""
    with tempfile.TemporaryDirectory() as d, \
            mock.patch.dict(os.environ, {"KOPIFUND_DATA": d}):
        yield Path(d)


def write_csvs(folder, blocks, rows):
    """A parser run's two CSVs, the way every parser writes them."""
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / "period_balances.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=BAL_COLS, extrasaction="ignore")
        w.writeheader()
        w.writerows(blocks)
    with (folder / "transactions.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=TXN_COLS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def bank_block(**over):
    return {"period": f"{POSB} · SGD · 2026-01", "period_end": "2026-01-31",
            "account": "POSB Everyday Savings Account", "account_no": POSB,
            "currency": "SGD", "opening": "1001.00", "closing": "988.70",
            "total_in": "0.00", "total_out": "12.30", "fx_to_sgd": "",
            "closing_sgd": "988.70", "n_txns": "1", "reconciled": "True", "note": "",
            "source": "data/dbs-statements/posb-everyday/2026/1.pdf", **over}


def bank_row(**over):
    return {"txn_key": "k-bank-1", "date": "2026-01-05",
            "account": "POSB Everyday Savings Account", "account_no": POSB,
            "currency": "SGD", "bank_txn_type": "Debit Card Transaction",
            "direction": "Out", "amount": "-12.30", "flow_class": "External",
            "merchant": "KOPITIAM SGP", "merchant_country": "SGP", "purchase_date": "",
            "card_last4": "", "fx_original": "", "counterparty": "", "reference": "",
            "memo": "", "balance_after": "988.70",
            "source": "data/dbs-statements/posb-everyday/2026/1.pdf", **over}


def wallet_block(month, **over):
    return {"period": f"{WISE} · SGD · {month}", "period_end": f"{month}-28",
            "account": "Wise wallet", "account_no": WISE, "currency": "SGD",
            "opening": "40.00", "closing": "40.00", "total_in": "0.00",
            "total_out": "0.00", "fx_to_sgd": "", "closing_sgd": "40.00", "n_txns": "0",
            "reconciled": "True", "note": "no activity this month",
            "source": "data/wise/2026/export.pdf", **over}


def analyse_args(**over):
    base = dict(from_csv=None, refresh=True, only=None, out=None,
                no_publish=True, dry_run=False)
    return argparse.Namespace(**{**base, **over})


# ---------------------------------------------------------------- 3


class ReusedCsvsPassTheBlockChecks(unittest.TestCase):
    """A parser writes its CSVs before it exits on a failed balance check, so
    `rollup --csv` and `analyse --from-csv` used to load a block whose
    `reconciled` was False."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.csv = Path(self.tmp.name) / "parsed"
        write_csvs(self.csv, [bank_block(reconciled="False")], [bank_row()])
        self.out = Path(self.tmp.name) / "cache"

    def test_the_snapshot_build_refuses_an_unreconciled_block(self):
        with private_data_folder():
            with self.assertRaises(snap.UncheckedStatements) as e:
                snap.build_from_csv(CFG, self.csv)
        self.assertEqual([p.code for p in e.exception.problems], ["block-not-reconciled"])

    def test_analyse_from_csv_exits_one_and_writes_nothing(self):
        said = io.StringIO()
        with private_data_folder(), contextlib.redirect_stderr(said), \
                contextlib.redirect_stdout(io.StringIO()):
            code = cli().cmd_analyse(CFG, analyse_args(from_csv=str(self.csv),
                                                       out=str(self.out)))
        self.assertEqual(code, 1)
        self.assertIn("block-not-reconciled", said.getvalue())
        self.assertFalse(self.out.exists() and any(self.out.rglob("*.json")))

    def test_rollup_from_csv_exits_one_before_it_reaches_notion(self):
        args = argparse.Namespace(rules=None, csv=str(self.csv), dry_run=True)
        module = cli()
        said = io.StringIO()
        with private_data_folder(), contextlib.redirect_stdout(said), \
                mock.patch.object(module, "client_for",
                                  side_effect=AssertionError("Notion was reached")):
            self.assertEqual(module.cmd_rollup(CFG, args), module.DOCTOR_FAILED)
        self.assertIn("block-not-reconciled", said.getvalue())

    def run_from_csv(self, command, **over):
        """One of the commands that reuse parser CSVs, with Notion replaced by
        a client_for that fails the test if it is ever reached."""
        args = argparse.Namespace(**{"rules": None, "csv": str(self.csv), "dry_run": True,
                                     "prune": False, **over})
        module = cli()
        said = io.StringIO()
        with private_data_folder(), contextlib.redirect_stdout(said), \
                mock.patch.object(module, "client_for",
                                  side_effect=AssertionError("Notion was reached")):
            code = getattr(module, command)(CFG, args)
        return module, code, said.getvalue()

    def test_payees_add_options_and_verify_from_csv_exit_one_before_notion(self):
        """The three other commands that take --csv used to skip the block
        checks altogether: payees counted unchecked rows into a default
        category, add-options added their values, verify compared Notion
        with them."""
        for command in ("cmd_payees", "cmd_add_options", "cmd_verify"):
            with self.subTest(command):
                module, code, said = self.run_from_csv(command)
                self.assertEqual(code, module.DOCTOR_FAILED)
                self.assertIn("block-not-reconciled", said)
                self.assertIn("nothing written", said)

    def test_a_row_with_no_block_is_refused_by_every_csv_command(self):
        write_csvs(self.csv, [bank_block()], [bank_row(account_no="999-99999-9")])
        for command in ("cmd_payees", "cmd_add_options", "cmd_verify", "cmd_rollup"):
            with self.subTest(command):
                module, code, said = self.run_from_csv(command)
                self.assertEqual(code, module.DOCTOR_FAILED)
                self.assertIn("row-without-block", said)

    def test_a_parser_stopped_by_a_duplicate_period_stops_every_command(self):
        """A parser that stops on two statements for one month writes no CSV,
        so the block checks see only the other parsers' rows; the problem it
        reported is all that says the run is partial."""
        from finance.parsers.parse_statements import report_duplicate_periods
        said = io.StringIO()
        with contextlib.redirect_stderr(said), self.assertRaises(SystemExit) as stop:
            report_duplicate_periods([dict(account_no=POSB, currency="SGD",
                                           statement_date="31 Jan 2026", source=src)
                                      for src in ("1.pdf", "1 (1).pdf")])
        failed = mock.Mock(returncode=1, stdout="", stderr=f"{said.getvalue()}{stop.exception}\n")
        for command in ("cmd_payees", "cmd_add_options", "cmd_verify", "cmd_rollup"):
            with self.subTest(command), \
                    mock.patch("finance.sync.subprocess.run", return_value=failed):
                module, code, out = self.run_from_csv(command, csv=None)
                self.assertEqual(code, module.DOCTOR_FAILED)
                self.assertIn("duplicate-period", out)

    def test_a_clean_csv_gets_past_the_checks_to_notion(self):
        write_csvs(self.csv, [bank_block()], [bank_row()])
        for command in ("cmd_payees", "cmd_add_options", "cmd_verify"):
            with self.subTest(command), self.assertRaisesRegex(AssertionError,
                                                               "Notion was reached"):
                self.run_from_csv(command)

    def test_a_clean_csv_still_builds(self):
        write_csvs(self.csv, [bank_block()], [bank_row()])
        with private_data_folder():
            built = snap.build_from_csv(CFG, self.csv)
        self.assertEqual([t["key"] for t in built["transactions"]], ["k-bank-1"])

    def test_a_foreign_row_with_no_rate_has_no_sgd_value_in_a_csv_snapshot(self):
        """As the record's v_txn: a NOK row whose block printed no FX rate is
        unknown in SGD, never NOK counted one to one (audit, snapshot.py)."""
        nok = wallet_block("2026-01", period=f"{WISE} · NOK · 2026-01", currency="NOK",
                           opening="1500.00", closing="0.00", total_out="1500.00", n_txns="1",
                           closing_sgd="", note="")
        spent = bank_row(txn_key="k-nok-1", account="Wise wallet", account_no=WISE, currency="NOK",
                         amount="-1500.00", balance_after="0.00", source=nok["source"])
        write_csvs(self.csv, [bank_block(), nok], [bank_row(), spent])
        with private_data_folder():
            built = snap.build_from_csv(CFG, self.csv)
        sgd = {t["key"]: t["amount_sgd_minor"] for t in built["transactions"]}
        self.assertEqual(sgd, {"k-bank-1": -1230, "k-nok-1": None})
        self.assertTrue(any("1 rows sit in a block with no FX rate" in n for n in built["meta"]["notes"]),
                        built["meta"]["notes"])

    def test_todays_csv_snapshot_is_never_reused_in_place_of_the_folder_given(self):
        """A CSV snapshot already on disk for today used to be reused before
        the checks ran, and reuse never asked which folder it came from."""
        module = cli()

        def analyse(folder):
            said = io.StringIO()
            with contextlib.redirect_stderr(said), contextlib.redirect_stdout(said):
                code = module.cmd_analyse(CFG, analyse_args(from_csv=str(folder),
                                                            out=str(self.out), refresh=False))
            return code, said.getvalue()

        clean = Path(self.tmp.name) / "clean"
        write_csvs(clean, [bank_block()], [bank_row()])
        other = Path(self.tmp.name) / "other"
        write_csvs(other, [bank_block(total_out="20.00", closing="981.00",
                                      closing_sgd="981.00")],
                   [bank_row(txn_key="k-bank-2", amount="-20.00", balance_after="981.00")])
        with private_data_folder():
            code, _ = analyse(clean)
            self.assertEqual(code, module.CLEAN)
            on_disk = sorted(self.out.glob("snapshot-*.json"))
            self.assertEqual(len(on_disk), 1)
            self.assertEqual(snap.load(on_disk[0])["meta"]["source"], "csv")
            # the failed parser output is checked, not swapped for today's snapshot
            code, said = analyse(self.csv)
            self.assertEqual(code, module.DOCTOR_FAILED)
            self.assertIn("block-not-reconciled", said)
            self.assertNotIn("reusing", said)
            # another folder is read, not the one today's snapshot came from
            code, said = analyse(other)
            self.assertEqual(code, module.CLEAN)
            self.assertNotIn("reusing", said)
            built = snap.load(sorted(self.out.glob("snapshot-*.json"))[0])
        self.assertEqual([t["key"] for t in built["transactions"]], ["k-bank-2"])


# ---------------------------------------------------------------- 4


class WalletsAreKnownWithoutAnAccountsRow(unittest.TestCase):
    """Coverage tells an idle wallet from a missing statement by subclass
    Wallet. A CSV snapshot had None on every account, and so does an account
    the loader created, so an idle wallet read as a hole."""

    def test_the_config_names_every_wallet_kind_and_no_bank(self):
        wallets = snap.wallet_account_nos(CFG)
        self.assertTrue({WISE, PAYLAH, "Y-4444444444", "555555555555555"} <= wallets)
        self.assertNotIn(POSB, wallets)
        self.assertNotIn("111-11111-1", wallets)

    def test_a_folder_with_no_account_number_is_matched_by_its_statements(self):
        cfg = copy.copy(CFG)
        cfg.folders = [dataclasses.replace(f, account_no="") for f in CFG.folders]
        self.assertEqual(snap.wallet_account_nos(cfg), set())
        blocks = [{"account_no": "W-9", "source": "data/wise/2026/export.pdf"},
                  {"account_no": "B-1", "source": "data/dbs-statements/posb-everyday/1.pdf"},
                  {"account_no": "P-7", "source": "elsewhere/1.pdf",
                   "account": "DBS PayLah! wallet"}]
        self.assertEqual(snap.wallet_account_nos(cfg, blocks), {"W-9", "P-7"})

    def test_a_csv_snapshot_marks_the_wallet_and_coverage_lets_it_idle(self):
        with tempfile.TemporaryDirectory() as tmp, private_data_folder():
            folder = Path(tmp)
            write_csvs(folder, [bank_block(), bank_block(
                period=f"{POSB} · SGD · 2026-02", period_end="2026-02-28",
                opening="988.70", closing="988.70", total_out="0.00", n_txns="0",
                note="", source="data/dbs-statements/posb-everyday/2026/2.pdf"),
                wallet_block("2026-01")], [bank_row()])
            built = snap.build_from_csv(CFG, folder)
        subclass = {a["account_no"]: a["subclass"] for a in built["accounts"]}
        self.assertEqual(subclass, {POSB: None, WISE: "Wallet"})
        cov = common.coverage(built)
        self.assertEqual(cov["2026-02"]["missing"], [])
        self.assertFalse(cov["2026-02"]["partial"])

    def test_the_record_marks_a_loader_created_wallet(self):
        conn = db.connect(":memory:")
        self.addCleanup(conn.close)
        db._ensure_account(conn, "Wise wallet", WISE, "SGD")
        db._ensure_account(conn, "POSB", POSB, "SGD")
        conn.execute("INSERT INTO account(name, account_no, subclass) VALUES (?,?,?)",
                     ("Own label", "X-1", "Cash"))
        with private_data_folder():
            plain = db.snapshot(conn)
            marked = db.snapshot(conn, wallets={WISE, "X-1"})
        self.assertEqual({a["account_no"]: a["subclass"] for a in plain["accounts"]},
                         {WISE: None, POSB: None, "X-1": "Cash"})
        # the user's own subclass always wins; only a blank one is filled in
        self.assertEqual({a["account_no"]: a["subclass"] for a in marked["accounts"]},
                         {WISE: "Wallet", POSB: None, "X-1": "Cash"})


# ---------------------------------------------------------------- 2


class CpfYearsThatDoNotReconcile(unittest.TestCase):
    """The analytics read CPF through parse_folder, never main(), so an
    unbalanced year reached the snapshot. Now it is rejected and named."""

    def balanced(self):
        """The fixture's three years, each given one transaction row that
        carries it from its opening to its closing."""
        stmts = []
        for st in fx.cpf_statements():
            move = {k: round(st["closing"][k] - st["opening"][k], 2) for k in ("oa", "sa", "ma")}
            stmts.append({**st, "transactions": [
                {"date": f"{st['year']}-06-01", "code": "CON", "for_month": None,
                 "ref": "", **move}]})
        return stmts

    def unbalanced(self):
        """balanced(), then 2024 given a second row of 5.00 that its closing
        does not show."""
        stmts = self.balanced()
        stmts[1]["transactions"] = stmts[1]["transactions"] + [
            {"date": "2024-03-01", "code": "CON", "for_month": None, "ref": "",
             "oa": 5.0, "sa": 0.0, "ma": 0.0}]
        return stmts

    def test_the_stat_names_the_year_instead_of_counting_it(self):
        block = snap.checked_cpf(self.unbalanced())
        self.assertEqual([s["year"] for s in block["statements"]], [2023, 2025])
        self.assertEqual(block["rejected"], [{"year": 2024, "file": "2024/2024.pdf",
                                             "off": {"oa": 5.0, "sa": 0.0, "ma": 0.0}}])
        with fx.settings():
            res = cpf_stat.run(fx.snapshot(cpf=block))
        self.assertIn("The 2024 statement (2024/2024.pdf) does not reconcile",
                      res["findings"][0])
        self.assertEqual([y["year"] for y in res["data"]["years"]], [2023, 2025])

    def test_a_record_that_stored_an_unbalanced_year_rejects_it_on_read(self):
        conn = db.connect(":memory:")
        self.addCleanup(conn.close)
        db.load_broker_and_cpf(conn, None, {"statements": self.unbalanced()})
        with private_data_folder():
            built = db.snapshot(conn)
        self.assertEqual([s["year"] for s in built["cpf"]["statements"]], [2023, 2025])
        self.assertEqual([r["year"] for r in built["cpf"]["rejected"]], [2024])

    def test_a_load_keeps_the_rejected_year_in_the_record(self):
        """`load` stored only the statements read_cpf passed, so a SQLite
        snapshot could not name a year it rejected, and a year stored valid by
        an earlier load kept showing its old figures beside nothing."""
        from finance.parsers import parse_cpf
        from finance import local
        from types import SimpleNamespace
        conn = db.connect(":memory:")
        self.addCleanup(conn.close)

        def load(stmts):
            good, bad = parse_cpf.split_reconciled(stmts)
            block = {"source": "cpf", "folder": "statements/cpf", "statements": good,
                     "rejected": bad}
            with private_data_folder() as root, \
                    mock.patch.object(snap, "read_portfolio", return_value=None), \
                    mock.patch.object(snap, "read_cpf", return_value=block), \
                    contextlib.redirect_stderr(io.StringIO()) as said:
                cfg = SimpleNamespace(root=root, folders=[], wallet_accounts=lambda: set())
                local.load(cfg, conn, sync.Parsed())
                return db.snapshot(conn), said.getvalue()

        # an earlier load stored 2024 when it still added up
        first, _ = load(self.balanced())
        self.assertEqual([s["year"] for s in first["cpf"]["statements"]], [2023, 2024, 2025])
        self.assertEqual(first["cpf"]["rejected"], [])
        # then the 2024 file on disk stops reconciling
        built, said = load(self.unbalanced())
        self.assertIn("CPF 2024 (2024/2024.pdf) does not reconcile", said)
        self.assertEqual(built["cpf"]["rejected"], [{"year": 2024, "file": "2024/2024.pdf",
                                                    "off": {"oa": 5.0, "sa": 0.0, "ma": 0.0}}])
        # the older stored 2024 figures are not shown as if they were valid
        self.assertEqual([s["year"] for s in built["cpf"]["statements"]], [2023, 2025])
        self.assertEqual(conn.execute(
            "SELECT off_oa_minor FROM cpf_rejected WHERE year = 2024").fetchone()[0], 500)
        with fx.settings():
            res = cpf_stat.run(fx.snapshot(cpf=built["cpf"]))
        self.assertIn("The 2024 statement (2024/2024.pdf) does not reconcile",
                      res["findings"][0])
        self.assertEqual([y["year"] for y in res["data"]["years"]], [2023, 2025])
        # fixed on disk, the next load clears the rejection and shows the year
        fixed, _ = load(self.balanced())
        self.assertEqual([s["year"] for s in fixed["cpf"]["statements"]], [2023, 2024, 2025])
        self.assertEqual(fixed["cpf"]["rejected"], [])

    def test_read_cpf_carries_the_rejected_years(self):
        from finance.parsers import parse_cpf
        stmts = self.unbalanced()

        def fake(folder, write_text=True, rejected=None):
            good, bad = parse_cpf.split_reconciled(stmts)
            rejected.extend(bad)
            return good
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(parse_cpf, "parse_folder", side_effect=fake):
            cfg = copy.copy(CFG)
            cfg.analytics = {"cpf_folder": tmp}
            block = snap.read_cpf(cfg)
        self.assertEqual([s["year"] for s in block["statements"]], [2023, 2025])
        self.assertEqual([r["year"] for r in block["rejected"]], [2024])


# ---------------------------------------------------------------- 5


class TheMonthRangeRunsToTheLastBalance(unittest.TestCase):
    """month_range ended at the last transaction, so savings-runway never
    visited a later month and could miss an account whose only statements
    came after it."""

    def snapshot(self):
        s = fx.snapshot()
        s["periods"] = s["periods"] + [
            {"name": "W2 · SGD · 2026-02", "account": "Late wallet", "account_no": "W2",
             "period_end": "2026-02-28", "currency": "SGD", "opening_minor": 7_500,
             "closing_minor": 7_500, "total_in_minor": 0, "total_out_minor": 0, "fx_to_sgd": None,
             "closing_sgd_minor": 7_500, "reconciled": True, "has_balances": True},
            # a block with no balance holds no figure and stretches nothing
            {"name": "W2 · SGD · 2026-05", "account": "Late wallet", "account_no": "W2",
             "period_end": "2026-05-28", "currency": "SGD", "opening_minor": None,
             "closing_minor": None, "total_in_minor": 0, "total_out_minor": 0, "fx_to_sgd": None,
             "closing_sgd_minor": None, "reconciled": True, "has_balances": False}]
        s["accounts"] = s["accounts"] + [
            {"name": "Late wallet", "account_no": "W2", "institution": "Test Wallet",
             "class": "Asset", "subclass": "Wallet", "base_currency": "SGD",
             "statements_from": "2026-02-28", "statements_to": "2026-02-28",
             "data_coverage": "Full", "txn_count": 0}]
        return s

    def test_the_balance_range_ends_at_the_last_balance_block(self):
        s = self.snapshot()
        self.assertEqual(common.balance_month_range(s)[-1], "2026-02")
        self.assertEqual(common.balance_month_range(s)[0], fx.MONTHS[0])
        # the flow range is untouched: a month with balances and no rows is
        # not a month of zero spending in any phase average
        self.assertEqual(common.month_range(s)[-1], "2025-12")
        self.assertEqual(common.last_month(s), "2025-12")
        self.assertIn("2026-02", common.coverage(s))

    def test_savings_runway_counts_the_late_account(self):
        with fx.settings():
            res = savings_runway.run(self.snapshot())
            before = savings_runway.run(fx.snapshot())
        accounts = {a["account"]: a for a in res["data"]["accounts"]}
        self.assertEqual((accounts["Late wallet"]["last_balance_minor"], accounts["Late wallet"]["as_of"]),
                         (7500, "2026-02"))
        self.assertEqual(res["data"]["summary"]["latest_month"], "2026-02")
        self.assertEqual([m["month"] for m in res["data"]["provisional"]],
                         common.months_between("2026-01", "2026-02"))
        # the headline still stops at the last month every bank printed, and
        # the spend rates behind the runway do not move
        for key in ("as_of_complete", "cash_minor", "spend_avg_all_minor", "spend_avg_6m_minor"):
            self.assertEqual(res["data"]["summary"][key], before["data"]["summary"][key], key)

    def test_a_balance_only_month_is_no_month_of_zero_spending(self):
        """A month every bank printed a statement in but no row fell in is a
        complete, non-partial month on the balance walk; counted in the spend
        rates its zero pulled both down and stretched the runway."""
        s = fx.snapshot()
        s["periods"] = s["periods"] + [
            {"name": f"{fx.MAIN} · SGD · 2026-01", "account": fx.MAIN_NAME,
             "account_no": fx.MAIN, "period_end": "2026-01-28", "currency": "SGD",
             "opening_minor": 210_000, "closing_minor": 210_000, "total_in_minor": 0,
             "total_out_minor": 0, "fx_to_sgd": None, "closing_sgd_minor": 210_000, "reconciled": True,
             "has_balances": True}]
        self.assertFalse(common.coverage(s)["2026-01"]["partial"])
        self.assertNotIn("2026-01", common.month_range(s))
        with fx.settings():
            res = savings_runway.run(s)
            before = savings_runway.run(fx.snapshot())
        summary = res["data"]["summary"]
        # the balance reaches the new month: it is the headline month now
        self.assertEqual(summary["as_of_complete"], "2026-01")
        self.assertEqual(res["data"]["months"][-1]["spend_minor"], 0)
        # but the spend rates are the flow months' alone
        for key in ("spend_avg_all_minor", "spend_avg_6m_minor"):
            self.assertEqual(summary[key], before["data"]["summary"][key], key)
        self.assertEqual(summary["runway_6m"],
                         round(summary["cash_minor"] / before["data"]["summary"]["spend_avg_6m_minor"], 1))


# ---------------------------------------------------------------- 6


class CacheFilesAreReplacedWhole(unittest.TestCase):
    """The app reads the cache while `analyse` writes it; a file written in
    place can be read half-written."""

    def test_a_write_that_fails_leaves_the_old_file_and_no_temp(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "latest.json"
            path.write_text('{"old": true}\n')
            with mock.patch.object(atomic.os, "fsync", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    atomic.write_private(path, '{"new": true}\n')
            self.assertEqual(path.read_text(), '{"old": true}\n')
            self.assertEqual([p.name for p in Path(tmp).iterdir()], ["latest.json"])

    def test_snapshot_latest_and_results_all_go_through_a_replace(self):
        replaced = []
        real = os.replace

        def spy(src, dst):
            self.assertTrue(Path(src).name.startswith("."))
            self.assertEqual(Path(src).parent, Path(dst).parent)
            replaced.append(Path(dst).name)
            real(src, dst)
        with tempfile.TemporaryDirectory() as tmp, fx.settings(), \
                mock.patch.object(atomic.os, "replace", side_effect=spy):
            snap.save(fx.snapshot(), tmp, day="2026-01-02")
            analytics_run.run_stats(fx.snapshot(), tmp, only=("travel",),
                                    benchmarks=fx.BENCHMARKS, day="2026-01-02",
                                    echo=lambda *a: None)
            json.loads((Path(tmp) / "latest.json").read_text())
        self.assertEqual(replaced, ["snapshot-2026-01-02.json", "latest.json",
                                    "travel-2026-01-02.json", "latest.json"])


# ---------------------------------------------------------------- 7


class OnlyPublishesFromEveryResult(unittest.TestCase):
    """`analyse --only cpf` published the CPF rows numbered from one, on top of
    the Cash row that already had Order one."""

    def test_the_metrics_keep_the_order_a_full_run_gives_them(self):
        s = fx.snapshot(portfolio=fx.portfolio(), cpf=fx.cpf())
        cfg = copy.copy(CFG)
        cfg.data_sources = {**CFG.data_sources, "metrics": "m" * 32}
        module = cli()
        with tempfile.TemporaryDirectory() as tmp, fx.settings():
            full = analytics_run.run_stats(s, tmp, benchmarks=fx.BENCHMARKS,
                                           day="2026-01-02", echo=lambda *a: None)
            subset = {"cpf": full["cpf"]}
            seen = {}
            with mock.patch.object(analytics_run, "analyse", return_value=(s, subset)), \
                    mock.patch.object(module, "client_for", return_value=None), \
                    mock.patch.object(publish, "publish",
                                      side_effect=lambda c, cl, results, **kw:
                                      seen.update(results)), \
                    contextlib.redirect_stdout(io.StringIO()):
                code = module.cmd_analyse(cfg, analyse_args(only=["cpf"], out=tmp,
                                                            no_publish=False))
            self.assertEqual(code, module.CLEAN)
            order = lambda rows: {r["Name"]: r["Order"] for r in rows}
            want = order(publish.metrics_from(full, s))
            got = order(publish.metrics_from(seen, s))
        self.assertEqual(got, want)
        self.assertEqual(len(set(got.values())), len(got))
        # the subset alone, which used to be numbered from one, now keeps the
        # orders a full run gives its rows
        alone = order(publish.metrics_from(subset, s))
        self.assertEqual(alone, {k: want[k] for k in alone})
        self.assertNotIn(want["Cash, all accounts"], alone.values())

    def test_a_result_gone_from_the_cache_shifts_no_other_row(self):
        """With the savings-runway file deleted, the merged results lack it,
        and the rest used to be numbered from one onto the Cash row's Order,
        which the upsert leaves standing."""
        s = fx.snapshot(portfolio=fx.portfolio(), cpf=fx.cpf())
        cfg = copy.copy(CFG)
        cfg.data_sources = {**CFG.data_sources, "metrics": "m" * 32}
        module = cli()
        with tempfile.TemporaryDirectory() as tmp, fx.settings():
            full = analytics_run.run_stats(s, tmp, benchmarks=fx.BENCHMARKS,
                                           day="2026-01-02", echo=lambda *a: None)
            (Path(tmp) / "results" / "savings-runway-2026-01-02.json").unlink()
            seen = {}
            with mock.patch.object(analytics_run, "analyse",
                                   return_value=(s, {"cpf": full["cpf"]})), \
                    mock.patch.object(module, "client_for", return_value=None), \
                    mock.patch.object(publish, "publish",
                                      side_effect=lambda c, cl, results, **kw:
                                      seen.update(results)), \
                    contextlib.redirect_stdout(io.StringIO()):
                code = module.cmd_analyse(cfg, analyse_args(only=["cpf"], out=tmp,
                                                            no_publish=False))
            self.assertEqual(code, module.CLEAN)
            self.assertNotIn("savings-runway", seen)
            order = lambda rows: {r["Name"]: r["Order"] for r in rows}
            want = order(publish.metrics_from(full, s))
            got = order(publish.metrics_from(seen, s))
        self.assertNotIn("Cash, all accounts", got)
        self.assertEqual(got, {k: want[k] for k in got})
        self.assertNotIn(want["Cash, all accounts"], got.values())


if __name__ == "__main__":
    unittest.main()
