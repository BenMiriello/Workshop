"""Layer 1 of managed-file-references-and-path-scoping-2026-08-18.md: run a turn
inside a bwrap filesystem view where only its scope is reachable, mounted at a
fixed managed mountpoint.

OFF BY DEFAULT. Nothing changes until `WORKSHOP_SCOPED_EXEC=1` is set in the
backend environment, and flipping it on is an ATTENDED step (it changes how every
turn spawns; see the plan doc section 9, Layer 1). When off, `wrap()` returns the
command unchanged, so importing and calling this is a no-op on the live path.

WHAT IT DOES WHEN ON. The turn's scope root (its cwd) is bind-mounted at
`/workspace`, the child chdir's there, and the read-only system directories it
needs to run (the claude binary at /usr/local, node, libs, certs, DNS) are bound.
Everything else, ~/.config, ~/.ssh, other scopes, other owners' .runtime/users,
is simply absent: not hidden by a rule the agent could probe around, just not in
the mount namespace. Two things follow:

  1. The agent cannot read outside its scope, which is the confinement.
  2. The agent's own cwd IS `/workspace`, so every path its Bash/Read tools print
     is a `/workspace/...` managed path. The real /home/... path does not exist
     inside the sandbox, so it cannot be echoed. This is the half the runtime
     briefing change cannot reach on its own.

WHAT STAYS SHARED. The network namespace is not unshared, so the Anthropic API is
reachable. The journal (stdout/stderr) and the stdin fifo are passed as already
open file descriptors from the backend, so nothing on disk needs binding for them.

The uid layer (running as a dedicated sub-user for escape containment) composes on
top of this: it is `sudo -u <user>` in front of exactly the argv this returns.
That is a separate, root-requiring step and is not done here.
"""
from __future__ import annotations

import os
import shutil

# Where the scope root is mounted inside the sandbox. Fixed on purpose: it is the
# managed path the agent sees and prints, independent of where the scope really
# lives on the host.
MOUNTPOINT = "/workspace"

_FLAG = "WORKSHOP_SCOPED_EXEC"

# System directories bound read-only so the child can execute. /usr/local holds
# the claude binary and its node_modules on this host; /etc carries the CA bundle
# and resolv.conf that TLS and DNS need.
_RO_SYSTEM = ("/usr", "/usr/local", "/bin", "/sbin", "/lib", "/lib64", "/lib32", "/etc")


def enabled() -> bool:
    return os.environ.get(_FLAG) == "1"


def available() -> bool:
    return shutil.which("bwrap") is not None


def wrap(cmd: list[str], workdir: str) -> list[str]:
    """The command to actually spawn for a turn whose scope root is `workdir`.

    When scoped exec is off or bwrap is missing or `workdir` is empty, returns
    `cmd` unchanged (the live path is untouched by default). Otherwise returns a
    bwrap invocation that confines the turn to `workdir` mounted at MOUNTPOINT.

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

    args = [
        bwrap,
        "--die-with-parent",      # the sandbox dies if the backend does
        "--unshare-pid",          # its own PID namespace: cannot see or signal host procs
        "--unshare-ipc",
        "--unshare-uts",
        "--proc", "/proc",
        "--dev", "/dev",
        "--tmpfs", "/tmp",
    ]
    for p in _RO_SYSTEM:
        if os.path.isdir(p):
            args += ["--ro-bind", p, p]
    # The scope, writable, at the fixed managed mountpoint. Bound AFTER the tmpfs
    # so a scope that happens to live under /tmp is still present.
    args += ["--bind", root, MOUNTPOINT, "--chdir", MOUNTPOINT, "--"]
    return args + cmd
