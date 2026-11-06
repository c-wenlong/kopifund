// G26: the bridge between a saved widget row ({id, type, title, query,
// options}) and the chart components under dashboard/widgets/. Each adapter
// reads the row's `query` and its free-form `options` into the config its
// component draws; an option the row does not carry falls back to the
// component's own default, so a layout saved by an older build, or written
// by a newer one, still draws. The row's `query` fills in for a config's
// missing binding: a layout saved while the placeholders held the type ids
// keeps the question it asked, now answered by the real chart.
import type { WidgetQuery, WidgetRange } from "../api";
import type { ChartHeight } from "../components/chartKit";
import type { WidgetProps } from "./registry";
import {
  defaultKpiConfig, defaultPieConfig, defaultTreemapConfig,
  KpiState, PieState, TreemapState,
  type Aggregate, type KpiStateConfig, type PieStateConfig, type TreemapStateConfig,
} from "./widgets/state";
import {
  DEFAULT_STACKED_AREA_CONFIG, DEFAULT_TIME_SERIES_CONFIG, DEFAULT_WATERFALL_CONFIG,
  StackedAreaWidget, TimeSeriesWidget, WaterfallWidget,
  type SeriesSpec, type StackedAreaConfig, type TimeSeriesConfig, type WaterfallConfig,
} from "./widgets/change";
import { defaultFlowConfig, FlowWidget, type FlowConfig } from "./widgets/flow";

const isObj = (v: unknown): v is Record<string, unknown> => !!v && typeof v === "object" && !Array.isArray(v);
const str = (v: unknown): string | undefined => (typeof v === "string" && v ? v : undefined);
const numOf = (v: unknown): number | undefined =>
  (typeof v === "number" && Number.isFinite(v) ? v : undefined);
const boolOf = (v: unknown, d: boolean): boolean => (typeof v === "boolean" ? v : d);
const aggregateOf = (v: unknown): Aggregate | undefined => (v === "latest" || v === "sum" ? v : undefined);

/** A saved value is a WidgetQuery when it names a source and a metric, the two a query cannot lack. */
const isQuery = (v: unknown): v is WidgetQuery =>
  isObj(v) && typeof v.source === "string" && typeof v.metric === "string";

/** A saved `range` ({from, to}, either end optional) or undefined. */
const rangeOf = (v: unknown): WidgetRange | undefined => {
  if (!isObj(v)) return undefined;
  const from = str(v.from);
  const to = str(v.to);
  return from || to ? { ...(from ? { from } : {}), ...(to ? { to } : {}) } : undefined;
};

/** A saved series spec ({query, label?}) or null. */
function specOf(v: unknown): SeriesSpec | null {
  if (!isObj(v) || !isQuery(v.query)) return null;
  const label = str(v.label);
  return label ? { query: v.query, label } : { query: v.query };
}

/** A saved list of series specs, undefined when nothing in it parses. */
function specList(v: unknown): SeriesSpec[] | undefined {
  if (!Array.isArray(v)) return undefined;
  const out = v.map(specOf).filter((s): s is SeriesSpec => s !== null);
  return out.length ? out : undefined;
}

/** A saved list of queries (a pie's several bindings), undefined when nothing in it parses. */
function queryList(v: unknown): WidgetQuery[] | undefined {
  if (!Array.isArray(v)) return undefined;
  const out = v.filter(isQuery);
  return out.length ? out : undefined;
}

/** A saved string record (the flow's filters), keys with non-string values dropped. */
function stringRecord(v: unknown): Record<string, string> | undefined {
  if (!isObj(v)) return undefined;
  const out = Object.fromEntries(Object.entries(v).filter(([, x]) => typeof x === "string")) as Record<string, string>;
  return Object.keys(out).length ? out : undefined;
}

/**
 * A chart's height: a saved `options.height` in px, else "fill", the room the
 * widget's cell leaves under its legend and notes (chartKit's ChartBox), so the
 * chart fits the cell and follows a resize.
 */
const heightOf = (v: unknown): ChartHeight => numOf(v) ?? "fill";

/** `kpi`: one figure with its change against the prior point (state/KpiState). */
export function BoundKpi({ widget }: WidgetProps) {
  const o = widget.options ?? {};
  const config: KpiStateConfig = {
    title: widget.title ?? defaultKpiConfig.title,
    query: widget.query ?? defaultKpiConfig.query,
    series: str(o.series),
    aggregate: aggregateOf(o.aggregate),
    delta: boolOf(o.delta, defaultKpiConfig.delta ?? true),
    sparkline: boolOf(o.sparkline, defaultKpiConfig.sparkline ?? true),
    goodWhen: o.goodWhen === "up" || o.goodWhen === "down" ? o.goodWhen : undefined,
  };
  return <KpiState config={config} bare />;
}

/** `pie` and `donut`: a share of a whole; the `donut` type rings it with the total inside (state/PieState). */
export function BoundPie({ widget }: WidgetProps) {
  const o = widget.options ?? {};
  const donut = boolOf(o.donut, widget.type === "donut");
  const config: PieStateConfig = {
    title: widget.title ?? defaultPieConfig.title,
    query: queryList(o.queries) ?? widget.query ?? defaultPieConfig.query,
    donut,
    showTotal: boolOf(o.showTotal, donut),
    aggregate: aggregateOf(o.aggregate),
    height: heightOf(o.height),
  };
  return <PieState config={config} bare />;
}

/** `treemap`: a share of a whole nested by bucket (state/TreemapState). */
export function BoundTreemap({ widget }: WidgetProps) {
  const o = widget.options ?? {};
  const config: TreemapStateConfig = {
    title: widget.title ?? defaultTreemapConfig.title,
    query: widget.query ?? defaultTreemapConfig.query,
    groupBy: o.groupBy === "none" ? "none" : "bucket",
    aggregate: aggregateOf(o.aggregate),
    height: heightOf(o.height),
  };
  return <TreemapState config={config} bare />;
}

/** `line`: series over months, days or years (change/TimeSeries). */
export function BoundSeries({ widget }: WidgetProps) {
  const o = widget.options ?? {};
  const config: TimeSeriesConfig = {
    id: widget.id,
    title: widget.title,
    series: specList(o.series) ?? (widget.query ? [{ query: widget.query }] : DEFAULT_TIME_SERIES_CONFIG.series),
    range: rangeOf(o.range),
    height: heightOf(o.height),
  };
  return <TimeSeriesWidget config={config} bare />;
}

/** `area`: a composition over time with the Stacked / Lines / Total switch (change/StackedArea). */
export function BoundArea({ widget }: WidgetProps) {
  const o = widget.options ?? {};
  const config: StackedAreaConfig = {
    id: widget.id,
    title: widget.title,
    series: specList(o.series) ?? (widget.query ? [{ query: widget.query }] : DEFAULT_STACKED_AREA_CONFIG.series),
    range: rangeOf(o.range),
    height: heightOf(o.height),
  };
  return <StackedAreaWidget config={config} bare />;
}

/** `waterfall`: a balance's first figure to its last, bridged one step per flow (change/Waterfall). */
export function BoundWaterfall({ widget }: WidgetProps) {
  const o = widget.options ?? {};
  const config: WaterfallConfig = {
    id: widget.id,
    title: widget.title,
    balance: specOf(o.balance) ?? (widget.query ? { query: widget.query } : DEFAULT_WATERFALL_CONFIG.balance),
    flows: specList(o.flows) ?? DEFAULT_WATERFALL_CONFIG.flows,
    range: rangeOf(o.range),
    height: heightOf(o.height),
  };
  return <WaterfallWidget config={config} bare />;
}

/** `flow`: the Sankey of income into buckets (or need classes) and categories (flow/FlowWidget). */
export function BoundFlow({ widget }: WidgetProps) {
  const o = widget.options ?? {};
  const config: FlowConfig = {
    title: widget.title ?? defaultFlowConfig.title,
    range: rangeOf(o.range),
    filters: stringRecord(o.filters),
    middle: o.middle === "need" ? "need" : "bucket",
    maxCategories: numOf(o.maxCategories) ?? defaultFlowConfig.maxCategories,
    transfers: boolOf(o.transfers, defaultFlowConfig.transfers ?? true),
  };
  return <FlowWidget config={config} bare />;
}
