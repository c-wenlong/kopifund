import { render, screen, waitFor } from "@testing-library/react";
import { clearApiCache } from "../api";
import { OneStat } from "../pages/StatsPage";
import { StatCard } from "./StatCard";
import { StatChart, Tip, type TipItem } from "./StatChart";
import { columnUnit, fmt, label, plan, type Row, type StatResult } from "./hint";
import { CENTS_UNIT, centsOf, inCents } from "./money";
import { tipExtras } from "./tipExtras";
import { versusFigure } from "./VersusList";

// G20 line 6: a stat result's money is cents only. A card shows a result in
// cents (unit "sgd_minor", `<name>_minor`) exactly as it showed the dollar
// result it replaces, never reads a bare dollar figure, and shows a result
// still in dollars (a copy the browser kept) as no result. Every figure is invented.

// jsdom has no layout; a chart needs a size to draw its axes
class SizedObserver {
  constructor(private cb: ResizeObserverCallback) {}
  observe(el: Element) {
    this.cb([{ target: el, contentRect: { width: 800, height: 400 } } as unknown as ResizeObserverEntry], this as unknown as ResizeObserver);
  }
  unobserve() {}
  disconnect() {}
}
vi.mock("../router", () => ({ navigate: vi.fn() }));
const real = globalThis.ResizeObserver;
beforeEach(() => { globalThis.ResizeObserver = SizedObserver as unknown as typeof ResizeObserver; });
afterEach(() => {
  globalThis.ResizeObserver = real;
  vi.unstubAllGlobals();
});

describe("centsOf", () => {
  it("reads the cents under `<name>_minor`", () => {
    expect(centsOf({ cash_minor: 123456 }, "cash")).toBe(123456);
    expect(centsOf({ cash: 1.0, cash_minor: 101 }, "cash")).toBe(101);
    expect(centsOf({ spend_minor: 5 }, "spend")).toBe(5);
  });
  it("never reads a bare dollar figure", () => {
    expect(centsOf({ cash: 1234.56 }, "cash")).toBeNull();
    expect(centsOf({ spend: 5 }, "spend")).toBeNull();
  });
  it("is null for an absent or null figure, or cents that are not whole", () => {
    for (const obj of [{}, { cash: null }, { cash_minor: null, cash: 5 }, { cash_minor: 12.5 }, null, [], "x"])
      expect(centsOf(obj, "cash")).toBeNull();
  });
});

describe("the renderer's units", () => {
  it("writes cents as money, and a bare number as the number it is", () => {
    expect(fmt(123456, CENTS_UNIT, true)).toBe("S$1,234.56");
    expect(fmt(123456, CENTS_UNIT)).toBe("S$1,235");
    expect(fmt(-775502, CENTS_UNIT)).toBe("−S$7,755");
    expect(fmt(9999, CENTS_UNIT, true)).toBe("S$99.99");
    expect(fmt(0, CENTS_UNIT, true)).toBe("S$0.00");
    expect(fmt(1234.56, "sgd")).toBe("1,234.6");
  });
  it("names and reads a _minor column as money in cents, and no other column", () => {
    expect(columnUnit("cash_minor")).toBe(CENTS_UNIT);
    expect(columnUnit("share_minor")).toBe(CENTS_UNIT);       // the suffix decides, not the rest of the name
    expect(columnUnit("count")).toBe("number");
    // a card in cents writes its money under `_minor`, so a plain number beside it is no amount
    expect(columnUnit("occurrences", undefined, CENTS_UNIT)).toBe("number");
    expect(columnUnit("ratio", undefined, CENTS_UNIT)).toBe("number");
    expect(columnUnit("cash", undefined, CENTS_UNIT)).toBe("number");
    expect(columnUnit("occurrences", undefined, "sgd")).toBe("number");
    expect(label("cash_minor")).toBe("Cash");
    expect(label("value_sgd_minor")).toBe("Value");
  });
  it("writes a versus side in cents as money, and a bare one in an SGD row as a plain number", () => {
    expect(versusFigure(45040, CENTS_UNIT)).toBe("S$450");
    expect(versusFigure(450.4, "SGD per household per month")).toBe("450.4");
  });
});

// Each stat as it writes its result since G20: money in cents under `_minor`
const runway: StatResult = {
  id: "savings-runway", title: "Cash and runway",
  chart: { type: "line", x: "month", series: ["cash_minor", "runway_months"], source: "months", unit: CENTS_UNIT, units: { runway_months: "months" } },
  data: {
    months: [{ month: "2026-08", cash_minor: 541008, runway_months: 1.6, "Main Bank_minor": 500050 },
             { month: "2026-09", cash_minor: 330266, runway_months: 1, "Main Bank_minor": 300006 }],
    summary: { cash_minor: 330266, as_of: "2026-09", runway_6m: 1, runway_all: 1.24, spend_avg_6m_minor: 344226, spend_avg_all_minor: 265325,
               trend_6m_minor: -775502, rule_low: 3, rule_high: 6 },
  },
};
const cashflow: StatResult = {
  id: "monthly-cashflow", title: "Monthly cash flow",
  chart: { type: "bar", x: "month", series: ["income_minor", "spend_minor", "net_minor"], unit: CENTS_UNIT,
    links: { income_minor: { month: "{x}", income: "1" }, spend_minor: { month: "{x}", spend: "1" } } },
  data: { months: [{ month: "2026-08", income_minor: 520050, spend_minor: 241025, net_minor: 279025, investing_minor: 100000 },
                   { month: "2026-09", income_minor: 410000, spend_minor: 491590, net_minor: -81590, investing_minor: 0 }] },
};
const split: StatResult = {
  id: "needs-wants-savings", title: "Needs, wants and savings",
  chart: { type: "stacked", x: "month", series: ["need_minor", "want_minor", "savings_minor"], unit: CENTS_UNIT },
  data: {
    months: [{ month: "2026-09", need_minor: 95410, want_minor: 168845, savings_minor: 75000 }],
    summary: { need_share: 0.29, want_share: 0.52, savings_share: 0.23, need_per_month_minor: 95410, want_per_month_minor: 168845,
               savings_per_month_minor: 75000, targets: { need: 0.5, want: 0.3, savings: 0.2 } },
  },
};
const quality: StatResult = {
  id: "data-quality", title: "Data quality",
  chart: { type: "bar", x: "month", series: ["uncategorised", "synthetic"], source: "by_month", unit: "count" },
  data: { counts: { uncategorised: 12, uncategorised_out_minor: 34560, synthetic: 30, unconverted: 2, partial_months: 7 },
          by_month: [{ month: "2026-09", uncategorised: 12, synthetic: 30 }] },
};
const table: StatResult = {
  id: "travel", title: "Trips",
  chart: { type: "table", unit: CENTS_UNIT },
  data: { trips: [{ name: "Trip A", days: 3, spend_minor: 120050, spend_per_day_minor: 40017, share: 0.3 }] },
};
// benchmarks-sg: money under `_minor` beside the unit it measures, a figure in months or percent under the bare name
const versus: StatResult = {
  id: "benchmarks-sg", title: "Against Singapore references",
  chart: { type: "versus", x: "short", series: ["owner", "benchmark"], source: "headline", unit_field: "unit", unit: CENTS_UNIT },
  data: { headline: [
    { short: "Food", owner_minor: 67420, benchmark_minor: 57800, unit: "SGD per household per month" },
    { short: "Months of cash", owner: 1.04, benchmark: 6, unit: "months of expenses" },
  ] },
};

// The text each card showed for the dollar result it replaces (G20 line 1 proved the
// two alike): the card's text, then each piece of text the chart's svg draws
const SHOWN: Record<string, string> = {
  "savings-runway": "Cash and runwayCashS$3,303at Sep 2026Runway, 6 months1.0 moS$3,442 a monthRunway, all months1.2 moS$2,653 a monthCash over 6 months−S$7,755CashRunwayAug 26Sep 26S$0S$1.5kS$3kS$4.5kS$6k0 mo0.4 mo0.8 mo1.2 mo1.6 moFigures as a tableMonthCashRunwayAug 2026S$5,410.081.6 moSep 2026S$3,302.661 mo|Aug 26|Sep 26|S$0|S$1.5k|S$3k|S$4.5k|S$6k|0 mo|0.4 mo|0.8 mo|1.2 mo|1.6 mo",
  "monthly-cashflow": "Monthly cash flowIncomeSpendNetAug 26Sep 26−S$2kS$0S$2kS$4kS$6kFigures as a tableMonthIncomeSpendNetAug 2026S$5,200.50S$2,410.25S$2,790.25Sep 2026S$4,100.00S$4,915.90−S$815.90|Aug 26|Sep 26|−S$2k|S$0|S$2k|S$4k|S$6k",
  "needs-wants-savings": "Needs, wants and savingsStackedLinesNeeds, of income29%rule 50% · S$954 a monthWants, of income52%rule 30% · S$1,688 a monthSavings, of income23%rule 20% · S$750 a monthNeedWantSavingsSep 26S$0S$850S$1.7kS$2.5kS$3.4kFigures as a tableMonthNeedWantSavingsSep 2026S$954.10S$1,688.45S$750.00|Sep 26|S$0|S$850|S$1.7k|S$2.5k|S$3.4k",
  "data-quality": "Data qualityRows with no category, and stand-in rows that fill a gap in a YouTrip export, by month.Rows with no category12S$346 outStand-in rows30Rows with no exchange rate2Months missing statements7UncategorisedSyntheticSep 2608162432Figures as a tableMonthUncategorisedSyntheticSep 20261230|Sep 26|0|8|16|24|32",
  "travel": "TripsNameDaysSpendSpend per dayShareTrip A3S$1,200.50S$400.1730%|",
  "benchmarks-sg": "Against Singapore referencesFoodS$674 vs S$578Months of cash1.0 vs 6.0Line = typical. SGD a month.|",
};

const svgText = (c: HTMLElement) => [...c.querySelectorAll("svg text")].map((t) => t.textContent).join("|");
const shown = (result: StatResult) => {
  const { container, unmount } = render(<StatCard result={result} />);
  const text = `${container.textContent}|${svgText(container)}`;
  unmount();
  return text;
};

describe("a stat card shows a result in cents as it showed the dollars", () => {
  it.each([runway, cashflow, split, quality, table, versus].map((r) => [r.id, r] as const))(
    "%s: its tiles, its axes, its table or its list", (id, result) => {
      expect(shown(result)).toBe(SHOWN[id]);
    });

  it("the hover card, and the figures it lists under the series", () => {
    const tip = (r: StatResult) => {
      const p = plan(r);
      if (p.kind !== "chart") throw new Error("expected a chart");
      const row = p.rows[p.rows.length - 1];
      const items: TipItem[] = p.series.map((s) => ({ dataKey: s, value: row[s], color: "red", payload: row }));
      const extras = (x: Row) => tipExtras(r.id, x, p.xKey, p.series, r.chart?.units, r.chart?.unit);
      const { container, unmount } = render(<Tip active x={row[p.xKey]} items={items} p={p} total={p.type === "stacked"} clickable extras={extras} />);
      const text = container.textContent;
      unmount();
      return text;
    };
    expect(tip(runway)).toBe("Sep 2026Runway1 moCashS$3,302.66Main BankS$3,000.06Click a bar to see its transactions");
    expect(tip(cashflow)).toBe("Sep 2026Net−S$815.90SpendS$4,915.90IncomeS$4,100.00Click a bar to see its transactions");
    expect(tip(split)).toBe("Sep 2026S$3,392.55SavingsS$750.00WantS$1,688.45NeedS$954.10Click a bar to see its transactions");
  });

  it("a chart in sgd_minor draws its money ticks in dollars", () => {
    const p = plan(cashflow);
    if (p.kind !== "chart") throw new Error("expected a chart");
    const { container } = render(<StatChart p={p} />);
    const ticks = [...container.querySelectorAll(".recharts-cartesian-axis-tick-value")].map((x) => x.textContent);
    expect(ticks).toEqual(["Aug 26", "Sep 26", "−S$2k", "S$0", "S$2k", "S$4k", "S$6k"]);
  });
});

// The same result as a stat wrote it before G20: dollars under the bare names, unit "sgd"
function inDollars(result: StatResult, unit: string | undefined = "sgd"): StatResult {
  const bare = (k: string) => (k.endsWith("_minor") ? k.slice(0, -"_minor".length) : k);
  const rename = (o: unknown): unknown => {
    if (Array.isArray(o)) return o.map(rename);
    if (!o || typeof o !== "object") return o;
    return Object.fromEntries(Object.entries(o as Row).map(([k, v]) =>
      k.endsWith("_minor") && typeof v === "number" ? [bare(k), v / 100] : [k, rename(v)]));
  };
  const { unit: _cents, ...rest } = result.chart ?? {};
  const chart = {
    ...rest,
    ...(unit === undefined ? {} : { unit }),
    series: rest.series?.map(bare),
    ...(rest.links ? { links: Object.fromEntries(Object.entries(rest.links).map(([s, l]) => [bare(s), l])) } : {}),
  };
  return { ...result, chart, data: rename(result.data) as Record<string, unknown> };
}

describe("inCents, the rule finance/money.py's in_cents keeps", () => {
  it("is true for a result whose chart is in sgd_minor, even with no data yet", () => {
    for (const r of [runway, cashflow, split, table, versus]) expect(inCents(r)).toBe(true);
    expect(inCents({ id: "eating-out", title: "Eating out", chart: { type: "line", unit: CENTS_UNIT }, data: {} })).toBe(true);
    expect(inCents({ chart: { unit: CENTS_UNIT } })).toBe(true);
  });

  it("is false for a result in dollars: unit sgd, or no unit, which meant sgd", () => {
    for (const r of [runway, cashflow, split, versus]) {
      expect(inCents(inDollars(r))).toBe(false);
      expect(inCents(inDollars(r, undefined))).toBe(false);
    }
    // the unit decides before any field: no unit with a `_minor` key is still dollars
    expect(inCents({ chart: { type: "bar" }, data: { months: [{ spend_minor: 100 }] } })).toBe(false);
    expect(inCents({ data: { months: [{ spend_minor: 100 }] } })).toBe(false);
    expect(inCents({ chart: { unit: null }, data: { spend_minor: 1 } })).toBe(false);
  });

  it("is false for a forward view from G17, its chart in sgd beside some `_minor` keys", () => {
    expect(inCents({
      id: "forward-view", title: "Forward view",
      chart: { type: "line", x: "month", series: ["cash"], source: "months", unit: "sgd" },
      data: { months: [{ month: "2026-10", cash: 5000, cash_minor: 500000 }], low_point: { month: "2026-10", cash_minor: 500000 } },
    })).toBe(false);
  });

  it("is true for a chart that is not money only when the result holds a `_minor` field", () => {
    expect(inCents(quality)).toBe(true);
    expect(inCents(inDollars(quality, "count"))).toBe(false);
    expect(inCents({ chart: { unit: "count" }, data: { by_month: [{ month: "2026-09", uncategorised: 1 }] } })).toBe(false);
    // at any depth, in lists too, and in the params
    expect(inCents({ chart: { unit: "count" }, data: { a: [{ b: [{ c_minor: 0 }] }] } })).toBe(true);
    expect(inCents({ chart: { unit: "months" }, data: {}, params: { floor_minor: 100 } })).toBe(true);
    expect(inCents({ chart: { unit: "count" }, data: { note: "spend_minor" } })).toBe(false);
  });

  it("is false for what is not a result", () => {
    for (const r of [null, undefined, [], "x", 3]) expect(inCents(r)).toBe(false);
  });
});

describe("OneStat, as a page shows one stat", () => {
  // a fresh page each time, so no answer kept from the last render paints first
  const serve = (result: StatResult) => {
    clearApiCache();
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(result), { status: 200 })));
  };

  it.each([
    ["savings-runway", runway, "No savings runway result yet."],
    ["monthly-cashflow", cashflow, "No monthly cashflow result yet."],
    ["benchmarks-sg", versus, "No benchmarks sg result yet."],
    ["needs-wants-savings", split, "No needs wants savings result yet."],
  ] as const)("%s: shows a result still in dollars as no result, and its cents twin as the card", async (id, result, none) => {
    for (const old of [inDollars(result), inDollars(result, undefined)]) {
      serve(old);
      const { unmount } = render(<OneStat id={id} />);
      expect(await screen.findByText(none)).toBeInTheDocument();
      expect(screen.queryByText(/S\$/)).not.toBeInTheDocument();
      unmount();
    }
    serve(result);
    const { container } = render(<OneStat id={id} />);
    expect(await screen.findByRole("heading", { name: result.title })).toBeInTheDocument();
    // the chart draws its axes a moment after the card mounts
    await waitFor(() => expect(`${container.textContent}|${svgText(container)}`).toBe(SHOWN[id]));
  });

  it("data-quality: shows a result with no `_minor` field as no result", async () => {
    serve(inDollars(quality, "count"));
    render(<OneStat id="data-quality" />);
    expect(await screen.findByText("No data quality result yet.")).toBeInTheDocument();
  });
});
