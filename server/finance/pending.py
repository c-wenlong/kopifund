"""G25: the phone's rows into the record (SPEC.md, "Phone link (G25)").

An approved alert on the phone posts to `POST /api/link/transactions` and
lands in `pending_txn` keyed by its `import_id`, so a retry upserts and never
doubles. The row waits there until a load brings the statement row that is the
same spend: `confirm` pairs them on amount, currency and date with a payee that
reads alike after normalising (the same rule the iOS Dedup uses), one record
row to one pending row, never re-offering a pair the owner put back. The row
counts as spending nowhere: the real row does the counting once it lands.

A waiting row is dismissed from the web app's Settings card; a wrong match is
restored to waiting, which also records the pair on the row's `rejected` list
so it is never offered again.
"""
import json
import re
from datetime import date

from . import db
from .errors import NotFound, Refused

STATES = ("waiting", "matched", "dismissed")
# how much of a post's detail the table keeps out of the API answer
MAX_IMPORT_ID = 200


def _row(d):
    """One posted row checked, or ValueError naming the problem."""
    if not isinstance(d, dict):
        raise ValueError("each row is an object")
    import_id = d.get("import_id")
    if not isinstance(import_id, str) or not import_id.strip() or len(import_id) > MAX_IMPORT_ID:
        raise ValueError("import_id is text, 1 to 200 characters")
    when = d.get("date")
    if not isinstance(when, str):
        raise ValueError("date is text, YYYY-MM-DD")
    try:
        # store the parsed form: "20261015" or a week date would slip past
        # fromisoformat, then SQLite's date() never sees them as that day
        when = date.fromisoformat(when).isoformat()
    except ValueError:
        raise ValueError("date is YYYY-MM-DD")
    amount = d.get("amount_minor")
    if not isinstance(amount, int) or isinstance(amount, bool):
        raise ValueError("amount_minor is integer cents")
    currency = d.get("currency")
    if not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency):
        raise ValueError("currency is a three-letter code")
    payee = d.get("payee")
    if payee is not None and not isinstance(payee, str):
        raise ValueError("payee is text")
    return {"import_id": import_id.strip(), "date": when, "amount_minor": amount,
            "currency": currency, "payee": payee or ""}


def post_rows(conn, device_id, rows):
    """Upsert the phone's rows and answer {"accepted": n}: how many were new or
    corrected. A row that arrives again keeps its one record row (`import_id`
    is unique); a retry of a row still waiting updates its fields, a row
    already matched or dismissed is left alone. New rows confirm against the
    record as it stands, so one whose statement loaded first matches at once."""
    if not isinstance(rows, list):
        raise ValueError("rows is a list")
    accepted = 0
    for d in rows:
        r = _row(d)
        with db.transaction(conn):
            cur = conn.execute(
                """INSERT INTO pending_txn(import_id, device_id, txn_date, amount_minor, currency,
                                         payee, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(import_id) DO UPDATE SET
                       txn_date = excluded.txn_date, amount_minor = excluded.amount_minor,
                       currency = excluded.currency, payee = excluded.payee
                   WHERE pending_txn.state = 'waiting'""",
                (r["import_id"], device_id, r["date"], r["amount_minor"], r["currency"], r["payee"],
                 db.now()))
            accepted += cur.rowcount > 0
    matched = confirm(conn)
    return {"accepted": accepted, "matched": matched}


# ---------------------------------------------------------------- matching

def _norm(s):
    """Case and punctuation out, letters and digits kept: the same normalising
    the iOS Dedup uses (apps/ios/Packages/Matching, Dedup.normalizedPayee)."""
    return "".join(ch for ch in s.lower() if ch.isalnum())


def _similar(a, b):
    """Equal after normalising, or one name inside the other with the short
    side a few letters at least ("AMAZON SG" in "Amazon.sg Marketplace")."""
    x, y = _norm(a), _norm(b)
    if not x or not y:
        return False
    if x == y:
        return True
    short, long = (x, y) if len(x) <= len(y) else (y, x)
    return len(short) >= 4 and short in long


def _current_keys(conn, key):
    """A txn_key plus the keys it is now known by (txn_key_alias follows a
    re-key), both ways: for a claimed or refused pair to hold across loads."""
    keys = {key}
    keys.update(r[0] for r in conn.execute(
        "SELECT t.txn_key FROM txn t JOIN txn_key_alias a ON a.txn_id = t.id WHERE a.old_key = ?",
        (key,)))
    keys.update(r[0] for r in conn.execute(
        "SELECT a.old_key FROM txn_key_alias a JOIN txn t ON t.id = a.txn_id WHERE t.txn_key = ?",
        (key,)))
    return keys


def confirm(conn):
    """Pair every waiting row with the record row that is the same spend.
    Returns the count newly matched.

    A candidate must agree on amount, currency and date exactly and read like
    the payee or the name its statement line carries — the raw merchant, and
    for rows that name only their recipient (a PayNow To:) the counterparty,
    memo or bank line behind it — after normalising; two real purchases on
    one day at one amount exist, so money alone is not enough. One record row
    takes at most one pending row (claimed), a pair the owner refused is never
    offered again (rejected), and a made-up row is no spend (is_synthetic).

    The read and the write sit in one immediate transaction, so a dismiss or
    restore arriving meanwhile can never be overwritten by a match decided
    from before it."""
    matched = 0
    with db.transaction(conn):
        waiting = conn.execute(
            "SELECT * FROM pending_txn WHERE state = 'waiting' ORDER BY id").fetchall()
        if not waiting:
            return 0
        claimed = set()
        for r in conn.execute("SELECT matched_txn_key FROM pending_txn "
                              "WHERE state = 'matched' AND matched_txn_key IS NOT NULL"):
            claimed.update(_current_keys(conn, r[0]))
        for p in waiting:
            rejected = set()
            for key in json.loads(p["rejected"] or "[]"):
                rejected.update(_current_keys(conn, key))
            candidates = conn.execute(
                """SELECT txn_key, payee_name, merchant_raw, counterparty, memo, bank_txn_type
                   FROM v_txn
                   WHERE txn_date = ? AND amount_minor = ? AND currency = ? AND is_synthetic = 0
                   ORDER BY txn_key""",
                (p["txn_date"], p["amount_minor"], p["currency"])).fetchall()
            for c in candidates:
                if c["txn_key"] in claimed or c["txn_key"] in rejected:
                    continue
                printed = c["merchant_raw"] or c["counterparty"] or c["memo"] or c["bank_txn_type"]
                if not (_similar(p["payee"], c["payee_name"] or "") or
                        _similar(p["payee"], printed)):
                    continue
                cur = conn.execute(
                    "UPDATE pending_txn SET state = 'matched', matched_txn_key = ? "
                    "WHERE id = ? AND state = 'waiting'", (c["txn_key"], p["id"]))
                if cur.rowcount:
                    claimed.update(_current_keys(conn, c["txn_key"]))
                    matched += 1
                    break
    return matched


# ---------------------------------------------------------------- reads

def waiting_count(conn):
    return conn.execute("SELECT COUNT(*) FROM pending_txn WHERE state = 'waiting'").fetchone()[0]


def rows(conn, device_names=None):
    """Every pending row as the API sends it, waiting first then matched then
    dismissed, newest of each first. `device` names the paired phone the row
    came from, "" when its registry row is gone."""
    names = device_names if device_names is not None else {}
    out = []
    order = {"waiting": 0, "matched": 1, "dismissed": 2}
    found = conn.execute("SELECT * FROM pending_txn").fetchall()
    found.sort(key=lambda r: (order.get(r["state"], 3), -r["id"]))
    for r in found:
        out.append({"import_id": r["import_id"], "device_id": r["device_id"],
                    "device": names.get(r["device_id"], ""), "date": r["txn_date"],
                    "amount_minor": r["amount_minor"], "currency": r["currency"], "payee": r["payee"],
                    "state": r["state"], "matched_txn_key": r["matched_txn_key"]})
    return out


# ---------------------------------------------------------------- the owner's calls

def dismiss(conn, import_id):
    """A waiting row the owner says is no spend: it never matches. NotFound
    when no row has the id, Refused when it is not waiting."""
    with db.transaction(conn):
        row = conn.execute("SELECT state FROM pending_txn WHERE import_id = ?", (import_id,)).fetchone()
        if row is None:
            raise NotFound(f"no pending row {import_id}")
        if row["state"] != "waiting":
            raise Refused("Only a waiting row can be dismissed")
        conn.execute("UPDATE pending_txn SET state = 'dismissed' "
                     "WHERE import_id = ? AND state = 'waiting'", (import_id,))


def dismiss_all(conn):
    """Dismiss every waiting row; the number dismissed. Rows never expire on
    their own (an alert's spend may wait weeks for its statement), so the
    owner clears the ones no statement will confirm here, and Put back
    undoes any one of them."""
    with db.transaction(conn):
        return conn.execute("UPDATE pending_txn SET state = 'dismissed' WHERE state = 'waiting'").rowcount


def restore(conn, import_id):
    """Put a matched or dismissed row back to waiting. A row that was matched
    is also given the row it matched on its `rejected` list: the owner said it
    was the wrong row, so the pair is never offered again."""
    with db.transaction(conn):
        row = conn.execute("SELECT state, matched_txn_key, rejected FROM pending_txn "
                           "WHERE import_id = ?", (import_id,)).fetchone()
        if row is None:
            raise NotFound(f"no pending row {import_id}")
        if row["state"] == "waiting":
            raise Refused("The row is already waiting")
        rejected = json.loads(row["rejected"] or "[]")
        if row["state"] == "matched" and row["matched_txn_key"]:
            rejected = sorted({*rejected, row["matched_txn_key"]})
        conn.execute("""UPDATE pending_txn SET state = 'waiting', matched_txn_key = NULL, rejected = ?
                        WHERE import_id = ? AND state != 'waiting'""",
                     (json.dumps(rejected), import_id))
