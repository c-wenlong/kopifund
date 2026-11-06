import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { PieState } from "./PieState";
import type { CategoriesState, WidgetAnswer, WidgetQuery } from "../../../types.gen";
import { json, navigate } from "../../../test-helpers";

vi.mock("../../../router", async (actual) => (await import("../../../test-helpers")).withNavigate(await actual()));

// invented figures only
const QUERY: WidgetQuery = { source: "spending", metric: "spend", dimension: "bucket" };

const ANSWER: WidgetAnswer = {
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

const CATS: CategoriesState = {
  groups: [
    {
      id: 1, name: "Food", kind: "spend", fixed: false, icon: "utensils", colour: null, shown: "c4",
      categories: [{ id: 11, name: "Groceries", need_class: "Need", cost_type: "Variable", group: "Food",
        transactions: 0, monthly: null, budgets: 0, rules: 0, personal_rules: 0 }],
    },
  ],
  need_classes: { spend: [], income: [], transfer: [] },
  months: 0,
};

function stubApi(answer: unknown = ANSWER, status = 200) {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.startsWith("/api/categories")) return json(CATS);
    return json(answer, status);
  }));
}

/** Lets Recharts' ResponsiveContainer see a size in jsdom, so the chart draws. */
function stubSize(width = 640, height = 240) {
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue(
    { width, height, top: 0, left: 0, right: width, bottom: height, x: 0, y: 0, toJSON: () => ({}) } as DOMRect);
}

beforeEach(() => {
  stubApi();
});

describe("PieState", () => {
  it("renders the fixture answer: slices, total in the ring, bucket icon in the legend", async () => {
    stubSize();
    render(<PieState config={{ title: "Spending by bucket", query: QUERY }} />);
    await waitFor(() => expect(document.querySelectorAll(".recharts-pie-sector").length).toBe(3));
    expect(screen.getByText("S$2,500")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Food" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Transport" })).toBeInTheDocument();
    expect(screen.getByText("Spend")).toBeInTheDocument();   // the ring's caption: the bound metric
    // 175000 + 60000 + 15000 cents = S$2,500; Food is a bucket, so the legend carries its icon
    expect(document.querySelector("[data-bucket-icon]")).not.toBeNull();
  });

  it("hides a slice from the legend and never lets the last one hide", async () => {
    stubSize();
    render(<PieState config={{ title: "Spending by bucket", query: QUERY }} />);
    await waitFor(() => expect(screen.getByText("S$2,500")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Transport" }));
    expect(screen.getByText("S$1,900")).toBeInTheDocument();  // 175000 + 15000
    expect(document.querySelectorAll(".recharts-pie-sector").length).toBe(2);
    fireEvent.click(screen.getByRole("button", { name: "Food" }));
    const last = screen.getByRole("button", { name: "Everything else" });
    expect(last).toBeDisabled();
    expect(document.querySelectorAll(".recharts-pie-sector").length).toBe(1);
  });

  it("opens a slice's transactions on click", async () => {
    stubSize();
    render(<PieState config={{ title: "Spending by bucket", query: QUERY }} />);
    await waitFor(() => expect(document.querySelectorAll(".recharts-pie-sector").length).toBe(3));
    fireEvent.click(document.querySelectorAll(".recharts-pie-sector path")[0]);
    expect(navigate).toHaveBeenCalledWith("/transactions?from=2026-10&to=2026-10&spend=1&bucket=Food&home=1");
  });

  it("reads several bound queries as one slice a series", async () => {
    const month = (metric: string, values: (number | null)[]): WidgetAnswer => ({
      source: "needs-wants-savings", metric, dimension: "month", unit: "sgd_minor",
      labels: ["2026-09", "2026-10"], series: [{ name: metric, values_minor: values }], range: null,
    });
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.startsWith("/api/categories")) return json(CATS);
      const q = JSON.parse(decodeURIComponent(url.slice(url.indexOf("=") + 1)));
      const answers: Record<string, WidgetAnswer> = {
        need: month("need", [100_000, 120_000]),
        want: month("want", [80_000, 90_000]),
        savings: month("savings", [50_000, 60_000]),
      };
      return json(answers[q.metric as string]);
    }));
    render(<PieState config={{
      title: "Needs, wants, savings",
      query: [
        { source: "needs-wants-savings", metric: "need", dimension: "month" },
        { source: "needs-wants-savings", metric: "want", dimension: "month" },
        { source: "needs-wants-savings", metric: "savings", dimension: "month" },
      ],
    }} />);
    await waitFor(() => expect(screen.getByText("S$2,700")).toBeInTheDocument());  // 120000 + 90000 + 60000
    expect(screen.getByRole("button", { name: "Need" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Want" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Savings" })).toBeInTheDocument();
    expect(screen.getByText("total")).toBeInTheDocument();
  });

  it("shows the empty state when the answer holds no figures", async () => {
    stubApi({ ...ANSWER, labels: [], label_links: [], series: [{ name: "spend", values_minor: [] }] });
    render(<PieState config={{ title: "Spending by bucket", query: QUERY }} />);
    await waitFor(() => expect(screen.getByText("Nothing to show yet")).toBeInTheDocument());
  });

  it("shows the server's error rather than a blank chart", async () => {
    stubApi({ error: "For spending by bucket, metric is one of spend, income, transfers, net" }, 400);
    render(<PieState config={{ title: "Spending by bucket", query: QUERY }} />);
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(/metric is one of/));
  });

  it("holds a card-shaped placeholder while the answer loads", () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).startsWith("/api/categories")) return json(CATS);
      return new Promise<Response>(() => {});
    }));
    render(<PieState config={{ title: "Spending by bucket", query: QUERY }} />);
    expect(screen.getByText("Loading Spending by bucket")).toBeInTheDocument();
  });
});
