# 03 — UI Kit and Marketplace

Status: **draft for operator review** · Date: 2026-06-14

Companion to [00-overview.md](00-overview.md). Covers the design system as a
versioned, shareable package, and the marketplace for apps and kits. This is
Phases 4 and 5; the registry itself is gated on overview decision #2.

---

## 1. The kit as a package

Today the design system is `frontend/src/ui` + `src/styles/tokens.css`, imported via
the `@workshop/ui` path alias (not a real package). Make it a real **versioned
package**:

- `@workshop/ui` becomes a workspace package with its own `package.json` and semver.
  It owns the primitives, the CSS, and the tokens.
- Apps declare a kit dependency by version (in the project/app manifest:
  `"kit": "@workshop/ui@^2"`). The default kit matches the built-in apps and the
  workshop shell.
- Tokens get a single source of truth and are emitted in both forms the codebase
  needs: CSS custom properties (web) and the JS object (Expo/mobile), so the two stop
  drifting (they are hand-synced today).

This is the prerequisite that app-platform-plan.md and app-manager-ui.md both already
assume ("promote `src/ui` to a workspace package").

## 2. Multiple kits, default kit

- There is one **default kit** (the current system). Apps inherit it unless they
  declare another.
- Anyone can publish their **own kit** to the marketplace. A kit is just a special
  project type (a package, no runnable surface) that exports primitives + tokens
  conforming to the kit contract.
- An app chooses its kit; the workshop shell always renders in the default kit so the
  chrome stays consistent even around an app using a custom kit (overview: "retain the
  workshop toolbar even if they do not use the workshop UI toolkit").

## 3. Versioning and upgrades

- **Compatible upgrades** (same major): programmatic. Bump the version; nothing else
  to do.
- **Breaking upgrades** (new major): **AI-assisted migration**. A builder task is fed
  the kit's changelog/codemod notes and the app's source, and it migrates the app to
  the new major, surfacing anything it cannot do automatically. Old apps stay on their
  pinned major until the operator opts in to upgrade.
- A kit ships a machine-readable changelog (what moved/renamed/removed per major) so
  the AI migration is grounded, not guesswork.

## 4. Marketplace

A registry of publishable **apps** and **kits**, reachable from both local and hosted
deployments (the "run anyone's app in your workshop" idea, Roblox-style).

- **Publish**: a project with a deployment surface (app) or a kit publishes a release
  to the registry (sensitivity must be `public`).
- **Install**: pulls the backing project into your workspace. Two install modes:
  - **Run** a hosted public deployment (read-only consumption, no local copy).
  - **Fork** the project into your workspace to modify it (a real git fork; overview
    §4 "secondary mode").
- **Update from upstream**: an installed/forked app can pull upstream changes via the
  normal branch/merge machinery (the primary mode), so staying current is the same
  motion as any other branch merge, not a re-fork.

## 5. Local-now, remote-later; full registry, built last

Operator decision (2026-06-14): **build the full registry, but last**, and treat the
current system as the **local dev version**. So the registry is real, not a stub, but
it starts **local** and migrates to remote when remote hosting exists.

- **Phase 5 (seams, with the platform work)**: extract `@workshop/ui` as a versioned
  package; add the `kit` field to the app manifest; make tokens single-source. This
  unblocks custom kits and costs nothing in single-user mode.
- **Phase 6 (local registry)**: a **local** app + kit registry: publish a release
  locally, install/fork from it into the workspace, update via branch-merge, and the
  AI-assisted kit migration. This is the "local dev version" of the marketplace, fully
  functional on one box.
- **Remote migration (later)**: point the same publish/install/fork flows at a remote
  registry once remote hosting exists (doc 05 §8, 05c). The registry interface is the
  same local and remote, so this is a backend swap, not a rewrite.

The seams (manifest `kit` field, versioned package, `public` sensitivity, git
fork/merge already present, a registry interface with a local implementation first)
mean the remote step is additive.
