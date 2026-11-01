import { LoaderCircle } from "lucide-react";

// G13: what a running step shows inside its card: a spinner, a sentence saying
// what is running ("Reading 129 statements"), and a thin bar sliding along a
// track. The step has no real progress to report, so the bar is indeterminate;
// a step that can count (a batch of uploads) puts the count in the sentence.
export function BusyStrip({ what }: { what: string }) {
  return (
    <div role="status" className="flex flex-col gap-2">
      <p className="m-0 flex items-center gap-2 text-[14px] text-ink-2">
        <LoaderCircle size={16} className="animate-spin" aria-hidden /> {what}
      </p>
      <div aria-hidden className="h-1 overflow-hidden rounded-full bg-hover">
        <div className="kf-slide h-full w-1/3 rounded-full bg-ink-2" />
      </div>
    </div>
  );
}
