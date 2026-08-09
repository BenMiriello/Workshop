import { useCallback, useEffect, useState, type ComponentType } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  Banknote,
  PiggyBank,
  TrainFront,
  Watch,
  Bird,
  MapPin,
  ExternalLink,
  ChevronDown,
  Wrench,
  FolderPlus,
  LayoutGrid,
  type LucideProps,
} from "lucide-react";
import {
  Button,
  Card,
  CardGrid,
  Divider,
  PageHeader,
  Row,
  SectionLabel,
  Segmented,
  Skeleton,
  StateBlock,
  StatusDot,
  Toggle,
  type SegmentedOption,
  type StatusKind,
} from "../ui";

// Apps page: a uniform grid of cards. Every app — internal or external — looks
// the same here. Tap a card to enter the app. Privacy-on-glance: cards show
// generic status (counts / freshness / health) but never specific values like
// dollar amounts. Real data lives one tap deeper, on each app's full page.

type CacheMeta = { cached: boolean; ts: number; age_s: number; ttl_s: number };

type FinanceStatusEntry = { ts: number; age_s: number; ttl_s: number; size_bytes: number };
type FinanceStatusResp = {
  entries: Record<string, FinanceStatusEntry>;
};

type FinanceBalancesShape = { items: { accounts: unknown[] }[]; _cache?: CacheMeta };

type ServiceProbe = {
  name: string;
  status: "up" | "down" | string;
  http_status?: number | null;
  latency_ms?: number | null;
  notes?: string | null;
};
type ServicesResp = { services: ServiceProbe[] };

const fmtAge = (sec: number) => {
  if (sec <= 0) return "just now";
  if (sec < 60) return `${Math.round(sec)}s ago`;
  if (sec < 3600) return `${Math.round(sec / 60)}m ago`;
  if (sec < 86400) return `${Math.round(sec / 3600)}h ago`;
  return `${Math.round(sec / 86400)}d ago`;
};

// Card status tone maps to the design-system status kinds (or null for neutral
// muted text). "warn" doubles for degraded; "err" for unreachable.
type Tone = "ok" | "warn" | "err" | "muted";

const toneStatus = (tone: Tone | undefined): StatusKind | null => {
  switch (tone) {
    case "ok":
      return "ok";
    case "warn":
      return "warn";
    case "err":
      return "danger";
    default:
      return null;
  }
};

type AppCard = {
  key: string;
  Icon: ComponentType<LucideProps>;
  title: string;
  tagline: string;
  status: { text: string; tone?: Tone } | null;
  to?: string; // internal Workshop route — react-router Link
  href?: string; // same-origin path-prefixed app or external URL
  external?: boolean; // true => render an ExternalLink hint; we still navigate same-tab
};

// A launcher card as declared by the backend for an installed app. The launcher
// is manifest-driven (GET /api/base-apps/launcher): which apps appear, their
// order, titles, icons, and routes all come from the installed-app catalog, not
// from a hardcoded list here. A fresh instance installs nothing => empty roster.
type LauncherApp = {
  id: string;
  title: string;
  tagline: string;
  icon: string;
  route?: string;
  href?: string;
  external?: boolean;
  probe?: string; // service-probe name for external apps (health dot)
};

// Icon-name -> lucide component. The backend sends a stable icon name; the shell
// owns the actual glyph. Unknown names fall back to a generic app icon.
const ICONS: Record<string, ComponentType<LucideProps>> = {
  Banknote,
  PiggyBank,
  TrainFront,
  Watch,
  Bird,
  MapPin,
};
const iconFor = (name: string): ComponentType<LucideProps> => ICONS[name] ?? LayoutGrid;

type LauncherState =
  | { kind: "loading" }
  | { kind: "loaded"; apps: LauncherApp[] }
  | { kind: "error"; message: string };

// View | Manage. View = the plain launcher grid (cards are launch links).
// Manage = cards expand to reveal per-app details + the workbench bridge.
type ViewMode = "view" | "manage";
const VIEW_OPTIONS: SegmentedOption<ViewMode>[] = [
  { value: "view", label: "View" },
  { value: "manage", label: "Manage" },
];

// Just the project fields the bridge needs. GET /api/projects/ returns a LIST
// of these (matched to an app by slug == the app's key). We do not fabricate
// per-app manage data (storage/grants/jobs); those land with the Phase 4/5
// manifest. Only genuinely known fields are shown.
type ProjectLite = { slug: string };

// Async state for the projects lookup (drives the four list states in the
// expanded panel: loading / loaded / empty / error).
type ProjectsState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "loaded"; slugs: Set<string> }
  | { kind: "error"; message: string };

function CardBody({ card }: { card: AppCard }) {
  const Icon = card.Icon;
  const kind = card.status ? toneStatus(card.status.tone) : null;
  return (
    <div style={{ padding: "var(--space-5)", height: "100%" }}>
      <Row style={{ marginBottom: "var(--space-2)" }}>
        <Icon size={22} strokeWidth={1.8} aria-hidden style={{ flexShrink: 0 }} />
        <h3 className="ws-card__title" style={{ display: "flex", alignItems: "center", gap: "var(--space-1)" }}>
          {card.title}
          {card.external && <ExternalLink size={14} strokeWidth={1.8} aria-hidden style={{ color: "var(--muted)" }} />}
        </h3>
      </Row>
      <p className="ws-card__meta">{card.tagline}</p>
      {card.status &&
        (kind ? (
          <div style={{ marginTop: "var(--space-2)", fontSize: "var(--fs-xs)" }}>
            <StatusDot status={kind}>{card.status.text}</StatusDot>
          </div>
        ) : (
          <p className="ws-card__meta" style={{ marginTop: "var(--space-2)" }}>
            {card.status.text}
          </p>
        ))}
    </div>
  );
}

// The destination an app launches to, for display in the manage panel.
const cardRoute = (card: AppCard): string => card.to ?? card.href ?? "(none)";

// Async state for one app's manifest-backed agent-button setting.
type AgentState =
  | { kind: "loading" }
  | { kind: "ready"; on: boolean }
  | { kind: "absent" } // no manifest (external app) — setting not applicable
  | { kind: "error"; message: string };

// The "Assistant" toggle in an app's Manage panel. Reads the app's manifest
// (ui.agent_button) and writes it back on change — a view preference, so it
// commits immediately rather than behind an Edit/Save bar. Optimistic with
// revert on failure. Apps with no manifest (external, e.g. Wearables) render
// nothing here.
function AgentButtonSetting({ appId }: { appId: string }) {
  const [state, setState] = useState<AgentState>({ kind: "loading" });
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const r = await fetch(`/api/base-apps/manifests/${appId}`);
      if (r.status === 404) {
        setState({ kind: "absent" });
        return;
      }
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      const m = await r.json();
      setState({ kind: "ready", on: m?.ui?.agent_button !== false });
    } catch (e) {
      setState({ kind: "error", message: e instanceof Error ? e.message : String(e) });
    }
  }, [appId]);

  useEffect(() => {
    void load();
  }, [load]);

  const toggle = useCallback(
    async (next: boolean) => {
      const prev = state;
      setState({ kind: "ready", on: next }); // optimistic
      setSaving(true);
      try {
        const r = await fetch(`/api/base-apps/manifests/${appId}/ui`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ agent_button: next }),
        });
        if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
        const m = await r.json();
        setState({ kind: "ready", on: m?.ui?.agent_button !== false });
      } catch (e) {
        setState(prev); // revert
        setState({ kind: "error", message: e instanceof Error ? e.message : String(e) });
      } finally {
        setSaving(false);
      }
    },
    [appId, state],
  );

  if (state.kind === "absent") return null;

  return (
    <>
      <SectionLabel>Assistant</SectionLabel>
      {state.kind === "loading" ? (
        <Skeleton width="14rem" style={{ height: "var(--space-6)" }} />
      ) : state.kind === "error" ? (
        <StateBlock
          variant="error"
          title="Couldn't load the assistant setting"
          hint={
            <>
              <code>PUT /api/base-apps/manifests/{appId}/ui</code>: {state.message}
            </>
          }
          action={
            <Button variant="ghost" size="sm" onClick={() => void load()}>
              Retry
            </Button>
          }
        />
      ) : (
        <Toggle
          checked={state.on}
          disabled={saving}
          onChange={(next) => void toggle(next)}
          label="Show the assistant button inside this app"
        />
      )}
    </>
  );
}

// The expanded manage panel for a single app. Shows only fields we genuinely
// know (title, description, route, status) plus the link-not-fuse workbench
// bridge. Richer manage data (storage/grants/jobs/agents) arrives with the
// app manifest in a later phase (noted, not faked).
function ManageDetails({
  card,
  projects,
  busy,
  onOpenOrCreate,
  onRetry,
}: {
  card: AppCard;
  projects: ProjectsState;
  busy: boolean;
  onOpenOrCreate: (card: AppCard) => void;
  onRetry: () => void;
}) {
  const statusKind = card.status ? toneStatus(card.status.tone) : null;

  // The bridge row. Link-not-fuse: a backing project is optional. If one
  // exists, link to it; if not, offer to create it (only on click, never auto).
  const bridge = (() => {
    switch (projects.kind) {
      case "idle":
      case "loading":
        return <Skeleton width="11rem" style={{ height: "var(--space-6)" }} />;
      case "error":
        return (
          <StateBlock
            variant="error"
            title="Couldn't load projects"
            hint={
              <>
                <code>GET /api/projects/</code>: {projects.message}
              </>
            }
            action={
              <Button variant="ghost" size="sm" onClick={onRetry}>
                Retry
              </Button>
            }
          />
        );
      case "loaded": {
        const linked = projects.slugs.has(card.key);
        return (
          <Row style={{ justifyContent: "space-between", flexWrap: "wrap", gap: "var(--space-2)" }}>
            <span className="ws-card__meta">
              {linked ? "Backed by a project" : "No backing project yet"}
            </span>
            <Button
              variant={linked ? "default" : "primary"}
              size="sm"
              disabled={busy}
              loading={busy}
              onClick={() => onOpenOrCreate(card)}
            >
              {linked ? <Wrench aria-hidden /> : <FolderPlus aria-hidden />}
              {linked ? "Open in workbench" : "Create project"}
            </Button>
          </Row>
        );
      }
    }
  })();

  return (
    <div
      style={{
        padding: "0 var(--space-5) var(--space-5)",
        display: "flex",
        flexDirection: "column",
        gap: "var(--space-3)",
      }}
    >
      <Divider />
      <dl style={{ margin: 0, display: "grid", gridTemplateColumns: "auto 1fr", gap: "var(--space-1) var(--space-3)" }}>
        <dt className="ws-card__meta">Route</dt>
        <dd style={{ margin: 0, fontSize: "var(--fs-xs)", overflowWrap: "anywhere" }}>
          <code>{cardRoute(card)}</code>
        </dd>
        <dt className="ws-card__meta">Status</dt>
        <dd style={{ margin: 0, fontSize: "var(--fs-xs)" }}>
          {card.status ? (
            statusKind ? (
              <StatusDot status={statusKind}>{card.status.text}</StatusDot>
            ) : (
              <span className="ws-card__meta">{card.status.text}</span>
            )
          ) : (
            <span className="ws-card__meta">(none)</span>
          )}
        </dd>
      </dl>

      <AgentButtonSetting appId={card.key} />

      <SectionLabel>Workbench</SectionLabel>
      {bridge}

      <p className="ws-card__meta" style={{ margin: 0 }}>
        Storage, grants, scheduled jobs and attached agents come with the app.
      </p>
    </div>
  );
}

export default function Apps() {
  const nav = useNavigate();
  const [financeStatus, setFinanceStatus] = useState<{ accountCount: number | null; ageS: number | null } | null>(null);
  const [services, setServices] = useState<ServiceProbe[] | null>(null);
  const [birdStatus, setBirdStatus] = useState<{ species: number; today: number; available: boolean } | null>(null);
  const [locationStatus, setLocationStatus] = useState<{ ageS: number | null; available: boolean } | null>(null);

  const [launcher, setLauncher] = useState<LauncherState>({ kind: "loading" });
  const [mode, setMode] = useState<ViewMode>("view");
  const [expanded, setExpanded] = useState<string | null>(null); // one card open at a time
  const [projects, setProjects] = useState<ProjectsState>({ kind: "idle" });
  const [bridgeBusy, setBridgeBusy] = useState<string | null>(null); // app key mid open/create

  // The installed-app roster drives every card here. A fresh instance installs
  // nothing and this returns an empty list — the launcher then shows its empty
  // state rather than a hardcoded app set.
  const loadLauncher = useCallback(async () => {
    setLauncher({ kind: "loading" });
    try {
      const r = await fetch("/api/base-apps/launcher");
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      const j = (await r.json()) as { apps?: LauncherApp[] };
      setLauncher({ kind: "loaded", apps: j.apps || [] });
    } catch (e) {
      setLauncher({ kind: "error", message: e instanceof Error ? e.message : String(e) });
    }
  }, []);

  // Lazily load the project list, only when Manage is first turned on, and
  // only once (re-runs on retry via an explicit reset to "idle").
  const loadProjects = useCallback(async () => {
    setProjects({ kind: "loading" });
    try {
      const r = await fetch("/api/projects/");
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      const list = (await r.json()) as ProjectLite[];
      setProjects({ kind: "loaded", slugs: new Set(list.map((p) => p.slug)) });
    } catch (e) {
      setProjects({ kind: "error", message: e instanceof Error ? e.message : String(e) });
    }
  }, []);

  const enterManage = useCallback(() => {
    setMode("manage");
    // Fetch once when Manage is first entered; if it already loaded (or is
    // loading), leave it. Errors are recoverable via the in-panel Retry.
    if (projects.kind === "idle") void loadProjects();
  }, [loadProjects, projects.kind]);

  // Link-not-fuse bridge. If a backing project exists, open it. If not, create
  // one (only here, on click) then open it. A 409 means it already exists,
  // so just navigate. Never auto-create.
  const openOrCreate = useCallback(
    async (card: AppCard) => {
      const slug = card.key;
      const exists = projects.kind === "loaded" && projects.slugs.has(slug);
      if (exists) {
        nav(`/projects/${slug}`);
        return;
      }
      setBridgeBusy(slug);
      try {
        const r = await fetch("/api/projects/", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            slug,
            title: card.title,
            purpose: `${card.title} app`,
            template: "blank",
          }),
        });
        if (!r.ok && r.status !== 409) throw new Error(`${r.status} ${await r.text()}`);
        nav(`/projects/${slug}`);
      } catch (e) {
        setProjects({ kind: "error", message: e instanceof Error ? e.message : String(e) });
      } finally {
        setBridgeBusy(null);
      }
    },
    [nav, projects],
  );

  const loadFinance = useCallback(async () => {
    try {
      const [statusR, balR] = await Promise.all([fetch("/api/finance/cache"), fetch("/api/finance/balances")]);
      const status = statusR.ok ? ((await statusR.json()) as FinanceStatusResp) : { entries: {} };
      const bal = balR.ok ? ((await balR.json()) as FinanceBalancesShape) : { items: [] };
      const accountCount = (bal.items || []).reduce((s, it) => s + (it.accounts?.length || 0), 0);
      const entry = status.entries?.["balances"];
      const ageS = entry ? Math.max(0, entry.age_s) : null;
      setFinanceStatus({ accountCount, ageS });
    } catch {
      setFinanceStatus({ accountCount: null, ageS: null });
    }
  }, []);

  const loadServices = useCallback(async () => {
    try {
      const r = await fetch("/api/services/");
      const s = r.ok ? ((await r.json()) as ServicesResp) : { services: [] };
      setServices(s.services || []);
    } catch {
      setServices([]);
    }
  }, []);

  const loadBirds = useCallback(async () => {
    try {
      const r = await fetch("/api/birds/summary");
      const s = r.ok ? await r.json() : null;
      if (s) setBirdStatus({ species: s.species_count || 0, today: s.today?.detections || 0, available: !!s.available });
      else setBirdStatus({ species: 0, today: 0, available: false });
    } catch {
      setBirdStatus({ species: 0, today: 0, available: false });
    }
  }, []);

  const loadLocation = useCallback(async () => {
    try {
      const r = await fetch("/api/location/latest");
      const j = r.ok ? await r.json() : { latest: null };
      const rec = j.latest as { ts?: string } | null;
      if (rec?.ts) {
        const ageS = Math.max(0, Math.round((Date.now() - Date.parse(rec.ts)) / 1000));
        setLocationStatus({ ageS, available: true });
      } else {
        setLocationStatus({ ageS: null, available: false });
      }
    } catch {
      setLocationStatus({ ageS: null, available: false });
    }
  }, []);

  useEffect(() => {
    loadLauncher();
    loadFinance();
    loadServices();
    loadBirds();
    loadLocation();
  }, [loadLauncher, loadFinance, loadServices, loadBirds, loadLocation]);

  const findService = (needle: string) => services?.find((s) => s.name.includes(needle));

  // Live status for one launcher card, derived from the per-app probes above and
  // keyed by app id. Unknown ids get no status line (the card still launches).
  const statusFor = (app: LauncherApp): AppCard["status"] => {
    switch (app.id) {
      case "finance":
        return financeStatus == null
          ? { text: "loading…", tone: "muted" }
          : financeStatus.accountCount == null
            ? { text: "unavailable", tone: "err" }
            : {
                text: `${financeStatus.accountCount} account${financeStatus.accountCount === 1 ? "" : "s"} · ${financeStatus.ageS != null ? `updated ${fmtAge(financeStatus.ageS)}` : "no data yet"}`,
                tone: "muted",
              };
      case "budget":
        return { text: "plan & simulate", tone: "muted" };
      case "birds":
        return birdStatus == null
          ? { text: "loading…", tone: "muted" }
          : !birdStatus.available
            ? { text: "no detections yet", tone: "muted" }
            : { text: `${birdStatus.species} species · ${birdStatus.today} today`, tone: "ok" };
      case "location":
        return locationStatus == null
          ? { text: "loading…", tone: "muted" }
          : !locationStatus.available
            ? { text: "no pings yet", tone: "muted" }
            : {
                // A ping age is just info, not health — OwnTracks "significant"
                // mode only pings on movement, so hours-old while stationary is
                // normal. Green only when genuinely recent; otherwise neutral.
                text: `last ping ${fmtAge(locationStatus.ageS ?? 0)}`,
                tone: (locationStatus.ageS ?? Infinity) < 3600 ? "ok" : "muted",
              };
      case "commute":
        return { text: "schedule & live status", tone: "muted" };
      default:
        break;
    }
    // External apps declare a service probe; render its health as the status.
    if (app.probe) {
      const probe = findService(app.probe);
      if (services === null) return { text: "probing…", tone: "muted" };
      if (!probe) return { text: "no probe configured", tone: "muted" };
      return probe.status === "up"
        ? { text: `up · ${probe.http_status} · ${probe.latency_ms}ms`, tone: "ok" }
        : { text: probe.notes ? `down: ${probe.notes}` : probe.http_status ? `down: HTTP ${probe.http_status}` : "down", tone: "err" };
    }
    return null;
  };

  const cards: AppCard[] =
    launcher.kind === "loaded"
      ? launcher.apps.map((app) => ({
          key: app.id,
          Icon: iconFor(app.icon),
          title: app.title,
          tagline: app.tagline,
          to: app.external ? undefined : app.route,
          href: app.external ? app.href : undefined,
          external: app.external,
          status: statusFor(app),
        }))
      : [];

  const manage = mode === "manage";

  return (
    <div>
      <PageHeader
        title="Apps"
        sub={launcher.kind === "loaded" ? `${cards.length} app${cards.length === 1 ? "" : "s"}` : "installed apps"}
        actions={
          <Segmented
            options={VIEW_OPTIONS}
            value={mode}
            ariaLabel="Apps view mode"
            onChange={(next) => {
              if (next === "manage") enterManage();
              else {
                setMode("view");
                setExpanded(null);
              }
            }}
          />
        }
      />
      {launcher.kind === "loading" ? (
        <CardGrid>
          {[0, 1, 2, 3].map((i) => (
            <li key={i}>
              <Card pad0>
                <div style={{ padding: "var(--space-5)" }}>
                  <Skeleton width="9rem" style={{ height: "var(--space-6)", marginBottom: "var(--space-2)" }} />
                  <Skeleton width="13rem" style={{ height: "var(--space-5)" }} />
                </div>
              </Card>
            </li>
          ))}
        </CardGrid>
      ) : launcher.kind === "error" ? (
        <StateBlock
          variant="error"
          title="Couldn't load installed apps"
          hint={
            <>
              <code>GET /api/base-apps/launcher</code>: {launcher.message}
            </>
          }
          action={
            <Button variant="ghost" size="sm" onClick={() => void loadLauncher()}>
              Retry
            </Button>
          }
        />
      ) : cards.length === 0 ? (
        <StateBlock
          title="No apps installed"
          hint={
            <>
              Install an app to see it here, or build one from{" "}
              <Link to="/projects/new">Projects</Link>.
            </>
          }
        />
      ) : (
        <CardGrid>
          {cards.map((card) => {
          const isOpen = manage && expanded === card.key;
          // View mode: the card face is the launch link, unchanged. Manage mode:
          // the face is a toggle that expands the details panel below it.
          const face = manage ? (
            <button
              type="button"
              aria-expanded={isOpen}
              onClick={() => setExpanded((cur) => (cur === card.key ? null : card.key))}
              style={{
                display: "block",
                width: "100%",
                textAlign: "left",
                background: "none",
                border: "none",
                padding: 0,
                color: "inherit",
                font: "inherit",
                cursor: "pointer",
              }}
            >
              <Row style={{ alignItems: "stretch" }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <CardBody card={card} />
                </div>
                <span
                  aria-hidden
                  style={{
                    display: "flex",
                    alignItems: "flex-start",
                    padding: "var(--space-5) var(--space-5) 0 0",
                    color: "var(--muted)",
                    transform: isOpen ? "rotate(180deg)" : "none",
                    transition: "transform var(--dur-fast) var(--ease-out)",
                  }}
                >
                  <ChevronDown size={18} strokeWidth={1.8} />
                </span>
              </Row>
            </button>
          ) : card.to ? (
            <Link to={card.to} style={{ display: "block", height: "100%", color: "inherit", textDecoration: "none" }}>
              <CardBody card={card} />
            </Link>
          ) : (
            <a
              href={card.href}
              rel="noopener noreferrer"
              style={{ display: "block", height: "100%", color: "inherit", textDecoration: "none" }}
            >
              <CardBody card={card} />
            </a>
          );

          return (
            <li key={card.key}>
              <Card interactive={!manage} pad0 style={{ overflow: "hidden" }}>
                {face}
                {isOpen && (
                  <ManageDetails
                    card={card}
                    projects={projects}
                    busy={bridgeBusy === card.key}
                    onOpenOrCreate={openOrCreate}
                    onRetry={loadProjects}
                  />
                )}
              </Card>
            </li>
          );
          })}
        </CardGrid>
      )}
    </div>
  );
}
