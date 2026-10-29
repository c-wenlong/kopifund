<p align="center"><img src="apps/web/public/icon-512.png" width="128" alt="Kopifund's app icon"></p>

# Kopifund

A personal finance tracker for people in Singapore, built on the statements
their banks and wallets already send them. The name is the local flavour: the
money you track is the money that keeps the kopi coming.

Kopifund does two things:

1. **Look back.** Read the PDF statements from every bank, wallet, brokerage and
   CPF account you hold, turn them into one ledger, name each payee and
   category with rules, and show where the money went: cash flow per month,
   categories, recurring payments, trips, cash runway and net worth.
2. **Plan ahead.** Set monthly budgets per category, project the months ahead
   from your real spending and income, and see how long your cash lasts.

It runs in the browser as a web app (installable as a PWA), served from your
own machine. It describes and projects; it does not give investment advice.

## Status

The pipeline runs: statements are parsed, categorised and checked from the data
folder and written to `kopifund.db`, the local SQLite record, which the analytics
read. Notion is an optional mirror. The app runs the whole month from the
browser: a first-run setup, the dashboard and every stat, adding statements,
the check and the load, rules, budgets, trips and the plan, and export. See
[ROADMAP.md](ROADMAP.md).

## Quickstart

```bash
make install                      # once: npm packages for apps/web/
make migrate FROM="<old folder>"  # once, if you used finance-tracking
make doctor                       # parse and check every statement
make load                         # write them to kopifund.db
make analyse                      # run the stats from kopifund.db
make serve                        # build the app and open http://127.0.0.1:8770
```

### The build warns that a chunk is over 500 kB

`make serve` ends its build with Vite's warning that `index-*.js` is larger
than 500 kB (about 1 MB, 290 kB gzipped). Leave it. The app is served from
127.0.0.1 and the service worker caches the bundle after the first load, so the
size costs nothing.

Splitting the app into lazy-loaded pages (`React.lazy` per page) only makes the
first load faster over a network, and on localhost that gain is near zero. It
also adds a way to break: after a rebuild, a tab that is still open can ask for
a page chunk the build has deleted, and that page fails to load until a reload.
A single bundle cannot fail that way, because the open tab already holds every
page.

Revisit it only if Kopifund is served over a real network. Then split by page
and, in the same change, add an error boundary that reloads the page when a
chunk fails to load.

## Supported statements

| Provider | What is read |
| --- | --- |
| DBS | Multiplier account e-statements, debit card statements |
| POSB | Everyday Savings e-statements |
| DBS PayLah! | Wallet e-statements |
| YouTrip | Monthly statement per currency wallet |
| Revolut | "All accounts" statement export |
| Wise | Statement per currency balance |
| OCBC | 360 Account statements (from a public sample) |
| UOB | One Account and savings account statements (from a public sample) |
| Tiger Brokers | Annual account statement |
| CPF | Yearly statement of account |

All of them are PDFs. [SPEC.md](SPEC.md#supported-statements) has the detail.
A statement from any other bank is named as not supported when you upload it,
and nothing of it is kept; `python3 server/finance.py sample <pdf>` turns it into a
text with your details replaced, for whoever builds its layout.

## Documents

| File | What it holds |
| --- | --- |
| [SPEC.md](SPEC.md) | The problem, who it is for, the architecture, the supported statements, the domain model, the rules for money and dates, and the open questions |
| [ROADMAP.md](ROADMAP.md) | Goals G0 onwards, in build order, each with what done looks like |
| [AGENTS.md](AGENTS.md) | How to work in this repo, for people and coding agents |

## Privacy

Statements and the database stay on your machine, in `~/.kopifund`. Kopifund
never asks for a bank login and never connects to a bank; it reads the PDFs you
download yourself. Nothing leaves the machine unless you switch it on or use it:

- **Notion mirror and exports.** What you choose to mirror to your own Notion
  workspace, or export.
- **AI payee suggestions.** After an upload check, the merchant text of rows no
  rule matched, and a rounded typical amount unless `send_amounts = false`
  under `[ai]`, go to the model provider you set in Settings. Turn AI off in Settings to stop it;
  `[ai] enabled = false` only turns it off while Settings has not picked a provider.
- **Copilot.** Dates, amounts, payees, categories and account display names of
  the rows a question needs go to that provider, only after you turn on "Send
  matching rows" in Settings (a model on this machine needs no switch).
- **The iOS app.** The transactions you approve go to `api.ynab.com`, your YNAB
  plan, with the personal access token you give it.
- **The phone link (G25).** Once you pair a phone (Settings, "Link a phone")
  and run `tailscale serve`, the phone reads your profile, transactions,
  categories and overview from this machine over your own tailnet, with a
  device token you can revoke, and posts the alert rows you approve back to it.
  The server still listens on this machine only; nothing is opened to the
  internet. Without the pairing, nothing is shared.
