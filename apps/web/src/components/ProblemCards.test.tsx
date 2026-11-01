import { render, screen } from "@testing-library/react";
import { accounts } from "../accounts";
import { ProblemCards } from "./ProblemCards";

const list = accounts([
  { path: "statements/dbs-multiplier", name: "dbs-multiplier", parser: "dbs", wallet: "", title: "DBS Multiplier", bank: "dbs" },
  { path: "statements/youtrip-sek", name: "youtrip-sek", parser: "youtrip", wallet: "SEK", title: "YouTrip", bank: "youtrip" },
]);
const fix = "parse_statements.py exited 1. Nothing is written until it exits 0";

describe("ProblemCards", () => {
  it("puts each account's bank mark before its name when a problem names several (G18)", () => {
    render(<ProblemCards problems={[{ code: "parser-failed", subject: "statements/dbs-multiplier; statements/youtrip-sek", fix }]} accounts={list} />);
    for (const [name, bank] of [["DBS Multiplier", "dbs"], ["YouTrip SEK", "youtrip"]]) {
      const label = screen.getByText(name);
      expect(label.firstElementChild).toHaveAttribute("data-bank", bank);
    }
  });

  it("names a period's account in words alone, with no mark to give it", () => {
    const { container } = render(<ProblemCards problems={[{ code: "block-not-reconciled", subject: "•• 3333 · SGD · 2026-02", fix: "" }]} accounts={list} />);
    expect(screen.getByText(/•• 3333 SGD, Feb 2026/)).toBeInTheDocument();
    expect(container.querySelector("[data-bank]")).toBeNull();
  });
});
