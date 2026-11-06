// G22: the copilot panel, "Ask Kopifund". From 768px it sits beside the page
// and pushes it narrower (the shell lays it out), so the chart a question is
// about stays in view next to the answer; under 768px the same contents are a
// bottom sheet opened from the top bar. The conversation lives in the shell
// (useCopilot), so switching between the two keeps it.
import { useEffect, useRef, useState, type KeyboardEvent, type ReactNode, type RefObject } from "react";
import { ArrowUp, History, LoaderCircle, Lock, Plus, Sparkles, Square, Trash2, X } from "lucide-react";
import { postJSON, refreshViews, revalidate, useApi, type Loaded } from "../api";
import { Button, buttonClass } from "../components/Button";
import { EmptyState } from "../components/EmptyState";
import { ErrorBoundary } from "../components/ErrorBoundary";
import { Problem } from "../components/States";
import { Sheet } from "../components/Sheet";
import { showToast } from "../components/Toast";
import { formatStampDay } from "../dates";
import { onLinkClick } from "../router";
import type { AiSettings, CopilotPage, CopilotThreads } from "../types.gen";
import { AnswerChart, AnswerLink, AnswerSteps, AnswerText, ConfirmCard, contextLine, pageContext } from "./parts";
import { useUrlParams } from "../useUrlParams";
import { usePublishedFilters } from "./pageFilters";
import type { Copilot, Turn } from "./useCopilot";

export const PANEL_TITLE = "Ask Kopifund";
const SETTINGS_AI = "/settings#ai";

// ---------------------------------------------------------------- open or closed

const KEY = "kopifund.copilot";

/** Whether the side panel was left open, remembered per browser. */
export function readPanelOpen(): boolean {
  try {
    return window.localStorage.getItem(KEY) === "open";
  } catch {
    return false;
  }
}

export function savePanelOpen(open: boolean) {
  try {
    window.localStorage.setItem(KEY, open ? "open" : "closed");
  } catch {
    // storage blocked: the choice holds for this visit
  }
}

/** The button that opens the panel: in the page header from 768px, an icon in the top bar under it. */
export function AskButton({ open, onClick, compact = false, buttonRef }: {
  open: boolean; onClick: () => void; compact?: boolean; buttonRef?: RefObject<HTMLButtonElement | null>;
}) {
  if (compact) {
    return (
      <button ref={buttonRef} type="button" onClick={onClick} aria-label={PANEL_TITLE} aria-expanded={open}
        className="ml-auto grid size-9 shrink-0 place-items-center rounded-md text-ink-2 hover:bg-hover hover:text-ink">
        <Sparkles size={18} aria-hidden />
      </button>
    );
  }
  return (
    <Button ref={buttonRef} onClick={onClick} aria-expanded={open}>
      <Sparkles size={15} aria-hidden /> {PANEL_TITLE}
    </Button>
  );
}

// ---------------------------------------------------------------- the side panel

/** The page a question is asked on, read here rather than in the shell, so a filter typed on the page redraws only the panel. */
function usePage(path: string, title: string): CopilotPage {
  const [params] = useUrlParams();
  const published = usePublishedFilters();
  return pageContext(path, title, params, published);
}

type Where = { copilot: Copilot; path: string; title: string };

/** `focus`: opened by a press just now, so the box takes the focus (a panel left open from the last visit does not). */
export function CopilotSide({ copilot, path, title, onClose, focus = false }: Where & { onClose: () => void; focus?: boolean }) {
  const page = usePage(path, title);
  const box = useRef<HTMLTextAreaElement>(null);
  const ai = useApi<AiSettings>("/api/ai");
  return (
    <aside aria-label={PANEL_TITLE} data-testid="copilot-side"
      onKeyDown={(e) => {
        // a list or a menu inside the panel keeps its own Escape
        if (e.key === "Escape" && !e.defaultPrevented) {
          e.preventDefault();
          onClose();
        }
      }}
      className="flex h-full w-[360px] shrink-0 flex-col border-l border-line bg-panel">
      <div className="flex h-12 shrink-0 items-center gap-1 border-b border-line pr-2 pl-4">
        <h2 className="m-0 min-w-0 flex-1 truncate text-[15px] font-semibold tracking-tight">{PANEL_TITLE}</h2>
        <HistoryButton copilot={copilot} />
        <Button size="icon" variant="ghost" aria-label={`Close ${PANEL_TITLE}`} onClick={onClose}>
          <X size={16} aria-hidden />
        </Button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-4 py-3">
        <Body copilot={copilot} page={page} ai={ai} />
      </div>
      <div className="shrink-0 border-t border-line px-4 pt-3 pb-3">
        <Composer copilot={copilot} page={page} box={box} ai={ready(ai)} focus={focus} />
      </div>
    </aside>
  );
}

// ---------------------------------------------------------------- the phone's sheet

export function CopilotSheet({ copilot, path, title, open, onClose }: Where & { open: boolean; onClose: () => void }) {
  const page = usePage(path, title);
  const box = useRef<HTMLTextAreaElement>(null);
  const ai = useApi<AiSettings>(open ? "/api/ai" : null);
  return (
    <Sheet open={open} title={PANEL_TITLE} onClose={onClose} initialFocus={box}
      footer={<Composer copilot={copilot} page={page} box={box} ai={ready(ai)} focus />}>
      {/* G22: tall enough to read an answer in; a link in one opens its page, which the sheet would hide */}
      <div className="min-h-[55dvh]">
        <Body copilot={copilot} page={page} ai={ai} onFollow={onClose} extra={<HistoryButton copilot={copilot} />} />
      </div>
    </Sheet>
  );
}

// ---------------------------------------------------------------- shared

function HistoryButton({ copilot }: { copilot: Copilot }) {
  return (
    <Button size="icon" variant="ghost" aria-label="History" aria-pressed={copilot.history}
      onClick={() => copilot.setHistory(!copilot.history)}>
      <History size={16} aria-hidden />
    </Button>
  );
}

type Gate = "none" | "locked" | "ready";
/** Whether the copilot can be asked: a provider picked, and consent given unless the provider is on this machine. */
export const gate = (ai: AiSettings): Gate =>
  !ai.enabled || !ai.provider ? "none" : !ai.local && !ai.send_rows ? "locked" : "ready";

/** The settings when the copilot can be asked, else null (the box stays off). */
const ready = (ai: Loaded<AiSettings>) => (ai.data && gate(ai.data) === "ready" ? ai.data : null);

const labelOf = (ai: AiSettings) => ai.providers.find((p) => p.name === ai.provider)?.label ?? ai.provider ?? "the provider";

type BodyProps = { copilot: Copilot; page: CopilotPage; ai: Loaded<AiSettings>; onFollow?: () => void; extra?: ReactNode };

export const ANSWER_FAILED = "This answer failed to draw.";

export const PANEL_FAILED = "The panel failed to draw.";

/**
 * Audit: what the panel cannot draw stays in the panel instead of taking the page down. Each answer
 * has its own boundary (AnswerTurn), so one bad answer leaves the others and later replies drawn;
 * this one is the last resort for the rest, and keeps the History button (`extra`, the phone's only
 * way to another thread) beside its message. Another thread or the history list starts it clean.
 */
function Body(props: BodyProps) {
  const { copilot, extra } = props;
  return (
    <ErrorBoundary resetKey={`${copilot.thread}:${copilot.history}`}
      fallback={(reset) => (
        <div className="flex flex-col gap-2">
          {extra && <div className="flex justify-end">{extra}</div>}
          <Problem message={PANEL_FAILED} onRetry={reset} />
        </div>
      )}>
      <Turns {...props} />
    </ErrorBoundary>
  );
}

/** `extra` sits at the right of the first line (the sheet's History button, whose header has no room for it). */
function Turns({ copilot, page, ai, onFollow, extra }: BodyProps) {
  const end = useRef<HTMLDivElement>(null);
  const last = copilot.turns[copilot.turns.length - 1];
  // follow the answer as it grows
  useEffect(() => {
    end.current?.scrollIntoView?.({ block: "end" });
  }, [copilot.turns.length, last?.steps.length, last?.tools.length, last?.text, last?.status]);

  const context = (
    <div className="-mb-1 flex min-h-8 items-center gap-2">
      <p className="m-0 min-w-0 flex-1 truncate text-[12px] text-ink-3" title="Sent with each question">{contextLine(page)}</p>
      {extra}
    </div>
  );
  if (copilot.history) return <Threads copilot={copilot} extra={extra} />;
  if (!ai.data) {
    if (ai.error) return <Trouble message={ai.error} onRetry={revalidate} />;
    return <p className="m-0 flex items-center gap-2 text-[13px] text-ink-3" role="status"><LoaderCircle size={14} className="animate-spin" aria-hidden /> Loading</p>;
  }
  const g = gate(ai.data);
  if (g === "none") {
    return (
      <EmptyState title="No AI provider yet" action={<SettingsLink label="Open Settings" onFollow={onFollow} />}>
        Pick one on the Settings page.
      </EmptyState>
    );
  }
  if (g === "locked") {
    return (
      <div className="flex flex-col items-start gap-2 rounded-lg border border-line bg-tile px-4 py-4 text-[13px]">
        <p className="m-0 flex items-center gap-2 text-[14px] font-medium"><Lock size={15} aria-hidden /> {PANEL_TITLE} is off</p>
        <p className="m-0 text-ink-2">
          Turned on, it sends the dates, amounts, payees and categories of matching rows to {labelOf(ai.data)}; never account numbers or memo lines.
        </p>
        <SettingsLink label="Turn on in Settings" onFollow={onFollow} />
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-4">
      {context}
      {copilot.turns.map((t, i) => t.role === "user"
        ? <UserTurn key={t.id} turn={t} />
        : (
          <ErrorBoundary key={t.id} resetKey={`${t.status}:${t.charts.length}:${t.cards.length}:${t.links.length}`}
            fallback={(reset) => <Problem message={ANSWER_FAILED} onRetry={reset} />}>
            <AnswerTurn turn={t} copilot={copilot} page={page} latest={i === copilot.turns.length - 1} onFollow={onFollow} />
          </ErrorBoundary>
        ))}
      <div ref={end} />
    </div>
  );
}

function SettingsLink({ label, onFollow }: { label: string; onFollow?: () => void }) {
  const go = onLinkClick(SETTINGS_AI);
  return (
    <a href={SETTINGS_AI} className={buttonClass("secondary")} onClick={(e) => {
      go(e);
      if (e.defaultPrevented) onFollow?.();
    }}>{label}</a>
  );
}

/** Something went wrong, in the app's problem style, with Retry. */
function Trouble({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="rounded-lg bg-warn-bg px-3 py-2.5 text-[13px] text-warn" role="alert">
      <p className="m-0">{message}</p>
      <button type="button" onClick={onRetry}
        className="mt-2 rounded-md border border-warn/40 px-2.5 py-1 text-[13px] text-warn hover:bg-hover">
        Retry
      </button>
    </div>
  );
}

function UserTurn({ turn }: { turn: Turn }) {
  return (
    <div className="flex justify-end">
      <p className="m-0 max-w-[85%] rounded-lg bg-tile px-3 py-2 text-[13px] whitespace-pre-line">{turn.text}</p>
    </div>
  );
}

function AnswerTurn({ turn: t, copilot, page, latest, onFollow }: {
  turn: Turn; copilot: Copilot; page: CopilotPage; latest: boolean; onFollow?: () => void;
}) {
  const waiting = t.status === "running" && !t.text;
  return (
    <div className="flex flex-col gap-2.5">
      {/* G22: the steps in order, collapsed, then the answer and what it shows */}
      <AnswerSteps steps={t.steps} thinking={waiting} />
      {t.text && <AnswerText text={t.text} />}
      {t.charts.map((c, i) => <AnswerChart key={i} chart={c} />)}
      {t.links.length > 0 && (
        <div className="flex flex-col gap-1">{t.links.map((l, i) => <AnswerLink key={i} link={l} onFollow={onFollow} />)}</div>
      )}
      {t.cards.map((c) => <ConfirmCard key={c.id} card={c} state={copilot.cards[c.id] ?? "open"} onState={copilot.setCard} />)}
      {t.status === "stopped" && <p className="m-0 text-[12px] text-ink-3">Stopped</p>}
      {t.status === "error" && (
        latest ? <Trouble message={t.error ?? "The copilot could not answer."} onRetry={copilot.retry}
        /> : <p className="m-0 text-[12px] text-warn">{t.error}</p>
      )}
      {latest && t.status === "done" && t.followups.length > 0 && (
        <div className="flex flex-wrap gap-1.5" aria-label="Follow-up questions" role="group">
          {t.followups.map((f) => (
            <button key={f} type="button" disabled={copilot.running} onClick={() => void copilot.send(f, page)}
              className="rounded-full border border-line bg-panel px-2.5 py-1 text-left text-[12px] text-ink-2 hover:bg-hover hover:text-ink disabled:opacity-40">
              {f}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function Threads({ copilot, extra }: { copilot: Copilot; extra?: ReactNode }) {
  const list = useApi<CopilotThreads>("/api/copilot/threads");
  const [busy, setBusy] = useState<string | null>(null);
  // G22: a removal changes nothing a view reads (NO_CHANGE), so the list it answers is the one shown,
  // until the list is asked for again and answers anew
  const [after, setAfter] = useState<{ was: CopilotThreads | null; now: CopilotThreads } | null>(null);
  const shownList = after && after.was === list.data ? after.now : list.data;
  async function remove(id: string) {
    setBusy(id);
    try {
      const now = await postJSON<CopilotThreads>("/api/copilot/threads/remove", JSON.stringify({ id }));
      if (Array.isArray(now?.threads)) setAfter({ was: list.data, now });
      refreshViews(["/api/copilot/threads"]);
      // the thread open in the panel is gone: the next question starts a new one
      if (copilot.thread === id) copilot.reset();
      // the reset closes the list; reopen it so the removal shows in place
      copilot.setHistory(true);
    } catch (e) {
      showToast({ tone: "error", message: (e as Error).message });
    } finally {
      setBusy(null);
    }
  }
  const threads = shownList?.threads ?? [];
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center justify-between">
        <h3 className="m-0 text-[13px] font-semibold">History</h3>
        <div className="flex items-center gap-1">
          <Button size="sm" variant="ghost" onClick={copilot.reset}><Plus size={13} aria-hidden /> New thread</Button>
          {extra}
        </div>
      </div>
      {list.error && !shownList && <Trouble message={list.error} onRetry={revalidate} />}
      {shownList && threads.length === 0 && <p className="m-0 text-[13px] text-ink-3">No threads yet</p>}
      {threads.length > 0 && (
        <ul aria-label="Threads" className="m-0 list-none rounded-lg border border-line p-0 text-[13px]">
          {threads.map((t) => (
            <li key={t.id} className="flex items-center gap-2 border-t border-line py-1 pr-1 pl-3 first:border-t-0">
              <button type="button" onClick={() => void copilot.open(t.id)}
                className="flex min-w-0 flex-1 flex-col items-start rounded py-0.5 text-left hover:underline">
                <span className={`w-full truncate ${t.id === copilot.thread ? "font-medium" : ""}`}>{t.title}</span>
                <span className="num text-[11px] text-ink-3">{formatStampDay(t.updated_at)}</span>
              </button>
              <Button size="icon" variant="ghost" aria-label={`Remove ${t.title}`} disabled={busy === t.id}
                onClick={() => void remove(t.id)}>
                <Trash2 size={14} aria-hidden />
              </Button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Composer({ copilot, page, box, ai, focus = false }: {
  copilot: Copilot; page: CopilotPage; box: RefObject<HTMLTextAreaElement | null>; ai: AiSettings | null; focus?: boolean;
}) {
  const [text, setText] = useState("");
  const off = !ai;
  // the box is off while an answer runs, which drops the focus to the page: it comes back for the next question
  const wasRunning = useRef(copilot.running);
  useEffect(() => {
    const ended = wasRunning.current && !copilot.running;
    wasRunning.current = copilot.running;
    const lost = !document.activeElement || document.activeElement === document.body;
    if (ended && lost) box.current?.focus({ preventScroll: true });
  }, [copilot.running, box]);
  // the box takes the focus as the panel opens, once the settings say it can be asked (it is off until then)
  useEffect(() => {
    if (!off && focus) box.current?.focus({ preventScroll: true });
  }, [off, box, focus]);
  const send = () => {
    const q = text.trim();
    if (!q || copilot.running || off) return;
    setText("");
    void copilot.send(q, page);
  };
  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    // Enter sends, Shift+Enter starts a new line; a word still being composed (an IME) is left alone
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      send();
    }
  };
  return (
    <form className="flex flex-col gap-1.5" onSubmit={(e) => { e.preventDefault(); send(); }}>
      <div className="flex items-end gap-2">
        <textarea ref={box} rows={2} value={text} onChange={(e) => setText(e.target.value)} onKeyDown={onKey}
          aria-label="Ask about your money" placeholder="Ask about your money" disabled={copilot.running || off}
          className="min-h-16 flex-1 resize-none rounded-md border border-line bg-panel px-2.5 py-2 text-[13px] disabled:opacity-60" />
        {copilot.running
          ? <Button onClick={copilot.stop}><Square size={12} aria-hidden /> Stop</Button>
          : <Button type="submit" variant="primary" size="icon" aria-label="Send" disabled={off || !text.trim()}><ArrowUp size={16} aria-hidden /></Button>}
      </div>
      {ai && (
        <p className="m-0 text-[11px] text-ink-3">
          {ai.local ? "Runs on this machine" : <>Sends matching rows to {labelOf(ai)} · <SettingsInline /></>}
        </p>
      )}
    </form>
  );
}

function SettingsInline() {
  return <a href={SETTINGS_AI} onClick={onLinkClick(SETTINGS_AI)} className="text-ink-2 underline underline-offset-2">Settings</a>;
}
