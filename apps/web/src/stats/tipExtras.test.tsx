import { render, screen, within } from "@testing-library/react";
import { Tip } from "./StatChart";
import { plan, type StatResult } from "./hint";
import { tipExtras } from "./tipExtras";

// G13: the figures a stat's Details table held (its rows, every column) are now
// in the chart's hover card. G20: a row's money is cents under `<name>_minor`.
// Every figure here is invented.
const lines = (id: string, row: Record<string, unknown>, xKey: string, series: string[], unit = "sgd_minor", units?: Record<string, string>) =>
  Object.fromEntries(tipExtras(id, row, xKey, series, units, unit).lines.map((l) => [l.name, l.text]));

describe("tipExtras, card by card", () => {
  it("Monthly cash flow: investing, net after investing and transfers out", () => {
    const row = { month: "2026-09", income_minor: 500000, spend_minor: 600000, net_minor: -100000, investing_minor: 100000,
      net_after_investing_minor: -200000, transfers_out_minor: 87300, partial: false };
    expect(lines("monthly-cashflow", row, "month", ["income_minor", "spend_minor", "net_minor"])).toEqual({
      Investing: "S$1,000.00", "Net after investing": "−S$2,000.00", "Transfers out": "S$873.00",
    });
  });

  it("Monthly cash flow: says when the month's statements are incomplete", () => {
    const row = { month: "2024-05", income_minor: 0, spend_minor: 60000, net_minor: -60000, investing_minor: 0,
      net_after_investing_minor: -60000, transfers_out_minor: 0, partial: true };
    const extra = tipExtras("monthly-cashflow", row, "month", ["income_minor", "spend_minor", "net_minor"], undefined, "sgd_minor");
    expect(extra.notes).toEqual(["Some statements for this month are missing"]);
    // a figure at zero says nothing
    expect(extra.lines.map((l) => l.name)).toEqual(["Net after investing"]);
  });

  it("Needs, wants and savings: income, the transfers and each share of income", () => {
    const row = { month: "2026-09", income_minor: 400000, need_minor: 100000, want_minor: 80000, savings_minor: 60000, transfer_minor: 9000,
      unclassified_minor: 0, need_share: 0.25, want_share: 0.2, savings_share: 0.15 };
    expect(lines("needs-wants-savings", row, "month", ["need_minor", "want_minor", "savings_minor"])).toEqual({
      Income: "S$4,000.00", Transfer: "S$90.00", "Need share": "25%", "Want share": "20%", "Savings share": "15%",
    });
  });

  it("Eating out: the meals and the average ticket for the week", () => {
    expect(lines("eating-out", { week: "2026-09-07", count: 6, spend_minor: 10500, avg_ticket_minor: 1750 }, "week", ["spend_minor"])).toEqual({
      Count: "6", "Avg ticket": "S$17.50",
    });
  });

  it("Income stability: each source of the month's other income, the total and Salary still left out", () => {
    const row = { month: "2026-09", total_minor: 580000, salary_minor: 500000, other_minor: 80000, has_salary: true, partial: false,
      Salary_minor: 500000, Interest_minor: 1250, "Refunds and cashback_minor": 78750, Freelance_minor: 0 };
    expect(lines("income-stability", row, "month", ["salary_minor", "other_minor"])).toEqual({
      Interest: "S$12.50", "Refunds and cashback": "S$787.50",
    });
  });

  it("Cash and runway: spending, each account's balance named without the suffix, and what is carried", () => {
    const row = {
      month: "2026-09", cash_minor: 330266, spend_minor: 300000, carried: ["Wallet A"], carried_amount_minor: 108,
      not_started: ["Wallet B", "Wallet C"], complete: true, partial: false,
      "Main account_minor": 300000, "Wallet A_minor": 108, "Wallet B_minor": 0, runway_months: 1.1,
    };
    const extra = tipExtras("savings-runway", row, "month", ["cash_minor", "runway_months"], { runway_months: "months" }, "sgd_minor");
    expect(Object.fromEntries(extra.lines.map((l) => [l.name, l.text]))).toEqual({
      Spend: "S$3,000.00", "Carried amount": "S$1.08", "Main account": "S$3,000.00", "Wallet A": "S$1.08",
    });
    expect(extra.notes).toEqual(["1 account shows an earlier month's balance", "2 accounts have no statement yet"]);
  });

  it("Data quality: the rows behind each month's bar, as a count, not money", () => {
    const row = { month: "2026-09", rows: 141, uncategorised: 3, synthetic: 0, partial: true };
    const extra = tipExtras("data-quality", row, "month", ["uncategorised", "synthetic"], undefined, "count");
    expect(extra.lines).toEqual([{ key: "rows", name: "Rows", text: "141" }]);
    expect(extra.notes).toEqual(["Some statements for this month are missing"]);
  });

  it("keeps an account whose name differs from a plotted series only by case", () => {
    const row = { month: "2026-09", cash_minor: 330266, runway_months: 1, Cash_minor: 12000, "Main account_minor": 300000 };
    expect(lines("savings-runway", row, "month", ["cash_minor", "runway_months"], "sgd_minor", { runway_months: "months" })).toEqual({
      Cash: "S$120.00", "Main account": "S$3,000.00",
    });
  });

  it("writes a figure under a bare name as the plain number it is, never as money", () => {
    // a dollar figure from before G20, an account's and a snake_case one alike
    const row = { month: "2026-09", cash_minor: 330266, "Main account": 3000, carried_amount: 1.08 };
    expect(lines("savings-runway", row, "month", ["cash_minor"])).toEqual({ "Main account": "3,000", "Carried amount": "1.1" });
  });

  it("says nothing for a row with nothing extra", () => {
    expect(tipExtras("x", { month: "2026-09", spend_minor: 500 }, "month", ["spend_minor"], undefined, "sgd_minor")).toEqual({ lines: [], notes: [] });
    expect(tipExtras("x", undefined, "month", ["spend_minor"])).toEqual({ lines: [], notes: [] });
  });
});

describe("the hover card lists them under the series", () => {
  const stacked = plan({
    id: "monthly-cashflow", title: "Cash flow",
    chart: { type: "bar", x: "month", series: ["income_minor", "spend_minor", "net_minor"], unit: "sgd_minor" },
    data: { months: [{ month: "2026-09", income_minor: 500000, spend_minor: 600000, net_minor: -100000, investing_minor: 100000 }] },
  } as StatResult);
  if (stacked.kind !== "chart") throw new Error("expected a chart");
  const row = { month: "2026-09", income_minor: 500000, spend_minor: 600000, net_minor: -100000, investing_minor: 100000, partial: true };
  const items = [{ dataKey: "spend_minor", value: 600000, color: "red", payload: row }];

  it("draws the extra figures and notes in their own part, under the series", () => {
    const extras = (r: Record<string, unknown>) => tipExtras("monthly-cashflow", r, "month", stacked.series, undefined, "sgd_minor");
    const { container } = render(<Tip active x="2026-09" items={items} p={stacked} total={false} clickable={false} extras={extras} />);
    const more = container.querySelector("[data-part=tip-more]") as HTMLElement;
    expect(within(more).getByText("Investing").parentElement).toHaveTextContent("S$1,000.00");
    expect(within(more).getByText("Some statements for this month are missing")).toBeInTheDocument();
    expect(screen.getByText("Spend").compareDocumentPosition(more) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("draws no extra part when there is nothing extra", () => {
    const { container } = render(<Tip active x="2026-09" items={items} p={stacked} total={false} clickable={false}
      extras={() => ({ lines: [], notes: [] })} />);
    expect(container.querySelector("[data-part=tip-more]")).toBeNull();
  });
});
