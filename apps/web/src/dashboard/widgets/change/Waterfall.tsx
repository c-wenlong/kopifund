// G26: the waterfall change widget. A balance query's first figure opens the
// bridge and its last closes it; every flow series sums over the range to one
// step, inflows first. Each step draws as a floating range bar (Recharts Bar
// with a [low, high] dataKey), up in the good tone and down in danger, in
// integer cents.
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Card } from "../../../components/Card";
import { EmptyState } from "../../../components/EmptyState";
import { Loading, Problem } from "../../../components/States";
import {
  cutName, GRID, KeyItem, Swatch, TipBox, X_AXIS, Y_AXIS,
} from "../../../components/chartKit";
import { navigate } from "../../../router";
import { tickLabel } from "../../../stats/hint";
import { formatSgd, type Cents } from "../../../money";
import type { WidgetRange, WidgetUnit } from "../../../types.gen";
import {
  tickValue, useAnswers, useChartHeight,
  type Frame, type SeriesSpec,
} from "./shared";

const UP = "var(--good)";
const DOWN = "var(--danger)";
const END = "var(--ink)";

/** What a waterfall widget is bound to: the level it runs between, and the flows that bridge it. */
export type WaterfallConfig = {
  /** the widget instance's id */
  id: string;
  title?: string;
  /**
   * the level the bridge starts and ends at (say savings-runway's cash by
   * month): the answer's first series, its first figure opening and its last
   * closing
   */
  balance: SeriesSpec;
  /** each series of these answers sums over the range to one step (a spending split gives several) */
  flows: SeriesSpec[];
  /** the range a query without its own inherits, e.g. a dashboard-level filter */
  range?: WidgetRange;
};

/** An example binding: cash one month, bridged by the month's net per bucket. */
export const DEFAULT_WATERFALL_CONFIG: WaterfallConfig = {
  id: "waterfall",
  title: "Where the money moved",
  range: { from: "2026-08", to: "2026-08" },
  balance: { query: { source: "savings-runway", metric: "cash", dimension: "month" } },
  flows: [{ query: { source: "spending", metric: "net", dimension: "month", split: "bucket" } }],
};

/** One bar of the bridge: an endpoint's level, or a signed change from `before` to `after`. */
export type WaterfallStep = {
  name: string;
  kind: "open" | "in" | "out" | "close";
  /** open/close: the level; in/out: the signed change, integer cents (a negative is a true minus when written) */
  value: Cents;
  before: Cents;
  after: Cents;
  /** the Transactions page the step's rows list on, or null */
  href: string | null;
};

/** A flow step's link: the whole range's rows behind its series, bounded by the series' own answer. */
function stepHref(series: Frame["series"][number]): string | null {
  if (!series.rangeLink) return null;
  return `/transactions?${new URLSearchParams(series.rangeLink)}`;
}

/**
 * The bridge: the balance's first figure opens it and its last closes it;
 * every flow series is one step between, the inflows first so the reader
 * climbs before falling. Steps do not have to reconcile to the balance: the
 * widget draws what its queries answered. Null when the balance answered no
 * figure at all: a chart then would read missing cash data as zero balances.
 */
export function waterfallSteps(balance: Frame | null, flows: Frame | null): WaterfallStep[] | null {
  const level = balance?.series[0];
  const vals = level?.values ?? [];
  const first = vals.findIndex((v) => v != null);
  if (first < 0) return null;
  const last = vals.length - 1 - [...vals].reverse().findIndex((v) => v != null);
  const opening = vals[first]!;
  const closing = vals[last]!;
  const steps: WaterfallStep[] = [{
    name: "Opening", kind: "open", value: opening, before: 0, after: opening,
    href: level?.href[first] ?? null,
  }];
  const deltas = (flows?.series ?? []).map((s) => ({
    name: s.name, value: s.values.reduce<Cents>((a, v) => a + (v ?? 0), 0), href: stepHref(s),
  }));
  let run = opening;
  for (const d of [...deltas.filter((x) => x.value > 0), ...deltas.filter((x) => x.value <= 0)]) {
    steps.push({ name: d.name, kind: d.value >= 0 ? "in" : "out", value: d.value, before: run, after: run + d.value, href: d.href });
    run += d.value;
  }
  steps.push({
    name: "Closing", kind: "close", value: closing, before: run, after: closing,
    href: level?.href[last] ?? null,
  });
  return steps;
}

export function WaterfallWidget({ config = DEFAULT_WATERFALL_CONFIG }: { config?: WaterfallConfig }) {
  const balance = useAnswers([config.balance], config.range);
  const flows = useAnswers(config.flows, config.range);
  const title = config.title ?? "Waterfall";
  const loading = balance.loading || flows.loading;
  const error = balance.error ?? flows.error;
  const steps = !loading && !error && balance.frame && flows.frame
    ? waterfallSteps(balance.frame, flows.frame)
    : undefined;
  // a projection or preview answer marks its figures as estimates; show every note either frame carries
  const notes = [...new Set([balance.frame?.note, flows.frame?.note].filter((n): n is string => !!n))];
  return (
    <Card title={title}>
      {loading && <Loading what={`Loading ${title.toLowerCase()}`} />}
      {!loading && error && <Problem message={error} retry />}
      {!loading && !error && balance.frame && flows.frame && (steps === null
        ? <EmptyState title="Nothing to show yet">The balance this widget asks about has no figures in the range.</EmptyState>
        : steps && steps.length <= 2
          ? <EmptyState title="Nothing to show yet">The flows this widget asks about have no figures in the range.</EmptyState>
          : steps && <WaterfallChart steps={steps} unit={balance.frame.unit ?? "sgd_minor"} notes={notes} />)}
    </Card>
  );
}

const KIND: Record<WaterfallStep["kind"], string> = { open: END, in: UP, out: DOWN, close: END };

type Row = { step: string; low: Cents; high: Cents; fill: string; data: WaterfallStep };

/** The span of a step's bar: the level it sits at for an endpoint, the run it crosses for a change. */
const span = (s: WaterfallStep): [Cents, Cents] =>
  s.kind === "in" ? [s.before, s.after]
    : s.kind === "out" ? [s.after, s.before]
    : [Math.min(0, s.value), Math.max(0, s.value)];

export function WaterfallChart({ steps, unit = "sgd_minor", notes }: {
  steps: WaterfallStep[]; unit?: WidgetUnit; notes?: string[];
}) {
  const height = useChartHeight();
  // a range bar, one rect a step: dataKey answers [low, high], so each bar
  // floats where its step crosses, no invisible baseline needed
  const rows: Row[] = steps.map((s) => {
    const [low, high] = span(s);
    return { step: s.name, low, high, fill: KIND[s.kind], data: s };
  });
  const summary = steps.length
    ? `Waterfall from ${formatSgd(steps[0].value)} to ${formatSgd(steps[steps.length - 1].value)}, ${Math.max(0, steps.length - 2)} steps`
    : "Waterfall, nothing in it";
  const clickable = steps.some((s) => s.href);
  return (
    <div role="img" aria-label={summary}>
      <div className="mb-2 flex flex-wrap items-center gap-x-1 gap-y-0.5 text-[12px] text-ink-2">
        <KeyItem><Swatch colour={END} />Balance</KeyItem>
        <KeyItem><Swatch colour={UP} />In</KeyItem>
        <KeyItem><Swatch colour={DOWN} />Out</KeyItem>
      </div>
      <ResponsiveContainer width="100%" height={height}>
        <BarChart data={rows} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid {...GRID} />
          <XAxis dataKey="step" {...X_AXIS} interval={0}
            tickFormatter={(v) => cutName(tickLabel(v), 12)} />
          <YAxis {...Y_AXIS} tickFormatter={(v) => tickValue(v, unit)} />
          <Tooltip cursor={{ fill: "var(--hover)" }} content={({ active, payload }) => {
            const row = active ? (payload?.[0]?.payload as Row | undefined) : undefined;
            return row ? <StepTip row={row} clickable={clickable} /> : null;
          }} />
          {rows.some((r) => r.low < 0) && <ReferenceLine y={0} stroke="var(--ink-3)" strokeOpacity={0.5} />}
          <Bar dataKey={(r: Row) => [r.low, r.high]} isAnimationActive={false} maxBarSize={48}
            cursor={clickable ? "pointer" : undefined}
            onClick={clickable ? (d: { payload?: Row }) => { const to = d?.payload?.data.href; if (to) navigate(to); } : undefined}>
            {rows.map((r) => <Cell key={r.step} fill={r.fill} />)}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
      {notes?.map((n) => <p key={n} className="m-0 mt-2 text-[12px] text-ink-3">{n}</p>)}
    </div>
  );
}

/** The hover card: what the step is and what it came to; a change under zero keeps its true minus in danger. */
function StepTip({ row, clickable }: { row: Row; clickable: boolean }) {
  const s = row.data;
  const endpoint = s.kind === "open" || s.kind === "close";
  return (
    <TipBox heading={s.name} hint={s.href && clickable ? "Click the bar to see its transactions" : undefined}>
      {!endpoint && <StepRow name="Change" value={s.value} danger={s.value < 0} />}
      <StepRow name={endpoint ? "Balance" : "Now"} value={s.after} danger={s.after < 0} />
    </TipBox>
  );
}

function StepRow({ name, value, danger = false }: { name: string; value: Cents; danger?: boolean }) {
  return (
    <div className="flex items-center gap-2 text-ink-2">
      <span className="w-2" aria-hidden />
      <span>{name}</span>
      <span className={`num ml-auto shrink-0 pl-3 ${danger ? "text-danger" : "text-ink"}`}>
        {formatSgd(value, { decimals: true })}
      </span>
    </div>
  );
}
