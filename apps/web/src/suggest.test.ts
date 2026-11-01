import { describe, expect, it } from "vitest";
import type { IncomeSuggestion } from "./api";
import { chipKind, chipText, coversLast, dollarsText, incomeText, claims, typedValue, unclaimed } from "./suggest";

// Every name and figure here is invented.
const pay = (over: Partial<IncomeSuggestion> = {}): IncomeSuggestion => ({
  name: "Invented Co", type: "employment", from: "2025-03-14", last: "2026-09-28", take_home: 400_000, gross: 500_000,
  cpf: true, basis: "gross", bonus_months: null, bonus_month: null, ...over,
});
const row = (over: Partial<Parameters<typeof claims>[0][number]> = {}) => ({ name: "", type: "employment", from: "2025-01-01", to: "", gross: "", ...over });

describe("chipKind", () => {
  it("is seen for an empty or zero box, record for one far off, and nothing for one close", () => {
    expect(chipKind(null, 210)).toBe("seen");
    expect(chipKind(0, 210)).toBe("seen");
    expect(chipKind(100, 210)).toBe("record");
    expect(chipKind(400, 210)).toBe("record");
    expect(chipKind(200, 210)).toBeNull();
    expect(chipKind(210, 210)).toBeNull();
  });

  it("offers nothing without a figure, or for text that is not a number", () => {
    expect(chipKind(null, null)).toBeNull();
    expect(chipKind(null, 0)).toBeNull();
    expect(chipKind(NaN, 210)).toBeNull();
  });
});

describe("typedValue", () => {
  it("reads a box's text, or a plan field's cents, as cents", () => {
    expect(typedValue("")).toBeNull();
    expect(typedValue(undefined)).toBeNull();
    expect(typedValue("1,250.5")).toBe(125_050);
    expect(typedValue("1.15")).toBe(115);
    expect(typedValue(4_000)).toBe(4_000);
    expect(typedValue("1k5")).toBeNaN();
  });
});

describe("the words and the figure on a chip", () => {
  it("writes cents as the whole dollars a box takes", () => {
    expect(dollarsText(262_745)).toBe("2627");
    expect(dollarsText(100_000)).toBe("1000");
    expect(dollarsText(55_050)).toBe("551");
    expect(dollarsText(0)).toBe("0");
  });

  it("says Seen for an empty box and Record shows for one that differs", () => {
    expect(chipText("seen", 21_000)).toBe("Seen S$210 a month, use");
    expect(chipText("record", 55_050)).toBe("Record shows S$551 a month, use");
    expect(chipText("seen", 500_000, "gross")).toBe("Seen S$5,000 a month gross, use");
  });

  it("says take-home when that is the figure, and gross with the take-home beside it when it was added back", () => {
    expect(incomeText(pay({ basis: "take_home", gross: 400_000, cpf: false }), "Mar 2025")).toBe("Add Invented Co, S$4,000 a month take-home, from Mar 2025");
    expect(incomeText(pay(), "Mar 2025")).toBe("Add Invented Co, about S$5,000 a month gross (S$4,000 take-home), from Mar 2025");
    expect(incomeText(pay({ type: "side", basis: "side", cpf: false, gross: 90_000 }), "Mar 2025")).toBe("Add Invented Co, S$900 a month, from Mar 2025");
    expect(incomeText(pay({ bonus_months: 1, bonus_month: 12 }), "Mar 2025")).toMatch(/, bonus about 1 month in Dec$/);
  });
});

describe("matching a row to the pay the record saw", () => {
  it("matches by name, ignoring case and spacing", () => {
    expect(claims([row({ name: " invented co " })], [pay()])[0]).not.toBeNull();
    expect(claims([row({ name: "Someone else" })], [pay()])[0]).toBeNull();
  });

  it("matches a name that holds the other, but not a stub too short to mean anything", () => {
    expect(claims([row({ name: "Invented Co, part time" })], [pay()])[0]).not.toBeNull();
    expect(claims([row({ name: "Invented" })], [pay()])[0]).not.toBeNull();
    expect(claims([row({ name: "In" })], [pay()])[0]).toBeNull();
  });

  it("covers the last day the pay arrived only for a row running then, not a planned later one", () => {
    expect(coversLast(row({ from: "2026-01-01" }), pay())).toBe(true);
    expect(coversLast(row({ from: "2026-01-01", to: "2026-12-31" }), pay())).toBe(true);
    expect(coversLast(row({ from: "2026-11-01" }), pay())).toBe(false);
    expect(coversLast(row({ from: "2025-01-01", to: "2025-12-31" }), pay())).toBe(false);
  });

  it("matches an unnamed row of the same kind and size that runs over the same stretch", () => {
    expect(claims([row({ name: "Main job", gross: "5000" })], [pay()])[0]).not.toBeNull();
    expect(claims([row({ name: "Main job", gross: "4,100" })], [pay()])[0]).not.toBeNull();      // near the take-home
    expect(claims([row({ name: "Main job", gross: "2000" })], [pay()])[0]).toBeNull();
    expect(claims([row({ name: "Main job", gross: "5000", type: "side" })], [pay()])[0]).toBeNull();
    expect(claims([row({ name: "Main job", gross: "5000", to: "2025-02-28" })], [pay()])[0]).toBeNull();   // ended before it began
  });

  it("lets one row claim one payer, so adding a payer does not hide another of the same size", () => {
    const a = pay({ name: "Client A", type: "side", gross: 90_000, take_home: 90_000, basis: "side", cpf: false });
    const b = pay({ name: "Client B", type: "side", gross: 90_000, take_home: 90_000, basis: "side", cpf: false });
    // the row added from A's chip carries A's name and A's figure
    const added = row({ name: "Client A", type: "side", gross: "900" });
    expect(claims([added], [a, b])).toEqual([a]);
    expect(unclaimed([added], [a, b]).map((s) => s.name)).toEqual(["Client B"]);
    // an unnamed row of that size takes one of them, not both
    expect(unclaimed([row({ name: "", type: "side", gross: "900" })], [a, b])).toHaveLength(1);
    // several rows may stand for one employer by name
    expect(claims([row({ name: "Invented Co, intern" }), row({ name: "Invented Co, full time" })], [pay()])).toEqual([pay(), pay()]);
  });

  it("leaves unclaimed the pay no row stands for", () => {
    const other = pay({ name: "Other Co", gross: 150_000, take_home: 150_000 });
    expect(unclaimed([row({ name: "Invented Co" })], [pay(), other]).map((s) => s.name)).toEqual(["Other Co"]);
    expect(unclaimed([], [pay(), other])).toHaveLength(2);
  });
});
