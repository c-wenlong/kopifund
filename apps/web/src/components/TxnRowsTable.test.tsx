import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import { TxnRowsTable, type TxnRow } from "./TxnRowsTable";

const row = (key: string, over: Partial<TxnRow> = {}): TxnRow =>
  ({ key, date: "2026-09-03", amount: -1048, currency: "SGD", payee: "Grab", text: "GRAB*", category: "Transport", ...over });

describe("TxnRowsTable", () => {
  beforeEach(() => window.localStorage.clear());

  it("has a header row and writes every amount through formatAmount", () => {
    render(<TxnRowsTable list="t" rows={[
      row("a"),
      row("b", { currency: "SEK", amount: -1234567, payee: "" , text: "KIOSK", category: "" }),
      row("c", { currency: "USD", amount: 5, payee: "Refund" }),
      row("d", { amount: 123456789 }),
    ]} />);
    for (const h of ["Date", "Payee", "Category", "Amount"]) expect(screen.getByRole("columnheader", { name: h })).toBeInTheDocument();
    // a true minus, grouping and the currency in front: never a raw toFixed or a hyphen
    expect(screen.getByText("−S$10.48")).toBeInTheDocument();
    expect(screen.getByText(/^−SEK\s12,345\.67$/)).toBeInTheDocument();
    expect(screen.getByText(/^USD\s0\.05$/)).toBeInTheDocument();
    expect(screen.getByText("S$1,234,567.89")).toBeInTheDocument();
    expect(screen.getByText("KIOSK")).toBeInTheDocument();
    expect(screen.getByText("no rule")).toBeInTheDocument();
  });

  it("keeps an amount on one line, and stacks the row under 768px", () => {
    render(<TxnRowsTable list="t" rows={[row("a")]} />);
    const amount = screen.getByText("−S$10.48");
    expect(amount).toHaveClass("whitespace-nowrap");
    const tr = amount.closest("tr")!;
    expect(tr.className).toContain("max-md:grid");
    expect(screen.getByRole("table").className).toContain("max-md:block");
  });
});
