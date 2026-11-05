// G26: how a state widget reads a WidgetAnswer as shares of a whole, plus the
// helpers the three of them share. A single-series answer over a split
// dimension (spend by bucket, net worth by part) gives one slice per label; a
// multi-series answer, or several queries bound to one widget, gives one slice
// per series, read at its latest point (where things stand now) or summed over
// the range. Money stays integer cents: a `sgd_minor` unit is cents, and only
// it is ever written as money.
import { useMemo } from "react";
import { useApi, type Loaded } from "../../../api";
import { bucketLook } from "../../../components/BucketMark";
import { REST_COLOUR, seriesColour } from "../../../components/chartKit";
import { formatSgd, MINUS, type Cents } from "../../../money";
import { fmt, label, tickLabel } from "../../../stats/hint";
import type { BucketStyle, CategoriesState, WidgetAnswer, WidgetQuery, WidgetSeries, WidgetUnit } from "../../../types.gen";
import { useWidgetData } from "../../../widgets";

/** A part of the whole one slice draws: its figure in the answer's unit and the Transactions filters its rows list. */
export type Slice = {
  key: string;
  name: string;
  value: number;
  link: Record<string, string> | null;
};

/** How a series becomes one figure: its latest non-null point (the state now) or the range's sum. */
export type Aggregate = "latest" | "sum";

const valuesOf = (s: WidgetSeries): (number | null)[] => s.values_minor ?? s.values ?? [];

/** The index of a value's last non-null entry before `end`, or -1 when there is none. */
function lastAt(values: (number | null)[], end = values.length): number {
  for (let i = end - 1; i >= 0; i--) if (values[i] != null) return i;
  return -1;
}

/**
 * The transactions a series' slice lists: the series' own filters over the
 * period the figure counts. `latest` counts one point, so the link is that
 * point's label link (its `from`/`to` month) plus the series' filters; `sum`
 * counts every non-null point, so the link spans the first to the last.
 */
function seriesLink(a: WidgetAnswer, s: WidgetSeries, values: (number | null)[], at: number, aggregate: Aggregate) {
  const points = a.label_links;
  if (aggregate === "sum" && points?.length) {
    const first = points[values.findIndex((v) => v != null)]?.from;
    const last = points[at]?.to;
    if (first && last) return { ...(s.link ?? {}), from: first, to: last };
  }
  const point = points?.[at];
  if (point) return { ...point, ...s.link };
  return s.link ?? null;
}

/** One slice per series: for several bound queries or one split answer. `name` is the series' display name. */
function seriesSlices(answers: readonly WidgetAnswer[], aggregate: Aggregate): Slice[] {
  const slices: Slice[] = [];
  answers.forEach((a, ai) => {
    for (const s of a.series) {
      const values = valuesOf(s);
      const at = lastAt(values);
      if (at < 0) continue;
      slices.push({
        // two bound queries can answer a series of the same name: the answer's
        // place keys the slice apart so the legend toggles each on its own
        key: answers.length > 1 ? `${ai}:${s.name}` : s.name,
        name: label(s.name),
        value: aggregate === "sum" ? values.reduce<number>((sum, v) => sum + (v ?? 0), 0) : (values[at] as number),
        link: seriesLink(a, s, values, at, aggregate),
      });
    }
  });
  return slices;
}

/** One slice per label, the series' figures added where several exist. `name` is the label, dates ticked. */
function labelSlices(answer: WidgetAnswer): Slice[] {
  return answer.labels.map((raw, i) => {
    const missing = answer.series.every((s) => valuesOf(s)[i] == null);
    return {
      key: raw,
      name: tickLabel(raw),
      value: answer.series.reduce((sum, s) => sum + (valuesOf(s)[i] ?? 0), 0),
      link: answer.label_links?.[i] ?? null,
      missing,
    };
  }).filter((s) => !s.missing).map(({ missing: _, ...s }) => s);
}

/** Every slice the answers hold, the ones at or below zero included. */
function allSlices(answers: readonly WidgetAnswer[], aggregate: Aggregate): Slice[] {
  const list = answers.filter(Boolean);
  if (list.length === 0) return [];
  if (list.length > 1 || list[0].series.length > 1) return seriesSlices(list, aggregate);
  return labelSlices(list[0]);
}

/**
 * The slices a set of answers draws. Several answers, or one answer with
 * several series, read as one slice per series (`aggregate` says how); one
 * answer with one series reads as one slice per label. A share of a whole
 * has no room for a part at or below zero (a bucket whose refunds passed its
 * spending): it would draw overlapping sectors or a tile of negative area and
 * net into the total, so it is left out here and listed by `slicesLeftOut`.
 */
export function slicesFor(answers: readonly WidgetAnswer[], aggregate: Aggregate = "latest"): Slice[] {
  return allSlices(answers, aggregate).filter((s) => s.value > 0);
}

/** The slices `slicesFor` leaves out, at or below zero, for a note under the chart. */
export function slicesLeftOut(answers: readonly WidgetAnswer[], aggregate: Aggregate = "latest"): Slice[] {
  return allSlices(answers, aggregate).filter((s) => s.value <= 0);
}

/** Every answer's figure added up: what "the whole" is for the slices drawn. */
export const totalOf = (slices: readonly Slice[]) => slices.reduce((sum, s) => sum + s.value, 0);

const oneDec = new Intl.NumberFormat("en-SG", { maximumFractionDigits: 1 });

/** A slice's share of `total` written compact: "42.3%". A total of nothing shows "0%". */
export function shareText(value: number, total: number): string {
  return `${oneDec.format(total > 0 ? (value / total) * 100 : 0)}%`;
}

/** The unit every bound answer shares, for one widget's display. Mixed units are the first answer's. */
export const unitOf = (answers: readonly WidgetAnswer[]): WidgetUnit => answers[0]?.unit ?? "sgd_minor";

/** A figure in the answer's unit: cents written as money, everything else in its own unit. */
export function fmtValue(v: number, unit: WidgetUnit | string): string {
  return unit === "sgd_minor" ? formatSgd(v as Cents) : fmt(v, unit, true);
}

/** A change with its sign: "+S$12", "−S$12", "+5.0%", "+0.4 mo". */
export function signedValue(delta: number, unit: WidgetUnit | string): string {
  const sign = delta >= 0 ? "+" : MINUS;
  const a = Math.abs(delta);
  if (unit === "sgd_minor") return `${sign}${formatSgd(a as Cents)}`;
  if (unit === "fraction") return `${sign}${oneDec.format(a * 100)}%`;
  if (unit === "percent") return `${sign}${oneDec.format(a)}%`;
  if (unit === "months") return `${sign}${oneDec.format(a)} mo`;
  return `${sign}${oneDec.format(a)}`;
}

/** The Transactions address a slice's or a point's link lists, or null when it carries none. */
export function txnLink(link: Record<string, string> | null | undefined): string | null {
  return link ? `/transactions?${new URLSearchParams(link)}` : null;
}

// Labels the server uses for "the rest folded together" or "belongs nowhere",
// drawn in the kit's neutral rather than a series colour (spending.OTHER).
const FOLDED = new Set(["Everything else", "(no bucket)", "(uncategorised)", "(none)", "(unknown)"]);

/**
 * The record's buckets by name, so a slice named after a bucket can draw in
 * its own colour and icon, the way Spending and Categories draw it
 * (categories.py's `shown`). `on` false asks for nothing.
 */
export function useBucketStyles(on: boolean): Record<string, BucketStyle> | null {
  const { data } = useApi<CategoriesState>(on ? "/api/categories" : null);
  return useMemo(
    () => (on && data ? Object.fromEntries(data.groups.map((g) => [g.name, g as BucketStyle])) : null),
    [on, data],
  );
}

/** The colour a slice draws in: its bucket's own when it is one, the folded grey for "the rest", else its series colour. */
export function sliceColour(name: string, rank: number, buckets: Record<string, BucketStyle> | null): string {
  if (FOLDED.has(name)) return REST_COLOUR;
  const style = buckets?.[name];
  return style ? bucketLook(style, rank).colour : seriesColour(rank);
}

/** The icon a slice's legend entry carries: its bucket's when it is one, none for anything else. */
export function sliceIcon(name: string, buckets: Record<string, BucketStyle> | null) {
  const style = buckets?.[name];
  return style && !FOLDED.has(name) ? bucketLook(style, 0).icon : undefined;
}

/**
 * One `useWidgetData` per bound query. The list comes from the widget's
 * config, so its length never changes between renders: one hook per query,
 * in the same order every time.
 */
export function useWidgetAnswers(queries: readonly WidgetQuery[]): Loaded<WidgetAnswer>[] {
  return queries.map((q) => useWidgetData(q));
}

/** A widget's answers as one load: the first error, still loading, or the answers once all have arrived. */
export function combineAnswers(loaded: readonly Loaded<WidgetAnswer>[]): {
  data: WidgetAnswer[] | null; error: string | null; loading: boolean;
} {
  const error = loaded.find((l) => l.error)?.error ?? null;
  const loading = loaded.some((l) => l.loading);
  const data = !error && loaded.length > 0 && loaded.every((l) => l.data)
    ? loaded.map((l) => l.data as WidgetAnswer)
    : null;
  return { data, error, loading };
}
