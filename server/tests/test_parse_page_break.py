#!/usr/bin/env python3
"""A page break between a card transaction and its detail lines.

The consolidated statement sometimes ends a page right after the dated line,
so the merchant and card lines arrive after four blank lines and the page
footer. The parser used to close the transaction on the first blank line and
lose the merchant; several consolidated-statement rows over a few months came
out with no merchant that way. A transaction still waiting for its detail now
survives the gap, while one that already has detail still closes on a blank.
"""
import isolate  # noqa: F401  first: no test writes the real record or cache
import re
import unittest

import parse_support as sup
from parse_support import ps

FIXTURE = "dbs-consolidated.txt"
FOOTER = ("SG40020000876 (08/2014) DBS Co. Reg. No. 196800306E GST Reg No: "
          "MR-8500180-3 / POSB Biz Reg No. 52880148A")
DATED = re.compile(r"^\s+\d\d/\d\d/\d{4}\s+Debit Card Transaction")


def split_first_card_row(text):
    """Move the first card transaction's two detail lines behind a page gap
    and the footer. Returns (text, merchant line) so the test knows the
    merchant it should see back."""
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        if DATED.match(ln) and i + 2 < len(lines) and re.match(r"^\s+\d{4}-", lines[i + 2]):
            merchant, card = lines[i + 1], lines[i + 2]
            lines[i + 1:i + 3] = ["", "", "", "", FOOTER, merchant, card]
            return "\n".join(lines) + "\n", merchant.strip()
    raise AssertionError("no card transaction with a merchant and card line")


class CardDetailAfterPageBreak(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        text = sup.text(FIXTURE)
        cls.bent, cls.merchant = split_first_card_row(text)
        cls.before = sup.run_statements([FIXTURE])
        cls.after = sup.run_statements([FIXTURE], lines={FIXTURE: cls.bent})

    def test_the_merchant_survives_the_gap(self):
        name, _country, _day = ps.split_merchant(self.merchant)
        self.assertIn(name, [r["merchant"] for r in self.after.rows])

    def test_the_bent_text_parses_to_the_same_rows(self):
        same = [(r["date"], r["amount"], r["merchant"]) for r in self.before.rows]
        bent = [(r["date"], r["amount"], r["merchant"]) for r in self.after.rows]
        self.assertEqual(same, bent)

    def test_the_keys_do_not_move(self):
        self.assertEqual(self.before.keys(), self.after.keys())

    def test_nothing_from_the_footer_leaks_into_a_row(self):
        for r in self.after.rows:
            blob = " ".join(str(v) for v in r.values())
            self.assertNotIn("SG40020000876", blob)
            self.assertNotIn("Carried Forward", blob)


if __name__ == "__main__":
    unittest.main()
