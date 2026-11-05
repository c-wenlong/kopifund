/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { VitePWA } from "vite-plugin-pwa";

// the config runs in Node; this is the one Node global it reads, typed here
// rather than pulling in @types/node for the whole app
declare const process: { env: Record<string, string | undefined> };

// The Python server (server/serve.py) owns /api and serves the built app
// from apps/web/dist. In dev, Vite serves the app and forwards /api.
export default defineConfig({
  // G13: names this build, so the API answers a page keeps in localStorage
  // (src/api.ts) are only ever painted by the build that kept them
  define: { __KOPIFUND_BUILD__: JSON.stringify(Date.now().toString(36)) },
  plugins: [
    react(),
    tailwindcss(),
    VitePWA({
      registerType: "autoUpdate",
      // src/serviceWorker.ts registers it, and skips it inside the desktop window
      injectRegister: false,
      manifest: {
        name: "Kopifund",
        short_name: "Kopifund",
        description: "Your money across every Singapore account, from the statements you already get.",
        // the page token's light value (--page in src/index.css): the app opens light, so the splash must not flash dark
        theme_color: "#f6f5f0",
        background_color: "#f6f5f0",
        display: "standalone",
        icons: [
          { src: "icon-192.png", sizes: "192x192", type: "image/png", purpose: "any" },
          { src: "icon-512.png", sizes: "512x512", type: "image/png", purpose: "any" },
          // The art sits in the middle 80%, inside the circle a maskable crop always keeps.
          { src: "icon-maskable-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
        ],
      },
      workbox: {
        // A page is always asked of the server first, so the first open after a
        // new build (`make serve`, build_macos.sh) runs that build, and a page asked
        // of 8770 during `make dev` reaches serve.py's redirect to 5173. index.html
        // is precached only as the offline fallback: no precache route answers a
        // page (no navigation fallback, no "/" to index.html mapping), and when the
        // server does not answer, any page, /settings included, gets that copy.
        globPatterns: ["**/*.{js,css,html,svg,png,ico,woff2}"],
        navigateFallback: null,
        directoryIndex: null,
        runtimeCaching: [{
          urlPattern: ({ request, url }) => request.mode === "navigate" && !url.pathname.startsWith("/api/"),
          handler: "NetworkFirst",
          options: { cacheName: "pages", networkTimeoutSeconds: 5, precacheFallback: { fallbackURL: "index.html" } },
        }],
        // set by the plugin only when it injects the register script itself:
        // a new worker takes over at once instead of waiting for every tab to close
        skipWaiting: true,
        clientsClaim: true,
      },
    }),
  ],
  // `make dev` passes the API's port; 8770 is serve.py's own default
  server: { proxy: { "/api": `http://127.0.0.1:${process.env.KOPIFUND_API_PORT ?? 8770}` } },
  test: { environment: "jsdom", globals: true, setupFiles: ["src/test-setup.ts"] },
});
