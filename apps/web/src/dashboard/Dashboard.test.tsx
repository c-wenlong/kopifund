import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { DashboardLayout, DashboardLayouts } from "../api";
import { Toaster } from "../components/Toast";
import { Dashboard } from "./Dashboard";

// G26: the Dashboard page against a stand-in server that keeps what is posted,
// so a second render sees what the first saved, as a reload would. All figures
// and names are invented.

const ok = (data: unknown) => new Response(JSON.stringify(data), { status: 200 });

const ANSWER = {
  source: "net-worth", metric: "total", dimension: "month", unit: "sgd_minor",
  labels: ["2026-09"], series: [{ name: "total", values_minor: [123456] }], range: null,
};

let stamp = 0;
function doc(id: string, name: string, widgets: DashboardLayout["widgets"]): DashboardLayout {
  return { id, name, widgets, updated_at: `2026-10-01T02:03:${String(stamp++).padStart(2, "0")}+00:00` };
}

/** A fetch that holds the layouts in `saved`, like the record does. */
function stubServer(saved: { layouts: DashboardLayout[]; active: string | null }) {
  const posts: { url: string; body: unknown }[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    if (url === "/api/dashboard/layouts") {
      if (init?.method === "POST") {
        const body = JSON.parse(String(init.body));
        posts.push({ url, body });
        saved.layouts = body.layouts.map((d: DashboardLayout) => doc(d.id, d.name, d.widgets));
        saved.active = body.active ?? saved.active;
        return ok({ layouts: saved.layouts, active: saved.active });
      }
      return ok({ layouts: saved.layouts, active: saved.active } satisfies DashboardLayouts);
    }
    const one = url.match(/^\/api\/dashboard\/layouts\/([a-z0-9_-]+)$/);
    if (one && init?.method === "POST") {
      const body = JSON.parse(String(init.body));
      posts.push({ url, body });
      const d = doc(one[1], body.name, body.widgets ?? []);
      saved.layouts = [...saved.layouts.filter((x) => x.id !== d.id), d];
      if (body.active) saved.active = d.id;
      return ok(d);
    }
    if (url.startsWith("/api/dashboard/query")) return ok(ANSWER);
    return new Response(JSON.stringify({ error: "not here" }), { status: 404 });
  });
  return posts;
}

const lastPost = (posts: { url: string; body: unknown }[]) => posts.at(-1);

describe("Dashboard", () => {
  it("draws the saved layout's widgets", async () => {
    stubServer({
      layouts: [doc("default", "Default", [
        { id: "w-note", type: "note", title: "A note", options: { text: "invented words" }, layout: { x: 0, y: 0, w: 4, h: 2 } },
      ])],
      active: "default",
    });
    render(<Dashboard />);
    expect(await screen.findByText("invented words")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "A note" })).toBeInTheDocument();
  });

  it("adds a widget from the sheet and saves the board a beat later", async () => {
    const saved = {
      layouts: [doc("default", "Default", [
        { id: "w-note", type: "note", title: "A note", options: { text: "kept" }, layout: { x: 0, y: 0, w: 4, h: 2 } },
      ])],
      active: "default",
    };
    const posts = stubServer(saved);
    render(<Dashboard />);
    await screen.findByText("kept");
    fireEvent.click(screen.getByRole("button", { name: /Add widget/ }));
    const sheet = await screen.findByRole("dialog", { name: "Add a widget" });
    fireEvent.click(within(sheet).getByRole("button", { name: /^A note/ }));
    // the new card is on the board, and the layout wrote itself back
    await waitFor(() => expect(saved.layouts[0].widgets).toHaveLength(2), { timeout: 3000 });
    expect(lastPost(posts)?.url).toBe("/api/dashboard/layouts/default");
    const body = lastPost(posts)?.body as { name: string; widgets: { type: string }[]; active: boolean };
    expect(body.name).toBe("Default");
    expect(body.widgets).toHaveLength(2);
    // and a second open, as after a reload, shows both widgets
    const again = render(<Dashboard />);
    expect((await again.findAllByRole("heading", { name: "A note" })).length).toBeGreaterThanOrEqual(2);
  });

  it("removes a widget and its Undo puts it back", async () => {
    const saved = {
      layouts: [doc("default", "Default", [
        { id: "w-note", type: "note", title: "A note", options: { text: "kept" }, layout: { x: 0, y: 0, w: 4, h: 2 } },
      ])],
      active: "default",
    };
    stubServer(saved);
    render(<><Dashboard /><Toaster /></>);
    await screen.findByText("kept");
    fireEvent.click(screen.getByRole("button", { name: "Remove A note" }));
    await waitFor(() => expect(saved.layouts[0].widgets).toHaveLength(0), { timeout: 3000 });
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    await waitFor(() => expect(saved.layouts[0].widgets).toHaveLength(1), { timeout: 3000 });
  });

  it("starts an empty record from a preset, saved as the active layout", async () => {
    const saved: { layouts: DashboardLayout[]; active: string | null } = { layouts: [], active: null };
    stubServer(saved);
    render(<Dashboard />);
    expect(await screen.findByText("No dashboard yet")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Spending/ }));
    await waitFor(() => expect(saved.layouts).toHaveLength(1), { timeout: 3000 });
    expect(saved.layouts[0].name).toBe("Spending");
    expect(saved.layouts[0].widgets.length).toBeGreaterThan(0);
    expect(saved.active).toBe(saved.layouts[0].id);
  });

  it("resets a board to the default preset", async () => {
    const saved = {
      layouts: [doc("default", "Default", [
        { id: "w-note", type: "note", title: "A note", options: { text: "kept" }, layout: { x: 0, y: 0, w: 4, h: 2 } },
      ])],
      active: "default",
    };
    stubServer(saved);
    render(<Dashboard />);
    await screen.findByText("kept");
    fireEvent.click(screen.getByRole("button", { name: /Reset/ }));
    await waitFor(() => expect(saved.layouts[0].widgets.length).toBeGreaterThan(1), { timeout: 3000 });
    expect(await screen.findByRole("heading", { name: "Net worth" })).toBeInTheDocument();
  });
});
