import { useState } from "react";
import { FileUp } from "lucide-react";

// G13: where the PDFs go. A button that opens the file picker, inside an area a
// file can be dropped on (a phone has no dropping, so there it is the button
// alone). While nothing can be added it says why, beside the button, so a
// disabled "Choose PDFs" is never a mystery.
export function DropZone({ disabled, reason, onFiles }: {
  disabled: boolean;
  /** what to do first, shown while `disabled` */
  reason: string;
  onFiles: (files: FileList | null) => void;
}) {
  const [over, setOver] = useState(false);
  const [pending, setPending] = useState<string | null>(null);
  const block = (e: React.DragEvent) => { e.preventDefault(); };
  return (
    <div data-testid="drop-zone"
      onDragEnter={(e) => { block(e); setOver(true); }}
      onDragOver={(e) => { block(e); setOver(true); }}
      onDragLeave={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setOver(false); }}
      onDrop={(e) => {
        block(e);
        setOver(false);
        if (disabled) setPending(reason);
        else { setPending(null); onFiles(e.dataTransfer.files); }
      }}
      className={`flex flex-wrap items-center gap-x-3 gap-y-2 rounded-lg border border-dashed px-3 py-3 text-[13px] sm:py-4 ${over && !disabled ? "border-ink bg-hover" : "border-line"}`}>
      <label className={`flex h-8 items-center gap-2 rounded-md border border-line px-3 has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-[var(--focus)] ${disabled ? "cursor-not-allowed opacity-40" : "cursor-pointer hover:bg-hover"}`}>
        <FileUp size={15} aria-hidden /> Choose PDFs
        <input type="file" accept="application/pdf" multiple className="sr-only" disabled={disabled}
          onChange={(e) => { setPending(null); onFiles(e.target.files); e.target.value = ""; }} />
      </label>
      <span className={`min-w-0 flex-1 ${disabled ? (pending ? "text-danger" : "text-ink-2") : "hidden text-ink-3 sm:inline"}`}>
        {disabled ? reason : "or drop them here"}
      </span>
    </div>
  );
}
