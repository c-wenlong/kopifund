// G13: the headline figures a stat's findings used to carry as prose. A card
// whose answer is "is this fine?" shows them as tiles above its chart; every
// other card leaves its figures to the chart and its hover card.
import { formatMonth } from "../dates";
import { Tile, TileGrid, type Tone } from "../components/Tile";
import { fmt, isNum, type StatResult } from "./hint";
import { CENTS_UNIT, centsOf } from "./money";

type Dict = Record<string, unknown>;
const dict = (v: unknown): Dict | null => (v && typeof v === "object" && !Array.isArray(v) ? (v as Dict) : null);
const num = (d: Dict, k: string): number | null => (isNum(d[k]) ? (d[k] as number) : null);
// G20: the way a tile writes a money figure, read in cents from `<name>_minor` (centsOf)
const sgd = (cents: number) => fmt(cents, CENTS_UNIT);
const count = (n: number) => n.toLocaleString("en-SG");
const percent = (fraction: number) => `${Math.round(fraction * 100)}%`;
const tone = (bad: boolean): Tone => (bad ? "warn" : "neutral");

/** The tiles for this stat, or none: a stat without a set draws its chart alone. */
export function StatTiles({ result }: { result: StatResult }) {
  const data = result.data ?? {};
  const tiles = result.id === "data-quality" ? dataQuality(data)
    : result.id === "savings-runway" ? runway(data)
      : result.id === "needs-wants-savings" ? split(data)
        : null;
  if (!tiles) return null;
  return <div className="mb-3"><TileGrid cols={tiles.length === 3 ? 3 : 4} label={`${result.title}, in figures`}>{tiles}</TileGrid></div>;
}

function dataQuality(data: Dict) {
  const c = dict(data.counts);
  if (!c) return null;
  const none = num(c, "uncategorised") ?? 0;
  const noRate = num(c, "unconverted") ?? 0;
  const partial = num(c, "partial_months") ?? 0;
  const out = centsOf(c, "uncategorised_out");
  return [
    <Tile key="none" label="Rows with no category" value={count(none)} tone={tone(none > 0)} noteTone="neutral"
      note={none > 0 && out ? `${sgd(out)} out` : undefined} />,
    <Tile key="synthetic" label="Stand-in rows" value={count(num(c, "synthetic") ?? 0)} />,
    <Tile key="rate" label="Rows with no exchange rate" value={count(noRate)} tone={tone(noRate > 0)} />,
    <Tile key="partial" label="Months missing statements" value={count(partial)} tone={tone(partial > 0)} />,
  ];
}

function runway(data: Dict) {
  const s = dict(data.summary);
  const cash = centsOf(s, "cash");
  if (!s || cash === null) return null;
  const low = num(s, "rule_low");
  const months = (v: number | null) => (v === null ? "None yet" : `${v.toFixed(1)} mo`);
  const six = num(s, "runway_6m");
  const all = num(s, "runway_all");
  const trend = centsOf(s, "trend_6m");
  const rate6 = centsOf(s, "spend_avg_6m");
  const rateAll = centsOf(s, "spend_avg_all");
  const asOf = typeof s.as_of === "string" ? s.as_of : "";
  return [
    <Tile key="cash" label="Cash" value={sgd(cash)} note={asOf ? `at ${formatMonth(asOf)}` : undefined} />,
    <Tile key="six" label="Runway, 6 months" value={months(six)} noteTone="neutral" tone={tone(six !== null && low !== null && six < low)}
      note={rate6 !== null ? `${sgd(rate6)} a month` : undefined} />,
    <Tile key="all" label="Runway, all months" value={months(all)} noteTone="neutral" tone={tone(all !== null && low !== null && all < low)}
      note={rateAll !== null ? `${sgd(rateAll)} a month` : undefined} />,
    <Tile key="trend" label="Cash over 6 months" value={trend === null ? "None yet" : sgd(trend)} tone={tone(trend !== null && trend < 0)} />,
  ];
}

const SHARES = [
  { key: "need", label: "Needs", worseWhenAbove: true },
  { key: "want", label: "Wants", worseWhenAbove: true },
  { key: "savings", label: "Savings", worseWhenAbove: false },
] as const;

function split(data: Dict) {
  const s = dict(data.summary);
  const targets = dict(s?.targets);
  if (!s || !targets) return null;
  const tiles = SHARES.flatMap(({ key, label, worseWhenAbove }) => {
    const share = num(s, `${key}_share`);
    const target = num(targets, key);
    if (share === null) return [];
    const off = target !== null && (worseWhenAbove ? share > target : share < target);
    const perMonth = centsOf(s, `${key}_per_month`);
    const note = [target !== null ? `rule ${percent(target)}` : "", perMonth !== null ? `${sgd(perMonth)} a month` : ""]
      .filter(Boolean).join(" · ");
    return [<Tile key={key} label={`${label}, of income`} value={percent(share)} tone={tone(off)} noteTone="neutral" note={note || undefined} />];
  });
  return tiles.length ? tiles : null;
}
