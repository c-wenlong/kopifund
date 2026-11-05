import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { stubQueries, useSizedCharts } from "./testkit";
import {
  WaterfallChart, WaterfallWidget, waterfallSteps, type WaterfallConfig, type WaterfallStep,
} from "./Waterfall";
import { joinFrames } from "./shared";
import type { WidgetAnswer, WidgetQuery } from "../../../types.gen";

const navigate = vi.fn();
vi.mock("../../../router", () => ({ navigate: (to: string) => navigate(to) }));

useSizedCharts();

const CASH: WidgetQuery = { source: "savings-runway", metric: "cash", dimension: "month", range: { from: "2026-08", to: "2026-10" } };
const NET_BY_BUCKET: WidgetQuery = {
  source: "spending", metric: "net", dimension: "month", split: "bucket", range: { from: "2026-08", to: "2026-10" },
};

// invented: cash at each month end, and the month's net per bucket
const balance: WidgetAnswer = {
  source: "savings-runway", metric: "cash", dimension: "month", unit: "sgd_minor",
  labels: ["2026-08", "2026-09", "2026-10"],
  series: [{ name: "cash", values_minor: [900000, 970000, 940000] }],
  range: { from: "2026-08", to: "2026-10" },
};
const flows: WidgetAnswer = {
  source: "spending", metric: "net", dimension: "month", unit: "sgd_minor",
  labels: ["2026-08", "2026-09", "2026-10"],
  label_links: [
    { from: "2026-08", to: "2026-08", income: "1", spend: "1", transfer: "1", home: "1" },
    { from: "2026-09", to: "2026-09", income: "1", spend: "1", transfer: "1", home: "1" },
    { from: "2026-10", to: "2026-10", income: "1", spend: "1", transfer: "1", home: "1" },
  ],
  series: [
    { name: "Income", values_minor: [900000, 910000, 920000], link: { income: "1", bucket: "Income" } },
    { name: "Spend", values_minor: [-400000, -430000, -380000], link: { spend: "1", bucket: "Spend" } },
    { name: "Transfer", values_minor: [-200000, -100000, -100000], link: { transfer: "1", bucket: "Transfer" } },
  ],
  range: { from: "2026-08", to: "2026-10" },
};

const config: WaterfallConfig = { id: "wf-test", title: "Where cash went", balance: { query: CASH }, flows: [{ query: NET_BY_BUCKET }] };

const bars = (c: HTMLElement) => c.querySelectorAll(".recharts-bar-rectangle path");

describe("waterfallSteps", () => {
  const steps = waterfallSteps(
    joinFrames([{ query: CASH }], [balance]),
    joinFrames([{ query: NET_BY_BUCKET }], [flows]),
  );

  it("opens on the balance's first figure and closes on its last", () => {
    expect(steps[0]).toMatchObject({ name: "Opening", kind: "open", value: 900000, before: 0, after: 900000 });
    expect(steps.at(-1)).toMatchObject({ name: "Closing", kind: "close", value: 940000 });
  });

  it("sums each flow series to one step, inflows before outflows, the running level kept", () => {
    const names = steps.map((s) => s.name);
    expect(names).toEqual(["Opening", "Income", "Spend", "Transfer", "Closing"]);
    expect(steps[1]).toMatchObject({ kind: "in", value: 2730000, before: 900000, after: 3630000 });
    expect(steps[2]).toMatchObject({ kind: "out", value: -1210000, before: 3630000, after: 2420000 });
    expect(steps[3]).toMatchObject({ kind: "out", value: -400000, before: 2420000, after: 2020000 });
  });

  it("links a flow step to every row behind it over the range", () => {
    expect(steps[1].href).toBe("/transactions?from=2026-08&to=2026-10&income=1&spend=1&transfer=1&home=1&bucket=Income");
    expect(steps[0].href).toBeNull();
  });

  it("reads a balance with missing months off the figures it does have", () => {
    const thin: WidgetAnswer = { ...balance, series: [{ name: "cash", values_minor: [null, 970000, null] }] };
    const s = waterfallSteps(joinFrames([{ query: CASH }], [thin]), null);
    expect(s[0].value).toBe(970000);
    expect(s.at(-1)!.value).toBe(970000);
    expect(s.map((x) => x.name)).toEqual(["Opening", "Closing"]);
  });

  it("opens and closes on nothing when the balance answered no figure", () => {
    const none: WidgetAnswer = { ...balance, series: [{ name: "cash", values_minor: [null, null] }] };
    const s = waterfallSteps(joinFrames([{ query: CASH }], [none]), null);
    expect(s.map((x) => x.value)).toEqual([0, 0]);
  });
});

describe("WaterfallChart", () => {
  afterEach(() => navigate.mockReset());
  const steps: WaterfallStep[] = [
    { name: "Opening", kind: "open", value: 900000, before: 0, after: 900000, href: null },
    { name: "Income", kind: "in", value: 300000, before: 900000, after: 1200000, href: "/transactions?income=1" },
    { name: "Spend", kind: "out", value: -400000, before: 1200000, after: 800000, href: null },
    { name: "Closing", kind: "close", value: 800000, before: 800000, after: 800000, href: null },
  ];
  const renderChart = () => render(<WaterfallChart steps={steps} />);

  it("draws one labelled, floating bar a step", () => {
    const { container } = renderChart();
    expect(bars(container)).toHaveLength(4);
    const ticks = [...container.querySelectorAll(".recharts-xAxis .recharts-cartesian-axis-tick-value")].map((t) => t.textContent);
    expect(ticks).toEqual(["Opening", "Income", "Spend", "Closing"]);
  });

  it("colours a step down in danger, a step up in good, an endpoint in ink", () => {
    const { container } = renderChart();
    const fills = [...bars(container)].map((b) => b.getAttribute("fill"));
    expect(fills).toEqual(["var(--ink)", "var(--good)", "var(--danger)", "var(--ink)"]);
  });

  it("writes a negative change with a true minus in its hover card", async () => {
    const { container } = renderChart();
    const wrapper = container.querySelector(".recharts-wrapper")!;
    fireEvent.mouseMove(wrapper, { clientX: 500, clientY: 100 });
    await waitFor(() => expect(container).toHaveTextContent("Change"));
    expect(container.querySelector(".text-danger")).toHaveTextContent("−S$4,000.00");
  });

  it("opens a linked step's rows from its bar", () => {
    const { container } = renderChart();
    fireEvent.click(bars(container)[1]);   // Income's bar
    expect(navigate).toHaveBeenLastCalledWith("/transactions?income=1");
    fireEvent.click(bars(container)[2]);   // Spend has no link
    expect(navigate).toHaveBeenCalledTimes(1);
  });
});

describe("WaterfallWidget", () => {
  afterEach(() => { vi.unstubAllGlobals(); navigate.mockReset(); });

  it("draws the bridge from the queries' answers", async () => {
    stubQueries([[CASH, balance], [NET_BY_BUCKET, flows]]);
    const { container } = render(<WaterfallWidget config={config} />);
    await waitFor(() => expect(bars(container)).toHaveLength(5));
    expect(screen.getByText("Where cash went")).toBeInTheDocument();
    const ticks = [...container.querySelectorAll(".recharts-xAxis .recharts-cartesian-axis-tick-value")].map((t) => t.textContent);
    expect(ticks).toEqual(["Opening", "Income", "Spend", "Transfer", "Closing"]);
  });

  it("says there is nothing to show when the flows answer no series", async () => {
    const none: WidgetAnswer = { ...flows, series: [], labels: [], label_links: [] };
    stubQueries([[CASH, balance], [NET_BY_BUCKET, none]]);
    render(<WaterfallWidget config={config} />);
    await waitFor(() => expect(screen.getByText("Nothing to show yet")).toBeInTheDocument());
  });
});
