import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { revalidate } from "../api";
import { Profile, ageOn, freshness } from "./Profile";

const profile = {
  data_folder: "/Users/x/.kopifund", notion_enabled: false, about: { birth_date: null },
  accounts: [], folders: [], transactions: 0,
};

const reply = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

// G18: true when the page would hold a reload or a closed tab behind the browser's question
const leaveAsks = () => !window.dispatchEvent(new Event("beforeunload", { cancelable: true }));

describe("Profile", () => {
  afterEach(() => vi.restoreAllMocks());

  it("shows five accounts and a row that opens the rest", async () => {
    const accounts = Array.from({ length: 7 }, (_, i) => ({
      name: `Account ${i + 1}`, bank: "OCBC", number: "", statements: 3, transactions: 40, first: "2026-01-01", last: "2026-06-30",
    }));
    vi.spyOn(globalThis, "fetch").mockImplementation(async () => reply({ ...profile, accounts }));
    render(<Profile />);
    await screen.findByText("Account 1");
    expect(screen.queryByText("Account 6")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Show all 7" }));
    expect(screen.getByText("Account 7")).toBeInTheDocument();
  });

  it("counts completed years", () => {
    expect(ageOn("2000-08-03", new Date(2026, 7, 2))).toBe(25);
    expect(ageOn("2000-08-03", new Date(2026, 7, 3))).toBe(26);
  });

  it("saves the date of birth with the write header", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(async (_url, init) =>
      init?.method === "POST" ? reply({ about: { birth_date: "2000-08-03" } }) : reply(profile));
    render(<Profile />);
    const input = await screen.findByLabelText("Date of birth");
    expect(leaveAsks()).toBe(false);
    fireEvent.change(input, { target: { value: "2000-08-03" } });
    expect(leaveAsks()).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText("Saved");
    expect(leaveAsks()).toBe(false);
    const post = fetchSpy.mock.calls.find(([, init]) => init?.method === "POST")!;
    expect(post[0]).toBe("/api/profile");
    expect((post[1] as RequestInit).headers).toMatchObject({ "X-Kopifund": "1" });
    expect(JSON.parse(String((post[1] as RequestInit).body))).toEqual({ birth_date: "2000-08-03" });
  });

  it("shows the date of birth in the app's format and reads it typed day first", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async () => reply({ ...profile, about: { birth_date: "2000-08-03" } }));
    render(<Profile />);
    const input = await screen.findByLabelText("Date of birth");
    expect(input).toHaveValue("3 Aug 2000");
    fireEvent.change(input, { target: { value: "4/8/1999" } });
    fireEvent.blur(input);
    expect(input).toHaveValue("4 Aug 1999");
    expect(screen.getByRole("button", { name: "Save" })).toBeEnabled();
  });

  it("jumps to a year from a dropdown instead of paging back month by month", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (_url, init) =>
      init?.method === "POST" ? reply({ about: { birth_date: "1995-03-01" } }) : reply(profile));
    render(<Profile />);
    fireEvent.click(await screen.findByLabelText("Date of birth"));
    const cal = within(screen.getByRole("dialog", { name: "Date of birth calendar" }));
    const years = cal.getByRole("combobox", { name: /year/i }) as HTMLSelectElement;
    // an empty date opens a few decades back, and the dropdown reaches the 1900s
    expect(Number(years.value)).toBeLessThan(new Date().getFullYear() - 20);
    expect(years.options[0].value).toBe("1900");
    fireEvent.change(years, { target: { value: "1995" } });
    fireEvent.click(cal.getByRole("button", { name: /January 12th, 1995/ }));
    expect(screen.getByLabelText("Date of birth")).toHaveValue("12 Jan 1995");
  });

  it("clears a saved date of birth and saves it empty", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(async (_url, init) =>
      init?.method === "POST" ? reply({ about: { birth_date: null } }) : reply({ ...profile, about: { birth_date: "2000-08-03" } }));
    render(<Profile />);
    const input = await screen.findByLabelText("Date of birth");
    expect(input).toHaveValue("3 Aug 2000");
    fireEvent.click(screen.getByRole("button", { name: "Clear Date of birth" }));
    expect(input).toHaveValue("");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText("Saved");
    const post = fetchSpy.mock.calls.find(([, init]) => init?.method === "POST")!;
    expect(JSON.parse(String((post[1] as RequestInit).body))).toEqual({ birth_date: null });
  });

  it("disables the date while saving, so the confirmation is always for what is shown", async () => {
    let finish: (r: Response) => void = () => {};
    vi.spyOn(globalThis, "fetch").mockImplementation((_url, init) =>
      init?.method === "POST" ? new Promise<Response>((res) => { finish = res; }) : Promise.resolve(reply(profile)));
    render(<Profile />);
    const input = await screen.findByLabelText("Date of birth");
    fireEvent.change(input, { target: { value: "2000-08-03" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(input).toBeDisabled();
    finish(reply({ about: { birth_date: "2000-08-03" } }));
    await screen.findByText("Saved");
    expect(input).toBeEnabled();
    fireEvent.change(input, { target: { value: "2000-08-04" } });
    expect(screen.queryByText("Saved")).not.toBeInTheDocument();
  });

  it("says when the stored file is damaged", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
      reply({ ...profile, about: { birth_date: null, problem: "profile.json is damaged (not a JSON object); saving replaces it and keeps a copy" } }));
    render(<Profile />);
    expect(await screen.findByRole("alert")).toHaveTextContent("damaged");
    expect(screen.getByRole("button", { name: "Save" })).toBeEnabled();
  });

  // G13: invented accounts, as GET /api/profile sends them (names and masks only, the upload key and PDF count of the folder each belongs to)
  const withAccounts = {
    ...profile,
    data_folder: "/Users/x/.kopifund",
    transactions: 1240,
    accounts: [
      { name: "Travel wallet", bank: "youtrip", number: "•• 3210", statements: 12, transactions: 1200, first: "2025-01-03", last: "2026-09-30",
        folder: "statements/youtrip", pdfs: 12 },
      { name: "Old saver", bank: "dbs", number: "•• 4455", statements: 3, transactions: 30, first: "2025-01-01", last: "2026-07-31",
        folder: "statements/dbs-saver", pdfs: 3 },
      { name: "Tiger Brokers", bank: "tiger", number: "", statements: 2, transactions: 0, first: "2024-01-01", last: "2026-08-31",
        folder: "statements/tiger", pdfs: 2 },
      { name: "Joint saver", bank: "other", number: "•• 7888", statements: 0, transactions: 0, first: null, last: null, folder: null, pdfs: null },
    ],
    folders: [
      { path: "statements/youtrip-sgd", name: "youtrip-sgd", parser: "youtrip", wallet: "SGD", title: "YouTrip", bank: "youtrip", statements: 8 },
      { path: "statements/youtrip-sek", name: "youtrip-sek", parser: "youtrip", wallet: "SEK", title: "YouTrip", bank: "youtrip", statements: 4 },
      { path: "statements/dbs-saver", name: "dbs-saver", parser: "dbs", wallet: "", title: "Dbs Saver", bank: "dbs", statements: 3 },
      { path: "statements/tiger", name: "tiger", parser: "tiger", wallet: "", title: "Tiger Brokers", bank: "tiger", statements: 2 },
      { path: "statements/posb-everyday", name: "posb-everyday", parser: "dbs", wallet: "", title: "POSB Everyday", bank: "posb", statements: 1 },
    ],
  };

  // today is 17 Oct 2026 in these tests, so the last month a statement can cover is Sep 2026
  const today = () => vi.useFakeTimers({ toFake: ["Date"], now: new Date(2026, 9, 17, 12) });
  afterEach(() => vi.useRealTimers());

  it("lists accounts by bank mark, name and the last four digits, with what the statements cover", async () => {
    today();
    vi.spyOn(globalThis, "fetch").mockImplementation(async () => reply(withAccounts));
    render(<Profile />);
    const row = (await screen.findByText("Travel wallet")).closest("li")!;
    expect(row).toHaveTextContent("•• 3210");
    expect(row).toHaveTextContent("3 Jan 2025 to 30 Sep 2026");
    expect(row).toHaveTextContent("12 statements");
    expect(row).toHaveTextContent("1,200 rows");
    expect(row.querySelector('[data-bank="youtrip"]')).not.toBeNull();
    // a brokerage with statements but no ledger rows is not waiting
    expect(screen.getByText("Tiger Brokers").closest("li")).not.toHaveTextContent("No statements yet");
  });

  it("says in a chip whether each account's statements reach last month, and which months are missing", async () => {
    today();
    vi.spyOn(globalThis, "fetch").mockImplementation(async () => reply(withAccounts));
    render(<Profile />);
    const chip = async (name: string) => within((await screen.findByText(name)).closest("li")!);
    expect((await chip("Travel wallet")).getByText("Up to date")).toBeInTheDocument();
    // last statement ends Jul 2026: Aug and Sep are missing
    expect((await chip("Old saver")).getByText("Missing Aug to Sep 2026")).toBeInTheDocument();
    // Aug 2026 is the last month covered: only Sep is missing
    expect((await chip("Tiger Brokers")).getByText("Missing Sep 2026")).toBeInTheDocument();
    expect((await chip("Joint saver")).getByText("No statements yet")).toBeInTheDocument();
  });

  it("works out freshness from the last statement's month, a year at a time for CPF", () => {
    const now = new Date(2026, 9, 17);
    expect(freshness({ bank: "dbs", last: "2026-09-01" }, now)).toEqual({ ok: true, text: "Up to date" });
    expect(freshness({ bank: "dbs", last: "2026-10-05" }, now)).toEqual({ ok: true, text: "Up to date" });
    expect(freshness({ bank: "dbs", last: "2026-08-31" }, now)).toEqual({ ok: false, text: "Missing Sep 2026" });
    expect(freshness({ bank: "dbs", last: "2025-11-30" }, now)).toEqual({ ok: false, text: "Missing Dec 2025 to Sep 2026" });
    expect(freshness({ bank: "dbs", last: null }, now)).toEqual({ ok: false, text: "No statements yet" });
    expect(freshness({ bank: "cpf", last: "2025-12-31" }, now)).toEqual({ ok: true, text: "Up to date" });
    expect(freshness({ bank: "cpf", last: "2024-12-31" }, now)).toEqual({ ok: false, text: "Missing 2025" });
  });

  it("links each account to the upload page with that account chosen", async () => {
    today();
    vi.spyOn(globalThis, "fetch").mockImplementation(async () => reply(withAccounts));
    render(<Profile />);
    const link = await screen.findByRole("link", { name: "Add statement for Travel wallet" });
    expect(link).toHaveTextContent("Add statement");
    expect(link).toHaveAttribute("href", "/upload?account=statements%2Fyoutrip");
    expect(screen.getByRole("link", { name: "Add statement for Tiger Brokers" })).toHaveAttribute("href", "/upload?account=statements%2Ftiger");
    // an account with no statement folder has nowhere to add one
    expect(within(screen.getByText("Joint saver").closest("li")!).queryByRole("link")).toBeNull();
  });

  it("puts the statement folders in the accounts: the PDF count is a detail, and a folder no account has yet is still listed", async () => {
    today();
    vi.spyOn(globalThis, "fetch").mockImplementation(async () => reply(withAccounts));
    render(<Profile />);
    expect(screen.queryByRole("heading", { name: "Statement folders" })).not.toBeInTheDocument();
    const travel = (await screen.findByText("Travel wallet")).closest("li")!;
    expect(travel).toHaveTextContent("12 PDFs");
    // POSB's folder holds a PDF and no account has claimed it
    const posb = screen.getByText("POSB Everyday").closest("li")!;
    expect(posb).toHaveTextContent("1 PDF");
    expect(posb).toHaveTextContent("Not loaded yet");
    expect(posb.querySelector('[data-bank="posb"]')).not.toBeNull();
    expect(posb.getAttribute("title")).toBe("statements/posb-everyday");
    expect(within(posb).getByRole("link", { name: "Add statement for POSB Everyday" })).toHaveAttribute("href", "/upload?account=statements%2Fposb-everyday");
    expect(screen.queryByText(/statements\/posb/)).toBeNull();
  });

  it("lists accounts with a statement first and those still waiting last", async () => {
    today();
    vi.spyOn(globalThis, "fetch").mockImplementation(async () => reply(withAccounts));
    render(<Profile />);
    await screen.findByText("Travel wallet");
    const names = screen.getAllByRole("listitem").map((li) => li.querySelector("span.truncate")?.textContent);
    expect(names.indexOf("Joint saver")).toBeGreaterThan(names.indexOf("Tiger Brokers"));
  });

  it("shrinks where the data lives to one line at the foot of the page", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async () => reply(withAccounts));
    render(<Profile />);
    const foot = await screen.findByText(/\.kopifund/);
    expect(foot.tagName).toBe("P");
    expect(foot).toHaveTextContent("/Users/x/.kopifund · 1,240 transactions · Notion mirror off");
    expect(screen.queryByRole("heading", { name: "Where your data lives" })).not.toBeInTheDocument();
    // it comes after the cards
    expect(screen.getByRole("heading", { name: "Accounts" }).compareDocumentPosition(foot) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("says nothing under the date of birth but its label", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async () => reply(profile));
    render(<Profile />);
    await screen.findByLabelText("Date of birth");
    expect(screen.queryByText(/Stored in your data folder only/)).not.toBeInTheDocument();
    expect(screen.getByText("Date of birth, for CPF ages")).toBeInTheDocument();
  });

  it("shows the reason when the server refuses", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (_url, init) =>
      init?.method === "POST" ? reply({ error: "the date of birth must be a real past date" }, 400) : reply(profile));
    render(<Profile />);
    fireEvent.change(await screen.findByLabelText("Date of birth"), { target: { value: "2999-01-01" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("real past date"));
  });
});

// G18: unsaved means typed here; a date saved from another tab is not an edit on this one
describe("a date of birth saved from another tab", () => {
  afterEach(() => vi.restoreAllMocks());

  it("shows in the untouched box and leaves nothing unsaved", async () => {
    let birth: string | null = null;
    vi.spyOn(globalThis, "fetch").mockImplementation(async () => reply({ ...profile, about: { birth_date: birth } }));
    render(<Profile />);
    const input = await screen.findByLabelText("Date of birth");
    expect(input).toHaveValue("");
    birth = "1990-05-04";
    act(() => revalidate());
    await waitFor(() => expect(screen.getByLabelText("Date of birth")).toHaveValue("4 May 1990"));
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    expect(leaveAsks()).toBe(false);
  });
});
