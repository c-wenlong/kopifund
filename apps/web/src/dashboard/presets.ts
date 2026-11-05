// G26: the starter boards the empty Dashboard offers. `PRESETS[0]` is the
// documented default Reset restores: a figure to read, a line to follow and a
// split to explain. Each `widgets()` makes fresh ids, so applying a preset
// twice never collides.
import type { DashboardWidget, WidgetQuery } from "../api";
import { newWidgetId, pack, type Cell } from "./layout";
import { widgetDef } from "./registry";

export type Preset = {
  id: string;
  name: string;
  /** What the board is for, in the empty state's words. */
  blurb: string;
  widgets: () => DashboardWidget[];
};

function spawn(type: string, cell: Cell, title?: string, query?: WidgetQuery,
  options?: Record<string, unknown>): DashboardWidget {
  const def = widgetDef(type);
  const w: DashboardWidget = { id: newWidgetId(), type, layout: { ...cell } };
  const t = title ?? def?.title;
  if (t) w.title = t;
  const made = def?.make?.();
  if (query ?? made?.query) w.query = (query ?? made?.query)!;
  const opts = { ...made?.options, ...options };
  if (Object.keys(opts).length) w.options = opts;
  return w;
}

export const PRESETS: readonly Preset[] = [
  {
    id: "default",
    name: "Default",
    blurb: "Net worth, runway and last month's spend up top; the spend's line and split below.",
    widgets: () => pack([
      spawn("kpi", { x: 0, y: 0, w: 4, h: 2 }, "Net worth"),
      spawn("kpi", { x: 4, y: 0, w: 4, h: 2 }, "Cash runway",
        { source: "savings-runway", metric: "runway_months", dimension: "month" }),
      spawn("kpi", { x: 8, y: 0, w: 4, h: 2 }, "Spend by month",
        { source: "spending", metric: "spend", dimension: "month" },
        // spend falling reads well; the type's default ("up") is for balances
        { goodWhen: "down" }),
      spawn("line", { x: 0, y: 2, w: 8, h: 3 }, "Monthly spend"),
      spawn("pie", { x: 8, y: 2, w: 4, h: 3 }, "Spend by bucket"),
    ]),
  },
  {
    id: "spending",
    name: "Spending",
    blurb: "The spend line over a year, then who and what it went to.",
    widgets: () => pack([
      spawn("line", { x: 0, y: 0, w: 12, h: 3 }, "Monthly spend"),
      spawn("pie", { x: 0, y: 3, w: 6, h: 3 }, "By category",
        { source: "spending", metric: "spend", dimension: "category" }),
      spawn("pie", { x: 6, y: 3, w: 6, h: 3 }, "By payee",
        { source: "spending", metric: "spend", dimension: "payee" }),
    ]),
  },
  {
    id: "planning",
    name: "Planning",
    blurb: "How long the cash lasts, and the forward view's estimate of the months ahead.",
    widgets: () => pack([
      spawn("kpi", { x: 0, y: 0, w: 4, h: 2 }, "Cash runway",
        { source: "savings-runway", metric: "runway_months", dimension: "month" }),
      spawn("line", { x: 4, y: 0, w: 8, h: 3 }, "The months ahead",
        { source: "forward-view", metric: "net", dimension: "month" }),
      spawn("line", { x: 0, y: 3, w: 6, h: 3 }, "Saved by month",
        { source: "needs-wants-savings", metric: "savings", dimension: "month" }),
      spawn("note", { x: 6, y: 3, w: 6, h: 2 }, "A note"),
    ]),
  },
];

/** The preset Reset restores (SPEC.md, "The dashboard"). */
export const DEFAULT_PRESET = PRESETS[0];
