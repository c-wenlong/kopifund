import { useEffect, useState } from "react";
import { getJSON, postJSON } from "../api";
import type { AppIconPage } from "../types.gen";

// The app icon set picked in Settings (SPEC.md, "App icon"). The record keeps
// the pick; this tab holds the pick it last heard of in memory, which the
// tab's icon, the logo and the Settings card all read, and the browser's
// storage keeps a copy so a reload shows it before the server answers and
// other tabs hear of it. index.html and the root of public/ hold the default
// set, so a page that has heard of no pick shows the default.
const KEY = "kopifund.appIcon";
const EVENT = "kopifund:app-icon";

// undefined: storage not read yet. Storage is only the copy: when it is
// blocked the pick still lives here, so the logo moves with the tab's icon.
let current: string | null | undefined;

function read(): string | null {
  try {
    return window.localStorage.getItem(KEY);
  } catch {
    return null;
  }
}

function known(): string | null {
  if (current === undefined) current = read();
  return current;
}

/** Forget the pick held in memory, as a fresh page would (tests). */
export function resetAppIcon() {
  current = undefined;
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

function heard(name: string) {
  current = name;
  showIcon(name);
  window.dispatchEvent(new Event(EVENT));
}

function keep(name: string) {
  try {
    window.localStorage.setItem(KEY, name);
  } catch {
    // Storage can be blocked; the pick still shows for this visit.
  }
  heard(name);
}

/** Show the pick this browser last heard of, then ask the server for the current one. */
export async function syncAppIcon(): Promise<void> {
  const last = known();
  if (last) showIcon(last);
  try {
    const page = await getJSON<AppIconPage>("/api/app-icon");
    if (page.chosen !== known()) keep(page.chosen);
  } catch {
    // the server is down or older than the picker: the last pick, or the default, stays
  }
}

export async function pickAppIcon(name: string): Promise<AppIconPage> {
  const page = await postJSON<AppIconPage>("/api/app-icon", JSON.stringify({ name }));
  keep(page.chosen);
  return page;
}

/**
 * Follow a pick made in another tab of this app: the browser tells every other
 * tab of the origin when one writes the stored copy. Returns the unsubscribe.
 */
export function followAppIcon(): () => void {
  const changed = (e: StorageEvent) => {
    if (e.key === KEY && e.newValue && e.newValue !== current) heard(e.newValue);
  };
  window.addEventListener("storage", changed);
  return () => window.removeEventListener("storage", changed);
}

/** The pick this tab last heard of (its own, the server's, another tab's), or null before any. */
export function useAppIconChoice(): string | null {
  const [name, setName] = useState(known);
  useEffect(() => {
    const sync = () => setName(known());
    sync();
    window.addEventListener(EVENT, sync);
    return () => window.removeEventListener(EVENT, sync);
  }, []);
  return name;
}

/** The logo beside the app's name: the pick's mark, or the default's before any pick is known. */
export function useAppIconLogo(): string {
  const name = useAppIconChoice();
  return name ? iconFile(name, "logo.svg") : "/logo.svg";
}
