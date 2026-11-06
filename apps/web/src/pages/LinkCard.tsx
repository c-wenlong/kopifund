// G25: the "Link a phone" card on Settings. "New pairing code" mints the
// one-time token behind the QR the iPhone scans (POST /api/link/pair-token);
// below it the registry's rows list every paired phone and revoke a lost
// one's bearer (SPEC.md, "Phone link (G25)"). The payload is also copyable
// for the times a camera cannot take it. Last come the phone rows still
// waiting: spends a paired phone posted before their statement landed, each
// dismissable here, several at once. Every row the phone ever posted, with
// what became of it (matched, dismissed to review, expired after
// `expire_days`) and when, is the history sheet (PhoneRowHistory), which
// the address's `phone_rows` opens on one state, so Home's Needs your eye
// line can open it too. No code is minted while Tailscale cannot name the
// Mac: it would carry no address, and the phone refuses it.
import { useEffect, useRef, useState } from "react";
import QRCode from "react-qr-code";
import { Copy, Smartphone } from "lucide-react";
import { postJSON, useApi } from "../api";
import { onLinkClick } from "../router";
import { formatDay, formatStampDay } from "../dates";
import { formatAmount } from "../money";
import { Button } from "../components/Button";
import { Card } from "../components/Card";
import { Segmented } from "../components/Segmented";
import { Sheet } from "../components/Sheet";
import { StatusChip, type StatusTone } from "../components/StatusChip";
import { useAction } from "../components/useAction";
import { useArmed } from "../components/useArmed";
import { useUrlParams } from "../useUrlParams";
import { plural } from "../words";
import type { LinkDevice, LinkDevices, LinkPending, PairToken, PendingRow, PendingState } from "../types.gen";

// G25: the address that opens the history sheet on one state ("all" for every row)
export const HISTORY_PARAM = "phone_rows";
type HistoryFilter = "all" | PendingState;
const FILTERS: { value: HistoryFilter; text: string }[] = [
  { value: "all", text: "All" }, { value: "waiting", text: "Waiting" }, { value: "matched", text: "Matched" },
  { value: "dismissed", text: "To review" }, { value: "expired", text: "Expired" },
];
const asFilter = (v: string | null): HistoryFilter | null =>
  FILTERS.find((f) => f.value === v)?.value ?? null;

// what each state reads as on a row; a dismissed row is the owner's to look at again
const STATE: Record<PendingState, { text: string; tone: StatusTone }> = {
  waiting: { text: "Waiting", tone: "neutral" },
  matched: { text: "Matched", tone: "good" },
  dismissed: { text: "Dismissed, to review", tone: "warn" },
  expired: { text: "Expired", tone: "empty" },
};

export function LinkCard() {
  const [version, setVersion] = useState(0);
  const list = useApi<LinkDevices>(`/api/link/devices?v=${version}`);
  const pending = useApi<LinkPending>(`/api/link/pending?v=${version}`);
  const [pair, setPair] = useState<PairToken | null>(null);
  const [expired, setExpired] = useState(false);
  const { running: busy, run } = useAction();
  const [copied, setCopied] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  // a fresh code replaces the one shown: its ten minutes run from minting,
  // so the copy under the QR counts it down no further than seconds
  const copyTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const expireTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => {
    if (copyTimer.current) clearTimeout(copyTimer.current);
    if (expireTimer.current) clearTimeout(expireTimer.current);
  }, []);
  // while a live code is on screen the list polls: a phone that redeems it
  // shows up without the owner reloading Settings
  useEffect(() => {
    if (!pair || expired) return;
    const poll = setInterval(() => setVersion((v) => v + 1), 4000);
    return () => clearInterval(poll);
  }, [pair, expired]);
  // an answer without the devices (an older server, a test's stand-in) is no answer to draw
  const data = Array.isArray(list.data?.devices) ? list.data : null;
  const rows = Array.isArray(pending.data?.rows) ? pending.data.rows : null;
  const waitingRows = rows ? rows.filter((r) => r.state === "waiting") : [];
  const waiting = waitingRows.length;
  const noAddress = data !== null && !data.url;
  const [params, patch] = useUrlParams();
  const filter = asFilter(params.get(HISTORY_PARAM));

  async function mint() {
    setProblem(null);
    await run("mint", async () => {
      const fresh = await postJSON<PairToken>("/api/link/pair-token", JSON.stringify({}));
      setPair(fresh);
      setCopied(false);
      setExpired(false);
      // the hub spends the token `expires_in` seconds after minting; when the
      // code is past it the QR cannot work, so it leaves the screen
      if (expireTimer.current) clearTimeout(expireTimer.current);
      expireTimer.current = setTimeout(() => setExpired(true), fresh.expires_in * 1000);
    }, { onError: setProblem });
  }

  async function copy(payload: string) {
    try {
      await navigator.clipboard.writeText(payload);
      setCopied(true);
      if (copyTimer.current) clearTimeout(copyTimer.current);
      copyTimer.current = setTimeout(() => setCopied(false), 2000);
    } catch {
      setProblem("The code could not be copied");
    }
  }

  return (
    <Card title="Link a phone" actions={
      <Button variant="primary" onClick={() => void mint()} disabled={busy || noAddress}>
        {busy ? "Minting…" : pair ? "New pairing code" : "Pair a phone"}
      </Button>}>
      <div className="flex flex-col gap-3">
        {noAddress && (
          <p className="m-0 text-[13px] text-ink-3">
            Tailscale cannot name this Mac, so there is no address to pair with yet: <span className="num">make link</span> says what is missing.
          </p>
        )}
        {pair && expired && (
          <p className="m-0 text-[13px] text-ink-2" role="status">
            That pairing code expired. Make a new one when the phone is ready to scan it.
          </p>
        )}
        {pair && !expired && (
          <section aria-label="Pairing code" className="flex flex-col items-start gap-2">
            <div className="rounded-lg bg-white p-3">
              <QRCode value={pair.payload} size={168} />
            </div>
            <p className="m-0 text-[13px] text-ink-2">
              Scan it in the app, or paste the code there. It pairs one phone, once,
              within {Math.floor(pair.expires_in / 60)} minutes.
            </p>
            <div className="flex w-full min-w-0 items-center gap-2">
              <code className="min-w-0 flex-1 truncate rounded-md border border-line bg-panel px-2 py-1.5 text-[12px] text-ink-2">{pair.payload}</code>
              <Button size="sm" onClick={() => void copy(pair.payload)} aria-label="Copy the pairing code">
                <Copy size={13} aria-hidden /> {copied ? "Copied" : "Copy"}
              </Button>
            </div>
          </section>
        )}
        {data && data.devices.length > 0 && (
          <ul aria-label="Paired phones" className="m-0 list-none rounded-lg border border-line p-0 text-[13px]">
            {data.devices.map((d) => (
              <DeviceRow key={d.id} device={d}
                onError={setProblem} onRevoked={() => setVersion((v) => v + 1)} />
            ))}
          </ul>
        )}
        {waiting > 1 && (
          <DismissAll count={waiting} onError={setProblem} onDone={() => setVersion((v) => v + 1)} />
        )}
        {waiting > 0 && (
          <ul aria-label="Waiting phone rows" className="m-0 list-none rounded-lg border border-line p-0 text-[13px]">
            {waitingRows.map((r) => (
              <PendingItem key={r.import_id} row={r}
                onError={setProblem} onDone={() => setVersion((v) => v + 1)} />
            ))}
          </ul>
        )}
        {rows && rows.length > 0 && (
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[13px] text-ink-2">
            <span className="num min-w-0 grow">
              {waiting === 0 ? "No phone rows are waiting." : `${plural(rows.length, "phone row")} in all.`}
            </span>
            <Button size="sm" onClick={() => patch({ [HISTORY_PARAM]: "all" })}>Phone row history</Button>
          </div>
        )}
        {list.error && <p role="alert" className="m-0 text-[13px] text-warn">{list.error}</p>}
        {pending.error && <p role="alert" className="m-0 text-[13px] text-warn">{pending.error}</p>}
        {problem && <p role="alert" className="m-0 text-[13px] text-danger">{problem}</p>}
      </div>
      <PhoneRowHistory filter={filter} rows={rows} error={pending.error}
        onFilter={(f) => patch({ [HISTORY_PARAM]: f })} onClose={() => patch({ [HISTORY_PARAM]: null })}
        onDone={() => setVersion((v) => v + 1)} />
    </Card>
  );
}

// G25: every phone row ever posted, newest first, filtered by state; each
// row says when it last changed, and keeps the call that moves it on
function PhoneRowHistory({ filter, rows, error, onFilter, onClose, onDone }: {
  filter: HistoryFilter | null; rows: PendingRow[] | null; error?: string | null; onFilter: (f: HistoryFilter) => void;
  onClose: () => void; onDone: () => void;
}) {
  const [problem, setProblem] = useState<string | null>(null);
  // null until the rows arrive: an address that opens the sheet comes before them
  const shown = (rows ?? []).filter((r) => filter === "all" || r.state === filter);
  const close = () => { setProblem(null); onClose(); };
  return (
    <Sheet open={filter !== null} title="Phone row history" onClose={close}>
      <div className="flex flex-col gap-3 pb-1">
        <div className="-mx-4 overflow-x-auto px-4">
          <Segmented label="Show" value={filter ?? "all"} options={FILTERS} onChange={onFilter} />
        </div>
        {rows && (shown.length > 0
          ? <ul aria-label="Phone row history" className="m-0 list-none rounded-lg border border-line p-0 text-[13px]">
              {shown.map((r) => <PendingItem key={r.import_id} row={r} onError={setProblem} onDone={onDone} />)}
            </ul>
          : <p className="m-0 text-[13px] text-ink-3">
              {rows.length === 0 ? "The phone has sent no rows yet." : "No phone rows in this state."}
            </p>)}
        {/* the card behind is inert while the sheet is open, so a failed read is said here too */}
        {error && <p role="alert" className="m-0 text-[13px] text-warn">{error}</p>}
        {problem && <p role="alert" className="m-0 text-[13px] text-danger">{problem}</p>}
      </div>
    </Sheet>
  );
}

function DeviceRow({ device, onRevoked, onError }: {
  device: LinkDevice; onRevoked: () => void; onError: (m: string | null) => void;
}) {
  const { running: busy, run } = useAction();
  const confirm = useArmed();

  // a revoked phone cannot be paired back: the first press asks (useArmed)
  const revoke = () => confirm.press(() => {
    onError(null);
    return run("write", async () => {
      await postJSON("/api/link/devices/revoke", JSON.stringify({ id: device.id }));
      onRevoked();
    }, { onError });
  });

  return (
    <li className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-line px-3 py-1.5 first:border-t-0">
      <Smartphone size={14} aria-hidden className="shrink-0 text-ink-3" />
      <span className="min-w-0 flex-1 truncate">{device.name}</span>
      <span className="num shrink-0 text-ink-3">paired {formatStampDay(device.paired_at)}</span>
      {device.revoked
        ? <span className="shrink-0 text-[12px] text-ink-3">revoked</span>
        : <Button size="sm" disabled={busy} onBlur={confirm.disarm} onClick={revoke}
            aria-label={confirm.armed ? `Press again to revoke ${device.name}` : `Revoke ${device.name}`}>
            {confirm.armed ? "Press again to revoke" : "Revoke"}
          </Button>}
    </li>
  );
}

// every waiting row dismissed at once; the first press asks, as Revoke does,
// and each row's Put back undoes it
function DismissAll({ count, onDone, onError }: {
  count: number; onDone: () => void; onError: (m: string | null) => void;
}) {
  const { running: busy, run } = useAction();
  const confirm = useArmed();

  const go = () => confirm.press(() => {
    onError(null);
    return run("write", async () => {
      await postJSON("/api/link/pending/dismiss-all", JSON.stringify({}));
      onDone();
    }, { onError });
  });

  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[13px] text-ink-2">
      <span className="min-w-0 grow basis-56">
        {count} phone rows are waiting for a statement.
      </span>
      <Button size="sm" disabled={busy} onBlur={confirm.disarm} onClick={go}>
        {confirm.armed ? `Press again to dismiss ${count}` : "Dismiss all waiting"}
      </Button>
    </div>
  );
}

function PendingItem({ row, onDone, onError }: {
  row: PendingRow; onDone: () => void; onError: (m: string | null) => void;
}) {
  const { running: busy, run } = useAction();
  const state = STATE[row.state] ?? { text: row.state, tone: "neutral" as StatusTone };
  // a matched row opens its record row, and only that row (a transfer too)
  const to = row.state === "matched" && row.matched_txn_key
    ? `/transactions?${new URLSearchParams({ key: row.matched_txn_key, hide_transfers: "0" })}` : null;
  // when the state began; a waiting row also says when its wait ends
  const since = formatStampDay(row.state_changed_at);
  const when = row.state === "waiting" && row.expires_on ? `${since}, expires ${formatDay(row.expires_on)}` : since;

  async function act(url: string) {
    onError(null);
    await run("write", async () => {
      await postJSON(url, JSON.stringify({ import_id: row.import_id }));
      onDone();
    }, { onError });
  }

  return (
    <li className="flex items-center gap-3 border-t border-line px-3 py-2 first:border-t-0">
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <div className="flex min-w-0 items-baseline gap-3">
          <span className="min-w-0 flex-1 truncate">{row.payee || "No payee"}</span>
          <span className="num shrink-0">{formatAmount(row.amount_minor, row.currency)}</span>
        </div>
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[12px] text-ink-3">
          <span className="num">{formatDay(row.date)}</span>
          <StatusChip tone={state.tone}>{state.text}</StatusChip>
          {when && <span className="num">{when}</span>}
          {to && <a className="text-ink-3 underline underline-offset-2" href={to} onClick={onLinkClick(to)}
            aria-label={`See the record row ${row.payee} matched`}>See the row</a>}
          {row.device && <span>{row.device}</span>}
        </div>
      </div>
      {/* one width for every row's button, so the amounts line up down the list */}
      <div className="flex w-[5.5rem] shrink-0 justify-end">
        {row.state === "waiting" &&
          <Button size="sm" disabled={busy} onClick={() => void act("/api/link/pending/dismiss")}
            aria-label={`Dismiss ${row.payee}`}>Dismiss</Button>}
        {row.state === "matched" &&
          <Button size="sm" disabled={busy} onClick={() => void act("/api/link/pending/restore")}
            aria-label={`Not it: put ${row.payee} back to waiting`}>Not it</Button>}
        {(row.state === "dismissed" || row.state === "expired") &&
          <Button size="sm" disabled={busy} onClick={() => void act("/api/link/pending/restore")}
            aria-label={`Put ${row.payee} back to waiting`}>Put back</Button>}
      </div>
    </li>
  );
}
