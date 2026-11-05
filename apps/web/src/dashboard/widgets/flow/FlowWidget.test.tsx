import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { WidgetAnswer } from "../../../types.gen";
import { widgetQueryUrl } from "../../../widgets";
import { FlowWidget } from "./FlowWidget";

// Invented figures only: S$5,000 of income in 2026-09 over two made-up
// buckets, their made-up categories and one invented savings transfer.
const RANGE = { from: "2026-09", to: "2026-09" };
const base = { from: "2026-09", to: "2026-09", spend: "1", home: "1" };

const INCOME: WidgetAnswer = {
  source: "spending", metric: "income", dimension: "month", unit: "sgd_minor",
  labels: ["2026-09"], series: [{ name: "income", values_minor: [500_000] }], range: RANGE,
  label_links: [{ from: "2026-09", to: "2026-09", income: "1", home: "1" }],
};
const BUCKETS: WidgetAnswer = {
  source: "spending", metric: "spend", dimension: "bucket", unit: "sgd_minor",
  labels: ["Food", "Transport"], series: [{ name: "spend", values_minor: [200_000, 100_000] }], range: RANGE,
  label_links: [{ ...base, bucket: "Food" }, { ...base, bucket: "Transport" }],
};
const CAT_FOOD: WidgetAnswer = {
  source: "spending", metric: "spend", dimension: "category", unit: "sgd_minor",
  labels: ["Eating out", "Groceries", "Everything else"],
  series: [{ name: "spend", values_minor: [120_000, 60_000, 20_000] }], range: RANGE,
  label_links: [
    { ...base, bucket: "Food", category: "Eating out" },
    { ...base, bucket: "Food", category: "Groceries" },
    { ...base, bucket: "Food", not_categories: '["Eating out","Groceries"]' },
  ],
};
const CAT_TRANSPORT: WidgetAnswer = {
  source: "spending", metric: "spend", dimension: "category", unit: "sgd_minor",
  labels: ["Bus & MRT", "Petrol"], series: [{ name: "spend", values_minor: [70_000, 30_000] }], range: RANGE,
  label_links: [
    { ...base, bucket: "Transport", category: "Bus & MRT" },
    { ...base, bucket: "Transport", category: "Petrol" },
  ],
};
const TRANSFERS: WidgetAnswer = {
  source: "spending", metric: "transfers", dimension: "bucket", unit: "sgd_minor",
  labels: ["Investments"], series: [{ name: "transfers", values_minor: [50_000] }], range: RANGE,
  label_links: [{ from: "2026-09", to: "2026-09", transfer: "1", home: "1", bucket: "Investments" }],
};
const EMPTY: WidgetAnswer = {
  source: "spending", metric: "spend", dimension: "bucket", unit: "sgd_minor",
  labels: [], series: [], range: RANGE,
};

function answerFor(url: string): WidgetAnswer | { error: string } {
  const q = JSON.parse(decodeURIComponent(url.slice(url.indexOf("?q=") + 3)));
  if (q.metric === "income") return INCOME;
  if (q.metric === "transfers") return TRANSFERS;
  if (q.dimension === "bucket") return BUCKETS;
  if (q.dimension === "category") return q.filters?.bucket === "Transport" ? CAT_TRANSPORT : CAT_FOOD;
  return { error: "unexpected query" };
}

function stubFetch(answer: (url: string) => WidgetAnswer | { error: string } = answerFor) {
  const asked: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    asked.push(url);
    const a = answer(url);
    return "error" in a
      ? new Response(JSON.stringify(a), { status: 400 })
      : new Response(JSON.stringify(a), { status: 200 });
  }));
  return asked;
}

afterEach(() => vi.unstubAllGlobals());

describe("FlowWidget", () => {
  it("renders the flow: income, the buckets, their categories, transfers and the unspent rest", async () => {
    stubFetch();
    render(<FlowWidget config={{ range: RANGE }} />);
    await waitFor(() => expect(screen.getByText(/Eating out/)).toBeInTheDocument());
    expect(screen.getByText(/^Income/)).toBeInTheDocument();
    expect(screen.getByText(/^Food/)).toBeInTheDocument();
    expect(screen.getByText(/Bus & MRT/)).toBeInTheDocument();
    expect(screen.getByText(/Investments/)).toBeInTheDocument();
    expect(screen.getByText(/Unspent/)).toBeInTheDocument();
    expect(screen.getByText(/Everything else/)).toBeInTheDocument();
    // Unspent = 5000 - 2000 - 1000 - 500 = S$1,500
    expect(screen.getByText(/S\$1,500/)).toBeInTheDocument();
  });

  it("asks one category query per middle node, plus income and transfers", async () => {
    const asked = stubFetch();
    render(<FlowWidget config={{ range: RANGE }} />);
    await waitFor(() => expect(screen.getByText(/Eating out/)).toBeInTheDocument());
    const queries = asked.map((u) => JSON.parse(decodeURIComponent(u.slice(u.indexOf("?q=") + 3))));
    expect(queries.filter((q) => q.dimension === "category").map((q) => q.filters.bucket))
      .toEqual(["Food", "Transport"]);
    expect(queries.filter((q) => q.metric === "income")).toHaveLength(1);
    expect(queries.filter((q) => q.metric === "transfers")).toHaveLength(1);
  });

  it("links a node and a band to the Transactions rows behind them", async () => {
    stubFetch();
    render(<FlowWidget config={{ range: RANGE }} />);
    await waitFor(() => expect(screen.getByText(/Eating out/)).toBeInTheDocument());
    const food = screen.getByRole("link", { name: "Food" });
    expect(food).toHaveAttribute("href", "/transactions?from=2026-09&to=2026-09&spend=1&home=1&bucket=Food");
    fireEvent.click(food);
    expect(window.location.pathname + window.location.search)
      .toBe("/transactions?from=2026-09&to=2026-09&spend=1&home=1&bucket=Food");
    expect(screen.getByRole("link", { name: "Income to Food" }))
      .toHaveAttribute("href", expect.stringContaining("bucket=Food"));
    expect(screen.getByRole("link", { name: "Food to Eating out" }))
      .toHaveAttribute("href", expect.stringContaining("category=Eating+out"));
  });

  it("names the amount and its share of inflow on hover", async () => {
    stubFetch();
    render(<FlowWidget config={{ range: RANGE }} />);
    const band = await screen.findByRole("link", { name: "Income to Food" });
    fireEvent.mouseEnter(band);
    fireEvent.mouseMove(band, { clientX: 120, clientY: 60 });
    expect(screen.getByText("Income → Food")).toBeInTheDocument();
    expect(screen.getByText("Share of inflow")).toBeInTheDocument();
    expect(screen.getByText("40%")).toBeInTheDocument();
    fireEvent.mouseLeave(band);
    expect(screen.queryByText("Share of inflow")).not.toBeInTheDocument();
  });

  it("keeps its width under a narrow card and scrolls sideways", async () => {
    stubFetch();
    const { container } = render(<FlowWidget config={{ range: RANGE }} />);
    await waitFor(() => expect(screen.getByText(/Eating out/)).toBeInTheDocument());
    const svg = container.querySelector("svg")!;
    expect(Number(svg.getAttribute("width"))).toBeGreaterThanOrEqual(480);
    expect(svg.parentElement?.className).toContain("overflow-x-auto");
  });

  it("skips the transfers query when the config turns them off", async () => {
    const asked = stubFetch();
    render(<FlowWidget config={{ range: RANGE, transfers: false }} />);
    await waitFor(() => expect(screen.getByText(/Eating out/)).toBeInTheDocument());
    const queries = asked.map((u) => JSON.parse(decodeURIComponent(u.slice(u.indexOf("?q=") + 3))));
    expect(queries.some((q) => q.metric === "transfers")).toBe(false);
    expect(screen.queryByText(/Investments/)).not.toBeInTheDocument();
    // Unspent now carries what the transfer held: 5000 - 3000 = S$2,000
    expect(screen.getByText(/Unspent/)).toHaveTextContent("S$2,000");
  });

  it("drops the transfer nodes when they are turned off after the answers arrived", async () => {
    stubFetch();
    const { rerender } = render(<FlowWidget config={{ range: RANGE }} />);
    await waitFor(() => expect(screen.getByText(/Investments/)).toBeInTheDocument());
    // the disabled query keeps its last answer in useWidgetData; none of it may stay drawn
    rerender(<FlowWidget config={{ range: RANGE, transfers: false }} />);
    await waitFor(() => expect(screen.queryByText(/Investments/)).not.toBeInTheDocument());
    expect(screen.getByText(/Unspent/)).toHaveTextContent("S$2,000");
  });

  it("says so plainly when the range holds nothing", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      const q = JSON.parse(decodeURIComponent(url.slice(url.indexOf("?q=") + 3)));
      const a = q.metric === "income"
        ? { ...INCOME, series: [{ name: "income", values_minor: [0] }] }
        : q.metric === "transfers" ? { ...TRANSFERS, labels: [], series: [], label_links: [] } : EMPTY;
      return new Response(JSON.stringify(a), { status: 200 });
    }));
    render(<FlowWidget config={{ range: RANGE }} />);
    await waitFor(() => expect(screen.getByText("No income or spending in this range.")).toBeInTheDocument());
  });

  it("shows the server's error rather than a blank chart", async () => {
    stubFetch(() => ({ error: "For spending, dimension is one of month, bucket, category" }));
    render(<FlowWidget config={{ range: RANGE }} />);
    await waitFor(() => expect(screen.getByText(/dimension is one of/)).toBeInTheDocument());
  });

  it("covers a middle node with Everything else when its category query fails", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      const q = JSON.parse(decodeURIComponent(url.slice(url.indexOf("?q=") + 3)));
      if (q.dimension === "category" && q.filters?.bucket === "Transport") {
        return new Response(JSON.stringify({ error: "boom" }), { status: 400 });
      }
      const a = answerFor(url);
      return "error" in a
        ? new Response(JSON.stringify(a), { status: 400 })
        : new Response(JSON.stringify(a), { status: 200 });
    }));
    render(<FlowWidget config={{ range: RANGE }} />);
    await waitFor(() => expect(screen.getByText(/Eating out/)).toBeInTheDocument());
    // Transport's whole S$1,000 sits in a residual node that still links to its rows
    expect(screen.getAllByText(/Everything else/).length).toBeGreaterThanOrEqual(2);
    expect(screen.getByRole("link", { name: "Transport to Everything else" }))
      .toHaveAttribute("href", expect.stringContaining("bucket=Transport"));
  });

  it("draws a Beyond income source when the range's outflow beats income", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      const q = JSON.parse(decodeURIComponent(url.slice(url.indexOf("?q=") + 3)));
      const a = q.metric === "income"
        ? { ...INCOME, series: [{ name: "income", values_minor: [250_000] }] }
        : answerFor(url);
      return "error" in a
        ? new Response(JSON.stringify(a), { status: 400 })
        : new Response(JSON.stringify(a), { status: 200 });
    }));
    render(<FlowWidget config={{ range: RANGE }} />);
    await waitFor(() => expect(screen.getByText(/Beyond income/)).toBeInTheDocument());
    // 3500 out - 2500 in = S$1,000 beyond
    expect(screen.getByText(/Beyond income/)).toHaveTextContent("S$1,000");
    expect(screen.queryByText(/Unspent/)).not.toBeInTheDocument();
  });

  it("exports a widgetQueryUrl the flow itself uses", () => {
    expect(widgetQueryUrl({ source: "spending", metric: "income", dimension: "month", range: RANGE }))
      .toContain("/api/dashboard/query?q=");
  });
});
