#!/usr/bin/env python3
"""Rules loading, tier precedence, linting and `why`.

    python3 -m unittest discover -s tests

Every test builds its own tiny rules directory in a temp folder, so nothing here
depends on the real rules/ contents; test_real_rules_load is the one exception
and only checks that the shipped files load and split by tier as documented.
"""
import isolate  # noqa: F401  first: no test writes the real record or cache
import io
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

from finance.parsers import categorise as c

HEADER = "match_on,pattern,payee,kind,country,category,notes,account_no,recurring"


def rule(match_on, pattern, payee="", kind="", country="", category="Transport",
         notes="", account_no="", recurring=""):
    return ",".join([match_on, pattern, payee, kind, country, category, notes,
                     account_no, recurring])


def row(**kw):
    r = dict(txn_key="", flow_class="", bank_txn_type="", merchant="",
             counterparty="", memo="", reference="")
    r.update(kw)
    return r


class RulesDir:
    """A temp rules directory; `files` maps filename to a list of CSV lines."""

    def __init__(self, files, header=HEADER):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)
        for name, lines in files.items():
            (self.path / name).write_text("\n".join([header] + lines) + "\n",
                                          encoding="utf-8")

    def __enter__(self):
        return self.path

    def __exit__(self, *exc):
        self.tmp.cleanup()


def load(files, **kw):
    with RulesDir(files) as d:
        return c.load_rules(d, **kw)


class TestTierPrecedence(unittest.TestCase):

    def test_type_beats_flow(self):
        rules = load({"10-types.csv": [
            rule("flow", "Internal - YouTrip", category="Wallet top-up"),
            rule("type", "YouTrip Exchange In", category="Between own accounts"),
        ]})
        r = row(bank_txn_type="YouTrip Exchange In", flow_class="Internal - YouTrip")
        c.categorise_rows([r], rules)
        self.assertEqual(r["category"], "Between own accounts")
        self.assertEqual(r["rule"], "type:YouTrip Exchange In")

    def test_flow_wins_when_type_has_no_rule(self):
        rules = load({"10-types.csv": [
            rule("flow", "Internal - YouTrip", category="Wallet top-up"),
            rule("type", "YouTrip Exchange In", category="Between own accounts"),
        ]})
        r = row(bank_txn_type="FAST Payment / Receipt", flow_class="Internal - YouTrip")
        c.categorise_rows([r], rules)
        self.assertEqual(r["rule"], "flow:Internal - YouTrip")

    def test_key_beats_everything(self):
        rules = load({"00-keys.csv": [rule("key", "abc123", category="Rent")],
                      "30-merchants.csv": [rule("merchant", "FRESHCO*", category="Groceries")]})
        r = row(txn_key="abc123", merchant="FRESHCO SUPERMARKET")
        c.categorise_rows([r], rules)
        self.assertEqual(r["category"], "Rent")

    def test_earlier_tier_wins_over_later_tier(self):
        rules = load({"30-merchants.csv": [rule("merchant", "FRESHCO*", category="Groceries")],
                      "20-people.csv": [rule("memo", "RENT*", category="Rent")]})
        r = row(merchant="FRESHCO EXPRESS", memo="RENT SEPTEMBER")
        c.categorise_rows([r], rules)
        self.assertEqual(r["category"], "Groceries")

    def test_empty_field_skips_its_tier(self):
        rules = load({"30-merchants.csv": [rule("merchant", "*", category="Groceries")],
                      "20-people.csv": [rule("memo", "RENT*", category="Rent")]})
        r = row(merchant="", memo="RENT SEPTEMBER")
        c.categorise_rows([r], rules)
        self.assertEqual(r["category"], "Rent")

    def test_no_match_leaves_the_row_blank(self):
        rules = load({"30-merchants.csv": [rule("merchant", "FRESHCO*", category="Groceries")]})
        r = row(merchant="COOP")
        c.categorise_rows([r], rules)
        self.assertEqual((r["payee"], r["category"], r["rule"]), ("", "", ""))


class TestFirstMatchInTier(unittest.TestCase):

    def test_first_matching_rule_in_file_order_wins(self):
        rules = load({"30-merchants.csv": [
            rule("merchant", "FRESHCO*", payee="FreshCo", kind="Merchant", category="Groceries"),
            rule("merchant", "FRESHCO EXPRESS*", payee="FreshCo Express", kind="Merchant",
                 category="Eating out"),
        ]})
        r = row(merchant="FRESHCO EXPRESS TAMPINES")
        c.categorise_rows([r], rules)
        self.assertEqual(r["payee"], "FreshCo")

    def test_order_across_files_is_by_filename(self):
        # both files hold merchant rules, so the sorted filename order decides
        with RulesDir({"30-merchants.csv": [rule("merchant", "FRESHCO*", category="Groceries")],
                       "40-extra.csv": [rule("merchant", "FRESHCO*", category="Eating out")]}) as d:
            rules = c.load_rules(d)
        self.assertEqual([r["file"] for r in rules], ["30-merchants.csv", "40-extra.csv"])
        r = row(merchant="FRESHCO EXPRESS")
        c.categorise_rows([r], rules)
        self.assertEqual(r["category"], "Groceries")


class TestNormalisation(unittest.TestCase):

    def test_case_insensitive(self):
        rules = load({"30-merchants.csv": [rule("merchant", "freshco *", category="Groceries")]})
        r = row(merchant="FreshCo Express")
        c.categorise_rows([r], rules)
        self.assertEqual(r["category"], "Groceries")

    def test_whitespace_is_collapsed_on_both_sides(self):
        rules = load({"30-merchants.csv": [rule("merchant", "BUS/MRT  432*",
                                                category="Transport")]})
        r = row(merchant="  bus/mrt\t432100100   SINGAPORE ")
        c.categorise_rows([r], rules)
        self.assertEqual(r["category"], "Transport")

    def test_exact_pattern_does_not_match_a_prefix(self):
        rules = load({"30-merchants.csv": [rule("merchant", "TFL", category="Transport")]})
        r = row(merchant="TFL APP LONDON")
        c.categorise_rows([r], rules)
        self.assertEqual(r["rule"], "")


class TestDirectoryLoading(unittest.TestCase):

    def test_directory_concatenates_in_sorted_filename_order(self):
        with RulesDir({"30-merchants.csv": [rule("merchant", "FRESHCO*", category="Groceries")],
                       "00-keys.csv": [rule("key", "abc", category="Rent")],
                       "10-types.csv": [rule("type", "Interest Earned",
                                             category="Interest")]}) as d:
            self.assertEqual([f.name for f in c.rule_files(d)],
                             ["00-keys.csv", "10-types.csv", "30-merchants.csv"])
            self.assertEqual([r["file"] for r in c.load_rules(d)],
                             ["00-keys.csv", "10-types.csv", "30-merchants.csv"])

    def test_personal_types_and_merchants_load_before_the_shipped_ones(self):
        """G10: the data folder's types.csv and merchants.csv hold the rules
        only one user meets, and each wins a tie over the shipped tier."""
        with RulesDir({"10-types.csv": [rule("type", "Fees", category="Fees and charges")],
                       "30-merchants.csv": [rule("merchant", "KEDAI*", category="Groceries")]}) as shared, \
                RulesDir({"types.csv": [rule("type", "Fees", category="Financial aid")],
                          "merchants.csv": [rule("merchant", "KEDAI*", category="Eating out")]}) as mine:
            self.assertEqual([f.name for f in c.rule_files([shared, mine])],
                             ["types.csv", "10-types.csv", "merchants.csv", "30-merchants.csv"])
            rules = c.load_rules([shared, mine])
        r = row(merchant="KEDAI LAMA", bank_txn_type="Fees")
        c.categorise_rows([r], rules)
        self.assertEqual(r["category"], "Financial aid")
        r = row(merchant="KEDAI LAMA")
        c.categorise_rows([r], rules)
        self.assertEqual(r["category"], "Eating out")

    def test_a_merchant_rule_in_the_personal_types_file_is_refused(self):
        with RulesDir({"types.csv": [rule("merchant", "KEDAI*", category="Groceries")]}) as d:
            with self.assertRaises(SystemExit):
                c.load_rules(d)

    def test_single_file_still_works(self):
        with RulesDir({"00-keys.csv": [rule("key", "abc", category="Rent")]}) as d:
            rules = c.load_rules(d / "00-keys.csv")
        self.assertEqual(len(rules), 1)

    def test_optional_columns_may_be_absent(self):
        with RulesDir({"00-keys.csv": [",".join(["key", "abc", "", "", "", "Rent", ""])]},
                      header=",".join(c.REQUIRED_COLUMNS)) as d:
            rules = c.load_rules(d)
        self.assertEqual(rules[0]["account_no"], "")
        self.assertEqual(rules[0]["recurring"], "")

    def test_recurring_column_is_read(self):
        rules = load({"30-merchants.csv": [
            rule("merchant", "NOTION.COM*", payee="Notion", kind="Merchant",
                 category="Software and tools", recurring="yes"),
            rule("merchant", "FRESHCO*", payee="FreshCo", kind="Merchant", category="Groceries"),
        ]})
        self.assertEqual(c.recurring_payees(rules), {"Notion"})

    def test_missing_path_exits(self):
        with self.assertRaises(SystemExit):
            with redirect_stderr(io.StringIO()):
                c.load_rules("/nonexistent/rules/dir/file.csv")


class TestValidation(unittest.TestCase):

    def _fails(self, files, needle, header=HEADER):
        buf = io.StringIO()
        with RulesDir(files, header=header) as d:
            with self.assertRaises(SystemExit) as cm, redirect_stderr(buf):
                c.load_rules(d)
        self.assertIn(needle, str(cm.exception) + buf.getvalue())

    def test_rule_in_the_wrong_file_is_an_error(self):
        self._fails({"00-keys.csv": [rule("merchant", "FRESHCO*", category="Groceries")]},
                    "does not belong in 00-keys.csv")

    def test_flow_rule_in_the_people_file_is_an_error(self):
        self._fails({"20-people.csv": [rule("flow", "Internal - PayLah",
                                            category="Wallet top-up")]},
                    "does not belong in 20-people.csv")

    def test_tier_allowed_in_its_own_file(self):
        for name, tier, pat in (("00-keys.csv", "key", "abc"),
                                ("10-types.csv", "type", "Interest Earned"),
                                ("10-types.csv", "flow", "Internal - PayLah"),
                                ("20-people.csv", "counterparty", "ALI*"),
                                ("20-people.csv", "memo", "RENT*"),
                                ("20-people.csv", "reference", "*107*"),
                                ("30-merchants.csv", "merchant", "FRESHCO*")):
            with self.subTest(file=name, tier=tier):
                rules = load({name: [rule(tier, pat, category="Transport")]})
                self.assertEqual(rules[0]["match_on"], tier)

    def test_unknown_category_is_rejected(self):
        self._fails({"30-merchants.csv": [rule("merchant", "FRESHCO*", category="Snacks")]},
                    "unknown category 'Snacks'")

    def test_a_rule_naming_a_renamed_category_still_loads(self):
        rules = load({"20-people.csv": [rule("counterparty", "NUS*", category="Tuition")]})
        self.assertEqual(rules[0]["category"], "Tuition")

    def test_categories_override_replaces_the_offline_list(self):
        with RulesDir({"30-merchants.csv": [rule("merchant", "FRESHCO*",
                                                 category="Snacks")]}) as d:
            rules = c.load_rules(d, categories=["Snacks"])
        self.assertEqual(rules[0]["category"], "Snacks")
        with RulesDir({"30-merchants.csv": [rule("merchant", "FRESHCO*",
                                                 category="Transport")]}) as d:
            with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
                c.load_rules(d, categories=["Snacks"])

    def test_check_categories_reports_against_a_live_list(self):
        rules = load({"30-merchants.csv": [rule("merchant", "FRESHCO*", category="Transport")]})
        self.assertEqual(c.check_categories(rules, ["Transport"]), [])
        self.assertEqual(len(c.check_categories(rules, ["Rent"])), 1)

    def test_known_categories_has_31_names(self):
        self.assertEqual(len(c.KNOWN_CATEGORIES), 31)   # 29, Transport split in two, kept too
        self.assertEqual(len(set(c.KNOWN_CATEGORIES)), 31)

    def test_payee_without_kind_is_rejected(self):
        self._fails({"30-merchants.csv": [rule("merchant", "FRESHCO*", payee="FreshCo",
                                               category="Groceries")]},
                    "payee without kind")

    def test_bad_country_is_rejected(self):
        self._fails({"30-merchants.csv": [rule("merchant", "FRESHCO*", payee="FreshCo",
                                               kind="Merchant", country="Japan",
                                               category="Groceries")]},
                    "is not ISO alpha-3")

    def test_bad_recurring_value_is_rejected(self):
        self._fails({"30-merchants.csv": [rule("merchant", "FRESHCO*", category="Groceries",
                                               recurring="weekly")]},
                    "is not yes or blank")

    def test_bad_header_is_rejected(self):
        self._fails({"00-keys.csv": [rule("key", "abc", category="Rent")]},
                    "header must be", header="match_on,pattern,category")

    def test_comment_and_blank_lines_are_skipped(self):
        with RulesDir({"30-merchants.csv": ["# a section header", "",
                                            rule("merchant", "FRESHCO*",
                                                 category="Groceries")]}) as d:
            rules = c.load_rules(d)
        self.assertEqual(len(rules), 1)


class TestLint(unittest.TestCase):

    def test_dead_rule_that_matches_nothing(self):
        rules = load({"30-merchants.csv": [rule("merchant", "FRESHCO*", category="Groceries"),
                                           rule("merchant", "COOP*", category="Groceries")]})
        findings = c.lint([row(merchant="FRESHCO EXPRESS")], rules)
        dead = findings["rules that never win"]
        self.assertEqual(len(dead), 1)
        self.assertIn("merchant:COOP*", dead[0])
        self.assertIn("matches no row", dead[0])

    def test_rule_lost_to_an_earlier_tier(self):
        rules = load({"10-types.csv": [rule("flow", "Internal - YouTrip",
                                            category="Wallet top-up")],
                      "20-people.csv": [rule("counterparty", "YOU TECHNOLOGIES*",
                                             category="Wallet top-up")]})
        r = row(flow_class="Internal - YouTrip",
                counterparty="YOU TECHNOLOGIES GROUP (SG) PL")
        dead = c.lint([r], rules)["rules that never win"]
        self.assertEqual(len(dead), 1)
        self.assertIn("counterparty:YOU TECHNOLOGIES*", dead[0])
        self.assertIn("lost to an earlier tier", dead[0])

    def test_rule_lost_to_an_earlier_rule_in_the_same_tier(self):
        rules = load({"30-merchants.csv": [
            rule("merchant", "FRESHCO*", category="Groceries"),
            rule("merchant", "FRESHCO EXPRESS*", category="Eating out"),
        ]})
        dead = c.lint([row(merchant="FRESHCO EXPRESS")], rules)["rules that never win"]
        self.assertIn("lost to an earlier rule in the same tier", dead[0])

    def test_clean_rules_report_nothing(self):
        rules = load({"30-merchants.csv": [rule("merchant", "FRESHCO*", category="Groceries")]})
        self.assertEqual(c.lint([row(merchant="FRESHCO EXPRESS")], rules), {})

    def test_duplicate_tier_and_pattern(self):
        rules = load({"30-merchants.csv": [rule("merchant", "FRESHCO*", category="Groceries"),
                                           rule("merchant", "freshco*", category="Groceries")]})
        findings = c.lint([row(merchant="FRESHCO EXPRESS")], rules)
        self.assertEqual(len(findings["duplicate (tier, pattern)"]), 1)

    def test_shadowed_within_a_tier(self):
        rules = load({"30-merchants.csv": [rule("merchant", "FRESHCO*", category="Groceries"),
                                           rule("merchant", "FRESHCO EXPRESS*",
                                                category="Eating out")]})
        findings = c.lint([], rules)
        self.assertEqual(len(findings["shadowed within a tier"]), 1)
        self.assertIn("is covered by the earlier", findings["shadowed within a tier"][0])

    def test_payee_with_two_kinds(self):
        rules = load({"30-merchants.csv": [
            rule("merchant", "FRESHCO*", payee="FreshCo", kind="Merchant", category="Groceries"),
            rule("merchant", "COOP*", payee="FreshCo", kind="Institution",
                 category="Groceries"),
        ]})
        findings = c.lint([], rules)
        self.assertEqual(len(findings["payee with more than one kind"]), 1)

    def test_over_broad_pattern(self):
        rules = load({"30-merchants.csv": [rule("merchant", "GRAB*",
                                                category="Transport")]})
        rows = [row(merchant=f"GRAB* A-{i}") for i in range(c.BROAD + 1)]
        findings = c.lint(rows, rules)
        key = f"pattern matching more than {c.BROAD} distinct raw strings"
        self.assertEqual(len(findings[key]), 1)
        rows = [row(merchant=f"GRAB* A-{i}") for i in range(c.BROAD)]
        self.assertNotIn(key, c.lint(rows, rules))

    def test_ids_marker_waves_off_the_over_broad_check(self):
        """A ride id or a fare id in the merchant string makes the distinct
        count a count of how often the payee was used, not of how broad the
        pattern is."""
        key = f"pattern matching more than {c.BROAD} distinct raw strings"
        rows = [row(merchant=f"BUS/MRT {i} SINGAPORE") for i in range(c.BROAD + 1)]
        marked = load({"30-merchants.csv": [
            rule("merchant", "BUS/MRT*", payee="Bus/MRT", kind="Merchant",
                 category="Transport", notes="[ids] SimplyGo fare postings")]})
        self.assertNotIn(key, c.lint(rows, marked))
        plain = load({"30-merchants.csv": [
            rule("merchant", "BUS/MRT*", payee="Bus/MRT", kind="Merchant",
                 category="Transport", notes="SimplyGo fare postings")]})
        self.assertEqual(1, len(c.lint(rows, plain)[key]))

    def test_payee_with_two_countries(self):
        rules = load({"30-merchants.csv": [
            rule("merchant", "FRESHCO*", payee="FreshCo", kind="Merchant", country="JPN",
                 category="Groceries"),
            rule("merchant", "COOP*", payee="FreshCo", kind="Merchant", country="NOR",
                 category="Groceries"),
        ]})
        self.assertEqual(1, len(c.lint([], rules)["payee with more than one country"]))

    def test_chain_marker_lets_a_payee_name_one_country_per_branch(self):
        rules = load({"30-merchants.csv": [
            rule("merchant", "MCDONALD'S*", payee="McDonald's", kind="Merchant",
                 country="SGP", category="Eating out", notes="[chain]"),
            rule("merchant", "MCD*", payee="McDonald's", kind="Merchant",
                 country="JPN", category="Eating out", notes="[chain] MCD TOKYO"),
        ]})
        self.assertNotIn("payee with more than one country", c.lint([], rules))

    def test_a_half_marked_payee_is_still_reported(self):
        """The marker on one rule must not cover a typo in another, so every
        rule for the payee has to carry it."""
        rules = load({"30-merchants.csv": [
            rule("merchant", "MCDONALD'S*", payee="McDonald's", kind="Merchant",
                 country="SGP", category="Eating out", notes="[chain]"),
            rule("merchant", "MCD*", payee="McDonald's", kind="Merchant",
                 country="JPN", category="Eating out"),
        ]})
        self.assertEqual(1, len(c.lint([], rules)["payee with more than one country"]))

    def test_the_chain_marker_does_not_excuse_two_kinds(self):
        """A branch has its own country; it does not have its own kind."""
        rules = load({"30-merchants.csv": [
            rule("merchant", "FRESHCO*", payee="FreshCo", kind="Merchant", country="JPN",
                 category="Groceries", notes="[chain]"),
            rule("merchant", "COOP*", payee="FreshCo", kind="Institution", country="NOR",
                 category="Groceries", notes="[chain]"),
        ]})
        findings = c.lint([], rules)
        self.assertEqual(1, len(findings["payee with more than one kind"]))
        self.assertNotIn("payee with more than one country", findings)

    def test_lint_lines_says_clean_when_empty(self):
        self.assertIn("clean", "\n".join(c.lint_lines({}, "rules/")))


class TestWhy(unittest.TestCase):

    def _why(self, rows, rules, key):
        buf = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(io.StringIO()):
            found = c.print_why(rows, rules, key)
        return found, buf.getvalue()

    def _row(self, **kw):
        r = row(**kw)
        r.update(date="2025-08-16", account_no="130-24680-5", currency="SGD",
                 amount="-999.0")
        return r

    def test_lists_every_matching_rule_and_the_winner(self):
        rules = load({"10-types.csv": [rule("flow", "Internal - YouTrip",
                                            payee="YouTrip", kind="Institution",
                                            category="Wallet top-up")],
                      "20-people.csv": [rule("counterparty", "YOU TECHNOLOGIES*",
                                             payee="YouTrip", kind="Institution",
                                             category="Wallet top-up")]})
        r = self._row(txn_key="k1", flow_class="Internal - YouTrip",
                      bank_txn_type="FAST Payment / Receipt",
                      counterparty="YOU TECHNOLOGIES GROUP (SG) PL")
        found, out = self._why([r], rules, "k1")
        self.assertTrue(found)
        self.assertIn("flow:Internal - YouTrip", out)
        self.assertIn("counterparty:YOU TECHNOLOGIES*", out)   # the loser is shown too
        self.assertIn("<- wins", out)
        self.assertIn("winner: 10-types.csv line 2  flow:Internal - YouTrip", out)
        self.assertIn("category: Wallet top-up", out)

    def test_says_so_when_nothing_matches(self):
        rules = load({"30-merchants.csv": [rule("merchant", "FRESHCO*", category="Groceries")]})
        found, out = self._why([self._row(txn_key="k1", merchant="COOP")], rules, "k1")
        self.assertTrue(found)
        self.assertIn("no rule matches this row", out)

    def test_unknown_key_returns_false(self):
        rules = load({"30-merchants.csv": [rule("merchant", "FRESHCO*", category="Groceries")]})
        found, _ = self._why([self._row(txn_key="k1")], rules, "nope")
        self.assertFalse(found)


class TestRealRules(unittest.TestCase):

    def test_real_rules_load_and_each_file_holds_its_own_tiers(self):
        d = Path(__file__).resolve().parents[2] / "data" / "rules"
        if not d.is_dir():
            self.skipTest("data/rules/ not present")
        rules = c.load_rules(d)
        self.assertTrue(rules)
        for r in rules:
            self.assertIn(r["match_on"], c.FILE_TIERS[r["file"]])


if __name__ == "__main__":
    unittest.main()
