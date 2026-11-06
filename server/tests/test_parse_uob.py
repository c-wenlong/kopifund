"""server/finance/parsers/parse_uob.py over invented UOB statements (G21).

The fixtures under server/tests/fixtures/statements/uob-*.txt are written in the
layout of a public sample (synthetic previews of a UOB One, Uniplus and Stash
statement on a converter's page; the parser's docstring names it), as the text
pdftotext -layout would give; every name and number in them is made up. The
owner holds no UOB account, so these tests are all that has read the layout:

  uob-one.txt               March, three pages, the table broken across two
                            pages with a carried and a brought forward balance
  uob-stash-feb.txt         February, a credit and a debit on one day
  uob-stash-mar.txt         March, brought forward from February's closing
  uob-stash-unbalanced.txt  February with a printed closing its rows never reach
"""
import isolate  # noqa: F401  first: no test writes the real record or cache
import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from finance import monthly, sniff, sync
from finance.parsers import layouts
from finance.parsers import parse_statements as ps
from finance.parsers import parse_uob as pu

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "statements"


def fixture(name):
    return (FIXTURES / f"{name}.txt").read_text()


def parsed(name, text=None):
    rows, blocks = pu.parse(fixture(name) if text is None else text, f"statements/x/2026/{name}.pdf")
    return rows, blocks, pu.validate(rows, blocks)


class Recognised(unittest.TestCase):
    def test_the_one_account_and_the_savings_accounts_are_two_layouts(self):
        one, stash = fixture("uob-one"), fixture("uob-stash-feb")
        self.assertEqual([(f.layout, f.numbers) for f in sniff.sniff(one)], [("uob-one", frozenset({"1234567890"}))])
        self.assertEqual([(f.layout, f.numbers) for f in sniff.sniff(stash)],
                         [("uob-savings", frozenset({"9876543210"}))])

    def test_a_uniplus_account_is_a_savings_account(self):
        uniplus = fixture("uob-stash-feb").replace("Stash Account", "Uniplus Account")
        self.assertEqual([f.layout for f in sniff.sniff(uniplus)], ["uob-savings"])

    def test_no_other_statement_reads_as_uob(self):
        for p in sorted(FIXTURES.glob("*.txt")):
            if not p.name.startswith("uob-"):
                with self.subTest(p.name):
                    self.assertFalse(pu.detect(p.read_text()))

    def test_the_registry_declares_both_from_a_public_sample(self):
        one, savings = layouts.get("uob-one"), layouts.get("uob-savings")
        for lay in (one, savings):
            self.assertEqual((lay.parser, lay.kind, lay.bank, lay.csv), ("uob", "bank account", "uob", "uob"))
            self.assertEqual(lay.status, layouts.SAMPLE)
            self.assertNotIn("unverified", lay.setup + lay.detected_by)
        self.assertEqual((one.folder, savings.folder), ("uob-one", "uob-savings"))
        self.assertEqual(layouts.of_folder("uob", "uob-stash").name, "uob-savings")
        self.assertEqual(layouts.of_folder("uob", "uob-one").name, "uob-one")

    def test_setup_offers_both_with_their_mark_and_name(self):
        from finance import api
        offered = {p["id"]: p for p in api.get_setup(None)["providers"]}
        self.assertEqual(offered["uob-one"], {"id": "uob-one", "label": "UOB One account", "status": layouts.SAMPLE,
                                              "parser": "uob", "bank": "uob", "title": "UOB One"})
        self.assertEqual((offered["uob-savings"]["bank"], offered["uob-savings"]["title"]), ("uob", "UOB Savings"))

    def test_a_uob_folder_is_sent_with_its_name_and_mark(self):
        from finance import model
        cfg = model.Config.__new__(model.Config)
        cfg.folders = [model.Folder("statements/uob-savings", "uob")]
        cfg.root = Path(tempfile.gettempdir())
        with mock.patch("finance.snapshot.analytics_folder", return_value=""):
            [sent] = monthly.folders(cfg)
        self.assertEqual((sent["title"], sent["bank"]), ("UOB Savings", "uob"))


class TheOneAccount(unittest.TestCase):
    def setUp(self):
        self.rows, self.blocks, self.bad = parsed("uob-one")
        [self.block] = self.blocks

    def test_the_chain_balances(self):
        self.assertEqual(self.bad, [])
        self.assertTrue(self.block["ok"])
        self.assertEqual((self.block["opening"], self.block["closing"]), (4210.55, 7108.63))
        # the printed Total line: 318.15 out, 3,216.23 in
        self.assertEqual((self.block["total_out"], self.block["total_in"]), (318.15, 3216.23))

    def test_the_block(self):
        self.assertEqual((self.block["account"], self.block["account_no"]), ("UOB One Account", "123-456-789-0"))
        self.assertEqual(pu.period_name(self.block), "123-456-789-0 · SGD · 2026-03")
        self.assertEqual(self.block["period_end"], "2026-03-31")

    def test_rows_are_the_printed_dates_and_signs(self):
        self.assertEqual([(r["date"], r["amount"]) for r in self.rows], [
            ("2026-03-01", -12.4), ("2026-03-03", 3200.0), ("2026-03-03", -250.0), ("2026-03-09", -48.95),
            ("2026-03-14", 15.0), ("2026-03-21", -6.8), ("2026-03-31", 0.5), ("2026-03-31", 0.73)])
        self.assertEqual(self.rows[4]["bank_txn_type"], "UOB PAYNOW-FAST")

    def test_a_credit_and_a_debit_on_one_day(self):
        giro, transfer = self.rows[1], self.rows[2]
        self.assertEqual((giro["direction"], giro["counterparty"], giro["balance_after"]),
                         ("In", "ACME LABS PTE. LTD.", 7398.15))
        self.assertEqual((transfer["direction"], transfer["counterparty"], transfer["reference"],
                          transfer["memo"], transfer["balance_after"]),
                         ("Out", "LIM AH KOW", "mBK-0012345678", "RENT MAR", 7148.15))

    def test_a_debit_card_row_names_the_merchant(self):
        kopi = self.rows[0]
        self.assertEqual((kopi["merchant"], kopi["merchant_country"], kopi["purchase_date"]),
                         ("KOPI CORNER", "SGP", "2026-02-28"))

    def test_the_page_break_carries_the_chain(self):
        # page 2 ends at BALANCE C/F 7,099.20 and page 3 brings it forward; neither is a row
        self.assertEqual(len(self.rows), 8)
        self.assertEqual([m[1:] for m in self.block["_marks"]], [("cf", 709920), ("bf", 709920), ("cf", 710863)])
        self.assertFalse([r for r in self.rows if "BALANCE" in r["bank_txn_type"]])

    def test_a_statement_missing_its_last_page_is_refused(self):
        # page 2 alone ends at its own BALANCE C/F, 7,099.20; the overview prints 7,108.63
        text = fixture("uob-one")
        cut = text[:text.index("\fPage 3 of 3") if "\fPage 3 of 3" in text else text.rindex("Page 3 of 3")]
        rows, blocks, bad = parsed("uob-one", cut)
        self.assertEqual(len(rows), 4)
        self.assertEqual(bad, [("", "Account Overview", 7099.2, 7108.63)])
        self.assertFalse(blocks[0]["ok"])

    def test_no_overview_balance_does_not_close(self):
        text = fixture("uob-one").replace("123-456-789-0                                    SGD", "", 1)
        _, blocks, bad = parsed("uob-one", text)
        self.assertIn("no overview balance printed", [b[3] for b in bad])
        self.assertFalse(blocks[0]["ok"])

    def test_one_account_failing_does_not_fail_another(self):
        # two accounts in one statement: the Stash one does not balance, the One does
        rows, blocks, bad = parsed("uob-one", fixture("uob-one") + "\f" + fixture("uob-stash-unbalanced"))
        self.assertEqual({b["account_no"]: b["ok"] for b in blocks}, {"123-456-789-0": True, "987-654-321-0": False})
        self.assertEqual([b[1] for b in bad], ["BALANCE C/F"])

    def test_a_card_purchase_keeps_its_day_and_reference(self):
        kopi = self.rows[0]
        self.assertEqual((kopi["purchase_date"], kopi["reference"]), ("2026-02-28", "4800123456"))
        # bought on 31 Dec, posted on 2 Jan: the purchase is in the old year
        text = (fixture("uob-one").replace("01 Mar 2026 to 31 Mar 2026", "01 Jan 2026 to 31 Jan 2026")
                .replace("01 Mar        Misc", "02 Jan        Misc").replace("28 Feb 4800123456", "31 Dec 4800123456"))
        rows, _, _ = parsed("uob-one", text)
        self.assertEqual((rows[0]["date"], rows[0]["purchase_date"]), ("2026-01-02", "2025-12-31"))

    def test_a_page_footer_never_joins_a_row(self):
        text = " ".join(r["memo"] + r["merchant"] + r["counterparty"] for r in self.rows)
        for word in ("United Overseas", "Page", "Account Transaction", "SGD"):
            self.assertNotIn(word, text)

    def test_a_page_whose_carried_balance_is_wrong_is_caught(self):
        text = fixture("uob-one")
        bent = text.replace("BALANCE C/F                                                                                     7,099.20",
                            "BALANCE C/F                                                                                     7,099.30", 1)
        self.assertNotEqual(bent, text)
        _, blocks, bad = parsed("uob-one", bent)
        self.assertTrue(bad)
        self.assertFalse(blocks[0]["ok"])

    def test_a_bent_running_balance_is_caught(self):
        _, blocks, bad = parsed("uob-one", fixture("uob-one").replace("7,148.15", "7,148.25", 1))
        self.assertTrue(bad)
        self.assertFalse(blocks[0]["ok"])

    def test_a_missing_running_balance_fails_the_chain(self):
        lost = fixture("uob-one").replace("6.80                            7,107.40", "6.80                                    ", 1)
        _, blocks, bad = parsed("uob-one", lost)
        self.assertIn("no running balance printed", [b[3] for b in bad])
        self.assertFalse(blocks[0]["ok"])

    def test_a_total_the_rows_do_not_make_is_caught(self):
        _, blocks, bad = parsed("uob-one", fixture("uob-one").replace("318.15", "318.25", 1))
        self.assertEqual([b[1] for b in bad], ["Total"])
        self.assertFalse(blocks[0]["ok"])

    def test_no_closing_balance_does_not_close(self):
        cut = fixture("uob-one").replace("BALANCE C/F", "", 2)
        cut = cut.replace("              BALANCE B/F                                                                                     7,099.20\n", "")
        _, blocks, bad = parsed("uob-one", cut)
        self.assertFalse(blocks[0]["ok"])

    def test_numbers_in_the_description_stay_text(self):
        text = fixture("uob-one").replace("RENT MAR", "RENT 250.00", 1)
        rows, blocks, bad = parsed("uob-one", text)
        self.assertEqual(bad, [])
        self.assertEqual((rows[2]["memo"], rows[2]["amount"]), ("RENT 250.00", -250.0))

    def test_money_is_held_in_integer_cents(self):
        self.assertIsInstance(self.rows[0]["_cents"], int)
        self.assertEqual(self.block["_closing_c"], 710863)

    def test_a_statement_over_the_new_year_keeps_december_in_the_old_year(self):
        text = (fixture("uob-one").replace("01 Mar 2026 to 31 Mar 2026", "15 Dec 2025 to 14 Jan 2026")
                .replace("01 Mar        Misc", "29 Dec        Misc").replace("03 Mar        Inward", "05 Jan        Inward"))
        rows, _, _ = parsed("uob-one", text)
        self.assertEqual((rows[0]["date"], rows[1]["date"]), ("2025-12-29", "2026-01-05"))


class TheStashAccount(unittest.TestCase):
    def test_february_balances_with_a_credit_and_a_debit_on_one_day(self):
        rows, [block], bad = parsed("uob-stash-feb")
        self.assertEqual(bad, [])
        self.assertTrue(block["ok"])
        self.assertEqual(block["account"], "UOB Stash Account")
        same_day = [(r["direction"], r["amount"]) for r in rows if r["date"] == "2026-02-16"]
        self.assertEqual(same_day, [("Out", -100.0), ("In", 25.0)])

    def test_march_is_brought_forward_from_februarys_closing(self):
        _, [feb], _ = parsed("uob-stash-feb")
        rows, [mar], bad = parsed("uob-stash-mar")
        self.assertEqual(bad, [])
        self.assertTrue(mar["ok"])
        self.assertEqual(mar["opening"], feb["closing"])
        self.assertEqual((mar["opening"], mar["closing"]), (12925.41, 12895.8))
        # the brought forward line, dated the period's first day, is the opening and never a row
        self.assertEqual([r["date"] for r in rows], ["2026-03-05", "2026-03-31"])

    def test_a_statement_that_does_not_balance_is_refused(self):
        _, [block], bad = parsed("uob-stash-unbalanced")
        self.assertFalse(block["ok"])
        self.assertEqual(bad, [("", "BALANCE C/F", 12925.41, 12926.41)])


class TheRun(unittest.TestCase):
    """The parser as the run starts it: a balanced folder writes both CSVs and
    the run's block checks pass; an unbalanced statement exits non-zero and
    its block fails the checks, so nothing is loaded."""

    def run_main(self, names):
        with tempfile.TemporaryDirectory() as d:
            folder, out = Path(d) / "statements" / "uob-savings" / "2026", Path(d) / "out"
            folder.mkdir(parents=True)
            for n in names:
                (folder / f"{n}.pdf").write_bytes(b"%PDF-1.4\n")
            texts = {f"{n}.pdf": fixture(n) for n in names}
            argv = ["parse_uob", str(folder.parent), "--out", str(out), "--no-text"]
            with mock.patch.object(sys, "argv", argv), mock.patch.object(pu.shutil, "which", return_value="x"), \
                    mock.patch.object(ps, "pdf_to_text", side_effect=lambda pdf, write=True: texts[Path(pdf).name]):
                try:
                    with contextlib.redirect_stdout(io.StringIO()):
                        pu.main()
                    code = 0
                except SystemExit as e:
                    code = e.code
            # the run reads each parser's CSVs from its own folder under the run's
            run = Path(d) / "run"
            (run / "uob").mkdir(parents=True)
            for name in ("transactions.csv", "period_balances.csv"):
                (run / "uob" / name).write_text((out / name).read_text())
            blocks, rows = sync._read_csvs(run)
            return code, blocks, rows

    def test_balanced_statements_pass_the_run(self):
        code, blocks, rows = self.run_main(["uob-stash-feb", "uob-stash-mar"])
        self.assertIn(code, (0, None))
        self.assertEqual((len(blocks), len(rows)), (2, 6))
        self.assertEqual([b["period"] for b in blocks],
                         ["987-654-321-0 · SGD · 2026-02", "987-654-321-0 · SGD · 2026-03"])
        self.assertEqual(sync.check_blocks(_Parsed(blocks, rows)), [])

    def test_an_unbalanced_statement_stops_the_run(self):
        code, blocks, rows = self.run_main(["uob-stash-unbalanced"])
        self.assertNotIn(code, (0, None))
        self.assertEqual([p.code for p in sync.check_blocks(_Parsed(blocks, rows))], ["block-not-reconciled"])


class _Parsed:
    def __init__(self, blocks, rows):
        self.blocks, self.rows = blocks, rows


class Upload(unittest.TestCase):
    def test_an_uploaded_statement_is_named_for_its_month(self):
        with mock.patch.object(monthly, "_text", return_value=fixture("uob-one")):
            self.assertEqual(monthly.canonical_name("uob", "upload.pdf"), ("2026", "2026-03.pdf"))

    def test_another_pdf_is_refused(self):
        with mock.patch.object(monthly, "_text", return_value="Statement of Account\nPeriod: 01 Mar 2026 to 31 Mar 2026\n"):
            with self.assertRaises(monthly.UploadRefused):
                monthly.canonical_name("uob", "upload.pdf")

    def test_a_savings_statement_goes_to_the_savings_folder(self):
        folders = [sniff.Place("statements/uob-one", "uob"), sniff.Place("statements/uob-savings", "uob")]
        r = sniff.route(sniff.sniff(fixture("uob-stash-feb")), folders)
        self.assertEqual(r.folder, "statements/uob-savings")
        r = sniff.route(sniff.sniff(fixture("uob-one")), folders)
        self.assertEqual(r.folder, "statements/uob-one")


if __name__ == "__main__":
    unittest.main()
