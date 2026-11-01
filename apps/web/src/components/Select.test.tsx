import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { useState } from "react";
import { Select } from "./Select";

const OPTS = [{ value: "sgd", label: "SGD" }, { value: "sek", label: "SEK" }, { value: "usd", label: "USD" }];

function Harness() {
  const [value, setValue] = useState("");
  return (
    <>
      <Select label="Currency" placeholder="Currency" value={value} options={OPTS} onChange={setValue} />
      <output data-testid="value">{value}</output>
    </>
  );
}

describe("Select", () => {
  it("shows the placeholder until something is chosen, then the choice", () => {
    render(<Harness />);
    const button = screen.getByRole("button", { name: "Currency" });
    expect(button).toHaveTextContent("Currency");
    fireEvent.click(button);
    fireEvent.mouseDown(screen.getByRole("option", { name: "SEK" }));
    expect(screen.getByTestId("value")).toHaveTextContent("sek");
    expect(button).toHaveTextContent("SEK");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  it("chooses with the keyboard and Escape leaves the value alone", () => {
    render(<Harness />);
    const button = screen.getByRole("button", { name: "Currency" });
    fireEvent.keyDown(button, { key: "ArrowDown" });
    const list = screen.getByRole("listbox");
    fireEvent.keyDown(list, { key: "End" });
    fireEvent.keyDown(list, { key: "Enter" });
    expect(screen.getByTestId("value")).toHaveTextContent("usd");
    fireEvent.click(button);
    fireEvent.keyDown(screen.getByRole("listbox"), { key: "ArrowUp" });
    fireEvent.keyDown(screen.getByRole("listbox"), { key: "Escape" });
    expect(screen.getByTestId("value")).toHaveTextContent("usd");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  it("lists a disabled option with its note but never chooses it", () => {
    function WithSoon() {
      const [value, setValue] = useState("");
      return (
        <>
          <Select label="Bank" placeholder="Bank" value={value} onChange={setValue}
            options={[{ value: "a", label: "A" }, { value: "t", label: "Trust", disabled: true, note: "Coming soon" }, { value: "b", label: "B" }]} />
          <output data-testid="bank">{value}</output>
        </>
      );
    }
    render(<WithSoon />);
    fireEvent.click(screen.getByRole("button", { name: "Bank" }));
    const soon = screen.getByRole("option", { name: /Trust/ });
    expect(soon).toHaveAttribute("aria-disabled", "true");
    expect(soon).toHaveTextContent("Coming soon");
    fireEvent.mouseDown(soon);
    expect(screen.getByTestId("bank")).toBeEmptyDOMElement();
    expect(screen.getByRole("listbox")).toBeInTheDocument();
    // ArrowDown from A steps over Trust to B
    const list = screen.getByRole("listbox");
    fireEvent.keyDown(list, { key: "ArrowDown" });
    fireEvent.keyDown(list, { key: "Enter" });
    expect(screen.getByTestId("bank")).toHaveTextContent("b");
  });

  it("closes without choosing on a click elsewhere", () => {
    render(<Harness />);
    fireEvent.click(screen.getByRole("button", { name: "Currency" }));
    fireEvent.mouseDown(document.body);
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    expect(screen.getByTestId("value")).toBeEmptyDOMElement();
  });

  // G18 (U8): it stands in for every native select, so it keeps what a native one does
  it("jumps to the option a typed letter starts, open or closed, as a native select does", () => {
    const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun"].map((m) => ({ value: m, label: m }));
    function Months() {
      const [value, setValue] = useState("Jan");
      return (
        <>
          <Select label="Month" placeholder="Month" value={value} options={months} onChange={setValue} />
          <output data-testid="month">{value}</output>
        </>
      );
    }
    render(<Months />);
    const button = screen.getByRole("button", { name: "Month" });
    // closed: a letter changes the value at once; the same letter again moves on to the next match
    fireEvent.keyDown(button, { key: "m" });
    expect(screen.getByTestId("month")).toHaveTextContent("Mar");
    fireEvent.keyDown(button, { key: "m" });
    expect(screen.getByTestId("month")).toHaveTextContent("May");
    // open: a letter moves the highlight, Enter chooses it
    fireEvent.click(button);
    const list = screen.getByRole("listbox");
    fireEvent.keyDown(list, { key: "f" });
    expect(screen.getByRole("option", { name: "Feb" }).id).toBe(list.getAttribute("aria-activedescendant"));
    fireEvent.keyDown(list, { key: "Enter" });
    expect(screen.getByTestId("month")).toHaveTextContent("Feb");
  });

  it("calls onChange only for a new value, as a native select fires change", () => {
    const onChange = vi.fn();
    render(<Select label="Currency" placeholder="Currency" value="sek" options={OPTS} onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: "Currency" }));
    fireEvent.mouseDown(screen.getByRole("option", { name: "SEK" }));
    expect(onChange).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Currency" }));
    fireEvent.mouseDown(screen.getByRole("option", { name: "USD" }));
    expect(onChange).toHaveBeenCalledWith("usd");
  });

  it("heads each group of options, as an optgroup does, and marks a refused value", () => {
    render(<Select label="Category" placeholder="Choose a category" value="" invalid onChange={() => {}}
      options={[{ value: "1", label: "Groceries", group: "Food" }, { value: "2", label: "Coffee", group: "Food" },
        { value: "3", label: "Shopping", group: "Lifestyle" }]} />);
    const button = screen.getByRole("button", { name: "Category" });
    expect(button).toHaveAttribute("aria-invalid", "true");
    expect(button).toHaveTextContent("Choose a category");
    fireEvent.click(button);
    // each run is a group named by its heading, which is never an option itself
    const food = screen.getByRole("group", { name: "Food" });
    expect(within(food).getAllByRole("option").map((o) => o.textContent)).toEqual(["Groceries", "Coffee"]);
    expect(within(screen.getByRole("group", { name: "Lifestyle" })).getAllByRole("option").map((o) => o.textContent)).toEqual(["Shopping"]);
    expect(screen.getAllByRole("option")).toHaveLength(3);
  });
});
