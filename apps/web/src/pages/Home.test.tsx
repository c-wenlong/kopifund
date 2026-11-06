import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { Home, axisSgd, comingUpRows, lastBar } from "./Home";
import { colourFor, stackRows } from "./SpendingExplorer";
import { Sidebar } from "../components/Sidebar";
import type { ComingUpRow, EyeLine, NeedsYourEye, Overview } from "../api";
import { leadingIcon } from "../test-icons";
import { json, navigate, useSizedCharts } from "../test-helpers";

vi.mock("../router", async (actual) => (await import("../test-helpers")).withNavigate(await actual()));

// invented figures
const row = (payee: string, expected: string, status: ComingUpRow["status"], extra: Partial<ComingUpRow> = {}): ComingUpRow => ({
  id: payee, name: payee, payee, link: { q: payee }, cadence: "monthly", category: "Bills", investing: false, amount: 5_000,
  expected, estimated: true, status, paid_on: status === "paid" || status === "different" ? expected : null,
  paid_amount: status === "paid" ? 5_000 : status === "different" ? 8_000 : null, usual_low: 4_750, usual_high: 5_250, ...extra,
});

const calm: NeedsYourEye = { needs_category: 0, recurring_off: [], one_off: null, cash_trend_6m: null, irregular: 0, scam: null,
  upload: null, needs_rule: 0, missing_fx: 0, stale_accounts: [], pending: 0, lines: [] };
// G18: the server writes the lines (finance/home.py, eye_lines); these are its words for invented figures
const CATEGORY: EyeLine = { kind: "needs_category", tone: "warn", text: "3 transactions need a category",
  to: "/transactions?category=(none)&synthetic=0&hide_transfers=1" };
const SCAM: EyeLine = { kind: "scam", tone: "danger", to: "/transactions?flag=scam&hide_transfers=0",
  text: "S$800 to A PAYEE WITH A LONG REGISTERED BUSINESS NAME PTE LTD on 10 Sep 2026 looks like a scam" };
const FLAGGED: EyeLine = { kind: "flagged", tone: "warn", to: "/transactions?flag=scam&hide_transfers=0",
  text: "New monthly payment to STREAM*TV, first charged S$16 on 1 Jul 2026" };
const MORE: EyeLine[] = [
  { kind: "upload", tone: "warn", text: "The last upload was checked but not loaded", to: "/upload" },
  { kind: "recurring_off", tone: "warn", text: "Phone is late", to: "/recurring" },
  { kind: "needs_rule", tone: "warn", text: "2 statement texts need a rule", to: "/rules" },
  { kind: "stale_accounts", tone: "warn", text: "No statement from POSB for 2 months", to: "/profile" },
];

const overview: Overview = {
  as_of: "2026-09-30",
  headline: {
    net_worth_incl_cpf: 4_929_900, net_worth_change: { change: 512_300, since: "2025-09" },
    cash: 1_355_100, cash_change: { change: -42_000, since: "2026-08" },
    runway_months: 6.6, runway_range: [3, 6],
  },
  holdings: [{ label: "Cash", value: 1_355_100 }],
  spending: {
    by: "bucket", scope: "all", bucket_order: ["Food", "Getting around", "Lifestyle"], bucket_style: {},
    months: ["2026-07", "2026-08", "2026-09"],
    series: [
      { name: "Food", total: 150_000, per_month: 50_000, values: [40_000, 50_000, 60_000], link: { bucket: "Food" } },
      { name: "Lifestyle", total: 30_000, per_month: 10_000, values: [10_000, 0, 20_000], link: { bucket: "Lifestyle" } },
    ],
    link: { from: "2026-07", to: "2026-09", spend: "1" },
    totals: { spend: 180_000, per_month: 60_000, overseas: 0 },
  },
  coming_up: {
    month: "2026-09", as_of: "2026-09-30",
    rows: [row("Gym", "2026-09-05", "paid"), row("Phone", "2026-09-12", "late")],
    totals: { committed: 10_000, paid: 5_000, investing: 0, investing_paid: 0, extra: 0, investing_extra: 0 },
  },
  needs_your_eye: { ...calm, needs_category: 3, lines: [CATEGORY] },
  missing_fx: 0,
};

describe("Home tiles", () => {
  it("shows the four headline figures from cents, each with its one-line change", () => {
    render(<Home overview={overview} />);
    const tiles = within(screen.getByRole("region", { name: "Headline figures" }));
    expect(tiles.getByText("S$49,299")).toBeInTheDocument();
    expect(tiles.getByText("+S$5,123 since Sep 2025")).toBeInTheDocument();
    expect(tiles.getByText("S$13,551")).toBeInTheDocument();
    expect(tiles.getByText("\u2212S$420 since Aug 2026")).toBeInTheDocument();
    expect(tiles.getByText("6.6 mo")).toBeInTheDocument();
    expect(tiles.getByText("above the 3 to 6 month range")).toBeInTheDocument();
  });

  it("shows no change under a figure that is not there", () => {
    render(<Home overview={{ ...overview, headline: { ...overview.headline, net_worth_incl_cpf: null, cash: null, runway_months: null } }} />);
    expect(screen.queryByText(/since/)).not.toBeInTheDocument();
    expect(screen.queryByText(/month range/)).not.toBeInTheDocument();
  });

  it("says No data where a figure is missing, and never an en dash", () => {
    const { container } = render(<Home overview={{ ...overview, headline: { ...overview.headline, net_worth_incl_cpf: null, cash: null, runway_months: null } }} />);
    expect(screen.getAllByText("No data")).toHaveLength(3);
    expect(container.textContent).not.toContain("\u2013");
  });

  it("puts the runway against its range", () => {
    const at = (m: number) => {
      const { unmount } = render(<Home overview={{ ...overview, headline: { ...overview.headline, runway_months: m } }} />);
      const text = screen.getByText(/the 3 to 6 month range/).textContent;
      unmount();
      return text;
    };
    expect(at(2.4)).toBe("below the 3 to 6 month range");
    expect(at(4)).toBe("inside the 3 to 6 month range");
  });

  it("leaves a change out where there is none to give", () => {
    render(<Home overview={{ ...overview, headline: { ...overview.headline, cash_change: null, net_worth_change: null, runway_range: null } }} />);
    expect(screen.queryByText(/since/)).not.toBeInTheDocument();
    expect(screen.queryByText(/month range/)).not.toBeInTheDocument();
  });

  it("is the chart's last bar: its month and every bucket in it", () => {
    expect(lastBar(overview.spending)).toEqual({ month: "2026-09", spend: 80_000 });
    render(<Home overview={overview} />);
    const tiles = within(screen.getByRole("region", { name: "Headline figures" }));
    expect(tiles.getByText("Spent in Sep 2026")).toBeInTheDocument();
    expect(tiles.getByText("S$800")).toBeInTheDocument();
    // 800 against the chart's own dashed average of 600, over the months it shows
    expect(tiles.getByText("+S$200 on the 3-month average")).toBeInTheDocument();
  });

  it("follows the chart when it ends on another month", () => {
    const spending = { ...overview.spending, months: ["2026-07", "2026-08"], series: overview.spending.series.map((s) => ({ ...s, values: s.values.slice(0, 2) })) };
    render(<Home overview={{ ...overview, spending }} />);
    expect(screen.getByText("Spent in Aug 2026")).toBeInTheDocument();
    expect(screen.getByText("S$500")).toBeInTheDocument();
  });
});

describe("Home tiles, the figure's own tone", () => {
  it("turns a runway below the range red, and leaves one inside it ink", () => {
    const at = (m: number) => {
      const { unmount } = render(<Home overview={{ ...overview, headline: { ...overview.headline, runway_months: m } }} />);
      const figure = screen.getByText(`${m.toFixed(1)} mo`);
      const cls = figure.className;
      unmount();
      return cls;
    };
    expect(at(2.4)).toContain("text-danger");
    expect(at(4)).not.toContain("text-danger");
    expect(at(6.6)).not.toContain("text-danger");
  });

  it("marks the runway on a track with the range shaded", () => {
    render(<Home overview={{ ...overview, headline: { ...overview.headline, runway_months: 2.4 } }} />);
    expect(screen.getByRole("img", { name: "2.4 months of cash; the 3 to 6 month range is shaded" })).toBeInTheDocument();
  });

  it("draws a sparkline of the last 12 months under net worth and cash, from the net worth history", async () => {
    const months = Array.from({ length: 14 }, (_, i) => ({
      month: `${i < 4 ? 2025 : 2026}-${String(((i + 8) % 12) + 1).padStart(2, "0")}`, total: 1_000_000 + i * 10_000, cash: 500_000 - i * 5_000,
    }));
    vi.stubGlobal("fetch", vi.fn(async () => json({ parts: ["cash"], months })));
    render(<Home overview={overview} />);
    const net = await screen.findByRole("img", { name: /^Net worth, .* to .*/ });
    expect(net.querySelector("polyline")!.getAttribute("points")!.split(" ")).toHaveLength(12);
    expect(screen.getByRole("img", { name: /^Cash, / })).toBeInTheDocument();
  });
});

describe("Home spending chart", () => {
  it("stacks a row per month with each bucket's values", () => {
    expect(stackRows(overview.spending)).toEqual([
      { __month: "2026-07", s0: 40_000, s1: 10_000 },
      { __month: "2026-08", s0: 50_000, s1: 0 },
      { __month: "2026-09", s0: 60_000, s1: 20_000 },
    ]);
  });

  it("gives each bucket its fixed colour, the explorer's", () => {
    expect(colourFor("Lifestyle", 1, overview.spending)).toBe("var(--c3)");
    expect(colourFor("Lifestyle", 1, overview.spending)).toBe(colourFor("Lifestyle", 0, { by: "bucket", bucket_order: overview.spending.bucket_order }));
  });

  it("says so when there is nothing yet", () => {
    render(<Home overview={{ ...overview, spending: { ...overview.spending, months: [], series: [] },
      coming_up: { ...overview.coming_up, rows: [] }, headline: { ...overview.headline, cash: null } }} />);
    expect(screen.getByText("No whole month of spending in the record yet")).toBeInTheDocument();
    expect(screen.getByText("Nothing expected this month")).toBeInTheDocument();
    expect(screen.getByText("Spent last month")).toBeInTheDocument();
  });
});

describe("Home coming up", () => {
  it("lists each payment with its day, amount and status, and links to Recurring", () => {
    render(<Home overview={overview} />);
    const card = within(screen.getByRole("heading", { name: "Coming up in Sep 2026" }).closest("section")!);
    expect(card.getByRole("link", { name: "Gym" })).toHaveAttribute("href", "/transactions?q=Gym");
    expect(card.getByText("Paid")).toBeInTheDocument();
    expect(card.getByText("Late")).toBeInTheDocument();
    // G18: each status leads with its icon, the same one Recurring uses
    expect(leadingIcon(card.getByText("Late"))).toBe("clock-alert");
    expect(card.getByText("~12 Sep 2026")).toBeInTheDocument();
    expect(card.getAllByText("S$50.00")).toHaveLength(2);
    expect(card.getByRole("link", { name: "Recurring" })).toHaveAttribute("href", "/recurring");
  });

  it("keeps the pressing ones when there is no room for all, most pressing first", () => {
    // the server sends them in date order
    const rows = [row("A", "2026-09-01", "paid"), row("B", "2026-09-02", "late"), row("C", "2026-09-03", "paid"),
      row("E", "2026-09-04", "different"), row("D", "2026-09-20", "due")];
    expect(comingUpRows(rows, 3).map((r) => r.payee)).toEqual(["B", "E", "D"]);
    expect(comingUpRows(rows, 5).map((r) => r.payee)).toEqual(["B", "E", "D", "A", "C"]);
  });

  it("lists late, then different amount, then due, then paid, each by date", () => {
    const rows = [row("P1", "2026-09-01", "paid"), row("D2", "2026-09-05", "due"), row("L2", "2026-09-06", "late"),
      row("X1", "2026-09-07", "different"), row("D1", "2026-09-03", "due"), row("L1", "2026-09-02", "late"),
      row("P2", "2026-09-04", "paid")];
    expect(comingUpRows(rows, 7).map((r) => r.payee)).toEqual(["L1", "L2", "X1", "D1", "D2", "P1", "P2"]);
  });

  it("lists an extra charge after a different amount and before what is still to come, G16", () => {
    const rows = [row("D1", "2026-09-03", "due"), row("E1", "2026-09-20", "extra"), row("X1", "2026-09-07", "different"),
      row("P1", "2026-09-01", "paid")];
    expect(comingUpRows(rows, 4).map((r) => r.payee)).toEqual(["X1", "E1", "D1", "P1"]);
  });

  it("shows a late bill from the end of the month among the first four rows", () => {
    const rows = [row("P1", "2026-09-01", "paid"), row("P2", "2026-09-02", "paid"), row("P3", "2026-09-03", "paid"),
      row("P4", "2026-09-04", "paid"), row("Late", "2026-09-25", "late"), row("Due", "2026-09-28", "due")];
    render(<Home overview={{ ...overview, coming_up: { ...overview.coming_up, rows } }} />);
    const card = within(screen.getByRole("heading", { name: "Coming up in Sep 2026" }).closest("section")!);
    const names = card.getAllByRole("link").map((a) => a.textContent).filter((t) => /^(P\d|Late|Due)$/.test(t ?? ""));
    expect(names).toEqual(["Late", "Due", "P1", "P2"]);
    expect(card.queryByRole("link", { name: "P4" })).not.toBeInTheDocument();
  });

  it("opens with four rows and a row that shows the rest", () => {
    const rows = Array.from({ length: 6 }, (_, i) => row(`P${i}`, `2026-09-${String(i + 1).padStart(2, "0")}`, "due"));
    render(<Home overview={{ ...overview, coming_up: { ...overview.coming_up, rows } }} />);
    const card = within(screen.getByRole("heading", { name: "Coming up in Sep 2026" }).closest("section")!);
    expect(card.queryByRole("link", { name: "P4" })).not.toBeInTheDocument();
    fireEvent.click(card.getByRole("button", { name: "Show all 6" }));
    expect(card.getByRole("link", { name: "P5" })).toBeInTheDocument();
  });

  it("names the full list when it is cut", () => {
    const rows = Array.from({ length: 8 }, (_, i) => row(`P${i}`, `2026-09-${String(i + 1).padStart(2, "0")}`, "due"));
    render(<Home overview={{ ...overview, coming_up: { ...overview.coming_up, rows } }} />);
    expect(screen.getByRole("link", { name: "All 8 on Recurring" })).toHaveAttribute("href", "/recurring");
    expect(screen.queryByRole("link", { name: "P7" })).not.toBeInTheDocument();
  });
});

describe("Needs your eye", () => {
  const withLines = (lines: EyeLine[]) => ({ ...overview, needs_your_eye: { ...calm, lines } });
  const card = () => screen.getByRole("heading", { name: "Needs your eye" }).closest("section") as HTMLElement;

  it("shows no card when nothing needs a look", () => {
    render(<Home overview={withLines([])} />);
    expect(screen.queryByText("Needs your eye")).not.toBeInTheDocument();
  });

  it("links each line where the server says, a scam line in the danger colour and wrapped, not cut", () => {
    render(<Home overview={withLines([SCAM, CATEGORY])} />);
    const scam = screen.getByRole("link", { name: /looks like a scam/ });
    expect(scam).toHaveAttribute("href", "/transactions?flag=scam&hide_transfers=0");
    expect(scam.closest("li")).toHaveAttribute("data-tone", "danger");
    const words = within(scam).getByText(SCAM.text);
    expect(words).toHaveClass("text-danger", "break-words");
    expect(words).not.toHaveClass("truncate");
    expect(screen.getByRole("link", { name: /3 transactions need a category/ }).closest("li")).toHaveAttribute("data-tone", "warn");
    // four or fewer: nothing was cut, so there is no "All"
    expect(within(card()).queryByRole("button", { name: /^All/ })).not.toBeInTheDocument();
  });

  it("leads each line with the icon of what it is about, in its tone's colour", () => {
    render(<Home overview={withLines([SCAM, CATEGORY, MORE[2]])} />);
    const line = (text: string) => screen.getByRole("link", { name: text });
    expect(leadingIcon(line(CATEGORY.text))).toBe("tag");
    expect(leadingIcon(line(MORE[2].text))).toBe("sliders-horizontal");
    expect(leadingIcon(line(SCAM.text))).toBe("shield-alert");
    expect(line(SCAM.text).firstElementChild).toHaveClass("text-danger");
    expect(line(CATEGORY.text).firstElementChild).toHaveClass("text-warn");
  });

  it("draws a flag that is no scam in the warning colour, with a flag rather than the scam shield", () => {
    render(<Home overview={withLines([CATEGORY, FLAGGED])} />);
    const flagged = screen.getByRole("link", { name: FLAGGED.text });
    expect(flagged).toHaveAttribute("href", "/transactions?flag=scam&hide_transfers=0");
    expect(flagged.closest("li")).toHaveAttribute("data-tone", "warn");
    expect(leadingIcon(flagged)).toBe("flag");
    expect(flagged.firstElementChild).toHaveClass("text-warn");
    expect(within(flagged).getByText(FLAGGED.text)).not.toHaveClass("text-danger");
    expect(screen.queryByText(/scam/)).not.toBeInTheDocument();
  });

  it("keeps the first four, most urgent first, and All N opens the sidebar's list of every line", () => {
    const lines = [SCAM, CATEGORY, ...MORE];
    render(<><Sidebar path="/" eye={lines} /><Home overview={withLines(lines)} /></>);
    expect(within(card()).getAllByRole("link").map((a) => a.textContent)).toEqual(lines.slice(0, 4).map((l) => l.text));
    fireEvent.click(within(card()).getByRole("button", { name: "All 6" }));
    const list = screen.getByRole("dialog", { name: "Needs your eye" });
    expect(within(list).getAllByRole("link").map((a) => a.getAttribute("href"))).toEqual(lines.map((l) => l.to));
  });
});

describe("Home spending axis", () => {
  it("keeps low ticks in dollars and high ones in thousands", () => {
    expect(axisSgd(20_000)).toBe("S$200");
    expect(axisSgd(50_000)).toBe("S$500");
    expect(axisSgd(150_000)).toBe("S$1.5k");
    expect(axisSgd(400_000)).toBe("S$4k");
    expect(axisSgd(1_200_000)).toBe("S$12k");
    expect(axisSgd(150_000_000)).toBe("S$1.5M");
    expect(axisSgd(200_000_000)).toBe("S$2M");
    expect(axisSgd(-150_000)).toBe("−S$1.5k");
    expect(axisSgd(0)).toBe("S$0");
  });
});

describe("Home spending chart views", () => {
  useSizedCharts();

  const card = () => screen.getByRole("heading", { name: "Spending, last 12 months" }).closest("section")!;
  const bars = () => card().querySelectorAll(".recharts-bar-rectangle").length;
  const lines = () => card().querySelectorAll(".recharts-line-curve").length;

  it("stacks bars by default, with the average", async () => {
    render(<Home overview={overview} />);
    await waitFor(() => expect(bars()).toBe(6));   // two buckets over three months
    expect(lines()).toBe(0);
    expect(within(card()).getByText(/average S\$600/)).toBeInTheDocument();
  });

  it("draws a line a bucket on Lines, drops the average, and remembers it", async () => {
    const first = render(<Home overview={overview} />);
    fireEvent.click(within(card()).getByRole("radio", { name: "Lines" }));
    await waitFor(() => expect(lines()).toBe(2));
    expect(bars()).toBe(0);
    expect(within(card()).queryByText(/average S\$600/)).not.toBeInTheDocument();
    expect(localStorage.getItem("kopifund.chart-view.home-spending")).toBe("lines");
    first.unmount();
    render(<Home overview={overview} />);
    expect(within(card()).getByRole("radio", { name: "Lines" })).toHaveAttribute("aria-checked", "true");
    await waitFor(() => expect(lines()).toBe(2));
  });

  it("opens the rows a bar adds up, with the explorer's own filters: its bucket in that month, or the whole month", async () => {
    render(<Home overview={overview} />);
    await waitFor(() => expect(bars()).toBe(6));
    fireEvent.click(card().querySelectorAll(".recharts-bar-rectangle path")[1]);
    expect(navigate).toHaveBeenLastCalledWith("/transactions?from=2026-07&to=2026-09&spend=1&bucket=Food&month=2026-08");
    fireEvent.click(within(card()).getByRole("radio", { name: "Total" }));
    await waitFor(() => expect(bars()).toBe(3));
    fireEvent.click(card().querySelectorAll(".recharts-bar-rectangle path")[2]);
    expect(navigate).toHaveBeenLastCalledWith("/transactions?from=2026-07&to=2026-09&spend=1&month=2026-09");
  });

  it("draws one bar a month on Total, and a legend entry hides a bucket", async () => {
    render(<Home overview={overview} />);
    fireEvent.click(within(card()).getByRole("radio", { name: "Total" }));
    await waitFor(() => expect(bars()).toBe(3));
    fireEvent.click(within(card()).getByRole("radio", { name: "Stacked" }));
    await waitFor(() => expect(bars()).toBe(6));
    fireEvent.click(within(card()).getByRole("button", { name: "Lifestyle" }));
    await waitFor(() => expect(bars()).toBe(3));
    expect(within(card()).getByRole("button", { name: "Food" })).toBeDisabled();   // the last bucket showing stays
  });
});
