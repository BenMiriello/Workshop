"""Every tool this instance can name, with an owner. ENUMERATION, NOT AUTHORITY.

Design of record: `docs/platform-redesign/tools-are-declared-2026-08-23.md`
(stage 1 of its section 5). The operator's instruction is the whole shape:

    "tool call definitions ... something that any system can add to but there
    can be undetectable base ones that can only be used not edited ... then any
    user can make any of their own tools."

WHAT THIS MODULE IS NOT, and this is the load-bearing constraint (doc §2.3, §4).
There is exactly ONE enforcement point for tool capability in this tree:
`services/llm_router.tool_flags()`, which builds the CLI argv (`--tools`,
`--allowedTools`, `--disallowedTools`, `--mcp-config`), called from
`llm_router.call_cloud` and `build_streaming._build_cmd`. Nothing here grants
anything. A tool being in this catalog does not put it on any argv; a tool
missing from it does not take it off one. This is a table with owners, and if it
ever becomes the reason an agent can do something, the design has failed.

So the three registration sources below answer "what tools exist and who owns
each" — nothing more:

  PLATFORM  a literal tuple in source (`PLATFORM_TOOLS`), exactly as
            `app_registry._CATALOG_FACTORIES` is for apps. Code-owned, present
            on every install, and NOT editable or deletable through any API.
            These are the "base ones that can only be used not edited".
  APP       declared by an INSTALLED app's manifest (`services/manifests`), so
            it arrives and leaves with the app.
  USER      created at runtime in `.runtime/tools.json`, shaped and treated
            exactly like `services/installed_apps`' installed-apps registry: one
            JSON file, missing means empty, a fresh clone ships none, and
            editing it is never an edit to platform source.

THE ONE DELIBERATE DIVERGENCE FROM `app_registry` (doc §2.2). A discovered app
may SHADOW a built-in app of the same id, "deliberately and loudly", because
replacing the stock app with an extracted one is a legitimate thing to want.
A user tool may NOT take a platform tool's id. A platform tool that a user
record can quietly redefine is not a base tool, it is a default. So a collision
is REFUSED at write time with the conflicting id named (`ToolConflict`), and a
colliding record hand-written into tools.json is dropped on read with a warning
that names it, rather than being allowed to win.

TOLERANCE IS ASYMMETRIC, on purpose. Reads never raise: a malformed record is
normalized to defaults and an unreadable file reads as empty, so a hand-edited
tools.json can never take the API down (the discipline `services/manifests`
already sets for app metadata). Writes are strict: `create_user_tool` validates
the id and kind and refuses a collision, because that is the moment a person is
present to be told why.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# backend/mcp/workshop_query_server.py — THIS repo's MCP server module, not the
# PyPI `mcp` SDK (which is deliberately not installed; that server implements
# JSON-RPC by hand). Only module-level string constants are read from it, so the
# five workshop tool descriptions below have ONE source: the server that
# actually answers `tools/list`. Duplicating that prose here is how a catalog
# starts describing a tool the server no longer exposes.
from mcp import workshop_query_server as _wqs

log = logging.getLogger("workshop.tool_registry")

KINDS = ("builtin", "mcp", "command")
PROVENANCES = ("platform", "app", "user")

# `scopes` entry meaning "offer this anywhere it is otherwise allowed". The
# narrowing that actually happens still happens elsewhere (agent_authority,
# the persona ceiling, tool_flags); this is eligibility, not permission.
ANY_SCOPE = "*"

# A tool id is what an allowlist names, so it must survive being written into a
# CLI argv unquoted: CLI builtins (`Read`, `WebFetch`), MCP tool names
# (`mcp__workshop__search`), and user ids (`preview`, `deploy.staging`).
_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,63}$")


class ToolConflict(ValueError):
    """A write whose id is already taken by a tool its author does not own.

    Separate from a plain ValueError because the route maps it to 409 and the
    message must name the conflicting id — "that name is taken" with no name in
    it is the failure mode this whole refusal exists to avoid.
    """


class ToolNotEditable(ValueError):
    """An attempt to edit or delete a platform or app tool. Refused with a
    reason rather than silently ignored: a write that returns 200 and changes
    nothing teaches an operator that the record is theirs when it is not."""


@dataclass(frozen=True)
class ToolParam:
    """One typed input a `command` tool accepts.

    A declared parameter list rather than a raw JSON-Schema blob, for two
    reasons. It is what a person editing a tool actually fills in, and it
    generates a usable TypeScript type — an opaque `dict` field renders as
    `Record<string, never>` in the client (see `scripts/check_opaque_fields.py`),
    which is a type that can express nothing at all.
    """

    name: str
    type: str = "string"
    description: str = ""
    required: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "type": self.type,
                "description": self.description, "required": self.required}


@dataclass(frozen=True)
class Tool:
    """A named capability an agent may be granted. One record shape for all
    three kinds, because the point is that the set is enumerable and ownable.

    id          stable name, the thing an allowlist names.
    title       human label.
    description what it does — shown to a person AND to a model.
    kind        builtin | mcp | command (doc §2.1):
                  `builtin` names a CLI tool the harness already has. The record
                    adds description and eligibility; it implements nothing.
                  `mcp` names a tool on an MCP server. The server's launch spec
                    stays where it already lives (`mcp_registry` /
                    `app_registry.installed_mcp_servers`); this does not
                    duplicate it.
                  `command` is a declared command line with typed inputs — what
                    makes "any user can make their own tools" real. There is
                    deliberately NO in-process-Python kind, so confinement and
                    the unattended deny list apply to a user tool unchanged.
    provenance  platform | app | user. Set by the SOURCE that loaded the record,
                never read off the record itself: a hand-edited tools.json
                declaring `"provenance": "platform"` must not thereby become
                uneditable-and-trusted.
    spec        kind-specific launch detail (`server`/`tool` for mcp, `command`
                and `params` for command, `app` for an app-declared record).
    scopes      where it may be offered. `("*",)` means "wherever it is
                otherwise allowed".
    ceiling     the tool ids this resolves to, which may never widen. For a
                builtin or an mcp tool that is itself. For a `command` tool it
                is the CLI tools running it requires, and it defaults to EMPTY:
                declaring a tool grants nothing, so an undeclared ceiling
                resolves to nothing rather than to everything.
    """

    id: str
    title: str
    description: str
    kind: str
    provenance: str
    spec: dict[str, Any] = field(default_factory=dict)
    scopes: tuple[str, ...] = (ANY_SCOPE,)
    ceiling: tuple[str, ...] = ()

    @property
    def editable(self) -> bool:
        """Whether this record can be changed through the API. Provenance is
        the whole answer, and the API enforces it independently — this is the
        flag a UI reads to avoid offering an edit that will be refused."""
        return self.provenance == "user"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "kind": self.kind,
            "provenance": self.provenance,
            "spec": dict(self.spec),
            "scopes": [*self.scopes],
            "ceiling": [*self.ceiling],
            "editable": self.editable,
        }


# --------------------------------------------------------------------------- #
# PLATFORM: a literal tuple in source.
#
# Stage 1: seeded with the tools that GENUINELY EXIST on this instance today.
# Stage 3 (build order, doc §5): the four declaration sources now read the
# named group constants below rather than maintaining their own copies, so a
# name cannot disagree between sources.
#
# The ids ARE the CLI/MCP names, because the id is "the thing an allowlist
# names" and an allowlist here is an exact-match list of exactly these strings.
# --------------------------------------------------------------------------- #

_WORKSHOP_SERVER = _wqs.SERVER_NAME  # "workshop"


def _mcp_id(tool_name: str) -> str:
    """The allowlist name for a tool on the workshop MCP server. The CLI's
    `mcp__<server>__<tool>` spelling is the thing `chat_context` lists and the
    thing `--allowedTools` matches, so it is the id."""
    return f"mcp__{_WORKSHOP_SERVER}__{tool_name}"


PLATFORM_TOOLS: tuple[Tool, ...] = (
    Tool(
        id="WebSearch",
        title="Web search",
        description=(
            "Search the public web and return ranked results. Granted to every "
            "chat: the operator rule is that all agents can search and fetch "
            "the web, on top of whatever their provider declares."
        ),
        kind="builtin",
        provenance="platform",
        ceiling=("WebSearch",),
    ),
    Tool(
        id="WebFetch",
        title="Fetch a web page",
        description=(
            "Fetch one URL and read its content. The other half of the "
            "universal web grant; a search that cannot open what it finds is "
            "half a capability."
        ),
        kind="builtin",
        provenance="platform",
        ceiling=("WebFetch",),
    ),
    Tool(
        id="Agent",
        title="Delegate to a subagent",
        description=(
            "Push a large read or a long search into an isolated context and "
            "bring back the summary. Every chat is TOLD it can delegate "
            "(chat_mode_block, DELEGATION_RULE), so the grant is what makes "
            "that instruction true. `Agent` is the emitted tool name; `Task` is "
            "the older accepted spelling."
        ),
        kind="builtin",
        provenance="platform",
        ceiling=("Agent",),
    ),
    Tool(
        id=_mcp_id(_wqs.TOOL_NAME),
        title="Workshop digest",
        description=_wqs.TOOL_DESC,
        kind="mcp",
        provenance="platform",
        spec={"server": _WORKSHOP_SERVER, "tool": _wqs.TOOL_NAME},
        ceiling=(_mcp_id(_wqs.TOOL_NAME),),
    ),
    Tool(
        id=_mcp_id(_wqs.SEARCH_TOOL_NAME),
        title="Search the workshop",
        description=_wqs.SEARCH_TOOL_DESC,
        kind="mcp",
        provenance="platform",
        spec={"server": _WORKSHOP_SERVER, "tool": _wqs.SEARCH_TOOL_NAME},
        ceiling=(_mcp_id(_wqs.SEARCH_TOOL_NAME),),
    ),
    Tool(
        id=_mcp_id(_wqs.WHERE_IS_TOOL_NAME),
        title="Where does this live",
        description=_wqs.WHERE_IS_TOOL_DESC,
        kind="mcp",
        provenance="platform",
        spec={"server": _WORKSHOP_SERVER, "tool": _wqs.WHERE_IS_TOOL_NAME},
        ceiling=(_mcp_id(_wqs.WHERE_IS_TOOL_NAME),),
    ),
    Tool(
        id=_mcp_id(_wqs.LIST_ATTACHMENTS_TOOL_NAME),
        title="List this chat's uploads",
        description=_wqs.LIST_ATTACHMENTS_TOOL_DESC,
        kind="mcp",
        provenance="platform",
        spec={"server": _WORKSHOP_SERVER, "tool": _wqs.LIST_ATTACHMENTS_TOOL_NAME},
        ceiling=(_mcp_id(_wqs.LIST_ATTACHMENTS_TOOL_NAME),),
    ),
    Tool(
        id=_mcp_id(_wqs.GET_FILE_TOOL_NAME),
        title="Read an upload",
        description=_wqs.GET_FILE_TOOL_DESC,
        kind="mcp",
        provenance="platform",
        spec={"server": _WORKSHOP_SERVER, "tool": _wqs.GET_FILE_TOOL_NAME},
        ceiling=(_mcp_id(_wqs.GET_FILE_TOOL_NAME),),
    ),
    Tool(
        id=_mcp_id(_wqs.SPAWN_TOOL_NAME),
        title="Start a background agent",
        description=_wqs.SPAWN_TOOL_DESC,
        kind="mcp",
        provenance="platform",
        spec={"server": _WORKSHOP_SERVER, "tool": _wqs.SPAWN_TOOL_NAME},
        # The one platform tool that is not offered everywhere: the server adds
        # it to `tools/list` ONLY when WORKSHOP_QUERY_THREAD is bound, because
        # only a real chat has somewhere to post the agent's result back to.
        # That condition is a fact about the tool, so it is recorded as a scope
        # rather than left implicit in the server's `_tool_list`.
        scopes=("chat",),
        ceiling=(_mcp_id(_wqs.SPAWN_TOOL_NAME),),
    ),
    # ---- CLI builtins -------------------------------------------------------
    # These add description and eligibility to tools the harness already has.
    # Names are the CLI's own allowlist vocabulary (verified against the four
    # declaration sources; see named groups below).
    Tool(
        id="Read",
        title="Read a file",
        description="Read the contents of a file from the local filesystem.",
        kind="builtin",
        provenance="platform",
        ceiling=("Read",),
    ),
    Tool(
        id="Grep",
        title="Search file contents",
        description="Search file contents with regular expressions (ripgrep).",
        kind="builtin",
        provenance="platform",
        ceiling=("Grep",),
    ),
    Tool(
        id="Glob",
        title="Find files by pattern",
        description="Find files matching a glob pattern, sorted by modification time.",
        kind="builtin",
        provenance="platform",
        ceiling=("Glob",),
    ),
    Tool(
        id="Write",
        title="Write a file",
        description="Write or overwrite a file on the local filesystem.",
        kind="builtin",
        provenance="platform",
        ceiling=("Write",),
    ),
    Tool(
        id="Edit",
        title="Edit a file",
        description="Make an exact string replacement in a file.",
        kind="builtin",
        provenance="platform",
        ceiling=("Edit",),
    ),
    Tool(
        id="MultiEdit",
        title="Edit a file (multiple changes)",
        description="Make multiple exact string replacements in a file in one call.",
        kind="builtin",
        provenance="platform",
        ceiling=("MultiEdit",),
    ),
    Tool(
        id="Bash",
        title="Run a shell command",
        description="Execute a bash command and return its output.",
        kind="builtin",
        provenance="platform",
        ceiling=("Bash",),
    ),
    Tool(
        id="BashOutput",
        title="Read streaming bash output",
        description="Read buffered output from a long-running bash session.",
        kind="builtin",
        provenance="platform",
        ceiling=("BashOutput",),
    ),
    Tool(
        id="KillShell",
        title="Kill a shell session",
        description="Terminate a running bash shell session by its pid.",
        kind="builtin",
        provenance="platform",
        ceiling=("KillShell",),
    ),
    Tool(
        id="NotebookEdit",
        title="Edit a notebook cell",
        description="Insert, replace, or delete a cell in a Jupyter notebook.",
        kind="builtin",
        provenance="platform",
        ceiling=("NotebookEdit",),
    ),
    Tool(
        id="Task",
        title="Delegate to a subagent (legacy name)",
        description=(
            "Older accepted spelling of Agent: push work into an isolated "
            "context and bring back the summary. Kept for backwards compatibility "
            "with definitions that still use this name."
        ),
        kind="builtin",
        provenance="platform",
        ceiling=("Task",),
    ),
    Tool(
        id="AskUserQuestion",
        title="Ask the user a question",
        description=(
            "Surface a question to the user for an interactive answer. Denied in "
            "async chat turns (no one is watching to reply), so the model must "
            "ask in prose instead."
        ),
        kind="builtin",
        provenance="platform",
        ceiling=("AskUserQuestion",),
    ),
)

PLATFORM_TOOL_IDS: frozenset[str] = frozenset(t.id for t in PLATFORM_TOOLS)

# --------------------------------------------------------------------------- #
# Named groups — stage 3 of the build order (doc §5).
#
# These are the SINGLE DEFINITION of every tool name the four declaration
# sources consume. The sources import these constants rather than maintaining
# their own lists, so a name cannot be misspelled or silently added in one
# place and not another. Each group is a subset of PLATFORM_TOOL_IDS (verified
# by test_tool_registry.TestDeclarationSourcesUseTheRegistry).
#
# consumers:
#   WEB_TOOL_IDS, SUBAGENT_TOOL_ID   → chat_context.WEB_TOOLS / SUBAGENT_TOOL
#   WORKSHOP_TOOLKIT_IDS              → chat_context.WORKSHOP_TOOLKIT
#   GENERAL_ALLOWLIST_IDS             → chat_context._general.allowed_tools
#   READ_TOOL_IDS                     → agent_authority._READ_TOOLS
#   TREE_WRITE_IDS                    → agent_authority._TARGET_TOOLS["tree"]
#   PERSONA_CEILING_IDS               → agents_registry._PERSONA_TOOL_CEILING
#   BASELINE_DISALLOW_IDS             → llm_router.BASELINE_DISALLOWED_TOOLS
# --------------------------------------------------------------------------- #

WEB_TOOL_IDS: tuple[str, ...] = ("WebSearch", "WebFetch")
SUBAGENT_TOOL_ID: str = "Agent"
WORKSHOP_SEARCH_TOOL_ID: str = _mcp_id(_wqs.SEARCH_TOOL_NAME)

# Workshop tools offered to every chat (not spawn, which is chat-scope only).
WORKSHOP_TOOLKIT_IDS: tuple[str, ...] = (
    _mcp_id(_wqs.WHERE_IS_TOOL_NAME),
    _mcp_id(_wqs.LIST_ATTACHMENTS_TOOL_NAME),
    _mcp_id(_wqs.GET_FILE_TOOL_NAME),
)

# Full allowlist for the general/assistant chat provider.
GENERAL_ALLOWLIST_IDS: tuple[str, ...] = (
    _mcp_id(_wqs.TOOL_NAME),
    _mcp_id(_wqs.SEARCH_TOOL_NAME),
    *WORKSHOP_TOOLKIT_IDS,
)

# Tools agent_authority grants to any role that has writes (reads are shared).
READ_TOOL_IDS: tuple[str, ...] = (
    "Read", "Grep", "Glob", _mcp_id(_wqs.SEARCH_TOOL_NAME)
)

# Tools agent_authority grants specifically for "tree" writes.
TREE_WRITE_IDS: tuple[str, ...] = ("Write", "Edit", "MultiEdit", "Bash")

# Hard ceiling for app-persona tool declarations (agents_registry). A persona
# file may not hold any of these — they are too broad for an app assistant that
# runs without a scoped working directory.
PERSONA_CEILING_IDS: frozenset[str] = frozenset({
    "Bash", "BashOutput", "KillShell",
    "Edit", "MultiEdit", "Write", "NotebookEdit",
    "Read", "Glob", "Grep",
    "Task", SUBAGENT_TOOL_ID,
})

# The one tool the harness always denies in async chat turns.
BASELINE_DISALLOW_IDS: tuple[str, ...] = ("AskUserQuestion",)


# --------------------------------------------------------------------------- #
# Normalization — shared by the app-manifest reader and the user store, so a
# tool declared by an app and a tool declared by a person are the same record.
# --------------------------------------------------------------------------- #

def _str(raw: Any, default: str = "") -> str:
    return raw.strip() if isinstance(raw, str) and raw.strip() else default


def _str_tuple(raw: Any) -> tuple[str, ...]:
    if isinstance(raw, str):  # a single string where a list was meant
        raw = [raw]
    if not isinstance(raw, list):
        return ()
    out: list[str] = []
    for item in raw:
        s = _str(item)
        if s and s not in out:
            out.append(s)
    return tuple(out)


def _normalize_params(raw: Any) -> list[dict[str, Any]]:
    """A `command` tool's typed inputs. Anything unusable is dropped, not
    fatal; a param with no name cannot be filled in, so it is not a param."""
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, str):
            item = {"name": item}
        if not isinstance(item, dict):
            continue
        name = _str(item.get("name"))
        if not name:
            continue
        out.append(ToolParam(
            name=name,
            type=_str(item.get("type"), "string"),
            description=_str(item.get("description")),
            required=item.get("required") is True,
        ).to_dict())
    return out


def _normalize_spec(raw: Any, kind: str, *, app_id: str | None) -> dict[str, Any]:
    """Keep the keys the kind actually uses and drop the rest.

    Kind-directed rather than free-form: an `mcp` record carrying a `command`
    would read as an executable declaration that nothing will ever execute.
    """
    raw = raw if isinstance(raw, dict) else {}
    spec: dict[str, Any] = {}
    if app_id:
        spec["app"] = app_id
    if kind == "mcp":
        server = _str(raw.get("server"))
        tool = _str(raw.get("tool")) or _str(raw.get("name"))
        if server:
            spec["server"] = server
        if tool:
            spec["tool"] = tool
    elif kind == "command":
        argv = _str_tuple(raw.get("command") or raw.get("argv"))
        if argv:
            spec["command"] = [*argv]
        params = _normalize_params(raw.get("params") or raw.get("inputs"))
        if params:
            spec["params"] = params
    return spec


def _normalize(raw: Any, *, provenance: str, app_id: str | None = None) -> Tool | None:
    """One record, coerced into the canonical shape. Returns None only when
    there is no usable id, because a tool nothing can name is not a tool.

    Tolerant by design (the `services/manifests` discipline): every other field
    falls back to a default rather than rejecting the record, so a partial or
    hand-mangled entry costs its author a default, not the whole file.

    `provenance` is an ARGUMENT, never read from `raw`. The source that loaded
    the record is the only thing that knows who owns it, and a user file that
    could claim `platform` would make the uneditable tier self-service.
    """
    if isinstance(raw, str):  # a bare id is a legal, if minimal, declaration
        raw = {"id": raw}
    if not isinstance(raw, dict):
        return None
    tool_id = _str(raw.get("id")) or _str(raw.get("name"))
    if not tool_id:
        return None

    kind = _str(raw.get("kind"))
    if kind not in KINDS:
        # `builtin` is the kind that implements nothing — it only adds a
        # description and eligibility to a tool the harness already has. That
        # makes it the right landing place for an unreadable `kind`: the record
        # stays listed and describes nothing it might not be.
        kind = "builtin"

    ceiling = _str_tuple(raw.get("ceiling"))
    if not ceiling and kind in ("builtin", "mcp"):
        # A builtin/mcp record IS its tool; the identity ceiling is the honest
        # default. A `command` record's default stays empty on purpose.
        ceiling = (tool_id,)

    scopes = _str_tuple(raw.get("scopes")) or (ANY_SCOPE,)

    return Tool(
        id=tool_id,
        title=_str(raw.get("title"), tool_id),
        description=_str(raw.get("description")),
        kind=kind,
        provenance=provenance,
        spec=_normalize_spec(raw.get("spec"), kind, app_id=app_id),
        scopes=scopes,
        ceiling=ceiling,
    )


# --------------------------------------------------------------------------- #
# APP: an installed app's manifest.
# --------------------------------------------------------------------------- #

def app_tools() -> list[Tool]:
    """Tools declared by INSTALLED apps, in install order.

    Reads the normalized manifest (`services/manifests`), which is the existing
    metadata layer for "what an app declares about itself" — so a tool arrives
    and leaves with its app and no platform file names any app to make that
    happen. An uninstalled app's declaration is not in the catalog at all.
    """
    from services import installed_apps, manifests

    try:
        declared = manifests.load_manifests()
    except Exception as e:  # a read that never raises; the catalog stays usable
        log.warning("tool registry: could not read app manifests: %s", e)
        return []

    out: list[Tool] = []
    for app_id in installed_apps.installed_app_ids():
        entry = declared.get(app_id) or {}
        for raw in entry.get("tools") or []:
            tool = _normalize(raw, provenance="app", app_id=app_id)
            if tool is None:
                log.info("tool registry: app %r declared a tool with no id; skipped", app_id)
                continue
            out.append(tool)
    return out


# --------------------------------------------------------------------------- #
# USER: .runtime/tools.json.
#
# Shaped exactly like `services/installed_apps`' installed-apps registry: one
# JSON file under `.runtime/` (gitignored), a missing file means none, and
# writing it is never an edit to platform source. A fresh clone ships none.
#
#     { "tools": [ { "id": "...", "kind": "command", ... } ] }
# --------------------------------------------------------------------------- #

REGISTRY_PATH = Path(os.environ.get(
    "WORKSHOP_TOOLS",
    str(Path(__file__).resolve().parent.parent.parent / ".runtime" / "tools.json"),
))


def _read_doc() -> list[Any]:
    """The raw `tools` list off disk. Missing or corrupt file => []. Never
    raises: this read is on the path of every catalog listing."""
    if not REGISTRY_PATH.exists():
        return []
    try:
        data = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        log.warning("tool registry: %s is unreadable; reading as empty", REGISTRY_PATH)
        return []
    raw = data.get("tools") if isinstance(data, dict) else None
    return raw if isinstance(raw, list) else []


def user_tools() -> list[Tool]:
    """The user-declared tools, in file order, every record normalized.

    A record whose id collides with a platform tool is DROPPED here, loudly.
    The API refuses that write, but tools.json is an ordinary file an agent or a
    person can edit directly, and "base ones can only be used not edited" has to
    survive the file being edited by hand — otherwise the refusal is a UI
    nicety rather than a property of the system.
    """
    out: list[Tool] = []
    seen: set[str] = set()
    for raw in _read_doc():
        tool = _normalize(raw, provenance="user")
        if tool is None:
            log.info("tool registry: a record in %s has no id; skipped", REGISTRY_PATH)
            continue
        if tool.id in PLATFORM_TOOL_IDS:
            log.warning(
                "tool registry: user tool %r takes a platform tool's id and is ignored; "
                "a platform tool can be used but not redefined", tool.id)
            continue
        if tool.id in seen:
            continue
        seen.add(tool.id)
        out.append(tool)
    return out


def _write(tools: list[Tool]) -> None:
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {"tools": [_persisted(t) for t in tools]}, indent=2) + "\n"
    tmp = REGISTRY_PATH.with_suffix(REGISTRY_PATH.suffix + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, REGISTRY_PATH)


def _persisted(tool: Tool) -> dict[str, Any]:
    """What a user record looks like on disk. `provenance` is deliberately NOT
    written: it is the source's to know, and a value in the file would be a
    value someone could change."""
    return {
        "id": tool.id,
        "title": tool.title,
        "description": tool.description,
        "kind": tool.kind,
        "spec": dict(tool.spec),
        "scopes": [*tool.scopes],
        "ceiling": [*tool.ceiling],
    }


# --------------------------------------------------------------------------- #
# The catalog.
# --------------------------------------------------------------------------- #

def catalog() -> list[Tool]:
    """Every tool this instance can name: platform, then installed apps, then
    user records.

    Precedence follows that order and the FIRST registration of an id wins, so
    no later source can displace an earlier one. Platform is therefore
    unshadowable by construction as well as by refusal.
    """
    out: list[Tool] = []
    seen: set[str] = set()
    for tool in (*PLATFORM_TOOLS, *app_tools(), *user_tools()):
        if tool.id in seen:
            log.info("tool registry: %r is already declared by a %s tool; the later "
                     "%s declaration is ignored", tool.id,
                     next(t.provenance for t in out if t.id == tool.id), tool.provenance)
            continue
        seen.add(tool.id)
        out.append(tool)
    return out


def get(tool_id: str) -> Tool | None:
    """The catalog record for an id, or None."""
    for tool in catalog():
        if tool.id == tool_id:
            return tool
    return None


def tool_ids() -> list[str]:
    """Just the ids — the vocabulary an allowlist may name."""
    return [t.id for t in catalog()]


# --------------------------------------------------------------------------- #
# Writes. Strict, because a person is present to be told why.
# --------------------------------------------------------------------------- #

def _reserved(tool_id: str) -> Tool | None:
    """The non-user tool holding this id, if any. This is the collision check:
    platform ids are refused because base tools cannot be redefined, and app
    ids are refused because a user record that silently loses to an installed
    app would be a write that appears to succeed and does nothing."""
    if tool_id in PLATFORM_TOOL_IDS:
        return next(t for t in PLATFORM_TOOLS if t.id == tool_id)
    for tool in app_tools():
        if tool.id == tool_id:
            return tool
    return None


def _validated(tool_id: str, body: dict[str, Any]) -> Tool:
    if not _ID_RE.match(tool_id or ""):
        raise ValueError(
            f"{tool_id!r} is not a usable tool id: a tool id starts with a letter and "
            "continues with letters, digits, '_', '.', ':' or '-' (max 64 characters), "
            "because it is written into an allowlist verbatim")
    kind = _str(body.get("kind"))
    if kind and kind not in KINDS:
        raise ValueError(f"{kind!r} is not a tool kind; expected one of {', '.join(KINDS)}")
    tool = _normalize({**body, "id": tool_id}, provenance="user")
    if tool is None:  # unreachable: the id is validated above
        raise ValueError(f"{tool_id!r} could not be read as a tool record")
    return tool


def _command_denied_by_pattern(command: list[str]) -> str | None:
    """The first IRREVERSIBLE_PATTERNS entry the command matches, or None.

    Lazy-imports agent_authority to avoid the circular import that would arise
    from a module-level import (agent_authority imports tool_registry).

    Pattern format: `Bash(<prefix>:*)`. A command list is joined with spaces
    and checked for a prefix match, so `["git", "push", "origin"]` matches
    `"Bash(git push:*)"`. Applied at REGISTRATION (refuse here, with the
    pattern named) and again at RUN TIME inside the execution path (doc §2.3):
    a tool declared before a pattern was added cannot slip through.
    """
    if not command:
        return None
    from services.agent_authority import IRREVERSIBLE_PATTERNS  # lazy: no circular import
    cmd_str = " ".join(str(s) for s in command)
    for pattern in IRREVERSIBLE_PATTERNS:
        if pattern.startswith("Bash(") and pattern.endswith(":*)"):
            prefix = pattern[5:-3]
            if cmd_str == prefix or cmd_str.startswith(prefix + " "):
                return pattern
    return None


def create_user_tool(tool_id: str, body: dict[str, Any]) -> Tool:
    """Declare a user tool. Refuses a platform or app id, naming it.

    Declaring a tool GRANTS NOTHING (doc §2.3): the record is an entry in this
    enumeration, and what any agent may actually call is still decided by
    `llm_router.tool_flags` and narrowed by role and by the persona ceiling.
    """
    tool = _validated(tool_id, body)
    held = _reserved(tool.id)
    if held is not None:
        raise ToolConflict(
            f"{tool.id!r} is already a {held.provenance} tool and cannot be redefined; "
            "choose a different id")
    existing = user_tools()
    if any(t.id == tool.id for t in existing):
        raise ToolConflict(f"{tool.id!r} is already a user tool; edit it instead of creating it")
    # DENY-LIST AT REGISTRATION (doc §2.3). A command tool whose argv matches
    # IRREVERSIBLE_PATTERNS is refused here, with the matching pattern named, so
    # the author knows exactly which rule it hit and can choose a different command.
    if tool.kind == "command":
        cmd = list(tool.spec.get("command") or [])
        bad = _command_denied_by_pattern(cmd)
        if bad:
            raise ValueError(
                f"the command {cmd!r} matches the irreversible-action deny-list "
                f"pattern {bad!r}; user tools are subject to the same fence as "
                "unattended runs — choose a command that does not match it")
    _write([*existing, tool])
    return tool


def update_user_tool(tool_id: str, body: dict[str, Any]) -> Tool:
    """Replace a user tool's record. Raises ToolNotEditable for a platform or
    app tool and KeyError when nothing holds the id."""
    held = _reserved(tool_id)
    if held is not None:
        raise ToolNotEditable(
            f"{tool_id!r} is a {held.provenance} tool: it can be used but not edited")
    existing = user_tools()
    if not any(t.id == tool_id for t in existing):
        raise KeyError(tool_id)
    tool = _validated(tool_id, body)
    # Deny-list check on update too: a re-registration to a forbidden command
    # must be refused with the same fence as the original declaration.
    if tool.kind == "command":
        cmd = list(tool.spec.get("command") or [])
        bad = _command_denied_by_pattern(cmd)
        if bad:
            raise ValueError(
                f"the command {cmd!r} matches the irreversible-action deny-list "
                f"pattern {bad!r}")
    _write([tool if t.id == tool_id else t for t in existing])
    return tool


def delete_user_tool(tool_id: str) -> bool:
    """Remove a user tool. Raises ToolNotEditable for a platform or app tool;
    returns False when nothing holds the id."""
    held = _reserved(tool_id)
    if held is not None:
        raise ToolNotEditable(
            f"{tool_id!r} is a {held.provenance} tool: it can be used but not deleted")
    existing = user_tools()
    remaining = [t for t in existing if t.id != tool_id]
    if len(remaining) == len(existing):
        return False
    _write(remaining)
    return True
