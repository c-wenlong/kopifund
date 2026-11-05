// G26: the stacked area change widget: composition over time, drawn the way
// Net worth draws it, with the same Stacked / Lines / Total switch. The view
// the owner picks is remembered per widget instance (the config's `id`).
import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Card } from "../../../components/Card";
import { EmptyState } from "../../../components/EmptyState";
import { Loading, Problem } from "../../../components/States";
import {
  activeDot, GRID, KeyItem, SeriesLegend, Swatch, TipBox, TipRow, useDotClick, useHidden, X_AXIS, Y_AXIS,
} from "../../../components/chartKit";
import { useChartView, ViewSwitch, viewsOf, type ChartView } from "../../../components/ViewSwitch";
import { tickLabel } from "../../../stats/hint";
import type { WidgetRange } from "../../../types.gen";
import {
  fmtValue, frameSummary, openPoint, tickValue, useAnswers, useChartHeight,
  type Drawn, type Frame, type SeriesSpec,
} from "./shared";

const VIEWS = viewsOf("stacked", "lines", "total");
const VIEW_KEYS = ["stacked", "lines", "total"] as const;
const TOTAL_INK = "var(--ink)";

/** What a stacked area widget is bound to: its series' queries, and the range they inherit. */
export type StackedAreaConfig = {
  /** the widget instance's id: its remembered view choice keys off it */
  id: string;
  title?: string;
  /** the queries drawn; each answer's series pile up in the order given */
  series: SeriesSpec[];
  /** the range a query without its own inherits, e.g. a dashboard-level filter */
  range?: WidgetRange;
};

/** An example binding: net worth's parts by month, the Net worth page as a widget. */
export const DEFAULT_STACKED_AREA_CONFIG: StackedAreaConfig = {
  id: "stacked-area",
  title: "Net worth by part",
  series: [
    { query: { source: "net-worth", metric: "cash", dimension: "month" }, label: "Cash" },
    { query: { source: "net-worth", metric: "investments", dimension: "month" }, label: "Investments" },
    { query: { source: "net-worth", metric: "cpf", dimension: "month" }, label: "CPF" },
  ],
};

export function StackedAreaWidget({ config = DEFAULT_STACKED_AREA_CONFIG }: { config?: StackedAreaConfig }) {
  const { frame, error, loading } = useAnswers(config.series, config.range);
  const [view, setView] = useChartView<ChartView>(`widget-${config.id}`, VIEW_KEYS, "stacked");
  const title = config.title ?? "Composition over time";
  return (
    <Card title={title} actions={frame && frame.series.length > 0 ? (
      <ViewSwitch label="Chart view" value={view} options={VIEWS} onChange={setView} />
    ) : null}>
      {loading && <Loading what={`Loading ${title.toLowerCase()}`} />}
      {!loading && error && <Problem message={error} retry />}
      {frame && (frame.labels.length === 0
        ? <EmptyState title="Nothing to show yet">The series this widget asks about have no figures in the range.</EmptyState>
        : <StackedAreaChart frame={frame} view={view} />)}
    </Card>
  );
}

type Row = Record<string, number | string | null>;

/** Stacked: the series piled up. Lines: each on its own scale. Total: the one line. */
export function StackedAreaChart({ frame, view }: { frame: Frame; view: ChartView }) {
  const height = useChartHeight();
  const { hidden, toggle } = useHidden(frame.series.map((s) => s.id));
  const shown = frame.series.filter((s) => !hidden.has(s.id));
  const total = view === "total";
  const rows: Row[] = frame.labels.map((l, i) => {
    const row: Row = { label: l };
    let sum: number | null = null;
    let shownSum: number | null = null;
    for (const s of frame.series) {
      const v = s.values[i];
      row[s.key] = v;
      if (v == null) continue;
      sum = (sum ?? 0) + v;
      if (!hidden.has(s.id)) shownSum = (shownSum ?? 0) + v;
    }
    row.total = sum;
    row.shown = shownSum;
    return row;
  });
  // a point's dot opens its rows on Transactions when the answer carried links
  const linked = shown.filter((s) => s.href.some(Boolean));
  const clickable = !total && linked.length > 0;
  const dots = useDotClick();
  return (
    <div role="img" aria-label={frameSummary(frame)}>
      {total
        ? <SeriesLegend items={[]} hidden={hidden} onToggle={() => {}} extra={<KeyItem><Swatch colour={TOTAL_INK} line />Total</KeyItem>} />
        : <SeriesLegend hidden={hidden} onToggle={toggle}
          items={frame.series.map((s) => ({ key: s.id, label: s.name, colour: s.colour, line: view === "lines" }))} />}
      <ResponsiveContainer width="100%" height={height}>
        <ComposedChart data={rows} stackOffset="sign" margin={{ top: 8, right: 8, bottom: 0, left: 0 }}
          onClick={clickable ? (st: { activeLabel?: string | number }) => {
            const key = dots.take();
            const s = key ? frame.series.find((x) => x.key === key) : undefined;
            if (s && st?.activeLabel != null) openPoint(s, frame.labels, String(st.activeLabel));
          } : undefined}>
          <CartesianGrid {...GRID} />
          <XAxis dataKey="label" {...X_AXIS} />
          <YAxis {...Y_AXIS} domain={view === "stacked" ? undefined : ["auto", "auto"]}
            tickFormatter={(v) => tickValue(v, frame.unit)} />
          <Tooltip cursor={{ stroke: "var(--ink-3)", strokeWidth: 1 }} content={({ active, payload }) => {
            const row = active ? (payload?.[0]?.payload as Row | undefined) : undefined;
            return row ? <AreaTip row={row} shown={shown} all={frame.series} view={view} clickable={clickable} /> : null;
          }} />
          {view === "stacked" && shown.map((s) => (
            <Area key={s.id} dataKey={s.key} name={s.name} stackId="w" type="linear"
              stroke={s.colour} strokeWidth={1.5} fill={s.colour} fillOpacity={0.3}
              activeDot={dots.dot(s.key, s.colour, clickable && linked.includes(s))}
              connectNulls isAnimationActive={false} />
          ))}
          {view === "lines" && shown.map((s) => (
            <Line key={s.id} dataKey={s.key} name={s.name} type="linear" stroke={s.colour} strokeWidth={2}
              dot={false} activeDot={dots.dot(s.key, s.colour, clickable && linked.includes(s))}
              connectNulls isAnimationActive={false} />
          ))}
          {total && (
            <Line dataKey="total" name="Total" type="linear" stroke={TOTAL_INK} strokeWidth={2} dot={false}
              activeDot={activeDot(TOTAL_INK)} connectNulls isAnimationActive={false} />
          )}
        </ComposedChart>
      </ResponsiveContainer>
      {frame.note && <p className="m-0 mt-2 text-[12px] text-ink-3">{frame.note}</p>}
    </div>
  );
}

/**
 * The hover card: the label, then the figures (every series on Total, the
 * shown ones otherwise; lines biggest first, the stack's top first), and the
 * sum they come to. "Parts showing only" marks a total the hidden series
 * would change, as Net worth's card does.
 */
function AreaTip({ row, shown, all, view, clickable }: {
  row: Row; shown: Drawn[]; all: Drawn[]; view: ChartView; clickable: boolean;
}) {
  const total = view === "total";
  const items = view === "lines"
    ? [...shown].sort((a, b) => (Number(row[b.key]) || 0) - (Number(row[a.key]) || 0))
    : [...(total ? all : shown)].reverse();
  const sum = (total ? row.total : row.shown) as number | null;
  const listed = items.filter((s) => row[s.key] != null);
  return (
    <TipBox heading={tickLabel(row.label, true)}
      total={listed.length === 1 ? undefined : fmtValue(sum, all[0]?.unit ?? "number", true) || undefined}
      hint={clickable ? "Click a dot to see its transactions" : undefined}>
      {listed.map((s) => (
        <TipRow key={s.id} colour={s.colour} line={view === "lines" || total} name={s.name}
          value={fmtValue(row[s.key] as number | null, s.unit)} keepZero />
      ))}
      {!total && shown.length < all.length && <div className="mt-1 text-[11px] text-ink-3">Parts showing only</div>}
    </TipBox>
  );
}
