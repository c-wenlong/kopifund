import { describe, expect, it } from "vitest";
import { plural, sentence } from "./words";

describe("plural", () => {
  it("agrees with the count and groups its thousands", () => {
    expect(plural(1, "row")).toBe("1 row");
    expect(plural(0, "row")).toBe("0 rows");
    expect(plural(1500, "row")).toBe("1,500 rows");
    expect(plural(2, "category", "categories")).toBe("2 categories");
  });
});

describe("sentence", () => {
  it("opens with a capital and leaves the rest", () => {
    expect(sentence("checked on a real statement")).toBe("Checked on a real statement");
    expect(sentence("")).toBe("");
    expect(sentence("PDF read")).toBe("PDF read");
  });
});
