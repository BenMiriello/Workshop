"""Reading a shell command for what deletes (`tool_policy`): every command of
a list or pipeline, split outside quotes, through the wrappers that run
another command, with here-document bodies left out as the data they are.
Removing files under a scratch directory (/tmp, tmp/, scratch/) is not a
delete: those are throwaway by convention. Nor is removing files git can bring
back (`tool_policy_git`), or an empty folder (`rmdir` removes nothing else). A
variable assigned earlier in the same command, or made by `mktemp`, is read as
the path it holds, so `X=/tmp/idx; rm -f $X` removes a scratch file; one the
command does not assign stays unknown.

`trash`: the turn's own `rm` and `unlink` move what they remove to the trash
(`turn_rm`), so calling them by name is not a delete. By a path (`/bin/rm`) or
under `sudo`, which resets PATH, the system's own runs, and that still is.
`kept`: commands `preserve` already made recoverable.

Joined into the policy hook's own source (`tool_policy.hook_source`), so it
imports nothing of the backend.
"""
from __future__ import annotations

import os
import re
import shlex

from services.tool_policy_git import _git_deletes, _in_git, keep  # hook: joined

_RM_COMMANDS = frozenset({"rm", "unlink", "shred", "srm", "wipefs", "mkfs"})
_TRASHED = frozenset({"rm", "unlink"})
_SQL_DELETE = re.compile(r"\b(drop\s+(table|database|schema|index|view)|delete\s+from|truncate\s+(table\s+)?\w)", re.I)
# Where SQL in a command reaches a database rather than, say, a search for it.
_SQL_RUNNER = re.compile(r"\b(psql|sqlite3|mysql|mariadb|duckdb|sqlcmd|clickhouse-client)\b|\.execute(many|script)?\(")
# Deleting from inside a one-line program.
_CODE_DELETE = re.compile(
    r"\b(os\.(remove|unlink|rmdir|removedirs)|shutil\.rmtree|fs\.(rm|rmdir|unlink)(Sync)?)\s*\(|\.(unlink|rmdir)\(\s*\)")
# Tools whose own subcommand deletes: `docker rm`, `kubectl delete`, `gh repo delete`.
_SUBCOMMAND_CLIS = frozenset({"docker", "podman", "kubectl", "helm", "gh", "flyctl", "heroku", "aws", "gcloud"})
_SUBCOMMAND_DELETES = frozenset({"rm", "rmi", "delete", "remove", "prune", "destroy", "purge", "uninstall"})
# Commands that run another command: the one they run is what is read.
_WRAPPERS = frozenset({"sudo", "nice", "nohup", "env", "time", "command", "exec", "timeout",
                       "ionice", "stdbuf", "chronic", "flock", "systemd-run", "setsid", "taskset"})
_WRAPPER_VALUE_OPTS = frozenset({"-n", "-p", "-u", "-E", "-s", "-k", "-c", "--unit", "--property",
                                 "--setenv", "--signal", "--kill-after", "--slice", "--nice"})
_HTTP_DELETE = re.compile(r"(-X|--request)\s*['\"]?DELETE\b", re.I)
_HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")
# Paths whose files are throwaway by convention: removing them loses nothing.
_SCRATCH = re.compile(r"^(/tmp/|/var/tmp/|(\./)?(tmp|scratch)/|.*/(tmp|scratch)/)")
_NAME = r"[A-Za-z_][A-Za-z0-9_]*"
_ASSIGN = re.compile(rf"^({_NAME})=(.*)$", re.S)
_MKTEMP = re.compile(rf"^(?:export\s+)?({_NAME})=\$\(mktemp\b([^)]*)\)$")
_VAR = re.compile(rf"\$(?:\{{({_NAME})\}}|({_NAME}))")


def _without_heredocs(command: str) -> str:
    """The command with each here-document's body taken out: it is input to
    the command, data, not commands."""
    out, lines, i = [], command.split("\n"), 0
    while i < len(lines):
        out.append(lines[i])
        ends = [m.group(2) for m in _HEREDOC.finditer(lines[i])]
        i += 1
        for end in ends:
            while i < len(lines) and lines[i].strip() != end:
                i += 1
            i += 1
    return "\n".join(out)


def _parts(command: str) -> list[str]:
    """The commands of a list or pipeline, split outside quotes only."""
    parts, cur, quote, i = [], [], "", 0
    while i < len(command):
        c = command[i]
        if quote:
            cur.append(c)
            if c == "\\" and quote == '"' and i + 1 < len(command):
                cur.append(command[i + 1])
                i += 1
            elif c == quote:
                quote = ""
        elif c in "'\"":
            quote = c
            cur.append(c)
        elif c == "\\" and i + 1 < len(command):
            cur.append(command[i:i + 2])
            i += 1
        elif c in ";|&\n":
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(c)
        i += 1
    parts.append("".join(cur))
    return [p.strip() for p in parts if p.strip()]


def _argv(part: str) -> list[str]:
    try:
        return shlex.split(part, comments=True)
    except ValueError:
        return part.split()


def _absent(paths: list[str], cwd: str | None) -> bool:
    """Whether none of the paths exists, so removing them deletes nothing."""
    if not paths or any(any(c in p for c in "*?[{$`") for p in paths):
        return False
    where = [os.path.expanduser(p) for p in paths]
    if any(not os.path.isabs(p) for p in where) and not cwd:
        return False
    return not any(os.path.lexists(os.path.join(cwd or "/", p)) for p in where)


def _unwrap(argv: list[str]) -> list[str]:
    """The command a wrapper runs: `nice -n 10 rm x` is `rm x`."""
    while argv:
        head = argv[0].rsplit("/", 1)[-1]
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", argv[0]):
            argv = argv[1:]
            continue
        if head not in _WRAPPERS:
            return argv
        argv = argv[1:]
        while argv and argv[0].startswith("-"):
            opt = argv[0]
            argv = argv[2:] if opt in _WRAPPER_VALUE_OPTS else argv[1:]
        if head == "timeout" and argv and re.match(r"^\d", argv[0]):
            argv = argv[1:]
    return argv


def _scratch(path: str, in_scratch: bool) -> bool:
    if ".." in path.split("/"):
        return False
    return bool(_SCRATCH.match(path.rstrip("/") + "/")) or (in_scratch and not path.startswith(("/", "~")))


def _expand(word: str, env: dict[str, str]) -> str:
    return _VAR.sub(lambda m: env.get(m.group(1) or m.group(2), m.group(0)), word)


def _mktemp_path(args: str) -> str:
    """Where `mktemp <args>` makes its file: its template, under `-p` or
    `--tmpdir` when given, else under TMPDIR."""
    argv, base, template = _argv(args), os.environ.get("TMPDIR") or "/tmp", "tmp.X"
    for i, a in enumerate(argv):
        if a in ("-p", "--tmpdir") and i + 1 < len(argv):
            base = argv[i + 1]
        elif a.startswith("--tmpdir="):
            base = a.split("=", 1)[1]
        elif not a.startswith("-") and (i == 0 or argv[i - 1] not in ("-p", "--tmpdir")):
            template = a
    return template if template.startswith("/") else os.path.join(base, template)


def _assigns(part: str, env: dict[str, str]) -> bool:
    """Record the variables a command that only assigns sets. False for any
    other command, which also forgets a variable it begins by assigning."""
    m = _MKTEMP.match(part)
    if m:
        env[m.group(1)] = _mktemp_path(m.group(2))
        return True
    words = _argv(part)
    words = words[1:] if words[:1] == ["export"] else words
    found = [_ASSIGN.match(w) for w in words]
    if not words or not all(found):
        if found and found[0]:
            env.pop(found[0].group(1), None)
        return False
    for f in found:
        env[f.group(1)] = _expand(f.group(2), env)
    return True


def _git_at(args: list[str], cwd: str | None) -> tuple[list[str], str | None]:
    """A git command's own arguments and directory, through `-C <dir>`."""
    if args[:1] == ["-C"] and len(args) > 1:
        return args[2:], args[1] if args[1].startswith("/") else (os.path.join(cwd, args[1]) if cwd else None)
    return args, cwd


def _command_deletes(part: str, in_scratch: bool = False, cwd: str | None = None,
                     env: dict[str, str] | None = None, trash: bool = False) -> str | None:
    """Why one command deletes, or None. `in_scratch`: an earlier `cd` in the
    same command went into a scratch directory. `cwd`: where it runs, when known.
    `env`: the variables assigned before it. `trash`: see the module doc."""
    raw = [_expand(a, env or {}) for a in _argv(part)]
    argv = _unwrap(raw)
    if not argv:
        return None
    sudo = any(w.rsplit("/", 1)[-1] == "sudo" for w in raw[:len(raw) - len(argv)])
    trashed = trash and not sudo
    head = argv[0].rsplit("/", 1)[-1]
    if head in _RM_COMMANDS or head.startswith("mkfs."):
        if trashed and argv[0] in _TRASHED:
            return None
        paths = [a for a in argv[1:] if not a.startswith("-")]
        if head in ("rm", "unlink") and paths and all(_scratch(a, in_scratch) for a in paths):
            return None
        if head in ("rm", "unlink") and (_in_git(paths, cwd) or _absent(paths, cwd)):
            return None
        return head
    removers = [a for a in argv[1:] if a.rsplit("/", 1)[-1] in _RM_COMMANDS]
    by_trash = trashed and all(a in _TRASHED for a in removers)
    if head == "find" and "-delete" in argv:
        return "find -delete"
    if head in ("find", "xargs") and removers and not by_trash:
        return f"{head} that deletes"
    if head == "git":
        return _git_deletes(*_git_at(argv[1:], cwd))
    if head == "truncate" or (head == "dd" and any(a.startswith("of=") for a in argv)):
        return head
    if head in ("bash", "sh", "zsh") and "-c" in argv:
        i = argv.index("-c")
        return command_deletes(argv[i + 1], cwd, trash=trashed) if i + 1 < len(argv) else None
    if head in _SUBCOMMAND_CLIS:
        words = [a for a in argv[1:5] if not a.startswith("-")]
        verb = next((w for w in words if w in _SUBCOMMAND_DELETES), None)
        if verb:
            return f"{head} {verb}"
    if re.match(r"^(python[0-9.]*|node)$", head) and _CODE_DELETE.search(part):
        return "a program that deletes files"
    return None


def _commands(command: str, cwd: str | None):
    """Each command of a list or pipeline as (part, in_scratch, cwd, env), with
    the `cd`s and variable assignments before it applied."""
    env: dict[str, str] = {}
    in_scratch = False
    for part in _parts(_without_heredocs(command)):
        if _assigns(part, env):
            continue
        argv = [_expand(a, env) for a in _argv(part)]
        if argv[:1] == ["cd"]:
            in_scratch = len(argv) > 1 and _scratch(argv[1], in_scratch)
            to = os.path.expanduser(argv[1]) if len(argv) > 1 else "~"
            cwd = os.path.normpath(os.path.join(cwd or "/", to)) if cwd or to.startswith("/") else None
            continue
        yield part, in_scratch, cwd, dict(env)


def deleting_parts(command: str, cwd: str | None = None, trash: bool = False,
                   kept: frozenset[str] = frozenset()) -> list[tuple[str, str]]:
    """Each command of a list or pipeline that deletes, with why. `cwd`: where
    it starts; a `cd` moves it for the commands after."""
    out = []
    for part, in_scratch, at, env in _commands(command, cwd):
        why = None if part in kept else _command_deletes(part, in_scratch, at, env, trash)
        if why:
            out.append((part, why))
    return out


def preserve(command: str, cwd: str | None) -> frozenset[str]:
    """The git commands of `command` that `keep` made recoverable, which then
    run without asking. Has effects: run only for a call about to be decided."""
    kept = set()
    for part, _, at, env in _commands(command, cwd):
        argv = _unwrap([_expand(a, env) for a in _argv(part)])
        if argv and argv[0].rsplit("/", 1)[-1] == "git":
            args, where = _git_at(argv[1:], at)
            if _git_deletes(args, where) and keep(args, where):
                kept.add(part)
    return frozenset(kept)


def command_deletes(command: str, cwd: str | None = None, trash: bool = False,
                    kept: frozenset[str] = frozenset()) -> str | None:
    """Why a shell command deletes something, or None. Reads every command of a
    pipeline or list; a nested `bash -c` is read too. `cwd`: where it starts."""
    data_out = _without_heredocs(command)
    if _SQL_DELETE.search(data_out) and _SQL_RUNNER.search(data_out):
        return "SQL that deletes"
    if _HTTP_DELETE.search(data_out):
        return "an HTTP DELETE"
    found = deleting_parts(command, cwd, trash, kept)
    return found[0][1] if found else None
