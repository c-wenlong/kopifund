# Roadmap

Goals in build order. Each goal number is used in commit messages and in source
comments (`// G5: ...`) so a search for `G5` finds everything that goal touched.

| Goal | What | Done when | Status |
| --- | --- | --- | --- |
| G0 | Repo bootstrap | README, SPEC, ROADMAP, AGENTS and CLAUDE are in place | Done |
| G1 | Scope reset | The docs describe a Singapore finance tracker built on the existing pipeline, with the owner's providers as the v1 statement list | Done |
| G2 | Front-end stack | SPEC names the web framework and the reason, AGENTS has real setup and test commands, a hello-world PWA is served from localhost by the Python backend | Done |
| G3 | Move the pipeline in | Parsers, rules, the Notion sync, analytics and their tests live here and pass with `cd server && python3 -m unittest discover tests`; parser tests run on scrubbed fixtures in the repo, not real statement text; a migrate command moves finance-tracking's data into `~/.kopifund` exactly as SPEC's migration table says (copied files verified by sha256, skipped files listed, the original folder untouched); the pipeline reads only from the data folder; the Notion token is read from the Keychain; finance-tracking is archived with a pointer here | Done |
| G4 | Local record | `kopifund.db` (SQLite) follows SPEC's record section: every table the Notion databases hold, plus statement files, the brokerage, CPF and plan; ids not names; money as integer cents; keys imported verbatim; each sync reads the Notion-owned fields back, writes SQLite, then mirrors to Notion only when Notion is configured; analytics reads SQLite instead of the daily snapshot JSON; the owner's existing Notion data is imported once and nothing is lost | Done |
| G5 | Dashboard in the app | Every stat card renders in the PWA, read from SQLite through the API. The old analytics page is retired | Done |
| G6 | Monthly run in the app | Dropping PDFs in, the dry run report (problems, to add, to change, needs a rule) and the real sync all work from the browser | Done |
| G7 | Rules in the app | A row under "needs a rule" can become a rule from the browser, and the rules lint runs on save | Done |
| G8 | Planning in the app | Budgets, need classes, trips, the forward view's plan and one-off payments are edited in the browser and saved to SQLite | Done |
| G9 | Export | The ledger and every stat result export as CSV and JSON | Done |
| G10 | Other users | A new user can set up Kopifund without editing code or owning a Notion workspace: their own accounts and plan, with nothing about the owner in tracked files | Done |
| G11 | More providers | At least one statement layout the owner does not use (OCBC, UOB or a credit card) parses and balances | Done |
| G12 | AI payee suggestions | After an upload check, the payees no rule names come back from a configurable model (Anthropic or any OpenAI-style provider) as suggested payees and categories; ticked ones save as personal rules in one step from the upload page; only merchant text and a rough amount are sent | Done |
| G13 | UI pass | Every chart carries its figures in the chart and its tooltip rather than in prose, every bar opens the rows behind it, and every page in the list below is rebuilt or ticked off | Done |
| G14 | Net worth projection inputs | Pay (employment and side income, with bonuses), investing steps, spending and the assumptions are typed, previewed and saved on the Net worth page, shared with Plan's forward view; net worth (cash, Tiger Brokers and CPF) is drawn forward with take-home after CPF, income tax and self-employed MediSave; nothing about the owner is hardcoded | Done |
| G15 | Scam checks | After each load, transfers that look like a scam are flagged on Home's "Needs your eye" card and in a Transactions filter, each with the reason it fired; a row marked fine is saved and never flagged again; every threshold is a saved setting; the first two flags in the list below ship first | Done |
| G16 | Recurring you can trust | A missed, early, extra, repriced or lagging payment shows the right status on Recurring and Home; every common schedule is found, more than one per payee; a series opens its own view on Recurring and can be edited there | Done |
| G17 | One set of types | Every entity the API sends has one Python type that generates the TypeScript type and a test fails on drift; plan money crosses as cents; writes use ids; the SQLite schema enforces the links and enums | Done |
| G18 | v3 interface | Every kind of thing (bucket, bank, transaction kind, status) carries an icon; buckets keep their own colour; Net worth's inputs open in a dialog; trips have a bubble view; alerts live in the sidebar; charts and tables share one look | Done |
| G19 | Server and repo structure | One route table on each side, serve.py only routes, the parsers are a package, analytics depends on finance and never the reverse | Done |
| G20 | Stats in cents | Every stat reads the record's integer cents, adds and averages in cents with one rounding rule, and writes cents into its result; nothing between the record and the screen holds money as float dollars; the hub page converts once, at its own edge | Done |
| G21 | More banks | Someone who banks with UOB, as well as DBS, POSB or OCBC, can pick their banks in Setup and drop their statements in, and every one parses and balances; a statement from a bank no layout knows, Trust, GXS and MariBank included, is named as not supported and can be turned into a sample text for building its layout; each layout says whether a real statement has checked it or only a public sample; adding a bank is one parser module, its fixture and its tests | Done |
| G22 | Copilot | A panel beside every page answers questions about the record in words and charts, through read-only tools over `kopifund.db`; exact rows leave the machine only after the owner turns that on in Settings; the model has no tool that writes, and a change it suggests is a card the owner confirms | Done |
| G23 | Desktop app | The owner opens Kopifund from the Dock like any Mac app: no terminal, no command, no browser tab; the app starts and stops the server itself, every page works in its window, and later milestones make it something a friend can install | Not started |
| G24 | Alerts to YNAB | A bank alert reaching the phone (bank SMS or email through a Shortcuts automation, an Apple Pay transaction through the Wallet trigger, the share sheet or a paste) is matched against the owner's own patterns, queued for review, and lands in YNAB as an unapproved transaction the owner confirms there | Done |
| G25 | Phone link | The iOS app pairs with the server over Tailscale and reads the record's accounts and transactions; an alert row a loaded statement row already covers is marked instead of offered for posting; approved alert rows can land in the record as pending rows the next load confirms | Done |
| G26 | Interactive visual dashboard | The owner composes their own board: drag-and-drop widgets over the record (state, change, flow, allocation, goals, scenarios, game pot) with layouts saved locally; epic [#4](https://github.com/c-wenlong/kopifund/issues/4), children [#5](https://github.com/c-wenlong/kopifund/issues/5) to [#15](https://github.com/c-wenlong/kopifund/issues/15) | Not started |
| G27 | SRS tracked account | An SRS account tracked like CPF: a new account kind and statement layout, contributions against the yearly cap, tax relief by year of assessment, holdings inside SRS and SRS in net worth ([issue #16](https://github.com/c-wenlong/kopifund/issues/16)) | Not started |

## G13: the UI pass

A review of every page found broken charts (bar charts with no axes, horizontal
bars collapsed onto one spot) and figures buried in paragraphs. The fix is a
page at a time, each its own PR.

Done:

- [x] Chart foundation: axes, hover values, click-through to transactions, prose behind Details ([#17](https://github.com/c-wenlong/kopifund-finanace/pull/17))
- [x] Date of birth on Your profile, for the CPF projection ([#18](https://github.com/c-wenlong/kopifund-finanace/pull/18))
- [x] Rules page: grouped by payee, search, problems inline, edit with a live preview, undo ([#19](https://github.com/c-wenlong/kopifund-finanace/pull/19))
- [x] Categories page with buckets; Plan grouped by bucket ([#20](https://github.com/c-wenlong/kopifund-finanace/pull/20))
- [x] Transport split into public and private ([#21](https://github.com/c-wenlong/kopifund-finanace/pull/21))
- [x] Spending explorer: range, Singapore only or with overseas, by bucket, category or payee ([#22](https://github.com/c-wenlong/kopifund-finanace/pull/22))
- [x] Net worth over time; Tiger and CPF drawn forward ([#23](https://github.com/c-wenlong/kopifund-finanace/pull/23))
- [x] Trips: country flags, a trip opens its transactions, cost per day in place of the chart with long stays apart, and the trips table and the travel stat agreeing on a long stay's total ([#25](https://github.com/c-wenlong/kopifund-finanace/pull/25))
- [x] Recurring: lists in place of the bar chart (coming up this month with expected date and paid, due, late or different amount; then active; then stopped; payees marked recurring with no regular spacing) ([#26](https://github.com/c-wenlong/kopifund-finanace/pull/26))
- [x] Home: bars coloured by bucket with the "Spent in" tile as the last bar, "Coming up" in place of this month's recurring list, a "Needs your eye" card, a one-line change on each tile ([#27](https://github.com/c-wenlong/kopifund-finanace/pull/27))
- [x] Plan: labelled plan form with units, CPF rate as a percentage, spending rates in words, no raw placeholders, long notes cut short; the forward view's low point and any month under zero marked on the chart, the month's figures on hover
- [x] Profile and stat text: bank logos and friendly names, account numbers masked to the last four digits on Your profile and in every stat, accounts with no statement yet apart, statement folders by the upload picker's names ([#30](https://github.com/c-wenlong/kopifund-finanace/pull/30))
- [x] Transactions: hide transfers, each account's bank mark and display name with numbers masked to the last four digits, plain text for rows with no payee (the bank line on hover), and "apply to similar rows" that saves a rule through the Rules editor ([#28](https://github.com/c-wenlong/kopifund-finanace/pull/28))
- [x] Account numbers masked to the last four digits in Past uploads (old entries included), the Upload page's check, load and mirror answers and their problem text, and the Transactions account filter ([#33](https://github.com/c-wenlong/kopifund-finanace/pull/33))
- [x] Singapore references kept as a short you-vs-typical list: one row per metric with the owner's figure against the typical one on its own scale, months of cash below the reference in red, a figure on too few rows greyed as "not billed here", the source on hover ([#31](https://github.com/c-wenlong/kopifund-finanace/pull/31))
- [x] Phone layout: under 768px a top bar and a drawer in place of the sidebar, safe-area insets for the installed app, every page fitting at 375px ([#32](https://github.com/c-wenlong/kopifund-finanace/pull/32))
- [x] One date format: a day "3 Oct 2026", a month "Oct 2026", a range "3 Oct to 9 Oct 2026", compact "Oct 26" only on chart axes; stat and rules prose the same ([#35](https://github.com/c-wenlong/kopifund-finanace/pull/35))
- [x] Notion mirror creates the Categories rows the record holds (a category added in the app, or the two Transport rows), so a sync no longer stops at missing-category for them; a dry run says what it would create ([#34](https://github.com/c-wenlong/kopifund-finanace/pull/34))
- [x] Categories: one row of tabs, one per bucket, and only the selected bucket's categories below, the tab kept in `?bucket=` and in the browser; a reusable `Tabs` component
- [x] Lists among other cards collapse to 3, 4 or 5 rows by row height, with a "Show all N" row and a down arrow that opens them (Spending's biggest payments and top payees, Trips, Recurring, Home's coming up, Profile, Plan's pay and investing, the Singapore references)
- [x] Chart views: a Stacked, Lines, Total switch (remembered per chart) on Net worth, the projection, Home and the explorer's spending charts and on stacked stat cards; a legend that hides a series; compact money ticks; one hover card colour that reads in dark mode
- [x] Less table, more picture: one `Tile` (tone colours the figure, a slot for a picture) on Home, Spending, Recurring and Net worth, with sparklines for net worth and cash and a runway track on Home, one progress bar for Recurring's paid of committed, red negative projections; one `Segmented` control, one `Card` heading size; Spending's "Where it went" with each bucket's share of total spend and no empty box; signed changes with a true minus
- [x] Pagination: one pager (page numbers with ellipses, previous and next, rows per page 10, 25, 50 or 100 remembered per list) under Transactions and Past uploads (server-side), Rules (in place of Show more), the payees awaiting a rule, the Upload check's rows to add and the stat tables
- [x] Pages open at once: the app keeps every page's last answer (in memory and localStorage) and checks it with an ETag, the server answers a current tag with a 304 and works each answer out once per data version, every write empties the cache, and a sidebar link under the pointer fetches its page ahead (SPEC.md, "Caching")
- [x] App shell and shared states: one "Can't reach Kopifund" banner with Retry (and "Showing figures from your last visit" over cached pages) in place of a raw error on every card, "run the analysis" only for a real 404, fixed-height skeleton cards, a shared toast with Undo for Categories, Rules, Plan and Trips (removing a trip can be undone), a title per page, the page name once on a phone, a plain Not found page, and the sidebar holdings in S$ linking to Net worth
- [x] Date and month fields in place of the browser's pickers: a `DateField` that shows "3 Oct 2026", takes typing and opens a calendar (react-day-picker, app tokens, light and dark, keyboard and Escape), a `MonthField` with a year header and month grid, one calendar for a trip's first and last day, a year dropdown for the date of birth, previous and next arrows on the Budgets month, and new income rows and planned trips filling their own dates
- [x] App-wide foundations: quiet text (`--ink-3`) at 4.5:1 or better on every surface in both themes, ink-coloured radios and checkboxes, a focus ring on buttons and links, reduced motion, 40px touch targets on touch screens, light splash and theme-color tags, a true minus sign and grouped foreign amounts in money
- [x] Plan, Net worth and Profile on a phone and in the forms: one `MoneyInput` for every amount (decimal keypad, commas stripped, thousands grouped on blur, Enter to the next, no spinners), a shared "Saved / Unsaved changes, Discard, Save" bar pinned on a phone, Plan's budgets with three tiles and a "Show only over plan" chip, a per-row "Saved" tick and inline errors, rows that stack as small cards on a phone (budgets, trips, one-offs, income, investing steps) so nothing scrolls sideways, inline validation under the box, no hint lines, the broker named only from the loaded statement, the forward view's duplicate label dropped, and Your profile's accounts with a freshness chip, an Add statement link (`/upload?account=`) and the statement folders merged in
- [x] Transactions, friendlier: two-line rows under 768px (payee and amount, then category chip, bank mark and date), money in, money out and net as tiles with a Clear filters button, a "Nothing matches" state with no pager, the category as a quiet chip with its pencil always shown on touch, the category filter a combobox and the month and account filters the app's Select in one family of boxes, every filter and the page kept in the address, foreign amounts grouped with a true minus, and the one-row form opening on the row's own category and a fitting kind
- [x] Trips and Recurring, less table: summary tiles on Trips (count, spent, average a day, this year against last), "Long stay" and "Planned" chips, a Payments column, an average line and warn-coloured bars over it on cost per day, a country picker with flags in place of typed ISO codes (`GET /api/countries`) with inline validation; stacked phone rows for the Trips, Active and Stopped tables, shared column layout for Active and Stopped, a dot strip per payee for the irregular list, "11 months ago" under a stopped payment, payee links that show a chevron and underline, and a compact Coming up on phones
- [x] Upload and Past uploads: a stepper (Add, Check, Load, Mirror) and a status chip on each card, a revisit showing the last attempt's real state; each problem a card with a plain headline, the account and file, one next step and the code on a details line (every server code worded), blockers in the danger colour; a success state after Load (count tiles, links to the transactions and Home) with Load off until the next check; Load saying "Fix N problems above first" or "Nothing new to load"; a busy strip saying what is running; a row per uploaded file (Added, Already there, Refused with its reason); the check as three tiles, phone rows for the needs-rule list and the rows to add, a header row and one money format; a drop zone, the reason "Choose PDFs" is off, and `?account=` to preselect; a plain subtitle and a Synthetic tooltip on the data quality chart; the history's empty chips for steps not run, "Checked, not loaded" and "Failed at load", counts in the closed row, "Technical details" and an empty state

- [x] Rules, Categories, Settings and Setup in plain words and small shapes: a status line with "N to apply" and "N problems" chips, one count with its unit, phone rows, single rows and "matches first" for pins and wins, one preview chip in the rule editor, the wildcard behind "Edit pattern"; Need or Want as a segmented control, bucket totals with a share bar, a neutral empty state; Settings with a server chip, no sidebar setting on a phone and a file list for exports; Setup with bank tiles and currency chips; a category box that takes the highlighted option on Tab
- [x] Final verifier fixes: a reader that stops names the file and account on the Upload problem card, three leftover hint lines gone, every field box 40px on touch with the focus colour token, no raw colour in the tabs mask, `num` on chip counts, "Saved" on a phone, the Actual so far note saying what its percentage covers, the flipped calendar 4px clear of its field, no leading zero in a money box
- [x] Details toggles gone from the stat cards: Spending, Net worth (cash and runway), Upload (data quality), Trips (cost per day) and Plan (forward view) show no findings, caveats or reference numbers; the figures a Details table held are in the chart's hover card (a row's other figures and a missing-statements note), and Data quality, Cash and runway and Needs, wants and savings get tiles; Home's Coming up lists late, then different amount, then due, then paid, so the four rows it opens on are the pressing ones
- [x] Last polish: the "Not a date" line makes room under its field instead of covering the line below; hint lines gone from the rule editor, Settings export and the Categories intro; the rule editor's preview box only shows when it has something to say
- [x] Upload finds the account itself: drop PDFs from any banks at once, each filed where its own text says (layout, wallet currency, the account number it prints against each folder's), one it cannot place listed with a small account picker and saved nowhere until the owner chooses, a statement for another kind of account refused even when chosen by hand, the check running by itself after a save, and one "Load N new rows" button under the check
- [x] Saves apply at once: saving a rule, a single row's category or a trip relinks the rows it decides differently straight away, exactly as a full load would (`server/finance/relink.py`, checked against a full load in `server/tests/test_relink.py`), toasts "Rule saved, 14 rows changed" with an Undo that takes the rows back too, and reruns the stats in the background; Apply rules shows only while rows wait, and every "next load" message is gone
- [x] Exact links: a link into Transactions lists exactly the rows its figure counts, where it used to search the rows' text (`q=`) and list extras (a payee "13 payments" opened 57 rows, a top payee of "1 payment" opened 28). Transactions takes `payee=` (the exact name), `key=` (rows by key) and `out=1` (payments only), each a chip with Clear; Recurring's payee links, Spending's top payees and biggest payments, Home's Coming up and biggest one-off, and the stat charts' payee bars use them, with a test per link family that the figure equals the rows listed
- [x] Stat links list exactly the rows their figure counts: a bar used to open every row of its month or category, which differs from the stat's own figure (transfers, made-up rows, refunds, overseas rows, rows with no SGD value, a bucket). Each stat now writes `chart.links` per series (`monthly-cashflow` income, spend and net; `needs-wants-savings` need, want and savings; `income-stability` salary and other; `merchant-concentration`; `travel` spend; `data-quality`), the Spending explorer and Home send a `link` per series, and Transactions takes `income=1`, `transfer=1`, `need=`, `bucket=`, `categories=`, `not_categories=`, `not_payees=`, `external=1` and `synthetic=1` on the same removable chip; an average (spend by category, a recurring payment's monthly cost, a trip's cost per day) no longer links and no longer says "Click", with a test per stat (`server/tests/test_stat_links.py`) that each bar's figure equals the rows its link lists
- [x] Last links list exactly the rows their figure counts: a Coming up row (Home and Recurring) shows this month's one payment, so a paid row opens that payment and a different-amount row the charges it adds up, by key, and a due or late row (no payment, only the usual amount) is plain text instead of a link to the payee's whole history; the eating-out weekly chart now links, each point to its Monday to Sunday of eating-out spending (Transactions takes `from=` and `to=` as days as well as months, and a stat's link may fill `{name}` from the point's own row); Undo toasts stack, newest on top, at most three, so "Apply to N similar rows" no longer takes away the pick's own Undo
- [x] One-tap category on Transactions: the category chip opens the category picker on the row's category, a pick saves the row's own rule at once (picking again replaces it) with an Undo, one chip offers "Apply to N similar rows" from the rule preview, and payee, kind and note sit behind "More", on the phone's two-line rows too
- [x] A statement saved twice or moved to another account's folder is a plain problem (`same-file-two-places`, `statement-moved`, both paths named) that doctor, load and the Upload check report before anything is written, in place of a raw `UNIQUE constraint failed: statement_file.sha256` half way through a load; a rename inside one account folder just updates the record
- [x] Polish 2: an Undo toast stays up until it is dismissed, pressed or replaced by a newer Undo (trip, rule and category Undo alike); the pin summary prints its amount the app's way (S$, commas, a true minus, a foreign amount with its code); a Transactions day that carries on says "continues on the next page"; refusals and upload replies start with a capital and say it plainly; the hint sentences under Upload's Check, Load and Mirror are gone; the split-transport command says what it leaves for a load
- [x] Recurring rows: the payee opens the series, the Paid or Different amount chip opens this month's payment, so each link lists exactly what it stands for
- [x] A byte copy of a statement is one problem (same file in two places), not that plus a duplicate month; the real-data duplicate test now covers a re-download and an exact copy apart
- [x] `make doctor` with Notion off runs every check but the mirror's, so a user without Notion, or an agent on a copy of the data folder, can run it

Open:

- Nothing open in this list.

## G14: net worth projection inputs

The Net worth page projected Tiger Brokers and CPF from query parameters that
were never saved, and drew neither cash nor a total. G14 makes every input
something the owner types, saves and can change later.

- [x] One income list shared with Plan and the forward view: name, employment or side income, from and to (empty runs to the horizon), gross a month, CPF yes or no, a yearly bonus for employment rows; old pay segments still load ([#37](https://github.com/c-wenlong/kopifund-finanace/pull/37))
- [x] Investing in steps, the old single transfer read as the first step until the owner saves ([#37](https://github.com/c-wenlong/kopifund-finanace/pull/37))
- [x] Spending a month with the record's 12-month average as its hint, and its own growth rate ([#37](https://github.com/c-wenlong/kopifund-finanace/pull/37))
- [x] The assumptions (returns, inflation, salary growth, FRS and BHS growth, horizon, dollars, milestone) saved in the plan ([#37](https://github.com/c-wenlong/kopifund-finanace/pull/37))
- [x] Take-home after age-banded employee CPF (with the Additional Wage ceiling), estimated income tax (`server/analytics/tax_rules.py`) and self-employed MediSave on side income ([#37](https://github.com/c-wenlong/kopifund-finanace/pull/37))
- [x] Net worth drawn forward as cash, Tiger Brokers and CPF stacked, with the total, the Tiger band, tiles for today, 5 and 10 years and the milestone, and the months cash is under zero marked ([#37](https://github.com/c-wenlong/kopifund-finanace/pull/37))
- [x] Inputs always shown at the top of the Growth card, with a live preview, Save, Discard and "Unsaved changes"; working at 375px ([#37](https://github.com/c-wenlong/kopifund-finanace/pull/37))
- [x] Inputs moved to one compact form at the top of the page, the projection right under it, notes and toggles removed
- [x] The forward view reads the same income rows and investing steps ([#37](https://github.com/c-wenlong/kopifund-finanace/pull/37))
- [x] Verifier fixes in the shared pieces: a "Show all" row no longer centres on a touch screen, the pager on a phone has page numbers and a Go to page box and can keep its page in the address, tabs that overflow show fades and arrows, a page open in a tab revalidates when the window comes back, a date popover flips or scrolls on a short screen, a typed date that is not one is flagged instead of dropped, chart switches name their chart, hover cards skip a series at zero
- [x] Transactions by day: rows grouped under a day header ("Tue 13 Oct 2026", with that day's spending and money in, counted by the server over the whole day so a day split across pages is right on both and says "continued"), no date column, transfers hidden by default with a "Transfers hidden" chip that toggles
- [x] Suggestion chips beside the Net worth and Plan inputs, computed from the record and profile (`GET /api/suggestions`): pay still arriving as rows to add, the brokerage deposits as the investing amount, the employer CPF rate for the age, insurance and repayments from the recurring detector, and the 12-month spending average; nothing is filled until a chip is tapped
- [x] Recurring is one list of every live payee, soonest next payment first, each with its status chip and a bar of its yearly cost on one shared scale; Trips are cards grouped by year with only the newest year showing; the cost per day chart marks only trips a quarter or more above the average

## G15: scam checks

Kopifund sees a transaction only once its statement is loaded, so it cannot stop
a payment. It can point at one weeks sooner than a person reading the ledger
would. Most scams in Singapore are the victim sending money by PayNow or FAST,
so the first flags look at outgoing transfers.

The checks are plain rules over the record (`server/finance/scam_checks.py`), read on each request so a row marked fine drops out at once.
There are no labelled scam rows to learn from and each flag must say why it
fired, so there is no model. Payees in `people.csv` count as known.

First:

- [x] New payee, big transfer: an outgoing transfer of at least S$500 to a payee never paid before
- [x] Repeated transfers: 3 or more transfers to the same new payee within 14 days that add up to the big-transfer amount; the reason says when each was larger than the last
- [x] Flagged rows on Home's "Needs your eye" card and in a Transactions filter, each with its reason
- [x] "This is fine" on a flagged row, saved in the record, so that payee is not flagged again; Settings lists them with Flag again
- [x] The amount and day thresholds saved in the record from the Settings page, never hardcoded

Then:

- [x] Abroad, no trip: at least S$20 at a new merchant overseas on a row no trip covers
- [x] Test charge: a first charge of at most S$2 at a new merchant, then a larger one within 7 days; both flagged
- [x] Charged twice: same payee, same amount, same day, from S$20
- [x] New subscription: a shop the rules mark recurring, or charged about the same a month apart, first paid in the last 60 days

## G16 to G19: Kopifund v3

A review on 18 Oct 2026 ([docs/v3-audit-2026-10.md](docs/v3-audit-2026-10.md))
found recurring payments wrong in common cases, no shared types between the
server and the app, and a server file doing seven jobs. Each line below is one
PR, listed in build order within its goal; the audit rows it closes are in
brackets.

### G16: recurring you can trust

- [x] Schedule: lateness from the last real payment, an early payment kept in its own month, an extra charge never booked as next month's, each series judged against its own account's statements, Home's Needs your eye in step with Recurring (P2, P3, P4, P5, P9) ([#89](https://github.com/c-wenlong/kopifund-finanace/pull/89))
- [x] Detection: several series per payee, fortnightly, two-monthly and half-yearly schedules, a yearly bill from two payments when the payee is marked recurring, a price rise read as a new price, a paused state apart from stopped (P1, P6, P7, P8, P10, P11, P12) ([#99](https://github.com/c-wenlong/kopifund-finanace/pull/99))
- [x] A series opens its own view on Recurring (`?series=`, no new tab) with its schedule, payments and status, and is edited there: recurring or not, usual amount, how often, day, ended on, saved in the record; "Not recurring" replaces "Edit rule" (P13, P14, P15, P16) ([#107](https://github.com/c-wenlong/kopifund-finanace/pull/107))

### G17: one set of types

- [x] Types generated from Python (`server/finance/types.py` to `apps/web/src/types.gen.ts`) with a test that fails on drift; existing type names kept (T1, T8, T11, RT4) ([#87](https://github.com/c-wenlong/kopifund-finanace/pull/87))
- [x] Plan money crosses the wire as cents, one rounding rule for cents everywhere (T2, T3) ([#93](https://github.com/c-wenlong/kopifund-finanace/pull/93))
- [x] Trips, categories, buckets and budgets written by id, names still accepted (T4, T5) ([#94](https://github.com/c-wenlong/kopifund-finanace/pull/94))
- [x] Schema v5: deletes cascade or clear, enums checked, a trip's countries and pinned rows in their own tables, a period linked to its statement file, an icon and a colour stored on each bucket (T6, T7, T9, T10, T12, T13, U1) ([#100](https://github.com/c-wenlong/kopifund-finanace/pull/100))

### G18: v3 interface

- [x] Every chart from one kit (axes, hover card, swatch, colours), and no two buckets share a colour (U1, U6, U7, U12) ([#86](https://github.com/c-wenlong/kopifund-finanace/pull/86))
- [x] A dialog and bottom sheet, and a guard on leaving with unsaved changes (U2, U10) ([#88](https://github.com/c-wenlong/kopifund-finanace/pull/88))
- [x] An icon for every kind: the bank mark wherever an account is named, and an icon for each transaction kind, need, want and savings, how often a payment recurs, each status chip, each upload step and each scam reason ([#92](https://github.com/c-wenlong/kopifund-finanace/pull/92))
- [x] Net worth's inputs open in a dialog from a one-line summary at the top of the page ([#98](https://github.com/c-wenlong/kopifund-finanace/pull/98))
- [x] Trips as bubbles sized by spend, beside the cards, with the trip form in the dialog (U14) ([#95](https://github.com/c-wenlong/kopifund-finanace/pull/95))
- [x] Needs your eye in the sidebar with a count, every alert in one list, scam lines in red (U3, U11, U13) ([#96](https://github.com/c-wenlong/kopifund-finanace/pull/96))
- [x] Bucket icons and colours picked on Categories and shown on its tabs, in Where it went and in chart legends ([#103](https://github.com/c-wenlong/kopifund-finanace/pull/103))
- [x] One table, button and select style, and one empty state (U4, U5, U8, U9) ([#106](https://github.com/c-wenlong/kopifund-finanace/pull/106))

### G19: server and repo structure

- [x] Ignore Playwright output, drop `concat_csv.py`, test fixtures under `server/tests/`, the runbook check in `make check` (R8, R9, R10) ([#85](https://github.com/c-wenlong/kopifund-finanace/pull/85))
- [x] One frontend route table feeds the sidebar, page titles and prefetch (RT1, RT2, RT5) ([#97](https://github.com/c-wenlong/kopifund-finanace/pull/97))
- [x] One server route table, one JSON body parser, one database connection per request (R3, R6, R7, RT3) ([#90](https://github.com/c-wenlong/kopifund-finanace/pull/90))
- [x] serve.py only routes: endpoint answers in `server/finance/`, the cache, upload steps and dev reload in their own modules (R1, R2, R9) ([#104](https://github.com/c-wenlong/kopifund-finanace/pull/104))
- [x] The parsers are a package imported normally, and analytics depends on finance only (R4, R5) ([#105](https://github.com/c-wenlong/kopifund-finanace/pull/105), [#108](https://github.com/c-wenlong/kopifund-finanace/pull/108))

## G20: the stats in cents

The record holds money as integer cents, but the snapshot hands the stats
float dollars (`server/finance/snapshot.py`, `money`), the 15 stats add and average
in floats and write dollars into their results, and Home, Net worth, the API,
the export and the hub page each turn them back into cents with `to_cents`
(SPEC.md, Open questions, "Floats in the stats"). This goal runs cents from
the record to the screen. Each line below is one PR, in build order; lines 2
to 4 touch different stats and can run side by side once line 1 is in.

Anything that reads a stat's result, another stat or code in `server/finance/`, reads it through `stat_cents` (`server/finance/money.py`), and the line that converts a stat also moves every reader of it that touches the fields directly. Every stat line proves itself the same way: `analyse --no-publish` on a copy
of the data folder before and after, every money field within one cent of
`to_cents` of the old dollars, and every field that moved listed in the PR.
Findings print their amounts with `server/finance/money.py`'s `label`.

- [x] The contract: a money field in a result is integer cents named `<name>_minor`, like the record's columns, so an old dollar result can never be read as cents; the chart hint's unit for it is `sgd_minor`; snapshot rows carry `amount_sgd_minor` from the record; `server/analytics/stats/common.py` gets a cents sum, mean and median with one rounding rule (half up, as `to_cents`); the app's stat cards, Home, Net worth, the export and the hub page read either shape until the last line ([#113](https://github.com/c-wenlong/kopifund-finanace/pull/113))
- [x] Cash stats in cents: monthly-cashflow, savings-runway, one-offs, data-quality, income-stability ([#115](https://github.com/c-wenlong/kopifund-finanace/pull/115))
- [x] Spending stats in cents: category-breakdown, needs-wants-savings, merchant-concentration, eating-out, travel and the recurring detector's stat, with `server/finance/recurring.py` (Home's Coming up and the series rows it writes) and `server/finance/suggestions.py`'s recurring reads taking its cents ([#117](https://github.com/c-wenlong/kopifund-finanace/pull/117))
- [x] Holdings and references in cents: portfolio, cpf (its caps and ceilings as cents in `server/finance/cpf_rules.py`) and benchmarks-sg (reference figures turned to cents once, where `benchmarks.json` is read), with `server/finance/suggestions.py`'s portfolio reads taking its cents ([#116](https://github.com/c-wenlong/kopifund-finanace/pull/116))
- [x] forward-view and the Net worth projection (`server/analytics/growth.py`) in cents only: the dollar twins beside their `_minor` fields go, and growth and interest round to the cent once a month by the same rule ([#119](https://github.com/c-wenlong/kopifund-finanace/pull/119))
- [x] No dollars left: the snapshot's `money` and every `to_cents` on a stat result are gone from Home, Net worth, the API and the export; the hub page drops its dollar reads and writes Notion's number fields from cents in one place; a test fails on any float money field in a result, every stat in `server/tests/test_cents_contract.py`'s `MOVED` set with benchmarks-sg's plain numbers named as exceptions; "Floats in the stats" leaves SPEC.md's open questions ([#121](https://github.com/c-wenlong/kopifund-finanace/pull/121))

## G21: more banks

Kopifund reads the owner's providers plus OCBC 360, which was built from a
public sample and has never met a real statement (docs/parsing.md, "The seven
layouts"). A new layout today touches six places: its parser, the three
tables at the top of `server/finance/sniff.py`, the parser calls in `server/finance/sync.py`
and `server/finance/monthly.py`, Setup's `PROVIDERS` and the layouts table in
docs/parsing.md. This goal makes a layout one module, adds the deposit
accounts most people in Singapore hold, and gives a statement no layout knows
a way in. Credit cards stay out: SPEC.md lists liabilities as a non-goal.
Each line below is one PR, in build order; line 3 was dropped, see
below.

A new layout's fixture is invented rows set out the way a public sample of
that bank's statement prints them, never a real statement. A layout stays
"from a public sample" until a real statement has parsed and balanced, and
says so wherever it is named.

- [x] One layout registry: each layout in `server/finance/parsers` declares its name, its label, its layout test, the account number it prints, its statement folder and its parse function, and the Upload page's sniffing, the sync, the monthly run, Setup's provider list and docs/parsing.md's layouts table all read that registry; all eight layouts move into it with byte-identical CSVs and doctor output, and the parsing doc's table, today headed "The seven layouts", is written from the registry ([#129](https://github.com/c-wenlong/kopifund-finanace/pull/129))
- [x] UOB accounts: the UOB One account and a UOB savings account parse and balance (opening plus deposits less withdrawals is the printed closing, every running balance replayed), each from a public sample; the bank names still kept outside the registry (`server/finance/account_names.py`'s names and marks, `snapshot.wallet_folders` and Setup's `bankOf` in `apps/web/src/pages/Setup.tsx`) move onto it here, so line 3 only adds its own layouts ([#135](https://github.com/c-wenlong/kopifund-finanace/pull/135))
- Dropped: Trust, GXS and MariBank. None of them publishes a sample statement or its layout, so there is nothing to build a layout from without guessing (the sources checked are in docs/parsing.md, "Trust, GXS and MariBank: no layout yet"). Each waits for a statement's text from line 4's `finance.py sample` command
- [x] A statement no layout knows: the Upload page names it as not supported, lists the banks that are, and keeps nothing; `finance.py sample <pdf>` writes its text with names, addresses and account numbers replaced, for whoever builds its layout; each layout's status (checked on a real statement, or from a public sample) shows on Setup; "Banks for other users" leaves SPEC.md's open questions ([#137](https://github.com/c-wenlong/kopifund-finanace/pull/137))

## G22: copilot

Today the owner asks questions about their money by talking to Claude Code
in a terminal. This goal puts that in the app: a panel beside every page
that answers from the record, in words and charts, and shows which figures
it looked at. It grows out of G12's `server/finance/ai.py`, keeps its providers and
its standard-library rule, and adds a tool loop on the server (SPEC.md,
"Copilot"). The model reads; it never writes. Each line below is one PR, in
build order; the owner asked for all of it in one PR, so the lines
land together.

- [x] The loop: `server/finance/copilot.py` sends the question, the page's context and the tool list through `server/finance/ai.py`, runs each tool the model asks for, returns every result of a round in one message, and stops when the model answers in plain text or after 8 rounds; both wire formats (Anthropic's `tool_use`, the OpenAI-style `tool_calls`); a fake provider in the tests replays scripted replies, so no test calls the network
- [x] The tools: read-only functions over what the API already answers (overview, spending, transactions, recurring, trips, net worth, one stat's result) plus `query`, one SELECT on an in-memory copy of the record that holds no counterparty, memo, reference or account number, capped at 200 rows, 5 seconds and 1 MB a value; money as cents with its currency; a result that counts rows carries the Transactions link for exactly those rows, and balances, averages and estimates carry none; a test runs every tool on a fixture record and checks the connection refuses a write
- [x] The panel: an "Ask Kopifund" button at the top right of every page opens it beside the page on desktop, pushing the page narrower so its charts stay in view, and as a bottom sheet under 768px; the answer streams in; each tool call shows as one quiet line; `show_chart` draws with the chart kit; follow-up chips; the page and its filters go with each question; threads saved in the record and listed in the panel
- [x] Keys in Settings: an AI card on the Settings page picks the provider and the two models (saved in the record's `setting` table) and takes the API key, kept in the macOS Keychain or, with no keychain, in the data folder's `.env` (mode 600); the page only ever sees where the key is and its last four characters; Replace, Remove and Test; the model fields autocomplete from the provider's models endpoint through the server, cached a day on the server, asked after a 250ms pause in typing and remembered on the page; tests never touch the Keychain
- [x] Consent: off by default; Settings turns on "Send matching rows to <provider>", saved in the record's `setting` table; while it is off the panel says so and links to Settings; a provider whose URL is on this machine (a local Ollama) needs no switch; the panel names the provider under the box
- [x] Confirm cards: the model can suggest a rule, a row's category, a budget or a trip as a card with the change and its preview (a rule shows the rows it changes); the existing write endpoint saves it only when the owner presses Confirm, with an Undo toast; a budget save gains the Undo that rules, categories and trips already have

## G23: desktop app

Today opening Kopifund means a terminal, `make serve` and a browser tab. This
goal wraps the same app in a Mac window: a Tauri shell in `apps/desktop/` starts
`serve.py` as a sidecar and shows the page it serves, so the React app and the
Python server stay as they are (SPEC.md, "Desktop app"). Tauri over Electron
because the backend is Python, so Electron's bundled Node and Chromium would
add about 150 MB and do nothing; over a Swift app because a full rewrite of
`apps/web/` buys the same screens, and a thin Swift wrapper would hand-build the
sidecar, updater and single-instance handling Tauri ships as plugins.

Lines 1 to 4 are for the owner and run from the checkout with Homebrew's
`python3` and `pdftotext`. Lines 5 to 7 are later milestones, for sharing the
app with friends, and start only when the owner asks. Each line is one PR, in
build order.

For the owner:

- [x] The server as a sidecar: `serve.py --app` serves on 127.0.0.1:8771 (fixed, so the page's saved state survives a restart, and clear of `make serve`'s 8770), prints one ready line with its address once it is listening, and exits when its standard input closes, so a crashed shell never leaves a server behind; the stats rebuild takes a file lock in the cache folder, so the app and `make serve` on one data folder rebuild one at a time and an older rebuild never lands over a newer one; a test starts it, waits for the line and closes its input, and another holds the lock from a second process ([#130](https://github.com/c-wenlong/kopifund-finanace/pull/130))
- [x] The shell: `apps/desktop/` holds a Tauri v2 project and `make desktop` builds `Kopifund.app`; launching it starts `serve.py --app` from the checkout it was built from and shows the window once the ready line arrives; quitting stops the server; a second launch brings the open window forward; the window keeps its size and place; Kopifund's icon in the Dock; a server that fails to start shows its last lines and a Retry instead of a blank window; `make check` runs `cargo check` on it ([#131](https://github.com/c-wenlong/kopifund-finanace/pull/131))
- [x] Settings' Server card: `GET /api/server` answers how the server was started (`make serve`, `make dev` or the app), its port, pid and start time, so the card can say how long it has run; `POST /api/server/stop` and `/api/server/restart` answer first, then the process ends or runs its command line again in its place, and under `--app` the new ready line reloads the window; a stopped server answers nothing its own page could use to start it, so the card's stopped state names where the start lives instead of offering a button
- [ ] Mac behaviour: the standard app, Edit, View and Window menus (copy and paste work, Cmd+R reloads, Cmd+[ and Cmd+] go back and forward, Cmd+, opens Settings); PDFs dropped on the window or the Dock icon open Upload with them queued; a link to another site opens in the default browser; the service worker is not registered inside the app
- [ ] The WebKit pass: every page in the app's window at desktop width, light and dark, checked against Chrome; whatever renders or behaves differently in WebKit is fixed in `apps/web/`; the PR carries a screenshot of each page; README says how to build and open the app

Later, for sharing:

- [ ] Self-contained: the app bundles a standalone CPython 3.14 build, a `pdftotext` binary and a copy of the Python code, and runs on a Mac with no Homebrew and no checkout; the data folder is still `~/.kopifund`, and a first launch with none opens Setup
- [ ] Signed and updated: a GitHub Actions job on a version tag signs the app with a Developer ID, notarises it and attaches a DMG to a GitHub Release; the app checks that release on launch and offers the update, verified by the updater's signature; About lists poppler's GPL licence and a link to its source; needs an Apple Developer account, which the owner decides on before this line starts
- [ ] Windows: an installer built in CI with a Windows Python and `pdftotext`, the window in WebView2, and every page checked there

## G24: bank alerts into YNAB from the phone

Kopifund reads the month's statements in one run, but the owner's YNAB plan
wants each transaction as it happens, and YNAB cannot import the unlinked
Singapore accounts (DBS, OCBC, UOB) directly. Every bank already tells the
phone about each spend: an SMS, an email or an Apple Pay alert. This goal is an
iOS app (`apps/ios/`, SwiftUI, iOS 17+) that turns that alert text into a
reviewed YNAB transaction.

iOS lets no third-party app read another app's notifications or the SMS inbox,
so the app never listens: alert text is handed to it through App Intents the
Shortcuts app calls (a Message trigger for bank SMS, an Email trigger for
Mail, the Wallet transaction trigger for Apple Pay), a share extension and a
paste box, and the intents run without opening the app.

No bank's wording is hardcoded. The owner pastes a real alert, taps the spans
that are the amount, the payee and the date, and the app builds a reusable
pattern: the fixed text becomes literal anchors and the tapped spans become
typed captures. Every alert runs through the owner's patterns, matches queue
for review, and approved rows go to api.ynab.com (the only network host) as
unapproved transactions with a deterministic `import_id`, so the same alert
never double-posts. Shipped in [#271](https://github.com/c-wenlong/kopifund-finanace/pull/271).

Each line is one PR, in build order:

- [x] The monorepo move: `apps/` for the apps a user opens (`apps/web/`, `apps/desktop/`, `apps/ios/` next), `server/` for the Python pipeline and `data/` for the shipped data files, because the iOS app lands beside them rather than in another repo ([#141](https://github.com/c-wenlong/kopifund-finanace/pull/141))
- [x] The `Matching` package and the project shell: an xcodegen project with four Swift packages (Matching, YNABClient, Storage, Intents) and the app and share-extension targets building for the simulator; the pattern model built from tapped spans, the priority-ordered matcher and its unit tests on fixture alerts
- [x] `YNABClient`: personal access token in the Keychain, plans, accounts, categories and payees fetched and cached for the pickers, transactions created uncleared and unapproved with a deterministic `import_id`, and HTTP 429 retried; tests over mocked URLSession responses
- [x] Intake: the Ingest Alert and Ingest Wallet Transaction App Intents running in the background, the share extension and the in-app paste box, all writing the same App Group store
- [x] Screens and sync state: the review queue with edit and approve, the tap-to-tag pattern builder with live testing, the unmatched inbox with one tap into a new pattern, Settings (token, default plan, per-pattern auto-approve, delete all data, revoke token) and each transaction's sync state; the README walks through the three Shortcuts automations
- [x] The unmatched flow rework: rows lead with the alert's own line and a detected-amount chip, a row opens a readable alert before any teaching, the builder pre-tags its confident guesses with weaker ones as dashed suggestions, and "always ignore" drops a remembered alert shape at intake whatever channel carried it ([#280](https://github.com/c-wenlong/kopifund-finanace/pull/280))
- [x] Anchors read as regex: digits and date-shaped runs inside a literal fold to wildcards so a changing reference or date cannot break a pattern, the payee capture reads to its line's end only, and a wildcard touching the payee is refused at build time instead of silently capturing one letter ([#283](https://github.com/c-wenlong/kopifund-finanace/pull/283))
## G25: the phone link

The iOS app (G24) talks only to api.ynab.com, and serve.py answers only
127.0.0.1. This goal links them. The phone reaches the record through
`tailscale serve`, which proxies 443 on the machine's tailnet name to
loopback 8770 and terminates TLS at the tailscale daemon, so serve.py
never binds a non-local address and needs no TLS code. A request carrying
`X-Forwarded-For` is one the proxy forwarded: it needs a paired device's
bearer token and is answered only GETs (and the two link writes below),
while the loopback path and its `X-Kopifund` plus Origin checks are
unchanged. Pairing mirrors a local hub: Settings' "Link a phone" card
mints a one-time token behind a QR, the phone redeems it exactly once
under a `flock` on `~/.kopifund/devices.json` and keeps the returned
bearer in the iOS Keychain; revoking a lost phone is flagging one row.
`python3 server/finance.py link` is the setup checklist (tailscale
installed and signed in, MagicDNS, HTTPS certs, `tailscale serve`
proxying, the hub answering).

Each line is one PR, in build order:

- [x] The link gate and the device registry (`server/finance/link.py`): a request with `X-Forwarded-For` needs a paired device's bearer for every `/api` GET and is refused everything else but `POST /api/pair/redeem` (unauthenticated: the one-time token is the credential) and, later, `POST /api/link/transactions`; pair tokens are single-use and spent under `flock`; `GET /api/link/devices` lists the phones and `POST /api/link/devices/revoke` flags one
- [x] The setup checklist: `python3 server/finance.py link` verifies the tailscale binary, the signed-in tailnet, MagicDNS, HTTPS certs, a fetched cert, `tailscale serve` proxying 443 to 8770 and the hub's `/api/health` answering, each miss printing the fix
- [x] Settings' "Link a phone" card: the pair token in a QR (`kopifund://pair?url=…&token=…`) plus a copyable payload, the tailnet name `tailscale status` reports as the payload's default URL, the paired devices listed with their paired date and a revoke
- [x] `KopifundClient` (`apps/ios/Packages/`): a Swift package beside YNABClient with the same URLSession + Codable shape: pair redeem, the bearer in the Keychain, typed reads of health, profile, accounts and transactions, tests over mocked URLSession responses
- [x] The app's Kopifund screens: a Settings section that scans the QR or takes a paste, tests the connection and unlinks; an Accounts list (`/api/profile`) and a Transactions browser (`/api/transactions`)
- [x] Alerts reads the record: an alert row whose amount, currency, date and payee a loaded statement row already covers is marked "already in kopifund" and never offered for posting; rows that share all four with more than one loaded row are left for the owner
- [x] Phone rows into the record: `POST /api/link/transactions` lands an approved alert row as a `pending_txn` (`import_id` unique, so a retry never doubles), each load matches a pending row to one real row on amount, currency, date and payee (a statement row takes at most one pending row, and a pair the owner refuses is kept on the row's rejected list and never offered again), and Home's "Needs your eye" lists what is still waiting until it is matched, dismissed or restored

## G26: interactive visual dashboard

Every page today is fixed: Home, Spending, Net worth and the rest show the
charts the app ships, in the order it ships them. This goal turns the app
into a dashboard builder: the owner drags widgets onto a grid, each bound
to a typed query over the record, and the layout is saved locally in
`~/.kopifund`. State widgets (pie, treemap, KPI tiles) show where the
money stands, change widgets (time series, stacked area, waterfall) show
it over time, and a Sankey shows income flowing to buckets to categories.
Goals with trajectories, a describe-only what-if editor, monthly
allocation buckets and a game-pot tracker ride on the same widgets.
Tracked in epic [#4](https://github.com/c-wenlong/kopifund/issues/4).
Each line is one child issue, in build order:

- [ ] Backend endpoints and local persistence, with the typed widget data-source and query layer ([#15](https://github.com/c-wenlong/kopifund/issues/15), [#6](https://github.com/c-wenlong/kopifund/issues/6))
- [ ] Drag-and-drop layout engine with persisted layouts ([#5](https://github.com/c-wenlong/kopifund/issues/5))
- [ ] State widgets: pie and donut, treemap, KPI cards ([#7](https://github.com/c-wenlong/kopifund/issues/7))
- [ ] Change widgets: time series, stacked area, waterfall ([#8](https://github.com/c-wenlong/kopifund/issues/8))
- [ ] Flow widget: Sankey income to buckets to categories ([#9](https://github.com/c-wenlong/kopifund/issues/9))
- [ ] Visual editor UX: side panel, templates, presets ([#14](https://github.com/c-wenlong/kopifund/issues/14))
- [ ] Monthly allocation-plan buckets (actual vs plan) ([#12](https://github.com/c-wenlong/kopifund/issues/12))
- [ ] Goals and milestones model with trajectory visuals ([#10](https://github.com/c-wenlong/kopifund/issues/10))
- [ ] Scenario and what-if editor (describe-only projections) ([#11](https://github.com/c-wenlong/kopifund/issues/11))
- [ ] Game-pot experiment tracker vs benchmark ([#13](https://github.com/c-wenlong/kopifund/issues/13))

## G27: an SRS tracked account

The owner has no Supplementary Retirement Scheme account yet, but Kopifund
should be ready the day they open one: contributions against the yearly
cap, tax relief by year of assessment, holdings inside the account and SRS
counted in net worth, the way cash, Tiger Brokers and CPF already are.
The operators are the agent banks (DBS, POSB, OCBC, UOB); the
contribution cap lives in local config, never hardcoded, since it differs
for foreigners. Tracked in [#16](https://github.com/c-wenlong/kopifund/issues/16).

SRS becomes a new account kind beside bank account, wallet, card,
brokerage and CPF (`server/finance/parsers/layouts.py`), with its folder
added in Setup like any other account. The parser registers through the
G21 layout registry at SAMPLE status until a real or public-sample
statement balances, and unsupported PDFs go through `finance.py sample`.
Contributions and tax relief are tracked and shown in cents, describe
only, and SRS joins `GET /api/net-worth`, the Net worth page and the
projection where cash, Tiger Brokers and CPF already appear, so the G26
widgets can bind it without a second data path.
