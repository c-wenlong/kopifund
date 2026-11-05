import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { outcome, PastUploads, statusText, stoppedAt } from "./PastUploads";
import { leadingIcon } from "../test-icons";
import type { UploadStepOutline } from "../api";

const step = (id: number, name: UploadStepOutline["step"], status: UploadStepOutline["status"], summary = ""): UploadStepOutline =>
  ({ id, step: name, status, summary, started_at: "2026-10-12T06:00:00+00:00", finished_at: "2026-10-12T06:00:05+00:00" });

const list = {
  next: null,
  total: 2,
  attempts: [
    { id: 2, origin: "upload", started_at: "2026-10-12T06:00:00+00:00",
      steps: [step(3, "upload", "ok"), step(4, "upload", "duplicate"), step(5, "check", "ok"), step(6, "load", "blocked", "not loaded: 1 problem")] },
    { id: 1, origin: "rules", started_at: "2026-10-11T06:00:00+00:00",
      steps: [step(1, "load", "ok", "added 5, updated 2, archived 0; 12 stats rerun")] },
  ],
};
const detail = {
  id: 2, origin: "upload", started_at: "2026-10-12T06:00:00+00:00",
  steps: [
    { ...step(5, "check", "ok", "129 statements, 1,200 rows: 1 to add"), input: { folders: ["statements/dbs"] },
      output: { problems: [], statements: 129, rows_by_statement: { "statements/dbs/2026/2026-09.pdf": 40 },
        to_add: [{ key: "k1", date: "2026-09-03", amount: -1250, currency: "SGD", payee: "Grab", text: "GRAB*", category: "Transport" },
          { key: "k2", date: "2026-09-04", amount: -1234567, currency: "SEK", payee: "Kiosk", text: "KIOSK", category: "" }] } },
    { ...step(6, "load", "blocked", "not loaded: 1 problem"), input: {},
      output: { problems: [{ code: "block-not-reconciled", subject: "•• 1111 · SGD · 2026-09", fix: "the balance chain in statements/dbs-multiplier/2026/2026-09.pdf does not close" }] } },
  ],
};
const status = { folders: [{ path: "statements/dbs-multiplier", name: "dbs-multiplier", parser: "dbs", wallet: "", title: "DBS Multiplier", bank: "dbs" }], notion_enabled: false, pdftotext: true, runs: [] };

function reply(body: unknown) {
  return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
}

describe("stoppedAt", () => {
  it("names the furthest step and how it went", () => {
    expect(stoppedAt([step(1, "upload", "ok"), step(2, "check", "problems")])).toEqual({ text: "Failed at check", ok: false, tone: "danger" });
    // a check that passed and was never loaded is a quiet outcome, not a failure
    expect(stoppedAt([step(1, "upload", "ok"), step(2, "check", "ok")])).toEqual({ text: "Checked, not loaded", ok: true, tone: "neutral" });
    expect(stoppedAt([step(1, "upload", "ok")])).toEqual({ text: "Uploaded, not checked", ok: true, tone: "neutral" });
    expect(stoppedAt([step(1, "check", "ok"), step(2, "load", "ok")])).toEqual({ text: "Loaded", ok: true, tone: "good" });
    expect(stoppedAt([step(1, "load", "ok"), step(2, "mirror", "ok")])).toEqual({ text: "Loaded and mirrored", ok: true, tone: "good" });
    expect(stoppedAt([step(1, "load", "ok"), step(2, "mirror", "failed")]).text).toBe("Failed at mirror");
    // a second check after a failed load does not hide where it got to
    expect(stoppedAt([step(1, "load", "blocked"), step(2, "check", "ok")]).text).toBe("Failed at load");
  });
});

describe("outcome", () => {
  it("reads what a load wrote, else what a check found, else the files saved", () => {
    expect(outcome([step(1, "load", "ok", "added 5, updated 2, archived 1; 12 stats rerun")])).toBe("5 added, 2 updated, 1 archived");
    expect(outcome([step(1, "check", "ok", "129 statements, 4,693 rows: 7 to add, 3 recategorised, 0 to archive")])).toBe("7 to add, 3 recategorised, 0 to archive");
    expect(outcome([step(1, "upload", "ok"), step(2, "upload", "ok"), step(3, "upload", "duplicate")])).toBe("2 files saved");
    expect(outcome([step(1, "load", "blocked", "not loaded: 1 problem")])).toBeNull();
  });
});

describe("statusText", () => {
  it("says Passed for a check that went well, and each step's own word for the rest", () => {
    expect(statusText("check", "ok")).toBe("Passed");
    expect(statusText("upload", "ok")).toBe("Added");
    expect(statusText("load", "ok")).toBe("Loaded");
    expect(statusText("upload", "duplicate")).toBe("Already there");
    expect(statusText("mirror", "failed")).toBe("Failed");
  });
});

describe("PastUploads", () => {
  beforeEach(() => window.localStorage.clear());
  afterEach(() => vi.restoreAllMocks());

  const serve = () => vi.spyOn(globalThis, "fetch").mockImplementation(async (url) => {
    const u = String(url);
    return reply(u.startsWith("/api/uploads?") ? list : u === "/api/run/status" ? status : detail);
  });

  it("lists each attempt with its steps and opens one to show inputs and outputs", async () => {
    serve();
    render(<PastUploads />);
    const rows = await screen.findAllByRole("button", { expanded: false });
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("Failed at load");
    expect(rows[0]).toHaveTextContent("Upload ×2");
    expect(rows[1]).toHaveTextContent("from Rules");
    expect(rows[1]).toHaveTextContent("Loaded");
    // the closed row carries the counts: what the load wrote, and for an attempt that never loaded, the files saved
    expect(rows[1]).toHaveTextContent("5 added, 2 updated, 0 archived");
    expect(rows[0]).toHaveTextContent("1 file saved");

    fireEvent.click(rows[0]);
    // the problem is a card with plain words and the code on its details line
    expect(await screen.findByText("Statement doesn't add up")).toBeInTheDocument();
    expect(screen.getByText("block-not-reconciled")).toBeInTheDocument();
    expect(screen.getByText(/DBS Multiplier/)).toBeInTheDocument();
    // 129 statements were read but one file printed transactions: the gap is explained
    expect(screen.getByText("Rows read from each file (1 file)")).toBeInTheDocument();
    expect(screen.getByText(/129 statements were read. The other 128 produced no transaction rows/)).toBeInTheDocument();
    expect(screen.getByText("Transactions to add (2)")).toBeInTheDocument();
    expect(screen.getAllByText("Technical details").length).toBeGreaterThan(0);
    expect(screen.queryByText("Everything that went in and came out")).not.toBeInTheDocument();
    const table = screen.getByText("statements/dbs/2026/2026-09.pdf").closest("tr")!;
    expect(within(table).getByText("40")).toBeInTheDocument();
    // a foreign amount is grouped and signed like every other: no raw toFixed
    expect(screen.getByText(/−SEK\s12,345\.67/)).toBeInTheDocument();
    expect(screen.getByText("−S$12.50")).toBeInTheDocument();
    // the check's chip says Passed, not the raw status
    expect(screen.getByText("Passed")).toBeInTheDocument();
  });

  it("asks for the run status once, however many attempts are opened one after another", async () => {
    const spy = serve();
    render(<PastUploads />);
    const rows = await screen.findAllByRole("button", { expanded: false });
    const asked = () => spy.mock.calls.filter(([u]) => String(u) === "/api/run/status").length;
    // nothing open, nothing asked
    expect(asked()).toBe(0);
    fireEvent.click(rows[0]);
    expect(await screen.findByText("Statement doesn't add up")).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole("button", { expanded: false })[0]);
    fireEvent.click(screen.getAllByRole("button", { expanded: false })[0]);
    expect(await screen.findByText("Statement doesn't add up")).toBeInTheDocument();
    expect(asked()).toBe(1);
  });

  it("asks for the run status again on the next opening when the first ask failed", async () => {
    let fail = true;
    const spy = vi.spyOn(globalThis, "fetch").mockImplementation(async (url) => {
      const u = String(url);
      if (u === "/api/run/status" && fail) return new Response(JSON.stringify({ error: "Busy" }), { status: 500 });
      return reply(u.startsWith("/api/uploads?") ? list : u === "/api/run/status" ? status : detail);
    });
    const asked = () => spy.mock.calls.filter(([u]) => String(u) === "/api/run/status").length;
    render(<PastUploads />);
    fireEvent.click((await screen.findAllByRole("button", { expanded: false }))[0]);
    expect(await screen.findByText("Statement doesn't add up")).toBeInTheDocument();
    await waitFor(() => expect(asked()).toBe(1));
    expect(screen.queryByText(/DBS Multiplier/)).not.toBeInTheDocument();
    // closed and opened again: the status is asked for once more, and the account is named this time
    fail = false;
    fireEvent.click(screen.getByRole("button", { expanded: true }));
    fireEvent.click(screen.getAllByRole("button", { expanded: false })[0]);
    expect(await screen.findByText(/DBS Multiplier/)).toBeInTheDocument();
    expect(asked()).toBe(2);
  });

  it("leads each step and status with its icon, and a statement file with its bank's mark (G18)", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (url) => {
      const u = String(url);
      const statusWithDbs = { ...status, folders: [...status.folders, { path: "statements/dbs", name: "dbs", parser: "dbs", wallet: "", title: "Dbs", bank: "dbs" }] };
      return reply(u.startsWith("/api/uploads?") ? list : u === "/api/run/status" ? statusWithDbs : detail);
    });
    render(<PastUploads />);
    const rows = await screen.findAllByRole("button", { expanded: false });
    // the closed row: each step's chip after the step's icon, the end after how it went
    const trail = (name: string) => rows[0].querySelector(`[data-step="${name}"] span`);
    expect(leadingIcon(trail("upload"))).toBe("file-plus");
    expect(leadingIcon(trail("load"))).toBe("database");
    expect(leadingIcon(trail("mirror"))).toBe("cloud-upload");
    expect(leadingIcon(within(rows[0]).getByText("Failed at load"))).toBe("circle-x");
    expect(leadingIcon(within(rows[1]).getByText("Loaded"))).toBe("circle-check");
    fireEvent.click(rows[0]);
    expect(leadingIcon(await screen.findByText("Passed"))).toBe("circle-check");
    expect(leadingIcon(screen.getByText("Blocked"))).toBe("octagon-x");
    expect(leadingIcon(screen.getAllByText("Check").find((el) => el.closest("li")?.textContent?.includes("Passed"))!)).toBe("search-check");
    const file = screen.getByText("statements/dbs/2026/2026-09.pdf");
    expect(file.previousElementSibling?.querySelector("[data-bank]")).toHaveAttribute("data-bank", "dbs");
  });

  it("shows a step that did not run as an empty chip, not as faded text", async () => {
    serve();
    render(<PastUploads />);
    const rows = await screen.findAllByRole("button", { expanded: false });
    const mirror = rows[0].querySelector('[data-step="mirror"] span')!;
    expect(mirror).toHaveAttribute("title", "Not run");
    expect(mirror.className).toContain("border");
    expect(mirror.className).not.toContain("opacity");
    expect(within(rows[0]).getByText("not run")).toHaveClass("sr-only");
  });

  it("pages by number: asks the server for the page and shows 1 to 25 of the total", async () => {
    window.localStorage.clear();
    const many = Array.from({ length: 25 }, (_, i) => ({ ...list.attempts[1], id: 100 - i }));
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(async (url) =>
      reply(String(url).startsWith("/api/uploads?") ? { attempts: many, next: 76, total: 60 } : detail));
    render(<PastUploads />);
    expect(await screen.findByText("1 to 25 of 60")).toBeInTheDocument();
    expect(fetchSpy.mock.calls.map(([u]) => u)).toContain("/api/uploads?limit=25&offset=0");
    fireEvent.click(screen.getByRole("button", { name: "Next page" }));
    expect(await screen.findByText("26 to 50 of 60")).toBeInTheDocument();
    expect(fetchSpy.mock.calls.map(([u]) => u)).toContain("/api/uploads?limit=25&offset=25");
    // the old "Show older uploads" button is gone
    expect(screen.queryByRole("button", { name: "Show older uploads" })).not.toBeInTheDocument();
  });

  it("says so when there is no history yet, with a way to start one", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async () => reply({ attempts: [], next: null, total: 0 }));
    render(<PastUploads />);
    expect(await screen.findByText(/No uploads yet/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Upload statements" })).toHaveAttribute("href", "/upload");
  });
});
