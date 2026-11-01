// G22: the pieces of an answer: its steps, its words, a chart, a link into the
// app and a confirm card. Charts and links follow the rules every page follows: a chart
// is drawn by the chart kit from the stats' chart hint, and a link lists
// exactly the rows the answer counted.
import { Fragment, useState, type ReactNode } from "react";
import { ArrowRight, ChevronRight, Lightbulb, Wrench } from "lucide-react";
import { getJSON, postJSON, rowsChanged, type Relinked } from "../api";
import { Button } from "../components/Button";
import { COLLAPSED_ROWS, CollapsibleRows } from "../components/CollapsibleRows";
import { showToast, type ToastAction } from "../components/Toast";
import { onLinkClick } from "../router";
import { plan, type ChartHint, type Row, type StatResult } from "../stats/hint";
import { RowsTable } from "../stats/StatCard";
import { StatChart } from "../stats/StatChart";
import { VersusList } from "../stats/VersusList";
import type { Cents } from "../money";
import type { Budgets, CopilotCard, CopilotChart, CopilotLink, CopilotPage, CopilotRequest, CopilotStep, CopilotTool } from "../types.gen";
import type { CardState } from "./useCopilot";

// ---------------------------------------------------------------- the page

/**
 * The page a question is asked on: its address, its name and its filters: the
 * address's own, with what the page published over them (G22: selections it
 * keeps in state, such as Spending's range, pageFilters.ts).
 */
export function pageContext(path: string, title: string, search: URLSearchParams, published: Record<string, string> = {}): CopilotPage {
  return { path, title, filters: { ...Object.fromEntries(search.entries()), ...published } };
}

/** The quiet line under the panel's title: the page and the filters that go with each question. */
export function contextLine(page: CopilotPage): string {
  const filters = Object.entries(page.filters).map(([k, v]) => `${k.replace(/_/g, " ")} ${v}`);
  return [page.title, ...filters].join(" · ");
}

// ---------------------------------------------------------------- the words

// **bold** inside a line; nothing else is read as markup
function inline(text: string): ReactNode[] {
  return text.split(/(\*\*[^*]+\*\*)/g).filter(Boolean).map((part, i) =>
    part.startsWith("**") && part.endsWith("**") && part.length > 4
      ? <strong key={i} className="font-semibold">{part.slice(2, -2)}</strong>
      : <Fragment key={i}>{part}</Fragment>);
}

/**
 * The answer as paragraphs, with light formatting only: **bold**, and lines
 * starting "- " as a list. No markdown library: anything else stays as typed.
 * `quiet`: smaller, in the secondary colour (a thinking summary).
 */
export function AnswerText({ text, quiet = false }: { text: string; quiet?: boolean }) {
  const blocks = text.replace(/\r\n/g, "\n").split(/\n\s*\n/).map((b) => b.trim()).filter(Boolean);
  return (
    <div className={`flex flex-col gap-2 leading-relaxed ${quiet ? "text-[12px] text-ink-2" : "text-[13px]"}`}>
      {blocks.map((block, i) => {
        const lines = block.split("\n");
        // a block whose lines are all "- " items is a list; one that starts with words then lists keeps both
        const items = lines.filter((l) => /^\s*[-•]\s+/.test(l));
        if (!items.length) return <p key={i} className="m-0 whitespace-pre-line">{inline(block)}</p>;
        const lead = lines.filter((l) => !/^\s*[-•]\s+/.test(l)).join("\n");
        return (
          <Fragment key={i}>
            {lead && <p className="m-0 whitespace-pre-line">{inline(lead)}</p>}
            <ul className="m-0 flex list-disc flex-col gap-0.5 pl-5">
              {items.map((l, j) => <li key={j}>{inline(l.replace(/^\s*[-•]\s+/, ""))}</li>)}
            </ul>
          </Fragment>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------- the steps

// G22: a tool's name as the quiet summary beside "Used 2 tools" says it
const SHORT: Record<string, string> = {
  overview: "Overview", spending: "Spending", transactions: "Transactions", recurring: "Recurring", trips: "Trips",
  net_worth: "Net worth", stat_result: "Stat", query: "Query", show_chart: "Chart", suggest_change: "Change",
};

/** "Overview, Stat": each tool's short name once, in the order they ran. */
export function toolNames(tools: CopilotTool[]): string {
  const names = tools.map((t) => SHORT[t.name] ?? (t.name.charAt(0).toUpperCase() + t.name.slice(1)).replace(/_/g, " "));
  return [...new Set(names)].join(", ");
}

const usedLine = (n: number) => `Used ${n} ${n === 1 ? "tool" : "tools"}`;

function Chevron({ open }: { open: boolean }) {
  return <ChevronRight size={13} aria-hidden className={`shrink-0 text-ink-3 transition-transform ${open ? "rotate-90" : ""}`} />;
}

/** A round's thinking, summarized: "Thought", opening to the summary under a quiet rule. */
function ThoughtStep({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="flex flex-col gap-1">
      <button type="button" aria-expanded={open} onClick={() => setOpen(!open)}
        className="flex items-center gap-1.5 self-start rounded text-[12px] text-ink-3 hover:text-ink-2">
        <Lightbulb size={13} aria-hidden className="shrink-0" />
        <span>Thought</span>
        <Chevron open={open} />
      </button>
      {open && <div className="ml-1.5 border-l-2 border-line pl-3"><AnswerText text={text} quiet /></div>}
    </div>
  );
}

/** The tools one round ran, as one bordered row: "Used 2 tools", their short names, and each one's line when opened. */
function ToolsStep({ tools }: { tools: CopilotTool[] }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="rounded-md border border-line text-[12px]">
      <button type="button" aria-expanded={open} onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-1.5 rounded-md px-2.5 py-1.5 text-left hover:bg-hover">
        <Wrench size={13} aria-hidden className="shrink-0 text-ink-3" />
        <span className="shrink-0 font-medium text-ink-2">{usedLine(tools.length)}</span>
        <span className="min-w-0 flex-1 truncate text-ink-3">{toolNames(tools)}</span>
        <Chevron open={open} />
      </button>
      {open && (
        <ul aria-label="Tools used" className="m-0 flex list-none flex-col gap-1 border-t border-line px-2.5 py-1.5 text-ink-2">
          {tools.map((t, i) => <li key={i}>{t.label}</li>)}
        </ul>
      )}
    </div>
  );
}

/**
 * An answer's steps in order, each row collapsed until pressed: a round's
 * thinking ("Thought"), the tools it ran ("Used 2 tools"), and the text it
 * wrote before calling them, shown in place. `thinking`: the answer is still
 * on its way, so the last row reads "Thinking…".
 */
export function AnswerSteps({ steps, thinking }: { steps: CopilotStep[]; thinking: boolean }) {
  if (!steps.length && !thinking) return null;
  return (
    <ol aria-label="Steps" className="m-0 flex list-none flex-col gap-2 p-0">
      {steps.map((s, i) => (
        <li key={i}>
          {s.kind === "thinking" ? <ThoughtStep text={s.text} />
            : s.kind === "tools" ? <ToolsStep tools={s.tools} />
            : <AnswerText text={s.text} />}
        </li>
      ))}
      {thinking && (
        <li>
          <p role="status" className="m-0 flex items-center gap-1.5 text-[12px] text-ink-3">
            <Lightbulb size={13} aria-hidden className="shrink-0 animate-pulse" />
            <span>Thinking…</span>
          </p>
        </li>
      )}
    </ol>
  );
}

// ---------------------------------------------------------------- a chart

/**
 * A chart hint as a stat result, so the chart kit draws it as it draws a stat
 * card. The rows ride in the hint itself: under `rows`, or under `data` (a
 * list of rows, or an object the hint's `source` points into).
 */
export function chartResult(c: CopilotChart): StatResult {
  const { rows, data, ...hint } = c.chart as Record<string, unknown> & { rows?: unknown; data?: unknown };
  const body: Record<string, unknown> = Array.isArray(data) ? { rows: data }
    : data && typeof data === "object" ? { ...(data as Record<string, unknown>) } : {};
  if (Array.isArray(rows)) body.rows = rows as Row[];
  return { id: "copilot", title: c.title, chart: hint as ChartHint, data: body };
}

export function AnswerChart({ chart }: { chart: CopilotChart }) {
  const result = chartResult(chart);
  const p = plan(result);
  return (
    <figure className="m-0 flex flex-col gap-1.5 rounded-lg border border-line bg-panel px-3 py-2.5" aria-label={chart.title}>
      <figcaption className="text-[13px] font-medium">{chart.title}</figcaption>
      {p.kind === "chart" && <StatChart p={p} />}
      {p.kind === "versus" && <VersusList p={p} />}
      {p.kind === "table" && !!p.rows?.length && <RowsTable rows={p.rows} units={result.chart?.units} unit={result.chart?.unit} />}
      {p.note && <p className="m-0 text-[12px] text-ink-3">{p.note}</p>}
    </figure>
  );
}

// ---------------------------------------------------------------- a link

export function AnswerLink({ link, onFollow }: { link: CopilotLink; onFollow?: () => void }) {
  const go = onLinkClick(link.href);
  return (
    <a href={link.href} className="flex items-center gap-1.5 text-[13px] text-ink underline-offset-2 hover:underline"
      onClick={(e) => {
        go(e);
        if (e.defaultPrevented) onFollow?.();
      }}>
      <ArrowRight size={14} aria-hidden className="shrink-0 text-ink-3" />
      {link.label}
    </a>
  );
}

// ---------------------------------------------------------------- a confirm card

// what an endpoint answers that carries its own way back (Rules, a row's category, Trips)
type Saved = { change?: string | null; undo?: unknown; restore?: unknown; relinked?: Relinked };

/**
 * The Undo for a card's write, from the endpoint's answer, as the Rules,
 * Transactions and Trips pages read it: a rule write's `change` token, a trip
 * save's `undo`, a trip removal's `restore`. A card that still carries its own
 * `undo` (an older server) keeps it. A budget's way back is not here: it is
 * the amount read just before Confirm (budgetUndo).
 */
export function undoFor(card: CopilotCard, saved: Saved): CopilotRequest | null {
  if (card.undo) return card.undo;
  if (saved.change) return { path: "/api/rules/undo", body: { change: saved.change } };
  if (card.request.path === "/api/trips" && saved.undo) return { path: "/api/trips/undo", body: { undo: saved.undo } };
  if (saved.restore && typeof saved.restore === "object") return { path: "/api/trips/restore", body: saved.restore as Record<string, unknown> };
  return null;
}

async function runUndo(req: CopilotRequest): Promise<boolean> {
  try {
    const r = await postJSON<Saved>(req.path, JSON.stringify(req.body));
    showToast({ message: r.relinked ? `Undone, ${rowsChanged(r.relinked, true)}.` : "Undone." });
    return true;
  } catch (e) {
    // the server refused (a newer write stands): the toast stays for another try
    showToast({ tone: "error", message: (e as Error).message });
    return false;
  }
}

// ---------------------------------------------------------------- a budget card's way back

const BUDGETS = "/api/budgets";

/** A budget card: its write is POST /api/budgets, whose answer carries no way back. */
export const isBudget = (card: CopilotCard) => card.kind === "budget" && card.request.path === BUDGETS;

/**
 * The planned amount (cents, or null for none) a budget write's body names,
 * read from the record now, as the Plan page reads it: GET /api/budgets for
 * the month, the row by its category's id (by name when the body has no id).
 */
export async function plannedNow(body: Record<string, unknown>): Promise<Cents | null> {
  const month = String(body.month ?? "");
  const b = await getJSON<Budgets>(`${BUDGETS}?month=${encodeURIComponent(month)}`);
  const row = b.rows.find((r) => (body.category_id != null ? r.category_id === body.category_id : r.category === body.category));
  if (!row) throw new Error(`${String(body.category ?? "That category")} has no budget row for ${month}.`);
  return row.planned;
}

/**
 * G22: a budget's Undo puts back the amount read just before Confirm. It
 * reads the budget again first and leaves it alone when it is no longer the
 * amount the card set (the Plan page or another card changed it since).
 */
export function budgetUndo(card: CopilotCard, before: Cents | null): () => Promise<boolean> {
  const body = card.request.body;
  return async () => {
    try {
      const now = await plannedNow(body);
      if (now !== (body.planned ?? null)) {
        showToast({ tone: "error", message: `${card.title} changed since, so it was left as it is.` });
        return true;
      }
    } catch (e) {
      showToast({ tone: "error", message: (e as Error).message });
      return false;
    }
    return runUndo({ path: BUDGETS, body: { ...body, planned: before } });
  };
}

/**
 * A confirm card. Its state lives in the conversation (useCopilot), not here,
 * so a card already saved shows Saved after the panel closes and opens again,
 * and in a saved thread opened again during the same visit.
 */
export function ConfirmCard({ card, state, onState }: {
  card: CopilotCard; state: CardState; onState: (id: string, state: CardState) => void;
}) {
  const [problem, setProblem] = useState<string | null>(null);

  async function confirm() {
    onState(card.id, "saving");
    setProblem(null);
    try {
      // G22: a budget's amount before the write, read now, is its Undo
      const before = isBudget(card) ? await plannedNow(card.request.body) : undefined;
      // the endpoint's own write; postJSON tells every open view the record changed
      const saved = await postJSON<Saved>(card.request.path, JSON.stringify(card.request.body));
      onState(card.id, "saved");
      let undo: ToastAction["onClick"] | null = null;
      if (before !== undefined) undo = budgetUndo(card, before);
      else {
        const back = undoFor(card, saved);
        if (back) undo = () => runUndo(back);
      }
      const action: ToastAction | undefined = undo ? { label: "Undo", onClick: undo } : undefined;
      showToast({ message: saved.relinked ? `${card.title}: saved, ${rowsChanged(saved.relinked)}.` : `${card.title}: saved.`, action });
    } catch (e) {
      onState(card.id, "open");
      setProblem((e as Error).message);
    }
  }

  return (
    <section aria-label={card.title} className="flex flex-col gap-1.5 rounded-lg border border-line bg-panel px-3 py-2.5 text-[13px]">
      <p className="m-0 font-medium">{card.title}</p>
      {card.summary && <p className="m-0 text-ink-2">{card.summary}</p>}
      {card.preview.length > 0 && (
        <ul aria-label="Preview" className="m-0 list-none rounded-md border border-line p-0 text-[12px] text-ink-2">
          <CollapsibleRows rows={card.preview} limit={COLLAPSED_ROWS.thin} render={(line) => (
            <li className="border-t border-line px-2 py-1 first:border-t-0">{line}</li>
          )} />
        </ul>
      )}
      {state === "saved" ? <p className="m-0 text-[12px] text-good" role="status">Saved</p>
        : state === "skipped" ? <p className="m-0 text-[12px] text-ink-3">Not saved</p>
        : (
          <div className="flex flex-wrap gap-2 pt-0.5">
            <Button variant="primary" disabled={state === "saving"} onClick={() => void confirm()}>
              {state === "saving" ? "Saving" : "Confirm"}
            </Button>
            <Button disabled={state === "saving"} onClick={() => onState(card.id, "skipped")}>Not now</Button>
          </div>
        )}
      {problem && <p role="alert" className="m-0 text-[12px] text-danger">{problem}</p>}
    </section>
  );
}
