import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "../App";

// G22: the panel sits beside the page from 768px, pushing it narrower, and is a bottom sheet under it.

function setWidth(wide: boolean) {
  window.matchMedia = ((query: string) => ({
    matches: wide, media: query, onchange: null,
    addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
}
const original = window.matchMedia;

beforeEach(() => {
  window.history.pushState(null, "", "/spending");
  // no answer ever comes: the shell and the panel draw before any figure
  vi.spyOn(globalThis, "fetch").mockImplementation(() => new Promise<Response>(() => {}));
});

afterEach(() => {
  vi.restoreAllMocks();
  window.matchMedia = original;
  window.history.pushState(null, "", "/");
});

describe("Ask Kopifund in the shell", () => {
  it("opens beside the page and pushes it narrower, rather than over it", () => {
    setWidth(true);
    const { container } = render(<App />);
    const shell = container.querySelector(".safe-area")!;
    expect(shell).not.toHaveAttribute("data-copilot");
    fireEvent.click(screen.getByRole("button", { name: "Ask Kopifund" }));
    expect(shell).toHaveAttribute("data-copilot", "open");
    const panel = screen.getByRole("complementary", { name: "Ask Kopifund" });
    // a flex sibling of the page, after it: the page gives up the width
    expect(panel.parentElement).toBe(shell);
    expect(panel.previousElementSibling).toBe(screen.getByRole("main"));
    expect(panel.className).toMatch(/\bshrink-0\b/);
    expect(panel.className).toMatch(/w-\[360px\]/);
    expect(panel.className).not.toMatch(/\b(fixed|absolute)\b/);
    expect(screen.getByRole("main").className).toMatch(/\bflex-1\b/);
    expect(window.localStorage.getItem("kopifund.copilot")).toBe("open");
  });

  it("stays open on the next visit when it was left open, and Escape closes it", () => {
    setWidth(true);
    window.localStorage.setItem("kopifund.copilot", "open");
    render(<App />);
    const panel = screen.getByRole("complementary", { name: "Ask Kopifund" });
    fireEvent.keyDown(within(panel).getByRole("textbox", { name: "Ask about your money" }), { key: "Escape" });
    expect(screen.queryByRole("complementary", { name: "Ask Kopifund" })).not.toBeInTheDocument();
    expect(window.localStorage.getItem("kopifund.copilot")).toBe("closed");
    expect(screen.getByRole("button", { name: "Ask Kopifund" })).toHaveFocus();
  });

  it("is a bottom sheet from the top bar under 768px", () => {
    setWidth(false);
    render(<App />);
    expect(screen.queryByRole("complementary", { name: "Ask Kopifund" })).not.toBeInTheDocument();
    fireEvent.click(within(screen.getByRole("banner")).getByRole("button", { name: "Ask Kopifund" }));
    const sheet = screen.getByRole("dialog", { name: "Ask Kopifund" });
    expect(within(sheet).getByRole("textbox", { name: "Ask about your money" })).toBeInTheDocument();
  });
});
