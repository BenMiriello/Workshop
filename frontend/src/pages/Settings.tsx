import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  Bot,
  Server,
  Palette,
  Sun,
  Moon,
  Monitor,
  Trash2,
  UserRound,
  LogOut,
  Smartphone,
  MessageSquare,
  Pencil,
  Library,
} from "lucide-react";
import {
  ActionBar,
  Button,
  ConfirmDialog,
  PageHeader,
  ReadOnly,
  Segmented,
  Select,
  StatusDot,
  useEditMode,
  useTheme,
  usePalette,
  useResolvedMode,
  PalettePicker,
  PALETTE_META,
  StatusPill,
  Input,
  Skeleton,
  StateBlock,
  Notice,
  type SegmentedOption,
  type ThemeMode,
} from "@workshop/ui";
import { chatApi, type ChatPrefs } from "../lib/chat";
import { CHAT_MODES, chatModeDef, type ChatModeId } from "../components/chat/chatModes";
import { effortLabel } from "../components/chat/effort";
import { permissionModeDef } from "../components/chat/permissionModes";
import { useAuth } from "../auth/AuthProvider";
import Services from "./Services";
import Events from "./Events";
import { SettingsSection as Section, SettingsRow as Row } from "../components/AppSettings";
import {
  getPolicy,
  setDefaults,
  SHARE_VALUES,
  SHARE_LABEL,
  SHARE_HELP,
  type MemoryPolicy,
  type ShareValue,
} from "../lib/memoryPolicy";
import AutonomyControls from "../components/AutonomyControls";
import { native } from "../native/bridge";
import { useNative, useTrackingStatus } from "../native/useNative";
import { startDeviceTracking, stopDeviceTracking } from "../native/location";
import type { PermissionState } from "@shared/native-bridge";
import { AppSuspense, appSettingsPanels } from "../apps/registry";
import { timeAgo } from "../lib/time";

// Settings. One section per tab, ordered by how often the operator touches it.
//
// The four "pointer" sections that used to live here (Mcp, BaseApps, Trackers,
// Platform) are gone: each was ~15 lines of Card chrome wrapping a single link
// to a page that already existed. In a tabbed Settings that link IS the tab, so
// the wrapper had nothing left to do.

type ServiceProbe = {
  name: string;
  category?: string;
  status: string;
  http_status?: number | null;
  latency_ms?: number | null;
  notes?: string | null;
};

/** Which tab of Settings is on screen. One per route; see App.tsx. */
export type SettingsTab =
  | "account"
  | "appearance"
  | "chat"
  | "memory"
  | "apps"
  | "host";

const TAB_TITLE: Record<SettingsTab, string> = {
  account: "Account",
  appearance: "Appearance",
  chat: "Chat",
  memory: "Memory",
  apps: "Apps",
  host: "This host",
};

/**
 * Settings is a PLACE with one page per section, not one long page.
 *
 * Operator, 2026-08-20: "clicking settings just goes to the workshop home page
 * and then settings, which is actually not correct. Settings should be its own
 * page, and the secondary toolbar should have all the tabs for all the
 * different sections. So instead of settings all being one big blob of things,
 * all of these things should be separate tabs."
 *
 * It was thirteen sections stacked vertically, and rendering them all mounted
 * AutonomyControls, Services and Events together: five polling loops (services
 * every 10s from two different callers, events 15s, autonomy 15s x2, health
 * 60s) plus about nine one-shot fetches, on a screen most visits use to change
 * the theme. One tab at a time fixes that as a side effect.
 *
 * The sections themselves did not move. Each tab renders the same components
 * this file already had, so this is a routing change, not a rewrite of any
 * control.
 */
export default function Settings({ tab = "account" }: { tab?: SettingsTab }) {
  return (
    <div>
      <PageHeader title={TAB_TITLE[tab]} />
      {tab === "account" && (
        <>
          <AccountSection />
          {/* Native-shell only; renders nothing in a browser. It belongs to
              this signed-in session's device, so it sits under Account. */}
          <DeviceSection />
        </>
      )}
      {tab === "appearance" && <AppearanceSection />}
      {tab === "chat" && <ChatSection />}
      {tab === "memory" && <MemorySection />}
      {/* Panels contributed by installed apps. Settings does not know which
          apps exist or what they need configured. */}
      {tab === "apps" && <AppSuspense>{appSettingsPanels()}</AppSuspense>}
      {tab === "host" && (
        <>
          {/* EVERYTHING HERE IS ABOUT THIS MACHINE, and that is why it is one
              tab with that name rather than scattered through Settings.
              Operator, 2026-08-20, on the GPU and broker rows: "the broker is a
              specific thing to my particular distribution and should not be
              included in this at all", and on the system section: "that might
              not be correct at all or relevant to a production system, like a
              cloud hosted version of this".
              He is right: gpubrokerd is an external daemon at 127.0.0.1:8447
              that Workshop is only a client of, and it cannot exist in a cloud
              deploy. Quarantining it under an honest name is the step that can
              be taken without breaking controls he uses today; deciding what is
              deleted versus gated is the settings-redesign phase, recorded in
              docs/platform-redesign/app-separation-and-github-2026-08-20.md
              section 5.1. */}
          <AgentsSection />
          <AutonomyControls />
          <SystemSection />
        </>
      )}
    </div>
  );
}

// ----------------------------------------------------------------------------
// Memory section: what one chat's search turns up from another's work
// (memory-and-documentation-model-2026-08-14.md sec 6).
//
// This is CURATION, NOT SECURITY, and the copy has to keep saying so. Everything
// on this box runs as one uid, so none of this stops anything reading anything;
// it decides what a SEARCH surfaces, so a chat's recall stays about that chat's
// own work. Per SEC-A2 this repo already has one open ticket about having
// described scope as a boundary it was not. Do not reintroduce the words
// permission, access, allowed or blocked here.
//
// Chats default to "Only this scope" on the operator's 2026-08-14 direction:
// transcripts are the biggest and least deliberate corpus, so they stay out of
// other chats' recall unless he says otherwise. Per-item overrides live on the
// Memory page.
// ----------------------------------------------------------------------------
type ShareDraft = { note: ShareValue; doc: ShareValue; chat: ShareValue };

function MemorySection() {
  const [policy, setPolicy] = useState<MemoryPolicy | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [saveErr, setSaveErr] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    void getPolicy()
      .then((p) => { if (live) setPolicy(p); })
      .catch((e) => { if (live) setErr(String(e)); });
    return () => { live = false; };
  }, []);

  const current: ShareDraft = {
    note: policy?.defaults.note ?? "inherit",
    doc: policy?.defaults.doc ?? "inherit",
    chat: policy?.defaults.chat ?? "private",
  };

  const edit = useEditMode<ShareDraft>(current, async (draft) => {
    setSaveErr(null);
    try {
      setPolicy(await setDefaults({ note: draft.note, doc: draft.doc, chat: draft.chat }));
    } catch (e) {
      setSaveErr(String(e));
      throw e;
    }
  });

  if (err) {
    return (
      <Section icon={Library} title="Memory">
        <StateBlock
          variant="error"
          title="Couldn't load memory settings"
          hint={`GET /api/memory-policy failed: ${err}`}
        />
      </Section>
    );
  }
  if (!policy) {
    return (
      <Section icon={Library} title="Memory">
        <Row label="notes"><Skeleton width="12rem" style={{ height: "var(--control-h)" }} /></Row>
        <Row label="documents"><Skeleton width="12rem" style={{ height: "var(--control-h)" }} /></Row>
        <Row label="chat transcripts"><Skeleton width="12rem" style={{ height: "var(--control-h)" }} /></Row>
      </Section>
    );
  }

  const kinds: Array<{ key: keyof ShareDraft; label: string; hint: string }> = [
    { key: "note", label: "notes", hint: "Notes are written to be read, so they follow the tree by default." },
    { key: "doc", label: "documents", hint: "Documents sit in no scope, so this only matters if you narrow them." },
    {
      key: "chat",
      label: "chat transcripts",
      hint: "Transcripts are the biggest and least finished writing here, so they stay out of other chats' searches by default. The notes a chat files are the intended path. Override a single chat on the Memory page.",
    },
  ];

  return (
    <Section icon={Library} title="Memory">
      <p className="ws-muted" style={{ fontSize: "var(--fs-sm)", margin: 0 }}>
        What one chat finds when it searches another's work. This shapes what
        search turns up, to keep a chat's recall about its own work. It is not a
        security boundary and does not stop anything from reading anything.
      </p>

      {kinds.map(({ key, label, hint }) => (
        <Row key={key} label={label} hint={hint}>
          {edit.editing ? (
            <Select
              value={edit.draft[key]}
              onChange={(e) => edit.set(key, e.target.value as ShareValue)}
              aria-label={`Default for ${label}`}
            >
              {SHARE_VALUES.map((v) => (
                <option key={v} value={v}>{SHARE_LABEL[v]}</option>
              ))}
            </Select>
          ) : (
            <ReadOnly value={SHARE_LABEL[current[key]]} />
          )}
        </Row>
      ))}

      <Row label="right now" hint={SHARE_HELP[edit.editing ? edit.draft.chat : current.chat]}>
        <ReadOnly value={`Chats: ${SHARE_LABEL[edit.editing ? edit.draft.chat : current.chat]}`} />
      </Row>

      {saveErr && (
        <Notice tone="error" detail={saveErr} where="PUT /api/memory-policy/defaults">
          Couldn't save memory settings.
        </Notice>
      )}

      {edit.editing ? (
        <ActionBar flash={edit.flash ? "Saved" : undefined}>
          <Button variant="ghost" onClick={edit.cancel} disabled={edit.saving}>Cancel</Button>
          <Button
            variant="primary"
            onClick={() => { void edit.save().catch(() => {}); }}
            loading={edit.saving}
            disabled={!edit.dirty}
          >
            Save
          </Button>
        </ActionBar>
      ) : (
        <Button variant="ghost" onClick={edit.begin}><Pencil size={16} strokeWidth={1.8} aria-hidden /> Edit</Button>
      )}
    </Section>
  );
}

// ----------------------------------------------------------------------------
// MCP section: pointer into Settings -> MCP. A pointer rather than a panel
// because the answer needs two CLI probes and a few seconds, and the Settings
// index should not wait on them to render. The reason it exists at all: on
// 2026-08-10 three of this host's MCP servers were failing to connect, two more
// were unauthenticated, and no screen in the app said so.
// ----------------------------------------------------------------------------

// ----------------------------------------------------------------------------
// Base Apps section — pointer into Settings → Base Apps subroute. Holds the
// tenant-level git-repo listing previously at /projects. See ADR-008.
// ----------------------------------------------------------------------------

// ----------------------------------------------------------------------------
// Trackers section: pointer into Settings -> Trackers. ONE external ticket
// integration for the system, with per-scope targets inside it. Everything that
// looked like a setting collapsed into a decision already made, so this is a
// pointer and not a panel.
// ----------------------------------------------------------------------------

// ----------------------------------------------------------------------------
// Platform section: pointer into Settings -> Grants. Cross-app + guest access
// control (capability grants). Operator is always fully permitted; these govern
// shared/guest access only.
// ----------------------------------------------------------------------------

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

const DEVICES_PAGE_SIZE = 10;

function DeviceTokens() {
  const [devices, setDevices] = useState<DeviceToken[] | null>(null);
  const [total, setTotal] = useState(0);
  const [revoking, setRevoking] = useState<DeviceToken | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async (limit: number) => {
    try {
      const r = await fetch(`/api/auth/devices?limit=${limit}&offset=0`);
      const j = r.ok ? await r.json() : { devices: [], total: 0 };
      setDevices(Array.isArray(j.devices) ? j.devices : []);
      setTotal(typeof j.total === "number" ? j.total : 0);
    } catch {
      setDevices([]);
      setTotal(0);
    }
  }, []);

  useEffect(() => { load(DEVICES_PAGE_SIZE); }, [load]);

  // No-cruft: render nothing until at least one device exists.
  if (!devices || devices.length === 0) return null;

  const onRevoke = async () => {
    if (!revoking) return;
    setBusy(true);
    try {
      await fetch(`/api/auth/devices/${revoking.id}`, { method: "DELETE" });
      await load(Math.max(DEVICES_PAGE_SIZE, devices.length));
    } finally {
      setBusy(false);
      setRevoking(null);
    }
  };

  return (
    <Row label={`devices${total > devices.length ? ` (${devices.length} of ${total})` : ""}`}>
      <span style={{ display: "flex", flexDirection: "column", gap: "var(--space-2)" }}>
        {devices.map((d) => (
          <span key={d.id} style={{ display: "inline-flex", alignItems: "center", gap: "var(--space-2)", flexWrap: "wrap" }}>
            <Smartphone size={14} strokeWidth={1.8} aria-hidden />
            <span>{d.label || "device"}</span>
            <span className="ws-muted ws-mono" style={{ fontSize: "var(--fs-xs)" }}>
              {d.id}{d.last_seen ? ` · seen ${timeAgo(d.last_seen)}` : ""}
            </span>
            <Button size="sm" variant="danger-ghost" onClick={() => setRevoking(d)}>
              <Trash2 size={14} strokeWidth={1.8} aria-hidden /> Revoke
            </Button>
          </span>
        ))}
        {total > devices.length && (
          <Button size="sm" variant="ghost" onClick={() => load(devices.length + DEVICES_PAGE_SIZE)}>
            Show more ({total - devices.length} more)
          </Button>
        )}
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
// (eight schemes in two groups, default iris). Both commit ON CHANGE — view
// preferences. Picking a scheme picks the three AGENCY tones together (you /
// system / autonomous); that relationship is the identity, not one accent.
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
// Chat — the operator's three chat defaults.
//
// `new chats` is baked into a chat at creation, so changing it never reroutes
// work already in flight. `effort` and `asks before acting` are different: they
// are consulted per turn, BELOW whatever the thread itself stored, so changing
// one moves every chat that never picked for itself and no chat that did.
//
// All three commit on explicit Save. They write config (design system rule 7),
// and a mis-click on the mode picker would put the next chat's work somewhere
// the operator did not intend.
// ----------------------------------------------------------------------------
const CHAT_MODE_OPTIONS: SegmentedOption<ChatModeId>[] = CHAT_MODES.map(({ id, label, Icon }) => ({
  value: id,
  label: <><Icon size={15} strokeWidth={1.8} aria-hidden /> {label}</>,
}));

// "" is a real, chosen value here: no preference. It is what clears a default
// back to the model's own (effort) or the platform's (permission mode).
const NO_PREFERENCE = "";

type ChatPrefsDraft = {
  default_mode: ChatModeId;
  default_effort: string;
  default_permission_mode: string;
};

function ChatSection() {
  const [prefs, setPrefs] = useState<ChatPrefs | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [saveErr, setSaveErr] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    void chatApi.getChatPrefs()
      .then((p) => { if (live) setPrefs(p); })
      .catch((e) => { if (live) setErr(String(e)); });
    return () => { live = false; };
  }, []);

  const current: ChatPrefsDraft = {
    default_mode: (prefs?.default_mode ?? "direct") as ChatModeId,
    default_effort: prefs?.default_effort ?? NO_PREFERENCE,
    default_permission_mode: prefs?.default_permission_mode ?? NO_PREFERENCE,
  };

  const edit = useEditMode<ChatPrefsDraft>(current, async (draft) => {
    setSaveErr(null);
    try {
      setPrefs(await chatApi.setChatPrefs(draft));
    } catch (e) {
      // Keep the stored values on screen; the draft is what failed to store.
      setSaveErr(String(e));
      throw e;
    }
  });

  if (err) {
    return (
      <Section icon={MessageSquare} title="Chat">
        <StateBlock
          variant="error"
          title="Couldn't load chat settings"
          hint={`GET /api/chat/prefs failed: ${err}`}
        />
      </Section>
    );
  }
  if (!prefs) {
    return (
      <Section icon={MessageSquare} title="Chat">
        <Row label="new chats"><Skeleton width="12rem" style={{ height: "var(--control-h)" }} /></Row>
        <Row label="effort"><Skeleton width="12rem" style={{ height: "var(--control-h)" }} /></Row>
        <Row label="asks before acting"><Skeleton width="12rem" style={{ height: "var(--control-h)" }} /></Row>
      </Section>
    );
  }

  const platformMode = permissionModeDef(prefs.platform_permission_mode).label;
  // What the hints describe: the draft while editing, the stored values while
  // not. A hint that keeps describing the old value during an edit is a caption
  // for a control that has already moved.
  const shown = edit.editing ? edit.draft : current;

  return (
    <Section icon={MessageSquare} title="Chat">
      <Row
        label="new chats"
        // The hint follows what is SELECTED, so it explains the choice being
        // made rather than the one being replaced.
        hint={`${chatModeDef(shown.default_mode).title} Existing chats keep the mode they were started in.`}
      >
        {edit.editing ? (
          <Segmented
            options={CHAT_MODE_OPTIONS}
            value={edit.draft.default_mode}
            onChange={(v) => edit.set("default_mode", v)}
            ariaLabel="Mode for new chats"
          />
        ) : (
          <ReadOnly value={chatModeDef(current.default_mode).label} />
        )}
      </Row>

      <Row
        label="effort"
        hint="How hard the model works on a turn, for any chat that hasn't picked its own. A level the chosen model doesn't offer falls back to that model's default."
      >
        {edit.editing ? (
          <Select
            value={edit.draft.default_effort}
            onChange={(e) => edit.set("default_effort", e.target.value)}
            aria-label="Default effort"
          >
            <option value={NO_PREFERENCE}>Model default</option>
            {prefs.efforts.map((level) => (
              <option key={level} value={level}>{effortLabel(level)}</option>
            ))}
          </Select>
        ) : (
          <ReadOnly
            value={current.default_effort ? effortLabel(current.default_effort) : "Model default"}
          />
        )}
      </Row>

      <Row
        label="asks before acting"
        hint={
          shown.default_permission_mode
            ? permissionModeDef(shown.default_permission_mode).hint
            : `Chats that haven't picked run at ${platformMode}: ` +
              `${permissionModeDef(prefs.platform_permission_mode).hint}`
        }
      >
        {edit.editing ? (
          <Select
            value={edit.draft.default_permission_mode}
            onChange={(e) => edit.set("default_permission_mode", e.target.value)}
            aria-label="Default permission mode"
          >
            <option value={NO_PREFERENCE}>{`System default (${platformMode})`}</option>
            {prefs.permission_modes.map((id) => (
              <option key={id} value={id}>{permissionModeDef(id).label}</option>
            ))}
          </Select>
        ) : (
          <ReadOnly
            value={
              current.default_permission_mode
                ? permissionModeDef(current.default_permission_mode).label
                : `System default (${platformMode})`
            }
          />
        )}
      </Row>

      {/* A line, not a region: the settings it failed to save are still on
          screen right above it. */}
      {saveErr && (
        <Notice tone="error" detail={saveErr} where="POST /api/chat/prefs">
          Couldn't save chat settings.
        </Notice>
      )}

      {/* Direct children of the card, not a Row: the action bar is sticky and
          full width, and a label-beside-value grid cell is neither. */}
      {edit.editing ? (
        <ActionBar flash={edit.flash ? "Saved" : undefined}>
          <Button variant="ghost" onClick={edit.cancel} disabled={edit.saving}>Cancel</Button>
          <Button
            variant="primary"
            // The rejection is already surfaced as `saveErr`; swallowing it here
            // keeps a failed save from becoming an unhandled rejection.
            onClick={() => { void edit.save().catch(() => {}); }}
            loading={edit.saving}
            disabled={!edit.dirty}
          >
            Save
          </Button>
        </ActionBar>
      ) : (
        <div style={{ paddingTop: "var(--space-3)" }}>
          <Button size="sm" variant="ghost" onClick={edit.begin}>
            <Pencil aria-hidden /> Edit
          </Button>
        </div>
      )}
    </Section>
  );
}

// ----------------------------------------------------------------------------
// Agents section — fleet overview, defaults
// ----------------------------------------------------------------------------
type Pool = { limit: number; active: number; waiting: number };
type Concurrency = { turns: Pool; helpers: Pool };
type ConcurrencyDraft = { max_turns: number; max_helpers: number };

function AgentsSection() {
  const [agents, setAgents] = useState<unknown[] | null>(null);
  const [conc, setConc] = useState<Concurrency | null>(null);
  const [concErr, setConcErr] = useState<string | null>(null);
  const [saveErr, setSaveErr] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/agents/").then((r) => r.ok ? r.json() : []).then((d) => setAgents(Array.isArray(d) ? d : []));
  }, []);

  const loadConc = useCallback(() => {
    setConcErr(null);
    fetch("/api/concurrency")
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then(setConc)
      .catch((e) => setConcErr(String(e)));
  }, []);
  useEffect(() => { loadConc(); }, [loadConc]);

  const edit = useEditMode<ConcurrencyDraft>(
    { max_turns: conc?.turns.limit ?? 8, max_helpers: conc?.helpers.limit ?? 4 },
    async (draft) => {
      setSaveErr(null);
      const r = await fetch("/api/concurrency", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(draft),
      });
      if (!r.ok) {
        const detail = `PUT /api/concurrency failed: HTTP ${r.status}`;
        setSaveErr(detail);
        throw new Error(detail);
      }
      setConc(await r.json());
    },
  );

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
      {/* How many model processes may run at once. Each one is a whole OS
          process, so this is the only thing standing between a runaway fan-out
          and the host's memory. */}
      <Row label="agent runs at once">
        {concErr ? (
          <Notice
            tone="error"
            detail={concErr}
            where="GET /api/concurrency"
            action={<Button size="sm" variant="ghost" onClick={loadConc}>Retry</Button>}
          >
            Couldn't read the concurrency limits.
          </Notice>
        ) : conc == null ? (
          <Skeleton />
        ) : edit.editing ? (
          <Input
            type="number"
            min={1}
            max={64}
            value={String(edit.draft.max_turns)}
            onChange={(e) => edit.set("max_turns", Number(e.currentTarget.value))}
          />
        ) : (
          <ReadOnly value={`${conc.turns.limit} concurrent, ${conc.turns.active} running${
            conc.turns.waiting > 0 ? `, ${conc.turns.waiting} waiting for a slot` : ""}`} />
        )}
      </Row>
      <Row label="background calls at once">
        {conc != null && !concErr && (edit.editing ? (
          <Input
            type="number"
            min={1}
            max={64}
            value={String(edit.draft.max_helpers)}
            onChange={(e) => edit.set("max_helpers", Number(e.currentTarget.value))}
          />
        ) : (
          <ReadOnly value={`${conc.helpers.limit} concurrent, ${conc.helpers.active} running${
            conc.helpers.waiting > 0 ? `, ${conc.helpers.waiting} waiting for a slot` : ""}`} />
        ))}
      </Row>
      <Row label="">
        <span className="muted" style={{ fontSize: "0.85rem" }}>
          Short background calls (naming a chat, triage, compaction) have their own
          pool, so a long agent run never holds them up. Over the limit, work waits
          for a slot. Nothing running is ever stopped.
        </span>
      </Row>
      {saveErr && (
        <Notice tone="error" detail={saveErr}>
          Couldn't save the concurrency limits.
        </Notice>
      )}
      {conc != null && !concErr && (edit.editing ? (
        <ActionBar flash={edit.flash ? "Saved" : undefined}>
          <Button variant="ghost" onClick={edit.cancel} disabled={edit.saving}>Cancel</Button>
          <Button
            variant="primary"
            onClick={() => { void edit.save().catch(() => {}); }}
            loading={edit.saving}
            disabled={!edit.dirty}
          >
            Save
          </Button>
        </ActionBar>
      ) : (
        <div style={{ paddingTop: "var(--space-3)" }}>
          <Button size="sm" variant="ghost" onClick={edit.begin}>
            <Pencil aria-hidden /> Edit
          </Button>
        </div>
      ))}
      <Row label="new agent">
        <Link to="/agents/new">
          <Button size="sm" variant="ghost">New agent ↗</Button>
        </Link>
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
