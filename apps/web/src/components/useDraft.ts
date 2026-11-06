import { useRef, useState } from "react";
import { useUnsavedGuard } from "../router";

// Audit (G18): a card's settings typed into boxes and saved with a SaveBar, once
// for every such card (AI, Scam checks). The draft holds only the boxes typed in,
// so a save from another tab still shows in every box left alone, and a box typed
// back to its saved value is no edit, unless a save is on its way (the saved value
// is about to change under it).
//
// A save that lands keeps what was typed while it was on its way, and what it sent
// stands for the saved values until the card is read again after it (`answer`, the
// card's answer from the server, tells a newer read from the one on screen when it
// landed), so the boxes never flash the old values. A save that fails saved nothing:
// a box typed back to the saved value meanwhile is no edit after all. Leaving the
// page asks first while anything is unsaved or a save is on its way.

type Values = Record<string, unknown>;

export function useDraft<T extends Values>(fromServer: T | null, answer: unknown) {
  const [draft, setDraft] = useState<Partial<T> | null>(null);
  const [saving, setSaving] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const inFlight = useRef(false);
  const latest = useRef({ answer, fromServer });
  latest.current = { answer, fromServer };
  // what the last save sent; `landedOn` is the answer on screen when it landed
  const [sending, setSending] = useState<{ sent: T; landed: boolean; landedOn?: unknown } | null>(null);
  const standing = !!sending && (!sending.landed || sending.landedOn === answer);
  const saved = fromServer && standing ? { ...fromServer, ...sending.sent } : fromServer;
  const value = saved ? ({ ...saved, ...draft } as T) : null;
  const keys = saved ? (Object.keys(saved) as (keyof T)[]) : [];
  const dirty = !!saved && keys.some((k) => value![k] !== saved[k]);
  useUnsavedGuard(dirty || (!!sending && !sending.landed));

  // the boxes still typed in that differ from `from`
  const left = (now: Partial<T> | null, from: T) => {
    const rest = Object.fromEntries(Object.entries(now ?? {}).filter(([k, v]) => v !== from[k])) as Partial<T>;
    return Object.keys(rest).length ? rest : null;
  };

  const set = (patch: Partial<T>) => {
    setDraft((now) => {
      const next = { ...now, ...patch };
      if (!inFlight.current && saved) for (const k of keys) if (k in next && next[k] === saved[k]) delete next[k];
      return Object.keys(next).length ? next : null;
    });
    setProblem(null);
  };

  const discard = () => { setDraft(null); setProblem(null); };

  /** Sends what the boxes show; a refusal lands in `problem`. */
  async function save(send: (sent: T) => Promise<unknown>) {
    if (!value) return;
    const sent = { ...value };
    setProblem(null);
    setSaving(true);
    inFlight.current = true;
    setSending({ sent, landed: false });
    try {
      await send(sent);
      setDraft((now) => left(now, sent));
      setSending((s) => (s?.sent === sent ? { sent, landed: true, landedOn: latest.current.answer } : s));
    } catch (e) {
      setSending((s) => (s?.sent === sent ? null : s));
      const server = latest.current.fromServer;
      if (server) setDraft((now) => left(now, server));
      setProblem((e as Error).message);
    } finally {
      inFlight.current = false;
      setSaving(false);
    }
  }

  return { value, saved, dirty, saving, problem, setProblem, set, discard, save };
}
