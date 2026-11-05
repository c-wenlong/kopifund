// G26: the widget registry: a saved widget's `type` id to the component that
// draws it, the words the Add widget sheet shows, and the cell and query it
// starts with. The chart widgets land under dashboard/widgets/ in the issues
// that follow this one (#7, #8, #9) and register here; the placeholders
// (placeholders.tsx) hold the type ids until then. A type id no entry knows
// (a layout saved by a newer app) draws an unknown-tile card, never a crash.
import type { ComponentType } from "react";
import type { DashboardWidget, WidgetQuery } from "../api";
import { KpiWidget, PieWidget, SeriesWidget, TextWidget } from "./placeholders";

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
    blurb: "One number and its note: a balance, a runway, a month's spend.",
    size: { w: 4, h: 2 },
    make: () => ({ query: { source: "net-worth", metric: "total", dimension: "month" } }),
    component: KpiWidget,
  },
  {
    type: "line", title: "A line over months",
    blurb: "How a figure moved: spend, income, net or net worth by month.",
    size: { w: 8, h: 3 },
    make: () => ({ query: { source: "spending", metric: "spend", dimension: "month" } }),
    component: SeriesWidget,
  },
  {
    type: "pie", title: "A split",
    blurb: "Where a figure sits, spread over buckets, categories or payees.",
    size: { w: 4, h: 3 },
    make: () => ({ query: { source: "spending", metric: "spend", dimension: "bucket" } }),
    component: PieWidget,
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
