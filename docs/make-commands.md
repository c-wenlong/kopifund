# The make commands, step by step

Every target in the [Makefile](../Makefile), each walked through the way the
sync widget does it: the state of the Notion table (or the terminal) before,
the command, and the same table after, with the cells that changed in
backticks. `make` on its own prints the one-line list; this page is the long
form. The example rows are invented; the shapes of the terminal output are the
real ones.

The Notion targets (`plan`, `sync`, `report`, `rollup`, `payees`, `options`,
`budget-plan`, `budget`, `refresh`) are the optional mirror. Without Notion the
monthly run is `make doctor` then `make load`, and budgets, trips and accounts
are edited in the app (the Plan, Trips and Your profile pages), which no Notion
target reads back.

The order below is the order you meet them: the list itself, then the monthly
run, then the maintenance targets you run when something changes, then the rules
and the analytics.

- [make help](#make-help)
- [make install](#make-install)
- [make build](#make-build)
- [make dev](#make-dev)
- [make migrate FROM=](#make-migrate-from)
- [make doctor](#make-doctor)
- [make load](#make-load)
- [make import-notion](#make-import-notion)
- [make plan](#make-plan)
- [make sync](#make-sync)
- [make report](#make-report)
- [make check](#make-check)
- [make test](#make-test)
- [make types](#make-types)
- [make rollup](#make-rollup)
- [make payees](#make-payees)
- [make options](#make-options)
- [make budget-plan](#make-budget-plan)
- [make budget](#make-budget)
- [make backup](#make-backup)
- [make clean-cache](#make-clean-cache)
- [make setup](#make-setup)
- [make lint](#make-lint)
- [make why KEY=](#make-why-key)
- [make analyse](#make-analyse)
- [make refresh](#make-refresh)
- [make stats](#make-stats)
- [make serve](#make-serve)
- [make link](#make-link)
- [make desktop](#make-desktop)
- [make app-icon SET=](#make-app-icon-set)
- [make review](#make-review)
- [make open-items](#make-open-items)
- [make people](#make-people)

## make help

The one-line list of every target, which is also what bare `make` prints because
`help` is the default goal. The recipe is an `awk` over the
[Makefile](../Makefile) itself: a line ending in `## text` becomes a row, and a
comment line starting `## ` becomes the group heading above it, so a target
documented in the Makefile appears here without anything else being edited.

```text
$ make help
  help             list the commands

The app
  install          install the web app's npm packages
  build            build the web app into apps/web/dist
  types            write apps/web/src/types.gen.ts from finance/types.py and serve.py (after changing what an endpoint sends)
  serve            build the app, then serve it and the API at $(URL)
  link             the phone link's checklist (G25): tailscale serve must proxy 443 to 127.0.0.1:$(PORT), and the API up
  desktop          build Kopifund.app, the desktop app (G23), and put it in ~/Applications
  app-icon         write app-icons/ into the web, iOS and desktop apps; SET=iso makes iso the default first
  dev              the app with hot reload on http://localhost:5173 (port $(PORT) sends you there): the API restarts on a Python change, the page on a web change

Checks
  test             Python tests, then the web tests
  check            the tests, the rules lint, the runbook table, the record schema doc, the parsing doc's layouts table, the TypeScript typecheck and the desktop shell's cargo check: run before every push

Setup, once
  setup            install pdftotext and check the token
  migrate          copy a finance-tracking folder into $(DATA): make migrate FROM=<folder>

The monthly run (new PDFs into $(DATA)/statements/<account>/<year>/ first)
  doctor           parse, categorise, run every check, write nothing
  load             parse, check and write kopifund.db (the record); no Notion
  import-notion    read categories, budgets, trips, accounts, cards and [plan] from Notion into kopifund.db
  plan             the full dry run: what sync would add, change, and what needs a rule
  sync             the run with Notion: refuses on a problem, reads back, writes kopifund.db, then Notion
  report           current figures: rows, blocks, coverage, budgets, trips, needs a rule
  rollup           redo trips, budget actuals, coverage and recurring flags (after editing a trip or budget)
  payees           sync the Payees table from the rules (after adding or renaming payees)
  options          add missing select options to Notion (doctor names them)
  budget-plan      show the Budgets rows 'make budget' would seed: make budget-plan MONTH=2026-10
  budget           create next month's Budgets rows, Planned = 6-month median, never overwrites a typed Planned

Rules
  lint             dead, shadowed or over-broad rules against the latest parsed rows (needs the data folder)
  why              explain one row's payee and category: make why KEY=<txn_key>
  _rules-input     the configured parsers' rows into $(CACHE)/rules-input.csv (and $(CACHE)/parsed)

Analytics
  analyse          build today's snapshot from kopifund.db (else Notion) if missing, run all 15 stats into $(CACHE)
  refresh          force a fresh snapshot from Notion, then all stats
  stats            print every stat's findings from the cache, no Notion access

Notes and housekeeping
  review           open the advisor review and the spending profile
  open-items       what is still unexplained, assumed, or waiting on you
  people           the person payees still masked or numeric, biggest first
  backup           archive the record, rules, config and notes into $(DATA)/backups/ (no cache, no media, no secrets)
  clean-cache      delete everything in $(CACHE): it all rebuilds
```

**What it never does.** It runs no parser, no check and no Notion call, and it
writes nothing: the whole recipe is one `awk`. A target with no
`## ` comment on its line is left out. `_rules-input`, the internal recipe
`make lint` and `make why` call to rebuild the parsed CSV, does carry one, so it
appears in the list; you never run it yourself.

## make install

Installs the web app's npm packages from the lockfile. It runs
`cd apps/web && npm ci`. Run it once after a clone, and again when
`apps/web/package-lock.json` changes; `make serve`, `make dev`, `make test` and
`make check` need the packages and do not install them.

**What it never does.** It installs nothing outside `apps/web/node_modules`,
reads no data folder and calls no network service other than the npm registry.

## make build

Builds the web app into `apps/web/dist`. It runs `cd apps/web && npm run build`.
`make serve` and `make desktop` run it first, so call it yourself only to check
that the build passes.

**What it never does.** It touches nothing outside `apps/web/dist`, and it does
not start a server.

## make dev

The app with hot reload, for working on it. It starts
`python3 server/serve.py --reload --port 8770 --page http://localhost:5173` in
the background and `npm run dev -- --port 5173 --strictPort` in `apps/web/` in
the foreground. Open `http://localhost:5173`. Vite reloads the page on a web
change and forwards `/api` to the server; the server restarts on a Python
change and sends any page asked of port 8770 to 5173, so an old `apps/web/dist`
build never shows. Ctrl-C stops both. `PORT=9000 make dev` moves the server.

**What it never does.** It does not build the app, and it reads and writes the
same data folder as `make serve`, so use `KOPIFUND_DATA=<a copy>` when you
experiment.

## make migrate FROM=

Copies a finance-tracking folder into the data folder (`~/.kopifund`, or
`KOPIFUND_DATA`). It runs `python3 server/finance.py migrate --from <folder>`
and stops with a usage line when `FROM` is missing. Statements are renamed to
the new layout and every copy is checked by sha256. Run it once, when you move
over from the old repo.

**What it never does.** It only reads the old folder: nothing there is moved,
changed or deleted.

## make doctor

Parses every statement folder, applies the rules, reads Notion once and runs
every preflight check, writing nothing. It is `python3 server/finance.py doctor`, and
it is the first thing to run after dropping new PDFs in. With Notion off (no
`[data_sources]` in the config) it reads no Notion and runs every other check,
ending "Notion is off, so the mirror checks were skipped."

### Walkthrough

**Step 1 · Before.** The August statement has parsed into four rows; one of them
is a Malaysian merchant and its payee is new.

| Name | Txn key | Category | Merchant country | Notion has |
| --- | --- | --- | --- | --- |
| 2026-08-03 · BUS/MRT · -2.00 | `a1b2c3d4e5f6` | Transport (public) | SGP | nothing yet |
| 2026-08-05 · GRAB · -12.40 | `b2c3d4e5f6a1` | Transport (private) | SGP | nothing yet |
| 2026-08-09 · FAIRPRICE · -38.15 | `c3d4e5f6a1b2` | Groceries | SGP | nothing yet |
| 2026-08-21 · KOPITIAM · -18.60 | `e5f6a1b2c3d4` | Eating out | MYS | nothing yet |

**Step 2 · `make doctor`.** The parsers print every statement they read, then
the checks run. Three problems, each with its own fix line.

```text
$ (cd server && python3 -m finance.parsers.parse_statements statements/dbs-multiplier statements/posb-everyday statements/dbs-paylah statements/dbs-debit-cards --out /var/folders/…/T/finance_sync_ab12cd)
48 PDFs -> 2181 transactions, 50 account-month blocks (2 card statements skipped, fully covered by account statements)
$ (cd server && python3 -m finance.parsers.parse_youtrip ~/.kopifund/statements --out /var/folders/…/T/finance_sync_ab12cd/youtrip)
$ (cd server && python3 -m finance.parsers.parse_revolut statements/revolut --out /var/folders/…/T/finance_sync_ab12cd/revolut)
$ (cd server && python3 -m finance.parsers.parse_wise ~/.kopifund/statements --out /var/folders/…/T/finance_sync_ab12cd/wise)
(each parser's per-block table, then its reconciliation summary, trimmed here)

doctor: 3 problem(s)
  unknown-option         Merchant country = 'MYS'
      fix: add the option first: python3 server/finance.py add-options
  missing-payee          Kopitiam
      fix: run: python3 server/finance.py payees
  pdf-not-parsed         statements/dbs-paylah/2026/8.pdf
      fix: no rows and no block came out of this PDF; check the layout, or move it out of the statement tree
```

**Step 3 · After.** Nothing moved. Doctor read Notion and reported; the parsed
rows and the Notion rows are exactly where they were.

| Name | Txn key | Category | Merchant country | What changed |
| --- | --- | --- | --- | --- |
| 2026-08-03 · BUS/MRT · -2.00 | `a1b2c3d4e5f6` | Transport (public) | SGP | nothing |
| 2026-08-05 · GRAB · -12.40 | `b2c3d4e5f6a1` | Transport (private) | SGP | nothing |
| 2026-08-09 · FAIRPRICE · -38.15 | `c3d4e5f6a1b2` | Groceries | SGP | nothing |
| 2026-08-21 · KOPITIAM · -18.60 | `e5f6a1b2c3d4` | Eating out | MYS | nothing |

**Step 4 · Fix, then run it again.** `make options` adds the MYS option,
`make payees` creates the Kopitiam row, and the PayLah PDF moves out of the
tree or gets its layout fixed. Doctor then says how much work is waiting.

```text
doctor: clean. 4352 rows in 123 blocks; 6 write(s) pending.
```

**What it never does.** It makes no Notion write of any kind, so a wrong answer
here costs a rerun and nothing else. It will not add a select option or create a
payee for you, because both are the sort of thing you want to see before it
happens. Every problem code and its fix text is listed in
[runbook.md](runbook.md).

## make load

The monthly run without Notion. It runs `python3 server/finance.py load`: parse
every statement folder, apply the rules, run the `doctor` checks, and write
`kopifund.db`, the record. It needs no Notion token and no `[data_sources]` in
the config. `--dry-run` goes through the same checks and prints how many rows
from how many statements it would load, then stops.

It refuses, with exit code 1 and nothing written, on the problems `doctor` finds
in the statements, the folders and the balance chains. It skips `doctor`'s
config coverage check (`unknown-account` and `unknown-card`, which want an
account or card key under `[accounts]` or `[cards]` in config.toml), so a load
can succeed where `make doctor` still names those. Run `make doctor` first and
fix what it names. A clean load prints
one line, `kopifund.db: N added, N updated, N archived, N rows on a trip`, and
loading the same statements twice adds nothing. Categories, budgets, trips,
accounts and the plan are never overwritten by a load: the record keeps what you
typed in the app.

**What it never does.** It makes no Notion call and no network call. It reads
the statement PDFs and the rules and writes only `kopifund.db` and the cache in
the data folder.

## make import-notion

Reads the fields you typed in Notion (categories, budgets, trips, accounts,
cards) and the config's `[plan]` into `kopifund.db`. It runs
`python3 server/finance.py import-notion` and needs Notion configured in the
data folder (without it the command prints "nothing to import" and exits 3). Run it
once, when you start using the record: from then on the app owns those fields
and nothing is read back from Notion.

**What it never does.** It writes nothing to Notion, and running it again
replaces what you have since edited in the app with what Notion holds, so do
not run it twice.

## make plan

The full dry run: `python3 server/finance.py sync --dry-run`. It does everything
`make sync` does except the writes, so you can read what would happen before
anything happens.

### Walkthrough

**Step 1 · Before.** Doctor is clean now. Notion is missing the four August
rows, and one July row is sitting there with no category because the rule for it
was only written this month.

| Name | Txn key | Category | Trip | Notion |
| --- | --- | --- | --- | --- |
| 2026-08-03 · BUS/MRT · -2.00 | `a1b2c3d4e5f6` | Transport (public) | | missing |
| 2026-08-05 · GRAB · -12.40 | `b2c3d4e5f6a1` | Transport (private) | | missing |
| 2026-08-09 · FAIRPRICE · -38.15 | `c3d4e5f6a1b2` | Groceries | | missing |
| 2026-08-21 · KOPITIAM · -18.60 | `e5f6a1b2c3d4` | Eating out | | missing |
| 2026-07-14 · SPOTIFY · -11.98 | `d4e5f6a1b2c3` | Software and tools | | no Category |

**Step 2 · `make plan`, first half.** The report names the new block, the rows
under it, and which property differs on how many existing rows.

```text
doctor: clean

to add
  block  POSB ···1234 · SGD · 2026-08  (4 rows)
      4 rows  POSB ···1234 · SGD · 2026-08

to change
      1 rows  Category

unchanged: 4347 transactions, 122 blocks
```

**Step 3 · `make plan`, second half.** The same run ends with the rows no rule
matched and the write count it would have made.

```text
needs a rule
  2 unmatched stem(s), 21 rows; biggest first:
      19 rows      412.60  2025-02-03..2026-05-12  Debit Card Transaction
       2 rows       36.50  2026-08-11..2026-08-19  QR PAYMENT

dry run: 18 write(s) would be made (6 upsert, 12 rollup).
```

"Needs a rule" is the queue for your next rules edit. It lists only External
rows that matched no rule, grouped by whatever text they print (the merchant,
else the counterparty, else the memo, else the bank transaction type), biggest
total money first. A row in that list still syncs, it just lands in Notion with
no payee and no category.

**Step 4 · After.** The table is unchanged in every cell. The plan is a
prediction, and the rollup counts in it are predictions too, because the rollup
also ran with its writes turned off.

| Name | Txn key | Category | Trip | What changed |
| --- | --- | --- | --- | --- |
| 2026-08-03 · BUS/MRT · -2.00 | `a1b2c3d4e5f6` | Transport (public) | | nothing |
| 2026-08-05 · GRAB · -12.40 | `b2c3d4e5f6a1` | Transport (private) | | nothing |
| 2026-08-09 · FAIRPRICE · -38.15 | `c3d4e5f6a1b2` | Groceries | | nothing |
| 2026-08-21 · KOPITIAM · -18.60 | `e5f6a1b2c3d4` | Eating out | | nothing |
| 2026-07-14 · SPOTIFY · -11.98 | `d4e5f6a1b2c3` | Software and tools | | nothing |

**What it never does.** No page is created, patched or archived, and no Budgets
row is touched. The only thing it leaves on disk is a temporary directory of
parser CSVs, which you can hand back to a later run with `--csv DIR`. Run it as
often as you like.

## make sync

The real run: `python3 server/finance.py sync`. It repeats everything `make plan` did,
then writes, rolls up, reads Notion back to check itself and leaves today's
analytics snapshot in `~/.kopifund/cache/` for `make analyse` to reuse.

### Walkthrough

**Step 1 · Before.** Same state as the plan: four August rows parsed but absent
from Notion, and one July row whose category is now known.

| Name | Txn key | Category | Trip | Notion |
| --- | --- | --- | --- | --- |
| 2026-08-03 · BUS/MRT · -2.00 | `a1b2c3d4e5f6` | Transport (public) | | missing |
| 2026-08-05 · GRAB · -12.40 | `b2c3d4e5f6a1` | Transport (private) | | missing |
| 2026-08-09 · FAIRPRICE · -38.15 | `c3d4e5f6a1b2` | Groceries | | missing |
| 2026-08-21 · KOPITIAM · -18.60 | `e5f6a1b2c3d4` | Eating out | | missing |
| 2026-07-14 · SPOTIFY · -11.98 | `d4e5f6a1b2c3` | Software and tools | | no Category |

**Step 2 · The doctor gate.** Doctor runs first every time. If it finds
anything, `sync` prints the report and stops before the first write, and the
table above is untouched.

```text
doctor: 1 problem(s)
  orphan-rows            3 transaction(s): a1b2c3d4e5f6, b2c3d4e5f6a1, c3d4e5f6a1b2
      fix: the parser no longer produces these keys. Either the statement left the tree or a key changed; rerun with --archive-orphans once you have checked them

1 problem(s); nothing written.
```

**Step 3 · The writes.** With doctor clean, the periods go first, then the new
transactions, then the patches. Rows inside one phase are independent and run on
a small thread pool, but the phases stay in that order: a transaction needs its
Period page to exist before it can point at it.

```text
writing: 1 + 4 to create, 0 + 1 to patch
  + period POSB ···1234 · SGD · 2026-08
  + 4/4 transactions
  ~ 1/1 transactions
```

Before any of that, a category the rows name that Notion's `Categories` lacks
gets its row, when the record holds it: a category added on the app's
Categories page, or Transport (public) and Transport (private) after the
split. The run prints `+ category Transport (public)` for each, and
`--dry-run` prints `would create 2 categories in Notion: Transport (private),
Transport (public)` instead. A name the record does not hold stops the run at
`missing-category` in step 2.

| Name | Txn key | Category | Trip | Notion |
| --- | --- | --- | --- | --- |
| 2026-08-03 · BUS/MRT · -2.00 | `a1b2c3d4e5f6` | Transport (public) | | `created` |
| 2026-08-05 · GRAB · -12.40 | `b2c3d4e5f6a1` | Transport (private) | | `created` |
| 2026-08-09 · FAIRPRICE · -38.15 | `c3d4e5f6a1b2` | Groceries | | `created` |
| 2026-08-21 · KOPITIAM · -18.60 | `e5f6a1b2c3d4` | Eating out | | `created` |
| 2026-07-14 · SPOTIFY · -11.98 | `d4e5f6a1b2c3` | `Software and tools` | | `patched` |

**Step 4 · The rollup.** It runs straight after the writes, so a row created a
second ago gets its Trip and counts towards its month's budget. The Malaysian
row falls inside a trip and is claimed. The four Singapore rows change nothing
on themselves; what moves for them is the Actual on their category and month.

```text
  trips: 1 assigned, 0 cleared
  budgets: 3 created, 6 refreshed
  account POSB ···1234: Statements to
  payees: Recurring set on 1, cleared on 0
```

| Name | Txn key | Category | Trip | What changed |
| --- | --- | --- | --- | --- |
| 2026-08-03 · BUS/MRT · -2.00 | `a1b2c3d4e5f6` | Transport (public) | | nothing on the row |
| 2026-08-05 · GRAB · -12.40 | `b2c3d4e5f6a1` | Transport (private) | | nothing on the row |
| 2026-08-09 · FAIRPRICE · -38.15 | `c3d4e5f6a1b2` | Groceries | | nothing on the row |
| 2026-08-21 · KOPITIAM · -18.60 | `e5f6a1b2c3d4` | Eating out | `Kuala Lumpur, Aug 2026` | Trip set |
| 2026-07-14 · SPOTIFY · -11.98 | `d4e5f6a1b2c3` | Software and tools | | nothing on the row |

**Step 5 · Verify.** Notion is read again from scratch and compared with the
parsers property by property, plus the per-block row count and control totals.
Nothing left to write means the run is good.

```text
verify: clean

18 write(s) made.
```

**Step 6 · The snapshot.** Verify has just read Transactions, Period balances,
Categories and Trips back, after the last write of the run. Those four pages
plus a read of the four small databases the run does not hold (Payees, Accounts,
Cards, Budgets) are flattened into today's snapshot, so the `make analyse` after
a sync reuses the file instead of pulling the eight databases a second time.

```text
snapshot: wrote snapshot-2026-09-06.json (4352 transactions), analyse will reuse it
```

| File under `~/.kopifund/cache/` | Holds | What changed |
| --- | --- | --- |
| `snapshot-2026-09-06.json` | the eight databases as they read after the run | `new file` |
| `latest.json` | the snapshot name plus yesterday's result names | `now names the new snapshot` |
| `results/*-2026-09-05.json` | yesterday's stats, left alone | nothing until `make analyse` runs |

It is the last thing the run does and it writes nothing to Notion, so it cannot
turn a good sync into a bad one: a failure prints `snapshot: not written (...)`
and the exit code is whatever the writes and verify earned. A `--dry-run` writes
no snapshot, and neither does a run whose verify found problems.

| Code | Meaning |
| --- | --- |
| 0 | clean |
| 1 | doctor found problems and nothing was written |
| 2 | something was written and verify failed |
| 3 | environment or API failure: no config.toml, no token, network or API error |

**What it never does.** It writes nothing at all while doctor has a problem, so
a bad statement or an unknown card costs a rerun and no repair. It writes nothing
to Notion after verify: the snapshot goes to `~/.kopifund/cache/` and nowhere else. It
never touches `Budgets.Planned`, which is not in the loader's property table at
all. Rows Notion holds that the parser no longer produces are reported and left
alone unless you pass `--archive-orphans`, and a month with nothing new makes
zero writes.

## make report

The current figures with nothing written: `python3 server/finance.py report`. Run it
when you want to know where the data stands rather than what a sync would do.

### Walkthrough

**Step 1 · Before.** The August rows are in Notion and the rollup has run, so
the sync half of the report will be quiet.

| Name | Txn key | Category | Trip | Notion |
| --- | --- | --- | --- | --- |
| 2026-08-03 · BUS/MRT · -2.00 | `a1b2c3d4e5f6` | Transport (public) | | in Notion |
| 2026-08-05 · GRAB · -12.40 | `b2c3d4e5f6a1` | Transport (private) | | in Notion |
| 2026-08-09 · FAIRPRICE · -38.15 | `c3d4e5f6a1b2` | Groceries | | in Notion |
| 2026-08-21 · KOPITIAM · -18.60 | `e5f6a1b2c3d4` | Eating out | Kuala Lumpur, Aug 2026 | in Notion |

**Step 2 · `make report`.** Same report `make plan` prints, without the write
count at the end. The excerpt below skips the doctor, add, change and trips
sections; on a quiet month the coverage lines are the part to read, because they
say how far each account is loaded and which months have no statement.

```text
parsed
  2 card statement(s) fully covered by an account statement and skipped
  4352 transactions in 123 account-month blocks from 119 statements
  rules: /Users/you/.kopifund/rules
  4308 of 4352 rows matched a rule

unchanged: 4352 transactions, 123 blocks

coverage per account
  POSB ···1234         2024-09..2026-08   24 blocks   1010 rows
  Multiplier ···5678   2025-01..2026-07   18 blocks    240 rows, 1 month(s) with no statement
  PayLah               2025-01..2026-08   20 blocks    780 rows
  YouTrip SGD          2024-09..2026-08   36 blocks   1990 rows
  Revolut              2025-06..2026-08   15 blocks    260 rows
  Wise SGD             2025-11..2026-08   10 blocks     72 rows
```

One line per account named under `[accounts]` in
`config.toml`, so the six here are the four parsers'
accounts: two DBS accounts, PayLah, the YouTrip wallet, Revolut and Wise. The
blocks and the rows both add up to the totals printed above them.

**Step 3 · After.** Every cell is where it was. The report is a read.

| Name | Txn key | Category | Trip | What changed |
| --- | --- | --- | --- | --- |
| 2026-08-03 · BUS/MRT · -2.00 | `a1b2c3d4e5f6` | Transport (public) | | nothing |
| 2026-08-05 · GRAB · -12.40 | `b2c3d4e5f6a1` | Transport (private) | | nothing |
| 2026-08-09 · FAIRPRICE · -38.15 | `c3d4e5f6a1b2` | Groceries | | nothing |
| 2026-08-21 · KOPITIAM · -18.60 | `e5f6a1b2c3d4` | Eating out | Kuala Lumpur, Aug 2026 | nothing |

**What it never does.** No page is created, patched or archived, and the rollup
counts it prints come from a dry run. The one file it can write is the copy of
the report itself, and only when you ask with `--report PATH`.

## make check

Everything that runs without a Notion token: the unittest suite under
[tests](../server/tests), about fifty seconds, then the rules lint, then
`python3 docs/gen_runbook.py --check`, which fails when the "When it says X, do Y"
table in [runbook.md](runbook.md) no longer matches the `Problem` calls in
[sync.py](../server/finance/sync.py) and [finance.py](../server/finance.py), then
`python3 -m docs.gen_schema --check`, which fails when section 11 of
[schema.md](schema.md) no longer describes the record's tables as
[db.py](../server/finance/db.py) builds them (G17), then
`python3 -m docs.gen_parsing --check`, which fails when the layouts table in
[parsing.md](parsing.md) no longer matches the layouts the parsers register in
[layouts.py](../server/finance/parsers/layouts.py) (G21), then the
TypeScript typecheck, and last `cargo check` on the desktop shell in
[apps/desktop/src-tauri](../apps/desktop/src-tauri) (G23), skipped with a line saying so
off a Mac or where Rust is not installed (CI's Ubuntu runner has Rust but not
the WebKit libraries a Linux build of the shell needs). Run it after editing a parser, a rule file or
[sync.py](../server/finance/sync.py), and read the lint output for rules that never win,
a payee given two countries, or a pattern matching more than twenty-five
distinct raw strings. A runbook failure says to paste the output of
`python3 docs/gen_runbook.py` back into the table; a schema failure, to run
`python3 -m docs.gen_schema`, which rewrites that section itself; a layouts
failure, to run `python3 -m docs.gen_parsing`, which rewrites that table.

## make test

The unittest suite on its own: `cd server && python3 -m unittest discover tests`,
about fifty seconds. `make check` is this plus `make lint`, the runbook check and the typecheck; reach for
`make test` when you are iterating on one parser and do not want to reparse
every statement for the lint half.

```text
$ make test
cd server && python3 -m unittest discover tests
............................................................................
(one dot per test, plus the odd parser warning a fixture is there to provoke)
----------------------------------------------------------------------
Ran 831 tests in 45.299s

OK
```

**What it never does.** It needs no Notion token and no network, and it writes
nothing outside the temporary directories the tests make for themselves.

## make types

Writes [apps/web/src/types.gen.ts](../apps/web/src/types.gen.ts), the TypeScript types
for what the API sends, from the TypedDicts in
[server/finance/types.py](../server/finance/types.py), together with serve.py's lists of
cached GETs and of writes that change nothing (G17). Run it after changing
what an endpoint answers: edit the handler, edit its TypedDict to match, run
`make types`, and commit the three together. `make check` fails until you do:
[server/tests/test_types.py](../server/tests/test_types.py) compares the committed file
with a fresh generation and runs every endpoint on a small record against
the TypedDicts.

```text
$ make types
cd server && python3 -m finance.gen_types
wrote apps/web/src/types.gen.ts
```

`cd server && python3 -m finance.gen_types --check` writes nothing and exits 1 when the
file is out of date.

**What it never does.** It reads no data folder and no record; the only file
it writes is `apps/web/src/types.gen.ts`.

## make rollup

Redoes the analytics pass on its own, without touching a single transaction
property other than `Trip`. Run it after you add a trip, edit a budget or change
the `recurring` column in the rules; `make sync` already runs it at the end of
every monthly run. It calls `python3 server/finance.py rollup`.

This is a Notion-mirror target: the app's Trips and Plan pages do not read what
it writes. It reads Notion rather than a sync plan, so it is safe against a database this
session has not touched. It reads everything a snapshot needs on the way, so it
finishes by writing today's snapshot exactly as `make sync` does, from the pages
it has just patched. Five things come out of it: `Trips Expenses.Dates`
copied from the planner, `Transactions.Trip` assigned or cleared, one `Budgets`
row per category and month with its `Actual`, `Statements from` / `Statements
to` / `Data coverage` on every account named in
`config.toml`, and `Payees.Recurring`.

### Walkthrough

**Step 1 · Before.** Two trips, both already claimed and costed by an earlier run.

| Name | Trip plan | Dates | Spend | Rows |
| --- | --- | --- | --- | --- |
| Bangkok, May 2026 | Bangkok | 2026-05-02..2026-05-07 | 704.15 | 22 |
| Kuala Lumpur, Jun 2026 | KL weekend | 2026-06-12..2026-06-14 | 188.40 | 9 |

**Step 2 · You add the row in Notion.** Create the `Trips Expenses` row and point
`Trip plan` at the planner row. Leave `Dates` empty: the rollup copies it across.

| Name | Trip plan | Dates | Spend | Rows |
| --- | --- | --- | --- | --- |
| Bangkok, May 2026 | Bangkok | 2026-05-02..2026-05-07 | 704.15 | 22 |
| Kuala Lumpur, Jun 2026 | KL weekend | 2026-06-12..2026-06-14 | 188.40 | 9 |
| `Penang, Sep 2026` | `Penang` | | | `0` |

**Step 3 · `make rollup`.** It reparses the statements, reads Notion, copies the
dates from the planner, then claims the rows that fall inside them.

```text
python3 server/finance.py rollup
  trip dates: 1 copied from the planner
  trips: 14 assigned, 0 cleared
  budgets: 2 created, 3 refreshed

budgets refreshed
  2 row(s) created, 3 Actual(s) refreshed, 366 unchanged
  account coverage: 0 updated, 4 unchanged
  payees recurring: set on 0, cleared on 0, 340 unchanged
trips assigned
  14 row(s) assigned, 0 cleared, 4166 already right

20 write(s) made.
snapshot: wrote snapshot-2026-09-06.json (4180 transactions), analyse will reuse it
```

**Step 4 · After.** `Spend` and `Rows` are Notion rollups over the `Trip`
relation, so they fill in as soon as the relation is set.

| Name | Trip plan | Dates | Spend | Rows | What changed |
| --- | --- | --- | --- | --- | --- |
| Bangkok, May 2026 | Bangkok | 2026-05-02..2026-05-07 | 704.15 | 22 | |
| Kuala Lumpur, Jun 2026 | KL weekend | 2026-06-12..2026-06-14 | 188.40 | 9 | |
| Penang, Sep 2026 | Penang | `2026-09-04..2026-09-08` | `612.40` | `14` | dates copied, 14 rows claimed |

**The three days after the end.** A trip keeps claiming rows for
`SETTLEMENT_DAYS` (3) past its last date, because the dates are travel days from
the planner and a card row posts a day or three after the purchase. The Penang
trip ends on 2026-09-08, so a row dated up to 2026-09-11 is still its own.

**Bookings paid ahead.** Nothing before the start is claimed by date, so a flight
bought on 2026-08-11 stays off the trip. Put its txn key in the trip's `Extra
keys` (comma, space or newline separated) and the next rollup counts it with the
trip whatever its date; `make why KEY=<txn_key>` is where you read the key off.
See section 8 of [schema.md](schema.md) for the full claim rule.

**What it never does.** It never writes `Budgets.Planned`, which is absent from
the loader's property table, and it never deletes a Budgets row whose category
and month have run out of rows; it counts them and leaves `Actual` alone. It
never creates or patches a transaction, only the `Trip` relation on one that is
already there, and an Accounts row with no entry in
`config.toml` is left untouched. Everything it writes is
derived, so a rerun with nothing new makes zero writes. A `--dry-run` writes no
snapshot either, so the cache still holds whatever the last real pass left.

## make payees

Rebuilds the `Payees` rows from the rules: one page per distinct payee name,
created when missing and patched only where a field differs. Run it after you add
or rename a payee in [rules/](../data/rules), or when `make doctor` reports
`missing-payee`. It calls `python3 server/finance.py payees`.

### Walkthrough

**Step 1 · Before.** The payee has no row yet, so a rule naming it would fail
`doctor` at `missing-payee`.

| Name | Kind | Country | Default category | Recurring |
| --- | --- | --- | --- | --- |
| Grab | Merchant | SGP | Transport (private) | no |
| FairPrice | Merchant | SGP | Groceries | no |
| Bus/MRT | Merchant | SGP | Transport (public) | no |

**Step 2 · Add the rule.** One line in
[30-merchants.csv](../data/rules/30-merchants.csv), whose columns are
`match_on,pattern,payee,kind,country,category,notes,account_no,recurring`. The
last two are optional, which is why the line below ends with an empty
`account_no` and `yes` for `recurring`.

```text
merchant,SPOTIFY*,Spotify,Merchant,SWE,Entertainment,monthly subscription,,yes
```

**Step 3 · `make payees`.** It parses the statements first, so it knows how many
rows the new pattern actually matched.

```text
python3 server/finance.py payees
412 payees in the rules, 411 in Notion (0 of them not in the rules)
  create  Spotify: Merchant SWE Entertainment  recurring
created=1  updated=0  unchanged=411
```

The counts on the first line are the rules and Notion as they stand before the
run: the new rule takes the rules to 412 against the 411 rows Notion holds, and
the last line accounts for all 412 as one created and 411 left alone.

**Step 4 · After.** `Default category` is the category carrying most of that
payee's matched rows, ties broken by the larger total and then by the payee's
first rule; a payee with no matched rows keeps what its first rule names.

| Name | Kind | Country | Default category | Recurring |
| --- | --- | --- | --- | --- |
| Grab | Merchant | SGP | Transport (private) | no |
| FairPrice | Merchant | SGP | Groceries | no |
| Bus/MRT | Merchant | SGP | Transport (public) | no |
| `Spotify` | `Merchant` | `SWE` | `Entertainment` | `yes` |

**Step 5 · When the category has no Notion row.** A default category the record
holds but `Categories` lacks gets its row first, as in `make sync` (`+ category
Transport (private)`; `--dry-run` says `would create 1 category in Notion:
...`). A rule naming a category neither the live `Categories` database nor the
record holds stops the command before any write, one line per missing name, and
it exits 1.

```text
python3 server/finance.py payees
  missing-category       Gifts
      fix: create the Categories row in Notion first
```

**Step 6 · Rows a rename left behind.** Renaming a payee in the rules creates a
row under the new name and leaves the old one, so leftovers pile up. The command
counts them, and `--prune` trashes the ones nothing links to.

```text
python3 server/finance.py payees --prune
411 payees in the rules, 414 in Notion (3 of them not in the rules, 3 with no transactions)
  prune  Jane Example: no rule names it and no transaction links to it
  prune  Grabfood: no rule names it and no transaction links to it
  prune  Sheng Siong Supermarket: no rule names it and no transaction links to it
created=0  updated=0  unchanged=411  pruned=3
```

Without `--prune` the same rows are listed as `stale  <name>: no rule names it
and no transaction links to it; --prune trashes it`, and the last line counts
them as `stale=3` rather than `pruned=3`.

A row that still has transactions is never pruned however it is named: rows
pointing at a payee no rule produces mean a rule stopped matching, which is
something to look at in [rules/](../data/rules) rather than a page to throw away.
Notion keeps a trashed page for 30 days.

**What it never does.** It creates no transaction, and no Categories row other
than a missing default category the record holds; it writes nothing at all on
the refusal above. Without `--prune`, a Payees row that
no rule names is counted and left alone, and only the fields that differ are
patched. Edits you make to a Payees row by hand are overwritten on the next run,
so change the rule instead.

## make options

Adds the select options Notion does not have yet. Run it when `make doctor`
reports `unknown-option`, which is what a new currency, a new bank transaction
type or a new merchant country looks like. It calls
`python3 server/finance.py add-options`.

It adds two kinds of value. The first is what this run's statements carry, which
is what the walkthrough below shows. The second is what a parser can produce but
no statement has produced yet, listed by `reachable_options()` in
[model.py](../server/finance/model.py) and today six wallet transaction types
(`YouTrip Refund`, `YouTrip Fee`, `YouTrip Cashback`, `Revolut Other`,
`Wise Card Payment` and `Wise Other`). All six are in Notion already, so the
first YouTrip refund to be printed loads rather than halting the run.

### Walkthrough

**Step 1 · The doctor problem.** A first AUD wallet statement produces a currency
Notion has never seen, on both databases that carry a `Currency` select.

```text
doctor: 2 problem(s)
  unknown-option         Period balances Currency = 'AUD'
      fix: add the option first: python3 server/finance.py add-options
  unknown-option         Currency = 'AUD'
      fix: add the option first: python3 server/finance.py add-options
```

**Step 2 · Before.** The two option lists as Notion holds them.

| Data source | Property | Options |
| --- | --- | --- |
| Period balances | Currency | SGD, SEK, USD, EUR, MYR |
| Transactions | Currency | SGD, SEK, USD, EUR, MYR |

**Step 3 · `make options`.** One line per property, naming what it adds.

```text
python3 server/finance.py add-options
  period_balances.Currency: add AUD
  transactions.Currency: add AUD
2 option(s) added
```

**Step 4 · After.** `make doctor` is clean and `make plan` can build the rows.

| Data source | Property | Options | What changed |
| --- | --- | --- | --- |
| Period balances | Currency | SGD, SEK, USD, EUR, MYR, `AUD` | AUD appended |
| Transactions | Currency | SGD, SEK, USD, EUR, MYR, `AUD` | AUD appended |

**What it never does.** It only appends: no option is renamed, reordered or
removed, and a value already there is skipped. Notion replaces a whole property
definition on a patch, so it sends the existing options and the property's
description back with the change rather than clearing them. It writes to no page,
only to the six select properties listed in `SELECT_CHECKS`.

## make budget-plan

A Notion-mirror target: the app's Plan page is where planned amounts are typed
and nothing here is read by it. Shows the Budgets rows `make budget` would create for a month, and the Planned
number it would put in each one, without writing anything. It runs
`python3 server/finance.py budget --seed <month> --dry-run`, so it reads Notion and
parses no statement. Run it before the month starts, when you want to see the
numbers before they land.

### Walkthrough

**Step 1 · Before.** The Budgets database filtered to Month = 2026-10, at the
end of September.

| Name | Planned | Actual | Variance | Used |
| --- | --- | --- | --- | --- |
| (no rows yet for 2026-10) | | | | |

**Step 2 · `make budget-plan MONTH=2026-10`.** One line per spending category,
alphabetical, then the totals. The excerpt below drops six middle lines.

```text
Budgets for 2026-10 from the median of 2026-03..2026-08
  create  Coffee                   Planned 40
  create  Donations                Planned 20
  create  Eating out               Planned 520
  create  Entertainment            Planned 90
  create  Fees and charges         Planned (empty, median 0)
  create  Fitness                  Planned 60
  create  Groceries                Planned 15
  ...
  create  Transport                Planned 110
  create  Travel                   Planned 75
15 created, 0 filled, 0 kept  (dry run, nothing written)
```

**Step 3 · After.** Unchanged, because a dry run makes no request that writes.

| Name | Planned | Actual | Variance | Used |
| --- | --- | --- | --- | --- |
| (no rows yet for 2026-10) | | | | |

**What it never does.** It writes nothing at all, so it is safe to run as often
as you like. It stops with a usage line when `MONTH` is missing, and exits 3
when the month is not `YYYY-MM`.

## make budget

Creates next month's Budgets rows before there is any spending in them, so
there is a row to type a plan on, and pre-fills Planned with the median of the
last six months. It runs `python3 server/finance.py budget --seed <month>`; add
`--window N` to take the median over a different number of months and `--all`
to seed the Savings and Investment categories too.

The median comes from the `Actual` on the Budgets rows already in Notion. The
window is the last six months before the target month that have any Budgets
row at all, so a month whose statements are not in yet (September, when you
seed October on 4 September) is skipped rather than counted as 0. Inside the
window a category with no row that month counts as 0. With no history at all
the command refuses and tells you to run the sync first.

### Walkthrough

**Step 1 · Before.** The same view as above, still empty.

| Name | Planned | Actual | Variance | Used |
| --- | --- | --- | --- | --- |
| (no rows yet for 2026-10) | | | | |

**Step 2 · `make budget MONTH=2026-10`.** The same lines as the dry run, this
time written. Fifteen categories are seeded, ten with a number and five
whose median is 0.

```text
Budgets for 2026-10 from the median of 2026-03..2026-08
  create  Coffee                   Planned 40
  create  Eating out               Planned 520
  create  Groceries                Planned 15
  ...
  create  Rent                     Planned (empty, median 0)
  create  Transport                Planned 110
15 created, 0 filled, 0 kept
```

**Step 3 · After.** Every row exists, Planned is a starting number and Actual
is 0 until October spending lands.

| Name | Planned | Actual | Variance | Used |
| --- | --- | --- | --- | --- |
| Coffee · 2026-10 | `40` | `0` | 40 | 0 |
| Eating out · 2026-10 | `520` | `0` | 520 | 0 |
| Groceries · 2026-10 | `15` | `0` | 15 | 0 |
| Rent · 2026-10 | | `0` | | |
| Transport · 2026-10 | `110` | `0` | 110 | 0 |
| Travel · 2026-10 | `75` | `0` | 75 | 0 |

**Step 4 · You type over two of them, then rerun.** The seeded number is a
starting point, not a decision: 520 on eating out is what you did, not what you
want, and a median of 15 on groceries says more about eating out than about
groceries.

| Name | Planned | Actual | Variance | Used |
| --- | --- | --- | --- | --- |
| Coffee · 2026-10 | 40 | 0 | 40 | 0 |
| Eating out · 2026-10 | `450` | 0 | `450` | 0 |
| Groceries · 2026-10 | `120` | 0 | `120` | 0 |
| Rent · 2026-10 | | 0 | | |
| Transport · 2026-10 | 110 | 0 | 110 | 0 |
| Travel · 2026-10 | 75 | 0 | 75 | 0 |

```text
Budgets for 2026-10 from the median of 2026-03..2026-08
  keep    Coffee                   Planned 40 already set
  keep    Eating out               Planned 450 already set
  keep    Groceries                Planned 120 already set
  keep    Rent                     Planned empty, median 0
  ...
0 created, 0 filled, 15 kept
```

**Step 5 · Mid-October, after `make sync`.** The rollup fills Actual on the
rows that now have transactions, and Notion computes the rest.

| Name | Planned | Actual | Variance | Used |
| --- | --- | --- | --- | --- |
| Coffee · 2026-10 | 40 | `28` | `12` | `0.70` |
| Eating out · 2026-10 | 450 | `310` | `140` | `0.69` |
| Groceries · 2026-10 | 120 | `54` | `66` | `0.45` |
| Rent · 2026-10 | | 0 | | |
| Transport · 2026-10 | 110 | `66` | `44` | `0.60` |
| Travel · 2026-10 | 75 | 0 | 75 | 0 |

**What it never does.** It never overwrites a Planned that has a number in it,
so a rerun cannot undo your typing, and a row the rollup already made this
month with Planned still empty gets the median instead (a `fill` line). It only
covers categories whose `Need class` is Need or Want, plus Savings and
Investment under `--all`; Income and Transfer are always skipped. This is the
one command that writes Planned at all: `BUDGET_TYPES` in
[model.py](../server/finance/model.py) leaves the property out so that `make sync` and
`make rollup` cannot reach it, and the seed writes it through its own one
property map in [sync.py](../server/finance/sync.py).

## make backup

Archives what lives only in the data folder into
`~/.kopifund/backups/kopifund-<date>-<time>.tar.gz`: `kopifund.db`, `rules/`,
`config.toml`, `SOURCES.yml`, your own `benchmarks.json` and `places.csv` when
you have them, `notes/` and `documents/`. The statement PDFs, the
cache, video files in `notes/` and `.env` are left out. The archive is mode 600.

```text
$ make backup
wrote /Users/you/.kopifund/backups/kopifund-2026-09-04-2130.tar.gz
```

**What it never does.** It reads, never moves or deletes, and every run writes a
new archive. The archive stays on this machine, so keep a copy of it, and of
`statements/`, somewhere else.

## make clean-cache

Deletes everything in the cache folder `~/.kopifund/cache` (`rm -rf` of its
contents): the dated snapshots, `latest.json`, the `results/` directory, the
`parsed/` directory, `rules-input.csv`, and the AI answer cache, the models
cache and the rebuild lock. Everything it removes is rebuilt on demand, by `make analyse`
from Notion and by `make lint` or `make why` from the statement PDFs. Reach for it
when a snapshot has gone stale or a stat is reading numbers you know have moved.

```text
$ make clean-cache
rm -rf "/Users/you/.kopifund/cache"/*
```

**What it never does.** It touches nothing in Notion and nothing outside
`~/.kopifund/cache`: the statement PDFs, the rules, the notes and any
`backups/` folder are all left alone. `make stats` will fail until
something rebuilds the cache, since it reads `latest.json` and nothing else.

## make setup

The one-time install: it puts `pdftotext` on the machine if it is missing, says
where the Notion token goes, and then probes whether Notion is reachable. Run it
on a fresh clone, then once more after the data folder has a `config.toml` and
the token is in the keychain.

### Walkthrough

**Step 1 · A fresh clone.** `pdftotext` gets installed through Homebrew, the token
line is printed for you to act on, and the probe fails because there is no config
and no token yet.

```text
$ make setup
==> Downloading poppler
Token: [notion] token_command in ~/.kopifund/config.toml reads it from the Keychain (or export NOTION_TOKEN)
Run 'make doctor' to see what is missing
```

**Step 2 · You do the manual parts.** The app's first-run setup (or `make
migrate`) writes `~/.kopifund/config.toml`; for the Notion mirror, add the data
source ids to it the way
[config.example.toml](../server/finance/config.example.toml) shows, and store the token
in the keychain under the name `token_command` reads.

```text
$ security add-generic-password -s notion-finances -a "$USER" -w   # prompts for the token
```

**Step 3 · Run it again.** Homebrew is skipped now that `pdftotext` is on `PATH`,
and the probe runs `finance.py doctor` quietly to confirm the token, the config
and the integration's access to the Expenses page.

```text
$ make setup
Token: [notion] token_command in ~/.kopifund/config.toml reads it from the Keychain (or export NOTION_TOKEN)
Notion reachable, doctor clean
```

**What it never does.** It writes no config file and stores no token; it prints
the line and leaves both to you. The probe throws away everything `doctor` printed, so a
failure here tells you only to run `make doctor` and read it properly. Nothing in
this target writes to Notion.

**Where the token comes from.** In order: the environment variable named by
`token_env`, then `.env` in the data folder, then the keychain command in
`token_command`. An exported `NOTION_TOKEN` beats `.env`, which is how you run once against a
different integration.

## make lint

Checks the rules files against the latest parsed rows and reports the ones that
never win, the ones a broader rule already covers, and the patterns that catch
too much. It rebuilds `~/.kopifund/cache/rules-input.csv` from the statement PDFs every
time, running only the parsers your config.toml names, exactly as a run does,
and then runs
`cd server && python3 -m finance.parsers.categorise --lint ~/.kopifund/cache/rules-input.csv`. Every
account's rows are merged into that one file, so every wallet row is tested
against the rules too, and the lint is never reading a stale CSV from an
earlier month.

### Walkthrough

**Step 1 · Before.** Five rules, three of which have a problem.

| File and line | Rule | Payee | Category | Lint says |
| --- | --- | --- | --- | --- |
| keys.csv line 6 | `key:a1b2c3d4e5f6` | Acme Media | Salary | nothing |
| people.csv line 41 | `memo:SALARY PAYMENT` | (none) | Salary | never wins |
| 30-merchants.csv line 3 | `merchant:GRAB*` | Grab | Transport (private) | nothing |
| 30-merchants.csv line 9 | `merchant:GRAB* GPC-*` | GrabPay | Wallet top-up | shadowed |
| 30-merchants.csv line 12 | `merchant:*SINGAPORE*` | FairPrice | Groceries | over-broad |

**Step 2 · `make lint`.** Seven checks run over every rule and every parsed row.
Three of them have something to say here.

```text
$ make lint
cd server && python3 -m finance.parsers.categorise --lint ~/.kopifund/cache/rules-input.csv || true
lint /Users/you/.kopifund/rules
  rules that never win (1)
    people.csv line 41  memo:SALARY PAYMENT -> lost to an earlier tier on all 4 rows it matched
  shadowed within a tier (1)
    30-merchants.csv line 9  merchant:GRAB* GPC-* is covered by the earlier merchant:GRAB* (30-merchants.csv line 3)
  pattern matching more than 25 distinct raw strings (1)
    30-merchants.csv line 12  merchant:*SINGAPORE* matches 604 distinct raw strings
```

The other four checks are duplicate patterns inside one tier, categories that
are not in `KNOWN_CATEGORIES`, the offline copy of the Notion `Categories` names
in [categorise.py](../server/finance/parsers/categorise.py), and payees whose `kind` and whose
`country` disagree between two rules, which are counted separately. A clean run
prints `clean` and nothing else, which is what the rules do today.

Two of the checks can be waved off per rule by a marker in that rule's `notes`
column, because the statements break the assumption the check makes. `[chain]`
says the payee is an international chain, so its `country` is the branch's
rather than the payee's and the country check skips it; a payee is only a chain
when every one of its rules carries the marker. `[ids]` says the merchant string
carries a per-transaction id, which makes the distinct-string count meaningless,
so the over-broad check skips that pattern. The `notes` column is free text
otherwise.

**Step 3 · After.** One rule deleted, one moved up, one narrowed.

| File and line | Rule | Payee | Category | What changed |
| --- | --- | --- | --- | --- |
| keys.csv line 6 | `key:a1b2c3d4e5f6` | Acme Media | Salary | nothing, it was already winning |
| `(deleted)` | `memo:SALARY PAYMENT` | (none) | Salary | the key rule reaches those 4 rows first |
| `30-merchants.csv line 4` | `merchant:GRAB*` | Grab | Transport (private) | still catches every ride row |
| `30-merchants.csv line 2` | `merchant:GRAB* GPC-*` | GrabPay | Wallet top-up | moved above the general rule |
| 30-merchants.csv line 12 | `merchant:*FAIRPRICE*` | FairPrice | Groceries | narrowed from `*SINGAPORE*` |

**Step 4 · `make lint` again.**

```text
$ make lint
cd server && python3 -m finance.parsers.categorise --lint ~/.kopifund/cache/rules-input.csv || true
lint /Users/you/.kopifund/rules
  clean
```

**What it never does.** It edits no rule and reads no Notion database, it only
reads [rules/](../data/rules) and the parsed rows. The recipe ends in `|| true`, so a
finding never fails the target even though
[categorise.py](../server/finance/parsers/categorise.py) exits 1. The reparse it always does
first writes inside `~/.kopifund/cache/` and nowhere else, and it costs the time a full
parse costs, which is why `make why` reuses
`~/.kopifund/cache/rules-input.csv` instead of rebuilding it.

## make why KEY=

Explains one transaction: what each rule tier saw, which rules matched, and which
one set the payee and category. It runs
`cd server && python3 -m finance.parsers.categorise --why <txn_key> ~/.kopifund/cache/rules-input.csv`, and
the key comes from `~/.kopifund/cache/rules-input.csv` or from the
Transactions row in Notion. Unlike `make lint` it reuses that CSV and only
reparses when the file is missing, so run `make lint` first when the statements
have moved on since it was written.

### Walkthrough

**Step 1 · The row.** One line out of the parsed CSV.

| Key | Date | Account | Amount | Bank txn type |
| --- | --- | --- | --- | --- |
| `a1b2c3d4e5f6` | 2026-02-05 | 244-11223-4 | SGD 1,250.00 | FAST Payment / Receipt |

**Step 2 · The seven tiers.** Each tier reads one column. Both the pattern and
the column value are uppercased and their whitespace collapsed before an
`fnmatch` glob is tried, so `grab*` matches `GRAB* GPC-…`. A row whose column is
empty skips that tier.

| # | Tier | Column read | Value seen | Rules that match |
| ---: | --- | --- | --- | ---: |
| 1 | key | `txn_key` | A1B2C3D4E5F6 | 1 |
| 2 | type | `bank_txn_type` | FAST PAYMENT / RECEIPT | 0 |
| 3 | flow | `flow_class` | EXTERNAL | 0 |
| 4 | merchant | `merchant` | (empty, skipped) | 0 |
| 5 | counterparty | `counterparty` | (empty, skipped) | 0 |
| 6 | memo | `memo` | SALARY PAYMENT | 1 |
| 7 | reference | `reference` | PP0000AB12 20260205… | 0 |

The order is fixed: key, type, flow, merchant, counterparty, memo, reference.
Inside a tier the first matching line in file order wins, and the first tier with
any match sets both payee and category. Later tiers are still printed, so you can
see what would have happened.

**Step 3 · `make why KEY=a1b2c3d4e5f6`.**

```text
$ make why KEY=a1b2c3d4e5f6
cd server && python3 -m finance.parsers.categorise --why a1b2c3d4e5f6 ~/.kopifund/cache/rules-input.csv
a1b2c3d4e5f6  2026-02-05  244-11223-4  SGD 1250.0  FAST Payment / Receipt
  key (txn_key) = a1b2c3d4e5f6
  ... type, flow, merchant and counterparty, one line each ...
  memo (memo) = Salary Payment
  reference (reference) = PP0000AB12 20260205…

  key: 1 matching rule(s) for 'A1B2C3D4E5F6'
    keys.csv line 6  key:a1b2c3d4e5f6 -> payee Acme Media, category Salary  <- wins
  memo: 1 matching rule(s) for 'SALARY PAYMENT'
    people.csv line 41  memo:SALARY PAYMENT -> payee (none), category Salary

winner: keys.csv line 6  key:a1b2c3d4e5f6
payee: Acme Media   category: Salary   rule: key:a1b2c3d4e5f6
```

**Step 4 · After.** Two rules matched, the earlier tier took it.

| Tier | Rule | Payee | Category | Outcome |
| --- | --- | --- | --- | --- |
| key | keys.csv line 6 | Acme Media | Salary | `wins, and stops the search` |
| memo | people.csv line 41 | (none) | Salary | `printed, never reached` |

The last line is the answer:
`payee: Acme Media   category: Salary   rule: key:a1b2c3d4e5f6`. Notion stores
only the payee and the category, as relations, so this command is the way back
from a row in Notion to the one rules line that decided it.

**What it never does.** It writes nothing and reads no Notion database, and a key
that is not in the CSV prints `no row with txn_key …` and exits 1. Rules are not
re-ordered or repaired for you: the tier order shown above is fixed in
[categorise.py](../server/finance/parsers/categorise.py), and a rule you want to win has to move to
an earlier tier or an earlier line.

## make analyse

Builds one dated snapshot under `~/.kopifund/cache/`, then runs all 15 stat
modules over it and writes one result JSON each. The snapshot is read from
`kopifund.db` when the data folder has one, and from the eight Notion databases
otherwise. It runs `python3 server/finance.py analyse`. A snapshot of
`kopifund.db` is rebuilt every time, so a load since the last run always shows,
including right after `make sync`. Only the Notion route reuses today's snapshot
when one is already on disk: after a `make sync` there is one, written from the
pages it read, so that pass starts at the stats. The walkthrough below shows the
Notion route.

With Notion configured, the run ends by
rewriting the hub page's `Metrics` and `Months` tables from the results, keyed
on Name, with zero writes when nothing moved, and it only does so when
`config.toml` names those two data sources.
`python3 server/finance.py analyse --no-publish` skips the step and leaves the pass
read-only. The Notion-side layout is in
[schema.md](schema.md#9-the-hub-page-metrics-and-months).

The snapshot also picks up the Tiger Brokers annual exports from
`statements/tiger` (`[analytics] tiger_folder`), parsed off disk rather than
read from Notion, which is where the `portfolio` stat and the hub's
`Investments, market value` and `Net worth, cash plus investments` rows come
from. No export in the folder means no portfolio card and no investment rows,
never a zero. They are read while the snapshot is built, so a new export dropped
in after today's snapshot exists needs `make refresh` rather than `make analyse`.

The CPF yearly statements in `statements/cpf` (`[analytics] cpf_folder`)
are read the same way, off disk on every build, and feed the `cpf` stat and the
hub's `CPF, all three accounts` and `Net worth, including CPF` rows. CPF is
mandated savings rather than a liability, so an empty folder means those cards
and rows are absent, never zero, and the net worth on the page is then cash and
investments only.

### Walkthrough

**Step 1 · Before.** The cache still holds yesterday's pass.

| File under `~/.kopifund/cache/` | Holds | Dated |
| --- | --- | --- |
| `snapshot-2026-09-03.json` | the eight databases flattened, 4,301 transactions | 3 Sep |
| `results/monthly-cashflow-2026-09-03.json` | one stat result | 3 Sep |
| `results/` (14 more files) | one per remaining stat | 3 Sep |
| `latest.json` | the snapshot name plus 15 result file names | 3 Sep |

**Step 2 · `make analyse`.** No snapshot exists for today (no sync has run
today either), so the eight databases are pulled and flattened first, then every
stat runs and prints its first finding.

```text
$ make analyse
python3 server/finance.py analyse
snapshot: building from notion
snapshot: wrote snapshot-2026-09-04.json (4346 transactions)
  monthly-cashflow         Home (2025-01 to 2026-08, 20 months) averages S$4,200.00 in, S$2,650.00 out and S$500.00 invested, leaving S$1,050.00 a month.
  category-breakdown       Eating out is the largest category at SGD 480.00 a month, 18 per cent of covered-month spending 53,000.00.
  ... one line per stat, in the order below ...
  data-quality             41 of 2240 External rows have no category: S$1,500.00 out and S$0.00 in, 2.5 per cent of all-month spending S$60,000.00.

15 stat(s) written to /Users/you/.kopifund/cache
hub page
  metrics: 0 created, 2 updated, 22 unchanged
  months: 0 created, 1 updated, 25 unchanged
```

**Step 3 · After.** Today's files sit beside yesterday's, and `latest.json` now
points at the new ones.

| File under `~/.kopifund/cache/` | Holds | Dated | What changed |
| --- | --- | --- | --- |
| `snapshot-2026-09-03.json` | the 3 Sep pull, left alone | 3 Sep | nothing |
| `snapshot-2026-09-04.json` | the eight databases flattened, 4,346 transactions | 4 Sep | `new file` |
| `results/monthly-cashflow-2026-09-04.json` | id, title, generated_at, chart, sources, params, data, findings, caveats | 4 Sep | `new, and 14 more beside it` |
| `latest.json` | the snapshot name plus 15 result file names | 4 Sep | `rewritten to name the 4 Sep files` |

Every result carries its own `generated_at` in Singapore time and the name of the
snapshot it was computed from, so a number on the page can always be dated.

**Step 4 · A second `make analyse` the same day.** The snapshot is reused, the
stats are recomputed and overwrite today's result files. The first `make analyse`
after a `make sync` prints this same line rather than `building from notion`,
because the sync has already left today's snapshot behind.

```text
$ make analyse
python3 server/finance.py analyse
snapshot: reusing snapshot-2026-09-04.json (4346 transactions, source notion)
  monthly-cashflow         Home (2025-01 to 2026-08, 20 months) averages S$4,200.00 in, S$2,650.00 out and S$500.00 invested, leaving S$1,050.00 a month.
  ... one line per stat ...

15 stat(s) written to /Users/you/.kopifund/cache
```

Reading the results is `make stats` for the terminal, or `make serve` for the
charts.

### The 15 stats

The ids are fixed, the order is the one in
[stats/__init__.py](../server/analytics/stats/__init__.py), and each module's contract is
in [SPEC.md](../server/analytics/SPEC.md).

| Stat id | Title | What it answers |
| --- | --- | --- |
| `monthly-cashflow` | Monthly cash flow | income, spend, net and investing per month, plus an average for each phase (`[analytics] phases` in config.toml; the whole record when none is set) |
| `category-breakdown` | Spend by category | spend per category per month, its share of the total, and the last three months against the six before |
| `needs-wants-savings` | Needs, wants and savings | how each month's income splits into Need, Want and Savings, read against the 50/30/20 rule |
| `recurring-detector` | Recurring payments | which payees repeat at a regular spacing, what they cost a month, and which ones stopped |
| `eating-out` | Eating out | meals per week, average ticket, weekday against weekend, top places, and the size next to groceries |
| `merchant-concentration` | Merchant concentration | who the money goes to, and how few payees make 80 per cent of the spending |
| `one-offs` | One-off charges | single charges far above their own category's median, big enough to move a month on their own |
| `income-stability` | Income stability | income by source per month, how volatile it is, and the months with no salary at all |
| `savings-runway` | Cash and runway | cash across accounts at each month end, and how many months of spending it covers |
| `portfolio` | Investments at Tiger Brokers | what the annual brokerage statements are worth against what was paid in, and cash plus investments as a net worth |
| `cpf` | CPF savings | what the yearly CPF statements hold across OA, SA and MA, who put it there (the member, the employer, the Government and interest), and cash plus investments plus CPF as a net worth |
| `travel` | Trips | days, spend and spend per day for each trip, split by category |
| `benchmarks-sg` | Against Singapore references | the owner's figures beside Singapore reference numbers, each with its source and as-of date |
| `forward-view` | Forward view to the plan's last month | month-by-month cash to the plan's last month from the plan in `kopifund.db` (edited on the Plan page; `[plan]` in config.toml only until `import-notion` copies it over): pay segments, CPF, spending rate, investing transfer, planned trips, one-off payments |
| `data-quality` | Data quality | rows with no category or payee, synthetic rows, unconverted currency, and months with gaps in coverage |

**What it never does.** It writes to no Notion database but one: the eight are only read,
the hub page's `Metrics` and `Months` tables are the only thing it writes back (and only with Notion configured),
and every file it creates is inside `~/.kopifund/cache/`. A stat that raises is
reported as `FAILED` and skipped, so one broken analysis does not cost the other
fourteen. `--only` with an id that is not a stat stops before the snapshot is built
and exits 3, rather than quietly running all of them or none. It never reads the
eight databases while today's snapshot is on disk, so a hand edit in Notion made
after a sync needs `make refresh`.

## make refresh

The same pass as `make analyse`, except it rebuilds today's snapshot from Notion
even when one is already on disk. It runs
`python3 server/finance.py analyse --refresh`, and you want it after a hand edit in
Notion, or a new brokerage or CPF statement, has changed what the cached snapshot
holds. A `make sync` no longer needs it: the sync writes today's snapshot itself,
from Notion as it read back after its own writes.

### Walkthrough

**Step 1 · Before.** This morning's snapshot was built before the August YouTrip
rows went in.

| File under `~/.kopifund/cache/` | `generated_at` | Size of the read |
| --- | --- | --- |
| `snapshot-2026-09-04.json` | 2026-09-04T09:12:03+08:00 | 4,346 transactions |
| `results/monthly-cashflow-2026-09-04.json` | 2026-09-04T09:12:44+08:00 | 2,045 rows used |
| `results/data-quality-2026-09-04.json` | 2026-09-04T09:12:58+08:00 | 2,240 External rows |

**Step 2 · `make refresh`.** The `snapshot: reusing` line never appears.

```text
$ make refresh
python3 server/finance.py analyse --refresh
snapshot: building from notion
snapshot: wrote snapshot-2026-09-04.json (4392 transactions)
  monthly-cashflow         Home (2025-01 to 2026-08, 20 months) averages S$4,200.00 in, S$2,650.00 out and S$500.00 invested, leaving S$1,050.00 a month.
  ... one line per stat ...
  data-quality             38 of 2286 External rows have no category: S$1,380.00 out and S$0.00 in, 2.3 per cent of all-month spending S$60,000.00.

15 stat(s) written to /Users/you/.kopifund/cache
hub page
  metrics: 0 created, 5 updated, 19 unchanged
  months: 0 created, 2 updated, 24 unchanged
```

**Step 3 · After.** Same file names, new timestamps and new contents.

| File under `~/.kopifund/cache/` | `generated_at` | Size of the read |
| --- | --- | --- |
| `snapshot-2026-09-04.json` | `2026-09-04T16:40:11+08:00` | `4,392 transactions` |
| `results/monthly-cashflow-2026-09-04.json` | `2026-09-04T16:40:52+08:00` | `2,091 rows used` |
| `results/data-quality-2026-09-04.json` | `2026-09-04T16:41:06+08:00` | `2,286 External rows` |

**What it never does.** It writes to no Notion database, and it republishes the
hub page's `Metrics` and `Months` tables exactly as `make analyse` does.
Because both the snapshot and the results are named `<thing>-<date>.json`, a
refresh overwrites today's files in place instead of adding a set; earlier dates
are untouched. Deleting the
whole cache is safe, and `make clean-cache` does it.

## make stats

Prints every stat's title, date and findings straight from the cache, with no
Notion access and no browser. The recipe is a `python3 -c` one-liner in the
[Makefile](../Makefile) that reads `~/.kopifund/cache/latest.json` and then each result
file it names.

### Walkthrough

**Step 1 · Before.** `latest.json` names one current file per stat.

| Stat id | Result file it names | `generated_at` |
| --- | --- | --- |
| `monthly-cashflow` | `monthly-cashflow-2026-09-04.json` | 2026-09-04T12:52:49+08:00 |
| `category-breakdown` | `category-breakdown-2026-09-04.json` | 2026-09-04T12:52:51+08:00 |
| … 12 more … | … | … |
| `data-quality` | `data-quality-2026-09-04.json` | 2026-09-04T12:53:14+08:00 |

**Step 2 · `make stats`.** One block per stat, in the order `latest.json` lists
them. The date in each heading is that stat's own `generated_at`, not today.

```text
$ make stats

## Monthly cash flow  (2026-09-04)
- Home (2025-01 to 2026-08, 20 months) averages S$4,200.00 in, S$2,650.00 out and S$500.00 invested, leaving S$1,050.00 a month.
- The worst month was 2025-12 at SGD -1,800.00 net; the best was 2026-03 at 3,900.00.

## Spend by category  (2026-09-04)
- Eating out is the largest category at SGD 480.00 a month, 18 per cent of covered-month spending 53,000.00.

... one block per stat, findings only ...

## Data quality  (2026-09-04)
- 41 of 2240 External rows have no category: S$1,500.00 out and S$0.00 in, 2.5 per cent of all-month spending S$60,000.00.
```

**Step 3 · After.** The table is unchanged, because nothing was written.

| Stat id | Result file it names | `generated_at` |
| --- | --- | --- |
| `monthly-cashflow` | `monthly-cashflow-2026-09-04.json` | 2026-09-04T12:52:49+08:00 |
| `category-breakdown` | `category-breakdown-2026-09-04.json` | 2026-09-04T12:52:51+08:00 |
| … 12 more … | … | … |
| `data-quality` | `data-quality-2026-09-04.json` | 2026-09-04T12:53:14+08:00 |

**What it never does.** It needs no token, no network and no browser, and it
creates nothing. It prints the `findings` list only, so the `data`, `caveats` and
`sources` of each result stay in the JSON. With no `~/.kopifund/cache/latest.json` it
fails outright, so run `make analyse` first.

## make serve

Builds the web app (`npm run build` in [apps/web/](../apps/web)), then serves it and the
JSON API it reads on `http://127.0.0.1:8770`. It runs `cd apps/web && npm run build`
and then `python3 server/serve.py --port 8770`, and stays in the foreground until
Ctrl-C. This is the only viewer: every stat card is a page of the app, read from
the cache.

### Walkthrough

**Step 1 · Before.** The cache is on disk and nothing is listening.

| What | State |
| --- | --- |
| Port 8770 | closed |
| `~/.kopifund/cache/latest.json` | 15 results, all dated 2026-09-04 |
| Notion token | not needed, not read |

**Step 2 · `make serve`.** The build runs first, then the server starts.

```text
$ make serve
cd apps/web && npm run build
python3 server/serve.py --port 8770
```

**Step 3 · After.** The same three things, with the port now open.

| What | State |
| --- | --- |
| Port 8770 | `open on 127.0.0.1 only` |
| `~/.kopifund/cache/latest.json` | `read by /api/results/<id>, one card per stat` |
| Notion token | not needed, not read |

Every result becomes one card, in the order set by `DISPLAY_ORDER` in
[results_cache.py](../server/finance/results_cache.py), so the portfolio card sits between cash and
runway and the CPF one, with the trips after those. The home page's tiles are the
Notion hub page's own headline figures, computed by the same `metrics_from()` in
[server/analytics/publish.py](../server/analytics/publish.py) that writes them there, so the
app never computes a headline number of its own. `PORT=9000 make serve` moves it.

**What it never does.** It binds the loopback interface only, so nothing else on
the network can reach it. A request addressed to any other host name is refused.
The stat cards show whatever the cache already holds and never recompute
anything, so the numbers only move when `make analyse` or `make refresh` runs.

## make link

The phone link's setup checklist (G25): what `tailscale serve` needs before a
phone can reach the hub at all. It runs `python3 server/finance.py link --port $(PORT)`,
which checks the machine rather than the data folder, so it needs no config
and never touches `~/.kopifund` or Notion. `make link PORT=9999` checks a hub
on another port, the same variable `make serve` uses.

```text
$ make link
phone link: what tailscale serve needs to carry the phone to 127.0.0.1:8770

  tailscale        ok        1.104.2 (/usr/local/bin/tailscale)
  signed in        ok        mac.tail1234.ts.net
  magicdns         ok        tail1234.ts.net
  https certs      missing   enable HTTPS certificates in the tailnet's admin console, under DNS, then run `tailscale cert` once
  tailscale serve  missing   run: tailscale serve --bg 8770
  hub              missing   start the server: make serve

3 to fix
```

Each line is `ok` with a detail or `missing` with the fix for that one thing.
The checks run in order: the tailnet lines all fail as "sign in first" while
`tailscale status` says so, and the serve line only counts an entry proxying
HTTPS 443 to `127.0.0.1:8770` (another port's entry, or a stopped `--bg` serve,
is still missing). It exits nonzero while any line is missing, so it works as
a preflight in a script. When every line is ok the phone's HTTPS lands on the
loopback hub and pairing can start: see SPEC.md, "Phone link (G25)".

## make desktop

Builds the web app, then `Kopifund.app` (G23): a Tauri shell in
[apps/desktop/](../apps/desktop) that opens Kopifund in its own window. It runs
`npm ci` and `npm run tauri -- build --bundles app` in `apps/desktop/`, then
replaces `~/Applications/Kopifund.app` with the new build. Open it from
Launchpad or Spotlight and keep it in the Dock.

On launch the app starts `python3 server/serve.py --app` from this checkout through
your login shell, so Homebrew's `python3` and `pdftotext`, and any key your
shell profile exports, are found as in a terminal. It shows a starting page
until the server prints `KOPIFUND_READY`, then loads the app from
`http://127.0.0.1:8771`. Quitting the app closes the server's standard input
and the server stops. If the server stops on its own, the window goes back to
the starting page with its last lines and a Retry button.

**What it never does.** It never copies or moves the data folder: the app
reads and writes the same `~/.kopifund` (or `KOPIFUND_DATA`) as `make serve`,
and the two can run side by side on 8771 and 8770. The page gets no Tauri API;
it talks to the server exactly as it does in a browser.

## make app-icon SET=

Writes the icon sets in [app-icons/](../app-icons) into the web, iOS and
desktop apps, from [app-icons/sets.json](../app-icons/sets.json): the default
is the icon each app opens with, and every set goes where Settings' picker
switches to it. `make app-icon SET=iso` first makes iso the default in
`sets.json`; `make app-icon` alone writes what `sets.json` already says, after
a set or `sets.json` is edited. It runs `npm ci` in `apps/desktop/`, then
[app-icons/apply.py](../app-icons/apply.py):

- the web app: the default's favicons, apple-touch icon, PWA icons and logo
  into `apps/web/public/`, and every set's into `apps/web/public/app-icons/<set>/`
- iOS: the default as `AppIcon`, every set as `AppIcon-<set>` and
  `IconPicker-<set>` in the asset catalog, with a dark and a tinted look for a
  set with a transparent mark, and `apps/ios/Alerts/AppIcons.swift` listing them
- the desktop app: `tauri icon` over the default on macOS's grid for the
  bundle's five icons, its `icon-512.png` on the starting page, and every set's
  Dock icon in `icons/sets/<set>.png`

```text
$ make app-icon SET=sticker
cd apps/desktop && npm ci
python3 app-icons/apply.py sticker
sticker is the app icon on the web app, iOS and the desktop app; sticker, iso, riso are in every picker
```

A rerun changes no file. `make test` fails (`test_app_icon`) when an app's
copy of a set is missing or stale, which is the sign this has not been run.

**What it never does.** It never edits the sets' own files, and it builds
nothing: run `make build`, `make desktop` or the Xcode build afterwards to see
the new icon.

## make review

Runs `open` on
`notes/advisor-review-2026-09.md` and
`notes/spending-profile-2026-09.md`, which
hands them to whatever the Mac has registered for markdown. Both are written by
hand over the stats rather than generated, so nothing regenerates them and the
dated file names in the recipe stay until the next pair is written and the
[Makefile](../Makefile) is pointed at it.

## make open-items

Prints the first 80 lines of `notes/open-items.md`, the
transactions whose source or intent is not settled yet, each with what is known
and what would settle it. It only reads the file, and that file is kept current
by hand when a rule is added or a decision is made, so nothing here re-runs when
the rules change.

## make people

Prints the first 60 lines of `notes/people.md`, every counterparty the
rules treat as a Person that is still a masked name or a bare mobile number,
biggest by money moved first. It only reads the file; the fix is to edit the
`payee` column of the rules line each row names in its last column, never the
`pattern` column, since that is what finds the transactions.
