import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import { AuthProvider } from "./auth/AuthProvider";
import { Guard } from "./auth/Guard";
import { installAuthFetch } from "./lib/auth";
// Workshop Design System: tokens (CSS custom properties) + primitives, imported
// BEFORE styles.css so the legacy sheet can only layer component classes on top
// of the token values — never redefine them. styles.css's old palette blocks
// were removed; tokens.css owns all theming via [data-theme] on <html>.
import "@workshop/ui/tokens.css";
import "@workshop/ui/styles.css";
import "./styles.css";

// Patch window.fetch before the app mounts so every /api request carries the
// access token and refreshes transparently on expiry.
installAuthFetch();

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <AuthProvider>
        <Guard>
          <App />
        </Guard>
      </AuthProvider>
    </BrowserRouter>
  </React.StrictMode>,
);
