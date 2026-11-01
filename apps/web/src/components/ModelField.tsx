// G22: a model name with suggestions from the provider's own list
// (GET /api/ai/models, which the server fetches with the saved key and keeps
// a day). Built on Combobox's look, but any typed name is kept: the list can
// be missing (no key, a provider with no list) or older than the newest model.
// It asks only once typing has paused for 250ms, and remembers each answer
// for the visit, so a query already seen, or one a complete answer already
// covers, asks nothing.
import { useEffect, useId, useLayoutEffect, useRef, useState, type KeyboardEvent } from "react";
import { getJSON } from "../api";
import type { AiModel, AiModels } from "../types.gen";

export const MODEL_PAUSE_MS = 250;
/** The most models the server answers for one query (finance/ai.py, MODELS_SHOWN): an answer this long may be cut short. */
export const MODELS_CAP = 100;

// each (provider, query) answer that came back with a list, for the visit; a query of "" is the provider's whole list.
// An answer that carries `error` (no key yet, the provider refused) is never kept, so the next ask tries again.
const answers = new Map<string, AiModels>();
const keyOf = (provider: string, q: string) => `${provider}\n${q.toLowerCase()}`;

/**
 * Forget the answers kept for `provider`, or for every provider when none is
 * named: the AI card calls it when a key is saved or removed, since the list
 * the server can fetch changes with the key. Tests call it with nothing.
 */
export function clearModelCache(provider?: string) {
  if (provider === undefined) answers.clear();
  else for (const k of [...answers.keys()]) if (k.startsWith(`${provider}\n`)) answers.delete(k);
}

const matches = (m: AiModel, q: string) => {
  const t = q.toLowerCase();
  return m.id.toLowerCase().includes(t) || m.label.toLowerCase().includes(t);
};

/**
 * The answer for `q` without asking: the one kept for it, else one kept for a
 * shorter query inside it, filtered here. Only a complete answer (fewer than
 * the server's cap) can be filtered: one of 100 may leave out models `q` matches.
 */
export function keptModels(provider: string, q: string): AiModels | undefined {
  const exact = answers.get(keyOf(provider, q));
  if (exact) return exact;
  const t = q.toLowerCase();
  for (const [k, a] of answers) {
    const [p, kq] = [k.slice(0, k.indexOf("\n")), k.slice(k.indexOf("\n") + 1)];
    // every model `q` matches also matches a query inside it, so that query's whole answer holds them all
    if (p === provider && t.includes(kq) && a.models.length < MODELS_CAP) {
      return { ...a, models: a.models.filter((m) => matches(m, q)) };
    }
  }
  return undefined;
}

export function ModelField({ label, provider, value, onChange, placeholder }: {
  label: string; provider: string; value: string; onChange: (value: string) => void; placeholder?: string;
}) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const [list, setList] = useState<AiModels | null>(null);
  const [active, setActive] = useState(-1);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const asked = useRef(0);

  // a new provider has another list: the one shown goes at once
  const [seenProvider, setSeenProvider] = useState(provider);
  if (seenProvider !== provider) {
    setSeenProvider(provider);
    setList(null);
    setActive(-1);
  }
  // and a pause timed for the old one never asks, and an answer still on its way for it is dropped
  // (a layout effect, so it runs before any answer can land)
  const first = useRef(true);
  useLayoutEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
    asked.current++;
  }, [provider]);
  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
  }, []);

  function lookup(q: string) {
    const kept = keptModels(provider, q);
    const mine = ++asked.current;
    if (kept) {
      setList(kept);
      return;
    }
    getJSON<AiModels>(`/api/ai/models?provider=${encodeURIComponent(provider)}&q=${encodeURIComponent(q)}`).then(
      (a) => {
        if (!a.error) answers.set(keyOf(provider, q), a);
        if (mine === asked.current) setList(a);
      },
      (e: Error) => {
        // not kept: the next pause asks again
        if (mine === asked.current) setList({ provider: provider as AiModels["provider"], models: [], fetched_at: null, error: e.message });
      },
    );
  }

  function later(q: string) {
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      timer.current = null;
      lookup(q);
    }, MODEL_PAUSE_MS);
  }

  const shown = list?.models ?? [];
  function choose(m: AiModel) {
    onChange(m.id);
    setOpen(false);
    setActive(-1);
  }

  function onKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if ((e.key === "ArrowDown" || e.key === "ArrowUp") && shown.length) {
      e.preventDefault();
      setOpen(true);
      const n = shown.length;
      setActive((a) => (e.key === "ArrowDown" ? (a < 0 ? 0 : (a + 1) % n) : a <= 0 ? n - 1 : a - 1));
    } else if (e.key === "Enter" && open && active >= 0 && shown[active]) {
      e.preventDefault();
      choose(shown[active]);
    } else if (e.key === "Escape" && open) {
      // used up here, as Combobox does, so a Sheet or the panel around the field stays open
      e.preventDefault();
      e.nativeEvent.stopPropagation();
      setOpen(false);
    }
  }

  const listId = `${id}-list`;
  const showing = open && shown.length > 0;
  return (
    <div className="relative flex flex-col gap-1">
      <input
        role="combobox"
        aria-label={label}
        aria-expanded={showing}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={showing && active >= 0 ? `${id}-${active}` : undefined}
        value={value}
        placeholder={placeholder}
        spellCheck={false}
        autoComplete="off"
        onChange={(e) => {
          onChange(e.target.value);
          setOpen(true);
          setActive(-1);
          later(e.target.value.trim());
        }}
        onFocus={() => {
          setOpen(true);
          // the whole list once typing pauses; at once when it is already kept
          const kept = keptModels(provider, "");
          if (kept) {
            // an answer still on its way for an older query no longer shows over it
            asked.current++;
            setList(kept);
          } else later("");
        }}
        onBlur={() => {
          setOpen(false);
          setActive(-1);
        }}
        onKeyDown={onKeyDown}
        className="h-8 w-full rounded-md border border-line bg-panel px-2 text-[13px]"
      />
      {showing && (
        <ul id={listId} role="listbox" aria-label={label}
          className="absolute top-9 right-0 left-0 z-30 m-0 max-h-56 list-none overflow-auto rounded-md border border-line bg-panel p-1 shadow-lg">
          {shown.map((m, i) => (
            <li key={m.id} id={`${id}-${i}`} role="option" aria-selected={m.id === value}
              onMouseDown={(e) => { e.preventDefault(); choose(m); }}
              onMouseEnter={() => setActive(i)}
              className={`flex cursor-pointer items-baseline gap-2 rounded px-2 py-1 text-[13px] ${i === active ? "bg-hover" : ""}`}>
              {/* the id is what gets saved, so it keeps its room and the label gives way */}
              <span className="max-w-full shrink-0 truncate">{m.id}</span>
              {m.label && m.label !== m.id && <span className="ml-auto min-w-0 truncate text-[12px] text-ink-3">{m.label}</span>}
            </li>
          ))}
        </ul>
      )}
      {list?.error && <p className="m-0 text-[12px] text-ink-3">{list.error}</p>}
    </div>
  );
}
