// G13: the one box for an amount the owner types: a budget, a pay figure, a
// trip's flights. A plain text box (so no spinner arrows and no scroll-wheel
// changes, which a number box has) that opens the phone's decimal keypad, takes
// "6,000.50" pasted as it is, shows thousands grouped once the cursor leaves,
// and on Enter moves to the next amount, as the keypad's "next" key does.
//
// It is controlled by text. What is typed stays exactly what is typed, so
// "17." can be on its way to "17.5"; the caller decides whether the text is a
// number (`isMoneyText`) and what to do when it is not. G17: an amount's text
// becomes cents through money.ts (parseCents), never through a float, and
// parseCents is NaN for a shape that is finer than a cent ("12.345"), so a
// caller checks that too before it saves. Audit: the grammar and the comma
// stripping are money.ts's, one copy for the box and for parseCents.
import { useState } from "react";
import { isMoneyShape, unformatMoney } from "../money";

export { unformatMoney };

/** True when the text, less any commas, is digits with at most one decimal point. */
export const isMoneyText = (s: string): boolean => isMoneyShape(s);

/** The same with an optional minus in front: a rate can be under zero, an amount cannot. */
export const isSignedText = (s: string): boolean => isMoneyShape(unformatMoney(s).replace(/^-/, ""));

/** "6000.5" as "6,000.5": the whole part grouped by thousands, the rest as typed. Text that is not a number comes back as it was. */
export function groupMoney(s: string): string {
  const t = unformatMoney(s);
  if (!isMoneyShape(t)) return s;
  const [whole, ...rest] = t.split(".");
  return [whole.replace(/\B(?=(\d{3})+(?!\d))/g, ","), ...rest].join(".");
}

/** "012" as "12", "0.50" and "0" as they are: a zero only leads when a point or nothing follows it. */
export const trimZeros = (s: string): string => s.replace(/^(-?)0+(?=\d)/, "$1");

const BOX = "flex h-8 pointer-coarse:h-10 min-w-0 items-center rounded-md border bg-panel text-[13px] focus-within:ring-2 focus-within:ring-[var(--focus)]";

// Enter does what the keypad's "next" key says: on to the next amount in the
// same card, or off the box when it is the last. A card's own sections (Income,
// Investing, Spending) sit inside it, so the scope is the outermost one.
function next(from: HTMLInputElement) {
  let scope: Element | null = from.closest("section, form");
  for (let up = scope?.parentElement?.closest("section, form"); up; up = scope?.parentElement?.closest("section, form")) scope = up;
  scope ??= document.body;
  const boxes = [...scope.querySelectorAll<HTMLInputElement>('input[inputmode="decimal"]:not(:disabled)')];
  const to = boxes[boxes.indexOf(from) + 1];
  if (to) to.focus();
  else from.blur();
}

export function MoneyInput({ id, label, value, onChange, onCommit, before, after, placeholder, invalid = false, describedBy, group = true, disabled = false, className = "" }: {
  id?: string;
  /** The accessible name; the visible label is the caller's. */
  label: string;
  value: string;
  onChange: (text: string) => void;
  /** The box is left, or Enter pressed, with this text. */
  onCommit?: (text: string) => void;
  /** A unit written before the figure ("S$"), and one after it ("% a year"). */
  before?: string;
  after?: string;
  placeholder?: string;
  /** Draws the border in the danger colour and marks the box invalid. */
  invalid?: boolean;
  describedBy?: string;
  /** False for a figure that is not an amount (a percentage, months of pay): no thousands. */
  group?: boolean;
  disabled?: boolean;
  className?: string;
}) {
  const [focused, setFocused] = useState(false);
  return (
    <span className={`${BOX} ${invalid ? "border-danger" : "border-line"} ${className}`}>
      {before && <span className="shrink-0 pl-2 text-ink-3" aria-hidden>{before}</span>}
      <input id={id} aria-label={label} type="text" inputMode="decimal" enterKeyHint="next" autoComplete="off" spellCheck={false}
        value={focused || !group ? unformatMoney(value) : groupMoney(value)} placeholder={placeholder} disabled={disabled}
        aria-invalid={invalid || undefined} aria-describedby={describedBy}
        onFocus={() => setFocused(true)}
        onBlur={(e) => { setFocused(false); onCommit?.(unformatMoney(e.target.value)); }}
        onChange={(e) => onChange(trimZeros(unformatMoney(e.target.value)))}
        onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); next(e.currentTarget); } }}
        className="num h-full w-full min-w-0 bg-transparent px-2 outline-none" />
      {after && <span className="shrink-0 whitespace-nowrap pr-2 text-ink-3" aria-hidden>{after}</span>}
    </span>
  );
}
