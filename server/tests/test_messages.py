"""G13: a refusal the app shows starts like a sentence, with a capital."""
import isolate  # noqa: F401  first: no test writes the real record or cache
import ast
import unittest
from pathlib import Path

from finance import monthly

ROOT = Path(__file__).resolve().parent.parent

# a message that opens with the name of the field it is about, as the API
# documents it ("month is YYYY-MM"), reads as a field name and keeps its case
FIELD_FIRST = {"month", "months", "planned", "kind", "to", "from", "by", "scope", "account", "index",
               "names", "countries", "spend_rate", "cpf_employer_rate", "need",
               # G22: the AI card's fields, and the copilot's tool arguments (server/finance/copilot_tools.py)
               "provider", "send_rows", "limit", "type", "x", "series", "rows", "unit", "questions", "payee_kind",
               "planned_minor"}


def _opening(node):
    """The first literal text of a message: a string, or the start of an f-string."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr) and node.values and isinstance(node.values[0], ast.Constant):
        return node.values[0].value
    return ""


def _name(func):
    return func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")


class SentenceCaseTest(unittest.TestCase):
    def test_no_refusal_in_finance_starts_in_lower_case(self):
        bad = []
        for path in sorted((ROOT / "finance").glob("*.py")) + [ROOT / "serve.py"]:
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if not (isinstance(node, ast.Call) and node.args):
                    continue
                name = _name(node.func)
                if not (name.endswith("Refused") or name == "AIError"):
                    continue
                text = _opening(node.args[0])
                word = text.split(" ", 1)[0].rstrip(":,")
                if text[:1].islower() and word not in FIELD_FIRST:
                    bad.append(f"{path.name}:{node.lineno} {text[:40]!r}")
        self.assertEqual(bad, [])

    def test_an_upload_that_reads_as_nothing_is_refused_in_a_sentence(self):
        from unittest import mock
        with mock.patch.object(monthly, "_text", return_value="An invoice\nno statement dates here\n"):
            with self.assertRaises(monthly.UploadRefused) as cm:
                monthly.canonical_name("posb-everyday", "upload.pdf")
        self.assertEqual(str(cm.exception), "This PDF is not a statement this account can read")
        self.assertTrue(monthly.UNREADABLE[0].isupper())


if __name__ == "__main__":
    unittest.main()
