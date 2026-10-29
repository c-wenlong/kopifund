import isolate  # noqa: F401  first: no test writes the real record or cache
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
DESKTOP = ROOT / "apps" / "desktop"


class DesktopConfigTest(unittest.TestCase):
    """G23: the desktop shell's settings that the Rust compiler cannot check,
    read here so a change to them fails on any machine, CI included."""

    def setUp(self):
        self.conf = json.loads((DESKTOP / "src-tauri" / "tauri.conf.json").read_text())
        self.main = next(w for w in self.conf["app"]["windows"] if w["label"] == "main")

    def test_drops_reach_the_page(self):
        # Tauri's own drop handler (on by default) takes every drop before WebKit
        # sees it: a PDF dropped on Upload, and an in-page drag such as the
        # Categories reorder, would never reach the page
        self.assertIs(self.main.get("dragDropEnabled"), False)

    def test_main_rs_builds_the_window(self):
        # the shell builds the window itself so it can refuse navigations
        # (main.rs, `navigation`); a config window would be built without that guard
        self.assertIs(self.main.get("create"), False)
        self.assertIn("on_navigation", (DESKTOP / "src-tauri" / "src" / "main.rs").read_text())

    def test_the_window_saves_downloads(self):
        # WebKit cancels a download when the window has no download handler,
        # so Settings' export links would do nothing (main.rs, `on_download`)
        self.assertIn(".on_download(", (DESKTOP / "src-tauri" / "src" / "main.rs").read_text())


if __name__ == "__main__":
    unittest.main()
