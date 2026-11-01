// G19: the one table of the app's pages. The sidebar and the phone drawer (nav.ts),
// the tab and top-bar title, the header link, which page App draws for an
// address, and what the sidebar asks for as the pointer lands on a link (PAGE_API
// in nav.ts) all read this list, so a page is added or renamed in one place.
import type { ComponentType } from "react";
import {
  ArrowLeft,
  ArrowLeftRight,
  ChartLine,
  ChartPie,
  Eye,
  History,
  House,
  List,
  Plane,
  SlidersHorizontal,
  Settings as SettingsIcon,
  Tags,
  Target,
  Upload,
  UserRound,
  type LucideIcon,
} from "lucide-react";
import type { Loaded, Overview } from "./api";
import { Problem, TilesSkeleton } from "./components/States";
import { Categories } from "./pages/Categories";
import { Home } from "./pages/Home";
import { NetWorth } from "./pages/NetWorth";
import { PastUploads } from "./pages/PastUploads";
import { Plan } from "./pages/Plan";
import { Profile } from "./pages/Profile";
import { Recurring } from "./pages/Recurring";
import { Rules } from "./pages/Rules";
import { Settings } from "./pages/Settings";
import { SpendingExplorer } from "./pages/SpendingExplorer";
import { StatsPage } from "./pages/StatsPage";
import { Transactions } from "./pages/Transactions";
import { Trips } from "./pages/Trips";
import { UploadPage } from "./pages/Upload";

/** What App hands the page it draws: only Home and Plan read it. */
export type RouteProps = { overview: Loaded<Overview> };

export type Route = {
  /** The address the sidebar links to and the page is known by. */
  path: string;
  /** Other addresses that open the same page (a saved link to an old address still lands here). */
  aliases?: readonly string[];
  /** The page's name: the tab, the top bar, the heading and the sidebar link. */
  title: string;
  /** In the sidebar and the drawer: `main` is the page list, `bottom` the group under the holdings. A page without it is reached by a link on another page. */
  nav?: { icon: LucideIcon; group: "main" | "bottom" };
  /** What the page asks for as it opens, bare URLs only; the sidebar asks for these as the pointer lands on its link (api.ts, prefetch). A page left out still paints at once from the cache the second time it opens. */
  api?: readonly string[];
  /** A link in the page header, to the page next to this one. */
  link?: { to: string; label: string; icon: LucideIcon };
  component: ComponentType<RouteProps>;
};

// G5: which stat cards Spending shows, in order. The explorer above them
// covers spend by category, top payees and the big one-offs, so
// category-breakdown, merchant-concentration and one-offs are not cards here.
const SPENDING_STATS = ["monthly-cashflow", "needs-wants-savings", "eating-out", "income-stability"];

function HomePage({ overview }: RouteProps) {
  return overview.data ? <Home overview={overview.data} />
    : overview.error ? <Problem message={overview.error} retry />
    : <TilesSkeleton what="Loading your figures" />;
}

function PlanPage({ overview }: RouteProps) {
  return <Plan asOf={overview.data ? overview.data.as_of : overview.error ? null : undefined} />;
}

function SpendingPage() {
  return <StatsPage ids={SPENDING_STATS} intro={<SpendingExplorer />} />;
}

export const ROUTES: readonly Route[] = [
  { path: "/", title: "Home", nav: { icon: House, group: "main" }, api: ["/api/overview", "/api/net-worth"], component: HomePage },
  { path: "/transactions", title: "Transactions", nav: { icon: List, group: "main" }, component: Transactions },
  {
    path: "/spending", title: "Spending", nav: { icon: ChartPie, group: "main" },
    api: ["/api/spending", ...SPENDING_STATS.map((id) => `/api/results/${id}`)],
    component: SpendingPage,
  },
  { path: "/recurring", title: "Recurring", nav: { icon: ArrowLeftRight, group: "main" }, api: ["/api/recurring"], component: Recurring },
  { path: "/trips", title: "Trips", nav: { icon: Plane, group: "main" }, api: ["/api/trips"], component: Trips },
  {
    path: "/net-worth", title: "Net worth", nav: { icon: ChartLine, group: "main" },
    api: ["/api/net-worth", "/api/growth", "/api/suggestions", "/api/results/savings-runway", "/api/results/benchmarks-sg"],
    component: NetWorth,
  },
  {
    path: "/plan", title: "Plan", nav: { icon: Target, group: "main" },
    api: ["/api/plan", "/api/suggestions", "/api/results/forward-view"],
    component: PlanPage,
  },
  { path: "/categories", title: "Categories", nav: { icon: Tags, group: "main" }, api: ["/api/categories"], component: Categories },
  {
    path: "/upload", aliases: ["/monthly-run"], title: "Upload", nav: { icon: Upload, group: "main" },
    link: { to: "/upload/history", label: "Past uploads", icon: History },
    component: UploadPage,
  },
  { path: "/rules", title: "Rules", nav: { icon: SlidersHorizontal, group: "main" }, api: ["/api/rules"], component: Rules },
  { path: "/profile", title: "Your profile", nav: { icon: UserRound, group: "bottom" }, api: ["/api/profile"], component: Profile },
  { path: "/settings", title: "Settings", nav: { icon: SettingsIcon, group: "bottom" }, api: ["/api/scam-checks"], component: Settings },
  {
    path: "/upload/history", title: "Past uploads",
    link: { to: "/upload", label: "Back to upload", icon: ArrowLeft },
    component: PastUploads,
  },
];

/**
 * G18: a sidebar item that opens a panel rather than a page: it has no address,
 * so it is not a Route, but it sits in a nav group like one, just above the
 * page `before` names (nav.ts, bottomItems). The sidebar draws the panel for
 * its `id`.
 */
export type NavPanel = { id: "needs-your-eye"; title: string; icon: LucideIcon; group: "main" | "bottom"; before: string };

export const NAV_PANELS: readonly NavPanel[] = [
  // what other pages flag, gathered above Your profile, with its count (components/Sidebar.tsx, EyeItem)
  { id: "needs-your-eye", title: "Needs your eye", icon: Eye, group: "bottom", before: "/profile" },
];

/** The route an address belongs to, its aliases included; undefined for an address no page answers. */
export function routeFor(path: string): Route | undefined {
  return ROUTES.find((r) => r.path === path || r.aliases?.includes(path));
}

/** The name of the page at an address, for the tab and the top bar. */
export function titleFor(path: string): string {
  return routeFor(path)?.title ?? "Page not found";
}
