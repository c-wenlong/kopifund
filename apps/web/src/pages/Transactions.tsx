import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { EyeOff, Pencil, Plus, X, type LucideIcon } from "lucide-react";
import {
  postJSON, rowsChanged, useApi, type GroupRow, type GroupRows, type PinSet, type Relinked, type RulePreview, type RuleSavedEdit,
  type Txn, type TxnDay, type TxnPage,
} from "../api";
import { formatAmount, formatSgd } from "../money";
import { BankMark } from "../components/BankMark";
import { Button, buttonClass } from "../components/Button";
import { Combobox } from "../components/Combobox";
import { EmptyState } from "../components/EmptyState";
import { Pager, usePager } from "../components/Pager";
import { PinForm, pinnedText, savePin, undoRuleWrite, type PinDraft } from "../components/PinRow";
import { draftParams, ruleOf, type Draft } from "../components/RuleEditor";
import { Select } from "../components/Select";
import { Loading, Problem } from "../components/States";
import { HEAD_ROW, ROW, TABLE_CARD, TH } from "../components/tableKit";
import { Tile } from "../components/Tile";
import { showToast } from "../components/Toast";
import { useWide } from "../components/useWide";
import { formatDate, formatDay, formatMonth, formatRange, formatWeekday } from "../dates";
import { onLinkClick } from "../router";
import { useUrlParams } from "../useUrlParams";
import type { GroupKind, NeedClass } from "../types.gen";
import { KIND_ICON, KindIcon, NEED_ICON, EYE_ICON, SCAM_FLAG_ICON, SCAM_ICON, TRIP_ICON } from "../icons";

// G13: transfers (wallet top-ups, moves between your own accounts) are hidden
// unless the browser has saved the opposite. The saved choice is per browser;
// the address carries it too (hide_transfers=1 or 0) and wins when it is there,
// so a shared link shows the view it was copied from.
export const HIDE_TRANSFERS_KEY = "kopifund.transactions.hideTransfers";

function readHide() {
  try {
    return window.localStorage.getItem(HIDE_TRANSFERS_KEY) !== "0";
  } catch {
    return true;
  }
}

// true when the choice was kept; false when storage is blocked, so the choice has to ride in the address
function saveHide(on: boolean): boolean {
  try {
    window.localStorage.setItem(HIDE_TRANSFERS_KEY, on ? "1" : "0");
  } catch {
    // storage can be blocked
  }
  return readHide() === on;
}

// G13: the filters a link from a chart or a trip carries, which share one chip and one Clear
const NO_SCOPE: Record<string, null> = Object.fromEntries(
  ["from", "to", "home", "spend", "trip", "income", "transfer", "need", "bucket", "categories", "not_categories", "not_payees", "external"]
    .map((k) => [k, null]));
const NEED_WORDS: Record<string, string> = { Need: "needs", Want: "wants", Investment: "investing", Savings: "saving" };

type Scope = {
  from: string; to: string; home: boolean; spend: boolean; trip: string; income: boolean; transfer: boolean; need: string;
  bucket: string; categories: string; notCategories: string; notPayees: string; external: boolean; standIn: boolean;
};

/** A filter sent as a JSON list of names, as its words; the raw text if it is not a list. */
function names(raw: string): string {
  try {
    const list: unknown = JSON.parse(raw);
    return Array.isArray(list) ? list.join(", ") : raw;
  } catch {
    return raw;
  }
}

/** What the chip says, in order: the trip, the rows counted, the range, the places. Empty when no such filter is on. */
export function scopeChip(s: Scope): string[] {
  return scopeParts(s).map((p) => p.text);
}

/** G18: the chip's parts, each with the icons of the kinds it names (a trip, spending, a need class), none for the rest. */
export function scopeParts(s: Scope): { text: string; icons: LucideIcon[] }[] {
  const kinds = ([s.spend && "spend", s.income && "income", s.transfer && "transfer"].filter(Boolean)) as GroupKind[];
  const needs = s.need.split(",").filter(Boolean);
  const kindWords: Record<GroupKind, string> = { spend: "spending", income: "income", transfer: "transfers" };
  const need = needs.map((n) => NEED_WORDS[n] ?? n.toLowerCase());
  // a need class narrows the bucket it sits in, so it names the rows: "Needs only" is spending
  const counted = (need.length ? need : kinds.map((k) => kindWords[k])).join(" and ");
  const countedIcons = needs.length
    ? needs.map((n) => NEED_ICON[n as NeedClass]).filter(Boolean)
    : kinds.map((k) => KIND_ICON[k]);
  const parts: [string | false | null | undefined, LucideIcon[]][] = [
    [s.trip && `Trip: ${s.trip}`, [TRIP_ICON]],
    [counted && `${counted.charAt(0).toUpperCase()}${counted.slice(1)}${kinds.length || need.length ? " only" : ""}`, countedIcons],
    [s.bucket && `Bucket: ${s.bucket}`, []],
    [s.categories && `Categories: ${names(s.categories)}`, []],
    [s.notCategories && `Not categories: ${names(s.notCategories)}`, []],
    [s.notPayees && `Not payees: ${names(s.notPayees)}`, []],
    [s.external && "Not between your accounts", []],
    [s.standIn && "Stand-in rows only", []],
    [s.from && s.to ? formatRange(s.from, s.to) : s.from ? `from ${formatDate(s.from)}` : s.to ? `to ${formatDate(s.to)}` : "", []],
    [s.home && "Singapore only", []],
  ];
  return parts.filter(([text]) => !!text).map(([text, icons]) => ({ text: text as string, icons }));
}

// the category box's entry for "no category filter": a Combobox only takes a choice from its list
const ALL_CATEGORIES = "All categories";

// how long typing in the search box pauses before the search is asked for
const SEARCH_PAUSE_MS = 250;

// one family: every filter is a 32px box with the same border, fill and 13px text
const FILTER_BOX = "flex-1 basis-36 sm:flex-none";

export function Transactions() {
  // G13: every filter, the page included, lives in the address. Back restores
  // them, a copied link shares them, and Home's "needs a category" banner and
  // every chart bar link here with ?category=, ?month= or ?q= to open the rows
  const [params, patchUrl] = useUrlParams();
  const month = params.get("month") ?? "";
  const category = params.get("category") ?? "";
  const account = params.get("account") ?? "";
  const q = params.get("q") ?? "";
  // the search box shows every key at once, and the address (so the server, and the kept answers) takes the
  // text once typing pauses or on Enter: "starbucks" is one request, not nine, and no half-typed word's page
  // pushes another page's answer out of the cache. Any change of the address fills the box from it, so a link
  // (the sidebar's, Back, Clear filters) drops a search still waiting; every write this page makes takes the
  // waiting search along (patch below), so a filter or a page picked here keeps it.
  const where = params.toString();
  const [box, setBox] = useState({ text: q, from: where });
  if (box.from !== where) setBox({ text: q, from: where });
  const typed = box.from !== where ? q : box.text;
  const waitingSearch = useRef<string | null>(null);
  waitingSearch.current = typed !== q ? typed : null;
  const patch = useCallback((change: Record<string, string | null>) => {
    const text = waitingSearch.current;
    patchUrl(text !== null && !("q" in change) ? { q: text, ...change } : change);
  }, [patchUrl]);
  useEffect(() => {
    if (typed === q) return;
    const wait = window.setTimeout(() => patchUrl({ q: typed }), SEARCH_PAUSE_MS);
    return () => window.clearTimeout(wait);
  }, [typed, q, patchUrl]);
  // G13: a link from another page lists exactly the rows its figure counts: one payee by its exact
  // name, or particular rows by their keys (comma-separated). Each shows as a chip that clears it.
  const payee = params.get("payee") ?? "";
  const keys = params.get("key") ?? "";
  const paymentsOnly = params.get("out") === "1";
  // with a payee: only the rows the rules gave that payee (Recurring's links), not rows whose bank text reads the same
  const named = params.get("named") === "1";
  const keyCount = keys ? keys.split(",").filter(Boolean).length : 0;
  // the Spending explorer's own filters (its range, Singapore only, spending
  // only) and a trip from the Trips page (G13), shown as one chip that clears them
  // G13: a stat's bar says which rows its figure adds up (finance/api.py): the bucket it counts (spending,
  // income, transfers), a need class, a bucket of categories or particular categories, rows leaving out
  // moves between own accounts, or only the pipeline's stand-in rows. They share the chip, and the Clear
  const scope = {
    from: params.get("from") ?? "", to: params.get("to") ?? "",
    home: params.get("home") === "1", spend: params.get("spend") === "1", trip: params.get("trip") ?? "",
    income: params.get("income") === "1", transfer: params.get("transfer") === "1", need: params.get("need") ?? "",
    bucket: params.get("bucket") ?? "", categories: params.get("categories") ?? "",
    notCategories: params.get("not_categories") ?? "", notPayees: params.get("not_payees") ?? "",
    external: params.get("external") === "1", standIn: params.get("synthetic") === "1",
  };
  // a link from Home's "needs a category" line leaves out the rows its count leaves out (no visible control)
  const noSynthetic = params.get("synthetic") === "0";
  // G15: Home's scam line opens the rows the scam checks flag (with hide_transfers=0)
  const scam = params.get("flag") === "scam";
  // G18: Needs your eye's rows with no SGD value (with hide_transfers=0), a filter of their own
  const noFx = params.get("missing_fx") === "1";
  const scopeText = scopeChip(scope);
  const scoped = scopeText.length > 0;
  const hideParam = params.get("hide_transfers");
  const hideTransfers = hideParam === "1" ? true : hideParam === "0" ? false : readHide();
  // hiding transfers is the page's default view, not a filter: Clear filters leaves it as it is
  const anyFilter = !!(q.trim() || month || category || account || scoped || noSynthetic || scam || noFx || payee || keyCount || paymentsOnly);
  const filters = useMemo(() => {
    const p = new URLSearchParams();
    if (month) p.set("month", month);
    if (category) p.set("category", category);
    if (account) p.set("account", account);
    if (q.trim()) p.set("q", q.trim());
    if (payee) p.set("payee", payee);
    if (payee && named) p.set("named", "1");
    if (keys) p.set("key", keys);
    if (paymentsOnly) p.set("out", "1");
    if (scope.from) p.set("from", scope.from);
    if (scope.to) p.set("to", scope.to);
    if (scope.home) p.set("home", "1");
    if (scope.spend) p.set("spend", "1");
    if (scope.trip) p.set("trip", scope.trip);
    if (scope.income) p.set("income", "1");
    if (scope.transfer) p.set("transfer", "1");
    if (scope.need) p.set("need", scope.need);
    if (scope.bucket) p.set("bucket", scope.bucket);
    if (scope.categories) p.set("categories", scope.categories);
    if (scope.notCategories) p.set("not_categories", scope.notCategories);
    if (scope.notPayees) p.set("not_payees", scope.notPayees);
    if (scope.external) p.set("external", "1");
    if (noSynthetic) p.set("synthetic", "0");
    if (scope.standIn) p.set("synthetic", "1");
    if (hideTransfers) p.set("hide_transfers", "1");
    if (scam) p.set("flag", "scam");
    if (noFx) p.set("missing_fx", "1");
    return p.toString();
  }, [month, category, account, q, payee, named, keys, paymentsOnly, scope.from, scope.to, scope.home, scope.spend, scope.trip, scope.income, scope.transfer,
    scope.need, scope.bucket, scope.categories, scope.notCategories, scope.notPayees, scope.external, scope.standIn, noSynthetic, hideTransfers, scam, noFx]);
  const urlPage = Math.max(1, Math.floor(Number(params.get("page"))) || 1);
  // G13: server-side pages. The page starts again at 1 when any filter changes
  // and is clamped when the total shrinks. The total is the one the last answer
  // gave for these filters: a view that has kept the old rows on screen while
  // the new ones load has not got it yet, and a link to page 3 of a new filter
  // must not be cut back to the old filter's last page meanwhile.
  const [answer, setAnswer] = useState<{ data: TxnPage | null; filters: string }>({ data: null, filters });
  const waiting = answer.filters !== filters && urlPage > 1;
  const pager = usePager("transactions", waiting ? undefined : answer.data?.total, filters, urlPage);
  // the page and the address follow each other: a click on a page number writes
  // it, a link to another page of this list (or none) moves the list there
  const seenUrlPage = useRef(urlPage);
  useEffect(() => {
    if (urlPage !== seenUrlPage.current) {
      seenUrlPage.current = urlPage;
      if (urlPage !== pager.page) pager.setPage(urlPage);
    } else if (pager.page !== urlPage) {
      seenUrlPage.current = pager.page;
      patch({ page: pager.page > 1 ? String(pager.page) : null });
    }
  }, [urlPage, pager, patch]);
  const url = `/api/transactions?${new URLSearchParams({ limit: String(pager.size), offset: String(pager.offset) })}${filters ? `&${filters}` : ""}`;
  const { data, error, loading } = useApi<TxnPage>(url);
  const [open, setOpen] = useState<string | null>(null);   // the row whose panel is open, under it
  const [picking, setPicking] = useState<string | null>(null);   // the row whose category picker is open
  const [pins, setPins] = useState<Record<string, PinSet | null | undefined>>({});   // what each pick set, for its panel
  const wide = useWide();
  if (data !== answer.data) setAnswer({ data, filters });
  // a saved choice to show transfers goes into the address, so a copied link keeps a view that is not the default
  useEffect(() => { if (hideParam === null && !hideTransfers) patch({ hide_transfers: "0" }); }, [hideParam, hideTransfers, patch]);
  const toggleTransfers = () => {
    const kept = saveHide(!hideTransfers);
    // hidden is the default, so the address says nothing for it unless the saved choice could not be changed
    patch({ hide_transfers: hideTransfers ? "0" : kept ? null : "1" });
  };

  // every filter off for this view; whether transfers show is left as it is
  // a search still waiting for the pause goes too, so it cannot come back after the clear
  const clearFilters = () => { setBox({ text: "", from: where }); patch({
    q: null, payee: null, named: null, key: null, out: null, month: null, category: null, account: null, page: null, ...NO_SCOPE, synthetic: null,
    flag: null, missing_fx: null,
  }); };
  const groups = useMemo(() => (data ? groupByDay(data.rows, data.days) : []), [data]);
  // the whole day's totals the server counted for a day on this page
  const dayOf = (date: string): TxnDay =>
    data?.days.find((d) => d.date === date) ?? { date, count: 0, in_sgd: 0, out_sgd: 0, continued: false, continues: false };
  // what was typed into a row's own form, kept here so a change of layout (a tablet turned over) does not empty it
  const drafts = useRef<Record<string, PinDraft>>({});
  const pickable = (data?.categories ?? []).filter((c) => c !== "(none)");
  const cell = (t: Txn) => (
    <CategoryCell t={t} categories={pickable} picking={picking === t.key}
      onStart={() => setPicking(t.key)} onDone={() => setPicking((k) => (k === t.key ? null : k))} onPicked={() => setOpen(t.key)}
      onPinned={(pin) => {
        setPins((p) => ({ ...p, [t.key]: pin }));
        // a form typed into before keeps its note and payee, with the category just picked
        const typed = drafts.current[t.key];
        if (pin && typed) drafts.current[t.key] = { ...typed, category: pin.category };
      }} />
  );

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2 text-[13px]">
        <input type="search" placeholder="Search merchant, payee, memo" value={typed}
          onChange={(e) => setBox({ text: e.target.value, from: where })}
          onKeyDown={(e) => { if (e.key === "Enter") patch({ q: typed }); }}
          aria-label="Search transactions" className="h-8 min-w-0 basis-full rounded-md border border-line bg-panel px-2.5 sm:min-w-56 sm:flex-1 sm:basis-auto" />
        <Combobox label="Category" value={category || ALL_CATEGORIES} options={[ALL_CATEGORIES, ...(data?.categories ?? [])]}
          className={`${FILTER_BOX} sm:w-48`} onChange={(v) => patch({ category: v === ALL_CATEGORIES ? null : v })} />
        <Select label="Month" value={month} placeholder="All months" className={`${FILTER_BOX} sm:w-36`}
          options={[{ value: "", label: "All months" }, ...(data?.months ?? []).map((m) => ({ value: m, label: formatMonth(m) }))]}
          onChange={(v) => patch({ month: v })} />
        <Select label="Account" value={account} placeholder="All accounts" className={`${FILTER_BOX} sm:w-44`}
          options={[{ value: "", label: "All accounts" },
            ...(data?.accounts ?? []).map((a) => ({ value: a.value, label: a.label, icon: <BankMark bank={a.bank} /> }))]}
          onChange={(v) => patch({ account: v })} />
      </div>
      {scoped && (
        <div className="flex items-center gap-2 text-[12px] text-ink-2">
          <span className="rounded-full bg-tile px-2.5 py-0.5">
            {/* G18: each part led by the icons of the kinds it names; the words stay one run of text */}
            {scopeParts(scope).map((p, i) => (
              <Fragment key={p.text}>
                {i > 0 && " · "}
                {p.icons.map((icon, j) => <KindIcon key={j} icon={icon} className="mr-1 inline align-[-2px]" />)}
                {p.text}
              </Fragment>
            ))}
          </span>
          <Button variant="ghost" size="sm" onClick={() => patch({ ...NO_SCOPE, ...(scope.standIn ? { synthetic: null } : {}) })}>Clear</Button>
        </div>
      )}
      {(payee || keyCount > 0 || paymentsOnly) && (
        <div className="flex items-center gap-2 text-[12px] text-ink-2">
          <span className="rounded-full bg-tile px-2.5 py-0.5">
            {[payee && `Payee: ${payee}`, keyCount > 0 && (keyCount === 1 ? "One payment" : `${keyCount.toLocaleString("en-SG")} payments`),
              paymentsOnly && "Money out only"]
              .filter(Boolean).join(" · ")}
          </span>
          <Button variant="ghost" size="sm" onClick={() => patch({ payee: null, named: null, key: null, out: null })}>Clear</Button>
        </div>
      )}
      {scam && (
        <div className="flex items-center gap-2 text-[12px] text-ink-2">
          <span className="inline-flex items-center gap-1 rounded-full bg-warn-bg px-2.5 py-0.5 text-warn">
            <KindIcon icon={SCAM_FLAG_ICON} />Flagged as a possible scam
          </span>
          <Button variant="ghost" size="sm" onClick={() => patch({ flag: null })}>Clear</Button>
        </div>
      )}
      {noFx && (
        <div className="flex items-center gap-2 text-[12px] text-ink-2">
          <span className="inline-flex items-center gap-1 rounded-full bg-warn-bg px-2.5 py-0.5 text-warn">
            <KindIcon icon={EYE_ICON.missing_fx} />No SGD value
          </span>
          <Button variant="ghost" size="sm" onClick={() => patch({ missing_fx: null })}>Clear</Button>
        </div>
      )}
      {error && <Problem message={error} />}
      {loading && !data && <Loading what="Loading transactions" />}
      {data && (
        <div className="flex flex-wrap items-center justify-between gap-2">
          <TransfersChip hidden={hideTransfers} onClick={toggleTransfers} />
          {data.total > 0 && anyFilter && <ClearFilters onClick={clearFilters} />}
        </div>
      )}
      {data && data.total === 0 && (
        anyFilter
          ? <EmptyState title="Nothing matches" action={<ClearFilters onClick={clearFilters} bordered />}>No transaction passes every filter.</EmptyState>
          : <EmptyState title="No transactions yet"
              action={<a href="/upload" onClick={onLinkClick("/upload")} className={buttonClass("secondary")}>Load your statements</a>}>
              Transactions come with your first statements.
            </EmptyState>
      )}
      {data && data.total > 0 && (
        <>
          <div className="flex flex-col gap-2">
            {/* money in, money out and the net of what the filters leave; the third tile takes a phone's whole row */}
            <div role="group" aria-label="Totals for the rows shown"
              className="grid grid-cols-2 gap-3 sm:grid-cols-3 [&>:nth-child(3)]:col-span-2 sm:[&>:nth-child(3)]:col-span-1">
              <Tile label="Money in" value={formatSgd(data.in_sgd ?? 0, { decimals: true })} />
              <Tile label="Money out" value={formatSgd(Math.abs(data.out_sgd ?? 0), { decimals: true })} />
              <Tile label="Net" value={formatSgd(data.sum_sgd, { decimals: true })}
                note={`${data.total.toLocaleString("en-SG")} transaction${data.total === 1 ? "" : "s"}${hideTransfers ? ", transfers hidden" : ""}`} />
            </div>
          </div>
          {wide ? (
            <div className={TABLE_CARD}>
              <table className="num w-full border-collapse text-[13px]">
                <thead>
                  <tr className={HEAD_ROW}>
                    <th className={`${TH} px-3`}>Payee</th>
                    <th className={`${TH} px-3`}>Category</th>
                    <th className={`${TH} px-3`}>Account</th>
                    <th className={`${TH} px-3 text-right`}>Amount</th>
                  </tr>
                </thead>
                  {groups.map((g) => (
                    <tbody key={g.day.date}>
                      <tr className={`${ROW} bg-tile`}>
                        <th scope="rowgroup" colSpan={4} className="px-3 py-1.5 text-left font-normal"><DayHeading day={g.day} /></th>
                      </tr>
                      {g.rows.map((t) => (
                        <Fragment key={t.key}>
                        <tr className={ROW}>
                          {/* G13: a row with no payee reads as a plain description; the
                              line its statement printed stays on hover */}
                          <td className="px-3 py-1.5" title={t.raw || t.merchant}>
                            <PayeeText t={t} />
                            {t.trip && <TripChip name={t.trip} className="ml-2" />}
                            <ScamNote t={t} />
                          </td>
                          <td className="px-3 py-1.5">{cell(t)}</td>
                          <td className="px-3 py-1.5 text-ink-2">
                            {/* G13: the bank's mark and a short name, never a full account number */}
                            <span className="inline-flex items-center gap-2 whitespace-nowrap">
                              <BankMark bank={t.bank ?? "other"} />
                              {t.account}
                            </span>
                          </td>
                          <td className={`whitespace-nowrap px-3 py-1.5 text-right ${t.amount > 0 ? "text-good" : ""}`}>
                            {formatAmount(t.amount, t.currency)}
                            {t.currency !== "SGD" && (
                              <span className="block text-[11px] text-ink-3">{sgdValue(t)}</span>
                            )}
                          </td>
                        </tr>
                        {open === t.key && (
                          <tr>
                            <td colSpan={4} className="bg-tile px-3 py-2"><RowPanel txnKey={t.key} drafts={drafts.current} pin={pins[t.key]} onClose={() => setOpen(null)} /></td>
                          </tr>
                        )}
                        </Fragment>
                      ))}
                    </tbody>
                  ))}
              </table>
            </div>
          ) : (
            // G13: a phone shows each row on two lines, so the amount is never off the edge:
            // the payee and the amount, then the category and the bank's mark; the day is the header above its rows
            <ul className="m-0 list-none overflow-hidden rounded-xl border border-line bg-panel p-0">
              {data.rows.map((t, i) => (
                <Fragment key={t.key}>
                {startsDay(data.rows, i) && (
                  <li className="border-t border-line bg-tile px-3 py-2 first:border-t-0"><DayHeading day={dayOf(t.date)} /></li>
                )}
                <li className="border-t border-line px-3 py-2.5">
                  <div className="flex items-baseline justify-between gap-3 text-[14px]" title={t.raw || t.merchant}>
                    <span className="line-clamp-2 min-w-0 flex-1 break-words"><PayeeText t={t} /></span>
                    <span className={`num shrink-0 whitespace-nowrap ${t.amount > 0 ? "text-good" : ""}`}>{formatAmount(t.amount, t.currency)}</span>
                  </div>
                  <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-[12px] text-ink-2">
                    {cell(t)}
                    <span className="inline-flex items-center"><BankMark bank={t.bank ?? "other"} /><span className="sr-only">{t.account}</span></span>
                    {t.trip && <TripChip name={t.trip} />}
                    {t.currency !== "SGD" && <span className="num ml-auto text-ink-3">{sgdValue(t)}</span>}
                  </div>
                  <ScamNote t={t} />
                  {open === t.key && <div className="mt-2 rounded-md bg-tile px-3 py-2"><RowPanel txnKey={t.key} drafts={drafts.current} pin={pins[t.key]} onClose={() => setOpen(null)} /></div>}
                </li>
                </Fragment>
              ))}
            </ul>
          )}
          <Pager pager={pager} label="Transactions pages" />
        </>
      )}
    </div>
  );
}

type DayGroup = { day: TxnDay; rows: Txn[] };

// G13: the page's rows (newest day first) in runs of one day, each with the day's
// totals the server counted over all that day's rows
function groupByDay(rows: Txn[], days: TxnDay[]): DayGroup[] {
  const totals = new Map(days.map((d) => [d.date, d]));
  const out: DayGroup[] = [];
  for (const t of rows) {
    const last = out.at(-1);
    if (last && last.day.date === t.date) last.rows.push(t);
    else out.push({ day: totals.get(t.date) ?? { date: t.date, count: 0, in_sgd: 0, out_sgd: 0, continued: false, continues: false }, rows: [t] });
  }
  return out;
}

// G13: rows come newest day first, so a row opens its day when the one before it is another day
const startsDay = (rows: Txn[], i: number) => i === 0 || rows[i - 1].date !== rows[i].date;

// "Mon 13 Oct 2026", then what went out and what came in that day. A day that
// began on the page before says so, and so does one that goes on onto the next page. A row with no SGD value is in neither figure.
function DayHeading({ day }: { day: TxnDay }) {
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-0.5 text-[13px]">
      <span className="font-medium">
        {formatWeekday(day.date)}
        {day.continued && <span className="ml-2 text-[12px] font-normal text-ink-3">continued</span>}
        {day.continues && <span className="ml-2 text-[12px] font-normal text-ink-3">continues on the next page</span>}
      </span>
      <span className="num flex gap-3 text-[12px] text-ink-2">
        {day.out_sgd < 0 && <span>Spent {formatSgd(Math.abs(day.out_sgd), { decimals: true })}</span>}
        {day.in_sgd > 0 && <span className="text-good">In {formatSgd(day.in_sgd, { decimals: true })}</span>}
      </span>
    </div>
  );
}

// G13: says whether the tiles and rows leave out transfers, and flips it. Hidden is
// the default, so that is the filled state.
function TransfersChip({ hidden, onClick }: { hidden: boolean; onClick: () => void }) {
  return (
    <button type="button" onClick={onClick}
      title={hidden ? "Wallet top-ups, moves between your own accounts and other transfers are left out" : "Transfers are counted and listed"}
      className={`inline-flex h-8 items-center gap-1.5 rounded-full px-3 text-[12px] ${hidden
        ? "bg-tile text-ink hover:bg-hover" : "border border-line text-ink-2 hover:bg-hover"}`}>
      <EyeOff size={13} aria-hidden className={hidden ? "text-ink-2" : "text-ink-3"} />
      {hidden ? "Transfers hidden" : "Transfers shown"}
      <span className="text-ink-3">{hidden ? "Show" : "Hide"}</span>
    </button>
  );
}

// G15: why the scam checks flag a row, and "This is fine", which saves the
// choice in the record so that payee is not flagged again
function ScamNote({ t }: { t: Txn }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!t.flags?.length) return null;
  async function fine() {
    setBusy(true);
    setError(null);
    try {
      await postJSON("/api/scam-checks/fine", JSON.stringify({ key: t.key }));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      // the row can stay on screen and be flagged again later (Flag again in Settings)
      setBusy(false);
    }
  }
  return (
    <div className="mt-1 flex flex-col items-start gap-1 text-[12px]">
      <ul aria-label="Why this looks like a scam" className="m-0 list-none p-0 text-warn">
        {/* G18: each reason led by its own icon, the one the Settings thresholds share */}
        {t.flags.map((f) => (
          <li key={f.code} className="flex items-start gap-1.5">
            <KindIcon icon={SCAM_ICON[f.code] ?? SCAM_FLAG_ICON} className="mt-[3px]" />{f.text}
          </li>
        ))}
      </ul>
      <Button size="sm" disabled={busy} onClick={() => void fine()}
        aria-label={`This is fine: ${t.payee || t.description || t.merchant} on ${formatDay(t.date)}`}>
        {busy ? "Saving" : "This is fine"}
      </Button>
      {error && <span role="alert" className="text-danger">{error}</span>}
    </div>
  );
}

// what the row is, in the words the page shows first
function PayeeText({ t }: { t: Txn }) {
  return t.payee ? <>{t.payee}</> : t.description
    ? <span className="text-ink-2">{t.description}</span>
    : <span className="text-ink-3">{t.merchant || "(no text)"}</span>;
}

function TripChip({ name, className = "" }: { name: string; className?: string }) {
  return (
    <span className={`inline-flex items-center gap-1 rounded-full bg-tile px-1.5 py-0.5 text-[11px] text-ink-2 ${className}`}>
      <KindIcon icon={TRIP_ICON} size={11} />{name}
    </span>
  );
}

const sgdValue = (t: Txn) => (t.amount_sgd == null ? "no SGD rate" : formatSgd(t.amount_sgd, { decimals: true }));

function ClearFilters({ onClick, bordered = false }: { onClick: () => void; bordered?: boolean }) {
  return (
    <Button variant={bordered ? "secondary" : "ghost"} onClick={onClick}>
      <X size={14} aria-hidden />
      Clear filters
    </Button>
  );
}

// G13: the category as a quiet chip. A row with no category gets a warm "Set
// category" chip. The pencil shows on hover (and on keyboard focus) with a
// mouse, and always on a touch screen, which has no hover.
function CategoryChip({ t, onClick }: { t: Txn; onClick: () => void }) {
  return (
    <button type="button" title="Pick this row's category"
      aria-label={`Set the category of the ${formatDay(t.date)} row ${t.payee || t.description || t.merchant || ""} on its own`}
      onClick={onClick}
      className={t.category
        ? "group inline-flex max-w-full items-center gap-1.5 rounded-full bg-tile px-2.5 py-0.5 text-left text-[12px] text-ink hover:bg-hover"
        : "inline-flex items-center gap-1.5 rounded-full border border-warn/40 bg-warn-bg px-2.5 py-0.5 text-[12px] text-warn hover:border-warn"}>
      <span className="truncate">{t.category || "Set category"}</span>
      <Pencil size={11} aria-hidden className={t.category
        ? "shrink-0 text-ink-3 opacity-0 group-hover:opacity-100 group-focus-visible:opacity-100 [@media(pointer:coarse)]:opacity-100" : "shrink-0"} />
    </button>
  );
}

// G13: one tap sets a row's category. The chip turns into the category picker,
// open on the row's category; a pick saves the row's own key rule at once (the
// record follows, finance/relink.py) with an Undo in the toast, and opens the
// row's panel. Leaving the picker without a pick changes nothing.
function CategoryCell({ t, categories, picking, onStart, onDone, onPicked, onPinned }: {
  t: Txn; categories: string[]; picking: boolean;
  // null while the pick is being saved, then what it set; undefined again when the save failed
  onStart: () => void; onDone: () => void; onPicked: () => void; onPinned: (pin: PinSet | null | undefined) => void;
}) {
  if (!picking) return <CategoryChip t={t} onClick={onStart} />;
  async function pick(category: string) {
    onPicked();
    if (category === t.category) return;
    onPinned(null);
    try {
      // the row keeps its payee; a payee other rules name keeps their kind and country
      const r = await savePin({ key: t.key, category, payee: t.payee });
      onPinned(r.pinned);
    } catch (e) {
      onPinned(undefined);
      showToast({ tone: "error", message: (e as Error).message });
    }
  }
  return (
    <Combobox autoFocus label={`Category for the ${formatDay(t.date)} row`} value={t.category} options={categories}
      placeholder="Choose a category" className="w-48 max-w-full" onChange={(c) => void pick(c)} onDone={onDone}
      commitOnLeave={false} />
  );
}

// G13: under a row once its category is picked: one chip that turns the pick
// into a rule for the rows like it ("Apply to 6 similar rows", the count from
// the rule preview), and "More" for the payee, kind and note of the row's own
// rule. A row a hand-edited rules file left unapplied offers Apply rules.
function RowPanel({ txnKey, drafts, pin, onClose }: {
  txnKey: string; drafts: Record<string, PinDraft>; onClose: () => void;
  // what the pick just set, so the similar-rows chip need not wait for the row to be asked again;
  // null while the pick is still being saved, when there is nothing to preview yet
  pin?: PinSet | null;
}) {
  const state = useApi<GroupRows>(`/api/rules/rows?${new URLSearchParams({ key: txnKey })}`);
  // a form opened before a change of layout (a tablet turned over) opens again with what was typed
  const [more, setMore] = useState(() => txnKey in drafts);
  const [busy, setBusy] = useState(false);
  // the pick's answer stands for the row's pin only until the row is asked again: after
  // that (a save through More, an Undo) the row's own pin is the newer word
  const [fresh, setFresh] = useState<PinSet | undefined>(pin ?? undefined);
  const seen = useRef(state.data);
  useEffect(() => {
    if (pin) { setFresh(pin); seen.current = state.data; }
  }, [pin]);
  useEffect(() => {
    if (state.data !== seen.current) { seen.current = state.data; setFresh(undefined); }
  }, [state.data]);
  const row = state.data?.rows[0];
  const draft = pin !== null && row?.similar && (fresh?.category || row.pinned?.category || row.category)
    ? similarDraft(row, fresh) : null;
  const query = draft ? draftParams(draft).toString() : null;
  const preview = useApi<RulePreview>(query ? `/api/rules/preview?${query}` : null);
  // the rule just saved hides its chip until the preview is asked again; then the preview decides
  // (no rows left to change, or the same count again after an Undo)
  const [hidden, setHidden] = useState<string | null>(null);
  // which rule the preview on hand counted: a new pick keeps the old count on screen until its own
  // arrives, and the chip must never promise one rule's count while saving another
  const [counted, setCounted] = useState<string | null>(null);
  useEffect(() => { setHidden(null); setCounted(preview.data ? query : null); }, [preview.data]);
  if (state.error) return <p className="m-0 text-[13px] text-warn">{state.error}</p>;
  if (!state.data) return <Loading what="Loading the row" />;
  const d = state.data;
  if (!row) return <p className="m-0 text-[13px] text-warn">That row is no longer in the record.</p>;
  const similar = counted === query && hidden !== query && preview.data && !preview.data.error ? preview.data.changes ?? 0 : 0;

  async function act(run: () => Promise<void>) {
    setBusy(true);
    try {
      await run();
    } catch (e) {
      showToast({ tone: "error", message: (e as Error).message });
    } finally {
      setBusy(false);
    }
  }
  // the same rule "Apply to similar rows" built before G13: the row's text as a pattern, with its category and payee
  const applySimilar = () => act(async () => {
    const r = await postJSON<RuleSavedEdit>("/api/rules/save", JSON.stringify({ rule: ruleOf(draft!), edit: null }));
    setHidden(query);
    showToast({ message: `Rule saved, ${rowsChanged(r.relinked)}.`, action: r.change ? { label: "Undo", onClick: () => undoRuleWrite(r.change!) } : undefined });
  });
  const apply = () => act(async () => {
    const r = await postJSON<{ relinked: Relinked }>("/api/rules/apply");
    showToast({ message: `Rules applied, ${rowsChanged(r.relinked)}.` });
  });

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2 text-[13px]">
        {row.pinned && !row.pinned.applied && (
          <>
            <span className="text-ink-2">{pinnedText(row.pinned)}</span>
            <Button size="sm" disabled={busy} onClick={() => void apply()}>Apply rules</Button>
          </>
        )}
        {similar > 0 && (
          <button type="button" disabled={busy} onClick={() => void applySimilar()}
            className="num inline-flex items-center gap-1.5 rounded-full border border-line bg-panel px-3 py-1 hover:bg-hover disabled:opacity-40">
            <Plus size={13} aria-hidden />
            Apply to {similar.toLocaleString("en-SG")} similar {similar === 1 ? "row" : "rows"}
          </button>
        )}
        <button type="button" aria-expanded={more} onClick={() => setMore(!more)}
          className="rounded-md px-1.5 py-0.5 text-ink-2 underline decoration-line underline-offset-2 hover:text-ink">
          {more ? "Less" : "More"}
        </button>
        <button type="button" aria-label="Close" onClick={onClose}
          className="ml-auto grid size-6 place-items-center rounded text-ink-3 hover:bg-hover hover:text-ink">
          <X size={14} aria-hidden />
        </button>
      </div>
      {more && (
        <PinForm row={row} categories={d.categories} kinds={d.kinds} draft={drafts[txnKey]}
          onDraft={(x) => { drafts[txnKey] = x; }} onPinned={() => setMore(false)} />
      )}
    </div>
  );
}

// G13: the rule for the rows like this one: the pattern guessed from the row's
// text, and the category and payee the row has now, with the kind and country
// its pin gave that payee. A row set with no payee gives a rule with none: the
// guess from the text is not the user's choice.
function similarDraft(row: GroupRow, pin?: PinSet): Draft {
  const s = row.similar!;
  const pinned = pin ?? row.pinned;
  return {
    match_on: s.match_on, pattern: s.pattern, payee: pinned ? pinned.payee : row.payee || s.payee,
    kind: pinned?.kind || s.kind || "Merchant",
    country: (pinned?.payee && pinned.country) || "", category: pinned?.category ?? row.category, recurring: false, notes: "",
  };
}
