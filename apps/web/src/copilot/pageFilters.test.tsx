import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Plan } from "../pages/Plan";
import { SpendingExplorer } from "../pages/SpendingExplorer";
import type { AiSettings, CopilotEvent } from "../types.gen";
import { CopilotSide } from "./CopilotPanel";
import { useCopilot } from "./useCopilot";
import { json } from "../test-helpers";

// G22: a question carries what the page shows, selections it keeps in state included, not only its address.

const ai: AiSettings = {
  enabled: true, provider: "anthropic", model: "", copilot_model: "", model_in_use: "m", copilot_model_in_use: "m",
  providers: [{ name: "anthropic", label: "Anthropic", local: false, default_model: "m", copilot_default_model: "m" }],
  key: { where: "keychain", last4: "9xQz", can_remove: true }, send_rows: true, local: false, from_config: false,
  config_provider: null,
};
const events: CopilotEvent[] = [{ type: "text", text: "ok" }, { type: "done" }];
const done = () => new Response(events.map((e) => JSON.stringify(e)).join("\n"), { status: 200 });

const asks: { page: { filters: Record<string, string> } }[] = [];
function stub(more: (url: string) => Response | undefined) {
  asks.length = 0;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    if (url === "/api/copilot/ask") {
      asks.push(JSON.parse(init!.body as string));
      return done();
    }
    if (url === "/api/ai") return json(ai);
    return more(url) ?? json({ error: "not here" }, 404);
  }));
}

function WithPanel({ children, path }: { children: React.ReactNode; path: string }) {
  const copilot = useCopilot();
  return <>{children}<CopilotSide copilot={copilot} path={path} title="Page" onClose={() => {}} /></>;
}

async function ask() {
  const box = await screen.findByRole("textbox", { name: "Ask about your money" });
  await waitFor(() => expect(box).toBeEnabled());
  fireEvent.change(box, { target: { value: "And this?" } });
  fireEvent.keyDown(box, { key: "Enter" });
  await waitFor(() => expect(asks.length).toBeGreaterThan(0));
  return asks[asks.length - 1].page.filters;
}

afterEach(() => {
  window.history.pushState(null, "", "/");
});

const spending = (url: string) => {
  if (!url.startsWith("/api/spending")) return undefined;
  const q = new URL(url, "http://x").searchParams;
  return json({ by: q.get("by") ?? "bucket", scope: q.get("scope") ?? "sg", bucket_order: [], bucket_style: {},
    available: { from: "2025-01", to: "2026-09" }, months: [q.get("from") ?? "2026-09", q.get("to") ?? "2026-09"],
    series: [], buckets: [], payees: [], biggest: [], link: {}, totals: { spend: 0, per_month: 0, overseas: 0 } });
};

describe("the page's selections go with a question", () => {
  it("sends Spending's range, scope and split, the default Singapore-only view included", async () => {
    window.history.pushState(null, "", "/spending?view=lines");
    stub(spending);
    render(<WithPanel path="/spending"><SpendingExplorer /></WithPanel>);
    expect(await ask()).toEqual({ view: "lines", from: "2025-10", to: "2026-09", scope: "sg", by: "bucket" });
    fireEvent.click(screen.getByRole("radio", { name: "3M" }));
    fireEvent.click(screen.getByRole("radio", { name: "Include overseas" }));
    fireEvent.click(screen.getByRole("radio", { name: "By payee" }));
    // the controls are in the address now too (range among them); the months they cover are published
    expect(await ask()).toEqual({ view: "lines", range: "3", from: "2026-07", to: "2026-09", scope: "all", by: "payee" });
  });

  it("sends the budget month Plan shows", async () => {
    stub((url) => (url.startsWith("/api/budgets?month=")
      ? json({ month: new URL(url, "http://x").searchParams.get("month"), rows: [], groups: [], months: [], need_classes: [] })
      : undefined));
    render(<WithPanel path="/plan"><Plan asOf="2026-09-30" /></WithPanel>);
    expect(await ask()).toMatchObject({ month: "2026-09" });
    fireEvent.click(screen.getByRole("button", { name: "Previous month" }));
    expect(await ask()).toMatchObject({ month: "2026-08" });
  });

  it("drops a page's selections once it is gone", async () => {
    stub(spending);
    const { rerender } = render(<WithPanel path="/spending"><SpendingExplorer /></WithPanel>);
    expect(await ask()).toMatchObject({ scope: "sg" });
    rerender(<WithPanel path="/spending"><p>Another page</p></WithPanel>);
    expect(await ask()).toEqual({});
  });
});
