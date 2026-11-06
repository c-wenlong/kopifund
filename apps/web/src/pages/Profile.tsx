import { useState } from "react";
import { accounts as folderAccounts, flag, type Bank } from "../accounts";
import { postJSON, useApi, type About, type Profile as ProfileT, type ProfileAccount } from "../api";
import { formatDay, formatMonth, formatRange, isoDay, isoMonth, shiftMonth } from "../dates";
import { BankMark } from "../components/BankMark";
import { Button, buttonClass } from "../components/Button";
import { Card } from "../components/Card";
import { CollapsibleRows, COLLAPSED_ROWS } from "../components/CollapsibleRows";
import { DateField } from "../components/DateField";
import { StatusChip, type StatusTone } from "../components/StatusChip";
import { Loaded } from "../components/States";
import { onLinkClick, useUnsavedGuard } from "../router";

export function Profile() {
  const state = useApi<ProfileT>("/api/profile");
  return (
    <Loaded state={state} what="Loading your profile" missing="No profile">{(data) => (
      <div className="flex flex-col gap-3">
        <AboutYou about={data.about} />
        <Accounts list={data.accounts} folders={data.folders} />
        <p className="m-0 px-1 text-[12px] text-ink-3">
          {data.data_folder} · {data.transactions.toLocaleString("en-SG")} transactions · Notion mirror {data.notion_enabled ? "on" : "off"}
        </p>
      </div>
    )}</Loaded>
  );
}

const waiting = (a: ProfileAccount) => a.statements === 0 && a.transactions === 0;

/**
 * Whether an account's statements reach the last month that has one to read:
 * the month before this one (CPF prints a year at a time, so the year before
 * this one). The words name what is missing, from the month after its last
 * statement to that month, or say there is none yet.
 */
export function freshness(a: { bank: Bank; last: string | null }, now: Date = new Date()): { ok: boolean; text: string } {
  if (!a.last) return { ok: false, text: "No statements yet" };
  if (a.bank === "cpf") {
    const want = now.getFullYear() - 1;
    const have = Number(a.last.slice(0, 4));
    return have >= want ? { ok: true, text: "Up to date" } : { ok: false, text: `Missing ${have + 1 === want ? want : `${have + 1} to ${want}`}` };
  }
  const want = shiftMonth(isoMonth(now), -1)!;
  const have = a.last.slice(0, 7);
  if (have >= want) return { ok: true, text: "Up to date" };
  const from = shiftMonth(have, 1)!;
  return { ok: false, text: `Missing ${from === want ? formatMonth(from) : formatRange(from, want)}` };
}

// One line of the Accounts list: an account the record holds, or a statement
// folder no account has claimed yet (CPF, a card folder with nothing loaded).
type Line = {
  key: string; bank: Bank; name: string; number: string;
  last: string | null; first: string | null; statements: number; rows: number;
  folder: string | null; pdfs: number | null; paths?: string[]; wallets?: string;
  chip: { ok: boolean | null; text: string };
};

function lines(list: ProfileAccount[], folders: ProfileT["folders"]): Line[] {
  const own: Line[] = list.map((a, i) => ({
    key: `${a.name}-${a.number}-${i}`, bank: a.bank, name: a.name, number: a.number, first: a.first, last: a.last,
    statements: a.statements, rows: a.transactions, folder: a.folder, pdfs: a.pdfs,
    chip: waiting(a) ? { ok: false, text: "No statements yet" } : freshness(a),
  }));
  const claimed = new Set(list.map((a) => a.folder).filter(Boolean));
  const count = new Map(folders.map((f) => [f.path, f.statements]));
  const rest: Line[] = folderAccounts(folders).filter((f) => !claimed.has(f.key)).map((f) => {
    const paths = f.wallets.length ? f.wallets.map((w) => w.path) : [f.path];
    const pdfs = paths.reduce((n, p) => n + (count.get(p) ?? 0), 0);
    return {
      key: f.key, bank: f.bank, name: f.name, number: "", first: null, last: null, statements: 0, rows: 0,
      folder: f.key, pdfs, paths, wallets: f.wallets.map((w) => `${w.currency} ${flag(w.currency)}`).join(" · "),
      chip: pdfs ? { ok: null, text: "Not loaded yet" } : { ok: false, text: "No statements yet" },
    };
  });
  // accounts with statements first, as the record holds them, then those still waiting
  return [...own.filter((l) => l.chip.text !== "No statements yet"), ...own.filter((l) => l.chip.text === "No statements yet"), ...rest];
}

// G13: an account by its bank's mark, a name with no number in it and the last
// four digits of its number; the full number never reaches the page
function AccountName({ l }: { l: Line }) {
  const last4 = l.number.replace(/\D/g, "");
  return (
    <span className="flex min-w-0 items-center gap-2">
      <BankMark bank={l.bank} />
      <span className="truncate">{l.name}</span>
      {l.number && <span className="num shrink-0 text-ink-3" title={`Account number ending ${last4}`}>{l.number}</span>}
      {l.wallets && <span className="truncate text-ink-3">{l.wallets}</span>}
    </span>
  );
}

// an account's freshness: up to date, behind, or nothing to judge yet
const CHIP_TONE = { true: "good", false: "warn", null: "neutral" } as const satisfies Record<string, StatusTone>;

function Accounts({ list, folders }: { list: ProfileAccount[]; folders: ProfileT["folders"] }) {
  const rows = lines(list, folders);
  return (
    <Card title="Accounts">
      {rows.length === 0 && <p className="m-0 text-[14px] text-ink-2">Accounts appear once their first statement is loaded.</p>}
      {rows.length > 0 && (
        <ul className="m-0 list-none p-0 text-[13px]">
          <CollapsibleRows rows={rows} limit={COLLAPSED_ROWS.thin} keyOf={(l) => l.key} render={(l) => {
            const details = [
              l.first ? formatRange(l.first, l.last ?? l.first) : "",
              l.statements ? `${l.statements.toLocaleString("en-SG")} ${l.statements === 1 ? "statement" : "statements"}` : "",
              l.pdfs ? `${l.pdfs.toLocaleString("en-SG")} ${l.pdfs === 1 ? "PDF" : "PDFs"}` : "",
              l.rows ? `${l.rows.toLocaleString("en-SG")} rows` : "",
            ].filter(Boolean);
            const to = l.folder ? `/upload?account=${encodeURIComponent(l.folder)}` : null;
            return (
              <li title={l.paths?.join("\n")} className="border-t border-line py-2 first:border-t-0">
                <div className="flex items-center justify-between gap-3">
                  <AccountName l={l} />
                  <span className="shrink-0"><StatusChip tone={CHIP_TONE[String(l.chip.ok) as keyof typeof CHIP_TONE]}>{l.chip.text}</StatusChip></span>
                </div>
                {(details.length > 0 || to) && (
                  <div className="mt-1 flex flex-wrap items-center justify-between gap-x-3 gap-y-1 pl-7 text-[12px] text-ink-2">
                    <span className="num">{details.join(" · ")}</span>
                    {to && (
                      <a href={to} onClick={onLinkClick(to)} aria-label={`Add statement for ${l.name}`}
                        className={buttonClass("secondary")}>
                        Add statement
                      </a>
                    )}
                  </div>
                )}
              </li>
            );
          }} />
        </ul>
      )}
    </Card>
  );
}

/** Years completed on `today`, from an ISO date of birth. */
export function ageOn(birth: string, today = new Date()): number {
  const [y, m, d] = birth.split("-").map(Number);
  let age = today.getFullYear() - y;
  if (today.getMonth() + 1 < m || (today.getMonth() + 1 === m && today.getDate() < d)) age -= 1;
  return age;
}

// An empty date of birth opens the calendar thirty years back, so the year
// dropdown starts nearer the answer than this year does.
const aroundThirty = () => new Date(new Date().getFullYear() - 30, 0, 1);

// The date of birth sets the CPF age bands and the 55 and 65 milestones on
// the growth projection. Kept in the data folder's profile.json, never the repo.
function AboutYou({ about }: { about: About }) {
  // G18: null until the box is used, so a date saved from another tab shows here while it is untouched
  const [typed, setTyped] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);     // the value the last save wrote
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // a damaged file can be repaired by saving, even with the date left empty
  const base = saved ?? about.birth_date ?? "";
  const value = typed ?? base;
  const changed = value !== base;
  const dirty = changed || Boolean(about.problem && saved === null);
  // G18: leaving the page with a date picked and not saved asks first. A damaged
  // file alone is not an unsaved edit, so it never asks on its own.
  useUnsavedGuard(changed);
  async function save() {
    const sent = value;
    setSaving(true);
    setError(null);
    try {
      await postJSON("/api/profile", JSON.stringify({ birth_date: sent || null }));
      setSaved(sent);
      // the box follows what is saved again, unless it was changed while the save was on its way
      setTyped((t) => (t === sent ? null : t));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  }
  const shown = saved ?? about.birth_date;
  return (
    <Card title="About you">
      <div className="flex flex-wrap items-center gap-2 text-[13px]">
        <label htmlFor="birth-date" className="text-ink-2">Date of birth, for CPF ages</label>
        <DateField id="birth-date" label="Date of birth" value={value} disabled={saving} onChange={(v) => setTyped(v === base ? null : v)} clearable
          yearRange={[1900, new Date().getFullYear()]} max={isoDay()} defaultMonth={aroundThirty()} className="w-40" />
        <Button onClick={save} disabled={!dirty || saving}>Save</Button>
        {shown && !dirty && (
          <span className="text-ink-3">{formatDay(shown)}, age {ageOn(shown)}</span>
        )}
        {saved !== null && !dirty && <span className="text-good">Saved</span>}
        {error && <span role="alert" className="text-warn">{error}</span>}
        {about.problem && saved === null && <span role="alert" className="text-warn">{about.problem}</span>}
      </div>
    </Card>
  );
}
