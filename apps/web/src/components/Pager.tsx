import { useRef, useState, useSyncExternalStore, type FormEvent } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { buttonLook } from "./Button";
import { Select } from "./Select";

// G13: one pager for every long list. "1 to 25 of 312", previous and next,
// page numbers with ellipses for a long run, and a rows-per-page choice that
// is remembered per list in this browser. Under 640px the numbers shrink to the
// first, current and last page, and a "Go to page" box jumps anywhere.

export const PAGE_SIZES = [10, 25, 50, 100];
export const DEFAULT_SIZE = 25;
const KEY = "kopifund.pagesize.";

function readSize(list: string) {
  try {
    const n = Number(window.localStorage.getItem(KEY + list));
    return PAGE_SIZES.includes(n) ? n : DEFAULT_SIZE;
  } catch {
    return DEFAULT_SIZE;
  }
}

function saveSize(list: string, size: number) {
  try {
    window.localStorage.setItem(KEY + list, String(size));
  } catch {
    // storage can be blocked; the choice still holds for this visit
  }
}

export const pageCount = (total: number, size: number) => Math.max(1, Math.ceil(total / size));
export const clampPage = (page: number, total: number, size: number) =>
  Math.min(Math.max(1, page), pageCount(total, size));

/** The page buttons to draw: at most seven entries, 1 ... 4 5 6 ... 13 in the middle of a long run. */
export type PageItem = number | "gap-before" | "gap-after";
export function pageItems(page: number, pages: number): PageItem[] {
  if (pages <= 7) return Array.from({ length: pages }, (_, i) => i + 1);
  if (page <= 4) return [1, 2, 3, 4, 5, "gap-after", pages];
  if (page >= pages - 3) return [1, "gap-before", pages - 4, pages - 3, pages - 2, pages - 1, pages];
  return [1, "gap-before", page - 1, page, page + 1, "gap-after", pages];
}

/** The page buttons on a phone: first, current and last, with an ellipsis over any run left out. */
export function compactItems(page: number, pages: number): PageItem[] {
  // a gap of one page is shown as that page: an ellipsis never hides a single number
  const between = (a: number, b: number, gap: PageItem): PageItem[] => (b - a > 2 ? [gap] : b - a === 2 ? [a + 1] : []);
  const out: PageItem[] = [1];
  if (page > 1) out.push(...between(1, page, "gap-before"), page);
  if (page < pages) out.push(...between(page, pages, "gap-after"), pages);
  return out;
}

// below Tailwind's `sm` (640px); without matchMedia (jsdom) the wide layout
const NARROW = "(max-width: 39.999rem)";
function subscribeNarrow(onChange: () => void) {
  if (typeof window.matchMedia !== "function") return () => {};
  const query = window.matchMedia(NARROW);
  query.addEventListener("change", onChange);
  return () => query.removeEventListener("change", onChange);
}
const narrow = () => typeof window.matchMedia === "function" && window.matchMedia(NARROW).matches;
const useNarrow = () => useSyncExternalStore(subscribeNarrow, narrow);

export type PagerState = {
  list: string;
  total: number;
  page: number;      // 1-based, never past the last page
  size: number;
  offset: number;
  setPage: (page: number) => void;
  setSize: (size: number) => void;
  slice: <T>(items: T[]) => T[];
};

/**
 * The page and rows-per-page of one list. `total` is the number of rows after
 * filtering (undefined while it is unknown); the page is clamped when it
 * shrinks. `resetOn` is a string that changes with the filters: when it does,
 * the list goes back to page 1. `list` names the remembered page size.
 * `initialPage` is the page a link opens on (a list that keeps its page in the
 * address: useUrlPage in useUrlParams.ts keeps the two in step). Pass
 * `undefined` for `total` while the rows load, so a page read from the address
 * is not clamped to 1 before they arrive.
 */
export function usePager(
  list: string, total: number | undefined, resetOn = "", initialPage = 1,
): PagerState {
  const [page, setPageState] = useState(() => Math.max(1, Math.floor(initialPage) || 1));
  const [size, setSizeState] = useState(() => readSize(list));
  const [seen, setSeen] = useState(resetOn);
  // set during render, so the list never draws (or fetches) the old page for a new filter
  let current = page;
  if (seen !== resetOn) {
    setSeen(resetOn);
    setPageState(1);
    current = 1;
  }
  const known = total ?? 0;
  const shown = total === undefined ? Math.max(1, current) : clampPage(current, known, size);
  // keep the clamped page, so a total that grows again does not bring back a page the list left
  if (shown !== page) setPageState(shown);
  return {
    list, total: known, page: shown, size, offset: (shown - 1) * size,
    setPage: (p) => setPageState(total === undefined ? Math.max(1, p) : clampPage(p, known, size)),
    setSize: (s) => { saveSize(list, s); setSizeState(s); setPageState(1); },
    slice: (items) => items.slice((shown - 1) * size, shown * size),
  };
}

const LOCKED = "Save or cancel your edit first";
// G18: the app's button look, sized for a page number (as wide as the number, at least a square)
const SIZE = "h-8 min-w-8 pointer-coarse:min-w-10 px-2 text-[13px]";
const button = `${buttonLook("secondary")} ${SIZE}`;
const current = `${buttonLook("primary")} ${SIZE}`;

// `locked` freezes the page and the size, for a list with an unsaved edit on this page
export function Pager({ pager, label = "Pages", locked = false }: { pager: PagerState; label?: string; locked?: boolean }) {
  const { total, page, size, setSize } = pager;
  const phone = useNarrow();
  const [jump, setJump] = useState("");
  const root = useRef<HTMLElement>(null);
  // the list sits just above this bar: after a page change from down here, bring its top back into view
  const toList = () => {
    const list = root.current?.previousElementSibling;
    if (list && list.getBoundingClientRect().top < 0) list.scrollIntoView?.({ block: "start" });
  };
  const setPage = (p: number) => {
    pager.setPage(p);
    toList();
  };
  // nothing to page through, and no smaller page size to choose
  if (total <= PAGE_SIZES[0]) return null;
  const pages = pageCount(total, size);
  const from = (page - 1) * size + 1;
  const to = Math.min(page * size, total);
  const n = (v: number) => v.toLocaleString("en-SG");
  const go = (e: FormEvent) => {
    e.preventDefault();
    const target = Number(jump);
    if (jump.trim() !== "" && Number.isInteger(target)) setPage(Math.min(Math.max(1, target), pages));
    setJump("");
  };
  return (
    <nav ref={root} aria-label={label} className="mt-3 flex flex-wrap items-center justify-between gap-x-4 gap-y-2 text-[13px]">
      <span className="num text-ink-2" role="status">{n(from)} to {n(to)} of {n(total)}</span>
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" className={button} title={locked ? LOCKED : undefined} disabled={locked || page <= 1} onClick={() => setPage(page - 1)} aria-label="Previous page">
          <ChevronLeft size={14} aria-hidden />
        </button>
        {/* a phone has room for first, current and last; the box beside the size jumps anywhere */}
        <ul className="m-0 flex list-none items-center gap-1 p-0">
          {(phone ? compactItems(page, pages) : pageItems(page, pages)).map((item) => (
            <li key={item}>
              {typeof item === "string" ? (
                <span className="inline-flex h-8 min-w-6 items-center justify-center text-ink-3" aria-hidden>…</span>
              ) : (
                <button type="button" onClick={() => setPage(item)} aria-label={`Page ${item}`} disabled={locked} title={locked ? LOCKED : undefined}
                  aria-current={item === page ? "page" : undefined}
                  className={`num ${item === page ? current : button}`}>
                  {item}
                </button>
              )}
            </li>
          ))}
        </ul>
        <button type="button" className={button} title={locked ? LOCKED : undefined} disabled={locked || page >= pages} onClick={() => setPage(page + 1)} aria-label="Next page">
          <ChevronRight size={14} aria-hidden />
        </button>
        {phone && (
          <form onSubmit={go} className="flex items-center gap-1">
            <input value={jump} onChange={(e) => setJump(e.target.value.replace(/\D/g, ""))} inputMode="numeric" enterKeyHint="go"
              aria-label="Go to page" placeholder="Go to page" disabled={locked} title={locked ? LOCKED : undefined}
              className="num h-8 w-24 rounded-md border border-line bg-panel px-2 text-[13px] text-ink placeholder:text-ink-3" />
            <button type="submit" className={button} disabled={locked || jump === ""}>Go</button>
          </form>
        )}
        <Select label="Rows per page" value={String(size)} placeholder="Rows per page" className="w-32" disabled={locked}
          options={PAGE_SIZES.map((s) => ({ value: String(s), label: `${s} per page` }))}
          onChange={(v) => { setSize(Number(v)); toList(); }} />
      </div>
    </nav>
  );
}
