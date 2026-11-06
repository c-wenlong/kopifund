import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { revalidate, type DashboardLayout, type DashboardLayouts, type DashboardWidget } from "../api";
import { Toaster } from "../components/Toast";
import type { CategoriesState, WidgetAnswer, WidgetQuery } from "../types.gen";
import { Dashboard } from "./Dashboard";
import { Grid, WIDGET_FAILED } from "./Grid";
import { DEFAULT_PRESET } from "./presets";
import { WIDGETS, type WidgetDef } from "./registry";
import { json, useSizedCharts } from "../test-helpers";
import { canonicalJson } from "../widgets";
import { H_SMALL } from "../components/chartKit";

// G26: the registry wired end to end: every `type` the picker offers draws its
// real widget through bind.tsx against a stand-in server, a saved layout
// reloads into the same widgets, and a layout saved while the placeholders
// held the ids keeps the question it asked. All figures and names invented.

/** The record's buckets/categories, invented, for the bucket-coloured widgets. */
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

/**
 * An invented WidgetAnswer for whatever a widget asks: months for a month
 * dimension (several series when the query splits), names for anything else.
 * Money in integer cents.
 */
function answerFor(q: WidgetQuery): WidgetAnswer {
  const base = { source: q.source, metric: q.metric, unit: "sgd_minor" as const };
  if (!q.dimension || q.dimension === "month") {
    const labels = ["2026-08", "2026-09"];
    const series = q.split
      ? [
        { name: "Salary", values_minor: [300000, 370000] },
        { name: "Food", values_minor: [-150000, -160000] },
        { name: "Transport", values_minor: [-50000, -55000] },
      ]
      : [{ name: q.metric, values_minor: [1000000, 1234560] }];
    return {
      ...base, dimension: "month", labels, series,
      label_links: labels.map((m) => ({ from: m, to: m })),
      range: { from: "2026-08", to: "2026-09" },
    };
  }
  const names = q.dimension === "category" ? ["Groceries", "Coffee"] : ["Food", "Transport"];
  return {
    ...base, dimension: q.dimension, labels: names,
    series: [{ name: q.metric, values_minor: [175000, 60000] }],
    label_links: names.map((n) => ({ from: "2026-09", to: "2026-09", [q.dimension!]: n })),
    range: { from: "2026-09", to: "2026-09" },
  };
}

let stamp = 0;
function doc(id: string, name: string, widgets: DashboardLayout["widgets"]): DashboardLayout {
  return { id, name, widgets, updated_at: `2026-10-01T02:03:${String(stamp++).padStart(2, "0")}+00:00` };
}

/** The app's whole read surface for the dashboard: layouts, widget queries, categories. */
function stubServer(saved: { layouts: DashboardLayout[]; active: string | null }) {
  const posts: { url: string; body: unknown }[] = [];
  const asked: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    if (url === "/api/dashboard/layouts") {
      if (init?.method === "POST") {
        const body = JSON.parse(String(init.body));
        posts.push({ url, body });
        saved.layouts = body.layouts.map((d: DashboardLayout) => doc(d.id, d.name, d.widgets));
        saved.active = body.active ?? saved.active;
        return json({ layouts: saved.layouts, active: saved.active });
      }
      return json({ layouts: saved.layouts, active: saved.active } satisfies DashboardLayouts);
    }
    const one = url.match(/^\/api\/dashboard\/layouts\/([a-z0-9_-]+)$/);
    if (one && init?.method === "POST") {
      const body = JSON.parse(String(init.body));
      posts.push({ url, body });
      const d = doc(one[1], body.name, body.widgets ?? []);
      saved.layouts = [...saved.layouts.filter((x) => x.id !== d.id), d];
      if (body.active) saved.active = d.id;
      return json(d);
    }
    if (url.startsWith("/api/dashboard/query")) {
      asked.push(url);
      const q = JSON.parse(decodeURIComponent(new URL(url, "http://t").searchParams.get("q") ?? ""));
      return json(answerFor(q));
    }
    if (url.startsWith("/api/categories")) return json(CATS);
    return json({ error: "not here" }, 404);
  });
  return { posts, asked };
}

/** A widget as the picker would add it: the registry's defaults, a fresh cell. */
function spawn(def: WidgetDef, i: number): DashboardWidget {
  const w: DashboardWidget = {
    id: `w-${def.type}`, type: def.type, title: `${def.title} ${i}`,
    layout: { x: 0, y: i * 5, w: def.size.w, h: def.size.h },
  };
  const made = def.make?.();
  if (made?.query) w.query = made.query;
  if (made?.options) w.options = made.options;
  return w;
}

/** Lets Recharts' ResponsiveContainer see a size in jsdom, so the charts draw. */
function stubSize(width = 800, height = 400) {
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue(
    { width, height, top: 0, left: 0, right: width, bottom: height, x: 0, y: 0, toJSON: () => ({}) } as DOMRect);
}

useSizedCharts();
beforeEach(() => stubSize());

describe("registry", () => {
  it("draws every registered kind's real widget on the grid, from its own defaults", async () => {
    stubServer({ layouts: [], active: null });
    const widgets = WIDGETS.map(spawn);
    const { container } = render(<Grid widgets={widgets} interactive={false} onArrange={vi.fn()} onRemove={vi.fn()} />);

    // each card keeps its heading, and none falls back to the unknown-tile card
    for (const w of widgets) {
      expect(await screen.findByRole("heading", { name: w.title! })).toBeInTheDocument();
    }
    expect(container).not.toHaveTextContent("is not one this build knows");

    // kpi: the figure and its change against the point before
    const kpi = await within(screen.getByRole("article", { name: "A figure 0" }))
      .findByText(/\+S\$2,346 vs Aug 26/);
    expect(kpi).toBeInTheDocument();

    // line: one series drawn over the months, announced by name and span
    const line = within(screen.getByRole("article", { name: "A line over months 1" }));
    await waitFor(() => expect(line.getByRole("img").getAttribute("aria-label")).toContain("Aug 2026 to Sep 2026"));

    // area: the composition's series and its Stacked / Lines / Total switch
    const area = within(screen.getByRole("article", { name: "A composition over time 2" }));
    await waitFor(() => expect(area.getByRole("radiogroup", { name: /Chart view/ })).toBeInTheDocument());
    expect(area.getByRole("radio", { name: "Stacked" })).toBeInTheDocument();
    expect(area.getByRole("button", { name: "CPF" })).toBeInTheDocument();

    // waterfall: the bridge between the balance's first and last figures
    const falls = within(screen.getByRole("article", { name: "A bridge between balances 3" }));
    await waitFor(() => expect(falls.getByRole("img").getAttribute("aria-label"))
      .toBe("Waterfall from S$10,000 to S$12,346, 3 steps"));

    // pie and donut: the same answer, two drawings; the ring holds the total
    const pie = within(screen.getByRole("article", { name: "A split 4" }));
    await waitFor(() => expect(pie.getAllByRole("button", { name: "Food" }).length).toBeGreaterThan(0));
    const donut = within(screen.getByRole("article", { name: "A ring 5" }));
    await waitFor(() => expect(donut.getByText("S$2,350")).toBeInTheDocument());

    // treemap: tiles grouped under their buckets
    await waitFor(() => expect(screen.getByRole("article", { name: "A nested split 6" })
      .querySelectorAll("svg rect").length).toBeGreaterThan(0));

    // flow: the Sankey of income into the middle nodes
    const flow = within(screen.getByRole("article", { name: "Money flow 7" }));
    await waitFor(() => expect(flow.getByRole("img").getAttribute("aria-label"))
      .toBe("Money flow: income into buckets and categories"));

    // note: its own words
    expect(within(screen.getByRole("article", { name: "A note 8" }))
      .getByText("A note for this board.")).toBeInTheDocument();
  });

  it("keeps a saved type this build does not know in its cell, saying so", () => {
    stubServer({ layouts: [], active: null });
    const w: DashboardWidget = { id: "w-x", type: "scenario", title: "From a newer app", layout: { x: 0, y: 0, w: 4, h: 2 } };
    render(<Grid widgets={[w]} interactive={false} onArrange={vi.fn()} onRemove={vi.fn()} />);
    expect(screen.getByText(/scenario, is not one this build knows/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "From a newer app" })).toBeInTheDocument();
  });

  it("asks each widget's own query, the one its registry defaults bound", async () => {
    const { asked } = stubServer({ layouts: [], active: null });
    render(<Grid widgets={WIDGETS.map(spawn)} interactive={false} onArrange={vi.fn()} onRemove={vi.fn()} />);
    // key order differs between a saved row and a built query; compare canonically
    const canon = (q: WidgetQuery) => canonicalJson(q);
    await waitFor(() => {
      const queries = asked.map((u) => canon(JSON.parse(decodeURIComponent(new URL(u, "http://t").searchParams.get("q") ?? ""))));
      const askedFor = (q: WidgetQuery) => queries.includes(canon(q));
      // every def's default query, and every query its default options name
      for (const def of WIDGETS) {
        const made = def.make?.();
        if (made?.query) expect(askedFor(made.query)).toBe(true);
        const opts = (made?.options ?? {}) as { series?: { query?: WidgetQuery }[]; flows?: { query?: WidgetQuery }[]; balance?: { query?: WidgetQuery } };
        for (const spec of [...(opts.series ?? []), ...(opts.flows ?? [])]) {
          if (spec.query) expect(askedFor(spec.query)).toBe(true);
        }
        if (opts.balance?.query) expect(askedFor(opts.balance.query)).toBe(true);
      }
      // the flow widget asks its own income / spend / transfers trio
      expect(askedFor({ source: "spending", metric: "income", dimension: "month" })).toBe(true);
      expect(askedFor({ source: "spending", metric: "spend", dimension: "bucket" })).toBe(true);
      expect(askedFor({ source: "spending", metric: "transfers", dimension: "bucket" })).toBe(true);
    }, { timeout: 3000 });
  });
});

describe("registry defaults", () => {
  it("binds a new waterfall to no fixed months, so the bridge keeps following the record", () => {
    // a range written when the widget is added would freeze it on that year;
    // left unset, Waterfall.tsx asks the balance from the month before the
    // flows' own first month, so both ends always match the flows
    const made = WIDGETS.find((d) => d.type === "waterfall")!.make!();
    expect(made.options).not.toHaveProperty("range");
  });

  it("reads a fall in the default board's spend as good, a rise as a warning", () => {
    const tiles = DEFAULT_PRESET.widgets().filter((w) => w.type === "kpi");
    const spend = tiles.find((w) => w.query?.source === "spending")!;
    expect(spend.options?.goodWhen).toBe("down");
    // net worth and runway read a rise as good
    for (const w of tiles.filter((t) => t !== spend)) expect(w.options?.goodWhen).toBe("up");
  });
});

describe("a widget fits its cell", () => {
  // jsdom lays nothing out, so this checks the wiring that does the fitting:
  // every chart takes the room its card leaves ("fill") rather than a fixed
  // height that spills past a short cell or ignores a resize
  it("draws each chart at the card's remaining height, the figure without a box of its own", async () => {
    stubServer({ layouts: [], active: null });
    const widgets = WIDGETS.filter((d) => d.type !== "flow" && d.type !== "note").map(spawn);
    const { container } = render(<Grid widgets={widgets} interactive onArrange={vi.fn()} onRemove={vi.fn()} />);
    const charts = ["A line over months 1", "A composition over time 2", "A bridge between balances 3",
      "A split 4", "A ring 5", "A nested split 6"];
    for (const name of charts) {
      const card = screen.getByRole("article", { name });
      await waitFor(() => expect(card.querySelector(".recharts-responsive-container")).not.toBeNull());
      const box = card.querySelector<HTMLElement>(".recharts-responsive-container")!;
      expect(box.style.height, name).toBe("100%");
      expect(box.parentElement!.className, name).toContain("flex-1");
    }
    // the figure: the grid's card is its box, so no tile inside it
    const kpi = screen.getByRole("article", { name: "A figure 0" });
    await within(kpi).findByText(/vs Aug 26/);
    expect(kpi.querySelector(".bg-tile")).toBeNull();
    expect(container).not.toHaveTextContent(WIDGET_FAILED);
  });

  it("draws a stacked card's chart at a phone's height, the card growing to hold it", async () => {
    stubServer({ layouts: [], active: null });
    const w: DashboardWidget = { id: "w-1", type: "line", title: "Spend", layout: { x: 0, y: 0, w: 8, h: 3 } };
    render(<Grid widgets={[w]} interactive={false} onArrange={vi.fn()} onRemove={vi.fn()} />);
    const card = screen.getByRole("article", { name: "Spend" });
    // a stacked card has no fixed height to fill: "fill" there would collapse the chart
    await waitFor(() => expect(card.querySelector(".recharts-responsive-container")).not.toBeNull());
    expect(card.querySelector<HTMLElement>(".recharts-responsive-container")!.style.height).toBe(`${H_SMALL}px`);
    expect(card.style.height).toBe("");
  });
});

describe("a widget whose binding changes its series count", () => {
  // useAnswers asks one hook per series: drawn outside the grid's boundary, a
  // widget kept mounted across the change would throw on the new hook count
  const month = (metric: string) => ({ query: { source: "spending", metric, dimension: "month" } as WidgetQuery });
  const cash = { query: { source: "savings-runway", metric: "cash", dimension: "month" } as WidgetQuery };
  const cases: [string, Record<string, unknown>, Record<string, unknown>][] = [
    ["line", { series: [month("spend")] }, { series: [month("spend"), month("income")] }],
    ["area", { series: [month("spend")] }, { series: [month("spend"), month("income"), month("net")] }],
    ["waterfall", { balance: cash, flows: [month("net")] }, { balance: cash, flows: [month("income"), month("spend")] }],
  ];
  it.each(cases)("%s redraws with the new count instead of throwing", async (type, before, after) => {
    stubServer({ layouts: [], active: null });
    const Body = WIDGETS.find((d) => d.type === type)!.component;
    const row = (options: Record<string, unknown>): DashboardWidget =>
      ({ id: "w-1", type, title: "Bound", layout: { x: 0, y: 0, w: 8, h: 3 }, options });
    const { container, rerender } = render(<Body widget={row(before)} />);
    await waitFor(() => expect(container.querySelector("[role=img]")).not.toBeNull());
    rerender(<Body widget={row(after)} />);
    await waitFor(() => expect(container.querySelector("[role=img]")).not.toBeNull());
    expect(container).not.toHaveTextContent(WIDGET_FAILED);
  });
});

describe("Dashboard with the real widgets", () => {
  it("offers every registered kind in the picker, and the one added saves its binding", async () => {
    const saved = {
      layouts: [doc("default", "Default", [
        { id: "w-note", type: "note", title: "A note", options: { text: "kept" }, layout: { x: 0, y: 0, w: 4, h: 2 } },
      ])],
      active: "default",
    };
    const { posts } = stubServer(saved);
    render(<><Dashboard /><Toaster /></>);
    await screen.findByText("kept");
    fireEvent.click(screen.getByRole("button", { name: /Add widget/ }));
    const sheet = await screen.findByRole("dialog", { name: "Add a widget" });
    // one entry per registered kind, plus the sheet's own close button
    expect(within(sheet).getAllByRole("button")).toHaveLength(WIDGETS.length + 1);
    for (const def of WIDGETS) {
      expect(within(sheet).getByRole("button", { name: new RegExp(`^${def.title}`) })).toBeInTheDocument();
    }
    fireEvent.click(within(sheet).getByRole("button", { name: /^A ring/ }));
    await waitFor(() => expect(saved.layouts[0].widgets).toHaveLength(2), { timeout: 3000 });
    const added = saved.layouts[0].widgets.find((w) => w.type === "donut")!;
    expect(added.query).toEqual({ source: "spending", metric: "spend", dimension: "bucket" });
    expect(added.options).toEqual({ donut: true, showTotal: true });
    // and the ring draws with the whole's figure in the middle
    await waitFor(() => expect(screen.getByText("S$2,350")).toBeInTheDocument());
    expect(posts.at(-1)?.url).toBe("/api/dashboard/layouts/default");
  });

  it("reloads a saved layout into the same real widgets, options intact", async () => {
    const saved = {
      layouts: [doc("default", "Default", [
        { id: "w-donut", type: "donut", title: "Where it went", layout: { x: 0, y: 0, w: 4, h: 3 },
          query: { source: "spending", metric: "spend", dimension: "bucket" },
          options: { donut: true, showTotal: true } },
        { id: "w-fall", type: "waterfall", title: "The bridge", layout: { x: 4, y: 0, w: 8, h: 3 },
          options: {
            balance: { query: { source: "savings-runway", metric: "cash", dimension: "month" } },
            flows: [{ query: { source: "spending", metric: "net", dimension: "month", split: "bucket" } }],
          } },
      ])],
      active: "default",
    };
    stubServer(saved);
    const first = render(<Dashboard />);
    // the saved options draw: the ring shows its total, the bridge its steps
    await waitFor(() => expect(screen.getByText("S$2,350")).toBeInTheDocument());
    await waitFor(() => expect(first.container.querySelector("[role=img][aria-label^='Waterfall from']")).not.toBeNull());
    first.unmount();

    // a second open, as after a reload, draws the same widgets from the same saved document
    const again = render(<Dashboard />);
    await waitFor(() => expect(screen.getByText("S$2,350")).toBeInTheDocument());
    await waitFor(() => expect(again.container.querySelector("[role=img][aria-label^='Waterfall from']")).not.toBeNull());
    expect(screen.getByRole("heading", { name: "Where it went" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "The bridge" })).toBeInTheDocument();
  });

  it("reads a layout saved while placeholders held the ids: same queries, real charts", async () => {
    // what the placeholder build wrote: a `query` and no options
    const saved = {
      layouts: [doc("default", "Default", [
        { id: "w-kpi", type: "kpi", title: "Net worth", layout: { x: 0, y: 0, w: 4, h: 2 },
          query: { source: "net-worth", metric: "total", dimension: "month" } },
        { id: "w-line", type: "line", title: "Monthly spend", layout: { x: 0, y: 2, w: 8, h: 3 },
          query: { source: "spending", metric: "spend", dimension: "month" } },
        { id: "w-pie", type: "pie", title: "Spend by bucket", layout: { x: 8, y: 2, w: 4, h: 3 },
          query: { source: "spending", metric: "spend", dimension: "bucket" } },
      ])],
      active: "default",
    };
    const { asked } = stubServer(saved);
    const { container } = render(<Dashboard />);
    // the saved questions still asked, now answered by the real widgets
    await waitFor(() => expect(screen.getByText(/\+S\$2,346/)).toBeInTheDocument());
    await waitFor(() => expect(container.querySelector("[role=img][aria-label*='Aug 2026 to Sep 2026']")).not.toBeNull());
    await waitFor(() => expect(screen.getAllByRole("button", { name: "Food" }).length).toBeGreaterThan(0));
    expect(asked.some((u) => u.includes(encodeURIComponent('"source":"net-worth"')))).toBe(true);
    act(() => revalidate());
    // nothing placeholder-shaped remains: no "No question is bound" stand-in text
    expect(container).not.toHaveTextContent("No question is bound");
  });
});
