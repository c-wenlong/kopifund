"""Planning from the browser (G8): budgets, need classes, trips and the plan.

These are the user-owned tables (SPEC.md, "The record"). From G8 the app owns
them: every change lands in kopifund.db, and the sync no longer reads them back
from Notion. Every function validates before it writes and writes in one
transaction, so a bad value never half-lands.

G17: a write names its trip or category by id; a name is still taken where
no id is sent (a tab opened before ids), and a name sent beside an id must
agree with it (db.pick).
"""
import re
import sqlite3
from datetime import date
from decimal import Decimal, InvalidOperation

from . import db
from .money import from_cents, to_cents

MONTH = re.compile(r"^\d{4}-\d{2}$")
DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
NEED_CLASSES = ("Need", "Want", "Savings", "Investment", "Income", "Transfer")
SPEND_RATES = ("window", "mean", "home", "phase")


class PlanRefused(Exception):
    """A value the record will not take, with the reason."""


# ---------------------------------------------------------------- budgets

def budgets(conn, month):
    """Every Spend and Income category for one month: planned and actual, in
    cents, in the order of the Categories page, each with its bucket
    (`category_group`) so the page can add up a bucket; `groups` lists those
    buckets in order with their kind."""
    if not MONTH.match(month or ""):
        raise PlanRefused("month is YYYY-MM")
    planned = {r["category_id"]: r for r in conn.execute(
        "SELECT * FROM v_budget WHERE month = ?", (month,))}
    actual = {r["category_id"]: r["v"] for r in conn.execute(
        """SELECT category_id, SUM(amount_sgd_minor) v FROM v_txn
           WHERE month = ? AND flow_class = 'External' GROUP BY category_id""", (month,))}
    out, groups = [], {}
    for c in conn.execute("""SELECT c.*, g.name AS group_name, g.kind AS kind FROM category c
                             LEFT JOIN category_group g ON g.id = c.group_id
                             WHERE c.bucket IN ('Spend', 'Income')
                             ORDER BY COALESCE(g.sort_order, 1e9), COALESCE(c.sort_order, 1e9), c.name"""):
        sign = 1 if c["need_class"] == "Income" else -1
        p = planned.get(c["id"])
        kind = c["kind"] or ("income" if c["bucket"] == "Income" else "spend")
        # every category has a bucket once the record is opened; a name in
        # brackets cannot be a real bucket's, so this one never merges with one
        group = c["group_name"] or "(no bucket)"
        out.append({"category_id": c["id"], "category": c["name"], "need_class": c["need_class"],
                    "bucket": c["bucket"], "category_group": group, "kind": kind,
                    "planned": p["planned_minor"] if p else None,
                    "actual": sign * (actual.get(c["id"]) or 0)})
        groups.setdefault(group, {"id": c["group_id"], "name": group, "kind": kind})
    months = [r[0] for r in conn.execute(
        "SELECT DISTINCT month FROM budget UNION SELECT DISTINCT month FROM v_txn ORDER BY 1 DESC")]
    return {"month": month, "rows": out, "groups": list(groups.values()), "months": months,
            "need_classes": list(NEED_CLASSES)}


def set_budget(conn, category, month, planned, category_id=None):
    """Set (or clear, with None) one category's planned amount, in cents.
    G17: the category by `category_id`, or by name when no id is sent."""
    if not MONTH.match(month or ""):
        raise PlanRefused("month is YYYY-MM")
    if planned is not None and (not isinstance(planned, int) or isinstance(planned, bool) or planned < 0):
        raise PlanRefused("planned is a whole number of cents, zero or more")
    with db.transaction(conn):
        c = _category(conn, category, category_id)
        conn.execute("""INSERT INTO budget(category_id, month, planned_minor) VALUES (?,?,?)
                        ON CONFLICT(category_id, month) DO UPDATE SET planned_minor = excluded.planned_minor""",
                     (c["id"], month, planned))
    return {"category": c["name"], "category_id": c["id"], "month": month, "planned": planned}


def copy_budgets(conn, src, dst):
    """Copy one month's planned amounts into another month that has none yet."""
    for m in (src, dst):
        if not MONTH.match(m or ""):
            raise PlanRefused("months are YYYY-MM")
    with db.transaction(conn):
        n = conn.execute("""INSERT INTO budget(category_id, month, planned_minor)
                            SELECT category_id, ?, planned_minor FROM budget
                            WHERE month = ? AND planned_minor IS NOT NULL
                            ON CONFLICT(category_id, month) DO NOTHING""", (dst, src)).rowcount
    return {"copied": n}


# ---------------------------------------------------------------- categories

def categories(conn):
    # G17: `group` is deprecated, the same value as cost_type (Fixed, Variable,
    # Savings), kept one release for an older page; the bucket is category_group
    return [{"id": r["id"], "name": r["name"], "bucket": r["bucket"], "cost_type": r["grp"],
             "group": r["grp"], "need_class": r["need_class"], "sort_order": r["sort_order"],
             "category_group": r["group_name"]}
            for r in conn.execute("""SELECT c.*, g.name AS group_name FROM category c
                                     LEFT JOIN category_group g ON g.id = c.group_id
                                     ORDER BY COALESCE(c.sort_order, 1e9), c.name""")]


def set_need_class(conn, name, need_class, category_id=None):
    """The Categories page sets a need class (server/finance/categories.py); this
    keeps the older call working, with the same check."""
    from . import categories as cats
    try:
        return cats.set_need_class(conn, name, need_class, category_id)
    except cats.CategoryRefused as e:
        raise PlanRefused(str(e)) from None


def _category(conn, name=None, category_id=None):
    try:
        return db.pick(conn, "category", category_id, name)
    except db.NotThere as e:
        raise PlanRefused(str(e)) from None


def _trip(conn, name=None, trip_id=None, named=False):
    try:
        return db.pick(conn, "trip", trip_id, name, named)
    except db.NotThere as e:
        raise PlanRefused(str(e)) from None


# ---------------------------------------------------------------- trips

def _trip_fields(trip):
    """A trip checked, as (name, start, end, countries, extra keys, notes):
    the countries upper case, each once, in the order given; the keys each
    once, sorted. A refusal names what is wrong."""
    name = (trip.get("name") or "").strip()
    start, end = trip.get("start") or "", trip.get("end") or trip.get("start") or ""
    if not name:
        raise PlanRefused("A trip needs a name")
    if not _real_day(start) or not _real_day(end):
        raise PlanRefused("The first and last day must be real calendar days")
    if end < start:
        raise PlanRefused("The trip ends before it starts")
    countries = list(dict.fromkeys(c.strip().upper() for c in trip.get("countries") or [] if c.strip()))
    if any(not re.fullmatch(r"[A-Z]{3}", c) for c in countries):
        raise PlanRefused("countries are three-letter codes, like JPN or MYS")
    keys = sorted({k.strip() for k in trip.get("extra_keys") or [] if k.strip()})
    return name, start, end, countries, keys, trip.get("notes") or ""


def _trip_dict(conn, row):
    """A trip row as the page sends one: what an Undo puts back. G17: with its
    id, so the Undo finds it, and a restore puts it back under the same id.
    Its countries and pinned keys are read from the record (trip_country,
    trip_pin) as saved, so take it before they change."""
    return {"id": row["id"], "name": row["name"], "start": row["start_date"], "end": row["end_date"],
            "countries": db.trip_countries(conn, row["id"]),
            "extra_keys": db.trip_pins(conn, row["id"], follow=False), "notes": row["notes"]}


_TRIP_COLUMNS = "id, name, start_date, end_date, notes"


def _trips_now(conn, cfg):
    """The trips as a load assigns rows to them, or None without a config,
    when the rows wait for the next load as before G13."""
    if cfg is None:
        return None
    from . import local
    return local.trips_from_db(conn)


def _relink(conn, cfg, before):
    """G13: relink the rows the trips now place differently from `before`
    (server/finance/relink.py), in the caller's transaction; the Change, or None."""
    if before is None:
        return None
    from . import relink
    return relink.trips(conn, cfg, before, _trips_now(conn, cfg))


def _relinked(change):
    return change.summary() if change else {"changed": 0, "written": 0}


def save_trip(conn, trip, old_name=None, cfg=None, trip_id=None):
    """Create or update one trip. Countries are ISO alpha-3 codes. G13: with
    `cfg`, the rows the trip now claims or lets go are relinked at once, as a
    load would place them, and the answer carries what an Undo
    (`undo_trip_save`) needs: the trip before and after, and the rows' trips
    before.

    G17: with `trip_id` the save edits that trip, its name included, and
    `old_name`, when sent, is the name the page read and must still be the
    trip's. With no id it goes by name as before: `old_name` renames, and a
    name no trip has makes a new trip."""
    name, start, end, countries, keys, notes = _trip_fields(trip)
    with db.transaction(conn):
        before = _trips_now(conn, cfg)
        prior = None
        if trip_id is not None:
            prior = _trip_dict(conn, _trip(conn, old_name, trip_id))
            try:
                conn.execute("UPDATE trip SET name = ?, start_date = ?, end_date = ?, notes = ? WHERE id = ?",
                             (name, start, end, notes, trip_id))
            except sqlite3.IntegrityError:
                raise PlanRefused(f"Another trip is already called {name!r}") from None
        else:
            if old_name and old_name != name:
                row = conn.execute(f"SELECT {_TRIP_COLUMNS} FROM trip WHERE name = ?", (old_name,)).fetchone()
                prior = row and _trip_dict(conn, row)
                try:
                    conn.execute("UPDATE trip SET name = ? WHERE name = ?", (name, old_name))
                except sqlite3.IntegrityError:
                    raise PlanRefused(f"Another trip is already called {name!r}") from None
            if prior is None:
                row = conn.execute(f"SELECT {_TRIP_COLUMNS} FROM trip WHERE name = ?", (name,)).fetchone()
                prior = row and _trip_dict(conn, row)
            conn.execute("""INSERT INTO trip(name, start_date, end_date, notes) VALUES (?,?,?,?)
                            ON CONFLICT(name) DO UPDATE SET start_date = excluded.start_date,
                              end_date = excluded.end_date, notes = excluded.notes""",
                         (name, start, end, notes))
        after = conn.execute(f"SELECT {_TRIP_COLUMNS} FROM trip WHERE name = ?", (name,)).fetchone()
        db.set_trip_links(conn, after["id"], countries, keys)
        change = _relink(conn, cfg, before)
    return {"id": after["id"], "name": name, "relinked": _relinked(change),
            "undo": {"before": prior or None, "after": _trip_dict(conn, after),
                     "record": change.data() if change else None}}


def undo_trip_save(conn, undo, cfg=None):
    """G13: Undo for a saved trip: a new trip goes, an edited one gets its old
    name, dates, countries, keys and notes back. A trip edited since the save
    is refused and left as it is.

    With `cfg`, the rows then follow the trips as they are once the trip is
    put back, relinked as a load would place them (server/finance/relink.py): the
    rows the save moved go back where they were, and a trip renamed or
    removed meanwhile is followed by name and date rather than by an id
    that may since belong to another trip. Without it, the rows the save
    moved get back the trip they were on (relink.revert), unless a later
    write moved them."""
    if not isinstance(undo, dict) or not isinstance(undo.get("after"), dict):
        raise PlanRefused("Nothing to undo")
    from . import relink
    after, before = undo["after"], undo.get("before")
    with db.transaction(conn):
        if _whole(after.get("id")):
            row = conn.execute(f"SELECT {_TRIP_COLUMNS} FROM trip WHERE id = ?", (after["id"],)).fetchone()
        else:                           # an Undo from a page opened before G17 carries no id
            row = conn.execute(f"SELECT {_TRIP_COLUMNS} FROM trip WHERE name = ?",
                               (str(after.get("name") or ""),)).fetchone()
            after = {**after, "id": row["id"]} if row is not None else after
        if row is None or _trip_dict(conn, row) != after:
            raise PlanRefused("The trip has changed since this save, so it cannot be undone here")
        trips_before = _trips_now(conn, cfg)
        back = relink.revert(conn, undo.get("record")) if trips_before is None else None
        if not isinstance(before, dict):
            # G17 (T7): the schema lets its rows go (ON DELETE SET NULL) and
            # takes its countries and pins with it
            held = {r[0] for r in conn.execute("SELECT txn_key FROM txn WHERE trip_id = ?", (row["id"],))}
            conn.execute("DELETE FROM trip WHERE id = ?", (row["id"],))
            out = {"removed": row["name"], "id": row["id"]}
        else:
            held = set()
            name, start, end, countries, extra, notes = _trip_fields(before)
            try:
                conn.execute("UPDATE trip SET name = ?, start_date = ?, end_date = ?, notes = ? WHERE id = ?",
                             (name, start, end, notes, row["id"]))
            except sqlite3.IntegrityError:
                raise PlanRefused(f"A trip named {name!r} exists now, so the old name was not put back") from None
            db.set_trip_links(conn, row["id"], countries, extra)
            out = {"name": name, "id": row["id"]}
        if back is None:
            change = _relink(conn, cfg, trips_before)
            moved = held | change.changed_keys()
            back = {"changed": len(moved), "written": len(moved)}
    return {**out, "relinked": back}


def remove_trip(conn, name, cfg=None, trip_id=None):
    """Take a trip out. The answer carries what an Undo needs to put it back:
    the trip as saved, its id included, the keys of the rows that were
    counted on it, and, with `cfg` (G13), the trip each of those rows moved
    to: a row on a side trip inside a longer stay goes to the stay, as a load
    would place it. G17: the trip by `trip_id`, or by name when no id is sent."""
    with db.transaction(conn):
        before = _trips_now(conn, cfg)
        row = _trip(conn, name, trip_id, named=True)
        name = row["name"]
        saved = _trip_dict(conn, row)
        keys = [r["txn_key"] for r in conn.execute(
            "SELECT txn_key FROM txn WHERE trip_id = ? ORDER BY txn_key", (row["id"],))]
        # G17 (T7): the schema lets its rows go (ON DELETE SET NULL) and takes
        # its countries and pins with it (CASCADE)
        conn.execute("DELETE FROM trip WHERE id = ?", (row["id"],))
        change = _relink(conn, cfg, before)
    moved = {key: after[0] for key, _, after in (change.rows if change else [])}
    # every row it counted changed: it lost the trip, and some went on to another
    return {"removed": name, "id": row["id"], "relinked": {"changed": len(keys), "written": len(keys)},
            "restore": {"trip": saved, "txn_keys": keys, "moved": moved}}


def restore_trip(conn, restore, cfg=None):
    """G13: Undo for a removed trip. Puts the trip back as `remove_trip`
    returned it. With `cfg` the rows then follow the trips as they now are,
    placed as a load would place them (server/finance/relink.py), so the rows it
    counted come back to it whichever trip they went to meanwhile, by dates
    and countries rather than by a trip id that may since belong to another
    trip. Without it, each row still where the remove left it (no trip, or the
    trip `moved` names) is counted on it again, but not a row that has since
    been counted on another trip, whose pinned key is dropped too (a later
    Load would otherwise take the row back for this trip). It only ever
    inserts, in one transaction: a trip of the same name made meanwhile, even
    a moment ago from another tab, is refused and left alone. G17: it comes
    back under the id it had, unless a trip made meanwhile holds that id."""
    if not isinstance(restore, dict) or not isinstance(restore.get("trip"), dict):
        raise PlanRefused("Nothing to restore")
    if cfg is not None:
        with db.transaction(conn):
            before = _trips_now(conn, cfg)
            new_id, name = _put_back(conn, dict(restore["trip"]))
            change = _relink(conn, cfg, before)
        return {"id": new_id, "name": name, "relinked": _relinked(change)}
    trip = dict(restore["trip"])
    keys = [str(k) for k in restore.get("txn_keys") or []]
    moved = restore.get("moved") if isinstance(restore.get("moved"), dict) else {}
    left = {str(k): (v if isinstance(v, int) and not isinstance(v, bool) else None) for k, v in moved.items()}
    taken = set()                       # keys of rows another trip holds now
    with db.transaction(conn):
        pinned = sorted({str(k).strip() for k in trip.get("extra_keys") or [] if str(k).strip()})
        for i in range(0, len(pinned), 500):
            part = pinned[i:i + 500]
            taken.update(r["txn_key"] for r in conn.execute(
                f"SELECT txn_key, trip_id FROM txn WHERE trip_id IS NOT NULL AND txn_key IN ({','.join('?' * len(part))})",
                part) if r["trip_id"] != left.get(r["txn_key"]))
        trip["extra_keys"] = [k for k in pinned if k not in taken]
        new_id, name = _put_back(conn, trip)
        back = 0
        for key in keys:
            back += conn.execute("UPDATE txn SET trip_id = ? WHERE txn_key = ? AND trip_id IS ?",
                                 (new_id, key, left.get(key))).rowcount
    return {"id": new_id, "name": name, "relinked": {"changed": back, "written": back}}


def _whole(v):
    return isinstance(v, int) and not isinstance(v, bool) and v > 0


def _put_back(conn, trip):
    """Insert a removed trip again, under its old id when no trip holds that
    id now; (the id it got, its name). A trip of the same name made meanwhile
    is refused."""
    name, start, end, countries, extra, notes = _trip_fields(trip)
    want = trip.get("id")
    if not _whole(want) or conn.execute("SELECT 1 FROM trip WHERE id = ?", (want,)).fetchone():
        want = None                     # SQLite picks the next id
    try:
        new_id = conn.execute("INSERT INTO trip(id, name, start_date, end_date, notes) VALUES (?,?,?,?,?)",
                              (want, name, start, end, notes)).lastrowid
    except sqlite3.IntegrityError:
        raise PlanRefused(f"A trip named {name!r} exists again, so the old one was not restored") from None
    db.set_trip_links(conn, new_id, countries, extra)
    return new_id, name


# ---------------------------------------------------------------- the plan

def get_plan(conn):
    return db.load_plan(conn)


def set_plan(conn, plan):
    """Replace the plan after checking every field the forward view and the
    Net worth projection read."""
    db.save_plan(conn, clean_plan(plan))
    return get_plan(conn)


def clean_plan(plan):
    """The plan checked field by field, as it will be stored: a refusal names
    the field, and nothing is written until every field passes."""
    if not isinstance(plan, dict):
        raise PlanRefused("The plan is an object")
    plan = dict(plan)
    if "to" in plan and not MONTH.match(str(plan["to"])):
        raise PlanRefused("to is the last month to project, YYYY-MM")
    for k in db.PLAN_MONEY:
        if k in plan and plan[k] is not None:
            _amount(plan[k], k)
    if "cpf_employer_rate" in plan and not 0 <= float(plan["cpf_employer_rate"]) <= 1:
        raise PlanRefused("cpf_employer_rate is a fraction, like 0.17")
    if "spend_rate" in plan and plan["spend_rate"] not in SPEND_RATES:
        raise PlanRefused(f"spend_rate is one of {', '.join(SPEND_RATES)}")
    if "income" in plan:
        plan["income"] = [_income_row(seg, i) for i, seg in enumerate(_list(plan["income"], "income"))]
    if "investing" in plan:
        plan["investing"] = _investing(plan["investing"])
    if "projection" in plan:
        plan["projection"] = check_projection(plan["projection"])
    trips = []
    for t in _list(plan.get("trips"), "trips"):
        t = dict(t)
        _span(t, "a planned trip")
        for f in ("per_day", "flights", "flights_month"):
            if t.get(f) in (None, ""):
                t.pop(f, None)          # unset: the forward view uses its own default
        for f in ("per_day", "flights"):
            if f in t:
                _amount(t[f], f)
        trips.append(t)
    if "trips" in plan:
        plan["trips"] = trips
    for o in _list(plan.get("one_offs"), "one_offs"):
        if not MONTH.match(str(o.get("month", ""))[:7]):
            raise PlanRefused("A one-off needs a month")
        _amount(o.get("amount"), "amount")
    return plan


def _list(v, name):
    if v is None:
        return []
    if not isinstance(v, list) or not all(isinstance(x, dict) for x in v):
        raise PlanRefused(f"{name} is a list of rows")
    return v


# ---------------------------------------------------------------- G14: projection inputs
#
# The Net worth page's inputs live in the plan beside what the forward view
# reads, so pay is typed once and both pages read the same rows:
#
#   income      [{name, type, from, to, gross, cpf, bonus_months, bonus_month}]
#               type is employment or side; an empty to runs to the horizon;
#               the bonus is months of that row's pay landing in one calendar
#               month each year, employment rows only
#   investing   [{from, amount}]: the monthly transfer to investments from that
#               month on; an empty from means from the start
#   projection  {the return, inflation and growth rates in per cent a year,
#               horizon, dollars, spend and milestone in dollars}
#
# Money is in dollars here, as the checks read it; the API sends and takes it
# as cents under `_minor` names (G17, plan_out and plan_in below).
#
# A plan from before G14 still reads: a segment with no type is employment and
# its note becomes its name, and a single `dca` is the first investing step.
# Neither is rewritten until the owner saves.

INCOME_TYPES = ("employment", "side")
HORIZONS = ("10y", "20y", "30y", "65")
DOLLARS = ("today", "future")
RATES = {
    # key: (what the page calls it, lowest, highest, default); None is no default
    "pessimistic_pct": ("The pessimistic return", "-50", "50", "3"),
    "expected_pct": ("The expected return", "-50", "50", "6"),
    "optimistic_pct": ("The optimistic return", "-50", "50", "9"),
    "inflation_pct": ("Inflation", "-10", "20", "2.5"),
    "salary_growth_pct": ("Salary growth", "-20", "30", "0"),
    "sums_growth_pct": ("FRS and BHS growth", "0", "10", "3.5"),
    "spend_growth_pct": ("Spending growth", "-10", "20", None),   # None: inflation
}
PROJECTION_MONEY = {"spend": "Spending a month", "milestone": "The milestone"}
MAX_MONTHLY = Decimal("1000000")          # S$ a month, far past any real figure
MAX_BONUS_MONTHS = Decimal("24")


def income_rows(plan):
    """The plan's income rows in the G14 shape, old segments included: no type
    is employment, a note is the name, CPF defaults to yes for employment and
    no for side income, an empty to date runs to the horizon."""
    out = []
    for seg in (plan or {}).get("income") or ():
        if not isinstance(seg, dict):
            continue
        kind = seg.get("type") or "employment"
        cpf = seg.get("cpf")
        out.append({
            "name": str(seg.get("name") or seg.get("note") or ""),
            "type": kind,
            "from": seg.get("from"),
            "to": seg.get("to") or None,
            "gross": seg.get("gross"),
            "cpf": (kind == "employment") if cpf is None else bool(cpf),
            "bonus_months": seg.get("bonus_months") if kind == "employment" else None,
            "bonus_month": seg.get("bonus_month") if kind == "employment" else None,
        })
    return out


def investing_steps(plan):
    """The investing steps, oldest first; a plan with only the old single
    `dca` reads as one step from the start."""
    plan = plan or {}
    if "investing" in plan:
        steps = [{"from": s.get("from") or None, "amount": s.get("amount")}
                 for s in plan.get("investing") or () if isinstance(s, dict)]
        return sorted(steps, key=lambda s: s["from"] or "")
    dca = plan.get("dca")
    if dca in (None, ""):
        return []
    return [{"from": None, "amount": dca}]


def projection_settings(plan):
    """The projection's saved settings with the defaults filled in. Rates are
    text holding a Decimal (None for spending growth means inflation); spend
    and milestone are dollars or None."""
    saved = (plan or {}).get("projection") or {}
    out = {k: (str(saved[k]) if saved.get(k) not in (None, "") else d)
           for k, (_, _, _, d) in RATES.items()}
    out["horizon"] = saved.get("horizon") or "10y"
    out["dollars"] = saved.get("dollars") or "today"
    for k in PROJECTION_MONEY:
        out[k] = saved.get(k) if saved.get(k) not in (None, "") else None
    return out


def merge_inputs(plan, inputs):
    """The stored plan with the Net worth page's three inputs in place of its
    own. Saving the investing steps retires the old single `dca`."""
    if not isinstance(inputs, dict):
        raise PlanRefused("The inputs are an object")
    out = dict(plan or {})
    for k in ("income", "investing", "projection"):
        if k in inputs:
            out[k] = inputs[k]
    if "investing" in inputs:
        out.pop("dca", None)
    return out


NET_WORTH_KEYS = ("income", "investing", "projection", "dca")


def save_plan_page(conn, plan):
    """The Plan page's Save. Pay, investing and the projection settings are the
    Net worth page's to write (G14), so what the record holds for them now
    stands, whatever copy the Plan page loaded: a Plan tab left open while
    Net worth saved cannot put the older rows back."""
    if not isinstance(plan, dict):
        raise PlanRefused("The plan is an object")
    stored = get_plan(conn)
    out = {k: v for k, v in plan.items() if k not in NET_WORTH_KEYS}
    out.update({k: stored[k] for k in NET_WORTH_KEYS if k in stored})
    return set_plan(conn, out)


def save_inputs(conn, inputs):
    """G14: save the Net worth page's inputs into the plan, all or nothing."""
    return set_plan(conn, merge_inputs(get_plan(conn), inputs))


def _label(i, row):
    name = str(row.get("name") or row.get("note") or "").strip()
    return f"Income row {i + 1}" + (f" ({name})" if name else "")


# ---------------------------------------------------------------- G17: the plan on the wire
#
# The record keeps the plan's money in integer cents and the code above checks
# and reads it in dollars. Between the browser and the server it is cents: each
# money field travels under its name plus `_minor` (`gross_minor`, `dca_minor`,
# `spend_minor`), never as dollars. The fields are db.PLAN_MONEY at the top,
# db.PLAN_LIST_MONEY in each row of a list, and db.PLAN_DICT_MONEY in the
# projection settings.

MINOR = "_minor"
_TOP_NAMES = {"dca": "The investing amount", "insurance": "Insurance premiums", "other_income": "Other income",
              "repayments": "Repayments"}
_ROW_NAMES = {
    "income": lambda i, row, f: f"{_label(i, row)}: gross a month",
    "investing": lambda i, row, f: f"Investing step {i + 1}: the amount a month",
    "trips": lambda i, row, f: f"Planned trip {i + 1}: " + ("spending a day" if f == "per_day" else "flights"),
    "one_offs": lambda i, row, f: f"One-off payment {i + 1}: the amount",
}


def _wire_levels(plan, convert):
    """The plan with `convert(d, fields, name)` applied to its top level, to each
    row of a money list and to each money object; `name(f)` says which field it
    is in the words a refusal uses."""
    out = convert(plan, db.PLAN_MONEY, lambda f: _TOP_NAMES.get(f, f))
    for k, fields in db.PLAN_LIST_MONEY.items():
        if isinstance(plan.get(k), list):
            out[k] = [convert(r, fields, lambda f, i=i, r=r, k=k: _ROW_NAMES[k](i, r, f)) if isinstance(r, dict) else r
                      for i, r in enumerate(plan[k])]
    for k, fields in db.PLAN_DICT_MONEY.items():
        if isinstance(plan.get(k), dict):
            out[k] = convert(plan[k], fields, lambda f: PROJECTION_MONEY.get(f, f))
    return out


def plan_out(plan):
    """The plan (or the Net worth page's inputs) as the API sends it: every
    money field as integer cents under its `_minor` name. Dollars come in as
    the record's Decimals or the checked text clean_plan keeps; an unset field
    goes as null."""
    def convert(d, fields, _name):
        return {(k + MINOR if k in fields else k): ((None if v in (None, "") else to_cents(v)) if k in fields else v)
                for k, v in d.items()}
    return _wire_levels(plan, convert)


def plan_in(plan):
    """The plan (or the Net worth page's inputs) as a page sent it, with its
    money in dollars for clean_plan. The page sends cents under the `_minor`
    names; a page from before G17, or a draft one kept, sends dollars under
    the bare names, which are still read for one release. The key decides
    which, never the size of the value, and a field sent both ways is refused.
    Anything that is not an object comes back as it is, for the checks to
    refuse in their own words."""
    if not isinstance(plan, dict):
        return plan

    def convert(d, fields, name):
        d = dict(d)
        for f in fields:
            if f + MINOR not in d:
                continue
            if f in d:
                raise PlanRefused(f"{name(f)} is sent twice, as {f} and as {f}{MINOR}")
            v = d.pop(f + MINOR)
            if v is not None and (isinstance(v, bool) or not isinstance(v, int)):
                raise PlanRefused(f"{name(f)} is a whole number of cents")
            d[f] = None if v is None else from_cents(v)
        return d
    return _wire_levels(plan, convert)


def _income_row(seg, i):
    """One income row checked, in its stored shape."""
    label = _label(i, seg)
    kind = seg.get("type") or "employment"
    if kind not in INCOME_TYPES:
        raise PlanRefused(f"{label}: the type is employment or side")
    name = seg.get("name")
    if name is not None and (not isinstance(name, str) or len(name) > 80):
        raise PlanRefused(f"{label}: the name is text, 80 characters at most")
    start, end = str(seg.get("from") or ""), seg.get("to")
    end = None if end in (None, "") else str(end)
    if not _real_day(start):
        raise PlanRefused(f"{label}: the from date is a real calendar day")
    if end is not None and not _real_day(end):
        raise PlanRefused(f"{label}: the to date is a real calendar day, or empty to run to the horizon")
    if end is not None and end < start:
        raise PlanRefused(f"{label}: the to date is before the from date")
    gross = _amount(seg.get("gross"), f"{label}: gross a month")
    if gross > MAX_MONTHLY:
        raise PlanRefused(f"{label}: gross a month is at most S$1,000,000")
    cpf = seg.get("cpf")
    if cpf is not None and not isinstance(cpf, bool):
        raise PlanRefused(f"{label}: CPF is yes or no")
    out = {"name": (name or "").strip(), "type": kind, "from": start, "to": end, "gross": str(gross),
           "cpf": (kind == "employment") if cpf is None else cpf}
    if not out["name"] and seg.get("note"):
        out["note"] = str(seg["note"])            # a segment from before G14 keeps its note
    months, month = seg.get("bonus_months"), seg.get("bonus_month")
    if months not in (None, "") and _decimal(months, f"{label}: the bonus") != 0:
        if kind != "employment":
            raise PlanRefused(f"{label}: a bonus is for employment rows only")
        n = _decimal(months, f"{label}: the bonus")
        if not 0 < n <= MAX_BONUS_MONTHS or n != n.quantize(Decimal("0.01")):
            raise PlanRefused(f"{label}: the bonus is months of pay, more than 0 and at most 24, "
                              f"to two decimal places")
        try:
            m = int(str(month))
        except (TypeError, ValueError):
            m = 0
        if isinstance(month, bool) or not 1 <= m <= 12:
            raise PlanRefused(f"{label}: the bonus month is a month of the year, 1 to 12")
        out["bonus_months"], out["bonus_month"] = _text(n), m
    return out


def _investing(steps):
    out, seen = [], set()
    for i, s in enumerate(_list(steps, "investing")):
        label = f"Investing step {i + 1}"
        start = s.get("from")
        start = None if start in (None, "") else str(start)
        if start is not None and not _real_month(start):
            raise PlanRefused(f"{label}: from is a month, or empty for from the start")
        if start in seen:
            raise PlanRefused(f"{label}: another step already starts "
                              + ("at the start" if start is None else f"in {start}"))
        seen.add(start)
        amount = _amount(s.get("amount"), f"{label}: the amount a month")
        if amount > MAX_MONTHLY:
            raise PlanRefused(f"{label}: the amount a month is at most S$1,000,000")
        out.append({"from": start, "amount": str(amount)})
    return sorted(out, key=lambda s: s["from"] or "")


def check_projection(p):
    if not isinstance(p, dict):
        raise PlanRefused("The projection settings are an object")
    out = {}
    for k, (name, lo, hi, _) in RATES.items():
        v = p.get(k)
        if v in (None, ""):
            continue
        d = _decimal(v, name)
        if not Decimal(lo) <= d <= Decimal(hi):
            raise PlanRefused(f"{name} is between {lo} and {hi} per cent a year")
        out[k] = _text(d)
    s = projection_settings({"projection": out})
    if not Decimal(s["pessimistic_pct"]) <= Decimal(s["expected_pct"]) <= Decimal(s["optimistic_pct"]):
        raise PlanRefused("The returns run pessimistic, expected, optimistic, lowest first")
    horizon = p.get("horizon") or "10y"
    if horizon not in HORIZONS:
        raise PlanRefused("The horizon is 10, 20 or 30 years, or to 65")
    dollars = p.get("dollars") or "today"
    if dollars not in DOLLARS:
        raise PlanRefused("Dollars are today's or future")
    out["horizon"], out["dollars"] = horizon, dollars
    for k, name in PROJECTION_MONEY.items():
        v = p.get(k)
        if v in (None, ""):
            continue
        amount = _amount(v, name)
        if amount > (MAX_MONTHLY if k == "spend" else MAX_MONTHLY * 1000):
            raise PlanRefused(f"{name} is too large to be a real figure")
        out[k] = str(amount)
    return out


def _decimal(x, name):
    """Text or a number as a Decimal, with commas allowed: "6,000.50"."""
    if isinstance(x, bool):
        raise PlanRefused(f"{name} is a number")
    try:
        v = Decimal(str(x).replace(",", "").strip())
    except (TypeError, ValueError, InvalidOperation):
        raise PlanRefused(f"{name} is a number") from None
    if not v.is_finite():
        raise PlanRefused(f"{name} is a number")
    return v


def _text(d):
    """A Decimal as plain text with no exponent and no trailing zeros: "2.5", "10"."""
    t = format(d, "f")
    return (t.rstrip("0").rstrip(".") if "." in t else t) or "0"


def _real_month(text):
    return bool(MONTH.match(text or "")) and 1 <= int(text[5:7]) <= 12


def _amount(x, name):
    """A plan amount in dollars, checked as an exact decimal: never held as a
    float, and never finer than a cent. Returns the Decimal."""
    if x is None or (isinstance(x, str) and not x.strip()):
        raise PlanRefused(f"{name} is a number")
    v = _decimal(x, name)
    if v < 0:
        raise PlanRefused(f"{name} is zero or more")
    if v != v.quantize(Decimal("0.01")):
        raise PlanRefused(f"{name} is in dollars and cents, no finer")
    return v


def _real_day(text):
    if not DAY.match(text or ""):
        return False
    try:
        date.fromisoformat(text)
    except ValueError:
        return False
    return True


def _span(item, what):
    a, b = str(item.get("from", "")), str(item.get("to", ""))
    if not _real_day(a) or not _real_day(b):
        raise PlanRefused(f"{what} needs a from and a to date, each a real calendar day")
    if b < a:
        raise PlanRefused(f"{what} ends before it starts")


def this_month():
    return date.today().strftime("%Y-%m")
