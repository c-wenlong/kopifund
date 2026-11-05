// G26: the board's grid. Twelve columns of fixed-height rows; a widget is
// dragged by the grip in its header and resized from its corner, every move
// repacked by layout.ts and handed to onArrange once on drop. With
// `interactive` off (a phone's width) the same widgets stack in one column in
// their saved order, keeping only their remove buttons. Drag feedback is a
// dashed outline and a ring: no motion, so reduced-motion changes nothing.
import { useRef, useState } from "react";
import { GripVertical, X } from "lucide-react";
import type { DashboardWidget } from "../api";
import { cellOf, clampCell, COLS, pack, type Cell } from "./layout";
import { widgetDef } from "./registry";
import { ErrorBoundary } from "../components/ErrorBoundary";
import { Problem } from "../components/States";

/** Audit: what a widget that throws as it draws shows in its card; the rest of the board keeps working. */
export const WIDGET_FAILED = "This widget failed to draw.";

/** One arrow-key step, as the [x, y] or [w, h] it nudges. */
const ARROW: Record<string, readonly [number, number]> = {
  ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1],
};

/** A grid row's height and the gap between cells, the px the pointer math shares with the style below. */
const ROW_PX = 84;
const GAP_PX = 12;

type Drag = {
  id: string;
  kind: "move" | "resize";
  pointerId: number;
  /** The cell the drag started from (resize keeps its x and y). */
  start: Cell;
  /** The pointer's offset into the cell when the drag began, in cell units (a move only). */
  grab: { dx: number; dy: number };
  /** The cell it would land on now. */
  cell: Cell;
};

export function Grid({ widgets, interactive, onArrange, onRemove }: {
  widgets: DashboardWidget[];
  /** Drag and resize are on at desktop width; under it the widgets stack in saved order. */
  interactive: boolean;
  onArrange: (widgets: DashboardWidget[]) => void;
  onRemove: (id: string) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [drag, setDrag] = useState<Drag | null>(null);

  const metrics = () => {
    const r = ref.current!.getBoundingClientRect();
    return { left: r.left, top: r.top, colW: r.width / COLS, rowH: ROW_PX + GAP_PX };
  };

  const begin = (e: React.PointerEvent, w: DashboardWidget, kind: Drag["kind"]) => {
    if (!interactive || !ref.current) return;
    e.preventDefault();
    e.currentTarget.setPointerCapture(e.pointerId);
    const m = metrics();
    const c = cellOf(w);
    const grab = kind === "move"
      ? { dx: (e.clientX - m.left) / m.colW - c.x, dy: (e.clientY - m.top) / m.rowH - c.y }
      : { dx: 0, dy: 0 };
    setDrag({ id: w.id, kind, pointerId: e.pointerId, start: c, grab, cell: c });
  };

  const move = (e: React.PointerEvent) => {
    if (!drag || e.pointerId !== drag.pointerId || !ref.current) return;
    const m = metrics();
    const cell = drag.kind === "move"
      ? clampCell({ ...drag.start, x: (e.clientX - m.left) / m.colW - drag.grab.dx, y: (e.clientY - m.top) / m.rowH - drag.grab.dy })
      : clampCell({
        ...drag.start,
        w: (e.clientX - m.left) / m.colW - drag.start.x + 0.5,
        h: (e.clientY - m.top) / m.rowH - drag.start.y + 0.5,
      });
    if (cell.x !== drag.cell.x || cell.y !== drag.cell.y || cell.w !== drag.cell.w || cell.h !== drag.cell.h) {
      setDrag({ ...drag, cell });
    }
  };

  const end = (e: React.PointerEvent) => {
    if (!drag || e.pointerId !== drag.pointerId) return;
    setDrag(null);
    onArrange(settle(widgets, drag.id, drag.cell));
  };

  // the moved or resized widget takes the cell it was given, then settles into
  // the highest free row like every read of the layout does (pack without a
  // pin), so the saved echo redraws the same board rather than jumping
  const settle = (ws: DashboardWidget[], id: string, cell: Cell) =>
    pack(ws.map((v) => (v.id === id ? { ...v, layout: { ...clampCell(cell) } } : v)));

  // the same moves as a drag, for the keyboard: arrows on the Move grip shift
  // the widget one cell, on the Resize corner they grow or shrink it. Up and
  // Down hop past the nearest widget that shares the columns in that
  // direction, since every widget already rests on the highest free row
  const nudge = (w: DashboardWidget, kind: Drag["kind"], e: React.KeyboardEvent) => {
    const d = ARROW[e.key];
    if (!d) return;
    e.preventDefault();
    const c = cellOf(w);
    if (kind === "resize") {
      onArrange(settle(widgets, w.id, { ...c, w: c.w + d[0], h: c.h + d[1] }));
      return;
    }
    if (d[0] !== 0) {
      onArrange(settle(widgets, w.id, { ...c, x: c.x + d[0] }));
      return;
    }
    const beside = widgets.filter((v) => v.id !== w.id)
      .map((v) => ({ v, t: cellOf(v) }))
      .filter(({ t }) => t.x < c.x + c.w && c.x < t.x + t.w);
    if (d[1] > 0) {
      const below = beside.filter(({ t }) => t.y >= c.y + c.h).sort((a, b) => a.t.y - b.t.y)[0];
      onArrange(settle(widgets, w.id, { ...c, y: below ? below.t.y + below.t.h : c.y + 1 }));
      return;
    }
    const above = beside.filter(({ t }) => t.y + t.h <= c.y)
      .sort((a, b) => b.t.y + b.t.h - (a.t.y + a.t.h))[0];
    // sorting first at a tie needs the widget ahead of the blocker in the list too
    let list = widgets;
    if (above) {
      list = widgets.filter((v) => v.id !== w.id);
      list.splice(list.findIndex((v) => v.id === above.v.id), 0, w);
    }
    onArrange(settle(list, w.id, { ...c, y: above ? above.t.y : 0 }));
  };

  if (!interactive) {
    const order = [...widgets].sort((a, b) => {
      const ac = cellOf(a), bc = cellOf(b);
      return ac.y - bc.y || ac.x - bc.x;
    });
    return (
      <div className="flex flex-col gap-3">
        {order.map((w) => <WidgetCard key={w.id} widget={w} onRemove={onRemove} />)}
      </div>
    );
  }

  // during a drag the board shows where everything would land, repacked around the held cell
  const placed = drag ? pack(widgets, { id: drag.id, cell: drag.cell }) : widgets;
  return (
    <div ref={ref}
      className="grid gap-3"
      style={{ gridTemplateColumns: `repeat(${COLS}, minmax(0, 1fr))`, gridAutoRows: `${ROW_PX}px` }}
      role="group" aria-label="Dashboard widgets">
      {drag && (
        <div aria-hidden
          className="rounded-xl border-2 border-dashed border-edge"
          style={{ gridColumn: `${drag.cell.x + 1} / span ${drag.cell.w}`, gridRow: `${drag.cell.y + 1} / span ${drag.cell.h}` }} />
      )}
      {placed.map((w) => (
        <WidgetCard key={w.id} widget={w} cell={cellOf(w)} dragging={drag?.id === w.id}
          onMove={(e) => begin(e, w, "move")} onResize={(e) => begin(e, w, "resize")}
          onKeyMove={(e) => nudge(w, "move", e)} onKeyResize={(e) => nudge(w, "resize", e)}
          onDragMove={move} onDragEnd={end} onRemove={onRemove} />
      ))}
    </div>
  );
}

function WidgetCard({ widget, cell, dragging = false, onMove, onResize, onKeyMove, onKeyResize, onDragMove, onDragEnd, onRemove }: {
  widget: DashboardWidget;
  cell?: Cell;                       // given in the grid; a stacked card sizes itself
  dragging?: boolean;
  onMove?: (e: React.PointerEvent) => void;
  onResize?: (e: React.PointerEvent) => void;
  onKeyMove?: (e: React.KeyboardEvent) => void;
  onKeyResize?: (e: React.KeyboardEvent) => void;
  onDragMove?: (e: React.PointerEvent) => void;
  onDragEnd?: (e: React.PointerEvent) => void;
  onRemove: (id: string) => void;
}) {
  const def = widgetDef(widget.type);
  const title = widget.title ?? def?.title ?? widget.type;
  const Body = def?.component;
  const editing = !!onMove;
  // where the widget sits, said aloud when an arrow moves it and in the grips' names
  const where = cell ? `column ${cell.x + 1}, row ${cell.y + 1}, ${cell.w} by ${cell.h}` : "";
  return (
    <article
      aria-label={title}
      style={cell ? { gridColumn: `${cell.x + 1} / span ${cell.w}`, gridRow: `${cell.y + 1} / span ${cell.h}` } : undefined}
      className={`relative min-h-0 ${dragging ? "z-10" : ""}`}>
      <section className={`flex h-full min-h-0 flex-col rounded-xl border bg-panel px-4 py-3 ${dragging ? "border-info" : "border-line"}`}>
        <div className="mb-2 flex items-center gap-1.5">
          {editing && (
            <button type="button" aria-label={`Move ${title}: ${where}`}
              onPointerDown={onMove} onPointerMove={onDragMove} onPointerUp={onDragEnd} onPointerCancel={onDragEnd}
              onKeyDown={onKeyMove}
              className="-ml-1 grid size-7 shrink-0 cursor-grab touch-none place-items-center rounded-md text-ink-3 hover:bg-hover hover:text-ink active:cursor-grabbing">
              <GripVertical size={15} aria-hidden />
            </button>
          )}
          <h2 className="m-0 min-w-0 flex-1 truncate text-[14px] font-semibold">{title}</h2>
          <button type="button" aria-label={`Remove ${title}`} onClick={() => onRemove(widget.id)}
            className="grid size-7 shrink-0 place-items-center rounded-md text-ink-3 hover:bg-hover hover:text-ink">
            <X size={15} aria-hidden />
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-hidden">
          {Body ? (
            // Audit: one widget that cannot draw its answer stays in its card; a changed query or option starts it clean
            <ErrorBoundary resetKey={JSON.stringify([widget.type, widget.query, widget.options])}
              fallback={(reset) => <Problem message={WIDGET_FAILED} onRetry={reset} />}>
              <Body widget={widget} />
            </ErrorBoundary>
          ) : (
            <p className="m-0 text-[13px] text-ink-3">
              This widget's kind, {widget.type}, is not one this build knows. Its saved place is kept.
            </p>
          )}
        </div>
        {editing && (
          <button type="button" aria-label={`Resize ${title}: ${where}`}
            onPointerDown={onResize} onPointerMove={onDragMove} onPointerUp={onDragEnd} onPointerCancel={onDragEnd}
            onKeyDown={onKeyResize}
            className="absolute right-1.5 bottom-1.5 size-4 cursor-nwse-resize touch-none rounded-br-md border-r-2 border-b-2 border-ink-3 hover:border-ink" />
        )}
        {editing && cell && <p className="sr-only" role="status">{`${title}: ${where}`}</p>}
      </section>
    </article>
  );
}
