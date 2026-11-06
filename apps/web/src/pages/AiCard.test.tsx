import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { choose } from "../test-select";
import { notifyChanged } from "../api";
import type { AiKey, AiSettings } from "../types.gen";
import { AiCard } from "./AiCard";
import { expectAsks, expectFree, json } from "../test-helpers";

// G22: the AI card on Settings: provider, models, the key (never shown), Test and consent.

const providers: AiSettings["providers"] = [
  { name: "anthropic", label: "Anthropic", local: false, default_model: "claude-sonnet-4-5", copilot_default_model: "claude-opus-5-5" },
  { name: "openai", label: "OpenAI", local: false, default_model: "gpt-5", copilot_default_model: "" },
  { name: "ollama", label: "Ollama", local: true, default_model: "", copilot_default_model: "" },
];
const settings = (key: AiKey, more: Partial<AiSettings> = {}): AiSettings => ({
  enabled: true, provider: "anthropic", providers, model: "", copilot_model: "", model_in_use: "claude-sonnet-4-5", copilot_model_in_use: "claude-opus-5-5", key, send_rows: false, local: false, from_config: false, config_provider: null, ...more,
});
const keychain: AiKey = { where: "keychain", last4: "9xQz", can_remove: true };
const none: AiKey = { where: "none", last4: null, can_remove: false };

const posts: { url: string; body: unknown }[] = [];
function stub(first: AiSettings, after: (url: string, body: Record<string, unknown>) => AiSettings | object = () => first) {
  posts.length = 0;
  let now = first;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      const body = init.body ? JSON.parse(init.body as string) : {};
      posts.push({ url, body });
      const out = after(url, body);
      if ("providers" in out) now = out as AiSettings;
      return json(out);
    }
    if (url === "/api/ai") return json(now);
    return json({ provider: "anthropic", models: [], fetched_at: null, error: null });
  }));
}

const card = async () => (await screen.findByRole("heading", { name: "AI" })).closest("section")!;

describe("the AI card's key", () => {
  it("with no key, offers a password box and forgets the key once sent", async () => {
    stub(settings(none), (url) => (url === "/api/ai/key" ? settings(keychain) : settings(none)));
    render(<AiCard />);
    const c = await card();
    const input = within(c).getByLabelText("API key");
    expect(input).toHaveAttribute("type", "password");
    expect(within(c).queryByRole("button", { name: "Cancel" })).not.toBeInTheDocument();
    fireEvent.change(input, { target: { value: "sk-ant-secret-9xQz" } });
    fireEvent.click(within(c).getByRole("button", { name: "Save key" }));
    await waitFor(() => expect(posts).toContainEqual({ url: "/api/ai/key", body: { provider: "anthropic", key: "sk-ant-secret-9xQz" } }));
    expect(await within(c).findByText("Saved in your Keychain · ends in 9xQz")).toBeInTheDocument();
    expect(c.innerHTML).not.toContain("sk-ant-secret");
  });

  it("names a Keychain key by its last four, with Replace, and Remove that asks for a second press", async () => {
    stub(settings(keychain), (url) => (url === "/api/ai/key/remove" ? settings(none) : settings(keychain)));
    render(<AiCard />);
    const c = await card();
    expect(within(c).getByText("Saved in your Keychain · ends in 9xQz")).toBeInTheDocument();
    fireEvent.click(within(c).getByRole("button", { name: "Replace" }));
    expect(within(c).getByLabelText("API key")).toHaveValue("");
    fireEvent.click(within(c).getByRole("button", { name: "Cancel" }));
    fireEvent.click(within(c).getByRole("button", { name: "Remove" }));
    expect(posts).toEqual([]);
    fireEvent.click(within(c).getByRole("button", { name: "Press again to remove" }));
    await waitFor(() => expect(posts).toEqual([{ url: "/api/ai/key/remove", body: { provider: "anthropic" } }]));
    expect(await within(c).findByLabelText("API key")).toBeInTheDocument();
  });

  it("names a key from the environment plainly, with no Remove", async () => {
    stub(settings({ where: "environment", last4: "abcd", can_remove: false }));
    render(<AiCard />);
    const c = await card();
    expect(within(c).getByText("Found in your environment")).toBeInTheDocument();
    expect(within(c).queryByText(/abcd/)).not.toBeInTheDocument();
    expect(within(c).queryByRole("button", { name: "Remove" })).not.toBeInTheDocument();
  });

  it("names a key in the data folder's .env", async () => {
    stub(settings({ where: "env_file", last4: "Zz12", can_remove: true }));
    render(<AiCard />);
    expect(within(await card()).getByText("Saved in the data folder's .env · ends in Zz12")).toBeInTheDocument();
  });
});

describe("the AI card's settings", () => {
  it("saves the provider, models and consent with Save, and shows no key row or consent for a local provider", async () => {
    stub(settings(keychain), (_url, body) => settings(keychain, body as Partial<AiSettings>));
    render(<AiCard />);
    const c = await card();
    const consent = within(c).getByRole("checkbox", { name: "Send matching rows to Anthropic" });
    expect(within(c).getByText(/never account numbers or memo lines/)).toBeInTheDocument();
    fireEvent.click(consent);
    expect(within(c).getByText("Unsaved changes")).toBeInTheDocument();
    fireEvent.change(within(c).getByRole("combobox", { name: "Ask Kopifund model" }), { target: { value: "claude-opus-4-1" } });
    fireEvent.click(within(c).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(posts).toContainEqual({ url: "/api/ai/settings", body: {
      provider: "anthropic", model: "", copilot_model: "claude-opus-4-1", send_rows: true,
    } }));
    expect(await within(c).findByText("Saved")).toBeInTheDocument();
    choose(within(c).getByRole("button", { name: "Provider" }), "Ollama");
    expect(within(c).queryByLabelText("API key")).not.toBeInTheDocument();
    expect(within(c).queryByText("Saved in your Keychain · ends in 9xQz")).not.toBeInTheDocument();
    expect(within(c).queryByRole("checkbox")).not.toBeInTheDocument();
    fireEvent.click(within(c).getByRole("button", { name: "Discard" }));
    expect(within(c).getByRole("checkbox", { name: "Send matching rows to Anthropic" })).toBeChecked();
  });

  it("keeps what is typed while a save is on its way, a box set back to the old saved value included", async () => {
    stub(settings(keychain));
    let release: () => void = () => {};
    const ask = globalThis.fetch;
    let now = settings(keychain);
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      if (url === "/api/ai/settings" && init?.method === "POST") {
        const body = JSON.parse(String(init.body));
        return new Promise<Response>((done) => {
          release = () => { now = settings(keychain, body); done(json(now)); };
        });
      }
      if (url === "/api/ai") return json(now);
      return ask(url, init);
    }));
    render(<AiCard />);
    const c = await card();
    fireEvent.change(within(c).getByRole("combobox", { name: "Ask Kopifund model" }), { target: { value: "claude-opus-4-1" } });
    fireEvent.click(within(c).getByRole("button", { name: "Save" }));
    // typed while the save is on its way: the consent ticked, and the model set back to the old saved one (none)
    fireEvent.click(within(c).getByRole("checkbox", { name: "Send matching rows to Anthropic" }));
    fireEvent.change(within(c).getByRole("combobox", { name: "Ask Kopifund model" }), { target: { value: "" } });
    await act(async () => release());
    await waitFor(() => expect(now.copilot_model).toBe("claude-opus-4-1"));
    await waitFor(() => expect(within(c).getByRole("button", { name: "Save" })).toBeEnabled());
    expect(within(c).getByRole("checkbox", { name: "Send matching rows to Anthropic" })).toBeChecked();
    expect(within(c).getByRole("combobox", { name: "Ask Kopifund model" })).toHaveValue("");
    expect(within(c).getByText("Unsaved changes")).toBeInTheDocument();
  });

  it("counts a box set back to the old value during a save as unsaved, before and after the save lands", async () => {
    stub(settings(keychain));
    let releasePost: () => void = () => {};
    let releaseRead: (() => void) | null = null;
    let posted = false;
    const ask = globalThis.fetch;
    let now = settings(keychain);
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      if (url === "/api/ai/settings" && init?.method === "POST") {
        const body = JSON.parse(String(init.body));
        return new Promise<Response>((done) => {
          releasePost = () => { now = settings(keychain, body); posted = true; done(json(now)); };
        });
      }
      // the settings asked for again after the save wait until let go
      if (url === "/api/ai" && posted) return new Promise<Response>((done) => { releaseRead = () => done(json(now)); });
      if (url === "/api/ai") return json(now);
      return ask(url, init);
    }));
    render(<AiCard />);
    const c = await card();
    const model = within(c).getByRole("combobox", { name: "Ask Kopifund model" });
    fireEvent.change(model, { target: { value: "claude-opus-4-1" } });
    fireEvent.click(within(c).getByRole("button", { name: "Save" }));
    // back to none while the save that sets claude-opus-4-1 is on its way: an edit, so leaving asks
    fireEvent.change(model, { target: { value: "" } });
    expect(within(c).getByText("Unsaved changes")).toBeInTheDocument();
    // the save lands; another box changed before the settings are read again keeps the first edit
    await act(async () => releasePost());
    fireEvent.click(within(c).getByRole("checkbox", { name: "Send matching rows to Anthropic" }));
    expect(model).toHaveValue("");
    await waitFor(() => expect(releaseRead).not.toBeNull());
    await act(async () => releaseRead!());
    expect(model).toHaveValue("");
    expect(within(c).getByText("Unsaved changes")).toBeInTheDocument();
  });

  it("lets a failed save leave no edit behind for a box set back to the saved value meanwhile", async () => {
    let fail: (() => void) | null = null;
    let now = settings(keychain);
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      if (url === "/api/ai/settings" && init?.method === "POST") {
        return new Promise<Response>((done) => { fail = () => done(json({ error: "disk full" }, 500)); });
      }
      if (url === "/api/ai") return json(now);
      return json({ provider: "anthropic", models: [], fetched_at: null, error: null });
    }));
    render(<AiCard />);
    const c = await card();
    fireEvent.change(within(c).getByRole("combobox", { name: "Ask Kopifund model" }), { target: { value: "claude-opus-4-1" } });
    fireEvent.click(within(c).getByRole("button", { name: "Save" }));
    // a save on its way may still fail: leaving asks until it lands
    await expectAsks();
    // the consent ticked and unticked while the save is on its way
    const consent = within(c).getByRole("checkbox", { name: "Send matching rows to Anthropic" });
    fireEvent.click(consent);
    fireEvent.click(consent);
    await waitFor(() => expect(fail).not.toBeNull());
    await act(async () => fail!());
    expect(await within(c).findByText("disk full")).toBeInTheDocument();
    // it failed: the model typed is still unsaved
    await expectAsks();
    // another tab turns the consent on: the card follows, since nothing here holds it off
    now = settings(keychain, { send_rows: true });
    await act(async () => notifyChanged());
    await waitFor(() => expect(within(c).getByRole("checkbox", { name: "Send matching rows to Anthropic" })).toBeChecked());
  });

  it("tests the provider and says how it went", async () => {
    stub(settings(keychain), (url) => (url === "/api/ai/test" ? { ok: true, message: "claude-sonnet-4-5 answered", ms: 812 } : settings(keychain)));
    render(<AiCard />);
    const c = await card();
    fireEvent.click(within(c).getByRole("button", { name: "Test" }));
    expect(await within(c).findByRole("status")).toHaveTextContent("Answered: claude-sonnet-4-5 answered · 812 ms");
  });
});

describe("the AI card's model fields", () => {
  it("shows the model the copilot really uses when none is picked", async () => {
    stub(settings(keychain));
    render(<AiCard />);
    const c = await card();
    expect(within(c).getByRole("combobox", { name: "Ask Kopifund model" })).toHaveAttribute("placeholder", "claude-opus-5-5");
    expect(within(c).getByRole("combobox", { name: "Payee suggestions model" })).toHaveAttribute("placeholder", "claude-sonnet-4-5");
  });

  it("shows the models requests really use, one inherited from config.toml included", async () => {
    stub(settings(keychain, { provider: "openai", model_in_use: "gpt-5-mini", copilot_model_in_use: "gpt-5-mini", from_config: true }));
    render(<AiCard />);
    const c = await card();
    expect(within(c).getByRole("combobox", { name: "Payee suggestions model" })).toHaveAttribute("placeholder", "gpt-5-mini");
    expect(within(c).getByRole("combobox", { name: "Ask Kopifund model" })).toHaveAttribute("placeholder", "gpt-5-mini");
    expect(within(c).queryByText(/Ask Kopifund needs a model/)).not.toBeInTheDocument();
  });

  it("says Ask Kopifund needs a model when requests have none to use", async () => {
    stub(settings(keychain, { provider: "openai", model_in_use: "", copilot_model_in_use: "" }));
    render(<AiCard />);
    const c = await card();
    expect(within(c).getByText("Ask Kopifund needs a model: type one above.")).toBeInTheDocument();
    fireEvent.change(within(c).getByRole("combobox", { name: "Ask Kopifund model" }), { target: { value: "gpt-5" } });
    expect(within(c).queryByText(/Ask Kopifund needs a model/)).not.toBeInTheDocument();
  });

  it("asks for the model list again after a key is saved", async () => {
    stub(settings(none), (url) => (url === "/api/ai/key" ? settings(keychain) : settings(none)));
    render(<AiCard />);
    const c = await card();
    const field = within(c).getByRole("combobox", { name: "Payee suggestions model" });
    const listed = () => vi.mocked(fetch).mock.calls.filter(([u]) => String(u).startsWith("/api/ai/models")).length;
    fireEvent.focus(field);
    await waitFor(() => expect(listed()).toBe(1));
    fireEvent.blur(field);
    fireEvent.change(within(c).getByLabelText("API key"), { target: { value: "sk-ant-secret-9xQz" } });
    fireEvent.click(within(c).getByRole("button", { name: "Save key" }));
    expect(await within(c).findByText("Saved in your Keychain · ends in 9xQz")).toBeInTheDocument();
    fireEvent.focus(within(c).getByRole("combobox", { name: "Payee suggestions model" }));
    await waitFor(() => expect(listed()).toBe(2));
  });
});

describe("turning AI off", () => {
  it("offers Off, which saves no provider and keeps the models", async () => {
    stub(settings(keychain, { model: "claude-sonnet-4-5" }), (_url, body) => settings(keychain, { ...body, enabled: false, model: "claude-sonnet-4-5" } as Partial<AiSettings>));
    render(<AiCard />);
    const c = await card();
    choose(within(c).getByRole("button", { name: "Provider" }), "Off");
    expect(within(c).queryByRole("combobox", { name: "Ask Kopifund model" })).not.toBeInTheDocument();
    expect(within(c).queryByLabelText("API key")).not.toBeInTheDocument();
    fireEvent.click(within(c).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(posts).toContainEqual({ url: "/api/ai/settings", body: { provider: null } }));
    // the server says AI is off: the card shows Off, with nothing to test and no unsaved change
    await waitFor(() => expect(within(c).getByRole("button", { name: "Provider" })).toHaveTextContent("Off"));
    expect(within(c).queryByText("Unsaved changes")).not.toBeInTheDocument();
    // Off shows as the save lands; Test goes once the settings are read again
    await waitFor(() => expect(within(c).queryByRole("button", { name: "Test" })).not.toBeInTheDocument());
  });

  it("shows Off when AI is off, though config.toml names a provider, and a provider turns it on", async () => {
    stub(settings(keychain, { enabled: false, from_config: true }), (_url, body) => settings(keychain, body as Partial<AiSettings>));
    render(<AiCard />);
    const c = await card();
    expect(within(c).getByRole("button", { name: "Provider" })).toHaveTextContent("Off");
    expect(within(c).queryByRole("button", { name: "Test" })).not.toBeInTheDocument();
    choose(within(c).getByRole("button", { name: "Provider" }), "Anthropic");
    fireEvent.click(within(c).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(posts).toContainEqual({ url: "/api/ai/settings", body: {
      provider: "anthropic", model: "", copilot_model: "", send_rows: false,
    } }));
  });

});

describe("a custom provider from config.toml", () => {
  const lab = (more: Partial<AiSettings> = {}) => settings(none, {
    provider: "mylab", from_config: true, config_provider: "mylab", model: "lab-1", model_in_use: "lab-1", copilot_model_in_use: "lab-1", ...more,
  });
  const asked = () => vi.mocked(fetch).mock.calls.map(([u]) => String(u)).filter((u) => u.startsWith("/api/ai/key") || u.startsWith("/api/ai/models"));

  it("is named, says its model is set in config.toml, and offers no model field or key box", async () => {
    stub(lab());
    render(<AiCard />);
    const c = await card();
    expect(within(c).getByRole("button", { name: "Provider" })).toHaveTextContent("mylab");
    expect(within(c).getByText("Set in config.toml: model lab-1")).toBeInTheDocument();
    expect(within(c).queryByRole("combobox")).not.toBeInTheDocument();
    expect(within(c).queryByLabelText("API key")).not.toBeInTheDocument();
    expect(within(c).queryByRole("button", { name: "Save key" })).not.toBeInTheDocument();
    expect(within(c).queryByRole("button", { name: "Remove" })).not.toBeInTheDocument();
    expect(within(c).getByRole("checkbox", { name: "Send matching rows to mylab" })).toBeInTheDocument();
    expect(asked()).toEqual([]);
  });

  it("saves only the provider and consent, never the models", async () => {
    stub(lab(), (_url, body) => lab(body as Partial<AiSettings>));
    render(<AiCard />);
    const c = await card();
    fireEvent.click(within(c).getByRole("checkbox", { name: "Send matching rows to mylab" }));
    fireEvent.click(within(c).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(posts).toEqual([{ url: "/api/ai/settings", body: { provider: "mylab", send_rows: true } }]));
    expect(asked()).toEqual([]);
  });

  it("stays on the list while AI is off, so picking it turns AI back on", async () => {
    stub(lab({ enabled: false, provider: null, from_config: false, model_in_use: "", copilot_model_in_use: "", model: "" }),
      (_url, body) => lab(body as Partial<AiSettings>));
    render(<AiCard />);
    const c = await card();
    expect(within(c).getByRole("button", { name: "Provider" })).toHaveTextContent("Off");
    choose(within(c).getByRole("button", { name: "Provider" }), "mylab");
    expect(within(c).getByText("Set in config.toml")).toBeInTheDocument();
    expect(within(c).queryByRole("combobox")).not.toBeInTheDocument();
    expect(within(c).queryByLabelText("API key")).not.toBeInTheDocument();
    fireEvent.click(within(c).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(posts).toEqual([{ url: "/api/ai/settings", body: { provider: "mylab", send_rows: false } }]));
    await waitFor(() => expect(within(c).getByText("Set in config.toml: model lab-1")).toBeInTheDocument());
    expect(within(c).queryByText("Unsaved changes")).not.toBeInTheDocument();
    expect(asked()).toEqual([]);
  });

  it("on this machine (a localhost base_url) needs no key and no consent", async () => {
    stub(lab({ local: true }));
    render(<AiCard />);
    const c = await card();
    expect(within(c).getByText("Set in config.toml: model lab-1")).toBeInTheDocument();
    expect(within(c).queryByRole("checkbox")).not.toBeInTheDocument();
    expect(within(c).queryByLabelText("API key")).not.toBeInTheDocument();
  });
});

// each write on the card can be refused, and says why where it was pressed
describe("the AI card when a write is refused", () => {
  /** a card whose POSTs to `refused` answer 500 with `message` until `state.on` is turned off */
  function refusing(first: AiSettings, refused: string, message: string) {
    const state = { on: true };
    stub(first, (url, body) => (url === "/api/ai/settings" ? settings(keychain, body as Partial<AiSettings>) : first));
    const real = globalThis.fetch;
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method === "POST" && url === refused && state.on) {
        posts.push({ url, body: init.body ? JSON.parse(init.body as string) : {} });
        return json({ error: message }, 500);
      }
      return real(url, init);
    }));
    return state;
  }

  it("says why the settings were not saved, keeps the edit unsaved, and saves it on a second try", async () => {
    const state = refusing(settings(keychain), "/api/ai/settings", "config.toml is read only.");
    render(<AiCard />);
    const c = await card();
    fireEvent.click(within(c).getByRole("checkbox", { name: "Send matching rows to Anthropic" }));
    fireEvent.click(within(c).getByRole("button", { name: "Save" }));
    expect(await within(c).findByText("config.toml is read only.")).toBeInTheDocument();
    expect(within(c).getByText("Unsaved changes")).toBeInTheDocument();
    expect(within(c).getByRole("checkbox", { name: "Send matching rows to Anthropic" })).toBeChecked();
    expect(within(c).getByRole("button", { name: "Save" })).toBeEnabled();
    await expectAsks();
    state.on = false;
    fireEvent.click(within(c).getByRole("button", { name: "Save" }));
    expect(await within(c).findByText("Saved")).toBeInTheDocument();
    await expectFree();
  });

  it("clears the reason when the edit is changed or discarded", async () => {
    refusing(settings(keychain), "/api/ai/settings", "config.toml is read only.");
    render(<AiCard />);
    const c = await card();
    const consent = () => within(c).getByRole("checkbox", { name: "Send matching rows to Anthropic" });
    fireEvent.click(consent());
    fireEvent.click(within(c).getByRole("button", { name: "Save" }));
    expect(await within(c).findByText("config.toml is read only.")).toBeInTheDocument();
    fireEvent.click(within(c).getByRole("button", { name: "Discard" }));
    expect(within(c).queryByText("config.toml is read only.")).not.toBeInTheDocument();
    expect(consent()).not.toBeChecked();
  });

  it("says why a key was not saved, and still forgets what was typed", async () => {
    const state = refusing(settings(none), "/api/ai/key", "The Keychain is locked.");
    render(<AiCard />);
    const c = await card();
    fireEvent.change(within(c).getByLabelText("API key"), { target: { value: "sk-ant-secret-9xQz" } });
    fireEvent.click(within(c).getByRole("button", { name: "Save key" }));
    expect(await within(c).findByText("The Keychain is locked.")).toBeInTheDocument();
    // the key left the page's state as it was sent, whatever the answer
    expect(within(c).getByLabelText("API key")).toHaveValue("");
    expect(c.innerHTML).not.toContain("sk-ant-secret");
    expect(within(c).getByRole("button", { name: "Save key" })).toBeDisabled();
    state.on = false;
  });

  it("says why a key was not removed, and keeps the key row", async () => {
    refusing(settings(keychain), "/api/ai/key/remove", "The Keychain is locked.");
    render(<AiCard />);
    const c = await card();
    fireEvent.click(within(c).getByRole("button", { name: "Remove" }));
    fireEvent.click(within(c).getByRole("button", { name: "Press again to remove" }));
    expect(await within(c).findByText("The Keychain is locked.")).toBeInTheDocument();
    expect(within(c).getByText("Saved in your Keychain · ends in 9xQz")).toBeInTheDocument();
    expect(within(c).getByRole("button", { name: "Remove" })).toBeEnabled();
  });

  it("says Failed with the reason when the test is refused", async () => {
    refusing(settings(keychain), "/api/ai/test", "The provider did not answer.");
    render(<AiCard />);
    const c = await card();
    fireEvent.click(within(c).getByRole("button", { name: "Test" }));
    expect(await within(c).findByRole("status")).toHaveTextContent("Failed: The provider did not answer.");
    expect(within(c).getByRole("button", { name: "Test" })).toBeEnabled();
  });
});
