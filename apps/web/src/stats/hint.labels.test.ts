import { barLink, label, plan, tickLabel } from "./hint";

describe("chart labels", () => {
  it("names series the way a person would", () => {
    expect(label("oa")).toBe("OA");
    expect(label("contributed_sgd")).toBe("Put in");
    expect(label("value_sgd")).toBe("Value");
    expect(label("net_after_investing")).toBe("Net after investing");
  });

  it("reads months and days, and leaves other text alone", () => {
    expect(tickLabel("2025-10")).toBe("Oct 25");
    expect(tickLabel("2025-10", true)).toBe("Oct 2025");
    expect(tickLabel("2024-07-01")).toBe("1 Jul 24");
    expect(tickLabel("Eating out")).toBe("Eating out");
    expect(tickLabel(3)).toBe("3");
  });
});

describe("bar links", () => {
  const links = {
    spend: { month: "{x}", spend: "1" },
    savings: { month: "{x}", transfer: "1", need: "Investment,Savings", hide_transfers: "0" },
    payee: { payee: "{x}", spend: "1" },
  };
  const row = { month: "2026-09", spend_minor: 1000 };

  it("fills the bar's own x value into the filters its stat wrote", () => {
    expect(barLink(links, "spend", row, "month")).toBe("/transactions?month=2026-09&spend=1");
    expect(barLink(links, "savings", row, "month")).toBe("/transactions?month=2026-09&transfer=1&need=Investment%2CSavings&hide_transfers=0");
  });

  it("fills another field of the same row where the stat names one, a week's last day", () => {
    const weekly = { spend: { from: "{x}", to: "{end}", spend: "1", categories: '["Eating out"]' } };
    expect(barLink(weekly, "spend", { week: "2026-06-08", end: "2026-06-14", spend: 33 }, "week"))
      .toBe("/transactions?from=2026-06-08&to=2026-06-14&spend=1&categories=%5B%22Eating+out%22%5D");
    // a field the row does not carry is left as written, not guessed
    expect(barLink(weekly, "spend", { week: "2026-06-08", spend: 33 }, "week")).toContain("to=%7Bend%7D");
  });

  it("links a payee as it is named, placeholders included, because the filter names them the same way", () => {
    expect(barLink(links, "payee", { payee: "Grab" }, "payee")).toBe("/transactions?payee=Grab&spend=1");
    expect(barLink(links, "payee", { payee: "(no name)" }, "payee")).toBe("/transactions?payee=%28no+name%29&spend=1");
  });

  it("gives a series its stat did not link, and a bar with no x value, no link", () => {
    expect(barLink(links, "net", row, "month")).toBeNull();
    expect(barLink(undefined, "spend", row, "month")).toBeNull();
    expect(barLink(links, "spend", { month: "" }, "month")).toBeNull();
    expect(barLink(links, "spend", undefined, "month")).toBeNull();
    expect(barLink(links, "spend", { month: 3 }, "month")).toBeNull();
  });

  it("rides on the plan, from the chart hint", () => {
    const p = plan({ id: "monthly-cashflow", title: "Cash flow", chart: { type: "bar", x: "month", series: ["spend_minor"], unit: "sgd_minor", links },
      data: { months: [row] } });
    expect(p.kind === "chart" && p.links).toEqual(links);
    const without = plan({ id: "x", title: "X", chart: { type: "bar", x: "month", series: ["spend_minor"], unit: "sgd_minor" }, data: { months: [row] } });
    expect(without.kind === "chart" && without.links).toEqual({});
  });
});
