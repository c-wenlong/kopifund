# Alerts: bank alerts into YNAB (G24)

A personal iOS app (SwiftUI, iOS 17+) that turns bank transaction alerts into
YNAB transactions. It exists for unlinked Singapore bank accounts (DBS, OCBC,
UOB), which YNAB cannot import directly.

iOS does not let apps read notifications or the SMS inbox, so the app only
processes text handed to it: through a Shortcuts automation, the share sheet,
or a paste box inside the app.

## Layout

| Folder | What it is |
| --- | --- |
| `Packages/Matching` | Pure Swift, no UI, no dependencies: tokenizer, alert-reading helpers (boilerplate, field candidates, shape signatures), tap-to-pattern builder, regex matcher, unit tests |
| `Packages/YNABClient` | `api.ynab.com` over `URLSession` + `Codable`: plans, accounts, categories, payees, transaction creation, milliunits, `import_id` |
| `Packages/Storage` | SwiftData models, the shared App Group store, and the suppressed alert-shape set |
| `Packages/Intents` | `Ingest Alert` and `Ingest Wallet Transaction` App Intents |
| `Alerts` | The SwiftUI app: Inbox, Unmatched, Alert reader, Pattern builder, Settings |
| `ShareExtension` | Share-sheet target that runs the same ingest path |
| `project.yml` | XcodeGen spec; `xcodegen` regenerates `Alerts.xcodeproj` |

The only network host the app calls is `api.ynab.com`. There is no backend,
no analytics, and no third-party dependency.

## Build

```sh
brew install xcodegen   # once
cd apps/ios
xcodegen
open Alerts.xcodeproj   # set your own signing team on the Alerts target
```

Run package tests with `swift test` inside any of `Packages/Matching`,
`Packages/YNABClient`, `Packages/Storage` or `Packages/Intents`, or build the app for a simulator with
`xcodebuild -scheme Alerts -destination 'platform=iOS Simulator,name=iPhone 17 Pro'`.

Signing: the bundle ids are `com.kopifund.alerts` and
`com.kopifund.alerts.ShareExtension`; both use the app group
`group.com.kopifund.alerts`. Point the targets at your team and keep or
change the ids, whichever your account allows.

## Setup

1. Open the app once. In **Settings**, paste your YNAB personal access token
   (YNAB > Account Settings > Developer Settings). It goes to the Keychain,
   never into a file.
2. Tap **Fetch plans & accounts**, pick your plan, then pick the default
   account Wallet transactions post to.
3. Build a pattern: **Patterns > +**, paste a real bank alert. The detector
   pre-tags its best guesses for the amount, the payee and the date; dashed
   words are weaker guesses that accept with one tap. Pick a field chip and
   tap or drag across words to fix anything, watch "This pattern reads",
   then save with the account it maps to.
4. Everything else is intake: matches land in the Inbox for review, misses
   land in Unmatched where a row opens the alert itself before teaching
   anything.

## Unmatched and teaching

An alert no pattern takes waits in Unmatched. Rows lead with the merchant
the detector reads out of the alert (falling back to its first telling
line), and a chip shows the detector's best money guess when one exists. Tapping a row opens
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

Anchors read as regex, not verbatim text: digits and date-shaped runs inside
the fixed words fold into wildcards on their own, so a reference number or a
dated line that changes every alert cannot break the match. A number that
must pin the pattern, like a card's last digits, is tagged Card instead, and
a later alert matches only when it prints the same number. The payee capture
reads only to the end of its line. A wildcard or a folding piece right
beside the payee is refused at build time: a fixed word or a printed line
break has to stand there, or the regex cannot tell where the name ends.

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

Each automation finishes in the background without opening the app. If iOS
refuses an unsigned shortcut file, enable **Allow Untrusted Shortcuts**
(Settings > Apps > Shortcuts) once, then add it again.

The unsigned sources live in `Shortcuts/unsigned/`; regenerate the bundled
copies with `shortcuts sign -m anyone -i <unsigned> -o <bundled>` on a Mac
signed into iCloud if signed files are ever needed.

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
poorer of the pair — the one without the bank's raw text — parks itself
under **Possible duplicates** and never syncs; open it to see the row it
matched and tap **Not a duplicate** if two real charges tripped the rule.

Matches wait in the Inbox. Edit payee, amount, date, category or memo, then
approve (or approve all). Approved rows sync to YNAB as uncleared and
unapproved so YNAB still shows them for review; a setting flips that. Rows
carry their sync state (pending, approved, syncing, synced, failed,
duplicate) and a
deterministic `import_id`, so a retried sync never duplicates a transaction.
