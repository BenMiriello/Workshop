import { useCallback, useEffect, useState, type ComponentType } from "react";
import { Link } from "react-router-dom";
import {
  FolderGit2,
  MessageSquare,
  MessagesSquare,
  LayoutGrid,
  Banknote,
  PiggyBank,
  TrainFront,
  Watch,
  Bird,
  MapPin,
  type LucideProps,
} from "lucide-react";
import {
  Card,
  CardGrid,
  PageHeader,
  Row,
  Stack,
  SectionLabel,
  Skeleton,
  StateBlock,
  StatusPill,
  type StatusKind,
} from "@workshop/ui";
import { shortHttpError } from "../lib/httpError";

// Home — three bands per operator direction 2026-07-18: Apps + Chats +
// Projects, each capped to the four most-recently-active items. No
// attention band, no jobs feed, no status pills. The signal is "what am I
// working on or near recently"; system status lives elsewhere (the /agents
// header carries the autonomy pill; Work carries the ticket queue).

function fmtAge(iso?: string): string {
  if (!iso) return "";
  const s = Math.max(0, Math.round((Date.now() - Date.parse(iso)) / 1000));
  if (Number.isNaN(s)) return "";
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}

// ---- Section shell --------------------------------------------------------
function Section({
  icon: Icon,
  title,
  viewAll,
  children,
}: {
  icon: ComponentType<LucideProps>;
  title: string;
  viewAll?: { to: string; label?: string };
  children: React.ReactNode;
}) {
  return (
    <section style={{ marginBottom: "var(--space-6)" }}>
      <Row between style={{ marginBottom: "var(--space-3)", alignItems: "baseline" }}>
        <SectionLabel icon={<Icon aria-hidden />}>{title}</SectionLabel>
        {viewAll && (
          <Link to={viewAll.to} className="ws-muted" style={{ fontSize: "var(--fs-xs)", color: "var(--accent)" }}>
            {viewAll.label ?? "View all"} ↗
          </Link>
        )}
      </Row>
      {children}
    </section>
  );
}

function CardSkeletons({ n = 3 }: { n?: number }) {
  return (
    <CardGrid>
      {Array.from({ length: n }, (_, i) => (
        <li key={i}>
          <Card>
            <Stack gap="sm">
              <Skeleton width="55%" />
              <Skeleton width="35%" />
            </Stack>
          </Card>
        </li>
      ))}
    </CardGrid>
  );
}

// AttentionStrip retired in C2 per operator direction 2026-07-18: Home stays
// a "where I was near recently" surface. Blocked / awaiting jobs surface via
// the ticket queue in Work; the /agents header pill carries autonomy state.

// ---- Recent chats ---------------------------------------------------------
type ThreadSummary = {
  id: string;
  context_key: string;
  title: string;
  updated_at?: string;
  message_count?: number;
};

function RecentChats() {
  const [threads, setThreads] = useState<ThreadSummary[] | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const r = await fetch("/api/chat/threads");
      if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
      const list = (await r.json()) as ThreadSummary[];
      setThreads(Array.isArray(list) ? list.slice(0, 4) : []);
      setErr(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  return (
    <Section icon={MessagesSquare} title="Recent chats" viewAll={{ to: "/chats" }}>
      {err ? (
        <StateBlock variant="error" title="Couldn't load chats" hint={<><code>/api/chat/threads</code> ({err})</>} />
      ) : !threads ? (
        <CardSkeletons />
      ) : threads.length === 0 ? (
        <StateBlock title="No chats yet" hint={<>Start one from <Link to="/chats">Chats</Link>.</>} />
      ) : (
        <CardGrid className="ws-card-grid--dense">
          {threads.map((t) => (
            <li key={t.id}>
              <Link
                to={`/chat/${encodeURIComponent(t.context_key)}?thread=${encodeURIComponent(t.id)}`}
                style={{ textDecoration: "none", color: "inherit" }}
              >
                <Card interactive compact style={{ display: "flex", flexDirection: "column", height: "100%" }}>
                  <Row between style={{ gap: "var(--space-2)" }}>
                    <h3 className="ws-card__title" style={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontSize: "var(--fs-base)" }}>
                      <MessageSquare size={14} strokeWidth={1.8} aria-hidden style={{ verticalAlign: "middle", marginRight: "0.4rem" }} />
                      {t.title || "Untitled chat"}
                    </h3>
                    <span className="ws-card__meta ws-mono" style={{ flex: "0 0 auto", whiteSpace: "nowrap" }}>{t.updated_at ? fmtAge(t.updated_at) : " "}</span>
                  </Row>
                  <p className="ws-card__meta ws-mono" style={{ marginTop: "var(--space-1)", minWidth: 0, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{t.context_key}</p>
                </Card>
              </Link>
            </li>
          ))}
        </CardGrid>
      )}
    </Section>
  );
}

// ---- Recent Jobs ----------------------------------------------------------
// RecentJobs retired in C2. Runs surface via Work > Runs; Home stays as
// three bands (Apps + Chats + Projects) per operator direction 2026-07-18.

// ---- Pinned / recent Apps -------------------------------------------------
type LauncherApp = { id: string; title: string; tagline: string; icon: string; route?: string; href?: string; external?: boolean };

const APP_ICONS: Record<string, ComponentType<LucideProps>> = {
  Banknote, PiggyBank, TrainFront, Watch, Bird, MapPin,
};
const appIcon = (name: string): ComponentType<LucideProps> => APP_ICONS[name] ?? LayoutGrid;

function PinnedApps() {
  const [apps, setApps] = useState<LauncherApp[] | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const r = await fetch("/api/base-apps/launcher");
      if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
      const j = (await r.json()) as { apps?: LauncherApp[] };
      setApps((j.apps ?? []).slice(0, 4));
      setErr(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  return (
    <Section icon={LayoutGrid} title="Apps" viewAll={{ to: "/apps" }}>
      {err ? (
        <StateBlock variant="error" title="Couldn't load apps" hint={<><code>/api/base-apps/launcher</code> ({err})</>} />
      ) : !apps ? (
        <CardSkeletons />
      ) : apps.length === 0 ? (
        <StateBlock title="No apps installed" hint={<>Install one, or build one from <Link to="/projects/new">Projects</Link>.</>} />
      ) : (
        <CardGrid>
          {apps.map((app) => {
            const Icon = appIcon(app.icon);
            const inner = (
              <Card interactive pad0>
                <div style={{ padding: "var(--space-5)" }}>
                  <Row style={{ marginBottom: "var(--space-2)" }}>
                    <Icon size={22} strokeWidth={1.8} aria-hidden style={{ flexShrink: 0 }} />
                    <h3 className="ws-card__title">{app.title}</h3>
                  </Row>
                  <p className="ws-card__meta">{app.tagline}</p>
                </div>
              </Card>
            );
            return (
              <li key={app.id}>
                {app.external ? (
                  <a href={app.href} rel="noopener noreferrer" style={{ display: "block", color: "inherit", textDecoration: "none" }}>{inner}</a>
                ) : (
                  <Link to={app.route ?? "/apps"} style={{ display: "block", color: "inherit", textDecoration: "none" }}>{inner}</Link>
                )}
              </li>
            );
          })}
        </CardGrid>
      )}
    </Section>
  );
}

// ---- Recent projects ------------------------------------------------------
type ProjectLite = { slug: string; title: string; purpose?: string; state?: string; last_touched?: string; sensitivity?: string };

const projectStateKind = (s?: string): StatusKind => {
  switch (s) {
    case "wip": return "running";
    case "sketch": return "warn";
    case "mounted":
    case "shared":
    case "published": return "ok";
    case "parked":
    case "retired": return "stopped";
    default: return "warn";
  }
};

function RecentProjects() {
  const [projects, setProjects] = useState<ProjectLite[] | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const r = await fetch("/api/projects/");
      if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
      const list = (await r.json()) as ProjectLite[];
      const sorted = [...list].sort((a, b) =>
        Date.parse(b.last_touched ?? "") - Date.parse(a.last_touched ?? ""),
      );
      setProjects(sorted.slice(0, 4));
      setErr(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  return (
    <Section icon={FolderGit2} title="Projects" viewAll={{ to: "/projects" }}>
      {err ? (
        <StateBlock variant="error" title="Couldn't load projects" hint={<><code>/api/projects/</code> ({err})</>} />
      ) : !projects ? (
        <CardSkeletons />
      ) : projects.length === 0 ? (
        <StateBlock title="No projects yet" hint={<>Start one with <Link to="/projects/new">+ New project</Link>.</>} />
      ) : (
        <CardGrid className="ws-projects-grid">
          {projects.map((p) => (
            <li key={p.slug}>
              <Link to={`/projects/${p.slug}`} style={{ textDecoration: "none", color: "inherit" }}>
                <Card interactive className="ws-project-card">
                  <Row between>
                    <h3 className="ws-card__title">
                      <FolderGit2 size={16} strokeWidth={1.8} aria-hidden style={{ verticalAlign: "middle", marginRight: "0.4rem" }} />
                      {p.title}
                    </h3>
                    <StatusPill status={p.state ? projectStateKind(p.state) : "stopped"}>{p.state || "unknown"}</StatusPill>
                  </Row>
                  <p className="ws-project-card__desc" style={{ marginTop: "var(--space-2)" }}>{p.purpose || " "}</p>
                  <p className="ws-card__meta ws-project-card__when">
                    {p.last_touched ? `touched ${fmtAge(p.last_touched)}` : " "}
                  </p>
                </Card>
              </Link>
            </li>
          ))}
        </CardGrid>
      )}
    </Section>
  );
}

export default function Home() {
  // Home per operator direction 2026-07-18: three bands (Apps, Chats,
  // Projects), each capped to the four most-recently-active items. No
  // attention strip, no jobs feed, no autonomy pill, no budget bar. The
  // signal is "what am I working on or near recently"; system status lives
  // elsewhere (the /agents header shows autonomy state, Work shows the
  // ticket queue, JobDetail shows run receipts).
  return (
    <div>
      <PageHeader title="Home" />
      <PinnedApps />
      <RecentChats />
      <RecentProjects />
    </div>
  );
}
