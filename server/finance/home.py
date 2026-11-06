"""Home (GET /api/overview): four tiles, twelve months of spending by bucket,
this month's coming-up payments and the "Needs your eye" card.

Every figure comes from something another page already shows, so Home and
that page cannot disagree:

  * the chart is the Spending explorer's answer (server/finance/spending.py) over
    every row, home and abroad, split by bucket, and the "Spent in" tile is
    its last bar;
  * Coming up is the Recurring page's list (server/finance/recurring.py `coming_up`);
  * cash, runway and their changes are the savings-runway result, net worth
    over time is server/analytics/growth.py's, and the biggest one-off is the
    one-offs result's;
  * the scam line is the Transactions filter's rows (server/finance/scam_checks.py);
  * G18: the upload line is where the Upload page's latest attempt left the
    month (apps/web/src/uploadState.ts reads the same steps), the needs-a-rule
    count is the Rules page's queue, and a late account is Your profile's
    freshness chip.

G18: `needs_your_eye` also carries its `lines`, most urgent first, each with
its kind, tone, words and link, so Home's card and the sidebar's list say the
same thing. A line whose figure is rows links to exactly those rows on
Transactions; one whose figure is not (bills, groups, accounts) opens its page.

Money leaves as integer cents. A stat's figure is read in cents as the stat
wrote it (G20, money.stat_cents: `<name>_minor`), and a headline figure is the
Metrics row's cents (`Value_minor`), so nothing here turns dollars into cents.
"""
import json
from datetime import date, datetime
from urllib.parse import urlencode

from .dates import add_months
from .metrics import metrics_from
from .net_worth import net_worth

from . import dates, money, pending, profile, recurring, rules_app, scam_checks, spending
from .money import stat_cents
from .spending import SPEND_WHERE

MONTHS_SHOWN = 12
# the recurring statuses that want a look: nothing came, or not the usual amount
OFF = ("late", "different")


def _data(results, stat):
    return ((results or {}).get(stat) or {}).get("data") or {}


def chart_range(conn, results):
    """(from, to) for the chart: up to twelve months ending at the record's
    last month, stepping back over a last month the monthly-cashflow stat
    calls partial (a bank account has not printed it yet), so the last bar and
    the tile it feeds are a whole month. None when the record is empty or
    holds no whole month at all (only card statements, say), since a partial
    month shown as the last bar would read as a whole one."""
    first, last = conn.execute("SELECT MIN(month), MAX(month) FROM v_txn").fetchone()
    if not last:
        return None
    partial = {m.get("month") for m in _data(results, "monthly-cashflow").get("months") or ()
               if m.get("partial")}
    to = last
    while to in partial:
        if to <= first:
            return None
        to = add_months(to, -1)
    return max(first, add_months(to, -(MONTHS_SHOWN - 1))), to


def spending_chart(conn, results):
    """The explorer's figures for the chart: every month's spending per
    bucket, the bucket order that fixes each bucket's colour (G17: unless
    `bucket_style` names one the owner chose), and the totals whose
    `per_month` is the dashed average."""
    span = chart_range(conn, results)
    if span is None:
        return {"by": "bucket", "scope": "all", "bucket_order": [], "bucket_style": {}, "months": [], "series": [],
                "link": {}, "totals": {"spend": 0, "per_month": 0, "overseas": 0}}
    d = spending.explore(conn, {"scope": "all", "by": "bucket", "from": span[0], "to": span[1]})
    return {k: d[k] for k in ("by", "scope", "bucket_order", "bucket_style", "months", "series", "totals", "link")}


def cash_change(results):
    """{change, since}: cash at the last complete month against the month
    before it, when that month was complete too. Else None."""
    rows = [m for m in _data(results, "savings-runway").get("months") or ()
            if m.get("complete", True) and stat_cents(m, "cash") is not None]
    if len(rows) < 2 or add_months(rows[-1]["month"], -1) != rows[-2]["month"]:
        return None
    return {"change": stat_cents(rows[-1], "cash") - stat_cents(rows[-2], "cash"), "since": rows[-2]["month"]}


def net_worth_change(results):
    """{change, since}: net worth now against twelve months before, from the
    same monthly series the Net worth page draws. None with under a year of it."""
    months = net_worth(results or {}).get("months") or []
    if len(months) <= MONTHS_SHOWN:
        return None
    then = months[-1 - MONTHS_SHOWN]
    return {"change": months[-1]["total"] - then["total"], "since": then["month"]}


def biggest_one_off(results, month, conn=None):
    """The largest single payment in `month` far above its category's usual
    (the one-offs stat: five times the category's median and at least its
    floor), with the Transactions query that finds it. None when there is none.

    G13: the query names the payment's own row by its key, found in the record
    `conn` by its day, payee and amount, so the list holds that one payment
    and not every row that mentions the payee. Without the record, or when it
    finds no such row, the query searches the month for the payee's text."""
    rows = [r for r in _data(results, "one-offs").get("one_offs") or () if r.get("month") == month]
    if not rows:
        return None
    r = max(rows, key=lambda x: stat_cents(x, "amount") or 0)
    payee = r.get("payee") or ""
    text = payee if payee and payee != "(no name)" else (r.get("merchant_raw") or r.get("memo") or "")
    category = r.get("category") or ""
    key = _one_off_key(conn, r) if conn is not None else None
    link = {"key": key} if key else {"month": month, "q": text} if text else {
        "month": month, "category": "(none)" if category in ("", "(uncategorised)") else category}
    return {"payee": text or payee or "(no name)", "date": r.get("date"), "category": category,
            "amount": stat_cents(r, "amount"), "multiple": r.get("multiple"), "link": link}


def _one_off_key(conn, r):
    """The key of the row a one-offs entry describes: the payment on that day
    to that payee (as the one-offs stat names it) for that amount. Several identical
    payments on one day are the same figure, so the first stands for them."""
    row = conn.execute(
        f"SELECT txn_key FROM v_txn WHERE {SPEND_WHERE} AND txn_date = ? AND amount_sgd_minor = ? "
        f"AND COALESCE(NULLIF(payee_name, ''), NULLIF(merchant_raw, ''), '(no name)') = ? ORDER BY txn_key LIMIT 1",
        (r.get("date"), -(stat_cents(r, "amount") or 0), r.get("payee") or "(no name)")).fetchone()
    return row[0] if row else None


def needs_your_eye(conn, results, up, folders=(), today=None, listing=(), rec=None):
    """What the "Needs your eye" card is built from, and its `lines`: one for
    each part that is true. `up` is this month's coming-up list; `folders`
    (the config's) and `listing` (the app's statement folders) name and find
    an account as Your profile does (server/finance/profile.py, accounts). `rec` is the
    recurring.Record `overview` built for `up`, read again here rather than rebuilt."""
    needs = conn.execute("""SELECT COUNT(*) FROM v_txn WHERE flow_class = 'External'
                            AND category_id IS NULL AND is_synthetic = 0""").fetchone()[0]
    trend = stat_cents(_data(results, "savings-runway").get("summary"), "trend_6m")
    # G16: the result as the Recurring page reads it, payees loaded since the stat ran read again
    detector = recurring.refreshed(conn, results, rec)
    eye = {
        "needs_category": needs,
        # G16: one line per series, its most urgent payment, the pick the Recurring page's chip
        # shows; bills only: a late or odd transfer to investing is not spending
        "recurring_off": [{"id": r["id"], "payee": r["payee"], "name": r["name"], "status": r["status"],
                           "expected": r["expected"]}
                          for r in recurring.most_urgent(up["rows"]) if r["status"] in OFF and not r["investing"]],
        "one_off": biggest_one_off(results, up["month"], conn),
        "cash_trend_6m": trend,
        "irregular": len((detector.get("flag_mismatches") or {}).get("flagged_not_detected") or ()),
        "scam": scam_line(conn, results, rec),
        # G18: what other pages flag, gathered here for the sidebar's list
        "upload": upload_state(conn),
        "needs_rule": rules_app.needs_rule_count(conn),
        "missing_fx": conn.execute("SELECT COUNT(*) FROM v_missing_fx").fetchone()[0],
        "stale_accounts": stale_accounts(conn, today or date.today(), folders, listing),
        # G25: phone rows still waiting for a statement to confirm them
        "pending": pending.waiting_count(conn, today),
    }
    eye["lines"] = eye_lines(eye)
    return eye


# ---------------------------------------------------------------- G18: the lines

# Every kind of line, in the order they are listed: money sent to a scammer
# first, then an upload that stopped short (the record is behind until it is
# dealt with), then what the figures cannot count yet (rows with no category,
# bills that did not come as usual, rows with no SGD value), then payments the
# scam checks flag for a reason that is no scam (`flagged`: the scam checks
# have one line, `scam` when any flag points at a scam), then the rest.
EYE_KINDS = ("scam", "upload", "needs_category", "recurring_off", "missing_fx", "flagged", "needs_rule", "one_off",
             "cash_trend", "stale_accounts", "pending", "irregular")
EYE_TONES = ("danger", "warn")
# the same rows the count has: External, no category, not made up by the pipeline (the list hides transfers on its own)
NEEDS_CATEGORY_LINK = "/transactions?category=(none)&synthetic=0&hide_transfers=1"
# hide_transfers=0: a saved "Hide transfers" would leave out the very rows flagged
SCAM_LINK = "/transactions?flag=scam&hide_transfers=0"
# what a flag that is no scam (scam_checks.SCAMS) is, in a few words
FLAG_WORDS = {"test_charge": "a small first charge then a larger one",
              "twice": "charged more than once that day",
              "new_subscription": "a new monthly payment"}
# the rows v_missing_fx holds, transfers included: every SGD figure leaves each out
MISSING_FX_LINK = "/transactions?missing_fx=1&hide_transfers=0"


def _txn_link(query):
    return "/transactions?" + urlencode(query, safe="(),*")


def series_link(sid):
    """G16: one series' own view on Recurring (apps/web/src/pages/Recurring.tsx, seriesPath)."""
    return "/recurring?" + urlencode({"series": sid})


def _n(count, one, many):
    return f"{count} {one if count == 1 else many}"


def _for(months):
    """How long, from whole months: "3 months", "1 year", "2 years"."""
    if months >= 12 and months % 12 == 0:
        return _n(months // 12, "year", "years")
    return _n(months, "month", "months")


def eye_lines(e):
    """The lines, most urgent first: {kind, tone, text, to}, one for each part
    of `e` that is true. A scam line and an upload whose problems stop a
    load are in the danger colour, the rest in the warning colour."""
    out = []

    def line(kind, text, to, tone="warn"):
        out.append({"kind": kind, "tone": tone, "text": text, "to": to})

    s = e["scam"]
    if s and s["scams"]:
        line("scam", scam_words(s), SCAM_LINK, "danger")
    u = e["upload"]
    if u and u["state"] == "problems":
        line("upload", f"The last upload has {_n(u['problems'], 'problem', 'problems')} to fix", "/upload", "danger")
    elif u and u["state"] == "checked":
        line("upload", "The last upload was checked but not loaded", "/upload")
    elif u:
        line("upload", "A statement was uploaded but not checked", "/upload")
    if e["needs_category"] > 0:
        line("needs_category", f"{_n(e['needs_category'], 'transaction needs', 'transactions need')} a category",
             NEEDS_CATEGORY_LINK)
    off = e["recurring_off"]
    if len(off) == 1:
        r = off[0]
        # G16: by its series' name, so one of a payee's several series is told apart, opening that series
        line("recurring_off", f"{r['name']} is late" if r["status"] == "late"
             else f"{r['name']} came in at a different amount", series_link(r["id"]))
    elif off:
        line("recurring_off", f"{len(off)} recurring bills late or a different amount", "/recurring")
    if e["missing_fx"]:
        # a filter of its own: there can be thousands of them, too many keys for an address
        line("missing_fx", f"{_n(e['missing_fx'], 'transaction has', 'transactions have')} no SGD value",
             MISSING_FX_LINK)
    if s and not s["scams"]:
        line("flagged", flag_words(s), SCAM_LINK)
    if e["needs_rule"]:
        # groups of rows by their text, as the Rules page's queue counts them: a page link
        line("needs_rule", f"{_n(e['needs_rule'], 'statement text needs', 'statement texts need')} a rule", "/rules")
    if e["one_off"]:
        o = e["one_off"]
        # half up, as the page rounded it (Math.round), not to even
        times = f", {int(o['multiple'] + 0.5)}x the usual for {o['category']}" if o["multiple"] else ""
        line("one_off", f"{money.whole(o['amount'])} at {o['payee']} on {dates.day_label(o['date'])}{times}",
             _txn_link(o["link"]))
    if e["cash_trend_6m"] is not None and e["cash_trend_6m"] < 0:
        line("cash_trend", f"Cash down {money.whole(-e['cash_trend_6m'])} over six months", "/net-worth")
    stale = e["stale_accounts"]
    if len(stale) == 1:
        line("stale_accounts", f"No statement from {stale[0]['name']} for {_for(stale[0]['months'])}", "/profile")
    elif stale:
        # most first, so the first is the longest
        up_to = "" if stale[0]["months"] == stale[-1]["months"] else "up to "
        line("stale_accounts", f"No statement from {len(stale)} accounts for {up_to}{_for(stale[0]['months'])}",
             "/profile")
    # G25: waiting is the normal state until a statement lands, so it sits with
    # the quiet lines; the Settings card is where a row is dismissed
    if e["pending"]:
        line("pending", f"{_n(e['pending'], 'spend from the phone waits', 'spends from the phone wait')} "
             "for a statement", "/settings?phone_rows=waiting")
    if e["irregular"] > 0:
        line("irregular", f"{_n(e['irregular'], 'payee marked recurring has', 'payees marked recurring have')} "
             "no regular spacing", "/recurring")
    return out


def scam_words(s):
    """The scam line: every flagged row is counted, as its link lists them,
    and the one named is a scam (scam_line picks it)."""
    paid, day = f"{money.whole(s['amount'])} to {s['payee']}", dates.day_label(s["date"])
    if s["count"] == 1:
        return f"{paid} on {day} looks like a scam"
    if s["scams"] == s["count"]:
        return f"{s['count']} payments look like a scam, the latest {paid}"
    if s["scams"] == 1:
        return f"{s['count']} flagged payments, one looks like a scam: {paid} on {day}"
    return f"{s['count']} flagged payments, {s['scams']} look like a scam, the latest {paid}"


def flag_words(s):
    """The line for flags that are no scam, saying what the newest one is."""
    paid, day = f"{money.whole(s['amount'])} to {s['payee']}", dates.day_label(s["date"])
    what = FLAG_WORDS.get(s["code"], s["reason"])
    if s["count"] > 1:
        return f"{s['count']} payments to look at, the latest {paid} ({what})"
    if s["code"] == "new_subscription":
        return f"New monthly payment to {s['payee']}, first charged {money.whole(s['amount'])} on {day}"
    return f"{paid} on {day}, {what}"


UPLOAD_STATES = ("problems", "checked", "added")


def upload_state(conn):
    """G18: where the latest upload left the month, when it wants something
    done: {id, state, problems}, else None. The steps are read the way the
    Upload page reads them back (apps/web/src/uploadState.ts, seedFrom): the two
    latest attempts, the newest check or load decides, a step that errored is
    no state, and a statement added after both means nothing has checked it.
    `state` is "problems" (the check found `problems` that stop a load, or a
    load was refused for them), "checked" (it passed with rows to add, and no
    load came after) or "added" (a statement uploaded and not checked). `id`
    is the attempt that left it.

    A load by any route after that step settles it: every load writes a
    sync_run row (server/finance/db.py, load_parsed), the app's and `make load`'s
    alike, and a load parses every folder, refuses while a problem stands, and
    takes in whatever was uploaded. So a later sync_run means nothing is left
    to do, even when the Upload page never saw that load."""
    from .monthly import blocking
    # two, as the page reads: a visit that only mirrored has no check of its own
    ids = [r[0] for r in conn.execute(
        """SELECT id FROM upload_attempt a WHERE EXISTS (SELECT 1 FROM upload_step s WHERE s.attempt_id = a.id)
           ORDER BY id DESC LIMIT 2""")]
    if not ids:
        return None
    steps = [{**dict(s), "output": json.loads(s["output"] or "{}")} for s in conn.execute(
        f"SELECT id, attempt_id, step, status, finished_at, output FROM upload_step WHERE attempt_id IN "
        f"({', '.join('?' * len(ids))}) ORDER BY id", ids)]

    def last(name, ok=lambda s: True):
        return next((s for s in reversed(steps) if s["step"] == name and ok(s)), None)

    check, load = last("check"), last("load")
    # the newest of each kind decides: a check that errored has no report, and the one before it is not the state any more
    if check and not isinstance(check["output"].get("problems"), list):
        if load and load["id"] < check["id"]:
            load = None
        check = None
    if load and not isinstance(load["output"].get("checked"), dict):
        load = None
    added = last("upload", lambda s: s["status"] == "ok")
    if added and added["id"] > max(load["id"] if load else 0, check["id"] if check else 0):
        found = added, "added", 0
    elif load and (not check or load["id"] > check["id"]):
        if load["status"] == "ok":
            return None
        found = load, "problems", len(blocking({"problems": load["output"].get("problems") or []}))
    elif check and blocking(check["output"]):
        found = check, "problems", len(blocking(check["output"]))
    elif check and (check["output"].get("to_add_count") or 0) > 0:
        found = check, "checked", 0
    else:
        return None
    step, state, stop = found
    if _loaded_since(conn, step["finished_at"]):
        return None
    return {"id": step["attempt_id"], "state": state, "problems": stop}


def _loaded_since(conn, stamp):
    """True when a load ran after `stamp`: a sync_run that started later than
    the step ended. New stamps carry microseconds (upload_log.now,
    db.now_fine) and order exactly; a row kept before them has whole seconds,
    and then a load that started in the second the step ended counts as after
    it, since a load cannot start before the check it follows has ended."""
    # only the runs from that second on: the first 19 characters are the second in either form
    runs = [r[0] for r in conn.execute("SELECT started_at FROM sync_run WHERE status = 'ok' AND "
                                       "substr(started_at, 1, 19) >= substr(?, 1, 19)", (stamp,))]
    return any(_after(run, stamp) for run in runs)


def _after(run, step):
    """Whether a load stamped `run` started after a step stamped `step` ended."""
    a, b = datetime.fromisoformat(run), datetime.fromisoformat(step)
    if run[19:20] == "." and step[19:20] == ".":
        return a > b
    return a.replace(microsecond=0) >= b.replace(microsecond=0)


def stale_accounts(conn, today, folders=(), listing=()):
    """G18: the accounts Your profile marks as missing statements (Profile.tsx,
    freshness): the latest statement or row is before last month, or for CPF,
    which prints a year at a time, before last year. Each is {name, last,
    months}: its last date and how many months of statements are missing,
    most first. An account still waiting for its first statement is not late."""
    want = add_months(today.isoformat()[:7], -1)
    out = []
    for a in profile.accounts(conn, folders, listing):
        last = a["last"]
        if not last:
            continue
        if a["bank"] == "cpf":
            months = 12 * max(0, today.year - 1 - int(last[:4]))
        else:
            months = max(0, (int(want[:4]) - int(last[:4])) * 12 + int(want[5:7]) - int(last[5:7]))
        if months:
            out.append({"name": a["name"], "last": last, "months": months})
    return sorted(out, key=lambda a: (-a["months"], a["name"]))


def scam_line(conn, results=None, rec=None):
    """G15: how many payments the scam checks flag (`count`), how many of
    those for a reason that points at a scam (`scams`, scam_checks.SCAMS),
    and the one Home names: the newest scam, else the newest flag, with the
    `code` and `reason` it is named for. None when none is flagged."""
    rows = scam_checks.flags(conn, results=results, rec=rec)
    if not rows:
        return None

    def scam(r):
        return next((x for x in r["reasons"] if x["code"] in scam_checks.SCAMS), None)

    scams = [r for r in rows if scam(r)]
    r = (scams or rows)[0]
    why = scam(r) or r["reasons"][0]
    return {"count": len(rows), "scams": len(scams), "payee": r["payee"], "date": r["date"], "amount": r["amount"],
            "code": why["code"], "reason": why["text"]}


def overview(conn, results, today=None, folders=(), listing=()):
    """GET /api/overview: everything Home draws, and the sidebar's holdings
    and Needs your eye, whose `folders` and `listing` these are."""
    today = today or date.today()
    metrics = {m["Name"]: m for m in metrics_from(results)} if results else {}

    # G20: a Metrics row of money holds its cents as `Value_minor`, any other `Value`
    def metric(name):
        return (metrics.get(name) or {}).get("Value")

    def cents(name):
        return (metrics.get(name) or {}).get("Value" + money.MINOR)

    runway = (_data(results, "savings-runway").get("summary") or {})
    worth = cents("Net worth, including CPF") or cents("Net worth, cash plus investments")
    cash = cents("Cash, all accounts")
    headline = {
        # a change only beside a figure: cash alone builds a net worth series,
        # but with no investments or CPF there is no net worth tile to put it under
        "net_worth_incl_cpf": worth,
        "net_worth_change": net_worth_change(results) if worth is not None else None,
        "cash": cash,
        "cash_change": cash_change(results) if cash is not None else None,
        "runway_months": metric("Runway, months"),
        "runway_range": ([runway["rule_low"], runway["rule_high"]]
                         if runway.get("rule_low") is not None and runway.get("rule_high") is not None else None),
    }
    holdings = [{"label": label, "value": cents(name)}
                for label, name in (("Cash", "Cash, all accounts"),
                                    ("Investments", "Investments, market value"),
                                    ("CPF", "CPF, all three accounts"))
                if cents(name) is not None]
    missing_fx = conn.execute("SELECT COUNT(*) FROM v_missing_fx").fetchone()[0]
    last = conn.execute("SELECT MAX(txn_date) FROM v_txn").fetchone()[0]
    # Audit: one Record for every reader of the recurring result, so the detector runs
    # once per group for this answer
    rec = recurring.record(conn, results)
    up = recurring.coming_up(conn, results, today, rec)
    return {"as_of": last, "headline": headline, "holdings": holdings, "missing_fx": missing_fx,
            "spending": spending_chart(conn, results), "coming_up": up,
            "needs_your_eye": needs_your_eye(conn, results, up, folders, today, listing, rec)}
