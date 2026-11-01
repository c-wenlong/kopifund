// G8: the Net worth page. A line of the owner's saved projection inputs, with
// Edit opening them in a dialog (G18; G14 before it), then what they project,
// net worth over time, cash and runway, the brokerage and CPF drawn forward,
// and the Singapore references. Every figure is computed by the server
// (analytics/growth.py); this only draws, and edits the inputs.
import { useEffect, useRef, useState } from "react";
import { Pencil } from "lucide-react";
import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  LineChart,
  ReferenceArea,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  postJSON, useApi, watchStatsRebuild, type CpfRow, type Growth, type NetWorth as NetWorthT, type NetWorthMonth,
  type NetWorthPart, type Suggestions, type TigerRow, type TotalPart, type TotalRow,
} from "../api";
import { Button } from "../components/Button";
import { useCopilotContext } from "../copilot/pageFilters";
import { Card } from "../components/Card";
import { EmptyState } from "../components/EmptyState";
import { SaveBar } from "../components/SaveBar";
import { Segmented } from "../components/Segmented";
import { Sheet } from "../components/Sheet";
import { Tile, TileGrid, type Tone } from "../components/Tile";
import { showToast } from "../components/Toast";
import {
  activeDot, GRID, H_MAIN, H_SMALL, KeyItem, NONE_HIDDEN, SeriesLegend, Swatch, TipBox, TipRow, useHidden, X_AXIS, Y_AXIS,
} from "../components/chartKit";
import { Loading, Problem } from "../components/States";
import { useChartView, ViewSwitch, viewsOf, type ChartView } from "../components/ViewSwitch";
import { formatSgd, parseCents, type Cents } from "../money";
import { onLinkClick, useUnsavedGuard } from "../router";
import { formatDate, formatDay, formatMonth, formatRange } from "../dates";
import { OneStat } from "./StatsPage";
import { InputsEditor, placeErrors, sameInputs, toBody, toDraft, type Draft } from "./ProjectionInputs";

const PARTS: Record<NetWorthPart, { label: string; colour: string }> = {
  cash: { label: "Cash", colour: "var(--c1)" },
  investments: { label: "Investments", colour: "var(--c2)" },
  cpf: { label: "CPF", colour: "var(--c3)" },
};
const RANGES = [
  { value: "1y", text: "1Y", months: 12 },
  { value: "3y", text: "3Y", months: 36 },
  { value: "all", text: "All", months: 0 },
] as const;
type Range = (typeof RANGES)[number]["value"];


// G18: one line of the saved inputs leads, with Edit opening them in a dialog,
// so the projection itself sits at the top of the page; the history follows.
export function NetWorth() {
  const pr = useProjection();
  const [editing, setEditing] = useState(false);
  return (
    <div className="flex flex-col gap-3">
      <InputsStrip pr={pr} onEdit={() => setEditing(true)} />
      <InputsSheet pr={pr} open={editing} onClose={() => setEditing(false)} />
      <ProjectionCard pr={pr} />
      <NetWorthCard />
      <OneStat id="savings-runway" />
      {pr.shown && <PartsCard g={pr.shown} />}
      <OneStat id="benchmarks-sg" />
    </div>
  );
}

// ------------------------------------------------------------ net worth over time

const WORTH_VIEWS = viewsOf("stacked", "lines", "total");
const WORTH_KEYS = ["stacked", "lines", "total"] as const;

function NetWorthCard() {
  const { data, error, loading } = useApi<NetWorthT>("/api/net-worth");
  const [range, setRange] = useState<Range>("all");
  const [view, setView] = useChartView<ChartView>("net-worth", WORTH_KEYS, "stacked");
  const months = RANGES.find((r) => r.value === range)!.months;
  const rows = data ? (months ? data.months.slice(-months) : data.months) : [];
  // G22: the months the chart shows, for a question asked beside it
  useCopilotContext({ from: rows[0]?.month, to: rows.at(-1)?.month });
  return (
    <Card title="Net worth so far" actions={data?.months.length ? (
      <div className="flex flex-wrap items-center justify-end gap-2">
        <ViewSwitch label="Chart view" value={view} options={WORTH_VIEWS} onChange={setView} />
        <Segmented label="Range" value={range} options={RANGES} onChange={setRange} />
      </div>
    ) : null}>
      {loading && <Loading what="Loading net worth" />}
      {error && <Problem message={error} retry />}
      {data && data.months.length === 0 && <p className="m-0 text-[14px] text-ink-2">Net worth appears once a statement is loaded.</p>}
      {data && rows.length > 0 && <NetWorthChart parts={data.parts} rows={rows} view={view} />}
    </Card>
  );
}

const TOTAL_INK = "var(--ink)";

/** Stacked: the parts piled up. Lines: each part on its own scale. Total: the one line. */
function NetWorthChart({ parts, rows, view }: { parts: NetWorthPart[]; rows: NetWorthMonth[]; view: ChartView }) {
  const { hidden, toggle } = useHidden(parts);
  const last = rows[rows.length - 1];
  const shown = parts.filter((p) => !hidden.has(p));
  // what the stack or the lines add up to, once a part is switched off
  const data = rows.map((r) => ({ ...r, shown: shown.reduce((a, p) => a + (r[p] ?? 0), 0) }));
  const total = view === "total";
  return (
    <div role="img" aria-label={`Net worth by month, ${formatSgd(last.total)} in ${formatMonth(last.month)}`}>
      {total
        ? <SeriesLegend items={[]} hidden={hidden} onToggle={() => {}} extra={<KeyItem><Swatch colour={TOTAL_INK} line />Net worth</KeyItem>} />
        : <SeriesLegend items={parts.map((p) => ({ key: p, label: PARTS[p].label, colour: PARTS[p].colour, line: view === "lines" }))}
          hidden={hidden} onToggle={toggle} />}
      <ResponsiveContainer width="100%" height={H_MAIN}>
        <ComposedChart data={data} stackOffset="sign" margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid {...GRID} />
          <XAxis dataKey="month" {...X_AXIS} />
          <YAxis {...Y_AXIS} domain={view === "stacked" ? undefined : ["auto", "auto"]} />
          <Tooltip cursor={{ stroke: "var(--ink-3)", strokeWidth: 1 }} content={({ active, payload }) => {
            const row = active ? (payload?.[0]?.payload as (NetWorthMonth & { shown: Cents }) | undefined) : undefined;
            if (!row) return null;
            const list = view === "lines" ? [...shown].sort((a, b) => (row[b] ?? 0) - (row[a] ?? 0)) : [...(total ? parts : shown)].reverse();
            return (
              <TipBox heading={formatMonth(row.month)} total={total ? row.total : row.shown}>
                {list.map((p) => (
                  <TipRow key={p} colour={PARTS[p].colour} line={view === "lines"} name={PARTS[p].label} value={row[p] ?? 0}
                    note={row[`${p}_as_of`] ? `as at ${formatDate(row[`${p}_as_of`])}` : undefined} />
                ))}
                {!total && shown.length < parts.length && <div className="mt-1 text-[11px] text-ink-3">Parts showing only</div>}
              </TipBox>
            );
          }} />
          <ReferenceLine y={0} stroke="var(--ink-3)" strokeOpacity={0.5} />
          {view === "stacked" && shown.map((p) => (
            <Area key={p} dataKey={p} name={PARTS[p].label} stackId="worth" type="linear" stroke={PARTS[p].colour} strokeWidth={1.5}
              fill={PARTS[p].colour} fillOpacity={0.3} isAnimationActive={false} activeDot={activeDot(PARTS[p].colour)} />
          ))}
          {view === "lines" && shown.map((p) => (
            <Line key={p} dataKey={p} name={PARTS[p].label} type="linear" stroke={PARTS[p].colour} strokeWidth={2} dot={false}
              activeDot={activeDot(PARTS[p].colour)} isAnimationActive={false} />
          ))}
          {total && (
            <Line dataKey="total" name="Net worth" type="linear" stroke={TOTAL_INK} strokeWidth={2} dot={false}
              activeDot={activeDot(TOTAL_INK)} isAnimationActive={false} />
          )}
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}

// ------------------------------------------------------------ growth

const HORIZONS = [
  { value: "10y", text: "10Y" },
  { value: "20y", text: "20Y" },
  { value: "30y", text: "30Y" },
  { value: "65", text: "To 65" },
] as const;
const DOLLARS = [
  { value: "today", text: "Today's dollars" },
  { value: "future", text: "Future dollars" },
] as const;
const TOTAL: Record<TotalPart, { label: string; colour: string }> = {
  cash: { label: "Cash", colour: "var(--c1)" },
  tiger: { label: "Investments", colour: "var(--c2)" },
  cpf: { label: "CPF", colour: "var(--c3)" },
};
// The brokerage is named only from the statement the record holds, so a page
// never carries a broker's name of its own: "Investments" until one is loaded.
const brokerOf = (g: Growth) => g.tiger?.broker ?? null;
const totalLabel = (g: Growth, k: TotalPart) => (k === "tiger" ? brokerOf(g) ?? TOTAL.tiger.label : TOTAL[k].label);
const PREVIEW_WAIT = 300;      // ms after the last keystroke before the preview is asked for

// G14: typing previews the projection with the unsaved values
// (POST /api/growth/preview, nothing written); Save stores them in the plan,
// Discard puts back what is saved. One hook owns this so the strip, the dialog
// and the cards that draw the answer stay in step.
type Projection = ReturnType<typeof useProjection>;

function useProjection() {
  const { data, error } = useApi<Growth>("/api/growth");
  // the answer to the last save, until the reload every write sets off arrives
  const [fresh, setFresh] = useState<Growth | null>(null);
  useEffect(() => setFresh(null), [data]);
  const saved = fresh ?? data;
  const [draft, setDraft] = useState<Draft | null>(null);
  // G18: unsaved means typed on this page. A save from another tab moves `base`
  // under a draft nobody touched; that draft follows it and stays clean.
  const [edited, setEdited] = useState(false);
  const base = saved ? toDraft(saved.inputs) : null;
  const dirty = edited && !!(draft && base && !sameInputs(draft, base));
  // G18: leaving the page with inputs typed and not saved asks first
  useUnsavedGuard(dirty);
  // a reload while nothing is typed shows the newly saved inputs; typing is never thrown away
  const typing = useRef(false);
  typing.current = edited;
  const latest = useRef(draft);
  latest.current = draft;
  // a save on its way: what is typed meanwhile is an edit, even back to the old saved value,
  // since the saved value is about to change under it
  const inFlight = useRef(false);
  useEffect(() => { if (saved && !typing.current) setDraft(toDraft(saved.inputs)); }, [saved]);

  const [preview, setPreview] = useState<Growth | null>(null);
  const [refused, setRefused] = useState<string | null>(null);
  // why the last save from the dialog failed, kept beside its Save until the next edit
  const [failed, setFailed] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  // with no save on its way, a draft that matches what is saved (another tab saved the same) is no longer an edit
  if (edited && !saving && draft && base && sameInputs(draft, base)) setEdited(false);
  const asked = useRef(0);
  const body = draft ? JSON.stringify(toBody(draft)) : "";
  useEffect(() => {
    const n = ++asked.current;
    if (!dirty) {
      setPreview(null);
      setRefused(null);
      return;
    }
    const t = setTimeout(() => {
      postJSON<Growth>("/api/growth/preview", body)
        .then((g) => { if (n === asked.current) { setPreview(g); setRefused(null); } })
        .catch((e: Error) => { if (n === asked.current) setRefused(e.message); });
    }, PREVIEW_WAIT);
    return () => clearTimeout(t);
  }, [body, dirty]);

  // a refused preview keeps the last good projection on screen beside the reason
  const last = useRef<Growth | null>(null);
  const shown = (dirty ? preview ?? last.current : saved) ?? null;
  if (shown) last.current = shown;

  // G18: one save at a time, the dialog's or a switch's; `saving` turns the
  // switches and the dialog's Save off, and this holds a second press in the same tick
  const busy = useRef(false);
  // the switch whose save is on its way, shown pressed until the answer comes
  const [switching, setSwitching] = useState<{ k: Setting; v: string } | null>(null);

  /** Saves what is typed and says whether everything typed is now saved; a failure stays beside Save. */
  async function save(): Promise<boolean> {
    const d = draft;
    if (!d || busy.current) return false;
    busy.current = true;
    setSaving(true);
    inFlight.current = true;
    const sent = JSON.stringify(toBody(d));
    try {
      const g = await postJSON<Growth>("/api/growth/inputs", sent);
      setFresh(g);
      // what was typed while the save was on its way stays, still unsaved
      const caughtUp = !!latest.current && JSON.stringify(toBody(latest.current)) === sent;
      if (caughtUp) {
        setDraft(toDraft(g.inputs));
        setEdited(false);
      }
      // G13: the save reruns the stats in the background; the forward view on Plan redraws once they are done
      watchStatsRebuild();
      showToast({ message: "Saved. The forward view on Plan updates in a moment." });
      return caughtUp;
    } catch (e) {
      setFailed((e as Error).message);
      return false;
    } finally {
      busy.current = false;
      inFlight.current = false;
      setSaving(false);
    }
  }
  /**
   * G18: the horizon and dollars switches sit on the page, outside the dialog
   * and its SaveBar, so a press there saves at once: the saved inputs plus that
   * one change, never the draft. Once saved, the change is carried into
   * whatever is being typed in the dialog, so its Save cannot put the old value
   * back; a refusal is a toast, and the switch shows the saved value again.
   */
  async function saveSetting(k: Setting, v: string) {
    if (!saved || busy.current) return;
    busy.current = true;
    const from = toDraft(saved.inputs);
    const sent = JSON.stringify(toBody({ ...from, projection: { ...from.projection, [k]: v } }));
    setSwitching({ k, v });
    setSaving(true);
    try {
      const g = await postJSON<Growth>("/api/growth/inputs", sent);
      setFresh(g);
      setDraft((cur) => (cur ? { ...cur, projection: { ...cur.projection, [k]: v } } : cur));
      watchStatsRebuild();
      showToast({ message: "Saved. The forward view on Plan updates in a moment." });
    } catch (e) {
      showToast({ tone: "error", message: (e as Error).message });
    } finally {
      busy.current = false;
      setSwitching(null);
      setSaving(false);
    }
  }
  function discard() {
    if (base) setDraft(base);
    setEdited(false);
    setFailed(null);
  }
  // typing a value back to the saved one leaves nothing unsaved, unless a save is on its way
  const edit = (d: Draft) => { setDraft(d); setEdited(inFlight.current || !base || !sameInputs(d, base)); setFailed(null); };
  return { error, saved, base, draft, dirty, shown, refused, failed, saving, switching, save, saveSetting, discard, edit };
}

type Setting = "horizon" | "dollars";

const HORIZON_WORDS: Record<Draft["projection"]["horizon"], string> = { "10y": "10 years", "20y": "20 years", "30y": "30 years", "65": "To 65" };

/**
 * What the saved inputs come to today: the pay of the income rows running on
 * `today`, the investing step in force this month, spending (the record's
 * average when the box is empty), the expected return and the horizon.
 */
export function summarise(d: Draft, g: Growth) {
  const day = g.today;
  const month = day.slice(0, 7);
  const amount = (s: string) => {
    const c = parseCents(s);
    return c == null || Number.isNaN(c) ? 0 : c;
  };
  const running = d.income.filter((r) => r.from <= day && (!r.to || r.to >= day));
  // steps run in the order they start; one with no month runs from the start
  const step = d.investing
    .filter((s) => !s.from || s.from <= month)
    .sort((x, y) => (x.from || "").localeCompare(y.from || ""))
    .at(-1);
  const typedSpend = d.projection.spend.trim() !== "";
  return {
    pay: running.length ? running.reduce((sum, r) => sum + amount(r.gross), 0) : null,
    investing: step ? amount(step.amount) : null,
    spending: typedSpend ? amount(d.projection.spend) : g.assumptions.spend,
    spendingIsAverage: !typedSpend,
    expected: d.projection.expected_pct.trim() || g.assumptions.expected_pct,
    horizon: HORIZON_WORDS[d.projection.horizon] ?? d.projection.horizon,
  };
}

function Fact({ label, value, unit, className = "" }: { label: string; value: string; unit?: string; className?: string }) {
  return (
    <dl className={`m-0 flex items-baseline gap-1.5 whitespace-nowrap ${className}`}>
      <dt className="text-ink-2">{label}</dt>
      <dd className="num m-0 font-medium text-ink">
        {value}
        {/* the unit is dropped on a phone, so the line wraps to two at most */}
        {unit && <span className="font-normal text-ink-3 max-sm:hidden"> {unit}</span>}
      </dd>
    </dl>
  );
}

/** G18: the saved inputs on one line at the top of the page, and Edit, which opens them. */
function InputsStrip({ pr, onEdit }: { pr: Projection; onEdit: () => void }) {
  const { saved: g, base, error } = pr;
  if (!g || !base) {
    return error ? <Problem message={error} retry /> : <Loading what="Loading your inputs" />;
  }
  const f = summarise(base, g);
  const money = (c: Cents | null) => (c == null ? "None" : formatSgd(c));
  // On a phone the figures are 12px and Return comes before Spending, so the
  // line breaks once: pay, investing and return, then spending, horizon and Edit.
  return (
    <section aria-label="Your projection inputs"
      className="flex flex-wrap items-center gap-x-4 gap-y-1 rounded-xl border border-line bg-panel py-2 pr-2 pl-4 text-[13px] max-sm:gap-x-3 max-sm:pl-3 max-sm:text-[12px]">
      <Fact label="Pay" value={money(f.pay)} unit={f.pay == null ? undefined : "a month"} />
      <Fact label="Investing" value={money(f.investing)} unit={f.investing == null ? undefined : "a month"} />
      <Fact label="Spending" value={formatSgd(f.spending)} unit={f.spendingIsAverage ? "a month, your average" : "a month"}
        className="max-sm:order-1" />
      <Fact label="Return" value={`${f.expected}%`} unit="a year" />
      <Fact label="Horizon" value={f.horizon} className="max-sm:order-1" />
      <Button onClick={onEdit} className="order-1 ml-auto">
        <Pencil size={13} aria-hidden /> Edit
      </Button>
    </section>
  );
}

/**
 * G18: the inputs in a dialog (a sheet on a phone). The tiles ride at its top
 * so the preview moves as the owner types; the SaveBar sits in its foot with
 * whatever went wrong beside it. Save closes it once everything typed is
 * saved; closing with edits asks first, and Leave drops them.
 */
function InputsSheet({ pr, open, onClose }: { pr: Projection; open: boolean; onClose: () => void }) {
  const { draft, shown: g, refused, failed, saving, dirty } = pr;
  // G13: what the record already knows; a failed ask just means no chips
  const { data: suggestions } = useApi<Suggestions>("/api/suggestions");
  // a refusal that names a box goes under that box; only one that names none goes beside Save
  const placed = draft ? placeErrors(draft, refused) : null;
  const problem = placed?.rest ?? failed;
  async function saveAndClose() {
    if (await pr.save()) onClose();
  }
  return (
    <Sheet open={open && !!g && !!draft} title="Your projection inputs" wide dirty={dirty}
      // a save on its way finishes before the dialog can go
      onRequestClose={() => !saving}
      onClose={() => { pr.discard(); onClose(); }}
      footer={
        <div className="flex flex-wrap items-center justify-end gap-x-3 gap-y-2">
          {problem && <p role="alert" className="m-0 mr-auto min-w-0 text-[13px] text-warn">{problem}</p>}
          <SaveBar dirty={dirty} saving={saving} onSave={saveAndClose} onDiscard={pr.discard} />
        </div>
      }>
      {g && draft && placed && (
        <div className="flex flex-col gap-4">
          {/* from a wide screen the tiles stay in view while the form scrolls under them */}
          <div className="lg:sticky lg:-top-3 lg:z-10 lg:-mx-4 lg:-mt-3 lg:border-b lg:border-line lg:bg-panel lg:px-4 lg:py-3">
            <Tiles g={g} />
          </div>
          <InputsEditor draft={draft} onChange={pr.edit} hint={g.spend_hint} errors={placed.fields}
            broker={g.tiger?.broker ?? null} suggestions={suggestions} inflation={draft.projection.inflation_pct || g.assumptions.inflation_pct} />
        </div>
      )}
    </Sheet>
  );
}

/** The answer to the saved inputs, or to the ones being typed: the tiles and the total. */
function ProjectionCard({ pr }: { pr: Projection }) {
  const { draft, shown: g } = pr;
  const [view, setView] = useChartView<ChartView>("net-worth-projected", WORTH_KEYS, "stacked");
  if (!g || !draft) return null;
  const p = draft.projection;
  const set = (k: Setting, v: string) => void pr.saveSetting(k, v);
  const value = <K extends Setting>(k: K) => (pr.switching?.k === k ? pr.switching.v : p[k]) as Draft["projection"][K];
  // G18: off while any save is on its way, so a second press cannot be sent from the inputs the first is replacing
  const horizonOptions = HORIZONS.map((h) => ({ ...h, disabled: pr.saving || (h.value === "65" && !g.can_reach_65) }));
  const dollarOptions = DOLLARS.map((d) => ({ ...d, disabled: pr.saving }));
  return (
    <Card title="Net worth projected" actions={
      <div className="flex flex-wrap items-center justify-end gap-2">
        <ViewSwitch label="Chart view" value={view} options={WORTH_VIEWS} onChange={setView} />
        <Segmented label="Horizon" value={value("horizon")} options={horizonOptions} onChange={(v) => set("horizon", v)} />
        <Segmented label="Dollars" value={value("dollars")} options={dollarOptions} onChange={(v) => set("dollars", v)} />
      </div>
    }>
      <div className="flex flex-col gap-4">
        <Tiles g={g} />
        <section aria-label="Net worth projected">
          <TotalChart g={g} view={view} />
        </section>
      </div>
    </Card>
  );
}

/** The brokerage and CPF each drawn forward on their own, below the history. */
function PartsCard({ g }: { g: Growth }) {
  const broker = brokerOf(g);
  const held = broker ? `Investments at ${broker}` : "Investments";
  return (
    <Card title={`${broker ?? "Investments"} and CPF projected`}>
      <div className="flex flex-col gap-4">
        <section aria-label={held}>
          <h3 className="m-0 mb-1 text-[13px] font-semibold text-ink-2">{held}</h3>
          {g.tiger && !g.tiger.measured && (
            <p className="m-0 mb-1 text-[12px] text-ink-3">No brokerage statement yet, so this starts from nothing and holds your investing steps.</p>
          )}
          {g.tiger?.rows.length ? <TigerChart g={g} rows={g.tiger.rows} />
            : <EmptyState title="No brokerage statement is loaded yet" />}
        </section>
        <section aria-label="CPF">
          <h3 className="m-0 mb-1 text-[13px] font-semibold text-ink-2">CPF</h3>
          {!g.cpf ? <EmptyState title="No CPF statement is loaded yet" />
            : g.cpf.needs === "birth_date" ? (
              <p className="m-0 text-[13px] text-ink-2">
                Add your date of birth in <a href="/profile" onClick={onLinkClick("/profile")} className="text-ink underline">Your profile</a> to project CPF.
              </p>
            ) : g.cpf.needs === "before_55"
              ? <p className="m-0 text-[13px] text-ink-2">The latest CPF statement is from 55 on, with a Retirement Account this projection does not read yet.</p>
              : <CpfChart g={g} rows={g.cpf.rows} />}
        </section>
      </div>
    </Card>
  );
}

/** Today, five and ten years on, and the month the milestone is first reached. A projected figure under zero is red. */
function Tiles({ g }: { g: Growth }) {
  const t = g.total.tiles;
  const m = g.total.milestone;
  const money = (x: { total: Cents } | null) => (x ? formatSgd(x.total) : "Past the horizon");
  const tone = (x: { total: Cents } | null): Tone => (x && x.total < 0 ? "danger" : "neutral");
  return (
    <TileGrid cols={4} label="Net worth at a glance">
      <Tile label="Today" value={money(t.today)} tone={tone(t.today)} note={t.today ? formatMonth(t.today.month) : undefined} />
      <Tile label="In 5 years" value={money(t.in_5y)} tone={tone(t.in_5y)} note={t.in_5y ? formatMonth(t.in_5y.month) : undefined} />
      <Tile label="In 10 years" value={money(t.in_10y)} tone={tone(t.in_10y)} note={t.in_10y ? formatMonth(t.in_10y.month) : undefined} />
      {m
        ? <Tile label={`${formatSgd(m.value)} first reached`} value={m.month ? formatMonth(m.month) : "Not reached"}
          note={m.month ? (m.total != null ? formatSgd(m.total) : undefined) : `by ${formatMonth(g.end)}`} />
        : <Tile label="Milestone" value="None set" />}
    </TileGrid>
  );
}

function TotalChart({ g, view }: { g: Growth; view: ChartView }) {
  const parts = g.total.parts;
  const hiddenParts = useHidden(parts);
  const { toggle } = hiddenParts;
  // the total view always draws the whole total, with its band, whatever the other views have hidden
  const hidden = view === "total" ? NONE_HIDDEN : hiddenParts.hidden;
  const shown = parts.filter((k) => !hidden.has(k));
  const none = shown.length === parts.length;
  // the stack's top is the total of the parts showing; the band is the whole total's, so it goes once a part does
  const rows = g.total.rows.map((r) => ({
    ...r, band: [r.low, r.high] as [number, number], shown: none ? r.total : shown.reduce((a, k) => a + (r[k] ?? 0), 0),
  }));
  const below = g.total.below_zero;
  const hasBand = parts.includes("tiger");
  const total = view === "total";
  const under = below.map((r) => (r.from === r.to ? formatMonth(r.from) : formatRange(r.from, r.to))).join(", ");
  const lastRow = rows[rows.length - 1];
  const band = hasBand && none && view !== "lines";
  return (
    <div role="img" aria-label={`Net worth projected, ${lastRow ? `${formatSgd(lastRow.total)} in ${formatMonth(lastRow.month)}` : "nothing to draw"}${under ? `; cash under zero in ${under}` : ""}`}>
      <SeriesLegend
        items={total ? [] : parts.map((k) => ({ key: k, label: totalLabel(g, k), colour: TOTAL[k].colour, line: view === "lines" }))}
        hidden={hidden} onToggle={toggle}
        extra={<>
          {view !== "lines" && <KeyItem><Swatch colour="var(--ink)" line />Total</KeyItem>}
          {band && <KeyItem>
            <span className="size-2 shrink-0 rounded-sm bg-[var(--ink-3)] opacity-30" aria-hidden />{totalLabel(g, "tiger")} at {g.assumptions.pessimistic_pct}% to {g.assumptions.optimistic_pct}%
          </KeyItem>}
        </>} />
      {below.length > 0 && (
        <p className="m-0 mb-1 px-1.5 text-[12px] text-warn">Cash under zero: {under}</p>
      )}
      <ResponsiveContainer width="100%" height={H_MAIN}>
        <ComposedChart data={rows} stackOffset="sign" margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid {...GRID} />
          <XAxis dataKey="month" {...X_AXIS} />
          <YAxis {...Y_AXIS} domain={view === "stacked" ? undefined : ["auto", "auto"]} />
          <Tooltip cursor={{ stroke: "var(--ink-3)", strokeWidth: 1 }} content={({ active, payload }) => {
            const row = active ? (payload?.[0]?.payload as TotalRow | undefined) : undefined;
            if (!row) return null;
            return <TotalTip g={g} row={row} hide={total ? undefined : hidden} lines={view === "lines"} />;
          }} />
          {below.map((r) => (
            // the months cash is under zero: shaded below the zero line, or the whole height when the line drawn is the total
            <ReferenceArea key={r.from} x1={r.from} x2={r.to} y2={total ? undefined : 0} fill="var(--warn)" fillOpacity={total ? 0.1 : 0.15}
              stroke="none" ifOverflow="extendDomain" />
          ))}
          {below.length > 0 && !total && <ReferenceLine y={0} stroke="var(--warn)" strokeDasharray="4 4" />}
          {view === "stacked" && shown.map((k) => (
            <Area key={k} dataKey={k} name={totalLabel(g, k)} stackId="worth" type="linear" stroke={TOTAL[k].colour} strokeWidth={1.5}
              fill={TOTAL[k].colour} fillOpacity={0.3} isAnimationActive={false} activeDot={false} />
          ))}
          {view === "lines" && shown.map((k) => (
            <Line key={k} dataKey={k} name={totalLabel(g, k)} type="linear" stroke={TOTAL[k].colour} strokeWidth={2} dot={false}
              activeDot={activeDot(TOTAL[k].colour)} isAnimationActive={false} />
          ))}
          {band && <Area dataKey="band" stroke="none" fill="var(--ink-3)" fillOpacity={0.12} isAnimationActive={false} activeDot={false} />}
          {view !== "lines" && (
            <Line dataKey={total ? "total" : "shown"} stroke="var(--ink)" strokeWidth={2} dot={false}
              activeDot={activeDot("var(--ink)")} isAnimationActive={false} />
          )}
          {markersIn(g, rows).map(marker)}
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}

/**
 * The hover for a projected month. With parts switched off (`hide`) it lists
 * and adds up the parts showing; `lines` orders them by size, as the lines run.
 */
export function TotalTip({ g, row, hide, lines = false }: { g: Growth; row: TotalRow; hide?: ReadonlySet<string>; lines?: boolean }) {
  const a = g.assumptions;
  const all = !hide || hide.size === 0;
  const entries: { k: TotalPart; name: string; note?: string; value: Cents }[] = [
    { k: "cash", name: "Cash", note: row.cash < 0 ? "under zero" : undefined, value: row.cash },
  ];
  if (row.tiger != null) entries.push({ k: "tiger", name: totalLabel(g, "tiger"), note: `expected ${a.expected_pct}%`, value: row.tiger });
  if (row.cpf != null) entries.push({ k: "cpf", name: "CPF", note: row.cpf_held ? "held from 65" : undefined, value: row.cpf });
  const shown = entries.filter((e) => !hide?.has(e.k));
  if (lines) shown.sort((x, y) => y.value - x.value);
  const total = all ? row.total : shown.reduce((sum, e) => sum + e.value, 0);
  return (
    <TipBox heading={heading(row.month, row.age)} total={total}>
      {shown.map((e) => <TipRow key={e.k} colour={TOTAL[e.k].colour} line={lines} name={e.name} note={e.note} value={e.value} />)}
      {!all && <div className="mt-1 text-[11px] text-ink-3">Parts showing only</div>}
      {all && row.tiger != null && (
        <div className="mt-1 text-[11px] text-ink-3">
          Total {formatSgd(row.low)} to {formatSgd(row.high)} at {a.pessimistic_pct}% to {a.optimistic_pct}%
        </div>
      )}
    </TipBox>
  );
}

function markersIn(g: Growth, rows: { month: string }[]) {
  const months = rows.map((r) => r.month);
  return g.markers
    .map((m) => ({ ...m, at: months.indexOf(m.month) }))
    .filter((m) => m.at >= 0)
    .map((m) => ({ ...m, right: m.at > months.length * 0.85 }));
}

function heading(month: string, age: number | null | undefined) {
  return age != null ? `${formatMonth(month)} · age ${age}` : formatMonth(month);
}

const marker = (m: { age: number; month: string; right: boolean }) => (
  <ReferenceLine key={m.age} x={m.month} stroke="var(--ink-3)" strokeDasharray="2 3"
    label={{ value: `Age ${m.age}`, position: m.right ? "insideTopRight" : "insideTopLeft", fontSize: 11, fill: "var(--ink-3)" }} />
);

function TigerChart({ g, rows }: { g: Growth; rows: TigerRow[] }) {
  const data = rows.map((r) => ({ ...r, band: [r.pessimistic, r.optimistic] }));
  const a = g.assumptions;
  return (
    <div>
      <div className="mb-2 flex flex-wrap gap-x-1 gap-y-0.5 text-[12px] text-ink-2">
        <KeyItem><Swatch colour="var(--c1)" line />Expected {a.expected_pct}%</KeyItem>
        <KeyItem>
          <span className="size-2 shrink-0 rounded-sm bg-[var(--c1)] opacity-30" aria-hidden />{a.pessimistic_pct}% to {a.optimistic_pct}%
        </KeyItem>
        <KeyItem><Swatch colour="var(--ink-3)" dashed />Money you put in</KeyItem>
      </div>
      <ResponsiveContainer width="100%" height={H_SMALL}>
        <ComposedChart data={data} margin={{ top: 14, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid {...GRID} />
          <XAxis dataKey="month" {...X_AXIS} />
          <YAxis {...Y_AXIS} />
          <Tooltip content={({ active, payload }) => {
            const row = active ? (payload?.[0]?.payload as (TigerRow & { age?: number | null }) | undefined) : undefined;
            if (!row) return null;
            return (
              <TipBox heading={heading(row.month, row.age)}>
                <TipRow colour="var(--c1)" name={`Optimistic ${a.optimistic_pct}%`} value={row.optimistic} />
                <TipRow colour="var(--c1)" name={`Expected ${a.expected_pct}%`} value={row.expected} />
                <TipRow colour="var(--c1)" name={`Pessimistic ${a.pessimistic_pct}%`} value={row.pessimistic} />
                <TipRow colour="var(--ink-3)" dashed name="Money you put in" value={row.put_in} />
                {row.month === rows[0].month && g.tiger?.as_of && <div className="mt-1 text-[11px] text-ink-3">Statement value as at {formatDay(g.tiger.as_of)}</div>}
              </TipBox>
            );
          }} />
          <Area dataKey="band" stroke="none" fill="var(--c1)" fillOpacity={0.15} isAnimationActive={false} activeDot={false} />
          <Line dataKey="expected" stroke="var(--c1)" strokeWidth={2} dot={false} isAnimationActive={false} />
          <Line dataKey="put_in" stroke="var(--ink-3)" strokeWidth={1.5} strokeDasharray="4 4" dot={false} isAnimationActive={false} />
          {markersIn(g, rows).map(marker)}
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}

function CpfChart({ g, rows }: { g: Growth; rows: CpfRow[] }) {
  // the cohort FRS rides beside the RA from 55, in the same dollars as the balances
  const showFrs = rows.some((r) => r.frs != null);
  return (
    <div>
      <div className="mb-2 flex flex-wrap gap-x-1 gap-y-0.5 text-[12px] text-ink-2">
        <KeyItem><Swatch colour="var(--c3)" line />All accounts</KeyItem>
        {showFrs && <KeyItem><Swatch colour="var(--c4)" dashed />Full Retirement Sum at 55</KeyItem>}
      </div>
      <ResponsiveContainer width="100%" height={H_SMALL}>
        <LineChart data={rows} margin={{ top: 14, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid {...GRID} />
          <XAxis dataKey="month" {...X_AXIS} />
          <YAxis {...Y_AXIS} />
          <Tooltip content={({ active, payload }) => {
            const row = active ? (payload?.[0]?.payload as CpfRow | undefined) : undefined;
            if (!row) return null;
            return (
              <TipBox heading={heading(row.month, row.age)} total={row.total}>
                <TipRow name="OA" value={row.oa} />
                {row.ra != null
                  ? <TipRow name="RA" value={row.ra} note={row.frs != null ? `FRS ${formatSgd(row.frs)}` : undefined} />
                  : <TipRow name="SA" value={row.sa ?? 0} />}
                <TipRow name="MA" value={row.ma} />
                {row.month === rows[0].month && g.cpf && <div className="mt-1 text-[11px] text-ink-3">Statement balance as at {formatDay(g.cpf.as_of)}</div>}
              </TipBox>
            );
          }} />
          <Line dataKey="total" stroke="var(--c3)" strokeWidth={2} dot={false} isAnimationActive={false} />
          {markersIn(g, rows).map(marker)}
          {showFrs && (
            <Line dataKey="frs" stroke="var(--c4)" strokeWidth={1.5} strokeDasharray="4 4" dot={false} activeDot={false}
              connectNulls={false} isAnimationActive={false} />
          )}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

