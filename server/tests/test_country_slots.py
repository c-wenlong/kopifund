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
from tests import parse_support as sup


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


def places_csv(body):
    path = Path(tempfile.mkdtemp()) / "places.csv"
    path.write_text("place,country,match\n" + body, encoding="utf-8")
    places.load(str(path))


class ThroughTheCardParser(unittest.TestCase):
    """parse_card collapses the spacing between the merchant and the town
    before enrich_card reads the slot, so the town is found in the last
    words of the line (review: card town separators are lost)."""

    ROW = "OPENAI *CHATGPT SUBSCR SAN FRANCISCO CA 10 NOV"

    def setUp(self):
        places.load(None)
        self.addCleanup(places.load, None)

    def country(self, printed):
        run = sup.run_statements(["card.txt"], lines={"card.txt": sup.edit_line("card.txt", [self.ROW], self.ROW, printed)})
        [r] = [r for r in run.rows if r["merchant"].startswith("CORNER DINER")]
        return r["merchant_country"]

    def test_a_us_town_from_places_csv_reads_as_the_state(self):
        places_csv("BOISE,USA,prefix\n")
        self.assertEqual(self.country("CORNER DINER     BOISE        ID 10 NOV"), "USA")

    def test_an_exact_us_town_reads_as_the_state(self):
        places_csv("BOISE,USA,exact\n")
        self.assertEqual(self.country("CORNER DINER     BOISE        ID 10 NOV"), "USA")

    def test_an_unknown_town_reads_as_the_country(self):
        self.assertEqual(self.country("CORNER DINER     DENPASAR     ID 10 NOV"), "IDN")

    def test_a_full_town_is_not_cut_to_a_shipped_city(self):
        places_csv("NORTH MANCHESTER,USA,prefix\n")
        self.assertEqual(self.country("CORNER DINER NORTH MANCHESTER IN 10 NOV"), "USA")

    def test_the_town_outvotes_a_us_city_earlier_in_the_name(self):
        self.assertEqual(self.country("CORNER DINER AUSTINS  DENPASAR  ID 10 NOV"), "IDN")


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

    def test_a_full_town_from_places_csv_is_not_cut_to_a_shipped_city(self):
        places_csv("NORTH MANCHESTER,USA,prefix\n")
        self.assertEqual(places.split_country("DINER,NORTH MANCHESTER,IN", None, "SGD")[1], "USA")

    def test_a_cut_off_town_still_reads_by_its_prefix(self):
        self.assertEqual(places.split_country("DINER,SAN FRANCIS,IN", None, "SGD")[1], "USA")

    def test_an_exact_us_town_keeps_the_state_reading(self):
        places_csv("BOISE,USA,exact\n")
        self.assertEqual(places.split_country("DINER,BOISE,ID", None, "SGD")[1], "USA")


if __name__ == "__main__":
    unittest.main()
