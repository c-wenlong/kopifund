"""txn_key is what stops a re-import creating a second copy of every row.

Notion holds the current keys, so the properties they hash and the way repeats
are numbered are frozen. These tests pin the two things that matter: the key set
does not depend on the order the files were read in, and adding a row never
moves the keys already issued.
"""
import isolate  # noqa: F401  first: no test writes the real record or cache
import unittest

import parse_support as sup
from parse_support import ps


def row(**over):
    base = dict(date="2026-06-14", account_no="130-24680-5", currency="SGD",
                amount=-12.64, reference="IP504794944894663217",
                merchant="THE COFFEE PLACE", counterparty=None,
                bank_txn_type="Debit Card Transaction")
    base.update(over)
    return base


class KeyInputs(unittest.TestCase):
    """The seven fields the key hashes, and nothing else."""

    def test_changing_the_merchant_changes_the_key(self):
        a, b = row(), row(merchant="SOME OTHER SHOP")
        ps.add_keys([a])
        ps.add_keys([b])
        self.assertNotEqual(a["txn_key"], b["txn_key"])

    def test_each_hashed_field_changes_the_key(self):
        base = row()
        ps.add_keys([base])
        for field, value in (("date", "2026-06-15"), ("account_no", "120-345678-9"),
                             ("currency", "USD"), ("amount", -12.65),
                             ("reference", "IP000000000000000000"),
                             ("merchant", "ANOTHER SHOP"),
                             ("bank_txn_type", "Funds Transfer")):
            with self.subTest(field):
                other = row(**{field: value})
                ps.add_keys([other])
                self.assertNotEqual(base["txn_key"], other["txn_key"])

    def test_fields_outside_the_hash_do_not_change_the_key(self):
        base, same = row(), row()
        ps.add_keys([base])
        same.update(memo="a memo", balance_after=99.0, source="elsewhere.pdf",
                    flow_class="Internal - PayLah", card_last4="5454")
        ps.add_keys([same])
        self.assertEqual(base["txn_key"], same["txn_key"])

    def test_counterparty_stands_in_when_there_is_no_merchant(self):
        a = row(merchant=None, counterparty="MX LX KAXX MEX")
        b = row(merchant=None, counterparty="SOMEONE ELSE")
        ps.add_keys([a])
        ps.add_keys([b])
        self.assertNotEqual(a["txn_key"], b["txn_key"])


class KeyStability(unittest.TestCase):

    def test_key_set_is_independent_of_input_order(self):
        forward = sup.run_statements(["posb-deposit.txt", "paylah.txt"])
        backward = sup.run_statements(["paylah.txt", "posb-deposit.txt"])
        self.assertEqual(forward.keys(), backward.keys())
        self.assertEqual(len(forward.rows), len(forward.keys()))

    def test_youtrip_key_set_is_independent_of_input_order(self):
        forward = sup.run_youtrip(["youtrip-sgd.txt", "youtrip-nok.txt"])
        backward = sup.run_youtrip(["youtrip-nok.txt", "youtrip-sgd.txt"])
        self.assertEqual(forward.keys(), backward.keys())

    def test_appending_an_identical_row_leaves_the_existing_keys_alone(self):
        rows = [row(date="2026-06-12"), row(), row(date="2026-06-20")]
        ps.add_keys(rows)
        before = [r["txn_key"] for r in rows]

        grown = rows + [row()]                 # the same row printed twice
        ps.add_keys(grown)
        self.assertEqual(before, [r["txn_key"] for r in grown[:3]])
        self.assertNotIn(grown[3]["txn_key"], before)
        self.assertEqual(4, len({r["txn_key"] for r in grown}))

    def test_a_repeat_is_numbered_by_position_not_by_content(self):
        """Two genuinely identical rows still get distinct keys."""
        rows = [row(), row()]
        ps.add_keys(rows)
        self.assertNotEqual(rows[0]["txn_key"], rows[1]["txn_key"])

    def test_a_new_row_elsewhere_does_not_disturb_earlier_keys(self):
        rows = [row(date="2026-06-12"), row(date="2026-06-20")]
        ps.add_keys(rows)
        before = [r["txn_key"] for r in rows]
        grown = [rows[0], row(date="2026-06-15", merchant="NEW SHOP"), rows[1]]
        ps.add_keys(grown)
        self.assertEqual(before, [grown[0]["txn_key"], grown[2]["txn_key"]])

    def test_the_key_is_twelve_hex_characters(self):
        rows = [row()]
        ps.add_keys(rows)
        self.assertRegex(rows[0]["txn_key"], r"^[0-9a-f]{12}$")


class NoCollisions(unittest.TestCase):

    def test_every_fixture_row_has_its_own_key(self):
        parsed = sup.run_statements(["dbs-consolidated.txt", "posb-deposit.txt",
                                     "posb-deposit-dec-jan.txt", "paylah.txt",
                                     "card-only-month.txt"])
        self.assertEqual(len(parsed.rows), len(parsed.keys()))

    def test_every_youtrip_fixture_row_has_its_own_key(self):
        parsed = sup.run_youtrip(["youtrip-sgd.txt", "youtrip-sgd-gap.txt",
                                  "youtrip-nok.txt", "youtrip-empty.txt"])
        self.assertEqual(len(parsed.rows), len(parsed.keys()))


if __name__ == "__main__":
    unittest.main()
