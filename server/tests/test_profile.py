import isolate  # noqa: F401  first: no test writes the real record or cache
import json
import os
import stat
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from finance import profile


class Profile(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"KOPIFUND_DATA": self.dir.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.dir.cleanup()

    def test_unset_reads_as_none(self):
        self.assertEqual(profile.load(), {"birth_date": None, "problem": None})

    def test_saves_into_the_data_folder_for_the_owner_only(self):
        out = profile.save({"birth_date": "2000-08-03"}, today=date(2026, 10, 12))
        self.assertEqual(out, {"birth_date": "2000-08-03", "problem": None})
        f = Path(self.dir.name) / "profile.json"
        self.assertEqual(json.loads(f.read_text()), {"birth_date": "2000-08-03"})
        self.assertEqual(stat.S_IMODE(f.stat().st_mode), 0o600)
        self.assertEqual(profile.load(), {"birth_date": "2000-08-03", "problem": None})

    def test_blank_clears_it(self):
        profile.save({"birth_date": "2000-08-03"}, today=date(2026, 10, 12))
        self.assertEqual(profile.save({"birth_date": ""})["birth_date"], None)

    def test_refuses_what_is_not_a_past_date(self):
        # today and the day before 120 years ago are out; the day 120 years ago is in
        for bad in ("03-08-2000", "2000-13-01", "2099-01-01", "2026-10-12", "1906-10-11"):
            with self.subTest(bad=bad), self.assertRaises(profile.ProfileRefused):
                profile.save({"birth_date": bad}, today=date(2026, 10, 12))
        self.assertFalse((Path(self.dir.name) / "profile.json").exists())

    def test_the_boundaries_that_are_allowed(self):
        today = date(2026, 10, 12)
        for good in ("2026-10-11", "1906-10-12"):
            with self.subTest(good=good):
                self.assertEqual(profile.save({"birth_date": good}, today=today)["birth_date"], good)

    def test_a_damaged_file_is_reported_not_trusted(self):
        f = Path(self.dir.name) / "profile.json"
        f.write_text("[]")
        out = profile.load()
        self.assertIsNone(out["birth_date"])
        self.assertIn("damaged", out["problem"])
        f.write_text(json.dumps({"birth_date": "not-a-date"}))
        out = profile.load()
        self.assertIsNone(out["birth_date"])
        self.assertIn("birth_date", out["problem"])

    def test_saving_over_a_damaged_file_keeps_a_copy(self):
        f = Path(self.dir.name) / "profile.json"
        f.write_text("[]")
        profile.save({"birth_date": "2000-08-03"}, today=date(2026, 10, 12))
        copy = Path(self.dir.name) / "profile.json.damaged"
        self.assertEqual(copy.read_text(), "[]")
        self.assertEqual(stat.S_IMODE(copy.stat().st_mode), 0o600)
        self.assertEqual(profile.load()["birth_date"], "2000-08-03")

    def test_refuses_unknown_fields(self):
        with self.assertRaises(profile.ProfileRefused):
            profile.save({"salary": 1})


if __name__ == "__main__":
    unittest.main()
