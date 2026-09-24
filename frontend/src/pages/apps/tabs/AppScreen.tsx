import { useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { ExternalLink, Hourglass, Maximize2, RefreshCw } from "lucide-react";
import { Button, Row, StateBlock, StatusDot, useCrumbLabel } from "@workshop/ui";
import { useAppRun } from "../useAppRun";

/**
 * A screen an app SERVES, framed inside the app's own page
 * (docs/platform-redesign/app-screens-2026-09-23.md).
 *
 * Route: `/apps/:appSlug/ui/:service/*`. Whatever follows the service is the
 * path inside the app, so a deep link into it is a real URL here. The frame
 * loads through the backend's reverse proxy on this page's own origin, which
 * the edge relays under `/api`, so no port is guessed.
 */
export default function AppScreen() {
  const { appSlug = "", service = "", "*": rest = "" } = useParams();
  const { search, hash } = useLocation();
  const navigate = useNavigate();
  const run = useAppRun(appSlug, service);
  const [nonce, setNonce] = useState(0);
  const box = useRef<HTMLDivElement>(null);
  useCrumbLabel(service);

  // The overlay injected into a framed app (services/app_overlay.py) posts
  // namespaced, versioned messages up to the shell. Only this origin serves
  // the frame, and only our message types are acted on.
  useEffect(() => {
    const handler = (e: MessageEvent) => {
      if (e.origin !== window.location.origin) return;
      const d = e.data;
      if (!d || typeof d !== "object" || d.v !== 1 || typeof d.type !== "string") return;
      if (d.type === "workshop/v1/nav") navigate(`/apps/${encodeURIComponent(appSlug)}/overview`);
      else if (d.type === "workshop/v1/openChat") navigate(`/apps/${encodeURIComponent(appSlug)}/chats`);
    };
    window.addEventListener("message", handler);
    return () => window.removeEventListener("message", handler);
  }, [navigate, appSlug]);

  const base = `/api/base-apps/run/${encodeURIComponent(appSlug)}/proxy/${encodeURIComponent(service)}/`;
  const src = `${base}${rest}${search}${hash}`;
  const overview = `/apps/${encodeURIComponent(appSlug)}/overview`;
  const running = run.service?.status === "running";

  const body = run.error ? (
    <StateBlock variant="error" title="Couldn't reach this app"
      hint={<code>{run.error}</code>}
      action={<Button size="sm" onClick={run.reload}>Retry</Button>} />
  ) : run.phase === "failed" ? (
    <StateBlock variant="error" title="This app failed to start"
      hint="Its services and logs are on the app's Overview."
      action={<Link to={overview}><Button size="sm" variant="primary">Open Overview</Button></Link>} />
  ) : !running ? (
    <StateBlock icon={<Hourglass aria-hidden />} title={run.started ? "Starting the app" : "Waiting for the app"}
      hint={`Showing ${service} as soon as it answers.`} />
  ) : (
    <iframe key={nonce} title={`${appSlug} ${service}`} src={src}
      style={{ flex: 1, width: "100%", border: 0, background: "var(--card)" }} />
  );

  return (
    <div ref={box} style={{ display: "flex", flexDirection: "column", flex: "1 1 auto", width: "100%", minWidth: 0, height: "calc(100dvh - 1rem)", background: "var(--bg)" }}>
      <Row style={{ gap: "var(--space-2)", alignItems: "center", padding: "var(--space-1) var(--space-2)" }}>
        <StatusDot status={running ? "running" : run.phase === "failed" || run.error ? "danger" : "warn"}>
          {running ? "running" : run.phase ?? "loading"}
        </StatusDot>
        <span style={{ flex: 1 }} />
        <Button size="sm" variant="ghost" icon aria-label="Reload" disabled={!running}
          onClick={() => { run.reload(); setNonce((n) => n + 1); }}>
          <RefreshCw aria-hidden />
        </Button>
        <Button size="sm" variant="ghost" icon aria-label="Full screen" disabled={!running}
          onClick={() => { void box.current?.requestFullscreen?.(); }}>
          <Maximize2 aria-hidden />
        </Button>
        <a href={src} target="_blank" rel="noreferrer" aria-label="Open in a new tab">
          <Button size="sm" variant="ghost" icon disabled={!running}><ExternalLink aria-hidden /></Button>
        </a>
      </Row>
      {body}
    </div>
  );
}
