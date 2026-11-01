import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { Combobox } from "./Combobox";

const CATS = ["Eating out", "Groceries", "Shopping", "Transport"];

function Harness({ start = "" }: { start?: string }) {
  const [value, setValue] = useState(start);
  return (
    <>
      <Combobox label="Category" value={value} options={CATS} onChange={setValue} />
      <output data-testid="value">{value}</output>
    </>
  );
}

describe("Combobox", () => {
  it("filters the options as you type", () => {
    render(<Harness />);
    const box = screen.getByRole("combobox", { name: "Category" });
    fireEvent.focus(box);
    expect(screen.getAllByRole("option")).toHaveLength(4);
    fireEvent.change(box, { target: { value: "ro" } });
    expect(screen.getAllByRole("option").map((o) => o.textContent)).toEqual(["Groceries"]);
  });

  it("chooses with the keyboard", () => {
    render(<Harness />);
    const box = screen.getByRole("combobox", { name: "Category" });
    fireEvent.focus(box);
    fireEvent.change(box, { target: { value: "s" } });
    fireEvent.keyDown(box, { key: "ArrowDown" });
    fireEvent.keyDown(box, { key: "Enter" });
    expect(screen.getByTestId("value")).toHaveTextContent("Shopping");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  it("chooses with the mouse", () => {
    render(<Harness />);
    fireEvent.focus(screen.getByRole("combobox", { name: "Category" }));
    fireEvent.mouseDown(screen.getByRole("option", { name: "Transport" }));
    expect(screen.getByTestId("value")).toHaveTextContent("Transport");
  });

  it("takes the highlighted option when you Tab away from a half-typed name", () => {
    const MORE = ["Transport", "Transport (public)", "Transport (private)", "Travel"];
    function Two() {
      const [value, setValue] = useState("");
      return <><Combobox label="Category" value={value} options={MORE} onChange={setValue} /><output data-testid="value">{value}</output></>;
    }
    render(<Two />);
    const box = screen.getByRole("combobox", { name: "Category" });
    fireEvent.focus(box);
    fireEvent.change(box, { target: { value: "transport (p" } });
    expect(screen.getAllByRole("option")).toHaveLength(2);
    fireEvent.keyDown(box, { key: "Tab" });
    fireEvent.blur(box);
    expect(screen.getByTestId("value")).toHaveTextContent("Transport (public)");
    expect(box).toHaveValue("Transport (public)");
  });

  it("takes the arrowed-to option on Tab, and the only option left on blur", () => {
    render(<Harness />);
    const box = screen.getByRole("combobox", { name: "Category" });
    fireEvent.focus(box);
    fireEvent.change(box, { target: { value: "s" } });
    fireEvent.keyDown(box, { key: "ArrowDown" });
    fireEvent.blur(box);
    expect(screen.getByTestId("value")).toHaveTextContent("Shopping");
    fireEvent.focus(box);
    fireEvent.change(box, { target: { value: "groc" } });
    fireEvent.blur(box);
    expect(screen.getByTestId("value")).toHaveTextContent("Groceries");
  });

  it("leaves the choice alone when nothing was typed", () => {
    render(<Harness start="Transport" />);
    const box = screen.getByRole("combobox", { name: "Category" });
    fireEvent.focus(box);
    fireEvent.blur(box);
    expect(screen.getByTestId("value")).toHaveTextContent("Transport");
    fireEvent.focus(box);
    fireEvent.change(box, { target: { value: "" } });
    fireEvent.blur(box);
    expect(screen.getByTestId("value")).toHaveTextContent("Transport");
  });

  it("puts the last choice back when you leave with text that is not a category", () => {
    render(<Harness start="Groceries" />);
    const box = screen.getByRole("combobox", { name: "Category" });
    fireEvent.focus(box);
    fireEvent.change(box, { target: { value: "Food" } });
    fireEvent.blur(box);
    expect(box).toHaveValue("Groceries");
    fireEvent.focus(box);
    fireEvent.change(box, { target: { value: "eating out" } });
    fireEvent.blur(box);
    expect(screen.getByTestId("value")).toHaveTextContent("Eating out");
  });

  it("opens on its value when it takes the cursor itself, and says when it closes (G13)", () => {
    const done = vi.fn();
    render(<Combobox label="Category" value="Shopping" options={CATS} onChange={() => {}} autoFocus onDone={done} />);
    const box = screen.getByRole("combobox", { name: "Category" });
    expect(box).toHaveFocus();
    expect(screen.getByRole("option", { name: "Shopping" })).toHaveAttribute("aria-selected", "true");
    fireEvent.keyDown(box, { key: "Escape" });
    expect(done).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  it("as a picker, takes no choice from leaving the box, only from a pick (G13)", () => {
    const chosen = vi.fn();
    const done = vi.fn();
    render(<Combobox label="Category" value="" options={CATS} onChange={chosen} onDone={done} commitOnLeave={false} />);
    const box = screen.getByRole("combobox", { name: "Category" });
    fireEvent.focus(box);
    fireEvent.change(box, { target: { value: "shop" } });
    fireEvent.blur(box);
    expect(chosen).not.toHaveBeenCalled();
    expect(done).toHaveBeenCalledTimes(1);
    expect(box).toHaveValue("");
  });
});
