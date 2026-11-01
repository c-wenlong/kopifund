// G13: one date format for the whole app. A day reads "3 Oct 2026", a month
// "Oct 2026", a range "3 Oct to 9 Oct 2026" (both ends in full when the years
// differ). Chart axis ticks are the one compact exception ("Oct 26", in
// stats/hint.ts). The API sends ISO dates; only what is shown changes, here.
//
// A transaction's date is the calendar date its statement prints, so an ISO
// date is read from its parts and never through `new Date("2026-10-03")`, which
// is UTC midnight and shows the day before west of Greenwich.

export const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

type Parts = { y: number; m: number; d: number | null };

const ISO = /^(\d{4})-(\d{2})(?:-(\d{2}))?$/;

/** The year, month (1 to 12) and day of "2026-10-03" or "2026-10", or null for anything else. */
export function isoParts(iso: unknown): Parts | null {
  if (typeof iso !== "string") return null;
  const hit = ISO.exec(iso);
  if (!hit) return null;
  const [y, m, d] = [Number(hit[1]), Number(hit[2]), hit[3] ? Number(hit[3]) : null];
  if (m < 1 || m > 12 || (d != null && (d < 1 || d > 31))) return null;
  return { y, m, d };
}

/** "2026-10-03" reads "3 Oct 2026". Anything that is not an ISO day comes back as it was. */
export function formatDay(iso: string): string {
  const p = isoParts(iso);
  return p && p.d != null ? `${p.d} ${MONTHS[p.m - 1]} ${p.y}` : iso;
}

export const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

/** "2026-10-13" reads "Tue 13 Oct 2026". The weekday is worked out from the date's own parts. Anything that is not an ISO day comes back as it was. */
export function formatWeekday(iso: string): string {
  const p = isoParts(iso);
  if (!p || p.d == null) return iso;
  return `${WEEKDAYS[new Date(Date.UTC(p.y, p.m - 1, p.d)).getUTCDay()]} ${formatDay(iso)}`;
}

/** A day without its year, for a phone's narrow column: "2026-10-03" reads "3 Oct". Anything that is not an ISO day comes back as it was. */
export function formatDayShort(iso: string): string {
  const p = isoParts(iso);
  return p && p.d != null ? `${p.d} ${MONTHS[p.m - 1]}` : iso;
}

/**
 * How long before `asOf` an ISO day was, in the nearest whole unit: "today",
 * "5 days ago", "11 months ago", "3 years ago". A day after `asOf` reads as
 * "today". Anything that is not an ISO day comes back empty.
 */
export function ago(iso: string, asOf: string): string {
  const a = isoParts(iso);
  const b = isoParts(asOf);
  if (!a || !b || a.d == null || b.d == null) return "";
  const days = Math.round((Date.UTC(b.y, b.m - 1, b.d) - Date.UTC(a.y, a.m - 1, a.d)) / 86_400_000);
  if (days <= 0) return "today";
  if (days < 30) return days === 1 ? "yesterday" : `${days} days ago`;
  // whole months, the way a person counts them: 1 Feb to 20 Sep is 7, not 8
  const months = (b.y - a.y) * 12 + (b.m - a.m) - (b.d < a.d ? 1 : 0);
  if (months < 1) return `${days} days ago`;
  if (months < 24) return months === 1 ? "1 month ago" : `${months} months ago`;
  return `${Math.floor(months / 12)} years ago`;
}

/** "2026-10" (or a day in it) reads "Oct 2026". Anything else comes back as it was. */
export function formatMonth(iso: string): string {
  const p = isoParts(iso);
  return p ? `${MONTHS[p.m - 1]} ${p.y}` : iso;
}

/** An ISO day or month in its display form; any other value as text. For table cells that may hold either. */
export function formatDate(v: unknown): string {
  const p = isoParts(v);
  if (!p) return v == null ? "" : String(v);
  return p.d != null ? formatDay(v as string) : formatMonth(v as string);
}

/**
 * Two ISO days or two ISO months as a range: "3 Oct to 9 Oct 2026",
 * "Oct to Dec 2026", or both ends in full across a year ("28 Dec 2025 to
 * 3 Jan 2026"). The same day twice is just that day.
 */
export function formatRange(from: string, to: string): string {
  const a = isoParts(from);
  const b = isoParts(to);
  if (!a || !b || (a.d == null) !== (b.d == null)) return `${formatDate(from)} to ${formatDate(to)}`;
  if (from === to) return formatDate(from);
  if (a.y !== b.y) return `${formatDate(from)} to ${formatDate(to)}`;
  const head = a.d != null ? `${a.d} ${MONTHS[a.m - 1]}` : MONTHS[a.m - 1];
  return `${head} to ${formatDate(to)}`;
}

const pad = (n: number) => String(n).padStart(2, "0");

/** The calendar date of a moment on this device, as ISO: "2026-10-03". */
export function isoDay(d: Date = new Date()): string {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

/** The month of a moment on this device, as ISO: "2026-10". */
export function isoMonth(d: Date = new Date()): string {
  return isoDay(d).slice(0, 7);
}

/** An ISO day as a Date at local midnight, built from its parts, or null for anything else. */
export function toLocalDate(iso: string): Date | null {
  const p = isoParts(iso);
  return p ? new Date(p.y, p.m - 1, p.d ?? 1) : null;
}

/** The ISO day `n` days after (or before, when negative) an ISO day; null for anything else. */
export function addDays(iso: string, n: number): string | null {
  const d = toLocalDate(iso);
  if (!d || isoParts(iso)?.d == null) return null;
  d.setDate(d.getDate() + n);
  return isoDay(d);
}

/** The ISO month `n` months after (or before) an ISO month or day; null for anything else. */
export function shiftMonth(iso: string, n: number): string | null {
  const p = isoParts(iso);
  if (!p) return null;
  const i = p.y * 12 + (p.m - 1) + n;
  return `${String(Math.floor(i / 12)).padStart(4, "0")}-${pad((i % 12) + 1)}`;
}

// ---- reading what a person types

const MONTH_WORDS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"];

/** 1 to 12 for "oct", "Sept" or "October" (three letters at least), else null. */
function monthNumber(word: string): number | null {
  const w = word.toLowerCase();
  if (w.length < 3) return null;
  const i = MONTH_WORDS.findIndex((m) => m.startsWith(w));
  return i >= 0 ? i + 1 : null;
}

function realDay(y: number, m: number, d: number): string | null {
  if (y < 1000) return null;
  const t = new Date(y, m - 1, d);
  if (t.getFullYear() !== y || t.getMonth() !== m - 1 || t.getDate() !== d) return null;
  return `${y}-${pad(m)}-${pad(d)}`;
}

const tidy = (text: string) => text.trim().toLowerCase().replace(/,/g, " ").replace(/\s+/g, " ");

/**
 * A day as a person types it, to ISO. "3 Oct 2026", "3 october 2026", "Oct 3
 * 2026", "3-Oct-2026" and "2026-10-03" read as they say; "3/10/2026" is day
 * first, as it is written in Singapore. A year is always four digits, so
 * "3/10/26" is not guessed at. A date that does not exist ("31 Feb 2026") is
 * null, like any text that is not a date.
 */
export function parseDay(text: string): string | null {
  const t = tidy(text);
  let m: RegExpExecArray | null;
  if ((m = /^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$/.exec(t))) return realDay(+m[1], +m[2], +m[3]);
  if ((m = /^(\d{1,2})[-/. ](\d{1,2})[-/. ](\d{4})$/.exec(t))) return realDay(+m[3], +m[2], +m[1]);
  if ((m = /^(\d{1,2})(?:st|nd|rd|th)?[-/. ]?([a-z]{3,9})\.?[-/. ]?(\d{4})$/.exec(t))) {
    const mo = monthNumber(m[2]);
    return mo ? realDay(+m[3], mo, +m[1]) : null;
  }
  if ((m = /^([a-z]{3,9})\.? (\d{1,2})(?:st|nd|rd|th)? (\d{4})$/.exec(t))) {
    const mo = monthNumber(m[1]);
    return mo ? realDay(+m[3], mo, +m[2]) : null;
  }
  return null;
}

/** A month as a person types it, to ISO: "Oct 2026", "october 2026", "2026-10", "10/2026". */
export function parseMonth(text: string): string | null {
  const t = tidy(text);
  const build = (y: number, mo: number) => (y >= 1000 && mo >= 1 && mo <= 12 ? `${y}-${pad(mo)}` : null);
  let m: RegExpExecArray | null;
  if ((m = /^(\d{4})[-/.](\d{1,2})$/.exec(t))) return build(+m[1], +m[2]);
  if ((m = /^(\d{1,2})[-/. ](\d{4})$/.exec(t))) return build(+m[2], +m[1]);
  if ((m = /^([a-z]{3,9})\.?[-/. ]?(\d{4})$/.exec(t))) {
    const mo = monthNumber(m[1]);
    return mo ? build(+m[2], mo) : null;
  }
  if ((m = /^(\d{4})[-/. ]?([a-z]{3,9})$/.exec(t))) {
    const mo = monthNumber(m[2]);
    return mo ? build(+m[1], mo) : null;
  }
  return null;
}

/**
 * A moment the server stamped ("2026-10-03T06:05:00+00:00") in this device's
 * time: "3 Oct 2026, 14:05". A stamp is an instant, unlike a statement's date,
 * so it is converted. Text that is not a time comes back as it was.
 */
export function formatDateTime(stamp: string): string {
  const t = moment(stamp);
  return t ? `${formatDay(isoDay(t))}, ${clock(t)}` : stamp;
}

/** Only the time of a stamp, on a 24-hour clock with seconds: "14:05:09". */
export function formatTime(stamp: string): string {
  const t = moment(stamp);
  return t ? `${clock(t)}:${pad(t.getSeconds())}` : stamp;
}

/** G18: a stamp's fraction of a second cut to milliseconds, the most a browser is bound to read (the server keeps microseconds). */
export function toMillis(stamp: string): string {
  return stamp.replace(/(\.\d{3})\d+/, "$1");
}

function moment(stamp: string): Date | null {
  if (!/^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}/.test(stamp)) return null;
  const t = new Date(toMillis(stamp));
  return Number.isNaN(t.getTime()) ? null : t;
}

const clock = (t: Date) => `${pad(t.getHours())}:${pad(t.getMinutes())}`;
