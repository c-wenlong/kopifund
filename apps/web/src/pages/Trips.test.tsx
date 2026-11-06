import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Trip } from "../api";
import { Toaster } from "../components/Toast";
import { averagePerDay, bubblesOf, costPerDay, dayOfYear, groupByYear, isDear, isPlanned, summarise, Trips } from "./Trips";
import { leadingIcon } from "../test-icons";
import { json, navigate, SizedObserver, stubMatchMedia, useSizedCharts } from "../test-helpers";

// G18: the Sheet the trip form opens in uses the router's unsaved guard, so only the moves are stood in for
vi.mock("../router", async (actual) => {
  const { navigate, withNavigate } = await import("../test-helpers");
  return { ...withNavigate(await actual()),
    onLinkClick: (to: string) => (e: { preventDefault: () => void }) => { e.preventDefault(); navigate(to); },
  };
});

const MY = "\u{1F1F2}\u{1F1FE}";
const SE = "\u{1F1F8}\u{1F1EA}";

// G17: every trip the server lists carries its id, which a save and a remove send back
let nextId = 1;
function trip(name: string, per_day: number, extra: Partial<Trip> = {}): Trip {
  return { id: nextId++, name, start: "2026-08-01", end: "2026-08-05", countries: ["MYS"], flags: [MY], notes: "", extra_keys: [],
    spend_sgd: per_day * 5, rows: 3, days: 5, per_day, long_stay: false, top_categories: [], ...extra };
}

const JP = "\u{1F1EF}\u{1F1F5}";
const COUNTRIES = { countries: [
  { code: "JPN", alpha2: "JP", flag: JP },
  { code: "MYS", alpha2: "MY", flag: MY },
  { code: "SWE", alpha2: "SE", flag: SE },
] };

// the record ends 20 Sep 2026, so a trip that starts in December has not happened yet
const trips = { long_stay_days: 60, last_txn: "2026-09-20", trips: [
  trip("Trip A", 2_400, { extra_keys: ["k9"], countries: ["MYS", "XXX"], flags: [MY, ""],
    top_categories: [{ name: "Eating out", spend: 8_000 }, { name: "Transport", spend: 4_000 }] }),
  trip("Term abroad", 6_400, { start: "2025-08-01", end: "2026-06-13", days: 317, long_stay: true, countries: ["SWE"], flags: [SE] }),
  trip("Trip B", 9_000),
  trip("Planned break", 0, { start: "2026-12-01", end: "2026-12-04", days: 4, rows: 0, spend_sgd: 0, per_day: 0 }),
] };

function stubFetch() {
  const calls: { url: string; body?: string }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, body: init?.body as string | undefined });
    const body = url.startsWith("/api/trips") ? trips
      : url === "/api/countries" ? COUNTRIES
      : url === "/api/results/travel" ? { id: "travel", title: "Trips", findings: ["Leaving out the stays over 60 days, two trips."], data: {} }
      : { results: [] };
    // G13: a save moves its rows at once, says how many, and answers with what its Undo sends back
    const a = trips.trips[0].id;
    const posted = url === "/api/trips/remove" ? { removed: "Trip A", id: a, restore: { trip: { id: a, name: "Trip A" }, txn_keys: ["k1"] } }
      : url === "/api/trips" ? { id: a, name: "Trip A", relinked: { changed: 14, written: 14 }, undo: { after: { id: a, name: "Trip A" } } }
      : { name: "Trip A", relinked: { changed: 14, written: 14 } };
    return json(init?.method === "POST" ? posted : body);
  }));
  return calls;
}

describe("Trips", () => {
  useSizedCharts();

  it("edits a trip and keeps its pinned rows", async () => {
    const calls = stubFetch();
    render(<Trips />);
    fireEvent.click(await screen.findByRole("button", { name: "Edit Trip A" }));
    // XXX is not a country: the form says so before Save and holds Save back until it is removed
    expect(await screen.findByText(/XXX is not an ISO country code/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save trip" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Remove XXX" }));
    expect(screen.queryByText(/not an ISO country code/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Save trip" }));
    await waitFor(() => expect(calls.some((c) => c.url === "/api/trips" && c.body)).toBe(true));
    const sent = JSON.parse(calls.find((c) => c.url === "/api/trips" && c.body)!.body!);
    expect(sent).toMatchObject({ id: trips.trips[0].id, old_name: "Trip A", name: "Trip A", countries: ["MYS"], extra_keys: ["k9"] });
    expect(navigate).not.toHaveBeenCalled();
  });

  it("says how many rows a save moved, and Undo takes the trip and its rows back", async () => {
    const calls = stubFetch();
    render(<><Trips /><Toaster /></>);
    fireEvent.click(await screen.findByRole("button", { name: "Edit Trip A" }));
    fireEvent.click(await screen.findByRole("button", { name: "Remove XXX" }));
    fireEvent.click(screen.getByRole("button", { name: "Save trip" }));
    const toast = await screen.findByRole("status");
    expect(toast).toHaveTextContent("Trip saved, 14 rows changed.");
    fireEvent.click(within(toast).getByRole("button", { name: "Undo" }));
    expect(await screen.findByText("Trip save undone, 14 rows changed back.")).toBeInTheDocument();
    expect(JSON.parse(calls.find((c) => c.url === "/api/trips/undo")!.body!)).toEqual({ undo: { after: { id: trips.trips[0].id, name: "Trip A" } } });
  });

  it("picks a trip's two days in one calendar, a day trip until the end is chosen, and sends ISO", async () => {
    const calls = stubFetch();
    render(<Trips />);
    fireEvent.click(await screen.findByRole("button", { name: "Add a trip" }));
    await waitFor(() => expect(screen.getByRole("combobox", { name: "Add a country" })).toBeEnabled());
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Invented weekend" } });
    expect(screen.getByLabelText("First day")).toHaveValue("");
    fireEvent.click(screen.getByLabelText("First day"));
    // typing a start jumps the calendar to its month; picking a day there is the start
    fireEvent.change(screen.getByLabelText("First day"), { target: { value: "2 Jan 2027" } });
    // G18: the form is a dialog of its own now, so the calendar is found by its name
    const cal = () => within(screen.getByRole("dialog", { name: /calendar/ }));
    expect(cal().getByText("January 2027")).toBeInTheDocument();
    fireEvent.click(cal().getByRole("button", { name: /January 8th, 2027/ }));
    expect(screen.getByLabelText("First day")).toHaveValue("8 Jan 2027");
    expect(screen.getByLabelText("Last day")).toHaveValue("8 Jan 2027");
    expect(screen.getByRole("dialog", { name: /calendar/ })).toBeInTheDocument();
    fireEvent.click(cal().getByRole("button", { name: /January 10th, 2027/ }));
    expect(screen.queryByRole("dialog", { name: /calendar/ })).toBeNull();
    expect(screen.getByLabelText("Last day")).toHaveValue("10 Jan 2027");
    fireEvent.click(screen.getByRole("button", { name: "Save trip" }));
    await waitFor(() => expect(calls.some((c) => c.url === "/api/trips" && c.body)).toBe(true));
    expect(JSON.parse(calls.find((c) => c.url === "/api/trips" && c.body)!.body!)).toMatchObject({ start: "2027-01-08", end: "2027-01-10" });
  });

  it("saves a day trip when only the first day is picked, and asks for it when it is not", async () => {
    const calls = stubFetch();
    render(<Trips />);
    fireEvent.click(await screen.findByRole("button", { name: "Add a trip" }));
    await waitFor(() => expect(screen.getByRole("combobox", { name: "Add a country" })).toBeEnabled());
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Invented day out" } });
    // text that is not a day leaves the first day empty, whatever the box shows
    fireEvent.change(screen.getByLabelText("First day"), { target: { value: "soon" } });
    fireEvent.click(screen.getByRole("button", { name: "Save trip" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Pick the first day");
    expect(calls.some((c) => c.url === "/api/trips" && c.body)).toBe(false);
    fireEvent.change(screen.getByLabelText("First day"), { target: { value: "2027-03-04" } });
    fireEvent.click(screen.getByRole("button", { name: "Save trip" }));
    await waitFor(() => expect(calls.some((c) => c.url === "/api/trips" && c.body)).toBe(true));
    expect(JSON.parse(calls.find((c) => c.url === "/api/trips" && c.body)!.body!)).toMatchObject({ start: "2027-03-04", end: "2027-03-04" });
  });

  it("opens an existing trip's days in the app's format", async () => {
    stubFetch();
    render(<Trips />);
    fireEvent.click(await screen.findByRole("button", { name: "Edit Trip B" }));
    expect(screen.getByLabelText("First day")).toHaveValue("1 Aug 2026");
    expect(screen.getByLabelText("Last day")).toHaveValue("5 Aug 2026");
  });

  it("removes a trip without opening it, and Undo puts it back", async () => {
    const calls = stubFetch();
    render(<><Trips /><Toaster /></>);
    fireEvent.click(await screen.findByRole("button", { name: "Remove Trip A" }));
    await waitFor(() => expect(calls.some((c) => c.url === "/api/trips/remove")).toBe(true));
    expect(JSON.parse(calls.find((c) => c.url === "/api/trips/remove")!.body!)).toEqual({ id: trips.trips[0].id, name: "Trip A" });
    expect(navigate).not.toHaveBeenCalled();
    const toast = await screen.findByRole("status");
    expect(toast).toHaveTextContent("Removed Trip A, 1 row changed.");
    fireEvent.click(within(toast).getByRole("button", { name: "Undo" }));
    await waitFor(() => expect(calls.some((c) => c.url === "/api/trips/restore")).toBe(true));
    expect(JSON.parse(calls.find((c) => c.url === "/api/trips/restore")!.body!))
      .toEqual({ trip: { id: trips.trips[0].id, name: "Trip A" }, txn_keys: ["k1"] });
  });

  it("shows each country as a flag with its code on hover, and an unknown code as itself", async () => {
    stubFetch();
    render(<Trips />);
    const card = (await screen.findByRole("link", { name: "Trip A" })).closest("li")!;
    const flag = within(card).getByRole("img", { name: "MYS" });
    expect(flag).toHaveTextContent(MY);
    expect(flag).toHaveAttribute("title", "MYS");
    expect(within(card).getByTitle("XXX")).toHaveTextContent("XXX");
  });

  it("opens a trip's spending from its name or anywhere on its card", async () => {
    stubFetch();
    render(<Trips />);
    const link = await screen.findByRole("link", { name: "Trip B" });
    expect(link).toHaveAttribute("href", "/transactions?trip=Trip+B&spend=1");
    fireEvent.click(link);
    expect(navigate).toHaveBeenLastCalledWith("/transactions?trip=Trip+B&spend=1");
    const card = screen.getByRole("link", { name: "Trip A" }).closest("li")!;
    fireEvent.click(within(card).getByText("S$120"));
    expect(navigate).toHaveBeenLastCalledWith("/transactions?trip=Trip+A&spend=1");
  });

  it("draws short trips by cost per day, dearest first, and lists long stays apart", async () => {
    stubFetch();
    const { container } = render(<Trips />);
    await screen.findByText("Cost per day");
    await waitFor(() => expect(container.querySelectorAll(".recharts-bar-rectangle path")).toHaveLength(2));
    const ticks = [...container.querySelectorAll(".recharts-yAxis .recharts-cartesian-axis-tick-value")].map((t) => t.textContent);
    expect(ticks).toEqual([`${MY} Trip B`, `${MY} Trip A`]);
    const long = screen.getByRole("list", { name: "Long stays" });
    expect(within(long).getByRole("link")).toHaveAttribute("href", "/transactions?trip=Term+abroad&spend=1");
    expect(long).toHaveTextContent("317 days");
    expect(long).toHaveTextContent("S$320");
    expect(long).toHaveTextContent("S$64 a day");
  });

  it("opens nothing from a cost-per-day bar, which is spend over days and not a list of rows", async () => {
    stubFetch();
    const { container } = render(<Trips />);
    await screen.findByText("Cost per day");
    await waitFor(() => expect(container.querySelectorAll(".recharts-bar-rectangle path")).toHaveLength(2));
    navigate.mockReset();
    fireEvent.click(container.querySelectorAll(".recharts-bar-rectangle path")[0]);
    expect(navigate).not.toHaveBeenCalled();
    expect(container.querySelector("[cursor=pointer]")).toBeNull();
  });

  it("puts the trip's dates and transactions in the hover card, where its Details table had them", async () => {
    stubFetch();
    const { container } = render(<Trips />);
    await screen.findByText("Cost per day");
    await waitFor(() => expect(container.querySelectorAll(".recharts-bar-rectangle path")).toHaveLength(2));
    const wrapper = container.querySelector(".recharts-wrapper")!;
    fireEvent.mouseMove(wrapper, { clientX: 400, clientY: 20 });
    const tip = await waitFor(() => {
      const t = container.querySelector(".bg-tip");
      if (!t) throw new Error("no hover card yet");
      return t as HTMLElement;
    });
    expect(within(tip).getByText("Dates").parentElement).toHaveTextContent("1 Aug to 5 Aug 2026");
    expect(within(tip).getByText("Transactions").parentElement).toHaveTextContent("3");
    // G18: the chart kit's box, so a long trip name wraps inside a phone screen instead of running off it
    expect(tip.className).toContain("max-w-[min(22rem,calc(100vw-2rem))]");
  });

  it("has no Details toggle on the cost per day card and fetches no stat for it", async () => {
    const calls = stubFetch();
    render(<Trips />);
    await screen.findByText("Cost per day");
    expect(screen.queryByRole("button", { name: /details/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/Leaving out the stays/)).not.toBeInTheDocument();
    expect(calls.some((c) => c.url === "/api/results/travel")).toBe(false);
  });
});

describe("costPerDay", () => {
  it("sorts short trips by day rate and splits off the long stays", () => {
    const { short, long } = costPerDay([
      trip("Cheap", 1_000), trip("Dear", 20_000), trip("Lived there", 5_000, { long_stay: true }),
      trip("Also cheap", 1_000), trip("Unpriced", 0, { per_day: null }),
    ]);
    expect(short.map((t) => t.name)).toEqual(["Dear", "Also cheap", "Cheap"]);
    expect(long.map((t) => t.name)).toEqual(["Lived there"]);
  });
});

describe("groupByYear", () => {
  it("groups by the year a trip starts, newest year first and newest trip first inside it", () => {
    const groups = groupByYear([
      trip("Old", 1_000, { start: "2024-05-01" }), trip("Spring", 1_000, { start: "2026-03-01" }),
      trip("Winter", 2_000, { start: "2026-12-20" }), trip("Last year", 1_000, { start: "2025-07-01" }),
    ]);
    expect(groups.map((g) => [g.year, g.trips.map((t) => t.name)])).toEqual([
      ["2026", ["Winter", "Spring"]], ["2025", ["Last year"]], ["2024", ["Old"]],
    ]);
    expect(groups[0].total).toBe(15_000);
  });
});

describe("Trips cards", () => {
  useSizedCharts();

  // two years of trips, three in the newest
  const spread = { long_stay_days: 60, last_txn: "2026-09-20", trips: [
    trip("Spring", 1_000, { start: "2026-03-01", end: "2026-03-03", days: 3, spend_sgd: 3_000,
      top_categories: [{ name: "Eating out", spend: 2_000 }, { name: "Transport", spend: 700 }, { name: "Gifts", spend: 300 }] }),
    trip("Summer", 2_000, { start: "2026-07-01", end: "2026-07-02", days: 2, spend_sgd: 4_000 }),
    trip("Autumn", 3_000, { start: "2026-09-10", end: "2026-09-10", days: 1, spend_sgd: 3_000 }),
    trip("Earlier A", 1_500, { start: "2025-06-01" }), trip("Earlier B", 2_500, { start: "2025-02-01" }),
  ] };
  const stubSpread = () => vi.stubGlobal("fetch", vi.fn(async (url: string) => json(
    url.startsWith("/api/trips") ? spread : url === "/api/results/travel" ? { id: "travel", title: "Trips", findings: [], data: {} } : { results: [] })));

  it("groups cards under a year header with its trip count and total, newest year first", async () => {
    stubSpread();
    render(<Trips />);
    await screen.findByRole("link", { name: "Autumn" });
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    const card = within(screen.getByRole("heading", { name: "2026" }).closest("li")!);
    expect(card.getByText("3 trips · S$100")).toBeInTheDocument();
  });

  it("shows the newest year's cards and a Show all N trips row that opens the older years", async () => {
    stubSpread();
    render(<Trips />);
    await screen.findByRole("link", { name: "Autumn" });
    expect(screen.getAllByRole("heading", { level: 3 }).map((h) => h.textContent)).toEqual(["2026"]);
    expect(["Spring", "Summer", "Autumn"].every((n) => screen.queryByRole("link", { name: n }))).toBe(true);
    expect(screen.queryByRole("link", { name: "Earlier A" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Show all 5 trips" }));
    expect(screen.getAllByRole("heading", { level: 3 }).map((h) => h.textContent)).toEqual(["2026", "2025"]);
    expect(screen.getByRole("link", { name: "Earlier B" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Show fewer" }));
    expect(screen.queryByRole("link", { name: "Earlier B" })).not.toBeInTheDocument();
  });

  it("has nothing to fold when every trip is in one year", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => json(
      url.startsWith("/api/trips") ? { long_stay_days: 60, trips: [trip("One", 1_000), trip("Two", 2_000)] } : { results: [] })));
    render(<Trips />);
    await screen.findByRole("link", { name: "One" });
    expect(screen.queryByRole("button", { name: /Show all/ })).not.toBeInTheDocument();
  });

  it("shows flags, name, dates and days, spent, a day and the top categories on a card", async () => {
    stubSpread();
    render(<Trips />);
    const card = within((await screen.findByRole("link", { name: "Spring" })).closest("li")!);
    expect(card.getByRole("img", { name: "MYS" })).toBeInTheDocument();
    expect(card.getByText("1 Mar to 3 Mar 2026 · 3 days")).toBeInTheDocument();
    expect(card.getByText("S$30")).toBeInTheDocument();
    expect(card.getByText("S$10 a day")).toBeInTheDocument();
    const cats = within(card.getByRole("list", { name: "Spring top categories" }));
    expect(cats.getAllByRole("listitem").map((li) => li.textContent)).toEqual(["Eating outS$20", "TransportS$7", "GiftsS$3"]);
  });

  it("marks a long stay and a trip that starts after the record ends, and leaves the others plain", async () => {
    stubFetch();
    render(<Trips />);
    const card = (name: string) => within(screen.getByRole("link", { name }).closest("li")!);
    fireEvent.click(await screen.findByRole("button", { name: "Show all 4 trips" }));
    expect(card("Term abroad").getByText("Long stay")).toBeInTheDocument();
    expect(card("Term abroad").queryByText("Planned")).not.toBeInTheDocument();
    expect(card("Planned break").getByText("Planned")).toBeInTheDocument();
    // a planned trip has spent nothing: no figures to show
    expect(card("Planned break").queryByText("S$0")).not.toBeInTheDocument();
    expect(card("Trip A").queryByText(/Long stay|Planned/)).not.toBeInTheDocument();
    // G18: each label leads with its icon
    expect(leadingIcon(card("Term abroad").getByText("Long stay"))).toBe("map-pin-house");
    expect(leadingIcon(card("Planned break").getByText("Planned"))).toBe("tickets-plane");
  });

  it("shows the spending of a planned trip that has payments linked to it, since the totals count it", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => json(
      url.startsWith("/api/trips") ? { long_stay_days: 60, last_txn: "2026-09-20", trips: [
        trip("Moved ahead", 2_000, { start: "2026-12-01", end: "2026-12-03", days: 3, spend_sgd: 6_000, rows: 2,
          top_categories: [{ name: "Eating out", spend: 6_000 }] }),
        trip("Nothing yet", 0, { start: "2026-12-10", end: "2026-12-12", days: 3, spend_sgd: 0, rows: 0, per_day: 0 }),
      ] } : { results: [] })));
    render(<Trips />);
    const linked = within((await screen.findByRole("link", { name: "Moved ahead" })).closest("li")!);
    expect(linked.getByText("Planned")).toBeInTheDocument();
    expect(linked.getAllByText("S$60")).toHaveLength(2);   // spent, and the one category
    expect(linked.getByText("S$20 a day")).toBeInTheDocument();
    expect(linked.getByRole("list", { name: "Moved ahead top categories" })).toHaveTextContent("Eating outS$60");
    const empty = within(screen.getByRole("link", { name: "Nothing yet" }).closest("li")!);
    expect(empty.queryByText(/S\$/)).not.toBeInTheDocument();
  });

  it("keeps edit and remove on the card, and the Undo toast after a remove", async () => {
    const calls = stubFetch();
    render(<><Trips /><Toaster /></>);
    const card = within((await screen.findByRole("link", { name: "Trip A" })).closest("li")!);
    expect(card.getByRole("button", { name: "Edit Trip A" })).toBeInTheDocument();
    fireEvent.click(card.getByRole("button", { name: "Remove Trip A" }));
    await waitFor(() => expect(calls.some((c) => c.url === "/api/trips/remove")).toBe(true));
    expect(navigate).not.toHaveBeenCalled();
    expect(within(await screen.findByRole("status")).getByRole("button", { name: "Undo" })).toBeInTheDocument();
  });

  it("makes the buttons big enough to touch on a phone", async () => {
    stubMatchMedia(false);
    stubFetch();
    render(<Trips />);
    const edit = await screen.findByRole("button", { name: "Edit Trip A" });
    expect(edit).toHaveClass("size-10", "md:size-8");
    expect(screen.getByRole("button", { name: "Remove Trip A" })).toHaveClass("size-10");
  });
});

describe("Trips, friendlier than a table", () => {
  beforeEach(() => {
    vi.stubGlobal("ResizeObserver", SizedObserver);
    vi.useFakeTimers({ toFake: ["Date"], now: new Date(2026, 9, 17) });
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("opens with tiles: how many trips, what they cost, the day rate and this year against last", async () => {
    stubFetch();
    render(<Trips />);
    const tiles = within(await screen.findByRole("group", { name: "Trip totals" }));
    const tile = (label: string) => tiles.getByText(label).parentElement!;
    expect(tile("Trips")).toHaveTextContent("4");
    expect(tile("Trips")).toHaveTextContent("1 planned, 1 long stay");
    expect(tile("Spent on trips")).toHaveTextContent("S$890");
    // all their spending over all their days, short trips only: (S$120 + S$450) over 10 days
    expect(tile("Average a day")).toHaveTextContent("S$57");
    expect(tile("Average a day")).toHaveTextContent("over 2 short trips");
    expect(tile("Trips in 2026")).toHaveTextContent("S$570");
    expect(tile("Trips in 2026")).toHaveTextContent("S$320 in 2025");
  });

  it("draws an average line, with bars above it in the warn token and the rest in the series colour", async () => {
    stubFetch();
    const { container } = render(<Trips />);
    await screen.findByText("Cost per day");
    await waitFor(() => expect(container.querySelectorAll(".recharts-bar-rectangle path")).toHaveLength(2));
    // dearest first: Trip B is over the S$57 average, Trip A under it
    const fills = [...container.querySelectorAll(".recharts-bar-rectangle path")].map((p) => p.getAttribute("fill"));
    expect(fills).toEqual(["var(--warn)", "var(--c1)"]);
    expect(container.querySelectorAll(".recharts-reference-line line")).toHaveLength(1);
    const key = within(screen.getByRole("list", { name: "Chart key" }));
    expect(key.getByText("25% or more above the average")).toBeInTheDocument();
    expect(key.getByText("Less than that")).toBeInTheDocument();
    expect(key.getByText("Average S$57 a day")).toBeInTheDocument();
    // a trip still to come has spent nothing and is not a bar
    expect(container.querySelector(".recharts-yAxis")).not.toHaveTextContent("Planned break");
  });

  it("marks only trips at least a quarter above the average, not every bar over the line", async () => {
    // one-day trips at S$10, S$10, S$13 and S$15: the average is S$12, a quarter above it is S$15
    const day = (name: string, cents: number) => trip(name, cents, { days: 1, spend_sgd: cents, start: "2026-03-01", end: "2026-03-01" });
    vi.stubGlobal("fetch", vi.fn(async (url: string) => json(
      url.startsWith("/api/trips") ? { long_stay_days: 60, last_txn: "2026-09-20",
        trips: [day("Cheap one", 1_000), day("Cheap two", 1_000), day("Over the line", 1_300), day("Well over", 1_500)] }
        : { results: [] })));
    const { container } = render(<Trips />);
    await waitFor(() => expect(container.querySelectorAll(".recharts-bar-rectangle path")).toHaveLength(4));
    const rows = [...container.querySelectorAll(".recharts-yAxis .recharts-cartesian-axis-tick-value")].map((t) => t.textContent!.replace(/^\S+ /, ""));
    const fills = [...container.querySelectorAll(".recharts-bar-rectangle path")].map((p) => p.getAttribute("fill"));
    // dearest first: exactly a quarter over is marked, over the average but short of a quarter is not
    expect(rows).toEqual(["Well over", "Over the line", "Cheap one", "Cheap two"]);
    expect(fills).toEqual(["var(--warn)", "var(--c1)", "var(--c1)", "var(--c1)"]);
    expect(screen.getByText("25% or more above the average")).toBeInTheDocument();
  });

  it("draws no average line for a single short trip", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => json(
      url.startsWith("/api/trips") ? { long_stay_days: 60, last_txn: "2026-09-20", trips: [trip("Only trip", 3_000)] } : { results: [] })));
    const { container } = render(<Trips />);
    await waitFor(() => expect(container.querySelectorAll(".recharts-bar-rectangle path")).toHaveLength(1));
    expect(container.querySelectorAll(".recharts-reference-line")).toHaveLength(0);
    expect(screen.queryByRole("list", { name: "Chart key" })).not.toBeInTheDocument();
  });

  it("picks countries by name with flags and sends their alpha-3 codes", async () => {
    const calls = stubFetch();
    render(<Trips />);
    fireEvent.click(await screen.findByRole("button", { name: "Add a trip" }));
    const box = screen.getByRole("combobox", { name: "Add a country" });
    await waitFor(() => expect(box).toBeEnabled());
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Invented trip" } });
    fireEvent.change(screen.getByLabelText("First day"), { target: { value: "2027-03-04" } });
    fireEvent.focus(box);
    fireEvent.change(box, { target: { value: "japan" } });
    expect(screen.getAllByRole("option").map((o) => o.textContent)).toEqual([`${JP} Japan (JPN)`]);
    fireEvent.mouseDown(screen.getAllByRole("option")[0]);
    // a code works as well as a name
    fireEvent.change(box, { target: { value: "swe" } });
    fireEvent.mouseDown(screen.getAllByRole("option")[0]);
    const chosen = within(screen.getByRole("list", { name: "Chosen countries" }));
    expect(chosen.getAllByRole("listitem").map((li) => li.textContent)).toEqual([`${JP}JapanJPN`, `${SE}SwedenSWE`]);
    fireEvent.click(chosen.getByRole("button", { name: "Remove Sweden" }));
    fireEvent.click(screen.getByRole("button", { name: "Save trip" }));
    await waitFor(() => expect(calls.some((c) => c.url === "/api/trips" && c.body)).toBe(true));
    expect(JSON.parse(calls.find((c) => c.url === "/api/trips" && c.body)!.body!)).toMatchObject({ countries: ["JPN"] });
  });

  it("keeps Save off until the country list has loaded, so no code goes unchecked", async () => {
    let release: (r: Response) => void = () => {};
    vi.stubGlobal("fetch", vi.fn((url: string) => url === "/api/countries"
      ? new Promise<Response>((r) => { release = r; })
      : Promise.resolve(json(url.startsWith("/api/trips") ? trips : { results: [] }))));
    render(<Trips />);
    fireEvent.click(await screen.findByRole("button", { name: "Edit Trip A" }));
    expect(screen.getByRole("button", { name: "Save trip" })).toBeDisabled();
    release(json(COUNTRIES));
    await screen.findByText(/XXX is not an ISO country code/);
    fireEvent.click(screen.getByRole("button", { name: "Remove XXX" }));
    expect(screen.getByRole("button", { name: "Save trip" })).toBeEnabled();
  });

  it("offers only countries on the list, and says so when nothing matches", async () => {
    stubFetch();
    render(<Trips />);
    fireEvent.click(await screen.findByRole("button", { name: "Add a trip" }));
    const box = screen.getByRole("combobox", { name: "Add a country" });
    await waitFor(() => expect(box).toBeEnabled());
    fireEvent.focus(box);
    fireEvent.change(box, { target: { value: "narnia" } });
    expect(screen.getByText("No country matches")).toBeInTheDocument();
    fireEvent.blur(box);
    expect(screen.queryByRole("list", { name: "Chosen countries" })).not.toBeInTheDocument();
  });
});

describe("the trip summary", () => {
  const t = (name: string, start: string, spend: number, days: number, extra: Partial<Trip> = {}) =>
    trip(name, Math.round(spend / days), { start, end: start, days, spend_sgd: spend, ...extra });

  it("adds up the short trips for the day rate and leaves long stays and planned trips out of it", () => {
    const list = [
      t("A", "2026-03-01", 30_000, 3), t("B", "2026-05-01", 10_000, 2),
      t("Lived", "2026-01-01", 900_000, 100, { long_stay: true }), t("Later", "2026-12-01", 0, 4),
    ];
    const s = summarise(list, "2026-09-20", 2026);
    expect(s).toMatchObject({ count: 4, planned: 1, longStays: 1, total: 940_000, shortTrips: 2, perDay: 8_000 });
    expect(averagePerDay(costPerDay(list, "2026-09-20").short)).toBe(8_000);
  });

  it("counts a trip by the year it started, this year against last", () => {
    const list = [t("A", "2026-03-01", 30_000, 3), t("B", "2025-12-30", 10_000, 6), t("C", "2025-02-01", 5_000, 2)];
    expect(summarise(list, undefined, 2026)).toMatchObject({ thisYear: 30_000, lastYear: 15_000, year: 2026 });
  });

  it("calls a day rate dear from exactly a quarter above the average, in whole cents", () => {
    expect(isDear(1_500, 1_200)).toBe(true);
    expect(isDear(1_499, 1_200)).toBe(false);
    expect(isDear(1_300, 1_200)).toBe(false);
    expect(isDear(9_000, null)).toBe(false);
    expect(isDear(null, 1_200)).toBe(false);
    // refunds can make the average zero or negative: a percentage of it marks nothing
    expect(isDear(-100, -100)).toBe(false);
    expect(isDear(0, 0)).toBe(false);
    expect(isDear(500, 0)).toBe(false);
    expect(isDear(500, -100)).toBe(false);
  });

  it("has no day rate before any short trip", () => {
    expect(summarise([], undefined, 2026).perDay).toBeNull();
  });

  it("calls a trip planned only when it starts after the record's last transaction", () => {
    expect(isPlanned({ start: "2026-09-21" }, "2026-09-20")).toBe(true);
    expect(isPlanned({ start: "2026-09-20" }, "2026-09-20")).toBe(false);
    expect(isPlanned({ start: "2030-01-01" }, "")).toBe(false);
    expect(isPlanned({ start: "2030-01-01" }, undefined)).toBe(false);
  });
});

// G18: the trips drawn as bubbles, a circle's area its spending
describe("Trips as bubbles", () => {
  useSizedCharts();

  const serve = (list: Trip[], last = "2026-09-20") => vi.stubGlobal("fetch", vi.fn(async (url: string) => json(
    url.startsWith("/api/trips") ? { long_stay_days: 60, last_txn: last, trips: list } : url === "/api/countries" ? COUNTRIES : { results: [] })));
  const bubble = (container: HTMLElement, name: string) => container.querySelector<SVGGElement>(`g[data-trip="${name}"]`)!;
  const radius = (container: HTMLElement, name: string) => Number(bubble(container, name).querySelector("[data-bubble]")!.getAttribute("r"));

  async function showBubbles(list: Trip[] = trips.trips) {
    serve(list);
    const view = render(<Trips />);
    fireEvent.click(await screen.findByRole("radio", { name: "Bubbles" }));
    await waitFor(() => expect(view.container.querySelectorAll("g[data-trip]").length).toBeGreaterThan(0));
    return view;
  }

  it("draws a circle's area in proportion to spend: four times the spend is twice the radius", async () => {
    const { container } = await showBubbles([
      trip("Small", 1_000, { spend_sgd: 10_000, start: "2026-02-01" }),
      trip("Big", 4_000, { spend_sgd: 40_000, start: "2026-06-01" }),
    ]);
    const small = radius(container, "Small");
    expect(small).toBeGreaterThan(0);
    expect(radius(container, "Big") / small).toBeCloseTo(2, 5);
  });

  it("colours a dear trip in the warn token and the rest in the series colour", async () => {
    const { container } = await showBubbles();
    const fill = (name: string) => bubble(container, name).querySelector("[data-bubble]")!.getAttribute("fill");
    // Trip B's S$90 a day is over a quarter above the S$57 average; Trip A's S$24 is not
    expect(fill("Trip B")).toBe("var(--warn)");
    expect(fill("Trip A")).toBe("var(--c1)");
    // a long stay is never dear by the short trips' rate
    expect(fill("Term abroad")).toBe("var(--c1)");
    const key = within(screen.getByRole("list", { name: "Bubble key" }));
    expect(key.getByText("Circle size is what the trip cost")).toBeInTheDocument();
    expect(key.getByText("25% or more above the average a day")).toBeInTheDocument();
    expect(key.queryByText(/Long stay/)).not.toBeInTheDocument();
  });

  // G18: the cost-per-day card has kept long stays apart since G13; the bubbles do the same
  it("draws long stays in a chart of their own under the trips, headed Long stays", async () => {
    await showBubbles();
    const trips = screen.getByRole("group", { name: "Trips as bubbles" });
    const long = screen.getByRole("group", { name: "Long stays as bubbles" });
    expect(trips.querySelector('g[data-trip="Term abroad"]')).toBeNull();
    expect(long.querySelector('g[data-trip="Term abroad"]')).toHaveAttribute("role", "link");
    expect(long.querySelector('g[data-trip="Trip A"]')).toBeNull();
    expect(long.previousElementSibling).toHaveTextContent(/^Long stays$/);
    fireEvent.keyDown(long.querySelector('g[data-trip="Term abroad"]')!, { key: "Enter" });
    expect(navigate).toHaveBeenLastCalledWith("/transactions?trip=Term+abroad&spend=1");
  });

  it("scales the short trips to the dearest of them, whatever a long stay cost", async () => {
    const shortOnes = [
      trip("Small", 1_000, { spend_sgd: 10_000, start: "2026-02-01" }),
      trip("Big", 4_000, { spend_sgd: 40_000, start: "2026-06-01" }),
    ];
    const alone = await showBubbles(shortOnes);
    const before = [radius(alone.container, "Small"), radius(alone.container, "Big")];
    alone.unmount();
    localStorage.clear();
    const lived = trip("Lived abroad", 5_000, { spend_sgd: 4_000_000, start: "2025-01-10", days: 200, long_stay: true });
    const { container } = await showBubbles([...shortOnes, lived]);
    expect([radius(container, "Small"), radius(container, "Big")]).toEqual(before);
    // the dearest short trip and the dearest long stay each take the full size of their own chart
    expect(radius(container, "Big")).toBeCloseTo(radius(container, "Lived abroad"), 5);
    expect(radius(container, "Big") / radius(container, "Small")).toBeCloseTo(2, 5);
  });

  it("opens the trip's exact rows on a click and on Enter, and is reachable by Tab", async () => {
    const { container } = await showBubbles();
    const b = bubble(container, "Trip A");
    expect(b).toHaveAttribute("role", "link");
    expect(b).toHaveAttribute("tabindex", "0");
    expect(b).toHaveAccessibleName("Trip A, S$120, 5 days, S$24 a day, 1 Aug to 5 Aug 2026");
    fireEvent.click(b);
    expect(navigate).toHaveBeenLastCalledWith("/transactions?trip=Trip+A&spend=1");
    navigate.mockReset();
    fireEvent.keyDown(bubble(container, "Trip B"), { key: "Enter" });
    expect(navigate).toHaveBeenLastCalledWith("/transactions?trip=Trip+B&spend=1");
  });

  it("draws a planned trip with nothing spent as a small outline that opens nothing", async () => {
    const { container } = await showBubbles();
    const b = bubble(container, "Planned break");
    expect(b).not.toHaveAttribute("role", "link");
    expect(b).not.toHaveAttribute("tabindex");
    expect(b.querySelector("circle")).toHaveAttribute("fill", "var(--panel)");
    fireEvent.click(b);
    fireEvent.keyDown(b, { key: "Enter" });
    expect(navigate).not.toHaveBeenCalled();
    expect(screen.getByText("Planned, nothing spent yet")).toBeInTheDocument();
  });

  it("puts the name, total, days, day rate and dates in the hover card", async () => {
    const { container } = await showBubbles();
    fireEvent.mouseEnter(bubble(container, "Trip A").parentElement!);
    const tip = await waitFor(() => {
      const t = container.querySelector(".bg-tip");
      if (!t) throw new Error("no hover card yet");
      return t as HTMLElement;
    });
    expect(tip).toHaveTextContent(`${MY} Trip A`);
    expect(tip).toHaveTextContent("S$120");
    expect(within(tip).getByText("Days").parentElement).toHaveTextContent("5");
    expect(within(tip).getByText("A day").parentElement).toHaveTextContent("S$24");
    expect(within(tip).getByText("Dates").parentElement).toHaveTextContent("1 Aug to 5 Aug 2026");
  });

  it("puts flags inside a bubble big enough to hold them, and none in a small one", async () => {
    const { container } = await showBubbles([
      trip("Huge", 1_000, { spend_sgd: 100_000, start: "2026-02-01" }),
      trip("Tiny", 1_000, { spend_sgd: 1_000, start: "2026-06-01" }),
    ]);
    expect(bubble(container, "Huge").querySelector("text")).toHaveTextContent(MY);
    expect(bubble(container, "Tiny").querySelector("text")).toBeNull();
  });

  it("draws one evenly spaced row per year that has a trip, however many years lie between", async () => {
    const { container } = await showBubbles([
      trip("Old", 1_000, { start: "2019-05-01" }), trip("Last", 1_000, { start: "2025-05-01" }), trip("Now", 1_000, { start: "2026-05-01" }),
    ]);
    const chart = screen.getByRole("group", { name: "Trips as bubbles" });
    const ticks = [...chart.querySelectorAll(".recharts-yAxis .recharts-cartesian-axis-tick-value")].map((t) => t.textContent);
    expect(ticks).toEqual(["2019", "2025", "2026"]);
    const cy = (name: string) => Number(bubble(container, name).querySelector("[data-bubble]")!.getAttribute("cy"));
    // newest at the top, and 2025 sits as far from 2026 as from 2019
    expect(cy("Now")).toBeLessThan(cy("Last"));
    expect(cy("Last") - cy("Now")).toBeCloseTo(cy("Old") - cy("Last"), 5);
  });

  it("remembers the view in this browser", async () => {
    const first = await showBubbles();
    expect(localStorage.getItem("kopifund.chart-view.trips")).toBe("bubbles");
    expect(screen.queryByRole("link", { name: "Trip A" })).not.toBeInTheDocument();
    first.unmount();
    render(<Trips />);
    expect(await screen.findByRole("radio", { name: "Bubbles" })).toHaveAttribute("aria-checked", "true");
    fireEvent.click(screen.getByRole("radio", { name: "Cards" }));
    expect(await screen.findByRole("link", { name: "Trip A" })).toBeInTheDocument();
    expect(localStorage.getItem("kopifund.chart-view.trips")).toBe("cards");
  });
});

describe("the trip form in a Sheet", () => {
  useSizedCharts();

  it("opens Add a trip in a dialog named for it, and Cancel closes it without saving", async () => {
    const calls = stubFetch();
    render(<Trips />);
    fireEvent.click(await screen.findByRole("button", { name: "Add a trip" }));
    const sheet = within(screen.getByRole("dialog", { name: "Add a trip" }));
    expect(sheet.getByLabelText("Name")).toBeInTheDocument();
    fireEvent.click(sheet.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(calls.some((c) => c.url === "/api/trips" && c.body)).toBe(false);
  });

  it("opens a card's edit in the dialog, and asks before Escape leaves typed changes behind", async () => {
    const calls = stubFetch();
    render(<Trips />);
    fireEvent.click(await screen.findByRole("button", { name: "Edit Trip B" }));
    const dialog = screen.getByRole("dialog", { name: "Edit Trip B" });
    expect(within(dialog).getByLabelText("Name")).toHaveValue("Trip B");
    // nothing typed yet: Escape just closes
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Edit Trip B" }));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Trip B, renamed" } });
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.getByRole("dialog", { name: "Edit Trip B" })).toHaveTextContent("Leave without saving?");
    fireEvent.click(screen.getByRole("button", { name: "Keep editing" }));
    expect(screen.getByLabelText("Name")).toHaveValue("Trip B, renamed");
    fireEvent.keyDown(window, { key: "Escape" });
    fireEvent.click(screen.getByRole("button", { name: "Leave" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(calls.some((c) => c.url === "/api/trips" && c.body)).toBe(false);
  });

  it("closes the dialog when the trip saves", async () => {
    stubFetch();
    render(<Trips />);
    fireEvent.click(await screen.findByRole("button", { name: "Edit Trip B" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Save trip" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "Save trip" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });
});

describe("bubblesOf", () => {
  it("counts the day of the year from 1 Jan", () => {
    expect(dayOfYear("2026-01-01")).toBe(1);
    expect(dayOfYear("2026-03-01")).toBe(60);
    expect(dayOfYear("2024-12-31")).toBe(366);
  });

  it("puts the biggest first so small ones draw on top, planned outlines last, and leaves undated trips out", () => {
    const list = bubblesOf([
      trip("Mid", 1_000, { spend_sgd: 5_000 }), trip("Big", 1_000, { spend_sgd: 9_000 }),
      trip("Later", 0, { start: "2026-12-01", spend_sgd: 0, rows: 0, per_day: 0 }), trip("Undated", 1_000, { start: "" }),
      trip("Refunded", 0, { spend_sgd: -500 }),
    ], "2026-09-20");
    expect(list.map((b) => b.name)).toEqual(["Big", "Mid", "Refunded", "Later"]);
    expect(list.map((b) => [b.year, b.area, b.empty])).toEqual([[2026, 9_000, false], [2026, 5_000, false], [2026, 0, false], [2026, 0, true]]);
  });

  it("gives each year that has a trip a row by its index, so years with none take no room", () => {
    const list = bubblesOf([
      trip("Old", 1_000, { start: "2019-05-01" }), trip("Last", 1_000, { start: "2025-05-01" }), trip("Now", 1_000, { start: "2026-05-01" }),
    ]);
    expect(Object.fromEntries(list.map((b) => [b.year, b.row]))).toEqual({ 2019: 0, 2025: 1, 2026: 2 });
  });
});

// G18: Greptile on #95. A save that fails after the Sheet was closed must still be seen.
describe("a trip save that answers late", () => {
  useSizedCharts();

  // the save waits on `answer`; every other request answers at once
  function stubSlowSave() {
    let answer: (r: Response) => void = () => {};
    vi.stubGlobal("fetch", vi.fn((url: string, init?: RequestInit) => {
      if (url === "/api/trips" && init?.method === "POST") return new Promise<Response>((r) => { answer = r; });
      const body = url.startsWith("/api/trips") ? trips : url === "/api/countries" ? COUNTRIES : { results: [] };
      return Promise.resolve(json(body));
    }));
    return (status: number, body: unknown) => answer(json(body, status));
  }

  it("shows a refusal in the form while it is open", async () => {
    const answer = stubSlowSave();
    render(<><Trips /><Toaster /></>);
    fireEvent.click(await screen.findByRole("button", { name: "Edit Trip B" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Save trip" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "Save trip" }));
    answer(400, { error: "A trip by that name already exists" });
    expect(await within(screen.getByRole("dialog", { name: "Edit Trip B" })).findByRole("alert"))
      .toHaveTextContent("A trip by that name already exists");
  });

  it("says so in an error toast when the form was closed before the refusal came", async () => {
    const answer = stubSlowSave();
    render(<><Trips /><Toaster /></>);
    fireEvent.click(await screen.findByRole("button", { name: "Edit Trip B" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Save trip" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "Save trip" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    answer(400, { error: "A trip by that name already exists" });
    expect(await screen.findByText("Trip B was not saved: A trip by that name already exists")).toBeInTheDocument();
  });

  it("leaves a form opened since alone when the earlier save answers", async () => {
    const answer = stubSlowSave();
    render(<><Trips /><Toaster /></>);
    fireEvent.click(await screen.findByRole("button", { name: "Edit Trip B" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Save trip" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "Save trip" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    fireEvent.click(screen.getByRole("button", { name: "Add a trip" }));
    answer(200, { name: "Trip B", relinked: { changed: 0, written: 0 } });
    expect(await screen.findByText(/Trip saved/)).toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "Add a trip" })).toBeInTheDocument();
  });
});
