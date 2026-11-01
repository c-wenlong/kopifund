import type { LucideIcon } from "lucide-react";
import { NAV_PANELS, ROUTES, routeFor, type NavPanel } from "./routes";

// G19: the sidebar and the drawer, and what the sidebar asks for under the
// pointer, are read off the one route table (routes.tsx). Nothing here is
// written twice.
export type Page = { path: string; label: string; icon: LucideIcon };

function inGroup(group: "main" | "bottom"): Page[] {
  return ROUTES.flatMap((r) => (r.nav?.group === group ? [{ path: r.path, label: r.title, icon: r.nav.icon }] : []));
}

/** The page list, in the table's order. */
export const pages: Page[] = inGroup("main");

/** The group under the holdings: profile and settings. */
export const bottomPages: Page[] = inGroup("bottom");

/** G18: one item of a nav group as the sidebar draws it: a page's link, or a panel's button. */
export type NavItem = ({ kind: "page" } & Page) | ({ kind: "panel" } & NavPanel);

/** The bottom group in order: its pages, each panel just above the page it names (Needs your eye above Your profile). */
export const bottomItems: NavItem[] = bottomPages.flatMap((p): NavItem[] => [
  ...NAV_PANELS.filter((n) => n.group === "bottom" && n.before === p.path).map((n): NavItem => ({ kind: "panel", ...n })),
  { kind: "page", ...p },
]);

// G13: what each page asks for as it opens, which the sidebar asks for as
// soon as the pointer is on its link (api.ts, prefetch).
export const PAGE_API: Record<string, string[]> = Object.fromEntries(
  ROUTES.flatMap((r) => (r.api ? [[r.path, [...r.api]]] : [])),
);

/** The sidebar link a page lights up: its own, or the one it sits under (Past uploads under Upload; an old address as the page it became). */
export function isUnder(path: string, page: string): boolean {
  const here = routeFor(path)?.path ?? path;
  return here === page || (page !== "/" && here.startsWith(`${page}/`));
}
