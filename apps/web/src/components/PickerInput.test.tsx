import { fireEvent, render, screen } from "@testing-library/react";
import { createRef, useState } from "react";
import { PickerInput } from "./PickerInput";

// a day as "3/10/2026" in the box, "2026-10-03" in the value
const parse = (t: string) => {
  const m = /^(\d{1,2})\/(\d{1,2})\/(\d{4})$/.exec(t.trim());
  return m ? `${m[3]}-${m[2].padStart(2, "0")}-${m[1].padStart(2, "0")}` : null;
};
const format = (iso: string) => { const [y, m, d] = iso.split("-"); return `${Number(d)}/${Number(m)}/${y}`; };

type Spies = { onOpen: () => void; onClose: () => void; onEnter: () => void; open?: boolean; clearable?: boolean; start?: string; picks?: number };

function Harness({ onOpen, onClose, onEnter, open = false, clearable = false, start = "2026-10-03", picks = 0, changed }: Spies & { changed: (v: string) => void }) {
  const [value, setValue] = useState(start);
  return (
    <PickerInput value={value} onChange={(v) => { setValue(v); changed(v); }} parse={parse} format={format} label="Due date"
      placeholder="d/m/yyyy" clearable={clearable} open={open} popupId="cal" inputRef={createRef()} boxRef={createRef()}
      skipOpen={{ current: false }} picks={picks} onOpen={onOpen} onClose={onClose} onEnter={onEnter} />
  );
}
const setup = (more: Partial<Spies> = {}) => {
  const spies = { onOpen: vi.fn(), onClose: vi.fn(), onEnter: vi.fn(), changed: vi.fn() };
  const view = render(<Harness {...spies} {...more} />);
  return { ...spies, ...view, box: screen.getByRole("textbox", { name: "Due date" }) as HTMLInputElement };
};

describe("PickerInput", () => {
  it("shows the value in the app's format and opens the calendar on focus", () => {
    const { box, onOpen } = setup();
    expect(box).toHaveValue("3/10/2026");
    fireEvent.focus(box);
    expect(onOpen).toHaveBeenCalled();
  });

  it("passes a value up the moment the typed text reads as one, and not before", () => {
    const { box, changed } = setup();
    fireEvent.change(box, { target: { value: "4/10/20" } });
    expect(changed).not.toHaveBeenCalled();
    fireEvent.change(box, { target: { value: "4/10/2026" } });
    expect(changed).toHaveBeenCalledWith("2026-10-04");
  });

  it("does not pass up a value it already holds, however it is written", () => {
    const { box, changed } = setup();
    fireEvent.change(box, { target: { value: "03/10/2026" } });
    expect(changed).not.toHaveBeenCalled();
  });

  it("keeps text that is not a date in the box on leaving, marked under it, until it is fixed", () => {
    const { box, changed } = setup();
    fireEvent.change(box, { target: { value: "soon" } });
    fireEvent.blur(box);
    expect(box).toHaveValue("soon");
    expect(box).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByRole("alert")).toHaveTextContent("Not a date");
    expect(box).toHaveAccessibleDescription("Not a date");
    expect(changed).not.toHaveBeenCalled();
    fireEvent.change(box, { target: { value: "9/10/2026" } });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(changed).toHaveBeenCalledWith("2026-10-09");
  });

  it("calls an emptied box an empty box, not an error, and puts the value back on leaving it", () => {
    const { box } = setup();
    fireEvent.change(box, { target: { value: "  " } });
    fireEvent.blur(box);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(box).toHaveValue("3/10/2026");
  });

  it("clears the value when a clearable box is emptied", () => {
    const { box, changed } = setup({ clearable: true });
    fireEvent.change(box, { target: { value: "" } });
    expect(changed).toHaveBeenLastCalledWith("");
    expect(box).toHaveValue("");
  });

  it("has a Clear button that empties it without reopening the calendar", () => {
    const { box, changed, onOpen } = setup({ clearable: true });
    fireEvent.click(screen.getByRole("button", { name: "Clear Due date" }));
    expect(changed).toHaveBeenCalledWith("");
    expect(box).toHaveValue("");
    expect(onOpen).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: "Clear Due date" })).not.toBeInTheDocument();
  });

  it("moves into the calendar on the arrow-down key, and closes it on Escape, Tab and Enter while it is open", () => {
    const closed = setup();
    fireEvent.keyDown(closed.box, { key: "ArrowDown" });
    expect(closed.onEnter).toHaveBeenCalledOnce();
    fireEvent.keyDown(closed.box, { key: "Escape" });
    fireEvent.keyDown(closed.box, { key: "Enter" });
    expect(closed.onClose).not.toHaveBeenCalled();
    closed.unmount();

    const open = setup({ open: true });
    expect(open.box).toHaveAttribute("aria-expanded", "true");
    expect(open.box).toHaveAttribute("aria-controls", "cal");
    for (const key of ["Escape", "Tab", "Enter"]) fireEvent.keyDown(open.box, { key });
    expect(open.onClose).toHaveBeenCalledTimes(3);
  });

  it("does not submit the form with Enter while the calendar is open", () => {
    const onSubmit = vi.fn((e: React.FormEvent) => e.preventDefault());
    render(<form onSubmit={onSubmit}><Harness onOpen={() => {}} onClose={() => {}} onEnter={() => {}} open changed={() => {}} /></form>);
    const stop = !fireEvent.keyDown(screen.getByRole("textbox", { name: "Due date" }), { key: "Enter" });
    expect(stop).toBe(true);
  });

  it("replaces what was typed with the day the calendar picked, even the day already held", () => {
    const { box, rerender, changed, onOpen, onClose, onEnter } = setup();
    fireEvent.change(box, { target: { value: "soon" } });
    fireEvent.blur(box);
    expect(box).toHaveValue("soon");
    rerender(<Harness onOpen={onOpen} onClose={onClose} onEnter={onEnter} changed={changed} picks={1} />);
    expect(box).toHaveValue("3/10/2026");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
