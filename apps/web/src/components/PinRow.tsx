import { useEffect, useRef, useState } from "react";
import { postJSON, rowsChanged, useApi, type GroupRow, type GroupRows, type PinSet, type Relinked, type RuleUndone } from "../api";
import { formatAmount } from "../money";
import { formatDay } from "../dates";
import { useUnsavedGuard } from "../router";
import { BankMark } from "./BankMark";
import { Button } from "./Button";
import { Combobox } from "./Combobox";
import { Select } from "./Select";
import { Loading } from "./States";
import { showToast } from "./Toast";

// One row set on its own: a key rule in the data folder's keys.csv pins this
// row's category (and a payee, when given), so a wedding gift or a donation is
// filed without a pattern rule that would also catch the next transfer to the
// same person. G13: the row is filed in the record at once, and the toast's
// Undo takes the rule and the row's category back together.
export type PinFields = { category: string; payee: string; kind: string; note: string };
// what was typed, and (`base`) what it is measured against: what the form opened on, or what its last save sent
export type PinDraft = PinFields & { base?: PinFields };

/** Typed and not saved, against `base`; a kind counts only with a payee. */
export const pinDirty = (d: PinFields, base: PinFields) => d.category !== base.category
  || d.payee.trim() !== base.payee.trim() || d.note.trim() !== base.note.trim() || (!!d.payee.trim() && d.kind !== base.kind);

type Pinned = { change?: string | null; relinked?: Relinked; unchanged?: boolean; pinned?: PinSet };

/** Undo one pin (or any rule write named by its token): the rule and the rows it filed. */
export async function undoRuleWrite(change: string): Promise<boolean> {
  try {
    const r = await postJSON<RuleUndone>("/api/rules/undo", JSON.stringify({ change }));
    showToast({ message: `Undone, ${rowsChanged(r.relinked, true)}.` });
    return true;
  } catch (e) {
    showToast({ tone: "error", message: (e as Error).message });
    return false;
  }
}

/** Save one row's category as a key rule; the toast says how many rows changed and offers Undo. */
export async function savePin(body: { key: string; category: string; payee?: string; kind?: string; note?: string }) {
  const r = await postJSON<Pinned>("/api/rules/pin", JSON.stringify(body));
  showToast({
    message: `Category set, ${rowsChanged(r.relinked)}.`,
    action: r.change ? { label: "Undo", onClick: () => undoRuleWrite(r.change!) } : undefined,
  });
  return r;
}

// `draft` and `onDraft` let a page keep what was typed above the form, so the
// form can be mounted again (a layout change, Less then More) without losing it.
// A page that keeps drafts also guards them (`onDirty`): the typed work outlives
// the form, so the question about leaving must too. Such a page keeps the form
// mounted while its save is on its way (`onSaving`), hidden if closed meanwhile,
// so the form that sent a save is the one that settles it.
export function PinForm({ row, categories, kinds, onPinned, draft, onDraft, onDirty, onSaving }: {
  row: GroupRow;
  categories: string[];
  kinds: string[];
  onPinned?: () => void;
  draft?: PinDraft;
  onDraft?: (d: PinDraft) => void;
  onDirty?: (dirty: boolean) => void;
  onSaving?: (on: boolean) => void;
}) {
  // G13: opens on what the row already has: its category, and the kind the
  // rules page would suggest for the text it printed (a person only for a
  // counterparty, as `suggest` in finance/rules_app.py), not "Person" for all
  // but a merchant's own line
  const [base, setBase] = useState<PinFields>(() => draft?.base ?? {
    category: row.category, payee: row.payee, kind: row.similar?.kind ?? (row.field === "counterparty" ? "Person" : "Merchant"), note: "",
  });
  const [category, setCategory] = useState(draft?.category ?? base.category);
  const [payee, setPayee] = useState(draft?.payee ?? base.payee);
  const [kind, setKind] = useState(draft?.kind ?? base.kind);
  const [note, setNote] = useState(draft?.note ?? base.note);
  const typed = { category, payee, kind, note };
  const latest = useRef(typed);
  latest.current = typed;
  useEffect(() => { onDraft?.({ category, payee, kind, note, base }); }, [category, payee, kind, note, base]);
  // a save on its way: what is typed meanwhile is measured against what it sent, and a failure puts `base` back
  const [sending, setSending] = useState<PinFields | null>(null);
  // G18: something typed and not saved (a draft kept from before a change of layout included) asks before the
  // page is left
  const dirty = pinDirty(typed, sending ?? base);
  // and a save on its way holds the page too, until it lands: one that fails leaves the edit unsaved
  const holding = dirty || !!sending;
  useUnsavedGuard(!onDirty && holding);
  useEffect(() => { onDirty?.(holding); }, [holding]);
  // a form replaced while its save ran (the layout crossing 768px) leaves the row to the form that replaced it
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const what = `the ${formatDay(row.date)} row`;

  async function save() {
    setSaving(true);
    setError(null);
    const sent = { category, payee: payee.trim(), kind, note: note.trim() };
    setSending(sent);
    onSaving?.(true);
    try {
      await savePin({ key: row.key, ...sent });
      // what was typed while the save was on its way was not sent: the form stays open on it, measured
      // against what was saved, and only a form with nothing left unsaved is done
      // said to the page straight away as well: a form hidden meanwhile is unmounted as the save ends, before
      // its effects would say it
      if (!mounted.current) return;
      if (pinDirty(latest.current, sent)) {
        setBase(sent);
        onDraft?.({ ...latest.current, base: sent });
        onDirty?.(true);
      } else {
        onPinned?.();
      }
    } catch (e) {
      if (!mounted.current) return;
      onDirty?.(pinDirty(latest.current, base));
      setError((e as Error).message);
    } finally {
      setSending(null);
      setSaving(false);
      onSaving?.(false);
    }
  }

  return (
    <div className="flex flex-col gap-1">
      <div className="flex flex-wrap items-center gap-2 text-[13px]">
        <Combobox label={`Category for ${what}`} value={category} options={categories}
          placeholder="Choose a category" className="w-48" onChange={setCategory} />
        <input value={payee} placeholder="Payee (optional)" aria-label={`Payee for ${what}`}
          onChange={(e) => setPayee(e.target.value)} className="h-8 w-40 rounded-md border border-line bg-panel px-2" />
        {payee.trim() && (
          <Select label={`Kind for ${what}`} placeholder="Kind" value={kind} onChange={setKind} className="w-32"
            options={kinds.map((k) => ({ value: k, label: k }))} />
        )}
        <input value={note} placeholder="Why (optional), e.g. a wedding gift" aria-label={`Note for ${what}`}
          onChange={(e) => setNote(e.target.value)} className="h-8 min-w-0 flex-1 basis-48 rounded-md border border-line bg-panel px-2" />
        <Button variant="primary" disabled={saving || !category} onClick={() => void save()}>
          {saving ? "Saving…" : "Save for this row"}
        </Button>
      </div>
      {error && <p className="m-0 text-[12px] text-warn" role="alert">{error}</p>}
    </div>
  );
}

export function pinnedText(p: NonNullable<GroupRow["pinned"]>) {
  const set = `Set to ${p.category}${p.payee ? `, ${p.payee}` : ""} by ${p.file} line ${p.line}`;
  // G13: a pin files its row at once; one that has not (a rules file changed by hand) waits for Apply rules
  return p.applied ? `${set}.` : `${set}; not applied yet.`;
}

// The rows behind one needs-rule group, newest first, each one able to be set
// on its own. `expected` is how many rows the group counted: the upload
// check counts statement rows the record has not loaded yet.
export function GroupRowsPanel({ query, expected }: { query: string; expected: number }) {
  const state = useApi<GroupRows>(`/api/rules/rows?${query}`);
  const [open, setOpen] = useState<string | null>(null);
  if (state.error) return <p className="m-0 text-[13px] text-warn">Could not list the rows: {state.error}</p>;
  if (!state.data) return <Loading what="Listing the rows" />;
  const d = state.data;
  const missing = expected - d.total;
  return (
    <div className="flex flex-col gap-1 text-[13px]">
      <ul className="m-0 flex list-none flex-col p-0">
        {d.rows.map((r) => (
          <li key={r.key} className="border-t border-line py-1.5 first:border-t-0">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
              <span className="num text-ink-2">{formatDay(r.date)}</span>
              <span className="num">{formatAmount(r.amount, r.currency)}</span>
              {/* G18: the bank's mark beside the account, as on Transactions */}
              <span className="inline-flex items-center gap-1.5 text-ink-3"><BankMark bank={r.bank ?? "other"} />{r.account}</span>
              {r.pinned ? (
                <span className="text-good">{pinnedText(r.pinned)}</span>
              ) : (
                <Button size="sm" aria-expanded={open === r.key}
                  aria-label={`Set the ${formatDay(r.date)} row of ${formatAmount(r.amount, r.currency)} on its own`}
                  onClick={() => setOpen(open === r.key ? null : r.key)} className="ml-auto">
                  {open === r.key ? "Cancel" : "Set this row"}
                </Button>
              )}
            </div>
            {open === r.key && !r.pinned && (
              <div className="mt-1.5">
                <PinForm row={r} categories={d.categories} kinds={d.kinds} onPinned={() => setOpen(null)} />
              </div>
            )}
          </li>
        ))}
      </ul>
      {d.total > d.rows.length && <p className="m-0 text-[12px] text-ink-3">The newest {d.rows.length} of {d.total}.</p>}
      {missing > 0 && (
        <p className="m-0 text-[12px] text-ink-3">
          {missing === 1 ? "1 more is" : `${missing} more are`} not in your record yet: load, then set {missing === 1 ? "it" : "them"} here.
        </p>
      )}
    </div>
  );
}
