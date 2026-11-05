"""G16: the recurring detector on made-up rows: several series per payee, more
cadences, a price change continues a series, paused apart from stopped."""
import isolate  # noqa: F401  first: no test writes the real record or cache
import random
import unittest
from datetime import date, timedelta

from analytics.stats import recurring_detector as rd
from finance.money import to_cents

# invented payees, amounts and bank text throughout


def tx(day, amount, payee="", text=None, category="Subscriptions", account="111-1", bucket="Spend"):
    day = day.isoformat() if isinstance(day, date) else day
    return {"key": f"{payee or text}-{day}-{amount}", "date": day, "month": day[:7],
            "amount_minor": to_cents(-amount), "amount_sgd_minor": to_cents(-amount), "currency": "SGD",
            "flow_class": "External", "synthetic": False, "bucket": bucket, "need_class": "Want",
            "payee": payee, "category": category, "merchant_raw": text if text is not None else payee.upper(),
            "counterparty": "", "memo": "", "account_no": account, "bank_txn_type": "POS"}


def snap(rows, flagged=(), periods=()):
    payees = sorted({r["payee"] for r in rows if r["payee"]})
    return {"meta": {}, "transactions": sorted(rows, key=lambda r: r["date"]),
            "payees": [{"name": p, "recurring": p in flagged} for p in payees],
            "periods": list(periods), "categories": []}


def months(first, count, day, every=1):
    """`count` dates on `day` of the month, every `every` months from `first` (YYYY-MM)."""
    y, m = int(first[:4]), int(first[5:]) - 1
    return [date((y * 12 + m + k * every) // 12, (y * 12 + m + k * every) % 12 + 1, day) for k in range(count)]


def end_on(day):
    """A row elsewhere that makes `day` the record's last."""
    return tx(day, 25.0, "Bookshop", category="Shopping")


def detect(rows, flagged=(), periods=()):
    return rd.run(snap(rows, flagged, periods))["data"]


def of(data, payee):
    return [s for s in data["series"] if s["payee"] == payee]


class PriceChange(unittest.TestCase):
    """P1: a lasting new price is the series' new normal, not a stop."""

    def test_a_price_rise_continues_the_series_at_the_new_price(self):
        rows = ([tx(d, 10.0, "Streamy") for d in months("2025-01", 12, 5)]
                + [tx(d, 13.0, "Streamy") for d in months("2026-01", 4, 5)] + [end_on("2026-04-20")])
        data = detect(rows)
        [s] = of(data, "Streamy")
        self.assertEqual((s["status"], s["median_amount_minor"], s["cadence"]), ("active", 1300, "monthly"))
        self.assertEqual((s["first_seen"], s["last_seen"], s["occurrences"]), ("2025-01-05", "2026-04-05", 16))
        self.assertFalse(s["amount_varies"])
        self.assertEqual(data["stopped"], [])

    def test_a_price_drop_continues_the_series_at_the_new_price(self):
        rows = ([tx(d, 13.0, "Streamy") for d in months("2025-01", 12, 5)]
                + [tx(d, 10.0, "Streamy") for d in months("2026-01", 4, 5)] + [end_on("2026-04-20")])
        [s] = of(detect(rows), "Streamy")
        self.assertEqual((s["status"], s["median_amount_minor"], s["occurrences"]), ("active", 1000, 16))

    def test_two_charges_at_the_new_price_make_it_the_usual_amount(self):
        rows = ([tx(d, 10.0, "Streamy") for d in months("2025-01", 12, 5)]
                + [tx(d, 13.0, "Streamy") for d in months("2026-01", 2, 5)] + [end_on("2026-02-20")])
        [s] = of(detect(rows), "Streamy")
        self.assertEqual((s["status"], s["median_amount_minor"], s["last_seen"]), ("active", 1300, "2026-02-05"))

    def test_a_new_price_seen_once_keeps_the_series_live_at_the_old_amount(self):
        # 46 days after the last S$10 charge, which alone would read paused
        rows = ([tx(d, 10.0, "Streamy") for d in months("2025-01", 12, 5)]
                + [tx("2026-01-05", 13.0, "Streamy"), end_on("2026-01-20")])
        [s] = of(detect(rows), "Streamy")
        self.assertEqual((s["status"], s["median_amount_minor"], s["last_seen"]), ("active", 1000, "2026-01-05"))

    def test_a_price_that_goes_back_to_an_earlier_one_is_still_one_series(self):
        amounts = [10.0] * 12 + [13.0] * 3 + [10.0] * 2
        rows = [tx(d, a, "Streamy") for d, a in zip(months("2025-01", 17, 5), amounts)] + [end_on("2026-05-20")]
        data = detect(rows)
        [s] = of(data, "Streamy")
        self.assertEqual((s["status"], s["median_amount_minor"], s["occurrences"]), ("active", 1000, 17))
        self.assertEqual([x["id"] for x in data["active"]], [s["id"]])

    def test_a_rise_and_a_drop_back_in_halves_of_a_year(self):
        amounts = [20.0] * 6 + [25.0] * 6 + [20.0] * 6
        rows = [tx(d, a, "Phone") for d, a in zip(months("2025-01", 18, 2), amounts)] + [end_on("2026-06-20")]
        [s] = of(detect(rows), "Phone")
        self.assertEqual((s["status"], s["median_amount_minor"], s["occurrences"]), ("active", 2000, 18))

    def test_a_price_that_alternates_month_by_month_is_one_series_that_varies(self):
        amounts = [10.0, 13.0] * 6
        rows = [tx(d, a, "Power") for d, a in zip(months("2025-01", 12, 8), amounts)] + [end_on("2025-12-20")]
        [s] = of(detect(rows), "Power")
        self.assertEqual((s["cadence"], s["status"], s["occurrences"], s["amount_varies"]), ("monthly", "active", 12, True))

    def test_a_price_that_alternates_two_months_at_a_time_is_one_series(self):
        amounts = [10.0, 10.0, 13.0, 13.0] * 3
        rows = [tx(d, a, "Power") for d, a in zip(months("2025-01", 12, 8), amounts)] + [end_on("2025-12-20")]
        [s] = of(detect(rows), "Power")
        self.assertEqual((s["cadence"], s["status"], s["occurrences"]), ("monthly", "active", 12))

    def test_a_price_that_changes_at_every_payment_after_a_steady_run_carries_on(self):
        days = months("2023-01", 10, 14, every=3)
        amounts = [80.0] * 7 + [100.0, 150.0, 195.0]
        rows = [tx(d, a, "Insurer") for d, a in zip(days, amounts)] + [end_on("2025-05-04")]
        [s] = of(detect(rows), "Insurer")
        self.assertEqual((s["status"], s["occurrences"], s["last_seen"], s["amount_varies"]),
                         ("active", 10, "2025-04-14", True))

    def test_random_monthly_price_walks_are_one_series_each(self):
        import random
        for seed in range(40):
            with self.subTest(seed=seed):
                rnd = random.Random(seed)
                price, amounts, days = rnd.choice([9.9, 15.0, 28.0, 60.0, 120.0]), [], []
                for k, d in enumerate(months("2024-01", 24, rnd.randint(1, 28))):
                    if k and rnd.random() < 0.25:
                        price = round(price * rnd.choice([0.8, 0.85, 1.15, 1.2, 1.3, 1 / 1.3]), 2)
                    if 0 < k < 23 and rnd.random() < 0.06:
                        continue                        # a month skipped now and then
                    amounts.append(max(price, 10.0))
                    days.append(d + timedelta(days=rnd.choice([0, 0, 0, 1, 2])))
                rows = [tx(d, a, "Walker") for d, a in zip(days, amounts)] + [end_on(days[-1] + timedelta(days=10))]
                got = of(detect(rows), "Walker")
                self.assertEqual([(s["cadence"], s["status"]) for s in got], [("monthly", "active")],
                                 list(zip(days, amounts)))

    def test_a_small_drift_is_read_from_the_recent_charges(self):
        amounts = [50.0] * 8 + [55.0] * 6
        rows = [tx(d, a, "Power") for d, a in zip(months("2025-01", 14, 12), amounts)] + [end_on("2026-02-20")]
        [s] = of(detect(rows), "Power")
        self.assertEqual(s["median_amount_minor"], 5500)


class Cadences(unittest.TestCase):
    """P6 and P7: every cadence a bill really keeps to."""

    def test_fortnightly(self):
        days = [date(2026, 1, 2) + timedelta(days=14 * k) for k in range(10)]
        [s] = of(detect([tx(d, 40.0, "Cleaner") for d in days] + [end_on(days[-1])]), "Cleaner")
        self.assertEqual((s["cadence"], s["cadence_days"], s["status"]), ("fortnightly", 14.0, "active"))

    def test_every_two_months(self):
        days = months("2026-01", 5, 10, every=2)
        [s] = of(detect([tx(d, 60.0, "Water") for d in days] + [end_on("2026-09-20")]), "Water")
        self.assertEqual((s["cadence"], s["occurrences"]), ("two-monthly", 5))

    def test_half_yearly(self):
        days = months("2024-02", 4, 1, every=6)
        [s] = of(detect([tx(d, 300.0, "Insurer") for d in days] + [end_on("2025-09-01")]), "Insurer")
        self.assertEqual((s["cadence"], s["status"]), ("half-yearly", "active"))

    def test_rent_paid_in_two_halves_is_twice_a_month(self):
        days = sorted(months("2026-01", 6, 1) + months("2026-01", 6, 15))
        # a half paid a day or two late now and then, as when the 1st is a Sunday
        days = [d + timedelta(days=2) if d in (date(2026, 2, 1), date(2026, 3, 15)) else d for d in days]
        [s] = of(detect([tx(d, 1000.0, "Landlord", category="Rent") for d in days] + [end_on("2026-06-20")]), "Landlord")
        self.assertEqual((s["cadence"], s["occurrences"], s["status"]), ("twice-monthly", 12, "active"))
        self.assertEqual(s["monthly_cost_minor"], 200000)

    def test_a_new_price_twice_a_month_is_judged_on_its_two_days(self):
        # S$500 on the 1st and the 10th, January to July; then S$650 on 1 August, 22 days
        # after the 10th, which is the next of its two days, not a gap out of the band
        days = sorted(months("2025-01", 7, 1) + months("2025-01", 7, 10))
        rows = [tx(d, 500.0, "Landlord", category="Rent") for d in days] + [
            tx("2025-08-01", 650.0, "Landlord", category="Rent"), end_on("2025-08-15")]
        data = detect(rows)
        [s] = of(data, "Landlord")
        self.assertEqual((s["cadence"], s["status"], s["last_seen"]), ("twice-monthly", "active", "2025-08-01"))
        self.assertEqual(data["totals"]["monthly_committed_minor"], 100000)

    def test_a_price_change_twice_a_month_is_one_series(self):
        # rent in halves on the 3rd and the 14th: S$25, then S$32 for six months, then S$25
        # again; the halves of one month sit 11 days apart and are two expected payments, not one
        days = sorted(months("2024-05", 20, 3) + months("2024-05", 20, 14))
        amounts = [25.0 if not 17 <= k < 29 else 32.0 for k in range(len(days))]
        rows = [tx(d, a, "Landlord", category="Rent") for d, a in zip(days, amounts)] + [end_on("2025-12-20")]
        [s] = of(detect(rows), "Landlord")
        self.assertEqual((s["cadence"], s["status"], s["occurrences"], s["median_amount_minor"]),
                         ("twice-monthly", "active", 40, 2500))

    def test_the_two_usual_days_are_the_pair_nearest_every_payment(self):
        # Audit: usual_pair counts each day of the month once; it must pick the pair the plain
        # sum over every payment picks, ties to the earlier pair, whatever the days
        from finance.cadence import _apart, usual_pair

        def plain(days):
            costs = {(x, y): sum(min(_apart(d, x, 31), _apart(d, y, 31)) for d in days)
                     for x in range(1, 32) for y in range(x + 1, 32)}
            return min(costs, key=lambda p: (costs[p], p))
        rnd = random.Random(16)
        for _ in range(300):
            days = [rnd.randint(1, 31) for _ in range(rnd.randint(1, 40))]
            self.assertEqual(usual_pair(days), plain(days), days)

    def test_on_schedule_answers_for_every_cadence(self):
        d = date.fromisoformat
        cases = [("weekly", ["2025-01-03", "2025-01-10", "2025-01-17"], "2025-01-24", 1),
                 ("fortnightly", ["2025-01-03", "2025-01-17"], "2025-01-31", 1),
                 ("twice-monthly", ["2025-06-01", "2025-06-10", "2025-07-01", "2025-07-10"], "2025-08-01", 1),
                 ("twice-monthly", ["2025-06-01", "2025-06-10", "2025-07-01", "2025-07-10"], "2025-08-10", 2),
                 ("twice-monthly", ["2025-06-01", "2025-06-10", "2025-07-01", "2025-07-10"], "2025-07-20", None),
                 ("monthly", ["2025-05-05", "2025-06-05", "2025-07-05"], "2025-08-06", 1),
                 ("monthly", ["2025-05-05", "2025-06-05", "2025-07-05"], "2025-09-04", 2),
                 ("monthly", ["2025-05-05", "2025-06-05", "2025-07-05"], "2025-07-21", None),
                 ("quarterly", ["2025-01-15", "2025-04-15"], "2025-07-17", 1),
                 ("yearly", ["2023-03-10", "2024-03-11"], "2025-03-09", 1)]
        for cadence, paid, day, want in cases:
            with self.subTest(cadence=cadence, day=day):
                got = rd.on_schedule(cadence, [d(x) for x in paid], d(day))
                self.assertEqual(got[0] if got else None, want)

    def test_a_true_fortnight_is_not_twice_a_month(self):
        days = [date(2026, 1, 9) + timedelta(days=14 * k) for k in range(14)]
        [s] = of(detect([tx(d, 80.0, "Tutor") for d in days] + [end_on(days[-1])]), "Tutor")
        self.assertEqual(s["cadence"], "fortnightly")

    def test_a_yearly_bill_seen_twice_counts_when_the_rules_mark_it_recurring(self):
        rows = [tx("2024-03-10", 120.0, "Domain"), tx("2025-03-11", 120.0, "Domain"), end_on("2025-06-01")]
        [s] = of(detect(rows, flagged=["Domain"]), "Domain")
        self.assertEqual((s["cadence"], s["occurrences"], s["status"]), ("yearly", 2, "active"))
        self.assertEqual(of(detect(rows), "Domain"), [])

    def test_two_payments_are_not_enough_for_a_monthly_bill_even_when_marked(self):
        rows = [tx("2025-03-10", 120.0, "Gym"), tx("2025-04-10", 120.0, "Gym"), end_on("2025-04-20")]
        self.assertEqual(of(detect(rows, flagged=["Gym"]), "Gym"), [])

    def test_one_late_quarterly_payment_costs_one_gap(self):
        days = ["2025-01-15", "2025-04-15", "2025-07-15", "2025-10-29", "2026-01-15"]
        [s] = of(detect([tx(d, 300.0, "Insurer") for d in days] + [end_on("2026-02-01")]), "Insurer")
        self.assertEqual((s["cadence"], s["occurrences"], s["first_seen"]), ("quarterly", 5, "2025-01-15"))

    def test_a_billing_day_that_moves_keeps_the_series(self):
        days = months("2025-01", 6, 5) + months("2025-07", 6, 20)
        [s] = of(detect([tx(d, 30.0, "Phone") for d in days] + [end_on("2025-12-25")]), "Phone")
        self.assertEqual((s["cadence"], s["occurrences"]), ("monthly", 12))

    def test_a_quarterly_bill_is_not_a_monthly_one_that_skips(self):
        days = months("2025-01", 5, 15, every=3)
        [s] = of(detect([tx(d, 300.0, "Insurer") for d in days] + [end_on("2026-01-20")]), "Insurer")
        self.assertEqual(s["cadence"], "quarterly")


class SeveralSeries(unittest.TestCase):
    """P8: one payee, several series, each with a stable id and its own name."""

    def test_a_subscription_and_its_top_ups_are_one_series(self):
        sub = [tx(d, 28.0, "Chatbot") for d in months("2025-03", 8, 3)]
        top = [tx(date(2025, 3 + k, day), 10.0, "Chatbot")
               for k, pair in enumerate([(7, 22), (11, 13), (5, 27), (16, 19), (8, 30), (12, 25), (6, 21), (9, 28)])
               for day in pair]
        [s] = of(detect(sub + top + [end_on("2025-10-31")]), "Chatbot")
        self.assertEqual((s["median_amount_minor"], s["cadence"], s["name"]), (2800, "monthly", "Chatbot"))
        self.assertEqual(s["occurrences"], 8)

    def test_two_subscriptions_at_one_payee_are_two_series(self):
        rows = ([tx(d, 3.98, "Fruitco") for d in months("2025-01", 8, 12)]
                + [tx(d, 10.98, "Fruitco") for d in months("2025-01", 8, 20)] + [end_on("2025-08-25")])
        data = detect(rows, flagged=["Fruitco"])
        got = sorted((s["median_amount_minor"], s["cadence"], s["name"], s["id"]) for s in of(data, "Fruitco"))
        self.assertEqual(got, [(398, "monthly", "Fruitco (S$3.98)", "payee:Fruitco:398"),
                               (1098, "monthly", "Fruitco (S$10.98)", "payee:Fruitco:1098")])

    def test_a_second_plan_that_starts_during_the_first_is_its_own_series(self):
        rows = ([tx(d, 10.0, "Fruitco") for d in months("2024-01", 20, 12)]
                + [tx(d, 13.0, "Fruitco") for d in months("2025-06", 3, 28)] + [end_on("2025-08-30")])
        got = sorted((s["median_amount_minor"], s["cadence"]) for s in of(detect(rows), "Fruitco"))
        self.assertEqual(got, [(1000, "monthly"), (1300, "monthly")])

    def test_two_bills_never_share_one_payment(self):
        # S$40 monthly January to June and S$100 quarterly, both on the 1st; then S$60 on
        # 1 July and S$76 on 1 August, each of which either bill could take as a new price
        rows = ([tx(d, 40.0, "Twobill") for d in months("2025-01", 6, 1)]
                + [tx(d, 100.0, "Twobill") for d in ("2024-11-01", "2025-02-01", "2025-05-01")]
                + [tx("2025-07-01", 60.0, "Twobill"), tx("2025-08-01", 76.0, "Twobill"), end_on("2025-08-20")])
        got = {s["cadence"]: s for s in of(detect(rows), "Twobill")}
        self.assertEqual(sorted(got), ["monthly", "quarterly"])
        # each later charge is one bill's: the monthly one, a cadence on from June
        self.assertEqual((got["monthly"]["last_seen"], got["monthly"]["occurrences"]), ("2025-08-01", 8))
        self.assertEqual(got["quarterly"]["last_seen"], "2025-05-01")
        self.assertEqual(got["quarterly"]["occurrences"], 3)

    def test_ids_are_the_same_on_every_run(self):
        rows = ([tx(d, 3.98, "Fruitco") for d in months("2025-01", 8, 12)]
                + [tx(d, 10.98, "Fruitco") for d in months("2025-01", 8, 20)] + [end_on("2025-08-25")])
        first = [s["id"] for s in detect(rows, flagged=["Fruitco"])["series"]]
        again = [s["id"] for s in detect(list(reversed(rows)), flagged=["Fruitco"])["series"]]
        self.assertEqual(first, again)
        self.assertEqual(len(set(first)), len(first))

    def test_a_price_rise_keeps_its_id(self):
        before = [tx(d, 10.0, "Streamy") for d in months("2025-01", 12, 5)]
        after = before + [tx(d, 13.0, "Streamy") for d in months("2026-01", 3, 5)]
        ids = [[s["id"] for s in of(detect(rows + [end_on(day)]), "Streamy")]
               for rows, day in ((before, "2025-12-20"), (after, "2026-03-20"))]
        self.assertEqual(ids, [["payee:Streamy:1000"], ["payee:Streamy:1000"]])


class Floor(unittest.TestCase):
    """P10: the S$10 floor on every path; amount_varies from the amounts' spread."""

    def test_a_flat_charge_under_the_floor_is_no_series(self):
        rows = [tx(d, 3.98, "Cloudbox") for d in months("2025-01", 8, 12)] + [end_on("2025-08-25")]
        self.assertEqual(of(detect(rows), "Cloudbox"), [])

    def test_a_flat_charge_under_the_floor_marked_recurring_does_not_vary(self):
        rows = [tx(d, 3.98, "Cloudbox") for d in months("2025-01", 8, 12)] + [end_on("2025-08-25")]
        [s] = of(detect(rows, flagged=["Cloudbox"]), "Cloudbox")
        self.assertFalse(s["amount_varies"])

    def test_the_floor_is_on_the_run_the_whole_group_retry_reports(self):
        # six flat S$3.98 charges, then years later three unrelated bigger ones: the
        # group's latest charges clear the floor, but the run that fits is the S$3.98 one
        rows = ([tx(d, 3.98, "Cloudbox") for d in months("2023-01", 6, 12)]
                + [tx("2025-05-02", 40.0, "Cloudbox"), tx("2025-09-14", 100.0, "Cloudbox"),
                   tx("2026-01-20", 250.0, "Cloudbox"), end_on("2026-02-01")])
        self.assertEqual(of(detect(rows), "Cloudbox"), [])
        [s] = of(detect(rows, flagged=["Cloudbox"]), "Cloudbox")
        self.assertEqual(s["median_amount_minor"], 398)

    def test_the_floor_holds_on_the_amount_a_falling_price_ends_at(self):
        days = months("2025-01", 12, 6)
        amounts = [40.0] * 6 + [25.0, 15.0, 9.0, 5.4, 3.2, 1.9]
        rows = [tx(d, a, "Fader") for d, a in zip(days, amounts)] + [end_on("2025-12-20")]
        self.assertTrue(all(s["median_amount_minor"] >= rd.MIN_AMOUNT_MINOR for s in of(detect(rows), "Fader")))
        flagged = of(detect(rows, flagged=["Fader"]), "Fader")
        self.assertEqual(len(flagged), 1)

    def test_a_bill_that_moves_varies(self):
        amounts = [30.0, 55.0, 42.0, 80.0, 35.0, 60.0, 48.0, 71.0]
        rows = [tx(d, a, "Telco") for d, a in zip(months("2025-01", 8, 2), amounts)] + [end_on("2025-08-20")]
        [s] = of(detect(rows), "Telco")
        self.assertTrue(s["amount_varies"])
        self.assertEqual(s["cadence"], "monthly")

    def test_a_flat_bill_does_not_vary(self):
        rows = [tx(d, 50.0, "Gym") for d in months("2025-01", 8, 2)] + [end_on("2025-08-20")]
        [s] = of(detect(rows), "Gym")
        self.assertFalse(s["amount_varies"])


class PausedAndStopped(unittest.TestCase):
    """P11: silent for one and a half to three cadences is paused, longer is stopped."""

    def rows(self, end):
        return [tx(d, 50.0, "Gym") for d in months("2025-01", 6, 5)] + [end_on(end)]

    def test_paused(self):
        data = detect(self.rows("2025-08-01"))       # 57 days after 5 June
        [s] = of(data, "Gym")
        self.assertEqual((s["status"], s["paused"], s["stopped"]), ("paused", True, False))
        self.assertEqual([x["id"] for x in data["paused"]], [s["id"]])
        self.assertNotIn(s["id"], [x["id"] for x in data["active"] + data["stopped"]])
        self.assertEqual((data["totals"]["paused"], data["totals"]["monthly_committed_minor"]), (1, 0))

    def test_active_before_one_and_a_half_cadences(self):
        [s] = of(detect(self.rows("2025-07-15")), "Gym")
        self.assertEqual(s["status"], "active")

    def test_stopped_after_three_cadences(self):
        [s] = of(detect(self.rows("2025-09-15")), "Gym")
        self.assertEqual((s["status"], s["stopped"]), ("stopped", True))

    def test_a_missed_rent_half_is_not_a_pause(self):
        # paid on the 1st and 15th to 1 June; nothing on 15 June; the record ends on 28 June
        days = sorted(months("2026-01", 6, 1) + months("2026-01", 5, 15))
        [s] = of(detect([tx(d, 1000.0, "Landlord", category="Rent") for d in days] + [end_on("2026-06-28")]), "Landlord")
        self.assertEqual((s["cadence"], s["status"]), ("twice-monthly", "active"))

    def test_a_missed_week_is_not_a_pause(self):
        days = [date(2026, 1, 2) + timedelta(days=7 * k) for k in range(12)]
        [s] = of(detect([tx(d, 60.0, "Cleaner") for d in days] + [end_on(days[-1] + timedelta(days=16))]), "Cleaner")
        self.assertEqual((s["cadence"], s["status"]), ("weekly", "active"))

    def test_a_short_cadence_pauses_after_a_month_and_stops_after_two(self):
        days = [date(2026, 1, 2) + timedelta(days=7 * k) for k in range(12)]
        rows = [tx(d, 60.0, "Cleaner") for d in days]
        for gap, status in ((30, "active"), (31, "paused"), (60, "paused"), (61, "stopped")):
            with self.subTest(gap=gap):
                [s] = of(detect(rows + [end_on(days[-1] + timedelta(days=gap))]), "Cleaner")
                self.assertEqual(s["status"], status)

    def test_judged_on_the_series_own_account(self):
        # the gym is paid from an account whose statements end on 30 June; a card runs to September
        rows = [tx(d, 50.0, "Gym", account="222-2") for d in months("2025-01", 6, 5)] + [end_on("2025-09-15")]
        periods = [{"account_no": "222-2", "period_end": "2025-06-30"}]
        [s] = of(detect(rows, periods=periods), "Gym")
        self.assertEqual((s["status"], s["as_of"], s["days_since_last"]), ("active", "2025-06-30", 25))


class Habit(unittest.TestCase):
    """Where one buys in, one amount level and the cadences the detector always had."""

    def test_a_stall_at_one_price_every_two_months_is_not_a_bill(self):
        rows = ([tx(d, 12.0, "Noodle Stall", category="Eating out") for d in months("2025-01", 5, 4, every=2)]
                + [tx(f"2025-{m:02d}-{d:02d}", a, "Noodle Stall", category="Eating out")
                   for m, d, a in ((1, 20, 6.0), (2, 9, 22.5), (3, 15, 7.0), (4, 2, 25.1), (6, 11, 5.0), (8, 28, 18.0))]
                + [end_on("2025-09-20")])
        self.assertEqual(of(detect(rows), "Noodle Stall"), [])

    def test_a_second_price_at_a_ride_app_is_not_a_series(self):
        rides = [tx(date(2025, 1, 1) + timedelta(days=3 * k), 9.0 + (k % 3), "Ridely", category="Transport")
                 for k in range(100)]
        odd = [tx(d, 26.6, "Ridely", category="Transport") for d in ("2025-02-16", "2025-08-15", "2026-02-10")]
        self.assertEqual(of(detect(rides + odd + [end_on("2026-02-20")]), "Ridely"), [])


class NoPayee(unittest.TestCase):
    """P12: rows with no payee group by their bank text, never by amount alone."""

    def test_two_merchants_at_one_amount_are_two_series(self):
        rows = ([tx(d, 15.0, text=f"STREAMCO.COM {d.month}0{d.month}") for d in months("2025-01", 6, 4)]
                + [tx(d, 15.0, text="MUSICCO P0123") for d in months("2025-01", 6, 18)] + [end_on("2025-06-25")])
        data = detect(rows)
        got = sorted((s["payee"], s["cadence"], s["occurrences"], s["grouped_by"]) for s in data["series"]
                     if s["grouped_by"] != "payee")
        self.assertEqual(got, [("(no payee) MUSICCO", "monthly", 6, "text"),
                               ("(no payee) STREAMCO COM", "monthly", 6, "text")])

    def test_bank_text_in_any_script_groups_a_series(self):
        rows = ([tx(d, 38.0, text="\u4e2d\u534e\u7535\u529b\u516c\u53f8 0123") for d in months("2025-01", 6, 9)]
                + [tx(d, 25.0, text="\u0e01\u0e32\u0e23\u0e44\u0e1f\u0e1f\u0e49\u0e32 99") for d in months("2025-01", 6, 20)]
                + [end_on("2025-06-25")])
        got = sorted((s["payee"], s["occurrences"]) for s in detect(rows)["series"] if s["grouped_by"] == "text")
        self.assertEqual(got, [("(no payee) \u0e01\u0e32\u0e23\u0e44\u0e1f\u0e1f\u0e49\u0e32", 6),
                               ("(no payee) \u4e2d\u534e\u7535\u529b\u516c\u53f8", 6)])

    def test_different_merchants_never_make_a_series_together(self):
        texts = ["SHOP ONE", "SHOP TWO", "CAFE THREE", "BAR FOUR", "PARK FIVE", "BOOK SIX"]
        rows = [tx(d, 20.0, text=t) for d, t in zip(months("2025-01", 6, 10), texts)] + [end_on("2025-06-25")]
        self.assertEqual([s for s in detect(rows)["series"] if s["grouped_by"] != "payee"], [])


if __name__ == "__main__":
    unittest.main()
