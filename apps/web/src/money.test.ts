import { axisSgd, centsText, formatAmount, formatSgd, isAmountText, parseCents, unformatMoney, wholeDollars } from "./money";
import { fmt } from "./stats/hint";
import { CENTS_UNIT } from "./stats/money";
import { isMoneyText } from "./components/MoneyInput";

describe("formatSgd", () => {
  it("formats whole dollars from cents", () => {
    expect(formatSgd(4_929_900)).toBe("S$49,299");
  });
  it("keeps cents when asked", () => {
    expect(formatSgd(123_405, { decimals: true })).toBe("S$1,234.05");
  });
  it("puts a true minus sign (U+2212) before the currency", () => {
    expect(formatSgd(-50_000)).toBe("−S$500");
    expect(formatSgd(-1_200, { decimals: true })).toBe("−S$12.00");
    expect(formatSgd(-50_000)).not.toContain("-");
  });
  it("has no break opportunity between the sign and the amount", () => {
    // U+2212 is a prefix character for line breaking, a hyphen is not
    expect(formatSgd(-1_200, { decimals: true })).toMatch(/^−S\$12\.00$/);
  });
  // Audit: a throw while a page draws blanked the app, so a bad value is said in the console and drawn as best it can be
  it("rounds a float to whole cents and reports it, rather than throwing", () => {
    const error = vi.spyOn(console, "error").mockImplementation(() => {});
    expect(formatSgd(1_234.6, { decimals: true })).toBe("S$12.35");
    expect(error).toHaveBeenCalledWith(expect.stringMatching(/integer cents, got 1234.6/));
  });
  it("shows a question mark for what is not a number at all", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    expect(formatSgd(NaN)).toBe("S$?");
    expect(formatSgd(undefined as unknown as number)).toBe("S$?");
  });
});

describe("formatAmount", () => {
  it("writes SGD as S$ with cents", () => {
    expect(formatAmount(570, "SGD")).toBe("S$5.70");
    expect(formatAmount(-570, "SGD")).toBe("−S$5.70");
  });
  it("groups other currencies like SGD and keeps the code and amount together", () => {
    expect(formatAmount(941_205, "SEK")).toBe("SEK 9,412.05");
    expect(formatAmount(123_456_789, "USD")).toBe("USD 1,234,567.89");
    expect(formatAmount(900, "EUR")).toBe("EUR 9.00");
  });
  it("signs a negative foreign amount with the minus before the code", () => {
    expect(formatAmount(-1_200, "SEK")).toBe("−SEK 12.00");
  });
  it("rounds a float to whole cents rather than throwing", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    expect(formatAmount(12.5, "SEK")).toBe("SEK\u00a00.13");
    expect(formatAmount(Infinity, "SEK")).toBe("SEK\u00a0?");
  });
});

describe("axisSgd", () => {
  it("is compact and uses the minus sign below zero", () => {
    expect(axisSgd(50_000)).toBe("S$500");
    expect(axisSgd(1_500_000)).toBe("S$15k");
    expect(axisSgd(-200_000)).toBe("−S$2k");
    expect(axisSgd(-20)).toBe("S$0");
  });
  it("moves to millions once a tick rounds to a thousand k", () => {
    expect(axisSgd(99_996_000)).toBe("S$1M");
    expect(axisSgd(-99_996_000)).toBe("−S$1M");
    expect(axisSgd(99_940_000)).toBe("S$999.4k");
    expect(axisSgd(99_960)).toBe("S$1k");
  });
});

// Audit: one rounding rule for whole dollars, finance/money.py's: half up on the size, away from zero,
// and no minus on what rounds to nothing. Every money figure the app draws goes through formatSgd.
describe("one rounding rule", () => {
  it("rounds a half away from zero on both sides", () => {
    expect(wholeDollars(150)).toBe(2);
    expect(wholeDollars(-150)).toBe(-2);
    expect(wholeDollars(250)).toBe(3);
    expect(wholeDollars(-250)).toBe(-3);
    expect(wholeDollars(149)).toBe(1);
    expect(wholeDollars(-149)).toBe(-1);
    expect(Object.is(wholeDollars(-40), 0)).toBe(true);
  });
  it("writes a refund the same in a tile and in a chart", () => {
    expect(formatSgd(-150)).toBe("−S$2");
    expect(formatSgd(-250)).toBe("−S$3");
    expect(formatSgd(250)).toBe("S$3");
    for (const c of [-150, -250, -40, -50, 0, 49, 50, 123_456, -123_456, 99_950]) {
      expect(fmt(c, CENTS_UNIT), String(c)).toBe(formatSgd(c));
      expect(fmt(c, CENTS_UNIT, true), String(c)).toBe(formatSgd(c, { decimals: true }));
    }
  });
  it("puts no minus on what rounds to nothing", () => {
    expect(formatSgd(-40)).toBe("S$0");
    expect(fmt(-40, CENTS_UNIT)).toBe("S$0");
    expect(formatSgd(-50)).toBe("−S$1");
  });
});

describe("parseCents (G17)", () => {
  it("reads a box's text as exact cents, never through a float", () => {
    expect(parseCents("1,234.5")).toBe(123_450);
    expect(parseCents("1.15")).toBe(115);           // Number("1.15") * 100 is 114.99999999999999
    expect(parseCents("0.29")).toBe(29);
    expect(parseCents("6000.50")).toBe(600_050);
    expect(parseCents(" 2 500 ")).toBe(250_000);
    expect(parseCents("12.")).toBe(1_200);
    expect(parseCents(".5")).toBe(50);
    expect(parseCents("0")).toBe(0);
    expect(parseCents("1.500")).toBe(150);
  });
  it("is null when empty and NaN for what is not dollars and cents", () => {
    expect(parseCents("")).toBeNull();
    expect(parseCents(" , ")).toBeNull();
    for (const bad of ["abc", "1k5", ".", "1.2.3", "-5", "+5", "1.005", "1e3", "NaN", "99999999999999999"]) {
      expect(parseCents(bad), bad).toBeNaN();
    }
  });
  it("round-trips with centsText", () => {
    for (const c of [0, 5, 50, 100, 123_450, 600_050, 100_000_000]) expect(parseCents(centsText(c))).toBe(c);
  });
});

// Audit: the money box and parseCents read one grammar, so a box that passes isMoneyText is one parseCents reads
describe("one money-text grammar", () => {
  it("strips commas and spaces the same way", () => {
    expect(unformatMoney(" 6,000.50 ")).toBe("6000.50");
  });
  it("agrees with parseCents on what is a number", () => {
    const texts = ["12", "12.", "12.5", ".5", "1,234.56", " 2 500 ", "12.345", "12.340", "", " ", ".", "1.2.3", "-5", "1e3", "abc", "S$5", "1k5"];
    for (const t of texts) {
      const c = parseCents(t);
      const finer = /\.\d{2}\d*[1-9]/.test(unformatMoney(t));
      expect(isMoneyText(t), t).toBe(c !== null && (!Number.isNaN(c) || finer));
    }
  });
});

// Audit: the one rule a money box that must hold an amount checks (Settings' thresholds)
describe("isAmountText", () => {
  it("takes what parseCents reads as cents, .5 and 12. included", () => {
    for (const t of ["12", "12.", ".5", "1,234.56", "0", "1.500"]) expect(isAmountText(t), t).toBe(true);
  });
  it("refuses empty, finer than a cent, and what is not dollars and cents", () => {
    for (const t of ["", " ", ".", "12.345", "-5", "abc", "1.2.3"]) expect(isAmountText(t), t).toBe(false);
  });
});

describe("centsText (G17)", () => {
  it("writes whole dollars bare and cents with two decimals", () => {
    expect(centsText(250_000)).toBe("2500");
    expect(centsText(123_450)).toBe("1234.50");
    expect(centsText(5)).toBe("0.05");
    expect(centsText(0)).toBe("0");
    expect(centsText(-1_205)).toBe("-12.05");
  });
  it("rounds a float to whole cents and leaves the box empty for what is not a number", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    expect(centsText(12.5)).toBe("0.13");
    expect(centsText(NaN)).toBe("");
  });
});
