import { Fragment, useEffect, useState } from "react";
import { Sparkles } from "lucide-react";
import { postJSON, type NeedsRule, type SuggestReply } from "../api";
import { onLinkClick } from "../router";
import { Button } from "./Button";
import { Combobox } from "./Combobox";
import { formatDay } from "../dates";
import { GroupRowsPanel } from "./PinRow";
import { Pager, usePager } from "./Pager";
import { Loading } from "./States";
import { useAction } from "./useAction";
import { HEAD_ROW, TABLE_BOX, TH } from "./tableKit";
import { TextInput } from "./TextInput";

type Row = {
  text: string;
  field: string;
  rows: number;
  last: string;
  match_on: string;
  pattern: string;
  payee: string;
  kind: string;
  category: string;
  note: string;
  ai: boolean;
  picked: boolean;
};

// How the rules loader matches: the text upper-cased with spaces collapsed,
// against a shell-style pattern (* any run, ? one character).
const norm = (t: string) => t.split(/\s+/).filter(Boolean).join(" ").toUpperCase();
function globMatches(pattern: string, text: string) {
  const re = norm(pattern).replace(/[.+^${}()|[\]\\]/g, "\\$&").replace(/\*/g, ".*").replace(/\?/g, ".");
  return new RegExp(`^${re}$`).test(norm(text));
}

// Each ticked row whose text another ticked row's pattern would also catch
// with a different payee or category: saved together, the first rule in the
// file would win both, so the save waits until one pattern is narrowed.
// Rules only meet inside one field (merchant, counterparty, memo), so rows
// matched on different fields never clash.
export function clashes(rows: { text: string; match_on: string; pattern: string; payee: string; category: string; picked: boolean }[]) {
  const out = new Map<string, string>();
  const live = rows.filter((r) => r.picked && r.category);
  for (const a of live) {
    const b = live.find((o) => o !== a && o.match_on === a.match_on && globMatches(o.pattern, a.text) &&
      (o.payee.trim() !== a.payee.trim() || o.category !== a.category));
    if (b) out.set(a.text, b.pattern);
  }
  return out;
}

// G12: the payees no rule names, each with a payee and category to save as a
// rule right here. With [ai] on, a model fills them in and the sure ones come
// ticked; without it the table starts from a guess at the payee's name.
export function NeedsRuleTable({ groups, canLoad, onSaved }: {
  groups: NeedsRule[];
  canLoad: boolean;
  onSaved: (load: boolean) => Promise<void>;
}) {
  const [reply, setReply] = useState<SuggestReply | null>(null);
  const [rows, setRows] = useState<Row[]>([]);
  const [failed, setFailed] = useState<string | null>(null);
  const { running: saving, error, run } = useAction();
  const [open, setOpen] = useState<string | null>(null);   // the group whose rows are listed
  const [patterns, setPatterns] = useState<Set<string>>(new Set());   // rows whose pattern box is open

  // G13: pages of payees; a fresh check (a different set of payees) starts at page 1
  const pager = usePager("needs-rule", rows.length, groups.map((g) => g.text).join("\n"));

  useEffect(() => {
    let live = true;
    setReply(null);
    setFailed(null);
    postJSON<SuggestReply>("/api/rules/suggest", JSON.stringify({ groups }))
      .then((r) => {
        if (!live) return;
        const byText = new Map(r.suggestions.map((s) => [s.text, s]));
        setReply(r);
        setRows(groups.filter((g) => r.drafts[g.text]).map((g) => {
          const d = r.drafts[g.text];
          const s = byText.get(g.text);
          return {
            text: g.text, field: g.field, rows: g.rows, last: g.last, match_on: d.match_on, pattern: d.pattern,
            payee: s?.payee || d.payee, kind: s?.kind || d.kind, category: s?.category ?? "",
            note: s?.note ?? "", ai: !!s, picked: !!s?.confident,
          };
        }));
      })
      .catch((e: Error) => live && setFailed(e.message));
    return () => { live = false; };
  }, [groups]);

  if (failed) return <p className="m-0 text-warn">Could not prepare the payees: {failed}</p>;
  if (!reply) return <Loading what={`Naming ${groups.length === 1 ? "1 payee" : `${groups.length} payees`}…`} />;

  const update = (i: number, change: Partial<Row>) =>
    setRows((rs) => rs.map((r, j) => (j === i ? { ...r, ...change } : r)));
  const conflicts = clashes(rows);
  // two ticked rows with the same pattern and the same answer need one rule
  const chosen = rows.filter((r, i) => r.picked && r.category && !rows.slice(0, i).some((o) =>
    o.picked && o.category && o.match_on === r.match_on && norm(o.pattern) === norm(r.pattern) && o.payee.trim() === r.payee.trim() && o.category === r.category));
  const suggested = rows.filter((r) => r.ai && r.category).length;
  const unsure = rows.length - rows.filter((r) => r.picked).length;

  async function save() {
    await run("save", async () => {
      await postJSON("/api/rules/batch", JSON.stringify({
        rules: chosen.map((r) => ({
          match_on: r.match_on, pattern: r.pattern, payee: r.payee.trim(), kind: r.kind,
          category: r.category, notes: r.ai ? `[ai] suggested by ${reply!.model}` : "",
        })),
      }));
      await onSaved(canLoad);
    });
  }

  const n = chosen.length;
  // rows that print only a bank code: no pattern can name them, so each is set on its own
  const coded = groups.filter((g) => !rows.some((r) => r.text === g.text));
  const rowsOf = (g: { text: string; rows: number }) =>
    <GroupRowsPanel query={new URLSearchParams({ text: g.text }).toString()} expected={g.rows} />;
  const toggle = (text: string) => (
    <button type="button" aria-expanded={open === text} onClick={() => setOpen(open === text ? null : text)}
      className="mt-1 text-[12px] text-ink-2 underline decoration-line underline-offset-2 hover:text-ink">
      {open === text ? "Hide the rows" : "Set one row instead"}
    </button>
  );
  return (
    <div className="flex flex-col gap-2">
      <p className="m-0">
        {rows.length === 1 ? "1 payee had no rule." : `${rows.length} payees had no rule.`}{" "}
        {reply.enabled && !reply.error && (
          <span className="text-ink-2">
            <Sparkles size={14} aria-hidden className="mr-1 inline align-[-2px]" />
            {suggested} suggested by {reply.model}{unsure > 0 ? `; ${unsure} need you` : ""}.
          </span>
        )}
        {!reply.enabled && <span className="text-ink-2">Pick a category for each to save it as a rule.</span>}
      </p>
      {reply.error && <p className="m-0 text-[13px] text-warn">No suggestions this time: {reply.error}</p>}
      {rows.length > 0 && (
      <div className={`relative ${TABLE_BOX}`}>
        {/* G13: under 768px each payee is a stacked row (text, payee, category), so the category never sits behind a sideways scroll */}
        <table className="w-full border-collapse text-[13px] max-md:block md:min-w-[560px]">
          <thead className="max-md:hidden">
            <tr className={HEAD_ROW}>
              <th className={`${TH} w-8 px-2`}><span className="sr-only">Save</span></th>
              <th className={`${TH} px-2`}>Statement text</th>
              <th className={`${TH} px-2`}>Payee</th>
              <th className={`${TH} w-48 px-2`}>Category</th>
            </tr>
          </thead>
          <tbody className="max-md:block">
            {rows.map((r, i) => [r, i] as const).slice(pager.offset, pager.offset + pager.size).map(([r, i]) => (
              <Fragment key={r.text}>
                <tr className="border-t border-line align-top max-md:grid max-md:grid-cols-[auto_minmax(0,1fr)] max-md:gap-x-2 max-md:first:border-t-0">
                  <td className="px-2 py-2 max-md:row-span-3 max-md:py-1 max-md:pr-0">
                    <input type="checkbox" checked={r.picked} aria-label={`Save a rule for ${r.text}`}
                      onChange={(e) => update(i, { picked: e.target.checked })} />
                  </td>
                  <td className="px-2 py-2 max-md:py-1">
                    <span className="block break-all">{r.text}</span>
                    <span className="num block text-[12px] text-ink-3">
                      {r.rows} {r.rows === 1 ? "row" : "rows"}, last on {formatDay(r.last)}
                    </span>
                    {toggle(r.text)}
                  </td>
                  <td className="px-2 py-2 max-md:py-1">
                    <span className="mb-0.5 block text-[12px] text-ink-3 md:hidden">Payee</span>
                    <TextInput value={r.payee} aria-label={`Payee for ${r.text}`}
                      onChange={(e) => update(i, { payee: e.target.value })} className="w-full" />
                    {/* G13: the wildcard stays out of sight until asked for; a clash opens it so it can be narrowed */}
                    {patterns.has(r.text) || conflicts.has(r.text) ? (
                      <label className="mt-1 flex items-center gap-1 text-[12px] text-ink-3">
                        Pattern
                        <input value={r.pattern} aria-label={`Pattern for ${r.text}`}
                          // typing keeps the box open, even when it was opened by a clash that this edit clears
                          onChange={(e) => { setPatterns((s) => new Set(s).add(r.text)); update(i, { pattern: e.target.value }); }}
                          className="h-6 min-w-0 flex-1 rounded border border-line bg-panel px-1 text-[12px] text-ink-2" />
                      </label>
                    ) : (
                      <button type="button" aria-label={`Edit pattern for ${r.text}`}
                        onClick={() => setPatterns((s) => new Set(s).add(r.text))}
                        className="mt-1 text-[12px] text-ink-2 underline decoration-line underline-offset-2 hover:text-ink">
                        Edit pattern
                      </button>
                    )}
                    {r.note && !r.picked && <span className="mt-1 block text-[12px] text-warn">Not sure: {r.note}</span>}
                    {conflicts.has(r.text) && (
                      <span className="mt-1 block text-[12px] text-warn">
                        The rule {conflicts.get(r.text)} would catch this too. Narrow one of the two patterns.
                      </span>
                    )}
                  </td>
                  <td className="px-2 py-2 max-md:py-1">
                    <span className="mb-0.5 block text-[12px] text-ink-3 md:hidden">Category</span>
                    <Combobox label={`Category for ${r.text}`} value={r.category} options={reply.categories}
                      placeholder="Choose a category"
                      onChange={(category) => update(i, { category, picked: true })} />
                  </td>
                </tr>
                {open === r.text && (
                  <tr className="max-md:block">
                    <td className="max-md:hidden" />
                    <td colSpan={3} className="px-2 pb-2 max-md:block">{rowsOf(r)}</td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      </div>
      )}
      <Pager pager={pager} label="Payees pages" />
      {coded.length > 0 && (
        <div className="flex flex-col gap-1">
          <p className="m-0 text-[13px] text-ink-2">
            {coded.length === 1 ? "1 more prints" : `${coded.length} more print`} only a bank code, so no pattern can
            name {coded.length === 1 ? "it" : "them"}. Set {coded.length === 1 ? "its rows" : "their rows"} one at a time.
          </p>
          <ul className="m-0 flex list-none flex-col rounded-lg border border-line p-0 text-[13px]">
            {coded.map((g) => (
              <li key={g.text} className="border-t border-line px-2 py-2 first:border-t-0">
                <span className="break-all">{g.text}</span>{" "}
                <span className="num text-[12px] text-ink-3">{g.rows} {g.rows === 1 ? "row" : "rows"}, last on {formatDay(g.last)}</span>
                <div>{toggle(g.text)}</div>
                {open === g.text && <div className="mt-1">{rowsOf(g)}</div>}
              </li>
            ))}
          </ul>
        </div>
      )}
      {error && <p className="m-0 text-[13px] text-warn">{error}</p>}
      {conflicts.size > 0 && (
        <p className="m-0 text-[13px] text-warn">
          {conflicts.size === 1 ? "1 payee clashes" : `${conflicts.size} payees clash`} with another pattern, so nothing saves yet.{" "}
          <button type="button" onClick={() => pager.setPage(Math.floor(rows.findIndex((r) => conflicts.has(r.text)) / pager.size) + 1)}
            className="underline decoration-line underline-offset-2 hover:text-ink">Go to the first</button>
        </p>
      )}
      <div className="flex flex-wrap items-center gap-2">
        {rows.length > 0 && <Button variant="primary" disabled={saving || n === 0 || conflicts.size > 0} onClick={() => void save()}>
          {saving ? "Saving…" : `Save ${n === 1 ? "1 rule" : `${n} rules`}${canLoad ? " and load" : ""}`}
        </Button>}
        <a href="/rules" onClick={onLinkClick("/rules")} className="text-[13px] text-ink-2">Open Rules</a>
      </div>
    </div>
  );
}
