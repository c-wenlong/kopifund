"""server/finance/parsers/parse_tiger.py against three invented Tiger Brokers statements.

The fixtures under server/tests/fixtures/tiger/ are made up, but they are laid out
exactly the way pdftotext -layout renders the real exports, including the two
symbol layouts, the trades that print twice, a page break inside a table and a
page break inside a single row. The real PDFs live in data/, which is
gitignored, so nothing here reads them.

  legacy.txt   the 2022 to 2025 layout: the Symbol column prints the bare
               symbol, the older trades carry no Activity Type, and there is a
               withdrawal, two dividends and a page break inside two tables.
  wrapped.txt  the 2026 layout: the instrument name wraps with "(SYMBOL)"
               underneath, the date and the phase wrap in the Dividends table,
               a dividend carries a "Dividend tax:" charge, every Forex and
               Stock trade is printed twice, and one row is torn in half by a
               page break.
  sparse.txt   a quiet period: one deposit, no withdrawal, no trade and no
               Dividends section at all.

Two more fixtures come at the same two layouts from a second angle, so a change
to the column arithmetic has to survive two independent geometries rather than
one. They also pin every field of every table, the shape of the dict the
analytics side reads, and the folder-level entry points.

  old-layout.txt    the bare-symbol layout again, with no withdrawal and no
                    Dividends section, a fee cell wrapped over two lines and a
                    page break cutting both the Stock trades and the Deposits.
  wrapped-2026.txt  the wrapped layout again, with a withdrawal, an accrual
                    increase and reduction to skip, a Holdings row torn across
                    a page break and an exchange-rate table over two pages.
"""
import isolate  # noqa: F401  first: no test writes the real record or cache
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from finance.parsers import parse_tiger as pt

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "tiger"


def load(name):
    return pt.parse_text((FIXTURES / f"{name}.txt").read_text(), f"2024/{name}.pdf")


def raw(name):
    return (FIXTURES / f"{name}.txt").read_text()


def inject(name, contains, lines):
    """The fixture's text with `lines` pushed in above the one line holding
    every fragment in `contains`, so a test can move a page break somewhere new."""
    out, hits = [], 0
    for ln in raw(name).splitlines():
        if all(c in ln for c in contains):
            hits += 1
            out += lines
        out.append(ln)
    assert hits == 1, f"{contains} matched {hits} lines in {name}, expected 1"
    return "\n".join(out) + "\n"


# a page break: a blank line, the footer, the page header, and a blank line
BREAK = ["", "This statement also serves as a Tax Invoice for GST purpose."
             "                                        3/4", "",
         "                    Tiger Brokers (Singapore) PTE.LTD.", ""]


class Legacy(unittest.TestCase):
    """The layout every export from 2022 to 2025 uses."""

    @classmethod
    def setUpClass(cls):
        cls.s = load("legacy")

    def test_header(self):
        self.assertEqual(self.s["file"], "2024/legacy.pdf")
        self.assertEqual(self.s["account"], "99887766")
        self.assertEqual(self.s["base_currency"], "USD")
        self.assertEqual(self.s["period"], {"from": "2024-01-01", "to": "2024-12-31"})

    def test_overview(self):
        self.assertEqual(self.s["overview"]["begin"],
                         {"cash": 10.0, "stock": 100.0, "dividend_accruals": 1.0,
                          "total": 111.0})
        self.assertEqual(self.s["overview"]["end"],
                         {"cash": 20.5, "stock": 300.0, "dividend_accruals": 2.0,
                          "total": 322.5})

    def test_cash_report_is_the_base_summary_only(self):
        cash = self.s["cash_report"]
        self.assertEqual(cash["Starting Cash"], 10.0)
        self.assertEqual(cash["Ending Cash"], 20.5)
        self.assertEqual(cash["FOREX Gain&Loss"], 0.05)
        self.assertEqual(cash["Net Trades (Purchase)"], -600.0)
        # the per-currency blocks below repeat these labels; the base row wins
        self.assertEqual(cash["Deposits"], 300.25)
        self.assertEqual(cash["Dividends"], 12.34)
        self.assertNotIn("Total", cash)
        self.assertNotIn("Securities", cash)

    def test_derived_base_figures(self):
        self.assertEqual(self.s["dividends_base"], 12.34)
        self.assertEqual(self.s["withholding_tax_base"], -1.85)
        # Commissions, Platform Fees, GST, Other, Settlement Fees, Clearing Fees
        self.assertEqual(self.s["fees_base"], -4.15)

    def test_deposit_and_withdrawal(self):
        self.assertEqual(self.s["deposits"],
                         [{"date": "2024-02-01", "amount": 500.0, "currency": "SGD"}])
        self.assertEqual(self.s["withdrawals"],
                         [{"date": "2024-09-01", "amount": 100.0, "currency": "SGD"}])
        self.assertEqual(self.s["deposits_base"], 300.25)

    def test_a_page_break_inside_the_deposits_table_is_invisible(self):
        # the fixture prints the footer, the page header and the table header
        # again between the two rows; both rows still come through
        text = (FIXTURES / "legacy.txt").read_text()
        self.assertIn("This statement also serves as a Tax Invoice", text)
        self.assertEqual(len(self.s["deposits"]) + len(self.s["withdrawals"]), 2)

    def test_holdings_print_the_bare_symbol(self):
        self.assertEqual(self.s["holdings"], [
            {"symbol": "C6L.SI", "name": "", "quantity": 100.0, "cost_price": 5.0,
             "close_price": 6.0, "value": 600.0, "unrealized_pl": 100.0,
             "currency": "SGD"},
            {"symbol": "SPY", "name": "", "quantity": 2.0, "cost_price": 250.0,
             "close_price": 260.0, "value": 520.0, "unrealized_pl": 20.0,
             "currency": "USD"}])
        self.assertEqual(self.s["holdings_value_base"], 963.0)

    def test_holdings_stop_at_the_footnotes(self):
        for h in self.s["holdings"]:
            self.assertNotIn("multiplier", h["symbol"])
            self.assertTrue(h["currency"])

    def test_dividends(self):
        self.assertEqual(self.s["dividends"], [
            {"date": "2024-05-01", "symbol": "C6L.SI", "gross": 8.0, "tax": 0.0,
             "net": 8.0, "currency": "SGD", "phase": "Paid"},
            {"date": "2024-07-01", "symbol": "SPY", "gross": 5.0, "tax": 0.0,
             "net": 5.0, "currency": "USD", "phase": "Paid"}])

    def test_stock_trades_sum_the_comm_fee_column(self):
        # the first trade is printed twice, with a page break between the copies
        self.assertEqual(self.s["trades"], [
            {"date": "2024-03-01", "symbol": "SPY", "side": "", "quantity": 2.0,
             "price": 250.0, "amount": 500.0, "fee": -1.99, "gst": -0.16,
             "currency": "USD"},
            {"date": "2024-06-10", "symbol": "C6L.SI", "side": "", "quantity": 100.0,
             "price": 5.0, "amount": 500.0, "fee": -0.5, "gst": -0.05,
             "currency": "SGD"}])

    def test_forex_trades(self):
        self.assertEqual(self.s["forex"], [
            {"date": "2024-03-01", "pair": "USD.SGD", "side": "", "quantity": 400.0,
             "price": 1.325, "amount": -530.0, "currency": "SGD"},
            {"date": "2024-06-10", "pair": "USD.SGD", "side": "", "quantity": -50.0,
             "price": 1.3, "amount": 65.0, "currency": "SGD"}])

    def test_the_trade_date_wins_over_the_settlement_date(self):
        self.assertEqual(self.s["trades"][0]["date"], "2024-03-01")   # settles 03-05
        self.assertEqual(self.s["forex"][0]["date"], "2024-03-01")

    def test_fx_to_base(self):
        self.assertEqual(self.s["fx_to_base"],
                         {"USD": 1.0, "AUD": 0.62, "SGD": 0.75})
        self.assertEqual(self.s["fx_date"], "2024-12-31")
        self.assertAlmostEqual(1 / self.s["fx_to_base"]["SGD"], 1.3333, places=4)


class Wrapped(unittest.TestCase):
    """The 2026 layout, where names wrap and every trade prints twice."""

    @classmethod
    def setUpClass(cls):
        cls.s = load("wrapped")

    def test_period_and_overview(self):
        self.assertEqual(self.s["period"], {"from": "2026-01-01", "to": "2026-06-30"})
        self.assertEqual(self.s["overview"]["end"]["total"], 930.0)
        self.assertEqual(self.s["overview"]["end"]["dividend_accruals"], 0.0)

    def test_holdings_take_the_symbol_out_of_the_parentheses(self):
        self.assertEqual([(h["symbol"], h["name"]) for h in self.s["holdings"]],
                         [("C6L.SI", "SIA"), ("SPY", "SPDR S&P 500 ETF Trust")])
        self.assertEqual(self.s["holdings"][1]["quantity"], 0.59)
        self.assertEqual(self.s["holdings_value_base"], 900.0)

    def test_every_trade_is_printed_twice_and_kept_once(self):
        self.assertEqual(len(self.s["trades"]), 2)
        self.assertEqual([t["symbol"] for t in self.s["trades"]], ["SPY", "SPY"])
        self.assertEqual([t["date"] for t in self.s["trades"]],
                         ["2026-01-05", "2026-02-03"])
        self.assertEqual([t["side"] for t in self.s["trades"]], ["Open", "Open"])
        self.assertEqual(round(sum(t["amount"] for t in self.s["trades"]), 2), 384.95)
        self.assertEqual(round(sum(t["fee"] for t in self.s["trades"]), 2), -2.0)
        self.assertEqual(round(sum(t["gst"] for t in self.s["trades"]), 2), -0.18)

    def test_a_row_torn_by_a_page_break_is_put_back_together(self):
        # the second trade's name and trade time print at the foot of one page
        # and its figures at the top of the next; without the rejoin the two
        # halves would read as one dated and one undated trade
        torn = self.s["trades"][1]
        self.assertEqual(torn["date"], "2026-02-03")
        self.assertEqual(torn["symbol"], "SPY")
        self.assertEqual(torn["amount"], 189.95)

    def test_forex_is_de_duplicated_too(self):
        self.assertEqual(len(self.s["forex"]), 2)
        self.assertEqual([f["pair"] for f in self.s["forex"]], ["USD.SGD", "USD.SGD"])
        self.assertEqual([f["side"] for f in self.s["forex"]], ["Buy", "Buy"])
        self.assertEqual(sum(f["amount"] for f in self.s["forex"]), -400.0)

    def test_dividends_wrap_their_date_and_carry_a_tax(self):
        self.assertEqual(self.s["dividends"], [
            {"date": "2026-05-20", "symbol": "C6L.SI", "gross": 9.0, "tax": 0.0,
             "net": 9.0, "currency": "SGD", "phase": "Paid"},
            {"date": "2026-03-15", "symbol": "SPY", "gross": 20.0, "tax": 4.0,
             "net": 16.0, "currency": "USD", "phase": "Paid"}])

    def test_accrual_rows_are_not_dividends(self):
        # the fixture prints the SPY dividend again as "Dividend Accruals
        # Increase"; counting it would book the same 16.00 twice
        self.assertIn("Dividend Accruals", (FIXTURES / "wrapped.txt").read_text())
        self.assertEqual([d["phase"] for d in self.s["dividends"]], ["Paid", "Paid"])
        self.assertEqual(sum(d["net"] for d in self.s["dividends"]), 25.0)

    def test_base_figures(self):
        self.assertEqual(self.s["deposits_base"], 310.0)
        self.assertEqual(self.s["dividends_base"], 25.0)
        self.assertEqual(self.s["withholding_tax_base"], -6.0)
        self.assertEqual(self.s["fees_base"], -2.18)

    def test_fx_to_base(self):
        self.assertEqual(self.s["fx_to_base"], {"USD": 1.0, "SGD": 0.78})
        self.assertEqual(self.s["fx_date"], "2026-06-30")


class Sparse(unittest.TestCase):
    """A period with no withdrawal, no trade and no Dividends section."""

    @classmethod
    def setUpClass(cls):
        cls.s = load("sparse")

    def test_the_empty_tables_are_empty_lists_not_missing_keys(self):
        for key in ("withdrawals", "dividends", "trades", "forex"):
            self.assertEqual(self.s[key], [], key)

    def test_absent_cash_report_rows_read_as_zero(self):
        self.assertNotIn("Dividends", self.s["cash_report"])
        self.assertNotIn("Withholding Tax", self.s["cash_report"])
        self.assertEqual(self.s["dividends_base"], 0.0)
        self.assertEqual(self.s["withholding_tax_base"], 0.0)
        self.assertEqual(self.s["fees_base"], 0.0)

    def test_what_is_printed_is_still_read(self):
        self.assertEqual(self.s["deposits"],
                         [{"date": "2027-02-01", "amount": 130.0, "currency": "SGD"}])
        self.assertEqual(self.s["deposits_base"], 100.0)
        self.assertEqual(self.s["holdings_value_base"], 950.0)
        self.assertEqual(self.s["overview"]["end"]["total"], 1080.0)
        self.assertEqual(self.s["fx_to_base"]["SGD"], 0.79)


class Pieces(unittest.TestCase):
    """The helpers the row parsers are built out of."""

    def test_symbol_of(self):
        self.assertEqual(pt.symbol_of("C6L.SI"), ("C6L.SI", ""))
        self.assertEqual(pt.symbol_of("SPDR S&P 500 ETF Trust (SPY)"),
                         ("SPY", "SPDR S&P 500 ETF Trust"))
        self.assertEqual(pt.symbol_of(""), ("", ""))

    def test_dedupe_keeps_the_first_of_each_trade(self):
        rows = [{"date": "d", "symbol": "SPY", "quantity": 1.0, "price": 2.0, "n": 1},
                {"date": "d", "symbol": "SPY", "quantity": 1.0, "price": 2.0, "n": 2},
                {"date": "d", "symbol": "SPY", "quantity": 3.0, "price": 2.0, "n": 3}]
        self.assertEqual([r["n"] for r in pt.dedupe(rows, "symbol")], [1, 3])

    def test_columns_cluster_a_heading_spread_over_three_lines(self):
        cols = pt.Columns([
            " " * 16 + "Activity" + " " * 22 + "Realized P",
            " Symbol" + " " * 3 + "Market" + " " * 10 + "Comm/Fee" + " " * 3 + "GST",
            " " * 18 + "Type" + " " * 28 + "/L"])
        labels = [c[0] for c in cols.cols]
        self.assertIn("Activity Type", labels)
        self.assertIn("Realized P /L", labels)
        self.assertEqual(cols.find("Comm/Fee"), "Comm/Fee")

    def test_blocks_drop_the_page_furniture(self):
        text = ("  Holdings\n\n row one\n\n"
                "This statement also serves as a Tax Invoice for GST purpose.   2/3\n\n"
                "        Tiger Brokers (Singapore) PTE.LTD.\n\n row two\n")
        got = pt.blocks(text)
        self.assertEqual([(b[0], b[2]) for b in got],
                         [("Holdings", [" row one"]), ("Holdings", [" row two"])])


class SecondOldLayout(unittest.TestCase):
    """old-layout.txt: the 2022 to 2025 shape, every field of every table."""

    @classmethod
    def setUpClass(cls):
        cls.s = pt.parse_text(raw("old-layout"), "2021/1-12.pdf")

    def test_header_and_overview(self):
        self.assertEqual(self.s["file"], "2021/1-12.pdf")
        self.assertEqual(self.s["account"], "99001122")
        self.assertEqual(self.s["base_currency"], "USD")
        self.assertEqual(self.s["period"], {"from": "2021-03-08", "to": "2021-12-31"})
        self.assertEqual(self.s["overview"]["begin"],
                         {"cash": 10.0, "stock": 0.0, "dividend_accruals": 0.0,
                          "total": 10.0})
        self.assertEqual(self.s["overview"]["end"],
                         {"cash": 33.85, "stock": 876.78, "dividend_accruals": 0.0,
                          "total": 910.63})

    def test_every_cash_report_row_with_its_printed_label(self):
        self.assertEqual(self.s["cash_report"], {
            "Starting Cash": 10.0, "Commissions": -1.72, "Platform Fees": -1.0,
            "GST": -0.25, "Settlement Fees": -0.13, "Deposits": 851.0,
            "Net Trades (Purchase)": -824.05, "Ending Cash": 33.85})
        self.assertEqual(self.s["fees_base"], -3.1)   # -1.72 -1 -0.25 -0.13

    def test_the_per_currency_section_does_not_leak_in(self):
        # the fixture prints "Custody Fees" and a different Deposits figure
        # under Currency: SGD, neither of which is the base summary's
        self.assertIn("Custody Fees", raw("old-layout"))
        self.assertNotIn("Custody Fees", self.s["cash_report"])
        self.assertEqual(self.s["cash_report"]["Deposits"], 851.0)

    def test_no_withdrawal_and_no_dividends_section(self):
        self.assertEqual(self.s["withdrawals"], [])
        self.assertEqual(self.s["dividends"], [])
        self.assertEqual(self.s["dividends_base"], 0.0)
        self.assertEqual(self.s["withholding_tax_base"], 0.0)

    def test_the_deposit_and_the_printed_base_total(self):
        # the page breaks between the deposit and its Total(In Base) row
        self.assertEqual(self.s["deposits"],
                         [{"date": "2021-03-08", "amount": 1150.0, "currency": "SGD"}])
        self.assertEqual(self.s["deposits_base"], 851.0)

    def test_holdings_and_the_printed_base_value(self):
        self.assertEqual(self.s["holdings"], [
            {"symbol": "C6L.SI", "name": "", "quantity": 150.0, "cost_price": 3.4577333,
             "close_price": 3.62, "value": 543.0, "unrealized_pl": 24.34,
             "currency": "SGD"},
            {"symbol": "SPY", "name": "", "quantity": 1.0, "cost_price": 443.09,
             "close_price": 474.96, "value": 474.96, "unrealized_pl": 31.87,
             "currency": "USD"}])
        self.assertEqual(self.s["holdings_value_base"], 876.78)

    def test_the_fee_is_the_sum_of_the_charges_in_the_comm_fee_cell(self):
        # the second trade is read after the page break reprints the header
        self.assertEqual(self.s["trades"], [
            {"date": "2021-05-12", "symbol": "SPY", "side": "", "quantity": 1.0,
             "price": 441.1, "amount": 441.1, "fee": -1.99, "gst": -0.14,
             "currency": "USD"},
            {"date": "2021-03-16", "symbol": "C6L.SI", "side": "", "quantity": 150.0,
             "price": 3.45, "amount": 517.5, "fee": -1.16, "gst": -0.15,
             "currency": "SGD"}])

    def test_forex_with_no_activity_type_printed(self):
        self.assertEqual(self.s["forex"], [
            {"date": "2021-03-09", "pair": "USD.SGD", "side": "", "quantity": 833.3333,
             "price": 1.38, "amount": -1150.0, "currency": "SGD"}])

    def test_the_exchange_rate_table(self):
        self.assertEqual(self.s["fx_to_base"], {"USD": 1.0, "EUR": 1.1, "SGD": 0.74})
        self.assertEqual(self.s["fx_date"], "2021-12-31")

    def test_a_page_break_moved_between_two_holdings_changes_nothing(self):
        moved = pt.parse_text(inject("old-layout", ("SPY", "443.0900000"), BREAK), "x")
        self.assertEqual(moved["holdings"], self.s["holdings"])
        self.assertEqual(moved["holdings_value_base"], 876.78)


class SecondWrappedLayout(unittest.TestCase):
    """wrapped-2026.txt: the 2026 shape, every field of every table."""

    @classmethod
    def setUpClass(cls):
        cls.s = pt.parse_text(raw("wrapped-2026"), "2027/1-6.pdf")

    def test_header_and_overview(self):
        self.assertEqual(self.s["period"], {"from": "2027-01-01", "to": "2027-06-30"})
        self.assertEqual(self.s["overview"]["end"],
                         {"cash": 720.51, "stock": 2279.98, "dividend_accruals": 6.4,
                          "total": 3006.89})

    def test_a_withdrawal_is_kept_positive_and_out_of_the_deposits(self):
        self.assertEqual(self.s["deposits"],
                         [{"date": "2027-01-05", "amount": 1600.0, "currency": "SGD"}])
        self.assertEqual(self.s["withdrawals"],
                         [{"date": "2027-04-05", "amount": 150.0, "currency": "SGD"}])
        self.assertEqual(self.s["deposits_base"], 1140.72)

    def test_a_holding_torn_across_a_page_break_is_one_row(self):
        # the name prints at the foot of one page and the figures at the head of
        # the next, under a fresh copy of the Holdings header
        self.assertEqual(len(self.s["holdings"]), 2)
        self.assertEqual(self.s["holdings"][1],
                         {"symbol": "SPY", "name": "SPDR S&P 500 ETF Trust",
                          "quantity": 2.38204, "cost_price": 589.1840607,
                          "close_price": 753.05, "value": 1793.8,
                          "unrealized_pl": 390.34, "currency": "USD"})
        self.assertEqual(self.s["holdings"][0]["name"], "SIA")
        self.assertEqual(self.s["holdings_value_base"], 2279.98)

    def test_trades_and_forex_are_kept_once_and_add_up_to_the_printed_total(self):
        self.assertEqual(self.s["trades"][0],
                         {"date": "2027-01-05", "symbol": "SPY", "side": "Open",
                          "quantity": 1.38204, "price": 694.17, "amount": 959.37,
                          "fee": -1.0, "gst": -0.09, "currency": "USD"})
        self.assertEqual([(t["symbol"], t["side"]) for t in self.s["trades"]],
                         [("SPY", "Open"), ("AAPL", "Close")])
        self.assertEqual(round(sum(t["amount"] for t in self.s["trades"]), 2), 1442.49)
        self.assertEqual(round(sum(t["fee"] for t in self.s["trades"]), 2), -2.0)
        self.assertEqual(self.s["forex"], [
            {"date": "2027-01-05", "pair": "USD.SGD", "side": "Buy",
             "quantity": 990.4762, "price": 1.3125, "amount": -1300.0,
             "currency": "SGD"},
            {"date": "2027-02-07", "pair": "USD.SGD", "side": "Sell",
             "quantity": -2.41, "price": 1.3387, "amount": 3.23,
             "currency": "SGD"}])

    def test_a_stock_trade_torn_across_a_page_break_is_one_trade(self):
        torn = pt.parse_text(inject("wrapped-2026", ("Trust", "1.38204"), BREAK), "x")
        self.assertEqual(torn["trades"], self.s["trades"])

    def test_a_wrapped_date_a_blank_tax_cell_and_a_printed_one(self):
        self.assertEqual(self.s["dividends"], [
            {"date": "2027-05-28", "symbol": "C6L.SI", "gross": 27.0, "tax": 0.0,
             "net": 27.0, "currency": "SGD", "phase": "Paid"},
            {"date": "2027-01-30", "symbol": "SPY", "gross": 4.47, "tax": 1.34,
             "net": 3.13, "currency": "USD", "phase": "Paid"}])
        self.assertEqual(self.s["dividends_base"], 25.71)
        self.assertEqual(self.s["withholding_tax_base"], -1.34)
        self.assertEqual(self.s["fees_base"], -2.18)

    def test_the_exchange_rate_table_continues_onto_the_next_page(self):
        self.assertEqual(self.s["fx_to_base"],
                         {"USD": 1.0, "AUD": 0.7167, "SGD": 0.7867})
        self.assertEqual(self.s["fx_date"], "2027-06-30")
        self.assertAlmostEqual(1 / self.s["fx_to_base"]["SGD"], 1.2711, places=4)


class Shape(unittest.TestCase):
    """The dict the analytics side reads has the same keys on every layout."""

    KEYS = {"file", "account", "base_currency", "period", "overview", "cash_report",
            "deposits", "withdrawals", "deposits_base", "dividends", "dividends_base",
            "withholding_tax_base", "fees_base", "holdings", "holdings_value_base",
            "trades", "forex", "fx_to_base", "fx_date"}

    def test_every_fixture_fills_every_key(self):
        for name in ("legacy", "wrapped", "sparse", "old-layout", "wrapped-2026"):
            with self.subTest(name):
                s = pt.parse_text(raw(name), f"{name}.pdf")
                self.assertEqual(set(s), self.KEYS)
                for half in ("begin", "end"):
                    self.assertEqual(set(s["overview"][half]),
                                     {"cash", "stock", "dividend_accruals", "total"})
                for t in s["trades"]:
                    self.assertEqual(set(t), {"date", "symbol", "side", "quantity",
                                              "price", "amount", "fee", "gst",
                                              "currency"})
                for f in s["forex"]:
                    self.assertEqual(set(f), {"date", "pair", "side", "quantity",
                                              "price", "amount", "currency"})
                for h in s["holdings"]:
                    self.assertEqual(set(h), {"symbol", "name", "quantity",
                                              "cost_price", "close_price", "value",
                                              "unrealized_pl", "currency"})
                for d in s["dividends"]:
                    self.assertEqual(set(d), {"date", "symbol", "gross", "tax", "net",
                                              "currency", "phase"})


class Folder(unittest.TestCase):
    """parse_pdf and parse_folder, with pdftotext stubbed out."""

    def run_folder(self, files):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            for name, fixture in files.items():
                path = folder / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"%PDF-1.4")
                (folder / f"{name}.source").write_text(fixture)

            def fake(pdf, write=True):
                return raw(Path(str(pdf) + ".source").read_text().strip())

            with mock.patch.object(pt, "pdf_to_text", fake):
                return pt.parse_folder(folder)

    def test_a_pdf_is_named_by_its_path_inside_the_folder(self):
        got = self.run_folder({"2027/1-6.pdf": "wrapped-2026"})
        self.assertEqual([s["file"] for s in got], ["2027/1-6.pdf"])
        self.assertEqual(got[0]["period"]["from"], "2027-01-01")

    def test_statements_come_back_oldest_first(self):
        got = self.run_folder({"2027/1-6.pdf": "wrapped-2026",
                               "2021/1-12.pdf": "old-layout"})
        self.assertEqual([s["file"] for s in got],
                         ["2021/1-12.pdf", "2027/1-6.pdf"])
        self.assertEqual([s["period"]["from"] for s in got],
                         ["2021-03-08", "2027-01-01"])

    def test_two_exports_of_one_year_come_back_shorter_first(self):
        """By name "2025/1-12.pdf" sorts before "2025/1-8.pdf". Both start on
        1 January, so the order is the period's end, never the file name: the
        full year comes last and is the one read as the latest statement."""
        def fake(pdf, folder, write_text=True):
            last = int(pdf.stem.split("-")[1])
            return {"file": str(pdf.relative_to(folder)),
                    "period": {"from": "2025-01-01",
                               "to": f"2025-{last:02d}-{28 if last == 2 else 30:02d}"}}

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "2025").mkdir()
            for name in ("1-12.pdf", "1-8.pdf"):
                (folder / "2025" / name).write_bytes(b"%PDF-1.4")
            with mock.patch.object(pt, "parse_pdf", fake):
                got = pt.parse_folder(folder)
        self.assertEqual([s["file"] for s in got], ["2025/1-8.pdf", "2025/1-12.pdf"])

    def test_a_missing_folder_is_no_statements(self):
        self.assertEqual(pt.parse_folder(Path("/nonexistent/tiger-brokers")), [])

    def test_parse_pdf_shells_out_to_pdftotext_and_keeps_the_dump(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "2021").mkdir()
            pdf = folder / "2021" / "1-12.pdf"
            pdf.write_bytes(b"%PDF-1.4")
            with mock.patch.object(pt.subprocess, "run") as run:
                run.return_value = mock.Mock(stdout=raw("old-layout"))
                s = pt.parse_pdf(pdf, folder)
            self.assertEqual(run.call_args[0][0][:2], ["pdftotext", "-layout"])
            self.assertEqual(s["file"], "2021/1-12.pdf")
            self.assertEqual(s["holdings_value_base"], 876.78)
            self.assertEqual((folder / "2021" / "1-12.txt").read_text(),
                             raw("old-layout"))

    def test_parse_pdf_can_be_told_not_to_write_the_dump(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            pdf = folder / "1-12.pdf"
            pdf.write_bytes(b"%PDF-1.4")
            with mock.patch.object(pt.subprocess, "run") as run:
                run.return_value = mock.Mock(stdout=raw("old-layout"))
                pt.parse_pdf(pdf, folder, write_text=False)
            self.assertFalse((folder / "1-12.txt").exists())


class Summary(unittest.TestCase):

    def test_the_cli_line_names_the_period_and_the_four_figures(self):
        line = pt.summary(pt.parse_text(raw("wrapped-2026"), "2027/1-6.pdf"))
        for want in ("2027/1-6.pdf", "2027-01-01 .. 2027-06-30", "910.63",
                     "3,006.89", "1,600.00 SGD", "2,279.98 USD"):
            self.assertIn(want, line)


if __name__ == "__main__":
    unittest.main()
