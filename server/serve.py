"""Serve the built Kopifund web app and its JSON API on this machine only.

The API reads the record (kopifund.db) and the stat results in the data
folder's cache. G19: this file holds the HTTP handler and the route table and
little else. What each endpoint answers is in server/finance/api.py (the GETs),
server/finance/api_writes.py (the POSTs) and server/analytics/api.py (Net worth and the
projection); the data version and the answers already worked out in
server/finance/page_cache.py, the stat results in server/finance/results_cache.py, the
stats rebuild in server/finance/stats_rebuild.py, the upload steps in
server/finance/upload_steps.py, and --reload in dev_reload.py.

    GET /api/health                 is the server up
    GET /api/server                 G23: the server itself, for Settings' Server card: version, how it was
                                    started (serve, dev or app), port, pid and its start time
    GET /api/overview               the home screen's figures
    GET /api/spending               ?from=&to=&scope=sg|all&by=bucket|category|payee: the Spending explorer
    GET /api/recurring              coming up this month (paid, due, late, different amount), active, stopped, marked recurring but irregular
    GET /api/recurring/series?id=   G16: one series' own view: its schedule, payments, status, and what the owner set
    GET /api/results/<id>           one stat's current result
    GET /api/transactions?month=&category=&account=&q=&payee=&named=&key=&out=&from=&to=&home=&spend=&trip=&synthetic=&hide_transfers=&limit=&offset=
    GET /api/trips                  every trip with its spending, days, cost per day and top categories, and the record's last transaction day
    GET /api/countries              every ISO country by alpha-3 code, with its alpha-2 code and flag, for the trip form
    GET /api/run/status             statement folders, Notion on or off, recent loads
    GET /api/rules                  what needs a rule, every rule grouped by payee with its problems, the undo list
    GET /api/rules/rows?text=       the rows behind one needs-rule group (or ?key= for one row)
    GET /api/rules/preview?match_on=&pattern=&payee=&kind=&country=&category=&recurring=&notes=&edit_file=&edit_line=&edit_pattern=&edit_revision=
                                    what saving that rule would change, first match wins across every tier
    GET /api/budgets?month=         planned and actual per category for one month
    GET /api/categories             buckets with their categories, and what each change touches
    GET /api/stats/rebuild          whether the stats rebuild after a category edit is still running
    GET /api/plan                   the forward view's plan (money in cents, `<name>_minor`), categories, choices
    GET /api/exports                every export folder and its files
    GET /api/setup                  whether this is a first run, and the providers to pick
    GET /api/profile                the data folder, accounts, statement folders, and about you
    GET /api/net-worth              cash, investments and CPF by month, each carried from its last measured date
    GET /api/suggestions            G13: figures the record knows, offered beside the Plan and Net worth inputs
                                    (server/finance/suggestions.py): pay still arriving, brokerage deposits, the employer
                                    CPF rate for the age, insurance and repayments
    GET /api/growth                 net worth drawn forward on the saved inputs: cash, Tiger Brokers, CPF and
                                    their total (server/analytics/growth.py); ?horizon=&dollars= and any rate override them
                                    for one answer
    GET /api/scam-checks            G15: the thresholds, the flagged rows (newest first, each with its reasons) and the rows marked fine
    GET /api/app-icon               the icon sets in app-icons/sets.json, the default and the one picked in Settings
    GET /api/uploads?limit=&offset=  upload attempts (50 by default, at most 100), newest first, with their steps' outline and the total
    GET /api/uploads/<id>           one attempt with each step's input and output
    GET /api/exports/<stamp>/<file> one exported file, as a download
    GET /api/ai                     G22: the AI card on Settings: the provider, the two models, where the key is
                                    kept and its last four characters (never the key), the consent switch
    GET /api/ai/models?provider=&q= G22: the provider's models matching q, from the server's day-old list at most
    GET /api/copilot/threads        G22: Ask Kopifund's saved threads, newest first
    GET /api/copilot/threads/<id>   G22: one thread with its messages
    GET /api/link/devices           G25: the paired phones, and the tailnet URL the pair QR carries
    GET /api/link/pending           G25: the phone rows waiting for a statement, matched or dismissed
                                    (server/finance/pending.py); loopback only, like every read
                                    not in LINK_READS
    GET /api/dashboard/layouts      G26: the saved dashboard layouts, and the active one's id
    GET /api/dashboard/layouts/<id> G26: one layout, 404 when the id is not one
    GET /api/dashboard/query?q=     G26: a WidgetQuery as JSON, resolved to labels and series in
                                    cents (server/finance/dashboard.py): the app's useWidgetData asks this
    GET /api/dashboard/goals        G26: the goals, each with its milestones and the progress its
                                    binding resolves to
    GET /api/dashboard/allocation?month=  G26: the allocation template beside a month's actuals
    GET /api/dashboard/game-pot     G26: the game pot's settings, and the accounts it may link

Writes (POST) need the header `X-Kopifund: 1`, which a page on another site
cannot send without a CORS preflight this server never answers, and an Origin,
when there is one, on this machine.

G25: a request carrying `X-Forwarded-For` arrived through `tailscale serve`
(SPEC.md, "Phone link (G25)"), which forwards HTTPS from the tailnet to
loopback. Those requests take the link path instead of the rules above:
`/api/health` stays open, every other GET needs `Authorization: Bearer
<device token>` of a paired phone (`server/finance/link.py`) and is answered
only for the reads in LINK_READS (profile, transactions, categories,
overview), pages are never served, and the only writes are
`/api/pair/redeem` (unauthenticated: the one-time token is the credential)
and `/api/link/transactions` under a bearer. A request
without the header keeps the loopback path, so the header can only lose
privilege, never gain it.

    POST /api/uploads/start         {origin}: a new upload attempt, its id
    POST /api/statements?folder=&name=  one statement PDF as the body
    POST /api/statements/auto?name=     one statement PDF, kept in the folder its own text names; one it
                                    cannot place is answered `needs-account` with the folders it could be,
                                    and kept nowhere (server/finance/sniff.py)
    POST /api/run/check             parse and check everything, write no transactions
    POST /api/run/load              check, then write kopifund.db and rerun the stats
    POST /api/run/mirror            `finance.py sync`, the Notion mirror
    The four steps take ?attempt=<id>&from=<page> and are each recorded in the
    upload history (server/finance/upload_log.py), refused or failed ones included.
    POST /api/rules/remove          take one personal rule out ({file, line, pattern?, revision?})
    POST /api/rules/save            {rule, edit?: {file, line, pattern?, revision?}}: add or edit a rule in its personal file
    POST /api/rules/undo            put the personal rules back as they were before the last write ({change?}: only that one),
                                    and the rows it relinked
    POST /api/rules/apply           relink every row the record holds differently from what the rules and trips say now
    G13: every rule and trip write relinks the rows it decides differently at once, as a load would
    (server/finance/relink.py), answers with {relinked: {changed, written}}, and reruns the stats in the background
    POST /api/rules/suggest         {groups}: AI payee suggestions for needs_rule groups
    POST /api/rules/batch           {rules}: add several personal rules, all or none
    POST /api/rules/pin             {key, category, payee?, kind?, note?}: a key rule for one row
    G17: the writes below name a trip, category or bucket by `id` (and the one it goes to by
    `group_id` or `move_to_id`). A name is still taken where no id is sent, for one release;
    a name sent beside an id is the name the page read, and a pair that disagrees is refused;
    a move or a delete by id needs the name beside each id it sends
    POST /api/budgets               {category_id, category?, month, planned (cents or null)}
    POST /api/budgets/copy          {from, to}: last month's plan into this one
    POST /api/plan                  replace the plan, money in cents as `<name>_minor` (dollars under the bare name still read)
    POST /api/growth/preview        {income, investing, projection}: the projection on unsaved inputs, nothing written
    POST /api/growth/inputs         {income, investing, projection}: save them into the plan, then the projection
    POST /api/profile               save about-you fields (date of birth) to the data folder
    POST /api/categories            {id, name?, need_class}
    POST /api/categories/add        {name, group_id, group?, need_class?}: a category at the end of a bucket
    POST /api/categories/rename     {id, name?, new_name}: personal rules follow, the old name stays an alias
    POST /api/categories/move       {id, name, group_id, group, index?}: into a bucket (or within one), at index
    POST /api/categories/delete     {id, name, move_to_id?, move_to?}: rows, budgets and personal rules move first (move_to_id is optional for a category nothing points at)
    POST /api/category-groups/add   {name}: a new spending bucket
    POST /api/category-groups/rename  {id, name?, new_name}
    POST /api/category-groups/reorder {ids, names?}: every bucket, in order
    POST /api/category-groups/delete  {id, name, move_to_id?, move_to?}: a spending bucket; its categories move first
    POST /api/category-groups/style   {id, name?, icon?, colour?}: a bucket's icon and colour (null for the default)
    POST /api/trips                 {id?, old_name?, name, start, end, countries, notes, extra_keys}: no id makes a new trip
    POST /api/trips/remove          {id, name}: answers with `restore`, what an Undo sends back
    POST /api/trips/restore         {trip, txn_keys, moved?}: put a removed trip back under its id, with the rows it counted
    POST /api/trips/undo            {undo}: take back a trip save, with the rows it relinked
    POST /api/export                write a dated export of the record and the stats
    POST /api/scam-checks/settings  {big_transfer?, repeat_count?, ...}: save the thresholds, answer with the page
    POST /api/scam-checks/fine      {key, fine?}: mark a flagged row fine (fine: false flags it again), answer with the page
    POST /api/app-icon              {name}: pick an icon set, answer with the sets; --app prints ICON and its name
    POST /api/recurring/series      G16: {id, recurring?, amount?, cadence?, day?, ended_on?}: one series' edit, each
                                    one sent set (null for as detected), each left out kept; saved in the record,
                                    answers with its view
    POST /api/recurring/payee       G16: {payee, recurring: false or null}: a payee the rules mark recurring is not
    POST /api/setup                 first run only: {providers: [...]}
    POST /api/ai/settings           G22: {provider?, model?, copilot_model?, send_rows?}: saved in the record's
                                    setting table, answers with the AI card; provider null (or "off") turns AI
                                    off over [ai] until a provider is saved again; the provider config.toml
                                    names (config_provider, a custom one too) clears Settings' choice
    POST /api/ai/key                G22: {provider, key}: kept in the Keychain (or the data folder's .env)
    POST /api/ai/key/remove         G22: {provider}: the key Settings saved, taken out
    POST /api/ai/test               G22: one short request to the provider: whether it answered, and how fast
    POST /api/copilot/ask           G22: {thread, question, page}: the answer as application/x-ndjson, one
                                    event per line as it happens (thread, tools, text, charts, links, cards,
                                    follow-ups, done); refused before the first line when the copilot is off
                                    or sending rows is not turned on
    POST /api/copilot/threads/remove  G22: {id}: one thread gone, answers with the list
    POST /api/link/pair-token       G25: mint a single-use pair token for the Settings QR (loopback only)
    POST /api/pair/redeem           G25: {token, name}: spend the pair token for a device bearer, once,
                                    under the registry's flock; the one link write with no bearer
    POST /api/link/devices/revoke   G25: {id}: flag a paired phone's row so its bearer stops answering
    POST /api/link/transactions     G25: {rows:[{import_id,date,amount_minor,currency,payee}]}: the phone's
                                    approved rows upsert into pending_txn, unique on import_id so a retry
                                    never doubles; link path, bearer required (server/finance/pending.py)
    POST /api/link/pending/dismiss  G25: {import_id}: a waiting row is no spend, never matched (loopback)
    POST /api/link/pending/restore  G25: {import_id}: a matched or dismissed row back to waiting; a match
                                    it was in joins its rejected list and is never offered again (loopback)
    POST /api/server/stop           G23: answer with the work it waits for, refuse new writes, wait for
                                    every request and the stats rebuild to end (DRAIN_TIMEOUT at most),
                                    then stop the server. Nothing can start it from the page
                                    it served: the desktop shell's start page, or `make serve`, does
    POST /api/server/restart        G23: as stop, then run the same command line again in place of this
                                    process: under --app the shell's hold on stdin and the new ready line
                                    work as on first launch, so its window reloads itself
    POST /api/dashboard/layouts     G26: {layouts, active?}: replace the whole list of layouts
    POST /api/dashboard/layouts/<id> G26: {name, widgets?, active?}: one layout under the id the path names
    POST /api/dashboard/query       G26: a WidgetQuery as the body, the same answer as the GET; it
                                    writes nothing (NO_CHANGE)
    POST /api/dashboard/goals       G26: {id?, name, target_minor, month?, note?, milestones?, source?,
                                    metric?}: no id creates the goal, an id patches its fields
    POST /api/dashboard/goals/remove G26: {id}: one goal gone
    POST /api/dashboard/scenario/preview G26: {query, months?, delta_minor?}: the query's series drawn
                                    ahead on its trailing monthly average: an estimate, nothing written
    POST /api/dashboard/allocation  G26: {buckets: [{bucket, planned_minor}]}: the template, answered
                                    with the month's actuals
    POST /api/dashboard/game-pot    G26: {name, benchmark?, note?, account_ids?}: the pot's settings

G19: GET_ROUTES and POST_ROUTES, near the end of this file, are the one list of
endpoints: a path (or a pattern like /api/uploads/<id>) and the function that
answers it. server/tests/test_serve_routes.py checks that every endpoint above is in
them and every endpoint they hold is above, and that every /api/ path the web
app calls has a route. A request opens at most one connection to the record,
and the server closes it when the request ends.

G13: the GETs in CACHED answer with an ETag, the data version
(page_cache.data_version: this process, today's date, and the size and time
of every file in the data folder they read). A request carrying that tag in If-None-Match gets a 304,
and an answer already worked out for the same path, query and version is sent
again without working it out. Every other GET is worked out each time.

    python3 server/serve.py                 # http://127.0.0.1:8770
    python3 server/serve.py --port 9000
    python3 server/serve.py --reload        # restart whenever a Python file changes (make dev)
    python3 server/serve.py --page URL      # send pages to URL, the Vite dev server (make dev)
    python3 server/serve.py --app           # the desktop app's sidecar on http://127.0.0.1:8771

G23: --app is how the desktop shell runs the server. It listens on APP_PORT,
fixed so the page's origin (and its local storage) is the same every launch,
prints READY and the address once it is listening, and stops when its standard
input closes, so a shell that quits or crashes never leaves a server behind.
It also prints ICON and the app icon set picked in Settings (finance/app_icon.py),
once it is listening and again after every pick, which the shell makes its Dock icon.
"""

from __future__ import annotations

import argparse
import contextvars
import importlib
import json
import mimetypes
import os
import re
import sqlite3
import sys
import threading
import time
from contextlib import closing, contextmanager
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, NamedTuple
from urllib.parse import parse_qs, unquote, urlsplit

import dev_reload
from analytics import api as growth_api
from analytics import run as stats_run
from finance import api, api_writes, app_icon, link, monthly, page_cache, stats_rebuild
from finance.api import Download, Stream
from finance.errors import NotFound, Refused

# G19: finance never imports analytics, so the app, which holds both, hands it
# the stats rebuild that a load and an edit end with
monthly.rebuild_stats_with(stats_run.rebuild)

ROOT = Path(__file__).resolve().parent          # server/
DIST = ROOT.parent / "apps" / "web" / "dist"    # the built web app
HOST = "127.0.0.1"
PORT = 8770
APP_PORT = 8771
READY = "KOPIFUND_READY"
ICON = "KOPIFUND_ICON"
VERSION = "0.1.0"


MAX_BODY = 25 * 1024 * 1024


def _local_origin(origin: str | None) -> bool:
    """No Origin (a same-origin GET-style client) or one on this machine."""
    if not origin:
        return True
    host = urlsplit(origin).hostname or ""
    return host in ("127.0.0.1", "localhost", "::1")


def _local_host(host: str | None) -> bool:
    """A Host header naming this machine, with or without a port. A
    DNS-rebinding page reaches 127.0.0.1 under its own hostname, so any other
    name is refused before anything is read."""
    if not host:
        return False
    return (urlsplit(f"//{host}").hostname or "") in ("127.0.0.1", "localhost", "::1")


# ---------------------------------------------------------------- G13: caching

# What the cached GETs read and the answers already worked out are in
# server/finance/page_cache.py; the routes they cover are listed here, beside the
# route table.

# The GETs whose answer depends only on the data folder, the query and today's
# date, so a version of those says when an answer can be reused. Everything
# else is live: health, the upload page's status, the stats rebuild it polls,
# the upload history every step writes, first run, the export listing and its
# downloads, and the rule preview, which is a what-if per keystroke. G19: the
# app's copy is generated from this set (types.gen.ts), so it names only routes.
CACHED = frozenset({
    "/api/overview", "/api/spending", "/api/recurring", "/api/recurring/series", "/api/transactions", "/api/trips",
    "/api/rules", "/api/rules/rows", "/api/budgets", "/api/categories", "/api/plan", "/api/profile",
    "/api/net-worth", "/api/growth", "/api/scam-checks", "/api/suggestions",
    # G26: the dashboard's reads, whose answers hold only the record and the stats
    "/api/dashboard/layouts", "/api/dashboard/query", "/api/dashboard/goals", "/api/dashboard/allocation",
    "/api/dashboard/game-pot",
})
# and every stat's own result
CACHED_PREFIX = "/api/results/"

# G25: the writes the tailscale-serve path answers. pair/redeem is
# unauthenticated (the one-time token is the credential); a write in
# LINK_BEARER_WRITES needs a device bearer like the link GETs.
LINK_WRITES = frozenset({"/api/pair/redeem"})
LINK_BEARER_WRITES = frozenset({"/api/link/transactions"})
# G25: the only GETs the link path answers to a paired bearer, the reads the
# phone needs (SPEC.md, "Phone link (G25)"). An allowlist, so a route added
# later stays off the link until it is listed here: a lost phone, before it
# is revoked, must not pull exports, uploads, AI threads or folder paths.
LINK_READS = frozenset({"/api/profile", "/api/transactions", "/api/categories", "/api/overview"})

# The writes that change nothing a GET reads: a check, an AI suggestion, a
# new upload attempt, a projection preview. A check and a new attempt write
# the upload history, which is live, and the record's file times change with
# them anyway. G17: apps/web/src/api.ts reads CACHED, CACHED_PREFIX and NO_CHANGE
# from apps/web/src/types.gen.ts, which `make types` writes from here.
NO_CHANGE = frozenset({"/api/run/check", "/api/rules/suggest", "/api/uploads/start", "/api/growth/preview",
                       # G22: a question, a test request and a thread removed touch no page's data
                       "/api/copilot/ask", "/api/copilot/threads/remove", "/api/ai/test",
                       # G25: the device registry is no page's data either
                       "/api/link/pair-token", "/api/pair/redeem", "/api/link/devices/revoke",
                       # G23: nor is the server's own stop or restart (the next ask answers for itself)
                       "/api/server/stop", "/api/server/restart",
                       # G26: a widget query and a scenario preview read, they change nothing
                       "/api/dashboard/query", "/api/dashboard/scenario/preview"})
# G18: after one of these writes the app asks again for the GETs named, with
# their tags, and for nothing else: every other answer it keeps stays (any other
# write forgets them all, unless it is in NO_CHANGE). A check writes the upload
# history, and the overview's Needs your eye reads where the latest upload
# stands. G26: the dashboard's own state is read by its own GET alone, and a
# layout is saved on every drag, resize and board switch; forgetting every
# widget's answer each time would ask for them all afresh. Those writes still
# move the server's version (they are not in NO_CHANGE), so their GET's tag
# changes. types.gen.ts carries this too.
REREAD_AFTER = {"/api/run/check": ("/api/overview",),
                "/api/dashboard/layouts": ("/api/dashboard/layouts",),
                "/api/dashboard/layouts/<id>": ("/api/dashboard/layouts",),
                "/api/dashboard/goals": ("/api/dashboard/goals",),
                "/api/dashboard/goals/remove": ("/api/dashboard/goals",),
                "/api/dashboard/allocation": ("/api/dashboard/allocation",),
                "/api/dashboard/game-pot": ("/api/dashboard/game-pot",)}


def cached(path: str) -> bool:
    return path in CACHED or path.startswith(CACHED_PREFIX)


def make_handler(dist: Path, page: str | None = None) -> type[BaseHTTPRequestHandler]:
    """`page`, when set, is where the app's pages live instead (Vite under
    `make dev`): a page asked of this server is sent there, so an old build in
    apps/web/dist is never what the browser shows."""
    dist = dist.resolve()

    class Handler(BaseHTTPRequestHandler):
        server_version = f"Kopifund/{VERSION}"

        def foreign_host(self) -> bool:
            if _local_host(self.headers.get("Host")):
                return False
            self.send_json({"error": "This server answers only on 127.0.0.1 or localhost"},
                           HTTPStatus.MISDIRECTED_REQUEST)
            return True

        def _proxied(self) -> bool:
            """G25: arrived through `tailscale serve`, which forwards to
            loopback with the caller's tailnet IP in X-Forwarded-For."""
            return bool(self.headers.get("X-Forwarded-For"))

        def _link_device(self):
            """G25: the paired device this request's bearer names, or None."""
            auth = self.headers.get("Authorization", "")
            bearer = auth[7:] if auth.startswith("Bearer ") else ""
            return link.device_for(bearer)

        def _unpaired(self) -> None:
            self.send_response(HTTPStatus.UNAUTHORIZED)
            self.send_header("Content-Type", "application/json")
            self.send_header("WWW-Authenticate", 'Bearer realm="kopifund"')
            body = b'{"error": "This request needs a paired device"}'
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        @_counted
        def do_GET(self) -> None:  # noqa: N802 (http.server naming)
            path = unquote(urlsplit(self.path).path)
            if self._proxied():
                # G25: the link path. Health stays open so an app can check
                # reachability before it pairs; every other answer needs a
                # paired device's bearer, and then only the reads in
                # LINK_READS are served. Pages never are.
                if path == "/api/health":
                    self.api(path)
                    return
                if self._link_device() is None:
                    self._unpaired()
                    return
                if path in LINK_READS:
                    self.api(path)
                elif path.startswith("/api/"):
                    self.send_json({"error": "This route is not served on the link"},
                                   HTTPStatus.FORBIDDEN)
                else:
                    self.send_json({"error": "Pages are not served on the link"},
                                   HTTPStatus.FORBIDDEN)
                return
            if self.foreign_host():
                return
            if path.startswith("/api/"):
                self.api(path)
            else:
                self.static(path)

        def do_HEAD(self) -> None:  # noqa: N802
            self.do_GET()

        def refuse(self) -> None:
            self.send_error(HTTPStatus.METHOD_NOT_ALLOWED)

        do_PUT = do_PATCH = do_DELETE = refuse  # noqa: N815

        @_counted
        def do_POST(self) -> None:  # noqa: N802
            proxied = self._proxied()
            if not proxied and self.foreign_host():
                return
            path = unquote(urlsplit(self.path).path)
            if not path.startswith("/api/") or path == "/api/health":
                self.refuse()
                return
            device = None
            if proxied:
                # G25: the link path answers the pairing write, and the
                # bearer-guarded phone writes, nothing else.
                if path in LINK_BEARER_WRITES:
                    device = self._link_device()
                    if device is None:
                        self._unpaired()
                        return
                elif path not in LINK_WRITES:
                    self.send_json({"error": "This write is not accepted on the link"},
                                   HTTPStatus.FORBIDDEN)
                    return
            elif self.headers.get("X-Kopifund") != "1" or not _local_origin(self.headers.get("Origin")):
                self.send_json({"error": "Writes are only accepted from the Kopifund app"},
                               HTTPStatus.FORBIDDEN)
                return
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                self.send_json({"error": "That upload is too large"}, HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
                return
            body = self.rfile.read(length) if length else b""
            query = {k: v[-1] for k, v in parse_qs(urlsplit(self.path).query).items()}
            try:
                try:
                    with _owning():
                        out = write(path, query, body, device=device)
                finally:
                    # refused ones too: a refusal can come after part of the work
                    if path not in NO_CHANGE:
                        page_cache.wrote()
                if isinstance(out, Stream):
                    self.stream(out)
                else:
                    self.send_json(out)
            except NotFound as e:
                self.send_json({"error": str(e) or "Not found"}, HTTPStatus.NOT_FOUND)
            except Refused as e:
                self.send_json({"error": str(e)}, HTTPStatus.UNPROCESSABLE_ENTITY)
            except ValueError as e:
                self.send_json({"error": str(e)}, HTTPStatus.BAD_REQUEST)

        def api(self, path: str) -> None:
            query = {k: v[-1] for k, v in parse_qs(urlsplit(self.path).query).items()}
            try:
                with _owning():
                    if cached(path):
                        # the version first, so a 304 works nothing out
                        version = page_cache.data_version(statements=path == "/api/profile")
                        etag = f'"{version}"'
                        if page_cache.etag_matches(self.headers.get("If-None-Match"), etag):
                            self.send_body(b"", HTTPStatus.NOT_MODIFIED, etag)
                        else:
                            self.send_body(page_cache.cached_answer(path, query, version, answer), HTTPStatus.OK,
                                           etag)
                    else:
                        out = answer(path, query)
                        if isinstance(out, Download):
                            self.download(out)
                        else:
                            self.send_json(out)
            except NotFound as e:
                self.send_json({"error": str(e) or "Not found"}, HTTPStatus.NOT_FOUND)
            except ValueError as e:
                self.send_json({"error": str(e)}, HTTPStatus.BAD_REQUEST)

        def static(self, path: str) -> None:
            if page:
                # the path as it arrived, still encoded, so "%3F" stays part of the path
                raw = urlsplit(self.path)
                self.send_response(HTTPStatus.TEMPORARY_REDIRECT)
                self.send_header("Location", page.rstrip("/") + raw.path + (f"?{raw.query}" if raw.query else ""))
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            target = (dist / path.lstrip("/")).resolve()
            if not target.is_relative_to(dist):
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            if target.is_dir():
                target = target / "index.html"
            if not target.is_file():
                # A path with no extension is a page of the app: hand it index.html
                # so the browser's router can draw it. A missing asset is a 404.
                if Path(path).suffix:
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
                target = dist / "index.html"
            if not target.is_file():
                self.send_error(HTTPStatus.SERVICE_UNAVAILABLE, "apps/web/dist is missing: run `make build`")
                return
            body = target.read_bytes()
            kind = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            cache = "no-cache" if target.name in {"index.html", "sw.js"} else "public, max-age=31536000, immutable"
            self.send_header("Cache-Control", cache)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def download(self, file: Download) -> None:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", f"{file.kind}; charset=utf-8")
            self.send_header("Content-Disposition", f'attachment; filename="{file.filename}"')
            self.send_header("Content-Length", str(len(file.body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(file.body)

        def stream(self, out: Stream) -> None:
            """G22: one line of JSON per event, each written and flushed as it
            comes, with no length up front; the connection closes after the
            last line, which is how the page knows the answer is whole."""
            self.close_connection = True
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", out.kind)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Connection", "close")
            self.end_headers()
            events = iter(out.events)
            try:
                for event in events:
                    self.wfile.write(json.dumps(event).encode() + b"\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass                     # the page went away; a turn it left saves nothing
            finally:
                close = getattr(events, "close", None)
                if close:
                    close()

        def send_json(self, payload: object, status: HTTPStatus = HTTPStatus.OK) -> None:
            self.send_body(json.dumps(payload).encode(), status)

        def send_body(self, body: bytes, status: HTTPStatus, etag: str | None = None) -> None:
            # no-store either way: the browser keeps no copy of its own, and
            # the app's cache (apps/web/src/api.ts) is the only one
            self.send_response(status)
            if status != HTTPStatus.NOT_MODIFIED:
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
            if etag:
                self.send_header("ETag", etag)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command != "HEAD" and body:
                self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            pass

    return Handler


# ------------------------------------------------------- G19: one request

# Whether the connection a request opens is the request's to close. The HTTP
# handler says yes (_owning) around each request. A direct call of answer or
# write, as the tests make, hands over the record through `api.open_record` or
# `db.connect` and goes on using it, so that connection is left open.
_OWNS = contextvars.ContextVar("kopifund_request_owns_its_connection", default=False)


@contextmanager
def _owning():
    token = _OWNS.set(True)
    try:
        yield
    finally:
        _OWNS.reset(token)


class Request:
    """One call of one route: what was asked, and the one connection to the
    record it may open. The dispatcher makes it, hands it to the route's
    function, and closes the connection in a finally, so a route that fails
    or refuses leaves nothing open."""

    def __init__(self, method: str, path: str, query: dict, body: bytes = b"", args: dict | None = None,
                 device: dict | None = None):
        self.method = method
        self.path = path
        self.query = query
        self.body = body
        self.args = args or {}           # the <names> of a path pattern
        self.cfg = None                  # the config, for a route that asks for it (Route.config)
        self.data: dict = {}             # the body as a JSON object, for a route that reads one (Route.json_body)
        self.device = device             # G25: the paired device the gate resolved, else None
        self._conn = None
        self._owns = _OWNS.get()

    def record(self, create: bool = False):
        """The record's connection, opened on first use and the same one after.
        NotFound before the first load, unless `create`: a write that may
        start the record."""
        if self._conn is None:
            from finance import db
            self._conn = db.connect() if create else api.open_record()
        return self._conn

    def maybe_record(self):
        """The record's connection, or None before the first load."""
        try:
            return self.record()
        except NotFound:
            return None

    def close(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None and self._owns:
            conn.close()


class Route(NamedTuple):
    """One endpoint: the function that answers it, and what the dispatcher does first."""
    handler: Callable[[Request], object]
    tag: str | None = None               # the family it belongs to; also names the refusal it speaks (_REFUSALS)
    config: bool = False                 # load the config first (NotFound before the first run)
    json_body: bool = False              # read the body as a JSON object into Request.data (_json_body)


# a family's own refusal is a 422 `Refused` on the way out, whichever route of it raised it
_REFUSALS = {
    "planning": ("finance.planning", "PlanRefused"),
    "growth": ("finance.planning", "PlanRefused"),
    "rules": ("finance.rules_app", "RuleRefused"),
    "categories": ("finance.categories", "CategoryRefused"),
    "scam-checks": ("finance.scam_checks", "SettingsRefused"),
    "recurring": ("finance.series", "SeriesRefused"),
    "profile": ("finance.profile", "ProfileRefused"),
    "setup": ("finance.setup", "SetupRefused"),
    "ai": ("finance.ai", "AIRefused"),
    "dashboard": ("finance.dashboard", "DashboardRefused"),
}


class _NeverRaised(Exception):
    pass


def _refusal(tag: str | None) -> type[Exception]:
    if tag not in _REFUSALS:
        return _NeverRaised
    module, name = _REFUSALS[tag]
    return getattr(importlib.import_module(module), name)


def _json_body(body):
    try:
        data = json.loads(body or b"{}")
    except ValueError as e:
        raise ValueError("The body is not JSON") from e
    if not isinstance(data, dict):
        raise ValueError("The body is a JSON object")
    return data


def _dispatch(method: str, path: str, query: dict, body: bytes = b"", device: dict | None = None) -> object:
    """Find the route for this method and path, run it, and close the
    connection it opened. NotFound when there is none."""
    found = resolve(method, path)
    if found is None:
        raise NotFound("not found")
    route, args = found
    req = Request(method, path, query, body, args, device=device)
    try:
        if route.config:
            req.cfg = api.config()
        if route.json_body:
            req.data = _json_body(body)
        try:
            return route.handler(req)
        except _refusal(route.tag) as e:
            raise Refused(str(e)) from e
    finally:
        req.close()


# ---------------------------------------------------------------- health

def _get_health(req):
    return {"ok": True, "version": VERSION}


# ----------------------------------------------- G23: the process itself

STARTED = time.time()
_MODE = "serve"                              # make_server sets it from how the server was started
_PORT = PORT
_SERVER: ThreadingHTTPServer | None = None
_RESTART = False


def _get_server(req):
    return {"version": VERSION, "mode": _MODE, "port": _PORT, "pid": os.getpid(), "started": STARTED}


# A stop or restart waits for the work already under way: every request being
# answered (a load writing the record, a save) and the stats rebuild a write
# started in the background. Request threads are daemons and a restart
# execs, so without the wait that work would be cut off mid-run. The wait is
# bounded, so a stuck request cannot keep the server up for good, and while
# it lasts every new write is refused, so no new load starts only to be cut.
DRAIN_TIMEOUT = 120                          # seconds
_BUSY = threading.Condition()
_INFLIGHT: dict[tuple[str, str], int] = {}   # (method, path) -> requests being answered
_STOPPING = False
_ENDS = frozenset({"/api/server/stop", "/api/server/restart"})
# what the card names while it waits, for the writes that take a while
_WORK_WORDS = {"/api/run/load": "a load", "/api/run/check": "a check", "/api/run/mirror": "the Notion mirror",
               "/api/statements": "an upload", "/api/export": "an export"}


def _counted(method: Callable) -> Callable:
    """Wrap a do_GET or do_POST so a stop or restart knows it is running."""
    def counted(self) -> None:
        key = (self.command, unquote(urlsplit(self.path).path))
        with _BUSY:
            if _STOPPING and self.command == "POST":
                refused = True
            else:
                refused = False
                _INFLIGHT[key] = _INFLIGHT.get(key, 0) + 1
        if refused:
            self.send_json({"error": "The server is stopping, so it takes no new changes: "
                                     "try again once it is back"}, HTTPStatus.SERVICE_UNAVAILABLE)
            return
        try:
            method(self)
        finally:
            with _BUSY:
                _INFLIGHT[key] -= 1
                if not _INFLIGHT[key]:
                    del _INFLIGHT[key]
                _BUSY.notify_all()
    return counted


def _running_work(reads: bool = False) -> list[str]:
    """The work a stop waits for, in the card's words: the writes under way
    but the stop itself, and the stats rebuild. GETs are waited for too, and
    named only with `reads` (the warning when the wait runs out), as the
    card has no use for a read that takes a moment."""
    with _BUSY:
        posts = [path for (method, path) in _INFLIGHT if method == "POST" and path not in _ENDS]
        gets = sum(n for (method, _path), n in _INFLIGHT.items() if method in ("GET", "HEAD"))
    words = list(dict.fromkeys(_WORK_WORDS.get(p, "a save") for p in sorted(posts)))
    if stats_rebuild.running():
        words.append("the stats rebuild")
    if reads and gets:
        words.append("1 read" if gets == 1 else f"{gets} reads")
    return words


def _drain(timeout: float | None = None) -> list[str]:
    """Wait until no request is being answered and no stats rebuild runs, for
    at most `timeout` seconds (DRAIN_TIMEOUT). The work still running at the
    deadline, in the card's words, or [] when everything finished."""
    deadline = time.monotonic() + (DRAIN_TIMEOUT if timeout is None else timeout)
    with _BUSY:
        while _INFLIGHT or stats_rebuild.running():
            left = deadline - time.monotonic()
            if left <= 0:
                break
            # a request's end notifies; the stats rebuild does not, so look again shortly
            _BUSY.wait(min(left, 0.25))
    return _running_work(reads=True) if (_INFLIGHT or stats_rebuild.running()) else []


def _after_response(fn: Callable[[], None]) -> None:
    """Run `fn` once this request's answer has had a moment to leave, so the
    stop or restart that ends this process never eats its own reply."""
    threading.Timer(0.4, fn).start()


def _stop() -> None:
    left = _drain()
    if left:
        print(f"Stopping after {DRAIN_TIMEOUT} s with {', '.join(left)} still running: "
              "it ends unfinished, and the record keeps what it last committed", file=sys.stderr, flush=True)
    if _SERVER is not None:
        _SERVER.shutdown()


def _restart() -> None:
    global _RESTART
    _RESTART = True
    _stop()


def _ending(fn: Callable[[], None]) -> dict:
    """Stop taking writes, answer with the work the exit waits for, then end."""
    global _STOPPING
    with _BUSY:
        _STOPPING = True
    _after_response(fn)
    return {"ok": True, "waiting_for": _running_work(), "timeout": DRAIN_TIMEOUT}


def _post_server_stop(req):
    return _ending(_stop)


def _post_server_restart(req):
    return _ending(_restart)


# ----------------------------------------------------- G19: the route tables

# One table per method: a path, or a pattern with <names> in it, and the
# function that answers it. A request is found here and nowhere else, so a
# route is either in the table or not found; server/tests/test_serve_routes.py checks
# the table against the docstring above and against every /api/ path the web
# app calls. A write's `tag` names its family, and the family's own refusal
# (_REFUSALS) answers 422 for each of its routes. Each route's function is
# its endpoint's answer, in server/finance/api.py (GET), server/finance/api_writes.py
# (POST) or server/analytics/api.py (Net worth and the projection).
GET_ROUTES: dict[str, Route] = {
    "/api/health": Route(_get_health),
    "/api/server": Route(_get_server),
    "/api/overview": Route(api.get_overview),
    "/api/spending": Route(api.get_spending),
    "/api/recurring": Route(api.get_recurring),
    "/api/recurring/series": Route(api.get_series),
    "/api/results/<id>": Route(api.get_result),
    "/api/transactions": Route(api.get_transactions),
    "/api/trips": Route(api.get_trips),
    "/api/countries": Route(api.get_countries),
    "/api/run/status": Route(api.get_run_status, config=True),
    "/api/rules": Route(api.get_rules),
    "/api/rules/rows": Route(api.get_rules_rows),
    "/api/rules/preview": Route(api.get_rules_preview),
    "/api/budgets": Route(api.get_budgets),
    "/api/categories": Route(api.get_categories),
    "/api/stats/rebuild": Route(api.get_stats_rebuild),
    "/api/plan": Route(api.get_plan),
    "/api/exports": Route(api.get_exports),
    "/api/exports/<stamp>/<file>": Route(api.get_export_file),
    "/api/setup": Route(api.get_setup),
    "/api/profile": Route(api.get_profile, config=True),
    "/api/net-worth": Route(growth_api.get_net_worth),
    "/api/suggestions": Route(api.get_suggestions),
    "/api/growth": Route(growth_api.get_growth),
    "/api/uploads": Route(api.get_uploads),
    "/api/uploads/<id>": Route(api.get_upload),
    "/api/scam-checks": Route(api.get_scam_checks),
    "/api/app-icon": Route(api.get_app_icon),
    "/api/ai": Route(api.get_ai),
    "/api/ai/models": Route(api.get_ai_models),
    "/api/copilot/threads": Route(api.get_copilot_threads),
    "/api/copilot/threads/<id>": Route(api.get_copilot_thread),
    "/api/link/devices": Route(api.get_link_devices),
    "/api/link/pending": Route(api.get_link_pending),
    "/api/dashboard/layouts": Route(api.get_dashboard_layouts),
    "/api/dashboard/layouts/<id>": Route(api.get_dashboard_layout),
    "/api/dashboard/query": Route(api.get_dashboard_query),
    "/api/dashboard/goals": Route(api.get_dashboard_goals),
    "/api/dashboard/allocation": Route(api.get_dashboard_allocation),
    "/api/dashboard/game-pot": Route(api.get_dashboard_game_pot),
}

POST_ROUTES: dict[str, Route] = {
    "/api/uploads/start": Route(api_writes.post_uploads_start, config=True, json_body=True),
    "/api/statements": Route(api_writes.post_upload, config=True),
    "/api/statements/auto": Route(api_writes.post_upload_auto, config=True),
    "/api/run/check": Route(api_writes.post_check, config=True),
    "/api/run/load": Route(api_writes.post_load, config=True),
    "/api/run/mirror": Route(api_writes.post_mirror, config=True),
    "/api/rules/remove": Route(api_writes.post_rules_remove, "rules", config=True, json_body=True),
    "/api/rules/save": Route(api_writes.post_rules_save, "rules", config=True, json_body=True),
    "/api/rules/undo": Route(api_writes.post_rules_undo, "rules", config=True, json_body=True),
    "/api/rules/apply": Route(api_writes.post_rules_apply, "rules", config=True, json_body=True),
    "/api/rules/suggest": Route(api_writes.post_rules_suggest, config=True, json_body=True),
    "/api/rules/batch": Route(api_writes.post_rules_batch, "rules", config=True, json_body=True),
    "/api/rules/pin": Route(api_writes.post_rules_pin, "rules", config=True, json_body=True),
    "/api/budgets": Route(api_writes.post_budget, "planning", config=True, json_body=True),
    "/api/budgets/copy": Route(api_writes.post_budgets_copy, "planning", config=True, json_body=True),
    "/api/plan": Route(api_writes.post_plan, "planning", config=True, json_body=True),
    "/api/categories": Route(api_writes.post_need_class, "planning", config=True, json_body=True),
    "/api/trips": Route(api_writes.post_trip, "planning", config=True, json_body=True),
    "/api/trips/remove": Route(api_writes.post_trip_remove, "planning", config=True, json_body=True),
    "/api/trips/restore": Route(api_writes.post_trip_restore, "planning", config=True, json_body=True),
    "/api/trips/undo": Route(api_writes.post_trip_undo, "planning", config=True, json_body=True),
    "/api/categories/add": Route(api_writes.post_category_add, "categories", config=True, json_body=True),
    "/api/categories/rename": Route(api_writes.post_category_rename, "categories", config=True, json_body=True),
    "/api/categories/move": Route(api_writes.post_category_move, "categories", config=True, json_body=True),
    "/api/categories/delete": Route(api_writes.post_category_delete, "categories", config=True, json_body=True),
    "/api/category-groups/add": Route(api_writes.post_group_add, "categories", config=True, json_body=True),
    "/api/category-groups/rename": Route(api_writes.post_group_rename, "categories", config=True, json_body=True),
    "/api/category-groups/reorder": Route(api_writes.post_group_reorder, "categories", config=True, json_body=True),
    "/api/category-groups/delete": Route(api_writes.post_group_delete, "categories", config=True, json_body=True),
    "/api/category-groups/style": Route(api_writes.post_group_style, "categories", json_body=True),
    "/api/growth/preview": Route(growth_api.post_growth_preview, "growth", json_body=True),
    "/api/growth/inputs": Route(growth_api.post_growth_inputs, "growth", json_body=True),
    "/api/profile": Route(api_writes.post_profile, "profile", json_body=True),
    "/api/setup": Route(api_writes.post_setup, "setup", json_body=True),
    "/api/scam-checks/settings": Route(api_writes.post_scam_settings, "scam-checks", json_body=True),
    "/api/scam-checks/fine": Route(api_writes.post_scam_fine, "scam-checks", json_body=True),
    "/api/app-icon": Route(api_writes.post_app_icon, json_body=True),
    "/api/recurring/series": Route(api_writes.post_series, "recurring", json_body=True),
    "/api/recurring/payee": Route(api_writes.post_payee, "recurring", json_body=True),
    "/api/export": Route(api_writes.post_export, config=True),
    "/api/ai/settings": Route(api_writes.post_ai_settings, "ai", json_body=True),
    "/api/ai/key": Route(api_writes.post_ai_key, "ai", json_body=True),
    "/api/ai/key/remove": Route(api_writes.post_ai_key_remove, "ai", json_body=True),
    "/api/ai/test": Route(api_writes.post_ai_test, "ai", json_body=True),
    "/api/copilot/ask": Route(api_writes.post_copilot_ask, "ai", json_body=True),
    "/api/copilot/threads/remove": Route(api_writes.post_copilot_threads_remove, "ai", json_body=True),
    "/api/link/pair-token": Route(api_writes.post_pair_token),
    "/api/pair/redeem": Route(api_writes.post_pair_redeem, json_body=True),
    "/api/link/devices/revoke": Route(api_writes.post_devices_revoke, json_body=True),
    "/api/link/transactions": Route(api_writes.post_link_transactions, json_body=True),
    "/api/link/pending/dismiss": Route(api_writes.post_pending_dismiss, json_body=True),
    "/api/link/pending/restore": Route(api_writes.post_pending_restore, json_body=True),
    "/api/server/stop": Route(_post_server_stop),
    "/api/server/restart": Route(_post_server_restart),
    "/api/dashboard/layouts": Route(api_writes.post_dashboard_layouts, "dashboard", json_body=True),
    "/api/dashboard/layouts/<id>": Route(api_writes.post_dashboard_layout, "dashboard", json_body=True),
    "/api/dashboard/query": Route(api_writes.post_dashboard_query, "dashboard", json_body=True),
    "/api/dashboard/goals": Route(api_writes.post_dashboard_goal, "dashboard", json_body=True),
    "/api/dashboard/goals/remove": Route(api_writes.post_dashboard_goal_remove, "dashboard", json_body=True),
    "/api/dashboard/scenario/preview": Route(api_writes.post_dashboard_scenario_preview, "dashboard", json_body=True),
    "/api/dashboard/allocation": Route(api_writes.post_dashboard_allocation, "dashboard", json_body=True),
    "/api/dashboard/game-pot": Route(api_writes.post_dashboard_game_pot, "dashboard", json_body=True),
}


def _tagged(tag: str) -> tuple[str, ...]:
    return tuple(path for path, route in POST_ROUTES.items() if route.tag == tag)


PLANNING_WRITES = _tagged("planning")
RULE_WRITES = _tagged("rules")
CATEGORY_WRITES = _tagged("categories")


class _Router:
    """A table's paths: a plain one is found by name, a pattern by matching
    each <name> against one path segment."""

    def __init__(self, routes: dict[str, Route]):
        self.exact = {p: r for p, r in routes.items() if "<" not in p}
        self.patterns = []
        for pattern, route in routes.items():
            if "<" in pattern:
                parts = re.split(r"<(\w+)>", pattern)
                regex = "".join(f"(?P<{part}>[^/]+)" if i % 2 else re.escape(part) for i, part in enumerate(parts))
                self.patterns.append((re.compile(regex), route))

    def find(self, path: str) -> tuple[Route, dict] | None:
        if path in self.exact:
            return self.exact[path], {}
        for regex, route in self.patterns:
            match = regex.fullmatch(path)
            if match:
                return route, match.groupdict()
        return None


_ROUTERS = {"GET": _Router(GET_ROUTES), "POST": _Router(POST_ROUTES)}


def resolve(method: str, path: str) -> tuple[Route, dict] | None:
    """The route for a method and path with the <names> it matched, or None."""
    router = _ROUTERS.get(method)
    return router.find(path) if router else None


def answer(path: str, query: dict) -> object:
    """The JSON for one API path (a Download for a file), or NotFound."""
    return _dispatch("GET", path, query)


def write(path: str, query: dict, body: bytes, device: dict | None = None) -> object:
    """Carry out one write, or raise Refused/NotFound with the reason.
    `device` is the paired phone the link gate resolved for a bearer write."""
    return _dispatch("POST", path, query, body, device=device)


def make_server(port: int = PORT, dist: Path = DIST, page: str | None = None, mode: str = "serve") -> ThreadingHTTPServer:
    global _SERVER, _PORT, _MODE
    _SERVER = ThreadingHTTPServer((HOST, port), make_handler(dist, page))
    _PORT = _SERVER.server_address[1]
    _MODE = mode
    return _SERVER


def stop_when_stdin_closes(server: ThreadingHTTPServer) -> None:
    """G23: the shell holds the sidecar's standard input open while it runs.
    Its end (the shell quit or died) shuts the server down."""
    def watch() -> None:
        while sys.stdin.buffer.read(4096):
            pass
        server.shutdown()
    threading.Thread(target=watch, daemon=True).start()


def picked_icon() -> str:
    """The app icon set to announce as the desktop app starts; a record that
    is missing or will not open has no pick, so the default."""
    try:
        with closing(api.open_record()) as conn:
            return app_icon.chosen(conn)
    except (NotFound, sqlite3.Error):
        return app_icon.chosen(None)


def announce_icon(name: str) -> None:
    print(f"{ICON} {name}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int,
                        help=f"default {PORT}, or {APP_PORT} with --app")
    parser.add_argument("--reload", action="store_true",
                        help="restart the server whenever a Python file changes")
    parser.add_argument("--page", metavar="URL",
                        help="send page requests to this URL (the Vite dev server) instead of serving apps/web/dist")
    parser.add_argument("--app", action="store_true",
                        help="run as the desktop app's sidecar: print a ready line, stop when stdin closes")
    args = parser.parse_args()
    port = args.port if args.port is not None else (APP_PORT if args.app else PORT)
    if args.reload:
        dev_reload.reload_forever(port, page=args.page)
        return
    try:
        server = make_server(port, page=args.page, mode="app" if args.app else "dev" if args.page else "serve")
    except OSError as exc:
        print(f"Kopifund could not listen on {HOST}:{port}: {exc.strerror or exc}. "
              "Another program is using that port.", file=sys.stderr, flush=True)
        sys.exit(1)
    address = f"http://{HOST}:{server.server_address[1]}"
    if args.app:
        stop_when_stdin_closes(server)
        print(f"{READY} {address}", flush=True)
        app_icon.announce_with(announce_icon)
        announce_icon(picked_icon())
    else:
        print(f"Kopifund on {address}  (Ctrl-C to stop)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    if _RESTART:
        # the same command line in place of this process: fds 0-2 survive exec
        # (the app's shell still holds stdin, and the new process's ready line
        # reloads its window), while the listening socket is non-inheritable,
        # so the new process binds the port afresh
        os.execv(sys.executable, [sys.executable, str(Path(sys.argv[0]).resolve()), *sys.argv[1:]])


if __name__ == "__main__":
    main()

