// Money is integer cents plus a currency code. Formatting is the only place it
// becomes a decimal, and only for display.
export type Cents = number;

/** The sign of a negative amount: a true minus (U+2212), not a hyphen. It is a prefix character, so a line never breaks between it and the amount. */
export const MINUS = "−";

// One grouping for every currency: 1,234 and 1,234.05
const whole = new Intl.NumberFormat("en-SG", { minimumFractionDigits: 0, maximumFractionDigits: 0 });
const cents2 = new Intl.NumberFormat("en-SG", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

/**
 * Audit: money must be integer cents, and a value that is not is a bug in what
 * sent it. These formatters run while a page draws, where a throw blanked the
 * app, so they say so in the console and carry on: a float is rounded to whole
 * cents, and what is not a number at all comes back null (shown as "?").
 */
function wholeCents(cents: Cents): Cents | null {
  if (Number.isInteger(cents)) return cents;
  console.error(`money must be integer cents, got ${cents}`);
  return typeof cents === "number" && Number.isFinite(cents) ? Math.round(cents) : null;
}

export function formatSgd(amount: Cents, opts: { decimals?: boolean } = {}): string {
  const cents = wholeCents(amount);
  if (cents === null) return "S$?";
  const decimals = opts.decimals ?? false;
  const value = decimals ? cents / 100 : Math.round(cents / 100);
  const text = (decimals ? cents2 : whole).format(Math.abs(value));
  return `${value < 0 ? MINUS : ""}S$${text}`;
}

export function formatPlain(cents: Cents): string {
  return new Intl.NumberFormat("en-SG", { maximumFractionDigits: 0 }).format(Math.round(cents / 100));
}

// An amount in its own currency: S$ for SGD, the code in front for the rest,
// grouped and signed the same way ("SEK 9,412.05", "−SEK 12.00"). The space
// after the code is a no-break space, so the code and the amount stay together.
export function formatAmount(amount: Cents, currency: string): string {
  if (currency === "SGD") return formatSgd(amount, { decimals: true });
  const cents = wholeCents(amount);
  if (cents === null) return `${currency} ?`;
  return `${cents < 0 ? MINUS : ""}${currency} ${cents2.format(Math.abs(cents / 100))}`;
}

// G17: what a money box holds, read as cents and written back. The text is read
// digit by digit, never through a float (Number("1.15") * 100 is 114.99999999999999),
// so what is typed is exactly what is saved. Dollars become cents nowhere else
// in the app: the server sends cents (finance/money.py rounds once, half up).
const BOX = /^(\d*)(?:\.(\d*))?$/;

/**
 * The text of a money box as integer cents: "1,234.5" is 123450, "12." is
 * 1200, ".5" is 50. Commas and spaces are dropped; empty is null. Text that is
 * not dollars and cents (a letter, a sign, a second point, a third decimal
 * that is not zero) is NaN, for the box to say so.
 */
export function parseCents(text: string): Cents | null {
  const t = text.replace(/[,\s]/g, "");
  if (t === "") return null;
  const m = BOX.exec(t);
  if (!m || (m[1] === "" && !m[2])) return NaN;
  const frac = m[2] ?? "";
  if (/[^0]/.test(frac.slice(2))) return NaN;
  const cents = Number(m[1] || "0") * 100 + Number(frac.slice(0, 2).padEnd(2, "0"));
  return Number.isSafeInteger(cents) ? cents : NaN;
}

/** Cents as the plain text a money box shows: "1234" for whole dollars, "1234.50" otherwise; the box groups it. */
export function centsText(amount: Cents): string {
  const cents = wholeCents(amount);
  if (cents === null) return "";
  const a = Math.abs(cents);
  const rest = a % 100;
  return `${cents < 0 ? "-" : ""}${(a - rest) / 100}${rest ? `.${String(rest).padStart(2, "0")}` : ""}`;
}

/** A y-axis tick in cents, compact: "S$500" below a thousand, "S$12k" or "S$1.5k" above, "S$1.2M" from a million, "−S$2k" below zero. */
export function axisSgd(cents: number): string {
  const dollars = Math.abs(cents / 100);
  const sign = cents < 0 && Math.round(dollars) !== 0 ? MINUS : "";
  if (Math.round(dollars) < 1000) return `${sign}S$${Math.round(dollars)}`;
  const [n, unit] = dollars < 1_000_000 ? [dollars / 1000, "k"] : [dollars / 1_000_000, "M"];
  return `${sign}S$${Number(n.toFixed(1))}${unit}`;
}
