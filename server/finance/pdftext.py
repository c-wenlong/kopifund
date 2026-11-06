"""pdftotext, the one way Kopifund reads a PDF's text.

Every parser, the upload's sniff and file naming, and `finance.py sample` read
a statement as `pdftotext -layout <pdf> -` prints it (poppler; `brew install
poppler`). They all call pdf_text, so the arguments, the time limit and the
errors a caller has to handle are the same everywhere.
"""
import shutil
import signal
import subprocess
import sys

from .paths import text_dump_path

# A statement is a few pages and pdftotext reads one in well under a second;
# a PDF it is still working on after this long is one it cannot read.
TIMEOUT_SECONDS = 60


def installed():
    return shutil.which("pdftotext") is not None


def require():
    """For a parser's command line: stop with the fix when pdftotext is missing."""
    if not installed():
        sys.exit("pdftotext not found (brew install poppler)")


def pdf_text(pdf):
    """The PDF's layout-preserved text. Raises FileNotFoundError when pdftotext
    is not installed, and subprocess.CalledProcessError when it cannot read the
    file: it failed, or it ran past TIMEOUT_SECONDS (it is then stopped)."""
    cmd = ["pdftotext", "-layout", str(pdf), "-"]
    try:
        return subprocess.run(cmd, capture_output=True, text=True, check=True,
                              timeout=TIMEOUT_SECONDS).stdout
    except subprocess.TimeoutExpired as e:
        raise subprocess.CalledProcessError(-signal.SIGKILL, cmd, e.output, e.stderr) from e


def pdf_to_text(pdf, write=True):
    """pdf_text, saving a dump of it (finance.paths.text_dump_path: under
    cache/text/ for a statement in the data folder, beside the PDF otherwise)
    when `write`, so a later run can be checked against it."""
    out = pdf_text(pdf)
    if write:
        text_dump_path(pdf).write_text(out)
    return out
