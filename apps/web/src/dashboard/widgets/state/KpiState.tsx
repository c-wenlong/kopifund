// G26: the one-figure widget. Bound to a query, it shows the metric's latest
// figure (or the range's sum), the change against the period before it, and a
// sparkline of the run — Home's tile pattern, driven by a WidgetQuery instead
// of the overview. A bound series `series` names which of several to read;
// `goodWhen` says which direction of change reads well in the note's colour.
import { useMemo } from "react";
import { Sparkline, Tile, type Tone } from "../../../components/Tile";
import { Problem } from "../../../components/States";
import { tickLabel } from "../../../stats/hint";
import type { WidgetAnswer, WidgetQuery } from "../../../types.gen";
import { useWidgetData } from "../../../widgets";
import { fmtValue, signedValue, type Aggregate } from "./model";

/**
 * What a KPI tile draws: `title` is the label above the figure; `series` picks
 * one series of a multi-series answer (the first by default); `delta` adds the
 * "vs" note against the prior point; `sparkline` traces the run under it;
 * `goodWhen` colours the note good when the figure moves that way.
 */
export type KpiStateConfig = {
  title: string;
  query: WidgetQuery;
  series?: string;
  aggregate?: Aggregate;
  delta?: boolean;
  sparkline?: boolean;
  goodWhen?: "up" | "down";
};

/** A plain config for the editor to start from: this year's monthly spend, latest month against the one before. */
export const defaultKpiConfig: KpiStateConfig = {
  title: "Spending",
  query: { source: "spending", metric: "spend", dimension: "month" },
  delta: true,
  sparkline: true,
  goodWhen: "down",
};

// the dimensions a KPI reads as a series over time, in catalogue order
const TIME_DIMS = new Set(["month", "year", "period"]);

/**
 * What the tile shows for an answer: the figure, the point before it, and the
 * run of figures. Reading the latest point, a month the answer calls partial
 * (a bank has not printed it yet) is stepped over, as Home's tiles do: half a
 * month's spending set against a whole one would read as a fall.
 */
export function kpiOf(answer: WidgetAnswer, seriesName?: string, aggregate: Aggregate = "latest") {
  // a named series that is gone shows nothing rather than another series' figure
  const series = seriesName === undefined ? answer.series[0] : answer.series.find((s) => s.name === seriesName);
  const timed = TIME_DIMS.has(answer.dimension);
  const partial = new Set(timed && aggregate === "latest" ? answer.partial ?? [] : []);
  const raw = (series ? (series.values_minor ?? series.values ?? []) : [])
    .map((v, i) => (partial.has(answer.labels[i] ?? "") ? null : v));
  let value: number | null = null;
  let prior: number | null = null;
  let atLabel: string | null = null;
  let priorLabel: string | null = null;
  if (timed && aggregate === "latest") {
    for (let i = raw.length - 1; i >= 0 && value == null; i--) {
      if (raw[i] != null) {
        value = raw[i];
        atLabel = answer.labels[i] ?? null;
        for (let j = i - 1; j >= 0 && prior == null; j--) {
          if (raw[j] != null) {
            prior = raw[j];
            priorLabel = answer.labels[j] ?? null;
          }
        }
      }
    }
  } else {
    value = raw.some((v) => v != null) ? raw.reduce<number>((sum, v) => sum + (v ?? 0), 0) : null;
  }
  return { series, value, prior, atLabel, priorLabel, timed,
    run: timed ? raw.filter((v): v is number => v != null) : [] };
}

export function KpiState({ config = defaultKpiConfig }: { config?: KpiStateConfig }) {
  const { data: answer, error, loading } = useWidgetData(config.query);
  const kpi = useMemo(() => (answer ? kpiOf(answer, config.series, config.aggregate) : null),
    [answer, config.series, config.aggregate]);
  if (error) return <Problem message={error} retry />;
  if (loading && !answer) {
    return (
      <div className="flex min-w-0 flex-col rounded-lg bg-tile px-3 py-3 sm:px-4" aria-busy>
        <p role="status" className="sr-only">{`Loading ${config.title}`}</p>
        <div className="h-4 w-24 rounded bg-hover motion-safe:animate-pulse" aria-hidden />
        <div className="mt-2 h-6 w-32 rounded bg-hover motion-safe:animate-pulse" aria-hidden />
      </div>
    );
  }
  if (!answer || !kpi || kpi.value == null) {
    return <Tile label={config.title} value="No data" note="The widget's query answered with no figures." />;
  }
  const unit = answer.unit;
  const delta = kpi.prior != null ? kpi.value - kpi.prior : 0;
  const hasDelta = config.delta !== false && kpi.prior != null;
  const tone: Tone = config.goodWhen && hasDelta && delta !== 0
    ? (config.goodWhen === "up" ? delta > 0 : delta < 0) ? "good" : "warn"
    : "neutral";
  const name = `${config.title}${kpi.atLabel ? ` · ${tickLabel(kpi.atLabel)}` : ""}`;
  const note = hasDelta ? `${signedValue(delta, unit)} vs ${tickLabel(kpi.priorLabel)}` : undefined;
  const visual = config.sparkline !== false && kpi.run.length >= 2
    ? <Sparkline values={kpi.run} label={`${config.title}, ${fmtValue(kpi.run[0], unit)} to ${fmtValue(kpi.run[kpi.run.length - 1], unit)}`} />
    : undefined;
  return (
    <div>
      <Tile label={name} value={fmtValue(kpi.value, unit)} note={note} noteTone={tone} visual={visual} />
      {answer.note && (
        <p className="m-0 mt-1 text-[11px] text-ink-3">{answer.note.charAt(0).toUpperCase() + answer.note.slice(1)}.</p>
      )}
    </div>
  );
}
