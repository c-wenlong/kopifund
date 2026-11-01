import { useEffect, useId, useRef, useState, type ReactNode, type RefObject } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";
import { askToLeave, keepEditing, leaveAsked, useUnsavedGuard } from "../router";
import { LeaveBar, sheetOpened, topSheet } from "./LeaveBar";

// G18: the app's one modal for a form or a long read. At 768px and up it is a
// dialog in the middle of the screen; under 768px it rises from the foot as a
// sheet that scrolls inside itself, with its `footer` (a SaveBar) pinned at
// the bottom above the iPhone's home bar. It is built from the phone menu's
// parts: a dimmed backdrop, Escape, focus moved in and held there, focus given
// back to the opener on close, and the page behind it inert.
//
// `onClose` means the sheet is closing for good, so the caller drops any draft.
// A press on the backdrop, the close button or Escape first asks
// `onRequestClose`, which can keep the sheet open by answering false; then,
// when `dirty`, the sheet asks "Leave without saving?" in its foot, and only
// Leave goes on to close it.
//
// `wide` makes the dialog up to 1024px wide instead of 768px, for a form whose
// table needs the room (Net worth's projection inputs); a phone sees no change.
export type SheetProps = {
  open: boolean;
  title: string;
  onClose: () => void;
  onRequestClose?: () => boolean | void;
  dirty?: boolean;
  wide?: boolean;
  footer?: ReactNode;
  /** What takes the focus on open; the close button when not given. */
  initialFocus?: RefObject<HTMLElement | null>;
  children: ReactNode;
};

export function Sheet(props: SheetProps) {
  return props.open ? <SheetFrame {...props} /> : null;
}

const FOCUSABLE = 'a[href], button:not(:disabled), input:not(:disabled):not([type="hidden"]), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])';

function focusables(within: HTMLElement): HTMLElement[] {
  return [...within.querySelectorAll<HTMLElement>(FOCUSABLE)].filter((el) => !el.closest("[hidden], [inert]"));
}

function SheetFrame({ title, onClose, onRequestClose, dirty = false, wide = false, footer, initialFocus, children }: SheetProps) {
  const [me] = useState(() => ({}));
  const titleId = useId();
  const root = useRef<HTMLDivElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);
  // browser Back and a reload are held too while the sheet has unsaved edits
  useUnsavedGuard(dirty);

  // the newest props, for the listeners set up once on open
  const latest = useRef({ onClose, onRequestClose, dirty });
  latest.current = { onClose, onRequestClose, dirty };
  const requestClose = () => {
    const { onClose: close, onRequestClose: ask, dirty: unsaved } = latest.current;
    if (ask?.() === false) return;
    if (unsaved) askToLeave(close);
    else close();
  };
  const request = useRef(requestClose);
  request.current = requestClose;

  useEffect(() => {
    const closed = sheetOpened(me);
    // everything else on the page goes inert: no pointer, no focus, hidden from a screen reader
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const others = [...document.body.children].filter(
      (el): el is HTMLElement => el instanceof HTMLElement && el !== root.current && !el.hasAttribute("inert"),
    );
    others.forEach((el) => el.setAttribute("inert", ""));
    (initialFocus?.current ?? closeButton.current)?.focus();

    const onKey = (e: KeyboardEvent) => {
      if (topSheet() !== me) return;
      if (e.key === "Escape") {
        // a calendar or a list open inside the sheet keeps its Escape for itself
        if (e.defaultPrevented) return;
        e.preventDefault();
        if (leaveAsked()) keepEditing();
        else request.current();
      } else if (e.key === "Tab" && panel.current) {
        const all = focusables(panel.current);
        if (!all.length) return;
        const first = all[0];
        const last = all[all.length - 1];
        const at = document.activeElement;
        const inside = at instanceof Node && panel.current.contains(at);
        // focus that has left for nowhere (the body, a part gone inert) comes back in
        const lost = !at || at === document.body || (at instanceof HTMLElement && !!at.closest("[inert]"));
        if (lost || (inside && e.shiftKey && at === first)) {
          e.preventDefault();
          (e.shiftKey ? last : first).focus();
        } else if (inside && !e.shiftKey && at === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      closed();
      others.forEach((el) => el.removeAttribute("inert"));
      if (opener?.isConnected) opener.focus();
    };
  }, [me, initialFocus]);

  return createPortal(
    <div ref={root} className="fixed inset-0 z-50 flex items-end justify-center md:items-center md:p-6">
      <div data-testid="sheet-scrim" aria-hidden className="kf-scrim absolute inset-0 bg-scrim" onClick={() => request.current()} />
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className={`kf-sheet relative flex max-h-[90dvh] w-full flex-col rounded-t-xl border border-edge bg-panel text-ink shadow-xl md:rounded-xl ${wide ? "md:max-w-5xl" : "md:max-w-3xl"}`}
      >
        <div className="flex shrink-0 items-center justify-between gap-3 border-b border-line py-2 pr-2 pl-4">
          <h2 id={titleId} className="m-0 min-w-0 truncate text-[15px] font-semibold tracking-tight">{title}</h2>
          <button
            ref={closeButton}
            type="button"
            onClick={() => request.current()}
            aria-label="Close"
            className="grid size-8 shrink-0 place-items-center rounded-md text-ink-3 hover:bg-hover hover:text-ink"
          >
            <X size={16} aria-hidden />
          </button>
        </div>
        <div className={`min-h-0 flex-1 overflow-y-auto overscroll-contain px-4 pt-3 ${
          footer ? "pb-3" : "pb-[calc(0.75rem+env(safe-area-inset-bottom))]"}`}>
          {children}
        </div>
        {/* the question about unsaved edits sits in the foot, beside the SaveBar it is about */}
        <SheetFoot owner={me} footer={footer} />
      </div>
    </div>,
    document.body,
  );
}

function SheetFoot({ owner, footer }: { owner: object; footer?: ReactNode }) {
  const bar = <LeaveBar owner={owner} />;
  if (!footer) {
    return <div className="shrink-0 px-4 empty:hidden [&:not(:empty)]:pb-[calc(0.75rem+env(safe-area-inset-bottom))]">{bar}</div>;
  }
  return (
    <div className="flex shrink-0 flex-col gap-2 border-t border-line px-4 pt-3 pb-[calc(0.75rem+env(safe-area-inset-bottom))]">
      {bar}
      {footer}
    </div>
  );
}
