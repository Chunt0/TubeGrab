import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import { App } from "./App";
import { Router } from "./router";
import { StoreProvider } from "./store";
import { PlayerProvider } from "./player";

// old URLs → new routes
if (location.pathname === "/library") history.replaceState(null, "", "/videos");
const legacy = location.hash.match(/^#\/watch\/(.+)$/);
if (legacy) history.replaceState(null, "", `/watch/${legacy[1]}`);

// app shell + thumbnails on the device (https only; plain-http LAN addresses skip it)
if ("serviceWorker" in navigator && import.meta.env.PROD) {
  addEventListener("load", () => { navigator.serviceWorker.register("/sw.js").catch(() => {}); });
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Router>
      <StoreProvider>
        <PlayerProvider>
          <App />
        </PlayerProvider>
      </StoreProvider>
    </Router>
  </StrictMode>,
);
