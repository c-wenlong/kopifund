import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { NetWorth, TotalTip } from "./NetWorth";
import { revalidate, type Growth, type NetWorth as NetWorthT, type Suggestions } from "../api";
import { choose } from "../test-select";
import { expectAsks, expectFree, json, leaveAsks, settle, useSizedCharts } from "../test-helpers";

vi.mock("../router", async (actual) => ({
  ...(await import("../test-helpers")).withNavigate(await actual()), onLinkClick: () => () => {},
}));

// G18: true when the page would hold a reload or a closed tab behind the browser's question

// Every figure, name and date here is invented.
const netWorth: NetWorthT = {
  parts: ["cash", "investments", "cpf"],
  months: Array.from({ length: 30 }, (_, i) => {
    const month = `${2024 + Math.floor((i + 3) / 12)}-${String(((i + 3) % 12) + 1).padStart(2, "0")}`;
    return { month, cash: 100_000 + i, cash_as_of: month, investments: 200_000, investments_as_of: "2024-12-31",
      cpf: 300_000, cpf_as_of: "2024-12-31", total: 600_000 + i };
  }),
};

const totalRows = [
  { month: "2026-10", age: null, cash: 1_000_000, tiger: 2_000_000, cpf: null, cpf_held: false, total: 3_000_000, low: 2_990_000, high: 3_010_000 },
  { month: "2031-10", age: null, cash: 4_000_000, tiger: 6_000_000, cpf: null, cpf_held: false, total: 10_000_000, low: 9_000_000, high: 11_000_000 },
];

const growth = (over: Partial<Growth> = {}): Growth => ({
  today: "2026-10-12",
  end: "2036-10",
  birth_date: null,
  age: null,
  inputs: {
    income: [{ name: "Invented job", type: "employment", from: "2026-01-01", to: null, gross_minor: 500_000, cpf: true, bonus_months: null, bonus_month: null }],
    investing: [{ from: null, amount_minor: 30_000 }],
    projection: {
      pessimistic_pct: "3", expected_pct: "6", optimistic_pct: "9", inflation_pct: "2.5", salary_growth_pct: "0",
      sums_growth_pct: "3.5", spend_growth_pct: null, horizon: "10y", dollars: "today", spend_minor: null, milestone_minor: 10_000_000,
    },
  },
  spend_hint: { per_month: 250_000, from: "2025-10", to: "2026-09", months: 12 },
  assumptions: {
    pessimistic_pct: "3", expected_pct: "6", optimistic_pct: "9", inflation_pct: "2.5", salary_growth_pct: "0",
    sums_growth_pct: "3.5", spend_growth_pct: "2.5", spend_growth_follows_inflation: true, horizon: "10y", dollars: "today",
    spend: 250_000, spend_from: "average", milestone: 10_000_000, investing_now: 30_000,
  },
  can_reach_65: false,
  markers: [],
  rules: {
    as_of: "2026-10-01", source: "CPF Board", ow_ceiling: 800_000, bhs: 7_900_000, frs: 22_040_000, sums_year: 2026,
    tax: { as_of: "2026-10-01", source: "IRAS" }, medisave: { as_of: "2026-10-01", source: "IRAS" },
  },
  tax_years: [{ year: 2026, assessable: 6_000_000, reliefs: 1_300_000, chargeable: 4_700_000, tax: 104_000, cpf_employee: 1_200_000, medisave: 0 }],
  tiger: { as_of: "2026-08-31", measured: true, broker: "Tiger Brokers", start: 1_000_000, contributed: 800_000, rows: [
    { month: "2026-08", pessimistic: 1_000_000, expected: 1_000_000, optimistic: 1_000_000, put_in: 800_000 },
    { month: "2026-09", pessimistic: 1_052_500, expected: 1_055_000, optimistic: 1_057_500, put_in: 850_000 },
  ] },
  cpf: { as_of: "2025-12-31", needs: "birth_date", rows: [] },
  cash: { as_of: "2026-09", start: 1_000_000, measured: true,
    before_start: { from: "2026-01", to: "2026-09", tax: 0, medisave: 0 }, rows: [
    { month: "2026-09", cash: 1_000_000, net: 0, income: 0, employment: 0, bonus: 0, side: 0, cpf_employee: 0, tax: 0, medisave: 0, spend: 0, investing: 0 },
    { month: "2026-10", cash: 1_100_000, net: 100_000, income: 500_000, employment: 500_000, bonus: 0, side: 0, cpf_employee: 100_000, tax: 10_000, medisave: 0, spend: 260_000, investing: 30_000 },
  ] },
  total: {
    parts: ["cash", "tiger"],
    rows: totalRows,
    tiles: { today: { month: "2026-10", total: 3_000_000 }, in_5y: { month: "2031-10", total: 10_000_000 }, in_10y: null },
    milestone: { value: 10_000_000, month: "2031-10", total: 10_000_000 },
    below_zero: [],
    low: { month: "2026-10", cash: 1_000_000 },
  },
  ...over,
});

type Post = { url: string; body: Record<string, unknown> };
type Handler = (url: string, post?: Post) => Response | Promise<Response> | undefined;

function serve(handler: Handler = () => undefined) {
  const asked: string[] = [];
  const posts: Post[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    asked.push(url);
    const post = init?.method === "POST" ? { url, body: JSON.parse(String(init.body)) } : undefined;
    if (post) posts.push(post);
    const own = handler(url, post);
    if (own) return own;
    if (url.startsWith("/api/net-worth")) return json(netWorth);
    if (url.startsWith("/api/growth")) return json(growth());
    if (url.startsWith("/api/results/")) {
      const id = url.split("/").pop();
      // G20: a result in cents, as the stats write one; a card shows no result in dollars
      return json({ id, title: `${id} title`, findings: [`${id} finding`], chart: { unit: "sgd_minor" }, data: {} });
    }
    return new Response("{}", { status: 404 });
  }));
  return { asked, posts };
}

const rowsOf = () => screen.getAllByRole("row", { name: /^Income row \d+$/ });

// G18: the inputs live in a dialog that the strip's Edit opens
async function openInputs() {
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  return screen.getByRole("dialog", { name: "Your projection inputs" });
}
const dialog = () => screen.queryByRole("dialog", { name: "Your projection inputs" });
/** A figure on the strip, by its label: "S$5,000 a month". */
const fact = (label: string) =>
  within(screen.getByRole("region", { name: "Your projection inputs" })).getByText(label, { selector: "dt" }).nextElementSibling!;

// A server that keeps what is saved, so the reload every save sets off reads it back
function stored(start: Growth = growth(), refuse?: string) {
  let now = start;
  return serve((url, post) => {
    if (url === "/api/growth") return json(now);
    if (url !== "/api/growth/inputs" || !post) return undefined;
    if (refuse) return json({ error: refuse }, 422);
    now = { ...now, inputs: post.body as unknown as Growth["inputs"] };
    return json(now);
  });
}

describe("Net worth page", () => {
  it("draws net worth over time with a range selector", async () => {
    serve();
    render(<NetWorth />);
    const all = await screen.findByRole("radio", { name: "All" });
    expect(all).toHaveAttribute("aria-checked", "true");
    expect(screen.getByRole("img", { name: /Net worth by month, S\$6,000 in/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("radio", { name: "1Y" }));
    expect(screen.getByRole("radio", { name: "1Y" })).toHaveAttribute("aria-checked", "true");
  });

  it("asks for a date of birth before projecting CPF, and still draws Tiger Brokers", async () => {
    serve();
    render(<NetWorth />);
    expect(await screen.findByRole("link", { name: "Your profile" })).toHaveAttribute("href", "/profile");
    expect(screen.getByRole("region", { name: "Investments at Tiger Brokers" })).toBeInTheDocument();
    expect(await screen.findByRole("region", { name: "Net worth projected" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "To 65" })).toBeDisabled();
  });

  it("shows the saved inputs in the dialog as text boxes, the record's average as the spending hint", async () => {
    serve();
    render(<NetWorth />);
    await openInputs();
    expect(await screen.findByRole("textbox", { name: "Name, income row 1" })).toHaveValue("Invented job");
    const gross = screen.getByRole("textbox", { name: "Gross a month (S$), income row 1" });
    expect(gross).toHaveValue("5,000");
    expect(gross).toHaveAttribute("inputMode", "decimal");
    expect(screen.getByLabelText("To, income row 1")).toHaveValue("");
    expect(screen.getByRole("columnheader", { name: "To" })).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Amount a month (S$), investing step 1" })).toHaveValue("300");
    expect(screen.getByRole("textbox", { name: "Spending a month (S$)" })).toHaveAttribute("placeholder", "2500");
    expect(screen.getByRole("textbox", { name: "Expected return" })).toHaveValue("6");
    expect(screen.getByRole("textbox", { name: "Milestone (S$)" })).toHaveValue("100,000");
    // amounts are plain text boxes, so no spinner arrows and no scroll-wheel changes
    expect(screen.queryAllByRole("spinbutton")).toHaveLength(0);
    expect(screen.getByText("Saved")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });

  it("adds and removes an income row, marking the card unsaved, and Discard puts it back", async () => {
    serve();
    render(<NetWorth />);
    await openInputs();
    await screen.findByRole("textbox", { name: "Name, income row 1" });
    fireEvent.click(screen.getByRole("button", { name: "Add income" }));
    expect(rowsOf()).toHaveLength(2);
    expect(screen.getByRole("status")).toHaveTextContent("Unsaved changes");
    await expectAsks();
    choose(screen.getByLabelText("Type, income row 2"), "Side income");
    expect(screen.getByLabelText("Pays CPF, income row 2")).not.toBeChecked();      // side income: no CPF
    expect(screen.queryByLabelText("Bonus month, income row 2")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Remove income row 1" }));
    expect(rowsOf()).toHaveLength(1);
    expect(screen.getByLabelText("Type, income row 1")).toHaveTextContent("Side income");
    fireEvent.click(screen.getByRole("button", { name: "Discard" }));
    expect(rowsOf()).toHaveLength(1);
    expect(screen.getByRole("textbox", { name: "Name, income row 1" })).toHaveValue("Invented job");
    expect(screen.queryByText("Unsaved changes")).not.toBeInTheDocument();
    await expectFree();
  });

  // G18: what is typed while a save is on its way is a new edit, even typed back to the old saved value
  it("keeps typing made during a save, back to the old saved value included, as unsaved", async () => {
    let release: () => void = () => {};
    // the server holds "Saved as B" once the save lands, and every later ask says so
    let stored = "Invented job";
    const answer = () => {
      const g = growth();
      g.inputs.income[0].name = stored;
      return json(g);
    };
    serve((url, post) => {
      if (url === "/api/growth") return answer();
      if (url !== "/api/growth/inputs" || !post) return undefined;
      return new Promise<Response>((done) => { release = () => { stored = "Saved as B"; done(answer()); }; });
    });
    render(<NetWorth />);
    await openInputs();
    const name = await screen.findByRole("textbox", { name: "Name, income row 1" });
    fireEvent.change(name, { target: { value: "Saved as B" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Name, income row 1" }), { target: { value: "Invented job" } });
    await act(async () => release());
    await waitFor(() => expect(screen.queryByRole("button", { name: "Saving" })).not.toBeInTheDocument());
    // the save sets off a reload of the figures: let it land, since what is unsaved is measured against it
    await settle();
    expect(screen.getByRole("textbox", { name: "Name, income row 1" })).toHaveValue("Invented job");
    expect(screen.getByText("Unsaved changes")).toBeInTheDocument();
    await expectAsks();
  });

  // G18: unsaved means typed here, never "the record moved under an untouched form"
  describe("when another tab saves the inputs", () => {
    const otherTab = () => {
      let job = "Invented job";
      serve((url) => {
        if (url !== "/api/growth") return undefined;
        const g = growth();
        g.inputs.income[0].name = job;
        return json(g);
      });
      return (name: string) => { job = name; act(() => revalidate()); };
    };

    it("an untouched form follows the new inputs and stays saved", async () => {
      const save = otherTab();
      render(<NetWorth />);
      await openInputs();
      const name = await screen.findByRole("textbox", { name: "Name, income row 1" });
      expect(name).toHaveValue("Invented job");
      save("Renamed elsewhere");
      await waitFor(() => expect(screen.getByRole("textbox", { name: "Name, income row 1" })).toHaveValue("Renamed elsewhere"));
      expect(screen.queryByText("Unsaved changes")).not.toBeInTheDocument();
      await expectFree();
    });

    it("a form with typing in it keeps the typing, still unsaved", async () => {
      const save = otherTab();
      render(<NetWorth />);
      await openInputs();
      const name = await screen.findByRole("textbox", { name: "Name, income row 1" });
      fireEvent.change(name, { target: { value: "Typed here" } });
      save("Renamed elsewhere");
      await waitFor(() => expect(leaveAsks()).toBe(true));
      expect(screen.getByRole("textbox", { name: "Name, income row 1" })).toHaveValue("Typed here");
      expect(screen.getByRole("status")).toHaveTextContent("Unsaved changes");
    });

    it("a box typed back to its saved value leaves nothing unsaved, and then follows", async () => {
      const save = otherTab();
      render(<NetWorth />);
      await openInputs();
      const name = await screen.findByRole("textbox", { name: "Name, income row 1" });
      fireEvent.change(name, { target: { value: "Typed here" } });
      fireEvent.change(name, { target: { value: "Invented job" } });
      await expectFree();
      save("Renamed elsewhere");
      await waitFor(() => expect(screen.getByRole("textbox", { name: "Name, income row 1" })).toHaveValue("Renamed elsewhere"));
      await expectFree();
    });
  });

  it("previews the chart live with the unsaved values and writes nothing", async () => {
    const { posts } = serve((url) => (url === "/api/growth/preview"
      ? json(growth({ total: { ...growth().total, tiles: { ...growth().total.tiles, today: { month: "2026-10", total: 4_321_000 } } } }))
      : undefined));
    render(<NetWorth />);
    const sheet = await openInputs();
    const gross = await screen.findByRole("textbox", { name: "Gross a month (S$), income row 1" });
    // G18: the tiles ride at the top of the dialog, so the preview moves where the typing is
    const tiles = within(within(sheet).getByRole("group", { name: "Net worth at a glance" }));
    expect(tiles.getByText("S$30,000")).toBeInTheDocument();
    fireEvent.change(gross, { target: { value: "7,000" } });
    expect(await tiles.findByText("S$43,210")).toBeInTheDocument();
    const preview = posts.filter((p) => p.url === "/api/growth/preview");
    expect(preview.length).toBeGreaterThan(0);
    // G17: the typed text crosses as cents
    const income = preview[preview.length - 1].body.income as { gross_minor: number }[];
    expect(income[0].gross_minor).toBe(700_000);
    expect(posts.some((p) => p.url === "/api/growth/inputs")).toBe(false);
  });

  it("saves with the Save button, closes the dialog, and the strip shows what was saved", async () => {
    const { posts } = stored();
    render(<NetWorth />);
    await openInputs();
    const amount = await screen.findByRole("textbox", { name: "Amount a month (S$), investing step 1" });
    fireEvent.change(amount, { target: { value: "450" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(dialog()).not.toBeInTheDocument());
    const body = posts.find((p) => p.url === "/api/growth/inputs")!.body;
    expect(body.investing).toEqual([{ from: null, amount_minor: 45_000 }]);
    expect((body.projection as Record<string, unknown>).expected_pct).toBe("6");
    await waitFor(() => expect(fact("Investing")).toHaveTextContent("S$450 a month"));
    await expectFree();
    // the dialog opens again on what is saved
    await openInputs();
    expect(screen.getByRole("textbox", { name: "Amount a month (S$), investing step 1" })).toHaveValue("450");
    expect(screen.getByText("Saved")).toBeInTheDocument();
  });

  it("an empty investing month reads From the start, takes a month, and clears back", async () => {
    const { posts } = serve();
    render(<NetWorth />);
    await openInputs();
    const from = await screen.findByRole("textbox", { name: "From, investing step 1" });
    expect(from).toHaveAttribute("placeholder", "From the start");
    expect(from).toHaveValue("");
    // a month typed any way it reads is what the preview and the save send
    fireEvent.change(from, { target: { value: "jan 2027" } });
    expect(screen.getByText("Unsaved changes")).toBeInTheDocument();
    await waitFor(() => expect(posts.some((p) => p.url === "/api/growth/preview")).toBe(true));
    expect(posts.filter((p) => p.url === "/api/growth/preview").at(-1)!.body.investing).toEqual([{ from: "2027-01", amount_minor: 30_000 }]);
    fireEvent.blur(from);
    expect(from).toHaveValue("Jan 2027");
    // the clear button goes back to the words, which is the saved "from the start"
    fireEvent.click(screen.getByRole("button", { name: "Clear From, investing step 1" }));
    expect(from).toHaveValue("");
    expect(from).toHaveAttribute("placeholder", "From the start");
    expect(screen.queryByText("Unsaved changes")).not.toBeInTheDocument();
    fireEvent.change(from, { target: { value: "2027-03" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(posts.some((p) => p.url === "/api/growth/inputs")).toBe(true));
    expect(posts.find((p) => p.url === "/api/growth/inputs")!.body.investing).toEqual([{ from: "2027-03", amount_minor: 30_000 }]);
  });

  it("shows the income dates in the app's format, an empty end as Ongoing, and sends ISO days", async () => {
    const { posts } = serve();
    render(<NetWorth />);
    await openInputs();
    expect(await screen.findByLabelText("From, income row 1")).toHaveValue("1 Jan 2026");
    const to = screen.getByLabelText("To, income row 1");
    expect(to).toHaveValue("");
    expect(to).toHaveAttribute("placeholder", "Ongoing");
    fireEvent.change(to, { target: { value: "30/6/2027" } });
    await waitFor(() => expect(posts.some((p) => p.url === "/api/growth/preview")).toBe(true));
    expect(posts.filter((p) => p.url === "/api/growth/preview").at(-1)!.body.income).toMatchObject([{ from: "2026-01-01", to: "2027-06-30" }]);
    // a start moved past the end takes the end along
    fireEvent.change(screen.getByLabelText("From, income row 1"), { target: { value: "1 Aug 2027" } });
    expect(screen.getByLabelText("To, income row 1")).toHaveValue("1 Aug 2027");
  });

  it("starts a new income row the day after the last one ended, or this month when it runs on", async () => {
    serve();
    render(<NetWorth />);
    await openInputs();
    await screen.findByLabelText("From, income row 1");
    fireEvent.click(screen.getByRole("button", { name: "Add income" }));
    // the first row has no end, so the new one starts this month
    expect((screen.getByLabelText("From, income row 2") as HTMLInputElement).value).toMatch(/^1 [A-Z][a-z]{2} \d{4}$/);
    fireEvent.change(screen.getByLabelText("To, income row 2"), { target: { value: "30 Jun 2027" } });
    fireEvent.click(screen.getByRole("button", { name: "Add income" }));
    expect(screen.getByLabelText("From, income row 3")).toHaveValue("1 Jul 2027");
  });

  it("keeps what is typed while a save is on its way, still unsaved", async () => {
    let answer: (r: Response) => void = () => {};
    // the save answers only when the test says so
    const { posts } = serve((url, post) => (url === "/api/growth/inputs" && post
      ? new Promise<Response>((r) => { answer = r; })
      : undefined));
    render(<NetWorth />);
    await openInputs();
    const amount = await screen.findByRole("textbox", { name: "Amount a month (S$), investing step 1" });
    fireEvent.change(amount, { target: { value: "450" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(posts.some((p) => p.url === "/api/growth/inputs")).toBe(true));
    fireEvent.change(amount, { target: { value: "475" } });      // typed before the save answers
    const saved = growth();
    saved.inputs = { ...saved.inputs, investing: [{ from: null, amount_minor: 45_000 }] };
    answer(json(saved));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Saving" })).not.toBeInTheDocument());
    // G18: something typed is still unsaved, so the dialog stays open on it
    expect(dialog()).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Amount a month (S$), investing step 1" })).toHaveValue("475");
    expect(screen.getByText("Unsaved changes")).toBeInTheDocument();
  });

  it("says when the brokerage starts from nothing because there is no statement, and names no broker", async () => {
    serve((url) => (url === "/api/growth" ? json(growth({
      tiger: { as_of: null, measured: false, broker: null, start: 0, contributed: 0, rows: [
        { month: "2026-09", pessimistic: 0, expected: 0, optimistic: 0, put_in: 0 },
        { month: "2026-10", pessimistic: 30_000, expected: 30_000, optimistic: 30_000, put_in: 30_000 },
      ] },
    })) : undefined));
    render(<NetWorth />);
    await openInputs();
    expect(await screen.findByText(/No brokerage statement yet, so this starts from nothing/)).toBeInTheDocument();
    // with no statement loaded the page has no broker's name to show: not in a heading, not on the investing step
    expect(screen.getByRole("region", { name: "Investments" })).toBeInTheDocument();
    expect(await screen.findByRole("region", { name: "Investing" })).toBeInTheDocument();
    expect(screen.queryByText(/Tiger/)).not.toBeInTheDocument();
  });

  it("keeps the last projection on screen beside a refused preview", async () => {
    serve((url) => (url === "/api/growth/preview"
      ? json({ error: "Inflation is between -10 and 20 per cent a year" }, 422)
      : undefined));
    render(<NetWorth />);
    await openInputs();
    const inflation = await screen.findByRole("textbox", { name: "Inflation" });
    fireEvent.change(inflation, { target: { value: "99" } });
    // the refusal names the inflation box, so it sits under the assumptions with that box marked, not at the top of the card
    expect(await screen.findByText(/between -10 and 20/)).toBeInTheDocument();
    expect(inflation).toHaveAttribute("aria-invalid", "true");
    expect(inflation).toHaveAccessibleDescription(/between -10 and 20/);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Net worth projected" })).toBeInTheDocument();
    expect(within(dialog()!).getByText("S$30,000")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Discard" }));
    await waitFor(() => expect(screen.queryByText(/between -10 and 20/)).not.toBeInTheDocument());
    expect(screen.getByRole("textbox", { name: "Inflation" })).toHaveValue("2.5");
  });

  // G18: the switches sit on the page, away from the dialog's SaveBar, so a press saves at once
  it("saves the horizon and dollars from the page at once", async () => {
    const { posts } = stored();
    render(<NetWorth />);
    await waitFor(() => expect(fact("Horizon")).toHaveTextContent("10 years"));
    fireEvent.click(await screen.findByRole("radio", { name: "Future dollars" }));
    expect(screen.getByRole("radio", { name: "Future dollars" })).toHaveAttribute("aria-checked", "true");
    await waitFor(() => expect(posts.filter((p) => p.url === "/api/growth/inputs")).toHaveLength(1));
    expect((posts[posts.length - 1].body.projection as { dollars: string }).dollars).toBe("future");
    await waitFor(() => expect(screen.getByRole("radio", { name: "20Y" })).toBeEnabled());
    fireEvent.click(screen.getByRole("radio", { name: "20Y" }));
    await waitFor(() => expect(fact("Horizon")).toHaveTextContent("20 years"));
    const last = posts.filter((p) => p.url === "/api/growth/inputs").at(-1)!.body.projection as { horizon: string; dollars: string };
    expect(last).toMatchObject({ horizon: "20y", dollars: "future" });
    expect(posts.some((p) => p.url === "/api/growth/preview")).toBe(false);
    expect(dialog()).not.toBeInTheDocument();
    await expectFree();
  });

  it("puts a switch back when its save is refused", async () => {
    stored(growth(), "The plan could not be written");
    render(<NetWorth />);
    await waitFor(() => expect(fact("Horizon")).toHaveTextContent("10 years"));
    fireEvent.click(await screen.findByRole("radio", { name: "20Y" }));
    await waitFor(() => expect(screen.getByRole("radio", { name: "10Y" })).toHaveAttribute("aria-checked", "true"));
    expect(fact("Horizon")).toHaveTextContent("10 years");
    await expectFree();
  });

  // G18 review: each save of a switch is answered only when the test says, ok or refused
  function heldSaves() {
    let now = growth();
    const answers: ((ok: boolean) => void)[] = [];
    const { posts } = serve((url, post) => {
      if (url === "/api/growth") return json(now);
      if (url !== "/api/growth/inputs" || !post) return undefined;
      return new Promise<Response>((done) => answers.push((ok) => {
        if (!ok) return done(json({ error: "The plan could not be written" }, 422));
        now = { ...now, inputs: post.body as unknown as Growth["inputs"] };
        done(json(now));
      }));
    });
    const saves = () => posts.filter((p) => p.url === "/api/growth/inputs");
    return { saves, answer: async (ok: boolean) => act(async () => answers.shift()!(ok)), stored: () => now };
  }

  it("holds the switches while a save is on its way, so a second press cannot undo the first", async () => {
    const held = heldSaves();
    render(<NetWorth />);
    fireEvent.click(await screen.findByRole("radio", { name: "20Y" }));
    expect(screen.getByRole("radio", { name: "20Y" })).toHaveAttribute("aria-checked", "true");
    // the other switch is off until the first save answers, so nothing is sent from the old inputs
    expect(screen.getByRole("radio", { name: "Future dollars" })).toBeDisabled();
    fireEvent.click(screen.getByRole("radio", { name: "Future dollars" }));
    expect(held.saves()).toHaveLength(1);
    await held.answer(true);
    await waitFor(() => expect(screen.getByRole("radio", { name: "Future dollars" })).toBeEnabled());
    fireEvent.click(screen.getByRole("radio", { name: "Future dollars" }));
    await waitFor(() => expect(held.saves()).toHaveLength(2));
    // the second save starts from what the first one saved, plus only its own change
    expect(held.saves()[1].body.projection).toMatchObject({ horizon: "20y", dollars: "future" });
    await held.answer(true);
    await waitFor(() => expect(fact("Horizon")).toHaveTextContent("20 years"));
    expect(held.stored().inputs.projection).toMatchObject({ horizon: "20y", dollars: "future" });
    expect(screen.getByRole("radio", { name: "Future dollars" })).toHaveAttribute("aria-checked", "true");
  });

  it("puts back only the switch when its save fails, keeping what is typed in the dialog meanwhile", async () => {
    const held = heldSaves();
    render(<NetWorth />);
    fireEvent.click(await screen.findByRole("radio", { name: "20Y" }));
    // Edit still opens while the switch's save is on its way, and the dialog's Save waits for it
    await openInputs();
    const amount = screen.getByRole("textbox", { name: "Amount a month (S$), investing step 1" });
    fireEvent.change(amount, { target: { value: "450" } });
    expect(within(dialog()!).getByRole("button", { name: /^Sav/ })).toBeDisabled();
    await held.answer(false);
    expect(screen.getByRole("textbox", { name: "Amount a month (S$), investing step 1" })).toHaveValue("450");
    expect(screen.getByText("Unsaved changes")).toBeInTheDocument();
    expect(within(dialog()!).getByRole("button", { name: "Save" })).toBeEnabled();
    // the dialog's own save then sends the typing with the horizon that is still saved
    fireEvent.click(within(dialog()!).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(held.saves()).toHaveLength(2));
    expect(held.saves()[1].body.projection).toMatchObject({ horizon: "10y" });
    expect(held.saves()[1].body.investing).toEqual([{ from: null, amount_minor: 45_000 }]);
    await held.answer(true);
    await waitFor(() => expect(dialog()).not.toBeInTheDocument());
    expect(screen.getByRole("radio", { name: "10Y" })).toHaveAttribute("aria-checked", "true");
    expect(fact("Investing")).toHaveTextContent("S$450 a month");
  });

  it("carries a switch's saved change into what is being typed in the dialog", async () => {
    const held = heldSaves();
    render(<NetWorth />);
    fireEvent.click(await screen.findByRole("radio", { name: "20Y" }));
    await openInputs();
    fireEvent.change(screen.getByRole("textbox", { name: "Amount a month (S$), investing step 1" }), { target: { value: "450" } });
    await held.answer(true);
    fireEvent.click(within(dialog()!).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(held.saves()).toHaveLength(2));
    // the typing did not take the horizon back to what it was when the dialog opened
    expect(held.saves()[1].body.projection).toMatchObject({ horizon: "20y" });
    await held.answer(true);
    await waitFor(() => expect(dialog()).not.toBeInTheDocument());
    expect(fact("Horizon")).toHaveTextContent("20 years");
  });

  it("shows today, five and ten years on, and the month the milestone is first reached", async () => {
    serve();
    render(<NetWorth />);
    const tiles = within(await screen.findByRole("group", { name: "Net worth at a glance" }));
    expect(tiles.getByText("Today").parentElement).toHaveTextContent("S$30,000Oct 2026");
    expect(tiles.getByText("In 5 years").parentElement).toHaveTextContent("S$100,000Oct 2031");
    expect(tiles.getByText("In 10 years").parentElement).toHaveTextContent("Past the horizon");
    expect(tiles.getByText("S$100,000 first reached").parentElement).toHaveTextContent("Oct 2031");
  });

  it("turns a projected figure under zero red, and leaves the others ink", async () => {
    serve((url) => (url === "/api/growth" ? json(growth({
      total: { ...growth().total, tiles: { ...growth().total.tiles, in_5y: { month: "2031-10", total: -4_311_400 } } },
    })) : undefined));
    render(<NetWorth />);
    const tiles = within(await screen.findByRole("group", { name: "Net worth at a glance" }));
    expect(tiles.getByText(/S\$43,114/)).toHaveClass("text-danger");
    expect(tiles.getByText("S$30,000")).not.toHaveClass("text-danger");
  });

  it("names the months cash goes under zero", async () => {
    serve((url) => (url === "/api/growth" ? json(growth({
      total: { ...growth().total, below_zero: [{ from: "2029-03", to: "2029-06" }] },
    })) : undefined));
    render(<NetWorth />);
    expect(await screen.findByText("Cash under zero: Mar to Jun 2029")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: /cash under zero in Mar to Jun 2029/ })).toBeInTheDocument();
  });

  it("says why CPF is not projected from a statement after 55", async () => {
    serve((url) => (url.startsWith("/api/growth") ? json(growth({
      birth_date: "1960-01-01", age: 66, cpf: { as_of: "2025-12-31", needs: "before_55", rows: [] },
    })) : undefined));
    render(<NetWorth />);
    expect(await screen.findByText(/Retirement Account this projection does not read yet/)).toBeInTheDocument();
  });

  it("puts the strip of inputs first and the projection next, with no form, notes or toggles in the way", async () => {
    serve();
    render(<NetWorth />);
    const strip = await screen.findByRole("region", { name: "Your projection inputs" });
    const projected = await screen.findByRole("region", { name: "Net worth projected" });
    // G18: the strip comes before what it projects; the form waits in the dialog
    expect(strip.compareDocumentPosition(projected) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.queryAllByRole("textbox")).toHaveLength(0);
    for (const gone of ["Tiger Brokers details", "CPF details", "How cash is worked out", "Assumptions"]) {
      expect(screen.queryByRole("button", { name: gone })).not.toBeInTheDocument();
    }
    expect(screen.queryByText("Illustration, not advice")).not.toBeInTheDocument();
    expect(screen.queryByText(/Empty spends your average/)).not.toBeInTheDocument();
  });

  it("draws CPF once there is a date of birth, with the age markers", async () => {
    serve((url) => (url.startsWith("/api/growth") ? json(growth({
      birth_date: "1996-03-15", age: 30, can_reach_65: true,
      markers: [{ age: 55, month: "2051-03" }, { age: 65, month: "2061-03" }],
      cpf: { as_of: "2025-12-31", needs: null, frs: { year: 2051, month: "2051-03", value: 30_000_000, assumed: true }, rows: [
        { month: "2025-12", age: 29, oa: 100, sa: 50, ra: null, ma: 50, total: 200, frs: null },
        { month: "2026-01", age: 29, oa: 110, sa: 55, ra: null, ma: 55, total: 220, frs: null },
      ] },
    })) : undefined));
    render(<NetWorth />);
    expect(await screen.findByText("All accounts")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Your profile" })).not.toBeInTheDocument();
    expect(await screen.findByRole("radio", { name: "To 65" })).toBeEnabled();
  });
});

describe("the projected net worth tooltip", () => {
  it("gives each part and the total, with the band", () => {
    const g = growth();
    render(<TotalTip g={g} row={{ ...totalRows[1], age: 35, cpf: 500_000, total: 10_500_000, cpf_held: true }} />);
    expect(screen.getByText("Oct 2031 · age 35")).toBeInTheDocument();
    expect(screen.getByText("S$105,000")).toBeInTheDocument();
    expect(screen.getByText("Cash").parentElement).toHaveTextContent("S$40,000");
    expect(screen.getByText("Tiger Brokers").parentElement).toHaveTextContent("expected 6%S$60,000");
    expect(screen.getByText("CPF").parentElement).toHaveTextContent("held from 65S$5,000");
    expect(screen.getByText("Total S$90,000 to S$110,000 at 3% to 9%")).toBeInTheDocument();
  });

  it("says when cash is under zero", () => {
    render(<TotalTip g={growth()} row={{ ...totalRows[0], cash: -50_000, total: 1_950_000 }} />);
    expect(screen.getByText("Cash").parentElement).toHaveTextContent("under zero−S$500");
  });
});

describe("the chart views", () => {
  useSizedCharts();

  const card = (title: string) => screen.getByRole("heading", { name: title, level: 2 }).closest("section")!;
  const history = () => card("Net worth so far");
  const projected = () => card("Net worth projected");
  const areas = (el: HTMLElement) => el.querySelectorAll(".recharts-area-area").length;
  const lines = (el: HTMLElement) => el.querySelectorAll(".recharts-line-curve").length;

  it("draws net worth stacked, as one line a part, or as the total, and remembers the choice", async () => {
    serve();
    const first = render(<NetWorth />);
    await screen.findByRole("img", { name: /Net worth by month/ });
    await waitFor(() => expect(areas(history())).toBe(3));
    expect(lines(history())).toBe(0);

    fireEvent.click(within(history()).getByRole("radio", { name: "Lines" }));
    expect(areas(history())).toBe(0);
    expect(lines(history())).toBe(3);
    expect(localStorage.getItem("kopifund.chart-view.net-worth")).toBe("lines");

    fireEvent.click(within(history()).getByRole("radio", { name: "Total" }));
    expect(lines(history())).toBe(1);

    fireEvent.click(within(history()).getByRole("radio", { name: "Lines" }));
    first.unmount();
    render(<NetWorth />);
    await screen.findByRole("img", { name: /Net worth by month/ });
    expect(within(history()).getByRole("radio", { name: "Lines" })).toHaveAttribute("aria-checked", "true");
    await waitFor(() => expect(lines(history())).toBe(3));
  });

  it("keeps the projection's view apart from the history's, and the under-zero marks in every view", async () => {
    serve((url) => url.startsWith("/api/growth") ? json(growth({
      total: { ...growth().total, below_zero: [{ from: "2026-10", to: "2031-10" }] },
    })) : undefined);
    render(<NetWorth />);
    await screen.findByRole("img", { name: /Net worth projected/ });
    expect(areas(projected())).toBe(3);   // the parts and the band round the total
    fireEvent.click(within(projected()).getByRole("radio", { name: "Lines" }));
    expect(areas(projected())).toBe(0);
    expect(lines(projected())).toBe(2);
    expect(areas(history())).toBe(3);
    fireEvent.click(within(projected()).getByRole("radio", { name: "Total" }));
    expect(lines(projected())).toBe(1);
    expect(areas(projected())).toBe(1);   // the band stays
    expect(within(projected()).getByText(/Cash under zero: Oct 2026 to Oct 2031/)).toBeInTheDocument();
    expect(projected().querySelectorAll(".recharts-reference-area").length).toBe(1);
  });

  it("draws the whole total with its band on Total, whatever a part was hidden on the way", async () => {
    serve();
    render(<NetWorth />);
    await screen.findByRole("img", { name: /Net worth projected/ });
    fireEvent.click(within(projected()).getByRole("button", { name: "Cash" }));
    await waitFor(() => expect(areas(projected())).toBe(1));   // one part left, and no band while a part is off
    fireEvent.click(within(projected()).getByRole("radio", { name: "Total" }));
    await waitFor(() => expect(areas(projected())).toBe(1));   // the band
    expect(within(projected()).getByText(/Tiger Brokers at 3% to 9%/)).toBeInTheDocument();
    expect(lines(projected())).toBe(1);
    // back on Stacked the hidden part is still hidden
    fireEvent.click(within(projected()).getByRole("radio", { name: "Stacked" }));
    expect(within(projected()).getByRole("button", { name: "Cash" })).toHaveAttribute("aria-pressed", "false");
  });

  it("hides a part from its legend entry, and keeps the last one showing", async () => {
    serve();
    render(<NetWorth />);
    await screen.findByRole("img", { name: /Net worth by month/ });
    const legend = (name: string) => within(history()).getByRole("button", { name });
    fireEvent.click(legend("Cash"));
    expect(legend("Cash")).toHaveAttribute("aria-pressed", "false");
    expect(areas(history())).toBe(2);
    fireEvent.click(legend("Investments"));
    expect(areas(history())).toBe(1);
    expect(legend("CPF")).toBeDisabled();   // the last part cannot be hidden
    fireEvent.click(legend("Cash"));
    expect(areas(history())).toBe(2);
  });
});

describe("the projected tooltip with a part hidden", () => {
  it("lists and adds up the parts showing", () => {
    render(<TotalTip g={growth()} row={{ ...totalRows[1], age: null }} hide={new Set(["cash"])} />);
    expect(screen.queryByText("Cash")).not.toBeInTheDocument();
    expect(screen.getByText("Tiger Brokers")).toBeInTheDocument();
    expect(screen.getAllByText("S$60,000")).toHaveLength(2);   // the part and the sum of the parts showing
    expect(screen.queryByText(/at 3% to 9%/)).not.toBeInTheDocument();
  });

  it("orders the parts by size on the lines", () => {
    render(<TotalTip g={growth()} row={{ ...totalRows[1], age: null, cash: 3_000_000, tiger: 7_000_000 }} lines />);
    expect(screen.getAllByText(/^(Cash|Tiger Brokers)$/).map((n) => n.textContent)).toEqual(["Tiger Brokers", "Cash"]);
  });
});

describe("the inputs card", () => {
  it("names the broker on the investing step only from the loaded statement", async () => {
    serve();
    const first = render(<NetWorth />);
    await openInputs();
    expect(await screen.findByRole("region", { name: "Investing into Tiger Brokers" })).toBeInTheDocument();
    first.unmount();
    serve((url) => (url === "/api/growth" ? json(growth({ tiger: null })) : undefined));
    render(<NetWorth />);
    await openInputs();
    expect(await screen.findByRole("region", { name: "Investing" })).toBeInTheDocument();
    expect(screen.queryByText(/Tiger/)).not.toBeInTheDocument();
  });

  it("calls the bonus columns Bonus and Paid in, and lets Side income show whole", async () => {
    serve();
    render(<NetWorth />);
    await openInputs();
    await screen.findByRole("textbox", { name: "Name, income row 1" });
    expect(screen.getByRole("columnheader", { name: "Bonus" })).toBeInTheDocument();
    expect(screen.queryByText(/Bonus \(months, in\)/)).not.toBeInTheDocument();
    // G18: the app's Select, so the options are listed once the box opens
    fireEvent.click(screen.getByLabelText("Bonus month, income row 1"));
    expect(screen.getAllByRole("option")[0]).toHaveTextContent("Paid in");
    fireEvent.keyDown(screen.getByRole("listbox"), { key: "Escape" });
    expect(screen.getByLabelText("Bonus (months of pay), income row 1")).toHaveAttribute("placeholder", "Months");
    fireEvent.click(screen.getByLabelText("Type, income row 1"));
    expect(screen.getByRole("option", { name: "Side income" })).toBeInTheDocument();
  });

  it("marks a figure that cannot be a number, with the message under that box, the moment it is typed", async () => {
    serve();
    render(<NetWorth />);
    await openInputs();
    const gross = await screen.findByRole("textbox", { name: "Gross a month (S$), income row 1" });
    // the line for what is wrong is there before anything is, so nothing below it moves when a message comes
    const row = screen.getByRole("row", { name: "Income row 1" });
    const line = row.nextElementSibling!.querySelector("p")!;
    expect(line).toHaveTextContent("");
    expect(gross).not.toHaveAttribute("aria-invalid");
    fireEvent.change(gross, { target: { value: "1k5" } });
    expect(gross).toHaveAttribute("aria-invalid", "true");
    expect(gross.parentElement).toHaveClass("border-danger");
    expect(line).toHaveTextContent("Gross a month must be a number, like 6,000.50");
    expect(gross).toHaveAccessibleDescription("Gross a month must be a number, like 6,000.50");
    // nothing at the top of the card
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    fireEvent.change(gross, { target: { value: "1,500" } });
    expect(gross).not.toHaveAttribute("aria-invalid");
    expect(line).toHaveTextContent("");
  });

  it("lets a rate go under zero, and marks only text that is not a number", async () => {
    serve();
    render(<NetWorth />);
    await openInputs();
    const pessimistic = await screen.findByRole("textbox", { name: "Pessimistic return" });
    fireEvent.change(pessimistic, { target: { value: "-5" } });
    expect(pessimistic).not.toHaveAttribute("aria-invalid");
    fireEvent.change(pessimistic, { target: { value: "5-" } });
    expect(pessimistic).toHaveAttribute("aria-invalid", "true");
    expect(pessimistic).toHaveAccessibleDescription("Pessimistic return must be a number, like 6.5");
    // an amount cannot be under zero
    const gross = screen.getByRole("textbox", { name: "Gross a month (S$), income row 1" });
    fireEvent.change(gross, { target: { value: "-1" } });
    expect(gross).toHaveAttribute("aria-invalid", "true");
    // G17: nor finer than a cent, which cents cannot hold
    fireEvent.change(gross, { target: { value: "6000.505" } });
    expect(gross).toHaveAttribute("aria-invalid", "true");
    expect(gross).toHaveAccessibleDescription("Gross a month is in dollars and cents, no finer");
  });

  it("puts a refusal about a row under that row, not at the top of the card", async () => {
    serve((url) => (url === "/api/growth/preview"
      ? json({ error: "Income row 1 (Invented job): the to date is before the from date" }, 422)
      : undefined));
    render(<NetWorth />);
    await openInputs();
    const to = await screen.findByLabelText("To, income row 1");
    fireEvent.change(to, { target: { value: "30 Jun 2025" } });
    const row = screen.getByRole("row", { name: "Income row 1" });
    await waitFor(() => expect(row.nextElementSibling).toHaveTextContent("The to date is before the from date"));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("keeps the save in view in the dialog's foot, and an amount's keypad is the decimal one", async () => {
    serve();
    render(<NetWorth />);
    const sheet = await openInputs();
    await screen.findByRole("textbox", { name: "Name, income row 1" });
    // G18: Saved or Unsaved changes, Discard and Save sit in the dialog's foot, outside the part that scrolls
    const save = within(sheet).getByRole("button", { name: "Save" });
    const gross = screen.getByRole("textbox", { name: "Gross a month (S$), income row 1" });
    expect(gross.closest(".overflow-y-auto")).not.toBeNull();
    expect(save.closest(".overflow-y-auto")).toBeNull();
    expect(within(sheet).getByRole("button", { name: "Discard" })).toBeInTheDocument();
    expect(gross).toHaveAttribute("inputmode", "decimal");
    expect(gross).toHaveAttribute("enterkeyhint", "next");
  });

  it("is a stack of cards under 1280px, with no table wide enough to scroll the page sideways", async () => {
    serve();
    render(<NetWorth />);
    await openInputs();
    const table = (await screen.findByRole("row", { name: "Income row 1" })).closest("table")!;
    expect(table).toHaveClass("max-xl:block");
    expect(table.className).not.toMatch(/min-w-\[/);
    expect(table.parentElement).not.toHaveClass("overflow-x-auto");
    expect(screen.getByRole("row", { name: "Income row 1" })).toHaveClass("max-xl:grid");
  });
});

// G13: what the record already knows, as chips beside the inputs. Every name and figure is invented.
describe("suggestion chips on the inputs", () => {
  const known = (over: Partial<Suggestions> = {}): Suggestions => ({
    income: [{ name: "Invented Co", type: "employment", from: "2025-03-14", last: "2026-09-28", take_home: 400_000, gross: 500_000,
      cpf: true, basis: "gross", bonus_months: 2, bonus_month: 12 }],
    investing: { per_month: 100_000, months: 12, from: "2025-09", to: "2026-08" },
    cpf_employer_rate: 0.17, insurance: null, repayments: null, ...over,
  });
  const saved = (income: Growth["inputs"]["income"], investing: Growth["inputs"]["investing"], spend: number | null): Growth => {
    const g = growth();
    return { ...g, inputs: { ...g.inputs, income, investing, projection: { ...g.inputs.projection, spend_minor: spend } } };
  };
  const row = (over: Partial<Growth["inputs"]["income"][number]> = {}) => ({ ...growth().inputs.income[0], name: "Invented Co", ...over });
  const withKnown = (suggestions: Suggestions, g: Growth) => serve((url) => {
    if (url.startsWith("/api/suggestions")) return json(suggestions);
    if (url === "/api/growth") return json(g);
    return undefined;
  });
  const chips = () => screen.queryAllByRole("button", { name: /^Add Invented|^Seen |^Record shows / });

  it("offers pay seen in the statements, and fills nothing until the chip is tapped", async () => {
    withKnown(known(), saved([], [], 250_000));
    render(<NetWorth />);
    await openInputs();
    const chip = await screen.findByRole("button", { name: "Add Invented Co, about S$5,000 a month gross (S$4,000 take-home), from Mar 2025, bonus about 2 months in Dec" });
    expect(screen.queryAllByRole("row", { name: /^Income row \d+$/ })).toHaveLength(0);
    expect(screen.queryByText("Unsaved changes")).not.toBeInTheDocument();
    fireEvent.click(chip);
    expect(rowsOf()).toHaveLength(1);
    expect(screen.getByRole("textbox", { name: "Name, income row 1" })).toHaveValue("Invented Co");
    expect(screen.getByRole("textbox", { name: "Gross a month (S$), income row 1" })).toHaveValue("5,000");
    expect(screen.getByLabelText("From, income row 1")).toHaveValue("14 Mar 2025");
    expect(screen.getByLabelText("Pays CPF, income row 1")).toBeChecked();
    expect(screen.getByRole("textbox", { name: "Bonus (months of pay), income row 1" })).toHaveValue("2");
    expect(screen.getByLabelText("Bonus month, income row 1")).toHaveTextContent("Dec");
    expect(screen.getByRole("status")).toHaveTextContent("Unsaved changes");
    // the row now stands for it, so the offer goes
    expect(screen.queryByRole("button", { name: /^Add Invented Co/ })).not.toBeInTheDocument();
  });

  it("says take-home when that is all the statement shows, and the row then pays no CPF", async () => {
    withKnown(known({ income: [{ name: "Invented Co", type: "employment", from: "2025-03-14", last: "2026-09-28", take_home: 400_000,
      gross: 400_000, cpf: false, basis: "take_home", bonus_months: null, bonus_month: null }] }), saved([], [], 250_000));
    render(<NetWorth />);
    await openInputs();
    fireEvent.click(await screen.findByRole("button", { name: "Add Invented Co, S$4,000 a month take-home, from Mar 2025" }));
    expect(screen.getByRole("textbox", { name: "Gross a month (S$), income row 1" })).toHaveValue("4,000");
    expect(screen.getByLabelText("Pays CPF, income row 1")).not.toBeChecked();
  });

  it("shows no chip for a box that is close to the record, or for pay a row already stands for", async () => {
    // 5,000 is the detected gross, 300 is the step, 310 what was deposited, 2,600 the typed spending against 2,500 seen
    withKnown(known({ investing: { per_month: 31_000, months: 12, from: "2025-09", to: "2026-08" } }),
      saved([row({ name: "My job" })], [{ from: null, amount_minor: 30_000 }], 260_000));
    render(<NetWorth />);
    await openInputs();
    await screen.findByRole("textbox", { name: "Name, income row 1" });
    expect(chips()).toHaveLength(0);
  });

  it("says where the record clearly disagrees with a filled box, and fills it on a tap", async () => {
    withKnown(known(), saved([row({ gross_minor: 300_000 })], [{ from: null, amount_minor: 30_000 }], 250_000));
    render(<NetWorth />);
    await openInputs();
    const gross = await screen.findByRole("textbox", { name: "Gross a month (S$), income row 1" });
    expect(gross).toHaveValue("3,000");
    fireEvent.click(screen.getByRole("button", { name: "Record shows S$5,000 a month gross (S$4,000 take-home), use" }));
    expect(gross).toHaveValue("5,000");
    expect(screen.queryByRole("button", { name: /^Record shows S\$5,000/ })).not.toBeInTheDocument();
    // the step reads 300 where the deposits say 1,000
    const amount = screen.getByRole("textbox", { name: "Amount a month (S$), investing step 1" });
    expect(amount).toHaveValue("300");
    fireEvent.click(screen.getByRole("button", { name: "Record shows S$1,000 a month, use" }));
    expect(amount).toHaveValue("1,000");
    expect(chips()).toHaveLength(0);
  });

  it("offers a step when there is none, and the spending average while the box is empty", async () => {
    withKnown(known({ income: [] }), saved([], [], null));
    render(<NetWorth />);
    await openInputs();
    const spend = await screen.findByRole("textbox", { name: "Spending a month (S$)" });
    expect(spend).toHaveValue("");
    fireEvent.click(await screen.findByRole("button", { name: "Seen S$2,500 a month over the last 12 months, use" }));
    expect(spend).toHaveValue("2,500");
    expect(screen.queryByRole("button", { name: /^Seen S\$2,500/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: /investing step/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Seen S$1,000 a month, add as a step" }));
    expect(screen.getByRole("textbox", { name: "Amount a month (S$), investing step 1" })).toHaveValue("1,000");
  });

  it("shows no income or investing chip when the record has nothing to suggest, or the ask fails", async () => {
    serve();            // /api/suggestions is a 404 here
    render(<NetWorth />);
    await openInputs();
    await screen.findByRole("textbox", { name: "Name, income row 1" });
    expect(screen.queryByRole("group", { name: "Pay seen in your statements" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /add as a step$/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Record shows / })).not.toBeInTheDocument();
  });
});

// G18: the saved inputs as one line at the top, and Edit opening them in a dialog
describe("the inputs strip and its dialog", () => {
  it("sums the pay running today, takes the step in force, and shows the saved figures", async () => {
    const g = growth();
    const job = g.inputs.income[0];
    stored({ ...g, inputs: {
      income: [
        { ...job, name: "Old job", from: "2024-01-01", to: "2025-12-31", gross_minor: 900_000 },
        { ...job, name: "Main job", from: "2026-01-01", to: null, gross_minor: 500_000 },
        { ...job, name: "Tutoring", type: "side", cpf: false, from: "2026-03-01", to: "2026-10-12", gross_minor: 120_050 },
        { ...job, name: "Next job", from: "2027-01-01", to: null, gross_minor: 800_000 },
      ],
      investing: [{ from: "2026-06", amount_minor: 45_000 }, { from: null, amount_minor: 30_000 }, { from: "2027-01", amount_minor: 90_000 }],
      projection: { ...g.inputs.projection, spend_minor: 212_300, expected_pct: "6.5", horizon: "65" },
    } });
    render(<NetWorth />);
    await waitFor(() => expect(fact("Pay")).toHaveTextContent("S$6,201 a month"));
    expect(fact("Pay")).toHaveClass("num");
    expect(fact("Investing")).toHaveTextContent("S$450 a month");
    expect(fact("Spending")).toHaveTextContent(/^S\$2,123 a month$/);
    expect(fact("Return")).toHaveTextContent("6.5% a year");
    expect(fact("Horizon")).toHaveTextContent("To 65");
    // nothing to type on the page itself
    expect(screen.queryAllByRole("textbox")).toHaveLength(0);
  });

  it("says None with no pay or step in force, and the record's average for empty spending", async () => {
    const g = growth();
    stored({ ...g, inputs: { ...g.inputs, income: [], investing: [{ from: "2027-01", amount_minor: 90_000 }] } });
    render(<NetWorth />);
    await waitFor(() => expect(fact("Pay")).toHaveTextContent(/^None$/));
    expect(fact("Investing")).toHaveTextContent(/^None$/);
    expect(fact("Spending")).toHaveTextContent("S$2,500 a month, your average");
    expect(fact("Return")).toHaveTextContent("6% a year");
    expect(fact("Horizon")).toHaveTextContent("10 years");
  });

  it("opens the inputs in a dialog with the tiles and the save in it", async () => {
    serve();
    render(<NetWorth />);
    expect(dialog()).not.toBeInTheDocument();
    const sheet = within(await openInputs());
    expect(sheet.getByRole("textbox", { name: "Name, income row 1" })).toHaveValue("Invented job");
    expect(sheet.getByRole("group", { name: "Net worth at a glance" })).toBeInTheDocument();
    expect(sheet.getByText("Saved")).toBeInTheDocument();
    expect(sheet.getByRole("button", { name: "Save" })).toBeDisabled();
  });

  it("asks before closing with edits; Keep editing stays, Leave drops them", async () => {
    stored();
    render(<NetWorth />);
    await openInputs();
    fireEvent.change(screen.getByRole("textbox", { name: "Amount a month (S$), investing step 1" }), { target: { value: "999" } });
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(within(dialog()!).getByText("Leave without saving?")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Keep editing" }));
    expect(dialog()).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Amount a month (S$), investing step 1" })).toHaveValue("999");
    fireEvent.click(screen.getByTestId("sheet-scrim"));
    fireEvent.click(screen.getByRole("button", { name: "Leave" }));
    expect(dialog()).not.toBeInTheDocument();
    await expectFree();
    expect(fact("Investing")).toHaveTextContent("S$300 a month");
    // opened again, the dialog holds what is saved, not what was left behind
    await openInputs();
    expect(screen.getByRole("textbox", { name: "Amount a month (S$), investing step 1" })).toHaveValue("300");
    expect(screen.getByText("Saved")).toBeInTheDocument();
  });

  it("closes at once with nothing typed", async () => {
    serve();
    render(<NetWorth />);
    await openInputs();
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(dialog()).not.toBeInTheDocument();
  });

  it("keeps the dialog open with the reason when a save fails", async () => {
    stored(growth(), "The plan could not be written");
    render(<NetWorth />);
    await openInputs();
    fireEvent.change(screen.getByRole("textbox", { name: "Amount a month (S$), investing step 1" }), { target: { value: "450" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await within(dialog()!).findByRole("alert")).toHaveTextContent("The plan could not be written");
    expect(screen.getByText("Unsaved changes")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Amount a month (S$), investing step 1" })).toHaveValue("450");
    expect(fact("Investing")).toHaveTextContent("S$300 a month");
    // the next edit takes the reason away
    fireEvent.change(screen.getByRole("textbox", { name: "Amount a month (S$), investing step 1" }), { target: { value: "460" } });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("leaves an Escape in a date field's calendar to the calendar, and the next one closes the dialog", async () => {
    serve();
    const user = userEvent.setup();
    render(<NetWorth />);
    await user.click(await screen.findByRole("button", { name: "Edit" }));
    await user.click(screen.getByRole("textbox", { name: "To, income row 1" }));
    expect(screen.getByRole("dialog", { name: "To, income row 1 calendar" })).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "To, income row 1 calendar" })).not.toBeInTheDocument();
    expect(dialog()).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(dialog()).not.toBeInTheDocument();
  });
});
