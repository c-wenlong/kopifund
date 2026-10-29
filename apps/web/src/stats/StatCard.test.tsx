import { render, screen } from "@testing-library/react";
import { StatCard } from "./StatCard";
import type { StatResult } from "./hint";

describe("StatCard", () => {
  it("shows ratio and scaled percent columns correctly in the table", () => {
    const result: StatResult = {
      id: "category-breakdown",
      title: "Categories",
      data: { categories: [{ category: "Rent", share: 0.3, change_share: 0.25, gain_pct: 1.2, share_pct: 0.45 }] },
    };
    render(<StatCard result={result} />);
    expect(screen.getByText("30%")).toBeInTheDocument();
    expect(screen.getByText("25%")).toBeInTheDocument();
    expect(screen.getByText("1.2%")).toBeInTheDocument();
    expect(screen.getByText("0.5%")).toBeInTheDocument();
  });

  it("shows an empty state for an empty explicit source, not another list", () => {
    const result: StatResult = {
      id: "recurring-detector",
      title: "Recurring",
      chart: { type: "bar", x: "payee", series: ["monthly_cost_minor"], source: "active", unit: "sgd_minor" },
      data: { active: [], ended: [{ payee: "Gym", monthly_cost_minor: 5000 }] },
    };
    render(<StatCard result={result} />);
    expect(screen.getByText(/no rows/i)).toBeInTheDocument();
    expect(screen.queryByText("Gym")).not.toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });
});

describe("StatCard without Details", () => {
  const result: StatResult = {
    id: "monthly-cashflow",
    title: "Monthly cash flow",
    chart: { type: "bar", x: "month", series: ["spend_minor"], unit: "sgd_minor" },
    data: { months: [{ month: "2026-08", spend_minor: 10000 }, { month: "2026-09", spend_minor: 20000 }] },
    findings: ["Spending rose."],
    caveats: ["Partial month."],
    benchmarks: [{ name: "A reference", value: 3 }],
    generated_at: "2026-10-01T00:00:00Z",
    sources: [{ database: "Transactions", rows_used: 5 }],
  };

  it("has no Details toggle, and shows no findings, caveats, references or sources", () => {
    render(<StatCard result={result} />);
    expect(screen.queryByRole("button", { name: /details/i })).not.toBeInTheDocument();
    expect(screen.queryByText("Spending rose.")).not.toBeInTheDocument();
    expect(screen.queryByText("Partial month.")).not.toBeInTheDocument();
    expect(screen.queryByText(/reference numbers/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/computed/i)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /the numbers/i })).not.toBeInTheDocument();
  });
});
