import { useEffect, useRef, useState } from "react";
import { CheckCircle2 } from "lucide-react";
import {
  postJSON, PUBLIC_SAMPLE, useApi, type CheckReport, type LoadResult, type RunStatus, type SupportedBank, type UploadAttempt,
  type UploadStep,
} from "../api";
import type { Bank } from "../accounts";
import { formatDateTime } from "../dates";
import { accounts, findAccount, flag, type Account } from "../accounts";
import { onLinkClick, useUnsavedGuard } from "../router";
import { seedFrom, stepStates } from "../uploadState";
import { BankMark } from "../components/BankMark";
import { Button, buttonClass } from "../components/Button";
import { BusyStrip } from "../components/BusyStrip";
import { Select } from "../components/Select";
import { Card } from "../components/Card";
import { CollapsibleRows, COLLAPSED_ROWS } from "../components/CollapsibleRows";
import { DropZone } from "../components/DropZone";
import { NeedsRuleTable } from "../components/NeedsRuleTable";
import { ProblemCards } from "../components/ProblemCards";
import { StatusChip } from "../components/StatusChip";
import { Tile } from "../components/Tile";
import { TxnRowsTable } from "../components/TxnRowsTable";
import { UploadStepper } from "../components/UploadStepper";
import { Loading, Problem } from "../components/States";
import { StatsPage } from "./StatsPage";
import { NEEDS_ACCOUNT_ICON, NOT_SUPPORTED_ICON, RUNNING_ICON, STEP_STATUS_ICON } from "../icons";

// G13: a dropped PDF goes to the account its own text names (serve.py,
// /api/statements/auto). One the server cannot place comes back as
// "needs-account" with the folders it could be; it is kept here, not on the
// server, until the owner picks the account, and then sent to that folder.
// G21: one no layout reads comes back "not-supported" with the banks Kopifund
// does read; the server kept nothing of it and there is no account to pick.
type Status = "added" | "already-there" | "refused" | "needs-account" | "not-supported";
type Banks = { supported: SupportedBank[]; comingSoon: { bank: Bank; name: string }[] };
type Sent = {
  id: number;
  file: string;
  status: Status;
  message: string;
  /** the folder it was kept in */
  folder?: string;
  /** a file that needs an account: the folders it could be, and the currency its text names */
  candidates?: string[];
  currency?: string;
  /** the account last chosen for it refused it, and `message` says why */
  refused?: boolean;
  /** a statement no layout reads: the banks Kopifund does */
  banks?: Banks;
};
type Reply = {
  status: Exclude<Status, "refused">; message: string; account_folder?: string; candidates?: string[]; currency?: string;
  supported?: SupportedBank[]; coming_soon?: { bank: Bank; name: string }[];
};
type Busy = "" | "upload" | "check" | "load" | "mirror";

const num = (n: number) => n.toLocaleString("en-SG");
const plural = (n: number, one: string, many = `${one}s`) => `${num(n)} ${n === 1 ? one : many}`;

/** The account a folder belongs to, by the name the picker shows, and its currency for a wallet. */
function accountOf(list: Account[], folder: string) {
  const hit = findAccount(list, folder);
  return hit ? { account: hit.account, name: hit.currency ? `${hit.account.name} ${hit.currency}` : hit.account.name } : null;
}

export function UploadPage() {
  const status = useApi<RunStatus>("/api/run/status");
  // the latest attempts in the history, read back so a visit after a check or a load shows where the month stands.
  // Two, because a visit that only mirrored has no check or load of its own: they are in the one before.
  const latest = useApi<{ attempts: { id: number }[] }>("/api/uploads?limit=2&offset=0");
  const lastId = latest.data?.attempts?.[0]?.id;
  const olderId = latest.data?.attempts?.[1]?.id;
  const history = useApi<UploadAttempt<UploadStep>>(lastId ? `/api/uploads/${lastId}` : null);
  const older = useApi<UploadAttempt<UploadStep>>(olderId ? `/api/uploads/${olderId}` : null);
  // the account a link named (?account=), offered first for a file the server could not place
  const [fallback, setFallback] = useState<{ key: string; currency: string } | null>(null);
  const [uploads, setUploads] = useState<Sent[]>([]);
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null);
  const [busy, setBusy] = useState<Busy>("");
  const [report, setReport] = useState<CheckReport | null>(null);
  const [loaded, setLoaded] = useState<LoadResult | null>(null);
  const [checkedAt, setCheckedAt] = useState<string | null>(null);
  const [needsLeft, setNeedsLeft] = useState(0);
  const [mirror, setMirror] = useState<{ code: number; output: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  // the PDFs that wait for an account, by row id: nothing is sent for them until it is chosen
  const held = useRef(new Map<number, File>());
  const nextId = useRef(1);
  const hasReport = useRef(false);
  hasReport.current = !!report;
  // one upload attempt per visit, started on the first step, so the history
  // (Past uploads) groups this visit's uploads, check, load and mirror
  const attempt = useRef<Promise<number> | null>(null);
  function withAttempt(url: string) {
    attempt.current ??= postJSON<{ attempt: number }>("/api/uploads/start", JSON.stringify({ origin: "upload" }))
      .then((r) => r.attempt)
      .catch((e) => { attempt.current = null; throw e; });
    return attempt.current.then((id) => `${url}${url.includes("?") ? "&" : "?"}attempt=${id}&from=upload`);
  }

  // what the last attempt left, read once; anything done on this page since wins
  const touched = useRef(false);
  const seeded = useRef(false);
  useEffect(() => {
    // wait for the older attempt to settle, but a failed read of it must not hide what the latest one holds
    if (seeded.current || touched.current || !history.data || (olderId && older.loading)) return;
    seeded.current = true;
    const seed = seedFrom([...(older.data?.steps ?? []), ...(history.data.steps ?? [])]);
    if (!seed) return;
    setReport(seed.report);
    setLoaded(seed.loaded);
    setCheckedAt(seed.at);
    setNeedsLeft(seed.needsRule);
    setMirror(seed.mirror);
  }, [history.data, older.data, older.loading, olderId]);

  // a link from another page (?account=dbs-multiplier&currency=SEK, Your profile's "Add statement") names an
  // account: a file whose own text cannot say where it goes offers that one first
  const [preselected, setPreselected] = useState(false);
  if (!preselected && status.data) {
    setPreselected(true);
    const q = new URLSearchParams(window.location.search);
    const hit = findAccount(accounts(status.data.folders), q.get("account") ?? "");
    if (hit) {
      const wanted = (q.get("currency") ?? hit.currency).toUpperCase();
      setFallback({
        key: hit.account.key,
        currency: hit.account.wallets.find((w) => w.currency === wanted)?.currency
          ?? (hit.account.wallets.length === 1 ? hit.account.wallets[0].currency : ""),
      });
    }
  }

  // G18: a PDF waiting for its account is held in this page only, and an upload in progress stops with it:
  // leaving asks first
  useUnsavedGuard(busy === "upload" || uploads.some((u) => u.status === "needs-account"));

  if (status.loading) return <Loading what="Loading your statement folders" />;
  if (status.error || !status.data) return <Problem message={status.error ?? "No status"} retry />;
  const s = status.data;
  const choices = accounts(s.folders);

  // one PDF to the server: to the folder named, or to the one its text names
  async function sendOne(f: File, folder: string | null): Promise<Omit<Sent, "id">> {
    const url = folder === null
      ? `/api/statements/auto?name=${encodeURIComponent(f.name)}`
      : `/api/statements?folder=${encodeURIComponent(folder)}&name=${encodeURIComponent(f.name)}`;
    try {
      const r = await postJSON<Reply>(await withAttempt(url), f, "application/pdf");
      if (r.status === "needs-account") {
        return { file: f.name, status: r.status, message: r.message, candidates: r.candidates ?? [], currency: r.currency ?? "" };
      }
      if (r.status === "not-supported") {
        return { file: f.name, status: r.status, message: r.message, banks: { supported: r.supported ?? [], comingSoon: r.coming_soon ?? [] } };
      }
      return { file: f.name, status: r.status, message: r.message, folder: r.account_folder ?? folder ?? undefined };
    } catch (e) {
      return { file: f.name, status: "refused", message: (e as Error).message };
    }
  }

  // a new file changes what a check would find: what the page shows of the last check, load and mirror goes
  function forget() {
    setReport(null);
    setLoaded(null);
    setMirror(null);                    // a mirror of the older record is not a mirror of this one
    setCheckedAt(null);
    setNeedsLeft(0);
  }
  // and the check runs by itself. A refused or repeated file changes nothing, though a visit with no check
  // yet runs one for what is already there. Load stays a deliberate tap.
  async function settled(added: boolean, kept: boolean) {
    if (added || (kept && !hasReport.current)) await check();
  }

  async function send(files: FileList | null) {
    if (!files?.length || busy) return;
    touched.current = true;
    const list = Array.from(files);
    setBusy("upload");
    setError(null);
    let added = false;
    let kept = false;
    for (const [i, f] of list.entries()) {
      setProgress({ done: i, total: list.length });
      const one = { ...(await sendOne(f, null)), id: nextId.current++ };
      if (one.status === "needs-account") held.current.set(one.id, f);
      if (one.status === "added" && !added) forget();      // what was checked or loaded no longer covers every file
      added ||= one.status === "added";
      kept ||= one.status === "added" || one.status === "already-there";
      setUploads((u) => [one, ...u]);
    }
    setProgress(null);
    setBusy("");
    await settled(added, kept);
  }

  // the owner named the account for a file the server could not place
  async function place(id: number, folder: string) {
    const f = held.current.get(id);
    if (!f || busy) return;
    touched.current = true;
    setBusy("upload");
    setError(null);
    const one = await sendOne(f, folder);
    // a refusal keeps the file waiting, with the reason, so another account can be chosen
    if (one.status !== "refused") held.current.delete(id);
    setUploads((u) => u.map((x) => (x.id !== id ? x
      : one.status === "refused" ? { ...x, message: one.message, refused: true }
      : { ...one, id })));
    if (one.status === "added") forget();
    setBusy("");
    await settled(one.status === "added", one.status === "already-there");
  }

  async function run<T>(step: "check" | "load" | "mirror", url: string, then: (r: T) => void) {
    touched.current = true;
    setBusy(step);
    setError(null);
    try {
      then(await postJSON<T>(await withAttempt(url)));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy("");
    }
  }
  async function check() {
    await run<CheckReport>("check", "/api/run/check", (r) => {
      setReport(r);
      setLoaded(null);                  // the load that came before is not this check's
      setCheckedAt(new Date().toISOString());
      setNeedsLeft(0);
    });
  }
  const load = () => run<LoadResult>("load", "/api/run/load", (r) => {
    setLoaded(r);
    setMirror(null);                    // the record just changed, so the last mirror is stale
    setReport(r.report);
    setCheckedAt(new Date().toISOString());
    setNeedsLeft(0);
  });

  const blocking = report?.problems.filter((p) => p.code !== "pdftotext-missing") ?? [];
  const todo = report ? report.to_add_count + report.recategorised_count + report.to_archive_count : 0;
  const nothingNew = !!report && todo === 0;
  const loadedOk = !!loaded?.loaded;
  // the record holds everything the statements say: just loaded, or a clean check with nothing to add
  const recordCurrent = loadedOk || (nothingNew && blocking.length === 0);
  const waiting = uploads.filter((u) => u.status === "needs-account");
  const done = uploads.filter((u) => u.status !== "needs-account");
  // a file still waiting for its account has not been added, refused or found already there; one no layout
  // reads was not kept, as a refused one was not
  const steps = stepStates({
    uploads: done.map((u) => ({ status: u.status === "not-supported" ? "refused" as const : u.status as "added" | "already-there" | "refused" })),
    busy, report, blockers: blocking.length, nothingNew, loaded, mirror,
  });
  const added = uploads.filter((u) => u.status === "added").length;
  const repeated = uploads.filter((u) => u.status === "already-there").length;
  const refused = uploads.filter((u) => u.status === "refused").length;
  const unsupported = uploads.filter((u) => u.status === "not-supported");
  const banks = unsupported.find((u) => u.banks)?.banks;
  // why Load is off, in words; empty when it can run. A check with no new transactions
  // still leaves Load on: it also records a statement that has none (an empty month).
  const loadWhy = loadedOk ? "Loaded. Check again to load more."
    : !report ? "Run the check first."
    : blocking.length ? `Fix ${plural(blocking.length, "problem")} above first.`
    : "";
  const lastLoad = s.runs[0];
  const newRows = report && !loadedOk && !blocking.length ? report.to_add_count : 0;

  return (
    <div className="flex flex-col gap-3">
      <UploadStepper steps={[
        { key: "add", step: "upload", label: "Add", state: steps.add, note: added ? `${added} added` : waiting.length ? "Needs an account" : uploads.length ? (refused ? "Refused" : unsupported.length ? "Not supported" : "Already there") : undefined },
        { key: "check", step: "check", label: "Check", state: steps.check, note: blocking.length ? plural(blocking.length, "problem") : report ? "Passed" : undefined },
        { key: "load", step: "load", label: "Load", state: steps.load, note: loadedOk ? `${num(loaded?.counts?.inserted ?? 0)} added` : nothingNew && !blocking.length ? "Nothing new" : undefined },
        ...(s.notion_enabled ? [{ key: "mirror", step: "mirror" as const, label: "Mirror", state: steps.mirror, note: mirror ? (mirror.code === 0 ? "Mirrored" : "Failed") : undefined }] : []),
      ]} />
      {!s.pdftotext && <Problem message="pdftotext is not installed." hint="brew install poppler, then reload this page." />}

      <Card title="1. Add this month's statements" actions={
        busy === "upload" ? <StatusChip tone="info" icon={RUNNING_ICON} spin>Saving</StatusChip>
          : waiting.length ? <StatusChip tone="warn" icon={NEEDS_ACCOUNT_ICON}>{waiting.length === 1 ? "Needs an account" : `${num(waiting.length)} need an account`}</StatusChip>
          : added ? <StatusChip tone="good" icon={STEP_STATUS_ICON.ok}>Added {added}</StatusChip>
          : repeated ? <StatusChip tone="neutral" icon={STEP_STATUS_ICON.duplicate}>Already there</StatusChip>
          : refused ? <StatusChip tone="danger" icon={STEP_STATUS_ICON.refused}>Refused</StatusChip>
          : unsupported.length ? <StatusChip tone="warn" icon={NOT_SUPPORTED_ICON}>Not supported</StatusChip>
          : <StatusChip tone="empty">No files yet</StatusChip>}>
        <div className="flex flex-col gap-3 text-[13px]">
          <DropZone disabled={!!busy} reason="Wait for the running step to finish." onFiles={(files) => void send(files)} />
          {busy === "upload" && progress && <BusyStrip what={`Reading file ${Math.min(progress.done + 1, progress.total)} of ${progress.total}`} />}
          {waiting.length > 0 && (
            <ul aria-label="Files that need an account" className="m-0 flex list-none flex-col gap-2 p-0">
              {waiting.map((u) => (
                <NeedsAccountRow key={u.id} sent={u} choices={choices} fallback={fallback} busy={!!busy}
                  onSave={(folder) => void place(u.id, folder)} />
              ))}
            </ul>
          )}
          {done.length > 0 && (
            <ul aria-label="Uploaded files" className="m-0 flex list-none flex-col gap-2 p-0">
              <CollapsibleRows rows={done} limit={COLLAPSED_ROWS.mid} keyOf={(u) => String(u.id)}
                render={(u) => <UploadRow sent={u} where={u.folder ? accountOf(choices, u.folder) : null} />} />
            </ul>
          )}
          {banks && <SupportedBanks banks={banks} />}
        </div>
      </Card>

      <Card title="2. Check" actions={
        <div className="flex items-center gap-2">
          {busy === "check" ? <StatusChip tone="info" icon={RUNNING_ICON} spin>Checking</StatusChip>
            : !report ? <StatusChip tone="empty">Not run</StatusChip>
            : blocking.length ? <StatusChip tone="danger" icon={STEP_STATUS_ICON.problems}>{plural(blocking.length, "problem")}</StatusChip>
            : <StatusChip tone="good" icon={STEP_STATUS_ICON.ok}>Passed</StatusChip>}
          <Button disabled={!!busy} onClick={() => void check()}>
            {busy === "check" ? "Checking…" : report ? "Check again" : "Check statements"}
          </Button>
        </div>}>
        <div className="flex flex-col gap-3 text-[14px]">
          {busy === "check" && <BusyStrip what={report ? `Reading ${plural(report.statements, "statement")}` : "Reading your statements"} />}
          {report && (
            <>
              <p className="m-0 text-[13px] text-ink-2">
                {plural(report.statements, "statement")}, {plural(report.rows, "row")}
                {checkedAt ? `, checked ${formatDateTime(checkedAt)}` : ""}
                {blocking.length > 0 ? ". These counts are partial while a problem stands." : ""}
              </p>
              <div role="group" aria-label="What the check found" className={`grid grid-cols-3 gap-2 sm:gap-3 ${blocking.length ? "opacity-60" : ""}`}>
                <Tile label="To add" value={num(report.to_add_count)} />
                <Tile label="Recategorised" value={num(report.recategorised_count)} />
                <Tile label="To archive" value={num(report.to_archive_count)} />
              </div>
              {blocking.length > 0
                ? <ProblemCards problems={blocking} accounts={choices} />
                : (
                  // G13: the one Load button, right under what the check found, so a long list of rows to add
                  // never pushes it out of sight. It is still a tap: nothing loads by itself.
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <p className="m-0 flex items-center gap-2 text-good"><CheckCircle2 size={16} aria-hidden /> Every statement balances.</p>
                    {!loadedOk && (
                      <Button variant="primary" disabled={!!busy} onClick={() => void load()}>
                        {busy === "load" ? "Loading…" : newRows ? `Load ${plural(newRows, "new row")}` : "Load"}
                      </Button>
                    )}
                  </div>
                )}
              {report.needs_rule.length > 0 && (
                <NeedsRuleTable groups={report.needs_rule} canLoad={blocking.length === 0} onSaved={(go) => (go ? load() : check())} />
              )}
              {needsLeft > 0 && report.needs_rule.length === 0 && (
                <p className="m-0 text-ink-2">{plural(needsLeft, "payee")} had no rule. Check again to name {needsLeft === 1 ? "it" : "them"}.</p>
              )}
              {report.to_add.length > 0 && (
                <div className="flex flex-col gap-1">
                  <h3 className="m-0 text-[13px] font-semibold">Transactions to add</h3>
                  {report.to_add_count > report.to_add.length && (
                    <p className="num m-0 text-[12px] text-ink-3">The newest {num(report.to_add.length)} of {num(report.to_add_count)}.</p>
                  )}
                  <TxnRowsTable list="upload-check" rows={report.to_add} />
                </div>
              )}
            </>
          )}
        </div>
      </Card>

      <Card title="3. Load into Kopifund" actions={
        busy === "load" ? <StatusChip tone="info" icon={RUNNING_ICON} spin>Loading</StatusChip>
          : loadedOk ? <StatusChip tone="good" icon={STEP_STATUS_ICON.ok}>Loaded</StatusChip>
          : loaded ? <StatusChip tone="danger" icon={STEP_STATUS_ICON.blocked}>Blocked</StatusChip>
          : nothingNew && !blocking.length ? <StatusChip tone="neutral" icon={STEP_STATUS_ICON.duplicate}>Nothing new</StatusChip>
          : <StatusChip tone="empty">Not run</StatusChip>}>
        <div className="flex flex-col gap-3 text-[14px]">
          {busy === "load" && <BusyStrip what={report && report.to_add_count ? `Writing ${plural(report.to_add_count, "new transaction")}, then rerunning the stats` : "Writing to your record, then rerunning the stats"} />}
          {busy !== "load" && loadedOk && loaded?.counts && (
            <>
              <div role="group" aria-label="What the load did" className="grid grid-cols-3 gap-2 sm:gap-3">
                <Tile label="Added" value={num(loaded.counts.inserted)} tone={loaded.counts.inserted ? "good" : "neutral"} />
                <Tile label="Updated" value={num(loaded.counts.updated)} />
                <Tile label="Archived" value={num(loaded.counts.archived)} />
              </div>
              <p className="m-0 text-ink-2">
                {plural(loaded.stats?.stats ?? 0, "stat")} rerun.
              </p>
              <div className="flex flex-wrap gap-2 text-[13px]">
                <a href="/transactions" onClick={onLinkClick("/transactions")} className={buttonClass("primary")}>See the new transactions</a>
                <a href="/" onClick={onLinkClick("/")} className={buttonClass("secondary")}>Home</a>
              </div>
            </>
          )}
          {busy !== "load" && loaded && !loaded.loaded && <Problem message="The check found problems, so nothing was loaded." />}
          {busy !== "load" && !loaded && (
            <>
              {(loadWhy || nothingNew) && (
                <p className={`m-0 ${blocking.length ? "text-danger" : "text-ink-2"}`}>
                  {loadWhy || "Nothing new to load. Load anyway to record a statement that has no transactions."}
                </p>
              )}
              {lastLoad && (
                <p className="m-0 text-[12px] text-ink-3">
                  Last load {formatDateTime(lastLoad.started_at)}: {lastLoad.inserted} added, {lastLoad.updated} updated.
                </p>
              )}
            </>
          )}
        </div>
      </Card>

      {s.notion_enabled && (
        <Card title="4. Mirror to Notion (optional)" actions={
          <div className="flex items-center gap-2">
            {busy === "mirror" ? <StatusChip tone="info" icon={RUNNING_ICON} spin>Mirroring</StatusChip>
              : mirror ? (mirror.code === 0 ? <StatusChip tone="good" icon={STEP_STATUS_ICON.ok}>Mirrored</StatusChip> : <StatusChip tone="danger" icon={STEP_STATUS_ICON.failed}>Failed</StatusChip>)
              : <StatusChip tone="empty">Not run</StatusChip>}
            <Button disabled={!!busy || !recordCurrent}
              onClick={() => void run<{ code: number; output: string }>("mirror", "/api/run/mirror", setMirror)}>
              {busy === "mirror" ? "Mirroring…" : "Mirror"}
            </Button>
          </div>}>
          <div className="flex flex-col gap-2">
            {busy === "mirror" && <BusyStrip what="Copying your record to Notion" />}
            {mirror && <pre className="m-0 max-h-64 overflow-auto whitespace-pre-wrap rounded-lg bg-tile p-3 font-sans text-[12px]">{mirror.output}</pre>}
          </div>
        </Card>
      )}
      {error && <Problem message={error} />}
      <StatsPage ids={["data-quality"]} />
    </div>
  );
}

// a long path breaks after a slash on a phone, not in the middle of a name
function wrapAtSlashes(text: string) {
  return text.split("/").flatMap((part, i) => (i ? ["/", <wbr key={i} />, part] : [part]));
}

// one uploaded file: what happened to it and the account it went to, then why when it was refused
function UploadRow({ sent, where }: { sent: Sent; where: { account: Account; name: string } | null }) {
  const chip = sent.status === "added" ? <StatusChip tone="good" icon={STEP_STATUS_ICON.ok}>Added</StatusChip>
    : sent.status === "already-there" ? <StatusChip tone="neutral" icon={STEP_STATUS_ICON.duplicate}>Already there</StatusChip>
    : sent.status === "not-supported" ? <StatusChip tone="warn" icon={NOT_SUPPORTED_ICON}>Not supported</StatusChip>
    : <StatusChip tone="danger" icon={STEP_STATUS_ICON.refused}>Refused</StatusChip>;
  return (
    <li className="grid grid-cols-[max-content_minmax(0,1fr)] items-start gap-x-2 gap-y-0.5">
      {chip}
      <span className="break-all font-medium">{sent.file}</span>
      <span aria-hidden />
      <span className={`flex min-w-0 flex-wrap items-center gap-x-1.5 break-words text-[13px] ${sent.status === "refused" ? "text-danger" : "text-ink-2"}`}>
        {where && (
          <span className="inline-flex items-center gap-1.5 font-medium text-ink">
            <BankMark bank={where.account.bank} /> {where.name}
          </span>
        )}
        <span className="min-w-0 break-words">{wrapAtSlashes(sent.message)}</span>
      </span>
    </li>
  );
}

// G21: under a statement no layout reads, once however many there are: the banks Kopifund reads, from the
// layout registry, one from a public sample saying so, then the ones coming soon, then the command that
// turns the PDF into a text someone can build its layout from
function SupportedBanks({ banks }: { banks: Banks }) {
  const bank = (b: { bank: Bank; name: string }, note?: string) => (
    <li key={b.bank} className="inline-flex items-center gap-1.5 text-ink">
      <BankMark bank={b.bank} /><span>{b.name}{note && <span className="text-ink-3">{note}</span>}</span>
    </li>
  );
  return (
    <div className="flex flex-col gap-1.5 text-[13px] text-ink-2">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
        <span>Kopifund reads</span>
        <ul aria-label="Banks Kopifund reads" className="m-0 flex list-none flex-wrap items-center gap-x-3 gap-y-1.5 p-0">
          {banks.supported.map((b) => bank(b, b.status === PUBLIC_SAMPLE ? `, ${PUBLIC_SAMPLE}` : undefined))}
        </ul>
      </div>
      {banks.comingSoon.length > 0 && (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
          <span>Coming soon</span>
          <ul aria-label="Banks coming soon" className="m-0 flex list-none flex-wrap items-center gap-x-3 gap-y-1.5 p-0">
            {banks.comingSoon.map((b) => bank(b))}
          </ul>
        </div>
      )}
      <p className="m-0">To help add a bank, run <span className="font-medium text-ink">python3 finance.py sample</span> on its PDF.</p>
    </div>
  );
}

// G13: a file the server could not place: why, and a small picker to name its account. Nothing is
// saved for it until Save. The accounts it could be come first; a link's account is chosen already
// when it is one of them (or when the file could be any).
function NeedsAccountRow({ sent, choices, fallback, busy, onSave }: {
  sent: Sent;
  choices: Account[];
  fallback: { key: string; currency: string } | null;
  busy: boolean;
  onSave: (folder: string) => void;
}) {
  const candidates = sent.candidates ?? [];
  const likely = new Set(candidates.map((c) => findAccount(choices, c)?.account.key).filter(Boolean));
  const ordered = [...choices.filter((a) => likely.has(a.key)), ...choices.filter((a) => !likely.has(a.key))];
  const start = fallback && (!candidates.length || likely.has(fallback.key)) ? fallback : null;
  const [key, setKey] = useState(start?.key ?? "");
  const account = choices.find((a) => a.key === key);
  // a wallet account: the currency the statement names, else the link's, else the only one
  const pickCurrency = (a: Account | undefined) => {
    const have = a?.wallets.map((w) => w.currency) ?? [];
    return [sent.currency ?? "", a?.key === fallback?.key ? fallback?.currency ?? "" : ""].find((c) => c && have.includes(c))
      ?? (have.length === 1 ? have[0] : "");
  };
  const [currency, setCurrency] = useState(() => pickCurrency(account));
  const folder = !account ? "" : account.wallets.length ? account.wallets.find((w) => w.currency === currency)?.path ?? "" : account.path;
  return (
    <li className="flex flex-col gap-1.5 rounded-lg border border-line px-3 py-2.5">
      <div className="grid grid-cols-[max-content_minmax(0,1fr)] items-start gap-x-2 gap-y-0.5">
        <StatusChip tone="warn" icon={NEEDS_ACCOUNT_ICON}>Needs an account</StatusChip>
        <span className="break-all font-medium">{sent.file}</span>
        <span aria-hidden />
        <span className={`break-words text-[13px] ${sent.refused ? "text-danger" : "text-ink-2"}`}>{sent.message}</span>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <Select label="Account" placeholder="Choose the account" value={key} className="w-56 max-w-full"
          options={ordered.map((a) => ({ value: a.key, label: a.name, icon: <BankMark bank={a.bank} /> }))}
          onChange={(k) => { setKey(k); setCurrency(pickCurrency(choices.find((a) => a.key === k))); }} />
        {account && account.wallets.length > 0 && (
          <Select label="Currency" placeholder="Currency" value={currency} className="w-32"
            options={account.wallets.map((w) => ({ value: w.currency, label: `${w.currency} ${flag(w.currency)}` }))}
            onChange={setCurrency} />
        )}
        <Button variant="primary" disabled={busy || !folder} onClick={() => onSave(folder)}>
          Save here
        </Button>
      </div>
    </li>
  );
}
