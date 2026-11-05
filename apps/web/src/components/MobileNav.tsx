import { useEffect, useId, useRef, useState, type ReactNode, type RefObject } from "react";
import { Menu, X } from "lucide-react";
import type { EyeLine } from "../api";
import { SidebarBody, type Holdings } from "./Sidebar";
import { eyeTone } from "./NeedsYourEye";
import { useAppIconLogo } from "./appIcon";
import { holdTab, KEEP_LIVE } from "./focusTrap";

// G13: under 768px the sidebar would squeeze every page, so a slim bar takes
// its place and the sidebar opens over the page as a drawer.
export const DRAWER_ID = "app-drawer";

export function TopBar({ title, open, onOpen, buttonRef, eye = [], ask }: {
  title: string; open: boolean; onOpen: () => void; buttonRef: RefObject<HTMLButtonElement | null>; eye?: EyeLine[];
  /** G22: the Ask Kopifund button, at the bar's right end */
  ask?: ReactNode;
}) {
  const said = useId();
  const logo = useAppIconLogo();
  return (
    <header inert={open} className="flex h-12 shrink-0 items-center gap-2 border-b border-line bg-page px-2">
      <button
        ref={buttonRef}
        type="button"
        onClick={onOpen}
        aria-label="Open menu"
        aria-expanded={open}
        aria-controls={open ? DRAWER_ID : undefined}
        aria-describedby={eye.length ? said : undefined}
        className="grid size-9 shrink-0 place-items-center rounded-md text-ink-2 hover:bg-hover hover:text-ink"
      >
        <span className="relative">
          <Menu size={18} aria-hidden />
          {/* G18: something needs your eye; the menu holds the list */}
          {eye.length > 0 && (
            <span data-testid="menu-dot" aria-hidden
              className={`absolute -top-0.5 -right-1 size-2 rounded-full ring-2 ring-page ${eyeTone(eye) === "danger" ? "bg-danger" : "bg-warn"}`} />
          )}
        </span>
      </button>
      {eye.length > 0 && <span id={said} hidden>{eye.length === 1 ? "1 thing needs your eye" : `${eye.length} things need your eye`}</span>}
      <img src={logo} alt="" className="size-6 shrink-0" />
      <span className="min-w-0 truncate text-[15px] font-semibold tracking-tight">{title}</span>
      {ask}
    </header>
  );
}

export function Drawer({ path, holdings, asOf, onClose, returnFocus, eye = [], eyeFirst = false }: {
  path: string; holdings: Holdings; asOf?: string | null; onClose: () => void; returnFocus: RefObject<HTMLElement | null>;
  /** G18: Needs your eye's lines, and whether the menu opens with them showing (Home's "All N") */
  eye?: EyeLine[]; eyeFirst?: boolean;
}) {
  const closeButton = useRef<HTMLButtonElement>(null);
  const logo = useAppIconLogo();
  const panel = useRef<HTMLDivElement>(null);
  const [eyeOpen, setEyeOpen] = useState(eyeFirst);

  useEffect(() => {
    // focus moves into the drawer, and back to the menu button on close. The
    // button comes by ref: the top bar turns inert as the drawer opens, which
    // can take focus off it before this runs.
    const opener = returnFocus.current;
    // G18: without scrolling: opened on Needs your eye (Home's "All N"), the list has
    // already scrolled itself into view, and the button sits at the top
    closeButton.current?.focus({ preventScroll: true });
    const onKey = (e: KeyboardEvent) => {
      // Audit: a list open inside the drawer keeps its own Escape, and Tab stays in the drawer, which says it is modal
      if (e.key === "Escape" && !e.defaultPrevented) onClose();
      else if (e.key === "Tab" && panel.current) holdTab(e, panel.current, [...document.querySelectorAll<HTMLElement>(`[${KEEP_LIVE}]`)]);
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      if (opener?.isConnected) opener.focus();
    };
  }, [onClose, returnFocus]);

  return (
    <div className="fixed inset-0 z-40">
      <div data-testid="drawer-scrim" aria-hidden className="absolute inset-0 bg-scrim" onClick={onClose} />
      <div
        ref={panel}
        id={DRAWER_ID}
        role="dialog"
        aria-modal="true"
        aria-label="Menu"
        // a tap on any link closes the drawer, the current page's link included
        onClick={(e) => {
          if ((e.target as Element).closest("a")) onClose();
        }}
        className="absolute inset-y-0 left-0 flex w-72 max-w-[85vw] flex-col overflow-y-auto border-r border-line bg-page pr-2 pt-[calc(0.75rem+env(safe-area-inset-top))] pb-[calc(0.75rem+env(safe-area-inset-bottom))] pl-[calc(0.5rem+env(safe-area-inset-left))] shadow-xl"
      >
        <div className="mb-3 flex h-8 items-center justify-between pl-2">
          <span className="flex items-center gap-2 text-[15px] font-semibold tracking-tight">
            <img src={logo} alt="" className="size-6" />
            Kopifund
          </span>
          <button
            ref={closeButton}
            type="button"
            onClick={onClose}
            aria-label="Close menu"
            className="grid size-8 shrink-0 place-items-center rounded-md text-ink-3 hover:bg-hover hover:text-ink"
          >
            <X size={16} aria-hidden />
          </button>
        </div>
        <SidebarBody path={path} holdings={holdings} asOf={asOf} expanded eye={eye} eyeOpen={eyeOpen} onEyeOpen={setEyeOpen} inline />
      </div>
    </div>
  );
}
