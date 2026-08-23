"""Running a `command` tool: the one declared kind that actually executes.

Design of record: `docs/platform-redesign/tools-are-declared-2026-08-23.md`,
stage 4 of its §5. `services/tool_registry` says a command tool EXISTS; this
says what happens when one is called.

HOW AN AGENT REACHES ONE. Through the workshop MCP server it already has
(`mcp/workshop_query_server.py`), which lists every declared command tool in
`tools/list` beside its own five and dispatches `tools/call` here. No second
server, and no new grant path: the CLI name of a command tool is therefore
`mcp__workshop__<id>`, an ordinary MCP tool name, so it reaches an agent through
exactly the argv `llm_router.tool_flags` already builds (`--mcp-config` plus
`--allowedTools`) and through nothing else. That is the point — doc §4's first
bullet is that this must never grant a tool `tool_flags` did not put on the
argv.

SO DECLARING A TOOL STILL GRANTS NOTHING, and the two tool modes show it. A
full-agentic turn (a maker or project chat) inherits the workshop server from
the CLI's own configuration, so a declared command tool is callable there the
moment it is declared -- inside that turn's sandbox, under that turn's deny
list. A narrow turn only gets what is on its `--allowedTools`, and nothing here
puts a name there: that is a grant decision belonging to `tool_flags`' callers,
and it is still cut afterwards by `agent_authority.narrow_tools` and the
persona ceiling. Widening either to admit user tools is not this module's to do.

CONFINEMENT IS THE EXISTING ONE. The child is spawned through
`scoped_exec.spawn_argv` with `scoped_exec.turn_env`, exactly as a turn is, so a
command tool sees the same bwrap view of the same scope as the turn that called
it, its env values are remapped under the scope root, and the uid and lease
layers compose for free. `spawn_argv` fails CLOSED (`ConfinementUnavailable`)
when it cannot build a sandbox, and that propagates: a command tool runs
confined or it does not run. Nothing here weakens that module and nothing here
takes a path around it.

THE DENY LIST IS APPLIED TWICE, and the two are not redundant:

  * At REGISTRATION (`tool_registry._validated` calls `forbidden_pattern`), so a
    tool whose command line would run something `agent_authority`'s unattended
    deny list forbids is refused when it is DECLARED, with the matching pattern
    named. This is the good error message, delivered to a person who is present.
  * At RUN TIME (`run`), on the RESOLVED argv — after parameter substitution —
    because the registration check is a snapshot. A tool declared before a
    pattern was added, a record hand-written into `.runtime/tools.json`, or a
    forbidden command line assembled out of a parameter value all get past
    registration and none of them get past this. This one is load-bearing.

WHY A COMMAND AND NOT A PYTHON HOOK (doc §2.1, §4). A user tool is a command
line with a typed schema precisely so that confinement and the deny list apply
to it unchanged; an in-process kind would run inside the backend, outside bwrap
and outside every pattern below, and would have to reinvent both. There is
deliberately no such kind and this module is the reason the omission costs
nothing.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
from typing import Any

from services import agent_authority, scoped_exec, tool_registry

log = logging.getLogger("workshop.command_tools")

# A command tool is a tool call, not a job: it answers into a model's turn, so
# it is bounded in time and in how much it may say. Both are deliberately fixed
# in code rather than per-record — a declaration that could name its own timeout
# is a declaration that can hold an agent slot for as long as it likes.
TIMEOUT_S = 120
MAX_OUTPUT_CHARS = 20_000

# `{name}` is the only substitution. A declared argv is a LIST, and a value
# lands in exactly the element that names it — never split, never re-parsed by a
# shell — so a parameter cannot add an argument or a command to the line it is
# passed to.
_PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")

# Shell separators, for reading a `sh -c "..."` argument as the several command
# lines it really is (see `_command_lines`).
_SEPARATORS_RE = re.compile(r"&&|\|\||[;\n|&]")


class ToolInputError(ValueError):
    """The call's arguments do not satisfy the tool's declared parameters.
    Raised BEFORE anything is spawned: a missing required parameter or a value
    of the wrong type is a call that never runs, not a command that runs with a
    hole in it."""


class ToolForbidden(ValueError):
    """The resolved command line matches the unattended deny list. Separate
    from ToolInputError because it is not the caller's mistake to fix by
    passing different arguments -- the declaration itself may not run."""


# --------------------------------------------------------------------------- #
# The deny list, read from agent_authority rather than restated.
# --------------------------------------------------------------------------- #

def _deny_prefixes() -> list[tuple[str, str, bool]]:
    """(pattern, command prefix, is_prefix_match) for the unattended deny list.

    `agent_authority.IRREVERSIBLE_PATTERNS` is written in the CLI's own
    `--disallowedTools` vocabulary, `Bash(git push:*)`, because its first job is
    to be passed to `claude` verbatim. It is READ here rather than copied: a
    pattern added there must bind a command tool the same day, and a second list
    that has to be kept in step is the failure this avoids. `:*` is the CLI's
    "and anything after it", so it becomes a prefix match; a pattern without it
    matches the whole command line exactly.

    A non-`Bash(...)` pattern (a future `Write(...)`, say) is skipped: it names
    a CLI tool, not a command line, and pretending to match it here would be a
    refusal nobody could explain.
    """
    out: list[tuple[str, str, bool]] = []
    for pattern in agent_authority.disallowed_for(unattended=True):
        if not (pattern.startswith("Bash(") and pattern.endswith(")")):
            continue
        body = pattern[len("Bash("):-1]
        is_prefix = body.endswith(":*")
        if is_prefix:
            body = body[: -len(":*")]
        body = body.strip()
        if body:
            out.append((pattern, body, is_prefix))
    return out


def _command_lines(argv: list[str]) -> list[str]:
    """Every command line this argv could actually run.

    Three readings, because a command tool is not obliged to be a bare argv:

      1. The argv joined -- the ordinary case, `["git", "push"]`.
      2. Each individual argument on its own, because `["bash", "-c", "git
         push"]` hides the real command inside one string, and so does any
         wrapper that takes a command as an argument.
      3. Each of those split on shell separators, so `echo hi && git push` is
         two command lines and the second one is seen.

    Deliberately over-inclusive: a literal argument that merely READS like a
    forbidden command (`echo "sudo is not allowed"`) is refused too. That is the
    safe direction for a deny list, and the cost is renaming a string.
    """
    candidates = [" ".join(argv), *argv]
    lines: list[str] = []
    for candidate in candidates:
        for seg in _SEPARATORS_RE.split(candidate):
            seg = " ".join(seg.split())  # collapse whitespace, including newlines
            if seg and seg not in lines:
                lines.append(seg)
    return lines


def _basename_normalized(line: str) -> str:
    """`/usr/bin/sudo -i` -> `sudo -i`. A deny pattern names a command, and an
    absolute path to that command is the same command; matching only the bare
    spelling would make the list bypassable by typing `/bin/sudo`."""
    parts = line.split()
    if not parts:
        return ""
    parts[0] = os.path.basename(parts[0])
    return " ".join(parts)


def forbidden_pattern(argv: list[str] | tuple[str, ...] | None) -> str | None:
    """The deny-list pattern this command line would violate, or None.

    Returns the PATTERN, not a boolean, because both callers have to name it:
    registration puts it in the refusal a person reads, and the run-time check
    puts it in the tool result a model reads. "That is not allowed" without
    saying what rule was hit is the error message this exists to avoid.

    NOT A SANDBOX, and `agent_authority` says so of the same list: a determined
    author can spell a command differently. It stops the ordinary case -- an
    unattended run doing the one thing nobody can take back -- and the sandbox
    below it is what confines the rest.
    """
    items = [str(a) for a in (argv or [])]
    if not items:
        return None
    lines = _command_lines(items)
    for pattern, prefix, is_prefix in _deny_prefixes():
        for line in lines:
            for form in (line, _basename_normalized(line)):
                if form == prefix or (is_prefix and form.startswith(prefix)):
                    return pattern
    return None


# --------------------------------------------------------------------------- #
# Typed inputs: the schema a model sees, and the validation a call must pass.
# --------------------------------------------------------------------------- #

# What a declared `type` means to a JSON-RPC argument. A declared type this does
# not know falls back to string, matching `tool_registry._normalize_params`,
# which also defaults an unreadable type to string rather than dropping the
# parameter.
_JSON_TYPES = {
    "string": "string",
    "text": "string",
    "number": "number",
    "float": "number",
    "integer": "integer",
    "int": "integer",
    "boolean": "boolean",
    "bool": "boolean",
}


def _json_type(declared: str) -> str:
    return _JSON_TYPES.get((declared or "").strip().lower(), "string")


def params_of(tool: tool_registry.Tool) -> list[dict[str, Any]]:
    """The tool's declared parameters, normalized (`tool_registry.ToolParam`)."""
    raw = tool.spec.get("params")
    return [p for p in raw if isinstance(p, dict) and p.get("name")] if isinstance(raw, list) else []


def input_schema(tool: tool_registry.Tool) -> dict[str, Any]:
    """The JSON Schema for this tool's inputs, generated from its typed
    parameter list.

    The record holds a `ToolParam` list rather than a schema blob (the registry
    says why: it is what a person editing a tool fills in, and it generates a
    usable client type), so the schema an MCP client sees is DERIVED here. One
    direction only: nothing reads a hand-written schema back into the record, so
    the two cannot disagree.

    `additionalProperties: False` matches `validate_args` below, which refuses
    an argument the tool did not declare.
    """
    props: dict[str, Any] = {}
    required: list[str] = []
    for p in params_of(tool):
        name = str(p["name"])
        entry: dict[str, Any] = {"type": _json_type(str(p.get("type") or "string"))}
        desc = str(p.get("description") or "").strip()
        if desc:
            entry["description"] = desc
        props[name] = entry
        if p.get("required") is True:
            required.append(name)
    schema: dict[str, Any] = {"type": "object", "properties": props,
                              "additionalProperties": False}
    if required:
        schema["required"] = required
    return schema


def _coerce(name: str, value: Any, json_type: str) -> Any:
    """One argument, checked against its declared type.

    Strict, in the direction that a wrong type is an error rather than a cast:
    a tool that declares an integer and is handed `"rm -rf /"` must fail before
    it runs, and silently stringifying whatever arrived is how a typed schema
    stops being one. The single accommodation is an integer where a number was
    declared, which is the same number.
    """
    if json_type == "string":
        if not isinstance(value, str):
            raise ToolInputError(
                f"{name!r} must be a string, got {type(value).__name__}")
        return value
    if json_type == "boolean":
        if not isinstance(value, bool):
            raise ToolInputError(
                f"{name!r} must be a boolean (true or false), got {type(value).__name__}")
        return value
    if json_type == "integer":
        # bool is a subclass of int in Python; True is not an integer argument.
        if isinstance(value, bool) or not isinstance(value, int):
            raise ToolInputError(
                f"{name!r} must be an integer, got {type(value).__name__}")
        return value
    if json_type == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ToolInputError(
                f"{name!r} must be a number, got {type(value).__name__}")
        return value
    return value  # unreachable: _json_type only produces the four above


def validate_args(tool: tool_registry.Tool, args: dict[str, Any] | None) -> dict[str, Any]:
    """The call's arguments, checked against the declared parameters.

    Refuses an unknown argument, a missing required one, and a value of the
    wrong type -- all BEFORE anything is spawned, which is the whole point of
    the inputs being typed. An unknown argument is an error rather than a
    silently ignored key because a model that misspells a parameter would
    otherwise get a run with a default it did not ask for and no way to see why.
    """
    args = dict(args or {})
    declared = {str(p["name"]): p for p in params_of(tool)}
    unknown = [k for k in args if k not in declared]
    if unknown:
        known = ", ".join(sorted(declared)) or "none"
        raise ToolInputError(
            f"{tool.id!r} does not take {', '.join(sorted(unknown))}; "
            f"its parameters are: {known}")
    values: dict[str, Any] = {}
    for name, p in declared.items():
        if name not in args or args[name] is None:
            if p.get("required") is True:
                raise ToolInputError(f"{tool.id!r} requires {name!r}")
            continue
        values[name] = _coerce(name, args[name], _json_type(str(p.get("type") or "string")))
    return values


def _rendered(value: Any) -> str:
    """A validated value as one argv element. Booleans render lowercase, the
    spelling every shell tool and CLI already reads."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def resolve_argv(tool: tool_registry.Tool, values: dict[str, Any]) -> list[str]:
    """The declared command with its parameters substituted.

    An element that is EXACTLY a placeholder becomes exactly the value (or is
    dropped when an optional parameter was not supplied, so `--flag {x}` style
    optionals do not leave an empty argument behind). A placeholder inside a
    longer string is substituted into it. Either way the result is one element:
    a value never becomes two arguments, and no shell ever sees it, so there is
    no quoting to get wrong.
    """
    out: list[str] = []
    for item in tool.spec.get("command") or []:
        item = str(item)
        whole = _PLACEHOLDER_RE.fullmatch(item)
        if whole:
            name = whole.group(1)
            if name in values:
                out.append(_rendered(values[name]))
            continue  # an unsupplied optional contributes no argument at all
        out.append(_PLACEHOLDER_RE.sub(
            lambda m: _rendered(values[m.group(1)]) if m.group(1) in values else "", item))
    return out


# --------------------------------------------------------------------------- #
# The catalog, from the tool a model is offered to the process that runs.
# --------------------------------------------------------------------------- #

def declared() -> list[tool_registry.Tool]:
    """Every `command` tool this instance can name, whoever declared it.

    Reads the one catalog (`tool_registry.catalog`), so a user tool, an
    app-declared tool and a future platform command tool are the same thing to
    the runner. A record with no command line is skipped: it is a declaration
    someone has not finished, and offering a model a tool that cannot run is
    worse than not offering it.
    """
    return [t for t in tool_registry.catalog()
            if t.kind == "command" and t.spec.get("command")]


def get(tool_id: str) -> tool_registry.Tool | None:
    """The runnable command tool with this id, or None."""
    for tool in declared():
        if tool.id == tool_id:
            return tool
    return None


def _truncated(text: str) -> str:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    return text[:MAX_OUTPUT_CHARS] + f"\n...(truncated at {MAX_OUTPUT_CHARS} characters)"


def run(tool: tool_registry.Tool, args: dict[str, Any] | None, *, workdir: str) -> str:
    """Validate, check the deny list, and run one command tool. Returns the
    text a model sees.

    ORDER IS THE CONTRACT. Inputs are validated first, then the RESOLVED argv is
    checked against the deny list, and only then is anything spawned -- so a
    refused call has run no process at all, and the pattern check sees the exact
    command line that would have run rather than the template it came from.

    `workdir` is the scope this runs in, and it is the caller's: the MCP server
    passes its own cwd, which inside a confined turn IS the scope root at
    `scoped_exec.MOUNTPOINT`. The child gets the same bwrap view as its turn,
    not a wider one.

    Raises ToolInputError / ToolForbidden for a call that must not run, and
    `scoped_exec.ConfinementUnavailable` when the sandbox could not be built --
    all three propagate to the caller, which reports them as a failed tool call.
    A tool that cannot be confined does not run unconfined.
    """
    values = validate_args(tool, args)
    argv = resolve_argv(tool, values)
    if not argv:
        raise ToolInputError(
            f"{tool.id!r} declares no command to run; give it a command line before calling it")

    # RUN-TIME half of the deny list. Not the same check as registration's: this
    # one sees the substituted argv, and it re-reads the patterns as they are
    # NOW, so a tool declared before a pattern existed is refused today.
    pattern = forbidden_pattern(argv)
    if pattern is not None:
        raise ToolForbidden(
            f"{tool.id!r} resolves to a command line the unattended deny list forbids "
            f"({pattern}); it was not run. Irreversible actions are not taken by an "
            "agent -- ask the operator to run it.")

    spawn = scoped_exec.spawn_argv(argv, workdir)
    env = scoped_exec.turn_env(dict(os.environ), workdir)
    log.info("command tool %r running %s", tool.id, argv)
    try:
        proc = subprocess.run(spawn, cwd=workdir, env=env, capture_output=True,
                              text=True, timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return (f"{tool.id}: timed out after {TIMEOUT_S}s and was killed. "
                "Nothing further was read from it.")
    parts = [f"$ {' '.join(argv)}", f"exit {proc.returncode}"]
    if proc.stdout:
        parts.append(proc.stdout.rstrip())
    if proc.stderr:
        parts.append("stderr:\n" + proc.stderr.rstrip())
    if not proc.stdout and not proc.stderr:
        parts.append("(no output)")
    return _truncated("\n".join(parts))


def run_by_id(tool_id: str, args: dict[str, Any] | None, *, workdir: str) -> str:
    """`run`, resolving the id through the catalog first."""
    tool = get(tool_id)
    if tool is None:
        raise ToolInputError(f"no runnable command tool named {tool_id!r}")
    return run(tool, args, workdir=workdir)
