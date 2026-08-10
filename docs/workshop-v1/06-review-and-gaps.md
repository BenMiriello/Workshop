# 06 — Review and Gaps

Status: **review pass + prioritized plan** · Date: 2026-06-14 (eve) · Author: the service user (supervisor)

This is the self-review the operator asked for: everything built for v1, checked
against the design docs (00-05) and the operator's original complaints, across UI,
functionality, completeness, and coherence. Surfaces were walked live in the browser
(playwright) at multiple widths (1440 / 1024 / 768 / 390) with real content.

## 1. What is built (committed on main)

Design docs 00-05; Phase 1 chat; Phase 2 workbench (task store, builder page,
projects viewer, agents page); Phase 3 launcher bridge; Phase 4 safety (checkpoint
substrate, git-guard hook, anti-bloat GC); Phase 5 foundation (app manifest);
grants/permissions framework; context-aware egress hook; local marketplace
(publish/install-fork + UI). See [00-overview.md](00-overview.md) and the git log.

## 2. Found and FIXED in this review pass

All verified live at the widths above after fixing.

- **Builder page crammed into the 980px app cap** (operator's named complaint). The
  whole app is capped at `.ws-app` 980px (good for content), but the builder is a
  3-pane IDE, so the editor collapsed to a sliver and viewport breakpoints fought
  the cap. Fixed: workbench routes get `ws-app--wide` (1680px); narrow widths still
  stack chat-first.
- **Launcher/dashboard pages sparse on wide screens.** apps/projects/agents/
  marketplace sat in a 980px column with large empty margins on a 1440 display.
  Fixed: those get `ws-app--roomy` (1280px); tuned content apps (birds/finance/...)
  deliberately stay at 980 reading width.
- **Mobile header overflow.** Page-header action rows were non-wrapping `<Row>`s, so
  on /agents the "New builder" button clipped off the right edge at 390px. Fixed:
  added `wrap` to the action rows on Agents/Projects/ProjectDetail/AgentBuild.
- **Nonsensical status.** Apps page showed "down — HTTP null" for a probe with no
  status (null) and used an em-dash. Fixed to "down" / "down: HTTP <code>" /
  "down: <notes>".
- **Em-dashes** in backend-generated project briefs + the house-rules header
  (projects.py) replaced with comma/colon (operator hard rule).
- Verified working: the in-app assistant now correctly reports it has no tools
  (capability-aware prompt) instead of falsely promising actions.

## 3. Remaining work, prioritized (complete / refine / fix / improve)

> **Update (2026-06-14, later):** several P1/P2 items below are now BUILT and
> committed: the mobile chat composer squeeze is fixed (textarea full-width +
> controls bar); `@workshop/ui` is a versioned package (1.0.0) + single-source
> tokens (tokens.css canonical, mobile derived via `npm run gen:tokens`); the
> marketplace has update-from-upstream (conflict-safe branch merge); and the Grants
> operator UI is live at /settings/grants. Items 1 (the full workspace migration
> specifically), 4 (LocalRunner), 5 (chat tools / MCP race), and 3 (markdown
> renderer, needs a lib install) remain. Details unchanged below.

> **Update 2 (2026-06-14, npm install authorized):** the remaining items are now
> BUILT: markdown renderer (react-markdown), LocalRunner standalone deploy
> (backend + wired into the Deployments UI), chat tools enabled (MCP race fixed via
> `MCP_CONNECTION_NONBLOCKING=false`; flag flipped), and `@workshop/ui` is a real
> npm workspace package. Genuinely remaining: plaid MCP creds in the backend chat
> subprocess env (config, so live tool lookups complete; today the assistant
> honestly declines, no fabrication), Integrated route-mount deploy (needs frontend
> route injection), remote registry migration (needs remote hosting), and a minor
> tokens.js value reconciliation. The v1 plan is otherwise complete.

### P1 — should do next, mostly safe
1. **`@workshop/ui` as a real versioned package + single-source tokens** (Phase 5,
   doc 03). This is build-system surgery: it restructures the frontend into a
   workspace and needs an `npm install`, which can destabilize the build and is
   risky to do unattended (no easy recovery if the build breaks). **Recommend doing
   this interactively** with the operator able to verify `vite build`. Until then the
   existing `@workshop/ui` path alias works; the `kit` manifest field already
   references `@workshop/ui@^1`.
2. **Marketplace update-from-upstream.** An installed/forked app should pull new
   upstream releases via branch-merge (the registry stores per-version bundles
   already). Backend-additive, moderate. Rounds out the marketplace.
3. **A real content/markdown renderer for Brief and chat.** The Brief renders a
   small home-grown markdown subset; a proper renderer (sanitized) would handle
   tables/links/code fences consistently across Brief, Notes, and chat messages.

### P2 — functionality depth
4. **LocalRunner deploy model** (doc 05 §2, Phase 4/5). The Deployments rows
   (Integrated/Standalone/Public) show state but only Publish is wired; Mount/Start
   need the runner (run an app as a scoped subprocess from the manifest). Larger
   feature; design is in doc 05.
5. **Flip `ENABLE_CHAT_TOOLS` on** so data assistants can actually act (gated by the
   permissions framework, which is ready). Blocked on the MCP-startup race in
   `claude -p` (chat.py comment). Needs a careful fix; do not flip blind.
6. **Grants UI.** The grants/permissions backend exists (default-permissive); there
   is no operator-facing editor yet. Only needed once sharing/guests are real.

### P3 — polish / coherence
7. **Builder focus-pane affordance discoverability** and persisted layout per agent.
8. **Deployments "Mount/Start" disabled affordances** should clearly read as "lands
   with the runner" (currently a dimmed control + helper line; fine, revisit with #4).
9. **Token drift**: frontend `tokens.css` and mobile `tokens.js` are hand-synced.
   Low impact today (Expo app not in active use); fold into #1's single-source work.
10. **Wide-screen content apps**: birds/finance/etc stay at 980; if the operator
    wants them wider, bump per-app, but they were tuned at this width so leave unless
    asked.

## 4. Coherence check (does it make sense?)

The model holds: Project (build), App (run/share, optional backing project),
Deployment (instance); link-not-fuse. The Apps catalog ↔ Projects workbench bridge
works (Open/Create project). Agents are Assistants vs Builders by context. Chat is
one component, capability-aware. Safety (checkpoints + git-guard + egress) is
build-context-scoped and never gates the operator. The marketplace closes the
build → publish → install loop locally. The main honesty gaps are the unbuilt P1/P2
items above, which are documented, not hidden.
