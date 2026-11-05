"""No test writes into a real data folder (server/tests/isolate.py redirects it)."""
import isolate  # first: no test writes the real record or cache
import ast
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from finance import db, paths
from finance.paths import cache_dir, data_root


class IsolationTest(unittest.TestCase):
    def test_the_record_is_not_the_data_folders(self):
        self.assertNotEqual(db.db_path(), data_root() / "kopifund.db")
        self.assertFalse(db.db_path().is_relative_to(data_root()))

    def test_the_cache_is_not_the_data_folders(self):
        self.assertNotEqual(cache_dir(), data_root() / "cache")

    def test_the_data_folder_is_the_throwaway_one(self):
        scratch = Path(os.environ["KOPIFUND_TEST_SCRATCH"])
        self.assertTrue(data_root().is_relative_to(scratch))
        self.assertNotEqual(data_root(), (Path.home() / ".kopifund"))
        for where in (paths.config_path(), paths.personal_rules_dir(), paths.rules_history_dir(),
                      paths.statements_dir(), paths.places_path()):
            self.assertTrue(where.is_relative_to(scratch), where)

    def test_real_data_is_seen_only_inside_the_block_and_only_when_asked(self):
        throwaway = data_root()
        with mock.patch.dict(os.environ):
            os.environ.pop("KOPIFUND_TEST_REAL_DATA", None)
            with self.assertRaises(unittest.SkipTest):
                with isolate.real_data():
                    self.fail("ran without being asked")
            with tempfile.TemporaryDirectory() as real:
                os.environ["KOPIFUND_TEST_REAL_DATA"] = real
                with isolate.real_data() as folder:
                    self.assertEqual(data_root(), Path(real))
                    self.assertEqual(folder, Path(real))
                self.assertEqual(data_root(), throwaway)

    def test_the_module_hooks_hand_the_folder_back(self):
        throwaway = data_root()
        with tempfile.TemporaryDirectory() as real, \
                mock.patch.dict(os.environ, {"KOPIFUND_TEST_REAL_DATA": real}):
            up, down = isolate.real_data_for_module()
            up()
            self.assertEqual(data_root(), Path(real))
            down()
        self.assertEqual(data_root(), throwaway)


def first_statement_after_docstring(tree):
    body = tree.body
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
            and isinstance(body[0].value.value, str):
        body = body[1:]
    return body[0] if body else None


class EveryModuleImportsIsolateFirstTest(unittest.TestCase):
    """server/tests/__init__.py's `from . import isolate` does not run under
    `discover tests`, because tests/ is then the top-level folder, not a
    package. So each module has to import isolate itself, before anything
    that could reach the record. test_link.py once did not: run on its own it
    wrote to the real record and deleted its pending phone rows."""

    def test_every_test_module_starts_with_import_isolate(self):
        missing = []
        for path in sorted(Path(__file__).resolve().parent.glob("test_*.py")):
            first = first_statement_after_docstring(ast.parse(path.read_text(encoding="utf-8")))
            if not (isinstance(first, ast.Import) and [a.name for a in first.names] == ["isolate"]):
                missing.append(path.name)
        self.assertEqual(missing, [], "these test modules must start with `import isolate`")


if __name__ == "__main__":
    unittest.main()
