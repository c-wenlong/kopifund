import type { LucideIcon } from "lucide-react";
import { KindIcon } from "../icons";

// G13: a status pill: "4 to apply", "3 problems", "Will change 12 rows". Bigger than
// the small label `Chip` puts beside a name. The tone is the meaning (neutral, info,
// good, warn, danger), never decoration, so it reads the same on every page and in
// both themes. A pill that does something is a button with the same look (`onClick`).
//
// `empty` is a step that has not run (Upload, Past uploads): an outline with no fill,
// in the same quiet ink as other secondary text.
//
// G18: `icon` is the kind's icon from web/src/icons.tsx, before the words and in
// their colour; `spin` turns it for a step still running. The words always stay.
export type StatusTone = "neutral" | "info" | "good" | "warn" | "danger" | "empty";

const TONE: Record<StatusTone, string> = {
  neutral: "bg-tile text-ink-2",
  info: "bg-info-bg text-info",
  good: "bg-good-bg text-good",
  warn: "bg-warn-bg text-warn",
  danger: "bg-danger-bg text-danger",
  empty: "border border-line text-ink-3",
};

const LOOK = "num inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-[12px] font-medium whitespace-nowrap";

export function StatusChip({ tone = "neutral", icon, spin, title, children, onClick, pressed }: {
  tone?: StatusTone; icon?: LucideIcon; spin?: boolean; title?: string; children: React.ReactNode;
  onClick?: () => void; pressed?: boolean;
}) {
  const mark = icon && <KindIcon icon={icon} spin={spin} />;
  if (onClick) {
    return (
      <button type="button" onClick={onClick} title={title} aria-pressed={pressed}
        className={`${LOOK} ${TONE[tone]} hover:opacity-80 ${pressed ? "ring-1 ring-current" : ""}`}>
        {mark}{children}
      </button>
    );
  }
  return <span title={title} className={`${LOOK} ${TONE[tone]}`}>{mark}{children}</span>;
}
