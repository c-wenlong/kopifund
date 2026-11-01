"""The hub page's tables (server/analytics/publish.py) and its sync-status line."""
import isolate  # noqa: F401  first: no test writes the real record or cache
import unittest
from datetime import date
from pathlib import Path


from analytics_fixture import settings
from fake_notion import CFG, FakeNotion, block, row

from analytics import publish
from finance import notion, sync

# G20: every figure in cents, as the stats and the snapshot hold them
RESULTS = {
    "savings-runway": {"data": {"summary": {"cash_minor": 481236, "as_of": "2026-07",
                                            "runway_6m": 2.5, "spend_avg_6m_minor": 192540},
                                "months": [{"month": "2026-06", "cash_minor": 395015},
                                           {"month": "2026-07", "cash_minor": 481236}]}},
    "portfolio": {"data": {"summary": {"value_sgd_minor": 1524050, "value_base_minor": 1188759,
                                       "as_of": "2026-08-31", "contributed_sgd_minor": 1200000,
                                       "gain_sgd_minor": 324050, "gain_pct": 27.0,
                                       "statements": 3, "cash_sgd_minor": 481236,
                                       "cash_as_of": "2026-07",
                                       "net_worth_sgd_minor": 2005286,
                                       "unmatched_deposits": 1}}},
    "monthly-cashflow": {"data": {"months": [
        {"month": "2026-06", "income_minor": 392010, "spend_minor": 241025, "investing_minor": 100000,
         "net_after_investing_minor": 50985, "partial": False},
        {"month": "2026-07", "income_minor": 310540, "spend_minor": 153218, "investing_minor": 100000,
         "net_after_investing_minor": 57322, "partial": False},
        {"month": "2026-08", "income_minor": 164000, "spend_minor": 61590, "investing_minor": 100000,
         "net_after_investing_minor": 2410, "partial": True}]}},
    "forward-view": {"params": {"spend_ex_all_trips_minor": 189025, "starting_month": "2026-07",
                                "spend_window_all": ["2026-02", "2026-07"]},
                     "data": {"months": [{"month": "2026-12", "cash_minor": -125040},
                                         {"month": "2027-06", "cash_minor": 987560}]}},
    "eating-out": {"data": {"months": [{"month": "2026-07", "count": 24, "spend_minor": 61245}]}},
    "needs-wants-savings": {"data": {"summary": {"want_share": 0.468, "months": 18}}},
    "data-quality": {"data": {"counts": {"uncategorised": 37}}},
}
SNAPSHOT = {"budgets": [
    {"category": "Eating out", "month": "2026-09", "planned_minor": 60000, "actual_minor": 41200},
    {"category": "Transport", "month": "2026-09", "planned_minor": 12000, "actual_minor": 6600},
    {"category": "Rent", "month": "2026-09", "planned_minor": None, "actual_minor": 0}]}
TODAY = date(2026, 9, 4)


def cfg_with(**ds):
    import copy
    c = copy.copy(CFG)
    c.data_sources = {**CFG.data_sources, **ds}
    return c


class MetricsTest(unittest.TestCase):

    def test_the_headline_rows_come_from_the_stats(self):
        rows = {r["Name"]: r for r in publish.metrics_from(RESULTS, SNAPSHOT, TODAY)}
        # G20: a money row holds its cents, `Value_minor`, and no dollars
        self.assertEqual(rows["Cash, all accounts"]["Value_minor"], 481236)
        self.assertNotIn("Value", rows["Cash, all accounts"])
        self.assertEqual(rows["Runway, months"]["Unit"], "months")
        self.assertIn("spending rate of 1,925.40", rows["Runway, months"]["Note"])
        self.assertEqual(rows["Investments, market value"]["Value_minor"], 1524050)
        self.assertEqual(rows["Investments, market value"]["As of"], "2026-08-31")
        self.assertIn("contributed 12,000.00", rows["Investments, market value"]["Note"])
        self.assertIn("gain 3,240.50", rows["Investments, market value"]["Note"])
        self.assertEqual(rows["Net worth, cash plus investments"]["Value_minor"], 2005286)
        self.assertEqual(rows["Net worth, cash plus investments"]["As of"], "2026-08-31")
        self.assertIn("cash at 2026-07",
                      rows["Net worth, cash plus investments"]["Note"])
        self.assertEqual(rows["Spending, last full month"]["As of"], "2026-07")
        self.assertEqual(rows["Spending, last full month"]["Value_minor"], 153218)
        self.assertEqual(rows["Invested in 2026"]["Value_minor"], 300000)
        self.assertEqual(rows["Home rate, six-month median"]["Value_minor"], 189025)
        self.assertEqual(rows["Cash by 2027-06, on the plan"]["Value_minor"], 987560)
        self.assertIn("-1,250.40 in 2026-12", rows["Cash by 2027-06, on the plan"]["Note"])
        self.assertEqual(rows["Eating out, last full month"]["Value_minor"], 61245)
        self.assertEqual(rows["Wants, share of income"]["Value"], 46.8)
        self.assertEqual(rows["Rows needing a rule"]["Value"], 37)
        self.assertAlmostEqual(rows["Budget used, this month"]["Value"], 66.4, places=1)
        self.assertIn("Actual 478.00 over Planned 720.00", rows["Budget used, this month"]["Note"])
        # each row's Order is its place in the fixed table: RESULTS has no cpf
        # stat, so its two rows leave a gap rather than shifting the rest up
        self.assertEqual([r["Order"] for r in rows.values()],
                         [publish.METRIC_ORDER.index(slot) + 1 for slot in publish.METRIC_ORDER
                          if slot not in ("cpf", "net-worth-cpf")])

    def test_a_row_keeps_its_order_whatever_else_is_published(self):
        full = {r["Name"]: r["Order"] for r in publish.metrics_from(RESULTS, SNAPSHOT, TODAY)}
        for stat in RESULTS:
            subset = publish.metrics_from({stat: RESULTS[stat]}, None, TODAY)
            for r in subset:
                self.assertEqual(r["Order"], full[r["Name"]], (stat, r["Name"]))
        # a stat gone from the cache leaves the rest where a full run puts them
        gone = {k: v for k, v in RESULTS.items() if k != "savings-runway"}
        rows = publish.metrics_from(gone, SNAPSHOT, TODAY)
        self.assertEqual({r["Name"]: r["Order"] for r in rows},
                         {k: v for k, v in full.items()
                          if k not in ("Cash, all accounts", "Runway, months")})
        self.assertNotIn(1, [r["Order"] for r in rows])

    def test_the_investment_rows_follow_the_runway_row(self):
        rows = [r["Name"] for r in publish.metrics_from(RESULTS, SNAPSHOT, TODAY)]
        self.assertEqual(rows[:4], ["Cash, all accounts", "Runway, months",
                                    "Investments, market value",
                                    "Net worth, cash plus investments"])

    def test_a_portfolio_with_no_statements_writes_neither_row(self):
        results = {**RESULTS, "portfolio": {"data": {"summary": {"statements": 0,
                                                                 "value_sgd_minor": 0,
                                                                 "net_worth_sgd_minor": 0}}}}
        rows = [r["Name"] for r in publish.metrics_from(results, SNAPSHOT, TODAY)]
        self.assertNotIn("Investments, market value", rows)
        self.assertNotIn("Net worth, cash plus investments", rows)

    def test_a_missing_stat_skips_its_rows(self):
        rows = publish.metrics_from({"savings-runway": RESULTS["savings-runway"]}, None, TODAY)
        self.assertEqual([r["Name"] for r in rows], ["Cash, all accounts", "Runway, months"])

    def test_months_rows(self):
        phases = ('[analytics]\nphases = [{ name = "Abroad", from = "2026-01", to = "2026-06" },'
                  ' { name = "Back home", from = "2026-07" }]\n')
        with settings(phases):
            rows = publish.months_from(RESULTS)
        self.assertEqual([r["Name"] for r in rows], ["2026-06", "2026-07", "2026-08"])
        self.assertEqual(rows[1]["Cash_minor"], 481236)
        self.assertIsNone(rows[2]["Cash_minor"])
        self.assertEqual(rows[0]["Net_minor"], 50985)
        # the Notion row: the same figures in dollars, under the bare names
        self.assertEqual(publish.notion_row(rows[1])["Cash"], 4812.36)
        self.assertIsNone(publish.notion_row(rows[2])["Cash"])
        self.assertEqual(publish.notion_row(rows[0])["Spending"], 2410.25)
        self.assertTrue(rows[2]["Partial"])
        self.assertEqual([r["Phase"] for r in rows], ["Abroad", "Back home", "Back home"])

    def test_months_rows_carry_no_phase_when_none_is_configured(self):
        with settings(""):
            rows = publish.months_from(RESULTS)
        self.assertEqual([r["Phase"] for r in rows], [None, None, None])


class PublishTest(unittest.TestCase):

    def setUp(self):
        self.client = FakeNotion()
        self.cfg = cfg_with(metrics="ds-metrics", months="ds-months")

    def names(self, ds):
        return {notion.plain(p, "Name"): p for p in self.client.query_all(ds)}

    def test_first_run_creates_then_second_run_writes_nothing(self):
        out = publish.publish(self.cfg, self.client, RESULTS, SNAPSHOT, echo=None, today=TODAY)
        self.assertEqual(out["metrics"]["created"], 13)
        self.assertEqual(out["months"]["created"], 3)
        before = self.client.writes
        out = publish.publish(self.cfg, self.client, RESULTS, SNAPSHOT, echo=None, today=TODAY)
        self.assertEqual(self.client.writes, before)
        self.assertEqual((out["metrics"]["updated"], out["months"]["updated"]), (0, 0))

    def test_a_changed_number_is_patched_in_place(self):
        publish.publish(self.cfg, self.client, RESULTS, SNAPSHOT, echo=None, today=TODAY)
        newer = {**RESULTS, "savings-runway": {"data": {**RESULTS["savings-runway"]["data"],
                 "summary": {**RESULTS["savings-runway"]["data"]["summary"], "cash_minor": 700000}}}}
        out = publish.publish(self.cfg, self.client, newer, SNAPSHOT, echo=None, today=TODAY)
        self.assertEqual(out["metrics"]["updated"], 1)
        self.assertEqual(notion.plain(self.names("ds-metrics")["Cash, all accounts"], "Value"), 7000.0)

    def test_a_row_you_added_is_left_alone(self):
        self.client.create("ds-metrics", {"Name": {"title": [{"text": {"content": "Cash, all accounts"}}]},
                                          "Value": {"number": 1.0}})
        out = publish.publish(self.cfg, self.client, RESULTS, SNAPSHOT, echo=None, today=TODAY)
        self.assertEqual(out["metrics"]["yours"], 1)
        self.assertEqual(notion.plain(self.names("ds-metrics")["Cash, all accounts"], "Value"), 1.0)

    def test_notion_holds_the_dollars_of_the_cents(self):
        publish.publish(self.cfg, self.client, RESULTS, SNAPSHOT, echo=None, today=TODAY)
        metrics, months = self.names("ds-metrics"), self.names("ds-months")
        self.assertEqual(notion.plain(metrics["Cash, all accounts"], "Value"), 4812.36)
        self.assertEqual(notion.plain(metrics["Cash by 2027-06, on the plan"], "Value"), 9875.6)
        self.assertEqual(notion.plain(metrics["Runway, months"], "Value"), 2.5)
        self.assertEqual(notion.plain(months["2026-07"], "Income"), 3105.4)
        self.assertEqual(notion.plain(months["2026-07"], "Cash"), 4812.36)
        self.assertIsNone(notion.plain(months["2026-08"], "Cash"))

    def test_notion_row_is_the_one_place_cents_become_dollars(self):
        self.assertEqual(publish.notion_row({"Name": "x", "Value_minor": 123405, "Unit": "SGD"}),
                         {"Name": "x", "Value": 1234.05, "Unit": "SGD"})
        self.assertEqual(publish.notion_row({"Value_minor": -5, "Cash_minor": None}), {"Value": -0.05, "Cash": None})
        self.assertEqual(publish.notion_row({"Value": 2.5, "Unit": "months"}), {"Value": 2.5, "Unit": "months"})
        with self.assertRaises(TypeError):
            publish.notion_row({"Value_minor": 12.5})       # dollars never pass as cents

    def test_a_result_in_dollars_writes_no_money(self):
        # G20: a result from before its stat wrote cents is not read as cents, nor as dollars
        old = {"savings-runway": {"data": {"summary": {"cash": 4812.36, "as_of": "2026-07", "runway_6m": 2.5}}}}
        rows = publish.metrics_from(old, None, TODAY)
        self.assertEqual([r["Name"] for r in rows], ["Runway, months"])

    def test_dry_run_writes_nothing(self):
        out = publish.publish(self.cfg, self.client, RESULTS, SNAPSHOT, dry_run=True, echo=None, today=TODAY)
        self.assertEqual(out["metrics"]["created"], 13)
        self.assertEqual(self.client.writes, 0)

    def test_nothing_configured_means_nothing_touched(self):
        out = publish.publish(CFG, self.client, RESULTS, SNAPSHOT, echo=None, today=TODAY)
        self.assertEqual(out, {})
        self.assertEqual(self.client.writes, 0)


class HubStatusTest(unittest.TestCase):

    def test_the_status_line(self):
        parsed = sync.Parsed(rows=[row(), row(txn_key="b", category="")], blocks=[block()], csv_dir=Path("."))
        line = sync.hub_status_text(parsed, [], wrote=True, writes=3, when="2026-09-04")
        self.assertEqual(line, "Last sync 2026-09-04 · 2 rows in 1 blocks · clean, 3 write(s) · "
                               "2 row(s) need a rule · POSB Everyday to 2026-01")
        self.assertEqual(sync.short_account("DBS Multiplier Account"), "DBS Multiplier")
        self.assertEqual(sync.short_account("DBS PayLah! wallet"), "DBS PayLah!")
        self.assertEqual(sync.short_account("YouTrip wallet · Y-1029384756"), "YouTrip Y-1029384756")
        line = sync.hub_status_text(parsed, ["p"], wrote=False, when="2026-09-04")
        self.assertIn("1 problem(s), nothing written", line)

    def test_the_callout_is_rewritten_only_when_configured(self):
        import copy
        client = FakeNotion()
        self.assertFalse(sync.write_hub_status(CFG, client, "x"))
        cfg = copy.copy(CFG)
        cfg.hub = {"status_block": "blk-1"}
        self.assertTrue(sync.write_hub_status(cfg, client, "hello"))
        self.assertEqual(client.blocks["blk-1"]["callout"]["rich_text"][0]["text"]["content"], "hello")
        self.assertFalse(sync.write_hub_status(cfg, client, "x", dry_run=True))




class MonthsConsistencyTest(unittest.TestCase):
    def test_months_need_both_stats_from_one_snapshot(self):
        same = {"monthly-cashflow": {"snapshot": "snapshot-2026-10-01.json"},
                "savings-runway": {"snapshot": "snapshot-2026-10-01.json"}}
        mixed = {"monthly-cashflow": {"snapshot": "snapshot-2026-09-01.json"},
                 "savings-runway": {"snapshot": "snapshot-2026-10-01.json"}}
        self.assertTrue(publish.months_consistent(same))
        self.assertFalse(publish.months_consistent(mixed))

if __name__ == "__main__":
    unittest.main()
