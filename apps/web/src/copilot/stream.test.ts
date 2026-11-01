import { afterEach, describe, expect, it, vi } from "vitest";
import type { CopilotEvent } from "../types.gen";
import { ask, readEvents } from "./stream";

// G22: the copilot's answer is one JSON event per line, read as the lines arrive.

const enc = new TextEncoder();
const streamOf = (chunks: string[]) => new ReadableStream<Uint8Array>({
  start(c) {
    for (const ch of chunks) c.enqueue(enc.encode(ch));
    c.close();
  },
});

async function read(chunks: string[]) {
  const seen: CopilotEvent[] = [];
  await readEvents(streamOf(chunks), (e) => seen.push(e));
  return seen;
}

afterEach(() => vi.unstubAllGlobals());

describe("readEvents", () => {
  it("joins a line split across chunks, even mid-character", async () => {
    const line = JSON.stringify({ type: "text", text: "Kopi at S$1.80 ☕" });
    const bytes = enc.encode(`${line}\n`);
    // cut inside the multi-byte cup, so the decoder must hold the half character
    const cut = bytes.length - 3;
    const seen: CopilotEvent[] = [];
    await readEvents(new ReadableStream<Uint8Array>({
      start(c) {
        c.enqueue(bytes.slice(0, 7));
        c.enqueue(bytes.slice(7, cut));
        c.enqueue(bytes.slice(cut));
        c.close();
      },
    }), (e) => seen.push(e));
    expect(seen).toEqual([{ type: "text", text: "Kopi at S$1.80 ☕" }]);
  });

  it("reads several events from one chunk, in order", async () => {
    const seen = await read([
      '{"type":"tool","tool":{"name":"spending","label":"Read spending"}}\n{"type":"tool","tool":{"name":"query","label":"Ran a query"}}\n{"type":"te',
      'xt","text":"Done"}\n{"type":"done"}\n',
    ]);
    expect(seen.map((e) => e.type)).toEqual(["tool", "tool", "text", "done"]);
    expect(seen[1].tool?.label).toBe("Ran a query");
  });

  it("keeps a last line with no newline after it, and skips blank lines", async () => {
    const seen = await read(['{"type":"text","text":"a"}\n\n', '\n{"type":"done"}']);
    expect(seen.map((e) => e.type)).toEqual(["text", "done"]);
  });
});

describe("ask", () => {
  it("posts the question with the write header and hands over each event", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      return new Response(streamOf(['{"type":"thread","thread":{"id":"t1","title":"Kopi","updated_at":"2026-10-23T10:00:00","turns":1}}\n', '{"type":"done"}\n']),
        { status: 200, headers: { "Content-Type": "application/x-ndjson" } });
    }));
    const seen: string[] = [];
    await ask({ thread: null, question: "How much on kopi?", page: { path: "/spending", title: "Spending", filters: { month: "2026-09" } } },
      (e) => seen.push(e.type));
    expect(seen).toEqual(["thread", "done"]);
    expect(calls[0].url).toBe("/api/copilot/ask");
    expect(calls[0].init?.method).toBe("POST");
    expect((calls[0].init?.headers as Record<string, string>)["X-Kopifund"]).toBe("1");
    expect(JSON.parse(calls[0].init?.body as string)).toEqual({
      thread: null, question: "How much on kopi?", page: { path: "/spending", title: "Spending", filters: { month: "2026-09" } },
    });
  });

  it("throws the server's words for a refusal before any line", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ error: "Turn on Send matching rows in Settings first." }), { status: 422 })));
    await expect(ask({ thread: null, question: "q", page: { path: "/", title: "Home", filters: {} } }, () => {}))
      .rejects.toMatchObject({ message: "Turn on Send matching rows in Settings first.", status: 422 });
  });
});
