"""server/finance/parsers/parse_ocbc.py over an invented OCBC 360 statement (G11).

server/tests/fixtures/statements/ocbc-360.txt is written by hand in the layout of a
public sample statement; every name and number in it is made up."""
import isolate  # noqa: F401  first: no test writes the real record or cache
import csv
import tempfile
import unittest
from pathlib import Path

from finance.parsers import parse_ocbc as po

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "statements" / "ocbc-360.txt"


class OcbcTest(unittest.TestCase):
    def setUp(self):
        self.text = FIXTURE.read_text()
        self.rows, self.block = po.parse(self.text, "ocbc-360/2026/2026-03.pdf")
        self.bad = po.validate(self.rows, self.block)

    def test_it_is_recognised(self):
        self.assertTrue(po.detect(self.text))
        self.assertFalse(po.detect((FIXTURE.parent / "posb-deposit.txt").read_text()))

    def test_the_chain_balances(self):
        self.assertEqual(self.bad, [])
        self.assertTrue(self.block["ok"])
        self.assertEqual((self.block["opening"], self.block["closing"]), (4210.55, 7108.63))
        self.assertEqual((self.block["total_in"], self.block["total_out"]), (3216.23, 318.15))

    def test_the_block(self):
        self.assertEqual(self.block["account"], "OCBC 360 Account")
        self.assertEqual(po.period_name(self.block), "601234567001 · SGD · 2026-03")

    def test_rows_carry_signs_dates_and_detail(self):
        self.assertEqual(len(self.rows), 7)
        grab = self.rows[0]
        self.assertEqual((grab["date"], grab["amount"], grab["card_last4"], grab["merchant_country"]),
                         ("2026-03-01", -12.4, "4242", "SGP"))
        self.assertTrue(grab["merchant"].startswith("GRAB*"))
        salary = self.rows[1]
        self.assertEqual((salary["amount"], salary["counterparty"]), (3200.0, "ACME LABS PTE. LTD."))

    def test_a_page_break_does_not_leak_into_a_row(self):
        self.assertNotIn("STATEMENT", " ".join(r["memo"] for r in self.rows))

    def test_a_bent_balance_is_caught(self):
        bent = self.text.replace("7,099.20", "7,099.30", 1)
        rows, block = po.parse(bent, "x")
        self.assertTrue(po.validate(rows, block))
        self.assertFalse(block["ok"])

    def test_a_january_statement_keeps_december_rows_in_the_old_year(self):
        jan = (self.text.replace("1 MAR 2026 TO 31 MAR 2026", "28 DEC 2025 TO 27 JAN 2026")
               .replace("01 MAR       01 MAR", "29 DEC       29 DEC")
               .replace("03 MAR       03 MAR", "05 JAN       05 JAN"))
        rows, _ = po.parse(jan, "x")
        self.assertEqual(rows[0]["date"], "2025-12-29")
        self.assertEqual(rows[1]["date"], "2026-01-05")

    def test_a_missing_running_balance_fails_the_chain(self):
        lost = self.text.replace("250.00                          7,148.15", "250.00                                  ", 1)
        rows, block = po.parse(lost, "x")
        self.assertTrue(po.validate(rows, block))
        self.assertFalse(block["ok"])

    def test_numbers_in_the_description_stay_text(self):
        text = (self.text.replace("OTHR RENT MAR", "OTHR RENT 250.00", 1)
                .replace("FAST PAYMENT     ", "FAST PAYMENT 2.50", 1))
        rows, block = po.parse(text, "x")
        self.assertEqual(po.validate(rows, block), [])
        rent = rows[2]
        self.assertEqual(rent["bank_txn_type"], "OCBC Fast Payment 2.50")
        self.assertEqual(rent["memo"], "OTHR RENT 250.00")
        self.assertEqual(rent["amount"], -250.0)

    def test_money_is_held_in_integer_cents(self):
        self.assertIsInstance(self.rows[0]["_cents"], int)
        self.assertEqual(self.block["_closing_c"], 710863)


class OcbcUploadTest(unittest.TestCase):
    def test_an_uploaded_statement_is_named_for_its_month(self):
        from unittest import mock
        from finance import monthly
        with mock.patch.object(monthly, "_text", return_value=FIXTURE.read_text()):
            self.assertEqual(monthly.canonical_name("ocbc", "upload.pdf"), ("2026", "2026-03.pdf"))

    def test_another_pdf_with_a_date_range_is_refused(self):
        from unittest import mock
        from finance import monthly
        with mock.patch.object(monthly, "_text", return_value="An invoice\n1 MAR 2026 TO 31 MAR 2026\n"):
            with self.assertRaises(monthly.UploadRefused):
                monthly.canonical_name("ocbc", "upload.pdf")


class OcbcInTheRunTest(unittest.TestCase):
    def test_the_run_reads_the_ocbc_csvs(self):
        from finance.parsers import parse_statements as ps
        from finance import sync
        rows, block = po.parse(FIXTURE.read_text(), "statements/ocbc-360/2026/2026-03.pdf")
        po.validate(rows, block)
        ps.add_keys(rows)
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "ocbc"
            out.mkdir()
            with (out / "transactions.csv").open("w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=ps.TXN_COLS, extrasaction="ignore")
                w.writeheader()
                w.writerows(rows)
            with (out / "period_balances.csv").open("w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=ps.BAL_COLS, extrasaction="ignore")
                w.writeheader()
                w.writerow({**block, "period": po.period_name(block), "reconciled": block["ok"]})
            blocks, got = sync._read_csvs(Path(d))
        self.assertEqual((len(blocks), len(got)), (1, 7))


if __name__ == "__main__":
    unittest.main()
