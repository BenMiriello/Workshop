import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  Bot,
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
  Clock,
} from "lucide-react";
import { ActionBar, Button, ConfirmDialog, PageHeader, ReadOnly, Segmented, Select, StatusDot, useEditMode, useTheme, usePalette, useResolvedMode, PalettePicker, StatusPill, Input, Skeleton, StateBlock, Notice, type SegmentedOption, type ThemeMode, SettingsSection as Section, SettingsRow as Row, timeAgo, native, startDeviceTracking, stopDeviceTracking, useNative, useTrackingStatus } from "@workshop/ui";
import { chatApi, type ChatPrefs } from "../lib/chat";
import { CHAT_MODES, chatModeDef, type ChatModeId } from "../components/chat/chatModes";
import { effortLabel } from "../components/chat/effort";
import { permissionModeDef } from "../components/chat/permissionModes";
import { useAuth } from "../auth/AuthProvider";

import {
  getPolicy,
  setDefaults,
  SHARE_VALUES,
  SHARE_LABEL,
  type MemoryPolicy,
  type ShareValue,
} from "../lib/memoryPolicy";
import AutonomyControls from "../components/AutonomyControls";
import InstalledApps from "./settings/InstalledApps";
import GitHubAccount from "./settings/GitHubAccount";
import Trackers from "./settings/Trackers";
import CustomInstructions from "./settings/CustomInstructions";

import type { PermissionState } from "@shared/native-bridge";

// Settings. One section per tab, ordered by how often the operator touches it.
//
// The four "pointer" sections that used to live here (Mcp, BaseApps, Trackers,
// Platform) are gone: each was ~15 lines of Card chrome wrapping a single link
// to a page that already existed. In a tabbed Settings that link IS the tab, so
// the wrapper had nothing left to do.

/** Which tab of Settings is on screen. One per route; see App.tsx. */
export type SettingsTab =
  | "account"
  | "appearance"
  | "chat"
  | "memory"
  | "connectors"
  | "apps"
  | "agents";

const TAB_TITLE: Record<SettingsTab, string> = {
  account: "Account",
  appearance: "Appearance",
  chat: "Chat",
  memory: "Memory",
  connectors: "Connectors",
  apps: "Apps",
  agents: "Agents",
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
 * AutonomyControls and Services together: several polling loops (services every
 * 10s from two different callers, autonomy 15s x2, health 60s) plus about nine
 * one-shot fetches, on a screen most visits use to change the theme. One tab at a time fixes that as a side effect.
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
          {/* WHICH CLOCK this account's times are read in. It sits under
              Account rather than Appearance because it is not a presentation
              preference: it is a fact about the person, and every agent turn is
              told it so an event can be reported in their hours. */}
          <TimezoneSection />
          {/* Native-shell only; renders nothing in a browser. It belongs to
              this signed-in session's device, so it sits under Account. */}
          <DeviceSection />
        </>
      )}
      {tab === "appearance" && <AppearanceSection />}
      {/* INSTRUCTIONS FOLD IN HERE (threads-branches-and-pages-2026-08-29
          section 6, "Settings: 12 rows to 9"). Custom instructions are how the
          assistant behaves in a chat, which is the question this row already
          answers; they had a row of their own because they arrived on their
          own. /settings/instructions still resolves, as a redirect. */}
      {tab === "chat" && (
        <>
          <ChatSection />
          <CustomInstructions embedded />
        </>
      )}
      {tab === "memory" && <MemorySection />}
      {/* Panels contributed by installed apps. Settings does not know which
          apps exist or what they need configured.

          TRACKERS FOLDS IN HERE (threads-branches-and-pages-2026-08-29 section
          6). An issue tracker is a thing this installation is connected to,
          which is the question this page already answers; it had a row of its
          own because it arrived on its own, not because it is a different
          kind of thing. */}
      {/* CONNECTORS: the accounts and services this installation is signed in
          to, as opposed to the apps installed in it.

          GitHub and Trackers used to live under Apps, each folded in with its
          own reasoning: the tracker because "an issue tracker is a thing this
          installation is connected to", the GitHub account because it "belongs
          beside the tracker that spends the same kind of credential". Both
          arguments were right about what those two ARE and wrong about the tab
          they were on, because the answer they kept reaching for -- a place for
          things this installation is connected to -- did not exist yet. It does
          now, and Apps goes back to being the installed-app catalog and nothing
          else.

          GitHub stays ABOVE the tracker, for the reason it was given: a tracker
          connection is one use of a GitHub account and this is the account. */}
      {tab === "connectors" && (
        <>
          <GitHubAccount />
          <Trackers embedded />
        </>
      )}
      {tab === "apps" && <InstalledApps />}
      {tab === "agents" && (
        <>
          {/* How the agent fleet runs: concurrency limits, the autonomy brake,
              and the queue runner. The GPU/broker/Ollama residency rows and the
              loopback service-probe grid that used to sit here were removed on
              2026-08-20: they were a client of one local machine's daemons
              (gpubrokerd at 127.0.0.1:8447, a single consumer GPU) and cannot
              exist in a production deploy. Ollama stays only as the embeddings
              backend behind the WORKSHOP_TEXT_EMBED_URL config seam, which is
              not a Settings control. */}
          <AgentsSection />
          <AutonomyControls />
        </>
      )}
    </div>
  );
}

// ----------------------------------------------------------------------------
// Memory section: defaults for what a chat's SEARCH can find outside its own
// scope (memory-and-documentation-model-2026-08-14.md sec 6).
//
// This is CURATION, NOT SECURITY, and the copy has to keep saying so. Everything
// on this box runs as one uid, so none of this stops anything reading anything;
// it decides what a SEARCH surfaces, so a chat's recall stays about that chat's
// own work. Per SEC-A2 this repo already has one open ticket about having
// described scope as a boundary it was not. Do not reintroduce the words
// permission, access, allowed or blocked here. Real confinement is a separate,
// already-shipped mechanism (the bwrap/uid scoped-execution layer, A1): it
// governs what an agent's OWN turn can touch on disk, not what a search index
// returns, so making search enforce a boundary is a different feature, not a
// tightening of this one.
//
// Chats default to "Its scope only" on the operator's 2026-08-14 direction:
// transcripts are the biggest and least deliberate corpus, so they stay out of
// other chats' recall unless he says otherwise. Per-item overrides live on the
// Memory page.
//
// Commits on change, like the other view-preference controls on this page
// (AppearanceSection above). Operator, 2026-08-20: "having to click an edit
// button to edit the contents is probably not the right way that we want to do
// this."
//
// THESE ARE NOT DEFAULTS FOR ITEMS THAT DON'T EXIST YET. This comment used to
// say each control was "a low-stakes default for items that don't exist yet, so
// a misclick costs nothing already written", and that reasoning is what let one
// misclick here cost everything already written: a default is resolved at
// SEARCH time against the whole corpus, not stamped onto an item at write time,
// so every existing note, document and transcript of that kind changes what it
// answers the instant this control moves. On 2026-08-23 documents sat at "Its
// scope only" and all 215 of them, including the platform's model of record,
// returned nothing from every real search, because a document has no scope and
// "only from its own scope" therefore matched nowhere. Write the copy for a
// control that retunes the entire corpus, because that is what it is.
//
// The erasure itself is fixed in memory_policy.reachable(): a kind with no
// scope falls back to the tree's rule rather than to a comparison that cannot
// hold. One consequence lands in this copy -- for documents all three values
// now behave identically -- and the documents hint says so rather than
// implying a difference the code does not have.
// ----------------------------------------------------------------------------
type ShareDraft = { note: ShareValue; doc: ShareValue; chat: ShareValue };

function MemorySection() {
  const [policy, setPolicy] = useState<MemoryPolicy | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [saveErr, setSaveErr] = useState<string | null>(null);
  const [savingKey, setSavingKey] = useState<keyof ShareDraft | null>(null);

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

  const setDefault = useCallback(async (key: keyof ShareDraft, value: ShareValue) => {
    setSaveErr(null);
    setSavingKey(key);
    try {
      setPolicy(await setDefaults({ ...current, [key]: value }));
    } catch (e) {
      setSaveErr(String(e));
    } finally {
      setSavingKey(null);
    }
  }, [current]);

  if (err) {
    return (
      <Section icon={Library}>
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
      <Section icon={Library}>
        <Row label="notes"><Skeleton width="12rem" style={{ height: "var(--control-h)" }} /></Row>
        <Row label="documents"><Skeleton width="12rem" style={{ height: "var(--control-h)" }} /></Row>
        <Row label="chat transcripts"><Skeleton width="12rem" style={{ height: "var(--control-h)" }} /></Row>
      </Section>
    );
  }

  const kinds: Array<{ key: keyof ShareDraft; label: string; hint: string }> = [
    {
      key: "note",
      label: "notes",
      hint: "Applies to every note you already have, not just the next one. A note is meant to be found, so the default lets it reach parent scopes too.",
    },
    {
      key: "doc",
      label: "documents",
      hint: "Documents sit outside the scope tree, so every chat's search finds all of them whichever of these you pick. Nothing here can hide a document.",
    },
    {
      key: "chat",
      label: "chat transcripts",
      hint: "Applies to every transcript you already have, not just the next one. A transcript is the biggest, least curated thing here, so the default keeps it to its own scope. File a note if you want the substance to travel further, or change one chat's own setting from Sharing in that chat's actions menu.",
    },
  ];

  return (
    <Section icon={Library}>
      {/* ONE LINE, and it is here because of SEC-A2 rather than to teach the
          control: without it this reads as an access control, which it is not.
          The five sentences that used to be here explained the mechanism, which
          is not what a screen is for. */}
      <p className="ws-muted" style={{ fontSize: "var(--fs-sm)", margin: 0 }}>
        A search filter, not a lock.
      </p>

      {kinds.map(({ key, label, hint }) => (
        <Row key={key} label={label} hint={hint}>
          <Select
            value={current[key]}
            onChange={(e) => { void setDefault(key, e.target.value as ShareValue); }}
            disabled={savingKey === key}
            aria-label={`Default for ${label}`}
          >
            {SHARE_VALUES.map((v) => (
              <option key={v} value={v}>{SHARE_LABEL[v]}</option>
            ))}
          </Select>
        </Row>
      ))}

      {saveErr && (
        <Notice tone="error" detail={saveErr} where="PUT /api/memory-policy/defaults">
          Couldn't save memory settings.
        </Notice>
      )}

      <IndexStatus />
    </Section>
  );
}

// ----------------------------------------------------------------------------
// The search index, and how it is built. This is what makes a search over notes
// and documents work at all, so "is it up to date and what is doing the
// embedding" is a real question about how Memory is configured.
//
// IT USED TO BE ON THE MEMORY PAGE, in one muted line under every view: "15,909
// chunks indexed | <model name>". The operator's read on 2026-08-27: "at the
// bottom of every page we show the number of chunks indexed and then the local
// model used to embed it, and I don't know why that's here at all. That
// shouldn't be here. Are we doing anything like this anywhere else?" We are
// not, and it should not be: it is a readout about the machinery, on the page
// whose whole job is the content. Here it is beside the other answers about how
// memory behaves. See memory-page-and-file-viewer-2026-08-27.md D10.
// ----------------------------------------------------------------------------

type IndexStatusResp = {
  chunks: number;
  model: string;
  embedder_ok: boolean;
  embedder_error: string;
};

function IndexStatus() {
  const [status, setStatus] = useState<IndexStatusResp | null>(null);

  useEffect(() => {
    let live = true;
    // Cheap: it reads two already-cached files. Silent on failure, because a
    // readout that cannot be read is not a thing the operator has to act on and
    // this is not the section's subject.
    fetch("/api/memory/status")
      .then((r) => (r.ok ? (r.json() as Promise<IndexStatusResp>) : Promise.reject(new Error(String(r.status)))))
      .then((j) => { if (live) setStatus(j); })
      .catch(() => {});
    return () => { live = false; };
  }, []);

  if (!status) return null;
  return (
    <>
      <Row
        label="Search index"
        hint={
          status.embedder_ok
            ? `${status.chunks.toLocaleString()} chunks of notes, documents, chats and tickets, embedded by ${status.model}. Rebuilt as things are written.`
            : `${status.chunks.toLocaleString()} chunks, embedded by ${status.model}. Nothing written since the embedder stopped is searchable.`
        }
      >
        {status.embedder_ok
          ? <StatusPill status="ok">Ready</StatusPill>
          : <StatusPill status="warn">Embedder offline</StatusPill>}
      </Row>
      {!status.embedder_ok && status.embedder_error && (
        <Notice tone="warn" detail={status.embedder_error} where="GET /api/memory/status">
          New notes are not being indexed until this is fixed.
        </Notice>
      )}
    </>
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
// shared/native-bridge.ts + @workshop/ui's native/* (promoted from
// frontend/src/native/* 2026-08-20, Stage 4b).
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
  const [confirmSignOutAll, setConfirmSignOutAll] = useState(false);
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
    <Section icon={UserRound}>
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
          {/* CONFIRMED, AND DRESSED AS WHAT IT IS. This ended every session on
              every device on one click, while rendering as flat grey text
              beside a red "Sign out" that only ends this one. So the more
              destructive of the two read as the safer of the two. Rule 8 wants
              a ConfirmDialog naming the consequence; DeviceTokens two rows
              down already uses one. */}
          <Button
            variant="danger-ghost"
            size="sm"
            disabled={busy}
            onClick={() => setConfirmSignOutAll(true)}
          >
            Sign out everywhere
          </Button>
          {confirmSignOutAll && (
            <ConfirmDialog
              open
              title="Sign out everywhere?"
              body="Every signed-in device is signed out, including this one. Anything running keeps running; you will need to sign in again to watch it."
              confirmLabel="Sign out everywhere"
              onCancel={() => setConfirmSignOutAll(false)}
              onConfirm={async () => {
                setConfirmSignOutAll(false);
                setBusy(true);
                await logout(true);
              }}
            />
          )}
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
  return (
    <Section icon={Palette}>
      <Row label="theme">
        <Segmented options={THEME_OPTIONS} value={theme} onChange={setTheme} ariaLabel="Theme" />
      </Row>
      {/* No `hint` here: the field's hint renders below the WHOLE control, so
          the selected scheme's description captioned whichever group happened
          to be last. The picker places it under the group holding the
          selection. */}
      <Row label="color scheme">
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
// All three commit on change, matching the Memory section's defaults above.
// This used to require an explicit Save on the theory that these write config
// (design system rule 7) and a mis-click would misroute the next chat's work.
// Overridden by the operator, 2026-08-20: none of the three touches a chat
// that already exists, so the actual cost of a mis-click is picking a
// different default for chats not yet created, which the same control fixes
// in one more click. That is the "view preference" case in rule 7's table,
// not the "config" one.
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
  const [savingKey, setSavingKey] = useState<keyof ChatPrefsDraft | null>(null);

  useEffect(() => {
    let live = true;
    void chatApi.getChatPrefs()
      .then((p) => { if (live) setPrefs(p); })
      .catch((e) => { if (live) setErr(String(e)); });
    return () => { live = false; };
  }, []);

  const current: ChatPrefsDraft = {
    // Resolved through `chatModeDef`, not cast. The server clamps, but a
    // retired id (`orchestrated`) reaching this as a bare cast would leave the
    // Segmented with no matching option, i.e. a blank control.
    default_mode: chatModeDef(prefs?.default_mode ?? undefined).id,
    default_effort: prefs?.default_effort ?? NO_PREFERENCE,
    default_permission_mode: prefs?.default_permission_mode ?? NO_PREFERENCE,
  };

  const setPref = useCallback(async <K extends keyof ChatPrefsDraft>(key: K, value: ChatPrefsDraft[K]) => {
    setSaveErr(null);
    setSavingKey(key);
    try {
      setPrefs(await chatApi.setChatPrefs({ ...current, [key]: value }));
    } catch (e) {
      setSaveErr(String(e));
    } finally {
      setSavingKey(null);
    }
  }, [current]);

  if (err) {
    return (
      <Section icon={MessageSquare}>
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
      <Section icon={MessageSquare}>
        <Row label="new chats"><Skeleton width="12rem" style={{ height: "var(--control-h)" }} /></Row>
        <Row label="effort"><Skeleton width="12rem" style={{ height: "var(--control-h)" }} /></Row>
        <Row label="asks before acting"><Skeleton width="12rem" style={{ height: "var(--control-h)" }} /></Row>
      </Section>
    );
  }

  const platformMode = permissionModeDef(prefs.platform_permission_mode).label;

  return (
    <Section icon={MessageSquare}>
      <Row
        label="new chats"
        // The hint follows what is SELECTED, so it explains the choice being
        // made rather than the one being replaced.
        hint={`${chatModeDef(current.default_mode).title} Existing chats keep the mode they were started in.`}
      >
        <Segmented
          options={CHAT_MODE_OPTIONS}
          value={current.default_mode}
          onChange={(v) => { void setPref("default_mode", v); }}
          ariaLabel="Mode for new chats"
        />
      </Row>

      <Row
        label="effort"
        hint="How hard the model works on a turn, for any chat that hasn't picked its own. A level the chosen model doesn't offer falls back to that model's default."
      >
        <Select
          value={current.default_effort}
          onChange={(e) => { void setPref("default_effort", e.target.value); }}
          disabled={savingKey === "default_effort"}
          aria-label="Default effort"
        >
          <option value={NO_PREFERENCE}>Model default</option>
          {prefs.efforts.map((level) => (
            <option key={level} value={level}>{effortLabel(level)}</option>
          ))}
        </Select>
      </Row>

      <Row
        label="asks before acting"
        hint={
          current.default_permission_mode
            ? permissionModeDef(current.default_permission_mode).hint
            : `Chats that haven't picked run at ${platformMode}: ` +
              `${permissionModeDef(prefs.platform_permission_mode).hint}`
        }
      >
        <Select
          value={current.default_permission_mode}
          onChange={(e) => { void setPref("default_permission_mode", e.target.value); }}
          disabled={savingKey === "default_permission_mode"}
          aria-label="Default permission mode"
        >
          <option value={NO_PREFERENCE}>{`System default (${platformMode})`}</option>
          {prefs.permission_modes.map((id) => (
            <option key={id} value={id}>{permissionModeDef(id).label}</option>
          ))}
        </Select>
      </Row>

      {/* A line, not a region: the settings it failed to save are still on
          screen right above it. */}
      {saveErr && (
        <Notice tone="error" detail={saveErr} where="POST /api/chat/prefs">
          Couldn't save chat settings.
        </Notice>
      )}
    </Section>
  );
}

// ----------------------------------------------------------------------------
// Timezone — which clock this account's times are read in
// ----------------------------------------------------------------------------
// UTC IS CANONICAL AND THIS SETTING DOES NOT CHANGE THAT. Everything is stored
// in UTC; this says which zone a time is PRESENTED in, and it is what every
// agent turn is told alongside the UTC reading (backend `services/clock.py`,
// `chat_context.clock_line`) so an event can be reported in the user's hours.
//
// THE BROWSER IS A SUGGESTION, NEVER A WRITER. `Intl` knows this browser's zone
// and the row offers it as a button the user presses. It is not adopted on load
// and it is not adopted on a mismatch: a user with a work browser, a phone and a
// laptop that travelled would have the zone rewritten by whichever one connected
// last, and every armed wake-up would move with it. A clock that follows the
// most recent device is worse than one that is merely wrong, because there is
// nothing stable in it to correct.
//
// It shares `/api/chat/prefs` with the chat defaults because that is the one
// per-owner prefs store this screen already writes. A second settings store for
// one field is how two answers to the same question start.

/** Every zone this browser's ICU data knows, or [] on a runtime without the
 *  ES2022 accessor — in which case the row still renders and still shows what is
 *  in force, it just cannot offer the full list. */
function browserZones(): string[] {
  try {
    return Intl.supportedValuesOf("timeZone");
  } catch {
    return [];
  }
}

/** This browser's own guess at where it is. "UTC" on a machine that has none. */
function browserZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

/** The clock reading in `zone` right now, for the caption that confirms a
 *  choice. Empty when the browser cannot format that zone, rather than throwing
 *  inside a render. */
function readingIn(zone: string): string {
  try {
    return new Intl.DateTimeFormat(undefined, {
      timeZone: zone, hour: "numeric", minute: "2-digit", weekday: "short",
    }).format(new Date());
  } catch {
    return "";
  }
}

function TimezoneSection() {
  const [prefs, setPrefs] = useState<ChatPrefs | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [saveErr, setSaveErr] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let live = true;
    void chatApi.getChatPrefs()
      .then((p) => { if (live) setPrefs(p); })
      .catch((e) => { if (live) setErr(String(e)); });
    return () => { live = false; };
  }, []);

  const save = useCallback(async (zone: string) => {
    setSaveErr(null);
    setSaving(true);
    try {
      setPrefs(await chatApi.setChatPrefs({ timezone: zone }));
    } catch (e) {
      setSaveErr(String(e));
    } finally {
      setSaving(false);
    }
  }, []);

  if (err) {
    return (
      <Section icon={Clock}>
        <StateBlock
          variant="error"
          title="Couldn't load your timezone"
          hint={`GET /api/chat/prefs failed: ${err}`}
        />
      </Section>
    );
  }
  if (!prefs) {
    return (
      <Section icon={Clock}>
        <Row label="timezone"><Skeleton width="16rem" style={{ height: "var(--control-h)" }} /></Row>
      </Section>
    );
  }

  const set = prefs.timezone ?? NO_PREFERENCE;
  const inForce = prefs.resolved_timezone || "UTC";
  const source = prefs.timezone_source || "utc";
  const zones = browserZones();
  const here = browserZone();
  const reading = readingIn(inForce);

  // What "not set" is honest about depends on what answers instead: the zone
  // this installation is configured for, or UTC.
  const unsetLabel = source === "installation"
    ? `Not set: using this installation's zone (${inForce})`
    : "Not set: times are UTC";

  // The list comes from the browser, so a stored zone this browser's ICU data
  // has never heard of would have no option to select and the control would
  // render blank. Carry it rather than dropping it.
  const options = set && !zones.includes(set) ? [set, ...zones] : zones;

  return (
    <Section icon={Clock}>
      <Row
        label="timezone"
        hint={
          <>
            Optional. Times are stored in UTC and shown to you in this zone, and
            every chat is told both readings so it can report an event in your
            hours.{reading ? ` It is ${reading} in ${inForce} now.` : ""}
          </>
        }
      >
        <Select
          value={set}
          onChange={(e) => { void save(e.target.value); }}
          disabled={saving}
          aria-label="Your timezone"
        >
          <option value={NO_PREFERENCE}>{unsetLabel}</option>
          {options.map((z) => <option key={z} value={z}>{z}</option>)}
        </Select>
      </Row>

      {/* THE PREFILL, and the only thing the browser is allowed to do here. It
          appears when this browser disagrees with what is set, and it writes
          only when pressed. */}
      {here !== set && (
        <Row
          label="this browser"
          hint="Detected from this browser. Nothing is saved until you use it, and switching browsers never changes your setting on its own."
        >
          <span style={{ display: "inline-flex", gap: "var(--space-2)", alignItems: "center", flexWrap: "wrap" }}>
            <ReadOnly value={here} />
            <Button size="sm" variant="ghost" loading={saving} disabled={saving}
                    onClick={() => { void save(here); }}>
              Use this
            </Button>
          </span>
        </Row>
      )}

      {saveErr && (
        <Notice tone="error" detail={saveErr} where="POST /api/chat/prefs">
          Couldn't save your timezone.
        </Notice>
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
  const [conc, setConc] = useState<Concurrency | null>(null);
  const [concErr, setConcErr] = useState<string | null>(null);
  const [saveErr, setSaveErr] = useState<string | null>(null);

  // THE FLEET COUNT AND THE "NEW AGENT" BUTTON ARE GONE. Both belonged to the
  // roster: one counted `GET /api/agents/`, which counted markdown files on
  // disk rather than anything that runs, and the other made a second entity
  // that the model no longer has. A thread is the one entity, and what runs
  // unasked is a thread that is armed
  // (threads-branches-and-pages-2026-08-29 sections 1 and 6). What is left in
  // this section is what was always true of the whole installation rather than
  // of any one agent: how many model processes may run at once.

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
      <Row label="what runs unasked">
        <span>
          A thread set to run on its own. That setting and its role are on{" "}
          <Link to="/roles">Roles</Link>, and on the thread itself.
        </span>
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
    </Section>
  );
}

