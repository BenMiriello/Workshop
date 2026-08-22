"""Structured task store (canonical Task model, orchestration-and-autonomy O1).

WHICH STORE DEPENDS ON THE ROOT, and there is exactly one per root. The
relational scopes (`development`, `workshop` -- see
`services.task_scope.RELATIONAL_SCOPES`) keep their tickets in Postgres and
have no `.tasks.json` at all; ``TODO.md`` there is a ONE-WAY export and is never
read back. Every other working tree -- project trees, agent scopes -- is
file-backed at ``<root>/.tasks.json``, with ``TODO.md`` as a regenerated export
plus a healed inbox: a stray hand edit is absorbed by ``reconcile`` on the next
read, never destroyed.

The two branches are chosen by `is_relational_scope`, which is a fact about the
PATH. It is never chosen by whether the database happens to be reachable, and
never by whether the running interpreter can import a database driver. Both of
those were tried; both produced a second live ticket store that diverged from
the first (`docs/platform-redesign/tickets-postgres-cutover-2026-08-16.md`).

Canonical Task (version 2)::

    {
      "id": str,                       # uuid4 hex, stable across reorders
      "title": str,                    # CANONICAL. One line, written not clamped
      "body": str,                     # CANONICAL. The spec a worker acts on,
                                       # with its Objective / Output / Start from
                                       # / Boundaries sections as labelled lines
                                       # (services/ticket_format owns the labels)
      "text": str,                     # PROJECTION of the pair, `title\\n\\nbody`,
                                       # re-derived by `_normalize` on every read
                                       # and NEVER stored (it is in
                                       # _PROJECTED_KEYS). A ticket has been a
                                       # title and a body since 010a9a4; `text`
                                       # survives as the read shape the TODO.md
                                       # export, reconcile, dedup, run_inbox and
                                       # the shell CLI were written against.
      "status": "todo"|"doing"|"in_review"|"blocked"|"parked"|"done"|"cancelled",
      "assignee": None|"agent:<slug>"|"operator",   # the delegate (human owns)
      "parent": str | None,
      "priority": int,                 # higher first; default 0
      "depends_on": [task_id],         # gates readiness
      "acceptance": {"kind": "reviewer"|"command"|"operator", "spec": str|None,
                     # optional machine-checkable assertions run by the gate; ALL
                     # must pass. Each: {"desc": str, "cmd": str, "expect": "exit0"|str}
                     # (expect "exit0" => command exits 0; any other string => that
                     # substring must appear in the command's stdout).
                     "criteria": [{"desc": str, "cmd": str, "expect": str}]}|None,
      "max_attempts": int | None,      # per-task cap on worker RUNS (sweep attempts);
                                       # distinct from ralph's inner iteration cap.
      "blocked_reason": str | None,   # the human line. UNCHANGED, still first-class.
      "awaiting": {...}|None,          # THE block record. See "The block record" below.
      "attempts": int,                 # worker runs spent on this task (cap enforcement)
      "claim_token": int | None,       # monotonic fencing generation, bumped every
                                       # (re)claim AND on any operator edit to a
                                       # claimed task (Kleppmann fencing token)
      "lease_until": iso | None,       # claim lease; set while doing, cleared on release
      "last_heartbeat": iso | None,    # worker's last lease renewal (missed -> reclaim)
      "claim_pid": int | None,         # pid of the sweep that holds the claim (liveness)
      "job_ids": [str],                # executions that worked this task
      "evidence": [{"kind": str, "ref": str, "ts": iso}],
      "source": {"kind": "operator"|"chat"|"agent"|"autogen", "ref": str|None},
      "provenance": str | None,        # stable dedupe key
      "touches": [str],                # path globs this task writes (fan-out safety).
                                       # ENFORCED: a run that writes outside them
                                       # blocks the task (see enforce_touches).
                                       # [] = unrestricted (the default).
      "discovered_from": str | None,   # task id that spawned this follow-up
      "effort": str | None,            # quick|standard|deep hint (aliases s/m/l
                                       # and any case accepted on read)
      "latitude": str | None,          # "strict"|"standard"|"open" — what the
                                       # worker may do where THIS ticket does not
                                       # say. None means INHERIT (not "standard"):
                                       # the cascade is invocation > task > scope
                                       # > DEFAULT_LATITUDE, so a stored default
                                       # here would silently undo a strict scope.
                                       # See services/latitude.py.
      "created": iso, "updated": iso
    }

The block record (P2)
---------------------
A task stops for many reasons: a permission it does not have, a missing resource,
an ambiguous spec at ``strict`` latitude, a contradiction. All of them are the
SAME primitive — ``awaiting`` — widened in place rather than given a sibling
status or a parallel record. ``awaiting`` was ``{"question", "since"}``; a bare
question cannot say *what would unblock me* in a form the system can check, so it
now carries::

    "awaiting": {
      "question": str,       # EXISTING. the human sentence; still first-class.
      "since": iso,          # EXISTING.
      "kind": str,           # short slug: permission|resource|input|decision|
                             # missing-info|dependency|environment|other. OPEN,
                             # deliberately not an enum: a closed set fails on the
                             # first unanticipated reason to stop.
      "ask": str,            # the request itself (defaults to `question`)
      "why": str | None,     # why the task cannot proceed without an answer
      "unblocks_if": str | None,   # what would resolve it, in one line
      "options": [str]|None, # kind=decision: what the worker would choose between
      "default": str | None, # what it WOULD do at a higher latitude
      "proposal": {"uri": str, "verb": str}|None,   # the machine-checkable ask.
                             # Present => a grant can be prefilled from the block;
                             # absent => the answer is a judgment, so a textual
                             # rule is what the review offers instead.
      "raised_by": str|None, # "job:<id>" / "reviewer" / "gate"
      "dedupe_key": str|None,# groups a mass-block: 10 tasks stopped by one rule
                             # answer together, in one gesture.
      "context": {...},      # free-form detail for the reviewer's eyes
      "dismissed": bool,     # operator left the block in place and took it off
                             # the review queue. NOT a status: the task is still
                             # blocked, it is just not being asked about.
    }

Every field is additive, so a stored ``{"question", "since"}`` record keeps
working unchanged and older readers keep rendering the question. ``blocked_reason``
is untouched and still carries the human line, so blocked tasks written before
this and the ``[!]`` TODO.md box both behave exactly as they did.

Backward compatibility: records carry an ``owner`` field mirroring ``assignee``
so existing readers (frontend task panel, per-agent endpoints) keep working while
callers migrate. Legacy ``owner`` on read maps to ``assignee`` (an agent slug
becomes ``agent:<slug>``; ``operator`` stays). ``blocked_request`` mirrors
``awaiting`` the same way: it is the name the plan, the API and the Review tab use
for the structured request, projected on read from the ONE stored record. There is
no second record to keep in sync.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import re
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from services import latitude as _latitude

log = logging.getLogger(__name__)

# draft = filed but not yet ready to run (Triage/Backlog in every other tracker).
# in_review = worker finished, operator is the gate; cancelled = dropped.
# parked = dead-letter: a poison-pill task that exhausted its attempt cap. Not
# terminal (the operator still sees it as open work), but next_ready never picks
# it, so it stops burning the budget every sweep. Self-healing design 2d.
STATUSES = {"draft", "todo", "doing", "in_review", "blocked", "parked", "done",
            "cancelled"}
TERMINAL = {"done", "cancelled"}
DEFAULT_OWNER = "operator"  # canonical assignee for the human

# Two-level status (ticket-system-consolidation-2026-08-12 §3.1). The eight named
# statuses above stay exactly as they are; this is the coarse CATEGORY every
# other tracker also models, so a board can group without inventing lanes.
# `blocked` is `started` because the work has begun and is stuck (Linear's and
# Shortcut's reading); `parked` is `backlog` because it is out of the active plan
# but not finished.
#
# Spelling: the type is `canceled` with ONE l, matching every tracker's API,
# while the status value stays `cancelled` with two because it is persisted in
# live records and renaming it buys nothing.
#
# DERIVED, NEVER STORED. `_for_storage` strips only `_PROJECTED_KEYS`, so writing
# this onto a record inside `_normalize` would persist it into .tasks.json and
# make it a second source of truth. It is computed on the way out, in the route
# layer, and the record on disk never carries it.
#
# CANONICAL, for the JSON store and for the schema both: db/models.py imports
# this as `STATE_CATEGORIES` and builds its CHECK constraint from it. It lives
# here rather than there because this store is the one that has to heal a
# hand-edited TODO.md against the vocabulary, and because the reverse direction
# would drag SQLAlchemy into every importer of this module.
#
# Linear's five, verified against its documentation. Plane adds a sixth
# (`triage`); it is skipped because `draft` already covers that lane here, and
# adding a category to serve one state would be inventing a distinction rather
# than adopting one.
STATE_TYPES = ("backlog", "unstarted", "started", "completed", "canceled")

_STATE_TYPE = {
    "draft": "backlog",
    "parked": "backlog",
    "todo": "unstarted",
    "doing": "started",
    "in_review": "started",
    "blocked": "started",
    "done": "completed",
    "cancelled": "canceled",
}


def state_type(status) -> str:
    """The coarse category for one of the eight statuses. An unrecognised status
    reads as `backlog`, matching `_normalize`'s bias toward "not started yet"
    for a record it cannot classify — a board must never drop a row."""
    return _STATE_TYPE.get(str(status or ""), "backlog")

# The sync boundary, in ONE place (ticket-model-and-external-trackers-2026-08-07).
# These are the only fields that may ever cross to an external tracker; every
# other field on the record is workshop-private execution state. A field added to
# the record is private by default: it has to be named here to leave the box.
SYNCABLE_FIELDS = ("title", "body", "status", "labels", "assignee", "priority")

# The execution fields, named once so the projection below and any future sync
# adapter agree on what is private. Mirrors `exec` in the design doc; storage
# stays flat (every existing reader indexes these directly) and `exec` is a
# read-side projection, the same discipline `owner`/`blocked_request` follow.
EXEC_FIELDS = ("touches", "latitude", "acceptance", "max_attempts", "attempts",
               "claim_token", "lease_until", "last_heartbeat", "claim_pid",
               "job_ids", "evidence", "awaiting", "blocked_reason", "triage")

# Self-healing (design 2d + Fix 2): a claim carries a lease so a crashed/killed
# sweep never strands a task in `doing` forever, but a HEALTHY-but-slow worker must
# never be reclaimed out from under itself. A single ralph iteration is one
# `claude -p` turn that can run minutes (per-iter timeout up to ~1500s), so a fixed
# wall-clock lease sized to a "typical" run spuriously reclaims a slow tick. The
# lease is therefore sized as a MISSED-HEARTBEAT window, not a total-runtime guess:
# the worker renews it from inside ralph.sh's loop (see heartbeat()), and reclaim
# fires only when now - last_heartbeat exceeds this TTL. Default 3600s (60 min) is
# generous: it comfortably clears the longest single iteration + reviewer gap while
# the worker heartbeats every iteration (well under 50% of TTL). Env-overridable.
LEASE_TTL_SEC = int(os.environ.get("WORKSHOP_TASK_LEASE_TTL_SEC", "3600"))
# Attempt cap used by reclaim when a task carries no per-task max_attempts.
# Mirrors project_ralph.sh DEFAULT_MAX_ITER so shell and store agree.
RECLAIM_ATTEMPT_CAP = int(os.environ.get("WORKSHOP_TASK_ATTEMPT_CAP", "3"))

STORE_NAME = ".tasks.json"
EXPORT_NAME = "TODO.md"
LOCK_NAME = ".tasks.json.lock"
STORE_VERSION = 2

# Roots this store must NOT manage. Empty by default since the 2026-07-15 root
# migration (Stage 3 / O7): the workshop root's legacy prose TODO.md was archived
# to docs/archive/root-todo-legacy-2026-07-15.md and the structured store became
# canonical at root, so nothing is protected anymore. Still env-overridable
# (`WORKSHOP_TASKS_PROTECTED_ROOTS`, colon-separated) as an escape hatch.
_PROTECTED_ROOTS = {
    Path(p).resolve()
    for p in os.environ.get("WORKSHOP_TASKS_PROTECTED_ROOTS", "").split(":")
    if p.strip()
}


def _is_protected(root: Path) -> bool:
    try:
        return root.resolve() in _PROTECTED_ROOTS
    except OSError:
        return False


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_iso(s) -> datetime | None:
    """Parse a stored ISO timestamp into an aware datetime, or None."""
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s))
    except (TypeError, ValueError):
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _pid_alive(pid) -> bool:
    """True if a process with this pid is alive (signal 0 probe). Unknown pid
    (None / unparseable) is treated as NOT alive so a claim with no recorded pid
    is reclaimable once its lease expires."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by someone else
    except OSError:
        return False
    return True


def _would_cycle(items: list[dict], task_id: str, new_deps: list[str]) -> bool:
    """Would adding these deps to `task_id` form a cycle in depends_on?

    Walks forward from each new dep through the current graph. If task_id is
    reachable, the edge closes a cycle. `items` is the current store; new_deps
    is the proposed depends_on list for task_id.

    Cheap: O(V+E) worst case, one-shot; runs on every update so the DAG stays
    a DAG (entity-model 2026-07-18 §4.4). Ignores self-links (they never gate
    anything and are stripped by the caller anyway)."""
    by_id = {str(t.get("id")): t for t in items if t.get("id")}
    def _reaches(start: str, target: str) -> bool:
        # BFS forward. Deps of `start`'s task, then their deps, etc.
        seen: set[str] = set()
        stack = [start]
        while stack:
            cur = stack.pop()
            if cur == target:
                return True
            if cur in seen:
                continue
            seen.add(cur)
            t = by_id.get(cur)
            if not t:
                continue
            for d in t.get("depends_on") or []:
                stack.append(str(d))
        return False
    return any(str(d) != task_id and _reaches(str(d), task_id) for d in new_deps)


def _new_id() -> str:
    return uuid.uuid4().hex


def _store_path(root: Path) -> Path:
    return root / STORE_NAME


def _export_path(root: Path) -> Path:
    return root / EXPORT_NAME


def _lock_path(root: Path) -> Path:
    return root / LOCK_NAME


# ---- IO --------------------------------------------------------------------

def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(content)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


@contextmanager
def _locked(root: Path):
    """flock around a load-modify-persist so the backend API and the shell CLI
    (project_tasks.py) can genuinely interleave writes to the same store during
    a sweep without clobbering each other. One lock file, one module, every
    writer goes through here."""
    root.mkdir(parents=True, exist_ok=True)
    lp = _lock_path(root)
    f = open(lp, "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(f, fcntl.LOCK_UN)
        finally:
            f.close()


def _read_payload(root: Path) -> tuple[list[dict] | None, str | None]:
    """Return (raw task list, export_sha) from .tasks.json, or (None, None)."""
    p = _store_path(root)
    if not p.exists():
        return None, None
    try:
        data = json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return None, None
    if not isinstance(data, dict):
        return None, None
    tasks = data.get("tasks")
    export_sha = data.get("export_sha")
    return (tasks if isinstance(tasks, list) else None,
            export_sha if isinstance(export_sha, str) else None)


def _read_store(root: Path) -> list[dict] | None:
    tasks, _ = _read_payload(root)
    return tasks


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---- Normalization ---------------------------------------------------------

def _assignee_from_legacy(task: dict) -> str | None:
    """Map a record's assignee/owner to the canonical assignee. Legacy `owner`:
    'operator' stays; a bare agent slug becomes 'agent:<slug>'; blank -> operator."""
    a = task.get("assignee")
    if a is not None:
        return a or None
    owner = task.get("owner")
    if owner is None or owner == "" or owner == DEFAULT_OWNER:
        return DEFAULT_OWNER
    return owner if str(owner).startswith("agent:") else f"agent:{owner}"


# ---- title and body ---------------------------------------------------------
# `title` and `body` are STORED fields, the way every tracker on earth models a
# ticket (ticket-model-and-external-trackers-2026-08-07 D1). `text` survives as a
# read-side projection (`title` + "\n" + `body`) so TODO.md export, reconcile,
# dedup and every existing reader keep working byte-identically.
#
# This replaces the first-line convention of ADR 0001. That convention needed a
# SPLIT rule at every boundary, the rule was lossy, and it mangled 32 of 179 live
# tickets into a title ending in "…". A join rule is one pure function and cannot
# lose anything. Producers are prompts and will keep emitting one long paragraph,
# so the store still normalizes on write and on read — but losslessly now: an
# over-long title moves its remainder DOWN into the body and nothing is elided.
TITLE_MAX = 120        # hard cap: longer than this and the remainder moves to body
TITLE_WARN = 80        # a good title is under this; not enforced, reported
_TITLE_MIN_CUT = 28    # a cut leaving less than this is a fragment, not a title
# Where a title may end, best first. The match IS the separator, so the text
# before it keeps its punctuation and the text after it starts the body.
#   1. end of a sentence or clause — a whole thought.
#   2. a comma or a closed parenthetical — a weaker seam, but "[SEC-A5]
#      Personal-data partition is app-layer only (contextvar)" is a real title
#      and the sentence tier alone would never find it.
_CLAUSE_BREAKS = (
    re.compile(r"(?<=[.:;?!])\s+|\s+[-—–]\s+"),
    re.compile(r"(?<=[,)])\s+"),
)


def _balanced(s: str) -> bool:
    """No bracket left hanging open."""
    return s.count("(") == s.count(")") and s.count("[") == s.count("]")


def _split_overlong_title(head: str) -> tuple[str, str]:
    """(title, the part of `head` that belongs in the body).

    Cuts are rejected where they would leave a bracket open. Without that guard
    the comma tier turns "DEFERRED DECISION (operator must weigh in; gated on
    ...)" into "DEFERRED DECISION (operator must weigh in" — an unbalanced
    fragment, which reads worse than the over-long line it replaced. A title
    that is merely long is honest; one that looks like it was chopped is not.
    """
    # Prefer the LAST boundary that still fits, so the title is the most
    # complete thought that can be a title rather than the first fragment.
    for pattern in _CLAUSE_BREAKS:
        cut = None
        for m in pattern.finditer(head[: TITLE_MAX + 1]):
            if m.start() >= _TITLE_MIN_CUT and _balanced(head[: m.start()]):
                cut = m
        if cut is not None:
            return head[: cut.start()].rstrip(" .:;,"), head[cut.end():].strip()
    # Nothing to cut on: break at the last word boundary that fits and move the
    # remainder into the body. NO ellipsis is emitted — an elided title claimed
    # information had been dropped when it had not, and because the preview was
    # written back to the store, a later change to TITLE_MAX stacked a second
    # preview on the first. A word-boundary break is lossless and idempotent:
    # re-normalizing the joined text reproduces exactly the same split.
    space = head.rfind(" ", 0, TITLE_MAX)
    head_cut = space if space >= _TITLE_MIN_CUT else TITLE_MAX
    return head[:head_cut].rstrip(" .:;,"), head[head_cut:].strip()


def _repair_elided_title(title: str, body: str) -> tuple[str, str]:
    """Undo an old ellipsis clamp: rejoin "Do the thing…" + its spilled remainder.

    The retired clamp wrote `head[:cut] + "…"` as the title and `head[cut:]` as
    the FIRST line of the body, so the original line is recoverable exactly by
    concatenating the two. 32 of 179 live tickets are in this state.

    Guarded so a human-written title that happens to end in an ellipsis is never
    touched: the rejoined line must actually be over-long (which is the only
    condition under which the clamp fired), and the spilled fragment must look
    like a continuation rather than the start of a real body. Two things count as
    a continuation — a fragment that does not begin a new sentence, or a title
    left holding an open bracket, which only a mid-construct cut produces.
    """
    if not title.endswith("…") or not body:
        return title, body
    stem = title[:-1].rstrip(" .:;,")
    if len(stem) < _TITLE_MIN_CUT:
        return title, body
    spill, sep, rest = body.partition("\n")
    spill = spill.strip()
    if not spill:
        return title, body
    if spill[0].isupper() and _balanced(stem):
        return title, body
    rejoined = f"{stem} {spill}".strip()
    if len(rejoined) <= TITLE_WARN:
        return title, body   # too short to have tripped the clamp; not our output
    return rejoined, rest


def _undo_stacked_preview(body: str) -> str:
    """Drop an elided title line that the NEXT line just repeats.

    Repairs the retired clamp's earlier output. An elided title used to be
    prepended while the whole original line was kept below it, so every pass at a
    new TITLE_MAX stacked another preview: one ticket ended up as
    "...secrets out of…" / "...secrets out of container env into a…" / the real
    line. Collapsing them on read means stored records heal themselves. Kept
    (though nothing emits previews anymore) because stores on disk still carry
    them.
    """
    while True:
        head, sep, rest = body.partition("\n")
        if not sep or not head.endswith("…"):
            return body
        stem = head[:-1].rstrip(" .:;,")
        if len(stem) < _TITLE_MIN_CUT or not rest.startswith(stem):
            return body
        body = rest


def split_text(text) -> tuple[str, str]:
    """(title, body) for a blob of ticket text. THE split rule, in one place.

    Idempotent: ``split_text(join_text(*split_text(x))) == split_text(x)`` for any
    input, which is what makes normalize-on-read safe to run forever.
    """
    blob = _undo_stacked_preview(str(text or "").strip())
    head, _sep, rest = blob.partition("\n")
    title, body = _repair_elided_title(head.strip(), rest)
    if len(title) > TITLE_MAX:
        title, spilled = _split_overlong_title(title)
        body = f"{spilled}\n{body}" if (spilled and body) else (spilled or body)
    return title.strip(), body.strip("\n")


def join_text(title: str, body: str) -> str:
    """THE join rule: the projected `text` every legacy reader still sees.

    One newline, not two, so a record that round-trips through the TODO.md
    export renders byte-identically to how it did when `text` was stored.
    """
    title = (title or "").strip()
    body = (body or "").strip("\n")
    return f"{title}\n{body}" if body else title


def _normalize_text(text) -> str:
    """Back-compat shim: normalize a text blob through the split/join pair."""
    return join_text(*split_text(text))


def title_warnings(title: str) -> list[str]:
    """Why this title reads badly, for the readiness check and the UI. Empty is
    a good title. Length is a WARNING, never a rejection: refusing a long title
    would just lose the ticket a producer was trying to file."""
    out: list[str] = []
    t = (title or "").strip()
    if not t:
        out.append("title is empty")
    if len(t) > TITLE_WARN:
        out.append(f"title is {len(t)} chars; a title reads best under {TITLE_WARN}")
    if t.endswith("…") or t.endswith("..."):
        out.append("title is elided, not written")
    return out


def _normalize_acceptance(acc) -> dict | None:
    """Coerce a stored acceptance into a well-formed record, losslessly. Preserves
    every existing key (kind/spec/allow_no_diff/...) verbatim so callers that read
    them are unaffected, and only re-shapes the optional ``criteria`` list of
    machine-checkable assertions. Each criterion normalizes to
    ``{"desc", "cmd", "expect"}``: a criterion with no ``cmd`` is dropped, ``desc``
    defaults to the ``cmd``, and ``expect`` defaults to ``"exit0"``. A missing or
    empty criteria list => the key is removed (missing means none), so a task with
    no assertions round-trips exactly as before this field existed."""
    if not isinstance(acc, dict):
        return None
    out = dict(acc)
    raw = out.get("criteria")
    norm: list[dict] = []
    if isinstance(raw, list):
        for c in raw:
            if not isinstance(c, dict):
                continue
            cmd = str(c.get("cmd") or "").strip()
            if not cmd:
                continue
            desc = str(c.get("desc") or cmd).strip()
            expect = c.get("expect")
            expect = str(expect).strip() if expect not in (None, "") else "exit0"
            norm.append({"desc": desc, "cmd": cmd, "expect": expect})
    if norm:
        out["criteria"] = norm
    else:
        out.pop("criteria", None)
    return out or None


# --- the block record --------------------------------------------------------
# `awaiting` is the general escalation primitive: whatever stopped the task — a
# permission, a missing fixture, an ambiguous ticket, a contradiction — writes the
# same record, so one review surface serves all of them. The producers (the gate,
# the worker at `strict` latitude, the reviewer's block-for-decision verdict, the
# harness) differ; the record does not.

# A `kind` is a short slug, validated as a slug and NOT against a fixed list. A
# closed enum would reject the first reason to stop that nobody anticipated,
# which is exactly the case a block loop exists to catch. (A named seed list —
# permission, resource, input, decision, missing-info, dependency, environment,
# other — used to live here as a constant; it had zero readers, because nothing
# validates against it, so it was deleted rather than kept as unread
# documentation.)
_KIND_RE = re.compile(r"[a-z][a-z0-9-]{0,31}\Z")
_VALID_PROPOSAL_VERBS = ("read", "write", "append")


def _slug_or(value, default: str) -> str:
    v = str(value or "").strip().lower()
    return v if _KIND_RE.fullmatch(v) else default


def _normalize_awaiting(payload) -> dict | None:
    """Coerce a block record into canonical shape, or None when there is nothing
    to ask (no `ask`/`question` => no block record; that is how a caller clears
    one).

    Legacy `{"question": ..., "since": ...}` records normalize losslessly: `ask`
    mirrors the question and `kind` reads as `input`, which is what a bare
    `awaiting` has always meant (operator input this task waits on)."""
    if not isinstance(payload, dict):
        return None
    question = str(payload.get("question") or "").strip()
    ask = str(payload.get("ask") or "").strip() or question
    question = question or ask
    if not ask:
        return None
    proposal = payload.get("proposal")
    if isinstance(proposal, dict):
        uri = str(proposal.get("uri") or "").strip()
        verb = str(proposal.get("verb") or "").strip().lower()
        # A proposal is the machine-checkable half of the ask; a half-formed one
        # would prefill a grant over nothing, so it degrades to "no proposal" (a
        # judgment call) rather than to a broken prefill.
        proposal = {"uri": uri, "verb": verb} if uri and verb in _VALID_PROPOSAL_VERBS else None
    else:
        proposal = None
    options = [str(o).strip() for o in (payload.get("options") or []) if str(o).strip()]
    context = payload.get("context")
    rec = {
        "question": question,
        "since": str(payload.get("since") or _now()),
        "kind": _slug_or(payload.get("kind"), "input"),
        "ask": ask,
        "why": str(payload.get("why") or "").strip() or None,
        "unblocks_if": str(payload.get("unblocks_if") or "").strip() or None,
        "options": options or None,
        "default": str(payload.get("default") or "").strip() or None,
        "proposal": proposal,
        "raised_by": str(payload.get("raised_by") or "").strip() or None,
        "dedupe_key": str(payload.get("dedupe_key") or "").strip() or None,
        "context": context if isinstance(context, dict) else {},
        "dismissed": bool(payload.get("dismissed")),
    }
    return rec


# One vocabulary for effort, canonicalized on read and write.
#
# There were three. The store's docstring said "s"|"m"|"l", the task-proposal card
# and the drafting prompt say quick|standard|deep, and the live stores held both
# (23 tickets "S", 14 "M", 3 "deep", 1 "standard", 1 "L"). That was not merely
# untidy: `scripts/project_ralph.sh` maps effort to an inner-loop cap and a model
# tier with a case-SENSITIVE bash `case` on `quick|q|s` / `deep|d|l`, so every
# ticket stored as uppercase "S" or "L" fell through to the default branch and its
# effort hint did nothing at all. 24 of the 38 tickets that set one.
#
# Canonicalizing here fixes the sweep without touching it, because it already
# understands the canonical words.
_EFFORT_ALIASES = {
    "quick": "quick", "q": "quick", "s": "quick", "small": "quick", "xs": "quick",
    "standard": "standard", "m": "standard", "med": "standard",
    "medium": "standard", "normal": "standard",
    "deep": "deep", "d": "deep", "l": "deep", "large": "deep", "xl": "deep",
}


def _normalize_effort(value) -> str | None:
    """quick | standard | deep, or None when unset. An unrecognized value maps to
    None rather than to "standard": a typo must not read as a deliberate hint."""
    key = str(value or "").strip().lower()
    if not key:
        return None
    return _EFFORT_ALIASES.get(key)


EFFORTS = ("quick", "standard", "deep")


# --- execution: can a machine do this at all ---------------------------------
# A SPEC field, not an exec field: it is a thing the operator sets about the work
# itself, and an external tracker would legitimately want it. It is the selection
# predicate for UNATTENDED dispatch (next_ready(unattended=True)) and nothing
# else — it never narrows what a human may run.
#
#   code       a change to a tree this worker can write        eligible
#   judgement  the output is a decision, a design or an audit  never
#   external   needs root, another host user, a vendor console never
#   None       not yet classified                              never (fail closed)
#
# None is NOT code. The queue is ordered priority-first and the head of it is a
# root-requiring security ticket, so an unattended sweep that guessed would spend
# its first five runs failing on work no agent can do.
EXECUTIONS = ("code", "judgement", "external")


def _normalize_execution(value) -> str | None:
    """One of EXECUTIONS, or None when unset OR unrecognized. A typo coerces to
    None (unclassified, ineligible) rather than to "code": the fail-closed
    direction is the whole point of the field."""
    key = str(value or "").strip().lower()
    return key if key in EXECUTIONS else None


# --- triage: the mark that stops the queue triager re-judging -----------------
# An EXEC field (run metadata, never synced). The queue triager reads the store
# it writes, so it cannot use the note triager's mtime watermark: `update()` sets
# `updated` on EVERY write, so a frontier keyed on it re-selects every ticket the
# triager itself just wrote to, forever. The note triager is safe from this only
# because that role cannot write notes.
#
# The mark is therefore a hash of the fields a triage DECISION depends on, and
# only those. `triage_due` compares the stored hash to the live one.
TRIAGE_DECISIONS = ("ok", "split", "underspecified", "none")


def _normalize_triage(payload) -> dict | None:
    """Canonical triage mark, or None. An unrecognized decision reads as `none`
    (looked at, nothing to do) rather than being dropped: losing the mark would
    re-select the ticket, which is the failure mode this field exists to stop."""
    if not isinstance(payload, dict):
        return None
    h = str(payload.get("spec_hash") or "").strip()
    if not h:
        return None
    decision = str(payload.get("decision") or "").strip().lower()
    return {
        "at": str(payload.get("at") or _now()),
        "decision": decision if decision in TRIAGE_DECISIONS else "none",
        "spec_hash": h,
        "reason": str(payload.get("reason") or "").strip() or None,
    }


def spec_hash(task: dict) -> str:
    """Hash of the fields a triage decision depends on, and ONLY those.

    Deliberately NOT `updated`: `update()` sets it on every write, so a frontier
    keyed on it never terminates (see the section comment above). Also excluded,
    each for its own reason:

      status              todo -> doing -> todo is not a new question
      awaiting            the triager's own `underspecified` write
      evidence/job_ids/attempts   run history, not spec
      execution/priority  change ROUTING, not whether the spec is adequate

    `test_triage_mark.py` asserts this hash is stable across an `awaiting` write.
    That test is the regression guard for the infinite loop and must fail if
    `updated` is ever added to the payload below.
    """
    payload = json.dumps({
        "title": str(task.get("title") or "").strip(),
        "body": str(task.get("body") or "").strip(),
        "depends_on": sorted(str(d) for d in (task.get("depends_on") or [])),
        "effort": task.get("effort") or "",
    }, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def triage_due(task: dict) -> bool:
    """Has this ticket's SPEC changed since it was last triaged? No mark means
    due. A rewritten body means due again, which is correct: the question
    genuinely changed. The triager answering its own question does not."""
    return (task.get("triage") or {}).get("spec_hash") != spec_hash(task)


# Which `awaiting.kind` values a latitude-permitted auto-resolve may ever expire.
#
# AN ALLOWLIST, NOT A DENYLIST, AND THIS IS THE LOAD-BEARING DETAIL.
# `_normalize_awaiting` defaults `kind` to "input", and its docstring notes that
# a legacy `{question, since}` record also reads as `input`. `input` expires. So
# a denylist of permanent kinds would make every unlabelled block and every
# pre-existing block silently auto-resolvable on the day expiry ships.
#
# Anything outside this set waits for a human, forever, including `permission`
# and including any future slug. `kind` stays an open set, which is exactly why
# the default has to be the safe side of the line.
EXPIRING_AWAITING_KINDS = frozenset({"input", "decision", "missing-info"})


def awaiting_may_expire(task: dict) -> bool:
    """Whether a block MAY be auto-resolved on timeout — the kind filter only.

    The rest of the ladder (resolved latitude is standard|open, a non-null
    `default`, the decision is reversible, the block was not raised by the
    reviewer) is ratified in latitude-model-2026-07-18.md §158-167 and lives with
    the caller that knows the resolved latitude. This adds no new policy."""
    rec = task.get("awaiting") or {}
    if not rec:
        return False
    return str(rec.get("kind") or "") in EXPIRING_AWAITING_KINDS


def _norm_labels(raw) -> list[str]:
    """Labels as an ordered, deduped list of non-empty strings."""
    out: list[str] = []
    for label in (raw or []):
        s = str(label).strip()
        if s and s not in out:
            out.append(s)
    return out


# A comment is `{id, author, text, ts}`. `author` is free text on purpose: it is
# "operator", "agent:<slug>", or a run id, and pinning it to an enum now would
# just mean rewriting it the first time something new writes one. Order is
# preserved (append-only); an entry with no text is dropped rather than rendered
# as an empty bubble.
def _norm_comments(raw) -> list[dict]:
    out: list[dict] = []
    for c in (raw or []):
        if not isinstance(c, dict):
            continue
        text = str(c.get("text") or "").strip()
        if not text:
            continue
        out.append({
            "id": str(c.get("id") or _new_id()),
            "author": str(c.get("author") or DEFAULT_OWNER),
            "text": text,
            "ts": str(c.get("ts") or _now()),
        })
    return out


# A ticket whose scope nobody is accountable for is how a bad spec becomes a
# mystery. Records written before `scoped_by` existed get the best available
# answer from where the request came from, rather than a null that reads as "no
# one" when the truth is "not recorded".
_SCOPED_BY_FROM_SOURCE = {"operator": "operator", "chat": "operator",
                          "import": "import", "agent": "agent", "autogen": "agent"}


def _scoped_by_from_source(src: dict) -> str:
    kind = str(src.get("kind") or "operator")
    return _SCOPED_BY_FROM_SOURCE.get(kind, "operator")


def _normalize(task: dict) -> dict:
    """Coerce a stored record into a well-formed canonical Task (defensive
    against hand edits and forward-migrating v1 records losslessly)."""
    status = task.get("status")
    if status not in STATUSES:
        status = "done" if task.get("done") else "todo"
    now = _now()
    assignee = _assignee_from_legacy(task)
    acceptance = _normalize_acceptance(task.get("acceptance"))
    src = task.get("source")
    if not isinstance(src, dict):
        src = {"kind": "operator", "ref": None}
    # title/body are canonical; `text` is projected from them below. A record
    # written before this (title absent) migrates here on READ, so no store needs
    # a rewrite pass to load correctly — the repair lands the next time it
    # persists. Migration is therefore idempotent by construction.
    if task.get("title") is not None:
        title, body = str(task.get("title") or "").strip(), \
            str(task.get("body") or "").strip("\n")
        # A record can carry an elided title from the retired clamp even after it
        # has the pair (it was migrated before the repair existed, or hand-edited).
        # Running the repair on this branch too is what makes healing complete
        # rather than only reaching records that never migrated.
        title, body = _repair_elided_title(title, body)
        if len(title) > TITLE_MAX or "\n" in title:
            title, spilled = _split_overlong_title(title.replace("\n", " "))
            body = f"{spilled}\n{body}" if (spilled and body) else (spilled or body)
    else:
        title, body = split_text(task.get("text"))
    rec = {
        "id": str(task.get("id") or _new_id()),
        "title": title,
        "body": body,
        "status": status,
        "assignee": assignee,
        "parent": task.get("parent") or None,
        "priority": _as_int(task.get("priority"), 0),
        "depends_on": [str(d) for d in (task.get("depends_on") or []) if d],
        # relates_to: soft link (entity-model 2026-07-18 §4.2), UI-only, no
        # scheduling effect. Distinct from depends_on which gates readiness.
        # blocks is DERIVED from the inverse of depends_on and never stored.
        "relates_to": [str(r) for r in (task.get("relates_to") or []) if r],
        # labels: free strings, the one field every tracker has in common besides
        # title/body/state/assignee, and the default selection mechanism for an
        # external import. Deduped, order preserved.
        "labels": _norm_labels(task.get("labels")),
        "acceptance": acceptance,
        # max_attempts is the per-task cap on worker RUNS (sweep attempts), and
        # is distinct from ralph's inner iteration cap. It used to also read a
        # legacy `max_iterations` key; measured 2026-08-15, that key is on 0
        # records across all 27 stores, so the alias was reading for nobody.
        "max_attempts": _as_int_or_none(task.get("max_attempts")),
        "blocked_reason": task.get("blocked_reason") or None,
        "awaiting": _normalize_awaiting(task.get("awaiting")),
        "attempts": _as_int(task.get("attempts"), 0),
        # claim_token is a per-task monotonic generation counter. Forward-migrate
        # losslessly: a missing token or a legacy UUID/unstructured value coerces to
        # None here and is read as generation 0 by the fence (_as_int(...,0)), so the
        # next claim starts incrementing from 1. No store needs rewriting on load.
        "claim_token": _as_int_or_none(task.get("claim_token")),
        "lease_until": task.get("lease_until") or None,
        "last_heartbeat": task.get("last_heartbeat") or None,
        "claim_pid": _as_int_or_none(task.get("claim_pid")),
        "job_ids": [str(j) for j in (task.get("job_ids") or []) if j],
        "evidence": [e for e in (task.get("evidence") or []) if isinstance(e, dict)],
        # comments: the human discussion channel, distinct from `evidence`.
        # Evidence is what the SYSTEM recorded about a run (a receipt, a gate
        # verdict, a dedup merge) and carries no author; a comment is something a
        # person or an agent SAID about the ticket, and is attributed. Keeping
        # them apart is what lets the detail page show one timeline without
        # mixing "the reviewer rejected this" into "here is what I decided".
        #
        # NOT in SYNCABLE_FIELDS: a field is workshop-private unless it is named
        # there, and a comment thread is not something we mirror to a tracker
        # today (the sync boundary is title/body/status/labels/assignee/priority).
        "comments": _norm_comments(task.get("comments")),
        "source": {"kind": str(src.get("kind") or "operator"), "ref": src.get("ref")},
        "provenance": task.get("provenance") or None,
        "touches": [str(t) for t in (task.get("touches") or []) if t],
        "discovered_from": task.get("discovered_from") or None,
        "effort": _normalize_effort(task.get("effort")),
        # execution: SPEC (see EXECUTIONS). Unclassified is None and is NOT
        # eligible for unattended selection — fail closed, never guess.
        "execution": _normalize_execution(task.get("execution")),
        # triage: EXEC. The queue triager's own mark; see spec_hash().
        "triage": _normalize_triage(task.get("triage")),
        # Round-trips as None (inherit) when unset OR when the stored value is
        # junk: coercing a typo to "standard" would look like a deliberate
        # per-task default and silently undo a strict scope (latitude.py).
        "latitude": _latitude.coerce(task.get("latitude")),
        # scoped_by: who wrote this scope (accountability). Distinct from `source`,
        # which is where the REQUEST came from: a chat-sourced ticket can be scoped
        # by the ticket-maker, and when a run fails on a bad spec that difference is
        # the whole question. Defaults from source.kind for records written before it.
        "scoped_by": str(task.get("scoped_by") or _scoped_by_from_source(src)),
        # sync: present only while this ticket is LINKED to an external tracker.
        # `None` means local-only, which is most tickets and the default. The
        # shadow inside it is the last-synced value of each field, which is what
        # makes a three-way merge possible instead of a last-writer-wins clobber.
        "sync": task.get("sync") if isinstance(task.get("sync"), dict) else None,
        # external_refs: links from PAST bindings, frozen. A disconnect collapses
        # `sync` into an entry here rather than erasing it, so a ticket can always
        # say "this was BM-17 in Jira" long after that integration is gone.
        "external_refs": [r for r in (task.get("external_refs") or [])
                          if isinstance(r, dict)],
        "created": str(task.get("created") or now),
        "updated": str(task.get("updated") or now),
    }
    # `owner` mirror keeps legacy readers (frontend panel, per-agent endpoints)
    # working until they migrate to `assignee`.
    rec["owner"] = rec["assignee"] or DEFAULT_OWNER
    # `blocked_request` mirror: the same ONE record under the name the API and the
    # Review tab use for it. Projected, never stored (see the module docstring), so
    # it cannot drift from `awaiting`.
    rec["blocked_request"] = rec["awaiting"]
    # `text` mirror: title + body under the name every existing reader uses
    # (TODO.md export, reconcile, dedup, run_inbox, the CLI). Projected from the
    # canonical pair, never authoritative, so the two can never disagree.
    rec["text"] = join_text(rec["title"], rec["body"])
    # `exec` mirror: the workshop-private execution fields, gathered under one key
    # so the sync boundary is legible at a glance. Storage stays flat (every
    # reader indexes these directly); this is the projection a sync adapter reads
    # to know what it must NOT send.
    rec["exec"] = {k: rec.get(k) for k in EXEC_FIELDS}
    return rec


# --- touches: an after-the-fact audit of what a run wrote --------------------
# `touches` sat in the schema since v2, documented as "fan-out safety", and read
# by nothing. It is now checked — but check is the honest word, and BOUNDARY is
# not, so the section that used to be titled "the task's fan-out boundary" is
# titled this instead.
#
# WHEN THIS RUNS. `project_ralph.sh` calls it after the sweep returns, against
# the COMMITTED diff between the pre-run baseline and HEAD. By then the write has
# happened, been staged and been committed. Nothing here refused it, because
# nothing here was consulted while it was happening: writes to the tree go
# through the agent's own tools inside its own subprocess, where no in-process
# Python check can see them. The only preventive control available at that layer
# is not granting the tool (agent_authority.tools_for_role) or denying the
# command (agent_authority.IRREVERSIBLE_PATTERNS).
#
# WHAT IT DOES DO, which is worth having: it names the stray in an evidence line
# and blocks the ticket, so a run that wandered out of its lane surfaces to the
# operator instead of quietly landing. Detective, not preventive. Describe it
# that way everywhere — a detective control mistaken for a preventive one is
# worse than no control, because it is trusted for something it never does.
#
# touches == [] means UNRESTRICTED. That is the default and today's behavior for
# every task in every store; the check is opt-in per task, exactly as the field
# always advertised. This is not a permission system for the operator — it is a
# task saying what it is about, and the sweep reporting whether that held.
_GLOB_MAGIC = re.compile(r"[*?\[]")

# Repo-relative paths named in a ticket body, e.g. "backend/services/tasks.py" or
# "scripts/project_ralph.sh:652". Requires a directory separator, so a bare
# "tasks.py" mentioned in passing does not widen a lane.
_BODY_PATH = re.compile(r"\b((?:[\w.-]+/)+[\w.-]+\.(?:py|sh|tsx?|jsx?|md|json|css))\b")


def cited_paths(text: str) -> list[str]:
    """The repo-relative files a ticket body names as the work."""
    seen, out = set(), []
    for m in _BODY_PATH.finditer(text or ""):
        p = m.group(1)
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def lane_gaps(text: str, touches: list[str] | None) -> list[str]:
    """Files the body cites that the declared lane does NOT cover.

    A non-empty result means the ticket contradicts itself: it names work in
    files the worker is forbidden to write. That is not a hypothetical - it is
    what actually blocked four of the five tickets stuck on 2026-07-18. The
    clearest case declared a lane of exactly ONE file, the one file its own body
    says is already finished ("the computation exists; the CONSUMERS are the
    work"), while excluding all three consumers it went on to name."""
    globs = [g for g in (touches or []) if str(g).strip()]
    if not globs:
        return []          # unrestricted; nothing to contradict
    pats = [_glob_to_re(g) for g in globs]
    return [p for p in cited_paths(text)
            if not any(rx.match(p) for rx in pats)]


def _glob_to_re(pattern: str) -> re.Pattern:
    """Compile ONE path glob with the semantics people expect from a .gitignore /
    tsconfig include: `*` and `?` do not cross a directory separator, `**` does.

    A pattern with no magic at all names a path prefix (`backend` covers
    `backend/services/tasks.py`), which is what a hand-written `touches: [backend]`
    obviously means."""
    p = pattern.strip().strip("/")
    if not _GLOB_MAGIC.search(p):
        # Plain path: itself or anything under it.
        return re.compile(rf"{re.escape(p)}(?:/.*)?\Z")
    out: list[str] = []
    i = 0
    while i < len(p):
        c = p[i]
        if p.startswith("**/", i):
            out.append(r"(?:.*/)?")   # zero or more leading dirs
            i += 3
        elif p.startswith("**", i):
            out.append(r".*")
            i += 2
        elif c == "*":
            out.append(r"[^/]*")
            i += 1
        elif c == "?":
            out.append(r"[^/]")
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
    return re.compile("".join(out) + r"\Z")


def _norm_rel(path: str) -> str:
    """A repo-relative POSIX path, however the caller spelled it."""
    return str(path or "").strip().replace("\\", "/").lstrip("./").lstrip("/")


def _check_touches(root: Path, tid: str, path: str) -> bool:
    """May this task cause a write to `path`? True when it may.

    Unrestricted (True) when the task declares no `touches`, and — deliberately —
    when the task is unknown: this gate exists to hold a task to its own stated
    boundary, not to become a second, silent way for work to fail."""
    task = get_task(root, tid)
    if task is None:
        return True
    globs = [g for g in (task.get("touches") or []) if str(g).strip()]
    if not globs:
        return True
    rel = _norm_rel(path)
    if not rel:
        return True
    return any(_glob_to_re(str(g)).match(rel) for g in globs)


def touches_violations(root: Path, tid: str, paths: list[str]) -> list[str]:
    """Every path in `paths` this task was not supposed to write. Empty list when
    the task is unrestricted or every path is in bounds."""
    return [p for p in paths if p and not _check_touches(root, tid, p)]


def enforce_touches(root: Path, tid: str, paths: list[str], *,
                    job_id: str | None = None,
                    expect_claim_token: int | None = None) -> list[str]:
    """Audit what a finished run actually wrote against the task's declared
    `touches`. Returns the violating paths (empty = nothing to do).

    NOT A GATE. Called by `project_ralph.sh` after the run, on the committed
    diff: every path passed in has already been written and committed, and this
    function's job is to notice, not to prevent. The name is `enforce_` for the
    ticket's state machine — it enforces the CONSEQUENCE — and not for the write.

    On a violation: record ONE evidence line naming what strayed and BLOCK the
    task. Blocking (not failing) is the right terminal state — the work happened
    but landed outside what the ticket said it was about, which is a decision for
    the operator, not something a retry fixes."""
    bad = touches_violations(root, tid, paths)
    if not bad:
        return []
    task = get_task(root, tid)
    globs = [str(g) for g in ((task or {}).get("touches") or [])]
    shown = ", ".join(bad[:8]) + (f" (+{len(bad) - 8} more)" if len(bad) > 8 else "")
    reason = (f"touches violation: wrote outside the task's declared globs "
              f"[{', '.join(globs)}] -> {shown}")
    update(root, tid, evidence={"kind": "touches", "ref": reason}, job_id=job_id)
    update(root, tid, status="blocked", blocked_reason=reason,
           expect_claim_token=expect_claim_token)
    return bad


def _as_int(v, default: int) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _as_int_or_none(v):
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _migrate_from_todo(root: Path) -> list[dict]:
    """Build a store from an existing TODO.md, for a file-backed scope that has
    an export but no `.tasks.json` (the store was deleted, or the directory
    predates it).

    THE LAST REMAINING READ OF THE EXPORT, and it is a bootstrap, not a sync:
    it runs only for a file-backed root that has an export and NO store at all,
    and `_load` refuses to reach it for a relational scope (see its docstring).
    The two paths that ran it against a store that already existed --
    `import_markdown` and `reconcile`'s healing half -- are deleted.

    It reads through `_parse_export_blocks`, which is the only parser of this
    file. It used to read `services/todos.py`, a second parser of the same left
    over from ADR-009, whose model was a checkbox: `done: bool`. The export has
    written eight statuses since ADR 0019, so every draft, doing, in_review,
    blocked, parked and cancelled line came back through it as plain `todo` or
    `done`, and a multi-line detail block came back as its first line. One file,
    one parser; the other one is deleted.
    """
    export = _export_path(root)
    text = export.read_text() if export.exists() else ""
    now = _now()
    tasks: list[dict] = []
    for status, item_text in _parse_export_blocks(text):
        tasks.append(_normalize({
            "id": _new_id(),
            "text": item_text,
            "status": status,
            "assignee": DEFAULT_OWNER,
            "created": now, "updated": now,
            "source": {"kind": "operator", "ref": "todo-migrate"},
        }))
    return tasks


# Priority is a NAMED BAND in the schema and a signed integer in this API. The
# band is the truth (ADR 0019: the integer held 38 distinct values across 112
# tickets and none of the distinctions were meant); the integer is what the 18
# callers, the CLI and the UI still read, so it is projected back here.
#
# The values are the FLOORS of the bands migration 769d64dbfa74 defined, so a
# record that round-trips through the schema keeps its band, and any sort by
# priority keeps its order. This mapping is lossy on purpose and in one
# direction only: 87 and 92 both come back as 80, because they were always the
# same priority and only looked different.
_BAND_TO_INT = {"urgent": 100, "high": 80, "medium": 40, "low": 1, "none": 0}


def is_relational(root: Path) -> bool:
    """Whether this working tree's tickets live in Postgres rather than a file.

    True for the two migrated scopes (`development`, `workshop`) when a database
    is reachable; False for every project tree, which is still file-backed, and
    for any checkout with no database.

    THE SPLIT IS BY ROOT, NOT BY INSTALLATION, and that is why this is a
    function rather than a flag. `projects/<slug>/` trees keep their own
    `.tasks.json` and their own `TODO.md` inbox, so the file paths below are
    live code, not legacy. Deleting them wholesale would break every project.
    """
    try:
        from services import tenancy
        return tenancy.available() and is_relational_scope(root)
    except Exception:
        return False


def is_relational_scope(root: Path) -> bool:
    """Whether this root's tickets BELONG in Postgres, regardless of whether the
    database is reachable right now.

    `is_relational` conflates two questions, and the conflation is a data hazard
    on the write side. A scope whose tickets live in Postgres, during a moment
    when the database is down, would answer False and quietly take the file
    path: the write lands in `.tasks.json`, the database comes back, reads
    resume from rows, and the write is gone. Belonging is a static fact about
    the root, so the writer asks THIS and fails closed instead, and only the
    reader asks whether the database answered.

    IT ASKS PATHLIB, NOT THE DATABASE LAYER, and that is the whole point. This
    used to route through `db.ticket_sync.scope_for_root` inside a bare
    `except Exception: return False`, and `db/ticket_sync.py` imports sqlalchemy
    at module scope. Under an interpreter without the driver the import raised,
    the answer came back False for the repo root, and the retired `.tasks.json`
    became authoritative again: on 2026-08-17 a `python3` (not
    `backend/.venv/bin/python`) run of `scripts/field_occupancy.py` took the
    file branch of `_persist`, `reconcile` re-imported `TODO.md`, and four
    tickets ended up with a second id apiece behind Postgres's back. A guard
    that answers "file-backed" when it means "I could not tell" is not a guard.
    """
    from services import task_scope
    return task_scope.relational_scope_for_root(root) is not None


def has_store(root: Path) -> bool:
    """Whether this working tree has a ticket store at all, either kind.

    For the two shell callers that used `(root / ".tasks.json").exists()` as
    their guard. That test answers "is there a file", which stops being the same
    question as "is there a store" the moment a scope's tickets live in
    Postgres. A relational scope HAS a store whether or not the database is up
    at this instant, so this asks belonging, not reachability.
    """
    return is_relational_scope(root) or _store_path(root).exists()


def last_modified(root: Path) -> datetime | None:
    """When this store last changed, whichever store it is.

    `dispatch_triggers` compared `.tasks.json`'s MTIME against a job's end time
    to decide whether anything had moved since. A file that is no longer written
    has a frozen mtime, so that comparison would quietly answer "nothing
    changed" forever and the sweep would stop re-opening work that had in fact
    been updated. A relational scope has no `.tasks.json` left to stat at all,
    so the branch is chosen by belonging rather than by reachability -- falling
    through to the file for one would read an mtime that no longer moves.
    """
    if is_relational_scope(root):
        rows = _from_relational(root) or []
        stamps = [r.get("updated") for r in rows if r.get("updated")]
        if not stamps:
            return None
        try:
            newest = max(stamps)
            dt = datetime.fromisoformat(str(newest).replace("Z", "+00:00"))
        except ValueError:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    store = _store_path(root)
    if not store.exists():
        return None
    return datetime.fromtimestamp(store.stat().st_mtime, timezone.utc)


class RelationalStoreUnavailable(RuntimeError):
    """A relational scope was read while its database could not answer.

    Raised instead of serving the retired `.tasks.json`, because the alternative
    is not "degraded": it is a different, older set of tickets presented as the
    current one. That is what put a cancelled ticket in the operator's daily
    digest for three days.
    """


def _from_relational(root: Path) -> list[dict] | None:
    """This scope's tickets out of Postgres, in the shape `_normalize` accepts.

    Returns None ONLY for the two cases where Postgres is not the right place to
    look: this installation has no database at all (a plain checkout, and the
    test suite), or this working tree is not a relational scope (every project
    tree, which is still file-backed and legitimately so).

    IT NO LONGER SWALLOWS FAILURES. A read that could not reach a database that
    exists used to log a warning and fall back to the file, and `log.warning`
    goes to a log nobody tails: `scripts/root_task_counts.sh` had been calling
    this without a pinned owner, taking the fallback on `OwnerUnset` every time,
    and feeding the operator's 07:00 and 14:00 cron digests a ticket count out
    of a store the app had stopped writing. Raising is what makes that visible
    on the first run instead of the ninetieth.
    """
    from services import task_scope, tenancy
    # WHICH ROOT FIRST, always. A project tree is file-backed by design, so an
    # unreachable database is not a failure for it and must not raise: asking
    # reachability before belonging would take every project scope down with the
    # database.
    scope = task_scope.relational_scope_for_root(root)
    if scope is None:
        return None
    if not tenancy.available():
        if tenancy.configured():
            raise RelationalStoreUnavailable(
                f"{root}: a database is configured but did not answer")
        # No database on this installation at all, or a test that has redirected
        # the file store. Nothing failed; `_load` decides what to do about it.
        return None
    from db import repo
    org_id, _user = tenancy.org_and_user()
    rows = repo.list_tickets(org_id, scope=scope)
    for row in rows:
        row["priority"] = _BAND_TO_INT.get(row.get("priority"), 0)
    return [_normalize(r) for r in rows]


def _load(root: Path) -> list[dict]:
    """Load the authoritative task list, migrating from TODO.md on first access.
    Does NOT reconcile (callers that want the healed view call reconcile first).

    A RELATIONAL SCOPE HAS EXACTLY ONE STORE and never reaches the file branch
    below, not even as a fallback. The file branch does not just read: with no
    `.tasks.json` present it re-parses `TODO.md` and mints a fresh id for every
    line (`_migrate_from_todo`). Pointed at a relational root that is one
    database outage away from empty, that is a duplicate of the entire backlog.
    """
    relational = _from_relational(root)
    if relational is not None:
        return relational
    if is_relational_scope(root):
        # `_from_relational` already raised if a database exists. Getting here
        # means this checkout has none, so the scope has no tickets to show --
        # which is not the same as "regenerate them from the export".
        log.warning("%s keeps its tickets in Postgres and this installation has "
                    "no database configured; reporting no tickets rather than "
                    "reading the retired file store", root)
        return []
    raw = _read_store(root)
    if raw is None:
        # Protected root with no store yet: return empty WITHOUT migrating (which
        # would _persist and wipe its prose TODO.md). Inert until O7.
        if _is_protected(root):
            return []
        tasks = _migrate_from_todo(root)
        _persist(root, tasks)
        return tasks
    return [_normalize(t) for t in raw if isinstance(t, dict)]


# Keys `_normalize` PROJECTS rather than stores. Written to disk they would be a
# second copy of a canonical field that a hand edit could put out of step with it,
# which is exactly the two-sources-of-truth failure ADR 0001 was right to fear.
# Stripped on the way out; re-derived on the way in. `owner` is deliberately NOT
# stripped: the shell CLI still reads it.
#
# `text` was exempted from this list while title/body were landing (010a9a4), so
# that a reader running the PREVIOUS version of this module could still see a
# ticket's words. That exemption is now removed, and the condition it named is
# the reason: it said to remove it "once no old reader can still be running".
# Checked 2026-08-15 before cutting it — one copy of this module exists on the
# box, one process imports it, and it had restarted onto the current code. What
# the exemption cost meanwhile was measured: `text` was stored on 100% of the
# records in all 27 stores, a second copy of the same words that a hand edit
# could put out of step with the pair it is derived from.
_PROJECTED_KEYS = ("exec", "blocked_request", "text")


def _for_storage(task: dict) -> dict:
    """Strip the projections. Total by construction: a raw record that carries
    only `text` (an older writer, a hand edit, a caller that built a dict itself)
    has its canonical pair derived here FIRST, so dropping the projection can
    never drop the only copy of the ticket's words."""
    out = {k: v for k, v in task.items() if k not in _PROJECTED_KEYS}
    if not out.get("title"):
        out["title"], out["body"] = split_text(task.get("text"))
    return out


def _persist(root: Path, tasks: list[dict]) -> None:
    """Write the store, then regenerate the TODO.md export.

    WHICH STORE depends on the root (ADR 0019 stage 2). A relational scope
    writes Postgres and does NOT write `.tasks.json`; every other root writes
    the file exactly as before. `TODO.md` is rendered either way, as the one-way
    export it is now the only remaining job of.

    Reads moved to Postgres before writes did, which left the authoritative
    write on a file that no reader consulted: a mirror that skipped produced a
    ticket that was filed and did not exist, and a writer that could not reach
    the database (the CLI under the wrong interpreter, e7b758a) wrote a file
    nothing read. Both are the same defect, and this is where it ends.

    A relational write RAISES on failure (`ticket_sync.WriteFailed`) rather than
    falling back to the file. Falling back is what produced the divergence: the
    write would land on disk, the database would come back, and reads would
    resume from rows that never saw it.
    """
    if _is_protected(root):
        # Never regenerate a protected root's TODO.md (would wipe its prose
        # sections). Inert until O7 migrates it properly.
        return
    rendered = render_markdown(tasks)
    relational = is_relational_scope(root)

    if relational:
        from db import ticket_sync
        stats = ticket_sync.mirror(root, tasks, required=True)
        # What left the store, reported by the writer, for the backlink cleanup
        # below. The file diff that used to answer this is gone with the file.
        old_ids = set(stats.get("deleted_ids") or [])
    else:
        # Captured BEFORE the write below overwrites it: the set of ids this
        # store held last time, so a ticket dropped from `tasks` (deleted, or
        # merged into another) can have its backlink row cleaned up further down
        # instead of being left as a source that no longer exists.
        try:
            old_ids = {t.get("id") for t in json.loads(_store_path(root).read_text())
                       .get("tasks", []) if t.get("id")}
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            old_ids = set()
        payload = {"version": STORE_VERSION,
                   "tasks": [_for_storage(t) for t in tasks],
                   "export_sha": _sha(rendered)}
        _atomic_write(_store_path(root), json.dumps(payload, indent=2) + "\n")
    _atomic_write(_export_path(root), rendered)
    # Announce the change to the scope, here rather than at the nine call sites
    # that reach this function: one write path, one bump, no way to add a tenth
    # caller that silently skips it. A chat in this scope compares one integer
    # to learn it is behind, instead of searching the store every turn to learn
    # it is not. See services/scope_version.
    try:
        from services import scope_version, task_scope
        scope = task_scope.key_for_root(root)
        if scope:
            scope_version.bump(scope, "ticket", f"{len(tasks)} tickets")
    except Exception:
        # Never let bookkeeping fail a write that already landed on disk.
        pass
    # The semantic index has just gone out of date (ticket is in DEFAULT_KINDS).
    # `_persist` is the one funnel every ticket mutation already goes through
    # for the scope_version bump above and the backlinks re-extraction below,
    # so it is the one place a mark covers add/update/remove/reconcile/import
    # without a call site at each of them. Same best-effort, never-raise
    # posture as knowledge.put_note/delete_note; `mark_stale` itself debounces
    # (a no-op `exists()` check once the marker is already down), so a lease
    # heartbeat that reaches this same funnel costs nothing extra once the
    # index is already known stale.
    try:
        from services import semantic_index
        semantic_index.mark_stale(f"tickets {root}")
    except Exception:
        log.debug("semantic index stale-mark skipped for %s", root, exc_info=True)
    # The relational write happened at the top of this function, before the
    # export was rendered, because for those roots it IS the write. There is no
    # mirror step here any more and no second store to keep in step.
    #
    # Re-extract every ticket's outgoing refs into the backlink index (design
    # doc sec 10/7.2). `_persist` always receives the FULL current list, so
    # replacing each ticket's row here is what makes an edited-out ref
    # disappear from `referenced_by` on the very next write -- there is no
    # separate remove path to forget to call. A ticket present in `old_ids`
    # but missing from `tasks` was deleted; its row is dropped too.
    try:
        from services import backlinks, refs
        new_ids: set[str] = set()
        for t in tasks:
            tid = t.get("id")
            if not tid:
                continue
            new_ids.add(tid)
            body = f"{t.get('title') or ''}\n{t.get('body') or ''}"
            backlinks.set_source(refs.ticket_ref(tid), refs.extract_refs(body))
        for gone in old_ids - new_ids:
            backlinks.remove_source(refs.ticket_ref(gone))
    except Exception:
        pass


# ---- Reconcile (heal out-of-band TODO.md edits) ----------------------------

_BOX_TO_STATUS = {" ": "todo", "x": "done", "X": "done", "~": "doing",
                  "!": "blocked", "?": "in_review", "p": "parked", "-": "cancelled",
                  "d": "draft"}
_LINE_RE = re.compile(r"^(\s*)[-*]\s*\[([ xX~!?pd\-])\]\s*(.*?)\s*$")


# `_norm_text` lived here: it matched a rendered export line back to a stored
# ticket by stripping the trailing status tag ("... (cancelled)") and lowercasing.
# Nothing matches an export line to a ticket any more, and the tag it stripped is
# exactly what got written into 24 stored titles when the matcher missed.


def _parse_export_blocks(text: str) -> list[tuple[str, str]]:
    """Return [(status, full_text)] for every checklist item in a TODO.md body.
    A task's full_text is its checklist line PLUS any following continuation
    lines indented deeper than the marker (the detail block), dedented and joined
    with newlines, so multi-line task bodies round-trip losslessly. Matches all
    six status boxes plus a bare `[ ]`. Nested checklist items are their own
    tasks (handled by the store's parent field, not folded in here)."""
    lines = text.splitlines()
    out: list[tuple[str, str]] = []
    i = 0
    while i < len(lines):
        m = _LINE_RE.match(lines[i])
        if not m:
            i += 1
            continue
        marker_indent = len(m.group(1))
        box, body = m.group(2), m.group(3)
        i += 1
        cont: list[str] = []
        while i < len(lines):
            nxt = lines[i]
            if not nxt.strip():
                # blank line: include only if a deeper continuation follows
                if (i + 1 < len(lines) and lines[i + 1].strip()
                        and not _LINE_RE.match(lines[i + 1])
                        and len(lines[i + 1]) - len(lines[i + 1].lstrip()) > marker_indent):
                    cont.append("")
                    i += 1
                    continue
                break
            if _LINE_RE.match(nxt):
                break  # next checklist item / nested task
            indent = len(nxt) - len(nxt.lstrip())
            if indent <= marker_indent:
                break  # dedented back out: not part of this item
            cont.append(nxt.strip())
            i += 1
        full = body.strip()
        if cont:
            full = full + "\n" + "\n".join(cont)
        if full.strip():
            out.append((_BOX_TO_STATUS.get(box, "todo"), full))
    return out


def reconcile(root: Path) -> bool:
    """Restore a drifted TODO.md from the store. ONE WAY: store -> file, never
    file -> store. Returns True if the export was rewritten.

    THE EXPORT IS NOT AN INBOX. This function used to run the other direction
    too: checkbox flips healed onto the matching store task by normalized-text
    match, hand-edited detail blocks were absorbed, and any checklist line that
    matched nothing was minted as a NEW ticket with a fresh id. That read-back is
    what made `TODO.md` a second store rather than a rendering, and it is
    deleted. It is not deleted on principle. It ran against the repository root
    on 2026-08-17 and produced four duplicate tickets stamped
    `source={"kind":"operator","ref":"todo-reconcile"}`, one status flipped from
    `todo` to `cancelled`, and 24 stored TITLES with the export's rendered status
    marker baked into them ("... (cancelled)  (cancelled)"). The data damage was
    repaired in ba1a71c by narrowing the guard from `is_relational` (which asks
    whether the database answered) to `is_relational_scope` (which asks where a
    root's tickets belong). A guard is only ever as good as the next caller who
    reaches around it, so the direction itself is now gone.

    What remains is the export half, which has to stay somewhere: `_persist`
    regenerates `TODO.md` on every mutation, so the only way the file can
    disagree with the store is an edit made behind the store's back. That edit is
    now reverted on the next read instead of being swallowed into the store. The
    file is a rendering; a rendering that disagrees with what it renders is
    simply stale.

    Inert for a relational scope (`_persist` there writes Postgres and renders
    the export from it) and for a protected root, whose TODO.md carries prose
    sections a re-render would wipe.
    """
    if is_relational_scope(root):
        return False
    if _is_protected(root):
        return False  # never regenerate a protected root's mixed TODO.md
    export = _export_path(root)
    if not export.exists():
        return False
    with _locked(root):
        tasks_raw, export_sha = _read_payload(root)
        if tasks_raw is None:
            return False
        if _sha(export.read_text()) == export_sha:
            return False  # the file is exactly what we last rendered
        _persist(root, [_normalize(t) for t in tasks_raw])
        return True


# ---- Markdown export -------------------------------------------------------

_STATUS_BOX = {"draft": "d", "todo": " ", "doing": "~", "in_review": "?",
               "blocked": "!", "parked": "p", "done": "x", "cancelled": "-"}
_STATUS_LABEL = {"draft": "draft", "doing": "doing", "blocked": "blocked",
                 "in_review": "in_review", "parked": "parked",
                 "cancelled": "cancelled"}


def render_markdown(tasks: list[dict], *, header: str = "# TODO\n\n") -> str:
    """Render the tasks as a GitHub-style checklist grouped by assignee (operator
    first). .tasks.json is the source of truth; this is the export."""
    groups = group_by_owner(tasks)
    owners = sorted(groups.keys(), key=lambda o: (o != DEFAULT_OWNER, o))
    lines: list[str] = []
    for owner in owners:
        if not groups[owner]:
            continue
        roots, children = _tree_for(groups[owner])
        lines.append(f"## {owner}")
        lines.append("")
        for t in roots:
            _emit(t, children, lines, depth=0)
        lines.append("")
    body = "\n".join(lines).rstrip("\n")
    if body:
        body += "\n"
    return f"{header}{body}"


def _emit(task: dict, children: dict[str, list[dict]], out: list[str], *, depth: int) -> None:
    indent = "  " * depth
    box = _STATUS_BOX.get(task["status"], " ")
    tag = _STATUS_LABEL.get(task["status"])
    suffix = f"  ({tag})" if tag else ""
    # Multi-line task bodies (detail lines under the checklist item) round-trip
    # losslessly: first line carries the box + tag, continuation lines align
    # under the text so the parser re-gathers them.
    lines = (task["text"] or "").split("\n")
    first = lines[0] if lines else ""
    out.append(f"{indent}- [{box}] {first}{suffix}")
    cont_indent = indent + "      "  # under "- [x] "
    for extra in lines[1:]:
        out.append(f"{cont_indent}{extra}" if extra.strip() else "")
    for child in children.get(task["id"], []):
        _emit(child, children, out, depth=depth + 1)


# ---- Tree / grouping helpers ----------------------------------------------

def group_by_owner(tasks: list[dict]) -> dict[str, list[dict]]:
    """Group tasks by assignee (the `owner` mirror). Name kept for compat."""
    groups: dict[str, list[dict]] = {}
    for t in tasks:
        groups.setdefault(t.get("owner") or t.get("assignee") or DEFAULT_OWNER, []).append(t)
    return groups


def _tree_for(tasks: list[dict]) -> tuple[list[dict], dict[str, list[dict]]]:
    ids = {t["id"] for t in tasks}
    children: dict[str, list[dict]] = {}
    roots: list[dict] = []
    for t in tasks:
        parent = t.get("parent")
        if parent and parent in ids:
            children.setdefault(parent, []).append(t)
        else:
            roots.append(t)
    return roots, children


def _match_assignee(task: dict, owner: str | None) -> bool:
    """Assignee-scoped match, tolerant of the legacy owner form (bare slug). A task
    with no assignee (assignee is None) is claimable by ANY scope, mirroring
    next_ready.in_scope: otherwise an agent sweep loops offering-then-refusing an
    unassigned task (next_ready returns it, claim's _match_assignee refuses),
    opening + cancelling a child Job on every pass (review F19)."""
    if owner is None:
        return True
    if task.get("assignee") is None:
        return True
    want = owner if (owner == DEFAULT_OWNER or owner.startswith("agent:")) else f"agent:{owner}"
    return (task.get("assignee") or DEFAULT_OWNER) == want or task.get("owner") == owner


def tree(root: Path, *, include_done: bool = True, owner: str | None = None) -> dict:
    reconcile(root)
    tasks = _load(root)
    if owner is not None:
        tasks = [t for t in tasks if _match_assignee(t, owner)]
    if not include_done:
        tasks = [t for t in tasks if t["status"] not in TERMINAL]
    groups = group_by_owner(tasks)
    owners = sorted(groups.keys(), key=lambda o: (o != DEFAULT_OWNER, o))

    def nest(task: dict, children: dict[str, list[dict]]) -> dict:
        node = dict(task)
        node["children"] = [nest(c, children) for c in children.get(task["id"], [])]
        return node

    out_owners = []
    for o in owners:
        roots, children = _tree_for(groups[o])
        out_owners.append({"owner": o, "tasks": [nest(r, children) for r in roots]})
    return {"owners": out_owners}


# ---- Queries ---------------------------------------------------------------

def list_tasks(root: Path, *, include_done: bool = True, owner: str | None = None) -> list[dict]:
    reconcile(root)
    tasks = _load(root)
    if owner is not None:
        tasks = [t for t in tasks if _match_assignee(t, owner)]
    if not include_done:
        tasks = [t for t in tasks if t["status"] not in TERMINAL]
    return tasks


def get_task(root: Path, task_id: str, *, owner: str | None = None) -> dict | None:
    for t in _load(root):
        if t["id"] == task_id and _match_assignee(t, owner):
            return t
    return None


def _is_stale_claim(t: dict, now_dt: datetime, ttl: int) -> bool:
    """A `doing` task is a stale claim (crashed/killed/hung sweep) when either:

      (a) dead-pid fast path: the process that recorded the claim is provably gone
          (a pid that _pid_alive reports dead). A crashed sweep is reclaimed at once,
          without waiting for the heartbeat window to lapse.
      (b) missed heartbeat: the worker has not renewed its lease within `ttl`
          (now - last_heartbeat > ttl). A slow-but-alive worker heartbeats from
          inside ralph.sh's loop, so it is never reclaimed; only a hung/dead worker
          lets last_heartbeat go stale (Fix 2).

    A legacy claim written before heartbeats (no last_heartbeat) falls back to the
    original lease/`updated` + ttl window, still guarded by pid liveness, so old
    stores behave exactly as before."""
    if t.get("status") != "doing":
        return False
    pid = t.get("claim_pid")
    # (a) Dead-pid fast path: a recorded-but-dead pid means the sweep crashed; its
    # last heartbeat (however recent) is moot. A None/unparseable pid is NOT an
    # instant reclaim here (a claim with no recorded pid must not be yanked from a
    # live worker); it is handled by the heartbeat / legacy-lease windows below.
    if pid is not None and not _pid_alive(pid):
        return True
    # (b) Missed-heartbeat window (new claims). Fresh heartbeat -> alive -> keep.
    hb = _parse_iso(t.get("last_heartbeat"))
    if hb is not None:
        return (now_dt - hb) > timedelta(seconds=ttl)
    # Legacy claim (pre-heartbeat store): original lease/`updated` + ttl behavior.
    eff = _parse_iso(t.get("lease_until"))
    if eff is None:
        u = _parse_iso(t.get("updated"))
        eff = (u + timedelta(seconds=ttl)) if u else None
    if eff is None or now_dt < eff:
        return False
    if pid is not None and _pid_alive(pid):
        return False  # sweep is legitimately still working; lease was conservative
    return True


def reclaim(root: Path, *, now: datetime | None = None, ttl: int = LEASE_TTL_SEC,
            attempt_cap: int = RECLAIM_ATTEMPT_CAP) -> list[dict]:
    """Release or park stale `doing` tasks so a crashed/killed sweep never strands
    a task forever (self-healing 2d). A stale claim under its attempt cap returns
    to `todo` (re-runnable next sweep); a stale claim AT the cap lands in `parked`
    (dead-letter, visible to the operator, never re-picked). The monotonic
    `claim_token` is left intact so a zombie that later tries to finalize is
    fenced. Returns [{id, action}] for logging. Cheap: scans lock-free first and
    only takes the lock when there is actually something to reclaim."""
    raw = _read_store(root)
    if not raw:
        return []
    now_dt = now or datetime.now(timezone.utc)
    normalized = [_normalize(t) for t in raw if isinstance(t, dict)]
    if not any(_is_stale_claim(t, now_dt, ttl) for t in normalized):
        return []
    out: list[dict] = []
    with _locked(root):
        tasks = _load(root)
        changed = False
        for t in tasks:
            if not _is_stale_claim(t, now_dt, ttl):
                continue
            # attempts was already incremented at claim time, so the crashed run
            # is already counted; do NOT bump again here (avoids double-counting).
            attempts = _as_int(t.get("attempts"), 0)
            cap = t.get("max_attempts") or attempt_cap
            t["lease_until"] = None
            t["claim_pid"] = None
            if attempts >= cap:
                t["status"] = "parked"
                t["blocked_reason"] = (
                    f"parked: worker died with lease expired after {attempts} "
                    f"attempt(s) (cap {cap})"
                )
                out.append({"id": t["id"], "action": "parked"})
            else:
                t["status"] = "todo"
                t["blocked_reason"] = None
                out.append({"id": t["id"], "action": "released"})
            t["updated"] = _now()
            changed = True
        if changed:
            _persist(root, tasks)
    return out


def is_ready(task: dict, by_id: dict[str, dict]) -> bool:
    """Could the scheduler pick this task up as it stands? A PURE predicate over
    stored state: it reads the record and the sibling index it is handed, and
    touches nothing else.

    PURITY IS THE POINT. `next_ready` calls `reconcile()` and `reclaim()`, and
    `reclaim` rewrites `doing` -> `todo` or -> `parked` at the attempt cap and
    then persists. That is correct for the scheduler and the liveness watchdog,
    and catastrophic on an HTTP GET: the ticket list polls every few seconds
    while anything is `doing`, so wiring reclaim into a read would dead-letter
    live work simply because a tab was left open. This function is therefore the
    only readiness answer a read path may use. The honest consequence: `ready`
    lags a crashed claim by one watchdog interval.

    `by_id` is the index of ONE store, exactly as `next_ready` builds it, and the
    `d is not None` test below deliberately treats a dependency id that is not in
    it as satisfied. Cross-scope dependencies are therefore invisible here — not
    because that is right, but because it is what the scheduler does, and a
    stricter predicate would disagree with which tickets the sweep actually
    picks up. `blocked_by` resolves across scopes for DISPLAY, so a row can
    legitimately read `ready: true` next to a non-empty `blocked_by`.
    """
    if task.get("status") != "todo":
        return False
    for dep in task.get("depends_on") or []:
        d = by_id.get(dep)
        if d is not None and d.get("status") not in TERMINAL:
            return False
    return True


def next_ready(root: Path, *, assignee: str | None = None,
               unattended: bool = False) -> dict | None:
    """The next task ready to run: status==todo AND every depends_on is
    done|cancelled, ordered by priority desc then created asc. Optionally scoped
    to an assignee (an agent claims only its own or unassigned tasks). Replaces
    'first todo'. Reconciles first so a task added by any route is seen, and
    reclaims stale claims so a crashed sweep's task becomes ready again.

    ``unattended`` adds the two phase-4 selection rules, and is passed by the
    SWEEP and by nothing else:

        execution == "code"                  (a machine can do it at all)
        ticket_readiness.check(t)["ok"]       (the spec is adequate)

    It is an extra predicate rather than a change of meaning because this same
    function is what a MANUAL run path consults, and readiness must never refuse
    a human. That keeps two ratified statements true at once: readiness never
    vetoes a person, and only tickets marked ready may run unattended.

    Ordering is deliberately unchanged. `priority DESC` is why the head of this
    queue was a root-requiring security ticket; the fix is `execution`, not a
    re-sort, because a human running the queue by hand still wants the most
    important thing first.
    """
    # The per-scope execution setting gates the UNATTENDED path only. A scope
    # left at its reactive default (fail-closed) yields no unattended work, so
    # nothing auto-runs until the operator arms it (services/scope_execution).
    # The attended path is untouched: readiness and arming must never refuse a
    # human, only the background sweep. Composes under the global override.
    if unattended:
        from services import scope_execution
        if not scope_execution.is_armed_root(root):
            return None
    reconcile(root)
    reclaim(root)
    tasks = _load(root)
    by_id = {t["id"]: t for t in tasks}

    def ready(t: dict) -> bool:
        if not is_ready(t, by_id):
            return False
        if not unattended:
            return True
        if t.get("execution") != "code":
            return False
        # Imported here, not at module scope: ticket_readiness reads this module
        # for its status vocabulary, so a top-level import is a cycle.
        from services import ticket_readiness
        return bool(ticket_readiness.check(t).get("ok"))

    def in_scope(t: dict) -> bool:
        if assignee is None:
            return True
        a = t.get("assignee") or DEFAULT_OWNER
        return a == assignee or a is None or t.get("assignee") is None

    cands = [t for t in tasks if ready(t) and in_scope(t)]
    cands.sort(key=lambda t: (-_as_int(t.get("priority"), 0), t.get("created") or ""))
    return cands[0] if cands else None


# ---- Mutations -------------------------------------------------------------

def add(
    root: Path,
    *,
    text: str | None = None,
    title: str | None = None,
    body: str | None = None,
    owner: str = DEFAULT_OWNER,
    assignee: str | None = None,
    parent: str | None = None,
    status: str = "todo",
    priority: int = 0,
    depends_on: list[str] | None = None,
    labels: list[str] | None = None,
    acceptance: dict | None = None,
    max_attempts: int | None = None,
    touches: list[str] | None = None,
    discovered_from: str | None = None,
    effort: str | None = None,
    execution: str | None = None,
    latitude: str | None = None,
    source: dict | None = None,
    provenance: str | None = None,
    scoped_by: str | None = None,
    sync: dict | None = None,
) -> dict:
    # Two ways in, one shape out. A caller with a real title/body pair (the API,
    # the ticket-maker, an import) passes them; a caller holding one blob (the
    # CLI, TODO.md reconcile, dedup) passes `text` and the store splits it.
    if title is not None:
        title, body = str(title).strip(), str(body or "").strip("\n")
    else:
        title, body = split_text(text)
    text = join_text(title, body)
    if not title:
        raise ValueError("text required")
    # Validated (not coerced) on the write path: an unknown value raises so the
    # caller sees a 400 instead of persisting a field the cascade will ignore.
    latitude = _latitude.validate(latitude)
    if status not in STATUSES:
        status = "todo"
    with _locked(root):
        tasks = _load(root)
        if parent is not None and not any(t["id"] == parent for t in tasks):
            raise ValueError(f"parent task not found: {parent}")
        if provenance:  # idempotent re-add (work_generator / dedup key)
            for t in tasks:
                # Skip parked (dead-letter): a fresh re-request with the same key
                # must become live work, not collapse back into a poison-pill task.
                if t.get("provenance") == provenance and t.get("status") != "parked":
                    return t
        now = _now()
        # A lane that excludes files the body names as the work makes the ticket
        # unwinnable: the worker either writes them and trips the touches gate, or
        # does not and the done-gate reports "nothing was committed". Either way
        # it burns every attempt and blocks, with the worker never at fault. The
        # ticket's own prose is the better evidence of intent than a hand-guessed
        # glob list, so widen the lane to match what the ticket says it will
        # touch, rather than filing a self-contradicting ticket.
        if touches:
            gaps = lane_gaps(text, touches)
            if gaps:
                touches = list(touches) + gaps
        task = _normalize({
            "id": _new_id(), "title": title, "body": body, "status": status,
            "assignee": assignee if assignee is not None else owner,
            "parent": parent or None, "priority": priority,
            "depends_on": depends_on or [], "labels": labels or [],
            "acceptance": acceptance,
            "max_attempts": max_attempts, "touches": touches or [],
            "discovered_from": discovered_from, "effort": effort,
            "execution": execution,
            "latitude": latitude,
            "source": source or {"kind": "operator", "ref": None},
            "scoped_by": scoped_by, "sync": sync,
            "provenance": provenance, "created": now, "updated": now,
        })
        tasks.append(task)
        _persist(root, tasks)
        return task


def update(
    root: Path,
    task_id: str,
    *,
    status: str | None = None,
    text: str | None = None,
    title: str | None = None,
    body: str | None = None,
    owner: str | None = None,
    assignee: str | None = None,
    set_assignee: str | None = None,
    priority: int | None = None,
    depends_on: list[str] | None = None,
    relates_to: list[str] | None = None,
    labels: list[str] | None = None,
    sync: dict | None = None,
    unlink: bool = False,
    touches: list[str] | None = None,
    blocked_reason: str | None = None,
    acceptance: dict | None = None,
    max_attempts: int | None = None,
    effort: str | None = None,
    execution: str | None = None,
    triage: dict | None = None,
    latitude: str | None = None,
    job_id: str | None = None,
    evidence: dict | None = None,
    awaiting: dict | None = None,
    blocked_request: dict | None = None,
    bump_attempt: bool = False,
    restore_attempt: bool = False,
    expect_claim_token: int | None = None,
) -> dict | None:
    """Update a task. Additive fields are patched when provided. Returns the
    updated task or None.

    THREE ownership parameters, because two different questions were being asked
    through one word and the answers contradicted each other:

      ``owner``        MATCH only. Scopes the lookup so an agent cannot mutate
                       another delegate's task. Never written.
      ``set_assignee`` WRITE only. Reassigns the ticket. Never filters, so a
                       patch may name an assignee the ticket does not have yet.
                       "" (or whitespace) hands it back to DEFAULT_OWNER, which
                       is what "nobody is delegated this" means on a record.
      ``assignee``     LEGACY: match AND write, the original single parameter.
                       Kept because callers depend on the filter half of it.

    ``assignee`` doing both is exactly why an assignee could not be changed at
    all: the value being written was also the filter used to find the record, so
    naming a NEW assignee matched nothing, `update` returned None, and the HTTP
    layer reported "task not found" while dropping the entire save. Anything that
    means "reassign this ticket" must use ``set_assignee``.

    ``expect_claim_token`` fences a zombie: if provided and it does not match the
    task's current ``claim_token``, the task was reclaimed and re-claimed by a
    newer sweep, so this stale caller's finalize is rejected (returns None) rather
    than clobbering live work (2d). Any transition OUT of ``doing`` releases the
    claim (clears lease + pid) so a waiting task never freezes a dispatch slot and
    is never mistaken for a crash.

    Fix 1 (fencing generation): a token-bearing (worker) write is fence-checked as
    above and never bumps the generation. A NO-token write (operator / panel / chat)
    is never blocked, but when it MATERIALLY edits a task a worker currently holds
    (status was ``doing`` with a live claim), it INVALIDATES that generation by
    bumping ``claim_token``, so the displaced worker's next token-bearing write is
    fenced out instead of the operator being blocked. A no-token evidence-only
    append (a `note`) or a job-id link is not a material edit and does not bump, so
    a worker's own untokened notes never self-fence its finalize."""
    if status is not None and status not in STATUSES:
        raise ValueError(f"status must be one of {sorted(STATUSES)}")
    # `title`/`body` patch the canonical pair independently — editing a title must
    # not require resending the whole body, which is the entire reason a tracker
    # models them separately. `text` remains accepted and splits, so every caller
    # written against the old signature keeps working.
    if title is not None:
        title = str(title).strip()
        if not title:
            raise ValueError("title required")
    if text is not None:
        if title is not None or body is not None:
            raise ValueError("pass text, or title/body, not both")
        title, body = split_text(text)
        if not title:
            raise ValueError("text required")
    if latitude is not None:
        # "" clears the override back to inherit; an unknown value raises.
        latitude = _latitude.validate(latitude) or ""
    # The MATCH filter. `set_assignee` is deliberately absent from this line:
    # reassigning a ticket must not also narrow the search to tickets that already
    # carry the new assignee, which is the whole bug.
    scope = assignee if assignee is not None else owner
    with _locked(root):
        tasks = _load(root)
        found = None
        for t in tasks:
            if t["id"] == task_id and _match_assignee(t, scope):
                found = t
                break
        if found is None:
            return None
        if expect_claim_token is not None and \
                _as_int(found.get("claim_token"), 0) != int(expect_claim_token):
            return None  # fenced: task was reclaimed + reassigned to a newer sweep
        # Fix 1: does a live worker currently hold this task? Captured BEFORE any
        # field change (a status transition out of `doing` below would otherwise
        # hide it). A no-token material edit to such a task bumps the generation at
        # the end so the worker is fenced out.
        _held_by_worker = (found.get("status") == "doing"
                           and found.get("claim_token") is not None)
        _material_edit = any(v is not None for v in (
            status, title, body, assignee, set_assignee, priority, depends_on,
            blocked_reason, acceptance, max_attempts, effort, latitude, awaiting,
            blocked_request))
        if status is not None:
            found["status"] = status
            # Leaving a waiting state clears the operator-input payload; nothing
            # is waiting on a task that is now todo/doing/done/cancelled.
            if status not in ("blocked", "in_review"):
                found["awaiting"] = None
            # Any transition out of `doing` releases the claim: a task that is
            # waiting (blocked/in_review), released (todo), parked, or finished is
            # NOT being worked, so it must not hold a lease/pid. This is the single
            # most important detail for "the whole system never freezes" (2d).
            if status != "doing":
                found["lease_until"] = None
                found["claim_pid"] = None
        if title is not None:
            found["title"] = title
        if body is not None:
            found["body"] = str(body).strip("\n")
        if title is not None or body is not None:
            # Keep the projection true for THIS call's return value; the next
            # load re-derives it from the pair either way.
            found["text"] = join_text(found["title"], found["body"])
        if labels is not None:
            found["labels"] = _norm_labels(labels)
        if sync is not None:
            # An empty dict clears the link WITHOUT recording an archive entry;
            # `unlink` is the path that keeps the history. Both exist because
            # clearing a half-written link is not the same act as disconnecting.
            found["sync"] = sync or None
        if unlink and found.get("sync"):
            # Collapse the live link into a frozen reference. Never delete it: a
            # ticket has to be able to say what it used to be, permanently.
            old = found["sync"]
            found["external_refs"] = list(found.get("external_refs") or []) + [{
                "provider": old.get("provider"), "key": old.get("remote_key"),
                "url": old.get("url"), "remote_id": old.get("remote_id"),
                "unlinked_at": _now(),
            }]
            found["sync"] = None
        if assignee is not None:
            found["assignee"] = assignee
            found["owner"] = assignee
        if set_assignee is not None:
            # Written last of the two so an explicit reassign wins over the legacy
            # parameter if a caller ever passes both.
            #
            # "" clears back to DEFAULT_OWNER rather than to None, because that is
            # what the record means when nothing is delegated: `_assignee_from_legacy`
            # reads a blank assignee as the operator, so storing None here would
            # return None from this call and then read back as "operator" on the
            # very next load. Same value in the return and in the store.
            who = str(set_assignee).strip() or DEFAULT_OWNER
            found["assignee"] = who
            found["owner"] = who
        if priority is not None:
            found["priority"] = int(priority)
        if depends_on is not None:
            new_deps = [str(d) for d in depends_on if d]
            # Cycle guard (entity-model 2026-07-18 §4.4): the depends_on graph is
            # a DAG. Any mutation that would create a cycle is rejected here
            # BEFORE the write lands, so a bad patch never wedges the queue. The
            # check walks forward from each new dependency: if we can reach this
            # task, the edge is a cycle.
            if _would_cycle(tasks, task_id, new_deps):
                raise ValueError(
                    f"depends_on would form a cycle back to {task_id}")
            found["depends_on"] = new_deps
        if relates_to is not None:
            # Soft link. No cycle check (relates_to has no scheduling effect;
            # entity-model §4.2). Duplicates and self-links are stripped so the
            # UI never has to render them.
            found["relates_to"] = [
                str(r) for r in relates_to if r and str(r) != task_id]
        if touches is not None:
            # `touches` was settable at add() but not here, which made the single
            # most common cause of a blocked ticket unfixable through the supported
            # path: a lane that is too narrow (or names the wrong files) fails the
            # run, and correcting it meant hand-editing .tasks.json. Four of the
            # five tickets blocked on 2026-07-18 were blocked by their own lane,
            # not by the worker. An empty list clears back to UNRESTRICTED, which
            # is what `touches: []` already means everywhere else (_check_touches).
            found["touches"] = [str(t) for t in touches if t]
        if blocked_reason is not None:
            found["blocked_reason"] = blocked_reason or None
        if acceptance is not None:
            found["acceptance"] = acceptance or None
        if max_attempts is not None:
            found["max_attempts"] = _as_int_or_none(max_attempts)
        if effort is not None:
            # Additive: an empty string clears the hint back to None.
            found["effort"] = effort or None
        if execution is not None:
            # Additive: "" clears back to unclassified, which is INELIGIBLE for
            # unattended dispatch, not "code". Deliberately absent from
            # `_material_edit` above: retagging how a ticket is routed does not
            # invalidate the work a holding worker is already doing, so it must
            # not fence that worker out.
            found["execution"] = _normalize_execution(execution)
        if triage is not None:
            # The triager's own mark. Also absent from `_material_edit`: the
            # whole point is that recording a decision is not an edit to the
            # ticket. An empty dict clears the mark, which makes it due again.
            found["triage"] = _normalize_triage(triage)
        if latitude is not None:
            # Additive: an empty string clears the override back to None, which
            # means INHERIT from the scope — not "standard" (latitude.py).
            found["latitude"] = latitude or None
        # `awaiting` and `blocked_request` are two names for ONE record (module
        # docstring). Both write here, through the same normalizer, so a caller
        # that thinks in questions and a caller that thinks in structured requests
        # cannot produce two different shapes. An empty ask clears the record.
        req = blocked_request if blocked_request is not None else awaiting
        _asked_before = bool(found.get("awaiting"))
        if req is not None:
            found["awaiting"] = _normalize_awaiting(req)
        # A question is NEWLY raised when this write puts a request on a task that
        # did not carry one. Captured here and delivered after the persist, so the
        # chat is never told about a question that failed to save.
        _newly_asked = bool(found.get("awaiting")) and not _asked_before
        # Keep the mirror true for THIS call's return value. `found` came from a
        # normalized load, so its `blocked_request` is the pre-update projection
        # until it is re-derived here — exactly the discipline `owner`/`assignee`
        # already follow above. A reader of the returned record must never see a
        # block request the task no longer carries.
        found["blocked_request"] = found.get("awaiting")
        if bump_attempt:
            found["attempts"] = _as_int(found.get("attempts"), 0) + 1
        if restore_attempt:
            # Give back the attempt the claim consumed: the run failed on the
            # environment (API outage / auth), not on the task, so it must not
            # count toward the block/park cap (2d).
            found["attempts"] = max(0, _as_int(found.get("attempts"), 0) - 1)
        if job_id:
            if job_id not in found["job_ids"]:
                found["job_ids"].append(job_id)
        if evidence:
            ev = dict(evidence)
            ev.setdefault("ts", _now())
            found["evidence"].append(ev)
        # Fix 1: an operator/no-token MATERIAL edit to a task a worker holds
        # invalidates that worker's generation. Token-bearing writes never reach
        # here with a bump (they matched the fence and must NOT advance it);
        # note-only / job-id-only no-token writes are not material, so a worker's
        # own untokened notes never fence its subsequent tokened finalize.
        if expect_claim_token is None and _held_by_worker and _material_edit:
            found["claim_token"] = _as_int(found.get("claim_token"), 0) + 1
        found["updated"] = _now()
        # Keep the `exec` projection true for THIS call's return value, the same
        # discipline `owner`, `text` and `blocked_request` already follow above.
        # `found` came from a normalized load, so without this line every EXEC
        # field this call just wrote (touches, awaiting, attempts, triage) reads
        # back at its PRE-update value in the returned record. The STORED record
        # was always right — `exec` is stripped on write and re-derived on read —
        # so this only ever affected the caller holding the return value.
        found["exec"] = {k: found.get(k) for k in EXEC_FIELDS}
        _persist(root, tasks)
    # Outside the lock and after the write: a newly raised question goes to the
    # chat that filed the ticket, not only to the queue. Here rather than at each
    # producer because there are several (the CLI's `set-awaiting` and `block`,
    # the sweep, the reviewer) and a producer that forgets is a question nobody
    # ever sees — which is the state the audit found the system in. Never raises;
    # a ticket that correctly stopped to ask must not fail because telling
    # someone went wrong.
    if _newly_asked and not (found.get("awaiting") or {}).get("dismissed"):
        from services import question_report, task_scope
        question_report.post_for_task(found, found["awaiting"],
                                      scope=task_scope.key_for_root(root))
    return found


def claim(root: Path, task_id: str, job_id: str, *, assignee: str | None = None,
          pid: int | None = None, ttl: int = LEASE_TTL_SEC) -> dict | None:
    """Atomically claim a task: todo -> doing under flock, recording the job id.
    Records a fencing token (`claim_token`, monotonic per task), a lease
    (`lease_until` = now + ttl) and the claiming `claim_pid` so a crashed sweep's
    task can be reclaimed and a zombie's late finalize can be fenced (2d).
    Returns the claimed task, or None if it was already claimed / not todo (a
    concurrent sweep + CLI can race for the same task; the loser gets None)."""
    with _locked(root):
        tasks = _load(root)
        found = None
        for t in tasks:
            if t["id"] == task_id and _match_assignee(t, assignee):
                found = t
                break
        if found is None or found["status"] != "todo":
            return None
        found["status"] = "doing"
        found["attempts"] = _as_int(found.get("attempts"), 0) + 1
        found["claim_token"] = _as_int(found.get("claim_token"), 0) + 1
        found["lease_until"] = (
            datetime.now(timezone.utc) + timedelta(seconds=ttl)
        ).isoformat(timespec="seconds")
        # Seed the heartbeat at claim time so a fresh claim is never mistaken for a
        # missed heartbeat before the worker's first renewal lands (Fix 2).
        found["last_heartbeat"] = _now()
        found["claim_pid"] = _as_int_or_none(pid)
        if job_id and job_id not in found["job_ids"]:
            found["job_ids"].append(job_id)
        found["updated"] = _now()
        # As in `update()`: a claim writes six EXEC fields, so the projection has
        # to be re-derived or the returned record reports the pre-claim lease.
        found["exec"] = {k: found.get(k) for k in EXEC_FIELDS}
        _persist(root, tasks)
        return found


CLARIFICATION_HEADING = "## Clarifications"


def append_clarification(root: Path, task_id: str, *, question: str, answer: str,
                         assignee: str | None = None) -> dict | None:
    """Write an answered question INTO the ticket body, where the next attempt
    will read it.

    This closes the gap that made the whole question path pointless. An answer
    used to land only in `evidence`, but a worker is handed the ticket's TEXT and
    nothing else (scripts/project_ralph.sh hands `$TASK_TEXT` to ralph.sh), so the
    operator could answer a question and the next attempt would ask it again,
    having never seen the reply. The body is the one field the worker is
    guaranteed to read, so that is where an answer belongs.

    Appends under a single ``## Clarifications`` heading, so a ticket answered
    three times has three entries and one heading rather than three headings.
    """
    q = " ".join(str(question or "").split()).strip()
    a = str(answer or "").strip()
    if not a:
        return None
    entry = f"- **Q:** {q}\n  **A:** {a}" if q else f"- {a}"
    task = get_task(root, task_id, owner=assignee)
    if task is None:
        return None
    body = task.get("body") or ""
    if CLARIFICATION_HEADING in body:
        body = f"{body.rstrip()}\n{entry}"
    else:
        body = f"{body.rstrip()}\n\n{CLARIFICATION_HEADING}\n{entry}" if body.strip() \
            else f"{CLARIFICATION_HEADING}\n{entry}"
    # `owner=`, not `assignee=`: this parameter is the same delegate FILTER
    # `get_task` above applies. Passing it as `assignee=` also WROTE it, so
    # answering a question on an unassigned ticket silently assigned the ticket to
    # whoever the caller happened to be scoping by.
    return update(root, task_id, body=body, owner=assignee)


def add_comment(root: Path, task_id: str, *, text: str, author: str = DEFAULT_OWNER,
                assignee: str | None = None) -> dict | None:
    """Append a comment to a ticket's discussion and return the updated task.

    Deliberately NOT the same channel as `append_clarification`. That one writes
    an ANSWER into the body, because a worker is handed the ticket text and
    nothing else, so an answer that lives anywhere but the body is an answer the
    next attempt never reads. A comment is discussion: it is for the operator
    and the reader of the page, and it must not silently become part of the spec
    a worker executes. Moving clarifications in here would re-open the exact gap
    `append_clarification` exists to close.
    """
    body_text = str(text or "").strip()
    if not body_text:
        return None
    with _locked(root):
        items = _load(root)
        found = None
        for t in items:
            if t["id"] == task_id and _match_assignee(t, assignee):
                found = t
                break
        if found is None:
            return None
        entry = {"id": _new_id(), "author": str(author or DEFAULT_OWNER),
                 "text": body_text, "ts": _now()}
        found["comments"] = _norm_comments(found.get("comments")) + [entry]
        # A comment is not a content edit to the spec, so it does not bump
        # `updated` (which drives "last touched" sorting and the sync shadow).
        # It would otherwise make every commented ticket look freshly edited.
        _persist(root, items)
        return _normalize(found)


def delete_comment(root: Path, task_id: str, comment_id: str, *,
                   assignee: str | None = None) -> dict | None:
    """Remove one comment. Returns the updated task, or None if either the task
    or the comment is not there."""
    with _locked(root):
        items = _load(root)
        found = None
        for t in items:
            if t["id"] == task_id and _match_assignee(t, assignee):
                found = t
                break
        if found is None:
            return None
        before = _norm_comments(found.get("comments"))
        after = [c for c in before if c["id"] != comment_id]
        if len(after) == len(before):
            return None
        found["comments"] = after
        _persist(root, items)
        return _normalize(found)


# ---- Ordering --------------------------------------------------------------
# `list_tasks` returns raw file order, which is insertion order. That is correct
# for the store (TODO.md export depends on it) and useless for a list the
# operator reads, so ordering is applied on the way OUT, here, where both the
# cross-scope route and any single-scope caller can share one definition of what
# "by priority" means.

# Rank for status ordering: what is in front of you, then what is waiting, then
# what is finished. Alphabetical would put `blocked` first and `todo` sixth.
_STATUS_RANK = {"blocked": 0, "doing": 1, "in_review": 2, "todo": 3, "draft": 4,
                "parked": 5, "done": 6, "cancelled": 7}

SORT_KEYS = ("updated", "created", "priority", "title", "status", "queue")


def sort_tasks(items: list[dict], key: str = "updated",
               desc: bool | None = None) -> list[dict]:
    """Order tasks by one of SORT_KEYS. Unknown key falls back to `updated`.

    `desc` defaults per key to the direction that is useful rather than the one
    that is alphabetical: newest first for dates, biggest first for priority,
    A-Z for title, most-urgent first for status, and next-to-run first for queue.
    """
    if key not in SORT_KEYS:
        key = "updated"
    # `status` ranks ascending (0 = most urgent) so its useful default is
    # ascending; the date and priority keys want biggest first; title wants A-Z.
    reverse = (key in ("updated", "created", "priority")) if desc is None else bool(desc)
    if key == "priority":
        def rank(t: dict):
            return (_as_int(t.get("priority"), 0), str(t.get("updated") or ""))
    elif key == "title":
        def rank(t: dict):
            return (str(t.get("title") or "").casefold(), str(t.get("created") or ""))
    elif key == "status":
        def rank(t: dict):
            return (_STATUS_RANK.get(str(t.get("status")), 99),
                    str(t.get("updated") or ""))
    elif key == "queue":
        # The scheduler's own order: highest priority first, oldest first within
        # a priority — the same key `next_ready` sorts candidates by, so "what
        # would run next" is the top of this list rather than a guess. Negating
        # priority folds the mixed directions into one ASCENDING key, which is
        # why its useful default is ascending and why `desc=True` gives the exact
        # inverse instead of a half-reversed order.
        def rank(t: dict):
            return (-_as_int(t.get("priority"), 0), str(t.get("created") or ""))
    else:
        def rank(t: dict):
            return (str(t.get(key) or ""), str(t.get("id") or ""))
    return sorted(items, key=rank, reverse=reverse)


def heartbeat(root: Path, task_id: str, claim_token: int | None, *,
              assignee: str | None = None, ttl: int = LEASE_TTL_SEC) -> dict | None:
    """Renew a claimed task's lease from inside a running worker (Fix 2). Presents
    the worker's ``claim_token`` as a fence so it composes with Fix 1: a heartbeat
    whose generation does not match the task's current ``claim_token`` is a no-op
    (the worker was displaced by an operator edit or a liveness reclaim, so it no
    longer owns the task). On a match, refreshes ``last_heartbeat`` and extends
    ``lease_until`` = now + ttl, so a slow-but-alive worker is never reclaimed.
    Returns the task, or None if it is not found / not ``doing`` / fenced. Does not
    bump ``updated`` (a heartbeat is liveness, not a content edit)."""
    with _locked(root):
        tasks = _load(root)
        found = None
        for t in tasks:
            if t["id"] == task_id and _match_assignee(t, assignee):
                found = t
                break
        if found is None or found.get("status") != "doing":
            return None
        if _as_int(found.get("claim_token"), 0) != _as_int(claim_token, 0):
            return None  # fenced: this is no longer this worker's claim generation
        now = _now()
        found["last_heartbeat"] = now
        found["lease_until"] = (
            datetime.now(timezone.utc) + timedelta(seconds=ttl)
        ).isoformat(timespec="seconds")
        _persist(root, tasks)
        return found


def remove(root: Path, task_id: str, *, owner: str | None = None) -> bool:
    with _locked(root):
        tasks = _load(root)
        target = None
        for t in tasks:
            if t["id"] == task_id and _match_assignee(t, owner):
                target = t
                break
        if target is None:
            return False
        new_parent = target.get("parent")
        keep = [t for t in tasks if t["id"] != task_id]
        for t in keep:
            if t.get("parent") == task_id:
                t["parent"] = new_parent
        _persist(root, keep)
        return True


# ---- TODO.md export --------------------------------------------------------
#
# There is no counterpart to this function and there must not be one. The import
# direction (`import_markdown`, reached by `PUT /projects/{slug}/todo`) is
# deleted: see `reconcile` for what it did to the live store on 2026-08-17.

def export_markdown(root: Path) -> str:
    reconcile(root)
    tasks = _load(root)
    _persist(root, tasks)
    return _export_path(root).read_text()
