import { useApi, type Change, type ComingStatus, type ComingUp, type ComingUpRow, type NetWorth, type Overview, type SpendingChart as Chart } from "../api";
import { axisSgd, formatSgd, type Cents } from "../money";
import { navigate, onLinkClick } from "../router";
import { formatDate, formatDay, formatMonth } from "../dates";
import { Card } from "../components/Card";
import { EmptyState } from "../components/EmptyState";
import { RangeTrack, Sparkline, Tile, TileGrid, type Tone } from "../components/Tile";
import { useChartView, ViewSwitch, type ChartView } from "../components/ViewSwitch";
import { CollapsibleRows, COLLAPSED_ROWS } from "../components/CollapsibleRows";
import { SPEND_KEYS, SPEND_VIEWS, SpendChart, signed } from "./SpendingExplorer";
import { ComingUpItem } from "./Recurring";
import { EyeList, openEyeList } from "../components/NeedsYourEye";

// G13: Home is four tiles, "Needs your eye" when something does, twelve months
// of spending by bucket, and this month's coming-up payments.

const EYE_LINES = 4;
const COMING_UP_ROWS = 6;
const TRACE_MONTHS = 12;

export function Home({ overview }: { overview: Overview }) {
  const h = overview.headline;
  const chart = overview.spending;
  const last = lastBar(chart);
  const versus = last ? last.spend - chart.totals.per_month : null;
  const money = (c: number | null) => (c == null ? "No data" : formatSgd(c));
  const lines = overview.needs_your_eye.lines ?? [];
  const [view, setView] = useChartView<ChartView>("home-spending", SPEND_KEYS, "stacked");
  // the sparklines read the same history the Net worth page draws; without it the tiles are just figures
  const history = useApi<NetWorth>("/api/net-worth").data?.months.slice(-TRACE_MONTHS) ?? [];

  return (
    <div className="flex flex-col gap-3">
      <section aria-label="Headline figures">
        <TileGrid cols={4}>
          <Tile label="Net worth incl. CPF" value={money(h.net_worth_incl_cpf)} {...changeNote(h.net_worth_incl_cpf, h.net_worth_change)}
            visual={<Trace months={history} of="total" what="Net worth" />} />
          <Tile label="Cash" value={money(h.cash)} {...changeNote(h.cash, h.cash_change)}
            visual={<Trace months={history} of="cash" what="Cash" />} />
          <Tile label="Runway" value={h.runway_months == null ? "No data" : `${h.runway_months.toFixed(1)} mo`}
            {...runwayNote(h.runway_months, h.runway_range)}
            visual={h.runway_months != null && h.runway_range && (
              <RangeTrack value={h.runway_months} low={h.runway_range[0]} high={h.runway_range[1]}
                tone={runwayNote(h.runway_months, h.runway_range).tone ?? "neutral"}
                label={`${h.runway_months.toFixed(1)} months of cash; the ${h.runway_range[0]} to ${h.runway_range[1]} month range is shaded`} />)} />
          <Tile label={last ? `Spent in ${formatMonth(last.month)}` : "Spent last month"} value={money(last ? last.spend : null)}
            note={versus == null ? undefined : `${signed(versus)} on the ${chart.months.length}-month average`}
            noteTone={versus != null && versus > 0 ? "warn" : "good"} />
        </TileGrid>
      </section>

      {lines.length > 0 && (
        // G18: the first four, most urgent first; the rest are in the sidebar's list, which "All N" opens
        <Card title="Needs your eye" actions={lines.length > EYE_LINES && (
          <button type="button" onClick={openEyeList} className="text-[12px] text-ink-2 hover:underline">All {lines.length}</button>)}>
          <div className="-mx-2"><EyeList lines={lines.slice(0, EYE_LINES)} /></div>
        </Card>
      )}

      <Card title="Spending, last 12 months" actions={chart.months.length > 0 && (
        <div className="flex flex-wrap items-center justify-end gap-x-3 gap-y-2">
          {view !== "lines" && (
            <span className="num flex items-center gap-1.5 text-[12px] text-ink-3">
              <span className="w-4 border-t border-dashed border-ink-2" aria-hidden />average {formatSgd(chart.totals.per_month)}
            </span>
          )}
          <ViewSwitch label="Chart view" value={view} options={SPEND_VIEWS} onChange={setView} />
        </div>)}>
        {chart.months.length === 0 ? (
          <EmptyState title="No whole month of spending in the record yet" />
        ) : (
          <SpendChart d={chart} view={view} average={chart.totals.per_month} open={navigate} />
        )}
      </Card>

      <ComingUpCard up={overview.coming_up} />
      {overview.as_of && <p className="m-0 text-[12px] text-ink-3">Figures as of {formatDay(overview.as_of)}.</p>}
    </div>
  );
}


/** The chart's last month and what it holds: the "Spent in" tile, so the two always agree. */
export function lastBar(d: Chart): { month: string; spend: Cents } | null {
  const i = d.months.length - 1;
  if (i < 0) return null;
  return { month: d.months[i], spend: d.series.reduce((a, s) => a + (s.values[i] ?? 0), 0) };
}

/** A tile's change, only under a figure: a change with nothing above it says nothing. */
function changeNote(figure: Cents | null, c: Change | null): { note?: string; noteTone?: Tone } {
  if (figure == null || !c) return {};
  return { note: `${signed(c.change)} since ${formatDate(c.since)}`, noteTone: c.change >= 0 ? "good" : "warn" };
}

/** Below the range is the one that matters: the figure itself turns red. */
function runwayNote(months: number | null, range: [number, number] | null): { note?: string; tone?: Tone } {
  if (months == null || !range) return {};
  const [low, high] = range;
  const where = months < low ? "below" : months <= high ? "inside" : "above";
  return { note: `${where} the ${low} to ${high} month range`, tone: where === "below" ? "danger" : where === "inside" ? "good" : "neutral" };
}

/** A tile's sparkline: one part of the monthly net worth history, as far back as it goes. */
function Trace({ months, of, what }: { months: NetWorth["months"]; of: "total" | "cash"; what: string }) {
  const rows = months.filter((m) => m[of] != null);
  if (rows.length < 2) return null;
  const first = rows[0];
  const last = rows[rows.length - 1];
  return <Sparkline values={rows.map((m) => m[of] as Cents)}
    label={`${what}, ${formatMonth(first.month)} ${formatSgd(first[of] as Cents)} to ${formatMonth(last.month)} ${formatSgd(last[of] as Cents)}`} />;
}

export { axisSgd };


// what wants a look first: nothing came, then paid at an odd amount, then an
// extra charge (G16), then still to come, then paid. The card opens on four rows, so this is the order they
// are listed in; a bill late or due low in the month must not sit under four
// paid ones.
const URGENCY: Record<ComingStatus, number> = { late: 0, different: 1, extra: 2, due: 3, paid: 4 };

/** The rows Home lists, most pressing first and by date within a kind; the most pressing few when there is no room for all. */
export function comingUpRows(rows: ComingUpRow[], room = COMING_UP_ROWS): ComingUpRow[] {
  return [...rows].sort((a, b) => URGENCY[a.status] - URGENCY[b.status] || a.expected.localeCompare(b.expected)).slice(0, room);
}

function ComingUpCard({ up }: { up: ComingUp }) {
  const shown = comingUpRows(up.rows);
  const more = up.rows.length - shown.length;
  return (
    <Card title={`Coming up in ${formatMonth(up.month)}`} actions={
      <a href="/recurring" onClick={onLinkClick("/recurring")} className="text-[12px] text-ink-2 no-underline hover:underline">
        {more > 0 ? `All ${up.rows.length} on Recurring` : "Recurring"}
      </a>}>
      {up.rows.length === 0 ? <EmptyState title="Nothing expected this month" /> : (
        <ul className="m-0 list-none p-0">
          <CollapsibleRows rows={shown} limit={COLLAPSED_ROWS.mid} keyOf={(r) => `${r.id}-${r.expected}-${r.status}`}
            render={(r) => <ComingUpItem r={r} />} />
        </ul>
      )}
    </Card>
  );
}
