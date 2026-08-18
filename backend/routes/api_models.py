"""Response models shared by more than one router.

ADR 0023 clause 6 ("`response_model=` on every route") is the reason this file
exists, and ADR 0023's own rationale is the reason it is ONE file rather than a
copy per router: an entity described independently in four places is exactly
the mechanism that produced 52 envelope key names across 39 routers. A Job is
returned by `routes/jobs.py`, `routes/tasks_all.py`, `routes/projects.py` and
`routes/agents.py`; a Task by `routes/tasks_all.py` and `routes/agents.py`.
Each gets one description, here.

WHAT A `response_model` DOES, and why these models are wide. FastAPI FILTERS a
handler's return value through the declared model and silently DROPS any key
the model does not name. So an under-declared model is not a documentation
gap, it is a field that disappears from a live API with a green test suite.
Every field below was read off the emitter -- `services/tasks.py::_normalize`,
`services/jobs.py`'s `start`/`finish` records plus `routes/jobs.py::_enrich`,
`services/agents_registry.py::_record_from_def` -- and not off the frontend's
hand-written types, which are known-incomplete (the TS `Task` declares 36
fields against the 39 `_normalize` emits).

WHERE A FIELD IS TYPED `dict` / `list[dict]`. Deliberate, and only where the
substructure is genuinely open today: `evidence` entries take whatever key the
writer put on them, `acceptance` is preserved verbatim key-for-key by
`_normalize_acceptance`, `limits` and `budget` are free-form bundles, and
`sync` carries a per-tracker shadow. Declaring a narrow model for one of those
would drop the keys it did not guess. Where a substructure IS normalized to a
closed shape (`awaiting`, `source`, a comment, a readiness report) it is typed,
because there it can be typed truthfully.

RFC3339 AND `*_at` (ADR 0023 clause 4) ARE NOT APPLIED HERE. `created`,
`updated` and a comment's `ts` are the names the live records, the live
frontend and 27 on-disk stores use. Renaming them is a wire-shape change to
every ticket surface, and ADR 0023's migration posture makes that change
incomplete until the frontend reads the new name in the same commit -- which
this pass deliberately does not do. The names are recorded as debt here rather
than half-changed.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel


# --------------------------------------------------------------- tasks

class TaskSource(BaseModel):
    """Where the REQUEST came from. Normalized to exactly this pair by
    `tasks._normalize` (`{"kind": ..., "ref": ...}`), so it can be typed."""

    kind: str
    ref: str | None = None


class TaskComment(BaseModel):
    """A comment as `tasks._norm_comments` emits it: a closed four-key record,
    distinct from `evidence` (which the system writes and which carries no
    author). `ts` is ADR 0023 clause 4 debt; see the module docstring."""

    id: str
    author: str
    text: str
    ts: str


class AwaitingProposal(BaseModel):
    uri: str
    verb: str


class Awaiting(BaseModel):
    """THE block record, exactly as `tasks._normalize_awaiting` emits it. Also
    served under the `blocked_request` mirror, which is the same object."""

    question: str
    since: str
    kind: str
    ask: str
    why: str | None = None
    unblocks_if: str | None = None
    options: list[str] | None = None
    default: str | None = None
    proposal: AwaitingProposal | None = None
    raised_by: str | None = None
    dedupe_key: str | None = None
    # Free-form detail for the reviewer's eyes; never normalized, so never typed.
    context: dict[str, Any] = {}
    dismissed: bool = False


class ReadinessItem(BaseModel):
    check: str
    why: str


class Readiness(BaseModel):
    """`services/ticket_readiness.check` -- what a ticket does not yet say.
    Advisory: only `blocking()` refuses a run."""

    ok: bool
    missing: list[ReadinessItem] = []
    warnings: list[str] = []


class EffectiveLatitude(BaseModel):
    """The RESOLVED latitude plus where it came from. The source travels with
    the value because an inherited value rendered as if it were set is how the
    null-means-inherit bug becomes invisible on screen."""

    value: str
    source: str


class TaskLink(BaseModel):
    """`tasks_all._link_stub`: enough to render a row and build a working href.
    `scope` rides along because a ticket is addressed by (scope, id) and a
    cross-scope dependency linked from the current scope 404s."""

    id: str
    scope: str
    title: str
    status: str


class TaskLinks(BaseModel):
    parent: TaskLink | None = None
    children: list[TaskLink] = []
    depends_on: list[TaskLink] = []
    relates_to: list[TaskLink] = []
    # Ids that resolve in no scope. Named so a page can say so rather than
    # linking into space.
    missing: list[str] = []


class TaskOrigin(BaseModel):
    """A ticket's `source` resolved into something a UI can link to. `id` with
    null `title`/`context_key` means the chat was deleted."""

    kind: str
    id: str | None = None
    title: str | None = None
    context_key: str | None = None


class Task(BaseModel):
    """The canonical Task on the wire.

    The first 39 fields are `services/tasks.py::_normalize`'s output verbatim,
    including the four it PROJECTS rather than stores (`text`, `owner`,
    `blocked_request`, `exec`). The trailing block is what the route layer
    stamps on the way out (`tasks_all._with_latitude` / `_with_triage` /
    `_detail`); each defaults to None because not every route computes them --
    `POST /agents/build/{slug}/tasks` returns a store record with none of them.
    """

    id: str
    title: str
    body: str
    # PROJECTION of title + body, re-derived on every read, never stored.
    text: str
    status: str
    assignee: str | None = None
    # Legacy mirror of `assignee`, still read by the shell CLI.
    owner: str
    parent: str | None = None
    priority: int = 0
    depends_on: list[str] = []
    relates_to: list[str] = []
    labels: list[str] = []
    # Preserved verbatim key-for-key by `_normalize_acceptance`; see docstring.
    acceptance: dict[str, Any] | None = None
    max_attempts: int | None = None
    blocked_reason: str | None = None
    awaiting: Awaiting | None = None
    # The same ONE record under the name the API and the Review tab use.
    blocked_request: Awaiting | None = None
    attempts: int = 0
    # The Kleppmann fencing generation and its lease. Emitted by the store and
    # absent from the frontend's hand-written type; kept because dropping a
    # concurrency primitive from the wire is not a change to make silently.
    claim_token: int | None = None
    lease_until: str | None = None
    last_heartbeat: str | None = None
    claim_pid: int | None = None
    job_ids: list[str] = []
    evidence: list[dict[str, Any]] = []
    comments: list[TaskComment] = []
    source: TaskSource
    provenance: str | None = None
    touches: list[str] = []
    discovered_from: str | None = None
    effort: str | None = None
    execution: str | None = None
    triage: dict[str, Any] | None = None
    latitude: str | None = None
    scoped_by: str
    sync: dict[str, Any] | None = None
    external_refs: list[dict[str, Any]] = []
    # The workshop-private execution fields gathered under one key, so a sync
    # adapter can read what it must NOT send.
    exec: dict[str, Any] = {}
    created: str
    updated: str

    # ---- stamped by the route layer, never stored ----
    scope: str | None = None
    effective_latitude: EffectiveLatitude | None = None
    state_type: str | None = None
    ready: bool | None = None
    blocked_by: list[TaskLink] | None = None
    readiness: Readiness | None = None
    links: TaskLinks | None = None
    origin: TaskOrigin | None = None


# ---------------------------------------------------------------- jobs

class Job(BaseModel):
    """One bounded execution: a sweep, a cron tick, an event reaction, a manual
    run. The stored half is `services/jobs.py`'s record (every key optional
    except `id`, because `finish()` appends a PARTIAL line and readers collapse
    by id); the display half is computed at read time by `routes/jobs._enrich`
    so a job reads like a ticket without migrating an append-only log.

    `status` is `str`, not the `jobs.STATUSES` enum: `projects._run_state`
    serves a synthetic `crashed` for a run left `running` past the stale bound,
    and that value is the honest one for a dead sweep.
    """

    id: str
    agent_id: str | None = None
    project_slug: str | None = None
    chat_id: str | None = None
    trigger: str | None = None
    status: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    exit_code: int | None = None
    tasks_done: int | None = None
    tasks_failed: int | None = None
    evidence: Any = None
    budget: dict[str, Any] | None = None
    task_id: str | None = None
    parent_id: str | None = None
    # Recorded so the liveness watchdog can tell a running Job whose process is
    # dead from one genuinely in flight. Emitted since 2026-08-12 and absent
    # from the frontend's hand-written `Job` type.
    pid: int | None = None
    kind: str | None = None
    binding: dict[str, Any] | None = None

    # ---- computed by routes/jobs._enrich ----
    title: str | None = None
    agent_name: str | None = None
    trigger_label: str | None = None
    context_label: str | None = None
    summary: str | None = None
    error: str | None = None
    task_text: str | None = None
    task_status: str | None = None
    chat_title: str | None = None
    chat_context_key: str | None = None

    # ---- detail only (GET /jobs/{id}): the usage ledger joined by job id ----
    tokens: int | None = None
    tokens_own: int | None = None


# ------------------------------------------------- small shared shapes

class ContentOut(BaseModel):
    """A whole text file's contents. Used by every brief / todo /
    interventions read."""

    content: str


class FileOut(BaseModel):
    path: str
    content: str


class OkOut(BaseModel):
    """ADR 0023 clause 7 bans `{"ok": true}` as a mutation body and this model
    is that body. It is declared rather than fixed because replacing it means
    returning the resource in its new state, which is a wire-shape change on
    routes the frontend reads -- and the ADR's one non-optional migration rule
    is that such a change lands with its frontend edit in the same commit.
    Typed here so the debt is countable in the schema instead of invisible."""

    ok: bool


class DeletedOut(BaseModel):
    deleted: bool


class TreeNode(BaseModel):
    """One entry in a working-tree listing. Recursive: `children` is present on
    a directory and absent on a file, `size` the other way round."""

    name: str
    path: str
    kind: str
    size: int | None = None
    children: list["TreeNode"] | None = None


class TreeOut(BaseModel):
    root: str
    tree: list[TreeNode]


class QueueCounts(BaseModel):
    """A scope's task tally. `ready` is the count of `todo` rows whose
    dependencies are all terminal (the predicate `next_ready` uses), not a
    status."""

    ready: int
    doing: int
    in_review: int
    blocked: int
    parked: int
    todo: int


class AwaitingRow(BaseModel):
    """A pending operator prompt, flattened for a queue readout."""

    id: str | None = None
    status: str | None = None
    text: str = ""
    question: str | None = None
    since: str | None = None


class LabelCount(BaseModel):
    """A facet value and how many tickets carry it."""

    label: str
    count: int
