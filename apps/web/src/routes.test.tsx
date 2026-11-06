import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { App } from "./App";
import { cacheable, type Overview } from "./api";
import { bottomItems, bottomPages, isUnder, pages } from "./nav";
import { NAV_PANELS, ROUTES, routeFor, titleFor } from "./routes";
import { json, settle, stubMatchMedia } from "./test-helpers";

// G19: the route table (routes.tsx) feeds the sidebar, the page titles and the
// prefetch lists. These tests open every page and check the table against what
// the page really asks for.

// invented figures; just enough for Home to draw, since Home asks for the net
// worth history only once the overview is in
const overview: Overview = {
  as_of: "2026-09-30",
  headline: { net_worth_incl_cpf: null, net_worth_change: null, cash: null, cash_change: null, runway_months: null, runway_range: null },
  holdings: [],
  spending: {
    by: "bucket", scope: "all", bucket_order: [], bucket_style: {}, months: [], series: [], link: {},
    totals: { spend: 0, per_month: 0, overseas: 0 },
  },
  coming_up: {
    month: "2026-09", as_of: "2026-09-30", rows: [],
    totals: { committed: 0, paid: 0, investing: 0, investing_paid: 0, extra: 0, investing_extra: 0 },
  },
  needs_your_eye: { needs_category: 0, recurring_off: [], one_off: null, cash_trend_6m: null, irregular: 0, scam: null,
    upload: null, needs_rule: 0, missing_fx: 0, stale_accounts: [], pending: 0, lines: [] },
  missing_fx: 0,
};

const requested: string[] = [];

beforeEach(() => {
  requested.length = 0;
  // wide, so the sidebar draws
  stubMatchMedia(true);
  // every figure but the overview fails to load, which is enough: the requests
  // a page makes as it opens do not wait for an answer
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = String(input);
    requested.push(url);
    if (url.startsWith("/api/setup")) return json({ needed: false });
    if (url === "/api/overview") return json(overview);
    return json({ error: "not loaded" }, 404);
  });
});

afterEach(() => {
  window.history.pushState(null, "", "/");
});

// A URL with a query is built from what the page has loaded or what is typed (a
// month, a filter, a page of rows), so the pointer on a link cannot know it.
// Only the cacheable ones matter: prefetch skips the rest. The overview is the
// shell's own ask on every page, not the page's.
function asked(route: { path: string }) {
  const fixed = requested.filter((u) => !u.includes("?") && cacheable(u) && (u !== "/api/overview" || route.path === "/"));
  return [...new Set(fixed)].sort();
}

describe("the route table", () => {
  it.each(ROUTES.map((r) => [r.path, r] as const))("%s: its prefetch list is what the page asks for as it opens", async (_path, route) => {
    window.history.pushState(null, "", route.path);
    render(<App />);
    await screen.findByRole("heading", { level: 1, name: route.title });
    const want = [...(route.api ?? [])].sort();
    await waitFor(() => expect(asked(route)).toEqual(want));
    // nothing else turns up a moment later
    await settle();
    expect(asked(route)).toEqual(want);
  });

  it("gives every address one page", () => {
    const addresses = ROUTES.flatMap((r) => [r.path, ...(r.aliases ?? [])]);
    expect(new Set(addresses).size).toBe(addresses.length);
  });

  it("puts the sidebar's pages in the table's order, profile and settings in the bottom group", () => {
    expect(pages.map((p) => p.path)).toEqual([
      "/", "/dashboard", "/transactions", "/spending", "/recurring", "/trips", "/net-worth", "/plan", "/categories", "/upload", "/rules",
    ]);
    expect(bottomPages.map((p) => p.path)).toEqual(["/profile", "/settings"]);
    expect(pages.map((p) => p.label)).toContain("Net worth");
  });

  it("places Needs your eye, a panel with no address, in the bottom group above Your profile (G18)", async () => {
    expect(bottomItems.map((i) => (i.kind === "page" ? i.path : i.id))).toEqual(["needs-your-eye", "/profile", "/settings"]);
    expect(NAV_PANELS.every((n) => !routeFor(n.id) && ROUTES.some((r) => r.path === n.before && r.nav?.group === n.group))).toBe(true);
    window.history.pushState(null, "", "/settings");
    render(<App />);
    await screen.findByRole("heading", { level: 1, name: "Settings" });
    const bottom = screen.getByRole("link", { name: "Your profile" }).parentElement!;
    expect([...bottom.children].map((el) => el.getAttribute("aria-label") ?? el.textContent))
      .toEqual(["Needs your eye", "Your profile", "Settings"]);
  });

  it("names the tab after the page, an old address and a page under another included", () => {
    expect(titleFor("/net-worth")).toBe("Net worth");
    expect(titleFor("/monthly-run")).toBe("Upload");
    expect(titleFor("/upload/history")).toBe("Past uploads");
    expect(titleFor("/profile")).toBe("Your profile");
    expect(titleFor("/nowhere")).toBe("Page not found");
    expect(routeFor("/nowhere")).toBeUndefined();
  });

  it("lights the link of the page an address sits under", () => {
    expect(isUnder("/monthly-run", "/upload")).toBe(true);
    expect(isUnder("/upload/history", "/upload")).toBe(true);
    expect(isUnder("/upload", "/rules")).toBe(false);
    expect(isUnder("/transactions", "/")).toBe(false);
  });

  it("answers an address no page has with Page not found, and keeps the old Upload address working", async () => {
    window.history.pushState(null, "", "/nowhere");
    const { unmount } = render(<App />);
    expect(await screen.findByRole("heading", { level: 1, name: "Page not found" })).toBeInTheDocument();
    expect(screen.getByText("There is no page at this address.")).toBeInTheDocument();
    unmount();
    window.history.pushState(null, "", "/monthly-run");
    render(<App />);
    expect(await screen.findByRole("heading", { level: 1, name: "Upload" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Past uploads" })).toHaveAttribute("href", "/upload/history");
    expect(document.title).toBe("Upload, Kopifund");
  });
});

describe("Home when the overview does not load", () => {
  it("says why, and Try again asks again and draws Home", async () => {
    let up = false;
    vi.mocked(globalThis.fetch).mockImplementation(async (input) => {
      const url = String(input);
      if (url.startsWith("/api/setup")) return json({ needed: false });
      if (url === "/api/overview") return up ? json(overview) : json({ error: "The record is locked." }, 500);
      return json({ error: "not loaded" }, 404);
    });
    window.history.pushState(null, "", "/");
    render(<App />);
    expect(await within(screen.getByRole("main")).findByText("The record is locked.")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Spending, last 12 months" })).not.toBeInTheDocument();
    up = true;
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("heading", { name: "Spending, last 12 months" })).toBeInTheDocument();
    expect(screen.queryByText("The record is locked.")).not.toBeInTheDocument();
  });
});
