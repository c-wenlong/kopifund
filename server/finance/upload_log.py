"""The upload history: every upload, check, load and mirror the app runs,
grouped into attempts, with what went in and what came out.

An attempt is one visit to the Upload page: the page asks for one with
start() before its first step and sends its id with every step after. A load
started from another page (Rules, Transactions) with no attempt gets an
attempt of its own, so every write to the record leaves a trace. Nothing here
is ever deleted; the steps are small JSON, and a check's list of rows is
capped the same way the page caps it.

G13: an upload step's input says whose the folder was (`folder_from`): the
statement's own text (server/finance/sniff.py) or the owner's choice. A PDF the
text could not place is not a step until the owner names its account, since
nothing was kept for it.

G13: the app never shows a full account number. serve.py masks the owner's
numbers in each step as it is kept, and again in what it serves, so an entry
kept before it did is masked too without being rewritten.
"""
import json
from datetime import datetime, timezone

from . import db

STEPS = ("upload", "check", "load", "mirror")
ORIGINS = ("upload", "rules", "transactions", "app")
# G17: how a step can end (serve.py, _run_step, and logged below); server/finance/types.py types it from here
STATUSES = ("ok", "duplicate", "refused", "problems", "blocked", "failed", "error")


def now():
    """G18: to the microsecond, so a step and a load in the same second keep their order
    (server/finance/home.py, upload_state); rows kept before carry whole seconds and still read."""
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def start(conn, origin="upload"):
    """A new attempt; returns its id."""
    origin = origin if origin in ORIGINS else "app"
    with db.transaction(conn):
        cur = conn.execute("INSERT INTO upload_attempt(origin, started_at) VALUES (?, ?)", (origin, now()))
    return cur.lastrowid


def attempt_for(conn, attempt, origin="app"):
    """The attempt a step belongs to: the one named, when it exists, else a new one."""
    try:
        wanted = int(attempt or 0)
    except (TypeError, ValueError):
        wanted = 0
    if wanted and conn.execute("SELECT 1 FROM upload_attempt WHERE id = ?", (wanted,)).fetchone():
        return wanted
    return start(conn, origin)


def record(conn, attempt_id, step, started_at, status, summary="", input=None, output=None, scrub=None):
    """One finished step, whatever its outcome. `scrub`, when given, is applied
    to the summary, the input and the output before they are kept (G13: the
    app masks the owner's account numbers, serve.py)."""
    if step not in STEPS:
        raise ValueError(f"unknown step {step!r}")
    if scrub is not None:
        summary, input, output = scrub(summary), scrub(input or {}), scrub(output or {})
    with db.transaction(conn):
        conn.execute(
            """INSERT INTO upload_step(attempt_id, step, started_at, finished_at, status, summary, input, output)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (attempt_id, step, started_at, now(), status, summary,
             json.dumps(input or {}, default=str), json.dumps(output or {}, default=str)))


def attempts(conn, before=None, limit=50, offset=0):
    """One page of attempts, newest first, each with its steps' outline (no
    inputs or outputs), `total`, how many attempts there are, and `next`: the
    `before` that fetches the page after this one, or None at the oldest.
    G13: the Past uploads page pages by `offset` and `limit` (it shows page
    numbers, which need the total). An attempt the page started but never
    used is not history, so it is left out before the page is cut."""
    used = "EXISTS (SELECT 1 FROM upload_step s WHERE s.attempt_id = a.id)"
    rows = conn.execute(
        f"""SELECT id, origin, started_at FROM upload_attempt a WHERE {used} AND a.id < ?
            ORDER BY id DESC LIMIT ? OFFSET ?""",
        (int(before) if before else 2**62, limit + 1, max(0, int(offset)))).fetchall()
    total = conn.execute(f"SELECT COUNT(*) FROM upload_attempt a WHERE {used}").fetchone()[0]
    page = rows[:limit]
    out = []
    for a in page:
        steps = [dict(s) for s in conn.execute(
            """SELECT id, step, started_at, finished_at, status, summary FROM upload_step
               WHERE attempt_id = ? ORDER BY id""", (a["id"],))]
        out.append({**dict(a), "steps": steps})
    return {"attempts": out, "next": page[-1]["id"] if len(rows) > limit else None, "total": total}


def attempt(conn, attempt_id):
    """One attempt with every step's input and output, or None."""
    a = conn.execute("SELECT id, origin, started_at FROM upload_attempt WHERE id = ?", (attempt_id,)).fetchone()
    if a is None:
        return None
    steps = []
    for s in conn.execute("SELECT * FROM upload_step WHERE attempt_id = ? ORDER BY id", (attempt_id,)):
        d = dict(s)
        d["input"], d["output"] = json.loads(d["input"]), json.loads(d["output"])
        del d["attempt_id"]
        steps.append(d)
    return {**dict(a), "steps": steps}


# ---------------------------------------------------------------- one step

class Refusal(Exception):
    """A step that said no for a reason the user can act on (a refused upload)."""


def logged(conn, attempt_id, step, input, run, describe, refusals=(), scrub=None):
    """Run one step and record it, through `scrub` (see record) when given.
    `describe(result)` gives (status, summary, output). A refusal (an exception in `refusals`) is recorded as refused, any
    other exception as error, and both are raised again for the caller. A step
    that finished but could not be described is still recorded, as an error
    that says the step itself ran, and its result is returned: its work (a load
    may have written the record) is done either way."""
    started = now()
    try:
        result = run()
    except refusals as e:
        record(conn, attempt_id, step, started, "refused", str(e), input, {"error": str(e)}, scrub)
        raise
    except Exception as e:
        record(conn, attempt_id, step, started, "error", f"{type(e).__name__}: {e}", input,
               {"error": f"{type(e).__name__}: {e}"}, scrub)
        raise
    try:
        status, summary, output = describe(result)
    except Exception as e:
        status, summary = "error", f"the {step} finished, but its record could not be written: {type(e).__name__}: {e}"
        output = {"error": summary}
    record(conn, attempt_id, step, started, status, summary, input, output, scrub)
    return result


def check_summary(report):
    p = len(report["problems"])
    return (f"{report['statements']} statements, {report['rows']:,} rows: {report['to_add_count']} to add, "
            f"{report['recategorised_count']} recategorised, {report['to_archive_count']} to archive"
            + (f"; {p} problem{'s' * (p != 1)}" if p else ""))
