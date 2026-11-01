import { render, screen, within } from "@testing-library/react";
import { StatCard } from "./StatCard";
import type { StatResult } from "./hint";

// G13: the figures a few cards' findings used to carry as prose are tiles above
// the chart. Every figure here is invented.
const tile = (label: string) => within(screen.getByRole("group")).getByText(label).parentElement as HTMLElement;

describe("data quality tiles", () => {
  const result: StatResult = {
    id: "data-quality", title: "Data quality",
    chart: { type: "bar", x: "month", series: ["uncategorised", "synthetic"], source: "by_month", unit: "count" },
    data: {
      counts: { rows: 4000, uncategorised: 12, uncategorised_out_minor: 34560, synthetic: 30, unconverted: 2, partial_months: 7 },
      by_month: [{ month: "2026-09", rows: 100, uncategorised: 12, synthetic: 30, partial: false }],
    },
    findings: ["12 of 4000 rows have no category."],
  };

  it("counts the rows with no category, the stand-ins, the rows with no rate and the months missing statements", () => {
    render(<StatCard result={result} />);
    const grid = within(screen.getByRole("group", { name: "Data quality, in figures" }));
    expect(grid.getByText("Rows with no category").parentElement).toHaveTextContent("12");
    expect(grid.getByText("Rows with no category").parentElement).toHaveTextContent("S$346 out");
    expect(tile("Stand-in rows")).toHaveTextContent("30");
    expect(tile("Rows with no exchange rate")).toHaveTextContent("2");
    expect(tile("Months missing statements")).toHaveTextContent("7");
    expect(screen.queryByText("12 of 4000 rows have no category.")).not.toBeInTheDocument();
  });

  it("colours only a count that is a problem", () => {
    render(<StatCard result={{ ...result, data: { ...result.data, counts: { uncategorised: 0, synthetic: 5, unconverted: 0, partial_months: 3 } } }} />);
    expect(within(tile("Rows with no category")).getByText("0")).not.toHaveClass("text-warn");
    expect(within(tile("Stand-in rows")).getByText("5")).not.toHaveClass("text-warn");
    expect(within(tile("Months missing statements")).getByText("3")).toHaveClass("text-warn");
  });

  it("draws no tiles when the result has no counts", () => {
    render(<StatCard result={{ ...result, data: { by_month: result.data!.by_month } }} />);
    expect(screen.queryByRole("group")).not.toBeInTheDocument();
  });
});

describe("cash and runway tiles", () => {
  const result: StatResult = {
    id: "savings-runway", title: "Cash and runway",
    chart: { type: "line", x: "month", series: ["cash_minor", "runway_months"], source: "months", unit: "sgd_minor", units: { runway_months: "months" } },
    data: {
      months: [{ month: "2026-09", cash_minor: 330266, runway_months: 1 }],
      summary: {
        cash_minor: 330266, as_of: "2026-09", runway_6m: 1, runway_all: 1.24, spend_avg_6m_minor: 344226, spend_avg_all_minor: 265325,
        trend_6m_minor: -775502, rule_low: 3, rule_high: 6,
      },
    },
    findings: ["Cash across 6 accounts is invented."],
  };

  it("shows the cash, the runway at both spending rates and the six-month change", () => {
    render(<StatCard result={result} />);
    expect(tile("Cash")).toHaveTextContent("S$3,303");
    expect(tile("Cash")).toHaveTextContent("at Sep 2026");
    expect(tile("Runway, 6 months")).toHaveTextContent("1.0 mo");
    expect(tile("Runway, 6 months")).toHaveTextContent("S$3,442 a month");
    expect(tile("Runway, all months")).toHaveTextContent("1.2 mo");
    expect(tile("Cash over 6 months")).toHaveTextContent("−S$7,755");
    expect(screen.queryByText("Cash across 6 accounts is invented.")).not.toBeInTheDocument();
  });

  it("colours a runway under the low end of the usual range, and cash that fell", () => {
    render(<StatCard result={result} />);
    expect(within(tile("Runway, 6 months")).getByText("1.0 mo")).toHaveClass("text-warn");
    // the note is a figure, not a warning
    expect(within(tile("Runway, 6 months")).getByText("S$3,442 a month")).not.toHaveClass("text-warn");
    expect(within(tile("Cash over 6 months")).getByText("−S$7,755")).toHaveClass("text-warn");
  });

  it("leaves a healthy runway and cash that grew in ink", () => {
    const healthy = { ...result, data: { ...result.data, summary: { ...(result.data!.summary as object), runway_6m: 4, runway_all: 5, trend_6m_minor: 90000 } } };
    render(<StatCard result={healthy} />);
    expect(within(tile("Runway, 6 months")).getByText("4.0 mo")).not.toHaveClass("text-warn");
    expect(within(tile("Cash over 6 months")).getByText("S$900")).not.toHaveClass("text-warn");
  });
});

describe("needs, wants and savings tiles", () => {
  const result: StatResult = {
    id: "needs-wants-savings", title: "Needs, wants and savings",
    chart: { type: "stacked", x: "month", series: ["need_minor", "want_minor", "savings_minor"], source: "months", unit: "sgd_minor" },
    data: {
      months: [{ month: "2026-09", income_minor: 400000, need_minor: 100000, want_minor: 80000, savings_minor: 60000 }],
      summary: {
        need_share: 0.29, want_share: 0.52, savings_share: 0.23,
        need_per_month_minor: 95427, want_per_month_minor: 168789, savings_per_month_minor: 75000,
        targets: { need: 0.5, want: 0.3, savings: 0.2 },
      },
    },
  };

  it("shows each share of income against its rule, with what it is a month", () => {
    render(<StatCard result={result} />);
    expect(tile("Needs, of income")).toHaveTextContent("29%");
    expect(tile("Needs, of income")).toHaveTextContent("rule 50% · S$954 a month");
    expect(tile("Wants, of income")).toHaveTextContent("52%");
    expect(tile("Savings, of income")).toHaveTextContent("rule 20% · S$750 a month");
  });

  it("colours wants over their rule, and leaves needs under theirs and savings over theirs alone", () => {
    render(<StatCard result={result} />);
    expect(within(tile("Wants, of income")).getByText("52%")).toHaveClass("text-warn");
    expect(within(tile("Needs, of income")).getByText("29%")).not.toHaveClass("text-warn");
    expect(within(tile("Savings, of income")).getByText("23%")).not.toHaveClass("text-warn");
  });

  it("colours savings under their rule", () => {
    const short = { ...result, data: { ...result.data, summary: { ...(result.data!.summary as object), savings_share: 0.05 } } };
    render(<StatCard result={short} />);
    expect(within(tile("Savings, of income")).getByText("5%")).toHaveClass("text-warn");
  });
});

describe("a card without tiles", () => {
  it("draws its chart alone", () => {
    render(<StatCard result={{
      id: "eating-out", title: "Eating out", chart: { type: "line", x: "week", series: ["spend_minor"], unit: "sgd_minor" },
      data: { weeks: [{ week: "2026-09-07", spend_minor: 10000 }] },
    }} />);
    expect(screen.queryByRole("group")).not.toBeInTheDocument();
  });
});
