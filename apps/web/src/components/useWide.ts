import { useSyncExternalStore } from "react";

// Tailwind's `md` breakpoint (48rem, 768px). At or above it the app keeps the
// desktop sidebar; below it the sidebar becomes a drawer behind a top bar.
const WIDE = "(min-width: 48rem)";

function subscribe(onChange: () => void) {
  if (typeof window.matchMedia !== "function") return () => {};
  const query = window.matchMedia(WIDE);
  query.addEventListener("change", onChange);
  return () => query.removeEventListener("change", onChange);
}

// Without matchMedia (jsdom, very old browsers) the app takes the desktop layout.
const wide = () => typeof window.matchMedia !== "function" || window.matchMedia(WIDE).matches;

export function useWide(): boolean {
  return useSyncExternalStore(subscribe, wide);
}
