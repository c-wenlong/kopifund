import { useCallback, useMemo, useSyncExternalStore } from "react";

// G13: a page's filters kept in its address. The address is the state: reading
// it gives the filters, writing it replaces the current history entry (typing
// in a search box must not stack up a hundred Back steps), so Back from
// another page lands on the page as it was left and a copied link opens the
// same view. A link followed to the same page (the sidebar's, a chart bar's)
// arrives as a popstate and is read the same way.

const CHANGED = "kopifund:urlchange";

function subscribe(onChange: () => void) {
  window.addEventListener("popstate", onChange);
  window.addEventListener(CHANGED, onChange);
  return () => {
    window.removeEventListener("popstate", onChange);
    window.removeEventListener(CHANGED, onChange);
  };
}

const snapshot = () => window.location.search;

/** The address's query as parameters, and a writer that sets (a string) or drops (null or "") some of them. */
export function useUrlParams(): [URLSearchParams, (patch: Record<string, string | null>) => void] {
  const search = useSyncExternalStore(subscribe, snapshot);
  const write = useCallback((patch: Record<string, string | null>) => {
    // read the address afresh: two writes in one tick must both land
    const next = new URLSearchParams(window.location.search);
    for (const [key, value] of Object.entries(patch)) {
      if (value === null || value === "") next.delete(key);
      else next.set(key, value);
    }
    const query = next.toString();
    if (`?${query}` === (window.location.search || "?")) return;
    window.history.replaceState(window.history.state, "", `${window.location.pathname}${query ? `?${query}` : ""}${window.location.hash}`);
    window.dispatchEvent(new Event(CHANGED));
  }, []);
  const params = useMemo(() => new URLSearchParams(search), [search]);
  return [params, write];
}
