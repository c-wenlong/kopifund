// G13: when a suggestion chip shows, and what it says. The figures come from
// the server (finance/suggestions.py); this only decides whether a box needs
// one. A box the owner left empty gets "Seen"; a box they filled gets a chip
// only when it is clearly off the record's figure; one that is close gets none.
import type { IncomeSuggestion } from "./api";
import { MONTHS } from "./dates";
import { formatSgd, parseCents, type Cents } from "./money";

/** How far a typed figure may sit from the record's before the record "clearly disagrees". */
export const CLOSE = 0.15;

export type ChipKind = "seen" | "record";

/** What a box holds, in cents (G17): a box's text read by parseCents, or the cents a plan field holds; null when empty, NaN when the text is not an amount. */
export function typedValue(text: string | number | null | undefined): Cents | null {
  if (text == null) return null;
  if (typeof text === "number") return text;
  return parseCents(text);
}

/**
 * "seen" for an empty (or zero) box, "record" for one that is far from the
 * suggestion, null for nothing to offer: no figure, text that is not a number,
 * or a figure within CLOSE of the record's. Both are cents.
 */
export function chipKind(typed: Cents | null, suggested: Cents | null | undefined): ChipKind | null {
  if (!suggested || suggested <= 0 || (typed != null && Number.isNaN(typed))) return null;
  if (!typed) return "seen";
  return Math.abs(typed - suggested) / suggested > CLOSE ? "record" : null;
}

/** Cents as the whole dollars a box takes, as a chip says them: 262745 as "2627". Cents on a plan figure are noise. */
export const dollarsText = (cents: Cents): string => String(Math.round(cents / 100));

/** "Seen S$210 a month, use" or "Record shows S$210 a month, use". `what` goes after the amount ("gross"). */
export function chipText(kind: ChipKind, cents: Cents, what = ""): string {
  const money = formatSgd(cents);
  return `${kind === "seen" ? "Seen" : "Record shows"} ${money} a month${what ? ` ${what}` : ""}, use`;
}

// ------------------------------------------------------------ income rows

type Row = { name: string; type: string; from: string; to: string; gross: string };
const norm = (s: string) => s.trim().toLowerCase();

// a name stands for another when it is the same, or one holds the other ("Acme" in "Acme, part time")
const sameName = (a: string, b: string) => {
  const [x, y] = [norm(a), norm(b)];
  return x.length >= 3 && y.length >= 3 && (x === y || x.includes(y) || y.includes(x));
};

/**
 * The detected pay each row already stands for, row by row (null for none).
 * A row claims the pay of the same name first, any number of rows to one
 * payer (an employer's internship and then full time). A row that names no
 * payer claims at most one other, of the same kind of pay, running over the
 * same stretch at about the same figure, and a payer already claimed is not
 * claimed again, so one row never hides two payers. The detected pay is still
 * arriving, so a row ended before it began is another job.
 */
export function claims(rows: Row[], list: IncomeSuggestion[]): (IncomeSuggestion | null)[] {
  const out = rows.map((r) => list.find((s) => sameName(r.name, s.name)) ?? null);
  const taken = new Set(out.filter((s) => s));
  rows.forEach((r, i) => {
    const typed = typedValue(r.gross);
    if (out[i] || !typed || Number.isNaN(typed)) return;
    const s = list.find((p) => !taken.has(p) && p.type === r.type && (!r.to || r.to >= p.from)
      && [p.gross, p.take_home].some((c) => Math.abs(typed - c) / c <= CLOSE));
    if (s) {
      out[i] = s;
      taken.add(s);
    }
  });
  return out;
}

/** Whether the row covers the day the pay last arrived: a later row is a plan, and the record says nothing of it yet. */
export const coversLast = (row: Row, s: IncomeSuggestion): boolean => row.from <= s.last && (!row.to || row.to >= s.last);

/** The detected pay no row stands for yet. */
export const unclaimed = (rows: Row[], list: IncomeSuggestion[]): IncomeSuggestion[] => {
  const held = claims(rows, list);
  return list.filter((s) => !held.includes(s));
};

/** A detected pay as the words on its chip. */
export function incomeText(s: IncomeSuggestion, month: string): string {
  const pay = s.basis === "gross"
    ? `about ${formatSgd(s.gross)} a month gross (${formatSgd(s.take_home)} take-home)`
    : s.basis === "take_home" ? `${formatSgd(s.take_home)} a month take-home` : `${formatSgd(s.gross)} a month`;
  const bonus = s.bonus_months && s.bonus_month
    ? `, bonus about ${s.bonus_months} ${s.bonus_months === 1 ? "month" : "months"} in ${MONTHS[s.bonus_month - 1]}` : "";
  return `Add ${s.name}, ${pay}, from ${month}${bonus}`;
}
