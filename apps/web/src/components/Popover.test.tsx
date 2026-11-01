import { render, screen } from "@testing-library/react";
import { createRef } from "react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { Popover } from "./Popover";

// jsdom has no layout: give the field a place on the screen and the box a size
const SCREEN = { width: 400, height: 600 };

function stubLayout(box: { w: number; h: number }) {
  Object.defineProperty(window, "innerWidth", { configurable: true, value: SCREEN.width });
  Object.defineProperty(window, "innerHeight", { configurable: true, value: SCREEN.height });
  Object.defineProperty(HTMLElement.prototype, "offsetWidth", { configurable: true, get() { return this.getAttribute("role") === "dialog" ? box.w : 0; } });
  Object.defineProperty(HTMLElement.prototype, "offsetHeight", { configurable: true, get() { return this.getAttribute("role") === "dialog" ? box.h : 0; } });
  Object.defineProperty(HTMLElement.prototype, "scrollHeight", { configurable: true, get() { return this.getAttribute("role") === "dialog" ? box.h - 2 : 0; } });
}

function open(field: { left: number; top: number; width?: number; height?: number }) {
  const anchor = createRef<HTMLElement>();
  const el = document.createElement("span");
  const { left, top, width = 160, height = 32 } = field;
  el.getBoundingClientRect = () => ({ left, top, width, height, right: left + width, bottom: top + height, x: left, y: top, toJSON() {} }) as DOMRect;
  (anchor as { current: HTMLElement | null }).current = el;
  render(<Popover anchor={anchor} label="Date calendar" onClose={() => {}}><p>calendar</p></Popover>);
  return screen.getByRole("dialog", { name: "Date calendar" });
}

describe("Popover placement", () => {
  beforeEach(() => stubLayout({ w: 280, h: 340 }));
  afterEach(() => {
    for (const k of ["offsetWidth", "offsetHeight", "scrollHeight"]) delete (HTMLElement.prototype as unknown as Record<string, unknown>)[k];
  });

  it("opens under the field when there is room", () => {
    const box = open({ left: 20, top: 100 });
    expect(box.style.top).toBe("136px");
    expect(box.style.bottom).toBe("");
    expect(box.style.maxHeight).toBe("");
  });

  it("flips above the field when there is no room below but there is above", () => {
    const box = open({ left: 20, top: 480 });
    // its bottom edge 4px over the field, the same gap as below it, and placed by top
    // (a bottom offset is measured from the layout viewport and left a gap on a phone)
    expect(Number.parseFloat(box.style.top) + 340).toBe(480 - 4);
    expect(box.style.bottom).toBe("");
    expect(box.style.maxHeight).toBe("");
  });

  it("keeps the 4px gap above the field when the box is held shorter than it is", () => {
    // a short screen with the field low: 250px over it, a 340px box
    const box = open({ left: 20, top: 300 });
    const max = 300 - 4 - 8;
    expect(box.style.maxHeight).toBe(`${max}px`);
    expect(Number.parseFloat(box.style.top) + max).toBe(300 - 4);
  });

  it("takes the roomier side and scrolls inside it when the box fits neither", () => {
    // a short screen: 340px tall box, field in the middle of 600px leaves about 250px a side
    const box = open({ left: 20, top: 330 });
    const aboveRoom = 330 - 4 - 8;
    const belowRoom = SCREEN.height - (330 + 32) - 4 - 8;
    expect(belowRoom).toBeLessThan(340);
    expect(aboveRoom).toBeLessThan(340);
    expect(box.style.maxHeight).toBe(`${Math.max(aboveRoom, belowRoom)}px`);
    expect(box.className).toContain("overflow-y-auto");
  });

  it("never holds the box taller than the room there is, even on a very short screen", () => {
    Object.defineProperty(window, "innerHeight", { configurable: true, value: 240 });
    // 100px of 240 for the field's own row leaves under 120px on the roomier side
    const box = open({ left: 20, top: 100 });
    const below = 240 - 132 - 4 - 8;
    expect(below).toBeLessThan(120);
    expect(box.style.maxHeight).toBe(`${Math.max(below, 100 - 4 - 8)}px`);
    expect(Number.parseFloat(box.style.maxHeight)).toBeLessThan(120);
  });

  it("stays inside the screen sideways", () => {
    expect(open({ left: 330, top: 100 }).style.left).toBe(`${SCREEN.width - 280 - 8}px`);
  });

  it("keeps an 8px margin on the left", () => {
    document.body.innerHTML = "";
    expect(open({ left: -40, top: 100 }).style.left).toBe("8px");
  });
});
