import { Fragment, useEffect, useId, useLayoutEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { Check, ChevronDown } from "lucide-react";

// a disabled option is listed but cannot be chosen; `note` sits after its label.
// G18: `group` heads a run of options, as an <optgroup> does (options of one group sit together)
export type Option = { value: string; label: string; icon?: ReactNode; disabled?: boolean; note?: string; group?: string };

// G18: the options cut into runs that share a group (an ungrouped run has none), each with its options' indexes
function runs(options: Option[]) {
  const out: { group?: string; start: number; items: [Option, number][] }[] = [];
  options.forEach((o, i) => {
    const last = out[out.length - 1];
    if (last && last.group === o.group) last.items.push([o, i]);
    else out.push({ group: o.group, start: i, items: [[o, i]] });
  });
  return out;
}

// A dropdown whose options can carry an icon, which a native <select> cannot
// draw. A button opens a listbox; arrows, Home, End, Enter and Escape work as
// in a native one, and (G18) typing a letter jumps to the next option that
// starts with it, open or closed, as a native one does. It is the app's only
// dropdown: no native <select> is drawn anywhere. Like Combobox, the list is
// drawn on document.body so a card around it cannot clip it.
export function Select({ label, value, options, onChange, placeholder, disabled = false, className = "", id: buttonId, invalid = false }: {
  label: string;
  value: string;
  options: Option[];
  onChange: (value: string) => void;
  placeholder: string;
  disabled?: boolean;
  className?: string;
  id?: string;           // so a <label htmlFor> can point at it
  invalid?: boolean;     // a value the form refuses: the box is drawn in the danger colour
}) {
  const id = useId();
  const typed = useRef({ text: "", at: 0 });
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const button = useRef<HTMLButtonElement>(null);
  const list = useRef<HTMLUListElement>(null);
  const [at, setAt] = useState<{ left: number; minWidth: number; top?: number; bottom?: number } | null>(null);
  const chosen = options.find((o) => o.value === value);

  useLayoutEffect(() => {
    if (!open) return;
    const place = () => {
      const r = button.current?.getBoundingClientRect();
      if (!r) return;
      // open upwards when the list (at most 384px) would run off the bottom
      const up = r.bottom + 392 > window.innerHeight && r.top > 392;
      setAt(up ? { left: r.left, minWidth: r.width, bottom: window.innerHeight - r.top + 4 }
        : { left: r.left, minWidth: r.width, top: r.bottom + 4 });
    };
    place();
    window.addEventListener("scroll", place, true);
    window.addEventListener("resize", place);
    return () => {
      window.removeEventListener("scroll", place, true);
      window.removeEventListener("resize", place);
    };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    list.current?.focus();
    document.getElementById(`${id}-${active}`)?.scrollIntoView?.({ block: "nearest" });
  }, [open, active, id]);

  // a click anywhere else closes the list without choosing
  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => {
      const t = e.target as Node;
      if (!list.current?.contains(t) && !button.current?.contains(t)) setOpen(false);
    };
    document.addEventListener("mousedown", away);
    return () => document.removeEventListener("mousedown", away);
  }, [open]);

  const usable = options.map((o, i) => (o.disabled ? -1 : i)).filter((i) => i >= 0);

  // G18: the option that typing `key` reaches from `from`, as a native select finds it: letters typed
  // quickly build a prefix; one letter again and again cycles through the options starting with it
  function typeahead(key: string, from: number): number | null {
    const now = Date.now();
    const t = typed.current;
    t.text = now - t.at < 700 ? t.text + key.toLowerCase() : key.toLowerCase();
    t.at = now;
    const cycle = [...t.text].every((c) => c === t.text[0]);
    const prefix = cycle ? t.text[0] : t.text;
    const here = usable.indexOf(from);
    const order = [...usable.slice(here + (cycle ? 1 : 0)), ...usable.slice(0, here + (cycle ? 1 : 0))];
    const hit = order.find((i) => options[i].label.toLowerCase().startsWith(prefix));
    if (hit != null) return hit;
    // nothing starts with the letters typed so far: start again from this one
    if (t.text.length > 1) {
      t.text = "";
      return typeahead(key, from);
    }
    return null;
  }
  const printable = (e: KeyboardEvent) => e.key.length === 1 && e.key !== " " && !e.ctrlKey && !e.metaKey && !e.altKey;

  function show() {
    const at = options.findIndex((o) => o.value === value && !o.disabled);
    setActive(at >= 0 ? at : usable[0] ?? 0);
    setOpen(true);
  }

  function choose(o: Option) {
    if (o.disabled) return;
    // G18: choosing the value it already holds is no change, as with a native select
    if (o.value !== value) onChange(o.value);
    setOpen(false);
    button.current?.focus();
  }

  function onButtonKey(e: KeyboardEvent) {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      show();
    } else if (printable(e)) {
      // closed, a letter changes the value at once, as a native select does
      const hit = typeahead(e.key, options.findIndex((o) => o.value === value));
      if (hit != null && options[hit].value !== value) onChange(options[hit].value);
    }
  }

  function onListKey(e: KeyboardEvent) {
    // the arrows, Home and End step over disabled options
    const here = usable.indexOf(active);
    const moves: Record<string, () => number> = {
      ArrowDown: () => usable[Math.min(usable.length - 1, here + 1)] ?? active,
      ArrowUp: () => usable[Math.max(0, here - 1)] ?? active,
      Home: () => usable[0] ?? active,
      End: () => usable[usable.length - 1] ?? active,
    };
    if (moves[e.key]) {
      e.preventDefault();
      setActive(moves[e.key]());
    } else if (printable(e)) {
      const hit = typeahead(e.key, active);
      if (hit != null) setActive(hit);
    } else if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      if (options[active]) choose(options[active]);
    } else if (e.key === "Escape" || e.key === "Tab") {
      if (e.key === "Escape") {
        // G18: closing the list uses the Escape up, so a Sheet or the phone menu around it stays open
        e.preventDefault();
        e.nativeEvent.stopPropagation();
      }
      setOpen(false);
      button.current?.focus();
    }
  }

  const listId = `${id}-list`;
  return (
    <>
      <button
        ref={button}
        id={buttonId}
        type="button"
        aria-label={label}
        aria-invalid={invalid || undefined}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        disabled={disabled}
        onClick={() => (open ? setOpen(false) : show())}
        onKeyDown={onButtonKey}
        className={`flex h-8 items-center gap-2 rounded-md border bg-panel pl-2 pr-2 text-left hover:bg-hover disabled:pointer-events-none disabled:opacity-40 ${invalid ? "border-danger" : "border-line"} ${className}`}
      >
        {chosen?.icon}
        <span className={`flex-1 truncate ${chosen ? "" : "text-ink-3"}`}>{chosen?.label ?? placeholder}</span>
        <ChevronDown size={14} aria-hidden className="shrink-0 text-ink-3" />
      </button>
      {open && createPortal(
        <ul ref={list} id={listId} role="listbox" aria-label={label} tabIndex={-1}
          aria-activedescendant={`${id}-${active}`}
          onKeyDown={onListKey}
          style={at ?? { visibility: "hidden" }}
          className="fixed z-50 m-0 max-h-96 list-none overflow-auto rounded-md border border-line bg-panel p-1 shadow-lg outline-none">
          {runs(options).map((run) => {
            const items = run.items.map(([o, i]) => (
              <li key={o.value} id={`${id}-${i}`} role="option" aria-selected={o.value === value}
                aria-disabled={o.disabled || undefined}
                onMouseDown={(e) => { e.preventDefault(); choose(o); }}
                onMouseEnter={() => !o.disabled && setActive(i)}
                className={`flex items-center gap-2 rounded px-2 py-1.5 text-[13px] ${
                  o.disabled ? "cursor-default opacity-40 grayscale" : `cursor-pointer ${i === active ? "bg-hover" : ""}`}`}>
                {o.icon}
                <span className="flex-1 whitespace-nowrap">{o.label}</span>
                {o.note && <span className="whitespace-nowrap text-[12px] text-ink-3">{o.note}</span>}
                <Check size={14} aria-hidden className={o.value === value ? "text-ink-2" : "invisible"} />
              </li>
            ));
            if (run.group == null) return <Fragment key={`run-${run.start}`}>{items}</Fragment>;
            // G18: a named group, as an <optgroup> is, so a screen reader gives the heading with its options
            const head = `${id}-group-${run.start}`;
            return (
              <li key={`run-${run.start}`} role="group" aria-labelledby={head}>
                <div id={head} className="px-2 pb-0.5 pt-1.5 text-[12px] font-medium text-ink-3">{run.group}</div>
                <ul role="none" className="m-0 list-none p-0">{items}</ul>
              </li>
            );
          })}
        </ul>,
        document.body,
      )}
    </>
  );
}
