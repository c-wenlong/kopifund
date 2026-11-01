// G13: the one segmented control: a range, a scope, a split, a chart view. A
// radio group with one tab stop; the arrow keys move and choose, skipping any
// option that is off. The chosen option is a solid ink pill so it reads at a
// glance in both themes, and each target is 32px tall.
import { useRef } from "react";
import type { LucideIcon } from "lucide-react";
import { KindIcon } from "../icons";

// G18: an option for a kind of thing (a need class) leads with that kind's icon
export type SegmentOption<T extends string | number> = { value: T; text: string; disabled?: boolean; icon?: LucideIcon };

export function Segmented<T extends string | number>({ label, value, options, onChange }: {
  label: string; value: T; options: readonly SegmentOption<T>[]; onChange: (v: T) => void;
}) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const move = (from: number, step: number) => {
    for (let n = 1; n <= options.length; n++) {
      const i = (from + step * n + options.length * n) % options.length;
      if (options[i].disabled) continue;
      onChange(options[i].value);
      refs.current[i]?.focus();
      return;
    }
  };
  // the one tab stop: the chosen option, or the first one that is on when the value is not among them
  const stop = options.some((o) => o.value === value && !o.disabled) ? value : options.find((o) => !o.disabled)?.value;
  return (
    <div role="radiogroup" aria-label={label} className="inline-flex max-w-full rounded-lg border border-line p-0.5 text-[13px]">
      {options.map((o, i) => {
        const on = o.value === value;
        return (
          <button key={String(o.value)} ref={(el) => { refs.current[i] = el; }} type="button" role="radio" aria-checked={on}
            disabled={o.disabled} tabIndex={o.value === stop ? 0 : -1} onClick={() => onChange(o.value)}
            onKeyDown={(e) => {
              if (e.key === "ArrowRight" || e.key === "ArrowDown") { e.preventDefault(); move(i, 1); }
              else if (e.key === "ArrowLeft" || e.key === "ArrowUp") { e.preventDefault(); move(i, -1); }
            }}
            className={`h-8 min-w-8 whitespace-nowrap rounded-md px-3 disabled:opacity-40 ${o.icon ? "inline-flex items-center gap-1.5" : ""} ${on ? "bg-ink font-medium text-page" : "text-ink-2 hover:bg-hover"}`}>
            {o.icon && <KindIcon icon={o.icon} size={13} />}
            {o.text}
          </button>
        );
      })}
    </div>
  );
}
