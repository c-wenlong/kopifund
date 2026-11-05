// The service worker (vite-plugin-pwa, vite.config.ts) is registered here
// rather than by the plugin's injected script, so the desktop window can skip it.

/** The app's port inside Kopifund.app: serve.py's APP_PORT, fixed (SPEC.md, "Desktop app (G23)"). */
const DESKTOP_PORT = "8771";

/** True inside Kopifund.app's window. Tauri sets `__TAURI_INTERNALS__` on every page its
 *  webview loads; the fixed sidecar port is the second sign, in case a Tauri update stops
 *  setting it on a page served from another origin. */
export function inDesktopShell(w: Window = window): boolean {
  return "__TAURI_INTERNALS__" in w || w.location.port === DESKTOP_PORT;
}

/** Register the service worker in a browser. In the desktop window the sidecar is always
 *  there, so there is no offline case to serve, and a cached shell would only show the last
 *  build after an update: none is registered, and one an older build registered is removed.
 *
 *  A page an older worker served may be an older build (that worker answered it from its
 *  own precache), so the page reloads once a newer worker takes over in a browser, or once
 *  the old worker is removed in the desktop window. A first visit, with no worker before,
 *  never reloads. */
export async function setUpServiceWorker(w: Window = window): Promise<void> {
  const sw = w.navigator.serviceWorker;
  if (!sw) return;
  const servedByWorker = !!sw.controller;
  if (inDesktopShell(w)) {
    const registrations = await sw.getRegistrations();
    await Promise.all(registrations.map((r) => r.unregister()));
    if (servedByWorker) w.location.reload();
    return;
  }
  if (servedByWorker) {
    let reloading = false;
    sw.addEventListener("controllerchange", () => {
      if (reloading) return;
      reloading = true;
      w.location.reload();
    });
  }
  await sw.register("/sw.js", { scope: "/" });
}
