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
    // Audit: the CSP in index.html is for the built app; Vite's dev server injects inline scripts and styles it would block
    { name: "kopifund-csp-build-only", apply: "serve", transformIndexHtml: (html: string) => html.replace(/\s*<meta http-equiv="Content-Security-Policy"[^>]*>/, "") },
    VitePWA({
      registerType: "autoUpdate",
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
        globPatterns: ["**/*.{js,css,html,svg,png,ico,woff2}"],
        navigateFallbackDenylist: [/^\/api\//],
      },
    }),
  ],
  // `make dev` passes the API's port; 8770 is serve.py's own default
  server: { proxy: { "/api": `http://127.0.0.1:${process.env.KOPIFUND_API_PORT ?? 8770}` } },
  test: { environment: "jsdom", globals: true, setupFiles: ["src/test-setup.ts"] },
});
