# Workshop App Manager — in-place app editor + governance surface

Status: **WIP proposal / for operator review** · Date: 2026-06-13 · Author: clawd-a (supervisor)

A concept for editing and managing apps from two zoom levels: a platform-wide editor
on the Apps page (compare apps side by side, see totals), and the same knobs scoped to
one app from inside it. Nothing here is built. This is the shape to review before we
commit, and it is meant to be **general purpose**: every knob comes from a per-app
manifest, so any app gets the editor for free without app-specific UI code.

This builds directly on [app-platform-plan.md](app-platform-plan.md) (apps.json manifest,
capability grants, isolation). The App Manager is the **UI over that manifest plus
runtime/resource data**. It does not invent a second source of truth.

---

## 1. The core idea

One source of truth (the manifest + live runtime stats), rendered at two zoom levels:

- **Outside view (Apps page, "Manage" mode).** Each app card stays where it is. Toggling
  Manage mode lets a card expand: a drawer slides down from behind the card, growing the
  card's height, revealing that app's editable area below the existing card face. The value
  of editing here is **comparison and totals**: storage used across all apps, every
  capability grant in one audit list, which background jobs are scheduled to run, which
  agents are attached where. You cannot see any of that from inside a single app.

- **Inside view (per-app Settings).** The same fields, scoped to just this app. No totals,
  no cross-app comparison. This is where you live when you are working in the app.

If the editor is not pulling its weight, it is because it only mirrors what in-app settings
already show. The thing that justifies the outside view is the aggregate: a persistent
totals strip and a cross-app audit that no single app can render.

---

## 2. What "app settings" actually are

Each is a manifest field or a derived runtime stat, not bespoke per-app UI. The manifest
already has `title`, `sensitivity`, `api_prefixes`, `provides`, `needs` (see platform plan).
The Manager reads those and adds the operational ones below.

| Section | Source | Outside-view value (the comparison) |
|---|---|---|
| **Storage** | reserved/delegated quota + measured usage | Totals and breakdown: "Birds is 4 GB, everything else 200 MB combined." Spot the hog. |
| **Capabilities / grants** | manifest `provides`/`needs` + `grants.json` | Audit every grant at once: "which apps can reach the network / read location / touch Plaid?" |
| **Background jobs** | per-app declared crons/loops (analyzer, backfill, watchdogs) | One scheduled-run timeline. Enable/pause, last run, last error. Helps with GPU/compute contention. |
| **Agents** | per-app agent registry (see §3) | See which agents are attached to which app, their status, and links to edit/manage each. |
| **Devices** | shared device store (e.g. Yard Mic) | Which hardware belongs to which app; reassign or detach. |
| **Data retention** | manifest field | Keep-for-N-days, purge now, scoped per app. |
| **Health / status** | runtime | Running / stopped, uptime, last error, version. At-a-glance row across all apps. |

In-app Settings shows the app's own slice of these, plus genuinely app-specific knobs
(Birds detection thresholds, Budget plan), which stay inside the app and are not the
Manager's concern.

---

## 3. Agents as a first-class app facet

Apps increasingly have **agents** attached: a chat context provider (ChatDock), background
workers, schedulers, the masini bridge acting on the app's data. The Manager should surface
these per app:

- **List** the agents bound to each app: kind (chat / worker / scheduler / bridge), status
  (idle / running / errored), last activity.
- **Link out** to edit or manage each one. The Manager does not become an agent IDE. It is
  a directory with deep links: "edit prompt/context", "view logs", "pause", "open its config".
- **Scope follows the same boundary as everything else.** An app's agent can touch only that
  app's capabilities plus whatever grants allow (the platform plan's chat-boundary rule).
  The Manager shows that scope so it is auditable in one place: "this agent can read X, Y."

This makes the outside view the natural place to answer "what is running on my behalf, for
which app, and what can it touch."

---

## 4. Why general-purpose, and how that is enforced

The Manager must not contain per-app conditionals. The mechanism:

- Every section above is driven by a typed manifest entry or a registered runtime provider.
  Adding an app means adding a manifest entry; it appears in the Manager with no UI changes.
- Storage, jobs, agents, devices, retention each expose a small **provider interface** the
  backend implements once. An app opts in by declaring the field; the UI renders whatever
  providers report.
- The drawer is a single generic component rendering `sections[]` for the selected app. The
  totals strip aggregates the same provider outputs across all apps.

This mirrors the platform plan's intent: write the app once from shared primitives, and the
platform surfaces (auth, chat, and now management) wrap it uniformly.

---

## 5. UI shape (outside view)

- Apps page gets a **Manage** toggle. Off = today's grid. On = cards become expandable.
- Expanding a card slides a drawer down from behind it, extending the card. **One open at a
  time** so you are never scrolling a stack of six drawers.
- A **persistent totals strip** at the top of Manage mode shows storage total, grant count,
  scheduled-jobs count, agent count, and any errored items, always visible without expanding
  anything. This is the comparison surface.
- Icons via Lucide (no emoji), styled from the shared tokens / `@workshop/ui` primitives so
  the Manager looks native to the rest of Workshop.

Inside view reuses the exact same section components, fed only the one app's data.

---

## 6. Relationship to existing work

- **Manifest / grants / isolation**: defined in app-platform-plan.md. The Manager is the
  editor UI for `grants.json` (operator-only write) and a read view of `apps.json`.
- **Chat**: ChatDock + `chat_context.py` providers already exist; agents section links to them.
- **Shared UI**: depends on promoting `frontend/src/ui` to `@workshop/ui` (also a platform-plan
  item) so both views import the same components.

---

## 7. Open questions

- Storage measurement: per-app directory accounting, or each app reports its own usage via a
  provider? (Leaning provider, with a filesystem fallback.)
- Reserved vs delegated storage: is "reserved" a hard quota the platform enforces, or an
  advisory target? Enforcement needs a write path and eviction policy.
- Background-jobs registry: today jobs are ad-hoc (cron, lifespan loops, watchdogs). A common
  registry is prerequisite for showing/pausing them here. How much to formalize first?
- Agent registry: is there one list of agents-per-app today, or does this need a small registry
  the chat/worker/bridge pieces register into?
- Does Manage mode also allow **creating/installing** an app, or strictly editing existing ones?
