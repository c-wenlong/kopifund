// G13: what the app's charts share, so they read as one family: axis and grid
// props, the hover card, and a legend whose entries hide and show a series.
// Recharts only finds its axes, grid and tooltip among a chart's direct
// children, so these are props to spread, not wrapper components.
import { useRef, useState } from "react";
import type { LucideIcon } from "lucide-react";
import { axisSgd, formatSgd, type Cents } from "../money";
import { tickLabel } from "../stats/hint";

export const AXIS = { fontSize: 11, fill: "var(--ink-3)" };

/**
 * G18: the colours a chart draws its series in, in order (--c1 to --c12 in index.css, light and dark).
 * A chart indexes into this by its series' place and takes no other colour, so two series of one chart
 * never share one for up to twelve of them. The folded "others" is `REST_COLOUR`, a neutral of its own.
 */
export const SERIES_COLOURS: readonly string[] = [
  "var(--c1)", "var(--c2)", "var(--c3)", "var(--c4)", "var(--c5)", "var(--c6)",
  "var(--c7)", "var(--c8)", "var(--c9)", "var(--c10)", "var(--c11)", "var(--c12)",
];
export const REST_COLOUR = "var(--rest)";
/** The colour of the series at `index`; past the twelfth it starts over, which no chart of the app reaches. */
export const seriesColour = (index: number) => SERIES_COLOURS[index % SERIES_COLOURS.length];
/** The main chart on a page, and the smaller ones that sit beneath it. */
export const H_MAIN = 260;
export const H_SMALL = 220;
export const Y_WIDTH = 56;

export const GRID = { stroke: "var(--grid)", vertical: false } as const;
export const X_AXIS = {
  tick: AXIS, tickFormatter: (v: unknown) => tickLabel(v), axisLine: false, tickLine: false, minTickGap: 24, tickMargin: 6,
} as const;
export const Y_AXIS = { width: Y_WIDTH, tick: AXIS, tickFormatter: axisSgd, axisLine: false, tickLine: false } as const;

// G18: a chart whose bars lie down has its names down the left and its figures along the bottom.
/** The grid of a chart whose bars lie down: a line across at each figure on the bottom axis, none between the bars. */
export const BAR_GRID = { stroke: "var(--grid)", horizontal: false } as const;
/** The name column of such a chart: every name shown, none wrapped. Give it a `width` and `tick={nameTick(...)}`. */
export const Y_NAMES = { tick: AXIS, axisLine: false, tickLine: false, interval: 0 } as const;
/** `name` cut to `max` characters, ending in an ellipsis when it was longer. */
export const cutName = (name: string, max: number) => (name.length > max ? `${name.slice(0, max - 1)}…` : name);
/**
 * A tick for a name column: one line per name, cut to `max` characters when given. Recharts' own tick
 * wraps a long name onto the next bar, so a long name is cut here instead.
 */
export const nameTick = (max?: number) => function NameTick({ x, y, payload }: { x: number; y: number; payload: { value: unknown } }) {
  const text = tickLabel(payload.value);
  return (
    <text x={x} y={y} dy={4} textAnchor="end" fontSize={AXIS.fontSize} fill={AXIS.fill} className="recharts-cartesian-axis-tick-value">
      {max ? cutName(text, max) : text}
    </text>
  );
};
/** A dot on a line: filled with the series colour, ringed in the card's colour so it stays legible over a crossing line. */
export const activeDot = (colour: string) => ({ r: 4, fill: colour, stroke: "var(--panel)", strokeWidth: 2 });

/**
 * G13: a click on a line's dot, for a line chart whose points open the rows behind them. Recharts
 * hands a dot's handler nothing about the point, but the chart's own click knows the month under
 * the pointer: the dot says which line was hit and the chart's handler reads it with `take`.
 * A click that misses every dot takes nothing, so it opens nothing.
 */
export function useDotClick() {
  const hit = useRef<string | null>(null);
  return {
    /** The active dot of line `key`; with `on` false it is the plain dot, with no pointer and no click. */
    dot: (key: string, colour: string, on = true) =>
      on ? { ...activeDot(colour), cursor: "pointer", onClick: () => { hit.current = key; } } : activeDot(colour),
    take: () => { const k = hit.current; hit.current = null; return k; },
  };
}

/**
 * A key to a drawn series: a square for a bar or area, a short line for a line (dashed too), a round dot for a point,
 * and a ring (dashed too) for an outline drawn round a point.
 */
export function Swatch({ colour, dashed = false, line = false, dot = false, ring = false }: {
  colour: string; dashed?: boolean; line?: boolean; dot?: boolean; ring?: boolean;
}) {
  if (ring) {
    return <span className={`size-3 shrink-0 rounded-full border-[1.5px] ${dashed ? "border-dashed" : ""}`} style={{ borderColor: colour }} aria-hidden />;
  }
  if (dot) return <span className="size-2 shrink-0 rounded-full" style={{ background: colour }} aria-hidden />;
  return dashed || line
    ? <span className={`w-3 shrink-0 border-t-2 ${dashed ? "border-dashed" : ""}`} style={{ borderColor: colour }} aria-hidden />
    : <span className="size-2 shrink-0 rounded-sm" style={{ background: colour }} aria-hidden />;
}

/** G18: the icon of a series that has one (a bucket's), beside its swatch, in the words' colour: the swatch carries the colour. */
function SeriesIcon({ icon: Icon }: { icon: LucideIcon }) {
  return <Icon size={12} aria-hidden focusable={false} className="-mx-0.5 shrink-0" data-bucket-icon="" />;
}

/**
 * G18: the one box every chart's hover card sits in, so they share a width: at least 13rem, at most
 * 22rem and never wider than a phone screen less its margins. `total` is a sum in cents, or already
 * written when the chart's unit is not money; `hint` is the line under a rule ("Click a bar ...").
 */
export function TipBox({ heading, total, hint, children }: {
  heading: React.ReactNode; total?: Cents | string; hint?: string; children?: React.ReactNode;
}) {
  return (
    <div className="min-w-52 max-w-[min(22rem,calc(100vw-2rem))] rounded-lg border border-line bg-tip px-3 py-2 text-[12px] text-ink shadow-lg">
      <div className="mb-1 flex justify-between gap-4 font-semibold">
        <span className="min-w-0 break-words">{heading}</span>
        {total != null && <span className="num shrink-0">{typeof total === "string" ? total : formatSgd(total)}</span>}
      </div>
      {children}
      {hint && <div className="mt-1.5 border-t border-line pt-1 text-[11px] text-ink-3">{hint}</div>}
    </div>
  );
}

/**
 * One series in a hover card. A series at zero is left out (a row of S$0 says nothing); `keepZero` shows it anyway.
 * `value` is cents, or text already written (a count, a date range). Without a `colour` the row keeps a gap
 * where a swatch would be, so it lines up with the coloured rows beside it; `bare` drops the gap, for a card
 * of plain labels and figures such as a trip's.
 */
export function TipRow({ colour, dashed, line, dot, icon, name, note, value, keepZero = false, bare = false }: {
  colour?: string; dashed?: boolean; line?: boolean; dot?: boolean; icon?: LucideIcon; name: string; note?: string; value: Cents | string;
  keepZero?: boolean; bare?: boolean;
}) {
  if (value === 0 && !keepZero) return null;
  return (
    <div className="flex items-center gap-2 text-ink-2">
      {colour ? <Swatch colour={colour} dashed={dashed} line={line} dot={dot} /> : !bare && <span className="w-2" aria-hidden />}
      {icon && <SeriesIcon icon={icon} />}
      <span className="min-w-0 break-words">{name}</span>
      {note && <span className="text-ink-3">{note}</span>}
      <span className="num ml-auto shrink-0 pl-3 text-ink">{typeof value === "string" ? value : formatSgd(value)}</span>
    </div>
  );
}

/**
 * Which of the series `all` the reader has switched off. The last one showing
 * cannot be hidden, and a series that is no longer in `all` (an edit changed
 * what is drawn) is forgotten, so what was hidden can never leave nothing showing.
 */
export function useHidden(all: readonly string[]) {
  const [raw, setRaw] = useState<ReadonlySet<string>>(new Set());
  const live = all.filter((k) => raw.has(k));
  const hidden: ReadonlySet<string> = new Set(live.length < all.length ? live : []);
  // drop what no longer applies now, so it cannot come back when a series does
  if (raw.size !== hidden.size) setRaw(hidden);
  const toggle = (key: string) => setRaw(() => {
    const n = new Set(hidden);
    if (n.has(key)) n.delete(key);
    else if (all.filter((k) => !n.has(k)).length > 1) n.add(key);
    return n;
  });
  return { hidden, toggle };
}

/** No series hidden: what a total view uses, whatever the stacked and line views have switched off. */
export const NONE_HIDDEN: ReadonlySet<string> = new Set();

/** `note` says what the entry means: it rides along in the entry's tooltip; `icon` (G18: a bucket's) sits before the label */
export type LegendItem = { key: string; label: string; colour: string; dashed?: boolean; line?: boolean; note?: string; icon?: LucideIcon };

/** Above the plot, left aligned. Each entry is a button that hides or shows its series; `extra` entries are plain. */
export function SeriesLegend({ items, hidden, onToggle, extra }: {
  items: LegendItem[]; hidden: ReadonlySet<string>; onToggle: (key: string) => void; extra?: React.ReactNode;
}) {
  const shown = items.filter((i) => !hidden.has(i.key)).length;
  return (
    <div className="mb-2 flex flex-wrap items-center gap-x-1 gap-y-0.5 text-[12px] text-ink-2">
      {items.map((i) => {
        const off = hidden.has(i.key);
        return (
          <button key={i.key} type="button" aria-pressed={!off} disabled={!off && shown <= 1} onClick={() => onToggle(i.key)}
            title={`${off ? "Show" : "Hide"} ${i.label}${i.note ? `. ${i.note}` : ""}`}
            className={`flex items-center gap-1.5 rounded px-1.5 py-0.5 hover:bg-hover disabled:cursor-default disabled:hover:bg-transparent ${off ? "text-ink-3 line-through" : ""}`}>
            <span className={`flex ${off ? "opacity-30" : ""}`}><Swatch colour={i.colour} dashed={i.dashed} line={i.line} /></span>
            {i.icon && <SeriesIcon icon={i.icon} />}
            {i.label}
          </button>
        );
      })}
      {extra}
    </div>
  );
}

/** A legend entry that is only a key to something drawn, not a series to hide. */
export function KeyItem({ children }: { children: React.ReactNode }) {
  return <span className="flex items-center gap-1.5 px-1.5 py-0.5">{children}</span>;
}
