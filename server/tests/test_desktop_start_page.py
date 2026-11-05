import isolate  # noqa: F401  first: no test writes the real record or cache
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
START_PAGE = ROOT / "apps" / "desktop" / "ui" / "index.html"
TOKENS = ROOT / "apps" / "web" / "src" / "index.css"
SHARED = ("--page", "--ink", "--ink-2", "--line")


def tokens(css: str) -> tuple[dict[str, str], dict[str, str]]:
    """The light :root block and the dark one under prefers-color-scheme."""
    dark_at = css.index("@media (prefers-color-scheme: dark)")
    def block(text: str) -> dict[str, str]:
        body = text[text.index(":root") :]
        body = body[: body.index("}")]
        return {k: v.strip() for k, v in re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", body)}
    return block(css[:dark_at]), block(css[dark_at:])


class StartPageColoursTest(unittest.TestCase):
    """G23: the desktop start page shows until the app loads, so its colours
    are index.css's tokens, or the switch to the app flashes."""

    def test_the_start_page_uses_the_app_tokens(self):
        page = START_PAGE.read_text()
        css = page[page.index("<style>") : page.index("</style>")]
        app_light, app_dark = tokens(TOKENS.read_text())
        page_light, page_dark = tokens(css)
        for name in SHARED:
            with self.subTest(name=name):
                self.assertEqual(page_light.get(name), app_light[name], f"light {name}")
                self.assertEqual(page_dark.get(name), app_dark[name], f"dark {name}")


if __name__ == "__main__":
    unittest.main()
