import "@testing-library/jest-dom/vitest";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Pager, clampPage, compactItems, pageCount, pageItems, usePager } from "./Pager";
import { stubMatchMedia } from "../test-helpers";

const range = (n: number) => Array.from({ length: n }, (_, i) => i + 1);

describe("page math", () => {
  it("counts pages, and always has at least one", () => {
    expect(pageCount(0, 25)).toBe(1);
    expect(pageCount(25, 25)).toBe(1);
    expect(pageCount(26, 25)).toBe(2);
    expect(pageCount(312, 25)).toBe(13);
  });

  it("clamps a page into the range that exists", () => {
    expect(clampPage(0, 100, 25)).toBe(1);
    expect(clampPage(9, 100, 25)).toBe(4);
    expect(clampPage(3, 0, 25)).toBe(1);
  });

  it("lists every page when there are seven or fewer", () => {
    expect(pageItems(1, 1)).toEqual([1]);
    expect(pageItems(4, 7)).toEqual(range(7));
  });

  it("puts ellipses where a long run is cut, never more than seven entries", () => {
    expect(pageItems(1, 13)).toEqual([1, 2, 3, 4, 5, "gap-after", 13]);
    expect(pageItems(4, 13)).toEqual([1, 2, 3, 4, 5, "gap-after", 13]);
    expect(pageItems(5, 13)).toEqual([1, "gap-before", 4, 5, 6, "gap-after", 13]);
    expect(pageItems(6, 13)).toEqual([1, "gap-before", 5, 6, 7, "gap-after", 13]);
    expect(pageItems(10, 13)).toEqual([1, "gap-before", 9, 10, 11, 12, 13]);
    expect(pageItems(13, 13)).toEqual([1, "gap-before", 9, 10, 11, 12, 13]);
    for (let p = 1; p <= 40; p++) expect(pageItems(p, 40)).toHaveLength(7);
  });

  it("never skips a single page behind an ellipsis", () => {
    for (let pages = 8; pages <= 30; pages++) {
      for (let p = 1; p <= pages; p++) {
        const items = pageItems(p, pages);
        items.forEach((item, i) => {
          if (typeof item !== "string") return;
          const before = items[i - 1] as number;
          const after = items[i + 1] as number;
          expect(after - before).toBeGreaterThan(2);
        });
        expect(items).toContain(p);
      }
    }
  });
});

// a list of `rows` items behind a filter, drawn the way a page does
function Harness({ initial = 312, list = "test", urlKey }: { initial?: number; list?: string; urlKey?: string }) {
  const [rows, setRows] = useState(initial);
  const [filter, setFilter] = useState("");
  const pager = usePager(list, rows, filter, 1, { urlKey });
  const items = pager.slice(range(rows));
  return (
    <>
      <output data-testid="items">{items.length ? `${items[0]}-${items[items.length - 1]}` : "none"}</output>
      <input aria-label="Filter" value={filter} onChange={(e) => setFilter(e.target.value)} />
      <button type="button" onClick={() => setRows(30)}>Shrink</button>
      <button type="button" onClick={() => setRows(312)}>Grow</button>
      <Pager pager={pager} label="Test pages" />
    </>
  );
}

const shown = () => screen.getByTestId("items").textContent;
const nav = () => screen.getByRole("navigation", { name: "Test pages" });

describe("Pager", () => {
  it("says where you are, as 1 to 25 of 312 with no dashes", () => {
    render(<Harness />);
    expect(within(nav()).getByRole("status")).toHaveTextContent("1 to 25 of 312");
    expect(shown()).toBe("1-25");
    fireEvent.click(within(nav()).getByRole("button", { name: "Next page" }));
    expect(within(nav()).getByRole("status")).toHaveTextContent("26 to 50 of 312");
    expect(shown()).toBe("26-50");
  });

  it("marks the current page and moves with the page numbers, with ellipses on a long run", () => {
    render(<Harness />);
    const current = () => within(nav()).getByRole("button", { current: "page" });
    expect(current()).toHaveAccessibleName("Page 1");
    expect(within(nav()).getByRole("button", { name: "Page 13" })).toBeInTheDocument();
    expect(within(nav()).queryByRole("button", { name: "Page 7" })).not.toBeInTheDocument();
    fireEvent.click(within(nav()).getByRole("button", { name: "Page 4" }));
    fireEvent.click(within(nav()).getByRole("button", { name: "Page 5" }));
    expect(current()).toHaveAccessibleName("Page 5");
    expect(shown()).toBe("101-125");
    // 1 ... 4 5 6 ... 13
    expect(within(nav()).getAllByRole("button", { name: /^Page \d+$/ }).map((b) => b.textContent)).toEqual(["1", "4", "5", "6", "13"]);
    expect(nav().textContent).toContain("…");
  });

  describe("on a phone", () => {
    const phone = (on: boolean) => stubMatchMedia((q) => on && q.includes("max-width"));

    it("shows the first, current and last page, with ellipses over the rest", () => {
      phone(true);
      render(<Harness initial={4700} />);   // 188 pages of 25
      const numbers = () => within(nav()).getAllByRole("button", { name: /^Page \d+$/ }).map((b) => b.textContent);
      expect(numbers()).toEqual(["1", "188"]);
      fireEvent.click(within(nav()).getByRole("button", { name: "Page 188" }));
      expect(numbers()).toEqual(["1", "188"]);
      expect(within(nav()).getByRole("button", { current: "page" })).toHaveTextContent("188");
      fireEvent.click(within(nav()).getByRole("button", { name: "Previous page" }));
      expect(numbers()).toEqual(["1", "187", "188"]);
      fireEvent.click(within(nav()).getByRole("button", { name: "Previous page" }));
      expect(numbers()).toEqual(["1", "186", "187", "188"]);
    });

    it("jumps to any page from the Go to page box, clamped to the pages there are", () => {
      phone(true);
      render(<Harness initial={4700} />);
      const box = within(nav()).getByRole("textbox", { name: "Go to page" });
      expect(within(nav()).getByRole("button", { name: "Go" })).toBeDisabled();
      fireEvent.change(box, { target: { value: "94" } });
      fireEvent.submit(box);
      expect(shown()).toBe("2326-2350");
      expect(within(nav()).getByRole("button", { current: "page" })).toHaveTextContent("94");
      expect(box).toHaveValue("");
      fireEvent.change(box, { target: { value: "9999" } });
      fireEvent.submit(box);
      expect(within(nav()).getByRole("button", { current: "page" })).toHaveTextContent("188");
      // letters never get in
      fireEvent.change(box, { target: { value: "a1b2" } });
      expect(box).toHaveValue("12");
    });

    it("has no Go to page box on a wide screen, where the numbers do the job", () => {
      phone(false);
      render(<Harness initial={4700} />);
      expect(within(nav()).queryByRole("textbox", { name: "Go to page" })).toBeNull();
    });
  });

  it("builds the phone's compact page list, never hiding a single page behind an ellipsis", () => {
    expect(compactItems(1, 188)).toEqual([1, "gap-after", 188]);
    expect(compactItems(94, 188)).toEqual([1, "gap-before", 94, "gap-after", 188]);
    expect(compactItems(188, 188)).toEqual([1, "gap-before", 188]);
    expect(compactItems(3, 5)).toEqual([1, 2, 3, 4, 5]);
    expect(compactItems(1, 1)).toEqual([1]);
    expect(compactItems(2, 3)).toEqual([1, 2, 3]);
  });

  it("disables previous on the first page and next on the last", () => {
    render(<Harness initial={60} />);
    const previous = () => within(nav()).getByRole("button", { name: "Previous page" });
    const next = () => within(nav()).getByRole("button", { name: "Next page" });
    expect(previous()).toBeDisabled();
    fireEvent.click(next());
    fireEvent.click(next());
    expect(shown()).toBe("51-60");
    expect(next()).toBeDisabled();
    fireEvent.click(previous());
    expect(shown()).toBe("26-50");
  });

  it("offers 10, 25, 50 and 100 rows per page, 25 to begin with", () => {
    render(<Harness />);
    const picker = within(nav()).getByRole("button", { name: "Rows per page" });
    expect(picker).toHaveTextContent("25 per page");
    fireEvent.click(picker);
    expect(screen.getAllByRole("option").map((o) => o.textContent)).toEqual(["10 per page", "25 per page", "50 per page", "100 per page"]);
  });

  it("goes back to page 1 when the page size changes, and remembers the size per list", () => {
    const { unmount } = render(<Harness />);
    fireEvent.click(within(nav()).getByRole("button", { name: "Page 4" }));
    expect(shown()).toBe("76-100");
    fireEvent.click(within(nav()).getByRole("button", { name: "Rows per page" }));
    fireEvent.mouseDown(screen.getByRole("option", { name: "50 per page" }));
    expect(shown()).toBe("1-50");
    expect(within(nav()).getByRole("status")).toHaveTextContent("1 to 50 of 312");
    expect(window.localStorage.getItem("kopifund.pagesize.test")).toBe("50");
    unmount();
    // the next visit opens with 50; another list is unaffected
    const again = render(<Harness />);
    expect(shown()).toBe("1-50");
    again.unmount();
    render(<Harness list="other" />);
    expect(shown()).toBe("1-25");
  });

  it("brings the list's top back into view after a size change from below it", () => {
    render(<Harness />);
    // the element just above the bar stands in for the list, scrolled past the top of the screen
    const list = nav().previousElementSibling as HTMLElement;
    list.getBoundingClientRect = () => ({ top: -400 }) as DOMRect;
    const scroll = vi.fn();
    list.scrollIntoView = scroll;
    fireEvent.click(within(nav()).getByRole("button", { name: "Rows per page" }));
    fireEvent.mouseDown(screen.getByRole("option", { name: "100 per page" }));
    expect(shown()).toBe("1-100");
    expect(scroll).toHaveBeenCalledWith({ block: "start" });
  });

  it("ignores a remembered size that is not on offer", () => {
    window.localStorage.setItem("kopifund.pagesize.test", "7");
    render(<Harness />);
    expect(shown()).toBe("1-25");
  });

  it("goes back to page 1 when the filter changes", () => {
    render(<Harness />);
    fireEvent.click(within(nav()).getByRole("button", { name: "Page 3" }));
    expect(shown()).toBe("51-75");
    fireEvent.change(screen.getByRole("textbox", { name: "Filter" }), { target: { value: "x" } });
    expect(shown()).toBe("1-25");
  });

  it("clamps the page when the total shrinks", () => {
    render(<Harness />);
    fireEvent.click(within(nav()).getByRole("button", { name: "Page 4" }));
    fireEvent.click(within(nav()).getByRole("button", { name: "Page 5" }));
    expect(shown()).toBe("101-125");
    fireEvent.click(screen.getByRole("button", { name: "Shrink" }));
    // 30 rows is two pages of 25: the last page, not an empty one
    expect(shown()).toBe("26-30");
    expect(within(nav()).getByRole("status")).toHaveTextContent("26 to 30 of 30");
  });

  it("keeps a clamped page, so a total that grows again does not bring back the old one", () => {
    render(<Harness />);
    fireEvent.click(within(nav()).getByRole("button", { name: "Page 4" }));
    fireEvent.click(within(nav()).getByRole("button", { name: "Page 5" }));
    fireEvent.click(screen.getByRole("button", { name: "Shrink" }));
    expect(shown()).toBe("26-30");
    fireEvent.click(screen.getByRole("button", { name: "Grow" }));
    expect(shown()).toBe("26-50");
  });

  describe("keeping the page in the address", () => {
    const at = () => window.location.pathname + window.location.search;
    beforeEach(() => window.history.replaceState(null, "", "/transactions"));
    afterEach(() => window.history.replaceState(null, "", "/"));

    it("starts on the page the address names, and writes each page it moves to", () => {
      window.history.replaceState(null, "", "/transactions?page=3&q=food");
      render(<Harness urlKey="page" />);
      expect(shown()).toBe("51-75");
      fireEvent.click(within(nav()).getByRole("button", { name: "Next page" }));
      expect(at()).toBe("/transactions?page=4&q=food");
      fireEvent.click(within(nav()).getByRole("button", { name: "Page 1" }));
      // page 1 is the bare path; other keys stay
      expect(at()).toBe("/transactions?q=food");
      expect(shown()).toBe("1-25");
    });

    it("does not add an entry to the history, so Back leaves the list rather than paging back", () => {
      const before = window.history.length;
      render(<Harness urlKey="page" />);
      fireEvent.click(within(nav()).getByRole("button", { name: "Next page" }));
      expect(window.history.length).toBe(before);
    });

    it("goes back to page 1, and off the address, when the filter changes", () => {
      window.history.replaceState(null, "", "/transactions?page=3");
      render(<Harness urlKey="page" />);
      fireEvent.change(screen.getByRole("textbox", { name: "Filter" }), { target: { value: "x" } });
      expect(shown()).toBe("1-25");
      expect(at()).toBe("/transactions");
    });

    it("follows the address when it changes under the list (Back, or a link to the bare path)", () => {
      window.history.replaceState(null, "", "/transactions?page=3");
      render(<Harness urlKey="page" />);
      act(() => {
        window.history.replaceState(null, "", "/transactions?page=5");
        window.dispatchEvent(new PopStateEvent("popstate"));
      });
      expect(shown()).toBe("101-125");
      act(() => {
        window.history.replaceState(null, "", "/transactions");
        window.dispatchEvent(new PopStateEvent("popstate"));
      });
      expect(shown()).toBe("1-25");
    });

    it("clamps a page past the end, and ignores nonsense", () => {
      window.history.replaceState(null, "", "/transactions?page=99");
      const { unmount } = render(<Harness urlKey="page" />);
      expect(shown()).toBe("301-312");
      expect(at()).toBe("/transactions?page=13");
      unmount();
      window.history.replaceState(null, "", "/transactions?page=abc");
      render(<Harness urlKey="page" />);
      expect(shown()).toBe("1-25");
    });

    it("leaves the address alone without a urlKey", () => {
      window.history.replaceState(null, "", "/transactions?page=3");
      render(<Harness />);
      expect(shown()).toBe("1-25");
      fireEvent.click(within(nav()).getByRole("button", { name: "Next page" }));
      expect(at()).toBe("/transactions?page=3");
    });
  });

  it("shows nothing when ten rows or fewer fit on one page", () => {
    render(<Harness initial={10} />);
    expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
    expect(shown()).toBe("1-10");
  });

  it("still works when storage is blocked", () => {
    const get = Storage.prototype.getItem;
    const set = Storage.prototype.setItem;
    Storage.prototype.getItem = () => { throw new Error("blocked"); };
    Storage.prototype.setItem = () => { throw new Error("blocked"); };
    try {
      render(<Harness />);
      fireEvent.click(within(nav()).getByRole("button", { name: "Rows per page" }));
      fireEvent.mouseDown(screen.getByRole("option", { name: "100 per page" }));
      expect(shown()).toBe("1-100");
    } finally {
      Storage.prototype.getItem = get;
      Storage.prototype.setItem = set;
    }
  });
});
