import { useCallback, useEffect, useState, type ComponentType } from "react";
import { Link } from "react-router-dom";
import {
  Bot,
  FolderGit2,
  MessageSquare,
  MessagesSquare,
  ListChecks,
  LayoutGrid,
  Banknote,
  PiggyBank,
  TrainFront,
  Watch,
  Bird,
  MapPin,
  AlertTriangle,
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
  Tag,
  catFor,
  type StatusKind,
} from "../ui";

// Home — the operational hub (the platform root, "/"). Calm and scannable: a
// strip for anything needing attention (a Job awaiting input), then recent
// chats, recent Jobs, and pinned apps + projects. Each section reads its own
// endpoint and ships all four list states. Deep views live behind each section's
// "view all" link (Chats, Agents/Jobs, Apps, Projects).

// ---- Jobs -----------------------------------------------------------------
type Job = {
  id: string;
  agent_id?: string;
  project_slug?: string;
  chat_id?: string;
  trigger?: string;
  status?: string;
  started_at?: string;
  ended_at?: string;
};

const JOB_STATUS_KIND: Record<string, StatusKind> = {
  running: "running",
  done: "ok",
  queued: "stopped",
  cancelled: "stopped",
  awaiting_input: "warn",
  failed: "danger",
};

const jobStatusKind = (status?: string): StatusKind => JOB_STATUS_KIND[status ?? ""] ?? "stopped";

function jobBinding(job: Job): { icon: JSX.Element; text: string } {
  if (job.project_slug) return { icon: <FolderGit2 aria-hidden />, text: job.project_slug };
  if (job.agent_id) return { icon: <Bot aria-hidden />, text: job.agent_id };
  if (job.chat_id) return { icon: <MessageSquare aria-hidden />, text: job.chat_id };
  return { icon: <Bot aria-hidden />, text: "unbound" };
}

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
    <section style={{ marginBottom: "var(--space-7)" }}>
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

// ---- Attention strip ------------------------------------------------------
// Any Job in `awaiting_input` — the one status that wants the operator now.
function AttentionStrip() {
  const [jobs, setJobs] = useState<Job[] | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const r = await fetch("/api/jobs?status=awaiting_input&limit=20");
      if (!r.ok) throw new Error(`${r.status}`);
      const j = await r.json();
      setJobs((j.jobs as Job[]) ?? []);
      setErr(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 30_000);
    return () => clearInterval(t);
  }, [load]);

  // Self-hiding: no attention items and no error means nothing to show.
  if (err || !jobs || jobs.length === 0) return null;

  return (
    <section style={{ marginBottom: "var(--space-7)" }}>
      <SectionLabel icon={<AlertTriangle aria-hidden />}>Needs attention</SectionLabel>
      <CardGrid>
        {jobs.map((job) => {
          const bind = jobBinding(job);
          return (
            <li key={job.id}>
              <Link to="/agents" style={{ textDecoration: "none", color: "inherit" }}>
                <Card interactive>
                  <Row between wrap style={{ gap: "var(--space-2)" }}>
                    <Row style={{ gap: "var(--space-1)", alignItems: "center", minWidth: 0 }}>
                      {bind.icon}
                      <span className="ws-mono" style={{ overflow: "hidden", textOverflow: "ellipsis" }}>{bind.text}</span>
                    </Row>
                    <StatusPill status="warn">awaiting input</StatusPill>
                  </Row>
                  {job.trigger && (
                    <p className="ws-card__meta" style={{ marginTop: "var(--space-2)" }}>
                      <Tag hue={catFor(job.trigger)}>{job.trigger}</Tag>
                    </p>
                  )}
                </Card>
              </Link>
            </li>
          );
        })}
      </CardGrid>
    </section>
  );
}

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
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      const list = (await r.json()) as ThreadSummary[];
      setThreads(Array.isArray(list) ? list.slice(0, 6) : []);
      setErr(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  return (
    <Section icon={MessagesSquare} title="Recent chats" viewAll={{ to: "/chats" }}>
      {err ? (
        <StateBlock variant="error" title="Couldn't load chats" hint={<><code>/api/chat/threads</code> — {err}</>} />
      ) : !threads ? (
        <CardSkeletons />
      ) : threads.length === 0 ? (
        <StateBlock title="No chats yet" hint={<>Start one from <Link to="/chats">Chats</Link>.</>} />
      ) : (
        <CardGrid>
          {threads.map((t) => (
            <li key={t.id}>
              <Link
                to={`/chat/${encodeURIComponent(t.context_key)}?thread=${encodeURIComponent(t.id)}`}
                style={{ textDecoration: "none", color: "inherit" }}
              >
                <Card interactive>
                  <Row between style={{ gap: "var(--space-2)" }}>
                    <h3 className="ws-card__title" style={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      <MessageSquare size={14} strokeWidth={1.8} aria-hidden style={{ verticalAlign: "middle", marginRight: "0.4rem" }} />
                      {t.title || "Untitled chat"}
                    </h3>
                    {t.updated_at && <span className="ws-card__meta" style={{ flex: "0 0 auto" }}>{fmtAge(t.updated_at)}</span>}
                  </Row>
                  <p className="ws-card__meta ws-mono" style={{ marginTop: "var(--space-1)" }}>{t.context_key}</p>
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
function RecentJobs() {
  const [jobs, setJobs] = useState<Job[] | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const r = await fetch("/api/jobs?limit=6");
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      const j = await r.json();
      setJobs((j.jobs as Job[]) ?? []);
      setErr(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 30_000);
    return () => clearInterval(t);
  }, [load]);

  return (
    <Section icon={ListChecks} title="Recent jobs" viewAll={{ to: "/agents" }}>
      {err ? (
        <StateBlock variant="error" title="Couldn't load jobs" hint={<><code>/api/jobs</code> — {err}</>} />
      ) : !jobs ? (
        <CardSkeletons />
      ) : jobs.length === 0 ? (
        <StateBlock
          title="No jobs yet"
          hint={<>A Job is recorded for every bounded run — cron sweep, project sweep, or a manual run.</>}
        />
      ) : (
        <CardGrid>
          {jobs.map((job) => {
            const bind = jobBinding(job);
            return (
              <li key={job.id}>
                <Card>
                  <Stack gap="sm">
                    <Row between wrap style={{ gap: "var(--space-2)" }}>
                      <Row style={{ gap: "var(--space-1)", alignItems: "center", minWidth: 0 }}>
                        {bind.icon}
                        <span className="ws-mono" style={{ overflow: "hidden", textOverflow: "ellipsis" }}>{bind.text}</span>
                      </Row>
                      <StatusPill status={jobStatusKind(job.status)}>{job.status ?? "unknown"}</StatusPill>
                    </Row>
                    <Row wrap style={{ gap: "var(--space-2)", alignItems: "center" }}>
                      {job.trigger && <Tag hue={catFor(job.trigger)}>{job.trigger}</Tag>}
                      {job.started_at && <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>{fmtAge(job.started_at)}</span>}
                    </Row>
                  </Stack>
                </Card>
              </li>
            );
          })}
        </CardGrid>
      )}
    </Section>
  );
}

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
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
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
        <StateBlock variant="error" title="Couldn't load apps" hint={<><code>/api/base-apps/launcher</code> — {err}</>} />
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
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
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
        <StateBlock variant="error" title="Couldn't load projects" hint={<><code>/api/projects/</code> — {err}</>} />
      ) : !projects ? (
        <CardSkeletons />
      ) : projects.length === 0 ? (
        <StateBlock title="No projects yet" hint={<>Start one with <Link to="/projects/new">+ New project</Link>.</>} />
      ) : (
        <CardGrid>
          {projects.map((p) => (
            <li key={p.slug}>
              <Link to={`/projects/${p.slug}`} style={{ textDecoration: "none", color: "inherit" }}>
                <Card interactive>
                  <Row between>
                    <h3 className="ws-card__title">
                      <FolderGit2 size={16} strokeWidth={1.8} aria-hidden style={{ verticalAlign: "middle", marginRight: "0.4rem" }} />
                      {p.title}
                    </h3>
                    {p.state && <StatusPill status={projectStateKind(p.state)}>{p.state}</StatusPill>}
                  </Row>
                  {p.purpose && <p style={{ marginTop: "var(--space-2)" }}>{p.purpose}</p>}
                  {p.last_touched && (
                    <p className="ws-card__meta" style={{ marginTop: "var(--space-2)" }}>touched {fmtAge(p.last_touched)}</p>
                  )}
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
  return (
    <div>
      <PageHeader title="Home" />
      <AttentionStrip />
      <RecentChats />
      <RecentJobs />
      <PinnedApps />
      <RecentProjects />
    </div>
  );
}
