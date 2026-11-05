import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { notifyChanged } from "../api";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { HIDE_TRANSFERS_KEY, Transactions } from "./Transactions";
import { leadingIcon } from "../test-icons";
import { choose } from "../test-select";
import { clearToasts, Toaster } from "../components/Toast";

// what the server sends for the days on a page: each day's rows, money in and money out
type StubRow = { date: string; amount_sgd: number | null };
const daysOf = (rows: StubRow[], continued = false, continues = false) => {
  const days: { date: string; count: number; in_sgd: number; out_sgd: number; continued: boolean; continues: boolean }[] = [];
  for (const r of rows) {
    let d = days.find((x) => x.date === r.date);
    if (!d) days.push(d = { date: r.date, count: 0, in_sgd: 0, out_sgd: 0, continued: false, continues: false });
    d.count += 1;
    if ((r.amount_sgd ?? 0) > 0) d.in_sgd += r.amount_sgd!; else d.out_sgd += r.amount_sgd ?? 0;
  }
  if (days[0]) days[0].continued = continued;
  if (days.length) days[days.length - 1].continues = continues;
  return days;
};

const page = {
  rows: [{ key: "f1", date: "2026-09-12", amount: -10000, amount_sgd: -10000, currency: "SGD", account: "Everyday",
    bank: "posb", merchant: "FAST PAYMENT", raw: "FAST PAYMENT", description: "", payee: "", category: "",
    flow: "External", trip: "" }],
  days: [{ date: "2026-09-12", count: 1, in_sgd: 0, out_sgd: -10000, continued: false, continues: false }],
  total: 1, sum_sgd: -10000, in_sgd: 0, out_sgd: -10000, months: ["2026-09"], categories: ["(none)", "Donations", "Shopping"],
  accounts: [{ value: "Everyday Savings 111-11111-1", label: "Everyday", bank: "posb" }],
};
// G13: a pin files its row at once, so the server answers with it applied unless told otherwise
const one = (pinned: boolean, payee = "A friend", applied = true, category = "Donations") => ({
  // the record holds the pinned category once the pin is saved
  rows: [{ ...page.rows[0], ...(pinned ? { category } : {}), text: "FAST PAYMENT", field: "memo",
    similar: { match_on: "memo", pattern: "FAST PAYMENT*", payee: "Fast Payment", kind: "Merchant" },
    pinned: pinned ? { payee, category, kind: payee ? "Person" : "", country: payee ? "SGP" : "",
      file: "keys.csv", line: 3, applied } : null }],
  total: 1, categories: ["Donations", "Shopping"], kinds: ["Merchant", "Person"],
  tiers: ["key", "type", "flow", "merchant", "counterparty", "memo", "reference"],
  tier_files: { key: "keys.csv", merchant: "merchants.csv", memo: "people.csv" },
});
const preview = {
  error: null, file: "people.csv", override: null, matches: 3, wins: 2, already: 0, changes: 2,
  sample: [{ key: "f2", date: "2026-08-01", amount: -5000, currency: "SGD", text: "FAST PAYMENT 2",
    before: { payee: "", category: "" }, after: { payee: "A friend", category: "Donations" } }],
  beaten_by: [{ file: "keys.csv", tier: "key", pattern: "f1", payee: "A friend", category: "Donations", rows: 1 }],
};

function stubFetch(rows = page.rows, pinnedPayee = "A friend", applied = true) {
  const calls: { url: string; body?: string }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, body: init?.body as string | undefined });
    const pins = calls.filter((c) => c.url === "/api/rules/pin");
    const pinned = pins.length > 0;
    const category = pinned ? JSON.parse(pins.at(-1)!.body!).category : "Donations";
    // a saved rule leaves no row to change until its Undo
    const saved = calls.filter((c) => c.url === "/api/rules/save").length > calls.filter((c) => c.url === "/api/rules/undo").length;
    const body = url.startsWith("/api/transactions") ? { ...page, days: daysOf(rows), rows, total: rows.length }
      : url.startsWith("/api/rules/rows") ? one(pinned, pinnedPayee, applied, category)
      : url.startsWith("/api/rules/preview") ? { ...preview, changes: saved ? 0 : preview.changes }
      : url === "/api/rules/save" ? { file: "people.csv", line: 2, change: "a1b2c3", relinked: { changed: 2, written: 2 } }
      : url === "/api/rules/undo" ? { undone: "Added memo FAST PAYMENT* (A friend) to people.csv", relinked: { changed: 2, written: 2 } }
      : url === "/api/rules/apply" ? { relinked: { changed: 1, written: 1 } }
      : url === "/api/rules/pin" ? { file: "keys.csv", change: "p1", relinked: { changed: 1, written: 1 },
        pinned: { payee: pinnedPayee, kind: pinnedPayee ? "Person" : "", country: pinnedPayee ? "SGP" : "", category: JSON.parse(init!.body as string).category } }
      : { file: "keys.csv" };
    return new Response(JSON.stringify(body), { status: 200 });
  }));
  return calls;
}

// the address is shared by every test in this file: each starts on a bare /transactions
const leaveAsks = () => !window.dispatchEvent(new Event("beforeunload", { cancelable: true }));
const bare = () => window.history.replaceState(null, "", "/transactions");

describe("Transactions", () => {
  beforeEach(() => { window.localStorage.clear(); bare(); });
  afterEach(() => { vi.unstubAllGlobals(); clearToasts(); });

  it("says why a row is flagged as a possible scam and saves This is fine", async () => {
    const flagged = [{ ...page.rows[0], flags: [{ code: "new_payee" as const, text: "S$100 to a new payee, the first payment to them" }] }];
    const calls = stubFetch(flagged);
    window.history.replaceState(null, "", "/transactions?flag=scam");
    render(<Transactions />);
    expect(await screen.findByText("S$100 to a new payee, the first payment to them")).toBeInTheDocument();
    expect(screen.getByText("Flagged as a possible scam")).toBeInTheDocument();
    expect(calls[0].url).toContain("flag=scam");
    fireEvent.click(screen.getByRole("button", { name: /This is fine: FAST PAYMENT on 12 Sep 2026/ }));
    await waitFor(() => expect(calls.some((c) => c.url === "/api/scam-checks/fine")).toBe(true));
    expect(JSON.parse(calls.find((c) => c.url === "/api/scam-checks/fine")!.body!)).toEqual({ key: "f1" });
  });

  it("lists the rows with no SGD value by a filter of its own, with a chip that clears it (G18)", async () => {
    const calls = stubFetch(page.rows);
    window.history.replaceState(null, "", "/transactions?missing_fx=1&hide_transfers=0");
    render(<Transactions />);
    expect(await screen.findByText("No SGD value")).toBeInTheDocument();
    expect(calls[0].url).toContain("missing_fx=1");
    expect(calls[0].url).not.toContain("key=");
    fireEvent.click(within(screen.getByText("No SGD value").parentElement!).getByRole("button", { name: "Clear" }));
    expect(screen.queryByText("No SGD value")).not.toBeInTheDocument();
    expect(new URLSearchParams(window.location.search).get("missing_fx")).toBeNull();
    await waitFor(() => expect(calls.at(-1)!.url).not.toContain("missing_fx"));
  });

  it("leads each scam reason and the flagged chip with its icon, the words beside them (G18)", async () => {
    const flagged = [{ ...page.rows[0], trip: "Trip A", flags: [
      { code: "new_payee" as const, text: "S$100 to a new payee, the first payment to them" },
      { code: "abroad" as const, text: "Paid abroad with no trip on" }] }];
    stubFetch(flagged);
    window.history.replaceState(null, "", "/transactions?flag=scam");
    render(<Transactions />);
    expect(leadingIcon(await screen.findByText("S$100 to a new payee, the first payment to them"))).toBe("user-plus");
    expect(leadingIcon(screen.getByText("Paid abroad with no trip on"))).toBe("globe");
    expect(leadingIcon(screen.getByText("Flagged as a possible scam"))).toBe("shield-alert");
    // a row's trip chip leads with the plane, as the Trips page does
    expect(leadingIcon(screen.getAllByText("Trip A")[0])).toBe("plane");
  });

  it("leads the scope chip's kinds and need classes with their icons, its words one run of text (G18)", async () => {
    window.history.replaceState(null, "", "/transactions?trip=Trip+A&spend=1");
    stubFetch();
    const { unmount } = render(<Transactions />);
    const chip = await screen.findByText("Trip: Trip A · Spending only");
    const icons = [...chip.querySelectorAll("[data-kind-icon]")].map((svg) => svg.getAttribute("class"));
    expect(icons).toHaveLength(2);
    expect(icons[0]).toContain("lucide-plane");
    expect(icons[1]).toContain("lucide-credit-card");
    unmount();
    window.history.replaceState(null, "", "/transactions?month=2026-09&spend=1&need=Need");
    render(<Transactions />);
    const need = await screen.findByText("Needs only");
    expect(leadingIcon(need)).toBe("shopping-basket");
    window.history.replaceState(null, "", "/");
  });

  it("shows no scam line on a row the checks do not flag", async () => {
    stubFetch();
    render(<Transactions />);
    await screen.findByRole("button", { name: /Set the category of the 12 Sep 2026 row/ });
    expect(screen.queryByRole("button", { name: /This is fine/ })).not.toBeInTheDocument();
  });

  it("shows a row with no category as a button to set it", async () => {
    stubFetch();
    render(<Transactions />);
    const button = await screen.findByRole("button", { name: /Set the category of the 12 Sep 2026 row/ });
    expect(button).toHaveTextContent("Set category");
  });

  it("sets a row's category in one tap: the chip opens the picker, a pick saves at once, with an Undo", async () => {
    const calls = stubFetch();
    render(<><Transactions /><Toaster /></>);
    fireEvent.click(await screen.findByRole("button", { name: /Set the category of the 12 Sep 2026 row/ }));
    // the picker takes the chip's place, open and with the cursor in it
    const box = await screen.findByRole("combobox", { name: "Category for the 12 Sep 2026 row" });
    expect(box).toHaveFocus();
    fireEvent.mouseDown(within(await screen.findByRole("listbox")).getByRole("option", { name: "Donations" }));
    expect(await screen.findByText("Category set, 1 row changed.")).toBeInTheDocument();
    expect(JSON.parse(calls.find((c) => c.url === "/api/rules/pin")!.body!))
      .toEqual({ key: "f1", category: "Donations", payee: "" });
    expect(screen.queryByRole("combobox", { name: "Category for the 12 Sep 2026 row" })).not.toBeInTheDocument();
    // no Save, no Load now, no Apply rules: it is done
    expect(screen.queryByRole("button", { name: "Save for this row" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Load now" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Apply rules" })).not.toBeInTheDocument();
    expect(calls.some((c) => c.url.startsWith("/api/run/load"))).toBe(false);
    const toast = screen.getByText("Category set, 1 row changed.").closest("[role=status]") as HTMLElement;
    fireEvent.click(within(toast).getByRole("button", { name: "Undo" }));
    expect(await screen.findByText("Undone, 2 rows changed back.")).toBeInTheDocument();
    expect(JSON.parse(calls.find((c) => c.url === "/api/rules/undo")!.body!)).toEqual({ change: "p1" });
  });

  it("offers one chip for the rows like it, counted by the preview, which saves the rule with an Undo", async () => {
    const calls = stubFetch();
    render(<><Transactions /><Toaster /></>);
    fireEvent.click(await screen.findByRole("button", { name: /Set the category of the 12 Sep 2026 row/ }));
    fireEvent.mouseDown(within(await screen.findByRole("listbox")).getByRole("option", { name: "Donations" }));
    const chip = await screen.findByRole("button", { name: "Apply to 2 similar rows" });
    // the rule is the row's text as a pattern, with the category and payee just set, and the pin's country
    const asked = new URLSearchParams(calls.filter((c) => c.url.startsWith("/api/rules/preview")).at(-1)!.url.split("?")[1]);
    expect(Object.fromEntries(asked)).toMatchObject({ match_on: "memo", pattern: "FAST PAYMENT*", payee: "A friend",
      kind: "Person", country: "SGP", category: "Donations" });
    fireEvent.click(chip);
    expect(await screen.findByText("Rule saved, 2 rows changed.")).toBeInTheDocument();
    expect(JSON.parse(calls.find((c) => c.url === "/api/rules/save")!.body!)).toEqual({
      rule: { match_on: "memo", pattern: "FAST PAYMENT*", payee: "A friend", kind: "Person", country: "SGP",
        category: "Donations", recurring: "", notes: "" },
      edit: null,
    });
    expect(screen.queryByRole("button", { name: "Apply to 2 similar rows" })).not.toBeInTheDocument();
    // Undo names the write it takes back, so another tab's newer one is refused, not undone
    const toast = screen.getByText("Rule saved, 2 rows changed.").closest("[role=status]") as HTMLElement;
    fireEvent.click(within(toast).getByRole("button", { name: "Undo" }));
    await waitFor(() => expect(calls.some((c) => c.url === "/api/rules/undo")).toBe(true));
    expect(JSON.parse(calls.find((c) => c.url === "/api/rules/undo")!.body!)).toEqual({ change: "a1b2c3" });
  });

  it("keeps the pick's Undo after the rule's, and an older Undo the server refuses stays for another try", async () => {
    const calls = stubFetch();
    const answer = globalThis.fetch;
    // the server takes back the newest write first: the pick's own Undo is refused while the rule stands
    let ruleUndone = false;
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      if (url === "/api/rules/undo") {
        const change = JSON.parse(init!.body as string).change;
        if (change === "p1" && !ruleUndone) {
          calls.push({ url, body: init!.body as string });
          return new Response(JSON.stringify({ error: "The rules have changed since this save, so it cannot be undone here" }), { status: 409 });
        }
        if (change === "a1b2c3") ruleUndone = true;
      }
      return (answer as typeof fetch)(url, init);
    }));
    render(<><Transactions /><Toaster /></>);
    fireEvent.click(await screen.findByRole("button", { name: /Set the category of the 12 Sep 2026 row/ }));
    fireEvent.mouseDown(within(await screen.findByRole("listbox")).getByRole("option", { name: "Donations" }));
    fireEvent.click(await screen.findByRole("button", { name: "Apply to 2 similar rows" }));
    await screen.findByText("Rule saved, 2 rows changed.");
    // both Undos are up, the rule's on top
    const toast = (text: string) => screen.getByText(text).closest("[role=status]") as HTMLElement;
    expect(screen.getAllByRole("button", { name: "Undo" })).toHaveLength(2);
    expect(screen.getAllByRole("status").map((el) => el.textContent)[0]).toContain("Rule saved");
    // the older one first: refused, said plainly, and still there
    fireEvent.click(within(toast("Category set, 1 row changed.")).getByRole("button", { name: "Undo" }));
    expect(await screen.findByText(/cannot be undone here/)).toBeInTheDocument();
    expect(screen.getByText("Category set, 1 row changed.")).toBeInTheDocument();
    // the rule's, then the older one again
    fireEvent.click(within(toast("Rule saved, 2 rows changed.")).getByRole("button", { name: "Undo" }));
    await waitFor(() => expect(screen.queryByText("Rule saved, 2 rows changed.")).not.toBeInTheDocument());
    fireEvent.click(within(toast("Category set, 1 row changed.")).getByRole("button", { name: "Undo" }));
    await waitFor(() => expect(screen.queryByText("Category set, 1 row changed.")).not.toBeInTheDocument());
    expect(calls.filter((c) => c.url === "/api/rules/undo").map((c) => JSON.parse(c.body!).change)).toEqual(["p1", "a1b2c3", "p1"]);
  });

  it("keeps the payee, kind and note behind More", async () => {
    const calls = stubFetch();
    render(<><Transactions /><Toaster /></>);
    fireEvent.click(await screen.findByRole("button", { name: /Set the category of the 12 Sep 2026 row/ }));
    fireEvent.mouseDown(within(await screen.findByRole("listbox")).getByRole("option", { name: "Donations" }));
    expect(screen.queryByRole("textbox", { name: "Payee for the 12 Sep 2026 row" })).not.toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: "More" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Payee for the 12 Sep 2026 row" }), { target: { value: "A friend" } });
    choose(screen.getByRole("button", { name: "Kind for the 12 Sep 2026 row" }), "Person");
    fireEvent.change(screen.getByRole("textbox", { name: "Note for the 12 Sep 2026 row" }), { target: { value: "a gift" } });
    fireEvent.click(screen.getByRole("button", { name: "Save for this row" }));
    await waitFor(() => expect(calls.filter((c) => c.url === "/api/rules/pin")).toHaveLength(2));
    expect(JSON.parse(calls.filter((c) => c.url === "/api/rules/pin")[1].body!))
      .toEqual({ key: "f1", category: "Donations", payee: "A friend", kind: "Person", note: "a gift" });
  });

  it("asks before leaving a row's More form with something typed, and not once it is saved", async () => {
    stubFetch();
    render(<><Transactions /><Toaster /></>);
    fireEvent.click(await screen.findByRole("button", { name: /Set the category of the 12 Sep 2026 row/ }));
    fireEvent.mouseDown(within(await screen.findByRole("listbox")).getByRole("option", { name: "Donations" }));
    fireEvent.click(await screen.findByRole("button", { name: "More" }));
    await screen.findByRole("textbox", { name: "Payee for the 12 Sep 2026 row" });
    expect(leaveAsks()).toBe(false);
    fireEvent.change(screen.getByRole("textbox", { name: "Note for the 12 Sep 2026 row" }), { target: { value: "a gift" } });
    expect(leaveAsks()).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Save for this row" }));
    await waitFor(() => expect(screen.queryByRole("textbox", { name: "Note for the 12 Sep 2026 row" })).not.toBeInTheDocument());
    expect(leaveAsks()).toBe(false);
  });

  it("still asks before leaving when Less or Close hides a typed form, since the page keeps what was typed", async () => {
    stubFetch();
    render(<><Transactions /><Toaster /></>);
    fireEvent.click(await screen.findByRole("button", { name: /Set the category of the 12 Sep 2026 row/ }));
    fireEvent.mouseDown(within(await screen.findByRole("listbox")).getByRole("option", { name: "Donations" }));
    fireEvent.click(await screen.findByRole("button", { name: "More" }));
    fireEvent.change(await screen.findByRole("textbox", { name: "Note for the 12 Sep 2026 row" }), { target: { value: "a gift" } });
    fireEvent.click(screen.getByRole("button", { name: "Less" }));
    expect(screen.queryByRole("textbox", { name: "Note for the 12 Sep 2026 row" })).not.toBeInTheDocument();
    expect(leaveAsks()).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(leaveAsks()).toBe(true);
  });

  it("keeps a row's form open on what was typed while its save was on its way", async () => {
    stubFetch();
    const answer = globalThis.fetch;
    let release: (() => void) | null = null;
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      // the More form's save (the one with a note) waits until let go
      if (url === "/api/rules/pin" && JSON.parse(String(init!.body)).note) {
        const r = await answer(url, init);
        return new Promise<Response>((done) => { release = () => done(r); });
      }
      return answer(url, init);
    }));
    render(<><Transactions /><Toaster /></>);
    fireEvent.click(await screen.findByRole("button", { name: /Set the category of the 12 Sep 2026 row/ }));
    fireEvent.mouseDown(within(await screen.findByRole("listbox")).getByRole("option", { name: "Donations" }));
    fireEvent.click(await screen.findByRole("button", { name: "More" }));
    const note = await screen.findByRole("textbox", { name: "Note for the 12 Sep 2026 row" });
    fireEvent.change(note, { target: { value: "a gift" } });
    fireEvent.click(screen.getByRole("button", { name: "Save for this row" }));
    fireEvent.change(note, { target: { value: "a wedding gift" } });
    await waitFor(() => expect(release).not.toBeNull());
    await act(async () => release!());
    await act(async () => { await new Promise((r) => setTimeout(r, 20)); });
    // "a gift" is saved; the rest was never sent, so the form stays open on it and leaving asks
    expect(screen.getByRole("textbox", { name: "Note for the 12 Sep 2026 row" })).toHaveValue("a wedding gift");
    expect(leaveAsks()).toBe(true);
    // typed back to what was saved: nothing is left unsaved
    fireEvent.change(screen.getByRole("textbox", { name: "Note for the 12 Sep 2026 row" }), { target: { value: "a gift" } });
    expect(leaveAsks()).toBe(false);
  });

  it("asks before leaving when a row's form is cleared while its save is on its way", async () => {
    stubFetch();
    const answer = globalThis.fetch;
    let release: (() => void) | null = null;
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      if (url === "/api/rules/pin" && JSON.parse(String(init!.body)).note) {
        const r = await answer(url, init);
        return new Promise<Response>((done) => { release = () => done(r); });
      }
      return answer(url, init);
    }));
    render(<><Transactions /><Toaster /></>);
    fireEvent.click(await screen.findByRole("button", { name: /Set the category of the 12 Sep 2026 row/ }));
    fireEvent.mouseDown(within(await screen.findByRole("listbox")).getByRole("option", { name: "Donations" }));
    fireEvent.click(await screen.findByRole("button", { name: "More" }));
    const note = await screen.findByRole("textbox", { name: "Note for the 12 Sep 2026 row" });
    fireEvent.change(note, { target: { value: "a gift" } });
    fireEvent.click(screen.getByRole("button", { name: "Save for this row" }));
    // a save on its way may still fail: leaving asks until it lands
    expect(leaveAsks()).toBe(true);
    // cleared while "a gift" is being saved: the clear was never sent
    fireEvent.change(note, { target: { value: "" } });
    expect(leaveAsks()).toBe(true);
    await waitFor(() => expect(release).not.toBeNull());
    await act(async () => release!());
    await act(async () => { await new Promise((r) => setTimeout(r, 20)); });
    expect(screen.getByRole("textbox", { name: "Note for the 12 Sep 2026 row" })).toHaveValue("");
    expect(leaveAsks()).toBe(true);
  });

  it("stops asking when a form hidden during its save has nothing typed and the save fails", async () => {
    stubFetch();
    const answer = globalThis.fetch;
    let fail: (() => void) | null = null;
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      // the More form's save (the chip's sends no kind) waits, then is refused
      if (url === "/api/rules/pin" && "kind" in JSON.parse(String(init!.body))) {
        return new Promise<Response>((done) => { fail = () => done(new Response(JSON.stringify({ error: "disk full" }), { status: 500 })); });
      }
      return answer(url, init);
    }));
    render(<><Transactions /><Toaster /></>);
    fireEvent.click(await screen.findByRole("button", { name: /Set the category of the 12 Sep 2026 row/ }));
    fireEvent.mouseDown(within(await screen.findByRole("listbox")).getByRole("option", { name: "Donations" }));
    fireEvent.click(await screen.findByRole("button", { name: "More" }));
    await screen.findByRole("textbox", { name: "Note for the 12 Sep 2026 row" });
    fireEvent.click(screen.getByRole("button", { name: "Save for this row" }));
    expect(leaveAsks()).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Less" }));
    await waitFor(() => expect(fail).not.toBeNull());
    await act(async () => fail!());
    await waitFor(() => expect(leaveAsks()).toBe(false));
  });

  it("stops asking when the category chip saves the only thing a hidden form held", async () => {
    stubFetch();
    render(<><Transactions /><Toaster /></>);
    fireEvent.click(await screen.findByRole("button", { name: /Set the category of the 12 Sep 2026 row/ }));
    fireEvent.mouseDown(within(await screen.findByRole("listbox")).getByRole("option", { name: "Donations" }));
    fireEvent.click(await screen.findByRole("button", { name: "More" }));
    const box = await screen.findByRole("combobox", { name: "Category for the 12 Sep 2026 row" });
    fireEvent.change(box, { target: { value: "Shop" } });
    fireEvent.mouseDown(within(await screen.findByRole("listbox")).getByRole("option", { name: "Shopping" }));
    await waitFor(() => expect(leaveAsks()).toBe(true));
    fireEvent.click(screen.getByRole("button", { name: "Less" }));
    expect(leaveAsks()).toBe(true);
    // the chip saves Shopping, all the hidden form held
    fireEvent.click(screen.getByRole("button", { name: /Set the category of the 12 Sep 2026 row/ }));
    fireEvent.mouseDown(within(await screen.findByRole("listbox")).getByRole("option", { name: "Shopping" }));
    await waitFor(() => expect(leaveAsks()).toBe(false));
  });

  it("opens More again clean after a save, with no question about leaving", async () => {
    stubFetch();
    render(<><Transactions /><Toaster /></>);
    fireEvent.click(await screen.findByRole("button", { name: /Set the category of the 12 Sep 2026 row/ }));
    fireEvent.mouseDown(within(await screen.findByRole("listbox")).getByRole("option", { name: "Donations" }));
    fireEvent.click(await screen.findByRole("button", { name: "More" }));
    fireEvent.change(await screen.findByRole("textbox", { name: "Note for the 12 Sep 2026 row" }), { target: { value: "a gift" } });
    fireEvent.click(screen.getByRole("button", { name: "Save for this row" }));
    await waitFor(() => expect(screen.queryByRole("textbox", { name: "Note for the 12 Sep 2026 row" })).not.toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "More" }));
    expect(await screen.findByRole("textbox", { name: "Note for the 12 Sep 2026 row" })).toHaveValue("");
    expect(leaveAsks()).toBe(false);
  });

  it("opens on the row's category, and picking it again saves nothing", async () => {
    const calls = stubFetch([{ ...page.rows[0], category: "Donations", payee: "A friend" }]);
    render(<Transactions />);
    fireEvent.click(await screen.findByRole("button", { name: /Set the category of the 12 Sep 2026 row A friend/ }));
    expect(await screen.findByRole("combobox", { name: "Category for the 12 Sep 2026 row" })).toHaveValue("Donations");
    const current = within(screen.getByRole("listbox")).getByRole("option", { name: "Donations" });
    expect(current).toHaveAttribute("aria-selected", "true");
    fireEvent.mouseDown(current);
    expect(await screen.findByRole("button", { name: "More" })).toBeInTheDocument();
    expect(calls.some((c) => c.url === "/api/rules/pin")).toBe(false);
  });

  it("puts the chip back, unchanged, when the picker is left with Escape", async () => {
    const calls = stubFetch();
    render(<Transactions />);
    fireEvent.click(await screen.findByRole("button", { name: /Set the category of the 12 Sep 2026 row/ }));
    fireEvent.keyDown(await screen.findByRole("combobox", { name: "Category for the 12 Sep 2026 row" }), { key: "Escape" });
    expect(await screen.findByRole("button", { name: /Set the category of the 12 Sep 2026 row/ })).toHaveTextContent("Set category");
    expect(calls.some((c) => c.url === "/api/rules/pin")).toBe(false);
  });

  it("changes nothing when the picker is left by clicking away, even after typing", async () => {
    const calls = stubFetch();
    render(<Transactions />);
    fireEvent.click(await screen.findByRole("button", { name: /Set the category of the 12 Sep 2026 row/ }));
    const box = await screen.findByRole("combobox", { name: "Category for the 12 Sep 2026 row" });
    fireEvent.change(box, { target: { value: "Dona" } });
    fireEvent.blur(box);
    expect(await screen.findByRole("button", { name: /Set the category of the 12 Sep 2026 row/ })).toHaveTextContent("Set category");
    expect(calls.some((c) => c.url === "/api/rules/pin")).toBe(false);
  });

  it("builds the similar rule from the category the row has now, after a save through More", async () => {
    const calls = stubFetch();
    render(<><Transactions /><Toaster /></>);
    fireEvent.click(await screen.findByRole("button", { name: /Set the category of the 12 Sep 2026 row/ }));
    fireEvent.mouseDown(within(await screen.findByRole("listbox")).getByRole("option", { name: "Donations" }));
    await screen.findByRole("button", { name: "Apply to 2 similar rows" });
    fireEvent.click(screen.getByRole("button", { name: "More" }));
    const box = screen.getByRole("combobox", { name: "Category for the 12 Sep 2026 row" });
    fireEvent.focus(box);
    fireEvent.mouseDown(within(screen.getByRole("listbox")).getByRole("option", { name: "Shopping" }));
    fireEvent.click(screen.getByRole("button", { name: "Save for this row" }));
    await waitFor(() => expect(calls.filter((c) => c.url === "/api/rules/pin")).toHaveLength(2));
    await waitFor(() => expect(calls.filter((c) => c.url.startsWith("/api/rules/preview")).at(-1)!.url).toContain("category=Shopping"));
    fireEvent.click(await screen.findByRole("button", { name: "Apply to 2 similar rows" }));
    await waitFor(() => expect(calls.some((c) => c.url === "/api/rules/save")).toBe(true));
    expect(JSON.parse(calls.find((c) => c.url === "/api/rules/save")!.body!).rule.category).toBe("Shopping");
  });

  it("hides the chip after a new pick until the new rule's own count arrives", async () => {
    const calls = stubFetch();
    const stubbed = vi.mocked(fetch).getMockImplementation()!;
    render(<Transactions />);
    fireEvent.click(await screen.findByRole("button", { name: /Set the category of the 12 Sep 2026 row/ }));
    fireEvent.mouseDown(within(await screen.findByRole("listbox")).getByRole("option", { name: "Donations" }));
    await screen.findByRole("button", { name: "Apply to 2 similar rows" });
    // the preview for the next pick never answers: the old count must not stand in for it
    vi.mocked(fetch).mockImplementation((url, init) =>
      String(url).includes("category=Shopping") ? new Promise(() => {}) : stubbed(url as string, init));
    fireEvent.click(screen.getByRole("button", { name: /Set the category of the 12 Sep 2026 row/ }));
    fireEvent.mouseDown(within(await screen.findByRole("listbox")).getByRole("option", { name: "Shopping" }));
    await waitFor(() => expect(calls.filter((c) => c.url === "/api/rules/pin")).toHaveLength(2));
    await waitFor(() => expect(screen.queryByRole("button", { name: /similar row/ })).not.toBeInTheDocument());
  });

  it("lets the preview bring the chip back after the rule's Undo", async () => {
    const calls = stubFetch();
    render(<><Transactions /><Toaster /></>);
    fireEvent.click(await screen.findByRole("button", { name: /Set the category of the 12 Sep 2026 row/ }));
    fireEvent.mouseDown(within(await screen.findByRole("listbox")).getByRole("option", { name: "Donations" }));
    fireEvent.click(await screen.findByRole("button", { name: "Apply to 2 similar rows" }));
    const toast = (await screen.findByText("Rule saved, 2 rows changed.")).closest("[role=status]") as HTMLElement;
    await waitFor(() => expect(screen.queryByRole("button", { name: "Apply to 2 similar rows" })).not.toBeInTheDocument());
    fireEvent.click(within(toast).getByRole("button", { name: "Undo" }));
    await waitFor(() => expect(calls.some((c) => c.url === "/api/rules/undo")).toBe(true));
    expect(await screen.findByRole("button", { name: "Apply to 2 similar rows" })).toBeInTheDocument();
  });

  it("keeps a note typed under More when the category is picked again", async () => {
    const calls = stubFetch();
    render(<Transactions />);
    fireEvent.click(await screen.findByRole("button", { name: /Set the category of the 12 Sep 2026 row/ }));
    fireEvent.mouseDown(within(await screen.findByRole("listbox")).getByRole("option", { name: "Donations" }));
    fireEvent.click(await screen.findByRole("button", { name: "More" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Note for the 12 Sep 2026 row" }), { target: { value: "a gift" } });
    fireEvent.click(screen.getByRole("button", { name: "Less" }));
    fireEvent.click(screen.getByRole("button", { name: /Set the category of the 12 Sep 2026 row/ }));
    fireEvent.mouseDown(within(await screen.findByRole("listbox")).getByRole("option", { name: "Shopping" }));
    await waitFor(() => expect(calls.filter((c) => c.url === "/api/rules/pin")).toHaveLength(2));
    fireEvent.click(screen.getByRole("button", { name: "More" }));
    expect(screen.getByRole("textbox", { name: "Note for the 12 Sep 2026 row" })).toHaveValue("a gift");
    fireEvent.click(screen.getByRole("button", { name: "Save for this row" }));
    await waitFor(() => expect(calls.filter((c) => c.url === "/api/rules/pin")).toHaveLength(3));
    expect(JSON.parse(calls.filter((c) => c.url === "/api/rules/pin")[2].body!)).toMatchObject({ category: "Shopping", note: "a gift" });
  });

  it("applies a pin a rules file left waiting", async () => {
    const calls = stubFetch(page.rows, "A friend", false);
    render(<><Transactions /><Toaster /></>);
    fireEvent.click(await screen.findByRole("button", { name: /Set the category of the 12 Sep 2026 row/ }));
    fireEvent.mouseDown(within(await screen.findByRole("listbox")).getByRole("option", { name: "Donations" }));
    await screen.findByText("Set to Donations, A friend by keys.csv line 3; not applied yet.");
    fireEvent.click(screen.getByRole("button", { name: "Apply rules" }));
    expect(await screen.findByText("Rules applied, 1 row changed.")).toBeInTheDocument();
    expect(calls.some((c) => c.url === "/api/rules/apply")).toBe(true);
  });

  it("hides transfers by default, says so, and keeps the address clean", async () => {
    const calls = stubFetch();
    render(<Transactions />);
    expect(await screen.findByRole("button", { name: /Transfers hidden/ })).toBeInTheDocument();
    expect(calls[0].url).toContain("hide_transfers=1");
    expect(window.location.search).toBe("");
    expect(window.localStorage.getItem(HIDE_TRANSFERS_KEY)).toBeNull();
    await screen.findByText(/transfers hidden/);
  });

  it("shows transfers when the chip is switched, and remembers it for the next visit", async () => {
    const calls = stubFetch();
    const { unmount } = render(<Transactions />);
    fireEvent.click(await screen.findByRole("button", { name: /Transfers hidden/ }));
    await screen.findByRole("button", { name: /Transfers shown/ });
    await waitFor(() => expect(calls.at(-1)!.url).not.toContain("hide_transfers"));
    expect(window.localStorage.getItem(HIDE_TRANSFERS_KEY)).toBe("0");
    expect(window.location.search).toBe("?hide_transfers=0");
    expect(screen.queryByText(/transfers hidden/)).not.toBeInTheDocument();
    unmount();
    // a later visit with no filters in its address starts the way it was left
    bare();
    render(<Transactions />);
    expect(await screen.findByRole("button", { name: /Transfers shown/ })).toBeInTheDocument();
    expect(calls.at(-1)!.url).not.toContain("hide_transfers");
    // and switching back is the default again: remembered, and nothing left in the address
    fireEvent.click(screen.getByRole("button", { name: /Transfers shown/ }));
    await screen.findByRole("button", { name: /Transfers hidden/ });
    await waitFor(() => expect(calls.at(-1)!.url).toContain("hide_transfers=1"));
    expect(window.localStorage.getItem(HIDE_TRANSFERS_KEY)).toBe("1");
    expect(window.location.search).toBe("");
  });

  it("shows each account's bank mark and short name, and lists the short names", async () => {
    const calls = stubFetch();
    const { container } = render(<Transactions />);
    const cell = (await screen.findAllByText("Everyday")).find((e) => e.tagName === "SPAN")!;
    expect(cell.querySelector("[data-bank='posb']")).not.toBeNull();
    expect(container.querySelector("img[data-bank='posb']")).not.toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Account" }));
    const option = within(screen.getByRole("listbox", { name: "Account" })).getByRole("option", { name: "Everyday" });
    expect(option.querySelector("[data-bank='posb']")).not.toBeNull();
    fireEvent.mouseDown(option);
    // the filter sends the account's id, never its name or number
    await waitFor(() => expect(calls.at(-1)!.url).toContain("account=Everyday+Savings+111-11111-1"));
    expect(screen.queryByText(/111-11111-1/)).not.toBeInTheDocument();
  });

  it("describes a row with no payee in plain words, the bank line on hover", async () => {
    stubFetch([{ ...page.rows[0], key: "t1", merchant: "TOP-UP TO PAYLAH! : | 80000000",
      raw: "TOP-UP TO PAYLAH! : | 80000000", description: "PayLah! top-up", category: "Wallet top-up" }]);
    render(<Transactions />);
    const text = await screen.findByText("PayLah! top-up");
    expect(text.closest("td")).toHaveAttribute("title", "TOP-UP TO PAYLAH! : | 80000000");
    expect(screen.queryByText("TOP-UP TO PAYLAH! : | 80000000")).not.toBeInTheDocument();
  });

  it("keeps a row set with no payee payee-less in the rule for similar rows", async () => {
    const calls = stubFetch(page.rows, "");
    render(<Transactions />);
    fireEvent.click(await screen.findByRole("button", { name: /Set the category of the 12 Sep 2026 row/ }));
    fireEvent.mouseDown(within(await screen.findByRole("listbox")).getByRole("option", { name: "Donations" }));
    fireEvent.click(await screen.findByRole("button", { name: "Apply to 2 similar rows" }));
    await waitFor(() => expect(calls.some((c) => c.url === "/api/rules/save")).toBe(true));
    expect(JSON.parse(calls.find((c) => c.url === "/api/rules/save")!.body!).rule.payee).toBe("");
  });
});

describe("Transactions opened from the Spending explorer", () => {
  afterEach(() => { vi.unstubAllGlobals(); window.history.replaceState(null, "", "/"); });

  it("asks for the explorer's range, scope and spending, shows them, and clears them", async () => {
    window.history.replaceState(null, "", "/transactions?from=2025-10&to=2026-09&spend=1&home=1&category=Eating+out");
    const calls = stubFetch();
    render(<Transactions />);
    await screen.findByText("Spending only · Oct 2025 to Sep 2026 · Singapore only");
    expect(calls[0].url).toContain("category=Eating+out");
    expect(calls[0].url).toContain("from=2025-10&to=2026-09&home=1&spend=1");
    fireEvent.click(screen.getByRole("button", { name: "Clear" }));
    await waitFor(() => expect(calls.at(-1)!.url).not.toContain("spend=1"));
    expect(screen.queryByText(/Spending only/)).not.toBeInTheDocument();
  });

  it("asks for a week's days from a weekly chart point, shows the days in the chip, and clears them", async () => {
    window.history.replaceState(null, "", "/transactions?from=2026-09-14&to=2026-09-20&spend=1&categories=%5B%22Eating+out%22%5D");
    const calls = stubFetch();
    render(<Transactions />);
    await screen.findByText("Spending only · Categories: Eating out · 14 Sep to 20 Sep 2026");
    expect(calls[0].url).toContain("from=2026-09-14&to=2026-09-20");
    fireEvent.click(screen.getByRole("button", { name: "Clear" }));
    await waitFor(() => expect(calls.at(-1)!.url).not.toContain("from="));
    expect(calls.at(-1)!.url).not.toContain("to=2026");
    expect(screen.queryByText(/14 Sep/)).not.toBeInTheDocument();
    expect(window.location.search).not.toContain("from=");
  });

  it("asks for a trip's spending from the Trips page, shows the trip, and clears it", async () => {
    window.history.replaceState(null, "", "/transactions?trip=Trip+A&spend=1");
    const calls = stubFetch();
    render(<Transactions />);
    await screen.findByText("Trip: Trip A · Spending only");
    expect(calls[0].url).toContain("spend=1&trip=Trip+A");
    fireEvent.click(screen.getByRole("button", { name: "Clear" }));
    await waitFor(() => expect(calls.at(-1)!.url).not.toContain("trip="));
    expect(screen.queryByText(/Trip: Trip A/)).not.toBeInTheDocument();
  });
});

describe("Transactions opened from a stat's bar", () => {
  afterEach(() => { vi.unstubAllGlobals(); window.history.replaceState(null, "", "/"); });

  const open = async (query: string, chip: string) => {
    window.history.replaceState(null, "", `/transactions?${query}`);
    const calls = stubFetch();
    render(<Transactions />);
    await screen.findByText(chip);
    return calls;
  };

  it("asks for the rows a needs bar adds up, names them, and clears them with the button", async () => {
    const calls = await open("month=2026-09&spend=1&need=Need", "Needs only");
    expect(calls[0].url).toContain("month=2026-09");
    expect(calls[0].url).toContain("spend=1");
    expect(calls[0].url).toContain("need=Need");
    fireEvent.click(screen.getByRole("button", { name: "Clear" }));
    await waitFor(() => expect(calls.at(-1)!.url).not.toContain("need="));
    expect(calls.at(-1)!.url).not.toContain("spend=1");
    expect(calls.at(-1)!.url).toContain("month=2026-09");   // the month is the Month box's, not the chip's
  });

  it("says savings are the investing and saving transfers, and shows transfers for them", async () => {
    const calls = await open("month=2026-09&transfer=1&need=Investment%2CSavings&hide_transfers=0", "Investing and saving only");
    expect(calls[0].url).toContain("transfer=1");
    expect(calls[0].url).toContain("need=Investment%2CSavings");
    expect(calls[0].url).not.toContain("hide_transfers=1");
  });

  it("names income and spending together, a bucket, and categories given as a list", async () => {
    await open("income=1&spend=1", "Spending and income only");
    cleanup();
    await open("spend=1&bucket=Food", "Spending only · Bucket: Food");
    cleanup();
    const calls = await open("income=1&not_categories=%5B%22Salary%22%2C%22Freelance+income%22%5D", "Income only · Not categories: Salary, Freelance income");
    expect(calls[0].url).toContain("not_categories=%5B%22Salary%22%2C%22Freelance+income%22%5D");
  });

  it("asks for rows leaving out moves between own accounts, and for the stand-in rows alone", async () => {
    const calls = await open("month=2026-09&external=1&category=(none)", "Not between your accounts");
    expect(calls[0].url).toContain("external=1");
    cleanup();
    const stand = await open("month=2026-09&synthetic=1&hide_transfers=0", "Stand-in rows only");
    expect(stand[0].url).toContain("synthetic=1");
    fireEvent.click(screen.getByRole("button", { name: "Clear" }));
    await waitFor(() => expect(stand.at(-1)!.url).not.toContain("synthetic="));
  });

  it("gives the explorer's rest of a folded series as a list of what it is not", async () => {
    const calls = await open("spend=1&not_payees=%5B%22Grab%22%5D", "Spending only · Not payees: Grab");
    expect(calls[0].url).toContain("not_payees=");
  });
});

describe("Transactions opened from a figure on another page", () => {
  afterEach(() => { vi.unstubAllGlobals(); window.history.replaceState(null, "", "/"); });

  it("asks for a payee by its exact name, shows it as a chip, and clears it with the button", async () => {
    window.history.replaceState(null, "", "/transactions?payee=Gym&named=1&out=1&synthetic=0&hide_transfers=0");
    const calls = stubFetch();
    render(<Transactions />);
    await screen.findByText("Payee: Gym · Money out only");
    expect(calls[0].url).toContain("payee=Gym");
    expect(calls[0].url).toContain("out=1");
    expect(calls[0].url).toContain("named=1");
    expect(calls[0].url).not.toContain("q=");
    fireEvent.click(screen.getByRole("button", { name: "Clear" }));
    await waitFor(() => expect(calls.at(-1)!.url).not.toContain("payee="));
    expect(calls.at(-1)!.url).not.toContain("out=1");
    expect(calls.at(-1)!.url).not.toContain("named=1");
    expect(screen.queryByText(/Payee: Gym/)).not.toBeInTheDocument();
  });

  it("asks for rows by key, says how many, and Clear filters takes the keys off", async () => {
    window.history.replaceState(null, "", "/transactions?key=a1b2c3d4e5f6");
    const calls = stubFetch();
    render(<Transactions />);
    await screen.findByText("One payment");
    expect(calls[0].url).toContain("key=a1b2c3d4e5f6");
    fireEvent.click(await screen.findByRole("button", { name: "Clear filters" }));
    await waitFor(() => expect(calls.at(-1)!.url).not.toContain("key="));
    expect(screen.queryByText("One payment")).not.toBeInTheDocument();
    window.history.replaceState(null, "", "/transactions?key=aaa%2Cbbb%2Cccc");
    window.dispatchEvent(new PopStateEvent("popstate"));
    expect(await screen.findByText("3 payments")).toBeInTheDocument();
  });
});

describe("Transactions pages", () => {
  const row = (i: number) => ({ ...page.rows[0], key: `r${i}`, payee: `Shop ${i}` });
  // the server cuts the 312 invented rows by the limit and offset it is asked for
  function stubPaged(total = 312) {
    const calls: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      calls.push(url);
      const q = new URL(url, "http://x").searchParams;
      const limit = Number(q.get("limit")), offset = Number(q.get("offset"));
      const rows = Array.from({ length: Math.max(0, Math.min(limit, total - offset)) }, (_, i) => row(offset + i + 1));
      return new Response(JSON.stringify({ ...page, days: daysOf(rows), rows, total }), { status: 200 });
    }));
    return calls;
  }
  beforeEach(() => { window.localStorage.clear(); bare(); });
  afterEach(() => { vi.unstubAllGlobals(); clearToasts(); });

  it("asks the server for a page of 25 and moves by page number", async () => {
    const calls = stubPaged();
    render(<Transactions />);
    await screen.findByText("Shop 1");
    expect(calls[0]).toContain("limit=25&offset=0");
    expect(screen.getByText("1 to 25 of 312")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Page 3" }));
    await screen.findByText("Shop 51");
    expect(calls.at(-1)).toContain("limit=25&offset=50");
    expect(screen.getByText("51 to 75 of 312")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Page 3" })).toHaveAttribute("aria-current", "page");
    fireEvent.click(screen.getByRole("button", { name: "Next page" }));
    await screen.findByText("Shop 76");
    expect(calls.at(-1)).toContain("offset=75");
  });

  // the search and a filter each get a test of their own: together they were six page renders in one test,
  // which a busy machine could stretch past the five-second limit
  it("goes back to the first page when the search changes", async () => {
    const calls = stubPaged();
    render(<Transactions />);
    await screen.findByText("Shop 1");
    fireEvent.click(screen.getByRole("button", { name: "Page 4" }));
    await screen.findByText("Shop 76");
    fireEvent.change(screen.getByRole("searchbox", { name: "Search transactions" }), { target: { value: "shop" } });
    await waitFor(() => expect(calls.at(-1)).toContain("offset=0&q=shop"));
    expect(calls.filter((u) => u.includes("q=shop") && !u.includes("offset=0"))).toEqual([]);
  });

  it("goes back to the first page when a filter changes", async () => {
    const calls = stubPaged();
    render(<Transactions />);
    await screen.findByText("Shop 1");
    fireEvent.click(screen.getByRole("button", { name: "Page 2" }));
    await screen.findByText("Shop 26");
    fireEvent.click(screen.getByRole("button", { name: /Transfers hidden/ }));
    await waitFor(() => expect(calls.at(-1)).toMatch(/offset=0/));
    expect(calls.at(-1)).not.toContain("hide_transfers");
  });

  it("asks for the page size chosen, and uses it next time", async () => {
    const calls = stubPaged();
    const { unmount } = render(<Transactions />);
    await screen.findByText("Shop 1");
    fireEvent.click(screen.getByRole("button", { name: "Rows per page" }));
    fireEvent.mouseDown(screen.getByRole("option", { name: "100 per page" }));
    await waitFor(() => expect(calls.at(-1)).toContain("limit=100&offset=0"));
    await screen.findByText("1 to 100 of 312");
    unmount();
    render(<Transactions />);
    await screen.findByText("1 to 100 of 312");
  });

  it("steps back to the last page when the total shrinks under the page it is on", async () => {
    let total = 312;
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      const q = new URL(url, "http://x").searchParams;
      const limit = Number(q.get("limit")), offset = Number(q.get("offset"));
      const rows = Array.from({ length: Math.max(0, Math.min(limit, total - offset)) }, (_, i) => row(offset + i + 1));
      return new Response(JSON.stringify({ ...page, days: daysOf(rows), rows, total }), { status: 200 });
    }));
    render(<Transactions />);
    await screen.findByText("Shop 1");
    fireEvent.click(screen.getByRole("button", { name: "Page 4" }));
    fireEvent.click(await screen.findByRole("button", { name: "Page 5" }));
    await screen.findByText("Shop 101");
    total = 30;
    await act(async () => { notifyChanged(); });
    await screen.findByText("26 to 30 of 30");
    expect(screen.getByText("Shop 26")).toBeInTheDocument();
  });
});

// ---- G13: the friendlier page ----

function stubPhone(wide: boolean) {
  // useWide asks for (min-width: 48rem): a phone says no
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: wide, media: query, addEventListener: () => {}, removeEventListener: () => {},
  }));
}

const money = (i: number, over: object = {}) => ({ ...page.rows[0], key: `m${i}`, payee: `Shop ${i}`, category: "Shopping", ...over });

function stubTxns(body: object) {
  const calls: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    calls.push(url);
    return new Response(JSON.stringify({ ...page, days: daysOf((body as { rows?: StubRow[] }).rows ?? page.rows), ...body }), { status: 200 });
  }));
  return calls;
}

describe("Transactions on a phone", () => {
  beforeEach(() => { window.localStorage.clear(); bare(); stubPhone(false); });
  afterEach(() => { vi.unstubAllGlobals(); });

  it("shows each row on two lines in place of a table, so the amount is never off the edge", async () => {
    stubTxns({ rows: [money(1, { amount: -4550, amount_sgd: -4550 })], total: 1 });
    const { container } = render(<Transactions />);
    await screen.findByText("Shop 1");
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    const row = screen.getByText("Shop 1").closest("li")!;
    const [first, second] = Array.from(row.children) as HTMLElement[];
    // line one: the payee and the amount
    expect(within(first).getByText("Shop 1")).toBeInTheDocument();
    expect(within(first).getByText("−S$45.50")).toBeInTheDocument();
    // line two: the category chip and the bank's mark; the date is the header above the row
    expect(within(second).getByRole("button", { name: /Set the category of the 12 Sep 2026 row Shop 1/ })).toHaveTextContent("Shopping");
    expect(second.querySelector("[data-bank='posb']")).not.toBeNull();
    expect(within(second).queryByText("12 Sep 2026")).not.toBeInTheDocument();
    expect(row.previousElementSibling).toHaveTextContent("Sat 12 Sep 2026");
    // the account's name is still there for a screen reader
    expect(within(second).getByText("Everyday")).toHaveClass("sr-only");
    expect(container.querySelector(".overflow-x-auto")).toBeNull();
  });

  it("shows a foreign amount in full with its SGD value on the second line", async () => {
    stubTxns({
      rows: [money(1, { amount: -941205, amount_sgd: -1234567, currency: "SEK" }),
        money(2, { amount: -1200, amount_sgd: null, currency: "SEK" })],
      total: 2,
    });
    render(<Transactions />);
    await screen.findByText("Shop 1");
    expect(screen.getByText("−SEK 9,412.05")).toBeInTheDocument();
    expect(screen.getByText("−S$12,345.67")).toBeInTheDocument();
    expect(screen.getByText("no SGD rate")).toBeInTheDocument();
  });

  it("picks a category in one tap on the two-line row too, with the similar-rows chip under it", async () => {
    const calls = stubFetch(page.rows.map((r) => ({ ...r, payee: "A shop" })));
    render(<Transactions />);
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: /Set the category of the 12 Sep 2026 row A shop/ }));
    const box = await screen.findByRole("combobox", { name: "Category for the 12 Sep 2026 row" });
    expect(box.closest("li")).not.toBeNull();
    fireEvent.mouseDown(within(await screen.findByRole("listbox")).getByRole("option", { name: "Donations" }));
    await waitFor(() => expect(calls.some((c) => c.url === "/api/rules/pin")).toBe(true));
    expect(JSON.parse(calls.find((c) => c.url === "/api/rules/pin")!.body!)).toMatchObject({ category: "Donations", payee: "A shop" });
    const chip = await screen.findByRole("button", { name: "Apply to 2 similar rows" });
    expect(chip.closest("li")).not.toBeNull();
  });
});

describe("Transactions on a wide screen", () => {
  beforeEach(() => { window.localStorage.clear(); bare(); stubPhone(true); });
  afterEach(() => { vi.unstubAllGlobals(); clearToasts(); });

  it("keeps the table, and formats a foreign amount with thousands separators and a true minus", async () => {
    stubTxns({
      rows: [money(1, { amount: -941205, amount_sgd: -1234567, currency: "SEK" }), money(2, { amount: 250000, amount_sgd: 250000 })],
      total: 2,
    });
    render(<Transactions />);
    await screen.findByText("Shop 1");
    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.getByText("−SEK 9,412.05")).toBeInTheDocument();
    expect(screen.getByText("−S$12,345.67")).toBeInTheDocument();
    expect(screen.getByText("S$2,500.00")).toBeInTheDocument();
    expect(screen.queryByText(/SEK -/)).not.toBeInTheDocument();
  });

  it("shows the category as a chip whose pencil is hover-only with a mouse and always there on touch", async () => {
    stubTxns({ rows: [money(1)], total: 1 });
    render(<Transactions />);
    const chip = await screen.findByRole("button", { name: /Set the category of the 12 Sep 2026 row Shop 1/ });
    expect(chip).toHaveClass("rounded-full", "bg-tile");
    expect(chip).toHaveTextContent("Shopping");
    const pencil = chip.querySelector("svg")!;
    expect(pencil).toHaveClass("opacity-0", "group-hover:opacity-100", "[@media(pointer:coarse)]:opacity-100");
  });
});

describe("Transactions summary", () => {
  beforeEach(() => { window.localStorage.clear(); bare(); });
  afterEach(() => { vi.unstubAllGlobals(); clearToasts(); });

  it("shows money in, money out and the net of the rows that match", async () => {
    stubTxns({ rows: [money(1)], total: 312, in_sgd: 300000, out_sgd: -1234567, sum_sgd: -934567 });
    render(<Transactions />);
    const totals = await screen.findByRole("group", { name: "Totals for the rows shown" });
    expect(within(totals).getByText("Money in").nextSibling).toHaveTextContent("S$3,000.00");
    expect(within(totals).getByText("Money out").nextSibling).toHaveTextContent("S$12,345.67");
    expect(within(totals).getByText("Net").nextSibling).toHaveTextContent("−S$9,345.67");
    expect(within(totals).getByText("312 transactions, transfers hidden")).toBeInTheDocument();
  });

  it("offers Clear filters only while a filter is set, and clears them all", async () => {
    window.history.replaceState(null, "", "/transactions?q=shop&month=2026-09&category=Donations&account=7&page=1");
    const calls = stubTxns({ rows: [money(1)], total: 1 });
    const { unmount } = render(<Transactions />);
    fireEvent.click(await screen.findByRole("button", { name: "Clear filters" }));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Clear filters" })).not.toBeInTheDocument());
    expect(window.location.search).toBe("");
    expect(calls.at(-1)).not.toMatch(/q=|month=|category=|account=/);
    unmount();
    bare();
    render(<Transactions />);
    await screen.findByText("Shop 1");
    expect(screen.queryByRole("button", { name: "Clear filters" })).not.toBeInTheDocument();
  });

  it("does not count hidden transfers as a filter, and Clear filters leaves the choice as it is", async () => {
    const calls = stubTxns({ rows: [money(1)], total: 1 });
    const { unmount } = render(<Transactions />);
    expect(await screen.findByText("1 transaction, transfers hidden")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Clear filters" })).not.toBeInTheDocument();
    unmount();
    window.history.replaceState(null, "", "/transactions?q=shop");
    render(<Transactions />);
    fireEvent.click(await screen.findByRole("button", { name: "Clear filters" }));
    await waitFor(() => expect(calls.at(-1)).not.toContain("q=shop"));
    expect(calls.at(-1)).toContain("hide_transfers=1");
    expect(screen.getByRole("button", { name: /Transfers hidden/ })).toBeInTheDocument();
    expect(window.location.search).toBe("");
  });

  it("says nothing matches, offers Clear filters and hides the totals, the table and the pager", async () => {
    window.history.replaceState(null, "", "/transactions?q=zzzz");
    stubTxns({ rows: [], total: 0, in_sgd: 0, out_sgd: 0, sum_sgd: 0 });
    render(<Transactions />);
    await screen.findByText("Nothing matches");
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.queryByText(/^Net$/)).not.toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "Transactions pages" })).not.toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Clear filters" })).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));
    await waitFor(() => expect(window.location.search).toBe(""));
    expect(screen.getByRole("searchbox", { name: "Search transactions" })).toHaveValue("");
  });

  it("says there are no transactions when the record is empty and no filter is set", async () => {
    stubTxns({ rows: [], total: 0, in_sgd: 0, out_sgd: 0, sum_sgd: 0 });
    render(<Transactions />);
    await screen.findByText("No transactions yet");
    expect(screen.queryByRole("button", { name: "Clear filters" })).not.toBeInTheDocument();
  });

  it("hides transfers again when the saved choice cannot be changed, by keeping it in the address", async () => {
    window.localStorage.setItem(HIDE_TRANSFERS_KEY, "0");
    const calls = stubFetch();
    render(<Transactions />);
    await screen.findByRole("button", { name: /Transfers shown/ });
    // from here on the browser reads its settings but cannot write them
    const blocked = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("blocked"); });
    try {
      fireEvent.click(screen.getByRole("button", { name: /Transfers shown/ }));
      await screen.findByRole("button", { name: /Transfers hidden/ });
      await waitFor(() => expect(calls.at(-1)!.url).toContain("hide_transfers=1"));
      expect(window.location.search).toBe("?hide_transfers=1");
    } finally {
      blocked.mockRestore();
    }
  });

  it("keeps the chip when hiding transfers leaves no rows, so they can be shown", async () => {
    // a record of only transfers: nothing is left to list until they are shown
    const calls: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      calls.push(url);
      const rows = url.includes("hide_transfers=1") ? [] : [money(1)];
      return new Response(JSON.stringify({ ...page, rows, days: daysOf(rows), total: rows.length }), { status: 200 });
    }));
    render(<Transactions />);
    fireEvent.click(await screen.findByRole("button", { name: /Transfers hidden/ }));
    await screen.findByText("Shop 1");
    expect(calls.at(-1)).not.toContain("hide_transfers");
    expect(screen.getByRole("button", { name: /Transfers shown/ })).toBeInTheDocument();
  });
});

describe("Transactions filters in the address", () => {
  beforeEach(() => { window.localStorage.clear(); bare(); });
  afterEach(() => { vi.unstubAllGlobals(); clearToasts(); });

  const months = { months: ["2026-09", "2026-08"] };

  it("writes the search, category, month, account and hide transfers to the address as they change", async () => {
    const calls = stubTxns({ rows: [money(1)], total: 1, ...months });
    render(<Transactions />);
    await screen.findByText("Shop 1");
    fireEvent.change(screen.getByRole("searchbox", { name: "Search transactions" }), { target: { value: "kopi" } });
    // the category is a combobox: pick from its list
    const box = screen.getByRole("combobox", { name: "Category" });
    expect(box).toHaveValue("All categories");
    fireEvent.focus(box);
    fireEvent.mouseDown(within(screen.getByRole("listbox", { name: "Category" })).getByRole("option", { name: "Donations" }));
    // the month and the account are the app's Select
    fireEvent.click(screen.getByRole("button", { name: "Month" }));
    fireEvent.mouseDown(within(screen.getByRole("listbox", { name: "Month" })).getByRole("option", { name: "Aug 2026" }));
    fireEvent.click(screen.getByRole("button", { name: "Account" }));
    fireEvent.mouseDown(within(screen.getByRole("listbox", { name: "Account" })).getByRole("option", { name: "Everyday" }));
    fireEvent.click(screen.getByRole("button", { name: /Transfers hidden/ }));
    expect(Object.fromEntries(new URLSearchParams(window.location.search))).toEqual({
      q: "kopi", category: "Donations", month: "2026-08", account: "Everyday Savings 111-11111-1", hide_transfers: "0",
    });
    await waitFor(() => expect(calls.at(-1)).toContain("month=2026-08&category=Donations&account=Everyday+Savings+111-11111-1&q=kopi"));
    // choosing "All" in a list drops its parameter
    fireEvent.click(screen.getByRole("button", { name: "Month" }));
    fireEvent.mouseDown(within(screen.getByRole("listbox", { name: "Month" })).getByRole("option", { name: "All months" }));
    expect(new URLSearchParams(window.location.search).has("month")).toBe(false);
  });

  it("opens on the filters in the address, as Back or a shared link does", async () => {
    window.history.replaceState(null, "", "/transactions?q=kopi&category=Donations&month=2026-09&account=7&hide_transfers=1");
    const calls = stubTxns({ rows: [money(1)], total: 1, ...months });
    render(<Transactions />);
    await screen.findByText("Shop 1");
    expect(screen.getByRole("searchbox", { name: "Search transactions" })).toHaveValue("kopi");
    expect(screen.getByRole("combobox", { name: "Category" })).toHaveValue("Donations");
    expect(screen.getByRole("button", { name: "Month" })).toHaveTextContent("Sep 2026");
    expect(screen.getByRole("button", { name: /Transfers hidden/ })).toBeInTheDocument();
    expect(calls[0]).toContain("month=2026-09&category=Donations&account=7&q=kopi");
    expect(calls[0]).toContain("hide_transfers=1");
  });

  it("lets the address turn hide transfers off against the saved choice", async () => {
    window.localStorage.setItem(HIDE_TRANSFERS_KEY, "1");
    window.history.replaceState(null, "", "/transactions?hide_transfers=0");
    const calls = stubTxns({ rows: [money(1)], total: 1 });
    render(<Transactions />);
    await screen.findByText("Shop 1");
    expect(screen.getByRole("button", { name: /Transfers shown/ })).toBeInTheDocument();
    expect(calls[0]).not.toContain("hide_transfers");
  });

  it("lets the address turn hide transfers on against a saved choice of off", async () => {
    window.localStorage.setItem(HIDE_TRANSFERS_KEY, "0");
    window.history.replaceState(null, "", "/transactions?hide_transfers=1");
    const calls = stubTxns({ rows: [money(1)], total: 1 });
    render(<Transactions />);
    await screen.findByText("Shop 1");
    expect(screen.getByRole("button", { name: /Transfers hidden/ })).toBeInTheDocument();
    expect(calls[0]).toContain("hide_transfers=1");
    expect(window.localStorage.getItem(HIDE_TRANSFERS_KEY)).toBe("0");
  });

  it("follows a link to the same page (the sidebar's, a chart bar's)", async () => {
    window.history.replaceState(null, "", "/transactions?q=kopi");
    stubTxns({ rows: [money(1)], total: 1 });
    render(<Transactions />);
    await screen.findByText("Shop 1");
    act(() => {
      window.history.pushState(null, "", "/transactions");
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
    await waitFor(() => expect(screen.getByRole("searchbox", { name: "Search transactions" })).toHaveValue(""));
  });

  it("keeps the page in the address, opens on it, and drops it when a filter changes", async () => {
    const row = (i: number) => ({ ...page.rows[0], key: `r${i}`, payee: `Shop ${i}` });
    const calls: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      calls.push(url);
      const q = new URL(url, "http://x").searchParams;
      const limit = Number(q.get("limit")), offset = Number(q.get("offset"));
      const rows = Array.from({ length: Math.max(0, Math.min(limit, 312 - offset)) }, (_, i) => row(offset + i + 1));
      return new Response(JSON.stringify({ ...page, days: daysOf(rows), rows, total: 312 }), { status: 200 });
    }));
    const { unmount } = render(<Transactions />);
    await screen.findByText("Shop 1");
    expect(window.location.search).toBe("");
    fireEvent.click(screen.getByRole("button", { name: "Page 3" }));
    await screen.findByText("Shop 51");
    expect(new URLSearchParams(window.location.search).get("page")).toBe("3");
    // a link with the page opens on it
    unmount();
    render(<Transactions />);
    await screen.findByText("51 to 75 of 312");
    expect(calls.at(-1)).toContain("offset=50");
    // a filter puts the list back on page 1 and the address with it
    fireEvent.change(screen.getByRole("searchbox", { name: "Search transactions" }), { target: { value: "shop" } });
    await waitFor(() => expect(new URLSearchParams(window.location.search).has("page")).toBe(false));
    expect(new URLSearchParams(window.location.search).get("q")).toBe("shop");
    expect(calls.at(-1)).toContain("offset=0&q=shop");
  });

  it("makes the four filters one family of 32px boxes, none of them a native select", async () => {
    stubTxns({ rows: [money(1)], total: 1, ...months });
    render(<Transactions />);
    await screen.findByText("Shop 1");
    const boxes = [screen.getByRole("combobox", { name: "Category" }), screen.getByRole("button", { name: "Month" }),
      screen.getByRole("button", { name: "Account" }), screen.getByRole("searchbox", { name: "Search transactions" })];
    for (const el of boxes) expect(el).toHaveClass("h-8", "rounded-md", "border", "border-line", "bg-panel");
    expect(document.querySelector("select")).toBeNull();
  });
});

describe("The row form opens on what the row has", () => {
  beforeEach(() => { window.localStorage.clear(); bare(); });
  afterEach(() => { vi.unstubAllGlobals(); clearToasts(); });

  function stubRow(extra: object) {
    const calls: { url: string; body?: string }[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, body: init?.body as string | undefined });
      const body = url.startsWith("/api/transactions")
        ? { ...page, categories: ["(none)", "Donations", "Shopping"], rows: [{ ...page.rows[0], payee: "Shop", category: "Shopping" }] }
        : url.startsWith("/api/rules/rows") ? { ...one(false), rows: [{ ...one(false).rows[0], ...extra }] }
        : {};
      return new Response(JSON.stringify(body), { status: 200 });
    }));
    return calls;
  }

  // G13: the form sits behind More, under the row once its category is picked; picking the one it has saves nothing
  async function openMore() {
    fireEvent.click(await screen.findByRole("button", { name: /Set the category of the 12 Sep 2026 row Shop/ }));
    fireEvent.mouseDown(within(await screen.findByRole("listbox")).getByRole("option", { name: "Shopping" }));
    fireEvent.click(await screen.findByRole("button", { name: "More" }));
  }

  it("selects the row's category, so saving it keeps it", async () => {
    const calls = stubRow({ category: "Shopping", payee: "Shop", field: "merchant" });
    render(<Transactions />);
    await openMore();
    const box = await screen.findByRole("combobox", { name: "Category for the 12 Sep 2026 row" });
    expect(box).toHaveValue("Shopping");
    expect(screen.getByRole("button", { name: "Save for this row" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "Save for this row" }));
    await waitFor(() => expect(calls.some((c) => c.url === "/api/rules/pin")).toBe(true));
    expect(JSON.parse(calls.find((c) => c.url === "/api/rules/pin")!.body!)).toMatchObject({ category: "Shopping", payee: "Shop", kind: "Merchant" });
  });

  it("still opens empty for a row with no category", async () => {
    stubRow({ category: "", payee: "" });
    render(<Transactions />);
    await openMore();
    expect(await screen.findByRole("combobox", { name: "Category for the 12 Sep 2026 row" })).toHaveValue("");
    expect(screen.getByRole("button", { name: "Save for this row" })).toBeDisabled();
  });

  it.each([
    { name: "a merchant's own line", extra: { field: "merchant", similar: { match_on: "merchant", pattern: "SHOP*", payee: "Shop", kind: "Merchant" } }, kind: "Merchant" },
    { name: "a memo on a categorised row", extra: { field: "memo", similar: { match_on: "memo", pattern: "SHOP*", payee: "Shop", kind: "Merchant" } }, kind: "Merchant" },
    { name: "a counterparty", extra: { field: "counterparty", similar: { match_on: "counterparty", pattern: "A FRIEND*", payee: "A Friend", kind: "Person" } }, kind: "Person" },
    { name: "a bank code only", extra: { field: "bank_txn_type", similar: null }, kind: "Merchant" },
  ])("opens the kind on $name as $kind", async ({ extra, kind }) => {
    stubRow({ category: "Shopping", payee: "Shop", ...extra });
    render(<Transactions />);
    await openMore();
    expect(await screen.findByRole("button", { name: "Kind for the 12 Sep 2026 row" })).toHaveTextContent(kind);
  });
});

describe("Transactions: review fixes", () => {
  beforeEach(() => { window.localStorage.clear(); bare(); });
  afterEach(() => { vi.unstubAllGlobals(); clearToasts(); });

  it("keeps an explicit hide_transfers=0 when Clear filters runs", async () => {
    window.history.replaceState(null, "", "/transactions?q=shop&hide_transfers=0");
    const calls = stubTxns({ rows: [money(1)], total: 1 });
    render(<Transactions />);
    fireEvent.click(await screen.findByRole("button", { name: "Clear filters" }));
    await waitFor(() => expect(window.location.search).toBe("?hide_transfers=0"));
    expect(screen.getByRole("button", { name: /Transfers shown/ })).toBeInTheDocument();
    expect(calls.at(-1)).not.toContain("hide_transfers");
    expect(screen.queryByRole("button", { name: "Clear filters" })).not.toBeInTheDocument();
  });

  it("writes a saved choice to show transfers into the address, so a copied link keeps the view", async () => {
    window.localStorage.setItem(HIDE_TRANSFERS_KEY, "0");
    stubTxns({ rows: [money(1)], total: 1 });
    render(<Transactions />);
    await screen.findByText("Shop 1");
    expect(window.location.search).toBe("?hide_transfers=0");
  });

  it("keeps the page of a link to another filter, whatever the old filter's total was", async () => {
    const row = (i: number) => ({ ...page.rows[0], key: `r${i}`, payee: `Shop ${i}` });
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      const q = new URL(url, "http://x").searchParams;
      const total = q.get("q") === "one" ? 1 : 312;
      const limit = Number(q.get("limit")), offset = Number(q.get("offset"));
      const rows = Array.from({ length: Math.max(0, Math.min(limit, total - offset)) }, (_, i) => row(offset + i + 1));
      return new Response(JSON.stringify({ ...page, days: daysOf(rows), rows, total }), { status: 200 });
    }));
    window.history.replaceState(null, "", "/transactions?q=one");
    render(<Transactions />);
    await screen.findByText("Shop 1");
    act(() => {
      window.history.pushState(null, "", "/transactions?page=3");
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
    await screen.findByText("51 to 75 of 312");
    expect(new URLSearchParams(window.location.search).get("page")).toBe("3");
    expect(screen.getByText("Shop 51")).toBeInTheDocument();
  });

  it("keeps what was typed in a row's form when the layout changes under it", async () => {
    let wide = false;
    const listeners = new Set<() => void>();
    vi.stubGlobal("matchMedia", (query: string) => ({
      get matches() { return wide; }, media: query,
      addEventListener: (_: string, f: () => void) => listeners.add(f),
      removeEventListener: (_: string, f: () => void) => listeners.delete(f),
    }));
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      const body = url.startsWith("/api/transactions") ? { ...page, rows: [{ ...page.rows[0], payee: "A shop" }] }
        : { ...one(false), rows: [{ ...one(false).rows[0], category: "Shopping", payee: "A shop" }] };
      return new Response(JSON.stringify(body), { status: 200 });
    }));
    render(<Transactions />);
    fireEvent.click(await screen.findByRole("button", { name: /Set the category of the 12 Sep 2026 row A shop/ }));
    fireEvent.mouseDown(within(await screen.findByRole("listbox")).getByRole("option", { name: "Donations" }));
    fireEvent.click(await screen.findByRole("button", { name: "More" }));
    fireEvent.change(await screen.findByRole("textbox", { name: "Note for the 12 Sep 2026 row" }), { target: { value: "a gift" } });
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    act(() => { wide = true; listeners.forEach((f) => f()); });
    expect(await screen.findByRole("table")).toBeInTheDocument();
    expect(await screen.findByRole("textbox", { name: "Note for the 12 Sep 2026 row" })).toHaveValue("a gift");
  });
});

describe("Transactions page survives a reload and Back", () => {
  beforeEach(() => { window.localStorage.clear(); bare(); });
  afterEach(() => { vi.unstubAllGlobals(); clearToasts(); });

  it("comes back on page 4 after a reload, and after leaving and pressing Back", async () => {
    const row = (i: number) => ({ ...page.rows[0], key: `r${i}`, payee: `Shop ${i}` });
    const calls: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      calls.push(url);
      const q = new URL(url, "http://x").searchParams;
      const limit = Number(q.get("limit")), offset = Number(q.get("offset"));
      const rows = Array.from({ length: Math.max(0, Math.min(limit, 312 - offset)) }, (_, i) => row(offset + i + 1));
      return new Response(JSON.stringify({ ...page, days: daysOf(rows), rows, total: 312 }), { status: 200 });
    }));
    const first = render(<Transactions />);
    await screen.findByText("Shop 1");
    fireEvent.click(screen.getByRole("button", { name: "Page 4" }));
    await screen.findByText("Shop 76");
    expect(window.location.search).toBe("?page=4");
    // a reload: a fresh page opened on the same address
    first.unmount();
    const second = render(<Transactions />);
    await screen.findByText("76 to 100 of 312");
    expect(calls.at(-1)).toContain("limit=25&offset=75");
    expect(screen.getByRole("button", { name: "Page 4" })).toHaveAttribute("aria-current", "page");
    // leave for another page of the app, then Back: the entry we left still carries the page
    second.unmount();
    window.history.pushState(null, "", "/spending");
    // jsdom moves back on a later task, so wait for the popstate it fires
    // rather than a fixed time, which a busy machine can overrun
    await act(async () => {
      const popped = new Promise((r) => window.addEventListener("popstate", r, { once: true }));
      window.history.back();
      await popped;
    });
    expect(window.location.pathname + window.location.search).toBe("/transactions?page=4");
    render(<Transactions />);
    await screen.findByText("76 to 100 of 312");
    expect(screen.getByText("Shop 76")).toBeInTheDocument();
  });
});

// G13: the rows sit under a header for their day, with that day's spending and money in
describe("Transactions grouped by day", () => {
  beforeEach(() => { window.localStorage.clear(); bare(); });
  afterEach(() => vi.unstubAllGlobals());

  const rows = [
    money(1, { date: "2026-09-12", amount: -4550, amount_sgd: -4550 }),
    money(2, { date: "2026-09-12", amount: -1000, amount_sgd: -1000 }),
    money(3, { date: "2026-09-12", amount: 250000, amount_sgd: 250000 }),
    money(4, { date: "2026-09-10", amount: -700, amount_sgd: -700 }),
  ];

  it("puts a header on each day with its spending and money in, and no date on the rows", async () => {
    stubTxns({ rows, total: 4 });
    render(<Transactions />);
    await screen.findByText("Shop 1");
    const headers = screen.getAllByRole("rowheader");
    expect(headers).toHaveLength(2);
    // each day is its own row group, headed by its day
    expect(screen.getAllByRole("rowgroup").filter((g) => g.tagName === "TBODY")).toHaveLength(2);
    expect(headers[0]).toHaveTextContent("Sat 12 Sep 2026");
    expect(headers[0]).toHaveTextContent("Spent S$55.50");
    expect(headers[0]).toHaveTextContent("In S$2,500.00");
    expect(headers[1]).toHaveTextContent("Thu 10 Sep 2026");
    expect(headers[1]).toHaveTextContent("Spent S$7.00");
    expect(headers[1]).not.toHaveTextContent("In S$");
    expect(screen.queryByRole("columnheader", { name: "Date" })).not.toBeInTheDocument();
    // no row repeats its date: the only places it reads are the headers and the category chip's label
    const body = screen.getByRole("table").querySelectorAll("tbody td");
    expect(Array.from(body).some((td) => /Sep 2026/.test(td.textContent ?? ""))).toBe(false);
  });

  it("uses the server's totals for the day, so a day split across pages shows the whole day on both", async () => {
    stubTxns({ rows: rows.slice(2), total: 4, days: [
      { date: "2026-09-12", count: 3, in_sgd: 250000, out_sgd: -5550, continued: true, continues: false },
      { date: "2026-09-10", count: 1, in_sgd: 0, out_sgd: -700, continued: false, continues: false }] });
    render(<Transactions />);
    await screen.findByText("Shop 3");
    const [split, next] = screen.getAllByRole("rowheader");
    // only one of the day's three rows is on this page, and the header still adds up the day
    expect(split).toHaveTextContent("Sat 12 Sep 2026");
    expect(split).toHaveTextContent("continued");
    expect(split).toHaveTextContent("Spent S$55.50");
    expect(split).toHaveTextContent("In S$2,500.00");
    expect(next).not.toHaveTextContent("continued");
  });

  it("says the last day goes on onto the next page, and only that day", async () => {
    stubTxns({ rows, total: 6, days: [
      { date: "2026-09-12", count: 3, in_sgd: 250000, out_sgd: -5550, continued: false, continues: false },
      { date: "2026-09-10", count: 3, in_sgd: 0, out_sgd: -2100, continued: false, continues: true }] });
    render(<Transactions />);
    await screen.findByText("Shop 1");
    const [first, last] = screen.getAllByRole("rowheader");
    expect(last).toHaveTextContent("continues on the next page");
    expect(first).not.toHaveTextContent("continues on the next page");
    expect(last).not.toHaveTextContent("continued");
  });

  it("does not say continued on a day that starts on its page", async () => {
    stubTxns({ rows, total: 4 });
    render(<Transactions />);
    await screen.findByText("Shop 1");
    expect(screen.queryByText("continued")).not.toBeInTheDocument();
  });

  it("groups the phone's rows under the same headers, and keeps the date off the rows", async () => {
    stubPhone(false);
    stubTxns({ rows, total: 4 });
    const { container } = render(<Transactions />);
    await screen.findByText("Shop 1");
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    const items = Array.from(container.querySelectorAll("ul > li")).map((li) => li.textContent ?? "");
    expect(items[0]).toMatch(/^Sat 12 Sep 2026.*Spent S\$55\.50.*In S\$2,500\.00/);
    expect(items.filter((t) => /Thu 10 Sep 2026/.test(t))).toHaveLength(1);
    expect(items.filter((t) => /Shop \d/.test(t) && /\d{1,2} Sep 2026/.test(t))).toEqual([]);
    expect(container.querySelectorAll("ul > li")).toHaveLength(6);
  });
});

describe("Transactions from Home's needs-a-category line", () => {
  beforeEach(() => { window.localStorage.clear(); bare(); });
  afterEach(() => vi.unstubAllGlobals());

  it("asks for the rows its count has, and Clear filters drops that", async () => {
    window.history.replaceState(null, "", "/transactions?category=(none)&synthetic=0");
    const calls = stubTxns({ rows: [money(1)], total: 1 });
    render(<Transactions />);
    await screen.findByText("Shop 1");
    expect(calls[0]).toContain("category=%28none%29");
    expect(calls[0]).toContain("synthetic=0");
    expect(calls[0]).toContain("hide_transfers=1");
    fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));
    await waitFor(() => expect(calls.at(-1)).not.toContain("synthetic"));
    expect(window.location.search).toBe("");
  });
});
