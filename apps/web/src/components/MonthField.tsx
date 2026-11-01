import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { formatMonth, isoMonth, MONTHS, parseMonth } from "../dates";
import { PickerInput } from "./PickerInput";
import { Popover } from "./Popover";

// G13: the app's month box. It shows a month as "Oct 2026", takes typing
// ("oct 2026", "2026-10", "10/2026") and opens a year header over a grid of
// twelve months, instead of the browser's own month picker. The value in and
// out is the ISO month ("2026-10") or "" for none.

/** The month a linear index names: year * 12 + (month - 1). */
const indexOf = (iso: string | undefined): number | null => {
  const hit = iso ? /^(\d{4})-(\d{2})/.exec(iso) : null;
  return hit ? Number(hit[1]) * 12 + Number(hit[2]) - 1 : null;
};
const isoOf = (i: number) => `${String(Math.floor(i / 12)).padStart(4, "0")}-${String((i % 12) + 1).padStart(2, "0")}`;

export function MonthField({
  value, onChange, label, id, placeholder = "Oct 2026", disabled, required, className, title, clearable = false,
}: {
  value: string;
  onChange: (iso: string) => void;
  /** The box's accessible name. */
  label: string;
  id?: string;
  placeholder?: string;
  disabled?: boolean;
  required?: boolean;
  /** Width and spacing of the box; it fills its cell by default. */
  className?: string;
  title?: string;
  /** The month can be left empty: the box gets a clear button. */
  clearable?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [enter, setEnter] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const boxRef = useRef<HTMLSpanElement>(null);
  const skipOpen = useRef(false);
  const popupId = useId();

  const [picks, setPicks] = useState(0);
  const hide = (refocus = false) => {
    setOpen(false);
    setEnter(0);
    if (refocus && inputRef.current && document.activeElement !== inputRef.current) {
      skipOpen.current = true;
      inputRef.current.focus();
      skipOpen.current = false;
    }
  };

  return (
    <>
      <PickerInput invalidText="Not a month" value={value} onChange={onChange} parse={parseMonth} format={formatMonth} label={label} id={id}
        placeholder={placeholder} clearable={clearable} disabled={disabled} required={required} className={className}
        title={title} picks={picks} open={open} popupId={popupId} inputRef={inputRef} boxRef={boxRef} skipOpen={skipOpen}
        onOpen={() => setOpen(true)} onClose={() => hide(false)} onEnter={() => { setOpen(true); setEnter((n) => n + 1); }} />
      {open && (
        <Popover anchor={boxRef} label={`${label} calendar`} id={popupId} focusKey={enter} onClose={hide}>
          <MonthGrid value={value} onPick={(iso) => { setPicks((n) => n + 1); onChange(iso); hide(true); }} />
        </Popover>
      )}
    </>
  );
}

/**
 * A year header with arrows over a 3 by 4 grid of months. The arrow keys move
 * between months (left and right by one, up and down by a row), Page Up and
 * Page Down by a year, and crossing January or December changes the year.
 */
function MonthGrid({ value, onPick }: { value: string; onPick: (iso: string) => void }) {
  const now = indexOf(isoMonth()) as number;
  const selected = indexOf(value);
  const [cursor, setCursor] = useState(selected ?? now);
  // a month typed while the grid is open moves it there; browsing years by hand is left alone
  const [seen, setSeen] = useState(selected);
  if (selected !== seen) {
    setSeen(selected);
    if (selected != null) setCursor(selected);
  }
  const grid = useRef<HTMLDivElement>(null);
  const year = Math.floor(cursor / 12);

  // the focus follows the cursor only while it is in the grid; a month typed in the box never pulls it away
  useEffect(() => {
    if (grid.current?.contains(document.activeElement)) grid.current.querySelector<HTMLElement>('button[tabindex="0"]')?.focus();
  }, [cursor]);

  function onKeyDown(e: KeyboardEvent) {
    const step: Record<string, number> = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -3, ArrowDown: 3, PageUp: -12, PageDown: 12 };
    if (e.key in step) {
      e.preventDefault();
      setCursor(cursor + step[e.key]);
    } else if (e.key === "Home" || e.key === "End") {
      e.preventDefault();
      setCursor(year * 12 + (e.key === "Home" ? 0 : 11));
    }
  }

  const nav = "flex size-7 items-center justify-center rounded-md text-ink-2 hover:bg-hover hover:text-ink";
  return (
    <div className="w-56">
      <div className="mb-1 flex items-center justify-between">
        <button type="button" aria-label="Previous year" className={nav} onClick={() => setCursor(cursor - 12)}>
          <ChevronLeft size={16} aria-hidden />
        </button>
        <span className="num text-[13px] font-semibold" aria-live="polite">{year}</span>
        <button type="button" aria-label="Next year" className={nav} onClick={() => setCursor(cursor + 12)}>
          <ChevronRight size={16} aria-hidden />
        </button>
      </div>
      <div ref={grid} role="group" aria-label={`Months of ${year}`} onKeyDown={onKeyDown} className="grid grid-cols-3 gap-1">
        {MONTHS.map((m, k) => {
          const i = year * 12 + k;
          const picked = i === selected;
          return (
            <button key={m} type="button" tabIndex={i === cursor ? 0 : -1} aria-pressed={picked}
              aria-label={formatMonth(isoOf(i))} onClick={() => onPick(isoOf(i))}
              className={`h-9 rounded-md text-[13px] ${
                picked ? "bg-ink font-semibold text-page" : `hover:bg-hover ${i === now ? "font-semibold ring-1 ring-inset ring-[var(--line)]" : ""}`}`}>
              {m}
            </button>
          );
        })}
      </div>
    </div>
  );
}
