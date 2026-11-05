import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { treeFor, TreemapState } from "./TreemapState";
import type { Slice } from "./model";
import type { CategoriesState, WidgetAnswer, WidgetQuery } from "../../../types.gen";

const navigate = vi.fn();
vi.mock("../../../router", async (importActual) => ({
  ...(await importActual<typeof import("../../../router")>()), navigate: (to: string) => navigate(to),
}));

// invented figures only
const QUERY: WidgetQuery = { source: "spending", metric: "spend", dimension: "category" };

const ANSWER: WidgetAnswer = {
  source: "spending", metric: "spend", dimension: "category", unit: "sgd_minor",
  labels: ["Groceries", "Coffee", "MRT", "Oddments"],
  label_links: [
    { from: "2026-10", to: "2026-10", spend: "1", category: "Groceries", home: "1" },
    { from: "2026-10", to: "2026-10", spend: "1", category: "Coffee", home: "1" },
    { from: "2026-10", to: "2026-10", spend: "1", category: "MRT", home: "1" },
    { from: "2026-10", to: "2026-10", spend: "1", category: "Oddments", home: "1" },
  ],
  series: [{ name: "spend", values_minor: [90_000, 30_000, 60_000, 20_000] }],
  range: { from: "2026-10", to: "2026-10" },
};

const entry = (id: number, name: string, group: string) => ({
  id, name, need_class: "Need" as const, cost_type: "Variable", group,
  transactions: 0, monthly: null, budgets: 0, rules: 0, personal_rules: 0,
});

const CATS: CategoriesState = {
  groups: [
    { id: 1, name: "Food", kind: "spend", fixed: false, icon: "utensils", colour: null, shown: "c4",
      categories: [entry(11, "Groceries", "Food"), entry(12, "Coffee", "Food")] },
    { id: 2, name: "Getting around", kind: "spend", fixed: false, icon: "bus", colour: null, shown: "c2",
      categories: [entry(13, "MRT", "Getting around")] },
  ],
  need_classes: { spend: [], income: [], transfer: [] },
  months: 0,
};

function stubApi(answer: unknown = ANSWER, status = 200) {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.startsWith("/api/categories")) return new Response(JSON.stringify(CATS), { status: 200 });
    return new Response(JSON.stringify(answer), { status });
  }));
}

/** Lets Recharts' ResponsiveContainer see a size in jsdom, so the chart draws. */
function stubSize(width = 640, height = 300) {
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue(
    { width, height, top: 0, left: 0, right: width, bottom: height, x: 0, y: 0, toJSON: () => ({}) } as DOMRect);
}

const leaves = () => document.querySelectorAll(".recharts-treemap-depth-2");

beforeEach(() => {
  navigate.mockClear();
  stubApi();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("treeFor", () => {
  const slices: Slice[] = [
    { key: "Groceries", name: "Groceries", value: 90_000, link: { category: "Groceries" } },
    { key: "Coffee", name: "Coffee", value: 30_000, link: { category: "Coffee" } },
    { key: "MRT", name: "MRT", value: 60_000, link: { category: "MRT" } },
    { key: "Oddments", name: "Oddments", value: 20_000, link: { category: "Oddments" } },
    { key: "Everything else", name: "Everything else", value: 10_000, link: null },
  ];

  it("nests each slice under its bucket, biggest first, the largest leaf carrying the group name", () => {
    const tree = treeFor(slices, CATS.groups);
    expect(tree.map((g) => g.name)).toEqual(["Food", "Getting around", "(no bucket)"]);
    expect(tree[0].children.map((l) => l.name)).toEqual(["Groceries", "Coffee"]);
    expect(tree[0].children[0].head).toBe(true);
    expect(tree[0].children[1].head).toBe(false);
    // the bucket's own shown colour, not a series colour; the rest folds to the neutral
    expect(tree[0].children[0].colour).toBe("var(--c4)");
    expect(tree[1].children[0].colour).toBe("var(--c2)");
    expect(tree[2].children.map((l) => l.name)).toEqual(["Oddments", "Everything else"]);
  });

  it("draws flat in series colours when no buckets are given", () => {
    const tree = treeFor(slices, null);
    expect(tree).toHaveLength(1);
    expect(tree[0].children.map((l) => l.name)).toEqual(["Groceries", "MRT", "Coffee", "Oddments", "Everything else"]);
    expect(tree[0].children[0].colour).toBe("var(--c1)");
    expect(tree[0].children[1].colour).toBe("var(--c2)");
  });
});

describe("TreemapState", () => {
  it("renders the fixture answer nested by bucket, each group in the legend", async () => {
    stubSize();
    render(<TreemapState config={{ title: "Spending by category", query: QUERY }} />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Food" })).toBeInTheDocument());
    expect(screen.getByRole("button", { name: "Getting around" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "(no bucket)" })).toBeInTheDocument();
    await waitFor(() => expect(leaves().length).toBe(4));
    // the leaves carry their bucket's colour: Food's c4 on two of them
    const fills = [...document.querySelectorAll(".recharts-treemap-depth-2 > g > g > rect")]
      .map((r) => r.getAttribute("fill"));
    expect(fills.filter((f) => f === "var(--c4)").length).toBe(2);
    expect(fills.filter((f) => f === "var(--c2)").length).toBe(1);
  });

  it("hides a whole group from the legend and never lets the last one hide", async () => {
    stubSize();
    render(<TreemapState config={{ title: "Spending by category", query: QUERY }} />);
    await waitFor(() => expect(leaves().length).toBe(4));
    fireEvent.click(screen.getByRole("button", { name: "Food" }));
    await waitFor(() => expect(leaves().length).toBe(2));
    fireEvent.click(screen.getByRole("button", { name: "Getting around" }));
    expect(screen.getByRole("button", { name: "(no bucket)" })).toBeDisabled();
    expect(leaves().length).toBe(1);
  });

  it("opens a tile's transactions on click", async () => {
    stubSize();
    render(<TreemapState config={{ title: "Spending by category", query: QUERY }} />);
    await waitFor(() => expect(leaves().length).toBe(4));
    fireEvent.click(leaves()[0].querySelector("rect")!);
    expect(navigate).toHaveBeenCalledWith(
      "/transactions?from=2026-10&to=2026-10&spend=1&category=Groceries&home=1");
  });

  it("draws flat with a slice a legend entry when groupBy is none", async () => {
    stubSize();
    render(<TreemapState config={{ title: "Spending by category", query: QUERY, groupBy: "none" }} />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Groceries" })).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "Food" })).not.toBeInTheDocument();
    await waitFor(() => expect(leaves().length).toBe(4));
  });

  it("shows the empty state when the answer holds no figures", async () => {
    stubApi({ ...ANSWER, labels: [], label_links: [], series: [{ name: "spend", values_minor: [] }] });
    render(<TreemapState config={{ title: "Spending by category", query: QUERY }} />);
    await waitFor(() => expect(screen.getByText("Nothing to show yet")).toBeInTheDocument());
  });

  it("shows the server's error rather than a blank chart", async () => {
    stubApi({ error: "no result for 'category-breakdown' yet" }, 404);
    render(<TreemapState config={{ title: "Spending by category", query: QUERY }} />);
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(/no result/));
  });
});
