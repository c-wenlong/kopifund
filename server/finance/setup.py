"""First run for a new user (G10): a data folder, a config and an empty record.

Nothing here knows about any one person. The user picks the providers they
hold; each becomes a statement folder with its parser, and the shipped
category list seeds the record. Notion is off until the user configures it.
"""
import json
import os
from pathlib import Path

from . import db
from .migrate import to_toml
from .parsers import layouts
from .paths import SERVER, config_path, data_root


def _offered():
    """(provider, its layout) for every statement folder a new user can pick:
    one per layout, one per currency for a currency wallet (G21: read from
    server/finance/parsers/layouts.py)."""
    for lay in layouts.every():
        for ccy in lay.currencies or ("",):
            p = {"id": lay.folder.format(ccy=ccy.lower()), "label": lay.setup.format(ccy=ccy),
                 "parser": lay.parser, "bank": lay.bank or "other", "title": lay.folder_title,
                 "status": lay.status}
            if ccy:
                p["wallet"] = ccy
            if lay.multi_currency:
                p["multi_currency"] = True
            yield p, lay


def providers():
    """Every statement layout Kopifund can parse, as a folder a new user can pick."""
    return [p for p, _ in _offered()]


class SetupRefused(Exception):
    pass


def needed():
    """True when there is no config in the data folder yet."""
    return not config_path().exists()


def create(provider_ids, root=None):
    """Make the data folder for the chosen providers. Refuses to touch a data
    folder that already has a config, so it can never overwrite anyone's setup."""
    root = Path(root) if root else data_root()
    if (root / "config.toml").exists():
        raise SetupRefused(f"{root} already has a config.toml; nothing was changed")
    offered = list(_offered())
    chosen = [(p, lay) for p, lay in offered if p["id"] in set(provider_ids or ())]
    if not chosen:
        raise SetupRefused("Choose at least one bank, wallet or statement")
    _private(root)
    for sub in ("statements", "documents", "rules", "notes", "exports", "backups", "cache"):
        _private(root / sub)
    # a layout with no rows (Tiger, CPF) keeps its folder in [analytics], off
    # until it is chosen
    folders = []
    analytics = {lay.config_key: "" for _, lay in offered if lay.csv is None}
    for p, lay in chosen:
        _private(root / "statements" / p["id"])
        if lay.csv is None:
            analytics[lay.config_key] = f"statements/{p['id']}"
            continue
        entry = {"path": f"statements/{p['id']}", "parser": p["parser"], "account_no": ""}
        if p.get("wallet"):
            entry["wallet"] = p["wallet"]
        if p.get("multi_currency"):
            entry["multi_currency"] = True
        folders.append(entry)
    config = {"notion": {"token_env": "NOTION_TOKEN", "token_command": ""},
              "analytics": analytics, "folder": folders}
    text = ("# Kopifund settings, written at setup. Folder paths are relative to the\n"
            "# data folder. Notion is off: add [data_sources] and a token to mirror.\n\n"
            + to_toml(config))
    (root / "config.toml").write_text(text, encoding="utf-8")
    os.chmod(root / "config.toml", 0o600)
    conn = db.connect(root / "kopifund.db")
    shipped = json.loads((SERVER / "analytics" / "categories.json").read_text(encoding="utf-8"))
    db.import_user_tables(conn, {"categories": shipped["categories"]}, plan={})
    conn.close()
    return {"data_folder": str(root), "folders": [p["id"] for p, _ in chosen],
            "categories": len(shipped["categories"])}


def _private(d):
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
