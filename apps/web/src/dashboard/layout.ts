// G26: the dashboard grid's geometry, and the layout document's read/write.
//
// The board is a 12-column grid of fixed-height rows. A widget's saved
// `layout` is its cell {x, y, w, h} in columns and rows
// (server/finance/dashboard.py, _check_widget). `pack` settles every widget
// as high as it will fit without overlapping, so a drag or a removal never
// leaves a hole; the same widgets in the same order always pack to the same
// cells, so a saved board redraws exactly.
import type { DashboardLayout, DashboardWidget } from "../api";

export const COLS = 12;

/** A widget is never smaller than this; a resize clamps to it. */
export const MIN_W = 2;
export const MIN_H = 1;

/** The cell a widget takes when its saved layout says nothing. */
export const DEFAULT_CELL: Cell = { x: 0, y: 0, w: 4, h: 2 };

export type Cell = { x: number; y: number; w: number; h: number };

const int = (v: unknown, fallback: number) =>
  typeof v === "number" && Number.isInteger(v) ? v : fallback;

/** A cell held inside the board: whole numbers, x within the columns, y at or under the top. */
export function clampCell(c: Cell): Cell {
  const w = Math.min(Math.max(Math.round(c.w), MIN_W), COLS);
  const h = Math.max(Math.round(c.h), MIN_H);
  return {
    x: Math.min(Math.max(Math.round(c.x), 0), COLS - w),
    y: Math.max(Math.round(c.y), 0),
    w, h,
  };
}

/** The cell a saved widget sits in, the defaults where its layout field is short or missing. */
export function cellOf(w: DashboardWidget): Cell {
  const g = w.layout ?? {};
  return clampCell({ x: int(g.x, DEFAULT_CELL.x), y: int(g.y, DEFAULT_CELL.y), w: int(g.w, DEFAULT_CELL.w), h: int(g.h, DEFAULT_CELL.h) });
}

/** Two cells that share a row and a column's space overlap. */
export function collides(a: Cell, b: Cell): boolean {
  return a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
}

/**
 * Settle the widgets: `pinned` keeps the cell it is given (the one being
 * dragged or resized), the rest take the highest cell at their own column
 * where nothing already placed sits, tried in stacking order (y, then x).
 */
export function pack(widgets: DashboardWidget[], pinned?: { id: string; cell: Cell }): DashboardWidget[] {
  const taken: Cell[] = [];
  const cells = new Map<string, Cell>();
  if (pinned) {
    const c = clampCell(pinned.cell);
    cells.set(pinned.id, c);
    taken.push(c);
  }
  const order = [...widgets].sort((a, b) => {
    const ac = cellOf(a), bc = cellOf(b);
    return ac.y - bc.y || ac.x - bc.x;
  });
  for (const w of order) {
    if (w.id === pinned?.id) continue;
    const want = cellOf(w);
    let c = { ...want, y: 0 };
    while (taken.some((t) => collides(c, t))) c = { ...c, y: c.y + 1 };
    cells.set(w.id, c);
    taken.push(c);
  }
  return widgets.map((w) => ({ ...w, layout: { ...cells.get(w.id)! } }));
}

/** The highest cell a new widget of `size` fits into, beside what is already placed. */
export function firstFit(widgets: DashboardWidget[], size: { w: number; h: number }): Cell {
  const taken = widgets.map(cellOf);
  const base = clampCell({ x: 0, y: 0, w: size.w, h: size.h });
  for (let y = 0; ; y++) {
    for (let x = 0; x + base.w <= COLS; x++) {
      const c = { ...base, x, y };
      if (!taken.some((t) => collides(c, t))) return c;
    }
  }
}

/** The row the widgets end at, for the grid's own height. */
export function bottomOf(widgets: DashboardWidget[]): number {
  return widgets.reduce((b, w) => Math.max(b, cellOf(w).y + cellOf(w).h), 0);
}

/** One layout as the page edits it. */
export type Board = { id: string; name: string; widgets: DashboardWidget[] };

/**
 * A saved layout as the board holds it: widgets without an id or a type
 * dropped, duplicate ids kept once, every cell whole and inside the grid,
 * widgets settled. Reading then writing (`writeLayout`) is idempotent.
 */
export function readLayout(d: DashboardLayout): Board {
  const seen = new Set<string>();
  const widgets = (d.widgets ?? []).flatMap((w) => {
    if (!w || typeof w.id !== "string" || !w.id || typeof w.type !== "string" || !w.type || seen.has(w.id)) return [];
    seen.add(w.id);
    const out: DashboardWidget = { id: w.id, type: w.type, layout: cellOf(w) };
    if (w.title != null) out.title = w.title;
    if (w.query != null) out.query = w.query;
    if (w.options != null) out.options = w.options;
    return [out];
  });
  return { id: d.id, name: d.name, widgets: pack(widgets) };
}

/** The board as POST /api/dashboard/layouts/<id> takes it: each widget's cell as plain {x, y, w, h}. */
export function writeLayout(b: Board): { name: string; widgets: DashboardWidget[] } {
  return { name: b.name, widgets: b.widgets.map((w) => ({ ...w, layout: { ...cellOf(w) } })) };
}

let serial = 0;

/** A widget id the server takes: lowercase letters, digits, '-' and '_' only (its _check_id). */
export function newWidgetId(): string {
  serial = (serial + 1) % (36 * 36 * 36);
  return `w${Date.now().toString(36)}${serial.toString(36).padStart(2, "0")}`;
}
