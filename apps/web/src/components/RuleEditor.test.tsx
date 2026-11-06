import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { NEW_DRAFT, RuleEditor } from "./RuleEditor";
import { json } from "../test-helpers";

describe("RuleEditor's preview", () => {
  // Audit: the lists were read with `!`, so an answer without them threw while the editor drew
  it("draws an answer that has no sample or earlier rules", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
      json({ error: null, matches: 3, changes: 2, already: 0 }));
    render(
      <RuleEditor rule={null} initial={{ ...NEW_DRAFT, pattern: "KOPI", category: "Food" }} tiers={["merchant"]}
        tierFiles={{ merchant: "merchants.csv" }} categories={["Food"]} kinds={["Merchant"]}
        onSaved={() => {}} onCancel={() => {}} />,
    );
    expect(await screen.findByText("Will change 2 rows")).toBeInTheDocument();
  });
});

describe("RuleEditor saving a new rule", () => {
  const editor = (onSaved = vi.fn(), onCancel = vi.fn(), initial = NEW_DRAFT) => {
    render(
      <RuleEditor rule={null} initial={initial} tiers={["merchant"]} tierFiles={{ merchant: "merchants.csv" }}
        categories={["Eating out", "Food"]} kinds={["Merchant", "Person"]} onSaved={onSaved} onCancel={onCancel} />,
    );
    return { onSaved, onCancel };
  };
  const pickCategory = (name: string) => {
    fireEvent.focus(screen.getByRole("combobox", { name: "Category for new rule" }));
    fireEvent.mouseDown(screen.getByRole("option", { name }));
  };
  const type = (label: string, value: string) => fireEvent.change(screen.getByLabelText(label), { target: { value } });
  const save = () => screen.getByRole("button", { name: "Save rule" });

  it("will not save without text to match and a category, and enables Save once it has both", () => {
    editor();
    expect(save()).toBeDisabled();
    type("Pattern for new rule", "KOPITIAM*");
    expect(save()).toBeDisabled();
    pickCategory("Eating out");
    expect(save()).toBeEnabled();
  });

  it("offers a kind only beside a payee, and sends none without one", async () => {
    const bodies: { rule: Record<string, unknown> }[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (url, init) => {
      if (String(url) === "/api/rules/save") {
        bodies.push(JSON.parse(String(init?.body)));
        return json({ file: "merchants.csv", line: 5, relinked: { changed: 6, written: 6 } });
      }
      return json({ error: null, matches: 6, changes: 6, already: 0 });
    });
    const { onSaved } = editor();
    expect(screen.getByRole("button", { name: "Kind for new rule" })).toBeDisabled();
    type("Pattern for new rule", "KOPITIAM*");
    pickCategory("Eating out");
    fireEvent.click(save());
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith("Rule saved, 6 rows changed", expect.objectContaining({ line: 5 })));
    expect(bodies[0].rule).toMatchObject({ match_on: "merchant", pattern: "KOPITIAM*", category: "Eating out", payee: "", kind: "", recurring: "" });
  });

  it("sends the kind with a payee and recurring as yes", async () => {
    const bodies: { rule: Record<string, unknown> }[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (url, init) => {
      if (String(url) === "/api/rules/save") {
        bodies.push(JSON.parse(String(init?.body)));
        return json({ file: "merchants.csv", line: 5, relinked: { changed: 0, written: 0 } });
      }
      return json({ error: null, matches: 0, changes: 0, already: 0 });
    });
    editor();
    type("Pattern for new rule", "SPOTIFY*");
    pickCategory("Food");
    type("Payee for new rule", "Spotify");
    fireEvent.click(screen.getByLabelText("Recurring"));
    expect(screen.getByRole("button", { name: "Kind for new rule" })).toBeEnabled();
    fireEvent.click(save());
    await waitFor(() => expect(bodies).toHaveLength(1));
    expect(bodies[0].rule).toMatchObject({ payee: "Spotify", kind: "Merchant", recurring: "yes" });
  });

  it("says why a save was refused, keeps what was typed, and lets it be sent again", async () => {
    let refuse = true;
    vi.spyOn(globalThis, "fetch").mockImplementation(async (url) => {
      if (String(url) === "/api/rules/save") return refuse ? json({ error: "merchants.csv is read only." }, 500) : json({ file: "merchants.csv", line: 5 });
      return json({ error: null, matches: 1, changes: 1, already: 0 });
    });
    const { onSaved } = editor();
    type("Pattern for new rule", "KOPITIAM*");
    pickCategory("Eating out");
    fireEvent.click(save());
    expect(await screen.findByText("merchants.csv is read only.")).toBeInTheDocument();
    expect(onSaved).not.toHaveBeenCalled();
    expect(screen.getByLabelText("Pattern for new rule")).toHaveValue("KOPITIAM*");
    expect(save()).toBeEnabled();
    refuse = false;
    fireEvent.click(save());
    await waitFor(() => expect(onSaved).toHaveBeenCalledOnce());
    expect(screen.queryByText("merchants.csv is read only.")).not.toBeInTheDocument();
  });

  it("does not save a rule the server's preview refuses, and Cancel leaves", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async () => json({ error: "That pattern matches everything.", matches: 0, changes: 0, already: 0 }));
    const { onCancel } = editor();
    type("Pattern for new rule", "*");
    pickCategory("Eating out");
    expect(await screen.findByRole("alert")).toHaveTextContent("That pattern matches everything.");
    expect(save()).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onCancel).toHaveBeenCalledOnce();
  });
});
