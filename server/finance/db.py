"""kopifund.db: the record (SPEC.md, "The record").

One SQLite file in the data folder. Python's sqlite3 drives it, so the backend
stays stdlib-only. Three rules shape every table:

* Money is `*_minor INTEGER` plus a currency code; FX rates are integers scaled
  by 10^9. `to_minor` refuses a float that is not a whole number of cents, so a
  parser bug cannot round itself into the record.
* Links are ids. A transaction points at its period, account, card, payee,
  category and trip by id, so renaming any of them changes one row.
* Stored versus derived. Tables hold what a statement printed or a person
  typed; what Notion computed (amount in SGD, month, reconciled, budget actual)
  is a view.

Tables are loader-owned or user-owned. The loader (`load_parsed`) writes
statement_file, period, txn and payee, and `load_broker_and_cpf` writes
broker_statement, cpf_statement and cpf_rejected; it never writes account, card,
category, budget, trip or plan, which the user owns: the app edits those
(G8), and the one-time Notion import (`import_user_tables`) seeds them.

G17 (schema version 5): the record keeps itself straight where it can. A
delete follows its links (ON DELETE SET NULL or CASCADE), a column whose
values Python lists has a CHECK built from that list, category.bucket is
kept by triggers, and docs/gen_schema.py writes docs/schema.md's tables from
this schema.
"""
import json
import os
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import date as date_type
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from .money import from_cents, rate_e9  # noqa: F401  G20: the one FX scaling, shared with the stats
from .paths import data_root

# 2: upload_attempt and upload_step, the upload history
# 3: category_group (the buckets on the Categories page), category.group_id,
#    category_alias
# 4: setting and txn_marked_fine (the scam checks)
# 5: ON DELETE on the links, a CHECK on every column whose values Python
#    lists, trip_country and trip_pin in place of trip.countries and
#    trip.extra_keys, period.statement_file_id, category.bucket kept by
#    triggers, a bucket's icon and colour, txn.currency and period.currency
#    linked to currency, and no mirror_map (G17)
# 6: series_override and series_override_txn, the owner's edits to a
#    recurring series (G16)
# 7: pending_txn, the phone's rows waiting for a statement to cover them (G25)
SCHEMA_VERSION = 7
SYNTHETIC_TYPE = "YouTrip Unprinted"

# The schema, with each enum's list of values ({need_class}, ...) filled in by
# `schema()` from the Python tuple the code checks against (`enums`), so the
# two cannot drift. Change it only together with SCHEMA_VERSION: a record at
# the version never runs it again. Braces that are not a list are doubled.
SCHEMA = """
CREATE TABLE IF NOT EXISTS currency (
  code TEXT PRIMARY KEY,
  exponent INTEGER NOT NULL DEFAULT 2 CHECK (exponent BETWEEN 0 AND 4)
) STRICT;

-- user-owned ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS account (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  account_no TEXT UNIQUE,
  institution TEXT,
  class TEXT,
  subclass TEXT,
  base_currency TEXT
) STRICT;

CREATE TABLE IF NOT EXISTS card (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  last4 TEXT NOT NULL UNIQUE,
  network TEXT,
  type TEXT,
  state TEXT
) STRICT;

-- a bucket of categories on the Categories page (Food, Home and bills,
-- Income, Transfers). `kind` says what its categories are: spend, income or
-- transfer, and category.bucket (Spend, Income, Transfer, which the stats
-- read) follows it. Named category_group because category.bucket and
-- category.grp already mean something else. `icon` is a lucide icon name and
-- `colour` a chart series token (c1 to c12), NULL for the default by order;
-- server/finance/categories.py holds both lists and is their one writer.
CREATE TABLE IF NOT EXISTS category_group (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  kind TEXT NOT NULL CHECK (kind IN ({group_kind})),
  sort_order REAL,
  icon TEXT,
  colour TEXT
) STRICT;

-- `bucket` is derived: the triggers below keep it the bucket of its group's
-- kind (BUCKET_OF_KIND), and nothing else writes it
CREATE TABLE IF NOT EXISTS category (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  bucket TEXT CHECK (bucket IN ({bucket})),
  grp TEXT CHECK (grp IN ({cost_type})),
  need_class TEXT CHECK (need_class IN ({need_class})),
  sort_order REAL,
  group_id INTEGER REFERENCES category_group(id)   -- an older record gains it in _migrations
) STRICT;

-- a name a rule or the shipped list may still use for a category the user
-- renamed, or deleted into another: rows whose rule says the old name land on
-- the category it points at, and the shipped list never brings the name back
CREATE TABLE IF NOT EXISTS category_alias (
  name TEXT PRIMARY KEY,
  category_id INTEGER NOT NULL REFERENCES category(id),
  -- rename: the category lives on under its new name, and a stat that looks
  -- for the old one follows it; delete: the name is gone, only rules follow
  how TEXT NOT NULL DEFAULT 'rename' CHECK (how IN ('rename', 'delete'))
) STRICT;

CREATE TABLE IF NOT EXISTS budget (
  category_id INTEGER NOT NULL REFERENCES category(id) ON DELETE CASCADE,
  month TEXT NOT NULL CHECK (month GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]'),
  planned_minor INTEGER,
  PRIMARY KEY (category_id, month)
) STRICT;

CREATE TABLE IF NOT EXISTS trip (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  start_date TEXT,
  end_date TEXT,
  notes TEXT NOT NULL DEFAULT '',
  CHECK (end_date IS NULL OR start_date IS NULL OR end_date >= start_date)
) STRICT;

-- the countries a trip names, ISO alpha-3, in the order the user gave them
CREATE TABLE IF NOT EXISTS trip_country (
  trip_id INTEGER NOT NULL REFERENCES trip(id) ON DELETE CASCADE,
  code TEXT NOT NULL CHECK (code GLOB '[A-Z][A-Z][A-Z]'),
  position INTEGER NOT NULL,
  PRIMARY KEY (trip_id, code)
) STRICT;

-- a row pinned to a trip whatever its date (a flight booked weeks ahead), by
-- its txn_key: a key the parser has since changed is followed through
-- txn_key_alias (trip_pins), and a key not loaded yet waits for its row
CREATE TABLE IF NOT EXISTS trip_pin (
  trip_id INTEGER NOT NULL REFERENCES trip(id) ON DELETE CASCADE,
  txn_key TEXT NOT NULL,
  PRIMARY KEY (trip_id, txn_key)
) STRICT;

-- the forward view's plan: one JSON value per key, the [plan] shape of the old
-- config with every money field in integer cents (plan_to_db / load_plan)
CREATE TABLE IF NOT EXISTS plan (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
) STRICT;

-- G15: the scam checks' settings, and the rows the user marked fine. Keyed
-- by txn_key, which survives a reload (txn_key_alias follows a re-key).
CREATE TABLE IF NOT EXISTS setting (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS txn_marked_fine (
  txn_key TEXT PRIMARY KEY,
  marked_at TEXT NOT NULL
) STRICT;

-- G16: the owner's edits to a recurring series (server/finance/series.py). `series_id`
-- is the detector's id for the series when the edit was saved, NULL for a
-- payee's own line (Not recurring on a payee the rules mark recurring);
-- group_kind and group_name are the detector's group. Each edit may be NULL
-- (as detected), and a row with none is not kept. `day` is the day of the
-- month, or for weekly and fortnightly the weekday, 1 Monday to 7 Sunday.
CREATE TABLE IF NOT EXISTS series_override (
  id INTEGER PRIMARY KEY,
  series_id TEXT UNIQUE,
  group_kind TEXT NOT NULL CHECK (group_kind IN ({series_group})),
  group_name TEXT NOT NULL,
  recurring INTEGER CHECK (recurring IN (0, 1)),
  amount_minor INTEGER CHECK (amount_minor > 0),
  cadence TEXT CHECK (cadence IN ({cadence})),
  day INTEGER CHECK (day BETWEEN 1 AND 31),
  ended_on TEXT CHECK (date(ended_on) = ended_on),
  saved_at TEXT NOT NULL
) STRICT;
CREATE UNIQUE INDEX IF NOT EXISTS series_override_payee_idx ON series_override(group_kind, group_name)
  WHERE series_id IS NULL;

-- the series' own payments when its edit was saved, by txn_key (a key the
-- parser has since changed is followed through txn_key_alias), so the edit
-- finds its series again when the detector's id for it changes
CREATE TABLE IF NOT EXISTS series_override_txn (
  override_id INTEGER NOT NULL REFERENCES series_override(id) ON DELETE CASCADE,
  txn_key TEXT NOT NULL,
  PRIMARY KEY (override_id, txn_key)
) STRICT;

-- G25: a spend the phone saw and told the record about before its statement
-- landed (server/finance/pending.py, SPEC.md "Phone link (G25)"). `import_id`
-- is the phone's own key, so a retry upserts and never doubles. The row waits
-- until a load's row matches it on amount, currency and date with a payee
-- that reads alike; `rejected` holds the txn_keys a match the owner put back
-- must never pair with again. It counts nowhere: the real row does the
-- counting once it lands. `device_id` is the paired phone's id in the
-- registry (devices.json, a file, so no link here); `currency` is plain text
-- for the same reason a code no statement has used yet must still load.
CREATE TABLE IF NOT EXISTS pending_txn (
  id INTEGER PRIMARY KEY,
  import_id TEXT NOT NULL UNIQUE,
  device_id TEXT NOT NULL DEFAULT '',
  txn_date TEXT NOT NULL CHECK (date(txn_date) = txn_date),
  amount_minor INTEGER NOT NULL,             -- signed the ledger's way (spending negative)
  currency TEXT NOT NULL CHECK (currency GLOB '[A-Z][A-Z][A-Z]'),
  payee TEXT NOT NULL DEFAULT '',
  state TEXT NOT NULL DEFAULT 'waiting' CHECK (state IN ('waiting', 'matched', 'dismissed')),
  matched_txn_key TEXT,
  rejected TEXT NOT NULL DEFAULT '[]',       -- txn_keys, as JSON
  created_at TEXT NOT NULL
) STRICT;
CREATE INDEX IF NOT EXISTS pending_txn_state_idx ON pending_txn(state);

-- loader-owned ----------------------------------------------------------
-- `parser` is the parser of the folder it sits in; `original_name` the name
-- it was uploaded under (the upload history), else its name when first seen
CREATE TABLE IF NOT EXISTS statement_file (
  id INTEGER PRIMARY KEY,
  path TEXT NOT NULL UNIQUE,                -- relative to the data folder
  sha256 TEXT NOT NULL UNIQUE,
  parser TEXT NOT NULL DEFAULT '',
  original_name TEXT NOT NULL DEFAULT '',
  first_seen TEXT NOT NULL,
  superseded INTEGER NOT NULL DEFAULT 0
) STRICT;

CREATE TABLE IF NOT EXISTS payee (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  kind TEXT CHECK (kind IN ({payee_kind})),
  country TEXT,
  recurring INTEGER NOT NULL DEFAULT 0,
  account_no TEXT NOT NULL DEFAULT ''
) STRICT;

-- `source` is the file the block came from, "#<part>" added for one of
-- several blocks in it; statement_file_id is that file's row, set on every
-- load (link_statement_files), NULL while no statement PDF matches it
CREATE TABLE IF NOT EXISTS period (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,                -- "<account_no> · <ccy> · <yyyy-mm>[ · card]"
  account_id INTEGER REFERENCES account(id),
  account_no TEXT NOT NULL,
  currency TEXT NOT NULL REFERENCES currency(code),
  period_end TEXT,
  opening_minor INTEGER,
  closing_minor INTEGER,
  total_in_minor INTEGER,
  total_out_minor INTEGER,
  fx_to_sgd_e9 INTEGER,
  parser_reconciled INTEGER,
  n_txns INTEGER,
  note TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL DEFAULT '',
  archived_at TEXT,
  statement_file_id INTEGER REFERENCES statement_file(id) ON DELETE SET NULL
) STRICT;

CREATE TABLE IF NOT EXISTS txn (
  id INTEGER PRIMARY KEY,
  txn_key TEXT NOT NULL UNIQUE,
  period_id INTEGER REFERENCES period(id),
  account_id INTEGER REFERENCES account(id),
  account_no TEXT NOT NULL,
  currency TEXT NOT NULL REFERENCES currency(code),
  txn_date TEXT NOT NULL CHECK (date(txn_date) = txn_date),
  purchase_date TEXT,
  amount_minor INTEGER NOT NULL,
  balance_after_minor INTEGER,
  direction TEXT NOT NULL DEFAULT '',
  bank_txn_type TEXT NOT NULL DEFAULT '',
  flow_class TEXT NOT NULL DEFAULT '',
  merchant_raw TEXT NOT NULL DEFAULT '',
  merchant_country TEXT NOT NULL DEFAULT '',
  card_id INTEGER REFERENCES card(id),
  card_last4 TEXT NOT NULL DEFAULT '',
  fx_original TEXT NOT NULL DEFAULT '',
  reference TEXT NOT NULL DEFAULT '',
  counterparty TEXT NOT NULL DEFAULT '',
  memo TEXT NOT NULL DEFAULT '',
  is_synthetic INTEGER NOT NULL DEFAULT 0,
  payee_id INTEGER REFERENCES payee(id),
  category_id INTEGER REFERENCES category(id) ON DELETE SET NULL,
  rule TEXT NOT NULL DEFAULT '',
  trip_id INTEGER REFERENCES trip(id) ON DELETE SET NULL,
  source TEXT NOT NULL DEFAULT '',
  first_seen TEXT NOT NULL,
  last_seen TEXT NOT NULL,
  archived_at TEXT
) STRICT;
CREATE INDEX IF NOT EXISTS txn_date_idx ON txn(txn_date);
CREATE INDEX IF NOT EXISTS txn_category_idx ON txn(category_id);
CREATE INDEX IF NOT EXISTS txn_trip_idx ON txn(trip_id);

-- a key the parser used to produce for a row it now keys differently
CREATE TABLE IF NOT EXISTS txn_key_alias (
  old_key TEXT PRIMARY KEY,
  txn_id INTEGER NOT NULL REFERENCES txn(id)
) STRICT;

CREATE TABLE IF NOT EXISTS broker_statement (
  id INTEGER PRIMARY KEY,
  file TEXT NOT NULL UNIQUE,
  period_from TEXT,
  period_to TEXT,
  base_currency TEXT,
  data TEXT NOT NULL                        -- the parser's dict, as JSON
) STRICT;

CREATE TABLE IF NOT EXISTS cpf_statement (
  year INTEGER PRIMARY KEY,
  file TEXT NOT NULL,
  as_of TEXT,
  data TEXT NOT NULL                        -- the parser's dict, as JSON
) STRICT;

-- a CPF year the last load read off disk and left out because its opening plus
-- transactions is not its closing; replaced whole on every load, and while a
-- year is here its cpf_statement row (an older read) is not shown as valid
CREATE TABLE IF NOT EXISTS cpf_rejected (
  year INTEGER NOT NULL,
  file TEXT NOT NULL,
  off_oa_minor INTEGER NOT NULL,            -- reconcile()'s gap per account
  off_sa_minor INTEGER NOT NULL,
  off_ma_minor INTEGER NOT NULL,
  PRIMARY KEY (year, file)
) STRICT;

CREATE TABLE IF NOT EXISTS sync_run (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  status TEXT,
  inserted INTEGER NOT NULL DEFAULT 0,
  updated INTEGER NOT NULL DEFAULT 0,
  archived INTEGER NOT NULL DEFAULT 0,
  note TEXT NOT NULL DEFAULT ''
) STRICT;

-- the upload history: one attempt per visit to the Upload page (or one load
-- started elsewhere), one step per upload, check, load or mirror it ran, with
-- what went in and what came out, so an old error can be found again
CREATE TABLE IF NOT EXISTS upload_attempt (
  id INTEGER PRIMARY KEY,
  origin TEXT NOT NULL DEFAULT 'upload' CHECK (origin IN ({origin})),   -- the page that started it
  started_at TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS upload_step (
  id INTEGER PRIMARY KEY,
  attempt_id INTEGER NOT NULL REFERENCES upload_attempt(id),
  step TEXT NOT NULL CHECK (step IN ({step})),
  started_at TEXT NOT NULL,
  finished_at TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ({step_status})),
  summary TEXT NOT NULL DEFAULT '',
  input TEXT NOT NULL DEFAULT '{{}}',       -- JSON
  output TEXT NOT NULL DEFAULT '{{}}'       -- JSON
) STRICT;
CREATE INDEX IF NOT EXISTS upload_step_attempt_idx ON upload_step(attempt_id, id);

-- derived ---------------------------------------------------------------
-- category.bucket follows its group's kind: on a new category, on a move, on
-- a write to bucket itself (put back at once), and when a group's kind changes
CREATE TRIGGER IF NOT EXISTS category_bucket_insert AFTER INSERT ON category
WHEN NEW.group_id IS NOT NULL
BEGIN
  UPDATE category SET bucket = ({bucket_of_new}) WHERE id = NEW.id AND bucket IS NOT ({bucket_of_new});
END;
CREATE TRIGGER IF NOT EXISTS category_bucket_update AFTER UPDATE OF group_id, bucket ON category
WHEN NEW.group_id IS NOT NULL
BEGIN
  UPDATE category SET bucket = ({bucket_of_new}) WHERE id = NEW.id AND bucket IS NOT ({bucket_of_new});
END;
CREATE TRIGGER IF NOT EXISTS category_group_kind AFTER UPDATE OF kind ON category_group
BEGIN
  UPDATE category SET bucket = ({bucket_of_kind}) WHERE group_id = NEW.id;
END;

DROP VIEW IF EXISTS v_txn;
CREATE VIEW v_txn AS
SELECT t.*,
       substr(t.txn_date, 1, 7) AS month,
       CASE WHEN t.currency = 'SGD' THEN t.amount_minor
            WHEN p.fx_to_sgd_e9 IS NULL THEN NULL
            ELSE CAST(round(t.amount_minor * (p.fx_to_sgd_e9 / 1e9)) AS INTEGER)
       END AS amount_sgd_minor,
       p.name AS period_name,
       a.name AS account_name,
       py.name AS payee_name,
       c.name AS category_name, c.bucket AS bucket, c.grp AS grp, c.need_class AS need_class,
       g.name AS category_group, g.kind AS category_group_kind,
       tr.name AS trip_name
FROM txn t
LEFT JOIN period p ON p.id = t.period_id
LEFT JOIN account a ON a.id = t.account_id
LEFT JOIN payee py ON py.id = t.payee_id
LEFT JOIN category c ON c.id = t.category_id
LEFT JOIN category_group g ON g.id = c.group_id
LEFT JOIN trip tr ON tr.id = t.trip_id
WHERE t.archived_at IS NULL;

DROP VIEW IF EXISTS v_period;
CREATE VIEW v_period AS
SELECT p.*,
       a.name AS account_name,
       CASE WHEN p.closing_minor IS NULL THEN NULL
            WHEN p.currency = 'SGD' THEN p.closing_minor
            WHEN p.fx_to_sgd_e9 IS NULL THEN NULL
            ELSE CAST(round(p.closing_minor * (p.fx_to_sgd_e9 / 1e9)) AS INTEGER)
       END AS closing_sgd_minor,
       (p.opening_minor IS NOT NULL AND p.closing_minor IS NOT NULL) AS has_balances,
       -- the statement's own control totals, to the cent; a blank counts as 0,
       -- so a block that prints no balances reads false and has_balances says why
       (COALESCE(p.opening_minor, 0) + COALESCE(p.total_in_minor, 0)
        - COALESCE(p.total_out_minor, 0) - COALESCE(p.closing_minor, 0)) = 0 AS reconciled
FROM period p
LEFT JOIN account a ON a.id = p.account_id
WHERE p.archived_at IS NULL;

DROP VIEW IF EXISTS v_budget;
CREATE VIEW v_budget AS
-- Actual: External rows only, signed so that Planned and Actual compare like
-- for like (spending positive when money went out, income positive when it
-- came in). A row with no SGD value is left out, as everywhere.
SELECT b.category_id, c.name AS category, b.month, b.planned_minor,
       (SELECT CASE WHEN c.need_class = 'Income' THEN 1 ELSE -1 END
               * COALESCE(SUM(v.amount_sgd_minor), 0) FROM v_txn v
        WHERE v.category_id = b.category_id AND v.month = b.month
          AND v.flow_class = 'External') AS actual_minor
FROM budget b JOIN category c ON c.id = b.category_id;

DROP VIEW IF EXISTS v_missing_fx;
CREATE VIEW v_missing_fx AS
SELECT * FROM v_txn WHERE amount_sgd_minor IS NULL;
"""

# category.bucket (what the stats read) for each kind of category_group
BUCKET_OF_KIND = {"spend": "Spend", "income": "Income", "transfer": "Transfer"}
KIND_OF_BUCKET = {v: k for k, v in BUCKET_OF_KIND.items()}


def enums():
    """{column: its allowed values}, each the tuple the code that writes the
    column checks against. G17: the schema's CHECKs are built from these.
    Imported here, not at the top, since those modules import this one.

    A value added to one of these tuples is refused by a record built
    before it: that change needs a SCHEMA_VERSION of its own, whose upgrade
    rebuilds the table (server/tests/test_schema_v5.py holds the lists that
    version 5 was built with)."""
    from . import categories, planning, recurring, series, upload_log
    from .rules_app import categorise
    return {"group_kind": categories.KINDS, "bucket": tuple(BUCKET_OF_KIND.values()),
            "cost_type": categories.COST_TYPES, "need_class": planning.NEED_CLASSES,
            "payee_kind": categorise.KINDS, "origin": upload_log.ORIGINS, "step": upload_log.STEPS,
            "step_status": upload_log.STATUSES, "series_group": series.GROUP_KINDS,
            "cadence": recurring.CADENCES}


def _sql_list(values):
    return ", ".join("'" + str(v).replace("'", "''") + "'" for v in values)


def _bucket_case(kind_sql):
    """SQL for the bucket of the kind `kind_sql` names, from BUCKET_OF_KIND."""
    whens = " ".join(f"WHEN {_sql_list([k])} THEN {_sql_list([b])}" for k, b in BUCKET_OF_KIND.items())
    return f"CASE {kind_sql} {whens} END"


def schema():
    """SCHEMA with every enum's list filled in: the script a new record runs,
    and the tables an upgrade rebuilds to."""
    lists = {k: _sql_list(v) for k, v in enums().items()}
    of_new = f"SELECT {_bucket_case('g.kind')} FROM category_group g WHERE g.id = NEW.group_id"
    return SCHEMA.format(**lists, bucket_of_new=of_new, bucket_of_kind=_bucket_case("NEW.kind"))


# ---------------------------------------------------------------- values

def to_minor(x, exponent=2):
    """A parsed amount as integer minor units, or None. A value that is not a
    whole number of minor units is a parser bug, not something to round away."""
    if x is None or x == "":
        return None
    v = float(x) * 10 ** exponent
    r = round(v)
    if abs(v - r) > 1e-6:
        raise ValueError(f"{x!r} is not a whole number of minor units")
    return int(r)


def from_minor(m, exponent=2):
    """Integer minor units back to dollars as the CPF parser writes them, or
    None: a rejected year's gaps, which the cpf stat reads as the parser's."""
    return None if m is None else round(m / 10 ** exponent, exponent)


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def now_fine():
    """G18: now to the microsecond, for sync_run, so a load and an upload step in the
    same second keep their order (server/finance/home.py, upload_state)."""
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


# ---------------------------------------------------------------- connection

def db_path():
    """The record. KOPIFUND_DB points somewhere else, which the test suite does
    so that no test ever writes the real record."""
    override = os.environ.get("KOPIFUND_DB")
    return Path(override).expanduser() if override else data_root() / "kopifund.db"


class RecordTooNew(RuntimeError):
    """The record was written by a newer Kopifund than this one."""


# how long a write waits for another connection's write (a load, a rule save
# in another tab or process) before it gives up with "database is locked"
BUSY_SECONDS = 30


def connect(path=None):
    """Open (and if new, create) the record. `path=":memory:"` for tests.

    The record's `user_version` is its schema version. A new or older record
    gets the schema and this version; one at this version is not written; a
    newer one is refused, so an older checkout never downgrades it. An older
    record is first copied whole into backups/ beside it (server/finance/backup.py),
    and its upgrade is one transaction: it lands whole or not at all."""
    path = str(path or db_path())
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=BUSY_SECONDS)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version > SCHEMA_VERSION:
        conn.close()
        raise RecordTooNew(
            f"{path} is at schema version {version}, but this code only knows "
            f"version {SCHEMA_VERSION}: update Kopifund before opening it")
    if path != ":memory:":
        # switching a record to WAL needs it to itself: not while another
        # thread here is upgrading it (a record already in WAL is not changed)
        with _UPGRADE_LOCK:
            conn.execute("PRAGMA journal_mode = WAL")
    # A record already at this version is opened as it is, so a read (every API
    # request) writes nothing. Change SCHEMA only together with SCHEMA_VERSION.
    if version < SCHEMA_VERSION:
        try:
            _upgrade(conn, path)
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            conn.close()
            raise
    if path != ":memory:":
        Path(path).chmod(0o600)
    return conn


# the server answers on threads, and the app's first page asks for several
# things at once: one upgrade at a time in this process, so an old record is
# copied and upgraded once, not once per request
_UPGRADE_LOCK = threading.Lock()


class UpgradeRefused(RuntimeError):
    """The record holds something the new schema does not take (a value
    outside a CHECK's list, a link to a row that is not there). The upgrade
    changed nothing, and the message says what to look at."""


def _upgrade(conn, path):
    """Bring an older (or new, empty) record to SCHEMA_VERSION.

    The copy comes first and outside the write transaction (SQLite's backup
    cannot read a database its own connection holds a write lock on); a copy
    that fails stops the upgrade. The rest (`_migrate`) and the version are
    then one IMMEDIATE transaction, which takes the write lock before it
    reads the version again: another process that upgraded the record
    meanwhile leaves this one nothing to do.

    G17: foreign keys are off for the upgrade, as SQLite's procedure for
    rebuilding a table asks (its "Making Other Kinds Of Table Schema
    Changes"): the pragma does nothing inside a transaction, so it is set
    before BEGIN and set back after COMMIT or ROLLBACK, and `_migrate` runs
    PRAGMA foreign_key_check before the commit instead."""
    with _UPGRADE_LOCK:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version >= SCHEMA_VERSION:
            return                              # another thread finished it
        if version and path != ":memory:":
            from .backup import backup_record
            backup_record(conn, path, f"v{version}-before-v{SCHEMA_VERSION}")
        conn.execute("PRAGMA foreign_keys = OFF")
        try:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("PRAGMA user_version").fetchone()[0] < SCHEMA_VERSION:
                _migrate(conn)
                conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.execute("PRAGMA foreign_keys = ON")


# each CHECK built from an enum: (table, column, the key in enums())
CHECKED = (("category_group", "kind", "group_kind"), ("category", "bucket", "bucket"),
           ("category", "grp", "cost_type"), ("category", "need_class", "need_class"),
           ("payee", "kind", "payee_kind"), ("upload_attempt", "origin", "origin"),
           ("upload_step", "step", "step"), ("upload_step", "status", "step_status"),
           ("series_override", "group_kind", "series_group"), ("series_override", "cadence", "cadence"))


def _migrate(conn):
    """Everything an older record needs, inside the upgrade's transaction,
    with foreign keys off. Decided by what the tables hold rather than the
    version number, so a record is brought up the same way however it got
    here, and running it on a record already in the new shape changes
    nothing.

    Every table the schema names is rebuilt to it (`_rebuild`); a table it
    does not have yet is made new. Before anything is written, a value the
    new CHECKs would refuse stops the upgrade (`_refuse_unlisted`): the
    record is never changed to fit. After the data moves, a link that points
    nowhere stops it too (foreign_key_check)."""
    script = schema()
    _refuse_unlisted(conn)
    trips = _legacy_trip_links(conn)
    # a view or trigger names the tables it reads, and a rename checks every
    # one of them: they go first and the schema makes them again
    for kind, name in conn.execute(
            "SELECT type, name FROM sqlite_master WHERE type IN ('view', 'trigger')").fetchall():
        conn.execute(f'DROP {kind.upper()} "{name}"')
    conn.execute("DROP TABLE IF EXISTS mirror_map")      # T12: nothing ever read or wrote it
    have = _tables(conn)
    for name, create in _creates(script).items():
        if name in have:
            _rebuild(conn, name, create)
    for statement in _statements(script):
        conn.execute(statement)
    # T12: txn.currency and period.currency now link to currency; every code
    # the rows use is listed there already, since the loader adds it first
    conn.execute("""INSERT OR IGNORE INTO currency(code)
                    SELECT currency FROM txn UNION SELECT currency FROM period""")
    for trip_id, countries, keys in trips:
        set_trip_links(conn, trip_id, countries, keys)
    link_statement_files(conn)
    assign_category_groups(conn)
    # T6: bucket follows the group from here on (the triggers); a category
    # assign_category_groups did not move gets its group's bucket now
    conn.execute(f"""UPDATE category SET bucket = (SELECT {_bucket_case('g.kind')} FROM category_group g
                                                   WHERE g.id = category.group_id)
                     WHERE group_id IS NOT NULL""")
    _shipped_styles(conn)
    broken = conn.execute("PRAGMA foreign_key_check").fetchall()
    if broken:
        where = sorted({f"{r[0]} -> {r[2]}" for r in broken})
        raise UpgradeRefused(f"{len(broken)} rows link to a row that is not there ({', '.join(where)}); "
                             "the record was left as it was")


def _tables(conn):
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def _creates(script):
    """{table: its CREATE TABLE statement} in the schema's order."""
    out = {}
    for s in _statements(script):
        m = re.search(r"^CREATE TABLE IF NOT EXISTS (\w+) \(", s, re.M)
        if m:
            out[m.group(1)] = s[m.start():]
    return out


def _rebuild(conn, name, create):
    """One table to its new shape, steps 4 to 7 of SQLite's procedure: make
    the new table under another name, copy every column the two share (a
    new column takes its default), drop the old one, rename the new one.
    Its indexes went with the old table; the schema makes them again."""
    temp = f"{name}__v{SCHEMA_VERSION}"
    conn.execute(f"DROP TABLE IF EXISTS {temp}")
    conn.execute(create.replace(f"CREATE TABLE IF NOT EXISTS {name} (", f"CREATE TABLE {temp} (", 1))
    old = _columns(conn, name)
    cols = ", ".join(f'"{r[1]}"' for r in conn.execute(f"PRAGMA table_info({temp})") if r[1] in old)
    conn.execute(f"INSERT INTO {temp} ({cols}) SELECT {cols} FROM {name}")
    conn.execute(f"DROP TABLE {name}")
    conn.execute(f"ALTER TABLE {temp} RENAME TO {name}")


def _refuse_unlisted(conn):
    """UpgradeRefused when a column the schema now CHECKs holds a value
    outside its list, or a trip names a country that is not three capital
    letters, naming each one: the upgrade stops rather than change the
    owner's data to fit."""
    lists = enums()
    have = _tables(conn)
    found = []
    for table, column, key in CHECKED:
        if table not in have or column not in _columns(conn, table):
            continue
        bad = [r[0] for r in conn.execute(
            f"SELECT DISTINCT {column} FROM {table} WHERE {column} IS NOT NULL "
            f"AND {column} NOT IN ({_sql_list(lists[key])}) ORDER BY 1")]
        if bad:
            found.append(f"{table}.{column} holds {', '.join(map(repr, bad))}, "
                         f"not one of {', '.join(lists[key])}")
    codes = {c for _, countries, _ in _legacy_trip_links(conn) for c in countries}
    bad = sorted(c for c in codes if not re.fullmatch(r"[A-Z]{3}", c))
    if bad:
        found.append(f"trip.countries holds {', '.join(map(repr, bad))}, not three-letter codes")
    if found:
        raise UpgradeRefused("The record was not upgraded, and is as it was: " + "; ".join(found))


def _legacy_trip_links(conn):
    """[(trip id, countries, pinned keys)] from the comma-separated columns a
    record before version 5 kept them in; [] once they are gone."""
    if not {"countries", "extra_keys"} <= _columns(conn, "trip"):
        return []
    return [(r[0], [c.strip() for c in r[1].split(",") if c.strip()],
             [k.strip() for k in r[2].split(",") if k.strip()])
            for r in conn.execute("SELECT id, countries, extra_keys FROM trip ORDER BY id")]


def _shipped_styles(conn):
    """A bucket the record has under a shipped name, with no icon of its own,
    gets the shipped one (server/analytics/categories.json)."""
    for g in shipped_category_groups():
        conn.execute("UPDATE category_group SET icon = ? WHERE name = ? AND icon IS NULL",
                     (g.get("icon"), g["name"]))


def _statements(script):
    """A script as single statements, so each can run inside one transaction
    (executescript would commit it first)."""
    out, buf = [], ""
    for line in script.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            out.append(buf.strip())
            buf = ""
    if buf.strip() and not all(ln.strip().startswith("--") or not ln.strip() for ln in buf.splitlines()):
        raise ValueError(f"an incomplete statement at the end of the schema: {buf.strip()[:60]}")
    return out


def _columns(conn, table):
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


@contextmanager
def transaction(conn):
    """All or nothing: a write that fails halfway leaves the record as it was.
    Inside a transaction already open, it is a savepoint of that one.

    The outer one is IMMEDIATE: it takes the write lock before its first read.
    A plain (deferred) BEGIN reads under a snapshot and asks for the lock only
    at its first write; in WAL mode SQLite cannot wait there while another
    connection writes, so it fails at once with "database is locked". Asked
    up front, the lock waits up to BUSY_SECONDS like any other."""
    if conn.in_transaction:
        conn.execute("SAVEPOINT kopifund")
        try:
            yield conn
        except BaseException:
            conn.execute("ROLLBACK TO kopifund")
            conn.execute("RELEASE kopifund")
            raise
        conn.execute("RELEASE kopifund")
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def _id(conn, table, column, value):
    if value in (None, ""):
        return None
    row = conn.execute(f"SELECT id FROM {table} WHERE {column} = ?", (value,)).fetchone()
    return row["id"] if row else None


class NotThere(LookupError):
    """The row a write names is not in the record, or not the one it meant."""


# the user-owned tables a write may name a row of by id (pick)
PICKABLE = {"trip": "trip", "category": "category", "category_group": "bucket"}


def pick(conn, table, row_id=None, name=None, named=False):
    """G17: the row a write means. By its id when one is sent: a name sent
    with it is the name the page read, and must still be the row's (an id
    SQLite has handed to a newer row, or a row renamed in another tab, is
    refused rather than written). With no id, by name, as before G17, so a
    tab left open across an upgrade keeps working. NotThere says why not.

    `named`: a write that moves or deletes what hangs off a row (a delete, a
    move, where they go) needs that name beside the id, so an id alone can
    never land the rows on a newer row that took it."""
    what = PICKABLE[table]
    if row_id is not None:
        if named and name in (None, ""):
            raise NotThere(f"Send the {what}'s name beside its id: reload the page")
        row = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (row_id,)).fetchone()
        if row is None:
            raise NotThere(f"That {what} is not in your record any more: reload the page")
        if name not in (None, "") and row["name"] != name:
            raise NotThere(f"That {what} is called {row['name']} now, not {name}: reload the page")
        return row
    if name in (None, ""):
        raise NotThere(f"Say which {what}")
    row = conn.execute(f"SELECT * FROM {table} WHERE name = ?", (name,)).fetchone()
    if row is None:
        raise NotThere(f"No {what} {name!r}")
    return row


def _ensure_account(conn, name, account_no, currency):
    """An account the statements print but the user has not described yet
    (G10: a new user has no Notion to import accounts from). The user owns the
    row afterwards; the loader only ever creates, never edits it."""
    if not account_no or _id(conn, "account", "account_no", account_no):
        return
    name = name or account_no
    if _id(conn, "account", "name", name):
        name = f"{name} · {account_no[-4:]}"
    conn.execute("INSERT OR IGNORE INTO account(name, account_no, base_currency) VALUES (?,?,?)",
                 (name, account_no, currency))


def _ensure_currency(conn, code):
    if code:
        conn.execute("INSERT OR IGNORE INTO currency(code) VALUES (?)", (code,))


# ---------------------------------------------------------------- user tables

def _shipped():
    from .paths import SERVER
    path = SERVER / "analytics" / "categories.json"
    return json.loads(path.read_text(encoding="utf-8"))


def shipped_categories():
    """The category list that ships with Kopifund (server/analytics/categories.json)."""
    return _shipped()["categories"]


def retired_categories():
    """Categories the shipped list used to have (Transport): a record that
    still has one keeps it, and a load adds it back while rows name it."""
    return _shipped().get("retired_categories", [])


def shipped_category_groups():
    """The buckets that ship with Kopifund, in order: [{name, kind, sort_order}]."""
    return _shipped().get("category_groups", [])


# shipped categories that were renamed: old name -> new name. The rules keep
# their own copy (categorise.RENAMED_CATEGORIES); a test holds the two equal.
RENAMED_CATEGORIES = {"Tuition": "Education"}

# the fixed buckets: one of each, never deleted
FIXED_GROUP = {"income": "Income", "transfer": "Transfers"}
FALLBACK_SPEND_GROUP = "Other"


def category_ids(conn):
    """{name: category id} for every name a rule may use, which is how a
    parsed row's category is found: each category's own name first, then the
    old names of ones the user renamed or deleted (category_alias), then a
    shipped name that was renamed (Tuition, now Education)."""
    out = {r[0]: r[1] for r in conn.execute(
        "SELECT a.name, a.category_id FROM category_alias a")}
    out.update({r[0]: r[1] for r in conn.execute("SELECT name, id FROM category")})
    for old, new in RENAMED_CATEGORIES.items():
        if old not in out and new in out:
            out[old] = out[new]
    return out


def category_alias_names(conn):
    return [r[0] for r in conn.execute("SELECT name FROM category_alias ORDER BY name")]


def category_renames(conn):
    """{old name: current name} for the categories the user renamed (not the
    ones deleted into another): what a stat looking for a shipped name by name
    follows (server/analytics/stats/common.py, `category_names`)."""
    return {r[0]: r[1] for r in conn.execute(
        """SELECT a.name, c.name FROM category_alias a JOIN category c ON c.id = a.category_id
           WHERE a.how = 'rename' ORDER BY a.name""")}


def _group_id(conn, kind, name=None):
    """The bucket a category of `kind` belongs in when nothing better is known:
    the fixed Income or Transfers bucket, or for spending the one named
    `name` when it is a spend bucket, else "Other", made when missing."""
    if kind in FIXED_GROUP:
        row = conn.execute("SELECT id FROM category_group WHERE kind = ? ORDER BY sort_order, id",
                           (kind,)).fetchone()
        name = name or FIXED_GROUP[kind]
    else:
        row = None
        if name:
            row = conn.execute("SELECT id FROM category_group WHERE name = ? AND kind = 'spend'",
                               (name,)).fetchone()
        if row is None:
            name = FALLBACK_SPEND_GROUP
            row = conn.execute("SELECT id FROM category_group WHERE name = ? AND kind = 'spend'",
                               (name,)).fetchone()
    if row:
        return row[0]
    if conn.execute("SELECT 1 FROM category_group WHERE name = ?", (name,)).fetchone():
        name = f"{name} ({kind})"         # the name is taken by a bucket of another kind
    top = conn.execute("SELECT COALESCE(MAX(sort_order), 0) FROM category_group").fetchone()[0]
    return conn.execute("INSERT INTO category_group(name, kind, sort_order, icon) VALUES (?,?,?,?)",
                        (name, kind, top + 10, _shipped_icon(name))).lastrowid


def _shipped_icon(name):
    """The icon server/analytics/categories.json gives the bucket of that name, or None."""
    return next((g.get("icon") for g in shipped_category_groups() if g["name"] == name), None)


def assign_category_groups(conn, groups=None, categories=None, buckets=None):
    """Give every category with no bucket one. A record with no buckets at all
    (new, or older than version 3) gets the shipped ones first. A shipped
    category goes into its shipped bucket when the record has it and the
    kinds agree; anything else goes by its own category.bucket, so moving
    it never changes what the stats count: Income and Transfer categories
    into the Income and Transfers buckets, the rest into "Other". Runs inside
    the caller's transaction. Returns how many categories it placed.

    G17 (T6): category.bucket now follows the bucket a category sits in, so
    a bucket wanted from elsewhere comes in as `buckets`, {name: Spend,
    Income or Transfer} (the Notion import's), and a category with none
    there or in the record takes the shipped list's."""
    if not conn.execute("SELECT 1 FROM category_group").fetchone():
        for g in groups if groups is not None else shipped_category_groups():
            conn.execute("INSERT OR IGNORE INTO category_group(name, kind, sort_order, icon) VALUES (?,?,?,?)",
                         (g["name"], g["kind"], g.get("sort_order"), g.get("icon")))
    # a category no longer shipped (Transport, before its split) still has a
    # bucket it belongs in, for a record that keeps it
    listed = _shipped().get("retired_categories", []) + list(
        categories if categories is not None else shipped_categories())
    shipped = {c["name"]: c.get("category_group") for c in listed}
    wanted = {c["name"]: c.get("bucket") for c in listed}
    wanted.update(buckets or {})
    # a category whose bucket (a record from before version 5) no longer
    # matches its group's kind is placed again, so the page and the stats agree
    conn.execute(f"""UPDATE category SET group_id = NULL WHERE group_id IS NOT NULL
                     AND bucket IN ({_sql_list(BUCKET_OF_KIND.values())})
                     AND bucket != (SELECT {_bucket_case('g.kind')}
                                    FROM category_group g WHERE g.id = category.group_id)""")
    n = 0
    for c in conn.execute("SELECT id, name, bucket FROM category WHERE group_id IS NULL").fetchall():
        kind = KIND_OF_BUCKET.get((buckets or {}).get(c["name"]) or c["bucket"] or wanted.get(c["name"]), "spend")
        gid = None
        if shipped.get(c["name"]):
            row = conn.execute("SELECT id FROM category_group WHERE name = ? AND kind = ?",
                               (shipped[c["name"]], kind)).fetchone()
            gid = row[0] if row else None
        if gid is None:
            gid = _group_id(conn, kind)
        conn.execute("UPDATE category SET group_id = ? WHERE id = ?", (gid, c["id"]))
        n += 1
    return n


def seed_categories(conn, categories=None, named=()):
    """Add every shipped category the record lacks, and fill a blank cost
    type, need class or order from the shipped list. A value the user (or
    Notion) set is never overwritten. Without this a record built by `load`
    alone holds bare category names, and every spending stat reads zero.
    A shipped category that was renamed is renamed in the record first, so
    its rows and budgets follow it rather than a second, empty one appearing;
    a record that already has the new name keeps both as they are. A shipped
    name the user renamed or deleted (category_alias) is not brought back.
    A category no longer shipped (Transport, split in two) is added only when
    `named`, the categories the rows being loaded carry, still names it: a
    personal rule not yet moved by split-transport keeps its rows counted.
    Every category ends up in a bucket (assign_category_groups); one added to
    a record that already had categories goes last in its bucket, so lists
    keep the order the Categories page shows."""
    n = 0
    shipped = categories if categories is not None else shipped_categories()
    if categories is None:
        shipped = shipped + [c for c in retired_categories() if c["name"] in set(named)]
    with transaction(conn):
        for old, new in RENAMED_CATEGORIES.items():
            # not when the user has had a say: a name they renamed or deleted
            # on the Categories page (either one is an alias then) stays theirs
            n += conn.execute("""UPDATE category SET name = ? WHERE name = ?
                                 AND NOT EXISTS (SELECT 1 FROM category WHERE name = ?)
                                 AND NOT EXISTS (SELECT 1 FROM category_alias WHERE name IN (?, ?))""",
                              (new, old, new, old, new)).rowcount
        # taken after the shipped renames, so a category renamed just now
        # counts as one the record had, and keeps its place
        held = {r[0] for r in conn.execute("SELECT name FROM category")}
        gone = set(category_alias_names(conn))
        for c in shipped:
            if c["name"] in gone:
                continue
            # G17 (T6): not bucket, which follows the bucket assign_category_groups puts it in
            n += conn.execute("""INSERT INTO category(name, grp, need_class, sort_order)
                                 VALUES (?,?,?,?)
                                 ON CONFLICT(name) DO UPDATE SET
                                   grp=COALESCE(category.grp, excluded.grp),
                                   need_class=COALESCE(category.need_class, excluded.need_class),
                                   sort_order=COALESCE(category.sort_order, excluded.sort_order)
                                 WHERE category.grp IS NULL
                                   OR category.need_class IS NULL OR category.sort_order IS NULL""",
                              (c["name"], c.get("group"), c.get("need_class"),
                               c.get("sort_order"))).rowcount
        assign_category_groups(conn, categories=shipped)
        if held:
            for c in shipped:
                if c["name"] in held or c["name"] in gone:
                    continue
                conn.execute("""UPDATE category SET sort_order = 1 + (
                                  SELECT MAX(o.sort_order) FROM category o
                                  WHERE o.group_id = category.group_id AND o.id != category.id)
                                WHERE name = ? AND EXISTS (
                                  SELECT 1 FROM category o WHERE o.group_id = category.group_id
                                  AND o.id != category.id AND o.sort_order IS NOT NULL)""", (c["name"],))
    return n


# ---------------------------------------------------------------- trips (G17, T9)

def set_trip_links(conn, trip_id, countries, keys):
    """Replace a trip's countries (ISO alpha-3, in the order given, each once)
    and pinned txn keys. Runs inside the caller's transaction."""
    codes = list(dict.fromkeys(c for c in countries if c))
    conn.execute("DELETE FROM trip_country WHERE trip_id = ?", (trip_id,))
    conn.executemany("INSERT INTO trip_country(trip_id, code, position) VALUES (?, ?, ?)",
                     [(trip_id, c, i) for i, c in enumerate(codes)])
    conn.execute("DELETE FROM trip_pin WHERE trip_id = ?", (trip_id,))
    conn.executemany("INSERT INTO trip_pin(trip_id, txn_key) VALUES (?, ?)",
                     [(trip_id, k) for k in sorted({k for k in keys if k})])


def trip_countries(conn, trip_id=None):
    """{trip id: [ISO alpha-3 code]} in each trip's own order; with `trip_id`,
    that trip's list."""
    out = {}
    for tid, code in conn.execute("SELECT trip_id, code FROM trip_country ORDER BY trip_id, position"):
        out.setdefault(tid, []).append(code)
    return out if trip_id is None else out.get(trip_id, [])


def trip_pins(conn, trip_id=None, follow=True):
    """{trip id: [txn key]} pinned to each trip, sorted; with `trip_id`, that
    trip's list. `follow`: a key the parser has since changed (txn_key_alias)
    reads as the row's key now, so the pin stays on its row. Without it, the
    keys as they were saved, which is what an Undo compares."""
    sql = ("""SELECT p.trip_id, COALESCE(t.txn_key, p.txn_key) FROM trip_pin p
              LEFT JOIN txn_key_alias a ON a.old_key = p.txn_key
              LEFT JOIN txn t ON t.id = a.txn_id""" if follow else "SELECT trip_id, txn_key FROM trip_pin")
    out = {}
    for tid, key in conn.execute(sql):
        out.setdefault(tid, set()).add(key)
    out = {tid: sorted(keys) for tid, keys in out.items()}
    return out if trip_id is None else out.get(trip_id, [])


def import_user_tables(conn, snapshot, plan=None):
    """Write the user-owned tables from a Notion-built snapshot (and the
    config's [plan]): the one-time import. The fields it covers are replaced
    wholesale; nothing the loader owns is touched. Returns counts."""
    counts = {}
    with transaction(conn):
        for c in snapshot.get("categories", []):
            # the import replaces what the app edited: a category it brings
            # back is that name again, no longer an alias of another
            conn.execute("DELETE FROM category_alias WHERE name = ?", (c["name"],))
            conn.execute("""INSERT INTO category(name, grp, need_class, sort_order)
                            VALUES (?,?,?,?)
                            ON CONFLICT(name) DO UPDATE SET
                              grp=excluded.grp, need_class=excluded.need_class,
                              sort_order=excluded.sort_order""",
                         (c["name"], c.get("group"), c.get("need_class"), c.get("sort_order")))
        # G17 (T6): Notion's bucket says which kind of bucket a category goes
        # in; one now in a bucket of another kind is placed again
        wanted = {c["name"]: c["bucket"] for c in snapshot.get("categories", [])
                  if c.get("bucket") in KIND_OF_BUCKET}
        for name, bucket in wanted.items():
            conn.execute(f"""UPDATE category SET group_id = NULL WHERE name = ? AND group_id IS NOT NULL
                             AND ? != (SELECT {_bucket_case('g.kind')} FROM category_group g
                                       WHERE g.id = category.group_id)""", (name, bucket))
        assign_category_groups(conn, buckets=wanted)
        counts["categories"] = len(snapshot.get("categories", []))
        for a in snapshot.get("accounts", []):
            _ensure_currency(conn, a.get("base_currency"))
            same_no = a.get("account_no") and conn.execute(
                "SELECT id FROM account WHERE account_no = ? AND name != ?",
                (a["account_no"], a["name"])).fetchone()
            if same_no:
                # renamed in Notion: the number is the identity, the name follows
                conn.execute("UPDATE account SET name = ? WHERE id = ?", (a["name"], same_no[0]))
            conn.execute("""INSERT INTO account(name, account_no, institution, class, subclass,
                              base_currency) VALUES (?,?,?,?,?,?)
                            ON CONFLICT(name) DO UPDATE SET account_no=excluded.account_no,
                              institution=excluded.institution, class=excluded.class,
                              subclass=excluded.subclass, base_currency=excluded.base_currency""",
                         (a["name"], a.get("account_no") or None, a.get("institution"),
                          a.get("class"), a.get("subclass"), a.get("base_currency")))
        counts["accounts"] = len(snapshot.get("accounts", []))
        for c in snapshot.get("cards", []):
            if not c.get("last4"):
                continue
            conn.execute("""INSERT INTO card(name, last4, network, type, state) VALUES (?,?,?,?,?)
                            ON CONFLICT(last4) DO UPDATE SET name=excluded.name,
                              network=excluded.network, type=excluded.type, state=excluded.state""",
                         (c["name"], c["last4"], c.get("network"), c.get("type"), c.get("state")))
        counts["cards"] = len(snapshot.get("cards", []))
        imported = [t["name"] for t in snapshot.get("trips", [])]
        for t in snapshot.get("trips", []):
            conn.execute("""INSERT INTO trip(name, start_date, end_date, notes) VALUES (?,?,?,?)
                            ON CONFLICT(name) DO UPDATE SET start_date=excluded.start_date,
                              end_date=excluded.end_date, notes=excluded.notes""",
                         (t["name"], t.get("start") or None, t.get("end") or None, t.get("notes") or ""))
            set_trip_links(conn, _id(conn, "trip", "name", t["name"]), t.get("countries") or (),
                           t.get("keys") or ())
        if "trips" in snapshot:
            # the import is the whole trip list: a trip deleted at the source
            # must stop claiming rows; its rows are reassigned on the next load.
            # G17 (T7): the schema lets its rows go (ON DELETE SET NULL) and
            # takes its countries and pins with it (CASCADE)
            gone = [r[0] for r in conn.execute(
                f"SELECT id FROM trip WHERE name NOT IN ({','.join('?' * len(imported))})", imported)
                    ] if imported else [r[0] for r in conn.execute("SELECT id FROM trip")]
            for tid in gone:
                conn.execute("DELETE FROM trip WHERE id = ?", (tid,))
            counts["trips retired"] = len(gone)
        counts["trips"] = len(imported)
        n = 0
        for b in snapshot.get("budgets", []):
            cid = _id(conn, "category", "name", b.get("category"))
            if cid is None or not b.get("month"):
                continue
            conn.execute("""INSERT INTO budget(category_id, month, planned_minor) VALUES (?,?,?)
                            ON CONFLICT(category_id, month) DO UPDATE
                              SET planned_minor=excluded.planned_minor""",
                         (cid, b["month"], b.get("planned_minor")))
            n += 1
        counts["budgets"] = n
        if plan is not None:
            conn.execute("DELETE FROM plan")
            for k, v in plan_to_db(plan).items():
                conn.execute("INSERT INTO plan(key, value) VALUES (?, ?)", (k, json.dumps(v, default=str)))
            counts["plan keys"] = len(plan)
    return counts


# money inside the plan: top-level keys, and fields of the plan's lists
PLAN_MONEY = ("dca", "insurance", "other_income", "repayments")
PLAN_LIST_MONEY = {"trips": ("per_day", "flights"), "one_offs": ("amount",), "income": ("gross",),
                   "investing": ("amount",)}
# G14: money inside one of the plan's objects (the Net worth projection's settings)
PLAN_DICT_MONEY = {"projection": ("spend", "milestone")}


def _plan_minor(x):
    """A plan amount in dollars as integer cents. A Decimal or its text is
    converted exactly; a number goes through to_minor, which refuses a value
    finer than a cent."""
    if isinstance(x, (Decimal, str)) and not isinstance(x, bool):
        v = Decimal(str(x)) * 100
        if v != v.to_integral_value():
            raise ValueError(f"{x!r} is not a whole number of cents")
        return int(v)
    return to_minor(x)


def plan_to_db(plan):
    """The [plan] shape with every money field as integer cents."""
    out = {}
    for k, v in plan.items():
        if k in PLAN_MONEY and v is not None:
            out[k] = _plan_minor(v)
        elif k in PLAN_LIST_MONEY and isinstance(v, list):
            out[k] = [{f: (_plan_minor(x) if f in PLAN_LIST_MONEY[k] and x is not None else
                           x if not isinstance(x, (date_type,)) else x.isoformat())
                       for f, x in item.items()} for item in v]
        elif k in PLAN_DICT_MONEY and isinstance(v, dict):
            out[k] = {f: (_plan_minor(x) if f in PLAN_DICT_MONEY[k] and x is not None else x)
                      for f, x in v.items()}
        else:
            out[k] = v.isoformat() if isinstance(v, date_type) else v
    return out


def load_plan(conn):
    """The plan as the dict [plan] in config.toml used to hold, what
    forward_view and the Net worth projection read. G17: its money is exact
    Decimal dollars (server/finance/money.py, from_cents), never a float; the API
    sends it as cents (server/finance/planning.py, plan_out)."""
    raw = {r["key"]: json.loads(r["value"]) for r in conn.execute("SELECT key, value FROM plan")}
    out = {}
    for k, v in raw.items():
        if k in PLAN_MONEY and v is not None:
            out[k] = from_cents(v)
        elif k in PLAN_LIST_MONEY and isinstance(v, list):
            out[k] = [{f: (from_cents(x) if f in PLAN_LIST_MONEY[k] and x is not None else x)
                       for f, x in item.items()} for item in v]
        elif k in PLAN_DICT_MONEY and isinstance(v, dict):
            out[k] = {f: (from_cents(x) if f in PLAN_DICT_MONEY[k] and x is not None else x)
                      for f, x in v.items()}
        else:
            out[k] = v
    return out


def save_plan(conn, plan):
    """Replace the plan (G8: the app owns it)."""
    with transaction(conn):
        conn.execute("DELETE FROM plan")
        for k, v in plan_to_db(plan).items():
            conn.execute("INSERT INTO plan(key, value) VALUES (?, ?)", (k, json.dumps(v)))


# ---------------------------------------------------------------- loader

# what a row is linked to, decided from what the rules and the trips gave it;
# a load (load_parsed) and a relink of the rows already in the record
# (server/finance/relink.py) both decide it here, so the two always agree
LINKS = ("payee_id", "category_id", "rule", "trip_id")
PAYEE_FIELDS = ("kind", "country", "recurring", "account_no")


def payee_values(p):
    """(kind, country, recurring, account_no) as the payee table stores one
    payee of snapshot.payees_from_rules."""
    return (p.get("kind"), p.get("country"), int(bool(p.get("recurring"))), p.get("account_no") or "")


def upsert_payees(conn, payees):
    """Every payee the rules name, added or brought up to date."""
    for p in payees:
        conn.execute("""INSERT INTO payee(name, kind, country, recurring, account_no)
                        VALUES (?,?,?,?,?)
                        ON CONFLICT(name) DO UPDATE SET kind=excluded.kind,
                          country=excluded.country, recurring=excluded.recurring,
                          account_no=excluded.account_no""",
                     (p["name"], *payee_values(p)))


def link_values(conn, r, category_of, trip_name):
    """{payee_id, category_id, rule, trip_id} for one categorised row: its
    payee and category (`category_of` is category_ids) and the trip it is on,
    by name, or None."""
    return dict(payee_id=_id(conn, "payee", "name", r.get("payee")),
                category_id=category_of.get(r.get("category") or ""),
                rule=r.get("rule") or "",
                trip_id=_id(conn, "trip", "name", trip_name))


def _identity(account_no, currency, txn_date, amount_minor, balance_after_minor):
    return (account_no or "", currency, txn_date, amount_minor, balance_after_minor)


def _detail(source, reference, card_last4, purchase_date):
    return (source or "", reference or "", card_last4 or "", purchase_date or "")


def _agree(a, b, balanced):
    """Two rows' details (_detail) name the same payment: the reference, card
    and purchase date agree wherever both print one, and without a running
    balance to vouch for the row, the statement file is the same too."""
    fields = range(1, 4) if balanced else range(4)
    return all(not a[i] or not b[i] or a[i] == b[i] for i in fields)


def _pairs(old, new, balanced=True):
    """[(txn id, old key, new key)] for one group of rows alike on the five
    identity fields, or [] when it cannot be paired safely. `old` holds
    (id, key, detail, text) in id order, `new` (key, detail, text) in parse
    order. A group of one each pairs unless their details disagree (_agree).
    A larger group is split on the details
    a re-key rarely touches (statement, reference, card, purchase date), and
    a split part pairs only when it is one each, or when every row on each
    side prints the same text: rows nothing tells apart (two identical
    coffees) are interchangeable, but two different payments that share a
    date and an amount on a card statement (no running balance) are not, and
    pairing those by id would move a pin to the wrong one."""
    if len(old) != len(new):
        return []
    if len(old) == 1:
        # one each is the same row only when nothing that names a payment
        # disagrees: a replaced card statement (no running balance) can drop
        # one payment and bring another on the same day at the same amount
        return [(old[0][0], old[0][1], new[0][0])] if _agree(old[0][2], new[0][1], balanced) else []
    out = []
    for d in {x[2] for x in old} | {x[1] for x in new}:
        o = [x for x in old if x[2] == d]
        n = [x for x in new if x[1] == d]
        if len(o) != len(n):
            continue
        if len(o) == 1 or (len({x[3] for x in o}) == 1 and len({x[2] for x in n}) == 1):
            out += [(tid, ok, nk) for (tid, ok, _, _), (nk, _, _) in zip(o, n)]
    return out


def rekeys(conn, rows):
    """[(txn id, old key, new key)]: the record's live rows this parser run
    gives a new key. `txn_key` hashes parsed text (merchant, reference, type),
    so a parser change, or a town added to places.csv that moves the city out
    of a merchant, renames a row the statement prints just as before. A row is
    the same row when its account, currency, date, amount and running balance
    are: a record row no parsed key covers is paired with a parsed key the
    record has never held on those five (_pairs says when a group of several
    alike can be), and only on a known account number. A row left unpaired is archived and its new key inserted,
    and sync.check_pinned_rows says if that drops anything the owner saved
    on one."""
    known = {k for (k,) in conn.execute("SELECT txn_key FROM txn")}
    fresh = {}
    for r in rows:
        # a row with no account number could be any account's: never paired
        if r["txn_key"] not in known and r.get("account_no"):
            ident = _identity(r.get("account_no"), r["currency"], r["date"], to_minor(r["amount"]),
                              to_minor(r.get("balance_after")))
            detail = _detail(r.get("source"), r.get("reference"), r.get("card_last4"), r.get("purchase_date"))
            text = (r.get("merchant") or "", r.get("counterparty") or "", r.get("memo") or "")
            fresh.setdefault(ident, []).append((r["txn_key"], detail, text))
    if not fresh:
        return []
    keys = {r["txn_key"] for r in rows}
    gone = {}
    for t in conn.execute("""SELECT id, txn_key, account_no, currency, txn_date, amount_minor,
                                    balance_after_minor, source, reference, card_last4, purchase_date,
                                    merchant_raw, counterparty, memo
                             FROM txn WHERE archived_at IS NULL ORDER BY id"""):
        if t["txn_key"] not in keys and t["account_no"]:
            ident = _identity(t["account_no"], t["currency"], t["txn_date"], t["amount_minor"],
                              t["balance_after_minor"])
            detail = _detail(t["source"], t["reference"], t["card_last4"], t["purchase_date"])
            gone.setdefault(ident, []).append((t["id"], t["txn_key"], detail,
                                               (t["merchant_raw"], t["counterparty"], t["memo"])))
    out = []
    for ident, new in fresh.items():
        out += _pairs(gone.get(ident, []), new, balanced=ident[4] is not None)
    return out


def former_keys(conn, rows=()):
    """{txn_key: [every key that row was loaded under before]}: the record's
    aliases, and for `rows` (a parser run not loaded yet) the keys rekeys
    would move. A keys.csv rule saved on an old key reads the row by any of
    them (categorise.tier_values), so it follows the row."""
    out = {}
    for old, new in conn.execute("""SELECT a.old_key, t.txn_key FROM txn_key_alias a
                                    JOIN txn t ON t.id = a.txn_id ORDER BY a.old_key"""):
        out.setdefault(new, []).append(old)
    for _, old, new in rekeys(conn, rows):
        out.setdefault(new, []).extend([old, *out.get(old, ())])
    # one order before and after the aliases are saved, so the same rule wins
    return {k: sorted(set(v)) for k, v in out.items()}


def _apply_rekeys(conn, moved):
    """Give each re-keyed row its new key in place, so its id (and its
    first_seen) stays, and record the old key in txn_key_alias: every trip
    pin, mark and series edit saved on the old key follows it there."""
    for tid, old_key, new_key in moved:
        conn.execute("UPDATE txn SET txn_key = ? WHERE id = ?", (new_key, tid))
        conn.execute("""INSERT INTO txn_key_alias(old_key, txn_id) VALUES (?, ?)
                        ON CONFLICT(old_key) DO UPDATE SET txn_id = excluded.txn_id""", (old_key, tid))
        # a key that comes back is the row's own again, not an alias of it
        conn.execute("DELETE FROM txn_key_alias WHERE old_key = ?", (new_key,))


def load_parsed(conn, parsed, payees=(), trips_by_key=None, complete=True, kind="load"):
    """Write one parser run (finance.sync.parse plus categorise) into the
    loader-owned tables, in one transaction.

    New keys are inserted, changed rows updated in place, and when `complete`
    (every folder parsed, no parser failed) rows and periods the run no longer
    produced are archived rather than deleted. A row the run gives a new key
    (rekeys) keeps its id and gets the new key, the old one going into
    txn_key_alias. Returns the sync_run counts, plus how many rows were
    re-keyed."""
    started = now_fine()
    ins = upd = arch = 0
    trips_by_key = trips_by_key or {}
    with transaction(conn):
        moved = rekeys(conn, parsed.rows)
        _apply_rekeys(conn, moved)
        upsert_payees(conn, payees)
        seen_periods = set()
        for b in parsed.blocks:
            _ensure_currency(conn, b["currency"])
            _ensure_account(conn, b.get("account"), b.get("account_no"), b["currency"])
            vals = dict(name=b["period"], account_id=_id(conn, "account", "account_no", b["account_no"]),
                        account_no=b["account_no"], currency=b["currency"],
                        period_end=b.get("period_end") or None,
                        opening_minor=to_minor(b.get("opening")), closing_minor=to_minor(b.get("closing")),
                        total_in_minor=to_minor(b.get("total_in")), total_out_minor=to_minor(b.get("total_out")),
                        fx_to_sgd_e9=rate_e9(b.get("fx_to_sgd")),
                        parser_reconciled=_bool_or_none(b.get("reconciled")),
                        n_txns=int(b["n_txns"]) if str(b.get("n_txns", "")).isdigit() else None,
                        note=b.get("note") or "", source=b.get("source") or "")
            cols = ", ".join(vals)
            conn.execute(f"""INSERT INTO period({cols}, archived_at) VALUES ({", ".join("?" * len(vals))}, NULL)
                             ON CONFLICT(name) DO UPDATE SET {", ".join(f"{k}=excluded.{k}" for k in vals if k != "name")},
                               archived_at=NULL""", tuple(vals.values()))
            seen_periods.add(b["period"])
        seen_keys = set()
        stamp = now()
        category_of = category_ids(conn)
        for r in parsed.rows:
            _ensure_currency(conn, r["currency"])
            vals = dict(
                txn_key=r["txn_key"],
                period_id=_id(conn, "period", "name", r.get("period")),
                account_id=_id(conn, "account", "account_no", r.get("account_no")),
                account_no=r.get("account_no") or "", currency=r["currency"],
                txn_date=r["date"], purchase_date=r.get("purchase_date") or None,
                amount_minor=to_minor(r["amount"]), balance_after_minor=to_minor(r.get("balance_after")),
                direction=r.get("direction") or "", bank_txn_type=r.get("bank_txn_type") or "",
                flow_class=r.get("flow_class") or "", merchant_raw=r.get("merchant") or "",
                merchant_country=r.get("merchant_country") or "",
                card_id=_id(conn, "card", "last4", r.get("card_last4")),
                card_last4=r.get("card_last4") or "", fx_original=r.get("fx_original") or "",
                reference=r.get("reference") or "", counterparty=r.get("counterparty") or "",
                memo=r.get("memo") or "",
                is_synthetic=int(r.get("bank_txn_type") == SYNTHETIC_TYPE),
                **link_values(conn, r, category_of, trips_by_key.get(r["txn_key"])),
                source=r.get("source") or "")
            old = conn.execute("SELECT * FROM txn WHERE txn_key = ?", (r["txn_key"],)).fetchone()
            if old is None:
                cols = ", ".join(vals)
                conn.execute(f"INSERT INTO txn({cols}, first_seen, last_seen) "
                             f"VALUES ({', '.join('?' * len(vals))}, ?, ?)",
                             (*vals.values(), stamp, stamp))
                ins += 1
            else:
                changed = any(old[k] != v for k, v in vals.items()) or old["archived_at"] is not None
                conn.execute(f"UPDATE txn SET {', '.join(f'{k} = ?' for k in vals)}, last_seen = ?, "
                             f"archived_at = NULL WHERE txn_key = ?",
                             (*vals.values(), stamp, r["txn_key"]))
                upd += int(changed)
            seen_keys.add(r["txn_key"])
        # a key the run produces is that row's own: an alias left from an
        # earlier re-key under the same key would send pins to another row
        conn.executemany("DELETE FROM txn_key_alias WHERE old_key = ?", [(k,) for k in seen_keys])
        if complete:
            gone = [row["txn_key"] for row in conn.execute(
                "SELECT txn_key FROM txn WHERE archived_at IS NULL")
                if row["txn_key"] not in seen_keys]
            for k in gone:
                conn.execute("UPDATE txn SET archived_at = ? WHERE txn_key = ?", (stamp, k))
            arch = len(gone)
            for row in conn.execute("SELECT name FROM period WHERE archived_at IS NULL").fetchall():
                if row["name"] not in seen_periods:
                    conn.execute("UPDATE period SET archived_at = ? WHERE name = ?", (stamp, row["name"]))
        conn.execute("""INSERT INTO sync_run(kind, started_at, finished_at, status, inserted,
                          updated, archived) VALUES (?,?,?,?,?,?,?)""",
                     (kind, started, now_fine(), "ok", ins, upd, arch))
    return {"inserted": ins, "updated": upd, "archived": arch, "rekeyed": len(moved)}


def _bool_or_none(x):
    if x in (None, ""):
        return None
    return int(str(x).strip().lower() in ("1", "true", "yes", "ok"))


def recorded_statement_files(path=None):
    """{path: sha256} of every statement file the record has seen. Read only: a
    record that is not there yet, or has no such table, has seen none."""
    path = Path(path or db_path())
    if not path.exists():
        return {}
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        return dict(conn.execute("SELECT path, sha256 FROM statement_file"))
    except sqlite3.OperationalError:
        return {}
    finally:
        conn.close()


def load_statements_meta(conn, root, files, parsers=None):
    """Record every statement PDF a run read: path, sha256, superseded, and
    (G17, T10) the parser of its folder (`parsers`, {file: parser}) and the
    name it was uploaded under, from the upload history, else the name it
    had when first seen. Then every period is linked to its file."""
    import hashlib
    from .paths import current_statements
    root = Path(root)
    files = [Path(f) for f in files]
    parsers = {Path(k): v for k, v in (parsers or {}).items()}
    current = set(current_statements(files))
    with transaction(conn):
        # each content's saved date and name, taken before any row changes:
        # files that trade names keep their own, whichever is written first
        first_seen = dict(conn.execute("SELECT sha256, first_seen FROM statement_file"))
        named = {h: n for h, n in conn.execute("SELECT sha256, original_name FROM statement_file") if n}
        named.update(uploaded_names(conn))
        for f in files:
            h = hashlib.sha256(f.read_bytes()).hexdigest()
            rel = str(f.relative_to(root)) if f.is_relative_to(root) else str(f)
            # the same content is one row (sha256 is unique): a file renamed
            # inside its folder takes over its old row rather than adding a
            # second one that would break the unique key
            conn.execute("DELETE FROM statement_file WHERE sha256=? AND path<>?", (h, rel))
            conn.execute("""INSERT INTO statement_file(path, sha256, parser, original_name, first_seen,
                              superseded) VALUES (?,?,?,?,?,?)
                            ON CONFLICT(path) DO UPDATE SET sha256=excluded.sha256,
                              parser=excluded.parser, original_name=excluded.original_name,
                              superseded=excluded.superseded,
                              first_seen=CASE WHEN ? THEN excluded.first_seen
                                              ELSE statement_file.first_seen END""",
                         (rel, h, parsers.get(f, ""), named.get(h) or f.name, first_seen.get(h) or now(),
                          int(f not in current), h in first_seen))
        link_statement_files(conn)


def uploaded_names(conn):
    """{sha256: the file's name as it was uploaded} from the upload history's
    upload steps that kept a file (the latest name wins)."""
    out = {}
    for (raw,) in conn.execute("""SELECT input FROM upload_step WHERE step = 'upload' AND status = 'ok'
                                  ORDER BY id"""):
        try:
            given = json.loads(raw)
        except ValueError:
            continue
        if isinstance(given, dict) and given.get("sha256") and given.get("file"):
            out[str(given["sha256"])] = str(given["file"])
    return out


def link_statement_files(conn):
    """G17 (T10): point every period at the statement_file its block came
    from: its `source` up to any "#<part>", matched against the file's path.
    A period whose file is not a recorded statement PDF gets NULL. Returns
    how many periods changed."""
    of_source = """(SELECT f.id FROM statement_file f WHERE f.path =
                      CASE WHEN instr(period.source, '#') > 0
                           THEN substr(period.source, 1, instr(period.source, '#') - 1)
                           ELSE period.source END)"""
    return conn.execute(f"UPDATE period SET statement_file_id = {of_source} "
                        f"WHERE statement_file_id IS NOT {of_source}").rowcount


CPF_ACCOUNTS = ("oa", "sa", "ma")


def load_broker_and_cpf(conn, portfolio, cpf):
    """Store the brokerage and CPF statements the parsers read off disk.

    `cpf["rejected"]` (read_cpf's years that do not reconcile) replaces the
    cpf_rejected table whole: it is what this read of the folder found, so a
    year fixed since drops out and a year that broke since is named, and
    snapshot() stops showing that year's older stored figures."""
    with transaction(conn):
        if cpf is not None:
            conn.execute("DELETE FROM cpf_rejected")
            for r in cpf.get("rejected") or ():
                off = r.get("off") or {}
                conn.execute("""INSERT INTO cpf_rejected(year, file, off_oa_minor, off_sa_minor,
                                  off_ma_minor) VALUES (?,?,?,?,?)""",
                             (r["year"], r["file"],
                              *(to_minor(off.get(k) or 0) for k in CPF_ACCOUNTS)))
        for st in (portfolio or {}).get("statements", []):
            period = st.get("period") or {}
            conn.execute("""INSERT INTO broker_statement(file, period_from, period_to, base_currency, data)
                            VALUES (?,?,?,?,?)
                            ON CONFLICT(file) DO UPDATE SET period_from=excluded.period_from,
                              period_to=excluded.period_to, base_currency=excluded.base_currency,
                              data=excluded.data""",
                         (st["file"], period.get("from"), period.get("to"), st.get("base_currency"),
                          json.dumps(st, sort_keys=True)))
        for st in (cpf or {}).get("statements", []):
            conn.execute("""INSERT INTO cpf_statement(year, file, as_of, data) VALUES (?,?,?,?)
                            ON CONFLICT(year) DO UPDATE SET file=excluded.file,
                              as_of=excluded.as_of, data=excluded.data""",
                         (st["year"], st["file"], st.get("as_of"), json.dumps(st, sort_keys=True)))


# ---------------------------------------------------------------- reading

def snapshot(conn, notes=(), wallets=()):
    """The snapshot dict server/finance/snapshot.py documents, built from the record
    instead of Notion, so every stat runs unchanged on SQLite. A row whose block
    has no FX rate carries amount_sgd_minor None (SPEC: unknown, flagged).

    `wallets` is the account numbers the config's statement folders mark as
    wallets (snapshot.wallet_account_nos); an account with no
    subclass of its own reads as subclass Wallet when it is one of them."""
    transactions = []
    for r in conn.execute("SELECT * FROM v_txn ORDER BY txn_date, txn_key"):
        transactions.append({
            "key": r["txn_key"], "date": r["txn_date"], "month": r["month"],
            # G20: the record's own cents; a snapshot holds no amount in dollars
            "amount_minor": r["amount_minor"],
            "amount_sgd_minor": r["amount_sgd_minor"],
            "currency": r["currency"], "account": r["account_name"] or "",
            "account_no": r["account_no"], "bank_txn_type": r["bank_txn_type"],
            "direction": r["direction"], "flow_class": r["flow_class"],
            "merchant_raw": r["merchant_raw"], "merchant_country": r["merchant_country"],
            "counterparty": r["counterparty"], "memo": r["memo"], "reference": r["reference"],
            "payee": r["payee_name"] or "", "category": r["category_name"] or "",
            "need_class": r["need_class"], "bucket": r["bucket"], "group": r["grp"],
            "category_group": r["category_group"],
            "card": r["card_last4"], "trip": r["trip_name"] or "",
            "synthetic": bool(r["is_synthetic"]), "period": r["period_name"] or ""})
    periods = [{
        "name": r["name"], "account": r["account_name"] or "", "account_no": r["account_no"],
        "period_end": r["period_end"] or "", "currency": r["currency"],
        # G20: the record's own cents, as amount_sgd_minor on a row
        "opening_minor": r["opening_minor"], "closing_minor": r["closing_minor"],
        "total_in_minor": r["total_in_minor"], "total_out_minor": r["total_out_minor"],
        "fx_to_sgd": None if r["fx_to_sgd_e9"] is None else r["fx_to_sgd_e9"] / 1e9,
        "closing_sgd_minor": r["closing_sgd_minor"],
        "reconciled": bool(r["reconciled"]),
        "has_balances": bool(r["has_balances"])}
        for r in conn.execute("SELECT * FROM v_period ORDER BY name")]
    counts = {r["payee_id"]: r["n"] for r in conn.execute(
        "SELECT payee_id, COUNT(*) AS n FROM v_txn GROUP BY payee_id")}
    defaults = _default_categories(conn)
    # G16: a payee the owner said is not recurring (server/finance/series.py) reads as the rules not marking it
    from .series import payees_off_in
    off = payees_off_in(conn)
    payees = [{"name": r["name"], "kind": r["kind"], "country": r["country"],
               "default_category": defaults.get(r["id"], ""),
               "recurring": bool(r["recurring"]) and r["name"] not in off,
               "txn_count": counts.get(r["id"], 0), "account_no": r["account_no"]}
              for r in conn.execute("SELECT * FROM payee ORDER BY name")]
    categories = [{"name": r["name"], "bucket": r["bucket"], "group": r["grp"],
                   "need_class": r["need_class"], "sort_order": r["sort_order"],
                   "category_group": r["category_group"]}
                  for r in conn.execute("""SELECT c.*, g.name AS category_group FROM category c
                                           LEFT JOIN category_group g ON g.id = c.group_id
                                           ORDER BY COALESCE(c.sort_order, 1e9), c.name""")]
    category_groups = [{"name": r["name"], "kind": r["kind"], "sort_order": r["sort_order"]}
                       for r in conn.execute("SELECT * FROM category_group "
                                             "ORDER BY COALESCE(sort_order, 1e9), name")]
    acct_counts = {r["account_id"]: (r["n"], r["lo"], r["hi"]) for r in conn.execute(
        "SELECT account_id, COUNT(*) n, MIN(txn_date) lo, MAX(txn_date) hi FROM v_txn GROUP BY account_id")}
    wallets = set(wallets)
    # an account the loader created (no Notion import, G10) has no subclass
    # until the user sets one; the config's statement folders still know a
    # wallet, and coverage needs that to tell an idle wallet from a hole
    accounts = [{"name": r["name"], "account_no": r["account_no"] or "",
                 "institution": r["institution"], "class": r["class"],
                 "subclass": r["subclass"] or ("Wallet" if r["account_no"] in wallets else None),
                 "base_currency": r["base_currency"],
                 "statements_from": (acct_counts.get(r["id"]) or (0, None, None))[1],
                 "statements_to": (acct_counts.get(r["id"]) or (0, None, None))[2],
                 "data_coverage": None,
                 "txn_count": (acct_counts.get(r["id"]) or (0, None, None))[0]}
                for r in conn.execute("SELECT * FROM account ORDER BY name")]
    cards = [{"name": r["name"], "last4": r["last4"], "network": r["network"], "type": r["type"],
              "state": r["state"]} for r in conn.execute("SELECT * FROM card ORDER BY last4, name")]
    budgets = [{"category": r["category"], "month": r["month"],
                "planned_minor": r["planned_minor"], "actual_minor": r["actual_minor"]}
               for r in conn.execute("SELECT * FROM v_budget ORDER BY month, category")]
    countries = trip_countries(conn)
    trips = [{"name": r["name"], "start": r["start_date"] or "", "end": r["end_date"] or "",
              "countries": sorted(countries.get(r["id"], []))}
             for r in conn.execute("SELECT * FROM trip ORDER BY start_date, end_date, name")]
    portfolio = {"source": "tiger", "folder": "statements/tiger", "statements": [
        json.loads(r["data"]) for r in conn.execute(
            "SELECT data FROM broker_statement ORDER BY period_from, file")]}
    from .snapshot import assemble, checked_cpf
    # the years the last load rejected: their stored statement, if any, is an
    # older read of that year and is not shown beside the rejection
    rejected = [{"year": r["year"], "file": r["file"],
                 "off": {k: from_minor(r[f"off_{k}_minor"]) for k in CPF_ACCOUNTS}}
                for r in conn.execute("SELECT * FROM cpf_rejected ORDER BY year, file")]
    held_back = {r["year"] for r in rejected}
    # the same balance check the parser's own folder read applies: a year
    # stored before that check existed is rejected here rather than counted
    cpf = checked_cpf([st for st in (json.loads(r["data"]) for r in conn.execute(
        "SELECT data FROM cpf_statement ORDER BY year")) if st["year"] not in held_back],
        rejected=rejected)
    missing = conn.execute("SELECT COUNT(*) FROM v_missing_fx").fetchone()[0]
    notes = list(notes) + ([f"{missing} rows sit in a block with no FX rate to SGD, so their "
                            "SGD amount is unknown and they are left out of SGD totals"]
                           if missing else [])
    databases = [{"name": name, "data_source_id": "kopifund.db", "rows": n, "filter": "all"}
                 for name, n in (("Transactions", len(transactions)), ("Period balances", len(periods)),
                                 ("Payees", len(payees)), ("Categories", len(categories)),
                                 ("Accounts", len(accounts)), ("Cards", len(cards)),
                                 ("Budgets", len(budgets)), ("Trips", len(trips)))]
    snap = assemble("sqlite", databases, transactions, periods, payees, categories,
                    accounts, cards, budgets, trips, notes=notes, portfolio=portfolio, cpf=cpf,
                    category_groups=category_groups)
    # a stat that looks for a shipped category by name follows a rename
    snap["category_aliases"] = category_renames(conn)
    return snap


def _default_categories(conn):
    """A payee's default category: the category most of its rows carry, ties
    to the larger absolute SGD amount, then the later name; the rule
    `finance.py payees` uses. Derived, never stored."""
    best = {}
    for r in conn.execute("""SELECT payee_id, category_name, COUNT(*) n,
                                    SUM(ABS(COALESCE(amount_sgd_minor, 0))) v
                             FROM v_txn WHERE payee_id IS NOT NULL AND category_name IS NOT NULL
                             GROUP BY payee_id, category_name
                             ORDER BY payee_id, n DESC, v DESC, category_name DESC"""):
        best.setdefault(r["payee_id"], r["category_name"])
    return best
