"""What the app's JSON endpoints answer, as plain functions over the record.

serve.py routes a request here and sends the dict back; nothing in this
module knows about HTTP, so each answer is tested on its own. G19: each GET's
answer is a `get_<name>(req)` near the end (the writes' are in
server/finance/api_writes.py, Net worth's in server/analytics/api.py), and serve.py's route
table names it; `req` is the route's call (serve.Request): its query, its
arguments, and the one connection to the record it may open. Money taken from
the record leaves as integer cents. A figure taken from a stat result is
read in cents by `money.stat_cents` (G20: `<name>_minor`), so the browser
only ever sees one kind of money; a whole result (GET /api/results/<id>) goes
as the stat wrote it, in cents. A result from before G20, in dollars, is no
result (server/finance/results_cache.py, `money.in_cents`) until the stats run again.
"""
import json
import re
from contextlib import closing
from datetime import date
from typing import Iterator, NamedTuple

from . import account_names, app_icon, db, home, results_cache, row_text, scam_checks, stats_rebuild
from .errors import NotFound
from .countries import flag
from .spending import INCOME_WHERE, PAYEE_OF, SPEND_WHERE, TRANSFER_ROWS_WHERE, per_month

MAX_LIMIT = 500

# G13: a transfer on Transactions, which "Hide transfers" leaves out: money
# moving between the owner's own accounts and wallets (a top-up, an exchange,
# a move between accounts, as the parsers mark the flow), and any row in a
# Transfer category (between own accounts, wallet top-up, investments, person
# to person)
TRANSFER_WHERE = "flow_class LIKE 'Internal%' OR COALESCE(bucket, '') = 'Transfer'"


# ---------------------------------------------------------------- overview

# G13: Home's answer lives in server/finance/home.py
overview = home.overview


# ---------------------------------------------------------------- transactions

def _is_day(text):
    """True for a real calendar day written YYYY-MM-DD."""
    try:
        return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", text)) and date.fromisoformat(text) is not None
    except ValueError:
        return False


def _names(raw, key):
    """A filter's names, sent as a JSON list of strings."""
    try:
        names = json.loads(raw)
    except ValueError:
        names = None
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
        raise ValueError(f"{key} is a JSON list of names")
    return names


def transactions(conn, query, folders=(), results=None):
    """One page of the ledger, newest first, with the filter choices.

    G13: each row's account comes as a short name and its bank (for the
    mark), never with a full account number, and a row with no payee carries
    a plain description of what it is beside the text its statement printed.
    `folders` are the config's statement folders, which help name a bank;
    `results` the stat results the scam flags read the owner's series edits over (G16)."""
    where, args = ["1 = 1"], []
    if query.get("month"):
        where.append("month = ?")
        args.append(query["month"])
    if query.get("category"):
        if query["category"] == "(none)":
            where.append("category_id IS NULL")
        else:
            where.append("category_name = ?")
            args.append(query["category"])
    if query.get("account"):
        # G13: an account by its id in the record: its name can hold its full
        # number, and the filter's choices travel to the browser
        if not str(query["account"]).isdigit():
            raise ValueError("account is an account's id, from this list's accounts")
        where.append("account_id = ?")
        args.append(int(query["account"]))
    # the Spending explorer's links carry its range, its scope and its idea of
    # spending, so the list shows exactly the rows behind the figure clicked
    # G13: a range's ends are months (YYYY-MM, whole months) or days (YYYY-MM-DD, both ends included),
    # so a weekly chart point lists exactly its seven days
    for key, op in (("from", ">="), ("to", "<=")):
        if query.get(key):
            if re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", query[key]):
                where.append(f"month {op} ?")
            elif _is_day(query[key]):
                where.append(f"txn_date {op} ?")
            else:
                raise ValueError(f"{key} is a month, YYYY-MM, or a day, YYYY-MM-DD")
            args.append(query[key])
    if query.get("home") == "1":
        where.append("trip_id IS NULL")
    # G13: the rows a stat adds up, by the bucket it counts: spending (the explorer's rows exactly: a
    # payment with no SGD value has no figure to sit in), income, and transfers (savings). Asking for
    # more than one lists the rows of either, as a net figure adds income and spending
    kinds = [w for flag, w in (("spend", SPEND_WHERE), ("income", INCOME_WHERE),
                               ("transfer", TRANSFER_ROWS_WHERE)) if query.get(flag) == "1"]
    if kinds:
        where.append("(" + " OR ".join(f"({w})" for w in kinds) + ")")
    if query.get("need"):
        # the category's need class (Need, Want, Investment, Savings), one or several comma-separated
        needs = [n for n in str(query["need"]).split(",") if n]
        where.append(f"need_class IN ({', '.join('?' * len(needs)) or 'NULL'})")
        args += needs
    if query.get("bucket"):
        # the explorer's bucket: the category's group (Food, Getting around), "Other" for none
        where.append("COALESCE(category_group, 'Other') = ?")
        args.append(query["bucket"])
    for key, column, negate in (("categories", "category_name", False), ("not_categories", "category_name", True),
                                ("not_payees", PAYEE_OF, True)):
        if query.get(key):
            # names as a JSON list, because a name may hold a comma
            names = _names(query[key], key)
            where.append(f"COALESCE({column}, '') {'NOT ' if negate else ''}IN ({', '.join('?' * len(names)) or 'NULL'})")
            args += names
    if query.get("external") == "1":
        # not a move between the owner's own accounts, whatever "Hide transfers" says
        where.append("flow_class = 'External'")
    if query.get("trip"):
        # G13: a trip on the Trips page opens the rows it counts (with spend=1)
        where.append("trip_name = ?")
        args.append(query["trip"])
    if query.get("synthetic") == "0":
        # G13: Home's "needs a category" link: its count leaves out the rows the pipeline
        # made up (an opening balance, a balancing entry), so the list does too
        where.append("is_synthetic = 0")
    elif query.get("synthetic") == "1":
        # only those rows: the data-quality stat counts them
        where.append("is_synthetic = 1")
    if query.get("missing_fx") == "1":
        # G18: Needs your eye's rows with no SGD value (a block that printed no FX rate), the rows
        # v_missing_fx holds, as one filter: a list of their keys would not fit in an address
        where.append("amount_sgd_minor IS NULL")
    if query.get("hide_transfers") == "1":
        # G13: money moving between the owner's own accounts and wallets
        where.append(f"NOT ({TRANSFER_WHERE})")
    # G15: the rows the scam checks flag, each carrying its reasons
    flagged = {f["key"]: f["reasons"] for f in scam_checks.flags(conn, results=results)}
    if query.get("flag") == "scam":
        where.append(f"txn_key IN ({', '.join('?' * len(flagged)) or 'NULL'})")
        args += list(flagged)
    if query.get("out") == "1":
        # G13: payments only: what left the owner for someone else (an inflow, a refund, or a move between
        # their own accounts is not one), as Recurring counts a payee's payments
        where.append("flow_class = 'External' AND amount_minor < 0")
    if query.get("payee"):
        # G13: exactly this payee, not every row whose text mentions the name; with named=1 only the
        # rows the rules gave that payee (Recurring counts a payee by the name it was given), not the
        # rows with no payee whose bank text happens to read the same
        where.append(f"{'payee_name' if query.get('named') == '1' else PAYEE_OF} = ?")
        args.append(query["payee"])
    if query.get("key"):
        # G13: one row by its key, or several, comma-separated (a key is twelve hex digits)
        keys = [k for k in str(query["key"]).split(",") if k]
        where.append(f"txn_key IN ({', '.join('?' * len(keys)) or 'NULL'})")
        args += keys
    if query.get("q"):
        like = f"%{query['q'].lower()}%"
        where.append("(lower(merchant_raw) LIKE ? OR lower(COALESCE(payee_name, '')) LIKE ? "
                     "OR lower(counterparty) LIKE ? OR lower(memo) LIKE ?)")
        args += [like] * 4
    clause = " AND ".join(where)
    limit = max(1, min(MAX_LIMIT, int(query.get("limit") or 100)))
    offset = max(0, int(query.get("offset") or 0))
    # G13: money in and money out beside the net, for the three tiles above the list
    total, net, money_in, money_out = conn.execute(
        f"SELECT COUNT(*), COALESCE(SUM(amount_sgd_minor), 0), "
        f"COALESCE(SUM(CASE WHEN amount_sgd_minor > 0 THEN amount_sgd_minor END), 0), "
        f"COALESCE(SUM(CASE WHEN amount_sgd_minor < 0 THEN amount_sgd_minor END), 0) FROM v_txn WHERE {clause}",
        args).fetchone()
    # accounts by their display name and bank, as Your profile names them
    # (server/finance/account_names.py), in the order record_names reads them so a
    # name shared by two accounts gets the same masked number everywhere
    known = [dict(r) for r in conn.execute("SELECT name, account_no, institution FROM account ORDER BY name")]
    named = {a["account_no"] or a["name"]: d for a, d in zip(known, account_names.describe(known, folders))}
    own = {a["account_no"]: named[a["account_no"]]["label"] for a in known if a["account_no"]}

    def account(r):
        d = named.get(r["account_no"]) or named.get(r["account_name"] or "")
        if d:
            return {"label": d["label"], "bank": d["bank"]}
        return {"label": account_names.label(r["account_name"], r["account_no"], None, folders),
                "bank": account_names.bank(r["account_name"], r["account_no"], None, folders)}

    def row(r):
        a = account(r)
        payee = r["payee_name"] or ""
        line = {"merchant": r["merchant_raw"], "counterparty": r["counterparty"], "memo": r["memo"],
                "reference": r["reference"], "bank_txn_type": r["bank_txn_type"], "flow_class": r["flow_class"],
                "category": r["category_name"] or "", "currency": r["currency"], "amount": r["amount_minor"],
                "account_no": r["account_no"], "synthetic": r["is_synthetic"]}
        # the printed line stays on hover, with the owner's own account numbers masked
        printed = r["merchant_raw"] or r["counterparty"] or r["memo"] or r["bank_txn_type"]
        return {"key": r["txn_key"], "date": r["txn_date"], "amount": r["amount_minor"],
                "amount_sgd": r["amount_sgd_minor"], "currency": r["currency"],
                "account": a["label"], "bank": a["bank"],
                "merchant": account_names.scrub(printed, own),
                "raw": account_names.scrub(row_text.raw_text(line), own),
                "description": "" if payee else row_text.describe(line, own),
                "payee": payee, "category": r["category_name"] or "",
                "category_group": r["category_group"] or "",
                "flow": r["flow_class"], "trip": r["trip_name"] or "",
                "flags": flagged.get(r["txn_key"], [])}

    rows = [row(r) for r in conn.execute(f"SELECT * FROM v_txn WHERE {clause} ORDER BY txn_date DESC, txn_key "
                                         f"LIMIT ? OFFSET ?", (*args, limit, offset))]
    # G13: each day on this page with its whole day's money out and in under the same
    # filters, counted over all its rows so a day split across two pages shows the same
    # totals on both; `continued` marks a first day that began on the page before,
    # and `continues` a last day that carries on onto the next one
    days = []
    if rows:
        dates = list(dict.fromkeys(r["date"] for r in rows))
        found = {r[0]: r for r in conn.execute(
            f"SELECT txn_date, COUNT(*), "
            f"COALESCE(SUM(CASE WHEN amount_sgd_minor > 0 THEN amount_sgd_minor END), 0), "
            f"COALESCE(SUM(CASE WHEN amount_sgd_minor < 0 THEN amount_sgd_minor END), 0) "
            f"FROM v_txn WHERE {clause} AND txn_date IN ({','.join('?' * len(dates))}) GROUP BY txn_date",
            (*args, *dates))}
        days = [{"date": d, "count": found[d][1], "in_sgd": found[d][2], "out_sgd": found[d][3], "continued": False,
                 "continues": False}
                for d in dates]
        if offset:
            before = conn.execute(f"SELECT txn_date FROM v_txn WHERE {clause} ORDER BY txn_date DESC, txn_key "
                                  f"LIMIT 1 OFFSET ?", (*args, offset - 1)).fetchone()
            days[0]["continued"] = bool(before) and before[0] == dates[0]
        if offset + len(rows) < total:
            after = conn.execute(f"SELECT txn_date FROM v_txn WHERE {clause} ORDER BY txn_date DESC, txn_key "
                                 f"LIMIT 1 OFFSET ?", (*args, offset + len(rows))).fetchone()
            days[-1]["continues"] = bool(after) and after[0] == dates[-1]
    months = [r[0] for r in conn.execute("SELECT DISTINCT month FROM v_txn ORDER BY month DESC")]
    categories = ["(none)"] + [r[0] for r in conn.execute(
        "SELECT name FROM category ORDER BY COALESCE(sort_order, 1e9), name")]
    # the filter sends the account's id; the list shows its short name
    accounts = sorted(({"value": str(r["account_id"]), **{k: account(r)[k] for k in ("label", "bank")}}
                       for r in conn.execute("""SELECT account_id, MIN(account_name) account_name,
                                                       MIN(account_no) account_no FROM v_txn
                                                WHERE account_id IS NOT NULL GROUP BY account_id""")),
                      key=lambda a: a["label"].lower())
    return {"rows": rows, "days": days, "total": total, "sum_sgd": net, "in_sgd": money_in, "out_sgd": money_out,
            "months": months,
            "categories": categories, "accounts": accounts}


def trips(conn, long_stay=None):
    """Every trip with what was spent on it, in cents.

    Spending is what the Spending explorer and every stat count
    (server/finance/spending.py, SPEND_WHERE): a transfer, an income row or a
    synthetic filler linked to the trip is not money spent on it, and a refund
    lowers it. The travel stat counts the same rows, so the two agree.

    A trip longer than `long_stay` days (the travel stat's long_stay_days, 60
    by default) is somewhere lived in, and the page lists it apart."""
    if long_stay is None:
        from .settings import long_stay_days
        long_stay = long_stay_days()
    spent = f"SELECT trip_id, category_name, amount_sgd_minor FROM v_txn WHERE {SPEND_WHERE}"
    top = {}
    for r in conn.execute(f"""SELECT trip_id, category_name, -SUM(amount_sgd_minor) spend
                              FROM ({spent}) WHERE trip_id IS NOT NULL
                              GROUP BY trip_id, category_name
                              ORDER BY trip_id, spend DESC, category_name"""):
        top.setdefault(r["trip_id"], []).append({"name": r["category_name"], "spend": r["spend"]})
    out = []
    # G17 (T9): a pinned key the parser has since changed reads as the row's key now
    codes, pins = db.trip_countries(conn), db.trip_pins(conn)
    for r in conn.execute(f"""SELECT t.id, t.name, t.start_date, t.end_date, t.notes, COUNT(s.trip_id) n,
                                     -COALESCE(SUM(s.amount_sgd_minor), 0) spend
                              FROM trip t LEFT JOIN ({spent}) s ON s.trip_id = t.id
                              GROUP BY t.id ORDER BY t.start_date DESC, t.name"""):
        start, end = r["start_date"] or "", r["end_date"] or ""
        days = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1 if start and end else None
        countries = codes.get(r["id"], [])
        out.append({"id": r["id"], "name": r["name"], "start": start, "end": end, "days": days,
                    "countries": countries, "flags": [flag(c) for c in countries],
                    "notes": r["notes"] or "",
                    "extra_keys": pins.get(r["id"], []),
                    "spend_sgd": r["spend"], "rows": r["n"],
                    # per_month is a whole-cent average in integer arithmetic; here over days
                    "per_day": per_month(r["spend"], days) if days and days > 0 else None,
                    "long_stay": bool(days and days > long_stay),
                    "top_categories": top.get(r["id"], [])[:2]})
    # the record's last transaction day: a trip that starts after it is planned
    last = conn.execute("SELECT MAX(txn_date) FROM txn").fetchone()[0]
    return {"trips": out, "long_stay_days": long_stay, "last_txn": last or ""}


# ------------------------------------------------- G19: what every endpoint reads

class Download(NamedTuple):
    """A file for the browser to save, which a route answers in place of JSON
    (GET /api/exports/<stamp>/<file>)."""
    filename: str
    kind: str
    body: bytes


class Stream(NamedTuple):
    """G22: an answer sent as it happens, one line of JSON per event, which a
    route answers in place of JSON (POST /api/copilot/ask). serve.py writes
    and flushes each line as `events` yields it, and closes the connection
    after the last; a route checks what it must refuse before it returns one,
    since nothing can be refused once the first line is out."""
    events: Iterator[dict]
    kind: str = "application/x-ndjson"


def own_numbers(conn=None, cfg=None):
    """Every account number the record or the config knows as the owner's
    (server/finance/account_names.py, own_numbers), from `conn` and `cfg` when given,
    else from the data folder's own, either of which may not exist yet."""
    numbers = set()
    try:
        if conn is None:
            with closing(open_record()) as c:
                numbers |= account_names.own_numbers(c)
        else:
            numbers |= account_names.own_numbers(conn)
    except NotFound:
        pass
    try:
        numbers |= account_names.own_numbers(cfg=cfg if cfg is not None else config())
    except NotFound:
        pass
    return numbers


def masked(value, keep=(), conn=None, cfg=None):
    """G13: the app never shows a full account number, so each one the record
    or the config knows is masked on the way out. The stats name accounts
    without their numbers, but a result cached before they did still carries
    them until the next rebuild; the upload history keeps what each check,
    load and mirror said, and a step kept before the history was masked still
    holds them. Only those exact numbers: a figure or a date is never touched.
    The numbers come from the record and the config both, so results from a
    Notion or CSV run with no record yet are masked too. `keep` names fields
    the page sends back as they came (account_names.scrub_tree)."""
    return account_names.scrub_tree(value, own_numbers(conn, cfg), keep)


def config_folders():
    """The config's statement folders, or none before there is a config."""
    try:
        return config().folders
    except NotFound:
        return []


def open_record():
    """The existing record, opened; NotFound before the first load. The caller
    closes it: a request goes through serve.Request.record, which does."""
    if not db.db_path().exists():
        raise NotFound("no kopifund.db yet: run `python3 server/finance.py load` first")
    return db.connect()


def config():
    from .model import ConfigMissing, load_config
    try:
        return load_config()
    except ConfigMissing as e:
        raise NotFound(str(e).splitlines()[0]) from e


# ---------------------------------------------------------- the GETs

def get_overview(req):
    from . import monthly
    # G18: the config's statement folders name a late account (Needs your eye) as Your profile does; the
    # PDFs in them are not counted, so the answer still depends on the record and the config alone
    try:
        cfg = config()
        folders, listing = cfg.folders, [{**f, "statements": 0} for f in monthly.folders(cfg)]
    except NotFound:
        folders, listing = (), ()
    return overview(req.record(), results_cache.results(req), folders=folders, listing=listing)


def get_spending(req):
    from . import spending
    try:
        return spending.explore(req.record(), req.query)
    except spending.SpendingRefused as e:
        raise ValueError(str(e)) from e


def get_recurring(req):
    from . import recurring
    return recurring.page(req.record(), results_cache.results(req))


def get_series(req):
    # G16: one series' own view on Recurring
    from . import recurring
    found = recurring.series_view(req.record(), results_cache.results(req), req.query.get("id", ""))
    if found is None:
        raise NotFound("No series has that id now")
    return found


def get_transactions(req):
    # G13: the statement folders name an account and its bank as Your profile does
    return transactions(req.record(), req.query, config_folders(), results_cache.results(req))


def get_trips(req):
    return trips(req.record())


def get_scam_checks(req):
    return scam_checks.page(req.record(), results_cache.results(req))


def get_app_icon(req):
    return app_icon.page(req.maybe_record())


def get_countries(req):
    from . import countries
    return {"countries": countries.listing()}


def get_run_status(req):
    import shutil
    from . import local, monthly
    cfg = req.cfg
    runs = []
    conn = req.maybe_record()
    if conn is not None:
        runs = [dict(r) for r in conn.execute(
            "SELECT kind, started_at, status, inserted, updated, archived FROM sync_run "
            "ORDER BY id DESC LIMIT 5")]
    return {"folders": monthly.folders(cfg), "notion_enabled": local.notion_enabled(cfg),
            "pdftotext": bool(shutil.which("pdftotext")), "runs": runs}


def get_uploads(req):
    from . import upload_log
    conn = req.record()
    q = req.query
    # G13: masked on the way out, so a step kept before the history masked is covered
    return masked(upload_log.attempts(
        conn, q.get("before"), limit=max(1, min(100, int(q.get("limit") or 50))),
        offset=int(q.get("offset") or 0)), conn=conn)


def get_upload(req):
    from . import upload_log
    conn = req.record()
    try:
        found = upload_log.attempt(conn, int(req.args["id"]))
    except ValueError:
        found = None
    if found is None:
        raise NotFound("no such upload attempt")
    return masked(found, conn=conn)


def get_stats_rebuild(req):
    # whether a rebuild after a category edit is still running; the app
    # asks until it is not, then reloads what it shows
    return {"running": stats_rebuild.running()}


def get_budgets(req):
    from . import planning
    try:
        return planning.budgets(req.record(), req.query.get("month") or planning.this_month())
    except planning.PlanRefused as e:
        raise ValueError(str(e)) from e


def get_categories(req):
    from . import categories
    return categories.overview(req.record())


def get_plan(req):
    from . import planning
    conn = req.record()
    # G17: the plan's money crosses as cents (planning.plan_out)
    return {"plan": planning.plan_out(planning.get_plan(conn)), "categories": planning.categories(conn),
            "need_classes": list(planning.NEED_CLASSES), "spend_rates": list(planning.SPEND_RATES)}


def get_setup(req):
    from . import setup
    from .paths import data_root
    # G21: each provider's status says whether a real statement has checked its layout
    keys = ("id", "label", "parser", "wallet", "bank", "title", "status")
    return {"needed": setup.needed(), "data_folder": str(data_root()),
            "providers": [{k: p[k] for k in keys if k in p} for p in setup.providers()]}


def get_profile(req):
    from . import local, monthly, profile
    from .paths import data_root
    cfg = req.cfg
    conn = req.record()
    # G13: accounts by display name, bank and masked number; folders
    # with what the upload picker needs to name them
    folders = [{**f, "statements": len(list((cfg.root / f["path"]).rglob("*.pdf")))}
               for f in monthly.folders(cfg)]
    accounts = profile.accounts(conn, cfg.folders, folders)
    total = conn.execute("SELECT COUNT(*) FROM v_txn").fetchone()[0]
    return {"data_folder": str(data_root()), "notion_enabled": local.notion_enabled(cfg),
            "about": profile.load(), "accounts": accounts, "folders": folders,
            "transactions": total}


def get_exports(req):
    from . import export
    return {"exports": export.listing()}


def get_export_file(req):
    from . import export
    stamp, name = req.args["stamp"], req.args["file"]
    target = export.path_of(stamp, name)
    if target is None:
        raise NotFound("No such export file")
    kind = "text/csv" if target.suffix == ".csv" else "application/json" if target.suffix == ".json" else "text/plain"
    return Download(f"kopifund-{stamp}-{target.name}", kind, target.read_bytes())


def get_suggestions(req):
    from . import profile, suggestions
    # no record yet: nothing to suggest from
    return suggestions.suggestions(req.maybe_record(), results_cache.results(req), profile.load().get("birth_date"))


def get_rules(req):
    from . import rules_app, rules_editor
    conn = req.record()
    from .parsers import categorise
    # G18: the total beside the biggest groups, the count Needs your eye shows too, from one grouping
    needs, total = rules_app.needs_queue(conn)
    return {"needs": needs, "needs_total": total, "categories": rules_app.categories(conn),
            "kinds": list(categorise.KINDS), **rules_editor.overview(conn)}


def get_rules_preview(req):
    from . import rules_editor
    query = req.query
    edit = ({"file": query["edit_file"], "line": query.get("edit_line"), "pattern": query.get("edit_pattern"),
             "revision": query.get("edit_revision")} if query.get("edit_file") else None)
    rule = {k: query.get(k, "") for k in rules_editor.EDITABLE}
    if "notes" in query:
        rule["notes"] = query["notes"]
    return rules_editor.preview(req.record(), {"rule": rule, "edit": edit})


def get_rules_rows(req):
    from . import rules_app
    try:
        return rules_app.group_rows(req.record(), text=req.query.get("text", ""), key=req.query.get("key", ""),
                                    folders=config_folders())
    except rules_app.RuleRefused as e:
        raise ValueError(str(e)) from e


def get_result(req):
    stat_id = req.args["id"]
    results = results_cache.results(req)
    if stat_id not in results:
        raise NotFound(f"no result for {stat_id!r} yet: run `python3 server/finance.py analyse`")
    return results[stat_id]


# ---------------------------------------------------------- G22: the AI card and the copilot

def config_or_none():
    """The config, or None before there is one: the AI card and the copilot
    work from Settings alone."""
    try:
        return config()
    except NotFound:
        return None


def get_ai(req):
    from . import ai
    return ai.view(config_or_none(), req.maybe_record())


def get_ai_models(req):
    from . import ai
    return ai.models(config_or_none(), req.maybe_record(), req.query.get("provider") or None, req.query.get("q", ""))


def get_copilot_threads(req):
    from . import copilot
    return copilot.listing()


def get_copilot_thread(req):
    from . import copilot
    return copilot.load(req.args["id"])


# ---------------------------------------------------------------- G25: the phone link

def get_link_devices(req):
    from . import link
    return {"devices": link.list_devices(), "url": link.tailnet_url()}


def get_link_pending(req):
    """The phone rows the Settings card lists, each named for the paired
    phone that sent it. Loopback only in practice: the link path refuses
    every /api/link/ read but /api/link/devices's own."""
    from . import link, pending
    names = {d["id"]: d["name"] for d in link.list_devices()}
    conn = req.maybe_record()
    return {"rows": pending.rows(conn, names) if conn else []}


# ---------------------------------------------------------------- G26: the dashboard

def get_dashboard_layouts(req):
    from . import dashboard
    return dashboard.layouts(req.maybe_record())


def get_dashboard_layout(req):
    from . import dashboard
    return dashboard.layout(req.maybe_record(), req.args["id"])


def get_dashboard_query(req):
    """A WidgetQuery resolved to labels and series: `?q=` holds the query as
    JSON, so the app's useWidgetData hook gets the ETag caching of every GET."""
    from . import dashboard
    return dashboard.resolve(req.maybe_record(), results_cache.results_view(req),
                             dashboard.query_from_get(req.query))


def get_dashboard_goals(req):
    from . import dashboard
    return dashboard.goals(req.maybe_record(), results_cache.results(req))


def get_dashboard_allocation(req):
    from . import dashboard
    return dashboard.allocation(req.maybe_record(), req.query.get("month"))


def get_dashboard_game_pot(req):
    from . import dashboard
    return dashboard.game_pot(req.maybe_record())
