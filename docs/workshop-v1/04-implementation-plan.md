# 04 — Implementation Plan (staged)

Status: **draft for operator review** · Date: 2026-06-14

Companion to [00-overview.md](00-overview.md). Concrete, staged build order. Every
item traces to a specific operator complaint or a model decision. Each stage leaves
the system working and is independently shippable. Files listed are the known touch
points from the codebase map.

Conventions: each item has an acceptance check. UI items must pass
`bash scripts/check_design.sh` and `cd frontend && npx tsc --noEmit`. No new stubs.

---

## Phase 1 — Chat, done right (top priority)

Shared component: `frontend/src/components/Chat.tsx`, `ChatDock.tsx`,
`frontend/src/lib/chat.ts`, `frontend/src/styles.css` (chat rules ~470-636),
`frontend/src/styles/components.css` (`.ws-dock`); backend
`backend/routes/chat.py`, `backend/services/chat_context.py`,
`backend/services/llm_router.py`.

1. **Aligned composer.** Send/stop/expand are equal-height icon buttons on one
   control-height token; textarea grows; no padding drift. Remove "pop chat," keep
   "expand chat." *Accept:* all composer controls same height at 360px and desktop.
2. **Mobile.** Textarea `font-size: 16px` (kill iOS zoom); dock bottom-sheet keeps
   composer above keyboard and in safe area. *Accept:* no zoom-on-focus on iOS; FAB
   and composer fully visible at 360px.
3. **Floating FAB.** Small circular message-bubble button, no agent name, respects
   `env(safe-area-inset-bottom)`. *Accept:* on-screen on mobile, not clipped low.
4. **Model picker.** Inline composer control (Opus 4.8 / Sonnet 4.6 / Haiku 4.5),
   default per role, remembered per thread; backend `llm_router` honors it. *Accept:*
   switching model changes the model used for the next turn.
5. **Auto-naming.** Generate a short title after the first exchange (cheap model
   call), editable; stop using first-N-chars. *Accept:* new threads get a meaningful
   title within one turn.
6. **Confirm delete.** Thread delete uses `ConfirmDialog` (danger). *Accept:* no
   immediate destructive delete.
7. **Scoped picker.** In-app chat lists only that app's assistant(s); cross-context
   list stays on the Agents page. *Accept:* an app dock does not list unrelated
   builders.
8. **Capability-aware prompt (layered).** Role preamble states exact tools available
   and forbids promising actions it cannot take; app/project context next; user
   custom prompt last. *Accept:* a tools-off assistant no longer claims it will
   perform actions; a builder states its real toolset + egress limits.
9. **Egress surfacing.** Builder preamble includes the box egress facts; builder page
   shows a one-line banner on egress failure instead of raw stderr. *Accept:* a
   blocked `git clone` yields a clear message, not a silent/opaque failure.
10. **Per-agent task list in chat.** Collapsible task panel in the chat; tasks
    created manually or by the agent (backend task store, doc 02 §3). *Accept:* an
    agent shows and updates its own task list in-chat.

## Phase 2 — Workbench

`frontend/src/pages/ProjectDetail.tsx`, `AgentBuild.tsx`,
`backend/routes/projects.py`, `backend/services/worktrees.py`,
`backend/services/build_agents.py`.

11. **Main branch on scaffold.** Project creation makes an initial commit on `main`.
    *Accept:* a new project shows `main` as trunk; worktree creation succeeds with no
    prior manual commit.
12. **Branches UI.** Show `main` as trunk; "no side branches; working on main" empty
    copy; per-branch actions condensed into one aligned menu. *Accept:* no scary empty
    block; aligned controls.
13. **Brief.** Rendered doc with explicit Edit mode; empty state is a prompt + one
    action, not a forced raw textarea. *Accept:* opening a project does not dump you
    into a blank `.md` editor.
14. **Tasks (structured).** Replace raw `TODO.md` editing with the structured task
    tree (per-agent + project roll-up, done-toggle). One-time migration from existing
    `TODO.md`; keep a markdown export. *Accept:* tasks render as a per-agent tree with
    a done toggle; agents and operator can add tasks.
15. **Notes.** Rename "Interventions" to "Notes," add a compose box, timeline view.
    *Accept:* clear label; operator can post a note.
16. **Section rename.** Retire "working brain"; sections are Brief / Tasks / Notes.
    *Accept:* no "working brain" string in the UI.
17. **Builder page redesign.** Focusable panes (expand any region full-width); label
    Editor/Preview/Diff purposes; normalize every control to the Button primitive at
    one height; stacked order is chat-first on mobile. *Accept:* editor and chat can
    each go full-size; no mismatched button heights; no "pop chat."
18. **Deployments card.** Rename "surfaces" to "Deployments"; three actionable rows
    (integrated/standalone/public). *Accept:* each row has state + an action.

## Phase 3 — Launcher bridge

`frontend/src/pages/Apps.tsx`, `Projects.tsx`, `ProjectDetail.tsx`;
backend `backend/routes/base_apps.py`, `projects.py`, a new app-manifest read.

19. **Apps Manage mode.** Edit toggle expands cards (one at a time) with a totals
    strip + per-app details (status, grants, agents, deployments). *Accept:* toggle
    expands a card with details and totals.
20. **Open in workbench.** App card action ensures a backing project exists (creates
    it if missing, including for built-in apps) and navigates to it. *Accept:* an app
    with no project gets one and opens; an app with one opens it.
21. **Run/Deploy bridge.** Workbench "Run/Deploy" creates/opens a Deployment shown on
    the Apps page; navigation works both directions. *Accept:* deploying from the
    workbench surfaces the app in the launcher and links back.
22. **Standalone view.** Integrated app view keeps the workshop toolbar around any
    app (even non-default kit) and offers a "View standalone" link to chrome-free URL.
    *Accept:* <app> runs inside the launcher with the toolbar and has a standalone
    link.

## Phase 4 — Deployment, isolation, safety (doc 05; the safety floor)

Backend git service (`backend/services/worktrees.py` + new deployment/runner svc),
the the bridge agent egress hook (`~/.claude/hooks/egress-guard.py`), per-project manifest.
Land the **05a safety substrate** early (it is cheap and protects all later work).

23. **Checkpoint substrate (05a).** Builders auto-commit checkpoints per turn;
    snapshot (tag/reflog anchor) before merges/destructive ops/deploys. *Accept:* a
    branch retains a recoverable timeline; a hard reset is recoverable via reflog/tag.
24. **Worktree blast-radius guardrails (05a).** Destructive ops auto-anchor first and
    are blocked when they would escape the worktree (other projects, platform root,
    remote). *Accept:* an `rm -rf` outside the worktree is refused; inside it is
    reversible.
25. **Remote protection (05a).** Remote ops sit behind the platform; no force-push /
    history-rewrite / branch-or-remote deletion to protected refs from an agent.
    *Accept:* an agent cannot force-push or delete the remote; publish goes via merge.
26. **Per-project egress approve-list (05a).** Manifest declares needed hosts;
    operator approves per project; the hook reads the list; default dev allowlist for
    the operator's own projects. *Accept:* an approved host clones; an unapproved host
    is blocked with a clear message.
27. **LocalRunner + manifest (05b).** Deployment runs an app as a scoped subprocess
    (own cwd, scoped env, port) from the manifest; lifecycle (start/stop/logs).
    *Accept:* a project with a manifest deploys integrated/standalone locally.
28. **Anti-bloat GC (05b).** Reap merged/abandoned worktrees; thin old checkpoints
    keeping tagged snapshots; reap stopped runtimes. *Accept:* a periodic pass reduces
    stale worktrees/checkpoints without losing tagged snapshots.

## Phase 5 — Platform seams

`frontend/` workspace, `vite.config.ts`, `tsconfig.json`, new package dir;
manifest + grants per app-platform-plan.md.

29. **`@workshop/ui` package.** Promote `src/ui` + tokens to a real versioned
    workspace package; apps import it by package, not path alias. *Accept:* package
    builds; frontend consumes it; `check_design.sh` still passes.
30. **Single-source tokens.** Emit CSS vars and the mobile JS object from one token
    source. *Accept:* changing a token updates both web and mobile without hand-sync.
31. **Manifest + `kit` field + grants.** App manifest with `kit`, `provides`, `needs`,
    `sensitivity`, plus runtime fields (doc 05 §2); capability grants (`grants.json`,
    operator-only write) wired into backend authz. *Accept:* an app declares a kit
    version + capabilities; cross-app access is default-deny.

## Phase 6 — Local registry, then remote (full marketplace, built last)

32. **Registry interface + local impl.** A registry interface with a **local**
    implementation: publish a release, list, install, fork-into-workspace, update via
    branch-merge. *Accept:* an app/kit publishes and installs locally end to end.
33. **AI-assisted kit migration.** Builder task migrates an app across a breaking kit
    major using the kit changelog; surfaces non-automatable changes. *Accept:* a major
    bump produces a migrated, building app or a clear list of manual steps.
34. **Remote registry migration (later).** Point publish/install/fork at a remote
    registry once remote hosting exists. *Accept:* same flows, remote backend.

---

## Build process (operator directive)

Each phase is implemented by **opus subagents working from the relevant doc section
as a complete spec**, fanned out per workstream, followed by a **review/critique/fix**
pass (the work-reviewer gate plus a supervisor refine). No TODO item is marked done
until the review gate approves (per workshop CLAUDE.md). Frontend items must pass
`scripts/check_design.sh` and `tsc --noEmit`.

## Suggested execution order within Phase 1

Do the chat in this order so each step is verifiable in isolation: 1 (aligned
composer), 3 (FAB), 2 (mobile) are pure frontend/CSS; then 6 (confirm) and 7 (scoped
picker); then 8/9 (prompting + egress) backend; then 4 (model picker) and 5
(auto-naming); then 10 (task list). The **task store** (Phase 2 §14) is the one
cross-phase dependency; build it first if landing item 10 early.
