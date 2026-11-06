import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { LinkDevice, LinkDevices, PendingRow } from "../types.gen";
import { LinkCard } from "./LinkCard";
import { json } from "../test-helpers";
import { zoneSwitch } from "../test-zone";

const setZone = zoneSwitch();

// G25: the Link a phone card: mint the pair QR, list the phones, revoke one,
// dismiss the waiting phone rows, and the history of every row with its state.

afterEach(() => window.history.replaceState(null, "", "/"));

const phone = (over: Partial<LinkDevice> = {}): LinkDevice => ({
  id: "dev_1", name: "Test iPhone", paired_at: "2026-10-20T10:00:00", revoked: false, ...over,
});
const devices = (ds: LinkDevice[], url = "https://mac.tail-abc.ts.net"): LinkDevices => ({ devices: ds, url });
const pending = (over: Partial<PendingRow> = {}): PendingRow => ({
  import_id: "kfp_1", device_id: "dev_1", device: "Test iPhone", date: "2026-10-20",
  amount_minor: -1250, currency: "SGD", payee: "Grab", state: "waiting", matched_txn_key: null,
  state_changed_at: "2026-10-20T02:00:00+00:00", posted_at: "2026-10-20T02:00:00+00:00", expires_on: "2026-12-19",
  ...over,
});

const posts: { url: string; body: unknown }[] = [];
const gets: string[] = [];
function stub(first: LinkDevices, post: Record<string, object> = {}, firstPending: PendingRow[] = []) {
  posts.length = 0;
  gets.length = 0;
  let now = first;
  let pendNow = firstPending;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const path = url.split("?")[0];
    if (init?.method === "POST") {
      posts.push({ url, body: init.body ? JSON.parse(init.body as string) : {} });
      const out = post[url];
      if (out) {
        if ("devices" in out) now = out as LinkDevices;
        if ("rows" in out) pendNow = (out as { rows: PendingRow[] }).rows;
        return json(out);
      }
      // a pending write moves its row the way the hub would, then answers the list
      if (path === "/api/link/pending/dismiss" || path === "/api/link/pending/restore") {
        const { import_id } = JSON.parse(init.body as string) as { import_id: string };
        const to = path.endsWith("dismiss") ? "dismissed" : "waiting";
        pendNow = pendNow.map((r) => (r.import_id === import_id
          ? { ...r, state: to, matched_txn_key: to === "waiting" ? null : r.matched_txn_key,
              state_changed_at: "2026-11-02T02:00:00+00:00", expires_on: to === "waiting" ? "2027-01-01" : null } : r));
        return json({ rows: pendNow });
      }
      return json({});
    }
    gets.push(url);
    if (path === "/api/link/pending") return json({ rows: pendNow });
    return json(now);
  }));
}

const card = async () => (await screen.findByRole("heading", { name: "Link a phone" })).closest("section")!;

describe("the Link a phone card", () => {
  it("mints a pairing code into a QR and a copyable payload", async () => {
    stub(devices([]), {
      "/api/link/pair-token": { payload: "kopifund://pair?url=https%3A%2F%2Fmac.tail-abc.ts.net&token=kfp_abc", url: "https://mac.tail-abc.ts.net", expires_in: 600 },
    });
    render(<LinkCard />);
    const c = await card();
    expect(within(c).queryByLabelText("Pairing code")).not.toBeInTheDocument();
    await userEvent.click(within(c).getByRole("button", { name: "Pair a phone" }));
    expect(posts).toEqual([{ url: "/api/link/pair-token", body: {} }]);
    expect(await within(c).findByLabelText("Pairing code")).toBeInTheDocument();
    expect(within(c).getByText(/kopifund:\/\/pair/)).toBeInTheDocument();
    expect(within(c).getByText(/once, within 10 minutes/)).toBeInTheDocument();
    expect(within(c).getByRole("button", { name: "New pairing code" })).toBeInTheDocument();
  });

  it("says the address is missing when tailscale cannot name the Mac", async () => {
    stub(devices([], ""));
    render(<LinkCard />);
    const c = await card();
    expect(await within(c).findByText(/Tailscale cannot name this Mac/)).toBeInTheDocument();
    // a code with no address pairs nothing, so none is minted
    expect(within(c).getByRole("button", { name: "Pair a phone" })).toBeDisabled();
  });

  it("dismisses every waiting row on the second press", async () => {
    stub(devices([phone()]), {}, [pending(), pending({ import_id: "kfp_2", payee: "Kopitiam" }),
      pending({ import_id: "kfp_3", payee: "Old", state: "matched", matched_txn_key: "k9" })]);
    const fetchMock = vi.mocked(fetch);
    const base = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation(async (url, init) => {
      if (url === "/api/link/pending/dismiss-all") {
        posts.push({ url: url as string, body: {} });
        return json({ rows: [pending({ state: "dismissed" }),
          pending({ import_id: "kfp_2", payee: "Kopitiam", state: "dismissed" }),
          pending({ import_id: "kfp_3", payee: "Old", state: "matched", matched_txn_key: "k9" })] });
      }
      return base(url as string, init);
    });
    render(<LinkCard />);
    const c = await card();
    expect(await within(c).findByText(/2 phone rows are waiting for a statement/)).toBeInTheDocument();
    await userEvent.click(within(c).getByRole("button", { name: "Dismiss all waiting" }));
    expect(posts).toEqual([]);
    await userEvent.click(within(c).getByRole("button", { name: "Press again to dismiss 2" }));
    await waitFor(() => expect(posts).toEqual([{ url: "/api/link/pending/dismiss-all", body: {} }]));
  });

  it("lists paired phones and revokes one on the second press", async () => {
    stub(devices([phone(), phone({ id: "dev_2", name: "Old iPad", revoked: true })]));
    render(<LinkCard />);
    const c = await card();
    const row = (await within(c).findByLabelText("Paired phones")).querySelector("li")!;
    expect(within(row).getByText("Test iPhone")).toBeInTheDocument();
    expect(within(c).getByText("revoked")).toBeInTheDocument();
    expect(within(c).queryByRole("button", { name: "Revoke Old iPad" })).not.toBeInTheDocument();
    const revoke = within(c).getByRole("button", { name: "Revoke Test iPhone" });
    await userEvent.click(revoke);
    expect(posts).toHaveLength(0);
    await userEvent.click(within(c).getByRole("button", { name: "Press again to revoke Test iPhone" }));
    await waitFor(() => expect(posts).toEqual([{ url: "/api/link/devices/revoke", body: { id: "dev_1" } }]));
  });

  it("says the day a phone was paired in this device's time", async () => {
    // the server writes the Mac's local time with no offset (link.py), read as local
    stub(devices([phone({ paired_at: "2026-10-03T23:30:00" })]));
    const { unmount } = render(<LinkCard />);
    expect(await within(await card()).findByText("paired 3 Oct 2026")).toBeInTheDocument();
    unmount();
    // a stamp with an offset is moved into this device's time, as every other stamp in the app
    setZone("Asia/Singapore");
    stub(devices([phone({ paired_at: "2026-10-03T22:30:00+00:00" })]));
    render(<LinkCard />);
    expect(await within(await card()).findByText("paired 4 Oct 2026")).toBeInTheDocument();
  });

  it("polls the device list while a code is on screen", async () => {
    // a phone that redeems the code shows up without the owner reloading
    stub(devices([]), {
      "/api/link/pair-token": { payload: "kopifund://pair?url=x&token=kfp_abc", url: "https://mac.tail-abc.ts.net", expires_in: 600 },
    });
    render(<LinkCard />);
    const c = await card();
    await userEvent.click(within(c).getByRole("button", { name: "Pair a phone" }));
    await within(c).findByLabelText("Pairing code");
    const before = gets.filter((u) => u.startsWith("/api/link/devices")).length;
    // the list polls every four seconds until the code expires or is replaced
    await waitFor(
      () => expect(gets.filter((u) => u.startsWith("/api/link/devices")).length).toBeGreaterThan(before),
      { timeout: 6000 },
    );
  }, 8000);

  it("lists the waiting rows and dismisses one", async () => {
    stub(devices([phone()]), {}, [
      pending(), pending({ import_id: "kfp_2", payee: "FairPrice", state: "matched", matched_txn_key: "txn_9", expires_on: null }),
    ]);
    render(<LinkCard />);
    const c = await card();
    const list = await within(c).findByLabelText("Waiting phone rows");
    expect(within(list).getByText("Grab")).toBeInTheDocument();
    expect(within(list).getByText("20 Oct 2026, expires 19 Dec 2026")).toBeInTheDocument();
    // a matched row is history, not something to act on
    expect(within(list).queryByText("FairPrice")).not.toBeInTheDocument();
    expect(within(c).getByText("2 phone rows in all.")).toBeInTheDocument();
    await userEvent.click(within(list).getByRole("button", { name: "Dismiss Grab" }));
    await waitFor(() =>
      expect(posts).toEqual([{ url: "/api/link/pending/dismiss", body: { import_id: "kfp_1" } }]));
    await waitFor(() => expect(within(c).queryByLabelText("Waiting phone rows")).not.toBeInTheDocument());
    expect(within(c).getByText("No phone rows are waiting.")).toBeInTheDocument();
  });

  it("opens the history of every row, newest first, with its state and when it changed", async () => {
    stub(devices([phone()]), {}, [
      pending(),
      pending({ import_id: "kfp_2", payee: "FairPrice", state: "matched", matched_txn_key: "txn_9", expires_on: null,
        state_changed_at: "2026-10-22T02:00:00+00:00" }),
      pending({ import_id: "kfp_3", payee: "Taxi", state: "dismissed", expires_on: null,
        state_changed_at: "2026-10-23T02:00:00+00:00" }),
      pending({ import_id: "kfp_4", payee: "Old shop", date: "2026-07-01", state: "expired", expires_on: null,
        state_changed_at: "2026-08-30T02:00:00+00:00" }),
    ]);
    render(<LinkCard />);
    const c = await card();
    await userEvent.click(await within(c).findByRole("button", { name: "Phone row history" }));
    expect(window.location.search).toBe("?phone_rows=all");
    const sheet = await screen.findByRole("dialog", { name: "Phone row history" });
    const list = within(sheet).getByLabelText("Phone row history");
    const items = within(list).getAllByRole("listitem");
    expect(items.map((li) => within(li).getAllByText(/Grab|FairPrice|Taxi|Old shop/)[0].textContent))
      .toEqual(["Grab", "FairPrice", "Taxi", "Old shop"]);
    expect(within(items[1]).getByText("Matched")).toBeInTheDocument();
    expect(within(items[1]).getByText("22 Oct 2026")).toBeInTheDocument();
    // the matched row opens exactly its record row, a transfer too
    expect(within(items[1]).getByRole("link", { name: "See the record row FairPrice matched" }))
      .toHaveAttribute("href", "/transactions?key=txn_9&hide_transfers=0");
    expect(within(items[2]).getByText("Dismissed, to review")).toBeInTheDocument();
    expect(within(items[3]).getByText("Expired")).toBeInTheDocument();
    expect(within(items[3]).getByText("30 Aug 2026")).toBeInTheDocument();
    // dismissed and expired rows keep Put back, a matched one Not it
    await userEvent.click(within(items[3]).getByRole("button", { name: "Put Old shop back to waiting" }));
    await waitFor(() =>
      expect(posts).toEqual([{ url: "/api/link/pending/restore", body: { import_id: "kfp_4" } }]));
    await userEvent.click(within(items[2]).getByRole("button", { name: "Put Taxi back to waiting" }));
    await userEvent.click(within(items[1]).getByRole("button", { name: /Not it: put FairPrice back/ }));
    await waitFor(() => expect(posts.map((p) => p.body)).toEqual(
      [{ import_id: "kfp_4" }, { import_id: "kfp_3" }, { import_id: "kfp_2" }]));
  });

  it("filters the history by state from the address, and closing it drops the filter", async () => {
    window.history.replaceState(null, "", "/settings?phone_rows=dismissed");
    stub(devices([phone()]), {}, [
      pending(), pending({ import_id: "kfp_3", payee: "Taxi", state: "dismissed", expires_on: null }),
    ]);
    render(<LinkCard />);
    const sheet = await screen.findByRole("dialog", { name: "Phone row history" });
    const list = await within(sheet).findByLabelText("Phone row history");
    expect(within(list).getByText("Taxi")).toBeInTheDocument();
    expect(within(list).queryByText("Grab")).not.toBeInTheDocument();
    await userEvent.click(within(sheet).getByRole("radio", { name: "Waiting" }));
    expect(window.location.search).toBe("?phone_rows=waiting");
    expect(within(list).getByText("Grab")).toBeInTheDocument();
    await userEvent.click(within(sheet).getByRole("radio", { name: "Expired" }));
    expect(within(sheet).getByText("No phone rows in this state.")).toBeInTheDocument();
    await userEvent.click(within(sheet).getByRole("button", { name: "Close" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(window.location.search).toBe("");
  });

  it("says in the history when the rows cannot be read", async () => {
    window.history.replaceState(null, "", "/settings?phone_rows=waiting");
    stub(devices([phone()]));
    const ok = globalThis.fetch;
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) =>
      url.split("?")[0] === "/api/link/pending" ? json({ error: "the record is busy" }, 422) : ok(url, init)));
    render(<LinkCard />);
    const sheet = await screen.findByRole("dialog", { name: "Phone row history" });
    expect(await within(sheet).findByRole("alert")).toHaveTextContent("the record is busy");
  });

  it("shows no phone rows list when the phone has posted none", async () => {
    stub(devices([phone()]));
    render(<LinkCard />);
    const c = await card();
    await within(c).findByLabelText("Paired phones");
    expect(within(c).queryByLabelText("Waiting phone rows")).not.toBeInTheDocument();
    expect(within(c).queryByRole("button", { name: "Phone row history" })).not.toBeInTheDocument();
  });

  it("says so when the phone rows cannot be read", async () => {
    stub(devices([phone()]));
    const ok = globalThis.fetch;
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) =>
      url.split("?")[0] === "/api/link/pending"
        ? json({ error: "the record is busy" }, 422)
        : ok(url, init)));
    render(<LinkCard />);
    const c = await card();
    await within(c).findByLabelText("Paired phones");
    expect(await within(c).findByRole("alert")).toHaveTextContent("the record is busy");
    expect(within(c).queryByLabelText("Waiting phone rows")).not.toBeInTheDocument();
  });

  it("hides an expired code and asks for a new one", async () => {
    stub(devices([]), {
      "/api/link/pair-token": { payload: "kopifund://pair?url=x&token=kfp_abc", url: "https://mac.tail-abc.ts.net", expires_in: 0 },
    });
    render(<LinkCard />);
    const c = await card();
    await userEvent.click(within(c).getByRole("button", { name: "Pair a phone" }));
    expect(await within(c).findByText(/That pairing code expired/)).toBeInTheDocument();
    expect(within(c).queryByLabelText("Pairing code")).not.toBeInTheDocument();
    expect(within(c).getByRole("button", { name: "New pairing code" })).toBeInTheDocument();
  });
});
