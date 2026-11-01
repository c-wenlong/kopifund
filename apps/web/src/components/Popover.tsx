import { useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent, type ReactNode, type RefObject } from "react";
import { createPortal } from "react-dom";

// G13: the floating box under a date or month field. Like Select, it is drawn
// on document.body so a card or a scrolling table around the field cannot clip
// it. It opens under the field (above it when the page has no room below),
// stays inside the screen sideways, follows the field when the page scrolls,
// and closes on Escape, Tab or a press anywhere outside it and the field.
export function Popover({ anchor, label, id, onClose, focusKey = 0, side = "below", links = false, children }: {
  anchor: RefObject<HTMLElement | null>;
  label: string;
  id?: string;
  /** G18: "right" opens it beside the anchor, its top level with the anchor's and kept on the screen (the sidebar's list). */
  side?: "below" | "right";
  /** G18: a box of links (the sidebar's list): it takes the focus as it opens, Tab moves through the links, and the focus leaving it closes it. */
  links?: boolean;
  /** `refocus` is true when the field should take the focus back (Escape, or a choice made). */
  onClose: (refocus: boolean) => void;
  /** Moves the focus onto the calendar's focus target each time it changes to a number above 0 (one per arrow-down on the field). */
  focusKey?: number;
  children: ReactNode;
}) {
  const box = useRef<HTMLDivElement>(null);
  const [at, setAt] = useState<{ left: number; top: number; maxHeight?: number } | null>(null);

  useLayoutEffect(() => {
    const place = () => {
      const a = anchor.current?.getBoundingClientRect();
      const me = box.current;
      if (!a || !me) return;
      const room = window.innerWidth - me.offsetWidth - 8;
      // the box's full height, even while a max-height is holding it shorter (its border is 1px a side)
      const height = Math.max(me.offsetHeight, me.scrollHeight + 2);
      if (side === "right") {
        const tall = window.innerHeight - 16;
        const shown = Math.min(height, tall);
        const next = {
          left: Math.max(8, Math.min(a.right + 6, room)),
          top: Math.max(8, Math.min(a.top, window.innerHeight - 8 - shown)),
          ...(height > tall ? { maxHeight: tall } : {}),
        };
        setAt((was) => (was && JSON.stringify(was) === JSON.stringify(next) ? was : next));
        return;
      }
      const left = Math.max(8, Math.min(a.left, room));
      const below = window.innerHeight - a.bottom - 4 - 8;
      const above = a.top - 4 - 8;
      // under the field if it fits, else over it; with room for neither, the roomier side
      // with the box scrolling inside it, so the calendar never runs off the screen
      const up = height > below && above > below;
      const space = up ? above : below;
      // always placed by `top`: a `bottom` offset is measured from the layout viewport, which is not
      // window.innerHeight on a phone (the address bar, a scrollbar), and left a gap above the field
      const shown = height > space ? Math.max(space, 0) : height;
      const next = {
        left,
        top: up ? Math.max(8, a.top - 4 - shown) : a.bottom + 4,
        ...(height > space ? { maxHeight: Math.max(space, 0) } : {}),
      };
      setAt((was) => (was && JSON.stringify(was) === JSON.stringify(next) ? was : next));
    };
    place();
    window.addEventListener("scroll", place, true);
    window.addEventListener("resize", place);
    // a month with six weeks is taller than one with five
    const watch = new ResizeObserver(place);
    if (box.current) watch.observe(box.current);
    // G18: and an anchor that grows (a sidebar item as the rail widens) moves the box beside it
    if (side === "right" && anchor.current) watch.observe(anchor.current);
    return () => {
      window.removeEventListener("scroll", place, true);
      window.removeEventListener("resize", place);
      watch.disconnect();
    };
  }, [anchor, side]);

  useEffect(() => {
    if (focusKey > 0) box.current?.querySelector<HTMLElement>('button[tabindex="0"]:not(:disabled)')?.focus();
  }, [focusKey]);

  // G18: a box of links takes the focus once it is placed (a hidden box cannot take it), so Escape and Tab work in it
  const placed = at !== null;
  useEffect(() => {
    if (links && placed) box.current?.focus();
  }, [links, placed]);

  // a press anywhere else closes it; the field itself decides what a press on it means
  const onAnchor = useRef(false);
  useEffect(() => {
    const away = (e: MouseEvent) => {
      const t = e.target as Node;
      onAnchor.current = !!anchor.current?.contains(t);
      if (!box.current?.contains(t) && !onAnchor.current) onClose(false);
    };
    document.addEventListener("mousedown", away);
    return () => document.removeEventListener("mousedown", away);
  }, [anchor, onClose]);

  function onKeyDown(e: KeyboardEvent) {
    if (e.key === "Escape") {
      // closes this box only: a drawer or dialog around the field keeps its Escape for itself
      e.preventDefault();
      e.nativeEvent.stopPropagation();
      onClose(true);
    } else if (e.key === "Tab" && !links) {
      onClose(true);
    }
  }

  return createPortal(
    <div ref={box} id={id} role="dialog" aria-label={label} onKeyDown={onKeyDown}
      tabIndex={links ? -1 : undefined}
      onBlur={links ? (e) => {
        const to = e.relatedTarget as Node | null;
        // no target: the focus left the page (the address bar, another window), so the box closes too,
        // unless a press on the field itself took it, which the field's own click decides (Safari focuses no button)
        if (!to ? !onAnchor.current : !box.current?.contains(to) && !anchor.current?.contains(to)) onClose(false);
      } : undefined}
      style={at ?? { visibility: "hidden", left: 0, top: 0 }}
      className="fixed z-50 w-max max-w-[calc(100vw-1rem)] overflow-y-auto rounded-lg border border-line bg-panel p-2 text-[13px] text-ink shadow-lg outline-none">
      {children}
    </div>,
    document.body,
  );
}
