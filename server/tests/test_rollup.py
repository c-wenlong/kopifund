#!/usr/bin/env python3
"""The rollup step against a fake Notion: trips, budget actuals, coverage, recurring.

The four things these tests pin down are that a row is only put on a trip when
something says it was spent away from Singapore, that a nested trip beats the
long one it sits inside, that a Budgets row's Actual is refreshed without the
owner's Planned ever moving, and that an account with no statements reads None
rather than an empty Full.
"""
import isolate  # noqa: F401  first: no test writes the real record or cache
import copy
import dataclasses
import unittest
from pathlib import Path

from fake_notion import ACCOUNT, CFG, FakeNotion, block, row

from finance import notion, sync
from finance.model import (BUDGET_TYPES, TRIP_TYPES, ConfigMissing,
                           canonicalise, load_config, page_properties)

WALLET = "Y-4444444444"                      # the YouTrip account, per-currency
POSB = ACCOUNT                               # the POSB account of the fixture config
MULTIPLIER = "111-11111-1"                   # an account the fixture blocks never touch


# ---------------------------------------------------------------- fixtures

def parsed_of(rows, blocks=()):
    return sync.Parsed(rows=list(rows), blocks=list(blocks), csv_dir=Path("."))


def make_trip(client, name, start, end, countries=()):
    """One Trips page, as the seed script writes them."""
    props = canonicalise({"Name": name, "Dates": (start, end),
                          "Countries": list(countries), "Notes": ""}, TRIP_TYPES)
    return client.create(CFG.ds("trips"), notion.to_api(props, TRIP_TYPES))


def make_category(client, name, need_class=None):
    """A Categories page, with the Need class the budget sign convention reads."""
    return client.create(CFG.ds("categories"), {
        "Name": {"title": [{"text": {"content": name}}]},
        "Need class": {"select": {"name": need_class} if need_class else None}})


def make_payee(client, name, recurring=False):
    return client.create(CFG.ds("payees"), {
        "Name": {"title": [{"text": {"content": name}}]},
        "Recurring": {"checkbox": recurring}})


def make_account(client, page_id, **props):
    """An Accounts page at the id server/finance/config.toml points at, so the rollup
    can find it the way it finds a live one."""
    body = {"Name": {"title": [{"text": {"content": page_id[:8]}}]}}
    for name, value in props.items():
        body[name] = ({"date": {"start": value}} if name.startswith("Statements")
                      else {"select": {"name": value} if value else None})
    page = {"id": page_id, "properties": {n: {"type": next(iter(b)), **b}
                                          for n, b in body.items()}}
    client.pages[CFG.ds("accounts")].append(page)
    return page


def rule(payee, recurring=""):
    return {"payee": payee, "recurring": recurring}


def load(client, rows, blocks):
    """Put the rows in Notion the way a sync would, so the rollup has pages to
    patch."""
    parsed = parsed_of(rows, blocks)
    state = sync.read_state(client and CFG, client)
    plan = sync.build_plan(CFG, parsed, state)
    sync.upsert(CFG, client, parsed, state, plan, echo=False)
    return parsed


# ---------------------------------------------------------------- trips

class TripRuleTest(unittest.TestCase):
    """sync.trip_for, the rule on its own."""

    def setUp(self):
        self.trips = [
            sync.Trip("long", "Long stay abroad", "2023-08-01", "2024-06-25",
                      ("JPN",)),
            sync.Trip("seoul", "Seoul weekend", "2023-12-08", "2023-12-11", ("KOR",)),
            sync.Trip("bkk", "Bangkok weekend", "2024-08-16", "2024-08-18", ("THA",)),
        ]
        self.wallets = {WALLET}

    def at(self, **over):
        return sync.trip_for(row(**over), self.trips, self.wallets)

    def test_a_foreign_row_inside_a_trip_is_assigned(self):
        trip = self.at(date="2024-03-04", merchant_country="JPN")
        self.assertEqual(trip.name, "Long stay abroad")

    def test_a_singapore_row_inside_a_trip_is_not(self):
        self.assertIsNone(self.at(date="2024-03-04", merchant_country="SGP"))

    def test_a_row_without_a_country_is_not(self):
        """An empty merchant country is missing evidence, not evidence of being
        abroad; otherwise the long window would swallow salary and rent."""
        self.assertIsNone(self.at(date="2024-03-04", merchant_country=""))

    def test_a_non_sgd_wallet_row_is_assigned_without_a_country(self):
        trip = self.at(date="2024-03-04", merchant_country="", currency="JPY",
                       account_no=WALLET)
        self.assertEqual(trip.name, "Long stay abroad")

    def test_the_sgd_wallet_is_not_a_foreign_wallet(self):
        self.assertIsNone(self.at(date="2024-03-04", merchant_country="",
                                  currency="SGD", account_no=WALLET))

    def test_a_named_country_wins_over_the_trip_it_sits_inside(self):
        trip = self.at(date="2023-12-09", merchant_country="KOR")
        self.assertEqual(trip.name, "Seoul weekend")

    def test_the_narrower_trip_wins_when_neither_names_the_country(self):
        """A charge from a third country made during the Seoul days is part of
        that trip (a flight, a booking), not of the long stay around it."""
        trip = self.at(date="2023-12-09", merchant_country="TWN")
        self.assertEqual(trip.name, "Seoul weekend")
        self.assertEqual(self.at(date="2023-11-09", merchant_country="TWN").name,
                         "Long stay abroad")

    def test_a_row_outside_every_trip_gets_none(self):
        self.assertIsNone(self.at(date="2024-07-04", merchant_country="USA"))

    def test_the_boundary_days_are_inside(self):
        self.assertIsNotNone(self.at(date="2024-08-16", merchant_country="THA"))
        self.assertIsNotNone(self.at(date="2024-08-18", merchant_country="THA"))
        self.assertIsNone(self.at(date="2024-08-15", merchant_country="THA"))

    def test_a_row_posting_within_the_settlement_window_is_still_the_trips(self):
        """The dates are travel days from the planner; card rows post later."""
        self.assertEqual(self.at(date="2024-08-21", merchant_country="THA").name,
                         "Bangkok weekend")
        self.assertIsNone(self.at(date="2024-08-22", merchant_country="THA"))

    def test_the_settlement_window_does_not_reach_backwards(self):
        self.assertIsNone(self.at(date="2024-08-13", merchant_country="THA"))

    def test_country_names_map_to_iso(self):
        unknown = []
        codes = sync.country_codes(["Malaysia", "JPN", "Japan", "Narnia"], unknown.append)
        self.assertEqual(codes, ("MYS", "JPN"))
        self.assertEqual(unknown, ["Narnia"])
        self.assertEqual(sync.country_codes(None), ())

    def test_a_rolled_up_trip_reads_like_a_typed_one(self):
        """Dates and Countries arrive as rollups over the Trip plan relation."""
        page = {"id": "3d03-0b13", "properties": {
            "Name": {"type": "title", "title": [{"plain_text": "Island weekend"}]},
            "Dates": {"type": "rollup", "rollup": {"type": "date", "function": "date_range",
                      "date": {"start": "2023-12-26T00:00:00.000+00:00",
                               "end": "2023-12-28T00:00:00.000+00:00"}}},
            "Countries": {"type": "rollup", "rollup": {"type": "array", "function": "show_original",
                          "array": [{"type": "multi_select", "multi_select": [{"name": "Indonesia"}]},
                                    {"type": "multi_select", "multi_select": [{"name": "Indonesia"},
                                                                              {"name": "Singapore"}]}]}},
            "Extra keys": {"type": "rich_text", "rich_text": []}}}
        (trip,) = sync.trips_from_pages([page])
        self.assertEqual((trip.start, trip.end, trip.countries), ("2023-12-26", "2023-12-28", ("IDN", "SGP")))
        empty = dict(page, properties=dict(page["properties"], Dates={
            "type": "rollup", "rollup": {"type": "date", "function": "date_range", "date": None}}))
        self.assertEqual(sync.trips_from_pages([empty]), [])

    def test_a_day_trip_rollup_has_no_end(self):
        page = {"id": "x", "properties": {
            "Name": {"type": "title", "title": [{"plain_text": "Day trip"}]},
            "Dates": {"type": "rollup", "rollup": {"type": "date", "function": "date_range",
                      "date": {"start": "2024-05-18T00:00:00.000+00:00", "end": None}}},
            "Countries": {"type": "rollup", "rollup": {"type": "array", "function": "show_original", "array": []}},
            "Extra keys": {"type": "rich_text", "rich_text": []}}}
        (trip,) = sync.trips_from_pages([page])
        self.assertEqual((trip.start, trip.end, trip.countries), ("2024-05-18", "2024-05-18", ()))

    def test_an_extra_key_is_claimed_whatever_the_date(self):
        """A booking paid before the trip, listed in the trip's Extra keys."""
        pinned = sync.Trip(
            "bkk", "Bangkok weekend", "2024-08-16", "2024-08-18", ("THA",),
            keys=frozenset({"aaaa1111"}))
        trips = self.trips[:2] + [pinned]
        early = row(date="2024-07-30", merchant_country="THA", txn_key="aaaa1111")
        self.assertEqual(sync.trip_for(early, trips, self.wallets).name, "Bangkok weekend")
        other = row(date="2024-07-30", merchant_country="THA", txn_key="bbbb2222")
        self.assertIsNone(sync.trip_for(other, trips, self.wallets))

    def test_an_extra_key_never_links_an_internal_row(self):
        pinned = sync.Trip("bkk", "Bangkok weekend", "2024-08-16", "2024-08-18", ("THA",),
                           keys=frozenset({"aaaa1111"}))
        internal = row(date="2024-07-30", txn_key="aaaa1111", flow_class="Internal - YouTrip")
        self.assertIsNone(sync.trip_for(internal, [pinned], self.wallets))

    def test_extra_keys_are_read_from_the_page(self):
        client = FakeNotion()
        client.create(CFG.ds("trips"), notion.to_api(
            canonicalise({"Name": "Weekend away", "Dates": ("2026-03-07", "2026-03-10"),
                          "Countries": ["THA"], "Notes": "",
                          "Extra keys": "1a2b3c4d5e6f, deadbeef0000\nc0ffee000000"},
                         TRIP_TYPES), TRIP_TYPES))
        (trip,) = sync.read_trips(CFG, client)
        self.assertEqual(trip.keys, {"1a2b3c4d5e6f", "deadbeef0000", "c0ffee000000"})
        self.assertEqual(sync.extra_keys(None), frozenset())

    def test_a_trip_with_no_dates_is_skipped_when_read(self):
        client = FakeNotion()
        make_trip(client, "Somewhere", "2026-01-01", "2026-01-05", ["JPN"])
        client.create(CFG.ds("trips"), notion.to_api(
            canonicalise({"Name": "Draft", "Dates": None, "Countries": [],
                          "Notes": ""}, TRIP_TYPES), TRIP_TYPES))
        self.assertEqual([t.name for t in sync.read_trips(CFG, client)], ["Somewhere"])


class TripPlanDatesTest(unittest.TestCase):
    """With data_sources.trip_plans set, Dates come from the planner rows the
    Trip plan relation names, and the rollup copies them onto the trip."""

    def setUp(self):
        self.cfg = dataclasses.replace(
            CFG, data_sources={**CFG.data_sources, "trip_plans": "planner-ds"})
        self.client = FakeNotion()
        self.leg1 = self.client.create("planner-ds", {
            "Trip": {"title": [{"text": {"content": "First city"}}]},
            "Dates": {"date": {"start": "2026-03-11", "end": "2026-03-17"}}})
        self.leg2 = self.client.create("planner-ds", {
            "Trip": {"title": [{"text": {"content": "Second city"}}]},
            "Dates": {"date": {"start": "2026-03-17", "end": None}}})
        self.trip = make_trip(self.client, "Two-city trip", None, None, ["JPN"])
        self.client.patch(self.trip["id"], {"Trip plan": {"relation": [
            {"id": self.leg1["id"]}, {"id": self.leg2["id"]}]}})

    def test_the_span_is_the_planner_legs_end_to_end(self):
        state = sync.read_state(self.cfg, self.client, with_options=False)
        (trip,) = state.trips
        self.assertEqual((trip.start, trip.end), ("2026-03-11", "2026-03-17"))
        self.assertEqual(state.plan_spans[self.trip["id"]], ("2026-03-11", "2026-03-17"))

    def test_the_rollup_copies_the_dates_then_rests(self):
        state = sync.read_state(self.cfg, self.client, with_options=False)
        counts = sync.rollup_trip_dates(self.cfg, self.client, state, echo=False)
        self.assertEqual(counts["updated"], 1)
        self.assertEqual(notion.plain_range(self.client.page(self.trip["id"]), "Dates"),
                         ("2026-03-11", "2026-03-17"))
        state = sync.read_state(self.cfg, self.client, with_options=False)
        counts = sync.rollup_trip_dates(self.cfg, self.client, state, echo=False)
        self.assertEqual((counts["updated"], counts["unchanged"]), (0, 1))

    def test_a_planner_range_that_moves_is_copied_across_again(self):
        """The planner is the source, so editing a leg there and rerunning the
        rollup is what changes the trip's Dates. Nothing else does."""
        sync.rollup_trip_dates(self.cfg, self.client,
                               sync.read_state(self.cfg, self.client, with_options=False),
                               echo=False)
        self.client.patch(self.leg1["id"],
                          {"Dates": {"date": {"start": "2026-03-09", "end": "2026-03-17"}}})
        self.client.patch(self.leg2["id"],
                          {"Dates": {"date": {"start": "2026-03-17", "end": "2026-03-21"}}})
        state = sync.read_state(self.cfg, self.client, with_options=False)
        counts = sync.rollup_trip_dates(self.cfg, self.client, state, echo=False)
        self.assertEqual(counts["updated"], 1)
        self.assertEqual(notion.plain_range(self.client.page(self.trip["id"]), "Dates"),
                         ("2026-03-09", "2026-03-21"))
        # and the trip the assignment rule reads has the new span, so a row on
        # one of the two new days is claimed
        (trip,) = sync.read_state(self.cfg, self.client, with_options=False).trips
        self.assertEqual((trip.start, trip.end), ("2026-03-09", "2026-03-21"))

    def test_a_trip_with_no_plan_keeps_its_typed_dates(self):
        typed = make_trip(self.client, "Somewhere", "2026-01-01", "2026-01-05", ["JPN"])
        state = sync.read_state(self.cfg, self.client, with_options=False)
        counts = sync.rollup_trip_dates(self.cfg, self.client, state, echo=False)
        self.assertEqual(counts["no plan"], 1)
        self.assertIn("Somewhere", [t.name for t in state.trips])
        self.assertEqual(notion.plain_range(self.client.page(typed["id"]), "Dates"),
                         ("2026-01-01", "2026-01-05"))

    def test_without_a_planner_in_config_nothing_is_copied(self):
        state = sync.read_state(CFG, self.client, with_options=False)
        self.assertEqual(sync.rollup_trip_dates(CFG, self.client, state, echo=False), {})
        self.assertEqual(state.plan_spans, {})


class StateStaysTrueTest(unittest.TestCase):
    """The rollup patches Trip and Dates on pages the run is still holding, and
    `finance.py rollup` then hands that state to the snapshot step. So every
    patch is written back into the page in hand: the state has to read the way
    Notion reads once the write has gone through.

    The pages are deep-copied first, because FakeNotion hands out the very
    dicts it stores and a state sharing them would pass whatever the rollup
    did.
    """

    def setUp(self):
        self.cfg = dataclasses.replace(
            CFG, data_sources={**CFG.data_sources, "trip_plans": "planner-ds"})
        self.client = FakeNotion()
        self.trip = make_trip(self.client, "Seoul weekend", "2024-12-08", "2024-12-11", ["KOR"])
        rows = [row(txn_key="aaaa1111", date="2024-12-09", merchant_country="KOR",
                    merchant="SEOUL METRO")]
        blocks = [block(period=f"{POSB} · SGD · 2024-12", period_end="2024-12-31",
                        n_txns="1")]
        rows[0]["period"] = blocks[0]["period"]
        self.parsed = load(self.client, rows, blocks)

    def detached(self):
        """A state whose pages are copies of Notion's, not the same objects."""
        st = sync.read_state(self.cfg, self.client, with_options=False)
        st.txns = st.pages["transactions"] = copy.deepcopy(st.txns)
        st.trip_pages = st.pages["trips"] = copy.deepcopy(st.trip_pages)
        return st

    def held(self, state, ds_name, prop):
        """(what the state holds, what Notion holds) for the one page in a
        database, read the same way the snapshot reads them."""
        page = state.pages[ds_name][0]
        return notion.plain(page, prop), notion.plain(self.client.page(page["id"]), prop)

    def test_a_cleared_trip_relation_is_cleared_in_the_state_too(self):
        self.client.pages[CFG.ds("trips")].clear()
        state = self.detached()
        counts, _ = sync.rollup_trips(self.cfg, self.client, self.parsed, state,
                                      echo=False)
        self.assertEqual(counts["cleared"], 1)
        self.assertEqual(self.held(state, "transactions", "Trip"), ([], []))

    def test_an_assigned_trip_relation_reads_back_as_the_page_id(self):
        self.client.pages[CFG.ds("transactions")][0]["properties"]["Trip"] = {
            "type": "relation", "relation": []}
        state = self.detached()
        counts, _ = sync.rollup_trips(self.cfg, self.client, self.parsed, state,
                                      echo=False)
        self.assertEqual(counts["assigned"], 1)
        held, live = self.held(state, "transactions", "Trip")
        self.assertEqual(held, [self.trip["id"]])
        self.assertEqual(held, live)

    def test_dates_copied_from_the_planner_land_in_the_state_too(self):
        leg = self.client.create("planner-ds", {
            "Trip": {"title": [{"text": {"content": "Seoul weekend"}}]},
            "Dates": {"date": {"start": "2024-12-07", "end": "2024-12-12"}}})
        self.client.patch(self.trip["id"],
                          {"Trip plan": {"relation": [{"id": leg["id"]}]}})
        state = self.detached()
        counts = sync.rollup_trip_dates(self.cfg, self.client, state, echo=False)
        self.assertEqual(counts["updated"], 1)
        page = state.pages["trips"][0]
        self.assertEqual(notion.plain_range(page, "Dates"), ("2024-12-07", "2024-12-12"))
        self.assertEqual(notion.plain_range(page, "Dates"),
                         notion.plain_range(self.client.page(page["id"]), "Dates"))

    def test_a_dry_run_leaves_the_state_exactly_as_it_was_read(self):
        self.client.pages[CFG.ds("trips")].clear()
        state = self.detached()
        before = copy.deepcopy(state.txns)
        sync.rollup_trips(self.cfg, self.client, self.parsed, state, dry_run=True,
                          echo=False)
        self.assertEqual(state.txns, before)


class TripWriteTest(unittest.TestCase):
    """rollup_trips against pages that are already in Notion."""

    def setUp(self):
        self.client = FakeNotion()
        make_trip(self.client, "Seoul weekend", "2024-12-08", "2024-12-11", ["KOR"])
        self.rows = [
            row(txn_key="aaaa1111", date="2024-12-09", merchant_country="KOR",
                merchant="SEOUL METRO"),
            row(txn_key="bbbb2222", date="2024-12-09", merchant_country="SGP",
                merchant="KOPITIAM"),
        ]
        self.blocks = [block(period=f"{POSB} · SGD · 2024-12", period_end="2024-12-31",
                             n_txns="2")]
        for r in self.rows:
            r["period"] = self.blocks[0]["period"]
        self.parsed = load(self.client, self.rows, self.blocks)

    def state(self):
        return sync.read_state(CFG, self.client, with_options=False)

    def trip_of(self, key):
        page = next(p for p in self.client.query_all(CFG.ds("transactions"))
                    if notion.plain(p, "Txn key") == key)
        return notion.plain(page, "Trip")

    def test_the_upsert_already_set_the_trip(self):
        """Trip is in TXN_TYPES, so a row created by the sync arrives with it."""
        self.assertEqual(len(self.trip_of("aaaa1111")), 1)
        self.assertEqual(self.trip_of("bbbb2222"), [])
        counts, per_trip = sync.rollup_trips(CFG, self.client, self.parsed,
                                             self.state(), echo=False)
        self.assertEqual(counts["assigned"], 0)
        self.assertEqual(counts["unchanged"], 2)
        self.assertEqual(per_trip, {"Seoul weekend": 1})

    def test_a_new_trip_row_is_picked_up_without_a_sync(self):
        make_trip(self.client, "Christmas at home", "2024-12-09", "2024-12-09", ["SGP"])
        counts, per_trip = sync.rollup_trips(CFG, self.client, self.parsed,
                                             self.state(), echo=False)
        self.assertEqual(counts["assigned"], 1)
        self.assertEqual(per_trip["Christmas at home"], 1)
        self.assertEqual(len(self.trip_of("bbbb2222")), 1)

    def test_deleting_the_trip_clears_the_relation(self):
        self.client.pages[CFG.ds("trips")].clear()
        counts, _ = sync.rollup_trips(CFG, self.client, self.parsed,
                                      self.state(), echo=False)
        self.assertEqual(counts["cleared"], 1)
        self.assertEqual(self.trip_of("aaaa1111"), [])

    def test_a_dry_run_writes_nothing(self):
        self.client.pages[CFG.ds("trips")].clear()
        before = self.client.writes
        counts, _ = sync.rollup_trips(CFG, self.client, self.parsed, self.state(),
                                      dry_run=True, echo=False)
        self.assertEqual(counts["cleared"], 1)
        self.assertEqual(self.client.writes, before)
        self.assertEqual(len(self.trip_of("aaaa1111")), 1)


# ---------------------------------------------------------------- budgets

class BudgetTest(unittest.TestCase):

    def setUp(self):
        self.client = FakeNotion()
        make_category(self.client, "Eating out", "Want")
        make_category(self.client, "Salary", "Income")
        self.rows = [
            row(txn_key="aaaa1111", date="2026-01-05", amount="-12.30",
                category="Eating out"),
            row(txn_key="bbbb2222", date="2026-01-20", amount="-7.70",
                category="Eating out"),
            row(txn_key="cccc3333", date="2026-01-25", amount="900.00",
                direction="In", bank_txn_type="Salary", category="Salary"),
            row(txn_key="dddd4444", date="2026-01-26", amount="-500.00",
                flow_class="Internal - own account", category="Eating out"),
        ]
        self.blocks = [block(period=f"{POSB} · SGD · 2026-01", n_txns="4")]

    def parsed(self, rows=None):
        return parsed_of(rows or self.rows, self.blocks)

    def state(self):
        return sync.read_state(CFG, self.client, with_options=False)

    def run_rollup(self, rows=None, dry_run=False):
        return sync.rollup_budgets(CFG, self.client, self.parsed(rows), self.state(),
                                   dry_run=dry_run, echo=False)

    def budgets(self):
        return {notion.plain(p, "Name"): p
                for p in self.client.query_all(CFG.ds("budgets"))}

    def test_actual_is_positive_for_spend_and_for_income(self):
        counts = self.run_rollup()
        self.assertEqual(counts["created"], 2)
        pages = self.budgets()
        self.assertEqual(sorted(pages), ["Eating out · 2026-01", "Salary · 2026-01"])
        self.assertEqual(notion.plain(pages["Eating out · 2026-01"], "Actual"), 20.0)
        self.assertEqual(notion.plain(pages["Salary · 2026-01"], "Actual"), 900.0)

    def test_only_external_rows_count(self):
        """The 500.00 internal transfer is not spending and must not land in a
        budget."""
        self.run_rollup()
        self.assertEqual(notion.plain(self.budgets()["Eating out · 2026-01"], "Actual"),
                         20.0)

    def test_a_foreign_block_is_converted_at_the_period_fx_rate(self):
        blocks = [block(period=f"{POSB} · NOK · 2026-01", currency="NOK",
                        fx_to_sgd="0.125", n_txns="1")]
        rows = [row(txn_key="eeee5555", date="2026-01-05", amount="-80.00",
                    currency="NOK", category="Eating out",
                    period=blocks[0]["period"])]
        counts = sync.rollup_budgets(CFG, self.client, parsed_of(rows, blocks),
                                     self.state(), echo=False)
        self.assertEqual(counts["created"], 1)
        self.assertEqual(notion.plain(self.budgets()["Eating out · 2026-01"], "Actual"),
                         10.0)

    def test_a_second_run_writes_nothing(self):
        self.run_rollup()
        before = self.client.writes
        counts = self.run_rollup()
        self.assertEqual(counts["unchanged"], 2)
        self.assertEqual(self.client.writes, before)

    def test_planned_is_never_written_or_cleared(self):
        self.run_rollup()
        page = self.budgets()["Eating out · 2026-01"]
        self.client.patch(page["id"], {"Planned": {"number": 250.0}})
        moved = [dict(self.rows[0], amount="-30.00")] + self.rows[1:]
        counts = self.run_rollup(moved)
        self.assertEqual(counts["updated"], 1)
        page = self.budgets()["Eating out · 2026-01"]
        self.assertEqual(notion.plain(page, "Actual"), 37.7)
        self.assertEqual(notion.plain(page, "Planned"), 250.0)
        self.assertNotIn("Planned", BUDGET_TYPES)

    def test_the_patch_touches_only_actual(self):
        self.run_rollup()
        self.client.calls.clear()
        self.run_rollup([dict(self.rows[0], amount="-30.00")] + self.rows[1:])
        page = self.budgets()["Eating out · 2026-01"]
        want = canonicalise({"Name": "Eating out · 2026-01", "Month": "2026-01",
                             "Category": [page_properties(page, BUDGET_TYPES)["Category"][0]],
                             "Need class": "Want", "Actual": 37.7}, BUDGET_TYPES)
        self.assertEqual(page_properties(page, BUDGET_TYPES), want)
        self.assertEqual([v for v, _ in self.client.calls], ["patch"])

    def test_a_category_notion_does_not_have_is_counted_not_created(self):
        rows = [row(txn_key="ffff6666", date="2026-01-05", category="Zoology")]
        counts = self.run_rollup(rows)
        self.assertEqual(counts["no category page"], 1)
        self.assertEqual(self.budgets(), {})

    def test_a_dry_run_writes_nothing(self):
        before = self.client.writes
        counts = self.run_rollup(dry_run=True)
        self.assertEqual(counts["created"], 2)
        self.assertEqual(self.client.writes, before)
        self.assertEqual(self.budgets(), {})


# ---------------------------------------------------------------- accounts

class AccountCoverageTest(unittest.TestCase):

    def setUp(self):
        self.client = FakeNotion()
        self.pages = {no: make_account(self.client, page_id)
                      for no, page_id in CFG.accounts.items()}

    def coverage(self, account_no):
        page = self.client.page(CFG.accounts[account_no])
        return (notion.plain(page, "Statements from"), notion.plain(page, "Statements to"),
                notion.plain(page, "Data coverage"))

    def test_the_span_comes_from_the_blocks(self):
        blocks = [block(period_end="2026-01-31"), block(period_end="2025-11-30"),
                  block(period_end="2026-03-31")]
        counts = sync.rollup_accounts(CFG, self.client, parsed_of([], blocks), echo=False)
        self.assertEqual(counts["updated"], len(CFG.accounts))
        self.assertEqual(self.coverage(POSB), ("2025-11-30", "2026-03-31", "Full"))
        self.assertEqual(self.coverage(MULTIPLIER), (None, None, "None"))

    def test_an_account_with_no_blocks_reads_none(self):
        sync.rollup_accounts(CFG, self.client, parsed_of([], []), echo=False)
        for account_no in CFG.accounts:
            self.assertEqual(self.coverage(account_no), (None, None, "None"))

    def test_a_second_run_writes_nothing(self):
        blocks = [block(period_end="2026-01-31")]
        sync.rollup_accounts(CFG, self.client, parsed_of([], blocks), echo=False)
        before = self.client.writes
        counts = sync.rollup_accounts(CFG, self.client, parsed_of([], blocks), echo=False)
        self.assertEqual(counts["updated"], 0)
        self.assertEqual(counts["unchanged"], len(CFG.accounts))
        self.assertEqual(self.client.writes, before)

    def test_a_placeholder_account_is_left_alone(self):
        """An Accounts page with no [accounts] entry is a statement source that
        is not loaded yet; the rollup must not claim coverage for it."""
        ghost = make_account(self.client, "ffffffffffffffffffffffffffffffff")
        sync.rollup_accounts(CFG, self.client, parsed_of([], [block()]), echo=False)
        self.assertIsNone(notion.plain(ghost, "Data coverage"))

    def test_a_dry_run_writes_nothing(self):
        before = self.client.writes
        counts = sync.rollup_accounts(CFG, self.client, parsed_of([], [block()]),
                                      dry_run=True, echo=False)
        self.assertEqual(counts["updated"], len(CFG.accounts))
        self.assertEqual(self.client.writes, before)
        self.assertEqual(self.coverage(POSB), (None, None, None))


# ---------------------------------------------------------------- recurring

class RecurringTest(unittest.TestCase):

    def setUp(self):
        self.client = FakeNotion()
        make_payee(self.client, "Netflix")
        make_payee(self.client, "Kopitiam")
        make_payee(self.client, "Old Gym", recurring=True)

    def recurring(self):
        return {notion.plain(p, "Name"): notion.plain(p, "Recurring")
                for p in self.client.query_all(CFG.ds("payees"))}

    def test_the_rules_column_sets_and_clears_the_box(self):
        rules = [rule("Netflix", "yes"), rule("Kopitiam", ""), rule("Old Gym", "")]
        counts = sync.rollup_recurring(CFG, self.client, rules, echo=False)
        self.assertEqual(counts["set"], 1)
        self.assertEqual(counts["cleared"], 1)
        self.assertEqual(counts["unchanged"], 1)
        self.assertEqual(self.recurring(),
                         {"Netflix": True, "Kopitiam": False, "Old Gym": False})

    def test_yes_is_case_insensitive(self):
        sync.rollup_recurring(CFG, self.client, [rule("Netflix", "Yes")], echo=False)
        self.assertIs(self.recurring()["Netflix"], True)

    def test_a_second_run_writes_nothing(self):
        rules = [rule("Netflix", "yes")]
        sync.rollup_recurring(CFG, self.client, rules, echo=False)
        before = self.client.writes
        counts = sync.rollup_recurring(CFG, self.client, rules, echo=False)
        self.assertEqual(counts["unchanged"], 3)
        self.assertEqual(self.client.writes, before)

    def test_a_dry_run_writes_nothing(self):
        before = self.client.writes
        counts = sync.rollup_recurring(CFG, self.client, [rule("Netflix", "yes")],
                                       dry_run=True, echo=False)
        self.assertEqual(counts["set"], 1)
        self.assertEqual(self.client.writes, before)
        self.assertIs(self.recurring()["Netflix"], False)


# ---------------------------------------------------------------- the whole step

class RollupStepTest(unittest.TestCase):
    """sync.rollup, and the report lines it feeds."""

    def setUp(self):
        self.client = FakeNotion()
        make_category(self.client, "Eating out", "Want")
        make_payee(self.client, "Seoul Metro")
        make_trip(self.client, "Seoul weekend", "2024-12-08", "2024-12-11", ["KOR"])
        for page_id in CFG.accounts.values():
            make_account(self.client, page_id)
        self.rows = [row(txn_key="aaaa1111", date="2024-12-09", merchant_country="KOR",
                         amount="-75.00", category="Eating out", payee="Seoul Metro",
                         period=f"{POSB} · SGD · 2024-12")]
        self.blocks = [block(period=f"{POSB} · SGD · 2024-12", period_end="2024-12-31",
                             n_txns="1", total_out="75.00")]
        self.parsed = load(self.client, self.rows, self.blocks)

    def test_one_pass_leaves_nothing_to_do_on_the_next(self):
        first = sync.rollup(CFG, self.client, self.parsed,
                            [rule("Seoul Metro", "yes")], echo=False)
        self.assertEqual(first.budgets["created"], 1)
        self.assertEqual(first.accounts["updated"], len(CFG.accounts))
        self.assertEqual(first.payees["set"], 1)
        second = sync.rollup(CFG, self.client, self.parsed,
                             [rule("Seoul Metro", "yes")], echo=False)
        self.assertEqual(second.writes, 0)

    def test_the_report_sections_read_back_what_happened(self):
        rolled = sync.rollup(CFG, self.client, self.parsed, [rule("Seoul Metro", "yes")],
                             echo=False)
        text = sync.report(CFG, self.parsed, None, [], rollup_result=rolled)
        self.assertIn("budgets refreshed", text)
        self.assertIn("trips assigned", text)
        self.assertIn("1 rows  Seoul weekend", text)
        self.assertIn("1 row(s) created", text)

    def test_the_report_says_so_when_the_rollup_has_not_run(self):
        text = sync.report(CFG, self.parsed, None, [])
        self.assertIn("(the rollup has not run)", text)


class ConfigTest(unittest.TestCase):
    """The config shape the rollup needs, against the fixture config. The
    owner's own server/finance/config.toml gets the same two assertions when it is
    present, which is what catches a real config that has drifted."""

    def test_the_two_new_data_sources_are_configured(self):
        for name in ("budgets", "trips"):
            self.assertRegex(CFG.ds(name), r"^[0-9a-f-]{36}$")

    def test_the_three_wallet_feeds_are_the_per_currency_accounts(self):
        self.assertEqual(CFG.wallet_accounts(), {WALLET, "555555555555555", "666-66666-6"})

    def test_the_real_config_has_the_same_shape(self):
        try:
            cfg = load_config()
        except ConfigMissing as e:
            self.skipTest(str(e))
        for name in ("budgets", "trips"):
            self.assertRegex(cfg.ds(name), r"^[0-9a-f-]{36}$")
        # every multi-currency wallet feed, and nothing else, counts as a wallet
        wallets = {f.account_no for f in cfg.folders if f.parser in ("youtrip", "revolut", "wise")}
        self.assertEqual(cfg.wallet_accounts(), wallets)
        self.assertGreaterEqual(len(wallets), 1)


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------- budget seed

class BudgetSeedTest(unittest.TestCase):
    """sync.seed_budgets: rows for a month ahead, Planned from the median."""

    def setUp(self):
        self.client = FakeNotion()
        self.cats = {name: make_category(self.client, name, kind) for name, kind in [
            ("Eating out", "Want"), ("Transport", "Need"), ("Rent", "Need"),
            ("Investments", "Investment"), ("Salary", "Income"),
            ("Between own accounts", "Transfer")]}
        # six months of Actual for Eating out, four for Transport, none for Rent
        for m, v in zip(["2026-04", "2026-05", "2026-06", "2026-07", "2026-08", "2026-09"],
                        [681, 800, 1089, 713, 836, 479]):
            self.budget("Eating out", m, v)
        for m, v in zip(["2026-06", "2026-07", "2026-08", "2026-09"], [119, 237, 415, 40]):
            self.budget("Transport", m, v)
        self.budget("Investments", "2026-09", 1000)

    def budget(self, category, month, actual, planned=None):
        props = {"Name": {"title": [{"text": {"content": f"{category} · {month}"}}]},
                 "Category": {"relation": [{"id": self.cats[category]["id"]}]},
                 "Month": {"rich_text": [{"text": {"content": month}}]},
                 "Actual": {"number": float(actual)}}
        if planned is not None:
            props["Planned"] = {"number": float(planned)}
        return self.client.create(CFG.ds("budgets"), props)

    def rows(self, month):
        return {notion.plain(p, "Name").rsplit(" · ", 1)[0]: p
                for p in self.client.query_all(CFG.ds("budgets"))
                if notion.plain(p, "Month") == month}

    def test_months_before(self):
        self.assertEqual(sync.months_before("2026-02", 3), ["2025-11", "2025-12", "2026-01"])

    def test_creates_need_and_want_rows_with_the_median(self):
        counts, _ = sync.seed_budgets(CFG, self.client, "2026-10", echo=False)
        rows = self.rows("2026-10")
        self.assertEqual(sorted(rows), ["Eating out", "Rent", "Transport"])
        self.assertEqual(counts["created"], 3)
        self.assertEqual(notion.plain(rows["Eating out"], "Planned"), 757)   # median of six
        # Transport: two missing months count as 0 -> [0, 0, 40, 119, 237, 415] -> 79.5 -> 80
        self.assertEqual(notion.plain(rows["Transport"], "Planned"), 80)
        self.assertIsNone(notion.plain(rows["Rent"], "Planned"))             # median 0
        self.assertEqual(notion.plain(rows["Rent"], "Actual"), 0)
        self.assertEqual(notion.plain(rows["Rent"], "Need class"), "Need")   # so the hub view can group it
        self.assertEqual(notion.plain(rows["Eating out"], "Need class"), "Want")
        self.assertEqual(notion.plain(rows["Eating out"], "Month"), "2026-10")

    def test_all_adds_savings_and_investment_never_income_or_transfer(self):
        sync.seed_budgets(CFG, self.client, "2026-10", everything=True, echo=False)
        self.assertEqual(sorted(self.rows("2026-10")),
                         ["Eating out", "Investments", "Rent", "Transport"])

    def test_a_typed_planned_is_kept_and_an_empty_one_filled(self):
        self.budget("Eating out", "2026-10", 0, planned=600)
        self.budget("Transport", "2026-10", 0)
        counts, lines = sync.seed_budgets(CFG, self.client, "2026-10", echo=False)
        rows = self.rows("2026-10")
        self.assertEqual(notion.plain(rows["Eating out"], "Planned"), 600)
        self.assertEqual(notion.plain(rows["Transport"], "Planned"), 80)
        self.assertEqual((counts["created"], counts["filled"], counts["kept"]), (1, 1, 1))
        self.assertTrue(any(l.startswith("  keep    Eating out") for l in lines))

    def test_a_rerun_writes_nothing(self):
        sync.seed_budgets(CFG, self.client, "2026-10", echo=False)
        before = self.client.writes
        counts, _ = sync.seed_budgets(CFG, self.client, "2026-10", echo=False)
        self.assertEqual(self.client.writes, before)
        self.assertEqual((counts["created"], counts["filled"]), (0, 0))

    def test_a_dry_run_writes_nothing(self):
        before = self.client.writes
        counts, _ = sync.seed_budgets(CFG, self.client, "2026-10", dry_run=True, echo=False)
        self.assertEqual(counts["created"], 3)
        self.assertEqual(self.rows("2026-10"), {})
        self.assertEqual(self.client.writes, before)

    def test_a_month_the_sync_has_not_covered_is_skipped_not_zero(self):
        """Seeding November on 4 October: October has no rows yet, so the
        window still ends at September."""
        sync.seed_budgets(CFG, self.client, "2026-11", echo=False)
        self.assertEqual(notion.plain(self.rows("2026-11")["Eating out"], "Planned"), 757)

    def test_no_history_at_all_is_refused(self):
        with self.assertRaises(ValueError):
            sync.seed_budgets(CFG, self.client, "2026-01", echo=False)

    def test_the_window_is_honoured(self):
        sync.seed_budgets(CFG, self.client, "2026-10", window=2, echo=False)
        # last two months of Eating out: 836, 479 -> 657.5 -> 658
        self.assertEqual(notion.plain(self.rows("2026-10")["Eating out"], "Planned"), 658)

    def test_a_bad_month_is_refused(self):
        with self.assertRaises(ValueError):
            sync.seed_budgets(CFG, self.client, "2026-13", echo=False)
        with self.assertRaises(ValueError):
            sync.seed_budgets(CFG, self.client, "Oct 2026", echo=False)

    def test_current_is_ticked_on_the_month_of_the_run_only(self):
        from datetime import date
        sync.seed_budgets(CFG, self.client, "2026-10", echo=False, today=date(2026, 10, 3))
        rows = self.rows("2026-10")
        self.assertTrue(notion.plain(rows["Eating out"], "Current"))
        # a later rollup run in November clears it, even with no November actuals
        state = sync.read_state(CFG, self.client, with_options=False)
        counts = sync.rollup_budgets(CFG, self.client, parsed_of([], []), state, echo=False,
                                     today=date(2026, 11, 2))
        self.assertEqual(counts["current flag"], 3)
        self.assertFalse(notion.plain(self.rows("2026-10")["Eating out"], "Current"))
        for m in ("2026-09",):
            self.assertFalse(notion.plain(self.rows(m)["Eating out"], "Current"))

    def test_the_rollup_then_refreshes_actual_and_leaves_planned(self):
        sync.seed_budgets(CFG, self.client, "2026-10", echo=False)
        rows = [row(txn_key="eeee5555", date="2026-10-03", amount="-20.00",
                    category="Eating out", period=f"{POSB} · SGD · 2026-10")]
        blocks = [block(period=f"{POSB} · SGD · 2026-10", n_txns="1")]
        state = sync.read_state(CFG, self.client, with_options=False)
        sync.rollup_budgets(CFG, self.client, parsed_of(rows, blocks), state, echo=False)
        page = self.rows("2026-10")["Eating out"]
        self.assertEqual(notion.plain(page, "Actual"), 20)
        self.assertEqual(notion.plain(page, "Planned"), 757)


# ------------------------------------------------------- payees --prune

class StalePayeesTest(unittest.TestCase):
    """finance.stale_payees: the rows a rename leaves behind.

    Renaming a payee in the rules makes the loader create a row under the new
    name; the old row stays, with its transactions still pointing at whichever
    row the rules now name. Those empty leftovers are what --prune trashes.
    """

    def page(self, name, rows=0):
        return {"id": f"id-{name}", "properties": {
            "Name": {"type": "title", "title": [{"plain_text": name}]},
            "Transactions": {"type": "relation",
                             "relation": [{"id": f"t{i}"} for i in range(rows)]}}}

    def cli(self):
        """finance.py, loaded by path: `import finance` finds the package."""
        import importlib.util
        from pathlib import Path
        root = Path(__file__).resolve().parent.parent
        spec = importlib.util.spec_from_file_location("finance_cli", root / "finance.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def stale(self, have, want):
        return self.cli().stale_payees({p: self.page(p, n) for p, n in have.items()}, want)

    def test_a_row_no_rule_names_and_nothing_links_to_is_stale(self):
        self.assertEqual(self.stale({"Old Name": 0}, {}), ["Old Name"])

    def test_a_row_the_rules_still_name_is_never_stale(self):
        self.assertEqual(self.stale({"Kept": 0}, {"Kept": {}}), [])

    def test_a_row_with_transactions_is_left_alone_however_it_is_named(self):
        """A rule that stopped matching is a rules problem to look at, not a
        page to throw away: the rows are still there and still say this payee."""
        self.assertEqual(self.stale({"Has rows": 3}, {}), [])

    def test_several_are_reported_in_name_order(self):
        have = {"Zed": 0, "Alice": 0, "Bob": 2, "Carol": 0}
        self.assertEqual(self.stale(have, {"Carol": {}}), ["Alice", "Zed"])

    def test_a_row_with_no_transactions_property_at_all_is_stale(self):
        page = {"id": "x", "properties": {
            "Name": {"type": "title", "title": [{"plain_text": "Bare"}]}}}
        self.assertEqual(self.cli().stale_payees({"Bare": page}, {}), ["Bare"])
