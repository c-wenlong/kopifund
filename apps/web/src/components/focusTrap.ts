// The Tab trap a modal keeps (G18's Sheet, and the phone menu's drawer since the audit):
// Tab and Shift-Tab go round the panel's own controls and never out of it.

/** Audit: marks a part of the page that stays live while a Sheet is open (the toasts, whose Undo must stay in reach). */
export const KEEP_LIVE = "data-keep-live";

const FOCUSABLE = 'a[href], button:not(:disabled), input:not(:disabled):not([type="hidden"]), select:not(:disabled), textarea:not(:disabled), summary, [tabindex]:not([tabindex="-1"])';

/** The controls inside `within` that can take the focus, in order. */
export function focusables(within: HTMLElement): HTMLElement[] {
  return [...within.querySelectorAll<HTMLElement>(FOCUSABLE)].filter((el) => !el.closest("[hidden], [inert]"));
}

/** For a Tab keydown: keeps the focus inside `panel`, wrapping at either end. */
export function holdTab(e: KeyboardEvent, panel: HTMLElement) {
  const all = focusables(panel);
  if (!all.length) return;
  const first = all[0];
  const last = all[all.length - 1];
  const at = document.activeElement;
  const inside = at instanceof Node && panel.contains(at);
  // focus that has left for nowhere (the body, a part gone inert) or for outside comes back in
  const lost = !at || at === document.body || (at instanceof HTMLElement && !!at.closest("[inert]")) || !inside;
  if (lost || (e.shiftKey && at === first)) {
    e.preventDefault();
    (e.shiftKey ? last : first).focus();
  } else if (!e.shiftKey && at === last) {
    e.preventDefault();
    first.focus();
  }
}
