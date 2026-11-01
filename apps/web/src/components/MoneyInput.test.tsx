import { useState } from "react";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { groupMoney, isMoneyText, isSignedText, MoneyInput, trimZeros, unformatMoney } from "./MoneyInput";

function Harness({ start = "", commits = [] as string[], invalid = false }) {
  const [a, setA] = useState(start);
  const [b, setB] = useState("");
  return (
    <section>
      <MoneyInput label="First" before="S$" value={a} onChange={setA} onCommit={(t) => commits.push(t)} invalid={invalid} />
      <MoneyInput label="Second" before="S$" value={b} onChange={setB} onCommit={(t) => commits.push(`second ${t}`)} />
    </section>
  );
}

describe("MoneyInput", () => {
  it("is a plain text box with the decimal keypad and a next key, so no spinner and no wheel change", () => {
    render(<Harness />);
    const box = screen.getByRole("textbox", { name: "First" });
    expect(box).toHaveAttribute("type", "text");
    expect(box).toHaveAttribute("inputmode", "decimal");
    expect(box).toHaveAttribute("enterkeyhint", "next");
    expect(screen.queryByRole("spinbutton")).not.toBeInTheDocument();
  });

  it("strips commas as they are typed or pasted, and groups thousands once the box is left", () => {
    render(<Harness />);
    const box = screen.getByRole("textbox", { name: "First" });
    fireEvent.focus(box);
    fireEvent.change(box, { target: { value: "1,234,567.5" } });
    expect(box).toHaveValue("1234567.5");
    fireEvent.blur(box);
    expect(box).toHaveValue("1,234,567.5");
    fireEvent.focus(box);
    expect(box).toHaveValue("1234567.5");
  });

  it("drops a zero typed in front of a digit, but keeps 0 and 0.x", () => {
    expect(trimZeros("012")).toBe("12");
    expect(trimZeros("007.5")).toBe("7.5");
    expect(trimZeros("00")).toBe("0");
    expect(trimZeros("0")).toBe("0");
    expect(trimZeros("0.50")).toBe("0.50");
    expect(trimZeros("0.")).toBe("0.");
    expect(trimZeros("")).toBe("");
    render(<Harness />);
    const box = screen.getByRole("textbox", { name: "First" });
    fireEvent.focus(box);
    fireEvent.change(box, { target: { value: "0" } });
    fireEvent.change(box, { target: { value: "01" } });
    expect(box).toHaveValue("1");
    fireEvent.change(box, { target: { value: "0.5" } });
    expect(box).toHaveValue("0.5");
  });

  it("is 40px tall on a touch screen, the same as the box a finger sees around it, and rings in the focus colour", () => {
    render(<Harness />);
    const box = screen.getByRole("textbox", { name: "First" }).parentElement!;
    expect(box).toHaveClass("h-8", "pointer-coarse:h-10");
    expect(box.className).toContain("ring-[var(--focus)]");
    expect(box.className).not.toContain("--c1");
  });

  it("leaves text that is not a number as typed, and marks the box when told it is invalid", () => {
    render(<Harness start="1k5" invalid />);
    const box = screen.getByRole("textbox", { name: "First" });
    expect(box).toHaveValue("1k5");
    expect(box).toHaveAttribute("aria-invalid", "true");
    expect(box.parentElement).toHaveClass("border-danger");
  });

  it("commits what was typed on blur, with no commas", () => {
    const commits: string[] = [];
    render(<Harness start="2,500" commits={commits} />);
    const box = screen.getByRole("textbox", { name: "First" });
    fireEvent.focus(box);
    fireEvent.change(box, { target: { value: "3,000" } });
    fireEvent.blur(box);
    expect(commits).toEqual(["3000"]);
  });

  it("moves to the next amount on Enter, and commits as the box is left", () => {
    const commits: string[] = [];
    render(<Harness commits={commits} />);
    const first = screen.getByRole("textbox", { name: "First" });
    const second = screen.getByRole("textbox", { name: "Second" });
    act(() => first.focus());
    fireEvent.change(first, { target: { value: "40" } });
    fireEvent.keyDown(first, { key: "Enter" });
    expect(second).toHaveFocus();
    expect(commits).toEqual(["40"]);
    // the last one in the card just leaves the box
    fireEvent.keyDown(second, { key: "Enter" });
    expect(second).not.toHaveFocus();
  });
});

describe("MoneyInput across a card's sections", () => {
  it("moves on from the last amount of one section to the first of the next, within the card", () => {
    render(
      <section>
        <section aria-label="Income"><MoneyInput label="Gross" value="" onChange={() => {}} /></section>
        <section aria-label="Spending"><MoneyInput label="Spend" value="" onChange={() => {}} /></section>
        <section aria-label="Assumptions"><MoneyInput label="Elsewhere" value="" onChange={() => {}} /></section>
      </section>,
    );
    const gross = screen.getByRole("textbox", { name: "Gross" });
    act(() => gross.focus());
    fireEvent.keyDown(gross, { key: "Enter" });
    expect(screen.getByRole("textbox", { name: "Spend" })).toHaveFocus();
    // the outermost section is the card, so every amount in it is reached in turn
    fireEvent.keyDown(screen.getByRole("textbox", { name: "Spend" }), { key: "Enter" });
    expect(screen.getByRole("textbox", { name: "Elsewhere" })).toHaveFocus();
  });
});

describe("money text", () => {
  it("reads and writes amounts", () => {
    expect(unformatMoney(" 6,000.50 ")).toBe("6000.50");
    expect(isMoneyText("6,000.50")).toBe(true);
    expect(isMoneyText("17.")).toBe(true);
    expect(isMoneyText(".5")).toBe(true);
    for (const bad of ["1k5", "-3", "1.2.3", "S$5", "1e3"]) expect(isMoneyText(bad)).toBe(false);
    // a rate can be under zero, an amount cannot
    for (const ok of ["-5", "-0.5", "6", "2.5", "-.5"]) expect(isSignedText(ok)).toBe(true);
    for (const bad of ["--5", "5-", "-", "1k5", "- 5x"]) expect(isSignedText(bad)).toBe(false);
    expect(groupMoney("1234567.5")).toBe("1,234,567.5");
    expect(groupMoney("999")).toBe("999");
    expect(groupMoney("1000")).toBe("1,000");
    expect(groupMoney("1k5")).toBe("1k5");
    expect(groupMoney("")).toBe("");
  });
});
