"""What a turn of an app's own assistant does not inherit from the account.

The chat CLI loads, on its own, everything the account it runs under has set up
for coding: the user's global CLAUDE.md, any CLAUDE.md it discovers from the
working directory up, the auto-memory index for that directory, and every hook
and plugin in the user's settings. A build or project chat wants that. An app's
assistant does not: it is a narrow surface over one app's data, and coding notes
reaching it are context it acts on as though it were about the app.

So an app-scoped turn runs with all four switched off, and with the platform's
own instructions in place of the CLI's built-in coding-agent system prompt. Which turns are app
scoped is the provider's declaration (`chat_context.is_app_scoped`), never a
named app. A build turn reaches none of this and is byte-for-byte unchanged.

What is NOT changed here is the working directory, deliberately. A turn is
confined to its cwd (`services/scoped_exec`), and the platform's own MCP server
is a stdio command launched from the backend's tree, so it starts only where
that tree is inside the confinement. Moving the cwd somewhere empty would drop
the server, and every workshop tool with it, without the CLI saying so.
"""
from __future__ import annotations

__all__ = ["CLI_FLAGS", "ISOLATION_ENV", "SUBAGENT_TOOLS", "cli_env",
           "system_prompt_flags", "without_subagents"]

# An EMPTY source list, not "project,local". Under confinement the cwd is
# mounted at a fixed path, and when that cwd is the account's home its
# `.claude/settings.json` is the user's own file read as project settings, so
# naming "project" would load the very hooks this exists to drop. Measured
# against CLI 2.1.287: with "" no settings hook fires, while `--mcp-config`,
# `--allowedTools` and `--permission-mode` all still apply.
CLI_FLAGS: tuple[str, ...] = ("--setting-sources", "")

ISOLATION_ENV: dict[str, str] = {
    "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1",
    "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
}

# Both spellings the CLI accepts for its subagent tool.
SUBAGENT_TOOLS: tuple[str, ...] = ("Agent", "Task")


def cli_env(env: dict) -> dict:
    """`env` with CLAUDE.md loading and auto-memory switched off."""
    return {**env, **ISOLATION_ENV}


def system_prompt_flags(text: str | None) -> list[str]:
    """The flag that makes `text` the turn's whole system prompt.

    `--system-prompt` REPLACES the CLI's default, a ~13.5k-character brief for a
    coding agent in a repository (plus a git-status reminder it adds beside it),
    none of which is true of an app's assistant and all of which a small local
    window pays for on every request. Tool use does not live in that brief: the
    tool definitions travel separately and are identical either way. Measured
    against CLI 2.1.287 with a scripted model, one real app turn per variant:
    the same 39 tools offered, a tool call made, its result read back.

    Sent on every spawn, resumed ones included, because the system prompt is a
    property of the process rather than of the stored session. [] for no text,
    so a turn without one keeps the CLI's default rather than an empty prompt.
    """
    return ["--system-prompt", text] if text else []


def without_subagents(tools: list[str]) -> list[str]:
    """`tools` minus the CLI's subagent tool, order kept."""
    return [t for t in tools if t not in SUBAGENT_TOOLS]
