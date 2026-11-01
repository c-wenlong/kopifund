"""No test writes into a real data folder (server/tests/__init__.py redirects both)."""
import isolate  # noqa: F401  first: no test writes the real record or cache
import unittest

from finance import db
from finance.paths import cache_dir, data_root


class IsolationTest(unittest.TestCase):
    def test_the_record_is_not_the_data_folders(self):
        self.assertNotEqual(db.db_path(), data_root() / "kopifund.db")
        self.assertFalse(db.db_path().is_relative_to(data_root()))

    def test_the_cache_is_not_the_data_folders(self):
        self.assertNotEqual(cache_dir(), data_root() / "cache")


if __name__ == "__main__":
    unittest.main()
