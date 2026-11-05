import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useUrlParams } from "./useUrlParams";

// Audit: the address is the state of a page's filters; these pin how it is read and written.

afterEach(() => {
  vi.restoreAllMocks();
  window.history.replaceState(null, "", "/");
});

describe("useUrlParams", () => {
  it("round-trips a value with & = # + %, a space and non-Latin text", () => {
    window.history.replaceState(null, "", "/transactions");
    const { result } = renderHook(() => useUrlParams());
    const odd = "A&B = 100% #1 + 日本 kopi";
    act(() => result.current[1]({ q: odd }));
    expect(result.current[0].get("q")).toBe(odd);
    expect(new URLSearchParams(window.location.search).get("q")).toBe(odd);
    expect(window.location.pathname).toBe("/transactions");
  });

  it("lands two writes made in one tick", () => {
    const { result } = renderHook(() => useUrlParams());
    act(() => {
      result.current[1]({ month: "2026-09" });
      result.current[1]({ bucket: "Food" });
    });
    expect(result.current[0].toString()).toBe("month=2026-09&bucket=Food");
  });

  it("drops a key written as empty or null", () => {
    window.history.replaceState(null, "", "/?a=1&b=2&c=3");
    const { result } = renderHook(() => useUrlParams());
    act(() => result.current[1]({ a: "", b: null }));
    expect(window.location.search).toBe("?c=3");
    act(() => result.current[1]({ c: null }));
    expect(window.location.search).toBe("");
  });

  it("leaves the history alone for a write that changes nothing", () => {
    window.history.replaceState(null, "", "/?a=1");
    const { result } = renderHook(() => useUrlParams());
    const replace = vi.spyOn(window.history, "replaceState");
    act(() => result.current[1]({ a: "1", b: null }));
    expect(replace).not.toHaveBeenCalled();
  });

  it("keeps the hash and the history entry's state, and adds no entry", () => {
    window.history.replaceState({ kfIndex: 4 }, "", "/settings#ai");
    const before = window.history.length;
    const { result } = renderHook(() => useUrlParams());
    act(() => result.current[1]({ tab: "keys" }));
    expect(window.location.hash).toBe("#ai");
    expect(window.history.state).toEqual({ kfIndex: 4 });
    expect(window.history.length).toBe(before);
  });

  it("redraws every reader when one writes", () => {
    const a = renderHook(() => useUrlParams());
    const b = renderHook(() => useUrlParams());
    act(() => a.result.current[1]({ x: "1" }));
    expect(b.result.current[0].get("x")).toBe("1");
  });
});
