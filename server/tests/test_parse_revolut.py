"""parse_revolut over the combined "all accounts" export.

The fixture revolut-all.txt is a scrubbed copy of the pdftotext dump of the
real export (five currency sections, 133 rows), under server/tests/fixtures/statements/;
the section splitter and the row enricher are also tested on text written here,
so the layout rules are checked on their own as well.
"""
import isolate  # noqa: F401  first: no test writes the real record or cache
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests import parse_support as sup

from finance.parsers import parse_revolut as rv
from finance.parsers import parse_youtrip as yt
from finance.parsers import places

PAGE = """\
{ccy} Statement
Generated on the 6 Sept 2026
Revolut Technologies Singapore Pte. Ltd.

TAN MEI LING
#01-100                              Account Number 885100012345678

Balance summary

 Product                 Opening balance      Money out      Money in      Closing balance

 Account (E-Money)       {summary}

Account transactions from 10 June 2024 to 6 September 2026

 Date            Description                          Money out           Money in            Balance

{rows}
"""


def page(ccy, summary, rows):
    return PAGE.format(ccy=ccy, summary=summary, rows=rows)


SGD_ROWS = """\
 10 Jun 2024     Apple Pay top-up by *9898                                   S$20.00             S$20.00
                 From: *9898


 12 Jun 2024     H&M                                  S$5.00                                     S$15.00
                 Revolut Rate S$1.00 = 18,690 VND (ECB rate* S$1.00 = 18,782 VND)     93,450 VND
                 To: Hm Vietnam, Ha Noi
                 Card: 400012******3636


 14 Aug 2024     Exchanged to NOK                     S$10.00                                     S$5.00
                 78.93 NOK


 14 Jul 2025     Exchanged to SGD                                            S$1.00               S$6.00
                 Fee: S$0.10                                                 S$1.10
                 8.00 NOK


 14 Jul 2025     TAN MEI LING                         S$6.00                                      S$0.00

 1 Aug 2026      Payment from TAN MEI LING                                   S$3.00               S$3.00
                 From: TAN MEI LING


 1 Aug 2026      Transfer to WANG ZIHAN               S$3.00                                      S$0.00
                 Reference: To WANG ZIHAN
                 To: WANG ZIHAN
"""

NOK_ROWS = """\
 14 Aug 2024     Exchanged to NOK                                            78.93 NOK           78.93 NOK
                 S$10.00


 25 Aug 2024     Kiosk Nord                           66.00 NOK                                  12.93 NOK
                 To: Kiosk Nord, Oslo
                 Card: 400012******3636


 26 Sept 2024    Transfer from AYA SUZUKI                                    5.00 NOK            17.93 NOK
                 Reference: From AYA SUZUKI
                 From: AYA SUZUKI


 14 Jul 2025     Exchanged to SGD                     8.00 NOK                                    9.93 NOK
                 S$1.10
"""

SMALL = (page("SGD", "S$0.00      S$24.10      S$24.10      S$0.00", SGD_ROWS)
         + "\f" + page("NOK", "0.00 NOK      74.00 NOK      83.93 NOK      9.93 NOK", NOK_ROWS))


def small_run():
    sources = [(f"small#{c}", t, c) for c, t in rv.sections(SMALL)]
    return rv.run(sources)


class SectionSplit(unittest.TestCase):
    def test_pages_group_by_the_currency_they_name(self):
        text = page("DKK", "x", "") + "\f" + page("NOK", "x", "") + "\f" + page("NOK", "x", "")
        self.assertEqual([c for c, _ in rv.sections(text)], ["DKK", "NOK"])
        self.assertEqual(rv.sections(text)[1][1].count("NOK Statement"), 2)

    def test_a_page_without_a_heading_stays_with_the_section_before_it(self):
        text = page("NOK", "x", "") + "\f" + "  continued\n"
        self.assertEqual(len(rv.sections(text)), 1)
        self.assertIn("continued", rv.sections(text)[0][1])


class SmallStatement(unittest.TestCase):
    def setUp(self):
        self.rows, self.blocks, self.stmts, self.bad, self.no_amount = small_run()

    def by(self, ccy, title):
        return next(r for r in self.rows if r["currency"] == ccy and r["title"] == title)

    def test_every_row_is_kept_and_every_month_reconciles(self):
        self.assertEqual(len(self.rows), 11)
        self.assertEqual(self.bad, [])
        self.assertEqual(self.no_amount, [])
        self.assertTrue(all(b["ok"] for b in self.blocks), [b for b in self.blocks if not b["ok"]])

    def test_account_number_is_borrowed_by_the_section_that_prints_none(self):
        self.assertEqual({s["account_no"] for s in self.stmts}, {"885100012345678"})

    def test_top_up_is_internal_with_the_card_as_counterparty(self):
        r = self.by("SGD", "Apple Pay top-up by *9898")
        self.assertEqual((r["bank_txn_type"], r["flow_class"], r["amount"], r["counterparty"]),
                         ("Revolut Top-up", "Internal - Revolut", 20.0, "*9898"))

    def test_owner_paynow_is_a_top_up(self):
        r = self.by("SGD", "Payment from TAN MEI LING")
        self.assertEqual((r["bank_txn_type"], r["counterparty"]), ("Revolut Top-up", "TAN MEI LING"))

    def test_card_row_carries_merchant_country_fx_and_card(self):
        r = self.by("SGD", "H&M")
        self.assertEqual(r["bank_txn_type"], "Revolut Payment")
        self.assertEqual((r["merchant"], r["merchant_country"]), ("Hm Vietnam", "VNM"))
        self.assertEqual(r["fx_original"], "VND 93450")
        self.assertEqual(r["card_last4"], "3636")
        self.assertIn("Revolut name: H&M", r["memo"])
        self.assertIn("S$1.00 = 18,690 VND", r["memo"])

    def test_card_row_city_names_the_country_when_no_foreign_amount_is_printed(self):
        r = self.by("NOK", "Kiosk Nord")
        self.assertEqual((r["merchant"], r["merchant_country"]), ("Kiosk Nord", "NOR"))

    def test_exchange_sides(self):
        out = self.by("SGD", "Exchanged to NOK")
        self.assertEqual((out["bank_txn_type"], out["amount"], out["fx_original"], out["memo"]),
                         ("Revolut Exchange Out", -10.0, "NOK 78.93", "NOK"))
        inn = self.by("NOK", "Exchanged to NOK")
        self.assertEqual((inn["bank_txn_type"], inn["amount"], inn["fx_original"]),
                         ("Revolut Exchange In", 78.93, "SGD 10.00"))

    def test_fee_stays_inside_the_column_amount(self):
        r = self.by("SGD", "Exchanged to SGD")
        self.assertEqual(r["amount"], 1.0)
        self.assertEqual(r["fee"], 0.10)
        self.assertIn("fee S$0.10", r["reference"])

    def test_withdrawal_to_the_owner_is_internal(self):
        r = self.by("SGD", "TAN MEI LING")
        self.assertEqual((r["bank_txn_type"], r["flow_class"]), ("Revolut Withdrawal", "Internal - Revolut"))

    def test_transfers_are_external_with_the_person_as_counterparty(self):
        r = self.by("SGD", "Transfer to WANG ZIHAN")
        self.assertEqual((r["bank_txn_type"], r["flow_class"], r["counterparty"], r["reference"]),
                         ("Revolut Transfer", "External", "WANG ZIHAN", "To WANG ZIHAN"))
        r = self.by("NOK", "Transfer from AYA SUZUKI")
        self.assertEqual((r["direction"], r["counterparty"]), ("In", "AYA SUZUKI"))

    def test_months_with_rows_become_blocks_plus_the_export_month(self):
        sgd = {b["month"]: b for b in self.blocks if b["currency"] == "SGD"}
        self.assertEqual(sorted(sgd), rv.every_month("2024-06", "2026-09"))
        active = {m: (b["opening"], b["closing"]) for m, b in sgd.items() if b["n_txns"]}
        self.assertEqual(active, {"2024-06": (0.0, 15.0), "2024-08": (15.0, 5.0),
                                  "2025-07": (5.0, 0.0), "2026-08": (0.0, 0.0)})
        # an idle month carries the balance and says so
        self.assertEqual((sgd["2024-07"]["opening"], sgd["2024-07"]["closing"]), (15.0, 15.0))
        self.assertEqual(sgd["2024-07"]["note"], "no activity this month")
        self.assertTrue(sgd["2024-07"]["ok"])
        self.assertEqual(sgd["2026-09"]["period_end"], "2026-09-06")
        self.assertEqual(sgd["2026-09"]["n_txns"], 0)
        self.assertIn("closes the export", sgd["2026-09"]["note"])
        self.assertNotIn("closes the export", sgd["2024-07"]["note"])
        self.assertEqual(rv.period_name(sgd["2024-06"]), "885100012345678 · SGD · 2024-06")

    def test_foreign_block_takes_the_latest_sgd_rate_seen(self):
        nok = {b["month"]: b for b in self.blocks if b["currency"] == "NOK"}
        self.assertAlmostEqual(nok["2024-08"]["fx_to_sgd"], 10.0 / 78.93, places=6)
        self.assertAlmostEqual(nok["2024-09"]["fx_to_sgd"], 10.0 / 78.93, places=6)
        self.assertAlmostEqual(nok["2025-07"]["fx_to_sgd"], 1.10 / 8.0, places=6)
        self.assertAlmostEqual(nok["2025-07"]["closing_sgd"], round(9.93 * 1.10 / 8.0, 2), places=2)

    def test_summary_check_adds_the_fee_back(self):
        self.assertEqual(rv.check_summary(self.stmts[0], self.rows), [])

    def test_summary_mismatch_is_reported(self):
        s = dict(self.stmts[0], summary_in=99.0)
        self.assertEqual(len(rv.check_summary(s, self.rows)), 1)

    def test_keys_do_not_depend_on_the_source_file(self):
        again = small_run()[0]
        renamed = [(f"other#{c}", t, c) for c, t in rv.sections(SMALL)]
        other = rv.run(renamed)[0]
        self.assertEqual([r["txn_key"] for r in again], [r["txn_key"] for r in other])


class RealExport(unittest.TestCase):
    def setUp(self):
        self.parsed = sup.run_revolut(["revolut-all.txt"])

    def test_five_sections_all_reconciled(self):
        self.assertEqual({s["currency"] for s in self.parsed.stmts}, {"SGD", "NOK", "EUR", "USD", "DKK"})
        self.assertEqual(len(self.parsed.rows), 133)
        self.assertEqual(self.parsed.bad, [])
        self.assertTrue(all(b["ok"] for b in self.parsed.blocks))
        # one block per month from each section's first row to the export month
        want = sum(len(rv.every_month(first, "2026-09")) for first in
                   ("2024-06", "2024-08", "2024-08", "2025-06", "2026-08"))   # SGD NOK EUR DKK USD
        self.assertEqual(len(self.parsed.blocks), want)
        self.assertEqual(sum(1 for b in self.parsed.blocks if b["n_txns"]), 28)

    def test_section_totals_match_the_balance_summaries(self):
        for s in self.parsed.stmts:
            self.assertEqual(rv.check_summary(s, self.parsed.rows), [], s["currency"])

    def test_closing_balances(self):
        closing = {s["currency"]: s["closing"] for s in self.parsed.stmts}
        self.assertEqual(closing, {"SGD": 29.86, "NOK": 0.0, "EUR": 0.0, "USD": 0.0, "DKK": 0.0})

    def test_flow_split(self):
        from collections import Counter
        c = Counter(r["flow_class"] for r in self.parsed.rows)
        self.assertEqual(c["Internal - Revolut"], 47)
        self.assertEqual(c["External"], 86)

    def test_every_card_row_has_a_country(self):
        cards = [r for r in self.parsed.rows if r["bank_txn_type"] == "Revolut Payment"]
        self.assertEqual(len(cards), 49)
        self.assertTrue(all(r["merchant_country"] for r in cards))
        self.assertEqual(sorted({r["merchant_country"] for r in cards}),
                         ["IDN", "MYS", "NLD", "NOR", "SGP", "USA", "VNM"])

    def test_eur_blocks_borrow_a_rate_through_nok(self):
        eur = sorted((b for b in self.parsed.blocks if b["currency"] == "EUR"), key=lambda b: b["month"])
        self.assertIn("exchange partner", eur[0]["note"])
        self.assertAlmostEqual(eur[0]["fx_to_sgd"], 1.48, places=1)

    def test_keys_are_unique(self):
        keys = [r["txn_key"] for r in self.parsed.rows]
        self.assertEqual(len(keys), len(set(keys)))



class NoSummaryPrinted(unittest.TestCase):
    """The balance summary is where Revolut prints a wallet's opening and
    closing. A section without it used to open at zero and then pass every
    month against balances derived from its own rows."""

    def stripped(self):
        return sup.drop_line("revolut-all.txt", ["Account (E-Money)", "S$29.86"])

    def test_run_refuses_rather_than_reconciling_against_itself(self):
        with self.assertRaises(rv.MissingSummary) as e:
            sup.run_revolut(["revolut-all.txt"], lines={"revolut-all.txt": self.stripped()})
        self.assertIn("revolut-all.txt#SGD", str(e.exception))
        self.assertIn("SGD balance chain", str(e.exception))

    def test_a_summary_with_amounts_missing_is_refused_too(self):
        text = sup.edit_line("revolut-all.txt", ["Account (E-Money)", "S$29.86"],
                             "S$29.86", "")
        with self.assertRaises(rv.MissingSummary):
            sup.run_revolut(["revolut-all.txt"], lines={"revolut-all.txt": text})

    def test_main_exits_non_zero_and_writes_nothing(self):
        from contextlib import redirect_stdout
        from io import StringIO
        from unittest import mock
        texts = {"all.pdf": self.stripped()}
        with tempfile.TemporaryDirectory() as out, \
                mock.patch.object(rv.shutil, "which", return_value="/bin/pdftotext"), \
                mock.patch.object(rv, "collect_pdfs", return_value=list(texts)), \
                mock.patch.object(rv, "pdf_to_text", side_effect=lambda p, write: texts[p]), \
                mock.patch.object(sys, "argv", ["parse_revolut.py", "--out", out]), \
                redirect_stdout(StringIO()):
            with self.assertRaises(SystemExit) as e:
                rv.main()
            self.assertNotIn(e.exception.code, (0, None))
            self.assertIn("no balance summary read", str(e.exception.code))
            self.assertEqual(list(Path(out).iterdir()), [])

class SharedPlaces(unittest.TestCase):
    """The city table is one table in one module, not something a parser adds to.

    It used to live in parse_youtrip and parse_revolut appended its own cities
    to it at import time, so a YouTrip run on its own saw a shorter table than a
    run that happened to import the Revolut parser first, and the same statement
    could resolve a city or not depending on import order.
    """

    def test_the_cities_the_revolut_rows_need_are_in_the_shared_table(self):
        for city, country in (("HA NOI", "VNM"), ("OSLO", "NOR"),
                              ("BERLIN", "DEU"), ("AMSTERDAM", "NLD")):
            with self.subTest(city):
                self.assertEqual(country, places.city_country(city))

    def test_every_parser_reads_the_same_table(self):
        self.assertIs(places.CITY_TABLE, yt.CITY_TABLE)
        self.assertIs(places.split_country, yt.split_country)

    def test_import_order_cannot_change_the_table(self):
        """Fresh interpreters, one per import order, so nothing this process has
        already imported can hide a parser that still mutates the table."""
        root = str(Path(__file__).resolve().parent.parent)
        orders = ("places", "parse_youtrip,places", "parse_revolut,places",
                  "parse_wise,parse_youtrip,places", "parse_revolut,parse_youtrip,places")
        seen = set()
        for order in orders:
            code = (f"from finance.parsers import {order.replace(',', ', ')}\n"
                    "print(len(places.CITY_TABLE))")
            out = subprocess.run([sys.executable, "-c", code], capture_output=True, cwd=root,
                                 text=True, check=True).stdout.strip()
            seen.add(out)
        self.assertEqual(1, len(seen), f"table size depends on import order: {seen}")


class PlacesFile(unittest.TestCase):
    """G10: the towns of one person's stays live in the data folder's
    places.csv, read after the shipped table, never in the repo."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(places.load, None)      # the shipped tables for every other test
        self.path = Path(self.tmp.name) / "places.csv"

    def write(self, *lines):
        self.path.write_text("\n".join(("place,country,match",) + lines) + "\n")
        return self.path

    def test_its_towns_come_after_the_shipped_table(self):
        places.load(self.write("FJELLBY,NOR,prefix", "FJB,NOR,exact"))
        shipped = list(places.SHIPPED_CITY_TABLE)
        self.assertEqual(places.CITY_TABLE[:len(shipped)], shipped)
        self.assertEqual(places.CITY_TABLE[len(shipped):], [("FJELLBY", "NOR")])
        self.assertEqual(places.city_country("Fjellb"), "NOR")
        self.assertEqual(places.split_country("Cafe Fjell, FJB", None, "EUR"), ("Cafe Fjell", "NOR"))

    def test_a_recognised_city_beats_the_charged_currency(self):
        from finance.parsers import parse_revolut as rv
        r = {"currency": "GBP", "title": "Shop", "withdrawal": 9.5, "deposit": None,
             "detail": ["To: Shop, London", "Card: 123456******4242", "$12.00"]}
        rv.enrich(r, None)
        self.assertEqual((r["merchant"], r["merchant_country"]), ("Shop", "GBR"))

    def test_a_country_must_be_a_real_iso_code(self):
        with self.assertRaises(SystemExit) as e:
            places.read_places(self.write("FJELLBY,NRO"))
        self.assertIn("ISO alpha-3", str(e.exception))

    def test_importing_a_parser_never_reads_the_personal_file(self):
        import os
        import subprocess
        import sys
        (Path(self.tmp.name) / "places.csv").write_text("not,a,header\n")
        root = str(Path(places.__file__).resolve().parent.parent.parent)
        run = subprocess.run([sys.executable, "-c",
                              "from finance.parsers import parse_revolut, parse_youtrip, parse_wise"],
                             cwd=root, capture_output=True, text=True,
                             env={**os.environ, "KOPIFUND_DATA": self.tmp.name})
        self.assertEqual(run.returncode, 0, run.stderr)

    def test_the_match_column_may_be_left_out(self):
        self.path.write_text("place,country\nFJELLBY,NOR\n")
        places.load(self.path)
        self.assertEqual(places.city_country("FJELLBY"), "NOR")

    def test_a_shipped_city_still_wins_a_prefix_both_could_take(self):
        places.load(self.write("OSLANDET,DNK"))
        self.assertEqual(places.city_country("OSL"), "NOR")
        self.assertEqual(places.city_country("OSLAND"), "DNK")

    def test_no_file_leaves_the_shipped_tables(self):
        places.load(self.path)
        self.assertEqual(places.CITY_TABLE, list(places.SHIPPED_CITY_TABLE))
        self.assertEqual(places.CITY_COUNTRY, places.SHIPPED_CITY_COUNTRY)

    def test_a_bad_line_stops_the_parser(self):
        for line, says in (("FJELLBY,Norway", "line 2: country"),
                           ("Fjellby 2,NOR", "line 2: place"),
                           ("FJELLBY,NOR,fuzzy", "line 2: match")):
            with self.subTest(line), self.assertRaises(SystemExit) as caught:
                places.load(self.write(line))
            self.assertIn(says, str(caught.exception))

    def test_the_data_folder_file_is_read_on_import(self):
        self.write("FJELLBY,NOR")
        root = str(Path(__file__).resolve().parent.parent)
        code = "from finance.parsers import places; print(places.city_country('FJELLBY'))"
        env = dict(os.environ, KOPIFUND_DATA=self.tmp.name)
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                             check=True, env=env, cwd=root).stdout.strip()
        self.assertEqual(out, "NOR")


if __name__ == "__main__":
    unittest.main()
