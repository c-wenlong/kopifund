import { useEffect, useState, useSyncExternalStore, type MouseEvent } from "react";

// Five pages do not need a router library: the path is the state, and the
// Python server answers every unknown path with index.html.
export function usePath(): string {
  const [path, setPath] = useState(window.location.pathname);
  useEffect(() => {
    const onPop = () => setPath(window.location.pathname);
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);
  return path;
}

export function navigate(path: string) {
  if (path === window.location.pathname + window.location.search) return;
  // G18: with something unsaved on the page, the move waits for Leave
  if (dirtyGuards.size > 0) askToLeave(() => go(path));
  else go(path);
}

export function onLinkClick(path: string) {
  return (e: MouseEvent<HTMLAnchorElement>) => {
    if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    e.preventDefault();
    navigate(path);
  };
}

// ------------------------------------------------------------ G18: the unsaved guard
//
// A page with unsaved edits calls useUnsavedGuard(true). While any guard is
// dirty, navigate() and the browser's Back and Forward are held, and the
// LeaveBar asks "Leave without saving?": Leave carries the move out, Keep
// editing drops it. Closing the browser tab or reloading gets the browser's own
// question through `beforeunload`. A dirty Sheet asks the same before it closes.

const dirtyGuards = new Set<object>();
let pending: { go: () => void } | null = null;
const watchers = new Set<() => void>();
const emit = () => watchers.forEach((w) => w());

function subscribe(onChange: () => void) {
  watchers.add(onChange);
  return () => watchers.delete(onChange);
}

/** Puts the question up, and runs `go` if the answer is Leave. */
export function askToLeave(go: () => void) {
  pending = { go };
  emit();
}

/** Keep editing: the move that was asked about never happens. */
export function keepEditing() {
  if (!pending) return;
  pending = null;
  emit();
}

/** Leave: the unsaved edits are left behind and the held move goes ahead. */
export function discardAndLeave() {
  const held = pending;
  pending = null;
  emit();
  held?.go();
}

export const leaveAsked = () => pending !== null;

/** True while the question is up. */
export function useLeaveAsked(): boolean {
  return useSyncExternalStore(subscribe, leaveAsked);
}

export function useUnsavedGuard(dirty: boolean) {
  useEffect(() => {
    if (!dirty) return;
    const me = {};
    dirtyGuards.add(me);
    const onUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      // older browsers ask only when this is set
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", onUnload);
    return () => {
      dirtyGuards.delete(me);
      window.removeEventListener("beforeunload", onUnload);
      // saved, discarded or gone: a question still up has nothing left to protect
      if (dirtyGuards.size === 0) keepEditing();
    };
  }, [dirty]);
}

// Back and Forward. The browser has already moved by the time `popstate`
// fires, so a held move is undone with history.go. Each entry this app pushes
// carries its place in the history (`kfIndex`); every replaceState in the app
// passes history.state on, so the number survives a filter written to the
// address. Comparing it with the entry on screen gives how far to step back.
const KEY = "kfIndex";
const indexOf = (state: unknown): number | null => {
  const n = state && typeof state === "object" ? (state as Record<string, unknown>)[KEY] : undefined;
  return typeof n === "number" ? n : null;
};
let at = 0;            // the index of the entry on screen
let passing = false;   // navigate's own popstate, already allowed
let restoring = false; // the step that undoes a held Back or Forward
let leaving = false;   // the step Leave sets off

function go(path: string) {
  at += 1;
  window.history.pushState({ [KEY]: at }, "", path);
  passing = true;
  try {
    window.dispatchEvent(new PopStateEvent("popstate"));
  } finally {
    passing = false;
  }
}

function onPop(e: PopStateEvent) {
  if (passing) return;
  const to = indexOf(window.history.state);
  if (restoring) {
    // back where the user was: nothing on the page needs to hear of it
    restoring = false;
    e.stopImmediatePropagation();
    return;
  }
  if (leaving) {
    leaving = false;
  } else if (dirtyGuards.size > 0 && to !== null && to !== at) {
    // an entry with no index (one from before this page loaded) cannot be measured, so it is let through
    e.stopImmediatePropagation();
    const step = to - at;
    restoring = true;
    window.history.go(-step);
    askToLeave(() => {
      leaving = true;
      window.history.go(step);
    });
    return;
  }
  if (to !== null) at = to;
}

function install() {
  // a reload of this module (Vite's hot reload) swaps the listener for its own
  const w = window as unknown as { __kfPop?: (e: PopStateEvent) => void };
  if (w.__kfPop) window.removeEventListener("popstate", w.__kfPop, true);
  w.__kfPop = onPop;
  const state: unknown = window.history.state;
  const now = indexOf(state);
  if (now === null) {
    window.history.replaceState({ ...(state && typeof state === "object" ? state : {}), [KEY]: 0 }, "");
  }
  at = now ?? 0;
  // in the capture phase and before any page's own listener, so a held Back
  // never reaches usePath, the pagers or the address-bar filters
  window.addEventListener("popstate", onPop, true);
}

if (typeof window !== "undefined") install();
