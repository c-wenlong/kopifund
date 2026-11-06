import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import { KindIcon } from "../icons";
import { Button } from "./Button";

// Audit: a filter a link put on a list, said as a chip with its own Clear
// (Transactions: a chart's scope, a payee, the scam flag, rows with no SGD
// value). `warn` is a filter that narrows to rows needing a look, led by its
// kind's icon.
export function FilterChip({ tone = "neutral", icon, onClear, children }: {
  tone?: "neutral" | "warn"; icon?: LucideIcon; onClear: () => void; children: ReactNode;
}) {
  return (
    <div className="flex items-center gap-2 text-[12px] text-ink-2">
      {tone === "warn"
        ? <span className="inline-flex items-center gap-1 rounded-full bg-warn-bg px-2.5 py-0.5 text-warn">{icon && <KindIcon icon={icon} />}{children}</span>
        : <span className="rounded-full bg-tile px-2.5 py-0.5">{children}</span>}
      <Button variant="ghost" size="sm" onClick={onClear}>Clear</Button>
    </div>
  );
}
