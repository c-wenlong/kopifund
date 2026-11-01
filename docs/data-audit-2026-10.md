# Data audit, October 2026

Done before moving the finance-tracking data into `~/.kopifund` and making
SQLite the record (G3, G4). Four read-only passes: the file layout of `data/`,
the data model end to end, the code's dependence on paths, and the owner's
earlier audit notes. No statement contents were read. This page holds structure
and counts only; nothing in it identifies an account or a person.

## What is there today

| Class | Files | Size | Share |
| --- | --- | --- | --- |
| Statement PDFs and other source documents | 175 | 12.9 MB | 30% |
| Daily snapshot JSONs (6) | 6 | 22.9 MB | 53% |
| Stat results, parsed CSVs, pointer | 97 | 4.3 MB | 10% |
| `.txt` text dumps beside each PDF | 164 | 2.5 MB | 6% |
| Fixtures, metadata, `.DS_Store` (22) | 18 | 0.4 MB | 1% |

69% of the folder is derived and can be rebuilt. The six snapshots hold 27,034
transaction records, of which 4,860 are unique.

Outside `data/`: personal rules (`rules/00-keys.csv`, `rules/20-people.csv`),
`notes/` (43 MB, of which 42.9 MB is two videos), `finance/config.toml` (settings,
account and card identifiers, Notion ids and the personal plan in one file) and
`.env` (the Notion token).

## Findings

### Layout

1. **Five naming schemes for one idea.** Months are `YYYY/M.pdf` (not zero
   padded, so `10` sorts before `2`), ranges are `1_2` at POSB and `1-12` at
   Tiger, CPF repeats the year (`YYYY/YYYY.pdf`), receipts invert it (`M.YY`).
2. **Exports that overwrite history.** Revolut, Wise and the Tiger year to date
   are replaced on every re-export, so the previous file is lost.
3. **Derived files mixed with irreplaceable ones.** 164 text dumps sit beside the
   PDFs, and `cache/` (63% of the bytes) shares the root with them.
4. **Test fixtures are real data.** All 16 files in `data/test-fixtures` are
   byte-identical to real statement dumps, despite being described as
   hand-written. The parser tests depend on a personal folder.
5. **Secrets and identifiers in the wrong places.** `SOURCE.yml` carries card
   digits and an email address; `make backup` copies `.env` and personal rules
   into `data/` in plain text; the code still knows a retired token file path.
6. **`active` is too coarse.** One flag covers all six YouTrip wallets, three of
   which are dormant.
7. **Junk.** 22 `.DS_Store` files, one empty folder, a browser `(1)` duplicate,
   a folder name with spaces, mixed file modes (600, 644, 664).

### Code and paths

8. **Moving files is safe for keys.** `txn_key` hashes statement text, never the
   path, so a new layout cannot change any key.
9. **One folder name carries meaning.** The YouTrip parser reads the wallet
   currency from the folder name and checks it against the statement heading.
   Everything else is located by text, not by path.
10. **About 100 hard-coded `data/` references**, a `Makefile` cache path, and a
    `make lint` target that re-lists the statement folders instead of reading
    config. One configurable data root fixes the first two.
11. **The source path string is a join key** between rows and balance blocks, and
    feeds the coverage check and the skip list, so it must keep one form.

### Data model

12. **Money is float end to end**, rounded in six different places with
    different rules. Chain checks use a 0.005 tolerance.
13. **`amount_sgd` has three implementations** (Notion formula, sync, snapshot)
    and a foreign block with no FX rate is counted one to one as SGD in Notion
    but as unknown in the CSV path.
14. **Relations are by name.** Payees, categories and budgets join by name, so a
    rename leaves a stale row. Period month and card flag are parsed back out of
    a formatted name string.
15. **Packed strings.** `flow_class` packs the counterpart account into the text
    (`Internal - YouTrip`); synthetic rows are marked only by a `bank_txn_type`
    string; `fx_original` is free text.
16. **Duplicated lists.** Three country tables, three category lists, two wallet
    detectors, two ways to compute a payee's default category, two writers of
    the recurring flag.
17. **Key churn risks.** Merchant text is part of `txn_key`, so improving the
    city table or merchant parsing re-keys old rows; synthetic YouTrip keys
    depend on other statements. Notion lookups take the first page on a
    duplicate key and hide it. User data keyed by `txn_key` in free text (pinned
    rules, trip extra keys, spend exclusions) detaches silently on a re-key.
18. **Dropped on load.** Block notes, row counts, the FX source and the statement
    file never reach Notion.
19. **Data only in Notion.** Budget planned amounts, account and card metadata,
    categories, trip names and notes have no other copy.
20. **Data only in files.** Tiger and CPF are re-parsed from PDF on every build;
    the plan lives in `config.toml` and is read around the config loader.

### Still open from the September audits

The loader writes before it checks; parser warnings are swallowed; a duplicated
PDF double-loads; verify compares only a few fields; partial writes on a new
account or option; a December purchase on a January statement can land in the
wrong year; a block with no printed closing passes silently. SQLite with unique
keys and one transaction per write fixes the storage half of these. The date
and card-duplicate bugs live in the parsers and stay G3 work.

## Decisions to keep

Taken by the owner earlier and not to be undone by the redesign: one flat
balance block per account, currency and month, with stored control totals;
rules stay ordered CSV tiers, first match wins; a payee's default category is
derived from the rules; `Between own accounts`, `Wallet top-up` and the
internal flow classes survive; synthetic YouTrip rows stay, with stable keys;
debit-card rows duplicated by an account statement are dropped; a combined
statement is filed under its last month; the category list and its recent
changes stand; `Unidentified` counts as spending; rule-based guesses stay marked
as assumed; closed cold cases stay closed; no plaintext secrets.

## Owner decisions, 11 October 2026

- The proposed `~/.kopifund` layout is approved as drawn: statements renamed on
  import, re-exports kept, derived files in `cache/`, token in the Keychain.
- Rules stay ordered CSV tiers; shared ones in the repo, personal ones in the
  data folder.
- A block with no FX rate has no SGD value and is flagged, instead of counting
  one to one.
- The original finance-tracking data folder is left untouched after the copy.

What was adopted is in [SPEC.md](../SPEC.md), "The data folder" and "The
record".
