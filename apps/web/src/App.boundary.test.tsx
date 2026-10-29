import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { act } from "react";
import { App } from "./App";
import { navigate } from "./router";
import { APP_FAILED, AppBoundary, PAGE_FAILED } from "./components/ErrorBoundary";
import type { Route } from "./routes";
import { useApi } from "./api";

// Audit: a page that throws as it draws used to unmount the whole app. Here the
// "/settings" page throws while `boom` is set; every other page draws as usual.
let boom = true;
let reads = false;
function Exploding(): never {
  throw new Error("a figure in a shape this build does not know");
}
/** A page that reads its figure off the server's answer, and throws on an answer without one. */
function Reads() {
  const plan = useApi<{ n: number }>("/api/plan");
  return <p>{plan.data ? `Figure ${plan.data.n.toFixed(0)}` : "Loading"}</p>;
}

vi.mock("./routes", async (original) => {
  const real = await original<typeof import("./routes")>();
  return {
    ...real,
    routeFor: (path: string): Route | undefined => {
      const route = real.routeFor(path);
      if (path !== "/settings" || !route) return route;
      return { ...route, component: (props) => (reads ? <Reads /> : boom ? <Exploding /> : <route.component {...props} />) };
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
  reads = false;
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

describe("Try again after a bad answer", () => {
  // Greptile on #305: the answer the page threw on stayed in the cache, so Try again threw on it again
  it("draws the page from the server's new answer, not the kept bad one", async () => {
    reads = true;
    let answer: unknown = {};
    vi.mocked(globalThis.fetch).mockImplementation(async (url) =>
      String(url).startsWith("/api/plan") ? new Response(JSON.stringify(answer), { status: 200 }) : new Promise<Response>(() => {}));
    render(<App />);
    expect(await within(screen.getByRole("main")).findByText(PAGE_FAILED)).toBeInTheDocument();
    answer = { n: 5 };
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("Figure 5")).toBeInTheDocument();
  });
});

describe("the app's last resort", () => {
  it("offers a reload when something outside a page throws", () => {
    render(<AppBoundary><Exploding /></AppBoundary>);
    expect(screen.getByRole("alert")).toHaveTextContent(APP_FAILED);
    expect(screen.getByRole("button", { name: "Reload" })).toBeInTheDocument();
  });
});
