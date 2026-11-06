"""server/finance/api.py: what the app's endpoints answer."""
import isolate  # noqa: F401  first: no test writes the real record or cache
import unittest
from contextlib import closing

from finance import api, db
from finance.money import stat_cents
import support
from test_db import SNAP, block, parsed, row


def loaded():
    return support.build_record([
        row("k1", "-12.50"), row("k2", "-17.00", date="2026-08-20", merchant="KOPITIAM",
                                 payee="", category=""),
        row("k3", "3000.00", date="2026-08-25", direction="In", category="Salary", payee="")],
        payees=support.payees(["Grab"]))


class TransactionsTest(unittest.TestCase):
    def setUp(self):
        self.conn = loaded()
        self.addCleanup(self.conn.close)

    def test_newest_first_with_totals(self):
        page = api.transactions(self.conn, {})
        self.assertEqual([r["key"] for r in page["rows"]], ["k3", "k2", "k1"])
        self.assertEqual((page["total"], page["sum_sgd"]), (3, 297_050))

    def test_money_in_and_out_add_up_to_the_net(self):
        # G13: the tiles above the list; money out is negative, and a filter narrows both
        page = api.transactions(self.conn, {})
        self.assertEqual(page["in_sgd"] + page["out_sgd"], page["sum_sgd"])
        self.assertGreater(page["in_sgd"], 0)
        self.assertLess(page["out_sgd"], 0)
        none = api.transactions(self.conn, {"month": "2026-07"})
        self.assertEqual((none["total"], none["in_sgd"], none["out_sgd"], none["sum_sgd"]), (0, 0, 0, 0))

    def test_each_day_on_the_page_carries_its_whole_days_totals(self):
        # G13: a day split across two pages shows the same totals on both, and the
        # second page's first day says it continues
        conn = db.connect(":memory:")
        self.addCleanup(conn.close)
        db.import_user_tables(conn, {k: [dict(x) for x in v] for k, v in SNAP.items()})
        db.load_parsed(conn, parsed([block()], [
            row("a1", "-10.00", date="2026-08-21"), row("a2", "-5.00", date="2026-08-21"),
            row("a3", "20.00", date="2026-08-21", direction="In"), row("b1", "-7.00", date="2026-08-20"),
            row("t1", "-40.00", date="2026-08-21", flow_class="Internal - own account", merchant="", payee="")]))
        # a transfer on the same day is in the day's totals only while transfers are shown
        hidden = {"hide_transfers": "1"}
        day = {"date": "2026-08-21", "count": 3, "in_sgd": 2000, "out_sgd": -1500}
        shown = api.transactions(conn, {"limit": "1"})["days"][0]
        self.assertEqual((shown["count"], shown["out_sgd"]), (4, -5500))
        first = api.transactions(conn, {"limit": "2", **hidden})
        second = api.transactions(conn, {"limit": "2", "offset": "2", **hidden})
        # the first page ends inside the day, so it says the day goes on; the second finishes it
        self.assertEqual(first["days"], [{**day, "continued": False, "continues": True}])
        self.assertEqual(second["days"], [
            {**day, "continued": True, "continues": False},
            {"date": "2026-08-20", "count": 1, "in_sgd": 0, "out_sgd": -700, "continued": False,
             "continues": False}])
        # a page that ends exactly at the end of a day does not continue it
        edge = api.transactions(conn, {"limit": "3", **hidden})
        self.assertEqual([(d["date"], d["continues"]) for d in edge["days"]], [("2026-08-21", False)])
        # a day spread over three pages continues on the middle one as well
        middle = api.transactions(conn, {"limit": "1", "offset": "1", **hidden})
        self.assertEqual((middle["days"][0]["continued"], middle["days"][0]["continues"]), (True, True))
        # a page that starts on a new day does not continue, and the filters narrow the totals
        third = api.transactions(conn, {"limit": "1", "offset": "3", **hidden})
        self.assertEqual([d["continued"] for d in third["days"]], [False])
        self.assertEqual(api.transactions(conn, {"month": "2026-07"})["days"], [])

    def test_a_made_up_wallet_leg_is_listed_only_with_transfers(self):
        # G13: the data-quality chart counts every made-up row, so its month link asks for transfers too
        conn = db.connect(":memory:")
        self.addCleanup(conn.close)
        db.import_user_tables(conn, {k: [dict(x) for x in v] for k, v in SNAP.items()})
        db.load_parsed(conn, parsed([block()], [
            row("w1", "-5.00", flow_class="Internal - YouTrip", bank_txn_type=db.SYNTHETIC_TYPE,
                merchant="", payee="", category="")]))
        counted = conn.execute("SELECT COUNT(*) FROM v_txn WHERE is_synthetic = 1 AND month = '2026-08'").fetchone()[0]
        self.assertEqual(api.transactions(conn, {"month": "2026-08", "hide_transfers": "0"})["total"], counted)
        self.assertEqual(api.transactions(conn, {"month": "2026-08", "hide_transfers": "1"})["total"], 0)

    def test_filters(self):
        self.assertEqual(api.transactions(self.conn, {"category": "Salary"})["total"], 1)
        self.assertEqual(api.transactions(self.conn, {"category": "(none)"})["total"], 1)
        self.assertEqual(api.transactions(self.conn, {"q": "kopi"})["rows"][0]["key"], "k2")
        self.assertEqual(api.transactions(self.conn, {"month": "2026-07"})["total"], 0)

    def test_a_row_with_no_merchant_shows_the_next_text_it_prints(self):
        def text():
            return next(r for r in api.transactions(self.conn, {})["rows"] if r["key"] == "k1")["merchant"]
        self.conn.execute("UPDATE txn SET merchant_raw = '', counterparty = '', memo = 'Gift', "
                          "bank_txn_type = 'Advice' WHERE txn_key = 'k1'")
        self.assertEqual(text(), "Gift")
        self.conn.execute("UPDATE txn SET memo = '' WHERE txn_key = 'k1'")
        self.assertEqual(text(), "Advice")

    def test_hide_transfers_leaves_out_own_moves_and_transfer_categories(self):
        # G13: a top-up between own accounts, and a row in a Transfer category
        conn = db.connect(":memory:")
        self.addCleanup(conn.close)
        snap = {k: [dict(x) for x in v] for k, v in SNAP.items()}
        snap["categories"].append({"name": "Wallet top-up", "bucket": "Transfer", "group": "Savings",
                                   "need_class": "Transfer", "sort_order": 900})
        db.import_user_tables(conn, snap)
        db.load_parsed(conn, parsed([block()], [
            row("meal", "-12.50"),
            row("topup", "-50.00", flow_class="Internal - PayLah", merchant="", memo="TOP-UP TO PAYLAH!",
                payee="", category="Wallet top-up"),
            row("moved", "-20.00", flow_class="Internal - own account", merchant="", payee="", category=""),
            row("sent", "-30.00", merchant="", counterparty="A FRIEND", payee="", category="Wallet top-up")]))
        everything = api.transactions(conn, {})
        hidden = api.transactions(conn, {"hide_transfers": "1"})
        self.assertEqual((everything["total"], hidden["total"]), (4, 1))
        self.assertEqual(([r["key"] for r in hidden["rows"]], hidden["sum_sgd"]), (["meal"], -1250))

    def test_rows_carry_the_account_display_name_a_bank_and_a_description(self):
        self.conn.execute("UPDATE txn SET merchant_raw = '', bank_txn_type = 'Interest Earned', "
                          "memo = 'VALUE DATE : 01/08/2026' WHERE txn_key = 'k3'")
        rows = {r["key"]: r for r in api.transactions(self.conn, {})["rows"]}
        self.assertEqual((rows["k1"]["account"], rows["k1"]["bank"]), ("POSB", "posb"))
        self.assertEqual((rows["k1"]["payee"], rows["k1"]["description"]), ("Grab", ""))
        self.assertEqual((rows["k3"]["description"], rows["k3"]["raw"]), ("Interest", "VALUE DATE : 01/08/2026"))
        [account] = api.transactions(self.conn, {})["accounts"]
        self.assertEqual({k: account[k] for k in ("label", "bank")}, {"label": "POSB", "bank": "posb"})

    def test_no_full_account_number_leaves_in_an_account_name(self):
        self.conn.execute("UPDATE account SET name = 'Savings 111-11111-1'")
        page = api.transactions(self.conn, {})
        self.assertEqual({r["account"] for r in page["rows"]}, {"Savings"})
        self.assertNotIn("111-11111-1", str(page["accounts"][0]["label"]))
        # G13: the filter's value is the account's id, never a name that holds its number
        self.assertNotIn("11111", str(page["accounts"]))

    def test_the_account_filter_takes_the_value_the_list_gives(self):
        [account] = api.transactions(self.conn, {})["accounts"]
        self.assertEqual(api.transactions(self.conn, {"account": account["value"]})["total"], 3)
        self.assertEqual(api.transactions(self.conn, {"account": str(int(account["value"]) + 1)})["total"], 0)
        with self.assertRaises(ValueError):
            api.transactions(self.conn, {"account": "POSB"})

    def test_the_hover_masks_the_owners_own_account_numbers(self):
        self.conn.execute("UPDATE txn SET merchant_raw = '', memo = 'FROM 111-11111-1:IB' WHERE txn_key = 'k3'")
        row = next(r for r in api.transactions(self.conn, {})["rows"] if r["key"] == "k3")
        self.assertEqual((row["raw"], row["merchant"]), ("FROM •• 1111:IB", "FROM •• 1111:IB"))

    def test_paging_is_bounded(self):
        page = api.transactions(self.conn, {"limit": "100000", "offset": "-5"})
        self.assertEqual(len(page["rows"]), 3)
        self.assertEqual(api.transactions(self.conn, {"limit": "1", "offset": "1"})["rows"][0]["key"], "k2")

    def test_a_bad_number_is_a_value_error(self):
        with self.assertRaises(ValueError):
            api.transactions(self.conn, {"limit": "lots"})


class TripsTest(unittest.TestCase):
    def test_spend_per_trip(self):
        conn = db.connect(":memory:")
        self.addCleanup(conn.close)
        db.import_user_tables(conn, {k: [dict(x) for x in v] for k, v in SNAP.items()})
        db.load_parsed(conn, parsed([block()], [row("k1", "-12.50", merchant_country="MYS")]),
                       trips_by_key={"k1": "Trip A"})
        self.assertEqual(api.trips(conn)["trips"][0]["spend_sgd"], 1250)


def on_a_trip():
    """Trip A (five days) with a meal, a taxi, a refund, a transfer, a top-up
    synthetic filler and a foreign row with no FX rate, all linked to it."""
    snap = support.user_tables()
    snap["categories"] += [
        {"name": "Eating out", "bucket": "Spend", "group": "Variable", "need_class": "Want", "sort_order": 20},
        {"name": "Transfers", "bucket": "Transfer", "group": "Savings", "need_class": "Transfer",
         "sort_order": 900}]
    away = dict(merchant_country="MYS")
    eur = block(period="222 · EUR · 2026-08", account="Wallet", account_no="222", currency="EUR",
                fx_to_sgd="", closing_sgd="")
    rows = [row("meal", "-40.00", category="Eating out", **away),
            row("taxi", "-25.00", **away),
            row("refund", "5.00", direction="In", **away),                         # lowers the trip
            row("sent", "-300.00", category="Transfers", payee="", **away),        # not spending
            row("filler", "-9.00", bank_txn_type="YouTrip Unprinted", **away),     # synthetic
            row("nofx", "-7.00", account="Wallet", account_no="222", currency="EUR",
                period="222 · EUR · 2026-08", **away),                             # no SGD value
            row("home", "-12.00", date="2026-08-20")]                              # not on the trip
    return support.build_record(rows, user=snap, blocks=[block(), eur],
                                trips_by_key={k: "Trip A" for k in ("meal", "taxi", "refund", "sent", "filler", "nofx")})


class TripSpendingTest(unittest.TestCase):
    """G13: the trips table and the travel stat count one thing, spending."""

    def setUp(self):
        self.conn = on_a_trip()
        self.addCleanup(self.conn.close)
        self.trip = api.trips(self.conn, long_stay=60)["trips"][0]

    def test_a_transfer_and_a_filler_are_not_spending_and_a_refund_lowers_it(self):
        self.assertEqual((self.trip["rows"], self.trip["spend_sgd"]), (3, 6000))

    def test_the_table_and_the_travel_stat_agree(self):
        from analytics.stats import travel
        stat = next(t for t in travel.run(db.snapshot(self.conn))["data"]["trips"] if t["name"] == "Trip A")
        self.assertEqual((stat["rows"], stat_cents(stat, "spend")),
                         (self.trip["rows"], self.trip["spend_sgd"]))
        self.assertEqual(stat_cents(stat, "spend_per_day"), self.trip["per_day"])

    def test_a_half_cent_day_rate_rounds_the_same_way_on_the_page_and_in_the_stat(self):
        from analytics.stats import travel
        # S$101.25 over two days is S$50.625 a day: half away from zero in both
        self.conn.execute("UPDATE txn SET amount_minor = -8125 WHERE txn_key = 'meal'")
        self.conn.execute("UPDATE trip SET start_date = '2026-08-03', end_date = '2026-08-04'")
        trip = api.trips(self.conn, long_stay=60)["trips"][0]
        stat = next(t for t in travel.run(db.snapshot(self.conn))["data"]["trips"] if t["name"] == "Trip A")
        self.assertEqual((trip["spend_sgd"], trip["days"], trip["per_day"]), (10125, 2, 5063))
        self.assertEqual(stat["spend_per_day_minor"], 5063)

    def test_the_trip_opens_the_rows_it_counts(self):
        page = api.transactions(self.conn, {"trip": "Trip A", "spend": "1"})
        self.assertEqual(sorted(r["key"] for r in page["rows"]), ["meal", "refund", "taxi"])
        self.assertEqual((page["total"], -page["sum_sgd"]), (self.trip["rows"], self.trip["spend_sgd"]))
        everything = api.transactions(self.conn, {"trip": "Trip A"})
        self.assertEqual(everything["total"], 6)
        self.assertEqual(api.transactions(self.conn, {"trip": "Nowhere"})["total"], 0)

    def test_days_cost_per_day_and_the_top_two_categories(self):
        t = self.trip
        self.assertEqual((t["days"], t["per_day"], t["long_stay"]), (5, 1200, False))
        self.assertEqual(t["top_categories"], [{"name": "Eating out", "spend": 4000},
                                               {"name": "Transport", "spend": 2000}])

    def test_a_trip_longer_than_the_limit_is_a_long_stay(self):
        self.assertTrue(api.trips(self.conn, long_stay=4)["trips"][0]["long_stay"])
        self.assertEqual(api.trips(self.conn, long_stay=4)["long_stay_days"], 4)

    def test_a_trip_with_no_rows_costs_nothing(self):
        self.conn.execute("INSERT INTO trip(name, start_date, end_date) "
                          "VALUES ('Trip B', '2026-09-01', '2026-09-02')")
        b = next(t for t in api.trips(self.conn, long_stay=60)["trips"] if t["name"] == "Trip B")
        self.assertEqual((b["rows"], b["spend_sgd"], b["per_day"], b["top_categories"]), (0, 0, 0, []))


class FlagsTest(unittest.TestCase):
    def test_alpha3_becomes_a_flag(self):
        from finance.countries import flag
        self.assertEqual(flag("MYS"), "\U0001F1F2\U0001F1FE")
        self.assertEqual(flag("gbr"), "\U0001F1EC\U0001F1E7")
        self.assertEqual(flag("XXX"), "")
        self.assertEqual(flag(""), "")

    def test_the_table_is_every_iso_code_once(self):
        from finance.countries import ALPHA2
        from finance.parsers import places
        self.assertEqual(set(ALPHA2), places.ISO_ALPHA3)
        self.assertEqual(len(set(ALPHA2.values())), len(ALPHA2))

    def test_the_picker_lists_every_country_with_its_twin_and_flag(self):
        from finance.countries import ALPHA2, listing
        rows = listing()
        self.assertEqual([r["code"] for r in rows], sorted(ALPHA2))
        self.assertTrue(all(len(r["code"]) == 3 and len(r["alpha2"]) == 2 and r["flag"] for r in rows))
        self.assertEqual(next(r for r in rows if r["code"] == "JPN"),
                         {"code": "JPN", "alpha2": "JP", "flag": "\U0001F1EF\U0001F1F5"})

    def test_the_trips_answer_says_when_the_record_ends(self):
        conn = on_a_trip()
        self.addCleanup(conn.close)
        last = conn.execute("SELECT MAX(txn_date) FROM txn").fetchone()[0]
        self.assertTrue(last)
        self.assertEqual(api.trips(conn, long_stay=60)["last_txn"], last)
        self.assertEqual(api.trips(self.enterContext(closing(db.connect(":memory:"))), long_stay=60)["last_txn"], "")

    def test_the_trips_answer_carries_a_flag_per_code(self):
        conn = on_a_trip()
        self.addCleanup(conn.close)
        db.set_trip_links(conn, conn.execute("SELECT id FROM trip").fetchone()[0], ["MYS", "XXX"], [])
        t = api.trips(conn, long_stay=60)["trips"][0]
        self.assertEqual((t["countries"], t["flags"]), (["MYS", "XXX"], ["\U0001F1F2\U0001F1FE", ""]))


if __name__ == "__main__":
    unittest.main()
