import { Button } from "./Button";

// G13: the save pattern for a form too long to save a field at a time: it says
// whether there is anything unsaved, and offers Discard and Save, both off while
// there is nothing to save. Plan's settings use it in the actions of a `pinned`
// Card so it stays in view on a phone; Net worth's inputs in a Sheet's foot (G18).
export function SaveBar({ dirty, saving = false, onSave, onDiscard, saveLabel = "Save" }: {
  dirty: boolean; saving?: boolean; onSave: () => void; onDiscard: () => void; saveLabel?: string;
}) {
  return (
    <div className="flex flex-wrap items-center justify-end gap-2">
      {dirty
        ? <span role="status" className="text-[13px] font-medium text-warn">Unsaved changes</span>
        : <span className="text-[13px] text-ink-3">Saved</span>}
      <Button onClick={onDiscard} disabled={!dirty || saving}>Discard</Button>
      <Button variant="primary" onClick={onSave} disabled={!dirty || saving}>{saving ? "Saving" : saveLabel}</Button>
    </div>
  );
}
