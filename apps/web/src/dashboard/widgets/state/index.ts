// G26: the state widgets, the "where do things stand now" tiles and charts
// the dashboard's layouts place (epic #4, issue #7): the pie/donut and the
// treemap for share of a whole, the KPI tile for one figure against its prior
// period. Each is config-driven, bound to WidgetQuery through useWidgetData.
export { PieState, defaultPieConfig, type PieStateConfig } from "./PieState";
export { TreemapState, defaultTreemapConfig, type TreemapStateConfig } from "./TreemapState";
export { KpiState, defaultKpiConfig, type KpiStateConfig } from "./KpiState";
export type { Aggregate, Slice } from "./model";
