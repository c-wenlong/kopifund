import type { Folder } from "./api";
import type { Bank } from "./types.gen";

// The upload picker shows an account once, with its bank's mark and a proper
// name; a wallet account (YouTrip, Wise) then asks for the currency. The
// folder slug in the config (youtrip-sek) is still what the upload sends.
// G21: the name and the mark come with each folder (its `title` and `bank`,
// from the layout registry on the server), so no bank is listed here.

// G17: generated from finance/account_names.py BANKS (finance/types.py)
export type { Bank } from "./types.gen";

export type Account = {
  key: string;                                   // the folder path without its currency: statements/youtrip
  name: string;
  bank: Bank;
  path: string;                                  // the folder, for an account with no wallets
  wallets: { currency: string; path: string }[];
};

export function accounts(folders: Folder[]): Account[] {
  // grouped by the full path less the currency, so only the wallets of one
  // account fold together: statements/joint/dbs-multiplier and
  // statements/dbs-multiplier stay two accounts
  const out = new Map<string, Account>();
  for (const f of folders) {
    const suffix = f.wallet ? `-${f.wallet.toLowerCase()}` : "";
    const cut = (s: string) => (suffix && s.endsWith(suffix) ? s.slice(0, -suffix.length) : s);
    const key = cut(f.path);
    const a = out.get(key) ?? { key, name: f.title, bank: f.bank, path: "", wallets: [] };
    if (f.wallet) a.wallets.push({ currency: f.wallet.toUpperCase(), path: f.path });
    else a.path = f.path;
    out.set(key, a);
  }
  // two accounts with one name get their parent folder after it
  const all = [...out.values()];
  const twice = new Set(all.map((a) => a.name).filter((n, i, names) => names.indexOf(n) !== i));
  for (const a of all) {
    if (twice.has(a.name)) {
      const parent = a.key.split("/").slice(-2, -1)[0];
      if (parent) a.name = `${a.name} (${parent})`;
    }
  }
  for (const a of out.values()) {
    a.wallets.sort((x, y) => (x.currency === "SGD" ? -1 : y.currency === "SGD" ? 1 : x.currency.localeCompare(y.currency)));
  }
  return [...out.values()];
}

// Banks Kopifund shows in the picker before it can read their statements.
// Each is dropped from the list once the config has a folder for it.
export const COMING_SOON: { key: string; name: string; bank: Bank }[] = [
  { key: "coming-soon/trust", name: "Trust Bank", bank: "trust" },
];

export function comingSoon(list: Account[]) {
  return COMING_SOON.filter((c) => !list.some((a) => a.bank === c.bank));
}

/**
 * The account a link names with ?account=: its key (`statements/youtrip`), the
 * folder's own name (`dbs-multiplier`), a wallet folder (`youtrip-sek`, which
 * also names the currency) or its display name, in any case. Null when none
 * matches, so a stale link shows an empty picker, never an error.
 */
export function findAccount(list: Account[], want: string): { account: Account; currency: string } | null {
  const w = want.trim().toLowerCase();
  if (!w) return null;
  for (const a of list) {
    const slug = a.key.split("/").pop() ?? "";
    if ([a.key, slug, a.name].some((x) => x.toLowerCase() === w)) return { account: a, currency: "" };
    const wallet = a.wallets.find((x) => x.path.toLowerCase() === w || (x.path.split("/").pop() ?? "").toLowerCase() === w);
    if (wallet) return { account: a, currency: wallet.currency };
  }
  return null;
}

// An ISO 4217 code starts with its country's ISO 3166 code (SEK: SE, EUR: EU),
// and a flag emoji is that code in regional indicator letters.
export function flag(currency: string) {
  const cc = currency.slice(0, 2).toUpperCase();
  if (!/^[A-Z]{2}$/.test(cc)) return "";
  return String.fromCodePoint(...[...cc].map((c) => 0x1f1e6 + c.charCodeAt(0) - 65));
}
