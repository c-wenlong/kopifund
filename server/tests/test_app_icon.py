"""The app icon picked in Settings (finance/app_icon.py), and the icon sets'
copies in the web, iOS and desktop apps, which app-icons/apply.py writes from
app-icons/sets.json (SPEC.md, "App icon")."""
import isolate  # noqa: F401  first: no test writes the real record or cache
import importlib.util
import json
import os
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

import serve
import support
from finance import app_icon, db
from finance.errors import Refused
from finance.paths import REPO

SPEC = importlib.util.spec_from_file_location("apply", REPO / "app-icons" / "apply.py")
apply = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(apply)

REG = json.loads((REPO / "app-icons" / "sets.json").read_text())
NAMES = [s["name"] for s in REG["sets"]]
OTHER = next(n for n in NAMES if n != REG["default"])


class PickTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        env = mock.patch.dict(os.environ, {"KOPIFUND_DATA": str(root), "KOPIFUND_DB": str(root / "kopifund.db"),
                                           "KOPIFUND_CACHE": str(root / "cache")})
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(app_icon.announce_with, None)

    def pick(self, name):
        return support.write("/api/app-icon", {}, json.dumps({"name": name}).encode())

    def test_before_any_pick_the_default_shows_and_no_record_is_started(self):
        page = support.answer("/api/app-icon", {})
        self.assertEqual(page, {"sets": REG["sets"], "default": REG["default"], "chosen": REG["default"]})
        self.assertFalse(db.db_path().exists())
        self.assertEqual(serve.picked_icon(), REG["default"])

    def test_a_pick_is_kept_in_the_record(self):
        self.assertEqual(self.pick(OTHER)["chosen"], OTHER)
        self.assertEqual(support.answer("/api/app-icon", {})["chosen"], OTHER)
        self.assertEqual(serve.picked_icon(), OTHER)

    def test_a_set_sets_json_does_not_list_is_refused(self):
        for name in ("nope", "", None, "../sticker"):
            with self.subTest(name=name), self.assertRaises(Refused):
                self.pick(name)

    def test_a_pick_of_a_set_since_removed_reads_as_the_default(self):
        self.pick(OTHER)
        with closing(db.connect()) as conn, conn:
            conn.execute("UPDATE setting SET value = ? WHERE key = ?", (json.dumps("gone"), app_icon.KEY))
        self.assertEqual(support.answer("/api/app-icon", {})["chosen"], REG["default"])

    def test_the_desktop_shell_hears_each_pick(self):
        heard = []
        app_icon.announce_with(heard.append)
        self.pick(OTHER)
        self.pick(REG["default"])
        self.assertEqual(heard, [OTHER, REG["default"]])


class ShippedTest(unittest.TestCase):
    """The apps hold what sets.json names; after editing it or a set, run `make app-icon`."""

    def same(self, a: Path, b: Path):
        self.assertTrue(b.is_file(), f"{b.relative_to(REPO)} is missing: run make app-icon")
        self.assertEqual(a.read_bytes(), b.read_bytes(), f"{b.relative_to(REPO)} is stale: run make app-icon")

    def test_the_default_is_what_each_app_opens_with(self):
        src = REPO / "app-icons" / REG["default"]
        for name in ("favicon.ico", "favicon.svg", "apple-touch-icon.png", "icon-192.png", "icon-512.png",
                     "icon-maskable-512.png"):
            self.same(src / name, apply.WEB / name)
        self.same(src / "AppIcon-1024.png", apply.CATALOG / "AppIcon.appiconset" / "AppIcon.png")
        self.same(src / "icon-512.png", apply.DESKTOP / "ui" / "icon.png")
        self.assertIn(f'static let primary = "{REG["default"]}"', (apply.IOS / "AppIcons.swift").read_text())

    def test_every_set_is_where_a_picker_switches_to_it(self):
        self.assertEqual((apply.IOS / "AppIcons.swift").read_text(), apply.swift_list(REG))
        for name in NAMES:
            with self.subTest(name=name):
                src = REPO / "app-icons" / name
                for file in apply.WEB_SET:
                    self.same(src / file, apply.WEB / "app-icons" / name / file)
                self.assertEqual((apply.WEB / "app-icons" / name / "logo.svg").read_text(), apply.web_logo(src))
                self.same(src / "AppIcon-1024.png", apply.CATALOG / f"AppIcon-{name}.appiconset" / "AppIcon.png")
                for n in (120, 180):
                    self.same(src / f"icon-picker-{n}.png",
                              apply.CATALOG / f"IconPicker-{name}.imageset" / f"icon-picker-{n}.png")
                self.assertTrue((apply.DESKTOP / "src-tauri" / "icons" / "sets" / f"{name}.png").is_file())
        on_disk = sorted(p.name for p in (apply.WEB / "app-icons").iterdir())
        self.assertEqual(on_disk, sorted(NAMES))

    def test_a_set_with_a_mark_has_a_dark_and_a_tinted_look(self):
        for name in NAMES:
            folder = apply.CATALOG / f"AppIcon-{name}.appiconset"
            looks = {i.get("appearances", [{}])[0].get("value")
                     for i in json.loads((folder / "Contents.json").read_text())["images"]}
            marked = (REPO / "app-icons" / name / "mark-transparent.svg").is_file()
            with self.subTest(name=name):
                self.assertEqual(looks, {None, "dark", "tinted"} if marked else {None})


if __name__ == "__main__":
    unittest.main()
