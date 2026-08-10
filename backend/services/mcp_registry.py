"""Where each MCP server's LAUNCH SPEC comes from, read off the real config.

WHY THIS EXISTS, and why it is separate from `mcp_health`.

`mcp_health` answers "which servers does the CLI see here, and do they work" by
running `claude mcp list`. That is the authoritative enumeration, and it is
enough to SHOW a server. It is not enough to RE-DECLARE one.

Re-declaring is what per-chat enable/disable requires. The CLI's only mechanism
is `--mcp-config <json>` plus `--strict-mcp-config`, and `strict` means exactly
what it says: the turn gets the servers in that JSON and nothing else. So
turning ONE server off means handing the CLI a full spec for every server that
stays on — command, args, env, or url. `claude mcp list` prints a rendered
command line, not a spec; splitting it back into argv would be guesswork the
moment an argument contains a space.

So this module does not parse the CLI's output. It reads the same files the CLI
reads:

  ~/.claude.json          `mcpServers`                        scope: user
  ~/.claude.json          `projects[<cwd>].mcpServers`        scope: local
  <cwd>/.mcp.json         `mcpServers`                        scope: project
  installed_plugins.json  -> <installPath>/.mcp.json          scope: plugin

Verified against this host on 2026-08-10: the four servers `claude mcp list`
reports non-interactively (plaid, google-docs, playwright,
plugin:discord:discord) all resolve here, and the reconstructed
`plugin:discord:discord` command matches the CLI's rendered line byte for byte
after `${CLAUDE_PLUGIN_ROOT}` expansion.

WHAT IT REFUSES TO DO.

**Guess a spec.** A server the CLI lists but no config file explains is
reported with no spec at all, and the caller must treat it as un-isolatable
rather than inventing a command for it. The failure mode being avoided is
specific and silent: strict mode DROPS anything absent from the JSON, so a
fabricated-or-omitted spec does not error, it just removes a server's tools from
the turn and says nothing.

**Rank scopes.** The CLI's own precedence between user/local/project is not
documented here and is not measured, so where two scopes define the same name
this keeps the FIRST one seen in the order above and records the rest in
`shadowed_by` rather than pretending to know which one wins. The UI can then say
"defined twice" instead of picking a side.

**Read the claude.ai connectors.** They are not in any of these files, and they
do not appear in `claude mcp list` non-interactively either (see mcp_health's
header: the backend's env sees four servers, an interactive terminal sees
eight). A turn spawned by this backend cannot reach them, so a screen that
listed them would be describing someone else's session.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

log = logging.getLogger("workshop.mcp_registry")

# The CLI's user-level config. One file, holding both the user scope
# (`mcpServers`) and every project's local scope (`projects[dir].mcpServers`).
CLAUDE_JSON = Path(os.path.expanduser("~/.claude.json"))
PLUGINS_DIR = Path(os.path.expanduser("~/.claude/plugins"))
INSTALLED_PLUGINS = PLUGINS_DIR / "installed_plugins.json"

# Substituted by the CLI inside a plugin's own .mcp.json. Expanding it is not
# interpretation — it is the documented meaning of the token, and the expansion
# is checkable against the command `claude mcp list` prints.
PLUGIN_ROOT_TOKEN = "${CLAUDE_PLUGIN_ROOT}"

SCOPE_USER = "user"
SCOPE_LOCAL = "local"
SCOPE_PROJECT = "project"
SCOPE_PLUGIN = "plugin"


def _read_json(path: Path) -> dict:
    """A config file as a dict. Never raises: a missing or malformed file means
    "this source contributes nothing", which is the truth, and is very different
    from "there are no MCP servers"."""
    try:
        with open(path, "r") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        if isinstance(e, ValueError):
            log.warning("mcp registry: %s is not valid JSON: %s", path, e)
        return {}
    return data if isinstance(data, dict) else {}


def expand_plugin_root(spec: dict, root: str) -> dict:
    """`${CLAUDE_PLUGIN_ROOT}` -> the plugin's install path, everywhere in a spec.

    Recursive over the strings the CLI puts the token in (command, each arg,
    each env value, url). Pure, so the expansion the argv gets is the expansion
    the tests check.
    """
    def walk(v):
        if isinstance(v, str):
            return v.replace(PLUGIN_ROOT_TOKEN, root)
        if isinstance(v, list):
            return [walk(x) for x in v]
        if isinstance(v, dict):
            return {k: walk(x) for k, x in v.items()}
        return v
    return walk(spec)


def _add(out: dict[str, dict], name: str, spec: object, scope: str, source: str) -> None:
    """Record one server, keeping the first definition and noting later ones.

    First-wins with a `shadowed_by` note rather than last-wins, because this
    module does not know the CLI's precedence order and must not imply that it
    does. A duplicate is a fact worth surfacing either way.
    """
    if not name or not isinstance(spec, dict):
        return
    prior = out.get(name)
    if prior is not None:
        prior.setdefault("shadowed_by", []).append({"scope": scope, "source": source})
        return
    out[name] = {"spec": spec, "scope": scope, "source": source}


def plugin_specs() -> dict[str, dict]:
    """Every INSTALLED plugin's MCP servers, named the way the CLI names them.

    `plugin:<plugin>:<server>` — the form `claude mcp list` prints, so these
    join to the health rows by name with no normalisation. Only installed
    plugins are read: the marketplace tree carries a `.mcp.json` for dozens of
    plugins nobody has, and listing those would invent servers.
    """
    out: dict[str, dict] = {}
    installed = _read_json(INSTALLED_PLUGINS).get("plugins")
    if not isinstance(installed, dict):
        return out
    for key, entries in installed.items():
        # "discord@<org>-plugins" -> "discord". The CLI's server prefix uses
        # the plugin name alone, not the marketplace it came from.
        plugin = str(key).split("@", 1)[0]
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            root = entry.get("installPath")
            if not root:
                continue
            path = Path(root) / ".mcp.json"
            servers = _read_json(path).get("mcpServers")
            if not isinstance(servers, dict):
                continue
            for sname, spec in servers.items():
                _add(out, f"plugin:{plugin}:{sname}",
                     expand_plugin_root(spec, str(root)) if isinstance(spec, dict) else spec,
                     SCOPE_PLUGIN, str(path))
    return out


def resolve_specs(cwd: str) -> dict[str, dict]:
    """Every MCP launch spec reachable from `cwd`, keyed by the CLI's own name.

    Each value is `{"spec": {...}, "scope": ..., "source": <path>}`, plus
    `shadowed_by` when more than one source defines the name. Directory-
    dependent, exactly as the CLI is: a project's `.mcp.json` adds servers that
    another directory does not have.
    """
    out: dict[str, dict] = {}
    root = _read_json(CLAUDE_JSON)

    user = root.get("mcpServers")
    if isinstance(user, dict):
        for name, spec in user.items():
            _add(out, name, spec, SCOPE_USER, str(CLAUDE_JSON))

    projects = root.get("projects")
    if isinstance(projects, dict):
        local = (projects.get(str(cwd)) or {}).get("mcpServers") \
            if isinstance(projects.get(str(cwd)), dict) else None
        if isinstance(local, dict):
            for name, spec in local.items():
                _add(out, name, spec, SCOPE_LOCAL, f"{CLAUDE_JSON} (projects/{cwd})")

    project_file = Path(cwd) / ".mcp.json"
    project = _read_json(project_file).get("mcpServers")
    if isinstance(project, dict):
        for name, spec in project.items():
            _add(out, name, spec, SCOPE_PROJECT, str(project_file))

    for name, entry in plugin_specs().items():
        _add(out, name, entry["spec"], entry["scope"], entry["source"])

    return out


def annotate(servers: list[dict], specs: dict[str, dict]) -> list[dict]:
    """Attach `scope`, `source` and `spec_available` to health rows.

    `spec_available` is the one field the toggles depend on: false means this
    server cannot be re-declared, so a turn running a subset cannot include it.
    Saying so is the whole point — the alternative is a switch that appears to
    work and silently removes a server's tools.
    """
    out: list[dict] = []
    for s in servers:
        entry = specs.get(s.get("name", ""))
        row = dict(s)
        row["spec_available"] = entry is not None
        row["scope"] = entry["scope"] if entry else ""
        row["source"] = entry["source"] if entry else ""
        row["shadowed"] = bool(entry and entry.get("shadowed_by"))
        out.append(row)
    return out
