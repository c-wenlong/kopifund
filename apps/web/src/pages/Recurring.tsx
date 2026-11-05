import { useRef, useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import {
  postJSON, useApi, type ActiveSeries, type ComingStatus, type ComingUpRow, type IrregularPayee, type NotRecurring,
  type RecurringPage, type SeriesView as SeriesAnswer, type SlotStatus, type StoppedSeries,
} from "../api";
import { centsText, formatSgd, parseCents, type Cents } from "../money";
import { onLinkClick, useUnsavedGuard } from "../router";
import { useUrlParams } from "../useUrlParams";
import { Button } from "../components/Button";
import { Chip } from "../components/Chip";
import { CollapsibleRows, COLLAPSED_ROWS } from "../components/CollapsibleRows";
import { Card } from "../components/Card";
import { DateField } from "../components/DateField";
import { EmptyState } from "../components/EmptyState";
import { MoneyInput } from "../components/MoneyInput";
import { SaveBar } from "../components/SaveBar";
import { Select } from "../components/Select";
import { StatusChip, type StatusTone } from "../components/StatusChip";
import { Loading, Problem } from "../components/States";
import { HEAD_ROW, TH } from "../components/tableKit";
import { Progress, Tile, TileGrid } from "../components/Tile";
import { useWide } from "../components/useWide";
import { ago, formatDay, formatDayShort, formatMonth, isoParts } from "../dates";
import type { Cadence } from "../types.gen";
import { CADENCE_ICON, COMING_ICON, KindIcon, KindLabel, NEED_ICON, type IconTone } from "../icons";

// G13: the Recurring page is lists: one list of every live series, soonest
// next payment first, each with this month's status and a bar of its yearly
// cost, then the ones that stopped, then the payees marked recurring that show
// no regular spacing.
// ComingUpItem is Home's Coming up row.
// G16: a series opens its own view on this page (`?series=<id>`, SeriesView), where it is
// edited; the payee names on Recurring and a due or late row on Home's Coming up open it.

const STATUS: Record<ComingStatus, { text: string; cls: string }> = {
  paid: { text: "Paid", cls: "bg-good-bg text-good" },
  due: { text: "Due", cls: "bg-tile text-ink-2" },
  late: { text: "Late", cls: "bg-warn-bg text-warn" },
  different: { text: "Different amount", cls: "bg-info-bg text-info" },
  extra: { text: "Extra", cls: "bg-info-bg text-info" },
};
const CADENCE: Record<Cadence, string> = {
  weekly: "Weekly", fortnightly: "Fortnightly", "twice-monthly": "Twice a month", monthly: "Monthly",
  "two-monthly": "Every 2 months", quarterly: "Quarterly", "half-yearly": "Half-yearly", yearly: "Yearly",
};

const money = (c: Cents) => formatSgd(c, { decimals: true });
// G18: a cadence's word, led by its icon; a series with no cadence says nothing
const cadence = (c: Cadence | "", size = 11) => (c ? <KindLabel icon={CADENCE_ICON[c]} size={size}>{CADENCE[c] ?? c}</KindLabel> : "");
export const txnLink = (link: Record<string, string>) => `/transactions?${new URLSearchParams(link)}`;
/** G16: one series' own view on Recurring (finance/home.py's series_link writes the same). */
export const seriesPath = (id: string) => `/recurring?${new URLSearchParams({ series: id })}`;

// A payee opens its series (G16) or its transactions, but a name in ink reads as
// plain text. On hover or keyboard focus it is underlined and a chevron appears; a
// touch screen has no hover, so there the chevron is always there.
function Payee({ name, to, investing, tag = true }: { name: string; to: string | null; investing?: boolean; tag?: boolean }) {
  // G13: a payment not made yet has no row to open, so its name is plain text
  if (!to) {
    return (
      <span className="flex min-w-0 items-center gap-1.5">
        <span className="min-w-0 truncate font-medium text-ink">{name}</span>
        {investing && tag && <Chip icon={NEED_ICON.Investment}>Investing</Chip>}
      </span>
    );
  }
  return (
    <span className="flex min-w-0 items-center gap-1.5">
      <a href={to} onClick={onLinkClick(to)}
        className="group/payee flex min-w-0 items-center gap-0.5 font-medium text-ink no-underline">
        <span className="truncate underline-offset-2 group-hover/payee:underline group-focus-visible/payee:underline">{name}</span>
        <ChevronRight size={13} aria-hidden
          className="shrink-0 text-ink-3 opacity-0 transition-opacity group-hover/payee:opacity-100 group-focus-visible/payee:opacity-100 [@media(hover:none)]:opacity-100" />
      </a>
      {investing && tag && <Chip icon={NEED_ICON.Investment}>Investing</Chip>}
    </span>
  );
}

export function Recurring() {
  const [params] = useUrlParams();
  const id = params.get("series");
  // G16: a series' own view is this page with `?series=`, so Back returns to the lists
  return id ? <SeriesView id={id} /> : <Lists />;
}

function Lists() {
  const { data, error, loading } = useApi<RecurringPage>("/api/recurring");
  if (loading && !data) return <Loading what="Loading your recurring payments" />;
  if (error || !data) return <Problem message={error ?? "No recurring payments yet."} retry />;
  if (!data.ready)
    return <Problem message="No recurring payments result yet." hint="Run the analysis from the Upload page, or `make analyse`." />;
  const up = data.coming_up;
  return (
    <div className="flex flex-col gap-3">
      <p className="m-0 text-[12px] text-ink-3">As of {formatDay(up.as_of)}</p>
      <Totals up={up} />
      <SeriesList rows={[...data.active, ...data.paused]} coming={up.rows} totals={data.totals} asOf={up.as_of} />
      {data.stopped.length > 0 && <StoppedList rows={data.stopped} asOf={up.as_of} />}
      {data.irregular.length > 0 && <IrregularList rows={data.irregular} asOf={up.as_of} />}
      {data.not_recurring.length > 0 && <NotRecurringList rows={data.not_recurring} />}
    </div>
  );
}

function Totals({ up }: { up: RecurringPage["coming_up"] }) {
  // G16: an extra charge is listed but is not one of the month's bills
  const spend = up.rows.filter((r) => !r.investing && r.status !== "extra");
  const settled = spend.filter((r) => r.paid_on).length;
  const t = up.totals;
  return (
    <TileGrid cols={2}>
      <Tile label={`Paid in ${formatMonth(up.month)}`} value={formatSgd(t.paid)}
        note={`of ${formatSgd(t.committed)} committed, ${settled} of ${spend.length} payments`}
        visual={<Progress value={t.paid} of={t.committed} label={`${formatSgd(t.paid)} paid of ${formatSgd(t.committed)} committed`} />} />
      {t.investing > 0 && (
        <Tile label="To investing" value={formatSgd(t.investing)} note={`${formatSgd(t.investing_paid)} sent so far`}
          visual={<Progress value={t.investing_paid} of={t.investing} label={`${formatSgd(t.investing_paid)} sent of ${formatSgd(t.investing)}`} />} />
      )}
    </TileGrid>
  );
}

// A status as its icon alone on a phone (Home's Coming up), where a word beside
// the payee would push it off the line; the word is its tooltip and its
// accessible name. G18: the icon replaced a coloured dot, in the same colours.
const DOT: Record<ComingStatus, IconTone> = { paid: "good", due: "ink-3", late: "warn", different: "info", extra: "info" };

export function ComingUpItem({ r }: { r: ComingUpRow }) {
  const wide = useWide();
  const s = STATUS[r.status];
  const fmt = wide ? formatDay : formatDayShort;
  // paid: the day it arrived; otherwise the day it is expected, which is an estimate
  const day = r.paid_on ? fmt(r.paid_on) : `~${fmt(r.expected)}`;
  // on a phone the year goes (the month is the card's) and the status is a dot, so the payee keeps its room
  return (
    <li className="grid grid-cols-[3.25rem_minmax(0,1fr)_auto_5rem] items-center gap-2 border-t border-line py-1.5 text-[13px] first:border-t-0 md:grid-cols-[6.5rem_minmax(0,1fr)_auto_6.5rem] md:gap-3">
      <span className="num text-ink-2" title={`Expected ${formatDay(r.expected)}`}>{day}</span>
      <span className="min-w-0">
        {/* on a phone the Investing tag moves under the name, so a long payee is not cut short for it */}
        {/* G16: a paid row opens its payment; a due or late one has none yet, so it opens its series */}
        <Payee name={r.name} to={r.link ? txnLink(r.link) : seriesPath(r.id)} investing={r.investing} tag={wide} />
        <span className="block truncate text-[12px] text-ink-3">
          {!wide && r.investing && <span className="mr-1.5"><Chip icon={NEED_ICON.Investment}>Investing</Chip></span>}
          {cadence(r.cadence)}{r.category ? ` · ${r.category}` : ""}
        </span>
      </span>
      {wide
        ? <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[12px] font-medium ${s.cls}`}>
            <KindIcon icon={COMING_ICON[r.status]} size={12} />{s.text}
          </span>
        : <span className="grid size-4 place-items-center"><KindIcon icon={COMING_ICON[r.status]} size={14} tone={DOT[r.status]} label={s.text} /></span>}
      <span className="num text-right">
        <span className="block">{money(r.paid_amount ?? r.amount)}</span>
        {r.status === "different" && <span className="block text-[12px] text-ink-3">usual {money(r.amount)}</span>}
      </span>
    </li>
  );
}

// The Stopped table's column widths. Its last-seen date spans two columns.
const COLUMNS = ["28%", "11%", "13%", "15%", "20%", "13%"];

function SeriesTable({ head, children }: { head: { text: string; span?: number }[]; children: React.ReactNode }) {
  return (
    <table className="num w-full table-fixed border-collapse text-[13px]">
      <colgroup>{COLUMNS.map((w, i) => <col key={i} style={{ width: w }} />)}</colgroup>
      <thead>
        <tr className={HEAD_ROW}>
          {head.map((h, i) => <th key={h.text} colSpan={h.span} className={`${TH} ${i > 0 ? "text-right" : ""}`}>{h.text}</th>)}
        </tr>
      </thead>
      <tbody>{children}</tbody>
    </table>
  );
}

const td = "border-t border-line py-1.5 text-right";
const payeeCell = "overflow-hidden border-t border-line py-1.5 pr-3";

/** A phone's row: the payee and the figure that matters on the first line, the other facts under them. */
function PhoneRow({ payee, figure, children }: { payee: React.ReactNode; figure: string; children: React.ReactNode }) {
  return (
    <li className="border-t border-line py-2 text-[13px] first:border-t-0">
      <div className="flex items-center justify-between gap-3">{payee}<span className="num shrink-0">{figure}</span></div>
      <div className="num mt-0.5 text-[12px] leading-snug text-ink-2">{children}</div>
    </li>
  );
}

const Facts = ({ items }: { items: (React.ReactNode | false | "")[] }) =>
  <>{items.filter(Boolean).map((x, i) => <span key={i}>{i > 0 ? " · " : ""}{x}</span>)}</>;

/** A live series with this month's answer to it: the status of its most pressing expected payment. */
// G13: `link` is the series (its payee name opens its history); `month_link` is this month's payment, on the status chip, null when nothing is paid yet
// G16: a paused series wears `paused` in place of this month's status: it owes nothing this month
export type MergedSeries = ActiveSeries & { status: ComingStatus | "paused" | null; paid_on: string | null; paid_amount: Cents | null; month_link: Record<string, string> | null };

// which of a series' payments this month to speak for it: the one that wants
// a look first (a weekly series has a row per week), and of several paid, the latest.
// G16: finance/recurring.py `most_urgent` picks the same way for Home's "Needs your eye"
const URGENT: Record<ComingStatus, number> = { late: 0, different: 1, extra: 2, due: 3, paid: 4 };

const MONTHS_ON: Record<string, number> = { monthly: 1, "two-monthly": 2, quarterly: 3, "half-yearly": 6, yearly: 12 };
const DAYS_ON: Record<string, number> = { weekly: 7, fortnightly: 14, "twice-monthly": 15 };

/**
 * The day one cadence after an ISO day: seven or fourteen days for weekly and fortnightly, about half a
 * month for twice a month, else whole months on (the 31st falls back to the month's last day).
 */
export function stepOn(iso: string, cadence: string): string {
  const p = isoParts(iso);
  if (!p || p.d == null) return iso;
  const months = MONTHS_ON[cadence];
  if (!months) return new Date(Date.UTC(p.y, p.m - 1, p.d + (DAYS_ON[cadence] ?? 7))).toISOString().slice(0, 10);
  const i = p.y * 12 + p.m - 1 + months;
  const y = Math.floor(i / 12);
  const m = i % 12;
  const d = Math.min(p.d, new Date(Date.UTC(y, m + 1, 0)).getUTCDate());
  return new Date(Date.UTC(y, m, d)).toISOString().slice(0, 10);
}

/**
 * The day of the series' next unpaid payment, whatever chip the month wears:
 * the earliest late or due payment this month; failing one, a series whose
 * payments this month are all in (paid, or at a different amount) steps one
 * cadence on from its latest, when the detector's own date is not past that.
 */
function nextPayment(a: ActiveSeries, rows: ComingUpRow[]): string {
  const unpaid = rows.filter((r) => r.status === "late" || r.status === "due").map((r) => r.expected).sort();
  if (unpaid.length) return unpaid[0];
  // G16: an extra charge is dated by the day it came, not an expected day
  const latest = rows.filter((r) => r.status !== "extra").map((r) => r.expected).sort().pop();
  if (latest && latest >= a.next_expected) return stepOn(latest, a.cadence);
  return a.next_expected;
}

/**
 * Coming up and Active are the same payees twice, so they are one list: each
 * live series with the status of this month's payment, soonest next payment
 * first. A late one is expected in the past, so it leads. A series with no
 * payment expected this month has no status (unless it is already late).
 */
export function mergeSeries(active: ActiveSeries[], coming: ComingUpRow[]): MergedSeries[] {
  // G16: a payee can hold several series, so a payment is its series' by the detector's id
  const mine = (a: ActiveSeries) => coming.filter((c) => c.id === a.id);
  return active.map((a): MergedSeries => {
    // G16: a paused series is listed, but nothing is expected of it this month
    if (a.state === "paused") return { ...a, month_link: null, status: "paused", paid_on: null, paid_amount: null };
    const rows = mine(a);
    const pick = [...rows].sort((x, y) => URGENT[x.status] - URGENT[y.status]
      || (x.status === "paid" ? y.expected.localeCompare(x.expected) : x.expected.localeCompare(y.expected)))[0];
    // G16: a payment due this month never hides the series being late on an earlier one
    if (a.late && pick?.status !== "late")
      return { ...a, month_link: null, next_expected: nextPayment(a, rows), status: "late", paid_on: null, paid_amount: null };
    // the status chip speaks for this month, so it opens exactly this month's payment; the name stays the series
    const month_link = pick?.paid_on && pick.link ? pick.link : null;
    return { ...a, month_link, next_expected: nextPayment(a, rows),
      status: pick?.status ?? null, paid_on: pick?.paid_on ?? null, paid_amount: pick?.paid_amount ?? null };
  }).sort((x, y) => Number(x.state === "paused") - Number(y.state === "paused")
    || x.next_expected.localeCompare(y.next_expected) || x.name.localeCompare(y.name));
}

const STATUS_CHIP: Record<ComingStatus, { text: string; tone: StatusTone }> = {
  paid: { text: "Paid", tone: "good" },
  due: { text: "Due", tone: "neutral" },
  late: { text: "Late", tone: "warn" },
  different: { text: "Different amount", tone: "info" },
  extra: { text: "Extra", tone: "info" },
};

function StatusOf({ s }: { s: MergedSeries }) {
  if (!s.status) return <StatusChip tone="empty" icon={COMING_ICON.later} title="Nothing is expected this month">Later</StatusChip>;
  if (s.status === "paused")
    return <StatusChip tone="neutral" icon={COMING_ICON.paused} title="Nothing has come for a while; not counted in the totals">Paused</StatusChip>;
  // G16: an extra charge names its amount, since the row's figure is the usual one
  const text = s.status === "extra" && s.paid_amount != null ? `Extra ${money(s.paid_amount)}` : STATUS_CHIP[s.status].text;
  const chip = <StatusChip tone={STATUS_CHIP[s.status].tone} icon={COMING_ICON[s.status]}>{text}</StatusChip>;
  if (!s.month_link) return chip;
  const to = txnLink(s.month_link);
  const what = s.status === "extra" ? "an extra charge this month from" : "this month's payment to";
  return <a href={to} onClick={onLinkClick(to)} aria-label={`${text}: ${what} ${s.name}`} className="no-underline hover:opacity-80">{chip}</a>;
}

/**
 * A year's cost as a bar, on one scale for the whole list so the costliest
 * stand out. The bar is the mark and the figure beside it is its label.
 */
export function CostBar({ value, max, label }: { value: Cents; max: Cents; label: string }) {
  const share = max > 0 ? Math.max(0, Math.min(1, value / max)) : 0;
  return (
    <span role="img" aria-label={label} title={label} data-cost-bar="" data-share={share.toFixed(4)}
      className="block h-2 min-w-0 flex-1 overflow-hidden rounded-sm bg-hover">
      <span className="block h-full rounded-r-[4px]" style={{ width: `${share * 100}%`, minWidth: value > 0 ? 2 : 0, background: "var(--c1)" }} />
    </span>
  );
}

// One row, three shapes, picked by the card's own width (a container query), not
// the screen's, because the sidebar takes a varying share of it:
//   narrow   payee and amount, then status and date, then the year's bar
//   medium   date, payee, status, amount; the bar on a line under
//   wide     date, payee, status, amount, bar and yearly figure, all on one line
const SERIES_GRID = "grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1 "
  + "@xl:grid-cols-[5.5rem_minmax(0,1fr)_7.5rem_6.75rem] @4xl:grid-cols-[6rem_minmax(0,1fr)_7.5rem_6.75rem_minmax(12rem,17rem)]";

function SeriesList({ rows, coming, totals, asOf }: { rows: ActiveSeries[]; coming: ComingUpRow[]; totals: RecurringPage["totals"]; asOf: string }) {
  const list = mergeSeries(rows, coming);
  // the scale is the dearest spending series: investing is saving, not a cost, so it has no bar to dwarf the rest;
  // G16: nor has a paused series, which is in no total
  const barred = (r: MergedSeries) => !r.investing && r.state !== "paused";
  const max = Math.max(0, ...list.filter(barred).map((r) => r.yearly));
  const aside = `${formatSgd(totals.yearly_spending)} a year in spending`
    + (totals.yearly_investing ? ` · ${formatSgd(totals.yearly_investing)} investing` : "");
  const amount = (r: MergedSeries) => (r.status === "different" && r.paid_amount != null ? money(r.paid_amount)
    : r.amount_varies ? `~${money(r.amount)}` : money(r.amount));
  const usual = (r: MergedSeries) => (r.status === "different" && r.paid_amount != null ? `usual ${money(r.amount)}` : "");
  // the year only when it is not the record's own
  const day = (iso: string) => (iso.slice(0, 4) === asOf.slice(0, 4) ? formatDayShort(iso) : formatDay(iso));
  return (
    <Card title="Active" region actions={<span className="num text-[12px] text-ink-3">{aside}</span>}>
      {list.length === 0 ? <EmptyState title="No live recurring payments" /> : (
        <div className="@container">
          <ul className="m-0 list-none p-0">
            <li aria-hidden className={`${SERIES_GRID} ${HEAD_ROW} hidden items-center gap-x-3 pb-1 font-medium @xl:grid`}>
              <span>Next</span><span>Payee</span><span>This month</span><span className="text-right">Each time</span>
              <span className="hidden pl-3 @4xl:block">A year</span>
            </li>
            <CollapsibleRows rows={list} limit={COLLAPSED_ROWS.thin} keyOf={(r) => r.id} render={(r) => (
              <li className={`grid ${SERIES_GRID} border-t border-line py-2 text-[13px] first:border-t-0`}>
                <span className="order-1 min-w-0 @xl:order-2">
                  <Payee name={r.name} to={seriesPath(r.id)} investing={r.investing} />
                  <span className="block truncate text-[12px] text-ink-3">
                    <Facts items={[cadence(r.cadence), r.category, r.paid_on && `${r.status === "extra" ? "extra" : "paid"} ${day(r.paid_on)}`]} />
                  </span>
                </span>
                <span className="num order-2 text-right @xl:order-4">
                  <span className="block">{amount(r)}</span>
                  {usual(r) && <span className="block text-[12px] text-ink-3">{usual(r)}</span>}
                </span>
                {/* status and date sit side by side when narrow; each takes its own column when wider */}
                <span className="order-3 col-span-2 flex items-center gap-2 @xl:contents">
                  {r.state === "paused"
                    // G16: a paused series is expected on no day; its last payment says how long it has been quiet
                    ? <span className="num text-[12px] text-ink-3 @xl:order-1 @xl:text-[13px]" title={`Last paid ${formatDay(r.last_paid)}`}>last {day(r.last_paid)}</span>
                    : <span className={`num text-[12px] @xl:order-1 @xl:text-[13px] ${r.status === "late" ? "text-warn" : "text-ink-2"}`}
                        title={`Expected ${formatDay(r.next_expected)}`}>~{day(r.next_expected)}</span>}
                  <span className="@xl:order-3"><StatusOf s={r} /></span>
                </span>
                <span className="num order-4 col-span-2 mt-0.5 flex items-center gap-2 @xl:order-5 @xl:col-span-3 @xl:col-start-2 @xl:mt-0 @4xl:col-span-1 @4xl:col-start-5">
                  {barred(r) ? <CostBar value={r.yearly} max={max} label={`${formatSgd(r.yearly)} a year`} /> : <span className="flex-1" />}
                  <span className={`w-[4.75rem] shrink-0 text-right ${barred(r) ? "" : "text-ink-2"}`}>{formatSgd(r.yearly)}</span>
                </span>
              </li>
            )} />
          </ul>
        </div>
      )}
    </Card>
  );
}

function StoppedList({ rows, asOf }: { rows: StoppedSeries[]; asOf: string }) {
  const wide = useWide();
  const since = (r: StoppedSeries) => (r.last_seen ? ago(r.last_seen, asOf) : "");
  return (
    <Card title="Stopped" region>
      {wide ? (
        <SeriesTable head={[{ text: "Payee" }, { text: "Every" }, { text: "Typical" }, { text: "Last seen", span: 2 }, { text: "Was a year" }]}>
          <CollapsibleRows as="tr" colSpan={6} rows={rows} limit={COLLAPSED_ROWS.thin} keyOf={(r) => r.id} render={(r) => (
            <tr>
              <td className={payeeCell}><Payee name={r.name} to={seriesPath(r.id)} investing={r.investing} /></td>
              <td className={td}>{cadence(r.cadence, 12)}</td>
              <td className={td}>{money(r.amount)}</td>
              <td colSpan={2} className={td}>
                {r.last_seen ? formatDay(r.last_seen) : ""}
                {since(r) && <span className="block text-[12px] text-ink-3">{since(r)}</span>}
              </td>
              <td className={td}>{formatSgd(r.yearly)}</td>
            </tr>
          )} />
        </SeriesTable>
      ) : (
        <ul className="m-0 list-none p-0">
          <CollapsibleRows rows={rows} limit={COLLAPSED_ROWS.thin} keyOf={(r) => r.id} render={(r) => (
            <PhoneRow payee={<Payee name={r.name} to={seriesPath(r.id)} investing={r.investing} />} figure={money(r.amount)}>
              <Facts items={[cadence(r.cadence), r.last_seen && `last seen ${formatDay(r.last_seen)}${since(r) ? `, ${since(r)}` : ""}`,
                `was ${formatSgd(r.yearly)} a year`]} />
            </PhoneRow>
          )} />
        </ul>
      )}
    </Card>
  );
}

const DAY = 86_400_000;
const at = (iso: string) => {
  const p = isoParts(iso);
  return p ? Date.UTC(p.y, p.m - 1, p.d ?? 1) : NaN;
};

/**
 * One payee's payments on a line: time runs left to right, one dot per
 * payment. Every payee shares `from` and `to`, so a strip's length means the
 * same everywhere and a payee that went quiet shows as dots stopping short.
 * The dates are in the tooltips: the strip's lists them all, a dot's gives its own.
 */
export function DotStrip({ dates, from, to, label }: { dates: string[]; from: string; to: string; label: string }) {
  const [open, setOpen] = useState(false);
  const lo = at(from);
  const span = Math.max(at(to) - lo, DAY);
  const place = (iso: string) => Math.min(1, Math.max(0, (at(iso) - lo) / span));
  const all = dates.map(formatDay).join(", ");
  // a touch screen has no hover, so tapping the strip lists the dates in text under it
  return (
    <div>
      <button type="button" aria-expanded={open} aria-label={`${label}: ${all}`} title={all} onClick={() => setOpen(!open)}
        className="relative block h-5 w-full cursor-pointer rounded">
        <span aria-hidden className="absolute inset-x-2 top-1/2 h-px bg-line" />
        {dates.map((d, i) => (
          <span key={`${d}-${i}`} title={formatDay(d)} data-dot="" data-at={place(d).toFixed(4)}
            className="absolute top-1/2 grid size-4 -translate-x-1/2 -translate-y-1/2 place-items-center"
            style={{ left: `calc(0.5rem + (100% - 1rem) * ${place(d).toFixed(4)})` }}>
            <span className="size-2 rounded-full bg-ink-2 ring-2 ring-panel" />
          </span>
        ))}
      </button>
      {open && <p className="num m-0 mt-1 text-[12px] leading-snug text-ink-2">{all || "No dates"}</p>}
    </div>
  );
}

const IRREGULAR_GRID = "grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1 md:grid-cols-[minmax(0,14rem)_minmax(10rem,1fr)_7.5rem_7rem]";

/** G16: one write at a time from a list's buttons, with the reason when the record refused it. */
function useWrite() {
  const [busy, setBusy] = useState<string | null>(null);
  const [failed, setFailed] = useState<{ key: string; message: string } | null>(null);
  async function run(key: string, url: string, body: object) {
    setBusy(key);
    setFailed(null);
    try {
      await postJSON(url, JSON.stringify(body));
    } catch (e) {
      setFailed({ key, message: (e as Error).message });
    } finally {
      setBusy(null);
    }
  }
  return { busy, failed, run };
}

function IrregularList({ rows, asOf }: { rows: IrregularPayee[]; asOf: string }) {
  // one time scale for every strip: the earliest payment to the later of the latest and today's record
  const days = rows.flatMap((r) => r.dates).filter((d) => !Number.isNaN(at(d)));
  const from = days.length ? days.reduce((a, b) => (a < b ? a : b)) : asOf;
  const to = [...days, asOf].reduce((a, b) => (a > b ? a : b));
  const write = useWrite();
  return (
    <Card title="Marked recurring, no regular spacing" region>
      <ul className="m-0 list-none p-0">
        {days.length > 1 && (
          <li aria-hidden className={`${IRREGULAR_GRID} pb-1 text-[12px] text-ink-3`}>
            <span className="col-span-2 flex justify-between md:col-span-1 md:col-start-2"><span>{formatMonth(from)}</span><span>{formatMonth(to)}</span></span>
          </li>
        )}
        <CollapsibleRows rows={rows} limit={COLLAPSED_ROWS.thin} keyOf={(r) => r.payee} render={(r) => {
          return (
            <li className={`${IRREGULAR_GRID} border-t border-line py-2 text-[13px] first:border-t-0`}>
              <span className="min-w-0 md:order-1"><Payee name={r.payee} to={txnLink(r.link)} /></span>
              {/* G16: saved in the record, so it wins over a rule's recurring yes, which no rule can clear */}
              <Button size="sm" disabled={write.busy === r.payee} aria-label={`Not recurring: ${r.payee}`}
                onClick={() => void write.run(r.payee, "/api/recurring/payee", { payee: r.payee, recurring: false })}
                className="order-2 justify-self-end md:order-4">Not recurring</Button>
              {write.failed?.key === r.payee && <span role="alert" className="order-5 col-span-2 text-[12px] text-danger md:col-span-4">{write.failed.message}</span>}
              <span className="order-3 col-span-2 md:order-2 md:col-span-1">
                <DotStrip dates={r.dates} from={from} to={to} label={`${plural(r.payments, "payment")}`} />
              </span>
              <span className="num order-4 col-span-2 text-[12px] text-ink-2 md:order-3 md:col-span-1 md:text-right md:text-[13px]">
                <span className="text-ink">{plural(r.payments, "payment")}</span>
                <span className="md:hidden"> · </span>
                <span className="md:block">{money(r.total)}</span>
              </span>
            </li>
          );
        }} />
      </ul>
    </Card>
  );
}

const plural = (n: number, one: string) => `${n} ${n === 1 ? one : `${one}s`}`;

/** G16: what the owner said is not recurring, each with Undo; a series' name opens its view. */
function NotRecurringList({ rows }: { rows: NotRecurring[] }) {
  const write = useWrite();
  return (
    <Card title="Not recurring" region>
      <ul className="m-0 list-none p-0">
        <CollapsibleRows rows={rows} limit={COLLAPSED_ROWS.thin} keyOf={(r) => r.id ?? `payee:${r.payee}`} render={(r) => {
          const key = r.id ?? `payee:${r.payee}`;
          const undo = r.id ? () => write.run(key, "/api/recurring/series", { id: r.id, recurring: null })
            : () => write.run(key, "/api/recurring/payee", { payee: r.payee, recurring: null });
          return (
            <li className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-line py-2 text-[13px] first:border-t-0">
              <span className="min-w-0 flex-1">
                <Payee name={r.name} to={r.id ? seriesPath(r.id) : null} />
                <span className="block truncate text-[12px] text-ink-3">
                  <Facts items={r.id ? [cadence(r.cadence), r.category, r.amount != null && money(r.amount)] : ["every payment of this payee"]} />
                </span>
              </span>
              <Button size="sm" disabled={write.busy === key} onClick={() => void undo()}
                aria-label={`Undo not recurring: ${r.name}`}>Undo</Button>
              {write.failed?.key === key && <span role="alert" className="basis-full text-[12px] text-danger">{write.failed.message}</span>}
            </li>
          );
        }} />
      </ul>
    </Card>
  );
}

// ---------------------------------------------------------------- G16: one series' own view

const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
const BY_WEEKDAY = ["weekly", "fortnightly"];
const ONE_DAY_NONE = "twice-monthly";          // two fixed days, which one day cannot set
const SLOT_CHIP: Record<SlotStatus, { text: string; tone: StatusTone }> = { ...STATUS_CHIP, skipped: { text: "Skipped", tone: "empty" } };
const STATE_CHIP = {
  paused: { text: "Paused", tone: "neutral" },
  stopped: { text: "Stopped", tone: "neutral" },
  not_recurring: { text: "Not recurring", tone: "neutral" },
} as const;

type Draft = { amount: string; cadence: Cadence; day: string; ended: string };
const draftOf = (v: SeriesAnswer): Draft => ({
  amount: centsText(v.amount), cadence: (v.cadence || v.detected.cadence || "monthly") as Cadence,
  day: v.day == null ? "" : String(v.day), ended: v.ended_on ?? "",
});
const DRAFT_KEYS: (keyof Draft)[] = ["amount", "cadence", "day", "ended"];
/** One box holds the same value as another: an amount as cents, so "50.00" is the saved 50; text that is no amount only as itself. */
const sameBox = (k: keyof Draft, a: string, b: string) => {
  if (a === b) return true;
  if (k !== "amount") return false;
  const x = parseCents(a);
  return x != null && !Number.isNaN(x) && x === parseCents(b);
};
/**
 * `d` moved from the values `from` to `to`: a box still holding its `from` value takes the new one, a box typed in
 * keeps its text. The day and how often go together: a day of the month is no weekday, so when only one of them
 * would move and the day would then be read under the other kind of cadence, both keep what was typed.
 */
const follow = (d: Draft, from: Draft, to: Draft): Draft => {
  const out = Object.fromEntries(DRAFT_KEYS.map((k) => [k, sameBox(k, d[k], from[k]) ? to[k] : d[k]])) as Draft;
  const dayUnder = out.day === d.day ? d.cadence : to.cadence;
  return sameKind(out.cadence, dayUnder) ? out : { ...out, cadence: d.cadence, day: d.day };
};
const sameKind = (a: string, b: string) => BY_WEEKDAY.includes(a) === BY_WEEKDAY.includes(b);
const dayOptions = (c: string) => (BY_WEEKDAY.includes(c)
  ? WEEKDAYS.map((w, i) => ({ value: String(i + 1), label: w }))
  : Array.from({ length: 31 }, (_, i) => ({ value: String(i + 1), label: String(i + 1) })));

function BackLink() {
  return (
    <a href="/recurring" onClick={onLinkClick("/recurring")}
      className="inline-flex w-fit items-center gap-0.5 text-[13px] text-ink-2 no-underline hover:underline">
      <ChevronLeft size={14} aria-hidden />Recurring
    </a>
  );
}

function SeriesView({ id }: { id: string }) {
  const { data, error, loading, status } = useApi<SeriesAnswer>(`/api/recurring/series?${new URLSearchParams({ id })}`);
  if (loading && !data) return <Loading what="Loading the series" />;
  if (error || !data) {
    return (
      <div className="flex flex-col gap-3">
        <BackLink />
        <Problem message={status === 404 ? "This series is not in the record now." : error ?? "No series."} retry={status !== 404} />
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-3">
      <BackLink />
      {/* keyed by the series alone: a save of its values (here or in another tab) moves the boxes left alone
          to what was saved and keeps what is typed in the others (SeriesEditor) */}
      <SeriesEditor key={data.id} v={data} />
      <ScheduleList v={data} />
      <PaymentList v={data} />
    </div>
  );
}

/** The series' state and facts, and the compact form that edits it, at the top of its view. */
function SeriesEditor({ v }: { v: SeriesAnswer }) {
  const saved = draftOf(v);
  const [draft, setDraft] = useState<Draft>(saved);
  // G18: the saved values the boxes started from. When they change (this form's save, or another tab's), a box
  // left alone shows the new value and one typed in, while a save was on its way included, keeps what was typed
  const [base, setBase] = useState<Draft>(saved);
  // a save of the boxes on its way (what it sent) or landed (what it saved, `landedOn` the series on screen
  // then): the boxes are measured against it, so one typed back to the old value meanwhile is an edit. A write
  // has every view ask again, so the first series that differs from `landedOn` was read after the save: only
  // that one ends it, moving the boxes that still show what the save left. A series read meanwhile (an earlier
  // save's, another tab's) moves nothing.
  const latest = useRef(v);
  latest.current = v;
  const [pending, setPending] = useState<{ draft: Draft; landedOn?: SeriesAnswer } | null>(null);
  if (pending?.landedOn && v !== pending.landedOn) {
    setPending(null);
    setBase(saved);
    setDraft((d) => follow(d, pending.draft, saved));
  } else if (!pending && DRAFT_KEYS.some((k) => base[k] !== saved[k])) {
    setBase(saved);
    setDraft((d) => follow(d, base, saved));
  }
  const [saving, setSaving] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  // an amount is compared as cents, so "98.00" is the saved 98
  const against = pending?.draft ?? saved;
  const dirty = DRAFT_KEYS.some((k) => !sameBox(k, draft[k], against[k]));
  // a save of the boxes on its way holds the page until it lands: one that fails leaves the edit unsaved
  useUnsavedGuard(dirty || (!!pending && !pending.landedOn));
  const set = (patch: Partial<Draft>) => { setDraft((d) => ({ ...d, ...patch })); setProblem(null); };
  const cents = parseCents(draft.amount);
  const badAmount = cents != null && (Number.isNaN(cents) || cents <= 0);

  // `form`: a save of the boxes, after which they show what was saved (a value the record keeps as detected
  // reads back as the detected one); Not recurring and Recurring leave what is typed alone
  async function send(body: object, form = false) {
    setSaving(true);
    setProblem(null);
    const sent = draft;
    const before = pending;
    if (form) setPending({ draft: sent });
    try {
      const next = await postJSON<SeriesAnswer>("/api/recurring/series", JSON.stringify({ id: v.id, ...body }));
      // a box the save covered shows what was saved (an amount kept as detected reads back as the detected
      // one); one changed while the save was on its way keeps what was typed
      if (form) {
        const after = draftOf(next);
        setDraft((d) => follow(d, sent, after));
        setPending({ draft: after, landedOn: latest.current });
      }
    } catch (e) {
      // nothing was saved: what stood before this save (an earlier one not yet read back) stands again
      if (form) setPending(before);
      setProblem((e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  function save() {
    if (badAmount) {
      setProblem("The usual amount is dollars and cents, more than zero.");
      return;
    }
    const d = v.detected;
    // what matches what was detected is saved as "as detected", so a later run can still move it
    const day = draft.day === "" || draft.cadence === ONE_DAY_NONE ? null : Number(draft.day);
    void send({
      amount: cents == null || cents === d.amount ? null : cents,
      cadence: draft.cadence === d.cadence ? null : draft.cadence,
      day: day == null || (day === d.day && sameKind(draft.cadence, d.cadence)) ? null : day,
      ended_on: draft.ended || null,
    }, true);
  }

  // a series hidden only by its payee's own line is brought back by its own word, true
  const hidden = v.state === "not_recurring";
  const toggle = hidden ? { recurring: v.edit.recurring === false ? null : true } : { recurring: false };
  const chip = v.state === "active"
    ? v.late && <StatusChip tone="warn" icon={COMING_ICON.late}>Late</StatusChip>
    : <StatusChip tone={STATE_CHIP[v.state].tone} icon={COMING_ICON[v.state]}>{STATE_CHIP[v.state].text}</StatusChip>;
  const day = (iso: string) => formatDay(iso);
  const facts = [
    cadence(v.cadence), v.category,
    v.next_expected ? `next ~${day(v.next_expected)}` : v.ended_on ? `ended ${day(v.ended_on)}` : "",
    v.last_paid && `last paid ${day(v.last_paid)}`,
    `${formatSgd(v.yearly)} a year`,
  ];
  const field = "flex min-w-0 flex-col justify-end gap-1 text-[12px] text-ink-2";
  return (
    <Card title={v.name} region actions={
      <span className="flex flex-wrap items-center gap-2">
        {chip}
        {v.investing && <Chip icon={NEED_ICON.Investment}>Investing</Chip>}
        <Button size="sm" disabled={saving} onClick={() => void send(toggle)}>{hidden ? "Recurring" : "Not recurring"}</Button>
      </span>}>
      <p className="num m-0 text-[12px] text-ink-2"><Facts items={facts} /></p>
      <form aria-label={`Edit ${v.name}`} onSubmit={(e) => { e.preventDefault(); save(); }}
        className="mt-3 grid grid-cols-2 gap-3 md:grid-cols-4">
        <label className={field}>
          Usual amount
          <MoneyInput label="Usual amount" before="S$" value={draft.amount} invalid={badAmount}
            placeholder={centsText(v.detected.amount)} onChange={(t) => set({ amount: t })} />
        </label>
        <label className={field}>
          How often
          <Select label="How often" value={draft.cadence} placeholder="How often"
            options={(Object.keys(CADENCE) as Cadence[]).map((c) => ({ value: c, label: CADENCE[c], icon: <KindIcon icon={CADENCE_ICON[c]} size={13} /> }))}
            onChange={(c) => set({ cadence: c as Cadence, ...(sameKind(c, draft.cadence) ? {} : { day: "" }) })} />
        </label>
        {draft.cadence !== ONE_DAY_NONE && (
          <label className={field}>
            Day
            <Select label="Day" value={draft.day} placeholder="Day" options={dayOptions(draft.cadence)}
              onChange={(x) => set({ day: x })} />
          </label>
        )}
        <label className={field}>
          Ended on
          <DateField label="Ended on" value={draft.ended} clearable placeholder="Still running"
            onChange={(iso) => set({ ended: iso })} />
        </label>
      </form>
      <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
        {problem ? <p role="alert" className="m-0 text-[13px] text-danger">{problem}</p> : <span />}
        <SaveBar dirty={dirty} saving={saving} onSave={save} onDiscard={() => { setDraft(against); setProblem(null); }} />
      </div>
    </Card>
  );
}

const SCHEDULE_GRID = "grid grid-cols-[6.5rem_minmax(0,1fr)_auto] items-center gap-x-3 py-1.5 text-[13px]";

/** Every expected payment of the series, newest first, each with its status and the payment that answered it. */
function ScheduleList({ v }: { v: SeriesAnswer }) {
  return (
    <Card title="Schedule" region>
      {v.schedule.length === 0 ? <EmptyState title="No schedule yet" /> : (
        <ul className="m-0 list-none p-0">
          <CollapsibleRows rows={v.schedule} limit={COLLAPSED_ROWS.mid} keyOf={(r, i) => r.link?.key ?? `${r.expected}-${r.status}-${i}`} render={(r) => {
            const chip = <StatusChip tone={SLOT_CHIP[r.status].tone} icon={COMING_ICON[r.status]}>{SLOT_CHIP[r.status].text}</StatusChip>;
            const figure = r.paid_amount != null ? money(r.paid_amount) : r.status === "skipped" ? "" : money(v.amount);
            return (
              <li className={`${SCHEDULE_GRID} border-t border-line first:border-t-0`}>
                <span className="num text-ink-2" title={r.status === "extra" ? "The day it came" : `Expected ${formatDay(r.expected)}`}>
                  {/* an expected day is an estimate; an extra charge is dated by the day it came */}
                  {r.status === "extra" ? formatDay(r.expected) : `~${formatDay(r.expected)}`}
                </span>
                <span className="flex min-w-0 flex-wrap items-center gap-x-2">
                  {chip}
                  {r.paid_on && r.status !== "extra" && <span className="num text-[12px] text-ink-3">paid {formatDay(r.paid_on)}</span>}
                </span>
                <span className="num text-right">
                  {r.link ? <a href={txnLink(r.link)} onClick={onLinkClick(txnLink(r.link))} className="text-ink no-underline hover:underline">{figure}</a> : figure}
                </span>
              </li>
            );
          }} />
        </ul>
      )}
    </Card>
  );
}

/**
 * Every payment of the series, newest first, each opening its own rows. One outside the usual range (the
 * owner's amount when they set one) is marked, as Schedule marks it.
 */
function PaymentList({ v }: { v: SeriesAnswer }) {
  const all = txnLink(v.link);
  return (
    <Card title="Payments" region actions={
      <a href={all} onClick={onLinkClick(all)} className="text-[12px] text-ink-2 no-underline hover:underline">In Transactions</a>}>
      {v.payments.length === 0 ? <EmptyState title="No payments" /> : (
        <ul className="m-0 list-none p-0">
          <CollapsibleRows rows={v.payments} limit={COLLAPSED_ROWS.mid} keyOf={(p, i) => p.link?.key ?? `${p.date}-${p.amount}-${i}`} render={(p) => (
            <li className="flex items-center justify-between gap-3 border-t border-line py-1.5 text-[13px] first:border-t-0">
              <span className="flex items-center gap-2">
                <span className="num text-ink-2">{formatDay(p.date)}</span>
                {(p.amount < v.usual_low || p.amount > v.usual_high) && <Chip tone="info">Different amount</Chip>}
              </span>
              {p.link ? <a href={txnLink(p.link)} onClick={onLinkClick(txnLink(p.link))} className="num text-ink no-underline hover:underline">{money(p.amount)}</a>
                : <span className="num">{money(p.amount)}</span>}
            </li>
          )} />
        </ul>
      )}
    </Card>
  );
}
