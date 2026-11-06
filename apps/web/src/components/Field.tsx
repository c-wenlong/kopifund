import { useId, type ReactNode } from "react";

/**
 * Audit: a labelled field: the name, then the box (given the id that ties them),
 * then anything that goes under it (a chip with a figure the record knows).
 * `small` is the 12px label most forms use; Plan's settings keep the card's size.
 */
export function Field({ label, className = "", small = true, chip, children }: {
  label: string; className?: string; small?: boolean; chip?: ReactNode;
  children: (id: string) => ReactNode;
}) {
  const id = useId();
  return (
    <div className={`flex min-w-0 flex-col gap-1 ${className}`}>
      <label htmlFor={id} className={`${small ? "text-[12px] " : ""}font-medium text-ink-2`}>{label}</label>
      {children(id)}
      {chip && <div>{chip}</div>}
    </div>
  );
}
