import type { ProblemRow } from "./api";
import type { Account, Bank } from "./accounts";
import { formatMonth } from "./dates";

// G13: a check's problems in plain words. The server sends a code, a subject
// (a statement file or a period) and one developer sentence; the Upload page and
// Past uploads show a headline, where it is, and one next step, with the code
// on a details line. Every code finance/sync.py (and finance.py) can send is
// here, and tests/test_upload_problems.py keeps the two lists in step. A code
// not here still shows, under a general headline, with the server's own sentence.

type Known = {
  headline: string;
  next: string;
  /** the server's own sentence goes on the details line: it names something the plain words cannot */
  fix?: boolean;
};

const MOVE_OUT = "Move the extra PDF out of the statements folder (documents/superseded is fine), then check again. Never delete a statement.";

export const KNOWN: Record<string, Known> = {
  // the parsers and the file checks: these stop a load with no Notion at all
  "duplicate-period": { headline: "Two files for the same month", next: MOVE_OUT },
  "duplicate-key": { headline: "The same transaction is in two files", next: "Two statements overlap. Move the older one out of the statements folder, then check again." },
  "block-not-reconciled": {
    headline: "Statement doesn't add up",
    next: "Move the bad file into documents/superseded first (never delete it). Then download the statement from your bank again, upload it and check again.",
  },
  "balance-unexplained": {
    headline: "Closing balance missing",
    next: "The statement prints no closing balance. Make sure it is the whole statement, not a part of one, then check again.",
  },
  "same-file-two-places": {
    headline: "The same statement is saved twice",
    next: "Two paths hold the same file. Move the extra copy out of the statements folder (documents/superseded is fine), then check again. Never delete a statement.",
  },
  "statement-moved": {
    headline: "A statement is in another account's folder",
    next: "This file moved since the last load, and a statement in the wrong account would take its rows with it. Move it back to where the line below says, then check again.",
    fix: true,
  },
  "pdf-not-parsed": {
    headline: "Statement couldn't be read",
    next: "No transactions came out of this PDF. Check it belongs to this account, or move it out of the statements folder.",
  },
  "no-rows-parsed": {
    headline: "Statement layout not recognised",
    next: "Check the PDF is in the right account's folder, or move it out of the statements folder.",
  },
  "unknown-wallet-folder": {
    headline: "Currency folder not set up",
    next: "This currency is not one of the account's wallets. Add it to the account's currency folders in config.toml, or move the file.",
    fix: true,
  },
  "folder-missing": { headline: "Statement folder missing", next: "Create the folder in your data folder, or remove its entry from config.toml." },
  "parser-failed": {
    headline: "A statement reader stopped",
    next: "Nothing was loaded. Fix or move the file named here, then check again.",
  },
  "rules-invalid": { headline: "Your rules didn't load", next: "Open Rules, fix the line named below, then check again.", fix: true },
  "row-without-block": {
    headline: "A transaction lost its statement",
    next: "Check again. If it stays, move the newest upload out of the statements folder and check once more.",
    fix: true,
  },
  "pdftotext-missing": { headline: "pdftotext is not installed", next: "Run brew install poppler, then reload this page." },
  // the Notion mirror's checks: they reach this page only through Past uploads
  "unknown-account": { headline: "Account not linked to Notion", next: "Add the account in Notion and in config.toml under [accounts], then try again." },
  "unknown-card": { headline: "Card not linked to Notion", next: "Add the card in Notion and in config.toml under [cards], then try again." },
  "unknown-option": { headline: "Notion is missing an option", next: "Run python3 finance.py add-options, then try again.", fix: true },
  "missing-payee": { headline: "A payee is not in Notion", next: "Run python3 finance.py payees, then try again." },
  "missing-category": { headline: "A category is not in Notion", next: "Create the category in Notion and check its spelling in your rules." },
  "implausible-insert": {
    headline: "Far more new rows than expected",
    next: "A reader change may have rewritten every key. Compare a few new rows with Notion before you load.",
  },
  "orphan-rows": {
    headline: "Rows no longer in any statement",
    next: "A statement may have left the folder. Put it back, or archive these rows once you have checked them.",
  },
  "orphan-blocks": {
    headline: "Months no longer in any statement",
    next: "A statement may have left the folder. Put it back, or archive these months once you have checked them.",
  },
  "block-row-count": { headline: "Notion has a different row count", next: "Run the mirror again. If it repeats, the write is failing.", fix: true },
  "block-total": { headline: "Notion has different totals", next: "Run the mirror again. If it repeats, the write is failing.", fix: true },
  "duplicate-page": { headline: "A transaction is on two Notion pages", next: "Archive the extra pages in Notion: one key must name one page.", fix: true },
  "row-without-account": { headline: "A Notion row has no account", next: "Run the mirror again.", fix: true },
  "row-without-period": { headline: "A Notion row has no month", next: "Run the mirror again.", fix: true },
  "verify-differs": { headline: "Notion didn't take the write", next: "Check the errors in the mirror's output, then run it again.", fix: true },
  "verify-missing": { headline: "Rows are still missing in Notion", next: "Run the mirror again. If it repeats, the create call is failing.", fix: true },
  "verify-orphan-blocks": { headline: "Notion has a month no file covers", next: "Put the statement back, or archive the month once you have checked it.", fix: true },
  "verify-orphans": { headline: "Notion kept rows it should have archived", next: "Run the mirror again.", fix: true },
};

export type ProblemView = {
  code: string;
  headline: string;
  next: string;
  /** the account the problem is in, as the upload picker names it; null when nothing says */
  account: string | null;
  /**
   * G18: the accounts a statement folder names, each with its bank for the mark
   * before its name, in the order `account` lists them; empty when the account
   * comes from a period's label, which no folder gives a bank for
   */
  accounts: { name: string; bank: Bank }[];
  /** the statement month, "Jan 2026" */
  month: string | null;
  /** statement files by name, the year folder they sit in first */
  files: string[];
  /** the code, then anything else worth reading, for the details line */
  detail: string;
  /** false only for a problem that does not stop a load */
  blocker: boolean;
};

const BOTH_PLACES = new Set(["same-file-two-places", "statement-moved"]);
const NOT_BLOCKING = new Set(["pdftotext-missing"]);
// folders are plain names; the file may hold spaces and brackets ("7 (1).pdf"), as a downloaded copy does
const PDF = /(?:[\w.-]+\/)+[\w.()-]+(?: [\w.()-]+)*\.pdf/g;
// a period is "account · CUR · 2026-01"; the account is a name or a masked number
const PERIOD = /^(.*?) · ([A-Z]{3}) · (\d{4}-\d{2})/;

/** The account whose folder a statement path sits in, with its currency for a wallet, and its bank. */
export function folderAccount(path: string, list: Account[]): { name: string; bank: Bank } | null {
  // a file inside the account's folder, or the folder itself (a reader that stopped names only the folder)
  const within = (folder: string) => !!folder && (path === folder || path.startsWith(`${folder}/`));
  for (const a of list) {
    if (within(a.path)) return { name: a.name, bank: a.bank };
    const w = a.wallets.find((x) => within(x.path));
    if (w) return { name: `${a.name} ${w.currency}`, bank: a.bank };
  }
  return null;
}

export function describeProblem(p: ProblemRow, list: Account[] = []): ProblemView {
  const known = KNOWN[p.code];
  const paths = [...new Set([...(p.subject.match(PDF) ?? []), ...(p.fix.match(PDF) ?? [])])];
  const period = PERIOD.exec(p.subject);
  // a reader that stopped may name only the account folders it was given, as "a; b"
  const folders = !paths.length && !period && p.code === "parser-failed" ? p.subject.split("; ").map((f) => f.trim()).filter(Boolean) : [];
  // G18: each account keeps its bank beside its name, so every one named gets its mark
  const ofFolders = folders.map((f) => folderAccount(f, list)).filter((a): a is { name: string; bank: Bank } => !!a)
    .filter((a, i, all) => all.findIndex((b) => b.name === a.name) === i);
  const ofFile = paths.length ? folderAccount(paths[0], list) : null;
  const accounts = ofFile ? [ofFile] : period ? [] : ofFolders;
  const account = ofFile?.name
    ?? (period ? `${period[1]} ${period[2]}` : null)
    ?? (ofFolders.length ? ofFolders.map((a) => a.name).join(", ") : null);
  // two paths for one file differ only by their account folder, so those name it in full
  const files = paths.map((f) => (BOTH_PLACES.has(p.code) ? f.replace(/^statements\//, "") : f.split("/").slice(-2).join("/")));
  // the subject is shown on the details line only when nothing above already says it
  const extra = !paths.length && !period && !ofFolders.length && p.subject ? [p.subject] : [];
  return {
    code: p.code,
    headline: known?.headline ?? "Something needs a look",
    next: known?.next ?? (p.fix || "Check again, then open Past uploads for the details."),
    account,
    accounts,
    month: period ? formatMonth(period[3]) : null,
    files,
    detail: [p.code, ...extra, ...(known?.fix && p.fix ? [p.fix] : [])].join(" · "),
    blocker: !NOT_BLOCKING.has(p.code),
  };
}
