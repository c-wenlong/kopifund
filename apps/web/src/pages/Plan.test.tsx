import { act, render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { revalidate } from "../api";
import { describe, expect, it, vi } from "vitest";
import { Plan, fractionToPercent, percentToFraction } from "./Plan";
import { Toaster } from "../components/Toast";
import { expectAsks, expectFree, json, leaveAsks } from "../test-helpers";

// A plan with every list filled, its money in cents as GET /api/plan sends it
// (G17). Every figure and name here is invented.
const LONG_NOTE = "Deposit for the invented course, paid in two parts";
const fullPlan = {
  to: "2027-06", dca_minor: 30_000, insurance_minor: 4_000, other_income_minor: 0, repayments_minor: 0, cpf_employer_rate: 0.17,
  spend_rate: "home",
  income: [{ from: "2026-10-01", to: "2027-06-30", gross_minor: 400_000, cpf: true, note: "Invented job" }],
  trips: [{ name: "Invented trip", from: "2026-12-10", to: "2026-12-16", flights_minor: 45_000 }],
  one_offs: [{ month: "2027-01", amount_minor: 120_000, label: LONG_NOTE }],
};

/** Serve the Plan page; returns every plan the page posts. */
function servePlan(plan: Record<string, unknown>) {
  const posted: Record<string, unknown>[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      const body = JSON.parse(init.body as string);
      posted.push(body);
      return json(body);
    }
    if (url.startsWith("/api/plan")) {
      return json({ plan, categories: [], need_classes: [], spend_rates: ["window", "mean", "home", "phase"] });
    }
    if (url.startsWith("/api/budgets")) return json({ month: "2026-08", rows: [], months: [] });
    return json({ error: "no result" }, 404);
  }));
  return posted;
}

describe("Plan", () => {
  it("keeps the saved plan on screen, not the one from before the save", async () => {
    let saved = { insurance_minor: 10_000 };
    let release: () => void = () => {};
    const slowReload = new Promise<void>((r) => { release = r; });
    let planGets = 0;
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        saved = JSON.parse(init.body as string);
        return json(saved);
      }
      if (url.startsWith("/api/plan")) {
        planGets += 1;
        if (planGets > 1) await slowReload;          // the refresh after the save is slow
        return json({ plan: saved, categories: [], need_classes: [], spend_rates: ["home"] });
      }
      if (url.startsWith("/api/budgets")) return json({ month: "", rows: [], months: [] });
      return json({ results: [] });
    }));
    render(<Plan />);
    const insurance = await screen.findByDisplayValue("100");
    fireEvent.change(insurance, { target: { value: "250" } });
    fireEvent.click(screen.getByRole("button", { name: "Save plan" }));
    await waitFor(() => expect(saved).toMatchObject({ insurance_minor: 25_000 }));
    expect(screen.getByDisplayValue("250")).toBeInTheDocument();
    release();
    await waitFor(() => expect(screen.getByDisplayValue("250")).toBeInTheDocument());
  });

  it("opens the budgets on the record's last month, and the picker still moves it", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-10-11T04:00:00Z"));
    const asked: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (url.startsWith("/api/budgets")) {
        const month = new URL(url, "http://x").searchParams.get("month") ?? "";
        asked.push(month);
        return json({
          month, months: ["2026-08", "2026-07"], need_classes: ["Need"],
          rows: [{ category: "Groceries", need_class: "Need", bucket: "Spend", planned: 40000, actual: month === "2026-08" ? 31200 : 0 }],
        });
      }
      if (url.startsWith("/api/plan")) return json({ plan: {}, categories: [], need_classes: [], spend_rates: ["home"] });
      return json({ results: [] });
    }));
    try {
      render(<Plan asOf="2026-08-28" />);
      expect(await screen.findByLabelText("Planned for Groceries")).toHaveValue("400");
      expect(screen.getAllByText("S$312").length).toBeGreaterThan(0);
      expect(screen.getByLabelText("Month")).toHaveValue("Aug 2026");
      expect(asked).toEqual(["2026-08"]);
      fireEvent.change(screen.getByLabelText("Month"), { target: { value: "sep 2026" } });
      await waitFor(() => expect(asked).toContain("2026-09"));
      fireEvent.blur(screen.getByLabelText("Month"));
      expect(screen.getByLabelText("Month")).toHaveValue("Sep 2026");
      // the arrows beside it step one month
      fireEvent.click(screen.getByRole("button", { name: "Next month" }));
      await waitFor(() => expect(asked).toContain("2026-10"));
      fireEvent.click(screen.getByRole("button", { name: "Previous month" }));
      fireEvent.click(screen.getByRole("button", { name: "Previous month" }));
      await waitFor(() => expect(screen.getByLabelText("Month")).toHaveValue("Aug 2026"));
    } finally {
      vi.useRealTimers();
    }
  });

  it("groups the budgets by bucket with a total row each, income on its own", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (url.startsWith("/api/budgets")) {
        return json({
          month: "2026-08", months: ["2026-08"],
          groups: [{ name: "Food", kind: "spend" }, { name: "Getting around", kind: "spend" }, { name: "Income", kind: "income" }],
          rows: [
            { category: "Groceries", need_class: "Need", bucket: "Spend", category_group: "Food", kind: "spend", planned: 40000, actual: 31200 },
            { category: "Coffee", need_class: "Want", bucket: "Spend", category_group: "Food", kind: "spend", planned: 5000, actual: 6000 },
            { category: "Transport", need_class: "Need", bucket: "Spend", category_group: "Getting around", kind: "spend", planned: null, actual: 9000 },
            { category: "Salary", need_class: "Income", bucket: "Income", category_group: "Income", kind: "income", planned: 400000, actual: 410000 },
          ],
        });
      }
      if (url.startsWith("/api/plan")) return json({ plan: {}, categories: [], need_classes: [], spend_rates: ["home"] });
      return json({ results: [] });
    }));
    render(<Plan asOf="2026-08-28" />);
    const food = await screen.findByRole("row", { name: "Food total" });
    expect(food).toHaveTextContent("FoodS$450S$372");
    expect(screen.getByRole("row", { name: "Getting around total" })).toHaveTextContent("Getting aroundS$90");
    const tables = screen.getAllByRole("table");
    expect(tables).toHaveLength(2);
    expect(tables[1]).toHaveTextContent("Income");
    expect(tables[1]).toHaveTextContent("Salary");
    expect(tables[0]).not.toHaveTextContent("Salary");
    // need classes are set on Categories now, and the page does not say so
    expect(screen.queryByLabelText("Need class for Coffee")).not.toBeInTheDocument();
    expect(screen.queryByText(/Buckets and need classes/)).not.toBeInTheDocument();
  });

  it("waits for the record's last month before asking for any budgets", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-10-11T04:00:00Z"));
    const asked: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (url.startsWith("/api/budgets")) {
        const month = new URL(url, "http://x").searchParams.get("month") ?? "";
        asked.push(month);
        return json({ month, months: [], rows: [] });
      }
      if (url.startsWith("/api/plan")) return json({ plan: {}, categories: [], need_classes: [], spend_rates: ["home"] });
      return json({ results: [] });
    }));
    try {
      const { rerender } = render(<Plan asOf={undefined} />);
      await screen.findByText("Save plan");
      expect(asked).toEqual([]);
      rerender(<Plan asOf="2026-08-28" />);
      await waitFor(() => expect(asked).toEqual(["2026-08"]));
    } finally {
      vi.useRealTimers();
    }
  });

  it("opens on today's month when the record is empty", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-10-11T04:00:00Z"));
    const asked: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (url.startsWith("/api/budgets")) {
        const month = new URL(url, "http://x").searchParams.get("month") ?? "";
        asked.push(month);
        return json({ month, months: [], rows: [] });
      }
      if (url.startsWith("/api/plan")) return json({ plan: {}, categories: [], need_classes: [], spend_rates: ["home"] });
      return json({ results: [] });
    }));
    try {
      render(<Plan asOf={null} />);
      await waitFor(() => expect(asked).toEqual(["2026-10"]));
    } finally {
      vi.useRealTimers();
    }
  });

  it("labels every plan field with its unit, and names the spending rates in words", async () => {
    servePlan(fullPlan);
    render(<Plan asOf="2026-08-28" />);
    expect(await screen.findByLabelText("Insurance premiums (S$ a month)")).toHaveValue("40");
    expect(screen.queryByLabelText("Investing (S$ a month)")).not.toBeInTheDocument();
    for (const name of ["Project until (month)", "Insurance premiums (S$ a month)", "Other income (S$ a month)",
      "Repayments (S$ a month)", "Employer CPF (%)", "Spending rate"]) {
      expect(screen.getByLabelText(name)).toBeInTheDocument();
    }
    // the visible label is tied to its input too
    expect(screen.getByText("Insurance premiums", { selector: "label" })).toHaveAttribute("for", screen.getByLabelText("Insurance premiums (S$ a month)").id);
    const rate = screen.getByLabelText("Spending rate");
    expect(rate).toHaveTextContent("A month at home (trips taken out)");
    // what each one is rides in its option, so no grey line sits under the select
    fireEvent.click(rate);
    expect(screen.getAllByRole("option").map((o) => o.textContent)).toEqual([
      "A typical month (middle of the last 6)", "An average month (the last 6 averaged)", "A month at home (trips taken out)",
      "The current phase's average (since it began)",
    ]);
    for (const gone of ["The last month the forward view draws", "Paid each month", "Paid back to someone each month",
      "Paid on top of gross pay, as a share of it", "Spending left empty is"]) {
      expect(screen.queryByText(new RegExp(gone))).not.toBeInTheDocument();
    }
  });

  it("shows pay and investing read from the rows Net worth edits, with a link there", async () => {
    servePlan({ ...fullPlan, income: [...fullPlan.income,
      { name: "Invented tutoring", type: "side", from: "2026-11-01", to: null, gross_minor: 80_000, cpf: false }],
      investing: [{ from: null, amount_minor: 30_000 }, { from: "2027-01", amount_minor: 50_000 }] });
    render(<Plan asOf="2026-08-28" />);
    const pay = within(await screen.findByRole("region", { name: "Pay and investing" }));
    expect(pay.getByRole("link", { name: "Edit on Net worth" })).toHaveAttribute("href", "/net-worth");
    const items = pay.getAllByRole("listitem").map((li) => li.textContent);
    expect(items).toEqual([
      "Invented jobS$4,000 a month1 Oct 2026 to 30 Jun 2027",
      "Invented tutoringS$800 a monthfrom 1 Nov 2026side income",
      "InvestingS$300 a monthfrom the start",
      "InvestingS$500 a monthfrom Jan 2027",
    ]);
    expect(screen.queryByRole("region", { name: "Pay" })).not.toBeInTheDocument();
  });

  it("reads an old single investing transfer as one step", async () => {
    servePlan(fullPlan);
    render(<Plan asOf="2026-08-28" />);
    const pay = within(await screen.findByRole("region", { name: "Pay and investing" }));
    expect(pay.getByText("from the start")).toBeInTheDocument();
  });

  it("puts a header on every column of trips and one-off payments, and no raw placeholder", async () => {
    servePlan(fullPlan);
    render(<Plan asOf="2026-08-28" />);
    const heads = (title: string) => within(screen.getByRole("region", { name: title })).getAllByRole("columnheader").map((h) => h.textContent);
    await screen.findByRole("region", { name: "Planned trips" });
    expect(heads("Planned trips")).toEqual(["Trip", "From", "To", "Spending S$ a day", "Flights S$ at start", "Remove"]);
    expect(heads("One-off payments")).toEqual(["Month", "Amount S$", "What it is", "Remove"]);
    const perDay = screen.getByLabelText("Spending (S$ a day), row 1");
    expect(perDay).toHaveValue("");
    // the meaning of an empty box is in the placeholder, not in a line above the table
    expect(perDay).toHaveAttribute("placeholder", "Like past trips");
    for (const raw of ["per_day", "flights", "gross", "label", "note", "name", "amount", "per_day_minor", "flights_minor", "amount_minor"]) {
      expect(screen.queryByPlaceholderText(raw)).not.toBeInTheDocument();
    }
  });

  it("shows the plan's months and days in the app's format, and sends them as ISO", async () => {
    const posted = servePlan(fullPlan);
    render(<Plan asOf="2026-08-28" />);
    expect(await screen.findByLabelText("Project until (month)")).toHaveValue("Jun 2027");
    expect(screen.getByLabelText("From, row 1")).toHaveValue("10 Dec 2026");
    expect(screen.getByLabelText("To, row 1")).toHaveValue("16 Dec 2026");
    expect(screen.getByLabelText("Month, row 1")).toHaveValue("Jan 2027");
    fireEvent.change(screen.getByLabelText("Month, row 1"), { target: { value: "mar 2027" } });
    fireEvent.change(screen.getByLabelText("Project until (month)"), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Save plan" }));
    await waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0]).toMatchObject({ trips: [{ from: "2026-12-10", to: "2026-12-16" }], one_offs: [{ month: "2027-03" }] });
    expect(posted[0]).not.toHaveProperty("to");
  });

  it("takes a planned trip's end along when its start moves past it", async () => {
    const posted = servePlan(fullPlan);
    render(<Plan asOf="2026-08-28" />);
    fireEvent.change(await screen.findByLabelText("From, row 1"), { target: { value: "20 Dec 2026" } });
    expect(screen.getByLabelText("To, row 1")).toHaveValue("20 Dec 2026");
    fireEvent.change(screen.getByLabelText("From, row 1"), { target: { value: "1 Dec 2026" } });
    expect(screen.getByLabelText("To, row 1")).toHaveValue("20 Dec 2026");
    fireEvent.click(screen.getByRole("button", { name: "Save plan" }));
    await waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0]).toMatchObject({ trips: [{ from: "2026-12-01", to: "2026-12-20" }] });
  });

  it("shows the employer CPF rate as a percentage and sends the fraction", async () => {
    const posted = servePlan(fullPlan);
    render(<Plan asOf="2026-08-28" />);
    const cpf = await screen.findByLabelText("Employer CPF (%)");
    expect(cpf).toHaveValue("17");
    // nothing to save until something changes
    expect(screen.getByRole("button", { name: "Save plan" })).toBeDisabled();
    fireEvent.change(cpf, { target: { value: "16.5" } });
    fireEvent.click(screen.getByRole("button", { name: "Save plan" }));
    await waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0].cpf_employer_rate).toBe(0.165);
    expect(cpf).toHaveValue("16.5");
    await waitFor(() => expect(screen.getByRole("button", { name: "Save plan" })).toBeDisabled());
    fireEvent.change(cpf, { target: { value: "18" } });
    fireEvent.click(screen.getByRole("button", { name: "Save plan" }));
    await waitFor(() => expect(posted).toHaveLength(2));
    expect(posted[1].cpf_employer_rate).toBe(0.18);
  });

  it("holds the save while the CPF rate is not a percentage", async () => {
    const posted = servePlan(fullPlan);
    render(<><Plan asOf="2026-08-28" /><Toaster /></>);
    const cpf = await screen.findByLabelText("Employer CPF (%)");
    fireEvent.change(cpf, { target: { value: "170" } });
    expect(cpf).toHaveAttribute("aria-invalid", "true");
    fireEvent.click(screen.getByRole("button", { name: "Save plan" }));
    expect((await screen.findByText("Employer CPF is a percentage from 0 to 100, like 17.")).closest("[role=alert]")).not.toBeNull();
    expect(posted).toHaveLength(0);
    fireEvent.change(cpf, { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Save plan" }));
    await waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0]).not.toHaveProperty("cpf_employer_rate");
  });

  it("cuts a long one-off note short in its row, with the whole of it on hover and focus", async () => {
    servePlan(fullPlan);
    render(<Plan asOf="2026-08-28" />);
    const note = await screen.findByLabelText("What it is, row 1");
    expect(note).toHaveValue(LONG_NOTE);
    expect(note).toHaveClass("truncate");
    expect(note).toHaveAttribute("title", LONG_NOTE);
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
    fireEvent.focus(note);
    expect(screen.getByRole("tooltip")).toHaveTextContent(LONG_NOTE);
    expect(note).toHaveAccessibleDescription(LONG_NOTE);
    fireEvent.change(note, { target: { value: LONG_NOTE + ", and more" } });
    expect(screen.getByRole("tooltip")).toHaveTextContent(LONG_NOTE + ", and more");
    fireEvent.blur(note);
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  });

  it("gives every budget column a header and the used share in words", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (url.startsWith("/api/budgets")) {
        return json({
          month: "2026-08", months: ["2026-08"],
          groups: [{ name: "Food", kind: "spend" }, { name: "Income", kind: "income" }],
          rows: [
            { category: "Groceries", need_class: "Need", bucket: "Spend", category_group: "Food", kind: "spend", planned: 40000, actual: 31200 },
            { category: "Salary", need_class: "Income", bucket: "Income", category_group: "Income", kind: "income", planned: 400000, actual: 410000 },
          ],
        });
      }
      if (url.startsWith("/api/plan")) return json({ plan: {}, categories: [], need_classes: [], spend_rates: ["home"] });
      return json({ results: [] });
    }));
    render(<Plan asOf="2026-08-28" />);
    await screen.findByRole("row", { name: "Food total" });
    const [spend, income] = screen.getAllByRole("table");
    expect(within(spend).getAllByRole("columnheader").map((h) => h.textContent)).toEqual(["Spending", "Planned", "Actual", "Used"]);
    expect(within(income).getAllByRole("columnheader").map((h) => h.textContent)).toEqual(["Income", "Planned", "Actual", "Of plan"]);
    expect(screen.getByRole("row", { name: "Food total" })).toHaveTextContent("78%");
    expect(screen.getByLabelText("Planned for Groceries")).toHaveValue("400");
    // 4,100 of 4,000 is 102.5 per cent, worked in whole cents so it rounds up
    expect(within(income).getAllByText("103%")).toHaveLength(2);
  });
});

describe("the CPF rate's two scales", () => {
  it("round-trips a percentage through the stored fraction", () => {
    expect(fractionToPercent(0.17)).toBe("17");
    expect(fractionToPercent(0.0725)).toBe("7.25");
    expect(fractionToPercent(undefined)).toBe("");
    expect(percentToFraction("17")).toBe(0.17);
    expect(percentToFraction(" 7.25 ")).toBe(0.0725);
    expect(percentToFraction("0")).toBe(0);
    expect(percentToFraction("100")).toBe(1);
    expect(percentToFraction("17.")).toBe(0.17);
    for (const bad of ["", "abc", "-1", "100.5", "1e2", "17%"]) expect(percentToFraction(bad)).toBeNull();
    for (const f of [0, 0.075, 0.17, 0.2, 1]) expect(percentToFraction(fractionToPercent(f))).toBe(f);
  });
});

// Budgets with a bucket and income, as GET /api/budgets sends them. Every figure and name is invented.
// G17: each row carries its category's id, which a save sends
const budgets = {
  month: "2026-08", months: ["2026-08", "2026-07"],
  groups: [{ id: 11, name: "Food", kind: "spend" }, { id: 12, name: "Getting around", kind: "spend" }, { id: 13, name: "Income", kind: "income" }],
  rows: [
    { category_id: 1, category: "Groceries", need_class: "Need", bucket: "Spend", category_group: "Food", kind: "spend", planned: 40000, actual: 31200 },
    { category_id: 2, category: "Coffee", need_class: "Want", bucket: "Spend", category_group: "Food", kind: "spend", planned: 5000, actual: 6000 },
    { category_id: 3, category: "Transport", need_class: "Need", bucket: "Spend", category_group: "Getting around", kind: "spend", planned: null, actual: 9000 },
    { category_id: 4, category: "Salary", need_class: "Income", bucket: "Income", category_group: "Income", kind: "income", planned: 400000, actual: 410000 },
  ],
};

/** Serve budgets and an empty plan; `save` answers the budget writes, and every write is recorded. */
function serveBudgets(save: (body: Record<string, unknown>) => Response = (b) => json(b)) {
  const posts: { url: string; body: Record<string, unknown> }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      const body = JSON.parse(init.body as string);
      posts.push({ url, body });
      return url === "/api/budgets" ? save(body) : json(body);
    }
    if (url.startsWith("/api/budgets")) return json(budgets);
    if (url.startsWith("/api/plan")) return json({ plan: {}, categories: [], need_classes: [], spend_rates: ["home"] });
    // G20: a forward view in cents with nothing to project, so the card raises no alert of its own
    if (url === "/api/results/forward-view") {
      return json({ id: "forward-view", title: "Forward view", chart: { type: "line", unit: "sgd_minor" }, data: { months: [] } });
    }
    return json({ results: [] });
  }));
  return posts;
}

describe("the plan card", () => {
  it("never shows No plan yet, or any alert, on its way to showing the plan", async () => {
    servePlan(fullPlan);
    const seen: string[] = [];
    const watch = new MutationObserver(() => {
      document.querySelectorAll('[role="alert"]').forEach((el) => seen.push(el.textContent ?? ""));
    });
    watch.observe(document.body, { childList: true, subtree: true });
    render(<Plan asOf="2026-08-28" />);
    expect(await screen.findByLabelText("Insurance premiums (S$ a month)")).toHaveValue("40");
    watch.disconnect();
    expect(seen.filter((t) => t.includes("No plan yet"))).toEqual([]);
  });
});

describe("the budgets card", () => {
  it("totals what is planned, what has gone and how many categories are over, income left out", async () => {
    serveBudgets();
    render(<Plan asOf="2026-08-28" />);
    const tiles = within(await screen.findByRole("group", { name: "Budgets at a glance" }));
    // planned 400 + 50, actual 312 + 60 + 90; Coffee is the one over, and Transport has no plan
    expect(tiles.getByText("Planned").parentElement).toHaveTextContent("S$450");
    // the share of plan is of the categories that have one (372 of 450), the rest said apart
    expect(tiles.getByText("Actual so far").parentElement).toHaveTextContent("S$462S$372 in planned categories is 83% of plan, S$90 unplanned");
    expect(tiles.getByText("S$462")).not.toHaveClass("text-warn");
    expect(tiles.getByText("Over plan").parentElement).toHaveTextContent("1category");
    expect(tiles.getByText("1")).toHaveClass("text-warn");
  });

  it("lists only the categories over plan on a chip, and puts the rest back when it is pressed again", async () => {
    serveBudgets();
    render(<Plan asOf="2026-08-28" />);
    const chip = await screen.findByRole("button", { name: "Show only over plan" });
    expect(chip).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByLabelText("Planned for Groceries")).toBeInTheDocument();
    fireEvent.click(chip);
    expect(chip).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByLabelText("Planned for Coffee")).toBeInTheDocument();
    for (const gone of ["Groceries", "Transport", "Salary"]) expect(screen.queryByLabelText(`Planned for ${gone}`)).not.toBeInTheDocument();
    // the bucket still totals all of its categories, over or not
    expect(screen.getByRole("row", { name: "Food total" })).toHaveTextContent("FoodS$450S$372");
    expect(screen.getAllByRole("table")).toHaveLength(1);
    fireEvent.click(chip);
    expect(screen.getByLabelText("Planned for Groceries")).toBeInTheDocument();
    expect(screen.getAllByRole("table")).toHaveLength(2);
  });

  it("saves a planned amount on Enter, moves on to the next row, and says Saved beside the row", async () => {
    const posts = serveBudgets();
    render(<Plan asOf="2026-08-28" />);
    const groceries = await screen.findByLabelText("Planned for Groceries");
    groceries.focus();
    fireEvent.change(groceries, { target: { value: "1,250.50" } });
    fireEvent.keyDown(groceries, { key: "Enter" });
    expect(screen.getByLabelText("Planned for Coffee")).toHaveFocus();
    await waitFor(() => expect(posts.filter((p) => p.url === "/api/budgets")).toHaveLength(1));
    expect(posts[0].body).toEqual({ category_id: 1, category: "Groceries", month: "2026-08", planned: 125050 });
    const row = screen.getByLabelText("Planned for Groceries").closest("tr")!;
    await waitFor(() => expect(within(row).getByRole("status")).toHaveTextContent("Saved"));
    expect(within(screen.getByLabelText("Planned for Coffee").closest("tr")!).getByRole("status")).toHaveTextContent("");
  });

  it("asks for the budgets once after a save, at the address it already shows", async () => {
    const posts = serveBudgets();
    const gets = () => vi.mocked(globalThis.fetch).mock.calls
      .filter(([u, init]) => String(u).startsWith("/api/budgets") && (init as RequestInit | undefined)?.method !== "POST").map(([u]) => String(u));
    render(<Plan asOf="2026-08-28" />);
    const groceries = await screen.findByLabelText("Planned for Groceries");
    const before = gets().length;
    fireEvent.change(groceries, { target: { value: "500" } });
    fireEvent.blur(groceries);
    await waitFor(() => expect(posts.filter((p) => p.url === "/api/budgets")).toHaveLength(1));
    await waitFor(() => expect(gets().length).toBeGreaterThan(before));
    await act(async () => {});
    // the write itself has every view on screen ask again; no version in the address asks a second time
    expect(gets().length).toBe(before + 1);
    expect(gets().every((u) => !u.includes("v="))).toBe(true);
  });

  it("saves on leaving the box too, and not at all when nothing changed", async () => {
    const posts = serveBudgets();
    render(<Plan asOf="2026-08-28" />);
    const coffee = await screen.findByLabelText("Planned for Coffee");
    fireEvent.focus(coffee);
    fireEvent.blur(coffee);
    expect(posts).toHaveLength(0);
    fireEvent.focus(coffee);
    fireEvent.change(coffee, { target: { value: "" } });
    fireEvent.blur(coffee);
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0].body).toEqual({ category_id: 2, category: "Coffee", month: "2026-08", planned: null });
  });

  it("says what is wrong under the row, and nowhere else, until the box is changed", async () => {
    const posts = serveBudgets();
    render(<Plan asOf="2026-08-28" />);
    const groceries = await screen.findByLabelText("Planned for Groceries");
    fireEvent.focus(groceries);
    fireEvent.change(groceries, { target: { value: "1k5" } });
    fireEvent.blur(groceries);
    const row = groceries.closest("tr")!;
    expect(row.nextElementSibling).toHaveTextContent("A planned amount is a number of dollars, zero or more.");
    expect(within(row.nextElementSibling as HTMLElement).getByRole("alert")).toBeInTheDocument();
    expect(groceries).toHaveAttribute("aria-invalid", "true");
    expect(posts).toHaveLength(0);
    // the card's own top is clear of it
    expect(screen.getAllByRole("alert")).toHaveLength(1);
    fireEvent.change(groceries, { target: { value: "300" } });
    expect(groceries).not.toHaveAttribute("aria-invalid");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("puts the server's refusal of a row under that row", async () => {
    serveBudgets(() => json({ error: "no category 'Coffee'" }, 422));
    render(<Plan asOf="2026-08-28" />);
    const coffee = await screen.findByLabelText("Planned for Coffee");
    fireEvent.focus(coffee);
    fireEvent.change(coffee, { target: { value: "70" } });
    fireEvent.blur(coffee);
    await waitFor(() => expect(coffee.closest("tr")!.nextElementSibling).toHaveTextContent("no category 'Coffee'"));
  });

  it("shows a planned amount with cents as it was planned, not rounded", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (url.startsWith("/api/budgets")) {
        return json({ ...budgets, groups: [], rows: [{ category: "Coffee", need_class: "Want", bucket: "Spend", planned: 12050, actual: 0 }] });
      }
      if (url.startsWith("/api/plan")) return json({ plan: {}, categories: [], need_classes: [], spend_rates: ["home"] });
      return json({ results: [] });
    }));
    render(<Plan asOf="2026-08-28" />);
    expect(await screen.findByLabelText("Planned for Coffee")).toHaveValue("120.50");
  });

  it("is a stack of small cards under 768px: no column is wider than the screen", async () => {
    serveBudgets();
    render(<Plan asOf="2026-08-28" />);
    const [spending] = await screen.findAllByRole("table");
    expect(spending).toHaveClass("max-md:block");
    const row = screen.getByLabelText("Planned for Groceries").closest("tr")!;
    expect(row).toHaveClass("max-md:grid");
    expect(row.className).not.toMatch(/overflow|min-w-\[/);
    expect(spending.parentElement).not.toHaveClass("overflow-x-auto");
  });
});

// G18: true when the page would hold a reload or a closed tab behind the browser's question

describe("the plan card's save", () => {
  it("starts saved, says Unsaved changes after an edit, and Discard puts the saved plan back", async () => {
    const posted = servePlan(fullPlan);
    render(<Plan asOf="2026-08-28" />);
    const insurance = await screen.findByLabelText("Insurance premiums (S$ a month)");
    expect(screen.getByRole("button", { name: "Save plan" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Discard" })).toBeDisabled();
    // "Saved" is there on a phone as on a desktop: nothing hides it under 768px
    expect(screen.getByText("Saved").className).not.toContain("hidden");
    fireEvent.change(insurance, { target: { value: "55" } });
    expect(screen.getByRole("status", { name: "" })).toHaveTextContent("Unsaved changes");
    await expectAsks();
    expect(screen.getByRole("button", { name: "Save plan" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "Discard" }));
    expect(screen.getByLabelText("Insurance premiums (S$ a month)")).toHaveValue("40");
    expect(screen.queryByText("Unsaved changes")).not.toBeInTheDocument();
    await expectFree();
    expect(screen.getByRole("button", { name: "Save plan" })).toBeDisabled();
    expect(posted).toHaveLength(0);
  });

  it("is saved again once it is saved, and an unsaved edit survives a reload of the page's data", async () => {
    const posted = servePlan(fullPlan);
    render(<Plan asOf="2026-08-28" />);
    const insurance = await screen.findByLabelText("Insurance premiums (S$ a month)");
    fireEvent.change(insurance, { target: { value: "55" } });
    fireEvent.click(screen.getByRole("button", { name: "Save plan" }));
    await waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0]).toMatchObject({ insurance_minor: 5_500 });
    await waitFor(() => expect(screen.queryByText("Unsaved changes")).not.toBeInTheDocument());
  });

  it("says the result of a save in a toast, not in a line inside the card", async () => {
    servePlan(fullPlan);
    render(<><Plan asOf="2026-08-28" /><Toaster /></>);
    const insurance = await screen.findByLabelText("Insurance premiums (S$ a month)");
    fireEvent.change(insurance, { target: { value: "55" } });
    fireEvent.click(screen.getByRole("button", { name: "Save plan" }));
    const toast = await screen.findByText(/^Plan saved\./);
    expect(toast.closest('[aria-live="polite"]')).not.toBeNull();
    expect(screen.getByRole("heading", { name: "Plan" }).closest("section")).not.toContainElement(toast);
  });

  it("keeps the heading row, with the save in it, pinned on a phone", async () => {
    servePlan(fullPlan);
    render(<Plan asOf="2026-08-28" />);
    await screen.findByLabelText("Insurance premiums (S$ a month)");
    const header = screen.getByRole("heading", { name: "Plan" }).parentElement!;
    expect(header).toHaveClass("max-md:sticky", "max-md:-top-4");
    expect(header).toContainElement(screen.getByRole("button", { name: "Save plan" }));
  });

  it("reads an amount that is not a number as it is typed, marks it, and holds the save", async () => {
    const posted = servePlan(fullPlan);
    render(<Plan asOf="2026-08-28" />);
    const insurance = await screen.findByLabelText("Insurance premiums (S$ a month)");
    fireEvent.change(insurance, { target: { value: "1k5" } });
    expect(insurance).toHaveAttribute("aria-invalid", "true");
    expect(insurance).toHaveAccessibleDescription("Use digits, like 1,200.50");
    fireEvent.click(screen.getByRole("button", { name: "Save plan" }));
    expect(posted).toHaveLength(0);
    expect(insurance).toHaveFocus();
    // a trip's flights and a one-off amount are held the same way
    const flights = screen.getByLabelText("Flights (S$ at start), row 1");
    fireEvent.change(flights, { target: { value: "4x" } });
    expect(flights).toHaveAttribute("aria-invalid", "true");
    fireEvent.change(flights, { target: { value: "450" } });
    fireEvent.change(insurance, { target: { value: "1,500" } });
    expect(flights).not.toHaveAttribute("aria-invalid");
    expect(insurance).not.toHaveAttribute("aria-invalid");
    expect(screen.queryByText("Use digits, like 1,200.50")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Save plan" }));
    await waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0]).toMatchObject({ insurance_minor: 150_000, trips: [{ flights_minor: 45_000 }] });
  });

  it("saves what is typed to the cent, sent as cents and never as dollars", async () => {
    // G17: "1,234.5" is S$1,234.50, read digit by digit; "0.29" is 29 cents, not 28.999999999999996
    const posted = servePlan(fullPlan);
    render(<Plan asOf="2026-08-28" />);
    const insurance = await screen.findByLabelText("Insurance premiums (S$ a month)");
    expect(insurance).toHaveValue("40");
    fireEvent.focus(insurance);
    fireEvent.change(insurance, { target: { value: "1,234.5" } });
    fireEvent.blur(insurance);
    expect(insurance).toHaveValue("1,234.5");
    fireEvent.change(screen.getByLabelText("Amount (S$), row 1"), { target: { value: "0.29" } });
    fireEvent.click(screen.getByRole("button", { name: "Save plan" }));
    await waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0]).toMatchObject({ insurance_minor: 123_450, one_offs: [{ amount_minor: 29 }] });
    expect(posted[0]).not.toHaveProperty("insurance");
    expect(posted[0]).not.toHaveProperty("dca");
  });

  it("marks an amount finer than a cent, which cents cannot hold", async () => {
    const posted = servePlan(fullPlan);
    render(<Plan asOf="2026-08-28" />);
    const insurance = await screen.findByLabelText("Insurance premiums (S$ a month)");
    fireEvent.change(insurance, { target: { value: "12.345" } });
    expect(insurance).toHaveAttribute("aria-invalid", "true");
    fireEvent.click(screen.getByRole("button", { name: "Save plan" }));
    expect(posted).toHaveLength(0);
  });

  it("uses plain text boxes with the decimal keypad for every amount, no spinners", async () => {
    servePlan(fullPlan);
    render(<Plan asOf="2026-08-28" />);
    await screen.findByLabelText("Insurance premiums (S$ a month)");
    expect(screen.queryAllByRole("spinbutton")).toHaveLength(0);
    for (const name of ["Insurance premiums (S$ a month)", "Flights (S$ at start), row 1", "Amount (S$), row 1"]) {
      const box = screen.getByLabelText(name);
      expect(box).toHaveAttribute("inputmode", "decimal");
      expect(box).toHaveAttribute("enterkeyhint", "next");
    }
  });

  it("stacks trips and one-off payments as cards, each field under its name, below 1280px", async () => {
    servePlan(fullPlan);
    render(<Plan asOf="2026-08-28" />);
    const trips = within(await screen.findByRole("region", { name: "Planned trips" }));
    const row = trips.getByLabelText("Flights (S$ at start), row 1").closest("tr")!;
    expect(row).toHaveClass("max-xl:grid");
    expect(row.closest("table")).toHaveClass("max-xl:block");
    expect([...row.querySelectorAll("td[data-label]")].map((td) => td.getAttribute("data-label")))
      .toEqual(["Trip", "From", "To", "Spending S$ a day", "Flights S$ at start"]);
    expect(row.closest("table")!.parentElement).not.toHaveClass("overflow-x-auto");
  });

  // G13: figures the record already knows, as chips under the boxes. All figures here are invented.
  describe("suggestion chips", () => {
    const known = {
      income: [], investing: null, cpf_employer_rate: 0.17,
      insurance: { per_month: 21_000, series: 2 }, repayments: { per_month: 55_050, series: 1 },
    };
    function serveWith(plan: Record<string, unknown>, suggestions: unknown = known) {
      const posted: Record<string, unknown>[] = [];
      vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
        if (init?.method === "POST") {
          const body = JSON.parse(init.body as string);
          posted.push(body);
          return json(body);
        }
        if (url.startsWith("/api/suggestions")) return json(suggestions);
        if (url.startsWith("/api/plan")) {
          return json({ plan, categories: [], need_classes: [], spend_rates: ["window"] });
        }
        if (url.startsWith("/api/budgets")) return json({ month: "2026-08", rows: [], months: [] });
        return json({ error: "no result" }, 404);
      }));
      return posted;
    }

    it("offers what the detector sees for an empty box, and fills it only when the chip is tapped", async () => {
      const posted = serveWith({});
      render(<Plan asOf="2026-08-28" />);
      const insurance = await screen.findByLabelText("Insurance premiums (S$ a month)");
      expect(insurance).toHaveValue("");
      expect(screen.queryByText("Unsaved changes")).not.toBeInTheDocument();
      fireEvent.click(await screen.findByRole("button", { name: "Seen S$210 a month, use" }));
      expect(insurance).toHaveValue("210");
      fireEvent.click(screen.getByRole("button", { name: "Seen S$551 a month, use" }));
      expect(screen.getByLabelText("Repayments (S$ a month)")).toHaveValue("551");
      fireEvent.click(screen.getByRole("button", { name: "Rate for your age is 17%, use" }));
      expect(screen.getByLabelText("Employer CPF (%)")).toHaveValue("17");
      // the chips go once their boxes hold the figures
      expect(screen.queryByRole("button", { name: /, use$/ })).not.toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "Save plan" }));
      await waitFor(() => expect(posted).toHaveLength(1));
      // the chip fills the whole dollars it shows
      expect(posted[0]).toMatchObject({ insurance_minor: 21_000, repayments_minor: 55_100, cpf_employer_rate: 0.17 });
    });

    it("shows no chip for a box close to the record, and says where the record clearly disagrees", async () => {
      // 200 is within a sixth of 210; 100 is not. 0.17 is the rate for the age; 0.075 is not
      serveWith({ insurance_minor: 20_000, repayments_minor: 10_000, cpf_employer_rate: 0.17 });
      render(<Plan asOf="2026-08-28" />);
      await screen.findByLabelText("Insurance premiums (S$ a month)");
      expect(screen.queryByRole("button", { name: /^Seen S\$210/ })).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /^Record shows S\$210/ })).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /^Rate for your age/ })).not.toBeInTheDocument();
      const chip = screen.getByRole("button", { name: "Record shows S$551 a month, use" });
      fireEvent.click(chip);
      expect(screen.getByLabelText("Repayments (S$ a month)")).toHaveValue("551");
      expect(screen.queryByRole("button", { name: /^Record shows/ })).not.toBeInTheDocument();
    });

    it("offers the rate for the age when the box holds another", async () => {
      serveWith({ cpf_employer_rate: 0.075 });
      render(<Plan asOf="2026-08-28" />);
      const box = await screen.findByLabelText("Employer CPF (%)");
      expect(box).toHaveValue("7.5");
      fireEvent.click(await screen.findByRole("button", { name: "Rate for your age is 17%, use" }));
      expect(box).toHaveValue("17");
    });

    it("shows no chip when the record has nothing to offer", async () => {
      serveWith({}, { income: [], investing: null, cpf_employer_rate: null, insurance: null, repayments: null });
      render(<Plan asOf="2026-08-28" />);
      await screen.findByLabelText("Insurance premiums (S$ a month)");
      expect(screen.queryByRole("button", { name: /, use$/ })).not.toBeInTheDocument();
    });

    it("shows no chip, and no error, when the ask fails", async () => {
      servePlan({});
      render(<Plan asOf="2026-08-28" />);
      await screen.findByLabelText("Insurance premiums (S$ a month)");
      expect(screen.queryByRole("button", { name: /, use$/ })).not.toBeInTheDocument();
    });
  });
});

// G18: unsaved means typed here; a plan saved from another tab is not an edit on this one
describe("the plan when another tab saves it", () => {
  it("keeps typing made while its own save is on its way, back to the old saved value included", async () => {
    let saved: Record<string, unknown> = { ...fullPlan };
    let release: () => void = () => {};
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        const body = JSON.parse(init.body as string);
        return new Promise<Response>((done) => { release = () => { saved = body; done(json(body)); }; });
      }
      if (url.startsWith("/api/plan")) {
        return json({ plan: saved, categories: [], need_classes: [], spend_rates: ["home"] });
      }
      if (url.startsWith("/api/budgets")) return json({ month: "", rows: [], months: [] });
      return json({ results: [] });
    }));
    render(<Plan asOf="2026-08-28" />);
    const insurance = await screen.findByLabelText("Insurance premiums (S$ a month)");
    fireEvent.change(insurance, { target: { value: "250" } });
    fireEvent.click(screen.getByRole("button", { name: "Save plan" }));
    fireEvent.change(screen.getByLabelText("Insurance premiums (S$ a month)"), { target: { value: "40" } });
    await act(async () => release());
    await waitFor(() => expect(saved).toMatchObject({ insurance_minor: 25_000 }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Save plan" })).toBeEnabled());
    expect(screen.getByLabelText("Insurance premiums (S$ a month)")).toHaveValue("40");
    expect(screen.getByText("Unsaved changes")).toBeInTheDocument();
    await expectAsks();
  });

  it("follows the new plan while untouched, and keeps typing that is there", async () => {
    const plan = { ...fullPlan };
    servePlan(plan);
    render(<Plan asOf="2026-08-28" />);
    const insurance = await screen.findByLabelText("Insurance premiums (S$ a month)");
    expect(insurance).toHaveValue("40");
    plan.insurance_minor = 6_500;
    act(() => revalidate());
    await waitFor(() => expect(screen.getByLabelText("Insurance premiums (S$ a month)")).toHaveValue("65"));
    expect(screen.queryByText("Unsaved changes")).not.toBeInTheDocument();
    await expectFree();
    fireEvent.change(screen.getByLabelText("Insurance premiums (S$ a month)"), { target: { value: "70" } });
    plan.insurance_minor = 8_000;
    act(() => revalidate());
    await waitFor(() => expect(leaveAsks()).toBe(true));
    expect(screen.getByLabelText("Insurance premiums (S$ a month)")).toHaveValue("70");
  });
});
