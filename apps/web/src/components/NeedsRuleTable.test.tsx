import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { NeedsRule, SuggestReply, Suggestion } from "../api";
import { NeedsRuleTable, clashes } from "./NeedsRuleTable";
import { json } from "../test-helpers";

const GROUPS: NeedsRule[] = [
  { text: "TOAST BOX - RAFFLES PL", field: "merchant", rows: 4, amount: 2280, first: "2026-08-02", last: "2026-08-20", keys: [] },
  { text: "VM-B302 PL", field: "merchant", rows: 10, amount: 1450, first: "2026-08-01", last: "2026-08-28", keys: [] },
];
const draft = (pattern: string, payee: string): Suggestion => ({ match_on: "merchant", pattern, payee, kind: "Merchant" });
const REPLY: SuggestReply = {
  enabled: true, provider: "anthropic", model: "claude-haiku-5-5",
  categories: ["Eating out", "Groceries", "Shopping"],
  drafts: { "TOAST BOX - RAFFLES PL": draft("TOAST BOX*", "Toast Box"), "VM-B302 PL": draft("VM-B302 PL*", "VM-B302 PL") },
  suggestions: [
    { text: "TOAST BOX - RAFFLES PL", payee: "Toast Box", category: "Eating out", kind: "Merchant", confident: true, note: "" },
    { text: "VM-B302 PL", payee: "Vending machine", category: "", kind: "Merchant", confident: false, note: "a vending machine?" },
  ],
};

// the one TOAST BOX row the record has loaded, before and after it is pinned
const toast = (pinned: boolean) => ({
  rows: [{ key: "t4", date: "2026-08-20", amount: -570, currency: "SGD", amount_sgd: -570, account: "POSB Savings", bank: "posb",
    text: "TOAST BOX - RAFFLES PL", field: "merchant", payee: "", category: "",
    pinned: pinned ? { payee: "", category: "Shopping", file: "keys.csv", line: 2, applied: true } : null }],
  total: 1, categories: ["Eating out", "Shopping"], kinds: ["Merchant", "Person"],
});

function stubFetch(reply: SuggestReply) {
  const calls: { url: string; body?: string }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, body: init?.body as string | undefined });
    const body = url === "/api/rules/suggest" ? reply
      : url.startsWith("/api/rules/rows") ? toast(calls.some((c) => c.url === "/api/rules/pin"))
      : { added: 1, lint: {} };
    return json(body);
  }));
  return calls;
}

describe("NeedsRuleTable", () => {
  it("labels the payee and category fields for a phone, where the column headings are gone", async () => {
    stubFetch(REPLY);
    render(<NeedsRuleTable groups={GROUPS} canLoad onSaved={vi.fn()} />);
    await screen.findByText(/1 suggested by claude-haiku-5-5/);
    // under 768px the table is stacked rows, so each field names itself; from 768px up the headings do
    const labels = screen.getAllByText(/^(Payee|Category)$/).filter((el) => el.tagName === "SPAN");
    expect(labels).toHaveLength(4);
    for (const el of labels) expect(el).toHaveClass("md:hidden");
    expect(screen.getByRole("table").className).toContain("max-md:block");
    for (const row of screen.getAllByRole("row").slice(1)) expect(row.className).toContain("max-md:grid");
  });

  it("ticks the sure suggestions and flags the rest", async () => {
    stubFetch(REPLY);
    render(<NeedsRuleTable groups={GROUPS} canLoad onSaved={vi.fn()} />);
    expect(await screen.findByText(/1 suggested by claude-haiku-5-5; 1 need you/)).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "Save a rule for TOAST BOX - RAFFLES PL" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Save a rule for VM-B302 PL" })).not.toBeChecked();
    expect(screen.getByText("Not sure: a vending machine?")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save 1 rule and load" })).toBeEnabled();
  });

  it("keeps the wildcard out of sight until Edit pattern is chosen, and the sure rows ticked", async () => {
    stubFetch(REPLY);
    render(<NeedsRuleTable groups={GROUPS} canLoad onSaved={vi.fn()} />);
    expect(await screen.findByRole("checkbox", { name: "Save a rule for TOAST BOX - RAFFLES PL" })).toBeChecked();
    expect(screen.queryByRole("textbox", { name: /^Pattern for / })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Edit pattern for TOAST BOX - RAFFLES PL" }));
    expect(screen.getByRole("textbox", { name: "Pattern for TOAST BOX - RAFFLES PL" })).toHaveValue("TOAST BOX*");
    expect(screen.queryByRole("button", { name: "Edit pattern for TOAST BOX - RAFFLES PL" })).not.toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "Save a rule for TOAST BOX - RAFFLES PL" })).toBeChecked();
  });

  it("saves the ticked rules marked as AI, then loads", async () => {
    const calls = stubFetch(REPLY);
    const onSaved = vi.fn(async () => {});
    render(<NeedsRuleTable groups={GROUPS} canLoad onSaved={onSaved} />);
    const box = await screen.findByRole("combobox", { name: "Category for VM-B302 PL" });
    fireEvent.focus(box);
    fireEvent.change(box, { target: { value: "shop" } });
    fireEvent.keyDown(box, { key: "Enter" });
    fireEvent.click(screen.getByRole("button", { name: "Save 2 rules and load" }));
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(true));
    const sent = JSON.parse(calls.find((c) => c.url === "/api/rules/batch")!.body!);
    expect(sent.rules).toEqual([
      { match_on: "merchant", pattern: "TOAST BOX*", payee: "Toast Box", kind: "Merchant", category: "Eating out", notes: "[ai] suggested by claude-haiku-5-5" },
      { match_on: "merchant", pattern: "VM-B302 PL*", payee: "Vending machine", kind: "Merchant", category: "Shopping", notes: "[ai] suggested by claude-haiku-5-5" },
    ]);
  });

  it("counts only the payees it can show and lists the rest to set row by row", async () => {
    stubFetch(REPLY);
    const coded = { text: "ADVICE", field: "bank_txn_type", rows: 1, amount: 100, first: "2026-08-03", last: "2026-08-03", keys: ["k1"] };
    render(<NeedsRuleTable groups={[...GROUPS, coded]} canLoad onSaved={vi.fn()} />);
    expect(await screen.findByText(/^2 payees had no rule/)).toBeInTheDocument();
    expect(screen.getByText(/1 more prints only a bank code/)).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Set one row instead" })).toHaveLength(3);
  });

  it("sets one row of a group on its own, as a key rule", async () => {
    const calls = stubFetch(REPLY);
    const onSaved = vi.fn();
    render(<NeedsRuleTable groups={GROUPS} canLoad onSaved={onSaved} />);
    await screen.findByText(/^2 payees had no rule/);
    fireEvent.click(screen.getAllByRole("button", { name: "Set one row instead" })[0]);
    fireEvent.click(await screen.findByRole("button", { name: "Set the 20 Aug 2026 row of −S$5.70 on its own" }));
    expect(calls.some((c) => c.url === "/api/rules/rows?text=TOAST+BOX+-+RAFFLES+PL")).toBe(true);
    // G18: the row names its account after the bank's mark
    expect(screen.getByText("POSB Savings").querySelector("[data-bank]")).toHaveAttribute("data-bank", "posb");
    const box = screen.getByRole("combobox", { name: "Category for the 20 Aug 2026 row" });
    fireEvent.focus(box);
    fireEvent.mouseDown(screen.getByRole("option", { name: "Shopping" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Note for the 20 Aug 2026 row" }), { target: { value: "a gift" } });
    fireEvent.click(screen.getByRole("button", { name: "Save for this row" }));
    expect(await screen.findByText("Set to Shopping by keys.csv line 2.")).toBeInTheDocument();
    const sent = JSON.parse(calls.find((c) => c.url === "/api/rules/pin")!.body!);
    expect(sent).toEqual({ key: "t4", category: "Shopping", payee: "", kind: "Merchant", note: "a gift" });
    // the pattern table is untouched and no load runs: the pin filed its row at once (G13)
    expect(calls.some((c) => c.url === "/api/rules/batch")).toBe(false);
    expect(onSaved).not.toHaveBeenCalled();
  });

  it("says how many rows the record has not loaded yet", async () => {
    stubFetch(REPLY);
    render(<NeedsRuleTable groups={GROUPS} canLoad onSaved={vi.fn()} />);
    await screen.findByText(/^2 payees had no rule/);
    fireEvent.click(screen.getAllByRole("button", { name: "Set one row instead" })[0]);
    expect(await screen.findByText(/3 more are not in your record yet/)).toBeInTheDocument();
  });

  it("finds rules that would catch another row's text", () => {
    const row = (text: string, pattern: string, category: string, match_on = "merchant") =>
      ({ text, match_on, pattern, payee: "ABC", category, picked: true });
    expect([...clashes([row("ABC - CAFE", "ABC*", "Eating out"), row("ABC - BOOKS", "ABC*", "Shopping")]).keys()])
      .toEqual(["ABC - CAFE", "ABC - BOOKS"]);
    expect(clashes([row("ABC - CAFE", "ABC - CAFE*", "Eating out"), row("ABC - BOOKS", "ABC - BOOKS*", "Shopping")]).size).toBe(0);
    expect(clashes([row("ABC - CAFE", "ABC*", "Eating out"), row("ABC  - cafe 2", "ABC*", "Eating out")]).size).toBe(0);
    expect(clashes([row("ABC - CAFE", "ABC*", "Eating out"), row("ABC LEE", "ABC*", "Person to person", "counterparty")]).size).toBe(0);
  });

  it("holds the save until a clashing pattern is narrowed", async () => {
    stubFetch({ ...REPLY, drafts: { "TOAST BOX - RAFFLES PL": draft("T*", "Toast Box"), "VM-B302 PL": draft("VM*", "VM") } });
    render(<NeedsRuleTable groups={GROUPS} canLoad onSaved={vi.fn()} />);
    const box = await screen.findByRole("combobox", { name: "Category for VM-B302 PL" });
    fireEvent.focus(box);
    fireEvent.mouseDown(screen.getByRole("option", { name: "Shopping" }));
    fireEvent.click(screen.getByRole("button", { name: "Edit pattern for VM-B302 PL" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Pattern for VM-B302 PL" }), { target: { value: "*" } });
    expect(screen.getByText(/The rule \* would catch this too/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save 2 rules and load" })).toBeDisabled();
    fireEvent.change(screen.getByRole("textbox", { name: "Pattern for VM-B302 PL" }), { target: { value: "VM-B302*" } });
    expect(screen.getByRole("button", { name: "Save 2 rules and load" })).toBeEnabled();
  });

  it("opens the pattern box on a clash and keeps it open while the pattern is narrowed", async () => {
    stubFetch({ ...REPLY, drafts: { "TOAST BOX - RAFFLES PL": draft("*", "Toast Box"), "VM-B302 PL": draft("*", "VM") } });
    render(<NeedsRuleTable groups={GROUPS} canLoad onSaved={vi.fn()} />);
    const box = await screen.findByRole("combobox", { name: "Category for VM-B302 PL" });
    fireEvent.focus(box);
    fireEvent.mouseDown(screen.getByRole("option", { name: "Shopping" }));
    // both rows catch each other's text, so both boxes are open without a click on Edit pattern
    fireEvent.change(screen.getByRole("textbox", { name: "Pattern for TOAST BOX - RAFFLES PL" }), { target: { value: "T" } });
    // the other row's pattern still catches this one's text, so this box stays open, and the other box folds away until asked for
    expect(screen.getByText(/The rule \* would catch this too/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Edit pattern for VM-B302 PL" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Pattern for VM-B302 PL" }), { target: { value: "V" } });
    // the clash is gone, and the boxes being typed in stay for the user to finish
    expect(screen.queryByText(/would catch this too/)).not.toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Pattern for VM-B302 PL" })).toHaveValue("V");
    expect(screen.getByRole("textbox", { name: "Pattern for TOAST BOX - RAFFLES PL" })).toHaveValue("T");
  });

  it("keeps same-pattern rules on different fields apart", async () => {
    const calls = stubFetch({ ...REPLY, enabled: false, suggestions: [], drafts: {
      "GRAB A-1": { match_on: "merchant", pattern: "GRAB*", payee: "Grab", kind: "Merchant" },
      "GRAB SG": { match_on: "counterparty", pattern: "GRAB*", payee: "Grab", kind: "Merchant" },
    } });
    const grab = (text: string, field: string) => ({ text, field, rows: 1, amount: 100, first: "2026-08-01", last: "2026-08-01", keys: [] });
    render(<NeedsRuleTable groups={[grab("GRAB A-1", "merchant"), grab("GRAB SG", "counterparty")]} canLoad={false} onSaved={vi.fn(async () => {})} />);
    for (const t of ["GRAB A-1", "GRAB SG"]) {
      const box = await screen.findByRole("combobox", { name: `Category for ${t}` });
      fireEvent.focus(box);
      fireEvent.mouseDown(screen.getByRole("option", { name: "Shopping" }));
    }
    fireEvent.click(screen.getByRole("button", { name: "Save 2 rules" }));
    await waitFor(() => expect(calls.some((c) => c.url === "/api/rules/batch")).toBe(true));
    const sent = JSON.parse(calls.find((c) => c.url === "/api/rules/batch")!.body!).rules;
    expect(sent.map((r: { match_on: string }) => r.match_on)).toEqual(["merchant", "counterparty"]);
  });

  it("works without AI: guesses the payee, you pick the category", async () => {
    const calls = stubFetch({ ...REPLY, enabled: false, model: undefined, suggestions: [] });
    render(<NeedsRuleTable groups={GROUPS} canLoad={false} onSaved={vi.fn(async () => {})} />);
    expect(await screen.findByText(/Pick a category for each/)).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Payee for TOAST BOX - RAFFLES PL" })).toHaveValue("Toast Box");
    const box = screen.getByRole("combobox", { name: "Category for TOAST BOX - RAFFLES PL" });
    fireEvent.focus(box);
    fireEvent.mouseDown(screen.getByRole("option", { name: "Eating out" }));
    fireEvent.click(screen.getByRole("button", { name: "Save 1 rule" }));
    await waitFor(() => expect(calls.some((c) => c.url === "/api/rules/batch")).toBe(true));
    expect(JSON.parse(calls.find((c) => c.url === "/api/rules/batch")!.body!).rules[0].notes).toBe("");
  });

  it("pages the payees 25 at a time and keeps what was typed on another page", async () => {
    const many: NeedsRule[] = Array.from({ length: 40 }, (_, i) => ({
      text: `SHOP ${String(i + 1).padStart(2, "0")}`, field: "merchant", rows: 2, amount: 500, first: "2026-08-01", last: "2026-08-09", keys: [],
    }));
    const reply: SuggestReply = {
      enabled: false, provider: "", model: "", categories: ["Shopping"], suggestions: [],
      drafts: Object.fromEntries(many.map((g) => [g.text, draft(`${g.text}*`, g.text)])),
    };
    stubFetch(reply);
    render(<NeedsRuleTable groups={many} canLoad onSaved={vi.fn()} />);
    expect(await screen.findByText("1 to 25 of 40")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Payee for SHOP 25" })).toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: "Payee for SHOP 26" })).not.toBeInTheDocument();
    fireEvent.change(screen.getByRole("textbox", { name: "Payee for SHOP 03" }), { target: { value: "Shop Three" } });
    fireEvent.click(screen.getByRole("button", { name: "Next page" }));
    expect(screen.getByText("26 to 40 of 40")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Payee for SHOP 26" })).toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: "Payee for SHOP 03" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Previous page" }));
    expect(screen.getByRole("textbox", { name: "Payee for SHOP 03" })).toHaveValue("Shop Three");
  });

  it("says when a clash is on another page and goes to it", async () => {
    // SHOP 30 is caught by the pattern of SHOP 31 under another category
    const many: NeedsRule[] = Array.from({ length: 40 }, (_, i) => ({
      text: `SHOP ${String(i + 1).padStart(2, "0")}`, field: "merchant", rows: 2, amount: 500, first: "2026-08-01", last: "2026-08-09", keys: [],
    }));
    const reply: SuggestReply = {
      enabled: false, provider: "", model: "", categories: ["Shopping", "Groceries"], suggestions: [],
      drafts: Object.fromEntries(many.map((g) => [g.text, draft(g.text === "SHOP 31" ? "SHOP 3*" : `${g.text}*`, g.text)])),
    };
    stubFetch(reply);
    render(<NeedsRuleTable groups={many} canLoad onSaved={vi.fn()} />);
    const pick = async (text: string, category: string) => {
      const box = await screen.findByRole("combobox", { name: `Category for ${text}` });
      fireEvent.focus(box);
      fireEvent.change(box, { target: { value: category } });
      fireEvent.keyDown(box, { key: "Enter" });
    };
    await pick("SHOP 01", "Shopping");
    fireEvent.click(screen.getByRole("button", { name: "Next page" }));
    await pick("SHOP 30", "Shopping");
    await pick("SHOP 31", "Groceries");
    fireEvent.click(screen.getByRole("button", { name: "Previous page" }));
    expect(screen.getByText(/payees? clash/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Save/ })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Go to the first" }));
    expect(screen.getByText("26 to 40 of 40")).toBeInTheDocument();
    expect(screen.getAllByText(/would catch this too/).length).toBeGreaterThan(0);
  });
});
