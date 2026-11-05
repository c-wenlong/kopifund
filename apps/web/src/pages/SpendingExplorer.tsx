import { useMemo, useState } from "react";
import { useCopilotContext } from "../copilot/pageFilters";
import { ChevronRight, type LucideIcon } from "lucide-react";
import { Bar, CartesianGrid, ComposedChart, Line, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useApi, type BucketStyle } from "../api";
import { formatSgd, type Cents } from "../money";
import { navigate, onLinkClick } from "../router";
import { buttonClass } from "../components/Button";
import { Card } from "../components/Card";
import { EmptyState } from "../components/EmptyState";
import { Segmented } from "../components/Segmented";
import { Tile, TileGrid } from "../components/Tile";
import {
  GRID, H_MAIN, KeyItem, REST_COLOUR, seriesColour, SeriesLegend, Swatch, TipBox, TipRow, useDotClick, useHidden, X_AXIS, Y_AXIS,
} from "../components/chartKit";
import { CollapsibleRows, COLLAPSED_ROWS } from "../components/CollapsibleRows";
import { bucketLook, BucketMark } from "../components/BucketMark";
import { CardSkeleton, Problem } from "../components/States";
import { useChartView, ViewSwitch, viewsOf, type ChartView } from "../components/ViewSwitch";
import { formatDate, formatDay, shiftMonth } from "../dates";
import { useUrlParams } from "../useUrlParams";

// G13: `link` is the Transactions filters that list the rows behind a figure (finance/spending.py): the
// answer's own carries the range and scope, and each series, category and payee adds what narrows it
export type Spending = {
  by: "bucket" | "category" | "payee";
  scope: "sg" | "all";
  bucket_order: string[];
  /** G17: each spend bucket's icon and colour; a colour the owner set wins over the one by order */
  bucket_style: Record<string, BucketStyle>;
  available: { from: string; to: string } | null;
  months: string[];
  series: { name: string; total: Cents; per_month: Cents; values: Cents[]; folded?: number; link: Record<string, string> }[];
  buckets: { name: string; total: Cents; per_month: Cents; categories: { name: string; total: Cents; per_month: Cents; link: Record<string, string> }[] }[];
  payees: { name: string; total: Cents; count: number; link: Record<string, string> }[];
  biggest: { key: string; date: string; payee: string; category: string; amount: Cents }[];
  link: Record<string, string>;
  totals: { spend: Cents; per_month: Cents; overseas: Cents };
};

/** The Transactions address that lists a figure's rows: the range's own filters, what narrows it to a series or a payee, a month. */
export const spendLink = (...parts: Record<string, string>[]) => `/transactions?${new URLSearchParams(Object.assign({}, ...parts))}`;

/** A change with its sign: "+" for a rise, a true minus (U+2212, a prefix character, so it never parts from the amount) for a fall. */
export const signed = (c: Cents) => `${c >= 0 ? "+" : "\u2212"}${formatSgd(Math.abs(c))}`;

const RANGES = [{ text: "3M", value: 3 }, { text: "6M", value: 6 }, { text: "12M", value: 12 }, { text: "All", value: 0 }] as const;
const SCOPES = [{ text: "Singapore only", value: "sg" }, { text: "Include overseas", value: "all" }] as const;
const SPLITS = [{ text: "By bucket", value: "bucket" }, { text: "By category", value: "category" }, { text: "By payee", value: "payee" }] as const;

/**
 * A bucket keeps its colour whatever the range ranks first; other splits colour by rank. G18: both index
 * into the chart kit's twelve series colours, so nine buckets get nine colours and none wraps onto
 * another's; the folded "others" is the kit's neutral. G17, G18: a bucket draws in the colour its
 * `bucket_style` says it is shown in (bucketLook, the same colour Categories shows it in).
 */
export function colourFor(
  name: string, rank: number, data: Pick<Spending, "by" | "bucket_order"> & Partial<Pick<Spending, "bucket_style">>,
  folded = false,
): string {
  if (folded) return REST_COLOUR;
  if (data.by === "bucket") {
    const style = data.bucket_style?.[name];
    const i = data.bucket_order.indexOf(name);
    if (style || i >= 0) return bucketLook(style, i >= 0 ? i : rank).colour;
  }
  return seriesColour(rank);
}

/** G18: a bucket's icon, for a chart split by bucket; other splits and the folded rest have none. */
export function iconFor(
  name: string, data: Pick<Spending, "by"> & Partial<Pick<Spending, "bucket_style">>, folded = false,
): LucideIcon | undefined {
  if (folded || data.by !== "bucket") return undefined;
  const style = data.bucket_style?.[name];
  return style ? bucketLook(style, 0).icon : undefined;
}

type Scope = Spending["scope"];
type Split = Spending["by"];

/** An answer that is what the controls on screen ask for: same split, scope and months. */
const answers = (d: Spending | null | undefined, by: Split, scope: Scope, from: string, to: string) =>
  d && d.by === by && d.scope === scope && d.months[0] === from && d.months.at(-1) === to ? d : null;

/**
 * Where the money went: a range, Singapore only or with overseas, split by
 * bucket, category or payee. Every bar and row opens the transactions behind it.
 */
export function SpendingExplorer() {
  // the controls live in the address (`range`, `scope`, `by`, the defaults left out), so
  // Back from the transactions a bar opened lands on the view as it was left
  const [params, patch] = useUrlParams();
  const rangeText = params.get("range");
  const range = rangeText === "all" ? 0 : RANGES.find((r) => r.value && String(r.value) === rangeText)?.value ?? 12;
  const scope: Scope = SCOPES.find((o) => o.value === params.get("scope"))?.value ?? "sg";
  const by: Split = SPLITS.find((o) => o.value === params.get("by"))?.value ?? "bucket";
  const setRange = (r: number) => patch({ range: r === 12 ? null : r ? String(r) : "all" });
  const setScope = (v: Scope) => patch({ scope: v === "sg" ? null : v });
  const setBy = (v: Split) => patch({ by: v === "bucket" ? null : v });
  const [view, setView] = useChartView<ChartView>("spending-explorer", SPEND_KEYS, "stacked");
  // the bare request answers the default view (Singapore only, by bucket, the last 12 months
  // the record holds) and says which months the record holds
  const probe = useApi<Spending>("/api/spending");
  const last = probe.data?.available?.to;
  const first = probe.data?.available?.from;
  // never before the record's first month: a range longer than the record would add empty
  // months and divide "a month" by them, unlike Home and the projection's hint (spending.py)
  const back = last && range ? shiftMonth(last, -(range - 1)) : null;
  const from = last && first ? (back && back > first ? back : first) : undefined;
  const fromProbe = last && from ? answers(probe.data, by, scope, from, last) : null;
  const url = useMemo(() => (last && from && !fromProbe ? `/api/spending?${new URLSearchParams({ scope, by, to: last, from })}` : null),
    [last, from, scope, by, fromProbe]);
  const { data, error, loading } = useApi<Spending>(url);
  // G22: the copilot asks about what is on screen, in its spending tool's own names
  useCopilotContext({ from, to: last, scope, by });

  if (probe.loading) return <CardSkeleton height={300} what="Loading your spending" />;
  if (probe.error) return <Problem message={probe.error} retry />;
  if (!probe.data?.available || !last || !from) {
    return (
      <EmptyState title="No spending in the record yet"
        action={<a href="/upload" onClick={onLinkClick("/upload")} className={buttonClass("secondary")}>Load your statements</a>}>
        Spending comes with your first statements.
      </EmptyState>
    );
  }
  // only figures that answer the controls on screen: a pending or failed
  // request must not leave the last answer showing under new labels
  const d = fromProbe ?? answers(data, by, scope, from, last);

  return (
    <section aria-label="Spending explorer" className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <Segmented label="Range" value={range} options={RANGES} onChange={setRange} />
        <Segmented label="Where" value={scope} options={SCOPES} onChange={setScope} />
        <Segmented label="Split by" value={by} options={SPLITS} onChange={setBy} />
      </div>
      {!d ? (error && !loading ? <Problem message={error} retry /> : <CardSkeleton height={300} what="Loading your spending" />) : (
      <>

      <TileGrid cols={3}>
        <Tile label="Spent" value={formatSgd(d.totals.spend)} note={`${d.months.length} months`} />
        <Tile label="A month" value={formatSgd(d.totals.per_month)} note={scope === "sg" ? "Singapore only" : "including overseas"} />
        <Tile label="Overseas" value={formatSgd(d.totals.overseas)}
          note={scope === "sg" ? "left out of these figures" : "included in these figures"} />
      </TileGrid>

      <Card title="Spending by month" actions={<ViewSwitch label="Chart view" value={view} options={SPEND_VIEWS} onChange={setView} />}>
        <SpendChart d={d} view={view} open={navigate} />
      </Card>

      <div className="grid items-start gap-3 lg:grid-cols-[3fr_2fr]">
        <WhereItWent d={d} />
        <div className="flex flex-col gap-3">
          <List title="Biggest payments" rows={d.biggest.map((b) => ({
            key: b.key, left: b.payee, sub: `${formatDay(b.date)} · ${b.category}`,
            right: formatSgd(b.amount), to: `/transactions?${new URLSearchParams({ key: b.key })}`,
          }))} />
          <List title="Top payees" rows={d.payees.map((p) => ({
            key: p.name, left: p.name, sub: `${p.count} ${p.count === 1 ? "payment" : "payments"}`,
            right: formatSgd(p.total), to: spendLink(d.link, p.link),
          }))} />
        </div>
      </div>
      </>
      )}
    </section>
  );
}

// Series are keyed s0, s1, ... and named only for display: a category called
// "month", or one called "Everything else" beside the folded rest, would
// otherwise overwrite another series or the x axis.
export type Stack = Pick<Spending, "months" | "series">;

/** One chart row per month: `__month`, then each series' value under s0, s1, ... */
export function stackRows(d: Stack): Record<string, string | number>[] {
  return d.months.map((m, i) => {
    const row: Record<string, string | number> = { __month: m };
    d.series.forEach((s, j) => { row[`s${j}`] = s.values[i]; });
    return row;
  });
}

/** The display name behind a series key from stackRows. */
export function seriesName(d: Stack) {
  const names = d.series.map((s) => s.name);
  return (key: unknown) => names[Number(String(key).slice(1))] ?? String(key);
}

export const SPEND_VIEWS = viewsOf("stacked", "lines", "total");
export const SPEND_KEYS = ["stacked", "lines", "total"] as const;

/**
 * The hover for a month: the month and its total, then each series with its
 * colour and value. Given `series`, it reads them off the chart's row (so a
 * line chart lists its series largest first); without, it lists what the chart
 * handed over, top of the stack first. A series at zero is not listed. With `average`, how far
 * the month sits from it (G13: Home's spending chart).
 */
type HoverItem = { value?: unknown; dataKey?: unknown; color?: string; icon?: LucideIcon; payload?: Record<string, unknown> };
export function StackTooltip({ active, payload, label, nameOf, average, series, bySize = false, hint }: {
  active?: boolean; payload?: readonly HoverItem[]; label?: unknown; nameOf: (key: unknown) => string; average?: Cents;
  series?: { key: string; colour: string; icon?: LucideIcon }[]; bySize?: boolean;
  hint?: string;   // what a click does, when it does something
}) {
  if (!active || !payload?.length) return null;
  const row = payload[0].payload;
  const found: HoverItem[] = series && row
    ? series.map((s) => ({ dataKey: s.key, value: row[s.key], color: s.colour, icon: s.icon }))
    : payload.filter((p) => typeof p.value === "number" && p.value !== 0).reverse();
  // a series with nothing in this month is left out: a row of S$0 says nothing
  const items = found.filter((p) => typeof p.value === "number" && p.value !== 0);
  if (bySize) items.sort((a, b) => (b.value as number) - (a.value as number));
  const total = items.reduce((a, p) => a + (p.value as number), 0);
  const diff = average == null ? null : total - average;
  return (
    <TipBox heading={formatDate(label)} total={total} hint={hint}>
      {items.map((p) => (
        <TipRow key={String(p.dataKey)} colour={p.color ?? "var(--ink-3)"} line={bySize} icon={p.icon} name={nameOf(p.dataKey)} value={p.value as number} />
      ))}
      {diff != null && (
        <div className={`num mt-1 ${diff > 0 ? "text-warn" : "text-good"}`}>
          {signed(diff)} on the average
        </div>
      )}
    </TipBox>
  );
}

// G13: `link` (the range's own filters) and each series' `link` say which rows a bar adds up; without
// them a bar does not open anything
type SpendProps = {
  d: Stack & Pick<Spending, "by" | "bucket_order" | "link"> & Partial<Pick<Spending, "bucket_style">>;
  view: ChartView; open: (to: string) => void; average?: Cents;
};

/**
 * Spending by month in one of three views of the same series: stacked bars,
 * one line per series on its own scale, or one bar of the total. A bar opens
 * the transactions it adds up: a stacked bar its series' rows in that month,
 * the total bar the month's, and a line's dot (the line chart's way to pick a
 * series) its series' rows.
 * Legend entries hide a series; the hidden set starts afresh when the series do.
 */
export function SpendChart(props: SpendProps) {
  return <SpendChartBody key={props.d.series.map((s) => s.name).join("|")} {...props} />;
}

function SpendChartBody({ d, view, open, average }: SpendProps) {
  const nameOf = seriesName(d);
  const keys = d.series.map((_, i) => `s${i}`);
  const { hidden, toggle } = useHidden(keys);
  const colours = d.series.map((s, i) => colourFor(s.name, i, d, !!s.folded));
  const colourOf = (k: string) => colours[keys.indexOf(k)];
  const icons = d.series.map((s) => iconFor(s.name, d, !!s.folded));
  const iconOf = (k: string) => icons[keys.indexOf(k)];
  const shown = keys.filter((k) => !hidden.has(k));
  const total = view === "total";
  const lines = view === "lines";
  // the total bar is all of spending, whatever the stack has switched off
  const drawn = total ? keys : shown;
  const rows = stackRows(d).map((r) => ({ ...r, __total: drawn.reduce((a, k) => a + (Number(r[k]) || 0), 0) }));
  const series = [...drawn].reverse().map((k) => ({ key: k, colour: colourOf(k), icon: iconOf(k) }));
  // G13: where a bar goes is what it adds up: the range's filters, its series' (none for the total bar,
  // which is all of the month), and the month; a bar with nothing to say about its rows opens nothing
  const dots = useDotClick();
  const linkOf = (k?: string) => (k === undefined ? {} : d.series[keys.indexOf(k)]?.link);
  const canOpen = (k?: string) => !!d.link && !!linkOf(k);
  const opens = (m: unknown, k?: string) => {
    const extra = linkOf(k);
    if (typeof m === "string" && m && d.link && extra) open(spendLink(d.link, extra, { month: m }));
  };
  const onBar = (k?: string) => (e: { payload?: { __month?: string } }) => opens(e?.payload?.__month, k);
  const hint = total ? "Click to see the month's transactions" : lines ? "Click a dot to see its transactions" : "Click a bar to see its transactions";
  const anyLink = drawn.some((k) => canOpen(total ? undefined : k));
  return (
    <div role="img" aria-label={`Spending per month${average != null ? `, average ${formatSgd(average)}` : ""}, ${total ? "one bar for the total" : `${lines ? "a line" : "a bar"} for each of ${d.series.length} groups`}`}>
      {total
        ? <SeriesLegend items={[]} hidden={hidden} onToggle={() => {}} extra={<KeyItem><Swatch colour="var(--ink-2)" />All spending</KeyItem>} />
        : <SeriesLegend items={d.series.map((s, i) => ({ key: keys[i], label: s.name, colour: colours[i], icon: icons[i], line: lines }))}
          hidden={hidden} onToggle={toggle} />}
      <ResponsiveContainer width="100%" height={H_MAIN}>
        <ComposedChart data={rows} margin={{ top: 6, right: 4, bottom: 0, left: 0 }}
          onClick={lines ? (s: { activeLabel?: string | number }) => {
            const k = dots.take();
            if (k) opens(s?.activeLabel, k);
          } : undefined}>
          <CartesianGrid {...GRID} />
          <XAxis dataKey="__month" {...X_AXIS} minTickGap={8} />
          <YAxis {...Y_AXIS} />
          <Tooltip cursor={lines ? { stroke: "var(--ink-3)", strokeWidth: 1 } : { fill: "var(--hover)" }}
            content={(p) => <StackTooltip {...p} nameOf={nameOf} average={lines ? undefined : average} series={series} bySize={lines}
              hint={anyLink ? hint : undefined} />} />
          {lines && <ReferenceLine y={0} stroke="var(--ink-3)" strokeOpacity={0.5} />}
          {view === "stacked" && shown.map((k) => (
            <Bar key={k} dataKey={k} name={k} stackId="a" fill={colourOf(k)} stroke="var(--panel)" strokeWidth={1}
              isAnimationActive={false} maxBarSize={32} cursor={canOpen(k) ? "pointer" : undefined} onClick={onBar(k)} />
          ))}
          {total && (
            <Bar dataKey="__total" name="Total" fill="var(--ink-2)" radius={[4, 4, 0, 0]} isAnimationActive={false} maxBarSize={32}
              cursor={canOpen() ? "pointer" : undefined} onClick={onBar()} />
          )}
          {lines && shown.map((k) => (
            <Line key={k} dataKey={k} name={k} type="linear" stroke={colourOf(k)} strokeWidth={2}
              dot={rows.length <= 16 ? { r: 2.5 } : false} activeDot={dots.dot(k, colourOf(k), canOpen(k))} isAnimationActive={false} />
          ))}
          {/* after the bars, so the average shows over them */}
          {average != null && !lines && <ReferenceLine y={average} stroke="var(--ink-2)" strokeDasharray="4 4" />}
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}

/**
 * A share of net spending as a whole percent. A refund-heavy bucket is a
 * negative share ("\u221212%"), and the others then add up to more than 100%.
 * Net spending at or under zero has no share to give, so it says "n/a".
 */
export function percent(part: Cents, whole: Cents): string {
  if (whole <= 0) return "n/a";
  if (part === 0) return "0%";
  const p = Math.abs(part / whole) * 100;
  const text = p < 1 ? "<1%" : `${Math.round(p)}%`;
  return part < 0 ? `\u2212${text}` : text;
}

function WhereItWent({ d }: { d: Spending }) {
  const [open, setOpen] = useState<Set<string>>(new Set());
  const whole = d.totals.spend;
  const toggle = (name: string) => setOpen((s) => {
    const n = new Set(s);
    if (n.has(name)) n.delete(name); else n.add(name);
    return n;
  });
  // a phone stacks the bar under the name, so a long name has the width of the row
  const bucketRow = "grid w-full grid-cols-[16px_minmax(0,1fr)_3rem_5.5rem] items-center gap-x-2 gap-y-1 py-2 text-left text-[13px] hover:bg-hover sm:grid-cols-[16px_minmax(0,1fr)_minmax(0,1.2fr)_3rem_5.5rem]";
  const catRow = "grid grid-cols-[minmax(0,1fr)_3rem_5.5rem] items-center gap-x-2 gap-y-1 py-1.5 text-[13px] text-ink-2 no-underline hover:bg-hover sm:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)_3rem_5.5rem]";
  const bucketMeter = "col-start-2 col-end-[-1] row-start-2 sm:col-start-3 sm:col-end-4 sm:row-start-1";
  const catMeter = "col-start-1 col-end-[-1] row-start-2 sm:col-start-2 sm:col-end-3 sm:row-start-1";
  return (
    <Card title="Where it went" actions={<span className="text-[12px] text-ink-3">a month</span>}>
      <ul className="m-0 list-none p-0">
        {d.buckets.map((b) => {
          const look = bucketLook(d.bucket_style[b.name], Math.max(0, d.bucket_order.indexOf(b.name)));
          const colour = look.colour;
          const isOpen = open.has(b.name);
          return (
            <li key={b.name} className="border-t border-line first:border-t-0">
              <button type="button" onClick={() => toggle(b.name)} aria-expanded={isOpen} className={bucketRow}>
                <ChevronRight size={14} aria-hidden className={`col-start-1 row-start-1 text-ink-3 transition-transform ${isOpen ? "rotate-90" : ""}`} />
                <span className="col-start-2 row-start-1 flex min-w-0 items-center gap-2 font-medium">
                  <BucketMark look={look} />
                  <span className="min-w-0 break-words">{b.name}</span>
                </span>
                <Meter share={whole > 0 ? b.total / whole : 0} colour={colour} className={bucketMeter} />
                <span className="num col-start-3 row-start-1 text-right text-ink-2 sm:col-start-4">{percent(b.total, whole)}</span>
                <span className="num col-start-4 row-start-1 text-right sm:col-start-5">{formatSgd(b.per_month)}</span>
              </button>
              {isOpen && (
                <ul className="m-0 list-none pb-1 pl-6">
                  {b.categories.map((c) => {
                    const to = spendLink(d.link, c.link);
                    return (
                      <li key={c.name}>
                        <a href={to} onClick={onLinkClick(to)} className={catRow}>
                          <span className="col-start-1 row-start-1 min-w-0 break-words">{c.name}</span>
                          <Meter share={whole > 0 ? c.total / whole : 0} colour={colour} faint className={catMeter} />
                          <span className="num col-start-2 row-start-1 text-right sm:col-start-3">{percent(c.total, whole)}</span>
                          <span className="num col-start-3 row-start-1 text-right sm:col-start-4">{formatSgd(c.per_month)}</span>
                        </a>
                      </li>
                    );
                  })}
                </ul>
              )}
            </li>
          );
        })}
      </ul>
    </Card>
  );
}

function Meter({ share, colour, faint = false, className = "" }: { share: number; colour: string; faint?: boolean; className?: string }) {
  return (
    <span className={`block h-1.5 overflow-hidden rounded-full bg-hover ${className}`} aria-hidden>
      <span className="block h-full rounded-full" style={{ width: `${Math.max(0, Math.min(1, share)) * 100}%`, background: colour, opacity: faint ? 0.6 : 1 }} />
    </span>
  );
}

function List({ title, rows }: {
  title: string; rows: { key: string; left: string; sub: string; right: string; to: string | null }[];
}) {
  return (
    <Card title={title}>
      {rows.length === 0 ? <EmptyState title="Nothing in this range" /> : (
        <ul className="m-0 list-none p-0">
          <CollapsibleRows rows={rows} limit={COLLAPSED_ROWS.mid} keyOf={(r) => r.key} render={(r) => {
            const body = (
              <>
                <span className="min-w-0">
                  <span className="block truncate text-[13px]">{r.left}</span>
                  <span className="block text-[12px] text-ink-3">{r.sub}</span>
                </span>
                <span className="num text-[13px]">{r.right}</span>
              </>
            );
            const cls = "flex items-center justify-between gap-3 border-t border-line py-1.5 first:border-t-0";
            return (
              <li>
                {r.to ? <a href={r.to} onClick={onLinkClick(r.to)} className={`${cls} text-ink no-underline hover:bg-hover`}>{body}</a>
                  : <div className={cls}>{body}</div>}
              </li>
            );
          }} />
        </ul>
      )}
    </Card>
  );
}
