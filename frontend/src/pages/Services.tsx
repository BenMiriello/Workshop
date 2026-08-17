import { useState, useCallback } from "react";
import { usePoll } from "../lib/usePoll";
import { ExternalLink, KeyRound, RefreshCw, Server } from "lucide-react";
import {
  Button,
  Card,
  CardGrid,
  Row,
  SectionLabel,
  Skeleton,
  Stack,
  StateBlock,
  StatusDot,
  StatusPill,
  type StatusKind,
} from "@workshop/ui";
import { shortHttpError } from "../lib/httpError";

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

// A credential is not a service, which is why it has its own shape and its own
// section. A service is up or down at a URL. A credential is present, proven
// working, or merely configured, and `verified` is which of those the backend
// actually established. See backend/services/auth_health.py.
type CredentialRow = {
  name: string;
  label: string;
  kind: StatusKind;
  state: string;
  detail?: string;
  verified: boolean;
  unverified_because?: string;
  checks?: string;
  account?: string;
  scopes?: string[];
  method?: string;
  plan?: string;
};

type ServicesResp = {
  services: ServiceStatus[];
  summary: { up: number; degraded: number; down: number };
  credentials?: CredentialRow[];
  credentials_summary?: { ok: number; warn: number; danger: number; stopped: number; total: number };
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

/** One credential, with an honest account of how much the check proved.
 *
 * The `verified` line is the point of this card. The GitHub check reaches
 * GitHub, so a pass there is evidence. The Claude check reads local config and
 * cannot detect an expired token, so a pass there is not. Rendering both as the
 * same green pill with no caveat would recreate the silent failure this whole
 * change exists to fix.
 */
function CredentialCard({ c }: { c: CredentialRow }) {
  const facts = [
    c.account,
    c.plan ? `${c.plan} plan` : "",
    c.method,
    c.scopes?.length ? `scopes: ${c.scopes.join(", ")}` : "",
  ].filter(Boolean);

  return (
    <li>
      <Card>
        <Row between style={{ alignItems: "baseline" }}>
          <h3 className="ws-card__title">{c.label}</h3>
          <StatusPill status={c.kind}>{c.state}</StatusPill>
        </Row>
        {facts.length > 0 && <p className="ws-card__meta">{facts.join(" · ")}</p>}
        {c.detail && (
          <p
            className="ws-card__meta"
            style={{ color: c.kind === "ok" ? undefined : "var(--danger)" }}
          >
            {c.detail}
          </p>
        )}
        <p className="ws-card__meta">
          {c.verified ? "Checked against the provider." : "Not verified."}
          {c.checks ? ` Via ${c.checks}.` : ""}
        </p>
        {!c.verified && c.unverified_because && (
          <p className="ws-card__meta">{c.unverified_because}</p>
        )}
      </Card>
    </li>
  );
}

export default function Services() {
  const [data, setData] = useState<ServicesResp | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const r = await fetch("/api/services/");
      if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
      setData(await r.json());
    } catch (e) {
      setErr(String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  usePoll(refresh, 10_000);

  const refreshBtn = (
    <Button size="sm" onClick={refresh} loading={loading}>
      <RefreshCw aria-hidden /> Refresh
    </Button>
  );

  // Services only ever renders embedded, inside Settings > System — never
  // routed on its own (App.tsx redirects the old /services path to
  // /settings). Settings already carries the page's one PageHeader, so this
  // is a SectionLabel, not a second header stacked under it.
  if (err) {
    return (
      <div>
        <Row between style={{ marginBottom: "var(--space-3)" }}>
          <SectionLabel icon={<Server aria-hidden />}>Services</SectionLabel>
          {refreshBtn}
        </Row>
        <StateBlock
          variant="error"
          title="Couldn't probe services"
          hint={
            <>
              <code>/api/services/</code> ({err})
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
        <Row between style={{ marginBottom: "var(--space-3)" }}>
          <SectionLabel icon={<Server aria-hidden />}>Services</SectionLabel>
          {refreshBtn}
        </Row>
        <Card><Stack gap="sm"><Skeleton width="60%" /><Skeleton width="45%" /></Stack></Card>
      </div>
    );
  }

  const byCat = data.services.reduce<Record<string, ServiceStatus[]>>((acc, s) => {
    (acc[s.category] ||= []).push(s);
    return acc;
  }, {});

  const creds = data.credentials ?? [];
  const credsBad = creds.filter((c) => c.kind === "danger").length;
  const credsWarn = creds.filter((c) => c.kind === "warn").length;

  return (
    <div>
      <Row between wrap style={{ marginBottom: "var(--space-3)", alignItems: "center" }}>
        <SectionLabel icon={<Server aria-hidden />}>Services</SectionLabel>
        <Row wrap style={{ gap: "var(--space-3)", alignItems: "center" }}>
          <StatusDot status="ok">{data.summary.up} up</StatusDot>
          {data.summary.degraded > 0 && <StatusDot status="warn">{data.summary.degraded} degraded</StatusDot>}
          {data.summary.down > 0 && <StatusDot status="danger">{data.summary.down} down</StatusDot>}
          {credsBad > 0 && <StatusDot status="danger">{credsBad} credential{credsBad === 1 ? "" : "s"} failing</StatusDot>}
          {credsWarn > 0 && <StatusDot status="warn">{credsWarn} credential{credsWarn === 1 ? "" : "s"} to check</StatusDot>}
        </Row>
        {refreshBtn}
      </Row>

      {creds.length > 0 && (
        <section style={{ marginBottom: "var(--space-6)" }}>
          <SectionLabel>
            <KeyRound size={14} strokeWidth={1.8} aria-hidden /> credentials
          </SectionLabel>
          <CardGrid>
            {creds.map((c) => (
              <CredentialCard key={c.name} c={c} />
            ))}
          </CardGrid>
        </section>
      )}

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
