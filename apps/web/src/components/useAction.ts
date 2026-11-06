import { useCallback, useState } from "react";
import { showToast } from "./Toast";

// Audit: a button that writes to the record, once for every card. `run(key, fn)`
// marks `key` busy while `fn` runs (so its button can turn off and say so), and a
// failure lands in one of three places: `failed` (the default, drawn beside the
// button that was pressed: `failed.key` says which), an error toast (`toast`, for
// a write whose button may have scrolled away), or the caller's own `onError`
// (a row that reports to its card). A new run clears the stored failure.
// `run` answers whether `fn` finished without throwing.

export type ActionFailure = { key: string; message: string };
export type RunOptions = { toast?: boolean; onError?: (message: string) => void };

export function useAction() {
  const [busy, setBusy] = useState<string | null>(null);
  const [failed, setFailed] = useState<ActionFailure | null>(null);
  const run = useCallback(async (key: string, fn: () => Promise<unknown>, options: RunOptions = {}): Promise<boolean> => {
    setBusy(key);
    setFailed(null);
    try {
      await fn();
      return true;
    } catch (e) {
      const message = (e as Error).message;
      if (options.onError) options.onError(message);
      else if (options.toast) showToast({ tone: "error", message });
      else setFailed({ key, message });
      return false;
    } finally {
      setBusy(null);
    }
  }, []);
  const clear = useCallback(() => setFailed(null), []);
  return { busy, running: busy !== null, failed, error: failed?.message ?? null, run, clear };
}
