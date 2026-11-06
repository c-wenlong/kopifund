// Audit: the app's few word helpers, one copy each. A count is written with
// its thousands grouped ("1,500 rows", as every figure in the app is) and the
// word that agrees with it; a server's lower-case phrase opens a sentence with
// a capital.

/** "1 row", "1,500 rows"; `many` for a plural that is not one plus "s". */
export const plural = (n: number, one: string, many = `${one}s`): string =>
  `${n.toLocaleString("en-SG")} ${n === 1 ? one : many}`;

/** "checked on a real statement" as "Checked on a real statement". */
export const sentence = (s: string): string => s.charAt(0).toUpperCase() + s.slice(1);
