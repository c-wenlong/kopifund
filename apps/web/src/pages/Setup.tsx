import { useState } from "react";
import { Check } from "lucide-react";
import { postJSON, type Provider, type SetupState } from "../api";
import type { Bank } from "../accounts";
import { BankMark } from "../components/BankMark";
import { Button } from "../components/Button";
import { Card } from "../components/Card";
import { useAction } from "../components/useAction";
import { sentence } from "../words";

// G13: a wallet is one tile with a chip for each currency; every other provider is a tile of its own
// G21: each provider brings its bank's mark and its account's name from the layout registry, and its
// status: whether a real statement has checked the layout or it comes from a public sample
type Tile = { key: string; title: string; bank: Bank; providers: Provider[]; wallets: boolean };


function tiles(providers: Provider[]): Tile[] {
  const out: Tile[] = [];
  for (const p of providers) {
    if (p.wallet) {
      const found = out.find((t) => t.key === p.parser);
      if (found) { found.providers.push(p); continue; }
      out.push({ key: p.parser, title: p.title || p.parser, bank: p.bank, providers: [p], wallets: true });
    } else {
      out.push({ key: p.id, title: p.label, bank: p.bank, providers: [p], wallets: false });
    }
  }
  return out;
}

// the box is hidden and its label is the tile, so the whole tile takes the click and a keyboard ring shows on it
const CHECK = "sr-only";
const FRAME = "rounded-lg border border-line bg-panel px-3 py-2.5 has-[:checked]:border-ink has-[:checked]:bg-tile has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-[var(--focus)]";

/** First run: pick the banks and wallets you hold; Kopifund makes the data folder. */
export function Setup({ state, onDone }: { state: SetupState; onDone: () => void }) {
  const [chosen, setChosen] = useState<Set<string>>(new Set());
  const [empty, setEmpty] = useState(false);
  const action = useAction();
  const busy = action.running;
  const error = empty ? "Choose at least one bank, wallet or statement." : action.error;

  const pick = (id: string, on: boolean) => {
    const next = new Set(chosen);
    if (on) next.add(id); else next.delete(id);
    setChosen(next);
    setEmpty(false);
    action.clear();
  };

  async function create() {
    if (!chosen.size) {
      setEmpty(true);
      return;
    }
    await action.run("create", async () => {
      await postJSON("/api/setup", JSON.stringify({ providers: [...chosen] }));
      onDone();
    });
  }

  return (
    // G13: Setup draws outside the shell, so it keeps clear of the notch itself
    <div className="safe-area">
    <div className="mx-auto flex max-w-2xl flex-col gap-3 px-4 py-8 md:px-0">
      <h1 className="m-0 text-[22px] font-semibold tracking-tight">Welcome to Kopifund</h1>
      <div>
        <p className="m-0 text-[14px] text-ink-2">
          Kopifund reads the PDF statements your banks and wallets already send you. Everything stays on this Mac. Pick what you
          hold; you can add more later.
        </p>
        <p className="m-0 mt-1 text-[12px] text-ink-3">Data folder: <span className="break-all">{state.data_folder}</span></p>
      </div>
      <Card title="Your banks and wallets" actions={
        <span className="num text-[13px] text-ink-2" role="status">{chosen.size ? `${chosen.size} selected` : "None selected"}</span>}>
        <fieldset className="m-0 grid grid-cols-1 gap-2 border-0 p-0 sm:grid-cols-2">
          <legend className="sr-only">Providers</legend>
          {tiles(state.providers).map((t) => t.wallets ? (
            <div key={t.key} className={`${FRAME} ${t.providers.some((p) => chosen.has(p.id)) ? "border-ink bg-tile" : ""}`}>
              <div className="flex items-center gap-2 text-[14px]">
                <BankMark bank={t.bank} />
                <span className="min-w-0">
                  <span className="block font-medium">{t.title}</span>
                  <span className="block text-[12px] text-ink-3">{sentence(t.providers[0].status)}</span>
                </span>
              </div>
              <div role="group" aria-label={`${t.title} currencies`} className="mt-2 flex flex-wrap gap-x-1.5 gap-y-3">
                {t.providers.map((p) => (
                  <label key={p.id} className="relative cursor-pointer rounded-full after:absolute after:inset-x-0 after:-inset-y-[7px] after:content-[''] border border-line bg-panel px-2.5 py-0.5 text-[12px] has-[:checked]:border-ink has-[:checked]:bg-ink has-[:checked]:text-page has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-[var(--focus)]">
                    <input type="checkbox" className={CHECK} aria-label={p.label} checked={chosen.has(p.id)}
                      onChange={(e) => pick(p.id, e.target.checked)} />
                    {p.wallet}
                  </label>
                ))}
              </div>
            </div>
          ) : (
            <label key={t.key} className={`flex cursor-pointer items-center gap-2 text-[14px] hover:bg-hover ${FRAME}`}>
              <input type="checkbox" className={CHECK} aria-label={t.title} checked={chosen.has(t.providers[0].id)}
                onChange={(e) => pick(t.providers[0].id, e.target.checked)} />
              <BankMark bank={t.bank} />
              <span className="min-w-0 flex-1">
                <span className="block">{t.title}</span>
                <span className="block text-[12px] text-ink-3">{sentence(t.providers[0].status)}</span>
              </span>
              {chosen.has(t.providers[0].id) && <Check size={16} aria-hidden className="shrink-0" />}
            </label>
          ))}
        </fieldset>
        {error && <p className="m-0 mt-2 text-[13px] text-warn" role="alert">{error}</p>}
        <Button variant="primary" onClick={create} disabled={busy || chosen.size === 0} className="mt-3">
          {busy ? "Setting up…" : chosen.size ? "Create my data folder" : "Choose at least one"}
        </Button>
      </Card>
    </div>
    </div>
  );
}
