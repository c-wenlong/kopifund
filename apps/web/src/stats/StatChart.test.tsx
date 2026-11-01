import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { StatChart, Tip } from "./StatChart";
import { plan, type StatResult } from "./hint";

const navigate = vi.fn();
vi.mock("../router", () => ({ navigate: (to: string) => navigate(to) }));

// Give ResponsiveContainer a real size: jsdom has no layout, and a chart of
// width 0 draws nothing, which would let the collapsed-bar bug pass unseen.
class SizedObserver {
  constructor(private cb: ResizeObserverCallback) {}
  observe(el: Element) {
    this.cb([{ target: el, contentRect: { width: 800, height: 400 } } as unknown as ResizeObserverEntry], this as unknown as ResizeObserver);
  }
  unobserve() {}
  disconnect() {}
}

function chartFor(result: StatResult, view?: "stacked" | "lines") {
  const p = plan(result);
  if (p.kind !== "chart") throw new Error("expected a chart");
  return render(<StatChart p={p} view={view} />);
}

describe("StatChart", () => {
  const real = globalThis.ResizeObserver;
  beforeEach(() => {
    globalThis.ResizeObserver = SizedObserver as unknown as typeof ResizeObserver;
    navigate.mockReset();
  });
  afterEach(() => { globalThis.ResizeObserver = real; });

  const categories: StatResult = {
    id: "category-breakdown", title: "Spend by category",
    chart: { type: "bar", x: "category", series: ["per_month_minor"], unit: "sgd_minor", horizontal: true },
    data: { categories: [
      { category: "Eating out", per_month_minor: 54000 }, { category: "Travel", per_month_minor: 50000 },
      { category: "Shopping", per_month_minor: 39000 }, { category: "(uncategorised)", per_month_minor: 1100 },
    ] },
  };

  it("draws one horizontal bar per category, each on its own row, with a category axis", () => {
    const { container } = chartFor(categories);
    const ticks = [...container.querySelectorAll(".recharts-yAxis .recharts-cartesian-axis-tick-value")].map((t) => t.textContent);
    expect(ticks).toEqual(["Eating out", "Travel", "Shopping", "(uncategorised)"]);
    const bars = [...container.querySelectorAll(".recharts-bar-rectangle path")];
    expect(bars).toHaveLength(4);
    const ys = new Set(bars.map((b) => b.getAttribute("y")));
    expect(ys.size).toBe(4);   // the old bug drew every bar on one spot
  });

  // G13: a bar opens the rows its stat says it adds up, a series at a time; a series with no link opens nothing
  const cashflow: StatResult = {
    id: "monthly-cashflow", title: "Cash flow",
    chart: {
      type: "bar", x: "month", series: ["income_minor", "spend_minor", "net_minor"], unit: "sgd_minor",
      links: {
        income_minor: { month: "{x}", income: "1" }, spend_minor: { month: "{x}", spend: "1" },
        net_minor: { month: "{x}", income: "1", spend: "1" },
      },
    },
    data: { months: [
      { month: "2026-08", income_minor: 30000, spend_minor: 10000, net_minor: 20000 },
      { month: "2026-09", income_minor: 31000, spend_minor: 12000, net_minor: 19000 },
    ] },
  };

  it("opens the rows behind the bar clicked: income, spend and net each list their own", () => {
    const { container } = chartFor(cashflow);
    const bars = container.querySelectorAll(".recharts-bar-rectangle path");
    expect(bars).toHaveLength(6);   // income, then spend, then net, a month each
    fireEvent.click(bars[1]);
    expect(navigate).toHaveBeenLastCalledWith("/transactions?month=2026-09&income=1");
    fireEvent.click(bars[2]);
    expect(navigate).toHaveBeenLastCalledWith("/transactions?month=2026-08&spend=1");
    fireEvent.click(bars[5]);
    expect(navigate).toHaveBeenLastCalledWith("/transactions?month=2026-09&income=1&spend=1");
  });

  it("opens nothing from a bar whose figure is an average, and its cursor is not a pointer", () => {
    const { container } = chartFor({
      id: "travel", title: "Trips",
      chart: { type: "bar", x: "name", series: ["spend_minor", "spend_per_day_minor"], unit: "sgd_minor",
        links: { spend_minor: { trip: "{x}", spend: "1" } } },
      data: { trips: [
        { name: "Trip A", spend_minor: 50000, spend_per_day_minor: 10000 }, { name: "Trip B", spend_minor: 30000, spend_per_day_minor: 7500 },
      ] },
    });
    const bars = container.querySelectorAll(".recharts-bar-rectangle path");
    fireEvent.click(bars[3]);
    expect(navigate).not.toHaveBeenCalled();
    fireEvent.click(bars[1]);
    expect(navigate).toHaveBeenLastCalledWith("/transactions?trip=Trip+B&spend=1");
    const pointer = [...container.querySelectorAll(".recharts-bar")].map((g) => g.querySelector("path")!.getAttribute("cursor"));
    expect(pointer).toEqual(["pointer", null]);
  });

  it("does not link a chart whose stat wrote no links, as the category averages are not", () => {
    const { container } = chartFor(categories);
    fireEvent.click(container.querySelectorAll(".recharts-bar-rectangle path")[0]);
    expect(navigate).not.toHaveBeenCalled();
    expect(container.querySelector("[cursor=pointer]")).toBeNull();
  });

  it("labels month bars on the x axis", () => {
    const { container } = chartFor({
      id: "monthly-cashflow", title: "Cash flow",
      chart: { type: "bar", x: "month", series: ["spend_minor"], unit: "sgd_minor" },
      data: { months: [{ month: "2026-08", spend_minor: 10000 }, { month: "2026-09", spend_minor: 20000 }] },
    });
    const ticks = [...container.querySelectorAll(".recharts-xAxis .recharts-cartesian-axis-tick-value")].map((t) => t.textContent);
    expect(ticks).toEqual(["Aug 26", "Sep 26"]);
    expect(container.querySelectorAll(".recharts-yAxis .recharts-cartesian-axis-tick-value").length).toBeGreaterThan(1);
  });

  const split: StatResult = {
    id: "income-stability", title: "Income",
    chart: { type: "stacked", x: "month", series: ["salary_minor", "other_minor"], unit: "sgd_minor" },
    data: { months: [{ month: "2026-08", salary_minor: 500000, other_minor: 80000 }, { month: "2026-09", salary_minor: 520000, other_minor: 10000 }] },
  };

  it("draws a stacked chart as bars, or as one line a series, from the card's switch", () => {
    const bars = chartFor(split);
    expect(bars.container.querySelectorAll(".recharts-bar-rectangle path")).toHaveLength(4);
    expect(bars.container.querySelectorAll(".recharts-line-curve")).toHaveLength(0);
    bars.unmount();
    const lines = chartFor(split, "lines");
    expect(lines.container.querySelectorAll(".recharts-line-curve")).toHaveLength(2);
    expect(lines.container.querySelectorAll(".recharts-bar-rectangle")).toHaveLength(0);
  });

  // a stacked stat drawn as lines opens a line's own rows from its dot
  const savings: StatResult = {
    id: "needs-wants-savings", title: "Needs, wants and savings",
    chart: {
      type: "stacked", x: "month", series: ["need_minor", "want_minor", "savings_minor"], unit: "sgd_minor",
      links: { need_minor: { month: "{x}", spend: "1", need: "Need" }, want_minor: { month: "{x}", spend: "1", need: "Want" } },
    },
    data: { months: [
      { month: "2026-08", need_minor: 10000, want_minor: 5000, savings_minor: 2000 },
      { month: "2026-09", need_minor: 12000, want_minor: 6000, savings_minor: 2500 },
    ] },
  };

  it("opens the series of the dot clicked on Lines, in the month under the pointer, and nothing from a click off a dot", async () => {
    const { container } = chartFor(savings, "lines");
    const wrapper = container.querySelector(".recharts-wrapper")!;
    fireEvent.mouseMove(wrapper, { clientX: 700, clientY: 100 });
    await waitFor(() => expect(container.querySelectorAll(".recharts-active-dot circle").length).toBe(3));
    fireEvent.click(wrapper, { clientX: 700, clientY: 100 });
    expect(navigate).not.toHaveBeenCalled();
    const dots = [...container.querySelectorAll(".recharts-active-dot circle")];
    fireEvent.click(dots[0], { clientX: 700, clientY: 100 });
    expect(navigate).toHaveBeenLastCalledWith("/transactions?month=2026-09&spend=1&need=Need");
    fireEvent.click(dots[1], { clientX: 700, clientY: 100 });
    expect(navigate).toHaveBeenLastCalledWith("/transactions?month=2026-09&spend=1&need=Want");
    // savings has no link in this result, so its dot opens nothing
    navigate.mockClear();
    fireEvent.click(dots[2], { clientX: 700, clientY: 100 });
    expect(navigate).not.toHaveBeenCalled();
  });

  // a line chart (eating out, a week at a time) opens the rows of the dot clicked
  const weekly: StatResult = {
    id: "eating-out", title: "Eating out",
    chart: {
      type: "line", x: "week", series: ["spend_minor"], source: "weeks", unit: "sgd_minor",
      links: { spend_minor: { from: "{x}", to: "{end}", spend: "1", categories: '["Eating out"]' } },
    },
    data: { weeks: [
      { week: "2026-09-07", end: "2026-09-13", spend_minor: 4000 }, { week: "2026-09-14", end: "2026-09-20", spend_minor: 5500 },
    ] },
  };

  it("opens a week's rows from its dot on a line chart, from the Monday to the Sunday", async () => {
    const { container } = chartFor(weekly);
    const wrapper = container.querySelector(".recharts-wrapper")!;
    fireEvent.mouseMove(wrapper, { clientX: 700, clientY: 100 });
    await waitFor(() => expect(container.querySelectorAll(".recharts-active-dot circle").length).toBe(1));
    // a click that misses the dot opens nothing
    fireEvent.click(wrapper, { clientX: 700, clientY: 100 });
    expect(navigate).not.toHaveBeenCalled();
    fireEvent.click(container.querySelector(".recharts-active-dot circle")!, { clientX: 700, clientY: 100 });
    expect(navigate).toHaveBeenLastCalledWith(
      "/transactions?from=2026-09-14&to=2026-09-20&spend=1&categories=%5B%22Eating+out%22%5D");
  });

  it("keeps a line chart whose stat wrote no links as it was: no click, no invitation", async () => {
    const { container } = chartFor({ ...weekly, chart: { ...weekly.chart, links: undefined } });
    const wrapper = container.querySelector(".recharts-wrapper")!;
    fireEvent.mouseMove(wrapper, { clientX: 700, clientY: 100 });
    await waitFor(() => expect(container.querySelectorAll(".recharts-active-dot circle").length).toBe(1));
    fireEvent.click(container.querySelector(".recharts-active-dot circle")!, { clientX: 700, clientY: 100 });
    expect(navigate).not.toHaveBeenCalled();
    expect(container).not.toHaveTextContent("to see its transactions");
  });

  it("leaves a chart that is not stacked alone, whatever view it is given", () => {
    const { container } = chartFor(categories, "lines");
    expect(container.querySelectorAll(".recharts-bar-rectangle path")).toHaveLength(4);
  });

  // G18: the name column is 160px on a desktop and 104px on a phone, and a name too long for it is cut
  describe("the name column of a horizontal bar chart", () => {
    const long: StatResult = {
      id: "category-breakdown", title: "Spend by category",
      chart: { type: "bar", x: "category", series: ["per_month_minor"], unit: "sgd_minor", horizontal: true },
      data: { categories: [
        { category: "Government and statutory payments", per_month_minor: 54000 }, { category: "Travel", per_month_minor: 50000 },
      ] },
    };
    const names = (c: HTMLElement) => [...c.querySelectorAll(".recharts-yAxis .recharts-cartesian-axis-tick-value")];
    const phone = (on: boolean) => vi.stubGlobal("matchMedia", (q: string) => ({
      matches: !on, media: q, addEventListener: () => {}, removeEventListener: () => {},
    }));
    afterEach(() => vi.unstubAllGlobals());

    it("is 160px wide on a desktop and cuts a name that will not fit, with an ellipsis", () => {
      phone(false);
      const { container } = chartFor(long);
      expect(names(container).map((t) => t.textContent)).toEqual(["Government and statutor…", "Travel"]);
      // the names end 8px short of the column's right edge (the tick's margin and length)
      expect(names(container)[1].getAttribute("x")).toBe("152");
    });

    it("is 104px wide on a phone, with the name cut shorter to fit", () => {
      phone(true);
      const { container } = chartFor(long);
      expect(names(container).map((t) => t.textContent)).toEqual(["Government and…", "Travel"]);
      expect(names(container)[1].getAttribute("x")).toBe("96");
    });
  });

  it("hides a series from its legend entry, and writes money axis ticks compactly", () => {
    const { container } = chartFor({ ...split, data: { months: [
      { month: "2026-08", salary_minor: 1200000, other_minor: 80000 }, { month: "2026-09", salary_minor: 1500000, other_minor: 10000 },
    ] } });
    fireEvent.click(screen.getByRole("button", { name: "Other" }));
    expect(container.querySelectorAll(".recharts-bar-rectangle path")).toHaveLength(2);
    const ticks = [...container.querySelectorAll(".recharts-yAxis .recharts-cartesian-axis-tick-value")].map((t) => t.textContent);
    expect(ticks.some((t) => /^S\$\d+(\.\d)?k$/.test(t ?? ""))).toBe(true);
  });
});

// G18: every stat chart takes its colours from the chart kit's twelve
describe("StatChart's series colours", () => {
  const real = globalThis.ResizeObserver;
  beforeEach(() => { globalThis.ResizeObserver = SizedObserver as unknown as typeof ResizeObserver; });
  afterEach(() => { globalThis.ResizeObserver = real; });

  it("gives nine series nine different colours, in the legend and on the bars", () => {
    const series = Array.from({ length: 9 }, (_, i) => `part_${i}_minor`);
    const row = (month: string) => ({ month, ...Object.fromEntries(series.map((s) => [s, 10000])) });
    const { container } = chartFor({
      id: "mix", title: "Mix", chart: { type: "stacked", x: "month", series, unit: "sgd_minor" },
      data: { months: [row("2026-08"), row("2026-09")] },
    });
    const key = [...container.querySelectorAll("button[aria-pressed] [style]")].map((s) => (s as HTMLElement).style.background);
    expect(new Set(key).size).toBe(9);
    expect(key[0]).toBe("var(--c1)");
    expect(new Set([...container.querySelectorAll(".recharts-bar-rectangle path")].map((b) => b.getAttribute("fill"))).size).toBe(9);
  });

  it("writes a scatter's x ticks as the plain numbers they are, not as money", () => {
    const { container } = chartFor({
      id: "one-offs", title: "One-off charges",
      chart: { type: "scatter", x: "multiple_of_median", series: ["amount_minor"], unit: "sgd_minor", label: "label" },
      data: { rows: [{ label: "A", multiple_of_median: 6, amount_minor: 40000 }, { label: "B", multiple_of_median: 12, amount_minor: 90000 }] },
    });
    const xs = [...container.querySelectorAll(".recharts-xAxis .recharts-cartesian-axis-tick-value")].map((t) => t.textContent ?? "");
    expect(xs.length).toBeGreaterThan(1);
    expect(xs.filter((t) => t.includes("S$"))).toEqual([]);
    const ys = [...container.querySelectorAll(".recharts-yAxis .recharts-cartesian-axis-tick-value")].map((t) => t.textContent ?? "");
    expect(ys.some((t) => t.startsWith("S$"))).toBe(true);
  });
});

describe("Tip is the chart kit's hover card", () => {
  const p = plan({
    id: "travel", title: "Trips",
    chart: { type: "bar", x: "name", series: ["spend_minor"], unit: "sgd_minor" },
    data: { trips: [{ name: "Trip A", spend_minor: 50000 }] },
  });
  if (p.kind !== "chart") throw new Error("expected a chart");

  it("has the one width every chart's has, capped to a phone screen", () => {
    const { container } = render(<Tip active x="Trip A" items={[{ dataKey: "spend_minor", value: 50000, color: "red", payload: { name: "Trip A" } }]}
      p={p} total={false} clickable={false} />);
    const cls = (container.firstElementChild as HTMLElement).className;
    expect(cls).toContain("min-w-52");
    expect(cls).toContain("max-w-[min(22rem,calc(100vw-2rem))]");
  });
});

describe("Tip on a stack", () => {
  const stacked = plan({
    id: "income-stability", title: "Income",
    chart: { type: "stacked", x: "month", series: ["salary_minor", "other_minor"], unit: "sgd_minor" },
    data: { months: [{ month: "2026-08", salary_minor: 500000, other_minor: 80000 }] },
  });
  if (stacked.kind !== "chart") throw new Error("expected a chart");
  const row = { month: "2026-08" };
  const item = (dataKey: string, value: number) => ({ dataKey, value, color: "red", payload: row });
  const tip = (items: ReturnType<typeof item>[]) =>
    render(<Tip active x="2026-08" items={items} p={stacked} total clickable={false} />);

  it("leaves out a part at zero and keeps the total", () => {
    const { container } = tip([item("salary_minor", 500000), item("other_minor", 0), item("bonus_minor", 30000)]);
    expect(container).toHaveTextContent("Salary");
    expect(container).not.toHaveTextContent("Other");
    expect(container).toHaveTextContent("5,300");
  });

  it("shows the total, at zero, for a month where every part is zero", () => {
    const { container } = tip([item("salary_minor", 0), item("other_minor", 0)]);
    expect(container).not.toHaveTextContent("Salary");
    expect(container).toHaveTextContent("S$0");
  });

  it("does not repeat a total that is the one row shown", () => {
    const { container } = tip([item("salary_minor", 500000), item("other_minor", 0)]);
    expect(container.textContent?.match(/5,000/g)).toHaveLength(1);
  });
});

describe("Tip's click line", () => {
  const p = plan({
    id: "travel", title: "Trips",
    chart: { type: "bar", x: "name", series: ["spend_minor", "spend_per_day_minor"], unit: "sgd_minor" },
    data: { trips: [{ name: "Trip A", spend_minor: 50000, spend_per_day_minor: 10000 }] },
  });
  if (p.kind !== "chart") throw new Error("expected a chart");
  const items = [{ dataKey: "spend_minor", value: 50000, color: "red", payload: { name: "Trip A" } }];

  it("says nothing when no bar links", () => {
    const { container } = render(<Tip active x="Trip A" items={items} p={p} total={false} clickable={false} />);
    expect(container).not.toHaveTextContent("Click");
  });

  it("invites a click on a bar when every bar drawn links, a dot on a line chart", () => {
    const { container, rerender } = render(<Tip active x="Trip A" items={items} p={p} total={false} clickable />);
    expect(container).toHaveTextContent("Click a bar to see its transactions");
    rerender(<Tip active x="Trip A" items={items} p={p} total={false} clickable dots />);
    expect(container).toHaveTextContent("Click a dot to see its transactions");
  });

  it("names the series that link when others do not, so no average promises a click", () => {
    const { container } = render(<Tip active x="Trip A" items={items} p={p} total={false} clickable only={["spend_minor"]} />);
    expect(container).toHaveTextContent("Click the Spend bar to see its transactions");
  });
});
