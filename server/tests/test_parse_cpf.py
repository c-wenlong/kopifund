"""server/finance/parsers/parse_cpf.py against five invented CPF yearly statements.

The fixtures under server/tests/fixtures/cpf/ are made up, but they are laid out
exactly the way pdftotext -layout renders the real statements, down to the
column positions of the transactions table, so the positional column reading is
tested on the geometry it will meet. The real PDFs live in data/, which is
gitignored, so nothing here reads them.

  summary-2016.txt   the 2016-to-2019 personalised summary: a combined
                     "Employment" figure with no employee/employer split, two
                     labels to a printed line, six outflow tiles printed three
                     across, two REF legend lines, and a page header cutting
                     the transactions table in half.
  summary-quiet.txt  the same layout in a year with no employment at all: a
                     Government support top-up, a voluntary top-up, interest,
                     no outflow and no REF legend.
  one-off-2020.txt   the 2020 layout: "You / Employer / Government / Base
                     Interest / Extra Interest / Government support" on the
                     front, "You used" for the outflow, and two rows that print
                     an amount in one account column only.
  yearly-2024.txt    the 2021-to-2025 Yearly Statement of Account: an
                     employee/employer split, a DPS premium, a contribution and
                     the adjustment that reverses it, a housing withdrawal, and
                     the transactions numbered section 4 because a cash grants
                     section pushed it down.
  yearly-torn.txt    the same layout with the transactions table torn across a
                     page break, a year funded entirely by the Government, and
                     an outflow section that prints the total and no categories
                     at all, the way 2021 and 2022 do.

Every fixture is a link in one chain: each year opens where the one before it
closed, so the five of them replay six years of an invented member's savings and
each one reconciles opening plus transactions to closing.
"""
import isolate  # noqa: F401  first: no test writes the real record or cache
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from finance.parsers import parse_cpf as pc

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "cpf"


def raw(name):
    return (FIXTURES / f"{name}.txt").read_text()


def load(name, file=None):
    return pc.parse_text(raw(name), file or f"2024/{name}.pdf")


def edit_line(name, contains, old, new):
    """The fixture's text with `old` replaced by `new` on the one line that
    holds every fragment in `contains`."""
    out, hits = [], 0
    for ln in raw(name).splitlines():
        if all(c in ln for c in contains) and old in ln:
            ln = ln.replace(old, new)
            hits += 1
        out.append(ln)
    assert hits == 1, f"{contains} matched {hits} lines in {name}, expected 1"
    return "\n".join(out) + "\n"


class Reconciles(unittest.TestCase):
    """The one check that makes every other number trustworthy: the opening
    balance plus every printed transaction is the closing balance, to the cent,
    in each account."""

    def test_every_fixture_reconciles(self):
        for name in ("summary-2016", "summary-quiet", "one-off-2020",
                     "yearly-2024", "yearly-torn"):
            with self.subTest(name):
                self.assertEqual(pc.reconcile(load(name)),
                                 {"oa": 0.0, "sa": 0.0, "ma": 0.0})

    def test_the_five_years_are_one_chain(self):
        chain = ["summary-2016", "summary-quiet", "one-off-2020",
                 "yearly-2024", "yearly-torn"]
        years = [load(n) for n in chain]
        for before, after in zip(years, years[1:]):
            with self.subTest(after["year"]):
                self.assertEqual(after["opening"], before["closing"])

    def test_a_dropped_transaction_shows_up_as_a_gap(self):
        text = edit_line("yearly-2024", ["26 NOV", "HSE"], "-$102.00", "-$92.00")
        off = pc.reconcile(pc.parse_text(text, "2024/x.pdf"))
        self.assertEqual(off, {"oa": 10.0, "sa": 0.0, "ma": 0.0})


class Summary2016(unittest.TestCase):
    """The personalised summary CPF printed from 2016 to 2019."""

    @classmethod
    def setUpClass(cls):
        cls.s = load("summary-2016", "2016/2016.pdf")

    def test_header(self):
        self.assertEqual(self.s["file"], "2016/2016.pdf")
        self.assertEqual(self.s["account"], "S9012345A")
        self.assertEqual(self.s["year"], 2016)
        self.assertEqual(self.s["as_of"], "2016-12-31")

    def test_balances_come_from_the_two_bal_rows(self):
        self.assertEqual(self.s["opening"],
                         {"oa": 0.0, "sa": 0.0, "ma": 0.0, "total": 0.0})
        self.assertEqual(self.s["closing"],
                         {"oa": 119.5, "sa": 32.2, "ma": 40.5, "total": 192.2})

    def test_only_the_combined_employment_figure_is_printed(self):
        i = self.s["inflow"]
        self.assertIsNone(i["employee"])
        self.assertIsNone(i["employer"])
        self.assertEqual(i["employment"], 192.0)

    def test_interest_is_one_figure_with_no_base_and_extra_split(self):
        i = self.s["inflow"]
        self.assertEqual(i["interest"], 4.2)
        self.assertIsNone(i["base_interest"])
        self.assertIsNone(i["extra_interest"])

    def test_the_rest_of_the_inflow(self):
        i = self.s["inflow"]
        self.assertEqual(i["government"], 0.0)
        self.assertEqual(i["government_support"], 0.0)
        self.assertEqual(i["voluntary"], 0.0)
        self.assertEqual(i["total"], 196.2)

    def test_six_outflow_tiles_printed_three_across(self):
        self.assertEqual(self.s["outflow"],
                         {"housing": 0.0, "healthcare": 1.5, "retirement": 0.0,
                          "education": 0.0, "investment": 0.0, "others": 2.5,
                          "total": 4.0})

    def test_the_prose_under_a_tile_does_not_overwrite_its_amount(self):
        # "• CPF Investment Scheme transactions" is printed below the
        # Investment tile and holds the word again
        self.assertIn("CPF Investment Scheme transactions", raw("summary-2016"))
        self.assertEqual(self.s["outflow"]["investment"], 0.0)

    def test_transactions_without_the_bal_rows(self):
        self.assertEqual(self.s["transactions"], [
            {"date": "2016-03-15", "code": "CON", "for_month": "2015-12",
             "ref": "A", "oa": 100.0, "sa": 26.0, "ma": 34.0},
            {"date": "2016-11-15", "code": "CON", "for_month": "2016-10",
             "ref": "B", "oa": 20.0, "sa": 5.2, "ma": 6.8},
            {"date": "2016-12-01", "code": "DPS", "for_month": None,
             "ref": "", "oa": -2.5, "sa": 0.0, "ma": 0.0},
            {"date": "2016-12-05", "code": "MSL", "for_month": None,
             "ref": "", "oa": 0.0, "sa": 0.0, "ma": -1.5},
            {"date": "2016-12-31", "code": "INT", "for_month": None,
             "ref": "", "oa": 2.0, "sa": 1.0, "ma": 1.2}])

    def test_a_page_header_inside_the_table_is_invisible(self):
        # the fixture reprints the footer, the address block and the table
        # header between the second and third rows
        self.assertIn("Page 1 of 3", raw("summary-2016"))
        self.assertEqual(len(self.s["transactions"]), 5)

    def test_a_for_month_in_the_year_before(self):
        # "15 MAR CON DEC 2015" pays the December before the statement's year,
        # so the month it is for and the year it was credited in disagree
        self.assertEqual(self.s["transactions"][0]["for_month"], "2015-12")
        self.assertEqual(self.s["transactions"][0]["date"], "2016-03-15")

    def test_the_ref_legend_and_its_continuation_line(self):
        self.assertEqual(self.s["refs"], {"A": "ORCHID CATERING PTE LTD",
                                          "B": "MERLION LOGISTICS P/L"})


class SummaryQuiet(unittest.TestCase):
    """The same layout in a year nobody was paid in."""

    @classmethod
    def setUpClass(cls):
        cls.s = load("summary-quiet", "2019/2019.pdf")

    def test_no_employment(self):
        i = self.s["inflow"]
        self.assertEqual(i["employment"], 0.0)
        self.assertIsNone(i["employee"])
        self.assertIsNone(i["employer"])

    def test_government_support_and_a_voluntary_top_up(self):
        i = self.s["inflow"]
        self.assertEqual(i["government_support"], 10.0)
        self.assertEqual(i["government"], 10.0)
        self.assertEqual(i["voluntary"], 5.0)
        self.assertEqual(i["interest"], 5.9)
        self.assertEqual(i["total"], 20.9)

    def test_the_year_that_prints_its_total_in_a_bracket(self):
        # 2019 prints "(as at 31 December 2019)   $213.10" rather than
        # "all your CPF accounts is"
        self.assertEqual(self.s["closing"]["total"], 213.1)

    def test_a_row_with_no_ref_letter(self):
        self.assertEqual([t["ref"] for t in self.s["transactions"]], ["", "", ""])

    def test_no_ref_legend_at_all(self):
        self.assertEqual(self.s["refs"], {})

    def test_nothing_left_the_accounts(self):
        self.assertEqual(self.s["outflow"]["total"], 0.0)


class OneOff2020(unittest.TestCase):
    """The layout CPF used for 2020 only."""

    @classmethod
    def setUpClass(cls):
        cls.s = load("one-off-2020", "2020/2020.pdf")

    def test_the_inflow_is_split_the_way_2020_printed_it(self):
        self.assertEqual(self.s["inflow"],
                         {"employee": 0.0, "employer": 0.0, "employment": 0.0,
                          "government": 210.9, "base_interest": 12.9,
                          "extra_interest": 3.0, "interest": 15.9,
                          "government_support": 195.0, "voluntary": 0.0,
                          "total": 210.9})

    def test_the_government_line_is_the_interest_and_the_support_together(self):
        i = self.s["inflow"]
        self.assertEqual(i["government"], i["interest"] + i["government_support"])

    def test_you_have_in_your_cpf_accounts_is_not_read_as_the_you_line(self):
        # the front page prints "You have $421.00 in your CPF accounts" above
        # the "You    $0.00" contribution line
        self.assertIn("You have $421.00", raw("one-off-2020"))
        self.assertEqual(self.s["inflow"]["employee"], 0.0)

    def test_a_row_that_only_touched_medisave(self):
        con = self.s["transactions"][0]
        self.assertEqual((con["oa"], con["sa"], con["ma"]), (0.0, 0.0, 195.0))

    def test_a_row_that_only_touched_the_ordinary_account(self):
        dps = self.s["transactions"][1]
        self.assertEqual(dps["code"], "DPS")
        self.assertEqual((dps["oa"], dps["sa"], dps["ma"]), (-3.0, 0.0, 0.0))

    def test_the_outflow_tiles_are_read_from_you_used(self):
        self.assertEqual(self.s["outflow"]["others"], 3.0)
        self.assertEqual(self.s["outflow"]["total"], 3.0)


class Yearly2024(unittest.TestCase):
    """The Yearly Statement of Account, 2021 onwards."""

    @classmethod
    def setUpClass(cls):
        cls.s = load("yearly-2024", "2024/2024.pdf")

    def test_header(self):
        self.assertEqual(self.s["account"], "S9012345A")
        self.assertEqual(self.s["year"], 2024)
        self.assertEqual(self.s["as_of"], "2024-12-31")

    def test_the_employee_and_employer_split(self):
        i = self.s["inflow"]
        self.assertEqual(i["employee"], 173.51)
        self.assertEqual(i["employer"], 147.49)
        self.assertEqual(i["employment"], 321.0)

    def test_interest_is_the_base_and_extra_halves_together(self):
        i = self.s["inflow"]
        self.assertEqual((i["base_interest"], i["extra_interest"]), (14.5, 5.0))
        self.assertEqual(i["interest"], 19.5)
        self.assertEqual(i["government"], 19.5)
        self.assertEqual(i["total"], 340.5)

    def test_the_outflow_doughnut(self):
        self.assertEqual(self.s["outflow"],
                         {"housing": 102.0, "healthcare": 0.0, "retirement": 0.0,
                          "education": 0.0, "investment": 0.0, "others": 16.0,
                          "total": 118.0})

    def test_a_dps_premium_is_negative_in_the_transactions(self):
        dps = [t for t in self.s["transactions"] if t["code"] == "DPS"]
        self.assertEqual(dps, [{"date": "2024-07-11", "code": "DPS",
                                "for_month": None, "ref": "", "oa": -16.0,
                                "sa": 0.0, "ma": 0.0}])
        # and positive in the outflow it is reported under
        self.assertEqual(self.s["outflow"]["others"], 16.0)

    def test_a_contribution_and_the_adjustment_that_reverses_it(self):
        pair = [t for t in self.s["transactions"] if t["for_month"] == "2024-08"]
        self.assertEqual([t["code"] for t in pair], ["CON", "ADJ"])
        self.assertEqual(sum(t["oa"] for t in pair), 0.0)
        self.assertEqual(sum(t["ma"] for t in pair), 0.0)

    def test_the_transactions_table_is_found_under_any_section_number(self):
        # a cash grants section pushes it from "3." to "4."
        self.assertIn("4. CPF transactions", raw("yearly-2024"))
        self.assertEqual(len(self.s["transactions"]), 6)

    def test_the_cash_grants_section_is_not_inflow(self):
        # $500.00 was paid to a bank account, not into CPF
        self.assertIn("Assurance Package", raw("yearly-2024"))
        self.assertEqual(self.s["inflow"]["total"], 340.5)

    def test_the_refs(self):
        self.assertEqual(self.s["refs"], {"A": "ORCHID CATERING PTE. LTD.",
                                          "B": "MERLION LOGISTICS PTE. LTD."})


class YearlyTorn(unittest.TestCase):
    """A Government-only year whose table is cut in half by a page break."""

    @classmethod
    def setUpClass(cls):
        cls.s = load("yearly-torn", "2025/2025.pdf")

    def test_both_halves_of_the_table_come_through(self):
        self.assertIn("4. CPF transactions (continued)", raw("yearly-torn"))
        self.assertEqual([t["code"] for t in self.s["transactions"]],
                         ["CON", "INT"])
        self.assertEqual(self.s["closing"],
                         {"oa": 220.7, "sa": 92.4, "ma": 757.4, "total": 1070.5})

    def test_nobody_was_paid_that_year(self):
        i = self.s["inflow"]
        self.assertEqual((i["employee"], i["employer"], i["employment"]),
                         (0.0, 0.0, 0.0))
        self.assertEqual(i["government"], 427.0)
        self.assertEqual(i["government_support"], 385.0)
        self.assertEqual(i["interest"], 42.0)

    def test_an_outflow_section_with_a_total_and_no_categories(self):
        self.assertEqual(self.s["outflow"],
                         {"housing": 0.0, "healthcare": 0.0, "retirement": 0.0,
                          "education": 0.0, "investment": 0.0, "others": 0.0,
                          "total": 0.0})

    def test_a_thousand_separator_in_the_printed_total(self):
        self.assertIn("$1,070.50", raw("yearly-torn"))
        self.assertEqual(self.s["closing"]["total"], 1070.5)


class SelfCheck(unittest.TestCase):
    """The closing balances off the table are checked against the total the
    front page prints in words."""

    def test_a_statement_that_disagrees_with_itself_is_refused(self):
        text = edit_line("yearly-2024", ["You have $643.50"], "$643.50", "$644.50")
        with self.assertRaises(ValueError) as caught:
            pc.parse_text(text, "2024/2024.pdf")
        self.assertIn("643.50", str(caught.exception))
        self.assertIn("644.50", str(caught.exception))

    def test_the_older_layouts_are_checked_the_same_way(self):
        text = edit_line("summary-2016", ["all your CPF accounts is"],
                         "$192.20", "$192.30")
        with self.assertRaises(ValueError):
            pc.parse_text(text, "2016/2016.pdf")


class Pieces(unittest.TestCase):
    """The helpers the layouts share."""

    def test_money_reads_every_shape_cpf_prints(self):
        self.assertEqual(pc.money("1,234.56"), 1234.56)
        self.assertEqual(pc.money("$432.10"), 432.10)
        self.assertEqual(pc.money("-$18.00"), -18.0)
        self.assertEqual(pc.money("0.00"), 0.0)

    def test_labelled_wants_the_amount_next_to_its_label(self):
        text = "You have $421.00 in your CPF accounts.\nYou            $   0.00\n"
        self.assertEqual(pc.labelled(text, r"\bYou\b"), 0.0)

    def test_labelled_falls_back_when_the_label_is_not_printed(self):
        self.assertEqual(pc.labelled("nothing here", r"\bEmployment\b", 0.0), 0.0)

    def test_the_layout_of_each_fixture_is_recognised(self):
        self.assertEqual(pc.layout_of(raw("summary-2016")), "summary")
        self.assertEqual(pc.layout_of(raw("summary-quiet")), "summary")
        self.assertEqual(pc.layout_of(raw("one-off-2020")), "2020")
        self.assertEqual(pc.layout_of(raw("yearly-2024")), "yearly")
        self.assertEqual(pc.layout_of(raw("yearly-torn")), "yearly")

    def test_a_statement_with_no_transactions_table_is_all_zeroes(self):
        s = pc.parse_text("Yearly Statement of Account for 2030\n", "2030/x.pdf")
        self.assertEqual(s["opening"],
                         {"oa": 0.0, "sa": 0.0, "ma": 0.0, "total": 0.0})
        self.assertEqual(s["closing"],
                         {"oa": 0.0, "sa": 0.0, "ma": 0.0, "total": 0.0})
        self.assertEqual(s["transactions"], [])
        self.assertEqual(s["year"], 2030)


class Files(unittest.TestCase):
    """The folder-level entry points and the shell-out to pdftotext."""

    def test_a_missing_folder_is_no_statements(self):
        self.assertEqual(pc.parse_folder(Path("/nonexistent/cpf-statements")), [])

    def test_parse_pdf_shells_out_to_pdftotext_and_keeps_the_dump(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "2024").mkdir()
            pdf = folder / "2024" / "2024.pdf"
            pdf.write_bytes(b"%PDF-1.4")
            with mock.patch.object(pc.subprocess, "run") as run:
                run.return_value = mock.Mock(stdout=raw("yearly-2024"))
                s = pc.parse_pdf(pdf, folder)
            self.assertEqual(run.call_args[0][0][:2], ["pdftotext", "-layout"])
            self.assertEqual(s["file"], "2024/2024.pdf")
            self.assertEqual(s["closing"]["total"], 643.5)
            self.assertEqual((folder / "2024" / "2024.txt").read_text(),
                             raw("yearly-2024"))

    def test_parse_pdf_can_be_told_not_to_write_the_dump(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            pdf = folder / "2024.pdf"
            pdf.write_bytes(b"%PDF-1.4")
            with mock.patch.object(pc.subprocess, "run") as run:
                run.return_value = mock.Mock(stdout=raw("yearly-2024"))
                pc.parse_pdf(pdf, folder, write_text=False)
            self.assertFalse((folder / "2024.txt").exists())

    def test_parse_folder_returns_the_years_in_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            dumps = {}
            for year, name in ((2024, "yearly-2024"), (2020, "one-off-2020"),
                               (2016, "summary-2016")):
                (folder / str(year)).mkdir()
                (folder / str(year) / f"{year}.pdf").write_bytes(b"%PDF-1.4")
                dumps[str(folder / str(year) / f"{year}.pdf")] = raw(name)
            with mock.patch.object(pc.subprocess, "run") as run:
                run.side_effect = lambda cmd, **kw: mock.Mock(stdout=dumps[cmd[2]])
                out = pc.parse_folder(folder, write_text=False)
            self.assertEqual([s["year"] for s in out], [2016, 2020, 2024])
            self.assertEqual([s["file"] for s in out],
                             ["2016/2016.pdf", "2020/2020.pdf", "2024/2024.pdf"])

    def test_parse_folder_rejects_a_year_that_does_not_reconcile(self):
        """The analytics and the record read CPF through parse_folder, never
        through main(), so the balance check has to run here: a year whose
        opening plus transactions is not its closing is left out and named."""
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            dumps = {}
            broken = edit_line("yearly-2024", ["26 NOV", "HSE"], "-$102.00", "-$92.00")
            for year, body in ((2020, raw("one-off-2020")), (2024, broken)):
                (folder / str(year)).mkdir()
                (folder / str(year) / f"{year}.pdf").write_bytes(b"%PDF-1.4")
                dumps[str(folder / str(year) / f"{year}.pdf")] = body
            rejected = []
            with mock.patch.object(pc.subprocess, "run") as run:
                run.side_effect = lambda cmd, **kw: mock.Mock(stdout=dumps[cmd[2]])
                out = pc.parse_folder(folder, write_text=False, rejected=rejected)
            self.assertEqual([s["year"] for s in out], [2020])
            self.assertEqual(rejected, [{"year": 2024, "file": "2024/2024.pdf",
                                         "off": {"oa": 10.0, "sa": 0.0, "ma": 0.0}}])


class Line(unittest.TestCase):

    def test_the_cli_line_names_the_year_the_split_and_the_inflow(self):
        line = pc.summary(load("yearly-2024"))
        for want in ("2024", "643.50", "214.70", "89.40", "339.40",
                     "173.51", "147.49", "19.50"):
            self.assertIn(want, line)

    def test_a_year_with_no_split_prints_it_as_unknown(self):
        line = pc.summary(load("summary-2016"))
        self.assertIn("employee        n/a", line)
        self.assertIn("employer        n/a", line)


if __name__ == "__main__":
    unittest.main()
