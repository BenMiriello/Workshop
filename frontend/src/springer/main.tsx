// Springer Birds — standalone entry. This is the REAL birds app (the same React
// pages as /apps/birds) with the Workshop cross-app wrapper removed, behind a
// login gate. Served as a built bundle by backend/springer_app.py at
// birds.benmiriello.com. The pages call /api/birds/* which springer_app serves,
// scoped to the logged-in user.
import React, { useEffect, useState } from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import "../styles/tokens.css";
import "../styles/components.css";
import "../styles.css";
import { RecorderProvider } from "../pages/apps/birds/recorder";
import { BreadcrumbProvider } from "../components/appNav";
import { BirdsScopeProvider } from "../pages/apps/birds/scope";
import BirdsShell from "../pages/apps/birds/BirdsShell";
import Birds from "../pages/apps/Birds";
import Locations from "../pages/apps/birds/Locations";
import LocationDetail from "../pages/apps/birds/LocationDetail";
import Devices from "../pages/apps/birds/Devices";
import BirdDetail from "../pages/apps/birds/BirdDetail";
import BirdsReview from "../pages/apps/birds/BirdsReview";
import BirdSettings from "../pages/apps/birds/BirdSettings";
import Recordings from "../pages/apps/birds/RecordingsPage";
import Atlas from "../pages/apps/birds/Atlas";
import ForecastWeek from "../pages/apps/birds/ForecastWeek";

// The birds app, no Workshop top-nav. Each page renders its own BirdsHeader.
function BirdsApp() {
  return (
    <RecorderProvider>
      <BreadcrumbProvider>
        <BirdsScopeProvider>
          <div className="ws-root ws-app ws-app--roomy">
            {/* Standalone: no Workshop topnav, so the Birds shell IS the whole
                chrome (sidebar / mobile tabs + scope). Root-mounted — the
                dashboard is "/", not "/apps/birds"; shared page components link to
                "/apps/birds/…" and the build's rewriteBirdsPaths plugin rewrites
                those to root so they match these routes. */}
            <BirdsShell>
              <Routes>
                <Route path="/" element={<Birds />} />
                <Route path="/birds" element={<BirdsReview />} />
                <Route path="/locations" element={<Locations />} />
                <Route path="/location" element={<LocationDetail />} />
                <Route path="/devices" element={<Devices />} />
                <Route path="/bird" element={<BirdDetail />} />
                <Route path="/settings" element={<BirdSettings />} />
                <Route path="/recordings" element={<Recordings />} />
                <Route path="/atlas" element={<Atlas />} />
                <Route path="/forecast" element={<ForecastWeek />} />
                <Route path="*" element={<Navigate to="/" replace />} />
              </Routes>
            </BirdsShell>
          </div>
        </BirdsScopeProvider>
      </BreadcrumbProvider>
    </RecorderProvider>
  );
}

// Auth gate. Anonymous visitors see ONLY this; the app mounts after auth.
// /invite/<token> shows a set-your-own-password screen instead of the login.
function Gate() {
  const inviteToken = window.location.pathname.startsWith("/invite/")
    ? decodeURIComponent(window.location.pathname.slice("/invite/".length)) : "";
  const [state, setState] = useState<"loading" | "anon" | "authed" | "invite" | "badinvite">("loading");
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [inviteEmail, setInviteEmail] = useState("");
  const [pw, setPw] = useState("");
  const [pw2, setPw2] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (inviteToken) {
      fetch(`/api/auth/invite/${encodeURIComponent(inviteToken)}`)
        .then((r) => (r.ok ? r.json() : null))
        .then((info) => {
          if (info?.email) { setInviteEmail(info.email); setName(info.name || ""); setState("invite"); }
          else setState("badinvite");
        })
        .catch(() => setState("badinvite"));
      return;
    }
    fetch("/api/auth/me", { credentials: "same-origin" })
      .then((r) => setState(r.ok ? "authed" : "anon"))
      .catch(() => setState("anon"));
  }, [inviteToken]);

  if (state === "loading") return null;
  if (state === "authed") return <BirdsApp />;

  const shell = (children: React.ReactNode) => (
    <div className="ws-root" style={{ minHeight: "100vh", display: "grid", placeItems: "center", padding: "1rem" }}>
      <div className="ws-card ws-card--pad-lg" style={{ width: "100%", maxWidth: 360 }}>{children}</div>
    </div>
  );

  const doLogin = async (e: React.FormEvent) => {
    e.preventDefault(); setErr(""); setBusy(true);
    try {
      const fd = new FormData(); fd.append("email", email.trim()); fd.append("password", pw);
      const r = await fetch("/api/auth/login", { method: "POST", body: fd, credentials: "same-origin" });
      if (r.ok) { setState("authed"); return; }
      setErr((await r.json().catch(() => ({})))?.detail || "Sign in failed");
    } catch { setErr("Network error"); } finally { setBusy(false); }
  };

  const doRedeem = async (e: React.FormEvent) => {
    e.preventDefault(); setErr("");
    if (pw.length < 8) { setErr("Password must be at least 8 characters."); return; }
    if (pw !== pw2) { setErr("Passwords don't match."); return; }
    setBusy(true);
    try {
      const fd = new FormData(); fd.append("password", pw); if (name.trim()) fd.append("name", name.trim());
      const r = await fetch(`/api/auth/invite/${encodeURIComponent(inviteToken)}`,
        { method: "POST", body: fd, credentials: "same-origin" });
      if (r.ok) { window.history.replaceState({}, "", "/"); setState("authed"); return; }
      setErr((await r.json().catch(() => ({})))?.detail || "Could not set password");
    } catch { setErr("Network error"); } finally { setBusy(false); }
  };

  if (state === "badinvite") return shell(
    <>
      <header className="ws-page-header" style={{ marginBottom: "var(--space-3)" }}>
        <h1 className="ws-page-header__title">Springer Birds</h1>
        <p className="ws-page-header__sub">This invite link is invalid or expired.</p>
      </header>
      <a className="ws-btn ws-btn--block" href="/">Go to sign in</a>
    </>
  );

  if (state === "invite") return shell(
    <form onSubmit={doRedeem}>
      <header className="ws-page-header" style={{ marginBottom: "var(--space-4)" }}>
        <h1 className="ws-page-header__title">Set your password</h1>
        <p className="ws-page-header__sub">{inviteEmail}</p>
      </header>
      <div className="ws-stack">
        <div className="ws-field">
          <label className="ws-field__label" htmlFor="sb-name">Your name</label>
          <input className="ws-input" id="sb-name" value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <div className="ws-field">
          <label className="ws-field__label" htmlFor="sb-pw">Password</label>
          <input className="ws-input" id="sb-pw" type="password" autoComplete="new-password"
            value={pw} onChange={(e) => setPw(e.target.value)} required />
        </div>
        <div className="ws-field">
          <label className="ws-field__label" htmlFor="sb-pw2">Confirm password</label>
          <input className="ws-input" id="sb-pw2" type="password" autoComplete="new-password"
            value={pw2} onChange={(e) => setPw2(e.target.value)} required />
        </div>
        {err && <div style={{ color: "var(--danger)", fontSize: "var(--fs-sm)" }}>{err}</div>}
        <button className="ws-btn ws-btn--primary ws-btn--block" type="submit" disabled={busy}>
          {busy ? "Setting up…" : "Create account"}
        </button>
      </div>
    </form>
  );

  return shell(
    <form onSubmit={doLogin}>
      <header className="ws-page-header" style={{ marginBottom: "var(--space-4)" }}>
        <h1 className="ws-page-header__title">Springer Birds</h1>
      </header>
      <div className="ws-stack">
        <div className="ws-field">
          <label className="ws-field__label" htmlFor="sb-email">Email</label>
          <input className="ws-input" id="sb-email" type="email" autoComplete="username"
            value={email} onChange={(e) => setEmail(e.target.value)} required />
        </div>
        <div className="ws-field">
          <label className="ws-field__label" htmlFor="sb-pw">Password</label>
          <input className="ws-input" id="sb-pw" type="password" autoComplete="current-password"
            value={pw} onChange={(e) => setPw(e.target.value)} required />
        </div>
        {err && <div style={{ color: "var(--danger)", fontSize: "var(--fs-sm)" }}>{err}</div>}
        <button className="ws-btn ws-btn--primary ws-btn--block" type="submit" disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </div>
    </form>
  );
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <Gate />
    </BrowserRouter>
  </React.StrictMode>,
);
