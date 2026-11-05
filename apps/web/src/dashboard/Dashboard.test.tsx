import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { revalidate, type DashboardLayout, type DashboardLayouts } from "../api";
import { Toaster } from "../components/Toast";
import { keepEditing, navigate } from "../router";
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

  it("keeps an edit made while its save was still on the wire and failed", async () => {
    const saved = {
      layouts: [doc("default", "Default", [
        { id: "w-note", type: "note", title: "A note", options: { text: "kept" }, layout: { x: 0, y: 0, w: 4, h: 2 } },
      ])],
      active: "default",
    };
    const posts: { url: string; body: unknown }[] = [];
    let gate: ((e: unknown) => void) | null = null;
    let held = true;
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
        return ok({ layouts: saved.layouts, active: saved.active });
      }
      const one = url.match(/^\/api\/dashboard\/layouts\/([a-z0-9_-]+)$/);
      if (one && init?.method === "POST") {
        const body = JSON.parse(String(init.body));
        posts.push({ url, body });
        // the first save is held on the wire until the test lets it fail
        if (held) {
          held = false;
          await new Promise<never>((_, reject) => { gate = reject; });
        }
        const d = doc(one[1], body.name, body.widgets ?? []);
        saved.layouts = [...saved.layouts.filter((x) => x.id !== d.id), d];
        if (body.active) saved.active = d.id;
        return ok(d);
      }
      if (url.startsWith("/api/dashboard/query")) return ok(ANSWER);
      return new Response(JSON.stringify({ error: "not here" }), { status: 404 });
    });
    render(<><Dashboard /><Toaster /></>);
    await screen.findByText("kept");
    fireEvent.click(screen.getByRole("button", { name: "Remove A note" }));
    await waitFor(() => expect(posts).toHaveLength(1), { timeout: 3000 });
    // the widget comes back while the remove's save is still waiting
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    gate!(new Error("the wire dropped"));
    // the failed snapshot must not replace the undo: the board saves it next
    await waitFor(() => expect(saved.layouts[0].widgets.map((w) => w.id)).toEqual(["w-note"]), { timeout: 3000 });
  });

  it("restores the board a widget was removed from, not the one on screen", async () => {
    const saved = {
      layouts: [
        doc("default", "Default", [
          { id: "w-a", type: "note", title: "Note A", options: { text: "first board" }, layout: { x: 0, y: 0, w: 4, h: 2 } },
        ]),
        doc("board-b", "Second", [
          { id: "w-b", type: "note", title: "Note B", options: { text: "second board" }, layout: { x: 0, y: 0, w: 4, h: 2 } },
        ]),
      ],
      active: "default",
    };
    stubServer(saved);
    render(<><Dashboard /><Toaster /></>);
    await screen.findByText("first board");
    fireEvent.click(screen.getByRole("button", { name: "Remove Note A" }));
    fireEvent.click(screen.getByRole("button", { name: "Layout" }));
    fireEvent.mouseDown(await screen.findByRole("option", { name: "Second" }));
    await screen.findByText("second board");
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    await waitFor(() => {
      const a = saved.layouts.find((d) => d.id === "default")!;
      const b = saved.layouts.find((d) => d.id === "board-b")!;
      expect(a.widgets.map((w) => w.id)).toEqual(["w-a"]);
      expect(b.widgets.map((w) => w.id)).toEqual(["w-b"]);
    }, { timeout: 3000 });
    // the undo saved the board left behind without making it the one the next visit opens
    expect(saved.active).toBe("board-b");
  });

  it("switches board by writing that board alone, never this tab's copy of the others", async () => {
    const saved = {
      layouts: [
        doc("default", "Default", [
          { id: "w-a", type: "note", title: "Note A", options: { text: "first board" }, layout: { x: 0, y: 0, w: 4, h: 2 } },
        ]),
        doc("board-b", "Second", [
          { id: "w-b", type: "note", title: "Note B", options: { text: "second board" }, layout: { x: 0, y: 0, w: 4, h: 2 } },
        ]),
      ],
      active: "default",
    };
    const posts = stubServer(saved);
    render(<Dashboard />);
    await screen.findByText("first board");
    // another tab edits the first board after this one read it
    saved.layouts = saved.layouts.map((d) => d.id === "default"
      ? { ...d, name: "Edited elsewhere" } : d);
    fireEvent.click(screen.getByRole("button", { name: "Layout" }));
    fireEvent.mouseDown(await screen.findByRole("option", { name: "Second" }));
    await waitFor(() => expect(saved.active).toBe("board-b"));
    expect(posts.map((p) => p.url)).toEqual(["/api/dashboard/layouts/board-b"]);
    expect(saved.layouts.find((d) => d.id === "default")!.name).toBe("Edited elsewhere");
  });

  it("lets the page be left inside the wait before a save, and saves on the way out", async () => {
    const saved = {
      layouts: [doc("default", "Default", [
        { id: "w-note", type: "note", title: "A note", options: { text: "kept" }, layout: { x: 0, y: 0, w: 4, h: 2 } },
      ])],
      active: "default",
    };
    window.history.replaceState(window.history.state, "", "/dashboard");
    stubServer(saved);
    const { unmount } = render(<Dashboard />);
    await screen.findByText("kept");
    fireEvent.click(screen.getByRole("button", { name: "Remove A note" }));
    // no "Leave without saving?": the move goes through at once
    act(() => navigate("/transactions"));
    expect(window.location.pathname).toBe("/transactions");
    unmount();
    await waitFor(() => expect(saved.layouts[0].widgets).toHaveLength(0));
    keepEditing();
  });

  it("makes no board when the picker closes with nothing picked, and the Default one with the first widget", async () => {
    const saved = { layouts: [] as DashboardLayout[], active: null as string | null };
    const posts = stubServer(saved);
    render(<Dashboard />);
    fireEvent.click(await screen.findByRole("button", { name: /^Add a widget/ }));
    const sheet = await screen.findByRole("dialog", { name: "Add a widget" });
    fireEvent.keyDown(sheet, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Add a widget" })).not.toBeInTheDocument());
    await new Promise((r) => setTimeout(r, 900));
    expect(posts).toEqual([]);
    fireEvent.click(screen.getByRole("button", { name: /^Add a widget/ }));
    fireEvent.click(within(await screen.findByRole("dialog", { name: "Add a widget" })).getByRole("button", { name: /^A note/ }));
    await waitFor(() => expect(saved.layouts.map((d) => [d.name, d.widgets.length])).toEqual([["Default", 1]]), { timeout: 3000 });
    expect(saved.active).toBe("default");
  });

  it("adopts another tab's write that shares our save's whole-second stamp", async () => {
    const saved = {
      layouts: [doc("default", "Default", [
        { id: "w-note", type: "note", title: "A note", options: { text: "kept" }, layout: { x: 0, y: 0, w: 4, h: 2 } },
      ])],
      active: "default",
    };
    stubServer(saved);
    render(<Dashboard />);
    await screen.findByText("kept");
    fireEvent.click(screen.getByRole("button", { name: "Remove A note" }));
    await waitFor(() => expect(saved.layouts[0].widgets).toHaveLength(0), { timeout: 3000 });
    // a write from elsewhere lands under the same updated_at second ours got
    saved.layouts = [{
      ...saved.layouts[0],
      widgets: [{ id: "w-other", type: "note", title: "Other tab", options: { text: "from another tab" }, layout: { x: 0, y: 0, w: 4, h: 2 } }],
    }];
    act(() => revalidate());
    expect(await screen.findByText("from another tab")).toBeInTheDocument();
  });
});
