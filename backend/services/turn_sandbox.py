"""The sandbox a chat turn's shell commands run in: Claude Code's own `sandbox`
setting, not one of ours.

A sandboxed command runs without asking anyone, because what it can touch is
bounded by the operating system rather than by a guess at what the command
means: writes reach only the turn's working directory, its temp directory and
the caches below. Reads and outbound network stay open (`*`), so research and
installs work as before; the boundary is the filesystem.

A command that needs the host (a user service, a database, another machine,
or anything on this machine's own localhost, which a Linux sandbox cannot
reach) either matches `EXCLUDED` and runs outside the sandbox, or fails inside
it and is retried outside by the CLI. Both go through the normal permission
flow, where the tool policy answers: on `auto` everything but a delete runs.

Only an `auto` chat gets the sandbox. The CLI approves every sandboxed command
in any mode but Plan, so a chat the operator put on Manual would stop asking
before commands, which is the one thing Manual promises.

OFF unless an installation sets `WORKSHOP_TURN_SANDBOX=on`. On Linux a sandboxed
command cannot reach the host's localhost at all (the CLI forces `NO_PROXY` for
it, and there is no setting to keep the host network), and a refused local
connection carries no sign of the sandbox, so a turn reads it as the service
being down rather than retrying outside. Measured 2026-10-18: 453 of 9,296 chat
commands in a week called a localhost port, and 6 were bare enough for an
`excludedCommands` pattern to match. An installation whose chats reach local
services through tools rather than shell calls can turn it on.

Docs: https://code.claude.com/docs/en/sandboxing
"""
from __future__ import annotations

import os

from services import installation, tool_policy

# Host tools a chat reaches for bare, which can never work inside the sandbox:
# they talk to a user service bus, a container daemon, a database socket or
# another machine. Matched against the whole command line, so a call that
# starts with `cd` stays sandboxed and falls to the retry instead.
EXCLUDED = (
    "systemctl *", "journalctl *", "systemd-run *", "loginctl *",
    "docker *", "podman *", "psql *", "ssh *", "scp *", "rsync *",
)

# Where package managers and build tools keep their caches.
WRITABLE = ("/tmp", "~/.cache", "~/.npm", "~/.local/share/uv", "~/.cargo", "~/go")

ENABLE_ENV = "WORKSHOP_TURN_SANDBOX"


def settings(posture: str | None) -> dict | None:
    """The `sandbox` block for a turn at `posture`, or None for no sandbox."""
    if posture != tool_policy.OPEN or os.environ.get(ENABLE_ENV) != "on":
        return None
    return {
        "enabled": True,
        "failIfUnavailable": True,
        "allowUnsandboxedCommands": True,
        "excludedCommands": [*EXCLUDED, *installation.sandbox_excluded_commands()],
        "filesystem": {"allowWrite": [*WRITABLE, *installation.sandbox_write_paths()]},
        "network": {"allowedDomains": ["*"]},
    }
