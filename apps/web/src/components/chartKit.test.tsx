import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";
import { cutName, REST_COLOUR, SERIES_COLOURS, seriesColour, Swatch, TipBox, TipRow, useHidden } from "./chartKit";

describe("the series colours (G18)", () => {
  it("are twelve different colours, so up to twelve series never share one", () => {
    expect(SERIES_COLOURS).toHaveLength(12);
    expect(new Set(SERIES_COLOURS).size).toBe(12);
    expect(Array.from({ length: 12 }, (_, i) => seriesColour(i))).toEqual([...SERIES_COLOURS]);
  });

  it("start over only after the twelfth, and the folded rest is none of them", () => {
    expect(seriesColour(12)).toBe(SERIES_COLOURS[0]);
    expect(SERIES_COLOURS).not.toContain(REST_COLOUR);
  });

  it("give nine bucket names nine different colours", () => {
    const names = ["Food", "Home and bills", "Getting around", "Health and fitness", "Growth", "Lifestyle", "Travel", "Giving", "Other"];
    expect(new Set(names.map((_, i) => seriesColour(i))).size).toBe(names.length);
  });
});

describe("Swatch", () => {
  const first = (el: React.ReactElement) => render(el).container.firstElementChild as HTMLElement;

  it("is a square for a bar, a line for a line or a dash, a round dot for a point", () => {
    expect(first(<Swatch colour="red" />).className).toContain("rounded-sm");
    expect(first(<Swatch colour="red" line />).className).toContain("border-t-2");
    expect(first(<Swatch colour="red" dashed />).className).toContain("border-dashed");
    expect(first(<Swatch colour="red" dot />).className).toContain("rounded-full");
  });

  it("is a ring for an outline round a point, dashed when asked", () => {
    const ring = first(<Swatch colour="red" ring />);
    expect(ring.className).toContain("rounded-full");
    expect(ring.className).not.toContain("border-dashed");
    expect(ring).toHaveStyle({ borderColor: "red" });
    expect(first(<Swatch colour="red" ring dashed />).className).toContain("border-dashed");
  });
});

describe("TipBox", () => {
  it("has one width for every chart: at least 13rem, at most 22rem and a phone screen's", () => {
    const { container } = render(<TipBox heading="Oct 26">x</TipBox>);
    const cls = (container.firstElementChild as HTMLElement).className;
    expect(cls).toContain("min-w-52");
    expect(cls).toContain("max-w-[min(22rem,calc(100vw-2rem))]");
  });

  it("writes a total in cents as money, takes one already written, and shows a hint under a rule", () => {
    const { container, rerender } = render(<TipBox heading="Oct 26" total={123_400} hint="Click a bar">x</TipBox>);
    expect(container).toHaveTextContent("S$1,234");
    expect(container).toHaveTextContent("Click a bar");
    rerender(<TipBox heading="Oct 26" total="5.2 mo">x</TipBox>);
    expect(container).toHaveTextContent("5.2 mo");
    expect(container).not.toHaveTextContent("Click");
  });
});

describe("TipRow's text values", () => {
  it("shows a figure written already, a zero of it included, and a plain row has no gap for a swatch when bare", () => {
    const { container, rerender } = render(<TipRow name="Days" value="0" />);
    expect(container).toHaveTextContent("Days0");
    expect(container.querySelector("[aria-hidden]")).not.toBeNull();
    rerender(<TipRow name="Days" value="0" bare />);
    expect(container.querySelector("[aria-hidden]")).toBeNull();
  });
});

describe("cutName", () => {
  it("cuts a long name to the length with an ellipsis, and leaves a short one", () => {
    expect(cutName("Singapore Airlines", 8)).toBe("Singapo…");
    expect(cutName("Grab", 8)).toBe("Grab");
    expect(cutName("12345678", 8)).toBe("12345678");
  });
});

describe("useHidden", () => {
  const hiddenOf = (r: { current: { hidden: ReadonlySet<string> } }) => [...r.current.hidden].sort();

  it("hides and shows a series, and never the last one showing", () => {
    const { result } = renderHook(() => useHidden(["a", "b", "c"]));
    act(() => result.current.toggle("a"));
    act(() => result.current.toggle("b"));
    expect(hiddenOf(result)).toEqual(["a", "b"]);
    act(() => result.current.toggle("c"));   // the last one showing
    expect(hiddenOf(result)).toEqual(["a", "b"]);
    act(() => result.current.toggle("a"));
    expect(hiddenOf(result)).toEqual(["b"]);
  });

  it("forgets a series that is no longer drawn, so an edit cannot leave nothing showing", () => {
    // Cash and CPF hidden, only Tiger showing; then an edit removes Tiger
    const { result, rerender } = renderHook(({ all }) => useHidden(all), { initialProps: { all: ["cash", "tiger", "cpf"] } });
    act(() => result.current.toggle("cash"));
    act(() => result.current.toggle("cpf"));
    expect(hiddenOf(result)).toEqual(["cash", "cpf"]);
    rerender({ all: ["cash", "cpf"] });
    expect(hiddenOf(result)).toEqual([]);
    // and when the series come back the old choice does not return with them
    rerender({ all: ["cash", "tiger", "cpf"] });
    expect(hiddenOf(result)).toEqual([]);
  });

  it("shows everything when what is left of the hidden set would cover every series", () => {
    const { result, rerender } = renderHook(({ all }) => useHidden(all), { initialProps: { all: ["a", "b", "c"] } });
    act(() => result.current.toggle("a"));
    act(() => result.current.toggle("b"));
    rerender({ all: ["a", "b"] });
    expect(hiddenOf(result)).toEqual([]);
  });
});

describe("TipRow", () => {
  it("leaves out a series at zero, unless asked to keep it", () => {
    const { container, rerender } = render(<TipRow name="Cash" value={0} />);
    expect(container).toBeEmptyDOMElement();
    rerender(<TipRow name="Cash" value={0} keepZero />);
    expect(container).toHaveTextContent("Cash");
    rerender(<TipRow name="Cash" value={12345} />);
    expect(container).toHaveTextContent("S$123");
  });
});
