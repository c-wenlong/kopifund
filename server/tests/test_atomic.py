"""write_private, the one way a file beside the record is written."""
import isolate  # noqa: F401  first: no test writes the real record or cache
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from finance.atomic import write_private


class WritePrivate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def test_text_and_bytes_land_whole_and_private(self):
        path = self.dir / "a.json"
        write_private(path, "café\n")
        self.assertEqual(path.read_bytes(), "café\n".encode("utf-8"))
        write_private(path, b"\x00\x01")
        self.assertEqual(path.read_bytes(), b"\x00\x01")
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_an_open_file_loses_its_looser_mode(self):
        path = self.dir / "config.toml"
        path.write_text("old")
        path.chmod(0o644)
        write_private(path, "new")
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_a_linked_file_is_replaced_never_written_through(self):
        other = self.dir / "statement.pdf"
        other.write_bytes(b"%PDF")
        os.link(other, self.dir / "out.txt")
        write_private(self.dir / "out.txt", "text")
        self.assertEqual(other.read_bytes(), b"%PDF")

    def test_a_failed_replace_keeps_the_old_file_and_leaves_no_temp(self):
        path = self.dir / "keys.env"
        path.write_text("old")
        with mock.patch("os.replace", side_effect=OSError(28, "No space left on device")), \
                self.assertRaises(OSError):
            write_private(path, "new")
        self.assertEqual(path.read_text(), "old")
        self.assertEqual([p.name for p in self.dir.iterdir()], ["keys.env"])

    def test_a_long_name_still_gets_a_temp_file(self):
        # 250 one-byte characters, 62 four-byte ones (252 bytes with .txt), and
        # a name that is not UTF-8, as Linux allows (Python holds it as surrogates)
        odd = os.fsdecode(b"caf\xe9.txt")
        for name in ("x" * 250, "\U0001F600" * 62 + ".txt", odd):
            with self.subTest(ascii(name)[:20]):
                path = self.dir / name
                if name == odd:
                    try:
                        path.touch()     # APFS refuses a name that is not UTF-8
                    except OSError:
                        continue
                write_private(path, "ok")
                self.assertEqual(path.read_text(), "ok")


if __name__ == "__main__":
    unittest.main()
