import { fireEvent, render, screen } from "@testing-library/react";
import type { DashboardWidget } from "../api";
import { Grid } from "./Grid";
import { cellOf } from "./layout";

// G26: the drag grid's drop and its keyboard moves. jsdom has no layout, so the
// grid's box is stubbed to a fixed size; all figures and names are invented.

const ROW = 84 + 12;             // Grid.tsx's ROW_PX + GAP_PX
const COL = 1200 / 12;           // the stubbed width over the grid's columns

const widget = (id: string, cell: { x: number; y: number; w: number; h: number }): DashboardWidget =>
  ({ id, type: "note", title: `Note ${id}`, options: { text: `text ${id}` }, layout: { ...cell } });

function renderGrid(widgets: DashboardWidget[], onArrange = vi.fn()) {
  render(<Grid widgets={widgets} interactive onArrange={onArrange} onRemove={() => {}} />);
  return onArrange;
}

const arranged = (onArrange: ReturnType<typeof vi.fn>, id: string) =>
  cellOf((onArrange.mock.calls.at(-1)![0] as DashboardWidget[]).find((w) => w.id === id)!);

// jsdom has no PointerEvent, and testing-library's fallback drops clientX and
// pointerId, which the drag math needs; a MouseEvent-shaped stand-in carries both
if (!("PointerEvent" in window)) {
  class FakePointerEvent extends MouseEvent {
    readonly pointerId: number;
    readonly pointerType: string;
    constructor(type: string, init: PointerEventInit = {}) {
      super(type, init);
      this.pointerId = init.pointerId ?? 0;
      this.pointerType = init.pointerType ?? "mouse";
    }
  }
  Object.defineProperty(window, "PointerEvent", { value: FakePointerEvent });
}

beforeEach(() => {
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue(
    { left: 0, top: 0, width: 1200, height: 800, right: 1200, bottom: 800, x: 0, y: 0, toJSON: () => ({}) } as DOMRect);
  const el = HTMLElement.prototype as unknown as Record<string, unknown>;
  el.setPointerCapture ??= () => {};
  el.releasePointerCapture ??= () => {};
});

describe("Grid", () => {
  it("settles a drop into the highest free row at once, and saves nothing when that is where it was", () => {
    const onArrange = renderGrid([widget("w-a", { x: 0, y: 0, w: 4, h: 2 }), widget("w-b", { x: 4, y: 0, w: 4, h: 2 })]);
    const grip = screen.getByRole("button", { name: /Move Note w-a/ });
    // drag the widget five rows down, then drop it: it settles at the top, the
    // same cells the layout's own read would give it, so the board is unchanged
    fireEvent.pointerDown(grip, { pointerId: 1, clientX: 5, clientY: 5 });
    fireEvent.pointerMove(grip, { pointerId: 1, clientX: 5, clientY: 5 + 5 * ROW });
    fireEvent.pointerUp(grip, { pointerId: 1, clientX: 5, clientY: 5 + 5 * ROW });
    expect(onArrange).not.toHaveBeenCalled();
    // dropped under the other widget's column it settles at the top beside it, at once
    fireEvent.pointerDown(grip, { pointerId: 1, clientX: 5, clientY: 5 });
    fireEvent.pointerMove(grip, { pointerId: 1, clientX: 5 + 8 * COL, clientY: 5 + 5 * ROW });
    fireEvent.pointerUp(grip, { pointerId: 1, clientX: 5 + 8 * COL, clientY: 5 + 5 * ROW });
    expect(onArrange).toHaveBeenCalledTimes(1);
    expect(arranged(onArrange, "w-a")).toEqual({ x: 8, y: 0, w: 4, h: 2 });
  });

  it("saves nothing for an arrow that cannot move the widget", () => {
    const onArrange = renderGrid([widget("w-a", { x: 0, y: 0, w: 4, h: 2 })]);
    const grip = screen.getByRole("button", { name: /Move Note w-a/ });
    fireEvent.keyDown(grip, { key: "ArrowUp" });
    fireEvent.keyDown(grip, { key: "ArrowLeft" });
    expect(onArrange).not.toHaveBeenCalled();
  });

  it("lets go of a drag the pointer cancelled or Escape dropped, leaving the board as it was", () => {
    const onArrange = renderGrid([widget("w-a", { x: 0, y: 0, w: 4, h: 2 })]);
    const grip = screen.getByRole("button", { name: /Move Note w-a/ });
    fireEvent.pointerDown(grip, { pointerId: 1, clientX: 5, clientY: 5 });
    fireEvent.pointerMove(grip, { pointerId: 1, clientX: 5 + 6 * COL, clientY: 5 });
    fireEvent.pointerCancel(grip, { pointerId: 1 });
    fireEvent.pointerUp(grip, { pointerId: 1, clientX: 5 + 6 * COL, clientY: 5 });
    expect(onArrange).not.toHaveBeenCalled();
    fireEvent.pointerDown(grip, { pointerId: 2, clientX: 5, clientY: 5 });
    fireEvent.pointerMove(grip, { pointerId: 2, clientX: 5 + 6 * COL, clientY: 5 });
    fireEvent.keyDown(window, { key: "Escape" });
    fireEvent.pointerUp(grip, { pointerId: 2, clientX: 5 + 6 * COL, clientY: 5 });
    expect(onArrange).not.toHaveBeenCalled();
  });

  it("drops a widget dragged up onto a top-row widget above it, as the preview showed", () => {
    // w-a is first in the list, so a repack of the board from before the drag would keep it on top
    const onArrange = renderGrid([
      widget("w-a", { x: 0, y: 0, w: 6, h: 2 }),
      widget("w-b", { x: 0, y: 2, w: 6, h: 2 }),
    ]);
    const grip = screen.getByRole("button", { name: /Move Note w-b/ });
    fireEvent.pointerDown(grip, { pointerId: 1, clientX: 5, clientY: 5 + 2 * ROW });
    fireEvent.pointerMove(grip, { pointerId: 1, clientX: 5, clientY: 5 });
    fireEvent.pointerUp(grip, { pointerId: 1, clientX: 5, clientY: 5 });
    expect(arranged(onArrange, "w-b")).toEqual({ x: 0, y: 0, w: 6, h: 2 });
    expect(arranged(onArrange, "w-a")).toEqual({ x: 0, y: 2, w: 6, h: 2 });
  });

  it("keeps the dropped column while settling upwards", () => {
    const onArrange = renderGrid([widget("w-a", { x: 0, y: 0, w: 4, h: 2 })]);
    const grip = screen.getByRole("button", { name: /Move Note w-a/ });
    fireEvent.pointerDown(grip, { pointerId: 1, clientX: 5, clientY: 5 });
    fireEvent.pointerMove(grip, { pointerId: 1, clientX: 5 + 6 * COL, clientY: 5 + 5 * ROW });
    fireEvent.pointerUp(grip, { pointerId: 1, clientX: 5 + 6 * COL, clientY: 5 + 5 * ROW });
    expect(arranged(onArrange, "w-a")).toEqual({ x: 6, y: 0, w: 4, h: 2 });
  });

  it("moves a widget one cell per arrow key on the move grip", () => {
    const onArrange = renderGrid([widget("w-a", { x: 0, y: 0, w: 4, h: 2 })]);
    fireEvent.keyDown(screen.getByRole("button", { name: /Move Note w-a/ }), { key: "ArrowRight" });
    expect(arranged(onArrange, "w-a").x).toBe(1);
  });

  it("resizes a widget one cell per arrow key on the resize corner", () => {
    const onArrange = renderGrid([widget("w-a", { x: 0, y: 0, w: 4, h: 2 })]);
    fireEvent.keyDown(screen.getByRole("button", { name: /Resize Note w-a/ }), { key: "ArrowRight" });
    expect(arranged(onArrange, "w-a").w).toBe(5);
    fireEvent.keyDown(screen.getByRole("button", { name: /Resize Note w-a/ }), { key: "ArrowUp" });
    expect(arranged(onArrange, "w-a").h).toBe(1);
  });

  it("swaps two stacked widgets when the upper one is moved down", () => {
    const onArrange = renderGrid([
      widget("w-a", { x: 0, y: 0, w: 6, h: 2 }),
      widget("w-b", { x: 0, y: 2, w: 6, h: 2 }),
    ]);
    fireEvent.keyDown(screen.getByRole("button", { name: /Move Note w-a/ }), { key: "ArrowDown" });
    expect(arranged(onArrange, "w-a").y).toBe(2);
    expect(arranged(onArrange, "w-b").y).toBe(0);
  });

  it("swaps two stacked widgets when the lower one is moved up", () => {
    const onArrange = renderGrid([
      widget("w-a", { x: 0, y: 0, w: 6, h: 2 }),
      widget("w-b", { x: 0, y: 2, w: 6, h: 2 }),
    ]);
    fireEvent.keyDown(screen.getByRole("button", { name: /Move Note w-b/ }), { key: "ArrowUp" });
    expect(arranged(onArrange, "w-b").y).toBe(0);
    expect(arranged(onArrange, "w-a").y).toBe(2);
  });

  it("names the widget's place on its grips, and says aloud only where a moved widget landed", () => {
    renderGrid([widget("w-a", { x: 3, y: 0, w: 4, h: 2 }), widget("w-b", { x: 3, y: 2, w: 4, h: 2 })]);
    expect(screen.getByRole("button", { name: "Move Note w-a: column 4, row 1, 4 by 2" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Resize Note w-a: column 4, row 1, 4 by 2" })).toBeInTheDocument();
    // one status line for the board, silent until something moves
    expect(screen.getAllByRole("status")).toHaveLength(1);
    expect(screen.getByRole("status")).toHaveTextContent("");
    fireEvent.keyDown(screen.getByRole("button", { name: /Move Note w-b/ }), { key: "ArrowRight" });
    expect(screen.getByRole("status")).toHaveTextContent("Note w-b: column 5, row 3, 4 by 2");
  });
});
