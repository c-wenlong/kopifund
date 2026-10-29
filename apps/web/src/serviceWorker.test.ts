import { describe, expect, it, vi } from "vitest";
import { inDesktopShell, setUpServiceWorker } from "./serviceWorker";

function fakeWindow({ port = "8770", tauri = false, sw = true } = {}) {
  const registration = { unregister: vi.fn(async () => true) };
  const serviceWorker = {
    register: vi.fn(async () => registration),
    getRegistrations: vi.fn(async () => [registration, registration]),
  };
  const w = {
    location: { port },
    navigator: sw ? { serviceWorker } : {},
    ...(tauri ? { __TAURI_INTERNALS__: {} } : {}),
  } as unknown as Window;
  return { w, serviceWorker, registration };
}

describe("the service worker", () => {
  it("is registered in a browser", async () => {
    const { w, serviceWorker } = fakeWindow();
    expect(inDesktopShell(w)).toBe(false);
    await setUpServiceWorker(w);
    expect(serviceWorker.register).toHaveBeenCalledWith("/sw.js", { scope: "/" });
    expect(serviceWorker.getRegistrations).not.toHaveBeenCalled();
  });

  it("is not registered in the desktop window, and an older one there is removed", async () => {
    const { w, serviceWorker, registration } = fakeWindow({ tauri: true });
    await setUpServiceWorker(w);
    expect(serviceWorker.register).not.toHaveBeenCalled();
    expect(registration.unregister).toHaveBeenCalledTimes(2);
  });

  it("knows the desktop window by its fixed port when Tauri sets nothing", async () => {
    const { w, serviceWorker } = fakeWindow({ port: "8771" });
    expect(inDesktopShell(w)).toBe(true);
    await setUpServiceWorker(w);
    expect(serviceWorker.register).not.toHaveBeenCalled();
  });

  it("does nothing where the browser has no service workers", async () => {
    const { w } = fakeWindow({ sw: false });
    await expect(setUpServiceWorker(w)).resolves.toBeUndefined();
  });
});
