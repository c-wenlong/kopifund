import { useEffect } from "react";
import { ChevronRight } from "lucide-react";
import type { EyeLine, EyeTone } from "../api";
import { EYE_ICON, KindIcon } from "../icons";
import { onLinkClick } from "../router";
import { EmptyState } from "./EmptyState";

// G18: the lines of Needs your eye (finance/home.py builds them, most urgent
// first), drawn the same in Home's card, the sidebar's list and the phone
// menu's section. Each is a link with its kind's icon in its tone's colour, and
// wraps rather than cutting its words off.

export const NOTHING = "Nothing needs your eye";

/** The badge's colour: danger when any line is, else the warning colour. */
export function eyeTone(lines: EyeLine[]): EyeTone {
  return lines.some((l) => l.tone === "danger") ? "danger" : "warn";
}

/** The lines as links; `onFollow` runs as one is followed (the list around them closes). */
export function EyeList({ lines, onFollow, label }: { lines: EyeLine[]; onFollow?: () => void; label?: string }) {
  if (lines.length === 0) return <EmptyState title={NOTHING} />;
  return (
    <ul aria-label={label} className="m-0 flex list-none flex-col p-0">
      {lines.map((l) => (
        <li key={`${l.kind}-${l.to}`} data-tone={l.tone} className="border-t border-line first:border-t-0">
          <a href={l.to}
            onClick={(e) => {
              onLinkClick(l.to)(e);
              onFollow?.();
            }}
            className="flex items-start gap-2 rounded-md px-2 py-1.5 text-[13px] text-ink no-underline hover:bg-hover">
            {/* what the line is about, in its tone's colour (icons.tsx) */}
            <KindIcon icon={EYE_ICON[l.kind]} size={14} tone={l.tone} className="mt-0.5" />
            <span className={`min-w-0 flex-1 break-words ${l.tone === "danger" ? "text-danger" : ""}`}>{l.text}</span>
            <ChevronRight size={14} aria-hidden className="mt-0.5 shrink-0 text-ink-3" />
          </a>
        </li>
      ))}
    </ul>
  );
}

// Home's "All N" asks the sidebar (or, on a phone, the menu) to open its list
const OPEN = "kopifund:eye-open";

export function openEyeList() {
  window.dispatchEvent(new Event(OPEN));
}

export function useEyeListRequests(onOpen: () => void) {
  useEffect(() => {
    window.addEventListener(OPEN, onOpen);
    return () => window.removeEventListener(OPEN, onOpen);
  }, [onOpen]);
}
