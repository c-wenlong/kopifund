// G26: what the change widgets' tests share: a sized ResizeObserver so
// Recharts draws in jsdom, a fetch stub that answers widget queries, and the
// invented answers the widgets plot. Every figure is made up, in cents.
import { vi } from "vitest";
import { widgetQueryUrl } from "../../../widgets";
import type { WidgetAnswer, WidgetQuery } from "../../../types.gen";

export class SizedObserver {
  constructor(private cb: ResizeObserverCallback) {}
  observe(el: Element) {
    this.cb([{ target: el, contentRect: { width: 800, height: 400 } } as unknown as ResizeObserverEntry],
      this as unknown as ResizeObserver);
  }
  unobserve() {}
  disconnect() {}
}

/** ResizeObserver swapped for one that gives every chart 800x400, for the test's duration. */
export function useSizedCharts() {
  const real = globalThis.ResizeObserver;
  beforeEach(() => { globalThis.ResizeObserver = SizedObserver as unknown as typeof ResizeObserver; });
  afterEach(() => { globalThis.ResizeObserver = real; });
}

/** A fetch that answers every widget query from `answers` (by the query's JSON), a 400 for anything else. */
export function stubQueries(answers: [WidgetQuery, WidgetAnswer | { error: string }][]) {
  const byQuery = new Map(answers.map(([q, a]) => [JSON.stringify(q), a]));
  const seen: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    seen.push(url);
    const q = decodeURIComponent(new URL(url, "http://t").searchParams.get("q") ?? "");
    const a = byQuery.get(q);
    if (!a) return new Response(JSON.stringify({ error: `no stub for ${q}` }), { status: 400 });
    if ("error" in a) return new Response(JSON.stringify({ error: a.error }), { status: 400 });
    return new Response(JSON.stringify(a), { status: 200 });
  }));
  return seen;
}

export const QUERY_URL = widgetQueryUrl;

/** A money-by-month answer: invented months and figures in cents. */
export function cashflowAnswer(months: { m: string; income: number; spend: number; net: number }[], metric: string): WidgetAnswer {
  const key = metric as "income" | "spend" | "net";
  return {
    source: "monthly-cashflow", metric, dimension: "month", unit: "sgd_minor",
    labels: months.map((r) => r.m),
    label_links: months.map((r) => ({ from: r.m, to: r.m, [key]: "1" })),
    series: [{ name: key, values_minor: months.map((r) => r[key]) }],
    range: months.length ? { from: months[0].m, to: months[months.length - 1].m } : null,
  };
}
