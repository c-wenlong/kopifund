import { Card } from "../components/Card";
import { Pager, usePager } from "../components/Pager";
import { HEAD_ROW, TABLE_BOX, TH } from "../components/tableKit";
import { formatDate } from "../dates";
import { columnUnit, fmt, isNum, label, plan, SERIES_NOTE, STAT_SUBTITLE, type Row, type StatResult } from "./hint";
import { StatChart, hasLinesView } from "./StatChart";
import { useChartView, ViewSwitch, viewsOf, type ChartView } from "../components/ViewSwitch";
import { VersusList } from "./VersusList";
import { StatTiles } from "./StatTiles";
import { tipExtras } from "./tipExtras";

const LINE_VIEWS = viewsOf("stacked", "lines");
const LINE_KEYS = ["stacked", "lines"] as const;

/**
 * One stat result as a card: the title and the chart, nothing else on show.
 * The chart and its hover card carry every figure the result holds, and a few
 * cards add tiles above the chart (StatTiles). The findings and caveats the
 * stat writes are not shown: prose beside a chart repeats the chart.
 */
export function StatCard({ result }: { result: StatResult }) {
  const [view, setView] = useChartView<ChartView>(`stat-${result.id}`, LINE_KEYS, "stacked");
  const p = plan(result);
  const rows = p.kind === "table" ? p.rows : p.allRows;
  const switchable = p.kind === "chart" && hasLinesView(p);
  return (
    <Card title={result.title} id={`stat-${result.id}`} actions={
      switchable ? <ViewSwitch label="Chart view" value={view} options={LINE_VIEWS} onChange={setView} /> : undefined}>
      {STAT_SUBTITLE[result.id] && <p className="m-0 -mt-1.5 mb-3 text-[13px] text-ink-2">{STAT_SUBTITLE[result.id]}</p>}
      <StatTiles result={result} />
      {p.kind === "chart" && (
        <StatChart p={p} view={view} notes={notesOf(result.id)}
          extras={(row) => tipExtras(result.id, row, p.xKey, p.series, result.chart?.units, result.chart?.unit)} />
      )}
      {p.kind === "versus" && <VersusList p={p} />}
      {p.note && <p className="m-0 mt-2 text-[12px] text-ink-3">{p.note}</p>}
      {p.kind === "table" && !!rows?.length && <RowsTable rows={rows} units={result.chart?.units} unit={result.chart?.unit} />}
    </Card>
  );
}

// what each legend entry of this stat means, by series key
const notesOf = (id: string) => Object.fromEntries(
  Object.entries(SERIES_NOTE).filter(([k]) => k.startsWith(`${id}/`)).map(([k, v]) => [k.slice(id.length + 1), v]));

export function RowsTable({ rows, units, unit }: { rows: Row[]; units?: Record<string, string>; unit?: string }) {
  const cols = [...new Set(rows.flatMap((r) => Object.keys(r)))].filter((c) =>
    rows.some((r) => r[c] !== null && typeof r[c] !== "object"),
  );
  const unitOf = (c: string) => columnUnit(c, units, unit);
  // G13: a long table is paged like every other list; a short one shows no pager
  const pager = usePager("stat-rows", rows.length);
  return (
    <>
    <div className={TABLE_BOX}>
      <table className="num w-full border-collapse text-[12px]">
        <thead>
          <tr className={HEAD_ROW}>{cols.map((c) => <th key={c} className={`${TH} px-2`}>{label(c)}</th>)}</tr>
        </thead>
        <tbody>
          {pager.slice(rows).map((r, i) => (
            <tr key={i} className="border-t border-line">
              {cols.map((c) => (
                <td key={c} className={`px-2 py-1 ${isNum(r[c]) ? "text-right" : ""}`}>
                  {isNum(r[c]) ? fmt(r[c], unitOf(c), true) : formatDate(r[c])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
    <Pager pager={pager} label="Table pages" />
    </>
  );
}
