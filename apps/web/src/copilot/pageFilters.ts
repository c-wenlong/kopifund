// G22: what a page shows that its address does not say. Spending keeps its
// range, scope and split in state, Plan its budget month, Net worth its range;
// a question asked beside them means what is on screen, so each page publishes
// its effective selections here, in the names the copilot's tools take
// (from/to as YYYY-MM, scope sg|all, by bucket|category|payee, month), and the
// panel sends them over the address's own filters.
import { useEffect, useSyncExternalStore } from "react";

type Published = { id: number; filters: Record<string, string> };

let current: Published | null = null;
let nextId = 1;
const listeners = new Set<() => void>();
const EMPTY: Record<string, string> = {};

function publish(p: Published | null) {
  current = p;
  listeners.forEach((l) => l());
}

/** For tests: no page has published anything. */
export function resetPublished() {
  publish(null);
}

/** The filters the page on screen published, or none. */
export function usePublishedFilters(): Record<string, string> {
  return useSyncExternalStore(
    (fn) => {
      listeners.add(fn);
      return () => void listeners.delete(fn);
    },
    () => current?.filters ?? EMPTY,
  );
}

/**
 * Publish this page's effective selections to the copilot while it is on
 * screen. A value left undefined or "" is not sent; the selections go when
 * the page does.
 */
export function useCopilotContext(filters: Record<string, string | null | undefined>) {
  const clean = Object.fromEntries(Object.entries(filters).filter((e): e is [string, string] => !!e[1]));
  const key = JSON.stringify(clean);
  useEffect(() => {
    const mine: Published = { id: nextId++, filters: JSON.parse(key) as Record<string, string> };
    publish(mine);
    return () => {
      // a page that took over since keeps its own
      if (current?.id === mine.id) publish(null);
    };
  }, [key]);
}
