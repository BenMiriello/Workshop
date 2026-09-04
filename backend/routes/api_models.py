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

WHERE A FIELD IS TYPED `dict` / `list[dict]`. This paragraph used to say the
remaining ones were deliberate, because "declaring a narrow model for one of
them would drop the keys it did not guess". That reasoning was right about the
danger and wrong about the options: a model with
`model_config = ConfigDict(extra="allow")` declares a FLOOR and keeps
everything else, so it cannot drop an unguessed key. Verified on the chat
models 2026-08-18 -- pydantic retains extras through both `model_dump()` and
`model_dump(exclude_unset=True)`, which is the mode these routes serialise in.

So `acceptance`, `evidence`, `triage`, `sync`, `external_refs` and `exec` are
now typed. Each was read off its writer, not guessed:
`_normalize_acceptance` (which is why acceptance keeps `extra="allow"` -- its
docstring promises every existing key verbatim), `_normalize_triage` (a closed
four-key mark), `tracker_sync._sync_record` and `_shadow_of`, the
`external_refs` append at `tasks.py:2129`, and `EXEC_FIELDS`, which makes
`exec` a fixed 14-key projection of fields this model already declares.

Fields stay nullable rather than required even where the normalizer looks like
it guarantees them. A `response_model` is applied to HISTORICAL records as well
as new ones, and on 2026-08-18 a field typed required because the write path
always emits it took `/events/recent` to a 500 on the 5 of 20 stored records
that predated it. The value here is the key SET being known; requiredness adds
risk and no client capability.

`limits` and `budget` on `Job` and the agent records remain open: they are
free-form bundles with no normalizer to read a floor off, so there is nothing
truthful to declare yet.

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

from pydantic import BaseModel, ConfigDict


class McpCounts(BaseModel):
    """MCP servers tallied by health. The four buckets are the platform's five
    status kinds minus `running` (a server is not a process we run), so this is
    the same vocabulary the UI already renders, plus the total it would
    otherwise have to sum itself.

    Read off a live `claude mcp list` on 2026-08-18:
    `{"ok": 1, "warn": 0, "danger": 3, "stopped": 0, "total": 4}`."""

    model_config = ConfigDict(extra="allow")

    ok: int | None = None
    warn: int | None = None
    danger: int | None = None
    stopped: int | None = None
    total: int | None = None


# ------------------------------------------------------------------- triggers

class TriggerBudget(BaseModel):
    """What one autonomous loop may spend. `triggers.DEFAULT_LOOP_BUDGET` is
    `{"max_wall_s": 3000}` and is the only key any writer sets today;
    `extra="allow"` so adding a second dimension (tokens, iterations) does not
    need this model changed before the field can reach a client."""

    model_config = ConfigDict(extra="allow")

    max_wall_s: int | None = None


class AgentTrigger(BaseModel):
    """What makes a scope run. `triggers.make_trigger` is the ONE producer, and
    which keys appear depends on `kind` -- it sets only the ones its kind uses,
    so this is a union expressed as one model rather than five.

      manual    kind
      discord   kind, channels, and `disabled_reason` when channels is empty
      cron      kind, cron_expression, and `disabled_reason` when it is blank
      event     kind, event_sources
      loop      kind, max_iterations, budget, completion_gate

    That per-kind sparseness is why the routes serving this must declare
    `response_model_exclude_unset=True`: without it a manual trigger would go
    out carrying `cron_expression: null` and six other keys it has never had.

    `disabled_reason` is the trigger saying out loud that it can never match,
    rather than being wired to nothing in silence.

    Confirmed against the live registry and the templates on 2026-08-18: the
    keys observed are exactly kind, channels, disabled_reason, cron_expression
    and event_sources, with no key `make_trigger` cannot produce.
    """

    model_config = ConfigDict(extra="allow")

    kind: str
    channels: list[str] | None = None
    cron_expression: str | None = None
    event_sources: list[str] | None = None
    max_iterations: int | None = None
    budget: TriggerBudget | None = None
    completion_gate: str | None = None
    disabled_reason: str | None = None


class AgentLimits(BaseModel):
    """The ceiling a scope's autonomous work runs under.
    `agents_registry.DEFAULT_LIMITS` is `{max_iterations, budget}` and a
    definition may override either from its frontmatter."""

    model_config = ConfigDict(extra="allow")

    max_iterations: int | None = None
    budget: TriggerBudget | None = None


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


class TaskAcceptanceCriterion(BaseModel):
    """One machine-checkable assertion, normalized by `_normalize_acceptance`:
    a criterion with no `cmd` is dropped, `desc` defaults to the `cmd`, and
    `expect` defaults to "exit0". All three are therefore present whenever the
    criterion survived normalization."""

    desc: str | None = None
    cmd: str | None = None
    expect: str | None = None


class TaskAcceptance(BaseModel):
    """How a run proves it finished. `extra="allow"` is REQUIRED here, not
    incidental: `_normalize_acceptance`'s docstring promises it "preserves every
    existing key (kind/spec/allow_no_diff/...) verbatim so callers that read them
    are unaffected", and only re-shapes `criteria`. A closed model would break
    that promise. Measured over 28 live stores: the non-null acceptances carry
    `kind` and `spec`."""

    model_config = ConfigDict(extra="allow")

    kind: str | None = None
    spec: str | None = None
    criteria: list[TaskAcceptanceCriterion] | None = None


class TaskEvidenceEntry(BaseModel):
    """What the SYSTEM recorded about a run, as opposed to a comment, which is
    what a person or agent SAID. Measured over 28 live stores: 33 entries, every
    one carrying `kind`, `ref` and `ts`. Open at the edges because the writers
    are many and nothing normalizes an entry on the way in."""

    model_config = ConfigDict(extra="allow")

    kind: str | None = None
    ref: str | None = None
    ts: str | None = None


class TicketTriage(BaseModel):
    """The queue triager's mark: what it decided, and against WHICH version of
    the spec. `_normalize_triage` returns exactly these four or None, and an
    unrecognized decision reads as "none" rather than being dropped -- losing the
    mark would re-select the ticket, which is the failure this field exists to
    stop. `spec_hash` is the triager's own bookkeeping and is deliberately not
    shown in the UI."""

    at: str | None = None
    decision: str | None = None
    spec_hash: str | None = None
    reason: str | None = None


class TaskSyncShadow(BaseModel):
    """What the remote issue looked like at the last sync, so a conflict can be
    detected against what we last saw rather than against what we last sent.
    `tracker_sync._shadow_of`."""

    model_config = ConfigDict(extra="allow")

    title: str | None = None
    body: str | None = None
    labels: list[str] | None = None
    state: str | None = None


class TaskSyncConflict(BaseModel):
    """Both sides changed the same words since the last sync.
    `tracker_sync._merge_one` builds this. The tracker owns the words so it wins
    the write, and the operator's version is kept here rather than thrown away --
    which is the whole reason the record exists.

    `fields` is drawn from `title`, `body`, `labels` (the only three compared),
    so `mine` and `theirs` are partial shadows and reuse that model."""

    model_config = ConfigDict(extra="allow")

    fields: list[str] | None = None
    since: str | None = None
    mine: TaskSyncShadow | None = None
    theirs: TaskSyncShadow | None = None


class TaskSyncLink(BaseModel):
    """The LIVE link to a remote tracker issue, `tracker_sync._sync_record`.
    None means unlinked; a link that was disconnected is collapsed into
    `external_refs` and never deleted, because a ticket has to be able to say
    what it used to be."""

    model_config = ConfigDict(extra="allow")

    provider: str | None = None
    remote_id: str | None = None
    remote_key: str | None = None
    url: str | None = None
    remote_updated: str | None = None
    last_synced: str | None = None
    state: str | None = None
    shadow: TaskSyncShadow | None = None
    conflict: TaskSyncConflict | None = None


class TaskExternalRef(BaseModel):
    """A FROZEN reference to a tracker issue this ticket used to be linked to.
    Written once, at unlink, by `tasks.py`'s disconnect path. Note `key`, not
    `remote_key`: the archive entry renames the field, and describing it as it
    is beats quietly correcting it."""

    model_config = ConfigDict(extra="allow")

    provider: str | None = None
    key: str | None = None
    url: str | None = None
    remote_id: str | None = None
    unlinked_at: str | None = None


class TaskExecFields(BaseModel):
    """The workshop-private execution fields gathered under one key, so a sync
    adapter can read what it must NOT send to a remote tracker.

    A PROJECTION, never stored: `tasks.py` builds it as
    `{k: rec.get(k) for k in EXEC_FIELDS}`, so all fourteen keys are always
    present and every one of them is also declared flat on `Task` below. Read
    them flat; this exists for the sync adapters. `extra="allow"` so widening
    `EXEC_FIELDS` cannot silently drop the new one from the wire."""

    model_config = ConfigDict(extra="allow")

    touches: list[str] | None = None
    latitude: str | None = None
    acceptance: TaskAcceptance | None = None
    max_attempts: int | None = None
    attempts: int | None = None
    claim_token: int | None = None
    lease_until: str | None = None
    last_heartbeat: str | None = None
    claim_pid: int | None = None
    job_ids: list[str] | None = None
    evidence: list[TaskEvidenceEntry] | None = None
    awaiting: Awaiting | None = None
    blocked_reason: str | None = None
    triage: TicketTriage | None = None


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
    acceptance: TaskAcceptance | None = None
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
    evidence: list[TaskEvidenceEntry] = []
    comments: list[TaskComment] = []
    source: TaskSource
    provenance: str | None = None
    touches: list[str] = []
    discovered_from: str | None = None
    effort: str | None = None
    execution: str | None = None
    # The work CATEGORY (bug|feature|task|chore), orthogonal to `execution`.
    # None when unclassified; the store never invents a type.
    type: str | None = None
    triage: TicketTriage | None = None
    latitude: str | None = None
    scoped_by: str
    sync: TaskSyncLink | None = None
    external_refs: list[TaskExternalRef] = []
    # The workshop-private execution fields gathered under one key, so a sync
    # adapter can read what it must NOT send.
    exec: TaskExecFields | None = None
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

class TypecheckVerdict(BaseModel):
    """The two typecheck gates a reviewed run reports. Values are the gate's
    own words ("skipped", "pass", a failure line), not a bool: "skipped" and
    "passed" are different facts about a run and a bool loses that."""

    model_config = ConfigDict(extra="allow")

    py: str | None = None
    ts: str | None = None


class JobAwaitingQuestion(BaseModel):
    """One ticket a job stopped to ask about. `evidence` is a LIST of these
    when `status == "awaiting_input"`; `routes/jobs.answer` reads `id` off each
    to decide whether every question has now been answered."""

    model_config = ConfigDict(extra="allow")

    id: str | None = None
    status: str | None = None
    text: str | None = None
    question: str | None = None
    blocked_reason: str | None = None


class JobRefusal(BaseModel):
    """A GUARD said no, and this is what it said.

    Carried on `JobEvidence.refusal` by any run an admission guard turned away
    before it worked anything. A refusal is not a failure and not an operator
    cancellation: nothing is broken, the guard did its job, and the operator
    needs to know what refused the run and what would change it -- which, before
    this, only `.runtime/queue-runner.log` on the host knew.

    `reason` is the WHOLE sentence, composed once in `services/usage_ledger`, so
    the log line, this record and the run card all read identically. The rest is
    what a sentence cannot carry: exact figures where the sentence rounds, and an
    instant the UI renders in the reader's own timezone."""

    model_config = ConfigDict(extra="allow")

    # Which guard. `usage-budget` is the only one today.
    by: str | None = None
    # Which of that guard's limits: app_ceiling | scope_ceiling | scope_allocation.
    kind: str | None = None
    reason: str | None = None
    scope: str | None = None
    used: int | None = None
    limit: int | None = None
    window_s: int | None = None
    # ESTIMATED instant the rolling window falls back under the limit on its own,
    # assuming nothing else spends. Null when it cannot be derived.
    next_admissible_at: str | None = None


class JobEvidence(BaseModel):
    """What a run produced. One structured record, not a freeform dump.

    Read off the writers and cross-checked against all 2,761 live jobs on
    2026-08-18. `extra="allow"`, so these keys are a FLOOR: a provider or a
    future worker may add its own and nothing here drops it. Two keys carried
    only by history (`source`, `relay_route`, 7 records from a one-shot job
    import that has since been deleted) are deliberately NOT declared; they
    ride through as extras rather than being enshrined as live vocabulary.

    `summary` is the field every reader wants: the worker's own one-line
    account, present on 2,024 of the 2,032 structured records. It is also
    where the store folds the two retired freeform-string producers, so a
    reader never has to ask which shape it got.
    """

    model_config = ConfigDict(extra="allow")

    # Common to every producer.
    kind: str | None = None
    summary: str | None = None
    # Set when a sweep stopped BEFORE doing any work, which must not read like
    # a run that did work and failed.
    halted: str | None = None
    # Set when a GUARD is what stopped it, which is not the same event as the
    # sweep choosing to stop (yielding, running out of wall clock). `halted`
    # still carries the sentence; this carries the facts under it.
    refusal: JobRefusal | None = None
    reviewer: str | None = None
    gate: str | None = None

    # The reviewed-work receipt `project_ralph.sh` writes (kind="receipt").
    # `files` is repo-relative paths from the COMMITTED diff and `commits` is
    # full shas, both built by `build_receipt_evidence`. Readers report their
    # LENGTH, not their contents.
    files: list[str] | None = None
    commits: list[str] | None = None
    diff_files: int | None = None
    baseline: str | None = None
    head: str | None = None
    design: str | None = None
    typecheck: TypecheckVerdict | None = None
    iterations: int | None = None
    tokens: int | None = None

    # Historical deploy and rollback runs. The deploy subsystem was deleted
    # 2026-09-04 (ticket `726d75a9`) and nothing writes these any more; they
    # stay on the model because old `run` records in the jobs feed still carry
    # them and a reader must not choke on one.
    url: str | None = None
    commit_sha: str | None = None
    upload_ref: str | None = None
    log_tail: str | None = None
    missing_keys: list[str] | None = None
    done: bool | None = None


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
    evidence: JobEvidence | list[JobAwaitingQuestion] | None = None
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


# ---- Tools (docs/platform-redesign/tools-are-declared-2026-08-23.md) --------
# Shared because two routers serve the same enumeration: `/tools` (the tools
# surface, with the user-owned CRUD) and `GET /agents/tools` (what an agent's
# allowlist may name). One description, per this file's own rule.


class ToolParam(BaseModel):
    """One typed input a `command` tool accepts.

    Read off `services/tool_registry.ToolParam`. A declared parameter list
    rather than a raw JSON-Schema blob: a `dict` field here would generate
    `Record<string, never>` in the client (see `scripts/check_opaque_fields.py`)
    and the frontend would hand-write the shape anyway."""

    name: str
    type: str = "string"
    description: str = ""
    required: bool = False


class ToolSpec(BaseModel):
    """A tool's kind-specific launch detail. `extra="allow"` because the spec
    grows per kind: the declared floor below is what the three current kinds
    use, and an unrecognised key survives the round trip rather than being
    dropped by the response model."""

    model_config = ConfigDict(extra="allow")

    app: str | None = None       # app provenance: the app that declared it
    server: str | None = None    # mcp: the server exposing the tool
    tool: str | None = None      # mcp: the tool's name on that server
    command: list[str] | None = None   # command: the argv
    params: list[ToolParam] | None = None


class ToolRecord(BaseModel):
    """A named capability an agent may be granted. Read off
    `services/tool_registry.Tool.to_dict`.

    ENUMERATION, NOT AUTHORITY: appearing here does not put a tool on any
    agent's argv. `services/llm_router.tool_flags` remains the only thing that
    grants a tool, and this record says what exists and who owns it."""

    id: str
    title: str
    description: str = ""
    kind: str          # builtin | mcp | command
    provenance: str    # platform | app | user
    spec: ToolSpec = ToolSpec()
    scopes: list[str] = []
    ceiling: list[str] = []
    # provenance == "user". Derived, and sent so a client does not have to
    # re-derive the rule; the API enforces it independently either way.
    editable: bool = False


class ToolsOut(BaseModel):
    tools: list[ToolRecord]


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


# --- the lease, as any resource that names a holder reports it -----------------
#
# IT LIVES HERE RATHER THAN BESIDE ITS FIRST READER. `LeaseRef` was declared in
# `routes/presence.py`, which is right up until a second router needs it: the
# chat's worktree resource does, and importing it from `presence` put a route
# module into the backend's largest import cycle to borrow one shape. This file
# is the leaf every router already imports, so the shape has one definition and
# no router depends on another router to get at it. `routes/presence.py`
# re-exports it, so the three existing importers are unchanged.
class LeaseRef(BaseModel):
    """The lease on a directory entry's subject, read at join time.

    TYPED, not a `dict[str, Any]`. An opaque field satisfies the route-level
    response_model check and still generates `Record<string, never>` in the
    client, so the frontend hand-writes the shape and drifts from it -- which is
    the defect ADR 0023 clause 6 exists to remove.

    `state` has exactly two values (section 6): `held` or `expired`. `idle_since`
    is the moment of the last work event on a lease whose work has not moved for
    ten minutes, and null otherwise; it is a warning a client renders, not a
    third state.
    """
    subject_kind: str
    subject_id: str
    holder_kind: str
    holder_id: str
    principal_user_id: str | None = None
    generation: int
    state: str
    idle_since: str | None = None
    expires_at: str | None = None
    acquired_at: str | None = None
    last_renewed_at: str | None = None
    last_work_at: str | None = None
