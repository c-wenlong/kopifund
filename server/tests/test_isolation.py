"""No test writes into a real data folder (server/tests/__init__.py redirects both)."""
import isolate  # noqa: F401  first: no test writes the real record or cache
import ast
import unittest
from pathlib import Path

from finance import db
from finance.paths import cache_dir, data_root


class IsolationTest(unittest.TestCase):
    def test_the_record_is_not_the_data_folders(self):
        self.assertNotEqual(db.db_path(), data_root() / "kopifund.db")
        self.assertFalse(db.db_path().is_relative_to(data_root()))

    def test_the_cache_is_not_the_data_folders(self):
        self.assertNotEqual(cache_dir(), data_root() / "cache")


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
