import { renderHook, waitFor, act } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MAX_KEPT, OFFLINE, REVALIDATE_EVERY, getJSON, notifyChanged, postJSON, prefetch, rereadAfter, resetLink, revalidate, useApi, useLink } from "./api";

describe("useApi", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("reloads after any successful write, keeping the old data on screen meanwhile", async () => {
    let n = 0;
    vi.stubGlobal("fetch", vi.fn(async (_url: string, init?: RequestInit) =>
      new Response(JSON.stringify(init?.method === "POST" ? { ok: true } : { n: ++n }), { status: 200 })));
    const { result } = renderHook(() => useApi<{ n: number }>("/api/overview"));
    await waitFor(() => expect(result.current.data).toEqual({ n: 1 }));
    await act(async () => { await postJSON("/api/run/load"); });
    expect(result.current.loading).toBe(false);
    await waitFor(() => expect(result.current.data).toEqual({ n: 2 }));
  });

  it("reloads once more when the stats rebuild a category edit started is done", async () => {
    let n = 0;
    let asked = 0;
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method === "POST") return new Response(JSON.stringify({ stats: "rebuilding" }), { status: 200 });
      if (url === "/api/stats/rebuild") return new Response(JSON.stringify({ running: ++asked < 2 }), { status: 200 });
      return new Response(JSON.stringify({ n: ++n }), { status: 200 });
    }));
    const { result } = renderHook(() => useApi<{ n: number }>("/api/results/category-breakdown"));
    await waitFor(() => expect(result.current.data).toEqual({ n: 1 }));
    await act(async () => { await postJSON("/api/categories/move"); });
    await waitFor(() => expect(result.current.data).toEqual({ n: 2 }));        // the edit itself
    await waitFor(() => expect(asked).toBe(2), { timeout: 5000 });              // still running, then done
    await waitFor(() => expect(result.current.data).toEqual({ n: 3 }));        // the rebuilt results
  });

  it("asks again when asking about the rebuild fails, rather than taking it as done", async () => {
    let n = 0;
    let asked = 0;
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method === "POST") return new Response(JSON.stringify({ stats: "rebuilding" }), { status: 200 });
      if (url === "/api/stats/rebuild") {
        asked += 1;
        return asked === 1 ? new Response("{}", { status: 500 }) : new Response(JSON.stringify({ running: false }), { status: 200 });
      }
      return new Response(JSON.stringify({ n: ++n }), { status: 200 });
    }));
    const { result } = renderHook(() => useApi<{ n: number }>("/api/overview"));
    await waitFor(() => expect(result.current.data).toEqual({ n: 1 }));
    await act(async () => { await postJSON("/api/categories/rename"); });
    await waitFor(() => expect(result.current.data).toEqual({ n: 2 }));
    await waitFor(() => expect(asked).toBe(2), { timeout: 5000 });
    await waitFor(() => expect(result.current.data).toEqual({ n: 3 }));
  });

  it("after a check asks again only for the overview, whose Needs your eye shows the upload's state (G18)", async () => {
    const asked: string[] = [];
    let n = 0;
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method === "POST") return new Response("{}", { status: 200 });
      asked.push(url);
      return new Response(JSON.stringify({ n: ++n }), { status: 200 });
    }));
    const overview = renderHook(() => useApi<{ n: number }>("/api/overview"));
    const rules = renderHook(() => useApi<{ n: number }>("/api/rules"));
    await waitFor(() => expect(overview.result.current.data).not.toBeNull());
    await waitFor(() => expect(rules.result.current.data).not.toBeNull());
    const rulesBefore = rules.result.current.data;
    asked.length = 0;
    await act(async () => { await postJSON("/api/run/check"); });
    await waitFor(() => expect(asked).toEqual(["/api/overview"]));
    expect(rules.result.current.data).toBe(rulesBefore);
    // nothing else was forgotten: a page opened now paints its kept answer at once
    const again = renderHook(() => useApi<{ n: number }>("/api/rules"));
    expect(again.result.current.data).toEqual(rulesBefore);
  });

  it("after a dashboard layout save asks again only for the layouts, so no widget is asked afresh (G26)", async () => {
    const asked: string[] = [];
    let n = 0;
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method === "POST") return new Response("{}", { status: 200 });
      asked.push(url);
      return new Response(JSON.stringify({ n: ++n }), { status: 200 });
    }));
    const layouts = renderHook(() => useApi<{ n: number }>("/api/dashboard/layouts"));
    const widget = renderHook(() => useApi<{ n: number }>("/api/dashboard/query?q=1"));
    await waitFor(() => expect(layouts.result.current.data).not.toBeNull());
    await waitFor(() => expect(widget.result.current.data).not.toBeNull());
    const widgetBefore = widget.result.current.data;
    for (const write of ["/api/dashboard/layouts/main", "/api/dashboard/layouts"]) {
      asked.length = 0;
      await act(async () => { await postJSON(write, "{}"); });
      await waitFor(() => expect(asked).toEqual(["/api/dashboard/layouts"]));
      expect(widget.result.current.data).toBe(widgetBefore);
    }
    // the widget's answer is still kept: a board opened now paints it at once
    expect(renderHook(() => useApi<{ n: number }>("/api/dashboard/query?q=1")).result.current.data).toEqual(widgetBefore);
  });

  it("tells the app's other tabs to ask again for the layouts after a save, and asks when another tab says so", async () => {
    const asked: string[] = [];
    let n = 0;
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method === "POST") return new Response("{}", { status: 200 });
      asked.push(url);
      return new Response(JSON.stringify({ n: ++n }), { status: 200 });
    }));
    await act(async () => { await postJSON("/api/dashboard/layouts/main", "{}"); });
    expect(JSON.parse(window.localStorage.getItem("kopifund:api-refresh")!).paths).toEqual(["/api/dashboard/layouts"]);
    const layouts = renderHook(() => useApi<{ n: number }>("/api/dashboard/layouts"));
    const widget = renderHook(() => useApi<{ n: number }>("/api/dashboard/query?q=2"));
    await waitFor(() => expect(layouts.result.current.data).not.toBeNull());
    await waitFor(() => expect(widget.result.current.data).not.toBeNull());
    asked.length = 0;
    // another tab saved: the storage event names the views to ask again
    act(() => {
      window.dispatchEvent(new StorageEvent("storage", {
        key: "kopifund:api-refresh", newValue: JSON.stringify({ paths: ["/api/dashboard/layouts"], at: 1 }),
      }));
    });
    await waitFor(() => expect(asked).toEqual(["/api/dashboard/layouts"]));
  });

  it("matches a write's route by its <id> segment and nothing longer", () => {
    expect(rereadAfter("/api/dashboard/layouts/main")).toEqual(["/api/dashboard/layouts"]);
    expect(rereadAfter("/api/dashboard/layouts")).toEqual(["/api/dashboard/layouts"]);
    expect(rereadAfter("/api/dashboard/layouts/main/extra")).toBeUndefined();
    expect(rereadAfter("/api/dashboard/layouts/")).toBeUndefined();
    expect(rereadAfter("/api/run/load")).toBeUndefined();
  });

  it("asks afresh after a check even when an overview asked before it is still on its way (G18)", async () => {
    // the first overview answer is held back until after the check, and holds the state from before it
    let release: (r: Response) => void = () => {};
    let overviews = 0;
    vi.stubGlobal("fetch", vi.fn((url: string, init?: RequestInit) => {
      if (init?.method === "POST") return Promise.resolve(new Response("{}", { status: 200 }));
      if (url.startsWith("/api/overview") && ++overviews === 1) return new Promise<Response>((r) => { release = r; });
      return Promise.resolve(new Response(JSON.stringify({ n: overviews }), { status: 200 }));
    }));
    const { result } = renderHook(() => useApi<{ n: number }>("/api/overview?before-check"));
    await waitFor(() => expect(overviews).toBe(1));
    await act(async () => { await postJSON("/api/run/check"); });
    // the pending ask is not reused: a second one goes out
    await waitFor(() => expect(overviews).toBe(2));
    await waitFor(() => expect(result.current.data).toEqual({ n: 2 }));
    // the old answer arriving late is neither shown nor kept
    await act(async () => { release(new Response(JSON.stringify({ n: 1 }), { status: 200 })); });
    expect(result.current.data).toEqual({ n: 2 });
    const later = renderHook(() => useApi<{ n: number }>("/api/overview?before-check"));
    expect(later.result.current.data).toEqual({ n: 2 });
  });

  it("does not ask again after a write that changes nothing the overview reads (a suggestion)", async () => {
    let n = 0;
    vi.stubGlobal("fetch", vi.fn(async (_url: string, init?: RequestInit) =>
      new Response(JSON.stringify(init?.method === "POST" ? {} : { n: ++n }), { status: 200 })));
    const { result } = renderHook(() => useApi<{ n: number }>("/api/overview"));
    await waitFor(() => expect(result.current.data).toEqual({ n: 1 }));
    await act(async () => { await postJSON("/api/rules/suggest"); });
    expect(result.current.data).toEqual({ n: 1 });
  });
});

// G13: the cache. A served answer carries an ETag; asking again with it gets a 304.
describe("useApi's cache", () => {
  afterEach(() => vi.unstubAllGlobals());

  // a server whose answer to every GET is {n}, tagged with n; `bump` is a write
  function server() {
    const s = { n: 1, asked: [] as { url: string; tag: string | null }[] };
    const fetch = vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        s.n += 1;
        return new Response("{}", { status: 200 });
      }
      const tag = (init?.headers as Record<string, string> | undefined)?.["If-None-Match"] ?? null;
      s.asked.push({ url, tag });
      if (tag === `"${s.n}"`) return new Response(null, { status: 304, headers: { ETag: tag } });
      return new Response(JSON.stringify({ n: s.n, url }), { status: 200, headers: { ETag: `"${s.n}"` } });
    });
    vi.stubGlobal("fetch", fetch);
    return s;
  }

  it("paints a page opened again from its last answer in the first render, then asks with the tag", async () => {
    const s = server();
    const one = renderHook(() => useApi<{ n: number }>("/api/net-worth"));
    expect(one.result.current.loading).toBe(true);
    await waitFor(() => expect(one.result.current.data).toEqual({ n: 1, url: "/api/net-worth" }));
    const shown = one.result.current.data;
    one.unmount();

    const again = renderHook(() => useApi<{ n: number }>("/api/net-worth"));
    // the very first render: no loading state, the same object
    expect(again.result.current).toMatchObject({ loading: false, error: null });
    expect(again.result.current.data).toBe(shown);
    await waitFor(() => expect(s.asked).toHaveLength(2));
    expect(s.asked[1]).toEqual({ url: "/api/net-worth", tag: '"1"' });
    // a 304 keeps the very object on screen
    await act(async () => { await new Promise((r) => setTimeout(r, 10)); });
    expect(again.result.current.data).toBe(shown);
  });

  it("replaces the painted answer when the server has a newer one", async () => {
    const s = server();
    const one = renderHook(() => useApi<{ n: number }>("/api/trips"));
    await waitFor(() => expect(one.result.current.data?.n).toBe(1));
    one.unmount();
    s.n = 2;                                         // a `make load` outside the app
    const again = renderHook(() => useApi<{ n: number }>("/api/trips"));
    expect(again.result.current.data?.n).toBe(1);
    await waitFor(() => expect(again.result.current.data?.n).toBe(2));
  });

  it("forgets every kept answer on a write, so no page shows a figure from before it", async () => {
    const s = server();
    const home = renderHook(() => useApi<{ n: number }>("/api/overview"));
    const other = renderHook(() => useApi<{ n: number }>("/api/recurring"));
    await waitFor(() => expect(other.result.current.data?.n).toBe(1));
    other.unmount();
    await act(async () => { await postJSON("/api/trips"); });
    // the mounted view reloads, keeping its old figures meanwhile
    await waitFor(() => expect(home.result.current.data?.n).toBe(2));
    // a page opened after the write waits for the new figures rather than paint the old
    const reopened = renderHook(() => useApi<{ n: number }>("/api/recurring"));
    expect(reopened.result.current).toMatchObject({ data: null, loading: true });
    await waitFor(() => expect(reopened.result.current.data?.n).toBe(2));
    expect(s.asked.filter((a) => a.url === "/api/recurring").at(-1)?.tag).toBeNull();
  });

  it("never keeps an answer asked for before a write that lands while it is on its way", async () => {
    let release!: () => void;
    const held = new Promise<void>((r) => { release = r; });
    let n = 1;
    vi.stubGlobal("fetch", vi.fn(async () => {
      const mine = n;
      if (mine === 1) await held;
      return new Response(JSON.stringify({ n: mine }), { status: 200, headers: { ETag: `"${mine}"` } });
    }));
    prefetch(["/api/growth"]);                       // asked for, still on its way
    n = 2;
    notifyChanged();                                 // the write lands
    release();
    await act(async () => { await new Promise((r) => setTimeout(r, 10)); });
    const page = renderHook(() => useApi<{ n: number }>("/api/growth"));
    expect(page.result.current.data).toBeNull();
    await waitFor(() => expect(page.result.current.data?.n).toBe(2));
  });

  it("forgets on the end of a stats rebuild too", async () => {
    let n = 1;
    let asked = 0;
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method === "POST") return new Response(JSON.stringify({ stats: "rebuilding" }), { status: 200 });
      if (url === "/api/stats/rebuild") {
        asked += 1;
        if (asked === 1) return new Response(JSON.stringify({ running: true }), { status: 200 });
        n = 3;                                       // the rebuild wrote new results
        return new Response(JSON.stringify({ running: false }), { status: 200 });
      }
      return new Response(JSON.stringify({ n }), { status: 200, headers: { ETag: `"${n}"` } });
    }));
    const card = renderHook(() => useApi<{ n: number }>("/api/results/eating-out"));
    await waitFor(() => expect(card.result.current.data?.n).toBe(1));
    card.unmount();
    await act(async () => { await postJSON("/api/categories/move"); });
    await waitFor(() => expect(asked).toBe(2), { timeout: 5000 });
    const reopened = renderHook(() => useApi<{ n: number }>("/api/results/eating-out"));
    expect(reopened.result.current.data).toBeNull();
    await waitFor(() => expect(reopened.result.current.data?.n).toBe(3));
  });

  it("asks a live GET afresh every time and paints nothing kept", async () => {
    const s = server();
    const one = renderHook(() => useApi<{ n: number }>("/api/run/status"));
    await waitFor(() => expect(one.result.current.data?.n).toBe(1));
    one.unmount();
    const again = renderHook(() => useApi<{ n: number }>("/api/run/status"));
    expect(again.result.current).toMatchObject({ data: null, loading: true });
    await waitFor(() => expect(again.result.current.data?.n).toBe(1));
    expect(s.asked.map((a) => a.tag)).toEqual([null, null]);
    await act(async () => { await new Promise((r) => setTimeout(r, 500)); });   // past the save's wait
    for (const k of Object.keys(window.localStorage)) expect(window.localStorage.getItem(k)).not.toContain("/api/run/status");
  });

  it("drops a refused answer from the kept copy too, so a reload does not paint it", async () => {
    let ok = true;
    vi.stubGlobal("fetch", vi.fn(async () => ok
      ? new Response(JSON.stringify({ n: 1 }), { status: 200, headers: { ETag: '"1"' } })
      : new Response(JSON.stringify({ error: "no kopifund.db yet" }), { status: 404 })));
    const one = renderHook(() => useApi<{ n: number }>("/api/overview"));
    await waitFor(() => expect(one.result.current.data?.n).toBe(1));
    await act(async () => { await new Promise((r) => setTimeout(r, 500)); });   // saved
    one.unmount();
    ok = false;
    const again = renderHook(() => useApi<{ n: number }>("/api/overview"));
    await waitFor(() => expect(again.result.current.error).toBe("no kopifund.db yet"));
    // a reload straight after: the module starts again from the kept copy
    vi.resetModules();
    const fresh = await import("./api");
    const reloaded = renderHook(() => fresh.useApi<{ n: number }>("/api/overview"));
    expect(reloaded.result.current).toMatchObject({ data: null, loading: true });
    await waitFor(() => expect(reloaded.result.current.error).toBe("no kopifund.db yet"));
    fresh.clearApiCache();
  });

  it("keeps at most MAX_KEPT answers in memory, the least recently read going first", async () => {
    const s = server();
    const url = (i: number) => `/api/transactions?offset=${i}`;
    const first = renderHook(() => useApi<{ n: number }>(url(0)));
    await waitFor(() => expect(first.result.current.data?.n).toBe(1));
    first.unmount();
    for (let i = 1; i <= MAX_KEPT; i++) {
      await new Promise((r) => setTimeout(r, 2));                   // each read a moment later
      prefetch([url(i)]);
      await waitFor(() => expect(s.asked).toHaveLength(i + 1));
    }
    await act(async () => { await new Promise((r) => setTimeout(r, 10)); });
    // the oldest is gone, the newest still paints at once
    expect(renderHook(() => useApi<{ n: number }>(url(0))).result.current.data).toBeNull();
    expect(renderHook(() => useApi<{ n: number }>(url(MAX_KEPT))).result.current.data?.n).toBe(1);
    expect(renderHook(() => useApi<{ n: number }>(url(1))).result.current.data?.n).toBe(1);
  });

  it("drops a kept answer the server now refuses", async () => {
    let ok = true;
    vi.stubGlobal("fetch", vi.fn(async () => ok
      ? new Response(JSON.stringify({ n: 1 }), { status: 200, headers: { ETag: '"1"' } })
      : new Response(JSON.stringify({ error: "no kopifund.db yet" }), { status: 404 })));
    const one = renderHook(() => useApi<{ n: number }>("/api/overview"));
    await waitFor(() => expect(one.result.current.data?.n).toBe(1));
    one.unmount();
    ok = false;
    const again = renderHook(() => useApi<{ n: number }>("/api/overview"));
    await waitFor(() => expect(again.result.current.error).toBe("no kopifund.db yet"));
    expect(again.result.current.data).toBeNull();
    again.unmount();
    const third = renderHook(() => useApi<{ n: number }>("/api/overview"));
    expect(third.result.current.data).toBeNull();
  });

  it("warms a page's answers ahead of it, once in a while", async () => {
    const s = server();
    prefetch(["/api/recurring", "/api/run/status"]);
    prefetch(["/api/recurring"]);
    await waitFor(() => expect(s.asked).toHaveLength(1));
    const page = renderHook(() => useApi<{ n: number }>("/api/recurring"));
    expect(page.result.current.data?.n).toBe(1);
    await act(async () => { await new Promise((r) => setTimeout(r, 10)); });
    prefetch(["/api/recurring"]);                    // confirmed moments ago: not asked again
    expect(s.asked.filter((a) => a.url === "/api/recurring")).toHaveLength(2);
  });

  it("keeps a copy in localStorage that a reload paints from, and a write removes it", async () => {
    server();
    const one = renderHook(() => useApi<{ n: number }>("/api/plan"));
    await waitFor(() => expect(one.result.current.data?.n).toBe(1));
    await act(async () => { await new Promise((r) => setTimeout(r, 500)); });   // saved after a short wait
    const key = Object.keys(window.localStorage).find((k) => k.startsWith("kopifund:api:"))!;
    expect(window.localStorage.getItem(key)).toContain("/api/plan");

    // a reload: the module starts again and reads the copy
    vi.resetModules();
    const fresh = await import("./api");
    const reloaded = renderHook(() => fresh.useApi<{ n: number }>("/api/plan"));
    expect(reloaded.result.current).toMatchObject({ data: { n: 1 }, loading: false });

    await act(async () => { await postJSON("/api/trips"); });
    expect(window.localStorage.getItem(key)).toBeNull();
    fresh.clearApiCache();
  });

  it("forgets when another tab of the app writes", async () => {
    const s = server();
    const page = renderHook(() => useApi<{ n: number }>("/api/categories"));
    await waitFor(() => expect(page.result.current.data?.n).toBe(1));
    s.n = 2;
    await act(async () => {
      window.dispatchEvent(new StorageEvent("storage", { key: "kopifund:api-changed", newValue: "1" }));
    });
    await waitFor(() => expect(page.result.current.data?.n).toBe(2));
    expect(s.asked.at(-1)?.tag).toBeNull();
  });
});

describe("useApi revalidating when the window comes back", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  // the clock jumps on, since a real wait of several seconds is not worth a test
  let clock = Date.now();
  const later = (ms: number) => {
    clock += ms;
    vi.spyOn(Date, "now").mockReturnValue(clock);
  };
  const visibility = (state: "visible" | "hidden") =>
    Object.defineProperty(document, "visibilityState", { configurable: true, get: () => state });

  function served(answers: () => Response) {
    const fetcher = vi.fn(async () => answers());
    vi.stubGlobal("fetch", fetcher);
    return fetcher;
  }

  it("asks again when the tab turns visible, and shows what changed meanwhile", async () => {
    let n = 0;
    const fetcher = served(() => new Response(JSON.stringify({ n: ++n }), { status: 200 }));
    const { result } = renderHook(() => useApi<{ n: number }>("/api/overview"));
    await waitFor(() => expect(result.current.data).toEqual({ n: 1 }));
    later(REVALIDATE_EVERY + 1);
    visibility("visible");
    await act(async () => { document.dispatchEvent(new Event("visibilitychange")); });
    await waitFor(() => expect(result.current.data).toEqual({ n: 2 }));
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(result.current.loading).toBe(false);
  });

  it("asks again when the window gets the focus", async () => {
    let n = 0;
    served(() => new Response(JSON.stringify({ n: ++n }), { status: 200 }));
    const { result } = renderHook(() => useApi<{ n: number }>("/api/overview"));
    await waitFor(() => expect(result.current.data).toEqual({ n: 1 }));
    later(REVALIDATE_EVERY + 1);
    await act(async () => { window.dispatchEvent(new Event("focus")); });
    await waitFor(() => expect(result.current.data).toEqual({ n: 2 }));
  });

  it("does not ask when the tab is hidden, or twice in a few seconds", async () => {
    let n = 0;
    const fetcher = served(() => new Response(JSON.stringify({ n: ++n }), { status: 200 }));
    const { result } = renderHook(() => useApi<{ n: number }>("/api/overview"));
    await waitFor(() => expect(result.current.data).toEqual({ n: 1 }));
    later(REVALIDATE_EVERY + 1);
    visibility("hidden");
    await act(async () => { document.dispatchEvent(new Event("visibilitychange")); });
    expect(fetcher).toHaveBeenCalledTimes(1);
    // a tab switch fires both events: one round, not two
    visibility("visible");
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
      window.dispatchEvent(new Event("focus"));
    });
    await waitFor(() => expect(result.current.data).toEqual({ n: 2 }));
    await act(async () => { window.dispatchEvent(new Event("focus")); });
    expect(fetcher).toHaveBeenCalledTimes(2);
    later(REVALIDATE_EVERY + 1);
    await act(async () => { window.dispatchEvent(new Event("focus")); });
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(3));
  });

  it("sends the kept ETag, so an unchanged page is a 304 and nothing redraws", async () => {
    const seen: (string | null)[] = [];
    vi.stubGlobal("fetch", vi.fn(async (_url: string, init?: RequestInit) => {
      const tag = (init?.headers as Record<string, string> | undefined)?.["If-None-Match"] ?? null;
      seen.push(tag);
      return tag ? new Response(null, { status: 304 }) : new Response(JSON.stringify({ n: 1 }), { status: 200, headers: { ETag: '"a"' } });
    }));
    const { result } = renderHook(() => useApi<{ n: number }>("/api/overview"));
    await waitFor(() => expect(result.current.data).toEqual({ n: 1 }));
    const before = result.current.data;
    later(REVALIDATE_EVERY + 1);
    await act(async () => { window.dispatchEvent(new Event("focus")); });
    await waitFor(() => expect(seen).toEqual([null, '"a"']));
    expect(result.current.data).toBe(before);
  });

  it("leaves the page on screen when the ask fails, as it does just after the laptop wakes", async () => {
    let up = true;
    vi.stubGlobal("fetch", vi.fn(async () => {
      if (!up) throw new TypeError("network down");
      return new Response(JSON.stringify({ n: 1 }), { status: 200 });
    }));
    const { result } = renderHook(() => useApi<{ n: number }>("/api/overview"));
    await waitFor(() => expect(result.current.data).toEqual({ n: 1 }));
    up = false;
    later(REVALIDATE_EVERY + 1);
    await act(async () => { window.dispatchEvent(new Event("focus")); });
    await act(async () => { await Promise.resolve(); });
    expect(result.current.data).toEqual({ n: 1 });
    expect(result.current.error).toBeNull();
  });

  it("does not empty the cache: a page opened afterwards still paints at once", async () => {
    served(() => new Response(JSON.stringify({ n: 1 }), { status: 200 }));
    const one = renderHook(() => useApi<{ n: number }>("/api/overview"));
    await waitFor(() => expect(one.result.current.data).toEqual({ n: 1 }));
    later(REVALIDATE_EVERY + 1);
    await act(async () => { window.dispatchEvent(new Event("focus")); });
    one.unmount();
    const again = renderHook(() => useApi<{ n: number }>("/api/overview"));
    expect(again.result.current.data).toEqual({ n: 1 });
    expect(again.result.current.loading).toBe(false);
  });
});

describe("a connection that drops after the headers came", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    resetLink();
  });

  // the status and headers arrive; reading the body then fails the way a dropped connection does
  const dropped = () => {
    const r = new Response("{}", { status: 200 });
    vi.spyOn(r, "text").mockRejectedValue(new TypeError("network error"));
    return r;
  };

  it("says the server is not there, not the browser's own words, for a page with nothing kept", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => dropped()));
    const { result } = renderHook(() => ({ api: useApi<{ n: number }>("/api/overview"), link: useLink() }));
    await waitFor(() => expect(result.current.api.error).toBe(OFFLINE));
    expect(result.current.api.status).toBe(0);
    expect(result.current.link.down).toBe(true);
  });

  it("keeps the figures on screen and marks them old when the answer was kept", async () => {
    let drop = false;
    vi.stubGlobal("fetch", vi.fn(async () => (drop ? dropped() : new Response(JSON.stringify({ n: 1 }), { status: 200 }))));
    const { result } = renderHook(() => ({ api: useApi<{ n: number }>("/api/overview"), link: useLink() }));
    await waitFor(() => expect(result.current.api.data).toEqual({ n: 1 }));
    drop = true;
    await act(async () => { revalidate(); });
    await waitFor(() => expect(result.current.link.down).toBe(true));
    expect(result.current.api.data).toEqual({ n: 1 });
    expect(result.current.api.error).toBeNull();
    await waitFor(() => expect(result.current.link.stale).toBe(true));
  });

  it("is the same for an uncached GET", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => dropped()));
    await expect(getJSON("/api/run/status")).rejects.toMatchObject({ message: OFFLINE, status: 0 });
  });

  it("does not call a body that arrives but is not JSON a dropped connection", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("<html>", { status: 200 })));
    const { result } = renderHook(() => ({ api: useApi<{ n: number }>("/api/overview"), link: useLink() }));
    await waitFor(() => expect(result.current.api.error).not.toBeNull());
    expect(result.current.api.error).not.toBe(OFFLINE);
    expect(result.current.link.down).toBe(false);
  });
});
