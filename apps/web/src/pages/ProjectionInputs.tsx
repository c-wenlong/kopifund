// G14: the Net worth projection's inputs, typed, previewed and saved on the
// Growth card. Every figure about the owner (pay, dates, side income, amounts)
// is a text box they fill in; only the generic assumptions (returns,
// inflation) arrive filled with defaults, and those are editable and saved too.
// The draft holds exactly what is typed; the server checks it and names the
// field it refuses, so this file never decides whether a number is good. It
// only reads a figure that cannot be an amount at all ("1k5", "6000.505") on
// the spot, and puts whichever message there is under the box it is about.
// G17: an amount crosses as integer cents under its `_minor` name, read from
// the text by money.ts (parseCents) and shown back by centsText.
import { useId } from "react";
import { Plus, Trash2 } from "lucide-react";
import type { GrowthInputs, IncomeRow, IncomeSuggestion, ProjectionSettings, SpendHint, Suggestions } from "../api";
import { addDays, formatMonth, isoMonth, MONTHS } from "../dates";
import { Button } from "../components/Button";
import { DateField } from "../components/DateField";
import { Field } from "../components/Field";
import { EmptyState } from "../components/EmptyState";
import { isMoneyText, isSignedText, MoneyInput } from "../components/MoneyInput";
import { MonthField } from "../components/MonthField";
import { Select } from "../components/Select";
import { TextInput } from "../components/TextInput";
import { HEAD_ROW, TH } from "../components/tableKit";
import { centsText, formatSgd, parseCents, type Cents } from "../money";
import { SuggestChip } from "../components/SuggestChip";
import { chipKind, chipText, claims, coversLast, dollarsText, incomeText, typedValue } from "../suggest";

export type IncomeDraft = {
  name: string; type: "employment" | "side"; from: string; to: string; gross: string; cpf: boolean;
  bonus_months: string; bonus_month: string;
};
export type StepDraft = { from: string; amount: string };
type Rate = "pessimistic_pct" | "expected_pct" | "optimistic_pct" | "inflation_pct" | "salary_growth_pct" | "sums_growth_pct" | "spend_growth_pct";
export type ProjectionDraft = Record<Rate | "spend" | "milestone", string> & Pick<ProjectionSettings, "horizon" | "dollars">;
export type Draft = { income: IncomeDraft[]; investing: StepDraft[]; projection: ProjectionDraft };

const text = (v: unknown) => (v == null ? "" : String(v));
const money = (c: Cents | null) => (c == null ? "" : centsText(c));
const RATES: Rate[] = ["pessimistic_pct", "expected_pct", "optimistic_pct", "inflation_pct", "salary_growth_pct", "sums_growth_pct", "spend_growth_pct"];

/** The saved inputs as the text the boxes show. */
export function toDraft(inputs: GrowthInputs): Draft {
  const p = inputs.projection;
  return {
    income: inputs.income.map((r: IncomeRow) => ({
      name: r.name ?? "", type: r.type ?? "employment", from: r.from ?? "", to: r.to ?? "", gross: money(r.gross_minor),
      cpf: !!r.cpf, bonus_months: text(r.bonus_months), bonus_month: text(r.bonus_month),
    })),
    investing: inputs.investing.map((s) => ({ from: s.from ?? "", amount: money(s.amount_minor) })),
    projection: {
      ...(Object.fromEntries(RATES.map((k) => [k, text(p[k])])) as Record<Rate, string>),
      horizon: p.horizon, dollars: p.dollars, spend: money(p.spend_minor), milestone: money(p.milestone_minor),
    },
  };
}

// "6,000.50" as "6000.50", and an empty box as null: the server reads the rest
const clean = (s: string) => {
  const t = s.replace(/,/g, "").trim();
  return t === "" ? null : t;
};

// An amount's text as cents, or null when empty. Text that is not an amount
// goes as it is: the server refuses it, and the box already says why.
const cents = (s: string): Cents | string | null => {
  const c = parseCents(s);
  return c !== null && Number.isNaN(c) ? s.trim() : c;
};

/** The draft as the body the preview and the save send. */
export function toBody(d: Draft) {
  return {
    income: d.income.map((r) => {
      const bonus = r.type === "employment" ? clean(r.bonus_months) : null;
      return {
        name: r.name.trim(), type: r.type, from: r.from, to: r.to || null, gross_minor: cents(r.gross), cpf: r.cpf,
        bonus_months: bonus, bonus_month: bonus ? (clean(r.bonus_month) ?? "") : null,
      };
    }),
    investing: d.investing.map((s) => ({ from: s.from || null, amount_minor: cents(s.amount) })),
    projection: {
      ...Object.fromEntries(RATES.map((k) => [k, clean(d.projection[k])])),
      horizon: d.projection.horizon, dollars: d.projection.dollars,
      spend_minor: cents(d.projection.spend), milestone_minor: cents(d.projection.milestone),
    },
  };
}

export const sameInputs = (a: Draft, b: Draft) => JSON.stringify(toBody(a)) === JSON.stringify(toBody(b));

// ------------------------------------------------------------ what is wrong, and where

/** Messages by the box they are about: income.2.gross, investing.0.amount, p.inflation_pct, p.spend. */
export type FieldErrors = Record<string, string>;

const RATE_NAMES: Record<Rate, string> = {
  pessimistic_pct: "Pessimistic return", expected_pct: "Expected return", optimistic_pct: "Optimistic return",
  inflation_pct: "Inflation", salary_growth_pct: "Salary growth", sums_growth_pct: "FRS and BHS growth", spend_growth_pct: "Spending growth",
};

// text that cannot be a number at all, read as it is typed; an empty box is the server's to judge
const notNumber = (s: string) => s.trim() !== "" && !isMoneyText(s);
// an amount that is a number but finer than a cent ("6000.505"), which cents cannot hold
const finerThanCents = (s: string) => !notNumber(s) && Number.isNaN(parseCents(s) ?? 0);
const amountError = (s: string, what: string, like: string) =>
  notNumber(s) ? `${what} must be a number, like ${like}` : finerThanCents(s) ? `${what} is in dollars and cents, no finer` : undefined;
const put = (out: FieldErrors, key: string, message: string | undefined) => { if (message) out[key] = message; };
// a rate may be under zero (the server allows -50 to 50 a year)
const notRate = (s: string) => s.trim() !== "" && !isSignedText(s);

function typedErrors(d: Draft): FieldErrors {
  const out: FieldErrors = {};
  d.income.forEach((r, i) => {
    put(out, `income.${i}.gross`, amountError(r.gross, "Gross a month", "6,000.50"));
    if (r.type === "employment" && notNumber(r.bonus_months)) out[`income.${i}.bonus_months`] = "Bonus must be a number of months, like 1.5";
  });
  d.investing.forEach((s, i) => {
    put(out, `investing.${i}.amount`, amountError(s.amount, "Amount a month", "300"));
  });
  for (const k of RATES) if (notRate(d.projection[k])) out[`p.${k}`] = `${RATE_NAMES[k]} must be a number, like 6.5`;
  put(out, "p.spend", amountError(d.projection.spend, "Spending a month", "2,500"));
  put(out, "p.milestone", amountError(d.projection.milestone, "Milestone", "1,000,000"));
  return out;
}

// what the server says after "Income row 2 (Name): ", by the start of it
const INCOME_FIELD: [RegExp, string][] = [
  [/^the bonus month/i, "bonus_month"], [/^(the|a) bonus/i, "bonus_months"], [/^gross/i, "gross"],
  [/^the to date/i, "to"], [/^the from date/i, "from"], [/^the name/i, "name"], [/^the type/i, "type"], [/^CPF/, "cpf"],
];
const PROJECTION_FIELD: [RegExp, string][] = [
  [/^The pessimistic return/i, "pessimistic_pct"], [/^The expected return/i, "expected_pct"], [/^The optimistic return/i, "optimistic_pct"],
  [/^The returns run/i, "expected_pct"], [/^Inflation/i, "inflation_pct"], [/^Salary growth/i, "salary_growth_pct"],
  [/^FRS and BHS growth/i, "sums_growth_pct"], [/^Spending growth/i, "spend_growth_pct"],
  [/^Spending a month/i, "spend"], [/^The milestone/i, "milestone"],
];
const sentence = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

/**
 * What the draft's own text says is wrong, plus the server's refusal put
 * against the box it names. A refusal about a row (Income row 2, Investing
 * step 1) or about a setting the page has a box for lands under that box; one
 * it cannot place comes back as `rest`, for the top of the card.
 */
export function placeErrors(draft: Draft, refused: string | null): { fields: FieldErrors; rest: string | null } {
  const fields = typedErrors(draft);
  if (!refused) return { fields, rest: null };
  const put = (key: string, message: string) => {
    fields[key] ??= message;
    return null;
  };
  const row = /^Income row (\d+)(?: \(.*?\))?: (.+)$/.exec(refused);
  if (row && Number(row[1]) <= draft.income.length) {
    const hit = INCOME_FIELD.find(([re]) => re.test(row[2]));
    return { fields, rest: put(`income.${Number(row[1]) - 1}.${hit?.[1] ?? "gross"}`, sentence(row[2])) };
  }
  const step = /^Investing step (\d+): (.+)$/.exec(refused);
  if (step && Number(step[1]) <= draft.investing.length) {
    return { fields, rest: put(`investing.${Number(step[1]) - 1}.${/amount/i.test(step[2]) ? "amount" : "from"}`, sentence(step[2])) };
  }
  const setting = PROJECTION_FIELD.find(([re]) => re.test(refused));
  if (setting) return { fields, rest: put(`p.${setting[1]}`, refused) };
  return { fields, rest: refused };
}

/** The first message among the keys under one prefix: a row's, or a section's. */
const firstOf = (fields: FieldErrors, ...prefixes: string[]) =>
  Object.entries(fields).find(([k]) => prefixes.some((p) => k.startsWith(p)))?.[1];

// ------------------------------------------------------------ pieces

const firstOfThisMonth = () => `${isoMonth()}-01`;
// A new row starts the day after the last row ended (one job following another),
// or this month when the rows run on, so the date is rarely typed at all.
const blankRow = (rows: IncomeDraft[] = []): IncomeDraft => {
  const last = rows.at(-1)?.to;
  return {
    name: "", type: "employment", from: (last && addDays(last, 1)) || firstOfThisMonth(), to: "", gross: "", cpf: true,
    bonus_months: "", bonus_month: "",
  };
};

/** The message for a box or a row, under it. The line is always there, so nothing below it moves when a message comes. */
function Message({ id, text, className = "" }: { id?: string; text?: string; className?: string }) {
  return <p id={id} aria-live="polite" className={`m-0 min-h-4 text-[12px] leading-4 text-danger ${className}`}>{text}</p>;
}

function Section({ title, action, children }: { title: string; action?: React.ReactNode; children: React.ReactNode }) {
  return (
    <section aria-label={title} className="flex min-w-0 flex-col gap-2">
      <div className="flex items-baseline justify-between gap-2">
        <h3 className="m-0 text-[13px] font-semibold">{title}</h3>
        {action}
      </div>
      {children}
    </section>
  );
}

function AddButton({ onClick, children }: { onClick: () => void; children: React.ReactNode }) {
  return (
    <Button size="sm" onClick={onClick}>
      <Plus size={13} aria-hidden /> {children}
    </Button>
  );
}

function RemoveButton({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button type="button" aria-label={label} onClick={onClick} className="rounded-md p-1 text-ink-3 hover:bg-hover hover:text-ink">
      <Trash2 size={14} />
    </button>
  );
}

const th = `${TH} px-1 whitespace-nowrap`;
const td = "px-1 py-1 align-middle";

// ------------------------------------------------------------ income

// Under 1280px an income row is a small card, each box under its own name
// (eight columns do not fit beside the sidebar); the card boxes sit two to a
// line, the name across the top. Nothing scrolls sideways.
const CARD_ROW = "max-xl:relative max-xl:grid max-md:grid-cols-2 md:max-xl:grid-cols-4 max-xl:gap-x-2 max-xl:gap-y-2 max-xl:rounded-lg max-xl:border max-xl:border-line max-xl:p-3";
const CARD_CELL = "max-xl:flex max-xl:min-w-0 max-xl:flex-col max-xl:gap-1 max-xl:p-0 max-xl:before:text-[12px] max-xl:before:font-medium max-xl:before:text-ink-2 max-xl:before:content-[attr(data-label)]";

/** One income row on one line, or a small card on a narrow screen; the line under it is for what is wrong with it. */
function IncomeRowEditor({ row, i, errors, set, remove, chip }: {
  row: IncomeDraft; i: number; errors: FieldErrors; set: (r: IncomeDraft) => void; remove: () => void;
  /** A suggestion for this row's gross, when the record has one (G13). */
  chip?: React.ReactNode;
}) {
  const n = `income row ${i + 1}`;
  const employment = row.type === "employment";
  const note = useId();
  const bad = (k: string) => !!errors[`income.${i}.${k}`];
  const about = (k: string) => (bad(k) ? note : undefined);
  const message = firstOf(errors, `income.${i}.`);
  return (
    <>
      <tr aria-label={`Income row ${i + 1}`} className={CARD_ROW}>
        <td data-label="Name" className={`${td} ${CARD_CELL} max-xl:order-1 max-xl:col-span-2 max-xl:pr-8`}>
          <TextInput aria-label={`Name, ${n}`} value={row.name} placeholder="Main job" className="w-full min-w-0 text-[13px]"
            invalid={bad("name")} aria-describedby={about("name")}
            onChange={(e) => set({ ...row, name: e.target.value })} />
        </td>
        <td data-label="Type" className={`${td} ${CARD_CELL} max-xl:order-2`}>
          <Select label={`Type, ${n}`} placeholder="Type" value={row.type} className="w-full min-w-0"
            options={[{ value: "employment", label: "Job" }, { value: "side", label: "Side income" }]}
            onChange={(v) => {
              const type = v as IncomeDraft["type"];
              // employment pays CPF and side income does not, unless the box is changed
              set({ ...row, type, cpf: type === "employment", ...(type === "side" ? { bonus_months: "", bonus_month: "" } : {}) });
            }} />
        </td>
        <td data-label="From" className={`${td} ${CARD_CELL} max-xl:order-4 max-sm:col-span-2`}>
          <DateField label={`From, ${n}`} value={row.from}
            // moving the start past the end takes the end along, so the row never runs backwards
            onChange={(from) => set({ ...row, from, to: row.to && row.to < from ? from : row.to })} />
        </td>
        <td data-label="To" className={`${td} ${CARD_CELL} max-xl:order-5 max-sm:col-span-2`}>
          <DateField label={`To, ${n}`} value={row.to} clearable min={row.from} placeholder="Ongoing" title="Empty runs to the horizon"
            onChange={(to) => set({ ...row, to })} />
        </td>
        <td data-label="Gross a month" className={`${td} ${CARD_CELL} max-xl:order-3`}>
          <MoneyInput label={`Gross a month (S$), ${n}`} before="S$" value={row.gross} placeholder="0"
            invalid={!!errors[`income.${i}.gross`]} describedBy={errors[`income.${i}.gross`] ? note : undefined} onChange={(v) => set({ ...row, gross: v })} />
        </td>
        <td data-label="Pays CPF" className={`${td} text-center max-xl:order-7 max-xl:col-span-2 max-xl:flex max-xl:items-center max-xl:gap-2 max-xl:p-0 max-xl:text-left max-xl:before:text-[12px] max-xl:before:font-medium max-xl:before:text-ink-2 max-xl:before:content-[attr(data-label)]`}>
          <input type="checkbox" aria-label={`Pays CPF, ${n}`} checked={row.cpf} className="size-5 xl:size-4"
            onChange={(e) => set({ ...row, cpf: e.target.checked })} />
        </td>
        <td data-label={employment ? "Bonus" : ""} className={`${td} ${CARD_CELL} max-xl:order-6 max-xl:col-span-2 ${employment ? "" : "max-xl:hidden"}`}>
          {employment && (
            <div className="flex gap-1">
              <MoneyInput label={`Bonus (months of pay), ${n}`} value={row.bonus_months} placeholder="Months" group={false}
                invalid={!!errors[`income.${i}.bonus_months`]} describedBy={errors[`income.${i}.bonus_months`] ? note : undefined} className="w-20 shrink-0 max-xl:w-0 max-xl:flex-1"
                onChange={(v) => set({ ...row, bonus_months: v })} />
              <Select label={`Bonus month, ${n}`} placeholder="Paid in" value={row.bonus_month} className="min-w-0 flex-1"
                invalid={bad("bonus_month")} onChange={(v) => set({ ...row, bonus_month: v })}
                options={[{ value: "", label: "Paid in" }, ...MONTHS.map((m, k) => ({ value: String(k + 1), label: m }))]} />
            </div>
          )}
        </td>
        <td className={`${td} w-8 max-xl:absolute max-xl:right-1.5 max-xl:top-1.5 max-xl:p-0`}>
          <RemoveButton label={`Remove ${n}`} onClick={remove} />
        </td>
      </tr>
      <tr className="max-xl:block">
        <td colSpan={8} className="px-1 pb-1 max-xl:block max-xl:p-0">
          {chip && <div className="pb-1">{chip}</div>}
          <Message id={note} text={message} />
        </td>
      </tr>
    </>
  );
}

/** A detected pay as a row: the typical credit, the first credit's date, the bonus a yearly spike shows. */
const rowFrom = (s: IncomeSuggestion): IncomeDraft => ({
  name: s.name, type: s.type, from: s.from, to: "", gross: dollarsText(s.gross), cpf: s.cpf,
  bonus_months: s.bonus_months ? String(s.bonus_months) : "", bonus_month: s.bonus_month ? String(s.bonus_month) : "",
});

/** What the record says about a row's gross: nothing when it is close, or it matches no detected pay. */
function grossChip(row: IncomeDraft, s: IncomeSuggestion | null, set: (r: IncomeDraft) => void) {
  const kind = s && coversLast(row, s) ? chipKind(typedValue(row.gross), s.gross) : null;
  if (!s || !kind) return null;
  const what = s.basis === "gross" ? `gross (${formatSgd(s.take_home)} take-home)` : s.basis === "take_home" ? "take-home" : "";
  // take-home is not grossed up, so the row must not take CPF off it a second time
  return <SuggestChip onUse={() => set({ ...row, gross: dollarsText(s.gross), cpf: s.basis === "take_home" ? false : row.cpf })}>
    {chipText(kind, s.gross, what)}
  </SuggestChip>;
}

function IncomeTable({ rows, errors, setIncome, found = [] }: {
  rows: IncomeDraft[]; errors: FieldErrors; setIncome: (r: IncomeDraft[]) => void; found?: IncomeSuggestion[];
}) {
  const held = claims(rows, found);
  const offers = found.filter((s) => !held.includes(s));
  return (
    <Section title="Income"
      action={<AddButton onClick={() => setIncome([...rows, blankRow(rows)])}>Add income</AddButton>}>
      {offers.length > 0 && (
        <div className="flex flex-wrap gap-1.5" role="group" aria-label="Pay seen in your statements">
          {offers.map((s) => (
            <SuggestChip key={`${s.type}:${s.name}`} onUse={() => setIncome([...rows, rowFrom(s)])}>
              {incomeText(s, formatMonth(s.from))}
            </SuggestChip>
          ))}
        </div>
      )}
      {rows.length === 0
        ? <EmptyState title="No income yet">So nothing comes in from work.</EmptyState>
        : (
          <table className="w-full border-collapse max-xl:block xl:table-fixed">
            <colgroup className="max-xl:hidden">
              <col /><col className="w-32" /><col className="w-36" /><col className="w-36" /><col className="w-28" />
              <col className="w-12" /><col className="w-44" /><col className="w-10" />
            </colgroup>
            <thead className="max-xl:hidden">
              <tr className={HEAD_ROW}>
                <th className={th}>Name</th><th className={th}>Type</th><th className={th}>From</th><th className={th}>To</th>
                <th className={th}>Gross a month</th><th className={`${th} text-center`}>CPF</th><th className={th}>Bonus</th>
                <th className={th}><span className="sr-only">Remove</span></th>
              </tr>
            </thead>
            <tbody className="max-xl:flex max-xl:flex-col">
              {rows.map((r, i) => (
                <IncomeRowEditor key={i} row={r} i={i} errors={errors}
                  chip={grossChip(r, held[i], (row) => setIncome(rows.map((x, j) => (j === i ? row : x))))}
                  set={(row) => setIncome(rows.map((x, j) => (j === i ? row : x)))}
                  remove={() => setIncome(rows.filter((_, j) => j !== i))} />
              ))}
            </tbody>
          </table>
        )}
    </Section>
  );
}

// ------------------------------------------------------------ investing

function StepEditor({ step, i, first, errors, set, remove, chip }: {
  step: StepDraft; i: number; first: boolean; errors: FieldErrors; set: (s: StepDraft) => void; remove: () => void;
  /** A suggestion for this step's amount, when the record has one (G13). */
  chip?: React.ReactNode;
}) {
  const note = useId();
  const message = firstOf(errors, `investing.${i}.`);
  return (
    <div className="flex flex-col gap-1 max-md:relative max-md:rounded-lg max-md:border max-md:border-line max-md:p-3" role="group" aria-label={`Investing step ${i + 1}`}>
      <div className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto] items-end gap-2 max-md:grid-cols-2">
        <Field label={first ? "From" : "Then from"} className="max-md:col-span-2 max-md:pr-8">
          {(id) => <MonthField id={id} label={`From, investing step ${i + 1}`} value={step.from} clearable
            placeholder="From the start" onChange={(from) => set({ ...step, from })} />}
        </Field>
        <Field label="A month">
          {(id) => <MoneyInput id={id} label={`Amount a month (S$), investing step ${i + 1}`} before="S$" value={step.amount}
            placeholder="0" invalid={!!errors[`investing.${i}.amount`]} describedBy={errors[`investing.${i}.amount`] ? note : undefined} onChange={(v) => set({ ...step, amount: v })} />}
        </Field>
        <div className="max-md:absolute max-md:right-2 max-md:top-2">
          <RemoveButton label={`Remove investing step ${i + 1}`} onClick={remove} />
        </div>
      </div>
      {chip && <div>{chip}</div>}
      <Message id={note} text={message} />
    </div>
  );
}

// ------------------------------------------------------------ the editor

/** Every input on one form: income across the top, then investing, spending and the assumptions side by side. */
export function InputsEditor({ draft, onChange, hint, inflation, errors = {}, broker = null, suggestions = null }: {
  draft: Draft; onChange: (d: Draft) => void; hint: SpendHint | null; inflation: string;
  /** What is wrong, by box (`placeErrors`). */
  errors?: FieldErrors;
  /** The brokerage's name from the loaded statement; none until one is loaded. */
  broker?: string | null;
  /** G13: what the record already knows, shown as chips; a box is filled only when its chip is tapped. */
  suggestions?: Suggestions | null;
}) {
  const setIncome = (income: IncomeDraft[]) => onChange({ ...draft, income });
  const setSteps = (investing: StepDraft[]) => onChange({ ...draft, investing });
  const setP = (k: keyof ProjectionDraft, v: string) => onChange({ ...draft, projection: { ...draft.projection, [k]: v } });
  const p = draft.projection;
  const spendNote = useId();
  const assumeNote = useId();
  const rate = (k: Rate, label: string, placeholder?: string) => (
    <Field label={label}>
      {(id) => <MoneyInput id={id} label={label} after="% a year" value={p[k]} placeholder={placeholder} group={false}
        invalid={!!errors[`p.${k}`]} describedBy={errors[`p.${k}`] ? (k === "spend_growth_pct" ? spendNote : assumeNote) : undefined} onChange={(v) => setP(k, v)} />}
    </Field>
  );

  // the step in force is the last one; an older step is history the record's recent deposits say nothing about
  const deposits = suggestions?.investing ?? null;
  const lastStep = draft.investing.length - 1;
  const stepChip = (s: StepDraft, i: number) => {
    const kind = deposits && i === lastStep ? chipKind(typedValue(s.amount), deposits.per_month) : null;
    return kind && deposits ? (
      <SuggestChip onUse={() => setSteps(draft.investing.map((x, j) => (j === i ? { ...x, amount: dollarsText(deposits.per_month) } : x)))}>
        {chipText(kind, deposits.per_month)}
      </SuggestChip>
    ) : null;
  };
  const spendKind = hint ? chipKind(typedValue(p.spend), hint.per_month) : null;

  return (
    <div className="flex flex-col gap-4">
      <IncomeTable rows={draft.income} errors={errors} setIncome={setIncome} found={suggestions?.income} />

      <div className="grid gap-5 lg:grid-cols-2">
        <div className="flex min-w-0 flex-col gap-4">
        <Section title={broker ? `Investing into ${broker}` : "Investing"}
          action={<AddButton onClick={() => setSteps([...draft.investing, { from: draft.investing.length ? isoMonth() : "", amount: "" }])}>Add step</AddButton>}>
          {draft.investing.length === 0
            ? (
              <>
                <EmptyState title="No investing transfer" />
                {deposits && (
                  <div>
                    <SuggestChip onUse={() => setSteps([{ from: "", amount: dollarsText(deposits.per_month) }])}>
                      {chipText("seen", deposits.per_month).replace(/, use$/, ", add as a step")}
                    </SuggestChip>
                  </div>
                )}
              </>
            )
            : (
              <div className="flex flex-col gap-1">
                {draft.investing.map((s, i) => (
                  <StepEditor key={i} step={s} i={i} first={i === 0} errors={errors} chip={stepChip(s, i)}
                    set={(step) => setSteps(draft.investing.map((x, j) => (j === i ? step : x)))}
                    remove={() => setSteps(draft.investing.filter((_, j) => j !== i))} />
                ))}
              </div>
            )}
        </Section>

        <Section title="Spending">
          <div className="grid grid-cols-2 gap-2">
            <Field label="A month">
              {(id) => <MoneyInput id={id} label="Spending a month (S$)" before="S$" value={p.spend}
                placeholder={hint ? dollarsText(hint.per_month) : "0"} invalid={!!errors["p.spend"]} describedBy={errors["p.spend"] ? spendNote : undefined}
                onChange={(v) => setP("spend", v)} />}
            </Field>
            {rate("spend_growth_pct", "Growth", inflation)}
          </div>
          {hint && spendKind && (
            <div>
              <SuggestChip onUse={() => setP("spend", dollarsText(hint.per_month))}>{chipText(spendKind, hint.per_month, hint.months > 1 ? `over the last ${hint.months} months` : "")}</SuggestChip>
            </div>
          )}
          <Message id={spendNote} text={firstOf(errors, "p.spend")} />
        </Section>
        </div>

        <Section title="Assumptions">
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
            {rate("pessimistic_pct", "Pessimistic return")}
            {rate("expected_pct", "Expected return")}
            {rate("optimistic_pct", "Optimistic return")}
            {rate("inflation_pct", "Inflation")}
            {rate("salary_growth_pct", "Salary growth")}
            {rate("sums_growth_pct", "FRS and BHS growth")}
            <Field label="Milestone">
              {(id) => <MoneyInput id={id} label="Milestone (S$)" before="S$" value={p.milestone} placeholder="None"
                invalid={!!errors["p.milestone"]} describedBy={errors["p.milestone"] ? assumeNote : undefined} onChange={(v) => setP("milestone", v)} />}
            </Field>
          </div>
          <Message id={assumeNote} text={firstOf(errors, "p.milestone", ...RATES.filter((k) => k !== "spend_growth_pct").map((k) => `p.${k}`))} />
        </Section>
      </div>
    </div>
  );
}

/** "Oct 2026" for a month the server named, or the word for none. */
export const monthOrNone = (m: string | null | undefined, none: string) => (m ? formatMonth(m) : none);
