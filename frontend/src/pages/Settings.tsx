import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  Bot,
  Server,
  Heart,
  Eye,
  EyeOff,
  Copy,
  Check,
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
  MessageSquare,
  Link2,
} from "lucide-react";
import {
  Button,
  ConfirmDialog,
  PageHeader,
  Segmented,
  StatusDot,
  useTheme,
  usePalette,
  useResolvedMode,
  PalettePicker,
  PALETTE_META,
  StatusPill,
  Input,
  Skeleton,
  StateBlock,
  type SegmentedOption,
  type ThemeMode,
} from "../ui";
import { chatApi } from "../lib/chat";
import { CHAT_MODES, chatModeDef, type ChatModeId } from "../components/chat/chatModes";
import { useAuth } from "../auth/AuthProvider";
import Services from "./Services";
import Events from "./Events";
import { SettingsSection as Section, SettingsRow as Row } from "../components/AppSettings";
import AutonomyControls from "../components/AutonomyControls";
import { native } from "../native/bridge";
import { useNative, useTrackingStatus } from "../native/useNative";
import { startDeviceTracking, stopDeviceTracking } from "../native/location";
import type { PermissionState } from "@shared/native-bridge";

// Settings page. Logical groupings, no over-engineering. Sections in order
// of how often the operator actually touches them.

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
    <div>
      <PageHeader title="Settings" />
      <AccountSection />
      <DeviceSection />
      <AppearanceSection />
      <ChatSection />
      <BaseAppsSection />
      <PlatformSection />
      <TrackersSection />
      <HealthSection />
      <AgentsSection />
      <AutonomyControls />
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
// Trackers section: pointer into Settings -> Trackers. ONE external ticket
// integration for the system, with per-scope targets inside it. Everything that
// looked like a setting collapsed into a decision already made, so this is a
// pointer and not a panel.
// ----------------------------------------------------------------------------
function TrackersSection() {
  return (
    <Section icon={Link2} title="Trackers">
      <Row label="integration">
        <Link to="/settings/trackers" style={{ color: "var(--accent)" }}>
          external tickets + triage ↗
        </Link>
        <span className="ws-muted" style={{ marginLeft: "0.5rem", fontSize: "var(--fs-xs)" }}>
          (one at a time; imported issues need approving)
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
      <DeviceTokens />
    </Section>
  );
}

// Active native device tokens for this account (minted inside the iOS shell so
// background producers can authenticate outside the WebView cookie). Lets the
// operator revoke a lost or stolen device's credential. Self-hides when the
// account has none — the common case before the iOS build — so it adds no noise.
// Backend: GET/DELETE /auth/devices (backend/routes/auth.py).
type DeviceToken = { id: string; label?: string | null; created_at?: string; last_seen?: string | null };

function DeviceTokens() {
  const [devices, setDevices] = useState<DeviceToken[] | null>(null);
  const [revoking, setRevoking] = useState<DeviceToken | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const r = await fetch("/api/auth/devices");
      const j = r.ok ? await r.json() : { devices: [] };
      setDevices(Array.isArray(j.devices) ? j.devices : []);
    } catch {
      setDevices([]);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  // No-cruft: render nothing until at least one device exists.
  if (!devices || devices.length === 0) return null;

  const onRevoke = async () => {
    if (!revoking) return;
    setBusy(true);
    try {
      await fetch(`/api/auth/devices/${revoking.id}`, { method: "DELETE" });
      await load();
    } finally {
      setBusy(false);
      setRevoking(null);
    }
  };

  return (
    <Row label="devices">
      <span style={{ display: "flex", flexDirection: "column", gap: "var(--space-2)" }}>
        {devices.map((d) => (
          <span key={d.id} style={{ display: "inline-flex", alignItems: "center", gap: "var(--space-2)", flexWrap: "wrap" }}>
            <Smartphone size={14} strokeWidth={1.8} aria-hidden />
            <span>{d.label || "device"}</span>
            <span className="ws-muted ws-mono" style={{ fontSize: "var(--fs-xs)" }}>
              {d.id}{d.last_seen ? ` · seen ${fmtAge(d.last_seen)}` : ""}
            </span>
            <Button size="sm" variant="danger-ghost" onClick={() => setRevoking(d)}>
              <Trash2 size={14} strokeWidth={1.8} aria-hidden /> Revoke
            </Button>
          </span>
        ))}
      </span>
      <ConfirmDialog
        open={!!revoking}
        onCancel={() => setRevoking(null)}
        onConfirm={onRevoke}
        title="Revoke this device?"
        body={`The background tasks on "${revoking?.label || "device"}" stop authenticating until it signs in again. This can't be undone.`}
        confirmLabel="Revoke device"
        busy={busy}
      />
    </Row>
  );
}

// ----------------------------------------------------------------------------
// Appearance — the only writer of localStorage["ws-theme"] and ["ws-palette"].
// Two independent axes: mode (light/dark/system, default dark) and color scheme
// (five palettes, default violet). Both commit ON CHANGE — view preferences.
// ----------------------------------------------------------------------------
const THEME_OPTIONS: SegmentedOption<ThemeMode>[] = [
  { value: "light", label: <><Sun size={15} strokeWidth={1.8} aria-hidden /> Light</> },
  { value: "dark", label: <><Moon size={15} strokeWidth={1.8} aria-hidden /> Dark</> },
  { value: "system", label: <><Monitor size={15} strokeWidth={1.8} aria-hidden /> System</> },
];

function AppearanceSection() {
  const [theme, setTheme] = useTheme();
  const [palette, setPalette] = usePalette();
  const mode = useResolvedMode(theme);
  const current = PALETTE_META.find((p) => p.id === palette);
  return (
    <Section icon={Palette} title="Appearance">
      <Row label="theme">
        <Segmented options={THEME_OPTIONS} value={theme} onChange={setTheme} ariaLabel="Theme" />
      </Row>
      <Row label="color scheme" hint={current?.blurb}>
        <PalettePicker value={palette} onChange={setPalette} mode={mode} />
      </Row>
    </Section>
  );
}

// ----------------------------------------------------------------------------
// Chat — the mode a NEW chat starts in.
//
// Commits ON CHANGE: it is a preference about future chats, not a write to any
// conversation. Existing chats keep the mode they were created with, so this can
// never reroute work already in flight; the per-chat control in the chat toolbar
// is what changes a conversation you are in.
// ----------------------------------------------------------------------------
const CHAT_MODE_OPTIONS: SegmentedOption<ChatModeId>[] = CHAT_MODES.map(({ id, label, Icon }) => ({
  value: id,
  label: <><Icon size={15} strokeWidth={1.8} aria-hidden /> {label}</>,
}));

function ChatSection() {
  const [mode, setMode] = useState<ChatModeId | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    void chatApi.getChatPrefs()
      .then((p) => { if (live) setMode(p.default_mode); })
      .catch((e) => { if (live) setErr(String(e)); });
    return () => { live = false; };
  }, []);

  const choose = (next: ChatModeId) => {
    const prev = mode;
    setMode(next);
    setErr(null);
    void chatApi.setChatPrefs(next).catch((e) => {
      // Show the stored value, not the one that failed to store.
      setMode(prev);
      setErr(String(e));
    });
  };

  return (
    <Section icon={MessageSquare} title="Chat">
      <Row label="new chats">
        {err ? (
          <StateBlock
            variant="error"
            title="Couldn't load chat settings"
            hint={`GET /api/chat/prefs failed: ${err}`}
          />
        ) : mode === null ? (
          <Skeleton width="12rem" style={{ height: "var(--control-h)" }} />
        ) : (
          <Segmented
            options={CHAT_MODE_OPTIONS}
            value={mode}
            onChange={choose}
            ariaLabel="Mode for new chats"
          />
        )}
      </Row>
      <Row label="">
        <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>
          {chatModeDef(mode ?? undefined).title} Existing chats keep the mode they were started in.
        </span>
      </Row>
    </Section>
  );
}

// ----------------------------------------------------------------------------
// Health section — Apple Health data via Health Auto Export iOS app
// ----------------------------------------------------------------------------
function HealthSection() {
  const [metrics, setMetrics] = useState<Record<string, { ts?: string; date?: string; qty?: unknown; units?: string }> | null>(null);
  const [confirmClear, setConfirmClear] = useState(false);
  const [clearing, setClearing] = useState(false);
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
    setClearing(true);
    try {
      await fetch("/api/health/history", { method: "DELETE" });
      await load();
    } finally {
      setClearing(false);
      setConfirmClear(false);
    }
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
          <StatusDot status="danger">no data, install Health Auto Export</StatusDot>
        ) : stale ? (
          <StatusDot status="warn">stale ({fmtAge(latestTs)}), check Health Auto Export</StatusDot>
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
            {String(v.qty ?? "–")}{v.units ? ` ${v.units}` : ""}
            {v.date && <span className="muted" style={{ marginLeft: "0.5rem", fontSize: "0.78rem" }}>{v.date}</span>}
          </span>
        </Row>
      ))}
      <Row label="history">
        <button onClick={() => setConfirmClear(true)} style={{ padding: "0.2rem 0.6rem", fontSize: "0.8rem", color: "var(--err)" }}>
          clear all data
        </button>
      </Row>
      <ConfirmDialog
        open={confirmClear}
        onCancel={() => setConfirmClear(false)}
        onConfirm={onClear}
        title="Erase all stored Apple Health data?"
        body="Deletes every Apple Health metric Workshop has stored. This can't be undone, and the data only comes back if Health Auto Export pushes it again."
        confirmLabel="Erase health data"
        busy={clearing}
      />
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
            from Workshop's saved credentials
          </span>
        )}
      </Row>
    </>
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
        <Link to="/agents/new">
          <Button size="sm" variant="ghost">New agent ↗</Button>
        </Link>
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
    <>
      <Section icon={Server} title="System">
        <Row label="backend">{health?.ok ? <StatusDot status="ok">healthy</StatusDot> : <StatusDot status="danger">down</StatusDot>}</Row>
        <Row label="services">
          {services == null ? "probing…" : (
            <span style={{ display: "inline-flex", alignItems: "center", gap: "var(--space-3)", flexWrap: "wrap" }}>
              <StatusDot status="ok">{up} up</StatusDot>
              {down > 0 && <StatusDot status="danger">{down} down</StatusDot>}
            </span>
          )}
        </Row>
        <Row label="URLs">
          <span className="ws-muted ws-mono" style={{ fontSize: "var(--fs-xs)", display: "block" }}>
            :8443 workshop · :8444 commute · :8445 wearables
          </span>
        </Row>
      </Section>

      {/* Telemetry, re-homed from the retired Activity page: the full Services
          probe grid and the recent Event feed live under System now. */}
      <section style={{ marginBottom: "var(--space-6)" }}>
        <Services />
      </section>
      <section style={{ marginBottom: "var(--space-6)" }}>
        <Events />
      </section>
    </>
  );
}
