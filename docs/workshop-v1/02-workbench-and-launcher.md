# 02 — Workbench and Launcher

Status: **draft for operator review** · Date: 2026-06-14

Companion to [00-overview.md](00-overview.md). Covers the Project view (the
workbench), the builder page redesign, and the Apps page (the launcher) plus the
bridge between them. This is Phases 2 and 3.

---

## 1. Project view: from "working brain" to named sections

Retire "working brain" (opaque). The project's middle column becomes three clearly
named sections, each a real surface rather than a raw markdown textarea:

### Brief
The goal/constraints doc (backed by PROJECT.md). Rendered as formatted text with an
explicit Edit mode, not an always-open raw `.md` textarea. When empty, show a short
prompt ("Describe what this project is for") and a single action, not a forced blank
editor. The brief is reference, not a form you must fill before doing anything.

### Tasks
See §3. Structured, not raw markdown.

### Notes (was "Interventions")
Operator messages to the project/agent, shown as a timeline. Rename to "Notes"
(or "Operator notes"); "interventions" reads like a supervisor-block log and is
unclear. Keep append-only with timestamps; add a compose box.

## 2. Branches: there must be a main

Bug today: a freshly scaffolded project shows "no branches yet" and has no `main`,
because `git init` ran without an initial commit, so there is no trunk to branch
from. Fix:

- Project scaffold creates an initial commit on `main` (a `.gitkeep` or the scaffold
  files), so `main` always exists.
- The Branches section always shows `main` as the trunk ("Working on main"), with
  branches listed beneath it. "No branches yet" becomes "No side branches; working
  on main," which is a normal state, not an error-looking empty block.
- Each branch row keeps diff/merge/discard but condensed (a single actions menu, not
  three full-width buttons), aligned and equal-height.

## 3. Tasks: per-agent lists with a project roll-up

The operator's model, adopted: each agent has its **own** task list; the project's
general task list is the **roll-up** of agent tasks as a nested tree grouped by
agent. Done items hidden by default with a toggle to show them.

- Storage: structured (not raw `TODO.md` prose). A task has: text, status
  (`todo`/`doing`/`done`/`blocked`), owner (agent slug or "operator"), optional
  parent (for the tree), timestamps.
- Agent view (in chat, §5.7 of doc 01): just that agent's tasks.
- Project view: the tree, grouped by agent, operator tasks at top level. Toggle to
  reveal done. Tasks can be created by the operator or by agents.
- Migration: parse existing `TODO.md` checklists into the structured store once;
  keep a markdown export for git-friendliness.

## 4. Deployments (was "Surfaces")

Rename the "surfaces" card to **Deployments** and make it actionable, not a read-only
string. Three rows, each with state and an action:

- **Integrated**: route inside Workshop. Action: open / mount.
- **Standalone**: own origin/port. Action: open / start / show URL.
- **Public**: shared/marketplace. Action: publish / open public URL / unshare.

This is the workbench side of the App concept from the overview. A project becomes an
app the moment it has at least one deployment surface declared.

## 5. Builder page redesign

Today: a cramped 3-column grid (files | editor/preview/diff | chat) where everything
is too small, buttons are misaligned and different heights, there is a redundant
"pop chat," and "editor"/"preview" are unlabeled in purpose.

Goals: bigger, focusable panes; consistent controls; clear purpose for each pane.

### Layout
- Keep the three regions (Files, Center, Chat) but make the center and chat
  **focusable**: a layout control to expand any one region to full width (like an IDE
  focusing the editor or the terminal). Keep the existing "expand chat"; remove
  "pop chat."
- On narrow widths the regions stack (already the responsive behavior); make the
  stacked order Chat-first since chat is the primary driver.

### Center panes (label their purpose)
- **Editor**: the file editor for the file selected in Files. (Plan: CodeMirror for
  real editing; today's plain textarea is a placeholder.) Full height when focused.
- **Preview**: the live running app in an iframe (boots the dev server). Make the
  boot/stop/port controls one aligned row.
- **Diff**: working-tree diff with commit. One aligned action row.

### Controls
Every button in the builder page uses the shared Button primitive at one control
height. No mixed paddings, no text-styled links masquerading as buttons. Audit and
normalize the status bar, the pane tabs, and the preview/diff action rows.

## 6. Apps page: manage mode + bridge to workbench

Per [app-manager-ui.md](../app-manager-ui.md), add a **Manage** toggle to the Apps
page. Off = today's launcher grid. On = cards expand (one at a time) to reveal:

- A **totals strip** (storage, grants, scheduled jobs, agents, errors) across all
  apps, the thing no single app can show.
- Per app: status, capabilities/grants, attached agents (deep links), deployments,
  and an **"Open in workbench"** action.

### The bridge (overview §2, made concrete)
- "Open in workbench" on an app card ensures a **backing project exists**, creating
  it if the app has none yet (the built-in apps get their project lazily here), then
  navigates to the Project view.
- In the workbench, "Run"/"Deploy" creates or opens a Deployment, which then appears
  on the Apps page. Navigation works both directions from both places.

### Standalone view
A deployed app should be viewable **inside Workshop with the workshop toolbar around
it** (even if the app uses a non-default kit), and also reachable as a **pure
standalone** URL with no workshop chrome. Provide a "View standalone" link from the
integrated view. (<app> is the first real test of running a project-as-app inside
the launcher.)
