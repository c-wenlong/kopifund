"""G13: the figures the record already knows, offered beside the Plan and Net
worth inputs (server/finance/suggestions.py, GET /api/suggestions).

Every payer, amount and date here is invented."""
import isolate  # noqa: F401  first: no test writes the real record or cache
import json
import os
import shutil
import tempfile
import threading
import urllib.error
import unittest
import urllib.request
from datetime import date
from pathlib import Path
from unittest import mock

import serve
from finance import db, suggestions as sug
import support
from test_db import row

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "analytics"

# 30 in 2026: the 20 per cent employee band, 17 per cent from the employer
BIRTH = date(1996, 3, 15)
TODAY = date(2026, 10, 18)

CATEGORIES = [
    {"name": "Transport", "bucket": "Spend", "group": "Variable", "need_class": "Need", "sort_order": 100},
    {"name": "Salary", "bucket": "Income", "group": "Fixed", "need_class": "Income", "sort_order": 10},
    {"name": "Freelance income", "bucket": "Income", "group": "Variable", "need_class": "Income", "sort_order": 11},
    {"name": "Refunds and cashback", "bucket": "Income", "group": "Variable", "need_class": "Income", "sort_order": 12},
]


def month_days(first, n):
    """The 28th of n months from `first` (YYYY-MM), as ISO dates."""
    y, m = int(first[:4]), int(first[5:])
    out = []
    for i in range(n):
        out.append(f"{y + (m - 1 + i) // 12:04d}-{(m - 1 + i) % 12 + 1:02d}-28")
    return out


def credit(key, day, amount, payee, category="Salary"):
    return row(key, amount, date=day, direction="In", payee=payee, category=category, merchant="", bank_txn_type="FAST Payment")


def record(*rows, path=":memory:"):
    return support.build_record([row("t0", "-5.00", date="2026-09-01"), *rows], path=path, plan={},
                                user=support.user_tables(categories=CATEGORIES),
                                payees=support.payees(sorted({r["payee"] for r in rows if r["payee"]}), kind="Employer"))


def pay_rows(payee, first, n, amount, category="Salary", extra=None):
    """n monthly credits, the amounts overridden by index in `extra`."""
    extra = extra or {}
    return [credit(f"{payee}-{i}", day, extra.get(i, amount), payee, category) for i, day in enumerate(month_days(first, n))]


class Income(unittest.TestCase):
    def conn(self, *rows):
        c = record(*rows)
        self.addCleanup(c.close)
        return c

    def test_pay_still_arriving_is_offered_with_the_employee_cpf_added_back(self):
        c = self.conn(*pay_rows("Acme Pte", "2026-03", 7, "4000.00"))
        [pay] = sug.income(c, BIRTH)
        self.assertEqual((pay["name"], pay["type"], pay["from"], pay["last"]), ("Acme Pte", "employment", "2026-03-28", "2026-09-28"))
        self.assertEqual((pay["take_home"], pay["gross"], pay["cpf"], pay["basis"]), (400_000, 500_000, True, "gross"))
        self.assertEqual((pay["bonus_months"], pay["bonus_month"]), (None, None))

    def test_without_a_date_of_birth_the_take_home_is_offered_as_it_stands_on_a_row_with_no_cpf(self):
        c = self.conn(*pay_rows("Acme Pte", "2026-03", 7, "4000.00"))
        [pay] = sug.income(c, None)
        self.assertEqual((pay["take_home"], pay["gross"], pay["cpf"], pay["basis"]), (400_000, 400_000, False, "take_home"))

    def test_the_cpf_ceiling_is_respected_when_adding_it_back(self):
        # at the 20 per cent band, the employee's CPF on S$8,000 is S$1,600, so S$8,000 take-home is S$9,600 gross
        self.assertEqual(sug.gross_from_take_home(400_000, "2026-09", BIRTH), 500_000)
        self.assertEqual(sug.gross_from_take_home(800_000, "2026-09", BIRTH), 960_000)
        # the other way round: that gross leaves that take-home
        from finance import cpf_rules as rules
        self.assertEqual(960_000 - rules.employee_contribution(960_000, "2026-09", BIRTH), 800_000)

    def test_a_yearly_spike_is_a_bonus_month(self):
        # December pays three times the usual: two months of pay on top
        c = self.conn(*pay_rows("Acme Pte", "2025-10", 12, "4000.00", extra={2: "12000.00"}))
        [pay] = sug.income(c, BIRTH)
        self.assertEqual((pay["bonus_months"], pay["bonus_month"]), (2.0, 12))
        self.assertEqual(pay["take_home"], 400_000)         # the typical credit leaves the spike out

    def test_a_bonus_is_counted_in_gross_pay_where_the_cpf_ceilings_apply(self):
        # S$9,600 gross leaves S$8,000 a month. A three month bonus (S$28,800) pays CPF only on the S$6,000
        # the annual ceiling leaves room for, so it arrives as S$27,600 on top of the month's S$8,000
        c = self.conn(*pay_rows("Acme Pte", "2025-10", 12, "8000.00", extra={2: "35600.00"}))
        [pay] = sug.income(c, BIRTH)
        self.assertEqual((pay["gross"], pay["bonus_months"], pay["bonus_month"]), (960_000, 3.0, 12))

    def test_a_bonus_on_take_home_is_counted_against_the_take_home_the_row_will_hold(self):
        c = self.conn(*pay_rows("Acme Pte", "2025-10", 12, "4000.00", extra={2: "12000.00"}))
        [pay] = sug.income(c, None)
        self.assertEqual((pay["gross"], pay["basis"], pay["bonus_months"]), (400_000, "take_home", 2.0))

    def test_spikes_in_different_months_are_not_a_yearly_bonus(self):
        c = self.conn(*pay_rows("Acme Pte", "2025-10", 12, "4000.00", extra={2: "12000.00", 7: "12000.00"}))
        [pay] = sug.income(c, BIRTH)
        self.assertEqual((pay["bonus_months"], pay["bonus_month"]), (None, None))

    def test_the_typical_credit_follows_the_latest_months(self):
        c = self.conn(*pay_rows("Acme Pte", "2026-01", 9, "3000.00", extra={6: "3600.00", 7: "3600.00", 8: "3600.00"}))
        [pay] = sug.income(c, BIRTH)
        self.assertEqual(pay["take_home"], 360_000)

    def test_a_payer_that_stopped_or_paid_once_is_not_offered(self):
        c = self.conn(*pay_rows("Old Co", "2025-10", 4, "3000.00"),         # last credit in January, record runs to September
                      *pay_rows("New Co", "2026-09", 1, "3000.00"),         # one month only
                      *pay_rows("Acme Pte", "2026-03", 7, "4000.00"))
        self.assertEqual([p["name"] for p in sug.income(c, BIRTH)], ["Acme Pte"])

    def test_refunds_and_other_non_pay_credits_are_not_pay(self):
        c = self.conn(*pay_rows("Shop", "2026-03", 7, "30.00", category="Refunds and cashback"))
        self.assertEqual(sug.income(c, BIRTH), [])

    def test_other_income_is_side_income_paid_as_it_arrives(self):
        c = self.conn(*pay_rows("Client Ltd", "2026-03", 7, "900.00", category="Freelance income"))
        [pay] = sug.income(c, BIRTH)
        self.assertEqual((pay["type"], pay["basis"], pay["cpf"], pay["gross"], pay["bonus_months"]), ("side", "side", False, 90_000, None))

    def test_a_credit_with_no_payee_is_named_by_who_sent_it_else_by_its_category(self):
        sent = [dict(r, payee="", counterparty="SENDER CO", txn_key=f"s{i}") for i, r in enumerate(pay_rows("x", "2026-07", 3, "2000.00"))]
        anon = [dict(r, payee="", txn_key=f"a{i}") for i, r in enumerate(pay_rows("y", "2026-08", 2, "1000.00", category="Freelance income"))]
        c = self.conn(*sent, *anon)
        self.assertEqual(sorted(p["name"] for p in sug.income(c, BIRTH)), ["Freelance income", "SENDER CO"])

    def test_an_empty_record_offers_nothing(self):
        c = db.connect(":memory:")
        self.addCleanup(c.close)
        self.assertEqual(sug.income(c, BIRTH), [])


def deposits(first, n, cents, currency="SGD"):
    """G20: a deposit's amount is the portfolio stat's own cents, `amount_minor`."""
    return [{"date": f"{d[:7]}-05", "amount_minor": cents, "currency": currency, "matched": None}
            for d in month_days(first, n)]


class Investing(unittest.TestCase):
    def results(self, rows, periods=()):
        return {"portfolio": {"data": {"deposits": rows, "periods": list(periods)}}}

    def test_the_monthly_amount_is_the_middle_of_the_last_twelve_months_with_a_deposit(self):
        rows = deposits("2025-01", 20, 100_000)
        rows[3]["amount_minor"] = 500_000                            # a one-off, outside the window
        rows[-1]["amount_minor"] = 120_000
        out = sug.investing(self.results(rows))
        self.assertEqual(out, {"per_month": 100_000, "months": 12, "from": "2025-09", "to": "2026-08"})

    def test_two_deposits_in_a_month_add_up(self):
        rows = deposits("2026-01", 6, 50_000) + [{"date": "2026-03-20", "amount_minor": 50_000, "currency": "SGD",
                                                  "matched": None}]
        self.assertEqual(sug.investing(self.results(rows))["per_month"], 50_000)

    def test_a_deposit_in_the_base_currency_goes_to_sgd_at_its_period_s_rate(self):
        periods = [{"period_from": "2026-01-01", "period_to": "2026-12-31", "sgd_per_base": 1.25}]
        rows = deposits("2026-01", 6, 80_000, currency="USD") + deposits("2026-07", 1, 80_000, currency="EUR")
        out = sug.investing(self.results(rows, periods))
        self.assertEqual((out["per_month"], out["months"]), (100_000, 6))          # the euro deposit is left out

    def test_deposits_that_stopped_before_the_latest_statement_are_not_offered_as_current(self):
        rows = deposits("2024-01", 12, 100_000)
        periods = [{"period_from": "2024-01-01", "period_to": "2024-12-31", "sgd_per_base": 1.25},
                   {"period_from": "2025-01-01", "period_to": "2026-09-30", "sgd_per_base": 1.25}]
        self.assertIsNone(sug.investing(self.results(rows, periods)))
        # the same deposits are current while the newest statement ends with them
        self.assertEqual(sug.investing(self.results(rows, periods[:1]))["per_month"], 100_000)

    def test_the_portfolio_stat_s_cents_are_read_and_its_old_dollars_are_not(self):
        """G20: a deposit's own cents (`amount_minor`) and its period's rate
        scaled by 10^9 (`fx_to_sgd_e9`), converted by the record's rule. A
        deposit in dollars under the bare `amount` is from before G20 and is
        not read."""
        cents = deposits("2026-01", 5, 80_000, currency="USD") + deposits("2026-06", 3, 100_000)
        dollars = [{**{k: v for k, v in d.items() if k != "amount_minor"}, "amount": d["amount_minor"] / 100}
                   for d in cents]
        old = [{"period_from": "2026-01-01", "period_to": "2026-12-31", "sgd_per_base": 1.25}]
        new = [{"period_from": "2026-01-01", "period_to": "2026-12-31", "sgd_per_base": 1.25,
                "fx_to_sgd_e9": 1_250_000_000}]
        # the rate reads the same in either form: it is a rate, not money
        self.assertEqual(sug.investing(self.results(cents, new)), sug.investing(self.results(cents, old)))
        self.assertEqual(sug.investing(self.results(cents, new))["per_month"], 100_000)
        self.assertIsNone(sug.investing(self.results(dollars, new)))

    def test_a_us_dollar_deposit_rounds_half_up_to_the_cent(self):
        rows = deposits("2026-01", 3, 333, currency="USD")      # 3.33 USD at 1.25 is 4.1625 SGD
        periods = [{"period_from": "2026-01-01", "period_to": "2026-12-31", "fx_to_sgd_e9": 1_250_000_000}]
        self.assertEqual(sug.investing(self.results(rows, periods))["per_month"], 416)

    def test_fewer_than_three_months_of_deposits_is_not_a_habit(self):
        self.assertIsNone(sug.investing(self.results(deposits("2026-01", 2, 100_000))))
        self.assertIsNone(sug.investing(self.results([])))
        self.assertIsNone(sug.investing({}))


class RateAndStanding(unittest.TestCase):
    def test_the_employer_rate_follows_the_age_band(self):
        self.assertEqual(sug.employer_cpf_rate(BIRTH, TODAY), 0.17)
        self.assertEqual(sug.employer_cpf_rate(date(1968, 5, 1), TODAY), 0.16)       # 58: 34 per cent less 18
        self.assertEqual(sug.employer_cpf_rate(date(1950, 5, 1), TODAY), 0.075)      # 76: 12.5 per cent less 5
        self.assertIsNone(sug.employer_cpf_rate(None, TODAY))

    def series(self, category, cents, stopped=False, payee="Someone"):
        """G20: the detector's monthly cost in cents, `monthly_cost_minor`."""
        return {"payee": payee, "category": category, "monthly_cost_minor": cents, "stopped": stopped}

    def test_insurance_and_repayments_are_the_detector_s_active_series_in_those_categories(self):
        results = {"recurring-detector": {"data": {"series": [
            self.series("Insurance", 12_050), self.series("Insurance", 8_000),
            self.series("Insurance", 99_900, stopped=True),
            # G16: a paused series has gone quiet, so it is no standing cost either
            {**self.series("Insurance", 55_500), "paused": True},
            self.series("Loan repayments", 45_000), self.series("Rent", 180_000),
        ]}}}
        with mock.patch.object(sug.settings, "text_setting", return_value=""):
            got = sug.suggestions(None, results, BIRTH.isoformat(), TODAY)
        self.assertEqual(got["insurance"], {"per_month": 20_050, "series": 2})
        self.assertEqual(got["repayments"], {"per_month": 45_000, "series": 1})

    def test_the_result_it_reads_is_the_detector_s(self):
        """G19: finance names the stat's id rather than importing the stat."""
        from analytics.stats import recurring_detector
        self.assertEqual(sug.RECURRING_ID, recurring_detector.ID)

    def test_the_configured_repayment_payee_counts_in_any_category(self):
        results = {"recurring-detector": {"data": {"series": [
            self.series("Person to person", 30_000, payee="Lender A"),
            self.series("Person to person", 7_000, payee="Friend B"),
        ]}}}
        settings = {"repayment_payee": "Lender A", "repayment_category": "Person to person"}
        with mock.patch.object(sug.settings, "text_setting", side_effect=lambda k: settings.get(k, "")):
            got = sug.suggestions(None, results, None, TODAY)
        self.assertEqual(got["repayments"], {"per_month": 30_000, "series": 1})
        self.assertIsNone(got["insurance"])
        self.assertIsNone(got["cpf_employer_rate"])

    def test_nothing_known_is_all_empty(self):
        got = sug.suggestions(None, {}, None, TODAY)
        self.assertEqual(got, {"income": [], "investing": None, "cpf_employer_rate": None, "insurance": None, "repayments": None})


class Endpoint(unittest.TestCase):
    """GET /api/suggestions over a temporary data folder holding the fixtures as its cache."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        shutil.copytree(FIXTURES, self.root / "cache")
        env = mock.patch.dict(os.environ, {"KOPIFUND_DATA": str(self.root), "KOPIFUND_DB": str(self.root / "kopifund.db"),
                                           "KOPIFUND_CACHE": str(self.root / "cache")})
        env.start()
        self.addCleanup(env.stop)

    def test_without_a_record_or_a_date_of_birth_there_is_nothing_to_offer_from_the_record(self):
        out = support.answer("/api/suggestions", {})
        self.assertEqual((out["income"], out["cpf_employer_rate"]), ([], None))
        self.assertFalse((self.root / "kopifund.db").exists())

    def test_it_reads_the_record_the_profile_and_the_stat_results(self):
        record(*pay_rows("Acme Pte", "2026-03", 7, "4000.00"), path=str(self.root / "kopifund.db")).close()
        (self.root / "profile.json").write_text(json.dumps({"birth_date": BIRTH.isoformat()}))
        with support.today_is(TODAY):
            out = support.answer("/api/suggestions", {})
        self.assertEqual([(p["name"], p["gross"]) for p in out["income"]], [("Acme Pte", 500_000)])
        self.assertEqual(out["cpf_employer_rate"], sug.employer_cpf_rate(BIRTH, TODAY))
        self.assertGreater(out["investing"]["per_month"], 0)              # the fixtures' brokerage statement deposits

    def test_it_is_cached_by_the_data_version_and_moves_with_the_profile(self):
        server = serve.make_server(0, self.root)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        url = f"http://127.0.0.1:{server.server_address[1]}/api/suggestions"

        def get(etag=None):
            req = urllib.request.Request(url, headers={"If-None-Match": etag} if etag else {})
            try:
                with urllib.request.urlopen(req) as r:
                    return r.status, r.headers["ETag"], json.loads(r.read())
            except urllib.error.HTTPError as e:
                with e:
                    return e.code, e.headers["ETag"], None

        status, etag, body = get()
        self.assertEqual((status, body["cpf_employer_rate"]), (200, None))
        self.assertRegex(etag, r'^"[0-9a-f]{20}"$')
        self.assertEqual(get(etag)[0], 304)
        (self.root / "profile.json").write_text(json.dumps({"birth_date": BIRTH.isoformat()}))
        status, new, body = get(etag)
        self.assertEqual(status, 200)
        self.assertNotEqual(new, etag)
        self.assertIsNotNone(body["cpf_employer_rate"])


if __name__ == "__main__":
    unittest.main()
