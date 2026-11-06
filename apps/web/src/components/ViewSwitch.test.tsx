import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Card } from "./Card";
import { useChartView, ViewSwitch, viewsOf, type ChartView } from "./ViewSwitch";

const KEYS = ["stacked", "lines", "total"] as const;

function Harness({ id = "t" }: { id?: string }) {
  const [view, setView] = useChartView<ChartView>(id, KEYS, "stacked");
  return <><ViewSwitch label="Chart view" value={view} options={viewsOf("stacked", "lines", "total")} onChange={setView} /><p>now {view}</p></>;
}

describe("ViewSwitch", () => {
  it("is a radio group with one tab stop, and the arrow keys move and choose", () => {
    render(<Harness />);
    const group = screen.getByRole("radiogroup", { name: "Chart view" });
    expect(group).toBeInTheDocument();
    const [stacked, lines, total] = screen.getAllByRole("radio");
    expect(stacked).toHaveAttribute("aria-checked", "true");
    expect([stacked, lines, total].map((r) => r.tabIndex)).toEqual([0, -1, -1]);
    fireEvent.keyDown(stacked, { key: "ArrowRight" });
    expect(lines).toHaveAttribute("aria-checked", "true");
    expect(lines).toHaveFocus();
    fireEvent.keyDown(lines, { key: "ArrowLeft" });
    fireEvent.keyDown(stacked, { key: "ArrowLeft" });   // wraps round to the last one
    expect(total).toHaveAttribute("aria-checked", "true");
    expect(screen.getByText("now total")).toBeInTheDocument();
  });

  it("remembers the choice per chart and starts from it next time", () => {
    const first = render(<Harness id="a" />);
    fireEvent.click(screen.getByRole("radio", { name: "Lines" }));
    expect(localStorage.getItem("kopifund.chart-view.a")).toBe("lines");
    first.unmount();
    const second = render(<Harness id="a" />);
    expect(screen.getByRole("radio", { name: "Lines" })).toHaveAttribute("aria-checked", "true");
    second.unmount();
    render(<Harness id="b" />);
    expect(screen.getByRole("radio", { name: "Stacked" })).toHaveAttribute("aria-checked", "true");
  });

  it("ignores a stored view it does not offer, and works when storage throws", () => {
    localStorage.setItem("kopifund.chart-view.c", "pie");
    const { unmount } = render(<Harness id="c" />);
    expect(screen.getByText("now stacked")).toBeInTheDocument();
    unmount();
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("blocked"); });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("blocked"); });
    render(<Harness id="d" />);
    fireEvent.click(screen.getByRole("radio", { name: "Total" }));
    expect(screen.getByText("now total")).toBeInTheDocument();
  });
});

describe("ViewSwitch's name", () => {
  const options = viewsOf("stacked", "lines");
  it("names the chart it belongs to, from the card around it", () => {
    render(<>
      <Card title="Net worth so far"><ViewSwitch label="Chart view" value="stacked" options={options} onChange={() => {}} /></Card>
      <Card title="Net worth projected"><ViewSwitch label="Chart view" value="stacked" options={options} onChange={() => {}} /></Card>
    </>);
    expect(screen.getByRole("radiogroup", { name: "Chart view for Net worth so far" })).toBeInTheDocument();
    expect(screen.getByRole("radiogroup", { name: "Chart view for Net worth projected" })).toBeInTheDocument();
    expect(screen.queryByRole("radiogroup", { name: "Chart view" })).toBeNull();
  });

  it("takes the chart's name from a prop when it is given, and is plain outside a card", () => {
    const { rerender } = render(<Card title="Ignored"><ViewSwitch label="Chart view" chart="Home spending" value="stacked" options={options} onChange={() => {}} /></Card>);
    expect(screen.getByRole("radiogroup", { name: "Chart view for Home spending" })).toBeInTheDocument();
    rerender(<ViewSwitch label="Chart view" value="stacked" options={options} onChange={() => {}} />);
    expect(screen.getByRole("radiogroup", { name: "Chart view" })).toBeInTheDocument();
  });
});
