"""Saves that reach the record at once (G13).

Saving a rule, a single row's category or a trip used to wait for the next
load, so the owner went to Upload and pressed Load to see it. Now the save
relinks the rows it touches straight away: each row's payee, category, rule
and trip are decided from the record's own columns by the same code a load
runs (categorise.categorise_rows for the rules, local.trip_names for the
trips, db.link_values and db.upsert_payees for the write), so a relink leaves
a row exactly as a full load with the same rules and trips would.

Which rows a save touches is decided by running every live row through the
rules (or the trips) as they were before the save and as they are after it:
only a row whose outcome differs is written, so a row the record holds
differently for another reason (a rules file changed by hand) is left for
Apply rules on the Rules page, which relinks every row.

Each relink answers with what it wrote, as `Change.data()`: the columns, each
written row's values before and after, and each payee row before and after.
`revert` puts those back, a row only while it still holds what the relink
wrote, so an Undo never takes back a later write.
"""
from . import db

# the record's columns a parser's row carries under another name
_FROM_RECORD = {"txn_date": "date", "merchant_raw": "merchant"}
_INPUTS = ("txn_key", "txn_date", "currency", "account_no", "bank_txn_type", "flow_class", "merchant_raw",
           "merchant_country", "counterparty", "memo", "reference", "source")
RULE_LINKS = ("payee_id", "category_id", "rule")
TRIP_LINKS = ("trip_id",)


def record_rows(conn):
    """The record's live rows in the shape the parsers give them (the fields
    the rules and the trip assignment read), each with its id and its links
    as stored now under `_id` and `_links`."""
    cols = ", ".join(("id", *_INPUTS, *db.LINKS))
    out = []
    for r in conn.execute(f"SELECT {cols} FROM txn WHERE archived_at IS NULL ORDER BY id"):
        row = {_FROM_RECORD.get(k, k): r[k] for k in _INPUTS}
        row["_id"] = r["id"]
        row["_links"] = {k: r[k] for k in db.LINKS}
        out.append(row)
    return out


def categorised(rows, rules):
    """(payee, category, rule) for each row, as a load's categorise step gives
    them (sync.categorise runs the same categorise_rows)."""
    from .parsers import categorise
    copies = [dict(r) for r in rows]
    categorise.categorise_rows(copies, rules)
    return [(c["payee"], c["category"], c["rule"]) for c in copies]


class Change:
    """What one relink wrote: rows as [txn_key, before, after] over `cols`,
    payees as [name, before or None (added), after]."""

    def __init__(self, cols):
        self.cols = tuple(cols)
        self.rows = []
        self.payees = []
        # the name of each payee and trip a row pointed at before, by "column:id": an id
        # can be given again to a new trip once the old one is gone, and a name says which it was
        self.names = {}

    def changed_keys(self):
        """The rows whose payee, category or trip moved, which is what the
        owner sees change: a rule's new pattern alone moves none."""
        shown = [i for i, c in enumerate(self.cols) if c != "rule"]
        return {key for key, before, after in self.rows if any(before[i] != after[i] for i in shown)}

    def changed(self):
        return len(self.changed_keys())

    def summary(self):
        return {"changed": self.changed(), "written": len(self.rows) + len(self.payees)}

    def data(self):
        return {"cols": list(self.cols), "rows": self.rows, "payees": self.payees, "names": self.names}


def _write(conn, change, row, want):
    have = [row["_links"][c] for c in change.cols]
    want = [want[c] for c in change.cols]
    if have == want:
        return
    conn.execute(f"UPDATE txn SET {', '.join(f'{c} = ?' for c in change.cols)} WHERE id = ?", (*want, row["_id"]))
    change.rows.append([row["txn_key"], have, want])
    for col, value in zip(change.cols, have):
        if col in _NAMED and value is not None and f"{col}:{value}" not in change.names:
            change.names[f"{col}:{value}"] = _name(conn, col, value)


def _payees(conn, change, payees):
    """Add or bring up to date every payee the rules name, as a load does,
    keeping what each one held before."""
    moved = []
    for p in payees:
        old = conn.execute("SELECT kind, country, recurring, account_no FROM payee WHERE name = ?",
                           (p["name"],)).fetchone()
        new = list(db.payee_values(p))
        if old is None or list(old) != new:
            change.payees.append([p["name"], None if old is None else list(old), new])
            moved.append(p)
    db.upsert_payees(conn, moved)


def rules(conn, before, after):
    """Relink the rows whose payee, category or rule the rules `after` decide
    differently from `before` (both as categorise.load_rules gives them);
    `before` None relinks every row the record holds differently from what
    `after` says (Apply rules). Every payee `after` names is added or brought
    up to date, as a load does. One transaction; returns the Change."""
    from . import snapshot as snap
    rows = record_rows(conn)
    new = categorised(rows, after)
    old = categorised(rows, before) if before is not None else None
    touched = [i for i in range(len(rows)) if old is None or old[i] != new[i]]
    change = Change(RULE_LINKS)
    with db.transaction(conn):
        _payees(conn, change, snap.payees_from_rules(after, []))
        category_of = db.category_ids(conn)
        missing = {new[i][1] for i in touched if new[i][1]} - set(category_of)
        if missing:
            # a category no longer shipped that a rule still names comes back, as a load brings it back
            db.seed_categories(conn, named=missing)
            category_of = db.category_ids(conn)
        for i in touched:
            payee, category, rule = new[i]
            _write(conn, change, rows[i],
                   db.link_values(conn, {"payee": payee, "category": category, "rule": rule}, category_of, None))
    return change


def trips(conn, cfg, before, after):
    """Relink the rows the trips `after` (local.trips_from_db) place
    differently from `before`; `before` None relinks every row the record
    holds differently from what `after` says. One transaction; returns the
    Change."""
    from . import local
    rows = record_rows(conn)
    new = local.trip_names(cfg, rows, after)
    ids_after = {t.name: int(t.page_id) for t in after}
    if before is None:
        touched = rows
    else:
        old = local.trip_names(cfg, rows, before)
        ids_before = {t.name: int(t.page_id) for t in before}
        touched = [r for r in rows
                   if ids_before.get(old.get(r["txn_key"])) != ids_after.get(new.get(r["txn_key"]))]
    change = Change(TRIP_LINKS)
    with db.transaction(conn):
        for r in touched:
            _write(conn, change, r, db.link_values(conn, {}, {}, new.get(r["txn_key"])))
    return change


_TABLE = {"payee_id": "payee", "category_id": "category", "trip_id": "trip"}
# the links whose target is checked by name before a revert points a row back
# at it; a category keeps its id through a rename, which a name would refuse
_NAMED = ("payee_id", "trip_id")


def _name(conn, col, value):
    row = conn.execute(f"SELECT name FROM {_TABLE[col]} WHERE id = ?", (value,)).fetchone()
    return row[0] if row else None


def revert(conn, data):
    """Put back what a relink wrote (`Change.data()`): each row that still
    holds what it wrote gets its old values again, unless one of them points
    at a payee, category or trip that is gone; each payee goes back the same
    way, and one the relink added goes once no row names it. A row changed
    since (a later save, a load that decided it otherwise) is left as it is.
    One transaction; returns {"changed": rows put back whose payee, category
    or trip moved, "written": rows and payees written}."""
    nothing = {"changed": 0, "written": 0}
    if not isinstance(data, dict) or not isinstance(data.get("rows"), list):
        return nothing
    cols = [c for c in data.get("cols") or [] if c in db.LINKS]
    if not cols or len(cols) != len(data.get("cols") or []):
        return nothing
    shown = [i for i, c in enumerate(cols) if c != "rule"]
    exists = {}

    names = data.get("names") if isinstance(data.get("names"), dict) else {}

    def present(col, value):
        """The payee, category or trip the row pointed at is still there, and,
        for a payee or a trip, still the same one: not a new one given its id."""
        if value is None or col not in _TABLE:
            return True
        key = (col, value)
        if key not in exists:
            held = _name(conn, col, value)
            want = names.get(f"{col}:{value}")
            exists[key] = held is not None and (col not in _NAMED or want is None or held == want)
        return exists[key]

    sets = ", ".join(f"{c} = ?" for c in cols)
    held = " AND ".join(f"{c} IS ?" for c in cols)
    n = written = 0
    with db.transaction(conn):
        for item in data["rows"]:
            key, before, after = item
            if not all(present(c, v) for c, v in zip(cols, before)):
                continue
            done = conn.execute(f"UPDATE txn SET {sets} WHERE txn_key = ? AND {held}",
                                (*before, key, *after)).rowcount
            written += done
            if done and any(before[i] != after[i] for i in shown):
                n += 1
        for name, before, after in data.get("payees") or []:
            if before is None:
                written += conn.execute("""DELETE FROM payee WHERE name = ? AND kind IS ? AND country IS ? AND recurring IS ?
                                AND account_no IS ? AND NOT EXISTS (SELECT 1 FROM txn WHERE payee_id = payee.id)""",
                             (name, *after)).rowcount
            else:
                written += conn.execute("""UPDATE payee SET kind = ?, country = ?, recurring = ?, account_no = ?
                                WHERE name = ? AND kind IS ? AND country IS ? AND recurring IS ? AND account_no IS ?""",
                             (*before, name, *after)).rowcount
    return {"changed": n, "written": written}
