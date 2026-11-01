import { useEffect, useId, useLayoutEffect, useRef, useState, type KeyboardEvent } from "react";
import { createPortal } from "react-dom";
import { ChevronDown } from "lucide-react";

// G12: a text box with a dropdown. Typing filters the options; only an option
// from the list can be chosen. Leaving the box (Tab included) takes the exact
// match, else the highlighted option; text no option contains puts the last
// choice back. The list is drawn on document.body at the box's position, so a
// scrolling table around the box cannot clip it.
export function Combobox({ label, value, options, onChange, placeholder = "Choose", className = "", empty = "No category matches", disabled = false, autoFocus = false, onDone, commitOnLeave = true }: {
  label: string;
  value: string;
  options: string[];
  onChange: (value: string) => void;
  placeholder?: string;
  className?: string;
  /** What the list says when nothing matches what was typed. */
  empty?: string;
  disabled?: boolean;
  /** G13: open with the list showing, on the current value (a one-tap picker in a table cell). */
  autoFocus?: boolean;
  /** Told once the box closes: a choice, Escape, or leaving it. */
  onDone?: () => void;
  /** G13: false for a picker whose choice saves at once: leaving it (a click away, Tab) changes nothing, only a choice does. */
  commitOnLeave?: boolean;
}) {
  const id = useId();
  const [text, setText] = useState<string | null>(null);   // null: not typing, show the value
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const box = useRef<HTMLInputElement>(null);
  const [at, setAt] = useState<{ left: number; width: number; top?: number; bottom?: number } | null>(null);

  // follow the box while the list is open: the page or the table may scroll
  useLayoutEffect(() => {
    if (!open) return;
    const place = () => {
      const r = box.current?.getBoundingClientRect();
      if (!r) return;
      // open upwards when the list (at most 224px) would run off the bottom
      const up = r.bottom + 232 > window.innerHeight && r.top > 232;
      setAt(up ? { left: r.left, width: r.width, bottom: window.innerHeight - r.top + 4 }
        : { left: r.left, width: r.width, top: r.bottom + 4 });
    };
    place();
    window.addEventListener("scroll", place, true);
    window.addEventListener("resize", place);
    return () => {
      window.removeEventListener("scroll", place, true);
      window.removeEventListener("resize", place);
    };
  }, [open]);

  // keep the highlighted option on screen as the arrow keys move it
  useEffect(() => {
    if (open) document.getElementById(`${id}-${active}`)?.scrollIntoView?.({ block: "nearest" });
  }, [open, active, id]);

  const typed = text ?? "";
  const shown = text === null || text === value
    ? options
    : options.filter((o) => o.toLowerCase().includes(typed.trim().toLowerCase()));

  function choose(option: string) {
    onChange(option);
    setText(null);
    setOpen(false);
    onDone?.();
  }

  function leave() {
    // G13: leaving after typing (Tab, or a click elsewhere) accepts what the list
    // points at: the exact match, else the highlighted option, which is the only
    // one left when the text narrowed it to one. Text that no option contains is undone.
    const typedText = text?.trim().toLowerCase() ?? "";
    const exact = typedText ? options.find((o) => o.toLowerCase() === typedText) : undefined;
    const pick = exact ?? (typedText ? shown[Math.min(active, shown.length - 1)] : undefined);
    if (commitOnLeave && pick && pick !== value) onChange(pick);
    setText(null);
    setOpen(false);
    onDone?.();
  }

  function onKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (!open) { setOpen(true); setActive(Math.max(0, shown.indexOf(value))); return; }
      const step = e.key === "ArrowDown" ? 1 : -1;
      setActive((a) => (shown.length ? (a + step + shown.length) % shown.length : 0));
    } else if (e.key === "Enter" && open) {
      e.preventDefault();
      if (shown[active]) choose(shown[active]);
    } else if (e.key === "Escape") {
      // G18: an Escape that closes the list or undoes typing is used up here, so a
      // Sheet or the phone menu around the box stays open; a second one reaches them
      if (open || text !== null) {
        e.preventDefault();
        e.nativeEvent.stopPropagation();
      }
      setText(null);
      setOpen(false);
      onDone?.();
    }
  }

  const listId = `${id}-list`;
  return (
    <div className={`relative ${className}`}>
      <input
        ref={box}
        role="combobox"
        aria-label={label}
        aria-expanded={open}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={open && shown[active] ? `${id}-${active}` : undefined}
        value={text ?? value}
        placeholder={placeholder}
        disabled={disabled}
        autoFocus={autoFocus}
        onClick={() => setOpen(true)}
        onChange={(e) => { setText(e.target.value); setOpen(true); setActive(0); }}
        onFocus={(e) => { e.target.select(); setOpen(true); setActive(Math.max(0, options.indexOf(value))); }}
        onBlur={leave}
        onKeyDown={onKeyDown}
        className="h-8 w-full rounded-md border border-line bg-panel pl-2 pr-7 text-[13px] disabled:opacity-60"
      />
      <ChevronDown size={14} aria-hidden className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 text-ink-3" />
      {open && createPortal(
        <ul id={listId} role="listbox" aria-label={label}
          style={at ?? { visibility: "hidden" }}
          className="fixed z-50 m-0 max-h-56 list-none overflow-auto rounded-md border border-line bg-panel p-1 shadow-lg">
          {shown.length === 0 && <li className="px-2 py-1 text-[13px] text-ink-3">{empty}</li>}
          {shown.map((o, i) => (
            <li key={o} id={`${id}-${i}`} role="option" aria-selected={o === value}
              // mousedown, not click: the input's blur would close the list first
              onMouseDown={(e) => { e.preventDefault(); choose(o); }}
              onMouseEnter={() => setActive(i)}
              className={`cursor-pointer rounded px-2 py-1 text-[13px] ${i === active ? "bg-hover" : ""} ${o === value ? "font-medium" : ""}`}>
              {o}
            </li>
          ))}
        </ul>,
        document.body,
      )}
    </div>
  );
}
