import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { SpendChart, SpendingExplorer, StackTooltip, colourFor, iconFor, percent, signed, type Spending } from "./SpendingExplorer";
import { BUCKET_ICON } from "../components/BucketMark";
import { json, useSizedCharts } from "../test-helpers";

const base: Spending = {
  by: "bucket", scope: "sg", bucket_order: ["Food", "Getting around", "Lifestyle"], bucket_style: {},
  available: { from: "2026-01", to: "2026-09" }, months: ["2026-08", "2026-09"],
  series: [{ name: "Food", total: 60_000, per_month: 30_000, values: [20_000, 40_000], link: { bucket: "Food" } }],
  buckets: [{ name: "Food", total: 60_000, per_month: 30_000, categories: [
    { name: "Eating out", total: 50_000, per_month: 25_000, link: { category: "Eating out" } },
    { name: "Groceries", total: 10_000, per_month: 5_000, link: { category: "Groceries" } }] }],
  payees: [{ name: "Power Source", total: 6_000, count: 4, link: { payee: "Power Source" } }],
  biggest: [{ key: "a1b2c3d4e5f6", date: "2026-09-28", payee: "NUS", category: "Education", amount: 507_500 }],
  link: { from: "2026-08", to: "2026-09", spend: "1", home: "1" },
  totals: { spend: 60_000, per_month: 30_000, overseas: 11_000 },
};

// the server's answer to one request: the base figures, over the months asked for
function answer(url: unknown, body: Spending = base) {
  const q = new URL(String(url), "http://x").searchParams;
  const months: string[] = [];
  if (q.get("from") && q.get("to")) {
    let [y, m] = q.get("from")!.split("-").map(Number);
    const [ty, tm] = q.get("to")!.split("-").map(Number);
    while (y * 12 + m <= ty * 12 + tm) {
      months.push(`${y}-${String(m).padStart(2, "0")}`);
      [y, m] = m === 12 ? [y + 1, 1] : [y, m + 1];
    }
  }
  if (!months.length && body.available) {
    // with no range asked, the server's own default: the last 12 months the record holds, never before its first
    let [y, m] = body.available.to.split("-").map(Number);
    for (let i = 0; i < 12; i++) {
      const month = `${y}-${String(m).padStart(2, "0")}`;
      if (month < body.available.from) break;
      months.unshift(month);
      [y, m] = m === 1 ? [y - 1, 12] : [y, m - 1];
    }
  }
  const scope = q.get("scope") ?? "sg";
  const shown = months.length ? months : base.months;
  // the server's own link: the range, the scope and the idea of spending, which every figure's rows share
  const link = { from: shown[0], to: shown[shown.length - 1], spend: "1", ...(scope === "sg" ? { home: "1" } : {}) };
  return json({ ...body, link, by: q.get("by") ?? "bucket", scope, months: shown,
    series: body.series.map((x) => ({ ...x, values: (months.length ? months : base.months).map(() => 1000) })) });
}

describe("SpendingExplorer", () => {
  // the controls live in the address: each test starts on the bare page
  beforeEach(() => window.history.replaceState(null, "", "/spending"));
  afterEach(() => window.history.replaceState(null, "", "/"));

  it("shows no figures that do not answer the controls on screen", async () => {
    let calls = 0;
    vi.spyOn(globalThis, "fetch").mockImplementation(async (url) => {
      calls += 1;
      // the first answer is the default view on screen; the overseas one fails
      return calls <= 1 ? answer(url) : json({ error: "boom" }, 500);
    });
    render(<SpendingExplorer />);
    expect(await screen.findByText("left out of these figures")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("radio", { name: "Include overseas" }));
    expect(await screen.findByText("boom")).toBeInTheDocument();
    expect(screen.queryByText("left out of these figures")).not.toBeInTheDocument();
    expect(screen.queryByText("S$110")).not.toBeInTheDocument();
  });

  it("keeps a bucket's colour whatever its rank", () => {
    expect(colourFor("Lifestyle", 0, base)).toBe(colourFor("Lifestyle", 5, base));
    expect(colourFor("Everything else", 0, base, true)).toBe("var(--rest)");
    expect(colourFor("Everything else", 0, { ...base, by: "category" })).not.toBe("var(--rest)");
  });

  // G18: Spending had eight colours for nine buckets, so Other drew in Food's
  const BUCKETS = ["Food", "Home and bills", "Getting around", "Health and fitness", "Growth", "Lifestyle", "Travel", "Giving", "Other"];

  it("gives nine buckets nine different colours, and never the folded rest's", () => {
    const data = { by: "bucket" as const, bucket_order: BUCKETS };
    const colours = BUCKETS.map((b, rank) => colourFor(b, rank, data));
    expect(new Set(colours).size).toBe(9);
    expect(colourFor("Other", 0, data)).not.toBe(colourFor("Food", 0, data));
    expect(colours).not.toContain(colourFor("Everything else", 0, data, true));
    // a range that ranks Other first does not change its colour
    expect(colourFor("Other", 0, data)).toBe(colours[8]);
  });

  it("draws a bucket in the colour the server shows it in, and the rest by their order", () => {
    // G17, G18: bucket_style from /api/spending; `shown` is the colour worked out over every bucket
    const data = {
      by: "bucket" as const, bucket_order: BUCKETS,
      bucket_style: {
        Food: { icon: "utensils" as const, colour: "c11" as const, shown: "c11" as const },
        Other: { icon: "shapes" as const, colour: null, shown: "c1" as const },
      },
    };
    expect(colourFor("Food", 3, data)).toBe("var(--c11)");
    // Other took Food's place colour: the server moved it, and the page draws what the server says
    expect(colourFor("Other", 0, data)).toBe("var(--c1)");
    expect(colourFor("Food", 3, { ...data, by: "category" })).toBe(colourFor("Food", 3, { by: "category", bucket_order: [] }));
    // an answer kept from before G18 has no `shown`: the chosen colour, else the place's
    expect(colourFor("Lifestyle", 0, { by: "bucket", bucket_order: BUCKETS, bucket_style: {} })).toBe(colourFor("Lifestyle", 0, { by: "bucket", bucket_order: BUCKETS }));
  });

  it("puts each bucket's icon in the legend and the hover card, and none on another split (G18)", async () => {
    const d = {
      ...base, months: ["2026-08"], link: { from: "2026-08", to: "2026-08" },
      series: [
        { name: "Food", total: 1000, per_month: 1000, values: [1000], link: { bucket: "Food" } },
        { name: "Lifestyle", total: 500, per_month: 500, values: [500], link: { bucket: "Lifestyle" } },
      ],
      bucket_style: {
        Food: { icon: "coffee" as const, colour: "c5" as const, shown: "c5" as const },
        Lifestyle: { icon: "shopping-bag" as const, colour: null, shown: "c2" as const },
      },
    };
    expect(iconFor("Food", d)).toBe(BUCKET_ICON.coffee);
    expect(iconFor("Food", { ...d, by: "category" })).toBeUndefined();
    expect(iconFor("Everything else", d, true)).toBeUndefined();
    render(<SpendChart d={d} view="stacked" open={() => {}} />);
    const legend = screen.getByRole("button", { name: /Food/ });
    expect(legend.querySelector("[data-bucket-icon]")?.getAttribute("class")).toContain("lucide-coffee");
  });

  it("keeps twelve buckets apart too, and colours a split by rank without repeating up to twelve", () => {
    const twelve = Array.from({ length: 12 }, (_, i) => `Bucket ${i}`);
    expect(new Set(twelve.map((b) => colourFor(b, 0, { by: "bucket", bucket_order: twelve }))).size).toBe(12);
    expect(new Set(twelve.map((b, rank) => colourFor(b, rank, { by: "payee", bucket_order: [] }))).size).toBe(12);
  });

  it("opens on Singapore only by bucket over the last 12 months with one request, then follows the toggles", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(async (url) => answer(url));
    render(<SpendingExplorer />);
    expect(await screen.findByText("left out of these figures")).toBeInTheDocument();
    expect(screen.getByText("S$110")).toBeInTheDocument();                  // overseas
    // the first answer already is the default view: no second explore of the same months
    expect(fetchSpy.mock.calls.map(([u]) => String(u))).toEqual(["/api/spending"]);
    fireEvent.click(screen.getByRole("radio", { name: "Include overseas" }));
    fireEvent.click(screen.getByRole("radio", { name: "By payee" }));
    fireEvent.click(screen.getByRole("radio", { name: "3M" }));
    await waitFor(() => expect(fetchSpy.mock.calls.map(([u]) => String(u)))
      .toContain("/api/spending?scope=all&by=payee&to=2026-09&from=2026-07"));
  });

  it("never starts a range before the record's first month, so a month is the same average Home shows", async () => {
    // the record holds 2026-01 to 2026-09: nine months, fewer than 12M asks for
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(async (url) => answer(url));
    render(<SpendingExplorer />);
    expect(await screen.findByText("9 months")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("radio", { name: "Include overseas" }));
    await waitFor(() => expect(fetchSpy.mock.calls.map(([u]) => String(u)))
      .toContain("/api/spending?scope=all&by=bucket&to=2026-09&from=2026-01"));
    expect(fetchSpy.mock.calls.map(([u]) => String(u)).join(" ")).not.toContain("from=2025");
    expect(await screen.findByText("9 months")).toBeInTheDocument();
  });

  it("keeps the range, scope and split in the address, so Back and a copied link restore them", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(async (url) => answer(url));
    const first = render(<SpendingExplorer />);
    await screen.findByText("left out of these figures");
    // the defaults keep a clean address
    expect(window.location.search).toBe("");
    fireEvent.click(screen.getByRole("radio", { name: "3M" }));
    fireEvent.click(screen.getByRole("radio", { name: "Include overseas" }));
    fireEvent.click(screen.getByRole("radio", { name: "By payee" }));
    expect(Object.fromEntries(new URLSearchParams(window.location.search))).toEqual({ range: "3", scope: "all", by: "payee" });
    // leaving and coming back mounts a fresh page on the same address
    first.unmount();
    render(<SpendingExplorer />);
    await waitFor(() => expect(screen.getByRole("radio", { name: "3M" })).toHaveAttribute("aria-checked", "true"));
    expect(screen.getByRole("radio", { name: "Include overseas" })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByRole("radio", { name: "By payee" })).toHaveAttribute("aria-checked", "true");
    fireEvent.click(screen.getByRole("radio", { name: "All" }));
    expect(new URLSearchParams(window.location.search).get("range")).toBe("all");
    await waitFor(() => expect(fetchSpy.mock.calls.map(([u]) => String(u)))
      .toContain("/api/spending?scope=all&by=payee&to=2026-09&from=2026-01"));
    // back to the defaults, back to a bare address
    fireEvent.click(screen.getByRole("radio", { name: "12M" }));
    fireEvent.click(screen.getByRole("radio", { name: "Singapore only" }));
    fireEvent.click(screen.getByRole("radio", { name: "By bucket" }));
    expect(window.location.search).toBe("");
  });

  it("reads a range, scope or split it does not know as the default", async () => {
    window.history.replaceState(null, "", "/spending?range=7&scope=moon&by=colour");
    vi.spyOn(globalThis, "fetch").mockImplementation(async (url) => answer(url));
    render(<SpendingExplorer />);
    await screen.findByText("left out of these figures");
    expect(screen.getByRole("radio", { name: "12M" })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByRole("radio", { name: "Singapore only" })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByRole("radio", { name: "By bucket" })).toHaveAttribute("aria-checked", "true");
  });

  it("lists two identical biggest payments as two rows, keyed by their own transaction keys", async () => {
    const twice = { key: "", date: "2026-09-03", payee: "Shop", category: "Shopping", amount: 5_000 };
    vi.spyOn(globalThis, "fetch").mockImplementation(async (url) => answer(url, {
      ...base, biggest: [{ ...twice, key: "dup1" }, { ...twice, key: "dup2" }] }));
    const warn = vi.spyOn(console, "error").mockImplementation(() => {});
    render(<SpendingExplorer />);
    const rows = await screen.findAllByRole("link", { name: /Shop/ });
    expect(rows.map((a) => a.getAttribute("href"))).toEqual(["/transactions?key=dup1", "/transactions?key=dup2"]);
    expect(warn.mock.calls.flat().join(" ")).not.toMatch(/same key/);
  });

  it("shows four biggest payments and four top payees, each list with its own row to show the rest", async () => {
    const many = {
      ...base,
      payees: Array.from({ length: 6 }, (_, i) => ({ name: `Payee ${i + 1}`, total: 1_000 * (6 - i), count: 2, link: { payee: `Payee ${i + 1}` } })),
      biggest: Array.from({ length: 6 }, (_, i) => ({ key: `key${i + 1}`, date: "2026-09-01", payee: `Big ${i + 1}`, category: "Shopping", amount: 10_000 * (6 - i) })),
    };
    vi.spyOn(globalThis, "fetch").mockImplementation(async (url) => answer(url, many));
    render(<SpendingExplorer />);
    await screen.findByText("Biggest payments");
    expect(screen.queryByText("Big 5")).not.toBeInTheDocument();
    expect(screen.getByText("Big 4")).toBeInTheDocument();
    expect(screen.queryByText("Payee 5")).not.toBeInTheDocument();
    const toggles = screen.getAllByRole("button", { name: "Show all 6" });
    expect(toggles).toHaveLength(2);
    fireEvent.click(toggles[0]);
    expect(screen.getByText("Big 6")).toBeInTheDocument();
    expect(screen.queryByText("Payee 5")).not.toBeInTheDocument();           // the other list stays shut
    // the nested "where it went" list has no toggle of its own
    expect(screen.getAllByRole("button", { name: /Show (all|fewer)/ })).toHaveLength(2);
  });

  it("opens a bucket to its categories, each linking to its transactions", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (url) => answer(url));
    render(<SpendingExplorer />);
    // the legend also has a Food button; the one that opens a bucket is the one that expands
    await screen.findAllByRole("button", { name: /Food/ });
    fireEvent.click(screen.getAllByRole("button", { name: /Food/ }).find((b) => b.hasAttribute("aria-expanded"))!);
    // every link carries the range, Singapore only, and the explorer's idea of spending
    expect(screen.getByRole("link", { name: /Eating out/ }))
      .toHaveAttribute("href", "/transactions?from=2026-01&to=2026-09&spend=1&home=1&category=Eating+out");
    // a biggest payment opens that one row, by its key; a top payee opens its exact name within the same range
    expect(screen.getByRole("link", { name: /NUS/ })).toHaveAttribute("href", "/transactions?key=a1b2c3d4e5f6");
    expect(screen.getByRole("link", { name: /Power Source/ }))
      .toHaveAttribute("href", "/transactions?from=2026-01&to=2026-09&spend=1&home=1&payee=Power+Source");
  });

  it("links a payee the statement left unnamed too, since it is matched exactly", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (url) => answer(url, {
      ...base, payees: [{ name: "(no name)", total: 3_000, count: 2, link: { payee: "(no name)" } }],
      biggest: [{ key: "k0", date: "2026-09-01", payee: "(no name)", category: "Shopping", amount: 3_000 }],
    }));
    render(<SpendingExplorer />);
    const links = await screen.findAllByRole("link", { name: /\(no name\)/ });
    expect(links.map((a) => a.getAttribute("href"))).toEqual([
      "/transactions?key=k0", "/transactions?from=2026-01&to=2026-09&spend=1&home=1&payee=%28no+name%29"]);
  });
});

describe("Where it went shares", () => {
  it("says what share of all spending each bucket and category is", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (url) => answer(url));
    render(<SpendingExplorer />);
    const food = (await screen.findAllByRole("button", { name: /Food/ })).find((b) => b.hasAttribute("aria-expanded"))!;
    expect(within(food).getByText("100%")).toBeInTheDocument();
    fireEvent.click(food);
    expect(within(screen.getByRole("link", { name: /Eating out/ })).getByText("83%")).toBeInTheDocument();
    expect(within(screen.getByRole("link", { name: /Groceries/ })).getByText("17%")).toBeInTheDocument();
  });

  it("signs a change with a plus, or a true minus that stays glued to the amount", () => {
    expect(signed(20_000)).toBe("+S$200");
    expect(signed(-42_000)).toBe("\u2212S$420");
    expect(signed(-42_000)).not.toContain("-");
  });

  it("rounds to whole percents, and does not call a small real share zero", () => {
    expect(percent(23_400, 100_000)).toBe("23%");
    expect(percent(400, 100_000)).toBe("<1%");
    expect(percent(0, 100_000)).toBe("0%");
  });

  it("gives a refund-heavy bucket a signed share, and no share when net spending is not positive", () => {
    expect(percent(-12_000, 100_000)).toBe("\u2212" + "12%");
    expect(percent(-400, 100_000)).toBe("\u2212<1%");
    expect(percent(5, 0)).toBe("n/a");
    expect(percent(-5, -100)).toBe("n/a");
  });

  it("shows a negative bucket as a signed share beside the others, with an empty bar", async () => {
    const refunds = { ...base, totals: { ...base.totals, spend: 50_000 }, buckets: [
      { name: "Food", total: 60_000, per_month: 30_000, categories: [] as Spending["buckets"][number]["categories"] },
      { name: "Lifestyle", total: -10_000, per_month: -5_000, categories: [] as Spending["buckets"][number]["categories"] },
    ] };
    vi.spyOn(globalThis, "fetch").mockImplementation(async (url) => answer(url, refunds));
    render(<SpendingExplorer />);
    const life = (await screen.findAllByRole("button", { name: /Lifestyle/ })).find((b) => b.hasAttribute("aria-expanded"))!;
    expect(within(life).getByText("\u221220%")).toBeInTheDocument();
    const food = screen.getAllByRole("button", { name: /Food/ }).find((b) => b.hasAttribute("aria-expanded"))!;
    expect(within(food).getByText("120%")).toBeInTheDocument();
  });

  it("says n/a on every row when net spending is not positive", async () => {
    const net0 = { ...base, totals: { ...base.totals, spend: 0 } };
    vi.spyOn(globalThis, "fetch").mockImplementation(async (url) => answer(url, net0));
    render(<SpendingExplorer />);
    const food = (await screen.findAllByRole("button", { name: /Food/ })).find((b) => b.hasAttribute("aria-expanded"))!;
    expect(within(food).getByText("n/a")).toBeInTheDocument();
  });
});

describe("SpendChart views", () => {
  useSizedCharts();

  const d = {
    by: "bucket" as const, bucket_order: ["Food", "Lifestyle"], months: ["2026-08", "2026-09"],
    link: { from: "2026-08", to: "2026-09", spend: "1", home: "1" },
    series: [
      { name: "Food", total: 60_000, per_month: 30_000, values: [20_000, 40_000], link: { bucket: "Food" } },
      { name: "Lifestyle", total: 10_000, per_month: 5_000, values: [10_000, 0], link: { bucket: "Lifestyle" } },
    ],
  };
  const range = "from=2026-08&to=2026-09&spend=1&home=1";

  it("opens the rows a bar adds up: a stacked bar its own series in that month, the total bar the whole month", async () => {
    const open = vi.fn();
    const { container, rerender } = render(<SpendChart d={d} view="stacked" open={open} />);
    await waitFor(() => expect(container.querySelectorAll(".recharts-bar-rectangle path").length).toBeGreaterThan(0));
    fireEvent.click(container.querySelectorAll(".recharts-bar-rectangle path")[1]);
    expect(open).toHaveBeenLastCalledWith(`/transactions?${range}&bucket=Food&month=2026-09`);
    fireEvent.click(container.querySelectorAll(".recharts-bar-rectangle path")[2]);
    expect(open).toHaveBeenLastCalledWith(`/transactions?${range}&bucket=Lifestyle&month=2026-08`);
    rerender(<SpendChart d={d} view="total" open={open} />);
    await waitFor(() => expect(container.querySelectorAll(".recharts-bar-rectangle")).toHaveLength(2));
    fireEvent.click(container.querySelectorAll(".recharts-bar-rectangle path")[0]);
    expect(open).toHaveBeenLastCalledWith(`/transactions?${range}&month=2026-08`);
  });

  it("opens the rest of a folded series as everything the named ones are not", async () => {
    const open = vi.fn();
    const rest = { name: "Everything else", total: 5_000, per_month: 2_500, values: [2_000, 3_000], folded: 4, link: { not_categories: '["Food","Shopping"]' } };
    const { container } = render(<SpendChart d={{ ...d, by: "category", series: [d.series[0], rest] }} view="stacked" open={open} />);
    await waitFor(() => expect(container.querySelectorAll(".recharts-bar-rectangle path").length).toBeGreaterThan(0));
    fireEvent.click(container.querySelectorAll(".recharts-bar-rectangle path")[3]);
    expect(open).toHaveBeenLastCalledWith(`/transactions?${range}&not_categories=%5B%22Food%22%2C%22Shopping%22%5D&month=2026-09`);
  });

  it("opens nothing when the answer carries no link for its rows", async () => {
    const open = vi.fn();
    const { container } = render(<SpendChart d={{ ...d, link: undefined as never }} view="stacked" open={open} />);
    await waitFor(() => expect(container.querySelectorAll(".recharts-bar-rectangle path").length).toBeGreaterThan(0));
    fireEvent.click(container.querySelectorAll(".recharts-bar-rectangle path")[1]);
    expect(open).not.toHaveBeenCalled();
    expect(container.querySelector("[cursor=pointer]")).toBeNull();
  });

  it("opens a line's dot on Lines: its series in the month under the pointer, and nothing from a click off a dot", async () => {
    const open = vi.fn();
    const { container } = render(<SpendChart d={d} view="lines" open={open} />);
    await waitFor(() => expect(container.querySelectorAll(".recharts-line-curve")).toHaveLength(2));
    const wrapper = container.querySelector(".recharts-wrapper")!;
    // the pointer sits over the second month: its dots are the active ones
    fireEvent.mouseMove(wrapper, { clientX: 700, clientY: 100 });
    await waitFor(() => expect(container.querySelectorAll(".recharts-active-dot circle").length).toBeGreaterThan(0));
    // a click that lands off a dot opens nothing
    fireEvent.click(wrapper, { clientX: 700, clientY: 100 });
    expect(open).not.toHaveBeenCalled();
    const dots = [...container.querySelectorAll(".recharts-active-dot circle")];
    fireEvent.click(dots[0], { clientX: 700, clientY: 100 });
    expect(open).toHaveBeenLastCalledWith(`/transactions?${range}&bucket=Food&month=2026-09`);
    fireEvent.click(dots[1], { clientX: 700, clientY: 100 });
    expect(open).toHaveBeenLastCalledWith(`/transactions?${range}&bucket=Lifestyle&month=2026-09`);
    // and the dot's mark is spent: the next click off a dot opens nothing again
    open.mockClear();
    fireEvent.click(wrapper, { clientX: 700, clientY: 100 });
    expect(open).not.toHaveBeenCalled();
  });

  it("keys nine buckets in nine colours, Other's own and not Food's (G18)", async () => {
    const names = ["Food", "Home and bills", "Getting around", "Health and fitness", "Growth", "Lifestyle", "Travel", "Giving", "Other"];
    const nine = {
      ...d, bucket_order: names,
      series: names.map((name) => ({ name, total: 1_000, per_month: 500, values: [500, 500], link: { bucket: name } })),
    };
    const { container } = render(<SpendChart d={nine} view="stacked" open={() => {}} />);
    await waitFor(() => expect(container.querySelectorAll(".recharts-bar-rectangle path").length).toBeGreaterThan(0));
    const key = [...container.querySelectorAll("button[aria-pressed] [style]")].map((s) => (s as HTMLElement).style.background);
    expect(key).toHaveLength(9);
    expect(new Set(key).size).toBe(9);
    // and the bars carry those colours
    const fills = new Set([...container.querySelectorAll(".recharts-bar-rectangle path")].map((b) => b.getAttribute("fill")));
    expect(fills.size).toBe(9);
  });

  it("draws a line a series on Lines, and starts the hidden set afresh when the series change", async () => {
    const { container, rerender } = render(<SpendChart d={d} view="lines" open={() => {}} />);
    await waitFor(() => expect(container.querySelectorAll(".recharts-line-curve")).toHaveLength(2));
    fireEvent.click(screen.getByRole("button", { name: "Lifestyle" }));
    expect(container.querySelectorAll(".recharts-line-curve")).toHaveLength(1);
    rerender(<SpendChart d={{ ...d, series: [d.series[0], { ...d.series[1], name: "Travel" }] }} view="lines" open={() => {}} />);
    await waitFor(() => expect(container.querySelectorAll(".recharts-line-curve")).toHaveLength(2));
  });
});

describe("StackTooltip", () => {
  const row = { month: "2026-09-01", Food: 40_000, Lifestyle: 0, Travel: 5_000 };
  const series = [{ key: "Food", colour: "red" }, { key: "Lifestyle", colour: "green" }, { key: "Travel", colour: "blue" }];

  it("lists the series that spent something, and leaves out one at zero", () => {
    render(<StackTooltip active label="2026-09-01" payload={[{ payload: row }]} nameOf={(k) => String(k)} series={series} />);
    expect(screen.getByText("Food")).toBeInTheDocument();
    expect(screen.getByText("Travel")).toBeInTheDocument();
    expect(screen.queryByText("Lifestyle")).toBeNull();
    expect(screen.queryByText("S$0")).toBeNull();
    // the month's total still counts every series
    expect(screen.getByText("S$450")).toBeInTheDocument();
  });

  it("sits in the chart kit's hover card, whose width is capped to a phone screen (G18)", () => {
    const { container } = render(<StackTooltip active label="2026-09-01" payload={[{ payload: row }]} nameOf={(k) => String(k)} series={series} />);
    const box = container.firstElementChild as HTMLElement;
    expect(box.className).toContain("min-w-52");
    expect(box.className).toContain("max-w-[min(22rem,calc(100vw-2rem))]");
  });

  it("promises a click only when the chart can keep it", () => {
    const { unmount } = render(<StackTooltip active label="2026-09-01" payload={[{ payload: row }]} nameOf={(k) => String(k)} series={series} />);
    expect(screen.queryByText(/Click/)).toBeNull();
    unmount();
    render(<StackTooltip active label="2026-09-01" payload={[{ payload: row }]} nameOf={(k) => String(k)} series={series} hint="Click a bar to see its transactions" />);
    expect(screen.getByText("Click a bar to see its transactions")).toBeInTheDocument();
  });

  it("does the same on a stacked chart's own payload, and in the line view", () => {
    const payload = [{ dataKey: "Food", value: 40_000, color: "red", payload: row }, { dataKey: "Lifestyle", value: 0, color: "green", payload: row }];
    const { unmount } = render(<StackTooltip active label="2026-09-01" payload={payload} nameOf={(k) => String(k)} />);
    expect(screen.queryByText("Lifestyle")).toBeNull();
    unmount();
    render(<StackTooltip active label="2026-09-01" payload={[{ payload: row }]} nameOf={(k) => String(k)} series={series} bySize />);
    expect(screen.queryByText("Lifestyle")).toBeNull();
  });
});
