# Alerts: bank alerts into Kopifund (G24)

A personal iOS app (SwiftUI, iOS 17+) that turns bank transaction alerts into
transactions for the Kopifund record on your Mac, and into YNAB too when a
token is set. It exists for unlinked Singapore bank accounts (DBS, OCBC,
UOB), which YNAB cannot import directly.

iOS does not let apps read notifications or the SMS inbox, so the app only
processes text handed to it: through a Shortcuts automation, the share sheet,
or a paste box inside the app.

## Layout

| Folder | What it is |
| --- | --- |
| `Packages/Matching` | Pure Swift, no UI, no dependencies: tokenizer, alert-reading helpers (boilerplate, field candidates, shape signatures), tap-to-pattern builder, regex matcher, unit tests |
| `Packages/YNABClient` | `api.ynab.com` over `URLSession` + `Codable`: plans, the plan's currency, accounts, categories, transaction creation, milliunits, `import_id`; a 429 waits as long as `Retry-After` asks, up to 30 s |
| `Packages/KopifundClient` | The phone link (G25): pairs with the Mac's Kopifund server over Tailscale with a device token, reads its profile, transactions, categories and overview, and posts approved alert rows to it |
| `Packages/Storage` | SwiftData models, the shared App Group store, and the suppressed alert-shape set |
| `Packages/Intents` | `Ingest Alert` and `Ingest Wallet Transaction` App Intents |
| `Packages/TestSupport` | Test-only: `MockProtocol`, the URLSession stand-in the YNABClient, KopifundClient and Intents tests share |
| `Alerts` | The SwiftUI app: Inbox, Unmatched, Alert reader, Pattern builder, Settings |
| `ShareExtension` | Share-sheet target that runs the same ingest path |
| `project.yml` | XcodeGen spec; `xcodegen` regenerates `Alerts.xcodeproj` |

The app calls two hosts: `api.ynab.com`, and your own Mac's Kopifund server over
your tailnet once you pair a phone (the phone link; unpaired, it never calls it).
There is no backend of ours, no analytics, and no third-party dependency.

## Build

```sh
brew install xcodegen   # once
cd apps/ios
echo 'DEVELOPMENT_TEAM = <your team id>' > Local.xcconfig   # once, for a device build
xcodegen
open Alerts.xcodeproj
```

Your signing team lives in `Local.xcconfig`, which git ignores, so it never
reaches the public repo. `Signing.xcconfig` (tracked) sets an empty team and
includes `Local.xcconfig` when it exists; every target reads it, so a device
build signs with your team without touching `project.yml` or the generated
project. The team id is the 10 characters shown under Membership at
developer.apple.com, or in Xcode's Signing & Capabilities team list. Without
the file a device build fails with "No Account for Team"; simulator builds
need no team.

Run package tests with `swift test` inside any of `Packages/Matching`,
`Packages/YNABClient`, `Packages/KopifundClient`, `Packages/Storage` or `Packages/Intents`, or build the app for a simulator with
`xcodebuild -scheme Alerts -destination 'platform=iOS Simulator,name=iPhone 17 Pro'`.

Signing: the bundle ids are `com.kopifund.alerts` and
`com.kopifund.alerts.ShareExtension`; both use the app group
`group.com.kopifund.alerts`. Set your team in `Local.xcconfig` (above) and
keep or change the ids, whichever your account allows.

## Setup

1. Open the app once. In **Settings > Kopifund**, link the phone to your
   Mac's Kopifund (the web app's Settings > Link a phone shows the code).
   Kopifund is where approved rows go; with no link they wait on the phone,
   saying "Waiting for Kopifund: link this phone in Settings".
2. Optional, YNAB too: in **Settings > YNAB**, paste your personal access
   token (YNAB > Account Settings > Developer Settings; it goes to the
   Keychain, never into a file), tap **Fetch plans & accounts**, pick your
   plan, then the default account Wallet transactions post to. With a token
   and a plan, approved rows go to YNAB as well as to Kopifund.
3. Build a pattern: **Patterns > +**, paste a real bank alert. The detector
   pre-tags its best guesses for the amount, the payee, the date and the card
   (the digits after "ending", as in "Card ending 1234"); dashed
   words are weaker guesses that accept with one tap. Pick a field chip and
   tap or drag across words to fix anything, watch "This pattern reads",
   then save. No YNAB plan or account is needed; with a YNAB token the
   builder also offers the YNAB account its rows post to (Settings' default
   when none is picked). Save and Approve say where approved rows go.
4. Everything else is intake: matches land in the Inbox for review, misses
   land in Unmatched where a row opens the alert itself before teaching
   anything.

## Unmatched and teaching

An alert no pattern takes waits in Unmatched. Rows lead with the merchant
the detector reads out of the alert (falling back to its first telling
line), and a neutral chip shows the detector's best money guess when one
exists (no colour: the direction is unknown until a pattern says it). The
chip prints the figure the way the Inbox does ("SGD 2.20", whatever spacing
the alert used), without a sign. Tapping a row opens
the alert itself first: mail chrome dimmed, the detector's summary up top
("amount, date found"), and the matcher rejections tucked behind "Why
nothing matched".

Two exits leave noise without teaching anything: swipe for **Not an alert**
to drop just that row, or **Always ignore** to remember the alert's shape
(its opening words with every number folded to `#`) and drop the next one at
intake, whichever channel carried it. Always ignore asks first, since it
silences every alert of that template, and **Patterns** lists each ignored
shape under "Ignored alert shapes"; swipe one there to stop ignoring it.

**Teach a pattern** opens the builder with the confident guesses already
tagged; the save button says what is still missing rather than sitting grey.
The same `Matcher.test` powers the live read, so what the preview shows is
what intake does.

A field is found by the stable words right around it, not by every word of
the alert: the literal before a field keeps its nearest words on the field's
line (at most two words that do not change, never past an earlier clause's
end), or the label line above when the field starts its line ("Amount:");
the literal after a payee or card keeps the words that end it, or just its
line break; an amount or a date, which read their own end, keep one word.
Everything else (greeting, header, reworded sentences, the footer, extra
lines between fields) becomes ignore, so the next alert of the kind reads
when those change. A payee the sample followed with a blank line may wrap
onto two more lines and reads joined with a space, and a name never holds
the words the template opens on, so a line that repeats the template cannot
leak into it. **More options > Found between** shows each field's before
and after text and lets the owner change it (⏎ is a line break); clearing
the text beside a payee or card is refused like a fresh build would be, and
re-tagging picks the texts again. `CorpusTests` holds invented alerts of four
kinds with their variants and look-alikes from other banks; every variant
must read and no look-alike may.

Anchors read as regex, not verbatim text: digits and date-shaped runs inside
the fixed words fold into wildcards on their own, so a reference number or a
dated line that changes every alert cannot break the match. A number that
must pin the pattern, like a card's last digits, is tagged Card instead, and
a later alert matches only when it prints the same number. The payee capture
reads only to the end of its line. A wildcard or a folding piece right
beside the payee is refused at build time: a fixed word or a printed line
break has to stand there, or the regex cannot tell where the name ends.

The regex is built so a failed match stays close to linear in the alert's
length: whitespace runs read possessively, a payee or card capture starts
and ends on a non-space (and reads at most 200 characters while it looks
for the anchor after it; one that ends the template reads to its line's
end), and each ignore span commits to the first place the text after it
reads. One pattern gets 250 ms on one alert; past that it gives up with
"text too long to read in time" rather than freezing the app or getting an
intent killed. Ingest runs the match on a detached task, so even a phone
with many patterns never holds the main thread while it reads an alert.

## Shortcuts automations

iOS lets no app install an automation, so the trigger half is always created
by hand. The action half ships inside the app: three ready-made shortcuts
with every field pre-wired, so the token bindings (Get Amount, Get Content,
Source) never need to be touched.

1. In the app: **Settings > Shortcuts**, tap a shortcut, choose **Shortcuts**
   in the share sheet, then **Set Up Shortcut > Add Shortcut**. Repeat for
   each channel you use.
2. In Shortcuts: Automation tab > **+** > **Automation** pill > pick the
   trigger > add the action **Run Shortcut** and select the imported one
   (Kopifund SMS, Kopifund Email or Kopifund Wallet). The trigger's input
   passes through to the shortcut on its own. When a trigger delivers
   several items in one run, as Email can after a busy fetch, each is
   ingested as its own alert.

| Shortcut | Trigger | Notes |
| --- | --- | --- |
| Kopifund SMS | **Message** | Sender `DBS` for DBS alerts; sender fields are tokens: type, Return, checkmark |
| Kopifund Email | **Email** | Only fires for accounts in Apple Mail (Settings > Apps > Mail > Mail Accounts); Gmail/Outlook apps cannot fire it. PayLah sends no SMS, so this is its only path. Sender `dbs` catches `*@dbs.com` and PayLah mail |
| Kopifund Wallet | **Wallet** ("When I tap a Wallet Card or Pass") | Leave the card filter on Any Card |

Each automation finishes in the background without opening the app. The files
the app bundles and exports (`Alerts/Resources/Shortcuts/*.shortcut`) are
signed, in "anyone" mode, so iOS imports them without a setting. A signed
shortcut carries the signing Apple ID's validation record (its account id and
hashed email and phone), so sign with an account you are happy to have in the
file. The unsigned sources live in `Shortcuts/unsigned/`; if iOS refuses one of
those, enable **Allow Untrusted Shortcuts** (Settings > Apps > Shortcuts) once,
then add it again. To regenerate a bundled copy, run
`shortcuts sign -m anyone -i <unsigned> -o <bundled>` on a Mac signed into
iCloud.

The Wallet trigger does not always hand over a purchase: it fires for every
tap of a card or pass, and a tap can reach the shortcut before the card's
issuer has sent the merchant and amount. Such a capture (amount 0, no
merchant) is kept but never approvable; see "Needs details" below. The
unsigned Wallet source lists the Wallet transaction among the input types it
accepts (`WFWalletTransactionContentItem`), which earlier copies left out; the
bundled signed copy is regenerated from it with the command above.

The merchant and card names arrive from Apple's Wallet record, which prints
them title-cased ("Sgh - Digestive & Live") where the bank's SMS prints the
raw descriptor ("SGH - DIGESTIVE & LIVE"). The app stores what it is handed;
the Source row on a transaction says which path wrote it.

## Review and sync

One spend can arrive over two channels: a Wallet tap and the bank's SMS for
the same charge each write a row. With no shared id between channels a new
row counts as the same spend when the amount, currency and day all agree
with an existing row and the payees match once case and punctuation are
dropped ("Sgh - Digestive & Live" equals "SGH - DIGESTIVE & LIVE"). The
poorer of the pair (the one without the bank's raw text) parks itself
under **Possible duplicates** and never syncs; open it to see the row it
matched and tap **Not a duplicate** if two real charges tripped the rule.

A spend in another currency never posts as the plan's. Each sync reads the
YNAB plan's currency; a row that printed a different one ("USD 12.00", or a
Wallet tap whose shortcut passes Currency JPY) goes back to review with the
reason on it, and nothing converts it. Open it, type what it cost in the
plan's currency under **Amount in SGD** (the plan's code) and tap **Post in
SGD**; the button stays off until a figure is typed. A row whose source printed
no currency counts in the plan's. The bundled Wallet shortcut does not pass a
currency yet, so its taps count in the plan's currency; add the Currency
field to the Ingest Wallet Transaction action to pass one.

A row with an amount of 0 or no payee, such as a Wallet tap whose details
never came through, waits under **Needs details** with what it lacks. It
cannot be approved, "Approve all" skips it, it never pairs as a duplicate,
and a sync never posts it to YNAB or to the linked record (a row an older
build approved goes back to review). Open it and type the amount and payee
to make it an ordinary pending row, or swipe to delete it; **Delete all**
clears the section. The first launch of this build deletes, once, the empty
Wallet captures older builds queued: rows from the Wallet intent with amount
0, no payee and no alert text that never reached YNAB or the record, and
logs how many went.

Matches wait in the Inbox. Edit payee, amount, date, category or memo, then
approve (or approve all). A linked phone sends approved rows to the Kopifund
hub; with no YNAB set up such a row reads "in Kopifund" once the hub has it,
and with no link either it reads "Waiting for Kopifund: link this phone in
Settings" until one is made. A row that printed no currency counts in the
YNAB plan's currency, or in SGD, the record's own, when no plan is set.
With YNAB set up, approved rows also sync to YNAB as uncleared and
unapproved so YNAB still shows them for review; a setting flips that. Rows
carry their sync state (pending, approved, syncing, synced, failed,
duplicate) and an `import_id` minted on the row's first send and kept, so a
retried sync never duplicates a transaction. It hashes the day, amount, payee
and source with the moment the row was captured, so two real charges with the
same amount, payee and day still post as two (a row an earlier build wrote
keeps that build's formula).

The app owns each sync run, so leaving a tab never cancels one. A run starts
at launch, whenever the app comes back to the front, and after an approval;
it keeps going until no approved row is left. A failed row waits before it is
retried on its own: one minute, doubling up to six hours, and after eight
attempts, or after an error YNAB will repeat every time (a bad account or
category), it waits for **Sync now** in Settings, which retries everything at
once. A rejected token or a rate limit stops the run after one request and
shows a banner in the Inbox; rows stay approved, not failed.

A row's date is a calendar day (`CalendarDate`), never a moment: the day
the alert printed, or the day the Wallet tap or the alert arrived where the
phone was. It posts to YNAB as that `yyyy-MM-dd` whatever zone the phone is
in at sync, and dedup compares those days. An alert that prints no year
(`26 Oct`, `31/12`) takes the latest year that keeps it no later than the
day after it arrived, so `31 Dec` arriving on 1 January is last year's; a
day the year lacks (`29 Feb` in 2027) is unreadable rather than 1 March.
