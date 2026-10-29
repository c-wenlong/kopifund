#!/usr/bin/env python3
"""Everything that talks to the Notion API, plus the two property shapes.

NotionClient wraps the handful of endpoints the sync needs, with the pacing and
retry behaviour the earlier loader used: about three requests a second across
every thread, retry on 429 and 5xx. Independent calls (the rows of one upsert,
the shards of one read) run on a small thread pool through `map`; the pace is
global, so the pool only removes the per-request latency from the wall time, it
never sends more requests a second. Every write is one page, so an interrupted
run still leaves a state the next run's diff repairs.

`to_api` turns the flat {property name: value} shape the rest of the code uses
into the API's typed objects, and `plain` reads one back out of a page. They are
a pair: `plain(create(to_api(props)), name)` returns what went in.
"""
import json
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import dotenv


class NotionError(RuntimeError):
    """An API call failed after every retry, or the token could not be found."""


def never_sent(e):
    """True for a network error that stops a request before Notion can see
    it: the name did not resolve or the connection was refused."""
    reason = getattr(e, "reason", None) if isinstance(e, urllib.error.URLError) else e
    return isinstance(reason, (socket.gaierror, ConnectionRefusedError))


class NotionClient:
    """One Notion integration, paced and retrying."""

    API = "https://api.notion.com/v1"

    def __init__(self, token, version, pace=0.35, tries=6, timeout=60, workers=4):
        self.token = token
        self.version = version
        self.pace = pace                 # seconds between requests, across all threads
        self.tries = tries
        self.timeout = timeout
        self.workers = workers           # threads `map` uses; 1 means sequential
        self._lock = threading.Lock()
        self._next = 0.0                 # the earliest time the next request may go
        self.writes = 0                  # create + patch + archive calls made

    # ------------------------------------------------------------ transport

    def _slot(self):
        """Reserve the next send time and sleep until it. Threads take slots in
        turn, so the request rate is the same whether one or eight are calling."""
        with self._lock:
            at = max(time.time(), self._next)
            self._next = at + self.pace
        delay = at - time.time()
        if delay > 0:
            time.sleep(delay)

    def _count_write(self):
        with self._lock:
            self.writes += 1

    def map(self, fn, items, progress=None):
        """fn over every item on the thread pool, results in item order.

        The first exception cancels what has not started, waits for what has,
        and is raised: the same partial state a sequential loop would leave,
        minus the calls in flight. `progress(done, total)` is called on the
        calling thread after each completion, for the run's echo lines.
        """
        items = list(items)
        workers = min(self.workers, len(items))
        if workers <= 1:
            out = []
            for i, x in enumerate(items, 1):
                out.append(fn(x))
                if progress:
                    progress(i, len(items))
            return out
        out = [None] * len(items)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(fn, x): i for i, x in enumerate(items)}
            try:
                for n, f in enumerate(as_completed(futures), 1):
                    out[futures[f]] = f.result()
                    if progress:
                        progress(n, len(items))
            except BaseException:
                for f in futures:
                    f.cancel()
                raise
        return out

    def call(self, method, path, body=None):
        """One request. Raises NotionError rather than exiting, so the caller
        can decide the exit code.

        A request is retried on a rate limit, a 5xx and a network error, with
        one exception: creating a page (POST /pages) is not idempotent, so a
        create whose answer was lost (a timeout, a 5xx a gateway sent after
        Notion may have written it) is never sent again; a second send could
        make a second page with the same key. It is retried only where Notion
        cannot have seen it: a 429, or a connection that never opened. The
        run stops on the error and the next run's diff creates the page if
        it is still missing."""
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.API + path, data=data, method=method, headers={
            "Authorization": f"Bearer {self.token}", "Notion-Version": self.version,
            "Content-Type": "application/json"})
        creates = method == "POST" and path == "/pages"
        for attempt in range(self.tries):
            self._slot()
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return json.load(resp)
            except urllib.error.HTTPError as e:
                msg = e.read().decode(errors="replace")
                if creates and e.code != 429:
                    raise NotionError(f"Notion API {e.code} on {method} {path}, not retried: the page "
                                      f"may have been created; the next run creates it if not: {msg[:600]}")
                if e.code in (429, 500, 502, 503, 504) and attempt < self.tries - 1:
                    delay = float(e.headers.get("Retry-After") or 2 ** attempt)
                    print(f"    {e.code} on {method} {path}, retry in {delay:.0f}s",
                          file=sys.stderr)
                    time.sleep(delay)
                    continue
                raise NotionError(f"Notion API {e.code} on {method} {path}: {msg[:600]}")
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                # URLError covers DNS and connection failures; a socket that
                # times out mid-read raises TimeoutError instead, and both are
                # the same transient thing to a retry loop
                if creates and not never_sent(e):
                    raise NotionError(f"network error on {method} {path}, not retried: the page may "
                                      f"have been created; the next run creates it if not: {e}")
                if attempt < self.tries - 1:
                    print(f"    {type(e).__name__} on {method} {path}, retry in "
                          f"{2 ** attempt}s", file=sys.stderr)
                    time.sleep(2 ** attempt)
                    continue
                raise NotionError(f"network error on {method} {path}: {e}")

    # ------------------------------------------------------------ reads

    def query_all(self, ds_id, filter_=None):
        """Every page of a data source, as the API returns them."""
        out, cursor = [], None
        while True:
            body = {"page_size": 100}
            if filter_:
                body["filter"] = filter_
            if cursor:
                body["start_cursor"] = cursor
            res = self.call("POST", f"/data_sources/{ds_id}/query", body)
            out += res["results"]
            if not res.get("has_more"):
                return out
            cursor = res["next_cursor"]

    def query_sharded(self, ds_id, prop, edges):
        """query_all of a big data source, split on a date property into ranges
        fetched concurrently: before the first edge, between each pair, from the
        last edge on, and the rows with no date at all. Together the shards are
        the whole data source; a cursor walk of one query cannot be parallelised,
        several can.
        """
        edges = sorted(set(edges))
        if not edges:
            return self.query_all(ds_id)
        filters = [{"property": prop, "date": {"before": edges[0]}}]
        for lo, hi in zip(edges, edges[1:]):
            filters.append({"and": [{"property": prop, "date": {"on_or_after": lo}},
                                    {"property": prop, "date": {"before": hi}}]})
        filters.append({"property": prop, "date": {"on_or_after": edges[-1]}})
        filters.append({"property": prop, "date": {"is_empty": True}})
        out, seen = [], set()
        for pages in self.map(lambda f: self.query_all(ds_id, f), filters):
            for page in pages:
                if page["id"] not in seen:
                    seen.add(page["id"])
                    out.append(page)
        return out

    def schema(self, ds_id):
        """The data source object, including every property definition."""
        return self.call("GET", f"/data_sources/{ds_id}")

    def select_options(self, ds_id, prop):
        """The option names of a select property, in Notion's order."""
        p = self.schema(ds_id)["properties"].get(prop)
        if not p:
            raise NotionError(f"data source {ds_id} has no property {prop!r}")
        body = p.get(p["type"]) or {}
        return [o["name"] for o in body.get("options", [])]

    # ------------------------------------------------------------ writes

    def create(self, ds_id, properties):
        """Create one page in a data source. `properties` is API-shaped."""
        self._count_write()
        return self.call("POST", "/pages", {
            "parent": {"type": "data_source_id", "data_source_id": ds_id},
            "properties": properties})

    def patch(self, page_id, properties):
        """Update some properties of one page. `properties` is API-shaped."""
        self._count_write()
        return self.call("PATCH", f"/pages/{page_id}", {"properties": properties})

    def update_block(self, block_id, body):
        """Rewrite one block (the hub's sync-status callout). `body` is the
        block's typed object, e.g. {"callout": {"rich_text": [...]}}."""
        self._count_write()
        return self.call("PATCH", f"/blocks/{block_id}", body)

    def archive(self, page_id):
        """Move one page to the trash. The only destructive call in the code."""
        self._count_write()
        return self.call("PATCH", f"/pages/{page_id}", {"in_trash": True})

    def add_select_options(self, ds_id, prop, values):
        """Add option names to a select property, keeping the ones already there.

        Notion replaces the whole property definition on a PATCH, so the full
        option list has to be sent back, and so does the property's description:
        a PATCH that omits it clears the description.
        """
        p = self.schema(ds_id)["properties"][prop]
        kind = p["type"]                          # select or multi_select
        options = list((p.get(kind) or {}).get("options", []))
        have = {o["name"] for o in options}
        added = [v for v in values if v not in have]
        if not added:
            return []
        options += [{"name": v} for v in added]
        body = {kind: {"options": options}}
        if p.get("description") is not None:
            body["description"] = p["description"]
        self._count_write()
        self.call("PATCH", f"/data_sources/{ds_id}", {"properties": {prop: body}})
        return added


def quarter_edges(first="2024-01-01", until=None):
    """Quarter starts from `first` up to and including the quarter of `until`
    (today by default): the shard edges for a Txn date read. Rows before
    `first` land in the open first shard, so the value only tunes the split."""
    import datetime as dt
    until = until or dt.date.today().isoformat()
    y, m = int(first[:4]), int(first[5:7])
    m = ((m - 1) // 3) * 3 + 1
    out = []
    while f"{y:04d}-{m:02d}-01" <= until:
        out.append(f"{y:04d}-{m:02d}-01")
        m += 3
        if m > 12:
            y, m = y + 1, 1
    return out


# ---------------------------------------------------------------- token

def find_token(cfg, warn=print, env_file=None):
    """The integration token, from the first of these that has one:

    1. the real environment, under the name in [notion] token_env
    2. `.env` in the data folder (mode 600), under that same name; for a
       machine with no keychain
    3. the standard output of [notion] token_command (the macOS keychain)

    An exported variable always wins over `.env`. `env_file` overrides the
    path of step 2, for tests.
    """
    name = cfg.token_env or "NOTION_TOKEN"
    if cfg.token_env:
        env_file = cfg.root / ".env" if env_file is None else env_file
        tok = dotenv.lookup(cfg.token_env, env_file)
        if tok:
            return tok
    if cfg.token_command:
        try:
            r = subprocess.run(cfg.token_command, shell=True, capture_output=True, text=True)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip()
        except OSError:
            pass
    where = [f"store it with `{cfg.token_command}`" if cfg.token_command else
             "set [notion] token_command to a keychain lookup",
             f"or export ${name}", f"or put {name}=... in .env in the data folder"]
    raise NotionError("no Notion token: " + ", ".join(where))


# ---------------------------------------------------------------- property shapes

def to_api(props, types):
    """{name: value} -> the API's typed property objects.

    Values are the plain Python ones `plain` returns: a string for title and
    rich_text, a float or None for number, a name or None for select, an
    ISO date or None for date, a (start, end) pair for date_range, a bool for
    checkbox, a sequence of option names for multi_select, and a sequence of
    page ids for relation. The older "date:NAME:start" keys written by
    the earlier loader emitted are accepted too.
    """
    out = {}
    for name, val in props.items():
        if name.startswith("date:"):
            _, base, part = name.split(":", 2)
            if part == "start":
                out[base] = {"date": {"start": val} if val else None}
            continue
        t = types[name]
        if t == "title":
            out[name] = {"title": [{"text": {"content": str(val)}}] if val else []}
        elif t == "rich_text":                  # 2000 characters per text item
            s = str(val) if val else ""
            out[name] = {"rich_text": [{"text": {"content": s[i:i + 2000]}}
                                       for i in range(0, len(s), 2000)]}
        elif t == "number":
            out[name] = {"number": val}
        elif t == "select":
            out[name] = {"select": {"name": val} if val else None}
        elif t == "date":
            out[name] = {"date": {"start": val} if val else None}
        elif t == "date_range":
            start, end = val if val else (None, None)
            out[name] = {"date": {"start": start, "end": end} if start else None}
        elif t == "checkbox":
            out[name] = {"checkbox": bool(val)}
        elif t == "multi_select":
            out[name] = {"multi_select": [{"name": v} for v in (val or ())]}
        elif t == "relation":
            out[name] = {"relation": [{"id": v} for v in (val or ())]}
        else:
            raise ValueError(f"no shape for {name}: {t}")
    return out


def apply_patch(page, props):
    """Write API-shaped properties onto a page already in memory.

    A patch that Notion accepted has made the page in hand stale, and the run
    holds thousands of them. Rather than read the page back, the same body that
    was sent is written into it, with the `type` key the API returns and
    `to_api` does not send: `plain(page, name)` then reads what was patched.
    Only the properties in `props` move, so anything Notion computes for itself
    (a formula, a rollup) is left as it was read.
    """
    for name, body in props.items():
        page["properties"][name] = {"type": next(iter(body)), **body}
    return page


def plain(page, name):
    """Read one property as the plain Python value `to_api` accepts back.

    title / rich_text / number / select / relation / date, plus checkbox (bool),
    multi_select (list of option names) and date (as a (start, end) pair through
    `date_range`, read with `plain_range`).
    """
    p = page["properties"].get(name)
    if not p:
        return None
    t = p["type"]
    if t == "checkbox":
        return p["checkbox"]
    if t == "multi_select":
        return [o["name"] for o in p["multi_select"]]
    if t in ("title", "rich_text"):
        return "".join(x.get("plain_text", x.get("text", {}).get("content", ""))
                       for x in p[t])
    if t == "number":
        return p["number"]
    if t == "select":
        return p["select"]["name"] if p["select"] else None
    if t == "relation":
        return [x["id"].replace("-", "") for x in p["relation"]]
    if t == "date":
        return p["date"]["start"] if p["date"] else None
    if t == "rollup":
        r = p["rollup"]
        if r["type"] == "array":
            # one entry per related page; a multi_select entry is itself a list
            out = []
            for item in r["array"]:
                v = plain({"properties": {"x": item}}, "x")
                out += v if isinstance(v, list) else [v]
            return [v for v in dict.fromkeys(out) if v is not None]
        if r["type"] == "date":
            return r["date"]["start"] if r["date"] else None
        return r.get(r["type"])
    return None


def plain_range(page, name):
    """A date property, or a date rollup, as the (start, end) pair a
    date_range compares as."""
    p = page["properties"].get(name)
    if not p:
        return None
    d = p["rollup"].get("date") if p["type"] == "rollup" else (
        p["date"] if p["type"] == "date" else None)
    if not d:
        return None
    return (d.get("start"), d.get("end"))
