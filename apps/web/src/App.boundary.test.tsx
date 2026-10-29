import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { act } from "react";
import { App } from "./App";
import { navigate } from "./router";
import { APP_FAILED, AppBoundary, PAGE_FAILED } from "./components/ErrorBoundary";
import type { Route } from "./routes";

// Audit: a page that throws as it draws used to unmount the whole app. Here the
// "/settings" page throws while `boom` is set; every other page draws as usual.
let boom = true;
function Exploding(): never {
  throw new Error("a figure in a shape this build does not know");
}

vi.mock("./routes", async (original) => {
  const real = await original<typeof import("./routes")>();
  return {
    ...real,
    routeFor: (path: string): Route | undefined => {
      const route = real.routeFor(path);
      if (path !== "/settings" || !route) return route;
      return { ...route, component: (props) => (boom ? <Exploding /> : <route.component {...props} />) };
    },
  };
});

function setWide() {
  window.matchMedia = ((query: string) => ({
    matches: true, media: query, onchange: null,
    addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
}

const original = window.matchMedia;

beforeEach(() => {
  boom = true;
  setWide();
  window.history.pushState(null, "", "/settings");
  vi.spyOn(globalThis, "fetch").mockImplementation(() => new Promise<Response>(() => {}));
  // React reports each caught error to the console; the test only checks what is drawn
  vi.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  vi.restoreAllMocks();
  window.matchMedia = original;
  window.history.pushState(null, "", "/");
});

describe("a page that fails to draw", () => {
  it("shows a problem in the page and keeps the sidebar and heading", () => {
    render(<App />);
    expect(within(screen.getByRole("main")).getByRole("alert")).toHaveTextContent(PAGE_FAILED);
    expect(screen.getByTestId("sidebar-panel")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1, name: "Settings" })).toBeInTheDocument();
  });

  it("draws the page anew on Try again", async () => {
    render(<App />);
    boom = false;
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(screen.queryByText(PAGE_FAILED)).not.toBeInTheDocument();
  });

  it("starts clean on another page", () => {
    render(<App />);
    act(() => navigate("/rules"));
    expect(screen.queryByText(PAGE_FAILED)).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1, name: "Rules" })).toBeInTheDocument();
  });
});

describe("the app's last resort", () => {
  it("offers a reload when something outside a page throws", () => {
    render(<AppBoundary><Exploding /></AppBoundary>);
    expect(screen.getByRole("alert")).toHaveTextContent(APP_FAILED);
    expect(screen.getByRole("button", { name: "Reload" })).toBeInTheDocument();
  });
});
