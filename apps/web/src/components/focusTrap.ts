// The Tab trap a modal keeps (G18's Sheet, and the phone menu's drawer since the audit):
// Tab and Shift-Tab go round the panel's own controls and never out of it.

/** Audit: marks a part of the page that stays live while a Sheet is open (the toasts, whose Undo must stay in reach). */
export const KEEP_LIVE = "data-keep-live";

const FOCUSABLE = 'a[href], button:not(:disabled), input:not(:disabled):not([type="hidden"]), select:not(:disabled), textarea:not(:disabled), summary, [tabindex]:not([tabindex="-1"])';

/** The controls inside `within` that can take the focus, in order. */
export function focusables(within: HTMLElement): HTMLElement[] {
  return [...within.querySelectorAll<HTMLElement>(FOCUSABLE)].filter((el) => !el.closest("[hidden], [inert]"));
}

/**
 * For a Tab keydown: keeps the focus inside `panel`, wrapping at either end. `live` are parts outside
 * it that stay in reach (a toast's Undo over a Sheet, KEEP_LIVE): Tab goes on to them after the
 * panel's last control and comes back to its first, so the keyboard reaches them too.
 */
export function holdTab(e: KeyboardEvent, panel: HTMLElement, live: readonly HTMLElement[] = []) {
  const parts = [panel, ...live];
  const all = parts.flatMap(focusables);
  if (!all.length) return;
  const first = all[0];
  const last = all[all.length - 1];
  const at = document.activeElement;
  const home = (el: Element | null) => (el ? parts.find((p) => p.contains(el)) : undefined);
  // focus that has left for nowhere (the body, a part gone inert) or for outside comes back in
  const lost = !at || at === document.body || (at instanceof HTMLElement && !!at.closest("[inert]")) || !home(at);
  if (lost || (e.shiftKey && at === first)) {
    e.preventDefault();
    (e.shiftKey ? last : first).focus();
    return;
  }
  if (!e.shiftKey && at === last) {
    e.preventDefault();
    first.focus();
    return;
  }
  // across the gap between the panel and a live part, which the page's own order does not bridge
  const next = all[all.indexOf(at as HTMLElement) + (e.shiftKey ? -1 : 1)];
  if (next && home(next) !== home(at)) {
    e.preventDefault();
    next.focus();
  }
}
