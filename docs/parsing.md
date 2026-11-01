# Parsing

How a statement PDF becomes transaction rows and period balances. Read this when
a statement will not parse, when a new layout turns up, or before changing
anything under `server/finance/parsers/`. The one rule the whole pipeline rests on is that a
misparse is invisible unless a printed balance catches it, so every bank feed
below is described down to the balance check it ends at.

Four parsers in the `finance.parsers` package turn statement PDFs into two
CSVs with the same columns:
[../server/finance/parsers/parse_statements.py](../server/finance/parsers/parse_statements.py) for the four
DBS and POSB layouts,
[../server/finance/parsers/parse_youtrip.py](../server/finance/parsers/parse_youtrip.py) for the YouTrip
wallet, [../server/finance/parsers/parse_revolut.py](../server/finance/parsers/parse_revolut.py) for the
Revolut wallets, and [../server/finance/parsers/parse_wise.py](../server/finance/parsers/parse_wise.py) for
the Wise balances. `finance.py sync` runs all four over the whole tree on every
run, and shows everything they print. They also run on their own, from
server/:

```bash
(cd server && python3 -m finance.parsers.parse_statements)                       # extract and check, no CSVs
(cd server && python3 -m finance.parsers.parse_statements --out /tmp/sync)       # also write the two CSVs
(cd server && python3 -m finance.parsers.parse_statements statements/posb-everyday/2026/7.pdf)
(cd server && python3 -m finance.parsers.parse_youtrip --out /tmp/sync/youtrip --wallet eur)
(cd server && python3 -m finance.parsers.parse_revolut --out /tmp/sync/revolut --wallet eur)
(cd server && python3 -m finance.parsers.parse_wise --out /tmp/sync/wise --wallet eur)
```

Each PDF's text is written beside it as `<name>.txt` (`--no-text` suppresses
it). That dump is the first thing to open when a statement will not parse, and
scrubbed copies of a few of them under `server/tests/fixtures/statements/` are what
the parser tests run on.

Two more feeds are read separately and are covered at the end: the Tiger Brokers
annual exports and the CPF yearly statements. Neither produces transaction rows
or period blocks.

**Flow class** appears in every row table below. It says whether a row is money
crossing the boundary of the owner's own holdings (`External`) or a move between
things the owner already owns (`Internal - PayLah`, `Internal - YouTrip`,
`Internal - Revolut`, `Internal - Wise`, `Internal - own account`). Only
`External` rows are spending or income.

## The layouts

Each is detected from the text inside the PDF rather than from the folder or the
file name, so a PDF in the wrong folder is caught rather than misread. The
YouTrip wallet is the one exception: its currency comes from the folder, and the
statement heading has to agree or the run stops.

Every layout is declared once, in its parser module, and collected by
[../server/finance/parsers/layouts.py](../server/finance/parsers/layouts.py) (G21): its
name, the parser a `[[folder]]` names, the Upload page's words for it, its
layout test (which also reads the account number and currency it prints), its
statement folder, its kind (bank account, wallet, card, brokerage or CPF) and
how an upload of it is named. The Upload page's sniffing, the run, Setup's
provider list and the table below all read that registry, so a new layout is
one `parse_<name>.py` module that registers its layouts, and nothing else. The
table is written by `python3 -m docs.gen_parsing`, and `make check` fails when
it and the registry differ.

<!-- layouts: begin, written by docs/gen_parsing.py -->

| Layout | Folder | Detected by | Balance check | Status |
| --- | --- | --- | --- | --- |
| DBS consolidated | `statements/dbs-multiplier/` | the `Withdrawal (-)` column header | opening + in - out = printed closing, per currency | checked on a real statement |
| POSB deposit account | `statements/posb-everyday/` | an account statement with no `Withdrawal (-)` header; its columns come off `WITHDRAWAL($)` | the same, against the printed `Balance Carried Forward` | checked on a real statement |
| PayLah! wallet | `statements/dbs-paylah/` | `PayLah!` in the first four lines | `PREVIOUS BALANCE` + CR - DB = the printed `Total :` line | checked on a real statement |
| Debit card | `statements/dbs-debit-cards/` | `Debit Cards` in the first four lines | the rows sum to the printed `GRAND TOTAL`; no balances exist | checked on a real statement |
| OCBC 360 account | `statements/ocbc-360/` | `STATEMENT OF ACCOUNT` with an OCBC product line | `BALANCE B/F` + deposits - withdrawals = `BALANCE C/F`, every running balance replayed | from a public sample |
| UOB One account | `statements/uob-one/` | `Statement of Account` with an `Account Transaction Details` bar naming the One Account | `BALANCE B/F` + deposits - withdrawals = the last `BALANCE C/F` = the overview's balance, every running balance and every page's carried balance replayed, and the printed `Total` | from a public sample |
| UOB savings account | `statements/uob-savings/` | the same, naming any other UOB product (Stash, Uniplus) | `BALANCE B/F` + deposits - withdrawals = the last `BALANCE C/F` = the overview's balance, every running balance and every page's carried balance replayed, and the printed `Total` | from a public sample |
| YouTrip wallet | `statements/youtrip-<ccy>/` | `My XXX Statement`, which must agree with the folder | opening + in - out = printed closing, after synthesis | checked on a real statement |
| Revolut wallet | `statements/revolut/` | an `XXX Statement` heading on the page, which also names the wallet | every printed row balance, then each wallet's rows against its own balance summary | checked on a real statement |
| Wise balance | `statements/wise-<ccy>/` | an `XXX statement` heading at the top of the file, which also names the balance | every printed running balance, plus the opening derived two ways and the two compared | checked on a real statement |

<!-- layouts: end -->

The Upload page files a dropped PDF by the same tests.
[../server/finance/sniff.py](../server/finance/sniff.py) runs every layout test above, and
the Tiger Brokers and CPF ones below, on the PDF's text: the one that matches
gives the parser, the layout and, for a wallet, the currency, and the account
number the statement prints is compared with the numbers each statement
folder is known by (its `account_no` in the config and what its statements
already print). A PDF goes in only when exactly one folder fits; anything else
is the owner's to place, and nothing is guessed. A PDF that no layout test
matches is a statement Kopifund does not read: the Upload page names it as
not supported and keeps nothing of it (see "Trust, GXS and MariBank: no layout
yet" below).

The table's Status is the registry's own words, the ones Setup shows beside
each provider: "checked on a real statement" once one has parsed and balanced
through the layout, "from a public sample" until then.

Each YouTrip wallet has its own folder, `youtrip-` and the lower-case currency
code (`youtrip-sgd`, `youtrip-usd`, ...), and its `[[folder]]` entry names the
currency with `wallet = "USD"`.

The consolidated and POSB layouts also print a line of total withdrawals and
deposits (`Total Balance Carried Forward in SGD:` and `Total`). Those figures
become the block's `total_in` and `total_out`, but nothing compares them to the
rows: the closing balance is the check.

## How a row is read

Three things are worth knowing before reading any of the sections below.

**Column assignment is positional.** `pdftotext` flattens a row to one line, so
`18.00` carries no meaning on its own: only its horizontal position says whether
it is a withdrawal, a deposit or the running balance. The header row fixes each
column's right edge and every number goes to the nearest one. DBS shifts those
columns between pages, so the header is re-read on every page. Every feed here
works the same way, and so do the two annual exports at the end.

**A page break can split a transaction from its detail.** The consolidated
statement sometimes ends a page right after the dated line, or between the
`PAYNOW TRANSFER` line and the `TO:` line, so the rest arrives after four blank
lines and the tax footer. Blank lines therefore never close a transaction; only
the next dated line, a column header, a balance line or an account header does,
and the footer is dropped as noise. Before this, consolidated-statement rows cut
by a page break came out with no merchant or counterparty.

**A description can span three lines.** On the YouTrip layout it may sit on the
line above the date, on the date line, and on the tail of the time line.

## DBS and POSB: four layouts, one script

The consolidated and deposit-account layouts print their own type in the
description column, so the bank txn type is whatever the statement called it:
`Funds Transfer`, `FAST Payment / Receipt`, `PayNow`, `GIRO Salary`,
`Interest Earned`, `Debit Card Transaction` and so on. DBS prefixes some
consolidated rows with `Advice`; `TYPE_MAP` folds those back onto the
unprefixed type, except a bare `Advice`, which is kept as printed because the
bank posts several different things that way.

PayLah rows carry `DB` or `CR` instead of columns and are typed from their
description by `paylah_type`.

| Description starts with | Bank txn type | Flow class |
| --- | --- | --- |
| `PAYNOW` | `PayNow` | External |
| `SEND MONEY TO` | `PayLah Send Money` | External |
| `RECEIVE MONEY FROM`, `RECEIVE EGIFT`, `FAST TRANSFER TO MY WALLET` | `PayLah Receive Money` | External |
| `TOP UP WALLET FROM MY ACCOUNT` | `PayLah Top-up` | Internal - PayLah |
| `CASHBACK` | `Cashback` | External |
| anything else | `PayLah Payment` | External |

Debit-card rows print the merchant, a country slot and the purchase date on one
line, and every one of them is typed `Debit Card Transaction`. The country slot
is inconsistent: a 3-letter code, a 2-letter code, a US state, or `065` (the IDD
code) for some Singapore merchants. All of it is normalised to ISO alpha-3 so
one select value covers each country.

On the bank side, a row is a wallet load only when the statement names the
wallet's own counterparty, never when the word appears in free text.
`classify_flow` matches a Revolut load on the acquirer string `REVOLUT**NNNN*`
or the PayNow counterparty `REVOLUT TECHNOLOGIES`, and a YouTrip load on
`YOU TECHNOLOGIES` or the collection code `M9496`. A memo that merely mentions
Revolut stays External.

## Where a card row was spent: one shared table

The YouTrip, Revolut and Wise statements all print the merchant and its city in
one field, and all three have to answer the same question from it: which country
was this charged in? The tables and the splitting rule therefore live in
[../server/finance/parsers/places.py](../server/finance/parsers/places.py), which all three import.

| Name | What it holds |
| --- | --- |
| `CITY_TABLE` | the cities a fragment is matched against, by prefix in both directions because the fragment is cut off at an arbitrary length: `SHIPPED_CITY_TABLE`, then the data folder's `places.csv` |
| `SHIPPED_CITY_TABLE` | capitals and major cities, plus the Singapore and Malaysia places people here commonly pay in |
| `CITY_COUNTRY` | fragments that are not city names, or are cut so short that a prefix match would reach the wrong country: `SHIPPED_CITY_COUNTRY` plus the `exact` lines of `places.csv` |
| `CCY_COUNTRY` | the country a charged foreign currency implies, which is the reliable signal on an SGD wallet |
| `WALLET_COUNTRY` | the home country a wallet in a one-country currency falls back to when nothing else resolves; every currency in `CCY_COUNTRY` but USD |
| `ALPHA3` | every ISO alpha-3 code the tables can produce, so a fragment that is already a country code is taken as one |
| `split_country` | the rule itself: Singapore, then the charged currency, then the tables, then the wallet's home country |
| `load(path)` | the shipped tables plus one `places.csv`. Nothing runs at import: the first lookup loads the data folder's file, so importing a parser neither fills the tables nor checks a personal `places.csv`. The parser tests call it with `None` first, so a data folder never changes what they resolve |

The towns of one person's stays and trips are not in the repo: they go in
`places.csv` in the data folder (columns `place,country,match`, where `match` is
`prefix`, the default, or `exact`), which is appended after the shipped table. A
bad line stops the parser with the line number rather than being skipped.

`CITY_TABLE` is ordered, and order decides a prefix match, so a new city goes at
the end of the shipped table or of `places.csv` rather than being inserted, and
an entry is never removed: a city that has dropped out of the statements still
has to resolve when an old month is re-parsed. A shipped entry always wins over a
`places.csv` one for a fragment both could take. Adding a city can also split a
merchant string that used to stay whole, which changes that row's merchant and
txn key, so run `make doctor` after adding one and check that nothing already
recorded moved.

Until this was moved out, the table lived in the YouTrip parser and the Revolut
parser appended its own cities to it on import. A YouTrip run on its own
therefore saw a shorter table than a run that had imported the Revolut parser
first, which made the country on a row depend on import order. Nothing in the
statements to date resolved differently either way, which is why it went
unnoticed. `parse_youtrip` still re-exports the names, so
`parse_youtrip.split_country` keeps working, but it re-exports the one table
rather than owning a copy.

## The balance chain is the only real check

A misparse yields plausible rows. Nothing about a wrong number looks wrong, so
totals alone would never catch it. Every run therefore replays each
account-month's chain against the printed opening, every printed intermediate
balance and the printed closing, and reports every point where the running
figure drifts. The chain resyncs onto the printed balance after a drift, so one
wrong number is one report rather than N. The script exits non-zero if any chain
fails, and `sync` writes nothing when it does.

Card statements print no balances at all, so their only check is the printed
`GRAND TOTAL`, and a YouTrip month that prints "No transactions" carries no
balances either. Both are explicit states with a note on the block, not a silent
pass: a block with no closing balance and no note is a `balance-unexplained`
problem.

## Debit-card duplicates, and why the whole tree is parsed at once

Debit-card statements list the same card transactions the deposit-account
statement carries. So a card row dated inside a month that an account statement
already covers is dropped as a duplicate, the card block's totals are reduced by
what left, and a card statement with nothing left over is skipped whole and
printed as `SKIP`.

The parser can only do that when both statements are in the same run. Parsing
`dbs-debit-cards/` on its own yields duplicates, which is why `sync` always
passes every configured folder of a parser to one invocation. A card statement
whose months an account statement fully covers stays on disk and loads nothing
at all, because the account statement carries every row.

This is also why the file name matters. Account and wallet statements are named
for the calendar month they cover; a debit-card statement closes on the 9th and
is named for the month **before** its printed "As at" date, so the statement "As
at 09 Jan 2025" is `2024/12.pdf`. A statement spanning several months takes the
last one, so `posb-everyday/2025/1_2.pdf` runs Dec 2024 to Feb 2025.

## UOB: the One Account and the savings accounts

[../server/finance/parsers/parse_uob.py](../server/finance/parsers/parse_uob.py) reads the
UOB One Account and the UOB savings accounts (Stash, Uniplus). It was built
from a public sample, not from a real statement: the owner holds no UOB
account, so only the invented fixtures `server/tests/fixtures/statements/uob-*.txt`
have ever been through it, and both layouts are "from a public sample" in the
registry, in the table above and on Setup until a real statement has parsed and
balanced.

UOB publishes no sample deposit statement; its own statement pages
(https://www.uob.com.sg/personal/invest/statement.page) cover investment
statements only. The sample is three synthetic previews, a One Account, a
Uniplus and a Stash statement, on a statement converter's page, each marked
there as made up for demonstration:
https://bankstatemently.com/banks/sg/uob/bank-statement. The One and the
savings previews print the same "Statement of Account", so one parse reads
both. The registry still declares two layouts, `uob-one` and `uob-savings`,
told apart by the product the statement names on its transaction pages, so
Setup makes a folder for each and the Upload page files a Stash statement
apart from a One Account one.

| What | As the sample prints it |
| --- | --- |
| The statement | `Statement of Account` and `Period: 01 Mar 2026 to 31 Mar 2026` on the first page, then an account overview |
| The account | an `Account Transaction Details` heading per account, with the product and its number on the bar under it: `One Account 123-456-789-0` |
| Columns | Date, Description, Withdrawals, Deposits, Balance, each money heading with `SGD` under it; a number's column is its position against the heading's right edge, re-read on every page |
| Dates | `01 Mar`, with no year: the year comes from the period, so a statement over the new year keeps its December rows in the old year |
| The chain | `BALANCE B/F` on the period's first day opens it; a description runs on over lines with no date |

The converter's page also says, in words rather than in a preview, that the
statements repeat a balance brought forward and a balance carried forward on
every page. The sample shows no last page, so the closing `BALANCE C/F` and
the `Total` line of withdrawals and deposits are taken on that word. A
brought-forward line after the first is checked against the running balance
and never read as a new opening.

The balance check replays every row's running balance in integer cents, checks
every page's carried and brought forward balance against it, compares a
printed `Total` with the rows, and ends at the last `BALANCE C/F`, which must be
the balance the first page's overview prints for the account, so a statement
that has lost its last page is refused. A drift, a missing running balance, no
closing or no overview balance stops the run. Each account in a statement is
judged on its own failures. A debit card row's first
detail line is the purchase day (on or before the day it posted) and a
reference, and its second the merchant, its city and its country; a transfer's
`mBK-` line is its reference.

## YouTrip: one statement per wallet-month

One PDF is one calendar month of one wallet, and the wallet folder gives the
currency. The table has five columns (Completed Date, Description, Money Out,
Money In, Balance), with the time on the line under the date.

| Description | Bank txn type | Flow class |
| --- | --- | --- |
| `Top up with ...` | `YouTrip Top-up` | Internal - YouTrip |
| `SmartExchange`, `Exchange` | `YouTrip Exchange In` or `YouTrip Exchange Out`, by the row's direction | Internal - YouTrip |
| a comma-less description naming a refund, a fee or a cashback | `YouTrip Refund`, `YouTrip Fee`, `YouTrip Cashback` | External |
| `To Mobile no. ending NNNN`, `From Mobile no. ending NNNN`, `P2P-NNNNNNN-...` | `YouTrip Other` | External |
| anything else, which is a merchant string or `MRT/BUS Total Fare` | `YouTrip Payment` | External |
| synthesised for a balance jump | `YouTrip Unprinted` | Internal - YouTrip or External, see below |

No refund, fee or cashback row has appeared in the statements to date. The three
types are mapped so a future one lands in the right bucket rather than in
`YouTrip Payment`.

An exchange puts the other wallet's side in `fx_original`, its currency code in
the memo and the printed FX rate in the reference, which is what pairs the two
legs. A card row puts the merchant's city through the shared table above and the
charged foreign amount in `fx_original`.

**The export drops rows.** The YouTrip PDF export omits rows, mostly but not
only at page breaks. Wherever the printed balance jumps past what the printed
rows explain, the parser synthesises one `YouTrip Unprinted` row for the jump,
dated on the next printed row, with a note naming the window. A month whose
opening differs from the previous statement's closing gets the same treatment
with a row dated the first of the month, and the period then opens at the
previous closing so that opening + in - out still equals the printed closing.

A synthetic row is `Internal - YouTrip` when its size is a known top-up size, or
when it matches an exchange printed in another wallet over the same days.
Everything else is External and is listed in the open-items note, which is
local-only (`notes/` is gitignored). The script exits non-zero only when a chain
still fails to close after synthesis.

Every exchange appears on both wallets' statements, and all the pairs match; the
YouTrip crosscheck note records the pairing and is local-only, like the rest of
`notes/`.

Foreign wallets carry `fx_to_sgd` from the latest SGD exchange printed in that
wallet up to that month, which makes `closing_sgd` an approximation. Unlike
Revolut and Wise, this parser never looks forward: a month before the wallet's
first SGD exchange carries no rate at all.

Early statements print no `Y-` account number. Those blocks borrow the number
from the earliest later statement that prints one and say so in the block's
note.

Synthetic rows are the one part of the data a re-export can change; the
[runbook](runbook.md) has the procedure.

The test fixtures are `server/tests/fixtures/statements/youtrip-sgd.txt`,
`youtrip-sgd-gap.txt`, `youtrip-sgd-boundary.txt`, `youtrip-nok.txt` and
`youtrip-empty.txt`: a clean month, a hidden mid-page row, a month-boundary
mismatch, a foreign wallet and a "No transactions" month.

## Revolut: one export, every wallet

Revolut exports the whole account at once. `statements/revolut/` holds a single PDF,
the "Export all accounts", "All time" statement, whose file name carries the
range it covers and ends on the day it was generated
(`account-statement_2024-01-01_2025-12-31_en-sg_<hash>.pdf`). A re-export
replaces that file rather than sitting beside it.

Inside, each currency wallet is a statement of its own: an `XXX Statement`
heading on every page, a balance summary, then the five-column table (Date,
Description, Money out, Money in, Balance). The parser groups consecutive pages
by the currency their heading names, so a page whose heading did not survive
`pdftotext` stays with the section before it, and a single-wallet export parses
the same way as the combined one. Two files whose sections cover the same months
stop the run with a `duplicate-period` error rather than loading a month twice.

A row is the dated line plus the indented detail lines under it, up to the next
blank line: `Reference:`, `To:`, `From:`, `Card:`, `Fee:`, a `Revolut Rate`
line, and a bare amount in another currency. Those lines are what type the row.
The first rule that matches wins, in this order.

| Row | Bank txn type | Flow class |
| --- | --- | --- |
| `Exchanged to XXX` | `Revolut Exchange In` or `Revolut Exchange Out`, by which side the wallet is on | Internal - Revolut |
| `Apple Pay top-up by *NNNN`, `Top-up by *NNNN`, `Payment from <owner>` | `Revolut Top-up` | Internal - Revolut |
| `Transfer to NAME`, `Transfer from NAME` | `Revolut Transfer` | External |
| anything with a `Card:` line | `Revolut Payment` | External |
| the owner's own name, no detail lines, money out | `Revolut Withdrawal` | Internal - Revolut |
| anything else | `Revolut Other` | External |

No row in the export to date has fallen through to `Revolut Other`; the branch
is there so an unknown description is still loaded rather than dropped.

The card rows carry the merchant from the `To:` line, its city through the
shared city table above, the last four from the `Card:` line and the charged
foreign amount as `fx_original`. An exchange puts the other side in
`fx_original` and its currency code in the memo, which is what pairs the two
legs.

**Months, not statements.** The export is one long period, so each section is
split into one block per calendar month, from the first month with rows to the
month the export was generated in, opening at the running balance and closing
at the last balance that month printed. A month with no activity still gets a
block, with no rows and the balance carried unchanged, because the analytics
count a month as covered only when every account has a block in it. The last
block ends on the export date, so the account's coverage runs to the day of the
export rather than to the last transaction.

The cut itself is not this parser's. Wise exports the same way and needs the
same rule, so `every_month`, `month_end` and `split_months` live in
[../server/finance/parsers/wallet_months.py](../server/finance/parsers/wallet_months.py) and both parsers
import them. Each had its own copy until this was moved out, and the two had
already begun to drift.

Every row prints its balance, so the chain is replayed row by row and every
month is checked against it. Each section is then checked against its own
balance summary, with one correction: the summary counts a fee taken off
incoming money as money in and money out both, while the column and the balance
carry the net, so the fee is added back to both totals before they are compared.

The balance summary is required. It is the only place Revolut prints a wallet's
opening and closing, and without it the section would open at zero and every
month would only be checked against balances derived from its own rows. A
section whose summary line is missing, or prints fewer than four amounts, stops
the parser with a non-zero exit before any CSV is written, naming the section.

Foreign wallets carry `fx_to_sgd`, and the block's note says which of three
sources it came from.

| Source | When it is used | Note says |
| --- | --- | --- |
| a row of this wallet showing both sides in SGD, an exchange or card spend with a printed S$ amount | the latest such row up to the end of the block's month | `row rate` |
| the wallet it was exchanged from: 1,100.00 SEK into 100.00 EUR, plus SEK's own rate that day, gives EUR's | the wallet has no SGD leg of its own up to that month | `exchange partner's rate` |
| the first rate after the block, direct or borrowed | the block is earlier than the wallet's first rate of either kind | `row rate, the first one after this month` |

`closing_sgd` is the closing balance times that rate. A wallet with no rate of
any kind carries neither field.

The SGD, USD and EUR sections print `Account Number`; the NOK and SEK sections
have no local account details and print none. Those two borrow the first number
another section printed and say so in the block's note.

The test fixture is `server/tests/fixtures/statements/revolut-all.txt`, a scrubbed
copy of the `pdftotext` dump of a real export with five wallets.

## Wise: one file per balance

Wise's "Statements" export writes one PDF per currency balance rather than one
file with a section per wallet. Each balance has its own folder,
`statements/wise-<ccy>/<year>/export-<date>.pdf`, and the files of one export
set all cover the same range and all end on the day they were generated. Only
the newest export in each folder is read, so a re-export goes in beside the old
one. A copy of the same export under another name would cover the same
balance-months and stops the run with a `duplicate-period` error.

The currency comes from the `XXX statement` heading at the top of the file, not
from the folder or the file name. Under it sit the range line, `Generated on:`,
an account block whose fields differ per currency, and the closing balance. Then
the table, four columns (Description, Incoming, Outgoing, Amount), newest first,
two lines per row:

```text
Converted 100.00 SEK to 12.50 SGD (fee: 0.55 SEK)       -99.45        0.00
3 March 2025 | Transaction: BALANCE-1000000001
```

The first line carries the description and the column amounts, assigned by
nearest column right-edge. `Outgoing` already has its minus sign and `Amount` is
the running balance after the row, not the row's own amount. Descriptions carry
amounts of their own, so a number is only a column value when it ends under one
and the rest of the line is the description. The second line carries the date
and Wise's transaction id, which becomes the row's `reference`: it is the one
thing about a row that never changes, so the keys stay stable across a
re-export. The rows are then reversed into oldest first, which leaves a single
day's rows in the order Wise applied them.

| Row | Bank txn type | Flow class |
| --- | --- | --- |
| `Topped up account` | `Wise Top-up` | Internal - Wise |
| `Converted 100.00 SEK to 12.50 SGD` | `Wise Conversion In` or `Wise Conversion Out`, by which side the balance is on | Internal - Wise |
| `Wise Charges for: BALANCE-...` | `Wise Fee` | External |
| `Received money from NAME`, `Sent money to NAME` | `Wise Transfer` | External |
| `Card transaction of 12.00 SGD issued by MERCHANT City` | `Wise Card Payment` | External |
| anything else | `Wise Other` | External, with a warning naming the description |

A conversion puts the other side in `fx_original`, which is what pairs the two
legs. Its fee is never netted off, because Wise prints it as its own `Wise
Charges for:` row beside the conversion; that row stays separate and the flow
rule in [../data/rules/10-types.csv](../data/rules/10-types.csv) sends it to Fees and
charges with Wise as the payee. A transfer takes the person or the number as its
counterparty. The card branch has not fired yet: this export has no card rows,
and the branch is there so one does not fall through to `Wise Other`.

**One account, one account number.** One Wise account holds every balance, so
`account_no` is the SGD balance's `150-246-80` for all of them, the `Wise wallet`
row in Accounts. The USD and SEK files print their own local details (another
account number, an IBAN); the parser keeps them on the statement and the block's
note says the account number came from the SGD balance instead.

**No opening balance is printed.** It is derived twice and the two are checked
against each other: the closing less every row, and the oldest row's own printed
running balance less its own amount. A balance with no rows opens and closes at
the printed closing, and gets the single export-month block. The closing is
required: without it every month would only be checked against balances derived
from its own rows, so a file that prints none stops the parser with a non-zero
exit before any CSV is written.

**Months, not statements.** Blocks come out of the same
[../server/finance/parsers/wallet_months.py](../server/finance/parsers/wallet_months.py) the Revolut parser
uses, one per calendar month from the first month with rows to the export month,
idle months included: see the Revolut section above.

Foreign balances carry `fx_to_sgd` from a conversion with SGD on one side. The
description prints both amounts, so the SGD file and the foreign file agree on
the rate. The order is the latest conversion up to that month, then the first
conversion after it, then the same two through the currency the balance was
converted from for a balance that has never touched SGD. The note says which,
and money can sit in a balance for months before it is converted, so the
"first one after this month" case is the normal one for an early block rather
than an edge case.

The test fixtures are `server/tests/fixtures/statements/wise-sgd.txt`, `wise-nok.txt`
and `wise-usd.txt`, scrubbed copies of the `pdftotext` dumps of a real export:
three balances, a handful of rows and one block per balance-month.

## Tiger Brokers annual exports

The brokerage is not a bank account and is not parsed by any of the four
parsers above. Tiger Brokers exports one PDF per year, so
[../server/finance/parsers/parse_tiger.py](../server/finance/parsers/parse_tiger.py) reads
`statements/tiger/<year>/<year>-<first month>_<year>-<last month>.pdf` and hands the whole
statement to the analytics layer as one dict per year. The filename says which
months it covers, but the `Statement Period` line printed inside the PDF is what
wins.

```bash
(cd server && python3 -m finance.parsers.parse_tiger statements/tiger)                  # one line per statement
(cd server && python3 -m finance.parsers.parse_tiger statements/tiger --json /tmp/tiger.json)
```

The text comes from `pdftotext -layout`, the same way the DBS statements are
read, and the same three habits of a PDF table apply: column positions move
between years, a cell can wrap over three lines, and the page header and the
tax-invoice footer interrupt a table wherever a page happens to break. Every
export from the 2022 layout on also prints each Forex and Stock trade twice, once with the
symbol and once without, so the symbol-less copy inherits the symbol above it
and trades are then de-duplicated on date, symbol, quantity and price.

What is read out of each statement:

| Section | What comes back |
| --- | --- |
| Account Overview | opening and closing cash, stock, dividend accruals and total, in the account's base currency |
| Cash Report, base currency summary | every printed row by its own label, plus the deposits, dividends, fees and withholding tax totals |
| Deposits and Withdrawals | one row per transfer with its date, amount and currency, and the base-currency total |
| Dividends | the paid rows only, with gross, tax and net; the accrual increase and reduction rows are not money that moved |
| Holdings | the positions at the end of the period: quantity, cost, close price, value and unrealised profit |
| Trades and Forex | the stock and currency trades, de-duplicated |
| Base Currency Exchange Rate | one unit of each currency in the base currency at the period end, which is how everything reaches SGD |

**What is deliberately not loaded.** These statements produce no Transactions
rows and no Period balances blocks, and nothing about them reaches Notion. Three
reasons. The bank statements already carry the money leaving the bank, as a
transfer to Tiger Brokers, so loading the deposits again would count the same
money twice. A Period balances block is a month with a printed opening and
closing that the balance chain can check, and an annual statement is neither
monthly nor chained. And a holding is not a transaction: it is a valuation on
one day, which moves without anything happening. So the statements stay on disk
and are read fresh on every `finance.py analyse`, into `snapshot["portfolio"]`
and the `portfolio` stat.

## CPF yearly statements

**What CPF is.** Singapore's Central Provident Fund is a mandatory savings
scheme, not a liability. For a member aged 55 or under, 20 per cent of gross
salary up to the Ordinary Wage ceiling is deducted from pay and the employer
adds 17 per cent on top, so 37 per cent of gross lands in three accounts in the
member's own name: Ordinary Account (OA) for housing and education, Special
Account (SA) for retirement, MediSave Account (MA) for healthcare. It earns
guaranteed interest (2.5 per cent on OA, 4 per cent on SA and MA, with extra
interest on the first 60,000) and cannot be spent on daily life. It belongs on
the balance sheet as locked savings, kept apart from cash and from the
brokerage.

CPF publishes one yearly statement of account per year, so
[../server/finance/parsers/parse_cpf.py](../server/finance/parsers/parse_cpf.py) reads
`statements/cpf/<year>/<year>.pdf` and hands each year to the analytics
layer as one dict.

```bash
(cd server && python3 -m finance.parsers.parse_cpf statements/cpf)                  # one line per year
(cd server && python3 -m finance.parsers.parse_cpf statements/cpf --json /tmp/cpf.json)
```

The text comes from `pdftotext -layout`, the same way every other statement here
is read. Three layouts have been seen:

| Years | Layout |
| --- | --- |
| 2016 to 2019 | a personalised summary page with the four account balances in a row, one combined `Employment` figure rather than an employee and an employer half, `Interest` printed outside the Government column, then a debited section (Housing, Healthcare, Retirement, Education, Investment, Others) |
| 2020 | a `Balances as at 31 December 2020` list with You, Employer, Government, Base Interest, Extra Interest and Government support on their own lines |
| 2021 to 2025 | `1. Account balances`, `2. Inflow and outflow`, `2.1. Inflow details` (employee, employer, base and extra interest, Government support), `2.2. Other outflow details` |

Every year ends with the `CPF transactions` table, whose columns are Date, Code,
For Mth/Year, Ref and one per account, and the `01 JAN BAL` and `31 DEC BAL`
rows in it are the source of the opening and closing balances; the summary page
is the source of the inflow split. The 2016 to 2020 statements print the amounts without a dollar sign, and
a page header block can interrupt the table wherever a page breaks, both of
which the parser expects. Opening plus every transaction equals closing, per
account, in every year, and the CLI says so at the end of its run.

The Government column is worth one note, because it means two different things.
From 2020 on it carries the interest CPF credited as well as any support
scheme; before that the interest is printed outside it. The `cpf` stat adds the
interest back so that the figure means the same thing in every year, and
reports the interest on its own beside it.

**What is deliberately not loaded.** These statements produce no Transactions
rows, no Period balances blocks and no Notion writes, for the same reasons the
Tiger Brokers exports do not. The CPF contribution never passes through the
bank statements as a payment the owner made: it is deducted from gross pay
before anything lands, so there is no bank row it could be matched to and
nothing to double count. A yearly statement is not a month with a printed
opening and closing that the balance chain can check. And the balance is a
position rather than a flow. So the statements stay on disk and are read fresh
on every `finance.py analyse`, into `snapshot["cpf"]` and the `cpf` stat.

## Trust, GXS and MariBank: no layout yet

Trust Bank, GXS Bank and MariBank have no layout. None of the three publishes a
sample statement or says how its statement is set out, and a layout is never
built from a guess or from someone's own statement posted online. Each waits
for a statement's text from someone who holds the account, written by
`finance.py sample` (below). Until then the Upload page names a statement from
any of them as not supported, keeps nothing of it, lists the banks Kopifund
reads and shows Trust as coming soon.

### A sample text: `finance.py sample <pdf>`

```bash
python3 server/finance.py sample ~/Downloads/trust-2026-09.pdf                 # writes trust-2026-09.sample.txt beside it
python3 server/finance.py sample ~/Downloads/trust-2026-09.pdf --out ~/Desktop/trust-sample.txt
```

It reads the PDF the way the parsers do (`pdftotext -layout`) and writes the
text with every name, street address, postcode, NRIC or FIN number, email,
phone number and account or card number it finds replaced by a placeholder of
the same length and shape: a digit becomes 0, a capital X and a small letter x,
and punctuation stays. Every line keeps its length, so the columns stay where
the statement printed them, and amounts and dates are never replaced. Then it
prints how many of each kind it replaced. It needs no config and no data
folder, sends nothing anywhere, never writes inside this repository, and only
reads the PDF.

The replacement is best effort. It is a set of patterns
([../server/finance/sample.py](../server/finance/sample.py)), not a guarantee: a name printed
somewhere it does not look, or in a shape it does not know, can survive. Read
the whole file before you share it, and replace by hand anything it missed.

What it looks for:

| Kind | Found by |
| --- | --- |
| Names | the holder's name over an address, an email or an account number in one column; a capitalised name alone at the left near the top of a page; the line after `Account Holder` or `Name`; whoever follows `TO:`, `FROM:`, `Transfer to`, `Received money from`, `PAYNOW TO` and the like; the first name-shaped detail line under a transfer; a name after `MR`, `MS` or `MDM`. Each name found is then replaced wherever else it is printed |
| Street addresses | `BLK` and a number, a number then a street word (`RD`, `Road`, `AVE`, `Street`, `Drive` ...), and unit numbers like `#01-100` |
| Postcodes | six digits after `Singapore` or `S(`, or alone on a line |
| NRIC or FIN numbers | a letter S, T, F, G or M, seven digits and a letter |
| Emails | anything shaped `name@domain.tld` |
| Phone numbers | Singapore numbers: eight digits starting 3, 6, 8 or 9, with or without `+65` |
| Account and card numbers | any run of seven or more digits (hyphens allowed between them) that is not a date, a card's four groups, an IBAN, a masked card (`XXXX-XXXX-XXXX-1234`, `123456******1234`, `*1234`) and the four digits after `ending` |

What was checked, on 23 Oct 2026:

| Bank | What its own pages say | Sources |
| --- | --- | --- |
| Trust | Statements are monthly, in the app, kept for seven years. An account is a Main account and up to five Savings Pots. Nothing on headings, columns, dates, balance wording, or whether pots get their own section | [savings FAQ](https://trustbank.sg/faqs/savings-account/), [product terms](https://trustbank.sg/legal/key-facts-sheet-and-product-terms-savings-account/), [general terms](https://trustbank.sg/legal/general-terms-and-conditions/) |
| GXS | A Saving Pocket shows on the statement only in a month money moved in or out of it, and a Boost Pocket once it exists. The account number is ten digits starting 888. A statement is made even in a month with no transactions. No layout | [pockets on e-statements](https://help.gxs.com.sg/GXS_Savings_Account/Account_Statements/Are_Saving_Pockets_and_Boost_Pocket_reflected_on_my_GXS_Bank_e-statements%3F), [statement FAQs](https://help.gxs.com.sg/GXS_Savings_Account/Account_Statements), [deposit terms](https://www.gxs.com.sg/deposit-account-terms) |
| MariBank | Where statements are, and that each is made in the first week of the next month. Nothing on pockets or layout | [where to view](https://www.maribank.sg/help-centre/article/10091-where-can-i-view-my-mari-savings-account-e-statement), [when generated](https://www.maribank.sg/help-centre/article/10092-when-will-i-receive-my-e-statement-for-my-mari-savings-account) |

Two sources were set aside. A statement converter's site
(bankstatemently.com) describes a Trust layout, but it is a third party's
account, not the bank's. And the only real statements found are people's own,
uploaded to document sites, which a fixture never copies.

## The gaps that are permanent

| Gap | Why |
| --- | --- |
| PayLah wallet statements only go back a limited time | DBS provides no earlier ones. Before the first one, top-ups leave the dataset and never return as itemised spending |
| The earliest months can be card statements only | When no deposit-account statement can be obtained for a month, it has card spending but no transfers, no salary and no balances |
| Nothing before the first statement | No statement exists |
| Some debit-card rows carry no merchant text | A multi-month deposit statement can list card rows in its summary section without the merchant. The text is not in the PDF, so no rule can ever reach them |
| No credit card or loan statements | The balance sheet is assets-only; there is no liability side. The Tiger Brokers exports and the CPF statements are the assets outside the banks, and both are annual, so the investments and CPF figures move once a year rather than monthly |

None of these is a parser bug and none of them will be fixed by a rerun. A month
missing from an account's coverage line in the run report is either one of these
or a PDF you have not downloaded yet.

## The tests

```bash
cd server && python3 -m unittest discover tests
```

That is the canonical command, run from server/. The parser tests run on the
`.txt` dumps in `server/tests/fixtures/statements/`, real statements copied out of
the statement tree with every personal identifier replaced by a consistent fake
(dates, amounts and merchants kept), so they need no PDFs and no network. Two suites are slower
because they use the tree itself: `test_monthly_run` parses a temporary copy of
it, and `test_analytics_snapshot` builds the snapshot from it into a temporary
directory.

The statement tree lives in the data folder, outside the repo, so a fresh clone
has the scrubbed fixtures but not the statements. Every module that needs the tree skips itself with a line saying
which path is missing, and the suite still ends clean.

| Test | Pins down |
| --- | --- |
| `test_parse_layouts` | one fixture per DBS, POSB, PayLah, card and YouTrip layout: row count, every field of the first and last row, the block's opening, closing and totals, and which detail text makes a bank row a wallet load rather than spending |
| `test_parse_balance_chain` | one printed number bent at a time reports exactly one drift, and the chain resyncs so one error does not become N |
| `test_parse_no_closing` | a card statement and an empty YouTrip month say in their note why they print no closing; any other block that does not is a problem |
| `test_parse_page_break` | a consolidated card row whose detail lines fall behind a page break keeps its merchant, parses to the same rows and keeps the same keys, and nothing from the footer leaks into a row |
| `test_parse_revolut` | the Revolut export: pages grouped into wallet sections, every row kind and the type it gets, a fee left inside the column amount, one block per month with the export month closing the section, the borrowed FX rate, the real export's five wallets all reconciled, a section with no balance summary stopping the parser before it writes anything, and that the shared city table is the same table whatever order the parsers are imported in |
| `test_parse_wise` | the Wise export set: the column split at the real column positions, the currency read off the heading, every balance taking the SGD file's account number, every row kind and the type it gets, the transaction id as the reference, rows reversed into oldest first, one block per month plus the export month, the opening derived twice, the SEK rate off the conversion, the real export's three balances all reconciled, and a file with no printed closing stopping the parser before it writes anything |
| `test_parse_tiger` | the Tiger Brokers parser on five invented statements across the two layouts: the bare-symbol layout, the wrapped 2026 layout with every trade printed twice, and a quiet period with no trades, withdrawals or dividends: every field, the duplicated trades and the interrupted tables |
| `test_parse_cpf` | the CPF parser on five invented yearly statements, one per layout plus a quiet year and a year whose transactions table is torn across a page break: every field, the positional column reading and the page furniture, and a year that does not reconcile left out of `parse_folder` and named |
| `test_parse_uob` | the UOB One and Stash layouts on invented statements from the public sample: a statement that balances, one that does not and is refused by the run, the table broken across pages with a carried and a brought forward balance, a credit and a debit on one day, a month brought forward from the last one's closing, and the One and savings statements filed apart |
| `test_sniff` | every fixture here, the Tiger Brokers ones and the CPF ones read as exactly one layout and go to the right account and currency, with the config's numbers, with none and with statements already filed; two accounts it cannot tell apart, a number no account knows, a wallet currency with no folder and a file that reads as two statements all ask; a statement no layout reads is not supported and kept nowhere; an upload whose text names another account is refused |
| `test_sample` | `finance.py sample` on every fixture here plus an invented bank's statement (`server/tests/fixtures/unknown/`): no name, address, account or card number, NRIC, email or phone number survives, every amount and date does, every line keeps its length; a random identifier of each kind planted in a fixture is always replaced; the text goes beside the PDF or where it is told, never into the repository, and the PDF is only read |
| `test_youtrip_synthesis` | a jump inside a statement and a jump across a month boundary each produce the right synthetic row |
| `test_dates` | both printed date forms, including the consolidated layout keeping its own printed year |
| `test_keys` | the fields `txn_key` hashes and how repeats are numbered, both frozen because Notion holds the current keys |
| `test_card_duplicates` | card rows covered by an account statement are dropped, the card block's totals fall by what left, and an empty card statement is skipped |
| `test_rules` | rules loading, tier precedence, lint and `--why` |
| `test_sync` | the upsert against a fake Notion: create, patch, leave alone, archive, and that a month with no change issues no writes |
| `test_notion_client` | the Notion client's pacing, connection pool and sharded reads, with the transport stubbed out, so no token and no network |
| `test_token` | the `.env` reader and the order the token is looked up in |
| `test_doctor` | every doctor check, each fired by a plan crafted to break it |
| `test_rollup` | the rollup against a fake Notion: a row is only put on a trip when something says it was spent away from Singapore, a nested trip beats the long one around it, an Actual is refreshed without touching Planned |
| `test_publish` | the hub page's tables and its sync-status line |
| `test_monthly_run` | the whole run end to end over a temporary copy of the real statement tree, the real rules and a fake Notion |
| `test_portfolio` | the portfolio stat: cumulative contributions, the SGD conversion, deposits matched back to the bank rows, net worth, and the two hub rows |
| `test_cpf` | the CPF stat on three invented yearly statements: the years table, a layout that prints no employee and employer split, the Government figure with its interest added back, the whole-record totals, net worth including CPF, the stale-statement finding, the forward view's employer share leaving the cash line untouched, and the two hub rows |
| `test_analytics` | every stat against a fixture built to break them: a recurring series that stops, a refund inside a spend category, a synthetic row, foreign rows with and without an FX rate, a trip and internal legs |
| `test_analytics_snapshot` | the snapshot builder over the real parser output, run once into a temporary directory, so nothing touches Notion or the network |
| `test_pipeline_integrity` | every path checks the balance before it uses a row: reused CSVs refused by `rollup --csv`, `analyse --from-csv`, `payees --csv`, `add-options --csv` and `verify --csv` when a block fails, before Notion is reached, and a CSV snapshot never reused in place of the folder given, an unbalanced CPF year rejected and named, and kept in the record by `load`, wallets known from the config without an Accounts row, savings-runway walking to the last balance block while its spend rates stay on the flow months, cache files swapped in whole, and every Metrics row keeping its fixed Order whatever is published beside it |
| `test_analytics_review` | every shipped stat result recomputed from the raw snapshot in plain Python, so a headline that moves has to be defended. Skips when the gitignored cache is absent |
| `test_cache` | the stat cache reader over `server/tests/fixtures/analytics`: the result shape and chart hints the app relies on, the run history, the hub Metrics rows, and a path that tries to leave the cache |
