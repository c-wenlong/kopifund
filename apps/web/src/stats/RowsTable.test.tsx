import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { RowsTable } from "./StatCard";

describe("RowsTable", () => {
  it("shows a count chart's columns as counts, and money columns as money", () => {
    render(<RowsTable unit="count" rows={[{ month: "2026-08", uncategorised: 3, synthetic: 2, uncategorised_out_minor: 6000 }]} />);
    expect(screen.getByText("3")).toBeInTheDocument();
    expect(screen.queryByText("S$3")).not.toBeInTheDocument();
    expect(screen.getByText(/S\$60/)).toBeInTheDocument();
  });

  describe("a long table", () => {
    const rows = Array.from({ length: 60 }, (_, i) => ({ month: `row ${i + 1}`, count: i + 1 }));

    it("shows 25 rows and a pager, and no pager when it is short", () => {
      const { unmount } = render(<RowsTable unit="count" rows={rows} />);
      expect(screen.getAllByRole("row")).toHaveLength(26);
      expect(screen.getByText("1 to 25 of 60")).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "Page 3" }));
      expect(screen.getByText("51 to 60 of 60")).toBeInTheDocument();
      expect(screen.getByText("row 51")).toBeInTheDocument();
      unmount();
      render(<RowsTable unit="count" rows={rows.slice(0, 8)} />);
      expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
    });
  });
});
