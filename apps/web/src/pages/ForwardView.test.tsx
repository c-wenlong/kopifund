import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ForwardChart, ForwardViewCard, lowPoint, points, underZero } from "./ForwardView";
import { json, useSizedCharts } from "../test-helpers";

// Every figure here is invented. `month` is a month as the stat writes it since
// G20: each figure in integer cents under `<name>_minor` and no dollars at all.
// `dollars` is a month from before G17, which carried no cents.
const dollars = (m: string, cash: number, over: Record<string, number> = {}) => ({
  month: m, gross: 4000, cpf_employee: 800, take_home: 3200, other_income: 0, spend: 2500.5, trips: 0, one_offs: 0,
  investing: 300, insurance: 0, other_outflow: 99.5, repayments: 0, net: 300, cash, ...over,
});
const month = (m: string, cash: number, over: Record<string, number> = {}) =>
  Object.fromEntries(Object.entries(dollars(m, cash, over)).map(([k, v]) =>
    typeof v === "number" ? [`${k}_minor`, Math.round(v * 100)] : [k, v])) as { month: string } & Record<string, unknown>;
const months = [
  month("2026-10", 5000),
  month("2026-11", 3000.25, { trips: 1200, net: -900 }),
  month("2027-01", 1234.56),
  month("2027-02", 2000),
];

describe("forward view", () => {
  useSizedCharts();

  it("reads the stat's cents, which add up to the month's net", () => {
    const [oct, nov] = points(months);
    expect(oct).toMatchObject({ cash: 500_000, income: 320_000, spending: 250_050, other: 39_950, net: 30_000 });
    expect(oct.income - oct.spending - oct.other).toBe(oct.net);
    expect(nov).toMatchObject({ spending: 370_050, trips: 120_000 });
    expect(nov.income - nov.spending - nov.other).toBe(nov.net);
  });

  it("takes the stat's own low point, and reads one off the months for an older result", () => {
    const rows = points(months);
    expect(lowPoint({ low_point: { month: "2026-11", cash_minor: 300_025 } }, rows)).toEqual({ month: "2026-11", cash_minor: 300_025 });
    expect(lowPoint({}, rows)).toEqual({ month: "2027-01", cash_minor: 123_456 });
    expect(lowPoint({}, [])).toBeNull();
  });

  it("marks the lowest month with a dot and says it once, in the key beside the chart, not again over the line", () => {
    const { container } = render(<ForwardChart rows={points(months)} low={{ month: "2027-01", cash_minor: 123_456 }} below={[]} />);
    expect(container.querySelector(".recharts-reference-dot")).not.toBeNull();
    // no text drawn in the chart for it: a label there sat over the cash line and repeated the key
    expect(container.querySelector(".recharts-reference-dot text")).toBeNull();
    expect(container.querySelector("svg")).not.toHaveTextContent(/Lowest/);
    expect(screen.getByText(/in Jan 2027/)).toHaveTextContent("Lowest S$1,235 in Jan 2027");
    expect(screen.getByRole("img", { name: "Cash by month to Feb 2027, lowest S$1,235 in Jan 2027" })).toBeInTheDocument();
    // nothing under zero: no band, no zero line
    expect(container.querySelector(".recharts-reference-area")).toBeNull();
    expect(container.querySelector(".recharts-reference-line")).toBeNull();
  });

  it("shades below zero and draws the zero line when cash goes under it", () => {
    const under = [month("2026-10", 800), month("2026-11", -450.1), month("2026-12", -120), month("2027-01", 300)];
    const { container } = render(<ForwardChart rows={points(under)} low={{ month: "2026-11", cash_minor: -45_010 }} below={["2026-11", "2026-12"]} />);
    expect(container.querySelector(".recharts-reference-area")).not.toBeNull();
    expect(container.querySelector(".recharts-reference-line")).not.toBeNull();
    expect(screen.getByText("Under zero: Nov to Dec 2026")).toBeInTheDocument();
    expect(screen.getByText(/in Nov 2026/)).toHaveTextContent("Lowest −S$450 in Nov 2026");
    expect(screen.getByRole("img")).toHaveAccessibleName(/under zero in Nov to Dec 2026/);
  });

  it("names each run under zero apart, never the months between them", () => {
    const rows = points([month("2026-10", -1), month("2026-11", -2), month("2026-12", 5), month("2027-01", -3), month("2027-02", 4)]);
    expect(underZero(["2026-10", "2026-11", "2027-01"], rows)).toBe("Oct to Nov 2026, Jan 2027");
    expect(underZero(["2027-01"], rows)).toBe("Jan 2027");
    const { container } = render(<ForwardChart rows={rows} low={{ month: "2027-01", cash_minor: -300 }} below={["2026-10", "2026-11", "2027-01"]} />);
    expect(screen.getByText("Under zero: Oct to Nov 2026, Jan 2027")).toBeInTheDocument();
    expect(screen.getByRole("img")).toHaveAccessibleName(/under zero in Oct to Nov 2026, Jan 2027$/);
    expect(container.querySelector(".recharts-reference-area")).not.toBeNull();
  });

  it("shows the month's cash, income, spending and net on hover", () => {
    const { container } = render(<ForwardChart rows={points(months)} low={null} below={[]} />);
    const chart = container.querySelector(".recharts-wrapper")!;
    // four months over 800 wide: the second sits a third of the way across
    fireEvent.mouseMove(chart, { clientX: 300, clientY: 120 });
    const tip = screen.getByTestId("forward-tip");
    expect(tip).toHaveTextContent("Nov 2026");
    const line = (name: string) => within(tip).getByText(name).parentElement!;
    expect(line("Cash at month end")).toHaveTextContent("S$3,000.25");
    expect(line("Income")).toHaveTextContent("S$3,200.00");
    expect(line("Spending")).toHaveTextContent("with trips S$1,200");
    expect(line("Spending")).toHaveTextContent("S$3,700.50");
    expect(line("Investing and other")).toHaveTextContent("S$399.50");
    // to the cent, the figures shown add up: 3,200.00 - 3,700.50 - 399.50
    expect(line("Net")).toHaveTextContent("−S$900.00");
  });

  it("reads the cents only, never the dollar twins a result from before G20 carried beside them", () => {
    const [oct] = points([{ ...month("2026-10", 5000), cash: 1, net: 1, spend: 1 }]);
    expect(oct).toMatchObject({ cash: 500_000, net: 30_000, spending: 250_050 });
  });

  it("waits for the next analysis when the result's months carry no cents", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json({
      id: "forward-view", title: "Forward view to Feb 2027", chart: { type: "line", x: "month", series: ["cash"], source: "months" },
      data: { months: [dollars("2026-10", 5000)], low_point: { month: "2026-10", cash_minor: 500_000 }, below_zero: [] },
      findings: [], caveats: [],
    })));
    render(<ForwardViewCard />);
    expect(await screen.findByText("No forward view result yet.")).toBeInTheDocument();
  });

  it("shows no Details toggle and none of the stat's findings", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json({
      id: "forward-view", title: "Forward view to Feb 2027", chart: { type: "line", x: "month", series: ["cash_minor"], source: "months", unit: "sgd_minor" },
      data: { months, low_point: { month: "2027-01", cash_minor: 123_456 }, below_zero: [] },
      findings: ["An invented finding about the low point."], caveats: ["An invented caveat."],
    })));
    render(<ForwardViewCard />);
    expect(await screen.findByRole("heading", { name: "Forward view to Feb 2027" })).toBeInTheDocument();
    expect(await screen.findByText(/in Jan 2027/)).toHaveTextContent("Lowest S$1,235 in Jan 2027");
    expect(screen.queryByRole("button", { name: /details/i })).not.toBeInTheDocument();
    expect(screen.queryByText("An invented finding about the low point.")).not.toBeInTheDocument();
    expect(screen.queryByText("An invented caveat.")).not.toBeInTheDocument();
  });

  it("splits investing and other on hover, and shows the pay before CPF and the CPF going in", () => {
    const rich = [
      month("2026-10", 5000),
      month("2026-11", 3000, { investing: 300, insurance: 17.42, other_outflow: 82.08, cpf_total: 1480, gross: 5000 }),
    ];
    const { container } = render(<ForwardChart rows={points(rich)} low={null} below={[]} />);
    fireEvent.mouseMove(container.querySelector(".recharts-wrapper")!, { clientX: 600, clientY: 120 });
    const tip = screen.getByTestId("forward-tip");
    const line = (name: string) => within(tip).getByText(name).parentElement!;
    expect(line("Investing and other")).toHaveTextContent("S$399.50");
    expect(line("Investing")).toHaveTextContent("S$300.00");
    expect(line("Insurance")).toHaveTextContent("S$17.42");
    expect(line("Other outflow")).toHaveTextContent("S$82.08");
    expect(within(tip).queryByText("Repayments")).not.toBeInTheDocument();
    expect(line("Pay before CPF")).toHaveTextContent("S$5,000.00");
    expect(line("CPF going in, not cash")).toHaveTextContent("S$1,480.00");
  });

  it("does not split a single outflow into a row of its own", () => {
    const { container } = render(<ForwardChart rows={points([month("2026-10", 5000, { investing: 300, other_outflow: 0 }), month("2026-11", 3000, { investing: 300, other_outflow: 0 })])} low={null} below={[]} />);
    fireEvent.mouseMove(container.querySelector(".recharts-wrapper")!, { clientX: 600, clientY: 120 });
    const tip = screen.getByTestId("forward-tip");
    expect(within(tip).queryByText("Investing")).not.toBeInTheDocument();
  });
});
