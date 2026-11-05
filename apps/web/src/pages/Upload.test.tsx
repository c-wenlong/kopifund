import "@testing-library/jest-dom/vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { UploadPage } from "./Upload";
import { leadingIcon } from "../test-icons";
import type { CheckReport, LoadResult } from "../api";

const status = {
  folders: [
    { path: "statements/dbs-multiplier", name: "dbs-multiplier", parser: "dbs", wallet: "", title: "DBS Multiplier", bank: "dbs" },
    { path: "statements/youtrip-sgd", name: "youtrip-sgd", parser: "youtrip", wallet: "SGD", title: "YouTrip", bank: "youtrip" },
    { path: "statements/youtrip-sek", name: "youtrip-sek", parser: "youtrip", wallet: "SEK", title: "YouTrip", bank: "youtrip" },
  ],
  notion_enabled: false, pdftotext: true, runs: [] as { kind: string; started_at: string; status: string; inserted: number; updated: number; archived: number }[],
};

const report = (over: Partial<CheckReport> = {}): CheckReport => ({
  problems: [], statements: 129, rows: 4693, to_add: [], to_add_count: 0, recategorised_count: 0, to_archive_count: 0, needs_rule: [], ...over,
});
const row = (key: string, over = {}) => ({ key, date: "2026-09-03", amount: -1048, currency: "SGD", account: "", text: "GRAB*", payee: "Grab", category: "Transport", ...over });
const duplicate = {
  code: "duplicate-period",
  subject: "•• 1111 · SGD · 2026-01: statements/dbs-multiplier/2026/2026-01-extra.pdf, statements/dbs-multiplier/2026/2026-01.pdf",
  fix: "two files parse to the same account-month",
};
const unbalanced = {
  code: "block-not-reconciled", subject: "•• 1111 · SGD · 2026-02",
  fix: "the balance chain in statements/dbs-multiplier/2026/2026-02.pdf does not close",
};

function reply(body: unknown, code = 200) {
  return new Response(JSON.stringify(body), { status: code, headers: { "Content-Type": "application/json" } });
}

type Routes = {
  status?: unknown; latest?: unknown; history?: unknown;
  check?: () => Promise<unknown> | unknown; load?: () => Promise<unknown> | unknown; mirror?: unknown;
};

const UNTOLD = "This reads as more than one kind of statement";
const UNSUPPORTED = "Kopifund does not read this statement yet, so nothing was kept";
// G21: what the server sends with a statement no layout reads, from the layout registry
const BANKS = {
  supported: [
    { bank: "dbs", name: "DBS", status: "checked on a real statement" },
    { bank: "ocbc", name: "OCBC", status: "from a public sample" },
    { bank: "youtrip", name: "YouTrip", status: "checked on a real statement" },
  ],
  coming_soon: [{ bank: "trust", name: "Trust Bank" }],
};
const TWO = "This is a YouTrip SEK statement, and more than one of your accounts prints its number";

/**
 * The server, by URL: a check, a load, the history and the statements, each as the test sets it. A PDF sent
 * to /api/statements/auto goes where its name says: dup.pdf is already there, bad.txt is no PDF, odd.pdf
 * reads as no statement, two.pdf could be either YouTrip wallet, and anything else is a DBS Multiplier one.
 * One sent to a folder is kept there, but odd.pdf, which no parser reads, is refused.
 */
function serve(routes: Routes = {}) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = String(input);
    if (url === "/api/run/status") return reply(routes.status ?? status);
    if (url === "/api/uploads/start") return reply({ attempt: 7 });
    if (url.startsWith("/api/uploads?")) return reply(routes.latest ?? { attempts: [], total: 0, next: null });
    if (url.startsWith("/api/uploads/")) return reply(typeof routes.history === "function" ? (routes.history as (u: string) => unknown)(url) : routes.history ?? {});
    if (url.startsWith("/api/run/check")) return reply(await (routes.check?.() ?? report()));
    if (url.startsWith("/api/run/load")) return reply(await (routes.load?.() ?? { loaded: true, report: report(), counts: { inserted: 0, updated: 0, archived: 0 } }));
    if (url.startsWith("/api/run/mirror")) return reply(routes.mirror ?? { code: 0, output: "synced" });
    const q = new URL(url, "http://x").searchParams;
    const name = q.get("name");
    if (name === "bad.txt") return reply({ error: "that file is not a PDF" }, 400);
    if (url.startsWith("/api/statements/auto")) {
      if (name === "dup.pdf") return reply({ status: "already-there", message: "You already have this statement", account_folder: "statements/dbs-multiplier" });
      if (name === "odd.pdf") return reply({ status: "needs-account", message: UNTOLD, kind: "", currency: "", candidates: [] });
      if (name === "acme.pdf" || name === "acme-2.pdf") return reply({ status: "not-supported", message: UNSUPPORTED, attempt: 7, ...BANKS });
      if (name === "two.pdf") return reply({ status: "needs-account", message: TWO, kind: "a YouTrip SEK statement", currency: "SEK", candidates: ["statements/youtrip-sgd", "statements/youtrip-sek"] });
      return reply({ status: "added", message: "Saved as statements/dbs-multiplier/2026/2026-09.pdf", account_folder: "statements/dbs-multiplier" });
    }
    if (url.startsWith("/api/statements?")) {
      if (name === "odd.pdf") return reply({ error: "This PDF is not a statement this account can read" }, 400);
      return reply({ status: "added", message: `Saved as ${q.get("folder")}/2026/2026-09.pdf`, account_folder: q.get("folder") });
    }
    return reply({ error: "none" }, 404);
  });
}

const leaveAsks = () => !window.dispatchEvent(new Event("beforeunload", { cancelable: true }));
const pdf = (name: string) => new File(["%PDF"], name, { type: "application/pdf" });
const stepper = () => within(screen.getByRole("list", { name: "Steps" }));
const stateOf = (key: string) => stepper().getAllByRole("listitem").find((li) => li.getAttribute("data-step") === key)?.getAttribute("data-state");
const sent = (spy: ReturnType<typeof serve>, start: string) => spy.mock.calls.map(([u]) => String(u)).filter((u) => u.startsWith(start));

// A step's button is on the page before it can be used: Mirror shows, disabled, while the load runs. A person
// waits for it to be enabled, and so does a test, or the click lands on a disabled button and does nothing.
async function press(name: string) {
  const button = await screen.findByRole("button", { name });
  await waitFor(() => expect(button).toBeEnabled());
  fireEvent.click(button);
}

async function add(...names: string[]) {
  fireEvent.change(await screen.findByLabelText("Choose PDFs", { selector: "input" }), { target: { files: names.map(pdf) } });
}

describe("Upload page", () => {
  beforeEach(() => { window.localStorage.clear(); window.history.replaceState(null, "", "/upload"); });
  afterEach(() => vi.restoreAllMocks());

  it("takes PDFs at once with no account chosen: each goes where its own text says, and the check runs by itself", async () => {
    const fetchSpy = serve({ check: () => report({ to_add_count: 3, to_add: [row("a")] }) });
    render(<UploadPage />);
    expect(await screen.findByLabelText("Choose PDFs", { selector: "input" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "Account" })).not.toBeInTheDocument();
    await add("new.pdf", "dup.pdf");
    await screen.findByText(/Saved as/);
    // the visit's attempt was started first and every step carries it
    expect(sent(fetchSpy, "/api/statements")).toEqual([
      "/api/statements/auto?name=new.pdf&attempt=7&from=upload",
      "/api/statements/auto?name=dup.pdf&attempt=7&from=upload",
    ]);
    expect(fetchSpy.mock.calls.filter(([u]) => u === "/api/uploads/start")).toHaveLength(1);
    // each row names the account the file went to
    const list = within(screen.getByRole("list", { name: "Uploaded files" }));
    expect(list.getAllByText("DBS Multiplier")).toHaveLength(2);
    // a new file was kept, so the check ran once by itself; the load waits for a tap
    await waitFor(() => expect(stateOf("check")).toBe("done"));
    expect(sent(fetchSpy, "/api/run/check")).toHaveLength(1);
    const loadButton = screen.getByRole("button", { name: "Load 3 new rows" });
    expect(sent(fetchSpy, "/api/run/load")).toHaveLength(0);
    expect(screen.getAllByRole("button", { name: /^Load/ })).toHaveLength(1);
    fireEvent.click(loadButton);
    await waitFor(() => expect(sent(fetchSpy, "/api/run/load")).toHaveLength(1));
  });

  it("checks nothing when no file was kept", async () => {
    const fetchSpy = serve();
    render(<UploadPage />);
    await add("bad.txt");
    await screen.findByText("that file is not a PDF");
    expect(sent(fetchSpy, "/api/run/check")).toHaveLength(0);
  });

  it("opens fresh: every step waiting, every card says so", async () => {
    serve();
    render(<UploadPage />);
    await screen.findByLabelText("Choose PDFs", { selector: "input" });
    for (const key of ["add", "check", "load"]) expect(stateOf(key)).toBe("pending");
    expect(stepper().queryByText("Mirror")).not.toBeInTheDocument();   // Notion is off
    expect(screen.getByText("No files yet")).toBeInTheDocument();
    expect(screen.getAllByText("Not run")).toHaveLength(2);
    expect(screen.getByText("Run the check first.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Load/ })).not.toBeInTheDocument();
    expect(screen.getByText("or drop them here")).toBeInTheDocument();
    // the owner's no-hint rule: no sentence says what Check or Load will do before they have run
    expect(screen.queryByText(/Parses every statement/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Writes no transactions/)).not.toBeInTheDocument();
  });

  it("takes a file dropped on the zone", async () => {
    const fetchSpy = serve();
    render(<UploadPage />);
    fireEvent.drop(await screen.findByTestId("drop-zone"), { dataTransfer: { files: [pdf("dropped.pdf")] } });
    await screen.findByText("dropped.pdf");
    expect(sent(fetchSpy, "/api/statements")).toEqual(["/api/statements/auto?name=dropped.pdf&attempt=7&from=upload"]);
  });

  it("lists each file with what happened to it, a refusal in the danger colour with its reason", async () => {
    serve();
    render(<UploadPage />);
    await add("new.pdf", "dup.pdf", "bad.txt");
    const list = await screen.findByRole("list", { name: "Uploaded files" });
    await waitFor(() => expect(within(list).getAllByRole("listitem")).toHaveLength(3));
    const items = within(list).getAllByRole("listitem");
    const of = (name: string) => items.find((li) => li.textContent?.includes(name))!;
    expect(within(of("new.pdf")).getByText("Added")).toBeInTheDocument();
    expect(within(of("dup.pdf")).getByText("Already there")).toBeInTheDocument();
    expect(within(of("bad.txt")).getByText("Refused")).toBeInTheDocument();
    expect(within(of("bad.txt")).getByText("that file is not a PDF").parentElement).toHaveClass("text-danger");
    expect(within(of("dup.pdf")).getByText("You already have this statement").parentElement).not.toHaveClass("text-danger");
    // the card and the stepper agree: files were kept
    expect(stateOf("add")).toBe("done");
  });

  it("blocks the Add step when every file was refused", async () => {
    serve();
    render(<UploadPage />);
    await add("bad.txt");
    await screen.findByText("that file is not a PDF");
    expect(stateOf("add")).toBe("blocked");
    expect(screen.getAllByText("Refused").length).toBeGreaterThan(0);
  });

  describe("a statement no layout reads (G21)", () => {
    it("is named as not supported, with no account to pick, and lists the banks Kopifund reads once", async () => {
      const fetchSpy = serve();
      render(<UploadPage />);
      await add("acme.pdf", "acme-2.pdf");
      const list = within(await screen.findByRole("list", { name: "Uploaded files" }));
      await waitFor(() => expect(list.getAllByText("Not supported")).toHaveLength(2));
      expect(list.getAllByText(UNSUPPORTED)).toHaveLength(2);
      expect(list.getAllByText(UNSUPPORTED)[0].parentElement).not.toHaveClass("text-danger");
      expect(leadingIcon(list.getAllByText("Not supported")[0])).not.toBeNull();
      // nothing to choose and nothing more sent: the server kept nothing of it
      expect(screen.queryByRole("list", { name: "Files that need an account" })).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Save here" })).not.toBeInTheDocument();
      expect(sent(fetchSpy, "/api/statements?")).toHaveLength(0);
      expect(sent(fetchSpy, "/api/run/check")).toHaveLength(0);
      // the banks it reads, a public-sample one saying so, then the ones coming soon, each list shown once
      const reads = within(screen.getByRole("list", { name: "Banks Kopifund reads" }));
      const items = reads.getAllByRole("listitem");
      expect(items).toHaveLength(3);
      expect(items[0]).toHaveTextContent(/DBS$/);
      expect(items[1]).toHaveTextContent(/OCBC, from a public sample$/);
      expect(items[2]).toHaveTextContent(/YouTrip$/);
      expect(within(screen.getByRole("list", { name: "Banks coming soon" })).getByText(/Trust Bank/)).toBeInTheDocument();
      expect(screen.getAllByRole("list", { name: "Banks Kopifund reads" })).toHaveLength(1);
      expect(screen.getByText("python3 finance.py sample")).toBeInTheDocument();
      // the card and the stepper agree: nothing was kept
      expect(screen.getAllByText("Not supported").length).toBeGreaterThan(2);
      expect(stateOf("add")).toBe("blocked");
    });

    it("shows no bank list for files that went through", async () => {
      serve();
      render(<UploadPage />);
      await add("new.pdf", "odd.pdf");
      await screen.findByText(/Saved as/);
      expect(screen.queryByRole("list", { name: "Banks Kopifund reads" })).not.toBeInTheDocument();
      expect(screen.queryByText("python3 finance.py sample")).not.toBeInTheDocument();
    });
  });

  describe("a file whose text cannot say which account it is", () => {
    it("waits with its reason and a picker, and nothing is sent for it until Save", async () => {
      const fetchSpy = serve({ check: () => report({ to_add_count: 2 }) });
      render(<UploadPage />);
      await add("two.pdf");
      const waiting = within(await screen.findByRole("list", { name: "Files that need an account" }));
      expect(waiting.getByText("Needs an account")).toBeInTheDocument();
      expect(waiting.getByText(TWO)).toBeInTheDocument();
      expect(screen.getAllByText("Needs an account").length).toBeGreaterThan(1);   // the card says so too
      expect(sent(fetchSpy, "/api/statements")).toHaveLength(1);
      expect(sent(fetchSpy, "/api/run/check")).toHaveLength(0);
      const save = waiting.getByRole("button", { name: "Save here" });
      expect(save).toBeDisabled();

      // the accounts it could be come first
      fireEvent.click(waiting.getByRole("button", { name: "Account" }));
      expect(screen.getAllByRole("option").map((o) => o.textContent)).toEqual(["YouTrip", "DBS Multiplier"]);
      fireEvent.mouseDown(screen.getByRole("option", { name: /YouTrip/ }));
      // the currency the statement names is chosen already
      expect(waiting.getByRole("button", { name: "Currency" })).toHaveTextContent("SEK");
      fireEvent.click(save);

      await screen.findByText(/Saved as statements\/youtrip-sek/);
      expect(sent(fetchSpy, "/api/statements?")).toEqual(["/api/statements?folder=statements%2Fyoutrip-sek&name=two.pdf&attempt=7&from=upload"]);
      expect(screen.queryByRole("list", { name: "Files that need an account" })).not.toBeInTheDocument();
      expect(within(screen.getByRole("list", { name: "Uploaded files" })).getByText("YouTrip SEK")).toBeInTheDocument();
      // kept, so the check runs by itself
      await waitFor(() => expect(sent(fetchSpy, "/api/run/check")).toHaveLength(1));
      expect(await screen.findByRole("button", { name: "Load 2 new rows" })).toBeEnabled();
    });

    it("asks before leaving while a file waits for its account, and not once it is placed", async () => {
      serve({ check: () => report({ to_add_count: 2 }) });
      render(<UploadPage />);
      expect(await screen.findByLabelText("Choose PDFs", { selector: "input" })).toBeEnabled();
      expect(leaveAsks()).toBe(false);
      await add("two.pdf");
      const waiting = within(await screen.findByRole("list", { name: "Files that need an account" }));
      // the PDF is held in this page only: leaving would drop it with nothing saved
      expect(leaveAsks()).toBe(true);
      fireEvent.click(waiting.getByRole("button", { name: "Account" }));
      fireEvent.mouseDown(screen.getByRole("option", { name: /YouTrip/ }));
      fireEvent.click(waiting.getByRole("button", { name: "Save here" }));
      await screen.findByText(/Saved as statements\/youtrip-sek/);
      await waitFor(() => expect(leaveAsks()).toBe(false));
    });

    it("stays waiting with the reason in the danger colour when the account chosen refuses it", async () => {
      serve();
      render(<UploadPage />);
      await add("odd.pdf");
      const waiting = within(await screen.findByRole("list", { name: "Files that need an account" }));
      expect(waiting.getByText(UNTOLD)).not.toHaveClass("text-danger");
      fireEvent.click(waiting.getByRole("button", { name: "Account" }));
      fireEvent.mouseDown(screen.getByRole("option", { name: /DBS Multiplier/ }));
      fireEvent.click(waiting.getByRole("button", { name: "Save here" }));
      expect(await waiting.findByText("This PDF is not a statement this account can read")).toHaveClass("text-danger");
      expect(waiting.getByRole("button", { name: "Save here" })).toBeEnabled();
      expect(stateOf("add")).toBe("pending");
    });

    it("stays at the top of the list, never folded away under the files that went through", async () => {
      serve();
      render(<UploadPage />);
      await add("a.pdf", "b.pdf", "c.pdf", "d.pdf", "e.pdf", "odd.pdf");
      expect(await screen.findByText(UNTOLD)).toBeVisible();
      expect(screen.getByRole("list", { name: "Files that need an account" }).compareDocumentPosition(
        screen.getByRole("list", { name: "Uploaded files" }))).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
    });
  });

  describe("the check", () => {
    it("shows a running check with what it reads, and the count it will read next time", async () => {
      let finish: (r: CheckReport) => void = () => {};
      let calls = 0;
      serve({ check: () => (calls++ === 0 ? report() : new Promise((done) => { finish = done as (r: CheckReport) => void; })) });
      render(<UploadPage />);
      fireEvent.click(await screen.findByRole("button", { name: "Check statements" }));
      await waitFor(() => expect(stateOf("check")).toBe("done"));
      fireEvent.click(screen.getByRole("button", { name: "Check again" }));
      expect(await screen.findByText("Reading 129 statements")).toBeInTheDocument();
      expect(stateOf("check")).toBe("running");
      expect(screen.getByRole("button", { name: "Checking…" })).toBeDisabled();
      await act(async () => { finish(report()); });
      await waitFor(() => expect(stateOf("check")).toBe("done"));
    });

    it("names what a first check reads before it knows the count", async () => {
      serve({ check: () => new Promise(() => {}) });
      render(<UploadPage />);
      fireEvent.click(await screen.findByRole("button", { name: "Check statements" }));
      expect(await screen.findByText("Reading your statements")).toBeInTheDocument();
    });

    it("shows what it found as three tiles, and the to-add rows under a header", async () => {
      serve({ check: () => report({ to_add_count: 2, recategorised_count: 1, to_archive_count: 3,
        to_add: [row("a"), row("b", { currency: "SEK", amount: -1234567 })] }) });
      render(<UploadPage />);
      fireEvent.click(await screen.findByRole("button", { name: "Check statements" }));
      const tiles = within(await screen.findByRole("group", { name: "What the check found" }));
      expect(tiles.getByText("To add").nextSibling).toHaveTextContent("2");
      expect(tiles.getByText("Recategorised").nextSibling).toHaveTextContent("1");
      expect(tiles.getByText("To archive").nextSibling).toHaveTextContent("3");
      expect(screen.getByText("129 statements, 4,693 rows", { exact: false })).toBeInTheDocument();
      for (const h of ["Date", "Payee", "Category", "Amount"]) expect(screen.getByRole("columnheader", { name: h })).toBeInTheDocument();
      // amounts through the one formatter: a true minus, grouping, the currency in front
      expect(screen.getByText("−S$10.48")).toBeInTheDocument();
      expect(screen.getByText(/−SEK\s12,345\.67/)).toBeInTheDocument();
      expect(stateOf("check")).toBe("done");
      expect(screen.getByText("Every statement balances.")).toBeInTheDocument();
    });

    it("turns each problem into a card: plain headline, account and files, one next step, the code on a details line", async () => {
      serve({ check: () => report({ problems: [duplicate, unbalanced] }) });
      render(<UploadPage />);
      fireEvent.click(await screen.findByRole("button", { name: "Check statements" }));
      expect(await screen.findByText("Two files for the same month")).toBeInTheDocument();
      expect(screen.getByText("Statement doesn't add up")).toBeInTheDocument();
      expect(screen.getByText(/2026\/2026-01-extra\.pdf, 2026\/2026-01\.pdf/)).toBeInTheDocument();
      expect(screen.getAllByText(/DBS Multiplier/).length).toBeGreaterThan(0);
      expect(screen.getByText("duplicate-period")).toBeInTheDocument();
      expect(screen.getByText("block-not-reconciled")).toBeInTheDocument();
      // blockers wear the danger colour, never the amber of over-budget
      expect(screen.getByText("Two files for the same month")).toHaveClass("text-danger");
      expect(screen.getByText("Two files for the same month").closest("li")).toHaveClass("bg-danger-bg");
      expect(screen.getAllByText("2 problems")).toHaveLength(2);       // the chip and the stepper's note
      expect(stateOf("check")).toBe("blocked");
      expect(screen.getByText(/These counts are partial/)).toBeInTheDocument();
    });

    it("leads each step, status chip and problem's account with its icon or bank mark (G18)", async () => {
      serve({ check: () => report({ problems: [duplicate] }) });
      render(<UploadPage />);
      await screen.findByRole("button", { name: "Check statements" });
      // the stepper: each step's name after its icon
      const step = (key: string) => stepper().getAllByRole("listitem").find((li) => li.getAttribute("data-step") === key)!;
      expect(leadingIcon(within(step("add")).getByText("Add"))).toBe("file-plus");
      expect(leadingIcon(within(step("check")).getByText("Check"))).toBe("search-check");
      expect(leadingIcon(within(step("load")).getByText("Load"))).toBe("database");
      fireEvent.click(await screen.findByRole("button", { name: "Check statements" }));
      const chip = (await screen.findAllByText("1 problem")).find((el) => el.tagName === "SPAN" && el.className.includes("rounded-full"))!;
      expect(leadingIcon(chip)).toBe("triangle-alert");
      // the problem names its account by the bank's mark, then the name
      const where = screen.getByText("DBS Multiplier");
      expect(where.firstElementChild).toHaveAttribute("data-bank", "dbs");
      expect(where.parentElement).toHaveTextContent("DBS Multiplier, Jan 2026");
    });

    it("leads each uploaded file's chip with how it went (G18)", async () => {
      serve();
      render(<UploadPage />);
      await add("new.pdf", "dup.pdf");
      const list = await screen.findByRole("list", { name: "Uploaded files" });
      await waitFor(() => expect(within(list).getAllByRole("listitem")).toHaveLength(2));
      expect(leadingIcon(within(list).getByText("Added"))).toBe("circle-check");
      expect(leadingIcon(within(list).getByText("Already there"))).toBe("file-check");
    });

    it("tells Load why it is off: problems to fix, or nothing new", async () => {
      let next: CheckReport = report({ problems: [duplicate, unbalanced] });
      serve({ check: () => next });
      render(<UploadPage />);
      fireEvent.click(await screen.findByRole("button", { name: "Check statements" }));
      expect(await screen.findByText("Fix 2 problems above first.")).toBeInTheDocument();
      expect(screen.queryByText("Run the check first.")).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /^Load/ })).not.toBeInTheDocument();
      next = report({ problems: [duplicate] });
      fireEvent.click(screen.getByRole("button", { name: "Check again" }));
      expect(await screen.findByText("Fix 1 problem above first.")).toBeInTheDocument();
      next = report();
      fireEvent.click(screen.getByRole("button", { name: "Check again" }));
      expect(await screen.findByText(/^Nothing new to load\./)).toBeInTheDocument();
      // Load stays on: it also records a statement with no transactions
      expect(screen.getByRole("button", { name: "Load" })).toBeEnabled();
      expect(stateOf("load")).toBe("done");
    });
  });

  describe("the load", () => {
    const checked = () => report({ to_add_count: 5, recategorised_count: 2, to_archive_count: 1, to_add: [row("a")] });
    const loaded = (): LoadResult => ({ loaded: true, report: checked(), counts: { inserted: 5, updated: 2, archived: 1, "trips assigned": 0 }, stats: { stats: 15, transactions: 4693 } });

    it("is a success state with the counts, two links and no more loading until the next check", async () => {
      status.runs = [{ kind: "load", started_at: "2026-10-01T06:00:00+00:00", status: "ok", inserted: 9, updated: 9, archived: 0 }];
      serve({ check: checked, load: loaded });
      render(<UploadPage />);
      fireEvent.click(await screen.findByRole("button", { name: "Check statements" }));
      // no hint sentence about what Load does: the button says it
      expect(await screen.findByRole("button", { name: "Load 5 new rows" })).toBeEnabled();
      expect(screen.queryByText(/Writes the/)).not.toBeInTheDocument();
      // the old footnote is on the idle Load card, and goes when the new result replaces it
      expect(screen.getByText(/Last load .*: 9 added, 9 updated\./)).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "Load 5 new rows" }));

      const tiles = within(await screen.findByRole("group", { name: "What the load did" }));
      expect(tiles.getByText("Added").nextSibling).toHaveTextContent("5");
      expect(tiles.getByText("Updated").nextSibling).toHaveTextContent("2");
      expect(tiles.getByText("Archived").nextSibling).toHaveTextContent("1");
      expect(screen.getByText("15 stats rerun.")).toBeInTheDocument();
      expect(screen.getByRole("link", { name: "See the new transactions" })).toHaveAttribute("href", "/transactions");
      expect(screen.getByRole("link", { name: "Home" })).toHaveAttribute("href", "/");
      expect(screen.queryByText(/Last load/)).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /^Load/ })).not.toBeInTheDocument();
      expect(stateOf("load")).toBe("done");
      expect(screen.getByText("5 added")).toBeInTheDocument();      // the stepper's note
      // the Check card keeps what the check found; it is not reset to "0 to add"
      const found = within(screen.getByRole("group", { name: "What the check found" }));
      expect(found.getByText("To add").nextSibling).toHaveTextContent("5");

      // Load stays off until the next check; a new check turns it back to its idle words
      status.runs = [];
      fireEvent.click(screen.getByRole("button", { name: "Check again" }));
      await waitFor(() => expect(screen.queryByRole("link", { name: "Home" })).not.toBeInTheDocument());
      expect(screen.getByRole("button", { name: "Load 5 new rows" })).toBeEnabled();
    });

    it("shows what a load is writing while it runs", async () => {
      let finish: (r: LoadResult) => void = () => {};
      serve({ check: checked, load: () => new Promise((done) => { finish = done as (r: LoadResult) => void; }) });
      render(<UploadPage />);
      fireEvent.click(await screen.findByRole("button", { name: "Check statements" }));
      fireEvent.click(await screen.findByRole("button", { name: "Load 5 new rows" }));
      expect(await screen.findByText("Writing 5 new transactions, then rerunning the stats")).toBeInTheDocument();
      expect(stateOf("load")).toBe("running");
      await act(async () => { finish(loaded()); });
      await screen.findByRole("link", { name: "Home" });
    });

    it("says nothing was loaded when the server refused", async () => {
      const refused = report({ problems: [unbalanced], to_add_count: 1 });
      serve({ check: () => report({ to_add_count: 1 }), load: () => ({ loaded: false, report: refused }) });
      render(<UploadPage />);
      fireEvent.click(await screen.findByRole("button", { name: "Check statements" }));
      fireEvent.click(await screen.findByRole("button", { name: "Load 1 new row" }));
      expect(await screen.findByText("The check found problems, so nothing was loaded.")).toBeInTheDocument();
      expect(screen.getByText("Statement doesn't add up")).toBeInTheDocument();
      expect(stateOf("load")).toBe("blocked");
    });
  });

  describe("a visit after an earlier run", () => {
    const at = "2026-10-12T06:00:00+00:00";
    const outline = { id: 9, origin: "upload", started_at: at, steps: [] };
    const step = (id: number, name: string, st: string, output: unknown) =>
      ({ id, step: name, status: st, summary: "", started_at: at, finished_at: at, input: {}, output });

    it("shows the last load as the state of the month, not a fresh start", async () => {
      serve({
        latest: { attempts: [outline], total: 1, next: null },
        history: { ...outline, steps: [step(1, "check", "ok", report({ to_add_count: 4 })), step(2, "load", "ok", {
          checked: { statements: 129, rows: 4693, to_add_count: 4, recategorised_count: 0, to_archive_count: 0 },
          counts: { inserted: 4, updated: 0, archived: 0, "trips assigned": 0 }, stats: { stats: 15, transactions: 4693 }, added: [row("a")] })] },
      });
      render(<UploadPage />);
      expect(await screen.findByRole("link", { name: "Home" })).toBeInTheDocument();
      expect(stateOf("load")).toBe("done");
      expect(stateOf("check")).toBe("done");
      expect(screen.getByText("Loaded")).toBeInTheDocument();
      expect(screen.queryByText("Run the check first.")).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /^Load/ })).not.toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Check again" })).toBeInTheDocument();
    });

    it("shows the last check's problems, and what to do about them", async () => {
      serve({
        latest: { attempts: [outline], total: 1, next: null },
        history: { ...outline, steps: [step(1, "check", "problems", report({ problems: [duplicate] }))] },
      });
      render(<UploadPage />);
      expect(await screen.findByText("Two files for the same month")).toBeInTheDocument();
      expect(stateOf("check")).toBe("blocked");
      expect(screen.getByText("Fix 1 problem above first.")).toBeInTheDocument();
    });

    it("counts the payees a restored check had no rule for, since their text is not kept", async () => {
      serve({
        latest: { attempts: [outline], total: 1, next: null },
        history: { ...outline, steps: [step(1, "check", "ok", report({ to_add_count: 3,
          needs_rule: [{ text: "x", field: "merchant", rows: 1, amount: 1, first: "2026-09-01", last: "2026-09-01", keys: [] }] }))] },
      });
      render(<UploadPage />);
      expect(await screen.findByText(/1 payee had no rule. Check again to name it./)).toBeInTheDocument();
    });

    it("finds the check and load in the attempt before a visit that only mirrored", async () => {
      const mirrorOnly = { id: 10, origin: "upload", started_at: at, steps: [step(5, "mirror", "ok", { code: 0, output: "synced" })] };
      const earlier = { ...outline, steps: [step(1, "check", "ok", report({ to_add_count: 2 })), step(2, "load", "ok", {
        checked: { statements: 129, rows: 4693, to_add_count: 2, recategorised_count: 0, to_archive_count: 0 },
        counts: { inserted: 2, updated: 0, archived: 0, "trips assigned": 0 }, stats: { stats: 15, transactions: 4693 }, added: [] })] };
      serve({
        status: { ...status, notion_enabled: true },
        latest: { attempts: [{ id: 10 }, { id: 9 }], total: 2, next: null },
        history: (url: string) => (url.endsWith("/10") ? mirrorOnly : earlier),
      });
      render(<UploadPage />);
      expect(await screen.findByRole("link", { name: "Home" })).toBeInTheDocument();
      expect(stateOf("load")).toBe("done");
      await waitFor(() => expect(stateOf("mirror")).toBe("done"));
    });

    it("still restores the latest attempt when the one before it cannot be read", async () => {
      serve({
        latest: { attempts: [{ id: 9 }, { id: 8 }], total: 2, next: null },
        history: (url: string) => (url.endsWith("/9") ? { ...outline, steps: [step(1, "check", "problems", report({ problems: [duplicate] }))] } : { error: "gone" }),
      });
      const real = globalThis.fetch;
      vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) =>
        String(input).endsWith("/api/uploads/8") ? reply({ error: "gone" }, 500) : real(input, init));
      render(<UploadPage />);
      expect(await screen.findByText("Two files for the same month")).toBeInTheDocument();
      expect(stateOf("check")).toBe("blocked");
    });

    it("lets what is done on the page win over the history that arrives late", async () => {
      serve({
        latest: { attempts: [outline], total: 1, next: null },
        history: { ...outline, steps: [step(1, "check", "problems", report({ problems: [duplicate] }))] },
        check: () => report({ to_add_count: 1 }),
      });
      render(<UploadPage />);
      fireEvent.click(await screen.findByRole("button", { name: /Check/ }));
      await waitFor(() => expect(stateOf("check")).toBe("done"));
      expect(screen.queryByText("Two files for the same month")).not.toBeInTheDocument();
    });
  });

  it("shows the Mirror step and card only when Notion is on, and marks its result", async () => {
    serve({ status: { ...status, notion_enabled: true }, check: () => report({ to_add_count: 1 }),
      load: () => ({ loaded: true, report: report({ to_add_count: 1 }), counts: { inserted: 1, updated: 0, archived: 0, "trips assigned": 0 }, stats: { stats: 1, transactions: 1 } }),
      mirror: { code: 1, output: "boom" } });
    render(<UploadPage />);
    expect(await screen.findByText("4. Mirror to Notion (optional)")).toBeInTheDocument();
    expect(stateOf("mirror")).toBe("pending");
    expect(screen.getByRole("button", { name: "Mirror" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Check statements" }));
    fireEvent.click(await screen.findByRole("button", { name: "Load 1 new row" }));
    await press("Mirror");
    expect(await screen.findByText("boom")).toBeInTheDocument();
    expect(screen.getAllByText("Failed")).toHaveLength(2);
    expect(screen.queryAllByText("Mirrored")).toHaveLength(0);
    expect(stateOf("mirror")).toBe("blocked");
  });

  it("forgets a mirror once the record changes: a later load or a new file", async () => {
    const loadedResult = { loaded: true, report: report({ to_add_count: 1 }), counts: { inserted: 1, updated: 0, archived: 0, "trips assigned": 0 }, stats: { stats: 1, transactions: 1 } };
    serve({ status: { ...status, notion_enabled: true }, check: () => report({ to_add_count: 1 }), load: () => loadedResult });
    render(<UploadPage />);
    await press("Check statements");
    await press("Load 1 new row");
    await press("Mirror");
    await waitFor(() => expect(stateOf("mirror")).toBe("done"));
    // a second load changes the record, so the earlier mirror no longer counts
    await press("Check again");
    await press("Load 1 new row");
    await waitFor(() => expect(stateOf("load")).toBe("done"));
    expect(stateOf("mirror")).toBe("pending");
    await press("Mirror");
    await waitFor(() => expect(stateOf("mirror")).toBe("done"));
    // and so does a new file
    await add("new.pdf");
    await waitFor(() => expect(stateOf("mirror")).toBe("pending"));
  });

  it("lets Mirror run after a clean check with nothing to add, since the record is already current", async () => {
    serve({ status: { ...status, notion_enabled: true }, check: () => report() });
    render(<UploadPage />);
    expect(await screen.findByRole("button", { name: "Mirror" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Check statements" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Mirror" })).toBeEnabled());
  });

  it("explains the data quality chart in plain words, and what Synthetic means on its legend entry", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url === "/api/run/status") return reply(status);
      if (url.startsWith("/api/uploads")) return reply({ attempts: [], total: 0, next: null });
      if (url === "/api/results/data-quality") {
        return reply({ id: "data-quality", title: "Data quality", chart: { type: "bar", x: "month", series: ["uncategorised", "synthetic"], source: "by_month", unit: "count" },
          data: {
            // G20: a data-quality result in cents holds its money under `_minor`, here the money with no category
            counts: { rows: 200, uncategorised: 5, uncategorised_out_minor: 12000, synthetic: 1, unconverted: 0, partial_months: 0 },
            by_month: [{ month: "2026-08", uncategorised: 3, synthetic: 1 }, { month: "2026-09", uncategorised: 2, synthetic: 0 }],
          } });
      }
      return reply({ error: "none" }, 404);
    });
    render(<UploadPage />);
    expect(await screen.findByText(/stand-in rows that fill a gap in a YouTrip export/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Synthetic/ })).toHaveAttribute("title", expect.stringContaining("stand-in row Kopifund adds when a YouTrip export leaves out a balance change"));
  });

  describe("opened from Your profile's Add statement (?account=)", () => {
    afterEach(() => window.history.replaceState(null, "", "/"));

    it("still lets a file go where its own text says", async () => {
      window.history.replaceState(null, "", "/upload?account=youtrip&currency=sek");
      const fetchSpy = serve();
      render(<UploadPage />);
      await add("new.pdf");
      await screen.findByText(/Saved as/);
      expect(sent(fetchSpy, "/api/statements")).toEqual(["/api/statements/auto?name=new.pdf&attempt=7&from=upload"]);
    });

    it("has that account, and its currency, chosen for a file that needs one", async () => {
      window.history.replaceState(null, "", "/upload?account=youtrip&currency=sek");
      const fetchSpy = serve();
      render(<UploadPage />);
      await add("odd.pdf");
      const waiting = within(await screen.findByRole("list", { name: "Files that need an account" }));
      expect(waiting.getByRole("button", { name: "Account" })).toHaveTextContent("YouTrip");
      expect(waiting.getByRole("button", { name: "Currency" })).toHaveTextContent("SEK");
      // still the owner's tap that saves it
      expect(sent(fetchSpy, "/api/statements?")).toHaveLength(0);
      fireEvent.click(waiting.getByRole("button", { name: "Save here" }));
      await waitFor(() => expect(sent(fetchSpy, "/api/statements?")).toEqual(["/api/statements?folder=statements%2Fyoutrip-sek&name=odd.pdf&attempt=7&from=upload"]));
    });

    it("takes the account by its folder name, and chooses nothing for one it does not know", async () => {
      window.history.replaceState(null, "", "/upload?account=statements%2Fdbs-multiplier");
      serve();
      const first = render(<UploadPage />);
      await add("odd.pdf");
      expect(await screen.findByRole("button", { name: "Account" })).toHaveTextContent("DBS Multiplier");
      first.unmount();
      window.history.replaceState(null, "", "/upload?account=statements%2Fnowhere");
      render(<UploadPage />);
      await add("odd.pdf");
      expect(await screen.findByRole("button", { name: "Account" })).toHaveTextContent("Choose the account");
    });

    it("chooses nothing when the file's own text points at other accounts", async () => {
      window.history.replaceState(null, "", "/upload?account=dbs-multiplier");
      serve();
      render(<UploadPage />);
      await add("two.pdf");
      expect(await screen.findByRole("button", { name: "Account" })).toHaveTextContent("Choose the account");
    });
  });
});
