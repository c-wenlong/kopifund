// G22: the AI card on Settings: the provider, the two models, the API key,
// Test, and the copilot's consent switch. The page never gets the key back:
// it sees where the key is kept and its last four characters. A key typed here
// leaves the page's state as it is sent.
import { useEffect, useRef, useState } from "react";
import { postJSON, useApi } from "../api";
import { Button } from "../components/Button";
import { Card } from "../components/Card";
import { clearModelCache, ModelField } from "../components/ModelField";
import { SaveBar } from "../components/SaveBar";
import { useAction } from "../components/useAction";
import { Select } from "../components/Select";
import { useUnsavedGuard } from "../router";
import type { AiKey, AiSettings, AiTest } from "../types.gen";

// G22: a provider is one of `providers`, or a custom one config.toml names (shown by its name, and set up
// there: its model and key are config.toml's, so the card offers no model field or key box for it)
type Draft = { provider: string | null; model: string; copilot_model: string; send_rows: boolean };
const KEYS: (keyof Draft)[] = ["provider", "model", "copilot_model", "send_rows"];
// G22: AI turned off in Settings reads as Off, whatever provider config.toml names
const savedOf = (a: AiSettings): Draft => ({
  provider: a.enabled ? a.provider : null, model: a.model, copilot_model: a.copilot_model, send_rows: a.send_rows,
});
// the provider list's first choice: no provider, so neither payee suggestions nor Ask Kopifund asks anything
const OFF = "off";

/** Where the key was found, in a few words. */
export function keyPlace(key: AiKey): string | null {
  switch (key.where) {
    case "keychain": return `Saved in your Keychain · ends in ${key.last4 ?? ""}`;
    case "env_file": return `Saved in the data folder's .env · ends in ${key.last4 ?? ""}`;
    case "environment": case "key_file": case "key_command": return "Found in your environment";
    default: return null;
  }
}

export function AiCard() {
  const ai = useApi<AiSettings>("/api/ai");
  const { error } = ai;
  // an answer without the providers (an older server, a test's stand-in) is no answer to draw
  const data = Array.isArray(ai.data?.providers) ? ai.data : null;
  const [draft, setDraft] = useState<Partial<Draft> | null>(null);
  const [saving, setSaving] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  // a save on its way: what is typed meanwhile is an edit, even back to the old saved value
  const inFlight = useRef(false);
  const latest = useRef(ai.data);
  latest.current = ai.data;
  // what the last save sent, standing for the saved values from when it is sent until the settings are read
  // again after it lands: an edit is measured against what is being, or has just been, saved
  // (`landedOn` is the answer on screen when the save landed: any newer one was read after it)
  const [sending, setSending] = useState<{ sent: Draft; landed: boolean; landedOn?: unknown } | null>(null);
  const fromServer = data ? savedOf(data) : null;
  const standing = !!sending && (!sending.landed || sending.landedOn === ai.data);
  const saved = fromServer && standing ? { ...fromServer, ...sending.sent } : fromServer;
  const d = { ...saved, ...draft } as Draft;
  const dirty = !!saved && KEYS.some((k) => d[k] !== saved[k]);
  // a save on its way holds the page until it lands: one that fails leaves the edit unsaved
  useUnsavedGuard(dirty || (!!sending && !sending.landed));

  // the panel's "Open Settings" lands on this card
  const shown = !!data;
  useEffect(() => {
    if (shown && window.location.hash === "#ai") document.getElementById("ai")?.scrollIntoView?.({ block: "start" });
  }, [shown]);

  if (!data || !saved) {
    return error ? <Card title="AI" id="ai"><p className="m-0 text-[13px] text-ink-3">{error}</p></Card> : null;
  }
  const info = data.providers.find((p) => p.name === d.provider);
  const custom = !!d.provider && !info;
  // the server knows where requests to the provider in use really go (config.toml's base_url included)
  const local = d.provider === data.provider ? data.local : !!info?.local;
  const label = info?.label ?? d.provider ?? "the provider";
  // G22: config.toml's custom provider stays on the list, AI turned off included, so it can be picked again
  const customs = [...new Set([data.config_provider, data.provider])]
    .filter((p): p is string => !!p && !data.providers.some((b) => b.name === p));
  // G22: the saved provider's fields show the models requests really use (a saved choice, config.toml or a
  // default), so an inherited one is visible; another provider picked here shows its defaults until saved
  const inUse = !!d.provider && d.provider === saved.provider && d.model === saved.model;
  const needsModel = inUse && !custom && !data.copilot_model_in_use && !d.copilot_model.trim() && !d.model.trim();
  const set = (patch: Partial<Draft>) => {
    setDraft((now) => {
      const next = { ...now, ...patch };
      // a value set back to what is saved is no longer an edit, unless that value is about to change
      if (!inFlight.current) for (const k of KEYS) if (k in next && next[k] === saved[k]) delete next[k];
      return Object.keys(next).length ? next : null;
    });
    setProblem(null);
  };

  async function save() {
    setSaving(true);
    inFlight.current = true;
    const sent = { ...d };
    setSending({ sent, landed: false });
    try {
      // Off sends only the provider: the models and consent stay saved for the day AI is turned on again
      const fields = { model: d.model.trim(), copilot_model: d.copilot_model.trim(), send_rows: d.send_rows };
      // G22: a custom provider is config.toml's: picking it hands back to config.toml, models and all
      await postJSON<AiSettings>("/api/ai/settings", JSON.stringify(d.provider === null ? { provider: null }
        : custom ? { provider: d.provider, send_rows: d.send_rows } : { provider: d.provider, ...fields }));
      // G18: the fields the save covered are saved; one changed while it was on its way stays an edit
      setDraft((now) => {
        const left = Object.fromEntries(Object.entries(now ?? {}).filter(([k, v]) => v !== sent[k as keyof Draft]));
        return Object.keys(left).length ? left : null;
      });
      setSending((s) => (s?.sent === sent ? { sent, landed: true, landedOn: latest.current } : s));
    } catch (e) {
      setSending((s) => (s?.sent === sent ? null : s));
      // nothing was saved: a box set back to the saved value while the save was on its way is no edit now
      const now = latest.current;
      if (Array.isArray(now?.providers)) {
        const server = savedOf(now);
        setDraft((dr) => {
          const left = Object.fromEntries(Object.entries(dr ?? {}).filter(([k, v]) => v !== server[k as keyof Draft]));
          return Object.keys(left).length ? left : null;
        });
      }
      setProblem((e as Error).message);
    } finally {
      inFlight.current = false;
      setSaving(false);
    }
  }

  return (
    <Card title="AI" id="ai" actions={
      <SaveBar dirty={dirty} saving={saving} onSave={() => void save()} onDiscard={() => { setDraft(null); setProblem(null); }} />}>
      <div className="flex flex-col gap-3">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          <label className="flex min-w-0 flex-col gap-1 text-[12px] text-ink-2">
            Provider
            <Select label="Provider" placeholder="Choose a provider" value={d.provider ?? OFF} className="w-full"
              options={[{ value: OFF, label: "Off" }, ...data.providers.map((p) => ({ value: p.name, label: p.label })),
                // a custom provider from config.toml is listed by its own name
                ...customs.map((p) => ({ value: p, label: p }))]}
              // another provider's models mean nothing here: both go back to its default (Off and a custom
              // provider send none, so theirs stay as saved)
              onChange={(v) => (v === OFF || customs.includes(v)
                ? set({ provider: v === OFF ? null : v, model: saved.model, copilot_model: saved.copilot_model })
                : set({ provider: v, model: "", copilot_model: "" }))} />
          </label>
          {d.provider && !custom && (
            <>
              <label className="flex min-w-0 flex-col gap-1 text-[12px] text-ink-2">
                Payee suggestions model
                <ModelField label="Payee suggestions model" provider={d.provider} value={d.model}
                  placeholder={inUse ? data.model_in_use || "None yet" : info?.default_model || "Provider default"} onChange={(v) => set({ model: v })} />
              </label>
              <label className="flex min-w-0 flex-col gap-1 text-[12px] text-ink-2">
                Ask Kopifund model
                <ModelField label="Ask Kopifund model" provider={d.provider} value={d.copilot_model}
                  placeholder={inUse ? data.copilot_model_in_use || "None yet"
                    : info?.copilot_default_model || d.model || info?.default_model || "Provider default"}
                  onChange={(v) => set({ copilot_model: v })} />
              </label>
            </>
          )}
        </div>
        {custom && (
          <p className="m-0 text-[13px] text-ink-3">
            Set in config.toml{d.provider === data.provider && data.model_in_use ? `: model ${data.model_in_use}` : ""}
          </p>
        )}
        {d.provider && !custom && !local && (
          <KeyRow provider={d.provider} keyInfo={d.provider === data.provider ? data.key : null} />
        )}
        {d.provider && !local && (
          <div className="flex flex-col gap-0.5">
            <label className="flex cursor-pointer items-center gap-2 text-[13px]">
              <input type="checkbox" checked={d.send_rows} onChange={(e) => set({ send_rows: e.target.checked })} />
              Send matching rows to {label}
            </label>
            <p className="m-0 pl-6 text-[12px] text-ink-3">
              Dates, amounts, payees and categories of the rows a question needs; never account numbers or memo lines.
            </p>
          </div>
        )}
        {needsModel && <p role="alert" className="m-0 text-[13px] text-warn">Ask Kopifund needs a model: type one above.</p>}
        {data.enabled && data.provider && <TestRow />}
        {problem && <p role="alert" className="m-0 text-[13px] text-danger">{problem}</p>}
      </div>
    </Card>
  );
}

/** The API key: where it is kept, with Replace and Remove; or a box to type one. `keyInfo` is null for a provider not saved yet. */
function KeyRow({ provider, keyInfo }: { provider: string; keyInfo: AiKey | null }) {
  const has = !!keyInfo && keyInfo.where !== "none";
  const [typing, setTyping] = useState(false);
  const [text, setText] = useState("");
  const { running: busy, error: problem, run, clear } = useAction();
  const [armed, setArmed] = useState(false);
  const disarm = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => {
    if (disarm.current) clearTimeout(disarm.current);
  }, []);
  // another provider's key is another matter
  const [seen, setSeen] = useState(provider);
  if (seen !== provider) {
    setSeen(provider);
    setTyping(false);
    setText("");
    setArmed(false);
    clear();
  }

  async function saveKey() {
    const key = text.trim();
    if (!key) return;
    // the key leaves the page's state before it is sent, whatever the answer
    setText("");
    await run("save", async () => {
      await postJSON<AiSettings>("/api/ai/key", JSON.stringify({ provider, key }));
      setTyping(false);
      // the models list the server can fetch changes with the key
      clearModelCache(provider);
    });
  }

  async function remove() {
    // the first press asks; a second within a few seconds removes
    if (!armed) {
      setArmed(true);
      if (disarm.current) clearTimeout(disarm.current);
      disarm.current = setTimeout(() => setArmed(false), 4000);
      return;
    }
    if (disarm.current) clearTimeout(disarm.current);
    setArmed(false);
    await run("remove", async () => {
      await postJSON<AiSettings>("/api/ai/key/remove", JSON.stringify({ provider }));
      clearModelCache(provider);
    });
  }

  return (
    <div className="flex flex-col gap-1">
      <span className="text-[12px] text-ink-2">API key</span>
      {has && !typing ? (
        <div className="flex flex-wrap items-center gap-2">
          <span className="num min-w-0 flex-1 text-[13px]">{keyPlace(keyInfo!)}</span>
          <Button onClick={() => { setTyping(true); setArmed(false); }} disabled={busy}>Replace</Button>
          {keyInfo!.can_remove && (
            <Button onClick={() => void remove()} disabled={busy} onBlur={() => setArmed(false)}>
              {armed ? "Press again to remove" : "Remove"}
            </Button>
          )}
        </div>
      ) : (
        <form className="flex flex-wrap items-center gap-2" onSubmit={(e) => { e.preventDefault(); void saveKey(); }}>
          <input type="password" aria-label="API key" value={text} onChange={(e) => setText(e.target.value)}
            autoComplete="off" spellCheck={false} autoFocus={typing}
            className="h-8 min-w-0 flex-1 rounded-md border border-line bg-panel px-2 text-[13px]" />
          <Button type="submit" variant="primary" disabled={busy || !text.trim()}>Save key</Button>
          {has && <Button onClick={() => { setTyping(false); setText(""); }} disabled={busy}>Cancel</Button>}
        </form>
      )}
      {problem && <p role="alert" className="m-0 text-[12px] text-danger">{problem}</p>}
    </div>
  );
}

function TestRow() {
  const [answer, setAnswer] = useState<AiTest | null>(null);
  const { running: busy, error, run: act } = useAction();
  const run = () => {
    setAnswer(null);
    return act("test", async () => setAnswer(await postJSON<AiTest>("/api/ai/test", JSON.stringify({}))));
  };
  const result: AiTest | null = error !== null ? { ok: false, message: error, ms: null } : answer;
  return (
    <div className="flex flex-wrap items-center gap-3">
      <Button onClick={() => void run()} disabled={busy}>{busy ? "Testing" : "Test"}</Button>
      {result && (
        <span role="status" className={`text-[13px] ${result.ok ? "text-good" : "text-danger"}`}>
          {result.ok ? "Answered" : "Failed"}: {result.message}
          {result.ms != null && <span className="num"> · {result.ms.toLocaleString("en-SG")} ms</span>}
        </span>
      )}
    </div>
  );
}
