"""server/finance/home.py: what Home draws (GET /api/overview)."""
import isolate  # noqa: F401  first: no test writes the real record or cache
import unittest
import urllib.parse
from datetime import date

from finance import api, db, home, money, recurring, rules_app, upload_log
from finance.db import SYNTHETIC_TYPE
from test_app_writes import record
from test_db import SNAP, block, parsed, row
from test_recurring import ACTIVE, ROWS, record as recurring_record, results as recurring_results
from test_scam_checks import MAY, card, sent

# invented figures; the record (test_app_writes.record) holds Grab -12.50 on
# 3 August and an uncategorised -17.00, plus what each test adds
JULY = dict(date="2026-07-10", period="111-11111-1 · SGD · 2026-07", source="statements/posb/2026/2026-07.pdf")
SEPT = dict(date="2026-09-04", period="111-11111-1 · SGD · 2026-09", source="statements/posb/2026/2026-09.pdf")


def cashflow(*partial):
    return {"monthly-cashflow": {"data": {"months": [
        {"month": m, "spend_minor": 100, "partial": m in partial} for m in ("2026-07", "2026-08", "2026-09")]}}}


def runway(months, **summary):
    # G20: the stat's money in cents, `<name>_minor`
    return {"savings-runway": {"data": {"months": months, "summary": {
        "cash_minor": months[-1]["cash_minor"] if months else 0, "as_of": months[-1]["month"] if months else None,
        "runway_6m": 4.2, "spend_avg_6m_minor": 100000, "rule_low": 3, "rule_high": 6, **summary}}}}


class Chart(unittest.TestCase):
    def setUp(self):
        self.conn = record(row("k3", "-100.00", merchant="GRAB*456", **JULY),
                           row("k4", "-40.00", merchant="GRAB*MY", merchant_country="MYS", **SEPT))
        self.addCleanup(self.conn.close)
        # a ride abroad still counts: the chart is every row, home and abroad
        self.conn.execute("UPDATE txn SET trip_id = (SELECT id FROM trip LIMIT 1) WHERE txn_key = 'k4'")

    def test_the_explorer_s_figures_by_bucket_over_every_row(self):
        c = home.spending_chart(self.conn, {})
        self.assertEqual((c["by"], c["scope"]), ("bucket", "all"))
        self.assertEqual(c["months"], ["2026-07", "2026-08", "2026-09"])
        self.assertEqual([s["name"] for s in c["series"]], ["Getting around"])
        self.assertEqual(c["series"][0]["values"], [10000, 1250, 4000])
        self.assertIn("Getting around", c["bucket_order"])
        self.assertEqual(c["totals"]["per_month"], (10000 + 1250 + 4000) // 3)

    def test_a_partial_last_month_is_left_off_so_the_last_bar_is_a_whole_month(self):
        c = home.spending_chart(self.conn, cashflow("2026-09"))
        self.assertEqual(c["months"][-1], "2026-08")
        self.assertEqual(c["series"][0]["values"][-1], 1250)

    def test_no_chart_when_no_month_is_whole(self):
        # only card statements, say: a partial month drawn last would read as a whole one
        c = home.spending_chart(self.conn, cashflow("2026-07", "2026-08", "2026-09"))
        self.assertEqual((c["months"], c["series"]), ([], []))

    def test_a_partial_month_before_the_last_stays(self):
        self.assertEqual(home.spending_chart(self.conn, cashflow("2026-07"))["months"],
                         ["2026-07", "2026-08", "2026-09"])

    def test_at_most_twelve_months(self):
        self.conn.execute("UPDATE txn SET txn_date = '2024-01-15' WHERE txn_key = 'k3'")
        self.assertEqual(home.chart_range(self.conn, {}), ("2025-10", "2026-09"))

    def test_an_empty_record_draws_nothing(self):
        self.conn.execute("DELETE FROM txn")
        c = home.spending_chart(self.conn, {})
        self.assertEqual((c["months"], c["series"]), ([], []))


class Tiles(unittest.TestCase):
    def test_cash_against_the_month_before(self):
        r = runway([{"month": "2026-07", "cash_minor": 100010, "complete": True},
                    {"month": "2026-08", "cash_minor": 90005, "complete": True}])
        self.assertEqual(home.cash_change(r), {"change": -10005, "since": "2026-07"})

    def test_no_cash_change_when_the_month_before_is_missing(self):
        r = runway([{"month": "2026-06", "cash_minor": 100000, "complete": True},
                    {"month": "2026-07", "cash_minor": 95000, "complete": False},
                    {"month": "2026-08", "cash_minor": 90000, "complete": True}])
        self.assertIsNone(home.cash_change(r))
        self.assertIsNone(home.cash_change({}))

    def test_net_worth_against_a_year_before(self):
        months = [{"month": f"2025-{m:02d}", "cash_minor": 10000 + 100 * m, "complete": True} for m in range(1, 13)]
        self.assertIsNone(home.net_worth_change(runway(months)))
        months.append({"month": "2026-01", "cash_minor": 15000, "complete": True})
        self.assertEqual(home.net_worth_change(runway(months)), {"change": 15000 - 10100, "since": "2025-01"})

    def test_no_net_worth_change_without_a_net_worth(self):
        # thirteen months of cash build a net worth series, but with no
        # investments or CPF there is no net worth figure to put a change under
        months = [{"month": f"2025-{m:02d}", "cash_minor": 10000, "complete": True} for m in range(1, 13)]
        months.append({"month": "2026-01", "cash_minor": 15000, "complete": True})
        h = api.overview(record(), runway(months), today=date(2026, 10, 15))["headline"]
        self.assertEqual((h["net_worth_incl_cpf"], h["net_worth_change"]), (None, None))
        self.assertEqual(h["cash_change"], {"change": 5000, "since": "2025-12"})

    def test_headline_figures_are_cents_with_the_runway_range(self):
        o = api.overview(record(), {**runway([{"month": "2026-08", "cash_minor": 1_355_100, "complete": True}]),
                                    "cpf": {"data": {"summary": {"years": 3, "total_minor": 606_103,
                                                                 "as_of": "2025-12-31",
                                                                 "net_worth_total_minor": 4_929_940, "oa_minor": 100,
                                                                 "sa_minor": 100, "ma_minor": 100}}}},
                          today=date(2026, 10, 15))
        h = o["headline"]
        self.assertEqual((h["cash"], h["net_worth_incl_cpf"], h["runway_months"]), (1_355_100, 4_929_940, 4.2))
        self.assertEqual(h["runway_range"], [3, 6])
        self.assertEqual([x["label"] for x in o["holdings"]], ["Cash", "CPF"])

    def test_no_results_yet_still_answers(self):
        o = api.overview(record(), {}, today=date(2026, 10, 15))
        h = o["headline"]
        self.assertEqual((h["cash"], h["cash_change"], h["net_worth_change"], h["runway_range"]),
                         (None, None, None, None))
        self.assertEqual((o["coming_up"]["rows"], o["holdings"]), ([], []))
        self.assertEqual(o["spending"]["months"], ["2026-08"])
        eye = o["needs_your_eye"]
        self.assertEqual((eye["needs_category"], eye["recurring_off"], eye["one_off"], eye["cash_trend_6m"],
                          eye["irregular"]), (1, [], None, None, 0))


class NeedsCategoryLink(unittest.TestCase):
    def test_the_count_equals_the_rows_the_link_lists(self):
        # G13: the line counts External rows with no category that the pipeline did not make up.
        # The link opens Transactions with that category, synthetic=0 and transfers hidden (its default)
        conn = record(
            row("own", "-30.00", flow_class="Internal - own account", merchant="", payee="", category=""),
            row("made", "-9.00", bank_txn_type=SYNTHETIC_TYPE, merchant="", payee="", category=""))
        self.addCleanup(conn.close)
        counted = home.needs_your_eye(conn, {}, {"rows": [], "month": "2026-08"})["needs_category"]
        link = {"category": "(none)", "synthetic": "0", "hide_transfers": "1"}
        self.assertEqual((counted, api.transactions(conn, link)["total"]), (1, 1))
        # each part of the link takes out a row the count leaves out
        self.assertEqual(api.transactions(conn, {"category": "(none)", "hide_transfers": "1"})["total"], 2)
        self.assertEqual(api.transactions(conn, {"category": "(none)", "synthetic": "0"})["total"], 2)
        self.assertEqual(api.transactions(conn, {"category": "(none)"})["total"], 3)


class NeedsYourEye(unittest.TestCase):
    def setUp(self):
        self.conn = recurring_record(*ROWS)
        self.addCleanup(self.conn.close)
        self.results = recurring_results(ACTIVE, flagged=["Club", "Magazine"])
        self.up = recurring.coming_up(self.conn, self.results, today=date(2026, 10, 16))

    def eye(self, **more):
        return home.needs_your_eye(self.conn, {**self.results, **more}, self.up)

    def test_late_or_different_bills_but_not_investing(self):
        self.assertEqual([(r["payee"], r["status"]) for r in self.eye()["recurring_off"]],
                         [("Landlord", "different"), ("Phone", "late")])

    def test_payees_marked_recurring_with_no_spacing(self):
        self.assertEqual(self.eye()["irregular"], 2)

    def test_cash_falling_over_six_months_is_cents(self):
        self.assertEqual(self.eye(**runway([], trend_6m_minor=-123450))["cash_trend_6m"], -123450)

    def test_the_biggest_one_off_in_this_month_only(self):
        offs = {"one-offs": {"data": {"one_offs": [
            {"date": "2026-08-30", "month": "2026-08", "payee": "Airline", "category": "Travel",
             "amount_minor": 200000, "multiple": 9.0},
            {"date": "2026-09-12", "month": "2026-09", "payee": "Clinic", "category": "Health",
             "amount_minor": 45000, "multiple": 6.1},
            {"date": "2026-09-03", "month": "2026-09", "payee": "(no name)", "category": "Shopping",
             "amount_minor": 80025, "multiple": 7.0, "merchant_raw": "SHOP*88"}]}}}
        got = self.eye(**offs)["one_off"]
        self.assertEqual((got["payee"], got["amount"], got["date"], got["link"]),
                         ("SHOP*88", 80025, "2026-09-03", {"month": "2026-09", "q": "SHOP*88"}))
        self.assertIsNone(self.eye(**{"one-offs": {"data": {"one_offs": offs["one-offs"]["data"]["one_offs"][:1]}}})
                          ["one_off"])

    def test_a_one_off_with_no_text_opens_its_category_in_its_month(self):
        got = home.biggest_one_off({"one-offs": {"data": {"one_offs": [
            {"date": "2026-09-03", "month": "2026-09", "payee": "(no name)", "category": "(uncategorised)",
             "amount": 500.0, "multiple": 6.0}]}}}, "2026-09")
        self.assertEqual(got["link"], {"month": "2026-09", "category": "(none)"})


# ---------------------------------------------------------------- G18: every line, its words and its link

def query_of(to):
    """The Transactions query a line's link sends, as the page reads it."""
    path, _, query = to.partition("?")
    assert path == "/transactions", to
    return dict(urllib.parse.parse_qsl(query))


def lines(conn, results=None, today=date(2026, 9, 10), **eye):
    """The lines for the record, with any part of the card given in `eye` in place of the record's."""
    e = {**home.needs_your_eye(conn, results or {}, {"rows": [], "month": "2026-08"}, today=today), **eye}
    return home.eye_lines(e)


def by_kind(rows):
    return {line["kind"]: line for line in rows}


class EyeLines(unittest.TestCase):
    def setUp(self):
        # an early row so a payee can be new, a transfer the scam checks flag, and an uncategorised row (k2)
        self.conn = record(row("early", "-5.00", **MAY), sent("t1", "-900.00", 10))
        self.addCleanup(self.conn.close)

    def test_scam_first_in_the_danger_colour_and_the_rest_in_the_warning_colour(self):
        got = lines(self.conn, cash_trend_6m=-123450, irregular=2)
        self.assertEqual([x["kind"] for x in got], ["scam", "needs_category", "needs_rule", "cash_trend", "irregular"])
        self.assertEqual([x["tone"] for x in got], ["danger"] + ["warn"] * 4)
        self.assertEqual(got[0]["text"], "S$900 to SCAMMER PTE on 10 Aug 2026 looks like a scam")
        self.assertEqual(got[0]["to"], "/transactions?flag=scam&hide_transfers=0")
        self.assertEqual(by_kind(got)["cash_trend"], {"kind": "cash_trend", "tone": "warn",
                                                       "text": "Cash down S$1,235 over six months", "to": "/net-worth"})

    def test_every_kind_is_in_the_order_the_lines_come(self):
        scam = {"count": 2, "scams": 2, "payee": "P", "date": "2026-08-10", "amount": 100, "code": "repeated",
                "reason": ""}
        e = {"scam": scam,
             "upload": {"id": 1, "state": "checked", "problems": 0}, "needs_category": 1,
             "recurring_off": [{"id": "payee:Gym:5000", "payee": "Gym", "name": "Gym", "status": "late",
                                "expected": "2026-08-05"}], "missing_fx": 1,
             "needs_rule": 1, "one_off": {"payee": "Clinic", "date": "2026-08-03", "category": "Health",
                                          "amount": 45000, "multiple": None, "link": {"key": "abc"}},
             "cash_trend_6m": -1, "stale_accounts": [{"name": "POSB", "last": "2026-06-30", "months": 2}],
             "pending": 1, "irregular": 1}
        got = home.eye_lines(e)
        # one scam checks line: "scam" when a flag points at a scam, else "flagged", further down
        self.assertEqual([x["kind"] for x in got], [k for k in home.EYE_KINDS if k != "flagged"])
        self.assertEqual(by_kind(got)["one_off"]["to"], "/transactions?key=abc")
        self.assertEqual(by_kind(got)["scam"]["text"], "2 payments look like a scam, the latest S$1 to P")
        got = home.eye_lines({**e, "scam": {**scam, "scams": 0, "code": "twice"}})
        self.assertEqual([x["kind"] for x in got], [k for k in home.EYE_KINDS if k != "scam"])

    def test_the_words_of_each_line(self):
        # the words Home's card had when the page wrote them (G13 to G16), now written here for both lists
        calm = {"scam": None, "upload": None, "needs_category": 0, "recurring_off": [], "missing_fx": 0,
                "needs_rule": 0, "one_off": None, "cash_trend_6m": None, "stale_accounts": [], "pending": 0,
                "irregular": 0}

        def words(**part):
            return [(x["text"], x["to"]) for x in home.eye_lines({**calm, **part})]

        self.assertEqual(words(), [])
        self.assertEqual(words(needs_category=1), [("1 transaction needs a category",
                                                    "/transactions?category=(none)&synthetic=0&hide_transfers=1")])
        late = {"id": "payee:Phone:2000", "payee": "Phone", "name": "Phone", "status": "late", "expected": "2026-09-02"}
        odd = {"id": "payee:Landlord:100000", "payee": "Landlord", "name": "Landlord", "status": "different",
               "expected": "2026-09-01"}
        # G16: one bill opens its series' own view on Recurring; several open the page
        self.assertEqual(words(recurring_off=[late]), [("Phone is late", "/recurring?series=payee%3APhone%3A2000")])
        self.assertEqual(words(recurring_off=[odd])[0][0], "Landlord came in at a different amount")
        self.assertEqual(words(recurring_off=[late, odd]), [("2 recurring bills late or a different amount", "/recurring")])
        # G16: one of a payee's several series is named with its usual amount
        plan = {"id": "payee:Fruitco:398", "payee": "Fruitco", "name": "Fruitco (S$3.98)", "status": "late",
                "expected": "2026-09-12"}
        self.assertEqual(words(recurring_off=[plan])[0][0], "Fruitco (S$3.98) is late")
        one_off = {"payee": "SHOP*88", "date": "2026-09-03", "category": "Shopping", "amount": 80025, "multiple": 7.2,
                   "link": {"month": "2026-09", "q": "SHOP*88"}}
        self.assertEqual(words(one_off=one_off), [("S$800 at SHOP*88 on 3 Sep 2026, 7x the usual for Shopping",
                                                   "/transactions?month=2026-09&q=SHOP*88")])
        self.assertEqual((words(cash_trend_6m=50000), words(cash_trend_6m=0)), ([], []))
        self.assertEqual(words(irregular=1)[0][0], "1 payee marked recurring has no regular spacing")
        self.assertEqual(words(needs_rule=1)[0][0], "1 statement text needs a rule")
        self.assertEqual(words(pending=1), [("1 spend from the phone waits for a statement", "/settings")])
        self.assertEqual(words(pending=3)[0][0], "3 spends from the phone wait for a statement")
        self.assertEqual(words(upload={"id": 3, "state": "problems", "problems": 1})[0][0],
                         "The last upload has 1 problem to fix")

    def test_a_flag_that_is_no_scam_is_worded_for_what_it_is_in_the_warning_colour(self):
        # a new streaming plan: the scam checks flag its first charge, and Home says what it is
        conn = record(row("early", "-5.00", **MAY), card("s1", "-15.98", 1, at="STREAM*TV", month="07"),
                      card("s2", "-15.98", 31, at="STREAM*TV", month="07"))
        self.addCleanup(conn.close)
        line = by_kind(lines(conn))["flagged"]
        self.assertEqual(line, {"kind": "flagged", "tone": "warn", "to": "/transactions?flag=scam&hide_transfers=0",
                                "text": "New monthly payment to STREAM*TV, first charged S$16 on 1 Jul 2026"})
        self.assertNotIn("scam", by_kind(lines(conn)))
        self.assertEqual(api.transactions(conn, query_of(line["to"]))["total"], 1)

    def test_the_words_of_the_scam_checks_line(self):
        def words(**s):
            e = {"scam": {"count": 1, "scams": 0, "payee": "P", "date": "2026-08-10", "amount": 4500,
                          "code": "new_subscription", "reason": "why", **s},
                 "upload": None, "needs_category": 0, "recurring_off": [], "missing_fx": 0, "needs_rule": 0,
                 "one_off": None, "cash_trend_6m": None, "stale_accounts": [], "pending": 0, "irregular": 0}
            (line,) = home.eye_lines(e)
            return line["tone"], line["text"]

        self.assertEqual(words(scams=1, code="abroad"), ("danger", "S$45 to P on 10 Aug 2026 looks like a scam"))
        self.assertEqual(words(count=3, scams=3, code="repeated"),
                         ("danger", "3 payments look like a scam, the latest S$45 to P"))
        # the line counts every flagged row, as its link lists them, and names the scam
        self.assertEqual(words(count=3, scams=1, code="new_payee"),
                         ("danger", "3 flagged payments, one looks like a scam: S$45 to P on 10 Aug 2026"))
        self.assertEqual(words(count=3, scams=2, code="new_payee"),
                         ("danger", "3 flagged payments, 2 look like a scam, the latest S$45 to P"))
        self.assertEqual(words(), ("warn", "New monthly payment to P, first charged S$45 on 10 Aug 2026"))
        self.assertEqual(words(count=2, code="twice"),
                         ("warn", "2 payments to look at, the latest S$45 to P (charged more than once that day)"))
        self.assertEqual(words(count=2, code="test_charge"),
                         ("warn", "2 payments to look at, the latest S$45 to P (a small first charge then a larger one)"))
        self.assertEqual(words(code="test_charge"),
                         ("warn", "S$45 to P on 10 Aug 2026, a small first charge then a larger one"))
        for code in ("new_subscription", "twice", "test_charge"):
            self.assertNotIn("scam", words(code=code)[1] + words(count=2, code=code)[1])

    def test_a_category_line_lists_the_rows_it_counts(self):
        line = by_kind(lines(self.conn))["needs_category"]
        self.assertEqual(line["text"], "2 transactions need a category")
        self.assertEqual(api.transactions(self.conn, query_of(line["to"]))["total"], 2)

    def test_the_needs_rule_count_is_the_rules_page_queue_and_opens_rules(self):
        line = by_kind(lines(self.conn))["needs_rule"]
        self.assertEqual(rules_app.needs_rule_count(self.conn), len(rules_app.needs_rule(self.conn)))
        self.assertEqual((line["text"], line["to"]), ("2 statement texts need a rule", "/rules"))

    def test_the_needs_rule_count_is_every_group_above_the_sixty_the_rules_page_lists(self):
        # 65 statement texts no rule names: the page lists the biggest 60 and titles the card with all 65
        conn = record(*(row(f"n{i:02d}", f"-{i + 1}.00", merchant=f"SHOP NUMBER {i:02d}", payee="", category="", rule="")
                        for i in range(65)))
        self.addCleanup(conn.close)
        total = rules_app.needs_rule_count(conn)
        self.assertEqual((total, len(rules_app.needs_rule(conn))), (66, rules_app.NEEDS_SHOWN))
        self.assertEqual(home.needs_your_eye(conn, {}, {"rows": [], "month": "2026-08"})["needs_rule"], total)

    def test_nothing_true_gives_no_lines(self):
        conn = record()
        self.addCleanup(conn.close)
        conn.execute("UPDATE txn SET rule = 'r1', category_id = (SELECT id FROM category WHERE name = 'Transport')")
        self.assertEqual(lines(conn), [])

    def test_whole_dollars_as_the_app_rounds_them(self):
        self.assertEqual((money.whole(123450), money.whole(149), money.whole(150), money.whole(-5000), money.whole(0)),
                         ("S$1,235", "S$1", "S$2", "−S$50", "S$0"))


def no_fx_record(more=0):
    """A record with a USD block that printed no rate to SGD: a payment and a move between own accounts
    in it, and `more` payments after them, beside an SGD payment that has its value."""
    conn = db.connect(":memory:")
    db.import_user_tables(conn, {k: [dict(x) for x in v] for k, v in SNAP.items()})
    usd = "111-11111-1 · USD · 2026-08"
    db.load_parsed(conn, parsed([block(), block(period=usd, currency="USD", fx_to_sgd="", closing_sgd="")], [
        row("k1", "-12.50"), row("u1", "-20.00", currency="USD", period=usd),
        row("u2", "15.00", currency="USD", period=usd, direction="In", flow_class="Internal - own account"),
        *(row(f"m{i:04d}", "-1.00", currency="USD", period=usd, merchant=f"SHOP {i}") for i in range(more))]),
        payees=[{"name": "Grab", "kind": "Merchant", "country": "SGP"}])
    return conn


class MissingFx(unittest.TestCase):
    def test_the_line_lists_exactly_the_rows_with_no_sgd_value_transfers_too(self):
        conn = no_fx_record()
        self.addCleanup(conn.close)
        e = home.needs_your_eye(conn, {}, {"rows": [], "month": "2026-08"}, today=date(2026, 9, 10))
        line = by_kind(e["lines"])["missing_fx"]
        self.assertEqual((e["missing_fx"], line["text"]), (2, "2 transactions have no SGD value"))
        # G18: a filter of its own, not every key in the address
        self.assertEqual(line["to"], "/transactions?missing_fx=1&hide_transfers=0")
        listed = api.transactions(conn, query_of(line["to"]))
        # every row with no SGD value, and not the SGD payment (k1) beside them
        self.assertEqual(sorted(r["key"] for r in listed["rows"]), ["u1", "u2"])


class UploadLine(unittest.TestCase):
    def setUp(self):
        self.conn = record()
        self.addCleanup(self.conn.close)
        # the stamps are to the second, so each step and load here gets its own minute, in the order they run;
        # the record's own first load comes before them all
        self.minute = 0
        self.conn.execute("UPDATE sync_run SET started_at = '2026-06-01T00:00:00+00:00'")

    def tick(self):
        self.minute += 1
        return f"2026-06-01T00:{self.minute:02d}:00+00:00"

    def step(self, attempt, step, status, output):
        upload_log.record(self.conn, attempt, step, upload_log.now(), status, "", {}, output)
        self.conn.execute("UPDATE upload_step SET finished_at = ? WHERE id = (SELECT MAX(id) FROM upload_step)",
                          (self.tick(),))

    def line(self):
        return by_kind(lines(self.conn)).get("upload")

    def load_outside_the_app(self):
        """`make load` (finance.py load, through server/finance/local.py): db.load_parsed and its sync_run row, no upload step."""
        db.load_parsed(self.conn, parsed([block()], [row("k1", "-12.50")]), complete=False)
        self.conn.execute("UPDATE sync_run SET started_at = ? WHERE id = (SELECT MAX(id) FROM sync_run)", (self.tick(),))

    def test_a_load_from_the_command_line_settles_a_check_that_was_not_loaded(self):
        a = upload_log.start(self.conn)
        self.step(a, "check", "ok", {"problems": [], "to_add_count": 4})
        self.assertEqual(home.upload_state(self.conn)["state"], "checked")
        self.load_outside_the_app()
        self.assertIsNone(home.upload_state(self.conn))
        self.assertIsNone(self.line())

    def test_a_load_from_the_command_line_settles_problems_and_an_unchecked_upload_too(self):
        a = upload_log.start(self.conn)
        self.step(a, "check", "problems", {"problems": [{"code": "unbalanced"}], "to_add_count": 0})
        self.step(a, "upload", "ok", {})
        self.assertEqual(home.upload_state(self.conn)["state"], "added")
        self.load_outside_the_app()
        self.assertIsNone(home.upload_state(self.conn))
        b = upload_log.start(self.conn)
        self.step(b, "check", "problems", {"problems": [{"code": "unbalanced"}], "to_add_count": 0})
        self.assertEqual(home.upload_state(self.conn)["state"], "problems")
        self.load_outside_the_app()
        self.assertIsNone(home.upload_state(self.conn))

    def test_a_load_in_the_app_settles_it_by_its_own_step_and_its_sync_run(self):
        a = upload_log.start(self.conn)
        self.step(a, "check", "ok", {"problems": [], "to_add_count": 4})
        self.step(a, "load", "ok", {"checked": {"to_add_count": 4}, "counts": {}})
        self.assertIsNone(home.upload_state(self.conn))
        # and its sync_run alone settles it, the way a load step that failed to be written would leave it
        self.conn.execute("DELETE FROM upload_step WHERE step = 'load'")
        self.assertEqual(home.upload_state(self.conn)["state"], "checked")
        self.load_outside_the_app()
        self.assertIsNone(home.upload_state(self.conn))

    def test_a_load_before_the_step_does_not_settle_it(self):
        self.load_outside_the_app()
        self.step(upload_log.start(self.conn), "check", "ok", {"problems": [], "to_add_count": 4})
        self.assertEqual(home.upload_state(self.conn)["state"], "checked")

    def stamps(self, step_end, load_start):
        """A check that ended at `step_end`, then a load that started at `load_start`; whether the line stays."""
        a = upload_log.start(self.conn)
        upload_log.record(self.conn, a, "check", upload_log.now(), "ok", "", {}, {"problems": [], "to_add_count": 4})
        self.conn.execute("UPDATE upload_step SET finished_at = ? WHERE id = (SELECT MAX(id) FROM upload_step)", (step_end,))
        db.load_parsed(self.conn, parsed([block()], [row("k1", "-12.50")]), complete=False)
        self.conn.execute("UPDATE sync_run SET started_at = ? WHERE id = (SELECT MAX(id) FROM sync_run)", (load_start,))
        return home.upload_state(self.conn)

    def test_a_load_in_the_same_second_is_ordered_by_the_finer_stamp(self):
        # G18: new stamps carry microseconds, so a load a moment after the check, in the same second, settles it
        self.assertIsNone(self.stamps("2026-06-01T00:05:00.250000+00:00", "2026-06-01T00:05:00.750000+00:00"))
        self.assertEqual(self.stamps("2026-06-01T00:06:00.750000+00:00", "2026-06-01T00:06:00.250000+00:00")["state"],
                         "checked")

    def test_an_old_whole_second_stamp_in_the_same_second_counts_as_after(self):
        # rows written before the finer stamps: a load starting in the second a check ended came after it
        self.assertIsNone(self.stamps("2026-06-01T00:05:00+00:00", "2026-06-01T00:05:00+00:00"))
        self.assertIsNone(self.stamps("2026-06-01T00:06:00+00:00", "2026-06-01T00:06:00.100000+00:00"))
        self.assertEqual(self.stamps("2026-06-01T00:07:01+00:00", "2026-06-01T00:07:00.900000+00:00")["state"], "checked")

    def test_new_steps_and_loads_are_stamped_to_the_microsecond(self):
        a = upload_log.start(self.conn)
        upload_log.record(self.conn, a, "check", upload_log.now(), "ok", "", {}, {"problems": []})
        step = self.conn.execute("SELECT started_at, finished_at FROM upload_step ORDER BY id DESC LIMIT 1").fetchone()
        db.load_parsed(self.conn, parsed([block()], [row("k1", "-12.50")]), complete=False)
        run = self.conn.execute("SELECT started_at, finished_at FROM sync_run ORDER BY id DESC LIMIT 1").fetchone()
        for stamp in (*step, *run):
            self.assertRegex(stamp, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}\+00:00$")

    def test_no_upload_yet_is_no_line(self):
        self.assertIsNone(home.upload_state(self.conn))

    def test_problems_that_stop_a_load_are_a_danger_line_to_the_upload_page(self):
        a = upload_log.start(self.conn)
        self.step(a, "check", "problems", {"problems": [{"code": "unbalanced"}, {"code": "pdftotext-missing"},
                                                        {"code": "gap"}], "to_add_count": 4})
        self.assertEqual(home.upload_state(self.conn), {"id": a, "state": "problems", "problems": 2})
        self.assertEqual(self.line(), {"kind": "upload", "tone": "danger", "to": "/upload",
                                       "text": "The last upload has 2 problems to fix"})

    def test_checked_with_rows_to_add_and_not_loaded_wants_a_load(self):
        a = upload_log.start(self.conn)
        self.step(a, "check", "ok", {"problems": [{"code": "pdftotext-missing"}], "to_add_count": 4})
        self.assertEqual(home.upload_state(self.conn)["state"], "checked")
        self.assertEqual((self.line()["tone"], self.line()["text"]),
                         ("warn", "The last upload was checked but not loaded"))
        # loaded: nothing left to do
        self.step(a, "load", "ok", {"checked": {"to_add_count": 4}, "counts": {}})
        self.assertIsNone(self.line())

    def test_checked_with_nothing_new_is_no_line(self):
        self.step(upload_log.start(self.conn), "check", "ok", {"problems": [], "to_add_count": 0})
        self.assertIsNone(home.upload_state(self.conn))

    def test_a_statement_added_after_the_last_check_has_not_been_checked(self):
        a = upload_log.start(self.conn)
        self.step(a, "check", "ok", {"problems": [], "to_add_count": 0})
        self.step(a, "upload", "duplicate", {})
        self.assertIsNone(home.upload_state(self.conn))
        self.step(a, "upload", "ok", {})
        self.assertEqual(home.upload_state(self.conn)["state"], "added")
        self.assertEqual(self.line()["text"], "A statement was uploaded but not checked")

    def test_a_refused_load_keeps_its_problems(self):
        a = upload_log.start(self.conn)
        self.step(a, "check", "ok", {"problems": [], "to_add_count": 4})
        self.step(a, "load", "blocked", {"checked": {}, "problems": [{"code": "unbalanced"}]})
        self.assertEqual(home.upload_state(self.conn), {"id": a, "state": "problems", "problems": 1})

    def test_a_check_that_errored_is_no_state_and_hides_the_one_before(self):
        a = upload_log.start(self.conn)
        self.step(a, "check", "ok", {"problems": [], "to_add_count": 4})
        self.step(a, "check", "error", {"error": "boom"})
        self.assertIsNone(home.upload_state(self.conn))

    def test_the_latest_attempt_decides_and_the_one_before_counts_for_a_mirror_only_visit(self):
        a = upload_log.start(self.conn)
        self.step(a, "check", "ok", {"problems": [], "to_add_count": 4})
        b = upload_log.start(self.conn)
        self.step(b, "mirror", "ok", {"code": 0})
        self.assertEqual(home.upload_state(self.conn), {"id": a, "state": "checked", "problems": 0})
        # a load from the Rules page is an attempt of its own, and it loaded
        c = upload_log.start(self.conn, "rules")
        self.step(c, "load", "ok", {"checked": {}, "counts": {}})
        self.assertIsNone(home.upload_state(self.conn))


class StaleAccounts(unittest.TestCase):
    def setUp(self):
        # POSB's last row and statement are in August 2026
        self.conn = record()
        self.addCleanup(self.conn.close)

    def test_an_account_missing_last_month_is_late_by_the_months_missing(self):
        self.assertEqual(home.stale_accounts(self.conn, date(2026, 9, 30)), [])
        self.assertEqual(home.stale_accounts(self.conn, date(2026, 10, 1)),
                         [{"name": "POSB", "last": "2026-08-31", "months": 1}])
        line = by_kind(lines(self.conn, today=date(2026, 12, 5)))["stale_accounts"]
        self.assertEqual(line, {"kind": "stale_accounts", "tone": "warn", "to": "/profile",
                                "text": "No statement from POSB for 3 months"})

    def test_cpf_is_late_only_when_last_year_is_missing(self):
        self.conn.execute("INSERT INTO cpf_statement(year, file, as_of, data) VALUES (2024, 'a.pdf', '2024-12-31', '{}')")
        listing = [{"path": "statements/cpf", "name": "cpf", "parser": "cpf", "wallet": "", "statements": 0}]
        late = home.stale_accounts(self.conn, date(2026, 9, 1), listing=listing)
        self.assertEqual([(a["name"], a["months"]) for a in late], [("CPF", 12)])
        self.conn.execute("INSERT INTO cpf_statement(year, file, as_of, data) VALUES (2025, 'b.pdf', '2025-12-31', '{}')")
        self.assertEqual(home.stale_accounts(self.conn, date(2026, 9, 1), listing=listing), [])

    def test_several_accounts_are_one_line_with_the_longest(self):
        e = {k: None for k in ("scam", "upload", "one_off", "cash_trend_6m")}
        e.update(needs_category=0, recurring_off=[], missing_fx=0, needs_rule=0, pending=0, irregular=0,
                 stale_accounts=[
            {"name": "A", "last": "2026-05-31", "months": 4}, {"name": "B", "last": "2026-08-31", "months": 1}])
        self.assertEqual(home.eye_lines(e)[0]["text"], "No statement from 2 accounts for up to 4 months")
        e["stale_accounts"] = [{"name": "CPF", "last": "2024-12-31", "months": 12}]
        self.assertEqual(home.eye_lines(e)[0]["text"], "No statement from CPF for 1 year")


if __name__ == "__main__":
    unittest.main()
