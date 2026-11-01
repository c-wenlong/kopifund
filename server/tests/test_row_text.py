"""server/finance/row_text.py: the plain description of a row with no payee on
Transactions (G13), and the bank line kept for the hover. Every name and
number here is invented."""
import isolate  # noqa: F401  first: no test writes the real record or cache
import unittest

from finance import row_text as rt


class MaskTest(unittest.TestCase):
    def test_numbers_in_a_line_are_masked_and_short_figures_kept(self):
        self.assertEqual(rt.mask_numbers("TO 123-456789-0:IB SGD@1.234567"), "TO •• 7890:IB SGD@1.234567")
        self.assertEqual(rt.mask_numbers("OCBC 360, 12.50 on 2026"), "OCBC 360, 12.50 on 2026")


def line(**kw):
    out = dict(merchant="", counterparty="", memo="", reference="", bank_txn_type="", flow_class="External",
               category="", currency="SGD", amount=-1000, account_no="111-11111-1", synthetic=0)
    out.update(kw)
    return out


class DescribeTest(unittest.TestCase):
    OWN = {"111-11111-1": "Everyday", "123-456789-0": "Main"}

    def says(self, **kw):
        return rt.describe(line(**kw), self.OWN)

    def test_a_merchant_speaks_for_itself(self):
        self.assertEqual(self.says(merchant="KOPITIAM 0123"), "")

    def test_wallet_top_ups_both_sides(self):
        self.assertEqual(self.says(flow_class="Internal - PayLah", bank_txn_type="Funds Transfer",
                                   memo="TOP-UP TO PAYLAH! : | 80000000"), "PayLah! top-up")
        self.assertEqual(self.says(flow_class="Internal - PayLah", bank_txn_type="PayLah Top-up", amount=5000),
                         "PayLah! top-up")
        self.assertEqual(self.says(flow_class="Internal - PayLah", bank_txn_type="Funds Transfer", amount=5000,
                                   memo="MAXED OUT FROM PAYLAH!"), "Transfer from PayLah!")
        self.assertEqual(self.says(flow_class="Internal - Revolut", bank_txn_type="Revolut Withdrawal",
                                   counterparty="A PERSON"), "Revolut withdrawal")

    def test_an_exchange_names_the_other_currency(self):
        self.assertEqual(self.says(flow_class="Internal - YouTrip", bank_txn_type="YouTrip Exchange Out", memo="SEK"),
                         "Exchange to SEK")
        self.assertEqual(self.says(flow_class="Internal - Wise", bank_txn_type="Wise Conversion In", amount=1867,
                                   memo="Converted 140.00 SEK to 18.67 SGD"), "Exchange from SEK")

    def test_a_transfer_between_own_accounts_names_the_other_one(self):
        self.assertEqual(self.says(flow_class="Internal - own account", bank_txn_type="Funds Transfer",
                                   memo="123-456789-0:IB"), "Transfer to Main")
        self.assertEqual(self.says(flow_class="Internal - own account", bank_txn_type="Funds Transfer", amount=1000,
                                   account_no="123-456789-0", memo="VALUE DATE : 01/04/2026"),
                         "Transfer between your accounts")

    def test_the_reference_can_name_an_own_account_but_a_transaction_reference_is_no_account(self):
        self.assertEqual(self.says(flow_class="Internal - own account", bank_txn_type="Funds Transfer",
                                   reference="123-456789-0:IB"), "Transfer to Main")
        self.assertEqual(self.says(bank_txn_type="FAST Payment / Receipt", reference="17700000000000001234"),
                         "FAST transfer out")

    def test_someone_elses_account_is_masked(self):
        self.assertEqual(self.says(bank_txn_type="Funds Transfer", memo="987-654321-0 | VALUE DATE : 01/04/2026"),
                         "Transfer to account •• 3210")

    def test_interest_cashback_and_a_bare_bank_code(self):
        self.assertEqual(self.says(bank_txn_type="Interest Earned", amount=12), "Interest")
        self.assertEqual(self.says(bank_txn_type="Cashback", amount=500, memo="CASHBACK - A CAMPAIGN"), "Cashback")
        self.assertEqual(self.says(bank_txn_type="Debit Card Transaction"), "Card payment")
        self.assertEqual(self.says(bank_txn_type="Funds Transfer", amount=1000), "Transfer in")
        self.assertEqual(self.says(bank_txn_type="Something New"), "Something New")

    def test_a_readable_memo_is_kept_in_sentence_case(self):
        self.assertEqual(self.says(bank_txn_type="FAST Payment / Receipt", amount=1000, memo="SALARY PAYMENT"),
                         "Salary payment")
        self.assertEqual(self.says(bank_txn_type="PayLah Receive Money", memo="FAST TRANSFER TO MY WALLET"),
                         "FAST transfer to my wallet")
        self.assertEqual(self.says(bank_txn_type="Funds Transfer", memo="wrong transfer"), "wrong transfer")

    def test_a_filler_row(self):
        self.assertEqual(self.says(synthetic=1, bank_txn_type="YouTrip Unprinted", memo="not printed"),
                         "Balance gap, not printed on the statement")

    def test_the_raw_line_has_every_text(self):
        self.assertEqual(rt.raw_text(line(counterparty="A", memo="B", reference="C")), "A · B · C")
        self.assertEqual(rt.raw_text(line(bank_txn_type="Interest Earned")), "Interest Earned")


if __name__ == "__main__":
    unittest.main()
