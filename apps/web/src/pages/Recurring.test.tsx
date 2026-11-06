import "@testing-library/jest-dom/vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { choose } from "../test-select";
import { notifyChanged } from "../api";
import type { ActiveSeries, ComingUpRow, RecurringPage, SeriesView } from "../api";
import { ComingUpItem, DotStrip, mergeSeries, Recurring, stepOn } from "./Recurring";
import { leadingIcon } from "../test-icons";
import { expectAsks, expectFree, json, settle, stubMatchMedia } from "../test-helpers";

// invented payees and amounts
// G16: a row's series id and name follow its payee unless the test says otherwise
const up = (over: Partial<ComingUpRow>): ComingUpRow => ({
  id: over.payee ?? "Gym", name: over.payee ?? "Gym", payee: "Gym", link: { key: "k-gym", hide_transfers: "0" }, cadence: "monthly", category: "Fitness", investing: false, amount: 5000,
  expected: "2026-09-05", estimated: true, status: "paid", paid_on: "2026-09-04", paid_amount: 5000,
  usual_low: 3750, usual_high: 6250, ...over,
});
const PAGE: RecurringPage = {
  ready: true, as_of: "2026-09-20", generated_at: "2026-09-20T08:00:00",
  coming_up: {
    month: "2026-09", as_of: "2026-09-20",
    rows: [
      up({ payee: "Landlord", link: { key: "k-rent", hide_transfers: "0" }, category: "Rent", amount: 100000, expected: "2026-09-01",
        status: "different", paid_on: "2026-09-01", paid_amount: 130000 }),
      up({ payee: "Phone", link: null, category: "Bills", amount: 2000, expected: "2026-09-02",
        status: "late", paid_on: null, paid_amount: null }),
      up({}),
      up({ payee: "Broker", link: { key: "k-broker", hide_transfers: "0" }, category: "Investments", investing: true, amount: 50000,
        expected: "2026-09-10", paid_on: "2026-09-10", paid_amount: 50000 }),
      up({ payee: "Stream", link: null, category: "Fun", amount: 1500, expected: "2026-09-25",
        status: "due", paid_on: null, paid_amount: null }),
    ],
    totals: { committed: 138500, paid: 135000, investing: 50000, investing_paid: 50000, extra: 0, investing_extra: 0 },
  },
  active: [
    { id: "Gym", name: "Gym", payee: "Gym", link: { q: "Gym" }, cadence: "monthly", category: "Fitness", investing: false, amount: 5000,
      usual_low: 3750, usual_high: 6250, amount_varies: false, occurrences: 7, last_paid: "2026-09-04",
      next_expected: "2026-10-04", late: false, state: "active", yearly: 60000, flagged: true },
    { id: "Phone", name: "Phone", payee: "Phone", link: { q: "Phone" }, cadence: "monthly", category: "Bills", investing: false, amount: 2000,
      usual_low: 1500, usual_high: 2500, amount_varies: false, occurrences: 5, last_paid: "2026-08-02",
      next_expected: "2026-09-02", late: true, state: "active", yearly: 24000, flagged: true },
    { id: "Broker", name: "Broker", payee: "Broker", link: { q: "Broker", hide_transfers: "0" }, cadence: "monthly", category: "Investments", investing: true, amount: 50000,
      usual_low: 37500, usual_high: 62500, amount_varies: false, occurrences: 12, last_paid: "2026-09-10",
      next_expected: "2026-10-10", late: false, state: "active", yearly: 600000, flagged: true },
    { id: "Landlord", name: "Landlord", payee: "Landlord", link: { q: "Landlord" }, cadence: "monthly", category: "Rent", investing: false, amount: 100000,
      usual_low: 75000, usual_high: 125000, amount_varies: false, occurrences: 12, last_paid: "2026-09-01",
      next_expected: "2026-10-01", late: false, state: "active", yearly: 1200000, flagged: true },
    { id: "Stream", name: "Stream", payee: "Stream", link: { q: "Stream" }, cadence: "monthly", category: "Fun", investing: false, amount: 1500,
      usual_low: 1125, usual_high: 1875, amount_varies: false, occurrences: 9, last_paid: "2026-08-25",
      next_expected: "2026-09-25", late: false, state: "active", yearly: 18000, flagged: true },
  ],
  paused: [],
  stopped: [
    { id: "(no payee, about SGD 18)", name: "(no payee, about SGD 18)", payee: "(no payee, about SGD 18)", link: { month: "2025-02", category: "(none)" }, cadence: "monthly",
      category: "(uncategorised)", investing: false, amount: 1800, first_seen: "2024-12-01", last_seen: "2025-02-01",
      ended_on: null, occurrences: 3, yearly: 21600 },
  ],
  irregular: [
    { payee: "Magazine", link: { q: "Magazine", hide_transfers: "0" }, payments: 3, first: "2026-01-03", last: "2026-04-20",
      dates: ["2026-01-03", "2026-04-11", "2026-04-20"], total: 2700 },
  ],
  not_recurring: [],
  totals: { yearly_spending: 1302000, yearly_investing: 600000, active: 5, paused: 0, stopped: 1 },
};

function stubFetch(page: RecurringPage = PAGE) {
  const urls: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    urls.push(url);
    return json(page);
  }));
  return urls;
}

const section = (name: string) => screen.getByRole("region", { name });

const href = (el: HTMLElement) => el.closest("a")?.getAttribute("href");

const rowsOf = (name = "Active") => within(section(name)).getAllByRole("listitem");
const nameOf = (li: HTMLElement) => within(li).getAllByRole("link")[0].textContent;

describe("Recurring", () => {
  it("lists each payee once, soonest next payment first, with this month's status", async () => {
    stubFetch();
    render(<Recurring />);
    await screen.findByText("Paid in Sep 2026");
    // Coming up and Active were the same payees twice: there is one list now
    expect(screen.queryByRole("region", { name: /Coming up/ })).not.toBeInTheDocument();
    const items = rowsOf();
    expect(items.map(nameOf)).toEqual(["Phone", "Stream", "Landlord", "Gym", "Broker"]);
    expect(within(items[0]).getByText("Late")).toBeInTheDocument();
    expect(within(items[1]).getByText("Due")).toBeInTheDocument();
    expect(within(items[2]).getByText("Different amount")).toBeInTheDocument();
    expect(within(items[3]).getByText("Paid")).toBeInTheDocument();
    expect(within(items[4]).getByText("Paid")).toBeInTheDocument();
    // the next expected day, with a ~ since it is an estimate, and no year inside the record's own
    expect(within(items[0]).getByText("~2 Sep")).toBeInTheDocument();
    expect(within(items[0]).getByTitle("Expected 2 Sep 2026")).toBeInTheDocument();
    expect(within(items[3]).getByText("~4 Oct")).toBeInTheDocument();
    // cadence and category under the name; a paid one says the day it came in
    expect(items[3]).toHaveTextContent("Monthly · Fitness · paid 4 Sep");
    expect(items[0]).toHaveTextContent("Monthly · Bills");
    expect(within(items[4]).getByText("Investing")).toBeInTheDocument();
  });

  it("shows what a different amount cost and what is usual, and the typical amount otherwise", async () => {
    stubFetch();
    render(<Recurring />);
    await screen.findByText("Paid in Sep 2026");
    const items = rowsOf();
    expect(within(items[2]).getByText("S$1,300.00")).toBeInTheDocument();
    expect(within(items[2]).getByText("usual S$1,000.00")).toBeInTheDocument();
    expect(within(items[3]).getByText("S$50.00")).toBeInTheDocument();
    expect(within(items[3]).queryByText(/usual/)).not.toBeInTheDocument();
  });

  it("keeps the year on a next payment that falls in another year", async () => {
    const next = { ...PAGE.active[0], id: "Yearly dues", name: "Yearly dues", payee: "Yearly dues", cadence: "yearly" as const, amount: 24000, yearly: 24000,
      last_paid: "2025-12-20", next_expected: "2026-12-20" };
    const far = { ...PAGE.active[0], id: "Far off", name: "Far off", payee: "Far off", next_expected: "2027-01-04" };
    stubFetch({ ...PAGE, active: [next, far] });
    render(<Recurring />);
    await screen.findByText("Paid in Sep 2026");
    expect(within(rowsOf().find((li) => nameOf(li) === "Far off")!).getByText("~4 Jan 2027")).toBeInTheDocument();
    expect(within(rowsOf().find((li) => nameOf(li) === "Yearly dues")!).getByText("~20 Dec")).toBeInTheDocument();
  });

  it("draws each year's cost as a bar on one scale for the whole list, the dearest full", async () => {
    stubFetch();
    render(<Recurring />);
    await screen.findByText("Paid in Sep 2026");
    const share = (li: HTMLElement) => li.querySelector("[data-cost-bar]")?.getAttribute("data-share");
    const items = rowsOf();
    // Landlord is S$12,000 a year, the scale's end; Phone is S$240 of it
    expect(share(items[2])).toBe("1.0000");
    expect(share(items[0])).toBe("0.0200");
    expect(share(items[3])).toBe("0.0500");
    expect(within(items[2]).getByRole("img", { name: "S$12,000 a year" })).toBeInTheDocument();
    expect(within(items[2]).getAllByText("S$12,000").length).toBeGreaterThan(0);
    // investing is saving, not a cost: its figure is there, a bar that would dwarf the rest is not
    expect(share(items[4])).toBeUndefined();
    expect(within(items[4]).getByText("S$6,000")).toBeInTheDocument();
    expect(within(section("Active")).getByText("S$13,020 a year in spending · S$6,000 investing")).toBeInTheDocument();
  });

  it("gives a series with nothing expected this month a quiet Later chip, and a late one its late chip", () => {
    const quarterly: ActiveSeries = { ...PAGE.active[0], id: "Quarterly", name: "Quarterly", payee: "Quarterly", cadence: "quarterly", yearly: 20000, next_expected: "2026-12-04" };
    const overdue: ActiveSeries = { ...PAGE.active[0], id: "Overdue", name: "Overdue", payee: "Overdue", late: true, next_expected: "2026-08-01" };
    const merged = mergeSeries([quarterly, overdue], []);
    expect(merged.map((m) => [m.payee, m.status])).toEqual([["Overdue", "late"], ["Quarterly", null]]);
  });

  it("never lets a payment due this month hide that the series is late on an earlier one, G16", () => {
    const missed: ActiveSeries = { ...PAGE.active[0], id: "Missed", name: "Missed", payee: "Missed", late: true, next_expected: "2026-08-28" };
    const due = up({ payee: "Missed", expected: "2026-09-28", status: "due", paid_on: null, paid_amount: null, link: null });
    const merged = mergeSeries([missed], [due])[0];
    expect([merged.status, merged.month_link, merged.next_expected]).toEqual(["late", null, "2026-09-28"]);
  });

  it("names an extra charge with its amount on the chip, which opens that charge, G16", async () => {
    stubMatchMedia(true);
    const extra = up({ expected: "2026-09-20", estimated: false, status: "extra", paid_on: "2026-09-20", paid_amount: 2100,
      link: { key: "k-extra", hide_transfers: "0" } });
    const merged = mergeSeries([PAGE.active[0]], [up({}), extra])[0];
    expect([merged.status, merged.paid_amount, merged.month_link]).toEqual(["extra", 2100, { key: "k-extra", hide_transfers: "0" }]);
    // the next payment is the series' own, not a month after the extra charge
    expect(merged.next_expected).toBe("2026-10-04");
    const page: RecurringPage = { ...PAGE, coming_up: { ...PAGE.coming_up, rows: [...PAGE.coming_up.rows, extra] } };
    stubFetch(page);
    render(<Recurring />);
    const chip = await screen.findByRole("link", { name: "Extra S$21.00: an extra charge this month from Gym" });
    expect(chip.getAttribute("href")).toBe("/transactions?key=k-extra&hide_transfers=0");
    // G18: the chip leads with the extra charge's own icon
    expect(leadingIcon(within(chip).getByText("Extra S$21.00"))).toBe("circle-plus");
    // and so does Home's Coming up row, as an icon alone on a phone
    stubMatchMedia(false);
    const { unmount } = render(<ul><ComingUpItem r={extra} /></ul>);
    expect(screen.getByRole("img", { name: "Extra" }).querySelector("svg")?.getAttribute("class")).toContain("lucide-circle-plus");
    unmount();
    stubMatchMedia(true);
    // the month's bills leave it out: 2 of 4 paid, not 3 of 5
    expect(screen.getByText(/of S\$1,385 committed, 2 of 4 payments/)).toBeInTheDocument();
  });

  it("shows an extra charge on Home's Coming up as its own row, G16", () => {
    stubMatchMedia(true);
    render(<ul><ComingUpItem r={up({ expected: "2026-09-20", estimated: false, status: "extra", paid_on: "2026-09-20", paid_amount: 2100 })} /></ul>);
    expect(screen.getByText("Extra")).toBeInTheDocument();
    expect(screen.getByText("S$21.00")).toBeInTheDocument();
    expect(screen.getByText("20 Sep 2026")).toBeInTheDocument();
  });

  it("speaks for a weekly series by its most pressing payment this month", () => {
    const weekly: ActiveSeries = { ...PAGE.active[0], id: "Weekly", name: "Weekly", payee: "Weekly", cadence: "weekly", yearly: 5200 };
    const row = (expected: string, status: ComingUpRow["status"]) =>
      up({ payee: "Weekly", cadence: "weekly", category: "Fitness", amount: 5000, expected, status,
        paid_on: status === "paid" ? expected : null, paid_amount: status === "paid" ? 5000 : null });
    expect(mergeSeries([weekly], [row("2026-09-07", "paid"), row("2026-09-14", "due"), row("2026-09-21", "late")])[0].status).toBe("late");
    expect(mergeSeries([weekly], [row("2026-09-07", "paid"), row("2026-09-14", "paid")])[0].paid_on).toBe("2026-09-14");
    expect(mergeSeries([weekly], [row("2026-09-07", "different"), row("2026-09-14", "due")])[0].status).toBe("different");
  });

  it("dates an overdue weekly payment by the week it missed, so it leads the list", () => {
    const weekly: ActiveSeries = { ...PAGE.active[0], id: "Weekly", name: "Weekly", payee: "Weekly", cadence: "weekly", yearly: 5200, next_expected: "2026-09-26" };
    const other: ActiveSeries = { ...PAGE.active[0], id: "Other", name: "Other", payee: "Other", next_expected: "2026-09-22" };
    const row = (expected: string, status: ComingUpRow["status"]) =>
      up({ payee: "Weekly", cadence: "weekly", category: "Fitness", amount: 5000, expected, status,
        paid_on: status === "paid" ? expected : null, paid_amount: status === "paid" ? 5000 : null });
    const merged = mergeSeries([other, weekly], [row("2026-09-12", "late"), row("2026-09-19", "paid")]);
    expect(merged.map((m) => [m.payee, m.status, m.next_expected])).toEqual([["Weekly", "late", "2026-09-12"], ["Other", null, "2026-09-22"]]);
    // a week still to come is the next one too; a paid series keeps the day after its last payment
    expect(mergeSeries([weekly], [row("2026-09-19", "paid"), row("2026-09-26", "due")])[0].next_expected).toBe("2026-09-26");
    expect(mergeSeries([weekly], [row("2026-09-19", "paid")])[0].next_expected).toBe("2026-09-26");
  });

  it("dates the next payment by the next unpaid week even when a different amount wins the chip", () => {
    const weekly: ActiveSeries = { ...PAGE.active[0], id: "Weekly", name: "Weekly", payee: "Weekly", cadence: "weekly", yearly: 5200,
      last_paid: "2026-08-31", next_expected: "2026-09-07" };
    const row = (expected: string, status: ComingUpRow["status"]) =>
      up({ payee: "Weekly", cadence: "weekly", category: "Fitness", amount: 5000, expected, status,
        paid_on: status === "due" ? null : expected, paid_amount: status === "due" ? null : 7000 });
    // paid 7 Sep at an odd amount, 14 Sep still to come: the chip is Different amount, the next payment 14 Sep
    const both = mergeSeries([weekly], [row("2026-09-07", "different"), row("2026-09-14", "due")])[0];
    expect([both.status, both.next_expected]).toEqual(["different", "2026-09-14"]);
    // the month's last week paid at an odd amount and nothing after it this month: one cadence on from it
    const last = mergeSeries([weekly], [row("2026-09-07", "different")])[0];
    expect([last.status, last.next_expected]).toEqual(["different", "2026-09-14"]);
  });

  it("steps one cadence on: seven days, or whole months with the 31st falling back to the month's last day", () => {
    expect(stepOn("2026-09-28", "weekly")).toBe("2026-10-05");
    expect(stepOn("2026-01-31", "monthly")).toBe("2026-02-28");
    expect(stepOn("2026-11-30", "quarterly")).toBe("2027-02-28");
    expect(stepOn("2028-02-29", "yearly")).toBe("2029-02-28");
    expect(stepOn("2026-12-15", "monthly")).toBe("2027-01-15");
    expect(stepOn("2026-09-28", "odd")).toBe("2026-10-05");
    // G16: the cadences added with the detector
    expect(stepOn("2026-09-28", "fortnightly")).toBe("2026-10-12");
    expect(stepOn("2026-08-31", "two-monthly")).toBe("2026-10-31");
    expect(stepOn("2026-08-31", "half-yearly")).toBe("2027-02-28");
  });

  it("keeps a payee's two series apart, each with its own name and this month's own payment, G16", async () => {
    const small: ActiveSeries = { ...PAGE.active[0], id: "payee:Fruitco:398", name: "Fruitco (S$3.98)", payee: "Fruitco",
      amount: 398, yearly: 4776, next_expected: "2026-10-12" };
    const big: ActiveSeries = { ...small, id: "payee:Fruitco:1098", name: "Fruitco (S$10.98)", amount: 1098, yearly: 13176,
      next_expected: "2026-10-20" };
    const rows = [
      up({ id: small.id, name: small.name, payee: "Fruitco", amount: 398, expected: "2026-09-12", paid_on: "2026-09-12", paid_amount: 398 }),
      up({ id: big.id, name: big.name, payee: "Fruitco", amount: 1098, expected: "2026-09-20", status: "due", paid_on: null, paid_amount: null }),
    ];
    const merged = mergeSeries([small, big], rows);
    // the one still due comes first, as its next payment is the sooner
    expect(merged.map((m) => [m.name, m.status, m.paid_amount])).toEqual([
      ["Fruitco (S$10.98)", "due", null], ["Fruitco (S$3.98)", "paid", 398]]);
    stubFetch({ ...PAGE, active: [small, big], coming_up: { ...PAGE.coming_up, rows } });
    render(<Recurring />);
    await screen.findByText("Paid in Sep 2026");
    expect(rowsOf().map(nameOf)).toEqual(["Fruitco (S$10.98)", "Fruitco (S$3.98)"]);
  });

  it("lists a paused series last with a Paused chip, its last payment and no bar, G16", async () => {
    const pool: ActiveSeries = { ...PAGE.active[0], id: "Pool", name: "Pool", payee: "Pool", state: "paused",
      last_paid: "2026-07-05", next_expected: "2026-08-05", yearly: 99000 };
    stubFetch({ ...PAGE, active: PAGE.active.slice(0, 2), paused: [pool], totals: { ...PAGE.totals, paused: 1 } });
    render(<Recurring />);
    await screen.findByText("Paid in Sep 2026");
    const items = rowsOf();
    const last = items[items.length - 1];
    expect(nameOf(last)).toBe("Pool");
    expect(within(last).getByText("Paused")).toBeInTheDocument();
    expect(within(last).getByText("last 5 Jul")).toBeInTheDocument();
    expect(last.querySelector("[data-cost-bar]")).toBeNull();
    expect(within(last).queryByText("Late")).not.toBeInTheDocument();
  });

  it("shows five rows of a longer list and a row that opens the rest", async () => {
    const more = Array.from({ length: 7 }, (_, i) => ({ ...PAGE.active[0], id: `s${i + 1}`, name: `Series ${i + 1}`, payee: `Series ${i + 1}` }));
    stubFetch({ ...PAGE, active: more });
    render(<Recurring />);
    await screen.findByText("Paid in Sep 2026");
    const active = section("Active");
    expect(rowsOf()).toHaveLength(5 + 1);
    expect(within(active).queryByText("Series 6")).not.toBeInTheDocument();
    fireEvent.click(within(active).getByRole("button", { name: "Show all 7" }));
    expect(within(active).getByText("Series 7")).toBeInTheDocument();
    // the short lists have nothing to hide
    expect(within(section("Stopped")).queryByRole("button")).not.toBeInTheDocument();
  });

  it("puts the month's totals on top, investing apart", async () => {
    stubFetch();
    render(<Recurring />);
    expect(await screen.findByText("Paid in Sep 2026")).toBeInTheDocument();
    // paid is the figure, committed its whole, drawn as one progress bar
    expect(screen.getByText("S$1,350")).toBeInTheDocument();
    expect(screen.getByText("of S$1,385 committed, 2 of 4 payments")).toBeInTheDocument();
    expect(screen.getByRole("progressbar", { name: "S$1,350 paid of S$1,385 committed" })).toHaveAttribute("aria-valuenow", "97");
    expect(screen.queryByText(/Committed in/)).not.toBeInTheDocument();
    expect(screen.getByText("To investing")).toBeInTheDocument();
  });

  it("lists what stopped and what is marked recurring with no regular spacing", async () => {
    stubFetch();
    render(<Recurring />);
    await screen.findByText("Paid in Sep 2026");
    expect(within(section("Stopped")).getByText("1 Feb 2025")).toBeInTheDocument();
    const odd = section("Marked recurring, no regular spacing");
    // the count and the total sit beside the strip; the dates are in its tooltips
    expect(within(odd).getByText("3 payments")).toBeInTheDocument();
    expect(within(odd).getByText("S$27.00")).toBeInTheDocument();
    expect(within(odd).queryByText(/3 Jan 2026, 11 Apr 2026/)).not.toBeInTheDocument();
    // G16: Not recurring, saved in the record, in place of a link to the rules that cannot clear a shipped yes
    expect(within(odd).queryByRole("link", { name: "Edit rule" })).not.toBeInTheDocument();
    expect(within(odd).getByRole("button", { name: "Not recurring: Magazine" })).toBeInTheDocument();
  });

  it("opens each series' own view, and a payee with no series its transactions, G16", async () => {
    stubFetch();
    render(<Recurring />);
    await screen.findByText("Paid in Sep 2026");
    expect(href(within(section("Active")).getByText("Broker"))).toBe("/recurring?series=Broker");
    expect(href(within(section("Stopped")).getByText("(no payee, about SGD 18)")))
      .toBe("/recurring?series=%28no+payee%2C+about+SGD+18%29");
    expect(href(within(section("Marked recurring, no regular spacing")).getByText("Magazine")))
      .toBe("/transactions?q=Magazine&hide_transfers=0");
  });

  it("has no Details toggle and asks for nothing but the page's own data", async () => {
    const urls = stubFetch();
    render(<Recurring />);
    await screen.findByText("Paid in Sep 2026");
    expect(screen.queryByRole("button", { name: /worked out|Details/ })).not.toBeInTheDocument();
    expect(screen.getByText("As of 20 Sep 2026")).toBeInTheDocument();
    expect(urls).toEqual(["/api/recurring"]);
  });

  it("says so before the first analysis", async () => {
    stubFetch({ ...PAGE, ready: false });
    render(<Recurring />);
    expect(await screen.findByText("No recurring payments result yet.")).toBeInTheDocument();
  });
});

const MORE: RecurringPage = {
  ...PAGE,
  irregular: [
    PAGE.irregular[0],
    { payee: "Quarterly box", link: { q: "Quarterly box" }, payments: 2, first: "2025-09-20", last: "2026-09-20",
      dates: ["2025-09-20", "2026-09-20"], total: 9000 },
  ],
};

describe("Recurring, friendlier than lists of text", () => {
  it("draws each irregular payee as a strip of dots, one per payment, with the dates in tooltips", async () => {
    stubFetch(MORE);
    render(<Recurring />);
    await screen.findByText("Paid in Sep 2026");
    const odd = within(section("Marked recurring, no regular spacing"));
    const strip = odd.getByRole("button", { name: /^3 payments: 3 Jan 2026, 11 Apr 2026, 20 Apr 2026$/ });
    expect(strip).toHaveAttribute("title", "3 Jan 2026, 11 Apr 2026, 20 Apr 2026");
    const dots = [...strip.querySelectorAll("[data-dot]")];
    expect(dots.map((d) => d.getAttribute("title"))).toEqual(["3 Jan 2026", "11 Apr 2026", "20 Apr 2026"]);
    // one time scale for every payee: the other strip starts earlier, so this one starts part of the way along
    const at = (d: Element) => (d as HTMLElement).dataset.at;
    expect(at(dots[0])).not.toBe("0.0000");
    const other = odd.getByRole("button", { name: /^2 payments/ });
    expect([...other.querySelectorAll("[data-dot]")].map(at)).toEqual(["0.0000", "1.0000"]);
    // the count and the total sit beside it
    expect(odd.getByText("2 payments")).toBeInTheDocument();
    expect(odd.getByText("S$90.00")).toBeInTheDocument();
    expect(odd.getByText("Sep 2025")).toBeInTheDocument();
  });

  it("lists the dates in text when the strip is tapped, for a screen with no hover, and hides them again", async () => {
    stubFetch(MORE);
    render(<Recurring />);
    await screen.findByText("Paid in Sep 2026");
    const odd = within(section("Marked recurring, no regular spacing"));
    const strip = odd.getByRole("button", { name: /^3 payments/ });
    expect(strip).toHaveAttribute("aria-expanded", "false");
    expect(odd.queryByText("3 Jan 2026, 11 Apr 2026, 20 Apr 2026")).not.toBeInTheDocument();
    fireEvent.click(strip);
    expect(strip).toHaveAttribute("aria-expanded", "true");
    expect(odd.getByText("3 Jan 2026, 11 Apr 2026, 20 Apr 2026")).toBeInTheDocument();
    fireEvent.click(strip);
    expect(odd.queryByText("3 Jan 2026, 11 Apr 2026, 20 Apr 2026")).not.toBeInTheDocument();
  });

  it("places each dot by its date between the two ends of the strip", () => {
    const { container } = render(<DotStrip dates={["2026-01-01", "2026-01-11", "2026-01-21"]} from="2026-01-01" to="2026-01-21" label="3 payments" />);
    expect([...container.querySelectorAll("[data-dot]")].map((d) => (d as HTMLElement).dataset.at)).toEqual(["0.0000", "0.5000", "1.0000"]);
  });

  it("says how long ago a stopped payment was last seen, under its date", async () => {
    stubFetch();
    render(<Recurring />);
    await screen.findByText("Paid in Sep 2026");
    // 1 Feb 2025 to the record's 20 Sep 2026
    const cell = within(section("Stopped")).getByText("1 Feb 2025").closest("td")!;
    expect(cell).toHaveTextContent("1 Feb 2025");
    expect(within(cell).getByText("19 months ago")).toBeInTheDocument();
  });

  it("makes a payee read as a link: a chevron, and an underline on hover or focus", async () => {
    stubFetch();
    render(<Recurring />);
    await screen.findByText("Paid in Sep 2026");
    const link = within(section("Active")).getByRole("link", { name: "Gym" });
    expect(link.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
    expect(link.querySelector("span")).toHaveClass("group-hover/payee:underline", "group-focus-visible/payee:underline");
    expect(link.querySelector("svg")).toHaveClass("group-hover/payee:opacity-100", "group-focus-visible/payee:opacity-100");
  });

  describe("on a phone", () => {
    beforeEach(() => stubMatchMedia(false));

    it("turns Stopped into stacked rows, no table to scroll; Active is rows on every width", async () => {
      stubFetch();
      render(<Recurring />);
      await screen.findByText("Paid in Sep 2026");
      expect(within(section("Active")).queryByRole("table")).not.toBeInTheDocument();
      expect(within(section("Stopped")).queryByRole("table")).not.toBeInTheDocument();
      // the same row serves a phone: its width, not the screen's, picks how it is laid out
      expect(rowsOf().map(nameOf)).toEqual(["Phone", "Stream", "Landlord", "Gym", "Broker"]);
      expect(within(rowsOf()[0]).getByText("Late")).toBeInTheDocument();
      const gone = within(section("Stopped")).getAllByRole("listitem")[0];
      expect(gone).toHaveTextContent("S$18.00");
      expect(gone).toHaveTextContent("Monthly · last seen 1 Feb 2025, 19 months ago · was S$216 a year");
    });

    it("keeps the payee link on a stacked row", async () => {
      stubFetch();
      render(<Recurring />);
      await screen.findByText("Paid in Sep 2026");
      expect(href(within(section("Active")).getByText("Broker"))).toBe("/recurring?series=Broker");
    });

    it("keeps Home's Coming up row working: the year gone, the status a dot named by its word", () => {
      render(<ul><ComingUpItem r={up({ status: "late", paid_on: null, paid_amount: null })} /></ul>);
      expect(screen.getByText("~5 Sep")).toBeInTheDocument();
      const dot = screen.getByRole("img", { name: "Late" });
      expect(dot).toHaveAttribute("title", "Late");
      expect(screen.getByTitle("Expected 5 Sep 2026")).toBeInTheDocument();
    });
  });

  describe("Coming up links, G13", () => {
    it("opens the one payment a paid row shows, and a due or late row its series (G16)", () => {
      stubMatchMedia(true);
      render(<ul>
        <ComingUpItem r={up({})} />
        <ComingUpItem r={up({ payee: "Phone", status: "due", paid_on: null, paid_amount: null, link: null })} />
        <ComingUpItem r={up({ payee: "Rent", status: "late", paid_on: null, paid_amount: null, link: null })} />
      </ul>);
      expect(href(screen.getByText("Gym"))).toBe("/transactions?key=k-gym&hide_transfers=0");
      expect(href(screen.getByText("Phone"))).toBe("/recurring?series=Phone");
      expect(href(screen.getByText("Rent"))).toBe("/recurring?series=Rent");
      // the figure is still there, and so is the status
      expect(screen.getByText("Due")).toBeInTheDocument();
      expect(screen.getAllByText("S$50.00").length).toBeGreaterThan(0);
    });

    it("keeps the series on the name and puts this month's payment on the status chip", () => {
      const active = PAGE.active.find((a) => a.payee === "Landlord")!;
      const rent = (over: Partial<ComingUpRow>) => mergeSeries([active], [up({ payee: "Landlord", category: "Rent", amount: 100000, ...over })])[0];
      // different amount and paid: the chip opens the one payment, the name the series
      const different = rent({ link: { key: "k-rent", hide_transfers: "0" }, status: "different", paid_amount: 130000 });
      expect([different.link, different.month_link]).toEqual([active.link, { key: "k-rent", hide_transfers: "0" }]);
      const paid = rent({ link: { key: "k-rent", hide_transfers: "0" } });
      expect([paid.link, paid.month_link]).toEqual([active.link, { key: "k-rent", hide_transfers: "0" }]);
      // due and late have no payment yet: the chip opens nothing
      expect(rent({ status: "due", paid_on: null, paid_amount: null, link: null }).month_link).toBeNull();
      expect(rent({ status: "late", paid_on: null, paid_amount: null, link: null }).month_link).toBeNull();
    });

    it("renders the paid chip as a link to this month's payment on the Recurring list", async () => {
      stubMatchMedia(true);
      stubFetch();
      render(<Recurring />);
      const chip = await screen.findByRole("link", { name: /^Paid: this month's payment to Gym$/ });
      expect(chip.getAttribute("href")).toBe("/transactions?key=k-gym&hide_transfers=0");
    });
  });

  it("keeps the year and the status word on a wide screen", () => {
    stubMatchMedia(true);
    render(<ul><ComingUpItem r={up({ status: "late", paid_on: null, paid_amount: null })} /></ul>);
    expect(screen.getByText("Late")).toBeInTheDocument();
    expect(screen.getByText("~5 Sep 2026")).toBeInTheDocument();
  });

  it("leads each status, cadence and Investing tag with its icon, the word beside it (G18)", async () => {
    stubMatchMedia(true);
    stubFetch();
    render(<Recurring />);
    await screen.findByText("Paid in Sep 2026");
    const items = rowsOf();
    expect(leadingIcon(within(items[0]).getByText("Late"))).toBe("clock-alert");
    expect(leadingIcon(within(items[1]).getByText("Due"))).toBe("hourglass");
    expect(leadingIcon(within(items[2]).getByText("Different amount"))).toBe("equal-not");
    expect(leadingIcon(within(items[3]).getByText("Paid"))).toBe("receipt-text");
    expect(leadingIcon(within(items[3]).getByText("Monthly"))).toBe("calendar-1");
    expect(leadingIcon(within(items[4]).getByText("Investing"))).toBe("trending-up");
    // Stopped's Every column too
    expect(leadingIcon(within(section("Stopped")).getByText("Monthly"))).toBe("calendar-1");
  });

  it("shows a phone's Coming up status as its icon in place of a dot, named by its word (G18)", () => {
    stubMatchMedia(false);
    render(<ul><ComingUpItem r={up({ status: "different", paid_amount: 8000 })} /></ul>);
    const mark = screen.getByRole("img", { name: "Different amount" });
    expect(mark.querySelector("svg")?.getAttribute("class")).toContain("lucide-equal-not");
    expect(mark).toHaveClass("text-info");
  });
});

// ---------------------------------------------------------------- G16: one series' own view

const VIEW: SeriesView = {
  id: "payee:Gym:5000", name: "Gym", payee: "Gym", link: { payee: "Gym", named: "1", hide_transfers: "0", synthetic: "0", out: "1" },
  cadence: "monthly", category: "Fitness", investing: false, amount: 5000, state: "active", late: false,
  usual_low: 3750, usual_high: 6250, amount_varies: false, day: 5, first_seen: "2026-01-05", last_paid: "2026-09-04",
  next_expected: "2026-10-05", ended_on: null, occurrences: 9, yearly: 60000,
  detected: { cadence: "monthly", amount: 5000, day: 5 },
  edit: { recurring: null, amount: null, cadence: null, day: null, ended_on: null },
  schedule: [
    { expected: "2026-10-05", status: "due", paid_on: null, paid_amount: null, link: null },
    { expected: "2026-09-05", status: "paid", paid_on: "2026-09-04", paid_amount: 5000, link: { key: "k9", hide_transfers: "0" } },
    { expected: "2026-08-05", status: "skipped", paid_on: null, paid_amount: null, link: null },
  ],
  payments: [
    { date: "2026-09-04", amount: 5000, regular: true, link: { key: "k9", hide_transfers: "0" } },
    { date: "2026-07-20", amount: 1200, regular: false, link: { key: "k7", hide_transfers: "0" } },
  ],
};

function stubSeries(view: SeriesView | null = VIEW, page: RecurringPage = PAGE) {
  const posts: { url: string; body: unknown }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      posts.push({ url, body: JSON.parse(String(init.body)) });
      return json(view);
    }
    if (url.startsWith("/api/recurring/series")) {
      return view ? json(view)
        : json({ error: "No series has that id now" }, 404);
    }
    return json(page);
  }));
  return posts;
}

describe("A series' own view, G16", () => {
  beforeEach(() => window.history.replaceState(null, "", `/recurring?series=${encodeURIComponent(VIEW.id)}`));
  afterEach(() => { window.history.replaceState(null, "", "/"); });

  it("opens on Recurring itself, with the form at the top and its schedule and payments under it", async () => {
    stubSeries();
    render(<Recurring />);
    const head = await screen.findByRole("region", { name: "Gym" });
    expect(within(head).getByRole("button", { name: "Not recurring" })).toBeInTheDocument();
    expect(within(head).getByLabelText("Usual amount")).toHaveValue("50");
    expect(within(head).getByRole("form", { name: "Edit Gym" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Recurring" })).toHaveAttribute("href", "/recurring");
    // every expected payment with its status; a paid one opens its payment by key
    const schedule = within(section("Schedule")).getAllByRole("listitem");
    expect(schedule.map((li) => within(li).getAllByText(/^(Due|Paid|Skipped)$/)[0].textContent)).toEqual(["Due", "Paid", "Skipped"]);
    expect(within(schedule[1]).getByRole("link", { name: "S$50.00" })).toHaveAttribute("href", "/transactions?key=k9&hide_transfers=0");
    // every payment, one outside the usual range marked; the card opens all of them in Transactions
    const payments = section("Payments");
    expect(within(payments).getAllByText("Different amount")).toHaveLength(1);
    expect(within(payments).getByText("Different amount").closest("li")).toHaveTextContent("20 Jul 2026");
    expect(within(payments).getByRole("link", { name: "In Transactions" }))
      .toHaveAttribute("href", "/transactions?payee=Gym&named=1&hide_transfers=0&synthetic=0&out=1");
    expect(screen.queryByRole("region", { name: "Active" })).not.toBeInTheDocument();
  });

  it("saves what differs from what was detected, and what matches it as detected", async () => {
    const posts = stubSeries();
    render(<Recurring />);
    const head = await screen.findByRole("region", { name: "Gym" });
    fireEvent.change(within(head).getByLabelText("Usual amount"), { target: { value: "55" } });
    fireEvent.click(within(head).getByRole("button", { name: "Save" }));
    await vi.waitFor(() => expect(posts.length).toBe(1));
    expect(posts[0]).toEqual({ url: "/api/recurring/series",
      body: { id: VIEW.id, amount: 5500, cadence: null, day: null, ended_on: null } });
  });

  it("lists a charge taken twice on one day as two payments and two extras, with no clashing row keys", async () => {
    const twice = { date: "2026-09-04", amount: 5000, regular: true };
    const extra = { expected: "2026-09-04", status: "extra" as const, paid_on: "2026-09-04", paid_amount: 5000 };
    stubSeries({ ...VIEW,
      schedule: [{ ...extra, link: { key: "k1", hide_transfers: "0" } }, { ...extra, link: { key: "k2", hide_transfers: "0" } }],
      payments: [{ ...twice, link: { key: "k1", hide_transfers: "0" } }, { ...twice, link: { key: "k2", hide_transfers: "0" } }] });
    const warn = vi.spyOn(console, "error").mockImplementation(() => {});
    render(<Recurring />);
    await screen.findByRole("region", { name: "Gym" });
    expect(within(section("Payments")).getAllByRole("link", { name: "S$50.00" }).map((a) => a.getAttribute("href")))
      .toEqual(["/transactions?key=k1&hide_transfers=0", "/transactions?key=k2&hide_transfers=0"]);
    expect(within(section("Schedule")).getAllByRole("listitem")).toHaveLength(2);
    expect(warn.mock.calls.flat().join(" ")).not.toMatch(/same key/);
  });

  it("marks a payment against the owner's amount, not the detected one", async () => {
    stubSeries({ ...VIEW, amount: 10000, usual_low: 7500, usual_high: 12500, edit: { ...VIEW.edit, amount: 10000 } });
    render(<Recurring />);
    const payments = await screen.findByRole("region", { name: "Payments" });
    expect(within(payments).getAllByText("Different amount")).toHaveLength(2);
  });

  it("shows what was saved once a save comes back, so nothing reads unsaved", async () => {
    const posts = stubSeries();
    render(<Recurring />);
    const head = await screen.findByRole("region", { name: "Gym" });
    fireEvent.change(within(head).getByLabelText("Usual amount"), { target: { value: "" } });
    fireEvent.click(within(head).getByRole("button", { name: "Save" }));
    await vi.waitFor(() => expect(posts.length).toBe(1));
    expect(posts[0].body).toEqual({ id: VIEW.id, amount: null, cadence: null, day: null, ended_on: null });
    // the record keeps the amount as detected, so the box reads the detected one again
    expect(await within(screen.getByRole("region", { name: "Gym" })).findByText("Saved")).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "Gym" })).getByLabelText("Usual amount")).toHaveValue("50");
  });

  it("keeps what is typed while a save is on its way, and shows what the save covered as saved", async () => {
    let release: () => void = () => {};
    let now = VIEW;
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        const body = JSON.parse(String(init.body));
        return new Promise<Response>((done) => {
          release = () => {
            now = { ...VIEW, amount: body.amount ?? VIEW.amount, edit: { ...VIEW.edit, amount: body.amount } };
            done(json(now));
          };
        });
      }
      return json(url.startsWith("/api/recurring/series") ? now : PAGE);
    }));
    render(<Recurring />);
    const head = await screen.findByRole("region", { name: "Gym" });
    fireEvent.change(within(head).getByLabelText("Usual amount"), { target: { value: "55" } });
    fireEvent.click(within(head).getByRole("button", { name: "Save" }));
    // typed while the save is on its way
    choose(within(head).getByRole("button", { name: "How often" }), "Quarterly");
    await act(async () => release());
    await waitFor(() => expect(now.amount).toBe(5500));
    const region = screen.getByRole("region", { name: "Gym" });
    await waitFor(() => expect(within(region).getByLabelText("Usual amount")).toHaveValue("55"));
    expect(within(region).getByRole("button", { name: "How often" })).toHaveTextContent("Quarterly");
    expect(within(region).getByText("Unsaved changes")).toBeInTheDocument();
  });

  it("keeps a box typed back to its old value during a save, through the refresh after it", async () => {
    let release: () => void = () => {};
    let now = VIEW;
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        const body = JSON.parse(String(init.body));
        return new Promise<Response>((done) => {
          release = () => {
            now = { ...VIEW, amount: body.amount ?? VIEW.amount, edit: { ...VIEW.edit, amount: body.amount } };
            done(json(now));
          };
        });
      }
      return json(url.startsWith("/api/recurring/series") ? now : PAGE);
    }));
    render(<Recurring />);
    const head = await screen.findByRole("region", { name: "Gym" });
    const amount = within(head).getByLabelText("Usual amount");
    const before = (amount as HTMLInputElement).value;
    fireEvent.change(amount, { target: { value: "55" } });
    fireEvent.click(within(head).getByRole("button", { name: "Save" }));
    // set back to the old amount while the save of 55 is on its way: an edit
    fireEvent.change(amount, { target: { value: before } });
    expect(within(head).getByText("Unsaved changes")).toBeInTheDocument();
    await act(async () => release());
    await waitFor(() => expect(now.amount).toBe(5500));
    // the series read again after the save does not put 55 over it
    await settle();
    const region = screen.getByRole("region", { name: "Gym" });
    expect(within(region).getByLabelText("Usual amount")).toHaveValue(before);
    expect(within(region).getByText("Unsaved changes")).toBeInTheDocument();
  });

  it("keeps a box typed during a second save when the first save's read comes back meanwhile", async () => {
    const posts: (() => void)[] = [];
    const reads: (() => void)[] = [];
    let now = VIEW;
    let hold = false;
    const answer = () => json(now);
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        const body = JSON.parse(String(init.body));
        return new Promise<Response>((done) => posts.push(() => {
          now = { ...VIEW, amount: body.amount ?? VIEW.amount, edit: { ...VIEW.edit, amount: body.amount } };
          done(answer());
        }));
      }
      if (!url.startsWith("/api/recurring/series")) return json(PAGE);
      // once held, a read of the series waits, and answers with the series as it is when let go
      return hold ? new Promise<Response>((done) => reads.push(() => done(answer()))) : answer();
    }));
    render(<Recurring />);
    const amount = () => within(screen.getByRole("region", { name: "Gym" })).getByLabelText("Usual amount");
    await screen.findByRole("region", { name: "Gym" });
    fireEvent.change(amount(), { target: { value: "55" } });
    fireEvent.click(within(screen.getByRole("region", { name: "Gym" })).getByRole("button", { name: "Save" }));
    hold = true;
    await act(async () => posts.shift()!());
    await waitFor(() => expect(reads.length).toBeGreaterThan(0));
    // a second save before the first one's read is back
    fireEvent.change(amount(), { target: { value: "60" } });
    fireEvent.click(within(screen.getByRole("region", { name: "Gym" })).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(posts.length).toBe(1));
    // the first save's read (55) lands while the second is on its way; then 55 is typed again, an edit against 60
    await act(async () => { while (reads.length) reads.shift()!(); });
    fireEvent.change(amount(), { target: { value: "55" } });
    await act(async () => posts.shift()!());
    await waitFor(() => expect(reads.length).toBeGreaterThan(0));
    await act(async () => { while (reads.length) reads.shift()!(); });
    await settle();
    expect(now.amount).toBe(6000);
    expect(amount()).toHaveValue("55");
    expect(within(screen.getByRole("region", { name: "Gym" })).getByText("Unsaved changes")).toBeInTheDocument();
  });

  it("discards back to what a save just saved, before the series is read again", async () => {
    let release: (() => void) | null = null;
    const reads: (() => void)[] = [];
    let now = VIEW;
    let hold = false;
    const answer = () => json(now);
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        const body = JSON.parse(String(init.body));
        return new Promise<Response>((done) => { release = () => {
          now = { ...VIEW, amount: body.amount ?? VIEW.amount, edit: { ...VIEW.edit, amount: body.amount } };
          done(answer());
        }; });
      }
      if (!url.startsWith("/api/recurring/series")) return json(PAGE);
      return hold ? new Promise<Response>((done) => reads.push(() => done(answer()))) : answer();
    }));
    render(<Recurring />);
    const region = () => screen.getByRole("region", { name: "Gym" });
    await screen.findByRole("region", { name: "Gym" });
    fireEvent.change(within(region()).getByLabelText("Usual amount"), { target: { value: "55" } });
    fireEvent.click(within(region()).getByRole("button", { name: "Save" }));
    // a save on its way may still fail: leaving asks until it lands
    await expectAsks();
    fireEvent.change(within(region()).getByLabelText("Usual amount"), { target: { value: "60" } });
    hold = true;
    await waitFor(() => expect(release).not.toBeNull());
    await act(async () => release!());
    // saved, and the series not read back yet: Discard goes back to 55, what is saved now
    await waitFor(() => expect(within(region()).getByRole("button", { name: "Discard" })).toBeEnabled());
    fireEvent.click(within(region()).getByRole("button", { name: "Discard" }));
    expect(within(region()).getByLabelText("Usual amount")).toHaveValue("55");
    await expectFree();
    expect(within(region()).queryByText("Unsaved changes")).not.toBeInTheDocument();
    await act(async () => { while (reads.length) reads.shift()!(); });
    expect(within(region()).getByLabelText("Usual amount")).toHaveValue("55");
    expect(within(region()).queryByText("Unsaved changes")).not.toBeInTheDocument();
  });

  it("keeps a typed day with its cadence when another tab saves a cadence of the other kind", async () => {
    let now = VIEW;
    vi.stubGlobal("fetch", vi.fn(async (url: string) =>
      json(url.startsWith("/api/recurring/series") ? now : PAGE)));
    render(<Recurring />);
    const head = await screen.findByRole("region", { name: "Gym" });
    // the 20th of the month, typed
    choose(within(head).getByRole("button", { name: "Day" }), "20");
    // another tab makes it weekly, on Tuesdays
    now = { ...VIEW, cadence: "weekly", day: 2, edit: { ...VIEW.edit, cadence: "weekly", day: 2 } };
    await act(async () => notifyChanged());
    await settle();
    const region = screen.getByRole("region", { name: "Gym" });
    // the day typed is a day of the month: it stays with the monthly cadence it was typed under
    expect(within(region).getByRole("button", { name: "How often" })).toHaveTextContent("Monthly");
    expect(within(region).getByRole("button", { name: "Day" })).toHaveTextContent("20");
    expect(within(region).getByText("Unsaved changes")).toBeInTheDocument();
  });

  it("moves a day and cadence left alone together when another tab saves both", async () => {
    let now = { ...VIEW, day: 2, edit: { ...VIEW.edit, day: 2 } };
    vi.stubGlobal("fetch", vi.fn(async (url: string) =>
      json(url.startsWith("/api/recurring/series") ? now : PAGE)));
    render(<Recurring />);
    await screen.findByRole("region", { name: "Gym" });
    // another tab makes the 2nd of the month a weekly Tuesday: the same "2" in the day box
    now = { ...VIEW, cadence: "weekly", day: 2, edit: { ...VIEW.edit, cadence: "weekly", day: 2 } };
    await act(async () => notifyChanged());
    const region = screen.getByRole("region", { name: "Gym" });
    await waitFor(() => expect(within(region).getByRole("button", { name: "How often" })).toHaveTextContent("Weekly"));
    expect(within(region).queryByText("Unsaved changes")).not.toBeInTheDocument();
  });

  it("moves an amount typed as the saved one in other words along with another tab's save", async () => {
    let now = VIEW;
    vi.stubGlobal("fetch", vi.fn(async (url: string) =>
      json(url.startsWith("/api/recurring/series") ? now : PAGE)));
    render(<Recurring />);
    const head = await screen.findByRole("region", { name: "Gym" });
    fireEvent.change(within(head).getByLabelText("Usual amount"), { target: { value: "50.00" } });
    expect(within(head).queryByText("Unsaved changes")).not.toBeInTheDocument();
    // another tab saves 60
    now = { ...VIEW, amount: 6000, edit: { ...VIEW.edit, amount: 6000 } };
    await act(async () => notifyChanged());
    const region = screen.getByRole("region", { name: "Gym" });
    await waitFor(() => expect(within(region).getByLabelText("Usual amount")).toHaveValue("60"));
    expect(within(region).queryByText("Unsaved changes")).not.toBeInTheDocument();
  });

  it("refuses an amount that is not dollars and cents before it sends anything", async () => {
    const posts = stubSeries();
    render(<Recurring />);
    const head = await screen.findByRole("region", { name: "Gym" });
    fireEvent.change(within(head).getByLabelText("Usual amount"), { target: { value: "0" } });
    fireEvent.click(within(head).getByRole("button", { name: "Save" }));
    expect(await within(head).findByRole("alert")).toHaveTextContent("more than zero");
    expect(posts).toEqual([]);
  });

  it("says Not recurring", async () => {
    const posts = stubSeries();
    render(<Recurring />);
    fireEvent.click(within(await screen.findByRole("region", { name: "Gym" })).getByRole("button", { name: "Not recurring" }));
    await vi.waitFor(() => expect(posts.map((p) => p.body)).toEqual([{ id: VIEW.id, recurring: false }]));
  });

  it("keeps what is typed and not saved through Not recurring", async () => {
    const answers = [VIEW, { ...VIEW, state: "not_recurring" as const, next_expected: null, edit: { ...VIEW.edit, recurring: false } }];
    let n = 0;
    vi.stubGlobal("fetch", vi.fn(async (_url: string, init?: RequestInit) => {
      if (init?.method === "POST") n = 1;
      return json(answers[n]);
    }));
    render(<Recurring />);
    const head = await screen.findByRole("region", { name: "Gym" });
    fireEvent.change(within(head).getByLabelText("Usual amount"), { target: { value: "55" } });
    fireEvent.click(within(head).getByRole("button", { name: "Not recurring" }));
    expect(await within(head).findByRole("button", { name: "Recurring" })).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "Gym" })).getByLabelText("Usual amount")).toHaveValue("55");
  });

  it("takes Not recurring back with Recurring", async () => {
    const back = stubSeries({ ...VIEW, state: "not_recurring", next_expected: null, edit: { ...VIEW.edit, recurring: false } });
    render(<Recurring />);
    const head = await screen.findByRole("region", { name: "Gym" });
    expect(await within(head).findByText("Not recurring")).toBeInTheDocument();
    fireEvent.click(within(head).getByRole("button", { name: "Recurring" }));
    await vi.waitFor(() => expect(back.map((p) => p.body)).toEqual([{ id: VIEW.id, recurring: null }]));
  });

  it("offers no day for twice a month, whose two days one day cannot set", async () => {
    stubSeries({ ...VIEW, cadence: "twice-monthly", day: null, detected: { ...VIEW.detected, cadence: "twice-monthly", day: null } });
    render(<Recurring />);
    const head = await screen.findByRole("region", { name: "Gym" });
    expect(within(head).queryByText("Day")).not.toBeInTheDocument();
  });

  it("says plainly when no series has that id now", async () => {
    stubSeries(null);
    render(<Recurring />);
    expect(await screen.findByText("This series is not in the record now.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Recurring" })).toHaveAttribute("href", "/recurring");
  });
});

describe("Not recurring on Recurring, G16", () => {
  it("marks an irregular payee not recurring, lists what is not recurring, and Undo takes each back", async () => {
    const page: RecurringPage = { ...PAGE, not_recurring: [
      { id: null, name: "Club", payee: "Club", cadence: "", category: "", amount: null, last_seen: null },
      { id: "payee:Pool:3000", name: "Pool", payee: "Pool", cadence: "monthly", category: "Fitness", amount: 3000, last_seen: "2026-07-05" },
    ] };
    const posts = stubSeries(VIEW, page);
    render(<Recurring />);
    fireEvent.click(await screen.findByRole("button", { name: "Not recurring: Magazine" }));
    await vi.waitFor(() => expect(posts.length).toBe(1));
    const list = section("Not recurring");
    expect(href(within(list).getByText("Pool"))).toBe("/recurring?series=payee%3APool%3A3000");
    expect(within(list).getByText("Club").closest("a")).toBeNull();
    fireEvent.click(within(list).getByRole("button", { name: "Undo not recurring: Club" }));
    await vi.waitFor(() => expect(posts.length).toBe(2));
    fireEvent.click(within(section("Not recurring")).getByRole("button", { name: "Undo not recurring: Pool" }));
    await vi.waitFor(() => expect(posts.length).toBe(3));
    expect(posts).toEqual([
      { url: "/api/recurring/payee", body: { payee: "Magazine", recurring: false } },
      { url: "/api/recurring/payee", body: { payee: "Club", recurring: null } },
      { url: "/api/recurring/series", body: { id: "payee:Pool:3000", recurring: null } },
    ]);
  });
});
