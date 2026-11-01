import { useEffect, useMemo, useState } from "react";
import { postJSON, rowsChanged, useApi, type PayeeCategory, type Rule, type RulePreview, type RuleSavedEdit } from "../api";
import { formatAmount } from "../money";
import { formatDay } from "../dates";
import { Button } from "./Button";
import { Combobox } from "./Combobox";
import { Select } from "./Select";
import { StatusChip } from "./StatusChip";
import { TIER_TESTS, tierLabel } from "../ruleWords";

export type Draft = {
  match_on: string; pattern: string; payee: string; kind: string; country: string;
  category: string; recurring: boolean; notes: string;
};

export const NEW_DRAFT: Draft = {
  match_on: "merchant", pattern: "", payee: "", kind: "Merchant", country: "", category: "", recurring: false, notes: "",
};

export function draftOf(r: Rule): Draft {
  return {
    match_on: r.tier, pattern: r.pattern, payee: r.payee, kind: r.kind || "Merchant", country: r.country,
    category: r.category, recurring: r.recurring, notes: r.notes,
  };
}

/** A draft as the server takes a rule: a kind only beside a payee, recurring as "yes" or blank. */
export function ruleOf(draft: Draft) {
  return { ...draft, kind: draft.payee.trim() ? draft.kind : "", recurring: draft.recurring ? "yes" : "" };
}

/** A draft as GET /api/rules/preview asks for it. */
export function draftParams(draft: Draft): URLSearchParams {
  const r = ruleOf(draft);
  return new URLSearchParams({
    match_on: r.match_on, pattern: r.pattern, payee: r.payee, kind: r.kind,
    country: r.country, category: r.category, recurring: r.recurring, notes: r.notes,
  });
}

const plural = (n: number, one: string, many = `${one}s`) => `${n.toLocaleString("en-SG")} ${n === 1 ? one : many}`;
const outcome = (o: PayeeCategory) => (o.category ? [o.payee, o.category].filter(Boolean).join(", ") : "no rule");

// G7: add a rule, or edit one, with a live preview of what saving it would
// change. The preview is the server's: the whole rule set loaded with the
// change, first match wins across every tier, run over the record.
export type SaveStatus = { saving: boolean; error: string | null };

export function RuleEditor({ rule, initial, tiers, tierFiles, categories, kinds, onSaved, onCancel, onDraft, status, onStatus }: {
  rule: Rule | null;                 // the rule being edited, or null for a new one
  initial: Draft;
  tiers: string[];
  tierFiles: Record<string, string>;
  categories: string[];
  kinds: string[];
  onSaved: (message: string, saved: RuleSavedEdit) => void;
  onCancel: () => void;
  // told of every change, so a parent that redraws the editor (the window crossing 768px) can start it from the live draft
  onDraft?: (draft: Draft) => void;
  // the same for a save in flight and its failure: a save started in the editor that was replaced still reports to the one that is shown
  status?: SaveStatus;
  onStatus?: (status: SaveStatus) => void;
}) {
  const [draft, setDraft] = useState<Draft>(initial);
  useEffect(() => { if (draft !== initial) onDraft?.(draft); }, [draft, initial, onDraft]);
  const [own, setOwn] = useState<SaveStatus>({ saving: false, error: null });
  const { saving, error } = status ?? own;
  const report = (s: SaveStatus) => { setOwn(s); onStatus?.(s); };
  const set = (change: Partial<Draft>) => setDraft((d) => ({ ...d, ...change }));
  const what = rule ? `rule ${rule.pattern}` : "new rule";

  const query = useMemo(() => {
    const p = draftParams(draft);
    if (rule) {
      p.set("edit_file", rule.file);
      p.set("edit_line", String(rule.line));
      p.set("edit_pattern", rule.pattern);
      p.set("edit_revision", rule.revision);
    }
    return p.toString();
  }, [draft, rule]);
  // wait for a pause in the typing before asking again
  const [asked, setAsked] = useState(query);
  useEffect(() => {
    const t = window.setTimeout(() => setAsked(query), 300);
    return () => window.clearTimeout(t);
  }, [query]);
  const ready = draft.pattern.trim() !== "" && draft.category !== "";
  const preview = useApi<RulePreview>(ready ? `/api/rules/preview?${asked}` : null);
  const p = ready ? preview.data : null;
  const current = asked === query && !preview.loading;

  async function save() {
    report({ saving: true, error: null });
    let failed: string | null = null;
    try {
      const body = {
        rule: ruleOf(draft),
        edit: rule && { file: rule.file, line: rule.line, pattern: rule.pattern, revision: rule.revision },
      };
      const r = await postJSON<RuleSavedEdit>("/api/rules/save", JSON.stringify(body));
      // G13: the save reaches the transactions at once; say how many it moved
      onSaved(r.unchanged ? "Nothing changed."
        : `Rule saved${r.override ? `, ahead of the shared one in ${r.override}` : ""}, ${rowsChanged(r.relinked)}`, r);
    } catch (e) {
      failed = (e as Error).message;
    } finally {
      report({ saving: false, error: failed });
    }
  }

  // a single-row rule is named by a row's key, which means nothing on screen: show the row
  const pinned = rule?.tier === "key";
  const pin = pinned ? rule.pins[0] : undefined;
  const file = tierFiles[draft.match_on];
  const where = rule?.shared
    ? `Shared rule, read only. Saving adds a rule to your ${file} that matches first, ahead of it.`
    : rule && rule.file !== file ? `Moves to your ${file}.` : null;
  const input = "h-8 w-full rounded-md border border-line bg-panel px-2";

  return (
    <div className="flex flex-col gap-2 text-[13px]">
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        {pinned ? (
          <Field label="Single row" full>
            <span className="num flex min-h-8 items-center">
              {pin ? <>{formatDay(pin.date)} · {formatAmount(pin.amount, pin.currency)}{pin.text && <span className="ml-2 break-all text-ink-3">{pin.text}</span>}</>
                : <span className="text-ink-3">A row that is not in your record</span>}
            </span>
          </Field>
        ) : (
          <>
            <Field label="Matches on">
              <Select label={`Tier for ${what}`} placeholder="Matches on" value={draft.match_on} onChange={(v) => set({ match_on: v })} className="w-full"
                options={tiers.filter((t) => t !== "key" || draft.match_on === "key").map((t) => ({ value: t, label: tierLabel(t) }))} />
            </Field>
            <Field label={`Text to match, in ${TIER_TESTS[draft.match_on] ?? draft.match_on}`} wide>
              <input value={draft.pattern} aria-label={`Pattern for ${what}`} placeholder="KOPITIAM*"
                onChange={(e) => set({ pattern: e.target.value })} className={input} />
            </Field>
          </>
        )}
        <Field label="Category">
          <Combobox label={`Category for ${what}`} value={draft.category} options={categories}
            placeholder="Choose a category" onChange={(category) => set({ category })} />
        </Field>
        <Field label="Payee">
          <input value={draft.payee} aria-label={`Payee for ${what}`} placeholder="None"
            onChange={(e) => set({ payee: e.target.value })} className={input} />
        </Field>
        <Field label="Kind">
          <Select label={`Kind for ${what}`} placeholder="Kind" value={draft.kind} disabled={!draft.payee.trim()}
            onChange={(v) => set({ kind: v })} className="w-full" options={kinds.map((k) => ({ value: k, label: k }))} />
        </Field>
        <Field label="Country">
          <input value={draft.country} aria-label={`Country for ${what}`} placeholder="SGP" maxLength={3}
            onChange={(e) => set({ country: e.target.value.toUpperCase() })} className={input} />
        </Field>
        <label className="flex items-center gap-2 self-end pb-1.5">
          <input type="checkbox" checked={draft.recurring} onChange={(e) => set({ recurring: e.target.checked })} />
          Recurring
        </label>
        <Field label="Notes" full>
          <input value={draft.notes} aria-label={`Notes for ${what}`} onChange={(e) => set({ notes: e.target.value })} className={input} />
        </Field>
      </div>

      <div className="rounded-lg bg-tile px-3 py-2 empty:hidden [&>:first-child]:mt-0" aria-live="polite">
        {where && <p className="m-0 text-ink-2">{where}</p>}
        {ready && !p && preview.loading && <p className="m-0 mt-1 text-ink-3">Checking…</p>}
        {ready && preview.error && <p className="m-0 mt-1 text-warn">{preview.error}</p>}
        {p?.error && <p className="m-0 mt-1 text-warn" role="alert">{p.error}</p>}
        {p && !p.error && (
          <div className={`mt-2 flex flex-col gap-2 ${current ? "" : "opacity-60"}`}>
            <div className="flex flex-wrap items-center gap-2">
              <StatusChip tone={p.changes ? "info" : "neutral"}>{p.changes ? `Will change ${plural(p.changes, "row")}` : "No change"}</StatusChip>
              {!p.changes && (
                <span className="num text-ink-2">
                  {p.matches === 0 ? "No row has this text yet." : p.already ? `${plural(p.already, "matching row")} already set this way.` : ""}
                </span>
              )}
            </div>
            {p.beaten_by!.length > 0 && (
              <p className="m-0 text-warn">
                {p.wins ? "Earlier rules match the rest first" : "Earlier rules match them first"}:{" "}
                {p.beaten_by!.map((b) => `${tierLabel(b.tier)} ${b.pattern} in ${b.file} (${plural(b.rows, "row")})`).join("; ")}.
              </p>
            )}
            {p.sample!.length > 0 && (
              <div className="overflow-x-auto">
                <table className="num w-full border-collapse text-[12px]">
                  <tbody>
                    {p.sample!.map((s) => (
                      <tr key={s.key} className="border-t border-line">
                        <td className="whitespace-nowrap py-1 pr-3 text-ink-2">{formatDay(s.date)}</td>
                        <td className="max-w-64 truncate py-1 pr-3" title={s.text}>{s.text || "(no text)"}</td>
                        <td className="whitespace-nowrap py-1 pr-3 text-right">{formatAmount(s.amount, s.currency)}</td>
                        <td className="py-1 text-ink-2">{outcome(s.before)} → <span className="text-ink">{outcome(s.after)}</span></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {p.changes! > p.sample!.length && <p className="m-0 text-ink-3">and {plural(p.changes! - p.sample!.length, "more")}.</p>}
              </div>
            )}
          </div>
        )}
      </div>

      {error && <p className="m-0 text-warn" role="alert">{error}</p>}
      <div className="flex gap-2">
        <Button variant="primary" onClick={() => void save()} disabled={saving || !ready || !!p?.error}>
          {saving ? "Saving…" : rule?.shared ? "Save as my rule" : "Save rule"}
        </Button>
        <Button onClick={onCancel}>Cancel</Button>
      </div>
    </div>
  );
}

function Field({ label, children, wide = false, full = false }: { label: string; children: React.ReactNode; wide?: boolean; full?: boolean }) {
  return (
    <label className={`flex min-w-0 flex-col gap-1 ${full ? "col-span-2 sm:col-span-4" : wide ? "col-span-2" : ""}`}>
      <span className="text-[12px] text-ink-2">{label}</span>
      {children}
    </label>
  );
}
