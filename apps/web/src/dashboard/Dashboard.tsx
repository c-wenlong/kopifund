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

// how many saves of each board this app has sent: a retry of a save made on
// leaving stands only while no later save of that board has gone out
const sent = new Map<string, number>();
function sending(id: string): number {
  const n = (sent.get(id) ?? 0) + 1;
  sent.set(id, n);
  return n;
}

/** Save a board after its page has gone; a failure says so in a toast that can try again. */
function saveOnLeaving(id: string, body: string) {
  const mine = sending(id);
  postJSON(`/api/dashboard/layouts/${id}`, body).catch((e: unknown) => {
    showToast({
      message: e instanceof Error ? `The dashboard was not saved: ${e.message}` : "The dashboard was not saved.",
      tone: "error",
      action: {
        label: "Try again",
        // a newer save of the board (the page opened again) holds the newer
        // edit, so this older body would only overwrite it
        onClick: () => {
          if (sent.get(id) === mine) saveOnLeaving(id, body);
          else showToast({ message: "A newer change to that board was saved since, so it was kept." });
        },
      },
    });
  });
}

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
  // a save the server refused or never answered: the one edit leaving could lose
  const [failed, setFailed] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(false);

  // the latest state for the save timer and the toast callbacks, which run after this render
  const boardsRef = useRef(boards);
  boardsRef.current = boards;
  const activeRef = useRef(activeId);
  activeRef.current = activeId;
  // the boards a save still has to write, keyed by id: a newer edit replaces
  // the board waiting, and a write's reply is applied only while nothing newer
  // waits for the same layout
  const pending = useRef(new Map<string, Board>());
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // the write on the wire; saves go one at a time, so a slow reply can never
  // land after a newer edit and overwrite it
  const saving = useRef<Promise<void> | null>(null);
  // the page has been left: its save loop sends nothing more
  const gone = useRef(false);
  // every document a write of ours put on the server, and the newest
  // updated_at one carried. A reload's answer is a stale echo only when it is
  // one of those documents; another tab writing in the same whole second (the
  // server's stamp has no smaller unit) is newer, echoed or not
  const written = useRef(new Map<string, { at: string; docs: Set<string> }>());

  const remember = (d: DashboardLayout) => {
    const rec = written.current.get(d.id) ?? { at: "", docs: new Set<string>() };
    if (d.updated_at > rec.at) rec.at = d.updated_at;
    rec.docs.add(JSON.stringify(d));
    written.current.set(d.id, rec);
  };

  // adopt the server's document while nothing is held unsaved; an edit of our
  // own would otherwise be clobbered by the reload our own save caused
  useEffect(() => {
    if (!res.data || dirty) return;
    setBoards(res.data.layouts.map((d) => {
      const kept = boardsRef.current?.find((x) => x.id === d.id);
      const rec = written.current.get(d.id);
      const stale = rec && (d.updated_at < rec.at ||
        (d.updated_at === rec.at && rec.docs.has(JSON.stringify(d))));
      return kept && stale ? kept : readLayout(d);
    }));
    setActiveId((id) => res.data!.layouts.some((d) => d.id === id) ? id : res.data!.active ?? res.data!.layouts[0]?.id ?? null);
  }, [res.data, dirty]);

  const flush = useCallback((): Promise<void> => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
    // the page is gone: what waited went out on leaving, and a later save would be older than it
    if (gone.current) return Promise.resolve();
    if (saving.current) return saving.current;
    const run = (async () => {
      try {
        for (;;) {
          const b = pending.current.values().next().value as Board | undefined;
          if (!b) break;
          pending.current.delete(b.id);
          try {
            // only the board on screen is the active one: an Undo on a board left
            // behind saves it without making it the board the next visit opens
            sending(b.id);
            const written = await postJSON<DashboardLayout>(`/api/dashboard/layouts/${b.id}`,
              JSON.stringify({ ...writeLayout(b), active: b.id === activeRef.current }));
            remember(written);
            // the write's own echo is the board's truth now, ahead of any read
            // still on its way -- unless a newer edit is already waiting
            if (!pending.current.has(b.id)) {
              setBoards((bs) => (bs ?? []).map((x) => (x.id === b.id ? readLayout(written) : x)));
            }
          } catch (e) {
            // retry the failed board only when nothing newer replaced it
            if (gone.current) break;
            if (!pending.current.has(b.id)) pending.current.set(b.id, b);
            setFailed(true);
            showToast({
              message: e instanceof Error ? e.message : "The layout was not saved.", tone: "error",
              action: { label: "Try again", onClick: () => void flush() },
            });
            break;
          }
        }
        setDirty(pending.current.size > 0);
        if (!pending.current.size) setFailed(false);
      } finally {
        saving.current = null;
      }
    })();
    saving.current = run;
    return run;
  }, []);

  const saveBoard = useCallback((b: Board) => {
    pending.current.set(b.id, b);
    setDirty(true);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => void flush(), SAVE_AFTER);
  }, [flush]);

  // a pending save still lands when the page is left inside the wait: the app's
  // toast outlives the page, so a save that fails then says so and offers to
  // try again, the edit kept in its body
  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
    // handed off once: the page's own save loop, if a slow reply still holds
    // it, finds nothing left to send after the page is gone
    gone.current = true;
    const left = [...pending.current.values()];
    pending.current.clear();
    for (const b of left) {
      saveOnLeaving(b.id, JSON.stringify({ ...writeLayout(b), active: b.id === activeRef.current }));
    }
  }, []);

  // leaving the page in the app saves what waits (the unmount above), so only a
  // save that failed asks first; closing the tab inside the short wait before
  // a save gets the browser's own question, as it cannot wait for the write
  useUnsavedGuard(failed);
  useEffect(() => {
    if (!dirty || failed) return;
    const ask = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", ask);
    return () => window.removeEventListener("beforeunload", ask);
  }, [dirty, failed]);

  const board = boards?.find((b) => b.id === activeId) ?? boards?.[0] ?? null;

  /** Change one board, the one `id` names, and save it. */
  const mutateBoard = useCallback((id: string, fn: (b: Board) => Board) => {
    const b = boardsRef.current?.find((x) => x.id === id);
    if (!b) return;
    const next = fn(b);
    setBoards((bs) => (bs ?? []).map((x) => (x.id === next.id ? next : x)));
    saveBoard(next);
  }, [saveBoard]);

  /** Change the active board and save it. */
  const mutate = useCallback((fn: (b: Board) => Board) => {
    const b = boardsRef.current?.find((x) => x.id === activeRef.current) ?? boardsRef.current?.[0];
    if (b) mutateBoard(b.id, fn);
  }, [mutateBoard]);

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
    const widget = (on: DashboardWidget[]): DashboardWidget => {
      const made = def.make?.() ?? {};
      const w: DashboardWidget = { id: newWidgetId(), type, title: def.title, layout: firstFit(on, def.size) };
      if (made.query) w.query = made.query;
      if (made.options) w.options = made.options;
      return w;
    };
    // with no board yet, the first widget picked makes the Default board, so
    // a picker closed with nothing picked leaves no empty board behind
    if (!boardsRef.current?.length) {
      createBoard("Default", [widget([])]);
      return;
    }
    mutate((b) => ({ ...b, widgets: pack([...b.widgets, widget(b.widgets)]) }));
  }, [mutate, createBoard]);

  const removeWidget = useCallback((id: string) => {
    const b = boardsRef.current?.find((x) => x.id === activeRef.current);
    const w = b?.widgets.find((x) => x.id === id);
    if (!b || !w) return;
    // the undo names the board it came from: a layout switch while the toast
    // waits must not put the widget back on whichever board is then active
    const boardId = b.id;
    mutateBoard(boardId, (x) => ({ ...x, widgets: x.widgets.filter((v) => v.id !== id) }));
    const name = w.title ?? widgetDef(w.type)?.title ?? "The widget";
    showToast({
      message: `${name} removed.`,
      action: { label: "Undo", onClick: () => mutateBoard(boardId, (x) => ({ ...x, widgets: pack([...x.widgets, w]) })) },
    });
  }, [mutateBoard]);

  const reset = useCallback(() => {
    const b = boardsRef.current?.find((x) => x.id === activeRef.current);
    if (!b) return;
    const boardId = b.id;
    const before = b.widgets;
    mutateBoard(boardId, (x) => ({ ...x, widgets: DEFAULT_PRESET.widgets() }));
    showToast({
      message: `${b.name} reset to ${DEFAULT_PRESET.name}.`,
      action: { label: "Undo", onClick: () => mutateBoard(boardId, (x) => ({ ...x, widgets: before })) },
    });
  }, [mutateBoard]);

  const chooseLayout = useCallback((id: string) => {
    if (id === activeRef.current) return;
    void (async () => {
      // save what is pending under the old layout, then mark the new one
      // active by writing it alone: a write of this tab's whole list would put
      // its older copies of the other boards over another tab's edits
      await flush();
      setActiveId(id);
      const b = boardsRef.current?.find((x) => x.id === id);
      if (!b) return;
      try {
        sending(id);
        const written = await postJSON<DashboardLayout>(`/api/dashboard/layouts/${id}`,
          JSON.stringify({ ...writeLayout(b), active: true }));
        remember(written);
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

  // "Add a widget" on an empty record opens the picker; the widget picked makes the Default board
  const startAdding = () => setPickerOpen(true);

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
