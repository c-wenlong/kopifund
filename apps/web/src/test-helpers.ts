// What the tests share. vitest.config (vite.config.ts) restores every mock, global
// and env var after each test, so nothing here needs an afterEach of its own.
import { act, waitFor } from "@testing-library/react";
import { beforeEach, expect, vi } from "vitest";

/** A fetch answer: `body` as JSON, `status` 200 unless given, `headers` (an ETag, say) added to the JSON content type. */
export function json(body: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json", ...headers } });
}

/** Recharts draws nothing into a box of width 0, which is all jsdom gives it: every chart gets 800x400. */
export class SizedObserver {
  constructor(private cb: ResizeObserverCallback) {}
  observe(el: Element) {
    this.cb([{ target: el, contentRect: { width: 800, height: 400 } } as unknown as ResizeObserverEntry],
      this as unknown as ResizeObserver);
  }
  unobserve() {}
  disconnect() {}
}

/** Call inside a describe (or at the top of a file): every test in it draws its charts at 800x400. */
export function useSizedCharts() {
  beforeEach(() => { vi.stubGlobal("ResizeObserver", SizedObserver); });
}

/**
 * matchMedia answering `matches` (a flag, or a function of the query) for the rest of the test.
 * `change` flips the answer later and tells every listener, as a window crossing a breakpoint does.
 */
export function stubMatchMedia(matches: boolean | ((query: string) => boolean)) {
  let now = matches;
  const listeners = new Set<() => void>();
  vi.stubGlobal("matchMedia", (query: string) => ({
    get matches() { return typeof now === "function" ? now(query) : now; },
    media: query, onchange: null,
    addEventListener: (_: string, f: () => void) => listeners.add(f),
    removeEventListener: (_: string, f: () => void) => listeners.delete(f),
    addListener() {}, removeListener() {}, dispatchEvent: () => false,
  }));
  return { change(next: boolean | ((query: string) => boolean)) { now = next; act(() => listeners.forEach((f) => f())); } };
}

/** The router's navigate, as a spy every test file shares; vitest resets its calls after each test. */
export const navigate = vi.fn();

/**
 * For `vi.mock("../router", async (actual) => (await import("../test-helpers")).withNavigate(await actual()))`:
 * the real router with navigate swapped for the spy above, so a test can see where a click went.
 */
export function withNavigate<T extends object>(actual: T): T & { navigate: (to: string) => void } {
  return { ...actual, navigate: (to: string) => navigate(to) };
}

/**
 * Let every promise already on its way finish: a few turns of the event loop inside act, so a mocked fetch
 * (which answers in promise steps, not on a clock) has landed and the page has drawn what it will. For
 * "and nothing else happens"; to wait for something that should happen, use findBy or waitFor.
 */
export async function settle() {
  for (let turn = 0; turn < 3; turn++) await act(async () => { await new Promise<void>((r) => setTimeout(r, 0)); });
}

/**
 * setTimeout on a fake clock, for a test that waits out a pause (a debounce, a poll) without sitting through it.
 * The clock also ticks on its own, which testing-library's waitFor and findBy need to finish; `pass(ms)`
 * jumps it on. Restored to the real one after the test.
 */
export function fakeClock() {
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"], shouldAdvanceTime: true });
  return { pass: (ms: number) => act(async () => { await vi.advanceTimersByTimeAsync(ms); }) };
}

/** Whether leaving the page now would ask first: what the browser does on a closing tab or a reload. */
export const leaveAsks = () => !window.dispatchEvent(new Event("beforeunload", { cancelable: true }));

/**
 * The page's guard against leaving is set and lifted in an effect, which React runs after the screen has
 * drawn: a test that has just seen "Saved" may look before the guard is lifted. These wait for it.
 */
export const expectAsks = () => waitFor(() => expect(leaveAsks()).toBe(true));
export const expectFree = () => waitFor(() => expect(leaveAsks()).toBe(false));
