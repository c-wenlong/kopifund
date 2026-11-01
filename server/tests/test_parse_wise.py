"""parse_wise over one Wise export set: one PDF per currency balance.

The layout is written out here at the real column positions, so the column
split, the newest-first ordering and the type table are checked on every
machine. The fixtures wise-sgd.txt, wise-nok.txt and wise-usd.txt are
scrubbed copies of the pdftotext dumps of the real export, under
server/tests/fixtures/statements/.
"""
import isolate  # noqa: F401  first: no test writes the real record or cache
import sys
import unittest
from collections import Counter
from pathlib import Path

from tests import parse_support as sup

from finance.parsers import parse_statements as ps
from finance.parsers import parse_wise as ws

# the column right-edges Wise prints: Incoming 104, Outgoing 122, Amount 141
EDGES = dict(incoming=104, outgoing=122, amount=141)


def place(*pairs):
    """A line with each text ending at the given column."""
    width = max(end for end, _ in pairs)
    out = [" "] * width
    for end, txt in pairs:
        out[end - len(txt):end] = txt
    return "".join(out)


def row(desc, incoming, outgoing, balance, date, txn_id):
    """A statement row: the description line with its columns, then the date
    line that carries Wise's transaction id."""
    cols = [(2 + len(desc), desc), (EDGES["amount"], balance)]
    if incoming:
        cols.append((EDGES["incoming"], incoming))
    if outgoing:
        cols.append((EDGES["outgoing"], outgoing))
    return place(*cols).rstrip() + f"\n  {date} | Transaction: {txn_id}\n\n"


def account_block(label, value, right_label, right_value):
    return ("   " + "Account Holder".ljust(70) + label.ljust(35) + right_label + "\n"
            + "   " + "Mei Ling Tan".ljust(70) + value.ljust(35) + right_value + "\n")


def closing_line(ccy, amount):
    left = f"   {ccy} on 6 September 2026 [GMT+08:00]"
    return left + " " * (129 - len(amount) - len(left)) + amount + " " + ccy


HEADER = place((13, "Description"), (104, "Incoming"), (122, "Outgoing"), (140, "Amount"))
FOOT = """
   Wise Asia-Pacific Pte. Ltd. is a company incorporated under the laws of Singapore with Unique Entity Number 201422384R. We are licensed by
   the Monetary Authority of Singapore ("MAS") as a major payment institution under the Payment Service Act ("PSA").

   Need help? Visit wise.com/help

ref:a0a0a0a0-0000-4000-8000-000000000000                                                                                                   1/1
"""


def page(ccy, acct, closing, rows):
    return f"""\
  Wise Asia-Pacific Pte Ltd.
  2 Tanjong Katong Road, #07-01, PLQ3
  Singapore

  {ccy} statement
  6 July 2024 [GMT+08:00] - 6 September 2026 [GMT+08:00]

  Generated on: 6 September 2026

{acct}

{closing_line(ccy, closing)}

{HEADER}

{rows}{FOOT}"""


SGD_ACCT = account_block("Account number", "150-246-80", "Bank code", "0516")
USD_ACCT = account_block("Account number", "100200300400500", "Routing number", "012345678")
NOK_ACCT = account_block("IBAN", "GB00 TRWI 0000 1111 2222 33", "Swift/BIC", "TRWIGB2LXXX")

# newest first, the way Wise prints them
SGD_ROWS = (
    row("Sent money to 80000001", None, "-43.67", "0.00", "14 July 2025", "TRANSFER-4163248355")
    + row("Converted 140.00 NOK to 18.67 SGD", "18.67", None, "43.67",
          "14 July 2025", "BALANCE-0272333191")
    + row("Card transaction of 5.00 SGD issued by Kopitiam, Singapore", None, "-5.00", "25.00",
          "20 August 2024", "CARD-4411")
    + row("Topped up account", "30.00", None, "30.00", "6 July 2024", "TRANSFER-5012495484"))

NOK_ROWS = (
    row("Converted 140.00 NOK to 18.67 SGD (fee: 0.62 NOK)", None, "-139.38", "0.00",
        "14 July 2025", "BALANCE-0272333191")
    + row("Wise Charges for: BALANCE-0272333191", None, "-0.62", "139.38",
          "14 July 2025", "FEE-BALANCE-0272333191")
    + row("Received money from Amita Roy with reference DINNER JUNE", "140.00", None, "140.00",
          "2 June 2025", "TRANSFER-2622102049"))

# a description the type table does not know, on a statement of its own so the
# warning it prints belongs to the one test that asks for it
OTHER_ROWS = row("Refunded by Wise", "2.00", None, "2.00", "1 September 2024", "REFUND-77")


def small_run(sgd_closing="0.00", prefix=""):
    sources = [(prefix + "sgd", page("SGD", SGD_ACCT, sgd_closing, SGD_ROWS), "SGD"),
               (prefix + "nok", page("NOK", NOK_ACCT, "0.00", NOK_ROWS), "NOK"),
               (prefix + "usd", page("USD", USD_ACCT, "0.00", ""), "USD")]
    ps.WARNINGS.clear()
    return ws.run(sources)


class SmallExport(unittest.TestCase):
    def setUp(self):
        self.rows, self.blocks, self.stmts, self.bad, self.no_amount = small_run()

    def by(self, ccy, ref):
        return next(r for r in self.rows if r["currency"] == ccy and r["reference"] == ref)

    def month(self, ccy, ym):
        return next(b for b in self.blocks if b["currency"] == ccy and b["month"] == ym)

    def test_every_row_is_kept_and_every_month_reconciles(self):
        self.assertEqual(len(self.rows), 7)
        self.assertEqual(self.bad, [])
        self.assertEqual(self.no_amount, [])
        self.assertTrue(all(b["ok"] for b in self.blocks),
                        [b for b in self.blocks if not b["ok"]])

    def test_the_currency_comes_from_the_statement_heading(self):
        self.assertEqual(ws.statement_currency(page("NOK", NOK_ACCT, "0.00", "")), "NOK")
        self.assertIsNone(ws.statement_currency("nothing here\n"))

    def test_every_balance_takes_the_sgd_files_account_number(self):
        self.assertEqual({s["account_no"] for s in self.stmts}, {"150-246-80"})
        nok = next(s for s in self.stmts if s["currency"] == "NOK")
        self.assertEqual(nok["local_account"], "GB00 TRWI 0000 1111 2222 33")
        self.assertIn("taken from the SGD balance", self.month("NOK", "2025-06")["note"])

    def test_the_transaction_id_is_the_reference(self):
        self.assertEqual(sorted(r["reference"] for r in self.rows if r["currency"] == "SGD"),
                         ["BALANCE-0272333191", "CARD-4411",
                          "TRANSFER-4163248355", "TRANSFER-5012495484"])

    def test_rows_are_reversed_into_oldest_first_and_a_day_keeps_its_order(self):
        sgd = [r for r in self.rows if r["currency"] == "SGD"]
        self.assertEqual([r["date"] for r in sgd],
                         ["2024-07-06", "2024-08-20", "2025-07-14", "2025-07-14"])
        self.assertEqual([r["bank_txn_type"] for r in sgd][-2:],
                         ["Wise Conversion In", "Wise Transfer"])

    def test_top_up_is_internal(self):
        r = self.by("SGD", "TRANSFER-5012495484")
        self.assertEqual((r["bank_txn_type"], r["flow_class"], r["amount"], r["memo"]),
                         ("Wise Top-up", "Internal - Wise", 30.0, "Topped up account"))

    def test_conversion_sides_carry_the_other_currency(self):
        inn = self.by("SGD", "BALANCE-0272333191")
        self.assertEqual((inn["bank_txn_type"], inn["flow_class"], inn["amount"],
                          inn["fx_original"]),
                         ("Wise Conversion In", "Internal - Wise", 18.67, "NOK 140.00"))
        out = self.by("NOK", "BALANCE-0272333191")
        self.assertEqual((out["bank_txn_type"], out["flow_class"], out["amount"],
                          out["fx_original"]),
                         ("Wise Conversion Out", "Internal - Wise", -139.38, "SGD 18.67"))
        self.assertEqual(out["fee"], 0.62)
        self.assertIn("fee 0.62 NOK charged as its own row", out["memo"])
        self.assertIsNone(inn["fee"])

    def test_the_conversion_fee_is_its_own_external_row(self):
        r = self.by("NOK", "FEE-BALANCE-0272333191")
        self.assertEqual((r["bank_txn_type"], r["flow_class"], r["counterparty"], r["amount"]),
                         ("Wise Fee", "External", "Wise", -0.62))
        self.assertEqual(r["memo"], "charges for BALANCE-0272333191")

    def test_money_received_names_the_sender_and_keeps_the_reference_text(self):
        r = self.by("NOK", "TRANSFER-2622102049")
        self.assertEqual((r["bank_txn_type"], r["flow_class"], r["direction"],
                          r["counterparty"], r["amount"]),
                         ("Wise Transfer", "External", "In", "Amita Roy", 140.0))
        self.assertIn("with reference DINNER JUNE", r["memo"])

    def test_money_sent_names_the_recipient(self):
        r = self.by("SGD", "TRANSFER-4163248355")
        self.assertEqual((r["bank_txn_type"], r["flow_class"], r["direction"],
                          r["counterparty"], r["amount"]),
                         ("Wise Transfer", "External", "Out", "80000001", -43.67))

    def test_a_card_row_splits_the_city_off_the_merchant(self):
        r = self.by("SGD", "CARD-4411")
        self.assertEqual((r["bank_txn_type"], r["flow_class"], r["amount"]),
                         ("Wise Card Payment", "External", -5.0))
        self.assertEqual((r["merchant"], r["merchant_country"]), ("Kopitiam", "SGP"))
        self.assertIsNone(r["fx_original"])          # charged in the wallet's own currency

    def test_months_with_rows_become_blocks_plus_the_export_month(self):
        sgd = [b for b in self.blocks if b["currency"] == "SGD"]
        months = {b["month"]: b for b in sgd}
        self.assertEqual(sorted(months), ws.every_month("2024-07", "2026-09"))
        idle = [m for m in sorted(months) if not months[m]["n_txns"] and m != "2026-09"]
        self.assertTrue(idle)
        m, before = idle[0], sorted(months)[sorted(months).index(idle[0]) - 1]
        self.assertEqual(months[m]["note"], "no activity this month")
        self.assertEqual((months[m]["opening"], months[m]["closing"]),
                         (months[before]["closing"], months[before]["closing"]))
        self.assertTrue(months[m]["ok"])
        self.assertIn("closes the export", months["2026-09"]["note"])
        self.assertEqual(ws.period_name(sgd[0]), "150-246-80 · SGD · 2024-07")

    def test_a_balance_with_no_rows_is_one_export_month_block(self):
        usd = [b for b in self.blocks if b["currency"] == "USD"]
        self.assertEqual(len(usd), 1)
        self.assertEqual((usd[0]["month"], usd[0]["n_txns"], usd[0]["opening"],
                          usd[0]["closing"], usd[0]["ok"]),
                         ("2026-09", 0, 0.0, 0.0, True))

    def test_the_opening_is_derived_from_the_closing_and_from_the_first_row(self):
        self.assertEqual([s["opening"] for s in self.stmts], [0.0, 0.0, 0.0])
        self.assertFalse([w for w in ps.WARNINGS if "opening balance" in w])

    def test_a_closing_the_rows_do_not_add_up_to_is_reported(self):
        """A wrong closing moves the derived opening, and the first printed
        running balance then disagrees with it: the run stops on that drift."""
        rows, blocks, stmts, bad, _ = small_run(sgd_closing="9.00")
        self.assertTrue(any("opening balance" in w for w in ps.WARNINGS), ps.WARNINGS)
        self.assertEqual([(b[1], b[2], b[4], b[5]) for b in bad],
                         [("SGD", "2024-07-06", 39.0, 30.0)])

    def test_the_nok_blocks_take_the_rate_the_conversion_printed(self):
        rate = 18.67 / 140.00          # both files print both sides of the pair
        for ym in ("2025-06", "2025-07", "2026-09"):
            self.assertAlmostEqual(self.month("NOK", ym)["fx_to_sgd"], rate, places=6)
        self.assertIn("the first one after this month", self.month("NOK", "2025-06")["note"])
        self.assertAlmostEqual(self.month("NOK", "2025-06")["closing_sgd"],
                               round(140.0 * rate, 2), places=2)

    def test_keys_do_not_depend_on_the_source_file(self):
        again = small_run()[0]
        other = small_run(prefix="renamed-")[0]
        self.assertEqual([r["txn_key"] for r in again], [r["txn_key"] for r in other])
        self.assertEqual(len(set(r["txn_key"] for r in again)), len(again))


class UnknownDescription(unittest.TestCase):
    def test_it_lands_in_wise_other_and_says_so(self):
        ps.WARNINGS.clear()
        rows, blocks, _, bad, _ = ws.run(
            [("other", page("SGD", SGD_ACCT, "2.00", OTHER_ROWS), "SGD")])
        r, = rows
        self.assertEqual((r["bank_txn_type"], r["flow_class"], r["counterparty"], r["memo"]),
                         ("Wise Other", "External", "Refunded by Wise", "Refunded by Wise"))
        self.assertEqual(bad, [])
        self.assertTrue(all(b["ok"] for b in blocks))
        self.assertTrue(any("Wise Other" in w and "Refunded by Wise" in w
                            for w in ps.WARNINGS), ps.WARNINGS)


class RealExport(unittest.TestCase):
    def setUp(self):
        self.parsed = sup.run_wise(["wise-sgd.txt", "wise-nok.txt", "wise-usd.txt"])

    def test_three_balances_all_reconciled(self):
        self.assertEqual({s["currency"] for s in self.parsed.stmts}, {"SGD", "NOK", "USD"})
        self.assertEqual(len(self.parsed.rows), 6)
        self.assertEqual(len(self.parsed.blocks), 27 + 16 + 1)
        self.assertEqual(self.parsed.bad, [])
        self.assertEqual(self.parsed.no_amount, [])
        self.assertTrue(all(b["ok"] for b in self.parsed.blocks))

    def test_openings_and_closings(self):
        self.assertEqual({s["currency"]: s["closing"] for s in self.parsed.stmts},
                         {"SGD": 0.0, "NOK": 0.0, "USD": 0.0})
        self.assertEqual({s["currency"]: s["opening"] for s in self.parsed.stmts},
                         {"SGD": 0.0, "NOK": 0.0, "USD": 0.0})

    def test_every_balance_is_the_one_wise_account(self):
        self.assertEqual({r["account_no"] for r in self.parsed.rows}, {"150-246-80"})
        self.assertEqual({b["account_no"] for b in self.parsed.blocks}, {"150-246-80"})

    def test_the_rows_are_typed_and_none_fell_through(self):
        self.assertEqual(Counter(r["bank_txn_type"] for r in self.parsed.rows),
                         Counter({"Wise Transfer": 2, "Wise Top-up": 1, "Wise Fee": 1,
                                  "Wise Conversion In": 1, "Wise Conversion Out": 1}))
        self.assertEqual(Counter(r["flow_class"] for r in self.parsed.rows),
                         Counter({"Internal - Wise": 3, "External": 3}))

    def test_month_blocks_per_balance(self):
        by_ccy = Counter(b["currency"] for b in self.parsed.blocks)
        # every month from the balance's first row to the export month
        self.assertEqual(by_ccy, Counter({"SGD": len(ws.every_month("2024-07", "2026-09")),
                                          "NOK": len(ws.every_month("2025-06", "2026-09")), "USD": 1}))
        self.assertEqual(Counter(b["currency"] for b in self.parsed.blocks if b["n_txns"]),
                         Counter({"SGD": 2, "NOK": 2}))

    def test_the_nok_rate_comes_off_the_july_conversion(self):
        nok = [b for b in self.parsed.blocks if b["currency"] == "NOK"]
        self.assertTrue(all(b["fx_to_sgd"] for b in nok), [b["month"] for b in nok])
        self.assertAlmostEqual(nok[0]["fx_to_sgd"], 18.67 / 140.00, places=6)

    def test_keys_are_unique(self):
        keys = [r["txn_key"] for r in self.parsed.rows]
        self.assertEqual(len(keys), len(set(keys)))

    def test_no_warnings(self):
        self.assertEqual(ps.WARNINGS, [])


class NoClosingPrinted(unittest.TestCase):
    """Wise prints no opening, so the closing is the one figure the rows are
    checked against. A file without it used to warn, open at zero and then
    pass every month against balances derived from its own rows."""

    def stripped(self):
        return sup.drop_line("wise-sgd.txt", ["SGD on 6 September 2026"])

    def test_run_refuses_rather_than_reconciling_against_itself(self):
        with self.assertRaises(ws.MissingClosing) as e:
            sup.run_wise(["wise-sgd.txt", "wise-sek.txt"],
                         lines={"wise-sgd.txt": self.stripped()})
        self.assertIn("wise-sgd.txt", str(e.exception))
        self.assertIn("SGD", str(e.exception))

    def test_main_exits_non_zero_and_writes_nothing(self):
        import tempfile
        from contextlib import redirect_stdout
        from io import StringIO
        from unittest import mock
        texts = {"sgd.pdf": self.stripped(), "sek.pdf": sup.text("wise-sek.txt")}
        with tempfile.TemporaryDirectory() as out, \
                mock.patch.object(ws.shutil, "which", return_value="/bin/pdftotext"), \
                mock.patch.object(ws, "collect_pdfs", return_value=list(texts)), \
                mock.patch.object(ws, "pdf_to_text", side_effect=lambda p, write: texts[p]), \
                mock.patch.object(sys, "argv", ["parse_wise.py", "--out", out]), \
                redirect_stdout(StringIO()):
            with self.assertRaises(SystemExit) as e:
                ws.main()
            self.assertNotIn(e.exception.code, (0, None))
            self.assertIn("no closing balance printed", str(e.exception.code))
            self.assertEqual(list(Path(out).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
