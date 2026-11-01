import { useId, useRef, useState } from "react";
import { DayPicker, type DateRange, type Matcher } from "react-day-picker";
import { formatDay, isoDay, parseDay, toLocalDate } from "../dates";
import { PickerInput } from "./PickerInput";
import { Popover } from "./Popover";

// G13: the app's date box. It shows a day as "3 Oct 2026", takes typing ("3 oct
// 2026", "2026-10-03", "3/10/2026" day first) and opens a calendar on a click
// or the focus, instead of the browser's own picker. The value in and out is
// the ISO day ("2026-10-03") or "" for none, so the callers and the API see
// what a native date box gave them.

const monthOf = (iso: string | null | undefined, fallback: Date = new Date()): Date => {
  const d = iso ? toLocalDate(iso) : null;
  return d ? new Date(d.getFullYear(), d.getMonth(), 1) : new Date(fallback.getFullYear(), fallback.getMonth(), 1);
};

function usePickerRefs() {
  return { inputRef: useRef<HTMLInputElement>(null), boxRef: useRef<HTMLSpanElement>(null), skipOpen: useRef(false) };
}

/** The open state, refs and focus rules every field with a calendar shares. */
function usePicker() {
  const [open, setOpen] = useState(false);
  // the arrow-down key opens the calendar and moves into it
  const [enter, setEnter] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const boxRef = useRef<HTMLSpanElement>(null);
  const skipOpen = useRef(false);
  const popupId = useId();
  const hide = (refocus = false) => {
    setOpen(false);
    setEnter(0);
    if (refocus && inputRef.current && document.activeElement !== inputRef.current) {
      skipOpen.current = true;
      inputRef.current.focus();
      skipOpen.current = false;
    }
  };
  return { open, setOpen, enter, setEnter, inputRef, boxRef, skipOpen, popupId, hide };
}

type Common = {
  /** The box's accessible name. */
  label: string;
  id?: string;
  placeholder?: string;
  disabled?: boolean;
  required?: boolean;
  /** Width and spacing of the box; it fills its cell by default. */
  className?: string;
  title?: string;
};

export function DateField({
  value, onChange, label, id, placeholder = "3 Oct 2026", disabled, required, className, title,
  clearable = false, min, max, defaultMonth, yearRange,
}: Common & {
  value: string;
  onChange: (iso: string) => void;
  /** A "to" date can be left empty: the box gets a clear button. */
  clearable?: boolean;
  /** The calendar greys out days before this ISO day. Typing is not held to it; the server still checks. */
  min?: string;
  /** The calendar greys out days after this ISO day. */
  max?: string;
  /** The month the calendar opens on while the box is empty. */
  defaultMonth?: Date;
  /** First and last year of a year dropdown, for a date far from today (a date of birth). */
  yearRange?: [number, number];
}) {
  const p = usePicker();
  const [month, setMonth] = useState(() => monthOf(value, defaultMonth));
  const [lastValue, setLastValue] = useState(value);
  // typing a date moves the open calendar to its month
  if (value !== lastValue) {
    setLastValue(value);
    if (value && toLocalDate(value)) setMonth(monthOf(value));
  }

  const selected = value ? toLocalDate(value) ?? undefined : undefined;
  const lo = min ? toLocalDate(min) : null;
  const hi = max ? toLocalDate(max) : null;
  const bounds: Matcher[] = [...(lo ? [{ before: lo }] : []), ...(hi ? [{ after: hi }] : [])];
  const [picks, setPicks] = useState(0);
  const choose = (iso: string) => {
    setPicks((n) => n + 1);
    onChange(iso);
    p.hide(true);
  };
  const today = isoDay();
  // a date far from today (a date of birth, with its year dropdown) has no use for a Today shortcut
  const todayOk = !yearRange && (!min || today >= min) && (!max || today <= max);

  return (
    <>
      <PickerInput value={value} onChange={onChange} parse={parseDay} format={formatDay} label={label} id={id}
        placeholder={placeholder} clearable={clearable} disabled={disabled} required={required} className={className}
        title={title} picks={picks} open={p.open} popupId={p.popupId} inputRef={p.inputRef} boxRef={p.boxRef} skipOpen={p.skipOpen}
        onOpen={() => { if (!p.open) { setMonth(monthOf(value, defaultMonth)); p.setOpen(true); } }}
        onClose={() => p.hide(false)}
        onEnter={() => { setMonth(monthOf(value, defaultMonth)); p.setOpen(true); p.setEnter((n) => n + 1); }} />
      {p.open && (
        <Popover anchor={p.boxRef} label={`${label} calendar`} id={p.popupId} focusKey={p.enter} onClose={p.hide}>
          <DayPicker mode="single" required selected={selected} month={month} onMonthChange={setMonth}
            onSelect={(d) => { if (d) choose(isoDay(d)); }}
            disabled={bounds.length ? bounds : undefined} {...pickerProps(yearRange)} />
          {todayOk && (
            <div className="mt-1 flex border-t border-line pt-1.5">
              <button type="button" onClick={() => choose(today)}
                className="rounded-md px-2 py-1 text-[12px] text-ink-2 hover:bg-hover hover:text-ink">Today</button>
            </div>
          )}
        </Popover>
      )}
    </>
  );
}

/** The DayPicker settings both fields share. */
function pickerProps(yearRange?: [number, number]) {
  return {
    className: "kf-cal",
    weekStartsOn: 1 as const,
    showOutsideDays: true,
    navLayout: "around" as const,
    ...(yearRange
      ? { captionLayout: "dropdown-years" as const, startMonth: new Date(yearRange[0], 0), endMonth: new Date(yearRange[1], 11) }
      : {}),
  };
}

/**
 * Two day boxes that work one calendar, for a start and an end: a trip's
 * first and last day. Pick the start and the end becomes the same day (a day
 * trip) with the calendar open at that month; pick the end to finish. A day
 * before the start begins the range again. Each cell is `<label>`, the text on
 * top and the box under it, so the two drop into a form grid as they are.
 */
export function DateRangeFields({
  start, end, onChange, startLabel, endLabel, startRequired = false, cellClassName = "flex flex-col gap-1",
}: {
  start: string;
  end: string;
  onChange: (start: string, end: string) => void;
  startLabel: string;
  endLabel: string;
  startRequired?: boolean;
  cellClassName?: string;
}) {
  const refs = { start: usePickerRefs(), end: usePickerRefs() };
  const popupId = useId();
  const [open, setOpen] = useState(false);
  // the arrow-down key opens the calendar and moves into it
  const [enter, setEnter] = useState(0);
  // the box the calendar hangs from, and the end of the range the next click sets
  const [at, setAt] = useState<"start" | "end">("start");
  const [which, setWhich] = useState<"start" | "end">("start");
  // per box: a pick that sets only the end leaves what is typed in the start box alone
  const [picks, setPicks] = useState({ start: 0, end: 0 });
  const [month, setMonth] = useState(() => monthOf(start || end));
  // typing a day moves the open calendar to its month
  const [seen, setSeen] = useState({ start, end });
  if (start !== seen.start || end !== seen.end) {
    setSeen({ start, end });
    const moved = start !== seen.start ? start : end;
    if (moved && toLocalDate(moved)) setMonth(monthOf(moved));
  }

  // a start sets the end with it unless the end is already on or after it
  const setStart = (iso: string) => onChange(iso, !end || end < iso ? iso : end);
  const setEnd = (iso: string) => onChange(start, iso);
  const close = (refocus: boolean) => {
    setOpen(false);
    setEnter(0);
    const input = refs[at].inputRef.current;
    if (refocus && input && document.activeElement !== input) {
      refs[at].skipOpen.current = true;
      input.focus();
      refs[at].skipOpen.current = false;
    }
  };

  const show = (w: "start" | "end", focusIn = false) => {
    if (open && at === w && !focusIn) return;
    setAt(w);
    setWhich(w === "end" && !start ? "start" : w);
    // the calendar opens at the start's month; on the end box at the end's, if it is another day
    setMonth(monthOf(w === "end" && end && end !== start ? end : start || end));
    setOpen(true);
    setEnter((n) => (focusIn ? n + 1 : 0));
  };

  const range: DateRange | undefined = start
    ? { from: toLocalDate(start) ?? undefined, to: end && end >= start ? toLocalDate(end) ?? undefined : undefined }
    : undefined;

  function pick(day: Date) {
    const iso = isoDay(day);
    if (which === "start" || !start || iso < start) {
      // a start (or a day before the start) begins the range: a day trip until the end is picked
      setPicks((n) => ({ start: n.start + 1, end: n.end + 1 }));
      onChange(iso, iso);
      setWhich("end");
      return;
    }
    setPicks((n) => ({ ...n, end: n.end + 1 }));
    setEnd(iso);
    close(true);
  }

  const field = (w: "start" | "end", text: string, label: string, value: string, set: (v: string) => void, placeholder: string, required = false) => (
    <label className={cellClassName}>{text}
      <PickerInput value={value} onChange={set} parse={parseDay} format={formatDay} label={label} placeholder={placeholder}
        required={required} picks={picks[w]} open={open} popupId={popupId} inputRef={refs[w].inputRef} boxRef={refs[w].boxRef} skipOpen={refs[w].skipOpen}
        active={which === w} onOpen={() => show(w)} onClose={() => close(false)} onEnter={() => show(w, true)} />
    </label>
  );

  return (
    <>
      {field("start", startLabel, startLabel, start, setStart, "3 Oct 2026", startRequired)}
      {field("end", endLabel, endLabel, end, setEnd, "Same day")}
      {open && (
        <Popover anchor={refs[at].boxRef} label={`${at === "start" ? startLabel : endLabel} calendar`} id={popupId}
          focusKey={enter} onClose={close}>
          {/* onSelect is given so the range stays ours: without it the picker keeps its own copy of `selected` */}
          <DayPicker mode="range" selected={range} onSelect={() => {}} month={month} onMonthChange={setMonth} onDayClick={pick}
            {...pickerProps()} />
          <div className="mt-1 flex border-t border-line pt-1.5">
            <button type="button" onClick={() => pick(new Date())}
              className="rounded-md px-2 py-1 text-[12px] text-ink-2 hover:bg-hover hover:text-ink">Today</button>
          </div>
        </Popover>
      )}
    </>
  );
}
