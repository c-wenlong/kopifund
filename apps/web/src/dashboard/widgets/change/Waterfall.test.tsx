import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { stubQueries } from "./testkit";
import {
  balanceSpec, DEFAULT_WATERFALL_CONFIG, WaterfallChart, WaterfallWidget, waterfallSteps,
  type WaterfallConfig, type WaterfallStep,
} from "./Waterfall";
import { joinFrames } from "./shared";
import type { WidgetAnswer, WidgetQuery } from "../../../types.gen";
import { navigate, SizedObserver, useSizedCharts } from "../../../test-helpers";

vi.mock("../../../router", async (actual) => (await import("../../../test-helpers")).withNavigate(await actual()));

useSizedCharts();

// the balance is asked for from the month before the flows: July's close opens the bridge
const CASH: WidgetQuery = { source: "savings-runway", metric: "cash", dimension: "month", range: { from: "2026-07", to: "2026-10" } };
const NET_BY_BUCKET: WidgetQuery = {
  source: "spending", metric: "net", dimension: "month", split: "bucket", range: { from: "2026-08", to: "2026-10" },
};

// invented: cash at each month end, and the month's net per bucket. The
// flows are every way the cash moves, so July's close plus them is October's
const balance: WidgetAnswer = {
  source: "savings-runway", metric: "cash", dimension: "month", unit: "sgd_minor",
  labels: ["2026-07", "2026-08", "2026-09", "2026-10"],
  series: [{ name: "cash", values_minor: [600000, 900000, 1280000, 1720000] }],
  range: { from: "2026-07", to: "2026-10" },
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

const config: WaterfallConfig = {
  id: "wf-test", title: "Where cash went",
  balance: { query: { ...CASH, range: undefined } }, flows: [{ query: { ...NET_BY_BUCKET, range: undefined } }],
  range: { from: "2026-08", to: "2026-10" },
};

const bars = (c: HTMLElement) => c.querySelectorAll(".recharts-bar-rectangle path");

describe("waterfallSteps", () => {
  const steps = waterfallSteps(
    joinFrames([{ query: CASH }], [balance]),
    joinFrames([{ query: NET_BY_BUCKET }], [flows]),
  )!;

  it("opens on the close of the month before the flows and closes on their last month's", () => {
    expect(steps[0]).toMatchObject({ name: "Opening", kind: "open", value: 600000, before: 0, after: 600000 });
    expect(steps.at(-1)).toMatchObject({ name: "Closing", kind: "close", value: 1720000 });
  });

  it("sums each flow series to one step, inflows before outflows, the running level kept", () => {
    const names = steps.map((s) => s.name);
    expect(names).toEqual(["Opening", "Income", "Spend", "Transfer", "Closing"]);
    expect(steps[1]).toMatchObject({ kind: "in", value: 2730000, before: 600000, after: 3330000 });
    expect(steps[2]).toMatchObject({ kind: "out", value: -1210000, before: 3330000, after: 2120000 });
    expect(steps[3]).toMatchObject({ kind: "out", value: -400000, before: 2120000, after: 1720000 });
  });

  it("reconciles: the last step lands on the closing balance, one month or several", () => {
    expect(steps[3].after).toBe(steps.at(-1)!.value);
    // one month: August's flows bridge July's close to August's, never counting August twice
    const aug: WidgetAnswer = { ...flows, labels: ["2026-08"], label_links: [flows.label_links![0]],
      series: flows.series.map((s) => ({ ...s, values_minor: [s.values_minor![0]] })) };
    const one = waterfallSteps(joinFrames([{ query: CASH }], [balance]), joinFrames([{ query: NET_BY_BUCKET }], [aug]))!;
    expect(one[0].value).toBe(600000);
    expect(one.at(-1)!.value).toBe(900000);
    expect(one.at(-2)!.after).toBe(900000);
  });

  it("opens on the first figure when the record starts with the flows, and leaves that month's flows out", () => {
    // no close before August: August's close opens, so only September and October bridge it
    const late: WidgetAnswer = { ...balance, labels: balance.labels.slice(1),
      series: [{ name: "cash", values_minor: balance.series[0].values_minor!.slice(1) }] };
    const s = waterfallSteps(joinFrames([{ query: CASH }], [late]), joinFrames([{ query: NET_BY_BUCKET }], [flows]))!;
    expect(s[0].value).toBe(900000);
    expect(s.find((x) => x.name === "Income")!.value).toBe(910000 + 920000);
    expect(s.find((x) => x.name === "Income")!.href).toContain("from=2026-09&to=2026-10");
    expect(s.at(-2)!.after).toBe(s.at(-1)!.value);
  });

  it("asks for the balance from the month before the range, and keeps a range the balance set itself", () => {
    expect(balanceSpec(config).query.range).toEqual({ from: "2026-07", to: "2026-10" });
    const own = { ...config, balance: { query: { ...CASH, range: { from: "2026-01-15", to: "2026-03" } } } };
    expect(balanceSpec(own).query.range).toEqual({ from: "2025-12", to: "2026-03" });
    // the default names no month: the flows follow the record and the balance is asked whole
    expect(DEFAULT_WATERFALL_CONFIG.range).toBeUndefined();
    expect(balanceSpec(DEFAULT_WATERFALL_CONFIG).query.range).toBeUndefined();
  });

  it("links a flow step to every row behind it over the range", () => {
    expect(steps[1].href).toBe("/transactions?from=2026-08&to=2026-10&income=1&spend=1&transfer=1&home=1&bucket=Income");
    expect(steps[0].href).toBeNull();
  });

  it("bounds a flow step's link by its own answer, not the combined frame's", () => {
    const aug: WidgetAnswer = { ...flows, labels: ["2026-08"], label_links: [flows.label_links![0]],
      series: [{ name: "Income", values_minor: [900000], link: { income: "1" } }] };
    const sep: WidgetAnswer = { ...flows, labels: ["2026-09"], label_links: [flows.label_links![1]],
      series: [{ name: "Spend", values_minor: [-430000], link: { spend: "1" } }] };
    const joined = joinFrames(
      [{ query: { ...NET_BY_BUCKET, range: { from: "2026-08", to: "2026-08" } } },
       { query: { ...NET_BY_BUCKET, range: { from: "2026-09", to: "2026-09" } } }],
      [aug, sep]);
    const s = waterfallSteps(joinFrames([{ query: CASH }], [balance]), joined)!;
    expect(s.find((x) => x.name === "Income")!.href).toContain("from=2026-08&to=2026-08");
    expect(s.find((x) => x.name === "Spend")!.href).toContain("from=2026-09&to=2026-09");
  });

  it("does not link a step whose series cannot be filtered", () => {
    const f: WidgetAnswer = { ...flows, series: [...flows.series, { name: "(none)", values_minor: [10, 10, 10], link: null }] };
    const s = waterfallSteps(joinFrames([{ query: CASH }], [balance]), joinFrames([{ query: NET_BY_BUCKET }], [f]))!;
    expect(s.find((x) => x.name === "(none)")!.href).toBeNull();
  });

  it("reads a balance with missing months off the figures it does have", () => {
    const thin: WidgetAnswer = { ...balance, series: [{ name: "cash", values_minor: [null, 970000, null, null] }] };
    const s = waterfallSteps(joinFrames([{ query: CASH }], [thin]), null)!;
    expect(s[0].value).toBe(970000);
    expect(s.at(-1)!.value).toBe(970000);
    expect(s.map((x) => x.name)).toEqual(["Opening", "Closing"]);
  });

  it("is null when the balance answered no figure, rather than reading it as zero", () => {
    const none: WidgetAnswer = { ...balance, series: [{ name: "cash", values_minor: [null, null] }] };
    expect(waterfallSteps(joinFrames([{ query: CASH }], [none]), null)).toBeNull();
  });
});

describe("WaterfallChart", () => {
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

  it("thins its names on a narrow card so none overlap, keeping the first and last", () => {
    class Narrow extends SizedObserver {
      observe(el: Element) {
        (this as unknown as { cb: ResizeObserverCallback }).cb(
          [{ target: el, contentRect: { width: 300, height: 400 } } as unknown as ResizeObserverEntry], this as unknown as ResizeObserver);
      }
    }
    vi.stubGlobal("ResizeObserver", Narrow);
    // jsdom measures no text, so Recharts would see every name as 0 wide and thin nothing: 7px a character
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
      const w = (this.textContent ?? "").length * 7;
      return { width: w, height: 12, top: 0, left: 0, right: w, bottom: 12, x: 0, y: 0, toJSON() {} };
    });
    const many: WaterfallStep[] = [
      steps[0],
      ...["Groceries and food", "Transport costs", "Entertainment", "Subscriptions", "Healthcare", "Travel bookings", "Education fees"]
        .map((name): WaterfallStep => ({ name, kind: "out", value: -1000, before: 0, after: 0, href: null })),
      steps[3],
    ];
    const { container } = render(<WaterfallChart steps={many} />);
    const ticks = [...container.querySelectorAll(".recharts-xAxis .recharts-cartesian-axis-tick-value")].map((t) => t.textContent);
    expect(ticks.length).toBeLessThan(many.length);
    expect(ticks[0]).toBe("Opening");
    expect(ticks[ticks.length - 1]).toBe("Closing");
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

  it("says the balance data is missing when its query answered no figure, though flows have some", async () => {
    const none: WidgetAnswer = { ...balance, series: [{ name: "cash", values_minor: [null, null, null] }] };
    stubQueries([[CASH, none], [NET_BY_BUCKET, flows]]);
    const { container } = render(<WaterfallWidget config={config} />);
    await waitFor(() => expect(screen.getByText(/balance this widget asks about has no figures/i)).toBeInTheDocument());
    expect(bars(container)).toHaveLength(0);
  });

  it("shows an answer's note, which for a projection says it is an estimate", async () => {
    const note = "a projection: what the forward view expects, not a promise";
    const projected: WidgetAnswer = { ...balance, source: "forward-view", note };
    stubQueries([[CASH, projected], [NET_BY_BUCKET, flows]]);
    const { container } = render(<WaterfallWidget config={config} />);
    await waitFor(() => expect(bars(container)).toHaveLength(5));
    expect(container).toHaveTextContent(note);
  });
});
