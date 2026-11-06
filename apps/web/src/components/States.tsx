import type { ReactNode } from "react";
import { CircleAlert, LoaderCircle } from "lucide-react";
import { OFFLINE, revalidate, type Loaded as Answer } from "../api";
import { onLinkClick } from "../router";
import { buttonClass } from "./Button";
import { EmptyState } from "./EmptyState";

export function Loading({ what = "Loading" }: { what?: string }) {
  return (
    <p className="m-0 flex items-center gap-2 text-[14px] text-ink-3" role="status">
      <LoaderCircle size={16} className="animate-spin" aria-hidden /> {what}
    </p>
  );
}

/**
 * G13: a card-shaped block of fixed height where a card is on its way, so the
 * page does not jump when the figures arrive. `still` is the same block with
 * nothing coming (the server is down; the banner says so).
 */
export function CardSkeleton({ height = 260, what, still = false }: { height?: number; what?: string; still?: boolean }) {
  const pulse = still ? "" : "motion-safe:animate-pulse";
  return (
    <section aria-busy={!still} className="rounded-xl border border-line bg-panel px-4 py-3">
      {what && <p role="status" className="sr-only">{what}</p>}
      <div className={`mb-3 h-4 w-40 rounded bg-tile ${pulse}`} aria-hidden />
      <div className={`rounded-md bg-tile ${pulse}`} style={{ height }} aria-hidden />
    </section>
  );
}

/** G13: the four tiles and the chart card Home is made of, as a fixed-height placeholder. */
export function TilesSkeleton({ what }: { what: string }) {
  return (
    <div className="flex flex-col gap-3" aria-busy>
      <p role="status" className="sr-only">{what}</p>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4" aria-hidden>
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="h-[88px] rounded-xl border border-line bg-panel motion-safe:animate-pulse" />
        ))}
      </div>
      <CardSkeleton height={260} />
    </div>
  );
}

/** Whether a load failed only because the server did not answer. */
export const isOffline = (message: string) => message === OFFLINE;

/**
 * A problem, in plain words. When the server is simply not there the app
 * banner says so once (ServerBanner), so here the card stays a quiet placeholder
 * instead of every card repeating the browser's message. `retry` adds a Try
 * again button for a failed load; it asks the page's figures again. `onRetry`
 * gives the button its own action instead (an error boundary drawing anew).
 */
export function Problem({ message, hint, retry = false, onRetry }: {
  message: string; hint?: string; retry?: boolean; onRetry?: () => void;
}) {
  if (isOffline(message)) return <CardSkeleton height={120} still />;
  return (
    <div className="rounded-lg bg-warn-bg px-4 py-3 text-[14px] text-warn" role="alert">
      <p className="m-0 flex items-center gap-2"><CircleAlert size={16} className="shrink-0" aria-hidden /> {message}</p>
      {hint && <p className="m-0 mt-1 text-[13px]">{hint}</p>}
      {(retry || onRetry) && (
        <button type="button" onClick={onRetry ?? revalidate}
          className="mt-2 rounded-md border border-warn/40 px-2.5 py-1 text-[13px] text-warn hover:bg-hover">
          Try again
        </button>
      )}
    </div>
  );
}

/** "Nothing there yet" is only a 404; any other failure says what went wrong. */
export function NoResult({ status, error, what, hint = "Run the analysis from the Upload page." }: {
  status?: number; error: string | null; what: string; hint?: string;
}) {
  if (error && status !== 404) return <Problem message={error} retry />;
  return <Problem message={`No ${what} result yet.`} hint={hint} />;
}

/**
 * Audit: a view's first answer, once for every view. While it is on its way,
 * Loading says `what`; when it failed, or came back with nothing, the problem
 * (or `missing`) with Try again; once it is here, `children` draws it.
 * `className` wraps the first two where the view sits inside a padded box.
 */
export function Loaded<T>({ state, what, missing = "Nothing to show", hint, className, children }: {
  state: Answer<T>; what: string; missing?: string; hint?: string; className?: string;
  children: (data: T) => ReactNode;
}) {
  const wrap = (n: ReactNode) => (className ? <div className={className}>{n}</div> : n);
  if (state.loading && !state.data) return wrap(<Loading what={what} />);
  if (state.error || !state.data) return wrap(<Problem message={state.error ?? missing} hint={hint} retry />);
  return children(state.data);
}

/** The hint under a page that has nothing to show before any statement is loaded. */
export const LOAD_FIRST = "Load your statements first, from the Upload page.";

/**
 * Audit: the record holds no statement yet, so a page has nothing to draw:
 * its `title`, what comes with the first statements, and the way to Upload.
 */
export function NoStatementsYet({ title, what }: { title: string; what: string }) {
  return (
    <EmptyState title={title}
      action={<a href="/upload" onClick={onLinkClick("/upload")} className={buttonClass("secondary")}>Load your statements</a>}>
      {what} come with your first statements.
    </EmptyState>
  );
}
