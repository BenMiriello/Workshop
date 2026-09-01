import { useCallback, useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { appIcon, appStatuses } from "../apps/registry";
import { queryKeys } from "../lib/queryKeys";
import { AppGlyph } from "../lib/appIcon";
import {
  ExternalLink,
  ChevronDown,
  Wrench,
  FolderPlus,
} from "lucide-react";
import { Button, Card, CardGrid, Divider, PageHeader, Notice, Row, SectionLabel, Segmented, Skeleton, StateBlock, StatusDot, Toggle, type SegmentedOption, type StatusKind, shortHttpError } from "@workshop/ui";
import type { components } from "../api/schema.gen";

// Apps page: a uniform grid of cards. Every app — internal or external — looks
// the same here. Tap a card to enter the app. Privacy-on-glance: cards show
// generic status (counts / freshness / health) but never specific values like
// dollar amounts. Real data lives one tap deeper, on each app's full page.

type ServiceProbe = {
  name: string;
  status: "up" | "down" | string;
  http_status?: number | null;
  latency_ms?: number | null;
  notes?: string | null;
};
type ServicesResp = { services: ServiceProbe[] };

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
  /** What the app declared. Resolved at render by AppGlyph, which falls back to
   *  a monogram of the title, so a card always has a glyph. */
  icon?: string;
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
// The launcher card, named from the generated client
// (services/app_surface.LauncherCard). The hand-written copy declared `title`,
// `tagline` and `icon` as REQUIRED strings; all three are `string | null` on
// the route, so this page was written to render a card the server can send with
// no title at all.
type LauncherApp = components["schemas"]["LauncherCard"];

// What icon this app declared. The frontend registry entry wins (it is the
// app's own declaration for a compiled-in app), then the backend launcher
// card's `icon`, which is what an EXTERNAL app with no registry entry supplies.
// Both are strings. This used to hold a second name -> lucide component table
// beside the registry's component field: two tables, two spellings, and a
// generic placeholder for anything in neither. AppGlyph resolves the string and
// falls back to a monogram of the title, which names the app instead of
// shrugging at it.
const iconFor = (app: { id: string; icon?: string | null }): string | undefined =>
  appIcon(app.id) ?? app.icon ?? undefined;

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
  const kind = card.status ? toneStatus(card.status.tone) : null;
  return (
    <div style={{ padding: "var(--space-5)", height: "100%" }}>
      <Row style={{ marginBottom: "var(--space-2)" }}>
        <AppGlyph icon={card.icon} name={card.title} size={22} />
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
      if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
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
        if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
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
        <Notice
          tone="error"
          where={`/api/base-apps/manifests/${appId}/ui`}
          detail={state.message}
          action={<Button variant="ghost" size="sm" onClick={() => void load()}>Retry</Button>}
        >
          Couldn't load the assistant setting.
        </Notice>
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
          <Notice
            tone="error"
            where="/api/projects/"
            detail={projects.message}
            action={<Button variant="ghost" size="sm" onClick={onRetry}>Retry</Button>}
          >
            Couldn't load projects.
          </Notice>
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
  const [services, setServices] = useState<ServiceProbe[] | null>(null);
  // Each installed app supplies its own card status (apps/registry). The
  // launcher no longer knows which apps exist or what their endpoints are.
  //
  // IT IS A SERVER READ, so it belongs on the query layer, and moving it fixed
  // a real staleness: it was a one-shot `useEffect` on mount, so a launcher tab
  // left open showed the status lines it happened to load hours earlier. On a
  // key it re-reads on focus and on reconnect with everything else, and the
  // fan-out underneath now goes through each app's own cache entry
  // (`appStatuses` -> `fetchQuery`), so opening this page just after leaving an
  // app costs no request at all for that app.
  //
  // No cadence of its own: nobody sits on the launcher watching a card. The
  // app-wide 30s staleness window plus focus is what this needs, and it is what
  // it gets by declaring neither.
  const statusQ = useQuery({
    queryKey: queryKeys.apps.launcherStatuses(),
    queryFn: appStatuses,
  });
  const statuses = statusQ.data ?? null;

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
      if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
      const j = (await r.json()) as components["schemas"]["LauncherCards"];
      setLauncher({ kind: "loaded", apps: j.apps ?? [] });
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
      if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
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
        if (!r.ok && r.status !== 409) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
        nav(`/projects/${slug}`);
      } catch (e) {
        setProjects({ kind: "error", message: e instanceof Error ? e.message : String(e) });
      } finally {
        setBridgeBusy(null);
      }
    },
    [nav, projects],
  );

  const loadServices = useCallback(async () => {
    try {
      const r = await fetch("/api/services/");
      const s = r.ok ? ((await r.json()) as ServicesResp) : { services: [] };
      setServices(s.services || []);
    } catch {
      setServices([]);
    }
  }, []);

  useEffect(() => {
    loadLauncher();
    loadServices();
  }, [loadLauncher, loadServices]);

  const findService = (needle: string) => services?.find((s) => s.name.includes(needle));

  // Live status for one launcher card, derived from the per-app probes above and
  // keyed by app id. Unknown ids get no status line (the card still launches).
  const statusFor = (app: LauncherApp): AppCard["status"] => {
    // A built-in app reports its own state through the registry.
    if (statuses === null) return { text: "loading…", tone: "muted" };
    const declared = statuses[app.id];
    if (declared) return declared;

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
          icon: iconFor(app),
          // The route may send no title and no tagline: all three of these are
          // `string | null` there, and were declared as required strings here.
          // A card with no name falls back to the app's id rather than to a
          // blank tile, which is what the old type made unrepresentable.
          title: app.title ?? app.id,
          tagline: app.tagline ?? "",
          to: app.external ? undefined : (app.route ?? undefined),
          href: app.external ? (app.href ?? undefined) : undefined,
          external: app.external ?? undefined,
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
