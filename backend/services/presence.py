"""Presence: the expiring fact that a session is here and what it is doing.

Step B4 of `docs/platform-redesign/workspace-ownership-and-leases-2026-08-22.md`
(section 6). A presence is a TTL'd row that ANY process can write and that
NOBODY reconciles on read.

WHY THIS IS NOT AN IN-MEMORY MAP, which is what revision 1 of that design said.
The participants that actually hold trees are `scripts/project_ralph.sh`,
`scripts/ralph.sh` and `scripts/queue_runner.sh` -- separate OS processes. A map
in the backend cannot see any of them, would go empty on every backend restart
while every participant kept working, and would not survive a second worker
process. So presence is a shared store, and it has a CLI (`main()` here,
`scripts/presence.py` as the shell-facing shim) because its most important
writers are bash.

WHY THIS IS NOT THE 753-ROW INBOX AGAIN. That lesson was *do not keep a derived
duplicate of a fact another record owns*, not "never persist a fast-changing
fact". Presence is not derived from anything: it is a primary observation with a
natural expiry, the same shape as a Kubernetes `Lease`, which is itself
persisted.

GENERALIZED FROM `.runtime/heartbeats/`, NOT ADDED BESIDE IT. Section 6 says that
channel "is already this shape and is the thing to generalize rather than
replace", so this module owns that exact directory and that exact env var
(`WORKSHOP_HEARTBEATS_DIR`). `scripts/heartbeat.sh` now delegates here instead of
composing its own JSON, and `routes/agents.py` reads through this module instead
of parsing the file itself. There is one writer implementation and one reader
implementation, which is the entire point: two presence mechanisms is the defect
this step exists to avoid.

READING NEVER RECONCILES. `list_live` filters by timestamp and returns; it does
not rewrite a record, delete an expired one, or repair anything it finds. Expiry
is a comparison, not an event. `prune` exists for disk hygiene and is called by
NOTHING on a read path -- only by an explicit `presence prune` from the CLI.

THE ACCELERATOR MAY ONLY EVER SHORTEN A LIFETIME. See `effective_expiry`.

WHAT IS DELIBERATELY NOT HERE. The lease (design section 5) is step B3 and is
being built concurrently. There is no lease table, no lease module and no fake
lease in this file: `DirectoryEntry.lease` is a declared, always-null seam and
`directory(lease_source=...)` is the injection point B3 fills. Writing half a
lease here would give a caller something indistinguishable from the real one.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import socket
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Iterable, Mapping

# Module-scope, and each of these is cheap on the SYSTEM python3 that the shell
# workers' CLI runs under. `services.worktrees` is the one exception and is
# imported lazily inside `_resolve_worktree`; see the comment there.
from services import port_registry, task_scope, tasks
from wsplatform import paths

__all__ = [
    "PRESENCE_DIR", "DEFAULT_TTL_SEC", "MAX_TTL_SEC",
    "SESSION_KINDS", "SUBJECT_KINDS", "LIVENESS_PROFILES",
    "PresenceError",
    "beat", "beat_age_s", "get", "release", "list_all", "list_live", "prune",
    "effective_expiry", "is_live",
    "ACCELERATORS", "accelerator_for", "pid_accelerator",
    "directory", "main",
]


class PresenceError(ValueError):
    """A malformed write. Reads never raise: a corrupt record is skipped."""


# The channel. Same directory and same env var as the heartbeats channel this
# generalizes -- a rename here would BE the "replace" section 6 rules out, and
# would strand every live file an agent has already written.
PRESENCE_DIR: Path = paths.env_path("WORKSHOP_HEARTBEATS_DIR", "heartbeats")

# Section 5's windows: lease TTL 90s, heartbeat every 30s. Presence rides the
# same numbers so a presence-renewed lease cannot outlive the presence that
# renews it. MAX_TTL_SEC is a sanity bound on the one number a caller supplies,
# not a permission check.
DEFAULT_TTL_SEC = int(os.environ.get("WORKSHOP_PRESENCE_TTL_SEC", "90"))
MAX_TTL_SEC = 3600

# A session is one live participant (section 7): a chat, or an agent run. Closed.
SESSION_KINDS = ("chat", "run")

# What it is working on. The lease's subject enum (section 5) plus `none`, for a
# session that is present without holding anything -- a resident sweep waiting
# for work is here, and saying so is the point of a directory.
SUBJECT_KINDS = ("ticket", "worktree", "none")

# Which accelerator, if any, applies to this record. Section 6: process probing
# is "an optional per-profile accelerator". `heartbeat` means expiry only.
LIVENESS_PROFILES = ("heartbeat", "pid")

_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")

# "Already over", for the two cases that have no better answer: a record whose
# expiry will not parse, and a probe that says the process is gone. NOT `now()`
# -- see `pid_accelerator`.
_LONG_GONE = datetime.fromtimestamp(0, timezone.utc)


# --- the channel ------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _session_path(session_id: str) -> Path:
    """The file for a session id, with the id validated rather than escaped.

    Escaping would make two different ids collide on one file; refusing is the
    honest answer and every real id (an agent slug, a chat id, a job id) already
    passes.
    """
    if not isinstance(session_id, str) or not _ID_RE.fullmatch(session_id):
        raise PresenceError(f"not a usable session id: {session_id!r}")
    return PRESENCE_DIR / f"{session_id}.json"


def _one_of(value: Any, allowed: tuple[str, ...], label: str, default: str) -> str:
    if value is None:
        return default
    if value not in allowed:
        raise PresenceError(f"{label} must be one of {allowed}, got {value!r}")
    return value


def beat(session_id: str,
         *,
         session_kind: str = "run",
         activity: str = "",
         subject_kind: str | None = None,
         subject_id: str | None = None,
         scope: str | None = None,
         principal: str | None = None,
         pid: int | None = None,
         liveness_profile: str | None = None,
         ttl_sec: int | None = None,
         meta: dict[str, str] | None = None) -> dict:
    """Write or refresh this session's presence. Returns the stored record.

    `expires_at` IS SERVER-COMPUTED. Section 5 states the rule for the lease --
    "server-computed, never a caller's clock" -- and it applies identically here
    for the same reason: a caller with a skewed clock would otherwise be able to
    declare itself present until next Tuesday, and clock skew across the
    processes that write this channel is not hypothetical. A caller supplies a
    TTL (a duration); it never supplies an instant. `ts` is taken here too.

    Refreshing is a whole-record write, not a merge, so a session that stops
    working on a ticket stops claiming the ticket in the same beat.
    """
    path = _session_path(session_id)
    now = _now()
    ttl = DEFAULT_TTL_SEC if ttl_sec is None else int(ttl_sec)
    if ttl <= 0 or ttl > MAX_TTL_SEC:
        raise PresenceError(f"ttl_sec must be 1..{MAX_TTL_SEC}, got {ttl_sec!r}")

    kind = _one_of(session_kind, SESSION_KINDS, "session_kind", "run")
    subj_kind = _one_of(subject_kind, SUBJECT_KINDS, "subject_kind", "none")
    profile = _one_of(liveness_profile, LIVENESS_PROFILES, "liveness_profile", "heartbeat")
    if subj_kind != "none" and not subject_id:
        raise PresenceError(f"subject_kind={subj_kind} needs a subject_id")
    if subj_kind == "none":
        subject_id = None

    record = {
        "session_id": session_id,
        "session_kind": kind,
        "subject_kind": subj_kind,
        "subject_id": subject_id or None,
        "scope": _normalize_scope(scope),
        # WHO it acts for, separate from the session kind (section 5's
        # `principal_user_id`). Derived from the OS user when unstated so a
        # shell writer gets something true rather than a hardcoded name.
        "principal": principal or _default_principal(),
        "activity": (activity or "").strip()[:200],
        "pid": int(pid) if pid else None,
        "hostname": socket.gethostname().split(".")[0],
        "liveness_profile": profile,
        "ttl_sec": ttl,
        # `ts` keeps the heartbeat channel's original key and meaning (last
        # write), because it is on the wire in AgentRunsOut and in the
        # generated client.
        "ts": _iso(now),
        "expires_at": _iso(now + timedelta(seconds=ttl)),
        "meta": {str(k): str(v) for k, v in (meta or {}).items()},
    }

    PRESENCE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(record) + "\n", encoding="utf-8")
    # Atomic replace so a reader never sees a half-written record. The channel
    # has always done this (heartbeat.sh wrote .tmp then mv'd); keeping it is
    # what lets readers do no locking at all.
    os.replace(tmp, path)
    return record


def _normalize_scope(scope: str | None) -> str | None:
    """One spelling for a scope, decided at WRITE time.

    The writers disagree about what they know: `project_ralph.sh` has the
    prefixed key (`project:<slug>`), while `ralph.sh` and `queue_runner.sh` have
    a directory. If both were stored verbatim, two sessions on one scope would
    not join, and `?scope=` would answer differently depending on which process
    happened to write the row. So a path is resolved to its context key here,
    once, rather than by every reader afterwards -- the same reason
    `project_ralph.sh` asks `task_scope.key_for_root` instead of guessing from
    the basename. A path that names no registered scope is kept as-is: a wrong
    key would be worse than an unjoinable one.
    """
    if not scope:
        return None
    if "/" not in scope:
        return scope
    try:
        return task_scope.key_for_root(scope) or scope
    except Exception:
        return scope


def _default_principal() -> str:
    try:
        return getpass.getuser()
    except Exception:
        return "unknown"


def _load(path: Path) -> dict | None:
    """One record off disk, or None. A corrupt or half-written file is SKIPPED,
    never repaired: reading does not write."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return _tolerate(data, path)


def _tolerate(data: dict, path: Path) -> dict:
    """Fill what an OLD heartbeat file (pre-B4) does not carry.

    In memory only -- the file is not rewritten. A record written by the old
    `heartbeat.sh` has `ts`, `agent_id`, `hostname` and `pid` and nothing else,
    and it must still read as a presence rather than vanish or crash a list.
    Its expiry is derived from its `ts` + the default TTL, which is exactly what
    the old `heartbeat_age_s > 120` reader was computing by hand.
    """
    data.setdefault("session_id", data.get("agent_id") or path.stem)
    data.setdefault("session_kind", "run")
    data.setdefault("subject_kind", "none")
    data.setdefault("subject_id", None)
    data.setdefault("scope", None)
    data.setdefault("principal", None)
    data.setdefault("activity", "")
    data.setdefault("pid", None)
    data.setdefault("hostname", None)
    data.setdefault("liveness_profile", "heartbeat")
    data.setdefault("ttl_sec", DEFAULT_TTL_SEC)
    if not data.get("expires_at"):
        ts = _parse(data.get("ts"))
        ttl = int(data.get("ttl_sec") or DEFAULT_TTL_SEC)
        data["expires_at"] = _iso(ts + timedelta(seconds=ttl)) if ts else _iso(_now())
    if not isinstance(data.get("meta"), dict):
        data["meta"] = {}
    return data


def beat_age_s(record: dict | None) -> float | None:
    """Seconds since this record's last write, or None if it has no usable `ts`.

    A read, not a judgement: `is_live` answers whether the session is here.
    This exists because the age itself is on the wire (`AgentRunsOut`).
    """
    beat_at = _parse((record or {}).get("ts"))
    return None if beat_at is None else (_now() - beat_at).total_seconds()


def get(session_id: str) -> dict | None:
    """One session's record whether or not it has expired. No reconciliation."""
    try:
        path = _session_path(session_id)
    except PresenceError:
        return None
    return _load(path)


def release(session_id: str) -> bool:
    """Explicitly withdraw a presence: the session is leaving, now, rather than
    in TTL seconds.

    This is the WRITE half of section 6's join rule -- "presence expiry for an
    interactive holder releases its lease immediately" -- and it is why closing
    a chat tab can cost nothing. The lease half of that rule is step B3 and is
    deliberately absent; when it lands it hangs off this call and off
    `list_live`, and needs no change here.
    """
    try:
        path = _session_path(session_id)
    except PresenceError:
        return False
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return False


def list_all() -> list[dict]:
    """Every record in the channel, expired included, newest beat first."""
    try:
        files = sorted(PRESENCE_DIR.glob("*.json"))
    except OSError:
        return []
    out = [rec for rec in (_load(p) for p in files) if rec]
    out.sort(key=lambda r: r.get("ts") or "", reverse=True)
    return out


# --- the accelerator --------------------------------------------------------
#
# AN ACCELERATOR THAT CAN EXTEND A LIFETIME IS A BUG. Section 6: process-probing
# "may only ever *shorten* time-to-expiry, never extend it and never gate a
# state". That is not left to each accelerator to honour -- `effective_expiry`
# takes `min(stored, accelerated)`, so an accelerator that returned a later
# instant, or that returned a fixed instant regardless of the record, cannot
# lengthen anything. The rule is structural, and `test_presence.py` asserts it
# with a deliberately misbehaving accelerator.
#
# Why keep one at all, when heartbeat-and-expiry is the whole liveness model:
# removing the fast path without replacing it would strand a crashed sweep for
# the full TTL. So it is kept, made pluggable, and pointed at the correct probe.

Accelerator = Callable[[dict], datetime | None]


def pid_accelerator(record: dict) -> datetime | None:
    """End this presence AT ITS LAST BEAT if the process behind it is gone.

    NOT `now()`, and the difference is not cosmetic: a caller may be asking
    whether the session was live at some instant `t`, and `list_live` takes its
    comparison instant once before the loop. An accelerator returning `now()`
    would hand back a moment fractionally AFTER that snapshot, and the record
    would compare as live -- an accelerator lengthening a lifetime by exactly
    the arithmetic that was supposed to make lengthening impossible. The last
    beat is also the honest answer: it is the last moment anything observed the
    process, and when it died between then and now is not knowable.

    Uses `port_registry.process_alive`, NOT `tasks._pid_alive`, and section 6
    requires exactly that: `_pid_alive` is `os.kill(pid, 0)`, which reports a
    ZOMBIE as alive, so a crashed-but-unreaped sweep would keep looking present
    until its TTL ran out -- the failure this accelerator exists to prevent.
    `process_alive` reads /proc/<pid>/stat and treats `Z`/`X` as dead.

    Only records that OPTED IN (`liveness_profile == "pid"`) reach here. That
    matters: `scripts/heartbeat.sh`'s writer is a short-lived shell whose pid is
    dead by the time anyone reads the file, and probing it would expire every
    cron agent's presence the instant it was written.
    """
    pid = record.get("pid")
    if not pid:
        return None
    if port_registry.process_alive(int(pid)):
        return None
    return _parse(record.get("ts")) or _LONG_GONE


# The per-profile table. IMMUTABLE, and there is no `register()` that mutates it
# at runtime, deliberately: a mutable module-level container is process-local
# state that stops this backend running under more than one uvicorn worker
# (target-architecture-2026-08-17.md sec 2.6, gated by
# `scripts/check_no_process_globals.py`), and one worker's registration would be
# invisible to the next -- an accelerator that applies or does not depending on
# which process answered is worse than none.
#
# Pluggable means two things here, both of which survive that:
#   * a CALLER supplies its own for one call (`accelerator=`), which is how a
#     test drives it and how a future caller with a better probe overrides one;
#   * a PROFILE is added by adding a name to LIVENESS_PROFILES and its probe to
#     this table, beside the implementation it names, where it is reviewable.
ACCELERATORS: Mapping[str, Accelerator] = MappingProxyType({"pid": pid_accelerator})


def accelerator_for(record: dict) -> Accelerator | None:
    """The accelerator this record's profile asks for, if any."""
    return ACCELERATORS.get(record.get("liveness_profile") or "heartbeat")


def effective_expiry(record: dict,
                     *,
                     accelerator: Accelerator | None = None) -> datetime:
    """When this presence actually ends: the stored expiry, or sooner.

    `min` IS THE INVARIANT. Whatever an accelerator returns, the result is never
    later than the timestamp the writer's own beat computed, so no accelerator
    can renew a presence, and none can hold one open past its TTL.
    """
    stored = _parse(record.get("expires_at")) or _LONG_GONE
    fn = accelerator or accelerator_for(record)
    if fn is None:
        return stored
    try:
        early = fn(record)
    except Exception:
        # A broken probe must not make a record immortal OR kill it. Fall back
        # to the timestamp, which is the authority.
        return stored
    if early is None:
        return stored
    if early.tzinfo is None:
        early = early.replace(tzinfo=timezone.utc)
    return min(stored, early)


def is_live(record: dict,
            *,
            now: datetime | None = None,
            accelerator: Accelerator | None = None) -> bool:
    return effective_expiry(record, accelerator=accelerator) > (now or _now())


def list_live(*,
              session_kind: str | None = None,
              subject_kind: str | None = None,
              subject_id: str | None = None,
              scope: str | None = None,
              now: datetime | None = None,
              accelerator: Accelerator | None = None) -> list[dict]:
    """The sessions that are here, right now. Filters; writes nothing.

    An expired record is simply not returned. Nothing deletes it, nothing
    rewrites it, and nothing records that it expired -- the timestamp already
    said so, and a second record of the same fact is the duplicate this design
    forbids.
    """
    at = now or _now()
    out = []
    for rec in list_all():
        if session_kind and rec.get("session_kind") != session_kind:
            continue
        if subject_kind and rec.get("subject_kind") != subject_kind:
            continue
        if subject_id and rec.get("subject_id") != subject_id:
            continue
        if scope and rec.get("scope") != scope:
            continue
        if not is_live(rec, now=at, accelerator=accelerator):
            continue
        out.append(rec)
    return out


def prune(*, older_than_sec: int = 86400, now: datetime | None = None) -> int:
    """Delete records that expired more than `older_than_sec` ago. Returns count.

    DISK HYGIENE, NOT RECONCILIATION, and the difference is which code path
    calls it: nothing on a read path does. It is reachable only from
    `presence prune` on the CLI, so a reader can never be the thing that
    mutated the store.
    """
    at = now or _now()
    cutoff = at - timedelta(seconds=max(0, older_than_sec))
    removed = 0
    for rec in list_all():
        expires = _parse(rec.get("expires_at"))
        if expires is None or expires >= cutoff:
            continue
        try:
            _session_path(str(rec.get("session_id"))).unlink()
            removed += 1
        except (OSError, PresenceError):
            continue
    return removed


# --- the directory ----------------------------------------------------------

def _resolve_ticket(scope: str | None, ticket_id: str) -> tuple[str | None, str | None]:
    """(label, status) for a ticket subject, or (None, None) if unresolvable."""
    if not scope:
        return None, None
    root = task_scope.root_for_context(scope)
    if root is None:
        # A scope that names no registered context but IS a directory: an ad-hoc
        # `--dir` sweep. Reading its store directly is right, and refusing would
        # make the directory blank for exactly the runs least visible elsewhere.
        candidate = Path(scope)
        if "/" in scope and candidate.is_dir():
            root = candidate
        else:
            return None, None
    try:
        task = tasks.get_task(root, ticket_id)
    except Exception:
        return None, None
    if not task:
        return None, None
    title = (task.get("title") or tasks.split_text(task.get("text") or "")[0] or "").strip()
    return (title or ticket_id), task.get("status")


def _resolve_worktree(scope: str | None, branch_slug: str) -> tuple[str | None, str | None]:
    """(label, status) for a worktree subject, or (None, None).

    The worktree RECORD is step B2 and does not exist yet, so this reads the
    branch records `services/worktrees.py` already keeps. IMPORTED LAZILY, and
    for a reason that is not style: `worktrees` pulls in `app_runner` ->
    `compose_gen` -> `yaml`, which the system python3 that runs the shell
    workers' CLI does not have. A module-scope import here would make
    `scripts/presence.py heartbeat` fail on the interpreter every sweep uses.
    """
    if not scope:
        return None, None
    slug = scope.split(":", 1)[1] if ":" in scope else scope
    try:
        from services import worktrees
        branches = worktrees.list_branches(slug)
    except Exception:
        return None, None
    for b in branches:
        if b.get("slug") == branch_slug or b.get("branch") == branch_slug:
            return (b.get("branch") or branch_slug), (b.get("status") or None)
    return None, None


def directory(*,
              session_kind: str | None = None,
              subject_kind: str | None = None,
              subject_id: str | None = None,
              scope: str | None = None,
              now: datetime | None = None,
              accelerator: Accelerator | None = None,
              lease_source: Callable[[str, str], dict | None] | None = None,
              ) -> list[dict]:
    """Who is working here: live presence joined to what it is working on.

    A READ-TIME JOIN THAT STORES NOTHING (section 6). It is a function on this
    module and not a module of its own, for the same reason: there is no
    directory record to own. Every field below is read from presence, from the
    ticket store or from the branch records at the moment of the call, and the
    answer is thrown away.

    SUBJECT RESOLUTION IS BEST-EFFORT, on purpose. A ticket whose scope reads
    from Postgres needs a pinned owner, which the backend has and a bare
    `scripts/presence.py directory` from a sweep does not; an unresolvable
    subject comes back with a null label rather than raising, so the directory
    never fails to answer "who is here" because it could not decorate one row.

    `lease_source` IS THE SEAM FOR B3 and is the only thing missing from the
    join section 6 describes (presence, leases, tickets, worktrees). It is a
    callable `(subject_kind, subject_id) -> lease dict | None`; the lease module
    passes its own lookup and nothing here changes. It defaults to None, so
    `lease` is null on every entry today -- an honestly absent field rather than
    a plausible-looking fake that a caller could not tell from a real lease.
    """
    entries = []
    for rec in list_live(session_kind=session_kind, subject_kind=subject_kind,
                         subject_id=subject_id, scope=scope, now=now,
                         accelerator=accelerator):
        kind = rec.get("subject_kind") or "none"
        sid = rec.get("subject_id")
        label = status = None
        if sid and kind == "ticket":
            label, status = _resolve_ticket(rec.get("scope"), sid)
        elif sid and kind == "worktree":
            label, status = _resolve_worktree(rec.get("scope"), sid)
        entries.append({
            "session_id": rec.get("session_id"),
            "session_kind": rec.get("session_kind"),
            "principal": rec.get("principal"),
            "activity": rec.get("activity") or "",
            "scope": rec.get("scope"),
            "subject_kind": kind,
            "subject_id": sid,
            "subject_label": label,
            "subject_status": status,
            "pid": rec.get("pid"),
            "hostname": rec.get("hostname"),
            "last_beat_at": rec.get("ts"),
            "expires_at": _iso(effective_expiry(rec, accelerator=accelerator)),
            "lease": lease_source(kind, sid) if (lease_source and sid) else None,
        })
    return entries


# --- CLI --------------------------------------------------------------------
#
# The reason this module has one at all: `project_ralph.sh`, `ralph.sh` and
# `queue_runner.sh` are separate OS processes and must be able to write presence
# by shelling in. `scripts/presence.py` is the shim they call; it does nothing
# but hand argv here.

def _kv(pairs: Iterable[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in pairs:
        if "=" in item:
            k, v = item.split("=", 1)
            out[k] = v
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="presence",
        description="Read and write the shared presence channel. Every command "
                    "prints JSON on stdout.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    hb = sub.add_parser("heartbeat", help="write/refresh this session's presence")
    hb.add_argument("session_id")
    hb.add_argument("--kind", default="run", choices=list(SESSION_KINDS))
    hb.add_argument("--activity", default="")
    hb.add_argument("--subject-kind", default="none", choices=list(SUBJECT_KINDS))
    hb.add_argument("--subject-id", default=None)
    hb.add_argument("--scope", default=None)
    hb.add_argument("--principal", default=None)
    hb.add_argument("--pid", type=int, default=None)
    hb.add_argument("--profile", default="heartbeat", choices=list(LIVENESS_PROFILES),
                    help="'pid' opts this record into the liveness accelerator; "
                         "pass it only when --pid outlives the beat")
    hb.add_argument("--ttl", type=int, default=None)
    hb.add_argument("--path", action="store_true",
                    help="print the record's file path instead of the record")
    hb.add_argument("extras", nargs="*", metavar="key=val",
                    help="folded into the record's meta")

    for name, helptext in (("get", "one session's record, expired or not"),
                           ("release", "withdraw a presence now")):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("session_id")

    for name, helptext in (("list", "live presences"),
                           ("directory", "live presences joined to their subjects")):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("--kind", default=None, choices=list(SESSION_KINDS))
        p.add_argument("--subject-kind", default=None, choices=list(SUBJECT_KINDS))
        p.add_argument("--subject-id", default=None)
        p.add_argument("--scope", default=None)
        if name == "list":
            p.add_argument("--all", action="store_true",
                           help="include expired records")

    pr = sub.add_parser("prune", help="delete long-expired records (never called on read)")
    pr.add_argument("--older-than", type=int, default=86400)

    a = ap.parse_args(argv)

    if a.cmd == "heartbeat":
        rec = beat(a.session_id, session_kind=a.kind, activity=a.activity,
                   subject_kind=a.subject_kind, subject_id=a.subject_id,
                   scope=a.scope, principal=a.principal, pid=a.pid,
                   liveness_profile=a.profile, ttl_sec=a.ttl,
                   meta=_kv(a.extras))
        if a.path:
            print(_session_path(a.session_id))
        else:
            print(json.dumps(rec))
        return 0
    if a.cmd == "get":
        print(json.dumps(get(a.session_id)))
        return 0
    if a.cmd == "release":
        print(json.dumps({"released": release(a.session_id)}))
        return 0
    if a.cmd == "list":
        rows = (list_all() if a.all else
                list_live(session_kind=a.kind, subject_kind=a.subject_kind,
                          subject_id=a.subject_id, scope=a.scope))
        print(json.dumps(rows))
        return 0
    if a.cmd == "directory":
        print(json.dumps(directory(session_kind=a.kind, subject_kind=a.subject_kind,
                                   subject_id=a.subject_id, scope=a.scope)))
        return 0
    if a.cmd == "prune":
        print(json.dumps({"pruned": prune(older_than_sec=a.older_than)}))
        return 0
    return 2


if __name__ == "__main__":  # `python3 -m services.presence ...`
    sys.exit(main())
