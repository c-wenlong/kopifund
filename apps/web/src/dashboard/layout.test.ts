import { describe, expect, it } from "vitest";
import type { DashboardLayout, DashboardWidget } from "../api";
import { cellOf, clampCell, COLS, collides, firstFit, MAX_H, newWidgetId, pack, readLayout, writeLayout } from "./layout";

// an invented layout fixture: the document GET /api/dashboard/layouts answers
// and POST writes back, with one widget of each field kind
const DOC: DashboardLayout = {
  id: "default",
  name: "Default",
  updated_at: "2026-10-01T02:03:04+00:00",
  widgets: [
    {
      id: "w-net", type: "kpi", title: "Net worth",
      query: { source: "net-worth", metric: "total", dimension: "month" },
      layout: { x: 0, y: 0, w: 4, h: 2 },
    },
    {
      id: "w-spend", type: "line",
      query: { source: "spending", metric: "spend", dimension: "month" },
      layout: { x: 4, y: 0, w: 8, h: 3 },
    },
    {
      id: "w-note", type: "note", title: "A note",
      options: { text: "invented words" },
      layout: { x: 0, y: 2, w: 4, h: 2 },
    },
  ],
};

const widget = (id: string, cell: { x: number; y: number; w: number; h: number }): DashboardWidget =>
  ({ id, type: "note", layout: { ...cell } });

describe("readLayout / writeLayout", () => {
  it("round-trips a saved layout: reading then writing is the document itself", () => {
    const board = readLayout(DOC);
    expect(board.id).toBe("default");
    expect(board.name).toBe("Default");
    const written = writeLayout(board);
    expect(JSON.parse(JSON.stringify(written))).toEqual({
      name: DOC.name,
      widgets: DOC.widgets.map((w) => ({ ...w, layout: { ...w.layout } })),
    });
  });

  it("is stable: a doc it writes reads back to the same widgets", () => {
    const once = writeLayout(readLayout(DOC));
    const twice = writeLayout(readLayout({ ...DOC, widgets: once.widgets }));
    expect(twice).toEqual(once);
  });

  it("drops widgets with no id or type and keeps a duplicated id once", () => {
    const board = readLayout({
      ...DOC,
      widgets: [
        ...DOC.widgets,
        { id: "w-net", type: "pie", layout: { x: 0, y: 9, w: 4, h: 2 } } as DashboardWidget,
        { id: "", type: "kpi" } as DashboardWidget,
        { id: "w-x" } as DashboardWidget,
      ],
    });
    expect(board.widgets.map((w) => w.id)).toEqual(["w-net", "w-spend", "w-note"]);
  });

  it("fills a missing cell with the defaults and clamps a stray one into the grid", () => {
    const board = readLayout({
      ...DOC,
      widgets: [
        { id: "w-a", type: "kpi" },
        { id: "w-b", type: "kpi", layout: { x: 11, y: -2, w: 99, h: 0 } },
      ],
    });
    const a = cellOf(board.widgets[0]);
    expect(a.w).toBe(4);
    expect(a.h).toBe(2);
    const b = cellOf(board.widgets[1]);
    expect(b.x + b.w).toBeLessThanOrEqual(COLS);
    expect(b.y).toBeGreaterThanOrEqual(0);
    expect(b.h).toBeGreaterThanOrEqual(1);
  });

  it("settles a layout that was saved with holes", () => {
    const board = readLayout({
      ...DOC,
      widgets: [widget("w-a", { x: 0, y: 5, w: 4, h: 2 }), widget("w-b", { x: 0, y: 9, w: 4, h: 2 })],
    });
    expect(cellOf(board.widgets[0]).y).toBe(0);
    expect(cellOf(board.widgets[1]).y).toBe(2);
  });

  it("bounds a saved height, so an absurd one cannot stall the page", () => {
    const board = readLayout({
      ...DOC,
      widgets: [
        widget("w-big", { x: 0, y: 0, w: 12, h: 1_000_000_000 }),
        widget("w-next", { x: 0, y: 0, w: 4, h: 2 }),
      ],
    });
    const big = cellOf(board.widgets.find((w) => w.id === "w-big")!);
    expect(big.h).toBe(MAX_H);
    // and the next widget settles just under the bounded one, a scan away not a billion
    const next = cellOf(board.widgets.find((w) => w.id === "w-next")!);
    expect(next.y).toBe(MAX_H);
  });
});

describe("pack", () => {
  it("pushes what the moved widget lands on down, and settles the rest", () => {
    const ws = [
      widget("a", { x: 0, y: 0, w: 6, h: 2 }),
      widget("b", { x: 0, y: 2, w: 6, h: 2 }),
    ];
    const out = pack(ws, { id: "b", cell: { x: 0, y: 0, w: 6, h: 2 } });
    expect(cellOf(out.find((w) => w.id === "b")!)).toEqual({ x: 0, y: 0, w: 6, h: 2 });
    expect(cellOf(out.find((w) => w.id === "a")!)).toEqual({ x: 0, y: 2, w: 6, h: 2 });
  });

  it("lets a moved widget sit beside another when the width is there", () => {
    const ws = [
      widget("a", { x: 0, y: 0, w: 6, h: 2 }),
      widget("b", { x: 0, y: 2, w: 6, h: 2 }),
    ];
    const out = pack(ws, { id: "b", cell: { x: 6, y: 0, w: 6, h: 2 } });
    expect(cellOf(out.find((w) => w.id === "b")!)).toEqual({ x: 6, y: 0, w: 6, h: 2 });
    expect(cellOf(out.find((w) => w.id === "a")!)).toEqual({ x: 0, y: 0, w: 6, h: 2 });
  });

  it("never leaves two widgets on the same cell", () => {
    const ws = [
      widget("a", { x: 0, y: 0, w: 4, h: 2 }),
      widget("b", { x: 4, y: 0, w: 4, h: 2 }),
      widget("c", { x: 8, y: 0, w: 4, h: 2 }),
    ];
    const out = pack(ws, { id: "a", cell: { x: 2, y: 0, w: 8, h: 2 } });
    const cells = out.map(cellOf);
    for (let i = 0; i < cells.length; i++) {
      for (let j = i + 1; j < cells.length; j++) expect(collides(cells[i], cells[j])).toBe(false);
    }
  });
});

describe("firstFit / clampCell / newWidgetId", () => {
  it("places a new widget in the first free cell, then below a full row", () => {
    const taken = [widget("a", { x: 0, y: 0, w: 12, h: 2 })];
    expect(firstFit(taken, { w: 4, h: 2 })).toEqual({ x: 0, y: 2, w: 4, h: 2 });
    expect(firstFit([...taken, widget("b", { x: 0, y: 2, w: 8, h: 2 })], { w: 4, h: 2 }))
      .toEqual({ x: 8, y: 2, w: 4, h: 2 });
  });

  it("clamps a cell into the grid: size at least the minimums, x within the columns", () => {
    expect(clampCell({ x: -3, y: -1, w: 99, h: 0 })).toEqual({ x: 0, y: 0, w: COLS, h: 1 });
    expect(clampCell({ x: 10, y: 4, w: 6, h: 2 })).toEqual({ x: COLS - 6, y: 4, w: 6, h: 2 });
  });

  it("clamps a cell's height to the bound", () => {
    expect(clampCell({ x: 0, y: 0, w: 4, h: 1_000_000_000 }).h).toBe(MAX_H);
    expect(clampCell({ x: 0, y: 0, w: 4, h: MAX_H }).h).toBe(MAX_H);
  });

  it("makes ids the server takes: lowercase, digits, '-' and '_'", () => {
    const ids = new Set([newWidgetId(), newWidgetId(), newWidgetId()]);
    expect(ids.size).toBe(3);
    for (const id of ids) expect(id).toMatch(/^[a-z0-9_-]{1,64}$/);
  });
});
