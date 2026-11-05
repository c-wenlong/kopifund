// G26: stand-in widget bodies until the chart widgets land under
// dashboard/widgets/ (#7, #8, #9). Each draws a bound query's answer in the
// plainest way that still proves the binding, and is registered under the
// type id its real widget will take over (registry.tsx). A widget with no
// query says so rather than drawing nothing.
import type { WidgetAnswer, WidgetSeries, WidgetUnit } from "../api";
import { formatSgd } from "../money";
import { useWidgetData } from "../widgets";
import { Loading, Problem } from "../components/States";
import { Sparkline } from "../components/Tile";
import type { WidgetProps } from "./registry";

function valuesOf(s: WidgetSeries | undefined): (number | null)[] {
  return s?.values_minor ?? s?.values ?? [];
}

/** The last non-null figure and its own index, so a label names the same entry. */
function lastValue(s: WidgetSeries | undefined): { v: number; i: number } | null {
  const vs = valuesOf(s);
  for (let i = vs.length - 1; i >= 0; i--) if (vs[i] != null) return { v: vs[i]!, i };
  return null;
}

/** A figure in the answer's unit: cents as S$, the rest as the number the unit names (two scales kept apart, AGENTS.md). */
function figure(unit: WidgetUnit, v: number): string {
  if (unit === "sgd_minor") return formatSgd(v);
  if (unit === "months") return `${Number.isInteger(v) ? v : v.toFixed(1)} months`;
  if (unit === "fraction") return `${(v * 100).toFixed(0)}%`;
  return `${Number.isInteger(v) ? v : v.toFixed(1)}%`;
}

/** Where the answer came from, in one line; a projection's own note shows too. */
function SourceNote({ a }: { a: WidgetAnswer }) {
  const words = [a.source, a.metric, a.dimension !== "month" ? `by ${a.dimension}` : ""].filter(Boolean).join(" · ");
  return <p className="m-0 mt-auto pt-2 text-[11px] text-ink-3">{a.note ?? words}</p>;
}

function Unbound() {
  return <p className="m-0 text-[13px] text-ink-3">No question is bound to this widget yet.</p>;
}

function useAnswer(widget: WidgetProps["widget"]) {
  return useWidgetData(widget.query ?? null);
}

/** `kpi`: one figure with its note. */
export function KpiWidget({ widget }: WidgetProps) {
  const res = useAnswer(widget);
  if (!widget.query) return <Unbound />;
  if (res.error) return <Problem message={res.error} />;
  if (!res.data) return <Loading what="Loading the figure" />;
  const s = res.data.series[0];
  const last = lastValue(s);
  const note = [s?.name, last ? res.data.labels.at(last.i) : undefined].filter(Boolean).join(" · ");
  return (
    <div className="flex h-full flex-col">
      <div className="num text-[24px] font-semibold tracking-tight">{last == null ? "No data" : figure(res.data.unit, last.v)}</div>
      {note && <div className="num mt-0.5 text-[12px] text-ink-2">{note}</div>}
      <SourceNote a={res.data} />
    </div>
  );
}

/** `line`: a series over its labels, drawn as a sparkline. */
export function SeriesWidget({ widget }: WidgetProps) {
  const res = useAnswer(widget);
  if (!widget.query) return <Unbound />;
  if (res.error) return <Problem message={res.error} />;
  if (!res.data) return <Loading what="Loading the series" />;
  const vs = valuesOf(res.data.series[0]).map((v) => v ?? 0);
  const last = lastValue(res.data.series[0]);
  const span = [res.data.labels.at(0), res.data.labels.at(-1)].filter(Boolean).join(" to ");
  return (
    <div className="flex h-full flex-col">
      <div className="num text-[18px] font-semibold tracking-tight">
        {last == null ? "No data" : figure(res.data.unit, last.v)}
      </div>
      <div className="mt-1"><Sparkline values={vs} label={`${widget.title ?? "Widget"} over ${span || "time"}`} /></div>
      <div className="mt-1 text-[12px] text-ink-2">{span}</div>
      <SourceNote a={res.data} />
    </div>
  );
}

/** `pie`: a spread over a dimension, as its top rows with a share bar each. */
export function PieWidget({ widget }: WidgetProps) {
  const res = useAnswer(widget);
  if (!widget.query) return <Unbound />;
  if (res.error) return <Problem message={res.error} />;
  if (!res.data) return <Loading what="Loading the split" />;
  const a = res.data;
  const vs = valuesOf(a.series[0]);
  // the figure keeps its sign: a row whose refunds pass its spending is a
  // negative number, and only the bar sizes by magnitude
  const rows = a.labels.map((label, i) => ({ label, v: vs[i] ?? 0 }))
    .sort((x, y) => Math.abs(y.v) - Math.abs(x.v)).slice(0, 6);
  const top = Math.abs(rows[0]?.v ?? 0) || 1;
  return (
    <div className="flex h-full min-h-0 flex-col">
      <ul className="m-0 min-h-0 flex-1 list-none space-y-1.5 overflow-hidden p-0">
        {rows.map((r) => (
          <li key={r.label} className="text-[12px]">
            <div className="flex items-baseline justify-between gap-2">
              <span className="min-w-0 truncate">{r.label}</span>
              <span className="num shrink-0 text-ink-2">{figure(a.unit, r.v)}</span>
            </div>
            <div className="mt-0.5 h-1 rounded-full bg-hover">
              <div className="h-full rounded-full bg-bar" style={{ width: `${(Math.abs(r.v) / top) * 100}%` }} />
            </div>
          </li>
        ))}
        {!rows.length && <li className="text-ink-3">Nothing in this range.</li>}
      </ul>
      <SourceNote a={a} />
    </div>
  );
}

/** `note`: a line of text kept in the widget's options. */
export function TextWidget({ widget }: WidgetProps) {
  const text = typeof widget.options?.text === "string" ? widget.options.text : "";
  return <p className="m-0 whitespace-pre-wrap text-[13px] text-ink-2">{text || "A note for this board."}</p>;
}
