# 0013. Treat the public auto-deploy as a pre-launch arrangement, not as production

- Status: accepted
- Date: 2026-08-09

## Context

Every commit to `main` in the canonical worktree that touches UI inputs triggers
a background rebuild of `frontend/dist` (`.githooks/post-commit`, wired via
`core.hooksPath`). `workshop.<your-domain>` → cloudflared → `:8002` serves that
same `frontend/dist` from disk per request, so the rebuild is live with no
restart and no deliberate release step.

In other words: **committing to `main` publishes.** There is no staging origin,
no release gate, no environment separation, and no version pinning between what
is committed and what the public hostname serves. The post-commit hook's three
guards (canonical worktree, default branch, UI files touched) prevent publishing
*unmerged* code; they do nothing to make publishing a *decision*.

This is currently correct, and it is worth being explicit about why:

- There is one user, who is also the developer. `workshop.<your-domain>` is not
  a product surface with an audience; it is the operator's own daily UI, reached
  from devices where the tailnet dev origin is inconvenient.
- The alternative (a manual publish step) was tried implicitly and failed. The
  build is not hard; it is a separate thing to remember, after the work already
  feels finished. On 2026-08-09 an entire session of frontend work was correct in
  source, never built, and the operator spent the session looking at a stale
  bundle insisting the fixes were absent. Automation on the commit event is the
  fix for that failure, and it should not be removed while the conditions that
  make it safe still hold.

What makes it safe is precisely what will stop being true at launch: one user,
no audience, and no distinction between "the developer's build" and "what people
are running."

## Decision

Keep the post-commit auto-deploy for now, and **record it as a pre-launch
arrangement with an explicit expiry** rather than as the production deployment
model. It is not to be cited as precedent for how releases work.

The exit condition is hosting the app somewhere with a real deployment pipeline
(Cloudflare or equivalent). At that point `main` must stop publishing on commit,
and shipping becomes a deliberate, versioned act.

What happens to `workshop.<your-domain>` at that point is deliberately left
open. Two candidates, not chosen here:

- it becomes proper production, fed by a release pipeline instead of a git hook; or
- production moves to the new host and this hostname is demoted to a dev/preview
  origin, keeping auto-deploy exactly as it works today.

Choosing between those is a separate record, made when the hosting target is
real. Do not pre-build for either.

## Consequences

- The `post-commit` hook, `scripts/build_workshop_public.sh` and
  `scripts/ui_sync.sh` stay as they are. Builds hold a lock so the freshness
  stamp is trustworthy.
- Anything that would only make sense for a multi-user production surface
  (release channels, staged rollout, deploy approvals) is **out of scope** until
  the exit condition is met. This is a single-operator system; see `CLAUDE.md`.
- When this record's exit condition is met, the developer-mode separation
  described in the Development-scope work becomes load-bearing rather than
  cosmetic: the thing that must not ship to other users is the ability to edit
  the Workshop itself, and that gate is what makes a real production build
  possible. These two decisions are linked and should be revisited together.
- Until then, "committed" and "published" mean the same thing for the frontend.
  Anyone reasoning about what the operator can see should assume the public
  origin is at `main` — and verify with `scripts/ui_sync.sh`, never by assuming.
