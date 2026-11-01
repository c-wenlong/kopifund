import { useEffect, useState } from "react";
import { getJSON, postJSON } from "../api";
import type { AppIconPage } from "../types.gen";

// The app icon set picked in Settings (SPEC.md, "App icon"). The record keeps
// the pick; this copy lets a reload show it before the server answers.
// index.html and the root of public/ hold the default set, so a page that
// has heard of no pick shows the default.
const KEY = "kopifund.appIcon";
const EVENT = "kopifund:app-icon";

function read(): string | null {
  try {
    return window.localStorage.getItem(KEY);
  } catch {
    return null;
  }
}

export function iconFile(name: string, file: string): string {
  return `/app-icons/${encodeURIComponent(name)}/${file}`;
}

// the tab's icon and the one Add to Home Screen takes. The manifest's icons
// stay the default: an installed app keeps the icon it was installed with.
function showIcon(name: string) {
  const links: [string, string][] = [
    ['link[rel="icon"][type="image/svg+xml"]', "favicon.svg"],
    ['link[rel="icon"]:not([type])', "favicon.ico"],
    ['link[rel="apple-touch-icon"]', "apple-touch-icon.png"],
  ];
  for (const [selector, file] of links) document.querySelector(selector)?.setAttribute("href", iconFile(name, file));
}

function keep(name: string) {
  try {
    window.localStorage.setItem(KEY, name);
  } catch {
    // Storage can be blocked; the pick still shows for this visit.
  }
  showIcon(name);
  window.dispatchEvent(new Event(EVENT));
}

/** Show the pick this browser last heard of, then ask the server for the current one. */
export async function syncAppIcon(): Promise<void> {
  const known = read();
  if (known) showIcon(known);
  try {
    const page = await getJSON<AppIconPage>("/api/app-icon");
    if (page.chosen !== known) keep(page.chosen);
  } catch {
    // the server is down or older than the picker: the last pick, or the default, stays
  }
}

export async function pickAppIcon(name: string): Promise<AppIconPage> {
  const page = await postJSON<AppIconPage>("/api/app-icon", JSON.stringify({ name }));
  keep(page.chosen);
  return page;
}

/** The logo beside the app's name: the pick's mark, or the default's before any pick is known. */
export function useAppIconLogo(): string {
  const [name, setName] = useState(read);
  useEffect(() => {
    const sync = () => setName(read());
    window.addEventListener(EVENT, sync);
    return () => window.removeEventListener(EVENT, sync);
  }, []);
  return name ? iconFile(name, "logo.svg") : "/logo.svg";
}
