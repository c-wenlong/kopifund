import type { LucideIcon } from "lucide-react";
import { KindIcon } from "../icons";

// G13: the small label beside a name ("Investing", "Long stay", "Planned").
// One shape and one set of tokens, so a chip reads the same on every page.
// G18: a chip for a kind of thing leads with that kind's icon (web/src/icons.tsx); the word stays.
export type ChipTone = "neutral" | "info" | "warn";

const TONE: Record<ChipTone, string> = {
  neutral: "bg-tile text-ink-2",
  info: "bg-info-bg text-info",
  warn: "bg-warn-bg text-warn",
};

export function Chip({ children, tone = "neutral", icon }: { children: React.ReactNode; tone?: ChipTone; icon?: LucideIcon }) {
  const look = `shrink-0 whitespace-nowrap rounded px-1.5 py-px text-[11px] ${TONE[tone]}`;
  if (!icon) return <span className={look}>{children}</span>;
  return <span className={`inline-flex items-center gap-1 ${look}`}><KindIcon icon={icon} size={11} />{children}</span>;
}
