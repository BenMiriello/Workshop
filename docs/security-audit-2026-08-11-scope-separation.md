# Security audit — user/scope separation, 2026-08-11/12

**Status:** audit findings below (§§0-5), **remediation shipped 2026-08-12 (§8)**. Companion to
`security-posture.md` (living reference) and `security-audit-2026-07-18.md` (prior host-wide pass).
Commit at time of writing: `512474a`.

> **§6 is the ORIGINAL build list and is now historical.** Read §8 for what was actually built, what
> changed shape on contact with the code, and what is still open. Two of §6's items were implemented,
> reviewed, and found not to do what they claimed; §8 says so explicitly rather than quietly
> re-listing them as done.

**Trigger:** the operator asked "why have I been able to do most things from the `general` scope" —
investigating that surfaced a live gap between the design (`development` is the only scope that may
touch the platform repo, ADR 0014) and the code, which led to this broader pass: assume Workshop is a
real deployed product where separate end-users each have "their own workshop" and must never see or
reach anything outside it — not the platform's own source, not the host, not another user's data.

**Scope of this pass:** application-layer separation between chat scopes, between authenticated users,
and the shared infrastructure (MCP servers, knowledge store) both reach into. Host/network exposure was
only delta-checked against the prior audit, not re-swept from scratch.

---

## 0. Already fixed this session

- `general` chat scope's `allowed_tools` was `["mcp__workshop__query", "Read", "Grep", "Glob", "Edit",
  "Write", "Bash"]` (widened 2026-07-18, commit `8d3b978`, undocumented) while its cwd
  (`WORKSHOP_CHAT_CWD`) pointed at the platform repo — the unscoped product chat could read and edit
  Workshop's own source, and fell back to `~` (the whole account home, including
  `~/.config/clawdbot/*` credentials) when unset. Fixed: `general.allowed_tools` is now
  `["mcp__workshop__query"]` only (`backend/services/chat_context.py:1476-1499`). **This is necessary
  but not sufficient — see §3, which shows the one tool left in place has its own leak.**

---

## 1. Registration is closed — nothing below is live-exploitable today

`WORKSHOP_REGISTRATION_OPEN=0` in `~/.config/workshop/secrets.env`, enforced fail-closed in
`backend/routes/auth.py:31-36,102-103`. No second account can be created right now. Everything in §§2-4
is what happens **the moment that flips to 1**, or the moment any code path lets an untrusted chat
reach these providers regardless of account count (the path-traversal finding in §2 needs no second
user at all — the operator's own session can trigger it).

The core web-auth model itself re-verified solid: `ownership.current_owner()`
(`backend/services/ownership.py:57-66`) is a fail-closed contextvar — raises `OwnerUnset` rather than
defaulting to the operator — and `WORKSHOP_OPERATOR_EMAIL` is confirmed absent from every request-time
authorization path (only touched by a one-off migration script). Session crypto, cookie flags, and
registration gating all match `security-posture.md` §3 as designed.

---

## 2. CRITICAL — project-scoped chat: path traversal to full host access + every host credential

`ProjectContextProvider.__init__` computes `self.dir = _projects_root() / slug`
(`backend/services/chat_context.py:1298`) with **no slug validation**. `SLUG_RE` exists but is only
enforced at project *creation* (`build_agents.py:41`, `routes/projects.py:47`) — never at chat *scope
resolution*. The one containment check downstream, `app_runner.is_self_dir`
(`app_runner.py:75-83`), is **exact-equality** against the platform repo root.

Concrete path: `POST /api/chat/threads` with `context_key="project:../../.."` (or the rescope
endpoint, which only checks `provider.key == new_key` and `provider.dir.exists()`,
`routes/chat.py:765-771`) resolves a cwd **outside `projects/` entirely** — `project:..` lands on the
repo root and is caught by the exact-match check, but `project:../..`, `project:../../home/x`, or
anything that isn't precisely the repo root is not caught **at all**. `is_build_provider` is true for
it, so the turn gets `allowed_tools=None` (no `--allowedTools`) and `bypassPermissions`
(`chat_context.py:2295-2297`, `llm_router.py:589,602-604`) — full agentic Bash/Edit/Write, unscoped.

**This compounds with §3's MCP finding**: the full-agentic path emits no `--strict-mcp-config` flags
unless the thread explicitly narrowed its MCP selection, so this same traversed session also inherits
**every MCP server on the host** — Plaid, Google Docs/Drive/Gmail, Discord, Playwright — not just
filesystem access.

This requires an authenticated Workshop chat session (chat routes are `require_user`), but nothing
beyond that — no dev mode, no special privilege. Today that's only the operator (registration closed);
the instant a second real account exists, it's theirs too.

Related, lower severity: `build:<slug>` for an *unknown* slug falls back to `paths.workshop_root()`
(`chat_context.py:1389`) — caught only by `assert_run_target`, which passes here because
`WORKSHOP_DEV_MODE=1` is set (for `development`'s own legitimate use). And the alias-multiplication
class fixed today for task stores (`task_scope.root_for_context`, `470823c`) was **not** fixed for chat
cwd resolution — `get_provider` has no equivalent "does this key's target collide with a reserved
scope's directory" check, so a project/app registry row named `workshop`, `general`, or `development`
could still mint a second address to a sensitive directory.

---

## 3. CRITICAL — no per-user knowledge or notes partitioning; the "safe" tool leaks anyway

Two stores were added after the 2026-06-25 ownership redesign and never got an `owner_id` segment:

- **`shared/knowledge/`** (`backend/services/knowledge.py:122-155`) — a *repo-level* path
  (`<repo>/shared/knowledge/`, `.../roles/<r>/`, `.../development/`). No user partitioning exists in
  this namespace at all (contrast `chat_store`'s `.runtime/users/<owner_id>/chat/`). It is:
  - **Read** into every chat turn's prompt via `layered_index_block` (`routes/chat.py:889-897`) — the
    `workshop` layer is injected into *every* scope by design, and `mcp__workshop__query`
    (`backend/mcp/workshop_query_server.py:47-57`) **hard-pins the caller to the operator's identity
    unconditionally** (`_pin_owner()`, comment: "Single-operator system") regardless of who is actually
    chatting. This is the one tool `general` was left with in §0 — it still returns the operator's
    whole-instance task/job/notes digest to whoever asks.
  - **Written** by any chat via a model-emitted ` ```note ``` ` fence carrying an arbitrary
    `"scope"` value (`task_scope.apply_note_block`, `:445`) — a chat can write into `workshop` (or any
    other scope) regardless of its own binding, and compaction auto-writes `chat-distilled` into it too
    (`routes/chat.py:1102`). `memory_keeper` (`services/memory_keeper.py`, keyed by `scope_key` not
    `owner_id`) then folds everything in a scope into one globally-readable `consolidated-memory` note.
    Current contents (19 files) include operator engineering memory and at least one note explicitly
    about personal-data handling.
- **`~/notes` (the notes-app)** — `services/notes_vault.py:26-32` resolves to one global directory,
  mounted for **every** authenticated user with only `require_user` (`app_registry.py:487`, zero
  owner-filtering anywhere — `grep -c owner routes/notes.py services/notes_vault.py` → 0/0). Any
  logged-in account can `GET`/`PUT`/delete/rename any note in the operator's vault today.

**Worst concrete leak in this pass:** a second authenticated user reads and overwrites the operator's
entire notes vault with no bypass required — there is no check to bypass. Runner-up: that same user's
first `general`-chat message surfaces the operator's `MEMORY.md` and `consolidated-memory.md` via
ambient injection, and a scope-fenced note write folds their own transcript into the operator's durable
memory.

---

## 4. HIGH — MCP servers are global, not per-user; app-persona tool grants are unvalidated

All MCP credentials are process-level, not caller-scoped (`app_registry.py:647` merges specs from env
+ repo paths, nothing parameterized by owner):

| Server | Credential scope | Reachable from |
|---|---|---|
| `plaid` | one host token set (`~/.config/workshop/plaid-auth.env` + `plaid-tokens.json`) | `budget`/`finance` app personas by declared tool list, **and** any traversed project/build/development chat (§2) |
| `google-docs`, `playwright`, `discord`, `filesystem` | single host OAuth/bot tokens, inherited from `~/.claude.json` | every build/project/`development` chat — the full-agentic path emits no MCP restriction flags by default (`llm_router.py:634-641`, `mcp_selection=None` → all) |
| `workshop` (query tool) | hard-pinned to the operator, see §3 | `general` |

Worst concrete path: a non-operator user in any project/build chat directly invokes
`mcp__plaid__list_transactions` or `mcp__google-docs__searchDriveFiles` — both execute against the
**operator's** credentials, with the reply landing in that user's own thread. No identity check exists
on this path at all.

Separately: app-persona tool grants (budget, finance, commute, location) come verbatim from each app's
`agents/apps/<key>.md` declaration (`app_personas.py:479-481`) with no runtime ceiling — if a persona
file is ever edited to include `Bash`, it runs at the same unscoped `WORKSHOP_CHAT_CWD`/`~` fallback
`general` used to. Today none do, but nothing enforces that they can't.

---

## 5. Delta check — prior 2026-07-18 host-wide audit

Good news: the four Critical/High findings from that pass are fixed.

| Finding | Status | Evidence |
|---|---|---|
| SEC-C1 docker group (clawd-a root-equivalent) | **FIXED** | `id clawd-a` → not in `docker` group; rootless docker via `DOCKER_HOST` override in the backend service's systemd drop-in |
| SEC-C2 unauthenticated Redis, RCE potential | **FIXED** on host surface | no `:6379` listener on the host (container-internal only); residual `intergen-redis :6380` is loopback-only, still no AUTH (low priority) |
| SEC-C3 IPv6 firewall unknown | **effectively moot** | IPv6 is disabled host-wide (`disable_ipv6=1`, no `[::]` route) — couldn't fully verify the ip6tables ruleset itself (root-only), but there's no v6 stack for it to matter on |
| SEC-H1 `/auto-pr` edge routing typo | **FIXED** | blocklist now matches `/auto-pr`; live 404 confirmed |
| SEC-H2 `0.0.0.0:8000` + RFC1918 LAN trust | **FIXED** | backend now binds `127.0.0.1:8000` only; `/tools` trust narrowed to `127.0.0.0/8` + `172.16.0.0/12` (Docker bridge), not the whole LAN |

Still open, unrelated to scope separation but worth carrying forward: **H3** (NOPASSWD sudo git as
clawd-house), **H4** (ComfyUI still `0.0.0.0:8188`), **H7** (Ollama still `0.0.0.0:11435`/`*:11434`),
M1-M3, L5, A2/A5/A6, G1.

**Tracker hygiene note:** SEC-A1 ("dedicated lower-privilege execution identity for the autonomy loop")
is marked `done` in the ticket board but only half-shipped — the rootless-docker half landed and is
verified above; the actual separate low-privilege identity for agent turns was never built. This is the
same gap `security-posture.md` §5a already names as the honest unfixed core: tool allowlists and cwd
checks are policy, not an OS boundary, and every finding in §§2-4 above is exactly that class of bug —
enforced by application code that can be reasoned around, not by anything the OS itself would stop.

---

## 6. What to build — in priority order

**Before registration ever opens (blocks real multi-user launch):**

1. Validate scope-derived cwds the same way for chat as for task stores. `task_scope.root_for_context`
   already rejects a key whose resolved root collides with a reserved directory — the chat-turn cwd
   resolution in `chat_context.py`/`routes/chat.py` needs the identical containment check (reject `..`,
   absolute paths, and any resolved path outside `_projects_root()`), not just an exact-match check
   against one specific directory.
2. Make MCP server availability an explicit per-provider allowlist, always — the full-agentic path
   should never default to "inherit every server on the host." At minimum, Plaid/Google/Discord must
   not be reachable from a project/build/development chat unless that specific project/app declares a
   real need for it.
3. Give `mcp__workshop__query` real caller-identity awareness — either scope its digest to the calling
   user's own owned data, or (simplest near-term) stop hard-pinning to the operator and return nothing
   instead of another user's private data.
4. Partition `shared/knowledge/` and `~/notes` by `owner_id`, mirroring `chat_store`'s
   `.runtime/users/<owner_id>/` pattern. Until that lands, the ambient `workshop`-layer knowledge
   injection and note-fence writes should be operator-only, not available to any authenticated chat.
5. Constrain the note-fence write path so a chat can only write into its **own bound scope**, not an
   arbitrary `"scope"` value it emits.
6. Put a hard ceiling on app-persona tool declarations (no Edit/Write/Bash ever, or a centrally
   maintained allowlist) rather than trusting whatever a markdown file says.
7. Close the alias-multiplication gap for chat cwd resolution the same way `470823c` closed it for task
   stores.

**Structural, not urgent but the actual fix underneath all of the above:** a dedicated lower-privilege
OS execution identity for chat-driven agent turns, distinct from the operator's own account — the gap
`security-posture.md` §5a already tracks for the autonomy loop turns out to matter for product-user
chat too, which raises its priority. Current industry practice for multi-tenant AI agent platforms
(researched 2026-08-11) converges on the same shape: provision one sandbox per session/tenant rather
than sharing one execution identity across all of them, and inject credentials at a boundary the
agent's own generated code cannot reach, rather than leaving them in files the same account can read.
Sources: [OWASP Multi-Tenant Security Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Multi_Tenant_Security_Cheat_Sheet.html),
[Blaxel — Multi-tenant isolation for AI agents](https://blaxel.ai/blog/multi-tenant-isolation-ai-agents).

---

## 6b. What the first remediation pass got wrong

Recorded because the failure mode matters more than the individual bugs: **three of the first pass's
fixes computed the right answer and never delivered it**, and each was covered by a test that stopped
short of the place it would have shown up.

1. **MCP default-deny was a no-op on the one path it was written for.** `routes/chat.py` set
   `mcp_selection = []` for a non-operator caller, but the value never left that function:
   `build_streaming.start_turn` had no such parameter, and `_build_cmd`'s full-agentic branch emitted a
   bare `--permission-mode` with no `--strict-mcp-config`, so the CLI inherited every server in
   `~/.claude.json` regardless. The accompanying test (`test_mcp_default_deny.py`) asserted only that
   `caller_is_operator()` returned False. It passed throughout.
2. **The note-fence gate was never run.** `_caller_is_operator` moved to `ownership` and two tests
   still imported it from `routes.chat`; the file failed at *collection*, so the whole module had been
   green-by-absence since the refactor.
3. **Slug validation sat at one call site, not at the join.** `get_provider` screened `project:` keys
   while `ProjectContextProvider.__init__` kept the unchecked `_projects_root() / slug`. A regex at one
   caller is not a containment check, and a second caller re-opens the hole.

The general lesson, worth keeping: **a security test that stops at the decision and never reaches the
argv, the path, or the file cannot see the wiring, and wiring is where all three of these died.**

---

## 7. What's already solid (don't re-litigate)

- Core web auth: fail-closed ownership, registration gate, cookie/session crypto — verified again,
  unchanged from `security-posture.md` §3.
- Per-domain data partitioning for finance, budget, location, health, birds, and chat transcripts is
  real and correctly enforced (`require_user`/`require_domain_owner` + owner-keyed `.runtime` paths).
  `commute` fails closed (403) rather than leaking, just isn't partitioned on disk yet — not a leak,
  a "not multi-user yet" gap.
- All four Critical/High infra findings from the 2026-07-18 audit are fixed (§5).

---

## 8. Remediation shipped — 2026-08-12

Verified by `backend/tests/` (1430 passing; the 6 failures in the tree at the time of writing are an
unrelated in-flight chat-attachments change in `build_streaming.inject` / `write_to_turn`, not this
work). Every item below is pinned by a test that reaches the argv, the resolved path, or the file on
disk — not the predicate that decides it. See §6b for why that distinction is written down.

### Containment

| Was | Now | Test |
|---|---|---|
| `project:<slug>` joined unchecked in `ProjectContextProvider.__init__` | `chat_context.contained_project_dir()` — `resolve()` + `is_relative_to`, so a symlink out of `projects/` is caught too — called from the constructor; `get_provider` also screens the key and catches `ProjectOutsideRoot` into a fall-through to `general` | `test_chat_provider_containment.py` |
| **`agent:<slug>` joined unchecked in `task_scope._agent_scope_dir` and `mkdir(parents=True)`'d** — reachable from `POST /chat/threads/{id}/spawn-task` under plain `require_user`, and `launch_task_run` would then start `project_ralph.sh --dir <that path>`: a detached autonomous run rooted anywhere on the host | slug regex (`fullmatch`, no `$`) + containment under `.runtime/agent-scopes/`; returns `None`, which `root_for_context` already reads as "no task scope" | `test_task_scope_containment.py` |
| `build:<unknown-slug>` fell back to the platform repo as its cwd | constructor propagates `KeyError`; no fallback record | `test_chat_provider_containment.py` |
| `memory_keeper._safe_scope` / `triager._safe_scope` mapped `:`→`-` and left `/` and `..` intact | both defer to `learnings._valid_scope`, which was already correct | — |
| `routes/projects.py:SLUG_RE` used `$` with `.match()`, so `"foo\n"` passed as `"foo"` | `\Z` | `test_task_scope_containment.py` (the analogous case) |

**This finding is the one that matters most in the pass**: the `agent:` branch was strictly worse than
the `project:` traversal that triggered the audit — same defect class, lower auth requirement, and it
ended in process execution rather than a chat cwd.

### Tool and credential surface

- **`general`**: `allowed_tools` is `["mcp__workshop__query"]`. It has no cwd of its own, so it gets no
  filesystem tool at all. (Widened undocumented on 2026-07-18 in `8d3b978`; that is the direct answer
  to "why have I been able to do most things from the workshop scope".)
- **MCP default-deny now actually reaches the CLI.** `mcp_selection` is computed above the build-agent
  early return, threaded through `build_streaming.start_turn` into `meta` (so a turn respawned after a
  restart comes back with the same server surface), and `_build_cmd`'s condition widened to call
  `tool_flags` whenever a selection exists — re-adding the `--permission-mode` that `tool_flags`'
  agentic branch deliberately does not emit. `[]` was always the right representation; it just never
  arrived. Pinned at argv level, including the "never chosen is byte-identical to before" case.
- **App-persona tool ceiling**: `agents_registry._PERSONA_TOOL_CEILING`, filtered in `app_persona()` —
  the single parser every reader goes through. No `Bash`/`Read`/`Write`/`Edit`/`Grep`/`Glob`, and no
  `Task`/`Agent` either (a ceiling that stops `Bash` but allows delegation stops nothing). Drops and
  logs rather than raising; no shipped persona changes.
- **`mcp__workshop__query`** no longer hard-pins the operator: `app_registry` passes the real caller as
  `WORKSHOP_QUERY_OWNER` at spawn time and the subprocess reads it, falling back to the operator only
  when nothing was handed down (a background pass, where that is the correct answer per `ownership.py`).

### Partitioning

- **`~/notes` is per-owner.** `notes_vault.vault_root()` is the chokepoint every read/write/rename/
  delete resolves through: the operator keeps `$WORKSHOP_NOTES_VAULT` / `~/notes` unmoved (real Obsidian
  points at it), everyone else gets `.runtime/users/<owner_id>/notes/`. This was the audit's worst
  concrete leak — every authenticated account could read and overwrite the operator's whole vault, with
  no check to bypass. `test_notes_owner_partition.py` exercises it through the real note operations,
  not just the root.
- **Fence writes stay in the chat's own scope.** The explicit `scope` override on both the ```note```
  and ```learning-note``` fences is operator-only; for anyone else the write still lands, at the scope
  the chat actually occupies. The learning fence was the sibling hole the first pass missed, and it
  writes to a store injected *more* eagerly than notes.
- **`shared/knowledge/`'s instance-wide layers** (`workshop`, the cross-chat work digest, learnings) are
  operator-gated on the ambient-injection paths, including the detached `_distill` task. This is a
  stopgap, not partitioning — see "still open" below.
- `knowledge.scope_for_context` no longer mints `app:<key>` for an unrecognized bare key (which, since
  `scope_dir` mkdirs on demand, meant an arbitrary thread `context_key` created `apps/<key>/knowledge/`
  inside the tracked tree), and `workshop` now names itself instead of falling through to
  `app:workshop` — a third directory for a name that already meant two things.
- `paths.chat_cwd()` replaces three separate `os.path.expanduser("~")` fallbacks. A scope-less turn no
  longer defaults to the account home directory — the one holding `~/.config/clawdbot`,
  `~/.claude.json` and every credential on the box — but to a real, empty, per-owner directory.

### Still open, and honestly so

1. **`shared/knowledge/` is gated, not partitioned.** A second user gets *none* of the workshop
   knowledge layer rather than *their own*. That satisfies "cannot see outside their workshop" and
   fails "has their own workshop". Real partitioning is blocked on something larger: `projects/`,
   `apps/` and `build-agents/` are single-tenant directories with no `owner_id` anywhere, so
   `project:<slug>` knowledge cannot be per-owner until projects themselves are.
2. **`WORKSHOP_CHAT_CWD` is set on this deployment and points into the workshop tree.** The `~`
   fallback is fixed, but the configured value still puts scope-less turns in the repo. Harmless for
   `general` and app personas now that neither has a filesystem tool; an assistant-runtime agent that
   declares one would still land there. This is operator config, not code — deliberately not changed
   here.
3. **A non-operator project/build turn still runs the full built-in toolset under `bypassPermissions`
   at its cwd.** Denying MCP stops it spending the operator's Plaid/Google credentials; it does not
   stop `Read` on `~/.claude.json`. Everything in this pass is application-layer policy, and §5's
   note stands: the actual fix underneath all of it is a dedicated lower-privilege OS execution
   identity for chat-driven turns. That remains unbuilt and is the honest ceiling on all of the above.
4. `POST /chat/threads` still stores an arbitrary `context_key` verbatim. Every consumer is now
   contained, so this is defence-in-depth rather than a live hole, but validating at creation would
   mean one check instead of N.

---

## 9. Ownership, not gating — 2026-08-12 (phase two)

§8 shipped with `shared/knowledge/` **gated** rather than partitioned, and named the reason: projects,
apps and build agents carry no `owner_id` anywhere, so there was nothing for a second user's knowledge
to hang off. That was the wrong conclusion. An unowned substrate is not a reason to gate around the
problem; it is the problem, and everything else in this audit is downstream of it. This phase fixes it.

### The rule that came out of the research

Two kinds of scope, needing opposite treatments:

| | examples | how owners are separated |
|---|---|---|
| **Unqualified** — names the same thing for every account | `workshop`, `role:<r>`, the chat cwd, the notes vault | **by directory.** There is no slug to make unique, so each owner gets their own root. The operator's tracked paths do not move. |
| **Slug-qualified** — names one specific thing | `project:<slug>`, `build:<slug>`, `agent:<slug>` | **by record.** ONE globally unique slug, ONE directory, plus an `owner_id` on the record, checked at scope resolution. |

The second row is the part that is easy to get wrong, and the reason is worth stating plainly:
`project:alpha` is not just a directory name. It is the key that a task store, a knowledge layer, a
learnings file, a latitude lookup, a role-uniqueness check, a port-registry owner string, a worktree
branch name and — in `secrets_store` — the **AAD a project's secrets are encrypted under** are all
keyed by. Giving each owner their own `projects/` while leaving that key global would let two owners
mint the same key: one name, two directories, secrets crossing. That is exactly the alias
multiplication ADR 0014 exists to end, arriving through the back door. So slugs stay globally unique
and ownership is enforced where the record is read. The honest cost is that "alpha is taken" is
observable — the same trade any shared username namespace makes.

### What shipped

- **`services/scope_owner.py`** — `owner_of()` / `may_reach()` / `stamp()`. One place that reads the
  `owner_id` on a project, build-agent or registry-agent record and answers "may this caller reach
  this scope".
- **Enforced at the two chokepoints a chat actually goes through**: `chat_context.get_provider` (which
  degrades to `general`, the same way a refused `development` does) and `task_scope.root_for_context`
  (which returns `None`, already its "no task scope" answer). **This is where it belongs**: every HTTP
  route touching a project, agent, worktree, ticket or knowledge note is mounted operator-only
  (`main.py`'s `_OPERATOR`), so the REST surface was never the hole. Chat is mounted for any
  authenticated user, takes `context_key` verbatim from the client, and turns it into a working
  directory and the full agentic toolset. That was the one door with no ownership question on it.
- **Ownership is not privilege.** `is_operator` does not grant access to a member's project either.
- **`owner_id` stamped at creation** for projects and build agents, so the "an absent `owner_id` reads
  as the operator's" assumption ages out instead of compounding. That assumption can only ever
  over-grant *to the operator*, never to a second account — a second account cannot have created a
  record that predates the field.
- **Per-owner roots** for the unqualified stores: `shared/knowledge/` (the `workshop` and `role:`
  layers), `learnings`, memory-keeper and triager state, the notes vault, the chat cwd.
- **One source of truth for the projects path.** It was spelled in NINE modules under THREE env var
  names — `worktrees.py` read `WORKSHOP_REGISTRY_PATH`, which belongs to the *marketplace* registry, so
  setting one moved the other. Now `paths.projects_root()` / `paths.projects_registry()`.

### The gates came out

This is the point of partitioning rather than gating, and the measure of whether it worked. The
ambient knowledge index, the cross-chat work digest, learnings injection, auto-recall and
compaction-time distillation were all `caller_is_operator()`-gated in §8. They are **unconditional
again** — every account gets them, against their own store. That check survives in exactly one place,
and correctly: MCP server credentials, which are genuinely one host's Plaid, Google and Discord
accounts with no per-user provisioning to partition into.

The note and learning fences moved from operator-only to `may_reach`, which is both safer and less
restrictive: a member may promote a finding into *their own* `workshop`, and may not aim one at
someone else's project.

### Still open after phase two

1. **Background sweeps have no owner axis.** `dispatch_triggers.sh`, `project_ralph.sh`,
   `liveness_watchdog.sh` and `memory_keeper_run.sh` read the global registry, iterate every project,
   and run as one OS user with the operator's `gh` login and OAuth token. They pin the operator id, so
   nothing would flag it. A second user's project must not be swept with the operator's credentials.
   This is the next thing to build, and it is a real gap rather than a theoretical one.
2. **`stores.py`'s `per_owner` flag is unimplemented.** `installed_stores()` returns it and
   `stores.py:305` unpacks and ignores it; `_operator_runtime_dir()` resolves **once at registration**
   to the operator, with a fallback that picks the first digit-named directory in sorted order. So
   `workshop://store/finance/transactions` is pinned to one user's file for the process lifetime. The
   REST path for finance/budget/location/health *is* correctly per-owner; the Store-registry door
   (grants, broker, `export`) is not.
3. **The Plaid MCP is a global-token path around per-owner finance.** `mcp/plaid/server.js` reads one
   `plaid-tokens.json` with no owner parameter. Non-operators are denied MCP wholesale (§8), so it is
   currently closed by a bigger hammer; the real fix is the `WORKSHOP_QUERY_OWNER` treatment.
4. **Single-`domain_owner` ingest paths** (location, health, commute) each need a per-user credential
   before they can be per-user at all. Honest single-owner today, not leaking.
5. **`apps/<key>/` is tracked source shared by everyone**, which is right for the code and wrong for
   `apps/birds/birdnet.db` — one database for all users, though birds does carry a real in-app
   `owner_user_id` + `shared` model on top of it.
6. Everything in §8's "still open" that this phase did not touch: the OS-level execution identity
   (still the honest ceiling under all of it), and `WORKSHOP_CHAT_CWD` pointing into the repo here.
