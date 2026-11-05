import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { postWidgetQuery, previewScenario, useWidgetData, widgetQueryUrl } from "./widgets";
import type { WidgetQuery } from "./types.gen";

const SPEND_BY_BUCKET: WidgetQuery = {
  source: "spending",
  metric: "spend",
  dimension: "bucket",
  range: { from: "2026-08", to: "2026-08" },
};

const ANSWER = {
  source: "spending", metric: "spend", dimension: "bucket", unit: "sgd_minor",
  labels: ["Food"], series: [{ name: "spend", values_minor: [1750] }], range: { from: "2026-08", to: "2026-08" },
};

describe("useWidgetData", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("asks the query endpoint for the widget's query, as JSON in ?q=", () => {
    const url = widgetQueryUrl(SPEND_BY_BUCKET);
    expect(url.startsWith("/api/dashboard/query?q=")).toBe(true);
    expect(JSON.parse(decodeURIComponent(url.slice(url.indexOf("=") + 1)))).toEqual(SPEND_BY_BUCKET);
  });

  it("loads the answer through api.ts's cache and revalidation", async () => {
    const asked: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      asked.push(url);
      return new Response(JSON.stringify(ANSWER), { status: 200 });
    }));
    const { result } = renderHook(() => useWidgetData(SPEND_BY_BUCKET));
    await waitFor(() => expect(result.current.data).toEqual(ANSWER));
    expect(asked).toEqual([widgetQueryUrl(SPEND_BY_BUCKET)]);
  });

  it("shows the server's error for a bad query rather than blank data", async () => {
    vi.stubGlobal("fetch", vi.fn(async () =>
      new Response(JSON.stringify({ error: "source is one of spending, monthly-cashflow" }), { status: 400 })));
    const { result } = renderHook(() =>
      useWidgetData({ ...SPEND_BY_BUCKET, source: "nonsense" as never }));
    await waitFor(() => expect(result.current.error).toContain("source is one of"));
    expect(result.current.data).toBeNull();
  });

  it("asks nothing while the query is null", () => {
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    renderHook(() => useWidgetData(null));
    expect(fetch).not.toHaveBeenCalled();
  });

  it("posts the same query for callers outside the view cache", async () => {
    const seen: { url: string; body: string }[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      seen.push({ url, body: String(init?.body ?? "") });
      return new Response(JSON.stringify(ANSWER), { status: 200 });
    }));
    await expect(postWidgetQuery(SPEND_BY_BUCKET)).resolves.toEqual(ANSWER);
    expect(seen).toEqual([{ url: "/api/dashboard/query", body: JSON.stringify(SPEND_BY_BUCKET) }]);
  });

  it("previews a scenario without saving anything", async () => {
    const seen: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      seen.push(url);
      return new Response(JSON.stringify({ ...ANSWER, projected_from: 1 }), { status: 200 });
    }));
    const out = await previewScenario({ query: SPEND_BY_BUCKET, months: 3, delta_minor: 100 });
    expect(out.projected_from).toBe(1);
    expect(seen).toEqual(["/api/dashboard/scenario/preview"]);
  });
});
