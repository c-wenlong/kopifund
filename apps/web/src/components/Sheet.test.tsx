import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { Sheet } from "./Sheet";
import { Combobox } from "./Combobox";
import { Select } from "./Select";
import { DateField } from "./DateField";

// G18: a page with a button that opens a sheet, as a caller would use it
function Page({ dirty = false, onRequestClose, onClose }: {
  dirty?: boolean; onRequestClose?: () => boolean | void; onClose?: () => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div data-testid="page">
      <button type="button" onClick={() => setOpen(true)}>Edit inputs</button>
      <Sheet open={open} title="Your projection inputs" dirty={dirty} onRequestClose={onRequestClose}
        onClose={() => { onClose?.(); setOpen(false); }}
        footer={<button type="button">Save</button>}>
        <label>Pay <input defaultValue="5000" /></label>
      </Sheet>
    </div>
  );
}

async function openSheet(props: Parameters<typeof Page>[0] = {}) {
  const user = userEvent.setup();
  render(<Page {...props} />);
  await user.click(screen.getByRole("button", { name: "Edit inputs" }));
  return user;
}

describe("Sheet", () => {
  it("opens as a modal dialog named by its title, with the focus inside it", async () => {
    await openSheet();
    const dialog = screen.getByRole("dialog", { name: "Your projection inputs" });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(dialog).toContainElement(document.activeElement as HTMLElement);
    expect(screen.getByRole("button", { name: "Close" })).toHaveFocus();
  });

  it("draws nothing while closed", () => {
    render(<Page />);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("closes on Escape and gives the focus back to the button that opened it", async () => {
    const onClose = vi.fn();
    const user = await openSheet({ onClose });
    await user.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledOnce();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Edit inputs" })).toHaveFocus();
  });

  it("closes on a press on the backdrop or the close button", async () => {
    const user = await openSheet();
    await user.click(screen.getByTestId("sheet-scrim"));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Edit inputs" }));
    await user.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("makes the rest of the page inert while open, and gives it back on close", async () => {
    const user = await openSheet();
    const app = screen.getByTestId("page").parentElement as HTMLElement;
    expect(app).toHaveAttribute("inert");
    expect(screen.getByRole("dialog").closest("[inert]")).toBeNull();
    await user.keyboard("{Escape}");
    expect(app).not.toHaveAttribute("inert");
  });

  it("keeps Tab inside the sheet, both ways round", async () => {
    const user = await openSheet();
    const close = screen.getByRole("button", { name: "Close" });
    const save = screen.getByRole("button", { name: "Save" });
    await user.tab();
    expect(screen.getByRole("textbox", { name: "Pay" })).toHaveFocus();
    await user.tab();
    expect(save).toHaveFocus();
    await user.tab();
    expect(close).toHaveFocus();
    await user.tab({ shift: true });
    expect(save).toHaveFocus();
  });

  it("leaves an Escape a field inside has used alone", async () => {
    const user = await openSheet();
    const field = screen.getByRole("textbox", { name: "Pay" });
    field.addEventListener("keydown", (e) => e.preventDefault());
    field.focus();
    await user.keyboard("{Escape}");
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("stays open when the caller's onRequestClose says no", async () => {
    const user = await openSheet({ onRequestClose: () => false });
    await user.keyboard("{Escape}");
    await user.click(screen.getByTestId("sheet-scrim"));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  describe("with unsaved edits", () => {
    it("asks in its foot before closing; Keep editing keeps it open", async () => {
      const onClose = vi.fn();
      const user = await openSheet({ dirty: true, onClose });
      await user.keyboard("{Escape}");
      const dialog = screen.getByRole("dialog");
      expect(dialog).toHaveTextContent("Leave without saving?");
      expect(screen.getByRole("button", { name: "Keep editing" })).toHaveFocus();
      await user.click(screen.getByRole("button", { name: "Keep editing" }));
      expect(screen.queryByText("Leave without saving?")).not.toBeInTheDocument();
      expect(screen.getByRole("dialog")).toBeInTheDocument();
      expect(onClose).not.toHaveBeenCalled();
    });

    it("closes on Leave", async () => {
      const onClose = vi.fn();
      const user = await openSheet({ dirty: true, onClose });
      await user.click(screen.getByTestId("sheet-scrim"));
      await user.click(screen.getByRole("button", { name: "Leave" }));
      expect(onClose).toHaveBeenCalledOnce();
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Edit inputs" })).toHaveFocus();
    });

    it("takes a second Escape as Keep editing, not as Leave", async () => {
      const onClose = vi.fn();
      const user = await openSheet({ dirty: true, onClose });
      await user.keyboard("{Escape}");
      await user.keyboard("{Escape}");
      expect(screen.queryByText("Leave without saving?")).not.toBeInTheDocument();
      expect(screen.getByRole("dialog")).toBeInTheDocument();
      expect(onClose).not.toHaveBeenCalled();
    });

    it("holds a reload behind the browser's own question", async () => {
      await openSheet({ dirty: true });
      const e = new Event("beforeunload", { cancelable: true });
      window.dispatchEvent(e);
      expect(e.defaultPrevented).toBe(true);
    });
  });
});

// G18: a list or calendar open inside the sheet takes the first Escape for
// itself; only the next one closes the sheet
describe("an Escape inside a field's own list", () => {
  function Fields() {
    const [open, setOpen] = useState(true);
    const [bucket, setBucket] = useState("Food");
    const [cadence, setCadence] = useState("monthly");
    const [day, setDay] = useState("2026-10-03");
    return (
      <Sheet open={open} title="Edit" onClose={() => setOpen(false)}>
        <Combobox label="Bucket" value={bucket} options={["Food", "Travel", "Other"]} onChange={setBucket} />
        <Select label="Cadence" value={cadence} placeholder="Cadence" onChange={setCadence}
          options={[{ value: "monthly", label: "Monthly" }, { value: "yearly", label: "Yearly" }]} />
        <DateField label="Day" value={day} onChange={setDay} />
      </Sheet>
    );
  }

  it("closes a Combobox's list first, then the sheet", async () => {
    const user = userEvent.setup();
    render(<Fields />);
    await user.click(screen.getByRole("combobox", { name: "Bucket" }));
    expect(screen.getByRole("listbox", { name: "Bucket" })).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("listbox", { name: "Bucket" })).not.toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "Edit" })).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "Edit" })).not.toBeInTheDocument();
  });

  it("closes a Select's list first, then the sheet", async () => {
    const user = userEvent.setup();
    render(<Fields />);
    await user.click(screen.getByRole("button", { name: "Cadence" }));
    expect(screen.getByRole("listbox")).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "Edit" })).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "Edit" })).not.toBeInTheDocument();
  });

  it("closes a date field's calendar first, then the sheet", async () => {
    const user = userEvent.setup();
    render(<Fields />);
    await user.click(screen.getByRole("textbox", { name: "Day" }));
    expect(screen.getByRole("dialog", { name: "Day calendar" })).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "Day calendar" })).not.toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "Edit" })).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "Edit" })).not.toBeInTheDocument();
  });
});
