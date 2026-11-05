"""Point the record and the cache at a throwaway folder for the whole test run.

Tests that need real statements read the data folder, but none may write to
it. A test that ran the real sync against the real record once deleted the
owner's trips. Every test module imports this first, so the redirect holds
however unittest finds the tests (`discover tests`, `-s tests -p ...`, or one
module by name), and inherited values are overwritten, never trusted.

G22: the same goes for API keys and the copilot's threads. KOPIFUND_NO_KEYCHAIN
makes server/finance/keys.py refuse to run the real `security` binary, so no test can
write to the owner's Keychain (tests hand in a fake instead), the `.env` that
Settings writes a key to without a keychain is a throwaway file, and so is the
folder Ask Kopifund keeps its threads in.

Audit: the data folder itself (KOPIFUND_DATA) is a throwaway folder too, so
the rules, rules history, exports, profile and config a test reaches without
its own fixture are never the owner's. The three tests that read real
statements (test_monthly_run, test_analytics_snapshot, test_analytics_review)
see the real folder only when the run asks for it by name:

    KOPIFUND_TEST_REAL_DATA=1 python3 -m unittest discover tests

reads the folder KOPIFUND_DATA (or ~/.kopifund) named before this redirect, and
KOPIFUND_TEST_REAL_DATA=<folder> reads that folder. Even then only those
modules see it, and only while their own tests run (`real_data` below).
Without it they skip.
"""
import atexit
import os
import shutil
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

if not os.environ.get("KOPIFUND_TEST_SCRATCH"):
    _SCRATCH = tempfile.mkdtemp(prefix="kopifund-tests-")
    atexit.register(shutil.rmtree, _SCRATCH, ignore_errors=True)
    os.environ["KOPIFUND_TEST_SCRATCH"] = _SCRATCH
# "1" names the folder this run would have used; resolved before KOPIFUND_DATA
# moves, and stored resolved, so a child process that imports this again
# reads the same folder rather than the throwaway one
if os.environ.get("KOPIFUND_TEST_REAL_DATA") == "1":
    os.environ["KOPIFUND_TEST_REAL_DATA"] = str(
        Path(os.environ.get("KOPIFUND_DATA") or Path.home() / ".kopifund").expanduser().absolute())
os.environ["KOPIFUND_DATA"] = str(Path(os.environ["KOPIFUND_TEST_SCRATCH"]) / "data")
os.environ["KOPIFUND_DB"] = str(Path(os.environ["KOPIFUND_TEST_SCRATCH"]) / "kopifund.db")
os.environ["KOPIFUND_CACHE"] = str(Path(os.environ["KOPIFUND_TEST_SCRATCH"]) / "cache")
os.environ["KOPIFUND_NO_KEYCHAIN"] = "1"
os.environ["KOPIFUND_ENV_FILE"] = str(Path(os.environ["KOPIFUND_TEST_SCRATCH"]) / "keys.env")
os.environ["KOPIFUND_COPILOT"] = str(Path(os.environ["KOPIFUND_TEST_SCRATCH"]) / "copilot")
os.environ["KOPIFUND_DEVICES"] = str(Path(os.environ["KOPIFUND_TEST_SCRATCH"]) / "devices.json")


def real_data_folder() -> Path | None:
    """The real data folder this run was asked to read, or None."""
    named = os.environ.get("KOPIFUND_TEST_REAL_DATA")
    # absolute, so a child process started in another folder (the parsers
    # run inside the statements folder) reads the same one, not a relative
    # path that misses the scratch cache and writes beside the PDFs
    return Path(named).expanduser().absolute() if named else None


@contextmanager
def real_data():
    """KOPIFUND_DATA names the real data folder inside the block and the
    throwaway one again after it. Skips (unittest.SkipTest) when the run did not
    ask for real data. The record and cache stay redirected throughout."""
    folder = real_data_folder()
    if folder is None:
        raise unittest.SkipTest("reads real statements: run with KOPIFUND_TEST_REAL_DATA=1 to include it")
    before = os.environ["KOPIFUND_DATA"]
    os.environ["KOPIFUND_DATA"] = str(folder)
    try:
        yield folder
    finally:
        os.environ["KOPIFUND_DATA"] = before


def real_data_for_module():
    """(setUpModule, tearDownModule) that hold `real_data` for one test
    module's whole run, so the folder is real only while that module's tests run."""
    held = []

    def set_up():
        block = real_data()
        block.__enter__()
        held.append(block)

    def tear_down():
        while held:
            held.pop().__exit__(None, None, None)

    return set_up, tear_down
