"""Which account an uploaded statement belongs to (server/finance/sniff.py), on every
scrubbed fixture, and the upload endpoints that use it (G13)."""
import isolate  # noqa: F401  first: no test writes the real record or cache
import dataclasses
import json
import os
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

import serve
from finance import db, model, monthly, sniff, upload_log
from support import DataFolder
import support

FIXTURES = Path(__file__).parent / "fixtures"

# every fixture: what its text says it is, and the folder it belongs in below
EXPECTED = {
    "statements/card.txt": ("dbs", "card", "", "statements/dbs-debit-cards"),
    "statements/card-covered.txt": ("dbs", "card", "", "statements/dbs-debit-cards"),
    "statements/card-only-month.txt": ("dbs", "card", "", "statements/dbs-debit-cards"),
    "statements/dbs-consolidated.txt": ("dbs", "consolidated", "", "statements/dbs-multiplier"),
    "statements/posb-deposit.txt": ("dbs", "posb", "", "statements/posb-everyday"),
    "statements/posb-deposit-dec-jan.txt": ("dbs", "posb", "", "statements/posb-everyday"),
    "statements/paylah.txt": ("dbs", "paylah", "", "statements/dbs-paylah"),
    "statements/ocbc-360.txt": ("ocbc", "ocbc", "", "statements/ocbc-360"),
    "statements/uob-one.txt": ("uob", "uob-one", "", "statements/uob-one"),
    "statements/uob-stash-feb.txt": ("uob", "uob-savings", "", "statements/uob-savings"),
    "statements/uob-stash-mar.txt": ("uob", "uob-savings", "", "statements/uob-savings"),
    "statements/uob-stash-unbalanced.txt": ("uob", "uob-savings", "", "statements/uob-savings"),
    "statements/youtrip-sgd.txt": ("youtrip", "youtrip", "SGD", "statements/youtrip-sgd"),
    "statements/youtrip-sgd-gap.txt": ("youtrip", "youtrip", "SGD", "statements/youtrip-sgd"),
    "statements/youtrip-sgd-boundary.txt": ("youtrip", "youtrip", "SGD", "statements/youtrip-sgd"),
    "statements/youtrip-nok.txt": ("youtrip", "youtrip", "NOK", "statements/youtrip-nok"),
    "statements/youtrip-empty.txt": ("youtrip", "youtrip", "EUR", "statements/youtrip-eur"),
    "statements/revolut-all.txt": ("revolut", "revolut", "", "statements/revolut"),
    "statements/wise-sgd.txt": ("wise", "wise", "SGD", "statements/wise-sgd"),
    "statements/wise-sgd-pages.txt": ("wise", "wise", "SGD", "statements/wise-sgd"),
    "statements/wise-usd.txt": ("wise", "wise", "USD", "statements/wise-usd"),
    "statements/wise-nok.txt": ("wise", "wise", "NOK", "statements/wise-nok"),
    "tiger/legacy.txt": ("tiger", "tiger", "", "statements/tiger"),
    "tiger/old-layout.txt": ("tiger", "tiger", "", "statements/tiger"),
    "tiger/sparse.txt": ("tiger", "tiger", "", "statements/tiger"),
    "tiger/wrapped.txt": ("tiger", "tiger", "", "statements/tiger"),
    "tiger/wrapped-2026.txt": ("tiger", "tiger", "", "statements/tiger"),
    "cpf/one-off-2020.txt": ("cpf", "cpf", "", "statements/cpf"),
    "cpf/summary-2016.txt": ("cpf", "cpf", "", "statements/cpf"),
    "cpf/summary-quiet.txt": ("cpf", "cpf", "", "statements/cpf"),
    "cpf/yearly-2024.txt": ("cpf", "cpf", "", "statements/cpf"),
    "cpf/yearly-torn.txt": ("cpf", "cpf", "", "statements/cpf"),
}

# the fixtures' invented numbers, as a config would hold them
NUMBERS = {"statements/dbs-multiplier": "120-345678-9", "statements/posb-everyday": "130-24680-5",
           "statements/dbs-paylah": "8888880001234567", "statements/ocbc-360": "601234567001",
           "statements/uob-one": "123-456-789-0", "statements/uob-savings": "987-654-321-0",
           "statements/youtrip": "Y-1029384756", "statements/revolut": "885100012345678",
           "statements/wise": "150-246-80"}

FOLDERS = [("statements/dbs-multiplier", "dbs", ""), ("statements/posb-everyday", "dbs", ""),
           ("statements/dbs-paylah", "dbs", ""), ("statements/dbs-debit-cards", "dbs", ""),
           ("statements/ocbc-360", "ocbc", ""), ("statements/uob-one", "uob", ""),
           ("statements/uob-savings", "uob", ""), ("statements/youtrip-sgd", "youtrip", "SGD"),
           ("statements/youtrip-eur", "youtrip", "EUR"), ("statements/youtrip-nok", "youtrip", "NOK"),
           ("statements/revolut", "revolut", ""), ("statements/wise-sgd", "wise", "SGD"),
           ("statements/wise-usd", "wise", "USD"), ("statements/wise-nok", "wise", "NOK"),
           ("statements/tiger", "tiger", ""), ("statements/cpf", "cpf", "")]


def text(name):
    return (FIXTURES / name).read_text()


def config_no(path, wallet):
    key = path.removesuffix(f"-{wallet.lower()}") if wallet else path
    return NUMBERS.get(key, "")


def places(numbers=True, learnt=False):
    """The folders above: with the config's numbers or none (a first run),
    and with or without the fixtures already filed in them."""
    out = [sniff.Place(p, parser, wallet, config_no(p, wallet) if numbers else "") for p, parser, wallet in FOLDERS]
    if learnt:
        for name, (*_, folder) in EXPECTED.items():
            next(p for p in out if p.path == folder).learnt.extend(sniff.sniff(text(name)))
    return out


def with_place(folders, path, **changes):
    return [dataclasses.replace(p, **changes) if p.path == path else p for p in folders]


class SniffTest(unittest.TestCase):
    def test_every_fixture_reads_as_one_layout(self):
        for name, (parser, layout, currency, _) in EXPECTED.items():
            with self.subTest(name):
                [found] = sniff.sniff(text(name))
                self.assertEqual((found.parser, found.layout, found.currency), (parser, layout, currency))

    def test_every_fixture_is_listed(self):
        on_disk = {str(p.relative_to(FIXTURES)) for d in ("statements", "tiger", "cpf")
                   for p in (FIXTURES / d).glob("*.txt")}
        self.assertEqual(on_disk, set(EXPECTED))

    def test_the_numbers_are_the_ones_the_statement_prints_as_its_own(self):
        self.assertEqual(sniff.sniff(text("statements/posb-deposit.txt"))[0].numbers, {"130246805"})
        # a card statement names the account its cards draw on
        self.assertEqual(sniff.sniff(text("statements/card.txt"))[0].numbers, {"130246805"})
        self.assertIn("Y1029384756", sniff.sniff(text("statements/youtrip-sgd.txt"))[0].numbers)
        self.assertEqual(sniff.sniff(text("statements/wise-nok.txt"))[0].numbers, {"GB00TRWI00001111222233"})

    def test_text_that_is_no_statement_reads_as_nothing(self):
        for junk in ("", "Invoice 42\nTotal 12.00\n", "Dear customer,\nYour card statement is ready.\n" * 3):
            self.assertEqual(sniff.sniff(junk), [])


class RouteTest(unittest.TestCase):
    def go(self, name, folders):
        return sniff.route(sniff.sniff(text(name)), folders)

    def test_every_fixture_routes_to_its_account(self):
        # with the config's numbers, on a first run with none, and with the
        # statements already filed telling each folder what it holds
        for label, folders in (("config", places()), ("first run", places(numbers=False)),
                               ("filed", places(learnt=True)), ("filed, no config", places(False, True))):
            for name, (*_, folder) in EXPECTED.items():
                with self.subTest(label=label, fixture=name):
                    r = self.go(name, folders)
                    self.assertEqual((r.folder, r.why), (folder, ""))

    def test_a_card_statement_never_goes_to_the_account_its_cards_draw_on(self):
        # POSB knows the number by its config and by its statements; the card folder knows nothing yet
        folders = with_place(places(learnt=True), "statements/dbs-debit-cards", learnt=[])
        self.assertEqual(self.go("statements/card.txt", folders).folder, "statements/dbs-debit-cards")

    def test_two_accounts_at_one_bank_go_by_the_number_printed(self):
        folders = places() + [sniff.Place("statements/joint/posb-everyday", "dbs", "", "130-99999-9")]
        self.assertEqual(self.go("statements/posb-deposit.txt", folders).folder, "statements/posb-everyday")
        other = text("statements/posb-deposit.txt").replace("130-24680-5", "130-99999-9")
        self.assertEqual(sniff.route(sniff.sniff(other), folders).folder, "statements/joint/posb-everyday")

    def test_two_accounts_it_cannot_tell_apart_ask(self):
        # two POSB folders, neither with a number yet: either could be it
        folders = places(numbers=False) + [sniff.Place("statements/joint/posb-everyday", "dbs")]
        r = self.go("statements/posb-deposit.txt", folders)
        self.assertIsNone(r.folder)
        self.assertEqual(sorted(r.candidates), ["statements/joint/posb-everyday", "statements/posb-everyday"])
        self.assertIn("POSB", r.why)
        # two folders that both claim the number
        folders = places() + [sniff.Place("statements/joint/posb-everyday", "dbs", "", "130-24680-5")]
        r = self.go("statements/posb-deposit.txt", folders)
        self.assertIsNone(r.folder)
        self.assertEqual(len(r.candidates), 2)

    def test_a_number_no_account_knows_asks(self):
        other = text("statements/posb-deposit.txt").replace("130-24680-5", "130-11111-1")
        r = sniff.route(sniff.sniff(other), places())
        self.assertIsNone(r.folder)
        self.assertIn("statements/posb-everyday", r.candidates)
        self.assertIn("not one Kopifund knows", r.why)
        # nor is a CPF statement of somebody else filed with the owner's
        folders = places(learnt=True)
        cpf = text("cpf/yearly-2024.txt").replace("S9012345A", "S7654321Z")
        self.assertIsNone(sniff.route(sniff.sniff(cpf), folders).folder)
        # or a Tiger account the folder has not held
        tiger = with_place(folders, "statements/tiger", learnt=sniff.sniff(text("tiger/legacy.txt")))
        self.assertEqual(sniff.route(sniff.sniff(text("tiger/sparse.txt")), tiger).folder, "statements/tiger")
        self.assertIsNone(sniff.route(sniff.sniff(text("tiger/wrapped-2026.txt")), tiger).folder)

    def test_a_folder_whose_layout_is_not_sure_is_never_chosen_alone(self):
        # a DBS folder named by the owner, no statements in it yet: it could
        # hold any DBS layout, so a POSB statement of a new number asks
        folders = places() + [sniff.Place("statements/my-bank", "dbs")]
        other = text("statements/posb-deposit.txt").replace("130-24680-5", "130-11111-1")
        r = sniff.route(sniff.sniff(other), folders)
        self.assertIsNone(r.folder)
        self.assertIn("statements/my-bank", r.candidates)
        # its own statements say what it holds once there are some
        filed = sniff.sniff(other)
        folders = with_place(folders, "statements/my-bank", learnt=filed)
        self.assertEqual(sniff.route(filed, folders).folder, "statements/my-bank")

    def test_a_wallet_goes_by_its_currency(self):
        folders = [p for p in places() if p.path != "statements/youtrip-nok"]
        r = self.go("statements/youtrip-nok.txt", folders)
        self.assertEqual((r.folder, r.candidates), (None, []))
        self.assertIn("YouTrip NOK", r.why)

    def test_a_wallet_that_prints_no_number_goes_to_its_one_folder_only(self):
        bare = "\n".join(ln for ln in text("statements/youtrip-sgd.txt").splitlines() if "Y-" not in ln)
        [found] = sniff.sniff(bare)
        self.assertEqual(found.numbers, frozenset())
        self.assertEqual(sniff.route([found], places()).folder, "statements/youtrip-sgd")
        two = places() + [sniff.Place("statements/youtrip-second-sgd", "youtrip", "SGD", "Y-5555555555")]
        r = sniff.route([found], two)
        self.assertIsNone(r.folder)
        self.assertEqual(len(r.candidates), 2)

    def test_a_wise_balance_other_than_sgd_does_not_go_by_the_account_number(self):
        # the config's number is the SGD balance's; the USD file prints its own local details
        self.assertEqual(self.go("statements/wise-usd.txt", places()).folder, "statements/wise-usd")
        self.assertEqual(self.go("statements/wise-sgd.txt", places()).folder, "statements/wise-sgd")

    def test_no_layout_or_two_layouts_ask_with_no_candidates(self):
        r = sniff.route(sniff.sniff("Invoice 42\n"), places())
        self.assertEqual((r.folder, r.candidates), (None, []))
        self.assertIn("could not tell", r.why)
        both = sniff.sniff(text("statements/paylah.txt") + "\f" + text("statements/revolut-all.txt"))
        self.assertEqual(len(both), 2)
        r = sniff.route(both, places())
        self.assertEqual((r.folder, r.candidates), (None, []))

    def test_two_dbs_layouts_in_one_file_read_as_two(self):
        # the DBS parser would read the whole file as its first layout and drop the other's rows
        for first, second in (("paylah", "card"), ("card", "paylah"), ("posb-deposit", "card"), ("paylah", "dbs-consolidated")):
            with self.subTest(first=first, second=second):
                both = sniff.sniff(text(f"statements/{first}.txt") + "\f" + text(f"statements/{second}.txt"))
                self.assertEqual(len(both), 2)
                r = sniff.route(both, places())
                self.assertEqual((r.folder, r.candidates), (None, []))

    def test_a_statement_no_account_takes_asks(self):
        folders = [p for p in places() if p.parser != "ocbc"]
        r = self.go("statements/ocbc-360.txt", folders)
        self.assertEqual((r.folder, r.candidates), (None, []))
        self.assertIn("OCBC", r.why)

    def test_no_reason_names_an_account_number(self):
        for name in EXPECTED:
            r = sniff.route(sniff.sniff(text(name)), [p for p in places() if p.parser != EXPECTED[name][0]])
            for n in NUMBERS.values():
                self.assertNotIn(n, r.why)


def fixture_pdf(name):
    """The bytes of a pretend PDF whose text is the fixture's."""
    return b"%PDF-1.4 fixture:" + name.encode()


def fake_text(pdf):
    data = Path(pdf).read_bytes()
    if not data.startswith(b"%PDF-1.4 fixture:"):
        import subprocess
        raise subprocess.CalledProcessError(1, ["pdftotext"])
    return text(data.split(b"fixture:", 1)[1].decode())


CONFIG = "\n".join(
    f'[[folder]]\npath = "{p}"\nparser = "{parser}"\naccount_no = "{config_no(p, wallet)}"\n'
    + (f'wallet = "{wallet}"\n' if wallet else "") + ("multi_currency = true\n" if parser == "revolut" else "")
    for p, parser, wallet in FOLDERS if parser not in ("tiger", "cpf"))


class UploadTest(DataFolder):
    """find_folder, the folder check on an upload, and the two endpoints, with
    pdftotext replaced by the fixtures' text."""

    def setUp(self):
        super().setUp()
        (self.root / "config.toml").write_text(CONFIG)
        for p, *_ in FOLDERS:
            (self.root / p).mkdir(parents=True, exist_ok=True)
        for target in ("finance.monthly._text", "finance.sniff.pdf_text"):
            p = mock.patch(target, side_effect=fake_text)
            p.start()
            self.addCleanup(p.stop)
        sniff._SEEN.clear()
        self.cfg = model.load_config()
        self.config = mock.patch("finance.api.config", return_value=self.cfg)
        self.config.start()
        self.addCleanup(self.config.stop)
        self.attempt = support.write("/api/uploads/start", {}, b'{"origin": "upload"}')["attempt"]

    def pdfs(self):
        return sorted(str(p.relative_to(self.root)) for p in (self.root / "statements").rglob("*.pdf"))

    def steps(self):
        with closing(db.connect()) as conn:
            return upload_log.attempt(conn, self.attempt)["steps"]

    def go(self, path, query=None, body=b""):
        return support.write(path, {"attempt": str(self.attempt), "from": "upload", **(query or {})}, body)

    def test_find_folder_names_the_one_folder_or_says_why_not(self):
        self.assertEqual(monthly.find_folder(self.cfg, fixture_pdf("statements/posb-deposit.txt")),
                         {"folder": "statements/posb-everyday", "kind": "a POSB account statement"})
        wallet = monthly.find_folder(self.cfg, fixture_pdf("statements/youtrip-nok.txt"))
        self.assertEqual(wallet["folder"], "statements/youtrip-nok")
        with self.assertRaises(monthly.UploadRefused):
            monthly.find_folder(self.cfg, b"%PDF-1.4 damaged")
        with self.assertRaises(monthly.UploadRefused):
            monthly.find_folder(self.cfg, b"hello")
        self.assertEqual(self.pdfs(), [])

    def test_an_auto_upload_is_filed_where_a_manual_one_would_be(self):
        out = self.go("/api/statements/auto", {"name": "June.pdf"}, fixture_pdf("statements/posb-deposit.txt"))
        self.assertEqual((out["status"], out["account_folder"]), ("added", "statements/posb-everyday"))
        self.assertEqual(self.pdfs(), ["statements/posb-everyday/2026/2026-06.pdf"])
        [step] = self.steps()
        self.assertEqual((step["step"], step["status"]), ("upload", "ok"))
        self.assertEqual(step["input"]["folder_from"], "statement")
        self.assertEqual(step["input"]["file"], "June.pdf")
        # the same file sent by hand to the same account is the same file
        again = self.go("/api/statements", {"folder": "statements/posb-everyday", "name": "x.pdf"},
                        fixture_pdf("statements/posb-deposit.txt"))
        self.assertEqual(again["status"], "already-there")
        self.assertEqual(self.steps()[-1]["input"]["folder_from"], "owner")

    def test_a_file_it_cannot_place_is_kept_nowhere(self):
        # a second POSB folder with no number: two accounts it cannot tell apart
        (self.root / "config.toml").write_text(CONFIG.replace('account_no = "130-24680-5"', 'account_no = ""')
                                               + '[[folder]]\npath = "statements/joint/posb-everyday"\nparser = "dbs"\n')
        self.cfg = model.load_config()
        self.config.stop()
        self.config = mock.patch("finance.api.config", return_value=self.cfg)
        self.config.start()
        out = self.go("/api/statements/auto", {"name": "June.pdf"}, fixture_pdf("statements/posb-deposit.txt"))
        self.assertEqual(out["status"], "needs-account")
        self.assertEqual(sorted(out["candidates"]), ["statements/joint/posb-everyday", "statements/posb-everyday"])
        self.assertEqual(out["kind"], "a POSB account statement")
        self.assertEqual(self.pdfs(), [])
        self.assertEqual(self.steps(), [])
        self.assertEqual(list((Path(os.environ["KOPIFUND_CACHE"]) / "inbox").glob("*")), [])
        # the owner names the account; then it is an upload like any other
        chosen = self.go("/api/statements", {"folder": "statements/joint/posb-everyday", "name": "June.pdf"},
                         fixture_pdf("statements/posb-deposit.txt"))
        self.assertEqual(chosen["status"], "added")
        self.assertEqual(self.pdfs(), ["statements/joint/posb-everyday/2026/2026-06.pdf"])

    def test_a_statement_no_layout_reads_is_not_supported_and_kept_nowhere(self):
        # G21: an invented bank's statement; no account to choose, nothing saved,
        # nothing in the history, nothing left in the inbox
        out = self.go("/api/statements/auto", {"name": "Acme Sep.pdf"}, fixture_pdf("unknown/acme-savings.txt"))
        self.assertEqual(out["status"], "not-supported")
        self.assertIn("does not read this statement", out["message"])
        self.assertNotIn("candidates", out)
        self.assertEqual([b["name"] for b in out["supported"]][:3], ["DBS", "POSB", "DBS PayLah!"])
        self.assertEqual({b["bank"] for b in out["supported"] if b["status"] == "from a public sample"}, {"ocbc", "uob"})
        self.assertEqual(out["coming_soon"], [{"bank": "trust", "name": "Trust Bank"}])
        self.assertEqual(self.pdfs(), [])
        self.assertEqual(self.steps(), [])
        self.assertEqual(list((Path(os.environ["KOPIFUND_CACHE"]) / "inbox").glob("*")), [])
        self.assertEqual(monthly.find_folder(self.cfg, fixture_pdf("unknown/acme-savings.txt"))["supported"], False)

    def test_two_layouts_in_one_file_still_ask_for_an_account(self):
        # the existing refusals stay: more than one layout, and more than one folder, are the owner's to place
        with mock.patch("finance.monthly._text", return_value=text("statements/paylah.txt") + "\f" + text("statements/revolut-all.txt")):
            out = self.go("/api/statements/auto", {"name": "two.pdf"}, b"%PDF-1.4 two in one")
        self.assertEqual((out["status"], out["candidates"]), ("needs-account", []))
        self.assertIn("more than one kind of statement", out["message"])
        self.assertEqual(self.steps(), [])

    def test_a_refused_auto_upload_is_recorded(self):
        with self.assertRaises(serve.Refused):
            self.go("/api/statements/auto", {"name": "notes.txt"}, b"hello")
        [step] = self.steps()
        self.assertEqual((step["step"], step["status"], step["input"]["folder"]), ("upload", "refused", ""))

    def test_an_upload_that_says_it_is_for_another_account_is_refused(self):
        cases = [("statements/youtrip-sgd", "statements/youtrip-nok.txt", "YouTrip NOK"),
                 ("statements/wise-usd", "statements/wise-sgd.txt", "Wise SGD"),
                 ("statements/revolut", "statements/posb-deposit.txt", "POSB"),
                 ("statements/dbs-paylah", "statements/posb-deposit.txt", "POSB"),
                 ("statements/dbs-debit-cards", "statements/dbs-consolidated.txt", "consolidated")]
        for folder, name, said in cases:
            with self.subTest(folder=folder, fixture=name):
                with self.assertRaises(monthly.UploadRefused) as cm:
                    monthly.upload(self.cfg, folder, fixture_pdf(name))
                self.assertIn(said, str(cm.exception))
        # a file that reads as several statements is the owner's to place, but only in a folder that takes one of them
        with mock.patch.object(monthly, "_text", return_value=text("statements/paylah.txt") + "\f" + text("statements/revolut-all.txt")):
            with self.assertRaises(monthly.UploadRefused) as cm:
                monthly.upload(self.cfg, "statements/wise-sgd", b"%PDF-1.4 two in one")
            self.assertIn("PayLah! statement and a Revolut export", str(cm.exception))
            with self.assertRaises(monthly.UploadRefused):
                monthly.upload(self.cfg, "statements/dbs-debit-cards", b"%PDF-1.4 two in one")
            self.assertEqual(monthly.upload(self.cfg, "statements/revolut", b"%PDF-1.4 two in one")["status"], "added")
        [kept] = self.pdfs()
        self.assertTrue(kept.startswith("statements/revolut/"))
        # a Revolut or Wise folder names its file by the day, so what it cannot read it refuses
        for folder in ("statements/revolut", "statements/wise-sgd"):
            with self.assertRaises(monthly.UploadRefused):
                monthly.upload(self.cfg, folder, b"%PDF-1.4 damaged")
        self.assertEqual(self.pdfs(), [kept])

    def test_the_folders_learn_from_the_statements_already_in_them(self):
        # a card folder with a statement in it is known by the account its cards draw on
        self.go("/api/statements/auto", {"name": "c.pdf"}, fixture_pdf("statements/card.txt"))
        [filed] = self.pdfs()
        self.assertTrue(filed.startswith("statements/dbs-debit-cards/"))
        learnt = next(p for p in sniff.places(self.cfg, read="dbs") if p.path == "statements/dbs-debit-cards")
        self.assertEqual(learnt.layouts(), {"card"})
        self.assertEqual(learnt.numbers(sniff.sniff(text("statements/card.txt"))[0]), {"130246805"})

    def test_the_answer_masks_nothing_the_page_sends_back(self):
        out = self.go("/api/statements/auto", {"name": "a.pdf"}, fixture_pdf("statements/dbs-consolidated.txt"))
        self.assertEqual(out["account_folder"], "statements/dbs-multiplier")
        self.assertNotIn("120-345678-9", json.dumps(self.steps()))


if __name__ == "__main__":
    unittest.main()
