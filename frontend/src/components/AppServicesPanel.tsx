import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Boxes, ExternalLink, RefreshCw, Play, Square, ScrollText, RotateCw, Eye } from "lucide-react";
import {
  Button, Card, Row, Stack, SectionLabel, StateBlock, StatusDot, StatusPill,
  Badge, Modal, Skeleton, type StatusKind,
} from "../ui";

// Multi-service app runner panel. Drives the backend app_runner: one app made of
// N services (each its own process + port), with build/spawn on a background
// thread, health probing, and the autonomous monitor restarting crashed services.
// The "where to see it" link points at a running service through the backend
// reverse proxy (the app sets its base to WORKSHOP_PROXY_BASE so assets resolve).

type Service = {
  name: string;
  status: "pending" | "building" | "starting" | "running" | "failed" | "stopped" | "disabled" | "crashlooping";
  pid: number | null;
  port: number | null;
  url: string | null;
  expose: boolean;
  healthy: boolean | null;
  error: string | null;
  restarts: number;
};
type Dep = { name: string; ok: boolean; error?: string | null };
type AppStatus = {
  slug: string;
  phase: string;
  desired: string;
  manifest_detected?: boolean;
  services: Service[];
  deps: Dep[];
};
type Manifest = {
  name: string;
  detected: boolean;
  services: { name: string; cwd: string; build: string | null; start: string; enabled: boolean; expose: boolean; depends_on: string[] }[];
  deps: { name: string }[];
};

const SVC_KIND: Record<Service["status"], StatusKind> = {
  running: "ok",
  building: "running",
  starting: "running",
  pending: "warn",
  failed: "danger",
  crashlooping: "danger",
  stopped: "stopped",
  disabled: "stopped",
};

// The running services are bound to 127.0.0.1 and reachable through the backend
// reverse proxy. The backend is served on its own tailnet port (8446) separate
// from this Vite origin (8443); in local dev it is :8000. Build the absolute URL
// to that origin so the proxied app (which uses this exact path as its base)
// loads with working asset paths.
function proxyHref(slug: string, service: string): string {
  const base = `/base-apps/run/${slug}/proxy/${service}/`;
  const { protocol, hostname, port } = window.location;
  const backendPort = port === "8443" ? "8446" : port === "5173" ? "8000" : port;
  const origin = backendPort ? `${protocol}//${hostname}:${backendPort}` : `${protocol}//${hostname}`;
  return origin + base;
}

export default function AppServicesPanel({ slug }: { slug: string }) {
  const [manifest, setManifest] = useState<Manifest | null>(null);
  const [status, setStatus] = useState<AppStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [logsFor, setLogsFor] = useState<string | null>(null);
  const [logText, setLogText] = useState("");
  const timer = useRef<number | null>(null);

  const loadStatus = useCallback(async () => {
    try {
      const r = await fetch(`/api/base-apps/run/${slug}/status`);
      if (r.ok) setStatus(await r.json());
    } catch { /* transient */ }
  }, [slug]);

  const loadManifest = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const r = await fetch(`/api/base-apps/run/${slug}/manifest`);
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      setManifest(await r.json());
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [slug]);

  useEffect(() => { loadManifest(); loadStatus(); }, [loadManifest, loadStatus]);

  // Poll while anything is in flight (starting/building) so the live state
  // machine is reflected without manual refresh.
  const inFlight = useMemo(
    () => status?.phase === "starting" ||
      (status?.services || []).some((s) => s.status === "building" || s.status === "starting"),
    [status],
  );
  useEffect(() => {
    if (timer.current) window.clearInterval(timer.current);
    timer.current = window.setInterval(loadStatus, inFlight ? 2000 : 6000) as unknown as number;
    return () => { if (timer.current) window.clearInterval(timer.current); };
  }, [inFlight, loadStatus]);

  const act = useCallback(async (path: string) => {
    setBusy(true);
    setErr(null);
    try {
      const r = await fetch(`/api/base-apps/run/${slug}/${path}`, { method: "POST" });
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      setStatus(await r.json());
      // A start kicks off background work; nudge a couple of refreshes.
      setTimeout(loadStatus, 1500);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [slug, loadStatus]);

  const openLogs = useCallback(async (service: string) => {
    setLogsFor(service);
    setLogText("Loading…");
    try {
      const r = await fetch(`/api/base-apps/run/${slug}/logs?service=${encodeURIComponent(service)}`);
      const d = await r.json();
      setLogText(d.log || "(no output yet)");
    } catch (e) {
      setLogText(String(e));
    }
  }, [slug]);

  const services = status?.services ?? [];
  const anyRunning = services.some((s) => s.status === "running");
  const phaseKind: StatusKind =
    status?.phase === "up" ? "ok" :
    status?.phase === "starting" ? "running" :
    status?.phase === "partial" ? "warn" : "stopped";

  return (
    <section style={{ marginBottom: "var(--space-6)" }}>
      <Row between style={{ marginBottom: "var(--space-3)" }}>
        <SectionLabel icon={<Boxes aria-hidden />}>App</SectionLabel>
        <Row wrap style={{ gap: "var(--space-2)" }}>
          {status && <StatusPill status={phaseKind}>{status.phase}</StatusPill>}
          <Button size="sm" variant="ghost" onClick={() => { loadManifest(); loadStatus(); }}>
            <RefreshCw aria-hidden /> Refresh
          </Button>
          <Button size="sm" variant="primary" loading={busy} disabled={busy} onClick={() => act("start")}>
            <Play aria-hidden /> Start app
          </Button>
          {anyRunning && (
            <Button size="sm" variant="danger-ghost" loading={busy} disabled={busy} onClick={() => act("stop")}>
              <Square aria-hidden /> Stop
            </Button>
          )}
        </Row>
      </Row>

      <Card>
        {loading && !manifest ? (
          <Stack gap="sm"><Skeleton width="60%" /><Skeleton width="45%" /></Stack>
        ) : err && !manifest ? (
          <StateBlock
            variant="error"
            title="Couldn't resolve the app manifest"
            hint={<code>GET /api/base-apps/run/{slug}/manifest — {err}</code>}
            action={<Button size="sm" onClick={loadManifest}>Retry</Button>}
          />
        ) : !manifest || manifest.services.length === 0 ? (
          <StateBlock
            title="No runnable services"
            hint="Add a workshop.app.json with a services[] list, or a package.json / deploy.config.yaml the runner can auto-detect."
          />
        ) : (
          <Stack>
            <p className="ws-muted" style={{ fontSize: "var(--fs-xs)", margin: 0 }}>
              {manifest.detected
                ? "Auto-detected manifest. Add a workshop.app.json to declare services explicitly."
                : "Declared in workshop.app.json."}
              {" "}Services run as scoped subprocesses on centrally-allocated ports; the monitor restarts a crashed service while the app is running.
            </p>

            <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
              {manifest.services.map((ms) => {
                const s = services.find((x) => x.name === ms.name);
                const st = s?.status ?? (ms.enabled ? "stopped" : "disabled");
                return (
                  <li key={ms.name} style={{ padding: "var(--space-3) 0", borderBottom: "1px solid var(--border)" }}>
                    <Row wrap style={{ alignItems: "center", gap: "var(--space-2)" }}>
                      <StatusDot status={SVC_KIND[st]}>{st}</StatusDot>
                      <span className="ws-mono" style={{ fontWeight: "var(--fw-semi)", flex: "0 0 auto" }}>{ms.name}</span>
                      {ms.expose && <Badge>web</Badge>}
                      {s?.port && <span className="ws-muted ws-mono" style={{ fontSize: "var(--fs-xs)" }}>:{s.port}</span>}
                      {s?.healthy === true && <StatusDot status="ok">healthy</StatusDot>}
                      {s?.healthy === false && st === "running" && <StatusDot status="warn">no health</StatusDot>}
                      {(s?.restarts ?? 0) > 0 && <Badge>{s!.restarts} restarts</Badge>}
                      <span style={{ flex: "1 1 2rem" }} />
                      {st === "running" && (ms.expose || s?.healthy) && (
                        <>
                          <Link to={`/projects/${slug}/run`} style={{ textDecoration: "none" }}>
                            <Button size="sm" variant="ghost"><Eye aria-hidden /> Preview</Button>
                          </Link>
                          <a href={proxyHref(slug, ms.name)} target="_blank" rel="noreferrer" style={{ textDecoration: "none" }}>
                            <Button size="sm" variant="ghost" icon aria-label="Open full screen in new tab"><ExternalLink aria-hidden /></Button>
                          </a>
                        </>
                      )}
                      {ms.enabled && (st === "running" || st === "failed" || st === "crashlooping") && (
                        <Button size="sm" variant="ghost" loading={busy} onClick={() => act(`services/${ms.name}/restart`)}>
                          <RotateCw aria-hidden /> Restart
                        </Button>
                      )}
                      <Button size="sm" variant="ghost" onClick={() => openLogs(ms.name)}>
                        <ScrollText aria-hidden /> Logs
                      </Button>
                    </Row>
                    {s?.error && (
                      <p className="ws-mono" style={{ color: "var(--danger)", fontSize: "var(--fs-xs)", margin: "var(--space-1) 0 0" }}>
                        {s.error}
                      </p>
                    )}
                    {!ms.enabled && (
                      <p className="ws-muted" style={{ fontSize: "var(--fs-xs)", margin: "var(--space-1) 0 0" }}>
                        Disabled in the manifest. Enable it (and supply any env/deps it needs) to run it.
                      </p>
                    )}
                  </li>
                );
              })}
            </ul>

            {(status?.deps?.length ?? 0) > 0 && (
              <div>
                <span className="ws-card__meta">Dependencies (docker)</span>
                <Row wrap style={{ gap: "var(--space-2)", marginTop: "var(--space-1)" }}>
                  {status!.deps.map((d) => (
                    <StatusDot key={d.name} status={d.ok ? "ok" : "danger"}>{d.name}</StatusDot>
                  ))}
                </Row>
              </div>
            )}

            {err && manifest && (
              <p style={{ color: "var(--danger)", fontSize: "var(--fs-sm)", margin: 0 }}>{err}</p>
            )}
          </Stack>
        )}
      </Card>

      <Modal open={logsFor !== null} onClose={() => setLogsFor(null)} title={logsFor ? `Logs — ${logsFor}` : "Logs"}>
        <pre className="ws-mono" style={{
          maxHeight: "60vh", overflow: "auto", fontSize: "var(--fs-xs)",
          background: "var(--surface-2)", padding: "var(--space-3)", borderRadius: "var(--radius-2)",
          whiteSpace: "pre-wrap", margin: 0,
        }}>
          {logText}
        </pre>
        <Row end style={{ marginTop: "var(--space-3)" }}>
          {logsFor && <Button size="sm" variant="ghost" onClick={() => openLogs(logsFor)}><RefreshCw aria-hidden /> Reload</Button>}
        </Row>
      </Modal>
    </section>
  );
}
