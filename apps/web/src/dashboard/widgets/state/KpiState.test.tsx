import { render, screen, waitFor } from "@testing-library/react";
import { kpiOf, KpiState } from "./KpiState";
import type { WidgetAnswer, WidgetQuery } from "../../../types.gen";

// invented figures only
const QUERY: WidgetQuery = { source: "spending", metric: "spend", dimension: "month" };

const BY_MONTH: WidgetAnswer = {
  source: "spending", metric: "spend", dimension: "month", unit: "sgd_minor",
  labels: ["2026-08", "2026-09", "2026-10"],
  series: [{ name: "spend", values_minor: [150_000, 175_000, 60_000] }],
  range: { from: "2026-08", to: "2026-10" },
};

const BY_PART: WidgetAnswer = {
  source: "net-worth", metric: "value", dimension: "part", unit: "sgd_minor",
  labels: ["Cash", "Investments", "CPF"],
  series: [{ name: "net worth", values_minor: [135_510_000, 214_780_000, 142_700_000] }],
  range: null,
};

function stubApi(answer: unknown = BY_MONTH, status = 200) {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(answer), { status })));
}

beforeEach(() => stubApi());
afterEach(() => vi.unstubAllGlobals());

describe("kpiOf", () => {
  it("reads the latest figure, the one before it, and the run", () => {
    const k = kpiOf(BY_MONTH);
    expect(k.value).toBe(60_000);
    expect(k.prior).toBe(175_000);
    expect(k.atLabel).toBe("2026-10");
    expect(k.priorLabel).toBe("2026-09");
    expect(k.run).toEqual([150_000, 175_000, 60_000]);
  });

  it("skips nulls when finding the latest and the prior", () => {
    const answer: WidgetAnswer = {
      ...BY_MONTH, labels: ["2026-08", "2026-09", "2026-10", "2026-11"],
      series: [{ name: "spend", values_minor: [150_000, null, 60_000, null] }],
    };
    const k = kpiOf(answer);
    expect(k.value).toBe(60_000);
    expect(k.prior).toBe(150_000);
    expect(k.priorLabel).toBe("2026-08");
  });

  it("sums the answer when asked, and reads a named series", () => {
    const answer: WidgetAnswer = {
      ...BY_MONTH,
      series: [
        { name: "need", values_minor: [100, 200] },
        { name: "want", values_minor: [300, 400] },
      ],
    };
    expect(kpiOf(answer, "want", "sum").value).toBe(700);
    expect(kpiOf(answer).series?.name).toBe("need");
  });

  it("sums a spread that is not over time, with no prior and no run", () => {
    const k = kpiOf(BY_PART);
    expect(k.value).toBe(492_990_000);
    expect(k.prior).toBeNull();
    expect(k.run).toEqual([]);
    expect(k.atLabel).toBeNull();
  });
});

describe("KpiState", () => {
  it("shows the latest figure, the month it is for, and the change on the prior", async () => {
    render(<KpiState config={{ title: "Spending", query: QUERY }} />);
    await waitFor(() => expect(screen.getByText("S$600")).toBeInTheDocument());
    expect(screen.getByText("Spending · Oct 26")).toBeInTheDocument();
    expect(screen.getByText("−S$1,150 vs Sep 26")).toBeInTheDocument();
  });

  it("reads a good fall as good and a rise as a warning when goodWhen is down", async () => {
    render(<KpiState config={{ title: "Spending", query: QUERY, goodWhen: "down" }} />);
    await waitFor(() => expect(screen.getByText("−S$1,150 vs Sep 26")).toBeInTheDocument());
    expect(screen.getByText("−S$1,150 vs Sep 26")).toHaveClass("text-good");
  });

  it("reads a rise as good when goodWhen is up", async () => {
    stubApi({ ...BY_MONTH, series: [{ name: "spend", values_minor: [150_000, 175_000] }] });
    render(<KpiState config={{ title: "Spending", query: QUERY, goodWhen: "up" }} />);
    await waitFor(() => expect(screen.getByText("+S$250 vs Aug 26")).toBeInTheDocument());
    expect(screen.getByText("+S$250 vs Aug 26")).toHaveClass("text-good");
  });

  it("keeps the note neutral without a goodWhen", async () => {
    render(<KpiState config={{ title: "Spending", query: QUERY }} />);
    await waitFor(() => expect(screen.getByText("−S$1,150 vs Sep 26")).toBeInTheDocument());
    expect(screen.getByText("−S$1,150 vs Sep 26")).toHaveClass("text-ink-2");
  });

  it("shows no change note with a single point", async () => {
    stubApi({ ...BY_MONTH, labels: ["2026-10"], series: [{ name: "spend", values_minor: [60_000] }] });
    render(<KpiState config={{ title: "Spending", query: QUERY }} />);
    await waitFor(() => expect(screen.getByText("S$600")).toBeInTheDocument());
    expect(screen.queryByText(/vs/)).not.toBeInTheDocument();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();   // a one-point run draws no sparkline
  });

  it("shows 'No data' when the answer holds no figures", async () => {
    stubApi({ ...BY_MONTH, labels: [], series: [{ name: "spend", values_minor: [] }] });
    render(<KpiState config={{ title: "Spending", query: QUERY }} />);
    await waitFor(() => expect(screen.getByText("No data")).toBeInTheDocument());
  });

  it("shows the server's error rather than a blank tile", async () => {
    stubApi({ error: "For spending by month, metric is one of spend, income, transfers, net" }, 400);
    render(<KpiState config={{ title: "Spending", query: QUERY }} />);
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(/metric is one of/));
  });

  it("marks a projection answer's figure as an estimate", async () => {
    stubApi({
      ...BY_MONTH, source: "forward-view",
      note: "a projection: what the forward view expects, not a promise",
    });
    render(<KpiState config={{ title: "Spending", query: QUERY }} />);
    await waitFor(() => expect(screen.getByText(/a projection: what the forward view expects/i)).toBeInTheDocument());
  });
});
