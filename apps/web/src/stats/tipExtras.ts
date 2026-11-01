// G13: a stat's chart plots two or three series, but its rows carry more
// figures (the investing in a month, the accounts a balance is made of, the
// rows behind a bar). They used to sit in a Details table; now the chart's
// hover card lists them under the series.
import { bareUnit, columnUnit, fmt, isNum, label, type Row } from "./hint";
import { CENTS_UNIT, MINOR } from "./money";

// fields a card leaves out because the hover card already says them (the total
// a stack adds up to, the Salary column that repeats the salary series)
export const TIP_SKIP: Record<string, string[]> = {
  "income-stability": ["total", "Salary"],
};
// a true flag on a row, in words
const TIP_FLAG: Record<string, string> = {
  partial: "Some statements for this month are missing",
};
// a list on a row, as a count in words
const TIP_LIST: Record<string, (n: number) => string> = {
  not_started: (n) => `${n} ${n === 1 ? "account has" : "accounts have"} no statement yet`,
  carried: (n) => `${n} ${n === 1 ? "account shows" : "accounts show"} an earlier month's balance`,
};

// A snake_case field name says what it is (a count, a share). Any other key is
// a name the data gave (an account, an income category): in cents when it ends
// in `_minor` ("Main account_minor"), else a number in the chart's unit when
// that is not money, else a plain number.
const unitOf = (key: string, units?: Record<string, string>, unit?: string) =>
  /^[a-z0-9_]+$/.test(key) ? columnUnit(key, units, unit)
    : units?.[key] ?? (key.endsWith(MINOR) ? CENTS_UNIT : bareUnit(unit));

export type TipExtras = { lines: { key: string; name: string; text: string }[]; notes: string[] };

/**
 * What a row says beyond its plotted series: every other figure that is not
 * zero, in its own unit, then a note for each flag or list the row carries.
 */
export function tipExtras(
  statId: string, row: Row | undefined, xKey: string, series: string[], units?: Record<string, string>, unit?: string,
): TipExtras {
  const out: TipExtras = { lines: [], notes: [] };
  if (!row) return out;
  // exact names: an account called "Cash" is not the plotted "cash"
  const skip = new Set([xKey, ...series, ...(TIP_SKIP[statId] ?? [])]);
  for (const [key, v] of Object.entries(row)) {
    // G20: a field in cents is skipped by its bare name too ("Salary_minor" by "Salary")
    if (skip.has(key) || (key.endsWith(MINOR) && skip.has(key.slice(0, -MINOR.length)))) continue;
    if (isNum(v)) {
      if (v !== 0) out.lines.push({ key, name: label(key), text: fmt(v, unitOf(key, units, unit), true) });
    } else if (v === true && TIP_FLAG[key]) out.notes.push(TIP_FLAG[key]);
    else if (Array.isArray(v) && v.length && TIP_LIST[key]) out.notes.push(TIP_LIST[key](v.length));
  }
  return out;
}
