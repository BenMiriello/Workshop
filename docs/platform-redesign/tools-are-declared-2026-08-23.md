# Tools are declared, and a user can declare one

**Status:** decided in design, not yet built. Written 2026-08-23 from an operator
instruction and a full audit of how capability is granted in the live tree.

**The instruction.** Operator, 2026-08-23:

> We also need tool call definitions to be something that any system can add to but there can be
> undetectable base ones that can only be used not edited obviously since all users systems have them
> but then any user can make any of their own tools.

and, on the screenshot script that prompted it:

> it's dependent on the stack and tools that go with it so better just to build in generalized tools
> and inform the system a default way to configure the rest.

Two asks, and they are the same ask. A capability should be a **declared thing with an owner**, not a
script welded to one stack in one repo. Some declarations are the platform's and cannot be edited.
Anyone can add their own.

---

## 1. What is actually there today

Audited 2026-08-23. This section is the reason the design is shaped the way it is, so read it before
proposing something simpler.

**There is exactly one enforcement point.** `services/llm_router.py:427` `tool_flags()` builds the CLI
argv (`--tools`, `--allowedTools`, `--disallowedTools`, `--permission-mode`, `--mcp-config`). Two
callers: `llm_router.call_cloud` and `build_streaming._build_cmd`. Nothing else can grant or deny a
tool. That is good, and the design must not add a second.

**There are four uncoordinated declaration sources feeding it.**

1. A baseline deny list, `llm_router.py:287`.
2. A role-to-tool derivation table, `agent_authority.py:88` and `:91`, with `narrow_tools()` at `:169`
   enforcing that an agent definition may only narrow what its role permits.
3. Per-provider Python class attributes, `chat_context.py:1506` (`allowed_tools`, `mcp_servers`),
   plus a universal web and subagent grant at `chat_context.py:2119`.
4. YAML frontmatter in agent and app-persona markdown, capped by `_PERSONA_TOOL_CEILING`
   (`agents_registry.py:513`) because "these files are ordinary markdown, edited by agents as well as
   people".

**MCP servers come from two disjoint resolvers** (`app_registry.installed_mcp_servers` for
app-registered specs, `mcp_registry.resolve_specs` for the host's real CLI config), narrowed by a
per-thread filter.

**What does not exist, at all:**

- No tools table, no `.runtime/tools.json`, no tool CRUD route, no tools UI.
- No place carrying a tool's name, description, schema and eligibility together. The only real tool
  metadata in the tree is `mcp/workshop_query_server.py:415`, for one server.
- No way for a user to define a tool at runtime. The closest is hand-editing a markdown file, and even
  the `tools:` field on a roster agent is unreachable through `POST` or `PUT /agents`.
- `GET /agents/tools` returns a hardcoded five-string list (`filesystem`, `google-docs`, `playwright`,
  `ollama`, `web`) in a vocabulary nothing else uses, nothing enforces, and no frontend consumes. It
  is dead and this replaces it.

**Grants are not tools.** `permissions.py` contains zero occurrences of "tool". Grants govern Store
URIs and verbs. `scoped_exec.py` confines what a tool can SEE, not which tools exist. Both are
orthogonal to this and stay that way.

---

## 2. The model

A **Tool** is a named capability an agent may be granted. It has one record shape regardless of how it
is implemented, because the point is that the set is enumerable and ownable.

    id            stable name, the thing an allowlist names
    title         human label
    description   what it does, shown to a person and to a model
    kind          builtin | mcp | command
    provenance    platform | app | user
    spec          kind-specific launch detail
    scopes        where it may be offered
    ceiling       the tool ids it resolves to, which may never widen

### 2.1 Three kinds, because there are genuinely three

- **`builtin`** names a CLI tool the harness already has (`Read`, `Bash`, `WebFetch`). The record adds
  description and eligibility; it does not implement anything.
- **`mcp`** names a tool on an MCP server (`mcp__workshop__search`). The server's own launch spec stays
  where it already lives; this record does not duplicate it.
- **`command`** is the new one, and it is what makes "any user can make their own tools" real: a
  declared command line with a typed input schema, run in the turn's own sandbox.

A fourth kind is deliberately NOT introduced: nothing here executes arbitrary in-process Python. A user
tool is a command with a schema, so confinement and the deny list already apply to it unchanged.

### 2.2 Provenance is the built-in versus user split

- **`platform`** is code-owned, present on every install, and **cannot be edited or deleted through any
  API**. The operator's words: base ones "can only be used not edited". They are a literal tuple in
  source, exactly as `app_registry._CATALOG_FACTORIES` is for apps.
- **`app`** is declared by an installed app's manifest. It arrives and leaves with the app.
- **`user`** is created at runtime, through the API or the UI, and is fully editable and deletable.

**A user tool may not take a platform tool's id.** This is the one place the design deliberately
diverges from `app_registry`, which lets a discovered app shadow a built-in "deliberately and loudly".
Shadowing is right for an app, where replacing the stock Finance app is a legitimate thing to want. It
is wrong here: a platform tool that a user record can quietly redefine is not a base tool, it is a
default, and the instruction says base ones cannot be edited. So a collision is refused at write time
with the conflicting id named.

### 2.3 The registry unifies declarations, it does not add an enforcement point

This is the load-bearing constraint. The registry resolves to the SAME argv builder that exists today:
the four current declaration sources become readers of one enumeration rather than four independent
lists, and `llm_router.tool_flags` stays the only thing that grants anything.

**Narrow, never widen, stays absolute.** `agent_authority.narrow_tools` and `_PERSONA_TOOL_CEILING`
already encode it in two places; the registry gives them one table to narrow against. A user-defined
tool is subject to it like everything else: declaring a tool does not grant it, and a `command` tool
whose command line would run something the unattended deny list forbids is refused at registration
rather than at run time.

---

## 3. Generalized capability, configured per stack

This is the other half of the instruction, and the screenshot script is the worked example of getting
it wrong.

`scripts/shoot.sh` (2026-08-22) hardcodes: Playwright, a Node 18 pin, port 8002, a cookie name, and a
user id. Every one of those is true of this repo on this machine and of nothing else. It closed a real
gap and it should not be the shape the capability keeps.

**The rule: the platform provides the generic verb, and the thing being acted on declares its own
particulars.** That is the same rule already settled for worktree provisioning, where an app declares
its heavy inputs and the platform clones whatever is declared rather than naming `node_modules` in
platform code.

So `preview` becomes a platform `command` tool that takes a target and a viewport, and an app declares
how it is previewed:

    preview:
      origin: http://127.0.0.1:8002      # where a built instance answers
      auth:   session-cookie             # how a headless client authenticates
      viewports: [[390, 800], [1440, 900]]

**Defaults are documented, not hardcoded.** An app that declares nothing gets what its detected stack
implies, and the defaults live in one table with the reasoning next to them, so a new stack is a row.
Nothing about "a web app is previewed in a browser" belongs in a `.sh` file that also knows this
machine's port number.

---

## 4. What this deliberately does not do

- **It does not become a second enforcement point.** If it ever grants a tool that
  `llm_router.tool_flags` did not put on the argv, the design has failed.
- **It does not touch grants or confinement.** Stores and bwrap are orthogonal and correct.
- **It does not let a user widen their own authority.** A user tool is still narrowed by role, by the
  persona ceiling and by the unattended deny list.
- **It does not execute in-process code.** A user tool is a command with a schema.
- **It does not introduce a per-user permission system.** Single operator; the platform/user split here
  is about ownership and editability, not about access control between people.

---

## 5. Build order

1. **The record and the registry.** `services/tool_registry.py`: the record shape, the platform tuple,
   the app-manifest reader, the `.runtime/tools.json` user store, and refusal on a platform-id
   collision. Nothing consumes it yet.
2. **Replace the dead surface.** `GET /agents/tools` serves the real enumeration; the hardcoded
   five-string list and its vocabulary are deleted.
3. **One reader, not four.** `agent_authority`, `chat_context` and the persona ceiling resolve their
   names against the registry rather than against their own private lists. No behaviour change is
   expected; if one appears, the lists disagreed and that is the finding.
4. **`command` tools actually run**, through the existing sandbox, with the deny list applied at
   registration and at run time.
5. **`preview` as the first generalized platform tool**, with the per-app declaration in section 3,
   and `scripts/shoot.sh` reduced to a thin caller so the capability has one implementation.
6. **The UI**: list tools, see which are platform (and that they are not editable), add and edit a
   user tool.
