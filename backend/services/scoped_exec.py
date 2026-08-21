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

THE UID LAYER ([SEC-A1], `spawn_argv`/`turn_env`/`signal_pg`). Composes on top of
the bwrap view above: `sudo -n -E -u <user> -- <wrapper> run <the wrap() argv>`.
Gated by its own flag (`WORKSHOP_TURN_USER`, unset = off) separate from
`WORKSHOP_SCOPED_EXEC`, because a missing sudoers rule must fail the turn, never
silently fall back to running as the host user — the opposite posture from
`enabled()`'s degrade-safely default. `-n` (no password prompt) is load-bearing:
without it, a misconfigured sudoers makes sudo block on stdin, which is the
turn's fifo (opened O_RDWR so it never EOFs) — the turn would hang forever
holding a slot instead of failing visibly. `-E` plus `SETENV:` in the sudoers
rule is load-bearing too: `env_reset` is sudo's default and would strip
`CLAUDE_CODE_OAUTH_TOKEN` and every `WORKSHOP_*` var the child needs.

Creating the account and the sudoers rule needs root and is not something an
agent turn can do to itself: `scripts/setup_turn_user.sh` is what the operator
runs once, out of band. See the design doc's uid-layer section for the full
mechanism, the `~/.claude` ownership resolution, and why a turn scoped to the
platform's own repo (`WORKSHOP_ROOT`) is carved out of the uid layer for now
(`systemctl --user` cannot cross a uid boundary, and `apply.sh` is how a change
here reaches the operator).
"""
from __future__ import annotations

import os
import pwd
import shutil
import subprocess

# Where the scope root is mounted inside the sandbox. Fixed on purpose: it is the
# managed path the agent sees and prints, independent of where the scope really
# lives on the host.
MOUNTPOINT = "/workspace"

_FLAG = "WORKSHOP_SCOPED_EXEC"

# The uid layer's own flag and the one binary its sudoers rule may run. Kept as
# constants (not derived) because the sudoers file and this module must agree
# on the exact path without either reading the other.
_USER_FLAG = "WORKSHOP_TURN_USER"
TURN_WRAPPER = "/usr/local/libexec/workshop/turn-exec"


class ConfinementUnavailable(RuntimeError):
    """Filesystem confinement could not be established for a turn, so the turn
    must NOT run. This is the fail-CLOSED contract: a turn is confined to its
    scope or it does not run at all -- it is NEVER spawned with host filesystem
    access. Raised by `spawn_argv` (the turn-spawn boundary) whenever `wrap()`
    could not build a real bwrap sandbox: bwrap missing, unprivileged user
    namespaces unavailable, no scope root, or the confinement flag forced off.
    The caller (build_streaming) turns this into a refused turn with a visible
    error, exactly as it already does for `TurnUserUnavailable`.

    Confinement being unavailable used to mean the turn ran UNCONFINED (`wrap()`
    returned the command unchanged) -- a fail-open posture that ran every turn
    with full host access whenever the sandbox could not initialise (e.g. the
    2026-08-21 probe bug that disabled it host-wide). That is the exact misdesign
    this class exists to make impossible: the walls dropping is not a degraded
    mode, it is a refusal."""


class TurnUserUnavailable(RuntimeError):
    """WORKSHOP_TURN_USER is set but the uid layer cannot actually run: the
    account, the wrapper, or sudo itself is missing. Raised rather than
    degrading to an un-isolated spawn -- see the module docstring on why this
    flag fails closed where `enabled()` fails open."""

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
        # Mirror the binds wrap() actually uses. The old probe bound only /usr and
        # then exec'd /bin/true: on a usrmerge host the dynamic linker lives under
        # /lib64 (left unbound here), so execvp failed with a misleading ENOENT and
        # the probe declared the sandbox dead on a host whose kernel fully supports
        # unprivileged user namespaces (measured 2026-08-21: userns available, probe
        # under-bound, so every turn ran UNCONFINED). Bind the same _RO_SYSTEM set
        # the real confinement binds, so the probe passes iff a real turn would run.
        # --unshare-pid is dropped to match wrap(), which deliberately keeps the
        # shared PID namespace so systemd --user (apply.sh's restarts) still works.
        args = [bwrap, "--unshare-user", "--proc", "/proc", "--dev", "/dev"]
        for p in _RO_SYSTEM:
            if os.path.isdir(p):
                args += ["--ro-bind", p, p]
        args += ["--", "/bin/true"]
        r = subprocess.run(args, capture_output=True, timeout=10)
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


def _grants(root: str | None = None) -> list[tuple[str, str, str]]:
    """(mode, src, dest) binds a build/dev turn needs beyond its scope. `dest`
    equals `src` for every grant except the two `~/.claude` policy files under
    the uid layer, which land inside the turn-user's own home instead of
    the host user's (see below). Skipped silently when `src` is absent, so the set
    is portable across hosts.

      - the systemd --user socket + the session bus (NOT the whole XDG runtime
        dir — see below), so `systemctl --user` (apply.sh's restarts) works.
      - ~/.config/workshop: instance.env and friends, read-only.
      - ~/.claude, ~/.claude.json, ~/.gitconfig, ~/.config/git: the CLI's home
        and commit identity, when no uid layer is running this turn (see below).
      - the CLI's own install root, when it isn't already under _RO_SYSTEM
        (see `_cli_root`) — without it, execvp fails before the sandbox does
        anything else: the binary it's told to run isn't in its own view.

    THE XDG BIND WAS TOO WIDE. It bound the whole runtime dir read-write, which
    also contains `docker.sock` (owned by the host user's group, mode 660, and
    the host user is a member of that group) -- reachable from inside a turn that
    Layer 1's own acceptance criterion says should not reach the docker socket
    at all. `systemctl --user` needs exactly two things under there: the
    private socket at `<xdg>/systemd` and the session bus at `<xdg>/bus`
    (confirmed against this host's own `DBUS_SESSION_BUS_ADDRESS`). Binding
    only those two closes the socket without touching what apply.sh needs.

    ~/.claude IS NOT BOUND WHEN A UID LAYER IS RUNNING THIS TURN. A bind mount
    does not change ownership, so binding a host-user-owned, mode-0700 home
    into a turn running as a different uid grants nothing there anyway --
    except `.credentials.json` (mode 0600), which a looser directory mode
    would have made reachable. See `turn_home()` / the design doc's uid-layer
    section for the replacement: a dedicated home for the turn-user, with only
    the two world-readable policy files (`CLAUDE.md`, `settings.json`) bound in
    from the host user's real `~/.claude`.
    """
    home = os.path.expanduser("~")
    uid = os.getuid()
    xdg = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{uid}"
    candidates: list[tuple[str, str, str]] = [
        ("rw", os.path.join(xdg, "systemd"), os.path.join(xdg, "systemd")),
        ("rw", os.path.join(xdg, "bus"), os.path.join(xdg, "bus")),
        ("ro", os.path.join(home, ".config", "workshop"), os.path.join(home, ".config", "workshop")),
    ]
    user = turn_user(root)
    if user is None:
        candidates += [
            ("rw", os.path.join(home, ".claude"), os.path.join(home, ".claude")),
            ("rw", os.path.join(home, ".claude.json"), os.path.join(home, ".claude.json")),
            ("ro", os.path.join(home, ".gitconfig"), os.path.join(home, ".gitconfig")),
            ("ro", os.path.join(home, ".config", "git"), os.path.join(home, ".config", "git")),
        ]
    else:
        thome = turn_home()
        candidates += [
            ("rw", thome, thome),
            ("ro", os.path.join(home, ".claude", "CLAUDE.md"),
             os.path.join(thome, ".claude", "CLAUDE.md")),
            ("ro", os.path.join(home, ".claude", "settings.json"),
             os.path.join(thome, ".claude", "settings.json")),
        ]
    cli_root = _cli_root()
    if cli_root and not any(cli_root == r or cli_root.startswith(r + "/") for r in _RO_SYSTEM):
        candidates.append(("ro", cli_root, cli_root))
    return [(mode, s, d) for mode, s, d in candidates if s and os.path.exists(s)]


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


def wrap(cmd: list[str], workdir: str, *, die_with_parent: bool = False) -> list[str]:
    """The command to actually spawn for a turn whose scope root is `workdir`.

    When scoped exec is off or bwrap is missing or `workdir` is empty, returns
    `cmd` unchanged. Otherwise returns a bwrap invocation that confines the turn
    to `workdir` mounted at MOUNTPOINT, plus the grant binds.

    The caller keeps passing the REAL `workdir` as the subprocess cwd: that is
    where bwrap itself starts, before it enters the namespace. bwrap's own
    --chdir puts the child at MOUNTPOINT.

    `die_with_parent` adds bwrap's `--die-with-parent`, tying the whole sandbox's
    lifetime to the launching process. A detached CHAT turn must NOT set it (it is
    reparented to init and adopted back across a backend restart -- the module
    docstring's "a restart is a pause, not a break"). A FOREGROUND WORKER (ralph
    under `timeout`) is the opposite case: when `timeout` fires or the sweep dies
    it must take the whole sandbox down with it, or an orphaned `claude` runs past
    its wall budget. So the worker sets it and the chat turn leaves it off.
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
    # what a dev turn exists to run. Process/signal isolation from other host-user
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
    if die_with_parent:
        args.append("--die-with-parent")
    for p in _RO_SYSTEM:
        if os.path.isdir(p):
            args += ["--ro-bind", p, p]
    # The grant set: the few things beyond the scope a build/dev turn needs to
    # operate. `dest` differs from `src` only for the two ~/.claude policy
    # files under the uid layer (they land in the turn-user's own home).
    for mode, src, dest in _grants(root):
        args += (["--bind", src, dest] if mode == "rw" else ["--ro-bind", src, dest])
    # The scope, writable, at the fixed managed mountpoint. Bound AFTER the tmpfs
    # so a scope that happens to live under /tmp is still present.
    args += ["--bind", root, MOUNTPOINT, "--chdir", MOUNTPOINT, "--"]
    return args + cmd


# --- The uid layer ([SEC-A1]): sudo -u <turn-user>, composed on top of wrap() ---


def turn_user(root: str | None = None) -> str | None:
    """The dedicated uid this turn should run as, or None to run as the host user.

    Unset `WORKSHOP_TURN_USER`: None, always -- the uid layer is off.

    Set: validates the account, the wrapper and sudo actually exist, and
    RAISES `TurnUserUnavailable` if not, rather than falling back to running
    as the host user. That is the opposite posture from `enabled()`'s
    degrade-safely default, deliberately: this flag is explicit opt-in, so a
    broken precondition is the exact failure the layer exists to prevent, not
    something to paper over.

    SELF-SCOPE CARVE-OUT. A turn whose scope root IS the platform's own repo
    (`root == wsplatform.paths.workshop_root()`) returns None even with the
    flag set and valid. `systemctl --user` ties its connection to the caller's
    real uid (SO_PEERCRED), so it cannot be delegated across a `sudo -u`
    boundary -- and `apply.sh`, which every dev-turn instruction tells an
    agent to run after a change, needs exactly that. Isolating platform-repo
    turns needs a small apply broker (a socket the backend listens on, on
    behalf of a turn that cannot restart itself) as its own follow-up; this
    carve-out is the named, tested state until that lands, not a silent gap.
    """
    name = os.environ.get(_USER_FLAG)
    if not name:
        return None
    if root is not None:
        try:
            from wsplatform import paths
            if os.path.realpath(root) == os.path.realpath(str(paths.workshop_root())):
                return None
        except Exception:
            pass
    try:
        pwd.getpwnam(name)
    except KeyError:
        raise TurnUserUnavailable(f"{_USER_FLAG}={name!r} but no such system account")
    if not os.path.exists(TURN_WRAPPER):
        raise TurnUserUnavailable(f"{_USER_FLAG} is set but {TURN_WRAPPER} is missing")
    if not shutil.which("sudo"):
        raise TurnUserUnavailable(f"{_USER_FLAG} is set but sudo is not on PATH")
    if not enabled():
        # The uid layer composes ON TOP of the bwrap view; without it there is
        # no filesystem confinement for `sudo -u` to run inside, only a bare
        # uid switch with the full host tree visible to it.
        raise TurnUserUnavailable(f"{_USER_FLAG} is set but the bwrap layer is off or non-functional")
    return name


def turn_home() -> str:
    """The uid layer's own home directory: the account's real `pw_dir` when
    `WORKSHOP_TURN_USER` names a real account, else the caller's own home (a
    harmless default for a caller that has not first checked `turn_user()`).
    Deliberately re-reads the account directly rather than going through
    `turn_user()`, which also applies the self-scope carve-out -- a grant
    destination is a fact about the account, not about which turn is asking.
    """
    name = os.environ.get(_USER_FLAG)
    if name:
        try:
            return pwd.getpwnam(name).pw_dir
        except KeyError:
            pass
    return os.path.expanduser("~")


def spawn_argv(cmd: list[str], workdir: str, *, die_with_parent: bool = False) -> list[str]:
    """The full argv to actually spawn for a turn: the bwrap view from
    `wrap()`, further wrapped in `sudo -n -E -u <user> -- <turn-exec> run`
    when the uid layer is on for this scope.

    `die_with_parent` is forwarded to `wrap()` -- set by a foreground worker so
    the sandbox dies with its launcher, left off by a detached chat turn.

    `-n` (no password prompt) is load-bearing: without it, a misconfigured
    sudoers rule makes sudo block reading a password from stdin, which is the
    turn's fifo -- opened O_RDWR so it never EOFs. The turn would hang forever
    holding an agent slot instead of failing visibly. `-E` (plus `SETENV:` in
    the sudoers rule) is load-bearing too: sudo's default `env_reset` would
    strip `CLAUDE_CODE_OAUTH_TOKEN` and every `WORKSHOP_*` var the child needs
    to run at all.

    Raises `TurnUserUnavailable` (never silently falls back to an un-isolated
    spawn) when the flag is set but its precondition is not met -- propagates
    to the caller, which must end the turn rather than spawn it unconfined.
    """
    base = wrap(cmd, workdir, die_with_parent=die_with_parent)
    # FAIL-CLOSED. `wrap()` returns the command UNCHANGED when it could not build
    # a sandbox (bwrap missing, the kernel probe failed, an empty scope root, or
    # the flag forced confinement off). For an agent turn that unchanged command
    # means FULL HOST FILESYSTEM ACCESS, which must never happen: the walls
    # dropping is a refusal, not a degraded mode. A confined result is a bwrap
    # invocation, so if `base` does not start with bwrap it was not confined.
    bwrap = shutil.which("bwrap")
    if not (bwrap and base and base[0] == bwrap):
        raise ConfinementUnavailable(
            "filesystem confinement could not be established for this turn "
            f"(bwrap={'present' if bwrap else 'MISSING'}, functional={functional()}, "
            f"scope_root={'set' if workdir else 'EMPTY'}, {_FLAG}={os.environ.get(_FLAG)!r}). "
            "A turn runs confined to its scope or it does not run; it is never "
            "spawned with host filesystem access."
        )
    root = os.path.realpath(workdir) if workdir else None
    user = turn_user(root)
    if user is None:
        return base
    sudo = shutil.which("sudo")  # presence already validated inside turn_user()
    return [sudo, "-n", "-E", "-u", user, "--", TURN_WRAPPER, "run", *base]


def turn_env(env: dict, workdir: str | None = None) -> dict:
    """The env a confined turn should run with.

    TWO responsibilities, layered:

    1. CONFINEMENT PATH REMAP (whenever the bwrap layer is on for this scope,
       uid layer or not). The backend's own environment sets WORKSHOP_ROOT and a
       dozen sibling vars (WORKSHOP_RUNTIME, WORKSHOP_NOTIFY_FILE, WORKSHOP_CHAT_CWD,
       ...) to the turn's REAL host scope path. Inside the sandbox that path is
       absent -- only the mountpoint exists -- so any value pointing under the
       scope root is rewritten to MOUNTPOINT, exactly as `wrap()` rewrites the
       argv. Without this, `paths.workshop_root()` reads the host WORKSHOP_ROOT
       and every path derived from it (the knowledge store, `.runtime`, the notify
       file) resolves OUTSIDE the sandbox and reads nothing -- which is why a
       confined triager saw zero notes. Only values under the scope root change;
       a granted path (~/.claude, ~/.config/workshop) lives outside the root and
       is left at its real location, matching its bind.

    2. UID LAYER (only when `WORKSHOP_TURN_USER` runs this turn): point HOME and
       git identity at the turn-user's own home instead of the host user's (no longer
       bound in -- see `_grants()`), and drop the XDG/D-Bus vars that named
       the host user's runtime dir.

    A no-op for an un-confined, non-uid spawn, so callers apply it unconditionally.
    """
    root = os.path.realpath(workdir) if workdir else None
    if root and enabled():
        env = {k: (_remap(v, root) if isinstance(v, str) else v) for k, v in env.items()}
    user = turn_user(root)
    if user is None:
        return env
    # turn_user raised unless enabled(), so the remap above already copied env.
    env = dict(env)
    home = turn_home()
    env["HOME"] = home
    env.pop("XDG_RUNTIME_DIR", None)
    env.pop("DBUS_SESSION_BUS_ADDRESS", None)
    env["GIT_CONFIG_GLOBAL"] = os.path.join(home, ".gitconfig")
    # Debian's sudoers sets `secure_path`, which overrides PATH even under -E
    # and would strip nvm's node from every Bash tool call inside the turn.
    # sudo does not recognise this name, so -E carries it through untouched;
    # the turn-exec wrapper restores PATH from it before the final exec.
    env["WORKSHOP_TURN_PATH"] = env.get("PATH", "")
    return env


def signal_pg(pid: int, sig: int, *, user: str | None, pgid: int | None = None) -> None:
    """Signal a turn's whole process group, whether or not it runs under the
    uid layer. `user` is the value RECORDED at spawn time (`turn_user(root)`
    then, not now) -- required as a keyword, not recomputed here, because a
    turn that was carved out of the uid layer at spawn (the self-scope case)
    must still be signalled directly even if the flag happens to be set when
    this runs later.

    `pgid` is used when the caller already resolved it (`os.getpgid` works
    same-uid); otherwise this falls back to `pid` itself, which IS the group
    id for anything spawned with `start_new_session=True` (a new session's
    leader has pgid == pid). Same-uid: `os.killpg` directly. Cross-uid:
    `os.killpg` from the host user onto a `workshop-turn`-owned group raises EPERM
    (silently swallowed by a bare except at the call site, which is what made
    this the layer's most likely regression), so the signal is routed through
    the turn wrapper's own identity instead.
    """
    gid = pgid or pid
    if user is None:
        os.killpg(gid, sig)
        return
    sudo = shutil.which("sudo")
    if not sudo:
        return
    subprocess.run(
        [sudo, "-n", "-u", user, "--", TURN_WRAPPER, "signal", str(int(sig)), str(gid)],
        capture_output=True, timeout=5,
    )


# --- Shell-callable entrypoint: confine a foreground worker (scripts/ralph.sh) --
#
# The chat turn wires confinement in Python (build_streaming calls spawn_argv).
# The ralph worker is a bash loop that spawns `claude -p` itself, so it needs a
# way into the SAME fail-closed seam from the shell. `python -m services.scoped_exec
# exec --workdir <scope> -- <cmd...>` builds the confined argv and execs it, so the
# worker runs confined to its scope or -- if the sandbox cannot be built -- does
# not run at all (exit 3), never with host filesystem access. This is the worker's
# side of the operator's inviolable rule: confinement is not opt-in and does not
# degrade to open.
def _exec_main(argv: list[str]) -> int:
    import argparse
    import sys

    ap = argparse.ArgumentParser(prog="scoped_exec")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ex = sub.add_parser("exec", help="run a command confined to a scope root")
    ex.add_argument("--workdir", required=True,
                    help="the scope root to confine to (bind-mounted at /workspace)")
    ex.add_argument("rest", nargs=argparse.REMAINDER,
                    help="-- then the command to run confined")
    args = ap.parse_args(argv)

    cmd = args.rest
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        print("scoped_exec exec: no command given after --", file=sys.stderr)
        return 2

    try:
        spawn = spawn_argv(cmd, args.workdir, die_with_parent=True)
    except (ConfinementUnavailable, TurnUserUnavailable) as e:
        # Fail-closed: refuse rather than run the worker unconfined. Bash sees a
        # nonzero exit and treats the iteration as failed, which is correct -- a
        # writer that cannot be sandboxed must not write.
        print(f"scoped_exec: refusing to run unconfined: {e}", file=sys.stderr)
        return 3
    env = turn_env(dict(os.environ), args.workdir)
    # Nothing is printed on the success path: os.execvpe replaces this process, so
    # the worker's own stdout (claude's --output-format json) is never corrupted.
    os.execvpe(spawn[0], spawn, env)
    return 127  # unreachable unless execvpe fails


if __name__ == "__main__":
    import sys
    raise SystemExit(_exec_main(sys.argv[1:]))
