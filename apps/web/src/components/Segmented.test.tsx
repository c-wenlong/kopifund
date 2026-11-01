import { useState } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { Segmented } from "./Segmented";

const OPTIONS = [
  { value: 3, text: "3M" }, { value: 6, text: "6M", disabled: true }, { value: 12, text: "12M" }, { value: 0, text: "All" },
] as const;

function Harness() {
  const [v, setV] = useState<number>(12);
  return <><Segmented label="Range" value={v} options={OPTIONS} onChange={setV} /><p>now {v}</p></>;
}

describe("Segmented", () => {
  it("is a radio group with one tab stop on the chosen option", () => {
    render(<Harness />);
    expect(screen.getByRole("radiogroup", { name: "Range" })).toBeInTheDocument();
    const radios = screen.getAllByRole("radio");
    expect(radios.map((r) => r.tabIndex)).toEqual([-1, -1, 0, -1]);
    expect(screen.getByRole("radio", { name: "12M" })).toHaveAttribute("aria-checked", "true");
  });

  it("moves with the arrow keys, wraps, and skips an option that is off", () => {
    render(<Harness />);
    const at = (name: string) => screen.getByRole("radio", { name });
    fireEvent.keyDown(at("12M"), { key: "ArrowRight" });
    expect(at("All")).toHaveFocus();
    expect(screen.getByText("now 0")).toBeInTheDocument();
    fireEvent.keyDown(at("All"), { key: "ArrowRight" });   // wraps to the first
    expect(screen.getByText("now 3")).toBeInTheDocument();
    fireEvent.keyDown(at("3M"), { key: "ArrowRight" });    // 6M is off: on to 12M
    expect(screen.getByText("now 12")).toBeInTheDocument();
    fireEvent.keyDown(at("12M"), { key: "ArrowLeft" });    // and back past it
    expect(screen.getByText("now 3")).toBeInTheDocument();
    expect(at("6M")).toBeDisabled();
  });

  it("is a 32px target with a solid ink pill on the chosen option", () => {
    render(<Harness />);
    expect(screen.getByRole("radio", { name: "12M" })).toHaveClass("h-8", "bg-ink", "text-page");
    expect(screen.getByRole("radio", { name: "3M" })).not.toHaveClass("bg-ink");
  });
});
