import { Fragment, useEffect, useId, useRef, useState } from "react";
import { Check, ChevronLeft, ChevronRight, Plus, Trash2 } from "lucide-react";
import {
  postJSON, useApi, watchStatsRebuild, type Budgets, type BudgetRow, type Plan as PlanT, type PlanOneOff, type PlanState,
  type PlanTrip, type Suggestions,
} from "../api";
import { SuggestChip } from "../components/SuggestChip";
import { chipKind, chipText, dollarsText, typedValue } from "../suggest";
import { centsText, formatSgd, parseCents, type Cents } from "../money";
import { formatDay, formatMonth, formatRange, isoMonth, shiftMonth } from "../dates";
import { Button, IconButton } from "../components/Button";
import { useCopilotContext } from "../copilot/pageFilters";
import { Card } from "../components/Card";
import { CollapsibleRows, COLLAPSED_ROWS } from "../components/CollapsibleRows";
import { DateField } from "../components/DateField";
import { EmptyState } from "../components/EmptyState";
import { Field } from "../components/Field";
import { MoneyInput } from "../components/MoneyInput";
import { MonthField } from "../components/MonthField";
import { SaveBar } from "../components/SaveBar";
import { Select } from "../components/Select";
import { TextInput } from "../components/TextInput";
import { Loading, Problem } from "../components/States";
import { CARD_CELL, CARD_ROW, HEAD_ROW, ROW, TH } from "../components/tableKit";
import { Tile, TileGrid } from "../components/Tile";
import { showToast } from "../components/Toast";
import { ForwardViewCard } from "./ForwardView";
import { onLinkClick, useUnsavedGuard } from "../router";

// this device's month, not UTC's: early on the 1st in Singapore, UTC is still last month
const thisMonth = () => isoMonth();
// Statements arrive a month or more late, so today's month has no actuals yet.
// The budgets open on the record's last month, or today's when the record is empty.
const startMonth = (asOf: string | null) => {
  const last = asOf?.slice(0, 7);
  return last && last < thisMonth() ? last : thisMonth();
};

// asOf is the record's last transaction date; undefined while it is still loading.
export function Plan({ asOf }: { asOf?: string | null }) {
  return (
    <div className="flex flex-col gap-3">
      <BudgetEditor asOf={asOf} />
      <PlanEditor />
      <ForwardViewCard />
    </div>
  );
}

// ------------------------------------------------------------ budgets

const isIncome = (r: BudgetRow) => r.kind === "income" || r.need_class === "Income";
const isOver = (r: BudgetRow) => r.planned != null && !isIncome(r) && r.actual > r.planned;

/** What a planned amount is shown as in its box: whole dollars, or dollars and cents when it has cents. */
const planned = (cents: Cents | null) => (cents == null ? "" : centsText(cents));

type RowNote = { saved?: boolean; error?: string };

function BudgetEditor({ asOf }: { asOf?: string | null }) {
  const [picked, setMonth] = useState<string | null>(null);
  const month = picked ?? (asOf === undefined ? null : startMonth(asOf));
  // a write empties the kept answers and has every view on screen ask again (api.ts), so a save needs no
  // version in the address to be read back
  const { data, error, loading } = useApi<Budgets>(month && `/api/budgets?month=${month}`);
  // G22: "this month" in a question here is the budget month on screen
  useCopilotContext({ month });
  const [only, setOnly] = useState(false);
  // what the last save of each row said: a tick that fades, or why it was refused, under the row
  const [notes, setNotes] = useState<Record<string, RowNote>>({});
  const ticks = useRef(new Map<string, number>());
  useEffect(() => () => ticks.current.forEach((t) => window.clearTimeout(t)), []);
  const note = (category: string, n: RowNote | null) => {
    window.clearTimeout(ticks.current.get(category));
    setNotes((all) => {
      const { [category]: _gone, ...rest } = all;
      return n ? { ...rest, [category]: n } : rest;
    });
    if (n?.saved) ticks.current.set(category, window.setTimeout(() => note(category, null), 2200));
  };

  async function save(category: string, text: string) {
    // G17: the text read as exact cents (money.ts), never through a float
    const v = parseCents(text);
    if (v !== null && Number.isNaN(v)) {
      note(category, { error: "A planned amount is a number of dollars, zero or more." });
      return;
    }
    // G17: the row by its category's id, with the name shown, which the server checks against it
    const id = data?.rows.find((r) => r.category === category)?.category_id;
    try {
      await postJSON("/api/budgets", JSON.stringify({ category_id: id, category, month, planned: v }));
      note(category, { saved: true });
    } catch (e) {
      note(category, { error: (e as Error).message });
    }
  }

  async function copyFrom(src: string) {
    try {
      await postJSON("/api/budgets/copy", JSON.stringify({ from: src, to: month }));
      setNotes({});
    } catch (e) {
      showToast({ tone: "error", message: (e as Error).message });
    }
  }

  const go = (m: string | null) => { if (m) { setMonth(m); setNotes({}); } };
  const prev = month ? data?.months.find((m) => m < month) : undefined;
  return (
    <Card title="Budgets" actions={
      <div className="flex flex-wrap items-center gap-2 text-[13px]">
        {prev && <Button onClick={() => copyFrom(prev)}>Copy from {formatMonth(prev)}</Button>}
        <span className="flex items-center gap-1">
          <Button size="icon" aria-label="Previous month" disabled={!month} onClick={() => month && go(shiftMonth(month, -1))}>
            <ChevronLeft size={15} aria-hidden />
          </Button>
          <MonthField label="Month" value={month ?? ""} onChange={(v) => v && go(v)} className="w-32" />
          <Button size="icon" aria-label="Next month" disabled={!month} onClick={() => month && go(shiftMonth(month, 1))}>
            <ChevronRight size={15} aria-hidden />
          </Button>
        </span>
      </div>}>
      {(!month || loading || (data && data.month !== month)) && !(data && data.month === month) && <Loading what="Loading budgets" />}
      {error && <Problem message={error} />}
      {data && data.month === month && (
        <div className="flex flex-col gap-4">
          <Overview rows={data.rows} only={only} onOnly={setOnly} />
          {sections(data, only).map((s) => (
            <div key={s.title} className="relative">
              <BudgetTable title={s.title} groups={s.groups} rows={data.rows} only={only} month={month!} save={save}
                notes={notes} clearNote={(c) => note(c, null)} />
            </div>
          ))}
          {only && !data.rows.some(isOver) && <EmptyState title="Nothing is over plan this month" />}
        </div>
      )}
    </Card>
  );
}

/** The month at a glance: what is planned, what has gone, how many categories passed their plan, and a way to list just those. */
function Overview({ rows, only, onOnly }: { rows: BudgetRow[]; only: boolean; onOnly: (v: boolean) => void }) {
  const spend = rows.filter((r) => !isIncome(r));
  const plan = spend.reduce((s, r) => s + (r.planned ?? 0), 0);
  const actual = spend.reduce((s, r) => s + r.actual, 0);
  const over = spend.filter(isOver).length;
  // the share of the plan is of the categories that have one: money spent where nothing is planned is said apart
  const onPlan = spend.filter((r) => r.planned != null).reduce((s, r) => s + r.actual, 0);
  const unplanned = actual - onPlan;
  const pct = plan > 0 ? `${Math.round((onPlan * 100) / plan)}% of plan` : "";
  // the percentage covers the planned categories only, so when some spending sits outside them the note
  // gives that part's own figure: the actual in the tile is the two parts added up
  const note = unplanned > 0
    ? [pct && `${formatSgd(onPlan)} in planned categories is ${pct}`, `${formatSgd(unplanned)} unplanned`].filter(Boolean).join(", ")
    : pct;
  return (
    <div className="flex flex-col gap-2">
      <TileGrid cols={3} label="Budgets at a glance">
        <Tile label="Planned" value={formatSgd(plan)} />
        <Tile label="Actual so far" value={formatSgd(actual)} tone={onPlan > plan ? "warn" : "neutral"} note={note || undefined} />
        <Tile label="Over plan" value={String(over)} tone={over > 0 ? "warn" : "neutral"} note={over === 1 ? "category" : "categories"} />
      </TileGrid>
      <div>
        <button type="button" aria-pressed={only} disabled={over === 0 && !only} onClick={() => onOnly(!only)}
          className={`h-8 rounded-full border px-3 text-[13px] disabled:opacity-40 ${only ? "border-ink bg-ink text-page" : "border-line text-ink-2 hover:bg-hover"}`}>
          Show only over plan
        </button>
      </div>
    </div>
  );
}

type Group = { name: string | null; kind: string };

// Spending buckets first, in the Categories page's order, then income on its
// own. A record from before buckets sends no groups: one plain list. Income
// is never over plan, so listing only what is over leaves it out.
function sections(data: Budgets, only: boolean): { title: string; groups: Group[] }[] {
  const groups: Group[] = data.groups?.length ? data.groups : [{ name: null, kind: "spend" }];
  const spend = groups.filter((g) => g.kind !== "income");
  const income = groups.filter((g) => g.kind === "income");
  return [
    ...(spend.length ? [{ title: "Spending", groups: spend }] : []),
    ...(income.length && !only ? [{ title: "Income", groups: income }] : []),
  ];
}

// How much of the planned amount the actual is: a bar, and the share in words
// beside it so the column reads without the bar.
function Progress({ planned, actual, over }: { planned: number | null; actual: number; over: boolean }) {
  if (!planned) return null;
  const share = actual / planned;
  // whole cents, so 4,100 of 4,000 is 103, not 102 from 102.49999
  const pct = Math.round((actual * 100) / planned);
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 flex-1 rounded-full bg-tile">
        <div className={`h-1.5 rounded-full ${over ? "bg-[var(--warn)]" : "bg-[var(--c3)]"}`} style={{ width: `${Math.max(0, Math.min(100, share * 100))}%` }} />
      </div>
      <span className={`w-10 text-right text-[12px] ${over ? "text-warn" : "text-ink-2"}`}>{pct > 999 ? ">999%" : `${pct}%`}</span>
    </div>
  );
}

// A planned amount's box. It keeps what is typed until the row is saved (on Enter or on leaving the box).
function PlannedInput({ category, cents, invalid, save, onEdit }: {
  category: string; cents: number | null; invalid: boolean; save: (category: string, text: string) => void; onEdit: () => void;
}) {
  const shown = planned(cents);
  const [text, setText] = useState(shown);
  // A new saved amount replaces the box, set while rendering rather than in an
  // effect: a mount effect could run after the first keystroke and put the
  // saved amount back over it.
  const [was, setWas] = useState(shown);
  if (shown !== was) {
    setWas(shown);
    setText(shown);
  }
  return (
    <MoneyInput label={`Planned for ${category}`} before="S$" value={text} placeholder="Not set" invalid={invalid}
      onChange={(t) => { setText(t); onEdit(); }} onCommit={(t) => { if (t !== shown) save(category, t); }}
      className="ml-auto w-28 max-md:w-32" />
  );
}

// Under 768px a row is a small card: the category and its planned amount on the
// first line, what has gone and the share used on the second.
const CARD = "max-md:grid max-md:grid-cols-[minmax(0,1fr)_auto] max-md:items-center max-md:gap-x-3 max-md:gap-y-1.5";
const ACTUAL = "max-md:before:mr-1.5 max-md:before:text-ink-3 max-md:before:content-['Actual']";

function BudgetTable({ title, groups, rows, only, month, save, notes, clearNote }: {
  title: string; groups: Group[]; rows: Budgets["rows"]; only: boolean; month: string;
  save: (category: string, text: string) => void; notes: Record<string, RowNote>; clearNote: (category: string) => void;
}) {
  const incomeTable = groups.every((g) => g.kind === "income");
  return (
    <table className="num w-full border-collapse text-[13px] max-md:block" role="table">
      <thead className="max-md:hidden" role="rowgroup">
        <tr className={HEAD_ROW} role="row">
          <th className={TH} role="columnheader">{title}</th>
          <th className={`${TH} text-right`} role="columnheader">Planned</th>
          <th className={`${TH} text-right`} role="columnheader">Actual</th>
          <th className={`${TH} w-44 pl-3`} role="columnheader">{incomeTable ? "Of plan" : "Used"}</th>
        </tr>
      </thead>
      {groups.map((g) => {
        const mine = g.name == null ? rows : rows.filter((r) => (r.category_group ?? null) === g.name);
        const listed = only ? mine.filter(isOver) : mine;
        if (listed.length === 0) return null;
        const income = g.kind === "income";
        const plan = mine.some((r) => r.planned != null) ? mine.reduce((s, r) => s + (r.planned ?? 0), 0) : null;
        const actual = mine.reduce((s, r) => s + r.actual, 0);
        const over = plan != null && !income && actual > plan;
        return (
          <tbody key={g.name ?? "all"} className="max-md:mt-2 max-md:block" role="rowgroup">
            {g.name != null && (
              <tr className={`${ROW} bg-tile font-semibold max-md:rounded-lg max-md:border-t-0 max-md:px-3 max-md:py-2 ${CARD}`}
                aria-label={`${g.name} total`} role="row">
                <td className="py-1.5 pl-2 max-md:p-0" role="cell">{g.name}</td>
                <td className="py-1.5 text-right max-md:p-0" role="cell">{plan == null ? "" : formatSgd(plan)}</td>
                <td className={`py-1.5 text-right max-md:p-0 max-md:text-left ${ACTUAL} ${over ? "text-warn" : ""}`} role="cell">{formatSgd(actual)}</td>
                <td className="py-1.5 pl-3 pr-2 max-md:p-0" role="cell"><Progress planned={plan} actual={actual} over={over} /></td>
              </tr>
            )}
            {listed.map((r) => {
              const rowOver = isOver(r);
              const n = notes[r.category];
              return (
                <Fragment key={r.category}>
                  <tr className={`${ROW} max-md:py-2.5 ${CARD}`} role="row">
                    <td className={`py-1 max-md:p-0 ${g.name != null ? "pl-5 max-md:pl-0" : ""}`} role="cell">
                      {r.category}
                      <span role="status" className="ml-2 inline-flex items-center gap-0.5 align-middle text-[12px] font-normal text-good">
                        {n?.saved && <><Check size={12} aria-hidden /> Saved</>}
                      </span>
                    </td>
                    <td className="py-1 text-right max-md:p-0" role="cell">
                      <PlannedInput category={r.category} cents={r.planned} invalid={!!n?.error} save={save} onEdit={() => n?.error && clearNote(r.category)}
                        key={`${month}-${r.category}`} />
                    </td>
                    <td className={`py-1 text-right max-md:p-0 max-md:text-left ${ACTUAL} ${rowOver ? "text-warn" : ""}`} role="cell">{formatSgd(r.actual)}</td>
                    <td className="py-1 pl-3 pr-2 max-md:p-0" role="cell"><Progress planned={r.planned} actual={r.actual} over={rowOver} /></td>
                  </tr>
                  {n?.error && (
                    <tr role="row" className="max-md:block">
                      <td colSpan={4} role="cell" className="pb-1.5 pl-5 text-[12px] text-danger max-md:block max-md:pl-0">
                        <span role="alert">{n.error}</span>
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        );
      })}
    </table>
  );
}

// ------------------------------------------------------------ the plan

/**
 * G13: the employer's CPF rate is typed as a percentage (17) and stored as the
 * fraction the forward view reads (0.17). A rate is not money; the conversion
 * goes through a fixed number of decimals so 0.17 shows as 17, not 17.000000000000004.
 */
export function fractionToPercent(f: number | undefined | null): string {
  return f == null || !Number.isFinite(f) ? "" : String(Number((f * 100).toFixed(6)));
}

/** "17" as 0.17; null for anything that is not a percentage from 0 to 100. */
export function percentToFraction(text: string): number | null {
  const t = text.trim();
  // "17." is on its way to "17.5", so a trailing point is still a number
  if (!/^\d+(\.\d*)?$/.test(t)) return null;
  const p = Number(t);
  return p <= 100 ? Number((p / 100).toFixed(8)) : null;
}

// The names a person reads for the forward view's spending rates; the stored
// value stays the stat's own. A rate this page does not know shows as stored.
// The detail rides in the option, so the select says what each one is.
export const SPEND_RATES: Record<string, { name: string; detail: string }> = {
  window: { name: "A typical month", detail: "middle of the last 6" },
  mean: { name: "An average month", detail: "the last 6 averaged" },
  home: { name: "A month at home", detail: "trips taken out" },
  phase: { name: "The current phase's average", detail: "since it began" },
};
const rateLabel = (r: string) => (SPEND_RATES[r] ? `${SPEND_RATES[r].name} (${SPEND_RATES[r].detail})` : r);

// G17: the plan as this page holds it: the server's shape, money in cents, except
// that an amount may hold the text typed while it is not one yet (Save waits
// for it) and an unset amount is null.
type Amount = Cents | string | null;
type MoneyKey = "insurance_minor" | "other_income_minor" | "repayments_minor";
type TripDraft = Omit<PlanTrip, "per_day_minor" | "flights_minor"> & { per_day_minor?: Amount; flights_minor?: Amount };
type OneOffDraft = Omit<PlanOneOff, "amount_minor"> & { amount_minor: Amount };
type PlanDraft = Omit<PlanT, MoneyKey | "cpf_employer_rate" | "trips" | "one_offs"> & {
  [K in MoneyKey]?: Amount } & { cpf_employer_rate?: number | string; trips?: TripDraft[]; one_offs?: OneOffDraft[] };

// G14: pay and investing are edited on Net worth, where the projection
// previews them; the forward view here reads the same rows.
const MONEY_FIELDS: { key: MoneyKey; label: string }[] = [
  { key: "insurance_minor", label: "Insurance premiums" },
  { key: "other_income_minor", label: "Other income" },
  { key: "repayments_minor", label: "Repayments" },
];

// A field holds the last good value, or the text typed when it is not one yet:
// a string in the plan is a figure that is not a number, and Save waits for it.
const typed = (v: unknown): v is string => typeof v === "string";

function hasBad(plan: PlanDraft): boolean {
  if (MONEY_FIELDS.some((f) => typed(plan[f.key])) || typed(plan.cpf_employer_rate)) return true;
  return (plan.trips ?? []).some((t) => typed(t.per_day_minor) || typed(t.flights_minor))
    || (plan.one_offs ?? []).some((o) => typed(o.amount_minor));
}

function PlanEditor() {
  const { data, error, loading } = useApi<PlanState>("/api/plan");
  // G13: what the record already knows, as chips; a failed ask just means no chips
  const { data: known } = useApi<Suggestions>("/api/suggestions");
  const [plan, setPlan] = useState<PlanDraft | null>(null);
  // the plan as the server holds it, to tell what is unsaved
  const [stored, setStored] = useState<PlanDraft | null>(null);
  const [saving, setSaving] = useState(false);
  // bumped by Discard, so every box shows the saved plan again, whatever was typed in it
  const [epoch, setEpoch] = useState(0);
  // an edit in progress survives a reload caused by an unrelated write (a budget, a class)
  // A ref, not state: it must not re-run the effect with the plan as it was
  // before a save; only a new response from the server may.
  const unsaved = useRef(false);
  const dirty = !!plan && !!stored && (JSON.stringify(plan) !== JSON.stringify(stored) || hasBad(plan));
  unsaved.current = dirty;
  // G18: leaving the page with the plan edited and not saved asks first
  useUnsavedGuard(dirty);
  // the plan as last drawn, so a save that lands can tell whether anything was typed while it was on its way
  const latest = useRef(plan);
  latest.current = plan;
  useEffect(() => { if (data && !unsaved.current) { setPlan(data.plan); setStored(data.plan); } }, [data]);
  // the first answer fills the form in this render, not after it: an empty form in between would show
  // "No plan yet" (an alert) for a frame on every visit
  if (data && !plan && !stored) { setPlan(data.plan); setStored(data.plan); }
  if (loading && !data) return <Loading what="Loading your plan" />;
  if (error || !data || !plan) return <Problem message={error ?? "No plan yet"} retry />;

  // the ref is set here, in the event, not left to the next render: a reload that lands between the edit and
  // that render would otherwise put the saved plan over what was just typed
  const set = (k: string, v: unknown) => { unsaved.current = true; setPlan({ ...plan, [k]: v }); };

  async function save() {
    if (!plan) return;
    if (hasBad(plan)) {
      // the field says what is wrong under itself; take the cursor there
      document.querySelector<HTMLElement>('[aria-invalid="true"]')?.focus();
      return;
    }
    setSaving(true);
    try {
      const sent = JSON.stringify(plan);
      const saved = await postJSON<PlanT>("/api/plan", sent);
      // G18: typing made while the save was on its way stays, still unsaved, even back to the old value
      if (JSON.stringify(latest.current) === sent) setPlan(saved);
      setStored(saved);
      // G13: the save reruns the stats in the background, and the forward view redraws once they are done
      watchStatsRebuild();
      showToast({ message: "Plan saved. The forward view updates in a moment." });
    } catch (e) {
      showToast({ tone: "error", message: (e as Error).message });
    } finally {
      setSaving(false);
    }
  }
  function discard() {
    setPlan(stored);
    setEpoch((n) => n + 1);
  }

  const rates = data.spend_rates.length ? data.spend_rates : Object.keys(SPEND_RATES);
  const rate = plan.spend_rate ?? "window";
  // a chip for a monthly figure the detector sees, and one for the employer's CPF rate for the owner's age
  const monthly = (key: "insurance" | "repayments") => {
    const seen = known?.[key];
    const field = `${key}_minor` as const;
    const kind = seen ? chipKind(typedValue(plan[field]), seen.per_month) : null;
    // the chip fills the whole dollars it shows, as cents
    return seen && kind
      ? <SuggestChip onUse={() => set(field, parseCents(dollarsText(seen.per_month)))}>{chipText(kind, seen.per_month)}</SuggestChip>
      : undefined;
  };
  const ageRate = known?.cpf_employer_rate ?? null;
  const rateOff = ageRate != null && (typeof plan.cpf_employer_rate !== "number" || Math.abs(plan.cpf_employer_rate - ageRate) > 0.0005);
  return (
    <Card title="Plan" pinned actions={<SaveBar dirty={dirty} saving={saving} onSave={save} onDiscard={discard} saveLabel="Save plan" />}>
      <div key={epoch} className="grid grid-cols-1 gap-x-4 gap-y-3 text-[13px] sm:grid-cols-2 lg:grid-cols-4">
        <Field small={false} label="Project until">
          {(id) => (
            <MonthField id={id} value={plan.to ?? ""} onChange={(v) => set("to", v || undefined)}
              label="Project until (month)" clearable />
          )}
        </Field>
        {MONEY_FIELDS.map((f) => (
          <Field small={false} key={f.key} label={f.label} chip={f.key === "insurance_minor" ? monthly("insurance") : f.key === "repayments_minor" ? monthly("repayments") : undefined}>
            {(id) => (
              <AmountInput id={id} label={`${f.label} (S$ a month)`} value={plan[f.key]} after="a month" placeholder="0"
                onChange={(v) => set(f.key, v ?? undefined)} />
            )}
          </Field>
        ))}
        <Field small={false} label="Employer CPF" chip={rateOff && ageRate != null ? (
          <SuggestChip onUse={() => set("cpf_employer_rate", ageRate)}>Rate for your age is {fractionToPercent(ageRate)}%, use</SuggestChip>
        ) : undefined}>
          {(id) => (
            <PercentInput id={id} label="Employer CPF (%)" value={plan.cpf_employer_rate} onChange={(v) => set("cpf_employer_rate", v)} />
          )}
        </Field>
        <Field small={false} label="Spending rate" className="sm:col-span-2">
          {(id) => (
            <Select id={id} label="Spending rate" placeholder="Spending rate" value={rate} onChange={(v) => set("spend_rate", v)} className="w-full"
              options={rates.map((r) => ({ value: r, label: rateLabel(r) }))} />
          )}
        </Field>
      </div>

      <PayAndInvesting plan={plan} />
      <div key={`lists-${epoch}`}>
        <ListEditor title="Planned trips" add="Add trip" rows={plan.trips ?? []} onChange={(v) => set("trips", v)}
          empty="No planned trips"
          blank={{ name: "", from: thisMonth() + "-01", to: thisMonth() + "-07", per_day_minor: null, flights_minor: 0, note: "" }}
          cols={[
            { key: "name", label: "Trip", kind: "text", placeholder: "Where to" },
            { key: "from", label: "From", kind: "date" },
            { key: "to", label: "To", kind: "date" },
            { key: "per_day_minor", label: "Spending", unit: "S$ a day", kind: "money", placeholder: "Like past trips" },
            { key: "flights_minor", label: "Flights", unit: "S$ at start", kind: "money", placeholder: "0" },
          ]} />
        <ListEditor title="One-off payments" add="Add payment" rows={plan.one_offs ?? []} onChange={(v) => set("one_offs", v)}
          empty="No one-off payments"
          blank={{ month: thisMonth(), amount_minor: 0, label: "" }}
          cols={[
            { key: "month", label: "Month", kind: "month" },
            { key: "amount_minor", label: "Amount", unit: "S$", kind: "money", placeholder: "0" },
            { key: "label", label: "What it is", kind: "note", placeholder: "For example, laptop" },
          ]} />
      </div>
    </Card>
  );
}

/**
 * G14: the income rows and investing steps the forward view reads, shown here
 * and edited in one place, the Net worth page. One editor means one set of
 * checks and one live preview, and no second form saving an older copy of the
 * same rows over the first.
 */
export function PayAndInvesting({ plan }: { plan: Pick<PlanT, "income" | "investing" | "dca_minor"> }) {
  const income = plan.income ?? [];
  const steps = plan.investing ?? (plan.dca_minor ? [{ from: null, amount_minor: plan.dca_minor }] : []);
  const lines = [...income.map((pay) => ({ pay, step: null })), ...steps.map((step) => ({ pay: null, step }))];
  return (
    <section className="mt-5" aria-label="Pay and investing">
      <div className="mb-1 flex flex-wrap items-center justify-between gap-2">
        <h3 className="m-0 text-[13px] font-semibold">Pay and investing</h3>
        <a href="/net-worth" onClick={onLinkClick("/net-worth")} className="text-[12px] text-ink-2 underline">Edit on Net worth</a>
      </div>
      {income.length === 0 && steps.length === 0
        ? <EmptyState title="No pay or investing yet">So the forward view counts no income from work.</EmptyState>
        : (
          <ul className="m-0 flex list-none flex-col gap-0.5 p-0 text-[13px]">
            <CollapsibleRows rows={lines} limit={COLLAPSED_ROWS.thin} keyOf={(l, i) => `${l.pay ? "i" : "s"}${i}`} render={(l) => l.pay ? (
              <li className="flex flex-wrap gap-x-2 text-ink-2">
                <span className="text-ink">{l.pay.name || l.pay.note || (l.pay.type === "side" ? "Side income" : "Pay")}</span>
                <span className="num">{formatSgd(l.pay.gross_minor)} a month</span>
                <span>{l.pay.to ? formatRange(l.pay.from, l.pay.to) : `from ${formatDay(l.pay.from)}`}</span>
                {l.pay.type === "side" && <span className="text-ink-3">side income</span>}
              </li>
            ) : (
              <li className="flex flex-wrap gap-x-2 text-ink-2">
                <span className="text-ink">Investing</span>
                <span className="num">{formatSgd(l.step.amount_minor)} a month</span>
                <span>{l.step.from ? `from ${formatMonth(l.step.from)}` : "from the start"}</span>
              </li>
            )} />
          </ul>
        )}
    </section>
  );
}

const NOT_A_NUMBER = "Use digits, like 1,200.50";

/**
 * An amount held as integer cents in the plan (G17). What is typed stays as
 * typed ("12." on its way to "12.5") and is read as exact cents; a figure that
 * is not an amount goes up as the text itself, so the plan knows it is not
 * saveable, and the box says so under itself.
 */
function AmountInput({ id, label, value, onChange, before = "S$", after, placeholder }: {
  id?: string; label: string; value: Amount | undefined; onChange: (v: Amount) => void;
  before?: string; after?: string; placeholder?: string;
}) {
  const show = (v: typeof value) => (v == null ? "" : typed(v) ? v : centsText(v));
  const [text, setText] = useState(show(value));
  useEffect(() => {
    // a new value from outside (a reload, Discard) shows itself, unless the text already says it
    setText((t) => ((typed(value) ? t === value : parseCents(t) === (value ?? null)) ? t : show(value)));
  }, [value]);
  const bad = typed(value);
  const note = useId();
  return (
    <>
      <MoneyInput id={id} label={label} before={before} after={after} placeholder={placeholder} value={text} invalid={bad}
        describedBy={bad ? note : undefined}
        onChange={(t) => {
          setText(t);
          const n = parseCents(t);
          onChange(n === null ? null : Number.isNaN(n) ? t : n);
        }} />
      {bad && <span id={note} role="alert" className="text-[12px] text-danger">{NOT_A_NUMBER}</span>}
    </>
  );
}

// Keeps what is typed ("17.", "") apart from the fraction in the plan, which
// only changes when the text is a percentage; text that is not one goes up as it is.
function PercentInput({ id, label, value, onChange }: {
  id: string; label: string; value: number | string | undefined; onChange: (v: number | string | undefined) => void;
}) {
  const show = (v: typeof value) => (typed(v) ? v : fractionToPercent(v));
  const [text, setText] = useState(show(value));
  useEffect(() => {
    // a new plan from the server shows its own rate, unless the text already says it
    setText((t) => ((typed(value) ? t === value : percentToFraction(t) === (value ?? null)) ? t : show(value)));
  }, [value]);
  const bad = typed(value);
  const note = useId();
  return (
    <>
      <MoneyInput id={id} label={label} after="%" placeholder="17" value={text} invalid={bad} group={false}
        describedBy={bad ? note : undefined}
        onChange={(t) => {
          setText(t);
          const f = percentToFraction(t);
          onChange(t.trim() === "" ? undefined : f != null ? f : t);
        }} />
      {bad && <span id={note} role="alert" className="text-[12px] text-danger">Employer CPF is a percentage from 0 to 100, like 17.</span>}
    </>
  );
}

type Kind = "text" | "note" | "date" | "month" | "money" | "bool";
type Col = { key: string; label: string; kind: Kind; unit?: string; placeholder?: string };

const WIDTH: Record<Kind, string> = { date: "w-36", month: "w-36", money: "w-36", bool: "w-20", text: "min-w-40", note: "min-w-48" };

// Under 1280px a row is a small card (the five columns of a trip do not fit
// beside a sidebar), each field under its own name: the name and the note take
// the whole width, the rest sit two to a line.

function ListEditor<T extends Record<string, unknown>>({ title, add, rows, onChange, blank, cols, empty }: {
  title: string; add: string; rows: T[]; onChange: (rows: T[]) => void; blank: T; cols: Col[]; empty: string;
}) {
  // a trip's "to" never sits before its "from": moving the start past the end takes the end along
  const edit = (i: number, k: string, v: unknown) => onChange(rows.map((r, j) => {
    if (j !== i) return r;
    const next: Record<string, unknown> = { ...r, [k]: v };
    if (k === "from" && typeof v === "string" && typeof r.to === "string" && v && r.to < v) next.to = v;
    return next as T;
  }));
  const name = (c: Col, i: number) => `${c.label}${c.unit ? ` (${c.unit})` : ""}, row ${i + 1}`;
  return (
    <section className="mt-5" aria-label={title}>
      <div className="mb-1 flex items-center justify-between">
        <h3 className="m-0 text-[13px] font-semibold">{title}</h3>
        <Button variant="ghost" size="sm" onClick={() => onChange([...rows, { ...blank }])}>
          <Plus size={13} aria-hidden /> {add}
        </Button>
      </div>
      {rows.length === 0 ? <EmptyState title={empty} /> : (
        <table className="w-full border-collapse text-[13px] max-xl:block">
          <thead className="max-xl:hidden">
            <tr className={HEAD_ROW}>
              {cols.map((c) => (
                <th key={c.key} scope="col" className={`${TH} pr-1.5 ${WIDTH[c.kind]}`}>
                  {c.label}{c.unit && <span className="font-normal text-ink-3"> {c.unit}</span>}
                </th>
              ))}
              <th className="w-8"><span className="sr-only">Remove</span></th>
            </tr>
          </thead>
          <tbody className="max-xl:flex max-xl:flex-col max-xl:gap-2">
            {rows.map((r, i) => (
              <tr key={i} className={CARD_ROW}>
                {cols.map((c) => (
                  <td key={c.key} data-label={`${c.label}${c.unit ? ` ${c.unit}` : ""}`}
                    className={`py-0.5 pr-1.5 align-middle ${CARD_CELL} ${c.kind === "text" || c.kind === "note" ? "max-xl:col-span-2 max-xl:pr-8" : c.kind === "date" ? "max-sm:col-span-2" : ""}`}>
                    <Cell col={c} value={r[c.key]} label={name(c, i)} onChange={(v) => edit(i, c.key, v)} />
                  </td>
                ))}
                <td className="py-0.5 text-right max-xl:absolute max-xl:right-1.5 max-xl:top-1.5 max-xl:p-0">
                  <IconButton label={`Remove ${title.toLowerCase()} row ${i + 1}`} onClick={() => onChange(rows.filter((_, j) => j !== i))}>
                    <Trash2 size={13} /></IconButton>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

function Cell({ col, value, label, onChange }: { col: Col; value: unknown; label: string; onChange: (v: unknown) => void }) {
  if (col.kind === "bool") {
    return <input type="checkbox" aria-label={label} checked={!!value} onChange={(e) => onChange(e.target.checked)} className="size-4" />;
  }
  if (col.kind === "note" || col.kind === "text") return <NoteInput label={label} value={String(value ?? "")} placeholder={col.placeholder} onChange={onChange} />;
  if (col.kind === "money") {
    return <AmountInput label={label} value={value as Amount | undefined} placeholder={col.placeholder} onChange={onChange} />;
  }
  const text = (value as string | undefined) ?? "";
  return col.kind === "month"
    ? <MonthField label={label} value={text} onChange={onChange} />
    : <DateField label={label} value={text} onChange={onChange} />;
}

// G13: a long note (or trip name) stays one line in its row, cut short with an
// ellipsis; the whole of it shows on hover (the title) and under the field
// while it has focus. Under the field means in the row, taking its own space:
// anything floating out of the row would be clipped.
function NoteInput({ label, value, placeholder, onChange }: {
  label: string; value: string; placeholder?: string; onChange: (v: string) => void;
}) {
  const [focused, setFocused] = useState(false);
  const tip = useId();
  const long = value.length > 20;
  return (
    <div>
      <TextInput aria-label={label} placeholder={placeholder} value={value} title={long ? value : undefined}
        aria-describedby={long && focused ? tip : undefined}
        onChange={(e) => onChange(e.target.value)} onFocus={() => setFocused(true)} onBlur={() => setFocused(false)}
        className="w-full truncate" />
      {long && focused && (
        <div id={tip} role="tooltip"
          className="mt-1 whitespace-normal break-words rounded-md border border-line bg-tile px-2 py-1.5 text-[12px] text-ink-2">
          {value}
        </div>
      )}
    </div>
  );
}
