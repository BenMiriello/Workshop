import { useCallback, useEffect, useMemo, useState } from "react";
import { useParams, Link, useNavigate } from "react-router-dom";
import { ArrowLeft, ExternalLink, RefreshCw, TriangleAlert } from "lucide-react";
import { Button, PageHeader, Row, StateBlock, StatusDot, Skeleton } from "@workshop/ui";
import { useCrumbLabel } from "../components/appNav";
import { shortHttpError } from "../lib/httpError";

// Embedded preview of a running app service. The service binds 127.0.0.1; this
// frames it through the backend reverse proxy inside the Workshop chrome, so there
// is always a way back (the header) — unlike opening the bare proxy URL, which
// replaces the page with the app and strands you. The app is served under
// WORKSHOP_PROXY_BASE so its assets + (base-aware) routing resolve through the proxy.

type Service = { name: string; status: string; port: number | null; expose: boolean; healthy: boolean | null };
type AppStatus = { slug: string; phase: string; services: Service[] };
type ManifestService = { name: string; depends_on: string[] };
type Manifest = { services: ManifestService[] };

function backendOrigin(): string {
  const { protocol, hostname, port } = window.location;
  const backendPort = port === "8443" ? "8446" : port === "5173" ? "8000" : port;
  return backendPort ? `${protocol}//${hostname}:${backendPort}` : `${protocol}//${hostname}`;
}

export default function AppPreview() {
  const { slug = "" } = useParams();
  const navigate = useNavigate();
  const [status, setStatus] = useState<AppStatus | null>(null);
  const [manifest, setManifest] = useState<Manifest | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);

  // The overlay injected into the framed app (services/app_overlay.py) posts
  // namespaced, versioned messages up to this shell when its buttons are tapped.
  // Verify the sender is the proxy origin (where the iframe is served) and the
  // type is ours before acting — any frame can postMessage to us.
  useEffect(() => {
    const handler = (e: MessageEvent) => {
      if (e.origin !== backendOrigin()) return;
      const d = e.data;
      if (!d || typeof d !== "object" || d.v !== 1 || typeof d.type !== "string") return;
      if (!d.type.startsWith("workshop/v1/")) return;
      const target = typeof d.slug === "string" ? d.slug : slug;
      if (d.type === "workshop/v1/nav") navigate(`/projects/${target}`);
      else if (d.type === "workshop/v1/openChat") navigate(`/projects/${target}#chat`);
    };
    window.addEventListener("message", handler);
    return () => window.removeEventListener("message", handler);
  }, [navigate, slug]);

  const load = useCallback(async () => {
    setErr(null);
    try {
      const [rs, rm] = await Promise.all([
        fetch(`/api/base-apps/run/${slug}/status`),
        fetch(`/api/base-apps/run/${slug}/manifest`),
      ]);
      if (!rs.ok) throw new Error(shortHttpError(rs.status, rs.statusText, await rs.text().catch(() => "")));
      setStatus(await rs.json());
      if (rm.ok) setManifest(await rm.json());
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }, [slug]);
  useEffect(() => { load(); }, [load]);
  useCrumbLabel(slug);

  // The service to show: the exposed one if running, else any running service.
  const service = useMemo(() => {
    const svcs = status?.services ?? [];
    return svcs.find((s) => s.expose && s.status === "running")
      ?? svcs.find((s) => s.status === "running")
      ?? null;
  }, [status]);

  // A frontend previewed with its API service down loads blank. Name the down
  // dependencies so the empty frame is explained, not mysterious.
  const downDeps = useMemo(() => {
    if (!service) return [];
    const ms = manifest?.services.find((m) => m.name === service.name);
    if (!ms) return [];
    const svcs = status?.services ?? [];
    return ms.depends_on.filter((d) => {
      const ds = svcs.find((s) => s.name === d);
      return !ds || ds.status !== "running";
    });
  }, [service, manifest, status]);

  const src = service ? `${backendOrigin()}/base-apps/run/${slug}/proxy/${service.name}/?_=${nonce}` : "";

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "calc(100dvh - 1rem)" }}>
      <PageHeader
        title={`${slug} · preview`}
        sub={service ? <span>service <code className="ws-mono">{service.name}</code> on <code className="ws-mono">:{service.port}</code></span> : "running app"}
        actions={
          <Row wrap style={{ gap: "var(--space-2)" }}>
            <Link to={`/projects/${slug}`} style={{ textDecoration: "none" }}>
              <Button size="sm" variant="ghost"><ArrowLeft aria-hidden /> Back to project</Button>
            </Link>
            <Button size="sm" variant="ghost" onClick={() => { load(); setNonce((n) => n + 1); }}>
              <RefreshCw aria-hidden /> Reload
            </Button>
            {src && (
              <a href={src} target="_blank" rel="noreferrer" style={{ textDecoration: "none" }}>
                <Button size="sm" variant="ghost"><ExternalLink aria-hidden /> Full screen</Button>
              </a>
            )}
          </Row>
        }
      />

      {err ? (
        <StateBlock variant="error" title="Couldn't load app status"
          hint={<code>GET /api/base-apps/run/{slug}/status ({err})</code>}
          action={<Button size="sm" onClick={load}>Retry</Button>} />
      ) : !status ? (
        <Skeleton width="100%" />
      ) : !service ? (
        <StateBlock title="No running service to preview"
          hint="Start the app from the project page to preview it here."
          action={<Link to={`/projects/${slug}`} style={{ textDecoration: "none" }}><Button size="sm" variant="primary">Go to project</Button></Link>} />
      ) : (
        <div style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column", gap: "var(--space-2)" }}>
          {downDeps.length > 0 ? (
            <Row wrap style={{ gap: "var(--space-2)", alignItems: "center" }}>
              <StatusDot status="warn">partial</StatusDot>
              <span className="ws-muted" style={{ fontSize: "var(--fs-xs)", display: "flex", gap: "var(--space-1)", alignItems: "center" }}>
                <TriangleAlert size={13} aria-hidden />
                <code className="ws-mono">{service!.name}</code> is running but its {downDeps.length > 1 ? "services" : "service"} <code className="ws-mono">{downDeps.join(", ")}</code> {downDeps.length > 1 ? "aren't" : "isn't"} up, so the frame below will likely be blank until you start {downDeps.length > 1 ? "them" : "it"} from the project's Run &amp; Deploy section.
              </span>
            </Row>
          ) : (
            <Row style={{ gap: "var(--space-2)" }}>
              <StatusDot status="ok">running</StatusDot>
              <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>
                Shown inside Workshop. If the app calls its own API, that has to be running too.
              </span>
            </Row>
          )}
          <iframe
            key={nonce}
            title={`${slug} preview`}
            src={src}
            style={{ flex: 1, width: "100%", border: "1px solid var(--border)", borderRadius: "var(--radius-2)", background: "var(--surface)" }}
          />
        </div>
      )}
    </div>
  );
}
