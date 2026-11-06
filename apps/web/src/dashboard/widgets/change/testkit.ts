// G26: what the change widgets' tests share: a fetch stub that answers widget queries, and the
// invented answers the widgets plot. Every figure is made up, in cents.
import { vi } from "vitest";
import { canonicalJson, widgetQueryUrl } from "../../../widgets";
import type { WidgetAnswer, WidgetQuery } from "../../../types.gen";
import { json } from "../../../test-helpers";

/** A fetch that answers every widget query from `answers` (by the query's JSON), a 400 for anything else. */
export function stubQueries(answers: [WidgetQuery, WidgetAnswer | { error: string }][]) {
  const byQuery = new Map(answers.map(([q, a]) => [canonicalJson(q), a]));
  const seen: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    seen.push(url);
    const q = decodeURIComponent(new URL(url, "http://t").searchParams.get("q") ?? "");
    const a = byQuery.get(q);
    if (!a) return json({ error: `no stub for ${q}` }, 400);
    if ("error" in a) return json({ error: a.error }, 400);
    return json(a);
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
