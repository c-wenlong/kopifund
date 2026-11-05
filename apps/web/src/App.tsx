import { Sidebar } from "./components/Sidebar";
import { routeFor, titleFor } from "./routes";
import { usePath, useLeaveAsked } from "./router";
import { useApi, type Overview } from "./api";
import { ServerBanner } from "./components/ServerBanner";
import { Toaster } from "./components/Toast";
import { NotFound } from "./pages/NotFound";
import type { LucideIcon } from "lucide-react";
import { onLinkClick } from "./router";
import { Setup } from "./pages/Setup";
import { useCallback, useEffect, useRef, useState } from "react";
import { Drawer, TopBar } from "./components/MobileNav";
import { useEyeListRequests } from "./components/NeedsYourEye";
import { useWide } from "./components/useWide";
import { syncAppIcon } from "./components/appIcon";
import { LeaveBar } from "./components/LeaveBar";
import { buttonClass } from "./components/Button";
import type { SetupState } from "./api";
import { useCopilot } from "./copilot/useCopilot";
import { AskButton, CopilotSheet, CopilotSide, readPanelOpen, savePanelOpen } from "./copilot/CopilotPanel";
import { PageBoundary } from "./components/ErrorBoundary";

function HeaderLink({ to, icon: Icon, label }: { to: string; icon: LucideIcon; label: string }) {
  return (
    <a href={to} onClick={onLinkClick(to)}
      className={buttonClass("secondary")}>
      <Icon size={15} aria-hidden /> {label}
    </a>
  );
}

export function App() {
  const [version, setVersion] = useState(0);
  const setup = useApi<SetupState>(`/api/setup?v=${version}`, { quiet: true });
  useEffect(() => { void syncAppIcon(); }, []);
  if (setup.data?.needed) return <><Setup state={setup.data} onDone={() => setVersion((v) => v + 1)} /><Toaster /></>;
  return <><Shell key={version} /><Toaster /></>;
}

function Shell() {
  const path = usePath();
  const overview = useApi<Overview>("/api/overview", { quiet: path !== "/" });
  // G19: which page an address opens, what it is called and what its header links to are all read off routes.tsx
  const route = routeFor(path);
  const title = titleFor(path);
  // G13: under 768px a top bar and a drawer stand in for the sidebar
  const wide = useWide();
  const [menuOpen, setMenuOpen] = useState(false);
  // G18: Home's "All N" on a phone opens the menu on its Needs your eye list (the sidebar listens on a desktop)
  const [menuEye, setMenuEye] = useState(false);
  const [menuPath, setMenuPath] = useState(path);
  // a new page, or a window grown past the breakpoint, closes the drawer. So does
  // "Leave without saving?" as it comes up (G18): a held Back leaves the page as it
  // is, and the question shows in the page, which the open drawer keeps out of reach
  const leaveAsked = useLeaveAsked();
  const [askSeen, setAskSeen] = useState(leaveAsked);
  const askCame = leaveAsked && !askSeen;
  if (askSeen !== leaveAsked) setAskSeen(leaveAsked);
  if (menuPath !== path || ((wide || askCame) && menuOpen)) {
    setMenuPath(path);
    setMenuOpen(false);
    setMenuEye(false);
  }
  useEyeListRequests(useCallback(() => {
    if (wide) return;
    setMenuEye(true);
    setMenuOpen(true);
  }, [wide]));
  const closeMenu = useCallback(() => {
    setMenuOpen(false);
    setMenuEye(false);
  }, []);
  const menuButton = useRef<HTMLButtonElement>(null);
  // G22: Ask Kopifund. From 768px a panel beside the page that pushes it narrower, left open or
  // closed as the last visit left it; under 768px a bottom sheet from the top bar. One
  // conversation for both, so a window crossing the breakpoint keeps it.
  const copilot = useCopilot();
  const [panelOpen, setPanelOpen] = useState(readPanelOpen);
  // opened by a press in this visit, so its box takes the focus; a panel left open last visit leaves the focus be
  const [panelPressed, setPanelPressed] = useState(false);
  const [sheetOpen, setSheetOpen] = useState(false);
  if (wide && sheetOpen) setSheetOpen(false);
  const askButton = useRef<HTMLButtonElement>(null);
  const togglePanel = () => {
    if (!wide) {
      setSheetOpen(true);
      return;
    }
    savePanelOpen(!panelOpen);
    setPanelOpen(!panelOpen);
    setPanelPressed(!panelOpen);
  };
  const closePanel = () => {
    savePanelOpen(false);
    setPanelOpen(false);
    askButton.current?.focus();
  };
  const ask = <AskButton open={panelOpen} onClick={togglePanel} buttonRef={askButton} />;
  const holdings = overview.data?.holdings ?? [];
  // G18: an answer from a server older than the lines (or a test's stand-in) has none
  const eye = overview.data?.needs_your_eye?.lines ?? [];
  // G13: each page names the tab, so several open tabs tell apart
  useEffect(() => {
    document.title = `${title}, Kopifund`;
  }, [title]);

  return (
    <div className="safe-area flex h-dvh flex-col md:flex-row" data-copilot={wide && panelOpen ? "open" : undefined}>
      {/* Audit: the first stop for the keyboard, past the sidebar's links; shown only while it has the focus */}
      <a href="#main" onClick={(e) => {
        e.preventDefault();
        document.getElementById("main")?.focus();
      }}
        className="sr-only z-[70] rounded-md bg-panel px-3 py-2 text-[14px] text-ink shadow-lg focus:not-sr-only focus:fixed focus:top-3 focus:left-3">
        Skip to content
      </a>
      {wide ? <Sidebar path={path} holdings={holdings} asOf={overview.data?.as_of} eye={eye} /> : (
        <TopBar title={title} open={menuOpen} onOpen={() => setMenuOpen(true)} buttonRef={menuButton} eye={eye}
          ask={<AskButton compact open={sheetOpen} onClick={togglePanel} />} />
      )}
      {menuOpen && <Drawer path={path} holdings={holdings} asOf={overview.data?.as_of} onClose={closeMenu} returnFocus={menuButton}
        eye={eye} eyeFirst={menuEye} />}
      <main id="main" tabIndex={-1} inert={menuOpen} className="min-w-0 flex-1 overflow-y-auto px-4 py-4 outline-none md:px-6 md:py-5">
        <div className="mx-auto max-w-5xl">
          {/* G18: "Leave without saving?" when a move is held by unsaved edits; in view however far the page is scrolled */}
          <div className="sticky -top-4 z-20 -mt-4 mb-3 bg-page pt-4 empty:hidden md:-top-5 md:-mt-5 md:pt-5"><LeaveBar /></div>
          <ServerBanner />
          {path === "/" ? <><h1 className="sr-only">{title}</h1>{wide && <div className="mb-3 flex justify-end">{ask}</div>}</> : (
            // under 768px the top bar already names the page: the h1 stays for screen readers only
            <div className={`flex items-center justify-end gap-3 md:mb-4 md:justify-between ${route?.link ? "mb-3" : ""}`}>
              <h1 className="m-0 text-[20px] font-semibold tracking-tight max-md:sr-only">{title}</h1>
              {route?.link && <HeaderLink {...route.link} />}
              {/* G13: a page puts its own buttons and status chips here (PageActions) */}
              <div id="page-actions" className="ml-auto flex items-center gap-2 empty:hidden" />
              {wide && ask}
            </div>
          )}
          {/* Audit: a page that throws as it draws shows a problem in its place; the shell stays, and another page starts clean */}
          <PageBoundary path={path}>{route ? <route.component overview={overview} /> : <NotFound />}</PageBoundary>
        </div>
      </main>
      {wide && panelOpen && <CopilotSide copilot={copilot} path={path} title={title} onClose={closePanel} focus={panelPressed} />}
      {!wide && <CopilotSheet copilot={copilot} path={path} title={title} open={sheetOpen} onClose={() => setSheetOpen(false)} />}
    </div>
  );
}
