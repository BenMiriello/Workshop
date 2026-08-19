"""Layer 1 of managed-file-references-and-path-scoping-2026-08-18.md: run a turn
inside a bwrap filesystem view where only its scope is reachable, mounted at a
fixed managed mountpoint.

CONFINED BY DEFAULT. Confinement is the floor, not an opt-in: `enabled()` is on
wherever the sandbox actually works, and access beyond the scope is a GRANT (the
binds below), never the absence of a sandbox. `WORKSHOP_SCOPED_EXEC=0` is an
escape hatch that forces it off; `=1` forces it on; unset means "on where bwrap
functions", which degrades safely on a host that cannot namespace rather than
breaking every turn. Only a turn WITH a cwd (a build/dev turn) is wrapped; a
regular chat turn passes no cwd and is untouched.

WHAT IT DOES WHEN ON. The turn's cwd (its scope root) is bind-mounted at
`/workspace`, the child chdir's there, and the read-only system directories it
needs to run (the claude binary at /usr/local, node, libs, certs, DNS) are bound.
Everything else, ~/.ssh, other scopes, other owners' .runtime/users, is simply
absent: not hidden by a rule the agent could probe around, just not in the mount
namespace. Two things follow:

  1. The agent cannot read outside its scope or its grants, which is the confinement.
  2. The agent's own cwd IS `/workspace`, so every path its Bash/Read tools print
     is a `/workspace/...` managed path. The real /home/... path does not exist
     inside the sandbox for the scope, so it cannot be echoed.

THE GRANT SET (`_grants`). A build/dev turn operates the platform: it runs
`apply.sh` (which talks to the systemd user manager), reads instance config, and
commits with git. Those live outside the scope, so they are granted as explicit
binds at their real paths. This is the capability model: the floor is the scope,
and each thing beyond it is named. A grant is the trusted-role widening, not a
hole in the sandbox; a plain chat turn gets none of it.

WHAT STAYS SHARED. The network namespace is not unshared, so the Anthropic API is
reachable. The journal (stdout/stderr) and the stdin fifo are passed as already
open file descriptors from the backend, so nothing on disk needs binding for them.

NOT `--die-with-parent`. The turn is spawned detached (start_new_session) so it
survives a backend restart, which is the platform's "a restart is a pause, not a
break" guarantee. `--die-with-parent` would kill the sandbox the instant the
backend exits and defeat that, so it is deliberately absent: an orphaned sandbox
is reparented to init and adopted back, exactly as the un-sandboxed turn was.

The uid layer (running as a dedicated sub-user for escape containment) composes on
top of this: it is `sudo -u <user>` in front of exactly the argv this returns.
That is a separate, root-requiring step and is not done here.
"""
from __future__ import annotations

import os
import shutil
import subprocess

# Where the scope root is mounted inside the sandbox. Fixed on purpose: it is the
# managed path the agent sees and prints, independent of where the scope really
# lives on the host.
MOUNTPOINT = "/workspace"

_FLAG = "WORKSHOP_SCOPED_EXEC"

# System directories bound read-only so the child can execute. /usr/local holds
# the claude binary and its node_modules on this host; /etc carries the CA bundle
# and resolv.conf that TLS and DNS need.
_RO_SYSTEM = ("/usr", "/usr/local", "/bin", "/sbin", "/lib", "/lib64", "/lib32", "/etc")

_functional_cache: bool | None = None


def available() -> bool:
    return shutil.which("bwrap") is not None


def functional() -> bool:
    """True if bwrap can actually create a user namespace on this host. Cached:
    a trivial sandbox is spawned once and the result reused, so the default-on
    path degrades safely (returns False, no sandbox) on a kernel without
    unprivileged user namespaces rather than failing every turn at spawn."""
    global _functional_cache
    if _functional_cache is not None:
        return _functional_cache
    bwrap = shutil.which("bwrap")
    if not bwrap:
        _functional_cache = False
        return False
    try:
        r = subprocess.run(
            [bwrap, "--unshare-user", "--unshare-pid", "--ro-bind", "/usr", "/usr",
             "--proc", "/proc", "--", "/bin/true"],
            capture_output=True, timeout=10,
        )
        _functional_cache = r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        _functional_cache = False
    return _functional_cache


def enabled() -> bool:
    """Confined by default where the sandbox works; `=0` forces off, `=1` forces
    on. Unset falls back to the functional probe so a host that cannot namespace
    is not broken."""
    v = os.environ.get(_FLAG)
    if v == "0":
        return False
    if v == "1":
        return True
    return functional()


def _cli_root() -> str | None:
    """Two directories up from wherever `claude` resolves on PATH.

    `_RO_SYSTEM` covers a package-manager install under /usr or /usr/local, but
    an nvm-managed Node puts `claude` (and the npm package it's a symlink into,
    lib/node_modules/@anthropic-ai/claude-code/) under ~/.nvm/versions/node/vX.Y.Z/
    — nowhere in `_RO_SYSTEM`. Binding just the `bin/` directory isn't enough:
    the `claude` entry there is a symlink to `../lib/node_modules/.../claude.exe`,
    and that relative target only resolves if the whole version directory is
    bound as one subtree matching its real layout. Two `dirname`s off the
    resolved path (before following any symlink) lands on exactly that
    directory for an nvm install, and lands on an already-`_RO_SYSTEM`-covered
    path for a system one — so this is a no-op there, not a special case.

    Mirrors CLAUDE_CLI's own resolution (llm_router._claude_env) so both agree
    on which binary a turn actually execs.
    """
    cli = os.environ.get("CLAUDE_CLI") or shutil.which("claude")
    if not cli or not os.path.isabs(cli):
        return None
    root = os.path.dirname(os.path.dirname(cli))
    return root if os.path.isdir(root) else None


def _grants() -> list[tuple[str, str]]:
    """(mode, path) binds a build/dev turn needs beyond its scope, each at its
    REAL path so a tool expecting an absolute location still finds it. Skipped
    silently when a path is absent, so the set is portable across hosts.

      - the XDG runtime dir: the systemd --user socket + session bus, so
        `systemctl --user` (apply.sh's restarts) works.
      - ~/.config/workshop: instance.env and friends, read-only.
      - ~/.claude: the CLI's home (hooks, credentials fallback, file history).
      - ~/.gitconfig + ~/.config/git: identity for a commit.
      - the CLI's own install root, when it isn't already under _RO_SYSTEM
        (see `_cli_root`) — without it, execvp fails before the sandbox does
        anything else: the binary it's told to run isn't in its own view.
    """
    home = os.path.expanduser("~")
    uid = os.getuid()
    xdg = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{uid}"
    candidates: list[tuple[str, str]] = [
        ("rw", xdg),
        ("ro", os.path.join(home, ".config", "workshop")),
        ("rw", os.path.join(home, ".claude")),
        ("rw", os.path.join(home, ".claude.json")),  # CLI settings + MCP config
        ("ro", os.path.join(home, ".gitconfig")),
        ("ro", os.path.join(home, ".config", "git")),
    ]
    cli_root = _cli_root()
    if cli_root and not any(cli_root == r or cli_root.startswith(r + "/") for r in _RO_SYSTEM):
        candidates.append(("ro", cli_root))
    return [(mode, p) for mode, p in candidates if p and os.path.exists(p)]


def _remap(arg: str, root: str) -> str:
    """Rewrite a scope-root path to the mountpoint inside one argv string. Matches
    `<root>/...` and a bare `<root>`, and leaves any path outside the root (a
    granted location, a system path) untouched, since those are bound at their
    real locations."""
    if not isinstance(arg, str):
        return arg
    arg = arg.replace(root + "/", MOUNTPOINT + "/")
    if arg == root:
        arg = MOUNTPOINT
    return arg


def wrap(cmd: list[str], workdir: str) -> list[str]:
    """The command to actually spawn for a turn whose scope root is `workdir`.

    When scoped exec is off or bwrap is missing or `workdir` is empty, returns
    `cmd` unchanged. Otherwise returns a bwrap invocation that confines the turn
    to `workdir` mounted at MOUNTPOINT, plus the grant binds.

    The caller keeps passing the REAL `workdir` as the subprocess cwd: that is
    where bwrap itself starts, before it enters the namespace. bwrap's own
    --chdir puts the child at MOUNTPOINT.
    """
    if not enabled() or not workdir:
        return cmd
    bwrap = shutil.which("bwrap")
    if not bwrap:
        return cmd
    root = os.path.realpath(workdir)

    # Remap any argv path under the scope root to the mountpoint. The turn's own
    # command carries absolute host paths that only exist inside the sandbox at
    # /workspace: most importantly the workshop MCP server's launch spec (its
    # python interpreter, its server script, its PYTHONPATH), embedded as JSON in
    # `--mcp-config`. Without this the server cannot start and the in-app agent
    # silently loses its search tools. Replacing `<root>/` and a bare `<root>`
    # covers the command, args and env in one pass, and never touches a granted
    # path (those live outside the root, at their real locations).
    cmd = [_remap(a, root) for a in cmd]

    # NOT --unshare-pid. A separate PID namespace makes systemd's D-Bus reject the
    # peer (SO_PEERCRED carries a pid from another namespace), so `systemctl
    # --user` fails with "Failed to connect to bus", and apply.sh's restarts are
    # what a dev turn exists to run. Process/signal isolation from other clawd-a
    # procs is the job of the uid sub-user layer (SEC-A1), not this filesystem
    # view; not unsharing the PID namespace here is no worse than today's
    # un-sandboxed turn, which shares it too. The confinement this layer delivers
    # is filesystem: only the scope and its grants are in the mount namespace.
    args = [
        bwrap,
        "--unshare-ipc",
        "--unshare-uts",
        "--proc", "/proc",
        "--dev", "/dev",
        "--tmpfs", "/tmp",
    ]
    for p in _RO_SYSTEM:
        if os.path.isdir(p):
            args += ["--ro-bind", p, p]
    # The grant set: the few things beyond the scope a build/dev turn needs to
    # operate, each at its real path.
    for mode, p in _grants():
        args += (["--bind", p, p] if mode == "rw" else ["--ro-bind", p, p])
    # The scope, writable, at the fixed managed mountpoint. Bound AFTER the tmpfs
    # so a scope that happens to live under /tmp is still present.
    args += ["--bind", root, MOUNTPOINT, "--chdir", MOUNTPOINT, "--"]
    return args + cmd
