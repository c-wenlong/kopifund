// G26: what the change widgets share. A widget's config names its series as
// `SeriesSpec`s; each resolves through useWidgetData (../widgets.ts) to a
// WidgetAnswer, and `joinFrames` lays them over one set of labels. Money stays
// in integer cents (unit `sgd_minor`) end to end; every other unit is written
// the way hint.ts's `fmt` writes it. A point's click lists its rows on
// Transactions from the answer's `label_links` and the series' `link`, the
// chart links idea (G13) carried to widgets.
import { useWidgetData } from "../../../widgets";
import { navigate } from "../../../router";
import { fmt, tickLabel } from "../../../stats/hint";
import { CENTS_UNIT } from "../../../stats/money";
import { axisSgd } from "../../../money";
import { H_MAIN, H_SMALL, seriesColour } from "../../../components/chartKit";
import { useWide } from "../../../components/useWide";
import type { WidgetAnswer, WidgetQuery, WidgetRange, WidgetUnit } from "../../../types.gen";

/** One series a widget asks for: the query, and the legend name when the answer's own reads badly. */
export type SeriesSpec = {
  query: WidgetQuery;
  label?: string;
};

/**
 * The range a config holds fills in for a query that names none, so a
 * dashboard-level filter can move every series at once. A query that names
 * its own keeps it: that range was chosen on purpose.
 */
export function queryWithRange(query: WidgetQuery, range?: WidgetRange): WidgetQuery {
  return range && !query.range ? { ...query, range } : query;
}

/** Where a point's click lists the rows behind it: the label's own link, then the series'. A series link of explicit null means its rows cannot be filtered, so the point is not clickable. */
export function pointHref(answer: WidgetAnswer, labelIndex: number, series?: { link?: Record<string, string> | null }): string | null {
  if (series?.link === null) return null;
  const base = answer.label_links?.[labelIndex] ?? null;
  const extra = series?.link ?? null;
  if (!base && !extra) return null;
  return `/transactions?${new URLSearchParams({ ...base, ...extra })}`;
}

/** One series drawn on a widget, its values and links aligned to the frame's labels. */
export type Drawn = {
  /** the series' field name in a chart row, in draw order */
  key: string;
  /** the series' identity (its spec and name): a resort keeps it, so legend choices stay put */
  id: string;
  name: string;
  colour: string;
  unit: WidgetUnit;
  values: (number | null)[];
  /** per label: the Transactions page its point opens, or null */
  href: (string | null)[];
  /**
   * the link params that hold over the series' own answer (the labels' own
   * flags and filters, then the series' link; from/to the answer's ends), for
   * a step that lists every row behind it at once. Null where the series'
   * link is an explicit null: its rows cannot be filtered.
   */
  rangeLink: Record<string, string> | null;
};

/**
 * Several answers on one axis: `labels` is the union of every answer's, in
 * order (ISO months, days and years sort as text). A series a label does not
 * reach reads null there, so lines connect and stacks treat it as nothing.
 */
export type Frame = {
  labels: string[];
  series: Drawn[];
  /** the unit every series shares; null when a config mixes units, which no widget expects */
  unit: WidgetUnit | null;
  /** a note an answer carries (a projection says it is an estimate) */
  note?: string;
};

export function joinFrames(specs: SeriesSpec[], answers: (WidgetAnswer | null)[]): Frame {
  const seen = new Set<string>();
  for (const a of answers) a?.labels.forEach((l) => seen.add(l));
  const labels = [...seen].sort();
  const series: Drawn[] = [];
  const units = new Set<WidgetUnit>();
  const ids = new Set<string>();
  let note: string | undefined;
  specs.forEach((spec, i) => {
    const a = answers[i];
    if (!a) return;
    units.add(a.unit);
    note ??= a.note;
    const at = new Map(a.labels.map((l, j) => [l, j] as const));
    const base = a.label_links?.find((l) => l) ?? null;
    const overRange = base
      ? Object.fromEntries(Object.entries(base).filter(([k]) => k !== "from" && k !== "to"))
      : null;
    const bounds = a.labels.length ? { from: a.labels[0]!, to: a.labels[a.labels.length - 1]! } : null;
    a.series.forEach((s, j) => {
      const raw = (a.unit === CENTS_UNIT ? s.values_minor : s.values) ?? [];
      let id = `w${i}:${s.name}`;
      if (ids.has(id)) id = `${id}#${j}`;
      ids.add(id);
      series.push({
        key: `s${series.length}`,
        id,
        name: spec.label ?? s.name,
        colour: seriesColour(series.length),
        unit: a.unit,
        values: labels.map((l) => {
          const j = at.get(l);
          return j == null ? null : raw[j] ?? null;
        }),
        href: labels.map((l) => {
          const j = at.get(l);
          return j == null ? null : pointHref(a, j, s);
        }),
        rangeLink: s.link === null || !bounds ? null
          : overRange || s.link ? { ...bounds, ...overRange, ...s.link } : null,
      });
    });
  });
  return { labels, series, unit: units.size === 1 ? [...units][0]! : null, note };
}

/** What a widget's specs came to: loading until every answer is in, the first error, else the frame. */
export type Answered = { frame: Frame | null; error: string | null; loading: boolean };

/**
 * Every spec's answer, joined once they are all in. The hook order is fixed by
 * the config's own series list, which does not change between renders.
 */
export function useAnswers(specs: SeriesSpec[], range?: WidgetRange): Answered {
  const loaded = specs.map((s) => useWidgetData(queryWithRange(s.query, range)));
  const loading = loaded.some((l) => l.loading);
  const error = loaded.map((l) => l.error).find((e): e is string => !!e) ?? null;
  const answers = loaded.map((l) => l.data);
  const frame = !loading && !error && answers.every((a) => a != null) ? joinFrames(specs, answers) : null;
  return { frame, error, loading };
}

/** A figure in its unit: cents to the cent when `full`, every other unit as hint.ts writes it. */
export const fmtValue = (v: number | null | undefined, unit: WidgetUnit | string, full = true): string =>
  v == null ? "" : fmt(v, unit, full);

/** An axis tick: compact money in cents, anything else in its unit. */
export const tickValue = (v: unknown, unit: WidgetUnit | null): string =>
  typeof v === "number" && unit === CENTS_UNIT ? axisSgd(Math.round(v)) : fmt(v, unit ?? "number");

/** The chart's height: H_MAIN on a desktop, H_SMALL on a phone where the widget's width is tight. */
export function useChartHeight(): number {
  return useWide() ? H_MAIN : H_SMALL;
}

/** What the chart announces: the series' names and the span they cover. */
export function frameSummary(frame: Frame): string {
  const names = [...new Set(frame.series.map((s) => s.name))].join(", ");
  const first = tickLabel(frame.labels[0], true);
  const last = tickLabel(frame.labels[frame.labels.length - 1], true);
  return first === last ? `${names}, ${first}` : `${names}, ${first} to ${last}`;
}

/** The rows Recharts plots: one object per label, each series' value under its key. */
export function frameRows(frame: Frame): Record<string, number | string | null>[] {
  return frame.labels.map((l, i) => ({
    label: l,
    ...Object.fromEntries(frame.series.map((s) => [s.key, s.values[i]])),
  }));
}

/** The click a series' point at `label` opens, when the answer carried a link for it. */
export function openPoint(series: Drawn, labels: string[], label: string) {
  const i = labels.indexOf(label);
  const to = i < 0 ? null : series.href[i];
  if (to) navigate(to);
}
