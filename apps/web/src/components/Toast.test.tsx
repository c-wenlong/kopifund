import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Toaster, clearToasts, lifetime, showToast } from "./Toast";

beforeEach(() => vi.useFakeTimers());
afterEach(() => {
  act(() => clearToasts());
  cleanup();
  vi.useRealTimers();
});

const undo = { label: "Undo", onClick: () => {} };

describe("toast lifetime", () => {
  it("lets a plain notice go by itself and a toast with an Undo stay", () => {
    expect(lifetime({ message: "Saved" })).toBe(5000);
    expect(lifetime({ message: "Nope", tone: "error" })).toBe(8000);
    expect(lifetime({ message: "Trip saved", action: undo })).toBeNull();
    expect(lifetime({ message: "Quick", ms: 1000, action: undo })).toBe(1000);
  });

  it("keeps an Undo up long after the plain notices are gone", () => {
    render(<Toaster />);
    act(() => {
      showToast({ message: "Trip saved, 3 rows.", action: undo });
      showToast({ message: "Plan saved." });
    });
    act(() => void vi.advanceTimersByTime(60_000));
    expect(screen.getByText("Trip saved, 3 rows.")).toBeInTheDocument();
    expect(screen.queryByText("Plan saved.")).toBeNull();
  });

  it("takes the Undo down when it is pressed or dismissed", async () => {
    const pressed = vi.fn();
    render(<Toaster />);
    act(() => void showToast({ message: "Removed a trip.", action: { label: "Undo", onClick: pressed } }));
    await act(async () => void fireEvent.click(screen.getByRole("button", { name: "Undo" })));
    expect(pressed).toHaveBeenCalledOnce();
    expect(screen.queryByText("Removed a trip.")).toBeNull();
    act(() => void showToast({ message: "Rule saved.", action: undo }));
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByText("Rule saved.")).toBeNull();
  });

  it("keeps the Undo when unrelated notices fill the queue around it", () => {
    render(<Toaster />);
    act(() => {
      showToast({ message: "Trip saved, 3 rows.", action: undo });
      showToast({ message: "Plan saved." });
      showToast({ message: "Rules applied." });
      showToast({ message: "Settings saved." });
      showToast({ tone: "error", message: "Could not reach Notion." });
    });
    expect(screen.getByText("Trip saved, 3 rows.")).toBeInTheDocument();
    expect(screen.getByText("Could not reach Notion.")).toBeInTheDocument();
    expect(screen.queryByText("Plan saved.")).toBeNull();
    expect(screen.getAllByRole("button", { name: "Undo" })).toHaveLength(1);
  });

  it("stacks Undos, newest on top, each undoing its own write", async () => {
    const first = vi.fn();
    const second = vi.fn();
    render(<Toaster />);
    act(() => {
      showToast({ message: "Category set, 1 row.", action: { label: "Undo", onClick: first } });
      showToast({ message: "Rule saved, 6 rows.", action: { label: "Undo", onClick: second } });
    });
    const messages = screen.getAllByRole("status").map((el) => el.textContent ?? "");
    expect(messages[0]).toContain("Rule saved, 6 rows.");
    expect(messages[1]).toContain("Category set, 1 row.");
    const undos = screen.getAllByRole("button", { name: "Undo" });
    expect(undos).toHaveLength(2);
    await act(async () => void fireEvent.click(undos[1]));
    expect(first).toHaveBeenCalledOnce();
    expect(second).not.toHaveBeenCalled();
    // the other one is still there, and still undoes its own write
    expect(screen.getByText("Rule saved, 6 rows.")).toBeInTheDocument();
    await act(async () => void fireEvent.click(screen.getByRole("button", { name: "Undo" })));
    expect(second).toHaveBeenCalledOnce();
    expect(screen.queryByText("Rule saved, 6 rows.")).toBeNull();
  });

  it("keeps an Undo that did not undo anything, so it can be tried again", async () => {
    // the server refuses an older Undo while a newer write stands; the toast must outlive the refusal
    let allowed = false;
    const older = vi.fn(async () => allowed);
    render(<Toaster />);
    act(() => void showToast({ message: "Category set, 1 row.", action: { label: "Undo", onClick: older } }));
    await act(async () => void fireEvent.click(screen.getByRole("button", { name: "Undo" })));
    expect(older).toHaveBeenCalledOnce();
    expect(screen.getByText("Category set, 1 row.")).toBeInTheDocument();
    // the button is pressable again, and once it works the toast goes
    allowed = true;
    await act(async () => void fireEvent.click(screen.getByRole("button", { name: "Undo" })));
    expect(older).toHaveBeenCalledTimes(2);
    expect(screen.queryByText("Category set, 1 row.")).toBeNull();
  });

  it("does not take a second press while its Undo is still running", async () => {
    let finish: (v: boolean) => void = () => {};
    const slow = vi.fn(() => new Promise<boolean>((r) => { finish = r; }));
    render(<Toaster />);
    act(() => void showToast({ message: "Rule saved, 2 rows.", action: { label: "Undo", onClick: slow } }));
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    expect(slow).toHaveBeenCalledOnce();
    await act(async () => finish(true));
    expect(screen.queryByText("Rule saved, 2 rows.")).toBeNull();
  });

  it("keeps two Undos with the same words, one each", () => {
    render(<Toaster />);
    act(() => {
      showToast({ message: "Category set, 1 row.", action: undo });
      showToast({ message: "Category set, 1 row.", action: undo });
    });
    expect(screen.getAllByRole("button", { name: "Undo" })).toHaveLength(2);
  });

  it("holds at most three Undos, the oldest going first, and drops plain notices before any Undo", () => {
    render(<Toaster />);
    act(() => {
      for (const n of [1, 2, 3]) showToast({ message: `Write ${n}.`, action: undo });
      showToast({ message: "Plan saved." });
      showToast({ message: "Rules applied." });
    });
    // three Undos and one plain notice fit; the older plain notice went
    expect(screen.getAllByRole("button", { name: "Undo" })).toHaveLength(3);
    expect(screen.queryByText("Plan saved.")).toBeNull();
    expect(screen.getByText("Rules applied.")).toBeInTheDocument();
    act(() => void showToast({ message: "Write 4.", action: undo }));
    expect(screen.getAllByRole("button", { name: "Undo" })).toHaveLength(3);
    expect(screen.queryByText("Write 1.")).toBeNull();
    for (const n of [2, 3, 4]) expect(screen.getByText(`Write ${n}.`)).toBeInTheDocument();
  });

  it("still replaces a plain notice that repeats", () => {
    render(<Toaster />);
    act(() => {
      showToast({ message: "Plan saved." });
      showToast({ message: "Plan saved." });
    });
    expect(screen.getAllByText("Plan saved.")).toHaveLength(1);
  });
});
