// G13: the forward view on the Plan page. The stat (analytics/stats/forward_view.py)
// does the arithmetic; this draws its cash line, marks the lowest month and any
// month under zero. Its hover card carries the month's figures; the stat's
// findings are not shown.
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceArea,
  ReferenceDot,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { useApi, type StatResult } from "../api";
import { Card } from "../components/Card";
import { EmptyState } from "../components/EmptyState";
import { CardSkeleton, NoResult } from "../components/States";
import { activeDot, GRID, H_MAIN, Swatch, TipBox, X_AXIS, Y_AXIS } from "../components/chartKit";
import { formatSgd, type Cents } from "../money";
import { inCents } from "../stats/money";
import { formatMonth, formatRange } from "../dates";

const CASH = "var(--c1)";
const BELOW = "var(--warn)";

/** One projected month as the stat writes it: each figure in integer cents as `<name>_minor` (G20, and no dollars beside them). */
type StatMonth = Record<string, unknown> & { month: string };
export type LowPoint = { month: string; cash_minor: Cents };

/** One month in cents: what the chart and its hover read. */
export type Point = {
  month: string; cash: Cents; net: Cents; income: Cents; spending: Cents; trips: Cents; one_offs: Cents; other: Cents;
  /** the parts of `other`, and the pay before CPF and what CPF takes in, for the hover card */
  investing: Cents; insurance: Cents; other_outflow: Cents; repayments: Cents; gross: Cents; cpf: Cents;
};

// G20: the stat counts in integer cents and sends each month's figures as
// `<name>_minor` only, so this reads them as they are and every sum below is
// whole cents. There is no dollar fallback (a result from before G17 has no
// cents and waits for the next analysis). A figure missing or not whole is none.
const cents = (m: StatMonth, name: string): Cents => {
  const x = m[`${name}_minor`];
  return Number.isInteger(x) ? (x as Cents) : 0;
};

/** True for months that carry their cents; a result from before G17 does not, and waits for the next analysis. */
export const monthsInCents = (months: StatMonth[]): boolean => months.every((m) => Number.isInteger(m.cash_minor));

export function points(months: StatMonth[]): Point[] {
  return months.map((m) => {
    const trips = cents(m, "trips");
    const oneOffs = cents(m, "one_offs");
    return {
      month: m.month,
      cash: cents(m, "cash"),
      net: cents(m, "net"),
      income: cents(m, "take_home") + cents(m, "other_income"),
      spending: cents(m, "spend") + trips + oneOffs,
      trips,
      one_offs: oneOffs,
      other: cents(m, "investing") + cents(m, "insurance") + cents(m, "other_outflow") + cents(m, "repayments"),
      investing: cents(m, "investing"),
      insurance: cents(m, "insurance"),
      other_outflow: cents(m, "other_outflow"),
      repayments: cents(m, "repayments"),
      gross: cents(m, "gross"),
      cpf: cents(m, "cpf_total"),
    };
  });
}

/** The stat's own low point; a result from before it carried one is read off the months instead. */
export function lowPoint(data: Record<string, unknown>, rows: Point[]): LowPoint | null {
  const given = data.low_point as LowPoint | null | undefined;
  if (given && typeof given.month === "string" && Number.isInteger(given.cash_minor)) return given;
  if (!rows.length) return null;
  const low = rows.reduce((a, r) => (r.cash < a.cash ? r : a));
  return { month: low.month, cash_minor: low.cash };
}

function belowZero(data: Record<string, unknown>, rows: Point[]): string[] {
  const given = data.below_zero;
  return Array.isArray(given) ? given.filter((m): m is string => typeof m === "string") : rows.filter((r) => r.cash < 0).map((r) => r.month);
}

/**
 * The months under zero as runs of months next to each other on the chart:
 * "Mar 2027" for one month, "Mar to Jun 2027" for a run, and a comma between
 * runs, so a month that climbs back above zero is never named as under it.
 */
export function underZero(below: string[], rows: Point[]): string {
  const at = new Map(rows.map((r, i) => [r.month, i]));
  const runs: string[][] = [];
  let prev = -2;
  for (const m of below) {
    const i = at.get(m) ?? -2;
    if (runs.length && i >= 0 && i === prev + 1) runs[runs.length - 1].push(m);
    else runs.push([m]);
    prev = i;
  }
  return runs.map((r) => (r.length === 1 ? formatMonth(r[0]) : formatRange(r[0], r[r.length - 1]))).join(", ");
}

export function ForwardViewCard() {
  const { data, error, loading, status } = useApi<StatResult>("/api/results/forward-view");
  if (loading) return <CardSkeleton what="Loading the forward view" />;
  if (error || !data) return <NoResult status={status} error={error} what="forward view" />;
  const stat = data.data ?? {};
  const months = Array.isArray(stat.months) ? (stat.months as StatMonth[]) : [];
  // a result written before the months carried cents, or (G20) one whose chart is still in
  // dollars, is as good as none until the stats run again, as the server's 404 for it says
  if (!inCents(data) || !monthsInCents(months)) return <NoResult status={404} error={null} what="forward view" />;
  const rows = points(months);
  return (
    <Card title={data.title}>
      {rows.length > 0
        ? <ForwardChart rows={rows} low={lowPoint(stat, rows)} below={belowZero(stat, rows)} />
        : <EmptyState title="Nothing to project yet">The record needs a month with a closing balance.</EmptyState>}
    </Card>
  );
}

export function ForwardChart({ rows, low, below }: { rows: Point[]; low: LowPoint | null; below: string[] }) {
  const last = rows[rows.length - 1];
  const summary = [
    `Cash by month to ${formatMonth(last.month)}`,
    low ? `lowest ${formatSgd(low.cash_minor)} in ${formatMonth(low.month)}` : "",
    below.length ? `under zero in ${underZero(below, rows)}` : "",
  ].filter(Boolean).join(", ");
  return (
    <div role="img" aria-label={summary}>
      <div className="mb-1 flex flex-wrap gap-x-4 gap-y-1 text-[12px] text-ink-2">
        <span className="flex items-center gap-1.5"><Swatch colour={CASH} line />Cash at month end</span>
        {low && (
          <span className="flex items-center gap-1.5">
            <Swatch colour={CASH} dot />
            Lowest <span className="num font-medium text-ink">{formatSgd(low.cash_minor)}</span> in {formatMonth(low.month)}
          </span>
        )}
        {below.length > 0 && (
          <span className="flex items-center gap-1.5 text-warn">
            <span className="h-2 w-3 shrink-0 border-t border-dashed border-warn bg-warn-bg" aria-hidden />
            Under zero: {underZero(below, rows)}
          </span>
        )}
      </div>
      <ResponsiveContainer width="100%" height={H_MAIN}>
        <LineChart data={rows} margin={{ top: 24, right: 12, bottom: 0, left: 0 }}>
          <CartesianGrid {...GRID} />
          <XAxis dataKey="month" {...X_AXIS} />
          <YAxis {...Y_AXIS} />
          {below.length > 0 && <ReferenceArea y2={0} fill={BELOW} fillOpacity={0.08} stroke="none" ifOverflow="extendDomain" />}
          {below.length > 0 && (
            <ReferenceLine y={0} stroke={BELOW} strokeDasharray="4 3"
              label={{ value: "S$0", position: "insideBottomLeft", fontSize: 11, fill: BELOW }} />
          )}
          <Tooltip cursor={{ stroke: "var(--line)" }} content={({ active, payload }) => {
            const row = active ? (payload?.[0]?.payload as Point | undefined) : undefined;
            return row ? <MonthTip row={row} low={low?.month === row.month} /> : null;
          }} />
          <Line dataKey="cash" name="Cash" stroke={CASH} strokeWidth={2} dot={rows.length > 30 ? false : { r: 2 }}
            activeDot={activeDot(CASH)} isAnimationActive={false} />
          {low && (
            // the dot marks the month; the key above the chart says what it is, so no label sits over the line
            <ReferenceDot x={low.month} y={low.cash_minor} r={5} fill={CASH} stroke="var(--panel)" strokeWidth={2}
              ifOverflow="extendDomain" />
          )}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

/**
 * The hover card: the month's cash, then what came in and went out, which add
 * up to its net. Figures are to the cent, so the sum can be checked by eye.
 */
export function MonthTip({ row, low = false }: { row: Point; low?: boolean }) {
  const extra = [
    row.trips ? `trips ${formatSgd(row.trips)}` : "",
    row.one_offs ? `one-offs ${formatSgd(row.one_offs)}` : "",
  ].filter(Boolean).join(", ");
  const note = [low ? "The lowest month" : "", row.cash < 0 ? "Cash is under zero" : ""].filter(Boolean).join(". ");
  return (
    <div data-testid="forward-tip">
      <TipBox heading={formatMonth(row.month)} hint={note || undefined}>
        <TipRow name="Cash at month end" value={row.cash} warn={row.cash < 0} strong />
        <TipRow name="Income" value={row.income} />
        <TipRow name="Spending" value={row.spending} note={extra ? `with ${extra}` : undefined} />
        {row.other !== 0 && <TipRow name="Investing and other" value={row.other} />}
        {parts(row).map((x) => <TipRow key={x.name} name={x.name} value={x.value} sub />)}
        <TipRow name="Net" value={row.net} warn={row.net < 0} />
        {(row.gross !== 0 || row.cpf !== 0) && (
          <div className="mt-1 border-t border-line pt-1">
            {row.gross !== 0 && <TipRow name="Pay before CPF" value={row.gross} sub />}
            {row.cpf !== 0 && <TipRow name="CPF going in, not cash" value={row.cpf} sub />}
          </div>
        )}
      </TipBox>
    </div>
  );
}

/** What "Investing and other" is made of, when it is more than one thing. */
function parts(row: Point): { name: string; value: Cents }[] {
  const all = [
    { name: "Investing", value: row.investing },
    { name: "Insurance", value: row.insurance },
    { name: "Other outflow", value: row.other_outflow },
    { name: "Repayments", value: row.repayments },
  ].filter((x) => x.value !== 0);
  return all.length > 1 ? all : [];
}

function TipRow({ name, value, note, warn = false, strong = false, sub = false }: {
  name: string; value: Cents; note?: string; warn?: boolean; strong?: boolean; sub?: boolean;
}) {
  return (
    <div className={`flex items-baseline gap-2 ${sub ? "pl-3 text-[11px] text-ink-3" : "text-ink-2"} ${strong ? "mb-0.5 font-medium" : ""}`}>
      <span>{name}</span>
      {note && <span className="text-[11px] text-ink-3">{note}</span>}
      <span className={`num ml-auto pl-3 ${warn ? "text-warn" : sub ? "text-ink-2" : "text-ink"}`}>{formatSgd(value, { decimals: true })}</span>
    </div>
  );
}
