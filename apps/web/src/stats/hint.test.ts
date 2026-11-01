import { columnUnit, fmt, normaliseUnit, percentUnitFor, plan, resolveRows, type StatResult } from "./hint";

const base: StatResult = { id: "x", title: "X" };

describe("chart hints", () => {
  it("finds rows through chart.source", () => {
    const r = { ...base, chart: { type: "bar", source: "active", unit: "sgd_minor" }, data: { active: [{ payee: "A", monthly_cost_minor: 100 }] } };
    expect(resolveRows(r)).toEqual([{ payee: "A", monthly_cost_minor: 100 }]);
  });

  it("plans a bar chart from the declared series", () => {
    const p = plan({ ...base, chart: { type: "bar", x: "month", series: ["income_minor", "spend_minor", "missing_minor"], unit: "sgd_minor" },
      data: { months: [{ month: "2026-08", income_minor: 300000, spend_minor: 200000 }] } });
    expect(p.kind).toBe("chart");
    if (p.kind === "chart") expect(p.series).toEqual(["income_minor", "spend_minor"]);
  });

  it("goes horizontal for a long single-series bar", () => {
    const rows = Array.from({ length: 12 }, (_, i) => ({ payee: `P${i}`, spend_minor: i * 100 }));
    const p = plan({ ...base, chart: { type: "bar", x: "payee", series: ["spend_minor"], unit: "sgd_minor" }, data: { rows } });
    expect(p.kind === "chart" && p.horizontal).toBe(true);
  });

  it("puts a second unit on a second axis", () => {
    const p = plan({ ...base, chart: { type: "line", x: "month", series: ["cash_minor", "runway_months"], unit: "sgd_minor", units: { runway_months: "months" } },
      data: { months: [{ month: "2026-08", cash_minor: 1000, runway_months: 6 }] } });
    expect(p.kind === "chart" && p.secondary).toBe("months");
  });

  it("falls back to a table, saying why, for a type it cannot draw", () => {
    const p = plan({ ...base, chart: { type: "sankey" }, data: { rows: [{ a: 1 }] } });
    expect(p.kind).toBe("table");
    expect(p.note).toMatch(/not one this page draws/);
  });

  it("formats each unit", () => {
    expect(fmt(-123450, "sgd_minor")).toBe("−S$1,235");
    expect(fmt(123450, "sgd_minor", true)).toBe("S$1,234.50");
    expect(fmt(0.53, "fraction")).toBe("53%");
    expect(fmt(6.6, "months")).toBe("6.6 mo");
  });

  it("shows a fraction as a percentage, whatever its size", () => {
    expect(fmt(0.3, "fraction")).toBe("30%");
    expect(fmt(0.005, "fraction")).toBe("0.5%");
    expect(fmt(1, "fraction")).toBe("100%");
    expect(fmt(3.2, "fraction")).toBe("320%");
    expect(fmt(-0.2297, "fraction")).toBe("-23%");
  });

  it("leaves an already-scaled percent alone, even below 1.5", () => {
    expect(fmt(18.7, "percent")).toBe("18.7%");
    expect(fmt(47.86, "percent")).toBe("47.9%");
    expect(fmt(0.45, "percent")).toBe("0.5%");
    expect(fmt(1, "percent")).toBe("1%");
  });

  it("reads the scale from the column name", () => {
    expect(percentUnitFor("share")).toBe("fraction");
    expect(percentUnitFor("need_share")).toBe("fraction");
    expect(percentUnitFor("share_of_month")).toBe("fraction");
    expect(percentUnitFor("change_pct")).toBe("fraction");
    expect(percentUnitFor("gain_pct")).toBe("percent");
    expect(percentUnitFor("share_pct")).toBe("percent");
    expect(percentUnitFor("cumulative_share_pct")).toBe("percent");
    expect(percentUnitFor("payees_for_80pct")).toBeNull();
    expect(percentUnitFor("spend")).toBeNull();
  });

  it("reads a prose unit's scale", () => {
    expect(fmt(0.5, "share of spend")).toBe("50%");
    expect(fmt(5, "percent of ordinary wages")).toBe("5%");
  });

  it("shows an empty explicit source as empty, not as another list", () => {
    const r = { ...base, chart: { type: "bar", x: "payee", series: ["spend_minor"], source: "active", unit: "sgd_minor" },
      data: { active: [], rows: [{ payee: "A", spend_minor: 100 }] } };
    expect(resolveRows(r)).toEqual([]);
    const p = plan(r);
    expect(p.kind).toBe("table");
    expect(p.kind === "table" && p.rows).toEqual([]);
    expect(p.note).toMatch(/no rows/i);
  });

  // G20: money is sgd_minor and nothing else: no unit, the old "sgd" and a currency's text are plain numbers
  it("writes money only for a figure in cents", () => {
    expect(fmt(1234.5)).toBe("1,234.5");
    expect(fmt(1234.5, "sgd", true)).toBe("1,234.5");
    expect(fmt(1234.5, "SGD per month")).toBe("1,234.5");
    expect(fmt(1234.5, "SGD per year")).toBe("1,234.5");
    expect(normaliseUnit("SGD per household per month")).toBe("money");
    expect(normaliseUnit("sgd")).toBe("money");
    expect(normaliseUnit("SGD per year")).toBe("money/yr");
    expect(normaliseUnit("sgd_minor")).toBe("sgd_minor");
  });

  it("guesses no money from a column's name", () => {
    for (const column of ["spend", "amount", "cash", "income", "value", "total", "uncategorised_out", "contributed_sgd"])
      expect(columnUnit(column, undefined, "sgd_minor")).toBe("number");
    expect(columnUnit("spend_minor")).toBe("sgd_minor");
    expect(columnUnit("spend")).toBe("number");
    expect(columnUnit("spend", undefined, "sgd")).toBe("number");
    expect(columnUnit("spend", { spend: "months" }, "sgd_minor")).toBe("months");
    expect(columnUnit("gain_pct", undefined, "sgd_minor")).toBe("percent");
    expect(columnUnit("need_share", undefined, "sgd_minor")).toBe("fraction");
    // a chart whose unit is not money still lends it to a bare number
    expect(columnUnit("runway", undefined, "months")).toBe("months");
    expect(columnUnit("trip_days", undefined, "months")).toBe("number");
  });

  it("keeps a row in a currency on a chart in cents and sets a row in months aside", () => {
    const p = plan({ ...base, chart: { type: "bar", x: "label", series: ["value_minor", "value"], unit: "sgd_minor" },
      data: { rows: [
        { label: "Food", value_minor: 60000, unit: "SGD per month" },
        { label: "Rent", value_minor: 90000, unit: "SGD per month" },
        { label: "Cash", value: 6, unit: "months of expenses" },
      ] } });
    expect(p.kind).toBe("chart");
    if (p.kind !== "chart") return;
    expect(p.rows.map((r) => r.label)).toEqual(["Food", "Rent"]);
    expect(p.note).toBe("1 row is in another unit, so it is in the table only.");
  });

  it("still falls back when the source names nothing usable", () => {
    const r = { ...base, chart: { type: "bar", source: "gone" }, data: { rows: [{ a: 1 }] } };
    expect(resolveRows(r)).toEqual([{ a: 1 }]);
  });
});
