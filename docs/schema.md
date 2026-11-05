# Schema

Two stores hold the money. The record, `kopifund.db` in the data folder, is
the one the app reads and writes; section 11 describes its tables, written from
the code by `python3 -m docs.gen_schema` (`make check` fails while it is out of
date). Sections 1 to 10 describe the Notion mirror.

This is the reference for the Notion side of the loader: every database it
reads or writes, every property on it, the type that property has, and who owns
it. Sections 1 to 8 are the eight databases that hold the money; they sit
together on one Notion page. Section 9 is the two small tables on the hub page
(`DBS Balance Sheets`), and section 10 is the rollup step that writes the
derived properties in sections 1, 7 and 8. The owner's trip planner, `Trips
Database`, is an eleventh data source; the loader reads it and never writes it
(section 8). Nothing here identifies a database except its data-source id, and
those ids live only in the data folder's `config.toml`:
[../server/finance/config.example.toml](../server/finance/config.example.toml) shows its
shape.

Every property below is marked with who owns it.

| Mark | Owner | Meaning |
| --- | --- | --- |
| loader | `finance.py` | written and rewritten by the sync. Edit it in Notion and the next run patches it back |
| formula | Notion | a formula or rollup. Nothing writes it |
| human | you | typed in Notion. The sync never reads or touches it |

The loader-owned properties are the property tables in
[../server/finance/model.py](../server/finance/model.py): `TXN_TYPES`, `PERIOD_TYPES` and
`PAYEE_TYPES` for the upsert, `BUDGET_TYPES` and `ACCOUNT_ROLLUP_TYPES` for the
rollup, and `Dates` alone out of `TRIP_TYPES`. Section 9's two tables have their
own maps, `METRIC_TYPES` and `MONTH_TYPES` in
[../server/analytics/publish.py](../server/analytics/publish.py). One place decides what a
page should look like, so the writer and the checker can never disagree, and the
run's diff is a plain dict comparison. A property that is deliberately not in
those tables, such as `Budgets.Planned`, cannot be written by any code path.

## 1. Accounts

One row per account: the balance-sheet line items. Eight rows.

| Property | Type | Owner | Notes |
| --- | --- | --- | --- |
| Name | Title | human | `DBS Multiplier`, `YouTrip wallet · Y-1029384756` |
| Institution | Select | human | DBS, POSB, PayLah, Other, YouTrip, Revolut, Wise |
| Account no. | Text | human | `120-345678-9`. This is the key `[accounts]` in config.toml uses |
| Class | Select | human | Asset, Liability |
| Subclass | Select | human | Cash, Wallet, Card, Investment, Receivable |
| Base currency | Select | human | SGD, USD |
| State | Select | human | Open, Closed |
| Active period | Date | human | used as a range: start = opened, end = closed, empty while open |
| Use cases | Text | human | what the account is for, in the owner's words |
| Statements from | Date | loader | earliest block loaded for this account, from the rollup |
| Statements to | Date | loader | latest block loaded, from the rollup |
| Data coverage | Select | loader | None, Partial or Full. The rollup writes Full when the account has any block and None when it has none; Partial is there to be set by hand |
| Period balances | Relation | formula | back-link from Period balances |
| Transactions | Relation | formula | back-link from Transactions |
| Related to Cards (Accounts) | Relation | formula | back-link from Cards |
| Txn count | Rollup | formula | count of Name via Transactions |

The bank prints no opening date, so `Active period` starts at the earliest
evidence: the first credit into a zero balance, or the first statement row when
the account is older than the statements held. Overwrite it with the real date
if you learn it.

Rows exist for the accounts with statements plus placeholders for sources with
no parser yet, such as a brokerage or another bank. An account with no `[accounts]` entry in config.toml
stops the run at `unknown-account` before anything is written; a
Notion row with no config entry is a placeholder for a source that is not loaded
yet, and the rollup leaves it alone rather than marking it empty.

Three of those accounts hold several currencies at once, and their `[[folder]]`
entry in config.toml says which shape the statements come in: the YouTrip wallet
(`Y-1029384756`) has one folder per currency, each its own `[[folder]]` entry with `wallet`, the
Revolut wallet (`885100012345678`) has one export holding every wallet as its
own section (`multi_currency`), and the Wise wallet (`150-246-80`) has one
folder per balance, each with `wallet`. Either mark makes the account a
wallet account, which is most of what tells the rollup that a row was spent
abroad (section 8). Each currency still gets its own Period balances block per
month, on the one account number.

## 2. Period balances

One row per **account x month x currency**. Currency belongs in the key: the
Multiplier account carries a separate USD balance with its own FX rate, so
account and month alone are not unique.

| Property | Type | Owner | Notes |
| --- | --- | --- | --- |
| Name | Title | loader | `130-24680-5 · SGD · 2026-06` |
| Account | Relation | loader | resolved from the parsed account number |
| Period end | Date | loader | the statement's "as at" date |
| Currency | Select | loader | SGD, USD, EUR and every other currency a statement has produced |
| Opening balance | Number | loader | brought forward |
| Closing balance | Number | loader | carried forward |
| Total in | Number | loader | the statement's own control total |
| Total out | Number | loader | the statement's own control total |
| FX rate to SGD | Number | loader | non-base-currency blocks only |
| Closing SGD | Formula | formula | closing x FX rate, or the closing balance when no rate is set |
| Reconciled | Formula | formula | opening + in - out - closing = 0, to the cent |
| Account month | Formula | formula | `<account no> · <yyyy-mm>`, split out of Name, for grouping the currencies of one account-month |
| Has balances | Formula | formula | false on the blocks that print no closing balance, which is what `Reconciled` cannot distinguish |
| Source PDF | Files | human | unused and empty everywhere; the block name identifies the statement |
| Transactions | Relation | formula | back-link from Transactions |

The control totals are stored numbers, not rollups from Transactions. That is
the point: they are the independent check that the import is complete, and
`verify` compares them against what Notion's own rows add up to.

`Reconciled` treats an empty balance as 0, so it reads false on the blocks that
print no closing balance; `Has balances` is how you tell those apart from a real
failure. The parser's own check is the one that matters, and it runs on every
sync.

### Period naming

`<account no> · <currency> · <yyyy-mm>`, with ` · card` appended for debit-card
statements, whose opening and closing balances stay empty because the statement
prints none and whose `Period end` is the printed "As at" date, the 9th of the
following month. Two statements producing the same name is a `duplicate-period`
problem: it means a duplicate PDF is in the tree.

## 3. Transactions

The fact table: one row per line item on a statement.

| Property | Type | Owner | Notes |
| --- | --- | --- | --- |
| Name | Title | loader | `2026-06-30 · GIRO Salary · +1,234.56` |
| Txn key | Text | loader | the content hash, see below |
| Txn date | Date | loader | posting date |
| Month | Formula | formula | `YYYY-MM` of Txn date, the grouping key for a monthly view |
| Purchase date | Date | loader | card rows only; DBS prints `29MAY` separately from the posting date |
| Account | Relation | loader | |
| Period | Relation | loader | the account-month-currency block this row came from |
| Currency | Select | loader | SGD, USD, EUR and every other currency a statement has produced |
| Amount | Number | loader | signed, negative for outflow |
| FX rate | Number | loader | the block's FX rate to SGD copied onto the row; empty on SGD rows |
| Amount SGD | Formula | formula | Amount x FX rate, or Amount when the row has no rate. An approximation, see below |
| Direction | Select | loader | In, Out |
| Bank txn type | Select | loader | the bank's own label, or the wallet parser's, see below |
| Flow class | Select | loader | External, Internal - own account, Internal - PayLah, Internal - YouTrip, Internal - Revolut, Internal - Wise |
| Payee | Relation | loader | set by the rules; never typed by hand |
| Category | Relation | loader | set by the rules; never typed by hand |
| Trip | Relation | loader | set by the rollup from the Trips Expenses rows, see section 8 |
| Card | Relation | loader | card rows only |
| Merchant raw | Text | loader | the original statement string |
| Merchant country | Select | loader | ISO alpha-3; the options are the countries seen so far, and a new one stops the run at `unknown-option`. Where a card row's country comes from is in [parsing.md](parsing.md) |
| FX original | Text | loader | e.g. `USD 5.00`, on foreign-currency rows |
| Reference | Text | loader | the bank reference only, e.g. `FT932602MB80215472` |
| Counterparty | Text | loader | the other party's name or PayNow number, from `To:` / `From:` lines |
| Memo | Text | loader | the user-typed transfer note |
| Balance after | Number | loader | audit only, do not sum |
| Need class | Rollup | formula | `show_original` of Need class via Category, so a transactions view can group on the category's class without opening Categories |

Anything not in that list is yours. The sync neither reads nor patches it.

### Bank txn type

The bank's own label on a bank row, and the parser's label on a wallet row.
These are the options Notion holds:

| Source | Options |
| --- | --- |
| DBS and POSB accounts | Advice, Cashback, Debit Card Transaction, FAST Collection, FAST Payment / Receipt, Funds Transfer, GIRO Salary, Interest Earned, Inward Telegraphic Transfer, Outward Telegraphic Transfer, Payments / Collections via GIRO, PayNow, Point-of-Sale Transaction, Salary, Schools |
| PayLah | PayLah Top-up, PayLah Payment, PayLah Send Money, PayLah Receive Money |
| YouTrip | YouTrip Top-up, YouTrip Payment, YouTrip Exchange In, YouTrip Exchange Out, YouTrip Other, YouTrip Unprinted, YouTrip Refund, YouTrip Fee, YouTrip Cashback |
| Revolut | Revolut Top-up, Revolut Payment, Revolut Transfer, Revolut Withdrawal, Revolut Exchange In, Revolut Exchange Out, Revolut Other |
| Wise | Wise Top-up, Wise Transfer, Wise Fee, Wise Conversion In, Wise Conversion Out, Wise Card Payment, Wise Other |

Six of those are in Notion ahead of the data: `YouTrip Refund`, `YouTrip Fee`,
`YouTrip Cashback`, `Revolut Other`, `Wise Card Payment` and `Wise Other`. No
statement has produced one yet, but a parser can, so `reachable_options()` in
[../server/finance/model.py](../server/finance/model.py) lists them and
`python3 server/finance.py add-options` adds them alongside the values the statements
actually carry. The first row of one of those kinds therefore loads instead of
stopping the run at `unknown-option`. A value no parser can name in advance, a
new merchant country say, still stops the run until `add-options` has run.

### Flow class

This is the property that makes the numbers mean anything. A large share of
all rows are internal: wallet top-ups and exchanges, PayLah loads, and transfers
between the owner's own accounts in pairs, one leg on each side, including a
same-account USD-to-SGD conversion. None of it is spending or income. Every
spend or income view must filter `Flow class = External`, or gross outflow reads
far too high. `python3 server/finance.py report` and the parsers both
print the split.

The PayLah, YouTrip and Revolut legs appear on both sides now, bank-side out
and wallet-side in, and cancel; each wallet's own spending stays External. On
the wallet side a leg is internal when the parser typed it as a top-up, an
exchange or a withdrawal back to the owner's own bank account. On the bank side
the test is deliberately tight: `Internal - Revolut` needs the acquirer string
(`REVOLUT**NNNN*`, the digits change) or the PayNow counterparty `REVOLUT
TECHNOLOGIES`, because a person-to-person memo saying "revolut" is somebody's
note about the app, not money leaving for the wallet.

Regular credits from an account that is not one of the statement accounts are
External. If such an account turns out to be the owner's, reclassify them and
external inflow falls by the same amount.

Money moving into the owner's own YouTrip or Revolut wallet is Internal rather
than spending. `classify_flow` in
[../server/finance/parsers/parse_statements.py](../server/finance/parsers/parse_statements.py) tags the bank
leg from the text the row prints, and the wallet parser tags the other leg. A
Wise load would be tagged the same way, but no DBS row has ever named Wise, so
there is no branch for one; the wallet-side `Wise Top-up` is already
`Internal - Wise`, and the flow rule is there for the day a bank leg appears.

Money leaving for the owner's own Tiger Brokers account is still External,
because the parser only knows the accounts in config.toml. The Transfer bucket
on the category is what keeps it out of spend totals.

### The txn_key contract

`txn_key` is the first 12 hex characters of a SHA-1 over seven parsed fields,
joined in this order and followed by an ordinal:

```
date | account_no | currency | amount | reference | merchant or counterparty | bank_txn_type
```

The ordinal is `#1` for the first row with those seven values and `#2` for a
second identical one in the same statement, which keeps duplicates distinct and
stable across a re-import.

The key is the join column between the parser and Notion, so **anything that
changes one of those seven fields renames the row**. A parser change to merchant
splitting, reference detection or transaction typing rewrites keys wholesale;
Notion then holds orphans under the old keys while the sync wants to create the
same rows under new ones. That is what the `implausible-insert` check exists to
catch, and why `--force` and `--archive-orphans` are not part of a normal month.
What does not change a key: adding a rule, renaming a payee or category, editing
anything in Notion.

### Synthetic YouTrip rows

The YouTrip export omits rows, so the parser turns every unexplained balance
jump into a `YouTrip Unprinted` transaction, dated on the next printed row and
sized by the jump, with a note naming the window. They make the period
reconcile and they are real money, but they are not statement lines, and their
key depends on the jump rather than on printed content. Re-exporting a month
therefore changes them; see [runbook.md](runbook.md).

Jumps the size of a known top-up, or matching an exchange printed in another
wallet over the same days, are `Internal - YouTrip`. The rest are External and
tracked in the open-items note, which is local-only (`notes/` is gitignored).

### Amount SGD is an approximation

`Amount SGD` multiplies a row's Amount by its FX rate, and that rate is one
number per account-month-currency block (Period balances.FX rate to SGD, copied
onto each row by the loader), taken from the latest exchange printed in that
wallet up to that month. It is copied rather than rolled up because Notion
evaluates a rolled-up formula without the formula's own rollups: with a rollup
in the formula, the Trips Spend SGD rollup summed raw Amount and counted foreign
amounts one to one.

It is not the rate of the day, and it was never printed against the individual
row. It is good enough to compare a trip's spending against a budget and wrong
for anything that needs the exact rate. Rows already in SGD carry Amount
unchanged.

## 4. Payees

Collapses the raw statement strings into stable entities.

| Property | Type | Owner | Notes |
| --- | --- | --- | --- |
| Name | Title | loader | Grab, HelloRide, an employer, a friend |
| Kind | Select | loader | Merchant, Person, Employer, Institution |
| Raw patterns | Text | loader | every glob mapping to this payee, joined with ` \| ` |
| Account no. | Text | loader | the payee's bank account number when a statement prints one |
| Country | Select | loader | ISO alpha-3 |
| Recurring | Checkbox | loader | ticked when a rule marks the payee `recurring`, cleared when none does |
| Default category | Relation | loader | see below |
| Transactions | Relation | formula | back-link from Transactions |
| Last seen | Rollup | formula | latest Txn date via Transactions; a Recurring payee whose Last seen is months old has stopped billing, and the hub's Fixed costs view sorts on it |
| Txn count | Rollup | formula | count of Name via Transactions, for sorting |
| Categories seen | Rollup | formula | count of unique Category via Transactions. More than one means the payee spans categories and the default is only the commonest |

**Every row here is generated.** `python3 server/finance.py payees` reads the rules and
creates or patches one page per distinct payee name, changing only the fields
that differ. Edits made in Notion are overwritten on the next run, so change the
rules instead. The row count is therefore exactly the number of distinct payee
names in `rules/`; a row the rules no longer name is stale and should be
trashed.

### The default category rule

A payee's default category is the category carrying most of that payee's matched
rows, ties broken by the larger total amount and then by the payee's first rule.
It is derived from the data, not declared, so a payee whose rows move to another
category follows on the next run. A payee with no matched rows keeps the
category its first rule names.

## 5. Categories

Twenty-nine rows, typed by hand; the mirror adds the row for a category the
app gained since (below).

| Property | Type | Owner | Notes |
| --- | --- | --- | --- |
| Name | Title | human | Rent, Groceries, Transport, Salary, Eating out, Insurance |
| Bucket | Select | human | Income, Spend, Transfer |
| Group | Select | human | Fixed, Variable, Savings |
| Need class | Select | human | Need, Want, Savings, Investment, Income, Transfer. May be left empty, see below |
| Sort order | Number | human | the order a category list should read in, in tens so a new category slots between two others: 10 to 70 income, 80 to 140 needs, 150 to 225 wants, 230 investments, 240 to 260 transfers |
| Transactions | Relation | formula | back-link |
| Payees | Relation | formula | back-link of Default category |
| Budgets | Relation | formula | back-link from Budgets |

The record (`kopifund.db`) keeps two things here that Notion does not. Each
category sits in a bucket on the app's Categories page (`category_group`, with
a kind of spend, income or transfer that its Bucket follows; SPEC.md,
"Categories and buckets"), and the app may renumber Sort order to the page's
order. Neither is mirrored, and the mirror never patches a Categories row
Notion already has. A category renamed or deleted in the app keeps its old name
in the record's `category_alias`, so the rules that still say it keep loading,
and the mirror keeps sending those rows to the Notion row of that old name.

The mirror creates a Categories row Notion lacks when a row it is about to
write links to it and the record holds the category (G13). It writes Name,
Bucket, Group, Need class and Sort order from the record, before any
transaction or budget links to the new row; a property or select option this
database does not have is left out and named in the run's output, never added.
`sync --dry-run`, `doctor` and `report` say "would create N categories in
Notion: ..." and write nothing. `finance.py payees` does the same for a payee's
default category. So Transport (public) and Transport (private), or a category
you add on the app's Categories page, reach Notion on the next `sync` with
nothing to do by hand.

`doctor` validates every category the rules name against the live Categories
rows and the record, so a name neither holds (a typo in a rule, or the old name
of a category renamed in the app with no Notion row of that name) stops the run
at `missing-category` before anything is written.
`KNOWN_CATEGORIES` in `server/finance/parsers/categorise.py` is only the offline copy `--lint`
falls back on, and `--categories FILE` overrides it.

### Need class

A second axis on Categories, beside Bucket and Group: whether a category is a
Need, a Want, Savings, Investment, Income or a Transfer. Bucket says what kind
of movement it is, Need class says whether it was a choice. It is typed by hand,
one value per category, and it is what a monthly budget review sorts on.

Two things read it. The rollup copies it onto every Budgets row, and Income is
the class that flips a budget's sign (section 7). The needs-wants-savings stat
groups spending on it, and a category with no Need class lands in that stat's
`unclassified` line, which is what `Unidentified` is for: its rows are the ones
nobody has explained yet, and they should not be counted as a need or a want.

## 6. Cards

Eight rows, one per card the owner has held.

| Property | Type | Owner | Notes |
| --- | --- | --- | --- |
| Name | Title | human | `DBS Visa Debit`, `YouTrip Physical` |
| Last 4 | Text | human | the only part of the number the statements print in full; the key `[cards]` in config.toml uses |
| Network | Select | human | Visa, Mastercard, read from the masked number (4xxx Visa, 5xxx Mastercard) |
| Type | Select | human | Debit, Credit |
| Accounts | Relation | human | the accounts the card draws on; the two DBS cards have drawn on POSB and Multiplier both, the wallet cards on one wallet each |
| State | Select | human | Active, Cancelled |
| Active period | Date | human | used as a range: start = first transaction seen, end = cancellation |
| Transactions | Relation | formula | back-link of Card on Transactions |

Only the cards that appear on a statement need a `[cards]` entry: the DBS pair
and the Revolut card today. A card the config does not know stops the run at
`unknown-card` before anything is written.

## 7. Budgets

One row per category and month that has External rows. You type Planned; the
rollup keeps Actual current and never touches anything else.

| Property | Type | Owner | Notes |
| --- | --- | --- | --- |
| Name | Title | loader | `<Category> · <yyyy-mm>`, e.g. `Groceries · 2026-08`. This is the key the rollup matches on |
| Category | Relation | loader | to Categories, dual with `Categories.Budgets` |
| Month | Text | loader | `yyyy-mm`, the same month the Name carries, so a view can group on it |
| Planned | Number | human | your budget. Absent from `BUDGET_TYPES`, so no code path can write or clear it |
| Actual | Number | loader | the month's spending in that category, see below |
| Need class | Select | loader | the category's Need class copied onto the row by the rollup, so a view can group needs, wants and savings; a rollup cannot be grouped on |
| Current | Checkbox | loader | ticked on the rows of the calendar month the last rollup or sync ran in (a seeded month ahead included), cleared everywhere else, so the hub page's budget view can filter on it. A view filter cannot read a formula, hence a checkbox |
| Variance | Formula | formula | Planned - Actual, empty while Planned is empty |
| Used | Formula | formula | Actual / Planned, empty while Planned is empty |

### How Actual is computed

Actual sums the SGD value of the rows with a category and `Flow class =
External`, computed in Python in cents by the record's rule (`v_txn`,
`sync.sgd_minor`): Amount times the row's FX rate when the block prints one, an
SGD row's Amount as it is, and nothing for a foreign row whose block prints no
rate. Such a row has no SGD value (SPEC), so Actual leaves it out and the rollup
says how many it left out, where the Notion `Amount SGD` formula would count it
one to one. The CSV snapshot (`analyse --from-csv`) gives the same rows an empty
`amount_sgd_minor` and a note.

The sign is flipped so Planned and Actual compare like for like. On a spending
category Actual is positive when money went out. On a category whose `Need
class` is Income it is positive when money came in. That is the only thing
`Need class = Income` changes mechanically, and it is read live from Categories
on every run.

A category and month with no External rows gets no row. The rollup counts a
Budgets row whose category and month no longer have any rows but does not delete
it, because it may be a budget you typed ahead of the statements.

## 8. Trips Expenses

One row per trip. The rollup reads them and sets `Trip` on the transactions
they claim. The database was once called `Trips`; it was renamed so it does not
read as the owner's trip planner, the separate `Trips Database` (with Status,
Companions, Activities, Places). The two stay distinct:
planning there, money here, joined by `Trip plan`. Dates and Countries are not
typed here: Countries rolls up from the planner row and Dates is copied from it
by the rollup step (a rollup of the planner's own date range would display as a
duration, not as dates). A row with no `Trip plan` keeps whatever Dates it has
typed.

| Property | Type | Owner | Notes |
| --- | --- | --- | --- |
| Name | Title | human | `Long stay abroad`, `Penang, Sep 2026` |
| Dates | Date | loader | used as a range, copied by the rollup from the planner rows `Trip plan` names: earliest start to latest end, a planner day trip (no end) as one day. Overwritten on every rollup while `data_sources.trip_plans` is set, so edit the planner, not this. A trip with no plan keeps its typed dates |
| Notes | Text | human | free text |
| Extra keys | Text | human | txn keys, comma or space separated, that belong to this trip whatever their date: a booking paid ahead (a ticket bought a week before the trip). The rollup links them like any other claimed row; an internal or synthetic row is still never linked |
| Trip plan | Relation | human | the matching row(s) in the owner's `Trips Database`; its back-link there is `Expenses`. Set by hand: the loader never reads or writes it. One expense row may point at several planner rows (a trip in three legs) and a planner row an expense row does not cover (a day out inside a longer stay) has none |
| Countries | Rollup | formula | `show_original` of Countries via Trip plan, the planner's full names. The loader maps them to ISO alpha-3 through `COUNTRY_ISO` in server/finance/sync.py and prints a note for a name it does not know |
| Transactions | Relation | formula | back-link of Trip on Transactions |
| Spend SGD | Rollup | formula | sum of Amount SGD via Transactions; negative, because spend is negative |
| Rows | Rollup | formula | count of Name via Transactions |
| Spend | Formula | formula | `-Spend SGD`, the trip's total as a positive number |
| Days | Formula | formula | inclusive length of Dates |
| Per day | Formula | formula | Spend divided by Days, to the cent |

### Which rows a trip claims

A transaction is on a trip when its `Txn date` falls inside the trip's dates,
extended by `SETTLEMENT_DAYS` (3) after the end because the dates are travel
days from the planner and card rows post a day or three later, **and** at least
one of these says it was spent away from home:

1. its merchant country is a country other than SGP, or
2. it sits on a non-SGD wallet of a wallet account and does not name Singapore
   itself, since a YouTrip, Revolut or Wise balance in a foreign currency is
   otherwise only ever spent abroad, or
3. the trip's Countries list the row's own country, which is how a trip inside
   Singapore or a day trip home claims its rows.

Rule 2 carries that exception because a leftover foreign balance can be spent
at home after a trip, and those rows print Singapore as the country.

Only External, non-synthetic rows are ever linked: wallet exchanges, top-ups and
balance-jump fillers are not trip spending, and linking them made every trip's
Spend net to zero. A row with no merchant country at all is left alone. An empty
country is missing evidence, not evidence of being abroad, and a months-long
stay abroad would otherwise swallow every salary and rent row it spans.

Trips nest: short side trips can sit inside a long stay abroad. When several
trips cover the same day, the one that names the
row's country wins, then the shorter trip, then the earlier one. A row that is
on no trip has its `Trip` relation cleared, so deleting or shortening a trip is
enough to undo an assignment.

## 9. The hub page: Metrics and Months

The Expenses hub page (`DBS Balance Sheets`) shows numbers, not databases: Notion
draws no charts on the free plan, so `finance.py analyse` writes two small
inline tables there from the analytics results
([../server/analytics/publish.py](../server/analytics/publish.py)), keyed on Name like
Budgets, zero writes when nothing moved. Everything else on the page is a linked
view of the databases above.

| Table | Property | Type | Owner | Notes |
| --- | --- | --- | --- | --- |
| Metrics | Name | Title | loader | the figure's label, e.g. `Cash, all accounts` |
| Metrics | Value | Number | loader | the figure itself, rounded to the cent |
| Metrics | Unit | Select | loader | SGD, months, per cent, rows |
| Metrics | As of | Text | loader | the day or month the figure is true of |
| Metrics | Note | Text | loader | one line of context under the number |
| Metrics | Stat | Text | loader | the analytics result the row came from. A row with no Stat is yours and is never touched |
| Metrics | Order | Number | loader | page order: each metric's fixed place in `publish.METRIC_ORDER`, whatever else is published, so a metric with no figure leaves a gap rather than renumbering the rest |
| Months | Name | Title | loader | `YYYY-MM`, one per month the stats cover |
| Months | Income, Spending, Investing, Net, Cash | Number | loader | the monthly-cashflow and savings-runway series; Net is after the investing transfer |
| Months | Partial | Checkbox | loader | a month a statement is still missing for |
| Months | Phase | Select | loader | the phase the month falls in: a named stretch of time spent in one place, such as a stay abroad or the months at home, from `[analytics] phases` in config.toml; empty when none is set |

Four of the Metrics rows are worth reading twice, because each is measured on a
different day from the one beside it.

| Row | What it is |
| --- | --- |
| `Investments, market value` | the Tiger Brokers portfolio in SGD, as of the latest annual statement's last day; the note carries what was contributed and the gain. Absent, rather than zero, while there is no statement to read |
| `Net worth, cash plus investments` | that figure plus the cash row. The two sides are measured on different days, so As of is the later of them and the note names both |
| `CPF, all three accounts` | the CPF balance in SGD as of 31 December of the newest yearly statement; the note carries the OA, SA and MA split and says it is locked savings rather than cash. Absent, rather than zero, while there is no statement to read |
| `Net worth, including CPF` | cash plus investments plus CPF. The three sides are measured on three different days, so As of is the latest of them and the note names all three. Absent while there is no brokerage statement, because investments would then be a guess |

The page's callout is rewritten by every `finance.py sync` with one line: when,
rows and blocks, clean or not, rows needing a rule, and how far each account is
loaded (`[hub] status_block` in config.toml).

The app (`make serve`) computes the same Metrics rows with `metrics_from()` for
its home page tiles, rather than computing any headline figure of its own.

## 10. The rollup step

`sync` runs the rollup after the upsert and before verify, so every row it has
just created already carries its Trip and is counted in its month's budget. It
also runs on its own:

```bash
python3 server/finance.py rollup --dry-run
python3 server/finance.py rollup
```

It reads Notion rather than the plan, so it is safe against a database the sync
has not touched this session, and it creates and patches nothing in
Transactions except the `Trip` relation. Five things it writes, in this order:

| Target | What |
| --- | --- |
| Trips Expenses.Dates | copied from the planner rows `Trip plan` names, while `data_sources.trip_plans` is set; a trip with no plan is left alone |
| Transactions.Trip | set from the Trips Expenses rows, cleared on a row that is on no trip |
| Budgets | one row per category and month, created when missing, Actual refreshed when it moved |
| Accounts | Statements from, Statements to and Data coverage, for the accounts named in config.toml |
| Payees.Recurring | ticked from the rules' `recurring` column, cleared on a payee no rule marks |

Every one of them is derived, so a rerun with nothing new issues zero writes.

## 11. The record (kopifund.db)

The tables below are what `server/finance/db.py` builds, described from SQLite's own
answers (`PRAGMA table_info`, `PRAGMA foreign_key_list`); SPEC.md ("The
record") says why they are shaped this way. A column whose values Python lists
(a bucket's kind, a need class, a payee's kind, an upload step's status) has a
CHECK built from that list (`enums()` in server/finance/db.py), so the two cannot
drift. A link marked CASCADE or SET NULL is followed by SQLite itself when the
row it points at is deleted; any other link refuses the delete while a row
still points at it.

<!-- sqlite-schema: begin, written by docs/gen_schema.py -->

Schema version 7 (`PRAGMA user_version`). Every table is STRICT.

### `currency`

| Column | Type | Not null | Default | Key | Links to | On delete | One of |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `code` | TEXT | yes |  | 1 |  |  |  |
| `exponent` | INTEGER | yes | 2 |  |  |  |  |

### `account`

| Column | Type | Not null | Default | Key | Links to | On delete | One of |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `id` | INTEGER |  |  | 1 |  |  |  |
| `name` | TEXT | yes |  |  |  |  |  |
| `account_no` | TEXT |  |  |  |  |  |  |
| `institution` | TEXT |  |  |  |  |  |  |
| `class` | TEXT |  |  |  |  |  |  |
| `subclass` | TEXT |  |  |  |  |  |  |
| `base_currency` | TEXT |  |  |  |  |  |  |

### `card`

| Column | Type | Not null | Default | Key | Links to | On delete | One of |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `id` | INTEGER |  |  | 1 |  |  |  |
| `name` | TEXT | yes |  |  |  |  |  |
| `last4` | TEXT | yes |  |  |  |  |  |
| `network` | TEXT |  |  |  |  |  |  |
| `type` | TEXT |  |  |  |  |  |  |
| `state` | TEXT |  |  |  |  |  |  |

### `category_group`

| Column | Type | Not null | Default | Key | Links to | On delete | One of |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `id` | INTEGER |  |  | 1 |  |  |  |
| `name` | TEXT | yes |  |  |  |  |  |
| `kind` | TEXT | yes |  |  |  |  | spend, income, transfer |
| `sort_order` | REAL |  |  |  |  |  |  |
| `icon` | TEXT |  |  |  |  |  |  |
| `colour` | TEXT |  |  |  |  |  |  |

### `category`

| Column | Type | Not null | Default | Key | Links to | On delete | One of |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `id` | INTEGER |  |  | 1 |  |  |  |
| `name` | TEXT | yes |  |  |  |  |  |
| `bucket` | TEXT |  |  |  |  |  | Spend, Income, Transfer |
| `grp` | TEXT |  |  |  |  |  | Fixed, Variable, Savings |
| `need_class` | TEXT |  |  |  |  |  | Need, Want, Savings, Investment, Income, Transfer |
| `sort_order` | REAL |  |  |  |  |  |  |
| `group_id` | INTEGER |  |  |  | `category_group.id` |  |  |

### `category_alias`

| Column | Type | Not null | Default | Key | Links to | On delete | One of |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `name` | TEXT | yes |  | 1 |  |  |  |
| `category_id` | INTEGER | yes |  |  | `category.id` |  |  |
| `how` | TEXT | yes | 'rename' |  |  |  |  |

### `budget`

| Column | Type | Not null | Default | Key | Links to | On delete | One of |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `category_id` | INTEGER | yes |  | 1 | `category.id` | CASCADE |  |
| `month` | TEXT | yes |  | 2 |  |  |  |
| `planned_minor` | INTEGER |  |  |  |  |  |  |

### `trip`

| Column | Type | Not null | Default | Key | Links to | On delete | One of |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `id` | INTEGER |  |  | 1 |  |  |  |
| `name` | TEXT | yes |  |  |  |  |  |
| `start_date` | TEXT |  |  |  |  |  |  |
| `end_date` | TEXT |  |  |  |  |  |  |
| `notes` | TEXT | yes | '' |  |  |  |  |

### `trip_country`

| Column | Type | Not null | Default | Key | Links to | On delete | One of |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `trip_id` | INTEGER | yes |  | 1 | `trip.id` | CASCADE |  |
| `code` | TEXT | yes |  | 2 |  |  |  |
| `position` | INTEGER | yes |  |  |  |  |  |

### `trip_pin`

| Column | Type | Not null | Default | Key | Links to | On delete | One of |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `trip_id` | INTEGER | yes |  | 1 | `trip.id` | CASCADE |  |
| `txn_key` | TEXT | yes |  | 2 |  |  |  |

### `plan`

| Column | Type | Not null | Default | Key | Links to | On delete | One of |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `key` | TEXT | yes |  | 1 |  |  |  |
| `value` | TEXT | yes |  |  |  |  |  |

### `setting`

| Column | Type | Not null | Default | Key | Links to | On delete | One of |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `key` | TEXT | yes |  | 1 |  |  |  |
| `value` | TEXT | yes |  |  |  |  |  |

### `txn_marked_fine`

| Column | Type | Not null | Default | Key | Links to | On delete | One of |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `txn_key` | TEXT | yes |  | 1 |  |  |  |
| `marked_at` | TEXT | yes |  |  |  |  |  |

### `series_override`

| Column | Type | Not null | Default | Key | Links to | On delete | One of |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `id` | INTEGER |  |  | 1 |  |  |  |
| `series_id` | TEXT |  |  |  |  |  |  |
| `group_kind` | TEXT | yes |  |  |  |  | payee, text, amount |
| `group_name` | TEXT | yes |  |  |  |  |  |
| `recurring` | INTEGER |  |  |  |  |  |  |
| `amount_minor` | INTEGER |  |  |  |  |  |  |
| `cadence` | TEXT |  |  |  |  |  | weekly, fortnightly, twice-monthly, monthly, two-monthly, quarterly, half-yearly, yearly |
| `day` | INTEGER |  |  |  |  |  |  |
| `ended_on` | TEXT |  |  |  |  |  |  |
| `saved_at` | TEXT | yes |  |  |  |  |  |

### `series_override_txn`

| Column | Type | Not null | Default | Key | Links to | On delete | One of |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `override_id` | INTEGER | yes |  | 1 | `series_override.id` | CASCADE |  |
| `txn_key` | TEXT | yes |  | 2 |  |  |  |

### `pending_txn`

| Column | Type | Not null | Default | Key | Links to | On delete | One of |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `id` | INTEGER |  |  | 1 |  |  |  |
| `import_id` | TEXT | yes |  |  |  |  |  |
| `device_id` | TEXT | yes | '' |  |  |  |  |
| `txn_date` | TEXT | yes |  |  |  |  |  |
| `amount_minor` | INTEGER | yes |  |  |  |  |  |
| `currency` | TEXT | yes |  |  |  |  |  |
| `payee` | TEXT | yes | '' |  |  |  |  |
| `state` | TEXT | yes | 'waiting' |  |  |  |  |
| `matched_txn_key` | TEXT |  |  |  |  |  |  |
| `rejected` | TEXT | yes | '[]' |  |  |  |  |
| `created_at` | TEXT | yes |  |  |  |  |  |

### `statement_file`

| Column | Type | Not null | Default | Key | Links to | On delete | One of |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `id` | INTEGER |  |  | 1 |  |  |  |
| `path` | TEXT | yes |  |  |  |  |  |
| `sha256` | TEXT | yes |  |  |  |  |  |
| `parser` | TEXT | yes | '' |  |  |  |  |
| `original_name` | TEXT | yes | '' |  |  |  |  |
| `first_seen` | TEXT | yes |  |  |  |  |  |
| `superseded` | INTEGER | yes | 0 |  |  |  |  |

### `payee`

| Column | Type | Not null | Default | Key | Links to | On delete | One of |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `id` | INTEGER |  |  | 1 |  |  |  |
| `name` | TEXT | yes |  |  |  |  |  |
| `kind` | TEXT |  |  |  |  |  | Merchant, Person, Employer, Institution |
| `country` | TEXT |  |  |  |  |  |  |
| `recurring` | INTEGER | yes | 0 |  |  |  |  |
| `account_no` | TEXT | yes | '' |  |  |  |  |

### `period`

| Column | Type | Not null | Default | Key | Links to | On delete | One of |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `id` | INTEGER |  |  | 1 |  |  |  |
| `name` | TEXT | yes |  |  |  |  |  |
| `account_id` | INTEGER |  |  |  | `account.id` |  |  |
| `account_no` | TEXT | yes |  |  |  |  |  |
| `currency` | TEXT | yes |  |  | `currency.code` |  |  |
| `period_end` | TEXT |  |  |  |  |  |  |
| `opening_minor` | INTEGER |  |  |  |  |  |  |
| `closing_minor` | INTEGER |  |  |  |  |  |  |
| `total_in_minor` | INTEGER |  |  |  |  |  |  |
| `total_out_minor` | INTEGER |  |  |  |  |  |  |
| `fx_to_sgd_e9` | INTEGER |  |  |  |  |  |  |
| `parser_reconciled` | INTEGER |  |  |  |  |  |  |
| `n_txns` | INTEGER |  |  |  |  |  |  |
| `note` | TEXT | yes | '' |  |  |  |  |
| `source` | TEXT | yes | '' |  |  |  |  |
| `archived_at` | TEXT |  |  |  |  |  |  |
| `statement_file_id` | INTEGER |  |  |  | `statement_file.id` | SET NULL |  |

### `txn`

| Column | Type | Not null | Default | Key | Links to | On delete | One of |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `id` | INTEGER |  |  | 1 |  |  |  |
| `txn_key` | TEXT | yes |  |  |  |  |  |
| `period_id` | INTEGER |  |  |  | `period.id` |  |  |
| `account_id` | INTEGER |  |  |  | `account.id` |  |  |
| `account_no` | TEXT | yes |  |  |  |  |  |
| `currency` | TEXT | yes |  |  | `currency.code` |  |  |
| `txn_date` | TEXT | yes |  |  |  |  |  |
| `purchase_date` | TEXT |  |  |  |  |  |  |
| `amount_minor` | INTEGER | yes |  |  |  |  |  |
| `balance_after_minor` | INTEGER |  |  |  |  |  |  |
| `direction` | TEXT | yes | '' |  |  |  |  |
| `bank_txn_type` | TEXT | yes | '' |  |  |  |  |
| `flow_class` | TEXT | yes | '' |  |  |  |  |
| `merchant_raw` | TEXT | yes | '' |  |  |  |  |
| `merchant_country` | TEXT | yes | '' |  |  |  |  |
| `card_id` | INTEGER |  |  |  | `card.id` |  |  |
| `card_last4` | TEXT | yes | '' |  |  |  |  |
| `fx_original` | TEXT | yes | '' |  |  |  |  |
| `reference` | TEXT | yes | '' |  |  |  |  |
| `counterparty` | TEXT | yes | '' |  |  |  |  |
| `memo` | TEXT | yes | '' |  |  |  |  |
| `is_synthetic` | INTEGER | yes | 0 |  |  |  |  |
| `payee_id` | INTEGER |  |  |  | `payee.id` |  |  |
| `category_id` | INTEGER |  |  |  | `category.id` | SET NULL |  |
| `rule` | TEXT | yes | '' |  |  |  |  |
| `trip_id` | INTEGER |  |  |  | `trip.id` | SET NULL |  |
| `source` | TEXT | yes | '' |  |  |  |  |
| `first_seen` | TEXT | yes |  |  |  |  |  |
| `last_seen` | TEXT | yes |  |  |  |  |  |
| `archived_at` | TEXT |  |  |  |  |  |  |

### `txn_key_alias`

| Column | Type | Not null | Default | Key | Links to | On delete | One of |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `old_key` | TEXT | yes |  | 1 |  |  |  |
| `txn_id` | INTEGER | yes |  |  | `txn.id` |  |  |

### `broker_statement`

| Column | Type | Not null | Default | Key | Links to | On delete | One of |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `id` | INTEGER |  |  | 1 |  |  |  |
| `file` | TEXT | yes |  |  |  |  |  |
| `period_from` | TEXT |  |  |  |  |  |  |
| `period_to` | TEXT |  |  |  |  |  |  |
| `base_currency` | TEXT |  |  |  |  |  |  |
| `data` | TEXT | yes |  |  |  |  |  |

### `cpf_statement`

| Column | Type | Not null | Default | Key | Links to | On delete | One of |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `year` | INTEGER |  |  | 1 |  |  |  |
| `file` | TEXT | yes |  |  |  |  |  |
| `as_of` | TEXT |  |  |  |  |  |  |
| `data` | TEXT | yes |  |  |  |  |  |

### `cpf_rejected`

| Column | Type | Not null | Default | Key | Links to | On delete | One of |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `year` | INTEGER | yes |  | 1 |  |  |  |
| `file` | TEXT | yes |  | 2 |  |  |  |
| `off_oa_minor` | INTEGER | yes |  |  |  |  |  |
| `off_sa_minor` | INTEGER | yes |  |  |  |  |  |
| `off_ma_minor` | INTEGER | yes |  |  |  |  |  |

### `sync_run`

| Column | Type | Not null | Default | Key | Links to | On delete | One of |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `id` | INTEGER |  |  | 1 |  |  |  |
| `kind` | TEXT | yes |  |  |  |  |  |
| `started_at` | TEXT | yes |  |  |  |  |  |
| `finished_at` | TEXT |  |  |  |  |  |  |
| `status` | TEXT |  |  |  |  |  |  |
| `inserted` | INTEGER | yes | 0 |  |  |  |  |
| `updated` | INTEGER | yes | 0 |  |  |  |  |
| `archived` | INTEGER | yes | 0 |  |  |  |  |
| `note` | TEXT | yes | '' |  |  |  |  |

### `upload_attempt`

| Column | Type | Not null | Default | Key | Links to | On delete | One of |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `id` | INTEGER |  |  | 1 |  |  |  |
| `origin` | TEXT | yes | 'upload' |  |  |  | upload, rules, transactions, app |
| `started_at` | TEXT | yes |  |  |  |  |  |

### `upload_step`

| Column | Type | Not null | Default | Key | Links to | On delete | One of |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `id` | INTEGER |  |  | 1 |  |  |  |
| `attempt_id` | INTEGER | yes |  |  | `upload_attempt.id` |  |  |
| `step` | TEXT | yes |  |  |  |  | upload, check, load, mirror |
| `started_at` | TEXT | yes |  |  |  |  |  |
| `finished_at` | TEXT | yes |  |  |  |  |  |
| `status` | TEXT | yes |  |  |  |  | ok, duplicate, refused, problems, blocked, failed, error |
| `summary` | TEXT | yes | '' |  |  |  |  |
| `input` | TEXT | yes | '{}' |  |  |  |  |
| `output` | TEXT | yes | '{}' |  |  |  |  |

Views: `v_budget`, `v_missing_fx`, `v_period`, `v_txn`.

Triggers: `category_bucket_insert`, `category_bucket_update`, `category_group_kind`.

Indexes: `pending_txn_state_idx`, `series_override_payee_idx`, `txn_category_idx`, `txn_date_idx`, `txn_trip_idx`, `upload_step_attempt_idx`.

<!-- sqlite-schema: end -->
