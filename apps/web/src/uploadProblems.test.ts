import { accounts, type Bank } from "./accounts";
import { describeProblem, KNOWN } from "./uploadProblems";

const folders: { path: string; name: string; parser: string; wallet: string; title: string; bank: Bank }[] = [
  { path: "statements/dbs-multiplier", name: "dbs-multiplier", parser: "dbs", wallet: "", title: "DBS Multiplier", bank: "dbs" },
  { path: "statements/youtrip-sek", name: "youtrip-sek", parser: "youtrip", wallet: "SEK", title: "YouTrip", bank: "youtrip" },
  { path: "statements/youtrip-sgd", name: "youtrip-sgd", parser: "youtrip", wallet: "SGD", title: "YouTrip", bank: "youtrip" },
];
const list = accounts(folders);

describe("describeProblem", () => {
  it("words two files for one month: the account, both files and one next step", () => {
    const v = describeProblem({
      code: "duplicate-period",
      subject: "•• 1111 · SGD · 2026-01: statements/dbs-multiplier/2026/2026-01-extra.pdf, statements/dbs-multiplier/2026/2026-01.pdf",
      fix: "two files parse to the same account-month, so the parser stopped",
    }, list);
    expect(v.headline).toBe("Two files for the same month");
    expect(v.account).toBe("DBS Multiplier");
    expect(v.month).toBe("Jan 2026");
    expect(v.files).toEqual(["2026/2026-01-extra.pdf", "2026/2026-01.pdf"]);
    expect(v.next).toMatch(/Move the extra PDF out of the statements folder/);
    // the code is on the details line, and the developer sentence is not
    expect(v.detail).toBe("duplicate-period");
    expect(v.blocker).toBe(true);
  });

  it("finds the file in the fix text when the subject is only a period, and names a wallet by its currency", () => {
    const v = describeProblem({
      code: "block-not-reconciled",
      subject: "•• 2222 · SEK · 2026-03",
      fix: "the balance chain in statements/youtrip-sek/2026/2026-03.pdf does not close; the parser output above says where",
    }, list);
    expect(v.headline).toBe("Statement doesn't add up");
    // the old file goes aside first: the server refuses different bytes at the same path
    expect(v.next).toMatch(/^Move the bad file into documents\/superseded first \(never delete it\)\. Then download/);
    expect(v.account).toBe("YouTrip SEK");
    expect(v.files).toEqual(["2026/2026-03.pdf"]);
  });

  it("falls back to the period's own account label when no file names the account", () => {
    const v = describeProblem({ code: "balance-unexplained", subject: "•• 3333 · SGD · 2026-02", fix: "prints no closing balance" }, list);
    expect(v.account).toBe("•• 3333 SGD");
    expect(v.month).toBe("Feb 2026");
  });

  it("keeps the server's sentence on the details line where it names something the plain words cannot", () => {
    const v = describeProblem({ code: "rules-invalid", subject: "rules/", fix: "the rules did not load: line 4 has no category" }, list);
    expect(v.headline).toBe("Your rules didn't load");
    expect(v.detail).toBe("rules-invalid · rules/ · the rules did not load: line 4 has no category");
  });

  it("shows a folder that is missing", () => {
    const v = describeProblem({ code: "folder-missing", subject: "statements/ocbc-360", fix: "create the folder" }, list);
    expect(v.headline).toBe("Statement folder missing");
    expect(v.detail).toBe("folder-missing · statements/ocbc-360");
  });

  it("words a statement moved to another account, naming where it was", () => {
    const v = describeProblem({
      code: "statement-moved",
      subject: "statements/youtrip-sgd/2026/2026-03.pdf",
      fix: "the record has this file at statements/dbs-multiplier/2026/2026-03.pdf, in another account's folder. Move it back to statements/dbs-multiplier/2026/2026-03.pdf",
    }, list);
    expect(v.headline).toBe("A statement is in another account's folder");
    expect(v.files).toEqual(["youtrip-sgd/2026/2026-03.pdf", "dbs-multiplier/2026/2026-03.pdf"]);
    expect(v.detail).toMatch(/^statement-moved · .*Move it back to statements\/dbs-multiplier\/2026\/2026-03\.pdf$/);
    expect(v.blocker).toBe(true);
  });

  it("words the same file in two places, both paths told apart", () => {
    const v = describeProblem({
      code: "same-file-two-places",
      subject: "statements/dbs-multiplier/2026/2026-03.pdf and statements/youtrip-sgd/2026/2026-03.pdf",
      fix: "these paths hold the same file. Move the extra copy out of statements/",
    }, list);
    expect(v.headline).toBe("The same statement is saved twice");
    expect(v.files).toEqual(["dbs-multiplier/2026/2026-03.pdf", "youtrip-sgd/2026/2026-03.pdf"]);
    expect(v.next).toMatch(/Never delete a statement/);
  });

  it("uses the server's own sentence for a code it has no words for", () => {
    const v = describeProblem({ code: "brand-new-check", subject: "x", fix: "do the new thing" }, list);
    expect(v.headline).toBe("Something needs a look");
    expect(v.next).toBe("do the new thing");
    expect(v.blocker).toBe(true);
  });

  it("shows the broken file and its account when a reader stops, with no command-line wording", () => {
    const v = describeProblem({
      code: "parser-failed",
      subject: "statements/youtrip-sek/2026/broken.pdf",
      fix: "parse_youtrip.py exited 1. Nothing is written until it exits 0",
    }, list);
    expect(v.headline).toBe("A statement reader stopped");
    expect(v.account).toBe("YouTrip SEK");
    expect(v.files).toEqual(["2026/broken.pdf"]);
    expect(v.next).not.toMatch(/reader names|output above/);
    expect(v.detail).toBe("parser-failed");
    expect(`${v.next} ${v.detail}`).not.toMatch(/output above|parse_\w+\.py/);
  });

  it("keeps a file name with spaces and brackets whole, as a downloaded copy has", () => {
    const v = describeProblem({
      code: "parser-failed",
      subject: "statements/dbs-multiplier/2026/7 (1).pdf; statements/youtrip-sek/2026/2026-03.pdf",
      fix: "parse_statements.py exited 1. Nothing is written until it exits 0",
    }, list);
    expect(v.files).toEqual(["2026/7 (1).pdf", "2026/2026-03.pdf"]);
    expect(v.account).toBe("DBS Multiplier");
    expect(v.detail).toBe("parser-failed");
  });

  it("names the account when the reader gave only its folder", () => {
    const v = describeProblem({ code: "parser-failed", subject: "statements/dbs-multiplier", fix: "parse_statements.py exited 1. Nothing is written until it exits 0" }, list);
    expect(v.account).toBe("DBS Multiplier");
    expect(v.files).toEqual([]);
    expect(v.detail).toBe("parser-failed");
  });

  it("keeps each account's bank with its name, for a mark before each (G18)", () => {
    const fix = "parse_statements.py exited 1. Nothing is written until it exits 0";
    const two = describeProblem({ code: "parser-failed", subject: "statements/dbs-multiplier; statements/youtrip-sek", fix }, list);
    expect(two.account).toBe("DBS Multiplier, YouTrip SEK");
    expect(two.accounts).toEqual([{ name: "DBS Multiplier", bank: "dbs" }, { name: "YouTrip SEK", bank: "youtrip" }]);
    // a file names its one account; a period names a label no folder gives a bank for
    expect(describeProblem({ code: "pdf-not-parsed", subject: "statements/youtrip-sgd/2026/2026-03.pdf", fix: "" }, list).accounts)
      .toEqual([{ name: "YouTrip SGD", bank: "youtrip" }]);
    const period = describeProblem({ code: "block-not-reconciled", subject: "•• 3333 · SGD · 2026-02", fix: "" }, list);
    expect(period.account).toBe("•• 3333 SGD");
    expect(period.accounts).toEqual([]);
  });

  it("does not call a missing pdftotext a blocker", () => {
    expect(describeProblem({ code: "pdftotext-missing", subject: "PATH", fix: "brew install poppler" }, list).blocker).toBe(false);
  });

  it("has words for a code on every entry, none of them an em dash", () => {
    for (const [code, k] of Object.entries(KNOWN)) {
      expect(k.headline.length, code).toBeGreaterThan(5);
      expect(k.next.length, code).toBeGreaterThan(5);
      expect(`${k.headline} ${k.next}`, code).not.toContain("\u2014");
    }
  });
});

// Audit: the next step is for someone using the app; a developer's command goes on the details line only
describe("the next step's words", () => {
  it("names no shell command", () => {
    for (const [code, k] of Object.entries(KNOWN)) {
      expect(k.next, code).not.toMatch(/\b(brew|python3|make|finance\.py|npm)\b/);
    }
  });
});
