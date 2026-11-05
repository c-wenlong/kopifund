# Analytics layer: contract

Everything below is stdlib Python 3.14. Nothing here writes to Notion except the
hub page's Metrics and Months tables. The stats are shown by the app in
[../../apps/web/](../../apps/web/), which `serve.py` feeds from the cache; there is no second
viewer.

## Files

```
server/analytics/
  SPEC.md                this file
  stats/                 one module per analysis, each writes one result JSON
  benchmarks.json        generic Singapore reference numbers with sources (research agent);
                         a benchmarks.json in the data folder replaces it (see Settings)
  categories.json        the Categories rows, refreshed from Notion, so --from-csv works offline
  run.py                 build or reuse today's snapshot, run every stat, write the cache
  api.py                 G19: what the Net worth endpoints answer (GET /api/net-worth, /api/growth,
                         POST /api/growth/preview and /api/growth/inputs), over growth.py; the cache
                         reader that was server.py is server/finance/results_cache.py
  growth.py              net worth by month (server/finance/net_worth.py), and the projection of cash,
                         Tiger Brokers, CPF and their total the Net worth page draws from the plan's
                         income rows, investing steps and settings (GET /api/net-worth, /api/growth)
  income.py              G14: the plan's income rows month by month in cents (proration, open-ended
                         rows, salary growth, bonuses, the AW ceiling), shared by growth.py and forward-view
  tax_rules.py           G14: Singapore resident income tax, earned income relief and CPF relief, dated,
                         with the IRAS source
server/finance/                 G19: the pieces the app shares with the stats, so analytics
                         imports finance and never the reverse
  snapshot.py            pull the eight databases into one cached JSON, or
                         flatten pages a caller already read (finance.py sync)
  cpf_rules.py           the CPF rules that projection runs on, dated, with their source; the employee
                         shares and self-employed MediSave from G14
  settings.py            the [analytics] table of config.toml (common.settings re-exports it)
  net_worth.py           net worth by month from the cached results (growth.net_worth re-exports it)
  metrics.py             the hub page's Metrics rows (publish.metrics_from re-exports it)
  cadence.py             the recurring detector's engine: amount levels, cadence fit, pause and
                         stop; stats/recurring_detector.py and the Recurring page both run on it
  figures.py             the row arithmetic the stats share with it (common re-exports it)
~/.kopifund/
  cache/                 one snapshot of the live databases is 3.5 MB
    snapshot-YYYY-MM-DD.json      the dataset
    results/<stat-id>-YYYY-MM-DD.json
    latest.json          {"snapshot": "...", "results": {"<stat-id>": "..."}}
finance.py analyse [--from-csv DIR] [--refresh] [--only STAT ...] [--out DIR]  builds snapshot (if stale or --refresh) and every stat
```

`serve.py` (the app's server, `make serve`) reads the cache through
`server/finance/results_cache.py` (`Cache`, G19: this was `server/analytics/server.py`) and answers `GET /api/results/<id>`. The app's
overview computes the hub page's Metrics rows with `publish.metrics_from()` over
the cached latest results, so the app and the Notion hub page read the same
numbers. [../tests/fixtures/analytics/](../tests/fixtures/analytics) holds invented
results, one per stat, laid out like the cache; [../tests/test_cache.py](../tests/test_cache.py)
checks the result shape against them.

## Snapshot JSON (`~/.kopifund/cache/snapshot-<date>.json`)

Two commands write this file and it has to be the same file either way.
`analyse` builds it when today's is missing or `--refresh` is given, reading the
eight databases itself. `sync` and `rollup` write it at the end of a real run
from the pages they have already read (`snapshot.build_from_pages`), so the
monthly run reads the eight databases once rather than twice. Both go through
one flattening, so the source of the pages cannot change what is in the file.

```json
{
  "meta": {
    "generated_at": "2026-09-05T10:12:00+08:00",
    "source": "notion",                      // or "csv"
    "databases": [
      {"name": "Transactions", "data_source_id": "...", "rows": 3200, "filter": "all"},
      {"name": "Period balances", "data_source_id": "...", "rows": 96, "filter": "all"},
      {"name": "Payees", ...}, {"name": "Categories", ...}, {"name": "Accounts", ...},
      {"name": "Cards", ...}, {"name": "Budgets", ...}, {"name": "Trips", ...}
    ],
    "fx_note": "Amount SGD uses the period's FX rate to SGD (latest exchange seen in that wallet); approximate for non-SGD wallets"
  },
  "transactions": [ { "key": "...", "date": "2026-08-04", "month": "2026-08", "amount_minor": -1240,
      "amount_sgd_minor": -1240, "currency": "SGD", "account": "POSB Everyday Savings", "account_no": "...",
      "bank_txn_type": "...", "direction": "Out", "flow_class": "External",
      "merchant_raw": "...", "merchant_country": "SGP", "counterparty": "", "memo": "", "reference": "",
      "payee": "Grab", "category": "Transport", "need_class": "Need", "bucket": "Spend", "group": "Variable", "category_group": "Getting around",
      "card": "5454", "trip": "Example trip 2026", "synthetic": false, "period": "130-24680-5 · SGD · 2026-08" } ],
  "periods": [ { "name", "account", "account_no", "period_end", "currency", "opening_minor", "closing_minor",
      "total_in_minor", "total_out_minor", "fx_to_sgd", "closing_sgd_minor", "reconciled", "has_balances" } ],
  "payees": [ { "name", "kind", "country", "default_category", "recurring", "txn_count", "account_no" } ],
  "categories": [ { "name", "bucket", "group", "need_class", "sort_order", "category_group" } ],
  "category_groups": [ { "name", "kind", "sort_order" } ],
  "category_aliases": { "<old name>": "<current name>" },        // SQLite only: categories the user renamed
  "accounts": [ { "name", "account_no", "institution", "class", "subclass", ..., "label", "bank" } ],   // label and bank: decision 27
  "cards": [ ... ], "budgets": [ { "category", "month", "planned_minor", "actual_minor" } ], "trips": [ { "name", "start", "end", "countries" } ],
  "portfolio": {
    "source": "tiger", "folder": "statements/tiger",
    "statements": [ { "file": "2026/1-8.pdf", "account": "...", "base_currency": "USD",
      "period": {"from": "2026-01-01", "to": "2026-08-31"},
      "overview": {"begin": {...}, "end": {"cash": 420.15, "stock": 12580.4, "total": 13000.55}},
      "cash_report": { "Deposits": 4000.0, ... }, "deposits": [ {"date", "amount", "currency"} ],
      "withdrawals": [ ... ], "deposits_base": 4000.0, "dividends": [ ... ], "dividends_base": 92.4,
      "withholding_tax_base": -27.72, "fees_base": -6.1, "holdings": [ ... ],
      "holdings_value_base": 12580.4, "trades": [ ... ], "forex": [ ... ],
      "fx_to_base": {"SGD": 0.7867, ...}, "fx_date": "2026-08-31" } ]
  },
  "cpf": {
    "source": "cpf", "folder": "statements/cpf",
    "statements": [ { "file": "2025/2025.pdf", "account": "T00...H", "year": 2025,
      "as_of": "2025-12-31",
      "opening": {"oa": 500.0, "sa": 150.0, "ma": 2000.0, "total": 2650.0},
      "closing": {"oa": 1800.0, "sa": 580.0, "ma": 2600.0, "total": 4980.0},
      "inflow": {"employee": 1200.0, "employer": 1020.0, "employment": 2220.0,
        "government": 120.0, "base_interest": 100.0, "extra_interest": 20.0,
        "interest": 120.0, "government_support": 0.0, "voluntary": 0.0, "total": 2340.0},
      "outflow": {"housing": 0.0, "healthcare": 0.0, "retirement": 0.0, "education": 0.0,
        "investment": 0.0, "others": 10.0, "total": 10.0},
      "transactions": [ {"date", "code", "for_month", "ref", "oa", "sa", "ma"} ],
      "refs": {"A": "EXAMPLE EMPLOYER PTE LTD"} } ],
    "rejected": [ {"year": 2024, "file": "2024/2024.pdf",
      "off": {"oa": 10.0, "sa": 0.0, "ma": 0.0}} ]
  }
}
```

`portfolio` is the Tiger Brokers annual exports, parsed off disk by
[../finance/parsers/parse_tiger.py](../finance/parsers/parse_tiger.py) on every build, oldest first,
and `meta.portfolio_files` lists the files they came from. It is not one of the
eight data sources and never reaches Notion, so `meta.databases` does not mention
it. The folder is `[analytics] tiger_folder` in config.toml, `statements/tiger`
when the key is absent and nothing at all when it is set to an empty string; a
missing folder or a missing parser gives `statements: []` rather than an error.
Snapshots written before this key existed have neither it nor
`meta.portfolio_files`, so every reader asks for it with `snapshot.get("portfolio")`.

`cpf` is the CPF yearly statements, parsed off disk by
[../finance/parsers/parse_cpf.py](../finance/parsers/parse_cpf.py) on every build, oldest first,
and `meta.cpf_files` lists the files they came from. It is read exactly the way
`portfolio` is: `[analytics] cpf_folder` in config.toml, `statements/cpf`
when the key is absent and nothing at all when it is set to an empty string, and
a missing folder or a missing parser gives `statements: []` rather than an
error. CPF is Singapore's mandatory savings scheme, so this block is an asset
that is locked rather than a liability, and readers ask for it with
`snapshot.get("cpf")`. A year whose opening plus transactions is not its
closing is never in `statements`: it is listed under `rejected` with the gap
per account, and the `cpf` stat names it (decision 25). `finance.py load` keeps
the rejected years in the record's `cpf_rejected` table, so a SQLite snapshot
names them too and leaves out any older figures it stored for that year.

Rules: keys are stable snake_case; money is integer cents under `<name>_minor` and never
a float in dollars (G20): `amount_minor` in the row's currency, `amount_sgd_minor` in SGD
(null where the block has no FX rate), a block's balances and a budget's figures likewise.
A SQLite snapshot takes each straight from the record's own column. A Notion snapshot turns
Notion's numbers into cents once, as it reads them (`snapshot.cents`, half up), and a CSV one
a parsed amount the same way, with a foreign row's SGD amount by the record's own rule
(`money.fx_minor` at the block's rate). The `portfolio` and `cpf` blocks are the parsers'
reading of each statement, its figures as printed, which the portfolio and cpf stats turn
into cents as they read them. A snapshot saved before G20 holds dollars, so `analyse`
builds it again rather than reuse it (`snapshot.in_cents`);
`synthetic` is true for `YouTrip Unprinted` rows; relation values are resolved to names.
A snapshot built `--from-csv` (parser output plus rules) must produce the same shape,
with `need_class` etc. taken from `server/analytics/categories.json` fallback when Notion is
not reachable, and `meta.source = "csv"`.

`category_group` is the category's bucket on the Categories page (Food, Income,
Transfers), and `category_groups` lists the buckets in order with their kind
(spend, income or transfer). It is not `bucket`, which stays Spend, Income or
Transfer and is what the stats filter on. Only a SQLite snapshot holds the
user's own buckets; a Notion or CSV snapshot takes each category's bucket from
the shipped `categories.json` by name, and a category it does not list has none.

## Stat result JSON (`~/.kopifund/cache/results/<stat-id>-<date>.json`)

```json
{
  "id": "monthly-cashflow",
  "title": "Monthly cash flow",
  "generated_at": "2026-09-05T10:13:02+08:00",
  "snapshot": "snapshot-2026-09-05.json",
  "sources": [ {"database": "Transactions", "rows_used": 1200, "filter": "flow_class = External and bucket != Transfer"} ],
  "params": { "from": "2025-01", "to": "2026-08", "currency": "SGD" },
  "benchmarks": [ {"name": "...", "value": ..., "source_url": "...", "as_of": "2024"} ],   // optional
  "data": { ... stat-specific, documented in the module docstring ... },
  "findings": [ "one sentence each, plain words, numbers included" ],
  "caveats": [ "what the numbers cannot see" ]
}
```

Every stat module exposes `ID`, `TITLE`, `run(snapshot, benchmarks) -> result dict`
and a `CHART` hint for the app: `{"type": "bar|line|stacked|table|scatter",
"x": "...", "series": [...]}` (the full hint is under "The chart hint" below).
The app reads `latest.json`, then each result, and prints `generated_at` and
`sources` under every chart.

### Money in a result (G20)

- **A money field is integer cents named `<name>_minor`**, like the record's
  columns: `cash_minor`, `spend_minor`, `value_sgd_minor`. A result written
  before its stat moved to cents holds dollars under the bare name (`cash`
  1234.56). The name decides which, never the size of the value, so an old
  dollar result can never be read as cents. A stat moves all its money at
  once, and holds no dollar twin beside a `_minor` field. All fifteen have
  moved (G20 line 6).
- **The chart hint's unit for it is `sgd_minor`** ("The chart hint" below).
  `sgd`, the unit of dollars, is gone: no hint uses it, and a result whose
  hint says `sgd` is from before G20.
- **Fractions and percents keep their own scales**: `share` 0.3 and
  `gain_pct` 18.7 are not money and do not change; nor do counts, months and
  days.
- **Cents arithmetic** lives in `server/finance/figures.py`, which the recurring
  engine (`server/finance/cadence.py`) shares, and `server/analytics/stats/common.py`
  re-exports it: `sum_cents`, `mean_cents`, `median_cents` (the mean of the
  two middle values for an even count) and `div_cents` (a total over a count
  of months or rows). Inputs and answers are ints, a float is refused, an
  empty list is 0, and every quotient rounds half up, away from zero, exactly
  as `server/finance/money.py`'s `to_cents` rounds dollars: 2.5 cents is 3, -2.5 is
  -3. `sgd_minor(row)` is a row's `amount_sgd_minor` (0 where it is null),
  and common.py adds `spend_minor` and `income_minor` (the signed sums of
  spend and income), `spend_base_minor` and `share_note_minor` (decision
  10). The float helpers they replaced (`sgd`, `spend`, `income`, `mean`,
  `median`, `stdev`, `per_month`, `spend_base`, `share_note`) are gone (G20
  line 6).
- **Findings print amounts with `server/finance/money.py`'s `label`**: "S$1,234.05",
  "−S$12.00", "USD 1,234.56".
- **Readers take the cents only (G20 line 6).** Outside the stats a money
  field is read through `server/finance/money.py`'s `stat_cents(obj, name)`:
  `<name>_minor`, None when it is absent or null. A bare `<name>` is never
  read, as dollars or as cents. Home (`server/finance/home.py`), Net worth
  (`server/finance/net_worth.py`, `growth.py`), the hub page's Metrics and Months
  rows (`server/finance/metrics.py`, `publish.py`), the export, `server/finance/recurring.py`
  (Recurring, its series view and Home's Coming up) and `server/finance/suggestions.py`
  read through it, and the app's stat cards through `apps/web/src/stats/money.ts`
  (`centsOf`, and `sgd_minor` in the renderer). A stat that reads another
  stat's result reads it through `stat_cents` too (forward-view reads
  travel's rate a day and savings-runway's cash so). Nothing turns a stat's
  dollars into cents: the `to_cents` calls that did (Home's headline,
  `net_worth.to_cents`, `stat_dollars`) are gone, and `to_cents` is left for
  money that arrives in dollars (the plan a user types, a statement's text, a
  Notion number, a reference figure).
- **A result from before G20 is no result.** `money.in_cents(result)` says
  whether a result may be read, from its chart hint, never a value's size:
  unit `sgd_minor` is cents; unit `sgd`, or no unit (which meant `sgd`), is a
  result in dollars; a stat whose chart is not money (data-quality counts
  rows) is in cents when it holds a `<name>_minor` field. The results cache
  (`server/finance/results_cache.py`, and `server/analytics/run.py`'s `cached_results` for
  the hub page) leaves an old result out, so `GET /api/results/<id>` answers
  404, the card says the analysis has not run, and Home, Net worth and the
  hub page show nothing from it until the stats run again, as decision 26
  did for a forward-view result from before G17. The app holds the same rule
  (`inCents` in `apps/web/src/stats/money.ts`) for an answer it kept from an
  earlier visit.
- **The hub page turns cents into dollars in one place.** `metrics_from` and
  `months_from` build their rows in cents (`Value_minor` on a money row,
  `Value` on one that is not money; `Income_minor` to `Cash_minor`), Home
  reads those cents, and `publish.notion_row` writes each `<field>_minor` as
  the dollars `from_cents` gives, the float a Notion Number field takes. A
  note's amounts are printed from the cents.
- **Holdings and references (G20 line 4).** `portfolio`, `cpf` and
  `benchmarks-sg` write cents:
  - `portfolio` keeps each amount in its own currency's cents with the
    currency beside it (`base_currency` for the `_base_minor` figures,
    `currency` on a holding or a deposit) and converts to SGD cents by the
    record's own rule for a foreign row: a statement's rate to SGD as an
    integer scaled by 10^9 (`fx_to_sgd_e9`, `money.rate_e9`, the function
    `server/finance/db.py` stores `period.fx_to_sgd_e9` with), and
    `money.fx_minor(minor, e9)`, which is v_txn's
    `round(amount_minor * fx_to_sgd_e9 / 1e9)` done in integers, half up.
    `sgd_per_base` stays, to six places, for reading; the conversion no
    longer rounds the rate to six places first, so a value can move by a
    cent. A holding's `cost_price` and `close_price` are prices per share as
    the statement prints them, not amounts, and stay as they are;
  - `cpf` turns each statement figure to cents once, as it reads it; its
    two shares are fractions;
  - `benchmarks-sg`: "Benchmarks JSON" below.
- **Cash stats (G20 line 2).** monthly-cashflow, savings-runway, one-offs,
  data-quality and income-stability write cents and no dollars. They add the rows' `amount_sgd_minor` (`common.sgd_minor`, `spend_minor`,
  `income_minor`), take a balance from a block's `closing_sgd_minor`, average
  with `div_cents` or `mean_cents` and take medians with `median_cents`;
  income-stability's spread is the population standard deviation of the
  months' cents, to the cent, half up. A column named for an account or a
  category carries the suffix too (`Main Bank_minor`, `Salary_minor`), and a
  threshold in money is cents (one-offs' `floor_sgd_minor`). forward-view
  and portfolio read savings-runway's cash through `stat_cents`.
- **The forward view and the Net worth projection (G20 line 5).**
  `forward-view` counts in cents and writes cents only: the G17 dollar twins
  beside its months' `_minor` fields are gone, and every money param and
  datum is `<name>_minor` (`spend_minor`, `spend_median_minor`,
  `other_outflow_observed.total_minor`, `starting_cash_minor`, a scenario's
  `ending_cash_minor`, a rate's `value_minor`, a trip part's `cost_minor`).
  Its trip and one-off parts are dicts now (`{name, days, cost_minor}`,
  `{label, amount_minor}`), since a list holds no field name. The plan's
  dollars turn to cents once, as the plan is read
  (`forward_view.params_from_plan`); the window's rates are cents sums,
  medians and means of the rows' `amount_sgd_minor`; the Ordinary Wage
  ceiling is `cpf_rules.OW_CEILING` (`cpf_ow_ceiling_minor`); a CPF share is
  its rate (a fraction) times the month's liable cents, the Ordinary Wage up
  to the ceiling and the capped bonus together, rounded half up once; and
  nothing compounds, since each month's cash is the month before plus its
  net. Against the float run it replaced (kept in
  `server/tests/test_projection_cents.py` as the reference), a month's figures agree
  to the cent wherever no CPF share rounds, a CPF share is at most a cent
  away (the float run rounded the Ordinary Wage's share and the bonus's apart,
  by Python's `round`), and the cash after n months is at most n cents away:
  exactly the months' CPF differences carried down. A note in `params.notes`
  is keyed by its parameter's name without `_minor`, since it is text. The
  Net worth projection (`server/analytics/growth.py`, `income.py`, `tax_rules.py`)
  already counted in cents (G14); its growth and interest round to the cent
  once a month, half up as `to_cents`: the brokerage's monthly return on the
  month's opening value, and each CPF account's monthly interest, which
  accrues as whole cents and is credited in December (`cpf_rules.accrue`).
  Income tax brackets, reliefs and caps, and the MediSave floor, bands and
  limits, are cents constants; their rates stay fractions.
- **The export** writes a stat's rows as they are, and beside each
  `<name>_minor` column with no `<name>` of its own, that money in dollars
  under `<name>`, as the record's columns go out.
- **The guard** (`server/tests/test_cents_contract.py`): every stat runs on the
  invented fixture (`MOVED` is all fifteen), and a walk over every result
  fails on any number under a money name that is not whole cents under
  `_minor`. A number is money unless its key says it is a count, a day, a
  month, a share, a ratio or a setting (`NOT_MONEY`). The plain numbers a
  stat keeps in their own units are named per stat with a reason
  (`EXCEPTIONS`), and each holds only where its reason does: benchmarks-sg's
  `owner`, `benchmark` and `value` on a row whose unit is not SGD (months of
  cash, a per cent of income; an SGD figure is `_minor`), and portfolio's
  `cost_price` and `close_price`, prices per share. A dollar planted in a
  result (a float under a money name, a whole dollar, a float under
  `_minor`, a figure on an SGD row of benchmarks-sg) fails it. The same file
  holds Home, Net worth, the projection, the hub page's rows and the export
  to the stats' own cents, and holds an old dollar result in the cache to no
  result; `apps/web/src/stats/money.test.tsx` does the same for the stat cards.

### The chart hint

`chart` is the stat module's `CHART` hint, copied into the result JSON. The app
draws it in [../../apps/web/src/stats/hint.ts](../../apps/web/src/stats/hint.ts) (what to plot) and
`StatChart.tsx` (how):

```json
"chart": {
  "type": "bar",
  "x": "month",
  "series": ["income_minor", "spend_minor", "net_minor"],
  "source": "months",
  "unit": "sgd_minor",
  "units": {"runway_months": "months"}
}
```

- **`type`** one of `bar`, `stacked`, `line`, `scatter`, `table`, `versus`.
- **`source`** the key inside `data` holding an array of row objects (a dotted
  path also works). It is authoritative: a source that names an empty list is an
  empty card ("Nothing to show yet"), never a reason to plot some other list.
  Only when `source` is absent, or names something that is not a list of rows,
  does the app try `data.rows`, `data.months`, `data.items`, `data.weeks`, then
  the first non-empty list of objects.
- **`x`** the field on each row used as the label; **`series`** the numeric
  fields to plot. Rows are objects, never parallel arrays.
- **`unit`** formats the axis and the tooltips: `sgd_minor` (G20: integer
  cents, the unit of a `<name>_minor` field and the only money unit),
  `count`, `percent`, `fraction`, `months`, `days`, `number` (the default).
  In a table or a hover card a column named `*_minor` is `sgd_minor` whatever
  else its name says, its label without the suffix ("cash_minor" reads
  "Cash"), and no other column is read as money from its name. `sgd`, the
  unit of dollars before G20, is gone (G20 line 6).
  **`units`** overrides it per
  series, and a series whose unit differs from the card's goes on a second y
  axis. That is how `savings-runway` puts cash and runway months on one chart.
- **`label`** (scatter only) the field used for the point's tooltip.
- **`horizontal`** forces or forbids a horizontal bar chart. Left out, a
  single-series bar chart with more than ten rows turns itself horizontal.
- **`unit_field`** (default `unit`) lets each row declare its own unit. When the
  rows do not all agree, the ones in the card's unit are plotted and the rest are
  named in a note under the chart, because a 6 next to a 4,322 is an invisible
  bar. They are still in the table. A row's unit may be prose: "SGD per
  household per month" reduces to money, "months of expenses" to `months`. Money
  keeps its period, so "SGD per year" is a different unit from "SGD per month".
  The prose says what a row measures, not how its number is held: a money
  figure is cents under `<name>_minor`, and a bare number is written plain.
- **`links`** (G13) says, for each plotted series that adds up rows, the
  Transactions filters that list exactly those rows (and, G26, for a row field
  that adds up rows and that a dashboard widget plots though the stat's own
  chart does not, such as `monthly-cashflow`'s `investing_minor`): `{"income_minor": {"month":
  "{x}", "income": "1"}, ...}`, where `{x}` stands for the bar's `x` field and
  `{name}` for any other field of the same row (`eating-out` sends a week from
  `{x}` to `{end}`, its Sunday). A
  series with no entry is an average, an estimate or a balance, which no list
  of rows can equal, so its bar does not link and its hover does not invite a
  click. `common.result(..., links=)` writes it into the result's `chart`; the
  app only fills in `{x}` and `{name}` (`barLink` in `hint.ts`). The filters are the ones
  `GET /api/transactions` takes (SPEC.md, Transactions), and
  `server/tests/test_stat_links.py` holds each bar's figure to the rows its link lists.
- **`versus`** (G13) lists each row as its own line rather than plotting the
  rows on one axis: `x` is the row's name, the first `series` field the
  owner's figure and the second the typical one, `unit_field` the row's unit,
  so months, a percentage and money each sit on their own scale. **`order`**
  lists values of **`order_field`** (default `x`) in the order the rows are
  shown; rows it leaves out follow in the order they came. A row may carry
  `thin` (shown greyed, "not billed here", with `thin_reason` on hover) and
  `concern` (the owner's figure in the `danger` colour). The app never works
  either out from a finding's wording. `benchmarks-sg` is the one user.

**Percentages have two scales and the name says which.** `percent` is a number
already multiplied by 100 (`gain_pct` 18.7 is 18.7%). `fraction` is a ratio the
app multiplies by 100 (`share` 0.3 is 30%, `change_pct` 0.25 is 25%). The app
never guesses from the size of a value, so a 0.45% share and a 45% share both
read right. In a table, a column named `*_pct` or `*percent*` is `percent`
(except `change_pct`, which `category-breakdown` writes as a ratio), and any
other column with `share` in its name is a `fraction`. A new stat that writes a
percentage under another name declares it in `units`.

Nothing is ever skipped in silence. A type the app does not know, a `source`
that resolves to nothing, a series that is not in the rows, or a hint that is
missing altogether all fall back to a data table with a line saying why.

## Stats to build (first set; the stats agent may add more, each with a docstring)

| id | question |
| --- | --- |
| monthly-cashflow | income, spend, net, investing per month; phase averages (one per `[[analytics.phases]]` entry, e.g. a stay abroad and then home, or one `Whole record` phase when none is set) |
| category-breakdown | spend by category per month and per phase, share of total, trend (last 3 months vs prior 6); each category carries its bucket (`category_group`) |
| needs-wants-savings | split of income into Need / Want / Savings+Investment / Transfer per month vs 50/30/20 |
| recurring-detector | series by payee (or bank text) and amount level, several per payee, with regular spacing (weekly, fortnightly, twice a month, monthly, every two months, quarterly, half-yearly, yearly), including ones not flagged Recurring; each with an `id`, a `name` and a `status` (active, paused or stopped); total monthly fixed cost. Data: `series`, `active`, `paused`, `stopped`, `possible_auto_bills`, `totals` {monthly_fixed_cost_minor, monthly_committed_minor, active, paused, stopped}, `flag_mismatches` |
| eating-out | count and spend per week, average ticket, weekday vs weekend, top 15 places, vs groceries |
| merchant-concentration | top payees by spend and by count, Pareto (how many payees make 80%) |
| one-offs | External rows above N times the category's median, listed with context |
| income-stability | income by source per month, volatility, months with no salary |
| savings-runway | cash across accounts per month end (from period closing balances, SGD), months of expenses covered, trend |
| portfolio | what is held at Tiger Brokers per annual statement, converted at that statement's own period-end rate: contributed against value, the latest holdings, each deposit matched back to a bank row, and cash plus investments as a net worth |
| cpf | what the yearly CPF statements hold in OA, SA and MA, what the member, the employer, the Government and the interest each put in, year by year and over the whole record, and cash plus investments plus CPF as a net worth with the locked share of it |
| travel | per trip: days, spend, spend per day, by category |
| benchmarks-sg | the owner's numbers vs Singapore references from benchmarks.json with the source for each, preferring a one-person household and then the owner's age band when `[analytics] age_band` is set |
| forward-view | projection from the last complete month to the plan's end month (twelve months on without one): the plan's income rows read by `server/analytics/income.py`, the same reading as the Net worth projection (employment and side income, an open-ended row running to the end month, bonuses in their month; CPF employee share deducted on CPF rows, employer share accrued beside it; no pay without a plan), spending at the window's median, the other outflow, the plan's investing steps (or the old single transfer), insurance premium param; month-by-month cash, its lowest month (`data.low_point`) and the months under zero (`data.below_zero`) |
| data-quality | rows without category/payee, synthetic rows, unconverted currency, coverage gaps; the "fix on an ad hoc basis" list sorted by amount |

## Benchmarks JSON (`server/analytics/benchmarks.json`)

```json
{ "generated_at": "...", "items": [
  { "id": "hes-2023-age-25-34-monthly-expenditure", "value": 4322, "unit": "SGD per household per month",
    "population": "households headed by 25 to 34 year olds", "as_of": "2023", "source": "SingStat Household Expenditure Survey 2023", "url": "...", "notes": "..." } ] }
```
Every item must carry a URL the owner can open and an as-of date. Prefer official
sources (SingStat, MOM, CPF, MoneySense, MAS) over blogs; blogs only for young-adult
survey numbers, clearly labelled.

The file in the repo is the generic set. References picked for one person (their
own university's graduate survey, say) go in a `benchmarks.json` in the data
folder, which `stats.load_benchmarks()` reads instead of the repo's whenever it
exists.

Either file holds dollars, as its sources publish them, and is never rewritten.
G20: `stats.load_benchmarks()` turns each item whose unit starts with SGD into
cents once, as it reads the file (`benchmarks_sg.in_cents`: `value` becomes
`value_minor`, half up as `to_cents`); an item in months, a percent or a count
keeps `value`. The stat passes whatever it is handed through the same
function, which leaves an item already in cents alone. So in `benchmarks-sg`'s
result a metric has `value_minor` (SGD a month) or `value`, a comparison
`owner_minor` and `benchmark_minor` or `owner` and `benchmark`, and `unit` says
what either measures; the card reads `<name>_minor` as cents whatever the unit
string says (`apps/web/src/stats/VersusList.tsx`). The matching (decision 16) reads
units and ids, never the size of a value, so it matches and rejects exactly
what it did in dollars, and a ratio is cents over cents.

## Settings (`[analytics]` in the data folder's config.toml)

Kopifund ships to other users, so nothing true of one person's life is a
constant in these modules (G10). It is a setting, read through
`common.settings()`. A missing file, table or key, or a value of the wrong
shape, falls back to the neutral default and never stops a stat. The values
below are invented examples.

```toml
[analytics]
tiger_folder = "statements/tiger"    # see the snapshot section
cpf_folder = "statements/cpf"
repayment_payee = "Sam Lee"          # rows to this payee are repayments, not a standing cost
repayment_category = "Person to person"   # optional: only rows in this category count
long_stay_days = 45                  # a trip longer than this is somewhere lived in
age_band = "30-34"                   # spelled as the reference ids spell it: hes-2023-age-30-34-...
phases = [                           # named stretches of time, oldest first; `to` optional
  { name = "Working in Penang", from = "2023-03", to = "2023-11" },
  { name = "Back home", from = "2023-12" },
]
income_scenarios = [                 # what-if extra monthly income, priced beside the plan
  { label = "a weekend tutoring job", monthly = 400.0 },
]
```

| Key | Used by | Neutral default |
| --- | --- | --- |
| `phases` | the phase averages in monthly-cashflow and category-breakdown, the partial flag on months before the first phase, the latest phase in benchmarks-sg and forward-view, the Phase column of the hub's Months table | none: the whole record is one phase named `Whole record`, no month is before it, and the Months table's Phase is empty |
| `repayment_payee`, `repayment_category` | forward-view's split of the other outflow into repayments and the rest, and its `with the repayments resuming` scenario | none: nothing is split out and the scenario is not offered |
| `long_stay_days` | travel's long stays and forward-view's short-trip rate | 60 |
| `age_band` | which age-band reference benchmarks-sg prefers, and the derived age-band food total | none: no reference is preferred for its age band |
| `income_scenarios` | one forward-view scenario each, named `with <label> at <monthly> a month`, the monthly figure in dollars here and written as `label` writes it (`S$420.50`) | none |

The plan itself (pay segments, end month, investing transfer, trips, one-offs)
is not a setting here: it lives in kopifund.db, or `[plan]` before the first
import (decisions 9 and 20).

## Decisions taken while building

Nineteen things the contract above did not settle, decided in the code and
written down here so the app and the next reader can rely on them. Ten to
eighteen came out of an adversarial review of the shipped numbers, and
[../tests/test_analytics_review.py](../tests/test_analytics_review.py) holds each
of them against a recomputation from the raw snapshot.

| # | Decision |
| --- | --- |
| 1 | The result JSON carries the module's `CHART` hint verbatim under a top-level `chart` key, beside `sources` and `params`. The runner copies it; the app never imports a stat module |
| 2 | `~/.kopifund/cache/` sits outside the repo. One snapshot of the live databases is 3.5 MB, so a daily one is not "small" |
| 3 | The snapshot's `meta` carries a `notes` list, used by a `--from-csv` build to say what it cannot know (no trips, no `Budgets.Planned`). `meta.file` is stamped when a snapshot is read, not stored in the file, so two builds of the same data stay byte-identical |
| 4 | `synthetic` is derived from `bank_txn_type = "YouTrip Unprinted"`. There is no Synthetic checkbox in Notion, and the same string is in the parser CSVs |
| 5 | The recurring detector's monthly band is 26 to 35 days, not 28 to 33. Months are 28 to 31 days long and a bill moves to the next working day; the tighter band misses a real monthly rent whose gaps ran anywhere from 27 to 33 days. Quarterly is 80 to 100 and yearly 340 to 390 for the same reason. The claim once made here, that a payee visited by habit still fails the 60 per cent fit, was wrong: a hawker stall visited four times in a month passed it and was reported as a commitment. Decision 14 is what keeps habit out |
| 6 | A month is **partial** when an account that has already produced a statement printed none that month, or when the month is before the first of the user's `[[analytics.phases]]`, which they start where the deposit-account statements begin (with no phases set, no month is). Averages leave partial months out; the phase figures do not, so a phase always keeps every one of its months |
| 7 | The `CHART` hint carries four fields the contract above does not name: `source` (which array inside `data` to plot), `unit` and `units` (how to format an axis, and which series belongs on a second one), and `label` (the field a scatter point shows on hover). A row may also declare its own `unit`, as prose or as a token. "The chart hint" below is the full list, and the app falls back to a data table for anything it cannot draw, so a hint it does not understand is never a blank card |
| 8 | Every run keeps its dated file, so an older run stays on disk and `Cache.files_for(stat_id)` lists them newest first. Only a file name that matches `<stat-id>-<date>.json` for that same stat is ever read, and a path that leaves the results directory is a `NotFound`. The app shows the current run only |
| 9 | `forward-view` takes a `params` dict as an optional third argument. Its defaults (`forward_view.DEFAULTS`) are neutral, so everything one person's comes from their plan: no pay, no investing transfer, an end month twelve months past the start, CPF employee 20 per cent capped at the 8,000 Ordinary Wage ceiling, insurance 0, and spending at the last six covered months' **median** (see 11). Every finding is built from the plan values present: the month with no income is a gap between pay segments read off the projection, and the investing-paused scenario and its finding appear only when there is a transfer to pause |
| 10 | A share of spending states its base. `common.spend_base(snapshot, rows, months)` returns `(total, label)` and every stat that prints a share puts the label in the sentence: "23 per cent of covered-month spending 30,000.00" or "of all-month spending 40,000.00". G20: a stat in cents takes `common.spend_base_minor`, the total in cents and its label printed by `label` ("of covered-month spending S$30,000.00"), and `share_note_minor` for the caveat. `category-breakdown` and `travel` use the covered base, `one-offs`, `merchant-concentration` and `data-quality` the all-month one, because their numerators span every month. A share that lands outside 0 to 1, which a refund inside the base can do, adds the `common.share_note` sentence to the caveats and a clamped twin of the field for a chart to draw; the raw value stays in the table |
| 11 | `forward-view` spends at the window's **median**, with the mean kept in `params.spend_mean_minor` and offered as a scenario. A window month whose refunds beat `params.refund_limit_minor` (50000 cents) enters the window at its gross charges instead of its signed total: `params.spend_window` names the months taken as they stand, `spend_window_restated` the ones restated and why, `spend_window_all` the ordered set the median is taken over, and `data.spend_by_month` shows every month's two figures. The window is not filtered: a genuinely cheap month is data, not an artefact |
| 12 | `forward-view` also subtracts `params.other_outflow_minor`, the window's mean of External, non-synthetic, negative rows whose bucket is empty or a Transfer other than Investments, which was projected as zero. The repayments to `[analytics] repayment_payee`, narrowed to `repayment_category` when that is set, are split out of it: `params.repayments_minor` defaults to 0 because they are not a standing cost, the rest stays in `other_outflow_minor`, and `data.other_outflow_observed` keeps both. It starts from `savings-runway`'s `as_of_complete` rather than the last month in the record |
| 13 | `savings-runway`'s cash line stops at `as_of_complete`, the last month every account that had started printed a usable balance. The months after it are kept under `data.provisional` with `carried` and `carried_amount_minor` naming what is stale, and the first finding says so. Balances carry per account **and currency**, so a wallet that printed only its ringgit block keeps its dollars; months before an account's first block are listed in `not_started` |
| 14 | The recurring detector asks each cadence for its own occurrence count, weekly wanting six over at least eight weeks; it tightens the amount band from a quarter to a twentieth where the dominant category is Eating out, Coffee, Groceries or Transport, and skips the amount-varies retry there entirely; it drops `need_class = Transfer` rows; the floor is S$10.00 (`MIN_AMOUNT_MINOR`, 1000 cents); `monthly_cost_minor` and the bucket come from the median and the dominant bucket. A gap longer than three times the band's upper bound cuts the dates into runs and the longest run is the series |
| 15 | `travel`'s `summary.days` is the union of the trip date ranges, because side trips can be nested inside a long stay abroad; the sum is kept as `trip_days_summed`. `summary.excluding_long_stays` drops trips over `[analytics] long_stay_days` (60 by default) and leads the findings, and `by_category_excluding_rent` is the split without the rent paid during a long stay. A trip's rows are its spending rows that carry an SGD value, the same rows the Trips page's table and the Spending explorer count (`server/finance/spending.py`, `SPEND_WHERE`), so the table and the stat agree on every trip; a row with no SGD value is named in a caveat |
| 16 | `benchmarks-sg` compares like for like or not at all. A reference whose unit says gross is never held against a figure that reached the bank, `savings_rate` wants the identical unit rather than a similar one, an age-band household row goes once a one-person one has matched the same metric, and a component never outranks its total (`component_of`, plus the derived `hes-2023-1person-food-total` of S$577.50, 57750 cents since G20). Every rejection is listed in `data.rejected` with its reason, and each comparison carries `basis` on both sides |
| 17 | `eating-out`'s weekly series runs across every calendar week between the first meal and the last, zeroes included, so a fortnight away does not close up. `per_week` is still over the weeks that had a meal, with `weeks_with_a_meal` and `per_calendar_week` beside it. `analyse --only` with an id that is not a stat stops before the snapshot is built and exits 3 |
| 18 | When several of the six window months were spent on a long trip, the default rate is a travelling one. `params.spend_ex_all_trips_minor`, the home rate, is the median of the last six months spent mostly at home (fewer than half their days on a trip, listed in `params.spend_home_window`) with every trip row and every Travel booking removed, and `spend_ex_short_trips_minor` keeps stays over `long_stay_days` in, since living somewhere is still living; `spend_phase_rate_minor` is the latest phase's own average, the same figure monthly-cashflow prints. All three are scenarios and `data.spend_rates` lists them, but none of them is the default: the trips happened, and no trip is booked for the projected months only because nothing has told this record about one. The first finding is a three-point range and names the months that were abroad |
| 19 | `portfolio` reads statements rather than rows. The value is the statement's own closing valuation on its last day, converted at the SGD rate that statement printed (`sgd_per_base` is one over it), never at today's rate; contributions accumulate across statements because nothing has been withdrawn; a statement whose whole period another statement of the same account covers (a year-to-date export beside the full year, which the record keeps from an earlier load, or one period exported twice) is left out and named in a finding, the rest are read in order of their start and then their end so the latest is the one that ends last, and a deposit dated inside an earlier statement's period counts with that statement only, so two statements that partly overlap never count a deposit twice (going by the period rather than by date and amount keeps two real deposits of one sum on one day); the cash half of `net_worth_sgd_minor` is `savings-runway`'s own summary rather than a second reading of the balances, so the two cards can never disagree. A deposit is `matched` when a Transactions row to the Tiger Brokers payee sits within three days of it for the same SGD amount, to within a cent (G20: the old float test of 0.01 dollars, in cents), and one bank row can only pay for one deposit; a deposit older than the first month the Transactions cover is `null`, because the rows that would decide it were never loaded. A latest statement more than four months behind the snapshot adds a finding asking for the next export |
| 20 | `forward-view` subtracts `[[plan.one_offs]]` (month, amount, label) in the month named: a tuition bill, a deposit, a purchase that is neither the base rate nor a trip. `data.months[].one_offs_minor` and `one_off_parts` carry them, `params.one_offs_total_minor` sums them, and the scenario `without the one-off payments` shows the line without them |
| 21 | The Notion Metrics table is the one place a headline figure is computed: `server/analytics/publish.py`'s `metrics_from()`. The app's overview (`server/finance/api.py`) calls the same function over the cached latest results rather than restating any of its own, so the app's headline tiles and the hub page can never disagree |
| 22 | `cpf` reads the yearly statements rather than rows, and CPF is mandated savings rather than a liability: 20 per cent of gross is deducted from pay, the employer adds 17 per cent on top, and the money sits locked in the member's own OA, SA and MA. The balance is the one printed on 31 December of the newest statement, so the as-of date is in every sentence that uses it. The `government` figure is the Government's support plus the interest it credited, because a 2020-onwards statement prints those in one column and a 2016 to 2019 one prints the interest outside it; interest is also reported alone and the two are never added. `employee` and `employer` are null, not zero, in the years whose layout prints only a combined employment figure. `cash_sgd_minor` and `investments_sgd_minor` come from the `portfolio` stat's own summary, so no card can disagree with another, and with no brokerage statement loaded the stat states no net worth at all rather than calling cash plus CPF the whole of it. A latest statement more than fifteen months behind the snapshot adds a finding asking for the next download |
| 23 | `forward-view` carries the employer's CPF share beside the employee's: `params.cpf_employer_rate` (0.17, `[plan] cpf_employer_rate`) on the same liable gross and the same Ordinary Wage ceiling, `data.months[].cpf_employer_minor`, `cpf_total_minor` and the running `cpf_accrued_minor`, and `params.cpf_accrued_total_minor`. It is paid on top of gross rather than out of it, so it changes no cash figure anywhere in the projection: the cash line is identical with the rate set to zero, and the finding says the accrual is savings the cash line never shows |
| 24 | Facts about one user's life are settings, never constants (G10): the phases, the repayment payee and category, the long-stay limit, the age band and the extra income scenarios are `[analytics]` keys (see Settings), and `forward-view`'s title names the plan's own end month. With none of them set every stat still runs and says nothing about anybody: one whole-record phase, no repayment split, no age preference, no pay and no scenarios beyond the plan's own |
| 25 | Every path checks the balance before it uses a row. A `--from-csv` snapshot puts the CSVs through `sync.check_blocks` (and the row-without-block check) first and raises `UncheckedStatements` on any problem, which `analyse` turns into exit 1 with nothing written; a CSV snapshot is never reused from the cache, so the folder given is checked on every run. `cpf` holds only years whose opening plus transactions is the closing: `parse_cpf.parse_folder` leaves the rest out and `cpf.rejected` lists them as `{year, file, off}`, `finance.py load` stores the rejected years in `cpf_rejected` (replaced on every load), a SQLite snapshot names them and leaves out any older figures stored for those years, it rechecks the years the record stored, and the `cpf` stat's first finding names each rejected year. An account with no subclass of its own reads as `Wallet` when the config's folders mark it as one (`snapshot.wallet_account_nos`: a `wallet`, `multi_currency` or per-currency folder, the YouTrip, Revolut or Wise parser, or a DBS PayLah folder), in a CSV and a SQLite snapshot alike, so coverage never takes an idle wallet for a missing statement. `savings-runway` and `common.coverage` walk `common.balance_month_range`, which runs from the first to the last month with a transaction or a block that prints a balance, so the runway reaches an account whose statements end after the last transaction and a wallet export's own export month. `common.month_range` stays the months money moved in: a month with balances and no rows would otherwise count as a month of zero spending in every phase average, and `savings-runway` takes its two spend rates over those months only, so a month its balance walk reaches through a balance alone lowers neither rate. Every cache file is written to a temp file in the same folder and swapped in with `os.replace`, so a reader never sees half of one. The hub page's Metrics are published from every cached result with this run's on top, and each row's Order is its fixed place in `publish.METRIC_ORDER`, so neither `--only` nor a result file gone from the cache renumbers a row onto another's Order |
| 26 | `forward-view` carries its low point as data: `data.low_point` is `{month, cash_minor}`, the as-stated line's lowest month and its cash in integer cents (the earliest month on a tie, `null` when nothing is projected), and `data.below_zero` lists the as-stated months whose cash is under zero. The Plan page marks the point on its chart and shades below zero from these fields; the finding that names the low point says the same thing in words. G17: each month in `data.months` carries every money figure in integer cents under its name plus `_minor` (`cash_minor`, `net_minor`, `take_home_minor`, ...), and the Plan page draws those; G20 line 5: the stat counts in those cents and its dollar twins are gone. A result from before G17 has no cents and shows as no result until the stats run again |
| 27 | No stat prints a full account number (G13). `common.account_labels` names every account a stat shows, in a finding, a table cell or a column name: the `label` `server/finance/snapshot.py` stamps on each snapshot account from `server/finance/account_names.py` (the record's name with any number taken out, else the upload picker's name for the statement folder that names its number, else the institution; with the masked number after it where two accounts would share a name or one has only "Account"), or the same worked out from the record alone for a snapshot without it. A period block is shown as that label, its currency and its month. `savings-runway`'s `accounts[].account_no` is masked to its last four digits ("•• 4821"). Naming changes no figure: the accounts are still keyed by their numbers inside every stat |
| 28 | `benchmarks-sg` carries the card's flags as data (G13). Each metric has a `short` name for the card, `worse_when` (`below` for `cash_months` alone: fewer months of cash than the reference is a thinner buffer; no spending line is called better or worse), and `thin` with `thin_reason` when it is a monthly average of spending rows (`ROW_AVERAGES`) taken over fewer rows than it has covered months, which is what was billed to these accounts rather than what the thing cost. Each comparison copies `short`, `thin` and `thin_reason`, and sets `concern` when its direction is the metric's `worse_when` and it is not thin. The chart hint is `versus` over `headline`; `headline_money` is still written. No figure changed |
| 29 | G14: the Net worth projection and `forward-view` read one set of inputs. Pay is the plan's `income` rows (`server/finance/planning.py`, `income_rows`: a segment from before G14 reads as employment, its note as its name), expanded month by month in integer cents by `server/analytics/income.py`, and the investing transfer is the plan's `investing` steps (`investing_steps`: an old single `dca` is one step from the start). `forward-view` keeps its own flat 20 and 17 per cent CPF shares and has no tax, since it is a cash plan for the months ahead; the Net worth projection uses the age-banded shares (`cpf_rules.employee_rate`), estimates income tax (`tax_rules.py`) and self-employed MediSave, and sets both aside in each month in proportion to that month's income, the start year's months before the cash balance taken in the first projected month. A side income row with CPF no pays no CPF in either. `params.salary_growth_pct` comes from the plan's saved settings, so an open-ended employment row grows the same way in both, and `server/tests/test_projection.py` checks that the same plan gives each month the same gross and investing in both, `server/tests/test_projection_cents.py` the same cents on random plans (G20 line 5) |
| 30 | G13: a bar links to exactly the rows its figure adds up (`chart.links`, "The chart hint"). `monthly-cashflow`: income is the `income=1` rows of the month, spend the `spend=1` rows, net both together, and investing (G26, not drawn) the `transfer=1` rows with `need=Investment` (and `hide_transfers=0`). `needs-wants-savings`: income (G26, not drawn) is the `income=1` rows; needs and wants are `spend=1` rows with `need=Need` or `need=Want`, savings the `transfer=1` rows with `need=Investment,Savings` (and `hide_transfers=0`). `income-stability`: salary is the `income=1` rows in the salary categories as the record names them now (`categories=`), other the rest (`not_categories=`). `merchant-concentration` and `travel`: a payee's or a trip's `spend=1` rows. `savings-runway` (G26): a month's spending is its `spend=1` rows, while cash (a balance) and runway (a ratio) have no link. `data-quality`: uncategorised is the External rows with no category (`external=1`, `category=(none)`), synthetic every made-up row (`synthetic=1`, `hide_transfers=0`). `category-breakdown` (a per-month average), `recurring-detector` (a typical charge scaled to a month) and `travel`'s rate a day have no link. `merchant-concentration` names a payee as the explorer does, the payee the rules gave, else the statement's text, else its counterparty, else "(no name)", so a row known only by its counterparty is no longer lumped into "(no name)" |
| 31 | G16: the recurring detector splits a group's charges into amount levels, densest first on a log scale, and levels whose charges together keep to one cadence (a month or longer, or no shorter than either alone), with no expected payment of the joined schedule answered by both, are one series at changing prices (a rise, a drop, a return to an earlier price, an alternation), so a price change is not a stop and one payee can still hold several series billed side by side. Charges with no payee group by their bank text without digits, never by amount alone. The usual amount is the median of the last six charges at the latest level; `amount_varies` is their spread against the band, whatever path found the series. Payments are held against the occurrence the schedule expected, so one late payment costs one gap. The S$10 floor applies on every path, waived with the occurrence count for half-yearly and yearly (two) where the rules mark the payee recurring. Paused is 1.5 to 3 cadences of silence (never under a month), stopped beyond (never under two months), measured to the last day the series' own account's statements cover. Where one buys in, only the densest level and the four older cadences are tried, which keeps a ride or a stall at one price every few months out. G20: the engine works in cents and finds the series it found in dollars (`server/tests/test_recurring_cents.py` holds it to the dollar engine's answers over every input the detector and Recurring tests give it): levels and band edges are read on logs of the amount in dollars (`_log`), so a charge exactly at a band's edge (S$10.00 and S$12.50 at a quarter) falls the side it always did; the usual amount is `median_cents`, so a median of two ending in half a cent rounds half up where the float rounded it either way, a cent at most, and the floor is held against that same figure; `monthly_cost_minor` is the usual amount times 3,044 over the cadence's nominal days in hundredths, half up (`monthly_cost_minor`), and a payee's several series are named with the amount by `label`. server/finance/recurring.py reads the cents (`usual_cents`), and an owner's usual amount (the record's `series_override.amount_minor`, cents since G16) replaces the series' `median_amount_minor` and `avg_amount_minor` and sets its `monthly_cost_minor` by the same rule |
