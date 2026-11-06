import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { stubQueries } from "./testkit";
import { StackedAreaChart, StackedAreaWidget, type StackedAreaConfig } from "./StackedArea";
import { joinFrames } from "./shared";
import type { WidgetAnswer, WidgetQuery } from "../../../types.gen";
import { navigate, useSizedCharts } from "../../../test-helpers";

vi.mock("../../../router", async (actual) => (await import("../../../test-helpers")).withNavigate(await actual()));

useSizedCharts();

const CASH: WidgetQuery = { source: "net-worth", metric: "cash", dimension: "month" };
const INVEST: WidgetQuery = { source: "net-worth", metric: "investments", dimension: "month" };
const CPF: WidgetQuery = { source: "net-worth", metric: "cpf", dimension: "month" };

const worth = (metric: string, values: number[]): WidgetAnswer => ({
  source: "net-worth", metric, dimension: "month", unit: "sgd_minor",
  labels: ["2026-08", "2026-09", "2026-10"],
  series: [{ name: metric, values_minor: values }],
  range: { from: "2026-08", to: "2026-10" },
});

const stubs = (): [WidgetQuery, WidgetAnswer][] => [
  [CASH, worth("cash", [5000000, 5200000, 5400000])],
  [INVEST, worth("investments", [8000000, 8100000, 8400000])],
  [CPF, worth("cpf", [3000000, 3100000, 3200000])],
];

const config: StackedAreaConfig = {
  id: "area-test", title: "Net worth",
  series: [
    { query: CASH, label: "Cash" },
    { query: INVEST, label: "Investments" },
    { query: CPF, label: "CPF" },
  ],
};

const frame = () => joinFrames(config.series, stubs().map(([, a]) => a as WidgetAnswer));

const areas = (c: HTMLElement) => c.querySelectorAll(".recharts-area-curve");
const lines = (c: HTMLElement) => c.querySelectorAll(".recharts-line-curve");

describe("StackedAreaWidget", () => {
  it("draws the series piled up, with the Stacked / Lines / Total switch in its header", async () => {
    stubQueries(stubs());
    const { container } = render(<StackedAreaWidget config={config} />);
    await waitFor(() => expect(areas(container)).toHaveLength(3));
    expect(screen.getByRole("radiogroup", { name: "Chart view for Net worth" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "Stacked" })).toHaveAttribute("aria-checked", "true");
  });

  it("switches to one line a series, then the one total line", async () => {
    stubQueries(stubs());
    const { container } = render(<StackedAreaWidget config={config} />);
    await waitFor(() => expect(areas(container)).toHaveLength(3));
    fireEvent.click(screen.getByRole("radio", { name: "Lines" }));
    expect(lines(container)).toHaveLength(3);
    expect(areas(container)).toHaveLength(0);
    fireEvent.click(screen.getByRole("radio", { name: "Total" }));
    expect(lines(container)).toHaveLength(1);
    expect(container).toHaveTextContent("Total");
  });

  it("remembers the view the owner picked, per widget instance", async () => {
    stubQueries(stubs());
    render(<StackedAreaWidget config={config} />);
    await waitFor(() => expect(screen.getByRole("radio", { name: "Lines" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("radio", { name: "Lines" }));
    expect(window.localStorage.getItem("kopifund.chart-view.widget-area-test")).toBe("lines");
  });
});

describe("StackedAreaChart", () => {
  it("hides a part from its legend entry and keeps the stack going", () => {
    const { container } = render(<StackedAreaChart frame={frame()} view="stacked" />);
    fireEvent.click(screen.getByRole("button", { name: "Investments" }));
    expect(areas(container)).toHaveLength(2);
  });

  it("opens the series of the dot clicked on Lines, in the month under the pointer", async () => {
    // invented links, the way a spending-split answer carries them
    const linked: WidgetAnswer = {
      source: "spending", metric: "spend", dimension: "month", unit: "sgd_minor",
      labels: ["2026-08", "2026-09"],
      label_links: [
        { from: "2026-08", to: "2026-08", spend: "1", home: "1" },
        { from: "2026-09", to: "2026-09", spend: "1", home: "1" },
      ],
      series: [
        { name: "Needs", values_minor: [200000, 210000], link: { spend: "1", need: "Need" } },
        { name: "Wants", values_minor: [100000, 90000], link: { spend: "1", need: "Want" } },
      ],
      range: { from: "2026-08", to: "2026-09" },
    };
    const { container } = render(<StackedAreaChart frame={joinFrames([{ query: CASH }], [linked])} view="lines" />);
    const wrapper = container.querySelector(".recharts-wrapper")!;
    fireEvent.mouseMove(wrapper, { clientX: 700, clientY: 100 });
    await waitFor(() => expect(container.querySelectorAll(".recharts-active-dot circle")).toHaveLength(2));
    fireEvent.click(wrapper, { clientX: 700, clientY: 100 });
    expect(navigate).not.toHaveBeenCalled();
    fireEvent.click(container.querySelectorAll(".recharts-active-dot circle")[0], { clientX: 700, clientY: 100 });
    expect(navigate).toHaveBeenLastCalledWith("/transactions?from=2026-09&to=2026-09&spend=1&home=1&need=Need");
  });
});
