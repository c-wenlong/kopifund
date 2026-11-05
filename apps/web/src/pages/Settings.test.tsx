import "@testing-library/jest-dom/vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { revalidate } from "../api";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Settings } from "./Settings";
import { leadingIcon } from "../test-icons";
import { ServerBanner } from "../components/ServerBanner";

const scamChecks = {
  settings: { big_transfer: 50_000, repeat_count: 3, repeat_days: 14, new_payee_days: 30,
    abroad_min: 2_000, test_max: 200, test_days: 7, twice_min: 2_000, new_sub_days: 60 },
  flags: [], marked_fine: [{ key: "t1", date: "2026-09-10", payee: "SCAMMER PTE", amount: 90_000 }],
};
const posts: { url: string; body?: string }[] = [];

// G23: the Server card's answer; a stop write flips `up` so the next health check misses
function stubFetch({ health = true, exports = [{ stamp: "2026-10-12", files: ["transactions.csv", "stats.json", "notes.txt"] }],
  waitingFor = [] as string[] } = {}) {
  let up = health, pid = 4321;
  const started = () => Math.floor(Date.now() / 1000) - 2 * 3600 - 5 * 60;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    if (url === "/api/health") return up ? new Response(JSON.stringify({ ok: true }), { status: 200 }) : Promise.reject(new Error("down"));
    if (url.startsWith("/api/server/") && init?.method === "POST") {
      posts.push({ url });
      // a stop with work under way keeps answering until the test lets it go
      if (url.endsWith("/stop") && !waitingFor.length) up = false;
      if (url.endsWith("/restart")) pid += 1;
      return new Response(JSON.stringify({ ok: true, waiting_for: waitingFor, timeout: 120 }), { status: 200 });
    }
    if (url === "/api/server") {
      if (!up) return Promise.reject(new Error("down"));
      return new Response(JSON.stringify({ version: "0.1.0", mode: "serve", port: 8770, pid, started: started() }), { status: 200 });
    }
    if (url.startsWith("/api/exports")) return new Response(JSON.stringify({ exports }), { status: 200 });
    // G22: the AI card has its own tests (AiCard.test.tsx); here it asks a server that is down along with the rest
    if (url.startsWith("/api/ai") && !health) return Promise.reject(new Error("down"));
    // the app icon card has its own tests (AppIconCard.test.tsx); a server that is down answers it nothing either
    if (url.startsWith("/api/app-icon")) {
      if (!health) return Promise.reject(new Error("down"));
      return new Response(JSON.stringify({ sets: [{ name: "sticker", label: "Sticker", about: "Kopi in a bag" }],
        default: "sticker", chosen: "sticker" }), { status: 200 });
    }
    if (url.startsWith("/api/scam-checks")) {
      posts.push({ url, body: init?.body as string | undefined });
      return new Response(JSON.stringify(scamChecks), { status: 200 });
    }
    if (url === "/api/export" && init?.method === "POST") {
      return new Response(JSON.stringify({ folder: "exports/2026-10-12", stamp: "2026-10-12", files: [], transactions: 1200 }), { status: 200 });
    }
    // G25: the link card has its own tests (LinkCard.test.tsx); here it sees an empty registry
    if (url.startsWith("/api/link/devices")) return new Response(JSON.stringify({ devices: [], url: "" }), { status: 200 });
    if (url.startsWith("/api/link/pending")) return new Response(JSON.stringify({ rows: [] }), { status: 200 });
    return new Response("{}", { status: 404 });
  }));
  return { goDown: () => { up = false; } };
}

// G18: true when the page would hold a reload or a closed tab behind the browser's question
const leaveAsks = () => !window.dispatchEvent(new Event("beforeunload", { cancelable: true }));

describe("Scam checks card", () => {
  beforeEach(() => { posts.length = 0; });
  afterEach(() => vi.unstubAllGlobals());

  it("shows the saved thresholds and saves a change in cents", async () => {
    stubFetch();
    render(<Settings />);
    const big = await screen.findByRole("textbox", { name: "Big transfer" });
    expect(big).toHaveValue("500");
    expect(screen.getByRole("textbox", { name: "Repeated within" })).toHaveValue("14");
    fireEvent.change(big, { target: { value: "750.50" } });
    expect(screen.getByRole("textbox", { name: "Test charge up to" })).toHaveValue("2");
    fireEvent.change(screen.getByRole("textbox", { name: "Test charge up to" }), { target: { value: "1.50" } });
    expect(screen.getByText("Unsaved changes")).toBeInTheDocument();
    expect(leaveAsks()).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(posts.some((p) => p.url === "/api/scam-checks/settings")).toBe(true));
    expect(JSON.parse(posts.find((p) => p.url === "/api/scam-checks/settings")!.body!))
      .toEqual({ big_transfer: 75_050, repeat_count: 3, repeat_days: 14, new_payee_days: 30,
        abroad_min: 2_000, test_max: 150, test_days: 7, twice_min: 2_000, new_sub_days: 60 });
  });

  it("refuses a count that is not a whole number before sending it", async () => {
    stubFetch();
    render(<Settings />);
    fireEvent.change(await screen.findByRole("textbox", { name: "Repeated transfers" }), { target: { value: "2.5" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("whole number");
    expect(posts.some((p) => p.url === "/api/scam-checks/settings")).toBe(false);
  });

  it("says so beside the row when flagging again fails", async () => {
    stubFetch();
    const ok = globalThis.fetch;
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => url === "/api/scam-checks/fine"
      ? new Response(JSON.stringify({ error: "the record is busy" }), { status: 422 }) : ok(url, init)));
    render(<Settings />);
    fireEvent.click(await screen.findByRole("button", { name: "Flag again: SCAMMER PTE on 10 Sep 2026" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("the record is busy");
  });

  it("lists rows marked fine and flags one again", async () => {
    stubFetch();
    render(<Settings />);
    fireEvent.click(await screen.findByRole("button", { name: "Flag again: SCAMMER PTE on 10 Sep 2026" }));
    await waitFor(() => expect(posts.some((p) => p.url === "/api/scam-checks/fine")).toBe(true));
    expect(JSON.parse(posts.find((p) => p.url === "/api/scam-checks/fine")!.body!)).toEqual({ key: "t1", fine: false });
  });
});

describe("Settings", () => {
  beforeEach(() => window.localStorage.clear());
  afterEach(() => vi.unstubAllGlobals());

  it("shows the local server as a status chip, not a card", async () => {
    stubFetch();
    render(<Settings />);
    expect(await screen.findByText("Server connected")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Backend" })).not.toBeInTheDocument();
    // G18: the chip leads with a plug, the word beside it
    expect(leadingIcon(screen.getByText("Server connected"))).toBe("plug-zap");
  });

  it("says when the server is offline", async () => {
    stubFetch({ health: false });
    render(<Settings />);
    expect(await screen.findByText("Server offline")).toBeInTheDocument();
    expect(leadingIcon(screen.getByText("Server offline"))).toBe("unplug");
  });

  // G19: the chip asks through checkServer, so the offline banner and the chip never disagree
  it("tells the offline banner too when the server does not answer", async () => {
    stubFetch({ health: false });
    render(<><Settings /><ServerBanner /></>);
    expect(await screen.findByText("Server offline")).toBeInTheDocument();
    expect(await screen.findByText(/Can't reach Kopifund\. Is/)).toBeInTheDocument();
  });

  it("says connected again once the server is back, as the banner goes", async () => {
    let up = false;
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (url === "/api/health") return up ? new Response(JSON.stringify({ ok: true }), { status: 200 }) : Promise.reject(new Error("down"));
      if (url.startsWith("/api/exports")) return new Response(JSON.stringify({ exports: [] }), { status: 200 });
      return new Response(JSON.stringify(scamChecks), { status: 200 });
    }));
    render(<><Settings /><ServerBanner /></>);
    expect(await screen.findByText("Server offline")).toBeInTheDocument();
    up = true;
    fireEvent.click(await screen.findByRole("button", { name: "Retry" }));
    expect(await screen.findByText("Server connected")).toBeInTheDocument();
    expect(screen.queryByText("Server offline")).not.toBeInTheDocument();
    expect(screen.queryByText(/Can't reach Kopifund\. Is/)).not.toBeInTheDocument();
  });

  it("offers the two sidebar modes with no hint lines under them", async () => {
    stubFetch();
    render(<Settings />);
    const group = await screen.findByRole("group", { name: "Sidebar behaviour" });
    expect(within(group).getAllByRole("radio").map((r) => r.closest("label")!.textContent)).toEqual(["Keep open", "Expand on hover"]);
    expect(screen.queryByText(/rests as icons|stays expanded/)).not.toBeInTheDocument();
    fireEvent.click(within(group).getByRole("radio", { name: "Expand on hover" }));
    expect(window.localStorage.getItem("kopifund.sidebar")).toBe("hover");
  });

  it("hides the sidebar setting under 768px, where the sidebar is a drawer", async () => {
    vi.stubGlobal("matchMedia", (media: string) => ({ matches: false, media, addEventListener() {}, removeEventListener() {} }));
    stubFetch();
    render(<Settings />);
    await screen.findByText("Server connected");
    expect(screen.queryByRole("group", { name: "Sidebar behaviour" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Sidebar" })).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Export" })).toBeInTheDocument();
  });

  it("lists the exported files with an icon and a download button each", async () => {
    stubFetch();
    render(<Settings />);
    const list = await screen.findByRole("list", { name: "Files exported 2026-10-12" });
    const items = within(list).getAllByRole("listitem");
    expect(items.map((li) => li.textContent?.replace("Download", "").trim())).toEqual(["transactions.csv", "stats.json", "notes.txt"]);
    for (const li of items) expect(li.querySelector("svg")).not.toBeNull();
    const link = within(list).getByRole("link", { name: "Download transactions.csv" });
    expect(link).toHaveAttribute("href", "/api/exports/2026-10-12/transactions.csv");
    expect(link).toHaveAttribute("download");
    expect(within(list).getAllByRole("link")).toHaveLength(3);
  });

  it("shows the first few files of a long export and opens the rest on request", async () => {
    stubFetch({ exports: [{ stamp: "2026-10-12", files: Array.from({ length: 9 }, (_, i) => `stat-${i}.csv`) }] });
    render(<Settings />);
    const list = await screen.findByRole("list", { name: "Files exported 2026-10-12" });
    expect(within(list).getAllByRole("link")).toHaveLength(5);
    fireEvent.click(within(list).getByRole("button", { name: "Show all 9" }));
    expect(within(list).getAllByRole("link")).toHaveLength(9);
  });

  it("exports everything and says how many transactions", async () => {
    stubFetch();
    render(<Settings />);
    fireEvent.click(await screen.findByRole("button", { name: "Export everything" }));
    await waitFor(() => expect(screen.getByText("Exported 1,200 transactions to exports/2026-10-12.")).toBeInTheDocument());
  });
});

// G18: unsaved means typed here; thresholds saved from another tab are not an edit on this one
describe("Scam checks saved from another tab", () => {
  const was = { ...scamChecks.settings };
  afterEach(() => { Object.assign(scamChecks.settings, was); vi.unstubAllGlobals(); });

  it("show in every box left alone, and a box typed in keeps its typing", async () => {
    stubFetch();
    render(<Settings />);
    const big = await screen.findByRole("textbox", { name: "Big transfer" });
    scamChecks.settings.big_transfer = 60_000;
    act(() => revalidate());
    await waitFor(() => expect(screen.getByRole("textbox", { name: "Big transfer" })).toHaveValue("600"));
    expect(screen.queryByText("Unsaved changes")).not.toBeInTheDocument();
    expect(leaveAsks()).toBe(false);
    fireEvent.change(big, { target: { value: "650" } });
    scamChecks.settings.big_transfer = 70_000;
    scamChecks.settings.repeat_days = 21;
    act(() => revalidate());
    await waitFor(() => expect(screen.getByRole("textbox", { name: "Repeated within" })).toHaveValue("21"));
    expect(screen.getByRole("textbox", { name: "Big transfer" })).toHaveValue("650");
    expect(leaveAsks()).toBe(true);
  });

  it("a box typed back to its saved value leaves nothing unsaved", async () => {
    stubFetch();
    render(<Settings />);
    const big = await screen.findByRole("textbox", { name: "Big transfer" });
    fireEvent.change(big, { target: { value: "650" } });
    fireEvent.change(big, { target: { value: "500" } });
    expect(screen.queryByText("Unsaved changes")).not.toBeInTheDocument();
    expect(leaveAsks()).toBe(false);
  });

  it("keeps typing made while a save is on its way, back to the old saved value included", async () => {
    stubFetch();
    const ask = globalThis.fetch;
    let release: () => void = () => {};
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      if (url === "/api/scam-checks/settings" && init?.method === "POST") {
        const sent = JSON.parse(String(init.body));
        return new Promise<Response>((done) => {
          release = () => { Object.assign(scamChecks.settings, sent); done(new Response(JSON.stringify(scamChecks), { status: 200 })); };
        });
      }
      return ask(url, init);
    }));
    render(<Settings />);
    const big = await screen.findByRole("textbox", { name: "Big transfer" });
    fireEvent.change(big, { target: { value: "650" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Big transfer" }), { target: { value: "500" } });
    fireEvent.change(screen.getByRole("textbox", { name: "Repeated within" }), { target: { value: "21" } });
    await act(async () => release());
    await waitFor(() => expect(scamChecks.settings.big_transfer).toBe(65_000));
    await waitFor(() => expect(screen.getByRole("button", { name: "Save" })).toBeEnabled());
    expect(screen.getByRole("textbox", { name: "Big transfer" })).toHaveValue("500");
    expect(screen.getByRole("textbox", { name: "Repeated within" })).toHaveValue("21");
    expect(screen.getByText("Unsaved changes")).toBeInTheDocument();
    expect(leaveAsks()).toBe(true);
  });
});

// G23: the Server card
describe("Server card", () => {
  beforeEach(() => { posts.length = 0; });
  afterEach(() => vi.unstubAllGlobals());

  it("says how long the server has run and what started it", async () => {
    stubFetch();
    render(<Settings />);
    expect(await screen.findByText(/Running for/)).toHaveTextContent("Running for 2 hours 5 minutes");
    expect(await screen.findByText(/Started by make serve on 127\.0\.0\.1:8770 · pid 4321/)).toBeInTheDocument();
    expect(await screen.findByText("Server connected")).toBeInTheDocument();
  });

  it("stops the server after a confirm, then says how to start it", async () => {
    stubFetch();
    render(<Settings />);
    await screen.findByText("Server connected");
    fireEvent.click(screen.getByRole("button", { name: "Stop" }));
    expect(screen.getByText(/Every page goes offline/)).toBeInTheDocument();
    expect(posts.some((p) => p.url === "/api/server/stop")).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Stop" }));
    await waitFor(() => expect(posts.some((p) => p.url === "/api/server/stop")).toBe(true));
    expect(await screen.findByText("Stopped.", {}, { timeout: 3000 })).toBeInTheDocument();
    expect(screen.getByText(/make serve/)).toBeInTheDocument();
    expect(await screen.findByText("Server offline")).toBeInTheDocument();
  });

  it("says what a stop waits for, and goes offline once the work is done", async () => {
    const server = stubFetch({ waitingFor: ["a load", "the stats rebuild"] });
    render(<><Settings /><ServerBanner /></>);
    await screen.findByText("Server connected");
    fireEvent.click(screen.getByRole("button", { name: "Stop" }));
    fireEvent.click(screen.getByRole("button", { name: "Stop" }));
    expect(await screen.findByRole("status")).toHaveTextContent(
      "Waiting for a load and the stats rebuild to finish first (2 minutes at most).");
    // still up a while later: the card waits, the banner stays quiet
    await new Promise((r) => setTimeout(r, 1200));
    expect(screen.queryByText("Stopped.")).not.toBeInTheDocument();
    server.goDown();
    expect(await screen.findByText("Stopped.", {}, { timeout: 4000 })).toBeInTheDocument();
  });

  it("restarts the server and waits for the new process without the offline banner", async () => {
    stubFetch();
    render(<><Settings /><ServerBanner /></>);
    await screen.findByText("Server connected");
    fireEvent.click(screen.getByRole("button", { name: "Restart" }));
    await waitFor(() => expect(posts.some((p) => p.url === "/api/server/restart")).toBe(true), { timeout: 3000 });
    await waitFor(() => expect(screen.getByRole("button", { name: "Restart" })).toBeEnabled(), { timeout: 3000 });
    expect(screen.getByText(/Running for/)).toBeInTheDocument();
    expect(screen.queryByText(/Can't reach Kopifund/)).not.toBeInTheDocument();
  });
});
