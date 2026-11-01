"""The same statement file in two places, or moved to another account (G13).

The record keeps one row per file content (statement_file.sha256 is unique). A
full load with two PDFs swapped between account folders used to stop half way
with a raw database error. The checks now name both paths before anything is
written, and the load refuses like it does for any other blocking problem.
Every file here is an invented few bytes; only its hash matters.
"""
import isolate  # noqa: F401  first: no test writes the real record or cache
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from finance import db, monthly, sync
from finance.model import Config, Folder, Problem, all_pdfs

A, B = "statements/account-a", "statements/account-b"
STATEMENT_ONE, STATEMENT_TWO = b"%PDF-1.4 invented statement one", b"%PDF-1.4 invented statement two"


def codes(problems):
    return [p.code for p in problems]


class Tree(unittest.TestCase):
    """A data folder with two account folders and a record that has seen
    A/2026/one.pdf and B/2026/two.pdf."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.cfg = Config(root=self.root, version="v", token_env="", token_command="",
                          folders=[Folder(path=A, parser="dbs"), Folder(path=B, parser="dbs")])
        self.put(f"{A}/2026/one.pdf", STATEMENT_ONE)
        self.put(f"{B}/2026/two.pdf", STATEMENT_TWO)
        self.conn = db.connect(":memory:")
        self.addCleanup(self.conn.close)
        self.read()

    def put(self, rel, data):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def read(self):
        """The record reads the folder, as a load does."""
        db.load_statements_meta(self.conn, self.root, all_pdfs(self.cfg))

    def recorded(self):
        return dict(self.conn.execute("SELECT path, sha256 FROM statement_file"))

    def problems(self):
        return sync.check_statement_files(self.cfg, self.recorded())

    def swap_by_moving(self):
        """Each file moves into the other account's folder, keeping its name."""
        (self.root / f"{A}/2026/one.pdf").rename(self.root / f"{B}/2026/one.pdf")
        (self.root / f"{B}/2026/two.pdf").rename(self.root / f"{A}/2026/two.pdf")

    def swap_contents(self):
        """The two paths stay, the files behind them trade places."""
        self.put(f"{A}/2026/one.pdf", STATEMENT_TWO)
        self.put(f"{B}/2026/two.pdf", STATEMENT_ONE)


class StatementMoved(Tree):
    def test_a_folder_nobody_touched_is_clean(self):
        self.assertEqual(self.problems(), [])

    def test_two_files_swapped_between_account_folders_are_named_with_where_they_were(self):
        self.swap_by_moving()
        problems = self.problems()
        self.assertEqual(codes(problems), ["statement-moved", "statement-moved"])
        by_subject = {p.subject: p.fix for p in problems}
        self.assertIn(f"{B}/2026/one.pdf", by_subject)
        self.assertIn(f"{A}/2026/one.pdf", by_subject[f"{B}/2026/one.pdf"])
        self.assertIn(f"{B}/2026/two.pdf", by_subject[f"{A}/2026/two.pdf"])
        self.assertTrue(all(p.fix for p in problems))

    def test_the_same_swap_by_trading_contents_is_caught_too(self):
        self.swap_contents()
        self.assertEqual(codes(self.problems()), ["statement-moved", "statement-moved"])

    def test_one_file_moved_to_another_account_is_one_problem(self):
        (self.root / f"{A}/2026/one.pdf").rename(self.root / f"{B}/2026/one.pdf")
        self.assertEqual(codes(self.problems()), ["statement-moved"])

    def test_a_rename_inside_one_account_folder_is_not_a_problem(self):
        (self.root / f"{A}/2026/one.pdf").rename(self.root / f"{A}/2026/one-again.pdf")
        self.assertEqual(self.problems(), [])

    def test_no_fix_ever_suggests_deleting_a_statement(self):
        self.swap_by_moving()
        for p in self.problems():
            self.assertNotRegex(p.fix.replace("never delete a statement", ""), r"(?i)\b(delete|remove)\b")

    def test_a_record_that_does_not_exist_yet_has_nothing_to_disagree_with(self):
        self.swap_by_moving()
        with mock.patch.dict("os.environ", {"KOPIFUND_DB": str(self.root / "none.db")}):
            self.assertEqual(sync.check_statement_files(self.cfg), [])


class SameFileTwoPlaces(Tree):
    def test_the_same_bytes_under_two_names_name_both_paths(self):
        self.put(f"{B}/2026/copy.pdf", STATEMENT_ONE)
        [p] = self.problems()
        self.assertEqual(p.code, "same-file-two-places")
        self.assertIn(f"{A}/2026/one.pdf", p.subject)
        self.assertIn(f"{B}/2026/copy.pdf", p.subject)
        self.assertIn("never delete a statement", p.fix)

    def test_three_copies_are_one_problem_naming_all_three(self):
        self.put(f"{B}/2026/copy.pdf", STATEMENT_ONE)
        self.put(f"{B}/2027/copy.pdf", STATEMENT_ONE)
        [p] = self.problems()
        self.assertEqual(p.subject.count("one.pdf") + p.subject.count("copy.pdf"), 3)

    def test_a_dot_file_copy_is_not_a_statement(self):
        self.put(f"{B}/2026/.copy.pdf", STATEMENT_ONE)
        self.assertEqual(self.problems(), [])


class LoadRefusesCleanly(Tree):
    def refused(self):
        with mock.patch.object(sync, "parse", return_value=sync.Parsed()), \
                mock.patch.object(sync, "categorise"), \
                mock.patch.object(sync, "default_rules"), \
                mock.patch.object(sync, "check_environment", return_value=[]), \
                mock.patch.object(sync, "check_statements", return_value=[]), \
                mock.patch.object(monthly, "analyse") as analyse:
            before = self.recorded()
            out = monthly.load(self.cfg, self.conn)
            self.assertFalse(analyse.called)
            self.assertEqual(self.recorded(), before)
            return out

    def test_a_swap_is_reported_by_the_check_and_the_load_writes_nothing(self):
        self.swap_by_moving()
        out = self.refused()
        self.assertFalse(out["loaded"])
        self.assertEqual([p["code"] for p in out["report"]["problems"]], ["statement-moved"] * 2)
        self.assertEqual(len(monthly.blocking(out["report"])), 2)

    def test_two_copies_are_reported_the_same_way(self):
        self.put(f"{B}/2026/copy.pdf", STATEMENT_ONE)
        out = self.refused()
        self.assertFalse(out["loaded"])
        self.assertEqual([p["code"] for p in out["report"]["problems"]], ["same-file-two-places"])


class TheRecordNeverRaises(Tree):
    """Whatever got past the check, the record's unique key is not what stops
    it: a file that moved takes over its old row."""

    def test_a_swap_that_reaches_the_record_is_written_without_an_error(self):
        first_seen = dict(self.conn.execute("SELECT path, first_seen FROM statement_file"))
        self.swap_by_moving()
        self.read()
        self.assertEqual(set(self.recorded()), {f"{B}/2026/one.pdf", f"{A}/2026/two.pdf"})
        self.assertEqual(len(set(self.recorded().values())), 2)
        self.assertEqual(
            dict(self.conn.execute("SELECT path, first_seen FROM statement_file"))[f"{B}/2026/one.pdf"],
            first_seen[f"{A}/2026/one.pdf"])

    def test_two_files_that_trade_names_in_one_folder_keep_their_own_dates(self):
        self.put(f"{A}/2026/two-a.pdf", b"%PDF-1.4 second invented statement")
        self.read()
        self.conn.execute("UPDATE statement_file SET first_seen='2025-01-01' WHERE path=?", (f"{A}/2026/one.pdf",))
        self.conn.execute("UPDATE statement_file SET first_seen='2025-02-02' WHERE path=?", (f"{A}/2026/two-a.pdf",))
        before = dict(self.conn.execute("SELECT sha256, first_seen FROM statement_file"))
        one, other = self.root / f"{A}/2026/one.pdf", self.root / f"{A}/2026/two-a.pdf"
        first, second = one.read_bytes(), other.read_bytes()
        one.write_bytes(second)
        other.write_bytes(first)
        self.assertEqual(self.problems(), [])
        self.read()
        self.assertEqual(dict(self.conn.execute("SELECT sha256, first_seen FROM statement_file")), before)

    def test_trading_contents_is_written_without_an_error(self):
        self.swap_contents()
        self.read()
        self.assertEqual(len(self.recorded()), 2)



class OneProblemPerCopy(unittest.TestCase):
    """A byte copy also collides on its period; the owner sees one card for it,
    matched on exact root-relative paths, never on names or display text (G13)."""
    A, A2 = "statements/a/2026/07.pdf", "statements/a/2026/07 (1).pdf"

    def same(self, *paths):
        return Problem("same-file-two-places", " and ".join(paths), "move the extra copy", paths=paths)

    def dup(self, *paths):
        return Problem("duplicate-period", "123 · SGD · 2026-07: " + ", ".join(paths), "move the extra PDF", paths=paths)

    def test_a_period_named_only_by_copies_is_folded_in(self):
        same = self.same(self.A, self.A2)
        self.assertEqual(sync.one_problem_per_copy([same, self.dup(self.A2, self.A)]), [same])

    def test_a_period_naming_any_other_file_stays(self):
        same, dup = self.same(self.A, self.A2), self.dup(self.A, "statements/a/2026/07 again.pdf")
        self.assertEqual(sync.one_problem_per_copy([same, dup]), [same, dup])

    def test_the_same_names_in_another_account_stay(self):
        # account a holds byte copies; account b has a real collision with the same names
        same = self.same(self.A, self.A2)
        other = self.dup("statements/b/2026/07.pdf", "statements/b/2026/07 (1).pdf")
        self.assertEqual(sync.one_problem_per_copy([same, other]), [same, other])

    def test_a_name_holding_and_is_still_one_path(self):
        july, both = "statements/a/2026/July.pdf", "statements/a/2026/July and August.pdf"
        same = self.same(july, both)
        self.assertEqual(sync.one_problem_per_copy([same, self.dup(july, both)]), [same])

    def test_without_a_copy_nothing_changes(self):
        dup = self.dup(self.A, self.A2)
        self.assertEqual(sync.one_problem_per_copy([dup]), [dup])

    def test_a_period_without_paths_is_never_dropped(self):
        same = self.same(self.A, self.A2)
        bare = Problem("duplicate-period", "123 · SGD · 2026-07", "two statements produced the same period")
        self.assertEqual(sync.one_problem_per_copy([same, bare]), [same, bare])

    def test_the_file_check_and_the_block_check_carry_their_paths(self):
        parsed = sync.Parsed(blocks=[{"period": "p", "source": self.A}, {"period": "p", "source": self.A2}],
                             rows=[], csv_dir=Path("."))
        dup = [p for p in sync.check_blocks(parsed) if p.code == "duplicate-period"][0]
        self.assertEqual(set(dup.paths), {self.A, self.A2})


if __name__ == "__main__":
    unittest.main()
