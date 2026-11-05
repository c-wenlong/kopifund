import { act, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { clearModelCache, MODEL_PAUSE_MS, MODELS_CAP, ModelField } from "./ModelField";

// G22: a model name field that asks the provider's list only once typing pauses, and remembers each answer.

const asked: string[] = [];
function stub(answer: (q: string) => object) {
  asked.length = 0;
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    asked.push(url);
    const q = new URL(url, "http://x").searchParams.get("q") ?? "";
    return new Response(JSON.stringify({ provider: "anthropic", fetched_at: "2026-10-23T09:00:00", error: null, models: [], ...answer(q) }), { status: 200 });
  }));
}

function Field({ provider = "anthropic" }: { provider?: string }) {
  const [v, setV] = useState("");
  return <><ModelField label="Ask Kopifund model" provider={provider} value={v} onChange={setV} /><span data-testid="value">{v}</span></>;
}

const box = () => screen.getByRole("combobox", { name: "Ask Kopifund model" });
const type = (text: string) => fireEvent.change(box(), { target: { value: text } });
// the answer resolves on the microtask queue after the timer fires
const pause = () => act(async () => { await vi.advanceTimersByTimeAsync(MODEL_PAUSE_MS); });

beforeEach(() => vi.useFakeTimers());
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("ModelField", () => {
  it("asks once after typing pauses, never per keystroke", async () => {
    stub((q) => ({ models: q ? [{ id: "claude-sonnet-4-5", label: "Claude Sonnet 4.5" }] : [] }));
    render(<Field />);
    type("s");
    await act(async () => { await vi.advanceTimersByTimeAsync(100); });
    type("so");
    await act(async () => { await vi.advanceTimersByTimeAsync(100); });
    type("son");
    await act(async () => { await vi.advanceTimersByTimeAsync(MODEL_PAUSE_MS - 1); });
    expect(asked).toEqual([]);
    await act(async () => { await vi.advanceTimersByTimeAsync(1); });
    expect(asked).toEqual(["/api/ai/models?provider=anthropic&q=son"]);
    expect(screen.getByRole("option", { name: /claude-sonnet-4-5/ })).toBeInTheDocument();
  });

  it("asks nothing for a query it has seen, and still keeps any typed name", async () => {
    stub(() => ({ models: [{ id: "claude-sonnet-4-5", label: "Claude Sonnet 4.5" }] }));
    render(<Field />);
    type("son");
    await pause();
    type("so");
    await pause();
    type("son");
    await pause();
    expect(asked).toEqual(["/api/ai/models?provider=anthropic&q=son", "/api/ai/models?provider=anthropic&q=so"]);
    // a name the list does not hold is kept as typed
    type("claude-next-preview");
    fireEvent.blur(box());
    expect(screen.getByTestId("value")).toHaveTextContent("claude-next-preview");
  });

  it("filters the whole list it already holds rather than asking again", async () => {
    stub((q) => ({ models: q ? [] : [
      { id: "claude-opus-4-1", label: "Claude Opus 4.1" }, { id: "claude-sonnet-4-5", label: "Claude Sonnet 4.5" },
    ] }));
    render(<Field />);
    fireEvent.focus(box());
    await pause();
    expect(asked).toEqual(["/api/ai/models?provider=anthropic&q="]);
    expect(screen.getAllByRole("option")).toHaveLength(2);
    type("opus");
    await pause();
    expect(asked).toHaveLength(1);
    expect(screen.getAllByRole("option").map((o) => o.textContent)).toEqual(["claude-opus-4-1Claude Opus 4.1"]);
    fireEvent.mouseDown(screen.getByRole("option"));
    expect(screen.getByTestId("value")).toHaveTextContent("claude-opus-4-1");
  });

  it("says quietly why the list could not be fetched", async () => {
    stub(() => ({ fetched_at: null, error: "No API key for Anthropic yet." }));
    render(<Field />);
    type("x");
    await pause();
    expect(screen.getByText("No API key for Anthropic yet.")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("asks the server for a query when the whole list it holds may be cut short at the cap", async () => {
    const many = Array.from({ length: MODELS_CAP }, (_, i) => ({ id: `claude-${String(i).padStart(3, "0")}`, label: "" }));
    stub((q) => ({ models: q ? [{ id: "gpt-5", label: "GPT-5" }] : many }));
    render(<Field />);
    fireEvent.focus(box());
    await pause();
    expect(asked).toEqual(["/api/ai/models?provider=anthropic&q="]);
    // 100 claude names may leave gpt-5 out: the server is asked, not the 100 filtered here
    type("gpt");
    await pause();
    expect(asked).toEqual(["/api/ai/models?provider=anthropic&q=", "/api/ai/models?provider=anthropic&q=gpt"]);
    expect(screen.getByRole("option", { name: /gpt-5/ })).toBeInTheDocument();
  });

  it("never keeps an answer that says why it has no list, so a key saved later is tried", async () => {
    let key = false;
    stub(() => (key ? { models: [{ id: "claude-sonnet-4-5", label: "Claude Sonnet 4.5" }] } : { fetched_at: null, error: "No API key for Anthropic yet." }));
    render(<Field />);
    type("son");
    await pause();
    expect(screen.getByText("No API key for Anthropic yet.")).toBeInTheDocument();
    key = true;
    type("q");
    await pause();
    type("son");
    await pause();
    expect(asked.filter((u) => u.endsWith("q=son"))).toHaveLength(2);
    expect(screen.getByRole("option", { name: /claude-sonnet-4-5/ })).toBeInTheDocument();
  });

  it("forgets one provider's answers when told to, and keeps the others'", async () => {
    stub(() => ({ models: [{ id: "m-1", label: "" }] }));
    // a fresh field for each provider, so each starts empty and the typing reaches it
    const { rerender } = render(<Field key="a1" />);
    type("m");
    await pause();
    rerender(<Field key="o1" provider="openai" />);
    type("m");
    await pause();
    expect(asked).toHaveLength(2);
    clearModelCache("anthropic");
    rerender(<Field key="a2" />);
    type("m");
    await pause();
    rerender(<Field key="o2" provider="openai" />);
    type("m");
    await pause();
    // anthropic asked again; openai's answer was still kept
    expect(asked).toEqual([
      "/api/ai/models?provider=anthropic&q=m", "/api/ai/models?provider=openai&q=m", "/api/ai/models?provider=anthropic&q=m",
    ]);
  });

  it("on a new provider, drops the pause timed for the old one and an answer still on its way for it", async () => {
    let release!: () => void;
    const gate = new Promise<void>((r) => { release = r; });
    asked.length = 0;
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      asked.push(url);
      if (url.includes("provider=anthropic")) await gate;
      return new Response(JSON.stringify({ provider: "anthropic", fetched_at: null, error: null,
        models: [{ id: url.includes("provider=anthropic") ? "claude-old" : "gpt-5", label: "" }] }), { status: 200 });
    }));
    const { rerender } = render(<Field />);
    // typed, and the provider changes before the pause ends: nothing is asked of the old one
    type("c");
    rerender(<Field provider="openai" />);
    await pause();
    expect(asked).toEqual([]);
    // an answer for the old provider still on its way when the provider changes never shows
    rerender(<Field provider="anthropic" />);
    type("cl");
    await pause();
    expect(asked).toEqual(["/api/ai/models?provider=anthropic&q=cl"]);
    rerender(<Field provider="openai" />);
    await act(async () => { release(); await vi.advanceTimersByTimeAsync(0); });
    expect(screen.queryByRole("option", { name: /claude-old/ })).not.toBeInTheDocument();
    type("g");
    await pause();
    expect(screen.getByRole("option", { name: /gpt-5/ })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: /claude-old/ })).not.toBeInTheDocument();
  });
});

// Audit: aria-controls named a list that is not there while closed
describe("ModelField's ARIA", () => {
  it("names no list while none is shown", () => {
    stub(() => ({ models: [] }));
    render(<Field />);
    expect(box()).not.toHaveAttribute("aria-controls");
  });
});
