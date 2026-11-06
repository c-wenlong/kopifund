import "@testing-library/jest-dom/vitest";
import { render, screen, within } from "@testing-library/react";
import { TxnRowsTable, type TxnRow } from "./TxnRowsTable";

const row = (key: string, over: Partial<TxnRow> = {}): TxnRow =>
  ({ key, date: "2026-09-03", amount: -1048, currency: "SGD", payee: "Grab", text: "GRAB*", category: "Transport", ...over });

describe("TxnRowsTable", () => {
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

  it("reads each row as its cells in the header's order, the payee falling back to the statement text", () => {
    render(<TxnRowsTable list="t" rows={[row("a"), row("b", { payee: "", text: "KIOSK", category: "" })]} />);
    const [, first, second] = screen.getAllByRole("row");
    expect(within(first).getAllByRole("cell").map((c) => c.textContent)).toEqual(["3 Sep 2026", "Grab", "Transport", "−S$10.48"]);
    expect(within(second).getAllByRole("cell").map((c) => c.textContent)).toEqual(["3 Sep 2026", "KIOSK", "no rule", "−S$10.48"]);
  });
});
