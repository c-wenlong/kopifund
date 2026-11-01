"""Facts about the owner that no statement prints, kept in the data folder.

Today that is the date of birth, which a CPF projection needs for the age
bands that set contribution and allocation rates and for the milestones at 55
and 65. It lives in `profile.json` beside the config, never in the repo and
never in the record, so the record's schema does not change for it.
"""
import json
import os
import tempfile
from datetime import date

from .paths import data_root

FIELDS = ("birth_date",)


class ProfileRefused(Exception):
    pass


def path():
    return data_root() / "profile.json"


def _read():
    """(fields, problem). A file that is not a JSON object, or holds a value
    that does not check, is reported rather than trusted; its bad fields read
    as None so the page still opens."""
    try:
        text = path().read_text(encoding="utf-8")
    except FileNotFoundError:
        return {k: None for k in FIELDS}, None
    try:
        raw = json.loads(text)
    except ValueError:
        raw = None
    if not isinstance(raw, dict):
        return {k: None for k in FIELDS}, f"{path().name} is damaged (not a JSON object); saving replaces it and keeps a copy"
    out, bad = {}, []
    for k in FIELDS:
        try:
            out[k] = _birth_date(raw.get(k)) if k == "birth_date" else raw.get(k)
        except ProfileRefused:
            out[k], _ = None, bad.append(k)
    problem = f"{path().name} holds an invalid {', '.join(bad)}; saving replaces it" if bad else None
    return out, problem


def load():
    """Every field, None where unset or invalid, plus `problem` when the file is damaged."""
    fields, problem = _read()
    return {**fields, "problem": problem}


def _birth_date(v, today=None):
    if v in (None, ""):
        return None
    try:
        d = date.fromisoformat(str(v))
    except ValueError as e:
        raise ProfileRefused("The date of birth is not a real date") from e
    today = today or date.today()
    try:
        oldest = today.replace(year=today.year - 120)
    except ValueError:                     # 29 February
        oldest = today.replace(year=today.year - 120, day=28)
    if d >= today or d < oldest:
        raise ProfileRefused("The date of birth must be a real past date, within 120 years")
    return d.isoformat()


def save(updates, today=None):
    """Merge `updates` into the profile after checking them; returns the profile.
    The file is written whole to a temp file and swapped in, readable by the
    owner only."""
    if not isinstance(updates, dict):
        raise ProfileRefused("The profile is an object")
    unknown = set(updates) - set(FIELDS)
    if unknown:
        raise ProfileRefused(f"Unknown profile field: {', '.join(sorted(unknown))}")
    current, problem = _read()
    if problem and path().exists():
        # an explicit save replaces a damaged file; the old bytes stay beside it
        copy = path().with_suffix(".json.damaged")
        fd = os.open(copy, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(path().read_bytes())
        os.chmod(copy, 0o600)              # O_CREAT's mode does not apply to a file already there
    if "birth_date" in updates:
        current["birth_date"] = _birth_date(updates["birth_date"], today)
    target = path()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".profile-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(current, f, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, target)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return {**current, "problem": None}


# ---------------------------------------------------------------- accounts

def _folder_groups(folders, listing):
    """{upload key: {name, bank, numbers, pdfs}} for the statement folders the app
    lists, wallets of one account folded together. `numbers` are the account
    numbers the config ties to those folders, never sent to a page."""
    from . import account_names as names
    numbers = {str(names._get(f, "path")): str(names._get(f, "account_no") or "") for f in folders or ()}
    groups = {}
    for f in listing or ():
        g = groups.setdefault(names.folder_key(f), {"name": names.folder_name(f), "bank": names.folder_bank(f),
                                                   "numbers": set(), "pdfs": 0})
        g["pdfs"] += int(f.get("statements") or 0)
        if numbers.get(str(f.get("path"))):
            g["numbers"].add(numbers[str(f.get("path"))])
    return groups


def accounts(conn, folders=(), listing=()):
    """Every account in the record for Your profile, named and marked the way
    the app shows an account (server/finance/account_names.py, G13): a display name,
    the bank key for its logo and the number masked to its last four digits,
    with its statement files, its first and last date and its rows.

    The dates run from the earliest row or statement to the latest of either.
    A brokerage or CPF account prints no ledger rows; its statements are the
    brokerage's and CPF's own. An account with no statement and no row is
    still waiting for its first statement.

    `listing` is the app's statement folders with their PDF counts
    ({path, parser, wallet, statements}); an account that one of them belongs
    to carries its upload key (`folder`) and the PDFs in it (`pdfs`), found by
    the account number the config ties to the folder, else by its bank when
    that is the only account and the only folder of the bank. A folder no
    account claims stays in the listing."""
    from . import account_names
    rows = [dict(r) for r in conn.execute("""
        SELECT a.id, a.name, a.account_no, a.institution,
               (SELECT COUNT(*) FROM v_txn v WHERE v.account_id = a.id) AS n,
               (SELECT MIN(txn_date) FROM v_txn v WHERE v.account_id = a.id) AS lo,
               (SELECT MAX(txn_date) FROM v_txn v WHERE v.account_id = a.id) AS hi,
               (SELECT COUNT(DISTINCT source) FROM period p
                 WHERE p.account_id = a.id AND p.archived_at IS NULL AND p.source != '') AS files,
               (SELECT MIN(period_end) FROM period p
                 WHERE p.account_id = a.id AND p.archived_at IS NULL) AS p_lo,
               (SELECT MAX(period_end) FROM period p
                 WHERE p.account_id = a.id AND p.archived_at IS NULL) AS p_hi
        FROM account a""")]
    broker = conn.execute("SELECT COUNT(*), MIN(period_from), MAX(period_to) FROM broker_statement").fetchone()
    cpf = conn.execute("SELECT COUNT(*), MIN(year), MAX(as_of) FROM cpf_statement").fetchone()
    described = account_names.describe(rows, folders)
    groups = _folder_groups(folders, listing)
    claimed = {}
    for i, r in enumerate(rows):
        no = str(r["account_no"] or "")
        key = next((k for k, g in groups.items() if no and no in g["numbers"]), None)
        if key:
            claimed[i] = key
    taken = set(claimed.values())
    for i, d in enumerate(described):
        if i in claimed:
            continue
        keys = [k for k, g in groups.items() if g["bank"] == d["bank"] and k not in taken]
        alike = [j for j, e in enumerate(described) if j not in claimed and e["bank"] == d["bank"]]
        if len(keys) == 1 and len(alike) == 1:
            claimed[i] = keys[0]
    out = []
    for i, (r, d) in enumerate(zip(rows, described)):
        files = r["files"]
        dates = [x for x in (r["lo"], r["hi"], r["p_lo"], r["p_hi"]) if x]
        first, last = (min(dates), max(dates)) if dates else (None, None)
        if not files and not r["n"] and d["bank"] == "tiger" and broker[0]:
            files, first, last = broker[0], broker[1], broker[2]
        if not files and not r["n"] and d["bank"] == "cpf" and cpf[0]:
            files, first, last = cpf[0], f"{cpf[1]}-01-01", cpf[2]
        key = claimed.get(i)
        out.append({"name": d["name"], "bank": d["bank"], "number": d["number"],
                    "statements": files or 0, "transactions": r["n"],
                    "first": first, "last": last,
                    "folder": key, "pdfs": groups[key]["pdfs"] if key else None})
    # A brokerage or CPF whose statements are loaded has no account row when nothing ties it to one (no Notion
    # mirror): it still gets its line here, named by its statement folder, so the page does not call a loaded
    # folder "not loaded yet".
    taken = {a["folder"] for a in out if a["folder"]}
    loaded = {"tiger": (broker[0], broker[1], broker[2]),
              "cpf": (cpf[0], f"{cpf[1]}-01-01" if cpf[1] else None, cpf[2])}
    for bank, (count, first, last) in loaded.items():
        keys = [k for k, g in groups.items() if g["bank"] == bank and k not in taken]
        if count and len(keys) == 1 and not any(a["bank"] == bank for a in out):
            out.append({"name": groups[keys[0]]["name"], "bank": bank, "number": "", "statements": count,
                        "transactions": 0, "first": first, "last": last,
                        "folder": keys[0], "pdfs": groups[keys[0]]["pdfs"]})
    return sorted(out, key=lambda a: (-a["transactions"], -a["statements"], a["name"]))
