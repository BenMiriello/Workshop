import { useCallback, useEffect, useMemo, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { ArrowLeft, ExternalLink, RefreshCw } from "lucide-react";
import { Button, PageHeader, Row, StateBlock, StatusDot, Skeleton } from "../ui";
import { useCrumbLabel } from "../components/appNav";

// Embedded preview of a running app service. The service binds 127.0.0.1; this
// frames it through the backend reverse proxy inside the Workshop chrome, so there
// is always a way back (the header) — unlike opening the bare proxy URL, which
// replaces the page with the app and strands you. The app is served under
// WORKSHOP_PROXY_BASE so its assets + (base-aware) routing resolve through the proxy.

type Service = { name: string; status: string; port: number | null; expose: boolean; healthy: boolean | null };
type AppStatus = { slug: string; phase: string; services: Service[] };

function backendOrigin(): string {
  const { protocol, hostname, port } = window.location;
  const backendPort = port === "8443" ? "8446" : port === "5173" ? "8000" : port;
  return backendPort ? `${protocol}//${hostname}:${backendPort}` : `${protocol}//${hostname}`;
}

export default function AppPreview() {
  const { slug = "" } = useParams();
  const [status, setStatus] = useState<AppStatus | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);

  const load = useCallback(async () => {
    setErr(null);
    try {
      const r = await fetch(`/api/base-apps/run/${slug}/status`);
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      setStatus(await r.json());
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
          hint={<code>GET /api/base-apps/run/{slug}/status — {err}</code>}
          action={<Button size="sm" onClick={load}>Retry</Button>} />
      ) : !status ? (
        <Skeleton width="100%" />
      ) : !service ? (
        <StateBlock title="No running service to preview"
          hint="Start the app from the project page, then a running service can be previewed here."
          action={<Link to={`/projects/${slug}`} style={{ textDecoration: "none" }}><Button size="sm" variant="primary">Go to project</Button></Link>} />
      ) : (
        <div style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column", gap: "var(--space-2)" }}>
          <Row style={{ gap: "var(--space-2)" }}>
            <StatusDot status="ok">running</StatusDot>
            <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>
              Framed through the app-runner proxy. If the app calls its own API it needs that service running too.
            </span>
          </Row>
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
