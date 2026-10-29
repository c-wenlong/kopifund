"""The 2-letter country slot on a card row (audit: ID, IN and DE read as the USA).

A US merchant prints its state where everyone else prints a country, and ID,
IN and DE are both: Idaho, Indiana and Delaware, or Indonesia, India and
Germany. They read as the country unless the town before them is a US city.
Every merchant here is invented."""
import isolate  # noqa: F401  first: no test writes the real record or cache
import tempfile
import unittest
from pathlib import Path

from finance.parsers import parse_statements as ps
from finance.parsers import places


def card_country(merchant):
    r = dict(detail=[merchant, "REF NO.: 123"], txn_type="Debit Card Transaction", date="2025-05-20")
    ps.enrich_card(r)
    return r["merchant"], r["merchant_country"]


class CardSlot(unittest.TestCase):
    def setUp(self):
        places.load(None)
        self.addCleanup(places.load, None)

    def test_a_code_that_is_a_country_and_a_state_reads_as_the_country(self):
        self.assertEqual(card_country("WARUNG MADE   DENPASAR   ID 15 MAY"), ("WARUNG MADE   DENPASAR", "IDN"))
        self.assertEqual(card_country("TAJ HOTEL   MUMBAI   IN 03 JUN")[1], "IND")
        self.assertEqual(card_country("REWE   BERLIN   DE 09 JUL")[1], "DEU")
        self.assertEqual(card_country("SOME SHOP   SOMEWHERE   ID 15 MAY")[1], "IDN")

    def test_it_reads_as_the_state_after_a_us_city(self):
        path = Path(tempfile.mkdtemp()) / "places.csv"
        path.write_text("place,country,match\nBOISE,USA,prefix\n", encoding="utf-8")
        places.load(str(path))
        self.assertEqual(card_country("CORNER DINER   BOISE   ID 15 MAY")[1], "USA")

    def test_a_state_that_is_no_country_in_the_table_is_still_the_usa(self):
        for merchant in ("TARGET   AUSTIN   TX 15 MAY", "DINER   SEATTLE   WA 15 MAY",
                         "SHOP   ANYWHERE   CA 15 MAY", "SHOP   ANYWHERE   GA 15 MAY"):
            with self.subTest(merchant):
                self.assertEqual(card_country(merchant)[1], "USA")

    def test_a_country_that_is_no_state_is_unchanged(self):
        self.assertEqual(card_country("CAFE   PARIS   FR 15 MAY")[1], "FRA")
        self.assertEqual(card_country("KOPITIAM   SINGAPORE   SG 15 MAY")[1], "SGP")


class WalletMerchant(unittest.TestCase):
    """places.split_country, which every wallet parser uses, the same way."""

    def setUp(self):
        places.load(None)
        self.addCleanup(places.load, None)

    def test_the_city_in_the_table_is_not_overruled_by_a_state(self):
        self.assertEqual(places.split_country("SHOP,DENPASAR,ID", None, "SGD"), ("SHOP,DENPASAR", "IDN"))
        self.assertEqual(places.split_country("SHOP,MUMBAI,IN", None, "SGD")[1], "IND")

    def test_a_us_city_keeps_the_state_reading(self):
        path = Path(tempfile.mkdtemp()) / "places.csv"
        path.write_text("place,country,match\nBOISE,USA,prefix\n", encoding="utf-8")
        places.load(str(path))
        self.assertEqual(places.split_country("DINER,BOISE,ID", None, "SGD")[1], "USA")
        self.assertEqual(places.split_country("DINER,AUSTIN,TX", None, "SGD")[1], "USA")


if __name__ == "__main__":
    unittest.main()
