import { Check, Circle, LoaderCircle, X } from "lucide-react";
import type { StepName } from "../types.gen";
import { KindIcon, STEP_ICON } from "../icons";

// G13: the slim strip at the top of the Upload page: Add, Check, Load and (when
// Notion is on) Mirror, each pending, running, done or blocked, so a visit
// that opens after a check or a load shows where the month stands.
// G18: each step's name leads with its icon, the same one Past uploads uses.
export type StepState = "pending" | "running" | "done" | "blocked";
export type StepItem = { key: string; step: StepName; label: string; state: StepState; note?: string };

const WORD: Record<StepState, string> = { pending: "not started", running: "running", done: "done", blocked: "blocked" };

function Mark({ state }: { state: StepState }) {
  const base = "grid size-6 shrink-0 place-items-center rounded-full";
  if (state === "done") return <span className={`${base} bg-good-bg text-good`}><Check size={14} aria-hidden /></span>;
  if (state === "blocked") return <span className={`${base} bg-danger-bg text-danger`}><X size={14} aria-hidden /></span>;
  if (state === "running") return <span className={`${base} bg-info-bg text-info`}><LoaderCircle size={14} className="animate-spin" aria-hidden /></span>;
  return <span className={`${base} border border-line text-ink-3`}><Circle size={8} aria-hidden /></span>;
}

export function UploadStepper({ steps }: { steps: StepItem[] }) {
  return (
    <ol aria-label="Steps" className="m-0 flex list-none items-start gap-1 rounded-xl border border-line bg-panel px-3 py-2.5 sm:items-center sm:px-4">
      {steps.map((s, i) => (
        <li key={s.key} data-step={s.key} data-state={s.state} className={`flex min-w-0 flex-1 items-center justify-center gap-2 sm:justify-start ${i < steps.length - 1 ? "" : "sm:flex-none"}`}>
          <div className="flex min-w-0 flex-1 flex-col items-center gap-1 sm:flex-none sm:flex-row sm:gap-2">
            <Mark state={s.state} />
            <div className="min-w-0 text-center sm:text-left">
              <div className={`inline-flex items-center gap-1 text-[13px] leading-tight ${s.state === "pending" ? "text-ink-2" : "font-medium text-ink"}`}>
                <KindIcon icon={STEP_ICON[s.step]} size={13} tone="ink-3" />
                {s.label}<span className="sr-only">: {WORD[s.state]}</span>
              </div>
              {s.note && <div className="num truncate text-[11px] leading-tight text-ink-3">{s.note}</div>}
            </div>
          </div>
          {i < steps.length - 1 && <span aria-hidden className="mt-3 hidden h-px min-w-4 flex-1 bg-line sm:mt-0 sm:block" />}
        </li>
      ))}
    </ol>
  );
}
