import "@testing-library/jest-dom/vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AppIconCard } from "./AppIconCard";
import { followAppIcon, syncAppIcon, useAppIconLogo } from "../components/appIcon";
import { json, settle } from "../test-helpers";

const SETS = [
  { name: "sticker", label: "Sticker", about: "Kopi in a tied bag with a coin, on yellow" },
  { name: "iso", label: "Iso", about: "A cup on a stack of coins, on green" },
  { name: "riso", label: "Riso", about: "A printed cup before a pink sun" },
];
const page = (chosen: string) => ({ sets: SETS, default: "sticker", chosen });
const posts: string[] = [];

function stubFetch({ chosen = "sticker", refuse = false } = {}) {
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    if (url === "/api/app-icon" && init?.method === "POST") {
      posts.push(init.body as string);
      if (refuse) return json({ error: "Not an app icon" }, 422);
      return json(page(JSON.parse(init.body as string).name));
    }
    if (url === "/api/app-icon") return json(page(chosen));
    return new Response("{}", { status: 404 });
  }));
}

function Logo() {
  return <img alt="logo" src={useAppIconLogo()} />;
}

const href = (selector: string) => document.querySelector(selector)?.getAttribute("href");

beforeEach(() => {
  posts.length = 0;
  document.head.innerHTML = '<link rel="icon" href="/favicon.ico" sizes="16x16 32x32 48x48" />'
    + '<link rel="icon" href="/favicon.svg" type="image/svg+xml" /><link rel="apple-touch-icon" href="/apple-touch-icon.png" />';
});

describe("App icon card", () => {
  it("lists the sets, marks the default and the pick, and saves a new pick", async () => {
    stubFetch();
    render(<><AppIconCard /><Logo /></>);
    const sticker = await screen.findByRole("radio", { name: /Sticker \(default\)/ });
    expect(sticker).toBeChecked();
    expect(screen.getByText("A cup on a stack of coins, on green")).toBeInTheDocument();
    // opening the card asks the server, which names sticker: the logo is sticker's own copy
    await settle();
    expect(screen.getByRole("img", { name: "logo" })).toHaveAttribute("src", "/app-icons/sticker/logo.svg");
    fireEvent.click(screen.getByRole("radio", { name: /Iso/ }));
    // the radio is ticked at once, before the server has answered: the pick is kept only when it has
    await waitFor(() => expect(window.localStorage.getItem("kopifund.appIcon")).toBe("iso"));
    await settle();
    expect(screen.getByRole("radio", { name: /Iso/ })).toBeChecked();
    expect(posts).toEqual([JSON.stringify({ name: "iso" })]);
    expect(href('link[rel="icon"][type="image/svg+xml"]')).toBe("/app-icons/iso/favicon.svg");
    expect(href('link[rel="icon"]:not([type])')).toBe("/app-icons/iso/favicon.ico");
    expect(href('link[rel="apple-touch-icon"]')).toBe("/app-icons/iso/apple-touch-icon.png");
    expect(screen.getByRole("img", { name: "logo" })).toHaveAttribute("src", "/app-icons/iso/logo.svg");
  });

  it("puts the pick back and says why when the server refuses it", async () => {
    stubFetch({ refuse: true });
    render(<AppIconCard />);
    fireEvent.click(await screen.findByRole("radio", { name: /Riso/ }));
    expect(await screen.findByText("Not an app icon")).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /Sticker/ })).toBeChecked();
    expect(href('link[rel="icon"][type="image/svg+xml"]')).toBe("/app-icons/sticker/favicon.svg");
  });
});

describe("App icon card, saves that race and picks made elsewhere", () => {
  it("takes no second pick while one is saving, so a failed save puts back only a saved pick", async () => {
    const answers: ((r: Response) => void)[] = [];
    vi.stubGlobal("fetch", vi.fn((_url: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        posts.push(init.body as string);
        return new Promise<Response>((resolve) => { answers.push(resolve); });
      }
      return Promise.resolve(json(page("sticker")));
    }));
    render(<AppIconCard />);
    fireEvent.click(await screen.findByRole("radio", { name: /Iso/ }));
    expect(screen.getByRole("radio", { name: /Riso/ })).toBeDisabled();
    fireEvent.click(screen.getByRole("radio", { name: /Riso/ }));
    expect(posts).toEqual([JSON.stringify({ name: "iso" })]);
    await act(async () => {
      for (const answer of answers) answer(json({ error: "Not an app icon" }, 422));
    });
    expect(await screen.findByText("Not an app icon")).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /Sticker/ })).toBeChecked();
    expect(screen.getByRole("radio", { name: /Riso/ })).toBeEnabled();
  });

  it("follows a pick another tab makes after this one picked", async () => {
    stubFetch();
    const stop = followAppIcon();
    render(<><AppIconCard /><Logo /></>);
    fireEvent.click(await screen.findByRole("radio", { name: /Iso/ }));
    await settle();
    expect(screen.getByRole("radio", { name: /Iso/ })).toBeChecked();
    // another tab saves riso: the browser tells this one through a storage event
    act(() => {
      window.localStorage.setItem("kopifund.appIcon", "riso");
      window.dispatchEvent(new StorageEvent("storage", { key: "kopifund.appIcon", newValue: "riso" }));
    });
    expect(screen.getByRole("radio", { name: /Riso/ })).toBeChecked();
    expect(screen.getByRole("img", { name: "logo" })).toHaveAttribute("src", "/app-icons/riso/logo.svg");
    expect(href('link[rel="icon"][type="image/svg+xml"]')).toBe("/app-icons/riso/favicon.svg");
    stop();
  });

  it("shows the server's pick, not an older one this tab heard of, and a refused pick goes back to it", async () => {
    window.localStorage.setItem("kopifund.appIcon", "riso");
    stubFetch({ chosen: "iso", refuse: true });
    render(<AppIconCard />);
    await settle();
    expect(screen.getByRole("radio", { name: /Iso/ })).toBeChecked();
    fireEvent.click(screen.getByRole("radio", { name: /Sticker/ }));
    expect(await screen.findByText("Not an app icon")).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /Iso/ })).toBeChecked();
  });

  it("moves the logo with the tab's icon when the browser blocks storage", async () => {
    stubFetch();
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("blocked"); });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("blocked"); });
    render(<><AppIconCard /><Logo /></>);
    fireEvent.click(await screen.findByRole("radio", { name: /Iso/ }));
    await settle();
    expect(href('link[rel="icon"][type="image/svg+xml"]')).toBe("/app-icons/iso/favicon.svg");
    expect(screen.getByRole("img", { name: "logo" })).toHaveAttribute("src", "/app-icons/iso/logo.svg");
    expect(screen.getByRole("radio", { name: /Iso/ })).toBeChecked();
  });
});

describe("syncAppIcon", () => {
  it("shows the last pick at once, then the server's", async () => {
    window.localStorage.setItem("kopifund.appIcon", "riso");
    let answer: (r: Response) => void = () => {};
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>((resolve) => { answer = resolve; })));
    const done = syncAppIcon();
    expect(href('link[rel="icon"][type="image/svg+xml"]')).toBe("/app-icons/riso/favicon.svg");
    await act(async () => {
      answer(json(page("iso")));
      await done;
    });
    expect(href('link[rel="icon"][type="image/svg+xml"]')).toBe("/app-icons/iso/favicon.svg");
    expect(window.localStorage.getItem("kopifund.appIcon")).toBe("iso");
  });

  it("drops a late answer when another tab's pick arrived while it was on its way", async () => {
    let answer: (r: Response) => void = () => {};
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>((resolve) => { answer = resolve; })));
    const stop = followAppIcon();
    const done = syncAppIcon();
    window.localStorage.setItem("kopifund.appIcon", "iso");
    window.dispatchEvent(new StorageEvent("storage", { key: "kopifund.appIcon", newValue: "iso" }));
    await act(async () => {
      answer(json(page("sticker")));
      await done;
    });
    expect(href('link[rel="icon"][type="image/svg+xml"]')).toBe("/app-icons/iso/favicon.svg");
    expect(window.localStorage.getItem("kopifund.appIcon")).toBe("iso");
    stop();
  });

  it("keeps the default when the server cannot be reached", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new Error("down"))));
    await syncAppIcon();
    expect(href('link[rel="icon"][type="image/svg+xml"]')).toBe("/favicon.svg");
    expect(window.localStorage.getItem("kopifund.appIcon")).toBeNull();
  });
});
