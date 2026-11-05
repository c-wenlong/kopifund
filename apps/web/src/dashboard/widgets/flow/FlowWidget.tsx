// G26 (#9): the flow widget. Three widget queries (income by month, spend over
// the middle dimension, transfers over it too) plus one category query per
// middle node build the graph flow.ts lays out as a pure-SVG Sankey: no chart
// library, every band's thickness in integer cents. A click on a node or band
// opens the Transactions rows behind it; hover names the amount and its share
// of inflow. Under 480px the SVG keeps its width and the card scrolls
// sideways, so nothing overlaps.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Loaded } from "../../../api";
import { Card } from "../../../components/Card";
import { cutName, TipBox, TipRow } from "../../../components/chartKit";
import { CardSkeleton, Problem } from "../../../components/States";
import { formatSgd, type Cents } from "../../../money";
import { navigate } from "../../../router";
import type { WidgetAnswer, WidgetQuery } from "../../../types.gen";
import { useWidgetData } from "../../../widgets";
import {
  buildFlow, childrenFromAnswer, defaultFlowConfig, flowQueries, incomeLink, layoutFlow, txnUrl,
  type FlowConfig, type FlowGraph, type FlowSlice, type LaidLink, type LaidNode,
} from "./flow";

/** The narrowest width the Sankey draws at; a slimmer card scrolls sideways instead of squeezing. */
const FLOW_W_MIN = 480;

/** The measured width of the element `ref` holds, 0 until a ResizeObserver answers. */
function useWidth(ref: React.RefObject<HTMLElement | null>): number {
  const [w, setW] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver !== "function") return;
    const ro = new ResizeObserver(() => setW(el.clientWidth));
    ro.observe(el);
    setW(el.clientWidth);
    return () => ro.disconnect();
  }, [ref]);
  return w;
}

/**
 * One middle node's category query, reported to the flow. A slice's answer
 * lands in the parent's map as it arrives (the kept answer at once, then the
 * server's), so a range change repaints on the kept copy before revalidating.
 */
function SliceFetch({ name, query, report }: {
  name: string; query: WidgetQuery; report: (name: string, res: Loaded<WidgetAnswer>) => void;
}) {
  const res = useWidgetData(query);
  useEffect(() => report(name, res), [name, res, report]);
  return null;
}

/** A figure's share of the inflow column: "34%" or "3.5%", "0%" when there is no inflow. */
function shareText(minor: Cents, inflow: Cents): string {
  if (inflow <= 0) return "0%";
  const pct = Math.round((minor / inflow) * 1000) / 10;
  return `${Number.isInteger(pct) ? pct.toFixed(0) : pct}%`;
}

type Tip = { x: number; y: number; body: React.ReactNode };

/** A click that opens the rows behind a node or band, the way the app's links do (modified clicks stay native). */
const open = (url: string) => (e: React.MouseEvent) => {
  if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
  e.preventDefault();
  navigate(url);
};

/** The drawn Sankey: laid-out nodes and bands plus the hover card they share. */
function Sankey({ graph }: { graph: FlowGraph }) {
  const wrap = useRef<HTMLDivElement>(null);
  const measured = useWidth(wrap);
  const [tip, setTip] = useState<Tip | null>(null);
  const [hot, setHot] = useState<number | null>(null);
  const laid = useMemo(
    () => layoutFlow(graph, Math.max(FLOW_W_MIN, measured || 640)),
    [graph, measured],
  );

  const show = (e: { clientX: number; clientY: number } | null, at: { x: number; y: number }, body: React.ReactNode) => {
    const r = wrap.current?.getBoundingClientRect();
    if (!r) return;
    const x = e ? e.clientX - r.left : at.x;
    const y = e ? e.clientY - r.top : at.y;
    setTip({ x: Math.max(40, Math.min(r.width - 40, x)), y: Math.max(0, y - 8), body });
  };

  const linkTip = (l: LaidLink): React.ReactNode => (
    <TipBox heading={<span>{l.source.label} → {l.target.label}</span>} total={l.minor}
      hint={l.link ? "Click for the transactions" : undefined}>
      <TipRow name="Share of inflow" value={shareText(l.minor, graph.inflow_minor)} bare keepZero />
    </TipBox>
  );
  const nodeTip = (n: LaidNode): React.ReactNode => (
    <TipBox heading={n.label} total={n.minor} hint={n.link ? "Click for the transactions" : undefined}>
      <TipRow name="Share of inflow" value={shareText(n.minor, graph.inflow_minor)} bare keepZero />
    </TipBox>
  );
  const tipEvents = (body: () => React.ReactNode, at: { x: number; y: number }) => ({
    onMouseMove: (e: React.MouseEvent) => show(e, at, body()),
    onMouseLeave: () => { setTip(null); setHot(null); },
    onFocus: () => show(null, at, body()),
    onBlur: () => { setTip(null); setHot(null); },
  });

  return (
    <div ref={wrap} className="relative">
      <div className="overflow-x-auto">
        <svg width={laid.width} height={laid.height} role="img"
          aria-label="Money flow: income into buckets and categories" className="block">
          {laid.links.map((l, i) => {
            const inner = (
              <>
                <path d={l.d} fill="none" stroke={l.colour} strokeWidth={l.width}
                  opacity={hot === i ? 0.62 : 0.3} />
                <path d={l.d} fill="none" stroke="transparent" strokeWidth={Math.max(10, l.width)} />
              </>
            );
            const at = { x: (l.source.x + l.source.w + l.target.x) / 2, y: (l.source.y + l.target.y) / 2 };
            const events = {
              ...tipEvents(() => linkTip(l), at),
              onMouseEnter: () => setHot(i),
            };
            const url = txnUrl(l.link);
            return url ? (
              <a key={i} href={url} onClick={open(url)} aria-label={`${l.source.label} to ${l.target.label}`} {...events}>
                {inner}
              </a>
            ) : (
              <g key={i} {...events}>{inner}</g>
            );
          })}
          {laid.nodes.map((n) => {
            const label = (
              <>
                <rect x={n.x} y={n.y} width={n.w} height={n.h} rx={2} fill={n.colour} />
                <text x={n.column === 2 ? n.x - 6 : n.x + n.w + 6} y={n.y + n.h / 2} dy={4}
                  textAnchor={n.column === 2 ? "end" : "start"} fontSize={11} fill="var(--ink-2)">
                  {cutName(n.label, 20)}
                  <tspan dx={6} fill="var(--ink-3)" className="num">{formatSgd(n.minor)}</tspan>
                </text>
              </>
            );
            const at = { x: n.x + n.w / 2, y: n.y + n.h / 2 };
            const events = tipEvents(() => nodeTip(n), at);
            const url = txnUrl(n.link);
            return url ? (
              <a key={n.id} href={url} onClick={open(url)} aria-label={n.label} {...events}>{label}</a>
            ) : (
              <g key={n.id} {...events}>{label}</g>
            );
          })}
        </svg>
      </div>
      {tip && (
        <div className="pointer-events-none absolute z-10 -translate-x-1/2 -translate-y-full" style={{ left: tip.x, top: tip.y }}>
          {tip.body}
        </div>
      )}
    </div>
  );
}

/**
 * Money moving through the range: income on the left, the middle column's
 * buckets (or need classes) it funds, and each one's categories. A widget on
 * a dashboard draws this with its own `config`; everything it shows is read
 * through widget queries.
 */
export function FlowWidget({ config = defaultFlowConfig }: { config?: FlowConfig }) {
  const cfg = {
    title: config.title ?? defaultFlowConfig.title,
    range: config.range,
    filters: config.filters,
    middle: config.middle ?? "bucket",
    maxCategories: config.maxCategories ?? 7,
    transfers: config.transfers ?? true,
  };
  const queries = useMemo(() => flowQueries(cfg), [JSON.stringify(cfg)]);
  const income = useWidgetData(queries.income);
  const middle = useWidgetData(queries.middle);
  const transfers = useWidgetData(queries.transfers);

  const names = middle.data?.labels ?? [];
  const namesKey = names.join("");
  const [slices, setSlices] = useState<Record<string, Loaded<WidgetAnswer>>>({});
  const report = useCallback(
    (name: string, res: Loaded<WidgetAnswer>) => setSlices((s) => (s[name] === res ? s : { ...s, [name]: res })),
    [],
  );
  // a node the new range dropped never reclaims its slice entry
  useEffect(() => {
    const keep = new Set(names);
    setSlices((s) => (Object.keys(s).every((k) => keep.has(k)) ? s : Object.fromEntries(Object.entries(s).filter(([k]) => keep.has(k)))));
  }, [namesKey]);

  if (income.error) return <Problem message={income.error} retry />;
  if (middle.error) return <Problem message={middle.error} retry />;
  if (queries.transfers != null && transfers.error) return <Problem message={transfers.error} retry />;
  if (!income.data || !middle.data || (queries.transfers != null && !transfers.data)) {
    return <CardSkeleton height={280} what="Loading the money flow" />;
  }

  const inc = income.data;
  const md = middle.data;
  const mvals = md.series[0]?.values_minor ?? [];
  const pending = names.some((n, i) => (mvals[i] ?? 0) > 0 && !slices[n]?.data && !slices[n]?.error);
  const graph = buildFlow({
    income_minor: (inc.series[0]?.values_minor ?? []).reduce((s: number, v) => s + (v ?? 0), 0),
    incomeLink: incomeLink(inc, cfg.filters),
    slices: names.map((name, i): FlowSlice => ({
      name,
      minor: mvals[i] ?? 0,
      link: md.label_links?.[i] ?? null,
      children: childrenFromAnswer(slices[name]?.data, cfg.maxCategories ?? 7),
    })),
    // a null query keeps its last answer in useWidgetData; transfers turned
    // off read as none, or the old nodes and their cents would stay drawn
    transfers: queries.transfers == null
      ? []
      : (transfers.data?.labels ?? []).map((name, i): FlowSlice => ({
        name,
        minor: transfers.data?.series[0]?.values_minor?.[i] ?? 0,
        link: transfers.data?.label_links?.[i] ?? null,
        children: [],
      })),
  });

  return (
    <Card title={cfg.title ?? "Money flow"}>
      {names.map((n, i) => ((mvals[i] ?? 0) > 0
        ? <SliceFetch key={n} name={n} query={queries.category(n)} report={report} />
        : null))}
      {pending ? (
        <div className="rounded-md bg-tile motion-safe:animate-pulse" style={{ height: 280 }} aria-busy>
          <span role="status" className="sr-only">Loading the categories</span>
        </div>
      ) : graph.nodes.length === 0 ? (
        <p className="m-0 text-[13px] text-ink-3">No income or spending in this range.</p>
      ) : (
        <Sankey graph={graph} />
      )}
    </Card>
  );
}
