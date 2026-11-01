import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";
import { clearApiCache, resetLink } from "./api";
import { clearToasts } from "./components/Toast";
import { clearModelCache } from "./components/ModelField";

// G13: the API cache lives as long as the module, so each test starts from an
// empty one (and an empty kept copy), as a fresh page would
afterEach(() => {
  clearApiCache();
  resetLink();
  clearToasts();
  clearModelCache();
  try {
    window.localStorage.clear();
  } catch {
    /* no storage */
  }
});

// jsdom has no ResizeObserver; Recharts' ResponsiveContainer needs one to mount.
// A no-op is enough: tests assert on text and links, not on drawn sizes.
if (!("ResizeObserver" in globalThis)) {
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
}
