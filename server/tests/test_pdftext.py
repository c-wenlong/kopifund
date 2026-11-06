"""pdf_text, the one call to pdftotext, and what it raises."""
import isolate  # noqa: F401  first: no test writes the real record or cache
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from finance import pdftext


class PdfText(unittest.TestCase):
    def test_the_call_is_layout_text_to_stdout_with_a_time_limit(self):
        with mock.patch("subprocess.run", return_value=mock.Mock(stdout="text")) as run:
            self.assertEqual(pdftext.pdf_text(Path("/x/a.pdf")), "text")
        self.assertEqual(run.call_args.args[0], ["pdftotext", "-layout", "/x/a.pdf", "-"])
        self.assertEqual(run.call_args.kwargs["timeout"], pdftext.TIMEOUT_SECONDS)
        self.assertTrue(run.call_args.kwargs["check"])

    def test_a_pdf_it_cannot_finish_reads_as_one_it_cannot_read(self):
        hung = subprocess.TimeoutExpired(["pdftotext"], pdftext.TIMEOUT_SECONDS)
        with mock.patch("subprocess.run", side_effect=hung), \
                self.assertRaises(subprocess.CalledProcessError):
            pdftext.pdf_text("a.pdf")

    def test_a_missing_command_is_file_not_found(self):
        with mock.patch("subprocess.run", side_effect=FileNotFoundError("pdftotext")), \
                self.assertRaises(FileNotFoundError):
            pdftext.pdf_text("a.pdf")

    def test_the_dump_is_written_only_when_asked(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch("subprocess.run", return_value=mock.Mock(stdout="text")):
            pdf = Path(tmp) / "a.pdf"
            pdftext.pdf_to_text(pdf, write=False)
            self.assertFalse((Path(tmp) / "a.txt").exists())
            pdftext.pdf_to_text(pdf)
            self.assertEqual((Path(tmp) / "a.txt").read_text(), "text")

    def test_require_stops_a_command_line_with_the_fix(self):
        with mock.patch("shutil.which", return_value=None), self.assertRaises(SystemExit) as e:
            pdftext.require()
        self.assertIn("brew install poppler", str(e.exception))


if __name__ == "__main__":
    unittest.main()
