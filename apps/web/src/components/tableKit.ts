// G18 (U5): the one table look, as class sets every table spreads, so a
// header reads the same on Transactions, Rules, Recurring, Plan and the stat
// tables. A table keeps its own cell padding and its phone layout (rows that
// turn into cards); only the box, the header and the row rule come from here.

/** The box around a table: it scrolls sideways inside itself on a narrow screen, so the page never does. */
export const TABLE_BOX = "overflow-x-auto rounded-lg border border-line";

/** The same box for a table that stands on the page by itself, like a card (Transactions). */
export const TABLE_CARD = "overflow-x-auto rounded-xl border border-line bg-panel";

/** The header row: small, quiet, left-aligned text. */
export const HEAD_ROW = "text-left text-[12px] text-ink-3";

/** A header cell (add the table's own side padding and `text-right` for a figure column). */
export const TH = "py-1.5 font-medium";

/** A body row: a hairline above it. */
export const ROW = "border-t border-line";
