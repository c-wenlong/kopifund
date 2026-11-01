import { useCallback, useEffect, useId, useRef, useState } from "react";
import { PanelLeftClose, PanelLeftOpen, type LucideIcon } from "lucide-react";
import { bottomItems, isUnder, pages, PAGE_API } from "../nav";
import { prefetch, type EyeLine } from "../api";
import { onLinkClick } from "../router";
import { formatDate } from "../dates";
import { formatSgd, type Cents } from "../money";
import { useSidebarMode } from "./sidebarMode";
import { useAppIconLogo } from "./appIcon";
import { EyeList, eyeTone, useEyeListRequests } from "./NeedsYourEye";
import { Popover } from "./Popover";

const RAIL = "w-14";

const FULL = "w-60";

export type Holdings = { label: string; value: Cents }[];

export function Sidebar({ path, holdings = [], asOf, eye = [] }: { path: string; holdings?: Holdings; asOf?: string | null; eye?: EyeLine[] }) {
  const [mode, setMode] = useSidebarMode();
  const logo = useAppIconLogo();
  const [hovering, setHovering] = useState(false);
  // G18: the Needs your eye list; a new page closes it, and Home's "All N" opens it
  const [eyeOpen, setEyeOpen] = useState(false);
  const [eyePath, setEyePath] = useState(path);
  if (eyePath !== path) {
    setEyePath(path);
    setEyeOpen(false);
  }
  useEyeListRequests(useCallback(() => setEyeOpen(true), []));
  // the rail stays open while its list is: the list sits beside the item, not beside an icon
  const expanded = mode === "open" || hovering || eyeOpen;

  return (
    // The aside holds the layout width; the inner panel can grow past it and
    // float over the page, so hovering never shifts the content.
    <aside className={`relative shrink-0 transition-[width] duration-200 ${mode === "open" ? FULL : RAIL}`}>
      <div
        data-testid="sidebar-panel"
        data-expanded={expanded}
        onMouseEnter={() => mode === "hover" && setHovering(true)}
        onMouseLeave={() => setHovering(false)}
        onFocus={() => mode === "hover" && setHovering(true)}
        onBlur={(e) => {
          if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setHovering(false);
        }}
        // G18: scrolls on a short screen, so Profile and Settings are never cut off (U11)
        className={`absolute inset-y-0 left-0 z-20 flex flex-col overflow-x-hidden overflow-y-auto border-r bg-page px-2 py-3 transition-[width] duration-200 ${
          expanded ? FULL : RAIL
        } ${
          // floating over the page: a shadow does not show on a dark page, so the edge is drawn
          mode === "hover" && (hovering || eyeOpen) ? "border-edge shadow-xl" : "border-line"
        }`}
      >
        <div className="mb-3 flex h-8 shrink-0 items-center justify-between pl-2">
          {expanded && (
            <span className="flex items-center gap-2 text-[15px] font-semibold tracking-tight">
              <img src={logo} alt="" className="size-6" />
              Kopifund
            </span>
          )}
          <button
            type="button"
            onClick={() => setMode(mode === "open" ? "hover" : "open")}
            aria-pressed={mode === "hover"}
            aria-label={mode === "open" ? "Expand on hover" : "Keep sidebar open"}
            title={mode === "open" ? "Expand on hover" : "Keep sidebar open"}
            className="grid size-8 shrink-0 place-items-center rounded-md text-ink-3 hover:bg-hover hover:text-ink"
          >
            {mode === "open" ? <PanelLeftClose size={16} /> : <PanelLeftOpen size={16} />}
          </button>
        </div>

        <SidebarBody path={path} holdings={holdings} asOf={asOf} expanded={expanded}
          eye={eye} eyeOpen={eyeOpen} onEyeOpen={setEyeOpen} />
      </div>
    </aside>
  );
}

// The pages, the holdings, and Needs your eye, profile and settings at the
// bottom: the same in the desktop sidebar and the phone drawer (MobileNav.tsx).
// `inline` (the drawer) opens the list under its item instead of beside it.
export function SidebarBody({ path, holdings, asOf, expanded, eye = [], eyeOpen = false, onEyeOpen = () => {}, inline = false }: {
  path: string; holdings: Holdings; asOf?: string | null; expanded: boolean;
  eye?: EyeLine[]; eyeOpen?: boolean; onEyeOpen?: (open: boolean) => void; inline?: boolean;
}) {
  return (
    <>
      <nav aria-label="Pages" className="flex flex-col gap-0.5">
        {pages.map((p) => (
          <NavLink key={p.path} path={p.path} label={p.label} icon={<p.icon size={16} />} active={isUnder(path, p.path)} expanded={expanded} />
        ))}
      </nav>

      {expanded && holdings.length > 0 && (
        // G13: S$ on each figure; each row opens Net worth, and the pointer on it names the date the figures are as of
        <dl className="num mt-4 border-t border-line pt-3 text-[13px]">
          {holdings.map((h) => (
            <div key={h.label} title={asOf ? `As of ${formatDate(asOf)}` : undefined}
              className="relative flex justify-between gap-2 rounded-md px-2 py-1 hover:bg-hover">
              <dt className="min-w-0 truncate">
                <a href="/net-worth" onClick={onLinkClick("/net-worth")}
                  className="text-ink-2 no-underline after:absolute after:inset-0 hover:text-ink">{h.label}</a>
              </dt>
              <dd className="m-0 shrink-0">{formatSgd(h.value)}</dd>
            </div>
          ))}
        </dl>
      )}

      <div className="mt-auto flex flex-col gap-0.5 border-t border-line pt-2">
        {/* G19: the bottom group from the route table (nav.ts); G18: Needs your eye is a panel in it, above Your profile */}
        {bottomItems.map((item) => item.kind === "page" ? (
          <NavLink key={item.path} path={item.path} label={item.label} icon={<item.icon size={16} />} active={isUnder(path, item.path)} expanded={expanded} />
        ) : (
          <EyeItem key={item.id} label={item.title} icon={item.icon} lines={eye} expanded={expanded} open={eyeOpen} onOpen={onEyeOpen} inline={inline} />
        ))}
      </div>
    </>
  );
}

const BADGE = { danger: "bg-danger-bg text-danger", warn: "bg-warn-bg text-warn" };
const DOT = { danger: "bg-danger", warn: "bg-warn" };

// G18: what other pages flag, gathered in one place: a count beside the item (a
// dot on the rail), and the list beside it, or under it in the phone menu
function EyeItem({ label, icon: Icon, lines, expanded, open, onOpen, inline }: {
  label: string; icon: LucideIcon; lines: EyeLine[]; expanded: boolean; open: boolean; onOpen: (open: boolean) => void; inline: boolean;
}) {
  const button = useRef<HTMLButtonElement>(null);
  const below = useRef<HTMLDivElement>(null);
  const listId = useId();
  // in the phone menu the list opens at its foot; bring it into view (Home's "All N" opens the menu on it)
  useEffect(() => {
    if (open && inline) below.current?.scrollIntoView?.({ block: "nearest" });
  }, [open, inline]);
  const n = lines.length;
  const tone = eyeTone(lines);
  const name = n ? `${label}, ${n}` : label;
  const close = useCallback((refocus: boolean) => {
    onOpen(false);
    if (refocus) button.current?.focus();
  }, [onOpen]);
  return (
    <>
      <button
        ref={button}
        type="button"
        onClick={() => onOpen(!open)}
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        aria-label={name}
        title={expanded ? undefined : name}
        className={`flex h-8 shrink-0 items-center gap-2.5 rounded-md px-2.5 text-[14px] whitespace-nowrap ${
          open ? "bg-tile text-ink" : "text-ink-2 hover:bg-hover hover:text-ink"
        }`}
      >
        <span className="relative shrink-0">
          <Icon size={16} aria-hidden />
          {!expanded && n > 0 && (
            <span data-testid="eye-dot" aria-hidden className={`absolute -top-0.5 -right-0.5 size-2 rounded-full ring-2 ring-page ${DOT[tone]}`} />
          )}
        </span>
        {expanded && <span className="min-w-0 flex-1 truncate text-left">{label}</span>}
        {expanded && n > 0 && (
          <span data-testid="eye-count" aria-hidden className={`num shrink-0 rounded-full px-1.5 text-[12px] leading-[18px] font-semibold ${BADGE[tone]}`}>
            {n}
          </span>
        )}
      </button>
      {open && (inline ? (
        <div ref={below} id={listId} className="mb-1 ml-2 border-l border-line pl-1">
          <EyeList lines={lines} label={label} onFollow={() => onOpen(false)} />
        </div>
      ) : (
        <Popover anchor={button} id={listId} label={label} side="right" links onClose={close}>
          <div className="w-80 max-w-full">
            <p className="m-0 px-2 pt-0.5 pb-1.5 text-[13px] font-semibold">{label}</p>
            <EyeList lines={lines} onFollow={() => onOpen(false)} />
          </div>
        </Popover>
      ))}
    </>
  );
}

function NavLink(props: { path: string; label: string; icon: React.ReactNode; active: boolean; expanded: boolean }) {
  return (
    <a
      href={props.path}
      onClick={onLinkClick(props.path)}
      // G13: the page's figures are on their way before the click lands
      onMouseEnter={() => prefetch(PAGE_API[props.path] ?? [])}
      onFocus={() => prefetch(PAGE_API[props.path] ?? [])}
      aria-current={props.active ? "page" : undefined}
      aria-label={props.expanded ? undefined : props.label}
      title={props.expanded ? undefined : props.label}
      className={`flex h-8 shrink-0 items-center gap-2.5 rounded-md px-2.5 text-[14px] whitespace-nowrap no-underline ${
        props.active ? "bg-tile text-ink" : "text-ink-2 hover:bg-hover hover:text-ink"
      }`}
    >
      <span className="shrink-0">{props.icon}</span>
      {props.expanded && <span>{props.label}</span>}
    </a>
  );
}
