// The shared-runtime guard, FIRST, so the shell registers its own React before
// any app bundle can load a second one. See runtime/shared/guard.ts: without
// this line the guard only ever sees an app's copy and has nothing to compare
// it against.
import "./runtime/shared/guard";
import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
import { ensureDeclarations } from "./apps/declarations";
import { AuthProvider } from "./auth/AuthProvider";
import { Guard } from "./auth/Guard";
import { installAuthFetch } from "./lib/auth";
import { queryClient } from "./lib/queryClient";
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

// The app catalog is asked for NOW, beside the session check, rather than after
// it: the routed area waits on both, and in series they were two round trips
// of splash. Signed out, the read is simply asked again after sign-in.
void ensureDeclarations();

// The query cache sits inside AuthProvider, so every query runs signed in, and
// above Guard, so the PIN lock reads through it as well as every route below:
// moving between screens does not throw away what was already read.
ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <AuthProvider>
        <QueryClientProvider client={queryClient}>
          <Guard>
            <App />
          </Guard>
        </QueryClientProvider>
      </AuthProvider>
    </BrowserRouter>
  </React.StrictMode>,
);
