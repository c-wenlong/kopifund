import { useId, useState, type KeyboardEvent, type RefObject } from "react";
import { CalendarDays, X } from "lucide-react";

// G13: the text box of a date or month field. It shows the value in the app's
// format ("3 Oct 2026"), accepts typing in any way `parse` reads, and tells the
// field when to open its calendar. A value is passed up the moment the text
// reads as one. Text that never does stays in the box when it loses the focus,
// marked "Not a date" under it, until it is fixed or cleared: dropping it
// silently would look like the page took a date it never got.
export function PickerInput({
  value, onChange, parse, format, label, id, placeholder, clearable = false, disabled = false, required = false,
  className = "w-full", title, invalidText = "Not a date", picks = 0, open, popupId, inputRef, boxRef, skipOpen, active = false, onOpen, onClose, onEnter,
}: {
  value: string;
  onChange: (v: string) => void;
  /** Text to an ISO value, or null when it is not one. */
  parse: (text: string) => string | null;
  /** An ISO value as shown. */
  format: (iso: string) => string;
  label: string;
  id?: string;
  placeholder: string;
  clearable?: boolean;
  disabled?: boolean;
  required?: boolean;
  className?: string;
  title?: string;
  /** The message under the box while its text is not a value. */
  invalidText?: string;
  /** A counter the field moves each time its calendar makes a choice, even one equal to the value: the box then shows the value. */
  picks?: number;
  open: boolean;
  popupId: string;
  inputRef: RefObject<HTMLInputElement | null>;
  boxRef: RefObject<HTMLSpanElement | null>;
  /** True while the field itself gives the focus back, so that does not reopen the calendar. */
  skipOpen: RefObject<boolean>;
  /** This is the box the calendar is working on (the range fields share one calendar). */
  active?: boolean;
  onOpen: () => void;
  onClose: () => void;
  /** The arrow-down key: open the calendar and move into it. */
  onEnter: () => void;
}) {
  // what is being typed, or null while the box just shows the value
  const [draft, setDraft] = useState<string | null>(null);
  // the box lost the focus holding text that is not a value
  const [bad, setBad] = useState(false);
  // a value that changed under the box (a calendar pick, an arrow) replaces what was typed
  const [seen, setSeen] = useState(value);
  if (value !== seen) {
    setSeen(value);
    if (draft !== null && parse(draft) !== value) {
      setDraft(null);
      setBad(false);
    }
  }
  // a day picked in the calendar replaces what was typed, even when it is the day already held
  const [seenPicks, setSeenPicks] = useState(picks);
  if (picks !== seenPicks) {
    setSeenPicks(picks);
    setDraft(null);
    setBad(false);
  }
  const shown = draft ?? (value ? format(value) : "");
  // on a touch screen the keyboard would cover the calendar, so a tap opens the calendar alone
  const coarse = typeof window !== "undefined" && window.matchMedia?.("(pointer: coarse)").matches;
  const reactId = useId();

  function type(text: string) {
    setDraft(text);
    setBad(false);
    const iso = parse(text);
    if (iso) {
      if (iso !== value) onChange(iso);
    } else if (clearable && text.trim() === "" && value) {
      onChange("");
    }
  }

  function onKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      onEnter();
    } else if (e.key === "Escape" && open) {
      e.preventDefault();
      e.nativeEvent.stopPropagation();
      onClose();
    } else if (e.key === "Tab" && open) {
      // focus moves on as usual; the calendar does not stay behind
      onClose();
    } else if (e.key === "Enter" && open) {
      // Enter in a form submits it; with the calendar open it only closes the calendar
      e.preventDefault();
      onClose();
    }
  }

  const problem = useId();
  const blur = () => {
    // blank text is not an error: it is an empty box
    if (draft !== null && draft.trim() !== "" && parse(draft) === null) setBad(true);
    else setDraft(null);
  };

  return (
    <span ref={boxRef}
      className={`relative flex h-8 pointer-coarse:h-10 min-w-0 items-center rounded-md border border-line bg-panel text-[13px] focus-within:ring-2 focus-within:ring-[var(--focus)] ${
        active && open ? "ring-2 ring-[var(--focus)]" : ""} ${disabled ? "opacity-60" : ""} ${bad ? "mb-4" : ""} ${className}`}>
      <input
        ref={inputRef}
        id={id ?? reactId}
        type="text"
        aria-label={label}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-invalid={bad || undefined}
        aria-describedby={bad ? problem : undefined}
        aria-controls={open ? popupId : undefined}
        autoComplete="off"
        inputMode={coarse ? "none" : "text"}
        spellCheck={false}
        title={title}
        value={shown}
        placeholder={placeholder}
        disabled={disabled}
        required={required}
        onChange={(e) => type(e.target.value)}
        onFocus={() => { if (!skipOpen.current && !disabled) onOpen(); }}
        onClick={() => { if (!disabled) onOpen(); }}
        onBlur={blur}
        onKeyDown={onKeyDown}
        className="num h-full min-w-0 flex-1 bg-transparent px-2 outline-none"
      />
      {clearable && value && !disabled
        ? (
          <button type="button" aria-label={`Clear ${label}`}
            onClick={() => {
              onChange("");
              setDraft(null);
              setBad(false);
              skipOpen.current = true;
              inputRef.current?.focus();
              skipOpen.current = false;
            }}
            className="mr-1 shrink-0 rounded p-0.5 text-ink-3 hover:bg-hover hover:text-ink">
            <X size={13} aria-hidden />
          </button>
        )
        : <CalendarDays size={14} aria-hidden className="pointer-events-none mr-2 shrink-0 text-ink-3" />}
      {/* G13: the box leaves room under itself (mb-4) while this shows, so it never covers the line below */}
      {bad && <span id={problem} role="alert" className="absolute left-0 top-full z-10 mt-0.5 whitespace-nowrap text-[11px] text-danger">{invalidText}</span>}
    </span>
  );
}
