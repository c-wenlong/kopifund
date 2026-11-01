import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Tabs } from "./Tabs";

const ITEMS = [
  { id: "a", label: "Food", count: 4 },
  { id: "b", label: "Home" },
  { id: "c", label: "Travel", count: 0 },
];

function Harness({ start = "a" }: { start?: string }) {
  const [value, setValue] = useState(start);
  return (
    <Tabs label="Buckets" items={ITEMS} value={value} onChange={setValue}>
      <p>panel for {value}</p>
    </Tabs>
  );
}

describe("Tabs", () => {
  it("has a tablist, tabs with aria-selected and one labelled tabpanel", () => {
    render(<Harness />);
    expect(screen.getByRole("tablist", { name: "Buckets" })).toBeInTheDocument();
    const tabs = screen.getAllByRole("tab");
    expect(tabs.map((t) => t.getAttribute("aria-selected"))).toEqual(["true", "false", "false"]);
    const panel = screen.getByRole("tabpanel");
    expect(panel).toHaveAccessibleName("Food 4");
    expect(panel).toHaveTextContent("panel for a");
    expect(tabs[0]).toHaveAttribute("aria-controls", panel.id);
  });

  it("shows a count only when it is given, zero included", () => {
    render(<Harness />);
    expect(screen.getByRole("tab", { name: "Food 4" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Home" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Travel 0" })).toBeInTheDocument();
  });

  it("selects on click, and only the selected tab is in the tab order", () => {
    render(<Harness />);
    fireEvent.click(screen.getByRole("tab", { name: "Home" }));
    expect(screen.getByRole("tabpanel")).toHaveTextContent("panel for b");
    expect(screen.getAllByRole("tab").map((t) => t.getAttribute("tabindex"))).toEqual(["-1", "0", "-1"]);
  });

  it("moves with the arrows, wrapping at both ends, Home and End", () => {
    render(<Harness />);
    const [food, home, travel] = screen.getAllByRole("tab");
    food.focus();
    fireEvent.keyDown(food, { key: "ArrowRight" });
    expect(home).toHaveAttribute("aria-selected", "true");
    expect(home).toHaveFocus();
    fireEvent.keyDown(home, { key: "ArrowLeft" });
    fireEvent.keyDown(food, { key: "ArrowLeft" });
    expect(travel).toHaveAttribute("aria-selected", "true");
    expect(travel).toHaveFocus();
    fireEvent.keyDown(travel, { key: "ArrowRight" });
    expect(food).toHaveAttribute("aria-selected", "true");
    fireEvent.keyDown(food, { key: "End" });
    expect(travel).toHaveFocus();
    fireEvent.keyDown(travel, { key: "Home" });
    expect(food).toHaveFocus();
    expect(screen.getByRole("tabpanel")).toHaveTextContent("panel for a");
  });

  it("leaves other keys alone, and falls back to the first tab for a value no tab has", () => {
    render(<Harness start="nope" />);
    expect(screen.getByRole("tab", { name: "Food 4" })).toHaveAttribute("aria-selected", "true");
    const tab = screen.getByRole("tab", { name: "Food 4" });
    expect(fireEvent.keyDown(tab, { key: "a" })).toBe(true);
  });
});

describe("Tabs that overflow", () => {
  // jsdom has no layout: give the tab row a width, a content width and a scroll position
  let left = 0;
  const stub = (content: number, shown: number) => {
    const row = (el: HTMLElement) => el.getAttribute("role") === "tablist";
    Object.defineProperty(HTMLElement.prototype, "scrollWidth", { configurable: true, get() { return row(this) ? content : 0; } });
    Object.defineProperty(HTMLElement.prototype, "clientWidth", { configurable: true, get() { return row(this) ? shown : 0; } });
    Object.defineProperty(HTMLElement.prototype, "scrollLeft", { configurable: true, get() { return row(this) ? left : 0; }, set(v) { left = v; } });
  };
  afterEach(() => {
    left = 0;
    for (const k of ["scrollWidth", "clientWidth", "scrollLeft"]) delete (HTMLElement.prototype as unknown as Record<string, unknown>)[k];
  });

  it("shows no arrow and no fade when every tab fits", () => {
    stub(300, 300);
    render(<Harness />);
    expect(screen.queryByRole("button", { name: /Scroll tabs/ })).toBeNull();
    expect(screen.getByRole("tablist").style.maskImage).toBe("");
    expect(screen.getByRole("tablist").style.scrollPaddingInline).toBe("");
  });

  it("shows an arrow only on the edge that has more tabs past it, and fades that edge", () => {
    stub(900, 300);
    render(<Harness />);
    const list = screen.getByRole("tablist");
    expect(screen.getByRole("button", { name: "Scroll tabs right" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Scroll tabs left" })).toBeNull();
    expect(list.style.maskImage).toContain("transparent");
    // a mask only needs opaque and clear: no raw colour in it
    expect(list.style.maskImage).not.toMatch(/#[0-9a-f]{3,6}/i);
    // a tab the keyboard scrolls to stops short of the fade and its arrow, not under them
    expect(list.style.scrollPaddingInline).toBe("44px");
    // scrolled to the middle: both ways
    left = 200;
    fireEvent.scroll(list);
    expect(screen.getByRole("button", { name: "Scroll tabs left" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Scroll tabs right" })).toBeInTheDocument();
    // scrolled to the end: only back
    left = 600;
    fireEvent.scroll(list);
    expect(screen.getByRole("button", { name: "Scroll tabs left" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Scroll tabs right" })).toBeNull();
  });

  it("scrolls the row a good part of its width when an arrow is pressed, and keeps the arrows out of the tab order", () => {
    stub(900, 300);
    render(<Harness />);
    const scrollBy = vi.fn();
    screen.getByRole("tablist").scrollBy = scrollBy;
    const right = screen.getByRole("button", { name: "Scroll tabs right" });
    expect(right.tabIndex).toBe(-1);
    fireEvent.click(right);
    expect(scrollBy).toHaveBeenCalledWith(expect.objectContaining({ left: 210 }));
  });
});
