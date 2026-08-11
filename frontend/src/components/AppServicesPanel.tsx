import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Boxes, ExternalLink, RefreshCw, Play, Square, ScrollText, RotateCw, Eye, TriangleAlert } from "lucide-react";
import {
  Button, Card, Row, Stack, SectionLabel, StateBlock, StatusDot, StatusPill,
  Badge, Modal, Skeleton, type StatusKind,
} from "@workshop/ui";

// Run: the single place a project becomes a running thing on this host. Drives
// the backend app_runner — one app made of N services (each its own process +
// port), with build/spawn on a background thread, health probing, and the
// autonomous monitor restarting crashed services. Declared deps (postgres/redis
// /...) are brought up the same way. Viewing a running service happens through
// the backend reverse proxy (the app sets its base to WORKSHOP_PROXY_BASE so
// assets resolve).
//
// Publishing a build to a hosted target (Cloudflare Pages, etc.) is a separate
// concept and lives in DeploysPanel — running the app locally IS NOT the same
// thing as shipping it to a hosted origin.

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
type ManifestService = {
  name: string; cwd: string; build: string | null; start: string;
  enabled: boolean; disabled_reason?: string | null; expose: boolean; depends_on: string[];
};
type Manifest = {
  name: string;
  detected: boolean;
  self?: boolean;
  builtin?: boolean;
  services: ManifestService[];
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

// Docker-daemon-unreachable errors read as scary red dots with no explanation.
// Detect the class so we can say what it means and where it's going (rootless
// container runtime) instead of just painting the dep danger-red.
function isDockerAccessError(msg?: string | null): boolean {
  if (!msg) return false;
  const m = msg.toLowerCase();
  return m.includes("docker") && (m.includes("permission denied") || m.includes("daemon") || m.includes("unavailable") || m.includes("cannot connect"));
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

  // The self / platform project IS the running Workshop — not a separate
  // deployable app. Show what that means instead of Start/Stop controls.
  if (manifest?.self) {
    return (
      <section style={{ marginBottom: "var(--space-6)" }}>
        <Row between style={{ marginBottom: "var(--space-3)" }}>
          <SectionLabel icon={<Boxes aria-hidden />}>Run</SectionLabel>
        </Row>
        <Card>
          <StateBlock
            title="This project is the running Workshop"
            hint="Editing it edits the live app you're using. The frontend updates on save; backend changes need a backend restart." />
        </Card>
      </section>
    );
  }

  // A built-in Workshop app whose live code is in the monorepo, not this project
  // dir — running it as a separate app is meaningless; it already runs as part of
  // Workshop (same reasoning that retired the "Integrated" deployment concept).
  if (manifest?.builtin && manifest.services.length === 0) {
    return (
      <section style={{ marginBottom: "var(--space-6)" }}>
        <Row between style={{ marginBottom: "var(--space-3)" }}>
          <SectionLabel icon={<Boxes aria-hidden />}>Run</SectionLabel>
        </Row>
        <Card>
          <StateBlock
            title="This is a built-in Workshop app"
            hint="It runs as part of Workshop, so there is nothing to start here. Open it from the Apps launcher." />
        </Card>
      </section>
    );
  }

  const services = status?.services ?? [];
  const byName = (n: string) => services.find((x) => x.name === n);
  const anyRunning = services.some((s) => s.status === "running");
  const hasServices = (manifest?.services.length ?? 0) > 0;
  const isUp = status?.phase === "up";
  const isStarting = status?.phase === "starting" || inFlight;
  const phaseKind: StatusKind =
    status?.phase === "up" ? "ok" :
    status?.phase === "starting" ? "running" :
    status?.phase === "partial" ? "warn" : "stopped";
  const phaseLabel = status?.phase === "up" ? "running" : (status?.phase ?? "stopped");

  // Deps only matter to the services that consume them. If the only services
  // that need Docker are disabled (gengui: postgres/redis/falkordb feed `core`,
  // which is off), a down dep is expected — not an alarm. Treat deps as blocking
  // only when an enabled service actually failed to come up.
  const enabledFailed = services.some(
    (s) => (s.status === "failed" || s.status === "crashlooping"),
  );
  const anyEnabledService = (manifest?.services ?? []).some((s) => s.enabled);

  return (
    <section style={{ marginBottom: "var(--space-6)" }}>
      <Row between style={{ marginBottom: "var(--space-3)" }}>
        <SectionLabel icon={<Boxes aria-hidden />}>Run</SectionLabel>
        <Row wrap style={{ gap: "var(--space-2)" }}>
          {status && <StatusPill status={phaseKind}>{phaseLabel}</StatusPill>}
          <Button size="sm" variant="ghost" onClick={() => { loadManifest(); loadStatus(); }}>
            <RefreshCw aria-hidden /> Refresh
          </Button>
          {/* Start only when it's actually stopped. Once it's up/partial, the
              meaningful actions are Restart (re-run bring-up) and Stop — showing a
              primary "Start" on a running app is the confusing signal we removed.
              With nothing runnable declared, there is nothing to start. */}
          {hasServices && !anyRunning && !isStarting && (
            <Button size="sm" variant="primary" loading={busy} disabled={busy} onClick={() => act("start")}>
              <Play aria-hidden /> Start app
            </Button>
          )}
          {(anyRunning || isStarting) && (
            <>
              <Button size="sm" variant="ghost" loading={busy} disabled={busy} onClick={() => act("start")}>
                <RotateCw aria-hidden /> Restart
              </Button>
              <Button size="sm" variant="danger-ghost" loading={busy} disabled={busy} onClick={() => act("stop")}>
                <Square aria-hidden /> Stop
              </Button>
            </>
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
            hint={<code>GET /api/base-apps/run/{slug}/manifest ({err})</code>}
            action={<Button size="sm" onClick={loadManifest}>Retry</Button>}
          />
        ) : !manifest || manifest.services.length === 0 ? (
          <StateBlock
            title="No runnable services"
            hint="Nothing in this project can be started yet. An agent can add the services it should run."
          />
        ) : (
          <Stack>
            <p className="ws-muted" style={{ fontSize: "var(--fs-xs)", margin: 0 }}>
              {manifest.detected
                ? "Services were detected automatically."
                : "Services are declared by this project."}
              {" "}Each runs on its own port, and a crashed one is restarted while the app is up.
            </p>

            <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
              {manifest.services.map((ms) => {
                const s = byName(ms.name);
                const st = s?.status ?? (ms.enabled ? "stopped" : "disabled");
                const running = st === "running";
                // An exposed service whose backend dependency isn't up will load
                // blank — say so instead of handing over an empty iframe.
                const downDeps = running
                  ? ms.depends_on.filter((d) => {
                      const ds = byName(d);
                      return !ds || ds.status !== "running";
                    })
                  : [];
                return (
                  <li key={ms.name} style={{ padding: "var(--space-3) 0", borderBottom: "1px solid var(--border)" }}>
                    <Row wrap style={{ alignItems: "center", gap: "var(--space-2)" }}>
                      <StatusDot status={SVC_KIND[st]}>{running && st === "running" ? "running" : st}</StatusDot>
                      <span className="ws-mono" style={{ fontWeight: "var(--fw-semi)", flex: "0 0 auto" }}>{ms.name}</span>
                      {ms.expose && <Badge>web</Badge>}
                      {s?.port && <span className="ws-muted ws-mono" style={{ fontSize: "var(--fs-xs)" }}>:{s.port}</span>}
                      {s?.healthy === true && <StatusDot status="ok">healthy</StatusDot>}
                      {s?.healthy === false && running && <StatusDot status="warn">no health</StatusDot>}
                      {(s?.restarts ?? 0) > 0 && <Badge>{s!.restarts} restarts</Badge>}
                      <span style={{ flex: "1 1 2rem" }} />
                      {running && (ms.expose || s?.healthy) && (
                        <>
                          <Link to={`/projects/${slug}/run`} style={{ textDecoration: "none" }}>
                            <Button size="sm" variant="ghost"><Eye aria-hidden /> Preview</Button>
                          </Link>
                          <a href={proxyHref(slug, ms.name)} target="_blank" rel="noreferrer" style={{ textDecoration: "none" }}>
                            <Button size="sm" variant="ghost" icon aria-label="Open full screen in new tab"><ExternalLink aria-hidden /></Button>
                          </a>
                        </>
                      )}
                      {ms.enabled && (running || st === "failed" || st === "crashlooping") && (
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
                    {downDeps.length > 0 && (
                      <p className="ws-muted" style={{ fontSize: "var(--fs-xs)", margin: "var(--space-1) 0 0", display: "flex", gap: "var(--space-1)", alignItems: "center" }}>
                        <TriangleAlert size={13} aria-hidden />
                        Running, but <code className="ws-mono">{downDeps.join(", ")}</code> {downDeps.length > 1 ? "are" : "is"} not up, so the app may load blank until {downDeps.length > 1 ? "they're" : "it's"} started.
                      </p>
                    )}
                    {!ms.enabled && (
                      <p className="ws-muted" style={{ fontSize: "var(--fs-xs)", margin: "var(--space-1) 0 0" }}>
                        {ms.disabled_reason
                          ? <>Disabled: {ms.disabled_reason}</>
                          : <>Disabled. Enable it to run it.</>}
                      </p>
                    )}
                  </li>
                );
              })}
            </ul>

            {(status?.deps?.length ?? 0) > 0 && (
              <div>
                <span className="ws-card__meta">Service dependencies</span>
                <Stack gap="sm" style={{ marginTop: "var(--space-1)" }}>
                  {status!.deps.map((d) => {
                    const blocking = !d.ok && enabledFailed;
                    const kind: StatusKind = d.ok ? "ok" : (blocking ? "danger" : "stopped");
                    return (
                      <div key={d.name}>
                        <StatusDot status={kind}>{d.name}{d.ok ? "" : blocking ? " · down" : " · not started"}</StatusDot>
                        {!d.ok && (
                          <p className="ws-muted" style={{ fontSize: "var(--fs-xs)", margin: "var(--space-1) 0 0 var(--space-4)" }}>
                            {isDockerAccessError(d.error)
                              ? "Docker isn't reachable, so these can't start. Any service that needs them stays down."
                              : (d.error || "not running").slice(0, 200)}
                          </p>
                        )}
                      </div>
                    );
                  })}
                  {!anyEnabledService && (
                    <p className="ws-muted" style={{ fontSize: "var(--fs-xs)", margin: 0 }}>
                      Only consumed by disabled services, so these aren't started.
                    </p>
                  )}
                </Stack>
              </div>
            )}

            {isUp && (
              <p className="ws-muted" style={{ fontSize: "var(--fs-xs)", margin: 0 }}>
                Up. Preview shows a service inside Workshop; the open-in-tab icon opens it on its own.
              </p>
            )}

            {err && manifest && (
              <p style={{ color: "var(--danger)", fontSize: "var(--fs-sm)", margin: 0 }}>{err}</p>
            )}
          </Stack>
        )}
      </Card>

      <Modal open={logsFor !== null} onClose={() => setLogsFor(null)} title={logsFor ? `Logs: ${logsFor}` : "Logs"}>
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
