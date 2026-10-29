import { useMemo, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { useApi, type RunStatus, type StepName, type StepStatus, type UploadAttempt, type UploadStep, type UploadStepOutline } from "../api";
import { accounts as folderAccounts } from "../accounts";
import { formatDateTime, formatTime } from "../dates";
import { onLinkClick } from "../router";
import { Pager, usePager } from "../components/Pager";
import { ProblemCards } from "../components/ProblemCards";
import { StatusChip, type StatusTone } from "../components/StatusChip";
import { TxnRowsTable, type TxnRow } from "../components/TxnRowsTable";
import { buttonClass } from "../components/Button";
import { EmptyState } from "../components/EmptyState";
import { Loading, Problem } from "../components/States";
import { BankMark } from "../components/BankMark";
import { KindLabel, STEP_ICON, STEP_STATUS_ICON } from "../icons";
import { folderAccount } from "../uploadProblems";

// Every upload attempt, newest first: which steps it ran, where it stopped, and
// for each step what went in and what came out, so an old error can be found
// again. Steps are recorded by the server (finance/upload_log.py).

const STEPS: StepName[] = ["upload", "check", "load", "mirror"];
const STEP_LABEL: Record<StepName, string> = { upload: "Upload", check: "Check", load: "Load", mirror: "Mirror" };
const ORIGIN_LABEL: Record<string, string> = { rules: "from Rules", transactions: "from Transactions", app: "from the app" };

// G13: a step's status in the words the Upload page uses for it
export function statusText(step: StepName, status: StepStatus): string {
  if (status === "ok") return { upload: "Added", check: "Passed", load: "Loaded", mirror: "Mirrored" }[step];
  return { duplicate: "Already there", refused: "Refused", problems: "Problems", blocked: "Blocked", failed: "Failed", error: "Error" }[status] ?? status;
}

function tone(status: StepStatus): StatusTone {
  if (status === "ok") return "good";
  if (status === "duplicate") return "neutral";
  return "danger";
}

// G18: an attempt that ended well wears the done icon, one that failed the failed one; a quiet stop has none
const endIcon = (end: { ok: boolean; tone: StatusTone }) =>
  !end.ok ? STEP_STATUS_ICON.failed : end.tone === "good" ? STEP_STATUS_ICON.ok : undefined;

function when(iso: string, withDay = true) {
  return withDay ? formatDateTime(iso) : formatTime(iso);
}

// where an attempt ended: its furthest step and how that step went
export function stoppedAt(steps: UploadStepOutline[]): { text: string; ok: boolean; tone: StatusTone } {
  if (steps.length === 0) return { text: "Nothing ran", ok: true, tone: "neutral" };
  const furthest = steps.reduce((a, s) => (STEPS.indexOf(s.step) >= STEPS.indexOf(a.step) ? s : a));
  const ok = furthest.status === "ok" || furthest.status === "duplicate";
  if (ok && furthest.step === "load") return { text: "Loaded", ok, tone: "good" };
  if (ok && furthest.step === "mirror") return { text: "Loaded and mirrored", ok, tone: "good" };
  if (ok && furthest.step === "check") return { text: "Checked, not loaded", ok, tone: "neutral" };
  if (ok) return { text: "Uploaded, not checked", ok, tone: "neutral" };
  return { text: `Failed at ${STEP_LABEL[furthest.step].toLowerCase()}`, ok, tone: "danger" };
}

// what an attempt did, in the server's own counts (finance/upload_log.py writes
// the summaries): what a load wrote, else what a check found, else the files saved
export function outcome(steps: UploadStepOutline[]): string | null {
  const last = (name: StepName) => [...steps].reverse().find((s) => s.step === name && (s.status === "ok" || s.status === "problems"));
  const load = last("load");
  const wrote = load && /added (\d+), updated (\d+), archived (\d+)/.exec(load.summary);
  if (wrote) return `${wrote[1]} added, ${wrote[2]} updated, ${wrote[3]} archived`;
  const check = last("check");
  const found = check && /([\d,]+) to add, ([\d,]+) recategorised, ([\d,]+) to archive/.exec(check.summary);
  if (found) return `${found[1]} to add, ${found[2]} recategorised, ${found[3]} to archive`;
  const saved = steps.filter((s) => s.step === "upload" && s.status === "ok").length;
  return saved ? `${saved} ${saved === 1 ? "file" : "files"} saved` : null;
}

type Page = { attempts: UploadAttempt[]; next: number | null; total: number };

export function PastUploads() {
  // G13: pages by number, the server counting the attempts
  const [total, setTotal] = useState<number | undefined>(undefined);
  const pager = usePager("past-uploads", total);
  const list = useApi<Page>(`/api/uploads?${new URLSearchParams({ limit: String(pager.size), offset: String(pager.offset) })}`);
  const [open, setOpen] = useState<number | null>(null);
  if (list.data && list.data.total !== total) setTotal(list.data.total);
  // the folders name the account a problem is in: asked once, when an attempt is first opened, for every one
  const [opened, setOpened] = useState(false);
  if (open != null && !opened) setOpened(true);
  const status = useApi<RunStatus>(opened ? "/api/run/status" : null);
  const accounts = useMemo(() => folderAccounts(Array.isArray(status.data?.folders) ? status.data.folders : []), [status.data]);

  if (list.loading) return <Loading what="Loading your past uploads" />;
  if (list.error || !list.data) return <Problem message={list.error ?? "No history"} retry />;
  const attempts = list.data.attempts;
  if (list.data.total === 0) {
    return (
      <EmptyState title="No uploads yet"
        action={<a href="/upload" onClick={onLinkClick("/upload")} className={buttonClass("primary")}>Upload statements</a>}>
        Each visit to the Upload page that runs a step is kept here.
      </EmptyState>
    );
  }
  return (
    <div className="flex flex-col">
      <ul className="m-0 flex list-none flex-col gap-2 p-0">
        {attempts.map((a) => {
          const end = stoppedAt(a.steps);
          const counts = outcome(a.steps);
          const expanded = open === a.id;
          return (
            <li key={a.id} className="rounded-xl border border-line bg-panel">
              <button type="button" aria-expanded={expanded} onClick={() => setOpen(expanded ? null : a.id)}
                className="flex w-full flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3 text-left hover:bg-hover">
                {expanded ? <ChevronDown size={15} aria-hidden className="text-ink-3" /> : <ChevronRight size={15} aria-hidden className="text-ink-3" />}
                <span className="min-w-44 text-[14px] font-medium">
                  {when(a.started_at)}
                  {a.origin !== "upload" && <span className="ml-2 text-[12px] font-normal text-ink-3">{ORIGIN_LABEL[a.origin] ?? a.origin}</span>}
                </span>
                <StepTrail steps={a.steps} />
                {counts && <span className="num text-[13px] text-ink-2">{counts}</span>}
                <span className="ml-auto"><StatusChip tone={end.tone} icon={endIcon(end)}>{end.text}</StatusChip></span>
              </button>
              {expanded && <AttemptDetail id={a.id} accounts={accounts} />}
            </li>
          );
        })}
      </ul>
      <Pager pager={pager} label="Past uploads pages" />
    </div>
  );
}

function StepTrail({ steps }: { steps: UploadStepOutline[] }) {
  return (
    <span className="flex flex-wrap items-center gap-1.5" aria-label="Steps">
      {STEPS.map((name) => {
        const ran = steps.filter((s) => s.step === name);
        const last = ran[ran.length - 1];
        return (
          <span key={name} data-step={name} className="inline-flex">
            {last
              ? <StatusChip tone={tone(last.status)} icon={STEP_ICON[name]}>{STEP_LABEL[name]}{ran.length > 1 ? ` ×${ran.length}` : ""}</StatusChip>
              : <StatusChip tone="empty" icon={STEP_ICON[name]} title="Not run">{STEP_LABEL[name]}<span className="sr-only"> not run</span></StatusChip>}
          </span>
        );
      })}
    </span>
  );
}

function AttemptDetail({ id, accounts }: { id: number; accounts: ReturnType<typeof folderAccounts> }) {
  const detail = useApi<UploadAttempt<UploadStep>>(`/api/uploads/${id}`);
  if (detail.loading) return <div className="px-4 pb-3"><Loading what="Loading the steps" /></div>;
  if (detail.error || !detail.data) return <div className="px-4 pb-3"><Problem message={detail.error ?? "Not found"} retry /></div>;
  return (
    <ol className="m-0 flex list-none flex-col gap-3 border-t border-line px-4 py-3">
      {detail.data.steps.map((s) => <StepDetail key={s.id} step={s} accounts={accounts} />)}
    </ol>
  );
}

type ProblemRow = { code: string; subject: string; fix: string };

// G18: a statement folder or file, led by the mark of the account whose folder it is in
function FolderName({ path, accounts }: { path: string; accounts: ReturnType<typeof folderAccounts> }) {
  const a = path ? folderAccount(path, accounts) : null;
  if (!a) return <>{path}</>;
  return (
    <span className="inline-flex items-start gap-1.5">
      <span title={a.name} className="inline-flex"><BankMark bank={a.bank} /></span>
      <span className="min-w-0 break-all">{path}</span>
    </span>
  );
}

function StepDetail({ step, accounts }: { step: UploadStep; accounts: ReturnType<typeof folderAccounts> }) {
  const out = step.output;
  const checked = (out.checked ?? out) as Record<string, unknown>;
  const problems = (out.problems ?? []) as ProblemRow[];
  const byStatement = checked.rows_by_statement as Record<string, number> | undefined;
  // a check's statements, and how many of them printed transactions
  const statements = typeof checked.statements === "number" ? checked.statements : undefined;
  const rows = (out.added ?? out.to_add) as TxnRow[] | undefined;
  const plainInput = Object.entries(step.input).filter(([, v]) => typeof v !== "object");
  return (
    <li className="flex flex-col gap-2 text-[13px]">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <KindLabel icon={STEP_ICON[step.step]} size={13} className="font-semibold">{STEP_LABEL[step.step]}</KindLabel>
        <span className="text-ink-3">{when(step.started_at, false)}</span>
        <StatusChip tone={tone(step.status)} icon={STEP_STATUS_ICON[step.status]}>{statusText(step.step, step.status)}</StatusChip>
        <span className="text-ink-2">{step.summary}</span>
      </div>
      {plainInput.length > 0 && (
        <dl className="m-0 grid grid-cols-[max-content_1fr] gap-x-3 gap-y-0.5 text-[12px]">
          {plainInput.map(([k, v]) => (
            <div key={k} className="contents">
              <dt className="text-ink-3">{k}</dt>
              <dd className="m-0 break-all">{k === "folder" ? <FolderName path={String(v)} accounts={accounts} />
                : k === "sha256" ? String(v).slice(0, 16) : String(v)}</dd>
            </div>
          ))}
        </dl>
      )}
      {typeof out.error === "string" && out.error !== step.summary && <p className="m-0 rounded-lg bg-danger-bg px-3 py-2 text-danger">{out.error}</p>}
      {problems.length > 0 && <ProblemCards problems={problems} accounts={accounts} />}
      {byStatement && Object.keys(byStatement).length > 0 && (
        <details>
          <summary className="cursor-pointer text-ink-2">Rows read from each file ({Object.keys(byStatement).length} {Object.keys(byStatement).length === 1 ? "file" : "files"})</summary>
          {statements != null && statements > Object.keys(byStatement).length && (
            <p className="m-0 mt-1 text-[12px] text-ink-3">
              {statements} statements were read. The other {statements - Object.keys(byStatement).length} produced no transaction rows, so they are not listed.
            </p>
          )}
          <div className="mt-1 max-h-56 overflow-auto rounded-lg border border-line">
            <table className="num w-full border-collapse text-[12px]">
              <tbody>
                {Object.entries(byStatement).map(([file, n]) => (
                  <tr key={file} className="border-t border-line first:border-t-0">
                    <td className="break-all px-2 py-1"><FolderName path={file} accounts={accounts} /></td>
                    <td className="px-2 py-1 text-right">{n}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      )}
      {rows && rows.length > 0 && (
        <details>
          <summary className="cursor-pointer text-ink-2">{out.added ? "Transactions added" : "Transactions to add"} ({rows.length}{rows.length === 200 ? ", the newest 200" : ""})</summary>
          <div className="mt-1"><TxnRowsTable list="past-uploads-rows" rows={rows} /></div>
        </details>
      )}
      {typeof out.output === "string" && (
        <pre className="m-0 max-h-56 overflow-auto whitespace-pre-wrap rounded-lg bg-tile p-3 font-sans text-[12px]">{out.output}</pre>
      )}
      <details>
        <summary className="cursor-pointer text-ink-3">Technical details</summary>
        <pre className="m-0 mt-1 max-h-72 overflow-auto whitespace-pre-wrap break-all rounded-lg bg-tile p-3 font-sans text-[12px]">
          {JSON.stringify({ input: step.input, output: step.output }, null, 2)}
        </pre>
      </details>
    </li>
  );
}
