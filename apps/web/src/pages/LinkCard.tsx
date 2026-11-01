// G25: the "Link a phone" card on Settings. "New pairing code" mints the
// one-time token behind the QR the iPhone scans (POST /api/link/pair-token);
// below it the registry's rows list every paired phone and revoke a lost
// one's bearer (SPEC.md, "Phone link (G25)"). The payload is also copyable
// for the times a camera cannot take it. Last come the phone rows: spends a
// paired phone posted before their statement landed, waiting, matched or
// dismissed here.
import { useEffect, useRef, useState } from "react";
import QRCode from "react-qr-code";
import { Copy, Smartphone } from "lucide-react";
import { postJSON, useApi } from "../api";
import { onLinkClick } from "../router";
import { formatDay } from "../dates";
import { formatAmount } from "../money";
import { Button } from "../components/Button";
import { Card } from "../components/Card";
import type { LinkDevice, LinkDevices, LinkPending, PairToken, PendingRow } from "../types.gen";

export function LinkCard() {
  const [version, setVersion] = useState(0);
  const list = useApi<LinkDevices>(`/api/link/devices?v=${version}`);
  const pending = useApi<LinkPending>(`/api/link/pending?v=${version}`);
  const [pair, setPair] = useState<PairToken | null>(null);
  const [expired, setExpired] = useState(false);
  const [busy, setBusy] = useState(false);
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

  async function mint() {
    setBusy(true);
    setProblem(null);
    try {
      const fresh = await postJSON<PairToken>("/api/link/pair-token", JSON.stringify({}));
      setPair(fresh);
      setCopied(false);
      setExpired(false);
      // the hub spends the token `expires_in` seconds after minting; when the
      // code is past it the QR cannot work, so it leaves the screen
      if (expireTimer.current) clearTimeout(expireTimer.current);
      expireTimer.current = setTimeout(() => setExpired(true), fresh.expires_in * 1000);
    } catch (e) {
      setProblem((e as Error).message);
    } finally {
      setBusy(false);
    }
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
      <Button variant="primary" onClick={() => void mint()} disabled={busy}>
        {busy ? "Minting…" : pair ? "New pairing code" : "Pair a phone"}
      </Button>}>
      <div className="flex flex-col gap-3">
        {data && !data.url && (
          <p className="m-0 text-[13px] text-ink-3">
            Tailscale cannot name this Mac, so the code carries no address yet: <span className="num">make link</span> says what is missing.
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
        {rows && rows.length > 0 && (
          <ul aria-label="Phone rows" className="m-0 list-none rounded-lg border border-line p-0 text-[13px]">
            {rows.map((r) => (
              <PendingItem key={r.import_id} row={r}
                onError={setProblem} onDone={() => setVersion((v) => v + 1)} />
            ))}
          </ul>
        )}
        {list.error && <p role="alert" className="m-0 text-[13px] text-warn">{list.error}</p>}
        {pending.error && <p role="alert" className="m-0 text-[13px] text-warn">{pending.error}</p>}
        {problem && <p role="alert" className="m-0 text-[13px] text-danger">{problem}</p>}
      </div>
    </Card>
  );
}

function DeviceRow({ device, onRevoked, onError }: {
  device: LinkDevice; onRevoked: () => void; onError: (m: string | null) => void;
}) {
  const [armed, setArmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const disarm = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => {
    if (disarm.current) clearTimeout(disarm.current);
  }, []);

  async function revoke() {
    // the first press asks; a second within a few seconds revokes
    if (!armed) {
      setArmed(true);
      if (disarm.current) clearTimeout(disarm.current);
      disarm.current = setTimeout(() => setArmed(false), 4000);
      return;
    }
    if (disarm.current) clearTimeout(disarm.current);
    setArmed(false);
    setBusy(true);
    onError(null);
    try {
      await postJSON("/api/link/devices/revoke", JSON.stringify({ id: device.id }));
      onRevoked();
    } catch (e) {
      onError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-line px-3 py-1.5 first:border-t-0">
      <Smartphone size={14} aria-hidden className="shrink-0 text-ink-3" />
      <span className="min-w-0 flex-1 truncate">{device.name}</span>
      <span className="num shrink-0 text-ink-3">paired {formatDay(device.paired_at.slice(0, 10))}</span>
      {device.revoked
        ? <span className="shrink-0 text-[12px] text-ink-3">revoked</span>
        : <Button size="sm" disabled={busy} onBlur={() => setArmed(false)} onClick={() => void revoke()}
            aria-label={armed ? `Press again to revoke ${device.name}` : `Revoke ${device.name}`}>
            {armed ? "Press again to revoke" : "Revoke"}
          </Button>}
    </li>
  );
}

function PendingItem({ row, onDone, onError }: {
  row: PendingRow; onDone: () => void; onError: (m: string | null) => void;
}) {
  const [busy, setBusy] = useState(false);

  async function act(url: string) {
    setBusy(true);
    onError(null);
    try {
      await postJSON(url, JSON.stringify({ import_id: row.import_id }));
      onDone();
    } catch (e) {
      onError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-line px-3 py-1.5 first:border-t-0">
      <span className="num shrink-0 text-ink-3">{formatDay(row.date)}</span>
      <span className="min-w-0 flex-1 truncate">{row.payee}</span>
      <span className="num shrink-0">{formatAmount(row.amount_minor, row.currency)}</span>
      <span className="shrink-0 text-[12px] text-ink-3">
        {row.state === "matched" && row.matched_txn_key
          ? <a className="text-ink-3 underline underline-offset-2"
              href={`/transactions?${new URLSearchParams({ key: row.matched_txn_key })}`}
              onClick={onLinkClick(`/transactions?${new URLSearchParams({ key: row.matched_txn_key })}`)}
              aria-label={`See the record row ${row.payee} matched`}>matched</a>
          : row.state}
        {row.device ? ` · ${row.device}` : ""}
      </span>
      {row.state === "waiting" &&
        <Button size="sm" disabled={busy} onClick={() => void act("/api/link/pending/dismiss")}
          aria-label={`Dismiss ${row.payee}`}>Dismiss</Button>}
      {row.state === "matched" &&
        <Button size="sm" disabled={busy} onClick={() => void act("/api/link/pending/restore")}
          aria-label={`Not it: put ${row.payee} back to waiting`}>Not it</Button>}
      {row.state === "dismissed" &&
        <Button size="sm" disabled={busy} onClick={() => void act("/api/link/pending/restore")}
          aria-label={`Put ${row.payee} back to waiting`}>Put back</Button>}
    </li>
  );
}
