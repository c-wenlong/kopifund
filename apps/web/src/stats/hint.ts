// G5: how a stat's result JSON becomes a chart. Every card reads its `chart`
// hint the same way. Units: "percent" is a number already scaled to 0-100
// (gain_pct 18.7), "fraction" is a ratio shown x100 (share 0.3, change_share 0.25).
// G20: money is "sgd_minor", integer cents in a field named `<name>_minor`,
// and nothing else is money: a bare number is written as the plain number it is.
import { formatDate, isoParts, MONTHS } from "../dates";
import { MINUS } from "../money";
import { CENTS_UNIT, MINOR } from "./money";

export type Row = Record<string, unknown>;
export type ChartHint = {
  type?: string;
  x?: string;
  series?: string[];
  source?: string;
  unit?: string;
  units?: Record<string, string>;
  label?: string;
  horizontal?: boolean;
  unit_field?: string;
  /** versus only: the values of `order_field` (default `x`) in the order the rows are listed */
  order?: string[];
  order_field?: string;
  /** G13: per plotted series, the Transactions filters that list the rows its bar adds up; a series left out does not link */
  links?: BarLinks;
};
export type StatResult = {
  id: string;
  title: string;
  generated_at?: string;
  snapshot?: string;
  sources?: { database: string; rows_used?: number; filter?: string }[];
  params?: Record<string, unknown>;
  data?: Record<string, unknown>;
  findings?: string[];
  caveats?: string[];
  benchmarks?: {
    id?: string; name?: string; topic?: string; value: unknown; unit?: string; as_of?: string;
    source?: string; url?: string; source_url?: string;
  }[];
  chart?: ChartHint;
};

// G13: the plain sentence under a stat's title, and what a legend entry means
// (a tooltip on it), for a stat whose series names a reader cannot guess. Both
// are keyed by the stat's id; a series by `id/series`.
export const STAT_SUBTITLE: Record<string, string> = {
  "data-quality": "Rows with no category, and stand-in rows that fill a gap in a YouTrip export, by month.",
};
export const SERIES_NOTE: Record<string, string> = {
  "data-quality/uncategorised": "Rows no rule has given a category yet. Add a rule on the Rules page.",
  "data-quality/synthetic": "A stand-in row Kopifund adds when a YouTrip export leaves out a balance change, so the balance still adds up. Spending and income leave it out.",
};

/** What `normaliseUnit` calls a currency's text: a row in money, its figures under `<name>_minor`. */
export const MONEY = "money";

export const KNOWN = new Set(["bar", "stacked", "line", "scatter", "table", "versus"]);

export const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

const isRows = (v: unknown): v is Row[] =>
  Array.isArray(v) && v.length > 0 && v.every((r) => r && typeof r === "object" && !Array.isArray(r));

function pick(obj: unknown, path: string): unknown {
  return path.split(".").reduce<unknown>(
    (acc, key) => (acc && typeof acc === "object" ? (acc as Record<string, unknown>)[key] : undefined),
    obj,
  );
}

/** Rows or an empty list: what an explicit `chart.source` may name. */
const isRowList = (v: unknown): v is Row[] =>
  Array.isArray(v) && v.every((r) => r && typeof r === "object" && !Array.isArray(r));

/**
 * The rows a chart plots. An explicit `chart.source` that names a list is
 * authoritative, even when the list is empty: an empty source is an empty
 * chart, never a reason to plot some other list. Without a usable source, the
 * usual names, then the first non-empty list of objects.
 */
export function resolveRows(result: StatResult): Row[] | null {
  const data = result.data ?? {};
  const chart = result.chart ?? {};
  if (chart.source) {
    for (const t of [pick(result, chart.source), pick(data, chart.source)]) if (isRowList(t)) return t;
  }
  for (const t of [data.rows, data.months, data.items, data.weeks]) if (isRows(t)) return t;
  for (const v of Object.values(data)) if (isRows(v)) return v;
  return null;
}

/** The numeric fields to plot: the declared series that exist, else the first eight numeric fields. */
export function seriesFor(chart: ChartHint, rows: Row[], xKey: string): string[] {
  const present = (k: string) => rows.some((r) => isNum(r[k]));
  if (Array.isArray(chart.series)) {
    const kept = chart.series.filter(present);
    if (kept.length) return kept;
  }
  return Object.keys(rows[0] ?? {})
    .filter((k) => k !== xKey && present(k))
    .slice(0, 8);
}

/**
 * What a unit's text measures. G20: a currency ("SGD per household per month",
 * or the old chart unit "sgd") is MONEY, which says what a row measures but not
 * how its number is held, so `fmt` writes it as a plain number: a stat's money
 * is cents under `<name>_minor`, and only `sgd_minor` is written as money.
 * "months of expenses" is "months", "per cent of monthly income" "percent".
 * Money keeps its period, so "SGD per year" is "money/yr".
 */
export function normaliseUnit(u: unknown): string | null {
  if (typeof u !== "string" || !u) return null;
  const s = u.toLowerCase();
  // money keeps its period: "SGD per year" is not the unit of "SGD per month"
  if (/^(sgd|usd|eur|sek|myr|aud)\b/.test(s)) return s.includes("per year") ? `${MONEY}/yr` : MONEY;
  if (s === "fraction" || s.includes("share") || s.includes("ratio")) return "fraction";
  if (s.includes("percent") || s.includes("per cent") || s === "%") return "percent";
  if (s.startsWith("month")) return "months";
  if (s.startsWith("day")) return "days";
  if (s.startsWith("count") || s.startsWith("rows")) return "count";
  return s;
}

export type Plan =
  | { kind: "table"; rows: Row[] | null; note: string }
  | {
      // G13: one row per item, the item's figure against a typical one, each on its own scale
      kind: "versus";
      rows: Row[];
      allRows: Row[];
      xKey: string;
      owner: string;
      typical: string;
      unitField: string;
      note: string;
    }
  | {
      kind: "chart";
      type: "bar" | "stacked" | "line" | "scatter";
      rows: Row[];
      allRows: Row[];
      xKey: string;
      series: string[];
      unitOf: (s: string) => string;
      primary: string;
      secondary: string | null;
      horizontal: boolean;
      labelKey: string;
      note: string;
      links: BarLinks;
    };

/** Decide what to draw. Anything the page cannot draw falls back to a table, with the reason. */
export function plan(result: StatResult): Plan {
  const chart = result.chart ?? {};
  let rows = resolveRows(result);
  if (rows && rows.length === 0) return { kind: "table", rows: [], note: "Nothing to show yet: this stat has no rows." };
  const type = String(chart.type ?? "").toLowerCase();
  if (!type) return { kind: "table", rows, note: "This result carries no chart hint, showing it as a table." };
  if (!rows) return { kind: "table", rows: null, note: `No rows to plot for chart type "${chart.type}".` };
  if (type === "table") return { kind: "table", rows, note: "" };
  if (type === "versus") return versusPlan(chart, rows);
  if (!KNOWN.has(type))
    return { kind: "table", rows, note: `Chart type "${chart.type}" is not one this page draws yet, showing the numbers.` };

  const xKey = chart.x ?? Object.keys(rows[0])[0];
  // G20: a chart with no unit makes no claim to be money (a stat's money names `sgd_minor`), so its numbers are plain
  const primary = chart.unit ?? "number";
  const unitOf = (s: string) => chart.units?.[s] ?? primary;
  const allRows = rows;
  const unitField = chart.unit_field ?? "unit";
  let setAside: Row[] = [];
  if (rows.some((r) => typeof r[unitField] === "string")) {
    // a row in a currency belongs on a chart in cents: its money is under `<name>_minor`
    const chartUnit = primary === CENTS_UNIT ? MONEY : normaliseUnit(primary) ?? primary;
    const rowUnit = (r: Row) => normaliseUnit(r[unitField]) ?? chartUnit;
    if (new Set(rows.map(rowUnit)).size > 1) {
      const kept = rows.filter((r) => rowUnit(r) === chartUnit);
      if (kept.length) {
        setAside = rows.filter((r) => !kept.includes(r));
        rows = kept;
      }
    }
  }
  const series = seriesFor(chart, rows, xKey);
  if (!series.length) return { kind: "table", rows: allRows, note: "No numeric series in these rows, showing them as a table." };
  const secondary = series.map(unitOf).find((u) => u !== primary) ?? null;
  const horizontal = chart.horizontal ?? (type === "bar" && series.length === 1 && rows.length > 10);
  const note = setAside.length
    ? `${setAside.length} row${setAside.length > 1 ? "s are" : " is"} in another unit, so ${
        setAside.length > 1 ? "they are" : "it is"
      } in the table only.`
    : "";
  return {
    kind: "chart",
    type: type as "bar" | "stacked" | "line" | "scatter",
    rows,
    allRows,
    xKey,
    series,
    unitOf,
    primary,
    secondary,
    horizontal,
    labelKey: chart.label ?? "label",
    note,
    links: chart.links ?? {},
  };
}

/**
 * The rows of a versus list in the hint's order (`order` against
 * `order_field`, else `x`), anything the order leaves out after it in the
 * order it came. The first two series are the item's figure and the typical one.
 */
function versusPlan(chart: ChartHint, rows: Row[]): Plan {
  const xKey = chart.x ?? "label";
  const [owner = "owner", typical = "benchmark"] = chart.series ?? [];
  const field = chart.order_field ?? xKey;
  const order = chart.order ?? [];
  const rank = (r: Row) => {
    const i = order.indexOf(String(r[field]));
    return i < 0 ? order.length : i;
  };
  const sorted = rows.map((r, i) => ({ r, i })).sort((a, b) => rank(a.r) - rank(b.r) || a.i - b.i).map((x) => x.r);
  return { kind: "versus", rows: sorted, allRows: rows, xKey, owner, typical, unitField: chart.unit_field ?? "unit", note: "" };
}

const sgd = new Intl.NumberFormat("en-SG", { maximumFractionDigits: 0 });
const sgd2 = new Intl.NumberFormat("en-SG", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const plain = new Intl.NumberFormat("en-SG", { maximumFractionDigits: 1 });

/**
 * One value in its unit. G20: money is "sgd_minor", integer cents, and only it
 * is written as money. The default is a plain number: a value that names no
 * unit makes no claim to be money, and a stat's money always names one.
 */
export function fmt(v: unknown, unit = "number", full = false): string {
  if (!isNum(v)) return v == null ? "" : String(v);
  if (unit === CENTS_UNIT) {
    const dollars = v / 100;
    const s = (full ? sgd2 : sgd).format(Math.abs(dollars));
    return `${dollars < 0 ? MINUS : ""}S$${s}`;
  }
  const u = normaliseUnit(unit) ?? unit;
  if (u === "percent") return `${plain.format(v)}%`;
  if (u === "fraction") return `${plain.format(v * 100)}%`;
  if (u === "months") return `${plain.format(v)} mo`;
  if (u === "days") return `${plain.format(v)} d`;
  return plain.format(v);
}

/**
 * The percent unit a column name implies, or null. Scaled names (gain_pct,
 * share_pct) are "percent"; ratio names (share, need_share, change_share) are
 * "fraction". The name decides, never the size of the value, so 0.45 and 45
 * both read right in their own column.
 */
export function percentUnitFor(column: string): "percent" | "fraction" | null {
  const k = column.toLowerCase();
  if (/(^|_)(pct|percent)(_|$)/.test(k)) return "percent";
  if (/share/.test(k)) return "fraction";
  return null;
}

/**
 * The unit a bare number takes from its chart: the chart's own when it is not
 * money (a chart in months or days), else a plain number. G20: a stat's money
 * is under `<name>_minor`, so a number under any other name is no amount.
 */
export function bareUnit(unit?: string): string {
  if (!unit || unit === CENTS_UNIT || unit === "count" || normaliseUnit(unit) === MONEY) return "number";
  return unit;
}

/**
 * A column's own unit, else what its name says (money in cents, a percent, a
 * count), else the chart's unit when it is not money, else a plain number.
 * The rows table and the chart's hover card read a field the same way.
 */
export function columnUnit(column: string, units?: Record<string, string>, unit?: string): string {
  return units?.[column]
    // G20: a field named `<name>_minor` is money in cents, whatever else its name says
    ?? (column.endsWith(MINOR) ? CENTS_UNIT : null)
    ?? percentUnitFor(column)
    // G18: a multiple (the one-offs scatter's x, "times the category's median") is a plain number
    ?? (/(count|n_|rows|days|weeks|months$|multiple)/.test(column) ? "number" : bareUnit(unit));
}

// Series keys that read badly when only the underscores are replaced, and a
// column whose name is not what the app calls it (category_group is a Bucket).
const LABELS: Record<string, string> = {
  oa: "OA", sa: "SA", ma: "MA", ra: "RA",
  contributed_sgd: "Put in", value_sgd: "Value",
  runway_months: "Runway", monthly_cost: "Monthly cost", per_month: "Per month",
  category_group: "Bucket",
};

export function label(key: string): string {
  // G20: a field in cents is named for its figure: "cash_minor" reads "Cash"
  const k = key.endsWith(MINOR) && key.length > MINOR.length ? key.slice(0, -MINOR.length) : key;
  if (LABELS[k]) return LABELS[k];
  const s = k.replace(/_sgd$/, "").replace(/_/g, " ");
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/**
 * An axis tick, the one compact date form: "2025-10" reads "Oct 25",
 * "2024-07-01" reads "1 Jul 24". `long` is for a tooltip or a heading and gives
 * the app's own format (dates.ts): "Oct 2025", "1 Jul 2024".
 */
export function tickLabel(v: unknown, long = false): string {
  if (typeof v !== "string") return v == null ? "" : String(v);
  const p = isoParts(v);
  if (!p) return v;
  if (long) return formatDate(v);
  const year = String(p.y).slice(2);
  return p.d != null ? `${p.d} ${MONTHS[p.m - 1]} ${year}` : `${MONTHS[p.m - 1]} ${year}`;
}

/** What a stat says about where each plotted series' bars go: Transactions filters, with {x} for the bar's x field. */
export type BarLinks = Record<string, Record<string, string>>;

/**
 * Where a click on one series' bar goes: the transactions its figure adds up, or null when the
 * series has no link (an average, an estimate or a balance is not a list of rows). The stat writes
 * the filters (analytics/stats/*.py, `LINKS`); the page only fills in the bar's own x value.
 */
export function barLink(links: BarLinks | undefined, series: string, row: Row | undefined, xKey: string): string | null {
  const spec = links?.[series];
  const x = row?.[xKey];
  if (!spec || typeof x !== "string" || !x) return null;
  const query = new URLSearchParams();
  // {x} is the bar's own x; {name} is another field of the same row (a week's `end`)
  const fill = (value: string) => value.replace(/\{(\w+)\}/g, (whole, name: string) => {
    const field = name === "x" ? x : row?.[name];
    return typeof field === "string" ? field : whole;
  });
  for (const [key, value] of Object.entries(spec)) query.set(key, fill(value));
  return `/transactions?${query}`;
}
