import { useEffect, useState, useCallback } from "react";
import { ExternalLink, RefreshCw } from "lucide-react";
import {
  Button,
  Card,
  CardGrid,
  PageHeader,
  Row,
  SectionLabel,
  StateBlock,
  StatusDot,
  StatusPill,
  type StatusKind,
} from "../ui";

type ServiceStatus = {
  name: string;
  category: string;
  url: string;
  probe_path: string;
  status: "up" | "degraded" | "down";
  http_status: number | null;
  latency_ms: number;
  error?: string;
  notes?: string;
};

type ServicesResp = {
  services: ServiceStatus[];
  summary: { up: number; degraded: number; down: number };
};

const statusKind = (s: ServiceStatus["status"]): StatusKind =>
  s === "up" ? "ok" : s === "degraded" ? "warn" : "danger";

// Backend reports service URLs as 127.0.0.1 (its own loopback). From the
// browser, rewrite to the host the user is actually viewing Workshop from
// so the link resolves to the same machine across LAN/tailnet/localhost.
const externalUrl = (url: string, probe: string) => {
  try {
    const u = new URL(url);
    if (u.hostname === "127.0.0.1" || u.hostname === "localhost") {
      u.hostname = window.location.hostname;
    }
    return u.toString().replace(/\/$/, "") + probe;
  } catch {
    return url + probe;
  }
};

export default function Services() {
  const [data, setData] = useState<ServicesResp | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const r = await fetch("/api/services/");
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      setData(await r.json());
    } catch (e) {
      setErr(String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 10_000);
    return () => clearInterval(t);
  }, [refresh]);

  const refreshBtn = (
    <Button size="sm" onClick={refresh} loading={loading}>
      <RefreshCw aria-hidden /> Refresh
    </Button>
  );

  if (err) {
    return (
      <div>
        <PageHeader title="Services" actions={refreshBtn} />
        <StateBlock
          variant="error"
          title="Couldn't probe services"
          hint={
            <>
              <code>/api/services/</code> — {err}
            </>
          }
          action={
            <Button size="sm" style={{ marginTop: "var(--space-2)" }} onClick={refresh}>
              Retry
            </Button>
          }
        />
      </div>
    );
  }

  if (!data) {
    return (
      <div>
        <PageHeader title="Services" sub="Probing…" actions={refreshBtn} />
      </div>
    );
  }

  const byCat = data.services.reduce<Record<string, ServiceStatus[]>>((acc, s) => {
    (acc[s.category] ||= []).push(s);
    return acc;
  }, {});

  return (
    <div>
      <PageHeader
        title="Services"
        sub={
          <Row wrap>
            <StatusDot status="ok">{data.summary.up} up</StatusDot>
            {data.summary.degraded > 0 && <StatusDot status="warn">{data.summary.degraded} degraded</StatusDot>}
            {data.summary.down > 0 && <StatusDot status="danger">{data.summary.down} down</StatusDot>}
          </Row>
        }
        actions={refreshBtn}
      />

      {Object.entries(byCat).map(([cat, items]) => (
        <section key={cat} style={{ marginBottom: "var(--space-6)" }}>
          <SectionLabel>{cat}</SectionLabel>
          <CardGrid>
            {items.map((s) => {
              const href = externalUrl(s.url, s.probe_path);
              return (
                <li key={s.name}>
                  <Card
                    interactive
                    pad0
                    onClick={() => window.open(href, "_blank", "noopener,noreferrer")}
                  >
                    <a
                      href={href}
                      target="_blank"
                      rel="noopener noreferrer"
                      title={`Open ${href}`}
                      style={{ display: "block", padding: "var(--space-5)", color: "inherit", textDecoration: "none" }}
                      onClick={(e) => e.stopPropagation()}
                    >
                      <Row between style={{ alignItems: "baseline" }}>
                        <h3 className="ws-card__title">
                          {s.name}{" "}
                          <ExternalLink size={14} strokeWidth={1.8} aria-hidden style={{ color: "var(--muted)" }} />
                        </h3>
                        <StatusPill status={statusKind(s.status)}>{s.status}</StatusPill>
                      </Row>
                      <p className="ws-card__meta ws-mono">{href}</p>
                      <p className="ws-card__meta">
                        {s.http_status != null ? `HTTP ${s.http_status} · ` : ""}
                        {s.latency_ms}ms
                      </p>
                      {s.error && (
                        <p className="ws-card__meta" style={{ color: "var(--danger)" }}>
                          {s.error}
                        </p>
                      )}
                    </a>
                  </Card>
                </li>
              );
            })}
          </CardGrid>
        </section>
      ))}
    </div>
  );
}
