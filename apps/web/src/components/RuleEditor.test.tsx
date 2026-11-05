import { render, screen } from "@testing-library/react";
import { NEW_DRAFT, RuleEditor } from "./RuleEditor";

afterEach(() => vi.restoreAllMocks());

describe("RuleEditor's preview", () => {
  // Audit: the lists were read with `!`, so an answer without them threw while the editor drew
  it("draws an answer that has no sample or earlier rules", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
      new Response(JSON.stringify({ error: null, matches: 3, changes: 2, already: 0 }), { headers: { "Content-Type": "application/json" } }));
    render(
      <RuleEditor rule={null} initial={{ ...NEW_DRAFT, pattern: "KOPI", category: "Food" }} tiers={["merchant"]}
        tierFiles={{ merchant: "merchants.csv" }} categories={["Food"]} kinds={["Merchant"]}
        onSaved={() => {}} onCancel={() => {}} />,
    );
    expect(await screen.findByText("Will change 2 rows")).toBeInTheDocument();
  });
});
