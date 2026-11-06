import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { Pager, usePager } from "./components/Pager";
import { pageParam, useUrlPage, useUrlParams } from "./useUrlParams";

// Audit: a list of 312 rows behind a filter kept in the address, paged the way Rules and Transactions page
function Harness({ withFilters = false, ready = true }: { withFilters?: boolean; ready?: boolean }) {
  const [params, patch] = useUrlParams();
  const q = params.get("q") ?? "";
  const urlPage = pageParam(params);
  const pager = usePager("test", ready ? 312 : undefined, q, urlPage);
  useUrlPage(pager, urlPage, patch, withFilters ? { filters: q, ready } : { ready });
  const items = pager.slice(Array.from({ length: 312 }, (_, i) => i + 1));
  return (
    <>
      <output data-testid="items">{items.length ? `${items[0]}-${items[items.length - 1]}` : "none"}</output>
      <input aria-label="Filter" value={q} onChange={(e) => patch({ q: e.target.value, page: null })} />
      <Pager pager={pager} label="Test pages" />
    </>
  );
}

const at = () => window.location.pathname + window.location.search;
const shown = () => screen.getByTestId("items").textContent;
const nav = () => screen.getByRole("navigation", { name: "Test pages" });
const go = (url: string) => act(() => {
  window.history.replaceState(null, "", url);
  window.dispatchEvent(new PopStateEvent("popstate"));
});

describe("pageParam", () => {
  it("reads a whole page of 1 or more, and 1 for none or nonsense", () => {
    expect(pageParam(new URLSearchParams("page=3"))).toBe(3);
    expect(pageParam(new URLSearchParams("page=2.7"))).toBe(2);
    expect(pageParam(new URLSearchParams(""))).toBe(1);
    expect(pageParam(new URLSearchParams("page=abc"))).toBe(1);
    expect(pageParam(new URLSearchParams("page=-4"))).toBe(1);
  });
});

describe("useUrlPage", () => {
  beforeEach(() => window.history.replaceState(null, "", "/transactions"));
  afterEach(() => window.history.replaceState(null, "", "/"));

  it("starts on the page the address names, and writes each page it moves to", () => {
    window.history.replaceState(null, "", "/transactions?page=3&q=");
    render(<Harness />);
    expect(shown()).toBe("51-75");
    fireEvent.click(within(nav()).getByRole("button", { name: "Next page" }));
    expect(at()).toBe("/transactions?page=4&q=");
    fireEvent.click(within(nav()).getByRole("button", { name: "Page 1" }));
    // page 1 is the bare path; other keys stay
    expect(at()).toBe("/transactions?q=");
    expect(shown()).toBe("1-25");
  });

  it("does not add an entry to the history, so Back leaves the list rather than paging back", () => {
    const before = window.history.length;
    render(<Harness />);
    fireEvent.click(within(nav()).getByRole("button", { name: "Next page" }));
    expect(window.history.length).toBe(before);
  });

  it("goes back to page 1, and off the address, when the filter changes", () => {
    window.history.replaceState(null, "", "/transactions?page=3");
    render(<Harness />);
    fireEvent.change(screen.getByRole("textbox", { name: "Filter" }), { target: { value: "x" } });
    expect(shown()).toBe("1-25");
    expect(at()).toBe("/transactions?q=x");
  });

  it("follows the address when it changes under the list (Back, or a link to the bare path)", () => {
    window.history.replaceState(null, "", "/transactions?page=3");
    render(<Harness />);
    go("/transactions?page=5");
    expect(shown()).toBe("101-125");
    go("/transactions");
    expect(shown()).toBe("1-25");
  });

  it("clamps a page past the end into the address", () => {
    window.history.replaceState(null, "", "/transactions?page=99");
    render(<Harness />);
    expect(shown()).toBe("301-312");
    expect(at()).toBe("/transactions?page=13");
  });

  it("with filters, opens the page a link names when the link also changes the search", () => {
    window.history.replaceState(null, "", "/transactions?page=2");
    render(<Harness withFilters />);
    go("/transactions?page=4&q=food");
    expect(shown()).toBe("76-100");
    expect(at()).toBe("/transactions?page=4&q=food");
  });

  it("without filters, a link that changes the search with the page left as it was starts at page 1", () => {
    window.history.replaceState(null, "", "/transactions?page=4");
    render(<Harness />);
    go("/transactions?page=4&q=food");
    expect(shown()).toBe("1-25");
    expect(at()).toBe("/transactions?q=food");
  });

  it("writes nothing while the rows are not ready", () => {
    window.history.replaceState(null, "", "/transactions?page=7");
    render(<Harness ready={false} />);
    expect(at()).toBe("/transactions?page=7");
  });
});
