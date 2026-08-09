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
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import chat_log, ownership  # noqa: F401  (ownership re-exported for tests)

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
    try:
        data = json.loads(_prefs_path().read_text())
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def get_default_mode() -> str:
    """The mode a new chat starts in: the operator's setting, else the platform
    default. Clamped, so a hand-edited junk value can't create unreachable chats."""
    return _clamp_mode(_read_prefs().get("default_mode"))


def set_default_mode(mode: str) -> str:
    """Persist the default for new chats. Returns the stored (clamped) value."""
    value = _clamp_mode(mode)
    prefs = _read_prefs()
    prefs["default_mode"] = value
    p = _prefs_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(prefs, indent=2))
    tmp.replace(p)
    return value


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# Meta fields that belong to the log machinery, not to the thread document. They
# never appear in a thread dict a caller sees, so a caller can never write them
# back, and `save_thread` never mistakes their absence for a deletion.
_INTERNAL_META = ("last_seq", "boundary_seq", "active_run_id", "live_message")


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

    A `message` event carrying `revises` replaces the content and meta of the
    message at that seq IN PLACE — the revision never moves anything, which is
    what lets a streaming reply be finalized without jumping below the messages
    that arrived while it ran.

    `meta.live_message` is the in-flight overlay for the one message a running
    turn is writing. It is applied last and lives in meta rather than the log so
    a turn's throttled progress writes never touch the record; only the
    finalized text does.
    """
    meta = chat_log.read_meta(thread_id)
    if meta is None:
        return None
    messages: list[dict] = []
    seqs: list[int] = []
    index_of: dict[int, int] = {}
    for ev in chat_log.read_events(thread_id):
        if ev.get("kind") != "message":
            continue
        revises = ev.get("revises")
        if revises is not None:
            i = index_of.get(int(revises))
            if i is None:
                continue  # revises a message that isn't there; nothing to apply
            messages[i]["content"] = ev.get("content") or ""
            if ev.get("meta"):
                messages[i]["meta"] = dict(ev["meta"])
            else:
                messages[i].pop("meta", None)
            continue
        msg = {
            "role": ev.get("role") or "user",
            "content": ev.get("content") or "",
            "ts": ev.get("ts"),
        }
        if ev.get("meta"):
            msg["meta"] = dict(ev["meta"])
        index_of[ev["seq"]] = len(messages)
        messages.append(msg)
        seqs.append(ev["seq"])
    live = meta.get("live_message")
    if isinstance(live, dict) and int(live.get("seq") or 0) in index_of:
        i = index_of[int(live["seq"])]
        messages[i]["content"] = live.get("content") or ""
        if live.get("meta"):
            messages[i]["meta"] = dict(live["meta"])
    return meta, messages, seqs


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
    t["messages"] = messages
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
                               meta: dict | None = None) -> dict:
    """File a message that arrived WHILE a turn was running.

    A turn is an INTERVAL; a message is a POINT. A message sent at minute twelve
    of a twenty-minute reply is neither before that reply nor after it, and the
    transcript has to pick one. This used to pick *before* — it inserted above
    the streaming placeholder, reasoning that a plain append would file the
    operator's message after a reply that had not been written yet.

    That is backwards for whoever READS the thread later. It produces a
    transcript saying the operator spoke and then the assistant answered, when
    the assistant was already twelve minutes into answering something else; a
    fresh session re-assembling history reads the reply as a response to a
    message it never saw. Worse, the placeholder is stamped at turn START, so
    the operator's message also sorted ABOVE a timestamp 91 seconds EARLIER than
    its own. That is the "my message got inserted above compact" report, on disk.

    So: file it AFTER, which is causally true — the reply did not see it — and
    record what "after" leaves out:

      during_turn — the turn that was running when this arrived
      sent_at     — when the operator actually hit enter
      at_segment  — how much of that reply had streamed by then

    `at_segment` is what lets the transcript still SHOW it inline at the point it
    landed while the stored order stays causal. One truth, two views, instead of
    one view baked into storage as though it were the truth.

    An append is now the only way to write a message, so "after" is what the log
    does on its own; this stays a separate entry point because the CALLER means
    something different by it.
    """
    if chat_log.read_meta(thread_id) is None:
        raise KeyError(thread_id)
    ev = chat_log.append_message_sync(thread_id, role, content, meta=meta, ts=_now_iso())
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
    changed: new messages become `message` events, an edited message becomes a
    revision of the one it edits, changed top-level fields become a meta patch.

    A stale snapshot can no longer delete anything. Messages that arrived after
    the caller's read are simply not in its dict, and "not in the dict" is not an
    instruction the log can express. That is why `LostUpdate` is gone: it existed
    to refuse a whole-document write, and there is no longer such a thing.
    """
    tid = thread["id"]
    folded = _fold(tid)
    if folded is None:
        raise KeyError(tid)
    meta, stored_msgs, seqs = folded

    incoming = thread.get("messages") or []
    i = 0
    while i < min(len(incoming), len(stored_msgs)):
        mine, theirs = incoming[i], stored_msgs[i]
        if not _same_message(mine, theirs):
            break
        if mine.get("content") != theirs.get("content") \
                or (mine.get("meta") or {}) != (theirs.get("meta") or {}):
            _revise_message(tid, seqs[i], mine, meta)
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


def _revise_message(thread_id: str, seq: int, msg: dict, meta: dict) -> None:
    """Append a revision of the message at `seq`.

    A revision that targets the message a running turn is writing supersedes the
    live overlay, so the overlay and the run marker are cleared in the same
    breath — otherwise the overlay would keep painting over the revision."""
    chat_log.append_message_sync(thread_id, msg.get("role") or "assistant",
                                 msg.get("content") or "", meta=msg.get("meta"),
                                 ts=msg.get("ts"), revises=seq)
    live = meta.get("live_message")
    if isinstance(live, dict) and int(live.get("seq") or 0) == seq:
        chat_log.patch_meta_sync(thread_id, live_message=None, active_run_id=None)


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


# ---- Streaming assistant turns --------------------------------------------
# A Build turn streams over SSE. The reply's slot in the transcript is claimed
# when the turn starts, so a message the operator sends mid-turn files after it
# and the finished reply does not jump below its own follow-ups. What streams
# into that slot lives in `meta.live_message` until the turn finishes: a turn
# writes ~150 times, and none of those belong in the record.


def _find_by_turn(messages: list[dict], seqs: list[int], turn_id: str) -> tuple[dict, int] | None:
    for i in range(len(messages) - 1, -1, -1):
        if (messages[i].get("meta") or {}).get("turn_id") == turn_id:
            return messages[i], seqs[i]
    return None


def append_streaming_assistant(thread_id: str, turn_id: str, model: str | None = None,
                               agent: str | None = None) -> dict:
    """Claim this turn's slot in the transcript with an empty assistant message.

    `agent` names the agent answering when the turn came from an @mention. It is
    stamped on the PLACEHOLDER as well as the finalized message so the reply is
    attributed while it streams, not only once it lands."""
    if chat_log.read_meta(thread_id) is None:
        raise KeyError(thread_id)
    meta: dict = {"streaming": True, "turn_id": turn_id, "segments": []}
    if model:
        meta["model"] = model
    if agent:
        meta["agent"] = agent
    chat_log.append_sync(thread_id, {"kind": "run", "run_id": turn_id, "state": "started"})
    ev = chat_log.append_message_sync(thread_id, "assistant", "", meta=meta, ts=_now_iso())
    chat_log.patch_meta_sync(
        thread_id, active_run_id=turn_id,
        live_message={"seq": ev["seq"], "content": "", "meta": meta},
    )
    return _msg_from_event(ev)


def update_streaming_assistant(
    thread_id: str, turn_id: str, segments: list[dict], content: str
) -> None:
    """Overwrite the live turn's segments + concatenated text (throttled by the
    caller). No-op if the thread/message is gone.

    Writes the overlay, not the log: this fires every ~0.4s for the length of a
    turn, and the record should carry the reply, not every draft of it."""
    folded = _fold(thread_id)
    if folded is None:
        return
    _meta, msgs, seqs = folded
    hit = _find_by_turn(msgs, seqs, turn_id)
    if hit is None:
        return
    msg, seq = hit
    meta = dict(msg.get("meta") or {})
    meta["segments"] = segments
    meta["streaming"] = True
    chat_log.patch_meta_sync(
        thread_id, live_message={"seq": seq, "content": content, "meta": meta})


def finalize_streaming_assistant(
    thread_id: str,
    turn_id: str,
    segments: list[dict],
    content: str,
    error: bool = False,
    timed_out: bool = False,
    notes: list[str] | None = None,
    stopped: str | None = None,
    agent: str | None = None,
) -> dict | None:
    """Commit the live turn as its final message, in the slot it claimed.

    `timed_out` flags a turn that hit its wall-clock limit (vs a hard error) so
    the UI can render a calm "stopped, continue" note instead of a red error.
    `stopped` names any OTHER non-completion ending, so a turn that was cut off
    mid-task is never rendered as a turn that finished. `notes` mirrors the
    blocking path's meta.notes (e.g. "Updated the plan" from an action provider)
    so a streamed action turn renders the same follow-ups."""
    folded = _fold(thread_id)
    if folded is None:
        return None
    stored, msgs, seqs = folded
    hit = _find_by_turn(msgs, seqs, turn_id)

    def _finish_meta(base: dict) -> dict:
        # A TURN IS AN INTERVAL. `ts` is when this reply began — the slot is
        # claimed at turn start and `ts` is never touched again — so on a twenty
        # minute turn every assistant message claimed it was produced at the
        # instant the turn opened, and a message the operator sent at minute
        # twelve sorted ABOVE a timestamp eleven minutes older than its own.
        # Recording the end is what makes "when did this happen" answerable.
        m = dict(base)
        m["ended_at"] = _now_iso()
        if segments:
            m["segments"] = segments
        if error:
            m["error"] = True
        if timed_out:
            m["timeout"] = True
        if stopped:
            m["stopped"] = stopped
        if notes:
            m["notes"] = notes
        if agent:
            m["agent"] = agent
        return m

    if hit is None:
        # No slot to fill, so this message is born finished and `ts` is already
        # the end. Set `ended_at` anyway: a consumer that has to ask "does this
        # one have an end?" before reading it is a consumer that will eventually
        # forget to, and the whole point of recording the interval is that every
        # finalized assistant message answers when it finished.
        meta = _finish_meta({})
        return append_message(thread_id, "assistant", content, meta=meta or None)

    msg, seq = hit
    meta = _finish_meta(msg.get("meta") or {})
    meta["segments"] = segments
    meta["streaming"] = False
    chat_log.append_message_sync(thread_id, "assistant", content, meta=meta,
                                 ts=msg.get("ts"), revises=seq)
    chat_log.append_sync(thread_id, {"kind": "run", "run_id": turn_id, "state": "finished"})
    live = stored.get("live_message")
    clear: dict = {}
    if isinstance(live, dict) and int(live.get("seq") or 0) == seq:
        clear["live_message"] = None
    if stored.get("active_run_id") == turn_id:
        clear["active_run_id"] = None
    if clear:
        chat_log.patch_meta_sync(thread_id, **clear)
    out = {"role": "assistant", "content": content, "ts": msg.get("ts"), "meta": meta}
    return out


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
