"""F10: "no closing balance printed" is an explicit state, never a silent pass.

A card statement and a YouTrip "No transactions" month print no closing by
design, and say so in their period note. Any other block whose closing is
missing fails the balance check, is warned about on stderr and is counted in the
final summary line.
"""
import isolate  # noqa: F401  first: no test writes the real record or cache
import io
import unittest
from contextlib import redirect_stderr

import parse_support as sup
from parse_support import ps


class RecognisedBalanceLessLayouts(unittest.TestCase):
    """The two layouts that legitimately print no closing balance."""

    def test_card_statement(self):
        block = sup.run_statements(["card-only-month.txt"]).block()
        self.assertIsNone(block["closing"])
        self.assertEqual("card", block["balance_state"])
        self.assertTrue(block["ok"])
        self.assertIn("no balances", block["note"])

    def test_youtrip_no_transactions_month(self):
        block = sup.run_youtrip(["youtrip-empty.txt"]).block()
        self.assertIsNone(block["closing"])
        self.assertIsNone(block["opening"])
        self.assertTrue(block["empty_statement"])
        self.assertEqual("empty", block["balance_state"])
        self.assertTrue(block["ok"])
        self.assertEqual("no transactions; no closing balance printed by this layout",
                         ps.BALANCE_STATES["empty"])

    def test_a_month_with_rows_is_not_treated_as_empty(self):
        block = sup.run_youtrip(["youtrip-sgd.txt"]).block()
        self.assertFalse(block["empty_statement"])
        self.assertEqual("checked", block["balance_state"])


class MissingClosing(unittest.TestCase):
    """A layout that stops printing its closing must fail, not pass quietly."""

    def parse_without_closing(self):
        stripped = sup.drop_line("posb-deposit.txt", ["Balance Carried Forward"])
        return sup.run_statements(["posb-deposit.txt"],
                                  lines={"posb-deposit.txt": stripped})

    def test_state_is_missing_and_the_block_fails(self):
        block = self.parse_without_closing().block()
        self.assertIsNone(block["closing"])
        self.assertEqual("missing", block["balance_state"])
        self.assertFalse(block["ok"])

    def test_the_period_note_says_so(self):
        block = self.parse_without_closing().block()
        self.assertEqual(ps.BALANCE_STATES["missing"], block["note"])
        self.assertIn("no closing balance printed", block["note"])

    def test_the_rows_are_still_parsed_and_the_chain_still_replayed(self):
        parsed = self.parse_without_closing()
        self.assertEqual(10, len(parsed.rows))
        self.assertEqual([], parsed.bad)
        self.assertEqual(1372.84, parsed.block()["computed_closing"])

    def test_the_summary_line_counts_it(self):
        parsed = self.parse_without_closing()
        self.assertIn("1 unchecked", ps.balance_summary(parsed.blocks_out))

    def test_a_recognised_layout_is_counted_separately(self):
        parsed = sup.run_statements(["card-only-month.txt"])
        summary = ps.balance_summary(parsed.blocks_out)
        self.assertIn("1 block(s) print no closing balance by layout", summary)
        self.assertIn("0 unchecked", summary)


class Warnings(unittest.TestCase):
    """Every advisory carries the prefix and the file, and lands on stderr."""

    def test_warn_writes_a_prefixed_line_to_stderr(self):
        ps.WARNINGS.clear()
        buf = io.StringIO()
        with redirect_stderr(buf):
            ps.warn("data/dbs-statements/posb-everyday/2026/6.pdf", "something to say")
        self.assertEqual("warning: data/dbs-statements/posb-everyday/2026/6.pdf: "
                         "something to say\n", buf.getvalue())
        self.assertEqual(1, len(ps.WARNINGS))

    def test_the_summary_line_states_the_warning_count(self):
        ps.WARNINGS.clear()
        buf = io.StringIO()
        with redirect_stderr(buf):
            ps.warn("a.pdf", "one")
            ps.warn("b.pdf", "two")
        self.assertIn("2 warning(s)", ps.balance_summary([]))

    def test_a_statement_with_no_rows_is_warned_about(self):
        ps.WARNINGS.clear()
        buf = io.StringIO()
        with redirect_stderr(buf):
            ps.parse_all([("empty.txt", "nothing parseable here\n")])
        self.assertEqual(["warning: empty.txt: no transactions parsed"], ps.WARNINGS)
        self.assertIn("warning: empty.txt", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
