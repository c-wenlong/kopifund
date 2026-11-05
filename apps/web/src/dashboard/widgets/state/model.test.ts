import { describe, expect, it } from "vitest";
import { REST_COLOUR } from "../../../components/chartKit";
import type { WidgetAnswer } from "../../../types.gen";
import { fmtValue, shareText, signedValue, sliceColour, slicesFor, slicesLeftOut, totalOf, txnLink } from "./model";

// invented figures only
const BY_BUCKET: WidgetAnswer = {
  source: "spending", metric: "spend", dimension: "bucket", unit: "sgd_minor",
  labels: ["Food", "Transport", "Everything else"],
  label_links: [
    { from: "2026-10", to: "2026-10", spend: "1", bucket: "Food", home: "1" },
    { from: "2026-10", to: "2026-10", spend: "1", bucket: "Transport", home: "1" },
    { from: "2026-10", to: "2026-10", spend: "1", home: "1" },
  ],
  series: [{ name: "spend", values_minor: [175_000, 60_000, 15_000] }],
  range: { from: "2026-10", to: "2026-10" },
};

const SPLIT_BY_MONTH: WidgetAnswer = {
  source: "spending", metric: "spend", dimension: "month", unit: "sgd_minor",
  labels: ["2026-08", "2026-09", "2026-10"],
  label_links: [
    { from: "2026-08", to: "2026-08", spend: "1", home: "1" },
    { from: "2026-09", to: "2026-09", spend: "1", home: "1" },
    { from: "2026-10", to: "2026-10", spend: "1", home: "1" },
  ],
  series: [
    { name: "Food", values_minor: [40_000, 50_000, 60_000], link: { spend: "1", bucket: "Food", home: "1" } },
    { name: "Transport", values_minor: [10_000, null, 20_000], link: { spend: "1", bucket: "Transport", home: "1" } },
  ],
  range: { from: "2026-08", to: "2026-10" },
};

describe("slicesFor", () => {
  it("reads one series over a split as one slice a label, keeping each label's link", () => {
    const slices = slicesFor([BY_BUCKET]);
    expect(slices.map((s) => s.name)).toEqual(["Food", "Transport", "Everything else"]);
    expect(slices.map((s) => s.value)).toEqual([175_000, 60_000, 15_000]);
    expect(slices[0].link).toEqual(BY_BUCKET.label_links?.[0]);
  });

  it("leaves out a slice at or below zero, so the total and the shares are of what is drawn", () => {
    const refunded: WidgetAnswer = { ...BY_BUCKET, series: [{ name: "spend", values_minor: [175_000, -5_000, 0] }] };
    expect(slicesFor([refunded]).map((s) => s.name)).toEqual(["Food"]);
    expect(totalOf(slicesFor([refunded]))).toBe(175_000);
    expect(slicesLeftOut([refunded]).map((s) => [s.name, s.value])).toEqual([["Transport", -5_000], ["Everything else", 0]]);
  });

  it("reads several series as one slice each, at the latest point", () => {
    const slices = slicesFor([SPLIT_BY_MONTH]);
    expect(slices.map((s) => s.name)).toEqual(["Food", "Transport"]);
    expect(slices.map((s) => s.value)).toEqual([60_000, 20_000]);
    // the series' own filters plus the shown point's month, so the click lists what the figure counts
    expect(slices[0].link).toEqual({ from: "2026-10", to: "2026-10", spend: "1", home: "1", bucket: "Food" });
    expect(slices[1].link).toEqual({ from: "2026-10", to: "2026-10", spend: "1", home: "1", bucket: "Transport" });
  });

  it("sums the range instead when aggregate is sum, and the link spans it", () => {
    const slices = slicesFor([SPLIT_BY_MONTH], "sum");
    expect(slices.map((s) => s.value)).toEqual([150_000, 30_000]);
    expect(slices[0].link).toEqual({ from: "2026-08", to: "2026-10", spend: "1", home: "1", bucket: "Food" });
    // Transport's figures are Aug and Oct: the summed link covers its own first to last point
    expect(slices[1].link).toEqual({ from: "2026-08", to: "2026-10", spend: "1", home: "1", bucket: "Transport" });
  });

  it("keys two same-named series from two bound queries apart, so each hides alone", () => {
    const other: WidgetAnswer = {
      ...SPLIT_BY_MONTH,
      series: [{ name: "spend", values_minor: [1_000, 2_000, 3_000], link: { spend: "1", bucket: "Food", home: "1" } }],
    };
    const slices = slicesFor([SPLIT_BY_MONTH, other]);
    expect(slices.map((s) => s.key)).toEqual(["0:Food", "0:Transport", "1:spend"]);
    expect(new Set(slices.map((s) => s.key)).size).toBe(3);
  });

  it("falls back to the point's own label link when a series has none", () => {
    const answer: WidgetAnswer = {
      ...SPLIT_BY_MONTH,
      series: [
        { name: "Food", values_minor: [40_000, 50_000, 60_000], link: { spend: "1", bucket: "Food", home: "1" } },
        { name: "Savings", values_minor: [null, null, 3_000] },
      ],
    };
    const slices = slicesFor([answer]);
    expect(slices[1].link).toEqual({ from: "2026-10", to: "2026-10", spend: "1", home: "1" });
  });

  it("reads several bound answers as one slice a series", () => {
    const need: WidgetAnswer = {
      source: "needs-wants-savings", metric: "need", dimension: "month", unit: "sgd_minor",
      labels: ["2026-09", "2026-10"], series: [{ name: "need", values_minor: [100_000, 120_000] }], range: null,
    };
    const want: WidgetAnswer = { ...need, metric: "want", series: [{ name: "want", values_minor: [80_000, 90_000] }] };
    const savings: WidgetAnswer = { ...need, metric: "savings", series: [{ name: "savings", values_minor: [50_000, null] }] };
    const slices = slicesFor([need, want, savings]);
    expect(slices.map((s) => s.name)).toEqual(["Need", "Want", "Savings"]);
    expect(slices.map((s) => s.value)).toEqual([120_000, 90_000, 50_000]);
  });

  it("skips a label or a series with no figure at all", () => {
    const answer: WidgetAnswer = {
      ...BY_BUCKET,
      labels: ["Food", "Empty"],
      label_links: [null, null],
      series: [{ name: "spend", values_minor: [175_000, null] }],
    };
    expect(slicesFor([answer]).map((s) => s.name)).toEqual(["Food"]);
    const emptySeries: WidgetAnswer = { ...SPLIT_BY_MONTH, series: [{ name: "Food", values_minor: [null, null] }] };
    expect(slicesFor([emptySeries])).toEqual([]);
  });

  it("is empty for no answers", () => {
    expect(slicesFor([])).toEqual([]);
  });
});

describe("figures and links", () => {
  it("writes cents as money and other units in their own way", () => {
    expect(fmtValue(175_000, "sgd_minor")).toBe("S$1,750");
    expect(fmtValue(0.3, "fraction")).toBe("30%");
    expect(fmtValue(18.7, "percent")).toBe("18.7%");
    expect(fmtValue(6.6, "months")).toBe("6.6 mo");
  });

  it("writes a change with its sign", () => {
    expect(signedValue(12_345, "sgd_minor")).toBe("+S$123");
    expect(signedValue(-12_345, "sgd_minor")).toBe("−S$123");
    expect(signedValue(0.05, "fraction")).toBe("+5%");
    expect(signedValue(-2.3, "percent")).toBe("−2.3%");
    expect(signedValue(0.4, "months")).toBe("+0.4 mo");
  });

  it("writes a share of the whole, or 0% of nothing", () => {
    expect(shareText(60_000, 240_000)).toBe("25%");
    expect(shareText(1, 3)).toBe("33.3%");
    expect(shareText(1, 0)).toBe("0%");
  });

  it("builds the Transactions address a slice lists", () => {
    expect(txnLink({ bucket: "Food", spend: "1" })).toBe("/transactions?bucket=Food&spend=1");
    expect(txnLink(null)).toBeNull();
    expect(txnLink(undefined)).toBeNull();
  });

  it("adds up the whole", () => {
    expect(totalOf(slicesFor([BY_BUCKET]))).toBe(250_000);
  });
});

describe("sliceColour", () => {
  it("gives the folded labels the kit's neutral", () => {
    expect(sliceColour("Everything else", 0, null)).toBe(REST_COLOUR);
    expect(sliceColour("(no bucket)", 3, null)).toBe(REST_COLOUR);
  });

  it("gives a bucket its own shown colour", () => {
    const buckets = { Food: { icon: "utensils", colour: null, shown: "c4" } } as const;
    expect(sliceColour("Food", 2, buckets)).toBe("var(--c4)");
  });

  it("gives any other name its series colour", () => {
    expect(sliceColour("Groceries", 2, null)).toBe("var(--c3)");
  });
});
