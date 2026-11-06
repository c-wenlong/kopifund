import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown, ChevronRight, Pencil, Plus, Trash2, TriangleAlert, Undo2 } from "lucide-react";
import {
  postJSON, rowsChanged, useApi, type NeedsGroup, type Relinked, type Rule, type RuleGroup, type RuleSavedEdit, type RuleUndone,
  type RulesState,
} from "../api";
import { formatAmount, formatSgd } from "../money";
import { formatDateTime, formatDay } from "../dates";
import { Button } from "../components/Button";
import { Card } from "../components/Card";
import { EmptyState } from "../components/EmptyState";
import { StatusChip } from "../components/StatusChip";
import { Pager, usePager } from "../components/Pager";
import { GroupRowsPanel } from "../components/PinRow";
import { NEW_DRAFT, RuleEditor, draftOf, type Draft, type SaveStatus } from "../components/RuleEditor";
import { Segmented } from "../components/Segmented";
import { Select } from "../components/Select";
import { HEAD_ROW, ROW, TABLE_BOX, TH } from "../components/tableKit";
import { Loading, Problem } from "../components/States";
import { showToast, type ToastAction } from "../components/Toast";
import { useWide } from "../components/useWide";
import { useAction } from "../components/useAction";
import { useUnsavedGuard } from "../router";
import { tierLabel } from "../ruleWords";
import { KindLabel, PAYEE_KIND_ICON, STEP_STATUS_ICON } from "../icons";
import { useUrlParams } from "../useUrlParams";

type Show = "all" | "pins" | "problems";
// `n` tells two drafts apart, so choosing another one remounts the form with it; `start` is what it opened on
type Editing = { rule: Rule | null; draft: Draft; start: Draft; n: number; status?: SaveStatus };

const sameDraft = (a: Draft, b: Draft) => (Object.keys(a) as (keyof Draft)[]).every((k) => a[k] === b[k]);

const count = (n: number, one: string, many = `${one}s`) => `${n.toLocaleString("en-SG")} ${n === 1 ? one : many}`;
const label = (g: RuleGroup) => g.payee || g.category;

// a single-row rule is named by a row's key, which means nothing on screen: say which row it is
const nameOf = (r: Rule) => (r.tier !== "key" ? r.pattern : r.pins[0] ? `the ${formatDay(r.pins[0].date)} row` : "a single-row rule");

// G13: "10 text matches, 11 single rows", the two kinds of rule a payee has
const kindsOf = (g: RuleGroup) =>
  [g.patterns && count(g.patterns, "text match", "text matches"), g.pins && count(g.pins, "single row")].filter(Boolean).join(", ");

// G7: every rule, grouped by payee, editable. Rules live in CSV files: the
// shared ones in the repo (read only here) and yours in the data folder,
// which the editor writes; every write can be undone.
export function Rules() {
  const state = useApi<RulesState>("/api/rules");
  const wide = useWide();
  // G13: what defines the list and the page of it live in the address (?q=, ?view=order, ?show=, ?category=,
  // ?page=), read from it on every render, so a reload, Back, or a link to the bare page while it is open
  // shows the list the address says, and every reader of the address (the copilot's page filters) hears a change
  const [params, patch] = useUrlParams();
  const view = params.get("view") === "order" ? "order" : "payee";
  const q = params.get("q") ?? "";
  const showParam = params.get("show");
  const show: Show = showParam === "pins" || showParam === "problems" ? showParam : "all";
  const category = params.get("category") ?? "";
  // a filter changed here starts the list at page 1, and the page leaves the address in the same write;
  // the option already chosen, clicked again, changes nothing and keeps the page
  const setView = (v: "payee" | "order") => { if (v !== view) patch({ view: v === "order" ? v : null, page: null }); };
  const setQ = (text: string) => { if (text !== q) patch({ q: text, page: null }); };
  const setShow = (v: Show) => { if (v !== show) patch({ show: v === "all" ? null : v, page: null }); };
  const setCategory = (c: string) => { if (c !== category) patch({ category: c, page: null }); };
  const [open, setOpen] = useState<Set<string>>(new Set());
  const [editing, setEditing] = useState<Editing | null>(null);
  const edits = useRef(0);   // a number for each editor opened, never reused, so a save from a closed one cannot reach a new one
  const { running: busy, run } = useAction();
  const [history, setHistory] = useState(false);
  const [needsOpen, setNeedsOpen] = useState<string | null>(null);   // the needs-rule group whose rows are listed
  // G18: leaving with a rule typed into and not saved asks first, as every other form does
  // (and a save on its way holds the page until it lands: one that fails leaves the rule unsaved)
  useUnsavedGuard(!!editing && (!!editing.status?.saving || !sameDraft(editing.draft, editing.start)));

  const s = state.data;
  const byId = useMemo(() => new Map((s?.rules ?? []).map((r) => [r.id, r])), [s]);
  const needle = q.trim().toLowerCase();
  const ruleHits = (r: Rule) =>
    (!needle || [r.payee, r.pattern, r.category].some((t) => t.toLowerCase().includes(needle))) &&
    (!category || r.category === category);
  // a group shows when any of its rules matches the search and the category
  const groups = (s?.groups ?? []).filter((g) =>
    (show !== "pins" || g.pins > 0) && (show !== "problems" || g.problems > 0) &&
    g.rules.some((id) => { const r = byId.get(id); return !!r && ruleHits(r); }));
  const flat = (s?.rules ?? []).filter((r) =>
    ruleHits(r) && (show !== "pins" || r.tier === "key") && (show !== "problems" || r.problems.length > 0));

  const shown = view === "payee" ? groups.length : flat.length;
  // G13: pages of groups (by payee) or of rules (in order); a new search, filter or view starts at page 1.
  // The total is unknown until the rules load, so the page read from the address is not clamped to 1 meanwhile.
  const urlPage = Math.max(1, Math.floor(Number(params.get("page"))) || 1);
  const filters = [view, q, show, category].join("\n");
  const pager = usePager("rules", s ? shown : undefined, filters, urlPage);
  // the page and the address follow each other: a click on a page number writes it, an address that names
  // another page or other filters (Back, a link) moves the list to its page. A filter changed here drops the
  // page from the address as it writes (above), so new filters with the page unchanged come from a link.
  const seen = useRef({ page: urlPage, filters });
  useEffect(() => {
    if (urlPage !== seen.current.page || filters !== seen.current.filters) {
      seen.current = { page: urlPage, filters };
      if (urlPage !== pager.page) pager.setPage(urlPage);
    } else if (s && pager.page !== urlPage) {
      seen.current = { page: pager.page, filters };
      patch({ page: pager.page > 1 ? String(pager.page) : null });
    }
  }, [urlPage, filters, pager, patch, s]);
  // G18 (U4): the payees awaiting a rule page like every other long list
  const needsPager = usePager("rules-needs", s?.needs.length ?? 0);
  // a link to a category that has since been renamed or deleted would filter out every rule behind a dropdown that says "All"
  useEffect(() => {
    if (s && category && !s.rules.some((r) => r.category === category)) patch({ category: null });
  }, [s, category, patch]);

  // an edit open on this page keeps the page where it is, so its unsaved changes are not dropped
  const here = view === "payee" ? pager.slice(groups).filter((g) => open.has(g.id)).flatMap((g) => g.rules) : pager.slice(flat).map((r) => r.id);
  const editingHere = !!editing?.rule && here.includes(editing.rule.id);

  if (state.loading && !s) return <Loading what="Loading your rules" />;
  if (state.error || !s) return <Problem message={state.error ?? "No rules"} hint="Load your statements first, from the Upload page." retry />;

  // what a write says goes to a toast, where the click was; a delete offers Undo there
  const act = (write: () => Promise<string | { message: string; action?: ToastAction }>) => run("act", async () => {
    const done = await write();
    showToast(typeof done === "string" ? { message: done } : done);
  }, { toast: true });
  // G13: a save reaches the transactions at once, so Apply rules is only for rows the record holds
  // differently for another reason (a rules file changed by hand); it relinks them and reads no statement
  const apply = () => act(async () => {
    const r = await postJSON<{ relinked: Relinked }>("/api/rules/apply");
    return `Rules applied, ${rowsChanged(r.relinked)}.`;
  });
  // `change` limits an undo to one write, so a newer write (another tab's) is never taken back by it
  const undo = (change?: string) => act(async () => {
    const r = await postJSON<RuleUndone>("/api/rules/undo", change ? JSON.stringify({ change }) : undefined);
    return `Undone: ${r.undone}, ${rowsChanged(r.relinked, true)}.`;
  });
  const undoAction = (change?: string | null): ToastAction | undefined =>
    change ? { label: "Undo", onClick: () => undo(change) } : undefined;
  const remove = (r: Rule) => act(async () => {
    const done = await postJSON<{ change?: string; relinked?: Relinked }>("/api/rules/remove",
      JSON.stringify({ file: r.file, line: r.line, pattern: r.pattern, revision: r.revision }));
    return { message: `Deleted ${nameOf(r)}, ${rowsChanged(done.relinked)}.`, action: undoAction(done.change) };
  });
  const edit = (rule: Rule | null, draft: Draft) => setEditing({ rule, draft, start: draft, n: ++edits.current });
  const saved = (text: string, r: RuleSavedEdit) => {
    setEditing(null);
    showToast({ message: r.unchanged ? text : `${text}.`, action: r.unchanged ? undefined : undoAction(r.change) });
  };
  const name = (g: NeedsGroup) => edit(null, { ...NEW_DRAFT, ...g.suggestion, kind: g.suggestion.kind || "Merchant" });

  const editor = (rule: Rule | null) => editing && editing.rule?.id === rule?.id && (
    <RuleEditor key={`${rule?.id ?? "new"}:${editing.n}`} rule={rule} initial={editing.draft} tiers={s.tiers} tierFiles={s.tier_files}
      categories={s.categories} kinds={s.kinds} onSaved={saved} onCancel={() => setEditing(null)}
      onDraft={(d) => setEditing((e) => (e && e.n === editing.n ? { ...e, draft: d } : e))}
      status={editing.status} onStatus={(st) => setEditing((e) => (e && e.n === editing.n ? { ...e, status: st } : e))} />
  );
  const allCategories = [...new Set(s.rules.map((r) => r.category))].sort();
  const toggle = (id: string) => setOpen((o) => {
    const next = new Set(o);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });
  const last = s.history[0];
  // G13: how many problems the lint found, and the button that lists the rules they sit on
  const problems = s.rules.reduce((n, r) => n + r.problems.length, 0);
  const showProblems = () => {
    const on = show !== "problems";
    setShow(on ? "problems" : "all");
    if (on) document.getElementById("rules-list")?.scrollIntoView?.({ block: "start" });
  };
  // one count with its unit: the payees and rules there are, whatever the view or filter shows
  const total = `${count(s.groups.length, "payee")}, ${count(s.rules.length, "rule")}`;
  const viewTotal = view === "payee" ? s.groups.length : s.rules.length;

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2 rounded-xl border border-line bg-panel px-4 py-2.5 text-[13px]">
        <span className="flex basis-full flex-wrap items-center gap-2 sm:basis-auto sm:flex-1">
          {s.pending > 0 && (
            <StatusChip tone="info" title={`${count(s.pending, "transaction")} change when you apply rules`}>{s.pending.toLocaleString("en-SG")} to apply</StatusChip>
          )}
          {problems > 0
            ? <StatusChip tone="danger" onClick={showProblems} pressed={show === "problems"} icon={STEP_STATUS_ICON.problems}
                title={show === "problems" ? "Show every rule again" : "Show only the rules with problems"}>{count(problems, "problem")}</StatusChip>
            : <StatusChip tone="good">No problems</StatusChip>}
        </span>
        {last && (
          <Button onClick={() => void undo()} disabled={busy} title={last.summary}>
            <Undo2 size={14} aria-hidden /> Undo
          </Button>
        )}
        {s.history.length > 0 && (
          <Button variant="ghost" aria-expanded={history} onClick={() => setHistory(!history)}>History</Button>
        )}
        {(s.pending > 0 || s.stale) && (
          <Button variant="primary" onClick={() => void apply()} disabled={busy}>
            {busy ? "Working…" : "Apply rules"}
          </Button>
        )}
        {history && (
          <ol className="m-0 w-full list-none p-0 text-[12px]" aria-label="Recent rule changes">
            {s.history.map((h, i) => (
              <li key={h.id} className="flex gap-3 border-t border-line py-1">
                <span className="num whitespace-nowrap text-ink-3">{formatDateTime(h.at)}</span>
                <span className={i === 0 ? "" : "text-ink-2"}>{h.summary}</span>
                {i === 0 && <span className="ml-auto text-ink-3">undo is next</span>}
              </li>
            ))}
          </ol>
        )}
      </div>

      {s.needs.length > 0 && (
        // G18: the total, not the biggest groups the server sends, so it matches Needs your eye's count
        <Card title={`Needs a rule (${s.needs_total})`}>
          {s.needs_total > s.needs.length && (
            <p className="num m-0 mb-2 text-[12px] text-ink-3">The biggest {s.needs.length} are listed.</p>
          )}
          <ul className="m-0 flex list-none flex-col p-0 text-[13px]">
            {needsPager.slice(s.needs).map((g) => (
              <li key={g.text} className="grid grid-cols-[1fr_auto_auto] items-center gap-x-3 gap-y-1 border-t border-line py-1.5 first:border-t-0 sm:grid-cols-[1fr_auto_auto_auto]">
                <span className="col-span-3 truncate sm:col-span-1" title={g.text}>{g.text} <span className="text-ink-3">· {g.field}</span></span>
                <span className="num text-ink-2">{g.rows} × · {formatSgd(g.amount)}</span>
                <Button size="sm" aria-expanded={needsOpen === g.text} aria-label={`Set one row of ${g.text}`}
                  onClick={() => setNeedsOpen(needsOpen === g.text ? null : g.text)}
                  className="justify-self-end sm:justify-self-auto">Set one row</Button>
                {/* a row that prints only a bank code has no text to match, so it can only be set on its own */}
                {g.suggestion.match_on !== "key" && (
                  <Button size="sm" aria-label={`Make a rule for ${g.text}`} onClick={() => name(g)}>Make a rule</Button>
                )}
                {needsOpen === g.text && (
                  <div className="col-span-3 pb-1 sm:col-span-4">
                    <GroupRowsPanel expected={g.rows} query={new URLSearchParams(
                      g.field === "key" ? { key: g.keys[0] } : { text: g.text }).toString()} />
                  </div>
                )}
              </li>
            ))}
          </ul>
          {/* a row being set on its own holds its draft in the open panel, so the page stays while one is open here */}
          <Pager pager={needsPager} label="Needs a rule pages"
            locked={needsOpen != null && needsPager.slice(s.needs).some((g) => g.text === needsOpen)} />
        </Card>
      )}

      {editing && !editing.rule && (
        <Card title="New rule">{editor(null)}</Card>
      )}

      <Card title="Rules" id="rules-list" actions={
        <Button onClick={() => edit(null, NEW_DRAFT)}>
          <Plus size={14} aria-hidden /> Add rule
        </Button>}>
        {s.rules_error ? (
          <Problem message={`Your rules do not load: ${s.rules_error}`} hint="Undo the last change, or fix the line named above." />
        ) : (
          <>
            <p className="num m-0 mb-2 text-[13px] text-ink-2">
              {total}{shown !== viewTotal && ` · ${count(shown, view === "payee" ? "payee" : "rule")} shown`}
            </p>
            <div className="mb-2 flex flex-wrap items-center gap-2 text-[13px]">
              <input type="search" value={q} onChange={(e) => setQ(e.target.value)}
                placeholder="Search payee, text, category" aria-label="Search rules"
                className="h-8 min-w-48 flex-1 rounded-md border border-line bg-panel px-2.5" />
              <Segmented label="Show" value={show} onChange={setShow}
                options={[{ value: "all", text: "All" }, { value: "pins", text: "Single rows" }]} />
              <Select label="Category" placeholder="All categories" value={category} onChange={setCategory} className="min-w-44"
                options={[{ value: "", label: "All categories" }, ...allCategories.map((c) => ({ value: c, label: c }))]} />
              <Segmented label="View" value={view} onChange={setView}
                options={[{ value: "payee", text: "By payee" }, { value: "order", text: "In order" }]} />
            </div>
            {shown === 0 ? (
              <EmptyState title="No rule matches">Clear the search or the filters to see every rule.</EmptyState>
            ) : view === "payee" ? (
              wide
                ? <GroupTable groups={pager.slice(groups)} byId={byId} open={open} toggle={toggle}
                    editing={editing} edit={edit} remove={remove} editor={editor} busy={busy} />
                : <GroupList groups={pager.slice(groups)} byId={byId} open={open} toggle={toggle}
                    editing={editing} edit={edit} remove={remove} editor={editor} busy={busy} />
            ) : (
              wide
                ? <FlatTable rules={pager.slice(flat)} editing={editing} edit={edit} remove={remove} editor={editor} busy={busy} />
                : <FlatList rules={pager.slice(flat)} editing={editing} edit={edit} remove={remove} editor={editor} busy={busy} />
            )}
            <Pager pager={pager} label="Rules pages" locked={editingHere} />
          </>
        )}
      </Card>
    </div>
  );
}

type RowActions = {
  editing: Editing | null;
  edit: (rule: Rule, draft: Draft) => void;
  remove: (rule: Rule) => void;
  editor: (rule: Rule) => React.ReactNode;
  busy: boolean;
};
type GroupProps = RowActions & { groups: RuleGroup[]; byId: Map<string, Rule>; open: Set<string>; toggle: (id: string) => void };

function Problems({ g }: { g: RuleGroup }) {
  return g.problems > 0 ? (
    <span className="inline-flex items-center gap-1 text-warn">
      <TriangleAlert size={12} aria-hidden /> {count(g.problems, "problem")}
    </span>
  ) : null;
}

// the payee's rules, listed under its row once opened
function GroupRules({ g, byId, ...actions }: RowActions & { g: RuleGroup; byId: Map<string, Rule> }) {
  return (
    <ul className="m-0 flex list-none flex-col p-0" aria-label={`Rules for ${label(g)}`}>
      {g.rules.map((id) => byId.get(id)).filter((r): r is Rule => !!r).map((r) => (
        <li key={r.id} className="border-t border-line py-1.5 first:border-t-0">
          <RuleLine rule={r} group={g} {...actions} />
          {actions.editing?.rule?.id === r.id && <div className="mt-2">{actions.editor(r)}</div>}
        </li>
      ))}
    </ul>
  );
}

function PayeeButton({ g, isOpen, toggle }: { g: RuleGroup; isOpen: boolean; toggle: () => void }) {
  return (
    <button type="button" aria-expanded={isOpen} onClick={toggle}
      aria-label={`${isOpen ? "Hide" : "Show"} the rules for ${label(g)}`}
      className="inline-flex min-w-0 items-start gap-1 text-left">
      {isOpen ? <ChevronDown size={14} aria-hidden className="mt-0.5 shrink-0 text-ink-3" />
        : <ChevronRight size={14} aria-hidden className="mt-0.5 shrink-0 text-ink-3" />}
      <span className="min-w-0">
        <span className={g.payee ? "font-medium" : ""}>{label(g)}</span>
        <span className="block text-[12px] text-ink-3">
          {/* G18: the payee's kind leads with its icon */}
          {g.payee ? <>{g.kind && <KindLabel icon={PAYEE_KIND_ICON[g.kind]}>{g.kind}</KindLabel>}{g.kind && g.country ? " · " : ""}{g.country}</> : "no payee"}
        </span>
      </span>
    </button>
  );
}

function GroupTable({ groups, byId, open, toggle, ...actions }: GroupProps) {
  return (
    <div className={`relative ${TABLE_BOX}`}>
      <table className="w-full border-collapse text-[13px]">
        <thead>
          <tr className={HEAD_ROW}>
            <th className={`${TH} px-3`}>Payee</th>
            <th className={`${TH} px-3`}>Category</th>
            <th className={`${TH} px-3`}>Rules</th>
            <th className={`${TH} px-3 text-right`}>Rows</th>
          </tr>
        </thead>
        <tbody>
          {groups.map((g) => {
            const isOpen = open.has(g.id);
            return (
              <Fragment key={g.id}>
                <tr className={`${ROW} align-top`}>
                  <td className="px-3 py-1.5"><PayeeButton g={g} isOpen={isOpen} toggle={() => toggle(g.id)} /></td>
                  <td className="px-3 py-1.5">
                    {g.category}
                    {g.categories.length > 1 && <span className="text-ink-3"> +{g.categories.length - 1}</span>}
                  </td>
                  <td className="px-3 py-1.5 text-ink-2">
                    {kindsOf(g)}
                    {g.shared > 0 && <span className="text-ink-3"> · {g.shared === g.rules.length ? "shared" : `${g.shared} shared`}</span>}
                    {g.problems > 0 && <span className="ml-2"><Problems g={g} /></span>}
                  </td>
                  <td className="num px-3 py-1.5 text-right">{g.rows.toLocaleString("en-SG")}</td>
                </tr>
                {isOpen && (
                  <tr>
                    <td colSpan={4} className="bg-tile/50 px-3 pb-2 pl-8"><GroupRules g={g} byId={byId} {...actions} /></td>
                  </tr>
                )}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// G13: phone rows. Under 768px the table's Rows column ran off the card, so each payee is two lines:
// its name and row count, then what the rest of the columns said.
function GroupList({ groups, byId, open, toggle, ...actions }: GroupProps) {
  return (
    <ul className="m-0 list-none rounded-lg border border-line p-0 text-[13px]">
      {groups.map((g) => {
        const isOpen = open.has(g.id);
        return (
          <li key={g.id} className="border-t border-line px-3 py-2 first:border-t-0">
            <div className="flex items-start justify-between gap-3">
              <PayeeButton g={g} isOpen={isOpen} toggle={() => toggle(g.id)} />
              <span className="num shrink-0 text-right">{count(g.rows, "row")}</span>
            </div>
            <p className="m-0 pl-5 text-[12px] text-ink-2">
              {[(g.payee || g.categories.length > 1) && g.category + (g.categories.length > 1 ? ` +${g.categories.length - 1}` : ""), kindsOf(g),
                g.shared > 0 && (g.shared === g.rules.length ? "shared" : `${g.shared} shared`)].filter(Boolean).join(" · ")}
              {g.problems > 0 && <span className="ml-2"><Problems g={g} /></span>}
            </p>
            {isOpen && <div className="mt-1 pl-5"><GroupRules g={g} byId={byId} {...actions} /></div>}
          </li>
        );
      })}
    </ul>
  );
}

// the text a rule matches: a single row shows its date, amount and bank text, never the key
function Matches({ rule: r }: { rule: Rule }) {
  const pin = r.tier === "key" ? r.pins[0] : undefined;
  if (r.tier !== "key") return <span className="break-all">{r.pattern}</span>;
  return pin ? (
    <span className="num">{formatDay(pin.date)} · {formatAmount(pin.amount, pin.currency)} <span className="text-ink-3">{pin.text}</span></span>
  ) : <span className="text-ink-3">A row not in your record</span>;
}

// One rule inside its payee's group: a single-row rule shows the row it sets.
function RuleLine({ rule: r, group, edit, remove, busy }: RowActions & { rule: Rule; group: RuleGroup }) {
  return (
    <div className="flex flex-col gap-0.5">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className="rounded bg-tile px-1.5 py-0.5 text-[11px] text-ink-2">{tierLabel(r.tier)}</span>
        <Matches rule={r} />
        {(r.category !== group.category || r.payee !== group.payee) && (
          <span className="text-ink-2">{[r.payee !== group.payee && r.payee, r.category].filter(Boolean).join(", ")}</span>
        )}
        <span className="num text-ink-3">
          {r.tier === "key" ? "" : r.matched > r.wins ? `matches first on ${r.wins} of ${count(r.matched, "row")} · ` : `${count(r.wins, "row")} · `}
          {r.shared ? `shared, ${r.file}` : r.file}
        </span>
        <Actions rule={r} edit={edit} remove={remove} busy={busy} />
      </div>
      {r.problems.map((p) => (
        <p key={p.check} className="m-0 flex items-center gap-1 text-[12px] text-warn" title={p.detail}>
          <TriangleAlert size={12} aria-hidden /> {p.why}
        </p>
      ))}
    </div>
  );
}

function Actions({ rule: r, edit, remove, busy }: Pick<RowActions, "edit" | "remove" | "busy"> & { rule: Rule }) {
  return (
    <span className="ml-auto inline-flex gap-1">
      <button type="button" aria-label={`Edit ${r.tier} ${r.pattern}`} title={r.shared ? "Edit: saved as your own rule" : "Edit"}
        onClick={() => edit(r, draftOf(r))} className="rounded-md p-1 text-ink-3 hover:bg-hover hover:text-ink">
        <Pencil size={14} aria-hidden />
      </button>
      {!r.shared && (
        <button type="button" aria-label={`Delete ${r.tier} ${r.pattern}`} title="Delete" disabled={busy}
          onClick={() => remove(r)} className="rounded-md p-1 text-ink-3 hover:bg-hover hover:text-ink disabled:opacity-40">
          <Trash2 size={14} aria-hidden />
        </button>
      )}
    </span>
  );
}

function RuleProblems({ rule: r }: { rule: Rule }) {
  return r.problems.map((p) => (
    <span key={p.check} className="flex items-center gap-1 text-[12px] text-warn" title={p.detail}>
      <TriangleAlert size={12} aria-hidden /> {p.why}
    </span>
  ));
}

function FlatTable({ rules, editing, edit, remove, editor, busy }: RowActions & { rules: Rule[] }) {
  return (
    <div className={`relative ${TABLE_BOX}`}>
      <table className="w-full border-collapse text-[13px]">
        <thead>
          <tr className={HEAD_ROW}>
            <th className={`${TH} px-3 text-right`}>#</th>
            <th className={`${TH} px-3`}>Matches on</th>
            <th className={`${TH} px-3`}>Text</th>
            <th className={`${TH} px-3`}>Payee</th>
            <th className={`${TH} px-3`}>Category</th>
            <th className={`${TH} px-3 text-right`}>Rows</th>
            <th className={`${TH} px-3`}>File</th>
            <th className={`${TH} px-3`}><span className="sr-only">Actions</span></th>
          </tr>
        </thead>
        <tbody>
          {rules.map((r) => (
            <Fragment key={r.id}>
              <tr className={`${ROW} align-top`}>
                <td className="num px-3 py-1.5 text-right text-ink-3">{r.order}</td>
                <td className="px-3 py-1.5 text-ink-2">{tierLabel(r.tier)}</td>
                <td className="px-3 py-1.5">
                  <Matches rule={r} />
                  <RuleProblems rule={r} />
                </td>
                <td className="px-3 py-1.5">{r.payee || <span className="text-ink-3">none</span>}</td>
                <td className="px-3 py-1.5">{r.category}</td>
                <td className="num px-3 py-1.5 text-right">{r.wins.toLocaleString("en-SG")}</td>
                <td className="px-3 py-1.5 text-ink-3">{r.shared ? `shared, ${r.file}` : r.file}</td>
                <td className="px-3 py-1.5"><Actions rule={r} edit={edit} remove={remove} busy={busy} /></td>
              </tr>
              {editing?.rule?.id === r.id && (
                <tr>
                  <td colSpan={8} className="bg-tile/50 px-3 py-2">{editor(r)}</td>
                </tr>
              )}
            </Fragment>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// G13: phone rows for the rules in order: the text and its row count, then who and where, then the file
function FlatList({ rules, editing, edit, remove, editor, busy }: RowActions & { rules: Rule[] }) {
  return (
    <ul className="m-0 list-none rounded-lg border border-line p-0 text-[13px]">
      {rules.map((r) => (
        <li key={r.id} className="border-t border-line px-3 py-2 first:border-t-0">
          <div className="flex items-start justify-between gap-3">
            <span className="min-w-0"><span className="num mr-2 text-ink-3">{r.order}</span><Matches rule={r} /></span>
            <span className="num shrink-0 text-right">{count(r.wins, "row")}</span>
          </div>
          <p className="m-0 text-[12px] text-ink-2">
            {[tierLabel(r.tier), [r.payee, r.category].filter(Boolean).join(", ")].filter(Boolean).join(" · ")}
          </p>
          <RuleProblems rule={r} />
          <div className="flex items-center justify-between gap-2 text-[12px] text-ink-3">
            <span className="min-w-0 truncate">{r.shared ? `shared, ${r.file}` : r.file}</span>
            <Actions rule={r} edit={edit} remove={remove} busy={busy} />
          </div>
          {editing?.rule?.id === r.id && <div className="mt-2">{editor(r)}</div>}
        </li>
      ))}
    </ul>
  );
}
