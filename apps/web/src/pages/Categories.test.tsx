import { act, cleanup, createEvent, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Categories } from "./Categories";
import { leadingIcon } from "../test-icons";
import { choose } from "../test-select";
import { Toaster } from "../components/Toast";
import type { CategoriesState, CategoryEntry, NeedClass } from "../api";
import { navigate } from "../router";
import { useUrlParams } from "../useUrlParams";
import { json } from "../test-helpers";

// another reader of the address, as the copilot panel is
function AddressProbe() {
  const [params] = useUrlParams();
  return <output data-testid="address">{params.toString()}</output>;
}

// G17: the ids the server sends with each category and bucket; every write names its row by one
const ID: Record<string, number> = {
  Groceries: 1, Coffee: 2, Shopping: 3, Salary: 4, Investments: 5, Pets: 6,
  Food: 11, Lifestyle: 12, Income: 13, Transfers: 14,
};
const PETS_BUCKET = 15;
const cat = (name: string, need_class: NeedClass | null, transactions = 0, monthly: number | null = 0): CategoryEntry =>
  ({ id: ID[name], name, need_class, cost_type: "Variable", group: "Variable", transactions, monthly, budgets: 1, rules: 3, personal_rules: 2 });

const STATE: CategoriesState = {
  months: 12,
  need_classes: { spend: ["Need", "Want"], income: ["Income"], transfer: ["Transfer", "Investment", "Savings"] },
  groups: [
    { id: ID.Food, name: "Food", kind: "spend", fixed: false, icon: "utensils", colour: null, shown: "c1", categories: [cat("Groceries", "Need", 345, 41200), cat("Coffee", "Want", 179, 5300)] },
    { id: ID.Lifestyle, name: "Lifestyle", kind: "spend", fixed: false, icon: "shopping-bag", colour: null, shown: "c2", categories: [cat("Shopping", "Want", 12, 9000)] },
    { id: ID.Income, name: "Income", kind: "income", fixed: true, icon: "wallet", colour: null, shown: "c3", categories: [cat("Salary", "Income", 13, 400000)] },
    { id: ID.Transfers, name: "Transfers", kind: "transfer", fixed: true, icon: "arrow-up-down", colour: null, shown: "c4", categories: [cat("Investments", "Investment", 16, 50000)] },
  ],
};

function stubFetch() {
  const posts: { url: string; body: Record<string, unknown> }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      posts.push({ url, body: JSON.parse(init.body as string) });
      return json({ ok: true });
    }
    return json(STATE);
  }));
  return posts;
}

describe("Categories", () => {
  beforeEach(() => {
    window.history.replaceState(null, "", "/categories");
  });

  it("shows one tab per bucket and only the selected bucket's categories", async () => {
    stubFetch();
    render(<><Categories /><Toaster /></>);
    const tabs = await screen.findAllByRole("tab");
    expect(tabs.map((t) => t.textContent)).toEqual(["Food2", "Lifestyle1", "Income1", "Transfers1"]);
    expect(screen.getByRole("tab", { name: "Food 2" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tabpanel")).toHaveAccessibleName("Food 2");
    expect(screen.getByText("Coffee")).toBeInTheDocument();
    expect(screen.queryByText("Shopping")).not.toBeInTheDocument();
    expect(screen.queryByText("Salary")).not.toBeInTheDocument();
  });

  it("switches buckets at once, and remembers the tab in the address and in this browser", async () => {
    stubFetch();
    const { unmount } = render(<><Categories /><Toaster /></>);
    fireEvent.click(await screen.findByRole("tab", { name: "Lifestyle 1" }));
    expect(screen.getByText("Shopping")).toBeInTheDocument();
    expect(screen.queryByText("Coffee")).not.toBeInTheDocument();
    expect(window.location.search).toBe("?bucket=Lifestyle");
    expect(window.localStorage.getItem("kopifund.categories.bucket")).toBe("Lifestyle");
    unmount();

    // the address wins over what this browser remembers, and a name no bucket has falls back to the first
    window.history.replaceState(null, "", "/categories?bucket=Income");
    const again = render(<><Categories /><Toaster /></>);
    expect(await screen.findByRole("tab", { name: "Income 1" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByText("Salary")).toBeInTheDocument();
    again.unmount();

    window.history.replaceState(null, "", "/categories?bucket=Nope");
    render(<><Categories /><Toaster /></>);
    expect(await screen.findByRole("tab", { name: "Food 2" })).toHaveAttribute("aria-selected", "true");

    // with no address, the remembered tab opens
    cleanup();
    window.history.replaceState(null, "", "/categories");
    render(<><Categories /><Toaster /></>);
    expect(await screen.findByRole("tab", { name: "Lifestyle 1" })).toHaveAttribute("aria-selected", "true");
  });

  it("follows the address while open: a link to another bucket opens its tab, and every reader of the address hears", async () => {
    stubFetch();
    render(<><Categories /><Toaster /><AddressProbe /></>);
    fireEvent.click(await screen.findByRole("tab", { name: "Lifestyle 1" }));
    expect(screen.getByTestId("address")).toHaveTextContent("bucket=Lifestyle");
    act(() => navigate("/categories?bucket=Income"));
    expect(screen.getByRole("tab", { name: "Income 1" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByText("Salary")).toBeInTheDocument();
    // Back returns to the tab the address had
    act(() => { window.history.back(); });
    await waitFor(() => expect(screen.getByRole("tab", { name: "Lifestyle 1" })).toHaveAttribute("aria-selected", "true"));
  });

  it("closes an add form when the address moves to another tab, so Back does not reopen it empty", async () => {
    stubFetch();
    render(<><Categories /><Toaster /></>);
    fireEvent.click(await screen.findByRole("button", { name: "Add category" }));
    fireEvent.change(screen.getByLabelText("New category in Food"), { target: { value: "Snacks" } });
    act(() => navigate("/categories?bucket=Income"));
    expect(screen.getByRole("tab", { name: "Income 1" })).toHaveAttribute("aria-selected", "true");
    act(() => { window.history.back(); });
    await waitFor(() => expect(screen.getByRole("tab", { name: "Food 2" })).toHaveAttribute("aria-selected", "true"));
    expect(screen.queryByLabelText(/New category in/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add category" })).toBeInTheDocument();
  });

  it("keeps working when this browser's storage is blocked", async () => {
    stubFetch();
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("blocked"); });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("blocked"); });
    render(<><Categories /><Toaster /></>);
    fireEvent.click(await screen.findByRole("tab", { name: "Lifestyle 1" }));
    expect(screen.getByText("Shopping")).toBeInTheDocument();
    expect(window.location.search).toBe("?bucket=Lifestyle");
  });

  it("stays on the tab after a category moves out, and the list loses it", async () => {
    // a server that applies the move, so the reloaded list shows it gone
    const state: CategoriesState = structuredClone(STATE);
    const posts: Record<string, unknown>[] = [];
    vi.stubGlobal("fetch", vi.fn(async (_url: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        const body = JSON.parse(init.body as string) as { id: number; group_id: number };
        posts.push(body);
        const from = state.groups.find((g) => g.categories.some((c) => c.id === body.id))!;
        const [c] = from.categories.splice(from.categories.findIndex((c) => c.id === body.id), 1);
        state.groups.find((g) => g.id === body.group_id)!.categories.push(c);
        return json({ ok: true });
      }
      return json(state);
    }));
    render(<><Categories /><Toaster /></>);
    choose(await screen.findByLabelText("Move Coffee to"), "Lifestyle");
    await waitFor(() => expect(screen.queryByText("Coffee")).not.toBeInTheDocument());
    expect(posts).toEqual([{ id: ID.Coffee, name: "Coffee", group_id: ID.Lifestyle, group: "Lifestyle" }]);
    expect(screen.getByRole("tab", { name: "Food 1" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "Lifestyle 2" })).toHaveAttribute("aria-selected", "false");
    expect(screen.getByText("Groceries")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "Lifestyle 2" }));
    expect(screen.getByText("Coffee")).toBeInTheDocument();
  });

  it("shows what each category touches", async () => {
    stubFetch();
    render(<><Categories /><Toaster /></>);
    const food = await screen.findByRole("tabpanel");
    expect(within(food).getByText("Coffee")).toBeInTheDocument();
    expect(within(food).getByText("179 transactions")).toBeInTheDocument();
    expect(within(food).getByText("S$53 a month")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Delete bucket Food" })).toBeInTheDocument();
    // Income and Transfers can be renamed but not deleted
    fireEvent.click(screen.getByRole("tab", { name: "Income 1" }));
    expect(screen.getByRole("button", { name: "Rename bucket Income" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Delete bucket Income" })).not.toBeInTheDocument();
    // an income category has no need class to choose
    expect(screen.queryByLabelText("Need class for Salary")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "Transfers 1" }));
    expect(screen.queryByRole("button", { name: "Delete bucket Transfers" })).not.toBeInTheDocument();
  });

  it("moves a category with the keyboard: Move to, then up and down", async () => {
    const posts = stubFetch();
    render(<><Categories /><Toaster /></>);
    choose(await screen.findByLabelText("Move Coffee to"), "Lifestyle");
    await waitFor(() => expect(posts.at(-1)).toEqual({ url: "/api/categories/move", body: { id: ID.Coffee, name: "Coffee", group_id: ID.Lifestyle, group: "Lifestyle" } }));
    // the arrows wait for the page to show the order the move left
    await waitFor(() => expect(screen.getByRole("button", { name: "Move Coffee up" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "Move Coffee up" }));
    await waitFor(() => expect(posts.at(-1)?.body).toEqual({ id: ID.Coffee, name: "Coffee", group_id: ID.Food, group: "Food", index: 0 }));
    expect(screen.getByRole("button", { name: "Move Groceries up" })).toBeDisabled();
  });

  it("moves a category by dropping it on a row, or on another bucket's tab", async () => {
    const posts = stubFetch();
    render(<><Categories /><Toaster /></>);
    const coffee = (await screen.findByText("Coffee")).closest("li")!;
    const groceries = screen.getByText("Groceries").closest("li")!;
    const data = new Map<string, string>();
    const dataTransfer = { setData: (k: string, v: string) => data.set(k, v), getData: (k: string) => data.get(k) ?? "", effectAllowed: "" };
    // onto a row of the same bucket: just before it
    fireEvent.dragStart(coffee, { dataTransfer });
    fireEvent(groceries, createEvent.drop(groceries, { dataTransfer }));
    await waitFor(() => expect(posts.at(-1)?.body).toEqual({ id: ID.Coffee, name: "Coffee", group_id: ID.Food, group: "Food", index: 0 }));
    await waitFor(() => expect(screen.getByText("Coffee").closest("li")).toHaveAttribute("draggable", "true"));
    // onto another bucket's tab: at the end of that bucket
    fireEvent.dragStart(screen.getByText("Coffee").closest("li")!, { dataTransfer });
    const tab = screen.getByRole("tab", { name: "Lifestyle 1" });
    fireEvent(tab, createEvent.drop(tab, { dataTransfer }));
    await waitFor(() => expect(posts.at(-1)?.body).toEqual({ id: ID.Coffee, name: "Coffee", group_id: ID.Lifestyle, group: "Lifestyle" }));
  });

  it("leads each need class option with its icon, the word beside it (G18)", async () => {
    stubFetch();
    render(<Categories />);
    const group = within(await screen.findByRole("radiogroup", { name: "Need class for Coffee" }));
    expect(leadingIcon(group.getByRole("radio", { name: "Need" }))).toBe("shopping-basket");
    expect(leadingIcon(group.getByRole("radio", { name: "Want" }))).toBe("heart");
  });

  it("sets a need class", async () => {
    const posts = stubFetch();
    render(<><Categories /><Toaster /></>);
    fireEvent.click(within(await screen.findByRole("radiogroup", { name: "Need class for Coffee" })).getByRole("radio", { name: "Need" }));
    await waitFor(() => expect(posts.at(-1)).toEqual({ url: "/api/categories", body: { id: ID.Coffee, name: "Coffee", need_class: "Need" } }));
  });

  it("deletes a category only once a category to move its rows to is chosen", async () => {
    const posts = stubFetch();
    render(<><Categories /><Toaster /></>);
    fireEvent.click(await screen.findByRole("button", { name: "Delete Coffee" }));
    expect(screen.getByText(/179 transactions, 1 budget and 2 of your rules move/)).toBeInTheDocument();
    const go = screen.getByRole("button", { name: "Delete and move" });
    expect(go).toBeDisabled();
    choose(screen.getByLabelText("Move Coffee's transactions to"), "Groceries");
    fireEvent.click(go);
    await waitFor(() => expect(posts.at(-1)).toEqual({ url: "/api/categories/delete", body: { id: ID.Coffee, name: "Coffee", move_to_id: ID.Groceries, move_to: "Groceries" } }));
  });

  it("deletes a category with nothing to move at once, without asking where to move it", async () => {
    const state: CategoriesState = structuredClone(STATE);
    state.groups[0].categories.push({ id: ID.Pets, name: "Pets", need_class: "Want", cost_type: "Variable", group: "Variable", transactions: 0, monthly: null, budgets: 0, rules: 0, personal_rules: 0 });
    const posts: { url: string; body: Record<string, unknown> }[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        posts.push({ url, body: JSON.parse(init.body as string) });
        return json({ ok: true });
      }
      return json(state);
    }));
    render(<><Categories /><Toaster /></>);
    fireEvent.click(await screen.findByRole("button", { name: "Delete Pets" }));
    expect(screen.queryByLabelText("Move Pets's transactions to")).not.toBeInTheDocument();
    expect(screen.queryByText("Move them to")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(posts.at(-1)).toEqual({ url: "/api/categories/delete", body: { id: ID.Pets, name: "Pets" } }));
    expect(await screen.findByText("Deleted Pets.")).toBeInTheDocument();
    // a category with a rule naming it still asks, even with no transactions
    fireEvent.click(await screen.findByRole("button", { name: "Delete Coffee" }));
    expect(screen.getByLabelText("Move Coffee's transactions to")).toBeInTheDocument();
  });

  it("asks where to move a category that has no transactions but a budget or a rule", async () => {
    const state: CategoriesState = structuredClone(STATE);
    state.groups[0].categories.push({ id: ID.Pets, name: "Pets", need_class: "Want", cost_type: "Variable", group: "Variable", transactions: 0, monthly: null, budgets: 1, rules: 0, personal_rules: 0 });
    vi.stubGlobal("fetch", vi.fn(async () => json(state)));
    render(<Categories />);
    fireEvent.click(await screen.findByRole("button", { name: "Delete Pets" }));
    expect(screen.getByLabelText("Move Pets's transactions to")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Delete and move" })).toBeDisabled();
  });

  it("rewrites ?bucket= to the tab shown when the bucket it names is gone", async () => {
    const state: CategoriesState = structuredClone(STATE);
    state.groups.splice(1, 0, { id: PETS_BUCKET, name: "Pets", kind: "spend", fixed: false, icon: "shapes", colour: null, shown: "c12", categories: [] });
    vi.stubGlobal("fetch", vi.fn(async (_url: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        state.groups = state.groups.filter((g) => g.name !== "Pets");
        return json({ ok: true });
      }
      return json(state);
    }));
    window.history.replaceState(null, "", "/categories?bucket=Pets");
    render(<Categories />);
    await screen.findByRole("tab", { name: "Pets 0" });
    expect(new URLSearchParams(window.location.search).get("bucket")).toBe("Pets");
    fireEvent.click(screen.getByRole("button", { name: "Delete bucket Pets" }));
    fireEvent.click(screen.getByRole("button", { name: "Delete Pets" }));
    await waitFor(() => expect(screen.queryByRole("tab", { name: "Pets 0" })).not.toBeInTheDocument());
    await waitFor(() => expect(new URLSearchParams(window.location.search).get("bucket")).toBe("Food"));
    expect(screen.getByRole("tab", { name: "Food 2" })).toHaveAttribute("aria-selected", "true");
  });

  it("rewrites a link to a bucket that no longer exists to the first tab", async () => {
    stubFetch();
    window.history.replaceState(null, "", "/categories?bucket=Gone");
    render(<Categories />);
    await screen.findByRole("tab", { name: "Food 2" });
    await waitFor(() => expect(new URLSearchParams(window.location.search).get("bucket")).toBe("Food"));
  });

  it("shows a bucket's total even when refunds take it below zero", async () => {
    const state: CategoriesState = structuredClone(STATE);
    state.groups[1].categories = [cat("Shopping", "Want", 12, -4000)];
    vi.stubGlobal("fetch", vi.fn(async () => json(state)));
    render(<Categories />);
    fireEvent.click(await screen.findByRole("tab", { name: "Lifestyle 1" }));
    expect(screen.getByText("1 category, −S$40 a month")).toBeInTheDocument();
    expect(screen.queryByText(/of spending/)).not.toBeInTheDocument();
  });

  it("renames, adds and deletes buckets and categories", async () => {
    const posts = stubFetch();
    render(<><Categories /><Toaster /></>);
    fireEvent.click(await screen.findByRole("button", { name: "Rename Coffee" }));
    const input = screen.getByLabelText("New name for Coffee");
    fireEvent.change(input, { target: { value: "Kopi" } });
    fireEvent.submit(input.closest("form")!);
    await waitFor(() => expect(posts.at(-1)).toEqual({ url: "/api/categories/rename", body: { id: ID.Coffee, name: "Coffee", new_name: "Kopi" } }));

    fireEvent.click(screen.getByRole("button", { name: "Add bucket" }));
    const bucket = screen.getByLabelText("New bucket name");
    fireEvent.change(bucket, { target: { value: "Pets" } });
    fireEvent.submit(bucket.closest("form")!);
    await waitFor(() => expect(posts.at(-1)).toEqual({ url: "/api/category-groups/add", body: { name: "Pets" } }));

    fireEvent.click(screen.getByRole("tab", { name: "Lifestyle 1" }));
    fireEvent.click(screen.getByRole("button", { name: "Delete bucket Lifestyle" }));
    choose(screen.getByLabelText("Move Lifestyle's categories to"), "Food");
    fireEvent.click(screen.getByRole("button", { name: "Delete Lifestyle" }));
    await waitFor(() => expect(posts.at(-1)).toEqual({ url: "/api/category-groups/delete", body: { id: ID.Lifestyle, name: "Lifestyle", move_to_id: ID.Food, move_to: "Food" } }));

    // the order controls wait for the page to show what the last write left
    fireEvent.click(screen.getByRole("tab", { name: "Food 2" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Move bucket Food down" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "Move bucket Food down" }));
    await waitFor(() => expect(posts.at(-1)).toEqual({
      url: "/api/category-groups/reorder", body: { ids: [ID.Lifestyle, ID.Food, ID.Income, ID.Transfers], names: ["Lifestyle", "Food", "Income", "Transfers"] } }));
  });

  it("says so when the change is saved but the rules could not follow", async () => {
    vi.stubGlobal("fetch", vi.fn(async (_url: string, init?: RequestInit) => init?.method === "POST"
      ? json({ name: "Kopi", rule_files: 0, rules_note: "your rules still say Coffee, which keeps working: disk full" })
      : json(STATE)));
    render(<><Categories /><Toaster /></>);
    fireEvent.click(await screen.findByRole("button", { name: "Rename Coffee" }));
    const input = screen.getByLabelText("New name for Coffee");
    fireEvent.change(input, { target: { value: "Kopi" } });
    fireEvent.submit(input.closest("form")!);
    expect(await screen.findByRole("alert")).toHaveTextContent("Saved. Your rules still say Coffee, which keeps working");
    expect(screen.queryByText(/now say Kopi/)).not.toBeInTheDocument();
  });

  it("sends a double-clicked rename once", async () => {
    const posts: string[] = [];
    let release: () => void = () => {};
    const slow = new Promise<void>((r) => { release = r; });
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        posts.push(url);
        await slow;
        return json({ name: "Kopi" });
      }
      return json(STATE);
    }));
    render(<><Categories /><Toaster /></>);
    fireEvent.click(await screen.findByRole("button", { name: "Rename Coffee" }));
    const input = screen.getByLabelText("New name for Coffee");
    fireEvent.change(input, { target: { value: "Kopi" } });
    fireEvent.submit(input.closest("form")!);
    fireEvent.submit(input.closest("form")!);
    await waitFor(() => expect(screen.getByRole("button", { name: "Rename" })).toBeDisabled());
    release();
    await waitFor(() => expect(posts).toEqual(["/api/categories/rename"]));
  });

  it("sends a second, different edit made while the first is on its way, after it", async () => {
    const posts: string[] = [];
    let release: () => void = () => {};
    const slow = new Promise<void>((r) => { release = r; });
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        posts.push(`${url} ${init.body}`);
        if (posts.length === 1) await slow;
        return json({ ok: true });
      }
      return json(STATE);
    }));
    render(<><Categories /><Toaster /></>);
    choose(await screen.findByLabelText("Move Coffee to"), "Lifestyle");
    fireEvent.click(within(screen.getByRole("radiogroup", { name: "Need class for Groceries" })).getByRole("radio", { name: "Want" }));
    await waitFor(() => expect(posts).toHaveLength(1));         // the second waits for the first
    release();
    await waitFor(() => expect(posts).toEqual([
      `/api/categories/move {"id":${ID.Coffee},"name":"Coffee","group_id":${ID.Lifestyle},"group":"Lifestyle"}`,
      `/api/categories {"id":${ID.Groceries},"name":"Groceries","need_class":"Want"}`,
    ]));
  });

  it("holds the order controls until the page shows the order the last move left", async () => {
    let release: () => void = () => {};
    const slow = new Promise<void>((r) => { release = r; });
    const posts: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        posts.push(url);
        await slow;
        return json({ ok: true });
      }
      return json(STATE);
    }));
    render(<><Categories /><Toaster /></>);
    fireEvent.click(await screen.findByRole("button", { name: "Move bucket Food down" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Move bucket Food down" })).toBeDisabled());
    expect(screen.getByRole("button", { name: "Move Coffee up" })).toBeDisabled();
    release();
    await waitFor(() => expect(screen.getByRole("button", { name: "Move bucket Food down" })).toBeEnabled());
    expect(posts).toEqual(["/api/category-groups/reorder"]);
  });

  it("sends the id and the name it shows, and says so when the server finds the row renamed meanwhile", async () => {
    const posts: { url: string; body: Record<string, unknown> }[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        posts.push({ url, body: JSON.parse(init.body as string) });
        return json({ error: "That category is called Kopi now, not Coffee: reload the page" }, 422);
      }
      return json(STATE);
    }));
    render(<><Categories /><Toaster /></>);
    fireEvent.click(within(await screen.findByRole("radiogroup", { name: "Need class for Coffee" })).getByRole("radio", { name: "Need" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("called Kopi now, not Coffee");
    expect(posts).toEqual([{ url: "/api/categories", body: { id: ID.Coffee, name: "Coffee", need_class: "Need" } }]);
  });

  it("shows why a change was refused", async () => {
    vi.stubGlobal("fetch", vi.fn(async (_url: string, init?: RequestInit) => init?.method === "POST"
      ? json({ error: "there is already a category called 'Groceries'" }, 422)
      : json(STATE)));
    render(<><Categories /><Toaster /></>);
    fireEvent.click(await screen.findByRole("button", { name: "Rename Coffee" }));
    const input = screen.getByLabelText("New name for Coffee");
    fireEvent.change(input, { target: { value: "Groceries" } });
    fireEvent.submit(input.closest("form")!);
    expect(await screen.findByRole("alert")).toHaveTextContent("already a category called");
  });
  it("reorders the tabs when a bucket moves, and stays on that bucket", async () => {
    const state: CategoriesState = structuredClone(STATE);
    vi.stubGlobal("fetch", vi.fn(async (_url: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        const { ids } = JSON.parse(init.body as string) as { ids: number[] };
        state.groups.sort((a, b) => ids.indexOf(a.id) - ids.indexOf(b.id));
        return json({ ok: true });
      }
      return json(state);
    }));
    render(<><Categories /><Toaster /></>);
    fireEvent.click(await screen.findByRole("tab", { name: "Lifestyle 1" }));
    fireEvent.click(screen.getByRole("button", { name: "Move bucket Lifestyle up" }));
    await waitFor(() => expect(screen.getAllByRole("tab").map((t) => t.textContent)).toEqual(["Lifestyle1", "Food2", "Income1", "Transfers1"]));
    expect(screen.getByRole("tab", { name: "Lifestyle 1" })).toHaveAttribute("aria-selected", "true");
  });

  it("follows a renamed bucket, and lands on the bucket a deleted one's categories went to", async () => {
    const state: CategoriesState = structuredClone(STATE);
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        const body = JSON.parse(init.body as string) as { id: number; new_name?: string; move_to_id?: number };
        if (url.endsWith("/rename")) state.groups.find((g) => g.id === body.id)!.name = body.new_name!;
        else {
          const gone = state.groups.findIndex((g) => g.id === body.id);
          state.groups.find((g) => g.id === body.move_to_id)!.categories.push(...state.groups[gone].categories);
          state.groups.splice(gone, 1);
        }
        return json({ ok: true });
      }
      return json(state);
    }));
    render(<><Categories /><Toaster /></>);
    fireEvent.click(await screen.findByRole("tab", { name: "Lifestyle 1" }));
    fireEvent.click(screen.getByRole("button", { name: "Rename bucket Lifestyle" }));
    const input = screen.getByLabelText("New name for Lifestyle");
    fireEvent.change(input, { target: { value: "Fun" } });
    fireEvent.submit(input.closest("form")!);
    expect(await screen.findByRole("tab", { name: "Fun 1" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByText("Shopping")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Delete bucket Fun" }));
    choose(screen.getByLabelText("Move Fun's categories to"), "Food");
    fireEvent.click(screen.getByRole("button", { name: "Delete Fun" }));
    expect(await screen.findByRole("tab", { name: "Food 3" })).toHaveAttribute("aria-selected", "true");
    expect(screen.queryByRole("tab", { name: /Fun/ })).not.toBeInTheDocument();
  });

  it("drops an unfinished new-category draft when the tab changes, so it cannot land in another bucket", async () => {
    const posts = stubFetch();
    render(<><Categories /><Toaster /></>);
    fireEvent.click(await screen.findByRole("button", { name: "Add category" }));
    fireEvent.change(screen.getByLabelText("New category in Food"), { target: { value: "Snacks" } });
    fireEvent.click(screen.getByRole("tab", { name: "Income 1" }));
    expect(screen.queryByLabelText(/New category in/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add category" })).toBeInTheDocument();
    // back on Food the draft is gone too; a fresh one goes to Food
    fireEvent.click(screen.getByRole("tab", { name: "Food 2" }));
    fireEvent.click(screen.getByRole("button", { name: "Add category" }));
    const input = screen.getByLabelText("New category in Food");
    expect(input).toHaveValue("");
    fireEvent.change(input, { target: { value: "Tea" } });
    fireEvent.submit(input.closest("form")!);
    await waitFor(() => expect(posts.at(-1)).toEqual({ url: "/api/categories/add", body: { name: "Tea", group_id: ID.Food, group: "Food" } }));
    expect(posts).toHaveLength(1);
  });

  it("opens the tab of the name the server saved, which collapses repeated spaces", async () => {
    const state: CategoriesState = structuredClone(STATE);
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        const body = JSON.parse(init.body as string) as { name: string; new_name?: string };
        const saved = (body.new_name ?? body.name).split(/\s+/).join(" ");
        if (url.endsWith("/add")) state.groups.splice(2, 0, { id: PETS_BUCKET, name: saved, kind: "spend", fixed: false, icon: "shapes", colour: null, shown: "c12", categories: [] });
        else state.groups.find((g) => g.name === body.name)!.name = saved;
        return json({ name: saved });
      }
      return json(state);
    }));
    render(<><Categories /><Toaster /></>);
    fireEvent.click(await screen.findByRole("button", { name: "Add bucket" }));
    const bucket = screen.getByLabelText("New bucket name");
    fireEvent.change(bucket, { target: { value: "Holiday  Fund" } });
    fireEvent.submit(bucket.closest("form")!);
    expect(await screen.findByRole("tab", { name: "Holiday Fund 0" })).toHaveAttribute("aria-selected", "true");
    expect(new URLSearchParams(window.location.search).get("bucket")).toBe("Holiday Fund");
    expect(window.localStorage.getItem("kopifund.categories.bucket")).toBe("Holiday Fund");

    // a rename the same way
    await waitFor(() => expect(screen.getByRole("button", { name: "Rename bucket Holiday Fund" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "Rename bucket Holiday Fund" }));
    const input = screen.getByLabelText("New name for Holiday Fund");
    fireEvent.change(input, { target: { value: "Trips   and  fun" } });
    fireEvent.submit(input.closest("form")!);
    expect(await screen.findByRole("tab", { name: "Trips and fun 0" })).toHaveAttribute("aria-selected", "true");
    expect(window.localStorage.getItem("kopifund.categories.bucket")).toBe("Trips and fun");
  });

  it("opens a new bucket's tab after adding it", async () => {
    const state: CategoriesState = structuredClone(STATE);
    vi.stubGlobal("fetch", vi.fn(async (_url: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        state.groups.splice(2, 0, { id: PETS_BUCKET, name: "Pets", kind: "spend", fixed: false, icon: "shapes", colour: null, shown: "c12", categories: [] });
        return json({ ok: true });
      }
      return json(state);
    }));
    render(<><Categories /><Toaster /></>);
    fireEvent.click(await screen.findByRole("button", { name: "Add bucket" }));
    const bucket = screen.getByLabelText("New bucket name");
    fireEvent.change(bucket, { target: { value: "Pets" } });
    fireEvent.submit(bucket.closest("form")!);
    expect(await screen.findByRole("tab", { name: "Pets 0" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByText(/No categories yet/)).toBeInTheDocument();
  });

  it("shows Need and Want side by side, the chosen one marked, and a plain label where there is nothing to choose", async () => {
    stubFetch();
    render(<Categories />);
    const coffee = await screen.findByRole("radiogroup", { name: "Need class for Coffee" });
    expect(within(coffee).getAllByRole("radio").map((r) => r.textContent)).toEqual(["Need", "Want"]);
    expect(within(coffee).getByRole("radio", { name: "Want" })).toBeChecked();
    expect(within(coffee).getByRole("radio", { name: "Need" })).not.toBeChecked();
    expect(screen.queryByLabelText("Need class for Coffee", { selector: "select" })).not.toBeInTheDocument();
    // income has one class, so it is a label, not a control
    fireEvent.click(screen.getByRole("tab", { name: "Income 1" }));
    expect(screen.queryByRole("radiogroup", { name: /Need class/ })).not.toBeInTheDocument();
    // (the bucket's own name is in the Move to box, a button; the class label is outside it)
    expect(within(screen.getByRole("tabpanel")).getAllByText("Income", { selector: "span" }).some((s) => !s.closest("button"))).toBe(true);
  });

  it("summarises each bucket: its categories, its monthly total, and its share of spending", async () => {
    stubFetch();
    render(<Categories />);
    const food = await screen.findByRole("tabpanel");
    expect(within(food).getByText("2 categories, S$465 a month")).toBeInTheDocument();
    expect(within(food).getByText("84% of spending")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "Lifestyle 1" }));
    expect(screen.getByText("1 category, S$90 a month")).toBeInTheDocument();
    expect(screen.getByText("16% of spending")).toBeInTheDocument();
    // income is not spending, so it has a total and no share
    fireEvent.click(screen.getByRole("tab", { name: "Income 1" }));
    expect(screen.getByText("1 category, S$4,000 a month")).toBeInTheDocument();
    expect(screen.queryByText(/of spending/)).not.toBeInTheDocument();
  });

  it("has no intro line, with Add bucket in the page header", async () => {
    stubFetch();
    render(<Categories />);
    expect(await screen.findByRole("button", { name: "Add bucket" })).toBeInTheDocument();
    expect(screen.queryByText(/A change here moves/)).not.toBeInTheDocument();
  });

  it("offers a neutral empty state, not a problem, for an empty bucket", async () => {
    const state: CategoriesState = structuredClone(STATE);
    state.groups.splice(1, 0, { id: PETS_BUCKET, name: "Pets", kind: "spend", fixed: false, icon: "shapes", colour: null, shown: "c12", categories: [] });
    vi.stubGlobal("fetch", vi.fn(async () => json(state)));
    window.history.replaceState(null, "", "/categories?bucket=Pets");
    render(<Categories />);
    expect(await screen.findByText("No categories yet")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    // its action opens the form for that bucket
    fireEvent.click(screen.getByRole("button", { name: "Add category" }));
    expect(screen.getByLabelText("New category in Pets")).toBeInTheDocument();
  });

  it("offers a neutral empty state with an Add a bucket action when there are no buckets at all", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json({ ...STATE, groups: [] })));
    render(<Categories />);
    expect(await screen.findByText("No categories yet")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Add a bucket" }));
    expect(screen.getByLabelText("New bucket name")).toBeInTheDocument();
  });

  it("leads each tab with its bucket's icon in the colour it is drawn in (G18)", async () => {
    stubFetch();
    render(<Categories />);
    const food = await screen.findByRole("tab", { name: "Food 2" });
    const icon = food.querySelector("[data-bucket-icon]");
    expect(icon?.getAttribute("class")).toContain("lucide-utensils");
    expect((icon as SVGElement).style.color).toBe("var(--c1)");
    const income = screen.getByRole("tab", { name: "Income 1" }).querySelector("[data-bucket-icon]") as SVGElement;
    expect(income.style.color).toBe("var(--c3)");
  });

  it("picks a bucket's icon and colour, saved at once, and says which bucket a taken colour swaps with (G18)", async () => {
    const posts: { url: string; body: Record<string, unknown> }[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        const body = JSON.parse(init.body as string);
        posts.push({ url, body });
        const swapped = body.colour === "c2" ? "Lifestyle" : null;
        return json({ id: ID.Food, name: "Food", icon: "utensils", colour: body.colour ?? null, shown: "c1", swapped });
      }
      return json(STATE);
    }));
    render(<><Categories /><Toaster /></>);
    fireEvent.click(await screen.findByRole("button", { name: "Icon and colour for Food" }));
    const box = screen.getByRole("dialog", { name: "Icon and colour for Food" });
    const colours = within(box).getByRole("radiogroup", { name: "Colour for Food" });
    expect(within(colours).getAllByRole("radio")).toHaveLength(12);
    expect(within(colours).getByRole("radio", { name: "Colour 1" })).toHaveAttribute("aria-checked", "true");
    // a colour another bucket is drawn in names that bucket
    const taken = within(colours).getByRole("radio", { name: "Colour 2, Lifestyle's" });
    expect(taken).toHaveAttribute("title", "Lifestyle's colour: picking it swaps the two");
    fireEvent.click(taken);
    expect(await screen.findByText("Swapped colours with Lifestyle.")).toBeInTheDocument();
    // the box stays open for the icon
    const icons = within(screen.getByRole("dialog")).getByRole("radiogroup", { name: "Icon for Food" });
    expect(within(icons).getAllByRole("radio")).toHaveLength(24);
    expect(within(icons).getByRole("radio", { name: "utensils" })).toHaveAttribute("aria-checked", "true");
    fireEvent.click(within(icons).getByRole("radio", { name: "coffee" }));
    await waitFor(() => expect(posts).toHaveLength(2));
    expect(posts).toEqual([
      { url: "/api/category-groups/style", body: { id: ID.Food, name: "Food", colour: "c2" } },
      { url: "/api/category-groups/style", body: { id: ID.Food, name: "Food", icon: "coffee" } },
    ]);
    // picking what it already has sends nothing
    await waitFor(() => expect(within(colours).getByRole("radio", { name: "Colour 1" })).toHaveAttribute("aria-checked", "true"));
    fireEvent.click(within(colours).getByRole("radio", { name: "Colour 1" }));
    expect(posts).toHaveLength(2);
  });

  it("keeps the last pick made while an earlier one is still saving (G18)", async () => {
    const posts: Record<string, unknown>[] = [];
    let release = () => {};
    vi.stubGlobal("fetch", vi.fn(async (_url: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        const body = JSON.parse(init.body as string);
        posts.push(body);
        if (posts.length === 1) await new Promise<void>((r) => { release = r; });
        return json({ id: ID.Food, name: "Food", icon: "utensils", colour: body.colour, shown: body.colour, swapped: null });
      }
      return json(STATE);
    }));
    render(<><Categories /><Toaster /></>);
    fireEvent.click(await screen.findByRole("button", { name: "Icon and colour for Food" }));
    const colours = screen.getByRole("radiogroup", { name: "Colour for Food" });
    fireEvent.click(within(colours).getByRole("radio", { name: "Colour 5" }));
    expect(within(colours).getByRole("radio", { name: "Colour 5" })).toHaveAttribute("aria-checked", "true");
    // back to the colour it had, before the first pick is saved: that is sent too, after it
    fireEvent.click(within(colours).getByRole("radio", { name: "Colour 1" }));
    expect(within(colours).getByRole("radio", { name: "Colour 1" })).toHaveAttribute("aria-checked", "true");
    await waitFor(() => expect(posts).toHaveLength(1));
    release();
    await waitFor(() => expect(posts.map((p) => p.colour)).toEqual(["c5", "c1"]));
  });

  it("never shows one bucket's unsaved pick on another bucket's tab (G18)", async () => {
    let release = () => {};
    vi.stubGlobal("fetch", vi.fn(async (_url: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        await new Promise<void>((r) => { release = r; });
        return json({ id: ID.Food, name: "Food", icon: "coffee", colour: null, shown: "c1", swapped: null });
      }
      return json(STATE);
    }));
    render(<><Categories /><Toaster /></>);
    fireEvent.click(await screen.findByRole("button", { name: "Icon and colour for Food" }));
    fireEvent.click(within(screen.getByRole("radiogroup", { name: "Icon for Food" })).getByRole("radio", { name: "coffee" }));
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    fireEvent.click(screen.getByRole("tab", { name: "Income 1" }));
    fireEvent.click(screen.getByRole("button", { name: "Icon and colour for Income" }));
    const icons = screen.getByRole("radiogroup", { name: "Icon for Income" });
    expect(within(icons).getByRole("radio", { name: "wallet" })).toHaveAttribute("aria-checked", "true");
    expect(within(icons).getByRole("radio", { name: "coffee" })).toHaveAttribute("aria-checked", "false");
    release();
  });
});
