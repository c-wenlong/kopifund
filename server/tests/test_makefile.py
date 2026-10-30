import isolate  # noqa: F401  first: no test writes the real record or cache
import os
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent


@unittest.skipUnless(shutil.which("make"), "needs make")
class DataFolderPathTest(unittest.TestCase):
    """The Makefile's data folder survives a space in its path. `make -n`
    prints the commands a target would run and runs none of them."""

    def planned(self, data: str, target: str = "backup") -> str:
        env = {**os.environ, "KOPIFUND_DATA": data}
        env.pop("DATA", None)
        return subprocess.run(["make", "-n", "--no-print-directory", target], cwd=ROOT, env=env,
                              capture_output=True, text=True, check=True).stdout

    def test_a_space_in_the_path_stays_one_path(self):
        self.assertIn('mkdir -p "/tmp/a b/data/backups"', self.planned("/tmp/a b/data"))

    def test_a_folder_named_on_the_command_line_is_used(self):
        env = {k: v for k, v in os.environ.items() if k not in ("DATA", "KOPIFUND_DATA")}
        out = subprocess.run(["make", "-n", "--no-print-directory", "KOPIFUND_DATA=/tmp/c d/data", "backup"],
                             cwd=ROOT, env=env, capture_output=True, text=True, check=True).stdout
        self.assertIn('mkdir -p "/tmp/c d/data/backups"', out)

    def test_a_relative_path_is_made_absolute(self):
        # recipes `cd server` before reading the data folder
        self.assertIn(f'mkdir -p "{ROOT}/rel/data/backups"', self.planned("rel/data"))


if __name__ == "__main__":
    unittest.main()
