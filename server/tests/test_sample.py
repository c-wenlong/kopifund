"""`finance.py sample <pdf>` (server/finance/sample.py, G21): a statement's text with
what identifies its holder replaced, for whoever builds its layout.

Run on every scrubbed fixture: no name, address, account or card number, NRIC,
email or phone number the fixtures print survives, every amount and date does,
and every line keeps its length. A property test plants a random identifier of
each kind in a fixture and checks it is always replaced. The command writes
beside the PDF or where it is told, never into the repository, and never
changes the PDF.
"""
import isolate  # noqa: F401  first: no test writes the real record or cache
import ast
import contextlib
import hashlib
import io
import os
import random
import re
import shutil
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import importlib.util

from finance import sample

FIXTURES = Path(__file__).parent / "fixtures"
ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("finance_cli_sample", ROOT / "finance.py")
CLI = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(CLI)
ALL = sorted(p for d in ("statements", "tiger", "cpf", "unknown") for p in (FIXTURES / d).glob("*.txt"))

# what the fixtures print that names their holder or someone they paid
NAMES = ["TAN MEI LING", "Mei Ling Tan", "NG AH LAN", "LIM AH KOW", "LIM WEI MING", "KWAME ASANTE BOATENG",
         "LAKSHMI SRINIVAS", "KRISTIN HALVORSEN", "DIEGO RAMOS FERNANDEZ", "Amita Roy", "TEST OWNER",
         "ALEX TEO WEI JIE", "JORDAN LIM KAI", "Priya Raman"]
ADDRESSES = ["ALPHA RD", "100100", "#01-100", "ANG MO KIO AVE 3", "560012", "BETA STREET", "300221", "#05-321"]
NUMBERS = ["120-345678-9", "130-24680-5", "8888880001234567", "601234567001", "123-456-789-0", "987-654-321-0",
           "1029384756", "150-246-80", "S9012345A", "6580000001", "4000-1234-5678-4242", "5100-1234-5678-5454",
           "7712-334455-1", "S1234567D", "4111 2222 3333 4444", "9123 4567", "91234567"]
EMAILS = ["MEILINGTAN.SAMPLE@EXAMPLE.COM", "alex.teo.sample@example.org"]


def placeholder(s):
    return set(s) <= set("0Xx -#.@*()/,:'")


class EveryFixture(unittest.TestCase):
    def test_the_fixtures_are_all_here(self):
        self.assertGreaterEqual(len(ALL), 30)

    def test_nothing_that_names_the_holder_survives(self):
        for path in ALL:
            with self.subTest(path.name):
                out, _ = sample.scrub(path.read_text())
                low = out.lower()
                for s in NAMES + ADDRESSES + NUMBERS + EMAILS:
                    self.assertNotIn(s.lower(), low)
                # and no shape of one: seven or more digits (other than a date or a
                # placeholder), a card's four groups, an NRIC, an email
                for m in sample.DIGITS.finditer(out):
                    if sum(c.isdigit() for c in m.group(0)) >= 7 and not sample.DATE.fullmatch(m.group(0)):
                        self.assertTrue(placeholder(m.group(0)), m.group(0))
                for rx in (sample.CARD, sample.NRIC, sample.EMAIL):
                    for m in rx.finditer(out):
                        self.assertTrue(placeholder(m.group(0)[1:-1]), m.group(0))

    def test_every_amount_and_date_survives_where_it_was_printed(self):
        for path in ALL:
            with self.subTest(path.name):
                text = path.read_text()
                out, _ = sample.scrub(text)
                for rx in (sample.AMOUNT, sample.DATE, sample.TEXT_DATE):
                    kept = [(m.start(), m.group(0)) for m in rx.finditer(text)]
                    self.assertTrue(all(out[s:s + len(v)] == v for s, v in kept))
                # so every line that prints an amount still prints all of them
                for before, after in zip(text.split("\n"), out.split("\n")):
                    self.assertEqual(sample.AMOUNT.findall(before), sample.AMOUNT.findall(after))

    def test_every_line_keeps_its_length_and_the_layout_its_words(self):
        for path in ALL:
            with self.subTest(path.name):
                text = path.read_text()
                out, _ = sample.scrub(text)
                self.assertEqual([len(ln) for ln in out.split("\n")], [len(ln) for ln in text.split("\n")])
        # the headings and column titles a layout is built from stay as printed
        out, _ = sample.scrub((FIXTURES / "unknown/acme-savings.txt").read_text())
        for heading in ("ACME DIGITAL BANK", "Monthly Statement", "1 Sep 2026 to 30 Sep 2026", "Money out",
                        "Money in", "Balance", "Opening balance", "Closing balance", "Account number",
                        "Savings pot", "KOPI CORNER", "From Main account", "Page 2 of 2"):
            self.assertIn(heading, out)

    def test_it_counts_what_it_replaced_by_kind(self):
        _, counts = sample.scrub((FIXTURES / "unknown/acme-savings.txt").read_text())
        self.assertEqual(set(counts), set(sample.KINDS))
        for kind in ("name", "address", "postcode", "id", "email", "phone", "account"):
            self.assertGreater(counts[kind], 0, kind)
        self.assertEqual(sample.report({"name": 1, "email": 2}),
                         "1 name, 0 street addresses, 0 postcodes, 0 NRIC or FIN numbers, 2 emails, "
                         "0 phone numbers, 0 account and card numbers")

    def test_the_owners_own_moves_and_the_statements_own_words_stay(self):
        text = ("30 May   TOP UP WALLET FROM MY ACCOUNT          40.00 CR\n"
                "         FAST TRANSFER TO MY WALLET              5.00 DB\n"
                "Sales: Cash received from selling securities\n"
                "120-345678-9:IB SGD@1.281713\n")
        out, _ = sample.scrub(text)
        for kept in ("TOP UP WALLET FROM MY ACCOUNT", "FAST TRANSFER TO MY WALLET", "selling securities",
                     "SGD@1.281713"):
            self.assertIn(kept, out)
        self.assertNotIn("120-345678-9", out)


def _word(rng):
    while True:
        w = "".join(rng.choice("BCDFGHJKLMNPRSTVWYZ") + rng.choice("AEIOU") for _ in range(rng.randint(2, 3)))
        if w not in sample.NOT_A_NAME:
            return w


def _digits(rng, n):
    return "".join(rng.choice("123456789") for _ in range(n))


def _planted(rng):
    """(the line to plant, the identifier in it, its kind)."""
    kind = rng.choice(["account", "card", "nric", "email", "phone", "name", "masked"])
    if kind == "account":
        value = rng.choice([f"{_digits(rng, 3)}-{_digits(rng, 6)}-{_digits(rng, 1)}",
                            f"{_digits(rng, 3)}-{_digits(rng, 5)}-{_digits(rng, 1)}",
                            f"{_digits(rng, 3)}-{_digits(rng, 3)}-{_digits(rng, 3)}-{_digits(rng, 1)}",
                            _digits(rng, rng.randint(9, 16)), f"Y-{_digits(rng, 10)}"])
        context = rng.choice(["Account No.: ", "for ACCOUNT: ", "Ref ", "Account number ", ""])
    elif kind == "card":
        sep = rng.choice([" ", "-"])
        value = sep.join(_digits(rng, 4) for _ in range(4))
        context = rng.choice(["Card ", "CARD NO.: ", ""])
    elif kind == "masked":
        value = rng.choice([f"XXXX-XXXX-XXXX-{_digits(rng, 4)}", f"{_digits(rng, 6)}******{_digits(rng, 4)}",
                            f"xx-{_digits(rng, 4)}"])
        context = rng.choice(["Card: ", ""])
    elif kind == "nric":
        value = rng.choice("STFGM") + _digits(rng, 7) + rng.choice("ABCDEFGHIJKZ")
        context = rng.choice(["NRIC ", "Account No. : ", "(CPF Account Number: "])
    elif kind == "email":
        value = f"{_word(rng).lower()}.{_word(rng).lower()}@{_word(rng).lower()}.com"
        context = rng.choice(["", "Email: ", "write to "])
    elif kind == "phone":
        value = rng.choice([f"+65 {rng.choice('689')}{_digits(rng, 3)} {_digits(rng, 4)}",
                            f"{rng.choice('89')}{_digits(rng, 7)}", f"65{rng.choice('89')}{_digits(rng, 7)}"])
        context = rng.choice(["Mobile ", "Call ", "PayNow to ", ""])
    else:
        value = " ".join(_word(rng) for _ in range(rng.randint(2, 3)))
        if rng.random() < 0.5:
            value = value.title()
        context = rng.choice(["TO: ", "FROM: ", "Transfer to ", "Transfer from ", "Received money from ",
                              "SEND MONEY TO ", "To: ", "Payment from "])
    return f"{' ' * rng.randint(0, 12)}{context}{value}", value, kind


class APlantedIdentifier(unittest.TestCase):
    def test_is_always_replaced(self):
        rng = random.Random(21)
        for n in range(400):
            path = rng.choice(ALL)
            lines = path.read_text().split("\n")
            line, value, kind = _planted(rng)
            at = rng.randrange(len(lines) + 1)
            lines.insert(at, line)
            text = "\n".join(lines)
            with self.subTest(n=n, fixture=path.name, kind=kind, value=value):
                out, counts = sample.scrub(text)
                self.assertNotIn(value, out)
                self.assertNotIn(value.lower(), out.lower())
                # and the statement's amounts are all still there
                self.assertEqual(sample.AMOUNT.findall(text), sample.AMOUNT.findall(out))
                self.assertEqual(len(out), len(text))


def _pdf(lines):
    """A one-page PDF of these lines in Courier, for pdftotext."""
    body = "BT /F1 8 Tf 10 TL 20 800 Td " + " ".join(
        "(" + ln.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") + ") Tj T*" for ln in lines) + " ET"
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 842] /Contents 4 0 R "
            b"/Resources << /Font << /F1 5 0 R >> >> >>",
            f"<< /Length {len(body)} >>\nstream\n{body}\nendstream".encode(),
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>"]
    out, offsets = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{off:010d} 00000 n \n".encode() for off in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


class TheCommand(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir)
        self.pdf = self.dir / "statement.pdf"
        self.pdf.write_bytes(b"%PDF-1.4 stand-in")
        self.text = (FIXTURES / "unknown/acme-savings.txt").read_text()
        p = mock.patch.object(sample, "pdf_text", return_value=self.text)
        self.read = p.start()
        self.addCleanup(p.stop)

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = CLI.main(["sample", *args])
        return code, out.getvalue(), err.getvalue()

    def test_it_writes_beside_the_pdf_and_says_what_it_replaced(self):
        before = (hashlib.sha256(self.pdf.read_bytes()).hexdigest(), self.pdf.stat().st_mtime_ns)
        with mock.patch.dict(os.environ, {"KOPIFUND_DATA": str(self.dir / "no-data-folder")}):
            code, out, _ = self.run_cli(str(self.pdf))
        self.assertEqual(code, 0)
        written = self.dir / "statement.sample.txt"
        self.assertEqual(written.read_text(), sample.scrub(self.text)[0])
        self.assertIn(f"wrote {written.resolve()}", out)
        self.assertIn("replaced: ", out)
        self.assertIn("NRIC or FIN number", out)
        self.assertIn("best effort", out)
        # private until the owner has read it
        self.assertEqual(stat.S_IMODE(written.stat().st_mode), 0o600)
        # the PDF is only read, and no data folder or config was needed
        self.assertEqual((hashlib.sha256(self.pdf.read_bytes()).hexdigest(), self.pdf.stat().st_mtime_ns), before)
        self.assertFalse((self.dir / "no-data-folder").exists())
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ["statement.pdf", "statement.sample.txt"])

    def test_it_writes_where_it_is_told(self):
        code, out, _ = self.run_cli(str(self.pdf), "--out", str(self.dir / "shared" / "acme.txt"))
        self.assertEqual(code, 0)
        self.assertTrue((self.dir / "shared" / "acme.txt").is_file())
        self.assertFalse((self.dir / "statement.sample.txt").exists())

    def test_an_existing_file_it_replaces_ends_up_private(self):
        old = self.dir / "shared.txt"
        old.write_text("an older sample")
        old.chmod(0o644)
        self.assertEqual(self.run_cli(str(self.pdf), "--out", str(old))[0], 0)
        self.assertEqual(stat.S_IMODE(old.stat().st_mode), 0o600)
        self.assertEqual(old.read_text(), sample.scrub(self.text)[0])
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ["shared.txt", "statement.pdf"])

    def test_the_longest_name_the_folder_takes_still_works(self):
        # the file it writes first and swaps in has a short name of its own
        longest = "s" * 251 + ".txt"
        self.assertEqual(self.run_cli(str(self.pdf), "--out", str(self.dir / longest))[0], 0)
        self.assertTrue((self.dir / longest).is_file())

    def test_a_hard_link_to_the_pdf_is_refused_and_the_pdf_kept(self):
        link = self.dir / "statement-copy.txt"
        os.link(self.pdf, link)
        code, _, err = self.run_cli(str(self.pdf), "--out", str(link))
        self.assertEqual(code, 3)
        self.assertIn("is a PDF", err)
        self.assertEqual(self.pdf.read_bytes(), b"%PDF-1.4 stand-in")

    def test_a_home_path_is_expanded_before_it_is_checked_and_written(self):
        home = self.dir / "home"
        home.mkdir()
        with mock.patch.dict(os.environ, {"HOME": str(home)}):
            code, out, _ = self.run_cli(str(self.pdf), "--out", "~/Desktop/sample.txt")
        self.assertEqual(code, 0)
        self.assertTrue((home / "Desktop" / "sample.txt").is_file())
        self.assertFalse((Path.cwd() / "~").exists())

    def test_a_folder_or_a_failed_write_is_a_refusal_not_a_traceback(self):
        code, _, err = self.run_cli(str(self.pdf), "--out", str(self.dir))
        self.assertEqual(code, 3)
        self.assertIn("is a folder", err)
        with mock.patch.object(sample.tempfile, "mkstemp", side_effect=PermissionError(13, "Permission denied")):
            code, _, err = self.run_cli(str(self.pdf), "--out", str(self.dir / "locked" / "x.txt"))
        self.assertEqual(code, 3)
        self.assertIn("Could not write", err)
        with mock.patch.object(sample.os, "replace", side_effect=OSError(28, "No space left on device")):
            code, _, err = self.run_cli(str(self.pdf), "--out", str(self.dir / "full.txt"))
        self.assertEqual(code, 3)
        self.assertIn("No space left", err)
        # the half-written file is not left behind
        self.assertEqual(sorted(p.name for p in self.dir.iterdir() if p.is_file()), ["statement.pdf"])

    def test_it_never_writes_into_the_repository_or_over_a_pdf(self):
        for out in (ROOT / "tests" / "fixtures" / "x.sample.txt", ROOT / "x.txt", self.pdf, self.dir / "other.pdf"):
            with self.subTest(str(out)):
                code, _, err = self.run_cli(str(self.pdf), "--out", str(out))
                self.assertEqual(code, 3)
                self.assertTrue(err.strip())
                self.assertFalse(out.exists() and out != self.pdf)
        # a PDF inside the repository has nowhere beside it to go
        inside = ROOT / "tests" / "fixtures" / "unknown"
        with self.assertRaises(sample.SampleRefused):
            sample.target(inside / "statement.pdf")
        self.assertEqual(self.pdf.read_bytes(), b"%PDF-1.4 stand-in")

    def test_it_says_what_is_missing(self):
        code, _, err = self.run_cli()
        self.assertEqual(code, 3)
        self.assertIn("sample needs the statement PDF", err)
        code, _, err = self.run_cli(str(self.dir / "nowhere.pdf"))
        self.assertEqual(code, 3)
        self.read.side_effect = FileNotFoundError("pdftotext")
        code, _, err = self.run_cli(str(self.pdf))
        self.assertEqual(code, 3)
        self.assertIn("brew install poppler", err)

    def test_it_says_when_kopifund_already_reads_the_statement(self):
        self.read.return_value = (FIXTURES / "statements/posb-deposit.txt").read_text()
        code, out, _ = self.run_cli(str(self.pdf))
        self.assertEqual(code, 0)
        self.assertIn("already reads this as a POSB account statement", out)


@unittest.skipUnless(shutil.which("pdftotext"), "pdftotext is not installed")
class ARealPdf(unittest.TestCase):
    def test_goes_through_pdftotext_as_the_parsers_read_it(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        pdf = tmp / "real.pdf"
        pdf.write_bytes(_pdf(["ACME DIGITAL BANK        Monthly Statement", "ALEX TEO WEI JIE",
                              "BLK 221 BETA STREET 9", "SINGAPORE 300221",
                              "Account number 7712-334455-1", "01 Sep   Opening balance      1,200.00"]))
        path, counts, _ = sample.write(pdf)
        out = path.read_text()
        self.assertIn("1,200.00", out)
        self.assertIn("ACME DIGITAL BANK", out)
        for gone in ("ALEX TEO", "BETA STREET", "300221", "7712-334455-1"):
            self.assertNotIn(gone, out)
        self.assertGreater(counts["account"], 0)


class ItSendsNothing(unittest.TestCase):
    def test_the_module_imports_nothing_that_reaches_the_network(self):
        tree = ast.parse((ROOT / "finance" / "sample.py").read_text())
        imported = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        imported |= {n.module.split(".")[0] for n in ast.walk(tree)
                     if isinstance(n, ast.ImportFrom) and n.module and n.level == 0}
        self.assertLessEqual(imported, {"os", "re", "subprocess", "tempfile", "pathlib"})


if __name__ == "__main__":
    unittest.main()
