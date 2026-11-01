"""The Tiger Brokers stat, and the two hub rows it feeds.

The fixture's snapshot carries no `portfolio` key by default, which is what a
snapshot written before the brokerage was loaded looks like, so the first test
here is that the stat still returns a result. Everything after it runs on the
three invented statements in server/tests/analytics_fixture.py, whose numbers are
consistent with each other and with the fixture's own monthly transfers to the
broker.
"""
import isolate  # noqa: F401  first: no test writes the real record or cache
import copy
import unittest
from datetime import date


import analytics_fixture as fixture

from analytics import publish
from analytics.stats import portfolio
from finance import dates
from finance.money import fx_minor, rate_e9, stat_cents

PAYEE = portfolio.PAYEE


def snapshot(statements=None, payee=PAYEE):
    """The fixture with a brokerage block on it.

    The fixture calls the broker "Tiger" because that is what its recurring
    series is named; the stat matches the payee the rules give the real
    transfers, so the rows are renamed here rather than in the fixture, where
    other tests hold that name.
    """
    snap = fixture.snapshot(portfolio=fixture.portfolio(statements))
    if payee:
        snap = copy.deepcopy(snap)
        for row in snap["transactions"]:
            if row["payee"] == "Tiger":
                row["payee"] = payee
    return snap


class WithNoStatements(unittest.TestCase):

    def test_a_snapshot_with_no_portfolio_key_still_produces_a_result(self):
        res = portfolio.run(fixture.snapshot(), None)
        self.assertEqual(res["id"], "portfolio")
        self.assertEqual(res["data"]["periods"], [])
        self.assertEqual(res["data"]["summary"]["statements"], 0)
        self.assertEqual(len(res["findings"]), 1)
        self.assertIn("No Tiger Brokers statements", res["findings"][0])

    def test_an_empty_folder_reads_the_same_way(self):
        res = portfolio.run(snapshot(statements=[]), None)
        self.assertEqual(res["data"]["summary"]["value_sgd_minor"], 0)
        self.assertEqual(res["data"]["holdings"], [])
        self.assertEqual(res["data"]["deposits"], [])


class ThePeriods(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.res = portfolio.run(snapshot(), None)
        cls.rows = cls.res["data"]["periods"]

    def test_one_row_per_statement_oldest_first(self):
        self.assertEqual([r["period_to"] for r in self.rows],
                         ["2023-12-31", "2024-12-31", "2025-12-31"])

    def test_contributions_accumulate_across_statements(self):
        self.assertEqual([r["deposits_sgd_minor"] for r in self.rows],
                         [100_000, 200_000, 250_000])
        self.assertEqual([r["contributed_sgd_minor"] for r in self.rows],
                         [100_000, 300_000, 550_000])

    def test_each_period_is_converted_at_its_own_printed_rate(self):
        self.assertEqual(self.rows[-1]["sgd_per_base"], 1.25)      # 1 / 0.80
        self.assertEqual(self.rows[-1]["fx_to_sgd_e9"], 1_250_000_000)
        self.assertEqual(self.rows[-1]["base_currency"], "USD")
        self.assertEqual(self.rows[-1]["value_base_minor"], 480_000)
        self.assertEqual(self.rows[-1]["value_sgd_minor"], 600_000)
        self.assertEqual(self.rows[0]["value_sgd_minor"], 105_405)      # 780 / 0.74

    def test_the_gain_is_the_value_against_everything_paid_in(self):
        self.assertEqual(self.rows[-1]["gain_sgd_minor"], 50_000)
        self.assertEqual(self.rows[1]["gain_sgd_minor"], 43_333)

    def test_cash_holdings_dividends_and_costs_come_off_the_statement(self):
        last = self.rows[-1]
        self.assertEqual(last["cash_base_minor"], 24_000)
        self.assertEqual(last["holdings_base_minor"], 456_000)
        self.assertEqual(last["dividends_base_minor"], 1_760)
        self.assertEqual(last["fees_base_minor"], -872)
        self.assertEqual(last["withholding_base_minor"], -540)

    def test_the_chart_plots_what_was_paid_in_against_what_it_is_worth(self):
        chart = self.res["chart"]
        self.assertEqual(chart["x"], "period_to")
        self.assertEqual(chart["series"], ["contributed_sgd_minor", "value_sgd_minor"])
        self.assertEqual(chart["unit"], "sgd_minor")
        for name in chart["series"] + [chart["x"]]:
            self.assertIn(name, self.rows[0])


class TheHoldings(unittest.TestCase):

    def test_the_latest_positions_carry_an_sgd_value(self):
        held = portfolio.run(snapshot(), None)["data"]["holdings"]
        self.assertEqual([h["symbol"] for h in held], ["SPY", "C6L.SI"])
        # 3,600 USD through the base at 1 / 0.80, and 1,200 SGD unchanged
        self.assertEqual([h["value_sgd_minor"] for h in held], [450_000, 120_000])
        # each in its own currency's cents, the currency beside it
        self.assertEqual([(h["value_minor"], h["currency"]) for h in held],
                         [(360_000, "USD"), (120_000, "SGD")])
        # a price per share stays as the statement prints it
        self.assertEqual(held[1]["close_price"], 6.0)

    def test_the_holdings_and_the_cash_add_up_to_the_headline(self):
        data = portfolio.run(snapshot(), None)["data"]
        last = data["periods"][-1]
        cash_sgd = fx_minor(last["cash_base_minor"], last["fx_to_sgd_e9"])
        self.assertLessEqual(abs(sum(h["value_sgd_minor"] for h in data["holdings"]) + cash_sgd
                                 - data["summary"]["value_sgd_minor"]), 1)


class TheDeposits(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.deposits = portfolio.run(snapshot(), None)["data"]["deposits"]

    def test_a_deposit_older_than_the_bank_rows_is_undecided(self):
        old = [d for d in self.deposits if d["date"] < "2025-01"]
        self.assertEqual(len(old), 3)
        self.assertTrue(all(d["matched"] is None for d in old))

    def test_a_deposit_a_bank_row_pays_for_is_matched(self):
        by_date = {d["date"]: d for d in self.deposits}
        self.assertTrue(by_date["2025-03-08"]["matched"])
        self.assertTrue(by_date["2025-04-08"]["matched"])

    def test_a_deposit_with_no_row_near_it_is_not(self):
        by_date = {d["date"]: d for d in self.deposits}
        self.assertIs(by_date["2025-05-20"]["matched"], False)

    def test_one_bank_row_cannot_pay_for_two_deposits(self):
        """Both 2025 deposits sit on the 8th of a month, one bank row each."""
        snap = snapshot()
        rows = [r for r in snap["transactions"] if r["payee"] == PAYEE]
        keep = {"2025-03-08", "2025-04-08"}
        snap["transactions"] = [r for r in snap["transactions"]
                                if r["payee"] != PAYEE or r["date"] in keep]
        self.assertEqual(len(rows) - 2, 10)
        matched = [d["matched"] for d in portfolio.run(snap, None)["data"]["deposits"]]
        self.assertEqual(matched.count(True), 2)

    def test_a_row_named_something_else_leaves_the_deposit_unmatched(self):
        res = portfolio.run(snapshot(payee=None), None)
        summary = res["data"]["summary"]
        self.assertEqual(summary["unmatched_deposits"], 3)
        self.assertIn("no Tiger Brokers row within", " ".join(res["findings"]))


class TheSummary(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.res = portfolio.run(snapshot(), None)
        cls.summary = cls.res["data"]["summary"]

    def test_the_headline_is_the_latest_statement(self):
        self.assertEqual(self.summary["as_of"], "2025-12-31")
        self.assertEqual(self.summary["value_sgd_minor"], 600_000)
        self.assertEqual(self.summary["value_base_minor"], 480_000)
        self.assertEqual(self.summary["base_currency"], "USD")
        self.assertEqual(self.summary["contributed_sgd_minor"], 550_000)
        self.assertEqual(self.summary["gain_sgd_minor"], 50_000)
        self.assertEqual(self.summary["gain_pct"], 9.1)
        self.assertEqual(self.summary["statements"], 3)
        self.assertEqual(self.summary["unmatched_deposits"], 1)

    def test_net_worth_is_the_cash_the_runway_stat_found_plus_the_investments(self):
        from analytics.stats import savings_runway
        cash = savings_runway.run(snapshot(), None)["data"]["summary"]
        # the runway stat's cash, in whichever shape its result has (G20)
        self.assertEqual(self.summary["cash_sgd_minor"], stat_cents(cash, "cash"))
        self.assertEqual(self.summary["cash_as_of"], cash["as_of"])
        self.assertEqual(self.summary["net_worth_sgd_minor"],
                         stat_cents(cash, "cash") + self.summary["value_sgd_minor"])

    def test_the_findings_name_both_as_of_dates(self):
        line = self.res["findings"][1]
        self.assertIn(dates.day_label(self.summary["as_of"]), line)
        self.assertIn(dates.date_label(self.summary["cash_as_of"]), line)

    def test_the_sources_name_the_statements_and_the_bank_rows(self):
        self.assertEqual([s["database"] for s in self.res["sources"]],
                         ["Tiger Brokers statements", "Transactions"])
        self.assertEqual(self.res["sources"][0]["rows_used"], 3)

    def test_no_finding_uses_an_em_dash(self):
        for line in self.res["findings"] + self.res["caveats"]:
            self.assertNotIn("—", line)


class TheStaleStatementFinding(unittest.TestCase):
    """The fixture's snapshot was generated on 2026-01-01."""

    def test_a_statement_a_month_old_says_nothing(self):
        res = portfolio.run(snapshot(), None)
        self.assertNotIn("stale", " ".join(res["findings"]))

    def test_a_statement_more_than_four_months_old_asks_for_the_next_one(self):
        older = fixture.tiger_statements()[:2]           # ends 2024-12-31
        res = portfolio.run(snapshot(statements=older), None)
        line = [f for f in res["findings"] if "stale" in f]
        self.assertEqual(len(line), 1)
        self.assertIn("stops at 31 Dec 2024,", line[0])
        self.assertIn("export the next one", line[0])


class TheHubRows(unittest.TestCase):

    # G20: savings-runway's money in cents, as the stat writes it
    RUNWAY = {"data": {"summary": {"cash_minor": 629_342, "as_of": "2026-07",
                                   "runway_6m": 2.5, "spend_avg_6m_minor": 252_129}}}

    def rows(self, results):
        return {r["Name"]: r for r in publish.metrics_from(results, None,
                                                           date(2026, 9, 4))}

    def test_both_rows_follow_the_runway_row(self):
        res = portfolio.run(snapshot(), None)
        names = [r["Name"] for r in publish.metrics_from(
            {"savings-runway": self.RUNWAY, "portfolio": res}, None,
            date(2026, 9, 4))]
        self.assertEqual(names, ["Cash, all accounts", "Runway, months",
                                 "Investments, market value",
                                 "Net worth, cash plus investments"])

    def test_the_two_rows_carry_the_stat_and_both_dates(self):
        res = portfolio.run(snapshot(), None)
        rows = self.rows({"savings-runway": self.RUNWAY, "portfolio": res})
        value = rows["Investments, market value"]
        self.assertEqual(value["Value_minor"], 600_000)
        self.assertEqual(value["Unit"], "SGD")
        self.assertEqual(value["As of"], "2025-12-31")
        self.assertEqual(value["Stat"], "portfolio")
        self.assertIn("contributed 5,500.00", value["Note"])
        self.assertIn("gain 500.00", value["Note"])
        worth = rows["Net worth, cash plus investments"]
        self.assertEqual(worth["As of"], "2025-12-31")     # the later of the two
        self.assertIn(str(res["data"]["summary"]["cash_as_of"]), worth["Note"])
        self.assertIn("2025-12-31", worth["Note"])

    def test_no_statements_means_no_rows_rather_than_a_zero(self):
        res = portfolio.run(fixture.snapshot(), None)
        rows = self.rows({"savings-runway": self.RUNWAY, "portfolio": res})
        self.assertNotIn("Investments, market value", rows)
        self.assertNotIn("Net worth, cash plus investments", rows)


if __name__ == "__main__":
    unittest.main()


class DepositsAfterTheLastBankMonth(unittest.TestCase):
    """A Tiger export can run past the newest bank statement loaded. A deposit
    in a month the bank has not printed yet is undecided, not missing."""

    def test_a_deposit_newer_than_the_bank_rows_is_undecided(self):
        statements = copy.deepcopy(fixture.tiger_statements())
        last_bank_month = max(t["month"] for t in fixture.transactions())
        year = int(last_bank_month[:4]) + 1
        statements[-1]["deposits"].append(
            {"date": f"{year}-01-05", "amount": 1000.0, "currency": "SGD"})
        snap = fixture.snapshot(portfolio=fixture.portfolio(statements))
        deposits = portfolio.run(snap, None)["data"]["deposits"]
        newest = [d for d in deposits if d["date"] == f"{year}-01-05"]
        self.assertEqual(len(newest), 1)
        self.assertIsNone(newest[0]["matched"])


def export(st, name, start, end, deposits=None, total=None):
    """A copy of statement `st` under another file name and period, keeping
    only its deposits inside the new period (or the ones given)."""
    out = copy.deepcopy(st)
    out["file"], out["period"] = name, {"from": start, "to": end}
    out["deposits"] = (deposits if deposits is not None else
                       [d for d in out["deposits"] if start <= d["date"] <= end])
    if total is not None:
        out["overview"]["end"]["total"] = total
    return out


class OverlappingExports(unittest.TestCase):
    """A year exported twice: a year-to-date file kept beside the full year that
    replaced it, as the runbook asks, or still held by the record from an
    earlier load. Each deposit is money paid in once, however many statements
    print it, and the latest statement is the one that ends last."""

    @classmethod
    def setUpClass(cls):
        cls.base = fixture.tiger_statements()
        cls.full = cls.base[-1]
        # Jan to Aug 2025, valued at its own 31 August close
        cls.ytd = export(cls.full, "2025/1-8.pdf", "2025-01-01", "2025-08-31", total=4000.0)
        cls.one = portfolio.run(snapshot(statements=cls.base), None)["data"]

    def run_on(self, statements):
        return portfolio.run(snapshot(statements=statements), None)

    def test_a_year_to_date_export_beside_the_full_year_counts_each_deposit_once(self):
        # the parser's old order: "2025/1-12.pdf" sorts before "2025/1-8.pdf"
        data = self.run_on(self.base + [self.ytd])["data"]
        summary = data["summary"]
        self.assertEqual(summary["contributed_sgd_minor"], 550_000)
        self.assertEqual(summary["gain_sgd_minor"], 50_000)
        self.assertEqual(summary["gain_pct"], 9.1)
        self.assertEqual(summary["unmatched_deposits"], 1)
        self.assertEqual(summary["statements"], 3)
        self.assertEqual(data["deposits"], self.one["deposits"])
        self.assertEqual(data["periods"], self.one["periods"])

    def test_the_full_year_is_the_latest_statement_whatever_the_order(self):
        for order in (self.base + [self.ytd], self.base[:-1] + [self.ytd, self.full],
                      [self.ytd] + self.base, list(reversed(self.base + [self.ytd]))):
            summary = self.run_on(order)["data"]["summary"]
            self.assertEqual(summary["as_of"], "2025-12-31")
            self.assertEqual(summary["value_sgd_minor"], 600_000)

    def test_the_export_left_out_is_named_with_the_one_that_covers_it(self):
        res = self.run_on(self.base + [self.ytd])
        line = [f for f in res["findings"] if "2025/1-8.pdf" in f]
        self.assertEqual(len(line), 1)
        self.assertIn("2025/1-12.pdf", line[0])
        self.assertEqual(res["sources"][0]["rows_used"], 3)
        self.assertFalse(any("2025/1-8.pdf" in f for f in self.run_on(self.base)["findings"]))

    def test_a_re_export_of_the_same_period_is_read_once(self):
        again = export(self.full, "2025/1-12 (2).pdf", "2025-01-01", "2025-12-31")
        summary = self.run_on(self.base + [again])["data"]["summary"]
        self.assertEqual(summary["contributed_sgd_minor"], 550_000)
        self.assertEqual(summary["statements"], 3)

    def test_two_exports_that_only_partly_overlap_share_their_common_deposits(self):
        """Jan to Aug and Jun to Dec both print the two S$300 deposits of 15
        July. Both statements stay (neither holds the other), and each deposit
        counts once: the two same-day deposits are two, not one and not four."""
        july = [{"date": "2025-07-15", "amount": 300.0, "currency": "SGD"}] * 2
        first = export(self.full, "2025/1-8.pdf", "2025-01-01", "2025-08-31",
                       deposits=self.full["deposits"] + july, total=4000.0)
        second = export(self.full, "2025/6-12.pdf", "2025-06-01", "2025-12-31",
                        deposits=july + [{"date": "2025-10-01", "amount": 200.0,
                                          "currency": "SGD"}])
        data = self.run_on(self.base[:-1] + [second, first])["data"]
        self.assertEqual([r["period_to"] for r in data["periods"]],
                         ["2023-12-31", "2024-12-31", "2025-08-31", "2025-12-31"])
        self.assertEqual([r["deposits_sgd_minor"] for r in data["periods"]],
                         [100_000, 200_000, 310_000, 20_000])
        self.assertEqual(data["summary"]["contributed_sgd_minor"], 630_000)
        self.assertEqual(data["summary"]["as_of"], "2025-12-31")
        self.assertEqual([d["date"] for d in data["deposits"]].count("2025-07-15"), 2)
        self.assertEqual(len(data["deposits"]), 9)

    def test_another_account_s_statement_for_the_same_year_is_not_left_out(self):
        other = export(self.full, "2025/1-12-joint.pdf", "2025-01-01", "2025-12-31")
        other["account"] = "50000002"
        summary = self.run_on(self.base + [other])["data"]["summary"]
        self.assertEqual(summary["statements"], 4)
        self.assertEqual(summary["contributed_sgd_minor"], 550_000 + 250_000)


class InCents(unittest.TestCase):
    """G20: every amount is integer cents of its own currency, converted to SGD
    cents by the record's one rule."""

    # the names the stat wrote in dollars before G20
    DOLLARS = {"deposits_sgd", "deposits_base", "contributed_sgd", "value_base", "value_sgd",
               "cash_base", "holdings_base", "dividends_base", "fees_base", "withholding_base",
               "gain_sgd", "value", "unrealized_pl", "amount", "cash_sgd", "net_worth_sgd",
               "dividends_base_latest"}

    def keys(self, o):
        if isinstance(o, dict):
            return list(o.items()) + [x for v in o.values() for x in self.keys(v)]
        if isinstance(o, list):
            return [x for v in o for x in self.keys(v)]
        return []

    def test_no_money_is_left_in_dollars(self):
        for snap in (snapshot(), snapshot(statements=[])):
            data = portfolio.run(snap, None)["data"]
            for k, v in self.keys(data):
                with self.subTest(key=k):
                    self.assertNotIn(k, self.DOLLARS)
                    if k.endswith("_minor"):
                        self.assertTrue(v is None or type(v) is int, (k, v))

    def test_a_deposit_in_us_dollars_goes_to_sgd_by_the_record_s_rule(self):
        statements = copy.deepcopy(fixture.tiger_statements())
        statements[-1]["deposits"] = [{"date": "2025-03-08", "amount": 1000.0, "currency": "SGD"},
                                      {"date": "2025-06-02", "amount": 333.33, "currency": "USD"}]
        data = portfolio.run(snapshot(statements=statements), None)["data"]
        usd = [d for d in data["deposits"] if d["currency"] == "USD"]
        self.assertEqual([d["amount_minor"] for d in usd], [33_333])      # US cents, as printed
        # 333.33 USD at 1 / 0.80 is 416.6625 SGD: half up to the cent, as v_txn rounds
        self.assertEqual(fx_minor(33_333, rate_e9(1 / 0.80)), 41_666)
        self.assertEqual(data["periods"][-1]["deposits_sgd_minor"], 100_000 + 41_666)

    def test_a_third_currency_goes_through_the_base(self):
        st = {"fx_to_base": {"SGD": 0.80, "USD": 1.0, "EUR": 1.10}, "base_currency": "USD"}
        rates = portfolio.rates(st)
        self.assertEqual(rates["EUR"], 1_375_000_000)                    # 1.10 / 0.80
        self.assertEqual(portfolio.to_sgd(10_000, "EUR", rates), 13_750)
        self.assertEqual(portfolio.to_sgd(10_000, "JPY", rates), 10_000)  # no rate: face value
        self.assertEqual(portfolio.to_sgd(10_000, "SGD", {}), 10_000)

    def test_a_bank_row_a_cent_away_still_pays_for_the_deposit(self):
        snap = snapshot()
        for r in snap["transactions"]:
            if r["payee"] == PAYEE and r["date"] == "2025-03-08":
                r["amount_sgd_minor"] -= 1
        by_date = {d["date"]: d for d in portfolio.run(snap, None)["data"]["deposits"]}
        self.assertTrue(by_date["2025-03-08"]["matched"])

    def test_the_findings_print_amounts_as_the_app_does(self):
        res = portfolio.run(snapshot(), None)
        self.assertIn("Tiger Brokers held S$6,000.00", res["findings"][0])
        self.assertNotIn("SGD ", " ".join(res["findings"]))
