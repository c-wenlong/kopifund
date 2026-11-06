"""server/finance/account_names.py, and every place the app names an account (G13).

The app never shows a full account number: a page or a stat names an account
by its display name and shows at most the last four digits. Every number here
is invented.
"""
import isolate  # noqa: F401  first: no test writes the real record or cache
import contextlib
import copy
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from finance import snapshot as snap
from analytics import stats
from analytics.stats import forward_view, savings_runway
from analytics_fixture import MAIN, WALLET, settings, snapshot
from finance import account_names as an
from finance import api as finance_api
from finance import db, profile
from finance.model import Folder
from test_db import SNAP, block, parsed, row

MAIN_NO, WALLET_NO, BARE_NO = "123-456789-0", "Y-9876543210", "555666777888"
NUMBERS = (MAIN_NO, WALLET_NO, BARE_NO)
FIXTURE_CONFIG = Path(__file__).resolve().parent / "fixtures" / "config.toml"


class Mask(unittest.TestCase):
    def test_keeps_the_last_four_digits(self):
        self.assertEqual(an.mask(MAIN_NO), "•• 7890")
        self.assertEqual(an.mask(WALLET_NO), "•• 3210")
        self.assertEqual(an.mask("4242"), "•• 4242")

    def test_no_number_is_no_mask(self):
        self.assertEqual(an.mask(""), "")
        self.assertEqual(an.mask(None), "")


class DisplayName(unittest.TestCase):
    def test_the_number_is_taken_out_of_the_name(self):
        self.assertEqual(an.display_name(f"Travel wallet · {WALLET_NO}", WALLET_NO), "Travel wallet")
        self.assertEqual(an.display_name(f"Savings ({MAIN_NO})", MAIN_NO), "Savings")
        self.assertEqual(an.display_name(f"Account No. {BARE_NO}"), "Account")

    def test_a_number_the_record_does_not_name_is_taken_out_too(self):
        # a card number in a name, with or without its own account number
        self.assertEqual(an.display_name("Card 4000 1234 5678 9010"), "Card")
        self.assertEqual(an.display_name(f"Joint {MAIN_NO}", ""), "Joint")

    def test_a_short_number_and_a_date_stay(self):
        self.assertEqual(an.display_name("Main Bank · 4242", "999-999999-9"), "Main Bank · 4242")
        self.assertEqual(an.display_name("Trip wallet 2025-06-01"), "Trip wallet 2025-06-01")
        # a short stored number is only taken out as a whole word
        self.assertEqual(an.display_name("Main Bank · A1", "A1"), "Main Bank")
        self.assertEqual(an.display_name("A1 Saver", "1"), "A1 Saver")

    def test_a_bare_number_is_named_by_its_folder_then_its_institution(self):
        folders = [Folder("statements/dbs-multiplier", "dbs", MAIN_NO),
                   Folder("statements/youtrip-sek", "youtrip", WALLET_NO, wallet="SEK"),
                   Folder("statements/joint-saver", "ocbc", BARE_NO)]
        self.assertEqual(an.display_name(MAIN_NO, MAIN_NO, None, folders), "DBS Multiplier")
        self.assertEqual(an.display_name(WALLET_NO, WALLET_NO, None, folders), "YouTrip")
        self.assertEqual(an.display_name(BARE_NO, BARE_NO, None, folders), "Joint Saver")
        self.assertEqual(an.display_name(BARE_NO, BARE_NO, "Test Bank"), "Test Bank")
        self.assertEqual(an.display_name(BARE_NO, BARE_NO), "Account")


class Bank(unittest.TestCase):
    def test_from_the_folder(self):
        folders = [Folder("statements/posb-everyday", "dbs", "1"), Folder("statements/dbs-paylah", "dbs", "2"),
                   Folder("statements/dbs-multiplier", "dbs", "3"), {"path": "statements/wise-usd",
                                                                    "parser": "wise", "account_no": "4",
                                                                    "wallet": "USD"}]
        self.assertEqual([an.bank("", n, None, folders) for n in "1234"], ["posb", "paylah", "dbs", "wise"])

    def test_from_the_words_paylah_and_posb_before_dbs(self):
        self.assertEqual(an.bank("DBS PayLah! wallet", "", "PayLah"), "paylah")
        self.assertEqual(an.bank("POSB", "", "DBS"), "posb")
        self.assertEqual(an.bank("Multiplier", "", "DBS"), "dbs")
        self.assertEqual(an.bank("Tiger Brokers Wallet"), "tiger")
        self.assertEqual(an.bank("Trust Bank"), "trust")
        self.assertEqual(an.bank("Trustee account"), "other")
        self.assertEqual(an.bank("Main Bank"), "other")
        for key in ("youtrip", "revolut", "wise", "cpf", "ocbc", "uob"):
            self.assertEqual(an.bank(f"My {key} account"), key)

    def test_uob_is_marked_by_its_folder_and_its_words(self):
        folders = [Folder("statements/uob-one", "uob", "1"), Folder("statements/uob-savings", "uob", "2")]
        self.assertEqual([an.bank("", n, None, folders) for n in "12"], ["uob", "uob"])
        self.assertEqual(an.bank("UOB Stash Account"), "uob")
        self.assertEqual(an.bank("Saver", "", "United Overseas Bank"), "uob")
        self.assertEqual([an.folder_name(f) for f in folders], ["UOB One", "UOB Savings"])

    def test_the_names_and_marks_are_the_layout_registry(self):
        # G21: each layout's folder and bank, with no list of its own here
        from finance.parsers import layouts
        for lay in layouts.every():
            with self.subTest(lay.name):
                f = {"path": "statements/" + lay.folder.format(ccy="sgd"), "parser": lay.parser,
                     "wallet": "SGD" if lay.per_currency else ""}
                self.assertEqual((an.folder_name(f), an.folder_bank(f)), (lay.folder_title, lay.bank))
                self.assertIn(lay.bank, an.BANKS)

    def test_a_new_bank_is_named_and_marked_with_no_edit_here(self):
        import dataclasses
        import fake_layout
        from finance.parsers import layouts
        layouts.register_bank(layouts.Bank("fakebank", r"\bfakebank\b"))
        self.addCleanup(fake_layout.unregister_bank, "fakebank")
        layouts.register(dataclasses.replace(fake_layout.LAYOUT, bank="fakebank", folder_title="Fakebank Saver"))
        self.addCleanup(fake_layout.unregister, "fakebank")
        f = Folder("statements/fakebank-savings", "fakebank", "FB-0042")
        self.assertEqual((an.folder_name(f), an.folder_bank(f)), ("Fakebank Saver", "fakebank"))
        self.assertEqual(an.bank("My Fakebank pot"), "fakebank")

    def test_the_web_app_keeps_no_list_of_banks(self):
        # the upload picker and Setup take each folder's title and bank from the API
        src = Path(__file__).resolve().parents[2] / "apps" / "web" / "src"
        for rel in ("accounts.ts", "pages/Setup.tsx"):
            with self.subTest(rel):
                body = (src / rel).read_text()
                self.assertNotIn("function bankOf", body)
                self.assertNotIn('"dbs-multiplier"', body)

    def test_every_key_is_one_the_web_app_draws(self):
        # G17: accounts.ts takes Bank from the generated types (server/finance/types.py, make types)
        web = (Path(__file__).resolve().parents[2] / "apps" / "web" / "src" / "types.gen.ts").read_text()
        drawn = set(re.findall(r'"(\w+)"', re.search(r"export type Bank = ([^;]+);", web).group(1)))
        self.assertEqual(set(an.BANKS), drawn)


class Unique(unittest.TestCase):
    def test_numbers_that_end_alike_are_counted_apart(self):
        got = an.describe([{"name": "Saver · 1111111234", "account_no": "1111111234"},
                           {"name": "Saver · 2222221234", "account_no": "2222221234"}])
        self.assertEqual([d["label"] for d in got], ["Saver •• 1234", "Saver •• 1234 (2)"])

    def test_a_reserved_name_is_never_taken(self):
        self.assertEqual(an.unique({"a": "cash", "b": "cash", "c": "Saver"}, reserved=("cash",)),
                         {"a": "cash (2)", "b": "cash (3)", "c": "Saver"})


class Describe(unittest.TestCase):
    def test_two_accounts_with_one_name_get_their_masks(self):
        got = an.describe([{"name": "Saver", "account_no": "111-111111-1"},
                           {"name": f"Saver · {BARE_NO}", "account_no": BARE_NO},
                           {"name": "Spender", "account_no": MAIN_NO}])
        self.assertEqual([d["label"] for d in got], ["Saver •• 1111", "Saver •• 7888", "Spender"])
        self.assertEqual(got[2], {"name": "Spender", "label": "Spender", "bank": "other", "number": "•• 7890"})
        # a page that shows the number beside the name keeps the name alone
        self.assertEqual([d["name"] for d in got], ["Saver", "Saver", "Spender"])

    def test_an_account_with_no_name_of_its_own_carries_its_mask(self):
        got = an.describe([{"name": BARE_NO, "account_no": BARE_NO}])
        self.assertEqual((got[0]["name"], got[0]["label"]), ("Account", "Account •• 7888"))


class Scrub(unittest.TestCase):
    def test_masks_the_known_numbers_and_leaves_dates_and_figures(self):
        text = f"SGD 1,234,567.00 in {MAIN_NO} on 2025-06-28, {WALLET_NO} and {BARE_NO}"
        self.assertEqual(an.scrub(text, NUMBERS),
                         "SGD 1,234,567.00 in •• 7890 on 2025-06-28, •• 3210 and •• 7888")

    def test_a_number_spelled_without_its_dashes(self):
        self.assertEqual(an.scrub("to 1234567890", [MAIN_NO]), "to •• 7890")

    def test_two_keys_that_mask_alike_keep_both_values(self):
        row = {"month": "2025-06", "Saver · 1111111234": 10.0, "Saver · 2222221234": 20.0}
        self.assertEqual(an.scrub_tree(row, ["1111111234", "2222221234"]),
                         {"month": "2025-06", "Saver · •• 1234": 10.0, "Saver · •• 1234 (2)": 20.0})

    def test_keys_are_masked_too(self):
        tree = {"months": [{f"Wallet · {WALLET_NO}": 1.5, "cash": 2}], "n": 3}
        self.assertEqual(an.scrub_tree(tree, NUMBERS), {"months": [{"Wallet · •• 3210": 1.5, "cash": 2}], "n": 3})

    def test_a_number_kept_bare_is_found_however_a_line_splits_it(self):
        self.assertEqual(an.scrub(f"to {MAIN_NO} and 123 456789 0 on 2025-06-28", ["1234567890"]),
                         "to •• 7890 and •• 7890 on 2025-06-28")
        # a longer run that only holds the number is someone else's, or a figure
        self.assertEqual(an.scrub("ref 12345678901 and 1,234,567,890.00", ["1234567890"]),
                         "ref 12345678901 and 1,234,567,890.00")
        # and so is a split spelling that runs on into more digits, either side
        for text in ("card 1234 5678 9012", "card 9012 1234 5678", "ref 1234-5678-9"):
            self.assertEqual(an.scrub(text, ["12345678"]), text)
        self.assertEqual(an.scrub("to 1234 5678, then 1234-5678.", ["12345678"]), "to •• 5678, then •• 5678.")

    def test_a_kept_field_passes_as_it_is(self):
        tree = {"needs_rule": [{"text": f"FAST {MAIN_NO}"}], "report": {"needs_rule": [MAIN_NO], "x": MAIN_NO}}
        self.assertEqual(an.scrub_tree(tree, NUMBERS, keep=("needs_rule",)),
                         {"needs_rule": [{"text": f"FAST {MAIN_NO}"}],
                          "report": {"needs_rule": [MAIN_NO], "x": "•• 7890"}})

    def test_the_owners_numbers_come_from_the_record_and_the_config(self):
        cfg = mock.Mock(accounts={BARE_NO: "page"}, folders=[Folder("statements/a", "dbs", WALLET_NO),
                                                             Folder("statements/b", "dbs")])
        self.assertEqual(an.own_numbers(record(), cfg), {"111-11111-1", WALLET_NO, BARE_NO})
        self.assertEqual(an.own_numbers(), set())


# ---------------------------------------------------------------- the stats

def _renumber(value):
    """The fixture's short account numbers (A1, W1) as full-length invented
    ones, wherever they sit: a field, the end of a name, the head of a period."""
    swap = {MAIN: MAIN_NO, WALLET: WALLET_NO}
    if isinstance(value, dict):
        return {k: _renumber(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_renumber(v) for v in value]
    if isinstance(value, str):
        if value in swap:
            return swap[value]
        for short, full in swap.items():
            if value.startswith(f"{short} · "):
                value = full + value[len(short):]
            if value.endswith(f" · {short}"):
                value = value[: -len(short)] + full
    return value


def numbered():
    """The analytics fixture with account numbers the length a bank prints,
    plus an account the loader created, named by its bare number."""
    s = _renumber(snapshot())
    s["periods"] = s["periods"] + [
        {"name": f"{BARE_NO} · SGD · 2025-12", "account": BARE_NO, "account_no": BARE_NO,
         "period_end": "2025-12-28", "currency": "SGD", "opening_minor": 5_000, "closing_minor": 5_000,
         "total_in_minor": 0, "total_out_minor": 0, "fx_to_sgd": None, "closing_sgd_minor": 5_000,
         "reconciled": True, "has_balances": True}]
    s["accounts"] = s["accounts"] + [
        {"name": BARE_NO, "account_no": BARE_NO, "institution": None, "class": None,
         "subclass": "Wallet", "base_currency": "SGD", "statements_from": "2025-12-28",
         "statements_to": "2025-12-28", "data_coverage": None, "txn_count": 0}]
    return s


def _strings(value):
    """Every string in a result, keys included."""
    if isinstance(value, dict):
        for k, v in value.items():
            yield str(k)
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)
    elif isinstance(value, str):
        yield value


def _leaks(texts):
    """Each string holding six digits in a row of one of the numbers, read
    through any dash or space the run is split by."""
    windows = {d[i:i + 6] for d in map(an.digits, NUMBERS) for i in range(len(d) - 5)}
    return [t for t in texts for run in re.findall(r"\d(?:[ -]?\d)*", t)
            if any(w in re.sub(r"\D", "", run) for w in windows)]


class TheStats(unittest.TestCase):
    """No finding, caveat, table cell or column of any stat carries six digits
    of an account number, and naming changes no figure."""

    @classmethod
    def setUpClass(cls):
        cls._stack = contextlib.ExitStack()
        cls._stack.enter_context(settings())
        cls._stack.enter_context(mock.patch.object(forward_view, "PLAN_CONFIG", FIXTURE_CONFIG))
        cls.snapshot = numbered()
        cls.results = {m.ID: m.run(copy.deepcopy(cls.snapshot), None) for m in stats.modules()}

    @classmethod
    def tearDownClass(cls):
        cls._stack.close()

    def test_the_invented_numbers_are_in_the_input(self):
        # the check below would pass on a fixture that never carried a number
        self.assertTrue(_leaks(_strings(self.snapshot)))

    def test_no_finding_or_caveat_carries_a_number(self):
        for stat_id, res in self.results.items():
            with self.subTest(stat_id):
                self.assertEqual(_leaks(res["findings"] + res["caveats"]), [])

    def test_no_table_or_column_carries_a_number(self):
        for stat_id, res in self.results.items():
            with self.subTest(stat_id):
                self.assertEqual(_leaks(_strings(res["data"])), [])
                self.assertEqual(_leaks(_strings(res["params"])), [])

    def test_accounts_are_named_and_masked(self):
        runway = self.results["savings-runway"]["data"]
        self.assertEqual({a["account"]: a["account_no"] for a in runway["accounts"]},
                         {"Main Bank": "•• 7890", "Travel wallet": "•• 3210", "Account •• 7888": "•• 7888"})
        self.assertIn("Travel wallet_minor", runway["months"][-1])
        quality = self.results["data-quality"]["data"]
        self.assertEqual(quality["unconverted"][0]["period"], "Travel wallet · NOK · 2025-06")
        self.assertIn("Main Bank", quality["coverage"][0]["accounts"])
        self.assertEqual({x["account"] for x in quality["synthetic"]}, {"Travel wallet"})

    def test_naming_changes_no_figure(self):
        with settings():
            before = savings_runway.run(snapshot())["data"]["summary"]
            short = numbered()
            short["periods"] = [p for p in short["periods"] if p["account_no"] != BARE_NO]
            after = savings_runway.run(short)["data"]["summary"]
        self.assertEqual(before, after)

    def test_two_accounts_never_share_a_column_and_none_takes_a_row_field(self):
        """Two accounts that would show one label, and an account named like a
        month row's own field, each keep a column of their own, and the cash
        total does not move."""
        with settings():
            before = savings_runway.run(numbered())["data"]
            s = numbered()
            for a in s["accounts"]:
                a["name"] = "cash" if a["account_no"] == MAIN_NO else "Saver"
            after = savings_runway.run(s)["data"]
        june_before = next(m for m in before["months"] if m["month"] == "2025-06")
        june = next(m for m in after["months"] if m["month"] == "2025-06")
        self.assertEqual(june["cash_minor"], june_before["cash_minor"])
        self.assertEqual(june["cash (2)_minor"], june_before["Main Bank_minor"])
        self.assertEqual(june["Saver •• 3210_minor"], june_before["Travel wallet_minor"])
        self.assertEqual(after["summary"], before["summary"])
        names = [a["account"] for a in after["accounts"]]
        self.assertEqual(len(names), len(set(names)))

    def test_a_reused_snapshot_gets_its_labels(self):
        from analytics import run
        cfg = mock.Mock(folders=[Folder("statements/joint-saver", "ocbc", BARE_NO)])
        old = numbered()
        old["meta"]["source"] = "notion"
        with tempfile.TemporaryDirectory() as cache:
            snap.save(old, cache, "2026-01-02")
            s, _ = run.build_or_reuse(cfg, source="notion", cache=cache, day="2026-01-02", echo=lambda *_: None)
        bare = next(a for a in s["accounts"] if a["account_no"] == BARE_NO)
        self.assertEqual(bare["label"], "Joint Saver")

    def test_a_built_snapshot_names_a_bare_number_by_its_folder(self):
        cfg = mock.Mock(folders=[Folder("statements/joint-saver", "ocbc", BARE_NO)])
        s = snap.labelled(numbered(), cfg)
        bare = next(a for a in s["accounts"] if a["account_no"] == BARE_NO)
        self.assertEqual((bare["label"], bare["bank"]), ("Joint Saver", "ocbc"))
        with settings():
            runway = savings_runway.run(s)["data"]
        self.assertIn("Joint Saver", {a["account"] for a in runway["accounts"]})


# ---------------------------------------------------------------- the record

def record():
    conn = db.connect(":memory:")
    user = {k: [dict(x) for x in v] for k, v in SNAP.items()}
    user["accounts"] += [
        {"name": f"Travel wallet · {WALLET_NO}", "account_no": WALLET_NO, "institution": "YouTrip",
         "class": "Asset", "subclass": "Wallet", "base_currency": "SGD"},
        {"name": "Tiger Brokers Wallet", "account_no": None, "institution": None,
         "class": None, "subclass": None, "base_currency": None},
        {"name": "Trust Bank", "account_no": None, "institution": None,
         "class": None, "subclass": None, "base_currency": None}]
    db.import_user_tables(conn, user)
    db.load_parsed(conn, parsed([block(), block(period=f"{WALLET_NO} · SGD · 2026-07", account_no=WALLET_NO,
                                             period_end="2026-07-31",
                                             source="statements/youtrip-sgd/2026/2026-07.pdf")],
                                [row("k1", "-12.50"),
                                 row("k2", "-3.00", date="2026-07-02", account_no=WALLET_NO,
                                     period=f"{WALLET_NO} · SGD · 2026-07",
                                     source="statements/youtrip-sgd/2026/2026-07.pdf")]))
    conn.execute("INSERT INTO broker_statement(file, period_from, period_to, base_currency, data) "
                 "VALUES ('2025/1-12.pdf', '2025-01-01', '2025-12-31', 'USD', '{}')")
    return conn


class YourProfile(unittest.TestCase):
    def setUp(self):
        self.accounts = profile.accounts(record(), [Folder("statements/youtrip-sgd", "youtrip", WALLET_NO,
                                                           wallet="SGD")])
        self.by_name = {a["name"]: a for a in self.accounts}

    def test_no_full_number_and_the_bank_for_each(self):
        self.assertEqual(_leaks(_strings(self.accounts)), [])
        self.assertEqual({a["name"]: (a["bank"], a["number"]) for a in self.accounts},
                         {"POSB": ("posb", "•• 1111"), "Travel wallet": ("youtrip", "•• 3210"),
                          "Tiger Brokers Wallet": ("tiger", ""), "Trust Bank": ("trust", "")})

    def test_statements_dates_and_rows(self):
        self.assertEqual({k: self.by_name["Travel wallet"][k] for k in ("statements", "transactions", "first", "last")},
                         {"statements": 1, "transactions": 1, "first": "2026-07-02", "last": "2026-07-31"})

    def test_an_account_carries_its_upload_key_and_pdf_count_from_the_folders_it_belongs_to(self):
        folders = [Folder("statements/youtrip-sgd", "youtrip", WALLET_NO, wallet="SGD"),
                   Folder("statements/youtrip-sek", "youtrip", WALLET_NO, wallet="SEK")]
        listing = [{"path": "statements/youtrip-sgd", "name": "youtrip-sgd", "parser": "youtrip", "wallet": "SGD", "statements": 3},
                   {"path": "statements/youtrip-sek", "name": "youtrip-sek", "parser": "youtrip", "wallet": "SEK", "statements": 2},
                   {"path": "statements/tiger", "name": "tiger", "parser": "tiger", "wallet": "", "statements": 4},
                   {"path": "statements/dbs-debit-cards", "name": "dbs-debit-cards", "parser": "dbs", "wallet": "", "statements": 9}]
        got = {a["name"]: (a["folder"], a["pdfs"]) for a in profile.accounts(record(), folders, listing)}
        # a wallet's currencies fold into one key; the number ties the folder, the bank ties the brokerage
        self.assertEqual(got["Travel wallet"], ("statements/youtrip", 5))
        self.assertEqual(got["Tiger Brokers Wallet"], ("statements/tiger", 4))
        # an account with no folder stays apart, and so does a folder with no account
        self.assertEqual(got["Trust Bank"], (None, None))
        self.assertEqual(got["POSB"], (None, None))

    def test_loaded_cpf_statements_get_a_line_even_with_no_account_row(self):
        conn = record()
        conn.execute("INSERT INTO cpf_statement(year, file, as_of, data) VALUES (2024, 'a.pdf', '2024-12-31', '{}')")
        conn.execute("INSERT INTO cpf_statement(year, file, as_of, data) VALUES (2025, 'b.pdf', '2025-12-31', '{}')")
        listing = [{"path": "statements/cpf", "name": "cpf", "parser": "cpf", "wallet": "", "statements": 2},
                   {"path": "statements/dbs-debit-cards", "name": "dbs-debit-cards", "parser": "dbs", "wallet": "", "statements": 9}]
        by_name = {a["name"]: a for a in profile.accounts(conn, [], listing)}
        cpf = by_name["CPF"]
        self.assertEqual((cpf["bank"], cpf["statements"], cpf["transactions"], cpf["first"], cpf["last"], cpf["folder"], cpf["pdfs"]),
                         ("cpf", 2, 0, "2024-01-01", "2025-12-31", "statements/cpf", 2))
        # a folder with nothing loaded from it gets no line of its own: the page lists it as not loaded
        self.assertNotIn("DBS Debit Cards", by_name)
        # no CPF statement loaded, no line
        self.assertNotIn("CPF", {a["name"] for a in profile.accounts(record(), [], listing)})

    def test_a_brokerage_counts_its_own_statements(self):
        tiger = self.by_name["Tiger Brokers Wallet"]
        self.assertEqual((tiger["statements"], tiger["first"], tiger["last"]), (1, "2025-01-01", "2025-12-31"))

    def test_an_account_with_nothing_yet_is_waiting_and_last(self):
        self.assertEqual({k: self.by_name["Trust Bank"][k] for k in ("statements", "transactions", "first")},
                         {"statements": 0, "transactions": 0, "first": None})
        self.assertEqual(self.accounts[-1]["name"], "Trust Bank")

    def test_rules_rows_name_the_account_without_its_number(self):
        from finance import rules_app
        with tempfile.TemporaryDirectory() as data, mock.patch.dict(os.environ, {"KOPIFUND_DATA": data}):
            got = rules_app.group_rows(record(), key="k2")
        self.assertEqual(got["rows"][0]["account"], "Travel wallet")
        self.assertNotIn(WALLET_NO, json.dumps(got))

    def test_rules_rows_name_a_bare_number_by_its_folder(self):
        from finance import rules_app
        conn = record()
        conn.execute("UPDATE account SET name = ?, institution = NULL WHERE account_no = ?", (WALLET_NO, WALLET_NO))
        with tempfile.TemporaryDirectory() as data, mock.patch.dict(os.environ, {"KOPIFUND_DATA": data}):
            got = rules_app.group_rows(conn, key="k2", folders=[Folder("statements/youtrip-sgd", "youtrip",
                                                                         WALLET_NO, wallet="SGD")])
            bare = rules_app.group_rows(conn, key="k2")
        self.assertEqual(got["rows"][0]["account"], "YouTrip")
        self.assertEqual(bare["rows"][0]["account"], "Account •• 3210")
        # G18: the row's bank, for the mark beside the account; a bare number with no folder has none
        self.assertEqual(got["rows"][0]["bank"], "youtrip")
        self.assertEqual(bare["rows"][0]["bank"], "other")


class TheServedResults(unittest.TestCase):
    """A result cached before the stats masked numbers is masked on the way out."""

    CACHED = {"savings-runway": {"findings": [f"Travel wallet · {WALLET_NO} holds the most"],
                                 "data": {"accounts": [{"account_no": MAIN_NO, "last_balance": 1234567.0}],
                                          "months": [{f"Travel wallet · {WALLET_NO}": 12.5, "month": "2025-06"}]}}}

    def test_from_the_config_when_there_is_no_record(self):
        import serve
        cfg = mock.Mock(accounts={WALLET_NO: "page"}, folders=[Folder("statements/a", "dbs", MAIN_NO)])
        with mock.patch("finance.api.open_record", side_effect=serve.NotFound("no record")), \
                mock.patch("finance.api.config", return_value=cfg):
            got = finance_api.masked(copy.deepcopy(self.CACHED))
        self.assertEqual(_leaks(_strings(got)), [])
        runway = got["savings-runway"]
        self.assertEqual(runway["findings"], ["Travel wallet · •• 3210 holds the most"])
        self.assertEqual(runway["data"]["accounts"][0], {"account_no": "•• 7890", "last_balance": 1234567.0})
        self.assertEqual(runway["data"]["months"][0], {"Travel wallet · •• 3210": 12.5, "month": "2025-06"})

    def test_nothing_to_go_on_leaves_the_results_as_they_are(self):
        import serve
        with mock.patch("finance.api.open_record", side_effect=serve.NotFound("no record")), \
                mock.patch("finance.api.config", side_effect=serve.NotFound("no config")):
            self.assertEqual(finance_api.masked(self.CACHED), self.CACHED)


class TheUploadCheck(unittest.TestCase):
    def test_a_row_to_add_names_its_account_without_the_number(self):
        from finance import monthly
        r = {"txn_key": "k", "date": "2026-08-01", "amount": "-1.00", "currency": "SGD",
             "account": None, "account_no": MAIN_NO, "merchant": "SHOP"}
        folders = [Folder("statements/dbs-multiplier", "dbs", MAIN_NO)]
        self.assertEqual(monthly._row(r, {}, folders)["account"], "DBS Multiplier")
        self.assertEqual(monthly._row(r)["account"], "Account •• 7890")
        self.assertEqual(monthly._row(r, {MAIN_NO: "Joint"})["account"], "Joint")

    def test_a_problem_names_a_block_without_its_number(self):
        """A first check, before the record or the config knows the numbers:
        the statements it parsed are the owner's own."""
        from finance import monthly, sync
        new = "Q-246813579024"
        p = parsed([block(period=f"{new} · SGD · 2026-09", account_no=new, source=f"statements/q-{new}/a.pdf")],
                   [row("k1", "-1234567.89", account_no=new, period=f"{new} · SGD · 2026-09",
                        source=f"statements/q-{new}/a.pdf", merchant="", memo="FROM 246813579024:IB")])
        p.problems = [sync.Problem("block-not-reconciled", f"{new} · SGD · 2026-09",
                                   f"the balance chain in statements/q-{new}/a.pdf does not close on 2026-09-30")]
        cfg = mock.Mock(accounts={}, folders=[])
        with mock.patch.object(sync, "parse", return_value=p), mock.patch.object(sync, "categorise"), \
                mock.patch.object(sync, "default_rules"), \
                mock.patch.object(sync, "check_environment", return_value=[]), \
                mock.patch.object(sync, "check_statements", return_value=[]), \
                mock.patch.object(sync, "check_blocks", return_value=[]):
            _, report = monthly.check(cfg, db.connect(":memory:"))
        self.assertEqual(report["problems"], [{
            "code": "block-not-reconciled", "subject": "•• 9024 · SGD · 2026-09",
            "fix": "the balance chain in statements/q-•• 9024/a.pdf does not close on 2026-09-30"}])
        self.assertEqual(report["rows_by_statement"], {"statements/q-•• 9024/a.pdf": 1})
        self.assertEqual((report["to_add"][0]["amount"], report["to_add"][0]["text"]), (-123456789, "FROM •• 9024:IB"))
        self.assertNotIn("246813", json.dumps(report))


# ---------------------------------------------------------------- the upload history

SHA = "ab" * 32
MIRROR = (f"period {MAIN_NO} · SGD · 2026-09: closing SGD 1,234,567.00 on 2026-09-30\n"
          f"period {WALLET_NO} · SGD · 2026-09: 3 rows\nunknown-account {BARE_NO}\n")


def history_report():
    """A check's report as one kept before the history masked anything."""
    return {"problems": [{"code": "block-not-reconciled", "subject": f"{MAIN_NO} · SGD · 2026-09",
                          "fix": f"the balance chain in statements/saver-{BARE_NO}/2026/2026-09.pdf does not close"},
                         {"code": "duplicate-period", "subject": f"{WALLET_NO} · SGD · 2026-08", "fix": "move one"}],
            "statements": 2, "rows": 1200, "to_add_count": 1, "recategorised_count": 0, "to_archive_count": 0,
            "to_add": [{"key": "k1", "date": "2026-09-01", "amount": -123456, "currency": "SGD",
                        "account": f"Main · {MAIN_NO}", "text": "TRANSFER TO 1234567890", "payee": "",
                        "category": ""}],
            "needs_rule": [{"text": f"FAST {MAIN_NO}", "field": "memo", "rows": 2, "amount": 4200,
                            "first": "2026-09-01", "last": "2026-09-02", "keys": ["k1"]}],
            "rows_by_statement": {f"statements/saver-{BARE_NO}/2026/2026-09.pdf": 40}}


def _figures(value):
    """Every number and every date in a JSON value, in order: what masking must leave alone."""
    if isinstance(value, dict):
        return [x for k, v in value.items() for x in _figures(v)]
    if isinstance(value, list):
        return [x for v in value for x in _figures(v)]
    if isinstance(value, str):
        return re.findall(r"\d{4}-\d{2}(?:-\d{2})?|\d{1,3}(?:,\d{3})+\.\d\d", value)
    return [value]


class TheUploadHistory(unittest.TestCase):
    """Past uploads and the Upload page's answers never carry a full account
    number the record or the config knows, and no figure moves."""

    def setUp(self):
        from finance import upload_log
        import serve
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        env = mock.patch.dict(os.environ, {"KOPIFUND_DATA": str(root), "KOPIFUND_DB": str(root / "kopifund.db")})
        env.start()
        self.addCleanup(env.stop)
        # the record knows two numbers, the config the third (a folder named by its number)
        cfg = mock.Mock(accounts={}, folders=[Folder(f"statements/saver-{BARE_NO}", "ocbc", BARE_NO)])
        p = mock.patch("finance.api.config", return_value=cfg)
        p.start()
        self.addCleanup(p.stop)
        user = {k: [dict(x) for x in v] for k, v in SNAP.items()}
        user["accounts"] = [{"name": f"Main · {MAIN_NO}", "account_no": MAIN_NO, "institution": "DBS",
                             "class": "Asset", "subclass": "Cash", "base_currency": "SGD"},
                            {"name": "Wallet", "account_no": WALLET_NO, "institution": "YouTrip",
                             "class": "Asset", "subclass": "Wallet", "base_currency": "SGD"}]
        with contextlib.closing(db.connect()) as conn:
            db.import_user_tables(conn, user)
            # kept before the history masked: the numbers as the steps printed them
            self.old = upload_log.start(conn)
            upload_log.record(conn, self.old, "upload", "2026-09-30T01:00:00+00:00", "ok",
                              f"Saved as statements/saver-{BARE_NO}/2026/2026-09.pdf",
                              {"folder": f"statements/saver-{BARE_NO}", "file": "x.pdf", "bytes": 1234567,
                               "sha256": SHA},
                              {"status": "added", "path": f"statements/saver-{BARE_NO}/2026/2026-09.pdf"})
            upload_log.record(conn, self.old, "check", "2026-09-30T01:01:00+00:00", "problems",
                              f"2 problems, the first in {MAIN_NO}", {}, history_report())
            upload_log.record(conn, self.old, "mirror", "2026-09-30T01:02:00+00:00", "failed",
                              "exit code 1", {}, {"code": 1, "output": MIRROR})
            self.attempt = upload_log.start(conn)
        self.serve = serve

    def kept(self, attempt):
        from finance import upload_log
        with contextlib.closing(db.connect()) as conn:
            return upload_log.attempt(conn, attempt)

    def go(self, path, query=None, body=b""):
        return self.serve.write(path, {"attempt": str(self.attempt), **(query or {})}, body)

    def test_the_old_entries_carry_the_numbers(self):
        # the checks below would pass on a history that never held one
        self.assertTrue(_leaks(_strings(self.kept(self.old))))

    def test_past_uploads_mask_an_entry_kept_before_without_rewriting_it(self):
        listed = self.serve.answer("/api/uploads", {})
        detail = self.serve.answer(f"/api/uploads/{self.old}", {})
        self.assertEqual(_leaks(_strings(listed)), [])
        self.assertEqual(_leaks(_strings(detail)), [])
        raw = self.kept(self.old)
        self.assertEqual(_figures(detail), _figures(raw))
        check, mirror = detail["steps"][1]["output"], detail["steps"][2]["output"]
        self.assertEqual(check["problems"][0]["subject"], "•• 7890 · SGD · 2026-09")
        self.assertEqual(check["to_add"][0]["text"], "TRANSFER TO •• 7890")
        self.assertEqual(check["rows_by_statement"], {"statements/saver-•• 7888/2026/2026-09.pdf": 40})
        self.assertEqual(mirror["output"].splitlines()[0], "period •• 7890 · SGD · 2026-09: closing SGD 1,234,567.00 on 2026-09-30")
        self.assertEqual(detail["steps"][0]["input"]["sha256"], SHA)
        self.assertEqual(listed["attempts"][0]["steps"][1]["summary"], "2 problems, the first in •• 7890")
        # the record keeps what it kept
        self.assertTrue(_leaks(_strings(raw)))

    def test_a_check_is_answered_and_kept_masked(self):
        from finance import monthly
        with mock.patch.object(monthly, "folders", return_value=[{"path": f"statements/saver-{BARE_NO}"}]), \
                mock.patch.object(monthly, "check", return_value=(parsed([], []), history_report())):
            out = self.go("/api/run/check")
        # the needs-rule text goes back to the Upload page as printed: it becomes a rule's pattern
        self.assertEqual(out["needs_rule"], history_report()["needs_rule"])
        self.assertEqual(_leaks(_strings({k: v for k, v in out.items() if k != "needs_rule"})), [])
        self.assertEqual(_figures({k: v for k, v in out.items() if k != "attempt"}), _figures(history_report()))
        [step] = self.kept(self.attempt)["steps"]
        self.assertEqual(_leaks(_strings(step)), [])
        self.assertEqual(step["input"], {"folders": ["statements/saver-•• 7888"]})
        self.assertEqual(_figures(step["output"]), _figures(history_report()))

    def test_a_first_check_masks_the_numbers_it_parsed(self):
        """The record and the config do not know this number yet: the
        statement the check parsed says it is the owner's."""
        from finance import monthly
        new = "Q-246813579024"
        report = {**history_report(), "needs_rule": [{"text": f"TOP-UP {new}", "field": "memo", "rows": 1,
                                                      "amount": 100, "keys": ["k1"]}],
                  "problems": [{"code": "x", "subject": f"{new} · SGD · 2026-09", "fix": "y"}]}
        statements = parsed([block(account_no=new)], [])
        with mock.patch.object(monthly, "folders", return_value=[]), \
                mock.patch.object(monthly, "check", return_value=(statements, report)):
            out = self.go("/api/run/check")
        self.assertEqual(out["problems"][0]["subject"], "•• 9024 · SGD · 2026-09")
        self.assertEqual(out["needs_rule"], report["needs_rule"])
        [step] = self.kept(self.attempt)["steps"]
        self.assertNotIn("246813", json.dumps(step))
        self.assertEqual(step["output"]["needs_rule"][0]["text"], "TOP-UP •• 9024")

    def test_a_blocked_load_is_answered_and_kept_masked(self):
        from finance import monthly
        with mock.patch.object(monthly, "load", return_value={"loaded": False, "report": history_report()}):
            out = self.go("/api/run/load")
        self.assertEqual(out["report"]["needs_rule"], history_report()["needs_rule"])
        out["report"].pop("needs_rule")
        self.assertEqual(_leaks(_strings(out)), [])
        [step] = self.kept(self.attempt)["steps"]
        self.assertEqual(_leaks(_strings(step)), [])
        self.assertEqual(step["output"]["problems"][0]["subject"], "•• 7890 · SGD · 2026-09")

    def test_a_mirror_report_is_answered_and_kept_masked(self):
        from finance import monthly
        with mock.patch.object(monthly, "mirror", return_value=(1, MIRROR)):
            out = self.go("/api/run/mirror")
        self.assertEqual(_leaks(_strings(out)), [])
        self.assertEqual(_figures(out), [1, *_figures(MIRROR), self.attempt])
        [step] = self.kept(self.attempt)["steps"]
        self.assertEqual(_leaks(_strings(step)), [])
        self.assertEqual(step["output"]["output"], an.scrub(MIRROR, NUMBERS))

    def test_an_upload_is_answered_and_kept_masked_refused_or_not(self):
        from finance import monthly
        target = f"statements/saver-{BARE_NO}/2026/2026-09.pdf"
        with mock.patch.object(monthly, "upload", return_value={"status": "added", "path": target,
                                                                "message": f"Saved as {target}"}):
            out = self.go("/api/statements", {"folder": f"statements/saver-{BARE_NO}", "name": "x.pdf"}, b"%PDF")
        self.assertEqual(out["path"], "statements/saver-•• 7888/2026/2026-09.pdf")
        with mock.patch.object(monthly, "upload",
                               side_effect=monthly.UploadRefused(f"{target} already holds a different file")):
            with self.assertRaises(self.serve.Refused) as refused:
                self.go("/api/statements", {"folder": f"statements/saver-{BARE_NO}", "name": "y.pdf"}, b"%PDF")
        self.assertEqual(str(refused.exception),
                         "statements/saver-•• 7888/2026/2026-09.pdf already holds a different file")
        steps = self.kept(self.attempt)["steps"]
        self.assertEqual([s["status"] for s in steps], ["ok", "refused"])
        self.assertEqual(_leaks(_strings(steps)), [])


if __name__ == "__main__":
    unittest.main()
