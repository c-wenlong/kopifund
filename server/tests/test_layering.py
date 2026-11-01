"""Which package may import which, and nothing edits sys.path (G19, R4 and R5).

analytics imports finance; finance never imports analytics, so the record,
the parsers and the app's answers build and run without the stats. And every
module is imported normally: the parsers are the finance.parsers package, so
no file in the repository puts a folder on sys.path to reach them.
"""
import isolate  # noqa: F401  first: no test writes the real record or cache
import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Folders that hold no Python of this repository's own.
SKIP = {".git", ".claude", "node_modules", "dist", "__pycache__", ".venv", "venv"}


def python_files(*under):
    for top in under or (ROOT,):
        for path in sorted(Path(top).rglob("*.py")):
            if not SKIP.intersection(path.relative_to(ROOT).parts):
                yield path


def imported_modules(tree):
    """Every module an `import` or `from ... import` names, at any depth (inside
    a function too), plus importlib.import_module("...") and __import__("...")
    with a literal name. A relative import has no package of its own to name."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            yield node.module
        elif (isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant)
              and isinstance(node.args[0].value, str)
              and (getattr(node.func, "attr", None) == "import_module"
                   or getattr(node.func, "id", None) == "__import__")):
            yield node.args[0].value


def path_edits(tree):
    """(line, what) for every change to sys.path: a call on it (insert, append,
    extend, ...), an assignment to it or one of its items, `+=` on it, and
    site.addsitedir. Code handed to a child interpreter as text counts too."""
    def is_sys_path(node):
        return (isinstance(node, ast.Attribute) and node.attr == "path"
                and isinstance(node.value, ast.Name) and node.value.id == "sys")
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if is_sys_path(node.func.value):
                yield node.lineno, f"sys.path.{node.func.attr}()"
            elif node.func.attr == "addsitedir":
                yield node.lineno, "site.addsitedir()"
        elif isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                if is_sys_path(t) or (isinstance(t, ast.Subscript) and is_sys_path(t.value)):
                    yield node.lineno, "an assignment to sys.path"
        elif isinstance(node, ast.ImportFrom) and node.module == "sys" and \
                any(a.name == "path" for a in node.names):
            yield node.lineno, "from sys import path"
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            for verb in ("insert", "append", "extend"):
                if f"sys.path.{verb}(" in node.value:
                    yield node.lineno, f"sys.path.{verb}() in code run as text"


class FinanceNeverImportsAnalytics(unittest.TestCase):
    def test_no_module_under_finance_imports_analytics(self):
        found = []
        for path in python_files(ROOT / "finance"):
            rel = path.relative_to(ROOT).as_posix()
            for name in imported_modules(ast.parse(path.read_text(encoding="utf-8"))):
                if name == "analytics" or name.startswith("analytics."):
                    found.append(f"{rel} imports {name}")
        self.assertEqual(found, [], "finance must not import analytics (analytics imports finance)")

    def test_the_check_sees_an_import_inside_a_function(self):
        tree = ast.parse("def f():\n    from analytics import run\n    import analytics.stats\n")
        self.assertEqual(sorted(imported_modules(tree)), ["analytics", "analytics.stats"])


class NoSysPathEdits(unittest.TestCase):
    def test_no_python_file_in_the_repository_edits_sys_path(self):
        found = [f"{path.relative_to(ROOT).as_posix()}:{line}: {what}"
                 for path in python_files() if path != Path(__file__).resolve()
                 for line, what in path_edits(ast.parse(path.read_text(encoding="utf-8")))]
        self.assertEqual(found, [], "import the package instead of putting a folder on sys.path")

    def test_the_check_sees_each_kind_of_edit(self):
        code = ("import sys, site\nsys.path.insert(0, 'x')\nsys.path.append('x')\n"
                "sys.path += ['x']\nsys.path[0:0] = ['x']\nsite.addsitedir('x')\n"
                "CODE = \"import sys; sys.path.insert(0, 'x')\"\n")
        self.assertEqual(sorted(line for line, _ in path_edits(ast.parse(code))), [2, 3, 4, 5, 6, 7])

    def test_the_parsers_are_a_package(self):
        self.assertTrue((ROOT / "finance" / "parsers" / "__init__.py").is_file())


if __name__ == "__main__":
    unittest.main()
