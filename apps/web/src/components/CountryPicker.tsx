import { useMemo } from "react";
import { X } from "lucide-react";
import type { Country } from "../api";
import { Combobox } from "./Combobox";

// G13: the trip form's countries. A trip stores ISO alpha-3 codes; a person
// picks them by name, from the list the server holds (GET /api/countries,
// finance/countries.py), each with its flag. Only a country from the list can
// be added, and a code a trip already holds that is not in the list (an old or
// mistyped one) is marked and has to be removed before the trip can be saved.

const regionNames = (() => {
  try {
    return typeof Intl.DisplayNames === "function" ? new Intl.DisplayNames(["en"], { type: "region" }) : null;
  } catch {
    return null;
  }
})();

/** A country's name in English, from its alpha-2 region code; its alpha-3 code when the browser has no name for it. */
export function countryName(c: Pick<Country, "code" | "alpha2">): string {
  try {
    return regionNames?.of(c.alpha2) || c.code;
  } catch {
    return c.code;
  }
}

/** The codes in `codes` that the list of countries does not hold; none while the list has not loaded. */
export function unknownCodes(codes: readonly string[], countries: readonly Country[] | null | undefined): string[] {
  if (!countries) return [];
  const known = new Set(countries.map((c) => c.code));
  return codes.filter((c) => !known.has(c));
}

const label = (c: Country) => `${c.flag} ${countryName(c)} (${c.code})`;

export function CountryPicker({ value, countries, onChange }: {
  value: string[];
  /** null while the list is loading */
  countries: Country[] | null;
  onChange: (codes: string[]) => void;
}) {
  const byCode = useMemo(() => new Map((countries ?? []).map((c) => [c.code, c])), [countries]);
  const options = useMemo(() => {
    const rows = (countries ?? []).filter((c) => !value.includes(c.code)).map((c) => ({ label: label(c), name: countryName(c), code: c.code }));
    return rows.sort((a, b) => a.name.localeCompare(b.name));
  }, [countries, value]);
  const unknown = unknownCodes(value, countries);

  return (
    <div role="group" aria-label="Countries" className="flex flex-col gap-1.5">
      <span>Countries</span>
      {value.length > 0 && (
        <ul aria-label="Chosen countries" className="m-0 flex list-none flex-wrap gap-1.5 p-0">
          {value.map((code) => {
            const c = byCode.get(code);
            const bad = !!countries && !c;
            const name = c ? countryName(c) : code;
            return (
              <li key={code} className={`flex items-center gap-1 rounded-full border py-0.5 pl-2 pr-1 text-[13px] ${bad ? "border-warn bg-warn-bg text-warn" : "border-line bg-tile"}`}>
                {c?.flag ? <span aria-hidden>{c.flag}</span> : null}
                <span>{name}</span>
                {c ? <span className="text-ink-3">{code}</span> : null}
                <button type="button" aria-label={`Remove ${name}`} onClick={() => onChange(value.filter((x) => x !== code))}
                  className="grid size-5 place-items-center rounded-full hover:bg-hover"><X size={12} aria-hidden /></button>
              </li>
            );
          })}
        </ul>
      )}
      <Combobox label="Add a country" value="" options={options.map((o) => o.label)} disabled={!countries}
        placeholder={countries ? "Search by name or code, like Japan or JPN" : "Loading countries"} empty="No country matches"
        onChange={(picked) => {
          const hit = options.find((o) => o.label === picked);
          if (hit) onChange([...value, hit.code]);
        }} />
      {unknown.length > 0 && (
        <p role="alert" className="m-0 text-warn">
          {unknown.join(", ")} {unknown.length === 1 ? "is not an ISO country code" : "are not ISO country codes"}. Remove {unknown.length === 1 ? "it" : "them"} to save the trip.
        </p>
      )}
    </div>
  );
}
