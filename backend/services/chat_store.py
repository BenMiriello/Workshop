"""Chat thread + message persistence — the generalized history layer.

App-agnostic: a thread carries a `context_key` (e.g. "budget", "general") that
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

import bisect
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import chat_log, ownership  # noqa: F401  (ownership re-exported for tests)

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
#   orchestrated  work leaves as Tickets run by Workshop agents; the Ticket is
#                 the record. Labelled "Tickets" in the UI — the stored name is
#                 kept so existing threads need no migration.
#
# DIRECT IS THE DEFAULT. Orchestrated was, and it made every passing remark a
# candidate for a ticket; work that outlives the chat is the exception, and the
# exception should be the one you ask for. Threads that set a mode explicitly
# keep it; threads that never did follow the default, which is what "the default"
# has to mean for it to be changeable at all.
CHAT_MODES = ("direct", "orchestrated")
DEFAULT_CHAT_MODE = "direct"


def _clamp_mode(mode: str | None) -> str:
    return mode if mode in CHAT_MODES else DEFAULT_CHAT_MODE


# The operator's chosen default for NEW chats, overriding DEFAULT_CHAT_MODE.
# Lives beside the threads it applies to, one small file per owner. A chat stores
# its mode at creation, so changing this never rewrites a conversation that
# already exists — "default" means the mode a new chat starts in, and nothing more.
_PREFS_FILE = "prefs.json"


def _prefs_path() -> Path:
    return _chat_dir() / _PREFS_FILE


def _read_prefs() -> dict:
    # OwnerUnset is caught alongside the io errors on purpose. Reading a
    # preference is a SOFT layer in every resolution chain that consults it
    # (routes.chat._resolve_effort / _resolve_permission_mode), so "nobody is
    # pinned" has to mean "no preference", not an exception thrown out of the
    # middle of a turn. Writes still fail closed: `_prefs_path()` is called
    # outside this guard in every setter below.
    try:
        data = json.loads(_prefs_path().read_text())
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError, ownership.OwnerUnset):
        return {}


def _write_pref(key: str, value: object) -> None:
    """Set (or, on None, clear) one key in prefs.json. Read-modify-write of a
    handful of scalars written only from the settings screen; there is no
    concurrent writer to lose an update to."""
    prefs = _read_prefs()
    if value is None:
        prefs.pop(key, None)
    else:
        prefs[key] = value
    p = _prefs_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(prefs, indent=2))
    tmp.replace(p)


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


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# Meta fields that belong to the log machinery, not to the thread document. They
# never appear in a thread dict a caller sees, so a caller can never write them
# back, and `save_thread` never mistakes their absence for a deletion.
_INTERNAL_META = ("last_seq", "boundary_seq", "active_run_id", "active_run_seq",
                  "live_message")


def _promotion_patch(meta: dict) -> dict:
    """P5: quietly migrate a legacy `agent:<slug>` (or `build:<slug>`) chat to
    workshop or project scope with the agent captured as a participant.

    An agent chat under the old model had context_key = agent:<slug> and no
    participants list. Under the new model an agent is a PARTICIPANT of a chat
    that lives in workshop / project / app scope. Returns the meta fields that
    move it, computed on every write so the store gradually drifts to the new
    shape without a data-migration pass.

    Never touches a chat whose participants have been explicitly diverged from
    the context_key (scope_locked = True, or a participants list that is empty
    or has more than one entry, or an entry that doesn't match). That way an
    operator-authored intentional agent:<slug>-scope chat stays as-is."""
    ck = meta.get("context_key", "")
    if ":" not in ck:
        return {}
    kind, rest = ck.split(":", 1)
    if kind not in ("agent", "build"):
        return {}
    if meta.get("scope_locked"):
        return {}
    parts = _normalize_participants(meta.get("participants") or [])
    canon = f"agent:{rest}"
    # Only promote when the participant list is exactly [that agent] (or the
    # freshly back-filled derivation). Anything else was set on purpose.
    if parts and parts != [canon]:
        return {}
    patch: dict = {"participants": [canon]}
    # Try to pull the agent's project link so a project-scoped agent's chat
    # lands in project:<slug>; otherwise land in workshop (general).
    project_slug = None
    try:
        from services import agents_registry as _reg
        rec = _reg.get(rest)
        if rec is not None:
            project_slug = rec.get("project_slug") or None
    except Exception:
        project_slug = None
    if project_slug:
        patch["context_key"] = f"project:{project_slug}"
        patch["scope"] = f"project:{project_slug}"
    else:
        patch["context_key"] = "general"
        patch["scope"] = "workshop"
    return patch


# ---- folding the log into the thread document ------------------------------


def _fold(thread_id: str) -> tuple[dict, list[dict], list[int]] | None:
    """(meta, messages, seq-of-each-message) or None if the thread is gone.

    A straight replay: every `message` event in seq order, nothing rewritten and
    nothing moved. The log has no revision event, so this cannot do anything
    else, which is the property the whole rebuild is for.

    Committed messages only. A run in flight has nothing here — see `_live_view`,
    which `_document` appends as a trailing view. Keeping it out of the fold is
    what keeps `seqs` a list of real seqs, so the context boundary still lands on
    a message that actually exists.
    """
    meta = chat_log.read_meta(thread_id)
    if meta is None:
        return None
    messages: list[dict] = []
    seqs: list[int] = []
    for ev in chat_log.read_events(thread_id):
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
    return meta, messages, seqs


def _live_view(meta: dict) -> dict | None:
    """The in-flight run rendered as a trailing assistant message, or None.

    THE RUN IS NOT IN THE LOG. While a turn is in flight the thread holds no
    assistant message for it; `meta.live_message` carries the throttled snapshot
    of what it has produced so far, and this is that snapshot shaped as a
    message so a reload still renders the turn in progress. Built fresh on every
    read, never written back.

    That is the ordering fix. The reply used to claim its slot when the turn
    STARTED, so a message sent at minute twelve of a twenty-minute turn was filed
    below the slot and the finished reply landed ABOVE it — reading as if the
    operator had been ignored. Now the snapshot is always last while the turn
    runs, and the committed reply is appended at the tail when it finishes: a
    message the run was handed sits above the reply that answered it, and one
    that arrived too late to be delivered sits below.

    `meta.streaming` and `meta.turn_id` are on it because that is how a client
    knows to attach to the SSE stream for this run."""
    if not meta.get("active_run_id"):
        return None
    live = meta.get("live_message")
    if not isinstance(live, dict):
        return None
    m = dict(live.get("meta") or {})
    m["streaming"] = True
    m["turn_id"] = meta["active_run_id"]
    m.setdefault("segments", [])
    return {
        "role": "assistant",
        "content": live.get("content") or "",
        "ts": live.get("ts"),
        "meta": m,
    }


def _compacted_through(seqs: list[int], boundary_seq: int) -> int:
    """How many messages are behind the context boundary.

    `boundary_seq` is a seq, which nothing can shift; `compacted_through` is the
    count of messages at or before it, which is the index-into-the-array the old
    field was and callers still read."""
    if boundary_seq <= 0:
        return 0
    return bisect.bisect_right(seqs, boundary_seq)


def _document(meta: dict, messages: list[dict], seqs: list[int]) -> dict:
    """The thread dict callers have always been handed."""
    t = {k: v for k, v in meta.items() if k not in _INTERNAL_META}
    # Back-fill the entity-model fields for legacy threads (P1 / C11).
    # Persisted-vs-derived is not observable to callers: an unset stored field
    # reads as the derived value.
    ck = t.get("context_key", "general")
    if not t.get("scope"):
        t["scope"] = _scope_from_context_key(ck)
    if "scope_locked" not in t:
        t["scope_locked"] = False
    if not t.get("mode"):
        t["mode"] = DEFAULT_CHAT_MODE
    if t.get("participants") is None:
        t["participants"] = _participants_from_context_key(ck)
    else:
        t["participants"] = _normalize_participants(t.get("participants"))
    ct = _compacted_through(seqs, int(meta.get("boundary_seq") or 0))
    if ct:
        t["compacted_through"] = ct
    live = _live_view(meta)
    t["messages"] = ([*messages, live] if live is not None else messages)
    return t


def _patch(thread_id: str, **fields: object) -> dict | None:
    """Write named meta fields (None removes one) and return the fresh thread."""
    meta = chat_log.read_meta(thread_id)
    if meta is None:
        return None
    merged = {**meta, **{k: v for k, v in fields.items() if v is not None}}
    promo = _promotion_patch(merged)
    if promo:
        fields = {**fields, **promo}
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
    scope: str | None = None,
    scope_locked: bool = False,
    mode: str | None = None,
    participants: list[str] | None = None,
) -> dict:
    tid = uuid.uuid4().hex
    # Entity-model 2026-07-18 §7 fields (C11, P1):
    # - scope: the Scope this chat lives in (workshop / project:X / app:Y).
    # - scope_locked: refuses to re-scope silently; the UI must confirm.
    # - mode: direct (the default) or orchestrated. See CHAT_MODES.
    # - participants: agent slugs (as `agent:<slug>`) in this chat. Under the
    #   new model an Agent is a PARTICIPANT of a chat, not the chat's home.
    #   A legacy `agent:<slug>` context_key back-fills this list on read so old
    #   chats still surface the agent inline.
    resolved_scope = scope or _scope_from_context_key(context_key or "general")
    parts = _normalize_participants(participants) if participants is not None \
        else _participants_from_context_key(context_key or "general")
    now = _now_iso()
    meta = {
        "id": tid,
        "context_key": context_key or "general",
        "scope": resolved_scope,
        "scope_locked": bool(scope_locked),
        # An explicit mode wins; otherwise the operator's default for new chats.
        "mode": _clamp_mode(mode) if mode is not None else get_default_mode(),
        "participants": parts,
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
        "title": title or "New chat",
        "created_at": now,
        "updated_at": now,
    }
    promo = _promotion_patch(meta)
    if promo:
        meta.update(promo)
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
    return gone


def list_threads(context_key: str | None = None) -> list[dict]:
    """Summaries (no message bodies), newest first."""
    if not _chat_dir().exists():
        return []
    chat_log.migrate_owner()
    out: list[dict] = []
    for tid in chat_log.list_thread_ids():
        folded = _fold(tid)
        if folded is None:
            continue
        meta, msgs, _seqs = folded
        if "id" not in meta:
            continue
        if context_key and meta.get("context_key") != context_key:
            continue
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
        out.append({
            "id": meta["id"],
            "context_key": ck,
            # Entity-model 2026-07-18 §7 fields (C11, P1). Existing threads
            # without stored scope/mode/participants lift them from the legacy
            # context_key so a data migration isn't needed; new writes persist
            # them explicitly.
            "scope": meta.get("scope") or _scope_from_context_key(ck),
            "scope_locked": bool(meta.get("scope_locked", False)),
            "mode": meta.get("mode") or DEFAULT_CHAT_MODE,
            "participants": _normalize_participants(meta.get("participants"))
                if meta.get("participants") is not None
                else _participants_from_context_key(ck),
            "agent_id": meta.get("agent_id"),
            "model": meta.get("model"),
            "title": title,
            "created_at": meta.get("created_at"),
            "updated_at": meta.get("updated_at"),
            "last_user_at": last_user_at,
            "message_count": len(msgs),
            "last_message": ((last.get("content") or "")[:120] if last else ""),
        })
    out.sort(key=_recency_key, reverse=True)
    return out


def _recency_key(summary: dict) -> str:
    """Order by when the operator last spoke, falling back to creation. Never
    updated_at — see the note on last_user_at."""
    return summary.get("last_user_at") or summary.get("created_at") or ""


# ---- scope / participants / mode -------------------------------------------


def _normalize_participants(raw) -> list[str]:
    """Coerce any incoming participant list into canonical `agent:<slug>` form.
    Drops empties, dedupes preserving first-seen order. Bare slugs are namespaced
    to `agent:` since that is the only participant kind today (per entity-model
    §7.1 the operator is always implicit; agents are the explicit list)."""
    if not isinstance(raw, (list, tuple)):
        return []
    seen: set[str] = set()
    out: list[str] = []
    for p in raw:
        if not isinstance(p, str):
            continue
        s = p.strip()
        if not s:
            continue
        if ":" not in s:
            s = f"agent:{s}"
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _participants_from_context_key(context_key: str) -> list[str]:
    """Legacy chats keyed by `agent:<slug>` back-fill their participants list
    from that prefix. Same for `build:<slug>` (a build is a runtime, not a
    separate scope kind). Everything else starts empty."""
    if not context_key or ":" not in context_key:
        return []
    kind, rest = context_key.split(":", 1)
    if kind == "agent":
        return [context_key]
    if kind == "build":
        return [f"agent:{rest}"]
    return []


def _scope_from_context_key(context_key: str) -> str:
    """Best-effort mapping from the legacy context_key convention to the entity-
    model scope address (§2). Preserves existing threads without a data
    migration: a thread that has no explicit `scope` field lifts one from the
    prefix on first read."""
    if not context_key or context_key == "general":
        return "workshop"
    if ":" in context_key:
        kind, rest = context_key.split(":", 1)
        if kind == "build":
            # build:<slug> and agent:<slug> are both agent-scope chats,
            # differing only in runtime (per entity-model §6, §7).
            return f"agent:{rest}"
        if kind in ("project", "agent", "app"):
            return context_key
        return context_key
    # Bare non-general = app id.
    return f"app:{context_key}"


def set_thread_scope(thread_id: str, scope: str, *, scope_locked: bool | None = None) -> dict | None:
    """Re-scope a chat. Optionally toggles scope_locked. Returns updated thread
    or None. C11 gate for rescope; the UI must confirm before calling."""
    fields: dict = {"scope": scope}
    if scope_locked is not None:
        fields["scope_locked"] = bool(scope_locked)
    return _patch(thread_id, **fields)


def set_thread_mode(thread_id: str, mode: str) -> dict | None:
    """Set the per-thread interaction mode. `mode` must be one of CHAT_MODES;
    anything else clamps to DEFAULT_CHAT_MODE."""
    return _patch(thread_id, mode=_clamp_mode(mode))


def set_thread_participants(thread_id: str, participants: list[str]) -> dict | None:
    """Overwrite the participants list. Normalized on write; empty list is a
    valid "no agent in this chat" state. C11/P1: agents are participants of a
    chat, not the chat's home. Returns updated thread or None if missing."""
    return _patch(thread_id, participants=_normalize_participants(participants))


def add_thread_participant(thread_id: str, participant: str) -> dict | None:
    """Add one participant to a thread. Idempotent (already-present is a no-op).
    Namespaces a bare slug to `agent:<slug>` for consistency with the store."""
    t = get_thread(thread_id)
    if t is None:
        return None
    parts = _normalize_participants(t.get("participants") or [])
    for c in _normalize_participants([participant]):
        if c not in parts:
            parts.append(c)
    return _patch(thread_id, participants=parts)


def remove_thread_participant(thread_id: str, participant: str) -> dict | None:
    """Remove one participant. Idempotent (missing is a no-op)."""
    t = get_thread(thread_id)
    if t is None:
        return None
    canon = _normalize_participants([participant])
    if not canon:
        return t
    target = canon[0]
    kept = [p for p in _normalize_participants(t.get("participants") or []) if p != target]
    return _patch(thread_id, participants=kept)


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


def set_thread_effort(thread_id: str, effort: str | None) -> dict | None:
    """Persist the per-thread effort preference (a level name, or None to fall
    back to the model default). Returns the updated thread, or None if missing.

    Validation belongs to the caller: this stores what it is handed, exactly as
    set_thread_model does, so a level is checked once against the resolved model
    (model_caps.normalize_effort) rather than twice against different ideas of
    which model is in play."""
    return _patch(thread_id, effort=effort)


# ---- messages --------------------------------------------------------------


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
        _patch(thread_id, _seed_title=content.strip().split("\n")[0][:60] or "New chat")
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
    Delivered means "the run read it", and the transcript says so by position.

    What position does not carry is recorded on the message:

      during_run — the run that was in flight when this arrived
      at_segment — how much of that reply had streamed by then

    `during_run` is what `chat_context.arrival_notice` queries, so "which
    messages arrived while that run was going" is a lookup rather than an
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
    meta, stored_msgs, _seqs = folded

    # The live view is a rendering of the run in flight, not a record. A caller
    # that read the thread mid-turn is holding one; appending it would commit a
    # half-written reply that the run is about to commit properly.
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
        if k in ("messages", "compacted_through", "id"):
            continue
        if meta.get(k) != v:
            fields[k] = v
    for k in meta:
        if k in _INTERNAL_META or k in ("id", "created_at"):
            continue
        if k not in thread:
            fields[k] = None
    fields["updated_at"] = _now_iso()
    _patch(tid, **fields)

    want_ct = thread.get("compacted_through")
    if want_ct is not None and int(want_ct) != _compacted_through(
            seqs, int(meta.get("boundary_seq") or 0)):
        _set_boundary(tid, int(want_ct), "compact", thread.get("summary"))
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

    `compacted_through` is translated: it is written as a `boundary` event plus
    the seq of the last folded message, so the cut survives anything appended
    afterwards instead of being an index into a list that grows."""
    if "messages" in fields:
        raise ValueError("update_thread cannot write `messages`; append instead")
    if chat_log.read_meta(thread_id) is None:
        return None
    ct = fields.pop("compacted_through", None)
    if fields:
        _patch(thread_id, **fields)
    if ct is not None:
        _set_boundary(thread_id, int(ct), "compact", fields.get("summary"))
    return get_thread(thread_id)


def _set_boundary(thread_id: str, compacted_through: int, mode: str,
                  summary: object = None) -> None:
    """Record a context cut: a `boundary` event plus the seq it cuts at."""
    folded = _fold(thread_id)
    if folded is None:
        return
    _meta, _msgs, seqs = folded
    ct = max(0, min(int(compacted_through), len(seqs)))
    boundary_seq = seqs[ct - 1] if ct > 0 else 0
    ev: dict = {"kind": "boundary", "mode": mode}
    if isinstance(summary, str) and summary:
        ev["summary"] = summary
    chat_log.append_sync(thread_id, ev)
    chat_log.patch_meta_sync(thread_id, boundary_seq=boundary_seq)


# ---- Runs ------------------------------------------------------------------
#
# A RUN IS NOT A MESSAGE. While a turn is in flight the log holds one
# `run: started` marker and `meta.active_run_id` points at it; the reply itself
# does not exist yet. What the turn has produced so far lives in
# `meta.live_message`, rewritten on a throttle (a turn writes it a few hundred
# times) and rendered by `_live_view` as a trailing assistant message.
#
# On finalize the reply is APPENDED at the tail, so it lands after every message
# the run was handed and before nothing. That is the whole ordering fix: the old
# arrangement claimed the reply's slot when the turn STARTED, so five messages
# sent during a twenty-minute turn were filed below the slot and the finished
# reply rendered above all five, reading as if the operator had been ignored.


def active_run_id(thread_id: str) -> str | None:
    """The id of the run in flight on this thread, or None. One field read.

    Distinct from `chat_log.active_run`, which returns the whole `{run_id,
    started_seq, live}` record the events endpoint hands a client. This is the
    one field every server-side caller actually wants."""
    meta = chat_log.read_meta(thread_id)
    return (meta or {}).get("active_run_id") or None


def begin_run(thread_id: str, run_id: str, model: str | None = None,
              agent: str | None = None) -> None:
    """Open a run on this thread: a `run: started` marker and `active_run_id`.

    Nothing is written to the transcript. The caller MUST hold this thread's
    write lock (`chat_log.lock_for`) across its own check-and-act, which is what
    makes two sends in the same event-loop tick unable to both open a run.

    `agent` names the agent answering when the turn came from an @mention, and
    rides on the live view so the reply is attributed while it streams, not only
    once it lands."""
    if chat_log.read_meta(thread_id) is None:
        raise KeyError(thread_id)
    meta: dict = {"turn_id": run_id, "segments": []}
    if model:
        meta["model"] = model
    if agent:
        meta["agent"] = agent
    started = chat_log.append_sync(
        thread_id, {"kind": "run", "run_id": run_id, "state": "started"})
    chat_log.patch_meta_sync(
        thread_id, active_run_id=run_id,
        # WHERE THE RUN STARTED, recorded rather than searched for. It is the one
        # thing `active_run` needed the whole log for: it scanned every event
        # backwards looking for this marker, on every 10s poll from every open
        # tab. The writer knows the answer for free. Threads written before this
        # existed have no such field, and `active_run` still falls back to the
        # scan for them, so nothing has to be migrated.
        active_run_seq=started.get("seq"),
        live_message={"run_id": run_id, "content": "", "meta": meta, "ts": _now_iso()},
    )


def update_run(thread_id: str, run_id: str, segments: list[dict], content: str) -> None:
    """Overwrite the running turn's live snapshot. No-op if it is not the active
    run any more (it finished, or something took the thread over).

    Writes meta, never the log: this fires every ~0.4s for the length of a turn,
    and the record should carry the reply, not every draft of it."""
    stored = chat_log.read_meta(thread_id)
    if stored is None or stored.get("active_run_id") != run_id:
        return
    live = stored.get("live_message")
    live = dict(live) if isinstance(live, dict) else {"run_id": run_id, "ts": _now_iso()}
    meta = dict(live.get("meta") or {})
    meta["segments"] = segments
    meta["turn_id"] = run_id
    live["meta"] = meta
    live["content"] = content
    chat_log.patch_meta_sync(thread_id, live_message=live)


def commit_run(
    thread_id: str,
    run_id: str,
    segments: list[dict],
    content: str,
    error: bool = False,
    timed_out: bool = False,
    notes: list[str] | None = None,
    stopped: str | None = None,
    agent: str | None = None,
    state: str = "finished",
) -> dict | None:
    """Commit a run: one assistant message at the TAIL, one `run` marker, done.

    Appending at the tail is the point. A message the operator sent during this
    run is already in the log ABOVE this reply, because it was persisted when it
    arrived and this reply did not exist yet. A message that arrived too late to
    be delivered lands BELOW, because it will be appended after. The transcript
    therefore says which messages the reply accounted for, without anyone having
    to annotate it.

    Committed whether or not a client is watching, and idempotent on the thread's
    view of the world: it clears `active_run_id` only when it still names this
    run, so a run that was taken over cannot un-claim its successor.

    `timed_out` flags a turn that hit its wall-clock limit (vs a hard error) so
    the UI can render a calm "stopped, continue" note instead of a red error.
    `stopped` names any OTHER non-completion ending — STOPPED_BY_OPERATOR when
    the operator pressed Stop, STOPPED_INTERRUPTED when a restart orphaned the
    run. `notes` carries the action pipeline's human-readable results (e.g.
    "Updated the plan")."""
    stored = chat_log.read_meta(thread_id)
    if stored is None:
        return None
    meta: dict = {"turn_id": run_id, "streaming": False, "ended_at": _now_iso()}
    if segments:
        meta["segments"] = segments
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
    if agent:
        meta["agent"] = agent
    ev = chat_log.append_message_sync(thread_id, "assistant", content, meta=meta,
                                      ts=_now_iso())
    chat_log.append_sync(thread_id, {"kind": "run", "run_id": run_id, "state": state})
    clear: dict = {}
    if stored.get("active_run_id") == run_id:
        clear["active_run_id"] = None
        clear["active_run_seq"] = None
        clear["live_message"] = None
    if clear:
        chat_log.patch_meta_sync(thread_id, **clear)
    return _msg_from_event(ev)


# The two ways a run ends without finishing, as `meta.stopped` on the committed
# message. They are different events and the transcript says which: the operator
# pressed Stop, or the backend went down under a run and nothing was left to
# answer for it. One value each, so the client has one thing to switch on and no
# reason to infer it from anything else.
STOPPED_BY_OPERATOR = "operator"
STOPPED_INTERRUPTED = "interrupted"


# The one wording for a run this backend can no longer account for. Used by the
# startup reconcile and by the re-attach path, so the operator reads the same
# sentence however the run was lost.
LOST_RUN_TEXT = (
    "Lost when the backend restarted mid-run. Anything already written to "
    "disk is intact. Send \"continue\" to pick it back up."
)


def lose_run(thread_id: str, run_id: str) -> dict | None:
    """Close out a run whose process is dead or gone: a `run: lost` marker and a
    message saying so, so the chat is never left waiting on something that will
    never answer.

    Marked `stopped=interrupted`, which is the value the client's tag has always
    been written for and which nothing had set since the per-read self-heal was
    deleted. Without it this renders as an ordinary reply that happens to be
    about a restart; with it, it reads as what it is."""
    return commit_run(thread_id, run_id, [], LOST_RUN_TEXT, state="lost",
                      stopped=STOPPED_INTERRUPTED)


def release_run(thread_id: str, run_id: str) -> None:
    """Close a run that never got off the ground, writing no message.

    A claim is taken before the prompt is assembled, because assembly awaits and
    an await is where a rival turn used to slip in. If assembly then fails, the
    caller has an error to return and the thread has a claim to give back; there
    is nothing to say in the transcript that the failed request does not already
    say to the person who made it."""
    stored = chat_log.read_meta(thread_id)
    if stored is None or stored.get("active_run_id") != run_id:
        return
    chat_log.append_sync(thread_id, {"kind": "run", "run_id": run_id, "state": "finished"})
    chat_log.patch_meta_sync(thread_id, active_run_id=None, active_run_seq=None,
                             live_message=None)


def previous_run_id(thread_id: str) -> str | None:
    """The last run this thread finished (or lost). Not the active one.

    This is what `chat_context.arrival_notice` asks for: "which messages arrived
    while the last run was going" is then a lookup on `meta.during_run`, not an
    inference from where a message sits relative to the last assistant reply.
    That inference is what broke the moment the reply started being appended at
    the tail."""
    for ev in reversed(chat_log.read_events(thread_id)):
        if ev.get("kind") == "run" and ev.get("state") in ("finished", "lost"):
            return ev.get("run_id")
    return None


# ---- context accounting ----------------------------------------------------


def set_thread_context_tokens(thread_id: str, tokens: int) -> dict | None:
    """Persist the real context occupancy (input + cached input tokens) the model
    reported on the latest turn. The composer meter prefers this over estimating
    from the stored transcript, because it reflects the live window and drops
    after an in-session /compact. Stored at the thread level (current occupancy,
    not per-message)."""
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
    that merely share a participant."""
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
    the session is gone from disk or a resume failed."""
    if chat_log.read_meta(thread_id) is None:
        return None
    return _patch(thread_id, claude_session=None)


def clear_thread_context(thread_id: str) -> tuple[dict, int] | None:
    """Drop everything this thread would send to a model, keeping the transcript.

    The counterpart to compaction's fold, and deliberately the same boundary:
    "no longer in the prompt" either way. A clear is a fold with nothing
    preserved — no `summary` survives it, and `context_cut` records that
    explicitly so the client never has to infer it from a summary that a fold
    would only write ~30s later.

    Also drops the live claude session, the per-@mentioned-agent sessions, and
    `turn_marks`: all three are "what this thread has already been told", and a
    clear means none of it has. Raw messages are untouched — reversible, same
    as a fold."""
    folded = _fold(thread_id)
    if folded is None:
        return None
    meta, msgs, seqs = folded
    ct = _compacted_through(seqs, int(meta.get("boundary_seq") or 0))
    cleared = max(0, len(msgs) - ct)
    if cleared == 0:
        return _document(meta, msgs, seqs), 0
    _set_boundary(thread_id, len(msgs), "clear")
    _patch(thread_id, context_cut="cleared", summary=None, context_tokens=None,
           claude_session=None, agent_sessions=None, turn_marks=None)
    return get_thread(thread_id), cleared


def rename_thread(thread_id: str, title: str, source: str = "manual") -> dict | None:
    """Name the thread. `source` records who named it (the operator, or the
    auto-namer) as a `title` event, so the log says how it got its name."""
    if chat_log.read_meta(thread_id) is None:
        return None
    clean = title.strip()[:80] or "New chat"
    chat_log.append_sync(thread_id, {"kind": "title", "title": clean, "source": source})
    return _patch(thread_id, title=clean)
