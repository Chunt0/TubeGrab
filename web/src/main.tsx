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
