"""G13: a link into Transactions lists exactly the rows its figure counts.

A link used to search the rows' text (`q=`), so "Gym" also found "Gym Plus"
and anything that merely mentioned the word. Now it names its payee exactly
(`payee=`) or its rows by key (`key=`). Each family below holds fixture data
with a near-name payee, a refund, a made-up filler and a second payment on the
same day, and checks that the figure on the page equals the rows the link
lists."""
import isolate  # noqa: F401  first: no test writes the real record or cache
import unittest
import urllib.parse
from datetime import date

from finance import api, home, recurring, spending
from finance.db import SYNTHETIC_TYPE
import support
from test_db import row
from test_recurring import ACTIVE as MONTH_ACTIVE, CATEGORIES, ROWS as MONTH_ROWS, record as month_record, results, series

# invented payees: "Gym Plus" and "Gymkhana" contain "Gym", "Magazine Club" contains "Magazine"
PAYEES = ["Gym", "Gym Plus", "Gymkhana", "Magazine", "Magazine Club", "Broker", "Clinic"]


def record(*rows):
    """(day, payee, amount, extra) rows in a record where the payee's name is also its bank text."""
    keyed = []
    for i, (day, payee, amount, extra) in enumerate(rows):
        extra = dict(extra)
        keyed.append(row(f"k{i:02d}", amount, date=day, payee=payee,
                         merchant=extra.pop("merchant", payee.upper() if payee else f"SHOP*{i}"),
                         category=extra.pop("category", "Investments" if payee == "Broker" else "Transport"),
                         **extra))
    return support.build_record(keyed, user=support.user_tables(categories=CATEGORIES), payees=support.payees(PAYEES))


def listed(conn, link):
    """How many rows Transactions lists for a link, as the page asks: transfers hidden unless the link says not."""
    return api.transactions(conn, {"hide_transfers": "1", **link})["total"]


def keys_of(conn, link):
    return sorted(r["key"] for r in api.transactions(conn, {"hide_transfers": "1", "limit": "500", **link})["rows"])


ROWS = [
    # Gym: four payments, a refund (not a payment), a made-up filler (not a payment)
    ("2026-06-05", "Gym", "-50.00", {}), ("2026-07-05", "Gym", "-50.00", {}),
    ("2026-08-05", "Gym", "-50.00", {}), ("2026-09-04", "Gym", "-50.00", {}),
    ("2026-09-06", "Gym", "20.00", {"direction": "In"}),
    ("2026-09-07", "Gym", "-1.00", {"bank_txn_type": SYNTHETIC_TYPE, "flow_class": "External"}),
    # names that contain "Gym" and a statement line that mentions it
    ("2026-08-10", "Gym Plus", "-30.00", {}), ("2026-09-10", "Gym Plus", "-30.00", {}),
    ("2026-09-11", "Gymkhana", "-25.00", {}),
    ("2026-09-12", "Clinic", "-9.00", {"merchant": "CLINIC GYM SHOP"}),
    # Magazine: three payments and a refund, with Magazine Club beside it
    ("2026-01-03", "Magazine", "-9.00", {}), ("2026-04-11", "Magazine", "-9.00", {}),
    ("2026-04-20", "Magazine", "-9.00", {}), ("2026-05-01", "Magazine", "3.00", {"direction": "In"}),
    ("2026-05-02", "Magazine Club", "-14.00", {}),
    # Broker: investing, a Transfer-bucket category that Transactions hides unless told not to
    ("2026-07-10", "Broker", "-500.00", {}), ("2026-08-10", "Broker", "-500.00", {}),
    # no payee: three payments of about 18 dollars, and a different amount
    ("2026-05-15", "", "-18.00", {}), ("2026-06-15", "", "-17.60", {}), ("2026-07-15", "", "-18.20", {}),
    ("2026-07-16", "", "-30.00", {}),
    # a one-off at Clinic, and two payments there on the same day
    ("2026-09-20", "Clinic", "-450.00", {}), ("2026-09-20", "Clinic", "-9.00", {}),
    # a payment the rules gave no payee, whose bank text reads "Gym" and "Magazine"
    ("2026-09-13", "", "-5.00", {"merchant": "Gym"}), ("2026-09-14", "", "-4.00", {"merchant": "Magazine"}),
]
ACTIVE = [series("Gym", 50.0, "2026-09-04"), series("Broker", 500.0, "2026-08-10", bucket="Transfer", category="Investments"),
          series("(no payee, about SGD 18)", 18.0, "2026-07-15", grouped_by="amount", category="Transport")]
STOPPED = [series("Gym Plus", 30.0, "2026-09-10"), series("Magazine Club", 14.0, "2026-05-02")]
TODAY = date(2026, 10, 16)


def eligible(conn, s):
    """The charges the detector reads for one series: its own rows."""
    return {c[3] for c in recurring._charges(conn).get(recurring._series_key(s), [])}


class TransactionsFilters(unittest.TestCase):
    def setUp(self):
        self.conn = record(*ROWS)
        self.addCleanup(self.conn.close)

    def test_payee_matches_the_name_exactly_not_the_text(self):
        self.assertEqual(listed(self.conn, {"payee": "Gym"}), 7)       # the unnamed row's text reads "Gym" too
        self.assertEqual(listed(self.conn, {"payee": "Gym", "named": "1"}), 6)   # only what the rules named
        self.assertEqual(listed(self.conn, {"q": "Gym"}), 7 + 2 + 1 + 1)   # what a search finds
        self.assertEqual(listed(self.conn, {"payee": "gym"}), 0)
        self.assertEqual(listed(self.conn, {"payee": "Gym Plus"}), 2)

    def test_payee_is_the_name_the_stats_give_a_row_with_no_payee(self):
        # a row the rules gave no payee is named by the text its statement printed, as the explorer names it
        self.assertEqual(listed(self.conn, {"payee": "SHOP*17"}), 1)

    def test_key_takes_one_row_or_several(self):
        one = keys_of(self.conn, {"payee": "Magazine", "named": "1"})[0]
        self.assertEqual(keys_of(self.conn, {"key": one}), [one])
        two = keys_of(self.conn, {"payee": "Magazine", "named": "1"})[:2]
        self.assertEqual(keys_of(self.conn, {"key": ",".join(two)}), two)
        self.assertEqual(listed(self.conn, {"key": "nosuchkey"}), 0)

    def test_out_leaves_the_refund_and_money_between_own_accounts(self):
        self.assertEqual(listed(self.conn, {"payee": "Magazine", "named": "1"}), 4)
        self.assertEqual(listed(self.conn, {"payee": "Magazine", "named": "1", "out": "1"}), 3)
        self.conn.execute("UPDATE txn SET flow_class = 'Internal - own accounts' WHERE txn_key = ?",
                          (keys_of(self.conn, {"payee": "Magazine", "named": "1", "out": "1"})[0],))
        self.assertEqual(listed(self.conn, {"payee": "Magazine", "named": "1", "out": "1"}), 2)


class RecurringLinks(unittest.TestCase):
    def setUp(self):
        self.conn = record(*ROWS)
        self.addCleanup(self.conn.close)
        self.page = recurring.page(self.conn, results(ACTIVE, stopped=STOPPED, flagged=["Magazine"]), today=TODAY)

    def test_an_active_or_stopped_series_link_lists_the_series_own_rows(self):
        for section, source in (("active", ACTIVE), ("stopped", STOPPED)):
            for r, s in zip(sorted(self.page[section], key=lambda r: r["payee"]), sorted(source, key=lambda s: s["payee"])):
                with self.subTest(section=section, payee=r["payee"]):
                    self.assertEqual(set(keys_of(self.conn, r["link"])), eligible(self.conn, s))

    def test_a_series_with_a_payee_links_by_that_exact_payee(self):
        gym = next(r for r in self.page["active"] if r["payee"] == "Gym")
        self.assertEqual(gym["link"], {"payee": "Gym", "named": "1", "hide_transfers": "0", "synthetic": "0", "out": "1"})
        # not Gym Plus, not the refund, not the filler, not the unnamed payment whose bank text reads "Gym"
        self.assertEqual(listed(self.conn, gym["link"]), 4)

    def test_investing_is_listed_though_transactions_hides_transfers(self):
        broker = next(r for r in self.page["active"] if r["payee"] == "Broker")
        self.assertEqual(listed(self.conn, broker["link"]), 2)
        self.assertEqual(listed(self.conn, {"payee": "Broker"}), 0)

    def test_a_series_with_no_payee_links_to_its_own_rows_by_key(self):
        nameless = next(r for r in self.page["active"] if r["payee"].startswith("(no payee"))
        self.assertEqual(sorted(nameless["link"]), ["hide_transfers", "key"])
        # the three payments of about 18 dollars, not the 30 dollar one or any other row
        self.assertEqual(listed(self.conn, nameless["link"]), 3)

    def test_an_irregular_payee_lists_the_payments_its_figure_counts(self):
        mag = self.page["irregular"][0]
        self.assertEqual((mag["payee"], mag["payments"]), ("Magazine", 3))
        self.assertEqual(listed(self.conn, mag["link"]), mag["payments"])    # not the refund, not Magazine Club


class ComingUpLinks(unittest.TestCase):
    """A Coming up row's figure is this month's one payment (or the usual amount where none has come), so its
    link lists that payment and nothing else: not the payee's whole history. Paid and different-amount rows
    link to the payment's own rows by key; a due or late row has no payment yet, so it has no link."""

    # the month's fixture (Recurring's own) plus two odd charges: a 3 dollar charge on the day the gym
    # was paid, and a second off-range charge the day after the rent was paid at another amount
    EXTRA = [("Gym", "2026-09-04", "-3.00"), ("Landlord", "2026-09-02", "-200.00")]

    def setUp(self):
        self.conn = month_record(*MONTH_ROWS, *self.EXTRA)
        self.addCleanup(self.conn.close)
        self.res = results(MONTH_ACTIVE)
        self.up = recurring.coming_up(self.conn, self.res, today=TODAY)

    def listed_rows(self, link):
        return api.transactions(self.conn, {"hide_transfers": "1", "limit": "500", **link})

    def test_every_row_shows_a_figure_its_link_lists(self):
        seen = set()
        for r in self.up["rows"]:
            with self.subTest(payee=r["payee"], expected=r["expected"], status=r["status"]):
                seen.add(r["status"])
                if r["status"] in ("paid", "different"):
                    page = self.listed_rows(r["link"])
                    # the figure the row shows is the sum of the rows its link lists
                    self.assertEqual(-page["sum_sgd"], r["paid_amount"])
                    self.assertGreaterEqual(page["total"], 1)
                    # all of them this month's, none of the payee's history
                    self.assertTrue(all(t["date"][:7] == self.up["month"] for t in page["rows"]))
                else:
                    self.assertIsNone(r["link"])
                    self.assertIsNone(r["paid_amount"])
        # the fixture holds every status, so none of the above passed by having nothing to check
        self.assertEqual(seen, {"paid", "different", "due", "late"})

    def test_a_paid_row_links_to_the_one_charge_it_was_paid_by(self):
        gym = next(r for r in self.up["rows"] if r["payee"] == "Gym")
        self.assertEqual(gym["status"], "paid")
        page = self.listed_rows(gym["link"])
        # not the 3 dollar charge beside it, not the three earlier months
        self.assertEqual((page["total"], -page["sum_sgd"]), (1, 5000))
        self.assertEqual(sorted(gym["link"]), ["hide_transfers", "key"])

    def test_a_different_amount_row_links_to_every_charge_it_adds_up(self):
        rent = next(r for r in self.up["rows"] if r["payee"] == "Landlord")
        self.assertEqual((rent["status"], rent["paid_amount"]), ("different", 150000))
        page = self.listed_rows(rent["link"])
        self.assertEqual((page["total"], -page["sum_sgd"]), (2, 150000))

    def test_the_weekly_series_has_a_link_to_each_week_s_own_payment(self):
        weeks = [r for r in self.up["rows"] if r["payee"] == "Cleaner"]
        self.assertGreater(len(weeks), 1)
        self.assertEqual(len({r["link"]["key"] for r in weeks if r["link"]}), len([r for r in weeks if r["link"]]))
        for r in weeks:
            if r["status"] == "paid":
                self.assertEqual(self.listed_rows(r["link"])["total"], 1)

    def test_the_page_and_home_carry_the_same_rows_and_links(self):
        page = recurring.page(self.conn, self.res, today=TODAY)
        self.assertEqual(page["coming_up"]["rows"], self.up["rows"])
        eye = home.needs_your_eye(self.conn, self.res, self.up)
        self.assertIsNotNone(eye)

    def test_a_link_survives_a_trip_through_a_url(self):
        paid = next(r for r in self.up["rows"] if r["status"] == "paid")
        query = dict(urllib.parse.parse_qsl(urllib.parse.urlencode(paid["link"])))
        self.assertEqual(self.listed_rows(query)["total"], 1)


class SpendingLinks(unittest.TestCase):
    def setUp(self):
        self.conn = record(*ROWS)
        self.addCleanup(self.conn.close)
        self.conn.execute("UPDATE txn SET trip_id = (SELECT id FROM trip LIMIT 1) WHERE txn_key = ?",
                          (keys_of(self.conn, {"payee": "Gymkhana"})[0],))

    def link(self, d, scope, extra):
        """The link the page builds: its range, spending only, Singapore only when that is the scope."""
        return {"from": d["months"][0], "to": d["months"][-1], "spend": "1", **({"home": "1"} if scope == "sg" else {}), **extra}

    def test_every_top_payee_lists_as_many_rows_as_its_count(self):
        for scope in ("sg", "all"):
            d = spending.explore(self.conn, {"by": "payee", "scope": scope, "from": "2026-01", "to": "2026-09"})
            self.assertTrue(d["payees"])
            for p in d["payees"]:
                with self.subTest(scope=scope, payee=p["name"]):
                    self.assertEqual(listed(self.conn, self.link(d, scope, {"payee": p["name"]})), p["count"])

    def test_a_payee_named_by_its_bank_text_lists_its_rows_too(self):
        d = spending.explore(self.conn, {"by": "payee", "from": "2026-01", "to": "2026-09"})
        named = [p for p in d["payees"] if p["name"].startswith("SHOP*")]
        self.assertTrue(named)
        for p in named:
            self.assertEqual(listed(self.conn, self.link(d, "sg", {"payee": p["name"]})), p["count"])

    def test_every_biggest_payment_lists_that_one_payment(self):
        d = spending.explore(self.conn, {"from": "2026-01", "to": "2026-09"})
        self.assertEqual(len(d["biggest"]), 5)
        for b in d["biggest"]:
            with self.subTest(payee=b["payee"], amount=b["amount"]):
                rows = api.transactions(self.conn, {"hide_transfers": "1", "key": b["key"]})["rows"]
                self.assertEqual([(r["key"], -r["amount_sgd"]) for r in rows], [(b["key"], b["amount"])])


class HomeLinks(unittest.TestCase):
    def setUp(self):
        self.conn = record(*ROWS)
        self.addCleanup(self.conn.close)

    def one_offs(self, *entries):
        return {"one-offs": {"data": {"one_offs": list(entries)}}}

    def test_the_biggest_one_off_lists_that_one_payment(self):
        # Clinic was paid twice on the day, 450.00 and 9.00; the one-off is the 450.00
        res = {**results(ACTIVE), **self.one_offs(
            {"date": "2026-09-20", "month": "2026-09", "payee": "Clinic", "category": "Transport",
             "amount_minor": 45_000, "multiple": 50.0})}
        up = recurring.coming_up(self.conn, res, today=TODAY)
        one = home.needs_your_eye(self.conn, res, up)["one_off"]
        self.assertEqual(sorted(one["link"]), ["key"])
        rows = api.transactions(self.conn, {"hide_transfers": "1", **one["link"]})["rows"]
        self.assertEqual([r["amount_sgd"] for r in rows], [-45000])
        self.assertEqual(one["amount"], -rows[0]["amount_sgd"])

    def test_a_one_off_the_record_cannot_find_still_searches_its_month(self):
        res = self.one_offs({"date": "2026-09-21", "month": "2026-09", "payee": "Clinic", "category": "Transport",
                             "amount_minor": 45_000, "multiple": 50.0})
        self.assertEqual(home.biggest_one_off(res, "2026-09", self.conn)["link"], {"month": "2026-09", "q": "Clinic"})

    def test_the_rows_with_no_sgd_value_are_one_short_link_however_many_they_are(self):
        # G18: thousands of keys in the address broke the request line; the line names a filter instead
        from test_home import no_fx_record
        conn = no_fx_record(more=3000)
        self.addCleanup(conn.close)
        eye = home.needs_your_eye(conn, {}, {"rows": [], "month": "2026-08"}, today=TODAY)
        line = next(x for x in eye["lines"] if x["kind"] == "missing_fx")
        self.assertLess(len(line["to"]), 100)
        query = dict(urllib.parse.parse_qsl(line["to"].partition("?")[2]))
        self.assertEqual(api.transactions(conn, query)["total"], eye["missing_fx"])
        self.assertEqual(eye["missing_fx"], 3002)

    def test_the_links_survive_a_trip_through_a_url(self):
        # the page sends a link as a query string and the server reads it back
        link = {"key": ",".join(keys_of(self.conn, {"payee": "Magazine", "named": "1"})), "hide_transfers": "0"}
        query = dict(urllib.parse.parse_qsl(urllib.parse.urlencode(link)))
        self.assertEqual(listed(self.conn, query), 4)


if __name__ == "__main__":
    unittest.main()
