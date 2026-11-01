import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState, type HTMLAttributes, type KeyboardEvent, type ReactNode } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";

// One row of tabs and the panel of the selected one. Only that panel's
// content is passed in, so switching is a state change and nothing else.
// Arrows, Home and End move between tabs and select as they go; Tab leaves
// the row for the panel. The row scrolls sideways when the tabs do not fit: the
// edge that has more tabs past it fades out and shows a small arrow that scrolls
// that way (the scrollbar stays hidden, so this is the only sign there is more).
export type TabItem = {
  id: string;
  label: string;
  // G18: drawn before the label, such as a bucket's icon in its colour
  icon?: ReactNode;
  // a number shown beside the label, such as how many categories a bucket holds
  count?: number;
  // extra props for the tab button, such as drag handlers
  props?: HTMLAttributes<HTMLButtonElement>;
};

// how far the fade (and so the arrow over it) reaches in from the row's edge; a tab the
// keyboard scrolls to is kept this far clear of it, so it never ends under the arrow
const FADE = 44;

export function Tabs({ label, items, value, onChange, children, panelProps, panelClassName = "" }: {
  label: string;
  items: TabItem[];
  value: string;
  onChange: (id: string) => void;
  children: ReactNode;
  panelProps?: HTMLAttributes<HTMLDivElement>;
  panelClassName?: string;
}) {
  const uid = useId();
  const buttons = useRef<(HTMLButtonElement | null)[]>([]);
  const at = Math.max(0, items.findIndex((t) => t.id === value));
  const row = useRef<HTMLDivElement>(null);
  const [more, setMore] = useState({ left: false, right: false });

  const measure = useCallback(() => {
    const el = row.current;
    if (!el) return;
    const left = el.scrollLeft > 1;
    const right = el.scrollLeft + el.clientWidth < el.scrollWidth - 1;
    setMore((m) => (m.left === left && m.right === right ? m : { left, right }));
  }, []);

  // the row's own size changes with the window, and its content with the tabs
  useLayoutEffect(measure, [measure, items]);
  useEffect(() => {
    const el = row.current;
    if (!el) return;
    const watch = new ResizeObserver(measure);
    watch.observe(el);
    window.addEventListener("resize", measure);
    return () => {
      watch.disconnect();
      window.removeEventListener("resize", measure);
    };
  }, [measure]);

  const scrollBy = (dir: -1 | 1) => {
    const el = row.current;
    el?.scrollBy?.({ left: dir * Math.max(120, el.clientWidth * 0.7), behavior: "smooth" });
  };

  // keep the selected tab in view when the row scrolls (a link opened on the last tab)
  useEffect(() => {
    buttons.current[at]?.scrollIntoView?.({ block: "nearest", inline: "nearest" });
    measure();
  }, [at, measure]);

  // a fade over the edge that has more tabs past it; a mask, so it suits any background
  const fade = `linear-gradient(to right, ${more.left ? `transparent, black ${FADE}px` : "black, black"}, ${more.right ? `black calc(100% - ${FADE}px), transparent` : "black, black"})`;
  // the arrows paint the page colour over the faded end of the row: the one place Tabs sits is on the page
  const arrow = "absolute top-0 z-10 flex h-[calc(100%-1px)] w-7 items-center justify-center bg-page text-ink-2 hover:text-ink";

  function onKeyDown(e: KeyboardEvent) {
    const last = items.length - 1;
    const to = e.key === "ArrowRight" ? (at === last ? 0 : at + 1)
      : e.key === "ArrowLeft" ? (at === 0 ? last : at - 1)
      : e.key === "Home" ? 0
      : e.key === "End" ? last
      : null;
    if (to == null || last < 0) return;
    e.preventDefault();
    onChange(items[to].id);
    buttons.current[to]?.focus();
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="relative">
      <div ref={row} role="tablist" aria-label={label} onKeyDown={onKeyDown} onScroll={measure}
        style={more.left || more.right ? { maskImage: fade, WebkitMaskImage: fade, scrollPaddingInline: `${FADE}px` } : undefined}
        className="flex gap-1 overflow-x-auto border-b border-line [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
        {items.map((t, i) => {
          const on = i === at;
          return (
            <button key={t.id} type="button" role="tab" id={`${uid}-tab-${i}`} aria-selected={on} aria-controls={`${uid}-panel`}
              tabIndex={on ? 0 : -1} ref={(el) => { buttons.current[i] = el; }}
              onClick={() => onChange(t.id)} {...t.props}
              className={`-mb-px flex shrink-0 items-center gap-1.5 whitespace-nowrap border-b-2 px-3 py-2 text-[13px] ${on
                ? "border-ink font-medium text-ink" : "border-transparent text-ink-2 hover:bg-hover hover:text-ink"}`}>
              {t.icon}
              {t.label}
              {t.count != null && <span className="num text-[12px] font-normal text-ink-3">{t.count}</span>}
            </button>
          );
        })}
      </div>
      {more.left && (
        <button type="button" tabIndex={-1} aria-label="Scroll tabs left" onClick={() => scrollBy(-1)} className={`${arrow} left-0`}>
          <ChevronLeft size={16} aria-hidden />
        </button>
      )}
      {more.right && (
        <button type="button" tabIndex={-1} aria-label="Scroll tabs right" onClick={() => scrollBy(1)} className={`${arrow} right-0`}>
          <ChevronRight size={16} aria-hidden />
        </button>
      )}
      </div>
      <div role="tabpanel" id={`${uid}-panel`} aria-labelledby={`${uid}-tab-${at}`} {...panelProps} className={panelClassName}>
        {children}
      </div>
    </div>
  );
}
