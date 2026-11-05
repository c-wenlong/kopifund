import { joinFrames, pointHref, queryWithRange, tickValue } from "./shared";
import type { WidgetAnswer } from "../../../types.gen";

const answer = (over: Partial<WidgetAnswer>): WidgetAnswer => ({
  source: "monthly-cashflow", metric: "net", dimension: "month", unit: "sgd_minor",
  labels: ["2026-08"], series: [{ name: "net", values_minor: [100] }], range: null, ...over,
});

describe("queryWithRange", () => {
  it("gives a query without a range the config's, and leaves one that names its own", () => {
    const range = { from: "2026-07", to: "2026-09" };
    expect(queryWithRange({ source: "net-worth", metric: "total" }, range).range).toEqual(range);
    const own = { source: "net-worth" as const, metric: "total", range: { from: "2026-01" } };
    expect(queryWithRange(own, range).range).toEqual({ from: "2026-01" });
    expect(queryWithRange(own).range).toEqual({ from: "2026-01" });
  });
});

describe("joinFrames", () => {
  it("lays the answers' series over their labels' union, in order, null where a series does not reach", () => {
    const frame = joinFrames(
      [{ query: { source: "net-worth", metric: "cash" } }, { query: { source: "net-worth", metric: "cpf" } }],
      [
        answer({ labels: ["2026-09", "2026-08"], series: [{ name: "cash", values_minor: [300, 200] }] }),
        answer({ labels: ["2026-09", "2026-10"], series: [{ name: "cpf", values_minor: [40, 50] }] }),
      ],
    );
    expect(frame.labels).toEqual(["2026-08", "2026-09", "2026-10"]);
    expect(frame.series.map((s) => s.name)).toEqual(["cash", "cpf"]);
    expect(frame.series[0].values).toEqual([200, 300, null]);
    expect(frame.series[1].values).toEqual([null, 40, 50]);
    expect(frame.unit).toBe("sgd_minor");
    expect(frame.series[0].colour).toBe("var(--c1)");
    expect(frame.series[1].colour).toBe("var(--c2)");
  });

  it("names a series after its spec's label when the spec gives one", () => {
    const frame = joinFrames(
      [{ query: { source: "monthly-cashflow", metric: "net" }, label: "What's left" }],
      [answer({})],
    );
    expect(frame.series[0].name).toBe("What's left");
  });

  it("reads numbers under `values` with the answer's own unit, and marks a mixed frame", () => {
    const months = answer({ unit: "months", series: [{ name: "runway_months", values: [4.2] }] });
    const frame = joinFrames([{ query: { source: "savings-runway", metric: "runway_months" } }], [months]);
    expect(frame.series[0].values).toEqual([4.2]);
    expect(frame.unit).toBe("months");
    const mixed = joinFrames(
      [{ query: { source: "savings-runway", metric: "runway_months" } }, { query: { source: "savings-runway", metric: "cash" } }],
      [months, answer({})],
    );
    expect(mixed.unit).toBeNull();
  });

  it("carries an answer's projection note", () => {
    const frame = joinFrames(
      [{ query: { source: "forward-view", metric: "cash" } }],
      [answer({ source: "forward-view", note: "a projection: what the forward view expects, not a promise" })],
    );
    expect(frame.note).toContain("a projection");
  });
});

describe("a point's link", () => {
  it("is the label's own filters with the series' on top, as Transactions params", () => {
    const a = answer({
      labels: ["2026-08", "2026-09"],
      label_links: [{ from: "2026-08", to: "2026-08", income: "1", spend: "1", home: "1" }, null],
      series: [{ name: "Food", values_minor: [1, 2], link: { spend: "1", bucket: "Food" } }],
    });
    expect(pointHref(a, 0, a.series[0])).toBe("/transactions?from=2026-08&to=2026-08&income=1&spend=1&home=1&bucket=Food");
    expect(pointHref(a, 1, a.series[0])).toBe("/transactions?spend=1&bucket=Food");
    expect(pointHref(a, 1)).toBeNull();
  });

  it("gives a drawn series a range link over its whole answer for a waterfall step", () => {
    const a = answer({
      labels: ["2026-08", "2026-09"],
      label_links: [{ from: "2026-08", to: "2026-08", spend: "1", home: "1" }, { from: "2026-09", to: "2026-09", spend: "1", home: "1" }],
      series: [{ name: "Food", values_minor: [1, 2], link: { bucket: "Food" } }],
    });
    const frame = joinFrames([{ query: { source: "spending", metric: "spend", split: "bucket" } }], [a]);
    expect(frame.series[0].rangeLink).toEqual({ spend: "1", home: "1", bucket: "Food" });
  });
});

describe("tickValue", () => {
  it("writes money ticks compactly and other units plainly", () => {
    expect(tickValue(1200000, "sgd_minor")).toBe("S$12k");
    expect(tickValue(-200000, "sgd_minor")).toBe("−S$2k");
    expect(tickValue(4.2, "months")).toBe("4.2 mo");
    expect(tickValue(30, "percent")).toBe("30%");
    expect(tickValue(0.3, "fraction")).toBe("30%");
  });
});
