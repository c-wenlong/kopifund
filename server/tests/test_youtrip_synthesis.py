"""The YouTrip PDF export drops rows at page breaks and between months.

Wherever the printed balance jumps past what the printed rows explain, one
"YouTrip Unprinted" row is synthesised for the jump. Inside a statement it is
dated on the next printed row; across a month boundary, where the opening does
not match the previous closing, it is dated the first of the month and the
opening is reset to the previous closing so the period still adds up.
"""
import isolate  # noqa: F401  first: no test writes the real record or cache
import unittest

import parse_support as sup
from parse_support import yt

GAP = "youtrip-sgd-gap.txt"             # Jul 2025 SGD, one hidden mid-page row
PREVIOUS = "youtrip-sgd.txt"            # Oct 2025 SGD, closes at 6.91
BOUNDARY = "youtrip-sgd-boundary.txt"   # May 2026 SGD, opens at a printed 271.12


def synthetic(parsed):
    return [r for r in parsed.rows if r["synthetic"]]


class NothingHidden(unittest.TestCase):

    def test_a_clean_statement_synthesises_nothing(self):
        for name in (PREVIOUS, "youtrip-nok.txt", "youtrip-empty.txt"):
            with self.subTest(name):
                parsed = sup.run_youtrip([name])
                self.assertEqual([], synthetic(parsed))
                self.assertEqual([], parsed.block()["gaps"])


class HiddenMidPageRow(unittest.TestCase):
    """One row the export never printed, found by the balance jump over it."""

    @classmethod
    def setUpClass(cls):
        cls.parsed = sup.run_youtrip([GAP])

    def test_exactly_one_row_is_synthesised(self):
        self.assertEqual(1, len(synthetic(self.parsed)))
        self.assertEqual(19, len(self.parsed.rows))
        self.assertEqual(1, len(self.parsed.block()["gaps"]))

    def test_it_is_the_size_of_the_jump(self):
        row = synthetic(self.parsed)[0]
        self.assertEqual(157.48, row["amount"])
        self.assertEqual("In", row["direction"])
        self.assertEqual("YouTrip Unprinted", row["bank_txn_type"])

    def test_it_is_dated_on_the_next_printed_row(self):
        row = synthetic(self.parsed)[0]
        self.assertEqual("2025-07-08", row["date"])
        self.assertIn("2025-07-08", [r["date"] for r in self.parsed.rows
                                     if not r["synthetic"]])

    def test_it_says_where_it_came_from(self):
        row = synthetic(self.parsed)[0]
        self.assertEqual("not printed by the YouTrip export; balance jump between "
                         "2025-07-06 and 2025-07-08", row["memo"])
        self.assertIsNone(row["merchant"])
        self.assertIsNone(row["counterparty"])

    def test_the_chain_reconciles_once_it_is_inserted(self):
        block = self.parsed.block()
        self.assertEqual([], self.parsed.bad)
        self.assertTrue(block["ok"])
        self.assertEqual(block["closing"], block["computed_closing"])


class MonthBoundaryMismatch(unittest.TestCase):
    """An opening that disagrees with the previous closing lost a movement
    between the two exports, so the row is dated the first of the month."""

    @classmethod
    def setUpClass(cls):
        cls.parsed = sup.run_youtrip([PREVIOUS, BOUNDARY])

    def test_one_row_dated_the_first_of_the_month(self):
        rows = synthetic(self.parsed)
        self.assertEqual(1, len(rows))
        self.assertEqual("2026-05-01", rows[0]["date"])
        self.assertEqual(BOUNDARY, rows[0]["source"])

    def test_it_is_the_size_of_the_opening_difference(self):
        row = synthetic(self.parsed)[0]
        self.assertEqual(264.21, row["amount"])          # 271.12 less the 6.91
        self.assertEqual("In", row["direction"])
        self.assertEqual("YouTrip Unprinted", row["bank_txn_type"])

    def test_the_opening_becomes_the_previous_closing(self):
        block = self.parsed.block(source=BOUNDARY)
        self.assertEqual(6.91, block["opening"])
        self.assertEqual(271.12, block["opening_printed"])
        self.assertEqual(6.91, self.parsed.block(source=PREVIOUS)["closing"])

    def test_the_note_explains_the_reset(self):
        block = self.parsed.block(source=BOUNDARY)
        self.assertEqual("printed opening 271.12 differs from the 2025-10 closing "
                         "6.91; opening set to the latter and +264.21 synthesised "
                         "on 2026-05-01", block["note_open"])

    def test_opening_plus_in_less_out_still_equals_the_printed_closing(self):
        block = self.parsed.block(source=BOUNDARY)
        self.assertAlmostEqual(block["opening"] + block["computed_in"]
                               - block["computed_out"], block["closing"], places=2)
        self.assertTrue(block["ok"])
        self.assertEqual([], self.parsed.bad)

    def test_the_statement_alone_shows_no_mismatch(self):
        """Without the previous month there is nothing to disagree with."""
        alone = sup.run_youtrip([BOUNDARY])
        self.assertEqual([], synthetic(alone))
        self.assertEqual(271.12, alone.block()["opening"])
        self.assertIsNone(alone.block().get("note_open"))


class SyntheticClassification(unittest.TestCase):
    """A jump that is a known top-up size, or that mirrors another wallet's
    printed exchange, is an internal move rather than real spending."""

    def test_a_known_top_up_size_is_internal(self):
        row = yt.synth_row(dict(source="s", account_no="Y-1", currency="SGD",
                                period_end="2025-06-30"),
                           999.0, "2025-06-09", "2025-06-08", "2025-06-09", 999.0)
        yt.classify_synthetic(row, "2025-06-08", "2025-06-09", {999.0}, [])
        self.assertEqual("Internal - YouTrip", row["flow_class"])
        self.assertEqual("known top-up size", row["reference"])

    def test_a_mirrored_exchange_is_internal(self):
        row = yt.synth_row(dict(source="s", account_no="Y-1", currency="NOK",
                                period_end="2025-06-30"),
                           4321.50, "2025-06-11", "2025-06-10", "2025-06-11", 4321.50)
        other = dict(currency="SGD", date="2025-06-11", direction="Out",
                     fx_original="NOK 4321.50")
        yt.classify_synthetic(row, "2025-06-10", "2025-06-11", set(), [other])
        self.assertEqual("Internal - YouTrip", row["flow_class"])
        self.assertEqual("mirrors SGD wallet exchange of 2025-06-11", row["reference"])

    def test_anything_else_stays_external(self):
        row = yt.synth_row(dict(source="s", account_no="Y-1", currency="SGD",
                                period_end="2025-07-31"),
                           157.48, "2025-07-08", "2025-07-06", "2025-07-08", 157.48)
        yt.classify_synthetic(row, "2025-07-06", "2025-07-08", {999.0}, [])
        self.assertEqual("External", row["flow_class"])
        self.assertIsNone(row["reference"])


class AColumnMisreadIsNotSynthesised(unittest.TestCase):
    """An amount read into the wrong column fails the balance check. Shifted
    right, the 3 Oct Money Out amount ends nearer the Money In edge and reads
    as a deposit; filling the gap with an "Unprinted" row of twice its size
    would balance the chain and hide the misread (audit, parse_youtrip)."""

    PRINTED = "$44.10                           $6.91"
    SHIFTED = "            $44.10               $6.91"

    @classmethod
    def setUpClass(cls):
        text = sup.text(PREVIOUS)
        assert cls.PRINTED in text
        cls.parsed = sup.run_youtrip([PREVIOUS], lines={PREVIOUS: text.replace(cls.PRINTED, cls.SHIFTED)})

    def test_the_misread_is_read_as_money_in(self):
        [row] = [r for r in self.parsed.rows if r["date"] == "2025-10-03" and abs(r["amount"]) == 44.10]
        self.assertEqual(44.10, row["amount"])

    def test_no_row_is_synthesised_for_it(self):
        self.assertEqual([], synthetic(self.parsed))
        self.assertEqual([], self.parsed.block()["gaps"])

    def test_the_balance_check_fails_on_that_row(self):
        self.assertEqual([("2025-10-03", 44.10)],
                         [(r["date"], r["amount"]) for r in self.parsed.block()["misreads"]])
        self.assertEqual(1, len(self.parsed.bad))
        self.assertEqual("2025-10-03", self.parsed.bad[0][2])

    def test_a_real_hidden_row_is_still_filled(self):
        self.assertEqual([], sup.run_youtrip([GAP]).block()["misreads"])


class SyntheticKeys(unittest.TestCase):
    """Synthetic rows are keyed like any other, so a re-import matches them."""

    def test_the_synthetic_row_has_a_key_of_its_own(self):
        parsed = sup.run_youtrip([GAP])
        self.assertEqual(len(parsed.rows), len(parsed.keys()))
        self.assertRegex(synthetic(parsed)[0]["txn_key"], r"^[0-9a-f]{12}$")

    def test_the_key_survives_a_second_run(self):
        first = sup.run_youtrip([GAP])
        second = sup.run_youtrip([GAP])
        self.assertEqual(first.keys(), second.keys())


if __name__ == "__main__":
    unittest.main()
