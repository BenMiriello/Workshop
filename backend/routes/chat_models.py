"""Response models for `routes/chat.py` (ADR 0023 clause 6).

WHY A SIBLING MODULE. `health.py` and `location.py`, the ADR's two exemplars,
declare their models inline, and that is the pattern where it fits. `chat.py` is
already 4,110 lines -- the largest file in `backend/` and long past
`check_module_layout.py`'s 800-line big-file threshold -- so another 600 lines of
model declarations would make the worst file in the tree measurably worse for no
gain. Shapes returned by more than one ROUTER still belong in `api_models.py`;
these are chat's own, so they live beside chat.

WHAT A `response_model` DOES, and why it is the dangerous half of this work.
FastAPI FILTERS the handler's return value through the declared model and
silently DROPS any key the model does not name. An under-declared model is not a
documentation gap, it is a live API field that disappears with a green test
suite. Every model below was read off the EMITTER -- `services/chat_store.py`,
`chat_list_pg.py`, `chat_queue.py`, `chat_branches.py`, `db/chat_repo.py`,
`chat_work.py`, `inbox.py`, `ticket_drafts.py`, `draft_store.py`,
`mcp_health.py`, `mcp_registry.py`, `context_report.py`, `build_streaming.py` --
and not off `frontend/src/lib/chat.ts`, whose types are known-incomplete (that
incompleteness is the finding ADR 0023 exists to fix).

THE THREAD DOCUMENT'S FIELD SET WAS MEASURED, NOT GUESSED. `chat_store._document`
returns `meta` minus `_INTERNAL_META` plus three derived keys, and `meta` is
patched by a dozen callers, so the enumerable set is not obvious from the code.
Counted over all 95 live `meta.json` files on 2026-08-18: 30 distinct keys, of
which 6 are stripped as internal. `ThreadSettings` below is that measured set plus
the derived three, plus `agent_sessions`, which `chat_store._patch` writes and
no live thread carries yet.

TWO PAYLOADS ARE DELIBERATELY LEFT OPEN, and both are the ADR's own reasoning:

  * `ThreadEvents.events` and any live-message view stay `list[dict]` / `dict`.
    The event log is heterogeneous by design (message, boundary, scope, turn
    kinds, each with its own keys) and the renderer splits replies on positional
    fields -- `meta.cut_id`, `seg.cut_after`, `meta.cut_head` -- that a closed
    model would drop. Dropping exactly those fields is the 2026-08-14 regression
    that put the operator's message above half an hour of work, and pinning this
    seam already has its own open ticket ("Pin the backend-to-renderer seam with
    a contract test"). Narrowing it here, ahead of that test, would be doing the
    dangerous half of that ticket without the half that catches it.
  * `ContextDetail`'s CLI-derived block. `/context` is parsed out of another
    process's markdown; its section set is that CLI's, not ours.

THE THREE THREAD-STATE MAPS ARE TYPED WITH AN OPEN FLOOR, NOT LEFT OPAQUE.
`turn_marks`, `card_states` and `ticket_drafts` were `dict[str, Any]`, which
satisfies `response_model=` and then generates `Record<string, never>` in the
TypeScript client -- a type whose every property is `never`, so the frontend
hand-writes the shape anyway and the drift this file exists to stop simply
moves one level down, from the route to its fields. Each now has a model whose
fields were read off the writers and confirmed against all 37 live threads
carrying them (0 differences, both dump modes), with `extra="allow"` so a
writer that adds a key still cannot have it dropped. That combination is the
point: a floor the client can rely on, and no ceiling.

RFC3339 AND `*_at` (ADR 0023 clause 4) ARE NOT APPLIED HERE. `ts`, `created`,
`updated`, `checked_at` (epoch seconds) and `sent_at` are the names and encodings
the live stores, the live frontend and the on-disk logs use. Renaming them is a
wire-shape change to every chat surface, and the ADR's migration posture makes
such a change incomplete until the frontend reads the new name in the SAME
commit. Recorded as debt here rather than half-changed.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from routes.api_models import JobRefusal, Task


# ------------------------------------------------------------------ providers

class EffortCaps(BaseModel):
    """`levels` is the vendor's ladder for this model; `default` folds in the
    operator's preference, which is why it can differ per reading."""

    levels: list[str]
    default: str | None = None


class ModelCaps(BaseModel):
    vendor: str
    efforts: EffortCaps


class ModesPayload(BaseModel):
    """Vendor-level, not per model: a permission mode is a property of the CLI
    running the turn, not of the model answering it."""

    options: list[str]
    default: str | None = None


class Provider(BaseModel):
    """One entry in the chat's scope picker. Every agent is a place you can type,
    so agents appear here alongside the built-in providers and the project
    build contexts."""

    # `model_caps` collides with pydantic's protected `model_` namespace. The
    # key is the wire name the frontend already reads, so the namespace is
    # opened rather than the field renamed.
    model_config = ConfigDict(protected_namespaces=())

    key: str
    label: str
    models: list[str]
    default_model: str | None = None
    tools_active: bool
    model_caps: dict[str, ModelCaps]
    modes: ModesPayload


# -------------------------------------------------------------------- threads

class ThreadSummary(BaseModel):
    """One sidebar row. `chat_store._summary_row` and `chat_list_pg._shape_row`
    emit exactly this, field for field -- that identity is what keeps the file
    fold and the Postgres path from drifting.

    `scope` is DERIVED from `context_key` on every read and is stored by nobody.
    `last_user_at` is when the OPERATOR last spoke, which is what the list orders
    by; `updated_at` moves for background writes and would put a chat nobody has
    touched above the one they were just in."""

    id: str
    context_key: str
    scope: str
    mode: str
    agent_id: str | None = None
    model: str | None = None
    title: str
    created_at: str | None = None
    updated_at: str | None = None
    archived_at: str | None = None
    last_user_at: str | None = None
    message_count: int
    last_message: str
    # IS A TURN IN FLIGHT ON THIS THREAD. Derived per read, never stored:
    # `chat_store.stamp_running` reads `meta.active_turn_id`, the same fact the
    # open chat already renders through `chat_log.active_turn`. It is on the row
    # so a surface showing many chats does not make one call per chat.
    running: bool = False
    # HOW FULL THE WINDOW IS: the size the model itself reported on the last
    # turn, over the one table (`services/context_window`). Both null until a
    # turn has completed -- a list row does not estimate, because the estimate
    # needs the draft and only the composer has that.
    context_tokens: int | None = None
    context_window: int | None = None
    # WHICH WORKTREE THIS CHAT IS PINNED TO. None for trunk (Main). Added so
    # the Chats page can group threads by worktree without a per-thread call.
    branch_slug: str | None = None
    # WHAT ADVANCES THIS THREAD WITHOUT A PERSON, and which definition it
    # answers as (threads-branches-and-pages-2026-08-29 sec 1). Two independent
    # settings: neither implies the other, so an "agent" on the wire is simply a
    # thread whose `armed` is not `off`. `role` names a role definition and is
    # nullable -- null is a chosen value, not a missing one, and is never
    # coerced to a default.
    armed: str = "off"
    role: str | None = None


class StoredCardState(BaseModel):
    """One in-chat ui-card's outcome, as it is STORED on the thread.

    NOT the write body -- that is `routes.chat.CardState`, which carries the
    `key` this is filed under and never carries `ts`, because
    `chat_store.set_card_state` stamps that itself. The two shapes are
    genuinely different and the generated client now says so.

    Measured over every live thread on 2026-08-18: 12 entries across 6 threads,
    all five fields present on all twelve. `extra="allow"` keeps the per-kind
    openness the write path has always had -- a card kind owns the shape of its
    own state, so a typed floor here must be unable to DROP anything a future
    kind files. Verified: pydantic retains extras through `model_dump()` and
    through `model_dump(exclude_unset=True)`, which is the mode these routes
    serialise in.
    """

    model_config = ConfigDict(extra="allow")

    # "success" | "dismissed".
    phase: str
    note: str | None = None
    slug: str | None = None
    title: str | None = None
    ts: str | None = None


class TurnMarks(BaseModel):
    """What this thread has already been TOLD, so a turn can avoid saying it
    twice. These three are `chat_store.get_turn_marks`'s own documented set and
    the only three any writer sets today (`routes/chat.py` writes
    `contract_hash`, `live_hash` and `seen_scope_version`).

    `set_turn_marks(thread_id, **fields)` takes arbitrary keywords by signature,
    so this is a floor and not a closed set; `extra="allow"` is what makes
    adding a fourth mark a one-line change rather than a silently dropped field.
    """

    model_config = ConfigDict(extra="allow")

    live_hash: str | None = None
    seen_scope_version: int | None = None
    contract_hash: str | None = None


class TicketDraftScan(BaseModel):
    """The SCAN state the ticket-draft pass leaves on a thread: when it last
    ran and how much of the conversation it had read, which is how
    `messages_since` is computed on the next read.

    `services/ticket_drafts.py` writes exactly `generated_at` and
    `message_count` -- deliberately NOT the drafts, because the filed tickets
    ARE the drafts and a second copy here is what that refactor removed. Three
    live threads still carry a `drafts` list written before it, so the field is
    declared rather than dropped: a response model that omits it would delete
    those from the wire.
    """

    model_config = ConfigDict(extra="allow")

    generated_at: str | None = None
    message_count: int | None = None
    drafts: list[dict[str, Any]] | None = None


class ThreadSettings(BaseModel):
    """A thread as SETTINGS: everything `chat_store._document` returns except the
    transcript. Every route that answers with a thread is answering a settings
    question; the transcript is served by GET /events. Measured 2026-08-14, the
    worst thread's `messages` was 4.77 MB of a 4.79 MB response no client reads,
    which is what `_without_messages` exists to strip.

    The field set is the measured union of all 95 live `meta.json` files, minus
    the six internal keys, plus the derived three. `seed_title` and
    `name_attempts` are aliased because their stored names lead with an
    underscore, which pydantic reserves for private attributes.

    Almost everything is optional because meta accretes: a thread created before
    a field existed simply does not carry it, and `_document` copies what is
    there rather than filling in defaults."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    context_key: str
    # Derived on every read from `context_key`. A stored copy is written by
    # nobody and trusted by nobody -- see chat_store._document.
    scope: str
    mode: str
    agent_id: str | None = None
    model: str | None = None
    title: str
    created_at: str | None = None
    updated_at: str | None = None
    archived_at: str | None = None
    effort: str | None = None
    permission_mode: str | None = None
    # None means ALL servers, which is both the default and what every chat did
    # before the control existed. A list is an explicit subset; never [].
    mcp_servers_enabled: list[str] | None = None
    claude_session: str | None = None
    context_tokens: int | None = None
    # A MARKER, not a count, and the only value it ever holds is the literal
    # string "cleared" (chat_store.clear_thread_context). The name reads like a
    # message index and is not one; recorded as it is rather than renamed, since
    # renaming it is a wire-shape change.
    turn_marks: TurnMarks | None = None
    card_states: dict[str, StoredCardState] | None = None
    ticket_drafts: TicketDraftScan | None = None
    # The resident voice worker's session id, a bare string -- one more copy of
    # "what this thread has already been told", which is why /clear drops it.
    voice_session: str | None = None
    # slug -> claude session id, one per @mentioned agent.
    agent_sessions: dict[str, str] | None = None
    converted_from: str | None = None
    converted_at: str | None = None
    # Present only when the resolved boundary has one; `_document` pops it
    # otherwise, so a fork past a compaction does not carry a summary of a line
    # that no longer exists.
    summary: str | None = None
    # Derived, and present only when a cut resolved.
    cleared_through: int | None = None
    cut_mode: str | None = None
    # WHAT ADVANCES THIS THREAD WITHOUT A PERSON, and which definition it
    # answers as (threads-branches-and-pages-2026-08-29 sec 1). Two independent
    # settings: neither implies the other, so an "agent" on the wire is simply a
    # thread whose `armed` is not `off`. `role` names a role definition and is
    # nullable -- null is a chosen value, not a missing one, and is never
    # coerced to a default.
    armed: str = "off"
    role: str | None = None
    seed_title: str | None = Field(default=None, alias="_seed_title")
    name_attempts: int | None = Field(default=None, alias="_name_attempts")


class AttachmentRef(BaseModel):
    """A file already uploaded, as it rides along on a QUEUED message or a
    saved draft -- a reference, not the row. `routes.chat.AttachmentRefIn` is
    the matching write shape; `AttachmentRow` below is the index entry, which
    is a different and much larger thing (size, scope, thread, missing).

    Measured over the live queue and draft stores on 2026-08-18: every stored
    attachment reference carries exactly `id`, `name` and `kind`. `name` and
    `kind` are carried so the transcript renders a chip without a second
    lookup; the server re-reads both from its own index before handing anything
    to a model, so a wrong one here can only mislabel a chip.
    """

    model_config = ConfigDict(extra="allow")

    # Every field defaults, `id` included. The write path validates
    # `AttachmentRefIn`, which requires `id`, so anything stored today has one --
    # but this model is applied to HISTORY as well, and it rides on
    # `GET /threads/{id}/events`, which is the single endpoint the transcript
    # load, the live poll, the active-turn snapshot, the cut divider, the branch
    # identity and the send queue all share. A 500 there presents as four
    # unrelated-looking bugs at once and names none of them (measured
    # 2026-08-17: a wrong response_model field type took it down for ~7 minutes
    # and produced exactly that). Requiring `id` buys no client capability,
    # since a client that has the reference already has the id.
    id: str = ""
    name: str = ""
    kind: str = ""


class QueuedMessage(BaseModel):
    """A message the SERVER has taken responsibility for but has not sent. The
    per-turn overrides ride with it so a queued message runs under the model,
    effort and permission mode it was typed with, not whatever the thread holds
    when it finally drains."""

    client_id: str
    text: str
    ts: str
    received_at: str
    # Already in the log: a delivery was attempted against a turn that could not
    # read it, so finding it in the transcript does not mean it was answered.
    recorded: bool | None = None
    attachments: list[AttachmentRef] | None = None
    model: str | None = None
    effort: str | None = None
    permission_mode: str | None = None
    # WHO PUT THIS HERE. Absent means the operator typed it. When present the
    # values are: person | thread:<id> | run:<job-id> | agent:<slug> | platform.
    # `role` (user/assistant) stays the model-API field it always was; `sender`
    # is ours and means "who put this here", which `role` never answered.
    sender: str | None = None


class QueueState(BaseModel):
    """`queue_error` is why the queue is not moving, when it is not moving for a
    reason the operator has to see. A queue that stopped and does not say so is
    the same lie as a message that looks sent and is not."""

    queued: list[QueuedMessage]
    queue_held: bool
    queue_error: str


class ThreadDocument(ThreadSettings):
    """The thread WITH its transcript, which is what `chat_store.get_thread`
    returns and what POST /threads answers with. Only the create route serves
    this shape; every read route strips `messages` -- see `ThreadSettings`.

    A turn in flight appears as a trailing assistant message carrying
    `streaming: true` and its `turn_id`, built as a VIEW of `meta.live_message`
    rather than stored. `messages` is left open for the same reason
    `ThreadEvents.events` is; see the module docstring."""

    messages: list[dict[str, Any]] = []


class ThreadWithQueue(ThreadSettings, QueueState):
    """GET /threads/{id}: the settings document merged with the queue, from one
    read, so opening a chat shows what is waiting in it on the first paint."""

    # "compact" | "clear" while one is running, else None. It is IN-PROCESS
    # truth published on a read, NOT a persisted flag: thread_ops holds it in
    # memory precisely so a crash cannot leave a stale "operation in progress"
    # behind, and that stays true. What was wrong was that no client could see
    # it. The operator compacted, navigated away, came back to no indication at
    # all, compacted again, and was told by an error string that one was already
    # running. A second client, or the same tab after a remount, had nothing to
    # read and rendered the button enabled.
    pending_operation: str | None = None


class ThreadDeleted(BaseModel):
    deleted: bool


class Rescoped(BaseModel):
    """POST /rescope answers with a summary-shaped record rather than the full
    document, and it is NOT `ThreadSummary`: it carries no `scope`, `mode` or
    `archived_at`. Described as it is rather than aligned, since aligning it
    would be a wire-shape change."""

    id: str
    context_key: str
    agent_id: str | None = None
    model: str | None = None
    title: str
    created_at: str | None = None
    updated_at: str | None = None
    message_count: int
    last_message: str


class ChatModeSet(BaseModel):
    id: str
    mode: str | None = None


class ThreadArming(BaseModel):
    """What advances this thread without a person, and which definition it
    answers as. The response to `POST /threads/{id}/arming` and the shape the
    Roles surface reads back.

    Both are always present here even though the request may carry one: the
    caller asked to change the thread's arming and the honest answer is what
    the thread now holds, not an echo of the half it sent."""

    id: str
    armed: str
    role: str | None = None


class CardStates(BaseModel):
    """Which in-chat cards have been acted on. Opaque per card kind: the client
    that renders a card owns the shape of its own state."""

    card_states: dict[str, StoredCardState]


# --------------------------------------------------------------------- drafts

class Draft(BaseModel):
    """The unsent text in a composer, plus the files attached to it -- a draft is
    everything in the box. `superseded` is set when a write LOST to a strictly
    newer stored entry, so the caller reconciles instead of believing a write
    that did not happen."""

    key: str
    text: str
    ts: int
    client_id: str
    attachments: list[AttachmentRef] | None = None
    superseded: bool | None = None


# ---------------------------------------------------------------------- prefs

class ChatPrefsOut(BaseModel):
    """Operator-level chat settings. The CHOICES are served, never assumed by the
    client: which permission modes a chat may pick is this system's decision
    (bypass is not among them) and the effort ladder is the vendor's. A null
    default means 'no preference', which renders as the model-default option
    rather than as a blank."""

    default_mode: str | None = None
    modes: list[str]
    default_effort: str | None = None
    efforts: list[str]
    default_permission_mode: str | None = None
    permission_modes: list[str]
    platform_permission_mode: str


# --------------------------------------------------------------------- events

class ActiveTurn(BaseModel):
    """The turn in flight. `live` is the draft reply as it is being written --
    the claim's immutable facts (model, agent, start ts) overlaid with what the
    turn rewrites constantly -- and is left open for the same reason `events` is.

    `accepts_input` is added by the route, not by `chat_log`, because only this
    layer sees the turn registry. A command turn takes no input, and a client
    that does not know that sends into a void."""

    turn_id: str
    started_seq: int | None = None
    live: dict[str, Any] | None = None
    accepts_input: bool | None = None


class SyncBranch(BaseModel):
    """Which branch the sync endpoint is serving. A THIRD branch shape, and
    deliberately so: the client compares `id` to notice the branch changed under
    it and re-read from zero, and renders `head` to grey out a branch action
    that would be a no-op. It needs neither the fork previews a `Branch` list
    entry computes nor the parent pointer `CurrentBranch` carries, and it is the
    only one of the three that carries `head` at all.

    Declaring this `str` is what took the whole chat surface down on 2026-08-17:
    `/threads/{id}/events` is THE sync endpoint, so every thread with a
    relational branch row 500d on load, on poll and on turn completion.
    """

    id: str
    name: str | None = None
    head: str | None = None


class ThreadEvents(BaseModel):
    """THE sync endpoint. Everything needed to render a transcript is here, and a
    client merges it with nothing except the live SSE for the active turn.

    `events` is `list[dict]` deliberately -- see the module docstring. `after` is
    a cursor, not an index. `has_more` and `first_seq` carry information only on
    a `tail`/`before` page; an `after=0` call already returns the whole log.

    `cleared_through` and `cut_mode` are the cut the SERVER is applying, derived
    from the same resolution the prompt uses, which is what makes the divider and
    the prompt incapable of disagreeing. The queue rides here rather than on a
    second endpoint so the transcript and the queue cannot disagree about a
    message that is in one and not the other."""

    events: list[dict[str, Any]]
    last_seq: int
    branch: SyncBranch | None = None
    branch_count: int
    active_turn: ActiveTurn | None = None
    has_more: bool
    first_seq: int | None = None
    cleared_through: int
    cut_mode: str
    queued: list[QueuedMessage]
    queue_held: bool
    queue_error: str
    # On the sync response as well as on the thread document, for the same
    # reason the queue is on both: the GET paints it once when the chat opens,
    # and sync is what keeps it true afterwards. A client that only ever saw it
    # at open would go stale the moment another tab started a fold.
    pending_operation: str | None = None


# ------------------------------------------------------------------- branches

class Branch(BaseModel):
    """One line of the conversation. The CURRENT branch is included and flagged
    rather than filtered out: a list that silently omits where you are makes the
    other entries unreadable."""

    id: str
    name: str | None = None
    ordinal: int | None = None
    is_current: bool
    is_root: bool
    created_at: str
    fork_message_id: str | None = None
    forked_from: str | None = None
    forked_from_role: str | None = None
    forked_at: str | None = None
    preview: str | None = None
    last_at: str


class BranchList(BaseModel):
    """Empty rather than an error when the relational store is unreachable: a
    chat renders without this, it just cannot be branched."""

    branches: list[Branch]


class CurrentBranch(BaseModel):
    """`chat_repo.current_branch` -- the raw row, which is a DIFFERENT shape from
    a `Branch` list entry: it carries the head pointer and the parent id, and
    none of the fork previews the list computes."""

    id: str
    name: str | None = None
    ordinal: int | None = None
    fork_message_id: str | None = None
    head_message_id: str | None = None
    parent_branch_id: str | None = None
    # A real datetime here, unlike the ISO string `Branch` carries: this row is
    # handed back raw and only its UUIDs are stringified.
    created_at: datetime | None = None


class ForkResult(BaseModel):
    """`queued_held` is how many messages the server was holding for the line
    just left. It is REPORTED, not acted on: cancelling them would destroy work
    the operator never asked to destroy, and staying silent is how it became a
    surprise."""

    branch: CurrentBranch
    branches: list[Branch]
    queued_held: int


class SwitchResult(BaseModel):
    branch: CurrentBranch
    branches: list[Branch]


class RenameBranchResult(BaseModel):
    branches: list[Branch]


# -------------------------------------------------------------- ticket drafts

class DraftTask(Task):
    """A draft ticket. `dedup` is the merge verdict and is stamped only by the
    PROPOSE path, so it is absent when the drafts are simply read back."""

    dedup: str | None = None


class TicketDrafts(BaseModel):
    """This chat's open draft tickets, with staleness against the transcript.
    `messages_since` is what lets the UI say '5 messages since these were
    drafted' rather than making the operator guess. Zero drafts is a normal
    answer: most of what is said in a conversation is discussion, reversal, or
    work already done."""

    drafts: list[DraftTask]
    scope: str | None = None
    generated_at: str | None = None
    message_count: int
    messages_since: int


class TicketScope(BaseModel):
    """Where this chat's tickets file, and whether that scope can run them.
    `runnable` answers 'can this be run by hand'; `automatic` answers 'will
    anything pick it up on its own', which is false for every project scope today
    and was previously invisible until the ticket sat forever."""

    scope: str
    runnable: bool
    automatic: bool
    why: str


# ----------------------------------------------------------------------- work

class RunSummary(BaseModel):
    """The latest run of a ticket, in the fields a job record HAS. `files` and
    `commits` are COUNTS and are null unless the evidence is the structured
    receipt `project_ralph.sh` writes: zero would claim the receipt said nothing
    changed, and a key that comes and goes makes every reader test for two
    spellings of the same absence."""

    id: str | None = None
    status: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    agent_name: str | None = None
    trigger: str | None = None
    exit_code: int | None = None
    files: int | None = None
    commits: int | None = None
    # WHY IT STOPPED. `halted` is the sentence a sweep closed with when it
    # stopped before working anything; `refusal` is present only when a GUARD is
    # what stopped it, and it is what lets the card say "refused" (a working
    # guard, nothing broken) instead of "cancelled" (indistinguishable from a
    # crash, which is how the operator read it on 2026-08-23).
    halted: str | None = None
    refusal: JobRefusal | None = None


class WorkTask(Task):
    """A ticket as the work panel shows it: the canonical Task plus its latest
    run, stamped on by `chat_work.thread_work`."""

    run: RunSummary | None = None


class InboxItem(BaseModel):
    """Something waiting on the operator. `kind` is question, review or notify,
    and they rank in that order because a blocked agent must not be buried under
    notifications."""

    id: str
    kind: str
    state: str
    agent: str | None = None
    scope: str
    title: str
    body: str
    options: list[str]
    chat_id: str | None = None
    ticket_id: str | None = None
    run_id: str | None = None
    dedupe_key: str | None = None
    created_at: str | None = None
    resolved_at: str | None = None
    answer: str | None = None
    snoozed_until: str | None = None


class WorkCounts(BaseModel):
    """`needs_you` is THE badge. An open-ticket count is ambient information
    wearing an alarm's clothing, so only what actually wants the operator counts
    there; the other three are chip counts. `all` is the size of the set the chip
    opens, not the size of the page shown."""

    needs_you: int
    running: int
    this_chat: int
    all: int


class ThreadWork(BaseModel):
    """Everything this chat's work surfaces show, in one read. The badge, the
    Work panel and every ticket card in the transcript poll this and nothing
    else -- they used to poll `/inbox` and `/tasks` on separate intervals, which
    let three surfaces hold three copies of one ticket and disagree."""

    inbox: list[InboxItem]
    filed: list[WorkTask]
    scope: list[WorkTask]
    counts: WorkCounts
    scope_key: str | None = None


class SpawnedTask(BaseModel):
    """`decision` is the dedup verdict (`new`, `merge`, `exact`, ...). A merge or
    exact is never run: it folded into an existing ticket, and running that would
    be a surprise re-run of unrelated work. `job_id` appears only on file-and-run
    for a freshly filed ticket, and `run_error` only when that launch failed."""

    task: Task | None = None
    scope: str | None = None
    thread_id: str
    decision: str
    matched_id: str | None = None
    cosine: float | None = None
    message: str
    job_id: str | None = None
    run_error: Any = None


# --------------------------------------------------------------------- folding

class OperationCancelled(BaseModel):
    """`cancelled: false` is not an error: it means the operation landed while
    this request was on the wire, which is a race, not a failure."""

    cancelled: bool
    operation: str


class CompactResult(BaseModel):
    """Compaction is the CLI's own `/compact`, run as a turn on this chat's
    live session, so this reports what happened to that TURN and nothing else.

    `turn_id` is the turn now running; the boundary, the divider and the new
    window reading all arrive on the change stream the same way any turn's
    output does. `queued` means the chat was busy and the command joined the
    waiting queue, which is a real acceptance, not a refusal.

    The old shape reported a fold Workshop computed itself (`folded`,
    `summary_chars`, `session_reset`, `deferred`). There is no such fold any
    more."""

    turn_id: str | None = None
    queued: bool | None = None
    error: str | None = None


class ClearResult(BaseModel):
    """Same five paths as `CompactResult`. `cleared_through` is how many messages
    are behind the cut; zero on the deferred path because the cut has not landed
    yet."""

    cleared: bool
    cleared_through: int
    session_reset: bool
    deferred: bool | None = None
    cancelled: bool | None = None
    busy: bool | None = None


# ---------------------------------------------------------------------- turns

class MessageAccepted(BaseModel):
    """Every branch is an acceptance -- running now, handed to the turn in
    flight, or queued. `injected` means the bytes reached the running process,
    NOT that the model read them: measured, the model addresses a mid-turn
    message at the end of its current plan."""

    turn_id: str
    injected: bool | None = None
    queued: bool | None = None
    at_segment: int | None = None
    queue: QueueState | None = None


class InjectResult(BaseModel):
    """THE CHAT HAS THE MESSAGE either way -- it is in the log before any
    delivery is attempted, so re-sending would show it twice. `delivered` says
    whether the RUNNING turn took it; false means a fresh turn was started."""

    delivered: bool
    injected: bool | None = None
    queued_for_turn: bool
    queued: bool | None = None
    turn_id: str
    at_segment: int | None = None
    queue: QueueState | None = None
    reason: str


class PermissionAnswered(BaseModel):
    """A mode change is recorded only AFTER the answer landed: recording it for a
    turn that never got the approval would leave the chat looser than the
    operator believes, on the strength of a click that did nothing."""

    state: str
    permission_mode: str | None = None


class TurnStopped(BaseModel):
    """Never an error. Stopping a turn that already finished is a no-op that
    returns `stopped: false`, because 'it is already stopped' is the outcome the
    caller wanted. `committed: false` with `stopped: true` means the commit is
    still coming -- said plainly rather than claiming an ending that has not been
    written yet."""

    turn_id: str
    stopped: bool
    committed: bool | None = None
    reason: str


class ThreadTreeDiff(BaseModel):
    """This chat's worktree against the tree it would merge into.

    ONE SHAPE FOR EVERY CHAT, including one that holds no worktree at all: an
    unpinned chat comes back `pinned: false` with `reason` saying why, so the
    caller renders one thing instead of branching on whether this chat happens
    to have a branch. That contract is `services/chat_worktree.diff`'s own and
    is restated here only as types.

    `summary` and the entries of `files` stay loose because they are git's
    numbers passed through (`worktrees.diff_branch`), not a shape this layer
    decides. Typing them here would be this module guessing at another one's
    output, which is how a declared type ends up describing something the route
    does not return.
    """

    pinned: bool
    project_slug: str | None = None
    branch_slug: str | None = None
    branch: str | None = None
    cwd: str | None = None
    uncommitted: list[str] = []
    files: list[dict[str, Any]] = []
    summary: dict[str, Any] = {}
    ahead: int = 0
    behind: int = 0
    base_ref: str = ""
    # False for every early return: not pinned, branch unreadable, branch gone,
    # diff unreadable, or level with the base. `reason` carries which.
    mergeable: bool = False
    reason: str = ""


class ThreadTreeMerge(BaseModel):
    """The result of merging this chat's worktree back.

    Only the SUCCESS shape is declared, because a refusal never reaches here:
    the route raises 409 with the reason as the body, on the grounds that every
    refusal this can hit is a fact about the trees rather than a failure. So
    `ok` is true whenever this model is what was serialised, and `reason` is
    kept for the merge's own commentary on a success.
    """

    ok: bool
    reason: str = ""
    commit: str = ""
    committed: list[str] = []
