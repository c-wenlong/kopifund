import { fireEvent, render, screen, within } from "@testing-library/react";
import { useState } from "react";
import type { GrowthInputs } from "../api";
import { InputsEditor, placeErrors, sameInputs, toBody, toDraft, type Draft } from "./ProjectionInputs";

// invented figures, in cents
const saved: GrowthInputs = {
  income: [
    { name: "Main job", type: "employment", from: "2024-01-01", to: "2026-06-30", gross_minor: 600_050, cpf: true, bonus_months: 1.5, bonus_month: 12 },
    { name: "Tutoring", type: "side", from: "2025-02-01", to: null, gross_minor: 40_000, cpf: false, bonus_months: null, bonus_month: null },
  ],
  investing: [{ from: null, amount_minor: 30_000 }, { from: "2026-07", amount_minor: 45_050 }],
  projection: {
    pessimistic_pct: "2", expected_pct: "5", optimistic_pct: "8", inflation_pct: "2.5", salary_growth_pct: "3", sums_growth_pct: "3.5",
    spend_growth_pct: null, horizon: "20y", dollars: "today", spend_minor: 250_000, milestone_minor: null,
  },
};

describe("toDraft and toBody", () => {
  it("shows cents as the text a box holds, and sends the same cents back", () => {
    const draft = toDraft(saved);
    expect(draft.income[0]).toMatchObject({ name: "Main job", gross: "6000.50", bonus_months: "1.5", bonus_month: "12", cpf: true });
    expect(draft.investing).toEqual([{ from: "", amount: "300" }, { from: "2026-07", amount: "450.50" }]);
    expect(draft.projection).toMatchObject({ spend: "2500", milestone: "", spend_growth_pct: "", horizon: "20y" });
    const body = toBody(draft);
    expect(body.income[0]).toMatchObject({ gross_minor: 600_050, to: "2026-06-30", bonus_months: "1.5", bonus_month: "12" });
    expect(body.investing).toEqual([{ from: null, amount_minor: 30_000 }, { from: "2026-07", amount_minor: 45_050 }]);
    expect(body.projection).toMatchObject({ spend_minor: 250_000, milestone_minor: null, spend_growth_pct: null, expected_pct: "5" });
  });

  it("reads typed text the way the server will: commas out, empty as none, side income with no bonus", () => {
    const draft = toDraft(saved);
    draft.income[0].gross = "6,000.5";
    draft.income[1].bonus_months = "2";
    draft.income[1].bonus_month = "3";
    draft.projection.milestone = "1,000,000";
    const body = toBody(draft);
    expect(body.income[0].gross_minor).toBe(600_050);
    expect(body.income[1]).toMatchObject({ bonus_months: null, bonus_month: null });
    expect(body.projection.milestone_minor).toBe(100_000_000);
  });

  it("passes on text that is not an amount so the server can refuse it", () => {
    const draft = toDraft(saved);
    draft.income[0].gross = "1k5";
    draft.projection.spend = "2500.505";
    const body = toBody(draft);
    expect(body.income[0].gross_minor).toBe("1k5");
    expect(body.projection.spend_minor).toBe("2500.505");
  });

  it("calls two drafts the same when they send the same figures, and not when one figure differs", () => {
    const a = toDraft(saved);
    const b = toDraft(saved);
    b.income[0].gross = "6,000.50";
    expect(sameInputs(a, b)).toBe(true);
    b.income[0].gross = "6000.51";
    expect(sameInputs(a, b)).toBe(false);
  });
});

describe("placeErrors", () => {
  const draft = toDraft(saved);

  it("reads a figure that cannot be an amount on the spot, under the box it is in", () => {
    const d: Draft = structuredClone(draft);
    d.income[0].gross = "1k5";
    d.investing[1].amount = "300.505";
    d.projection.expected_pct = "lots";
    d.projection.inflation_pct = "-1.5";
    const { fields, rest } = placeErrors(d, null);
    expect(fields["income.0.gross"]).toBe("Gross a month must be a number, like 6,000.50");
    expect(fields["investing.1.amount"]).toBe("Amount a month is in dollars and cents, no finer");
    expect(fields["p.expected_pct"]).toBe("Expected return must be a number, like 6.5");
    // a rate may be under zero
    expect(fields["p.inflation_pct"]).toBeUndefined();
    expect(rest).toBeNull();
  });

  it("puts the server's refusal against the income box it names", () => {
    const { fields, rest } = placeErrors(draft, "Income row 2 (Tutoring): gross must be above zero");
    expect(fields["income.1.gross"]).toBe("Gross must be above zero");
    expect(rest).toBeNull();
    expect(placeErrors(draft, "Income row 1 (Main job): the bonus month must be 1 to 12").fields["income.0.bonus_month"]).toBeDefined();
  });

  it("puts a refusal about an investing step or a setting against its box", () => {
    expect(placeErrors(draft, "Investing step 2: the amount must be above zero").fields["investing.1.amount"]).toBe("The amount must be above zero");
    const refused = "Inflation must be between -50 and 50";
    expect(placeErrors(draft, refused)).toMatchObject({ fields: { "p.inflation_pct": refused }, rest: null });
  });

  it("hands back a refusal it cannot place, or one about a row that is not there, for the top of the card", () => {
    expect(placeErrors(draft, "The record is locked.")).toMatchObject({ rest: "The record is locked." });
    expect(placeErrors(draft, "Income row 9: gross must be above zero").rest).toBe("Income row 9: gross must be above zero");
  });
});

describe("InputsEditor", () => {
  function Editor({ start = toDraft(saved), errors = {}, seen }: { start?: Draft; errors?: Record<string, string>; seen?: (d: Draft) => void }) {
    const [draft, setDraft] = useState(start);
    return <InputsEditor draft={draft} errors={errors} hint={null} inflation="2.5"
      onChange={(d) => { seen?.(d); setDraft(d); }} />;
  }

  it("draws a row for each income and a step for each investing transfer, with what the record holds", () => {
    render(<Editor />);
    expect(screen.getByRole("row", { name: "Income row 1" })).toBeInTheDocument();
    expect(screen.getByRole("row", { name: "Income row 2" })).toBeInTheDocument();
    expect(screen.getByLabelText("Name, income row 1")).toHaveValue("Main job");
    expect(screen.getByLabelText("Gross a month (S$), income row 1")).toHaveValue("6,000.50");
    expect(screen.getByRole("group", { name: "Investing step 2" })).toBeInTheDocument();
    expect(screen.getByLabelText("Spending a month (S$)")).toHaveValue("2,500");
  });

  it("sends the whole draft up with only the typed figure changed", () => {
    const seen = vi.fn();
    render(<Editor seen={seen} />);
    fireEvent.change(screen.getByLabelText("Gross a month (S$), income row 2"), { target: { value: "450" } });
    const sent = seen.mock.calls.at(-1)![0] as Draft;
    expect(sent.income[1].gross).toBe("450");
    expect(sent.income[0].gross).toBe("6000.50");
    expect(sent.investing).toHaveLength(2);
  });

  it("adds an income row that starts the day after the last one ends, and removes a row on request", () => {
    const seen = vi.fn();
    render(<Editor start={{ ...toDraft(saved), income: [toDraft(saved).income[0]] }} seen={seen} />);
    fireEvent.click(screen.getByRole("button", { name: /Add income/ }));
    const added = (seen.mock.calls.at(-1)![0] as Draft).income;
    expect(added).toHaveLength(2);
    expect(added[1]).toMatchObject({ from: "2026-07-01", type: "employment", cpf: true, gross: "" });
    fireEvent.click(screen.getByRole("button", { name: "Remove income row 1" }));
    expect((seen.mock.calls.at(-1)![0] as Draft).income).toHaveLength(1);
    expect(screen.getAllByRole("row", { name: /Income row/ })).toHaveLength(1);
  });

  it("adds and removes investing steps", () => {
    render(<Editor />);
    fireEvent.click(screen.getByRole("button", { name: /Add step/ }));
    expect(screen.getByRole("group", { name: "Investing step 3" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Remove investing step 1" }));
    expect(screen.queryByRole("group", { name: "Investing step 3" })).not.toBeInTheDocument();
    expect(screen.getAllByRole("group", { name: /^Investing step/ })).toHaveLength(2);
  });

  it("puts what is wrong under the row it is about, and marks the box", () => {
    render(<Editor errors={{ "income.1.gross": "Gross a month must be a number, like 6,000.50" }} />);
    const box = screen.getByLabelText("Gross a month (S$), income row 2");
    expect(box).toHaveAccessibleDescription("Gross a month must be a number, like 6,000.50");
    expect(within(screen.getByRole("row", { name: "Income row 2" }).parentElement!).getByText(/must be a number/)).toBeInTheDocument();
  });

  it("shows a side income with no bonus box, and a job with one", () => {
    render(<Editor />);
    expect(screen.getByLabelText("Bonus (months of pay), income row 1")).toBeInTheDocument();
    expect(screen.queryByLabelText("Bonus (months of pay), income row 2")).not.toBeInTheDocument();
  });
});
