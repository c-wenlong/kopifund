import type { CheckReport, UploadStep } from "./api";
import { seedFrom, stepStates, type Phase } from "./uploadState";

const at = "2026-10-12T06:00:00+00:00";
const step = (id: number, name: UploadStep["step"], status: UploadStep["status"], output: Record<string, unknown>): UploadStep =>
  ({ id, step: name, status, summary: "", started_at: at, finished_at: at, input: {}, output });
const report = (over: Partial<CheckReport> = {}): CheckReport => ({
  problems: [], statements: 129, rows: 4693, to_add: [], to_add_count: 5, recategorised_count: 1, to_archive_count: 0, needs_rule: [], ...over,
});

describe("seedFrom", () => {
  it("is nothing when the attempt only uploaded", () => {
    expect(seedFrom([step(1, "upload", "ok", {})])).toBeNull();
  });

  it("reads a check back: its counts, its problems, and how many payees had no rule", () => {
    const p = { code: "duplicate-period", subject: "s", fix: "f" };
    const s = seedFrom([step(1, "check", "problems", { ...report({ problems: [p] }),
      needs_rule: [{ text: "x" }, { text: "y" }] })])!;
    expect(s.report?.problems).toEqual([p]);
    expect(s.report?.statements).toBe(129);
    expect(s.needsRule).toBe(2);
    expect(s.report?.needs_rule).toEqual([]);
    expect(s.loaded).toBeNull();
    expect(s.at).toBe(at);
  });

  it("reads a load back as the finished state, with its counts and the rows it added", () => {
    const added = [{ key: "k", date: "2026-09-01", amount: -100, currency: "SGD", account: "", text: "t", payee: "p", category: "c" }];
    const s = seedFrom([
      step(1, "check", "ok", report()),
      step(2, "load", "ok", {
        checked: { statements: 129, rows: 4693, to_add_count: 5, recategorised_count: 1, to_archive_count: 0 },
        counts: { inserted: 5, updated: 1, archived: 0, "trips assigned": 2 }, stats: { stats: 15, transactions: 4693 }, added,
      }),
    ])!;
    expect(s.loaded?.loaded).toBe(true);
    expect(s.loaded?.counts?.inserted).toBe(5);
    expect(s.report?.to_add_count).toBe(5);
    expect(s.report?.to_add).toEqual(added);
  });

  it("reads a refused load as blocked, with the problems it stopped on", () => {
    const p = { code: "block-not-reconciled", subject: "s", fix: "f" };
    const s = seedFrom([step(1, "load", "blocked", {
      checked: { statements: 3, rows: 10, to_add_count: 1, recategorised_count: 0, to_archive_count: 0 }, problems: [p] })])!;
    expect(s.loaded).toEqual({ loaded: false, report: expect.objectContaining({ problems: [p] }) });
  });

  it("lets a check that came after the load win: the load is no longer the last word", () => {
    const s = seedFrom([
      step(1, "load", "ok", { checked: { statements: 1, rows: 1, to_add_count: 5 }, counts: { inserted: 5, updated: 0, archived: 0 } }),
      step(2, "check", "ok", report({ to_add_count: 0 })),
    ])!;
    expect(s.loaded).toBeNull();
    expect(s.report?.to_add_count).toBe(0);
  });

  it("starts over when a file was added after the last check or load, but not for a refused or repeated one", () => {
    const check = step(1, "check", "ok", report());
    expect(seedFrom([check, step(2, "upload", "ok", {})])).toBeNull();
    expect(seedFrom([check, step(2, "upload", "refused", {}), step(3, "upload", "duplicate", {})])?.report?.statements).toBe(129);
    // an upload before the check is already in it
    expect(seedFrom([step(1, "upload", "ok", {}), step(2, "check", "ok", report())])?.report).not.toBeNull();
  });

  it("lets a newer failed step win over the success before it", () => {
    const ok = step(1, "check", "ok", report());
    // the retry errored: nothing from before it describes the month now
    expect(seedFrom([ok, step(2, "check", "error", { error: "boom" })])).toBeNull();
    // a failed load leaves the check it followed in place, and no load
    const s = seedFrom([ok, step(2, "load", "error", { error: "boom" })])!;
    expect(s.loaded).toBeNull();
    expect(s.report?.statements).toBe(129);
    // a failed mirror after a good one is not a mirrored month
    const load = step(3, "load", "ok", { checked: { statements: 1 }, counts: { inserted: 1, updated: 0, archived: 0 } });
    const good = step(4, "mirror", "ok", { code: 0, output: "done" });
    expect(seedFrom([load, good, step(5, "mirror", "error", { error: "boom" })])?.mirror).toBeNull();
    expect(seedFrom([load, good])?.mirror).not.toBeNull();
  });

  it("keeps a mirror that ran after the load", () => {
    const load = step(2, "load", "ok", { checked: { statements: 1 }, counts: { inserted: 1, updated: 0, archived: 0 } });
    expect(seedFrom([load, step(3, "mirror", "ok", { code: 0, output: "done" })])?.mirror).toEqual({ code: 0, output: "done" });
    expect(seedFrom([step(1, "mirror", "ok", { code: 0, output: "old" }), load])?.mirror).toBeNull();
  });
});

describe("stepStates", () => {
  const base: Phase = { uploads: [], busy: "", report: null, blockers: 0, nothingNew: false, loaded: null, mirror: null };

  it("starts with nothing done", () => {
    expect(stepStates(base)).toEqual({ add: "pending", check: "pending", load: "pending", mirror: "pending" });
  });

  it("shows the running step", () => {
    expect(stepStates({ ...base, busy: "check" }).check).toBe("running");
    expect(stepStates({ ...base, busy: "upload" }).add).toBe("running");
    expect(stepStates({ ...base, busy: "load" }).load).toBe("running");
    expect(stepStates({ ...base, busy: "mirror" }).mirror).toBe("running");
  });

  it("blocks Add when every file was refused, and Check when a problem stands", () => {
    expect(stepStates({ ...base, uploads: [{ status: "refused" }] }).add).toBe("blocked");
    expect(stepStates({ ...base, uploads: [{ status: "refused" }, { status: "already-there" }] }).add).toBe("done");
    const s = stepStates({ ...base, report: report(), blockers: 2 });
    expect(s.check).toBe("blocked");
    // Add is done once a check exists: the files are there
    expect(s.add).toBe("done");
    expect(s.load).toBe("pending");
  });

  it("finishes Load when it wrote, or when there was nothing to write", () => {
    expect(stepStates({ ...base, report: report(), loaded: { loaded: true, report: report() } }).load).toBe("done");
    expect(stepStates({ ...base, report: report({ to_add_count: 0, recategorised_count: 0 }), nothingNew: true }).load).toBe("done");
    expect(stepStates({ ...base, report: report(), loaded: { loaded: false, report: report() } }).load).toBe("blocked");
  });

  it("marks a failed mirror as blocked", () => {
    expect(stepStates({ ...base, mirror: { code: 1 } }).mirror).toBe("blocked");
    expect(stepStates({ ...base, mirror: { code: 0 } }).mirror).toBe("done");
  });
});
