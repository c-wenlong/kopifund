import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Rule, RuleGroup, RulePreview, RulesState } from "../api";
import { Rules } from "./Rules";
import { leadingIcon } from "../test-icons";
import { Toaster } from "../components/Toast";
import { choose } from "../test-select";
import { navigate } from "../router";
import { useUrlParams } from "../useUrlParams";
import { json, settle, stubMatchMedia } from "../test-helpers";

// another reader of the address, as the copilot panel is: it sends every query parameter as the page's filters
function AddressProbe() {
  const [params] = useUrlParams();
  return <output data-testid="address">{params.toString()}</output>;
}

// invented rules: one employer pinned row by row, a coffee shop, and a shipped rule
const pin = (key: string, date: string) => ({ key, date, amount: 250000, currency: "SGD", text: "SALARY PAYMENT" });
const rule = (over: Partial<Rule>): Rule => ({
  id: "keys.csv:2", file: "keys.csv", line: 2, revision: "r0", shared: false, order: 1, tier: "key", pattern: "a1",
  payee: "Acme Co", kind: "Employer", country: "SGP", category: "Salary", notes: "", recurring: false,
  matched: 1, wins: 1, problems: [], pins: [pin("a1", "2026-08-25")], ...over,
});
const RULES: Rule[] = [
  rule({}),
  rule({ id: "keys.csv:3", line: 3, order: 2, pattern: "a2", pins: [pin("a2", "2026-07-25")] }),
  rule({
    id: "merchants.csv:2", file: "merchants.csv", revision: "r3", order: 3, tier: "merchant", pattern: "KOPITIAM*", payee: "Kopitiam",
    kind: "Merchant", category: "Eating out", matched: 6, wins: 6, pins: [],
    problems: [{ check: "Two countries", why: "This payee's rules name different countries.", detail: "Kopitiam: MYS in ..." }],
  }),
  rule({
    id: "30-merchants.csv:2", file: "30-merchants.csv", shared: true, order: 4, tier: "merchant", pattern: "GRAB*",
    payee: "Grab", kind: "Merchant", category: "Transport", matched: 40, wins: 40, pins: [],
  }),
];
const group = (over: Partial<RuleGroup>): RuleGroup => ({
  id: "", payee: "", kind: "Merchant", country: "SGP", category: "", categories: [], patterns: 1, pins: 0, rows: 0,
  problems: 0, shared: 0, rules: [], ...over,
});
const STATE: RulesState = {
  needs: [], needs_total: 0, categories: ["Eating out", "Salary", "Shopping", "Transport"], kinds: ["Merchant", "Person", "Employer", "Institution"],
  tiers: ["key", "type", "flow", "merchant", "counterparty", "memo", "reference"],
  tier_files: { key: "keys.csv", type: "types.csv", flow: "types.csv", merchant: "merchants.csv", counterparty: "people.csv", memo: "people.csv", reference: "people.csv" },
  rules: RULES,
  groups: [
    group({ id: "payee:Grab", payee: "Grab", category: "Transport", categories: ["Transport"], rows: 40, shared: 1, rules: ["30-merchants.csv:2"] }),
    group({ id: "payee:Kopitiam", payee: "Kopitiam", category: "Eating out", categories: ["Eating out"], rows: 6, problems: 1, rules: ["merchants.csv:2"] }),
    group({ id: "payee:Acme Co", payee: "Acme Co", kind: "Employer", category: "Salary", categories: ["Salary"], patterns: 0, pins: 2, rows: 2, rules: ["keys.csv:2", "keys.csv:3"] }),
  ],
  history: [{ id: 3, at: "2026-10-12T09:30:00", action: "edit", summary: "Edited merchant KOPITIAM* (Kopitiam) in merchants.csv" }],
  pending: 4,
  rules_error: null,
};
const PREVIEW: RulePreview = {
  error: null, file: "merchants.csv", override: null, matches: 6, wins: 6, already: 0, changes: 6, beaten_by: [],
  sample: [{ key: "k9", date: "2026-08-20", amount: -420, currency: "SGD", text: "KOPITIAM 0456 TPY",
    before: { payee: "Kopitiam", category: "Eating out" }, after: { payee: "Kopitiam", category: "Shopping" } }],
};

function stubFetch(state: RulesState = STATE, preview: RulePreview = PREVIEW) {
  const calls: { url: string; body?: string }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, body: init?.body as string | undefined });
    const body = url === "/api/rules" ? state
      : url.startsWith("/api/rules/preview") ? preview
      // G13: every write says how many rows it moved, which it did at once
      : url === "/api/rules/save" ? { file: "merchants.csv", line: 2, summary: "Edited", change: "s1", relinked: { changed: 6, written: 6 } }
      : url === "/api/rules/undo" ? { undone: STATE.history[0].summary, relinked: { changed: 6, written: 6 } }
      : url === "/api/rules/apply" ? { relinked: { changed: 4, written: 4 } }
      : { removed: true, change: "t1", relinked: { changed: 1, written: 1 } };
    return json(body);
  }));
  return calls;
}

// 60 invented payees with one pattern each, to page through
const MANY: RulesState = (() => {
  const rules = Array.from({ length: 60 }, (_, i) => rule({
    id: `merchants.csv:${i + 2}`, file: "merchants.csv", line: i + 2, order: i + 1, tier: "merchant",
    pattern: `SHOP${String(i + 1).padStart(2, "0")}*`, payee: `Shop ${String(i + 1).padStart(2, "0")}`, kind: "Merchant",
    category: "Shopping", matched: 1, wins: 1, pins: [],
  }));
  const groups = rules.map((r) => group({
    id: `payee:${r.payee}`, payee: r.payee, category: "Shopping", categories: ["Shopping"], rows: 1, rules: [r.id],
  }));
  return { ...STATE, rules, groups, pending: 0 };
})();
const leaveAsks = () => !window.dispatchEvent(new Event("beforeunload", { cancelable: true }));
const pages = () => screen.getByRole("navigation", { name: "Rules pages" });

const rowsOf = () => screen.getAllByRole("button", { name: /^Show the rules for / }).map((b) => b.getAttribute("aria-label"));

describe("Rules", () => {
  beforeEach(() => { window.history.replaceState(null, "", "/rules"); });
  afterEach(() => { window.history.replaceState(null, "", "/"); });

  it("shows one row per payee, and its pins when opened", async () => {
    stubFetch();
    render(<><Rules /><Toaster /></>);
    expect(await screen.findByText("4 to apply")).toBeInTheDocument();
    expect(rowsOf()).toEqual(["Show the rules for Grab", "Show the rules for Kopitiam", "Show the rules for Acme Co"]);
    expect(screen.getByText("2 single rows")).toBeInTheDocument();
    // once on the status line, once on the payee row
    expect(screen.getAllByText("1 problem")).toHaveLength(2);
    fireEvent.click(screen.getByRole("button", { name: "Show the rules for Acme Co" }));
    const list = screen.getByRole("list", { name: "Rules for Acme Co" });
    expect(within(list).getAllByRole("listitem")).toHaveLength(2);
    expect(within(list).getByText(/25 Aug 2026 · S\$2,500\.00/)).toBeInTheDocument();
    expect(within(list).getByText(/25 Jul 2026 · S\$2,500\.00/)).toBeInTheDocument();
  });

  it("leads a payee's kind and the problems chip with their icons, the words beside them (G18)", async () => {
    stubFetch();
    render(<Rules />);
    await screen.findByText("4 to apply");
    expect(leadingIcon(screen.getAllByText("Employer")[0])).toBe("briefcase-business");
    expect(leadingIcon(screen.getAllByText("Merchant")[0])).toBe("store");
    expect(leadingIcon(screen.getByRole("button", { name: "1 problem" }))).toBe("triangle-alert");
  });

  it("opens searched for the payee a link names", async () => {
    stubFetch();
    window.history.replaceState(null, "", "/rules?q=Kopitiam");
    try {
      render(<><Rules /><Toaster /></>);
      expect(await screen.findByRole("searchbox", { name: "Search rules" })).toHaveValue("Kopitiam");
      expect(rowsOf()).toEqual(["Show the rules for Kopitiam"]);
    } finally {
      window.history.replaceState(null, "", "/");
    }
  });

  it("searches payees, patterns and categories, and filters problems", async () => {
    stubFetch();
    render(<><Rules /><Toaster /></>);
    const search = await screen.findByRole("searchbox", { name: "Search rules" });
    fireEvent.change(search, { target: { value: "kopi" } });
    expect(rowsOf()).toEqual(["Show the rules for Kopitiam"]);
    fireEvent.change(search, { target: { value: "salary" } });
    expect(rowsOf()).toEqual(["Show the rules for Acme Co"]);
    fireEvent.change(search, { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "1 problem" }));
    expect(rowsOf()).toEqual(["Show the rules for Kopitiam"]);
    fireEvent.click(screen.getByRole("button", { name: "Show the rules for Kopitiam" }));
    expect(screen.getByText("This payee's rules name different countries.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("radio", { name: "All" }));
    fireEvent.click(screen.getByRole("radio", { name: "In order" }));
    const table = screen.getByRole("table");
    expect(within(table).getAllByRole("row")).toHaveLength(5);
    fireEvent.change(search, { target: { value: "grab" } });
    expect(within(screen.getByRole("table")).getAllByRole("row")).toHaveLength(2);
  });

  it("edits a rule with a live preview, then saves it", async () => {
    const calls = stubFetch();
    render(<><Rules /><Toaster /></>);
    fireEvent.click(await screen.findByRole("button", { name: "Show the rules for Kopitiam" }));
    fireEvent.click(screen.getByRole("button", { name: "Edit merchant KOPITIAM*" }));
    const box = screen.getByRole("combobox", { name: "Category for rule KOPITIAM*" });
    fireEvent.focus(box);
    fireEvent.mouseDown(within(screen.getByRole("listbox")).getByRole("option", { name: "Shopping" }));
    // the preview asks again once the typing pauses, with the edit named
    const asked = () => new URLSearchParams(calls.filter((c) => c.url.startsWith("/api/rules/preview")).at(-1)?.url.split("?")[1]);
    await waitFor(() => expect(asked().get("category")).toBe("Shopping"));
    expect([asked().get("edit_file"), asked().get("edit_line"), asked().get("edit_pattern")])
      .toEqual(["merchants.csv", "2", "KOPITIAM*"]);
    expect(await screen.findByText("Will change 6 rows")).toBeInTheDocument();
    expect(screen.getByText("KOPITIAM 0456 TPY")).toBeInTheDocument();
    expect(screen.getByText("Kopitiam, Shopping")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Save rule" }));
    // G13: the save reaches the transactions at once, and its Undo takes the rows back too
    const toast = await screen.findByRole("status");
    expect(toast).toHaveTextContent("Rule saved, 6 rows changed.");
    expect(JSON.parse(calls.find((c) => c.url === "/api/rules/save")!.body!)).toEqual({
      rule: { match_on: "merchant", pattern: "KOPITIAM*", payee: "Kopitiam", kind: "Merchant", country: "SGP",
        category: "Shopping", recurring: "", notes: "" },
      edit: { file: "merchants.csv", line: 2, pattern: "KOPITIAM*", revision: "r3" },
    });
    expect(calls.some((c) => c.url.startsWith("/api/run/load"))).toBe(false);
    fireEvent.click(within(toast).getByRole("button", { name: "Undo" }));
    expect(await screen.findByText("Undone: Edited merchant KOPITIAM* (Kopitiam) in merchants.csv, 6 rows changed back.")).toBeInTheDocument();
    expect(JSON.parse(calls.find((c) => c.url === "/api/rules/undo")!.body!)).toEqual({ change: "s1" });
  });

  it("applies rules only when the record holds rows differently from them", async () => {
    const calls = stubFetch();
    render(<><Rules /><Toaster /></>);
    fireEvent.click(await screen.findByRole("button", { name: "Apply rules" }));
    expect(await screen.findByText("Rules applied, 4 rows changed.")).toBeInTheDocument();
    expect(calls.some((c) => c.url === "/api/rules/apply")).toBe(true);
    expect(calls.some((c) => c.url.startsWith("/api/run/load"))).toBe(false);
  });

  it("titles Needs a rule with every group, not just the biggest the server sends (G18)", async () => {
    const needs = Array.from({ length: 60 }, (_, i) => ({
      text: `SHOP ${i}`, field: "merchant", rows: 1, amount: 900, first: "2026-08-01", last: "2026-08-01", keys: [],
      suggestion: { match_on: "merchant" as const, pattern: `SHOP ${i}*`, payee: `Shop ${i}`, kind: "Merchant" as const },
    }));
    stubFetch({ ...STATE, needs, needs_total: 75 });
    render(<Rules />);
    expect(await screen.findByRole("heading", { name: "Needs a rule (75)" })).toBeInTheDocument();
    expect(screen.getByText("The biggest 60 are listed.")).toBeInTheDocument();
    // G18 (U4): the groups page with the app's Pager, not a Show all of their own
    const needsPages = screen.getByRole("navigation", { name: "Needs a rule pages" });
    expect(within(needsPages).getByText("1 to 25 of 60")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Show (all|the biggest)/ })).not.toBeInTheDocument();
    expect(screen.getByText("SHOP 24")).toBeInTheDocument();
    expect(screen.queryByText("SHOP 25")).not.toBeInTheDocument();
    fireEvent.click(within(needsPages).getByRole("button", { name: "Page 2" }));
    expect(screen.getByText("SHOP 25")).toBeInTheDocument();
    expect(screen.queryByText("SHOP 24")).not.toBeInTheDocument();
    // a group's rows open to be set one at a time hold a draft, so the page stays until they close
    fireEvent.click(screen.getByRole("button", { name: "Set one row of SHOP 25" }));
    expect(within(needsPages).getByRole("button", { name: "Page 1" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Set one row of SHOP 25" }));
    expect(within(needsPages).getByRole("button", { name: "Page 1" })).toBeEnabled();
  });

  it("fills the new-rule form from each group named, one after another", async () => {
    const needs = ["TOAST BOX", "YA KUN"].map((text) => ({
      text, field: "merchant", rows: 2, amount: 900, first: "2026-08-01", last: "2026-08-09", keys: [],
      suggestion: { match_on: "merchant" as const, pattern: `${text}*`, payee: text, kind: "Merchant" as const },
    }));
    STATE.needs = needs;
    try {
      stubFetch();
      render(<><Rules /><Toaster /></>);
      const [first, second] = await screen.findAllByRole("button", { name: /^Make a rule/ });
      fireEvent.click(first);
      expect(screen.getByRole("textbox", { name: "Pattern for new rule" })).toHaveValue("TOAST BOX*");
      fireEvent.click(second);
      expect(screen.getByRole("textbox", { name: "Pattern for new rule" })).toHaveValue("YA KUN*");
      fireEvent.click(screen.getByRole("button", { name: "Add rule" }));
      expect(screen.getByRole("textbox", { name: "Pattern for new rule" })).toHaveValue("");
    } finally {
      STATE.needs = [];
    }
  });

  it("says a shared rule is saved as your own", async () => {
    stubFetch();
    render(<><Rules /><Toaster /></>);
    fireEvent.click(await screen.findByRole("button", { name: "Show the rules for Grab" }));
    expect(screen.queryByRole("button", { name: "Delete merchant GRAB*" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Edit merchant GRAB*" }));
    expect(screen.getByText("Shared rule, read only. Saving adds a rule to your merchants.csv that matches first, ahead of it.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save as my rule" })).toBeInTheDocument();
  });

  it("deletes a rule and undoes just that delete from the toast", async () => {
    const calls = stubFetch();
    render(<><Rules /><Toaster /></>);
    fireEvent.click(await screen.findByRole("button", { name: "Show the rules for Kopitiam" }));
    fireEvent.click(screen.getByRole("button", { name: "Delete merchant KOPITIAM*" }));
    const toast = await screen.findByRole("status");
    expect(toast).toHaveTextContent("Deleted KOPITIAM*, 1 row changed.");
    expect(JSON.parse(calls.find((c) => c.url === "/api/rules/remove")!.body!))
      .toEqual({ file: "merchants.csv", line: 2, pattern: "KOPITIAM*", revision: "r3" });
    fireEvent.click(within(toast).getByRole("button", { name: "Undo" }));
    await waitFor(() => expect(screen.getByText("Undone: Edited merchant KOPITIAM* (Kopitiam) in merchants.csv, 6 rows changed back.")).toBeInTheDocument());
    expect(JSON.parse(calls.find((c) => c.url === "/api/rules/undo")!.body!)).toEqual({ change: "t1" });
  });

  it("undoes the last change from the bar above the rules", async () => {
    const calls = stubFetch();
    render(<><Rules /><Toaster /></>);
    fireEvent.click(await screen.findByRole("button", { name: "Undo" }));
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Undone: Edited merchant KOPITIAM*"));
    expect(calls.find((c) => c.url === "/api/rules/undo")!.body).toBeUndefined();
  });

  it("pages the payees 25 at a time in place of Show more", async () => {
    stubFetch(MANY);
    render(<><Rules /><Toaster /></>);
    await screen.findByRole("button", { name: "Show the rules for Shop 01" });
    expect(rowsOf()).toHaveLength(25);
    expect(screen.queryByRole("button", { name: /Show more/ })).not.toBeInTheDocument();
    expect(within(pages()).getByText("1 to 25 of 60")).toBeInTheDocument();
    fireEvent.click(within(pages()).getByRole("button", { name: "Page 3" }));
    expect(within(pages()).getByText("51 to 60 of 60")).toBeInTheDocument();
    expect(rowsOf()[0]).toBe("Show the rules for Shop 51");
    expect(rowsOf()).toHaveLength(10);
  });

  it("pages the flat list of rules the same way", async () => {
    stubFetch(MANY);
    render(<><Rules /><Toaster /></>);
    fireEvent.click(await screen.findByRole("radio", { name: "In order" }));
    expect(within(screen.getByRole("table")).getAllByRole("row")).toHaveLength(26);
    fireEvent.click(within(pages()).getByRole("button", { name: "Next page" }));
    expect(within(pages()).getByText("26 to 50 of 60")).toBeInTheDocument();
    expect(screen.getByText("SHOP26*")).toBeInTheDocument();
    expect(screen.queryByText("SHOP25*")).not.toBeInTheDocument();
  });

  it("goes back to page 1 when the search changes, and remembers the page size", async () => {
    stubFetch(MANY);
    const { unmount } = render(<><Rules /><Toaster /></>);
    fireEvent.click(await screen.findByRole("button", { name: "Rows per page" }));
    fireEvent.mouseDown(screen.getByRole("option", { name: "10 per page" }));
    expect(rowsOf()).toHaveLength(10);
    fireEvent.click(within(pages()).getByRole("button", { name: "Page 3" }));
    expect(rowsOf()[0]).toBe("Show the rules for Shop 21");
    fireEvent.change(screen.getByRole("searchbox", { name: "Search rules" }), { target: { value: "1" } });
    // 01, 10 to 19, 21, 31, 41 and 51: fifteen payees
    expect(within(pages()).getByText("1 to 10 of 15")).toBeInTheDocument();
    expect(rowsOf()[0]).toBe("Show the rules for Shop 01");
    expect(window.localStorage.getItem("kopifund.pagesize.rules")).toBe("10");
    unmount();
    render(<><Rules /><Toaster /></>);
    await screen.findByRole("button", { name: "Show the rules for Shop 01" });
    expect(rowsOf()).toHaveLength(10);
  });

  it("shows no pager when everything fits", async () => {
    stubFetch();
    render(<><Rules /><Toaster /></>);
    await screen.findByRole("button", { name: "Show the rules for Grab" });
    expect(screen.queryByRole("navigation", { name: "Rules pages" })).not.toBeInTheDocument();
  });

  it("holds the page while an edit is open, so its unsaved changes are not dropped", async () => {
    stubFetch(MANY);
    render(<><Rules /><Toaster /></>);
    fireEvent.click(await screen.findByRole("button", { name: "Show the rules for Shop 01" }));
    fireEvent.click(screen.getByRole("button", { name: "Edit merchant SHOP01*" }));
    expect(within(pages()).getByRole("button", { name: "Next page" })).toBeDisabled();
    expect(within(pages()).getByRole("button", { name: "Page 2" })).toBeDisabled();
    expect(within(pages()).getByRole("button", { name: "Rows per page" })).toBeDisabled();
    // closing the edit frees the pager again
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(within(pages()).getByRole("button", { name: "Next page" })).toBeEnabled();
  });

  it("puts what is waiting and what is wrong on one status line, and the problems chip filters", async () => {
    stubFetch();
    render(<Rules />);
    expect(await screen.findByText("4 to apply")).toBeInTheDocument();
    expect(screen.queryByText("Your transactions match your rules.")).not.toBeInTheDocument();
    const chip = screen.getByRole("button", { name: "1 problem" });
    expect(chip).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(chip);
    expect(chip).toHaveAttribute("aria-pressed", "true");
    expect(rowsOf()).toEqual(["Show the rules for Kopitiam"]);
    // the same button turns the filter off again
    fireEvent.click(chip);
    expect(rowsOf()).toHaveLength(3);
  });

  it("says nothing is wrong and nothing is waiting when that is so", async () => {
    stubFetch({ ...STATE, pending: 0, rules: RULES.map((r) => ({ ...r, problems: [] })), groups: STATE.groups.map((g) => ({ ...g, problems: 0 })) });
    render(<Rules />);
    expect(await screen.findByText("No problems")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /problem/ })).not.toBeInTheDocument();
    // G13: saves apply at once, so with nothing waiting there is no chip and no Apply rules
    expect(screen.queryByText(/to apply/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Apply rules" })).not.toBeInTheDocument();
  });

  it("offers Apply rules, with no count, when only a payee's details or a rule's text wait", async () => {
    stubFetch({ ...STATE, pending: 0, stale: true });
    render(<Rules />);
    expect(await screen.findByRole("button", { name: "Apply rules" })).toBeInTheDocument();
    expect(screen.queryByText(/to apply/)).not.toBeInTheDocument();
  });

  it("counts payees and rules once, whichever view or filter is showing", async () => {
    stubFetch();
    render(<Rules />);
    expect(await screen.findByText("3 payees, 4 rules")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("radio", { name: "Single rows" }));
    expect(screen.getByText("3 payees, 4 rules · 1 payee shown")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("radio", { name: "In order" }));
    expect(screen.getByText("3 payees, 4 rules · 2 rules shown")).toBeInTheDocument();
  });

  it("uses plain words for the kinds of rule", async () => {
    stubFetch();
    render(<Rules />);
    await screen.findByText("4 to apply");
    expect(screen.getAllByText("1 text match")).toHaveLength(2);
    fireEvent.click(screen.getByRole("button", { name: "Show the rules for Acme Co" }));
    const list = screen.getByRole("list", { name: "Rules for Acme Co" });
    expect(within(list).getAllByText("Single row")).toHaveLength(2);
    fireEvent.click(screen.getByRole("button", { name: "Show the rules for Kopitiam" }));
    expect(within(screen.getByRole("list", { name: "Rules for Kopitiam" })).getByText("Merchant text")).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/\bpins?\b|\bwins\b/i);
  });

  it("names the buttons of a payee with no rule Set one row and Make a rule", async () => {
    STATE.needs = [{
      text: "TOAST BOX", field: "merchant", rows: 2, amount: 900, first: "2026-08-01", last: "2026-08-09", keys: [],
      suggestion: { match_on: "merchant", pattern: "TOAST BOX*", payee: "TOAST BOX", kind: "Merchant" },
    }];
    try {
      stubFetch();
      render(<Rules />);
      expect(await screen.findByRole("button", { name: "Set one row of TOAST BOX" })).toHaveTextContent("Set one row");
      expect(screen.getByRole("button", { name: "Make a rule for TOAST BOX" })).toHaveTextContent("Make a rule");
      expect(screen.queryByText("Name it")).not.toBeInTheDocument();
    } finally {
      STATE.needs = [];
    }
  });

  it("shows a single-row rule's date and bank text in the editor, never its key", async () => {
    stubFetch();
    render(<Rules />);
    fireEvent.click(await screen.findByRole("button", { name: "Show the rules for Acme Co" }));
    fireEvent.click(screen.getAllByRole("button", { name: "Edit key a1" })[0]);
    expect(screen.getByRole("textbox", { name: "Payee for rule a1" })).toHaveValue("Acme Co");
    expect(screen.queryByRole("textbox", { name: "Pattern for rule a1" })).not.toBeInTheDocument();
    expect(screen.queryByRole("combobox", { name: "Tier for rule a1" })).not.toBeInTheDocument();
    const field = screen.getByText("Single row", { selector: "span.text-\\[12px\\]" }).closest("label")!;
    expect(field).toHaveTextContent("25 Aug 2026 · S$2,500.00SALARY PAYMENT");
    expect(field).not.toHaveTextContent("a1");
  });

  it("offers every way to match except a single row when making a rule", async () => {
    stubFetch();
    render(<Rules />);
    fireEvent.click(await screen.findByRole("button", { name: "Add rule" }));
    fireEvent.click(screen.getByRole("button", { name: "Tier for new rule" }));
    expect(within(screen.getByRole("listbox", { name: "Tier for new rule" })).getAllByRole("option").map((o) => o.textContent))
      .toEqual(["Bank type", "Money in or out", "Merchant text", "Other party", "Memo", "Reference"]);
  });

  it("answers a rule that changes nothing with one No change chip", async () => {
    const calls = stubFetch(STATE, { ...PREVIEW, matches: 0, wins: 0, already: 0, changes: 0, sample: [] });
    render(<Rules />);
    fireEvent.click(await screen.findByRole("button", { name: "Show the rules for Kopitiam" }));
    fireEvent.click(screen.getByRole("button", { name: "Edit merchant KOPITIAM*" }));
    expect(await screen.findByText("No change")).toBeInTheDocument();
    expect(screen.getByText("No row has this text yet.")).toBeInTheDocument();
    expect(calls.some((c) => c.url.startsWith("/api/rules/preview"))).toBe(true);
    expect(screen.queryByText(/Matches \d+ rows?, wins/)).not.toBeInTheDocument();
  });

  it("keeps the search and the page in the address, so a reload or Back lands where you were", async () => {
    stubFetch(MANY);
    const { unmount } = render(<Rules />);
    const search = await screen.findByRole("searchbox", { name: "Search rules" });
    expect(window.location.search).toBe("");
    fireEvent.click(within(pages()).getByRole("button", { name: "Page 3" }));
    expect(new URLSearchParams(window.location.search).get("page")).toBe("3");
    // a new search starts at page 1, so the page leaves the address and the text joins it
    fireEvent.change(search, { target: { value: "shop 1" } });
    expect(new URLSearchParams(window.location.search).get("q")).toBe("shop 1");
    expect(new URLSearchParams(window.location.search).has("page")).toBe(false);
    fireEvent.change(search, { target: { value: "shop" } });
    fireEvent.click(within(pages()).getByRole("button", { name: "Page 2" }));
    expect(window.location.search).toBe("?q=shop&page=2");
    unmount();

    // a reload reads both back, and shows the page asked for once the rules have loaded
    render(<Rules />);
    expect(await screen.findByRole("searchbox", { name: "Search rules" })).toHaveValue("shop");
    await waitFor(() => expect(within(pages()).getByText("26 to 50 of 60")).toBeInTheDocument());
    expect(rowsOf()[0]).toBe("Show the rules for Shop 26");
    expect(window.location.search).toBe("?q=shop&page=2");
    // clearing the search takes it out of the address again
    fireEvent.change(screen.getByRole("searchbox", { name: "Search rules" }), { target: { value: "" } });
    expect(window.location.search).toBe("");
  });

  it("follows a link to the bare page while open, so the list, the box and the address agree", async () => {
    window.history.replaceState(null, "", "/rules?q=grab&category=Transport&view=order");
    stubFetch();
    render(<><Rules /><AddressProbe /></>);
    const search = await screen.findByRole("searchbox", { name: "Search rules" });
    expect(search).toHaveValue("grab");
    expect(screen.getByRole("button", { name: "Category" })).toHaveTextContent("Transport");
    // the sidebar's Rules link, or Needs your eye's, pushes the bare path under the open page
    act(() => navigate("/rules"));
    expect(search).toHaveValue("");
    expect(screen.getByRole("button", { name: "Category" })).toHaveTextContent("All categories");
    expect(screen.getByRole("radio", { name: "By payee" })).toBeChecked();
    expect(rowsOf()).toHaveLength(3);
    expect(window.location.search).toBe("");
    // and Back brings the filtered list back
    act(() => { window.history.back(); });
    await waitFor(() => expect(search).toHaveValue("grab"));
    expect(screen.getByRole("radio", { name: "In order" })).toBeChecked();
  });

  it("opens the page a link names when the link also changes the search", async () => {
    window.history.replaceState(null, "", "/rules?q=shop&page=2");
    stubFetch(MANY);
    render(<Rules />);
    await waitFor(() => expect(within(pages()).getByText("26 to 50 of 60")).toBeInTheDocument());
    // another search that also lists every Shop, on the same page number, from a link while the page is open
    act(() => navigate("/rules?q=s&page=2"));
    expect(screen.getByRole("searchbox", { name: "Search rules" })).toHaveValue("s");
    await act(async () => {});
    expect(within(pages()).getByText("26 to 50 of 60")).toBeInTheDocument();
    expect(window.location.search).toBe("?q=s&page=2");
    // a search typed here still starts at page 1
    fireEvent.change(screen.getByRole("searchbox", { name: "Search rules" }), { target: { value: "sh" } });
    expect(window.location.search).toBe("?q=sh");
    expect(within(pages()).getByText("1 to 25 of 60")).toBeInTheDocument();
  });

  it("stays on its page when the option already chosen is clicked again", async () => {
    stubFetch(MANY);
    render(<Rules />);
    await screen.findByRole("searchbox", { name: "Search rules" });
    fireEvent.click(within(pages()).getByRole("button", { name: "Page 2" }));
    expect(window.location.search).toBe("?page=2");
    for (const name of ["By payee", "All"]) fireEvent.click(screen.getByRole("radio", { name }));
    choose(screen.getByRole("button", { name: "Category" }), "All categories");
    expect(window.location.search).toBe("?page=2");
    expect(within(pages()).getByText("26 to 50 of 60")).toBeInTheDocument();
  });

  it("tells every other reader of the address when a filter changes", async () => {
    stubFetch();
    render(<><Rules /><AddressProbe /></>);
    fireEvent.change(await screen.findByRole("searchbox", { name: "Search rules" }), { target: { value: "kopi" } });
    expect(screen.getByTestId("address")).toHaveTextContent("q=kopi");
    fireEvent.click(screen.getByRole("radio", { name: "In order" }));
    expect(screen.getByTestId("address")).toHaveTextContent("q=kopi&view=order");
    choose(screen.getByRole("button", { name: "Category" }), "Eating out");
    expect(screen.getByTestId("address")).toHaveTextContent("q=kopi&view=order&category=Eating+out");
  });

  it("drops a category from the address that no rule has any more, rather than hide every rule", async () => {
    window.history.replaceState(null, "", "/rules?category=Gone&view=order");
    stubFetch();
    render(<Rules />);
    await screen.findByRole("radio", { name: "In order" });
    expect(screen.getByRole("button", { name: "Category" })).toHaveTextContent("All categories");
    await waitFor(() => expect(within(screen.getByRole("table")).getAllByRole("row")).toHaveLength(5));
    expect(window.location.search).toBe("?view=order");
  });

  it("keeps the view and the filters in the address with the page, so a reload shows the same list", async () => {
    stubFetch(MANY);
    const { unmount } = render(<Rules />);
    fireEvent.click(await screen.findByRole("radio", { name: "In order" }));
    fireEvent.click(screen.getByRole("radio", { name: "Single rows" }));
    expect(window.location.search).toBe("?view=order&show=pins");
    unmount();
    // none of MANY's rules is a single row, so the reload is empty; the two choices came back
    render(<Rules />);
    await screen.findByText("No rule matches");
    expect(screen.getByRole("radio", { name: "In order" })).toBeChecked();
    expect(screen.getByRole("radio", { name: "Single rows" })).toBeChecked();
    fireEvent.click(screen.getByRole("radio", { name: "All" }));
    choose(screen.getByRole("button", { name: "Category" }), "Shopping");
    fireEvent.click(within(pages()).getByRole("button", { name: "Page 2" }));
    expect(window.location.search).toBe("?view=order&category=Shopping&page=2");
    cleanup();
    render(<Rules />);
    await waitFor(() => expect(within(pages()).getByText("26 to 50 of 60")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: "Category" })).toHaveTextContent("Shopping");
  });

  it("keeps an unsaved edit when the window crosses 768px and the editor is drawn again", async () => {
    stubFetch();
    const screenWidth = stubMatchMedia(true);
    render(<Rules />);
    fireEvent.click(await screen.findByRole("button", { name: "Show the rules for Kopitiam" }));
    fireEvent.click(screen.getByRole("button", { name: "Edit merchant KOPITIAM*" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Payee for rule KOPITIAM*" }), { target: { value: "Kopi Corner" } });
    expect(screen.getByRole("table")).toBeInTheDocument();
    screenWidth.change(false);
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Payee for rule KOPITIAM*" })).toHaveValue("Kopi Corner");
  });

  it("shows a save that fails after the window crossed 768px in the editor now on screen", async () => {
    const screenWidth = stubMatchMedia(true);
    let refuse: () => void = () => {};
    const slow = new Promise<void>((r) => { refuse = r; });
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (url === "/api/rules") return json(STATE);
      if (url.startsWith("/api/rules/preview")) return json(PREVIEW);
      if (url === "/api/rules/save") { await slow; return json({ error: "Another tab changed that line." }, 409); }
      return new Response("{}", { status: 200 });
    }));
    render(<Rules />);
    fireEvent.click(await screen.findByRole("button", { name: "Show the rules for Kopitiam" }));
    fireEvent.click(screen.getByRole("button", { name: "Edit merchant KOPITIAM*" }));
    fireEvent.click(await screen.findByRole("button", { name: "Save rule" }));
    expect(await screen.findByRole("button", { name: "Saving…" })).toBeDisabled();
    screenWidth.change(false);
    // the editor drawn again still says it is saving, and then shows what went wrong
    expect(screen.getByRole("button", { name: "Saving…" })).toBeDisabled();
    refuse();
    expect(await screen.findByRole("alert")).toHaveTextContent(/Another tab changed that line|409|Request failed/);
    expect(screen.getByRole("button", { name: "Save rule" })).toBeEnabled();
  });

  it("asks before leaving a rule with typed changes, and not an editor opened and left as it was", async () => {
    stubFetch();
    render(<Rules />);
    fireEvent.click(await screen.findByRole("button", { name: "Add rule" }));
    const form = await screen.findByRole("textbox", { name: "Pattern for new rule" });
    expect(leaveAsks()).toBe(false);
    fireEvent.change(form, { target: { value: "KOPI*" } });
    await waitFor(() => expect(leaveAsks()).toBe(true));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(leaveAsks()).toBe(false);
    // a rule opened for editing is unsaved only once something in it changes
    fireEvent.click(screen.getByRole("button", { name: "Show the rules for Kopitiam" }));
    fireEvent.click(screen.getByRole("button", { name: "Edit merchant KOPITIAM*" }));
    const pattern = await screen.findByRole("textbox", { name: /^Pattern for / });
    expect(leaveAsks()).toBe(false);
    fireEvent.change(pattern, { target: { value: "KOPITIAM 0*" } });
    await waitFor(() => expect(leaveAsks()).toBe(true));
  });

  it("asks before leaving while a rule's save is on its way, though nothing in it changed", async () => {
    let refuse: () => void = () => {};
    const slow = new Promise<void>((r) => { refuse = r; });
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (url === "/api/rules") return json(STATE);
      if (url.startsWith("/api/rules/preview")) return json(PREVIEW);
      if (url === "/api/rules/save") { await slow; return json({ error: "Another tab changed that line." }, 409); }
      return new Response("{}", { status: 200 });
    }));
    render(<Rules />);
    fireEvent.click(await screen.findByRole("button", { name: "Show the rules for Kopitiam" }));
    fireEvent.click(screen.getByRole("button", { name: "Edit merchant KOPITIAM*" }));
    expect(leaveAsks()).toBe(false);
    fireEvent.click(await screen.findByRole("button", { name: "Save rule" }));
    await waitFor(() => expect(leaveAsks()).toBe(true));
    refuse();
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    await waitFor(() => expect(leaveAsks()).toBe(false));
  });

  it("does not let a save from a cancelled editor reach the next one opened", async () => {
    let refuse: () => void = () => {};
    const slow = new Promise<void>((r) => { refuse = r; });
    // settles once the page has read the refusal's body, the last step that waits on anything but the page itself
    let read: () => void = () => {};
    const answered = new Promise<void>((r) => { read = r; });
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (url === "/api/rules") return json(STATE);
      if (url.startsWith("/api/rules/preview")) return json(PREVIEW);
      if (url === "/api/rules/save") {
        await slow;
        const refusal = json({ error: "Another tab changed that line." }, 409);
        const text = refusal.text.bind(refusal);
        refusal.text = () => text().finally(read);
        return refusal;
      }
      return new Response("{}", { status: 200 });
    }));
    render(<Rules />);
    fireEvent.click(await screen.findByRole("button", { name: "Show the rules for Kopitiam" }));
    fireEvent.click(screen.getByRole("button", { name: "Edit merchant KOPITIAM*" }));
    fireEvent.click(await screen.findByRole("button", { name: "Save rule" }));
    fireEvent.click(await screen.findByRole("button", { name: "Cancel" }));
    // another rule is opened (the first editor of this session numbered 1 again, before the fix)
    fireEvent.click(screen.getByRole("button", { name: "Show the rules for Grab" }));
    fireEvent.click(screen.getByRole("button", { name: "Edit merchant GRAB*" }));
    expect(screen.getByRole("button", { name: "Save as my rule" })).toBeEnabled();
    refuse();
    // the refusal is read; what the page does with it is promises alone, all run before the next task: one
    // task later the late answer has landed wherever it was going to, on any machine, however slow
    await act(async () => { await answered; });
    await settle();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save as my rule" })).toBeEnabled();
  });

  it("offers only Set one row for a payee with no text to match", async () => {
    STATE.needs = [{
      text: "ADVICE 1", field: "key", rows: 1, amount: 900, first: "2026-08-01", last: "2026-08-01", keys: ["k1"],
      suggestion: { match_on: "key", pattern: "k1", payee: "", kind: "Merchant" },
    }];
    try {
      stubFetch();
      render(<Rules />);
      expect(await screen.findByRole("button", { name: "Set one row of ADVICE 1" })).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /^Make a rule/ })).not.toBeInTheDocument();
    } finally {
      STATE.needs = [];
    }
  });

  describe("on a phone", () => {
    beforeEach(() => {
      stubMatchMedia(false);
    });

    it("lists each payee as two lines in place of a table, with no column to clip", async () => {
      stubFetch();
      render(<Rules />);
      await screen.findByText("4 to apply");
      expect(screen.queryByRole("table")).not.toBeInTheDocument();
      expect(rowsOf()).toEqual(["Show the rules for Grab", "Show the rules for Kopitiam", "Show the rules for Acme Co"]);
      const acme = screen.getByRole("button", { name: "Show the rules for Acme Co" }).closest("li")!;
      expect(acme).toHaveTextContent("2 rows");
      expect(acme).toHaveTextContent("Salary · 2 single rows");
      fireEvent.click(within(acme).getByRole("button", { name: "Show the rules for Acme Co" }));
      expect(within(acme).getAllByRole("listitem")).toHaveLength(2);
    });

    it("lists the rules in order the same way", async () => {
      stubFetch();
      render(<Rules />);
      fireEvent.click(await screen.findByRole("radio", { name: "In order" }));
      expect(screen.queryByRole("table")).not.toBeInTheDocument();
      const item = screen.getByText("KOPITIAM*").closest("li")!;
      expect(item).toHaveTextContent("6 rows");
      expect(item).toHaveTextContent("Merchant text · Kopitiam, Eating out");
      expect(item).toHaveTextContent("merchants.csv");
    });
  });
});
