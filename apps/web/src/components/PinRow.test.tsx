import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useState } from "react";
import type { GroupRow, GroupRows } from "../api";
import { json } from "../test-helpers";
import { GroupRowsPanel, PinForm, pinDirty, pinnedText, savePin, undoRuleWrite, type PinDraft } from "./PinRow";
import { Toaster } from "./Toast";

// invented rows, in cents
const row = (over: Partial<GroupRow> = {}): GroupRow => ({
  key: "k1", date: "2026-09-12", amount: -5000, currency: "SGD", amount_sgd: -5000, account: "DBS Multiplier", bank: "dbs",
  text: "PAYNOW TO ALEX", field: "counterparty", payee: "", category: "", similar: null, pinned: null, ...over,
});
const rows = (list: GroupRow[], total = list.length): GroupRows => ({
  rows: list, total, categories: ["Donations", "Gifts"], kinds: ["Merchant", "Person"], tiers: [], tier_files: {},
});

describe("pinDirty", () => {
  const base = { category: "Gifts", payee: "Alex", kind: "Person", note: "" };
  it("is false for what the form opened on, however it is padded", () => {
    expect(pinDirty(base, base)).toBe(false);
    expect(pinDirty({ ...base, payee: " Alex ", note: "  " }, base)).toBe(false);
  });
  it("is true for another category, payee or note", () => {
    expect(pinDirty({ ...base, category: "Donations" }, base)).toBe(true);
    expect(pinDirty({ ...base, payee: "Sam" }, base)).toBe(true);
    expect(pinDirty({ ...base, note: "a wedding gift" }, base)).toBe(true);
  });
  it("counts a kind only beside a payee", () => {
    expect(pinDirty({ ...base, kind: "Merchant" }, base)).toBe(true);
    expect(pinDirty({ ...base, payee: "", kind: "Merchant" }, { ...base, payee: "" })).toBe(false);
  });
});

describe("pinnedText", () => {
  const pinned = { category: "Gifts", payee: "Alex", file: "keys.csv", line: 7, applied: true } as NonNullable<GroupRow["pinned"]>;
  it("says what the rule set, and where it lives", () => {
    expect(pinnedText(pinned)).toBe("Set to Gifts, Alex by keys.csv line 7.");
    expect(pinnedText({ ...pinned, payee: "" })).toBe("Set to Gifts by keys.csv line 7.");
  });
  it("says a rule a person edited by hand waits for Apply rules", () => {
    expect(pinnedText({ ...pinned, applied: false })).toBe("Set to Gifts, Alex by keys.csv line 7; not applied yet.");
  });
});

describe("savePin and undoRuleWrite", () => {
  it("files the row with the write header, and the toast counts the rows and offers Undo that takes the rule back", async () => {
    const calls: { url: string; body: unknown }[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, body: JSON.parse(String(init?.body)) });
      return json(url === "/api/rules/pin" ? { change: "c9", relinked: { changed: 2, written: 2 } } : { relinked: { changed: 2, written: 2 } });
    }));
    render(<Toaster />);
    await act(async () => { await savePin({ key: "k1", category: "Gifts", payee: "Alex", kind: "Person", note: "" }); });
    expect(calls[0]).toEqual({ url: "/api/rules/pin", body: { key: "k1", category: "Gifts", payee: "Alex", kind: "Person", note: "" } });
    expect(await screen.findByText("Category set, 2 rows changed.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    expect(await screen.findByText("Undone, 2 rows changed back.")).toBeInTheDocument();
    expect(calls[1]).toEqual({ url: "/api/rules/undo", body: { change: "c9" } });
  });

  it("offers no Undo when the server named no change to take back", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json({ unchanged: true })));
    render(<Toaster />);
    await act(async () => { await savePin({ key: "k1", category: "Gifts" }); });
    expect(await screen.findByText("Category set, no rows changed.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Undo" })).not.toBeInTheDocument();
  });

  it("answers false and says why in an error toast when the undo is refused", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json({ error: "That change was already undone." }, 409)));
    render(<Toaster />);
    let ok: boolean | undefined;
    await act(async () => { ok = await undoRuleWrite("c9"); });
    expect(ok).toBe(false);
    expect(await screen.findByText("That change was already undone.")).toBeInTheDocument();
  });
});

describe("PinForm", () => {
  function Form({ onPinned = () => {}, more = {} }: { onPinned?: () => void; more?: Partial<Parameters<typeof PinForm>[0]> }) {
    return <><PinForm row={row()} categories={["Donations", "Gifts"]} kinds={["Merchant", "Person"]} onPinned={onPinned} {...more} /><Toaster /></>;
  }
  const what = "the 12 Sep 2026 row";
  // the app's Combobox: open it, press the option
  const pickCategory = (name: string) => {
    fireEvent.focus(screen.getByRole("combobox", { name: `Category for ${what}` }));
    fireEvent.mouseDown(screen.getByRole("option", { name }));
  };

  it("will not save until a category is chosen", () => {
    render(<Form />);
    expect(screen.getByRole("button", { name: "Save for this row" })).toBeDisabled();
  });

  it("offers a kind only once a payee is typed, opened on Person for a counterparty", () => {
    render(<Form />);
    expect(screen.queryByRole("button", { name: `Kind for ${what}` })).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(`Payee for ${what}`), { target: { value: "Alex" } });
    expect(screen.getByRole("button", { name: `Kind for ${what}` })).toHaveTextContent("Person");
  });

  it("saves the typed category, payee and note, then tells the page it is done", async () => {
    const bodies: unknown[] = [];
    vi.stubGlobal("fetch", vi.fn(async (_url: string, init?: RequestInit) => {
      bodies.push(JSON.parse(String(init?.body)));
      return json({ change: "c1", relinked: { changed: 1, written: 1 } });
    }));
    const onPinned = vi.fn();
    render(<Form onPinned={onPinned} />);
    pickCategory("Gifts");
    fireEvent.change(screen.getByLabelText(`Payee for ${what}`), { target: { value: " Alex " } });
    fireEvent.change(screen.getByLabelText(`Note for ${what}`), { target: { value: "a wedding gift" } });
    fireEvent.click(screen.getByRole("button", { name: "Save for this row" }));
    await waitFor(() => expect(onPinned).toHaveBeenCalledOnce());
    expect(bodies).toEqual([{ key: "k1", category: "Gifts", payee: "Alex", kind: "Person", note: "a wedding gift" }]);
    expect(await screen.findByText("Category set, 1 row changed.")).toBeInTheDocument();
  });

  it("keeps the form open on what was typed when the save fails, says why, and saves again", async () => {
    let fail = true;
    vi.stubGlobal("fetch", vi.fn(async () => fail ? json({ error: "keys.csv is read only." }, 500) : json({ change: "c1", relinked: { changed: 1 } })));
    const onPinned = vi.fn();
    render(<Form onPinned={onPinned} />);
    pickCategory("Gifts");
    fireEvent.click(screen.getByRole("button", { name: "Save for this row" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("keys.csv is read only.");
    expect(onPinned).not.toHaveBeenCalled();
    expect(screen.getByRole("combobox", { name: `Category for ${what}` })).toHaveValue("Gifts");
    fail = false;
    fireEvent.click(screen.getByRole("button", { name: "Save for this row" }));
    await waitFor(() => expect(onPinned).toHaveBeenCalledOnce());
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("stays open on what was typed while the save was on its way, and is not done", async () => {
    let finish: (r: Response) => void = () => {};
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>((r) => { finish = r; })));
    const onPinned = vi.fn();
    render(<Form onPinned={onPinned} />);
    pickCategory("Gifts");
    fireEvent.click(screen.getByRole("button", { name: "Save for this row" }));
    expect(screen.getByRole("button", { name: "Saving…" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText(`Note for ${what}`), { target: { value: "added meanwhile" } });
    await act(async () => finish(json({ change: "c1", relinked: { changed: 1 } })));
    expect(onPinned).not.toHaveBeenCalled();
    expect(screen.getByLabelText(`Note for ${what}`)).toHaveValue("added meanwhile");
    expect(screen.getByRole("button", { name: "Save for this row" })).toBeEnabled();
  });

  it("reopens on a draft the page kept, and reports every change to it", () => {
    const drafts: PinDraft[] = [];
    function Keeper() {
      const [draft, setDraft] = useState<PinDraft>({ category: "Donations", payee: "", kind: "Person", note: "kept note" });
      return <PinForm row={row()} categories={["Donations", "Gifts"]} kinds={["Merchant", "Person"]} draft={draft}
        onDraft={(d) => { drafts.push(d); setDraft(d); }} />;
    }
    render(<Keeper />);
    expect(screen.getByLabelText(`Note for ${what}`)).toHaveValue("kept note");
    expect(screen.getByRole("combobox", { name: `Category for ${what}` })).toHaveValue("Donations");
    fireEvent.change(screen.getByLabelText(`Note for ${what}`), { target: { value: "newer note" } });
    expect(drafts.at(-1)).toMatchObject({ category: "Donations", note: "newer note" });
  });
});

describe("GroupRowsPanel", () => {
  const serve = (body: GroupRows) => vi.stubGlobal("fetch", vi.fn(async () => json(body)));

  it("lists the rows with their date, amount and account, and opens a form for one", async () => {
    serve(rows([row(), row({ key: "k2", date: "2026-09-10", amount: -1250 })]));
    render(<GroupRowsPanel query="payee=Alex" expected={2} />);
    expect(await screen.findAllByRole("listitem")).toHaveLength(2);
    const first = screen.getAllByRole("listitem")[0];
    expect(first).toHaveTextContent("12 Sep 2026");
    expect(first).toHaveTextContent("−S$50.00");
    expect(first).toHaveTextContent("DBS Multiplier");
    fireEvent.click(within(first).getByRole("button", { name: /^Set the 12 Sep 2026 row of −S\$50\.00 on its own/ }));
    expect(within(first).getByRole("combobox", { name: "Category for the 12 Sep 2026 row" })).toBeInTheDocument();
    expect(within(first).getByRole("button", { name: /Set the 12 Sep 2026 row/ })).toHaveTextContent("Cancel");
    fireEvent.click(within(first).getByRole("button", { name: /Set the 12 Sep 2026 row/ }));
    expect(within(first).queryByRole("combobox")).not.toBeInTheDocument();
  });

  it("says what a row already pinned was set to, with no button to set it again", async () => {
    serve(rows([row({ pinned: { category: "Gifts", payee: "", file: "keys.csv", line: 3, applied: true } as GroupRow["pinned"] })]));
    render(<GroupRowsPanel query="payee=Alex" expected={1} />);
    expect(await screen.findByText("Set to Gifts by keys.csv line 3.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /on its own/ })).not.toBeInTheDocument();
  });

  it("says when only the newest rows are listed, and how many the record does not have yet", async () => {
    serve(rows([row()], 40));
    render(<GroupRowsPanel query="payee=Alex" expected={43} />);
    expect(await screen.findByText("The newest 1 of 40.")).toBeInTheDocument();
    expect(screen.getByText(/3 more are not in your record yet: load, then set them here\./)).toBeInTheDocument();
  });

  it("says why the rows could not be listed", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json({ error: "The record is locked." }, 500)));
    render(<GroupRowsPanel query="payee=Alex" expected={1} />);
    expect(await screen.findByText("Could not list the rows: The record is locked.")).toBeInTheDocument();
  });
});
