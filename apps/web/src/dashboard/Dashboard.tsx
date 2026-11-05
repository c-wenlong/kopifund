// G26: the Dashboard page: named layouts of widgets on a drag-and-drop grid
// (dashboard/Grid.tsx), read and written whole through
// /api/dashboard/layouts (server/finance/dashboard.py). The board edits a
// local copy; every change saves itself a beat later, so nothing on the page
// waits for a Save. An empty record starts from the presets (presets.ts), and
// Reset puts the default one back.
import { useCallback, useEffect, useRef, useState } from "react";
import { Plus, RotateCcw } from "lucide-react";
import { postJSON, useApi, type DashboardLayout, type DashboardLayouts, type DashboardWidget } from "../api";
import { Button } from "../components/Button";
import { CardSkeleton, Problem } from "../components/States";
import { EmptyState } from "../components/EmptyState";
import { PageActions } from "../components/PageActions";
import { Select } from "../components/Select";
import { Sheet } from "../components/Sheet";
import { showToast } from "../components/Toast";
import { useWide } from "../components/useWide";
import { useUnsavedGuard } from "../router";
import { Grid } from "./Grid";
import { firstFit, newWidgetId, pack, readLayout, writeLayout, type Board } from "./layout";
import { DEFAULT_PRESET, PRESETS } from "./presets";
import { widgetDef, WIDGETS } from "./registry";

const SAVE_AFTER = 700;

/** A layout id the server takes (`_check_id`): lowercase, digits, '-' and '_' only. */
function newLayoutId(taken: readonly string[]): string {
  for (let i = 0; ; i++) {
    const id = i === 0 ? "default" : `board-${Date.now().toString(36)}-${i}`;
    if (!taken.includes(id)) return id;
  }
}

export function Dashboard() {
  const wide = useWide();
  const res = useApi<DashboardLayouts>("/api/dashboard/layouts");
  const [boards, setBoards] = useState<Board[] | null>(null);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(false);

  // the latest state for the save timer and the toast callbacks, which run after this render
  const boardsRef = useRef(boards);
  boardsRef.current = boards;
  const activeRef = useRef(activeId);
  activeRef.current = activeId;
  const pending = useRef<Board | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // the updated_at of every layout a save of ours wrote: a reload that lands
  // before the write's echo reaches the cache answers the older document, and
  // adopting it would walk the board back
  const writtenAt = useRef(new Map<string, string>());

  // adopt the server's document while nothing is held unsaved; an edit of our
  // own would otherwise be clobbered by the reload our own save caused
  useEffect(() => {
    if (!res.data || dirty) return;
    setBoards(res.data.layouts.map((d) => {
      const kept = boardsRef.current?.find((x) => x.id === d.id);
      const wrote = writtenAt.current.get(d.id);
      return kept && wrote && d.updated_at <= wrote ? kept : readLayout(d);
    }));
    setActiveId((id) => res.data!.layouts.some((d) => d.id === id) ? id : res.data!.active ?? res.data!.layouts[0]?.id ?? null);
  }, [res.data, dirty]);

  const flush = useCallback(async () => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
    const b = pending.current;
    if (!b) return;
    pending.current = null;
    try {
      const written = await postJSON<DashboardLayout>(`/api/dashboard/layouts/${b.id}`,
        JSON.stringify({ ...writeLayout(b), active: true }));
      writtenAt.current.set(b.id, written.updated_at);
      // the write's own echo is the board's truth now, ahead of any read still on its way
      setBoards((bs) => (bs ?? []).map((x) => (x.id === b.id ? readLayout(written) : x)));
      setDirty(pending.current != null);
    } catch (e) {
      pending.current = b;
      showToast({
        message: e instanceof Error ? e.message : "The layout was not saved.", tone: "error",
        action: { label: "Try again", onClick: () => void flush() },
      });
    }
  }, []);

  const saveBoard = useCallback((b: Board) => {
    pending.current = b;
    setDirty(true);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => void flush(), SAVE_AFTER);
  }, [flush]);

  // a pending save still lands if the page is left before the guard asks
  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
    if (pending.current) {
      const b = pending.current;
      void postJSON(`/api/dashboard/layouts/${b.id}`, JSON.stringify({ ...writeLayout(b), active: true })).catch(() => {});
    }
  }, []);

  useUnsavedGuard(dirty);

  const board = boards?.find((b) => b.id === activeId) ?? boards?.[0] ?? null;

  /** Change the active board and save it. */
  const mutate = useCallback((fn: (b: Board) => Board) => {
    const b = boardsRef.current?.find((x) => x.id === activeRef.current) ?? boardsRef.current?.[0];
    if (!b) return;
    const next = fn(b);
    setBoards((bs) => (bs ?? []).map((x) => (x.id === next.id ? next : x)));
    saveBoard(next);
  }, [saveBoard]);

  /** Add a layout from a preset or empty, switch to it and save it. */
  const createBoard = useCallback((name: string, widgets: DashboardWidget[]) => {
    const b: Board = { id: newLayoutId((boardsRef.current ?? []).map((x) => x.id)), name, widgets: pack(widgets) };
    setBoards((bs) => [...(bs ?? []), b]);
    setActiveId(b.id);
    saveBoard(b);
  }, [saveBoard]);

  const addWidget = useCallback((type: string) => {
    const def = widgetDef(type);
    if (!def) return;
    setPickerOpen(false);
    mutate((b) => {
      const made = def.make?.() ?? {};
      const w: DashboardWidget = { id: newWidgetId(), type, title: def.title, layout: firstFit(b.widgets, def.size) };
      if (made.query) w.query = made.query;
      if (made.options) w.options = made.options;
      return { ...b, widgets: pack([...b.widgets, w]) };
    });
  }, [mutate]);

  const removeWidget = useCallback((id: string) => {
    const b = boardsRef.current?.find((x) => x.id === activeRef.current);
    const w = b?.widgets.find((x) => x.id === id);
    if (!w) return;
    mutate((x) => ({ ...x, widgets: x.widgets.filter((v) => v.id !== id) }));
    const name = w.title ?? widgetDef(w.type)?.title ?? "The widget";
    showToast({
      message: `${name} removed.`,
      action: { label: "Undo", onClick: () => mutate((x) => ({ ...x, widgets: pack([...x.widgets, w]) })) },
    });
  }, [mutate]);

  const reset = useCallback(() => {
    const b = boardsRef.current?.find((x) => x.id === activeRef.current);
    if (!b) return;
    const before = b.widgets;
    mutate((x) => ({ ...x, widgets: DEFAULT_PRESET.widgets() }));
    showToast({
      message: `${b.name} reset to ${DEFAULT_PRESET.name}.`,
      action: { label: "Undo", onClick: () => mutate((x) => ({ ...x, widgets: before })) },
    });
  }, [mutate]);

  const chooseLayout = useCallback((id: string) => {
    if (id === activeRef.current) return;
    void (async () => {
      // save what is pending under the old layout, then the list write marks the new one active
      await flush();
      setActiveId(id);
      try {
        const out = await postJSON<DashboardLayouts>("/api/dashboard/layouts", JSON.stringify({
          layouts: (boardsRef.current ?? []).map((x) => ({ id: x.id, ...writeLayout(x) })),
          active: id,
        }));
        out.layouts.forEach((d) => writtenAt.current.set(d.id, d.updated_at));
      } catch (e) {
        showToast({ message: e instanceof Error ? e.message : "The layout was not saved.", tone: "error" });
      }
    })();
  }, [flush]);

  if (!boards) {
    return res.error ? <Problem message={res.error} retry /> : <CardSkeleton height={320} what="Loading the dashboard" />;
  }

  const picker = (
    <Sheet open={pickerOpen} title="Add a widget" onClose={() => setPickerOpen(false)}>
      <ul className="m-0 list-none space-y-2 p-0">
        {WIDGETS.map((def) => (
          <li key={def.type}>
            <button type="button" onClick={() => addWidget(def.type)}
              className="flex w-full flex-col items-start gap-0.5 rounded-lg border border-line px-3 py-2.5 text-left hover:bg-hover">
              <span className="text-[14px] font-medium">{def.title}</span>
              <span className="text-[13px] text-ink-2">{def.blurb}</span>
            </button>
          </li>
        ))}
      </ul>
    </Sheet>
  );

  // "Add a widget" on an empty record makes the Default board first, then opens the picker
  const startAdding = () => {
    if (!board) createBoard("Default", []);
    setPickerOpen(true);
  };

  return (
    <>
      <PageActions>
        {boards.length > 1 && (
          <Select label="Layout" value={board?.id ?? ""} placeholder="Layout" className="w-40"
            options={boards.map((b) => ({ value: b.id, label: b.name }))}
            onChange={chooseLayout} />
        )}
        <Button variant="secondary" onClick={startAdding}>
          <Plus size={15} aria-hidden /> Add widget
        </Button>
        {!!board?.widgets.length && (
          <Button variant="ghost" onClick={reset}><RotateCcw size={15} aria-hidden /> Reset</Button>
        )}
      </PageActions>

      {!board ? (
        <EmptyState title="No dashboard yet"
          action={<Button variant="primary" onClick={startAdding}>
            <Plus size={15} aria-hidden /> Add a widget
          </Button>}>
          Start from one of the boards below, or add a widget of your own.
        </EmptyState>
      ) : board.widgets.length ? (
        <Grid widgets={board.widgets} interactive={wide}
          onArrange={(ws) => mutate((x) => ({ ...x, widgets: ws }))}
          onRemove={removeWidget} />
      ) : (
        <EmptyState title="This board is empty"
          action={<Button variant="primary" onClick={() => setPickerOpen(true)}><Plus size={15} aria-hidden /> Add a widget</Button>}>
          Or start it from one of the boards below.
        </EmptyState>
      )}

      {(!board || !board.widgets.length) && (
        <div className="mt-3 grid gap-3 sm:grid-cols-3">
          {PRESETS.map((p) => (
            <button key={p.id} type="button"
              onClick={() => (board ? mutate((x) => ({ ...x, widgets: p.widgets() })) : createBoard(p.name, p.widgets()))}
              className="flex flex-col items-start gap-1 rounded-xl border border-line bg-panel px-4 py-3 text-left hover:bg-hover">
              <span className="text-[14px] font-semibold">{p.name}</span>
              <span className="text-[13px] text-ink-2">{p.blurb}</span>
            </button>
          ))}
        </div>
      )}
      {picker}
    </>
  );
}
