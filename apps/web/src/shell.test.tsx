import { act, fireEvent, render, renderHook, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { ApiError, OFFLINE, checkServer, getJSON, postJSON, useApi, useLink } from "./api";
import { ServerBanner } from "./components/ServerBanner";
import { NoResult, Problem } from "./components/States";
import { Toaster, showToast } from "./components/Toast";
import { Sidebar } from "./components/Sidebar";
import { json, stubMatchMedia } from "./test-helpers";

// G13: the server is down, the shared toast, and the shell's titles and not-found page.

afterEach(() => {
  vi.useRealTimers();
  window.history.pushState(null, "", "/");
});

const down = () => vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("Failed to fetch"); }));

describe("a server that is not there", () => {
  it("says so in plain words, never the browser's message", async () => {
    down();
    await expect(getJSON("/api/overview")).rejects.toMatchObject({ message: OFFLINE, status: 0 });
    await expect(postJSON("/api/rules/save", "{}")).rejects.toThrow("Can't reach Kopifund, so that was not saved.");
  });

  it("reads the dev proxy's empty 500 as not there, and a JSON 500 as the server's own error", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("", { status: 500 })));
    await expect(getJSON("/api/overview")).rejects.toMatchObject({ message: OFFLINE });
    vi.stubGlobal("fetch", vi.fn(async () => json({ error: "disk full" }, 500)));
    await expect(getJSON("/api/overview")).rejects.toMatchObject({ message: "disk full", status: 500 });
  });

  it("keeps the status of a refusal, so only a 404 reads as nothing there yet", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json({ error: "not found" }, 404)));
    const { result } = renderHook(() => useApi("/api/results/forward-view"));
    await waitFor(() => expect(result.current.error).toBe("not found"));
    expect(result.current.status).toBe(404);
  });

  it("keeps painting the last figures and marks them old, then asks again when the server returns", async () => {
    let up = true;
    vi.stubGlobal("fetch", vi.fn(async () => {
      if (!up) throw new TypeError("Failed to fetch");
      return json({ n: 1 });
    }));
    const { result } = renderHook(() => ({ api: useApi<{ n: number }>("/api/overview"), link: useLink() }));
    await waitFor(() => expect(result.current.api.data).toEqual({ n: 1 }));
    up = false;
    await act(async () => { await checkServer(); });
    expect(result.current.link.down).toBe(true);
    // a page opened again meanwhile still has its answer
    const again = renderHook(() => useApi<{ n: number }>("/api/overview"));
    await waitFor(() => expect(result.current.link.stale).toBe(true));
    expect(again.result.current.data).toEqual({ n: 1 });
    expect(again.result.current.error).toBeNull();
    up = true;
    await act(async () => { await checkServer(); });
    expect(result.current.link).toEqual({ down: false, stale: false });
  });

  it("calls the figures old only while a mounted view paints a kept answer", async () => {
    let up = true;
    vi.stubGlobal("fetch", vi.fn(async () => {
      if (!up) throw new TypeError("Failed to fetch");
      return json({ n: 1 });
    }));
    const seen = renderHook(() => useApi<{ n: number }>("/api/overview"));
    await waitFor(() => expect(seen.result.current.data).toEqual({ n: 1 }));
    seen.unmount();
    up = false;
    // a page whose answers were never kept: down, but nothing old on screen
    const bare = renderHook(() => ({ api: useApi<{ n: number }>("/api/plan"), link: useLink() }));
    await waitFor(() => expect(bare.result.current.api.error).toBe(OFFLINE));
    expect(bare.result.current.link).toEqual({ down: true, stale: false });
    // one with a kept answer: old figures
    const kept = renderHook(() => useApi<{ n: number }>("/api/overview"));
    await waitFor(() => expect(bare.result.current.link.stale).toBe(true));
    // and leaving that page takes the claim away
    kept.unmount();
    await waitFor(() => expect(bare.result.current.link.stale).toBe(false));
    expect(bare.result.current.link.down).toBe(true);
  });

  it("marks the figures on screen old when a failed save is what finds the server down", async () => {
    let up = true;
    vi.stubGlobal("fetch", vi.fn(async () => {
      if (!up) throw new TypeError("Failed to fetch");
      return json({ n: 1 });
    }));
    const { result } = renderHook(() => ({ api: useApi<{ n: number }>("/api/overview"), link: useLink() }));
    await waitFor(() => expect(result.current.api.data).toEqual({ n: 1 }));
    expect(result.current.link).toEqual({ down: false, stale: false });
    up = false;
    await act(async () => { await expect(postJSON("/api/budgets", "{}")).rejects.toThrow("not saved"); });
    expect(result.current.link).toEqual({ down: true, stale: true });
    up = true;
    await act(async () => { await checkServer(); });
    expect(result.current.link).toEqual({ down: false, stale: false });
  });

  it("does not count the shell's quiet use of an answer as figures on the page", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json({ n: 1 })));
    const { result } = renderHook(() => ({ api: useApi<{ n: number }>("/api/overview", { quiet: true }), link: useLink() }));
    await waitFor(() => expect(result.current.api.data).toEqual({ n: 1 }));
    down();
    await act(async () => { await checkServer(); });
    expect(result.current.link).toEqual({ down: true, stale: false });
  });

  it("keeps the last figures when the dev proxy answers an empty 500 for a stopped server", async () => {
    let up = true;
    vi.stubGlobal("fetch", vi.fn(async () => up
      ? json({ n: 1 }, 200, { ETag: '"1"' })
      : new Response("", { status: 500 })));
    const first = renderHook(() => useApi<{ n: number }>("/api/overview"));
    await waitFor(() => expect(first.result.current.data).toEqual({ n: 1 }));
    first.unmount();
    up = false;
    const again = renderHook(() => ({ api: useApi<{ n: number }>("/api/overview"), link: useLink() }));
    await waitFor(() => expect(again.result.current.link.stale).toBe(true));
    expect(again.result.current.api).toMatchObject({ data: { n: 1 }, error: null, loading: false });
  });

  it("draws a card that failed only because the server is down as a quiet placeholder", () => {
    const { container } = render(<Problem message={OFFLINE} retry />);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(container).not.toHaveTextContent("Failed to fetch");
  });

  it("shows other problems with Try again", () => {
    render(<Problem message="the stats are being rebuilt" retry />);
    expect(screen.getByRole("alert")).toHaveTextContent("the stats are being rebuilt");
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("blames the analysis only for a 404", () => {
    const { rerender } = render(<NoResult status={404} error="not found" what="forward view" />);
    expect(screen.getByRole("alert")).toHaveTextContent("No forward view result yet.");
    expect(screen.getByRole("alert")).toHaveTextContent("Run the analysis");
    rerender(<NoResult status={500} error="disk full" what="forward view" />);
    expect(screen.getByRole("alert")).toHaveTextContent("disk full");
    expect(screen.getByRole("alert")).not.toHaveTextContent("Run the analysis");
  });
});

describe("the server banner", () => {
  it("is absent while the server answers, and says what to do when it does not", async () => {
    const { container } = render(<ServerBanner />);
    expect(container).toBeEmptyDOMElement();
    down();
    await act(async () => { await checkServer(); });
    const banner = screen.getByRole("alert");
    // in a browser the server runs apart from the page, so reopening the page cannot bring it back
    expect(banner).toHaveTextContent("Can't reach Kopifund. Start it again on the computer that runs it, then Retry.");
    // Audit: words an app user can act on, not a developer's command
    expect(banner).not.toHaveTextContent(/make serve/);
    expect(within(banner).getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });

  // Greptile on #68: the Mac app starts its own server, so there reopening the app is the way back
  it("tells the Mac app's window to reopen the app", async () => {
    (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {};
    try {
      render(<ServerBanner />);
      down();
      await act(async () => { await checkServer(); });
      expect(screen.getByRole("alert")).toHaveTextContent("Can't reach Kopifund. Quit and reopen Kopifund, then Retry.");
    } finally {
      delete (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__;
    }
  });

  it("says the figures are from the last visit only when a view shows them", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json({ n: 1 })));
    const view = renderHook(() => useApi("/api/overview"));
    await waitFor(() => expect(view.result.current.data).not.toBeNull());
    view.unmount();
    down();
    render(<ServerBanner />);
    await act(async () => { await checkServer(); });
    expect(screen.getByRole("alert")).not.toHaveTextContent("last visit");
    renderHook(() => useApi("/api/overview"));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Showing figures from your last visit."));
  });

  it("Retry asks /api/health and goes away once it answers", async () => {
    down();
    render(<ServerBanner />);
    await act(async () => { await checkServer(); });
    const fetchSpy = vi.fn(async (_url: string) => new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetchSpy);
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
    expect(fetchSpy.mock.calls[0][0]).toBe("/api/health");
  });

  it("says a retry found nothing", async () => {
    down();
    render(<ServerBanner />);
    await act(async () => { await checkServer(); });
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Still no answer."));
  });
});

describe("Toast", () => {
  it("sits in a polite live region, and goes away by itself", () => {
    vi.useFakeTimers();
    const { container } = render(<Toaster />);
    // Audit: straight under <body>, outside the root an open Sheet makes inert
    expect(container).toBeEmptyDOMElement();
    expect(document.querySelector("body > [aria-live]")).toHaveAttribute("aria-live", "polite");
    act(() => { showToast({ message: "Saved." }); });
    expect(screen.getByRole("status")).toHaveTextContent("Saved.");
    act(() => { vi.advanceTimersByTime(5100); });
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("an error is an alert and stays longer; an action stays longer too", () => {
    vi.useFakeTimers();
    render(<Toaster />);
    act(() => { showToast({ message: "Refused.", tone: "error" }); });
    act(() => { vi.advanceTimersByTime(5100); });
    expect(screen.getByRole("alert")).toHaveTextContent("Refused.");
    act(() => { vi.advanceTimersByTime(3100); });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("runs its action once and takes the toast down", () => {
    const undo = vi.fn();
    render(<Toaster />);
    act(() => { showToast({ message: "Deleted.", action: { label: "Undo", onClick: undo } }); });
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    expect(undo).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("does not stack the same words twice, and can be dismissed", () => {
    render(<Toaster />);
    act(() => { showToast({ message: "Saved." }); showToast({ message: "Saved." }); });
    expect(screen.getAllByRole("status")).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });
});

describe("the sidebar's holdings", () => {
  it("labels each figure with S$, links it to Net worth, and names the date on hover", () => {
    render(<Sidebar path="/" asOf="2026-08-28" holdings={[{ label: "Cash", value: 1234500 }, { label: "Investments", value: 5000000 }]} />);
    const link = screen.getByRole("link", { name: "Cash" });
    expect(link).toHaveAttribute("href", "/net-worth");
    const row = link.closest("div")!;
    expect(row).toHaveTextContent("S$12,345");
    expect(row).toHaveAttribute("title", "As of 28 Aug 2026");
    expect(screen.getByText("S$50,000")).toBeInTheDocument();
  });
});

describe("the shell", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(() => {})));
    stubMatchMedia((q) => !q.includes("768"));
  });

  it("names the browser tab for each page", async () => {
    window.history.pushState(null, "", "/spending");
    render(<App />);
    await waitFor(() => expect(document.title).toBe("Spending, Kopifund"));
  });

  it("keeps the page heading for screen readers on a phone, shown from 768px", () => {
    window.history.pushState(null, "", "/rules");
    render(<App />);
    const h1 = screen.getByRole("heading", { level: 1, name: "Rules" });
    expect(h1.className).toContain("max-md:sr-only");
  });

  it("does not call the figures old when only the shell's own answers are on screen", async () => {
    // setup and the overview answer, then a save finds the server gone
    let up = true;
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (!up) throw new TypeError("Failed to fetch");
      return json(url.startsWith("/api/setup") ? { needed: false } : { holdings: [], as_of: null, headline: {} });
    }));
    window.history.pushState(null, "", "/nowhere");
    render(<App />);
    await screen.findByText("There is no page at this address.");
    await act(async () => { await new Promise((r) => setTimeout(r, 50)); });
    up = false;
    await act(async () => { await expect(postJSON("/api/budgets", "{}")).rejects.toThrow(); });
    const banner = await screen.findByRole("alert");
    expect(banner).toHaveTextContent("Can't reach Kopifund");
    expect(banner).not.toHaveTextContent("last visit");
  });

  it("an unknown address has one heading, plain words and a way home", () => {
    window.history.pushState(null, "", "/nowhere");
    render(<App />);
    expect(screen.getAllByRole("heading")).toHaveLength(1);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Page not found");
    expect(screen.getByText("There is no page at this address.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Go to Home" })).toHaveAttribute("href", "/");
    expect(document.title).toBe("Page not found, Kopifund");
  });

  it("is an ApiError that carries its status", () => {
    expect(new ApiError("x", 404).status).toBe(404);
    expect(new ApiError("x").status).toBe(0);
  });
});
