import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ARMED_MS, useArmed } from "./useArmed";

describe("useArmed", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("arms on the first press and acts on the second", () => {
    const act1 = vi.fn();
    const { result } = renderHook(() => useArmed());
    act(() => result.current.press(act1));
    expect(result.current.armed).toBe(true);
    expect(act1).not.toHaveBeenCalled();
    act(() => result.current.press(act1));
    expect(act1).toHaveBeenCalledOnce();
    expect(result.current.armed).toBe(false);
  });

  it("disarms once the time runs out, so a late press only arms again", () => {
    const go = vi.fn();
    const { result } = renderHook(() => useArmed());
    act(() => result.current.press(go));
    act(() => vi.advanceTimersByTime(ARMED_MS));
    expect(result.current.armed).toBe(false);
    act(() => result.current.press(go));
    expect(go).not.toHaveBeenCalled();
    expect(result.current.armed).toBe(true);
  });

  it("disarm takes the first press back", () => {
    const go = vi.fn();
    const { result } = renderHook(() => useArmed());
    act(() => result.current.press(go));
    act(() => result.current.disarm());
    act(() => result.current.press(go));
    expect(go).not.toHaveBeenCalled();
  });
});
