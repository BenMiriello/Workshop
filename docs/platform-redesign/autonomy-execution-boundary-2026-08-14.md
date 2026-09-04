# Making the autonomous loop able to work, and giving it a boundary

Status: **plan, not built.** Written 2026-08-14, after the first end-to-end
tier-1 run of the queue proved the loop has never been able to write a file.

## The finding this starts from

`scripts/ralph.sh` invokes `claude -p` with **no permission mode**. The CLI
takes its default, which can stop and ask; nothing unattended can answer. The
worker's `Write` calls are denied, every time, silently.

Measured: asked to create a one-line file, the worker tried `Write` three times
and a Bash redirect once, was denied on all four, wrote "All write operations
require user permission approval ... contradicts the autonomous operation
model" into the project TODO, and exited claiming completion. The reviewer gate
correctly rejected it. `backend/services/model_caps.py` already states the rule
this violates: every mode except `bypassPermissions` needs an approval channel,
and without one the CLI "silently auto-denies every gated call, which is the
worst available outcome".

The one-line fix is a permission mode. The reason this document exists is that
the obvious one-line fix is also the one that makes SEC-A1's blast radius
reachable, and there turn out to be four better options.

## What I got wrong the first time

I wrote that `bypassPermissions` was the only mode that works unattended and
that the deny list would carry the safety. The first half is true. The second
half I asserted without checking, and it is worth stating precisely, because it
changes how bad the option actually is.

Per the permission evaluation order, a tool call passes through six steps:

1. **Hooks** (`PreToolUse`) — can deny outright
2. **Deny rules** — `disallowed_tools`, settings.json
3. **Ask rules** — settings.json
4. **Permission mode**
5. **Allow rules**
6. **`canUseTool` callback**

Steps 1 to 3 run **before** the mode. A `PreToolUse` hook deny and a scoped
deny rule such as `Bash(rm *)` both hold **in every mode, including
`bypassPermissions`**. So our `agent_authority` deny list is genuinely enforced
there, and this repo already runs a `PreToolUse` hook (`egress-guard.py`).

Two things that are NOT true and that I should not have implied:

- **`allowedTools` does not constrain `bypassPermissions`.** Unlisted tools
  fall through to the mode and get approved. `--allowedTools Read` alongside
  bypass still approves `Bash`, `Write` and `Edit`. Only `disallowedTools`
  narrows it.
- A bare deny name (`Bash`) removes the tool from the model's context
  entirely; only a scoped rule (`Bash(rm *)`) is evaluated per call. Those are
  different mechanisms and our list mixes them.

So `bypassPermissions` is not "no controls". It is allow-by-default with a
deny list and a hook. That is a defensible posture and it is still the weakest
of the four.

## The four options

All four are supported by the CLI already on this box (2.1.143 offers
`acceptEdits`, `auto`, `bypassPermissions`, `default`, `dontAsk`, `plan`).

### A. `bypassPermissions`

Allow-by-default. Deny rules and hooks still fire.

- **For:** one flag, works today, nothing else to build.
- **Against:** every tool not explicitly denied is approved, and the deny list
  is a blocklist, which is the weaker shape. SEC-A1 already records that an
  autonomous turn runs as <service-user>, in the docker group, unsandboxed, with full
  tree write and the OAuth token. This is the flag that makes that reachable.

### B. `acceptEdits` + sandboxed Bash

Auto-approves file edits and filesystem commands (`mkdir`, `rm`, `mv`, `cp`,
`sed`), **and only for paths inside the working directory or
`additionalDirectories`**. Paths outside that scope still prompt, which for an
unattended worker means deny. Other Bash falls through.

That path scoping is the part that matters here: it is a containment boundary
we get for free, and it lines up exactly with the worktree isolation that is
already built (`worktrees.allocate_task_worktree`) and already has a local
completion path.

The Bash half is then handled by the OS sandbox below rather than by the
permission mode.

- **For:** a real filesystem boundary rather than a blocklist; matches the
  isolation we already have; no new approval machinery.
- **Against:** non-filesystem Bash still needs an answer, so this is only a
  complete story combined with the sandbox or with option D.

**Measured on this box, CLI 2.1.143.** `claude -p --permission-mode acceptEdits`
was asked to create a one-line file inside the project and did it: exit
`success`, `is_error: false`, file on disk with the right content. That is the
same request that produced four straight denials with no mode set, so the
half of this plan that unblocks the loop is verified rather than cited.

**Not verified: the containment half.** The matching probe, a write to `/tmp`
from a session rooted in the project, was refused by the auto-mode classifier
before it ran, which is a reasonable thing for it to refuse. So "paths outside
the working directory still prompt, and an unattended prompt is a denial" is
still only documented behaviour here. **Phase 1 must confirm it directly**, from
a shell where that probe is permitted, before anything relies on it as a
boundary. If it does not hold, option B collapses into option A and the choice
changes.

### C. `dontAsk` + an explicit allowlist

Converts any prompt into a denial. Documented for precisely this case: "you
want a fixed, explicit tool surface for a headless agent and prefer a hard deny
over silent reliance on `canUseTool` being absent."

- **For:** the strongest shape, an allowlist rather than a blocklist. Fails
  closed by construction, which is the direction the rest of the queue already
  fails.
- **Against:** requires enumerating the tool surface a coding worker needs. A
  bare `Bash` in the allowlist gives back most of what bypass gives, and
  scoping Bash finely enough to be meaningful (`Bash(git status:*)`, ...) is
  likely too tight for general work. Worth measuring, not assuming.

### D. A programmatic approver

`--permission-prompt-tool` routes each gated call to something that answers it.
The Agent SDK spells the same thing `canUseTool`, returning
`{behavior: "allow", updatedInput}` or `{behavior: "deny", message}`.

**We already have a working implementation of this.** `build_streaming.py`
drives the chat's own `claude -p` with `--permission-mode auto
--permission-prompt-tool stdio`, sends the `initialize` control frame, and
answers `can_use_tool` control requests. Its comment records that the
combination was verified against CLI 2.1.143, which is the version on this box.
The chat has had a working approval channel this whole time; the autonomy loop
just never got one.

Beyond allow/deny, the callback can **modify the input before execution**
(scope a path, add a constraint) and can **deny with a message the model
reads**, which turns a refusal into steering rather than a dead end.

- **For:** the policy is our code, in one place, and every decision is loggable
  as a receipt. It is the only option that produces evidence.
- **Against:** most work to build, and it must not become a second place where
  "what may an agent do" is defined. SEC-A2 already warns that scope/touches/
  latitude are advisory and must not be dressed up as a security boundary.

**Do not put a check only in `canUseTool` and call it enforced.** A call
approved at any earlier step never reaches the callback. Checks that must run
on every call belong in a `PreToolUse` hook, which runs first and whose deny
holds in every mode.

## The sandbox, which is orthogonal to all four

Anthropic ships an OS-level sandbox for exactly this problem, and **every
dependency is already installed on this box**: `bwrap`, `socat` and `rg` are
all present.

It uses bubblewrap plus a seccomp filter for the filesystem and syscalls, and
`--unshare-net` plus a `socat`-forwarded proxy for the network, so egress is
filtered by domain allowlist rather than being all-or-nothing. It sandboxes the
Bash tool specifically, which is precisely the hole option B leaves open. It is
configured in settings, not per invocation:

```json
{
  "sandbox": {
    "enabled": true,
    "allowUnsandboxedCommands": false,
    "network": { "allowedDomains": ["*.github.com", "registry.npmjs.org"] }
  }
}
```

`allowUnsandboxedCommands: false` is the strict form: the per-command escape
hatch is ignored and everything must run sandboxed or be named in
`excludedCommands`.

**Two honest caveats.** In April 2026 researchers documented Claude Code
escaping its own bubblewrap sandbox on Linux, so this is defense in depth and
not a hard boundary. And the wider consensus is that a container is not a
sandbox either, since it shares the host kernel; genuine isolation for hostile
code is a microVM. Our threat model is our own agent misbehaving rather than an
attacker, so OS-level primitives are proportionate. Stating it so nobody later
believes this closed SEC-A1.

## The Claude API path, and the decision it forces

The operator asked for a plan for `claude -p` **and** for the Claude API. The
API options are real and one is a genuinely good fit, but they all run into the
same wall, so that goes first.

**Every Claude API option requires `ANTHROPIC_API_KEY` and bills per token.**
The user-scope rules for this system are explicit: subscription only, via
`CLAUDE_CODE_OAUTH_TOKEN`, never `ANTHROPIC_API_KEY`. There is also a standing
note that subscription passthrough is prohibited. So the API path is not a
technical choice that can be made quietly inside a plan; it is a change of auth
posture and a new per-token cost line, and it is the operator's call.

With that stated, three shapes exist:

**Managed Agents.** Anthropic hosts the agent loop *and* a per-session
container where the tools run. Permission policies are first class
(`always_allow` / `always_ask`), an `always_ask` tool pauses the session and
waits for a `user.tool_confirmation` event, and credentials live in vaults and
are substituted at egress so the sandbox never sees them. This is the same
architecture this document is otherwise proposing to assemble by hand, already
built. It also solves SEC-A1 properly, because execution stops happening on
this host at all.

**Self-hosted sandboxes** (`config: {type: "self_hosted"}`). The loop stays on
Anthropic's side, but tool execution moves to a container we control, reached
by an outbound-polling worker. No inbound connection. This is the most
interesting middle path: a real execution boundary, while the work still
happens on our own machine against our own tree. Caveat: `environment_variable`
vault credentials are not supported there, since egress is ours.

**Tool Runner or a manual loop on the Messages API.** Rebuilds the harness we
already have (reviewer gate, leases, receipts, worktrees) from scratch. Not
worth it. Noted so it is visibly considered and rejected.

## Recommendation

Three phases. Phase 1 unblocks autonomy on the subscription with a real
boundary and no new cost; the API decision is deferred to phase 3 where it
belongs.

### Phase 1 — make it able to work, contained (no new cost, no new auth)

1. `--permission-mode acceptEdits` on the worker's `claude -p`, so edits inside
   the tree are approved and edits outside it are not.
2. Turn on the sandboxed Bash tool in settings with
   `allowUnsandboxedCommands: false` and a domain allowlist. Everything it
   needs is installed.
3. Keep the existing `agent_authority` deny list, and audit it for the bare-name
   versus scoped-rule distinction above, since the two behave differently.
4. Run the worker in a worktree, which `acceptEdits` then scopes writes to.
   Worktree mode is already built and C3 gave it a local completion path.
5. Re-run tier 1, including the failure cases the plan doc names, and the one
   that would bite today: confirm a worker commits only its own files on a
   dirty tree.

**Done when** the smoke project's three tickets pass, the deliberately
unsatisfiable one is refused by the reviewer rather than marked done, and no
file outside the worktree is modified.

### Phase 2 — make each decision a receipt

Wire `--permission-prompt-tool` for the worker, reusing the stdio channel
`build_streaming.py` has already proven on this exact CLI version. The policy
lives in one place, each decision is logged against the ticket, and a denial can
carry a message that steers the worker instead of stopping it.

Do not let this become a second definition of agent authority. One policy
module, called by both the hook and the approver.

**Done when** a run's receipt lists the tool calls that were gated and how each
was decided.

### Phase 3 — decide the execution boundary (operator, with SEC-A1)

Phases 1 and 2 make the loop work and make it observable. Neither removes the
fact that an autonomous turn runs as <service-user> in the docker group. That is
SEC-A1 and it is a product decision, now with a concrete menu:

- keep it local and accept the residual risk, with sandbox plus deny list as
  defense in depth
- run the loop as a dedicated lower-privilege sub-user without the docker group
  (SEC-A1's own proposal), or
- move execution off the host via Managed Agents or a self-hosted sandbox,
  which costs an API key and per-token billing.

## What not to do

- Do not enable `bypassPermissions` and treat the deny list as the boundary. It
  is a blocklist, and the modes above give an allowlist shape for the same
  effort.
- Do not put the only authorization check in `canUseTool`. Anything approved
  earlier never reaches it.
- Do not turn the crontab runner on before tier 1 passes. A resident loop turns
  one bad run into a repeating one.
- Do not describe any of this as sandboxing in the security sense in
  operator-facing copy. SEC-A2 exists because that has already happened once.

## Version note

The CLI here is **2.1.143**. Some documented behaviour needs newer builds: MCP
`requiresUserInteraction` needs 2.1.199+, an allow result may omit
`updatedInput` only from 2.1.207+, and plan mode routes file-modifying shell
commands to the callback only from 2.1.212+. Phase 2 should pin the behaviour
it depends on against the installed version rather than the docs.

## Sources

- [Configure permissions (Agent SDK)](https://code.claude.com/docs/en/agent-sdk/permissions)
- [Handle approvals and user input](https://code.claude.com/docs/en/agent-sdk/user-input)
- [Configure the sandboxed Bash tool](https://code.claude.com/docs/en/sandboxing)
- [anthropic-experimental/sandbox-runtime](https://github.com/anthropic-experimental/sandbox-runtime)
- [List of coding agent sandboxes, 2026-05](https://gist.github.com/wincent/2752d8d97727577050c043e4ff9e386e)
- [What the bubblewrap sandbox escape tells us about agent runtime hardening](https://tanayshah.dev/blog/agent-sandbox-runtime-hardening/)
- [Headless Claude Code: when the human leaves the loop](https://ranjankumar.in/claude-code-headless-hooks-human-backstop)
- Managed Agents and self-hosted sandboxes: the `claude-api` skill's
  `shared/managed-agents-*.md`, in particular `managed-agents-tools.md`
  (permission policies, vaults) and `managed-agents-self-hosted-sandboxes.md`.
