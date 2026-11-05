import { describe, expect, it } from "vitest";
import type { WidgetAnswer } from "../../../types.gen";
import {
  buildFlow, childrenFromAnswer, filtersLink, flowQueries, incomeLink, layoutFlow, OTHER_NAME, txnUrl,
  type FlowChild, type FlowSlice,
} from "./flow";

// Invented figures only (S$5,000 in, S$3,500 out), never real ledger data.
const FOOD: FlowSlice = {
  name: "Food", minor: 200_000, link: { bucket: "Food" },
  children: [
    { name: "Eating out", minor: 120_000, kind: "category", link: { category: "Eating out" } },
    { name: "Groceries", minor: 60_000, kind: "category", link: { category: "Groceries" } },
    { name: OTHER_NAME, minor: 20_000, kind: "rest", link: { not_categories: '["Eating out","Groceries"]' } },
  ],
};
const TRANSPORT: FlowSlice = {
  name: "Transport", minor: 100_000, link: { bucket: "Transport" },
  children: [
    { name: "Bus & MRT", minor: 70_000, kind: "category", link: { category: "Bus & MRT" } },
    { name: "Petrol", minor: 30_000, kind: "category", link: { category: "Petrol" } },
  ],
};
const INVESTED: FlowSlice = { name: "Investments", minor: 50_000, link: { transfer: "1", need: "Investment" }, children: [] };

const sum = (xs: { minor: number }[]) => xs.reduce((s, x) => s + x.minor, 0);
const inflowOf = (g: ReturnType<typeof buildFlow>, id: string) =>
  sum(g.links.filter((l) => l.target.id === id));
const outflowOf = (g: ReturnType<typeof buildFlow>, id: string) =>
  sum(g.links.filter((l) => l.source.id === id));

describe("flowQueries", () => {
  it("asks income by month and spend/transfers over the middle dimension", () => {
    const q = flowQueries({ range: { from: "2026-09", to: "2026-09" } });
    expect(q.income).toEqual({
      source: "spending", metric: "income", dimension: "month",
      range: { from: "2026-09", to: "2026-09" }, filters: undefined,
    });
    expect(q.middle.dimension).toBe("bucket");
    expect(q.transfers?.metric).toBe("transfers");
    expect(q.category("Food").filters).toEqual({ bucket: "Food" });
  });

  it("names the need filter for a need middle, and drops the transfer query when transfers are off", () => {
    const q = flowQueries({ middle: "need", transfers: false, filters: { scope: "all" } });
    expect(q.middle.dimension).toBe("need");
    expect(q.transfers).toBeNull();
    expect(q.category("Want").filters).toEqual({ scope: "all", need: "Want" });
  });
});

describe("buildFlow", () => {
  const g = buildFlow({
    income_minor: 500_000, incomeLink: { income: "1" },
    slices: [FOOD, TRANSPORT], transfers: [INVESTED],
  });

  it("keeps every weight in integer cents and conserves each node", () => {
    for (const n of g.nodes) {
      expect(Number.isInteger(n.minor)).toBe(true);
      const inflow = inflowOf(g, n.id);
      const outflow = outflowOf(g, n.id);
      if (inflow > 0) expect(inflow).toBe(n.minor);
      if (outflow > 0) expect(outflow).toBe(n.minor);
    }
    for (const l of g.links) expect(Number.isInteger(l.minor)).toBe(true);
  });

  it("balances the columns: income equals the whole middle column", () => {
    expect(g.income_minor).toBe(500_000);
    expect(sum(g.nodes.filter((n) => n.column === 0))).toBe(500_000);
    expect(sum(g.nodes.filter((n) => n.column === 1))).toBe(500_000);
    expect(sum(g.nodes.filter((n) => n.column === 2))).toBe(300_000);
  });

  it("draws the unspent rest and transfers as their own nodes, never dropped", () => {
    const kept = g.nodes.find((n) => n.kind === "kept");
    expect(kept?.label).toBe("Unspent");
    expect(kept?.minor).toBe(150_000);          // 5000 - 2000 - 1000 - 500
    const t = g.nodes.find((n) => n.kind === "transfers");
    expect(t?.label).toBe("Investments");
    expect(t?.minor).toBe(50_000);
    expect(t?.link).toEqual({ transfer: "1", need: "Investment" });
  });

  it("funds the middle nodes from income in drawn order, surplus to Unspent", () => {
    const incomeLinks = g.links.filter((l) => l.source.id === "income");
    expect(incomeLinks.map((l) => [l.target.id, l.minor])).toEqual([
      ["m0", 200_000], ["m1", 100_000], ["t0", 50_000], ["kept", 150_000],
    ]);
  });

  it("adds a Beyond income source when the outflow beats income, funding what income cannot", () => {
    const d = buildFlow({
      income_minor: 250_000, incomeLink: null,
      slices: [FOOD, TRANSPORT], transfers: [],
    });
    const beyond = d.nodes.find((n) => n.kind === "beyond");
    expect(beyond?.label).toBe("Beyond income");
    expect(beyond?.minor).toBe(50_000);
    expect(d.nodes.some((n) => n.kind === "kept")).toBe(false);
    // income covers Food (2000) and part of Transport; Beyond income the rest
    const t = d.links.filter((l) => l.target.id === "m1");
    expect(t.map((l) => [l.source.id, l.minor])).toEqual([["income", 50_000], ["beyond", 50_000]]);
    for (const n of d.nodes) {
      const inflow = inflowOf(d, n.id);
      if (inflow > 0) expect(inflow).toBe(n.minor);
    }
    expect(sum(d.nodes.filter((n) => n.column === 0))).toBe(sum(d.nodes.filter((n) => n.column === 1)));
  });

  it("shows the whole slice as Everything else when its categories never arrived", () => {
    const empty: FlowSlice = { ...FOOD, children: [] };
    const r = buildFlow({ income_minor: 500_000, incomeLink: null, slices: [empty], transfers: [] });
    const rest = r.nodes.find((n) => n.column === 2);
    expect(rest?.label).toBe(OTHER_NAME);
    expect(rest?.minor).toBe(200_000);
    expect(rest?.link).toEqual({ bucket: "Food" });
  });

  it("drops non-positive slices and leaves their cents in Unspent", () => {
    const refund: FlowSlice = { name: "Refunds", minor: -20_000, link: null, children: [] };
    const back: FlowSlice = { name: "Savings", minor: -10_000, link: null, children: [] };
    const r = buildFlow({
      income_minor: 500_000, incomeLink: null, slices: [FOOD, refund], transfers: [INVESTED, back],
    });
    expect(r.nodes.some((n) => n.label === "Refunds" || n.label === "Savings")).toBe(false);
    // unspent = income - spend - transfers drawn: the negative slices were never counted
    expect(r.nodes.find((n) => n.kind === "kept")?.minor).toBe(250_000);
  });

  it("nets a refund row into Everything else so the children add to the slice", () => {
    // S$1,000 of eating out and S$200 back in another category: the bucket nets S$800
    const food: FlowSlice = {
      name: "Food", minor: 80_000, link: { bucket: "Food" },
      children: [
        { name: "Eating out", minor: 100_000, kind: "category", link: { bucket: "Food", category: "Eating out" } },
        { name: "Refund", minor: -20_000, kind: "category", link: { bucket: "Food", category: "Refund" } },
      ],
    };
    const r = buildFlow({ income_minor: 500_000, incomeLink: null, slices: [food], transfers: [] });
    const m = r.nodes.find((n) => n.label === "Food")!;
    const kids = r.nodes.filter((n) => n.column === 2);
    expect(kids.map((k) => [k.label, k.minor])).toEqual([[OTHER_NAME, 80_000]]);
    expect(outflowOf(r, m.id)).toBe(80_000);
    // the residual holds both rows: the link is the whole bucket, not one category
    expect(kids[0].link).toEqual({ bucket: "Food" });
  });

  it("folds only what overshoots the slice, keeping the categories that still fit", () => {
    const food: FlowSlice = {
      name: "Food", minor: 80_000, link: { bucket: "Food" },
      children: [
        { name: "Groceries", minor: 60_000, kind: "category", link: { bucket: "Food", category: "Groceries" } },
        { name: "Eating out", minor: 100_000, kind: "category", link: { bucket: "Food", category: "Eating out" } },
        { name: "Refund", minor: -80_000, kind: "category", link: { bucket: "Food", category: "Refund" } },
      ],
    };
    const r = buildFlow({ income_minor: 500_000, incomeLink: null, slices: [food], transfers: [] });
    const kids = r.nodes.filter((n) => n.column === 2);
    // Eating out alone beats the S$800 net, so only Groceries stays; the residual is 1000 - 800
    expect(kids.map((k) => [k.label, k.minor])).toEqual([["Groceries", 60_000], [OTHER_NAME, 20_000]]);
    expect(JSON.parse(kids[1].link!.not_categories!)).toEqual(["Groceries"]);
  });
});

describe("childrenFromAnswer", () => {
  const answer = (labels: string[], minors: number[]): WidgetAnswer => ({
    source: "spending", metric: "spend", dimension: "category", unit: "sgd_minor",
    labels, series: [{ name: "spend", values_minor: minors }], range: { from: "2026-09", to: "2026-09" },
    label_links: labels.map((name): Record<string, string> => name === OTHER_NAME
      ? { from: "2026-09", to: "2026-09", spend: "1", home: "1", bucket: "Food", not_categories: '["Eating out","Groceries","Coffee"]' }
      : { from: "2026-09", to: "2026-09", spend: "1", home: "1", bucket: "Food", category: name }),
  });

  it("passes the answer's rows through, folded row included, under the cap", () => {
    const a = answer(["Eating out", "Groceries", OTHER_NAME], [120_000, 60_000, 20_000]);
    const children = childrenFromAnswer(a, 7);
    expect(children.map((c) => c.name)).toEqual(["Eating out", "Groceries", OTHER_NAME]);
    expect(children[2].kind).toBe("rest");
    expect(children[2].link).toEqual(expect.objectContaining({ not_categories: '["Eating out","Groceries","Coffee"]' }));
  });

  it("merges what the cap cuts into one residual that still names what is shown", () => {
    const a = answer(["Eating out", "Groceries", "Coffee", OTHER_NAME], [120_000, 60_000, 30_000, 20_000]);
    const children = childrenFromAnswer(a, 2);
    expect(children.map((c) => c.name)).toEqual(["Eating out", "Groceries", OTHER_NAME]);
    const rest = children[2];
    expect(rest.minor).toBe(50_000);
    expect(rest.link).toEqual({
      from: "2026-09", to: "2026-09", spend: "1", home: "1", bucket: "Food",
      not_categories: '["Eating out","Groceries"]',
    });
    expect(rest.link).not.toHaveProperty("category");
  });

  it("returns no children for a missing answer and drops zero rows", () => {
    expect(childrenFromAnswer(null, 7)).toEqual([]);
    const a = answer(["Eating out", "Empty", OTHER_NAME], [120_000, 0, 80_000]);
    expect(childrenFromAnswer(a, 7).map((c) => c.name)).toEqual(["Eating out", OTHER_NAME]);
  });

  it("keeps refund rows so buildFlow can net them against the slice", () => {
    const a = answer(["Eating out", "Refund", OTHER_NAME], [120_000, -20_000, 80_000]);
    const children = childrenFromAnswer(a, 7);
    expect(children.map((c) => [c.name, c.minor])).toEqual([
      ["Eating out", 120_000], ["Refund", -20_000], [OTHER_NAME, 80_000],
    ]);
  });

  it("merges the filter's own exclusions into the residual's not_categories", () => {
    const a = answer(["Eating out", "Groceries", "Coffee", OTHER_NAME], [120_000, 60_000, 30_000, 20_000]);
    a.label_links![0] = { ...a.label_links![0], not_categories: '["Shopping"]' };
    const children = childrenFromAnswer(a, 2);
    const rest = children[2];
    expect(rest.minor).toBe(50_000);
    // Shopping stays excluded: the residual's rows never had it
    expect(JSON.parse(rest.link!.not_categories!)).toEqual(["Shopping", "Eating out", "Groceries"]);
  });
});

describe("layoutFlow", () => {
  const g = buildFlow({
    income_minor: 500_000, incomeLink: null,
    slices: [FOOD, TRANSPORT], transfers: [INVESTED],
  });
  const laid = layoutFlow(g, 720);

  it("gives every node a place in bounds and stacks each column without overlap", () => {
    for (const n of laid.nodes) {
      expect(n.x).toBeGreaterThanOrEqual(0);
      expect(n.x + n.w).toBeLessThanOrEqual(720);
      expect(n.y).toBeGreaterThanOrEqual(0);
      expect(n.y + n.h).toBeLessThanOrEqual(laid.height);
      expect(n.h).toBeGreaterThan(0);
    }
    for (const col of [0, 1, 2]) {
      const c = laid.nodes.filter((n) => n.column === col).sort((a, b) => a.y - b.y);
      for (let i = 1; i < c.length; i++) {
        expect(c[i].y).toBeGreaterThanOrEqual(c[i - 1].y + c[i - 1].h);
      }
    }
  });

  it("proportions the node heights to cents on one shared scale", () => {
    const food = laid.nodes.find((n) => n.label === "Food");
    const transport = laid.nodes.find((n) => n.label === "Transport");
    expect(food && transport && food.h / transport.h).toBeCloseTo(2, 5);
  });

  it("draws one bezier band per link, its thickness the link's cents on the same scale", () => {
    const income = laid.nodes.find((n) => n.id === "income")!;
    const out = laid.links.filter((l) => l.source.id === "income");
    // the bands stacked on income fill its height
    expect(out.reduce((s, l) => s + l.width, 0)).toBeCloseTo(income.h, 5);
    for (const l of laid.links) {
      expect(l.d.startsWith("M ")).toBe(true);
      expect(l.d).toContain("C ");
      expect(l.width).toBeGreaterThan(0);
    }
  });

  it("grows past the cap rather than crush a full column's bands to nothing", () => {
    const many: FlowSlice = {
      name: "Food", minor: 200_000, link: null,
      children: Array.from({ length: 40 }, (_, i): FlowChild => ({
        name: `Cat ${i}`, minor: 5_000, kind: "category", link: null,
      })),
    };
    const big = buildFlow({ income_minor: 500_000, incomeLink: null, slices: [many], transfers: [] });
    const tall = layoutFlow(big, 720);
    expect(tall.height).toBeGreaterThan(560);
    for (const n of tall.nodes) {
      expect(n.y).toBeGreaterThanOrEqual(0);
      expect(n.y + n.h).toBeLessThanOrEqual(tall.height);
    }
    for (const l of tall.links) expect(l.width).toBeGreaterThan(0);
  });
});

describe("links", () => {
  it("writes a link record as a Transactions address", () => {
    expect(txnUrl({ from: "2026-09", to: "2026-09", spend: "1", bucket: "Food" }))
      .toBe("/transactions?from=2026-09&to=2026-09&spend=1&bucket=Food");
    expect(txnUrl(null)).toBeNull();
  });

  it("carries the sg scope as home=1 and keeps other filters", () => {
    expect(filtersLink(undefined)).toEqual({ home: "1" });
    expect(filtersLink({ scope: "all" })).toEqual({});
    expect(filtersLink({ trip: "Term abroad" })).toEqual({ trip: "Term abroad" });
    expect(filtersLink({ account: "3" })).toEqual({ account: "3", home: "1" });
  });

  it("builds the income node's link over the answer's resolved range", () => {
    const a = { range: { from: "2026-08", to: "2026-09" } } as WidgetAnswer;
    expect(incomeLink(a, undefined)).toEqual({ from: "2026-08", to: "2026-09", income: "1", home: "1" });
  });
});
