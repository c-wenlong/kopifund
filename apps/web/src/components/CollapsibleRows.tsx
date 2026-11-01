import { Fragment, useState } from "react";
import { ChevronDown } from "lucide-react";

// G13: a list that sits among other cards shows only its first few rows, with
// one more row at the bottom that opens the rest. How many depends on how tall
// a row is, so a card never grows past the room it shares with its neighbours.
// The toggle is left-aligned, not centred: a table in a phone-width scroll box
// is wider than the screen, and a centred label would sit past its edge.
export const COLLAPSED_ROWS = { thick: 3, mid: 4, thin: 5 } as const;

type Props<T> = {
  rows: readonly T[];
  /** Rows shown while collapsed: COLLAPSED_ROWS.thick, .mid or .thin. */
  limit: number;
  render: (row: T, index: number) => React.ReactNode;
  keyOf?: (row: T, index: number) => string;
  /** What the toggle row is: an `li` in a list, a `tr` (spanning `colSpan` columns) in a table body. */
  as?: "li" | "tr";
  colSpan?: number;
  /** What the closed toggle says when "Show all N" does not count what the reader sees (cards under headers). */
  allLabel?: string;
  /** Extra classes for the toggle row, e.g. `col-span-full` in a grid. */
  toggleClass?: string;
};

/**
 * The first `limit` rows, then a full-width toggle row ("Show all 12" / "Show
 * fewer", a down arrow that flips). The toggle row is there only when the list
 * is longer than `limit`. Put it inside the `ul` or `tbody` that holds the rows.
 */
export function CollapsibleRows<T>({ rows, limit, render, keyOf, as = "li", colSpan = 1, allLabel, toggleClass = "" }: Props<T>) {
  const [open, setOpen] = useState(false);
  const long = rows.length > limit;
  const shown = long && !open ? rows.slice(0, limit) : rows;
  const toggle = (
    <button type="button" onClick={() => setOpen(!open)} aria-expanded={open}
      className="flex w-full items-center justify-start gap-1.5 py-1.5 text-[12px] text-ink-2 hover:bg-hover">
      {open ? "Show fewer" : allLabel ?? `Show all ${rows.length}`}
      <ChevronDown size={14} aria-hidden className={`transition-transform ${open ? "rotate-180" : ""}`} />
    </button>
  );
  return (
    <>
      {shown.map((r, i) => <Fragment key={keyOf ? keyOf(r, i) : i}>{render(r, i)}</Fragment>)}
      {long && (as === "tr"
        ? <tr className="border-t border-line"><td colSpan={colSpan} className="p-0">{toggle}</td></tr>
        : <li className={`border-t border-line ${toggleClass}`}>{toggle}</li>)}
    </>
  );
}
