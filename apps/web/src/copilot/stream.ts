// G22: POST /api/copilot/ask answers with one line of JSON per event as it
// happens (application/x-ndjson), so the tool lines show while the model
// works. postJSON reads a whole body at once, so the question goes through
// fetch here, with the header every write carries, and the body is read line
// by line as it arrives.
import { ApiError, checkServer, OFFLINE } from "../api";
import type { CopilotEvent, CopilotPage } from "../types.gen";

/** Audit: a line that is not JSON, in place of the parser's words. */
export const GARBLED = "The answer came back garbled, so it stopped there.";

/**
 * Every event in an ndjson body, in order, as each line completes. A chunk can
 * end mid-line (the rest comes with the next chunk) or hold several lines; a
 * last line with no newline after it still counts. Blank lines are skipped.
 * Audit: a line that does not parse, or a listener that throws, cancels the
 * body before the error goes on, so the request is not left running.
 */
export async function readEvents(body: ReadableStream<Uint8Array>, onEvent: (e: CopilotEvent) => void): Promise<void> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let held = "";
  const emit = (line: string) => {
    const text = line.trim();
    if (text) onEvent(JSON.parse(text) as CopilotEvent);
  };
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      held += decoder.decode(value, { stream: true });
      let at = held.indexOf("\n");
      while (at !== -1) {
        emit(held.slice(0, at));
        held = held.slice(at + 1);
        at = held.indexOf("\n");
      }
    }
    held += decoder.decode();
    emit(held);
  } catch (e) {
    await reader.cancel().catch(() => {});
    throw e;
  }
}

export type AskBody = { thread: string | null; question: string; page: CopilotPage };

/**
 * Ask a question; `onEvent` hears each event as it comes. A refusal before any
 * line (the copilot is off, consent is off) throws an ApiError with the
 * server's words; a server that is not there throws the app's plain OFFLINE.
 * `signal` stops the request (the panel's Stop): the promise then rejects with
 * an AbortError.
 */
export async function ask(body: AskBody, onEvent: (e: CopilotEvent) => void, signal?: AbortSignal): Promise<void> {
  let r: Response;
  try {
    r = await fetch("/api/copilot/ask", {
      method: "POST",
      body: JSON.stringify(body),
      headers: { "X-Kopifund": "1", "Content-Type": "application/json" },
      signal,
    });
  } catch (e) {
    if (isAbort(e)) throw e;
    // the banner takes it from here, as it does for any other failed ask
    void checkServer();
    throw new ApiError(OFFLINE);
  }
  if (!r.ok) {
    let said: string | null = null;
    try {
      said = ((await r.json()) as { error?: string })?.error ?? null;
    } catch {
      /* not JSON */
    }
    if (!said && r.status >= 500) {
      void checkServer();
      throw new ApiError(OFFLINE, r.status);
    }
    throw new ApiError(said ?? `${r.status} ${r.statusText}`, r.status);
  }
  if (!r.body) {
    // a body the browser hands over whole (no stream): the same lines, read at once
    const text = await r.text();
    try {
      for (const line of text.split("\n")) if (line.trim()) onEvent(JSON.parse(line) as CopilotEvent);
    } catch (e) {
      throw e instanceof SyntaxError ? new ApiError(GARBLED, r.status) : e;
    }
    return;
  }
  try {
    await readEvents(r.body, onEvent);
  } catch (e) {
    if (isAbort(e)) throw e;
    if (e instanceof SyntaxError) throw new ApiError(GARBLED, r.status);
    // the connection dropped after the answer began
    void checkServer();
    throw new ApiError(OFFLINE);
  }
}

export const isAbort = (e: unknown) => e instanceof DOMException && e.name === "AbortError"
  || (e instanceof Error && e.name === "AbortError");
