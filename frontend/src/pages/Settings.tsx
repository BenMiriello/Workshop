import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { MapPin, TrainFront, Bot, Banknote, Server, Eye, EyeOff, Copy, Check } from "lucide-react";

// Settings page. Logical groupings, no over-engineering. Sections in order
// of how often the operator actually touches them.

type LocationLatest = {
  ts?: string;
  lat?: number;
  lng?: number;
  accuracy_m?: number;
  source?: string;
} | null;

type ServiceProbe = {
  name: string;
  category?: string;
  status: string;
  http_status?: number | null;
  latency_ms?: number | null;
  notes?: string | null;
};

type AgentSummary = {
  id: string;
  name: string;
  memory_blocks: string[];
};

const fmtAge = (iso: string) => {
  const ms = Date.now() - Date.parse(iso);
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
};

export default function Settings() {
  return (
    <div style={{ maxWidth: 760 }}>
      <h2 style={{ margin: "0 0 1.25rem" }}>Settings</h2>
      <LocationSection />
      <CommuteSection />
      <AgentsSection />
      <FinanceSection />
      <SystemSection />
    </div>
  );
}

// ----------------------------------------------------------------------------
// Reusable section shell
// ----------------------------------------------------------------------------
function Section({
  icon: Icon,
  title,
  children,
}: {
  icon: typeof MapPin;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section style={{ marginBottom: "1.5rem" }}>
      <h3 style={{
        margin: "0 0 0.6rem",
        fontSize: "0.85rem",
        letterSpacing: "0.05em",
        textTransform: "uppercase",
        color: "var(--muted)",
        display: "inline-flex",
        alignItems: "center",
        gap: "0.4rem",
      }}>
        <Icon size={14} strokeWidth={1.8} aria-hidden />
        {title}
      </h3>
      <div className="card">{children}</div>
    </section>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{
      display: "grid",
      gridTemplateColumns: "minmax(120px, 0.4fr) 1fr",
      gap: "0.4rem 1rem",
      alignItems: "baseline",
      padding: "0.45rem 0",
      borderBottom: "1px solid var(--border)",
      fontSize: "0.92rem",
    }}>
      <span className="muted" style={{ fontSize: "0.85rem" }}>{label}</span>
      <span>{children}</span>
    </div>
  );
}

// ----------------------------------------------------------------------------
// Location section — Owntracks status + privacy
// ----------------------------------------------------------------------------
function LocationSection() {
  const [latest, setLatest] = useState<LocationLatest>(null);
  const [, setTick] = useState(0);

  const load = useCallback(async () => {
    try {
      const r = await fetch("/api/location/latest");
      const j = r.ok ? await r.json() : { latest: null };
      setLatest((j.latest as LocationLatest) || null);
    } catch {
      setLatest(null);
    }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 30_000);
    const tick = setInterval(() => setTick((x) => x + 1), 10_000);
    return () => { clearInterval(t); clearInterval(tick); };
  }, [load]);

  const ageS = latest?.ts ? Math.max(0, Math.round((Date.now() - Date.parse(latest.ts)) / 1000)) : null;
  const stale = ageS != null && ageS > 30 * 60; // > 30min with no ping → likely a problem
  const noData = !latest?.ts;

  const onClearHistory = async () => {
    if (!confirm("Erase ALL stored location history? This deletes the backend file.")) return;
    try {
      await fetch("/api/location/history", { method: "DELETE" });
      await load();
    } catch (e) {
      alert(String(e));
    }
  };

  return (
    <Section icon={MapPin} title="Location">
      <Row label="status">
        {noData ? (
          <span style={{ color: "var(--err)" }}>no data — Owntracks not publishing yet</span>
        ) : stale ? (
          <span style={{ color: "var(--err)" }}>stale ({fmtAge(latest!.ts!)}) — check Owntracks app</span>
        ) : (
          <span style={{ color: "#16a34a" }}>live · last ping {fmtAge(latest!.ts!)}</span>
        )}
      </Row>
      <Row label="source">{latest?.source || "—"}</Row>
      {latest?.lat != null && (
        <Row label="position">
          <span style={{ fontFamily: "ui-monospace, monospace", fontSize: "0.85rem" }}>
            {latest.lat.toFixed(5)}, {latest.lng?.toFixed(5)}
            {latest.accuracy_m != null && ` · ±${Math.round(latest.accuracy_m)}m`}
          </span>
        </Row>
      )}
      <Row label="iOS app">
        <a href="https://owntracks.org/booklet/features/ios/" target="_blank" rel="noopener noreferrer" style={{ color: "var(--accent)" }}>
          Owntracks
        </a>{" · "}
        config walkthrough: <code style={{ fontSize: "0.8rem" }}>docs/owntracks-ios.md</code>
      </Row>
      <OwntracksCredentials />
      <Row label="history">
        <Link to="/apps/location" style={{ color: "var(--accent)" }}>full history viewer ↗</Link>
        {" · "}
        <button onClick={onClearHistory} style={{ marginLeft: "0.4rem", padding: "0.2rem 0.6rem", fontSize: "0.8rem", color: "var(--err)" }}>
          clear all
        </button>
      </Row>
    </Section>
  );
}

function OwntracksCredentials() {
  const [revealed, setRevealed] = useState(false);
  const [copied, setCopied] = useState<string | null>(null);
  const url = "https://debian.tail38e62e.ts.net:8446/api/location/owntracks";

  const copy = async (label: string, value: string) => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(label);
      setTimeout(() => setCopied(null), 1200);
    } catch {
      // ignore
    }
  };

  return (
    <>
      <Row label="endpoint URL">
        <span style={{ fontFamily: "ui-monospace, monospace", fontSize: "0.78rem", wordBreak: "break-all" }}>{url}</span>
        <button onClick={() => copy("url", url)} style={{ marginLeft: "0.4rem", padding: "0.2rem 0.4rem", fontSize: "0.75rem" }}>
          {copied === "url" ? <Check size={12} strokeWidth={2} /> : <Copy size={12} strokeWidth={1.8} />}
        </button>
      </Row>
      <Row label="auth">
        Basic · user <code style={{ fontSize: "0.8rem" }}>workshop</code> · pass{" "}
        <button onClick={() => setRevealed((v) => !v)} style={{ marginLeft: "0.3rem", padding: "0.2rem 0.4rem", fontSize: "0.75rem" }} title={revealed ? "hide" : "reveal"}>
          {revealed ? <EyeOff size={12} strokeWidth={1.8} /> : <Eye size={12} strokeWidth={1.8} />}
        </button>
        {revealed && (
          <span style={{ marginLeft: "0.4rem", fontSize: "0.75rem", color: "var(--muted)" }}>
            from <code>~/.config/clawdbot/owntracks-auth.env</code>
          </span>
        )}
      </Row>
    </>
  );
}

// ----------------------------------------------------------------------------
// Commute section — links to commute config + 511 key entry
// ----------------------------------------------------------------------------
function CommuteSection() {
  const [services, setServices] = useState<ServiceProbe[] | null>(null);
  const [schedule, setSchedule] = useState<{ home?: { lat?: number | null; lon?: number | null; address?: string }; work?: { lat?: number | null; lon?: number | null; address?: string } } | null>(null);

  const loadSchedule = useCallback(async () => {
    try {
      const r = await fetch("/api/commute/schedule");
      const j = r.ok ? await r.json() : null;
      setSchedule(j);
    } catch {
      setSchedule(null);
    }
  }, []);

  useEffect(() => {
    fetch("/api/services/").then((r) => r.ok ? r.json() : { services: [] }).then((j) => setServices(j.services || []));
    loadSchedule();
  }, [loadSchedule]);

  const realtime = services?.find((s) => s.name?.includes("caltrain.gtfs_rt")) || services?.find((s) => s.name?.includes("511"));

  return (
    <Section icon={TrainFront} title="Commute">
      <Row label="config">
        <a href="https://debian.tail38e62e.ts.net:8444/" target="_blank" rel="noopener noreferrer" style={{ color: "var(--accent)" }}>
          Open Commute · Config tab ↗
        </a>
      </Row>
      <Row label="511 API key">
        {realtime?.status === "ok" ? (
          <span style={{ color: "#16a34a" }}>configured · transit realtime active</span>
        ) : (
          <span style={{ color: "var(--err)" }}>
            missing · register at <a href="https://511.org/open-data/token" target="_blank" rel="noopener noreferrer" style={{ color: "var(--accent)" }}>511.org</a>,
            paste to <code style={{ fontSize: "0.8rem" }}>~/.config/clawdbot/auth.env</code> as <code>WORKSHOP_511_API_KEY</code>
          </span>
        )}
      </Row>
      <CoordsRow label="home coords" name="home" entry={schedule?.home} onSaved={loadSchedule} />
      <CoordsRow label="work coords" name="work" entry={schedule?.work} onSaved={loadSchedule} />
    </Section>
  );
}

function CoordsRow({
  label,
  name,
  entry,
  onSaved,
}: {
  label: string;
  name: "home" | "work";
  entry: { lat?: number | null; lon?: number | null; address?: string } | undefined;
  onSaved: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [lat, setLat] = useState("");
  const [lon, setLon] = useState("");
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const isSet = entry?.lat != null && entry?.lon != null;

  const openEdit = () => {
    setLat(entry?.lat != null ? String(entry.lat) : "");
    setLon(entry?.lon != null ? String(entry.lon) : "");
    setErr(null);
    setEditing(true);
  };

  const save = async () => {
    setSaving(true); setErr(null);
    try {
      const r = await fetch("/api/commute/coords", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, lat: parseFloat(lat), lon: parseFloat(lon) }),
      });
      if (!r.ok) throw new Error(await r.text());
      setEditing(false);
      onSaved();
    } catch (e) {
      setErr(String(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Row label={label}>
      {editing ? (
        <span style={{ display: "inline-flex", gap: "0.3rem", flexWrap: "wrap", alignItems: "center" }}>
          <input
            value={lat}
            onChange={(e) => setLat(e.target.value)}
            placeholder="lat"
            style={{ width: "9ch", padding: "0.2rem 0.4rem", fontSize: "0.85rem", fontFamily: "ui-monospace, monospace" }}
          />
          <input
            value={lon}
            onChange={(e) => setLon(e.target.value)}
            placeholder="lon"
            style={{ width: "10ch", padding: "0.2rem 0.4rem", fontSize: "0.85rem", fontFamily: "ui-monospace, monospace" }}
          />
          <button onClick={save} disabled={saving} style={{ fontSize: "0.8rem", padding: "0.2rem 0.5rem" }}>
            {saving ? "…" : "save"}
          </button>
          <button onClick={() => setEditing(false)} type="button" style={{ fontSize: "0.8rem", padding: "0.2rem 0.5rem" }}>
            cancel
          </button>
          {err && <span style={{ color: "var(--err)", fontSize: "0.78rem" }}>{err}</span>}
        </span>
      ) : (
        <span>
          {isSet ? (
            <span style={{ fontFamily: "ui-monospace, monospace", fontSize: "0.85rem" }}>
              {Number(entry!.lat).toFixed(5)}, {Number(entry!.lon).toFixed(5)}
            </span>
          ) : (
            <span style={{ color: "var(--err)", fontSize: "0.85rem" }}>
              not set · location-aware commute disabled
            </span>
          )}
          <button onClick={openEdit} style={{ marginLeft: "0.5rem", fontSize: "0.78rem", padding: "0.2rem 0.5rem" }}>
            {isSet ? "edit" : "set"}
          </button>
          {entry?.address && (
            <div className="muted" style={{ fontSize: "0.78rem", marginTop: "0.2rem" }}>{entry.address}</div>
          )}
        </span>
      )}
    </Row>
  );
}

// ----------------------------------------------------------------------------
// Agents section — fleet overview, defaults
// ----------------------------------------------------------------------------
function AgentsSection() {
  const [agents, setAgents] = useState<AgentSummary[] | null>(null);

  useEffect(() => {
    fetch("/api/agents/").then((r) => r.ok ? r.json() : []).then((d) => setAgents(Array.isArray(d) ? d : []));
  }, []);

  return (
    <Section icon={Bot} title="Agents">
      <Row label="fleet">
        {agents == null ? "loading…" : `${agents.length} agent${agents.length === 1 ? "" : "s"}`}
        {agents && agents.length > 0 && (
          <span style={{ marginLeft: "0.5rem" }}>
            <Link to="/agents" style={{ color: "var(--accent)" }}>manage ↗</Link>
          </span>
        )}
      </Row>
      <Row label="new agent">
        <Link to="/agents/new" style={{ color: "var(--accent)" }}>create from blank ↗</Link>
      </Row>
      <Row label="memory tree">
        <span className="muted" style={{ fontSize: "0.85rem" }}>
          <code style={{ fontSize: "0.8rem" }}>shared/memory-tree.yaml</code> · edit on disk
        </span>
      </Row>
    </Section>
  );
}

// ----------------------------------------------------------------------------
// Finance section — cache + privacy
// ----------------------------------------------------------------------------
function FinanceSection() {
  const [cache, setCache] = useState<Record<string, { age_s: number; ttl_s: number }> | null>(null);

  const load = useCallback(async () => {
    try {
      const r = await fetch("/api/finance/cache");
      const j = r.ok ? await r.json() : { entries: {} };
      setCache(j.entries || {});
    } catch {
      setCache({});
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const onInvalidate = async () => {
    if (!confirm("Drop the Plaid response cache? Next finance load will refetch from Plaid (~4s).")) return;
    await fetch("/api/finance/cache/invalidate", { method: "POST" });
    await load();
  };

  const entries = cache ? Object.entries(cache) : [];
  return (
    <Section icon={Banknote} title="Finance">
      <Row label="cache">
        {entries.length === 0 ? "no entries" : `${entries.length} key${entries.length === 1 ? "" : "s"}`}
        <button onClick={onInvalidate} style={{ marginLeft: "0.6rem", padding: "0.2rem 0.5rem", fontSize: "0.78rem", color: "var(--err)" }}>
          invalidate all
        </button>
      </Row>
      {entries.map(([k, v]) => (
        <Row key={k} label={k}>
          <span className="muted" style={{ fontSize: "0.85rem", fontFamily: "ui-monospace, monospace" }}>
            age {Math.round(v.age_s)}s · ttl {v.ttl_s}s
          </span>
        </Row>
      ))}
      <Row label="connected">
        <Link to="/apps/finance" style={{ color: "var(--accent)" }}>view balances + transactions ↗</Link>
      </Row>
    </Section>
  );
}

// ----------------------------------------------------------------------------
// System section
// ----------------------------------------------------------------------------
function SystemSection() {
  const [health, setHealth] = useState<{ ok: boolean } | null>(null);
  const [services, setServices] = useState<ServiceProbe[] | null>(null);

  useEffect(() => {
    fetch("/api/healthz").then((r) => r.ok ? r.json() : null).then(setHealth).catch(() => setHealth(null));
    fetch("/api/services/").then((r) => r.ok ? r.json() : { services: [] }).then((j) => setServices(j.services || []));
  }, []);

  const up = services?.filter((s) => s.status === "up").length ?? 0;
  const down = services?.filter((s) => s.status === "down").length ?? 0;

  return (
    <Section icon={Server} title="System">
      <Row label="backend">{health?.ok ? <span style={{ color: "#16a34a" }}>healthy</span> : <span style={{ color: "var(--err)" }}>down</span>}</Row>
      <Row label="services">
        {services == null ? "probing…" : (
          <>
            <span style={{ color: "#16a34a" }}>{up} up</span>
            {down > 0 && <span style={{ marginLeft: "0.6rem", color: "var(--err)" }}>{down} down</span>}
            <span style={{ marginLeft: "0.6rem" }}>
              <Link to="/activity#services" style={{ color: "var(--accent)" }}>details ↗</Link>
            </span>
          </>
        )}
      </Row>
      <Row label="URLs">
        <span className="muted" style={{ fontSize: "0.8rem", display: "block", fontFamily: "ui-monospace, monospace" }}>
          :8443 workshop · :8444 commute · :8445 wearables
        </span>
      </Row>
      <Row label="events">
        <Link to="/activity#events" style={{ color: "var(--accent)" }}>recent event feed ↗</Link>
      </Row>
    </Section>
  );
}
