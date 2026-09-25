# Instances & forking — where a change goes, and how it travels

Workshop is one codebase run as **multiple independent instances**, each a
long-lived branch of `<owner>/Workshop`:

| Branch | Instance | Owner / box |
|---|---|---|
| `main` | the primary deployment (`workshop.<your-domain>`) | its operator's box |
| `<instance>-local` | an independent instance (e.g. tailnet-only) | that operator's box |

`main` is the **upstream** — the shared source of truth for the *platform*.
Every instance branch is a **permanent downstream fork** (not a feature branch):
it carries its own config, customizations, and app roster forever, and
periodically pulls the platform forward from `main`. An instance keeps its own
specifics (ports, origin, local-only features) in untracked files on its own
branch's checkout, never in a tracked doc.

---

## The one rule: classify every change, default to Local

Before committing, decide what kind of change it is. **If you can't tell, it's
Local.** A wrong "Local" is harmless (it just isn't shared yet — promote it
later). A wrong "Platform" pollutes every other instance's `main`.

### Platform change → belongs on `main` (benefits every instance)
Generic improvements with no instance-specific assumptions:
- Bug fixes in platform code (auth, proxy, runner, chat, projects, agents).
- Navigation / shell / UI-primitive work (e.g. the framed-app `AppFrame`,
  escape-recovery, the reverse-proxy base template for legacy apps).
- Performance, refactors, new reusable `src/ui` primitives.
- **Portability**: making a hardcoded path/host/identity read from env *with a
  safe default*. The mechanism is Platform even when the value we set is Local.

### Local change → stays on the instance branch forever (never upstream)
Anything that only makes sense for this deployment:
- Single-operator conveniences the instance adds for itself.
- Removing another operator's personal apps; our app roster; operator identity.
- Our tailnet origin/ports, runtime paths, `.runtime/`-based registries, the
  committed env *values*.
- Throwaway/experimental changes for this operator's own use.

---

## How a change travels (two lanes)

We develop on our instance branch and do **not** push to `main` (we don't own
that deployment). So changes move like this:

### Lane A — Local change
Commit on the instance branch, push to `origin/<instance-branch>`. Done. It lives
here forever.

### Lane B — Platform change
Still commit on the instance branch (so our instance runs it immediately), **but
make it upstreamable**:
1. Keep the commit **atomic and self-contained** — no Local-only code mixed in.
   If a change is part-Platform part-Local, split it into two commits.
2. Prefix the commit subject with **`[platform]`**.
3. Add a line to [`upstream-queue.md`](upstream-queue.md): hash, one-line summary,
   and whether it's a **clean drop-in** or **needs migration** (see below).

The **`main` owner** periodically fetches `origin/<instance-branch>`, reviews the
queue, cherry-picks the `[platform]` commits onto `main`, pushes, and ticks them
off. Nothing lands on `main` without the owner's review.

**Clean drop-in vs needs-migration.** Some Platform changes cherry-pick straight
onto `main`; others assume groundwork this instance did that `main` hasn't (e.g.
the pluggable-launcher redesign assumes apps are installed content, but `main`
still hardcodes its apps — so it's a coordinated migration, not a `git
cherry-pick`). Tag each queue entry so the owner knows which is which.

### Staying current with upstream
Periodically merge `origin/main` into the instance branch to absorb everyone's
platform work: `git fetch origin && git merge origin/main`. Our cherry-picked
Platform commits usually reconcile by patch-id (same diff → no conflict);
Local commits sit on top and rarely conflict. Resolve any conflict in favor of
**keeping Local customizations** (that's the whole point of the fork).

---

## Shared app repos are NOT forked per instance

Apps live in their own repos and are **shared** — every instance runs the same
app `main`. A fix there benefits all instances directly: commit to the app repo's
`main`, and each instance just pulls, no cherry-pick.

Only the **Workshop platform repo** is forked per instance, because that's what
carries per-deployment identity and customization.

---

## Mobile app: one build per instance

The Expo app (`mobile/`) is operator-only and points at exactly **one** Workshop
backend origin. Each instance builds its **own** app — same source, different
identity + origin — so both can coexist on one device. No code edits: it's all
env overrides (`mobile/app.config.js` + `native/config.ts`):

| Env var | Purpose |
|---|---|
| `EXPO_PUBLIC_WORKSHOP_URL` | backend origin the WebView + native producers use |
| `WORKSHOP_APP_NAME` | home-screen display name |
| `WORKSHOP_APP_SLUG` | Expo slug |
| `WORKSHOP_APP_SCHEME` | deep-link scheme |
| `WORKSHOP_IOS_BUNDLE_ID` | iOS bundle id (must differ per instance to coexist) |

The device must be able to reach the instance's origin (a tailnet-only origin
needs the device on that tailnet). This is per-instance build config, so it's **Local** — it never
goes upstream.

---

## Quick decision flow

```
Is the change generic platform code that any instance would want,
with no assumption specific to this deployment?
        │
   yes ─┤─ no / unsure
        ▼            ▼
   [platform] +   Local: commit on the instance branch, stays here.
   queue entry;
   owner cherry-picks to main.
```
