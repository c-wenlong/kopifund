"""Debit-card statements repeat rows the deposit-account statement already has.

A card row dated inside a month an account statement covers is dropped, the card
block's totals are reduced by what left, and a card statement with nothing left
is skipped entirely. A month no account statement covers keeps all its rows,
which is the only reason the card statements are parsed at all.
"""
import isolate  # noqa: F401  first: no test writes the real record or cache
import unittest

import parse_support as sup
from parse_support import ps, yt

CARD = "card.txt"                       # Nov 2024 card statement, 3 rows
COVERED = "card-covered.txt"            # Jan 2025 card statement, 5 rows
ACCOUNT = "posb-deposit-dec-jan.txt"    # account statement covering Dec 24 to Feb 25


class NoAccountStatement(unittest.TestCase):
    """Card-only months are what the card statements are for."""

    def test_every_row_is_kept(self):
        parsed = sup.run_statements([CARD])
        self.assertEqual(3, len(parsed.rows))
        self.assertEqual({}, dict(parsed.dropped))
        block = parsed.block()
        self.assertEqual(3, block["n_txns"])
        self.assertEqual(174.32, block["total_out"])
        self.assertEqual(0.0, block["total_in"])
        self.assertFalse(block["skipped"])
        self.assertIn(block, parsed.blocks_out)

    def test_an_unrelated_account_statement_covers_nothing(self):
        """posb-deposit.txt is a 2026 month, so the 2024 card rows all stay."""
        parsed = sup.run_statements(["card-only-month.txt", "posb-deposit.txt"])
        self.assertEqual({}, dict(parsed.dropped))
        self.assertEqual(10, len(parsed.rows_from("card-only-month.txt")))
        self.assertEqual(2, len(parsed.blocks_out))


class PartiallyCoveredMonth(unittest.TestCase):
    """The account statement covers Dec 2024, so only the Dec card row goes."""

    @classmethod
    def setUpClass(cls):
        cls.parsed = sup.run_statements([CARD, ACCOUNT])

    def test_only_the_covered_row_is_dropped(self):
        self.assertEqual({CARD: 1}, dict(self.parsed.dropped))
        kept = self.parsed.rows_from(CARD)
        self.assertEqual(["2024-11-12", "2024-11-13"], [r["date"] for r in kept])

    def test_the_dropped_row_is_the_one_the_account_statement_carries(self):
        account_rows = self.parsed.rows_from(ACCOUNT)
        self.assertIn(("2024-12-01", -18.0),
                      [(r["date"], r["amount"]) for r in account_rows])

    def test_the_block_totals_are_adjusted_to_the_rows_kept(self):
        block = self.parsed.block(source=CARD)
        self.assertEqual(2, block["n_txns"])
        self.assertEqual(156.32, block["total_out"])       # 174.32 less the 18.00
        self.assertEqual(0.0, block["total_in"])

    def test_the_grand_total_check_still_describes_the_whole_statement(self):
        block = self.parsed.block(source=CARD)
        self.assertEqual(174.32, block["grand_total"])
        self.assertTrue(block["ok"])
        self.assertIn("1 rows dropped as duplicates of the account statement",
                      block["note"])

    def test_the_block_is_still_written(self):
        self.assertFalse(self.parsed.block(source=CARD)["skipped"])
        self.assertEqual(2, len(self.parsed.blocks_out))


class FullyCoveredMonth(unittest.TestCase):
    """Nothing left to load, so the card statement's period is not written."""

    @classmethod
    def setUpClass(cls):
        cls.parsed = sup.run_statements([COVERED, ACCOUNT])

    def test_every_card_row_is_dropped(self):
        self.assertEqual({COVERED: 5}, dict(self.parsed.dropped))
        self.assertEqual([], self.parsed.rows_from(COVERED))

    def test_the_block_is_skipped(self):
        block = self.parsed.block(source=COVERED)
        self.assertEqual(0, block["n_txns"])
        self.assertTrue(block["skipped"])
        self.assertNotIn(block, self.parsed.blocks_out)
        self.assertEqual([ACCOUNT], [b["source"] for b in self.parsed.blocks_out])

    def test_the_account_statement_is_untouched(self):
        block = self.parsed.block(source=ACCOUNT)
        self.assertEqual(30, block["n_txns"])
        self.assertTrue(block["ok"])


class CoverageRule(unittest.TestCase):
    """The covered months come from the account rows and the account blocks."""

    def test_a_month_with_no_account_rows_is_still_covered_by_its_block(self):
        rows = [dict(source="card.pdf", statement_format="card",
                     account_no="130-24680-5", date="2026-06-04", amount=-5.0)]
        blocks = [dict(source="card.pdf", card_statement=True, account_no="130-24680-5",
                       total_in=0.0, total_out=5.0),
                  dict(source="acct.pdf", account_no="130-24680-5",
                       statement_date="30 Jun 2026")]
        kept, dropped = ps.drop_card_duplicates(rows, blocks)
        self.assertEqual([], kept)
        self.assertEqual({"card.pdf": 1}, dict(dropped))
        self.assertEqual(0.0, blocks[0]["total_out"])

    def test_a_different_account_does_not_cover_the_card(self):
        rows = [dict(source="card.pdf", statement_format="card",
                     account_no="130-24680-5", date="2026-06-04", amount=-5.0)]
        blocks = [dict(source="card.pdf", card_statement=True, account_no="130-24680-5",
                       total_in=0.0, total_out=5.0),
                  dict(source="acct.pdf", account_no="120-345678-9",
                       statement_date="30 Jun 2026")]
        kept, dropped = ps.drop_card_duplicates(rows, blocks)
        self.assertEqual(1, len(kept))
        self.assertEqual({}, dict(dropped))


class DuplicatePeriods(unittest.TestCase):
    """F3: the same month arriving twice would double-count it."""

    def test_two_files_with_the_same_period_are_reported(self):
        parsed = sup.run_statements([ACCOUNT])
        twin = dict(parsed.block(), source="a copy.pdf")
        dups = ps.duplicate_periods(parsed.blocks + [twin])
        self.assertEqual(1, len(dups))
        name, srcs = dups[0]
        self.assertEqual("130-24680-5 · SGD · 2025-02", name)
        self.assertEqual(["a copy.pdf", ACCOUNT], srcs)

    def test_a_clean_run_reports_nothing(self):
        parsed = sup.run_statements([CARD, COVERED, ACCOUNT, "posb-deposit.txt",
                                     "dbs-consolidated.txt", "paylah.txt"])
        self.assertEqual([], ps.duplicate_periods(parsed.blocks))

    def test_two_currencies_of_one_statement_are_not_duplicates(self):
        parsed = sup.run_statements(["dbs-consolidated.txt"])
        self.assertEqual(2, len(parsed.blocks))
        self.assertEqual([], ps.duplicate_periods(parsed.blocks))

    def test_a_card_statement_and_its_account_month_are_not_duplicates(self):
        parsed = sup.run_statements([CARD, ACCOUNT])
        self.assertEqual([], ps.duplicate_periods(parsed.blocks))

    def test_youtrip_uses_its_own_period_name(self):
        parsed = sup.run_youtrip(["youtrip-nok.txt"])
        twin = dict(parsed.block(), source="7 (1).pdf")
        dups = ps.duplicate_periods(parsed.blocks + [twin], yt.period_name)
        self.assertEqual([("Y-1029384756 · NOK · 2025-07",
                           ["7 (1).pdf", "youtrip-nok.txt"])], dups)

    def test_youtrip_wallets_of_one_month_are_not_duplicates(self):
        parsed = sup.run_youtrip(["youtrip-nok.txt", "youtrip-sgd-gap.txt"])
        self.assertEqual("2025-07", parsed.blocks[0]["period_end"][:7])
        self.assertEqual("2025-07", parsed.blocks[1]["period_end"][:7])
        self.assertEqual([], ps.duplicate_periods(parsed.blocks, yt.period_name))


if __name__ == "__main__":
    unittest.main()
