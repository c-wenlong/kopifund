#!/usr/bin/env python3
"""The upsert engine against a fake Notion: create, patch, leave alone, archive.

The property these tests pin down is that a month with no change issues no
writes at all, and that a change to one property patches that one property on
that one page.
"""
import isolate  # noqa: F401  first: no test writes the real record or cache
import unittest
from pathlib import Path

from fake_notion import ACCOUNT, CARD, CFG, FakeNotion, block, row

from finance import notion, sync
from finance.model import (PERIOD_TYPES, TXN_TYPES, page_properties,
                           period_properties, txn_properties)


def parsed_of(rows, blocks):
    """A Parsed as sync.parse would have produced it."""
    return sync.Parsed(blocks=list(blocks), rows=list(rows), csv_dir=Path("."))


def plan_for(client, rows, blocks):
    parsed = parsed_of(rows, blocks)
    state = sync.read_state(CFG, client)
    return parsed, state, sync.build_plan(CFG, parsed, state)


class UpsertTest(unittest.TestCase):

    def setUp(self):
        self.client = FakeNotion()
        self.rows = [row(), row(txn_key="bbbb2222", date="2026-01-07", amount="-4.50",
                              merchant="BUS/MRT", card_last4="", purchase_date="",
                              balance_after="984.20")]
        self.blocks = [block(n_txns="2", total_out="16.80", closing="984.20")]

    def load(self, rows=None, blocks=None, archive_orphans=False):
        parsed, state, plan = plan_for(self.client, rows or self.rows, blocks or self.blocks)
        done = sync.upsert(CFG, self.client, parsed, state, plan,
                           archive_orphans=archive_orphans, echo=False)
        return parsed, plan, done

    def test_first_run_creates_everything(self):
        parsed, plan, done = self.load()
        self.assertEqual(len(plan.create_periods), 1)
        self.assertEqual(len(plan.create_txns), 2)
        self.assertEqual(plan.writes, 3)
        self.assertEqual(self.client.verbs(), ["create", "create", "create"])
        pages = self.client.query_all(CFG.ds("transactions"))
        self.assertEqual({notion.plain(p, "Txn key") for p in pages}, {"aaaa1111", "bbbb2222"})

    def test_created_page_holds_every_loader_property(self):
        self.load()
        page = self.client.query_all(CFG.ds("transactions"))[0]
        self.assertEqual(notion.plain(page, "Name"), "2026-01-05 · KOPITIAM SGP · -12.30")
        self.assertEqual(notion.plain(page, "Amount"), -12.30)
        self.assertEqual(notion.plain(page, "Purchase date"), "2026-01-04")
        self.assertEqual(notion.plain(page, "Card"), [CFG.cards[CARD]])
        self.assertEqual(notion.plain(page, "Merchant country"), "SGP")
        period = self.client.query_all(CFG.ds("period_balances"))[0]
        self.assertEqual(notion.plain(page, "Period"), [period["id"]])

    def test_second_run_writes_nothing(self):
        self.load()
        before = self.client.writes
        parsed, state, plan = plan_for(self.client, self.rows, self.blocks)
        self.assertEqual(plan.writes, 0)
        self.assertEqual(plan.same_txns, 2)
        self.assertEqual(plan.same_periods, 1)
        self.assertEqual(plan.orphan_txns, [])
        sync.upsert(CFG, self.client, parsed, state, plan, echo=False)
        self.assertEqual(self.client.writes, before)

    def test_changed_flow_class_patches_one_property(self):
        self.load()
        changed = [row(flow_class="Internal - own account"), self.rows[1]]
        parsed, state, plan = plan_for(self.client, changed, self.blocks)
        self.assertEqual(len(plan.patch_txns), 1)
        self.assertEqual(plan.patch_txns[0][3], ["Flow class"])
        self.assertEqual(plan.same_txns, 1)
        sync.upsert(CFG, self.client, parsed, state, plan, echo=False)
        self.assertEqual(self.client.verbs()[-1], "patch")
        self.assertEqual(sum(v == "patch" for v in self.client.verbs()), 1)
        page = next(p for p in self.client.query_all(CFG.ds("transactions"))
                    if notion.plain(p, "Txn key") == "aaaa1111")
        self.assertEqual(notion.plain(page, "Flow class"), "Internal - own account")
        self.assertEqual(notion.plain(page, "Merchant raw"), "KOPITIAM SGP")

    def test_changed_block_total_patches_the_block(self):
        self.load()
        blocks = [block(n_txns="2", total_out="16.80", closing="984.20", opening="1002.00")]
        _, _, plan = plan_for(self.client, self.rows, blocks)
        self.assertEqual([p[3] for p in plan.patch_periods], [["Opening balance"]])
        self.assertEqual(plan.create_txns, [])

    def test_orphans_are_reported_not_archived(self):
        self.load()
        fewer = self.rows[:1]
        blocks = [block(n_txns="1")]
        parsed, state, plan = plan_for(self.client, fewer, blocks)
        self.assertEqual([notion.plain(p, "Txn key") for p in plan.orphan_txns], ["bbbb2222"])
        sync.upsert(CFG, self.client, parsed, state, plan, echo=False)
        self.assertNotIn("archive", self.client.verbs())
        self.assertEqual(len(self.client.query_all(CFG.ds("transactions"))), 2)

    def test_archive_orphans_flag_archives_them(self):
        self.load()
        parsed, state, plan = plan_for(self.client, self.rows[:1], [block(n_txns="1")])
        sync.upsert(CFG, self.client, parsed, state, plan, archive_orphans=True, echo=False)
        self.assertIn("archive", self.client.verbs())
        left = self.client.query_all(CFG.ds("transactions"))
        self.assertEqual([notion.plain(p, "Txn key") for p in left], ["aaaa1111"])

    def test_verify_is_clean_after_a_load(self):
        parsed, plan, done = self.load()
        plan2, problems, state = sync.verify(CFG, self.client, parsed)
        self.assertEqual(problems, [])
        self.assertEqual(plan2.writes, 0)

    def test_verify_sees_a_missing_row(self):
        self.load()
        extra = self.rows + [row(txn_key="cccc3333", date="2026-01-09", amount="-1.00")]
        plan, problems, state = sync.verify(CFG, self.client, parsed_of(extra, self.blocks))
        self.assertIn("verify-missing", [p.code for p in problems])

    def test_block_checks_catch_a_row_count_mismatch(self):
        self.load()
        parsed = parsed_of(self.rows, [block(n_txns="3", total_out="16.80")])
        _, problems, _ = sync.verify(CFG, self.client, parsed)
        self.assertIn("block-row-count", [p.code for p in problems])


class PropertyModelTest(unittest.TestCase):
    """The canonical property dict is what makes the diff trustworthy."""

    def test_every_loader_property_is_present(self):
        props = txn_properties(CFG, row(), "period-id")
        self.assertEqual(sorted(props), sorted(TXN_TYPES))

    def test_empty_values_are_canonical(self):
        props = txn_properties(CFG, row(card_last4="", purchase_date="", memo="",
                                        merchant_country="", balance_after=""), "p")
        self.assertEqual(props["Card"], ())
        self.assertIsNone(props["Purchase date"])
        self.assertIsNone(props["Merchant country"])
        self.assertIsNone(props["Balance after"])
        self.assertEqual(props["Memo"], "")

    def test_row_carries_its_blocks_fx_rate(self):
        """The block's fx_to_sgd is copied onto every row of the block, so the
        Amount SGD formula (and any rollup over it) reads a plain number."""
        client = FakeNotion()
        nok = block(period=f"{ACCOUNT} · NOK · 2026-01", currency="NOK", fx_to_sgd="0.125",
                    n_txns="1", total_out="80.00", opening="1000.00", closing="920.00",
                    closing_sgd="115.00")
        rows = [row(), row(txn_key="dddd4444", currency="NOK", amount="-80.00",
                           period=nok["period"], balance_after="920.00", card_last4="",
                           purchase_date="")]
        _, _, plan = plan_for(client, rows, [block(), nok])
        rates = {r["txn_key"]: want["FX rate"] for r, want in plan.create_txns}
        self.assertIsNone(rates[row()["txn_key"]])
        self.assertEqual(rates["dddd4444"], 0.125)

    def test_relation_ids_compare_without_dashes(self):
        props = txn_properties(CFG, row(), "0000aaaa-bbbb-cccc-dddd-eeeeffff0001")
        self.assertEqual(props["Period"], ("0000aaaabbbbccccddddeeeeffff0001",))

    def test_round_trip_through_the_api_shape(self):
        client = FakeNotion()
        props = period_properties(CFG, block())
        page = client.create(CFG.ds("period_balances"), notion.to_api(props, PERIOD_TYPES))
        self.assertEqual(page_properties(page, PERIOD_TYPES), props)


if __name__ == "__main__":
    unittest.main()
