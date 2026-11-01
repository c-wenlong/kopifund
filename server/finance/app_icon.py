"""The app icon the owner picked, one of the sets in app-icons/ (SPEC.md, "App icon").

app-icons/sets.json lists the sets in the order the picker shows them and
names the default; the pick is the record's `app_icon` setting, and a pick
of a set sets.json no longer lists reads as the default. The web app swaps
its favicon and logo to the pick. In the desktop app the server prints
`KOPIFUND_ICON <name>` as it starts and after every pick (serve.py --app),
and the shell makes that set its Dock icon.
"""
import json
from collections.abc import Callable

from .db import transaction
from .errors import Refused
from .paths import REPO

REGISTRY = REPO / "app-icons" / "sets.json"
KEY = "app_icon"

_announce: Callable[[str], None] | None = None


def announce_with(announce: Callable[[str], None] | None) -> None:
    """Who hears of a pick: serve.py --app prints it for the desktop shell."""
    global _announce
    _announce = announce


def registry() -> dict:
    return json.loads(REGISTRY.read_text())


def chosen(conn) -> str:
    """The set the app shows: the saved pick, else sets.json's default. No record yet is no pick."""
    reg = registry()
    row = conn.execute("SELECT value FROM setting WHERE key = ?", (KEY,)).fetchone() if conn is not None else None
    name = json.loads(row["value"]) if row else None
    return name if name in {s["name"] for s in reg["sets"]} else reg["default"]


def page(conn) -> dict:
    reg = registry()
    return {"sets": reg["sets"], "default": reg["default"], "chosen": chosen(conn)}


def choose(conn, name) -> dict:
    names = [s["name"] for s in registry()["sets"]]
    if name not in names:
        raise Refused(f"Not an app icon: {name!r}. The sets are {', '.join(names)}.")
    with transaction(conn):
        conn.execute("INSERT INTO setting(key, value) VALUES (?, ?) "
                     "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (KEY, json.dumps(name)))
    if _announce is not None:
        _announce(name)
    return page(conn)
