// G26 (#9): the flow widget's model and layout. A Sankey of one month or
// range: Income -> the middle column (spend buckets, or need classes when the
// config asks) -> each middle node's categories, with the rest made explicit
// rather than dropped: a bucket's folded "Everything else", transfers, the
// unspent rest of income, and a "Beyond income" source when the outflow beats
// income. Every weight is integer cents end to end; only the drawn positions
// are numbers.
//
// Sum rules. The left column holds income (plus "Beyond income" when spend
// and transfers together exceed it) and the middle column totals the same:
//
//   income + beyond = sum(middle nodes) = spend + transfers + unspent
//
// Income funds the middle nodes in drawn order and what is left over lands in
// "Unspent"; whatever income cannot cover arrives from "Beyond income", so a
// middle node can carry two inflows and still read exactly. Each middle
// node's children add to its own total: the query layer folds a bucket's
// smaller categories into an "Everything else" row (server/finance/
// dashboard.py, spending.TOP) which is drawn as its own node, never dropped,
// and a slice whose per-bucket answer failed or has not arrived shows the
// same residual for its whole total. A slice with a negative total (refunds
// over spend, or transfers that came back) is not drawn and its cents stay in
// "Unspent".
import { REST_COLOUR, seriesColour } from "../../../components/chartKit";
import type { Cents } from "../../../money";
import type { WidgetAnswer, WidgetQuery, WidgetRange } from "../../../types.gen";

/** The folded row the query layer appends past its top list (spending.OTHER). */
export const OTHER_NAME = "Everything else";

/** What the middle column spreads income over: spend buckets, or need classes. */
export type FlowMiddle = "bucket" | "need";

export type FlowConfig = {
  /** The card's heading; "Money flow" when not given. */
  title?: string;
  /** The months the flow covers (the widget query's range: YYYY-MM or YYYY-MM-DD ends; a day resolves to its month). */
  range?: WidgetRange;
  /** Transactions' own filter names, applied to every query the flow reads. The middle dimension's own filter is set per node, so a `bucket` (or `need`) entry here is overridden for the category queries. */
  filters?: Record<string, string>;
  /** The middle column: "bucket" (the default) or "need". */
  middle?: FlowMiddle;
  /** Categories drawn under each middle node before the rest fold into "Everything else". The query layer never returns more than 7, so higher asks for 7. */
  maxCategories?: number;
  /** Draw transfers (money moved to savings and investments) as their own middle nodes. Default true. */
  transfers?: boolean;
};

export const defaultFlowConfig: FlowConfig = {
  title: "Money flow",
  middle: "bucket",
  maxCategories: 7,
  transfers: true,
};

/** The Transactions filter that names one middle node, for its category query. */
const MIDDLE_FILTER: Record<FlowMiddle, string> = { bucket: "bucket", need: "need" };

/** The widget queries the flow is built from: income by month, spend and transfers over the middle dimension, and per middle node its categories. */
export function flowQueries(config: FlowConfig): {
  income: WidgetQuery;
  middle: WidgetQuery;
  transfers: WidgetQuery | null;
  category: (name: string) => WidgetQuery;
} {
  const { range, filters } = config;
  const middle = config.middle ?? "bucket";
  const base = { range, filters };
  return {
    income: { source: "spending", metric: "income", dimension: "month", ...base },
    middle: { source: "spending", metric: "spend", dimension: middle, ...base },
    transfers: config.transfers === false
      ? null
      : { source: "spending", metric: "transfers", dimension: middle, ...base },
    category: (name) => ({
      source: "spending", metric: "spend", dimension: "category", range,
      filters: { ...filters, [MIDDLE_FILTER[middle]]: name },
    }),
  };
}

/** Transactions' link params for the query's own filters (the server writes them on every point link; the income node builds its own). */
export function filtersLink(filters: Record<string, string> | undefined): Record<string, string> {
  const out = { ...(filters ?? {}) };
  delete out.scope;
  const scope = filters?.scope ?? (filters?.trip ? "all" : "sg");
  if (scope === "sg") out.home = "1";
  return out;
}

/** Where a node or link click goes: the Transactions page filtered to exactly the rows behind it. */
export function txnUrl(link: Record<string, string> | null | undefined): string | null {
  return link ? `/transactions?${new URLSearchParams(link)}` : null;
}

/** The income node's own link: the answer's resolved range plus the income flag and the query's filters. */
export function incomeLink(answer: WidgetAnswer, filters: Record<string, string> | undefined): Record<string, string> {
  const link: Record<string, string> = {};
  if (answer.range?.from) link.from = answer.range.from;
  if (answer.range?.to) link.to = answer.range.to;
  link.income = "1";
  return { ...link, ...filtersLink(filters) };
}

export type FlowNodeKind = "income" | "beyond" | "middle" | "category" | "rest" | "transfers" | "kept";

export type FlowNode = {
  id: string;
  label: string;
  column: 0 | 1 | 2;
  /** The node's total, integer cents. Its inflow (or outflow for a leaf) sums to exactly this. */
  minor: Cents;
  colour: string;
  kind: FlowNodeKind;
  link: Record<string, string> | null;
};

export type FlowLink = {
  source: FlowNode;
  target: FlowNode;
  minor: Cents;
  /** The Transactions filters listing the rows behind the link; null where no filter names it (a residual). */
  link: Record<string, string> | null;
};

/** A category under a middle node; `rest` is the folded "Everything else". */
export type FlowChild = { name: string; minor: Cents; kind: "category" | "rest"; link: Record<string, string> | null };

/** One middle node: its total and its (already folded) children. */
export type FlowSlice = {
  name: string;
  minor: Cents;
  link: Record<string, string> | null;
  children: FlowChild[];
};

export type FlowGraph = {
  nodes: FlowNode[];
  links: FlowLink[];
  /** Income in the range, integer cents. */
  income_minor: Cents;
  /** The inflow column's total: income plus any "Beyond income" source. */
  inflow_minor: Cents;
  spend_minor: Cents;
  transfers_minor: Cents;
};

/**
 * A category answer's rows as one middle node's children, capped at
 * `maxCategories`; the rest (and the answer's own folded row, when it is cut)
 * merge into one "Everything else" child whose link still names the rows it
 * holds: the same base filters with `not_categories` reset to what is shown.
 */
export function childrenFromAnswer(answer: WidgetAnswer | null | undefined, maxCategories: number): FlowChild[] {
  if (!answer) return [];
  const values = answer.series[0]?.values_minor ?? [];
  const all = answer.labels
    .map((name, i): FlowChild => ({
      name,
      minor: values[i] ?? 0,
      kind: name === OTHER_NAME ? "rest" : "category",
      link: answer.label_links?.[i] ?? null,
    }))
    .filter((c) => c.minor > 0);
  const cap = Math.max(0, Math.floor(maxCategories));
  if (all.length <= cap) return all;
  const shown = all.slice(0, cap);
  const folded = all.slice(cap);
  const base = { ...(shown[0]?.link ?? folded.find((c) => c.link)?.link ?? {}) };
  delete base.category;
  const link: Record<string, string> | null = Object.keys(base).length
    ? { ...base, not_categories: JSON.stringify(shown.map((c) => c.name)) }
    : null;
  // an empty shown list is no filter at all: the residual holds the whole slice
  if (link && shown.length === 0) delete link.not_categories;
  return [...shown, { name: OTHER_NAME, minor: folded.reduce((s, c) => s + c.minor, 0), kind: "rest", link }];
}

/**
 * The graph the Sankey draws, from the answers already read. `slices` are the
 * middle column's spend nodes in drawn order; `transfers` the middle nodes
 * money moved to savings sits in. Nodes at zero or below are not drawn
 * (a net-negative slice's cents stay in "Unspent").
 */
export function buildFlow(input: {
  income_minor: Cents;
  incomeLink: Record<string, string> | null;
  slices: FlowSlice[];
  transfers: FlowSlice[];
}): FlowGraph {
  const slices = input.slices.filter((s) => s.minor > 0);
  const transfers = input.transfers.filter((s) => s.minor > 0);
  const spend = slices.reduce((s, x) => s + x.minor, 0);
  const moved = transfers.reduce((s, x) => s + x.minor, 0);
  const income = Math.max(0, input.income_minor);
  const outflow = spend + moved;
  const beyond = Math.max(0, outflow - income);
  const kept = Math.max(0, income - outflow);

  const nodes: FlowNode[] = [];
  const links: FlowLink[] = [];
  const incomeNode: FlowNode | null = income > 0
    ? { id: "income", label: "Income", column: 0, minor: income, colour: "var(--good)", kind: "income", link: input.incomeLink }
    : null;
  const beyondNode: FlowNode | null = beyond > 0
    ? { id: "beyond", label: "Beyond income", column: 0, minor: beyond, colour: REST_COLOUR, kind: "beyond", link: null }
    : null;
  if (incomeNode) nodes.push(incomeNode);
  if (beyondNode) nodes.push(beyondNode);

  let incomeLeft = income;
  const fund = (n: FlowNode) => {
    const fromIncome = Math.max(0, Math.min(n.minor, incomeLeft));
    incomeLeft -= fromIncome;
    if (fromIncome > 0 && incomeNode) links.push({ source: incomeNode, target: n, minor: fromIncome, link: n.link });
    const rest = n.minor - fromIncome;
    if (rest > 0 && beyondNode) links.push({ source: beyondNode, target: n, minor: rest, link: n.link });
  };

  slices.forEach((s, i) => {
    const n: FlowNode = { id: `m${i}`, label: s.name, column: 1, minor: s.minor, colour: seriesColour(i), kind: "middle", link: s.link };
    nodes.push(n);
    fund(n);
    const children = [...s.children];
    const childSum = children.reduce((t, c) => t + c.minor, 0);
    if (childSum < s.minor) {
      children.push({ name: OTHER_NAME, minor: s.minor - childSum, kind: "rest", link: s.link });
    }
    children.filter((c) => c.minor > 0).forEach((c, j) => {
      const child: FlowNode = {
        id: `m${i}c${j}`, label: c.name, column: 2, minor: c.minor,
        colour: c.kind === "rest" ? REST_COLOUR : n.colour, kind: c.kind === "rest" ? "rest" : "category",
        link: c.link,
      };
      nodes.push(child);
      links.push({ source: n, target: child, minor: child.minor, link: c.link });
    });
  });

  transfers.forEach((s, i) => {
    const n: FlowNode = { id: `t${i}`, label: s.name, column: 1, minor: s.minor, colour: "var(--info)", kind: "transfers", link: s.link };
    nodes.push(n);
    fund(n);
  });

  if (kept > 0 && incomeNode) {
    const n: FlowNode = { id: "kept", label: "Unspent", column: 1, minor: kept, colour: REST_COLOUR, kind: "kept", link: null };
    nodes.push(n);
    links.push({ source: incomeNode, target: n, minor: kept, link: null });
  }

  return { nodes, links, income_minor: income, inflow_minor: income + beyond, spend_minor: spend, transfers_minor: moved };
}

// ------------------------------------------------------------ the SVG layout

export const FLOW_NODE_W = 12;
export const FLOW_GAP = 12;
export const FLOW_PAD_Y = 8;
export const FLOW_MIN_PITCH = 26;
export const FLOW_H_MIN = 280;
export const FLOW_H_MAX = 560;
const FLOW_MIN_NODE_H = 3;

export type LaidNode = FlowNode & { x: number; y: number; w: number; h: number };
export type LaidLink = {
  d: string;
  minor: Cents;
  /** The band's thickness in pixels: the same cents-to-pixels scale as the node heights. */
  width: number;
  colour: string;
  source: LaidNode;
  target: LaidNode;
  link: Record<string, string> | null;
};

export type FlowLayout = { width: number; height: number; nodes: LaidNode[]; links: LaidLink[] };

/**
 * Nodes and link bands in pixels. Every column shares one cents-to-pixels
 * scale (the tightest column sets it), so a band's thickness means the same
 * money everywhere. Columns centre vertically; a node's own links stack from
 * its top, sorted so bands cross as little as the order allows.
 */
export function layoutFlow(graph: FlowGraph, width: number): FlowLayout {
  const cols: FlowNode[][] = [[], [], []];
  for (const n of graph.nodes) cols[n.column].push(n);
  const maxCount = Math.max(1, ...cols.map((c) => c.length));
  const height = Math.round(Math.min(FLOW_H_MAX, Math.max(FLOW_H_MIN, maxCount * FLOW_MIN_PITCH)));

  let ky = Infinity;
  for (const c of cols) {
    if (!c.length) continue;
    const avail = height - 2 * FLOW_PAD_Y - FLOW_GAP * (c.length - 1);
    ky = Math.min(ky, avail / Math.max(1, c.reduce((s, n) => s + n.minor, 0)));
  }
  if (!Number.isFinite(ky) || ky <= 0) ky = 0;

  const xOf = (col: number) => (col === 0 ? 0 : col === 1 ? (width - FLOW_NODE_W) / 2 : width - FLOW_NODE_W);
  const placed = new Map<string, LaidNode>();
  cols.forEach((c, col) => {
    const used = c.reduce((s, n) => s + Math.max(FLOW_MIN_NODE_H, n.minor * ky), 0) + FLOW_GAP * (c.length - 1);
    let y = Math.max(FLOW_PAD_Y, (height - used) / 2);
    for (const n of c) {
      const h = Math.max(FLOW_MIN_NODE_H, n.minor * ky);
      placed.set(n.id, { ...n, x: xOf(col), y, w: FLOW_NODE_W, h });
      y += h + FLOW_GAP;
    }
  });

  const yOf = (id: string) => placed.get(id)?.y ?? 0;
  const out = new Map<string, FlowLink[]>();
  const into = new Map<string, FlowLink[]>();
  for (const l of graph.links) {
    out.set(l.source.id, [...(out.get(l.source.id) ?? []), l]);
    into.set(l.target.id, [...(into.get(l.target.id) ?? []), l]);
  }

  const thick = (l: FlowLink) => l.minor * ky;
  const pos = new Map<FlowLink, { sy: number; ty: number }>();
  for (const n of graph.nodes) {
    const n0 = placed.get(n.id);
    if (!n0) continue;
    let y = n0.y;
    for (const l of (out.get(n.id) ?? []).sort((a, b) => yOf(a.target.id) - yOf(b.target.id))) {
      pos.set(l, { ...(pos.get(l) ?? { ty: 0 }), sy: y + thick(l) / 2 });
      y += thick(l);
    }
    y = n0.y;
    for (const l of (into.get(n.id) ?? [])
      .sort((a, b) => (placed.get(a.source.id)?.x ?? 0) - (placed.get(b.source.id)?.x ?? 0) || yOf(a.source.id) - yOf(b.source.id))) {
      pos.set(l, { ...(pos.get(l) ?? { sy: 0 }), ty: y + thick(l) / 2 });
      y += thick(l);
    }
  }

  const links: LaidLink[] = [];
  for (const l of graph.links) {
    const s = placed.get(l.source.id);
    const t = placed.get(l.target.id);
    const p = pos.get(l);
    if (!s || !t || !p || l.minor <= 0) continue;
    const x0 = s.x + s.w;
    const x1 = t.x;
    const xi = (x0 + x1) / 2;
    links.push({
      d: `M ${x0} ${p.sy} C ${xi} ${p.sy}, ${xi} ${p.ty}, ${x1} ${p.ty}`,
      minor: l.minor, width: thick(l), colour: s.colour, source: s, target: t, link: l.link,
    });
  }

  return { width, height, nodes: [...placed.values()], links };
}
