import type { CheckReport, LoadResult, UploadStep } from "./api";
import type { StepState } from "./components/UploadStepper";

// G13: the Upload page keeps no state between visits, but the upload history
// does: the server records every step with what it found. A visit that opens
// after a check or a load reads the latest attempt back, so the stepper and the
// cards show where the month really stands instead of a fresh start.

export type Seed = {
  report: CheckReport | null;
  loaded: LoadResult | null;
  /** when the check (or the load's own check) ran */
  at: string | null;
  /** payees the check found with no rule; the history masks their text, so they are counted, not listed */
  needsRule: number;
  mirror: { code: number; output: string } | null;
};

const obj = (v: unknown): Record<string, unknown> => (v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {});
const num = (v: unknown) => (typeof v === "number" ? v : 0);

/** The state of the month as the latest attempt left it; null when nothing there ran a check or a load. */
export function seedFrom(steps: UploadStep[]): Seed | null {
  const ordered = [...steps].sort((a, b) => a.id - b.id);
  const last = (name: string, ok: (s: UploadStep) => boolean = () => true) => [...ordered].reverse().find((s) => s.step === name && ok(s));
  // the newest step of each kind decides: a retry that errored has no report in its output, and the success before it is not the state any more
  let check = last("check");
  let load = last("load");
  let mirror = last("mirror");
  if (check && !Array.isArray(check.output.problems)) {
    const failed = check.id;
    check = undefined;
    if (load && load.id < failed) load = undefined;
    if (mirror && mirror.id < failed) mirror = undefined;
  }
  if (load && !load.output.checked) {
    const failed = load.id;
    load = undefined;
    if (mirror && mirror.id < failed) mirror = undefined;
  }
  if (mirror && typeof mirror.output.code !== "number") mirror = undefined;
  // a file added after the last check or load is not in either: start over. A refused or repeated file changes nothing.
  const added = last("upload", (s) => s.status === "ok");
  if (added && added.id > Math.max(load?.id ?? 0, check?.id ?? 0)) return null;
  const seed: Seed = { report: null, loaded: null, at: null, needsRule: 0, mirror: null };
  if (load && (!check || load.id > check.id)) {
    const checked = obj(load.output.checked);
    const report: CheckReport = {
      problems: (load.output.problems as CheckReport["problems"] | undefined) ?? [],
      statements: num(checked.statements), rows: num(checked.rows),
      to_add: (load.output.added as CheckReport["to_add"] | undefined) ?? [],
      to_add_count: num(checked.to_add_count), recategorised_count: num(checked.recategorised_count),
      to_archive_count: num(checked.to_archive_count), needs_rule: [],
    };
    seed.report = report;
    seed.at = load.started_at;
    seed.loaded = load.status === "ok"
      ? { loaded: true, report, counts: load.output.counts as LoadResult["counts"], stats: load.output.stats as LoadResult["stats"] }
      : { loaded: false, report };
  } else if (check) {
    const o = check.output as unknown as CheckReport;
    seed.report = { ...o, needs_rule: [], to_add: o.to_add ?? [] };
    seed.needsRule = Array.isArray(o.needs_rule) ? o.needs_rule.length : 0;
    seed.at = check.started_at;
  } else {
    return null;
  }
  const after = Math.max(load?.id ?? 0, check?.id ?? 0);
  if (mirror && mirror.id > after) seed.mirror = { code: mirror.output.code as number, output: String(mirror.output.output ?? "") };
  return seed;
}

export type Phase = {
  uploads: { status: "added" | "already-there" | "refused" }[];
  busy: "" | "upload" | "check" | "load" | "mirror";
  report: CheckReport | null;
  blockers: number;
  nothingNew: boolean;
  loaded: LoadResult | null;
  mirror: { code: number } | null;
};

/** Each step of the stepper: not started, running, done or blocked. */
export function stepStates(p: Phase): Record<"add" | "check" | "load" | "mirror", StepState> {
  const kept = p.uploads.some((u) => u.status !== "refused");
  return {
    add: p.busy === "upload" ? "running" : p.uploads.length ? (kept ? "done" : "blocked") : p.report ? "done" : "pending",
    check: p.busy === "check" ? "running" : p.report ? (p.blockers ? "blocked" : "done") : "pending",
    load: p.busy === "load" ? "running" : p.loaded?.loaded ? "done" : p.loaded ? "blocked"
      : p.report && !p.blockers && p.nothingNew ? "done" : "pending",
    mirror: p.busy === "mirror" ? "running" : p.mirror ? (p.mirror.code === 0 ? "done" : "blocked") : "pending",
  };
}
