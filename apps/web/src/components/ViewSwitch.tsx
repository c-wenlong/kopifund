// G13: the little switch in a chart card's header that picks how the same data
// is drawn (Stacked, Lines, Total), a Segmented control. The choice is
// remembered per chart in this browser.
import { useState } from "react";
import { useCardTitle } from "./Card";
import { Segmented } from "./Segmented";

export type ViewOption<T extends string> = { value: T; text: string };

/**
 * `label` names the control ("Chart view"). A page can hold several charts, so
 * its accessible name also says which one: "Chart view for Net worth so far",
 * from the card it sits in, or from `chart` when that is set.
 */
export function ViewSwitch<T extends string>({ label, chart, value, options, onChange }: {
  label: string; chart?: string; value: T; options: readonly ViewOption<T>[]; onChange: (v: T) => void;
}) {
  const inCard = useCardTitle();
  const named = chart ?? inCard;
  return <Segmented label={named ? `${label} for ${named}` : label} value={value} options={options} onChange={onChange} />;
}

const STORE = "kopifund.chart-view.";

function remembered<T extends string>(id: string, allowed: readonly T[]): T | null {
  try {
    const v = window.localStorage.getItem(STORE + id);
    return allowed.find((a) => a === v) ?? null;
  } catch {
    return null;
  }
}

/** The view a chart is drawn in, kept per chart (`id`) in localStorage; anything unreadable falls back to `fallback`. */
export function useChartView<T extends string>(id: string, allowed: readonly T[], fallback: T): [T, (v: T) => void] {
  const [view, setView] = useState<T>(() => remembered(id, allowed) ?? fallback);
  const set = (v: T) => {
    setView(v);
    try { window.localStorage.setItem(STORE + id, v); } catch { /* private mode: the choice lasts until reload */ }
  };
  return [view, set];
}

export type ChartView = "stacked" | "lines" | "total";

export const VIEW_OPTIONS: Record<ChartView, ViewOption<ChartView>> = {
  stacked: { value: "stacked", text: "Stacked" },
  lines: { value: "lines", text: "Lines" },
  total: { value: "total", text: "Total" },
};

/** The views a chart offers, in the order they are shown. */
export const viewsOf = (...v: ChartView[]) => v.map((k) => VIEW_OPTIONS[k]);
