import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Setup } from "./Setup";
import type { LayoutStatus } from "../api";

const CHECKED: LayoutStatus = "checked on a real statement";
const SAMPLE: LayoutStatus = "from a public sample";

const state = { needed: true, data_folder: "/Users/x/.kopifund",
  providers: [
    { id: "posb-everyday", label: "POSB Everyday Savings account", parser: "dbs", bank: "posb" as const, title: "POSB Everyday", status: CHECKED },
    { id: "ocbc-360", label: "OCBC 360 account", parser: "ocbc", bank: "ocbc" as const, title: "OCBC 360", status: SAMPLE },
    { id: "youtrip-sgd", label: "YouTrip SGD wallet", parser: "youtrip", wallet: "SGD", bank: "youtrip" as const, title: "YouTrip", status: CHECKED },
    { id: "youtrip-usd", label: "YouTrip USD wallet", parser: "youtrip", wallet: "USD", bank: "youtrip" as const, title: "YouTrip", status: CHECKED },
    { id: "wise-sgd", label: "Wise SGD balance", parser: "wise", wallet: "SGD", bank: "wise" as const, title: "Wise", status: CHECKED },
    { id: "wise-usd", label: "Wise USD balance", parser: "wise", wallet: "USD", bank: "wise" as const, title: "Wise", status: CHECKED },
  ] };

describe("Setup", () => {
  it("will not create anything until a provider is chosen, and says why on the button", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    render(<Setup state={state} onDone={() => {}} />);
    const create = screen.getByRole("button", { name: "Choose at least one" });
    expect(create).toBeDisabled();
    await userEvent.click(create);
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(screen.getByText("None selected")).toBeInTheDocument();
    // choosing one turns it into the real button
    await userEvent.click(screen.getByLabelText("POSB Everyday Savings account"));
    expect(screen.getByRole("button", { name: "Create my data folder" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "Choose at least one" })).not.toBeInTheDocument();
  });

  it("posts the chosen providers with the write header", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("{}", { status: 200 }));
    const done = vi.fn();
    render(<Setup state={state} onDone={done} />);
    await userEvent.click(screen.getByLabelText("POSB Everyday Savings account"));
    await userEvent.click(screen.getByRole("button", { name: "Create my data folder" }));
    const [url, init] = fetchSpy.mock.calls[0];
    expect(url).toBe("/api/setup");
    expect((init as RequestInit).headers).toMatchObject({ "X-Kopifund": "1" });
    expect(JSON.parse(String((init as RequestInit).body))).toEqual({ providers: ["posb-everyday"] });
    expect(done).toHaveBeenCalled();
  });

  it("draws each bank as a tile with its mark, and counts what is selected", async () => {
    render(<Setup state={state} onDone={() => {}} />);
    const tile = screen.getByLabelText("POSB Everyday Savings account").closest("label")!;
    expect(tile.querySelector("[data-bank='posb']")).not.toBeNull();
    expect(screen.getByLabelText("OCBC 360 account").closest("label")!.querySelector("[data-bank='ocbc']")).not.toBeNull();
    await userEvent.click(screen.getByLabelText("POSB Everyday Savings account"));
    await userEvent.click(screen.getByLabelText("OCBC 360 account"));
    expect(screen.getByText("2 selected")).toBeInTheDocument();
    await userEvent.click(screen.getByLabelText("OCBC 360 account"));
    expect(screen.getByText("1 selected")).toBeInTheDocument();
  });

  it("groups a wallet's currencies as chips under one tile", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("{}", { status: 200 }));
    render(<Setup state={state} onDone={() => {}} />);
    // one tile each for YouTrip and Wise, not one per currency
    expect(screen.getAllByText("YouTrip")).toHaveLength(1);
    expect(screen.getAllByText("Wise")).toHaveLength(1);
    const youtrip = screen.getByRole("group", { name: "YouTrip currencies" });
    expect(within(youtrip).getAllByRole("checkbox").map((c) => c.getAttribute("aria-label")))
      .toEqual(["YouTrip SGD wallet", "YouTrip USD wallet"]);
    expect(within(youtrip).getByText("SGD")).toBeInTheDocument();
    await userEvent.click(within(youtrip).getByLabelText("YouTrip USD wallet"));
    await userEvent.click(within(screen.getByRole("group", { name: "Wise currencies" })).getByLabelText("Wise SGD balance"));
    expect(screen.getByText("2 selected")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Create my data folder" }));
    expect(JSON.parse(String((fetchSpy.mock.calls[0][1] as RequestInit).body))).toEqual({ providers: ["youtrip-usd", "wise-sgd"] });
  });

  it("shows the data folder on a line of its own", () => {
    render(<Setup state={state} onDone={() => {}} />);
    const line = screen.getByText("/Users/x/.kopifund");
    expect(line.parentElement).toHaveTextContent("Data folder: /Users/x/.kopifund");
    expect(line.closest("p")!.textContent).not.toMatch(/PDF statements/);
  });

  it("says on each tile whether a real statement has checked its layout (G21)", () => {
    render(<Setup state={state} onDone={() => {}} />);
    expect(screen.getByLabelText("POSB Everyday Savings account").closest("label")).toHaveTextContent("Checked on a real statement");
    expect(screen.getByLabelText("OCBC 360 account").closest("label")).toHaveTextContent("From a public sample");
    // a wallet's tile says it once, above its currencies
    const youtrip = screen.getByRole("group", { name: "YouTrip currencies" }).parentElement!;
    expect(within(youtrip).getAllByText("Checked on a real statement")).toHaveLength(1);
    expect(screen.getAllByText("From a public sample")).toHaveLength(1);
  });

  it("draws a bank it has never heard of from what the provider says (G21)", async () => {
    const uob = { id: "uob-one", label: "UOB One account", parser: "uob", bank: "uob" as const, title: "UOB One", status: SAMPLE };
    render(<Setup state={{ ...state, providers: [...state.providers, uob] }} onDone={() => {}} />);
    const box = screen.getByLabelText("UOB One account");
    expect(box.closest("label")!.querySelector("[data-bank='uob']")).toHaveTextContent("U");
    await userEvent.click(box);
    expect(screen.getByText("1 selected")).toBeInTheDocument();
  });
});
