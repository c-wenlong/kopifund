# Working in this repo

Kopifund is a personal finance tracker for people in Singapore. It reads the PDF
statements from their banks, wallets, brokerage and CPF, turns them into one
categorised ledger, and shows where the money went and how long it lasts. Read
this page before changing anything.

## Read these first

| Document | What it is for |
| --- | --- |
| [README.md](README.md) | What the app is, in the words a stranger reads first |
| [SPEC.md](SPEC.md) | The architecture, the supported statements, the domain model, the money and date rules, the non-goals and the open questions |
| [ROADMAP.md](ROADMAP.md) | Goals G0 onwards. Work on the lowest open goal unless told otherwise |

## Current state

The repo is a monorepo: the apps a user opens live in `apps/`
([web/](apps/web/), [desktop/](apps/desktop/), and `apps/ios/` next), the
Python pipeline and API live in `server/`, and the shipped data files live in
`data/` (today just `data/rules/`). The app icon's three sets live in
`app-icons/` (SPEC.md, "App icon"). The pipeline itself (G3): parsers in
[server/finance/parsers/](server/finance/parsers/), the sync and its checks in
[server/finance/](server/finance/) with [server/finance.py](server/finance.py)
as the command line, the stats in [server/analytics/](server/analytics/), and
the app in [server/serve.py](server/serve.py). Everything personal is in the
data folder (`~/.kopifund`, or `KOPIFUND_DATA`;
[server/finance/paths.py](server/finance/paths.py)), never in the repo.

How the pipeline works, in depth:

| Document | What it is for |
| --- | --- |
| [docs/runbook.md](docs/runbook.md) | The monthly run in detail, one row per problem code, every `finance.py` command |
| [docs/parsing.md](docs/parsing.md) | Statement layouts and what each parser does with them |
| [docs/schema.md](docs/schema.md) | The record's tables (written from the code by `docs/gen_schema.py`) and the Notion databases the mirror writes |
| [docs/make-commands.md](docs/make-commands.md) | Every `make` target, step by step |
| [server/analytics/SPEC.md](server/analytics/SPEC.md) | The stats, their result JSON and the decisions behind them |

## Commands

Needs Python 3.14 or later, Node 22, and `pdftotext` (`brew install poppler`).
The desktop app (`make desktop`, G23) also needs Rust (`rustup`) and Xcode's
command line tools; off a Mac or without Rust, `make check` skips only the shell's `cargo check`.

| Command | What it does |
| --- | --- |
| `make install` | Install the web app's npm packages (`npm ci` in `apps/web/`) |
| `make migrate FROM=<folder>` | Copy a finance-tracking folder into the data folder; the old folder is only read |
| `make doctor` | Parse every statement, apply the rules, run every check; writes nothing. With Notion off it skips only the mirror's checks |
| `make load` | Parse, check and write `kopifund.db`, the record; no Notion. Refuses while doctor has a problem |
| `make import-notion` | Read categories, budgets, trips, accounts, cards and the config's `[plan]` from Notion into `kopifund.db`, once, when the record starts |
| `make sync` | The monthly run with Notion: write `kopifund.db`, then mirror to Notion. Refuses while doctor has a problem |
| `make analyse` | Run the 15 stats into the data folder's cache, read from `kopifund.db` when it exists |
| `make serve` | Build the app, then serve it and the API on http://127.0.0.1:8770 |
| `make link` | The phone link's checklist (G25): tailscale serve must proxy 443 to 127.0.0.1:8770 and the API must be up; prints the fix for anything missing |
| `./build_macos.sh [ref]` | One click: copy one version (`origin/main` unless a branch, tag or commit is named) into `~/Library/Application Support/Kopifund/builds`, build `Kopifund.app` from that copy and install it in `/Applications`, quitting the open one first. The app then keeps serving that version whatever this checkout switches to |
| `make desktop` | Build `Kopifund.app` (G23), a Mac window that starts `serve.py --app` on http://127.0.0.1:8771 and stops it on quit, and put it in `~/Applications`. It runs from this checkout, on the same data folder as `make serve` |
| `make app-icon [SET=<set>]` | Write the icon sets in `app-icons/` into the web, iOS and desktop apps from `app-icons/sets.json`, for each app's default icon and Settings' picker; `SET=iso` makes iso the default first |
| `make dev` | Development on http://localhost:5173: Vite reloads the page on a web change and forwards `/api` to `serve.py --reload`, which restarts on a Python change and sends any page asked of port 8770 to 5173, so an old `apps/web/dist` build never shows. Ctrl-C stops both |
| `make test` | Python tests (`python3 -m unittest discover tests` in `server/`) and web tests (`npm test` in `apps/web/`) |
| `make check` | The tests, the rules lint, the runbook table check, the record schema check (docs/schema.md), the layouts table check (docs/parsing.md), the TypeScript typecheck and `cargo check` on the desktop shell. Run it before every push |
| `make types` | Write `apps/web/src/types.gen.ts` from `server/finance/types.py`: run it after changing what an endpoint sends |

Tests that need real statements (`test_monthly_run`, `test_analytics_snapshot`,
`test_analytics_review`) run only where a data folder exists and skip
everywhere else, CI included. They read the data folder and never write it:
`server/tests/__init__.py` points `KOPIFUND_DB` and `KOPIFUND_CACHE` at a throwaway
folder for the whole run, so no test can touch the real record or cache. Parser tests run on the scrubbed fixtures in
`server/tests/fixtures/statements`.

### A feature is done when its make commands have run clean

Passing tests is not the end. Before calling a change done, run every make
command it can affect, from the branch, and read the output:

| The change touches | Run, in order |
| --- | --- |
| Anything | `make check` (the tests, the rules lint, the runbook check, the schema check, the layouts table check, the typecheck) |
| `apps/web/` | `make build`, then look at the page in `make dev` at desktop width and 375px |
| `apps/desktop/` | `make desktop`, then open the app from `~/Applications` and check it starts, loads Home and stops its server on quit |
| Parsers, rules, `server/finance/` sync or checks | `make doctor`, which must end with no problem |
| `server/analytics/` or anything a stat reads | `make analyse`, so the cache holds results in the new shape (a stat that gains a field shows nothing new until it runs) |
| Upload, load or the record's schema | `make doctor`, then a load on a copy of the data folder |

Where those commands run depends on who runs them:

- In the owner's own session, run them against the real data folder.
  `make analyse` there also updates the Notion hub page; that is expected.
  Copy `kopifund.db` into `backups/` first.
- An agent or subagent never touches `~/.kopifund` or Notion. It runs them
  against a copy of the whole data folder, `kopifund.db` included, with the
  `[notion]` and `[data_sources]` sections removed from its `config.toml`
  (`KOPIFUND_DATA=<copy> make doctor`, which then runs every check but the
  mirror's, with no Notion token needed), and runs
  `python3 server/finance.py analyse --no-publish` instead of `make analyse`. Analyse
  reads `kopifund.db`, and without one it would turn to Notion, so a copy with
  no record gets `KOPIFUND_DATA=<copy> python3 server/finance.py load` first (a load
  needs no Notion).
- `make load`, `make sync` and `make import-notion` write the record or Notion,
  so they run on the real folder only when the owner asks.

Say in the PR body which of these ran and what they printed.

## House rules

- Money is integer cents plus a currency code. A float holding money is a bug.
  The stats count in cents and their results hold cents under `<name>_minor`
  (G20, SPEC.md "Rules for money and dates"); `server/tests/test_cents_contract.py`
  fails on a dollar in any result. Only the parsers read a statement's printed
  figures as numbers, and those become cents as they are loaded.
- A transaction's date is the calendar date its statement prints. Never convert
  it between time zones.
- A statement that does not balance stops the run. Never load rows the balance
  check has not passed.
- Nothing personal goes into a tracked file: no statements, account or card
  numbers, names of people, Notion ids or tokens, pay figures or plans. Real
  data lives in `~/.kopifund` (SPEC.md, "The data folder"), never in the repo.
  Test fixtures use invented or scrubbed rows, never a copy of a real
  statement.
- Never modify or delete a statement PDF, in `~/.kopifund` or in the old
  finance-tracking folder. Imports copy; they do not move.
- Kopifund ships to other users, so nothing about one user's accounts is
  hardcoded. It goes in local config.
- Tag work with its goal number: `G5: render the cash flow card` in commit
  messages, `// G5: ...` in source where the reason is not obvious.
- When behaviour changes, update SPEC.md in the same change. When a goal is
  done, mark it in ROADMAP.md.
- After a PR merges, keep this checkout and the installed app current: bring
  `main` to the merged code (a cherry-pick of the squash commit while `main`
  tracks the older history) and run `./build_macos.sh`, which builds
  `origin/main`.
- Projections describe and estimate. Never write copy that tells the user where
  to put their money.
- Run Python as `python3`. Python is stdlib-only; ask before adding a package.
- UI: follow the layout section of SPEC.md. Sans serif only, never a mono font;
  figures use the `num` class for tabular numerals. Every colour is a token in
  `apps/web/src/index.css` so light and dark stay in step.
- Percentages have two scales. A `fraction` (`share` 0.3) is multiplied by 100
  on display, a `percent` (`gain_pct` 18.7) is not. The unit or the column name
  decides, never the size of the value (server/analytics/SPEC.md, "The chart hint").
- Pin npm versions exactly, to a release at least two weeks old.
- No em dashes in prose, in this repo or in anything written into it.
