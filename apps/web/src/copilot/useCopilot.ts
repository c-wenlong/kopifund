// G22: the copilot's conversation, held by the shell so the side panel and the
// phone's bottom sheet show the same thread, and closing the panel keeps it.
import { useCallback, useRef, useState } from "react";
import { getJSON } from "../api";
import { showToast } from "../components/Toast";
import type { CopilotCard, CopilotChart, CopilotEvent, CopilotLink, CopilotMessage, CopilotPage, CopilotStep, CopilotThread, CopilotTool } from "../types.gen";
import { ask, isAbort } from "./stream";

/** `running` while events still arrive; `stopped` after Stop; `error` with the words to show. */
export type TurnStatus = "running" | "done" | "stopped" | "error";

/** A confirm card's state: not answered yet, its write on the way, saved, or turned down with Not now. */
export type CardState = "open" | "saving" | "saved" | "skipped";

/** G22: what an answer that ended without its `done` line says: the connection or the server gave out mid-answer. */
export const CUT_SHORT = "The answer stopped before it finished.";

export type Turn = {
  id: number;
  role: "user" | "assistant";
  text: string;
  page: CopilotPage | null;
  tools: CopilotTool[];
  /** G22: the answer's steps in order (thinking, tools, text written between rounds), shown above the answer. */
  steps: CopilotStep[];
  charts: CopilotChart[];
  links: CopilotLink[];
  cards: CopilotCard[];
  followups: string[];
  status: TurnStatus;
  error: string | null;
};

let nextId = 1;
const turn = (role: Turn["role"], text: string, page: CopilotPage | null, status: TurnStatus = "done"): Turn => ({
  id: nextId++, role, text, page, tools: [], steps: [], charts: [], links: [], cards: [], followups: [], status, error: null,
});

/** G22: a tool joins the tools step just before it; after a step of another kind it starts a new one. */
function addTool(steps: CopilotStep[], tool: CopilotTool): CopilotStep[] {
  const last = steps[steps.length - 1];
  if (last?.kind === "tools") return [...steps.slice(0, -1), { ...last, tools: [...last.tools, tool] }];
  return [...steps, { kind: "tools", text: "", tools: [tool] }];
}

/**
 * G22: the steps without the text step the final answer repeats: the server
 * makes a round's text the answer when the model wrote its answer beside a
 * last tool call (follow-ups) and then nothing more.
 */
function withoutAnswer(steps: CopilotStep[], answer: string): CopilotStep[] {
  let at = steps.length - 1;
  while (at >= 0 && steps[at].kind === "thinking") at--;
  return at >= 0 && steps[at].kind === "text" && steps[at].text === answer ? steps.filter((_, i) => i !== at) : steps;
}

/** G22: a saved answer's steps; a thread saved before steps existed shows its tools as one tools step. */
export function stepsOf(m: Pick<CopilotMessage, "tools" | "text"> & { steps?: CopilotStep[] | null }): CopilotStep[] {
  // the same rule as live: a text step the final answer repeats is not shown twice
  if (m.steps?.length) return withoutAnswer(m.steps, m.text ?? "");
  return m.tools?.length ? [{ kind: "tools", text: "", tools: m.tools }] : [];
}

/** One event applied to the answer it belongs to. */
export function applyEvent(t: Turn, e: CopilotEvent): Turn {
  switch (e.type) {
    case "thinking": return e.text ? { ...t, steps: [...t.steps, { kind: "thinking", text: e.text, tools: [] }] } : t;
    case "tool": return e.tool ? { ...t, tools: [...t.tools, e.tool], steps: addTool(t.steps, e.tool) } : t;
    case "say": return e.text ? { ...t, steps: [...t.steps, { kind: "text", text: e.text, tools: [] }] } : t;
    // the whole answer comes in one event
    case "text": return e.text == null ? t : { ...t, text: e.text, steps: withoutAnswer(t.steps, e.text) };
    case "chart": return e.chart ? { ...t, charts: [...t.charts, e.chart] } : t;
    case "link": return e.link ? { ...t, links: [...t.links, e.link] } : t;
    case "card": return e.card ? { ...t, cards: [...t.cards, e.card] } : t;
    case "followups": return { ...t, followups: e.followups ?? [] };
    case "error": return { ...t, status: "error", error: e.message ?? "The copilot could not answer." };
    case "done": return t.status === "running" ? { ...t, status: "done" } : t;
    default: return t;
  }
}

export type Copilot = ReturnType<typeof useCopilot>;

export function useCopilot() {
  const [thread, setThread] = useState<string | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [running, setRunning] = useState(false);
  const [history, setHistory] = useState(false);
  // G22: each confirm card's state by its id, for the whole visit: closing the panel, starting a new
  // thread or opening a saved one keeps it, so a change already saved never offers Confirm again
  const [cards, setCards] = useState<Record<string, CardState>>({});
  const setCard = useCallback((id: string, state: CardState) => setCards((all) => ({ ...all, [id]: state })), []);
  const control = useRef<AbortController | null>(null);
  // the thread a question goes to, read when it is sent (a `thread` event may have just set it)
  const threadNow = useRef<string | null>(null);

  const turnsNow = useRef(turns);
  turnsNow.current = turns;

  const pickThread = (id: string | null) => {
    threadNow.current = id;
    setThread(id);
  };

  const stop = useCallback(() => {
    control.current?.abort();
    control.current = null;
  }, []);

  const send = useCallback(async (question: string, page: CopilotPage) => {
    const q = question.trim();
    if (!q || control.current) return;
    const mine = new AbortController();
    control.current = mine;
    const answer = turn("assistant", "", null, "running");
    setTurns((all) => [...all, turn("user", q, page), answer]);
    setRunning(true);
    setHistory(false);
    const update = (fn: (t: Turn) => Turn) => setTurns((all) => all.map((t) => (t.id === answer.id ? fn(t) : t)));
    // G22: an answer is whole only when its `done` line came; a body that just ends was cut short
    let finished = false;
    try {
      await ask({ thread: threadNow.current, question: q, page }, (e) => {
        if (e.type === "done") finished = true;
        if (e.type === "thread" && e.thread) pickThread(e.thread.id);
        else update((t) => applyEvent(t, e));
      }, mine.signal);
      if (!finished) update((t) => (t.status === "running" ? { ...t, status: "error", error: CUT_SHORT } : t));
    } catch (e) {
      if (isAbort(e)) update((t) => ({ ...t, status: "stopped" }));
      else {
        // Audit: whatever went wrong, the request ends here, so the model is not left answering no one
        mine.abort();
        update((t) => ({ ...t, status: "error", error: (e as Error).message }));
      }
    } finally {
      // Stop clears the controller at once; a question sent after it keeps its own running state
      if (control.current === mine) control.current = null;
      if (control.current === null) setRunning(false);
    }
  }, []);

  /**
   * Ask the last question again, in place of the answer that failed. The
   * server keeps no turn that ended in an error, so the question is simply
   * sent again; here the failed pair goes, so the question shows once.
   */
  const retry = useCallback(() => {
    if (control.current) return;
    const all = turnsNow.current;
    const at = all.length - 2;
    if (at < 0 || all[at].role !== "user" || all[at + 1].status !== "error") return;
    const { text, page } = all[at];
    const failed = new Set([all[at].id, all[at + 1].id]);
    setTurns((now) => now.filter((t) => !failed.has(t.id)));
    void send(text, page ?? { path: window.location.pathname, title: "", filters: {} });
  }, [send]);

  // G22: the latest History pick or New thread; a thread that arrives after a later choice is dropped
  const choice = useRef(0);

  /** Open a saved thread from the list. */
  const open = useCallback(async (id: string) => {
    stop();
    const mine = ++choice.current;
    try {
      const t = await getJSON<CopilotThread>(`/api/copilot/threads/${encodeURIComponent(id)}`);
      if (mine !== choice.current) return;
      setTurns(t.messages.map((m) => ({ ...turn(m.role, m.text, m.page), tools: m.tools, steps: stepsOf(m), charts: m.charts,
        links: m.links, cards: m.cards, followups: m.followups })));
      pickThread(t.id);
      setHistory(false);
    } catch (e) {
      if (mine === choice.current) showToast({ tone: "error", message: (e as Error).message });
    }
  }, [stop]);

  /** Start afresh: the next question opens a new thread. */
  const reset = useCallback(() => {
    stop();
    choice.current++;
    setTurns([]);
    pickThread(null);
    setHistory(false);
  }, [stop]);

  return { thread, turns, running, history, setHistory, send, stop, retry, open, reset, forget: pickThread, cards, setCard };
}
