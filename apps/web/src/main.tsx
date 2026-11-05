import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import { App } from "./App";
import { setUpServiceWorker } from "./serviceWorker";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);

window.addEventListener("load", () => { void setUpServiceWorker().catch(() => {}); });
