// G5: the browser's view of the local API (serve.py). Money from the record
// arrives as integer cents; stat results arrive as the stats wrote them.
import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import type { Cents } from "./money";
import type { StatResult } from "./stats/hint";
import {
  CACHED_PATHS, CACHED_PREFIX, NO_CHANGE_PATHS, REREAD_AFTER, type Bank, type BucketStyle,
  type ComingUp, type Dollars, type Folder, type GrowthInputs, type Horizon, type NeedsYourEye, type RuleRow,
  type Suggestion, type UploadAttempt as UploadAttemptBase, type UploadStepOutline,
} from "./types.gen";

// G17: the shapes below come from finance/types.py through types.gen.ts
// (`make types`), so they cannot drift from what the server sends;
// tests/test_types.py checks every endpoint's answer against them. The ones
// still written here (Overview, Growth, ...) are not generated yet. The plan
// and the Net worth page's inputs carry their money in cents under `_minor`
// names (G17).
export type {
  About, ActiveSeries, BucketIcon, BucketStyle, BudgetGroup, BudgetRow, Budgets, Cadence, CategoriesState, Category,
  CategoryEntry, CategoryGroup, ComingStatus, ComingUp, ComingUpRow, Dollars, EyeKind, EyeLine, EyeTone, Folder,
  GroupKind, GroupRow, GroupRows, GrowthInputs, Horizon, IncomeRow, IncomeType, InvestingStep, IrregularPayee,
  NeedClass, NeedsGroup, NeedsYourEye, OneOff, Origin, PayeeKind, PinnedRule, Plan, PlanIncomeRow, PlanOneOff,
  PlanProjection, PlanState, PlanTrip, Profile, ProfileAccount, ProfileFolder, ProjectionSettings, RecurringBase,
  NotRecurring, RecurringOff, RecurringPage, Rule, RuleChange, RuleGroup, RuleProblem, RuleRow, RulesState, ScamChecks, ScamCode,
  SeriesDetected, SeriesEdit, SeriesPayment, SeriesSlot, SeriesView, SeriesViewState, SlotStatus,
  ScamFine, ScamFlag, ScamLine, ScamReason, ScamSettings, SeriesColour, ServerInfo, ServerMode, StaleAccount, StepName, StepStatus,
  StoppedSeries, Suggestion, Tier, Trip, TripCategory, TripList, Txn, TxnAccount, TxnDay, TxnPage, UploadAlert,
  UploadPage, UploadStep, UploadStepOutline,
  // G26: the dashboard's widget queries and state
  Allocation, AllocationBucket, AllocationTemplate, DashboardGoal, DashboardGoals, DashboardLayout, DashboardLayouts,
  DashboardMilestone, DashboardWidget, GamePot, GamePotAccount, GoalBinding, ScenarioPreview, WidgetAnswer,
  WidgetDimension, WidgetQuery, WidgetRange, WidgetSeries, WidgetSource, WidgetUnit,
} from "./types.gen";

// G13: Home (GET /api/overview, finance/home.py). A change is against the
// month named in `since`.
export type Change = { change: Cents; since: string };
// the Spending explorer's figures over every row, by bucket: the chart's months
// and series, and `totals.per_month`, the dashed average
export type SpendingChart = {
  by: "bucket"; scope: "all"; bucket_order: string[]; bucket_style: Record<string, BucketStyle>; months: string[];
  series: { name: string; total: Cents; per_month: Cents; values: Cents[]; folded?: number; link: Record<string, string> }[];
  // G13: the Transactions filters that list the rows behind the chart's bars (see Spending in SpendingExplorer.tsx)
  link: Record<string, string>;
  totals: { spend: Cents; per_month: Cents; overseas: Cents };
};
export type Overview = {
  as_of: string | null;
  headline: {
    net_worth_incl_cpf: Cents | null;
    net_worth_change: Change | null;
    cash: Cents | null;
    cash_change: Change | null;
    runway_months: number | null;
    runway_range: [number, number] | null;
  };
  holdings: { label: string; value: Cents }[];
  spending: SpendingChart;
  coming_up: ComingUp;
  needs_your_eye: NeedsYourEye;
  missing_fx: number;
};

// GET /api/countries: every ISO country by alpha-3 code, with its alpha-2 code and flag
export type Country = { code: string; alpha2: string; flag: string };

// G13: the server cannot be reached. Every page shows this one plain message
// (States.tsx draws it as a placeholder, not a raw browser error) and the app
// shell says it once, in a banner (components/ServerBanner.tsx).
export const OFFLINE = "Can't reach Kopifund.";
const OFFLINE_WRITE = "Can't reach Kopifund, so that was not saved.";
// Audit: an answer came but is not the JSON the app reads (a proxy's or a captive portal's page, a body cut
// short). Said in plain words, never the parser's "Unexpected token"; a write whose answer is unreadable may
// still have landed, so it is not reported as saved, and the views ask again to show what is true.
export const UNREADABLE = "Kopifund's answer could not be read.";
export const UNREADABLE_WRITE = "Kopifund's answer could not be read, so check the change shows before doing it again.";

/** `status` is the HTTP status, or 0 when no answer came back at all. */
export class ApiError extends Error {
  status: number;
  constructor(message: string, status = 0) {
    super(message);
    this.status = status;
  }
}

// -------------------------------------------------- G13: is the server there
//
// Any answer from the server marks it up, any failure to get one marks it
// down. `stale` is true while the server is down and a view mounted now is
// painting what it showed last time (a page with nothing kept has none).
export type Link = { down: boolean; stale: boolean };
let link: Link = { down: false, stale: false };
const linkListeners = new Set<() => void>();

function setLink(next: Link) {
  if (next.down === link.down && next.stale === link.stale) return;
  link = next;
  linkListeners.forEach((l) => l());
}

// the mounted views that have figures on screen; while the server is down
// those figures are from the last visit, however the outage was found (a view's
// own GET, or a save that failed)
const shown = new Set<{ has: boolean }>();

function refreshLink(down: boolean) {
  setLink({ down, stale: down && [...shown].some((v) => v.has) });
}

function markDown() {
  refreshLink(true);
}

function markUp() {
  if (!link.down) return;
  refreshLink(false);
  // whatever failed meanwhile asks again
  revalidate();
}

export function useLink(): Link {
  return useSyncExternalStore(
    (fn) => {
      linkListeners.add(fn);
      return () => void linkListeners.delete(fn);
    },
    () => link,
  );
}

/** Ask the server whether it is there; true when it answered. */
export async function checkServer(): Promise<boolean> {
  try {
    const r = await fetch("/api/health", { cache: "no-store" });
    if (r.status < 500) {
      markUp();
      return true;
    }
  } catch {
    /* no answer */
  }
  markDown();
  return false;
}

/** For tests: forget that the server was ever down. */
export function resetLink() {
  link = { down: false, stale: false };
  shown.clear();
  linkListeners.forEach((l) => l());
}

// a fetch that says plainly when nothing answered
async function reach(url: string, init?: RequestInit, write = false): Promise<Response> {
  let r: Response;
  try {
    r = await fetch(url, init);
  } catch {
    markDown();
    throw new ApiError(write ? OFFLINE_WRITE : OFFLINE);
  }
  if (r.status < 500) markUp();
  return r;
}

// The body of an answer. When the connection drops after the headers came, reading
// it rejects with a TypeError, outside `reach`'s catch: that is the server not being
// there too, so it says so (and the banner and the kept figures follow) rather than
// handing a page the browser's own words. A body that arrives but is not JSON is
// another matter and passes through.
async function readText(r: Response, write = false): Promise<string> {
  try {
    return await r.text();
  } catch (e) {
    if (!(e instanceof TypeError)) throw e;
    markDown();
    throw new ApiError(write ? OFFLINE_WRITE : OFFLINE);
  }
}

/** An answer's text as JSON, or UNREADABLE for text that is not. */
function parse(text: string, status: number, write = false): unknown {
  try {
    return JSON.parse(text) as unknown;
  } catch {
    throw new ApiError(write ? UNREADABLE_WRITE : UNREADABLE, status);
  }
}

/** The `error` a refusal's body carries, when it is JSON with one. */
function saidIn(text: string): string | null {
  try {
    const said = (JSON.parse(text) as { error?: unknown } | null)?.error;
    return typeof said === "string" && said ? said : null;
  } catch {
    return null;
  }
}

/** A failed answer as the error a page shows; `text` is its body when the caller has read it already. */
async function failure(r: Response, write = false, text?: string): Promise<ApiError> {
  let msg = `${r.status} ${r.statusText}`;
  let said = false;
  let body = text;
  if (body === undefined) {
    try {
      body = await r.text();
    } catch {
      body = "";
    }
  }
  const words = saidIn(body);
  if (words) {
    msg = words;
    said = true;
  }
  // a gateway that found nothing behind it (the dev server's proxy answers 500
  // with no body when serve.py is stopped) is a server that is not there
  if (r.status === 502 || r.status === 503 || r.status === 504 || (r.status >= 500 && !said)) {
    markDown();
    return new ApiError(write ? OFFLINE_WRITE : OFFLINE);
  }
  markUp();
  return new ApiError(msg, r.status);
}

export async function getJSON<T>(url: string): Promise<T> {
  const r = await reach(url);
  if (!r.ok) throw await failure(r);
  return parse(await readText(r), r.status) as T;
}

// `status` is the HTTP status of a failed answer (404 for "nothing there yet"), 0 when the server did not answer
export type Loaded<T> = { data: T | null; error: string | null; loading: boolean; status?: number };

// ------------------------------------------------------------ G13: the cache
//
// The last answer to every cached GET, by URL, so a page opened again paints
// at once from what it showed last time, and a reload paints from the copy
// kept in localStorage. Each use still asks the server, with the answer's
// ETag (finance/page_cache.py, data_version): a 304 keeps what is on screen, anything else
// replaces it. Every write the app makes empties it (notifyChanged), and so
// does the end of a stats rebuild, so a figure kept from before a write is
// never shown after it.

// serve.py's CACHED (G17: generated from it): everything else is asked afresh every time, uncached
const CACHED = new Set(CACHED_PATHS);
export function cacheable(url: string): boolean {
  const path = url.split("?")[0];
  return CACHED.has(path) || path.startsWith(CACHED_PREFIX);
}

// the build, so a copy kept by an older app, whose answers may have another
// shape, is never painted by a newer one (vite.config.ts)
declare const __KOPIFUND_BUILD__: string;
const STORE_PREFIX = "kopifund:api:";
const STORE = `${STORE_PREFIX}${__KOPIFUND_BUILD__}`;
/** For tests: where the kept copy lives. */
export const KEPT_KEY = STORE;
const CHANGED_KEY = "kopifund:api-changed";       // tells the app's other tabs a write happened
const REFRESH_KEY = "kopifund:api-refresh";       // tells them which views a REREAD_AFTER write changed
// localStorage holds about 5 million characters for the whole origin
const MAX_ENTRY = 500_000;
const MAX_STORED = 1_500_000;
// Audit: the copy sits on disk in the browser profile, outside the data folder, so it holds only what a
// page paints first (the summaries and stat results), never the ledger's rows or the profile, which stay
// in memory for the visit; and an answer not read for KEEP_FOR is dropped
const NOT_KEPT = ["/api/transactions", "/api/rules/rows", "/api/scam-checks", "/api/profile"];
export const KEEP_FOR = 30 * 24 * 60 * 60 * 1000;
const keptOnDisk = (url: string) => !NOT_KEPT.includes(url.split("?")[0]);
// answers kept in memory, the least recently read going first: every search
// and page of Transactions is its own URL
export const MAX_KEPT = 50;

// `text` is the answer as it arrived, kept so saving it costs no stringify;
// `checked` is when the server last said it is current, `used` when a page last read it
type Entry = { data: unknown; etag: string | null; text: string; checked: number; used: number };
const cache = new Map<string, Entry>();
const inflight = new Map<string, { generation: string; promise: Promise<unknown> }>();
// every emptying starts a new generation; an answer asked for before it is not kept
let generation = 0;
// G18: the same for one URL: refreshViews moves it on for an ask still on its way, so that ask is
// neither reused nor kept, since it was sent before the write the view asks again for
const urlGeneration = new Map<string, number>();
const generationOf = (url: string) => `${generation}.${urlGeneration.get(url) ?? 0}`;

function keep(url: string, e: Entry) {
  cache.set(url, e);
  while (cache.size > MAX_KEPT) {
    let oldest: string | null = null;
    let at = Infinity;
    for (const [u, x] of cache) if (x.used < at) [oldest, at] = [u, x.used];
    if (oldest == null) break;
    cache.delete(oldest);
  }
}

function storage(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}

/** Paint from the kept copy: what a reload starts with. Exported for tests. */
export function restoreKept() {
  restore();
}

function restore() {
  const store = storage();
  if (!store) return;
  try {
    for (let i = store.length - 1; i >= 0; i--) {
      const key = store.key(i);
      if (key?.startsWith(STORE_PREFIX) && key !== STORE) store.removeItem(key);
    }
    const kept = JSON.parse(store.getItem(STORE) ?? "[]") as [string, string | null, string, number][];
    const now = Date.now();
    let dropped = false;
    for (const [url, etag, text, used] of kept) {
      // checked 0: painted from the copy, then asked about straight away
      if (cacheable(url) && keptOnDisk(url) && now - used <= KEEP_FOR) keep(url, { data: JSON.parse(text), etag, text, checked: 0, used });
      else dropped = true;
    }
    // what was dropped leaves the disk now, not at the next answer
    if (dropped) save();
  } catch {
    cache.clear();                                 // a copy that does not parse: start empty
  }
}

let saveTimer: ReturnType<typeof setTimeout> | null = null;
function save() {
  saveTimer = null;
  const store = storage();
  if (!store) return;
  // the most recently read first, as many as fit
  const rows: [string, string | null, string, number][] = [];
  let size = 0;
  for (const [url, e] of [...cache].sort((a, b) => b[1].used - a[1].used)) {
    const n = e.text.length + url.length + 64;
    if (!keptOnDisk(url) || n > MAX_ENTRY || size + n > MAX_STORED) continue;
    rows.push([url, e.etag, e.text, e.used]);
    size += n;
  }
  try {
    store.setItem(STORE, JSON.stringify(rows));
  } catch {
    try {
      store.removeItem(STORE);                     // full: better no copy than a stale one
    } catch {
      /* storage refused */
    }
  }
}
function saveSoon() {
  if (saveTimer == null) saveTimer = setTimeout(save, 400);
}
function saveNow() {
  if (saveTimer != null) clearTimeout(saveTimer);
  save();
}

/** Empty the cache, here and in the kept copy. */
function forget() {
  generation += 1;
  cache.clear();
  inflight.clear();
  if (saveTimer != null) clearTimeout(saveTimer);
  saveTimer = null;
  try {
    storage()?.removeItem(STORE);
  } catch {
    /* storage refused */
  }
}

/**
 * For tests: start from nothing, as a fresh page would. Audit: that includes
 * the per-URL generations, the revalidate clock and a stats watch left
 * running by a test that ended mid-rebuild (which would make the next one skip).
 */
export function clearApiCache() {
  forget();
  urlGeneration.clear();
  revalidatedAt = 0;               // no round asked yet, whatever clock a test moved
  watchRun += 1;
  watching = false;
}

/** The kept answer for `url`, if any, without asking the server. */
function peek<T>(url: string): T | undefined {
  const e = cacheable(url) ? cache.get(url) : undefined;
  if (!e) return undefined;
  e.used = Date.now();
  // the read time goes to disk too, so an answer drops 30 days after it was last read, offline or not
  saveSoon();
  return e.data as T;
}

/** Ask the server, with the kept answer's ETag; one request per URL at a time. */
function load<T>(url: string): Promise<T> {
  if (!cacheable(url)) return getJSON<T>(url);
  const running = inflight.get(url);
  if (running && running.generation === generationOf(url)) return running.promise as Promise<T>;
  const asked = generationOf(url);
  const held = cache.get(url);
  const promise = (async () => {
    const r = await reach(url, held?.etag ? { headers: { "If-None-Match": held.etag } } : undefined);
    if (r.status === 304 && held) {
      if (asked === generationOf(url)) held.checked = Date.now();
      return held.data;
    }
    if (!r.ok) {
      const err = await failure(r);
      // never paint an answer the server no longer gives, after a reload either;
      // a gateway with nothing behind it (status 0) says nothing about the answer
      if (err.status !== 0 && asked === generationOf(url) && cache.delete(url)) saveNow();
      throw err;
    }
    const text = await readText(r);
    const data = parse(text, r.status);
    if (asked === generationOf(url)) {
      const now = Date.now();
      keep(url, { data, etag: r.headers.get("ETag"), text, checked: now, used: now });
      saveSoon();
    }
    return data;
  })();
  inflight.set(url, { generation: asked, promise });
  const done = () => {
    if (inflight.get(url)?.promise === promise) inflight.delete(url);
  };
  promise.then(done, done);
  return promise as Promise<T>;
}

/**
 * Warm the cache for a page about to open (a sidebar link under the
 * pointer), so it paints at once. An answer the server confirmed in the
 * last few seconds is not asked about again.
 */
export function prefetch(urls: readonly string[]) {
  for (const url of urls) {
    const e = cache.get(url);
    if (!cacheable(url) || (e && Date.now() - e.checked < 10_000)) continue;
    load(url).catch(() => {
      /* the page asks again when it opens, and shows the problem then */
    });
  }
}

// Every successful write announces itself, and every mounted view reloads:
// after a load or a new rule, Home and the stat cards show the new record
// without a browser reload. A reload keeps the old data on screen meanwhile.
const CHANGED = "kopifund:changed";

/** Ask every mounted view again, keeping the cache: the ETags make it cheap. */
export function revalidate() {
  window.dispatchEvent(new Event(CHANGED));
}

// G18: ask the mounted views of these paths again (with their tags), and no other; nothing kept is forgotten
const REFRESH = "kopifund:refresh";

export function refreshViews(paths: readonly string[]) {
  for (const url of inflight.keys()) {
    if (paths.includes(url.split("?")[0])) urlGeneration.set(url, (urlGeneration.get(url) ?? 0) + 1);
  }
  window.dispatchEvent(new CustomEvent<readonly string[]>(REFRESH, { detail: paths }));
}

export function notifyChanged() {
  forget();
  try {
    storage()?.setItem(CHANGED_KEY, String(Date.now()));
  } catch {
    /* storage refused: other tabs catch up on their next ask */
  }
  window.dispatchEvent(new Event(CHANGED));
}

// A `make load` or an edit in another window changes the record while a page
// stays open. When the tab comes back (it turns visible, or the window gets
// focus), every mounted view asks again with its ETag (`revalidate`): an
// unchanged answer is a 304 and nothing redraws, a changed one replaces the
// data. The cache is not emptied, and an ask that gets no answer (the laptop
// just woke, the network is not up yet) leaves what is on screen with the
// offline banner. At most one round every few seconds, since a tab switch
// often fires both events.
export const REVALIDATE_EVERY = 5_000;
let revalidatedAt = Date.now();

export function revalidateViews() {
  const now = Date.now();
  if (now - revalidatedAt < REVALIDATE_EVERY) return;
  revalidatedAt = now;
  revalidate();
}

if (typeof window !== "undefined") {
  restore();
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") revalidateViews();
  });
  window.addEventListener("focus", revalidateViews);
  // a write in another tab of the app empties this tab's cache too
  window.addEventListener("storage", (e) => {
    if (e.key === REFRESH_KEY && e.newValue) {
      try {
        const { paths } = JSON.parse(e.newValue) as { paths?: unknown };
        if (Array.isArray(paths)) refreshViews(paths.filter((p): p is string => typeof p === "string"));
      } catch {
        /* not ours to read */
      }
      return;
    }
    if (e.key !== CHANGED_KEY) return;
    forget();
    window.dispatchEvent(new Event(CHANGED));
  });
  window.addEventListener("pagehide", () => {
    if (saveTimer != null) {
      clearTimeout(saveTimer);
      save();
    }
  });
}

type Held<T> = Loaded<T> & { url: string | null };

// what a view shows the moment it asks for `url`: the kept answer when there
// is one, else what it showed before (the old page of rows under a new filter)
function first<T>(url: string | null, before: Held<T> | null): Held<T> {
  const kept = url ? peek<T>(url) : undefined;
  if (kept !== undefined) return { data: kept, error: null, loading: false, url };
  if (!url) return { data: before?.data ?? null, error: before?.error ?? null, loading: false, url };
  return { data: before?.data ?? null, error: null, loading: before?.data == null, url };
}

/**
 * The answer to a GET: the kept one at once when there is one (no loading
 * state), then the server's, asked again whenever `url` changes or the app
 * writes something.
 */
export function useApi<T>(url: string | null, opts: { quiet?: boolean } = {}): Loaded<T> {
  const [state, setState] = useState<Held<T>>(() => first<T>(url, null));
  const [version, setVersion] = useState(0);
  let current = state;
  if (state.url !== url) {
    // a new URL shows its kept answer in this very render, not one later
    current = first<T>(url, state);
    setState(current);
  }
  // does this view have figures on screen? (`quiet`: the shell's own use of a
  // page's answer, which is no figure of the page being shown)
  const token = useRef({ has: false });
  token.current.has = current.data != null && !opts.quiet;
  const has = token.current.has;
  useEffect(() => {
    const bump = () => setVersion((v) => v + 1);
    window.addEventListener(CHANGED, bump);
    return () => window.removeEventListener(CHANGED, bump);
  }, []);
  useEffect(() => {
    if (!url) return;
    const path = url.split("?")[0];
    const mine = (e: Event) => {
      if ((e as CustomEvent<readonly string[]>).detail.includes(path)) setVersion((v) => v + 1);
    };
    window.addEventListener(REFRESH, mine);
    return () => window.removeEventListener(REFRESH, mine);
  }, [url]);
  useEffect(() => {
    const me = token.current;
    shown.add(me);
    return () => {
      shown.delete(me);
      refreshLink(link.down);
    };
  }, []);
  useEffect(() => refreshLink(link.down), [has]);
  useEffect(() => {
    if (!url) return;
    let live = true;
    setState((s) => (s.error == null && s.loading === (s.data == null) ? s : { ...s, loading: s.data == null, error: null }));
    load<T>(url).then(
      // a 304 hands back the same object, so nothing on screen redraws
      (data) => {
        if (!live) return;
        setState((s) => (s.data === data && !s.loading && !s.error && s.url === url ? s : { data, error: null, loading: false, url }));
      },
      (e: Error) => {
        if (!live) return;
        const status = e instanceof ApiError ? e.status : undefined;
        // no answer, but this page showed one last time: keep painting it (the
        // banner says it is old) rather than swap it for an error
        if (status === 0 && peek(url) !== undefined) {
          setState((s) => (s.error == null && !s.loading ? s : { ...s, error: null, loading: false }));
          return;
        }
        setState({ data: null, error: e.message, loading: false, url, status });
      },
    );
    return () => {
      live = false;
    };
  }, [url, version]);
  return current;
}

export type { StatResult };

// G6: writes. The local server only accepts a write that carries this header,
// which a page on another site cannot add without the server's consent.
// serve.py's NO_CHANGE (G17: generated from it)
const NO_CHANGE = NO_CHANGE_PATHS;

/**
 * The views serve.py's REREAD_AFTER asks again for after a write to `path`, or
 * undefined. A key may end in a route's `<id>` (`/api/dashboard/layouts/<id>`),
 * which stands for one segment of the path, as serve.py routes it.
 */
export function rereadAfter(path: string): readonly string[] | undefined {
  for (const [route, views] of Object.entries(REREAD_AFTER)) {
    const at = route.indexOf("<");
    if (route === path) return views;
    if (at > 0 && path.length > at && path.startsWith(route.slice(0, at)) && !path.slice(at).includes("/")) return views;
  }
  return undefined;
}

export async function postJSON<T>(url: string, body?: BodyInit, contentType = "application/json"): Promise<T> {
  const r = await reach(url, { method: "POST", body, headers: { "X-Kopifund": "1", "Content-Type": contentType } }, true);
  // Audit: a refusal goes through `failure` like every other answer, so a gateway's 502 is no answer
  // whatever its body says
  if (!r.ok) throw await failure(r, true, await readText(r, true).catch(() => ""));
  // the body read once, as text. Empty is an answer with nothing to say; text that is not JSON fails,
  // after every view is asked again, since the write may have landed. So does a connection that drops
  // while the body comes: the server answers only once the write is done, so it is not "not saved"
  let text: string;
  try {
    text = await r.text();
  } catch {
    notifyChanged();
    throw new ApiError(UNREADABLE_WRITE, r.status);
  }
  let data: unknown = {};
  if (text.trim() !== "") {
    try {
      data = parse(text, r.status, true);
    } catch (e) {
      notifyChanged();
      throw e;
    }
  }
  // a check, a suggestion and a projection preview (G14) change nothing, so no view needs to reload.
  // G18: but a check moves the upload history, which the overview's Needs your eye reads: the views
  // serve.py's REREAD_AFTER names ask again (with their tags), and nothing kept is forgotten. G26: a
  // dashboard layout save is one of those too, so a drag does not ask every widget afresh. A load
  // or a mirror is a write, so every view reloads after it anyway.
  const reread = rereadAfter(url.split("?")[0]);
  if (reread) {
    refreshViews(reread);
    // the app's other tabs ask those views again too: a Dashboard open in a
    // second window would otherwise edit, and save, a board this one changed
    try {
      storage()?.setItem(REFRESH_KEY, JSON.stringify({ paths: reread, at: Date.now() }));
    } catch {
      /* storage refused: other tabs catch up when they get focus */
    }
  }
  else if (!NO_CHANGE.some((p) => url.startsWith(p))) notifyChanged();
  if ((data as { stats?: string }).stats === "rebuilding") watchStatsRebuild();
  return data as T;
}

// A category edit rebuilds the stat results in the background
// (finance/stats_rebuild.py, refresh_stats). Ask until it is done, then announce a change once more, so
// a stat page opened meanwhile shows the new figures rather than the old ones.
// One watch at a time, whatever page started it; it gives up after two minutes.
let watching = false;
// Audit: which watch is the live one; a reset (a test's) moves it on, so a watch already under way stops
let watchRun = 0;
export function watchStatsRebuild(every = 1500, tries = 80) {
  if (watching) return;
  watching = true;
  const run = watchRun;
  const ask = (left: number) => {
    // a page torn down meanwhile (a test's, or the window closing) has nothing left to tell
    if (typeof window === "undefined") {
      watching = false;
      return;
    }
    window.setTimeout(async () => {
      if (run !== watchRun) return;
      if (typeof window === "undefined") {
        watching = false;
        return;
      }
      let running = true;
      try {
        running = (await getJSON<{ running: boolean }>("/api/stats/rebuild")).running;
      } catch {
        // a question that failed says nothing about the rebuild: ask again
        running = true;
      }
      if (run !== watchRun) return;
      if (running && left > 1) {
        ask(left - 1);
        return;
      }
      watching = false;
      notifyChanged();
    }, every);
  };
  ask(tries);
}

export type RunStatus = {
  folders: Folder[];
  notion_enabled: boolean;
  pdftotext: boolean;
  runs: { kind: string; started_at: string; status: string; inserted: number; updated: number; archived: number }[];
};
export type ProblemRow = { code: string; subject: string; fix: string };
export type NeedsRule = { text: string; field: string; rows: number; amount: Cents; first: string; last: string; keys: string[] };
// G12: the upload page's suggestions for the payees no rule names
export type AiSuggestion = { text: string; payee: string; category: string; kind: string; confident: boolean; note: string };
export type SuggestReply = {
  enabled: boolean;
  provider?: string;
  model?: string;
  suggestions: AiSuggestion[];
  error?: string;
  categories: string[];
  drafts: Record<string, Suggestion>;   // the rule-from-text guess (G7) for every payee
};
export type CheckReport = {
  problems: ProblemRow[];
  statements: number;
  rows: number;
  to_add: { key: string; date: string; amount: Cents; currency: string; account: string; text: string; payee: string; category: string }[];
  to_add_count: number;
  recategorised_count: number;
  to_archive_count: number;
  /** rows the load will give a new key, keeping their id (an older report has none) */
  rekeyed_count?: number;
  needs_rule: NeedsRule[];
};
export type LoadResult = {
  loaded: boolean;
  report: CheckReport;
  counts?: { inserted: number; updated: number; archived: number; "trips assigned": number };
  stats?: { stats: number; transactions: number };
};

// G7: rules
export type PayeeCategory = { payee: string; category: string };
export type RulePreview = {
  error: string | null;
  file?: string;
  override?: string | null;
  matches?: number;
  wins?: number;
  already?: number;
  changes?: number;
  sample?: (RuleRow & { before: PayeeCategory; after: PayeeCategory })[];
  beaten_by?: { file: string; tier: string; pattern: string; payee: string; category: string; rows: number }[];
};
// G13: a rule or trip write relinks the rows it decides differently at once, as a load would:
// `changed` is how many rows' payee, category or trip moved
export type Relinked = { changed: number; written?: number };

/** "14 rows changed", "1 row changed", "no rows changed". */
export function rowsChanged(r?: Relinked | null, back = false): string {
  const n = r?.changed ?? 0;
  const what = n === 0 ? "no rows" : n === 1 ? "1 row" : `${n.toLocaleString("en-SG")} rows`;
  return `${what} changed${back && n ? " back" : ""}`;
}

// `change` is the undo history's token for the write (never reused), which POST /api/rules/undo {change} names
export type RuleSavedEdit = {
  file: string; line: number; override?: string | null; summary?: string; unchanged?: boolean; change?: string | null;
  relinked?: Relinked;
};
// G13: what a pin set (POST /api/rules/pin answers it), as a row's `pinned` reads
export type PinSet = { payee: string; kind: string; country: string; category: string };
// what a rule write's Undo answers: the write it took back and the rows that went back with it
export type RuleUndone = { undone: string; relinked?: Relinked };
// G9: export
export type ExportDone = { folder: string; stamp: string; files: string[]; transactions: number };
/** G23: POST /api/server/stop or /restart: the work the exit waits for, in words, and the most it waits (seconds). */
export type ServerEnding = { ok: boolean; waiting_for: string[]; timeout: number };
export type Exports = { exports: { stamp: string; files: string[] }[] };

// G10: first run and profile
// G21: `bank` marks it and `title` names its account (a wallet's tile), both from the layout registry
/** G21: how far a layout has been checked, in the layout registry's words (finance/parsers/layouts.py). */
export type LayoutStatus = "checked on a real statement" | "from a public sample";
export const PUBLIC_SAMPLE: LayoutStatus = "from a public sample";
export type Provider = { id: string; label: string; parser: string; wallet?: string; bank: Bank; title: string; status: LayoutStatus };
/** G21: a bank whose statements Kopifund reads, for a statement it does not (the Upload page). */
export type SupportedBank = { bank: Bank; name: string; status: LayoutStatus };
export type SetupState = { needed: boolean; data_folder: string; providers: Provider[] };
// the upload history (GET /api/uploads, /api/uploads/<id>): an attempt with its
// steps' outline in the list, or with each step in full for one attempt
export type UploadAttempt<S = UploadStepOutline> = UploadAttemptBase & { steps: S[] };

// G8: the Net worth page. Every amount is integer cents.
export type NetWorthPart = "cash" | "investments" | "cpf";
export type NetWorthMonth = {
  month: string;
  total: Cents;
  cash?: Cents; cash_as_of?: string;
  investments?: Cents; investments_as_of?: string;
  cpf?: Cents; cpf_as_of?: string;
};
export type NetWorth = { parts: NetWorthPart[]; months: NetWorthMonth[] };

export type SpendHint = { per_month: Cents; from: string; to: string; months: number };

// G13: figures the record already knows, offered as chips beside the inputs (finance/suggestions.py)
export type IncomeSuggestion = {
  name: string; type: "employment" | "side"; from: string; last: string; take_home: Cents; gross: Cents; cpf: boolean;
  // gross: take-home with the employee's CPF added back; take_home: the statement's figure as it stands; side: side income
  basis: "gross" | "take_home" | "side"; bonus_months: number | null; bonus_month: number | null;
};
export type Suggestions = {
  income: IncomeSuggestion[];
  investing: { per_month: Cents; months: number; from: string; to: string } | null;
  // a fraction (0.17), from the date of birth on the profile
  cpf_employer_rate: number | null;
  insurance: { per_month: Cents; series: number } | null;
  repayments: { per_month: Cents; series: number } | null;
};

export type GrowthAssumptions = {
  pessimistic_pct: string; expected_pct: string; optimistic_pct: string;
  inflation_pct: string; salary_growth_pct: string; sums_growth_pct: string; spend_growth_pct: string;
  spend_growth_follows_inflation: boolean;
  horizon: Horizon;
  dollars: Dollars;
  spend: Cents; spend_from: "typed" | "average" | "none";
  milestone: Cents | null;
  investing_now: Cents;
};
export type TigerRow = { month: string; pessimistic: Cents; expected: Cents; optimistic: Cents; put_in: Cents };
export type CpfRow = {
  month: string; age: number; oa: Cents; sa: Cents | null; ra: Cents | null; ma: Cents; total: Cents;
  frs: Cents | null;          // the cohort FRS from 55, in the same dollars as the balances
};
export type CashRow = {
  month: string; cash: Cents; net: Cents; income: Cents; employment: Cents; bonus: Cents; side: Cents;
  cpf_employee: Cents; tax: Cents; medisave: Cents; spend: Cents; investing: Cents;
};
export type TotalPart = "cash" | "tiger" | "cpf";
export type TotalRow = {
  month: string; age: number | null; cash: Cents; tiger: Cents | null; cpf: Cents | null; cpf_held: boolean;
  total: Cents; low: Cents; high: Cents;
};
export type Growth = {
  today: string;
  end: string;
  birth_date: string | null;
  age: number | null;
  inputs: GrowthInputs;
  spend_hint: SpendHint | null;
  assumptions: GrowthAssumptions;
  can_reach_65: boolean;
  markers: { age: number; month: string }[];
  rules: {
    as_of: string; source: string; ow_ceiling: Cents; bhs: Cents; frs: Cents; sums_year: number;
    tax: { as_of: string; source: string }; medisave: { as_of: string; source: string };
  };
  tax_years: { year: number; assessable: Cents; reliefs: Cents; chargeable: Cents; tax: Cents; cpf_employee: Cents; medisave: Cents }[];
  // measured false: no statement yet, so it starts from nothing and holds only the investing steps (G14)
  // broker: the brokerage's name, read from the loaded statement's folder; null while no statement is loaded
  tiger: { as_of: string | null; measured: boolean; broker: string | null; start: Cents; contributed: Cents; rows: TigerRow[] } | null;
  cpf: {
    as_of: string;
    needs: "birth_date" | "before_55" | null;
    rows: CpfRow[];
    frs?: { year: number; month: string; value: Cents; assumed: boolean };
  } | null;
  // before_start: the tax and MediSave estimated for the start year's months up to the
  // balance, taken in the first projected month; null with no balance to start from
  cash: {
    as_of: string | null; start: Cents; measured: boolean; rows: CashRow[];
    before_start: { from: string; to: string; tax: Cents; medisave: Cents } | null;
  };
  total: {
    parts: TotalPart[];
    rows: TotalRow[];
    tiles: { today: { month: string; total: Cents } | null; in_5y: { month: string; total: Cents } | null; in_10y: { month: string; total: Cents } | null };
    milestone: { value: Cents; month: string | null; total: Cents | null } | null;
    below_zero: { from: string; to: string }[];
    low: { month: string; cash: Cents } | null;
  };
};
