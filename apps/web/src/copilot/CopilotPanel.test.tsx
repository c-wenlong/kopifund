import { act, fireEvent, render, renderHook, screen, waitFor, within } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Toaster } from "../components/Toast";
import type { AiSettings, CopilotCard, CopilotEvent, CopilotThread } from "../types.gen";
import { ANSWER_FAILED, CopilotSide } from "./CopilotPanel";
import { CUT_SHORT, stepsOf, useCopilot } from "./useCopilot";

// G22: the Ask Kopifund panel: the answer's steps as they come, the answer and its parts, confirm cards, and its states.

const providers: AiSettings["providers"] = [
  { name: "anthropic", label: "Anthropic", local: false, default_model: "claude-sonnet-4-5", copilot_default_model: "claude-opus-5-5" },
  { name: "ollama", label: "Ollama", local: true, default_model: "", copilot_default_model: "" },
];
const aiOn: AiSettings = {
  enabled: true, provider: "anthropic", providers, model: "", copilot_model: "", model_in_use: "claude-sonnet-4-5", copilot_model_in_use: "claude-opus-5-5",
  key: { where: "keychain", last4: "9xQz", can_remove: true }, send_rows: true, local: false, from_config: false,
  config_provider: null,
};

const enc = new TextEncoder();
/** An answer body the test writes to line by line, as the server would. */
function live() {
  let ctrl!: ReadableStreamDefaultController<Uint8Array>;
  const body = new ReadableStream<Uint8Array>({ start(c) { ctrl = c; } });
  return {
    body,
    send: (e: CopilotEvent) => act(async () => { ctrl.enqueue(enc.encode(`${JSON.stringify(e)}\n`)); }),
    end: () => act(async () => { ctrl.close(); }),
    fail: (e: unknown) => ctrl.error(e),
  };
}
const lines = (events: CopilotEvent[]) => new Response(events.map((e) => JSON.stringify(e)).join("\n"), { status: 200 });

type Handler = (url: string, init?: RequestInit) => Response | Promise<Response> | undefined;
const posts: { url: string; body: unknown }[] = [];
function stub(ai: AiSettings | null, handle: Handler = () => undefined) {
  posts.length = 0;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === "POST") posts.push({ url, body: init.body ? JSON.parse(init.body as string) : null });
    const own = await handle(url, init);
    if (own) return own;
    if (url === "/api/ai") return new Response(JSON.stringify(ai), { status: 200 });
    if (url === "/api/health") return new Response("{}", { status: 200 });
    return new Response(JSON.stringify({ error: "not here" }), { status: 404 });
  }));
}

const onClose = vi.fn();
function Panel({ path = "/spending", title = "Spending" }: { path?: string; title?: string }) {
  const copilot = useCopilot();
  return <><CopilotSide copilot={copilot} path={path} title={title} onClose={onClose} /><Toaster /></>;
}
/** The conversation held outside the panel, as the shell holds it, with a button that closes and opens the panel. */
function Shell() {
  const copilot = useCopilot();
  const [open, setOpen] = useState(true);
  return (
    <>
      <button type="button" onClick={() => setOpen(!open)}>Toggle panel</button>
      {open && <CopilotSide copilot={copilot} path="/spending" title="Spending" onClose={() => setOpen(false)} />}
      <Toaster />
    </>
  );
}

const box = () => screen.getByRole("textbox", { name: "Ask about your money" });
async function askIt(q: string) {
  const b = await screen.findByRole("textbox", { name: "Ask about your money" });
  await waitFor(() => expect(b).toBeEnabled());
  fireEvent.change(b, { target: { value: q } });
  fireEvent.keyDown(b, { key: "Enter" });
}

afterEach(() => {
  vi.unstubAllGlobals();
  onClose.mockReset();
  window.history.pushState(null, "", "/");
});

describe("Ask Kopifund panel", () => {
  it("names the page and its filters, and sends them with the question", async () => {
    window.history.pushState(null, "", "/spending?month=2026-09&bucket=Food");
    stub(aiOn, (url) => (url === "/api/copilot/ask" ? lines([{ type: "text", text: "S$42" }, { type: "done" }]) : undefined));
    render(<Panel />);
    expect(await screen.findByText("Spending · month 2026-09 · bucket Food")).toBeInTheDocument();
    await askIt("How much on kopi?");
    expect(await screen.findByText("S$42")).toBeInTheDocument();
    expect(posts.find((p) => p.url === "/api/copilot/ask")?.body).toEqual({
      thread: null, question: "How much on kopi?",
      page: { path: "/spending", title: "Spending", filters: { month: "2026-09", bucket: "Food" } },
    });
    expect(screen.getByText("Sends matching rows to Anthropic ·")).toBeInTheDocument();
  });

  it("shows each round's tools as they run, then the answer, links, a chart and follow-ups", async () => {
    const answer = live();
    stub(aiOn, (url) => (url === "/api/copilot/ask" ? new Response(answer.body, { status: 200 }) : undefined));
    render(<Panel />);
    await askIt("Where did September go?");
    expect(await screen.findByText("Where did September go?")).toBeInTheDocument();
    expect(box()).toBeDisabled();
    expect(screen.getByRole("button", { name: "Stop" })).toBeInTheDocument();
    await answer.send({ type: "thread", thread: { id: "t1", title: "September", updated_at: "2026-10-23T10:00:00", turns: 1 } });
    await answer.send({ type: "tool", tool: { name: "spending", label: "Read spending for Sep 2026" } });
    const steps = screen.getByRole("list", { name: "Steps" });
    // collapsed: the row names how many tools ran, not their lines
    expect(within(steps).getByRole("button", { name: /^Used 1 tool/ })).toHaveAttribute("aria-expanded", "false");
    expect(within(steps).queryByText("Read spending for Sep 2026")).not.toBeInTheDocument();
    // still working: no answer yet
    expect(within(steps).getByText("Thinking…")).toBeInTheDocument();
    await answer.send({ type: "tool", tool: { name: "query", label: "Ran a query over 212 rows" } });
    // the next tool joins the same row
    const used = within(steps).getByRole("button", { name: /^Used 2 tools/ });
    expect(used).toHaveTextContent("Spending, Query");
    fireEvent.click(used);
    expect(used).toHaveAttribute("aria-expanded", "true");
    expect(within(steps).getByRole("list", { name: "Tools used" })).toHaveTextContent("Read spending for Sep 2026Ran a query over 212 rows");
    await answer.send({ type: "text", text: "You spent **S$2,140** in September.\n\n- Food S$820\n- Transport S$310" });
    expect(screen.getByText("S$2,140").tagName).toBe("STRONG");
    expect(screen.getByText("Food S$820").tagName).toBe("LI");
    await answer.send({ type: "chart", chart: { title: "Spend by month", chart: {
      type: "bar", x: "month", series: ["spend_minor"], unit: "sgd_minor",
      rows: [{ month: "2026-08", spend_minor: 190_000 }, { month: "2026-09", spend_minor: 214_000 }],
    } } });
    expect(screen.getByRole("figure", { name: "Spend by month" })).toBeInTheDocument();
    await answer.send({ type: "link", link: { label: "The 31 food rows", href: "/transactions?month=2026-09&category=Food" } });
    await answer.send({ type: "followups", followups: ["And August?", "Only weekends"] });
    await answer.send({ type: "done" });
    await answer.end();
    await waitFor(() => expect(box()).toBeEnabled());
    expect(screen.queryByText("Thinking…")).not.toBeInTheDocument();
    // the link opens the rows in the app, not a new page load
    fireEvent.click(screen.getByRole("link", { name: "The 31 food rows" }));
    expect(window.location.pathname + window.location.search).toBe("/transactions?month=2026-09&category=Food");
    // a follow-up chip asks it, in the same thread
    stub(aiOn, (url) => (url === "/api/copilot/ask" ? lines([{ type: "text", text: "S$1,900" }, { type: "done" }]) : undefined));
    fireEvent.click(screen.getByRole("button", { name: "And August?" }));
    expect(await screen.findByText("S$1,900")).toBeInTheDocument();
    expect(posts.find((p) => p.url === "/api/copilot/ask")?.body).toMatchObject({ thread: "t1", question: "And August?" });
  });

  it("stops the answer when Stop is pressed", async () => {
    const answer = live();
    stub(aiOn, (url, init) => {
      if (url !== "/api/copilot/ask") return undefined;
      init?.signal?.addEventListener("abort", () => answer.fail(new DOMException("Aborted", "AbortError")));
      return new Response(answer.body, { status: 200 });
    });
    render(<Panel />);
    await askIt("Slow one");
    await answer.send({ type: "tool", tool: { name: "query", label: "Ran a query" } });
    fireEvent.click(screen.getByRole("button", { name: "Stop" }));
    expect(await screen.findByText("Stopped")).toBeInTheDocument();
    await waitFor(() => expect(box()).toBeEnabled());
  });

  it("shows a refusal in the problem style, and Retry asks again", async () => {
    let first = true;
    stub(aiOn, (url) => {
      if (url !== "/api/copilot/ask") return undefined;
      if (first) {
        first = false;
        return new Response(JSON.stringify({ error: "The provider is busy." }), { status: 422 });
      }
      return lines([{ type: "text", text: "Here it is" }, { type: "done" }]);
    });
    render(<Panel />);
    await askIt("Try me");
    expect(await screen.findByRole("alert")).toHaveTextContent("The provider is busy.");
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("Here it is")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    // the question shows once, not twice
    expect(screen.getAllByText("Try me")).toHaveLength(1);
  });

  it("removing the thread that is open starts a new one", async () => {
    let threads = [{ id: "b", title: "Kopi this year", updated_at: "2026-10-22T09:00:00", turns: 2 }];
    const thread: CopilotThread = { id: "b", title: "Kopi this year", updated_at: "2026-10-22T09:00:00", messages: [
      { role: "user", text: "Kopi this year?", at: "2026-10-22T09:00:00", page: null, tools: [], steps: [], charts: [], links: [], cards: [], followups: [] },
      { role: "assistant", text: "S$310 on kopi", at: "2026-10-22T09:00:05", page: null, tools: [], steps: [], charts: [], links: [], cards: [], followups: [] },
    ] };
    stub(aiOn, (url) => {
      if (url === "/api/copilot/threads") return new Response(JSON.stringify({ threads }), { status: 200 });
      if (url === "/api/copilot/threads/b") return new Response(JSON.stringify(thread), { status: 200 });
      if (url === "/api/copilot/threads/remove") {
        threads = [];
        return new Response(JSON.stringify({ threads }), { status: 200 });
      }
      if (url === "/api/copilot/ask") return lines([{ type: "text", text: "Fresh answer" }, { type: "done" }]);
      return undefined;
    });
    render(<Panel />);
    fireEvent.click(await screen.findByRole("button", { name: "History" }));
    fireEvent.click(await screen.findByRole("button", { name: /^Kopi this year/ }));
    expect(await screen.findByText("S$310 on kopi")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "History" }));
    fireEvent.click(await screen.findByRole("button", { name: "Remove Kopi this year" }));
    expect(await screen.findByText("No threads yet")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "History" }));
    expect(screen.queryByText("S$310 on kopi")).not.toBeInTheDocument();
    await askIt("Anything new?");
    expect(await screen.findByText("Fresh answer")).toBeInTheDocument();
    expect(posts.find((p) => p.url === "/api/copilot/ask")?.body).toMatchObject({ thread: null });
  });

  it("an answer that ends without its done line is cut short: the problem with Retry, never complete", async () => {
    stub(aiOn, (url) => (url === "/api/copilot/ask"
      ? lines([{ type: "text", text: "You spent S$2" }, { type: "followups", followups: ["And August?"] }])
      : undefined));
    render(<Panel />);
    await askIt("Where did it go?");
    expect(await screen.findByRole("alert")).toHaveTextContent(CUT_SHORT);
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
    // a part-answer offers no follow-ups, as a finished one would
    expect(screen.queryByRole("group", { name: "Follow-up questions" })).not.toBeInTheDocument();
    await waitFor(() => expect(box()).toBeEnabled());
  });

  it("Retry after an error event sends the question again and keeps no copy of the failed pair", async () => {
    let first = true;
    stub(aiOn, (url) => {
      if (url !== "/api/copilot/ask") return undefined;
      if (first) {
        first = false;
        return lines([{ type: "thread", thread: { id: "t9", title: "Try", updated_at: "2026-10-23T10:00:00", turns: 1 } },
          { type: "text", text: "Half an answer" }, { type: "error", message: "The provider gave out." }, { type: "done" }]);
      }
      return lines([{ type: "text", text: "The whole answer" }, { type: "done" }]);
    });
    render(<Panel />);
    await askIt("Try me");
    expect(await screen.findByRole("alert")).toHaveTextContent("The provider gave out.");
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("The whole answer")).toBeInTheDocument();
    expect(screen.queryByText("Half an answer")).not.toBeInTheDocument();
    expect(screen.getAllByText("Try me")).toHaveLength(1);
    // the server kept nothing of the failed turn: the same question goes again, in the same thread
    expect(posts.filter((p) => p.url === "/api/copilot/ask").map((p) => p.body)).toEqual([
      expect.objectContaining({ thread: null, question: "Try me" }), expect.objectContaining({ thread: "t9", question: "Try me" }),
    ]);
  });

  it("Retry pressed twice before the answer starts sends once and keeps the question once", async () => {
    let calls = 0;
    stub(aiOn, (url) => {
      if (url !== "/api/copilot/ask") return undefined;
      calls++;
      return calls === 1 ? lines([{ type: "error", message: "Busy." }, { type: "done" }]) : lines([{ type: "text", text: "Done now" }, { type: "done" }]);
    });
    const { result } = renderHook(() => useCopilot());
    await act(async () => { await result.current.send("Once", { path: "/", title: "Home", filters: {} }); });
    expect(result.current.turns.map((t) => t.status)).toEqual(["done", "error"]);
    await act(async () => {
      result.current.retry();
      result.current.retry();
    });
    await waitFor(() => expect(result.current.running).toBe(false));
    expect(calls).toBe(2);
    expect(result.current.turns.map((t) => [t.role, t.text])).toEqual([["user", "Once"], ["assistant", "Done now"]]);
  });

  it("shows an error event with Retry", async () => {
    stub(aiOn, (url) => (url === "/api/copilot/ask"
      ? lines([{ type: "tool", tool: { name: "q", label: "Ran a query" } }, { type: "error", message: "The model stopped after 8 rounds." }, { type: "done" }])
      : undefined));
    render(<Panel />);
    await askIt("Big one");
    expect(await screen.findByRole("alert")).toHaveTextContent("The model stopped after 8 rounds.");
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });

  it("says where to pick a provider when AI is turned off in Settings, even with one in config.toml", async () => {
    stub({ ...aiOn, enabled: false, from_config: true });
    render(<Panel />);
    expect(await screen.findByText("No AI provider yet")).toBeInTheDocument();
    expect(box()).toBeDisabled();
  });

  it("says where to pick a provider when there is none", async () => {
    stub({ ...aiOn, enabled: false, provider: null });
    render(<Panel />);
    expect(await screen.findByText("No AI provider yet")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open Settings" })).toHaveAttribute("href", "/settings#ai");
    expect(box()).toBeDisabled();
  });

  it("is locked while consent is off, and says what would be sent", async () => {
    stub({ ...aiOn, send_rows: false });
    render(<Panel />);
    expect(await screen.findByText("Ask Kopifund is off")).toBeInTheDocument();
    expect(screen.getByText(/dates, amounts, payees and categories of matching rows to Anthropic; never account numbers or memo lines/))
      .toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Turn on in Settings" })).toHaveAttribute("href", "/settings#ai");
    expect(box()).toBeDisabled();
  });

  it("needs no consent for a provider on this machine, and says so under the box", async () => {
    stub({ ...aiOn, provider: "ollama", local: true, send_rows: false, key: { where: "none", last4: null, can_remove: false } });
    render(<Panel />);
    expect(await screen.findByText("Runs on this machine")).toBeInTheDocument();
    expect(screen.queryByText("Ask Kopifund is off")).not.toBeInTheDocument();
    expect(box()).toBeEnabled();
  });

  it("names a custom provider from config.toml under the box", async () => {
    stub({ ...aiOn, provider: "mylab", from_config: true, config_provider: "mylab" });
    render(<Panel />);
    expect(await screen.findByText("Sends matching rows to mylab ·")).toBeInTheDocument();
  });

  it("closes on Escape", async () => {
    stub(aiOn);
    render(<Panel />);
    fireEvent.keyDown(await screen.findByRole("textbox", { name: "Ask about your money" }), { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("lists threads newest first, opens one and removes one", async () => {
    let threads = [
      { id: "b", title: "Kopi this year", updated_at: "2026-10-22T09:00:00", turns: 2 },
      { id: "a", title: "September spend", updated_at: "2026-10-01T09:00:00", turns: 4 },
    ];
    const thread: CopilotThread = { id: "b", title: "Kopi this year", updated_at: "2026-10-22T09:00:00", messages: [
      { role: "user", text: "Kopi this year?", at: "2026-10-22T09:00:00", page: null, tools: [], steps: [], charts: [], links: [], cards: [], followups: [] },
      { role: "assistant", text: "S$310 on kopi", at: "2026-10-22T09:00:05", page: null, tools: [{ name: "q", label: "Ran a query" }], steps: [],
        charts: [], links: [], cards: [], followups: [] },
    ] };
    stub(aiOn, (url) => {
      if (url === "/api/copilot/threads") return new Response(JSON.stringify({ threads }), { status: 200 });
      if (url === "/api/copilot/threads/b") return new Response(JSON.stringify(thread), { status: 200 });
      if (url === "/api/copilot/threads/remove") {
        threads = threads.slice(0, 1);
        return new Response(JSON.stringify({ threads }), { status: 200 });
      }
      return undefined;
    });
    render(<Panel />);
    fireEvent.click(await screen.findByRole("button", { name: "History" }));
    const list = await screen.findByRole("list", { name: "Threads" });
    expect(within(list).getAllByRole("listitem").map((li) => li.textContent)).toEqual(["Kopi this year22 Oct 2026", "September spend1 Oct 2026"]);
    fireEvent.click(within(list).getByRole("button", { name: "Remove September spend" }));
    await waitFor(() => expect(posts).toContainEqual({ url: "/api/copilot/threads/remove", body: { id: "a" } }));
    // the list the removal answers is the one shown: the removed thread is gone from History
    await waitFor(() => expect(within(list).queryByText("September spend")).not.toBeInTheDocument());
    expect(within(list).getAllByRole("listitem")).toHaveLength(1);
    fireEvent.click(within(list).getByRole("button", { name: /^Kopi this year/ }));
    expect(await screen.findByText("S$310 on kopi")).toBeInTheDocument();
    expect(screen.getByText("Kopi this year?")).toBeInTheDocument();
    // an answer saved with no steps shows its tools as one row
    expect(screen.getByRole("button", { name: /^Used 1 tool/ })).toHaveTextContent("Q");
  });
});

describe("an answer's steps", () => {
  const before = (a: HTMLElement, b: HTMLElement) => !!(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);

  it("shows each round's thinking, tools and interim text in order, each row collapsed until pressed", async () => {
    const answer = live();
    stub(aiOn, (url) => (url === "/api/copilot/ask" ? new Response(answer.body, { status: 200 }) : undefined));
    render(<Panel />);
    await askIt("How long does my money last?");
    await answer.send({ type: "thread", thread: { id: "t1", title: "Runway", updated_at: "2026-10-23T10:00:00", turns: 1 } });
    const steps = await screen.findByRole("list", { name: "Steps" });
    expect(within(steps).getByText("Thinking…")).toBeInTheDocument();
    await answer.send({ type: "thinking", text: "Start from the overview and the **runway** stat." });
    await answer.send({ type: "say", text: "Your runway is about 14 months." });
    await answer.send({ type: "tool", tool: { name: "overview", label: "The overview" } });
    await answer.send({ type: "tool", tool: { name: "stat_result", label: "The savings-runway stat" } });
    await answer.send({ type: "thinking", text: "The stat counts CPF, so the line above is off." });
    await answer.send({ type: "say", text: "I need to correct one line above." });
    await answer.send({ type: "tool", tool: { name: "query", label: "Looking it up in the record" } });
    await answer.send({ type: "thinking", text: "Say the corrected figure." });
    // the row being written is the last one
    const rows = within(steps).getAllByRole("listitem");
    expect(rows.map((r) => r.textContent)).toEqual([
      "Thought", "Your runway is about 14 months.", "Used 2 toolsOverview, Stat",
      "Thought", "I need to correct one line above.", "Used 1 toolQuery", "Thought", "Thinking…",
    ]);
    // collapsed: a thought opens to its summary, quiet, under a rule
    const thoughts = within(steps).getAllByRole("button", { name: "Thought" });
    for (const t of thoughts) expect(t).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText(/Start from the overview/)).not.toBeInTheDocument();
    fireEvent.click(thoughts[0]);
    expect(thoughts[0]).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("runway").tagName).toBe("STRONG");
    expect(screen.getByText(/Start from the overview/).closest(".border-l-2")).not.toBeNull();
    fireEvent.click(thoughts[0]);
    expect(screen.queryByText(/Start from the overview/)).not.toBeInTheDocument();
    // a tools row opens to each tool's line; it is a button, so Tab reaches it and Enter or Space opens it
    const tools = within(steps).getByRole("button", { name: /^Used 2 tools/ });
    tools.focus();
    expect(tools).toHaveFocus();
    fireEvent.click(tools);
    expect(within(steps).getByRole("list", { name: "Tools used" })).toHaveTextContent("The overviewThe savings-runway stat");
    await answer.send({ type: "text", text: "It is about 9 months without CPF." });
    await answer.send({ type: "done" });
    await answer.end();
    await waitFor(() => expect(box()).toBeEnabled());
    expect(screen.queryByText("Thinking…")).not.toBeInTheDocument();
    // the interim text stays in place, above the final answer
    const final = screen.getByText("It is about 9 months without CPF.");
    expect(before(screen.getByText("I need to correct one line above."), final)).toBe(true);
    expect(before(steps, final)).toBe(true);
  });

  it("shows an answer once when the server made a round's text the answer", async () => {
    stub(aiOn, (url) => (url === "/api/copilot/ask"
      ? lines([{ type: "say", text: "Groceries came to S$100.00." }, { type: "text", text: "Groceries came to S$100.00." },
        { type: "followups", followups: ["And eating out?"] }, { type: "done" }])
      : undefined));
    render(<Panel />);
    await askIt("Groceries?");
    expect(await screen.findByRole("button", { name: "And eating out?" })).toBeInTheDocument();
    expect(screen.getAllByText("Groceries came to S$100.00.")).toHaveLength(1);
    expect(screen.queryByRole("list", { name: "Steps" })).not.toBeInTheDocument();
  });

  it("opens a saved thread with its steps, and an older one with its tools as one row", async () => {
    const msg = (role: "user" | "assistant", text: string, more: object = {}) =>
      ({ role, text, at: "2026-10-22T09:00:00", page: null, tools: [], charts: [], links: [], cards: [], followups: [], ...more });
    const saved = { id: "s", title: "Runway", updated_at: "2026-10-22T09:00:00", messages: [
      msg("user", "Runway?", { steps: [] }),
      msg("assistant", "About 9 months.", { tools: [{ name: "overview", label: "The overview" }], steps: [
        { kind: "thinking", text: "Check the overview.", tools: [] },
        { kind: "text", text: "Looking at the overview first.", tools: [] },
        { kind: "tools", text: "", tools: [{ name: "overview", label: "The overview" }] }] }),
    ] };
    // saved before answers kept their steps: no `steps` at all
    const older = { id: "o", title: "Kopi", updated_at: "2026-10-01T09:00:00", messages: [
      msg("user", "Kopi?"),
      msg("assistant", "S$310 on kopi", { tools: [{ name: "query", label: "Looking it up in the record" }, { name: "trips", label: "Trips" }] }),
    ] } as unknown as CopilotThread;
    stub(aiOn, (url) => {
      if (url === "/api/copilot/threads") return new Response(JSON.stringify({ threads: [
        { id: "s", title: "Runway", updated_at: saved.updated_at, turns: 1 },
        { id: "o", title: "Kopi", updated_at: older.updated_at, turns: 1 }] }), { status: 200 });
      if (url === "/api/copilot/threads/s") return new Response(JSON.stringify(saved), { status: 200 });
      if (url === "/api/copilot/threads/o") return new Response(JSON.stringify(older), { status: 200 });
      return undefined;
    });
    render(<Panel />);
    fireEvent.click(await screen.findByRole("button", { name: "History" }));
    fireEvent.click(within(await screen.findByRole("list", { name: "Threads" })).getByRole("button", { name: /^Runway/ }));
    const steps = await screen.findByRole("list", { name: "Steps" });
    expect(within(steps).getAllByRole("listitem").map((r) => r.textContent)).toEqual([
      "Thought", "Looking at the overview first.", "Used 1 toolOverview"]);
    expect(within(steps).getByRole("button", { name: "Thought" })).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(screen.getByRole("button", { name: "History" }));
    fireEvent.click(within(await screen.findByRole("list", { name: "Threads" })).getByRole("button", { name: /^Kopi/ }));
    expect(await screen.findByText("S$310 on kopi")).toBeInTheDocument();
    const row = within(screen.getByRole("list", { name: "Steps" })).getByRole("button", { name: /^Used 2 tools/ });
    expect(row).toHaveTextContent("Query, Trips");
    fireEvent.click(row);
    expect(screen.getByRole("list", { name: "Tools used" })).toHaveTextContent("Looking it up in the recordTrips");
  });
});

describe("a confirm card", () => {
  const ruleCard: CopilotCard = {
    id: "c1", kind: "rule", title: "File GRAB* under Transport", summary: "A rule for 3 rows now in Uncategorised.",
    preview: ["2 Sep 2026 GRAB*TRIP S$14.20", "9 Sep 2026 GRAB*TRIP S$9.80", "15 Sep 2026 GRAB*TRIP S$22.00"],
    request: { path: "/api/rules/save", body: { rule: { pattern: "GRAB*", category: "Transport" }, edit: null } }, undo: null,
  };

  it("saves only on Confirm, then Undo uses the endpoint's own way back", async () => {
    stub(aiOn, (url) => {
      if (url === "/api/copilot/ask") return lines([{ type: "text", text: "Here is a rule." }, { type: "card", card: ruleCard }, { type: "done" }]);
      if (url === "/api/rules/save") return new Response(JSON.stringify({ file: "merchants.csv", line: 4, change: "c-77", relinked: { changed: 3 } }), { status: 200 });
      if (url === "/api/rules/undo") return new Response(JSON.stringify({ undone: "GRAB*", relinked: { changed: 3 } }), { status: 200 });
      return undefined;
    });
    render(<Panel />);
    await askIt("File my Grab rides");
    const card = await screen.findByRole("region", { name: "File GRAB* under Transport" });
    expect(within(card).getByText("9 Sep 2026 GRAB*TRIP S$9.80")).toBeInTheDocument();
    expect(posts.some((p) => p.url === "/api/rules/save")).toBe(false);
    fireEvent.click(within(card).getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(posts).toContainEqual({ url: "/api/rules/save", body: ruleCard.request.body }));
    expect(await within(card).findByText("Saved")).toBeInTheDocument();
    expect(await screen.findByText("File GRAB* under Transport: saved, 3 rows changed.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    await waitFor(() => expect(posts).toContainEqual({ url: "/api/rules/undo", body: { change: "c-77" } }));
    expect(await screen.findByText("Undone, 3 rows changed back.")).toBeInTheDocument();
  });

  // G22: a budget card carries no Undo: the amount before is read at Confirm, and read again before Undo puts it back
  const budget: CopilotCard = {
    id: "c2", kind: "budget", title: "Budget: Food, Oct 2026", summary: "Planned S$800.00 now, S$900.00 after", preview: [],
    request: { path: "/api/budgets", body: { category_id: 7, category: "Food", month: "2026-10", planned: 90_000 } }, undo: null,
  };
  function budgetServer(start: number | null) {
    const state = { planned: start, seen: [] as string[] };
    stub(aiOn, (url, init) => {
      if (url === "/api/copilot/ask") return lines([{ type: "card", card: budget }, { type: "done" }]);
      if (url.startsWith("/api/budgets")) {
        state.seen.push(`${init?.method ?? "GET"} ${url}`);
        if (init?.method === "POST") {
          state.planned = JSON.parse(init.body as string).planned;
          return new Response("{}", { status: 200 });
        }
        return new Response(JSON.stringify({ month: "2026-10", groups: [], months: [], need_classes: [], rows: [
          { category_id: 3, category: "Transport", need_class: null, bucket: "Spend", category_group: "Daily", kind: "spend", planned: 20_000, actual: 0 },
          { category_id: 7, category: "Food", need_class: null, bucket: "Spend", category_group: "Daily", kind: "spend", planned: state.planned, actual: 0 },
        ] }), { status: 200 });
      }
      return undefined;
    });
    return state;
  }
  const budgetPosts = () => posts.filter((p) => p.url === "/api/budgets").map((p) => p.body);

  it("a budget's Confirm reads the amount first, and its Undo puts that amount back", async () => {
    const server = budgetServer(80_000);
    render(<Panel />);
    await askIt("Raise food");
    fireEvent.click(await screen.findByRole("button", { name: "Confirm" }));
    fireEvent.click(await screen.findByRole("button", { name: "Undo" }));
    await waitFor(() => expect(budgetPosts()).toEqual([budget.request.body, { ...budget.request.body, planned: 80_000 }]));
    expect(server.seen).toEqual([
      "GET /api/budgets?month=2026-10", "POST /api/budgets", "GET /api/budgets?month=2026-10", "POST /api/budgets",
    ]);
    expect(await screen.findByText("Undone.")).toBeInTheDocument();
    expect(server.planned).toBe(80_000);
  });

  it("a budget's Undo puts back no amount when there was none", async () => {
    budgetServer(null);
    render(<Panel />);
    await askIt("Set food");
    fireEvent.click(await screen.findByRole("button", { name: "Confirm" }));
    fireEvent.click(await screen.findByRole("button", { name: "Undo" }));
    await waitFor(() => expect(budgetPosts()).toEqual([budget.request.body, { ...budget.request.body, planned: null }]));
  });

  it("a budget's Undo leaves the amount alone when it changed since Confirm", async () => {
    const server = budgetServer(80_000);
    render(<Panel />);
    await askIt("Raise food");
    fireEvent.click(await screen.findByRole("button", { name: "Confirm" }));
    const undo = await screen.findByRole("button", { name: "Undo" });
    // the Plan page set it again meanwhile
    server.planned = 95_000;
    fireEvent.click(undo);
    expect(await screen.findByText("Budget: Food, Oct 2026 changed since, so it was left as it is.")).toBeInTheDocument();
    expect(budgetPosts()).toEqual([budget.request.body]);
    expect(server.planned).toBe(95_000);
  });

  it("keeps Saved after the panel closes and opens again, so Confirm never shows twice", async () => {
    stub(aiOn, (url) => {
      if (url === "/api/copilot/ask") return lines([{ type: "card", card: ruleCard }, { type: "card", card: { ...ruleCard, id: "c9", title: "Second rule" } }, { type: "done" }]);
      if (url === "/api/rules/save") return new Response(JSON.stringify({ change: "c-77" }), { status: 200 });
      return undefined;
    });
    render(<Shell />);
    await askIt("File my Grab rides");
    const card = await screen.findByRole("region", { name: "File GRAB* under Transport" });
    fireEvent.click(within(card).getByRole("button", { name: "Confirm" }));
    expect(await within(card).findByText("Saved")).toBeInTheDocument();
    fireEvent.click(within(screen.getByRole("region", { name: "Second rule" })).getByRole("button", { name: "Not now" }));
    fireEvent.click(screen.getByRole("button", { name: "Toggle panel" }));
    expect(screen.queryByRole("region", { name: "File GRAB* under Transport" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Toggle panel" }));
    const again = await screen.findByRole("region", { name: "File GRAB* under Transport" });
    expect(within(again).getByText("Saved")).toBeInTheDocument();
    expect(within(again).queryByRole("button", { name: "Confirm" })).not.toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "Second rule" })).getByText("Not saved")).toBeInTheDocument();
    expect(posts.filter((p) => p.url === "/api/rules/save")).toHaveLength(1);
  });

  it("marks a card in a saved thread done once it was confirmed during this visit", async () => {
    const thread: CopilotThread = { id: "b", title: "Grab", updated_at: "2026-10-22T09:00:00", messages: [
      { role: "user", text: "File my Grab rides", at: "2026-10-22T09:00:00", page: null, tools: [], steps: [], charts: [], links: [], cards: [], followups: [] },
      { role: "assistant", text: "Here is a rule.", at: "2026-10-22T09:00:05", page: null, tools: [], steps: [], charts: [], links: [], cards: [ruleCard], followups: [] },
    ] };
    stub(aiOn, (url) => {
      if (url === "/api/copilot/threads") return new Response(JSON.stringify({ threads: [{ id: "b", title: "Grab", updated_at: thread.updated_at, turns: 2 }] }), { status: 200 });
      if (url === "/api/copilot/threads/b") return new Response(JSON.stringify(thread), { status: 200 });
      if (url === "/api/rules/save") return new Response(JSON.stringify({ change: "c-77" }), { status: 200 });
      return undefined;
    });
    render(<Panel />);
    const openB = async () => {
      fireEvent.click(await screen.findByRole("button", { name: "History" }));
      fireEvent.click(await screen.findByRole("button", { name: /^Grab/ }));
      return screen.findByRole("region", { name: "File GRAB* under Transport" });
    };
    fireEvent.click(within(await openB()).getByRole("button", { name: "Confirm" }));
    expect(await screen.findByText("Saved")).toBeInTheDocument();
    // a new thread, then the saved one opened again
    fireEvent.click(screen.getByRole("button", { name: "History" }));
    fireEvent.click(await screen.findByRole("button", { name: "New thread" }));
    expect(screen.queryByRole("region", { name: "File GRAB* under Transport" })).not.toBeInTheDocument();
    const card = await openB();
    expect(within(card).getByText("Saved")).toBeInTheDocument();
    expect(within(card).queryByRole("button", { name: "Confirm" })).not.toBeInTheDocument();
  });

  it("Not now leaves the record alone", async () => {
    stub(aiOn, (url) => (url === "/api/copilot/ask" ? lines([{ type: "card", card: ruleCard }, { type: "done" }]) : undefined));
    render(<Panel />);
    await askIt("File my Grab rides");
    fireEvent.click(await screen.findByRole("button", { name: "Not now" }));
    expect(screen.getByText("Not saved")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Confirm" })).not.toBeInTheDocument();
    expect(posts.some((p) => p.url === "/api/rules/save")).toBe(false);
  });
});

describe("choosing threads", () => {
  it("drops a thread that arrives after a later History pick or New thread", async () => {
    const waiting: Record<string, (r: Response) => void> = {};
    const thread = (id: string, q: string): CopilotThread => ({ id, title: q, updated_at: "2026-10-23T00:00:00+00:00", messages: [
      { role: "user", text: q, at: "2026-10-23T00:00:00+00:00", page: null, tools: [], steps: [], charts: [], links: [], cards: [], followups: [] }] });
    vi.stubGlobal("fetch", vi.fn((url: string) => new Promise<Response>((done) => { waiting[url] = done; })));
    const { result } = renderHook(() => useCopilot());
    let first!: Promise<void>, second!: Promise<void>;
    act(() => { first = result.current.open("aaaaaaaaaaaaaaaa"); });
    act(() => { second = result.current.open("bbbbbbbbbbbbbbbb"); });
    await act(async () => { waiting["/api/copilot/threads/bbbbbbbbbbbbbbbb"](new Response(JSON.stringify(thread("bbbbbbbbbbbbbbbb", "Second")))); await second; });
    await act(async () => { waiting["/api/copilot/threads/aaaaaaaaaaaaaaaa"](new Response(JSON.stringify(thread("aaaaaaaaaaaaaaaa", "First")))); await first; });
    expect(result.current.thread).toBe("bbbbbbbbbbbbbbbb");
    expect(result.current.turns.map((t) => t.text)).toEqual(["Second"]);

    let third!: Promise<void>;
    act(() => { third = result.current.open("cccccccccccccccc"); });
    act(() => { result.current.reset(); });
    await act(async () => { waiting["/api/copilot/threads/cccccccccccccccc"](new Response(JSON.stringify(thread("cccccccccccccccc", "Third")))); await third; });
    expect(result.current.thread).toBeNull();
    expect(result.current.turns).toEqual([]);
  });
});

describe("a reopened answer's steps", () => {
  it("drops a text step the saved answer repeats, as the live panel does", () => {
    const tools = [{ name: "follow_ups", label: "Follow-ups" }];
    const steps = [{ kind: "thinking" as const, text: "Sum it.", tools: [] },
      { kind: "text" as const, text: "Groceries came to S$100.00.", tools: [] }];
    expect(stepsOf({ text: "Groceries came to S$100.00.", tools, steps }).map((s) => s.kind)).toEqual(["thinking"]);
    expect(stepsOf({ text: "Something else.", tools, steps }).map((s) => s.kind)).toEqual(["thinking", "text"]);
  });
});

describe("an answer the panel cannot draw", () => {
  // Audit: a part in a shape this build does not know used to take the whole app down with it
  it("shows a problem in the panel and keeps the box", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const odd = { type: "chart", chart: { title: "Odd", result: "not a result" } } as unknown as CopilotEvent;
    stub(aiOn, (url) => (url === "/api/copilot/ask" ? lines([odd, { type: "done" }]) : undefined));
    render(<Panel />);
    await askIt("Show me something odd");
    expect(await screen.findByText(ANSWER_FAILED)).toBeInTheDocument();
    await waitFor(() => expect(box()).toBeEnabled());
    vi.mocked(console.error).mockRestore();
  });
});
