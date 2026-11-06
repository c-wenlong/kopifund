import { useEffect, useRef, useState } from "react";
import { Download, FileJson, FileSpreadsheet, FileText } from "lucide-react";
import { useSidebarMode, type SidebarMode } from "../components/sidebarMode";
import { checkServer, postJSON, useApi, useLink, type ExportDone, type Exports, type ScamChecks, type ScamSettings, type ServerEnding, type ServerInfo, type ServerMode } from "../api";
import { centsText, formatSgd, parseCents } from "../money";
import { formatDay } from "../dates";
import { MoneyInput } from "../components/MoneyInput";
import { SaveBar } from "../components/SaveBar";
import { Button, buttonClass } from "../components/Button";
import { Card } from "../components/Card";
import { StatusChip } from "../components/StatusChip";
import { COLLAPSED_ROWS, CollapsibleRows } from "../components/CollapsibleRows";
import { PageActions } from "../components/PageActions";
import { useWide } from "../components/useWide";
import { useAction } from "../components/useAction";
import { SERVER_ICON, type ServerState } from "../icons";
import { useUnsavedGuard } from "../router";
import { AiCard } from "./AiCard";
import { AppIconCard } from "./AppIconCard";
import { LinkCard } from "./LinkCard";

const options: { value: SidebarMode; label: string }[] = [
  { value: "open", label: "Keep open" },
  { value: "hover", label: "Expand on hover" },
];

export function Settings() {
  const [mode, setMode] = useSidebarMode();
  const wide = useWide();
  // G19: one check as the page opens, then the shared link state (api.ts), which the offline
  // banner reads and keeps asking while the server is down: the chip follows it, so it never
  // says "offline" after the banner has gone
  const { down } = useLink();
  const [checked, setChecked] = useState(false);
  const backend: ServerState = !checked ? "checking" : down ? "offline" : "connected";

  useEffect(() => {
    let live = true;
    void checkServer().then(() => live && setChecked(true));
    return () => { live = false; };
  }, []);

  return (
    <div className="flex flex-col gap-3">
      {/* G13: the local server's state is a chip in the page header, not a card of its own */}
      <PageActions>
        <StatusChip tone={backend === "connected" ? "good" : backend === "offline" ? "danger" : "neutral"}
          icon={SERVER_ICON[backend]} spin={backend === "checking"}>
          {backend === "connected" ? "Server connected" : backend === "offline" ? "Server offline" : "Checking the server"}
        </StatusChip>
      </PageActions>
      <ServerCard />
      {/* under 768px the sidebar is a drawer, so keep-open and hover mean nothing there */}
      {wide && (
        <Card title="Sidebar">
          <fieldset className="m-0 flex flex-col gap-1 border-0 p-0">
            <legend className="sr-only">Sidebar behaviour</legend>
            {options.map((o) => (
              <label key={o.value} className="flex cursor-pointer items-center gap-3 rounded-md px-2 py-1.5 text-[14px] hover:bg-hover">
                <input type="radio" name="sidebar" value={o.value} checked={mode === o.value} onChange={() => setMode(o.value)} />
                {o.label}
              </label>
            ))}
          </fieldset>
        </Card>
      )}
      <AppIconCard />
      {/* G22: the provider, its models and key, and the copilot's consent */}
      <AiCard />
      {/* G25: pair a phone over tailscale serve, and revoke one that is lost */}
      <LinkCard />
      <ScamChecksCard />
      <ExportCard />
    </div>
  );
}

// G23: the server itself: how it was started, how long it has run, and a way
// to stop or restart it. A stopped server answers nothing its own page could
// use to start it again, so the stopped state says where the start is instead
// of offering a button: the desktop app's own start page, or `make serve`.
const SERVER_MODE: Record<ServerMode, string> = {
  serve: "make serve",
  dev: "make dev",
  app: "the Kopifund app",
};

/** "for 4 days 2 hours", "for 3 minutes", "under a minute". */
function runningFor(started: number, now: number): string {
  const m = Math.floor(Math.max(0, Math.floor(now / 1000) - started) / 60);
  const h = Math.floor(m / 60), d = Math.floor(h / 24);
  const word = (n: number, unit: string) => `${n} ${unit}${n === 1 ? "" : "s"}`;
  if (d > 0) return h % 24 ? `${word(d, "day")} ${word(h % 24, "hour")}` : word(d, "day");
  if (h > 0) return m % 60 ? `${word(h, "hour")} ${word(m % 60, "minute")}` : word(h, "hour");
  return m > 0 ? word(m, "minute") : "under a minute";
}

/** "2 minutes", "30 seconds" */
function waitAtMost(seconds: number): string {
  return seconds >= 120 ? `${Math.round(seconds / 60)} minutes` : seconds >= 60 ? "a minute" : `${seconds} seconds`;
}

/** "a load and the stats rebuild" */
function andList(words: string[]): string {
  return words.length < 2 ? words.join("") : `${words.slice(0, -1).join(", ")} and ${words[words.length - 1]}`;
}

/** Whether the server still answers, asked without flipping the offline banner. */
async function serverAnswers(): Promise<boolean> {
  try {
    // a stalled connection must not hold the card past its deadline
    return (await fetch("/api/server", { cache: "no-store", signal: AbortSignal.timeout(2000) })).ok;
  } catch {
    return false;
  }
}

function ServerCard() {
  const { down } = useLink();
  const { data, error } = useApi<ServerInfo>("/api/server");
  const [now, setNow] = useState(() => Date.now());
  const [acting, setActing] = useState<"restart" | "stop" | null>(null);
  const [confirmStop, setConfirmStop] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  // the work a stop or restart waits for before the process ends
  const [waiting, setWaiting] = useState<ServerEnding | null>(null);

  useEffect(() => {
    if (!data) return;
    const t = window.setInterval(() => setNow(Date.now()), 30_000);
    return () => window.clearInterval(t);
  }, [data]);

  async function restart() {
    if (!data) return;
    // a re-exec keeps the pid, so the new process is known by its later `started`
    const started = data.started;
    setActing("restart");
    setProblem(null);
    try {
      const ending = await postJSON<ServerEnding>("/api/server/restart");
      setWaiting(ending);
      // ask with a plain fetch: the gap in the middle is expected, and must
      // not flash the offline banner on the way. The old process may first
      // wait up to `timeout` seconds for work under way.
      const tries = 60 + 2 * (ending.timeout ?? 0);
      for (let i = 0; i < tries; i++) {
        await new Promise((r) => setTimeout(r, 500));
        try {
          const r = await fetch("/api/server", { cache: "no-store" });
          if (r.ok && (await r.json()).started !== started) break;
        } catch { /* still coming up */ }
      }
      await checkServer();
    } catch (e) {
      setProblem((e as Error).message);
    } finally {
      setActing(null);
      setWaiting(null);
    }
  }

  async function stop() {
    setActing("stop");
    setConfirmStop(false);
    setProblem(null);
    try {
      const ending = await postJSON<ServerEnding>("/api/server/stop");
      setWaiting(ending);
      // give the exit a moment, then ask until it misses: the miss is what
      // flips the chip and the banner to offline. Work under way holds the
      // exit back by up to `timeout` seconds.
      const deadline = Date.now() + ((ending.timeout ?? 0) + 5) * 1000;
      const ask = async () => {
        if (!(await serverAnswers()) || Date.now() > deadline) void checkServer();
        else window.setTimeout(() => void ask(), 1000);
      };
      window.setTimeout(() => void ask(), 800);
    } catch (e) {
      setProblem((e as Error).message);
      setActing(null);
    }
  }

  if (down) {
    return (
      <Card title="Server">
        <p className="m-0 text-[13px] text-ink-2">Stopped.</p>
        <p className="m-0 mt-1 text-[13px] text-ink-3">
          Start it again with <span className="font-medium">make serve</span> or <span className="font-medium">make dev</span>;
          in the Kopifund app the window's Retry starts it. This page comes back on its own.
        </p>
      </Card>
    );
  }
  if (error) return <Card title="Server"><p className="m-0 text-[13px] text-warn">{error}</p></Card>;
  if (!data) return null;
  return (
    <Card title="Server" actions={
      confirmStop ? (
        <span className="flex items-center gap-2 text-[13px]">
          Stop the server? Every page goes offline until it starts again.
          <Button size="sm" variant="primary" disabled={acting !== null} onClick={() => void stop()}>Stop</Button>
          <Button size="sm" disabled={acting !== null} onClick={() => setConfirmStop(false)}>Cancel</Button>
        </span>
      ) : (
        <span className="flex gap-2">
          <Button size="sm" disabled={acting !== null} onClick={() => void restart()}>
            {acting === "restart" ? "Restarting…" : "Restart"}
          </Button>
          <Button size="sm" disabled={acting !== null} onClick={() => setConfirmStop(true)}>
            {acting === "stop" ? "Stopping…" : "Stop"}
          </Button>
        </span>
      )
    }>
      <p className="m-0 text-[13px]">
        Running for <span className="num">{runningFor(data.started, now)}</span>
      </p>
      <p className="m-0 mt-1 text-[13px] text-ink-3">
        Started by {SERVER_MODE[data.mode]} on 127.0.0.1:{data.port} · pid {data.pid}
      </p>
      {acting && waiting && waiting.waiting_for.length > 0 && (
        <p role="status" className="m-0 mt-1 text-[13px] text-ink-2">
          Waiting for {andList(waiting.waiting_for)} to finish first ({waitAtMost(waiting.timeout)} at most).
          Changes made meanwhile are refused until the server is back.
        </p>
      )}
      {problem && <p role="alert" className="m-0 mt-1 text-[13px] text-danger">{problem}</p>}
    </Card>
  );
}

// one icon for each kind of file the export writes
function FileIcon({ name }: { name: string }) {
  const ext = name.split(".").pop()?.toLowerCase();
  const Icon = ext === "csv" ? FileSpreadsheet : ext === "json" ? FileJson : FileText;
  return <Icon size={16} aria-hidden className="shrink-0 text-ink-3" />;
}

function ExportCard() {
  const [version, setVersion] = useState(0);
  const list = useApi<Exports>(`/api/exports?v=${version}`);
  const [done, setDone] = useState<ExportDone | null>(null);
  const { running: busy, error, run: act } = useAction();
  const run = () => act("export", async () => {
    setDone(await postJSON<ExportDone>("/api/export"));
    setVersion((v) => v + 1);
  });

  const latest = list.data?.exports[0];
  return (
    <Card title="Export" actions={
      <Button variant="primary" onClick={run} disabled={busy}>
        {busy ? "Exporting…" : "Export everything"}
      </Button>}>
      {done && <p className="m-0 mt-2 text-[13px] text-good" role="status">Exported {done.transactions.toLocaleString("en-SG")} transactions to {done.folder}.</p>}
      {error && <p className="m-0 mt-2 text-[13px] text-warn" role="alert">{error}</p>}
      {latest && (
        <ul aria-label={`Files exported ${latest.stamp}`} className="m-0 mt-3 list-none rounded-lg border border-line p-0 text-[13px]">
          <CollapsibleRows rows={latest.files} limit={COLLAPSED_ROWS.thin} keyOf={(f) => f} render={(f) => (
            <li className="flex items-center gap-2 border-t border-line px-3 py-1.5 first:border-t-0">
              <FileIcon name={f} />
              <span className="min-w-0 flex-1 truncate">{f}</span>
              <a href={`/api/exports/${latest.stamp}/${f}`} download aria-label={`Download ${f}`}
                className={buttonClass("secondary", "sm")}>
                <Download size={13} aria-hidden /> Download
              </a>
            </li>
          )} />
        </ul>
      )}
    </Card>
  );
}

// G15: the scam checks' thresholds as one compact form, and the rows marked
// fine on Transactions, each of which can be flagged again
type ScamKey = keyof ScamSettings;
type ScamDraft = Record<ScamKey, string>;
// the thresholds in cents, typed in dollars (read and shown by money.ts, G17); the rest are whole counts of days or payments
const MONEY: ScamKey[] = ["big_transfer", "abroad_min", "test_max", "twice_min"];
const isMoney = (k: ScamKey) => MONEY.includes(k);
const draftOf = (v: ScamSettings): ScamDraft =>
  Object.fromEntries((Object.keys(v) as ScamKey[]).map((k) => [k, isMoney(k) ? centsText(v[k]) : String(v[k])])) as ScamDraft;
const WHOLE = /^\d+$/;
const DOLLARS = /^\d+(\.\d{1,2})?$/;

export function ScamChecksCard() {
  const { data, error } = useApi<ScamChecks>("/api/scam-checks");
  // G18: only the boxes typed in, so a save from another tab still shows in every box left alone
  const [draft, setDraft] = useState<Partial<ScamDraft> | null>(null);
  const [saving, setSaving] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  // a save on its way: what is typed meanwhile is an edit, even back to the old saved value
  const inFlight = useRef(false);
  const saved = data ? draftOf(data.settings) : null;
  const keys = saved ? Object.keys(saved) as ScamKey[] : [];
  const d = { ...saved, ...draft } as ScamDraft;
  const dirty = !!saved && keys.some((k) => d[k] !== saved[k]);
  // G18: leaving the page with a threshold typed and not saved asks first
  useUnsavedGuard(dirty);
  if (error) return <Card title="Scam checks"><p className="m-0 text-[13px] text-warn">{error}</p></Card>;
  if (!data || !saved) return null;
  const bad = (k: ScamKey) => !(isMoney(k) ? DOLLARS : WHOLE).test(d[k]);
  const set = (k: ScamKey) => (v: string) => {
    // a box typed back to its saved value is no longer an edit, unless that value is about to change
    const rest = { ...draft };
    delete rest[k];
    setDraft(v === saved[k] && !inFlight.current ? rest : { ...rest, [k]: v });
    setProblem(null);
  };

  async function save() {
    if (keys.some(bad)) {
      setProblem("Each box takes a whole number; an amount can have cents.");
      return;
    }
    setSaving(true);
    inFlight.current = true;
    const sent = { ...d };
    try {
      await postJSON("/api/scam-checks/settings", JSON.stringify(Object.fromEntries(
        keys.map((k) => [k, isMoney(k) ? parseCents(sent[k]) : Number(sent[k])]))));
      // G18: the boxes the save covered are saved; one typed in while it was on its way stays an edit
      setDraft((now) => {
        const left = Object.fromEntries(Object.entries(now ?? {}).filter(([k, v]) => v !== sent[k as ScamKey]));
        return Object.keys(left).length ? left : null;
      });
    } catch (e) {
      setProblem((e as Error).message);
    } finally {
      inFlight.current = false;
      setSaving(false);
    }
  }

  const field = (k: ScamKey, label: string, opts: { before?: string; after?: string; group?: boolean }) => (
    // the box at the foot of its cell, so a label that wraps never pushes it out of line
    <label className="flex min-w-0 flex-col justify-end gap-1 text-[12px] text-ink-2">
      {label}
      <MoneyInput label={label} value={d[k]} onChange={set(k)} invalid={bad(k)} {...opts} />
    </label>
  );
  return (
    <Card title="Scam checks" actions={<SaveBar dirty={dirty} saving={saving} onSave={() => void save()} onDiscard={() => { setDraft(null); setProblem(null); }} />}>
      <form onSubmit={(e) => { e.preventDefault(); void save(); }} aria-label="Scam check thresholds"
        className="mt-2 flex flex-col gap-3">
        <section aria-label="Transfers" className="flex flex-col gap-1.5">
          <h3 className="m-0 text-[13px] font-semibold">Transfers</h3>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            {field("big_transfer", "Big transfer", { before: "S$" })}
            {field("new_payee_days", "Payee counts as new for", { after: "days", group: false })}
            {field("repeat_count", "Repeated transfers", { after: "or more", group: false })}
            {field("repeat_days", "Repeated within", { after: "days", group: false })}
          </div>
        </section>
        <section aria-label="Card and shop payments" className="flex flex-col gap-1.5">
          <h3 className="m-0 text-[13px] font-semibold">Card and shop payments</h3>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
            {field("abroad_min", "Abroad, no trip, from", { before: "S$" })}
            {field("test_max", "Test charge up to", { before: "S$" })}
            {field("test_days", "Then larger within", { after: "days", group: false })}
            {field("twice_min", "Charged twice from", { before: "S$" })}
            {field("new_sub_days", "New subscription for", { after: "days", group: false })}
          </div>
        </section>
      </form>
      {problem && <p role="alert" className="m-0 mt-2 text-[13px] text-danger">{problem}</p>}
      {data.marked_fine.length > 0 && <MarkedFine rows={data.marked_fine} />}
    </Card>
  );
}

function MarkedFine({ rows }: { rows: ScamChecks["marked_fine"] }) {
  const { busy, failed, run } = useAction();
  const again = (key: string) => run(key, () => postJSON("/api/scam-checks/fine", JSON.stringify({ key, fine: false })));
  return (
    <section aria-label="Marked fine" className="mt-3">
      <h3 className="m-0 mb-1.5 text-[13px] font-semibold">Marked fine</h3>
      <ul className="m-0 list-none rounded-lg border border-line p-0 text-[13px]">
        <CollapsibleRows rows={rows} limit={COLLAPSED_ROWS.thin} keyOf={(r) => r.key} render={(r) => (
          <li className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-line px-3 py-1.5 first:border-t-0">
            <span className="num shrink-0 text-ink-2">{formatDay(r.date)}</span>
            <span className="min-w-0 flex-1 truncate">{r.payee}</span>
            <span className="num shrink-0">{formatSgd(r.amount, { decimals: true })}</span>
            <Button size="sm" disabled={busy === r.key} onClick={() => void again(r.key)}
              aria-label={`Flag again: ${r.payee} on ${formatDay(r.date)}`}>Flag again</Button>
            {failed?.key === r.key && <span role="alert" className="basis-full text-[12px] text-danger">{failed.message}</span>}
          </li>
        )} />
      </ul>
    </section>
  );
}
