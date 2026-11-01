-- The record as schema version 5 built it (finance/db.py schema() before G16's series
-- edits), for tests/test_schema_v6.py: a v5 record is this script, invented rows, and
-- PRAGMA user_version = 5. Never edit it to match a newer schema.
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
-- finance/categories.py holds both lists and is their one writer.
CREATE TABLE IF NOT EXISTS category_group (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  kind TEXT NOT NULL CHECK (kind IN ('spend', 'income', 'transfer')),
  sort_order REAL,
  icon TEXT,
  colour TEXT
) STRICT;

-- `bucket` is derived: the triggers below keep it the bucket of its group's
-- kind (BUCKET_OF_KIND), and nothing else writes it
CREATE TABLE IF NOT EXISTS category (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  bucket TEXT CHECK (bucket IN ('Spend', 'Income', 'Transfer')),
  grp TEXT CHECK (grp IN ('Fixed', 'Variable', 'Savings')),
  need_class TEXT CHECK (need_class IN ('Need', 'Want', 'Savings', 'Investment', 'Income', 'Transfer')),
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
  kind TEXT CHECK (kind IN ('Merchant', 'Person', 'Employer', 'Institution')),
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
  origin TEXT NOT NULL DEFAULT 'upload' CHECK (origin IN ('upload', 'rules', 'transactions', 'app')),   -- the page that started it
  started_at TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS upload_step (
  id INTEGER PRIMARY KEY,
  attempt_id INTEGER NOT NULL REFERENCES upload_attempt(id),
  step TEXT NOT NULL CHECK (step IN ('upload', 'check', 'load', 'mirror')),
  started_at TEXT NOT NULL,
  finished_at TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('ok', 'duplicate', 'refused', 'problems', 'blocked', 'failed', 'error')),
  summary TEXT NOT NULL DEFAULT '',
  input TEXT NOT NULL DEFAULT '{}',       -- JSON
  output TEXT NOT NULL DEFAULT '{}'       -- JSON
) STRICT;
CREATE INDEX IF NOT EXISTS upload_step_attempt_idx ON upload_step(attempt_id, id);

-- derived ---------------------------------------------------------------
-- category.bucket follows its group's kind: on a new category, on a move, on
-- a write to bucket itself (put back at once), and when a group's kind changes
CREATE TRIGGER IF NOT EXISTS category_bucket_insert AFTER INSERT ON category
WHEN NEW.group_id IS NOT NULL
BEGIN
  UPDATE category SET bucket = (SELECT CASE g.kind WHEN 'spend' THEN 'Spend' WHEN 'income' THEN 'Income' WHEN 'transfer' THEN 'Transfer' END FROM category_group g WHERE g.id = NEW.group_id) WHERE id = NEW.id AND bucket IS NOT (SELECT CASE g.kind WHEN 'spend' THEN 'Spend' WHEN 'income' THEN 'Income' WHEN 'transfer' THEN 'Transfer' END FROM category_group g WHERE g.id = NEW.group_id);
END;
CREATE TRIGGER IF NOT EXISTS category_bucket_update AFTER UPDATE OF group_id, bucket ON category
WHEN NEW.group_id IS NOT NULL
BEGIN
  UPDATE category SET bucket = (SELECT CASE g.kind WHEN 'spend' THEN 'Spend' WHEN 'income' THEN 'Income' WHEN 'transfer' THEN 'Transfer' END FROM category_group g WHERE g.id = NEW.group_id) WHERE id = NEW.id AND bucket IS NOT (SELECT CASE g.kind WHEN 'spend' THEN 'Spend' WHEN 'income' THEN 'Income' WHEN 'transfer' THEN 'Transfer' END FROM category_group g WHERE g.id = NEW.group_id);
END;
CREATE TRIGGER IF NOT EXISTS category_group_kind AFTER UPDATE OF kind ON category_group
BEGIN
  UPDATE category SET bucket = (CASE NEW.kind WHEN 'spend' THEN 'Spend' WHEN 'income' THEN 'Income' WHEN 'transfer' THEN 'Transfer' END) WHERE group_id = NEW.id;
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
