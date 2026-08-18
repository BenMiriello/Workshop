# Managed paths, scoped execution, and the reference layer

- Status: ACCEPTED, 2026-08-18. Operator reviewed the first draft, rejected it as
  short-cutting, and approved this revision's recommendations: bwrap + a dedicated
  sub-user for Layer 1, scope-qualified paths as the human-facing managed address,
  the minimum `scope_root` for Layer 0 (plus evicting tracked state files), and
  icon+starting-text previews first with thumbnails as a follow-up. Filed as an
  ordered stage set the same day.
- The first draft proposed display-layer band-aids and was rejected. This revision is
  the real overhaul: one managed namespace, enforced by the OS, filesystem
  restructure first and execution confinement concurrent.
- Relates to: `target-architecture-2026-08-17.md` §2.3, §2.7 (one state root, one
  store per entity), `memory-and-documentation-model-2026-08-14.md` §10 (the ref
  format), `resource-access-interface-spec-2026-07-18.md` (the `workshop://store/...`
  URI grammar), ADR 0014 (developer mode), tickets [SEC-A1] (execution identity),
  [SEC-A2] (scope is not a security boundary), [SEC-A5] (personal-data partition is
  app-layer only), [SEC-C1] (docker-socket root-equivalence).

---

## 0. The principle, stated the way the operator did

There is no raw operating-system path anywhere, for anyone, ever. Every resource
(a file, a note, a doc, a ticket, an attachment, a run) has exactly one **managed
address**, and the agent operates **inside its scope's namespace, enforced by the
operating system**, not by prompt text and not by a display filter. Two consequences
the operator drew out and the first draft got wrong:

- **Copy-path copies the managed path, for everyone, always.** It is not gated on
  developer mode. The earlier draft gated it because it confused "path" with "OS
  path." Under this design the OS path is not a thing the surface knows, so there is
  nothing to gate: what you copy is the managed address, and a hosted user copying
  their own resource's address is exactly as valid as the operator copying one.
- **There is no path rewriter, because things only ever live in the right path.**
  The earlier draft proposed rewriting raw paths into refs at display time. That is a
  band-aid over a system that still emits raw paths. The real fix is that the agent
  runs in a namespace where its own cwd and tool output are already the managed path,
  so there is never a raw path to rewrite. A rewriter survives, if at all, only as a
  throwaway migration aid for transcripts written before the cutover (§6), never as
  the system.

"Chat running as the host user is insane" is correct and is [SEC-A1]. This document
treats real confinement as a first-class layer built concurrently, not a footnote.

## 1. Why raw paths appear today (measured, kept from the first pass)

- The literal source is the runtime briefing: `chat_context.py:972` writes
  `Working directory (cwd): {absolute path}` into the prompt every turn, and for the
  self repo also emits `~/workshop` and repo-relative paths (`:988-1021`).
- It fires **only for build/maker agents** (`runtime_briefing` returns `""`
  otherwise, `chat_context.py:965-966`; `is_build_provider` is the dev/build agent
  and `role:maker`, `:1947-1949`). This chat is the development build agent, so the
  operator sees it; a hosted general-chat user is never handed the cwd and has no
  filesystem tool (`general` declares `allowed_tools = []`, `:1361`).
- The build agent also echoes paths straight out of its own Bash/Read output. That
  half is not fixed by touching the briefing; it is fixed only by putting the agent
  in a namespace where those paths are already managed (§3, Layer 1).

So "just because I'm in dev?" is yes for the *briefing*, but the deeper cause is that
the build agent runs directly on the host tree with the host's paths. Fixing the
symptom without fixing that is the short-cut this revision refuses.

## 2. The security truth (why the UI cannot be the boundary)

- User **apps** are containerized (rootless per-user docker). The **agent itself is
  not**: chat and autonomy spawn `claude -p` as the host user with full-tree
  read/write, the OAuth token and docker access (`build_streaming.py:2054`; the
  systemd unit has no `User=`, no `ProtectHome`, no sandbox directive, runs as `%h`).
  A grep for `chroot|bwrap|firejail|nsjail|seccomp|unshare` across the tree is empty.
- `scope`/`touches`/`latitude`/`grants` look like a jail and are advisory in-process
  metadata the agent can rewrite ([SEC-A2]); a same-uid `cat` reads any owner's data
  ([SEC-A5]); the docker socket is root-equivalent and load-bearing ([SEC-C1]).
- ADR 0014 already records that developer mode is "a safety interlock, not a security
  boundary... real confinement is a separate execution identity, [SEC-A1], still
  open."

The team has already fought a smaller version of this: `paths.chat_cwd()` was changed
because scope-less turns defaulted to `~`, which "put every turn without a scope in
the directory holding `~/.config`, `~/.ssh` and every other credential." The fix
there was a real empty per-owner directory. This overhaul generalises that instinct
into an OS-enforced per-scope view.

## 3. The proper system: four layers, built bottom-up

### Layer 0 — Filesystem restructure (FIRST)

Nothing above can be clean until the disk is. Per `target-architecture` §2.7: code is
`backend/ frontend/ mobile/ packages/ infra/ docs/ scripts/`; **everything mutable
moves under one untracked state root** and is addressed only through
`wsplatform/paths.py`. Today `backend/grants.json` (tracked), `resource-grants.json`
(124 KB, live), `apps.json`, `store-policies.json` live inside the source tree, and
`.runtime/`, `projects/`, `shared/queue/` are scattered.

Then define a **scope root**: one directory that is the entire world of a scope. Today
a scope is smeared across parents and is asymmetric (agent *notes* at
`.runtime/agents/<id>/knowledge/` but agent *tasks* at
`.runtime/agent-scopes/<slug>/`; project tree, project notes and project queue in
three places). Restructure so each scope root contains that scope's working tree,
notes and data as subtrees. You cannot mount, address, or confine a scope you cannot
point at with one path. This layer is what makes the other three possible.

Output of the layer: `paths.scope_root(scope) -> Path` is the single mapping, every
other path derives from it, and a ratchet forbids a new mutable file under a source
directory (the same shape as the existing `check_no_raw_storage` gate).

DEFERRED FOLLOW-UP (agent-dir unification). The minimum landed 2026-08-18 keeps the
existing asymmetry as-is: agent *notes* live at `.runtime/agents/<slug>/knowledge/`
(what `scope_root(agent:<slug>)` returns) while agent *tasks* live at
`.runtime/agent-scopes/<slug>/` (unchanged, `task_scope._agent_scope_dir`). Folding
both under one agent scope root is a data move with its own conservation count and
is a separate ticket, not part of this behavior-preserving pass; `task_scope`
therefore does NOT call `scope_root` yet. Also deferred to the human: deleting the
now-stale tracked `backend/grants.json` after the startup relocation is verified.

### Layer 1 — OS-enforced scoped execution (concurrent with Layer 0; this is [SEC-A1])

The agent stops being the host user. Every turn runs:

- under a **dedicated lower-privilege identity** (a per-instance sub-user without the
  docker group), so a bug or an injection cannot reach the OAuth token, other owners'
  data, or the docker socket; and
- inside a **filesystem view** where the turn's scope root (Layer 0) is bind-mounted
  at a fixed managed mountpoint, e.g. `/workspace`, and nothing outside it is
  reachable.

Effect: the agent's cwd *is* `/workspace/...`; a `cat /home/...` or another scope's
data fails because it is not in the view; and its Bash/Read output is already the
managed path. "Only the right paths" becomes true by construction. This subsumes
[SEC-A1], [SEC-A5], and the execution half of [SEC-C1].

Mechanism is an implementation decision, not settled here: candidates are a rootless
container per turn (podman/docker; rootless docker is already configured in the unit),
`bwrap`/user-namespace bind mounts, or a sub-user plus systemd `ProtectHome` +
`ReadWritePaths` scoped per turn. The app runner's own docker need moves behind a
socket-proxy or stays on rootless docker so removing the group does not break it
([SEC-C1] remediation). This is a host-level change and the largest single pole.

### Layer 2 — One managed address per resource

Build on what exists, do not invent a parallel scheme:

- `services/stores.py` already has `workshop://store/<app>/<name>[/<path>]`, with
  broker, grants and policy tiers built.
- `services/refs.py` already has `workshop:<kind>/<key>` for
  note/doc/ticket/chat/run/project/agent/app, resolving to `{kind, title, href,
  status}` with an in-app href, refusing dead ones. Its acceptance test is literally
  "nothing renders a raw path or a copyable URL."

Add:

- a **`file` kind** addressed through the Store layer, so any file (pdf, zip, image,
  generated artifact) has one managed address, and chat **attachments** register as a
  Store so an attachment and a referenced file are the same object;
- `resolve_ref` returns a **preview descriptor** (mime, size, an icon token, and a
  thumbnail URL where one exists) so the UI can render §Layer 3 without a second call;
- a stable mapping between the logical managed address the user sees
  (`development:backend/services/refs.py`, or a `workshop://` URI) and the physical
  mount from Layer 1. The OS path under the mount is an implementation detail nothing
  above the mount ever names.

### Layer 3 — The reference UI, and copy-managed-path for everyone

Refs render as chips everywhere (chat, notes, tickets), to the design system (kit
primitives, tokens, lucide, no em-dash), reading as a link **without hover** since the
operator is often on a phone. The chip opens a drop-down or drop-up (the operator's
spec):

- **preview pane left**, capped at half the container width: a real thumbnail for a
  showable file, a shorter icon-and-title row for a zip/binary/nothing-to-show;
- **starting text right**, at least half the width;
- **actions below**: Open in memory (navigate to `href`), Copy url (the in-app
  href), **Copy path (the managed address, for everyone, no dev gate)**.

No OS path is ever shown. Real thumbnails for pdf/image are a **named follow-up** (the
operator still wants them; they are deferred only so the control ships without a
render pipeline first: icon + starting-text, thumbnails second).

### Layer 4 — The ratchet (so it does not regrow)

A reviewer-gate check, in the family of the existing `check_*` gates, that fails when
a raw OS path appears in agent-facing output, persona text, a ref, or UI copy.
`target-architecture` item 7 is explicit that the three prior overhaul docs decayed
because "nothing mechanically prevented the next contributor" from re-adding the mess.
This layer is not optional.

## 4. What each of the operator's points maps to

- "Chat as host user is insane" -> Layer 1, [SEC-A1], built concurrently, not
  deferred.
- "Copy-path dev-only, why?" -> corrected: copy the managed address, everyone, always
  (§0, Layer 3).
- "What's a path rewriter / why not just put things in the right paths?" -> the
  rewriter is dropped as the system; Layer 1 makes the agent emit managed paths
  natively (§0, §3).
- "Filesystem restructure first" -> Layer 0, the precondition.
- "Real preview as follow-up but I still want it" -> kept as a named later phase in
  Layer 3, not dropped.
- "Fully overhaul, make the proper system now" -> four enforced layers plus a ratchet,
  not a display filter.

## 5. Honest cost and blast radius

This is not one ticket. Layer 0 touches `paths.py` and ~every path call site and moves
tracked state files (a data migration with a conservation count). Layer 1 is
host-level: identity, mount/namespace mechanism, the systemd unit, the app-runner
docker path, and it must keep crash/restart turn-adoption working
(`build_streaming` resilience, the `KillMode=process` contract in the unit). Layer 2
is backend and testable in isolation. Layer 3 is frontend and design-system. Layer 4
is one gate. It is an **ordered ticket set**, and Layer 1 is the one that needs an
operator-level decision on mechanism because it changes how every turn is executed.

## 6. Migration

Old transcripts contain raw paths written before the cutover. Options: leave them
(they are history), or run a one-time pass that turns recognisably-in-tree paths into
refs. This is the *only* place a "rewriter" is acceptable, it is a batch migration not
a display filter, and it is optional. Nothing new writes a raw path once Layer 1 is
in.

## 7. Sequence

1. **Layer 0 — restructure** (state root, `scope_root`, ratchet on new mutable files
   under source). First.
2. **Layer 1 — scoped execution / [SEC-A1]** (lower-privilege identity + per-scope
   mount view). Concurrent with 0 where it does not depend on `scope_root`, blocking
   on it where it does. Needs the mechanism decision (§8).
3. **Layer 2 — `file` kind + attachments-as-Store + preview descriptor.** Backend.
4. **Layer 3 — the `<Ref>` dropdown; copy managed path for all.** Frontend. Thumbnails
   are the tail of this layer.
5. **Layer 4 — the raw-path ratchet.** Ships with the reviewer gate.
6. **Migration pass** on old transcripts. Optional, last.

## 8. Open questions for the operator

1. **Layer 1 mechanism.** Rootless container per turn (reuses the docker already
   configured, heaviest isolation, ~container-start latency per turn), `bwrap`/user
   namespace (lighter, needs the binary and namespace support), or sub-user + systemd
   `ProtectHome`/`ReadWritePaths` (no new dependency, coarser). Recommendation: prove
   the model with `bwrap` per turn for the filesystem view plus a dedicated sub-user
   for identity, because it is the lightest thing that makes the boundary real; revisit
   containers if per-turn latency is fine.
2. **Managed address surface form.** A scope-qualified path
   (`development:backend/services/refs.py`) or the `workshop://store/...` URI as the
   copyable string? Recommendation: the scope-qualified path for humans (short,
   pasteable back into chat, which is the operator's stated use), with the URI as the
   machine form behind it.
3. **How aggressive is Layer 0's move?** Full `target-architecture` §2.7 state-root
   consolidation now, or the minimum `scope_root` needed to unblock Layer 1, with the
   broader consolidation as its own effort? Recommendation: the minimum `scope_root`
   plus the tracked-state-file evictions (`backend/grants.json` and siblings), defer
   the full env-var/`var/` consolidation so Layer 1 is not gated on a 126-env-var
   sweep.
4. **Attachment preview backend.** Confirm icon + starting-text first, thumbnails as
   the Layer 3 tail (operator already said follow-up).

**Operator answers (2026-08-18):** q1 bwrap + dedicated sub-user; q2 scope-qualified
path for humans with the URI as the machine form behind it; q3 minimum `scope_root`
plus tracked-state eviction, defer the full `var/` consolidation; q4 icon+text first,
thumbnails follow-up.

---

## 9. Acceptance criteria per stage

Each stage is a filed ticket; a stage is done only when its criteria pass under the
reviewer gate, not by self-report.

**Layer 0 — restructure.**
- `wsplatform/paths.py` exposes `scope_root(scope) -> Path` as the single mapping, and
  `knowledge.scope_dir` / `task_scope.root_for_context` derive from it rather than
  spelling their own parents.
- `backend/grants.json`, `backend/resource-grants.json`, `backend/apps.json`,
  `backend/store-policies.json` no longer live under a source directory; they move to
  the state root with a conservation count reported before and after, and every reader
  updated in the same commit.
- A ratchet (`scripts/check_no_state_in_source.py`, wired into `reviewer.sh`) fails on
  a new mutable/state file committed under `backend/ frontend/ packages/`.
- Full backend suite green; `apply.sh --check` in sync.

**Layer 1 — scoped execution ([SEC-A1]).**
- A chat/autonomy turn runs under a dedicated non-`clawd-a` sub-user without the docker
  group, inside a filesystem view where only the turn's `scope_root` is reachable at a
  fixed mountpoint.
- Proof by test AND attended live check: from inside a turn, `cat` of an out-of-scope
  absolute path and of another owner's `.runtime/users/*` fails; the OAuth token and
  the docker socket are not reachable; the turn's own cwd resolves under the mountpoint.
- Crash/restart turn-adoption still works (a turn survives a backend restart), and the
  `KillMode=process` contract in the unit is preserved.
- This stage MUST NOT be marked done, and the backend MUST NOT be left restarted on it,
  until the operator confirms live chat still works. It changes how every turn spawns;
  a wrong cutover breaks chat for the whole instance. Attended verification is a
  requirement of the stage, not caution.

**Layer 2 — managed address.**
- `refs.py` gains a `file` kind resolving through the Store layer; chat attachments
  register as a Store; `resolve_ref` returns a `preview` descriptor.
- A file has exactly one managed address; `resolve_ref` refuses a deleted/escaping one.
- Tests cover parse/serialize round-trip for `file`, resolution of a live attachment,
  and refusal of a path escaping its store.

**Layer 3 — the reference UI.**
- The `<Ref>` chip renders as a link without hover and opens a drop-down/drop-up with
  preview-left (<= half width) / text-right (>= half width) / actions-below.
- Actions: Open in memory, Copy url, Copy path (the managed address, for every user,
  no dev gate).
- No surface renders a raw OS path. `npm run typecheck` and `check_design.sh` clean.
- Thumbnails are the stage tail; the chip ships first with icon + starting text.

**Layer 4 — ratchet.**
- `scripts/check_no_raw_paths.py`, wired into `reviewer.sh`, fails when a raw OS path
  appears in agent-facing output, persona text, a ref value, or changed UI copy. Pinned
  to the current baseline if not already zero, gated at zero once clean.

## 10. Verification and rollback

- Every stage lands on a branch off `main`, small and self-contained, and goes through
  `reviewer.sh`. Layers 0 and 2 rebuild nothing user-facing; Layer 3 rebuilds the
  bundle via `apply.sh`.
- Layer 1 is the only stage that can take the instance down. It is verified attended,
  behind a flag that defaults off, so an unproven cutover cannot become the default
  spawn path. The flag flips to on only after the §9 Layer-1 checks pass live.
- Data moves (Layer 0) report conservation counts and are backed up before deletion,
  per the "tests must not share owner storage" and stage-2-transcript lessons: a moved
  file is moved once, never copied, and never left half-present in the shared tree.
