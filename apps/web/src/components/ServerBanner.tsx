import { inDesktopShell } from "../serviceWorker";
import { useEffect, useState } from "react";
import { CircleAlert } from "lucide-react";
import { checkServer, useLink } from "../api";

// G13: one banner for "the server is not there", in place of a raw browser
// message on every card. It asks again every few seconds, and the Retry button
// asks at once; when the server answers, every page revalidates (api.ts, markUp).
const EVERY_MS = 5000;

export function ServerBanner() {
  const { down, stale } = useLink();
  const [checking, setChecking] = useState(false);
  const [tried, setTried] = useState(false);

  useEffect(() => {
    if (!down) {
      setTried(false);
      return;
    }
    const timer = window.setInterval(() => void checkServer(), EVERY_MS);
    return () => window.clearInterval(timer);
  }, [down]);

  if (!down) return null;

  async function retry() {
    setChecking(true);
    const up = await checkServer();
    setChecking(false);
    setTried(!up);
  }

  return (
    <div role="alert" className="sticky top-0 z-10 mb-3 flex flex-wrap items-center gap-x-3 gap-y-2 rounded-lg border border-line bg-warn-bg px-4 py-2.5 text-[14px] text-warn">
      <CircleAlert size={16} className="shrink-0" aria-hidden />
      <p className="m-0 min-w-0 flex-1">
        {/* Audit: words a user can act on, never a command: the Mac app starts its own server, while a
            browser or the installed web app reaches one started on the computer that runs Kopifund */}
        {inDesktopShell()
          ? "Can't reach Kopifund. Quit and reopen Kopifund, then Retry."
          : "Can't reach Kopifund. Start it again on the computer that runs it, then Retry."}
        {stale && " Showing figures from your last visit."}
        {tried && " Still no answer."}
      </p>
      <button type="button" onClick={() => void retry()} disabled={checking}
        className="rounded-md border border-warn/40 px-2.5 py-1 text-[13px] hover:bg-hover disabled:opacity-60">
        {checking ? "Checking…" : "Retry"}
      </button>
    </div>
  );
}
