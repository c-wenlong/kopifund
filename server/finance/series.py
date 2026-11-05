"""G16: the owner's edits to a recurring series, kept in the record
(series_override and series_override_txn in kopifund.db).

A series is what the recurring detector finds in the charges. On its view on
Recurring the owner can say it is not recurring, or set its usual amount, how
often it charges, its day and the day it ended; server/finance/recurring.py reads
these over what was detected, everywhere the series shows. Payee and category
stay in the rules.

Which series an edit is for. The detector's id for a series is its group and
the cents of its first charge (recurring_detector.partition), so it changes
when an older statement brings an earlier charge at another price, or when the
amount levels are cut differently. An edit is therefore saved with three
things: the id the series had, its group, and the keys of the series' own
payments at the time. On every read an edit goes to the series of its group
with that id; failing one, to the series of its group that holds most of
those payments (a key the parser has since changed is followed through
txn_key_alias), and failing that to the series of any group of its kind that
does (a re-key that changed the row's text moves it to another group);
failing that, to nothing, and it waits. Ids are matched
before payments, so two edits never take one series. A price more than double
the old one is a new series to the detector (the old one stops): the edit
stays with the series that holds its payments, the old one. Saving again
writes the edit under the id the series has now.

A payee's own line (`series_id` NULL) is Not recurring on a payee the rules
mark recurring: the stat reads the payee as unmarked (db.snapshot), it leaves
Marked recurring, no regular spacing, and every series of it is hidden unless
the series' own edit says it is recurring. The page still finds the payee's
series as the rules mark it (server/finance/recurring.py, _live), so a series only the
mark finds (a yearly bill seen twice) can be brought back that way. A rule's recurring tickbox cannot clear a shipped "yes" (the
categoriser takes any yes), so this is the one place that can.
"""
from datetime import date

from . import db

# the detector's groups (recurring_detector.group_key): a payee, a row's bank text when it has
# no payee, and the rounded amount a result from before G16 grouped such rows by
GROUP_KINDS = ("payee", "text", "amount")
EDITS = ("recurring", "amount", "cadence", "day", "ended_on")
DAY_CADENCES = ("weekly", "fortnightly")     # whose day is a weekday, 1 Monday to 7 Sunday
NO_DAY = ("twice-monthly",)                  # two fixed days, which one day cannot set


class SeriesRefused(ValueError):
    """An edit the record cannot take: an unknown series, an amount that is not
    whole positive cents, a cadence or day outside its list, a date that is not one."""


def _keys(conn):
    """{override id: {txn key}}, a key the parser has since changed read as the row's key now."""
    out = {}
    for oid, key in conn.execute("""SELECT o.override_id, COALESCE(t.txn_key, o.txn_key) FROM series_override_txn o
                                    LEFT JOIN txn_key_alias a ON a.old_key = o.txn_key
                                    LEFT JOIN txn t ON t.id = a.txn_id"""):
        out.setdefault(oid, set()).add(key)
    return out


def read(conn):
    """Every saved edit: {row, series_id, kind, group, recurring (True, False or None),
    amount (cents or None), cadence, day, ended_on, keys}."""
    keys = _keys(conn)
    return [{"row": r["id"], "series_id": r["series_id"], "kind": r["group_kind"], "group": r["group_name"],
             "recurring": None if r["recurring"] is None else bool(r["recurring"]),
             "amount": r["amount_minor"], "cadence": r["cadence"], "day": r["day"], "ended_on": r["ended_on"],
             "keys": keys.get(r["id"], set())}
            for r in conn.execute("SELECT * FROM series_override ORDER BY id")]


def payees_off(edits):
    """The payees whose own line says not recurring."""
    return {e["group"] for e in edits if e["series_id"] is None and e["kind"] == "payee" and e["recurring"] is False}


def payees_off_in(conn):
    """payees_off, read straight from the record (the snapshot the stats read)."""
    return {r[0] for r in conn.execute("""SELECT group_name FROM series_override WHERE series_id IS NULL
                                          AND group_kind = 'payee' AND recurring = 0""")}


def public(e):
    """What the page is told of an edit: each of EDITS, None where it is as detected."""
    return {k: (e or {}).get(k) for k in EDITS}


def resolve(edits, groups, ident, keys_of):
    """{id(series): edit} for the series each edit is for (the module's docstring).
    `groups` is {(kind, group): [series]}; `ident(s)` a series' id; `keys_of(s)`
    the keys of its own payments, asked only when an id does not match."""
    out, done = {}, set()
    mine = [e for e in edits if e["series_id"]]
    for e in mine:
        for s in groups.get((e["kind"], e["group"]), ()):
            if ident(s) == e["series_id"] and id(s) not in out:
                out[id(s)] = e
                done.add(e["row"])
                break
    # the latest saved first: of two edits holding one series' payments, the newer is the owner's word
    for e in sorted((e for e in mine if e["row"] not in done and e["keys"]), key=lambda e: -e["row"]):
        best = _most_keys(e, groups.get((e["kind"], e["group"]), ()), out, keys_of)
        if best is None:
            # a re-key that changed the row's text (a town split off the
            # merchant) moves a text series to another group: its payments,
            # followed through txn_key_alias, still say which series it is
            best = _most_keys(e, (s for (kind, _), ss in groups.items() if kind == e["kind"] for s in ss),
                              out, keys_of)
        if best is not None:
            out[id(best)] = e
    return out


def _most_keys(e, candidates, taken, keys_of):
    """The series among `candidates`, not already taken, that holds most of
    the edit's payments, or None."""
    best = None
    for s in candidates:
        if id(s) in taken:
            continue
        n = len(e["keys"] & keys_of(s))
        if n and (best is None or n > best[0]):
            best = (n, s)
    return best and best[1]


def _whole_cents(x):
    return isinstance(x, int) and not isinstance(x, bool)


def clean(body, cadences, detected_cadence):
    """The edit `body` asks for, checked: {recurring, amount, cadence, day, ended_on},
    each None for as detected. `cadences` are the ones the detector knows; the day
    is checked against the cadence the series will have."""
    unknown = set(body) - {"id", *EDITS}
    if unknown:
        raise SeriesRefused(f"Not something a series keeps: {', '.join(sorted(unknown))}")
    out = {k: body.get(k) for k in EDITS}
    if out["recurring"] is not None and not isinstance(out["recurring"], bool):
        raise SeriesRefused("Recurring is true, false or null")
    if out["amount"] is not None and not (_whole_cents(out["amount"]) and out["amount"] > 0):
        raise SeriesRefused("The usual amount is whole cents, more than zero")
    if out["cadence"] is not None and out["cadence"] not in cadences:
        raise SeriesRefused(f"How often is one of {', '.join(cadences)}")
    cadence = out["cadence"] or detected_cadence
    day = out["day"]
    if day is not None:
        if not _whole_cents(day):
            raise SeriesRefused("The day is a whole number")
        if cadence in NO_DAY:
            raise SeriesRefused("Twice a month keeps its two detected days; no day can be set")
        top = 7 if cadence in DAY_CADENCES else 31
        if not 1 <= day <= top:
            raise SeriesRefused("The day is a weekday, 1 Monday to 7 Sunday" if top == 7
                                else "The day is a day of the month, 1 to 31")
    if out["ended_on"] is not None:
        try:
            ok = isinstance(out["ended_on"], str) and date.fromisoformat(out["ended_on"]).isoformat() == out["ended_on"]
        except ValueError:
            ok = False
        if not ok:
            raise SeriesRefused("Ended on is a day, as 2026-10-31")
    return out


def save(conn, ident, group, edit, keys, replace=None):
    """Write the edit (`clean`'s answer) of the series with id `ident` now, in group
    `group` ((kind, name)), with the keys of its own payments, in place of the edit
    that was for it (`replace`, a saved edit's row). An edit with nothing set is not
    kept: the series is as detected again."""
    kind, group = group
    with db.transaction(conn):
        if replace is not None:
            conn.execute("DELETE FROM series_override WHERE id = ?", (replace,))
        conn.execute("DELETE FROM series_override WHERE series_id = ?", (ident,))
        if all(edit[k] is None for k in EDITS):
            return
        cur = conn.execute("""INSERT INTO series_override(series_id, group_kind, group_name, recurring, amount_minor,
                                                          cadence, day, ended_on, saved_at)
                              VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                           (ident, kind, group, None if edit["recurring"] is None else int(edit["recurring"]),
                            edit["amount"], edit["cadence"], edit["day"], edit["ended_on"], db.now()))
        conn.executemany("INSERT INTO series_override_txn(override_id, txn_key) VALUES (?, ?)",
                         [(cur.lastrowid, k) for k in sorted(keys)])


def set_payee(conn, payee, recurring):
    """A payee's own line: `recurring` False says it is not recurring whatever the
    rules say; None takes that back."""
    if not isinstance(payee, str) or not payee:
        raise SeriesRefused("Name the payee")
    if recurring is not None and recurring is not False:
        raise SeriesRefused("A payee's own line only says not recurring (false), or nothing (null)")
    if recurring is False and not conn.execute("SELECT 1 FROM payee WHERE name = ?", (payee,)).fetchone():
        raise SeriesRefused("No payee has that name")
    with db.transaction(conn):
        conn.execute("DELETE FROM series_override WHERE series_id IS NULL AND group_kind = 'payee' AND group_name = ?",
                     (payee,))
        if recurring is False:
            conn.execute("""INSERT INTO series_override(series_id, group_kind, group_name, recurring, saved_at)
                            VALUES (NULL, 'payee', ?, 0, ?)""", (payee, db.now()))
