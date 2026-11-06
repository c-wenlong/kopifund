import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { cashflowAnswer, stubQueries } from "./testkit";
import { TimeSeriesChart, TimeSeriesWidget, type TimeSeriesConfig } from "./TimeSeries";
import { joinFrames } from "./shared";
import type { WidgetAnswer, WidgetQuery } from "../../../types.gen";
import { navigate, stubMatchMedia, useSizedCharts } from "../../../test-helpers";

vi.mock("../../../router", async (actual) => (await import("../../../test-helpers")).withNavigate(await actual()));

useSizedCharts();

const INCOME: WidgetQuery = { source: "monthly-cashflow", metric: "income", dimension: "month" };
const SPEND: WidgetQuery = { source: "monthly-cashflow", metric: "spend", dimension: "month" };
const NET: WidgetQuery = { source: "monthly-cashflow", metric: "net", dimension: "month" };

const MONTHS = [
  { m: "2026-08", income: 900000, spend: 400000, net: 500000 },
  { m: "2026-09", income: 910000, spend: 430000, net: 480000 },
  { m: "2026-10", income: 920000, spend: 380000, net: 540000 },
];

const config: TimeSeriesConfig = {
  id: "ts-test", title: "Cash flow",
  series: [
    { query: INCOME, label: "Income" },
    { query: SPEND, label: "Spend" },
    { query: NET, label: "Net" },
  ],
};

const stubs = (): [WidgetQuery, WidgetAnswer][] => [
  [INCOME, cashflowAnswer(MONTHS, "income")],
  [SPEND, cashflowAnswer(MONTHS, "spend")],
  [NET, cashflowAnswer(MONTHS, "net")],
];

const lines = (c: HTMLElement) => c.querySelectorAll(".recharts-line-curve");

describe("TimeSeriesWidget", () => {
  it("draws one line per series, in invented cents, named in the legend", async () => {
    stubQueries(stubs());
    const { container } = render(<TimeSeriesWidget config={config} />);
    await waitFor(() => expect(lines(container)).toHaveLength(3));
    for (const name of ["Income", "Spend", "Net"]) {
      expect(screen.getByRole("button", { name })).toBeInTheDocument();
    }
    const ticks = [...container.querySelectorAll(".recharts-yAxis .recharts-cartesian-axis-tick-value")].map((t) => t.textContent);
    expect(ticks.some((t) => /^S\$\d/.test(t ?? ""))).toBe(true);
    expect(container.querySelector("[role=img]")?.getAttribute("aria-label"))
      .toBe("Income, Spend, Net, Aug 2026 to Oct 2026");
  });

  it("says when a query is refused instead of drawing nothing", async () => {
    stubQueries([[INCOME, { error: "metric is one of income, spend" }]]);
    render(<TimeSeriesWidget config={{ ...config, series: [{ query: INCOME }] }} />);
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("metric is one of"));
    expect(screen.queryByRole("img")).toBeNull();
  });

  it("says there is nothing to show when every answer is empty", async () => {
    const empty: WidgetAnswer = { ...cashflowAnswer([], "net"), labels: [], label_links: [], series: [] };
    stubQueries([[NET, empty]]);
    render(<TimeSeriesWidget config={{ ...config, series: [{ query: NET }] }} />);
    await waitFor(() => expect(screen.getByText("Nothing to show yet")).toBeInTheDocument());
  });
});

describe("TimeSeriesChart", () => {
  const frame = () => joinFrames(
    config.series,
    [cashflowAnswer(MONTHS, "income"), cashflowAnswer(MONTHS, "spend"), cashflowAnswer(MONTHS, "net")],
  );

  it("hides a series from its legend entry, keeping the others", () => {
    const { container } = render(<TimeSeriesChart frame={frame()} />);
    fireEvent.click(screen.getByRole("button", { name: "Spend" }));
    expect(lines(container)).toHaveLength(2);
  });

  it("opens the rows behind a clicked dot, and nothing off a dot", async () => {
    const { container } = render(<TimeSeriesChart frame={frame()} />);
    const wrapper = container.querySelector(".recharts-wrapper")!;
    fireEvent.mouseMove(wrapper, { clientX: 700, clientY: 100 });
    await waitFor(() => expect(container.querySelectorAll(".recharts-active-dot circle")).toHaveLength(3));
    fireEvent.click(wrapper, { clientX: 700, clientY: 100 });
    expect(navigate).not.toHaveBeenCalled();
    fireEvent.click(container.querySelectorAll(".recharts-active-dot circle")[0], { clientX: 700, clientY: 100 });
    expect(navigate).toHaveBeenLastCalledWith("/transactions?from=2026-10&to=2026-10&income=1");
  });

  it("opens nothing when the answers carried no links", async () => {
    const unlinked: WidgetAnswer = { ...cashflowAnswer(MONTHS, "net"), label_links: undefined };
    const { container } = render(<TimeSeriesChart frame={joinFrames([{ query: NET }], [unlinked])} />);
    const wrapper = container.querySelector(".recharts-wrapper")!;
    fireEvent.mouseMove(wrapper, { clientX: 700, clientY: 100 });
    await waitFor(() => expect(container.querySelectorAll(".recharts-active-dot circle")).toHaveLength(1));
    fireEvent.click(container.querySelector(".recharts-active-dot circle")!, { clientX: 700, clientY: 100 });
    expect(navigate).not.toHaveBeenCalled();
    expect(container).not.toHaveTextContent("to see its transactions");
  });

  it("shows an answer's note, which for a projection says it is an estimate", () => {
    const note = "a projection: what the forward view expects, not a promise";
    const projected: WidgetAnswer = { ...cashflowAnswer(MONTHS, "net"), source: "forward-view", note };
    const { container } = render(<TimeSeriesChart frame={joinFrames([{ query: NET }], [projected])} />);
    expect(container).toHaveTextContent(note);
  });

  it("draws at phone height on a phone-width screen", () => {
    stubMatchMedia(false);
    const { container } = render(<TimeSeriesChart frame={frame()} />);
    const box = container.querySelector(".recharts-responsive-container") as HTMLElement;
    expect(box.style.height).toBe("220px");
  });
});
