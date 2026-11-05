# Runbook

The operating manual for the monthly statement sync: the run step by step, every
command and flag, every problem code with the fix the tool prints, and how to add
a rule, a category, an account, a card or a currency. The short version is in
[../README.md](../README.md), the Notion side in [schema.md](schema.md), and how
each statement layout is read in [parsing.md](parsing.md).

## The run in detail

### 1. Drop the PDFs in

Statements live in the data folder (`~/.kopifund`, or `KOPIFUND_DATA`), one
folder per account under `statements/` and one folder per year inside it. DBS,
POSB, PayLah!, the debit cards and YouTrip take one PDF per month; Revolut and
Wise take an export, and only the newest export in a folder is read. The parsers
read the layout out of the page header rather than the filename, so a file in
the wrong folder is caught, but the month in the name is what the period is
called. `python3 server/finance.py migrate --from DIR` copies an old `data/` folder
into this layout; it never moves or changes the originals.

| Folder | Statement | File name |
| --- | --- | --- |
| `statements/dbs-multiplier/<year>/` | DBS consolidated | the calendar month it covers, `2026-03.pdf` |
| `statements/posb-everyday/<year>/` | POSB deposit account | same; a multi-month statement is named for its range (`2025-01_2025-02.pdf`) |
| `statements/dbs-paylah/<year>/` | PayLah! wallet | same |
| `statements/dbs-debit-cards/<year>/` | debit card | the month **before** the printed "As at" date, because card statements close on the 9th |
| `statements/youtrip-<ccy>/<year>/` | YouTrip wallet, one folder per currency (`youtrip-sgd`, `youtrip-usd`, ...) | the calendar month, `2026-03.pdf` |
| `statements/revolut/<year>/` | Revolut wallets, every currency in one file | one "Export all accounts", "All time" PDF named for the day it was exported, `export-2026-03-14.pdf` |
| `statements/wise-<ccy>/<year>/` | Wise balance, one folder per currency | one "Statements" PDF named for the day it was exported, `export-2026-03-14.pdf` |
| `statements/tiger/<year>/` | Tiger Brokers annual export | the months it covers, `2025-01_2025-12.pdf` for a full year and `2026-01_2026-08.pdf` for a year still running |
| `statements/cpf/<year>/` | CPF yearly statement of account | the year it covers, `2025.pdf` |

Every PDF under a configured folder must produce rows or a block. One that
produces neither is a `pdf-not-parsed` problem, never a silent skip. Two cases
are exempt: a card statement whose months an account statement already covers is
dropped whole and counted on the report's `parsed` line, and a PDF under a parser
that exited non-zero is not judged until that parser runs clean.

The last two rows of the table are not part of the monthly run. See
[Once a year](#once-a-year-tiger-brokers-and-cpf).

### 2. Dry run

```bash
python3 server/finance.py sync --dry-run
```

This runs the whole pipeline except the writes: all four statement parsers over
the whole tree, the rules, one read of Notion, the plan, every doctor check and a
dry rollup. It is safe to run as often as you like. The last line is the size of
the run:

```text
dry run: N write(s) would be made (N upsert, N rollup).
```

### 3. Read the report

The report has one section per step of the run.

| Section | What it tells you |
| --- | --- |
| parsed | rows, blocks and statements the parsers produced, how many rows matched a rule, and how many card statements were skipped as duplicates |
| doctor | every problem found, each with its own `fix:` line. Anything here means nothing is written |
| to add / added | new blocks and, per block, the number of new rows |
| to change / changed | which properties differ on how many existing rows |
| unchanged | rows and blocks Notion already matches. A quiet month is all unchanged |
| orphans | keys and Period pages Notion holds that the parser no longer produces |
| budgets refreshed | Budgets rows created and Actuals refreshed, plus account coverage and recurring payees |
| trips assigned | rows put on or taken off a trip, then rows per trip, biggest first |
| needs a rule | unmatched External rows grouped by the text they print, biggest money first |
| coverage per account | first and last month, blocks, rows, and any month with no statement |
| verify | only after a real write: what Notion looks like read back |

### 4. Fix and add rules

Work the doctor list top to bottom using the table below, then take the "needs a
rule" queue as far down as is worth naming. Both are cheap to redo: go back to
step 2 as often as you want.

### 5. Write

```bash
python3 server/finance.py sync
```

`sync` refuses to write while doctor has a problem. Otherwise it creates the
missing period blocks and patches the blocks that differ, then creates the
missing transactions and patches every row whose loader-owned property differs.
It then runs the rollup, so every row it has just created carries its Trip and is
counted in its month's budget, and only then reads Notion back and verifies it.
A month with nothing new issues zero writes.

Independent calls (the rows of one phase, the quarter shards of a Transactions
read) run on a small thread pool, but the pace is shared: about three requests a
second in total, whatever the thread count, with retries on rate limits. The pool
only removes the per-request latency, so a run is bounded by Notion's limit
(about 1,000 writes in six minutes). Every write is one page, so an interrupted
run leaves a state the next run's diff finishes. `pace` and `workers` under
`[notion]` in `config.toml` tune it; `workers = 1` is the old sequential
behaviour.

Last of all, a run that verified clean writes today's analytics snapshot into
`~/.kopifund/cache/`. Verify has just read Transactions, Period balances, Categories
and Trips back, after every write the run makes, so the snapshot is built from
those pages plus a read of the four small databases the run does not hold
(Payees, Accounts, Cards, Budgets). The line to look for is:

```text
snapshot: wrote snapshot-<today>.json (N transactions), analyse will reuse it
```

That is the file `make analyse` reuses, so the analytics pass after a sync no
longer pulls the eight databases a second time. Nothing about it can fail the
run: the writes are already made and the snapshot goes to disk, so a failure
prints `snapshot: not written (...)` and leaves the exit code alone. A
`--dry-run` writes none, and neither does a run whose verify found problems;
`make analyse` builds one itself when it finds none.

### 6. Exit code

| Code | Meaning |
| --- | --- |
| 0 | clean |
| 1 | doctor found problems, nothing was written |
| 2 | verify found a difference. On `sync` that means something was written first; `verify` on its own writes nothing |
| 3 | environment, argument or API failure (no config, no token, `budget` with no `--seed`, `analyse --only` with an unknown stat id, 404, network) |

## Once a year: Tiger Brokers and CPF

Both of these are read straight off disk while the analytics snapshot is built.
Nothing is synced and nothing is written to Notion by that step, so they are not
part of the monthly run. Both are read while the snapshot is built, so if today's
snapshot already exists when the export goes in, run `make refresh` rather than
`make analyse`, which would reuse it.

**Tiger Brokers, once a year and once more whenever the year's export is
refreshed.** Tiger Brokers only exports whole years.

1. Download the statement and save it as
   `statements/tiger/<year>/<year>-<first month>_<year>-<last month>.pdf`,
   for example `2026-01_2026-08.pdf`.
2. Run `make analyse`.
3. The portfolio card and the two hub rows, `Investments, market value` and
   `Net worth, cash plus investments`, pick it up on the next pass.

Keep the older statement when the year is reissued: save the new one beside it
under its own range (`2026-01_2026-12.pdf` beside `2026-01_2026-08.pdf`). Of two
statements that start in the same month, only the longer range is read, so the
older file is kept and simply ignored, and the portfolio stat leaves out any
statement another one covers whole, even one the record stored from an earlier load. Never overwrite or delete a statement. The portfolio stat says when
the newest statement is more than four months behind the snapshot.

**CPF, once a year, in January.** CPF publishes the year's statement of account
early in the following year.

1. Download it from the CPF website and save it as
   `statements/cpf/<year>/<year>.pdf`.
2. Run `make analyse`.
3. The `CPF savings` card and the two hub rows, `CPF, all three accounts` and
   `Net worth, including CPF`, pick it up on the next pass.

CPF is mandated savings rather than a liability, so a missing year understates
what the owner has, and the stat says so itself once the newest statement is more
than fifteen months behind the snapshot.

## Every command

Each `make` target is walked through step by step, table before and after,
in [make-commands.md](make-commands.md).

Every subcommand of `finance.py` is below. `python3 server/finance.py --help` prints the
same list and every flag.

| Command | Does |
| --- | --- |
| `python3 server/finance.py sync` | the monthly run above: parse, apply the rules, check, write `kopifund.db`, then write Notion, roll up, verify, then write today's analytics snapshot for `analyse` to reuse. Takes `--dry-run`, `--force` and `--archive-orphans` |
| `python3 server/finance.py load` | parse, apply the rules, check, and write `kopifund.db`; no Notion at all. Refuses on any problem `doctor` would report. Takes `--dry-run`. This is the whole monthly run without Notion |
| `python3 server/finance.py import-notion` | read the fields you typed in Notion (categories, budgets, trips, accounts, cards) and the config's `[plan]` into `kopifund.db`. Once, when you start using the record: the app owns those fields from then on, and running it again replaces what the app has edited |
| `python3 server/finance.py doctor` | the checks and nothing else, no report |
| `python3 server/finance.py report` | the full run report, nothing written |
| `python3 server/finance.py status` | an alias for `report`, the same command under a second name |
| `python3 server/finance.py verify` | read Notion back and compare it with the parsers, property by property |
| `python3 server/finance.py rollup` | the analytics pass on its own: Trips, Budgets Actual, account coverage, Payees.Recurring, then the same snapshot `sync` writes. Takes `--dry-run` |
| `python3 server/finance.py payees` | rebuild the Payees rows from the rules. Takes `--dry-run` and `--prune`, which trashes rows no rule names and no transaction links to, the leftovers a rename creates. A row that still has transactions is listed but never pruned |
| `python3 server/finance.py add-options` | add the select options Notion does not have: the values this run's statements carry, plus the values a parser can produce that no statement has produced yet (`reachable_options()` in [../server/finance/model.py](../server/finance/model.py), from each layout's `new_types`). Takes `--dry-run` |
| `python3 server/finance.py budget --seed YYYY-MM` | create that month's Budgets rows ahead of time, Planned set to the median of the last six covered months; never overwrites a Planned that is set. Takes `--dry-run`, `--window N` and `--all` (Savings and Investment too). `make budget MONTH=` and `make budget-plan MONTH=` |
| `python3 server/finance.py analyse` | build or reuse today's snapshot and run every stat into the cache, then rewrite the hub page's Metrics and Months tables when config names them (`--no-publish` skips that, `--dry-run` shows what it would write). Takes `--from-csv DIR` (build the snapshot from parser CSVs rather than `kopifund.db` or Notion; without the flag it reads `kopifund.db` when the data folder has one, else Notion), `--refresh` (rebuild today's snapshot even if it exists, which is what you want when Notion changed after the sync wrote it), `--only ID ...` (run just those stats; an id that is no stat exits 3) and `--out DIR` (write the cache somewhere other than `~/.kopifund/cache`) |
| `python3 server/finance.py split-transport` | once, after Transport was split: point your personal rules that say Transport at Transport (public) or Transport (private), classified from their pattern and payee, move the Transport rows the way the next load would file them, and print what moved and what it left in Transport for you to decide. `--dry-run` writes nothing; without it the record is copied to `backups/` first, and the rules change is one write the Rules page can undo. Running it again moves nothing more |
| `python3 server/finance.py sample <pdf>` | G21: write the statement's text, as the parsers read it (`pdftotext -layout`), with names, street addresses, postcodes, NRIC and FIN numbers, emails, phone numbers and account and card numbers replaced by placeholders of the same length and shape (a digit becomes 0, a letter X or x), for whoever builds a layout for a bank Kopifund does not read. Amounts, dates and the layout stay as printed. Writes `<name>.sample.txt` beside the PDF, or the path `--out` gives, never inside the repository, and prints how many of each kind it replaced. Needs no config, sends nothing anywhere and only reads the PDF. The replacement is best effort: read the file before you share it ([parsing.md](parsing.md), "Trust, GXS and MariBank: no layout yet") |
| `python3 server/finance.py link` | G25: the phone link's setup checklist, against the machine rather than the data folder, so it needs no config. One line each for the tailscale binary, the signed-in tailnet, MagicDNS, HTTPS certs, `tailscale serve` proxying 443 to 127.0.0.1:8770, and the hub's `/api/health`; a missing one prints its fix. Exits nonzero while any is missing (SPEC.md, "Phone link") |

The eight that parse (`sync`, `doctor`, `report`, `status`, `verify`, `rollup`,
`payees`, `add-options`) take `--rules PATH` (a rules file or directory) and
`--csv DIR` (reuse an earlier parser run instead of reparsing). `budget`,
`analyse` and `serve` do not parse and ignore both, `sample` reads only the one
PDF it is given, and `link` checks the machine.

`--report PATH` writes the report to a file as well as to the screen, and only
`sync` and `report` (so `status` too) act on it. The parser accepts the flag on
every command; the rest ignore it.

`--csv` expects `transactions.csv` and `period_balances.csv` in that directory
and the YouTrip pair in a `youtrip/` subdirectory, the Revolut pair in a
`revolut/` subdirectory and the Wise pair in a `wise/` subdirectory beneath it,
plus `skipped.txt`, which is the list of card statements the parsers dropped
whole. A run writes all nine files, so a `--csv` rerun of a directory an earlier
run produced checks exactly what that run checked. `make lint` and `make why`
lay out `~/.kopifund/cache/parsed` the same way, `skipped.txt` included, so
`python3 server/finance.py doctor --csv ~/.kopifund/cache/parsed` comes back clean off their
reparse too. A directory assembled by hand without `skipped.txt` gets one
`pdf-not-parsed` line per skipped card statement.

A parser writes its CSVs before it exits on a failed balance check, so reused
CSVs are never trusted as they stand: `rollup`, `payees`, `add-options` and
`verify` with `--csv`, and `analyse --from-csv`, put them through the same block
checks a sync runs (`block-not-reconciled`, `duplicate-period`, `duplicate-key`,
`balance-unexplained`, `row-without-block`, plus `parser-failed` on a fresh
parse) and exit 1 with nothing written, before Notion is reached, when any
fires. `analyse --from-csv` never
reuses today's snapshot in place of the folder it is given: a CSV snapshot is
rebuilt, and so rechecked, on every run.

Two checks look at the files themselves, before a load writes anything. The
record keeps one row per file content, so the same PDF saved under two names is
`same-file-two-places` (both paths are named; the duplicate month such a copy
also makes is not reported a second time), and a PDF that is now in a
different account's folder than the record last saw it is `statement-moved`
(the fix names where it was). Swapping two statements between account folders
gives one `statement-moved` per file. A rename inside one account folder is not
a problem: the record follows the file. A load that finds either writes nothing.

## When it says X, do Y

Generated from the `Problem` calls in [../server/finance/sync.py](../server/finance/sync.py)
and [../server/finance.py](../server/finance.py). Regenerate it after any change to those
files, and paste the output back here (`make check` runs the second command and fails when the table is stale):

```bash
python3 docs/gen_runbook.py            # the table
python3 docs/gen_runbook.py --check    # exit 1 if this file is out of date
```

A `{name}` in the fix column is filled in at run time with the offending file,
block or count.

| Step | It says | Do this |
| --- | --- | --- |
| parse | `duplicate-period` | two files parse to the same account-month, so the parser stopped before writing anything and nothing else in the run was checked. Move the extra PDF out of statements/ (into documents/superseded/, say) and run again; never delete a statement |
| parse | `no-rows-parsed` | the statement matched no known layout, or is in the wrong folder; open its .txt dump and check, or move the PDF out of the tree |
| parse | `parser-failed` | {script} exited {r.returncode}. Nothing is written until it exits 0 |
| parse | `row-without-block` | the row's account, currency and source match no block in period_balances.csv; rerun the parser over the whole tree |
| parse | `rules-invalid` | the rules did not load: {e} |
| doctor | `balance-unexplained` | {b['source']} prints no closing balance and the block carries no note saying why |
| doctor | `block-not-reconciled` | the balance chain in {b['source']} does not close; the parser output above says where |
| doctor | `duplicate-key` | two parsed rows share a txn_key: {', '.join(sources)} |
| doctor | `duplicate-period` | two statements produced the same period: {', '.join(sources)}. Move the duplicate PDF out of statements/ (into documents/superseded/, say); never delete a statement |
| doctor | `folder-missing` | create the folder or remove its [[folder]] entry from config.toml in the data folder |
| doctor | `implausible-insert` | a parser change can rewrite every txn_key, which would duplicate rows rather than add them. Check a few of the new keys against Notion, then rerun with --force if they really are new |
| doctor | `missing-category` | create the Categories row in Notion (and check the spelling in the rules) |
| doctor | `missing-payee` | run: python3 server/finance.py payees |
| doctor | `orphan-blocks` | no parsed block carries these account-months. Either the statement left the tree or its period name changed; rerun with --archive-orphans once you have checked them |
| doctor | `orphan-rows` | the parser no longer produces these keys. Either the statement left the tree or a key changed; rerun with --archive-orphans once you have checked them |
| doctor | `pdf-not-parsed` | no rows and no block came out of this PDF; check the layout, or move it out of the statement tree |
| doctor | `pdftotext-missing` | brew install poppler (the parsers shell out to pdftotext) |
| doctor | `pinned-key-reused` | the parser now gives a key you saved something on to a different row than the one it was saved for, so this load would move it to the wrong payment. Take what you saved off that key, then load again and save it on the right row (the command line loads past it with --archive-orphans) |
| doctor | `pinned-rows-gone` | this load would archive rows you saved something on: their statement left the tree or the parser now reads them differently. Put the statement back, or take what you saved off the rows, then load again (the command line loads past it with --archive-orphans) |
| doctor | `same-file-two-places` | these paths hold the same file. Move the extra copy out of statements/ (into documents/superseded/, say); never delete a statement |
| doctor | `statement-moved` | the record has this file at {was}, in another account's folder. Move it back to {was} |
| doctor | `unknown-account` | create the Accounts row in Notion and add its page id to [accounts] in the data folder's config.toml |
| doctor | `unknown-card` | create the Cards row in Notion and add its page id to [cards] in the data folder's config.toml |
| doctor | `unknown-option` | add the option first: python3 server/finance.py add-options |
| doctor | `unknown-wallet-folder` | {wallet} is not one of {', '.join(f.per_currency_subfolders)}; add it to per_currency_subfolders in the data folder's config.toml |
| verify | `block-row-count` | Notion holds {a['n']} rows, the statement has {want_n} |
| verify | `block-total` | total {label}: Notion {got}, statement {want} |
| verify | `duplicate-page` | archive the extra pages; a key must name one page |
| verify | `row-without-<account or period>` | every transaction needs its {prop} relation; rerun the sync |
| verify | `verify-differs` | the write did not take; check the API errors above |
| verify | `verify-missing` | rerun the sync; if it repeats, the create call is failing |
| verify | `verify-orphan-blocks` | the account-month is not in the parser output; put the statement back or rerun with --archive-orphans |
| verify | `verify-orphans` | the archive call did not take |
| payees | `missing-category` | create the Categories row in Notion first |

## How to add a rule

Rules live in `rules/`, one file per tier, loaded in filename order. A rule in
the wrong file is a load error, not a silent miss.

| File | `match_on` values | Tested against |
| --- | --- | --- |
| `rules/keys.csv` in the data folder | `key` | `txn_key`, to pin one row: a line that prints no text, or a one-off payment set on its own from the app |
| `rules/types.csv` in the data folder | `type`, `flow` | a bank type only you meet, such as your own school's credits; tried before the shipped ones |
| `data/rules/10-types.csv` | `type`, `flow` | `bank_txn_type`, `flow_class` |
| `rules/people.csv` in the data folder | `counterparty`, `memo`, `reference` | the three free-text fields |
| `rules/merchants.csv` in the data folder | `merchant` | the user's own merchants, tried before the shipped ones |
| `data/rules/30-merchants.csv` | `merchant` | `merchant`, the raw card or QR string |

Columns: `match_on, pattern, payee, kind, country, category, notes, account_no,
recurring`. The last two are optional and the column may be absent. `kind` is
required whenever `payee` is set, and is one of Merchant, Person, Employer,
Institution. `category` must be the exact name of a category the record holds (the Categories page lists them).
`notes` is free text apart from the two lint markers described below.

The four in the data folder name people, pin individual rows, or name the
places and merchants of one person's life, so none of them is in the repo. A
fresh clone has `10-types.csv` and `30-merchants.csv` only, and `--lint` loads
whichever of the six are there without complaining about the rest. A merchant only you
would meet (a campus cafe, a hostel, a shop in a city you lived in) goes in the
data folder's `merchants.csv`; one any Singapore user meets (a chain, a
platform, transit, a government body) goes in `data/rules/30-merchants.csv`.

`pattern` is an `fnmatch` glob, case-insensitive: `*` any run of characters,
`?` one character, `[abc]` a set. Both the pattern and the field are uppercased
and their whitespace collapsed before matching, so `grab*` matches
`GRAB* GPC-...` and `BUS/MRT` is an exact match with nothing after it. The
match is against the whole field, so a bare `GRAB` matches nothing.

Tiers are tried in the order key, type, flow, merchant, counterparty, memo,
reference. Type comes before flow so a wallet's own row types can override its
internal flow class. Within a tier the first matching line wins; the first tier
that matches sets both payee and category, and later tiers are not consulted.
A row whose field is empty skips that tier.

Two things follow from that ordering. A `type` rule for Salary would win over
the memo rules that name the employer, so there is none. And a rule that could
only ever be reached after an earlier tier has already matched is dead; `--lint`
finds those.

Write the rules against the parsed rows:

```bash
make lint                                   # every rule, against every row
make why KEY=a1b2c3d4e5f6                   # why one row got its payee and category
python3 server/finance.py sync --dry-run --csv ~/.kopifund/cache/parsed
```

Both run the configured parsers once, exactly as a run does, into
`~/.kopifund/cache/parsed/` and one merged `~/.kopifund/cache/rules-input.csv`,
so every account's rows are tested against every rule and a provider you do not
use is never run.

`--lint` reports rules that never win, rules lost to an earlier tier, duplicates,
rules shadowed within their own tier, unknown categories, payees whose kind or
country disagree between rules, and patterns matching more than 25 distinct raw
strings. It exits 1 when it finds anything. `--why TXN_KEY` prints the row's
value in every tier, every rule that matches it, and which one wins.

Two of those checks assume something the statements sometimes break, so a rule
can say so in its `notes` column and be left alone. Both markers are a claim
about the data, not an opinion about the rule, so write one only when the
sentence beside it is true.

| Marker | What it asserts | Which check it silences |
| --- | --- | --- |
| `[chain]` | the payee is an international chain, so the `country` on this rule is the country of that branch | payee with more than one country. Every rule for that payee has to carry it, or a typo in one of them would hide behind the marker on another. It never excuses two kinds: a branch has its own country, not its own kind |
| `[ids]` | the merchant string carries a per-transaction id, a ride, a fare or an order | pattern matching more than 25 distinct raw strings, which for such a pattern counts how often the payee was used rather than how broad it is |

Both markers are used only in the merchant tier (`data/rules/30-merchants.csv` and
the data folder's `merchants.csv`), because both are claims about a merchant
string. To see which rules carry one:

```bash
grep -n '\[chain\]\|\[ids\]' data/rules/30-merchants.csv ~/.kopifund/rules/merchants.csv
```

`categorise.py` writes `review.md` beside its output, which is the same "needs a
rule" queue the run report prints. Either is a fine place to work from.

## How to add a thing

| Adding | Do this |
| --- | --- |
| A category | add it to `kopifund.db` (the one-time `import-notion` brings over every category you made in Notion), then use the name in a rule. Rules are checked against the shipped category list plus every category the record holds |
| An account | the first-run Setup page makes a statement folder and its `[[folder]]` entry for each bank or wallet you pick; to add one later, add a `[[folder]]` block to `config.toml` with its path, parser and account number (a new bank also needs a parser for the layout). The account shows on Your profile once its first statement is loaded. `make doctor` (not `make load`) reports `unknown-account` until the account number is a key under `[accounts]` in `config.toml`, even with Notion off; the value is the account's Notion page id |
| A card | nothing in the app: a card appears with its first loaded statement. `make doctor` (not `make load`) reports `unknown-card` until the last four digits are a key under `[cards]` in `config.toml`, even with Notion off; the value is the card's Notion page id |
| A YouTrip currency | create the folder `statements/youtrip-<ccy>/<year>/`, add a `[[folder]]` entry with that path, `parser = "youtrip"` and `wallet = "<CCY>"`, and add it to `WALLETS` and `SYMBOL` in `server/finance/parsers/parse_youtrip.py` if the code or symbol is new |
| A Revolut wallet | nothing. The export holds every wallet and the parser splits it by the `XXX Statement` headings, so a new wallet arrives with the next export. A currency Revolut prints with a symbol rather than a code needs that symbol in `SYMBOL` and `ANY_SYMBOL` in `server/finance/parsers/parse_revolut.py` |
| A Wise balance | create the folder `statements/wise-<ccy>/<year>/` and add a `[[folder]]` entry with that path, `parser = "wise"` and `wallet = "<CCY>"`, on the same account number. The parser reads the currency off the `XXX statement` heading, so a file in the wrong folder is caught. There is no `[cards]` entry to add either: Wise prints no card number |
| A city, so its card rows get a country | a town of your own stays or trips: append a `place,country` line (`SOMETOWN,NOR`) to `places.csv` in the data folder, which YouTrip, Revolut and Wise all read after the shipped table. A capital or major city any user meets: append it to `SHIPPED_CITY_TABLE` in `server/finance/parsers/places.py`. Append rather than insert, because order decides a prefix match, never remove an entry (an old month has to still resolve when it is re-parsed), and run `make doctor` afterwards: a new city can split a merchant string that used to stay whole, which changes that row's txn key |
| A purchase to keep out of the spending rates | add its txn key to `spend_exclude_keys` under `[plan]` in `config.toml`, key first and a note after it on the same line (`make why KEY=` shows keys). Only the key is read; the rest of the line is for you. The forward view then leaves the row out of every rate it reports |
| A life detail the stats should know: a phase (a stay abroad, then home), who you repay, how long a trip runs before it counts as living there, your age band, a what-if extra income | add it under `[analytics]` in `config.toml`. [server/analytics/SPEC.md](../server/analytics/SPEC.md), "Settings", lists every key and what the stats do without it; nothing about one person is written into the code |
| A trip | "Add a trip" on the Trips page, with its dates and countries. Rows are claimed by date plus evidence of being abroad; see [schema.md](schema.md#which-rows-a-trip-claims). The Notion steps are under "The Notion mirror: rollup, budgets and trips" below |
| A budget | type the planned amount for the category and month on the Plan page. It saves when you press Enter or leave the box. Nothing reads a Planned typed in Notion |
| A select option | `python3 server/finance.py add-options`. Notion rejects an unknown select value, and patching a select property replaces its whole definition, so this sends the full option list and the existing description back with the change |

`categorise.py` keeps an offline copy of the category names in
`KNOWN_CATEGORIES` for `--lint`; override it with `--categories FILE`, one name
per line. The sync itself never uses it: `doctor` compares every category the
rules name against the rows in the live Categories database.

## The two flags to leave alone

**`--force`** switches off the insert-size check. That check refuses when the
new rows are more than about three times the rows in the blocks Notion is
missing, because `txn_key` is a hash of parsed content: a change to merchant
splitting or reference detection rewrites keys wholesale, and the sync would
then create a second copy of every affected row rather than add a month. Never
reach for it to get past `implausible-insert`. Check a handful of the new keys
against Notion first, and only use it once you have confirmed they really are
new rows.

**`--archive-orphans`** moves every transaction Notion holds whose key the
parser no longer produces into the Notion trash, and then the Period pages no
parsed block covers, in that order so no row is left pointing at a trashed block.

The same key change that makes `implausible-insert` fire also makes every old row
an orphan, so running both flags together on a bad run would delete the real rows
and insert duplicates. Read the orphan list in the report first. Legitimate
orphans are rare: a statement removed from the tree, or a re-exported YouTrip
month.

Neither flag is part of a normal month.

## Re-exporting a YouTrip statement

The YouTrip PDF export drops rows, so the parser synthesises a `YouTrip
Unprinted` row for every balance jump it cannot explain, dated on the next
printed row and sized by the jump. Those rows are correct in total but they are
not statement lines, and their `txn_key` depends on the jump.

So if you re-export a month and the new PDF prints a row the old one omitted:

1. Move the old PDF into `documents/superseded/` (keep it, never delete a
   statement), put the new one in its place, and rerun
   `python3 server/finance.py sync --dry-run`.
2. The synthetic row shrinks or disappears. Its old key is now an orphan, and
   the real row plus a possibly smaller synthetic row are new.
3. Check the orphan list: it should name only `YouTrip Unprinted` rows in that
   month. If it names ordinary rows, stop, because something else changed.
4. Rerun with `--archive-orphans` to trash the stale synthetic rows.

Nothing else in the month moves. Ordinary YouTrip rows are keyed on their
printed content and survive a re-export unchanged, and the block still
reconciles because the parser rebuilds the chain either way.

## Re-exporting the Revolut statement

Revolut only exports "All time", so each export covers everything the last one
did. Only the newest `export-<date>.pdf` in `statements/revolut/` is read.

1. Put the new export in `statements/revolut/<year>/` as `export-<date>.pdf`.
   The older export can stay; it is ignored from now on.
2. Run `python3 server/finance.py doctor`. It should come back clean.
3. Run `python3 server/finance.py sync`.

Nothing is keyed on the file name. A row's `txn_key` is its printed content and
a block is named `<account no> · <currency> · <yyyy-mm>`, so every month the old
export already covered comes back under the same name and updates in place. The
new rows are the ones dated after the last export.

The block that moves is the last one. Each wallet's coverage runs month by
month to the month the export was generated in, the last block ending on the
export date, so a later export ends that block on a month end and adds one block
per month up to its own date, idle months included. Nothing is orphaned by a
re-export; the old export-month block simply loses its "closes the export" note.

## Re-exporting the Wise statements

Wise exports one PDF per currency balance and every file in a set ends on the
day it was generated. Only the newest `export-<date>.pdf` in each
`statements/wise-<ccy>/` folder is read.

1. Put each new PDF in its balance's folder as `export-<date>.pdf`. The older
   exports can stay; they are ignored from now on.
2. Run `python3 server/finance.py doctor`. It should come back clean.
3. Run `python3 server/finance.py sync`.

Nothing is keyed on the file name here either. A row's `txn_key` carries Wise's
own transaction id, so every row the old set already covered comes back
unchanged, and a block is named `<account no> · <currency> · <yyyy-mm>` the same
way Revolut's is. The block that moves is the last one of each balance, for the
same reason: it ends on the export date, so a later export closes it on a month
end and opens a new one at its own date.

## The Notion mirror: rollup, budgets and trips

The record owns budgets, trips, accounts and cards: you edit them on the Plan,
Trips and Setup pages, and nothing is read back from Notion after the one-time
`import-notion`. A Planned or a trip typed in Notion changes nothing in the app.
This section is for the Notion mirror only, and the commands in it need a Notion
token.

The rollup is the analytics pass. It runs at the end of every `sync` and can be
run on its own, and it derives everything it writes, so a rerun with nothing new
makes zero writes.

```bash
python3 server/finance.py rollup --dry-run     # what it would change
python3 server/finance.py rollup               # change it
```

| It writes | From |
| --- | --- |
| `Trips Expenses.Dates` | the planner rows that `Trip plan` names, when config sets `trip_plans`. A trip with no plan keeps the dates you typed |
| `Transactions.Trip` | the Trips rows: date inside the trip, plus something saying the row was spent away from home |
| `Budgets` | one row per category and month with External rows, created when missing, `Actual` refreshed when it moved |
| `Accounts` Statements from / to and Data coverage | the blocks parsed for each account named in config.toml |
| `Payees.Recurring` | the `recurring` column in the rules |

**Budgets.Planned is yours, in Notion.** It is deliberately absent from the loader's
property table, so no code path can write or clear it. The app's planned amounts
(Plan page) are a separate copy in the record; type it on the row the
rollup created, or create the row yourself named `<Category> · <yyyy-mm>` and
the rollup will fill in Actual on the next run. Actual is signed so it compares
with Planned directly: positive when money went out of a spending category, and
positive when money came into a category whose `Need class` is Income.

A Budgets row whose category and month no longer have any rows is reported and
left alone, never deleted, because it may be a budget typed ahead of the
statements.

**To add a trip:**

1. Make sure the trip exists in the Trips Database with its Dates and Countries.
2. Create the Trips Expenses row and point its `Trip plan` at that planner row
   (several rows for a trip in legs).
3. Run `python3 server/finance.py rollup`. Countries roll up from the planner and the
   rollup copies the Dates across, then the rows are claimed.

A row with no plan keeps the dates you type. Something paid before the trip (a
flight, a booking) goes in `Extra keys` as its txn key (`make why KEY=` shows
keys); the next rollup counts it with the trip.

To change which rows a trip claims, change its dates or its country list and
rerun: a row that no longer belongs to any trip has its `Trip` cleared, so
shortening a trip undoes its assignments. Trips may overlap, and the narrower one
wins for a day both cover, which is what lets a short side trip sit inside a
longer stay abroad.

Rows with no merchant country are never put on a trip. If a trip looks light,
that is usually why, and the fix is a rule or a merchant country, not a wider
trip.

## Reading the data

`python3 server/finance.py report` prints the current row, block and coverage counts.
The parsers print the same per statement, plus the flow class breakdown, when
run on their own. No figure is maintained by hand in this repo.
