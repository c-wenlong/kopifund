// G26: the change widgets: how state moved over time, each a standalone
// config-driven component on the widget query layer (widgets.ts). A layout
// keeps a widget's config; the registry (another goal) names these by type.
export { queryWithRange } from "./shared";
export type { SeriesSpec } from "./shared";
export { DEFAULT_TIME_SERIES_CONFIG, TimeSeriesWidget } from "./TimeSeries";
export type { TimeSeriesConfig } from "./TimeSeries";
export { DEFAULT_STACKED_AREA_CONFIG, StackedAreaWidget } from "./StackedArea";
export type { StackedAreaConfig } from "./StackedArea";
export { DEFAULT_WATERFALL_CONFIG, WaterfallWidget } from "./Waterfall";
export type { WaterfallConfig } from "./Waterfall";
