import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  MapPin,
  TrainFront,
  Bot,
  Banknote,
  Server,
  Heart,
  Eye,
  EyeOff,
  Copy,
  Check,
  Power,
  Cpu,
  Palette,
  Sun,
  Moon,
  Monitor,
  Trash2,
  FolderGit2,
  Network,
  UserRound,
  LogOut,
  Smartphone,
} from "lucide-react";
import {
  Button,
  Card,
  ConfirmDialog,
  PageHeader,
  SectionLabel,
  Segmented,
  StatusDot,
  useTheme,
  StatusPill,
  Input,
  type SegmentedOption,
  type ThemeMode,
} from "../ui";
import { useAuth } from "../auth/AuthProvider";
import { native } from "../native/bridge";
import { useNative, useTrackingStatus } from "../native/useNative";
import { startDeviceTracking, stopDeviceTracking } from "../native/location";
import type { PermissionState } from "@shared/native-bridge";

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
      <PageHeader title="Settings" />
      <AccountSection />
      <DeviceSection />
      <AppearanceSection />
      <BaseAppsSection />
      <PlatformSection />
      <LocationSection />
      <HealthSection />
      <CommuteSection />
      <AgentsSection />
      <AutonomySection />
      <FinanceSection />
      <SystemSection />
    </div>
  );
}

// ----------------------------------------------------------------------------
// Base Apps section — pointer into Settings → Base Apps subroute. Holds the
// tenant-level git-repo listing previously at /projects. See ADR-008.
// ----------------------------------------------------------------------------
function BaseAppsSection() {
  return (
    <Section icon={FolderGit2} title="Base Apps">
      <Row label="repos">
        <Link to="/settings/base-apps" style={{ color: "var(--accent)" }}>
          tenant repos + branches ↗
        </Link>
        <span className="ws-muted" style={{ marginLeft: "0.5rem", fontSize: "var(--fs-xs)" }}>
          (foundational tooling: workshop, clawdbot, infra)
        </span>
      </Row>
    </Section>
  );
}

// ----------------------------------------------------------------------------
// Platform section: pointer into Settings -> Grants. Cross-app + guest access
// control (capability grants). Operator is always fully permitted; these govern
// shared/guest access only.
// ----------------------------------------------------------------------------
function PlatformSection() {
  return (
    <Section icon={Network} title="Platform">
      <Row label="grants">
        <Link to="/settings/grants" style={{ color: "var(--accent)" }}>
          capability map + cross-app/guest access ↗
        </Link>
        <span className="ws-muted" style={{ marginLeft: "0.5rem", fontSize: "var(--fs-xs)" }}>
          (governs shared/guest access only)
        </span>
      </Row>
    </Section>
  );
}

// ----------------------------------------------------------------------------
// Reusable section shell — design-system SectionLabel + Card.
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
    <section style={{ marginBottom: "var(--space-6)" }}>
      <SectionLabel icon={<Icon aria-hidden />}>{title}</SectionLabel>
      <Card>{children}</Card>
    </section>
  );
}

// Label-beside-value row. Uses the inline-field grid; a hairline separates rows.
function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div
      className="ws-field ws-field--inline"
      style={{ alignItems: "baseline", padding: "var(--space-2) 0", borderBottom: "1px solid var(--border)" }}
    >
      <span className="ws-field__label ws-muted">{label}</span>
      <span>{children}</span>
    </div>
  );
}

// ----------------------------------------------------------------------------
// This device — native capabilities, surfaced only inside the iOS shell (the
// bridge is absent in a plain browser, so this whole section hides there).
// Lets the operator control background location tracking, see the offline fix
// queue, enable notifications, and check Live Activity readiness. See
// shared/native-bridge.ts + frontend/src/native/*.
// ----------------------------------------------------------------------------
function DeviceSection() {
  const { isShell, tier, has } = useNative();
  const tracking = useTrackingStatus(4000);
  const [busy, setBusy] = useState(false);
  const [notifPerm, setNotifPerm] = useState<PermissionState | null>(null);
  const [laSupported, setLaSupported] = useState<boolean | null>(null);

  useEffect(() => {
    const n = native();
    if (!n) return;
    if (n.has("localNotify")) n.localNotify.permissionStatus().then(setNotifPerm).catch(() => {});
    if (n.has("liveActivity")) n.liveActivity.isSupported().then(setLaSupported).catch(() => {});
  }, []);

  if (!isShell) return null;

  const isTracking = !!tracking?.tracking;

  async function toggleTracking() {
    setBusy(true);
    try {
      if (isTracking) await stopDeviceTracking();
      else await startDeviceTracking({ mode: "significant" });
    } catch {
      /* permission denied etc. — the status row reflects it on next poll */
    } finally {
      setBusy(false);
    }
  }

  async function enableNotifs() {
    const n = native();
    if (!n?.has("localNotify")) return;
    try {
      setNotifPerm(await n.localNotify.requestPermission());
    } catch {
      /* ignore */
    }
  }

  const inline = { display: "inline-flex", alignItems: "center", gap: "var(--space-3)", flexWrap: "wrap" } as const;

  return (
    <Section icon={Smartphone} title="This device">
      <Row label="app">
        {tier === "paid" ? <StatusPill status="ok">paid</StatusPill> : <span className="ws-muted">free tier</span>}
      </Row>

      {has("location") && (
        <>
          <Row label="location tracking">
            <span style={inline}>
              {isTracking ? (
                <StatusDot status="ok">on{tracking?.mode ? ` · ${tracking.mode}` : ""}</StatusDot>
              ) : (
                <StatusDot status="stopped">off</StatusDot>
              )}
              <Button
                size="sm"
                variant={isTracking ? "danger-ghost" : "ghost"}
                loading={busy}
                disabled={busy}
                onClick={toggleTracking}
              >
                {isTracking ? "Stop" : "Start"}
              </Button>
            </span>
          </Row>
          {tracking?.permission && tracking.permission !== "granted" && (
            <Row label="permission">
              <StatusDot status="warn">{tracking.permission}</StatusDot>
            </Row>
          )}
          {typeof tracking?.queued === "number" && tracking.queued > 0 && (
            <Row label="offline queue">{tracking.queued} fixes waiting to send</Row>
          )}
          {tracking?.last?.lat != null && tracking?.last?.lng != null && (
            <Row label="last fix">
              <span className="ws-mono" style={{ fontSize: "var(--fs-xs)" }}>
                {tracking.last.lat.toFixed(4)}, {tracking.last.lng.toFixed(4)}
              </span>
            </Row>
          )}
        </>
      )}

      {has("localNotify") && (
        <Row label="notifications">
          {notifPerm === "granted" ? (
            <StatusDot status="ok">enabled</StatusDot>
          ) : (
            <span style={inline}>
              <StatusDot status={notifPerm === "denied" ? "danger" : "warn"}>{notifPerm ?? "checking…"}</StatusDot>
              {notifPerm !== "denied" && (
                <Button size="sm" variant="ghost" onClick={enableNotifs}>
                  Enable
                </Button>
              )}
            </span>
          )}
        </Row>
      )}

      {has("liveActivity") && (
        <Row label="live activity">
          {laSupported ? (
            <StatusDot status="ok">ready</StatusDot>
          ) : (
            <span className="ws-muted">needs the Widget extension build (see mobile/README)</span>
          )}
        </Row>
      )}
    </Section>
  );
}

// ----------------------------------------------------------------------------
// Account — the signed-in user + sign out. Product-auth layer (see
// backend/routes/auth.py). Operator accounts reach the single-owner apps; other
// accounts get a session and per-user data as apps are partitioned.
// ----------------------------------------------------------------------------
function AccountSection() {
  const { user, logout, changePassword } = useAuth();
  const [busy, setBusy] = useState(false);
  const [cur, setCur] = useState("");
  const [next, setNext] = useState("");
  const [pwBusy, setPwBusy] = useState(false);
  const [pwMsg, setPwMsg] = useState<{ ok: boolean; text: string } | null>(null);
  if (!user) return null;

  async function submitPw(e: React.FormEvent) {
    e.preventDefault();
    if (pwBusy) return;
    setPwBusy(true);
    setPwMsg(null);
    try {
      await changePassword(cur, next);
      setCur("");
      setNext("");
      setPwMsg({ ok: true, text: "Password updated" });
    } catch (err) {
      setPwMsg({ ok: false, text: err instanceof Error ? err.message : "Could not change password" });
    } finally {
      setPwBusy(false);
    }
  }

  return (
    <Section icon={UserRound} title="Account">
      <Row label="signed in as">{user.name ? `${user.name} · ${user.email}` : user.email}</Row>
      <Row label="role">
        {user.is_operator ? (
          <StatusPill status="ok">operator</StatusPill>
        ) : (
          <span className="ws-muted">member</span>
        )}
      </Row>
      <Row label="password">
        <form onSubmit={submitPw} style={{ display: "flex", flexWrap: "wrap", gap: "var(--space-2)", alignItems: "center" }}>
          <Input
            type="password"
            value={cur}
            onChange={(e) => setCur(e.target.value)}
            placeholder="current"
            autoComplete="current-password"
            style={{ maxWidth: 150 }}
          />
          <Input
            type="password"
            value={next}
            onChange={(e) => setNext(e.target.value)}
            placeholder="new (8+ chars)"
            autoComplete="new-password"
            style={{ maxWidth: 150 }}
          />
          <Button type="submit" size="sm" variant="ghost" loading={pwBusy} disabled={pwBusy || !cur || next.length < 8}>
            Change
          </Button>
          {pwMsg && (
            <span style={{ fontSize: "var(--fs-xs)", color: pwMsg.ok ? "var(--ok)" : "var(--danger)" }}>{pwMsg.text}</span>
          )}
        </form>
      </Row>
      <Row label="session">
        <span style={{ display: "inline-flex", gap: "var(--space-2)", flexWrap: "wrap" }}>
          <Button
            variant="danger-ghost"
            size="sm"
            loading={busy}
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              await logout();
            }}
          >
            <LogOut size={16} strokeWidth={1.8} aria-hidden /> Sign out
          </Button>
          <Button
            variant="ghost"
            size="sm"
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              await logout(true);
            }}
          >
            Sign out everywhere
          </Button>
        </span>
      </Row>
    </Section>
  );
}

// ----------------------------------------------------------------------------
// Appearance — theme switch (the only writer of localStorage["ws-theme"]).
// Commits ON CHANGE (a view preference): light / dark / system. Default dark.
// ----------------------------------------------------------------------------
const THEME_OPTIONS: SegmentedOption<ThemeMode>[] = [
  { value: "light", label: <><Sun size={15} strokeWidth={1.8} aria-hidden /> Light</> },
  { value: "dark", label: <><Moon size={15} strokeWidth={1.8} aria-hidden /> Dark</> },
  { value: "system", label: <><Monitor size={15} strokeWidth={1.8} aria-hidden /> System</> },
];

function AppearanceSection() {
  const [theme, setTheme] = useTheme();
  return (
    <Section icon={Palette} title="Appearance">
      <Row label="theme">
        <Segmented options={THEME_OPTIONS} value={theme} onChange={setTheme} ariaLabel="Theme" />
      </Row>
    </Section>
  );
}

// ----------------------------------------------------------------------------
// Location section — Owntracks status + privacy
// ----------------------------------------------------------------------------
function LocationSection() {
  const [latest, setLatest] = useState<LocationLatest>(null);
  const [, setTick] = useState(0);
  const [confirmClear, setConfirmClear] = useState(false);
  const [clearing, setClearing] = useState(false);

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
    setClearing(true);
    try {
      await fetch("/api/location/history", { method: "DELETE" });
      await load();
    } finally {
      setClearing(false);
      setConfirmClear(false);
    }
  };

  return (
    <Section icon={MapPin} title="Location">
      <Row label="status">
        {noData ? (
          <StatusDot status="danger">no data — Owntracks not publishing yet</StatusDot>
        ) : stale ? (
          <StatusDot status="warn">stale ({fmtAge(latest!.ts!)}) — check Owntracks app</StatusDot>
        ) : (
          <StatusDot status="ok">live · last ping {fmtAge(latest!.ts!)}</StatusDot>
        )}
      </Row>
      <Row label="source">{latest?.source || "—"}</Row>
      {latest?.lat != null && (
        <Row label="position">
          <span className="ws-mono" style={{ fontSize: "var(--fs-sm)" }}>
            {latest.lat.toFixed(5)}, {latest.lng?.toFixed(5)}
            {latest.accuracy_m != null && ` · ±${Math.round(latest.accuracy_m)}m`}
          </span>
        </Row>
      )}
      <Row label="iOS app">
        <a href="https://owntracks.org/booklet/features/ios/" target="_blank" rel="noopener noreferrer" style={{ color: "var(--accent)" }}>
          Owntracks
        </a>{" · "}
        config walkthrough: <code style={{ fontSize: "var(--fs-xs)" }}>docs/owntracks-ios.md</code>
      </Row>
      <OwntracksCredentials />
      <Row label="history">
        <Link to="/apps/location" style={{ color: "var(--accent)" }}>full history viewer ↗</Link>
        {" · "}
        <Button size="sm" variant="danger-ghost" onClick={() => setConfirmClear(true)}>
          <Trash2 aria-hidden /> Clear all
        </Button>
      </Row>
      <ConfirmDialog
        open={confirmClear}
        onCancel={() => setConfirmClear(false)}
        onConfirm={onClearHistory}
        title="Clear all location history?"
        body="This deletes the stored history file on the backend. This can't be undone."
        confirmLabel="Clear history"
        busy={clearing}
      />
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
// Health section — Apple Health data via Health Auto Export iOS app
// ----------------------------------------------------------------------------
function HealthSection() {
  const [metrics, setMetrics] = useState<Record<string, { ts?: string; date?: string; qty?: unknown; units?: string }> | null>(null);
  const [, setTick] = useState(0);

  const load = useCallback(async () => {
    try {
      const r = await fetch("/api/health/latest");
      const j = r.ok ? await r.json() : { metrics: {} };
      setMetrics(j.metrics || {});
    } catch {
      setMetrics({});
    }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 60_000);
    const tick = setInterval(() => setTick((x) => x + 1), 15_000);
    return () => { clearInterval(t); clearInterval(tick); };
  }, [load]);

  const onClear = async () => {
    if (!confirm("Erase ALL stored Apple Health data?")) return;
    await fetch("/api/health/history", { method: "DELETE" });
    await load();
  };

  const entries = metrics ? Object.entries(metrics) : [];
  // Latest ts across all metrics → top-line status
  const latestTs = entries.reduce<string | null>((acc, [, v]) => {
    if (!v?.ts) return acc;
    if (!acc) return v.ts;
    return v.ts > acc ? v.ts : acc;
  }, null);
  const ageS = latestTs ? Math.max(0, Math.round((Date.now() - Date.parse(latestTs)) / 1000)) : null;
  const stale = ageS != null && ageS > 24 * 3600; // > 24h with no push → likely a problem

  return (
    <Section icon={Heart} title="Health (Apple Health)">
      <Row label="status">
        {!latestTs ? (
          <StatusDot status="danger">no data — install Health Auto Export</StatusDot>
        ) : stale ? (
          <StatusDot status="warn">stale ({fmtAge(latestTs)}) — check Health Auto Export</StatusDot>
        ) : (
          <StatusDot status="ok">{entries.length} metric{entries.length === 1 ? "" : "s"} · last push {fmtAge(latestTs)}</StatusDot>
        )}
      </Row>
      <Row label="iOS app">
        <a href="https://apps.apple.com/us/app/health-auto-export/id1115567069" target="_blank" rel="noopener noreferrer" style={{ color: "var(--accent)" }}>
          Health Auto Export
        </a>{" · "}~$5 · config walkthrough at <code style={{ fontSize: "0.8rem" }}>docs/health-auto-export-ios.md</code>
      </Row>
      <HealthCredentials />
      {entries.slice(0, 8).map(([name, v]) => (
        <Row key={name} label={name}>
          <span style={{ fontFamily: "ui-monospace, monospace", fontSize: "0.85rem" }}>
            {String(v.qty ?? "—")}{v.units ? ` ${v.units}` : ""}
            {v.date && <span className="muted" style={{ marginLeft: "0.5rem", fontSize: "0.78rem" }}>{v.date}</span>}
          </span>
        </Row>
      ))}
      <Row label="history">
        <button onClick={onClear} style={{ padding: "0.2rem 0.6rem", fontSize: "0.8rem", color: "var(--err)" }}>
          clear all data
        </button>
      </Row>
    </Section>
  );
}

function HealthCredentials() {
  const [revealed, setRevealed] = useState(false);
  const [copied, setCopied] = useState<string | null>(null);
  const url = "https://debian.tail38e62e.ts.net:8446/api/health/ingest";

  const copy = async (label: string, value: string) => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(label);
      setTimeout(() => setCopied(null), 1200);
    } catch { /* ignore */ }
  };

  return (
    <>
      <Row label="endpoint URL">
        <span style={{ fontFamily: "ui-monospace, monospace", fontSize: "0.78rem", wordBreak: "break-all" }}>{url}</span>
        <button onClick={() => copy("health-url", url)} style={{ marginLeft: "0.4rem", padding: "0.2rem 0.4rem", fontSize: "0.75rem" }}>
          {copied === "health-url" ? <Check size={12} strokeWidth={2} /> : <Copy size={12} strokeWidth={1.8} />}
        </button>
      </Row>
      <Row label="auth">
        Basic · user <code style={{ fontSize: "0.8rem" }}>workshop</code> · pass{" "}
        <button onClick={() => setRevealed((v) => !v)} style={{ marginLeft: "0.3rem", padding: "0.2rem 0.4rem", fontSize: "0.75rem" }}>
          {revealed ? <EyeOff size={12} strokeWidth={1.8} /> : <Eye size={12} strokeWidth={1.8} />}
        </button>
        {revealed && (
          <span style={{ marginLeft: "0.4rem", fontSize: "0.75rem", color: "var(--muted)" }}>
            from <code>~/.config/clawdbot/health-auth.env</code>
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
          <StatusDot status="ok">configured · transit realtime active</StatusDot>
        ) : (
          <span style={{ color: "var(--danger)" }}>
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
// Autonomy & GPU section
// Three independent off-switches:
//   - Supervisor heartbeats (Wave-2 Letta loop, in-process)
//   - Cron autonomy (Wave-1 Ralph loop, via AUTONOMY_DISABLED flag file)
//   - Ollama GPU residency (unload resident models to free VRAM)
// Plus a single "Stop everything" that toggles supervisor + cron together.
// ----------------------------------------------------------------------------
type AutonomyStatus = {
  supervisor: {
    paused: boolean;
    paused_at: string | null;
    started_at: string | null;
    agent_count: number;
  };
  cron_autonomy: {
    disabled: boolean;
    disabled_at: string | null;
    flag_path: string;
  };
};

type LoadedModel = {
  name: string;
  size_vram_bytes: number | null;
  expires_at: string | null;
};

type OllamaStatus = {
  ollama_up: boolean;
  loaded_models: LoadedModel[];
  gpu: {
    available: boolean;
    memory_used_mib?: number;
    memory_free_mib?: number;
    memory_total_mib?: number;
    utilization_pct?: number;
    error?: string;
  };
};

const fmtMiB = (m: number | undefined) =>
  m == null ? "—" : m >= 1024 ? `${(m / 1024).toFixed(1)} GiB` : `${m} MiB`;

const fmtBytes = (b: number | null) => {
  if (b == null) return "—";
  if (b >= 1024 ** 3) return `${(b / 1024 ** 3).toFixed(1)} GiB`;
  if (b >= 1024 ** 2) return `${(b / 1024 ** 2).toFixed(0)} MiB`;
  return `${b} B`;
};

type BrokerLease = {
  id: string;
  app: string;
  vram_budget_mib: number;
  vram_actual_mib: number;
  priority: number;
  state: string;
  reason: string;
  implicit: boolean;
};

type BrokerStatus = {
  available: boolean;
  mode?: string;
  total_vram_mib?: number;
  committed_mib?: number;
  free_budget_mib?: number;
  active_lease_count?: number;
  queue_depth?: number;
  my_leases?: BrokerLease[];
};

function AutonomySection() {
  const [auto, setAuto] = useState<AutonomyStatus | null>(null);
  const [oll, setOll] = useState<OllamaStatus | null>(null);
  const [bro, setBro] = useState<BrokerStatus | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [confirmStopAll, setConfirmStopAll] = useState(false);
  const [confirmUnload, setConfirmUnload] = useState(false);

  const load = useCallback(async () => {
    try {
      const [a, o, b] = await Promise.all([
        fetch("/api/agents/autonomy/status").then((r) => r.ok ? r.json() : null),
        fetch("/api/ollama/status").then((r) => r.ok ? r.json() : null),
        fetch("/api/gpu/broker/status").then((r) => r.ok ? r.json() : { available: false }),
      ]);
      setAuto(a);
      setOll(o);
      setBro(b);
    } catch {
      // keep prior state
    }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 15_000);
    return () => clearInterval(t);
  }, [load]);

  const post = useCallback(async (label: string, path: string) => {
    setBusy(label);
    try {
      await fetch(path, { method: "POST" });
      await load();
    } finally {
      setBusy(null);
    }
  }, [load]);

  const supPaused = !!auto?.supervisor.paused;
  const cronDisabled = !!auto?.cron_autonomy.disabled;
  const allStopped = supPaused && cronDisabled;

  const onStopAll = async () => {
    setBusy("stop-all");
    try {
      // Order matters: pause supervisor and disable cron FIRST so nothing
      // can reload models the moment after we unload them.
      await Promise.all([
        fetch("/api/agents/supervisor/pause", { method: "POST" }),
        fetch("/api/agents/autonomy/disable", { method: "POST" }),
      ]);
      await fetch("/api/ollama/unload", { method: "POST" });
      await load();
    } finally {
      setBusy(null);
      setConfirmStopAll(false);
    }
  };

  const onStartAll = async () => {
    setBusy("start-all");
    try {
      await Promise.all([
        fetch("/api/agents/supervisor/resume", { method: "POST" }),
        fetch("/api/agents/autonomy/enable", { method: "POST" }),
      ]);
      await load();
    } finally {
      setBusy(null);
    }
  };

  const onUnload = async () => {
    setBusy("unload");
    try {
      await fetch("/api/ollama/unload", { method: "POST" });
      await load();
    } finally {
      setBusy(null);
      setConfirmUnload(false);
    }
  };

  return (
    <Section icon={Power} title="Autonomy & GPU">
      <Row label="all autonomy">
        {auto == null ? "loading…" : (
          <span style={{ display: "inline-flex", alignItems: "center", gap: "var(--space-3)", flexWrap: "wrap" }}>
            <StatusDot status={allStopped ? "stopped" : supPaused || cronDisabled ? "warn" : "running"}>
              {allStopped ? "stopped" : supPaused || cronDisabled ? "partial" : "running"}
            </StatusDot>
            {allStopped ? (
              <Button size="sm" variant="primary" loading={busy === "start-all"} disabled={busy != null} onClick={onStartAll}>
                Start all
              </Button>
            ) : (
              <Button size="sm" variant="danger-ghost" disabled={busy != null} onClick={() => setConfirmStopAll(true)}>
                <Power aria-hidden /> Stop all
              </Button>
            )}
          </span>
        )}
      </Row>

      <Row label="supervisor">
        {auto == null ? "—" : (
          <span style={{ display: "inline-flex", alignItems: "center", gap: "var(--space-3)", flexWrap: "wrap" }}>
            <StatusDot status={supPaused ? "warn" : "running"}>
              {supPaused ? "paused" : `ticking ${auto.supervisor.agent_count} agent${auto.supervisor.agent_count === 1 ? "" : "s"}`}
            </StatusDot>
            <Button
              size="sm"
              variant={supPaused ? "default" : "danger-ghost"}
              disabled={busy != null}
              onClick={() => post(supPaused ? "resume" : "pause", supPaused ? "/api/agents/supervisor/resume" : "/api/agents/supervisor/pause")}
            >
              {busy === "pause" ? "pausing…" : busy === "resume" ? "resuming…" : supPaused ? "Resume" : "Pause"}
            </Button>
            {supPaused && auto.supervisor.paused_at && (
              <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>
                since {fmtAge(auto.supervisor.paused_at)}
              </span>
            )}
          </span>
        )}
      </Row>

      <Row label="cron loop">
        {auto == null ? "—" : (
          <span style={{ display: "inline-flex", alignItems: "center", gap: "var(--space-3)", flexWrap: "wrap" }}>
            <StatusDot status={cronDisabled ? "stopped" : "running"}>
              {cronDisabled ? "disabled" : "enabled (Ralph fires every 20m)"}
            </StatusDot>
            <Button
              size="sm"
              variant={cronDisabled ? "default" : "danger-ghost"}
              disabled={busy != null}
              onClick={() => post(cronDisabled ? "enable" : "disable", cronDisabled ? "/api/agents/autonomy/enable" : "/api/agents/autonomy/disable")}
            >
              {busy === "enable" ? "enabling…" : busy === "disable" ? "disabling…" : cronDisabled ? "Enable" : "Disable"}
            </Button>
          </span>
        )}
      </Row>

      <Row label="gpu memory">
        {oll == null || !oll.gpu.available ? (
          oll?.gpu.error ? <span className="ws-muted">no nvidia-smi</span> : "loading…"
        ) : (
          <span style={{ display: "inline-flex", alignItems: "center", gap: "var(--space-3)", flexWrap: "wrap" }}>
            <span className="ws-mono" style={{ fontSize: "var(--fs-sm)" }}>
              {fmtMiB(oll.gpu.memory_used_mib)} / {fmtMiB(oll.gpu.memory_total_mib)}
              {" · "}{oll.gpu.utilization_pct}% util
            </span>
            <Button
              size="sm"
              variant="danger-ghost"
              disabled={busy != null || !oll.ollama_up || oll.loaded_models.length === 0}
              onClick={() => setConfirmUnload(true)}
            >
              {busy === "unload" ? "unloading…" : "Free GPU"}
            </Button>
          </span>
        )}
      </Row>

      <Row label="broker">
        {bro == null ? "—" : !bro.available ? (
          <span className="ws-muted">gpubrokerd offline · Workshop running standalone</span>
        ) : (
          <span style={{ display: "inline-flex", alignItems: "center", gap: "var(--space-3)", flexWrap: "wrap" }}>
            <StatusDot status="ok">up</StatusDot>
            <span className="ws-mono" style={{ fontSize: "var(--fs-sm)" }}>
              mode={bro.mode} · committed {fmtMiB(bro.committed_mib)}/{fmtMiB(bro.total_vram_mib)}
              {" · "}q={bro.queue_depth} · {bro.active_lease_count} lease{bro.active_lease_count === 1 ? "" : "s"}
            </span>
          </span>
        )}
      </Row>

      <ConfirmDialog
        open={confirmStopAll}
        onCancel={() => setConfirmStopAll(false)}
        onConfirm={onStopAll}
        title="Stop all autonomy and free GPU?"
        body="Pauses supervisor heartbeats, disables the cron autonomy loop, and unloads all resident Ollama models. The next manual start reloads them (~20s warm-up)."
        confirmLabel="Stop all"
        busy={busy === "stop-all"}
      />
      <ConfirmDialog
        open={confirmUnload}
        onCancel={() => setConfirmUnload(false)}
        onConfirm={onUnload}
        title="Free GPU memory?"
        body="Unloads all resident Ollama models. The next agent tick reloads qwen2.5:32b (~20s warm-up)."
        confirmLabel="Free GPU"
        busy={busy === "unload"}
      />

      {bro?.available && (
        <Row label="my leases">
          {bro.my_leases && bro.my_leases.length > 0 ? (
            <div style={{ display: "flex", flexDirection: "column", gap: "0.2rem" }}>
              {bro.my_leases.map((l) => (
                <span key={l.id} style={{ fontFamily: "ui-monospace, monospace", fontSize: "0.78rem" }}>
                  {l.app} · {fmtMiB(l.vram_budget_mib)} budget · {fmtMiB(l.vram_actual_mib)} actual · pri {l.priority}
                  {l.implicit ? " · implicit" : ""}
                </span>
              ))}
              <span>
                <button
                  onClick={async () => {
                    setBusy("release-mine");
                    try {
                      await fetch("/api/gpu/broker/release-mine", { method: "POST" });
                      await load();
                    } finally { setBusy(null); }
                  }}
                  disabled={busy != null}
                  style={{ padding: "0.2rem 0.6rem", fontSize: "0.78rem", color: "var(--err)" }}
                >
                  {busy === "release-mine" ? "releasing…" : "release all mine"}
                </button>
              </span>
            </div>
          ) : (
            <span className="muted">none</span>
          )}
        </Row>
      )}

      {bro?.available && (
        <Row label="operator hold">
          <button
            onClick={async () => {
              if (!confirm("Reserve GPU for you exclusively for 30 minutes? Workshop and other apps will be preempted.")) return;
              setBusy("reserve");
              try {
                await fetch("/api/gpu/broker/reserve", {
                  method: "POST",
                  headers: { "Content-Type": "application/json" },
                  body: JSON.stringify({ duration_s: 1800, reason: "operator hold via UI" }),
                });
                await load();
              } finally { setBusy(null); }
            }}
            disabled={busy != null}
            style={{ padding: "0.2rem 0.6rem", fontSize: "0.78rem" }}
          >
            {busy === "reserve" ? "reserving…" : "reserve for me (30m)"}
          </button>
          <button
            onClick={async () => {
              setBusy("boost");
              try {
                await fetch("/api/gpu/broker/boost", {
                  method: "POST",
                  headers: { "Content-Type": "application/json" },
                  body: JSON.stringify({ priority: 80, ttl_s: 1800 }),
                });
                await load();
              } finally { setBusy(null); }
            }}
            disabled={busy != null}
            style={{ marginLeft: "0.5rem", padding: "0.2rem 0.6rem", fontSize: "0.78rem" }}
          >
            {busy === "boost" ? "boosting…" : "boost workshop priority (30m)"}
          </button>
        </Row>
      )}

      {oll && oll.loaded_models.length > 0 && (
        <Row label="loaded">
          <div style={{ display: "flex", flexDirection: "column", gap: "0.15rem" }}>
            {oll.loaded_models.map((m) => (
              <span key={m.name} style={{ fontFamily: "ui-monospace, monospace", fontSize: "0.8rem" }}>
                <Cpu size={11} strokeWidth={1.8} style={{ verticalAlign: "middle", marginRight: "0.3rem" }} />
                {m.name} · {fmtBytes(m.size_vram_bytes)}
              </span>
            ))}
          </div>
        </Row>
      )}
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
      <Row label="backend">{health?.ok ? <StatusDot status="ok">healthy</StatusDot> : <StatusDot status="danger">down</StatusDot>}</Row>
      <Row label="services">
        {services == null ? "probing…" : (
          <span style={{ display: "inline-flex", alignItems: "center", gap: "var(--space-3)", flexWrap: "wrap" }}>
            <StatusDot status="ok">{up} up</StatusDot>
            {down > 0 && <StatusDot status="danger">{down} down</StatusDot>}
            <Link to="/activity#services" style={{ color: "var(--accent)" }}>details ↗</Link>
          </span>
        )}
      </Row>
      <Row label="URLs">
        <span className="ws-muted ws-mono" style={{ fontSize: "var(--fs-xs)", display: "block" }}>
          :8443 workshop · :8444 commute · :8445 wearables
        </span>
      </Row>
      <Row label="events">
        <Link to="/activity#events" style={{ color: "var(--accent)" }}>recent event feed ↗</Link>
      </Row>
    </Section>
  );
}
