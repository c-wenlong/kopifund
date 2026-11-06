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

// Audit: a table whose rows turn into small cards under 1280px (Plan's lists, the growth inputs' income),
// each cell under its own name (`data-label`): two to a line on a phone, four from 768px.
export const CARD_ROW = "max-xl:relative max-xl:grid max-md:grid-cols-2 md:max-xl:grid-cols-4 max-xl:gap-2 max-xl:rounded-lg max-xl:border max-xl:border-line max-xl:p-3";
export const CARD_CELL = "max-xl:flex max-xl:min-w-0 max-xl:flex-col max-xl:gap-1 max-xl:p-0 max-xl:before:text-[12px] max-xl:before:font-medium max-xl:before:text-ink-2 max-xl:before:content-[attr(data-label)]";
