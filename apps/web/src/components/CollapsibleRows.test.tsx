import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { COLLAPSED_ROWS, CollapsibleRows } from "./CollapsibleRows";

const names = (n: number) => Array.from({ length: n }, (_, i) => `Row ${i + 1}`);

function List({ n, limit }: { n: number; limit: number }) {
  return (
    <ul>
      <CollapsibleRows rows={names(n)} limit={limit} render={(r) => <li>{r}</li>} />
    </ul>
  );
}

function Table({ n, limit }: { n: number; limit: number }) {
  return (
    <table>
      <tbody>
        <CollapsibleRows as="tr" colSpan={2} rows={names(n)} limit={limit}
          render={(r) => <tr><td>{r}</td><td>x</td></tr>} />
      </tbody>
    </table>
  );
}

describe("CollapsibleRows", () => {
  it("counts 3 for thick rows, 4 for mid and 5 for thin", () => {
    expect(COLLAPSED_ROWS).toEqual({ thick: 3, mid: 4, thin: 5 });
  });

  it("shows only the first rows of a longer list, and a toggle that says how many there are", () => {
    render(<List n={7} limit={4} />);
    expect(screen.getAllByText(/^Row \d$/)).toHaveLength(4);
    expect(screen.queryByText("Row 5")).not.toBeInTheDocument();
    const toggle = screen.getByRole("button", { name: "Show all 7" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
  });

  it("opens the rest, then collapses again", () => {
    render(<List n={7} limit={4} />);
    fireEvent.click(screen.getByRole("button", { name: "Show all 7" }));
    expect(screen.getAllByText(/^Row \d$/)).toHaveLength(7);
    const toggle = screen.getByRole("button", { name: "Show fewer" });
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    fireEvent.click(toggle);
    expect(screen.getAllByText(/^Row \d$/)).toHaveLength(4);
    expect(screen.getByRole("button", { name: "Show all 7" })).toBeInTheDocument();
  });

  it("has no toggle when the list is no longer than the limit", () => {
    const { rerender } = render(<List n={4} limit={4} />);
    expect(screen.getAllByText(/^Row \d$/)).toHaveLength(4);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    rerender(<List n={0} limit={4} />);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("works in a table body, the toggle spanning every column", () => {
    render(<Table n={8} limit={5} />);
    expect(screen.getAllByRole("row")).toHaveLength(6);
    const toggle = screen.getByRole("button", { name: "Show all 8" });
    expect(toggle.closest("td")).toHaveAttribute("colspan", "2");
    fireEvent.click(toggle);
    expect(screen.getAllByRole("row")).toHaveLength(9);
  });
});

describe("CollapsibleRows, rows that are not all counted", () => {
  it("says what it is given when closed, and Show fewer once open", () => {
    render(<ul><CollapsibleRows rows={names(6)} limit={2} allLabel="Show all 4 trips" toggleClass="col-span-full" render={(r) => <li>{r}</li>} /></ul>);
    const toggle = screen.getByRole("button", { name: "Show all 4 trips" });
    expect(toggle.closest("li")).toHaveClass("col-span-full");
    fireEvent.click(toggle);
    expect(screen.getByRole("button", { name: "Show fewer" })).toBeInTheDocument();
  });
});

describe("CollapsibleRows toggle", () => {
  it("starts at the left even where a touch screen centres an icon button", () => {
    render(<List n={8} limit={3} />);
    expect(screen.getByRole("button", { name: "Show all 8" })).toHaveClass("justify-start");
  });
});
