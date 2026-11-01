import { useRef, useState, type KeyboardEvent, type MouseEvent } from "react";
import { Pencil, Plus, Trash2 } from "lucide-react";
import {
  Bar, BarChart, CartesianGrid, Cell, LabelList, ReferenceLine, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis, ZAxis,
} from "recharts";
import { postJSON, rowsChanged, useApi, type Country, type Relinked, type Trip, type TripList } from "../api";
import { axisSgd, formatSgd, type Cents } from "../money";
import { formatRange, isoDay } from "../dates";
import { navigate, onLinkClick } from "../router";
import { Button } from "../components/Button";
import { Card } from "../components/Card";
import { Chip } from "../components/Chip";
import { BAR_GRID, cutName, GRID, nameTick, Swatch, TipBox, TipRow, X_AXIS, Y_AXIS, Y_NAMES } from "../components/chartKit";
import { CollapsibleRows, COLLAPSED_ROWS } from "../components/CollapsibleRows";
import { CountryPicker, unknownCodes } from "../components/CountryPicker";
import { DateRangeFields } from "../components/DateField";
import { EmptyState } from "../components/EmptyState";
import { Sheet } from "../components/Sheet";
import { Loading, Problem } from "../components/States";
import { Tile, TileGrid } from "../components/Tile";
import { showToast } from "../components/Toast";
import { useWide } from "../components/useWide";
import { TRIP_TAG_ICON } from "../icons";
import { useChartView, ViewSwitch, type ViewOption } from "../components/ViewSwitch";

const input = "h-8 w-full rounded-md border border-line bg-panel px-2";
const LONG_STAY_DAYS = 60;   // the travel stat's default, until the server says otherwise

// G17: an edit carries the trip's id, which the save names it by, and the name it had, which the server checks
type Draft = { id?: number; old_name?: string; extra_keys?: string[]; name: string; start: string; end: string; countries: string[]; notes: string };
const EMPTY: Draft = { name: "", start: "", end: "", countries: [], notes: "" };

/** G13: the rows a trip counts, spending only, which is what its figures add up. */
export function tripLink(name: string): string {
  return `/transactions?${new URLSearchParams({ trip: name, spend: "1" })}`;
}

/** A trip that starts after the record's last transaction has not happened in the record yet. */
export function isPlanned(t: Pick<Trip, "start">, lastTxn?: string): boolean {
  return !!lastTxn && !!t.start && t.start > lastTxn;
}

/**
 * Short trips by cost per day, dearest first, and the long stays (over the
 * travel stat's long_stay_days) apart: a term abroad is somewhere lived in,
 * and its day rate is rent and groceries, not a holiday. A trip still to come
 * has spent nothing, so it is in neither.
 */
export function costPerDay(trips: Trip[], lastTxn?: string): { short: Trip[]; long: Trip[] } {
  const order = (a: Trip, b: Trip) => (b.per_day ?? 0) - (a.per_day ?? 0) || a.name.localeCompare(b.name);
  const priced = trips.filter((t) => t.per_day != null && !isPlanned(t, lastTxn));
  return { short: priced.filter((t) => !t.long_stay).sort(order), long: priced.filter((t) => t.long_stay).sort(order) };
}

/** What the short trips cost a day on average: all their spending over all their days. */
export function averagePerDay(short: readonly Trip[]): Cents | null {
  const days = short.reduce((n, t) => n + (t.days ?? 0), 0);
  return days > 0 ? Math.round(short.reduce((n, t) => n + t.spend_sgd, 0) / days) : null;
}

export type TripSummary = {
  count: number; planned: number; longStays: number; total: Cents; shortTrips: number; perDay: Cents | null;
  thisYear: Cents; lastYear: Cents; year: number;
};

/** The headline figures: counts, the total, the short-trip day rate, and spending by the year a trip started. */
export function summarise(trips: Trip[], lastTxn?: string, year = Number(isoDay().slice(0, 4))): TripSummary {
  const { short } = costPerDay(trips, lastTxn);
  const spentIn = (y: number) => trips.filter((t) => Number(t.start.slice(0, 4)) === y).reduce((n, t) => n + t.spend_sgd, 0);
  return {
    count: trips.length,
    planned: trips.filter((t) => isPlanned(t, lastTxn)).length,
    longStays: trips.filter((t) => t.long_stay).length,
    total: trips.reduce((n, t) => n + t.spend_sgd, 0),
    shortTrips: short.length,
    perDay: averagePerDay(short),
    thisYear: spentIn(year), lastYear: spentIn(year - 1), year,
  };
}

/** A trip's countries as flags, each code on hover; a code with no flag shows as itself. */
export function Flags({ trip }: { trip: Pick<Trip, "countries" | "flags"> }) {
  if (!trip.countries.length) return null;
  return (
    <span className="inline-flex shrink-0 gap-0.5">
      {trip.countries.map((code, i) => {
        const f = trip.flags?.[i];
        return f
          ? <span key={code} role="img" aria-label={code} title={code} className="cursor-default">{f}</span>
          : <span key={code} title={code} className="text-[11px] text-ink-3">{code}</span>;
      })}
    </span>
  );
}

const flagText = (t: Trip) => (t.flags ?? []).filter(Boolean).join("");
const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

// A recorded trip groups past spending: every row dated inside it (on a
// wallet, or abroad) is counted on it. G13: saving it moves those rows at
// once, as a load would place them, and the toast's Undo moves them back.
export function Trips() {
  const { data, error, loading } = useApi<TripList>("/api/trips");
  const [draft, setDraft] = useState<Draft | null>(null);
  // G18: the draft as the form opened, so the Sheet knows whether anything was typed
  const [base, setBase] = useState<Draft | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [view, setView] = useChartView<TripView>("trips", TRIP_VIEWS.map((o) => o.value), "cards");
  // G18: which opening of the form is on screen, so a save that answers after the Sheet was closed
  // (or opened again on another trip) neither closes the new form nor drops its error out of sight
  const opening = useRef(0);
  const open = (d: Draft) => { opening.current += 1; setDraft(d); setBase(d); setErr(null); };
  const close = () => { opening.current += 1; setDraft(null); setBase(null); setErr(null); };
  const dirty = !!draft && !!base && JSON.stringify(draft) !== JSON.stringify(base);
  // the country list is asked for only while the form is open
  const countryList = useApi<{ countries: Country[] }>(draft ? "/api/countries" : null);
  const countries = countryList.data?.countries ?? null;
  const unknown = draft ? unknownCodes(draft.countries, countries) : [];
  // a code can only be checked against the list, so Save waits for it
  const cannotSave = !countries || unknown.length > 0;

  async function save() {
    if (!draft || cannotSave) return;
    if (!draft.start) {
      setErr("Pick the first day, like 3 Oct 2026.");
      return;
    }
    const mine = opening.current;
    try {
      const done = await postJSON<{ name: string; relinked?: Relinked; undo?: unknown }>("/api/trips", JSON.stringify({
        name: draft.name.trim(), start: draft.start, end: draft.end || draft.start, countries: draft.countries,
        notes: draft.notes, id: draft.id, old_name: draft.old_name, extra_keys: draft.extra_keys ?? [],
      }));
      if (opening.current === mine) close();
      // G13: the rows moved already; Undo takes the trip and the rows back together
      showToast({
        message: `Trip saved, ${rowsChanged(done.relinked)}.`,
        action: done.undo ? {
          label: "Undo",
          onClick: () => undoWith("/api/trips/undo", { undo: done.undo }, "Trip save undone"),
        } : undefined,
      });
    } catch (e) {
      const message = (e as Error).message;
      // the form it belongs to is gone: say so where it can be seen
      if (opening.current === mine) setErr(message);
      else showToast({ tone: "error", message: `${draft.name.trim() || "The trip"} was not saved: ${message}` });
    }
  }

  async function undoWith(url: string, body: unknown, said: string): Promise<boolean> {
    try {
      const r = await postJSON<{ relinked?: Relinked }>(url, JSON.stringify(body));
      showToast({ message: `${said}, ${rowsChanged(r.relinked, true)}.` });
      return true;
    } catch (e) {
      showToast({ tone: "error", message: (e as Error).message });
      return false;
    }
  }

  // the server answers with what puts the trip back, with the rows it counted
  async function remove({ id, name }: Trip) {
    try {
      const done = await postJSON<{ restore?: { txn_keys?: string[] } }>("/api/trips/remove", JSON.stringify({ id, name }));
      setErr(null);
      showToast({
        message: `Removed ${name}, ${rowsChanged({ changed: done.restore?.txn_keys?.length ?? 0 })}.`,
        action: done.restore ? {
          label: "Undo",
          onClick: () => undoWith("/api/trips/restore", done.restore, `Put ${name} back`),
        } : undefined,
      });
    } catch (e) {
      showToast({ tone: "error", message: (e as Error).message });
    }
  }

  const edit = (t: Trip) => open({
    id: t.id, old_name: t.name, extra_keys: t.extra_keys ?? [], name: t.name, start: t.start, end: t.end, countries: [...t.countries], notes: t.notes ?? "",
  });
  const last = data?.last_txn;

  return (
    <div className="flex flex-col gap-3">
      {data && data.trips.length > 0 && <SummaryTiles trips={data.trips} last={last} />}
      <Card title="Your trips" actions={
        <div className="flex flex-wrap items-center gap-2">
          {data && data.trips.length > 0 && <ViewSwitch label="View" value={view} options={TRIP_VIEWS} onChange={setView} />}
          <Button onClick={() => open({ ...EMPTY })}>
            <Plus size={14} aria-hidden /> Add a trip
          </Button>
        </div>}>
        {loading && <Loading what="Loading trips" />}
        {error && <Problem message={error} retry />}
        {data && data.trips.length === 0 && <EmptyState title="No trips recorded yet" />}
        {data && data.trips.length > 0 && (view === "bubbles"
          ? <TripBubbles trips={data.trips} last={last} />
          : <TripCards trips={data.trips} last={last} onEdit={edit} onRemove={(t) => void remove(t)} />)}
      </Card>
      {data && data.trips.length > 0 && <CostPerDayCard trips={data.trips} last={last} limit={data.long_stay_days ?? LONG_STAY_DAYS} />}
      {/* G18: adding and editing a trip happen in the one modal; Escape, the backdrop or the close button ask first when something was typed */}
      <Sheet open={!!draft} title={draft?.old_name ? `Edit ${draft.old_name}` : "Add a trip"} dirty={dirty} onClose={close}
        footer={
          <div className="flex justify-end gap-2">
            <Button onClick={close}>Cancel</Button>
            <Button variant="primary" type="submit" form="trip-form" disabled={cannotSave}>Save trip</Button>
          </div>}>
        {draft && (
          <form id="trip-form" onSubmit={(e) => { e.preventDefault(); void save(); }}
            className="grid grid-cols-1 gap-2 text-[13px] sm:grid-cols-2">
            {err && <p className="m-0 text-warn sm:col-span-2" role="alert">{err}</p>}
            <label className="flex flex-col gap-1 sm:col-span-2">Name
              <input required value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} className={input} />
            </label>
            <DateRangeFields start={draft.start} end={draft.end} startLabel="First day" endLabel="Last day" startRequired
              onChange={(start, end) => setDraft({ ...draft, start, end })} />
            <div className="sm:col-span-2">
              <CountryPicker value={draft.countries} countries={countries} onChange={(c) => setDraft({ ...draft, countries: c })} />
              {countryList.error && <p className="m-0 mt-1 text-warn" role="alert">The country list did not load: {countryList.error}</p>}
            </div>
            <label className="flex flex-col gap-1 sm:col-span-2">Notes
              <input value={draft.notes} onChange={(e) => setDraft({ ...draft, notes: e.target.value })} className={input} />
            </label>
          </form>
        )}
      </Sheet>
    </div>
  );
}

type TripView = "cards" | "bubbles";
const TRIP_VIEWS: readonly ViewOption<TripView>[] = [{ value: "cards", text: "Cards" }, { value: "bubbles", text: "Bubbles" }];

export type YearGroup = { year: string; trips: Trip[]; total: Cents };

/** Trips by the year they start in, newest year first and, inside a year, newest trip first. */
export function groupByYear(trips: readonly Trip[]): YearGroup[] {
  const byYear = new Map<string, Trip[]>();
  for (const t of [...trips].sort((a, b) => b.start.localeCompare(a.start) || a.name.localeCompare(b.name))) {
    const year = t.start.slice(0, 4) || "Undated";
    byYear.set(year, [...(byYear.get(year) ?? []), t]);
  }
  return [...byYear].map(([year, list]) => ({ year, trips: list, total: list.reduce((n, t) => n + t.spend_sgd, 0) }));
}

// a year's header and its cards are the rows of one list, so the list can show the newest year and fold the rest
type Item = { kind: "year"; group: YearGroup } | { kind: "trip"; trip: Trip };
const itemsOf = (groups: YearGroup[]): Item[] =>
  groups.flatMap((g): Item[] => [{ kind: "year", group: g }, ...g.trips.map((trip): Item => ({ kind: "trip", trip }))]);

const ICON_BUTTON = "grid size-10 place-items-center rounded-md text-ink-2 hover:bg-hover md:size-8";

/** Cards grouped by year, newest first. Only the newest year shows at first, so the chart under it stays near the top. */
function TripCards({ trips, last, onEdit, onRemove }: { trips: Trip[]; last?: string; onEdit: (t: Trip) => void; onRemove: (t: Trip) => void }) {
  const groups = groupByYear(trips);
  const items = itemsOf(groups);
  // header and cards of the newest year, then everything else folds
  const limit = groups.length > 1 ? 1 + groups[0].trips.length : items.length;
  return (
    <div className="@container">
      <ul className="m-0 grid list-none grid-cols-1 gap-3 p-0 @xl:grid-cols-2 @4xl:grid-cols-3">
        <CollapsibleRows rows={items} limit={limit} as="li" allLabel={`Show all ${trips.length} trips`} toggleClass="col-span-full"
          keyOf={(it) => (it.kind === "year" ? `y-${it.group.year}` : `t-${it.trip.id}`)}
          render={(it) => it.kind === "year"
            ? <YearHeader group={it.group} />
            : <TripCard t={it.trip} last={last} onEdit={onEdit} onRemove={onRemove} />} />
      </ul>
    </div>
  );
}

function YearHeader({ group }: { group: YearGroup }) {
  return (
    <li className="col-span-full flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5 border-b border-line pb-1.5 [&:not(:first-child)]:mt-2">
      <h3 className="num m-0 text-[14px] font-semibold">{group.year}</h3>
      <span className="num text-[12px] text-ink-2">{plural(group.trips.length, "trip")} · {formatSgd(group.total)}</span>
    </li>
  );
}

function TripCard({ t, last, onEdit, onRemove }: { t: Trip; last?: string; onEdit: (t: Trip) => void; onRemove: (t: Trip) => void }) {
  const planned = isPlanned(t, last);
  // a planned trip has spent nothing, unless payments were linked to it: those are counted in the totals, so they show
  const recorded = !planned || t.spend_sgd !== 0 || t.rows > 0;
  const to = tripLink(t.name);
  // the whole card opens the trip's transactions, except its own buttons and link
  const open = (e: MouseEvent<HTMLElement>) => {
    if ((e.target as HTMLElement).closest("button, a, input")) return;
    navigate(to);
  };
  const when = [formatRange(t.start, t.end), t.days != null ? plural(t.days, "day") : ""].filter(Boolean).join(" · ");
  const cats = (t.top_categories ?? []).slice(0, 3);
  return (
    <li onClick={open} className="flex min-w-0 cursor-pointer flex-col gap-1.5 rounded-lg border border-line p-3 text-[13px] hover:bg-hover">
      <div className="flex items-start justify-between gap-2">
        <span className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
          <Flags trip={t} />
          <a href={to} onClick={onLinkClick(to)} className="min-w-0 truncate text-[14px] font-medium text-ink no-underline hover:underline">{t.name}</a>
          {t.long_stay && <Chip icon={TRIP_TAG_ICON.long_stay}>Long stay</Chip>}
          {planned && <Chip tone="info" icon={TRIP_TAG_ICON.planned}>Planned</Chip>}
        </span>
        <span className="-my-2 -mr-2 flex shrink-0">
          <button type="button" aria-label={`Edit ${t.name}`} onClick={() => onEdit(t)} className={ICON_BUTTON}><Pencil size={14} /></button>
          <button type="button" aria-label={`Remove ${t.name}`} onClick={() => onRemove(t)} className={ICON_BUTTON}><Trash2 size={14} /></button>
        </span>
      </div>
      <div className="num text-[12px] text-ink-2">{when}</div>
      {recorded && (
        <div className="num flex items-baseline justify-between gap-3">
          <span className="text-[18px] font-semibold leading-tight">{formatSgd(t.spend_sgd)}</span>
          {t.per_day != null && <span className="text-[12px] text-ink-2">{formatSgd(t.per_day)} a day</span>}
        </div>
      )}
      {recorded && cats.length > 0 && (
        <ul aria-label={`${t.name} top categories`} className="m-0 flex list-none flex-col gap-0.5 border-t border-line p-0 pt-1.5 text-[12px] text-ink-2">
          {cats.map((c) => (
            <li key={c.name} className="num flex justify-between gap-3"><span className="min-w-0 truncate">{c.name}</span><span className="shrink-0">{formatSgd(c.spend)}</span></li>
          ))}
        </ul>
      )}
      {t.notes ? <p className="m-0 truncate text-[12px] text-ink-3">{t.notes}</p> : null}
    </li>
  );
}

/** The headline figures over the cards: how many trips, what they cost, the day rate, and this year against last. */
function SummaryTiles({ trips, last }: { trips: Trip[]; last?: string }) {
  const s = summarise(trips, last);
  const kinds = [s.planned ? `${s.planned} planned` : "", s.longStays ? plural(s.longStays, "long stay") : ""].filter(Boolean);
  return (
    <TileGrid cols={4} label="Trip totals">
      <Tile label="Trips" value={String(s.count)} note={kinds.join(", ") || undefined} />
      <Tile label="Spent on trips" value={formatSgd(s.total)} />
      <Tile label="Average a day" value={s.perDay != null ? formatSgd(s.perDay) : "None yet"}
        note={s.perDay != null ? `over ${plural(s.shortTrips, "short trip")}` : undefined} />
      <Tile label={`Trips in ${s.year}`} value={formatSgd(s.thisYear)}
        note={s.lastYear ? `${formatSgd(s.lastYear)} in ${s.year - 1}` : `nothing in ${s.year - 1}`} />
    </TileGrid>
  );
}

function CostPerDayCard({ trips, limit, last }: { trips: Trip[]; limit: number; last?: string }) {
  const { short, long } = costPerDay(trips, last);
  return (
    <Card title="Cost per day">
      {short.length > 0 ? <CostPerDayChart trips={short} /> : <EmptyState title="No short trips yet" />}
      {long.length > 0 && (
        <div className="mt-3 border-t border-line pt-2">
          <h3 className="m-0 mb-1 text-[13px] font-semibold">Long stays <span className="font-normal text-ink-3">over {limit} days</span></h3>
          <ul className="m-0 list-none p-0" aria-label="Long stays">
            <CollapsibleRows rows={long} limit={COLLAPSED_ROWS.thin} keyOf={(t) => String(t.id)} render={(t) => (
              <li>
                <a href={tripLink(t.name)} onClick={onLinkClick(tripLink(t.name))}
                  className="num grid grid-cols-[minmax(0,1fr)_auto_auto_auto] items-center gap-3 py-1.5 text-[13px] text-ink no-underline hover:bg-hover">
                  <span className="flex min-w-0 items-center gap-2"><Flags trip={t} /><span className="truncate">{t.name}</span></span>
                  <span className="text-right text-ink-3">{t.days} days</span>
                  <span className="text-right">{formatSgd(t.spend_sgd)}</span>
                  <span className="w-24 text-right text-ink-2">{formatSgd(t.per_day ?? 0)} a day</span>
                </a>
              </li>
            )} />
          </ul>
        </div>
      )}
    </Card>
  );
}

type Row = Trip & { label: string };

// A bar well over the average is a dearer trip than most: the warn token marks
// it (never red; it is not an error), and the key says how far over in words.
// Over the line alone marks about half of any list, so it takes a quarter more.
export const DEAR_PERCENT = 25;
/** A day rate at least DEAR_PERCENT above the average, compared in whole cents. */
export const isDear = (perDay: number | null | undefined, avg: Cents | null): boolean =>
  // a quarter above an average that is zero or below (refunds) means nothing, so nothing is marked
  avg != null && avg > 0 && perDay != null && perDay * 100 >= avg * (100 + DEAR_PERCENT);
const BAR = "var(--c1)";
const OVER = "var(--warn)";

/**
 * One bar per short trip, dearest day first; hover for the figures. G13: a bar is a rate a day, spend
 * over days, which is not a list of rows, so it opens nothing; the trip's rows are its table row's link.
 */
function CostPerDayChart({ trips }: { trips: Trip[] }) {
  const wide = useWide();
  const avg = averagePerDay(trips);
  const showAvg = avg != null && trips.length > 1;
  // a phone has no room for a 210px name column: shorter names leave the bars some width
  const cut = wide ? 26 : 12;
  const rows: Row[] = trips.map((t) => {
    const name = cutName(t.name, cut);
    return { ...t, label: flagText(t) ? `${flagText(t)} ${name}` : name };
  });
  const over = (t: Trip) => showAvg && isDear(t.per_day, avg);
  return (
    <>
      {showAvg && (
        <ul aria-label="Chart key" className="m-0 mb-2 flex list-none flex-wrap gap-x-4 gap-y-1 p-0 text-[12px] text-ink-2">
          <li className="flex items-center gap-1.5"><Swatch colour={OVER} />{DEAR_PERCENT}% or more above the average</li>
          <li className="flex items-center gap-1.5"><Swatch colour={BAR} />Less than that</li>
          <li className="num flex items-center gap-1.5"><Swatch colour="var(--ink-3)" dashed line />Average {formatSgd(avg)} a day</li>
        </ul>
      )}
      <ResponsiveContainer width="100%" height={Math.max(120, 28 * rows.length + 40)}>
        <BarChart data={rows} layout="vertical" margin={{ top: 4, right: wide ? 56 : 48, bottom: 4, left: 0 }}>
          <CartesianGrid {...BAR_GRID} />
          <XAxis type="number" {...X_AXIS} tickFormatter={axisSgd} />
          {/* one line per name: Recharts' own tick wraps a long one onto the next bar */}
          <YAxis type="category" dataKey="label" {...Y_NAMES} width={wide ? 210 : 112} tick={nameTick()} />
          <Tooltip cursor={{ fill: "var(--hover)" }} content={({ active, payload }) => {
            const t = active ? (payload?.[0]?.payload as Row | undefined) : undefined;
            return t ? <TripTip t={t} avg={showAvg ? avg : null} /> : null;
          }} />
          <Bar dataKey="per_day" name="A day" fill={BAR} radius={2} maxBarSize={18} isAnimationActive={false}>
            {rows.map((t) => <Cell key={t.name} fill={over(t) ? OVER : BAR} />)}
            <LabelList dataKey="per_day" position="right" className="num" fontSize={11} fill="var(--ink-2)"
              formatter={(v: number) => formatSgd(v)} />
          </Bar>
          {showAvg && <ReferenceLine x={avg} stroke="var(--ink-3)" strokeWidth={1.5} strokeDasharray="4 3" ifOverflow="extendDomain" />}
        </BarChart>
      </ResponsiveContainer>
    </>
  );
}

function TripTip({ t, avg }: { t: Trip; avg: Cents | null }) {
  // G18: through the chart kit's hover card, whose width keeps a long trip name on a phone screen
  const line = (label: string, value: Cents | string) => <TipRow bare keepZero name={label} value={value} />;
  return (
    <TipBox heading={`${flagText(t)} ${t.name}`}>
      {line("A day", t.per_day ?? 0)}
      {avg != null && line("Average trip", avg)}
      {line("Total", t.spend_sgd)}
      {line("Days", String(t.days ?? ""))}
      {!!t.start && line("Dates", formatRange(t.start, t.end || t.start))}
      {line("Transactions", String(t.rows))}
      {!!t.top_categories?.length && (
        <div className="mt-1 border-t border-line pt-1">
          {t.top_categories.map((c) => <TipRow key={c.name} bare keepZero name={c.name} value={c.spend} />)}
        </div>
      )}
    </TipBox>
  );
}

// ------------------------------------------------------------ G18: trips as bubbles
//
// One row per year, newest at the top; across, the day of the year the trip
// starts; each circle's area is what the trip cost, so a trip that cost four
// times another is twice as wide. Recharts' ZAxis range is an area in square
// pixels, and its domain starts at nothing, so area stays in proportion to spend.
// Long stays are a chart of their own under the trips, with a scale of their own.

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
// the day of a common year each month starts on; a leap year is a day out after February, which no eye can see
const MONTH_DAYS = [1, 32, 60, 91, 121, 152, 182, 213, 244, 274, 305, 335];
/** The radius of the dearest trip's circle; every other circle's area is in proportion to it. */
const MAX_RADIUS = 44;
const MAX_RADIUS_PHONE = 36;
/** Below this a bubble is too small to hold a flag. */
const FLAG_RADIUS = 12;
/** The least a recorded trip is drawn at, so a few dollars still shows as a dot. */
const MIN_RADIUS = 3;
/** A planned trip with nothing spent: a small outline, the same size for all. */
const PLANNED_RADIUS = 5;
/** However small a bubble is drawn, the pointer can find it within this radius. */
const HIT_RADIUS = 9;

/** The day of its year an ISO day falls on, 1 Jan being 1. */
export function dayOfYear(iso: string): number {
  const [y, m, d] = iso.split("-").map(Number);
  return Math.round((Date.UTC(y, m - 1, d) - Date.UTC(y, 0, 1)) / 86_400_000) + 1;
}

export type Bubble = Trip & {
  /** the day of the year the trip starts */
  x: number;
  /** the year it starts */
  year: number;
  /** its year's row: 0 for the oldest year with a trip, counting only years that have one, so empty years take no room */
  row: number;
  /** what the circle's area stands for: its spending, nothing below zero */
  area: Cents;
  dear: boolean;
  /** a planned trip that has spent nothing, drawn as an outline that opens nothing */
  empty: boolean;
};

/**
 * The trips as bubbles, biggest first so the small ones draw on top and stay in reach. A trip is dear
 * by the cost-per-day chart's own rule: a short, priced trip at least DEAR_PERCENT above their average.
 */
export function bubblesOf(trips: readonly Trip[], lastTxn?: string): Bubble[] {
  const { short } = costPerDay([...trips], lastTxn);
  const avg = short.length > 1 ? averagePerDay(short) : null;
  const shortOnes = new Set(short);
  const dated = trips.filter((t) => /^\d{4}-\d{2}-\d{2}$/.test(t.start));
  const yearOf = (t: Trip) => Number(t.start.slice(0, 4));
  const years = [...new Set(dated.map(yearOf))].sort((a, b) => a - b);
  return dated
    .map((t) => ({
      ...t, x: dayOfYear(t.start), year: yearOf(t), row: years.indexOf(yearOf(t)), area: Math.max(t.spend_sgd, 0),
      dear: shortOnes.has(t) && isDear(t.per_day, avg),
      empty: isPlanned(t, lastTxn) && t.spend_sgd === 0 && t.rows === 0,
    }))
    .sort((a, b) => Number(a.empty) - Number(b.empty) || b.area - a.area || a.start.localeCompare(b.start));
}

/** What a bubble says to a screen reader: the hover card's figures in one line. */
const bubbleLabel = (b: Bubble) => [
  b.name, b.empty ? "planned" : formatSgd(b.spend_sgd), b.days != null ? plural(b.days, "day") : "",
  !b.empty && b.per_day != null ? `${formatSgd(b.per_day)} a day` : "", formatRange(b.start, b.end || b.start),
].filter(Boolean).join(", ");

function TripBubbles({ trips, last }: { trips: Trip[]; last?: string }) {
  // G18: long stays apart, as the cost-per-day card has them since G13: a term abroad costs many
  // holidays over, so on one scale it would shrink every short trip to a dot. Each part has its own scale.
  const short = bubblesOf(trips.filter((t) => !t.long_stay), last);
  const long = bubblesOf(trips.filter((t) => t.long_stay), last);
  if (!short.length && !long.length) return <EmptyState title="No dated trips yet" />;
  return (
    <>
      {short.length > 0 && <BubbleChart points={short} label="Trips as bubbles" />}
      {long.length > 0 && (
        <div className={short.length ? "mt-3 border-t border-line pt-2" : ""}>
          <h3 className="m-0 mb-1 text-[13px] font-semibold">Long stays</h3>
          <BubbleChart points={long} label="Long stays as bubbles" />
        </div>
      )}
      <ul aria-label="Bubble key" className="m-0 mt-2 flex list-none flex-wrap gap-x-4 gap-y-1 p-0 text-[12px] text-ink-2">
        <li className="flex items-center gap-1.5"><Swatch colour={BAR} dot />Circle size is what the trip cost</li>
        {short.some((p) => p.dear) && (
          <li className="flex items-center gap-1.5"><Swatch colour={OVER} dot />{DEAR_PERCENT}% or more above the average a day</li>
        )}
        {[...short, ...long].some((p) => p.empty) && (
          <li className="flex items-center gap-1.5"><Swatch colour="var(--ink-3)" ring />Planned, nothing spent yet</li>
        )}
      </ul>
    </>
  );
}

/**
 * One row per year that has one of `points`, on a scale of their own: the dearest of them is MAX_RADIUS.
 * A row is shorter than that circle, so two big ones in neighbouring years may touch, and the edges
 * leave room for the top and bottom rows' circles. A phone gets a little less and every other month.
 */
function BubbleChart({ points, label }: { points: Bubble[]; label: string }) {
  const wide = useWide();
  const years = [...new Set(points.map((p) => p.year))].sort((a, b) => a - b);
  const maxR = wide ? MAX_RADIUS : MAX_RADIUS_PHONE;
  const rowH = Math.round(1.5 * maxR) + 8;
  const edge = Math.max(4, maxR - rowH / 2 + 4);
  const most = Math.max(1, ...points.map((p) => p.area));
  return (
    <div role="group" aria-label={label}>
      <ResponsiveContainer width="100%" height={years.length * rowH + 2 * edge + 28}>
        <ScatterChart margin={{ top: edge, right: 8, bottom: edge, left: 0 }}>
          <CartesianGrid {...GRID} />
          <XAxis type="number" dataKey="x" {...X_AXIS} domain={[1, 366]} padding={{ left: maxR, right: maxR }}
            ticks={wide ? MONTH_DAYS : MONTH_DAYS.filter((_, i) => i % 2 === 0)} interval={0}
            tickFormatter={(v: number) => MONTHS[MONTH_DAYS.indexOf(v)] ?? ""} />
          {/* G18: a row per year that has a trip, placed by its index, so a gap of empty years takes no height */}
          <YAxis type="number" dataKey="row" {...Y_AXIS} width={40} domain={[-0.5, years.length - 0.5]}
            ticks={years.map((_, i) => i)} interval={0} allowDecimals={false} tickFormatter={(i: number) => String(years[i] ?? "")} />
          <ZAxis type="number" dataKey="area" domain={[0, most]} range={[0, Math.PI * maxR * maxR]} />
          <Tooltip cursor={false} isAnimationActive={false} content={({ active, payload }) => {
            const b = active ? (payload?.[0]?.payload as Bubble | undefined) : undefined;
            return b ? <BubbleTip b={b} /> : null;
          }} />
          <Scatter data={points} isAnimationActive={false} shape={(p: unknown) => <BubbleShape {...(p as BubbleShapeProps)} />} />
        </ScatterChart>
      </ResponsiveContainer>
    </div>
  );
}

type BubbleShapeProps = { cx?: number; cy?: number; width?: number; payload?: Bubble };

/** One trip: its circle (Recharts' `width` is the ZAxis diameter) and the flags inside when there is room. */
function BubbleShape({ cx = 0, cy = 0, width = 0, payload: b }: BubbleShapeProps) {
  if (!b) return null;
  if (b.empty) {
    // a trip still to come with nothing spent: there are no rows to open
    return (
      <g role="img" aria-label={bubbleLabel(b)} data-trip={b.name}>
        <circle cx={cx} cy={cy} r={PLANNED_RADIUS} fill="var(--panel)" stroke="var(--ink-3)" strokeWidth={1.5} />
      </g>
    );
  }
  const r = Math.max(width / 2, MIN_RADIUS);
  const go = () => navigate(tripLink(b.name));
  const onKey = (e: KeyboardEvent<SVGGElement>) => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); }
  };
  const flags = (b.flags ?? []).filter(Boolean);
  const size = Math.min(16, Math.max(11, Math.round(r * 0.6)));
  // as many flags as fit across the circle, at least one once it is big enough to hold any
  const fit = r >= FLAG_RADIUS ? flags.slice(0, Math.max(1, Math.floor((1.6 * r) / (size * 1.3)))) : [];
  return (
    <g role="link" tabIndex={0} aria-label={bubbleLabel(b)} data-trip={b.name} onClick={go} onKeyDown={onKey}
      className="group cursor-pointer outline-none">
      {/* a small trip is still easy to point at; the ring shows which one the pointer or the focus is on */}
      <circle cx={cx} cy={cy} r={Math.max(r, HIT_RADIUS)} fill="transparent" />
      <circle cx={cx} cy={cy} r={r + 4} fill="none" stroke="var(--ink-2)" strokeWidth={1.5} className="invisible group-hover:visible" />
      <circle cx={cx} cy={cy} r={r + 4} fill="none" stroke="var(--focus)" strokeWidth={2} className="invisible group-focus-visible:visible" />
      <circle data-bubble="" cx={cx} cy={cy} r={r} fill={b.dear ? OVER : BAR} stroke="var(--panel)" strokeWidth={1.5} />
      {fit.length > 0 && (
        <text x={cx} y={cy} dy="0.35em" textAnchor="middle" fontSize={size} aria-hidden className="pointer-events-none select-none">{fit.join("")}</text>
      )}
    </g>
  );
}

function BubbleTip({ b }: { b: Bubble }) {
  const line = (label: string, value: Cents | string) => <TipRow bare keepZero name={label} value={value} />;
  return (
    <TipBox heading={`${flagText(b)} ${b.name}`.trim()} total={b.empty ? "Planned" : b.spend_sgd}>
      {b.days != null && line("Days", String(b.days))}
      {!b.empty && b.per_day != null && line("A day", b.per_day)}
      {line("Dates", formatRange(b.start, b.end || b.start))}
    </TipBox>
  );
}
