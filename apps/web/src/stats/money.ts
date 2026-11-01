// G20: a stat result's money is integer cents. A money field is named
// `<name>_minor`, and a chart hint's unit for money is `sgd_minor`; a number
// under any other name is not money, whatever its name or size. A result from
// before G20 held dollars under the bare name (unit `sgd`, or no unit): the
// results cache leaves such a result out, and `inCents` keeps a copy the
// browser kept from before off the page too, so no dollar figure is ever read.
import type { Cents } from "../money";

export const MINOR = "_minor";
/** The chart hint's unit for a money field in cents. */
export const CENTS_UNIT = "sgd_minor";

const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

/**
 * The money field `name` of a result's object (a row, a summary) in cents:
 * `<name>_minor`, when it is a whole number. Null when it is absent, null or
 * not whole. A bare `<name>` is never read: it is a dollar figure from before G20.
 */
export function centsOf(obj: unknown, name: string): Cents | null {
  if (!obj || typeof obj !== "object" || Array.isArray(obj)) return null;
  const v = (obj as Record<string, unknown>)[name + MINOR];
  return isNum(v) && Number.isInteger(v) ? v : null;
}

const isDict = (o: unknown): o is Record<string, unknown> => !!o && typeof o === "object" && !Array.isArray(o);

// any key, at any depth of dicts and lists, ending in `_minor`
function holdsMinor(o: unknown): boolean {
  if (isDict(o)) return Object.keys(o).some((k) => k.endsWith(MINOR)) || Object.values(o).some(holdsMinor);
  if (Array.isArray(o)) return o.some(holdsMinor);
  return false;
}

/**
 * Whether a stat result holds its money in cents, so a card may show it. The
 * same rule, in the same order, as finance/money.py's `in_cents`: the chart
 * hint's unit decides, never the size of a value. Unit `sgd_minor` is cents;
 * unit `sgd`, or no unit (which meant `sgd`), is a result from before G20 in
 * dollars; a chart that is not money (data-quality counts rows) is in cents
 * when its data or params hold a `<name>_minor` field.
 */
export function inCents(result: unknown): boolean {
  if (!isDict(result)) return false;
  const chart = result.chart;
  const unit = isDict(chart) ? chart.unit : undefined;
  if (unit === CENTS_UNIT) return true;
  if (unit === undefined || unit === null || unit === "sgd") return false;
  return holdsMinor(result.data) || holdsMinor(result.params);
}
