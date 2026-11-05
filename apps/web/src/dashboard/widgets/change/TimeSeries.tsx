// G26: the time series change widget. One line per series over the answer's
// labels (months, days or years), bound to typed widget queries through
// useWidgetData. A point's dot opens the Transactions rows it lists when the
// answer carried links; one that carried none takes no click.
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Card } from "../../../components/Card";
import { EmptyState } from "../../../components/EmptyState";
import { Loading, Problem } from "../../../components/States";
import {
  GRID,
  SeriesLegend,
  TipBox,
  TipRow,
  useDotClick,
  useHidden,
  X_AXIS,
  Y_AXIS,
} from "../../../components/chartKit";
import { tickLabel } from "../../../stats/hint";
import type { WidgetRange } from "../../../types.gen";
import {
  fmtValue, frameRows, frameSummary, openPoint, tickValue, useAnswers, useChartHeight,
  type Drawn, type Frame, type SeriesSpec,
} from "./shared";

/** What a time series widget is bound to: its series' queries, and the range they inherit. */
export type TimeSeriesConfig = {
  /** the widget instance's id: remembered state keys off it */
  id: string;
  title?: string;
  /** the queries drawn; each answer's series become lines in the order given */
  series: SeriesSpec[];
  /** the range a query without its own inherits, e.g. a dashboard-level filter */
  range?: WidgetRange;
};

/** An example binding: a year's income, spend and net off the monthly cashflow stat. */
export const DEFAULT_TIME_SERIES_CONFIG: TimeSeriesConfig = {
  id: "time-series",
  title: "Income, spending and net",
  series: [
    { query: { source: "monthly-cashflow", metric: "income", dimension: "month" }, label: "Income" },
    { query: { source: "monthly-cashflow", metric: "spend", dimension: "month" }, label: "Spend" },
    { query: { source: "monthly-cashflow", metric: "net", dimension: "month" }, label: "Net" },
  ],
};

export function TimeSeriesWidget({ config = DEFAULT_TIME_SERIES_CONFIG }: { config?: TimeSeriesConfig }) {
  const { frame, error, loading } = useAnswers(config.series, config.range);
  const title = config.title ?? "Over time";
  return (
    <Card title={title}>
      {loading && <Loading what={`Loading ${title.toLowerCase()}`} />}
      {!loading && error && <Problem message={error} retry />}
      {frame && (frame.labels.length === 0
        ? <EmptyState title="Nothing to show yet">The series this widget asks about have no figures in the range.</EmptyState>
        : <TimeSeriesChart frame={frame} />)}
    </Card>
  );
}

export function TimeSeriesChart({ frame }: { frame: Frame }) {
  const height = useChartHeight();
  const { hidden, toggle } = useHidden(frame.series.map((s) => s.id));
  const drawn = frame.series.filter((s) => !hidden.has(s.id));
  const rows = frameRows(frame);
  const linked = drawn.filter((s) => s.href.some(Boolean));
  const clickable = linked.length > 0;
  const dots = useDotClick();
  const multi = frame.series.length > 1;
  return (
    <div role="img" aria-label={frameSummary(frame)}>
      {multi && (
        <SeriesLegend hidden={hidden} onToggle={toggle}
          items={frame.series.map((s) => ({ key: s.id, label: s.name, colour: s.colour, line: true }))} />
      )}
      <ResponsiveContainer width="100%" height={height}>
        <LineChart data={rows} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}
          onClick={clickable ? (st: { activeLabel?: string | number }) => {
            const key = dots.take();
            const s = key ? frame.series.find((x) => x.key === key) : undefined;
            if (s && st?.activeLabel != null) openPoint(s, frame.labels, String(st.activeLabel));
          } : undefined}>
          <CartesianGrid {...GRID} />
          <XAxis dataKey="label" {...X_AXIS} />
          <YAxis {...Y_AXIS} tickFormatter={(v) => tickValue(v, frame.unit)} />
          <Tooltip cursor={{ stroke: "var(--ink-3)", strokeWidth: 1 }} content={({ active, payload }) => {
            const row = active ? (payload?.[0]?.payload as Record<string, number | string | null> | undefined) : undefined;
            return row ? <SeriesTip row={row} drawn={drawn} clickable={clickable} /> : null;
          }} />
          {drawn.map((s) => (
            <Line key={s.id} dataKey={s.key} name={s.name} type="linear" stroke={s.colour} strokeWidth={2}
              dot={rows.length > 30 ? false : { r: 2.5 }}
              activeDot={dots.dot(s.key, s.colour, clickable && linked.includes(s))}
              connectNulls isAnimationActive={false} />
          ))}
        </LineChart>
      </ResponsiveContainer>
      {frame.note && <p className="m-0 mt-2 text-[12px] text-ink-3">{frame.note}</p>}
    </div>
  );
}

/** The hover card: the label, every drawn series' figure biggest first, and the click hint. */
function SeriesTip({ row, drawn, clickable }: {
  row: Record<string, number | string | null>; drawn: Drawn[]; clickable: boolean;
}) {
  return (
    <TipBox heading={tickLabel(row.label, true)}
      hint={clickable ? "Click a dot to see its transactions" : undefined}>
      {[...drawn].filter((s) => row[s.key] != null)
        .sort((a, b) => (Number(row[b.key]) || 0) - (Number(row[a.key]) || 0))
        .map((s) => (
          <TipRow key={s.id} colour={s.colour} line name={s.name}
            value={fmtValue(row[s.key] as number | null, s.unit)} keepZero />
        ))}
    </TipBox>
  );
}
