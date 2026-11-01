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
"""
import atexit
import os
import shutil
import tempfile
from pathlib import Path

if not os.environ.get("KOPIFUND_TEST_SCRATCH"):
    _SCRATCH = tempfile.mkdtemp(prefix="kopifund-tests-")
    atexit.register(shutil.rmtree, _SCRATCH, ignore_errors=True)
    os.environ["KOPIFUND_TEST_SCRATCH"] = _SCRATCH
os.environ["KOPIFUND_DB"] = str(Path(os.environ["KOPIFUND_TEST_SCRATCH"]) / "kopifund.db")
os.environ["KOPIFUND_CACHE"] = str(Path(os.environ["KOPIFUND_TEST_SCRATCH"]) / "cache")
os.environ["KOPIFUND_NO_KEYCHAIN"] = "1"
os.environ["KOPIFUND_ENV_FILE"] = str(Path(os.environ["KOPIFUND_TEST_SCRATCH"]) / "keys.env")
os.environ["KOPIFUND_COPILOT"] = str(Path(os.environ["KOPIFUND_TEST_SCRATCH"]) / "copilot")
os.environ["KOPIFUND_DEVICES"] = str(Path(os.environ["KOPIFUND_TEST_SCRATCH"]) / "devices.json")
