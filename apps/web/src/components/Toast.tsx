import { useEffect, useState, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";
import { KEEP_LIVE } from "./focusTrap";
import { CircleAlert, X } from "lucide-react";

// G13: a short message at the bottom of the screen, where the person who just
// acted is looking, for feedback that used to render at the top of a long page.
// Any code can call `showToast`; `<Toaster />` (mounted once, in App) shows it.
// A toast that carries an `action` (Undo) stays up until it is dismissed, the
// action is pressed, or newer Undos push it off: the Undo is the only way back
// from a trip, rule or category save, so no timer takes it away. Undos stack,
// newest on top, each with its own Undo, so a second save never takes away the
// first one's way back. Plain notices and errors still go by themselves.
// An action that answers `false` did not do its work (the server refused it, as it refuses an
// older Undo while a newer write stands), so its toast stays for another try once the newer one
// is undone; any other answer takes the toast down.

export type ToastAction = { label: string; onClick: () => void | boolean | Promise<void | boolean> };
export type ToastInput = { message: string; tone?: "ok" | "error"; action?: ToastAction; ms?: number };
type Item = ToastInput & { id: number };

const MAX_UNDOS = 3;
const MAX_SHOWN = 4;
let items: Item[] = [];
let next = 1;
const listeners = new Set<() => void>();

function publish(list: Item[]) {
  items = list;
  listeners.forEach((l) => l());
}

// Over the limits, the oldest plain notice goes first: an Undo is the only way back from a
// save, so unrelated notices never push it off the screen. Only a fourth Undo takes the
// oldest Undo's place.
function trim(list: Item[]): Item[] {
  const out = [...list];
  while (out.filter((i) => i.action).length > MAX_UNDOS) out.splice(out.findIndex((i) => i.action), 1);
  while (out.length > MAX_SHOWN) {
    const at = out.findIndex((i) => !i.action);
    out.splice(at === -1 ? 0 : at, 1);
  }
  return out;
}

export function showToast(t: ToastInput): number {
  const id = next++;
  // the same words again replace the plain notice already up, not stack beside it; an Undo is
  // never replaced by its words, because each one undoes its own write
  const kept = t.action ? items : items.filter((i) => i.action || i.message !== t.message);
  publish(trim([...kept, { ...t, id }]));
  return id;
}

export function dismissToast(id: number) {
  publish(items.filter((i) => i.id !== id));
}

/** For tests: take every toast down. */
export function clearToasts() {
  publish([]);
}

function useToasts(): Item[] {
  return useSyncExternalStore(
    (fn) => {
      listeners.add(fn);
      return () => void listeners.delete(fn);
    },
    () => items,
  );
}

/** How long a toast stays up in ms; null means until the person acts. */
export const lifetime = (t: ToastInput): number | null => t.ms ?? (t.action ? null : t.tone === "error" ? 8000 : 5000);

/**
 * Audit: the toasts sit straight under <body>, outside #root, above any Sheet
 * (z-60 over its z-50) and marked to stay live while one is open: a Sheet
 * makes everything else inert, which hid the copilot's Undo under its scrim
 * on a phone, out of reach and unannounced.
 */
export function Toaster() {
  const list = useToasts();
  return createPortal(
    <div aria-live="polite" {...{ [KEEP_LIVE]: "" }} className="pointer-events-none fixed inset-x-0 bottom-0 z-[60] flex flex-col items-center gap-2 px-4 pb-[calc(1rem+env(safe-area-inset-bottom))]">
      {[...list].reverse().map((t) => <ToastCard key={t.id} toast={t} />)}
    </div>,
    document.body,
  );
}

function ToastCard({ toast: t }: { toast: Item }) {
  const [paused, setPaused] = useState(false);
  const [working, setWorking] = useState(false);
  useEffect(() => {
    const ms = lifetime(t);
    if (paused || ms === null) return;
    const timer = window.setTimeout(() => dismissToast(t.id), ms);
    return () => window.clearTimeout(timer);
  }, [paused, t]);
  const error = t.tone === "error";
  return (
    <div
      role={error ? "alert" : "status"}
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
      onFocus={() => setPaused(true)}
      onBlur={() => setPaused(false)}
      className={`pointer-events-auto flex w-full max-w-md items-center gap-3 rounded-lg border px-3.5 py-2.5 text-[13px] shadow-lg ${
        error ? "border-line bg-warn-bg text-warn" : "border-line bg-ink text-page"
      }`}
    >
      {error && <CircleAlert size={16} className="shrink-0" aria-hidden />}
      <span className="min-w-0 flex-1">{t.message}</span>
      {t.action && (
        <button type="button" disabled={working}
          className="shrink-0 rounded px-1.5 py-0.5 font-medium underline underline-offset-2 hover:bg-hover disabled:opacity-40"
          onClick={async () => {
            const done = t.action!.onClick();
            // an action that answers at once takes the toast down at once; one that waits for the server does so when it is done
            if (!(done instanceof Promise)) {
              if (done !== false) dismissToast(t.id);
              return;
            }
            setWorking(true);
            try {
              if ((await done) !== false) dismissToast(t.id);
            } finally {
              setWorking(false);
            }
          }}>
          {t.action.label}
        </button>
      )}
      <button type="button" aria-label="Dismiss" onClick={() => dismissToast(t.id)}
        className="grid size-6 shrink-0 place-items-center rounded opacity-70 hover:bg-hover hover:opacity-100">
        <X size={14} aria-hidden />
      </button>
    </div>
  );
}
