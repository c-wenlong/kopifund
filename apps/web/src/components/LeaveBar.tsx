import { useEffect, useId, useRef, useSyncExternalStore, type KeyboardEvent } from "react";
import { discardAndLeave, keepEditing, useLeaveAsked } from "../router";
import { Button } from "./Button";

// G18: the question the unsaved guard asks, as a bar in the page rather than
// a browser dialog. The page shows it at the top of its content; an open Sheet
// shows it in its own foot instead, since the page behind a Sheet is inert.
// Only one of them draws it: the topmost Sheet, or the page when none is open.

const sheets: object[] = [];
const watchers = new Set<() => void>();
const top = () => sheets[sheets.length - 1] ?? null;

function subscribe(onChange: () => void) {
  watchers.add(onChange);
  return () => watchers.delete(onChange);
}

/** A Sheet says it is open, so the question moves into it; the returned function says it closed. */
export function sheetOpened(sheet: object): () => void {
  sheets.push(sheet);
  watchers.forEach((w) => w());
  return () => {
    const at = sheets.indexOf(sheet);
    if (at >= 0) sheets.splice(at, 1);
    watchers.forEach((w) => w());
  };
}

export const topSheet = top;

export function LeaveBar({ owner = null }: { owner?: object | null }) {
  const asked = useLeaveAsked();
  const shownHere = useSyncExternalStore(subscribe, top) === owner;
  return asked && shownHere ? <Question /> : null;
}

function Question() {
  const keep = useRef<HTMLButtonElement>(null);
  const id = useId();
  useEffect(() => {
    // the answer is one key away; Keep editing gives the focus back to where it was
    const before = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    keep.current?.focus();
    return () => {
      if (before?.isConnected && !before.closest("[inert]") && document.activeElement !== before) before.focus();
    };
  }, []);
  function onKeyDown(e: KeyboardEvent) {
    if (e.key === "Escape") {
      // the bar's own Escape: a Sheet around it does not close
      e.preventDefault();
      keepEditing();
    }
  }
  return (
    <div role="group" aria-labelledby={id} onKeyDown={onKeyDown}
      className="flex flex-wrap items-center justify-end gap-2 rounded-lg border border-line bg-warn-bg px-3 py-2 text-[13px]">
      <p id={id} role="alert" className="m-0 mr-auto basis-full font-medium text-ink sm:basis-auto">Leave without saving?</p>
      <Button ref={keep} variant="primary" onClick={keepEditing}>Keep editing</Button>
      <Button onClick={discardAndLeave}>Leave</Button>
    </div>
  );
}
