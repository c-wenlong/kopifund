// G13: the one headline tile. Home, Spending, Recurring and Net worth all show
// a label, a figure, a note and sometimes a small picture under them; they
// share this so the gap, the type sizes and the colours cannot drift apart.

export type Tone = "neutral" | "good" | "warn" | "danger";

const NOTE: Record<Tone, string> = { neutral: "text-ink-2", good: "text-good", warn: "text-warn", danger: "text-danger" };
// only a figure on the wrong side of something is coloured; a good one stays ink
const FIGURE: Record<Tone, string> = { neutral: "", good: "", warn: "text-warn", danger: "text-danger" };

/**
 * `tone` colours the note, and the figure itself too when it is `warn` or
 * `danger`, so a bad number does not read as a healthy one. `noteTone` colours
 * the note alone, for a note that is only a comparison. `visual` is a small
 * picture under the note: a sparkline, a track, a progress bar.
 */
export function Tile({ label, value, note, tone = "neutral", noteTone, visual }: {
  label: string; value: string; note?: string; tone?: Tone; noteTone?: Tone; visual?: React.ReactNode;
}) {
  return (
    <div className="flex min-w-0 flex-col rounded-lg bg-tile px-3 py-3 sm:px-4">
      <div className="text-[13px] text-ink-2">{label}</div>
      <div className={`num mt-0.5 text-[20px] font-semibold tracking-tight sm:text-[22px] ${FIGURE[tone]}`}>{value}</div>
      {note && <div className={`num mt-0.5 text-[12px] ${NOTE[noteTone ?? tone]}`}>{note}</div>}
      {visual && <div className="mt-auto pt-2">{visual}</div>}
    </div>
  );
}

const COLS = { 2: "lg:grid-cols-2", 3: "lg:grid-cols-3", 4: "lg:grid-cols-4" } as const;

/** Tiles in a row: two to a line on a phone, `cols` from a wide screen, the same gap everywhere. */
export function TileGrid({ cols, label, children }: { cols: keyof typeof COLS; label?: string; children: React.ReactNode }) {
  return (
    <div role={label ? "group" : undefined} aria-label={label} className={`grid grid-cols-2 gap-3 ${COLS[cols]}`}>
      {children}
    </div>
  );
}

/** The month-by-month trace under a tile's figure: a thin line, the latest point marked, no axes. */
export function Sparkline({ values, label }: { values: number[]; label: string }) {
  if (values.length < 2) return null;
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const span = hi - lo || 1;
  const W = 100;
  const H = 28;
  const PAD = 4;                          // room for the line's width and the end dot
  const y = (v: number) => (hi === lo ? H / 2 : H - PAD - ((v - lo) / span) * (H - 2 * PAD));
  const pts = values.map((v, i) => `${((i / (values.length - 1)) * W).toFixed(2)},${y(v).toFixed(2)}`);
  return (
    <div role="img" aria-label={label} className="relative h-7 w-full text-ink-2">
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="absolute inset-0 size-full overflow-visible" aria-hidden>
        <title>{label}</title>
        <polyline points={pts.join(" ")} fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinejoin="round"
          strokeLinecap="round" vectorEffect="non-scaling-stroke" />
      </svg>
      {/* an html dot, so the stretched svg cannot squash it into an ellipse */}
      <span aria-hidden className="absolute size-2 -translate-x-1/2 -translate-y-1/2 rounded-full bg-ink ring-2 ring-tile"
        style={{ left: "100%", top: `${(y(values[values.length - 1]) / H) * 100}%` }} />
    </div>
  );
}

/** A months-of-cash figure on a scale, the reference range shaded and the figure marked. */
export function RangeTrack({ value, low, high, tone, label }: {
  value: number; low: number; high: number; tone: Tone; label: string;
}) {
  const max = Math.max(high * 1.6, value * 1.15, 1);
  const at = (v: number) => `${Math.max(0, Math.min(1, v / max)) * 100}%`;
  const mark = tone === "danger" ? "bg-danger" : tone === "warn" ? "bg-warn" : "bg-ink";
  const tick = "num absolute top-full mt-1 -translate-x-1/2 text-[11px] leading-none text-ink-3";
  return (
    <div role="img" aria-label={label} className="pb-4">
      <div className="relative h-2 rounded-full bg-hover">
        <span className="absolute inset-y-0 bg-good opacity-30" style={{ left: at(low), width: `calc(${at(high)} - ${at(low)})` }} aria-hidden />
        <span aria-hidden className={`absolute top-1/2 size-3 -translate-x-1/2 -translate-y-1/2 rounded-full ring-2 ring-tile ${mark}`}
          style={{ left: at(value) }} />
        <span aria-hidden className={tick} style={{ left: at(low) }}>{low}</span>
        <span aria-hidden className={tick} style={{ left: at(high) }}>{high}</span>
      </div>
    </div>
  );
}

/** Paid of committed: a bar filled to the share, for a tile whose figure is part of a whole. */
export function Progress({ value, of, label }: { value: number; of: number; label: string }) {
  if (of <= 0) return null;
  const share = Math.max(0, Math.min(1, value / of));
  return (
    <div role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(share * 100)}
      className="h-2 overflow-hidden rounded-full bg-hover">
      <div className="h-full rounded-full bg-ink-2" style={{ width: `${share * 100}%` }} />
    </div>
  );
}
