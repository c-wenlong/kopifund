import { fireEvent, render, screen, within } from "@testing-library/react";
import { StatCard } from "./StatCard";
import { plan, type Row, type StatResult } from "./hint";
import { versusFigure, versusScale } from "./VersusList";

// G13: the Singapore references card as a list. G20: a row's money is cents
// under owner_minor and benchmark_minor; months and percents keep their names.
// Every figure is invented.
const ref = { source: "Survey of homes", as_of: "2023", population: "one-person homes" };
const result: StatResult = {
  id: "benchmarks-sg",
  title: "Against Singapore references",
  chart: {
    type: "versus", x: "short", series: ["owner", "benchmark"], source: "headline", unit_field: "unit",
    order_field: "metric", order: ["cash_months", "savings_rate", "monthly_spend", "food_spend", "transport_spend", "housing_spend"],
    unit: "sgd_minor",
  },
  data: {
    // sorted by label, the way the stat writes them; the card puts them in its own order
    headline: [
      { metric: "food_spend", short: "Food", owner_minor: 45040, benchmark_minor: 60000, unit: "SGD per household per month", thin: false, concern: false, ...ref },
      { metric: "housing_spend", short: "Housing", owner_minor: 2500, benchmark_minor: 90000, unit: "SGD per household per month", thin: true,
        thin_reason: "rests on 2 rows over 10 covered months, so it is what these accounts were billed rather than what housing cost",
        concern: false, ...ref },
      { metric: "monthly_spend", short: "All spending", owner_minor: 240000, benchmark_minor: 200000, unit: "SGD per household per month", thin: false, concern: false, ...ref },
      { metric: "cash_months", short: "Months of cash", owner: 1.04, benchmark: 6, unit: "months of expenses", thin: false, concern: true,
        source: "A planning guide", as_of: "2024", population: "working adults" },
      { metric: "savings_rate", short: "Savings rate", owner: 26.4, benchmark: 30, unit: "per cent of monthly income", thin: false, concern: false, ...ref },
      { metric: "odd_one", short: "Something else", owner: 3, benchmark: 4, unit: "widgets", thin: false, concern: false, ...ref },
    ],
  },
  findings: ["Food: 450.40 against 600.00."],
  caveats: ["One person against households."],
};

const rowFor = (name: string) => screen.getByText(name).closest("li") as HTMLElement;

describe("the versus list", () => {
  it("lists the headline in the hint's order, anything else after it", () => {
    render(<StatCard result={result} />);
    fireEvent.click(screen.getByRole("button", { name: "Show all 6" }));
    const names = screen.getAllByRole("listitem").map((li) => li.querySelector("span")?.textContent);
    expect(names.slice(0, 6)).toEqual(["Months of cash", "Savings rate", "All spending", "Food", "Housing", "Something else"]);
  });

  it("opens with the first four rows and a row that shows the rest", () => {
    render(<StatCard result={result} />);
    expect(screen.queryByText("Housing")).not.toBeInTheDocument();
    expect(screen.getByText("Food")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Show all 6" }));
    expect(screen.getByText("Something else")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Show fewer" }));
    expect(screen.queryByText("Housing")).not.toBeInTheDocument();
  });

  it("formats money without decimals, a percent rounded and months to one place", () => {
    render(<StatCard result={result} />);
    expect(within(rowFor("Months of cash")).getByText("1.0")).toBeInTheDocument();
    expect(within(rowFor("Months of cash")).getByText("vs 6.0", { exact: false })).toBeInTheDocument();
    expect(within(rowFor("Savings rate")).getByText("26%")).toBeInTheDocument();
    expect(within(rowFor("Savings rate")).getByText("vs 30%", { exact: false })).toBeInTheDocument();
    expect(within(rowFor("Food")).getByText("S$450")).toBeInTheDocument();
    expect(within(rowFor("All spending")).getByText("vs S$2,000", { exact: false })).toBeInTheDocument();
    expect(screen.getByText("Line = typical. SGD a month.")).toBeInTheDocument();
  });

  it("greys a thin row and says so beside its figures, with the reason on hover", () => {
    render(<StatCard result={result} />);
    fireEvent.click(screen.getByRole("button", { name: "Show all 6" }));
    const housing = rowFor("Housing");
    expect(housing).toHaveAttribute("data-thin", "true");
    expect(housing).toHaveClass("text-ink-3");
    expect(within(housing).getByText("not billed here")).toBeInTheDocument();
    expect(housing.querySelector("[data-part=fill]")).toBeNull();
    expect(housing.title).toMatch(/^Housing rests on 2 rows over 10 covered months/);
  });

  it("draws the owner's figure as the fill and the typical one as a tick on one scale per row", () => {
    render(<StatCard result={result} />);
    const cash = rowFor("Months of cash");
    // 1.04 of 6: the typical figure is the longer, so it sits at the end of the track
    expect((cash.querySelector("[data-part=tick]") as HTMLElement).style.left).toBe("100%");
    expect(parseFloat((cash.querySelector("[data-part=fill]") as HTMLElement).style.width)).toBeCloseTo((1.04 / 6) * 100, 5);
    const all = rowFor("All spending");
    // 2,400 against 2,000: the owner's figure fills the track and the tick sits at five sixths
    expect((all.querySelector("[data-part=fill]") as HTMLElement).style.width).toBe("100%");
    expect(parseFloat((all.querySelector("[data-part=tick]") as HTMLElement).style.left)).toBeCloseTo((2000 / 2400) * 100, 5);
  });

  it("colours only a row the stat flags as a concern", () => {
    render(<StatCard result={result} />);
    expect(within(rowFor("Months of cash")).getByText("1.0")).toHaveClass("text-danger");
    expect((rowFor("Months of cash").querySelector("[data-part=fill]") as HTMLElement).style.background).toBe("var(--danger)");
    // above the typical figure, but a spending line carries no verdict
    expect(within(rowFor("All spending")).getByText("S$2,400")).not.toHaveClass("text-danger");
    expect((rowFor("All spending").querySelector("[data-part=fill]") as HTMLElement).style.background).toBe("var(--c1)");
  });

  it("names the reference's source, date and population on hover", () => {
    render(<StatCard result={result} />);
    expect(rowFor("Months of cash").title).toBe("Typical: A planning guide, 2024. Population: working adults.");
  });

  it("shows no Details toggle and none of the stat's prose", () => {
    render(<StatCard result={result} />);
    expect(screen.queryByRole("button", { name: /details/i })).not.toBeInTheDocument();
    expect(screen.queryByText("Food: 450.40 against 600.00.")).not.toBeInTheDocument();
    expect(screen.queryByText("One person against households.")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /caveats|the numbers|reference numbers/i })).not.toBeInTheDocument();
  });

  it("shows a thin row's figures in the row, in its own unit, where a phone or the keyboard can read them", () => {
    render(<StatCard result={result} />);
    fireEvent.click(screen.getByRole("button", { name: "Show all 6" }));
    const housing = rowFor("Housing");
    expect(within(housing).getByText("S$25 vs S$900")).toBeInTheDocument();
    expect(within(housing).getByText("not billed here")).toBeInTheDocument();
  });

  it("never writes a bare figure in a row that says SGD as money: before G20 that was dollars", () => {
    const headline = (result.data?.headline as Row[]).map((r) =>
      "owner_minor" in r
        ? (({ owner_minor, benchmark_minor, ...rest }) => ({ ...rest, owner: Number(owner_minor) / 100, benchmark: Number(benchmark_minor) / 100 }))(r)
        : r);
    render(<StatCard result={{ ...result, data: { headline } }} />);
    expect(within(rowFor("All spending")).getByText("2,400")).toBeInTheDocument();
    expect(within(rowFor("Food")).getByText("vs 600", { exact: false })).toBeInTheDocument();
    expect(screen.queryByText(/S\$/)).not.toBeInTheDocument();
    expect(screen.getByText("Line = typical.")).toBeInTheDocument();
  });

  it("is an empty card, not some other list, when there is no headline", () => {
    const p = plan({ ...result, data: { headline: [], metrics: [{ key: "x", value: 1 }] } });
    expect(p.kind).toBe("table");
    expect(p.note).toMatch(/no rows/);
  });
});

describe("versus helpers", () => {
  it("formats each unit", () => {
    expect(versusFigure(123456, "sgd_minor")).toBe("S$1,235");
    // a bare figure is never money, whatever the row's unit says
    expect(versusFigure(1234.56, "SGD per month")).toBe("1,234.6");
    expect(versusFigure(26.4, "per cent of monthly income")).toBe("26%");
    expect(versusFigure(0.264, "fraction")).toBe("26%");
    expect(versusFigure(1, "months of expenses")).toBe("1.0");
    expect(versusFigure(null, "SGD per month")).toBe("");
  });

  it("scales to the larger figure and never runs backwards", () => {
    expect(versusScale(50, 100)).toEqual({ fill: 50, tick: 100 });
    expect(versusScale(200, 100)).toEqual({ fill: 100, tick: 50 });
    expect(versusScale(-20, 100)).toEqual({ fill: 0, tick: 100 });
    expect(versusScale(0, 0)).toEqual({ fill: 0, tick: 0 });
    expect(versusScale(5, null)).toEqual({ fill: 100, tick: null });
  });
});
