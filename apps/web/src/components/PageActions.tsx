import { useLayoutEffect, useState } from "react";
import { createPortal } from "react-dom";

// G13: a page's own buttons and status chips, drawn in the shell's page header
// (the row with the title) instead of inside the page. Where there is no shell,
// as in a test that renders the page alone, they draw in place.
export const PAGE_ACTIONS = "page-actions";

export function PageActions({ children }: { children: React.ReactNode }) {
  const [slot, setSlot] = useState<HTMLElement | null>(null);
  const [looked, setLooked] = useState(false);
  useLayoutEffect(() => {
    setSlot(document.getElementById(PAGE_ACTIONS));
    setLooked(true);
  }, []);
  if (!looked) return null;
  return slot ? createPortal(children, slot) : <div className="flex flex-wrap items-center justify-end gap-2">{children}</div>;
}
