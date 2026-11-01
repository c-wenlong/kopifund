"""The Upload page words every problem the server can send (G13).

apps/web/src/uploadProblems.ts gives each problem code a plain headline and one next
step. A code the server learns and the page does not know would show under a
general headline, so this keeps the two lists in step: every code a `Problem(...)`
call can send is in the page's table.
"""
import isolate  # noqa: F401  first: no test writes the real record or cache
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def server_codes():
    codes = set()
    for rel in ("finance/sync.py", "finance.py"):
        codes |= set(re.findall(r'Problem\(\s*"([a-z][a-z-]+)"', (ROOT / rel).read_text()))
    # Problem(f"row-without-{label}", ...) with label account or period
    codes |= {"row-without-account", "row-without-period"}
    return codes


def page_codes():
    src = (ROOT.parent / "apps" / "web" / "src" / "uploadProblems.ts").read_text()
    body = re.search(r"export const KNOWN: Record<string, Known> = \{(.*)\n\};", src, re.S).group(1)
    return set(re.findall(r'^\s*"([a-z][a-z-]+)": \{', body, re.M))


class UploadProblems(unittest.TestCase):
    def test_every_code_the_server_sends_is_worded_on_the_page(self):
        missing = server_codes() - page_codes()
        self.assertEqual(missing, set(), f"add these to KNOWN in apps/web/src/uploadProblems.ts: {sorted(missing)}")

    def test_the_page_words_no_code_the_server_never_sends(self):
        extra = page_codes() - server_codes()
        self.assertEqual(extra, set(), f"no Problem(...) call sends: {sorted(extra)}")


if __name__ == "__main__":
    unittest.main()
