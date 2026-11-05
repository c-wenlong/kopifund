import { describe, expect, it, vi } from "vitest";
import { inDesktopShell, setUpServiceWorker } from "./serviceWorker";

function fakeWindow({ port = "8770", tauri = false, sw = true, controlled = false } = {}) {
  const registration = { unregister: vi.fn(async () => true) };
  const listeners: Record<string, () => void> = {};
  const serviceWorker = {
    controller: controlled ? {} : null,
    register: vi.fn(async () => registration),
    getRegistrations: vi.fn(async () => [registration, registration]),
    addEventListener: vi.fn((type: string, fn: () => void) => { listeners[type] = fn; }),
    fire: (type: string) => listeners[type]?.(),
  };
  const reload = vi.fn();
  const w = {
    location: { port, reload },
    navigator: sw ? { serviceWorker } : {},
    ...(tauri ? { __TAURI_INTERNALS__: {} } : {}),
  } as unknown as Window;
  return { w, serviceWorker, registration, reload };
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

  it("reloads once when a newer worker takes over a page an older one served", async () => {
    const { w, serviceWorker, reload } = fakeWindow({ controlled: true });
    await setUpServiceWorker(w);
    serviceWorker.fire("controllerchange");
    serviceWorker.fire("controllerchange");
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it("never reloads a first visit", async () => {
    const { w, serviceWorker, reload } = fakeWindow();
    await setUpServiceWorker(w);
    serviceWorker.fire("controllerchange");
    expect(reload).not.toHaveBeenCalled();
  });

  it("reloads the desktop window from the server once an older worker is removed", async () => {
    const controlled = fakeWindow({ tauri: true, controlled: true });
    await setUpServiceWorker(controlled.w);
    expect(controlled.reload).toHaveBeenCalledTimes(1);
    const fresh = fakeWindow({ tauri: true });
    await setUpServiceWorker(fresh.w);
    expect(fresh.reload).not.toHaveBeenCalled();
  });
});
