"""The .env reader and the token lookup order.

Nothing here touches the real environment, the real repo root or the real
keychain: every test builds a Config over a temporary directory, passes the
`.env` path in, and stubs `subprocess.run` for the keychain step.
"""
import isolate  # noqa: F401  first: no test writes the real record or cache
import os
import subprocess
import tempfile
import unittest
import unittest.mock as mock
from pathlib import Path


from finance import dotenv, notion
from finance.model import Config

TOKEN = "secret_from_env_file"


def config(root, token_env="NOTION_TOKEN", token_command=""):
    return Config(root=Path(root), version="v", token_env=token_env,
                  token_command=token_command)


class ParseTest(unittest.TestCase):
    """dotenv.parse: what counts as a line, and what the value is."""

    def test_plain_pair(self):
        self.assertEqual(dotenv.parse("NOTION_TOKEN=secret_abc"),
                         {"NOTION_TOKEN": "secret_abc"})

    def test_export_prefix(self):
        self.assertEqual(dotenv.parse("export NOTION_TOKEN=secret_abc"),
                         {"NOTION_TOKEN": "secret_abc"})

    def test_quotes_are_stripped(self):
        got = dotenv.parse("A=\"double\"\nB='single'\nC=\"un'even\"")
        self.assertEqual(got, {"A": "double", "B": "single", "C": "un'even"})

    def test_quotes_keep_inner_spaces_and_hash(self):
        got = dotenv.parse("A=\" pad \"\nB='has # hash'")
        self.assertEqual(got, {"A": " pad ", "B": "has # hash"})

    def test_comments_and_blank_lines(self):
        text = "\n# a comment\n   # an indented one\n\nA=1\n"
        self.assertEqual(dotenv.parse(text), {"A": "1"})

    def test_inline_comment_after_whitespace(self):
        self.assertEqual(dotenv.parse("A=1 # why"), {"A": "1"})

    def test_hash_without_whitespace_is_part_of_the_value(self):
        self.assertEqual(dotenv.parse("A=one#two"), {"A": "one#two"})

    def test_crlf_line_endings(self):
        self.assertEqual(dotenv.parse("A=1\r\nB=2\r\n"), {"A": "1", "B": "2"})

    def test_equals_inside_the_value(self):
        self.assertEqual(dotenv.parse("A=b=c=d"), {"A": "b=c=d"})

    def test_surrounding_whitespace_is_dropped(self):
        self.assertEqual(dotenv.parse("  A =  1  \n"), {"A": "1"})

    def test_empty_value(self):
        self.assertEqual(dotenv.parse("A=\n"), {"A": ""})

    def test_lines_without_an_equals_are_skipped(self):
        self.assertEqual(dotenv.parse("just words\nA=1\n"), {"A": "1"})

    def test_empty_key_is_skipped(self):
        self.assertEqual(dotenv.parse("=orphan\nA=1\n"), {"A": "1"})

    def test_no_interpolation(self):
        self.assertEqual(dotenv.parse("A=$HOME/x"), {"A": "$HOME/x"})

    def test_later_line_wins(self):
        self.assertEqual(dotenv.parse("A=1\nA=2\n"), {"A": "2"})


class ReadTest(unittest.TestCase):
    """dotenv.read: a file, or nothing at all."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_reads_a_file(self):
        p = self.root / ".env"
        p.write_text("NOTION_TOKEN=secret_abc\n")
        self.assertEqual(dotenv.read(p), {"NOTION_TOKEN": "secret_abc"})

    def test_missing_file_is_empty(self):
        self.assertEqual(dotenv.read(self.root / "nope.env"), {})

    def test_unreadable_file_is_empty(self):
        p = self.root / ".env"
        p.write_text("NOTION_TOKEN=secret_abc\n")
        p.chmod(0o000)
        self.addCleanup(p.chmod, 0o600)
        if os.access(p, os.R_OK):        # root reads anything; skip there
            self.skipTest("this user can read a 000 file")
        self.assertEqual(dotenv.read(p), {})

    def test_a_directory_is_empty(self):
        self.assertEqual(dotenv.read(self.root), {})

    def test_bytes_that_are_not_utf8_are_empty(self):
        p = self.root / ".env"
        p.write_bytes(b"NOTION_TOKEN=secret_\xff\xfe\n")
        self.assertEqual(dotenv.read(p), {})


class LookupTest(unittest.TestCase):
    """dotenv.lookup: the environment wins, always."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env_file = Path(self.tmp.name) / ".env"
        self.env_file.write_text("NOTION_TOKEN=from_file\n")
        self.addCleanup(self.tmp.cleanup)

    def test_environment_wins(self):
        got = dotenv.lookup("NOTION_TOKEN", self.env_file, {"NOTION_TOKEN": "from_env"})
        self.assertEqual(got, "from_env")

    def test_file_when_the_environment_has_none(self):
        self.assertEqual(dotenv.lookup("NOTION_TOKEN", self.env_file, {}), "from_file")

    def test_empty_environment_value_falls_through(self):
        got = dotenv.lookup("NOTION_TOKEN", self.env_file, {"NOTION_TOKEN": ""})
        self.assertEqual(got, "from_file")

    def test_nothing_anywhere(self):
        self.assertIsNone(dotenv.lookup("OTHER_NAME", self.env_file, {}))

    def test_empty_file_value_is_none(self):
        self.env_file.write_text("NOTION_TOKEN=\n")
        self.assertIsNone(dotenv.lookup("NOTION_TOKEN", self.env_file, {}))


class FindTokenTest(unittest.TestCase):
    """notion.find_token: environment, then .env in the data folder, then the
    keychain. The old plain-text token file is no longer read at all."""

    KEYCHAIN = "print-the-token"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "data").mkdir()
        self.env_file = self.root / ".env"
        self.warnings = []
        self.addCleanup(self.tmp.cleanup)
        # No inherited NOTION_TOKEN, whatever the shell running the suite has.
        patcher = mock.patch.dict(os.environ, {}, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def warn(self, msg):
        self.warnings.append(msg)

    def write_env(self, value="from_dotenv"):
        self.env_file.write_text(f"NOTION_TOKEN={value}\n")

    def write_txt(self, value="from_txt"):
        (self.root / "data" / "notion-token.txt").write_text(value + "\n")

    def keychain(self, value="from_keychain", returncode=0):
        done = subprocess.CompletedProcess([], returncode, stdout=value + "\n", stderr="")
        return mock.patch("subprocess.run", return_value=done)

    def find(self, cfg=None):
        cfg = cfg or config(self.root, token_command=self.KEYCHAIN)
        return notion.find_token(cfg, warn=self.warn, env_file=self.env_file)

    # -------- order

    def test_environment_beats_everything(self):
        os.environ["NOTION_TOKEN"] = "from_environ"
        self.write_env()
        self.write_txt()
        with self.keychain():
            self.assertEqual(self.find(), "from_environ")
        self.assertEqual(self.warnings, [])

    def test_dotenv_beats_keychain_and_txt(self):
        self.write_env()
        self.write_txt()
        with self.keychain():
            self.assertEqual(self.find(), "from_dotenv")
        self.assertEqual(self.warnings, [])

    def test_keychain_beats_txt(self):
        self.write_txt()
        with self.keychain():
            self.assertEqual(self.find(), "from_keychain")
        self.assertEqual(self.warnings, [])

    # -------- each source on its own

    def test_dotenv_alone(self):
        self.write_env()
        cfg = config(self.root, token_command="")
        self.assertEqual(notion.find_token(cfg, warn=self.warn, env_file=self.env_file),
                         "from_dotenv")

    def test_dotenv_is_read_from_the_data_folder_by_default(self):
        self.write_env("from_root")
        cfg = config(self.root, token_command="")
        self.assertEqual(notion.find_token(cfg, warn=self.warn), "from_root")

    def test_quoted_and_exported_value_in_dotenv(self):
        self.env_file.write_text("export NOTION_TOKEN=\"secret_quoted\"\n")
        cfg = config(self.root, token_command="")
        self.assertEqual(notion.find_token(cfg, warn=self.warn, env_file=self.env_file),
                         "secret_quoted")

    def test_dotenv_under_a_renamed_variable(self):
        self.env_file.write_text("FINANCE_TOKEN=renamed\n")
        cfg = config(self.root, token_env="FINANCE_TOKEN", token_command="")
        self.assertEqual(notion.find_token(cfg, warn=self.warn, env_file=self.env_file),
                         "renamed")

    def test_empty_token_env_reads_neither_environment_nor_dotenv(self):
        os.environ["NOTION_TOKEN"] = "from_environ"
        self.write_env()
        cfg = config(self.root, token_env="", token_command="")
        with self.assertRaises(notion.NotionError):
            notion.find_token(cfg, warn=self.warn, env_file=self.env_file)

    def test_a_failing_keychain_command_ends_in_an_error(self):
        with self.keychain(returncode=1):
            with self.assertRaises(notion.NotionError):
                self.find()

    def test_a_keychain_command_that_cannot_run_ends_in_an_error(self):
        with mock.patch("subprocess.run", side_effect=OSError("no shell")):
            with self.assertRaises(notion.NotionError):
                self.find()

    def test_the_old_token_file_is_ignored(self):
        self.write_txt()
        cfg = config(self.root, token_command="")
        with self.assertRaises(notion.NotionError):
            notion.find_token(cfg, warn=self.warn, env_file=self.env_file)
        self.assertEqual(self.warnings, [])

    # -------- what gets printed

    def test_the_dotenv_path_prints_nothing(self):
        self.write_env()
        cfg = config(self.root, token_command="")
        notion.find_token(cfg, warn=self.warn, env_file=self.env_file)
        self.assertEqual(self.warnings, [])

    # -------- nothing anywhere

    def test_the_error_names_the_keychain_first(self):
        cfg = config(self.root, token_command=self.KEYCHAIN)
        with self.keychain(returncode=1):
            with self.assertRaises(notion.NotionError) as e:
                self.find(cfg)
        msg = str(e.exception)
        self.assertEqual(msg.split(", ")[0], f"no Notion token: store it with `{self.KEYCHAIN}`")
        self.assertLess(msg.index(self.KEYCHAIN), msg.index("export $NOTION_TOKEN"))
        self.assertLess(msg.index("export $NOTION_TOKEN"), msg.index(".env in the data folder"))

    def test_the_error_leaves_the_keychain_out_when_there_is_no_command(self):
        cfg = config(self.root, token_command="")
        with self.assertRaises(notion.NotionError) as e:
            notion.find_token(cfg, warn=self.warn, env_file=self.env_file)
        msg = str(e.exception)
        self.assertIn(".env", msg)
        self.assertNotIn("`", msg)


if __name__ == "__main__":
    unittest.main()
