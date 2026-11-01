import { useState } from "react";
import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DateField, DateRangeFields } from "./DateField";

// A controlled box that records what the page would hold.
function Holder({ initial = "", ...rest }: { initial?: string } & Partial<React.ComponentProps<typeof DateField>>) {
  const [v, setV] = useState(initial);
  return (
    <>
      <DateField label="Day" value={v} onChange={setV} {...rest} />
      <output data-testid="held">{v}</output>
    </>
  );
}
const held = () => screen.getByTestId("held").textContent;
const calendar = () => screen.queryByRole("dialog", { name: "Day calendar" });

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date(2026, 9, 17, 12));   // 17 Oct 2026, local
});
afterEach(() => vi.useRealTimers());

describe("DateField", () => {
  it("shows an ISO value as the app's day and holds ISO back out", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(<Holder initial="2026-10-03" />);
    const box = screen.getByRole("textbox", { name: "Day" });
    expect(box).toHaveValue("3 Oct 2026");
    await user.clear(box);
    await user.type(box, "9 Nov 2026");
    expect(held()).toBe("2026-11-09");
  });

  it("takes typing in words, ISO and day-first numbers, and ignores what is not a date", () => {
    render(<Holder />);
    const box = screen.getByRole("textbox", { name: "Day" });
    for (const [typed, iso] of [["3 oct 2026", "2026-10-03"], ["2026-11-04", "2026-11-04"], ["5/12/2026", "2026-12-05"], ["Jan 6 2027", "2027-01-06"]]) {
      fireEvent.change(box, { target: { value: typed } });
      expect(held(), typed).toBe(iso);
    }
    fireEvent.change(box, { target: { value: "31 Feb 2026" } });
    expect(held()).toBe("2027-01-06");
    expect(box).toHaveValue("31 Feb 2026");
    // leaving the box keeps the text and says so, until it is fixed; the page still holds the last good day
    fireEvent.blur(box);
    expect(box).toHaveValue("31 Feb 2026");
    expect(screen.getByRole("alert")).toHaveTextContent("Not a date");
    expect(box).toHaveAttribute("aria-invalid", "true");
    expect(box).toHaveAccessibleDescription("Not a date");
    // the box makes room under itself for the message, so it never covers the line below
    expect(box.parentElement).toHaveClass("mb-4");
    expect(held()).toBe("2027-01-06");
    // typing a real day clears the message
    fireEvent.change(box, { target: { value: "28 Feb 2026" } });
    expect(screen.queryByRole("alert")).toBeNull();
    expect(box.parentElement).not.toHaveClass("mb-4");
    expect(held()).toBe("2026-02-28");
  });

  it("shows the held day again when the calendar picks that same day over a bad text", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(<Holder initial="2026-10-03" />);
    const box = screen.getByRole("textbox", { name: "Day" });
    await user.click(box);
    fireEvent.change(box, { target: { value: "31 Feb 2026" } });
    fireEvent.blur(box);
    expect(screen.getByRole("alert")).toBeInTheDocument();
    await user.click(box);
    await user.click(within(calendar()!).getByRole("button", { name: "Saturday, October 3rd, 2026, selected" }));
    expect(held()).toBe("2026-10-03");
    expect(box).toHaveValue("3 Oct 2026");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("drops the warning when the box is cleared", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(<Holder initial="2026-10-03" clearable />);
    const box = screen.getByRole("textbox", { name: "Day" });
    fireEvent.change(box, { target: { value: "31 Feb 2026" } });
    fireEvent.blur(box);
    expect(screen.getByRole("alert")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Clear Day" }));
    expect(screen.queryByRole("alert")).toBeNull();
    expect(box).toHaveValue("");
    expect(held()).toBe("");
  });

  it("does not call an empty box not a date", () => {
    render(<Holder initial="2026-10-03" />);
    const box = screen.getByRole("textbox", { name: "Day" });
    fireEvent.change(box, { target: { value: "  " } });
    fireEvent.blur(box);
    expect(screen.queryByRole("alert")).toBeNull();
    expect(box).toHaveValue("3 Oct 2026");
  });

  it("opens a calendar on the value's month when the box is clicked, and a day picked sets ISO and closes it", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(<Holder initial="2026-10-03" />);
    expect(calendar()).toBeNull();
    await user.click(screen.getByRole("textbox", { name: "Day" }));
    const cal = within(calendar()!);
    expect(cal.getByText("October 2026")).toBeInTheDocument();
    expect(cal.getByRole("button", { name: "Saturday, October 3rd, 2026, selected" })).toBeInTheDocument();
    await user.click(cal.getByRole("button", { name: /October 20th/ }));
    expect(held()).toBe("2026-10-20");
    expect(calendar()).toBeNull();
    expect(screen.getByRole("textbox", { name: "Day" })).toHaveValue("20 Oct 2026");
    // the focus came back to the box without opening the calendar again
    expect(screen.getByRole("textbox", { name: "Day" })).toHaveFocus();
    expect(calendar()).toBeNull();
  });

  it("opens on today's month while empty, and Today fills today", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(<Holder />);
    await user.click(screen.getByRole("textbox", { name: "Day" }));
    expect(within(calendar()!).getByText("October 2026")).toBeInTheDocument();
    await user.click(within(calendar()!).getByRole("button", { name: "Today" }));
    expect(held()).toBe("2026-10-17");
    expect(calendar()).toBeNull();
  });

  it("opens on a given month while empty, and moves to the month of a date that is typed", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(<Holder defaultMonth={new Date(1996, 0, 1)} />);
    const box = screen.getByRole("textbox", { name: "Day" });
    await user.click(box);
    expect(within(calendar()!).getByText("January 1996")).toBeInTheDocument();
    await user.type(box, "12 Mar 1995");
    expect(within(calendar()!).getByText("March 1995")).toBeInTheDocument();
  });

  it("closes on Escape and gives the focus back, and on a press outside", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(<><Holder initial="2026-10-03" /><button type="button">Elsewhere</button></>);
    const box = screen.getByRole("textbox", { name: "Day" });
    await user.click(box);
    expect(calendar()).not.toBeNull();
    await user.keyboard("{Escape}");
    expect(calendar()).toBeNull();
    expect(box).toHaveFocus();
    expect(held()).toBe("2026-10-03");
    // a click on the box opens it again, and a press elsewhere closes it
    await user.click(box);
    expect(calendar()).not.toBeNull();
    await user.click(screen.getByRole("button", { name: "Elsewhere" }));
    expect(calendar()).toBeNull();
  });

  it("closes on Tab out of the box and lets the focus move on", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(<><Holder initial="2026-10-03" /><button type="button">Next control</button></>);
    await user.click(screen.getByRole("textbox", { name: "Day" }));
    expect(calendar()).not.toBeNull();
    await user.tab();
    expect(calendar()).toBeNull();
    expect(screen.getByRole("button", { name: "Next control" })).toHaveFocus();
  });

  it("closes on Escape from inside the calendar too, and does not leak the key to a parent", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    const outer = vi.fn();
    document.addEventListener("keydown", outer);
    try {
      render(<Holder initial="2026-10-03" />);
      await user.click(screen.getByRole("textbox", { name: "Day" }));
      within(calendar()!).getByRole("button", { name: /October 20th/ }).focus();
      outer.mockClear();
      await user.keyboard("{Escape}");
      expect(calendar()).toBeNull();
      expect(screen.getByRole("textbox", { name: "Day" })).toHaveFocus();
      expect(outer).not.toHaveBeenCalled();
    } finally {
      document.removeEventListener("keydown", outer);
    }
  });

  it("moves into the calendar with the arrow-down key and walks it with the arrows", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(<Holder initial="2026-10-03" />);
    const box = screen.getByRole("textbox", { name: "Day" });
    box.focus();
    await user.keyboard("{ArrowDown}");
    const cal = within(calendar()!);
    expect(cal.getByRole("button", { name: /October 3rd/ })).toHaveFocus();
    await user.keyboard("{ArrowRight}{ArrowDown}");
    expect(cal.getByRole("button", { name: /October 11th/ })).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(held()).toBe("2026-10-11");
    expect(calendar()).toBeNull();
    expect(box).toHaveFocus();
  });

  it("moves into the calendar on every arrow-down, also after clicking back into the box", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(<Holder initial="2026-10-03" />);
    const box = screen.getByRole("textbox", { name: "Day" });
    box.focus();
    await user.keyboard("{ArrowDown}");
    expect(within(calendar()!).getByRole("button", { name: /October 3rd/ })).toHaveFocus();
    await user.click(box);
    expect(box).toHaveFocus();
    await user.keyboard("{ArrowDown}");
    expect(within(calendar()!).getByRole("button", { name: /October 3rd/ })).toHaveFocus();
  });

  it("greys out days before the earliest and after the latest", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(<Holder initial="2026-10-10" min="2026-10-05" max="2026-10-25" />);
    await user.click(screen.getByRole("textbox", { name: "Day" }));
    const cal = within(calendar()!);
    expect(cal.getByRole("button", { name: /October 4th/ })).toBeDisabled();
    expect(cal.getByRole("button", { name: /October 5th/ })).toBeEnabled();
    expect(cal.getByRole("button", { name: /October 26th/ })).toBeDisabled();
  });

  it("clears an optional date with its button, and with emptied text", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(<Holder initial="2026-10-03" clearable />);
    await user.click(screen.getByRole("button", { name: "Clear Day" }));
    expect(held()).toBe("");
    expect(screen.getByRole("textbox", { name: "Day" })).toHaveValue("");
    expect(calendar()).toBeNull();
    expect(screen.queryByRole("button", { name: "Clear Day" })).toBeNull();
    fireEvent.change(screen.getByRole("textbox", { name: "Day" }), { target: { value: "4 Nov 2026" } });
    fireEvent.change(screen.getByRole("textbox", { name: "Day" }), { target: { value: "" } });
    expect(held()).toBe("");
  });

  it("gives a required date no clear button, and keeps the last good date when the text is emptied", () => {
    render(<Holder initial="2026-10-03" />);
    expect(screen.queryByRole("button", { name: /Clear/ })).toBeNull();
    const box = screen.getByRole("textbox", { name: "Day" });
    fireEvent.change(box, { target: { value: "" } });
    expect(held()).toBe("2026-10-03");
    fireEvent.blur(box);
    expect(box).toHaveValue("3 Oct 2026");
  });

  it("offers a year dropdown when given a range of years", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(<Holder initial="2000-08-03" yearRange={[1990, 2026]} />);
    await user.click(screen.getByRole("textbox", { name: "Day" }));
    const years = within(calendar()!).getByRole("combobox", { name: /year/i });
    expect([...(years as HTMLSelectElement).options].map((o) => o.value)).toContain("1995");
    fireEvent.change(years, { target: { value: "1995" } });
    expect(within(calendar()!).getByText(/August 1995/)).toBeInTheDocument();
  });

  it("does not open while disabled", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(<Holder initial="2026-10-03" disabled />);
    await user.click(screen.getByRole("textbox", { name: "Day" }));
    expect(calendar()).toBeNull();
  });
});

function Trip({ start = "", end = "" }: { start?: string; end?: string }) {
  const [r, setR] = useState({ start, end });
  return (
    <>
      <DateRangeFields start={r.start} end={r.end} startLabel="First day" endLabel="Last day" onChange={(s, e) => setR({ start: s, end: e })} />
      <output data-testid="held">{`${r.start}|${r.end}`}</output>
    </>
  );
}
const rangeCalendar = (name: string) => within(screen.getByRole("dialog", { name }));

describe("DateRangeFields", () => {
  it("picks a start, which makes a day trip, then an end, in one calendar", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(<Trip />);
    await user.click(screen.getByRole("textbox", { name: "First day" }));
    await user.click(rangeCalendar("First day calendar").getByRole("button", { name: /October 5th/ }));
    // the end follows the start, and the same calendar stays open, where it was, for the end
    expect(held()).toBe("2026-10-05|2026-10-05");
    expect(screen.getByRole("textbox", { name: "Last day" })).toHaveValue("5 Oct 2026");
    expect(screen.getAllByRole("dialog")).toHaveLength(1);
    await user.click(rangeCalendar("First day calendar").getByRole("button", { name: /October 9th/ }));
    expect(held()).toBe("2026-10-05|2026-10-09");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByRole("textbox", { name: "First day" })).toHaveValue("5 Oct 2026");
    expect(screen.getByRole("textbox", { name: "Last day" })).toHaveValue("9 Oct 2026");
  });

  it("leaves a start box's bad text alone when only the end is picked", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(<Trip start="2026-10-05" end="2026-10-05" />);
    const first = screen.getByRole("textbox", { name: "First day" });
    fireEvent.change(first, { target: { value: "31 Feb 2026" } });
    fireEvent.blur(first);
    expect(screen.getByRole("alert")).toHaveTextContent("Not a date");
    await user.click(screen.getByRole("textbox", { name: "Last day" }));
    await user.click(rangeCalendar("Last day calendar").getByRole("button", { name: /October 9th/ }));
    expect(held()).toBe("2026-10-05|2026-10-09");
    expect(first).toHaveValue("31 Feb 2026");
    expect(screen.getByRole("alert")).toBeInTheDocument();
  });

  it("draws the range between the two days", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(<Trip start="2026-10-05" end="2026-10-09" />);
    await user.click(screen.getByRole("textbox", { name: "First day" }));
    const cal = screen.getByRole("dialog");
    const days = (n: number) => within(cal).getByRole("button", { name: new RegExp(`October ${n}(st|nd|rd|th)`) }).closest("td")!;
    expect(days(5)).toHaveClass("rdp-range_start");
    expect(days(7)).toHaveClass("rdp-range_middle");
    expect(days(9)).toHaveClass("rdp-range_end");
    expect(days(10)).not.toHaveClass("rdp-range_middle");
  });

  it("redraws the range as the days are typed or picked while the calendar is open", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(<Trip />);
    const first = screen.getByRole("textbox", { name: "First day" });
    await user.click(first);
    await user.type(first, "5/10/2026");
    const cell = (n: number) => screen.getByRole("dialog").querySelector(`[data-day="2026-10-${String(n).padStart(2, "0")}"]`)!;
    expect(cell(5)).toHaveClass("rdp-selected");
    await user.type(screen.getByRole("textbox", { name: "Last day" }), "{Control>}a{/Control}9 Oct 2026");
    expect(cell(5)).toHaveClass("rdp-range_start");
    expect(cell(7)).toHaveClass("rdp-range_middle");
    expect(cell(9)).toHaveClass("rdp-range_end");
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: /October 12th/ }));
    expect(held()).toBe("2026-10-05|2026-10-12");
  });

  it("opens the end box on the start's month, even when the start is not this month", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(<Trip start="2027-02-10" end="2027-02-10" />);
    await user.click(screen.getByRole("textbox", { name: "Last day" }));
    expect(rangeCalendar("Last day calendar").getByText("February 2027")).toBeInTheDocument();
    await user.click(rangeCalendar("Last day calendar").getByRole("button", { name: /February 14th/ }));
    expect(held()).toBe("2027-02-10|2027-02-14");
  });

  it("starts the range again when the second click is before the start", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(<Trip start="2026-10-12" end="2026-10-12" />);
    await user.click(screen.getByRole("textbox", { name: "Last day" }));
    await user.click(rangeCalendar("Last day calendar").getByRole("button", { name: /October 3rd/ }));
    expect(held()).toBe("2026-10-03|2026-10-03");
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    await user.click(rangeCalendar("Last day calendar").getByRole("button", { name: /October 6th/ }));
    expect(held()).toBe("2026-10-03|2026-10-06");
  });

  it("clicking the start of a finished range picks a new start and clears the end back to it", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(<Trip start="2026-10-05" end="2026-10-09" />);
    await user.click(screen.getByRole("textbox", { name: "First day" }));
    await user.click(rangeCalendar("First day calendar").getByRole("button", { name: /October 20th/ }));
    expect(held()).toBe("2026-10-20|2026-10-20");
  });

  it("typing a start fills the end when it is empty or earlier, and leaves a later end", () => {
    render(<Trip />);
    const first = screen.getByRole("textbox", { name: "First day" });
    fireEvent.change(first, { target: { value: "3 Oct 2026" } });
    expect(held()).toBe("2026-10-03|2026-10-03");
    fireEvent.change(screen.getByRole("textbox", { name: "Last day" }), { target: { value: "9 Oct 2026" } });
    fireEvent.change(first, { target: { value: "4 Oct 2026" } });
    expect(held()).toBe("2026-10-04|2026-10-09");
    fireEvent.change(first, { target: { value: "20 Oct 2026" } });
    expect(held()).toBe("2026-10-20|2026-10-20");
  });

  it("tabbing from the first day to the last leaves one calendar, hanging from the last", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(<Trip start="2026-10-05" end="2026-10-09" />);
    await user.click(screen.getByRole("textbox", { name: "First day" }));
    await user.tab();
    expect(screen.getByRole("textbox", { name: "Last day" })).toHaveFocus();
    expect(screen.getAllByRole("dialog")).toHaveLength(1);
    expect(screen.getByRole("dialog", { name: "Last day calendar" })).toBeInTheDocument();
    await user.tab();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("moves into the range calendar on every arrow-down", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(<Trip start="2026-10-05" end="2026-10-09" />);
    const first = screen.getByRole("textbox", { name: "First day" });
    first.focus();
    await user.keyboard("{ArrowDown}");
    expect(within(screen.getByRole("dialog")).getByRole("button", { name: /October 5th/ })).toHaveFocus();
    await user.click(first);
    await user.keyboard("{ArrowDown}");
    expect(within(screen.getByRole("dialog")).getByRole("button", { name: /October 5th/ })).toHaveFocus();
  });

  it("has a Today shortcut that works as a pick: a start first, then the end", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(<Trip />);
    await user.click(screen.getByRole("textbox", { name: "First day" }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Today" }));
    expect(held()).toBe("2026-10-17|2026-10-17");
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Today" }));
    expect(held()).toBe("2026-10-17|2026-10-17");
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("closes on Escape with the days as they were", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(<Trip start="2026-10-05" end="2026-10-09" />);
    await user.click(screen.getByRole("textbox", { name: "Last day" }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(held()).toBe("2026-10-05|2026-10-09");
    expect(screen.getByRole("textbox", { name: "Last day" })).toHaveFocus();
  });
});
