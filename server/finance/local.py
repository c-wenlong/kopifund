"""The local record's side of a run: the one-time Notion import, and the
SQLite write.

`read_back` imports the fields the owner used to type in Notion (categories,
budget planned amounts, trips, accounts, cards) into kopifund.db, once, through
`finance.py import-notion`. From G8 the app owns them, so a sync no longer reads
them back: it writes kopifund.db (`load`) and then mirrors the loader-owned
tables to Notion (server/finance/sync.py). `finance.py load` is the whole monthly run
for a user without Notion.
"""
import sys

from . import snapshot as snap

from . import db, notion, pending, sync
from .model import all_pdfs, pdf_parsers

# the user-owned data sources the one-time import reads
USER_SOURCES = ("categories", "accounts", "cards", "budgets", "trips")


def notion_enabled(cfg):
    """Notion is on when every data source the mirror writes is configured."""
    return all(cfg.data_sources.get(name) for name in snap.DATABASES)


def read_back(cfg, client, conn, plan=None):
    """Step 1: the Notion-owned fields into SQLite. Returns the counts."""
    pages = {name: [] for name in snap.DATABASES}
    for name, got in zip(USER_SOURCES, client.map(lambda n: client.query_all(cfg.ds(n)),
                                                   USER_SOURCES)):
        pages[name] = got
    planner = cfg.data_sources.get("trip_plans")
    spans = sync.plan_spans(pages["trips"], client.query_all(planner)) if planner else {}
    flat = snap.build_from_pages(cfg, pages)
    notes = {notion.plain(p, "Name") or "": notion.plain(p, "Notes") or "" for p in pages["trips"]}
    for t in flat.get("trips", []):
        t["notes"] = notes.get(t["name"], "")
    return import_snapshot(conn, flat, sync.trips_from_pages(pages["trips"], spans=spans), plan)


def import_snapshot(conn, flat, trips=(), plan=None):
    """User tables from a flattened snapshot, with trip dates, ISO countries
    and pinned keys taken from the Trip objects the sync uses."""
    by_name = {t.name: t for t in trips}
    for t in flat.get("trips", []):
        full = by_name.get(t["name"])
        if full:
            t["start"], t["end"] = full.start, full.end
            t["countries"] = list(full.countries)
            t["keys"] = sorted(full.keys)
        else:
            t["countries"] = list(sync.country_codes(t.get("countries")))
    return db.import_user_tables(conn, flat, plan=plan)


def config_plan():
    """The [plan] table of the data folder's config.toml, or None: what the
    one-time import copies into the record. From G8 the app owns the plan."""
    import tomllib
    from .paths import config_path
    try:
        return tomllib.loads(config_path().read_text(encoding="utf-8")).get("plan")
    except (OSError, tomllib.TOMLDecodeError):
        return None


def trips_from_db(conn):
    """The trips as the sync's Trip objects, so assignment is the same rule.
    G17 (T9): a pinned key the parser has since changed (txn_key_alias) is
    the row's key now, so the row stays on its trip."""
    out = []
    codes, pins = db.trip_countries(conn), db.trip_pins(conn)
    for r in conn.execute("SELECT * FROM trip WHERE start_date IS NOT NULL"):
        out.append(sync.Trip(page_id=str(r["id"]), name=r["name"], start=r["start_date"],
                             end=r["end_date"] or r["start_date"],
                             countries=tuple(codes.get(r["id"], ())),
                             keys=frozenset(pins.get(r["id"], ()))))
    return sorted(out, key=lambda t: (t.start, t.end, t.name))


def wallet_numbers(cfg, rows):
    """The account numbers whose rows are a currency wallet's: the config's,
    and, since a wallet folder set up without an account number still marks
    its rows as wallet rows, every number a row from a wallet folder prints."""
    wallet_dirs = [str(cfg.root / f.path) for f in cfg.folders
                   if f.wallet or f.multi_currency or f.per_currency_subfolders]
    return cfg.wallet_accounts() - {""} | {
        r["account_no"] for r in rows
        if r.get("account_no") and any(str(r.get("source", "")).startswith(d) or
                                       str(cfg.root / r.get("source", "")).startswith(d)
                                       for d in wallet_dirs)}


def trip_names(cfg, rows, trips):
    """{txn_key: trip name} for every row a trip claims (sync.trip_for). A
    load and a relink after a trip is saved (server/finance/relink.py) both ask
    this, with every row, so a row lands on the same trip either way."""
    wallets = wallet_numbers(cfg, rows)
    out = {}
    for r in rows:
        t = sync.trip_for(r, trips, wallets)
        if t:
            out[r["txn_key"]] = t.name
    return out


def load(cfg, conn, parsed, kind="load"):
    """Step 2: one parser run into the loader-owned tables. `parsed` has been
    through sync.categorise. Returns the load counts."""
    # a category no longer shipped that the rows still name (Transport, before
    # split-transport) is added back, so its rows keep counting
    db.seed_categories(conn, named={r.get("category") for r in parsed.rows})
    trip_of = trip_names(cfg, parsed.rows, trips_from_db(conn))
    rows = [{"payee": r.get("payee") or "", "category": r.get("category") or "",
             "amount_sgd_minor": None} for r in parsed.rows]
    payees = snap.payees_from_rules(parsed.rules, rows) if getattr(parsed, "rules", None) else []
    complete = not parsed.failed_roots and not any(
        p.code in ("parser-failed", "duplicate-period") for p in parsed.problems)
    pdfs = all_pdfs(cfg)
    # read everything first, then write it all or nothing: a brokerage or CPF
    # file that fails must not leave new bank rows beside old investments
    portfolio, cpf = snap.read_portfolio(cfg), snap.read_cpf(cfg)
    for r in (cpf or {}).get("rejected", ()):
        # parse_folder has already left it out; say so rather than drop it quietly
        print(f"  warning: CPF {r['year']} ({r['file']}) does not reconcile "
              f"(off by {r['off']}); not loaded", file=sys.stderr)
    with db.transaction(conn):
        counts = db.load_parsed(conn, parsed, payees=payees, trips_by_key=trip_of,
                                complete=complete, kind=kind)
        db.load_statements_meta(conn, cfg.root, pdfs, pdf_parsers(cfg))
        db.load_broker_and_cpf(conn, portfolio, cpf)
        # G25: the phone's waiting rows confirm against what this load landed
        counts["phone rows matched"] = pending.confirm(conn)
    counts["trips assigned"] = len(trip_of)
    counts["complete"] = complete
    return counts
