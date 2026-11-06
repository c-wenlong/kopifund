// G26: a share-of-whole widget, a filled pie or a ring (donut). Bound to one
// query, or several when the whole is several series (need / want / savings is
// three queries on the one stat). Colours come from the chart kit's series
// tokens, or a bucket's own when a slice names one; the legend's entries hide
// a slice, never the last one (useHidden).
import { useMemo } from "react";
import { Cell, Pie, PieChart, Tooltip } from "recharts";
import { MaybeCard } from "../../../components/Card";
import {
  ChartBox, SeriesLegend, TipBox, TipRow, useHidden, type ChartHeight, type LegendItem,
} from "../../../components/chartKit";
import { EmptyState } from "../../../components/EmptyState";
import { CardSkeleton, Loading, Problem } from "../../../components/States";
import { label } from "../../../stats/hint";
import type { WidgetAnswer, WidgetQuery } from "../../../types.gen";
import { navigate } from "../../../router";
import {
  combineAnswers, fmtValue, shareText, sliceColour, sliceIcon, slicesFor, totalOf, txnLink, unitOf, useBucketStyles,
  useWidgetAnswers, type Aggregate, type Slice,
} from "./model";

/**
 * What a pie or donut widget draws. `query` is one WidgetQuery, or several
 * whose series each become a slice; `donut` rings the pie, `showTotal` writes
 * the whole's figure in the ring's centre; `aggregate` says how a multi-series
 * answer becomes one figure a series.
 */
export type PieStateConfig = {
  title: string;
  query: WidgetQuery | WidgetQuery[];
  donut?: boolean;
  showTotal?: boolean;
  aggregate?: Aggregate;
  /** px, or "fill" on a dashboard: the room the widget's cell leaves */
  height?: ChartHeight;
};

/** A plain config for the editor to start from: this month's spend by bucket, as a ring with its total inside. */
export const defaultPieConfig: PieStateConfig = {
  title: "Spending by bucket",
  query: { source: "spending", metric: "spend", dimension: "bucket" },
  donut: true,
  showTotal: true,
  aggregate: "latest",
  height: 240,
};

export function PieState({ config = defaultPieConfig, bare = false }: { config?: PieStateConfig; bare?: boolean }) {
  const queries = useMemo(() => (Array.isArray(config.query) ? config.query : [config.query]), [config.query]);
  // one useWidgetData per bound query, so the hook list's size is the query
  // count: an edit that changes it remounts here rather than change it mid-life
  return <PieStateLoad key={queries.length} config={config} queries={queries} bare={bare} />;
}

function PieStateLoad({ config, queries, bare }: { config: PieStateConfig; queries: WidgetQuery[]; bare: boolean }) {
  const { data: answers, error, loading } = combineAnswers(useWidgetAnswers(queries));
  const buckets = useBucketStyles(true);
  const height = config.height ?? 240;

  if (error) return <Problem message={error} retry />;
  if (!answers) {
    if (loading) {
      return bare
        ? <Loading what={`Loading ${config.title}`} />
        : <CardSkeleton height={typeof height === "number" ? height : 240} what={`Loading ${config.title}`} />;
    }
    return <MaybeCard title={config.title} bare={bare}><EmptyState title="Nothing to show yet" /></MaybeCard>;
  }
  return <PieStateView config={config} answers={answers} buckets={buckets} height={height} bare={bare} />;
}

/** The widget once its answers are in: legend, chart and the ring's total. */
function PieStateView({ config, answers, buckets, height, bare }: {
  config: PieStateConfig; answers: WidgetAnswer[]; buckets: ReturnType<typeof useBucketStyles>; height: ChartHeight;
  bare: boolean;
}) {
  const slices = useMemo(() => slicesFor(answers, config.aggregate ?? "latest"), [answers, config.aggregate]);
  const unit = unitOf(answers);
  const { hidden, toggle } = useHidden(slices.map((s) => s.key));
  const drawn = slices.filter((s) => !hidden.has(s.key));
  const total = totalOf(drawn);
  const colour = (s: Slice) => sliceColour(s.name, slices.indexOf(s), buckets);
  const data = drawn.map((s) => ({ ...s, colour: colour(s) }));
  const clickable = drawn.some((s) => s.link);
  const items: LegendItem[] = slices.map((s) => ({
    key: s.key, label: s.name, colour: colour(s), icon: sliceIcon(s.name, buckets),
  }));
  const raw = answers.find((a) => a.note)?.note;
  const note = raw ? raw.charAt(0).toUpperCase() + raw.slice(1) : undefined;
  const open = (s: Slice | undefined) => {
    const to = s ? txnLink(s.link) : null;
    if (to) navigate(to);
  };

  const tip = ({ active, payload }: { active?: boolean; payload?: { payload?: Slice & { colour: string } }[] }) => {
    const s = payload?.[0]?.payload;
    if (!active || !s) return null;
    const row = answers.length === 1 && answers[0].series.length === 1
      ? label(answers[0].metric)
      : (config.aggregate ?? "latest") === "sum" ? "Range total" : "Latest";
    return (
      <TipBox heading={s.name} total={shareText(s.value, total)}
        hint={s.link ? "Click the slice to see its transactions" : undefined}>
        <TipRow colour={s.colour} name={row} value={unit === "sgd_minor" ? s.value : fmtValue(s.value, unit)} />
      </TipBox>
    );
  };

  return (
    <MaybeCard title={config.title} bare={bare}>
      {slices.length === 0 ? (
        <EmptyState title="Nothing to show yet">The widget's query answered with no figures.</EmptyState>
      ) : (
        <>
          {slices.length > 1 && <SeriesLegend items={items} hidden={hidden} onToggle={toggle} />}
          <ChartBox height={height} overlay={config.donut !== false && config.showTotal !== false && drawn.length > 0 && (
            <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center text-center" aria-hidden>
              <span className="num text-[18px] font-semibold leading-tight">{fmtValue(total, unit)}</span>
              <span className="text-[11px] text-ink-3">{answers.length > 1 ? "total" : label(answers[0].metric)}</span>
            </div>
          )}>
            <PieChart>
              <Tooltip content={tip} />
              <Pie data={data} dataKey="value" nameKey="name" innerRadius={config.donut === false ? 0 : "58%"}
                outerRadius="85%" paddingAngle={config.donut === false ? 0 : 1} stroke="var(--panel)" strokeWidth={2}
                isAnimationActive={false} onClick={(_, i) => open(data[i])}>
                {data.map((s) => <Cell key={s.key} fill={s.colour} cursor={s.link ? "pointer" : undefined} />)}
              </Pie>
            </PieChart>
          </ChartBox>
          {clickable && <p className="m-0 mt-1 text-[11px] text-ink-3">Click a slice to see its transactions.</p>}
          {note && <p className="m-0 mt-1 text-[11px] text-ink-3">{note}.</p>}
        </>
      )}
    </MaybeCard>
  );
}
