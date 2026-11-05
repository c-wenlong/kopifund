"""Which months count as full months: the months every open bank account covers.

`common.coverage` decides it once and every average reads it (server/analytics/SPEC.md,
decision 6). Each case here is one way the rule went wrong on the invented
fixture (server/tests/analytics_fixture.py): an account that closed, one that prints
half-yearly, wallet-only months before the first bank statement, and a new
month that has only just begun.
"""
import isolate  # noqa: F401  first: no test writes the real record or cache
import copy
import unittest

import analytics_fixture as fx
from analytics.stats import category_breakdown, common as c, forward_view, monthly_cashflow, savings_runway

OLD = "B2"
OLD_NAME = "Old Bank · B2"


def bank(month, opening, closing, account=OLD, currency="SGD"):
    """One deposit-account statement block for the second bank account, in cents."""
    return {"name": f"{account} · {currency} · {month}", "account": OLD_NAME, "account_no": account,
            "period_end": f"{month}-28", "currency": currency, "opening_minor": opening,
            "closing_minor": closing, "total_in_minor": 0, "total_out_minor": 0, "fx_to_sgd": None,
            "closing_sgd_minor": closing, "reconciled": True, "has_balances": True}


def with_second_bank(*blocks):
    """The fixture with a second bank account that printed `blocks`."""
    s = fx.snapshot()
    s["periods"] = sorted(s["periods"] + list(blocks), key=lambda b: b["name"])
    s["accounts"] = s["accounts"] + [{"name": OLD_NAME, "account_no": OLD, "institution": "Old Bank",
                                      "class": "Asset", "subclass": "Cash", "base_currency": "SGD"}]
    return s


def partial_months(s):
    cov = c.coverage(s)
    return [m for m in c.month_range(s) if cov[m]["partial"]]


class NoPhases(unittest.TestCase):
    """Every case runs with no [analytics] phases, the default a new user has."""

    def setUp(self):
        self.enterContext(fx.settings(""))


class AClosedAccount(NoPhases):

    def test_stops_counting_after_its_last_statement(self):
        s = with_second_bank(bank("2025-01", 0, 50_000), bank("2025-02", 50_000, 50_000),
                             bank("2025-03", 50_000, 50_000), bank("2025-04", 50_000, 0))
        self.assertEqual(c.full_months(s), fx.MONTHS)
        self.assertEqual(c.coverage(s)["2025-06"]["expected"], [fx.MAIN])
        self.assertEqual(c.coverage(s)["2025-03"]["expected"], [fx.MAIN, OLD])
        self.assertEqual(monthly_cashflow.run(s)["data"]["totals"]["months"], 12)

    def test_does_not_freeze_the_cash_headline(self):
        s = with_second_bank(bank("2025-01", 0, 50_000), bank("2025-02", 50_000, 0))
        summary = savings_runway.run(s)["data"]["summary"]
        self.assertEqual(summary["as_of_complete"], "2025-12")
        self.assertEqual(summary["cash_minor"], savings_runway.run(fx.snapshot())["data"]["summary"]["cash_minor"])
        # an account that closed holds nothing and is not "showing an earlier month's balance"
        self.assertNotIn(c.account_labels(s).of(OLD), summary["carried"])

    def test_an_account_that_stops_with_money_in_it_is_still_a_hole(self):
        """No statement after one that leaves money in the account is not a closing: the
        money there may have moved, so every later month stays partial."""
        s = with_second_bank(bank("2025-01", 0, 50_000), bank("2025-02", 50_000, 50_000))
        self.assertEqual(partial_months(s), fx.MONTHS[2:])
        self.assertEqual(c.coverage(s)["2025-05"]["missing"], [OLD])
        self.assertEqual(savings_runway.run(s)["data"]["summary"]["as_of_complete"], "2025-02")


class AHalfYearlyAccount(NoPhases):

    def test_a_statement_covers_the_months_it_spans(self):
        s = with_second_bank(bank("2025-01", 0, 50_000), bank("2025-07", 50_000, 50_000))
        self.assertEqual(c.full_months(s), fx.MONTHS)
        cov = c.coverage(s)
        self.assertEqual(cov["2025-04"]["missing"], [])
        self.assertEqual(cov["2025-04"]["expected"], [fx.MAIN, OLD])

    def test_the_months_before_its_next_statement_is_due_are_covered(self):
        """Six months between linked statements is a half-yearly account: one with no
        activity, since a bank prints a month that had any. Until the next one is due
        (Jan 2026 here) nothing can be missing from it."""
        s = with_second_bank(bank("2025-01", 0, 50_000), bank("2025-07", 50_000, 50_000))
        summary = savings_runway.run(s)["data"]["summary"]
        self.assertEqual(summary["as_of_complete"], "2025-12")

    def test_an_overdue_statement_is_a_hole(self):
        # every three months: Feb and Mar are spanned, May and Jun not yet due, Jul on overdue
        s = with_second_bank(bank("2025-01", 0, 50_000), bank("2025-04", 50_000, 50_000))
        self.assertEqual(partial_months(s), fx.MONTHS[6:])

    def test_a_gap_whose_balances_do_not_meet_is_a_hole(self):
        """A statement that opens away from the last one's closing is not the next
        statement: one in between is missing, and the gap says nothing of a cadence."""
        s = with_second_bank(bank("2025-01", 0, 50_000), bank("2025-07", 40_000, 40_000))
        self.assertEqual(partial_months(s), fx.MONTHS[1:6] + fx.MONTHS[7:])


def on_old(day, amount, month):
    """A row on the second bank account's statement for `month`."""
    return fx.txn(day, amount, "Groceries", "Market", account=OLD, period=f"{OLD} · SGD · {month}")


class StatementsWithRows(NoPhases):
    """Greptile on #308: balances that meet do not by themselves show a statement
    spans the months before it. Its own rows do."""

    def test_a_missing_month_whose_money_in_and_out_were_equal_is_still_a_hole(self):
        # January and March print monthly with activity; February's statement is missing,
        # and whatever moved in it netted to zero, so March opens at January's closing
        s = with_second_bank(bank("2025-01", 0, 50_000), bank("2025-03", 50_000, 45_000))
        s["transactions"] += [on_old("2025-01-10", -10.0, "2025-01"), on_old("2025-03-10", -50.0, "2025-03")]
        self.assertEqual(partial_months(s), ["2025-02"] + fx.MONTHS[3:])

    def test_a_statement_covers_back_to_its_earliest_row(self):
        s = with_second_bank(bank("2025-01", 0, 50_000), bank("2025-03", 50_000, 45_000))
        s["transactions"] += [on_old("2025-02-10", -20.0, "2025-03"), on_old("2025-03-10", -30.0, "2025-03")]
        self.assertNotIn("2025-02", partial_months(s))

    def test_a_first_statement_that_reaches_back_starts_the_account_there(self):
        s = with_second_bank(bank("2025-03", 0, 45_000))
        s["transactions"] += [on_old("2025-01-20", -20.0, "2025-03")]
        self.assertEqual(c.statement_spans(s)[OLD]["from"], "2025-01")

    def test_a_row_posted_on_the_last_days_of_the_month_before_shows_nothing(self):
        s = with_second_bank(bank("2025-01", 0, 50_000), bank("2025-03", 50_000, 45_000))
        s["transactions"] += [on_old("2025-02-27", -20.0, "2025-03"), on_old("2025-03-10", -30.0, "2025-03")]
        self.assertEqual(partial_months(s), ["2025-02"] + fx.MONTHS[3:])

    def test_a_statement_with_activity_says_nothing_of_the_months_after_it(self):
        """Greptile on #308: only an idle (empty) statement shows the account idle, so
        only after one are the months before the next statement due covered."""
        s = with_second_bank(bank("2025-01", 0, 50_000), bank("2025-07", 50_000, 60_000))
        s["transactions"] += [on_old(f"2025-{m:02d}-10", -1.0, "2025-07") for m in range(2, 8)]
        self.assertEqual(partial_months(s), fx.MONTHS[7:])


    def test_a_later_statement_keeps_the_months_an_idle_one_covered(self):
        """Greptile on #39: an idle July statement covers August to December; a November
        statement with activity arriving later does not take August to October back."""
        s = with_second_bank(bank("2025-01", 0, 50_000), bank("2025-07", 50_000, 50_000),
                             bank("2025-11", 50_000, 49_000))
        s["transactions"] += [on_old("2025-11-10", -10.0, "2025-11")]
        self.assertEqual(partial_months(s), ["2025-12"])

class TheCashHeadline(NoPhases):
    """Greptile on #39: savings-runway's complete month is coverage's, and two more."""

    def test_a_statement_with_no_usable_balance_is_not_current_cash(self):
        """B2 prints December in USD with no SGD rate: its November balance would be
        carried and called current, so December is not complete."""
        months = fx.MONTHS
        blocks = [bank(m, 50_000, 50_000, currency="USD") for m in months]
        blocks[-1].update(closing_sgd_minor=None)
        s = with_second_bank(*blocks)
        self.assertEqual(savings_runway.run(s)["data"]["summary"]["as_of_complete"], "2025-11")

    def test_an_idle_account_between_half_yearly_statements_does_not_freeze_it(self):
        s = with_second_bank(bank("2025-01", 0, 50_000), bank("2025-07", 50_000, 50_000))
        self.assertEqual(savings_runway.run(s)["data"]["summary"]["as_of_complete"], "2025-12")

    def test_a_balance_carried_past_its_own_rows_is_not_current_cash(self):
        """Greptile on #39: B2's July statement holds rows from February on, so its
        January balance is stale in June, though July's statement covers June."""
        s = with_second_bank(bank("2025-01", 0, 50_000), bank("2025-07", 50_000, 45_000))
        s["transactions"] += [on_old(f"2025-{m:02d}-10", -10.0, "2025-07") for m in range(2, 8)]
        s["periods"] = [b for b in s["periods"] if b["account_no"] != fx.MAIN or c.period_month(b) <= "2025-06"]
        self.assertEqual(c.coverage(s)["2025-06"]["missing"], [])
        self.assertEqual(savings_runway.run(s)["data"]["summary"]["as_of_complete"], "2025-01")

    def test_a_wallet_month_after_every_bank_closed_is_not_complete(self):
        s = fx.snapshot()
        main = next(b for b in s["periods"] if b["name"] == f"{fx.MAIN} · SGD · 2025-12")
        main.update(closing_minor=0, closing_sgd_minor=0)
        wallet = dict(next(b for b in s["periods"] if b["name"] == f"{fx.WALLET} · NOK · 2025-12"),
                      name=f"{fx.WALLET} · NOK · 2026-01", period_end="2026-01-28")
        s["periods"].append(wallet)
        self.assertEqual(c.coverage(s)["2026-01"]["expected"], [])
        self.assertTrue(c.coverage(s)["2026-01"]["partial"])
        self.assertEqual(savings_runway.run(s)["data"]["summary"]["as_of_complete"], "2025-12")


class WalletOnlyMonths(NoPhases):

    def test_months_only_a_wallet_covers_are_partial(self):
        """The bank's statements start in June and the wallet's in March: March to May
        hold no income because no bank statement covers them, not because none came."""
        s = fx.snapshot()
        s["periods"] = [b for b in s["periods"]
                        if not (b["account_no"] == fx.MAIN and c.period_month(b) < "2025-06")]
        s["transactions"] = [r for r in s["transactions"] if r["account_no"] != fx.MAIN or r["month"] >= "2025-06"] + [
            fx.txn("2025-03-20", -30.0, "Eating out", "Cafe Z", account=fx.WALLET, currency="NOK", amount=-230.0)]
        self.assertEqual(c.month_range(s)[0], "2025-03")
        self.assertEqual(partial_months(s), ["2025-03", "2025-04", "2025-05"])
        self.assertEqual(c.full_months(s), fx.MONTHS[5:])
        self.assertEqual(monthly_cashflow.run(s)["data"]["totals"]["months"], 7)


class APartialCurrentMonth(NoPhases):
    """January has begun: two card rows and no bank statement yet."""

    def setUp(self):
        super().setUp()
        self.base = fx.snapshot()
        self.s = copy.deepcopy(self.base)
        self.s["transactions"] += [fx.txn("2026-01-02", -12.0, "Eating out", "Cafe A"),
                                   fx.txn("2026-01-03", -60.0, "Groceries", "Market")]
        self.assertEqual(partial_months(self.s), ["2026-01"])

    def category(self, res, name):
        return next(x for x in res["data"]["categories"] if x["category"] == name)

    def test_the_category_trend_compares_the_last_three_full_months(self):
        res = category_breakdown.run(self.s)
        self.assertEqual(res["params"]["trend_window"],
                         {"recent": fx.MONTHS[-3:], "prior": fx.MONTHS[-9:-3]})
        rent = self.category(res, "Rent")
        self.assertEqual(rent["last3_per_month_minor"], 80_000)
        self.assertEqual(rent["change_minor"], 0)
        self.assertFalse([f for f in res["findings"] if f.startswith("Rent fell")])

    def test_the_change_is_a_fraction_under_a_share_name(self):
        """AGENTS.md: the column name decides the scale, and `_pct` is a percent."""
        groceries = self.category(category_breakdown.run(self.s), "Groceries")
        self.assertNotIn("change_pct", groceries)
        self.assertEqual(groceries["change_share"], 0.0)

    def test_phase_averages_divide_by_full_months(self):
        before = monthly_cashflow.run(self.base)["data"]["phases"][-1]
        after = monthly_cashflow.run(self.s)["data"]["phases"][-1]
        self.assertEqual((after["months"], after["full_months"]), (13, 12))
        # the phase total keeps every month's money; the average is the full months'
        self.assertEqual(after["spend_minor"], before["spend_minor"] + 7_200)
        for key in ("income_per_month_minor", "spend_per_month_minor", "net_per_month_minor",
                    "investing_per_month_minor", "net_after_investing_per_month_minor"):
            self.assertEqual(after[key], before[key], key)

    def test_category_phase_averages_divide_by_full_months(self):
        key = f"{c.phase_key(c.latest_phase())}_per_month_minor"
        for name in ("Rent", "Groceries"):
            self.assertEqual(self.category(category_breakdown.run(self.s), name)[key],
                             self.category(category_breakdown.run(self.base), name)[key], name)
        phase = category_breakdown.run(self.s)["data"]["phases"][-1]
        self.assertEqual(phase["per_month_minor"], category_breakdown.run(self.base)["data"]["phases"][-1]["per_month_minor"])

    def test_the_forward_view_phase_rate_is_the_same_figure(self):
        self.assertEqual(forward_view.phase_rate(self.s), forward_view.phase_rate(self.base))
        self.assertEqual(forward_view.phase_rate(self.s)[0],
                         monthly_cashflow.run(self.s)["data"]["phases"][-1]["spend_per_month_minor"])


class ASavedResultFromBeforeTheRename(unittest.TestCase):
    """Greptile on #308: a category-breakdown result saved before `change_pct` became
    `change_share` is still served until the stats run again; it must not read 0.5
    as half a per cent."""

    def test_reads_the_old_field_under_its_new_name(self):
        from finance import results_cache
        body = {"data": {"categories": [{"category": "Rent", "change_pct": 0.5}]}}
        row = results_cache.current_names("category-breakdown", body)["data"]["categories"][0]
        self.assertEqual(row, {"category": "Rent", "change_share": 0.5})
        other = {"data": {"rows": [{"gain_pct": 18.7}]}}
        self.assertEqual(results_cache.current_names("portfolio", other), other)

    def test_the_cache_serves_it_renamed(self):
        import json
        import tempfile
        from pathlib import Path
        from finance import results_cache
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "results").mkdir()
            old = {"id": "category-breakdown", "chart": {"unit": "sgd_minor"},
                   "data": {"categories": [{"category": "Rent", "change_pct": 0.25}]}}
            (root / "results" / "category-breakdown-2026-10-01.json").write_text(json.dumps(old))
            (root / "latest.json").write_text(json.dumps(
                {"results": {"category-breakdown": "category-breakdown-2026-10-01.json"}}))
            got, _ = results_cache.Cache(root).results_all()
        self.assertEqual(got["category-breakdown"]["data"]["categories"][0]["change_share"], 0.25)


if __name__ == "__main__":
    unittest.main()
