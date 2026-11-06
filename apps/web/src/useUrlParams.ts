import { useCallback, useEffect, useMemo, useRef, useSyncExternalStore } from "react";

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

/** The page a list's address names under `page`: 1 when it names none, or nonsense. */
export const pageParam = (params: URLSearchParams) => Math.max(1, Math.floor(Number(params.get("page"))) || 1);

/**
 * Audit: a paged list and its address follow each other (Rules, Transactions). A
 * click on a page number writes `page` (page 1 drops it); an address that names
 * another page (Back, a link) moves the list there. With `filters`, an address
 * whose filters change while its page stays (a link to page 3 of another search)
 * moves the list to that page too, where the pager alone would go back to 1. While
 * `ready` is false (the rows have not loaded) the list's page is not written.
 */
export function useUrlPage(
  pager: { page: number; setPage: (page: number) => void }, urlPage: number,
  patch: (patch: Record<string, string | null>) => void,
  { filters = "", ready = true }: { filters?: string; ready?: boolean } = {},
) {
  const seen = useRef({ page: urlPage, filters });
  useEffect(() => {
    if (urlPage !== seen.current.page || filters !== seen.current.filters) {
      seen.current = { page: urlPage, filters };
      if (urlPage !== pager.page) pager.setPage(urlPage);
    } else if (ready && pager.page !== urlPage) {
      seen.current = { page: pager.page, filters };
      patch({ page: pager.page > 1 ? String(pager.page) : null });
    }
  }, [urlPage, filters, pager, patch, ready]);
}
