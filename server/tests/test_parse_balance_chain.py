"""Replaying a block's balance chain is the only thing that makes the output
trustworthy: a misparse yields plausible rows. These tests bend one printed
number at a time and check that exactly one drift is reported, and that the
chain resyncs so one error does not become N.
"""
import isolate  # noqa: F401  first: no test writes the real record or cache
import unittest

import parse_support as sup
from parse_support import ps


class CleanChains(unittest.TestCase):

    def test_every_fixture_reconciles(self):
        for name in ("dbs-consolidated.txt", "posb-deposit.txt", "paylah.txt",
                     "card-only-month.txt"):
            with self.subTest(name):
                parsed = sup.run_statements([name])
                self.assertEqual([], parsed.bad)
                self.assertTrue(all(b["ok"] for b in parsed.blocks))

    def test_youtrip_fixtures_reconcile(self):
        for name in ("youtrip-sgd.txt", "youtrip-nok.txt", "youtrip-sgd-gap.txt",
                     "youtrip-empty.txt"):
            with self.subTest(name):
                parsed = sup.run_youtrip([name])
                self.assertEqual([], parsed.bad)
                self.assertTrue(all(b["ok"] for b in parsed.blocks))


class OneChangedAmount(unittest.TestCase):
    """A single wrong amount is one drift, named at the row it happens on."""

    def test_changed_deposit_reports_exactly_one_drift(self):
        bent = sup.edit_line("posb-deposit.txt", ["14 Jun", "Funds Transfer"],
                             "500.00", "600.00")
        parsed = sup.run_statements(["posb-deposit.txt"],
                                    lines={"posb-deposit.txt": bent})
        self.assertEqual(1, len(parsed.bad))
        source, currency, date, txn_type, computed, printed = parsed.bad[0]
        self.assertEqual("posb-deposit.txt", source)
        self.assertEqual("SGD", currency)
        self.assertEqual("2026-06-14", date)
        self.assertEqual("Funds Transfer", txn_type)
        self.assertEqual(1310.87, computed)          # 710.87 + the bent 600.00
        self.assertEqual(1210.87, printed)

    def test_the_chain_resyncs_after_a_drift_and_the_block_still_fails(self):
        """Every later row still matches and the block's closing still ties,
        but the drift fails the block: its CSV says not reconciled, so a
        reuse of that CSV (load --csv) stops on it."""
        bent = sup.edit_line("posb-deposit.txt", ["14 Jun", "Funds Transfer"],
                             "500.00", "600.00")
        parsed = sup.run_statements(["posb-deposit.txt"],
                                    lines={"posb-deposit.txt": bent})
        block = parsed.block()
        self.assertEqual(1, len(parsed.bad))
        self.assertEqual(1372.84, block["computed_closing"])
        self.assertEqual(block["closing"], block["computed_closing"])
        self.assertFalse(block["ok"])

    def test_a_changed_closing_fails_the_block_without_row_drift(self):
        bent = sup.edit_line("posb-deposit.txt", ["Balance Carried Forward"],
                             "1,372.84", "1,999.99")
        parsed = sup.run_statements(["posb-deposit.txt"],
                                    lines={"posb-deposit.txt": bent})
        block = parsed.block()
        self.assertEqual([], parsed.bad)             # every row still ties
        self.assertEqual(1999.99, block["closing"])
        self.assertEqual(1372.84, block["computed_closing"])
        self.assertEqual("checked", block["balance_state"])
        self.assertFalse(block["ok"])

    def test_a_changed_printed_balance_drifts_twice(self):
        """A wrong balance column, unlike a wrong amount, is reported twice: once
        where it is printed and once on the next row, because the replay resyncs
        onto the wrong value and the row after it disagrees again."""
        bent = sup.edit_line("posb-deposit.txt", ["17 Jun", "FAST Payment"],
                             "2,477.87", "2,777.87")
        parsed = sup.run_statements(["posb-deposit.txt"],
                                    lines={"posb-deposit.txt": bent})
        self.assertEqual(["2026-06-17", "2026-06-18"], [x[2] for x in parsed.bad])
        self.assertEqual((2477.87, 2777.87), parsed.bad[0][4:])
        self.assertEqual((2730.17, 2430.17), parsed.bad[1][4:])
        self.assertFalse(parsed.block()["ok"])


class ADriftStopsAReusedCsv(unittest.TestCase):
    """The parsers write their CSVs before they exit on a drift, and load,
    sync and doctor --csv (and analyse --from-csv) read them back through
    sync.check_blocks, which reads only the `reconciled` column. A block
    whose chain drifted mid-statement must say False there (audit)."""

    def check(self, blocks):
        from finance import sync
        p = sync.Parsed()
        p.blocks = [{**b, "reconciled": str(b["ok"]), "period": f"{b['source']} {b.get('month', '')}"}
                    for b in blocks]
        return [x.code for x in sync.check_blocks(p)]

    def test_dbs(self):
        bent = sup.edit_line("posb-deposit.txt", ["14 Jun", "Funds Transfer"], "500.00", "600.00")
        parsed = sup.run_statements(["posb-deposit.txt"], lines={"posb-deposit.txt": bent})
        self.assertIn("block-not-reconciled", self.check(parsed.blocks))

    def test_revolut(self):
        bent = sup.edit_line("revolut-all.txt", ["14 Jul 2025", "S$3.12"], "S$14.70", "S$15.70")
        parsed = sup.run_revolut(["revolut-all.txt"], lines={"revolut-all.txt": bent})
        self.assertEqual(["2025-07-14", "2025-07-14"], [x[2] for x in parsed.bad])
        [drifted] = [b for b in parsed.blocks if b["currency"] == "SGD" and b["month"] == "2025-07"]
        self.assertEqual(drifted["closing"], drifted["computed_closing"])
        self.assertFalse(drifted["ok"])
        self.assertIn("block-not-reconciled", self.check([drifted]))

    def test_wise(self):
        bent = sup.edit_line("wise-sgd.txt", ["Converted 140.00 NOK"], "48.67", "49.67")
        parsed = sup.run_wise(["wise-sgd.txt"], lines={"wise-sgd.txt": bent})
        self.assertEqual(2, len(parsed.bad))
        [drifted] = [b for b in parsed.blocks if b["month"] == "2025-07"]
        self.assertEqual(drifted["closing"], drifted["computed_closing"])
        self.assertFalse(drifted["ok"])
        self.assertTrue(all(b["ok"] for b in parsed.blocks if b is not drifted))
        self.assertIn("block-not-reconciled", self.check([drifted]))


class CardGrandTotal(unittest.TestCase):
    """A card statement has no balances, so the GRAND TOTAL is its whole check."""

    def test_matching_grand_total_passes(self):
        block = sup.run_statements(["card-only-month.txt"]).block()
        self.assertEqual(181.6, block["grand_total"])
        self.assertIn("matches the rows", block["note"])
        self.assertTrue(block["ok"])

    def test_wrong_grand_total_fails(self):
        bent = sup.edit_line("card-only-month.txt", ["GRAND TOTAL"], "181.60", "191.60")
        block = sup.run_statements(["card-only-month.txt"],
                                   lines={"card-only-month.txt": bent}).block()
        self.assertEqual("card", block["balance_state"])
        self.assertIn("DOES NOT MATCH", block["note"])
        self.assertFalse(block["ok"])


class ValidateDirectly(unittest.TestCase):

    def test_running_balance_and_totals(self):
        rows = [dict(source="s", account_no="1", currency="SGD", amount=-10.0,
                     balance_after=90.0, date="2026-01-01", bank_txn_type="t"),
                dict(source="s", account_no="1", currency="SGD", amount=25.0,
                     balance_after=115.0, date="2026-01-02", bank_txn_type="t")]
        block = dict(source="s", account_no="1", currency="SGD", opening=100.0,
                     closing=115.0)
        self.assertEqual([], ps.validate(rows, [block]))
        self.assertEqual(115.0, block["computed_closing"])
        self.assertEqual(25.0, block["computed_in"])
        self.assertEqual(10.0, block["computed_out"])
        self.assertEqual(2, block["n_txns"])
        self.assertTrue(block["ok"])


if __name__ == "__main__":
    unittest.main()
