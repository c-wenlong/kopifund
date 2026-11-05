// G26: a nested share-of-whole widget. With `groupBy: "bucket"` each slice
// sits in its bucket's tile, in the bucket's own colour; the legend hides a
// whole group, never the last one (useHidden). `groupBy: "none"`, or a
// categories answer that never arrives, gives a flat treemap, each slice in
// its series colour. Recharts lays the rectangles out; the drawing is ours so
// every colour is a token.
import { useMemo } from "react";
import { ResponsiveContainer, Tooltip, Treemap } from "recharts";
import type { LucideIcon } from "lucide-react";
import { useApi } from "../../../api";
import { Card } from "../../../components/Card";
import { cutName, SeriesLegend, TipBox, TipRow, useHidden, type LegendItem } from "../../../components/chartKit";
import { EmptyState } from "../../../components/EmptyState";
import { CardSkeleton, Problem } from "../../../components/States";
import type { CategoriesState, CategoryGroup, WidgetAnswer, WidgetQuery } from "../../../types.gen";
import { navigate } from "../../../router";
import { useWidgetData } from "../../../widgets";
import {
  fmtValue, shareText, sliceColour, sliceIcon, slicesFor, totalOf, txnLink, unitOf,
  type Aggregate, type Slice,
} from "./model";

/**
 * What a treemap widget draws. `groupBy: "bucket"` nests each slice under its
 * bucket, the hierarchy Categories knows; "none" draws the slices flat.
 */
export type TreemapStateConfig = {
  title: string;
  query: WidgetQuery;
  groupBy?: "bucket" | "none";
  aggregate?: Aggregate;
  height?: number;
};

/** A plain config for the editor to start from: this month's spend by category, grouped into buckets. */
export const defaultTreemapConfig: TreemapStateConfig = {
  title: "Spending by category",
  query: { source: "spending", metric: "spend", dimension: "category" },
  groupBy: "bucket",
  aggregate: "latest",
  height: 300,
};

/** A leaf in the treemap's data: a slice plus the group it sits in and whether it heads that group. */
export type TreeLeaf = Slice & { group: string; colour: string; head: boolean };

/** A group's node: its name and colour (only for the legend; the leaves do the drawing) and its leaves. */
export type TreeGroup = { name: string; colour: string; icon?: LucideIcon; children: TreeLeaf[] };

const NO_BUCKET = "(no bucket)";

/**
 * Slices as a treemap's data. `groups` null draws them flat in series colours;
 * with buckets, each slice sits under its bucket's node in the bucket's
 * colour, slices the buckets do not know under "(no bucket)". Groups and their
 * leaves sort biggest first, so a group's head leaf is its largest and carries
 * the group's name on the chart.
 */
export function treeFor(slices: readonly Slice[], groups: readonly CategoryGroup[] | null): TreeGroup[] {
  const rank = (name: string, place: number) => sliceColour(name, place, null);
  if (!groups) {
    const leaves = [...slices].sort((a, b) => b.value - a.value)
      .map((s, i) => ({ ...s, group: "", colour: rank(s.name, i), head: false }));
    return [{ name: "", colour: "", children: leaves }];
  }
  const bucketOf = new Map<string, CategoryGroup>();
  for (const g of groups) {
    bucketOf.set(g.name, g);
    for (const c of g.categories) bucketOf.set(c.name, g);
  }
  const styleOf = new Map<string, CategoryGroup | null>();
  const byGroup = new Map<string, Slice[]>();
  for (const s of slices) {
    const g = s.name === "Everything else" ? null : (bucketOf.get(s.name) ?? null);
    const name = g?.name ?? NO_BUCKET;
    if (!byGroup.has(name)) {
      byGroup.set(name, []);
      styleOf.set(name, g);
    }
    byGroup.get(name)?.push(s);
  }
  const groupsOut: TreeGroup[] = [...byGroup.entries()].map(([name, members], i) => {
    const style = styleOf.get(name) ?? undefined;
    const look = style ? { colour: sliceColour(name, i, { [name]: style }), icon: sliceIcon(name, { [name]: style }) } : null;
    const children = [...members].sort((a, b) => b.value - a.value)
      .map((s, j) => ({ ...s, group: name, colour: look?.colour ?? rank(s.name, j), head: j === 0 }));
    return { name, colour: look?.colour ?? rank(name, i), icon: look?.icon, children };
  });
  groupsOut.sort((a, b) => totalOf(b.children) - totalOf(a.children));
  return groupsOut;
}

/** The ink a treemap writes inside a coloured tile: --tip contrasts with the series tokens in both themes. */
const TILE_INK = "var(--tip)";

type NodeProps = {
  x: number; y: number; width: number; height: number; depth: number;
  name: string; value: number; group?: string; colour?: string; head?: boolean; index: number;
};

/**
 * One treemap node, ours rather than Recharts'. Groups (depth 1) draw only a
 * frame, since the leaves cover their area; a leaf draws its tile, its group's
 * name on the group's head leaf, and its own name and figure when they fit.
 */
function TreeNode(props: Partial<NodeProps>) {
  const { x = 0, y = 0, width = 0, height = 0, depth = 0, name = "", group, colour, head } = props;
  if (depth === 0 || width < 2 || height < 2) return null;
  if (depth === 1) {
    return <rect x={x} y={y} width={width} height={height} fill="none" stroke="var(--panel)" strokeWidth={2} rx={3} />;
  }
  const room = width > 46 && height > 24;
  return (
    <g>
      <rect x={x} y={y} width={width} height={height} rx={2} fill={colour} stroke="var(--panel)" strokeWidth={1} />
      {head && group && width > 64 && height > 46 && (
        <>
          <rect x={x + 3} y={y + 3} width={Math.min(width - 6, group.length * 6 + 10)} height={14} rx={3} fill="var(--panel)" opacity={0.85} />
          <text x={x + 8} y={y + 13} fontSize={10} fill="var(--ink-2)">{cutName(group, 18)}</text>
        </>
      )}
      {room && (
        <text x={x + 4} y={y + height - 6} fontSize={11} fill={TILE_INK}>{cutName(name, Math.floor((width - 8) / 6.5))}</text>
      )}
    </g>
  );
}

export function TreemapState({ config = defaultTreemapConfig }: { config?: TreemapStateConfig }) {
  const grouped = config.groupBy !== "none";
  const { data: answer, error, loading } = useWidgetData(config.query);
  const cats = useApi<CategoriesState>(grouped ? "/api/categories" : null);
  const height = config.height ?? 300;

  if (error) return <Problem message={error} retry />;
  if (!answer) {
    return loading
      ? <CardSkeleton height={height} what={`Loading ${config.title}`} />
      : <Card title={config.title}><EmptyState title="Nothing to show yet" /></Card>;
  }
  return <TreemapStateView config={config} answer={answer} height={height} grouped={grouped}
    groups={grouped ? (cats.data?.groups ?? null) : null} />;
}

function TreemapStateView({ config, answer, groups, grouped, height }: {
  config: TreemapStateConfig; answer: WidgetAnswer; groups: CategoryGroup[] | null; grouped: boolean; height: number;
}) {
  const slices = useMemo(() => slicesFor([answer], config.aggregate ?? "latest"), [answer, config.aggregate]);
  const tree = useMemo(() => treeFor(slices, groups), [slices, groups]);
  const unit = unitOf([answer]);
  const keys = grouped ? tree.map((g) => g.name) : tree[0]?.children.map((l) => l.key) ?? [];
  const { hidden, toggle } = useHidden(keys);
  const data = grouped
    ? tree.filter((g) => !hidden.has(g.name)).map((g) => ({ ...g, children: g.children }))
    : [{ ...tree[0], children: tree[0].children.filter((l) => !hidden.has(l.key)) }];
  const leaves = data.flatMap((g) => g.children);
  const total = totalOf(leaves);
  const clickable = leaves.some((l) => l.link);
  const items: LegendItem[] = grouped
    ? tree.map((g) => ({ key: g.name, label: g.name, colour: g.colour, icon: g.icon }))
    : tree[0].children.map((l) => ({ key: l.key, label: l.name, colour: l.colour }));
  const open = (node: { name: string; link?: Record<string, string> | null }) => {
    const to = txnLink(node?.link);
    if (to) navigate(to);
  };

  const tip = ({ active, payload }: { active?: boolean; payload?: { payload?: TreeLeaf & { depth?: number } }[] }) => {
    const node = payload?.[0]?.payload;
    if (!active || !node || node.depth === 1) return null;
    return (
      <TipBox heading={node.group ? `${node.group} · ${node.name}` : node.name}
        total={shareText(node.value, total)}
        hint={node.link ? "Click the tile to see its transactions" : undefined}>
        <TipRow colour={node.colour} name={node.name}
          value={unit === "sgd_minor" ? node.value : fmtValue(node.value, unit)} />
      </TipBox>
    );
  };

  return (
    <Card title={config.title}>
      {leaves.length === 0 ? (
        <EmptyState title="Nothing to show yet">The widget's query answered with no figures.</EmptyState>
      ) : (
        <>
          {items.length > 1 && <SeriesLegend items={items} hidden={hidden} onToggle={toggle} />}
          <ResponsiveContainer width="100%" height={height}>
            <Treemap data={data} dataKey="value" nameKey="name" isAnimationActive={false}
              content={<TreeNode />} onClick={open}>
              <Tooltip content={tip} />
            </Treemap>
          </ResponsiveContainer>
          {clickable && <p className="m-0 mt-1 text-[11px] text-ink-3">Click a tile to see its transactions.</p>}
          {answer.note && <p className="m-0 mt-1 text-[11px] text-ink-3">{answer.note.charAt(0).toUpperCase() + answer.note.slice(1)}.</p>}
        </>
      )}
    </Card>
  );
}
