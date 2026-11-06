import "@testing-library/jest-dom/vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AppIconCard } from "./AppIconCard";
import { syncAppIcon, useAppIconLogo } from "../components/appIcon";
import { json } from "../test-helpers";

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
    expect(screen.getByRole("img", { name: "logo" })).toHaveAttribute("src", "/logo.svg");
    fireEvent.click(screen.getByRole("radio", { name: /Iso/ }));
    await waitFor(() => expect(screen.getByRole("radio", { name: /Iso/ })).toBeChecked());
    expect(posts).toEqual([JSON.stringify({ name: "iso" })]);
    expect(href('link[rel="icon"][type="image/svg+xml"]')).toBe("/app-icons/iso/favicon.svg");
    expect(href('link[rel="icon"]:not([type])')).toBe("/app-icons/iso/favicon.ico");
    expect(href('link[rel="apple-touch-icon"]')).toBe("/app-icons/iso/apple-touch-icon.png");
    expect(screen.getByRole("img", { name: "logo" })).toHaveAttribute("src", "/app-icons/iso/logo.svg");
    expect(window.localStorage.getItem("kopifund.appIcon")).toBe("iso");
  });

  it("puts the pick back and says why when the server refuses it", async () => {
    stubFetch({ refuse: true });
    render(<AppIconCard />);
    fireEvent.click(await screen.findByRole("radio", { name: /Riso/ }));
    expect(await screen.findByText("Not an app icon")).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /Sticker/ })).toBeChecked();
    expect(href('link[rel="icon"][type="image/svg+xml"]')).toBe("/favicon.svg");
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

  it("keeps the default when the server cannot be reached", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new Error("down"))));
    await syncAppIcon();
    expect(href('link[rel="icon"][type="image/svg+xml"]')).toBe("/favicon.svg");
    expect(window.localStorage.getItem("kopifund.appIcon")).toBeNull();
  });
});
