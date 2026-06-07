import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
// Workshop Design System: tokens (CSS custom properties) + primitives, imported
// BEFORE styles.css so the legacy sheet can only layer component classes on top
// of the token values — never redefine them. styles.css's old palette blocks
// were removed; tokens.css owns all theming via [data-theme] on <html>.
import "./styles/tokens.css";
import "./styles/components.css";
import "./styles.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>,
);
