// G13: nothing here yet. A neutral block (not the amber Problem, which is for
// something that went wrong) that says what is missing and offers the next step.
export function EmptyState({ title, children, action }: {
  title: string; children?: React.ReactNode; action?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col items-start gap-2 rounded-lg border border-dashed border-line px-4 py-5 text-[13px]">
      <p className="m-0 text-[14px] font-medium">{title}</p>
      {children && <p className="m-0 text-ink-2">{children}</p>}
      {action}
    </div>
  );
}
