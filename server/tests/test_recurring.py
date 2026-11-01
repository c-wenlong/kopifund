"""server/finance/recurring.py: the Recurring page and this month's coming-up list."""
import isolate  # noqa: F401  first: no test writes the real record or cache
import unittest
from datetime import date, timedelta

from finance import api, db, home, recurring
from finance.money import to_cents
from test_db import SNAP, block, parsed, row

# invented payees and amounts; every date is in 2026 unless it says otherwise
PAYEES = ["Gym", "Phone", "Stream", "Landlord", "Broker", "Cleaner", "Insurer", "Charity", "Club", "Magazine"]
CATEGORIES = SNAP["categories"] + [
    {"name": "Investments", "bucket": "Transfer", "group": "Fixed", "need_class": "Investment", "sort_order": 200}]


def record(*rows):
    conn = db.connect(":memory:")
    db.import_user_tables(conn, {**{k: [dict(x) for x in v] for k, v in SNAP.items()},
                                 "categories": [dict(c) for c in CATEGORIES]})
    keyed = [row(f"k{i}", amount, date=day, payee=payee, merchant=payee.upper(),
                 category="Investments" if payee == "Broker" else "Transport")
             for i, (payee, day, amount) in enumerate(rows)]
    db.load_parsed(conn, parsed([block()], keyed),
                   payees=[{"name": p, "kind": "Merchant", "country": "SGP"} for p in PAYEES])
    return conn


def series(payee, median, last_seen, cadence="monthly", **kw):
    days = {"weekly": 7.0, "monthly": 30.44, "quarterly": 91.3, "yearly": 365.0}[cadence]
    return {"payee": payee, "grouped_by": "payee", "category": "Bills", "bucket": "Spend",
            "cadence": cadence, "cadence_days": days, "occurrences": 4, "first_seen": "2026-01-01",
            # G20: the detector writes cents
            "last_seen": last_seen, "median_amount_minor": to_cents(median), "avg_amount_minor": to_cents(median),
            "monthly_cost_minor": to_cents(median), "amount_varies": False, "flagged_recurring": True, **kw}


def results(active=(), stopped=(), flagged=()):
    return {"recurring-detector": {"generated_at": "2026-09-20T08:00:00", "data": {
        "active": list(active), "stopped": list(stopped),
        "flag_mismatches": {"flagged_not_detected": list(flagged), "detected_not_flagged": []}}}}


def monthly(payee, amount, *days):
    return [(payee, d, amount) for d in days]


# the record ends on 20 September, so September is "this month" and the 20th is today
ROWS = [
    *monthly("Gym", "-50.00", "2026-06-05", "2026-07-05", "2026-08-05", "2026-09-04"),
    *monthly("Phone", "-20.00", "2026-06-02", "2026-07-02", "2026-08-02"),
    *monthly("Stream", "-15.00", "2026-06-25", "2026-07-25", "2026-08-25"),
    *monthly("Landlord", "-1000.00", "2026-06-01", "2026-07-01", "2026-08-01"),
    ("Landlord", "2026-09-01", "-1300.00"),
    *monthly("Broker", "-500.00", "2026-06-10", "2026-07-10", "2026-08-10", "2026-09-10"),
    *monthly("Cleaner", "-60.00", "2026-08-15", "2026-08-22", "2026-08-29", "2026-09-05", "2026-09-19"),
    *monthly("Insurer", "-300.00", "2025-12-15", "2026-03-15", "2026-06-15"),
    ("Club", "2026-09-20", "-12.00"),
]
ACTIVE = [series("Gym", 50.0, "2026-09-04"), series("Phone", 20.0, "2026-08-02"),
          series("Stream", 15.0, "2026-08-25"), series("Landlord", 1000.0, "2026-08-01"),
          series("Broker", 500.0, "2026-09-10", bucket="Transfer", category="Investments"),
          series("Cleaner", 60.0, "2026-09-19", cadence="weekly"),
          series("Insurer", 300.0, "2026-06-15", cadence="quarterly"),
          series("Yearly fee", 80.0, "2026-02-01", cadence="yearly")]


def by_payee(rows):
    out = {}
    for r in rows:
        out.setdefault(r["payee"], []).append(r)
    return out


class ComingUp(unittest.TestCase):
    def setUp(self):
        self.conn = record(*ROWS)
        self.addCleanup(self.conn.close)
        self.up = recurring.coming_up(self.conn, results(ACTIVE), today=date(2026, 10, 16))
        self.rows = by_payee(self.up["rows"])

    def test_the_month_is_the_record_s_last_when_it_ends_before_today(self):
        self.assertEqual((self.up["month"], self.up["as_of"]), ("2026-09", "2026-09-20"))

    def test_paid_carries_the_day_it_was_paid(self):
        gym = self.rows["Gym"][0]
        self.assertEqual((gym["status"], gym["expected"], gym["paid_on"], gym["paid_amount"]),
                         ("paid", "2026-09-05", "2026-09-04", 5000))

    def test_late_once_the_cadence_band_has_passed_with_nothing(self):
        phone = self.rows["Phone"][0]
        self.assertEqual((phone["status"], phone["expected"], phone["paid_on"]), ("late", "2026-09-02", None))

    def test_due_before_the_expected_day(self):
        stream = self.rows["Stream"][0]
        self.assertEqual((stream["status"], stream["expected"], stream["amount"]), ("due", "2026-09-25", 1500))
        self.assertTrue(stream["estimated"])

    def test_a_different_amount_shows_both(self):
        rent = self.rows["Landlord"][0]
        self.assertEqual((rent["status"], rent["paid_amount"], rent["amount"]), ("different", 130000, 100000))
        self.assertEqual((rent["usual_low"], rent["usual_high"]), (75000, 125000))

    def test_where_one_buys_in_the_band_is_the_detector_s_tight_one(self):
        self.assertEqual(recurring.usual_range(series("Kopi", 10.0, "2026-09-01", category="Transport"), []),
                         (950, 1050))

    def test_weekly_has_a_row_per_week(self):
        # G16: 12 September was skipped (19 September came), so it is history, not a late payment
        self.assertEqual([(r["expected"], r["status"]) for r in self.rows["Cleaner"]],
                         [("2026-09-05", "paid"), ("2026-09-19", "paid"), ("2026-09-26", "due")])

    def test_quarterly_is_due_until_its_band_runs_out(self):
        self.assertEqual([(r["expected"], r["status"]) for r in self.rows["Insurer"]], [("2026-09-15", "due")])

    def test_a_series_not_expected_this_month_is_left_out(self):
        self.assertNotIn("Yearly fee", self.rows)

    def test_sorted_by_expected_date(self):
        days = [r["expected"] for r in self.up["rows"]]
        self.assertEqual(days, sorted(days))

    def test_investing_is_labelled_and_kept_out_of_the_spending_total(self):
        self.assertTrue(self.rows["Broker"][0]["investing"])
        t = self.up["totals"]
        # Gym 50 + Phone 20 + Stream 15 + Landlord 1300 paid + Cleaner 3 x 60 (one week skipped) + Insurer 300
        self.assertEqual(t["committed"], 5000 + 2000 + 1500 + 130000 + 3 * 6000 + 30000)
        self.assertEqual(t["paid"], 5000 + 130000 + 2 * 6000)
        self.assertEqual((t["investing"], t["investing_paid"]), (50000, 50000))

    def test_every_amount_is_integer_cents(self):
        for r in self.up["rows"]:
            for k in ("amount", "paid_amount", "usual_low", "usual_high"):
                self.assertTrue(r[k] is None or isinstance(r[k], int), (k, r[k]))

    def test_a_varying_bill_is_judged_against_its_last_charges(self):
        conn = record(*monthly("Phone", "-30.00", "2026-05-02", "2026-06-02"),
                      *monthly("Phone", "-55.00", "2026-07-02", "2026-08-02"), ("Phone", "2026-09-02", "-90.00"))
        s = series("Phone", 42.5, "2026-09-02", amount_varies=True)
        up = recurring.coming_up(conn, results([s]), today=date(2026, 9, 30))
        self.assertEqual([(r["status"], r["usual_low"], r["usual_high"], r["paid_amount"]) for r in up["rows"]],
                         [("different", 3000, 5500, 9000)])

    def test_a_small_charge_the_day_before_does_not_swallow_the_payment(self):
        conn = record(*monthly("Gym", "-50.00", "2026-06-01", "2026-07-01", "2026-08-01", "2026-09-01"),
                      ("Gym", "2026-08-31", "-5.00"), ("Club", "2026-09-20", "-12.00"))
        up = recurring.coming_up(conn, results([series("Gym", 50.0, "2026-09-01")]), today=date(2026, 9, 30))
        self.assertEqual([(r["expected"], r["status"], r["paid_on"], r["paid_amount"]) for r in up["rows"]],
                         [("2026-09-01", "paid", "2026-09-01", 5000)])
        self.assertEqual(up["totals"]["paid"], 5000)

    def test_an_odd_charge_last_month_does_not_hide_this_month_s_bill(self):
        conn = record(*monthly("Gym", "-50.00", "2026-06-01", "2026-07-01", "2026-08-01"),
                      ("Gym", "2026-08-31", "-5.00"), ("Club", "2026-09-10", "-12.00"))
        up = recurring.coming_up(conn, results([series("Gym", 50.0, "2026-08-01")]), today=date(2026, 9, 30))
        self.assertEqual([(r["expected"], r["status"], r["paid_on"]) for r in up["rows"]],
                         [("2026-09-01", "late", None)])
        self.assertEqual(up["totals"]["committed"], 5000)

    def test_a_new_price_paid_on_time_is_a_different_amount(self):
        conn = record(*monthly("Gym", "-50.00", "2026-06-01", "2026-07-01", "2026-08-01"),
                      ("Gym", "2026-09-02", "-80.00"), ("Club", "2026-09-10", "-12.00"))
        up = recurring.coming_up(conn, results([series("Gym", 50.0, "2026-08-01")]), today=date(2026, 9, 30))
        self.assertEqual([(r["expected"], r["status"], r["paid_amount"]) for r in up["rows"]],
                         [("2026-09-01", "different", 8000)])

    def test_a_renamed_category_keeps_the_tight_band(self):
        conn = record(*monthly("Club", "-10.00", "2026-06-03", "2026-07-03", "2026-08-03"),
                      ("Club", "2026-09-03", "-11.00"))
        conn.execute("UPDATE category SET name = 'Rides' WHERE name = 'Transport'")
        conn.execute("""INSERT INTO category_alias(name, category_id, how)
                        SELECT 'Transport', id, 'rename' FROM category WHERE name = 'Rides'""")
        s = series("Club", 10.0, "2026-09-03", category="Rides")
        up = recurring.coming_up(conn, results([s]), today=date(2026, 9, 30))
        self.assertEqual([(r["status"], r["usual_low"], r["usual_high"]) for r in up["rows"]],
                         [("different", 950, 1050)])


class MonthBoundaries(unittest.TestCase):
    def test_a_payment_due_on_the_31st_paid_on_the_1st_counts_for_the_month_it_arrived(self):
        conn = record(*monthly("Charity", "-18.00", "2026-05-31", "2026-06-30", "2026-07-31", "2026-09-01"),
                      ("Club", "2026-09-20", "-12.00"))
        up = recurring.coming_up(conn, results([series("Charity", 18.0, "2026-09-01")]), today=date(2026, 9, 30))
        # G16: and September's own, due on the 30th, is still to come
        self.assertEqual([(r["expected"], r["status"], r["paid_on"]) for r in up["rows"]],
                         [("2026-08-31", "paid", "2026-09-01"), ("2026-09-30", "due", None)])
        august = recurring.coming_up(conn, results([series("Charity", 18.0, "2026-09-01")]), today=date(2026, 8, 31))
        self.assertEqual([(r["expected"], r["status"]) for r in august["rows"]], [("2026-08-31", "due")])

    def test_a_payment_after_the_month_ends_is_not_this_month_s(self):
        conn = record(*monthly("Charity", "-18.00", "2026-07-30", "2026-08-30", "2026-10-01"))
        s = [series("Charity", 18.0, "2026-10-01")]
        september = recurring.coming_up(conn, results(s), today=date(2026, 9, 30))
        self.assertEqual([(r["expected"], r["status"]) for r in september["rows"]], [("2026-09-30", "due")])
        october = recurring.coming_up(conn, results(s), today=date(2026, 10, 5))
        # G16: paying September's a day late does not move October's off the 30th
        self.assertEqual([(r["expected"], r["status"], r["paid_on"]) for r in october["rows"]],
                         [("2026-09-30", "paid", "2026-10-01"), ("2026-10-30", "due", None)])

    def test_the_31st_steps_to_the_end_of_a_shorter_month(self):
        self.assertEqual(recurring.step(date(2026, 1, 31), "monthly"), date(2026, 2, 28))
        self.assertEqual(recurring.step(date(2025, 11, 30), "quarterly"), date(2026, 2, 28))
        self.assertEqual(recurring.step(date(2025, 12, 29), "weekly"), date(2026, 1, 5))
        self.assertEqual(recurring.step(date(2025, 12, 15), "yearly"), date(2026, 12, 15))

    def test_quarterly_turns_late_after_its_band(self):
        conn = record(*monthly("Insurer", "-300.00", "2025-12-15", "2026-03-15", "2026-06-15"),
                      ("Club", "2026-09-28", "-12.00"))
        up = recurring.coming_up(conn, results([series("Insurer", 300.0, "2026-06-15", cadence="quarterly")]),
                                 today=date(2026, 10, 1))
        self.assertEqual([(r["expected"], r["status"]) for r in up["rows"]], [("2026-09-15", "late")])


CARD = {"account": "Card", "account_no": "222-22222-2", "period": "222-22222-2 · SGD · 2026-10",
        "source": "statements/card/2026/2026-10.pdf"}


def two_accounts(bank_end, card_end, bank_rows, card_rows):
    """A record with a bank account whose statements run to `bank_end` and a card whose run to `card_end`."""
    conn = db.connect(":memory:")
    db.import_user_tables(conn, {**{k: [dict(x) for x in v] for k, v in SNAP.items()},
                                 "categories": [dict(c) for c in CATEGORIES]})
    keyed = [row(f"b{i}", amount, date=day, payee=payee, merchant=payee.upper(), category="Transport")
             for i, (payee, day, amount) in enumerate(bank_rows)]
    keyed += [row(f"c{i}", amount, date=day, payee=payee, merchant=payee.upper(), category="Transport", **CARD)
              for i, (payee, day, amount) in enumerate(card_rows)]
    blocks = [block(period_end=bank_end), block(period_end=card_end, **CARD)]
    db.load_parsed(conn, parsed(blocks, keyed),
                   payees=[{"name": p, "kind": "Merchant", "country": "SGP"} for p in PAYEES])
    return conn


def statuses(up, payee):
    return [(r["expected"], r["status"], r["paid_on"]) for r in up["rows"] if r["payee"] == payee]


class Schedule(unittest.TestCase):
    """G16: each series has an anchor day, every payment answers the expected day nearest it, and lateness
    counts from the unpaid expected day, not from a schedule moved along by a missing payment."""

    def test_a_missed_month_stays_late_and_the_next_is_judged_by_its_own_day(self):
        # P2: monthly on the 28th, last paid 28 August, nothing in September
        conn = record(*monthly("Stream", "-15.00", "2026-06-28", "2026-07-28", "2026-08-28"), ("Club", "2026-11-05", "-12.00"))
        res = results([series("Stream", 15.0, "2026-08-28")])
        for today, october in ((date(2026, 10, 3), "due"), (date(2026, 10, 30), "due"), (date(2026, 11, 3), "late")):
            with self.subTest(today=today):
                up = recurring.coming_up(conn, res, today=today)
                rows = statuses(up, "Stream")
                self.assertEqual(rows[:2], [("2026-09-28", "late", None), ("2026-10-28", october, None)])
                active = recurring.page(conn, res, today=today)["active"][0]
                self.assertTrue(active["late"])
                self.assertEqual(active["next_expected"], "2026-09-28")

    def test_a_missed_slot_is_late_once_the_cadence_band_has_run_out(self):
        # 35 days after 28 August is 2 October, so the September payment is late from the 3rd and not before
        conn = record(*monthly("Stream", "-15.00", "2026-06-28", "2026-07-28", "2026-08-28"), ("Club", "2026-10-30", "-12.00"))
        res = results([series("Stream", 15.0, "2026-08-28")])
        self.assertEqual(statuses(recurring.coming_up(conn, res, today=date(2026, 10, 2)), "Stream")[0][1], "due")
        self.assertEqual(statuses(recurring.coming_up(conn, res, today=date(2026, 10, 3)), "Stream")[0][1], "late")

    def test_paying_the_missed_month_late_clears_it(self):
        conn = record(*monthly("Stream", "-15.00", "2026-06-28", "2026-07-28", "2026-08-28", "2026-10-06"),
                      ("Club", "2026-10-30", "-12.00"))
        up = recurring.coming_up(conn, results([series("Stream", 15.0, "2026-08-28")]), today=date(2026, 10, 30))
        self.assertEqual(statuses(up, "Stream"), [("2026-09-28", "paid", "2026-10-06"), ("2026-10-28", "due", None)])

    def test_a_missed_quarter_is_late_on_recurring_and_on_home(self):
        # P3: quarterly, last paid 15 June, judged on 20 October
        conn = record(*monthly("Insurer", "-300.00", "2025-12-15", "2026-03-15", "2026-06-15"), ("Club", "2026-10-20", "-12.00"))
        res = results([series("Insurer", 300.0, "2026-06-15", cadence="quarterly")])
        up = recurring.coming_up(conn, res, today=date(2026, 10, 20))
        self.assertEqual(statuses(up, "Insurer"), [("2026-09-15", "late", None)])
        self.assertTrue(recurring.page(conn, res, today=date(2026, 10, 20))["active"][0]["late"])
        self.assertEqual(home.needs_your_eye(conn, res, up)["recurring_off"],
                         [{"id": "payee:Insurer", "payee": "Insurer", "name": "Insurer", "status": "late",
                           "expected": "2026-09-15"}])

    def test_home_names_each_series_once_by_its_most_urgent_payment(self):
        # a weekly series late twice this month is one bill late, not two
        conn = record(*monthly("Cleaner", "-60.00", "2026-08-01", "2026-08-08", "2026-08-15", "2026-08-22", "2026-08-29"),
                      ("Club", "2026-09-20", "-12.00"))
        res = results([series("Cleaner", 60.0, "2026-08-29", cadence="weekly")])
        up = recurring.coming_up(conn, res, today=date(2026, 9, 30))
        self.assertEqual([r["status"] for r in up["rows"] if r["payee"] == "Cleaner"], ["late", "late", "due", "due"])
        self.assertEqual(home.needs_your_eye(conn, res, up)["recurring_off"],
                         [{"id": "payee:Cleaner", "payee": "Cleaner", "name": "Cleaner", "status": "late",
                           "expected": "2026-09-05"}])

    def test_an_early_payment_counts_for_the_month_it_was_due(self):
        # P4: monthly on the 1st, November's payment made on 30 October
        conn = record(*monthly("Gym", "-50.00", "2026-06-01", "2026-07-01", "2026-08-01", "2026-09-01", "2026-10-01",
                               "2026-10-30"), ("Club", "2026-11-20", "-12.00"))
        res = results([series("Gym", 50.0, "2026-10-30")])
        up = recurring.coming_up(conn, res, today=date(2026, 11, 20))
        self.assertEqual(statuses(up, "Gym"), [("2026-11-01", "paid", "2026-10-30")])
        active = recurring.page(conn, res, today=date(2026, 11, 20))["active"][0]
        self.assertEqual((active["next_expected"], active["late"]), ("2026-12-01", False))
        # in October it is not a second October payment
        october = recurring.coming_up(conn, res, today=date(2026, 10, 31))
        self.assertEqual(statuses(october, "Gym"), [("2026-10-01", "paid", "2026-10-01")])
        self.assertEqual(october["totals"]["committed"], 5000)

    def test_the_anchor_is_the_usual_day_across_a_month_end(self):
        # paid on the last day or the first, the bill is the one due on the 1st
        conn = record(*monthly("Charity", "-18.00", "2026-03-31", "2026-05-02", "2026-06-02", "2026-07-01", "2026-07-31",
                               "2026-09-01"), ("Club", "2026-09-30", "-12.00"))
        up = recurring.coming_up(conn, results([series("Charity", 18.0, "2026-09-01")]), today=date(2026, 9, 30))
        self.assertEqual(statuses(up, "Charity"), [("2026-09-01", "paid", "2026-09-01")])

    def test_a_second_charge_in_a_month_is_extra_not_next_month_s(self):
        # P5: S$20 on the 5th, and another S$21 on 20 September
        conn = record(*monthly("Phone", "-20.00", "2026-06-05", "2026-07-05", "2026-08-05", "2026-09-05"),
                      ("Phone", "2026-09-20", "-21.00"), ("Club", "2026-10-06", "-12.00"))
        res = results([series("Phone", 20.0, "2026-09-05")])
        up = recurring.coming_up(conn, res, today=date(2026, 9, 25))
        self.assertEqual(statuses(up, "Phone"), [("2026-09-05", "paid", "2026-09-05"), ("2026-09-20", "extra", "2026-09-20")])
        self.assertEqual((up["totals"]["committed"], up["totals"]["paid"], up["totals"]["extra"]), (2000, 2000, 2100))
        extra = next(r for r in up["rows"] if r["status"] == "extra")
        listed = api.transactions(conn, {"hide_transfers": "1", **extra["link"]})
        self.assertEqual((listed["total"], -listed["sum_sgd"]), (1, 2100))
        # an extra charge is not a bill late or at a different amount
        self.assertEqual(home.needs_your_eye(conn, res, up)["recurring_off"], [])
        # and it never pays October's bill
        october = recurring.coming_up(conn, res, today=date(2026, 10, 6))
        self.assertEqual(statuses(october, "Phone"), [("2026-10-05", "due", None)])

    def test_each_series_is_judged_on_its_own_account_s_last_statement(self):
        # P9: the card's statements run to 28 October, the bank's only to 30 September
        conn = two_accounts("2026-09-30", "2026-10-28",
                            monthly("Insurer", "-80.00", "2026-06-01", "2026-07-01", "2026-08-01", "2026-09-01"),
                            [("Club", "2026-10-28", "-12.00")])
        res = results([series("Insurer", 80.0, "2026-09-01")])
        up = recurring.coming_up(conn, res, today=date(2026, 11, 2))
        self.assertEqual((up["month"], up["as_of"]), ("2026-10", "2026-10-28"))
        self.assertEqual(statuses(up, "Insurer"), [("2026-10-01", "due", None)])
        self.assertFalse(recurring.page(conn, res, today=date(2026, 11, 2))["active"][0]["late"])
        self.assertEqual(home.needs_your_eye(conn, res, up)["recurring_off"], [])

    def test_a_week_skipped_before_a_paid_one_is_not_late_anywhere(self):
        # Saturdays, paid through 29 August, nothing on 5 September, paid 12 September; judged on 14 September
        conn = record(*monthly("Cleaner", "-60.00", "2026-08-01", "2026-08-08", "2026-08-15", "2026-08-22", "2026-08-29",
                               "2026-09-12"), ("Club", "2026-09-14", "-12.00"))
        res = results([series("Cleaner", 60.0, "2026-09-12", cadence="weekly")])
        up = recurring.coming_up(conn, res, today=date(2026, 9, 30))
        self.assertEqual(statuses(up, "Cleaner"), [("2026-09-12", "paid", "2026-09-12"), ("2026-09-19", "due", None),
                                                   ("2026-09-26", "due", None)])
        self.assertEqual(home.needs_your_eye(conn, res, up)["recurring_off"], [])
        active = recurring.page(conn, res, today=date(2026, 9, 30))["active"][0]
        self.assertEqual((active["late"], active["next_expected"]), (False, "2026-09-19"))

    def test_the_schedule_covers_the_whole_month_however_many_charges_it_holds(self):
        # Saturdays 5, 12, 19 and 26 September, and charges in the usual range on 15, 22 and 30 September:
        # the last six payments start on the 12th, but the 5th is still September's first payment
        for history in ((), ("2026-08-15", "2026-08-22", "2026-08-29")):
            with self.subTest(history=history):
                conn = record(*monthly("Cleaner", "-60.00", *history, "2026-09-05", "2026-09-12", "2026-09-19", "2026-09-26"),
                              *monthly("Cleaner", "-60.00", "2026-09-15", "2026-09-22", "2026-09-30"))
                up = recurring.coming_up(conn, results([series("Cleaner", 60.0, "2026-09-26", cadence="weekly")]),
                                         today=date(2026, 10, 5))
                self.assertEqual(statuses(up, "Cleaner"), [
                    ("2026-09-05", "paid", "2026-09-05"), ("2026-09-12", "paid", "2026-09-12"),
                    ("2026-09-15", "extra", "2026-09-15"), ("2026-09-19", "paid", "2026-09-19"),
                    ("2026-09-22", "extra", "2026-09-22"), ("2026-09-26", "paid", "2026-09-26"),
                    ("2026-09-30", "extra", "2026-09-30")])
                self.assertEqual((up["totals"]["committed"], up["totals"]["paid"], up["totals"]["extra"]),
                                 (4 * 6000, 4 * 6000, 3 * 6000))

    def test_the_judged_day_follows_the_series_own_payments_not_another_charge_to_the_payee(self):
        # the S$80 bill is paid from the bank, statements to 30 September; one S$300 purchase at the same
        # payee on the card, whose statements run to 28 October, is not part of the series
        conn = two_accounts("2026-09-30", "2026-10-28",
                            monthly("Insurer", "-80.00", "2026-06-01", "2026-07-01", "2026-08-01", "2026-09-01"),
                            [("Insurer", "2026-09-20", "-300.00"), ("Club", "2026-10-28", "-12.00")])
        res = results([series("Insurer", 80.0, "2026-09-01")])
        up = recurring.coming_up(conn, res, today=date(2026, 11, 2))
        self.assertEqual(statuses(up, "Insurer"), [("2026-10-01", "due", None)])
        self.assertFalse(recurring.page(conn, res, today=date(2026, 11, 2))["active"][0]["late"])

    def test_a_statement_that_ends_after_its_last_row_still_counts_to_its_end(self):
        # the bank printed nothing after 1 September but its statement runs to 15 October
        conn = two_accounts("2026-10-15", "2026-10-28",
                            monthly("Insurer", "-80.00", "2026-06-01", "2026-07-01", "2026-08-01", "2026-09-01"),
                            [("Club", "2026-10-28", "-12.00")])
        up = recurring.coming_up(conn, results([series("Insurer", 80.0, "2026-09-01")]), today=date(2026, 11, 2))
        self.assertEqual(statuses(up, "Insurer"), [("2026-10-01", "late", None)])


class Page(unittest.TestCase):
    def setUp(self):
        self.conn = record(*ROWS, *monthly("Magazine", "-9.00", "2026-01-03", "2026-04-11", "2026-04-20"))
        self.addCleanup(self.conn.close)
        old = series("Club", 12.0, "2026-03-01")
        self.page = recurring.page(self.conn, results(ACTIVE, stopped=[old], flagged=["Magazine"]),
                                   today=date(2026, 10, 16))

    def test_active_rows_carry_cadence_amount_last_next_and_yearly_cost(self):
        active = {r["payee"]: r for r in self.page["active"]}
        gym = active["Gym"]
        # G16: next is on the anchor day, the 5th, not a month after a payment a day early
        self.assertEqual((gym["cadence"], gym["amount"], gym["last_paid"], gym["next_expected"], gym["yearly"]),
                         ("monthly", 5000, "2026-09-04", "2026-10-05", 60000))
        self.assertEqual(active["Cleaner"]["yearly"], 52 * 6000)
        self.assertEqual(active["Insurer"]["yearly"], 4 * 30000)
        self.assertTrue(active["Phone"]["late"])
        self.assertEqual(gym["link"], {"payee": "Gym", "named": "1", "hide_transfers": "0", "synthetic": "0", "out": "1"})

    def test_investing_is_last_and_out_of_the_spending_total(self):
        self.assertEqual(self.page["active"][-1]["payee"], "Broker")
        t = self.page["totals"]
        self.assertEqual(t["yearly_investing"], 12 * 50000)
        self.assertEqual(t["yearly_spending"],
                         12 * (5000 + 2000 + 1500 + 100000) + 52 * 6000 + 4 * 30000 + 8000)

    def test_last_paid_follows_the_record_past_the_stat(self):
        conn = record(*ROWS, ("Gym", "2026-09-30", "-50.00"))
        page = recurring.page(conn, results([series("Gym", 50.0, "2026-08-05")]), today=date(2026, 10, 16))
        self.assertEqual(page["active"][0]["last_paid"], "2026-09-30")

    def test_stopped_rows_say_when_and_what_they_cost(self):
        club = self.page["stopped"][0]
        self.assertEqual((club["payee"], club["last_seen"], club["amount"], club["yearly"]),
                         ("Club", "2026-03-01", 1200, 14400))

    def test_marked_recurring_but_irregular_lists_its_payments(self):
        mag = self.page["irregular"][0]
        self.assertEqual((mag["payee"], mag["payments"], mag["first"], mag["last"], mag["total"]),
                         ("Magazine", 3, "2026-01-03", "2026-04-20", 2700))
        self.assertEqual(mag["dates"], ["2026-01-03", "2026-04-11", "2026-04-20"])
        self.assertEqual(mag["link"], {"payee": "Magazine", "named": "1", "hide_transfers": "0", "synthetic": "0", "out": "1"})

    def listed(self, link):
        # what Transactions asks the server for: transfers hidden unless the link says otherwise
        return api.transactions(self.conn, {"hide_transfers": "1", **link})["total"]

    def test_a_series_link_lists_every_payment_it_counts_investing_included(self):
        # G13: Broker's payments are Transfer-bucket rows, hidden by default in Transactions
        for row in self.page["active"]:
            if row["payee"] in ("Gym", "Broker", "Cleaner"):
                paid = self.conn.execute("SELECT COUNT(*) FROM v_txn WHERE payee_name = ?", (row["payee"],)).fetchone()[0]
                self.assertEqual(self.listed(row["link"]), paid, row["payee"])
        broker = next(r for r in self.page["active"] if r["payee"] == "Broker")
        self.assertEqual(broker["link"], {"payee": "Broker", "named": "1", "hide_transfers": "0", "synthetic": "0", "out": "1"})
        self.assertEqual(self.listed({"q": "Broker"}), 0)

    def test_a_payee_with_one_payment_in_a_transfer_category_still_lists_it(self):
        # the series is mostly Spend, but the detector counts every payment of the payee
        self.conn.execute("UPDATE txn SET category_id = (SELECT id FROM category WHERE name = 'Investments') "
                          "WHERE txn_key = (SELECT MIN(txn_key) FROM txn WHERE payee_id = "
                          "(SELECT id FROM payee WHERE name = 'Gym'))")
        gym = next(r for r in self.page["active"] if r["payee"] == "Gym")
        self.assertEqual(self.listed(gym["link"]), 4)
        self.assertEqual(self.listed({"q": "Gym"}), 3)

    def test_an_irregular_payee_link_lists_the_payments_it_counts(self):
        page = recurring.page(self.conn, results(ACTIVE, flagged=["Broker", "Magazine"]), today=date(2026, 10, 16))
        for r in page["irregular"]:
            self.assertEqual(self.listed(r["link"]), r["payments"], r["payee"])

    def test_a_series_with_no_payee_links_to_its_category_and_month(self):
        s = series("(no payee, about SGD 18)", 18.0, "2025-02-01", grouped_by="amount", category="(uncategorised)")
        self.assertEqual(recurring.transactions_filter(s, []),
                         {"month": "2025-02", "category": "(none)", "hide_transfers": "0"})

    def test_no_result_yet_still_answers(self):
        page = recurring.page(self.conn, {}, today=date(2026, 10, 16))
        self.assertFalse(page["ready"])
        self.assertEqual((page["active"], page["stopped"], page["irregular"], page["coming_up"]["rows"]),
                         ([], [], [], []))


SUBS = [{"name": "Subscriptions", "bucket": "Spend", "group": "Fixed", "need_class": "Want", "sort_order": 150}]


def detected(*rows, flagged=()):
    """G16: a record of (payee, day, amount[, bank text]) rows in a non-habit category, and the
    detector's own result over it, so the page is read against what the stat really finds."""
    from analytics.stats import recurring_detector
    conn = db.connect(":memory:")
    db.import_user_tables(conn, {**{k: [dict(x) for x in v] for k, v in SNAP.items()},
                                 "categories": [dict(c) for c in CATEGORIES + SUBS]})
    keyed = [row(f"k{i:03d}", amount, date=day, payee=payee, merchant=(rest[0] if rest else payee.upper()),
                 category="Subscriptions")
             for i, (payee, day, amount, *rest) in enumerate(rows)]
    names = sorted({r[0] for r in rows if r[0]})
    db.load_parsed(conn, parsed([block()], keyed), payees=[{"name": p, "kind": "Merchant", "country": "SGP"} for p in names])
    for p in flagged:
        conn.execute("UPDATE payee SET recurring = 1 WHERE name = ?", (p,))
    return conn, {recurring.STAT: recurring_detector.run(db.snapshot(conn))}


def days(first, count, day):
    y, m = int(first[:4]), int(first[5:]) - 1
    return [f"{(y * 12 + m + k) // 12}-{(y * 12 + m + k) % 12 + 1:02d}-{day:02d}" for k in range(count)]


def listed_keys(conn, link):
    return sorted(r["key"] for r in api.transactions(conn, {"hide_transfers": "1", "limit": "500", **link})["rows"])


def keys_where(conn, sql, *args):
    return sorted(r[0] for r in conn.execute(f"SELECT txn_key FROM v_txn WHERE {sql}", args))


class SeveralSeries(unittest.TestCase):
    """G16: a payee with several series, each its own row, its own link and its own payments."""

    def setUp(self):
        # two plans at one payee, on the 12th and the 20th; the record ends on 25 September
        self.conn, self.res = detected(*[("Fruitco", d, "-3.98") for d in days("2026-01", 9, 12)],
                                       *[("Fruitco", d, "-10.98") for d in days("2026-01", 9, 20)],
                                       ("Club", "2026-09-25", "-12.00"), flagged=["Fruitco"])
        self.addCleanup(self.conn.close)
        self.page = recurring.page(self.conn, self.res, today=date(2026, 10, 16))

    def test_each_series_is_a_row_with_its_own_id_and_name(self):
        rows = sorted((r["name"], r["id"], r["amount"]) for r in self.page["active"])
        self.assertEqual(rows, [("Fruitco (S$10.98)", "payee:Fruitco:1098", 1098),
                                ("Fruitco (S$3.98)", "payee:Fruitco:398", 398)])

    def test_each_series_links_to_exactly_its_own_rows(self):
        for r in self.page["active"]:
            with self.subTest(r["name"]):
                self.assertEqual(sorted(r["link"]), ["hide_transfers", "key"])
                want = keys_where(self.conn, "payee_name = 'Fruitco' AND amount_sgd_minor = ?", -r["amount"])
                self.assertEqual(listed_keys(self.conn, r["link"]), want)

    def test_coming_up_has_each_series_payment_and_home_names_both(self):
        up = self.page["coming_up"]
        fruit = sorted((r["name"], r["expected"], r["status"], r["paid_amount"]) for r in up["rows"] if r["payee"] == "Fruitco")
        self.assertEqual(fruit, [("Fruitco (S$10.98)", "2026-09-20", "paid", 1098),
                                 ("Fruitco (S$3.98)", "2026-09-12", "paid", 398)])
        self.assertEqual(len(recurring.most_urgent(up["rows"])), 2)
        self.assertEqual(up["totals"]["paid"], 398 + 1098)


class OneSeriesBesideTopUps(unittest.TestCase):
    """G16: a subscription with top-ups beside it: the top-ups are not its payments."""

    def setUp(self):
        top_ups = [("2026-03-07", "2026-03-22"), ("2026-04-11", "2026-04-14"), ("2026-05-05", "2026-05-27"),
                   ("2026-06-16", "2026-06-19"), ("2026-07-08", "2026-07-30"), ("2026-08-12", "2026-08-26"),
                   ("2026-09-06", "2026-09-09")]
        self.conn, self.res = detected(*[("Chatbot", d, "-28.00") for d in days("2026-03", 6, 23)],
                                       *[("Chatbot", d, "-10.00") for pair in top_ups for d in pair],
                                       ("Club", "2026-09-15", "-12.00"))
        self.addCleanup(self.conn.close)
        self.page = recurring.page(self.conn, self.res, today=date(2026, 10, 16))

    def test_one_row_whose_link_lists_the_subscription_alone(self):
        [r] = [r for r in self.page["active"] if r["payee"] == "Chatbot"]
        self.assertEqual((r["name"], r["amount"]), ("Chatbot", 2800))
        self.assertEqual(listed_keys(self.conn, r["link"]),
                         keys_where(self.conn, "payee_name = 'Chatbot' AND amount_sgd_minor = -2800"))

    def test_a_top_up_before_the_bill_does_not_answer_for_it(self):
        [r] = [r for r in self.page["coming_up"]["rows"] if r["payee"] == "Chatbot"]
        self.assertEqual((r["expected"], r["status"], r["paid_amount"]), ("2026-09-23", "due", None))


class PriceRiseOnThePage(unittest.TestCase):
    def test_the_new_price_is_paid_not_a_different_amount(self):
        conn, res = detected(*[("Streamy", d, "-10.00") for d in days("2025-09", 12, 5)],
                             *[("Streamy", d, "-13.00") for d in days("2026-09", 2, 5)], ("Club", "2026-10-20", "-12.00"))
        self.addCleanup(conn.close)
        page = recurring.page(conn, res, today=date(2026, 10, 25))
        [r] = [r for r in page["active"] if r["payee"] == "Streamy"]
        self.assertEqual((r["amount"], r["last_paid"]), (1300, "2026-10-05"))
        [c] = [c for c in page["coming_up"]["rows"] if c["payee"] == "Streamy"]
        self.assertEqual((c["status"], c["paid_amount"]), ("paid", 1300))
        self.assertEqual(page["stopped"], [])


class PriceBackToAnEarlierOne(unittest.TestCase):
    def test_the_live_bill_stays_on_coming_up_and_in_the_totals(self):
        amounts = ["-10.00"] * 12 + ["-13.00"] * 3 + ["-10.00"] * 2
        conn, res = detected(*[("Streamy", d, a) for d, a in zip(days("2025-05", 17, 5), amounts)],
                             ("Club", "2026-09-20", "-12.00"))
        self.addCleanup(conn.close)
        page = recurring.page(conn, res, today=date(2026, 10, 1))
        self.assertEqual((page["paused"], page["stopped"]), ([], []))
        [r] = [r for r in page["active"] if r["payee"] == "Streamy"]
        self.assertEqual((r["amount"], r["last_paid"]), (1000, "2026-09-05"))
        [c] = [c for c in page["coming_up"]["rows"] if c["payee"] == "Streamy"]
        self.assertEqual((c["status"], c["paid_amount"]), ("paid", 1000))
        self.assertEqual(page["coming_up"]["totals"]["paid"], 1000)


class Paused(unittest.TestCase):
    def setUp(self):
        self.conn, self.res = detected(*[("Gym", d, "-50.00") for d in days("2026-01", 8, 5)],
                                       *[("Stream", d, "-15.00") for d in days("2026-01", 9, 25)])
        self.addCleanup(self.conn.close)
        self.page = recurring.page(self.conn, self.res, today=date(2026, 10, 16))

    def test_a_paused_series_is_listed_apart_and_counted_nowhere(self):
        self.assertEqual([(r["payee"], r["state"], r["late"]) for r in self.page["paused"]], [("Gym", "paused", False)])
        self.assertEqual([r["payee"] for r in self.page["active"]], ["Stream"])
        self.assertNotIn("Gym", [r["payee"] for r in self.page["coming_up"]["rows"]])
        self.assertEqual((self.page["totals"]["paused"], self.page["totals"]["yearly_spending"]), (1, 12 * 1500))
        eye = home.needs_your_eye(self.conn, self.res, self.page["coming_up"])
        self.assertEqual(eye["recurring_off"], [])

    def test_a_paused_series_that_pays_again_is_active_before_the_stat_runs_again(self):
        conn, _ = detected(*[("Gym", d, "-50.00") for d in days("2026-01", 8, 5)], ("Gym", "2026-09-28", "-50.00"),
                           *[("Stream", d, "-15.00") for d in days("2026-01", 9, 25)])
        self.addCleanup(conn.close)
        page = recurring.page(conn, self.res, today=date(2026, 10, 16))
        self.assertEqual(page["paused"], [])
        self.assertEqual(sorted((r["payee"], r["state"]) for r in page["active"]), [("Gym", "active"), ("Stream", "active")])


class PausedResumes(unittest.TestCase):
    """G16: a paused series that pays again at a new price is active, its payment a different amount."""

    def setUp(self):
        self.gym = [("Gym", d, "-50.00") for d in days("2026-01", 6, 5)]
        _, self.res = detected(*self.gym, ("Club", "2026-08-15", "-12.00"))
        self.assertEqual([s["payee"] for s in self.res["recurring-detector"]["data"]["paused"]], ["Gym"])

    def page(self, *more):
        conn, _ = detected(*self.gym, ("Club", "2026-08-15", "-12.00"), *more)
        self.addCleanup(conn.close)
        return recurring.page(conn, self.res, today=date(2026, 10, 1))

    def test_a_resumed_payment_at_a_new_price_revives_it(self):
        page = self.page(("Gym", "2026-09-05", "-65.00"))
        self.assertEqual(page["paused"], [])
        [r] = [r for r in page["active"] if r["payee"] == "Gym"]
        # the payment that brought it back is its last, and the months it was quiet are not owed
        self.assertEqual((r["state"], r["last_paid"], r["late"]), ("active", "2026-09-05", False))
        [c] = [c for c in page["coming_up"]["rows"] if c["payee"] == "Gym"]
        self.assertEqual((c["expected"], c["status"], c["paid_amount"]), ("2026-09-05", "different", 6500))

    def test_a_charge_off_its_day_or_far_from_its_price_does_not(self):
        for day, amount in (("2026-09-19", "-65.00"), ("2026-09-05", "-400.00"), ("2026-09-05", "-9.00")):
            with self.subTest(day=day, amount=amount):
                page = self.page(("Gym", day, amount))
                # G16: still quiet (paused, or stopped by the record's later last day), never active
                self.assertNotIn("Gym", [r["payee"] for r in page["active"]])


class TwiceMonthlyTolerance(unittest.TestCase):
    """G16: twice a month on two uneven days (the 1st and the 10th) has a fixed tolerance:
    the long gap to the next month's 1st must not make that payment late before it is due."""

    def judged(self, upto, on):
        """Paid on the 1st and 10th of every month from January to the month of `upto` (its
        10th included), and the record and today both at `on`."""
        y, m = int(upto[:4]), int(upto[5:7])
        first = f"{y - 1}-{m:02d}" if m < 12 else f"{y}-01"
        rent = [d for d in sorted(days(first, 13, 1) + days(first, 13, 10)) if d <= f"{upto}-10"]
        conn, res = detected(*[("Landlord", d, "-500.00") for d in rent], ("Club", on, "-12.00"))
        self.addCleanup(conn.close)
        self.assertEqual(res["recurring-detector"]["data"]["active"][0]["cadence"], "twice-monthly")
        page = recurring.page(conn, res, today=date.fromisoformat(on))
        [r] = [r for r in page["active"] if r["payee"] == "Landlord"]
        return r

    def test_the_1st_is_not_late_before_it_comes_in_any_month_length(self):
        for upto, end, nxt in (("2026-07", "2026-07-31", "2026-08-01"), ("2026-04", "2026-04-30", "2026-05-01"),
                               ("2026-02", "2026-02-28", "2026-03-01"), ("2028-02", "2028-02-29", "2028-03-01")):
            with self.subTest(month=upto):
                r = self.judged(upto, end)
                self.assertEqual((r["late"], r["next_expected"]), (False, nxt))

    def test_the_1st_is_late_three_days_after_it_was_due(self):
        self.assertFalse(self.judged("2026-07", "2026-08-04")["late"])
        self.assertTrue(self.judged("2026-07", "2026-08-05")["late"])

    def test_no_cadence_has_a_tolerance_below_the_floor(self):
        for cadence in recurring.BANDS:
            with self.subTest(cadence=cadence):
                s = {"payee": "X", "cadence": cadence, "median_amount_minor": 5000}
                pays = recurring.merge([(date(2026, 1, 1) + timedelta(days=7 * k), 5000, "X", f"k{k}") for k in range(30)], True)
                sched = recurring.Schedule(s, pays, date(2026, 8, 1), date(2026, 12, 31), date(2026, 7, 1))
                self.assertTrue(all(sched.tolerance(n) >= recurring.MIN_TOLERANCE for n in range(sched.first, sched.last + 1)))


class VaryingBillRange(unittest.TestCase):
    def test_a_big_purchase_off_the_schedule_does_not_widen_the_usual_range(self):
        # a power bill that moves between S$120 and S$180 on the 8th, and a S$420 appliance from the same shop
        amounts = ["-120.00", "-150.00", "-135.00", "-180.00", "-160.00", "-140.00", "-170.00", "-130.00"]
        conn, res = detected(*[("Power", d, a) for d, a in zip(days("2026-01", 8, 8), amounts)],
                             ("Power", "2026-07-20", "-420.00"), ("Power", "2026-09-08", "-260.00"),
                             ("Club", "2026-09-20", "-12.00"))
        self.addCleanup(conn.close)
        [s] = res["recurring-detector"]["data"]["active"]
        self.assertTrue(s["amount_varies"])
        page = recurring.page(conn, res, today=date(2026, 9, 30))
        [c] = [c for c in page["coming_up"]["rows"] if c["payee"] == "Power"]
        self.assertEqual((c["status"], c["paid_amount"]), ("different", 26000))
        self.assertLess(c["usual_high"], 26000)


class TwoBillsOnePayment(unittest.TestCase):
    def test_the_page_gives_each_charge_to_one_bill(self):
        conn, res = detected(*[("Twobill", d, "-40.00") for d in days("2026-01", 6, 1)],
                             *[("Twobill", d, "-100.00") for d in ("2025-11-01", "2026-02-01", "2026-05-01")],
                             ("Twobill", "2026-07-01", "-60.00"), ("Twobill", "2026-08-01", "-76.00"),
                             ("Club", "2026-08-20", "-12.00"))
        self.addCleanup(conn.close)
        data = res["recurring-detector"]["data"]
        rec = recurring.Record(conn, data)
        keys = {}
        for s in rec.data["active"] + rec.data["paused"] + rec.data["stopped"]:
            if s["payee"] == "Twobill":
                keys[s["cadence"]] = {k for p in rec.prepare(s, date(2026, 9, 1))[0] for k in p.keys}
        self.assertEqual(keys["monthly"] & keys["quarterly"], set())
        late = keys_where(conn, "payee_name = 'Twobill' AND txn_date >= '2026-07-01'")
        self.assertTrue(set(late) <= keys["monthly"])


class StaleResult(unittest.TestCase):
    def test_a_payment_loaded_after_the_stat_still_credits_the_bill_the_detector_would(self):
        # the stat ran on S$40 monthly to 1 June and S$100 quarterly to 1 May; then S$40 on
        # 1 July and S$60 on 1 August were loaded, and the stat has not run again
        old = [*[("Twobill", d, "-40.00") for d in days("2026-01", 6, 1)],
               *[("Twobill", d, "-100.00") for d in ("2025-11-01", "2026-02-01", "2026-05-01")]]
        _, stale = detected(*old, ("Club", "2026-06-20", "-12.00"))
        new = [("Twobill", "2026-07-01", "-40.00"), ("Twobill", "2026-08-01", "-60.00")]
        conn, fresh = detected(*old, *new, ("Club", "2026-08-20", "-12.00"))
        self.addCleanup(conn.close)

        def credit(res):
            data = res["recurring-detector"]["data"]
            rec = recurring.Record(conn, data)
            return {k: s["cadence"] for s in rec.data["active"] + rec.data["paused"] + rec.data["stopped"] if s["payee"] == "Twobill"
                    for p in rec.prepare(s, date(2026, 9, 1))[0] for k in p.keys}
        [aug] = keys_where(conn, "payee_name = 'Twobill' AND txn_date = '2026-08-01'")
        self.assertEqual(credit(fresh)[aug], "monthly")
        self.assertEqual(credit(stale)[aug], "monthly")


class Backfilled(unittest.TestCase):
    def test_older_rows_loaded_after_the_stat_never_credit_one_charge_to_two_bills(self):
        # the stat ran on S$40 monthly January to June and S$100 quarterly November to May;
        # then older S$30 monthly rows, October to December, were loaded, which join the
        # S$40 chain and change its first charge, and S$100 came on 1 August
        saved = [*[("Twobill", d, "-40.00") for d in days("2026-01", 6, 1)],
                 *[("Twobill", d, "-100.00") for d in ("2025-11-01", "2026-02-01", "2026-05-01")]]
        _, stale = detected(*saved, ("Club", "2026-06-20", "-12.00"))
        older = [("Twobill", d, "-30.00") for d in days("2025-10", 3, 1)]
        conn, _ = detected(*older, *saved, ("Twobill", "2026-08-01", "-100.00"), ("Club", "2026-08-20", "-12.00"))
        self.addCleanup(conn.close)
        data = stale["recurring-detector"]["data"]
        rec = recurring.Record(conn, data)
        credit = {}
        for s in rec.data["active"] + rec.data["paused"] + rec.data["stopped"]:
            for p in rec.prepare(s, date(2026, 9, 1))[0]:
                for k in p.keys:
                    self.assertNotIn(k, credit, (k, s["cadence"], credit.get(k)))
                    credit[k] = s["cadence"]
        [aug] = keys_where(conn, "payee_name = 'Twobill' AND txn_date = '2026-08-01'")
        self.assertEqual(credit[aug], "quarterly")
        # the monthly bill is read again from the record: the older S$30 rows are its earlier
        # price, and none of the quarterly payments is among its own
        monthly = sorted(k for k, c in credit.items() if c == "monthly")
        self.assertEqual(monthly, keys_where(conn, "payee_name = 'Twobill' AND amount_sgd_minor IN (-3000, -4000)"))


class OlderStatementOfAVaryingBill(unittest.TestCase):
    def test_the_bill_stays_on_coming_up_when_an_older_statement_moves_its_first_charge(self):
        # S$40 and S$80 by turns on the 5th through 2025 (usual S$60); then the stat ran; then a
        # December 2024 statement with S$80 on the 5th was loaded, and 2026's first bill came
        bill = [("Power", d, "-40.00" if k % 2 == 0 else "-80.00") for k, d in enumerate(days("2025-01", 12, 5))]
        _, stale = detected(*bill, ("Club", "2025-12-20", "-12.00"))
        conn, fresh = detected(("Power", "2024-12-05", "-80.00"), *bill, ("Power", "2026-01-05", "-40.00"),
                               ("Club", "2026-01-20", "-12.00"))
        self.addCleanup(conn.close)
        page = recurring.page(conn, stale, today=date(2026, 1, 25))
        self.assertEqual([r["payee"] for r in page["active"]], ["Power"])
        self.assertEqual([(r["payee"], r["status"]) for r in page["coming_up"]["rows"]], [("Power", "paid")])
        # and it reads exactly as the page does once the stat has run again
        again = recurring.page(conn, fresh, today=date(2026, 1, 25))
        self.assertEqual({k: v for k, v in page.items() if k != "generated_at"},
                         {k: v for k, v in again.items() if k != "generated_at"})


class FlaggedPayeeGainsASeries(unittest.TestCase):
    def test_a_marked_payee_that_now_keeps_a_cadence_leaves_the_irregular_list(self):
        # the rules mark Gym recurring; the stat saw two payments (no series), then a third came
        two = [("Gym", "2026-07-05", "-50.00"), ("Gym", "2026-08-05", "-50.00")]
        _, stale = detected(*two, ("Club", "2026-08-20", "-12.00"), flagged=["Gym"])
        self.assertEqual(stale["recurring-detector"]["data"]["flag_mismatches"]["flagged_not_detected"], ["Gym"])
        conn, _ = detected(*two, ("Gym", "2026-09-05", "-50.00"), ("Club", "2026-09-20", "-12.00"), flagged=["Gym"])
        self.addCleanup(conn.close)
        page = recurring.page(conn, stale, today=date(2026, 9, 30))
        self.assertEqual([r["payee"] for r in page["active"]], ["Gym"])
        self.assertEqual(page["irregular"], [])
        up = recurring.coming_up(conn, stale, today=date(2026, 9, 30))
        self.assertEqual(home.needs_your_eye(conn, stale, up)["irregular"], 0)

    def test_a_marked_payee_whose_series_is_gone_joins_the_irregular_list(self):
        # the stat saw a monthly Gym; then an older, irregular statement was loaded that breaks it
        bills = [("Gym", d, "-50.00") for d in ("2026-06-05", "2026-07-05", "2026-08-05")]
        _, stale = detected(*bills, ("Club", "2026-08-20", "-12.00"), flagged=["Gym"])
        self.assertEqual([s["payee"] for s in stale["recurring-detector"]["data"]["active"]], ["Gym"])
        conn, fresh = detected(*bills, ("Gym", "2026-06-20", "-50.00"), ("Gym", "2026-07-22", "-50.00"),
                               ("Gym", "2026-08-12", "-50.00"), ("Club", "2026-08-20", "-12.00"), flagged=["Gym"])
        self.addCleanup(conn.close)
        if fresh["recurring-detector"]["data"]["active"]:
            self.skipTest("the fresh run still finds a series")
        page = recurring.page(conn, stale, today=date(2026, 8, 31))
        self.assertEqual(([r["payee"] for r in page["active"]], [r["payee"] for r in page["irregular"]]), ([], ["Gym"]))


class ShortCadenceMissed(unittest.TestCase):
    """G16: under a month, a missed payment is late on the page, not a paused series."""

    def page(self, rows, end, today):
        conn, res = detected(*rows, ("Club", end, "-12.00"))
        self.addCleanup(conn.close)
        return recurring.page(conn, res, today=today)

    def test_a_missed_rent_half_is_late(self):
        rent = sorted(days("2026-01", 6, 1) + days("2026-01", 5, 15))        # nothing on 15 June
        page = self.page([("Landlord", d, "-1000.00") for d in rent], "2026-06-28", date(2026, 7, 10))
        self.assertEqual(page["paused"], [])
        [r] = [r for r in page["active"] if r["payee"] == "Landlord"]
        self.assertEqual((r["cadence"], r["late"]), ("twice-monthly", True))
        self.assertIn(("2026-06-15", "late"), [(c["expected"], c["status"]) for c in page["coming_up"]["rows"]
                                              if c["payee"] == "Landlord"])

    def test_a_missed_week_is_late(self):
        weeks = [date.fromordinal(date(2026, 4, 3).toordinal() + 7 * k).isoformat() for k in range(12)]
        page = self.page([("Cleaner", d, "-60.00") for d in weeks if d != weeks[-1]], "2026-06-24", date(2026, 7, 10))
        self.assertEqual(page["paused"], [])
        [r] = [r for r in page["active"] if r["payee"] == "Cleaner"]
        self.assertEqual((r["cadence"], r["late"]), ("weekly", True))


class NoPayeeSeries(unittest.TestCase):
    def test_a_series_by_bank_text_links_to_its_own_rows(self):
        conn, res = detected(*[("", d, "-15.00", f"STREAMCO.COM {i}") for i, d in enumerate(days("2026-03", 6, 4))],
                             *[("", d, "-15.00", "MUSICCO") for d in days("2026-03", 6, 18)], ("Club", "2026-08-25", "-12.00"))
        self.addCleanup(conn.close)
        page = recurring.page(conn, res, today=date(2026, 10, 16))
        names = sorted(r["name"] for r in page["active"] if r["payee"].startswith("(no payee)"))
        self.assertEqual(names, ["(no payee) MUSICCO", "(no payee) STREAMCO COM"])
        for r in page["active"]:
            if r["payee"].startswith("(no payee)"):
                self.assertEqual(len(listed_keys(conn, r["link"])), 6, r["name"])


if __name__ == "__main__":
    unittest.main()
