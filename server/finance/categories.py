"""The Categories page (G8): categories, and the buckets they sit in.

A bucket is a `category_group` row (server/finance/db.py): Food, Home and bills,
Income, Transfers. Its `kind` (spend, income or transfer) decides what its
categories are, and each category's `bucket` column (Spend, Income, Transfer,
which every stat reads) follows the kind of the bucket it sits in: G17, the
record's triggers keep it so, and nothing here writes it. The page
calls the level "Bucket"; the code calls it a group, because `bucket` and
`grp` on category already mean something else.

G17: every write names its row by id (`id`, and `group_id` or `move_to_id`
for the bucket or category it goes to); a name is still taken where no id is
sent, for a tab opened before ids, and a name sent beside an id must agree
with it (db.pick).

Every change is checked before it is written and written in one transaction.
Renaming or deleting a category leaves the old name in category_alias, so a
shared rule that still says it lands in the right place and the shipped list
never brings it back; once that is committed, the personal rules that name it
are rewritten (rules_app.rewrite_categories: one write in the Rules page's
undo history). In that order the rules load at every moment, even for a check
running alongside. Deleting
a category moves its transactions and budgets to the category the user
picks; the record is copied into backups/ first.
"""
from . import db, rules_app
from .backup import backup_record

KINDS = ("spend", "income", "transfer")
# the need classes a category of each kind may carry
NEED_CLASSES = {"spend": ("Need", "Want"), "income": ("Income",),
                "transfer": ("Transfer", "Investment", "Savings")}
# what a category moved into a bucket of another kind gets
DEFAULT_NEED_CLASS = {"spend": "Want", "income": "Income", "transfer": "Transfer"}
# G17: a category's cost type (category.grp), as the shipped list and Notion
# name them; a new category is Variable
COST_TYPES = ("Fixed", "Variable", "Savings")
MAX_NAME = 60

# G17 (U1): the icons a bucket may have, lucide names (apps/web/src renders them),
# and the chart series tokens its colour may be (--c1 to --c12 in
# apps/web/src/index.css). server/analytics/categories.json gives each shipped bucket
# its icon; a bucket without one shows its kind's.
BUCKET_ICONS = ("utensils", "house-plug", "bus", "heart-pulse", "sprout", "shopping-bag", "plane", "gift",
                "shapes", "wallet", "arrow-up-down", "car", "baby", "dog", "graduation-cap", "music",
                "gamepad-2", "shirt", "coffee", "dumbbell", "book-open", "piggy-bank", "briefcase",
                "smartphone")
SERIES_COLOURS = tuple(f"c{i}" for i in range(1, 13))
DEFAULT_ICON = {"spend": "shapes", "income": "wallet", "transfer": "arrow-up-down"}


class CategoryRefused(Exception):
    """A change the record will not take, with the reason."""


# ---------------------------------------------------------------- reading

def overview(conn):
    """Every bucket in order, each with its categories in order, and for each
    category what a change to it touches: its transactions, its average a
    month (in cents), its budgets and the rules that name it."""
    first, last = conn.execute("SELECT MIN(month), MAX(month) FROM v_txn").fetchone()
    months = _months_between(first, last) if first else 0
    stats = {r["category_id"]: r for r in conn.execute(
        """SELECT category_id, COUNT(*) AS n,
                  COALESCE(SUM(CASE WHEN flow_class = 'External' THEN amount_sgd_minor END), 0) AS ext,
                  COALESCE(SUM(CASE WHEN amount_sgd_minor < 0 THEN -amount_sgd_minor END), 0) AS out
           FROM v_txn WHERE category_id IS NOT NULL GROUP BY category_id""")}
    budgets = {r[0]: r[1] for r in conn.execute(
        "SELECT category_id, COUNT(*) FROM budget WHERE planned_minor IS NOT NULL GROUP BY category_id")}
    ids = db.category_ids(conn)
    rules, personal = {}, {}
    try:
        for _, mine, name in rules_app.rule_categories():
            cid = ids.get(name)
            rules[cid] = rules.get(cid, 0) + 1
            if mine:
                personal[cid] = personal.get(cid, 0) + 1
    except (OSError, SystemExit):
        pass                              # a rules folder that cannot be read counts nothing
    groups = []
    rows = _groups(conn)
    shown = shown_colours(rows)
    for g in rows:
        cats = []
        for c in _members(conn, g["id"]):
            s = stats.get(c["id"])
            total = 0
            if s:
                # what "a month" means per kind: spending out, income in, and
                # for a transfer everything that left, whatever its flow
                total = {"spend": -s["ext"], "income": s["ext"]}.get(g["kind"], s["out"])
            cats.append({"id": c["id"], "name": c["name"], "need_class": c["need_class"],
                         "cost_type": c["grp"],
                         # G17: deprecated, the same value as cost_type, kept one release so an
                         # older page still reads it. Not the bucket: that is the group it sits in
                         "group": c["grp"],
                         "transactions": s["n"] if s else 0,
                         "monthly": round(total / months) if months else None,
                         "budgets": budgets.get(c["id"], 0),
                         "rules": rules.get(c["id"], 0), "personal_rules": personal.get(c["id"], 0)})
        groups.append({"id": g["id"], "name": g["name"], "kind": g["kind"], "fixed": g["kind"] != "spend",
                       **style(g, shown), "categories": cats})
    return {"groups": groups, "need_classes": {k: list(v) for k, v in NEED_CLASSES.items()},
            "months": months}


def style(group, shown):
    """G17 (U1): {icon, colour, shown} of one category_group row as the pages
    get it: its own icon, else its kind's; its colour token, or None for the
    default by order; and (G18) `shown`, the token it is drawn in, from
    shown_colours over every bucket."""
    return {"icon": group["icon"] or DEFAULT_ICON[group["kind"]], "colour": group["colour"],
            "shown": shown[group["id"]]}


def styles(conn):
    """{bucket name: style} for every bucket."""
    rows = _groups(conn)
    shown = shown_colours(rows)
    return {g["name"]: style(g, shown) for g in rows}


def shown_colours(groups):
    """G18: the colour token each bucket is drawn in, by id, no two alike for
    up to twelve buckets. `groups` is every category_group row in the page's
    order. A colour the owner chose is the bucket's own (the first in order
    keeps it, should two ever say the same). A bucket with none takes the
    colour its place gives it, the spend buckets in their order and then the
    others, unless a chosen colour has it; then it gets the first colour no
    bucket has. So with nothing chosen every bucket keeps its place's colour,
    and a choice moves no bucket but the one it clashes with."""
    order = [g for g in groups if g["kind"] == "spend"] + [g for g in groups if g["kind"] != "spend"]
    n = len(SERIES_COLOURS)
    shown, taken, waiting = {}, set(), []
    for g in order:
        if g["colour"] in SERIES_COLOURS and g["colour"] not in taken:
            shown[g["id"]] = g["colour"]
            taken.add(g["colour"])
    for i, g in enumerate(order):
        if g["id"] in shown:
            continue
        place = SERIES_COLOURS[i % n]
        if place in taken:
            waiting.append(g)
        else:
            shown[g["id"]] = place
            taken.add(place)
    free = [c for c in SERIES_COLOURS if c not in taken] or list(SERIES_COLOURS)
    for j, g in enumerate(waiting):
        shown[g["id"]] = free[j % len(free)]       # past twelve buckets a colour comes round again
    return shown


def _months_between(first, last):
    y1, m1 = map(int, first.split("-"))
    y2, m2 = map(int, last.split("-"))
    return (y2 - y1) * 12 + m2 - m1 + 1


def _groups(conn):
    return conn.execute("SELECT * FROM category_group ORDER BY COALESCE(sort_order, 1e9), name").fetchall()


def _members(conn, group_id):
    return conn.execute("""SELECT * FROM category WHERE group_id = ?
                           ORDER BY COALESCE(sort_order, 1e9), name""", (group_id,)).fetchall()


def _group(conn, name=None, group_id=None, named=False):
    """A bucket by id, or by name when no id is sent (db.pick)."""
    try:
        return db.pick(conn, "category_group", group_id, name, named)
    except db.NotThere as e:
        raise CategoryRefused(str(e)) from None


def _category(conn, name=None, category_id=None, named=False):
    """A category by id, or by name when no id is sent (db.pick), with its bucket's kind."""
    try:
        cid = db.pick(conn, "category", category_id, name, named)["id"]
    except db.NotThere as e:
        raise CategoryRefused(str(e)) from None
    return conn.execute("""SELECT c.*, g.kind AS kind FROM category c
                           LEFT JOIN category_group g ON g.id = c.group_id
                           WHERE c.id = ?""", (cid,)).fetchone()


def _clean(name, what):
    name = " ".join(str(name or "").split())
    if not name:
        raise CategoryRefused(f"A {what} needs a name")
    if len(name) > MAX_NAME:
        raise CategoryRefused(f"A {what} name is at most {MAX_NAME} characters")
    if name.startswith("("):
        # "(none)" and "(uncategorised)" are what the app and the stats call no category
        raise CategoryRefused(f"A {what} name cannot start with a bracket")
    return name


def _free_name(conn, table, name, but=None):
    """Refuse a name another row already has, whatever its case: two rows
    that differ only in case read as one in every list."""
    row = conn.execute(f"SELECT id FROM {table} WHERE lower(name) = lower(?)", (name,)).fetchone()
    if row and row[0] != but:
        raise CategoryRefused(f"There is already a {'bucket' if table == 'category_group' else 'category'} "
                              f"called {name!r}")


def _renumber(conn, order=None):
    """Give every category a sort order in the order the page shows them,
    bucket by bucket, in tens: every other list in the app (Transactions,
    Rules) sorts on it too. `order` is {group id: [category id]} for the
    buckets whose order is being set."""
    order = order or {}
    n = 0
    for g in _groups(conn):
        ids = order.get(g["id"]) or [c["id"] for c in _members(conn, g["id"])]
        for cid in ids:
            n += 10
            conn.execute("UPDATE category SET sort_order = ? WHERE id = ?", (n, cid))


def _place(conn, cat, group):
    """Put a category in a bucket. Into a bucket of another kind its bucket
    column follows (the record's own triggers keep it the kind's, G17), and
    a need class that kind does not take is replaced; within one kind only
    the bucket changes, so a reorder never changes what the stats count."""
    kind = group["kind"]
    if cat["kind"] == kind:
        conn.execute("UPDATE category SET group_id = ? WHERE id = ?", (group["id"], cat["id"]))
        return
    need = cat["need_class"]
    if need not in NEED_CLASSES[kind]:
        need = DEFAULT_NEED_CLASS[kind]
    conn.execute("UPDATE category SET group_id = ?, need_class = ? WHERE id = ?",
                 (group["id"], need, cat["id"]))


# ---------------------------------------------------------------- buckets

def add_group(conn, name):
    """A new spending bucket, last among the spending ones. Income and
    Transfers are one each and cannot be added to."""
    name = _clean(name, "bucket")
    with db.transaction(conn):
        _free_name(conn, "category_group", name)
        # after the last spending bucket, ahead of Income and Transfers
        last = conn.execute("""SELECT MAX(sort_order) FROM category_group WHERE kind = 'spend'""").fetchone()[0]
        gid = conn.execute("INSERT INTO category_group(name, kind, sort_order) VALUES (?, 'spend', ?)",
                           (name, (last or 0) + 5)).lastrowid
        _renumber_groups(conn)
    return {"name": name, "id": gid}


def rename_group(conn, name, new_name, group_id=None):
    new_name = _clean(new_name, "bucket")
    with db.transaction(conn):
        g = _group(conn, name, group_id)
        _free_name(conn, "category_group", new_name, but=g["id"])
        conn.execute("UPDATE category_group SET name = ? WHERE id = ?", (new_name, g["id"]))
    return {"name": new_name, "id": g["id"]}


def reorder_groups(conn, names=None, ids=None):
    """Set the order of the buckets: `ids` (G17), or `names` from an older
    page, is every bucket, in order. With both, each name is the one the
    page showed for the id in its place, and must still be that bucket's."""
    key, order = ("id", ids) if ids is not None else ("name", names)
    if not isinstance(order, list):
        raise CategoryRefused("Send the buckets in their new order")
    with db.transaction(conn):
        have = [g[key] for g in _groups(conn)]
        if sorted(order) != sorted(have):
            raise CategoryRefused("The new order must name every bucket once: reload the page")
        if ids is not None and names is not None:
            now = {g["id"]: g["name"] for g in _groups(conn)}
            if not isinstance(names, list) or [now[i] for i in ids] != names:
                raise CategoryRefused("The buckets have changed since this page loaded: reload the page")
        for i, v in enumerate(order):
            conn.execute(f"UPDATE category_group SET sort_order = ? WHERE {key} = ?", ((i + 1) * 10, v))
        _renumber(conn)
        names = [g["name"] for g in _groups(conn)]
    return {"order": names}


def _renumber_groups(conn):
    for i, g in enumerate(_groups(conn)):
        conn.execute("UPDATE category_group SET sort_order = ? WHERE id = ?", ((i + 1) * 10, g["id"]))


def delete_group(conn, name, move_to=None, group_id=None, move_to_id=None):
    """Delete a spending bucket. Its categories move to `move_to` first, which
    is required while it has any. Income and Transfers cannot be deleted."""
    with db.transaction(conn):
        g = _group(conn, name, group_id, named=True)
        name = g["name"]
        if g["kind"] != "spend":
            raise CategoryRefused(f"{name} is not a spending bucket, so it cannot be deleted")
        members = _members(conn, g["id"])
        moved = 0
        if members:
            if not move_to and move_to_id is None:
                raise CategoryRefused(f"{name} holds {len(members)} "
                                      f"categor{'y' if len(members) == 1 else 'ies'}: "
                                      "choose the bucket they move to")
            target = _group(conn, move_to, move_to_id, named=True)
            if target["id"] == g["id"]:
                raise CategoryRefused("Choose a different bucket to move them to")
            for c in members:
                _place(conn, _category(conn, category_id=c["id"]), target)
                moved += 1
        conn.execute("DELETE FROM category_group WHERE id = ?", (g["id"],))
        _renumber_groups(conn)
        _renumber(conn)
    return {"deleted": name, "id": g["id"], "moved": moved}


UNSET = object()


def set_group_style(conn, name=None, group_id=None, icon=UNSET, colour=UNSET):
    """G17 (U1): a bucket's icon (one of BUCKET_ICONS) and colour (one of
    SERIES_COLOURS). One left out is kept; None puts back the default (its
    kind's icon, the colour by order). Returns the bucket's style, and in
    `swapped` the name of the bucket it traded colours with, or None.

    G18: no two buckets share a colour. Picking the colour another bucket is
    drawn in (its own choice or its place's) swaps the two: that bucket is
    given, as its own choice, the colour this one was drawn in, in the same
    write. No other bucket changes colour: one whose colour came from its
    place, and that the pick would move, keeps the colour it had as its own."""
    if icon is not UNSET and icon is not None and icon not in BUCKET_ICONS:
        raise CategoryRefused(f"Icon is one of {', '.join(BUCKET_ICONS)}")
    if colour is not UNSET and colour is not None and colour not in SERIES_COLOURS:
        raise CategoryRefused(f"Colour is one of {', '.join(SERIES_COLOURS)}")
    swapped = None
    with db.transaction(conn):
        g = _group(conn, name, group_id)
        before = shown_colours(_groups(conn))
        moves = {g["id"]}                          # the buckets this write may recolour
        if colour is not UNSET and colour is not None:
            # the picked bucket is the only one saved with its colour, so it is drawn in it: past twelve
            # buckets, where colours come round again, another may have saved it too and gives it up
            conn.execute("UPDATE category_group SET colour = NULL WHERE colour = ? AND id != ?", (colour, g["id"]))
            holder = next((h for h in _groups(conn) if h["id"] != g["id"] and before[h["id"]] == colour), None)
            if holder is not None:
                conn.execute("UPDATE category_group SET colour = ? WHERE id = ?", (before[g["id"]], holder["id"]))
                swapped = holder["name"]
                moves.add(holder["id"])
        for column, value in (("icon", icon), ("colour", colour)):
            if value is not UNSET:
                conn.execute(f"UPDATE category_group SET {column} = ? WHERE id = ?", (value, g["id"]))
        if colour is not UNSET:
            # a colour freed or taken can move a bucket that waited for one (its place's was taken):
            # it keeps the one it had. Each round fixes at least one, so it ends within a round a bucket.
            # Never the picked colour: past twelve buckets another may have been drawn in it too, and
            # saving it there would take it from the pick
            for _ in range(len(before)):
                after = shown_colours(_groups(conn))
                drift = [k for k in before if k not in moves and after.get(k) != before[k] and before[k] != colour]
                if not drift:
                    break
                for k in drift:
                    conn.execute("UPDATE category_group SET colour = ? WHERE id = ?", (before[k], k))
        rows = _groups(conn)
        g = next(r for r in rows if r["id"] == g["id"])
        out = {"id": g["id"], "name": g["name"], **style(g, shown_colours(rows)), "swapped": swapped}
    return out


# ---------------------------------------------------------------- categories

def add_category(conn, name, group, need_class=None, group_id=None):
    """A new category at the end of a bucket. Its need class defaults to what
    the bucket's kind takes first (Want for spending)."""
    name = _clean(name, "category")
    with db.transaction(conn):
        g = _group(conn, group, group_id)
        _free_name(conn, "category", name)
        need = need_class or DEFAULT_NEED_CLASS[g["kind"]]
        if need not in NEED_CLASSES[g["kind"]]:
            raise CategoryRefused(f"A category in {g['name']} is one of {', '.join(NEED_CLASSES[g['kind']])}")
        # the name now means this category, not an old one it used to stand for
        conn.execute("DELETE FROM category_alias WHERE name = ?", (name,))
        cid = conn.execute("""INSERT INTO category(name, grp, need_class, group_id, sort_order)
                              VALUES (?, 'Variable', ?, ?, 1e9)""",
                           (name, need, g["id"])).lastrowid
        _renumber(conn)
    return {"name": name, "id": cid}


def set_need_class(conn, name, need_class, category_id=None):
    """Need or Want for spending, Transfer, Investment or Savings for a
    transfer; an income category is always Income."""
    with db.transaction(conn):
        c = _category(conn, name, category_id)
        name = c["name"]
        kind = c["kind"] or db.KIND_OF_BUCKET.get(c["bucket"], "spend")
        if need_class not in NEED_CLASSES[kind]:
            # no "none": a load fills a blank class from the shipped list, so a
            # cleared class would quietly come back
            raise CategoryRefused(f"need class for {name} is one of {', '.join(NEED_CLASSES[kind])}")
        conn.execute("UPDATE category SET need_class = ? WHERE id = ?", (need_class, c["id"]))
    return {"name": name, "id": c["id"], "need_class": need_class}


def move_category(conn, name, group, index=None, category_id=None, group_id=None):
    """Move a category into a bucket (its own, to reorder it) at position
    `index` among that bucket's categories, or last."""
    with db.transaction(conn):
        c = _category(conn, name, category_id, named=True)
        g = _group(conn, group, group_id, named=True)
        others = [m["id"] for m in _members(conn, g["id"]) if m["id"] != c["id"]]
        at = len(others) if index is None else max(0, min(len(others), int(index)))
        _place(conn, c, g)
        _renumber(conn, {g["id"]: others[:at] + [c["id"]] + others[at:]})
    return {"name": c["name"], "id": c["id"], "group": g["name"], "group_id": g["id"], "index": at}


def _follow_rules(conn, mapping, summary):
    """Point the personal rules at a renamed or deleted category's new name,
    after the record has committed the change, as one write the Rules page
    can undo. The old name is an alias by then, so the rules load at every
    moment: before this, during it (a check that runs alongside reads either
    name), after an undo, and if it fails, when nothing is written and the old
    name keeps working. Returns (files rewritten, why not or "")."""
    try:
        return rules_app.rewrite_categories(conn, mapping, summary), ""
    except (rules_app.RuleRefused, OSError) as e:
        return 0, f"your rules still say {', '.join(mapping)}, which keeps working: {e}"


def rename_category(conn, name, new_name, category_id=None):
    """Rename a category. Its rows and budgets follow by id; the old name
    stays as an alias; then the personal rules that name it are rewritten.
    G17: with an id, `name` (the name the page read) is optional."""
    new_name = _clean(new_name, "category")
    with db.transaction(conn):
        c = _category(conn, name, category_id)
        name = c["name"]
        _free_name(conn, "category", new_name, but=c["id"])
        if new_name == name:
            return {"name": name, "id": c["id"], "rule_files": 0, "rules_note": ""}
        conn.execute("UPDATE category SET name = ? WHERE id = ?", (new_name, c["id"]))
        conn.execute("DELETE FROM category_alias WHERE name = ?", (new_name,))
        conn.execute("INSERT OR REPLACE INTO category_alias(name, category_id, how) VALUES (?, ?, 'rename')",
                     (name, c["id"]))
    files, note = _follow_rules(conn, {name: new_name}, f"Renamed category {name} to {new_name}")
    return {"name": new_name, "id": c["id"], "rule_files": files, "rules_note": note}


def _nothing_to_move(conn, cat):
    """True when no transaction, budget or rule (shared or personal, under its
    name or an old one) points at the category. A rules folder that cannot be
    read counts as something to move: the safe answer."""
    if conn.execute("SELECT 1 FROM txn WHERE category_id = ? LIMIT 1", (cat["id"],)).fetchone():
        return False
    if conn.execute("SELECT 1 FROM budget WHERE category_id = ? AND planned_minor IS NOT NULL LIMIT 1", (cat["id"],)).fetchone():
        return False
    ids = db.category_ids(conn)
    try:
        return not any(ids.get(n) == cat["id"] for _, _, n in rules_app.rule_categories())
    except (OSError, SystemExit):
        return False


def delete_category(conn, name, move_to, category_id=None, move_to_id=None):
    """Delete a category. Its transactions, budgets and personal rules move to
    `move_to`, which is required; a month both have a budget for keeps the
    sum. The record is copied to backups/ first; the rules change is in the
    Rules page's undo history. A category nothing points at (no transactions,
    no budgets, no rule naming it) needs no `move_to` and is simply removed."""
    c = _category(conn, name, category_id, named=True)
    name = c["name"]
    if not move_to and move_to_id is None:
        if not _nothing_to_move(conn, c):
            raise CategoryRefused("Choose the category its transactions, rules and budgets move to")
        # Nothing moves, but the name is still kept in category_alias so the shipped list does not bring it
        # back; an alias needs a category to name, and only a rule that names this one later would reach it:
        # the first other category in its bucket.
        other = conn.execute("""SELECT id, name FROM category WHERE id != ?
                                ORDER BY group_id IS NOT ?, COALESCE(sort_order, 1e9), name LIMIT 1""",
                             (c["id"], c["group_id"])).fetchone()
        if not other:
            raise CategoryRefused("It is the only category: add another before deleting it")
        move_to, move_to_id = other["name"], other["id"]
    target = _category(conn, move_to, move_to_id, named=True)
    if target["id"] == c["id"]:
        raise CategoryRefused("Choose a different category to move them to")
    path = conn.execute("PRAGMA database_list").fetchone()["file"]
    if path:                              # an in-memory record (a test) has no file to copy
        backup_record(conn, path, "before-category-delete")
    with db.transaction(conn):
        moved = conn.execute("UPDATE txn SET category_id = ? WHERE category_id = ?",
                             (target["id"], c["id"])).rowcount
        own = conn.execute("SELECT month, planned_minor FROM budget WHERE category_id = ?",
                           (c["id"],)).fetchall()
        for b in own:
            conn.execute("""INSERT INTO budget(category_id, month, planned_minor) VALUES (?,?,?)
                            ON CONFLICT(category_id, month) DO UPDATE SET planned_minor =
                              CASE WHEN budget.planned_minor IS NULL THEN excluded.planned_minor
                                   WHEN excluded.planned_minor IS NULL THEN budget.planned_minor
                                   ELSE budget.planned_minor + excluded.planned_minor END""",
                         (target["id"], b["month"], b["planned_minor"]))
        # G17 (T7): its own budget rows go with it (ON DELETE CASCADE), once copied above
        budgets = len(own)
        # its old names, and its own, now send rules to the target; for the
        # stats the category is gone, so a stat looking for it finds nothing
        conn.execute("UPDATE category_alias SET category_id = ?, how = 'delete' WHERE category_id = ?",
                     (target["id"], c["id"]))
        conn.execute("INSERT OR REPLACE INTO category_alias(name, category_id, how) VALUES (?, ?, 'delete')",
                     (name, target["id"]))
        conn.execute("DELETE FROM category WHERE id = ?", (c["id"],))
        _renumber(conn)
    files, note = _follow_rules(conn, {name: target["name"]},
                                f"Deleted category {name}; its rules moved to {target['name']}")
    return {"deleted": name, "id": c["id"], "moved_to": target["name"], "moved_to_id": target["id"],
            "transactions": moved,
            "budgets": budgets, "rule_files": files, "rules_note": note}
