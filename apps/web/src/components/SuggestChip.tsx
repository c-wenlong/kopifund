// G13: one figure the record already knows, offered beside the box it fits. It
// fills nothing by itself; tapping it puts the figure in the box, and the owner
// can still change it or Discard. Touch targets stay 40px on a phone.
export function SuggestChip({ children, onUse }: { children: React.ReactNode; onUse: () => void }) {
  return (
    <button type="button" onClick={onUse}
      className="inline-flex max-w-full items-center rounded-full border border-dashed border-line bg-tile px-2.5 py-0.5 text-left text-[12px] text-ink-2 hover:bg-hover hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus)] pointer-coarse:min-h-10">
      {children}
    </button>
  );
}
