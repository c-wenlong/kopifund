import { Component, type ReactNode } from "react";
import { reloadViews } from "../api";
import { buttonClass } from "./Button";
import { Problem } from "./States";

// Audit: React unmounts the whole root on a render error nothing catches, so one
// figure the client did not expect would blank the app, sidebar and all. A
// boundary holds the failure to its own part of the screen: the page (keyed by
// its address, so opening another page starts clean), the copilot's answers,
// and the whole app as the last resort. React logs each caught error to the
// console itself (onCaughtError), so this only draws the fallback.

type Props = {
  /** A change here clears the failure: the address, for a page. */
  resetKey?: unknown;
  /** What stands in for the part that failed; `reset` draws it again. */
  fallback: (reset: () => void) => ReactNode;
  children: ReactNode;
};

export class ErrorBoundary extends Component<Props, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidUpdate(prev: Props) {
    if (this.state.failed && !Object.is(prev.resetKey, this.props.resetKey)) this.setState({ failed: false });
  }

  reset = () => this.setState({ failed: false });

  render() {
    return this.state.failed ? this.props.fallback(this.reset) : this.props.children;
  }
}

export const PAGE_FAILED = "Something on this page failed to draw.";

/**
 * A page that threw: the shell stays, and Try again draws it anew from the server's answers. The kept
 * answers are forgotten first, since the one the page threw on would otherwise be read again (the page
 * is not mounted, so nothing asks for it afresh) and throw again however the server answers now.
 */
export function PageBoundary({ path, children }: { path: string; children: ReactNode }) {
  return (
    <ErrorBoundary resetKey={path} fallback={(reset) => (
      <Problem message={PAGE_FAILED} hint="Try again, or open another page."
        onRetry={() => {
          reloadViews();
          reset();
        }} />
    )}>
      {children}
    </ErrorBoundary>
  );
}

export const APP_FAILED = "Kopifund could not draw this screen.";

/** The last resort, around the whole app: nothing of the shell is left to keep, so the way back is a reload. */
export function AppBoundary({ children }: { children: ReactNode }) {
  return (
    <ErrorBoundary fallback={() => (
      <div className="grid min-h-dvh place-items-center bg-page px-4 text-ink">
        <div role="alert" className="flex max-w-sm flex-col items-start gap-2 rounded-xl border border-line bg-panel px-5 py-4">
          <p className="m-0 text-[15px] font-semibold">{APP_FAILED}</p>
          <p className="m-0 text-[14px] text-ink-2">Reload to start again.</p>
          <button type="button" onClick={() => window.location.reload()} className={`mt-1 ${buttonClass("secondary")}`}>
            Reload
          </button>
        </div>
      </div>
    )}>
      {children}
    </ErrorBoundary>
  );
}
