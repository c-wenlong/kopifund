import { useCallback, useEffect, useState } from "react";

// "open" keeps the sidebar expanded and pushes the page over.
// "hover" rests it as an icon rail that expands over the page on hover or focus.
export type SidebarMode = "open" | "hover";

const KEY = "kopifund.sidebar";
const EVENT = "kopifund:sidebar";

function read(): SidebarMode {
  try {
    return window.localStorage.getItem(KEY) === "hover" ? "hover" : "open";
  } catch {
    return "open";
  }
}

export function useSidebarMode(): [SidebarMode, (mode: SidebarMode) => void] {
  const [mode, setModeState] = useState<SidebarMode>(read);

  useEffect(() => {
    const sync = () => setModeState(read());
    window.addEventListener(EVENT, sync);
    return () => window.removeEventListener(EVENT, sync);
  }, []);

  const setMode = useCallback((next: SidebarMode) => {
    try {
      window.localStorage.setItem(KEY, next);
    } catch {
      // Storage can be blocked; the choice still holds for this visit.
    }
    setModeState(next);
    window.dispatchEvent(new Event(EVENT));
  }, []);

  return [mode, setMode];
}
