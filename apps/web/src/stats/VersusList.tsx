import { CollapsibleRows, COLLAPSED_ROWS } from "../components/CollapsibleRows";
// G13: a stat's figures against typical ones, as a short list rather than a
// grouped bar chart. Each row has its own scale, so months of cash and a
// savings percentage sit beside money without a 6 vanishing next to a 2,000.
// Everything it shows is in the row: `thin` and `concern` are the stat's own
// flags, never read out of the findings' wording.
import { fmt, isNum, normaliseUnit, type Plan, type Row } from "./hint";
import { CENTS_UNIT, MINOR } from "./money";

type VersusPlan = Extract<Plan, { kind: "versus" }>;

/**
 * G20: one side of a row, with the unit to write it in. Money in cents is
 * under `<name>_minor` and is written as `sgd_minor` whatever the row's unit
 * says ("SGD per month" names what it measures); anything else is under the
 * bare name, in the row's unit (months, a percentage), and is never money.
 */
export function versusSide(row: Row, name: string, unitField: string): { v: unknown; unit: unknown } {
  return name + MINOR in row ? { v: row[name + MINOR], unit: CENTS_UNIT } : { v: row[name], unit: row[unitField] };
}

/**
 * One figure in its unit: money in cents as whole dollars, a percent rounded,
 * months to one place. A bare figure in a row whose text says SGD is no amount
 * (a stat's money is under `<name>_minor`), so it is written as a plain number.
 */
export function versusFigure(v: unknown, unit: unknown): string {
  if (!isNum(v)) return "";
  if (unit === CENTS_UNIT) return fmt(v, CENTS_UNIT);
  const u = normaliseUnit(unit);
  if (u === "percent") return `${Math.round(v)}%`;
  if (u === "fraction") return `${Math.round(v * 100)}%`;
  if (u === "months") return v.toFixed(1);
  return fmt(v, u ?? "number");
}

/**
 * Where the fill ends and the tick sits, as percentages of the track. The
 * scale is the larger of the two, so the longer one fills the track; a
 * negative figure draws nothing rather than running backwards.
 */
export function versusScale(owner: unknown, typical: unknown): { fill: number; tick: number | null } {
  const a = isNum(owner) ? Math.max(0, owner) : 0;
  const b = isNum(typical) ? Math.max(0, typical) : null;
  const top = Math.max(a, b ?? 0);
  if (top <= 0) return { fill: 0, tick: b === null ? null : 0 };
  return { fill: (a / top) * 100, tick: b === null ? null : (b / top) * 100 };
}

const text = (v: unknown) => (typeof v === "string" && v.trim() ? v.trim() : "");

/** The hover text: where the typical figure comes from, and why a thin row is greyed. */
function hover(row: Row, name: string): string {
  const from = [text(row.source), text(row.as_of)].filter(Boolean).join(", ");
  const who = text(row.population);
  const thin = row.thin && text(row.thin_reason) ? `${name} ${text(row.thin_reason)}.` : "";
  return [thin, from ? `Typical: ${from}.` : "", who ? `Population: ${who}.` : ""].filter(Boolean).join(" ");
}

export function VersusList({ p }: { p: VersusPlan }) {
  const money = p.rows.some((r) => !r.thin && versusSide(r, p.owner, p.unitField).unit === CENTS_UNIT);
  return (
    <div>
      <ul className="m-0 flex list-none flex-col gap-3 p-0">
        <CollapsibleRows rows={p.rows} limit={COLLAPSED_ROWS.mid} keyOf={(row, i) => `${String(row[p.xKey] ?? "")}-${i}`} render={(row) => {
          const name = String(row[p.xKey] ?? "");
          const owner = versusSide(row, p.owner, p.unitField);
          const typical = versusSide(row, p.typical, p.unitField);
          const thin = row.thin === true;
          const concern = row.concern === true && !thin;
          // both sides are on one scale (the stat holds cents against cents), so the bar's proportions are the same
          const { fill, tick } = versusScale(owner.v, typical.v);
          return (
            <li title={hover(row, name)} data-thin={thin || undefined}
              className={`text-[13px] ${thin ? "text-ink-3" : "text-ink"}`}>
              <div className="flex items-baseline justify-between gap-3">
                <span className={thin ? "" : "text-ink-2"}>{name}</span>
                {thin ? (
                  <span className="flex items-baseline gap-2 text-[12px]">
                    {/* its figures stay on show: a native title is no use on a phone or from the keyboard */}
                    <span className="num">{versusFigure(owner.v, owner.unit)} vs {versusFigure(typical.v, typical.unit)}</span>
                    <span>not billed here</span>
                  </span>
                ) : (
                  <span className="num whitespace-nowrap">
                    <span className={`font-semibold ${concern ? "text-danger" : ""}`}>{versusFigure(owner.v, owner.unit)}</span>
                    <span className="text-ink-3"> vs {versusFigure(typical.v, typical.unit)}</span>
                  </span>
                )}
              </div>
              <div className="relative mt-1.5 h-1.5 rounded-full bg-tile" aria-hidden>
                {!thin && (
                  <>
                    <div data-part="fill" className="absolute inset-y-0 left-0 rounded-full"
                      style={{ width: `${fill}%`, background: concern ? "var(--danger)" : "var(--c1)" }} />
                    {tick !== null && (
                      <div data-part="tick" className="absolute -top-1 -bottom-1 w-0.5 -translate-x-1/2 rounded-full bg-ink"
                        style={{ left: `${tick}%` }} />
                    )}
                  </>
                )}
              </div>
            </li>
          );
        }} />
      </ul>
      <p className="m-0 mt-3 text-[12px] text-ink-3">Line = typical.{money ? " SGD a month." : ""}</p>
    </div>
  );
}
