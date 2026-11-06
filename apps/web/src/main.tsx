import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import { App } from "./App";
import { AppBoundary } from "./components/ErrorBoundary";
import { setUpServiceWorker } from "./serviceWorker";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <AppBoundary>
      <App />
    </AppBoundary>
  </StrictMode>,
);

window.addEventListener("load", () => { void setUpServiceWorker().catch(() => {}); });
