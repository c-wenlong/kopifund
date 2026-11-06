// G26: the waterfall change widget. A balance is a month's closing figure, so
// the bridge opens on the close of the month before the flows start and closes
// on the close of their last month; every flow series sums over those months
// to one step, inflows first. Each step draws as a floating range bar (Recharts Bar
// with a [low, high] dataKey), up in the good tone and down in danger, in
// integer cents.
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ReferenceLine,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { MaybeCard } from "../../../components/Card";
import { EmptyState } from "../../../components/EmptyState";
import { Loading, Problem } from "../../../components/States";
import {
  ChartBox, cutName, fillClass, GRID, type ChartHeight, KeyItem, Swatch, TipBox, X_AXIS, Y_AXIS,
} from "../../../components/chartKit";
import { shiftMonth } from "../../../dates";
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
   * month): the answer's first series, each figure a month's close. The
   * widget asks for it from the month before the range, whose close is the
   * opening
   */
  balance: SeriesSpec;
  /** each series of these answers sums over the range to one step (a spending split gives several) */
  flows: SeriesSpec[];
  /** the range a query without its own inherits, e.g. a dashboard-level filter */
  range?: WidgetRange;
  /** the chart's height: "fill" on a dashboard (its cell decides), the viewport's otherwise */
  height?: ChartHeight;
};

/**
 * An example binding: cash bridged by each month's net per bucket. With no
 * range the flows cover the record's last twelve months (the spending
 * source's own default) and the balance the whole record, so the bridge
 * follows the record, never a fixed month.
 */
export const DEFAULT_WATERFALL_CONFIG: WaterfallConfig = {
  id: "waterfall",
  title: "Where the money moved",
  balance: { query: { source: "savings-runway", metric: "cash", dimension: "month" } },
  flows: [{ query: { source: "spending", metric: "net", dimension: "month", split: "bucket" } }],
};

/**
 * The balance as the widget asks for it: from the month before its range,
 * since the opening is that month's close. A balance with no range at all
 * is asked for whole, which holds that month already.
 */
export function balanceSpec(config: WaterfallConfig): SeriesSpec {
  const range = config.balance.query.range ?? config.range;
  const before = range?.from ? shiftMonth(range.from, -1) : null;
  if (!range || !before) return config.balance;
  return { ...config.balance, query: { ...config.balance.query, range: { ...range, from: before } } };
}

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

/** A flow step's link: the rows behind its series over the months it summed (`from`..`to`). */
function stepHref(series: Frame["series"][number], from: string | undefined, to: string | undefined): string | null {
  if (!series.rangeLink || !from || !to) return null;
  return `/transactions?${new URLSearchParams({ ...series.rangeLink, from, to })}`;
}

/** The last index at or before `cap` (any, with no cap) whose figure is there, at or after `floor`; -1 for none. */
function lastFigure(labels: string[], vals: (number | null)[], cap: string | null, floor = -1): number {
  for (let i = vals.length - 1; i > floor; i--) {
    if (vals[i] != null && (cap == null || labels[i]! <= cap)) return i;
  }
  return -1;
}

/**
 * The bridge. A balance figure is a month's close, so it already holds that
 * month's flows: the bridge opens on the close of the month before the
 * flows' first month (else the balance's first figure, the flows before it
 * then left out), closes on the close of their last month (else the last
 * figure before it), and each flow series sums over the months between,
 * after the opening's month up to the closing's, as one step, the inflows
 * first so the reader climbs before falling. Flows that cover every way the
 * balance moves then reconcile it exactly. Null when the balance answered
 * no figure at all: a chart then would read missing cash data as zero
 * balances.
 */
export function waterfallSteps(balance: Frame | null, flows: Frame | null): WaterfallStep[] | null {
  const level = balance?.series[0];
  const vals = level?.values ?? [];
  const labels = balance?.labels ?? [];
  const months = flows?.labels ?? [];
  const start = months.length ? shiftMonth(months[0]!, -1) : null;
  const end = months.length ? months[months.length - 1]! : null;
  let first = start == null ? vals.findIndex((v) => v != null) : lastFigure(labels, vals, start);
  // nothing as early as the month before: open on the first figure up to the flows' end
  if (first < 0) first = vals.findIndex((v, i) => v != null && (end == null || labels[i]! <= end));
  if (first < 0) return null;
  const last = lastFigure(labels, vals, end, first - 1);
  const opening = vals[first]!;
  const closing = vals[last]!;
  const steps: WaterfallStep[] = [{
    name: "Opening", kind: "open", value: opening, before: 0, after: opening,
    href: level?.href[first] ?? null,
  }];
  // the flow months the bridge spans: after the opening's month, up to the closing's
  const spans = (m: string) => m > labels[first]! && m <= labels[last]!;
  const deltas = (flows?.series ?? []).map((s) => {
    const counted = months.flatMap((m, i) => (spans(m) && s.values[i] != null ? [m] : []));
    return {
      name: s.name,
      value: s.values.reduce<Cents>((a, v, i) => a + (spans(months[i]!) ? v ?? 0 : 0), 0),
      href: stepHref(s, counted[0], counted[counted.length - 1]),
    };
  });
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

export function WaterfallWidget({ config = DEFAULT_WATERFALL_CONFIG, bare = false }: { config?: WaterfallConfig; bare?: boolean }) {
  // one hook per spec (the balance plus each flow), so a config that changes
  // the count remounts here rather than change the hook list mid-life
  return <WaterfallLoad key={config.flows.length + 1} config={config} bare={bare} />;
}

function WaterfallLoad({ config, bare }: { config: WaterfallConfig; bare: boolean }) {
  const balance = useAnswers([balanceSpec(config)], config.range);
  const flows = useAnswers(config.flows, config.range);
  const autoHeight = useChartHeight();
  const title = config.title ?? "Waterfall";
  const loading = balance.loading || flows.loading;
  const error = balance.error ?? flows.error;
  const steps = !loading && !error && balance.frame && flows.frame
    ? waterfallSteps(balance.frame, flows.frame)
    : undefined;
  // a projection or preview answer marks its figures as estimates; show every note either frame carries
  const notes = [...new Set([balance.frame?.note, flows.frame?.note].filter((n): n is string => !!n))];
  return (
    <MaybeCard title={title} bare={bare}>
      {loading && <Loading what={`Loading ${title.toLowerCase()}`} />}
      {!loading && error && <Problem message={error} retry />}
      {!loading && !error && balance.frame && flows.frame && (steps === null
        ? <EmptyState title="Nothing to show yet">The balance this widget asks about has no figures in the range.</EmptyState>
        : steps && steps.length <= 2
          ? <EmptyState title="Nothing to show yet">The flows this widget asks about have no figures in the range.</EmptyState>
          : steps && <WaterfallChart steps={steps} unit={balance.frame.unit ?? "sgd_minor"} notes={notes}
            height={config.height ?? autoHeight} />)}
    </MaybeCard>
  );
}

const KIND: Record<WaterfallStep["kind"], string> = { open: END, in: UP, out: DOWN, close: END };

type Row = { step: string; low: Cents; high: Cents; fill: string; data: WaterfallStep };

/** The span of a step's bar: the level it sits at for an endpoint, the run it crosses for a change. */
const span = (s: WaterfallStep): [Cents, Cents] =>
  s.kind === "in" ? [s.before, s.after]
    : s.kind === "out" ? [s.after, s.before]
    : [Math.min(0, s.value), Math.max(0, s.value)];

export function WaterfallChart({ steps, unit = "sgd_minor", notes, height }: {
  steps: WaterfallStep[]; unit?: WidgetUnit; notes?: string[]; height?: ChartHeight;
}) {
  const autoHeight = useChartHeight();
  height ??= autoHeight;
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
    <div role="img" aria-label={summary} className={fillClass(height)}>
      <div className="mb-2 flex flex-wrap items-center gap-x-1 gap-y-0.5 text-[12px] text-ink-2">
        <KeyItem><Swatch colour={END} />Balance</KeyItem>
        <KeyItem><Swatch colour={UP} />In</KeyItem>
        <KeyItem><Swatch colour={DOWN} />Out</KeyItem>
      </div>
      <ChartBox height={height}>
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
      </ChartBox>
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
