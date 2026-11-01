"""The copilot's tools (G22): what Ask Kopifund may read, and how it reads it.

Every tool reads; none writes. Each runs on the server over `kopifund.db`
opened read-only (`mode=ro`, so a write raises), and wraps what the app's own
GET endpoints already answer (server/finance/api.py and friends) rather than working
its figures out again, so the copilot and the pages agree. `query` is the one
free-form tool: exactly one SELECT, run not on the record but on a copy of a
few of its tables in memory (SANDBOX) that holds only the columns listed
there, every text in it already through the privacy filter below. A
counterparty, memo or reference line or an account number is not in the
copy at all, so no SQL (hex(), substr(), a join) can turn it into something
the filter would miss. An authorizer lets the statement read and nothing
else; it stops after a few seconds and returns at most 200 rows.

Money is integer cents with its currency, as everywhere in the app. A result
that counts rows carries `link`, the Transactions address that lists exactly
those rows (the same filters the pages' links use, SPEC.md "Exact links");
a balance, an average or an estimate carries none. The panel shows the links
of the rows an answer used: a tool's whole-result link at once, and a trip's
or a recurring payee's when the answer names it.

What the model sees goes through `Context.private` first: every account
number the owner's record knows is masked to its last four digits, the
fields that hold a counterparty, memo or reference line are dropped, and any
counterparty or memo text that is not also a payee, merchant or category name
is replaced, wherever it appears (a payee the explorer named from a
counterparty, a link that carries it). Those lines name people.

Three tools speak to the panel instead of reading: `show_chart` (a chart in
the stats' chart hint shape), `suggest_change` (a confirm card for a rule, a
row's category, a budget or a trip, saved only when the owner presses
Confirm, through the existing write endpoint) and `follow_ups`.
"""
import json
import re
import secrets
import sqlite3
import sys
import time
from datetime import date
from urllib.parse import quote, quote_plus, urlencode

from . import account_names, dates, money

try:
    import resource
except ImportError:              # not on Windows: the time limit and the 1 MB value limit still hold
    resource = None

MAX_ROWS = 200                 # what `query` returns at most
MAX_TXNS = 50                  # what `transactions` lists at most
QUERY_SECONDS = 5
QUERY_MEMORY = 256 * 1024 * 1024   # how far one query may raise the server's peak memory
QUERY_CHARS = 1_000_000        # the text `query` hands back at most, all cells together
CELL_CHARS = 2_000             # one value `query` hands back at most
RESULT_CHARS = 24_000          # a tool result is cut down to about this much JSON
# G22: what one statement on the sandbox may build: a value of 1 MB at most, so
# no hex(randomblob(...)) can grow past it before the row cap applies
QUERY_LIMITS = {sqlite3.SQLITE_LIMIT_LENGTH: 1_000_000, sqlite3.SQLITE_LIMIT_SQL_LENGTH: 100_000,
                sqlite3.SQLITE_LIMIT_COLUMN: 64, sqlite3.SQLITE_LIMIT_EXPR_DEPTH: 100,
                sqlite3.SQLITE_LIMIT_COMPOUND_SELECT: 20, sqlite3.SQLITE_LIMIT_LIKE_PATTERN_LENGTH: 1_000,
                sqlite3.SQLITE_LIMIT_FUNCTION_ARG: 32, sqlite3.SQLITE_LIMIT_ATTACHED: 0}
LINKS_SHOWN = 6

# keys whose value is a counterparty, memo or reference line, a statement's
# printed text that may be one, or an account number: never sent to the model
HIDDEN_KEYS = frozenset({"counterparty", "memo", "reference", "raw", "description", "account_no", "number",
                         "merchant", "rule", "source", "file", "path"})

# G22: what `query` reads: these tables and views of the record, copied into
# memory with only these columns. A counterparty, memo or reference line, an
# account number, a rule, a file and a note are left out; account names (which
# can hold a number) and every other text are copied through the privacy filter.
SANDBOX = {
    "v_txn": ("txn_key", "txn_date", "purchase_date", "month", "account_id", "account_name", "currency",
              "amount_minor", "amount_sgd_minor", "balance_after_minor", "direction", "bank_txn_type",
              "flow_class", "merchant_raw", "merchant_country", "card_last4", "fx_original", "is_synthetic",
              "payee_id", "payee_name", "category_id", "category_name", "bucket", "grp", "need_class",
              "category_group", "category_group_kind", "trip_id", "trip_name", "period_id"),
    "category": ("id", "name", "bucket", "grp", "need_class", "sort_order", "group_id"),
    "category_group": ("id", "name", "kind", "sort_order"),
    "budget": ("category_id", "month", "planned_minor"),
    "v_budget": ("category_id", "category", "month", "planned_minor", "actual_minor"),
    "trip": ("id", "name", "start_date", "end_date"),
    "trip_country": ("trip_id", "code", "position"),
    "payee": ("id", "name", "kind", "country", "recurring"),
    "account": ("id", "name", "institution", "class", "subclass", "base_currency"),
    "v_period": ("id", "account_id", "account_name", "currency", "period_end", "opening_minor", "closing_minor",
                 "total_in_minor", "total_out_minor", "fx_to_sgd_e9", "parser_reconciled", "n_txns",
                 "closing_sgd_minor", "has_balances", "reconciled"),
}
_ALLOWED_ACTIONS = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION,
                    getattr(sqlite3, "SQLITE_RECURSIVE", 33)}

CHART_TYPES = ("bar", "stacked", "line", "scatter", "table")
CHART_UNITS = ("sgd_minor", "count", "percent", "fraction", "months", "days", "number")
CARD_KINDS = ("rule", "category", "budget", "trip")
MONTH = re.compile(r"\d{4}-(0[1-9]|1[0-2])")


class ToolRefused(Exception):
    """A tool call the server will not run as asked, in words the model can act on."""


def href(query):
    """The Transactions address for a filter set. A link lists every row its
    filters match, transfers too, so it says hide_transfers=0 unless it says otherwise."""
    query = {k: str(v) for k, v in query.items() if v not in (None, "")}
    query.setdefault("hide_transfers", "0")
    return "/transactions?" + urlencode(query, safe="(),*")


def range_words(frm, to):
    """Two months as the app writes them: "Aug and Sep 2026" for two in a row,
    else "Aug to Oct 2026"; one month alone."""
    if frm and to and frm != to and frm[:4] == to[:4] and dates.months_apart(frm, to) == 1:
        return f"{dates.MONTHS[int(frm[5:]) - 1]} and {dates.month_label(to)}"
    return dates.range_label(frm, to) if frm and to else dates.month_label(frm or to)


# ---------------------------------------------------------------- the read-only record

def open_readonly(path):
    """kopifund.db opened read-only: any write on it raises."""
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _authorizer(action, arg1, arg2, dbname, source):
    """Reading, and nothing else: no write, ATTACH or PRAGMA, even on the copy."""
    return sqlite3.SQLITE_OK if action in _ALLOWED_ACTIONS else sqlite3.SQLITE_DENY


def _decltype(t):
    """A declared column type fit to write into CREATE TABLE: the record's own
    (INTEGER, TEXT, NUM for a view's computed column, or none), else none."""
    return t if re.fullmatch(r"[A-Za-z][A-Za-z0-9 _(),]*", t or "") else ""


def sandbox(conn, clean):
    """G22: an in-memory database holding SANDBOX's tables and columns from
    the record `conn`, each text value passed through `clean` (the privacy
    filter) as it is copied. The model's SQL runs here, never on the record,
    so whatever it does to a value, the value was safe to begin with."""
    mem = sqlite3.connect(":memory:", check_same_thread=False)
    seen = {}

    def safe(v):
        if not isinstance(v, str):
            return v
        if v not in seen:
            seen[v] = clean(v)
        return seen[v]
    for name, wanted in SANDBOX.items():
        # G22: each column keeps the type the record declares (a view's column
        # reports its source's), so its affinity is the same and `account_id = '1'`
        # or `amount_minor < '-100'` compares on the copy exactly as on the record
        have = {r[1]: r[2] for r in conn.execute(f"PRAGMA table_info({name})")}
        cols = [c for c in wanted if c in have]
        if not cols:
            continue
        listed = ", ".join(f'"{c}"' for c in cols)
        typed = ", ".join(f'"{c}" {_decltype(have[c])}' for c in cols)
        mem.execute(f'CREATE TABLE "{name}" ({typed})')
        rows = conn.execute(f'SELECT {listed} FROM "{name}"')
        mem.executemany(f'INSERT INTO "{name}" VALUES ({", ".join("?" * len(cols))})',
                        ([safe(v) for v in r] for r in rows))
    mem.commit()
    return mem


def _one_statement(sql):
    """The SQL with any trailing semicolons taken off, or ToolRefused unless it
    is exactly one SELECT (or WITH ... SELECT) statement."""
    if not isinstance(sql, str) or not sql.strip():
        raise ToolRefused("Send one SELECT statement in `sql`")
    text = sql.strip()
    while text.endswith(";"):
        text = text[:-1].rstrip()
    # a semicolon outside quotes and comments starts a second statement
    stripped = re.sub(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"|--[^\n]*|/\*.*?\*/", " ", text, flags=re.S)
    if ";" in stripped:
        raise ToolRefused("Only one statement at a time")
    if not re.match(r"(?is)\s*(select|with)\b", stripped):
        raise ToolRefused("Only a SELECT (or WITH ... SELECT) statement may run")
    return text


def _peak_memory():
    """The server's peak memory so far, in bytes (macOS counts bytes, Linux KiB)."""
    if resource is None:
        return 0
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak if sys.platform == "darwin" else peak * 1024


def _cell(v):
    """One value as `query` hands it back: long text cut, a blob as its size."""
    if isinstance(v, (bytes, bytearray, memoryview)):
        return f"({len(v):,} bytes)"
    if isinstance(v, str) and len(v) > CELL_CHARS:
        return v[:CELL_CHARS] + f"... ({len(v) - CELL_CHARS:,} more characters)"
    return v


def run_query(conn, sql, limit=MAX_ROWS, seconds=QUERY_SECONDS, memory=QUERY_MEMORY):
    """{columns, rows, truncated} for one SELECT on `conn`, the sandbox. The
    statement is stopped after `seconds`, or once it has raised the server's
    peak memory by `memory`; no value it builds may pass 1 MB (QUERY_LIMITS);
    and it hands back `limit` rows and QUERY_CHARS of text at most."""
    text = _one_statement(sql)
    for which, value in QUERY_LIMITS.items():
        conn.setlimit(which, value)
    deadline, ceiling = time.monotonic() + seconds, _peak_memory() + memory
    stopped = []

    def stop():
        if time.monotonic() > deadline:
            stopped.append(f"it ran past {seconds:g} seconds")
        elif _peak_memory() > ceiling:
            stopped.append("it needed too much memory")
        return bool(stopped)
    conn.set_authorizer(_authorizer)
    conn.set_progress_handler(stop, 10_000)
    rows, size, truncated = [], 0, False
    try:
        cur = conn.execute(text)
        if cur.description is None:
            raise ToolRefused("Only a SELECT statement may run")
        columns = [d[0] for d in cur.description]
        for r in cur:
            if len(rows) >= limit or size > QUERY_CHARS:
                truncated = True
                break
            row = [_cell(v) for v in r]
            size += sum(len(v) if isinstance(v, str) else 8 for v in row)
            rows.append(row)
        cur.close()
    except sqlite3.DataError as e:
        if "too big" in str(e):
            raise ToolRefused("The query built a value over 1 MB, more than a query may: "
                              "ask for less, or count or sum instead") from None
        if "too large" in str(e):
            raise ToolRefused("The query is longer than a query may be: make it simpler") from None
        raise ToolRefused(f"SQLite said: {e}") from None
    except (sqlite3.DatabaseError, sqlite3.ProgrammingError) as e:
        if stopped:
            raise ToolRefused(f"The query was stopped because {stopped[0]}: narrow it, "
                              "or count or sum instead of listing") from None
        if "too many" in str(e) or "too big" in str(e) or "too long" in str(e):
            raise ToolRefused(f"The query is bigger than a query may be ({e}): make it simpler") from None
        raise ToolRefused(f"SQLite said: {e}") from None
    finally:
        conn.set_authorizer(None)
        conn.set_progress_handler(None, 0)
    return {"columns": columns, "rows": rows, "truncated": truncated}


# ---------------------------------------------------------------- one question's context

class Context:
    """What one question's tools share: the read-only connection, the stat
    results, and what they have to show the panel (charts, links, cards,
    follow-up questions)."""

    def __init__(self, conn, path, results, folders=(), cfg=None, today=None):
        self.conn, self.path, self.results = conn, path, results or {}
        self.folders, self.cfg = folders, cfg
        self.today = today or date.today()
        self.charts, self.links, self.cards, self.followups = [], [], [], []
        self.named_links = []      # (name, label, link): offered once the answer names it
        self._sandbox = None
        self.numbers = account_names.own_numbers(conn)
        try:
            self.numbers |= account_names.own_numbers(cfg=cfg) if cfg is not None else set()
        except AttributeError:
            pass
        self._hidden = self._hidden_texts()

    # -------- privacy

    def _hidden_texts(self):
        """A pattern for every counterparty and memo text the record holds that
        is not also a name the app shows anyway (a payee, a merchant's text, a
        category or bucket), raw and as it reads inside a link."""
        known = set()
        for sql in ("SELECT name FROM payee", "SELECT DISTINCT merchant_raw FROM txn",
                    "SELECT name FROM category", "SELECT name FROM category_group"):
            known |= {str(r[0]).casefold() for r in self.conn.execute(sql) if r[0]}
        texts = set()
        for col in ("counterparty", "memo"):
            texts |= {r[0].strip() for r in self.conn.execute(f"SELECT DISTINCT {col} FROM txn") if r[0]}
        texts = {t for t in texts if len(t) >= 3 and t.casefold() not in known}
        forms = set()
        for t in texts:
            forms |= {t, quote_plus(t, safe="(),*"), quote(t, safe="(),*")}
        if not forms:
            return None
        alternation = "|".join(re.escape(f) for f in sorted(forms, key=len, reverse=True))
        return re.compile(rf"(?<![A-Za-z0-9])(?:{alternation})(?![A-Za-z0-9])")

    def private(self, value):
        """`value` as the model may see it (the module's docstring)."""
        def walk(x):
            if isinstance(x, dict):
                return {k: walk(v) for k, v in x.items() if k not in HIDDEN_KEYS}
            if isinstance(x, (list, tuple)):
                return [walk(v) for v in x]
            if isinstance(x, str):
                if self._hidden is not None:
                    x = self._hidden.sub("(hidden)", x)
                return account_names.scrub(x, self.numbers) if self.numbers else x
            return x
        return walk(value)

    # -------- what the panel shows

    def offer_link(self, label, link):
        """A Transactions link for the panel under the answer: the rows a
        figure the copilot looked at counts. Each address once, a few at most."""
        if link and all(link != seen["href"] for seen in self.links) and len(self.links) < LINKS_SHOWN:
            self.links.append({"label": label, "href": link})

    def offer_named_link(self, name, label, link):
        """A link the panel shows only if the answer names `name` (a trip, a
        recurring payee): a tool that lists many offers each this way, so the
        links under an answer are the rows it used."""
        if name and link:
            self.named_links.append((str(name), label, link))

    def settle_links(self, answer):
        """Offer the named links whose name the answer's text holds, in the
        order the tools listed them, a few at most (offer_link)."""
        text = (answer or "").casefold()
        for name, label, link in self.named_links:
            if re.search(rf"(?<!\w){re.escape(name.casefold())}(?!\w)", text):
                self.offer_link(label, link)

    def sandbox(self):
        """The in-memory copy `query` runs on (module docstring), made once a question."""
        if self._sandbox is None:
            self._sandbox = sandbox(self.conn, self.private)
        return self._sandbox

    def close(self):
        if self._sandbox is not None:
            self._sandbox.close()
            self._sandbox = None

    def names(self):
        """Every category's name as the record holds it now."""
        return [r[0] for r in self.conn.execute("SELECT name FROM category ORDER BY COALESCE(sort_order, 1e9), name")]


def fit(value, limit=RESULT_CHARS):
    """`value` with its longest lists and texts cut, step by step, until its
    JSON is about `limit` characters, each cut list saying how many it left
    out and each cut text how many characters."""
    def cut(x, n, chars):
        if isinstance(x, dict):
            return {k: cut(v, n, chars) for k, v in x.items()}
        if isinstance(x, list):
            kept = [cut(v, n, chars) for v in (x if n is None else x[:n])]
            return kept + [f"... and {len(x) - n} more"] if n is not None and len(x) > n else kept
        if isinstance(x, str) and len(x) > chars:
            return x[:chars] + f"... ({len(x) - chars:,} more characters)"
        return x
    for n, chars in ((None, limit), (60, 4_000), (30, 2_000), (15, 1_000), (8, 500), (4, 200)):
        out = cut(value, n, chars)
        if len(json.dumps(out, default=str)) <= limit:
            return out
    return out


# ---------------------------------------------------------------- the reads

def _check_month(v, name):
    if v in (None, ""):
        return None
    if not isinstance(v, str) or not MONTH.fullmatch(v):
        raise ToolRefused(f"{name} is a month, YYYY-MM")
    return v


def tool_overview(ctx, args):
    from . import home, monthly
    try:
        listing = [{**f, "statements": 0} for f in monthly.folders(ctx.cfg)] if ctx.cfg else ()
    except Exception:            # noqa: BLE001  the config's folders only name a late account
        listing = ()
    o = home.overview(ctx.conn, ctx.results, today=ctx.today, folders=ctx.folders, listing=listing)
    eye = o.get("needs_your_eye") or {}
    return {"as_of": o["as_of"], "headline": o["headline"], "holdings": o["holdings"],
            "rows_with_no_sgd_value": o["missing_fx"],
            "coming_up_this_month": _recurring_rows(ctx, (o.get("coming_up") or {}).get("rows") or []),
            "coming_up_totals": (o.get("coming_up") or {}).get("totals"),
            "needs_your_eye": [line.get("text") for line in eye.get("lines") or () if isinstance(line, dict)],
            "note": "headline, holdings and coming-up amounts are balances or estimates in SGD cents: no link"}


def _spend_label(by, frm, to, scope):
    return f"Spending by {by}, {range_words(frm, to)}" + (", overseas included" if scope == "all" else "")


def tool_spending(ctx, args):
    from . import spending
    query = {"from": _check_month(args.get("from"), "from"), "to": _check_month(args.get("to"), "to"),
             "scope": args.get("scope") or "all", "by": args.get("by") or "category"}
    try:
        d = spending.explore(ctx.conn, {k: v for k, v in query.items() if v})
    except spending.SpendingRefused as e:
        raise ToolRefused(str(e)) from None
    if not d["months"]:
        return {"months": [], "note": "The record holds no spending yet"}
    base = d["link"]
    total_link = href(base)
    ctx.offer_link(_spend_label(d["by"], d["months"][0], d["months"][-1], d["scope"]), total_link)
    return {
        "from": d["months"][0], "to": d["months"][-1], "scope": d["scope"], "by": d["by"],
        "record_holds": d["available"], "currency": "SGD",
        "total_spend": d["totals"]["spend"], "per_month_average": d["totals"]["per_month"],
        "overseas_spend": d["totals"]["overseas"], "link": total_link,
        "months": d["months"],
        "series": [{"name": s["name"], "total": s["total"], "per_month_average": s["per_month"],
                    "by_month": s["values"], "link": href({**base, **s["link"]})} for s in d["series"]],
        "buckets": [{"name": b["name"], "total": b["total"], "per_month_average": b["per_month"],
                     "categories": [{"name": c["name"], "total": c["total"], "per_month_average": c["per_month"],
                                     "link": href({**base, **c["link"]})} for c in b["categories"]]}
                    for b in d["buckets"]],
        "top_payees": [{"name": p["name"], "total": p["total"], "payments": p["count"],
                        "link": href({**base, **p["link"]})} for p in d["payees"]],
        "biggest_payments": [{"date": b["date"], "payee": b["payee"], "category": b["category"],
                              "amount": b["amount"], "link": href({"key": b["key"]})} for b in d["biggest"]],
    }


FILTERS = ("month", "category", "account", "payee", "named", "key", "from", "to", "home", "spend", "income",
           "transfer", "need", "bucket", "categories", "not_categories", "not_payees", "external", "trip",
           "synthetic", "out", "hide_transfers", "q")


def _filter_words(query):
    bits = []
    if query.get("category"):
        bits.append(query["category"] if query["category"] != "(none)" else "no category")
    for k in ("payee", "trip", "bucket"):
        if query.get(k):
            bits.append(query[k])
    if query.get("month"):
        bits.append(dates.month_label(query["month"]))
    if query.get("from") or query.get("to"):
        bits.append(dates.range_label(query.get("from") or query.get("to"), query.get("to") or query.get("from")))
    for k, word in (("spend", "spending"), ("income", "income"), ("out", "payments")):
        if query.get(k) == "1":
            bits.append(word)
    if query.get("q"):
        bits.append(f"matching {query['q']!r}")
    return ", ".join(bits) or "all rows"


def tool_transactions(ctx, args):
    from . import api
    query = {}
    for k in FILTERS:
        v = args.get(k)
        if v is None or v == "":
            continue
        if isinstance(v, list):
            v = json.dumps([str(x) for x in v], ensure_ascii=False)
        query[k] = str(v)
    query.setdefault("hide_transfers", "0")
    limit = args.get("limit") or 20
    if not isinstance(limit, int) or isinstance(limit, bool):
        raise ToolRefused("limit is a whole number")
    query["limit"] = str(max(1, min(MAX_TXNS, limit)))
    offset = args.get("offset") or 0
    query["offset"] = str(max(0, int(offset) if isinstance(offset, int) else 0))
    try:
        page = api.transactions(ctx.conn, query, ctx.folders, ctx.results)
    except ValueError as e:
        raise ToolRefused(str(e)) from None
    filters = {k: v for k, v in query.items() if k not in ("limit", "offset")}
    link = href(filters)
    ctx.offer_link(f"Transactions: {_filter_words(filters)}", link)
    keys = [r["key"] for r in page["rows"]]
    texts = {}
    if keys:
        texts = {r[0]: r[1] for r in ctx.conn.execute(
            f"SELECT txn_key, merchant_raw FROM v_txn WHERE txn_key IN ({','.join('?' * len(keys))})", keys)}
    return {"rows_matching": page["total"], "net_sgd": page["sum_sgd"], "in_sgd": page["in_sgd"],
            "out_sgd": page["out_sgd"], "link": link, "shown": len(page["rows"]), "offset": int(query["offset"]),
            "rows": [{"key": r["key"], "date": r["date"], "amount": r["amount"], "currency": r["currency"],
                      "amount_sgd": r["amount_sgd"], "account": r["account"], "payee": r["payee"],
                      "statement_text": "" if r["payee"] else texts.get(r["key"], ""),
                      "category": r["category"], "bucket": r["category_group"], "trip": r["trip"],
                      "flow": r["flow"]} for r in page["rows"]]}


def _recurring_rows(ctx, rows):
    out = []
    for r in rows:
        row = {k: r.get(k) for k in ("name", "payee", "category", "cadence", "amount", "expected", "status",
                                     "paid_on", "usual_low", "usual_high", "last_paid", "next_expected", "late",
                                     "yearly", "state", "first_seen", "last_seen", "ended_on") if k in r}
        if r.get("link"):
            row["payments_link"] = href(r["link"])
            who = r.get("payee") or r.get("name")
            for name in {r.get("payee"), r.get("name")} - {None, ""}:
                ctx.offer_named_link(name, f"Payments to {who}", row["payments_link"])
        out.append(row)
    return out


def tool_recurring(ctx, args):
    from . import recurring
    p = recurring.page(ctx.conn, ctx.results, ctx.today)
    if not p["ready"]:
        return {"ready": False, "note": "The recurring payments stat has not run yet"}
    return {"as_of": p["as_of"], "totals": p["totals"],
            "coming_up_this_month": _recurring_rows(ctx, p["coming_up"]["rows"]),
            "active": _recurring_rows(ctx, p["active"]), "paused": _recurring_rows(ctx, p["paused"]),
            "stopped": _recurring_rows(ctx, p["stopped"]),
            "note": "amount, usual_low/high and yearly are typical figures (estimates, SGD cents); "
                    "payments_link lists the payee's actual payments"}


def tool_trips(ctx, args):
    from . import api
    t = api.trips(ctx.conn)
    trips = []
    for x in t["trips"]:
        link = href({"trip": x["name"], "spend": "1"})
        ctx.offer_named_link(x["name"], f"Spending on {x['name']}", link)
        trips.append({"name": x["name"], "start": x["start"], "end": x["end"], "days": x["days"],
                      "countries": x["countries"], "spend_sgd": x["spend_sgd"], "rows": x["rows"],
                      "per_day_average": x["per_day"], "long_stay": x["long_stay"],
                      "top_categories": x["top_categories"], "link": link})
    return {"last_transaction_day": t["last_txn"], "long_stay_days": t["long_stay_days"], "trips": trips}


def tool_net_worth(ctx, args):
    from . import net_worth
    nw = net_worth.net_worth(ctx.results)
    return {"parts": nw["parts"], "months": nw["months"][-24:],
            "note": "balances in SGD cents, each carried from the date it was last measured: no link"}


def tool_stat_result(ctx, args):
    stat = args.get("id")
    if stat not in ctx.results:
        raise ToolRefused(f"No result for {stat!r}. Results there are: {', '.join(sorted(ctx.results)) or 'none'}")
    r = ctx.results[stat]
    return {"id": stat, "title": r.get("title"), "generated_at": r.get("generated_at"), "data": r.get("data"),
            "findings": r.get("findings") or [],
            "note": "a stat's money is cents under <name>_minor; its averages and estimates carry no link"}


# G22: at most this many rows a query lists get a Transactions link (key=...)
QUERY_LINK_KEYS = 60


def tool_query(ctx, args):
    """One SELECT on the scrubbed copy. A free-form count or sum cannot be
    turned back into the filters that list its rows, so only a query that
    returns `txn_key` gets a link: to exactly the rows it listed."""
    out = run_query(ctx.sandbox(), args.get("sql"))
    note = ("a copy of the record without counterparty, memo or reference lines; "
            "account numbers show their last four digits only")
    if "txn_key" in out["columns"] and not out["truncated"]:
        at = out["columns"].index("txn_key")
        keys = list(dict.fromkeys(r[at] for r in out["rows"] if isinstance(r[at], str) and r[at]))
        if 0 < len(keys) <= QUERY_LINK_KEYS:
            link = href({"key": ",".join(keys)})
            ctx.offer_link(f"Open the {len(keys)} {'row' if len(keys) == 1 else 'rows'} in Transactions", link)
            out = {**out, "link": link}
    return {**out, "note": note}


# ---------------------------------------------------------------- what the panel shows

def tool_show_chart(ctx, args):
    """A chart the panel draws with the chart kit: the stats' chart hint, with
    its rows inline under `rows` (server/analytics/SPEC.md, "The chart hint")."""
    title = args.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ToolRefused("A chart needs a title")
    kind = args.get("type") or "bar"
    if kind not in CHART_TYPES:
        raise ToolRefused(f"type is one of {', '.join(CHART_TYPES)}")
    x, series, rows = args.get("x"), args.get("series"), args.get("rows")
    if not isinstance(x, str) or not x:
        raise ToolRefused("x names the field each row is labelled by")
    if not isinstance(series, list) or not series or not all(isinstance(s, str) for s in series):
        raise ToolRefused("series lists the numeric fields to plot")
    if not isinstance(rows, list) or not rows or len(rows) > MAX_ROWS or not all(isinstance(r, dict) for r in rows):
        raise ToolRefused(f"rows is a list of 1 to {MAX_ROWS} objects")
    for r in rows:
        if x not in r:
            raise ToolRefused(f"Every row needs its {x!r}")
        for s in series:
            v = r.get(s)
            if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float))):
                raise ToolRefused(f"{s} is a number in every row")
    unit = args.get("unit") or "number"
    if unit not in CHART_UNITS:
        raise ToolRefused(f"unit is one of {', '.join(CHART_UNITS)}")
    units = None
    if isinstance(args.get("units"), dict):
        units = {k: v for k, v in args["units"].items() if k in series and v in CHART_UNITS}
    for s in series:
        # G22: each series by its own unit (`units` over `unit`): only money must be whole cents
        if (units or {}).get(s, unit) == "sgd_minor" and any(
                isinstance(r.get(s), float) and not r[s].is_integer() for r in rows):
            raise ToolRefused(f"{s} is money in whole cents (sgd_minor); {s} has a fraction")
    chart = {"type": kind, "x": x, "series": series, "unit": unit, "source": "rows", "rows": rows}
    if units is not None:
        chart["units"] = units
    if isinstance(args.get("horizontal"), bool):
        chart["horizontal"] = args["horizontal"]
    if isinstance(args.get("links"), dict):
        # G13's links: per series, the Transactions filters with {x} or {name} from the row
        chart["links"] = {k: {a: str(b) for a, b in v.items()} for k, v in args["links"].items()
                          if k in series and isinstance(v, dict)}
    ctx.charts.append({"title": title.strip()[:120], "chart": chart})
    return {"shown": True}


def tool_follow_ups(ctx, args):
    qs = args.get("questions")
    if not isinstance(qs, list) or not all(isinstance(q, str) for q in qs):
        raise ToolRefused("questions is a list of short questions")
    ctx.followups = [" ".join(q.split())[:100] for q in qs if q.strip()][:3]
    return {"shown": len(ctx.followups)}


def _card(ctx, kind, title, summary, preview, request, undo=None):
    card = {"id": secrets.token_hex(6), "kind": kind, "title": title, "summary": summary, "preview": preview,
            "request": request, "undo": undo}
    ctx.cards.append(card)
    return card


def _row_line(r, before, after):
    return (f"{dates.day_label(r['date'])}  {money.label(r['amount'], r['currency'])}  {r['text']}: "
            f"{before or 'no category'} to {after or 'no category'}")


def _rule_card(ctx, args):
    from . import rules_editor
    from .parsers import categorise
    match_on = args.get("match_on") or "merchant"
    if match_on not in ("merchant", "type", "flow"):
        raise ToolRefused("A suggested rule matches on merchant (the usual), type or flow; "
                          "for one row, suggest kind=category with its key")
    payee = (args.get("payee") or "").strip()
    rule = {"match_on": match_on, "pattern": (args.get("pattern") or "").strip(), "payee": payee,
            "kind": (args.get("payee_kind") or ("Merchant" if payee else "")) if payee else "",
            "country": (args.get("country") or "").strip().upper(), "category": (args.get("category") or "").strip(),
            "recurring": "", "notes": "[ai] suggested by Ask Kopifund"
            + (f": {' '.join(str(args['why']).split())[:120]}" if args.get("why") else "")}
    if rule["kind"] and rule["kind"] not in categorise.KINDS:
        raise ToolRefused(f"payee_kind is one of {', '.join(categorise.KINDS)}")
    body = {"rule": rule}
    p = rules_editor.preview(ctx.conn, body)
    if p.get("error"):
        raise ToolRefused(p["error"])
    lines = [_row_line(r, r["before"]["category"], r["after"]["category"]) for r in p["sample"]]
    if p["changes"] > len(lines):
        lines.append(f"and {p['changes'] - len(lines)} more")
    n = p["changes"]
    card = _card(ctx, "rule", f"Rule: {rule['pattern']} to {rule['category']}",
                 f"{n} row{'s' if n != 1 else ''} would change" + (f", payee {payee}" if payee else ""),
                 lines, {"path": "/api/rules/save", "body": body})
    return card, {"rows_that_would_change": n, "rows_matched": p["matches"]}


def _category_card(ctx, args):
    from . import rules_app
    key, category = (args.get("key") or "").strip(), (args.get("category") or "").strip()
    rows = rules_app.db_rows(ctx.conn, key) if key else []
    if not rows:
        raise ToolRefused("No row with that key in the record")
    if category not in ctx.names():
        raise ToolRefused(f"{category!r} is not a category; pick one from the data dictionary")
    r = {**rows[0], "text": rules_app._text(rows[0])}
    body = {"key": key, "category": category}
    if args.get("payee"):
        body["payee"] = str(args["payee"]).strip()
    card = _card(ctx, "category", f"Category: {category}",
                 f"This one row, {money.label(r['amount_minor'], r['currency'])} on {dates.day_label(r['date'])}",
                 [_row_line({**r, "amount": r["amount_minor"]}, r["category"], category)],
                 {"path": "/api/rules/pin", "body": body})
    return card, {"row": key, "category_now": rows[0]["category"] or None}


def _budget_card(ctx, args):
    category, month = (args.get("category") or "").strip(), _check_month(args.get("month"), "month")
    if not month:
        raise ToolRefused("month is the budget's month, YYYY-MM")
    planned = args.get("planned_minor")
    if planned is not None and (isinstance(planned, bool) or not isinstance(planned, int) or planned < 0):
        raise ToolRefused("planned_minor is whole cents, zero or more, or null to clear it")
    row = ctx.conn.execute("SELECT c.id, c.name, c.bucket FROM category c WHERE c.name = ?", (category,)).fetchone()
    if row is None or row["bucket"] not in ("Spend", "Income"):
        raise ToolRefused(f"{category!r} is not a spending or income category")
    old = ctx.conn.execute("SELECT planned_minor FROM budget WHERE category_id = ? AND month = ?",
                           (row["id"], month)).fetchone()
    old = old[0] if old else None

    def amount(c):
        return "none" if c is None else money.label(c)
    body = {"category_id": row["id"], "category": row["name"], "month": month, "planned": planned}
    # G22: no undo here: the amount now may change before Confirm, so the page
    # reads it as Confirm is pressed and its Undo puts that back
    card = _card(ctx, "budget", f"Budget: {row['name']}, {dates.month_label(month)}",
                 f"Planned {amount(old)} now, {amount(planned)} after",
                 [f"Planned for {dates.month_label(month)}: {amount(old)} to {amount(planned)}"],
                 {"path": "/api/budgets", "body": body})
    return card, {"planned_now": old, "planned_after": planned}


def _trip_card(ctx, args):
    from . import planning
    trip = {"name": (args.get("name") or "").strip(), "start": args.get("start") or "", "end": args.get("end") or "",
            "countries": args.get("countries") or [], "notes": " ".join(str(args.get("why") or "").split())[:200],
            "extra_keys": []}
    if not isinstance(trip["countries"], list) or not all(isinstance(c, str) for c in trip["countries"]):
        raise ToolRefused("countries is a list of three-letter codes, like JPN")
    try:
        name, start, end, countries, _, _ = planning._trip_fields(trip)
    except planning.PlanRefused as e:
        raise ToolRefused(str(e)) from None
    body = {**trip, "name": name, "start": start, "end": end, "countries": countries}
    held = ctx.conn.execute("SELECT id, notes FROM trip WHERE name = ?", (name,)).fetchone()
    if held:
        # an edit of the trip by that name keeps its notes and the rows pinned to it
        from . import db
        body.update({"id": held["id"], "old_name": name, "notes": held["notes"] or "",
                     "extra_keys": db.trip_pins(ctx.conn, held["id"], follow=False)})
    n = ctx.conn.execute("SELECT COUNT(*) FROM v_txn WHERE txn_date BETWEEN ? AND ?", (start, end)).fetchone()[0]
    card = _card(ctx, "trip", f"Trip: {name}", f"{dates.range_label(start, end)}, {', '.join(countries) or 'no country'}",
                 [f"{n} row{'s' if n != 1 else ''} in the record fall in those days; "
                  "the ones spent abroad join the trip"],
                 {"path": "/api/trips", "body": body})
    return card, {"edits_existing_trip": bool(held)}


def tool_suggest_change(ctx, args):
    kind = args.get("kind")
    build = {"rule": _rule_card, "category": _category_card, "budget": _budget_card, "trip": _trip_card}.get(kind)
    if build is None:
        raise ToolRefused(f"kind is one of {', '.join(CARD_KINDS)}")
    card, facts = build(ctx, args)
    return {"card": card["id"], "shown": True, **facts,
            "note": "Nothing is saved until the owner presses Confirm on the card"}


# ---------------------------------------------------------------- the list the model gets

def _labels(name, args):
    """The quiet line the panel shows while a tool runs, in plain words."""
    args = args or {}
    if name == "spending":
        frm, to = args.get("from"), args.get("to")
        when = range_words(frm, to) if (frm or to) else "the last 12 months"
        return f"Spending by {args.get('by') or 'category'}, {when}"
    if name == "transactions":
        return f"Transactions: {_filter_words({k: str(v) for k, v in args.items() if k in FILTERS})}"
    if name == "stat_result":
        return f"The {args.get('id') or ''} stat"
    if name == "show_chart":
        return f"Drawing {args.get('title') or 'a chart'}"
    if name == "suggest_change":
        return f"Preparing a {args.get('kind') or ''} change to confirm"
    return {"overview": "The overview", "recurring": "Recurring payments", "trips": "Trips",
            "net_worth": "Net worth over time", "query": "Looking it up in the record",
            "follow_ups": "Follow-up questions"}.get(name, name)


def label(name, args):
    try:
        return _labels(name, args)
    except Exception:            # noqa: BLE001  a label is never a reason to fail
        return name


_STR = {"type": "string"}
_MONTH = {"type": "string", "description": "YYYY-MM"}
TOOLS = [
    {"name": "overview", "run": tool_overview,
     "description": "Home's figures: net worth, cash, runway, holdings, this month's coming-up payments, "
                    "and what needs the owner's eye.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "spending", "run": tool_spending,
     "description": "Spending over a range of months, split by bucket, category or payee, with each month's "
                    "figure, the top payees and the biggest payments. Defaults: the last 12 months the record "
                    "holds, overseas included. scope=sg leaves out rows on trips.",
     "input_schema": {"type": "object", "properties": {
         "from": _MONTH, "to": _MONTH, "scope": {"type": "string", "enum": ["sg", "all"]},
         "by": {"type": "string", "enum": ["bucket", "category", "payee"]}}}},
    {"name": "transactions", "run": tool_transactions,
     "description": "Rows of the ledger, newest first, with the count and sums of every row the filters match. "
                    "Filters are the Transactions page's: month, from/to (YYYY-MM or YYYY-MM-DD), category "
                    "('(none)' for no category), payee (exact), bucket, trip, spend=1 (spending as the stats count "
                    "it), income=1, transfer=1, out=1 (payments only), need (Need,Want,...), categories / "
                    "not_categories / not_payees (lists of names), account (an id), key (row keys, comma-separated), "
                    "home=1 (not on a trip), external=1, synthetic=0|1, q (text search). limit at most 50.",
     "input_schema": {"type": "object", "properties": {
         **{k: _STR for k in ("month", "from", "to", "category", "payee", "bucket", "trip", "need", "key",
                              "account", "q")},
         **{k: {"type": "string", "enum": ["1"]} for k in ("spend", "income", "transfer", "out", "home",
                                                          "external", "named")},
         "synthetic": {"type": "string", "enum": ["0", "1"]},
         **{k: {"type": "array", "items": _STR} for k in ("categories", "not_categories", "not_payees")},
         "limit": {"type": "integer"}, "offset": {"type": "integer"}}}},
    {"name": "recurring", "run": tool_recurring,
     "description": "Recurring payments: this month's coming-up list (paid, due, late, different amount), "
                    "active, paused and stopped series with their typical amount and yearly cost.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "trips", "run": tool_trips,
     "description": "Every trip with its dates, countries, spending, cost per day and top categories.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "net_worth", "run": tool_net_worth,
     "description": "Cash, investments and CPF month by month (the last 24 months), and their total.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "stat_result", "run": tool_stat_result,
     "description": "One stat's whole result, by id: monthly-cashflow, needs-wants-savings, category-breakdown, "
                    "recurring-detector, eating-out, merchant-concentration, one-offs, income-stability, "
                    "savings-runway, portfolio, cpf, travel, benchmarks-sg, forward-view, data-quality.",
     "input_schema": {"type": "object", "properties": {"id": _STR}, "required": ["id"]}},
    {"name": "query", "run": tool_query,
     "description": "One SQLite SELECT on a read-only copy of the record, at most 200 rows. Tables: v_txn "
                    "(every live row: txn_key, txn_date, month, amount_minor, currency, amount_sgd_minor, "
                    "flow_class, merchant_raw, payee_name, category_name, bucket, need_class, category_group, "
                    "trip_name, account_id, account_name, is_synthetic, ...), category, category_group, budget, "
                    "v_budget, trip, trip_country, payee, account, v_period. It has no counterparty, memo or "
                    "reference column, and account numbers show their last four digits. Spending is "
                    "flow_class='External' AND bucket='Spend' AND is_synthetic=0, signed negative. Prefer the "
                    "other tools; use this for what they cannot answer. A count or sum has no link to its "
                    "rows: to give the owner one, also list the rows with their txn_key (60 at most).",
     "input_schema": {"type": "object", "properties": {"sql": _STR}, "required": ["sql"]}},
    {"name": "show_chart", "run": tool_show_chart,
     "description": "Show the owner a chart under the answer. rows are objects; x names the label field and "
                    "series the numeric fields. unit sgd_minor is whole cents. links (optional) maps a series "
                    "to the Transactions filters that list the rows its bar adds up, with {x} for the row's "
                    "label; leave averages and estimates out of links.",
     "input_schema": {"type": "object", "properties": {
         "title": _STR, "type": {"type": "string", "enum": list(CHART_TYPES)}, "x": _STR,
         "series": {"type": "array", "items": _STR}, "unit": {"type": "string", "enum": list(CHART_UNITS)},
         "units": {"type": "object"}, "horizontal": {"type": "boolean"},
         "rows": {"type": "array", "items": {"type": "object"}}, "links": {"type": "object"}},
         "required": ["title", "x", "series", "rows"]}},
    {"name": "suggest_change", "run": tool_suggest_change,
     "description": "Suggest one change as a card the owner confirms (nothing is saved until they do). "
                    "kind=rule: a pattern rule (match_on merchant, pattern with * wildcards, category, "
                    "payee, payee_kind). kind=category: one row's category (key, category, payee). "
                    "kind=budget: a category's planned amount for a month (category, month, planned_minor in "
                    "cents or null). kind=trip: a trip (name, start, end as YYYY-MM-DD, countries as ISO "
                    "alpha-3 codes). why: a few words on the reason.",
     "input_schema": {"type": "object", "properties": {
         "kind": {"type": "string", "enum": list(CARD_KINDS)}, "why": _STR,
         "match_on": {"type": "string", "enum": ["merchant", "type", "flow"]}, "pattern": _STR, "payee": _STR,
         "payee_kind": {"type": "string", "enum": ["Merchant", "Person", "Employer", "Institution"]},
         "country": _STR, "category": _STR, "key": _STR, "month": _MONTH,
         "planned_minor": {"type": ["integer", "null"]}, "name": _STR, "start": _STR, "end": _STR,
         "countries": {"type": "array", "items": _STR}},
         "required": ["kind"]}},
    {"name": "follow_ups", "run": tool_follow_ups,
     "description": "Up to 3 short follow-up questions the owner might ask next, shown as chips under the answer.",
     "input_schema": {"type": "object", "properties": {"questions": {"type": "array", "items": _STR}},
                      "required": ["questions"]}},
]
BY_NAME = {t["name"]: t for t in TOOLS}


def schemas():
    """The tools as the model gets them: name, description, input_schema."""
    return [{k: t[k] for k in ("name", "description", "input_schema")} for t in TOOLS]


def run(ctx, name, args):
    """(text for the model, is_error) for one call: the tool's result through
    the privacy filter, cut to size, as JSON; or what went wrong, in words."""
    tool = BY_NAME.get(name)
    if tool is None:
        return f"No tool called {name!r}", True
    if args is None:
        return "The arguments were not a JSON object", True
    try:
        out = tool["run"](ctx, args)
    except ToolRefused as e:
        return str(ctx.private(str(e))), True
    except sqlite3.Error as e:
        return f"The record could not answer: {e.__class__.__name__}", True
    return json.dumps(fit(ctx.private(out)), ensure_ascii=False, default=str), False


# ---------------------------------------------------------------- the data dictionary

def dictionary(ctx):
    """What the model is told about this record before any question: buckets
    and their categories, the accounts by display name (numbers masked to the
    last four digits) with their ids, and the first and last day it holds."""
    buckets = {}
    for r in ctx.conn.execute("""SELECT COALESCE(g.name, '(no bucket)') AS grp, g.kind, c.name, c.need_class
                                 FROM category c LEFT JOIN category_group g ON g.id = c.group_id
                                 ORDER BY COALESCE(g.sort_order, 1e9), g.name, COALESCE(c.sort_order, 1e9), c.name"""):
        b = buckets.setdefault(r["grp"], {"kind": r["kind"] or "", "categories": []})
        b["categories"].append(f"{r['name']} ({r['need_class']})" if r["need_class"] else r["name"])
    accounts = [dict(r) for r in ctx.conn.execute("SELECT id, name, account_no, institution FROM account ORDER BY name")]
    described = account_names.describe(accounts, ctx.folders)
    first, last = ctx.conn.execute("SELECT MIN(txn_date), MAX(txn_date) FROM v_txn").fetchone()
    lines = ["Buckets and their categories (need class in brackets):"]
    lines += [f"- {name} [{b['kind']}]: {', '.join(b['categories'])}" for name, b in buckets.items()]
    lines.append("Accounts (id: display name):")
    lines += [f"- {a['id']}: {d['label']}" for a, d in zip(accounts, described)]
    lines.append(f"The record runs from {dates.day_label(first) or 'nothing yet'} to {dates.day_label(last) or '-'}.")
    lines.append("Stat results on hand: " + (", ".join(sorted(ctx.results)) or "none (the stats have not run)"))
    return ctx.private("\n".join(lines))
