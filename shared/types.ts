// Per ADR-008 there are two kinds of Agent: Data (Letta-backed, today's
// fleet) and Build (Claude Code coding session scoped to a cwd). They share
// the Agents top-nav tab; the detail UI differs per kind.
export type AgentKind = "data" | "build";

export type Agent = {
  id: string;
  name: string;
  kind?: AgentKind;
  created_at?: string;
  // Last touched (Letta updated_at, falling back to created_at). Drives the
  // Agents-tab recency sort + the per-card activity timestamp.
  last_active?: string;
  memory_blocks: string[];
};

export type BuildAgentState = "idle" | "running" | "parked" | "retired";

export type BuildAgent = {
  slug: string;
  name: string;
  purpose: string;
  kind: "build";
  cwd: string;
  project_slug: string | null;
  // Optional pin to a worktree-backed branch under
  // ~/workshop/branches/<project>/<branch>/ (ADR-009 section 7). When set
  // together with project_slug, cwd resolves to that worktree instead of the
  // project's main checkout.
  branch_slug: string | null;
  state: BuildAgentState;
  created: string;
  last_active: string;
  claude_session: string | null;
  // Real check (detail endpoint) that the CLI session file still exists on disk,
  // not merely "a session id string is stored". Undefined on the roster payload.
  session_alive?: boolean;
};

// A worktree-pinned development branch off a project's main, as returned by
// /api/worktrees/<project>. The git ref name is e.g. "wt/widgets/refactor-css".
export type WorktreeBranch = {
  project_slug: string;
  branch_slug: string;
  branch: string;       // full git ref name, e.g. "wt/widgets/refactor-css"
  dir: string;
  head?: string;
  created?: string;
};

// Operator-built Project: standalone-or-integrated, with a working brain,
// surfaces, and a persistent vibe-coding session. Lives at /projects.
export type ProjectState =
  | "sketch"
  | "wip"
  | "mounted"
  | "shared"
  | "published"
  | "parked"
  | "retired";

export type ProjectSensitivity = "private" | "shareable" | "public";

export type ProjectSurfaces = {
  integrated: string | null;
  standalone: { port: number; tsServeMount?: string } | null;
  public: string | null;
};

// A project's GitHub binding (Phase G1). Absent until the project is connected
// to a remote via POST /api/projects/{slug}/connect-github. Owner is always the
// operator's own account; never an owner listed in WORKSHOP_BLOCKED_REPO_OWNERS.
export type ProjectRepo = {
  provider: string;
  owner: string;
  name: string;
  remote_url: string;
  default_branch: string;
  connected_at: string;
};

// The structured spec a `task-proposal` ui-card composes into a filed task's
// body (see frontend UiCard.tsx / uiCardActions.ts). The distiller (backend
// chat_context.py TASK_PROPOSAL_CARD) fills these so a worker can act on the
// ticket text alone. All fields optional and backward-compatible: a proposal
// carrying only a title (or a plain `text` body) still files fine.
export type TaskEffort = "quick" | "standard" | "deep";

export type TaskProposalSpec = {
  scope?: string;
  title?: string;
  // The description a worker acts on. Its sections (Objective / Output / Start
  // from / Boundaries) are labelled lines inside it, not separate fields: a
  // ticket is a title and a description, and every other field is edited on the
  // ticket itself rather than a second time on the card.
  description?: string;
  // Legacy, still read so proposals stored in older transcripts keep filing:
  // one field per section, plus priority and effort, which now live on the
  // ticket page (TicketDetail's Details rail).
  objective?: string;
  output?: string;
  entry_points?: string;
  boundaries?: string;
  priority?: number;
  effort?: TaskEffort;
};

export type Project = {
  slug: string;
  title: string;
  purpose: string;
  dir: string;
  sensitivity: ProjectSensitivity;
  state: ProjectState;
  surfaces: ProjectSurfaces;
  needs: string[];
  provides: string[];
  created: string;
  last_touched: string;
  claude_session: string | null;
  repo?: ProjectRepo;
  concurrent_worktrees?: boolean;
  // Resident agent slugs. A new project bootstraps with `[]`, on purpose: the
  // project loop is not an agent, so nothing needs to live here for the project
  // to be worked. Adopting a template group populates it.
  agents?: string[];
};
