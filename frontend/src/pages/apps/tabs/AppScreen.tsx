import { useEffect, useLayoutEffect } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { Loader2 } from "lucide-react";
import { Button, Row, StateBlock, native, useCrumbLabel } from "@workshop/ui";
import { useAppRun } from "../useAppRun";
import { useAppFrames } from "../../../components/shell/frames/AppFrames";
import { frameKey } from "../../../components/shell/frames/keptFrames";

/**
 * A screen an app SERVES, framed inside the app's own page
 * (docs/platform-redesign/app-screens-2026-09-23.md).
 *
 * Route: `/apps/:appSlug/ui/:service/*`. Whatever follows the service is the
 * path inside the app, so a deep link into it is a real URL here. The frame
 * loads through the backend's reverse proxy on this page's own origin, which
 * the edge relays under `/api`, so no port is guessed.
 *
 * The frame itself belongs to the shell (components/shell/frames), which keeps
 * it alive while you are elsewhere. This page asks for it while the service is
 * running, and while a kept one is waiting for the first status answer, so
 * coming back shows the document as it was left rather than a spinner first.
 */
export default function AppScreen() {
  const { appSlug = "", service = "", "*": rest = "" } = useParams();
  const { search, hash, key: visit } = useLocation();
  const navigate = useNavigate();
  const run = useAppRun(appSlug, service);
  const { frames, show, hide, drop, windowOf } = useAppFrames();
  useCrumbLabel(service);
  const running = run.service?.status === "running";
  const key = frameKey(appSlug, service);
  const kept = frames.some((f) => f.key === key);
  const shown = !run.error && (running || (kept && !run.service));

  // The overlay injected into a framed app (services/app_overlay.py) posts
  // namespaced, versioned messages up to the shell. Only this origin serves
  // the frame, and only our message types are acted on.
  useEffect(() => {
    const handler = (e: MessageEvent) => {
      if (e.origin !== window.location.origin || e.source !== windowOf(key)) return;
      const d = e.data;
      if (!d || typeof d !== "object" || d.v !== 1 || typeof d.type !== "string") return;
      if (d.type === "workshop/v1/nav") navigate(`/apps/${encodeURIComponent(appSlug)}/overview`);
      else if (d.type === "workshop/v1/openChat") navigate(`/apps/${encodeURIComponent(appSlug)}/chats`);
    };
    window.addEventListener("message", handler);
    return () => window.removeEventListener("message", handler);
  }, [navigate, appSlug, windowOf, key]);

  // A native shell gives the app whose frame is shown the keys it registered
  // for its pages while none of them is visible (shared/native-bridge.ts).
  useEffect(() => {
    const shell = native();
    if (!shown || !shell?.has("shell")) return;
    void shell.shell.foreground(appSlug).catch(() => {});
    return () => void shell.shell.foreground(null).catch(() => {});
  }, [shown, appSlug]);

  const base = `/api/base-apps/run/${encodeURIComponent(appSlug)}/proxy/${encodeURIComponent(service)}/`;
  const src = `${base}${rest}${search}${hash}`;

  useLayoutEffect(() => {
    if (!shown) return;
    show({ key, slug: appSlug, title: `${appSlug} ${service}`, src });
    return () => hide(key);
  }, [shown, show, hide, key, appSlug, service, src]);

  // A link to this screen with a hash, followed again or after the app read
  // and cleared it (a chat's link to one of its items), tells the loaded
  // document again: same document, so it hears a hashchange, not a reload.
  useEffect(() => {
    const win = windowOf(key);
    if (!shown || !hash || !win) return;
    try {
      if (win.location.pathname.startsWith(base) && win.location.hash !== hash) win.location.hash = hash;
    } catch { /* a document not of this origin keeps its own hash */ }
  }, [visit, shown, hash, key, base, windowOf]);

  // A service the runner reports as not running has lost whatever its kept
  // document was talking to, so the next visit starts it fresh.
  useEffect(() => {
    if (run.service && !running) drop(key);
  }, [run.service, running, drop, key]);

  const overview = `/apps/${encodeURIComponent(appSlug)}/overview`;

  const svc = run.service?.status;
  const starting = svc === "starting" || svc === "building" || run.phase === "starting"
    || (run.started && !svc);

  const body = run.error ? (
    <StateBlock variant="error" title="Couldn't reach this app"
      hint={<code>{run.error}</code>}
      action={<Button size="sm" onClick={run.reload}>Retry</Button>} />
  ) : starting ? (
    <StateBlock icon={<Loader2 aria-hidden className="ws-spin" />} title="Starting" />
  ) : !run.service && run.phase !== "failed" ? null : (
    <StateBlock variant="error" title="Not running"
      hint={run.service?.error ?? undefined}
      action={
        <Row style={{ gap: "var(--space-2)", justifyContent: "center" }}>
          <Button size="sm" variant="primary" onClick={run.restart}>Restart</Button>
          <Link to={overview}><Button size="sm" variant="ghost">Overview</Button></Link>
        </Row>
      } />
  );

  if (shown) return null;
  return (
    <div style={{ display: "flex", flexDirection: "column", flex: "1 1 auto", width: "100%", minWidth: 0, minHeight: 0, background: "var(--bg)" }}>
      <div style={{ margin: "auto", padding: "var(--space-4)" }}>{body}</div>
    </div>
  );
}
