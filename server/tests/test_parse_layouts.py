"""Every statement layout parses to the rows and block the statement prints.

One fixture per layout. Each test pins the row count, every field of the first
and last row, and the block's opening, closing and totals, so a change to the
column arithmetic or the enrichment shows up here rather than in Notion.
"""
import isolate  # noqa: F401  first: no test writes the real record or cache
import re
import unittest

import parse_support as sup
from parse_support import ps


class LayoutCase(unittest.TestCase):

    def assertRow(self, row, **expected):
        """Every column of one row, so a new field cannot slip through unpinned."""
        actual = {c: row.get(c) for c in ps.TXN_COLS if c != "txn_key"}
        for c, want in list(expected.items()):
            if hasattr(want, "fullmatch"):          # a compiled pattern, for values that must stay out of the repo
                self.assertRegex(str(actual.get(c)), want)
                expected[c] = actual.get(c)
        self.assertEqual(expected, actual)

    def assertBlock(self, block, **expected):
        actual = {k: block.get(k) for k in expected}
        self.assertEqual(expected, actual)


class ConsolidatedLayout(LayoutCase):
    """The DBS Multiplier consolidated statement: one block per currency."""

    @classmethod
    def setUpClass(cls):
        cls.parsed = sup.run_statements(["dbs-consolidated.txt"])

    def test_one_block_per_currency(self):
        self.assertEqual(2, len(self.parsed.blocks))
        self.assertEqual(["SGD", "USD"], sorted(b["currency"] for b in self.parsed.blocks))
        self.assertEqual(16, len(self.parsed.rows))

    def test_sgd_block(self):
        self.assertBlock(self.parsed.block("SGD"),
                         account="DBS Multiplier Account", account_no="120-345678-9",
                         statement_date="31 Jul 2026", opening=2594.69, closing=5503.0,
                         total_in=4209.94, total_out=1301.63, n_txns=11,
                         balance_state="checked", ok=True)
        self.assertEqual("120-345678-9 · SGD · 2026-07", ps.period_name(self.parsed.block("SGD")))
        self.assertEqual("2026-07-31", ps.period_end(self.parsed.block("SGD")))

    def test_usd_block(self):
        self.assertBlock(self.parsed.block("USD"),
                         currency="USD", opening=8.98, closing=0.0,
                         total_in=1415.0, total_out=1423.98, n_txns=5,
                         balance_state="checked", ok=True)

    def test_first_row(self):
        self.assertRow(self.parsed.rows[0],
                       date="2026-07-01", account="DBS Multiplier Account",
                       account_no="120-345678-9", currency="SGD",
                       bank_txn_type="Debit Card Transaction", direction="Out",
                       amount=-18.0, flow_class="External",
                       merchant="AID-SINGAPORE SINGAPORE", merchant_country="SGP",
                       purchase_date="2026-06-29", card_last4="5454", fx_original=None,
                       counterparty=None, reference=None, memo=None,
                       balance_after=2576.69, source="dbs-consolidated.txt")

    def test_last_row(self):
        self.assertRow(self.parsed.rows[-1],
                       date="2026-07-31", account="DBS Multiplier Account",
                       account_no="120-345678-9", currency="SGD",
                       bank_txn_type="Interest Earned", direction="In", amount=0.01,
                       flow_class="External", merchant=None, merchant_country=None,
                       purchase_date=None, card_last4=None, fx_original=None,
                       counterparty=None, reference=None, memo=None,
                       balance_after=5503.0, source="dbs-consolidated.txt")


class PosbDepositLayout(LayoutCase):
    """The POSB deposit-account statement: 'dd Mon' dates, one SGD block."""

    @classmethod
    def setUpClass(cls):
        cls.parsed = sup.run_statements(["posb-deposit.txt"])

    def test_block(self):
        self.assertEqual(10, len(self.parsed.rows))
        self.assertBlock(self.parsed.block(),
                         account="POSB Everyday Savings Account",
                         account_no="130-24680-5", currency="SGD",
                         statement_date="30 Jun 2026", opening=710.87, closing=1372.84,
                         total_in=3790.31, total_out=3128.34, n_txns=10,
                         balance_state="checked", ok=True)

    def test_first_row(self):
        self.assertRow(self.parsed.rows[0],
                       date="2026-05-31", account="POSB Everyday Savings Account",
                       account_no="130-24680-5", currency="SGD",
                       bank_txn_type="Funds Transfer", direction="In", amount=1000.0,
                       flow_class="External", merchant=None, merchant_country=None,
                       purchase_date=None, card_last4=None, fx_original=None,
                       counterparty=None,
                       reference=re.compile(r"FT\d{6}MB\d{8} \d{3}-\d{5}-\d:IB"),
                       memo="School fees", balance_after=1710.87,
                       source="posb-deposit.txt")

    def test_last_row(self):
        self.assertRow(self.parsed.rows[-1],
                       date="2026-06-30", account="POSB Everyday Savings Account",
                       account_no="130-24680-5", currency="SGD",
                       bank_txn_type="Interest Earned", direction="In", amount=0.05,
                       flow_class="External", merchant=None, merchant_country=None,
                       purchase_date=None, card_last4=None, fx_original=None,
                       counterparty=None, reference=None, memo=None,
                       balance_after=1372.84, source="posb-deposit.txt")


class PayLahLayout(LayoutCase):
    """The PayLah! wallet statement: DB/CR signs instead of columns."""

    @classmethod
    def setUpClass(cls):
        cls.parsed = sup.run_statements(["paylah.txt"])

    def test_block(self):
        self.assertEqual(6, len(self.parsed.rows))
        self.assertBlock(self.parsed.block(),
                         account="DBS PayLah! wallet",
                         account_no="8888880001234567", currency="SGD",
                         statement_date="29 Jun 2026", opening=0.0, closing=0.0,
                         total_in=100.34, total_out=100.34, n_txns=6,
                         balance_state="checked", ok=True)

    def test_first_row(self):
        self.assertRow(self.parsed.rows[0],
                       date="2026-05-30", account="DBS PayLah! wallet",
                       account_no="8888880001234567", currency="SGD",
                       bank_txn_type="PayLah Top-up", direction="In", amount=40.0,
                       flow_class="Internal - PayLah", merchant=None,
                       merchant_country=None, purchase_date=None, card_last4=None,
                       fx_original=None, counterparty=None,
                       reference="TF616807652702166316", memo=None,
                       balance_after=None, source="paylah.txt")

    def test_last_row(self):
        self.assertRow(self.parsed.rows[-1],
                       date="2026-06-20", account="DBS PayLah! wallet",
                       account_no="8888880001234567", currency="SGD",
                       bank_txn_type="PayNow", direction="Out", amount=-12.64,
                       flow_class="External", merchant=None, merchant_country=None,
                       purchase_date=None, card_last4=None, fx_original=None,
                       counterparty="MX LX KAXX MEX",
                       reference="IP504794944894663217", memo="90000003",
                       balance_after=None, source="paylah.txt")


class CardLayout(LayoutCase):
    """The debit-card statement: no balances, one printed GRAND TOTAL, and the
    account's display name taken from the 'for ACCOUNT:' line."""

    @classmethod
    def setUpClass(cls):
        cls.parsed = sup.run_statements(["card-only-month.txt"])

    def test_block(self):
        self.assertEqual(10, len(self.parsed.rows))
        b = self.parsed.block()
        self.assertBlock(b, account="POSB Everyday Savings Account",
                         account_no="130-24680-5", currency="SGD",
                         statement_date="09 Nov 2024", opening=None, closing=None,
                         grand_total=181.6, total_in=13.43, total_out=195.03,
                         n_txns=10, balance_state="card", ok=True)
        # a card statement closes on the 9th, so it is labelled by the month it
        # mostly covers, which is the one before its as-at date
        self.assertEqual("130-24680-5 · SGD · 2024-10 · card", ps.period_name(b))

    def test_first_row(self):
        self.assertRow(self.parsed.rows[0],
                       date="2024-10-10", account="POSB Everyday Savings Account",
                       account_no="130-24680-5", currency="SGD",
                       bank_txn_type="Debit Card Transaction", direction="Out",
                       amount=-46.33, flow_class="External",
                       merchant="TEMU.COM INTERNET", merchant_country="IRL",
                       purchase_date="2024-10-08", card_last4="4242",
                       fx_original="NOK 354.77", counterparty=None,
                       reference="VT441726871305117380569", memo=None,
                       balance_after=None, source="card-only-month.txt")

    def test_last_row(self):
        self.assertRow(self.parsed.rows[-1],
                       date="2024-11-03", account="POSB Everyday Savings Account",
                       account_no="130-24680-5", currency="SGD",
                       bank_txn_type="Debit Card Transaction", direction="Out",
                       amount=-0.01, flow_class="External",
                       merchant="GOOGLE*CLOUD HF1BFF CCGOOGLE.COM",
                       merchant_country="SGP", purchase_date="2024-11-01",
                       card_last4="5454", fx_original=None, counterparty=None,
                       reference="MT157670453493818392898", memo=None,
                       balance_after=None, source="card-only-month.txt")

    def test_account_name_comes_from_the_account_statement(self):
        """F9: the name is whatever the account statements call the number, and
        never a name hardcoded into parse_card."""
        rows, blocks = ps.parse_all([("card-only-month.txt", sup.text("card-only-month.txt"))])
        blocks[0]["account"] = None                    # nothing learnt yet
        renamed = dict(source="acct.txt", account_no="130-24680-5",
                       account="A Different Account Name", card_statement=False)
        ps.resolve_card_accounts(rows, blocks + [renamed])
        self.assertEqual("A Different Account Name", blocks[0]["account"])
        self.assertEqual({"A Different Account Name"}, {r["account"] for r in rows})

    def test_unnamed_account_is_reported(self):
        rows, blocks = ps.parse_all([("card-only-month.txt", sup.text("card-only-month.txt"))])
        for x in rows + blocks:
            x["account_no"] = "999-99999-9"
            x["account"] = None
        self.assertEqual([("card-only-month.txt", "999-99999-9")],
                         ps.resolve_card_accounts(rows, blocks))


class YouTripLayouts(LayoutCase):
    """The YouTrip wallet statement, in the home currency and a foreign one."""

    def test_sgd_wallet(self):
        parsed = sup.run_youtrip(["youtrip-sgd.txt"])
        self.assertEqual(5, len(parsed.rows))
        self.assertBlock(parsed.block(), account="YouTrip wallet",
                         account_no="Y-1029384756", currency="SGD",
                         period_start="2025-10-01", period_end="2025-10-31",
                         opening=68.41, closing=6.91, total_in=0.0, total_out=61.5,
                         n_txns=5, balance_state="checked", ok=True)
        self.assertRow(parsed.rows[0],
                       date="2025-10-01", account="YouTrip wallet",
                       account_no="Y-1029384756", currency="SGD",
                       bank_txn_type="YouTrip Payment", direction="Out", amount=-13.9,
                       flow_class="External", merchant="Grab* A-0MK4POJNM175FO,6 B",
                       merchant_country="SGP", purchase_date=None, card_last4=None,
                       fx_original=None, counterparty=None, reference=None, memo=None,
                       balance_after=54.51, source="youtrip-sgd.txt")
        self.assertRow(parsed.rows[-1],
                       date="2025-10-03", account="YouTrip wallet",
                       account_no="Y-1029384756", currency="SGD",
                       bank_txn_type="YouTrip Payment", direction="Out", amount=-44.1,
                       flow_class="External", merchant="WWW.RAMENY* RDKTRAMENY,9 R",
                       merchant_country="SGP", purchase_date=None, card_last4=None,
                       fx_original=None, counterparty=None, reference=None, memo=None,
                       balance_after=6.91, source="youtrip-sgd.txt")

    def test_nok_wallet(self):
        """The kr symbol, and an exchange whose other leg lands in fx_original."""
        parsed = sup.run_youtrip(["youtrip-nok.txt"])
        self.assertEqual(2, len(parsed.rows))
        self.assertBlock(parsed.block(), currency="NOK", period_start="2025-07-01",
                         period_end="2025-07-31", opening=88.0, closing=0.0,
                         total_in=0.0, total_out=88.0, n_txns=2,
                         balance_state="checked", ok=True)
        self.assertRow(parsed.rows[0],
                       date="2025-07-18", account="YouTrip wallet",
                       account_no="Y-1029384756", currency="NOK",
                       bank_txn_type="YouTrip Exchange Out", direction="Out",
                       amount=-80.59, flow_class="Internal - YouTrip", merchant=None,
                       merchant_country=None, purchase_date=None, card_last4=None,
                       fx_original="SGD 10.70", counterparty=None,
                       reference="1 NOK = 0.13277 SGD", memo="SGD",
                       balance_after=7.41, source="youtrip-nok.txt")
        self.assertRow(parsed.rows[-1],
                       date="2025-07-19", account="YouTrip wallet",
                       account_no="Y-1029384756", currency="NOK",
                       bank_txn_type="YouTrip Exchange Out", direction="Out",
                       amount=-7.41, flow_class="Internal - YouTrip", merchant=None,
                       merchant_country=None, purchase_date=None, card_last4=None,
                       fx_original="SGD 0.98", counterparty=None,
                       reference="1 NOK = 0.132253 SGD", memo="SGD",
                       balance_after=0.0, source="youtrip-nok.txt")

    def test_empty_month(self):
        parsed = sup.run_youtrip(["youtrip-empty.txt"])
        self.assertEqual([], parsed.rows)
        self.assertBlock(parsed.block(), currency="EUR", period_start="2024-11-01",
                         period_end="2024-11-30", opening=None, closing=None,
                         n_txns=0, empty_statement=True, balance_state="empty",
                         ok=True)


class FlowClass(unittest.TestCase):
    """Which detail text makes a bank row a wallet load rather than spending.

    The test has to be tight in both directions. Too loose and a row that only
    mentions a wallet in free text leaves the External spending it belongs to;
    too tight and a real load is counted as money spent. Each case below is a
    string the statements actually print, or the free-text near-miss it must not
    be confused with.
    """

    def flow(self, *detail, fmt="account", own=()):
        row = dict(detail=list(detail), statement_format=fmt,
                   bank_txn_type="Debit Card Transaction")
        return ps.classify_flow(row, set(own))

    def test_revolut_card_top_up_is_internal(self):
        """Every acquirer id the statements print, all POSB card rows."""
        for acquirer in ("REVOLUT**9144* SINGAPORE SGP",
                         "REVOLUT**9191* SINGAPORE SG",
                         "REVOLUT**0000* SINGAPORE"):
            with self.subTest(acquirer):
                self.assertEqual("Internal - Revolut",
                                 self.flow(acquirer, "VT242344198550110589723"))

    def test_revolut_paynow_load_is_internal(self):
        self.assertEqual("Internal - Revolut",
                         self.flow("PAYNOW TRANSFER", "TO: REVOLUT TECHNOLOGIES SINGAPORE",
                                   "OTHER"))

    def test_a_free_text_mention_of_revolut_stays_external(self):
        """Somebody's note about the app is not money leaving for the wallet."""
        for memo in ("TO: JANE TAN", "paying you back for revolut", "Revolut lah",
                     "REVOLUT REFUND?", "SPLIT THE REVOLUT BILL"):
            with self.subTest(memo):
                self.assertEqual("External", self.flow("PAYNOW TRANSFER", memo))

    def test_youtrip_load_is_internal_and_a_mention_is_not(self):
        self.assertEqual("Internal - YouTrip",
                         self.flow("TO: YOU TECHNOLOGIES GROUP (SG) PL"))
        self.assertEqual("Internal - YouTrip", self.flow("M9496*Collection Payment"))
        self.assertEqual("External", self.flow("TO: JANE TAN", "youtrip money"))

    def test_own_account_and_paylah(self):
        self.assertEqual("Internal - PayLah", self.flow("TO: PAYLAH WALLET"))
        self.assertEqual("Internal - own account",
                         self.flow("TO: 130-24680-5", own=("130-24680-5",)))
        self.assertEqual("External", self.flow("TO: 130-24680-5"))

    def test_a_printed_revolut_load_still_classifies_off_the_fixture(self):
        """The tightened pattern read off a real statement rather than a string
        written here: card.txt prints one REVOLUT**0000* top-up."""
        parsed = sup.run_statements(["card.txt"])
        loads = [r for r in parsed.rows if r["flow_class"] == "Internal - Revolut"]
        self.assertEqual(1, len(loads))
        self.assertTrue(loads[0]["merchant"].startswith("REVOLUT**0000*"))


class Direction(unittest.TestCase):
    """F12: a zero-amount row moves money neither way, so it gets no direction."""

    def test_zero_is_neither_in_nor_out(self):
        self.assertEqual("", ps.direction(0))
        self.assertEqual("", ps.direction(0.0))
        self.assertEqual("", ps.direction(-0.0))

    def test_positive_is_in_and_negative_is_out(self):
        self.assertEqual("In", ps.direction(0.01))
        self.assertEqual("Out", ps.direction(-0.01))

    def test_no_fixture_row_carries_a_zero_amount(self):
        """Every row in the statements to date moves money, so the rule above
        changes nothing that is already loaded."""
        for name in ("dbs-consolidated.txt", "posb-deposit.txt",
                     "posb-deposit-dec-jan.txt", "paylah.txt", "card-only-month.txt"):
            with self.subTest(name):
                for r in sup.run_statements([name]).rows:
                    self.assertNotEqual(0, r["amount"])
                    self.assertIn(r["direction"], ("In", "Out"))


if __name__ == "__main__":
    unittest.main()
