import { createContext, useContext, useEffect, useRef, useState, type DragEvent, type ReactNode } from "react";
import { ArrowDown, ArrowUp, GripVertical, Pencil, Plus, Trash2 } from "lucide-react";
import { postJSON, useApi, type CategoriesState, type CategoryEntry, type CategoryGroup } from "../api";
import { formatSgd } from "../money";
import { buttonClass } from "../components/Button";
import { EmptyState } from "../components/EmptyState";
import { PageActions } from "../components/PageActions";
import { Segmented } from "../components/Segmented";
import { Select } from "../components/Select";
import { Loading, Problem } from "../components/States";
import { Tabs } from "../components/Tabs";
import { showToast } from "../components/Toast";
import { onLinkClick } from "../router";
import type { BucketIcon, NeedClass, SeriesColour } from "../types.gen";
import { KindLabel, NEED_ICON } from "../icons";
import { bucketLook, BucketMark } from "../components/BucketMark";
import { useUrlParams } from "../useUrlParams";
import { BucketStylePicker } from "../components/BucketStylePicker";

// G8: categories on their own page, grouped into buckets. Every change lands
// in the record at once and moves everything that hangs off the category:
// its transactions, budgets and personal rules. G17: every write names the
// category or bucket by its id, with the name this page shows beside every id
// it sends (where it goes included), so a row renamed in another tab, or an id
// SQLite handed to a newer row, is refused rather than written.

const iconButton = "rounded p-1 text-ink-3 hover:bg-hover hover:text-ink disabled:pointer-events-none disabled:opacity-30";
const button = buttonClass("secondary");
const primary = buttonClass("primary");

const KIND_NOTE: Record<string, string> = { income: "Money in, not spending", transfer: "Moves between your own money, not spending" };

// G18: a bucket of another kind changes what a category moved there counts as, so its option says so
const kindNote = (to: CategoryGroup, from: CategoryGroup) =>
  to.kind === from.kind ? undefined : to.kind === "spend" ? "counts as spending" : "not spending";

const plural = (n: number, one: string, many = `${one}s`) => `${n.toLocaleString("en-SG")} ${n === 1 ? one : many}`;

// The tab shown is remembered in the address (?bucket=, so a link opens it) and
// in this browser. The address wins, and is read on every render, so a link or
// Back to another tab while the page is open shows that tab; a name no bucket
// has falls back to the first.
const BUCKET_KEY = "kopifund.categories.bucket";

function readStoredBucket(): string | null {
  try {
    return window.localStorage.getItem(BUCKET_KEY);
  } catch {
    return null;
  }
}

function storeBucket(name: string) {
  try {
    window.localStorage.setItem(BUCKET_KEY, name);
  } catch {
    // private mode or blocked storage: the address still carries it
  }
}

// what a write answers: the name as the server saved it (it collapses spaces), a note when the rules could not
// follow, and (G18, a bucket's colour) the bucket it swapped colours with
type Saved = { name?: string; rules_note?: string; swapped?: string | null };

type Busy = { kind: "rename-cat" | "delete-cat" | "rename-group" | "delete-group"; id: number } | null;

export function Categories() {
  const { data, error, loading } = useApi<CategoriesState>("/api/categories");
  const [busy, setBusy] = useState<Busy>(null);
  const [adding, setAdding] = useState(false);
  const [addingIn, setAddingIn] = useState<string | null>(null);   // the bucket whose "add a category" form is open
  const [dragging, setDragging] = useState<number | null>(null);   // the id of the category being dragged
  const [params, patch] = useUrlParams();
  const inUrl = params.get("bucket");
  const [stored, setStored] = useState<string | null>(readStoredBucket);
  const selected = inUrl || stored;
  const shown = useRef<string | null>(null);
  // Writes go one at a time, in the order they were made: a move, then a need
  // class picked before the move's answer, both land. The same write made
  // again while it waits (a double click) is sent once, since the second
  // would be refused, and say so, for a change that worked.
  const queue = useRef<Promise<unknown>>(Promise.resolve());
  const waiting = useRef(new Set<string>());
  const [pending, setPending] = useState(0);
  // A reorder or a drag sends positions read off the page, so it waits until
  // the page shows the order every earlier write left (`stale` until the
  // refreshed list arrives): a second arrow click cannot put the first back.
  const [stale, setStale] = useState(false);
  useEffect(() => setStale(false), [data]);
  const settling = pending > 0 || stale;
  // A bucket that is gone (deleted here or in another tab, or named by an old link) leaves its name in the
  // address; once the list is current, the address says the tab that is actually shown.
  // (the address alone: a link to a bucket that is gone is corrected without touching the tab this browser remembers)
  useEffect(() => {
    const now = shown.current;
    if (!data || stale || !now) return;
    if (inUrl && inUrl !== now) patch({ bucket: now });
  }, [data, stale, inUrl, patch]);

  if (loading && !data) return <Loading what="Loading your categories" />;
  if (error) return <Problem message={error} hint="Load your statements first, from the Upload page." retry />;
  if (!data) {
    return (
      <EmptyState title="No categories yet"
        action={<a href="/upload" onClick={onLinkClick("/upload")} className={button}>Load your statements</a>}>
        Categories come with your first statements.
      </EmptyState>
    );
  }
  const groups = data.groups;
  // G13: what each spending bucket takes of the month, for the bar under its name
  const withData = (g: CategoryGroup) => g.categories.filter((c) => c.transactions > 0 && c.monthly != null);
  // null when no category in the bucket has a month to average; a refund-heavy bucket can be negative
  const monthlyOf = (g: CategoryGroup): number | null => (withData(g).length ? withData(g).reduce((n, c) => n + c.monthly!, 0) : null);
  const spendMonthly = groups.filter((g) => g.kind === "spend").reduce((n, g) => n + (monthlyOf(g) ?? 0), 0);
  // A bucket that was just added or renamed is not in the list until the page
  // reloads it; until then the tab stays where it was rather than jumping to the first.
  const current = groups.find((g) => g.name === selected)
    ?? (stale ? groups.find((g) => g.name === shown.current) : undefined) ?? groups[0];
  shown.current = current?.name ?? null;
  // only the shown tab has an add form: one left open on a tab the address moved away from (a link, Back) is
  // closed, as a tab click closes it, so going back does not reopen it empty
  if (addingIn && current && addingIn !== current.name) setAddingIn(null);
  const pick = (name: string) => { setStored(name); storeBucket(name); patch({ bucket: name }); setAddingIn(null); };

  function send(url: string, body: object, done?: string): Promise<false | Saved> {
    const key = url + JSON.stringify(body);
    if (waiting.current.has(key)) return Promise.resolve(false);
    waiting.current.add(key);
    setPending((n) => n + 1);
    const run = queue.current.then(() => write(url, body, done)).finally(() => {
      waiting.current.delete(key);
      setPending((n) => n - 1);
    });
    queue.current = run.catch(() => undefined);
    return run;
  }

  async function write(url: string, body: object, done?: string) {
    try {
      const out = await postJSON<Saved>(url, JSON.stringify(body));
      setStale(true);
      if (out.rules_note) {
        // the change is saved, but the rules could not follow it: say so
        // rather than claim they did
        showToast({ tone: "error", message: `Saved. ${out.rules_note.charAt(0).toUpperCase()}${out.rules_note.slice(1)}.` });
      } else if (out.swapped) {
        showToast({ message: `Swapped colours with ${out.swapped}.` });
      } else if (done) {
        showToast({ message: done });
      }
      setBusy(null);
      return out;
    } catch (e) {
      showToast({ tone: "error", message: (e as Error).message });
      return false;
    }
  }

  // a move into a bucket of another kind changes what the stats count: say so
  const COUNTS_AS: Record<string, string> = { spend: "spending", income: "income, not spending", transfer: "a transfer, not spending" };
  const move = (cat: CategoryEntry, to: CategoryGroup, index?: number) => {
    const from = groups.find((g) => g.categories.some((c) => c.id === cat.id));
    const note = from && from.kind !== to.kind ? `${cat.name} now counts as ${COUNTS_AS[to.kind]}.` : undefined;
    return send("/api/categories/move", { id: cat.id, name: cat.name, group_id: to.id, group: to.name, index }, note);
  };

  function moveGroup(i: number, by: number) {
    const order = [...groups];
    const [g] = order.splice(i, 1);
    order.splice(i + by, 0, g);
    void send("/api/category-groups/reorder", { ids: order.map((o) => o.id), names: order.map((o) => o.name) });
  }

  // a category dropped on a row lands just before it; on the card's empty
  // space, at the end of that bucket
  function drop(e: DragEvent, group: CategoryGroup, index?: number) {
    e.preventDefault();
    e.stopPropagation();
    // only a category dragged from this page: dropped text or a file is not one
    const id = dragging;
    setDragging(null);
    if (id == null || settling) return;
    const from = groups.find((g) => g.categories.some((c) => c.id === id));
    const cat = from?.categories.find((c) => c.id === id);
    if (!from || !cat) return;
    let at = index;
    // moving down inside one bucket: the row it was dropped on moves up one
    if (at != null && from.id === group.id) {
      const was = from.categories.findIndex((c) => c.id === id);
      if (was === at || was === at - 1) return;
      if (was < at) at -= 1;
    }
    void move(cat, group, at);
  }

  return (
    <Pending.Provider value={pending > 0}>
    <Settling.Provider value={settling}>
    <div className="flex flex-col gap-3">
      <PageActions>
        <button type="button" onClick={() => setAdding(true)} className={button}>
          <Plus size={14} aria-hidden /> Add bucket
        </button>
      </PageActions>
      {adding && (
        <NameForm label="New bucket name" submit="Add bucket" onCancel={() => setAdding(false)}
          onSave={async (name) => { const saved = await send("/api/category-groups/add", { name }); if (saved) { setAdding(false); pick(saved.name ?? name); } }} />
      )}

      {groups.length === 0 && (
        <EmptyState title="No categories yet" action={<button type="button" onClick={() => setAdding(true)} className={button}>Add a bucket</button>}>
          Buckets group your categories. Add a bucket first, then the categories in it.
        </EmptyState>
      )}
      {current && (
        <Tabs label="Buckets" value={current.name} onChange={pick}
          items={groups.map((g, i) => ({
            id: g.name, label: g.name, count: g.categories.length, icon: <BucketMark look={bucketLook(g, i)} />,
            // a category dragged onto another bucket's tab moves there
            props: { onDragOver: (e) => { if (dragging != null) e.preventDefault(); }, onDrop: (e) => drop(e, g) },
          }))}
          panelProps={{ onDragOver: (e) => { if (dragging != null) e.preventDefault(); }, onDrop: (e) => drop(e, current) }}
          panelClassName="rounded-xl border border-line bg-panel px-4 py-3">
          {(() => {
            const g = current;
            const gi = groups.indexOf(g);
            return (
              <>
                <GroupHeader group={g} first={gi === 0} last={gi === groups.length - 1} index={gi}
                  others={Object.fromEntries(groups.filter((o) => o.id !== g.id).map((o) => [o.shown, o.name]))}
                  onStyle={(change) => send("/api/category-groups/style", { id: g.id, name: g.name, ...change })}
                  monthly={monthlyOf(g)} share={g.kind === "spend" && spendMonthly > 0 && (monthlyOf(g) ?? 0) > 0 ? monthlyOf(g)! / spendMonthly : null}
                  onUp={() => moveGroup(gi, -1)} onDown={() => moveGroup(gi, 1)}
                  onRename={() => setBusy({ kind: "rename-group", id: g.id })}
                  onDelete={() => setBusy({ kind: "delete-group", id: g.id })} />
                {busy?.kind === "rename-group" && busy.id === g.id && (
                  <NameForm label={`New name for ${g.name}`} submit="Rename" initial={g.name} onCancel={() => setBusy(null)}
                    onSave={async (name) => { const saved = await send("/api/category-groups/rename", { id: g.id, name: g.name, new_name: name }); if (saved) pick(saved.name ?? name); }} />
                )}
                {busy?.kind === "delete-group" && busy.id === g.id && (
                  <DeleteGroup group={g} groups={groups} onCancel={() => setBusy(null)}
                    onDelete={async (to) => {
                      if (await send("/api/category-groups/delete", { id: g.id, name: g.name, move_to_id: to?.id, move_to: to?.name }, `Deleted the ${g.name} bucket.`) && to) pick(to.name);
                    }} />
                )}

                {g.categories.length === 0 && (
                  <EmptyState title="No categories yet"
                    action={addingIn === g.name ? undefined : <button type="button" onClick={() => setAddingIn(g.name)} className={button}>Add category</button>}>
                    Add one, or move one here from another bucket.
                  </EmptyState>
                )}
                <ul className="m-0 list-none p-0">
                  {g.categories.map((c, ci) => (
                    <li key={c.id} draggable={!settling}
                      onDragStart={(e) => { e.dataTransfer.setData("text/plain", c.name); e.dataTransfer.effectAllowed = "move"; setDragging(c.id); }}
                      onDragEnd={() => setDragging(null)}
                      onDragOver={(e) => { if (dragging != null) e.preventDefault(); }}
                      onDrop={(e) => drop(e, g, ci)}
                      className={`border-t border-line py-1.5 first:border-t-0 ${dragging === c.id ? "opacity-40" : ""}`}>
                      <CategoryRow cat={c} group={g} groups={groups} needClasses={data.need_classes[g.kind] ?? []}
                        first={ci === 0} last={ci === g.categories.length - 1}
                        onNeed={(v) => void send("/api/categories", { id: c.id, name: c.name, need_class: v })}
                        onMove={(to) => void move(c, to)}
                        onUp={() => void move(c, g, ci - 1)}
                        onDown={() => void move(c, g, ci + 1)}
                        onRename={() => setBusy({ kind: "rename-cat", id: c.id })}
                        onDelete={() => setBusy({ kind: "delete-cat", id: c.id })} />
                      {busy?.kind === "rename-cat" && busy.id === c.id && (
                        <NameForm label={`New name for ${c.name}`} submit="Rename" initial={c.name} onCancel={() => setBusy(null)}
                          onSave={(name) => void send("/api/categories/rename", { id: c.id, name: c.name, new_name: name },
                            c.personal_rules ? `Renamed. Your rules that said ${c.name} now say ${name}.` : undefined)} />
                      )}
                      {busy?.kind === "delete-cat" && busy.id === c.id && (
                        <DeleteCategory cat={c} groups={groups} onCancel={() => setBusy(null)}
                          onDelete={(to) => void send("/api/categories/delete", { id: c.id, name: c.name, move_to_id: to?.id, move_to: to?.name },
                            to ? `Deleted ${c.name}. Its transactions, budgets and rules are in ${to.name} now.` : `Deleted ${c.name}.`)} />
                      )}
                    </li>
                  ))}
                </ul>
                <AddCategory key={g.id} group={g} open={addingIn === g.name} onOpen={() => setAddingIn(g.name)} onClose={() => setAddingIn(null)}
                  trigger={g.categories.length > 0} onAdd={(name) => send("/api/categories/add", { name, group_id: g.id, group: g.name })} />
              </>
            );
          })()}
        </Tabs>
      )}
    </div>
    </Settling.Provider>
    </Pending.Provider>
  );
}

// true while a write is on its way: the forms' buttons wait for it
const Pending = createContext(false);
// true until the page shows what every write left: the order controls wait
const Settling = createContext(false);

function GroupHeader({ group, first, last, index, others, onStyle, monthly, share, onUp, onDown, onRename, onDelete }: {
  group: CategoryGroup; first: boolean; last: boolean; index: number;
  others: Partial<Record<SeriesColour, string>>;          // G18: each other bucket's name by the colour it is drawn in
  onStyle: (change: { icon: BucketIcon } | { colour: SeriesColour }) => Promise<unknown>;
  monthly: number | null; share: number | null;           // what the bucket takes a month, and its share of all spending (spending buckets only)
  onUp: () => void; onDown: () => void; onRename: () => void; onDelete: () => void;
}) {
  const settling = useContext(Settling);
  const n = group.categories.length;
  return (
    <div className="mb-1 flex items-start justify-between gap-2">
      {/* one picker per bucket, so a pick still saving for one is never shown on another's tab */}
      <BucketStylePicker key={group.id} name={group.name} style={group} place={index} others={others} onPick={onStyle} />
      <div className="min-w-0 flex-1">
        <h2 className="m-0 text-[14px] font-semibold">{group.name}</h2>
        <p className="num m-0 text-[12px] text-ink-2">
          {plural(n, "category", "categories")}{monthly != null && `, ${formatSgd(monthly)} a month`}
        </p>
        {share != null && (
          <div className="mt-1 flex items-center gap-2">
            <div aria-hidden className="h-1.5 w-40 max-w-[50%] overflow-hidden rounded-full bg-hover">
              {/* G18: the bucket's own colour, the one Spending and Home draw it in */}
              <div className="h-full rounded-full" style={{ width: `${Math.max(2, share * 100)}%`, background: bucketLook(group, index).colour }} />
            </div>
            <span className="num text-[12px] text-ink-3">{Math.round(share * 100)}% of spending</span>
          </div>
        )}
        {KIND_NOTE[group.kind] && <p className="m-0 text-[12px] text-ink-3">{KIND_NOTE[group.kind]}</p>}
      </div>
      <div className="flex shrink-0 items-center">
        <button type="button" aria-label={`Move bucket ${group.name} up`} disabled={first || settling} onClick={onUp} className={iconButton}><ArrowUp size={14} /></button>
        <button type="button" aria-label={`Move bucket ${group.name} down`} disabled={last || settling} onClick={onDown} className={iconButton}><ArrowDown size={14} /></button>
        <button type="button" aria-label={`Rename bucket ${group.name}`} onClick={onRename} className={iconButton}><Pencil size={14} /></button>
        {!group.fixed && (
          <button type="button" aria-label={`Delete bucket ${group.name}`} onClick={onDelete} className={iconButton}><Trash2 size={14} /></button>
        )}
      </div>
    </div>
  );
}

function CategoryRow({ cat, group, groups, needClasses, first, last, onNeed, onMove, onUp, onDown, onRename, onDelete }: {
  cat: CategoryEntry; group: CategoryGroup; groups: CategoryGroup[]; needClasses: NeedClass[]; first: boolean; last: boolean;
  onNeed: (v: string) => void; onMove: (to: CategoryGroup) => void; onUp: () => void; onDown: () => void;
  onRename: () => void; onDelete: () => void;
}) {
  const settling = useContext(Settling);
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
      <GripVertical size={14} aria-hidden className="shrink-0 cursor-grab text-ink-3" />
      <span className="min-w-36 flex-1 truncate text-[13px]">{cat.name}</span>
      <span className="num text-[12px] text-ink-3 sm:w-32 sm:text-right">{plural(cat.transactions, "transaction")}</span>
      <span className="num text-[12px] text-ink-3 sm:w-28 sm:text-right">
        {cat.monthly != null && cat.transactions > 0 ? `${formatSgd(cat.monthly)} a month` : ""}
      </span>
      {/* G13: a choice shows all its options at once (Need / Want); a kind with nothing to choose shows its label */}
      {needClasses.length > 1 ? (
        <Segmented label={`Need class for ${cat.name}`} value={cat.need_class ?? ""} onChange={onNeed}
          options={needClasses.map((n) => ({ value: n, text: n, icon: NEED_ICON[n] }))} />
      ) : (
        <span className="w-[74px] text-[12px] text-ink-3">
          {cat.need_class ? <KindLabel icon={NEED_ICON[cat.need_class]}>{cat.need_class}</KindLabel> : ""}
        </span>
      )}
      {/* a bucket of another kind changes what the category counts as, so it says so */}
      <Select label={`Move ${cat.name} to`} placeholder="Bucket" value={String(group.id)} className="w-[136px] text-[12px]"
        options={groups.map((g) => ({ value: String(g.id), label: g.name, note: kindNote(g, group) }))}
        onChange={(v) => { const to = groups.find((g) => String(g.id) === v); if (to) onMove(to); }} />
      <div className="flex items-center">
        <button type="button" aria-label={`Move ${cat.name} up`} disabled={first || settling} onClick={onUp} className={iconButton}><ArrowUp size={13} /></button>
        <button type="button" aria-label={`Move ${cat.name} down`} disabled={last || settling} onClick={onDown} className={iconButton}><ArrowDown size={13} /></button>
        <button type="button" aria-label={`Rename ${cat.name}`} onClick={onRename} className={iconButton}><Pencil size={13} /></button>
        <button type="button" aria-label={`Delete ${cat.name}`} onClick={onDelete} className={iconButton}><Trash2 size={13} /></button>
      </div>
    </div>
  );
}

function NameForm({ label, submit, initial = "", onSave, onCancel }: {
  label: string; submit: string; initial?: string; onSave: (name: string) => void; onCancel: () => void;
}) {
  const [name, setName] = useState(initial);
  const pending = useContext(Pending);
  return (
    <form className="my-2 flex flex-wrap items-center gap-2" onSubmit={(e) => { e.preventDefault(); if (name.trim()) onSave(name.trim()); }}
      onKeyDown={(e) => { if (e.key === "Escape") onCancel(); }}>
      <input aria-label={label} placeholder={label} value={name} onChange={(e) => setName(e.target.value)} autoFocus
        className="h-8 min-w-40 flex-1 rounded-md border border-line bg-panel px-2 text-[13px]" />
      <button type="submit" disabled={pending} className={primary}>{submit}</button>
      <button type="button" onClick={onCancel} className={button}>Cancel</button>
    </form>
  );
}

function AddCategory({ group, open, onOpen, onClose, trigger, onAdd }: {
  group: CategoryGroup; open: boolean; onOpen: () => void; onClose: () => void;
  trigger: boolean;                      // false in an empty bucket, whose empty state carries the button
  onAdd: (name: string) => Promise<unknown>;
}) {
  if (!open) {
    if (!trigger) return null;
    return (
      <button type="button" onClick={onOpen} className={buttonClass("ghost", "sm", "mt-1 -ml-1")}>
        <Plus size={13} aria-hidden /> Add category
      </button>
    );
  }
  return (
    <NameForm label={`New category in ${group.name}`} submit="Add" onCancel={onClose}
      onSave={async (name) => { if (await onAdd(name)) onClose(); }} />
  );
}

function Confirm({ children, action, disabled, onConfirm, onCancel }: {
  children: ReactNode; action: string; disabled: boolean; onConfirm: () => void; onCancel: () => void;
}) {
  const pending = useContext(Pending);
  return (
    <div className="my-2 flex flex-col gap-2 rounded-md border border-line bg-tile px-3 py-2 text-[13px]">
      {children}
      <div className="flex gap-2">
        <button type="button" disabled={disabled || pending} onClick={onConfirm} className={primary}>{action}</button>
        <button type="button" onClick={onCancel} className={button}>Cancel</button>
      </div>
    </div>
  );
}

function DeleteCategory({ cat, groups, onDelete, onCancel }: {
  cat: CategoryEntry; groups: CategoryGroup[]; onDelete: (to: CategoryEntry | null) => void; onCancel: () => void;
}) {
  const [to, setTo] = useState("");      // the chosen category's id, as the select holds it
  const target = groups.flatMap((g) => g.categories).find((c) => String(c.id) === to) ?? null;
  const shared = cat.rules - cat.personal_rules;
  // nothing is filed under it and no rule names it: there is nothing to move, so no place to ask for
  if (cat.transactions === 0 && cat.budgets === 0 && cat.rules === 0) {
    return (
      <Confirm action="Delete" disabled={false} onConfirm={() => onDelete(null)} onCancel={onCancel}>
        <p className="m-0">Nothing is filed under {cat.name}, so it is just removed. A copy of your record is kept in backups first.</p>
      </Confirm>
    );
  }
  return (
    <Confirm action="Delete and move" disabled={!target} onConfirm={() => onDelete(target)} onCancel={onCancel}>
      <p className="m-0">
        {plural(cat.transactions, "transaction")}, {plural(cat.budgets, "budget")} and {plural(cat.personal_rules, "of your rules", "of your rules")} move
        to the category you pick. {shared > 0 && `${plural(shared, "shared rule")} still say ${cat.name}; their rows land in the same place.`} A
        copy of your record is kept in backups first.
      </p>
      <label className="flex flex-wrap items-center gap-2">
        <span className="text-ink-2">Move them to</span>
        <Select label={`Move ${cat.name}'s transactions to`} placeholder="Choose a category" value={to} onChange={setTo} className="min-w-48"
          options={groups.flatMap((g) => g.categories.filter((c) => c.id !== cat.id).map((c) => ({ value: String(c.id), label: c.name, group: g.name })))} />
      </label>
    </Confirm>
  );
}

function DeleteGroup({ group, groups, onDelete, onCancel }: {
  group: CategoryGroup; groups: CategoryGroup[]; onDelete: (to: CategoryGroup | null) => void; onCancel: () => void;
}) {
  const [to, setTo] = useState("");      // the chosen bucket's id, as the select holds it
  const target = groups.find((g) => String(g.id) === to) ?? null;
  const n = group.categories.length;
  return (
    <Confirm action={`Delete ${group.name}`} disabled={n > 0 && !target} onConfirm={() => onDelete(target)} onCancel={onCancel}>
      {n === 0 ? <p className="m-0">This bucket is empty.</p> : (
        <label className="flex flex-wrap items-center gap-2">
          <span className="text-ink-2">Its {plural(n, "category", "categories")} move to</span>
          <Select label={`Move ${group.name}'s categories to`} placeholder="Choose a bucket" value={to} onChange={setTo} className="min-w-40"
            options={groups.filter((g) => g.id !== group.id).map((g) => ({ value: String(g.id), label: g.name, note: kindNote(g, group) }))} />
        </label>
      )}
    </Confirm>
  );
}
