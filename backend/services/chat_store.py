"""Chat thread + message persistence — the generalized history layer.

App-agnostic: a thread carries a `context_key` (an app id, or "general") that
tells the chat route which context provider to wire in, but the store itself
knows nothing about any app.

THIS MODULE IS A VIEW. The record is `chat_log`: an append-only, sequence-
numbered event log per thread. Everything here folds that log into the thread
document callers have always been handed — `{id, title, ..., messages: [...]}` —
and turns each mutation back into the events that express it.

That inversion is the point. Every mutation used to be a read-modify-write of
one whole JSON document, which is safe only while no `await` sits between the
read and the write. `/compact` broke the rule by design: read the thread, spend
~30s summarizing, write the snapshot back — and everything the operator typed
during the fold was gone. There is now no operation that writes the transcript
as a whole, so a stale snapshot cannot erase anything. The `LostUpdate` tripwire
that used to catch that (by refusing any write that shrank the transcript) is
gone with it: it guarded an operation that no longer exists.

`save_thread` is the compatibility shim for callers that still mutate a thread
dict in place. It DIFFS the dict against the log and appends only what actually
changed. Passes B and C remove the last of those callers, and with them this
whole document view.
"""
from __future__ import annotations

import asyncio
import bisect
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import chat_log, context_window, ownership, scope_tree, user_prefs  # noqa: F401  (ownership re-exported for tests)

log = logging.getLogger("workshop.chat_store")

# Threads are partitioned per owner: `.runtime/users/<owner_id>/chat/<id>/`.


def _chat_dir() -> Path:
    return chat_log.chat_dir()


# How a chat handles work. The store owns the field, so it owns the enum; the
# prompt text for each mode lives in chat_context.chat_mode_block.
#
#   direct        the conversation does the work. Native subagents are available
#                 for context isolation and report back here; the transcript IS
#                 the record. Labelled "Direct" in the UI.
#   auto          work leaves as Tickets run by Workshop agents; the Ticket is
#                 the record. Filed and started WITHOUT asking first, stopping
#                 only for a question that cannot be answered. Labelled "Auto"
#                 in the UI.
#
# THERE WERE THREE. `orchestrated` was "file it but wait for approval" and `auto`
# was the same thing minus the approval step -- and they differed ONLY in prompt
# text, never in a code path. The operator states no intended distinction between
# them (2026-08-27), so there is one mode for "work leaves here" and it is `auto`,
# which is both the name for the behaviour the operator uses and the value on
# more threads (85 of 201 against orchestrated's 44, measured before the
# collapse).
#
# `auto` is an EXPLICIT per-chat operator choice and nothing about it is learned.
# ADR 0006 designed an auto-run preference inferred from past File-and-run
# choices and stored in the learnings store; that is a different mechanism, it
# stays Proposed, and this is not it (tickets-in-chat-2026-08-15 §7).
#
# DIRECT IS THE DEFAULT. The mode that files was, and it made every passing
# remark a candidate for a ticket; work that outlives the chat is the exception,
# and the exception should be the one you ask for. Threads that set a mode
# explicitly keep it; threads that never did follow the default, which is what
# "the default" has to mean for it to be changeable at all.
CHAT_MODES = ("direct", "auto")
DEFAULT_CHAT_MODE = "direct"

# Values that are no longer offered but are still written on disk and in the
# `chat_thread` mirror. Read through `_clamp_mode`, never re-offered.
#
# `orchestrated` MAPS TO `auto`, NOT TO THE DEFAULT. Letting it fall through to
# `direct` would silently turn 44 live threads from "work leaves here" into
# "work stays here" -- a behaviour change on somebody else's conversation, which
# is the one thing a rename is not allowed to do. The relational mirror is
# migrated (revision `a4e2c7b19f30`); the file log is NOT rewritten, because
# read-time mapping is idempotent and needs no bulk edit of live thread logs.
_LEGACY_CHAT_MODES = {"orchestrated": "auto"}


# WHAT ADVANCES A THREAD WITHOUT A PERSON. A thread is the one entity and every
# thread is chattable; `armed` is the ONLY field that decides whether one works
# unasked, and it is independent of `role` -- neither implies the other. A
# thread armed with no role continues with its own context, which is what
# "nudge it along" means. See docs/platform-redesign/threads-branches-and-pages.
#
# Adding or removing a value here means a migration for `ck_chat_thread_armed`
# as well, for exactly the reason spelled out on CHAT_MODES above: the
# constraint is not derived at runtime, and narrowing it under rows that still
# hold the retired value fails against the live database.
THREAD_ARMED = ("off", "on-message", "schedule", "on-ready-ticket")
DEFAULT_ARMED = "off"


def _clamp_armed(armed: str | None) -> str:
    """An unreadable or absent value is `off`. A thread whose stored arming
    cannot be understood must not run unasked -- the failure direction here is
    not symmetric, so it is chosen rather than left to a dict lookup."""
    return armed if armed in THREAD_ARMED else DEFAULT_ARMED


def _clamp_mode(mode: str | None) -> str:
    if mode in CHAT_MODES:
        return mode                       # type: ignore[return-value]
    return _LEGACY_CHAT_MODES.get(mode or "", DEFAULT_CHAT_MODE)


# The operator's chosen default for NEW chats, overriding DEFAULT_CHAT_MODE.
# Lives beside the threads it applies to, one small file per owner. A chat stores
# its mode at creation, so changing this never rewrites a conversation that
# already exists — "default" means the mode a new chat starts in, and nothing more.
# THE FILE ITSELF IS `services/user_prefs`, which is BELOW this module. It moved
# down when the first account-level key (the user's timezone) moved in: that key
# is read by `services/clock`, the prompt layer and the wake-up scheduler sit on
# top of clock, and this module is inside the backend's large import cycle, so a
# resolver reaching in here for one string would have joined the cycle. These
# two names stay as the spelling every setter below already uses.
_read_prefs = user_prefs.read
_write_pref = user_prefs.write


def get_default_mode() -> str:
    """The mode a new chat starts in: the operator's setting, else the platform
    default. Clamped, so a hand-edited junk value can't create unreachable chats."""
    return _clamp_mode(_read_prefs().get("default_mode"))


def set_default_mode(mode: str) -> str:
    """Persist the default for new chats. Returns the stored (clamped) value."""
    value = _clamp_mode(mode)
    _write_pref("default_mode", value)
    return value


# ---- operator defaults for the two turn-posture controls -------------------
# `default_mode` above is baked into a chat AT CREATION, because it says where
# work goes and a conversation should not change its mind about that. These two
# are different: they are consulted at RESOLUTION time, per turn, between the
# thread's stored value and the model's own default (the chain in
# routes.chat._resolve_effort / _resolve_permission_mode). Changing one of these
# therefore moves every chat that never chose for itself, which is what an
# operator asking for "my default" means — a chat that made a choice keeps it.


def get_default_model() -> str | None:
    """The operator's own model default — "which model do my chats use".

    Rung 5 of `services/model_resolution`, below a scope's own declaration and
    above the deployment default. Stored as a bare id and NOT validated here:
    which models exist is a function of what providers are configured, and the
    resolver already skips a rung naming a model this deployment cannot reach.
    Validating here would mean a model that went away could fail a read."""
    v = _read_prefs().get("default_model")
    if not isinstance(v, str):
        return None
    return v.strip() or None


def set_default_model(model: str | None) -> str | None:
    """Persist the operator's model default. Empty/None clears it (back to the
    deployment default). Raises ValueError on a model nothing can reach, because
    unlike a read this is somebody looking at the control."""
    from services import llm_providers

    value = (model or "").strip() or None
    if value is not None and llm_providers.find(value) is None:
        raise ValueError(
            f"unknown model {model!r}; pick one of {llm_providers.model_ids()}")
    _write_pref("default_model", value)
    return value


def get_default_effort() -> str | None:
    """The operator's preferred effort level, or None for "no preference".

    Stored as a bare level name, NOT validated against any model here: which
    levels exist is a per-model fact and the model isn't known until a turn
    resolves. An unspellable value reads as no preference rather than raising, so
    a hand-edited prefs file cannot fail a turn."""
    from services import model_caps

    v = _read_prefs().get("default_effort")
    if not isinstance(v, str):
        return None
    v = v.strip()
    return v if v in model_caps.EFFORT_LEVELS else None


def set_default_effort(effort: str | None) -> str | None:
    """Persist the operator's effort default. Empty/None clears it (back to the
    model's own default). Raises ValueError on a level that isn't a level."""
    from services import model_caps

    value = (effort or "").strip() or None
    if value is not None and value not in model_caps.EFFORT_LEVELS:
        raise ValueError(
            f"unknown effort {effort!r}; pick one of {list(model_caps.EFFORT_LEVELS)}")
    _write_pref("default_effort", value)
    return value


def get_default_permission_mode() -> str | None:
    """The operator's preferred permission mode, or None for "no preference".

    Clamped to the modes the CHAT PICKER offers, which excludes
    `bypassPermissions` — a preference is a chat posture, and this system does
    not offer bypass as one (model_caps.OFFERED_MODES). The unattended paths that
    still pass bypass pass it explicitly and never read this."""
    from services import model_caps

    v = _read_prefs().get("default_permission_mode")
    if not isinstance(v, str):
        return None
    try:
        mode = model_caps.normalize_permission_mode(v)
    except ValueError:
        return None
    return mode if mode in model_caps.OFFERED_MODES else None


def set_default_permission_mode(mode: str | None) -> str | None:
    """Persist the operator's permission-mode default. Empty/None clears it (back
    to model_caps.DEFAULT_PERMISSION_MODE). Raises ValueError on anything the
    picker does not offer, bypass included."""
    from services import model_caps

    value = model_caps.normalize_permission_mode(mode)
    if value is not None and value not in model_caps.OFFERED_MODES:
        raise ValueError(
            f"{value!r} is not offered as a chat default; "
            f"pick one of {list(model_caps.OFFERED_MODES)}")
    _write_pref("default_permission_mode", value)
    return value


def _loop_running() -> bool:
    """Is there an event loop to hand background work to? See append_message."""
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# Meta fields that belong to the log machinery, not to the thread document. They
# never appear in a thread dict a caller sees, so a caller can never write them
# back, and `save_thread` never mistakes their absence for a deletion.
# Keys `save_thread` never deletes on a round-trip: state OTHER writers own,
# which a caller holding a pre-award snapshot would silently strip (the
# latent race window R7 in docs/platform-redesign/chat-concurrency-model).
_PRESERVED_ON_SAVE = ("claude_session", "agent_sessions", "summary",
                      "context_tokens", "turn_marks", "card_states",
                      "_seed_title", "_name_attempts", "_name_idx", "cut_through",
                      "cut_mode", "voice_session", "live_message")

_INTERNAL_META = ("last_seq", "boundary_seq", "active_turn_id", "active_turn_seq",
                  "live_message", "cut_through")


# `_promotion_patch` USED TO BE HERE, and deleting it is what made "chat with an
# agent" exist.
#
# It was a P5 migration that rewrote any `agent:<slug>` or `build:<slug>`
# context_key to `project:<slug>` or `general`, capturing the agent in a
# `participants` list -- at creation AND on every subsequent `_patch`. The model
# it migrated INTO was never built: nothing in routes/chat.py, chat_context.py,
# mentions.py, build_streaming.py or the voice path ever read `participants`, so
# the rewrite moved every agent chat to a provider that is not the agent and
# recorded who it should have been in a field with no readers.
#
# Measured on 2026-08-16: `AssistantAgentContextProvider` and
# `BuildAgentContextProvider` were both UNREACHABLE, 0 of 94 live threads had an
# `agent:` context key, and AgentDetail's "Start chat with agent" button opened a
# chat answered by the general assistant. There was no way to talk to an agent at
# all except @mention, which is a per-turn override.
#
# `agent:<slug>` is now a durable context key like any other, and the provider it
# resolves to is that agent. Nothing rewrites a context key on write any more.


def canonical_context_key(context_key: str | None) -> str:
    """The one spelling of a chat's binding. Total: every input answers.

    THERE IS NOTHING LEFT TO FOLD, and that is the point. This used to rewrite
    `build:<slug>` to `agent:<slug>` -- a second spelling of the same binding,
    kept working by a translation at the read boundary. The spelling is now
    deleted everywhere that could mint one and the stored data is migrated
    (`scripts/migrate_build_context_keys.py`), so the fold has nothing to
    translate and carrying it would only invite the second spelling back.

    What remains is the empty case: an absent or blank key is the unbound chat.
    Everything else passes through untouched, because this decides SPELLING,
    never placement. Where a chat lives is `scope_tree.scope_for_context`, and
    it reads whatever comes out of here.
    """
    return (context_key or "general").strip() or "general"


# ---- folding the log into the thread document ------------------------------


def _fold(thread_id: str) -> tuple[dict, list[dict], list[int], int] | None:
    """(meta, messages, seq-of-each-message, cut-seq) or None if the thread is gone.

    The cut's MODE rides on `_fold.last_mode` rather than in the tuple, because
    every caller of this destructures four values and only `_document` needs the
    fifth. See `_document`: the divider the transcript draws is derived from the
    cut this function RESOLVED, never from the raw boundary events, so a
    boundary this function decided to ignore cannot draw a divider claiming
    something that is not happening.

    A straight replay of the line the conversation is CURRENTLY on: every
    `message` event in seq order, nothing rewritten and nothing moved. The log
    has no revision event, so this cannot do anything else, which is the property
    the whole rebuild is for.

    "Currently on" is the only thing branching changes here, and it had to be
    added rather than being free. Going back to an earlier point (ADR 0020) does
    not delete the line it left; the abandoned tail stays in the log, because the
    log is append-only and that is the point of it. The SCREEN has filtered that
    tail out since branching shipped (`chat_branches.keep_visible` on the sync
    endpoint) and this function did not, so after a fork the operator and the
    model were reading different conversations: he saw the line he had gone back
    to, and the prompt still carried everything he had branched away from,
    including the exchange he went back to get rid of. That is the failure the
    feature exists to prevent, arriving through the one path nobody was looking
    at. `hidden_event_ids_sync` was written for this caller and had never been
    called by anything.

    None from the filter means "show everything", which covers both a thread that
    was never branched (almost all of them) and any failure to ask. Both mean the
    file's own order is the whole truth, which is what this did before.

    Committed messages only. A turn in flight has nothing here — see `_live_view`,
    which `_document` appends as a trailing view. Keeping it out of the fold is
    what keeps `seqs` a list of real seqs, so the context boundary still lands on
    a message that actually exists.
    """
    meta = chat_log.read_meta(thread_id)
    if meta is None:
        return None
    # THE DRAFT IS MERGED IN HERE, once, so everything downstream keeps reading
    # `meta["live_message"]` exactly as it always did. The running turn's draft
    # moved out of meta.json into live.json (chat_log.write_live_sync explains
    # why); this is the one seam that has to know, and `_live_view`, `_document`
    # and every caller of `get_thread` are unchanged because of it.
    if meta.get("active_turn_id"):
        merged = chat_log.merge_live(meta.get("live_message"), chat_log.read_live(thread_id))
        if merged is not None:
            meta = {**meta, "live_message": merged}
    messages: list[dict] = []
    seqs: list[int] = []
    # THE CUT IS DERIVED HERE, FROM THE EVENTS, and only a `clear` makes one.
    #
    # It is read off the log rather than trusted from `meta.boundary_seq` for one
    # reason: the log records WHY a boundary exists and meta does not. There used
    # to be a second kind, written by server-side folding, which replaced real
    # turns with a summary. Folding is gone (2026-08-12), and the few threads
    # carrying one of its boundaries would otherwise keep hiding messages behind
    # a summary that no longer exists. Deriving from `mode == "clear"` makes
    # those inert and hands those threads their history back, with no migration
    # and no field to backfill: the condition IS the migration.
    #
    # Last clear wins, and it costs nothing: this loop already reads every event.
    #
    # `at_seq` on the event is new. A boundary used to say only THAT it happened,
    # never where, so the position lived in `meta.boundary_seq` alone and the log
    # could not answer the question by itself. Clears written before this fall
    # back to that field, which is the one piece of legacy handling here.
    # Two facts, not one: WHETHER a clear happened, and WHERE it cut. A clear
    # written before `at_seq` existed has the first and not the second, and
    # collapsing them into a single nullable made it indistinguishable from a
    # thread that was never cleared at all.
    # A `compact` boundary counts too, and ONLY when a summary is actually
    # stored. Folding was removed on 2026-08-12 and restored the same day (the
    # summarizer had been failing with E2BIG on every thread long enough to need
    # it), which leaves threads carrying a boundary from the broken era with
    # nothing behind it. The condition IS the migration: a compact boundary with
    # no summary hides nothing, so those threads keep their full history in the
    # prompt rather than losing it to a summary that was never written.
    saw_cut = False
    cut_at: int | None = None
    cut_mode = ""
    cut_summary = ""
    has_summary = bool((meta.get("summary") or "").strip())
    # Which events belong to a line this conversation is no longer on. See the
    # docstring. `turn` and `title` events never become rows, and are not read
    # here anyway; `message` and `boundary` both do, so both are filtered, which
    # is what makes a `clear` performed only on an abandoned line stop cutting
    # the line the operator went back to.
    from services import chat_branches
    hidden = chat_branches.hidden_event_ids_sync(thread_id)
    # Counted BEFORE the branch filter, and that is the whole point of it: it
    # separates "this thread has no boundary at all" from "this thread's boundary
    # is on a line we left". The first is a thread from before boundary events
    # existed, whose `meta.summary` is the only record of its fold and must keep
    # working. The second is a fork behind a compaction, where the summary must
    # go with the boundary. Without this they are the same state.
    any_boundary = False
    for ev in chat_log.read_events(thread_id):
        if ev.get("kind") == "boundary":
            any_boundary = True
        if hidden and ev.get("id") in hidden:
            continue
        if ev.get("kind") == "boundary":
            mode = ev.get("mode")
            # The event's own summary first; `meta.summary` is the fallback for
            # boundaries written before the event carried one. A compact boundary
            # still counts only when there IS a summary behind it, which is what
            # keeps the boundaries from the broken-summarizer hours inert. Note
            # the key is `fold_summary`: the retired folder's own `summary` key
            # is ignored here on purpose, and that is what keeps it retired.
            ev_summary = (ev.get("fold_summary") or "").strip()
            if mode == "clear" or (mode == "compact" and (ev_summary or has_summary)):
                saw_cut = True
                cut_at = ev.get("at_seq")
                cut_mode = mode
                # A `clear` REPLACES nothing, so it carries no summary: reaching
                # a clear after a compact must drop the earlier fold's summary,
                # not inherit it.
                cut_summary = (ev_summary or meta.get("summary") or "") if mode == "compact" else ""
            continue
        if ev.get("kind") != "message":
            continue
        msg = {
            "role": ev.get("role") or "user",
            "content": ev.get("content") or "",
            "ts": ev.get("ts"),
        }
        if ev.get("meta"):
            msg["meta"] = dict(ev["meta"])
        messages.append(msg)
        seqs.append(ev["seq"])
    # A cut with no `at_seq` is one written before the event carried it.
    # No cut at all means no cut, whatever `meta.boundary_seq` still says.
    if not saw_cut:
        resolved = 0
    elif cut_at:
        resolved = int(cut_at)
    else:
        resolved = int(meta.get("boundary_seq") or 0)
    _fold.last_mode = cut_mode if saw_cut else ""      # type: ignore[attr-defined]
    # WHICH SUMMARY IS IN FORCE ON THIS LINE, which is not the same question as
    # "does this thread have a summary". A fork to before a compaction leaves the
    # boundary off the path, so nothing is resolved here and the prompt goes back
    # to the full earlier history — the behaviour going back past a compact reads
    # as having. See _document, which is the only reader.
    if saw_cut:
        resolved_summary = cut_summary
    elif not any_boundary and has_summary:
        # Pre-boundary-event thread: `meta.summary` is the only record its fold
        # left, so it stays in force. Never reached once a boundary exists.
        resolved_summary = meta.get("summary") or ""
    else:
        resolved_summary = ""
    _fold.last_summary = resolved_summary               # type: ignore[attr-defined]
    return meta, messages, seqs, resolved


def _live_view(meta: dict) -> dict | None:
    """The in-flight turn rendered as a trailing assistant message, or None.

    THE TURN IS NOT IN THE LOG. While a turn is in flight the thread holds no
    assistant message for it; `meta.live_message` carries the throttled snapshot
    of what it has produced so far, and this is that snapshot shaped as a
    message so a reload still renders the turn in progress. Built fresh on every
    read, never written back.

    That is the ordering fix. The reply used to claim its slot when the turn
    STARTED, so a message sent at minute twelve of a twenty-minute turn was filed
    below the slot and the finished reply landed ABOVE it — reading as if the
    operator had been ignored. Now the snapshot is always last while the turn
    runs, and the committed reply is appended at the tail when it finishes: a
    message the turn was handed sits above the reply that answered it, and one
    that arrived too late to be delivered sits below.

    `meta.streaming` and `meta.turn_id` are on it because that is how a client
    knows to attach to the SSE stream for this turn."""
    if not meta.get("active_turn_id"):
        return None
    live = meta.get("live_message")
    if not isinstance(live, dict):
        return None
    m = dict(live.get("meta") or {})
    m["streaming"] = True
    m["turn_id"] = meta["active_turn_id"]
    m.setdefault("segments", [])
    return {
        "role": "assistant",
        "content": live.get("content") or "",
        "ts": live.get("ts"),
        "meta": m,
    }


def _cleared_through(seqs: list[int], cut_seq: int) -> int:
    """How many messages are behind the cut.

    The cut is a SEQ, which nothing can shift; this is the count of messages at
    or before it, which is the index-into-the-array that callers read.

    Named for what it is now. It used to be `compacted_through`, from a time when
    a fold could also move the boundary; folding is gone and `/clear` is the only
    thing that cuts, so the compaction vocabulary was describing a mechanism that
    no longer exists."""
    if cut_seq <= 0:
        return 0
    return bisect.bisect_right(seqs, cut_seq)


def _document(meta: dict, messages: list[dict], seqs: list[int], cut_seq: int = 0) -> dict:
    """The thread dict callers have always been handed."""
    t = {k: v for k, v in meta.items() if k not in _INTERNAL_META}
    # Back-fill the entity-model fields for legacy threads (P1 / C11).
    # Persisted-vs-derived is not observable to callers: an unset stored field
    # reads as the derived value.
    ck = t.get("context_key", "general")
    # SCOPE IS DERIVED, NOT STORED. It used to be back-filled only when the
    # stored field was absent, which made it stale from the moment anything
    # moved: `rescope_thread` writes `context_key` and nothing else, so a chat
    # rebound from `general` to `project:<slug>` went on reporting `workshop`
    # for the rest of its life. Reads that trusted the stored value are what
    # carried `app:development` (see scope_tree.scope_for_context) onto 27
    # threads and out to `Memory.tsx`, which filters the chat list against
    # `reach()` and can never match an address `reach()` does not mint.
    #
    # There is now one source of truth for where a chat lives, and it is
    # `context_key`. A stored copy is written by nobody and trusted by nobody.
    t["scope"] = scope_tree.scope_for_context(ck)
    # CLAMPED, not just defaulted. `_clamp_mode` is what turns a stored legacy
    # `orchestrated` into `auto` on the way out, so no thread log has to be
    # rewritten for the enum to have collapsed.
    t["mode"] = _clamp_mode(t.get("mode"))
    # CLAMPED for the same reason `mode` is: a thread whose stored arming is
    # absent or unreadable reads as `off`. `role` is deliberately NOT touched --
    # it is nullable, null is a valid value, and back-filling it with a default
    # would put a definition on a thread that never chose one.
    t["armed"] = _clamp_armed(t.get("armed"))
    # THE SUMMARY IS THE RESOLVED BOUNDARY'S, OR THERE IS NONE. `meta.summary`
    # is thread-global and reaches here through the copy above; on its own that
    # made going back past a compaction incoherent, because the cut correctly
    # disappeared with the boundary while the summary stayed. The prompt then
    # carried a summary of messages it was ALSO sending verbatim, including the
    # tail the operator had just branched away from. `_fold` decides which
    # boundary is on this line, so it decides the summary too.
    resolved_summary = getattr(_fold, "last_summary", "") or ""
    if resolved_summary:
        t["summary"] = resolved_summary
    else:
        t.pop("summary", None)
    ct = _cleared_through(seqs, cut_seq)
    if ct:
        t["cleared_through"] = ct
        # WHICH KIND OF CUT, from the one that was actually resolved. The client
        # draws its divider from this rather than from the boundary events, so a
        # boundary `_fold` decided to ignore (a `compact` from the hours when
        # folding was broken, with no summary behind it) cannot draw a divider
        # announcing a fold that is not in any prompt.
        t["cut_mode"] = getattr(_fold, "last_mode", "") or "clear"
    live = _live_view(meta)
    t["messages"] = ([*messages, live] if live is not None else messages)
    return t


def _patch(thread_id: str, **fields: object) -> dict | None:
    """Write named meta fields (None removes one) and return the fresh thread."""
    meta = chat_log.read_meta(thread_id)
    if meta is None:
        return None
    # NOTHING REWRITES A FIELD ON THE WAY THROUGH. `_promotion_patch` used to sit
    # here and re-key any agent chat on every single patch, so a thread could not
    # hold an `agent:` binding even if something managed to create one.
    if "context_key" in fields and fields["context_key"] is not None:
        fields = {**fields, "context_key": canonical_context_key(str(fields["context_key"]))}
    chat_log.patch_meta_sync(thread_id, **fields)
    return get_thread(thread_id)


def _ensure_migrated(thread_id: str) -> None:
    """Fold a not-yet-migrated legacy document in, on first touch. Startup does
    this for every thread; this covers one that appeared since."""
    try:
        if chat_log.exists(thread_id):
            return
        legacy = chat_log.legacy_path(thread_id)
    except ValueError:
        return
    if legacy.exists():
        chat_log.migrate_owner()


# ---- thread lifecycle ------------------------------------------------------


def create_thread(
    context_key: str = "general",
    title: str = "",
    agent_id: str | None = None,
    model: str | None = None,
    mode: str | None = None,
) -> dict:
    tid = uuid.uuid4().hex
    # Entity-model 2026-07-18 §7 fields (C11, P1):
    # - mode: direct (the default) or auto. See CHAT_MODES.
    #
    # NO `scope` AND NO `scope_locked`. The Scope a chat lives in is derived
    # from its `context_key` on every read (see `_document`), so an explicit
    # scope at creation could only ever be a second answer to a question that
    # already has one -- which is how `app:development` got stamped onto 27
    # threads. `scope_locked` was the flag that made such a divergence
    # permanent; nothing could set it and nothing but `_promotion_patch` read
    # it, so it is gone rather than wired.
    #
    # NO `participants` EITHER. It was a list of `agent:<slug>` strings with no
    # reader anywhere in the system: an agent in a chat is now the chat's
    # `context_key` (it answers the turns) or an @mention (it answers one turn),
    # and both of those are things that actually happen. See the note where
    # `_promotion_patch` used to be.
    #
    # THE CONTEXT KEY IS KEPT. Whatever the caller asked for is what this thread
    # is bound to, through creation and every later patch; the only thing
    # `canonical_context_key` decides is what an ABSENT key means.
    now = _now_iso()
    meta = {
        "id": tid,
        "context_key": canonical_context_key(context_key),
        # An explicit mode wins; otherwise the operator's default for new chats.
        "mode": _clamp_mode(mode) if mode is not None else get_default_mode(),
        "agent_id": agent_id,
        # Per-thread model preference (full model id) chosen via the composer's
        # model picker. None = fall back to the provider default at call time.
        "model": model,
        # Per-thread effort preference, persisted the same way the model is: it
        # arrives on a turn and sticks. None = the model's own default. A level
        # the newly-picked model doesn't support resolves back to None rather
        # than riding along invisibly (services/model_caps.normalize_effort).
        "effort": None,
        # How much this chat asks before acting. None = the platform default
        # (bypassPermissions), which is what every chat has always run at.
        "permission_mode": None,
        # Which MCP servers this chat's turns may reach. None = ALL of them,
        # which is both the default and what every chat did before the control
        # existed; a list is an explicit subset. Never [] by default — a chat
        # nobody has configured must not silently lose its tools. See
        # set_thread_mcp_servers.
        "mcp_servers_enabled": None,
        "title": title or "New chat",
        "created_at": now,
        "updated_at": now,
    }
    chat_log.create_thread(tid, meta)
    return get_thread(tid)


def get_thread(thread_id: str) -> dict | None:
    _ensure_migrated(thread_id)
    try:
        folded = _fold(thread_id)
    except ValueError:
        return None
    if folded is None:
        return None
    return _document(*folded)


def delete_thread(thread_id: str) -> bool:
    try:
        legacy = chat_log.legacy_path(thread_id)
    except ValueError:
        return False
    gone = chat_log.delete_thread(thread_id)
    if legacy.exists():
        legacy.unlink()
        gone = True
    _evict_summary_cache(thread_id)
    if gone:
        try:
            from services import backlinks
            backlinks.remove_sources_with_prefix(f"workshop:chat/{thread_id}#")
        except Exception:
            log.debug("backlink index cleanup skipped for deleted thread %s", thread_id,
                      exc_info=True)
    return gone


# ---- archive ---------------------------------------------------------------
#
# ARCHIVING IS THE GESTURE, DELETING IS THE EXCEPTION. Closing a conversation is
# something the operator does constantly and destroying one is something they do
# rarely, so the constant gesture must be the reversible one. `archived_at` is
# the whole model: a timestamp on the thread's meta, absent when it is live. It
# hides the thread from every list except the archive's own, and nothing else
# about the thread changes, which is what makes restoring it a field removal
# rather than an undo log.
#
# The hard delete below it is unchanged and still the only destructive path.
# What changed is who can reach it: only the archive view, one thread at a time.


def archive_thread(thread_id: str) -> dict | None:
    """Take the thread out of the lists. Returns the archived thread, or None if
    there is no such thread. Archiving an already-archived thread re-stamps it,
    which is harmless and keeps the call idempotent."""
    try:
        return _patch(thread_id, archived_at=_now_iso())
    except ValueError:
        # An id that is not even shaped like one. `delete_thread` reports the
        # same way: NOT THERE is the honest answer, and it is the difference
        # between a 404 and a 500 at the route above.
        return None


def restore_thread(thread_id: str) -> dict | None:
    """Put it back. `_patch` removes a field set to None, so this is the exact
    inverse of archiving and leaves no trace of the round trip."""
    try:
        return _patch(thread_id, archived_at=None)
    except ValueError:
        return None


def _summary_row(thread_id: str) -> dict | None:
    """The one sidebar row for `thread_id`, straight from a fresh fold.

    Exactly the row `list_threads` has always built for a thread, factored out
    so `_cached_summary_row` has something to cache. No filtering here
    (context_key / archived) — that stays in `list_threads`, over whatever row
    it got, cached or not.
    """
    folded = _fold(thread_id)
    if folded is None:
        return None
    meta, msgs, _seqs, _cut = folded
    if "id" not in meta:
        return None
    title = meta.get("title", "New chat")
    # When the OPERATOR last spoke in this chat. This is what orders the
    # list, not `updated_at`: a turn that finishes, an agent that reports
    # back, or a background write all bump updated_at, so a chat the
    # operator has not touched in days could sit above the one they were
    # just in. The question the list answers is "where was I", and only a
    # user message answers it. Threads with no user message yet fall back to
    # when they were created, so a brand-new chat still sorts to the top.
    last_user_at = next(
        (m.get("ts") for m in reversed(msgs)
         if m.get("role") == "user" and m.get("ts")),
        None,
    )
    # Until auto-naming runs (or if it never can), show the first-user-message
    # seed instead of a bare "New chat".
    if title in (None, "", "New chat") and meta.get("_seed_title"):
        title = meta["_seed_title"]
    last = msgs[-1] if msgs else None
    ck = meta.get("context_key", "general")
    stored_ctx = meta.get("context_tokens")
    ctx_tokens = (int(stored_ctx)
                  if isinstance(stored_ctx, (int, float)) and stored_ctx > 0 else 0)
    return {
        "id": meta["id"],
        "context_key": ck,
        # Entity-model 2026-07-18 §7 fields (C11, P1). `scope` is DERIVED, never
        # stored -- see `_document`.
        "scope": scope_tree.scope_for_context(ck),
        "mode": _clamp_mode(meta.get("mode")),
        # WHO ANSWERS THIS CHAT, when that is an agent. An `agent:<slug>` chat is
        # answered by that agent, so the key IS the answer; `agent_id` carries
        # the historical case, a chat that was with an agent whose scope no
        # longer exists (see the P5 casualties note in
        # scripts/migrate_chat_participants.py). One field, so the list can
        # filter, tag and search on one thing.
        "agent_id": meta.get("agent_id"),
        "model": meta.get("model"),
        "title": title,
        "created_at": meta.get("created_at"),
        "updated_at": meta.get("updated_at"),
        "archived_at": meta.get("archived_at"),
        "last_user_at": last_user_at,
        "message_count": len(msgs),
        "last_message": ((last.get("content") or "")[:300] if last else ""),
        # HOW FULL THE WINDOW IS, the same pair `GET /threads/{id}/context`
        # prefers: the size the model itself reported on the last turn, over the
        # one table (`services/context_window`). Null when no turn has completed
        # yet, which is the honest answer -- an estimate belongs to the composer,
        # which has the draft, not to a list row.
        "context_tokens": ctx_tokens or None,
        "context_window": (context_window.window_for(meta.get("model"), ctx_tokens)
                           if ctx_tokens else None),
        "branch_slug": meta.get("branch_slug"),
        # WHAT ADVANCES THIS THREAD WITHOUT A PERSON, and which definition it
        # answers as. Two independent fields: an armed thread with no role
        # continues with its own context, and a thread carrying a role may
        # never be armed at all. `armed` is clamped because "unreadable" must
        # resolve to `off`; `role` is NOT defaulted, because null is a real
        # value here and a coerced one would invent a definition nobody picked.
        "armed": _clamp_armed(meta.get("armed")),
        "role": meta.get("role"),
    }


# ---- sidebar summary cache --------------------------------------------------
#
# Profiled (cProfile, real store, 67 threads / 22MB on disk): list_threads was
# ~110-260ms, of which the bulk was `_fold` doing thousands of `json.loads` —
# once per thread, on the thread's ENTIRE event log — just to produce one
# sidebar row. `list_threads` is called on every page load AND after every
# turn (`refreshThreads()`), so that cost is paid constantly for a result
# (title, timestamps, a message count, a 120-char preview) that ninety-nine
# reads out of a hundred haven't changed since the last call.
#
# So: cache the row, keyed on a fingerprint that is cheap to prove wrong.
# `_fold_fingerprint` is stat()-only (mtime_ns + size, never a read) over every
# file `_fold` actually reads: `log.jsonl`, `log.1.jsonl` (rotation may or may
# not have one), and `meta.json` (title/scope/archived_at/mode live there, not
# in the log). A hit skips `_fold` entirely; a miss folds once and stores the
# result under the fingerprint it folded at, so the NEXT call can trust it.
#
# The fingerprint covers every writer that can change a row:
#   - a new message or boundary event: appended to `log.jsonl`, so its
#     (mtime_ns, size) changes.
#   - rotation (`chat_log.maybe_rotate_sync`): moves the head of `log.jsonl`
#     into `log.1.jsonl`, rewriting BOTH files, so both stat tuples change —
#     this is why the archive half is in the fingerprint too, not just the
#     live half.
#   - title / scope / mode / archived_at / anything else in meta.json: every
#     writer of meta.json goes through `chat_log._write_json_atomic` (see
#     `patch_meta_sync`, `_patch`, `create_thread`) with no other writer of
#     that path, so any such change moves meta.json's mtime.
#   - a branch fork or switch (`chat_branches.fork` / `.switch`, via
#     `routes/chat.py`): these mutate Postgres, not either log file, but both
#     routes call `chat_store.resolve_cut()` right after, which always calls
#     `chat_log.patch_meta_sync(...)` — unconditionally, even when the cut it
#     resolves to is unchanged. That write still moves meta.json's mtime, so
#     the row this cache would otherwise keep serving (built from the
#     now-hidden or now-revealed messages `_fold`'s branch filter selects) is
#     invalidated by the same action that could have changed it. A branch
#     RENAME does not call `resolve_cut` and is not covered, but a rename
#     changes no message's visibility, so no row depends on it.
#
# Correctness beats speed here: if the fingerprint can't be computed for any
# reason, `_fold_fingerprint` returns None and the caller always folds. A
# stale sidebar row (wrong order, wrong preview, a thread that looks
# unchanged when it isn't) is a worse outcome than a slow one, since this is
# what the operator navigates by.
#
# Process-local dict, keyed by (owner_id, thread_id) since threads are
# per-owner directories and a background loop can pin a different owner
# mid-process (see `services.ownership`). Doesn't need to survive a restart.
# Bounded by pruning dead threads out on every `list_threads` call, so a
# deleted thread's entry cannot linger, and by `_evict_summary_cache` on
# explicit delete so it doesn't even wait for the next list.
_SUMMARY_CACHE: dict[tuple[int, str], tuple[tuple, dict]] = {}


def _stat_sig(path: Path) -> tuple[int, int] | None:
    """(mtime_ns, size), or None if the file does not exist. Only None here
    means "absent" — any other stat failure propagates, which is what makes
    `_fold_fingerprint` catch it and report "unusable" instead of silently
    treating a permissions error the same as a missing file."""
    try:
        st = path.stat()
    except FileNotFoundError:
        return None
    return (st.st_mtime_ns, st.st_size)


def _fold_fingerprint(thread_id: str) -> tuple | None:
    """A cheap proxy for "would `_fold(thread_id)` read anything different
    than last time". None means it could not be computed — the caller must
    fold rather than trust a cache entry it cannot validate."""
    try:
        return (
            _stat_sig(chat_log.log_path(thread_id)),
            _stat_sig(chat_log.archive_path(thread_id)),
            _stat_sig(chat_log.meta_path(thread_id)),
        )
    except Exception:
        log.debug("summary cache: could not fingerprint %s, folding anyway",
                   thread_id, exc_info=True)
        return None


def _cached_summary_row(owner_id: int, thread_id: str) -> dict | None:
    fp = _fold_fingerprint(thread_id)
    if fp is not None:
        hit = _SUMMARY_CACHE.get((owner_id, thread_id))
        if hit is not None and hit[0] == fp:
            return dict(hit[1])
    row = _summary_row(thread_id)
    key = (owner_id, thread_id)
    if fp is not None and row is not None:
        _SUMMARY_CACHE[key] = (fp, row)
    else:
        # Either unfingerprintable (never cache what can't be invalidated) or
        # the thread is gone (nothing to serve on the next hit either).
        _SUMMARY_CACHE.pop(key, None)
    return dict(row) if row is not None else None


def _prune_summary_cache(owner_id: int, live_thread_ids: set[str]) -> None:
    dead = [k for k in _SUMMARY_CACHE
            if k[0] == owner_id and k[1] not in live_thread_ids]
    for k in dead:
        del _SUMMARY_CACHE[k]


def _evict_summary_cache(thread_id: str) -> None:
    """Best-effort: called from `delete_thread` so a removed thread's cache
    entry doesn't wait for the next `list_threads` prune. Never raises —
    `delete_thread` must still report success even if the owner can't be
    resolved (e.g. a script that deleted without pinning one)."""
    try:
        owner_id = ownership.current_owner()
    except Exception:
        return
    _SUMMARY_CACHE.pop((owner_id, thread_id), None)


def stamp_running(rows: list[dict]) -> list[dict]:
    """Mark the rows whose thread has a turn in flight. In place, returns rows.

    THE FACT ALREADY EXISTED and the open chat already renders it: a turn holds
    `meta.active_turn_id`, which `chat_log.active_turn` reads and the per-thread
    sync payload carries. What it lacked was a place on the LIST row, so any
    surface showing many chats at once had to make one call per chat to learn
    something each of those calls reads out of one small file.

    STAMPED HERE, over whichever source produced the rows, rather than inside
    the two row builders. There are two list sources (this module's fold and
    `chat_list_pg`'s Postgres query) and they are required to agree field for
    field; a fact derived twice is a fact that can disagree. This is also why it
    is not mirrored into Postgres: `active_turn_id` is per-turn bookkeeping that
    `patch_meta_sync` rewrites a few hundred times WHILE A TURN RUNS, and
    `chat_log._LIST_RELEVANT_META_FIELDS` deliberately keeps that off the
    database round trip.

    `_read_meta_raw`, not `read_meta`: the reconciled `last_seq` costs a read of
    the log's tail and nothing here wants it. Measured over the real store
    2026-08-26: 134 threads in 2.8ms.
    """
    for row in rows:
        tid = row.get("id")
        meta = None
        if tid:
            try:
                meta = chat_log._read_meta_raw(tid)
            except (ValueError, OSError):
                # A row whose id does not address a readable thread. NEVER a
                # raise: this is a display detail on a list, and the whole list
                # failing because one row cannot answer it is a far worse
                # outcome than that row saying "not running", which is also the
                # only answer that is safe to give.
                meta = None
        row["running"] = bool((meta or {}).get("active_turn_id"))
    return rows


def list_threads(context_key: str | None = None,
                 archived: bool = False) -> list[dict]:
    """Summaries (no message bodies), newest first.

    `archived` is a SWITCH BETWEEN TWO LISTS, not a widening: False is every
    live thread, True is every archived one. There is deliberately no "both",
    because every caller is either showing the operator their conversations or
    showing them the archive, and a list that mixed the two would need a marker
    on each row to be readable at all.
    """
    if not _chat_dir().exists():
        return []
    chat_log.migrate_owner()
    owner_id = ownership.current_owner()
    live_ids = set(chat_log.list_thread_ids())
    _prune_summary_cache(owner_id, live_ids)
    out: list[dict] = []
    for tid in live_ids:
        row = _cached_summary_row(owner_id, tid)
        if row is None:
            continue
        if context_key and row.get("context_key") != context_key:
            continue
        if bool(row.get("archived_at")) != archived:
            continue
        out.append(row)
    # The archive answers a different question than the chat list, so it takes a
    # different order: most recently archived first, because what you came to
    # find is almost always the one you just put there (or just regretted).
    if archived:
        out.sort(key=lambda s: s.get("archived_at") or "", reverse=True)
    else:
        out.sort(key=_recency_key, reverse=True)
    return out


def _recency_key(summary: dict) -> str:
    """Order by when the operator last spoke, falling back to creation. Never
    updated_at — see the note on last_user_at."""
    return summary.get("last_user_at") or summary.get("created_at") or ""


# ---- mode ------------------------------------------------------------------


def set_thread_mode(thread_id: str, mode: str) -> dict | None:
    """Set the per-thread interaction mode. `mode` must be one of CHAT_MODES;
    a retired value maps forward (`orchestrated` -> `auto`) and anything else
    clamps to DEFAULT_CHAT_MODE."""
    return _patch(thread_id, mode=_clamp_mode(mode))


def set_thread_model(thread_id: str, model: str | None) -> dict | None:
    """Persist the per-thread model preference (full model id, or None to clear).
    Returns the updated thread, or None if the thread is missing."""
    if model is None:
        return _patch(thread_id, model=None)
    return _patch(thread_id, model=model)


def set_thread_permission_mode(thread_id: str, mode: str | None) -> dict | None:
    """Persist how much this chat asks before acting (None = platform default).

    Plan mode is the one value a caller should think twice about storing: it is a
    ONE-TURN posture (routes.chat clears it once a plan is approved), so writing
    it here is what makes the picker show plan until that happens, not a claim
    that the chat is permanently in plan mode.

    ENTERING PLAN RECORDS WHERE THE CHAT CAME FROM. `pre_plan_permission_mode`
    is written on the transition into plan and removed on the transition out, so
    approving a plan can put the chat back where it was instead of on one fixed
    landing mode. It stores the mode the chat was actually RUNNING at, resolved
    through the operator's default — storing the raw stored value would make
    "was following the default" and "recorded nothing" the same absent field, and
    a chat that had never touched the control would land on the fallback instead
    of on its own posture.

    Every writer goes through here (the composer, and the CLI's own setMode
    suggestion), so there is one transition to get right rather than one per
    caller."""
    from services import model_caps

    # read_meta, not get_thread: this needs one meta field, and get_thread folds
    # the whole event log to produce it.
    current = (chat_log.read_meta(thread_id) or {}).get("permission_mode")
    fields: dict[str, object] = {"permission_mode": mode}
    if mode == "plan":
        if current != "plan":
            fields["pre_plan_permission_mode"] = (
                current or get_default_permission_mode()
                or model_caps.DEFAULT_PERMISSION_MODE)
    else:
        # Leaving plan (or a plain mode change that was never in plan): the note
        # has been spent. None removes the field — see chat_log.patch_meta_sync.
        fields["pre_plan_permission_mode"] = None
    return _patch(thread_id, **fields)


def normalize_mcp_selection(names: object, known: list[str] | None = None) -> list[str] | None:
    """The stored form of a per-chat MCP selection, or None for "all servers".

    Pure, and the ONE place the "all" collapse happens, so the store and the
    route cannot disagree about what a full selection means.

    A selection naming every server the machine currently has is stored as None,
    not as that list. The two are not the same thing later: a frozen list stops
    at the servers that existed the day it was saved, so a server added next
    month would arrive switched off, in a chat whose UI says everything is on.
    "All" has to stay a statement about the set, not a snapshot of it.

    An empty list is preserved as an empty list. "No MCP servers at all" is a
    real, reachable choice and must survive a round trip.
    """
    if names is None:
        return None
    if not isinstance(names, (list, tuple, set)):
        raise ValueError("mcp servers must be a list of names, or null for all")
    cleaned = [str(n) for n in names if str(n).strip()]
    # De-duped, order-stable: the list is a set with a stable rendering.
    out = list(dict.fromkeys(cleaned))
    if known is not None and set(out) >= set(known):
        return None
    return out


def set_thread_mcp_servers(thread_id: str, names: list[str] | None,
                           known: list[str] | None = None) -> dict | None:
    """Persist which MCP servers this chat's turns may reach.

    None (or a selection covering every known server) clears the field, and a
    cleared field is what "all servers" IS — the same shape a chat that never
    touched the control has. That is deliberate: there is exactly one
    representation of the default, so no chat can be in a state that looks
    configured but behaves like the default or vice versa.

    Stored the same way the model, effort and permission mode are: a field on
    the thread's meta, written through _patch, read back by the next turn.
    """
    value = normalize_mcp_selection(names, known)
    # _patch removes a field set to None (chat_log.patch_meta_sync), which is
    # how "back to all servers" erases the selection rather than recording an
    # empty one — an empty LIST means the opposite and is stored as `[]`.
    return _patch(thread_id, mcp_servers_enabled=value)


def get_thread_mcp_servers(thread_id: str) -> list[str] | None:
    """This chat's MCP selection, or None for all. Reads meta directly rather
    than folding the whole event log: a turn asks this on every send."""
    meta = chat_log.read_meta(thread_id) or {}
    value = meta.get("mcp_servers_enabled")
    return list(value) if isinstance(value, list) else None


class _Unset:
    """Sentinel for "this field was not in the request".

    `set_thread_arming` needs to tell "leave the role alone" apart from "clear
    the role", and `None` cannot mean both: null is a real, chosen value for
    `role`, so it has to reach `_patch` when it is sent and never when it is
    not.
    """

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "UNSET"


UNSET = _Unset()


def set_thread_arming(thread_id: str,
                      armed: str | _Unset = UNSET,
                      role: str | None | _Unset = UNSET) -> dict | None:
    """Set what advances this thread without a person, and which definition it
    answers as. Either field alone, or both.

    ARMED AND ROLE ARE INDEPENDENT and neither implies the other: arming a
    thread does not give it a role, and giving it a role does not arm it. That
    is the whole point of them being two fields, so this function never derives
    one from the other.

    `armed` is clamped to THREAD_ARMED (an unreadable value is `off`). `role`
    is free text naming a definition and is stored verbatim after trimming;
    `None` CLEARS it, which `_patch` does by removing the key, so the thread
    reads back with no role rather than with an empty string. A blank string is
    treated as a clear for the same reason -- "" is not a definition anybody
    picked.
    """
    fields: dict[str, object] = {}
    if not isinstance(armed, _Unset):
        fields["armed"] = _clamp_armed(armed)
    if not isinstance(role, _Unset):
        fields["role"] = (role.strip() or None) if isinstance(role, str) else None
    if not fields:
        # Nothing asked for. Hand back the thread unchanged rather than writing
        # meta (and bumping `updated_at`) for a request that said nothing.
        return get_thread(thread_id)
    return _patch(thread_id, **fields)


def set_thread_branch(thread_id: str, branch_slug: str | None) -> dict | None:
    """Pin this conversation to a branch of its project's tree (None unpins).

    THE PIN, NOT THE LEASE. This says where this chat's turns run; whether it
    may WRITE there is the worktree lease, taken per turn by
    `services/chat_worktree.py`, and enforced by the mount. Storing one where the
    other belongs is the mistake section 4 of
    `docs/platform-redesign/workspace-ownership-and-leases-2026-08-22.md`
    exists to prevent, so this stores a name and grants nothing.

    Validation belongs to the caller, on the same terms as `set_thread_model`:
    whether a branch exists is a question for the branches layer, which the
    route asks before it gets here, and asking it twice from two modules is two
    ideas of what a valid branch is.
    """
    return _patch(thread_id, branch_slug=branch_slug)


def set_thread_effort(thread_id: str, effort: str | None) -> dict | None:
    """Persist the per-thread effort preference (a level name, or None to fall
    back to the model default). Returns the updated thread, or None if missing.

    Validation belongs to the caller: this stores what it is handed, exactly as
    set_thread_model does, so a level is checked once against the resolved model
    (model_caps.normalize_effort) rather than twice against different ideas of
    which model is in play."""
    return _patch(thread_id, effort=effort)


# ---- messages --------------------------------------------------------------


# HOW MUCH OF THE FIRST MESSAGE STANDS IN FOR A NAME.
#
# It was 60 characters, hard, mid-word, with no ellipsis, and every list that
# renders a title already clips its own overflow: `.ws-chat-row__title` is
# `white-space: nowrap; overflow: hidden; text-overflow: ellipsis`. So the row
# had six hundred pixels of empty space to the right of a sentence that had been
# chopped at "...write one sentence abo". Reported by the operator: "the chat
# text/message preview itself in the chat card in chats is still truncated
# unnecessarily. can use its full row if its long enough."
#
# The cap that is left is a STORAGE bound, not a layout one -- a seed title is
# read into a list row and must not carry a paragraph -- and it is wide enough
# that the surface, which knows how much room it has, is the thing that decides
# where the text ends. When it does bite, it bites at a word boundary, because a
# cut mid-word reads as a bug even with an ellipsis after it.
SEED_TITLE_MAX = 160


def seed_title_from(content: str) -> str:
    """The first line of the first message, as a stand-in title."""
    line = (content or "").strip().split("\n")[0].strip()
    if not line:
        return "New chat"
    if len(line) <= SEED_TITLE_MAX:
        return line
    cut = line[:SEED_TITLE_MAX]
    spaced = cut.rsplit(" ", 1)[0]
    # A single word longer than the cap has no boundary to fall back to; keeping
    # the hard cut is better than returning nothing.
    return (spaced if len(spaced) >= SEED_TITLE_MAX // 2 else cut).rstrip()


def append_message(thread_id: str, role: str, content: str, meta: dict | None = None,
                   client_id: str | None = None) -> dict:
    """Append one message. Idempotent on `client_id` when the caller supplies one.

    Title is left as the "New chat" seed here; the chat route generates a proper
    short title via a cheap model call after the first assistant reply (see
    routes/chat._maybe_autoname). The thread-list summary already carries a
    `last_message` snippet, so an interim snippet-title is redundant. If a thread
    somehow never gets named (model unavailable), fall back to a first-user-
    message snippet so the list never shows a wall of "New chat".

    FIRST user message only. Rewriting it on every message made the chat list
    show whatever the operator said most recently as the chat's name, which reads
    as the title being the last message rather than a subject — and it changed
    under them each turn. It is a fallback for a chat that could not be named, so
    it must be as stable as the name it stands in for."""
    m = chat_log.read_meta(thread_id)
    if m is None:
        raise KeyError(thread_id)
    ev = chat_log.append_message_sync(thread_id, role, content, client_id=client_id,
                                      meta=meta, ts=_now_iso())
    if role == "user" and not m.get("_seed_title") \
            and m.get("title") in (None, "", "New chat"):
        _patch(thread_id, _seed_title=seed_title_from(content))
    # NAME IT NOW, NOT WHEN THE TURN ENDS. The message the chat is about has just
    # arrived, so everything the namer needs is already here.
    #
    # This used to be scheduled only from build_streaming._commit_turn, which
    # meant the earliest a name could exist was the moment the first turn
    # committed, and the title inherited that turn's length. Measured over the
    # real threads: a median of 9.4 minutes from opening message to name, ranging
    # 1.9 to 38.8. Nothing was broken and nothing was random; the trigger was
    # simply attached to the wrong event, and the spread is what the operator
    # reported as "sometimes it runs, other times it doesn't".
    #
    # Imported here rather than at module scope: chat_naming reads this module,
    # so a top-level import is a cycle. The namer guards itself (still unnamed,
    # and there is something to name) and is capped, so calling it on every user
    # message costs at most _MAX_NAME_ATTEMPTS model calls per chat, ever.
    #
    # ONLY WITH A LOOP TO PUT IT ON. `chat_naming.schedule` falls back to running
    # the model call INLINE when there is no running loop, which is right for the
    # scripts that call it deliberately and quite wrong here: appending a message
    # is a store write on the hot path, and it must never turn into a blocking
    # model call because of where it was called from. Off the loop (a script, a
    # test, a migration) the name simply waits for the next turn, which is the
    # behaviour that path already had.
    if role == "user" and _loop_running():
        try:
            from services import chat_naming
            chat_naming.schedule(thread_id)
        except Exception:   # pragma: no cover — naming is best-effort, always
            pass
    return _msg_from_event(ev)


def append_message_during_turn(thread_id: str, role: str, content: str,
                               meta: dict | None = None,
                               ts: str | None = None,
                               client_id: str | None = None) -> dict:
    """File a message that arrived WHILE a turn was running.

    A turn is an INTERVAL; a message is a POINT. A message sent at minute twelve
    of a twenty-minute reply is neither before that reply nor after it, and the
    transcript has to pick one. The log picks by simply appending, and that is
    now the right answer: the reply does not exist in the log yet, so this
    message lands above it, and the reply lands above nothing when it commits.
    Delivered means "the turn read it", and the transcript says so by position.

    What position does not carry is recorded on the message:

      during_turn — the turn that was in flight when this arrived
      at_segment — how much of that reply had streamed by then

    `during_turn` is what `chat_context.arrival_notice` queries, so "which
    messages arrived while that turn was going" is a lookup rather than an
    inference from where things sit. `at_segment` is what lets the transcript
    show it inline at the point it landed.

    `ts` is when the operator actually hit enter, when the caller knows it. It
    matters here more than anywhere: these are the messages the arrival notice
    reads times off, and "sent at 05:41, 05:42 and 05:43" is the evidence that
    they were one thought said three times rather than three requests.

    `client_id` is the browser's own id for this send, and it is what makes a
    retry safe: the log already holds the message, so a resend appends nothing
    and the client reconciles its optimistic bubble against the committed event
    by id rather than by matching text.

    This stays a separate entry point from `append_message` because the CALLER
    means something different by it: not "the operator spoke", but "the operator
    spoke into a turn that was already running".
    """
    if chat_log.read_meta(thread_id) is None:
        raise KeyError(thread_id)
    ev = chat_log.append_message_sync(thread_id, role, content, meta=meta,
                                      client_id=client_id, ts=ts or _now_iso())
    return _msg_from_event(ev)


def _msg_from_event(ev: dict) -> dict:
    msg = {"role": ev.get("role"), "content": ev.get("content") or "", "ts": ev.get("ts")}
    if ev.get("meta"):
        msg["meta"] = dict(ev["meta"])
    # The log seq, when the event carries one. It is what lets a caller compare
    # a message's position against a turn's prompt floor (Turn.prompt_through_seq)
    # instead of guessing whether the prompt included it.
    if ev.get("seq") is not None:
        msg["seq"] = ev.get("seq")
    return msg


def _same_message(a: dict, b: dict) -> bool:
    """Identity, not equality: is `a` the caller's copy of stored message `b`?

    Role and timestamp are stamped when a message is appended and no mutation
    path changes either, so they identify a message across a caller's read. If
    they differ, the two lists have diverged — a message landed while the caller
    was holding its copy — and everything from that index in the caller's list is
    something it is adding, not something it is editing."""
    return a.get("role") == b.get("role") and a.get("ts") == b.get("ts")


def save_thread(thread: dict) -> dict:
    """Persist a thread dict the caller already mutated.

    COMPATIBILITY SHIM. It diffs the dict against the log and appends only what
    changed: new messages become `message` events, changed top-level fields
    become a meta patch.

    A stale snapshot can no longer delete anything. Messages that arrived after
    the caller's read are simply not in its dict, and "not in the dict" is not an
    instruction the log can express. That is why `LostUpdate` is gone: it existed
    to refuse a whole-document write, and there is no longer such a thing.

    EDITING A STORED MESSAGE IS NOT EXPRESSIBLE and is not attempted. Message
    edit is out of scope for the rebuild, no caller does it, and the one thing
    that used to (finalizing a streaming reply into a slot claimed at turn start)
    is exactly what pass B removed. A differing message is logged and skipped
    rather than silently reordering the transcript to accommodate it.
    """
    tid = thread["id"]
    folded = _fold(tid)
    if folded is None:
        raise KeyError(tid)
    meta, stored_msgs, _seqs, _cut = folded

    # The live view is a rendering of the turn in flight, not a record. A caller
    # that read the thread mid-turn is holding one; appending it would commit a
    # half-written reply that the turn is about to commit properly.
    incoming = [m for m in (thread.get("messages") or [])
                if not (m.get("meta") or {}).get("streaming")]
    i = 0
    while i < min(len(incoming), len(stored_msgs)):
        mine, theirs = incoming[i], stored_msgs[i]
        if not _same_message(mine, theirs):
            break
        if mine.get("content") != theirs.get("content") \
                or (mine.get("meta") or {}) != (theirs.get("meta") or {}):
            log.warning("thread %s: message %d differs from the log and was not "
                        "written; the log has no edit event", tid, i)
        i += 1
    for m in incoming[i:]:
        chat_log.append_message_sync(tid, m.get("role") or "user", m.get("content") or "",
                                     meta=m.get("meta"), ts=m.get("ts") or _now_iso())

    fields: dict = {}
    for k, v in thread.items():
        # `scope` sits with `messages` and `cleared_through`: things `_document`
        # DERIVES for the caller. Writing one back would persist a copy of an
        # answer that is already computed on every read, and a persisted copy is
        # what went stale after a rescope and put `app:development` on 27
        # threads. A round-trip of the document must not be able to re-create it.
        if k in ("messages", "cleared_through", "id", "scope"):
            continue
        if meta.get(k) != v:
            fields[k] = v
    for k in meta:
        if k in _INTERNAL_META or k in ("id", "created_at"):
            continue
        if k in _PRESERVED_ON_SAVE:
            # A key absent from the caller's dict is USUALLY a key added to the
            # thread after the caller's read, not a deletion the caller means:
            # a document round-trip must not be able to strip the session, the
            # summary, or the bookkeeping as a side effect of saving something
            # else. Deleting one of these goes through its own clear_* call.
            continue
        if k not in thread:
            fields[k] = None
    fields["updated_at"] = _now_iso()
    _patch(tid, **fields)

    # No boundary is written here. A cut is made by `/clear` and nothing else,
    # through `clear_thread_context`; a document round-trip must never be able to
    # move it as a side effect of saving some unrelated field.
    return get_thread(tid)


def update_thread(thread_id: str, **fields: object) -> dict | None:
    """Apply named top-level fields to a thread, onto whatever it is NOW.

    THE WAY TO WRITE AFTER AN AWAIT. A caller that reads a thread, does something
    slow, and then wants to record a result writes the fields it owns, never the
    document it is holding.

    `messages` is not writable here, deliberately: appending is the store's own
    job (`append_message`, `append_message_during_turn`).

    A field set to `None` is REMOVED, which is how a caller unsets one (e.g.
    compaction dropping the stale `context_tokens` measurement).

    THE CUT IS NOT WRITABLE HERE. It used to be: passing `compacted_through`
    moved the boundary, which is how server-side folding hid messages. A cut is
    now made in exactly one place, by `clear_thread_context`, in response to the
    operator asking for one."""
    if "messages" in fields:
        raise ValueError("update_thread cannot write `messages`; append instead")
    if "cleared_through" in fields or "compacted_through" in fields:
        raise ValueError("update_thread cannot move the context cut; use clear_thread_context")
    if chat_log.read_meta(thread_id) is None:
        return None
    if fields:
        _patch(thread_id, **fields)
    return get_thread(thread_id)


def _set_cut(thread_id: str, through: int, mode: str = "clear",
             summary: str = "") -> None:
    """Record a context cut: `clear` (drop what is behind it) or `compact`
    (replace what is behind it with the stored summary).

    The event carries `at_seq`, so the log alone says both that a cut happened
    and where. It used to say only that one happened, leaving the position in
    `meta.boundary_seq`, which meant the log could not be replayed into a thread
    without also trusting a mutable field beside it. `boundary_seq` is still
    written, as a cache for readers that have not folded the log.

    `mode` is what tells the two apart everywhere downstream: the divider the
    transcript draws, and whether `_fold` honours the cut at all (a `compact`
    boundary with no summary behind it is inert — see `_fold`).

    THE SUMMARY RIDES ON THE EVENT, not only in meta, and that is what makes a
    fold survive branching. `meta.summary` is one field for the whole thread,
    while the cut that justifies it belongs to one point on one line. Going back
    to before a compaction therefore left the boundary off the branch (so the cut
    vanished, correctly) while the summary stayed (so the prompt carried a
    summary of the abandoned line PLUS the full history it summarized). Written
    here, the summary lives or dies with the boundary it describes."""
    folded = _fold(thread_id)
    if folded is None:
        return
    _meta, _msgs, seqs, _cut = folded
    ct = max(0, min(int(through), len(seqs)))
    at_seq = seqs[ct - 1] if ct > 0 else 0
    ev: dict = {"kind": "boundary", "mode": mode, "at_seq": at_seq}
    if summary:
        # `fold_summary`, NOT `summary`. The server-side folding removed on
        # 2026-08-12 wrote its summary onto the boundary under `summary`, and
        # threads still carry those events. They are deliberately INERT: the
        # summarizer of that era had been failing, so honouring them would hide
        # messages behind a summary that was never written (see `_fold`, and
        # TestALegacyFoldBoundaryIsInert). Reusing the key would have quietly
        # brought every one of them back to life.
        ev["fold_summary"] = summary
    chat_log.append_sync(thread_id, ev)
    # `cut_through` and `cut_mode` are the SAME answer `_fold` resolves, cached
    # in meta so a reader can have it without parsing the log. The sync endpoint
    # needs it on every poll from every open tab, and computing it there put a
    # full log read back on the hot path — which is the thing `read_meta` exists
    # to keep off it (see the endpoint's docstring, and the tests that pin it).
    #
    # It cannot drift: the count is FROM THE FRONT and appends land at the back,
    # so nothing but another cut can change it, and a cut writes both.
    chat_log.patch_meta_sync(thread_id, boundary_seq=at_seq,
                             cut_through=ct, cut_mode=mode)


def note_session_reset(thread_id: str) -> None:
    """Record that a compact dropped the live session without folding anything.

    A compact whose tail is already short (`fold_n <= 0`, see
    `routes/chat.py:_fold_summary`) still detaches the live session, which is a
    real reclaim, but writes nothing through `_set_cut`: there is nothing to
    fold and moving `cut_through` here would cut the tail out of the prompt for
    no reason. Without ANY event the transcript looked exactly like a `/compact`
    that did nothing, which is the bug this exists to fix.

    So this writes a boundary too, `mode="reset"`, but deliberately not through
    `_set_cut`: it carries no `at_seq` cut position for `_fold` to honour and no
    summary, and `_fold`'s cut-resolution loop does not match `"reset"` against
    either of its conditions (`mode == "clear"` or `mode == "compact" and
    <has a summary>`), so it changes nothing about what a turn sends the model.
    It exists solely for `MessageList`'s `ContextCut` to read and draw an
    honest line: the session reset, nothing was folded."""
    folded = _fold(thread_id)
    if folded is None:
        return
    _meta, _msgs, seqs, _cut = folded
    at_seq = seqs[-1] if seqs else 0
    chat_log.append_sync(thread_id, {"kind": "boundary", "mode": "reset", "at_seq": at_seq})


def note_auto_compact(thread_id: str) -> None:
    """Record that the CLI compacted its own window mid-turn, unasked.

    Operator decision 2026-08-20: an auto-compact and a manual `/compact` are
    the same event with a different trigger, so the transcript shows them the
    same way, not as a distinct-looking indicator. This writes the same shape
    of marker `note_session_reset` does -- `mode="auto"`, no `at_seq` cut
    position and no summary -- so `_fold`'s cut-resolution loop does not match
    it against either of its conditions (`mode == "clear"` or `mode ==
    "compact" and <has a summary>`) and it changes nothing about what a turn
    sends the model. It exists solely for `MessageList`'s `ContextCut` to read
    and draw the same divider a manual compact gets."""
    folded = _fold(thread_id)
    if folded is None:
        return
    _meta, _msgs, seqs, _cut = folded
    at_seq = seqs[-1] if seqs else 0
    chat_log.append_sync(thread_id, {"kind": "boundary", "mode": "auto", "at_seq": at_seq})
    # AND THE IMAGES WENT WITH IT. This is the one thing a compaction takes that
    # its summary cannot give back, so the thread is marked as owing them and
    # the next real turn re-attaches them. Measured; see
    # services/attachment_carry. Local import: that module reads this one, so
    # importing it at the top would close a cycle.
    from services import attachment_carry
    attachment_carry.note_dropped(thread_id)


def set_summary(thread_id: str, summary: str, *, folded_through: int) -> dict | None:
    """Store what a fold produced, and move the cut to match.

    THE SUMMARY IS WRITTEN BEFORE THE CUT MOVES. Between the two writes the
    thread carries a summary and no cut, which sends the summary alongside the
    full history: redundant for one instant. The other order sends NEITHER the
    messages nor the summary that replaced them, which is a chat that has
    forgotten the conversation it is in.

    `context_tokens` goes too: it measured the window this fold just changed, and
    a stale reading is what pinned a compacted chat's meter to the number it had
    before (a four-message thread reading 142k)."""
    if chat_log.read_meta(thread_id) is None:
        return None
    # BOTH HOMES. The event is the real one (it is what a branch filter can
    # reach); `meta.summary` stays as the cache every reader that has not folded
    # the log still uses, and as the fallback for boundaries written before the
    # event carried it.
    chat_log.patch_meta_sync(thread_id, summary=summary)
    _set_cut(thread_id, folded_through, mode="compact", summary=summary)
    chat_log.patch_meta_sync(thread_id, context_tokens=None)
    return get_thread(thread_id)


# ---- Turns -----------------------------------------------------------------
#
# A TURN IS NOT A MESSAGE. While a turn is in flight the log holds one
# `turn: started` marker and `meta.active_turn_id` points at it; the reply itself
# does not exist yet. What the turn has produced so far lives in
# `meta.live_message`, rewritten on a throttle (a turn writes it a few hundred
# times) and rendered by `_live_view` as a trailing assistant message.
#
# On finalize the reply is APPENDED at the tail, so it lands after every message
# the turn was handed and before nothing. That is the whole ordering fix: the old
# arrangement claimed the reply's slot when the turn STARTED, so five messages
# sent during a twenty-minute turn were filed below the slot and the finished
# reply rendered above all five, reading as if the operator had been ignored.


def active_turn_id(thread_id: str) -> str | None:
    """The id of the turn in flight on this thread, or None. One field read.

    Distinct from `chat_log.active_turn`, which returns the whole `{turn_id,
    started_seq, live}` record the events endpoint hands a client. This is the
    one field every server-side caller actually wants."""
    meta = chat_log.read_meta(thread_id)
    return (meta or {}).get("active_turn_id") or None


def mark_turn_started(thread_id: str, turn_id: str) -> None:
    """Append this turn's `started` marker, AFTER the message that caused it.

    THE MARKER IS A RECORD; THE CLAIM IS `active_turn_id`. They were one call, so
    the marker was appended while the thread was being claimed, which is BEFORE
    the send path records the message that started the turn. The log therefore
    said a turn began before the message that began it. Nothing renders turn
    events positionally, so it was invisible, but a log whose order is not what
    happened is the same defect the transcript spent a week on. Operator,
    2026-08-12. See docs/decisions/0018.

    Split rather than reordered on purpose: `begin_turn` still takes the claim
    first, under the caller's write lock, with no await between claiming and
    spawning. That ordering is what stops two POSTs opening rival turns on one
    thread, and it is not what was wrong.

    A turn whose marker never lands degrades softly: `active_turn` falls back to
    scanning for it, finds none, and reports `started_seq: None`, which no
    reader uses for anything.

    FENCED ON STILL OWNING THE THREAD (I6). This runs after the claim lock has
    been released, so a stop-and-finalize of this very turn can interleave
    before it: the finalize clears `active_turn_seq`, and an unconditional write
    here would set it back, pointing at a turn `active_turn_id` no longer names,
    which the rotation floor and `active_turn` then trust."""
    m = chat_log.read_meta(thread_id) or {}
    if m.get("active_turn_id") != turn_id:
        return
    started = chat_log.append_sync(
        thread_id, {"kind": "turn", "turn_id": turn_id, "state": "started"})
    chat_log.patch_meta_sync(thread_id, active_turn_seq=started.get("seq"))


def begin_turn(thread_id: str, turn_id: str, model: str | None = None,
               agent: str | None = None, announce: bool = True,
               command: str | None = None) -> None:
    """Open a turn on this thread: a `turn: started` marker and `active_turn_id`.

    `announce=False` takes the claim and leaves the marker to `mark_turn_started`,
    so the send path can record the operator's message first and the log reads in
    the order things happened.

    Nothing is written to the transcript. The caller MUST hold this thread's
    write lock (`chat_log.lock_for`) across its own check-and-act, which is what
    makes two sends in the same event-loop tick unable to both open a turn.

    `agent` names the agent answering when the turn came from an @mention, and
    rides on the live view so the reply is attributed while it streams, not only
    once it lands.

    `command` is the slash command this turn IS, when it is one ("/compact").
    It rides alongside `model` and `agent` for the same reason they do: it is an
    immutable fact about the run, fixed at claim time, and the client cannot
    otherwise tell a compaction from ordinary work. `accepts_input` already says
    "this turn is A command"; it does not say WHICH, so every command turn
    looked identical to a reply and the working indicator said "Working" through
    a compaction. Absent for an ordinary message turn, which is most of them."""
    if chat_log.read_meta(thread_id) is None:
        raise KeyError(thread_id)
    # Any draft still on disk belongs to a turn that never committed (a backend
    # killed mid-turn). It is not this turn's, so it goes before this one claims
    # the thread. `active_turn` also refuses a draft naming another turn, so this
    # is tidiness rather than the guarantee.
    chat_log.clear_live(thread_id)
    meta: dict = {"turn_id": turn_id, "segments": []}
    if model:
        meta["model"] = model
    if agent:
        meta["agent"] = agent
    if command:
        meta["command"] = command
    started = chat_log.append_sync(
        thread_id, {"kind": "turn", "turn_id": turn_id, "state": "started"}) if announce else None
    chat_log.patch_meta_sync(
        thread_id, active_turn_id=turn_id,
        # WHERE THE TURN STARTED, recorded rather than searched for. It is the one
        # thing `active_turn` needed the whole log for: it scanned every event
        # backwards looking for this marker, on every 10s poll from every open
        # tab. The writer knows the answer for free. Threads written before this
        # existed have no such field, and `active_turn` still falls back to the
        # scan for them, so nothing has to be migrated.
        active_turn_seq=(started.get("seq") if started else None),
        live_message={"turn_id": turn_id, "content": "", "meta": meta, "ts": _now_iso()},
    )


def update_turn(thread_id: str, turn_id: str, segments: list[dict], content: str,
                cut_head: list[str] | None = None,
                journal_offset: int | None = None,
                context_tokens: int | None = None) -> None:
    """Overwrite the running turn's live snapshot. No-op if it is not the active
    turn any more (it finished, or something took the thread over).

    Writes the DRAFT FILE, never meta.json and never the log. This fires every
    ~0.4s and on every tool event for the whole length of a turn, so what it
    costs is not a detail: it used to read meta.json twice, read the log's tail
    twice, and rewrite-and-fsync the thread's durable record, whose size grows
    with the reply. See chat_log.write_live_sync for the measurements and why
    the draft is allowed to skip the fsync.

    What is left is one small read (the ownership guard, which has to be exact:
    a turn that lost the thread must not keep writing over its successor's
    draft) and one atomic, unsynced write.
    """
    # `_read_meta_raw`, not `read_meta`: the guard needs `active_turn_id`, which
    # does not depend on the `last_seq` reconciliation that `read_meta` pays a
    # log-tail read for.
    stored = chat_log._read_meta_raw(thread_id)
    if stored is None or stored.get("active_turn_id") != turn_id:
        return
    # A CUT POSITION IS NEVER UNLEARNED WITHIN A TURN, so an empty list in
    # memory must not erase one already on disk.
    #
    # `cut_head` names the messages that arrived before this turn emitted
    # anything, and it only ever GROWS while the turn runs: `cut_here` appends,
    # and `carry_cuts` moves refs off dropped segments ONTO it. Nothing removes
    # one. So the only way the caller can hand us an empty list after a
    # non-empty one is that the in-memory turn lost it, which is exactly what a
    # re-attach does: a backend restart rebuilds the Turn from its journal, and
    # the journal carries segments, not cut positions.
    #
    # This write then completed the loss. `write_live_sync` replaces the file,
    # so an empty list did not merely fail to update `cut_head`, it DELETED it,
    # and the client's next fresh read got a snapshot with no way to place those
    # messages. They fall into the transcript's `stray` branch and render after
    # the whole reply, which is the operator's most-repeated complaint: a
    # message he sent mid-turn appearing BELOW work that came after it.
    #
    # Measured 2026-09-03 on the live installation: a message filed at 21:40:26
    # recorded `at_segment 0` with a cut id, the backend restarted at 21:40:32,
    # and the live snapshot thereafter carried 56 segments and no `cut_head` at
    # all. See docs/decisions/0018.
    if not cut_head or not context_tokens:
        prev = chat_log.read_live(thread_id)
        if prev is not None and prev.get("turn_id") == turn_id:
            pmeta = prev.get("meta") or {}
            if not cut_head and pmeta.get("cut_head"):
                cut_head = list(pmeta["cut_head"])
            # A RE-ATTACHED TURN HAS NOT SEEN A USAGE BLOCK YET, and the reading
            # it already published must not drop back to the previous turn's.
            if not context_tokens and pmeta.get("context_tokens"):
                context_tokens = int(pmeta["context_tokens"])
    chat_log.write_live_sync(thread_id, {
        "turn_id": turn_id,
        "content": content,
        # WHERE THESE SEGMENTS STOP in the turn's journal, so a backend that
        # restarts mid-reply resumes the tail instead of replaying it from byte
        # zero and rebuilding the message the operator is already looking at.
        # Written here, in the same call as the segments, because an offset that
        # does not match the segments beside it is worse than none at all.
        **({"journal_offset": int(journal_offset)} if journal_offset else {}),
        # `turn_id` rides inside `meta` because that is the shape `_live_view`
        # renders and the client already reads.
        # `cut_head` names the messages that arrived before this turn had
        # emitted anything. It rides in `meta` beside the segments because the
        # two are read together and a reader that has one without the other
        # cannot place those messages. See docs/decisions/0018.
        # `context_tokens` is the size of the prompt the model last reported for
        # THIS turn. It rides here for the same reason `cut_head` does: it is
        # live state that otherwise exists only on the wire, so a client that is
        # not currently streaming (a reconnect, a second tab, a reload) cannot
        # see it, and GET /context falls back to the LAST turn's number. On a
        # turn that runs for an hour that fallback is not stale by a little, it
        # is a different conversation's number.
        "meta": {"segments": segments, "turn_id": turn_id,
                 **({"cut_head": list(cut_head)} if cut_head else {}),
                 **({"context_tokens": int(context_tokens)}
                    if context_tokens else {})},
    })


def commit_turn(
    thread_id: str,
    turn_id: str,
    segments: list[dict],
    content: str,
    error: bool = False,
    timed_out: bool = False,
    notes: list[str] | None = None,
    receipts: list[dict] | None = None,
    stopped: str | None = None,
    agent: str | None = None,
    state: str = "finished",
    cut_head: list[str] | None = None,
) -> dict | None:
    """Commit a turn: one assistant message at the TAIL, one `turn` marker, done.

    Appending at the tail is the point. A message the operator sent during this
    run is already in the log ABOVE this reply, because it was persisted when it
    arrived and this reply did not exist yet. A message that arrived too late to
    be delivered lands BELOW, because it will be appended after. The transcript
    therefore says which messages the reply accounted for, without anyone having
    to annotate it.

    Committed whether or not a client is watching, and idempotent on the thread's
    view of the world: it clears `active_turn_id` only when it still names this
    run, so a turn that was taken over cannot un-claim its successor.

    `timed_out` flags a turn that hit its wall-clock limit (vs a hard error) so
    the UI can render a calm "stopped, continue" note instead of a red error.
    `stopped` names any OTHER non-completion ending — STOPPED_BY_OPERATOR when
    the operator pressed Stop, STOPPED_INTERRUPTED when a restart orphaned the
    run. `notes` carries the action pipeline's human-readable results (e.g.
    "Updated the plan").

    `receipts` is the same information TYPED: [{kind, label}], where kind names
    what changed outside the chat (note / ticket / learning / action / warn).
    `notes` stays alongside it and stays the plain labels, because every stored
    turn already has it and nothing may lose an old one on read."""
    stored = chat_log.read_meta(thread_id)
    if stored is None:
        return None
    meta: dict = {"turn_id": turn_id, "streaming": False, "ended_at": _now_iso()}
    if segments:
        meta["segments"] = segments
    # Where messages that beat this turn's first output belong. Stored even when
    # there are no segments: "the operator spoke before it said anything" is
    # exactly the case with none. See docs/decisions/0018.
    if cut_head:
        meta["cut_head"] = list(cut_head)
    live = stored.get("live_message")
    if isinstance(live, dict):
        base = live.get("meta") or {}
        for k in ("model", "agent"):
            if base.get(k):
                meta.setdefault(k, base[k])
        # When the turn began, so a reader can see how long it ran. `ts` on the
        # message is when it was COMMITTED, which is the honest answer to "when
        # did this land in the conversation".
        if live.get("ts"):
            meta["started_at"] = live["ts"]
    if error:
        meta["error"] = True
    if timed_out:
        meta["timeout"] = True
    if stopped:
        meta["stopped"] = stopped
    if notes:
        meta["notes"] = notes
    if receipts:
        meta["receipts"] = receipts
    if agent:
        meta["agent"] = agent
    ev = chat_log.append_message_sync(thread_id, "assistant", content, meta=meta,
                                      ts=_now_iso())
    # Index this message's receipts as backlink sources (design doc sec 10/7.2:
    # "sources of refs: note bodies, ticket bodies, chat receipts"). A receipt
    # is a durable effect outside the chat, and every one that carries a
    # `ref` (build_streaming.build_receipts) already names the note it wrote;
    # this is the one place that turns it into `workshop:chat/<thread>#<msg>`
    # naming `workshop:note/<scope>/<id>`, so "referenced by" on that note
    # shows the conversation that filed it.
    if receipts and ev.get("id"):
        try:
            from services import backlinks, refs
            targets = []
            for r in receipts:
                rref = r.get("ref") or {}
                if rref.get("scope") and rref.get("id"):
                    targets.append(refs.note_ref(rref["scope"], rref["id"]))
            backlinks.set_source(refs.chat_ref(thread_id, ev["id"]), targets)
        except Exception:
            log.debug("backlink index update skipped for chat %s", thread_id, exc_info=True)
    chat_log.append_sync(thread_id, {"kind": "turn", "turn_id": turn_id, "state": state})
    clear: dict = {}
    if stored.get("active_turn_id") == turn_id:
        clear["active_turn_id"] = None
        clear["active_turn_seq"] = None
        # The legacy field, cleared for a thread whose turn began before the
        # draft moved to its own file.
        clear["live_message"] = None
    if clear:
        chat_log.patch_meta_sync(thread_id, **clear)
        # The reply is in the log now, so the draft of it is finished business.
        # Left behind, `active_turn` would merge a dead turn's text into the next
        # one that claims this thread.
        chat_log.clear_live(thread_id)
    return _msg_from_event(ev)


# The two ways a turn ends without finishing, as `meta.stopped` on the committed
# message. They are different events and the transcript says which: the operator
# pressed Stop, or the backend went down under a turn and nothing was left to
# answer for it. One value each, so the client has one thing to switch on and no
# reason to infer it from anything else.
STOPPED_BY_OPERATOR = "operator"
STOPPED_INTERRUPTED = "interrupted"


# The one wording for a turn this backend can no longer account for. Used by the
# startup reconcile and by the re-attach path, so the operator reads the same
# sentence however the turn was lost.
LOST_TURN_TEXT = (
    "Lost when the backend restarted mid-turn. Anything already written to "
    "disk is intact. Send \"continue\" to pick it back up."
)


def lose_turn(thread_id: str, turn_id: str) -> dict | None:
    """Close out a turn whose process is dead or gone: a `turn: lost` marker and a
    message saying so, so the chat is never left waiting on something that will
    never answer.

    Marked `stopped=interrupted`, which is the value the client's tag has always
    been written for and which nothing had set since the per-read self-heal was
    deleted. Without it this renders as an ordinary reply that happens to be
    about a restart; with it, it reads as what it is."""
    return commit_turn(thread_id, turn_id, [], LOST_TURN_TEXT, state="lost",
                       stopped=STOPPED_INTERRUPTED)


def release_turn(thread_id: str, turn_id: str) -> None:
    """Close a turn that never got off the ground, writing no message.

    A claim is taken before the prompt is assembled, because assembly awaits and
    an await is where a rival turn used to slip in. If assembly then fails, the
    caller has an error to return and the thread has a claim to give back; there
    is nothing to say in the transcript that the failed request does not already
    say to the person who made it."""
    stored = chat_log.read_meta(thread_id)
    if stored is None or stored.get("active_turn_id") != turn_id:
        return
    chat_log.append_sync(thread_id, {"kind": "turn", "turn_id": turn_id, "state": "finished"})
    chat_log.patch_meta_sync(thread_id, active_turn_id=None, active_turn_seq=None,
                             live_message=None)
    chat_log.clear_live(thread_id)


def previous_turn_id(thread_id: str) -> str | None:
    """The last turn this thread finished (or lost). Not the active one.

    This is what `chat_context.arrival_notice` asks for: "which messages arrived
    while the last turn was going" is then a lookup on `meta.during_turn`, not an
    inference from where a message sits relative to the last assistant reply.
    That inference is what broke the moment the reply started being appended at
    the tail."""
    for ev in reversed(chat_log.read_events(thread_id)):
        if ev.get("kind") == "turn" and ev.get("state") in ("finished", "lost"):
            return ev.get("turn_id")
    return None


# ---- context accounting ----------------------------------------------------


def set_thread_context_tokens(thread_id: str, tokens: int,
                              measured_session: str | None = None) -> dict | None:
    """Persist the real context occupancy (input + cached input tokens) the model
    reported on the latest turn. The composer meter prefers this over estimating
    from the stored transcript, because it reflects the live window and drops
    after an in-session /compact. Stored at the thread level (current occupancy,
    not per-message).

    `measured_session` is the session the number DESCRIBES, and the write only
    lands while the thread still owns that session. A measurement is a fact
    about one live window; if a fold or /clear detached that window while the
    turn was still running, the number is a fact about nothing, and writing it
    would overwrite the clear the fold just made. That is not hypothetical: on
    2026-08-14 a compact landed at 03:20:38 and the turn already in flight
    finalized at 03:20:39, writing its pre-compact 467k back over the cleared
    meter, which then read 47% on a freshly compacted chat for ten minutes
    until the next turn happened to end. Passing None skips the check, for
    callers that have no session to speak of.

    A DROPPED WRITE IS LOGGED, NOT SILENT. It used to return the thread dict
    unchanged, exactly as a successful write does, so a caller could not tell
    the difference and neither could anyone reading a log. On 2026-09-04 a whole
    investigation went down the wrong path on the theory that this fence was
    silently eating every write on resumed threads; it was not (the real cause
    was a compaction clearing the meter with nothing to restore it), and the
    reason that theory could not be checked in five minutes is that a fence
    which drops a write leaves no trace. WARNING, with both sessions named, so
    the next such theory is settled by grep."""
    if measured_session is not None:
        t = get_thread(thread_id)
        if t is None:
            return None
        owned = {t.get("claude_session")}
        sessions = t.get("agent_sessions")
        if isinstance(sessions, dict):
            owned.update(sessions.values())
        if measured_session not in owned:
            log.warning(
                "thread %s: DROPPED a context measurement of %d tokens taken "
                "against session %s; the thread now owns %s. The window that "
                "number describes is detached, so it is a fact about nothing.",
                thread_id, max(0, int(tokens)), measured_session,
                sorted(s for s in owned if s) or "no session")
            return t
    return _patch(thread_id, context_tokens=max(0, int(tokens)))


def clear_thread_context_tokens(thread_id: str) -> dict | None:
    """Forget the measured context occupancy.

    Called when something has just shrunk the live window (an in-session
    /compact) but no fresh measurement came back with it. Absent the key the
    meter falls back to estimating from the compacted prompt, which is right;
    keeping a stale pre-compact number is not."""
    t = get_thread(thread_id)
    if t is None:
        return None
    if "context_tokens" not in t:
        return t
    return _patch(thread_id, context_tokens=None)


def get_turn_marks(thread: dict) -> dict:
    """What this thread has already been TOLD, so a turn can avoid saying it
    twice. Pure read off a thread dict the caller already has.

      live_hash          digest of the last live-context snapshot sent
      seen_scope_version the scope change-counter this thread last observed
      contract_hash      digest of the instruction contract (role preamble +
                         persona) this thread's claude session has been told.
                         A session is given its protocols once, at spawn, so
                         without this a contract change could never reach a
                         conversation that was already open. See
                         chat_context.instruction_contract.

    These are bookkeeping about the conversation, not part of it, which is why
    they live on the thread record rather than in the transcript."""
    marks = thread.get("turn_marks")
    return dict(marks) if isinstance(marks, dict) else {}


def set_turn_marks(thread_id: str, **fields: object) -> dict | None:
    """Merge bookkeeping marks. Only writes when something actually changed, so
    a turn that repeats the same state does not rewrite the thread file."""
    t = get_thread(thread_id)
    if t is None:
        return None
    marks = t.get("turn_marks")
    marks = dict(marks) if isinstance(marks, dict) else {}
    changed = False
    for k, v in fields.items():
        if marks.get(k) != v:
            marks[k] = v
            changed = True
    if not changed:
        return t
    return _patch(thread_id, turn_marks=marks)


def set_card_state(thread_id: str, key: str, state: dict) -> dict | None:
    """Persist the outcome of acting on one in-chat ui-card, keyed by a digest of
    the card block. Stored on the thread because it is a fact about this
    conversation, and because the operator hitting "run" on one device should not
    see that same card offering to run again on another."""
    t = get_thread(thread_id)
    if t is None:
        return None
    states = dict(t.get("card_states") or {})
    states[str(key)[:128]] = {**state, "ts": _now_iso()}
    return _patch(thread_id, card_states=states)


# ---- sessions --------------------------------------------------------------


def ensure_thread_session(thread_id: str) -> tuple[str, bool] | None:
    """Return (session_id, is_new) for this thread's persistent claude session.

    Every chat is a conversation, so every chat gets ONE session that the CLI
    carries across turns. Before this, only build-agent chats had a session and
    every other chat re-spawned stateless: each turn re-derived what was going on
    from a 24-message text transcript, so a chat mid-task visibly "started from
    scratch" every time and repeated the discovery work it had already done.

    is_new tells the caller whether this turn passes --session-id (mint) or
    --resume (continue)."""
    t = get_thread(thread_id)
    if t is None:
        return None
    existing = t.get("claude_session")
    if existing:
        return existing, False
    sid = str(uuid.uuid4())
    _patch(thread_id, claude_session=sid)
    return sid, True


def ensure_agent_session(thread_id: str, slug: str) -> tuple[str, bool] | None:
    """Return (session_id, is_new) for one AGENT speaking in one THREAD.

    Keyed by the pair, deliberately. An @mentioned agent used to spawn with no
    session at all, so every mention rebuilt its definition, its knowledge
    layers and the conversation excerpt from nothing — the one place in the
    system that really did reset on every turn.

    The obvious fix, reusing the agent's own long-lived session, is wrong: the
    same agent mentioned in two different chats would carry the first chat's
    context into the second. Per (agent, thread) gives continuity where a
    conversation is actually continuing, and isolation between conversations
    that merely share a speaker.

    SO A MENTION IS NOT STATELESS, and `routes.chat._start_agent_mention_turn`
    used to say it was, in a docstring three hundred lines above the call to this
    function. Both sides now describe what the code does."""
    t = get_thread(thread_id)
    if t is None:
        return None
    sessions = t.get("agent_sessions")
    sessions = dict(sessions) if isinstance(sessions, dict) else {}
    existing = sessions.get(slug)
    if existing:
        return existing, False
    sid = str(uuid.uuid4())
    sessions[slug] = sid
    _patch(thread_id, agent_sessions=sessions)
    return sid, True


def clear_agent_session(thread_id: str, slug: str) -> dict | None:
    """Forget one agent's session in this thread, so its next mention starts
    fresh. The counterpart to clear_thread_session, for the same reason: a
    session id whose CLI session is gone would fail the whole turn on --resume."""
    t = get_thread(thread_id)
    if t is None:
        return None
    sessions = t.get("agent_sessions")
    if not isinstance(sessions, dict) or slug not in sessions:
        return t
    sessions = dict(sessions)
    sessions.pop(slug, None)
    return _patch(thread_id, agent_sessions=sessions or None)


def clear_thread_session(thread_id: str) -> dict | None:
    """Detach the thread from its claude session so the next turn starts a fresh
    one (and re-sends the assembled history, so nothing is forgotten). Used when
    the session is gone from disk or a resume failed.

    "Nothing is forgotten" was never quite true. The re-assembled history is
    TEXT, so it carries no image -- the same hole a compaction leaves. So the
    images are marked as owed here too and the next real turn re-attaches them
    (see services/attachment_carry). That is right for every caller: a rescope,
    a fork, a branch switch, a session pruned off disk and a failed resume all
    hand the next turn the transcript as prose. A `/clear` does NOT come through
    here -- `clear_thread_context` drops `claude_session` itself, and drops
    `turn_marks` in the same write, so an operator who has just discarded the
    whole context is not handed their screenshots back."""
    if chat_log.read_meta(thread_id) is None:
        return None
    from services import attachment_carry  # local: see note_auto_compact
    attachment_carry.note_dropped(thread_id)
    return _patch(thread_id, claude_session=None)


def resolve_cut(thread_id: str) -> dict:
    """Resolve the cut from the log and cache it in meta. One read, then never
    again for this thread unless another cut is made."""
    folded = _fold(thread_id)
    if folded is None:
        return {"cleared_through": 0, "cut_mode": ""}
    _meta, _msgs, seqs, cut_seq = folded
    ct = _cleared_through(seqs, cut_seq)
    mode = getattr(_fold, "last_mode", "") or ("clear" if ct else "")
    chat_log.patch_meta_sync(thread_id, cut_through=ct, cut_mode=mode)
    return {"cleared_through": ct, "cut_mode": mode if ct else ""}


def _backfill_cuts_for_owner() -> int:
    done = 0
    for tid in chat_log.list_thread_ids():
        try:
            meta = chat_log.read_meta(tid)
            if meta is None or "cut_through" in meta:
                continue
            resolve_cut(tid)
            done += 1
        except Exception as e:  # pragma: no cover - defensive
            log.warning("could not resolve the context cut for %s: %s", tid, e)
    return done


def backfill_cuts() -> int:
    """Cache the resolved cut for every thread of every owner.

    Runs at startup, so the sync endpoint never pays for it. Threads cleared
    before 2026-08-12 carry `boundary_seq` and no count; without this the
    divider would vanish from them while the prompt kept honouring the cut.

    IT ITERATES OWNERS EXPLICITLY. Every
    read here is owner-scoped and startup has no owner in context, so the
    obvious version ran against nothing at all and reported success: measured
    2026-08-12, zero threads resolved on a box with sixty of them.
    """
    root = ownership._RUNTIME / "users"
    if not root.exists():
        return 0
    total = 0
    prior = ownership.owner_or_none()
    try:
        for user_dir in sorted(root.iterdir()):
            if not user_dir.is_dir() or not user_dir.name.isdigit():
                continue
            ownership.set_current_owner(int(user_dir.name))
            try:
                total += _backfill_cuts_for_owner()
            except Exception as e:  # pragma: no cover - defensive
                log.error("context-cut backfill failed for owner %s: %s", user_dir.name, e)
    finally:
        if prior is None:
            ownership.clear_current_owner()
        else:
            ownership.set_current_owner(prior)
    return total


def cut_state(thread_id: str) -> dict:
    """`{cleared_through, cut_mode}` from META ALONE, with no log read.

    The wire form of the context cut, for the sync endpoint. A `compact` cut
    with no summary behind it reads as no cut at all, matching `_fold` exactly:
    folding was removed and restored on 2026-08-12, and threads carrying a
    boundary from those hours have nothing behind them, so honouring one would
    hide real messages behind a summary that was never written.
    """
    meta = chat_log.read_meta(thread_id) or {}
    if "cut_through" not in meta and meta.get("boundary_seq"):
        # A cut made before the count was cached. `backfill_cuts` does this for
        # every thread at startup; this covers one that appeared since, and both
        # resolve it the same way `_document` does, so the divider and the
        # prompt cannot end up describing different cuts.
        #
        # Gated on `boundary_seq` rather than run unconditionally, because THIS
        # RUNS ON EVERY POLL FROM EVERY OPEN TAB: resolving from the log here
        # for a thread that has no cut at all would put a full log read back on
        # the endpoint whose whole point is not doing that. Every boundary is
        # written by `_set_cut`, which writes `boundary_seq` in the same patch.
        resolve_cut(thread_id)
        meta = chat_log.read_meta(thread_id) or meta
    ct = int(meta.get("cut_through") or 0)
    mode = meta.get("cut_mode") or ("clear" if ct else "")
    if not ct:
        return {"cleared_through": 0, "cut_mode": ""}
    if mode == "compact" and not (meta.get("summary") or "").strip():
        return {"cleared_through": 0, "cut_mode": ""}
    return {"cleared_through": ct, "cut_mode": mode}


def clear_thread_context(thread_id: str) -> tuple[dict, int] | None:
    """Drop everything this thread would send to a model, keeping the transcript.

    The counterpart to compaction's fold, and deliberately the same boundary:
    "no longer in the prompt" either way. A clear is a fold with nothing
    preserved: no `summary` survives it.

    Also drops the live claude session, the per-@mentioned-agent sessions, and
    `turn_marks`: all three are "what this thread has already been told", and a
    clear means none of it has. Raw messages are untouched — reversible, same
    as a fold."""
    folded = _fold(thread_id)
    if folded is None:
        return None
    meta, msgs, seqs, cut_seq = folded
    ct = _cleared_through(seqs, cut_seq)
    cleared = max(0, len(msgs) - ct)
    if cleared == 0:
        return _document(meta, msgs, seqs, cut_seq), 0
    _set_cut(thread_id, len(msgs))
    # `voice_session` joins the other three for the same reason: it is one more
    # copy of "what this thread has already been told", held by the resident voice
    # worker. Leaving it behind meant a /clear silently spared the spoken half of
    # the conversation, so talking after a clear resumed a model that still
    # remembered everything the clear was meant to drop.
    _patch(thread_id, summary=None, context_tokens=None,
           claude_session=None, agent_sessions=None, turn_marks=None,
           voice_session=None)
    return get_thread(thread_id), cleared


def rename_thread(thread_id: str, title: str, source: str = "manual") -> dict | None:
    """Name the thread. `source` records who named it (the operator, or the
    auto-namer) as a `title` event, so the log says how it got its name."""
    if chat_log.read_meta(thread_id) is None:
        return None
    clean = title.strip()[:80] or "New chat"
    chat_log.append_sync(thread_id, {"kind": "title", "title": clean, "source": source})
    return _patch(thread_id, title=clean)
