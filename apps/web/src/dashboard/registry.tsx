// G26: the widget registry: a saved widget's `type` id to the component that
// draws it, the words the Add widget sheet shows, and the cell and query it
// starts with. The components are the chart widgets under dashboard/widgets/
// (state, change, flow), bound to a saved row's `query` and `options` by the
// adapters in bind.tsx; `note` keeps its own line of text. A type id no entry
// knows (a layout saved by a newer app) draws an unknown-tile card, never a
// crash.
import type { ComponentType } from "react";
import type { DashboardWidget, WidgetQuery } from "../api";
import {
  BoundArea, BoundFlow, BoundKpi, BoundPie, BoundSeries, BoundTreemap, BoundWaterfall,
} from "./bind";
import { TextWidget } from "./note";
import { DEFAULT_STACKED_AREA_CONFIG, DEFAULT_WATERFALL_CONFIG } from "./widgets/change";
import { defaultFlowConfig } from "./widgets/flow";

/** What a widget component is given: its saved row, unchanged. */
export type WidgetProps = { widget: DashboardWidget };

export type WidgetDef = {
  /** The type id a saved layout names. */
  type: string;
  /** Its default card title, and its name in the Add widget sheet. */
  title: string;
  /** What it shows, in the sheet's words. */
  blurb: string;
  /** The size it starts at, added at the grid's first free cell. */
  size: { w: number; h: number };
  /** The query and options it starts bound to, when it has defaults. */
  make?: () => { query?: WidgetQuery; options?: Record<string, unknown> };
  component: ComponentType<WidgetProps>;
};

export const WIDGETS: readonly WidgetDef[] = [
  {
    type: "kpi", title: "A figure",
    blurb: "One number and its change: a balance, a runway, a month's spend.",
    size: { w: 4, h: 2 },
    make: () => ({
      query: { source: "net-worth", metric: "total", dimension: "month" },
      options: { delta: true, sparkline: true, goodWhen: "up" },
    }),
    component: BoundKpi,
  },
  {
    type: "line", title: "A line over months",
    blurb: "How a figure moved: spend, income, net or net worth by month.",
    size: { w: 8, h: 3 },
    make: () => ({ query: { source: "spending", metric: "spend", dimension: "month" } }),
    component: BoundSeries,
  },
  {
    type: "area", title: "A composition over time",
    blurb: "What a figure is made of, piled up by month: net worth by part.",
    size: { w: 8, h: 3 },
    make: () => ({ options: { series: DEFAULT_STACKED_AREA_CONFIG.series } }),
    component: BoundArea,
  },
  {
    type: "waterfall", title: "A bridge between balances",
    blurb: "How a balance got from its first figure to its last, one step a flow.",
    size: { w: 8, h: 3 },
    make: () => ({ options: { balance: DEFAULT_WATERFALL_CONFIG.balance, flows: DEFAULT_WATERFALL_CONFIG.flows } }),
    component: BoundWaterfall,
  },
  {
    type: "pie", title: "A split",
    blurb: "Where a figure sits, spread over buckets, categories or payees.",
    size: { w: 4, h: 3 },
    make: () => ({
      query: { source: "spending", metric: "spend", dimension: "bucket" },
      options: { donut: false },
    }),
    component: BoundPie,
  },
  {
    type: "donut", title: "A ring",
    blurb: "A split with the whole's figure in the middle.",
    size: { w: 4, h: 3 },
    make: () => ({
      query: { source: "spending", metric: "spend", dimension: "bucket" },
      options: { donut: true, showTotal: true },
    }),
    component: BoundPie,
  },
  {
    type: "treemap", title: "A nested split",
    blurb: "Where a figure sits, sized tiles grouped under their buckets.",
    size: { w: 6, h: 4 },
    make: () => ({
      query: { source: "spending", metric: "spend", dimension: "category" },
      options: { groupBy: "bucket" },
    }),
    component: BoundTreemap,
  },
  {
    type: "flow", title: "Money flow",
    blurb: "Income into buckets and categories for the range, as a Sankey.",
    size: { w: 12, h: 4 },
    make: () => ({
      options: {
        middle: defaultFlowConfig.middle,
        maxCategories: defaultFlowConfig.maxCategories,
        transfers: defaultFlowConfig.transfers,
      },
    }),
    component: BoundFlow,
  },
  {
    type: "note", title: "A note",
    blurb: "A line of text kept on the board.",
    size: { w: 4, h: 2 },
    make: () => ({ options: { text: "" } }),
    component: TextWidget,
  },
];

/** The registry entry for a saved `type`, or undefined for a type this build does not know. */
export function widgetDef(type: string): WidgetDef | undefined {
  return WIDGETS.find((d) => d.type === type);
}
