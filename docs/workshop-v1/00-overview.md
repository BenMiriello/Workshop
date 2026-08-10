# Workshop Platform — v1 Design Plan (Overview)

> **Superseded on 2026-07-18** by
> [`docs/platform-redesign/entity-model-2026-07-18.md`](../platform-redesign/entity-model-2026-07-18.md)
> (the canonical vocabulary and schema) and the phase docs that ride on top of
> it (autonomy-model, latitude-model, primitive-roles, chat-scope-design,
> chat-work-linkage, resource-access-interface-spec). This v1 doc set is kept
> for historical reference only. Do not update it; do not treat its
> assistant/builder axis as current — agents key off `role` now, not runtime.

Status: **superseded (historical reference)** · Date: 2026-06-14 · Author: the service user (supervisor)

This is the master plan for Workshop v1. It defines the conceptual model first
(what the nouns are and how they relate), then points at the four companion docs
that detail each area. It supersedes the scattered intuition in
[app-platform-plan.md](../app-platform-plan.md) and [app-manager-ui.md](../app-manager-ui.md)
by giving them one shared vocabulary; those two docs remain correct on isolation
and the manage-UI and are folded in by reference.

Companion docs:

- [01-agents-and-chat.md](01-agents-and-chat.md) — agent roles (assistant vs builder), permissions, the one shared chat component, capability-aware prompting.
- [02-workbench-and-launcher.md](02-workbench-and-launcher.md) — the Project workbench (builder page, brief/tasks/branches/deployments) and the Apps launcher (manage mode, link-to-project).
- [03-ui-kit-and-marketplace.md](03-ui-kit-and-marketplace.md) — the versionable UI kit, the app/kit marketplace (local-now, remote-later), fork vs branch, AI-assisted upgrades.
- [04-implementation-plan.md](04-implementation-plan.md) — staged build order, each stage mapped to a concrete fix, with acceptance criteria.
- [05-deployment-isolation-safety.md](05-deployment-isolation-safety.md) — the deployment/run model (worktrees + containerization), isolation/security between projects/apps, history/undo, remote-protection, anti-bloat.

---

## 1. The vision

A workshop where one person (then anyone) can **describe, build, run, use, and
distribute** their own software, with an agent harness doing the work a programmer
would otherwise do by hand. It runs locally and as a hosted web service. It started
as the operator's personal ops monorepo and stays that, while growing into a
general platform others can adopt.

The platform gives non-programmers the leverage programmers already have: version
control, environments, a component kit, a way to ship and share. The interface for
all of it is conversation plus a small set of direct-manipulation surfaces.

## 2. The core model: three linkable concepts (Project, App, Deployment)

The central confusion to resolve is "apps vs projects." The resolution
(operator-confirmed 2026-06-14): **they are distinct, independently existing
concepts that link when it makes sense. Neither requires the other.** A project does
not have to be an app; an app does not have to be a project. Do not collapse them
into one record, and do not force every app to have a backing project. Keep three
clean, loosely-coupled concepts:

### Project (the workbench: where you build)
A **Project** is a source artifact and its authoring context: the git repo plus the
working state around it (brief, tasks, branches, attached build agents, files). The
Project is the unit of version control and of forking. "Project" is the verb-side:
where work happens. A project may never become an app (a library, a data pipeline, a
scratch experiment).

### App (the launcher: where you run and share)
An **App** is a runnable, listable, installable unit: a manifest (UI entry points,
capabilities it provides and needs, sensitivity, kit) plus the deployable artifact.
An app **may** have a backing project (so you can open it in the workbench and edit
it), but it does not have to. Examples of app-without-project: a built-in app you
have not edited yet, an app installed from the marketplace that you only run, a
published artifact with no local source checked out.

### The link between them (optional, bidirectional)
- A project can be **promoted to an app** by declaring a manifest + a deployment.
- An app can be **opened in the workbench**, which links (or lazily creates) a
  backing project so you can edit its source.
- The link is a reference, not an identity. Unlinking an app from a project, or
  having an app with no project, are both valid states.

> This matches the operator's stance exactly: "merge or link yes, but projects do not
> strictly have to be apps, and apps do not need to be projects." So: link, do not
> fuse. The Apps page is the catalog (run/install/share); the Projects page is the
> workbench (build); "Open in workbench" / "Promote to app" are the two link actions.

### Deployment (the running instance)
A **Deployment** is a running instance of an app on a mount: `integrated` (a route
inside Workshop, inheriting the workshop shell), `standalone` (its own origin/port),
or `public` (shared, marketplace-reachable). One app can have several deployments at
once (Birds is integrated AND standalone today). This is a substantial technical
design in its own right (isolation, containerization vs worktrees, local-now and
remote-later); it has a dedicated doc:
[05-deployment-isolation-safety.md](05-deployment-isolation-safety.md). The existing
`surfaces` field is the seed; v1 makes it first-class and renames it **Deployments**
in the UI ("surfaces" is jargon).

### How the pages map to the model
- **Apps page** = the catalog/launcher. Consume side. Run an app, install one from
  the marketplace, manage deployments. "Manage" mode (per app-manager-ui.md) expands
  cards for cross-app totals and a link into the workbench.
- **Projects page** = the workbench. Build side. Brief, tasks, branches, files,
  build agents.
- **The bridge**: an "Edit" / "Open in workbench" action on an app card ensures a
  backing project exists (creating it if needed) and jumps to the Project view. A
  "Run" / "Deploy" action in the workbench creates or opens a Deployment and shows
  it on the Apps page.

## 3. Agents: two roles, one chat

Agents come in two **roles**, distinguished by capability, not by a name the user
invents:

- **Assistant** (today's "data agent"): persona-driven, attached to an app or a data
  domain, limited tools. Can answer, research, and perform **gated** data mutations
  (admin-configured per action: read-only / confirm / allow). Memory-backed (Letta).
  This is the in-app ChatDock.
- **Builder** (today's "build agent"): attached to a Project, full agentic toolset
  (Claude Code), works on branches. This is the workbench chat.

The chat **component is shared**; the role determines which capabilities and UI
affordances are enabled. Role is inferred from context (app context yields an
assistant, project context yields a builder), so the user never picks "data vs
build" as an abstract choice. Details in [01-agents-and-chat.md](01-agents-and-chat.md).

## 4. Building, versioning, forking

- **Primary mode: branches.** You work on an app/project in place via branches
  (git worktrees, already built): make a version, iterate, merge. This is the common
  path, handled under the hood.
- **Secondary mode: fork.** Forking copies someone else's project into your
  workspace (a real git fork), for when you want your own divergent line. Supported
  but not the default verb.
- **Marketplace.** Apps and UI kits publish to a shared registry. Local and web
  deployments both pull from it (the Roblox-style "run anyone's app here" idea).
  Installing = clone/fork the backing project, or run a hosted public deployment.

Detail in [03-ui-kit-and-marketplace.md](03-ui-kit-and-marketplace.md).

## 5. The UI kit is itself a versioned, shareable package

The design system (`src/ui` + tokens) becomes `@workshop/ui`, a semver-versioned
package that apps depend on by version. There is a default kit (matching the
built-in apps and the workshop shell); anyone can publish their own kit to the
marketplace. Upgrades are programmatic for compatible versions and **AI-assisted**
across breaking versions (a builder task fed the changelog). Detail in
[03-ui-kit-and-marketplace.md](03-ui-kit-and-marketplace.md).

## 6. Glossary (canonical terms; use these in UI and code)

| Term | Meaning | Replaces / clarifies |
|---|---|---|
| Project | Source artifact + authoring context (repo, brief, tasks, branches, build agents) | "project" (unchanged, sharpened) |
| App | A runnable/installable unit (manifest + artifact), optionally linked to a backing Project | "app" (distinct from Project; link is optional) |
| Deployment | A running instance of an app on a mount (integrated/standalone/public) | "surfaces" (renamed in UI) |
| Assistant | Persona agent, scoped, gated mutations, in-app chat | "data agent" |
| Builder | Full-tool agent on a Project, works on branches, workbench chat | "build agent" |
| Brief | The project's goal/constraints doc (rendered, editable) | "brief" / PROJECT.md (sharpened) |
| Tasks | Structured task tree, per-agent scoped, with a project roll-up | "todo" raw markdown |
| Notes | Operator messages to an agent/project (timeline) | "interventions" |
| Kit | A versioned UI component package an app depends on | (new) |
| Marketplace | Registry of publishable apps and kits | (new) |

"Working brain" is retired (opaque jargon). The project's middle column becomes
named sections: Brief, Tasks, Notes.

## 7. Non-goals / guardrails

- This is single-operator first. Do **not** add multi-tenant guardrails for the
  operator's own use (no allowlists/audit/mention-gating/rate-limiting). The
  capability-grant and assistant-mutation gating in this plan are **correctness
  boundaries for shared and marketplace apps**, not single-user cruft, and exist only
  on the sharing path (consistent with app-platform-plan.md's note).
- the excluded work org stays entirely out of scope.
- No new committed stubs; oversized tasks become `[~] BLOCKED` with sub-tasks.

## 8. Decisions (operator-confirmed 2026-06-14)

1. **Apps-vs-projects model**: **link, do not fuse.** Apps and projects are distinct
   and independently existing; neither requires the other; they link optionally and
   bidirectionally (§2). Deployment is its own concept and a real technical design
   (doc 05).
2. **Marketplace**: **build the full registry, but last.** Treat the current system as
   the **local dev version**: build a **local app + kit registry** now as part of the
   workbench, then migrate it to a remote/hosted registry once remote exists. No need
   for a remote registry yet (doc 03).
3. **Build-agent egress / safety**: do not just block. Design a real
   **security + permissions + isolation** model that lets a person do as much as
   possible *within their project*, with guardrails and fallbacks that catch local
   mistakes (worktrees, copies, history/undo), while hard-protecting the **remote**
   (personal or shared) from being destroyed, and keeping separation between
   people/apps/projects intact. Auto-merge/manage so copies do not bloat indefinitely.
   Full design in doc 05.

### Build process (operator directive)
For every phase, implement with **opus subagents working from a complete plan**, then
**review, critique, fix, and refine** the output. Design is synthesized first (by the
supervisor, with full context) so the plan is complete before code; implementation
fans out to subagents; a review pass follows each wave.

## 9. Phasing (see 04 for the task-level plan)

Staged by what is clearest and most valuable first (operator: "in stages based on
what's most defined/clear/important").

- **Phase 1 — Chat, done right.** The shared chat component rebuilt: model picker,
  mobile, auto-naming, confirm-delete, aligned controls, scoped agent picker,
  capability-aware prompting, integrated task list, simplified floating button. Top
  complaint, and independent of the model/deployment decisions. Clearest, do first.
- **Phase 2 — Workbench.** Project view reworked (Brief/Tasks/Notes, structured task
  tree, real main branch, Deployments card), builder page redesigned (full-size
  panes, aligned controls, focus mode). Includes the task store.
- **Phase 3 — Launcher bridge.** Apps page manage mode + "Open in workbench" /
  "Promote to app" link actions; deployment links both directions.
- **Phase 4 — Deployment + isolation + safety (doc 05).** The run/deploy model
  (worktrees + containerization), per-project isolation, history/undo, remote
  protection, anti-bloat. The "complex technical design" the operator flagged.
- **Phase 5 — Platform seams.** `@workshop/ui` as a versioned package; single-source
  tokens; app manifest + `kit` field + capability grants (from app-platform-plan.md).
- **Phase 6 — Local registry, then marketplace.** Local app + kit registry as the
  dev version (publish/install/fork locally; AI-assisted kit migration), then migrate
  to a remote/hosted registry once remote exists. Last.
