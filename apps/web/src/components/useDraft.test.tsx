import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { useDraft } from "./useDraft";

// Audit: the saved-versus-typed rules every SaveBar card shares (AI, Scam checks)

type V = { a: string; b: string };
const leaveAsks = () => !window.dispatchEvent(new Event("beforeunload", { cancelable: true }));

function setup(first: V = { a: "1", b: "2" }) {
  // `answer` is the object the server sent: a new one is a new read
  let answer: { v: V } = { v: first };
  const hook = renderHook(({ ans }) => useDraft<V>(ans.v, ans), { initialProps: { ans: answer } });
  const reread = (v: V) => { answer = { v }; hook.rerender({ ans: answer }); };
  return { ...hook, reread };
}

function deferred() {
  let resolve!: () => void, reject!: (e: Error) => void;
  const promise = new Promise<void>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

describe("useDraft", () => {
  it("shows the saved values, and only what is typed is an edit", () => {
    const { result } = setup();
    expect(result.current.value).toEqual({ a: "1", b: "2" });
    expect(result.current.dirty).toBe(false);
    act(() => result.current.set({ a: "5" }));
    expect(result.current.value).toEqual({ a: "5", b: "2" });
    expect(result.current.dirty).toBe(true);
    expect(leaveAsks()).toBe(true);
    // typed back to the saved value: no edit
    act(() => result.current.set({ a: "1" }));
    expect(result.current.dirty).toBe(false);
    expect(leaveAsks()).toBe(false);
  });

  it("shows another tab's save in a box left alone, and keeps what is typed in another", () => {
    const { result, reread } = setup();
    act(() => result.current.set({ a: "5" }));
    reread({ a: "1", b: "9" });
    expect(result.current.value).toEqual({ a: "5", b: "9" });
  });

  it("discard drops every edit and the problem", () => {
    const { result } = setup();
    act(() => { result.current.set({ a: "5" }); result.current.setProblem("bad"); });
    act(() => result.current.discard());
    expect(result.current.value).toEqual({ a: "1", b: "2" });
    expect(result.current.problem).toBeNull();
  });

  it("an edit clears the problem", () => {
    const { result } = setup();
    act(() => result.current.setProblem("bad"));
    act(() => result.current.set({ b: "3" }));
    expect(result.current.problem).toBeNull();
  });

  it("keeps what was typed while a save was on its way, even back to the old value, and holds the page meanwhile", async () => {
    const { result, reread } = setup();
    act(() => result.current.set({ a: "5" }));
    const d = deferred();
    let sent: V | null = null;
    let saving!: Promise<void>;
    act(() => { saving = result.current.save(async (v) => { sent = v; await d.promise; }); });
    expect(sent).toEqual({ a: "5", b: "2" });
    expect(result.current.saving).toBe(true);
    // typed during the save: b changes, a goes back to the value saved before
    act(() => result.current.set({ a: "1", b: "7" }));
    expect(leaveAsks()).toBe(true);
    await act(async () => { d.resolve(); await saving; });
    expect(result.current.saving).toBe(false);
    // what it sent stands for the saved values until the card is read again: a=1 is now an edit against a=5
    expect(result.current.saved).toEqual({ a: "5", b: "2" });
    expect(result.current.value).toEqual({ a: "1", b: "7" });
    expect(result.current.dirty).toBe(true);
    reread({ a: "5", b: "2" });
    expect(result.current.saved).toEqual({ a: "5", b: "2" });
    expect(result.current.value).toEqual({ a: "1", b: "7" });
  });

  it("shows what it sent, not the old values, between the save landing and the next read", async () => {
    const { result, reread } = setup();
    act(() => result.current.set({ a: "5" }));
    await act(async () => { await result.current.save(async () => {}); });
    expect(result.current.value).toEqual({ a: "5", b: "2" });
    expect(result.current.dirty).toBe(false);
    expect(leaveAsks()).toBe(false);
    reread({ a: "5", b: "2" });
    expect(result.current.value).toEqual({ a: "5", b: "2" });
    // a later read (another tab) shows through once more
    reread({ a: "6", b: "2" });
    expect(result.current.value).toEqual({ a: "6", b: "2" });
  });

  it("a refused save saves nothing: the refusal is the problem, and a box typed back meanwhile is no edit", async () => {
    const { result } = setup();
    act(() => result.current.set({ a: "5", b: "3" }));
    const d = deferred();
    let saving!: Promise<void>;
    act(() => { saving = result.current.save(() => d.promise); });
    act(() => result.current.set({ b: "2" }));
    await act(async () => { d.reject(new Error("Another tab changed it.")); await saving; });
    expect(result.current.problem).toBe("Another tab changed it.");
    expect(result.current.saved).toEqual({ a: "1", b: "2" });
    expect(result.current.value).toEqual({ a: "5", b: "2" });
    act(() => result.current.set({ a: "1" }));
    expect(result.current.dirty).toBe(false);
  });

  it("takes the refusal away when the same edit is sent again, whether the second try works or is refused again", async () => {
    const { result } = setup();
    act(() => result.current.set({ a: "5" }));
    await act(async () => { await result.current.save(() => Promise.reject(new Error("Disk full."))); });
    expect(result.current.problem).toBe("Disk full.");
    const d = deferred();
    let again!: Promise<void>;
    act(() => { again = result.current.save(() => d.promise); });
    // while the second try is on its way the old refusal is no longer true
    expect(result.current.problem).toBeNull();
    await act(async () => { d.resolve(); await again; });
    expect(result.current.problem).toBeNull();
    act(() => result.current.set({ a: "6" }));
    await act(async () => { await result.current.save(() => Promise.reject(new Error("Disk full again."))); });
    expect(result.current.problem).toBe("Disk full again.");
  });

  it("has no values while nothing is loaded", () => {
    const { result } = renderHook(() => useDraft<V>(null, null));
    expect(result.current.value).toBeNull();
    expect(result.current.dirty).toBe(false);
  });
});
