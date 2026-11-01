import { useState } from "react";
import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MonthField } from "./MonthField";

function Holder({ initial = "", ...rest }: { initial?: string } & Partial<React.ComponentProps<typeof MonthField>>) {
  const [v, setV] = useState(initial);
  return (
    <>
      <MonthField label="Month" value={v} onChange={setV} {...rest} />
      <output data-testid="held">{v}</output>
    </>
  );
}
const held = () => screen.getByTestId("held").textContent;
const popup = () => screen.queryByRole("dialog", { name: "Month calendar" });

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date(2026, 9, 17, 12));
});
afterEach(() => vi.useRealTimers());

describe("MonthField", () => {
  it("shows an ISO month as the app's month and holds ISO back out", () => {
    render(<Holder initial="2026-10" />);
    const box = screen.getByRole("textbox", { name: "Month" });
    expect(box).toHaveValue("Oct 2026");
    for (const [typed, iso] of [["feb 2027", "2027-02"], ["2026-11", "2026-11"], ["3/2028", "2028-03"], ["October 2029", "2029-10"]]) {
      fireEvent.change(box, { target: { value: typed } });
      expect(held(), typed).toBe(iso);
    }
    fireEvent.change(box, { target: { value: "13/2030" } });
    expect(held()).toBe("2029-10");
    // the text stays, marked, until it is fixed
    fireEvent.blur(box);
    expect(box).toHaveValue("13/2030");
    expect(screen.getByRole("alert")).toHaveTextContent("Not a month");
  });

  it("opens a year header over twelve months, on the value's year, and a month picked sets ISO and closes it", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(<Holder initial="2026-10" />);
    expect(popup()).toBeNull();
    await user.click(screen.getByRole("textbox", { name: "Month" }));
    const grid = within(popup()!);
    expect(grid.getByText("2026")).toBeInTheDocument();
    expect(grid.getAllByRole("button", { name: /^[A-Z][a-z]{2} 2026$/ })).toHaveLength(12);
    expect(grid.getByRole("button", { name: "Oct 2026" })).toHaveAttribute("aria-pressed", "true");
    await user.click(grid.getByRole("button", { name: "Feb 2026" }));
    expect(held()).toBe("2026-02");
    expect(popup()).toBeNull();
    expect(screen.getByRole("textbox", { name: "Month" })).toHaveValue("Feb 2026");
    expect(screen.getByRole("textbox", { name: "Month" })).toHaveFocus();
    expect(popup()).toBeNull();
  });

  it("steps the year with the arrows and picks a month in it", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(<Holder initial="2026-10" />);
    await user.click(screen.getByRole("textbox", { name: "Month" }));
    await user.click(screen.getByRole("button", { name: "Next year" }));
    await user.click(screen.getByRole("button", { name: "Next year" }));
    expect(within(popup()!).getByText("2028")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Previous year" }));
    await user.click(within(popup()!).getByRole("button", { name: "Jan 2027" }));
    expect(held()).toBe("2027-01");
  });

  it("opens on this year while empty", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(<Holder />);
    await user.click(screen.getByRole("textbox", { name: "Month" }));
    expect(within(popup()!).getByText("2026")).toBeInTheDocument();
    expect(within(popup()!).getByRole("button", { name: "Oct 2026" })).toHaveAttribute("aria-pressed", "false");
  });

  it("moves the open grid to the year of a month typed in, and keeps browsing years by hand", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(<Holder initial="2026-10" />);
    const box = screen.getByRole("textbox", { name: "Month" });
    await user.click(box);
    await user.clear(box);
    await user.type(box, "Mar 2028");
    expect(held()).toBe("2028-03");
    expect(within(popup()!).getByText("2028")).toBeInTheDocument();
    expect(within(popup()!).getByRole("button", { name: "Mar 2028" })).toHaveAttribute("aria-pressed", "true");
    await user.click(within(popup()!).getByRole("button", { name: "Next year" }));
    expect(within(popup()!).getByText("2029")).toBeInTheDocument();
    await user.click(within(popup()!).getByRole("button", { name: "Jun 2029" }));
    expect(held()).toBe("2029-06");
  });

  it("walks the grid with the arrow keys, over a year's edge, and picks with Enter", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(<Holder initial="2026-11" />);
    const box = screen.getByRole("textbox", { name: "Month" });
    box.focus();
    await user.keyboard("{ArrowDown}");
    expect(within(popup()!).getByRole("button", { name: "Nov 2026" })).toHaveFocus();
    await user.keyboard("{ArrowRight}{ArrowRight}");
    expect(within(popup()!).getByText("2027")).toBeInTheDocument();
    expect(within(popup()!).getByRole("button", { name: "Jan 2027" })).toHaveFocus();
    await user.keyboard("{ArrowDown}");
    expect(within(popup()!).getByRole("button", { name: "Apr 2027" })).toHaveFocus();
    await user.keyboard("{PageUp}");
    expect(within(popup()!).getByRole("button", { name: "Apr 2026" })).toHaveFocus();
    await user.keyboard("{End}");
    expect(within(popup()!).getByRole("button", { name: "Dec 2026" })).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(held()).toBe("2026-12");
    expect(popup()).toBeNull();
    expect(box).toHaveFocus();
  });

  it("moves into the grid on every arrow-down, also after clicking back into the box", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(<Holder initial="2026-10" />);
    const box = screen.getByRole("textbox", { name: "Month" });
    box.focus();
    await user.keyboard("{ArrowDown}");
    expect(within(popup()!).getByRole("button", { name: "Oct 2026" })).toHaveFocus();
    await user.click(box);
    await user.keyboard("{ArrowDown}");
    expect(within(popup()!).getByRole("button", { name: "Oct 2026" })).toHaveFocus();
  });

  it("keeps the focus in the box while a month is typed after visiting the grid", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(<Holder initial="2026-10" />);
    const box = screen.getByRole("textbox", { name: "Month" });
    box.focus();
    await user.keyboard("{ArrowDown}");
    expect(within(popup()!).getByRole("button", { name: "Oct 2026" })).toHaveFocus();
    await user.click(box);
    await user.clear(box);
    await user.type(box, "2028-12");
    expect(box).toHaveValue("2028-12");
    expect(box).toHaveFocus();
    expect(held()).toBe("2028-12");
    expect(within(popup()!).getByRole("button", { name: "Dec 2028" })).toHaveAttribute("aria-pressed", "true");
  });

  it("closes on Escape with the focus back in the box, and on a press outside", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(<><Holder initial="2026-10" /><button type="button">Elsewhere</button></>);
    const box = screen.getByRole("textbox", { name: "Month" });
    await user.click(box);
    expect(popup()).not.toBeNull();
    await user.keyboard("{Escape}");
    expect(popup()).toBeNull();
    expect(box).toHaveFocus();
    expect(held()).toBe("2026-10");
    await user.click(box);
    expect(popup()).not.toBeNull();
    await user.click(screen.getByRole("button", { name: "Elsewhere" }));
    expect(popup()).toBeNull();
  });

  it("clears an optional month, and offers no clear button on a required one", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    const { unmount } = render(<Holder initial="2026-10" />);
    expect(screen.queryByRole("button", { name: "Clear Month" })).toBeNull();
    unmount();
    render(<Holder initial="2026-10" clearable placeholder="From the start" />);
    expect(screen.getByRole("textbox", { name: "Month" })).toHaveAttribute("placeholder", "From the start");
    await user.click(screen.getByRole("button", { name: "Clear Month" }));
    expect(held()).toBe("");
    expect(screen.getByRole("textbox", { name: "Month" })).toHaveValue("");
    expect(popup()).toBeNull();
  });
});
