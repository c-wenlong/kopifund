import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { navigate } from "../router";
import {
  BAR_GRID, GRID, nameTick, seriesColour, SeriesLegend, TipBox, TipRow, useDotClick, useHidden, X_AXIS, Y_AXIS, Y_NAMES,
} from "../components/chartKit";
import type { ChartView } from "../components/ViewSwitch";
import { useWide } from "../components/useWide";
import { axisSgd } from "../money";
import { barLink, columnUnit, fmt, label, tickLabel, type Plan, type Row } from "./hint";
import { CENTS_UNIT } from "./money";
import type { TipExtras } from "./tipExtras";

/** G18: the name column of a horizontal bar chart: 160px on a desktop, 104px on a phone, where a long name is cut to fit. */
const NAMES = { wide: { width: 160, chars: 24 }, narrow: { width: 104, chars: 15 } } as const;

/**
 * An axis tick: money compact ("S$12k", "S$500") on an axis in cents (G20), every
 * other unit as the tooltip writes it. A tick between two whole cents is rounded to one.
 */
const tickFmt = (v: unknown, unit: string) =>
  typeof v === "number" && unit === CENTS_UNIT ? axisSgd(Math.round(v)) : fmt(v, unit);

type ChartPlan = Extract<Plan, { kind: "chart" }>;
export type TipItem = { dataKey?: string | number; name?: string; value?: unknown; color?: string; payload?: Row };

// Recharts finds its axes, grid and tooltip among the chart's direct children,
// so none of them may sit inside a fragment: a fragment hides them and the
// bars draw with no scale (every horizontal bar landed on one spot).
/** A stacked chart can be drawn as one line per series instead (StatCard's switch); every other chart has one way. */
export const hasLinesView = (p: ChartPlan) => p.type === "stacked" && p.series.length > 1 && !p.horizontal;

/**
 * `extras` reads the figures a row carries beyond the plotted series (the
 * hover card lists them under the series); `notes` says what a legend entry means.
 */
export function StatChart({ p, view = "stacked", notes, extras }: {
  p: ChartPlan; view?: ChartView; notes?: Record<string, string>; extras?: (row: Row) => TipExtras;
}) {
  const { hidden, toggle } = useHidden(p.series);
  const names = useWide() ? NAMES.wide : NAMES.narrow;
  const height = p.horizontal ? Math.max(200, 30 * p.rows.length + 40) : 260;
  const asLines = view === "lines" && hasLinesView(p);
  // a line chart's points are what open the rows, as a stacked chart's lines view does
  const dotted = asLines || p.type === "line";
  const stacked = p.type === "stacked" && !asLines;
  // legend entries hide a series; what is drawn is what is left
  const colourOf = (s: string) => seriesColour(p.series.indexOf(s));
  const drawn = p.series.filter((s) => !hidden.has(s));
  // G13: a bar opens exactly the rows its figure adds up, which the stat says per series; a series
  // that is an average or a balance has no link, so it takes no click and its hover promises none
  const link = (row: Row | undefined, series: string) => barLink(p.links, series, row, p.xKey);
  const open = (row: Row | undefined, series: string) => {
    const to = link(row, series);
    if (to) navigate(to);
  };
  // only bars and line dots take a click, and only for a series that has a link
  const opens = p.type === "bar" || p.type === "stacked" || p.type === "line";
  const linked = opens ? drawn.filter((s) => p.rows.some((r) => link(r, s))) : [];
  const clickable = linked.length > 0;
  const openLabel = (x: unknown, series: string) => open(p.rows.find((r) => String(r[p.xKey]) === String(x)), series);
  const dots = useDotClick();
  const tooltip = (
    <Tooltip
      cursor={{ fill: "var(--hover)" }}
      content={({ active, label: x, payload }) => (
        <Tip active={active} x={x} items={payload as TipItem[] | undefined} p={p} total={p.type === "stacked"} clickable={clickable}
          only={linked.length < drawn.length ? linked : undefined} dots={dotted} bySize={asLines} extras={extras} />
      )}
    />
  );
  const legend = p.series.length > 1 && p.type !== "scatter"
    ? <SeriesLegend hidden={hidden} onToggle={toggle}
      items={p.series.map((s) => ({ key: s, label: label(s), colour: colourOf(s), line: p.type === "line" || asLines, note: notes?.[s] }))} />
    : null;
  const grid = <CartesianGrid {...GRID} />;
  const y = <YAxis yAxisId="y" {...Y_AXIS} tickFormatter={(v) => tickFmt(v, p.primary)} />;
  const y1 = p.secondary ? (
    <YAxis yAxisId="y1" orientation="right" {...Y_AXIS} tickFormatter={(v) => tickFmt(v, p.secondary!)} />
  ) : null;
  const axisFor = (s: string) => (p.secondary && p.unitOf(s) === p.secondary ? "y1" : "y");
  const x = <XAxis dataKey={p.xKey} {...X_AXIS} />;

  if (p.type === "scatter") {
    const s = p.series[0];
    return (
      <ResponsiveContainer width="100%" height={height}>
        <ScatterChart margin={{ top: 8, right: 8, bottom: 8, left: 0 }}>
          {grid}
          <XAxis dataKey={p.xKey} type="number" {...X_AXIS} name={label(p.xKey)} tickFormatter={(v) => tickFmt(v, unitOfX(p))} />
          <YAxis dataKey={s} {...Y_AXIS} tickFormatter={(v) => tickFmt(v, p.unitOf(s))} />
          {tooltip}
          <Scatter data={p.rows} fill={seriesColour(0)} />
        </ScatterChart>
      </ResponsiveContainer>
    );
  }
  if (p.type === "line" || asLines) {
    return (
      <>
        {legend}
        <ResponsiveContainer width="100%" height={height}>
          <LineChart data={p.rows} margin={{ top: 8, right: 8, bottom: 8, left: 0 }}
            onClick={dotted && clickable ? (st: { activeLabel?: string | number }) => {
              const s = dots.take();
              if (s) openLabel(st?.activeLabel, s);
            } : undefined}>
            {grid}
            {x}
            {y}
            {y1}
            {tooltip}
            {drawn.map((s) => (
              <Line key={s} yAxisId={axisFor(s)} dataKey={s} name={s} stroke={colourOf(s)} strokeWidth={2}
                dot={p.rows.length > 30 ? false : { r: 2.5 }} activeDot={dots.dot(s, colourOf(s), dotted && linked.includes(s))}
                connectNulls isAnimationActive={false} />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </>
    );
  }
  const bars = drawn.map((s) => (
    <Bar key={s} yAxisId={p.horizontal ? "cat" : axisFor(s)} dataKey={s} name={s} stackId={stacked ? "a" : undefined}
      fill={colourOf(s)} stroke={stacked ? "var(--panel)" : undefined} strokeWidth={stacked ? 1 : 0} radius={stacked ? 0 : 2}
      maxBarSize={p.horizontal ? 18 : 34}
      isAnimationActive={false} cursor={linked.includes(s) ? "pointer" : undefined}
      onClick={(d: { payload?: Row }) => open(d?.payload, s)} />
  ));
  if (p.horizontal) {
    return (
      <>
        {legend}
        <ResponsiveContainer width="100%" height={height}>
          <BarChart data={p.rows} layout="vertical" margin={{ top: 4, right: 16, bottom: 4, left: 0 }}>
          <CartesianGrid {...BAR_GRID} />
          <XAxis type="number" {...X_AXIS} tickFormatter={(v) => tickFmt(v, p.primary)} />
          <YAxis yAxisId="cat" type="category" dataKey={p.xKey} {...Y_NAMES} width={names.width} tick={nameTick(names.chars)} />
          {tooltip}
          {bars}
          </BarChart>
        </ResponsiveContainer>
      </>
    );
  }
  return (
    <>
      {legend}
      <ResponsiveContainer width="100%" height={height}>
        <BarChart data={p.rows} margin={{ top: 8, right: 8, bottom: 8, left: 0 }}>
          {grid}
          {x}
          {y}
          {y1}
          {tooltip}
          {bars}
        </BarChart>
      </ResponsiveContainer>
    </>
  );
}

/** The hover card: the x value, every series with its colour and value, a total for stacks. */
export function Tip({ active, x, items, p, total, clickable, only, dots = false, bySize = false, extras }: {
  active?: boolean; x?: unknown; items?: TipItem[]; p: ChartPlan; total: boolean; clickable: boolean;
  only?: string[];   // when only some of the series link: which, so the card names them
  dots?: boolean;    // on a line chart the points open the rows, not bars
  bySize?: boolean; extras?: (row: Row) => TipExtras;
}) {
  if (!active || !items?.length) return null;
  // in a stack, a part at zero is left out (a row of S$0 says nothing)
  const shown = items.filter((it) => it.value != null && !(total && it.value === 0));
  const sum = shown.reduce((a, it) => a + (typeof it.value === "number" ? it.value : 0), 0);
  const more = extras && items[0]?.payload ? extras(items[0].payload) : null;
  const heading = x != null && x !== "" ? tickLabel(x, true) : tickLabel(items[0]?.payload?.[p.xKey], true);
  const hint = !clickable ? undefined : only?.length
    ? `Click the ${only.map(label).join(" or ")} ${dots ? "dot" : "bar"} to see its transactions`
    : `Click a ${dots ? "dot" : "bar"} to see its transactions`;
  // G18: the chart kit's box and rows, so this card is as wide as every other chart's
  return (
    <TipBox heading={heading} total={total && shown.length !== 1 ? fmt(sum, p.primary, true) : undefined} hint={hint}>
      {(bySize
        ? [...shown].sort((a, b) => (typeof b.value === "number" ? b.value : 0) - (typeof a.value === "number" ? a.value : 0))
        : [...shown].reverse()).map((it) => (
        <TipRow key={String(it.dataKey)} colour={it.color} line={bySize} name={label(String(it.dataKey ?? it.name ?? ""))}
          value={fmt(it.value, unitOfKey(p, String(it.dataKey)), true)} />
      ))}
      {more && (more.lines.length > 0 || more.notes.length > 0) && (
        <div className="mt-1.5 border-t border-line pt-1.5" data-part="tip-more">
          {more.lines.map((l) => <TipRow key={l.key} bare name={l.name} value={l.text} />)}
          {more.notes.map((n) => <div key={n} className="text-[11px] text-ink-3">{n}</div>)}
        </div>
      )}
    </TipBox>
  );
}

/** The unit of a scatter's x field: the one the stat declares for it, else what its name says (a multiple is not money). */
const unitOfX = (p: ChartPlan) => (p.unitOf(p.xKey) !== p.primary ? p.unitOf(p.xKey) : columnUnit(p.xKey, undefined, p.primary));
/** The unit a hover card writes the series `key` in. */
const unitOfKey = (p: ChartPlan, key: string) => (p.type === "scatter" && key === p.xKey ? unitOfX(p) : p.unitOf(key));
