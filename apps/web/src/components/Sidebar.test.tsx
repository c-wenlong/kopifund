import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { EyeLine } from "../api";
import { Drawer, TopBar } from "./MobileNav";
import { openEyeList } from "./NeedsYourEye";
import { Sidebar } from "./Sidebar";

beforeEach(() => window.localStorage.clear());

const panel = () => screen.getByTestId("sidebar-panel");

describe("Sidebar", () => {
  it("asks for a page's figures as soon as the pointer is on its link (G13)", () => {
    const fetchSpy = vi.fn(async (_url: string) => new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetchSpy);
    render(<Sidebar path="/" />);
    fireEvent.mouseEnter(screen.getByRole("link", { name: "Net worth" }));
    expect(fetchSpy.mock.calls.map(([u]) => u)).toEqual([
      "/api/net-worth", "/api/growth", "/api/suggestions", "/api/results/savings-runway", "/api/results/benchmarks-sg",
    ]);
    vi.unstubAllGlobals();
  });

  it("starts open with labels, profile and settings at the bottom", () => {
    render(<Sidebar path="/" />);
    expect(panel()).toHaveAttribute("data-expanded", "true");
    expect(screen.getByText("Transactions")).toBeInTheDocument();
    const links = screen.getAllByRole("link");
    expect(links.slice(-2).map((l) => l.getAttribute("href"))).toEqual(["/profile", "/settings"]);
  });

  it("marks the current page", () => {
    render(<Sidebar path="/rules" />);
    expect(screen.getByRole("link", { current: "page" })).toHaveAttribute("href", "/rules");
  });

  it("in hover mode rests as icons and expands on hover", async () => {
    render(<Sidebar path="/" />);
    await userEvent.click(screen.getByRole("button", { name: "Expand on hover" }));
    expect(panel()).toHaveAttribute("data-expanded", "false");
    expect(screen.queryByText("Transactions")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Transactions" })).toBeInTheDocument();

    fireEvent.mouseEnter(panel());
    expect(panel()).toHaveAttribute("data-expanded", "true");
    fireEvent.mouseLeave(panel());
    expect(panel()).toHaveAttribute("data-expanded", "false");
  });

  it("remembers the choice across a reload", async () => {
    const { unmount } = render(<Sidebar path="/" />);
    await userEvent.click(screen.getByRole("button", { name: "Expand on hover" }));
    unmount();
    render(<Sidebar path="/" />);
    expect(panel()).toHaveAttribute("data-expanded", "false");
    expect(screen.getByRole("button", { name: "Keep sidebar open" })).toHaveAttribute("aria-pressed", "true");
  });

  it("does not expand on hover while kept open", () => {
    render(<Sidebar path="/" />);
    fireEvent.mouseLeave(panel());
    expect(panel()).toHaveAttribute("data-expanded", "true");
  });
});

// G18: what other pages flag, gathered above Your profile
describe("Needs your eye in the sidebar", () => {
  const LINES: EyeLine[] = [
    { kind: "scam", tone: "danger", text: "S$900 to SCAMMER PTE on 10 Aug 2026 looks like a scam", to: "/transactions?flag=scam&hide_transfers=0" },
    { kind: "needs_rule", tone: "warn", text: "2 statement texts need a rule", to: "/rules" },
    { kind: "stale_accounts", tone: "warn", text: "No statement from POSB for 2 months", to: "/profile" },
  ];
  const item = () => screen.getByRole("button", { name: /^Needs your eye/ });

  afterEach(() => window.history.pushState(null, "", "/"));

  it("sits above Your profile with a count, and scrolls on a short screen", () => {
    render(<Sidebar path="/" eye={LINES} />);
    expect(item()).toHaveAccessibleName("Needs your eye, 3");
    expect(screen.getByTestId("eye-count")).toHaveTextContent("3");
    expect(screen.getByTestId("eye-count")).toHaveClass("text-danger");
    const profile = screen.getByRole("link", { name: "Your profile" });
    expect(item().compareDocumentPosition(profile) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(panel()).toHaveClass("overflow-y-auto");
    expect(panel()).not.toHaveClass("overflow-hidden");
  });

  it("shows a dot in place of the count on the rail, and nothing when nothing is flagged", async () => {
    const { unmount } = render(<Sidebar path="/" eye={LINES} />);
    await userEvent.click(screen.getByRole("button", { name: "Expand on hover" }));
    expect(screen.queryByTestId("eye-count")).not.toBeInTheDocument();
    expect(screen.getByTestId("eye-dot")).toHaveClass("bg-danger");
    unmount();
    const { unmount: again } = render(<Sidebar path="/" eye={LINES.slice(1)} />);
    expect(screen.getByTestId("eye-dot")).toHaveClass("bg-warn");
    again();
    render(<Sidebar path="/" eye={[]} />);
    expect(screen.queryByTestId("eye-dot")).not.toBeInTheDocument();
  });

  it("with nothing to show has no badge, and its list says so", async () => {
    render(<Sidebar path="/" eye={[]} />);
    expect(item()).toHaveAccessibleName("Needs your eye");
    expect(screen.queryByTestId("eye-count")).not.toBeInTheDocument();
    await userEvent.click(item());
    expect(within(screen.getByRole("dialog", { name: "Needs your eye" })).getByText("Nothing needs your eye")).toBeInTheDocument();
  });

  it("opens the list beside the item, each line a link in its tone, and closes when one is followed", async () => {
    render(<Sidebar path="/" eye={LINES} />);
    await userEvent.click(item());
    expect(item()).toHaveAttribute("aria-expanded", "true");
    const list = screen.getByRole("dialog", { name: "Needs your eye" });
    const links = within(list).getAllByRole("link");
    expect(links.map((a) => a.getAttribute("href"))).toEqual(LINES.map((l) => l.to));
    expect(links.map((a) => a.closest("li")?.getAttribute("data-tone"))).toEqual(["danger", "warn", "warn"]);
    await userEvent.click(within(list).getByRole("link", { name: /need a rule/ }));
    expect(window.location.pathname).toBe("/rules");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(item()).toHaveAttribute("aria-expanded", "false");
  });

  it("closes on Escape and gives the focus back to the item", async () => {
    render(<Sidebar path="/" eye={LINES} />);
    await userEvent.click(item());
    await waitFor(() => expect(screen.getByRole("dialog", { name: "Needs your eye" })).toHaveFocus());
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(item()).toHaveFocus();
  });

  it("closes when the focus leaves for somewhere outside the page, such as the browser's address bar", async () => {
    render(<Sidebar path="/" eye={LINES} />);
    await userEvent.click(item());
    const list = screen.getByRole("dialog", { name: "Needs your eye" });
    await waitFor(() => expect(list).toHaveFocus());
    // leaving the page: no element takes the focus, so the blur names none
    fireEvent.blur(list, { relatedTarget: null });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("stays open when the focus moves within it, and a press on its own item still just toggles it", async () => {
    render(<Sidebar path="/" eye={LINES} />);
    await userEvent.click(item());
    const list = screen.getByRole("dialog", { name: "Needs your eye" });
    fireEvent.blur(list, { relatedTarget: within(list).getAllByRole("link")[0] });
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    // a browser that does not focus a pressed button (Safari) blurs with no target; the press on the item decides
    fireEvent.mouseDown(item());
    fireEvent.blur(list, { relatedTarget: null });
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    fireEvent.click(item());
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("keeps the rail open while its list is open", async () => {
    render(<Sidebar path="/" eye={LINES} />);
    await userEvent.click(screen.getByRole("button", { name: "Expand on hover" }));
    await userEvent.click(item());
    fireEvent.mouseLeave(panel());
    expect(panel()).toHaveAttribute("data-expanded", "true");
    act(() => item().click());
    fireEvent.mouseLeave(panel());
    expect(panel()).toHaveAttribute("data-expanded", "false");
  });

  it("opens when Home asks (All N)", () => {
    render(<Sidebar path="/" eye={LINES} />);
    act(() => openEyeList());
    expect(screen.getByRole("dialog", { name: "Needs your eye" })).toBeInTheDocument();
  });
});

describe("Needs your eye on a phone", () => {
  const LINES: EyeLine[] = [{ kind: "upload", tone: "warn", text: "The last upload was checked but not loaded", to: "/upload" }];
  const ref = { current: null };

  afterEach(() => window.history.pushState(null, "", "/"));

  it("puts a dot on the menu button, and none when nothing is flagged", () => {
    const { unmount } = render(<TopBar title="Home" open={false} onOpen={() => {}} buttonRef={ref} eye={LINES} />);
    expect(screen.getByTestId("menu-dot")).toHaveClass("bg-warn");
    expect(screen.getByRole("button", { name: "Open menu" })).toHaveAccessibleDescription("1 thing needs your eye");
    unmount();
    render(<TopBar title="Home" open={false} onOpen={() => {}} buttonRef={ref} eye={[]} />);
    expect(screen.queryByTestId("menu-dot")).not.toBeInTheDocument();
  });

  it("opens the list under the item in the menu, and a followed line closes the menu", async () => {
    const onClose = vi.fn();
    render(<Drawer path="/" holdings={[]} onClose={onClose} returnFocus={ref} eye={LINES} />);
    const menu = screen.getByRole("dialog", { name: "Menu" });
    expect(within(menu).queryByRole("link", { name: /checked but not loaded/ })).not.toBeInTheDocument();
    await userEvent.click(within(menu).getByRole("button", { name: "Needs your eye, 1" }));
    await userEvent.click(within(menu).getByRole("link", { name: /checked but not loaded/ }));
    expect(onClose).toHaveBeenCalled();
    expect(window.location.pathname).toBe("/upload");
  });

  it("can open with the list showing (Home's All N)", () => {
    render(<Drawer path="/" holdings={[]} onClose={() => {}} returnFocus={ref} eye={LINES} eyeFirst />);
    expect(screen.getByRole("link", { name: /checked but not loaded/ })).toHaveAttribute("href", "/upload");
  });

  it("keeps the list in view on a short phone: the close button takes the focus without scrolling back up", () => {
    // the list scrolls itself into view first; a plain focus on the close button at the top would undo that
    const scrolled = vi.fn();
    const focused: { el: Element; opts?: FocusOptions }[] = [];
    const scrollIntoView = Element.prototype.scrollIntoView;
    const focus = HTMLElement.prototype.focus;
    Element.prototype.scrollIntoView = scrolled;
    vi.spyOn(HTMLElement.prototype, "focus").mockImplementation(function (this: HTMLElement, opts?: FocusOptions) {
      focused.push({ el: this, opts });
      focus.call(this, opts);
    });
    try {
      render(<Drawer path="/" holdings={[]} onClose={() => {}} returnFocus={ref} eye={LINES} eyeFirst />);
      expect(scrolled).toHaveBeenCalled();
      const close = screen.getByRole("button", { name: "Close menu" });
      expect(close).toHaveFocus();
      expect(focused.filter((f) => f.el === close).map((f) => f.opts)).toEqual([{ preventScroll: true }]);
    } finally {
      Element.prototype.scrollIntoView = scrollIntoView;
      vi.restoreAllMocks();
    }
  });
});
