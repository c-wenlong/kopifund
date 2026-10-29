import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, clearApiCache, getJSON, KEEP_FOR, KEPT_KEY, OFFLINE, postJSON, restoreKept, UNREADABLE, UNREADABLE_WRITE, useApi, watchStatsRebuild } from "./api";

// Audit: an answer the app cannot read says so plainly, and a write whose answer cannot be
// read is not reported as saved.

const html = (status = 200) => new Response("<html>Sign in to the network</html>", { status, headers: { "Content-Type": "text/html" } });
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });

afterEach(() => vi.unstubAllGlobals());

describe("postJSON", () => {
  it("fails a success whose body is not JSON, rather than resolving it as {}", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => html()));
    const asked = vi.fn();
    window.addEventListener("kopifund:changed", asked);
    await expect(postJSON("/api/trips", "{}")).rejects.toMatchObject({ message: UNREADABLE_WRITE, status: 200 });
    // the write may have landed, so the views ask again: what they then show is the truth
    expect(asked).toHaveBeenCalled();
    window.removeEventListener("kopifund:changed", asked);
  });

  it("takes an empty success as nothing to report", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(null, { status: 200 })));
    await expect(postJSON("/api/trips")).resolves.toEqual({});
  });

  it("says the server's own words for a refusal, and the status for one without words", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json({ error: "That trip overlaps another" }, 400)));
    await expect(postJSON("/api/trips")).rejects.toMatchObject({ message: "That trip overlaps another", status: 400 });
    vi.stubGlobal("fetch", vi.fn(async () => new Response("<h1>Bad Request</h1>", { status: 400, statusText: "Bad Request" })));
    await expect(postJSON("/api/trips")).rejects.toMatchObject({ message: "400 Bad Request", status: 400 });
  });

  it("treats a gateway's answer as no answer, words or not", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json({ error: "upstream timed out" }, 502)));
    await expect(postJSON("/api/trips")).rejects.toMatchObject({ message: expect.stringMatching(/^Can't reach Kopifund, so that was not saved/), status: 0 });
  });

  it("carries the write header", async () => {
    const f = vi.fn(async (_u: string, _i?: RequestInit) => json({ ok: true }));
    vi.stubGlobal("fetch", f);
    await postJSON("/api/trips", "{}");
    expect((f.mock.calls[0][1]?.headers as Record<string, string>)["X-Kopifund"]).toBe("1");
  });
});

describe("getJSON", () => {
  it("says an answer could not be read, not the parser's words", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => html()));
    const e = await getJSON("/api/run/status").catch((x: unknown) => x);
    expect(e).toBeInstanceOf(ApiError);
    expect((e as ApiError).message).toBe(UNREADABLE);
    expect((e as ApiError).message).not.toMatch(/Unexpected token/);
  });

  it("does the same for a cached view", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => html()));
    const { result } = renderHook(() => useApi("/api/overview"));
    await waitFor(() => expect(result.current.error).toBe(UNREADABLE));
  });

  it("still says OFFLINE when nothing answers", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("Failed to fetch"); }));
    await expect(getJSON("/api/run/status")).rejects.toMatchObject({ message: OFFLINE });
  });
});

// Audit: the copy kept for a reload holds the summaries a page paints first, never the ledger's rows or
// the profile, and nothing older than KEEP_FOR
describe("the copy kept in localStorage", () => {
  const kept = () => window.localStorage.getItem(KEPT_KEY) ?? "";

  it("keeps a summary but not transactions or the profile", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      vi.stubGlobal("fetch", vi.fn(async (url: string) => json({ url })));
      const a = renderHook(() => useApi("/api/overview"));
      const b = renderHook(() => useApi("/api/transactions?q=kopi"));
      const c = renderHook(() => useApi("/api/profile"));
      await waitFor(() => expect([a, b, c].every((h) => h.result.current.data)).toBe(true));
      await vi.advanceTimersByTimeAsync(1000);
      expect(kept()).toContain("/api/overview");
      expect(kept()).not.toContain("/api/transactions");
      expect(kept()).not.toContain("/api/profile");
    } finally {
      vi.useRealTimers();
    }
  });

  it("drops what was last read longer ago than it keeps answers", async () => {
    const key = KEPT_KEY;
    const now = Date.now();
    window.localStorage.setItem(key, JSON.stringify([
      ["/api/overview", null, JSON.stringify({ fresh: true }), now - 1000],
      ["/api/spending", null, JSON.stringify({ old: true }), now - KEEP_FOR - 1000],
    ]));
    restoreKept();
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(() => {})));
    const fresh = renderHook(() => useApi("/api/overview"));
    const old = renderHook(() => useApi("/api/spending"));
    expect(fresh.result.current.data).toEqual({ fresh: true });
    expect(old.result.current.data).toBeNull();
  });
});

// Audit: a test that ends mid-rebuild used to leave the watch on, so the next test's watch never started
describe("test isolation", () => {
  it("lets a new stats watch start after a reset", async () => {
    let asked = 0;
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (url === "/api/stats/rebuild") asked += 1;
      return json({ running: true });
    }));
    watchStatsRebuild(10, 1000);
    clearApiCache();
    asked = 0;
    watchStatsRebuild(10, 1);
    await waitFor(() => expect(asked).toBeGreaterThan(0));
    // the first watch stopped at the reset: only the second one asked, once
    await new Promise((r) => setTimeout(r, 60));
    expect(asked).toBe(1);
  });
});
