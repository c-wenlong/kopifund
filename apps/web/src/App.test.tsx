import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "./App";
import { navigate, useUnsavedGuard } from "./router";
import { openEyeList } from "./components/NeedsYourEye";

// G13: below 768px the sidebar gives way to a top bar and a drawer.
function setWidth(wide: boolean) {
  window.matchMedia = ((query: string) => ({
    matches: wide,
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
}

const original = window.matchMedia;

beforeEach(() => {
  window.localStorage.clear();
  window.history.pushState(null, "", "/settings");
  // no API call ever answers: the shell draws before any figure arrives
  vi.spyOn(globalThis, "fetch").mockImplementation(() => new Promise<Response>(() => {}));
});

afterEach(() => {
  vi.restoreAllMocks();
  window.matchMedia = original;
  window.history.pushState(null, "", "/");
});

const menuButton = () => screen.getByRole("button", { name: "Open menu" });

describe("App shell on a phone", () => {
  beforeEach(() => setWidth(false));

  it("shows a top bar with the page name in place of the sidebar", () => {
    render(<App />);
    expect(screen.queryByTestId("sidebar-panel")).not.toBeInTheDocument();
    expect(within(screen.getByRole("banner")).getByText("Settings")).toBeInTheDocument();
    expect(menuButton()).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("opens the drawer with every page, profile and settings", async () => {
    render(<App />);
    await userEvent.click(menuButton());
    const drawer = screen.getByRole("dialog", { name: "Menu" });
    expect(within(drawer).getByRole("link", { name: "Transactions" })).toBeInTheDocument();
    const links = within(drawer).getAllByRole("link");
    expect(links.slice(-2).map((l) => l.getAttribute("href"))).toEqual(["/profile", "/settings"]);
    expect(within(drawer).getByRole("button", { name: "Close menu" })).toHaveFocus();
    expect(screen.getByRole("main", { hidden: true })).toHaveAttribute("inert");
  });

  it("closes on Escape and gives focus back to the menu button", async () => {
    render(<App />);
    await userEvent.click(menuButton());
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(menuButton()).toHaveFocus();
    expect(screen.getByRole("main")).not.toHaveAttribute("inert");
  });

  it("gives focus back to the menu button even when focus was elsewhere as it opened", async () => {
    // a browser takes focus off a button that turns inert; fireEvent clicks without focusing
    render(<App />);
    fireEvent.click(menuButton());
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    await userEvent.keyboard("{Escape}");
    expect(menuButton()).toHaveFocus();
  });

  // G18: Home's "All N" asks for the Needs your eye list; on a phone it is in the menu
  it("opens the menu on the Needs your eye list when Home asks", () => {
    render(<App />);
    act(() => openEyeList());
    const menu = screen.getByRole("dialog", { name: "Menu" });
    expect(within(menu).getByRole("button", { name: "Needs your eye" })).toHaveAttribute("aria-expanded", "true");
    expect(within(menu).getByText("Nothing needs your eye")).toBeInTheDocument();
  });

  it("closes on a tap on the scrim", async () => {
    render(<App />);
    await userEvent.click(menuButton());
    await userEvent.click(screen.getByTestId("drawer-scrim"));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("closes when a link in it is followed", async () => {
    render(<App />);
    await userEvent.click(menuButton());
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("link", { name: "Your profile" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(window.location.pathname).toBe("/profile");
    expect(within(screen.getByRole("banner")).getByText("Your profile")).toBeInTheDocument();
  });

  it("closes when the current page's own link is tapped", async () => {
    render(<App />);
    await userEvent.click(menuButton());
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("link", { name: "Settings" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("closes when the page changes some other way, such as the back button", async () => {
    render(<App />);
    await userEvent.click(menuButton());
    act(() => navigate("/rules"));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  // G18: a held Back keeps the page, so the drawer must step aside for the question
  it("closes when Back is held by unsaved edits, so Keep editing and Leave can be reached", async () => {
    function Dirty() {
      useUnsavedGuard(true);
      return null;
    }
    act(() => navigate("/rules"));
    act(() => navigate("/settings"));
    render(<><App /><Dirty /></>);
    await userEvent.click(menuButton());
    expect(screen.getByRole("dialog", { name: "Menu" })).toBeInTheDocument();
    act(() => window.history.back());
    await waitFor(() => expect(screen.getByText("Leave without saving?")).toBeInTheDocument());
    expect(screen.queryByRole("dialog", { name: "Menu" })).not.toBeInTheDocument();
    expect(screen.getByRole("main")).not.toHaveAttribute("inert");
    expect(screen.getByRole("button", { name: "Keep editing" })).toHaveFocus();
    await waitFor(() => expect(window.location.pathname).toBe("/settings"));
    // the question gone, the menu opens as before
    await userEvent.click(screen.getByRole("button", { name: "Keep editing" }));
    await userEvent.click(menuButton());
    expect(screen.getByRole("dialog", { name: "Menu" })).toBeInTheDocument();
  });
});

describe("App shell on a desktop", () => {
  beforeEach(() => setWidth(true));

  it("keeps the sidebar and shows no top bar", () => {
    render(<App />);
    expect(screen.getByTestId("sidebar-panel")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Open menu" })).not.toBeInTheDocument();
    expect(screen.queryByRole("banner")).not.toBeInTheDocument();
  });
});
