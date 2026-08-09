"""Chat thread + message persistence — the generalized history layer.

App-agnostic: a thread carries a `context_key` (e.g. "budget", "general") that
tells the chat route which context provider to wire in, but the store itself
knows nothing about any app. One JSON file per thread under
`~/workshop/.runtime/chat/`. Single-user box, low concurrency — atomic
write-temp-then-rename is all the safety this needs.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import ownership

# Threads are partitioned per owner: `.runtime/users/<owner_id>/chat/<id>.json`.


def _chat_dir() -> Path:
    return ownership.user_runtime_dir() / "chat"


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


def _path(thread_id: str) -> Path:
    # Thread ids are uu4 hex; reject anything that isn't to keep this off the
    # filesystem's path-traversal surface.
    if not thread_id or not all(c in "0123456789abcdef-" for c in thread_id):
        raise ValueError(f"bad thread id: {thread_id!r}")
    return _chat_dir() / f"{thread_id}.json"


def _promote_agent_scope_if_legacy(thread: dict) -> None:
    """P5: quietly migrate a legacy `agent:<slug>` (or `build:<slug>`) chat to
    workshop or project scope with the agent captured as a participant.

    An agent chat under the old model had context_key = agent:<slug> and no
    participants list. Under the new model an agent is a PARTICIPANT of a
    chat that lives in workshop / project / app scope. Rewrite in place on
    write so the store gradually drifts to the new shape without a data-
    migration pass; if the resolver ever fails, the pre-promotion shape
    keeps working (P1 back-fill).

    Never touches a chat whose participants have been explicitly diverged
    from the context_key (scope_locked = True, or a participants list that
    is empty or has more than one entry, or an entry that doesn't match).
    That way an operator-authored intentional agent:<slug>-scope chat stays
    as-is."""
    ck = thread.get("context_key", "")
    if ":" not in ck:
        return
    kind, rest = ck.split(":", 1)
    if kind not in ("agent", "build"):
        return
    if thread.get("scope_locked"):
        return
    parts = _normalize_participants(thread.get("participants") or [])
    canon = f"agent:{rest}"
    # Only promote when the participant list is exactly [that agent] (or the
    # freshly back-filled derivation). Anything else was set on purpose.
    if parts and parts != [canon]:
        return
    thread["participants"] = [canon]
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
        thread["context_key"] = f"project:{project_slug}"
        thread["scope"] = f"project:{project_slug}"
    else:
        thread["context_key"] = "general"
        thread["scope"] = "workshop"


class LostUpdate(RuntimeError):
    """A write would have dropped messages that reached disk after it was read.

    See `_write`. This is a bug in the caller, never a condition to handle: the
    right response is to stop writing back a stale snapshot, not to retry.
    """


def _write(thread: dict) -> dict:
    _chat_dir().mkdir(parents=True, exist_ok=True)
    _promote_agent_scope_if_legacy(thread)
    p = _path(thread["id"])

    # THE ONE INVARIANT: A THREAD NEVER LOSES A MESSAGE.
    #
    # Every mutation here is read-modify-write of the whole file. That is fine
    # while the gap between read and write holds no `await` — this backend is one
    # event loop, so such a window is atomic. It is NOT fine when a caller reads a
    # thread, awaits something slow, and writes the snapshot back: everything
    # appended in between is silently erased.
    #
    # That was not hypothetical. `/compact` read the thread, spent ~30s in a
    # summarization call, then saved the snapshot — so a message typed during the
    # fold was deleted from disk. Reproduced: 11 messages written, 10 survived,
    # and the one that vanished was the operator's. The same shape existed on the
    # fresh-session turn path, where threshold compaction could erase the very
    # message that started the turn.
    #
    # The fix is `update_thread` (write the fields you own, never the document).
    # This check is what keeps it fixed: a shrinking transcript is always a lost
    # update, because nothing in this store ever removes a message — deleting a
    # chat removes the file. So refuse it loudly rather than losing it quietly.
    try:
        prior = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        prior = None
    if isinstance(prior, dict):
        on_disk = len(prior.get("messages") or [])
        now = len(thread.get("messages") or [])
        if now < on_disk:
            raise LostUpdate(
                f"refusing to write thread {thread['id']}: {on_disk} messages on disk, "
                f"{now} in the dict being written. This snapshot was read before "
                f"{on_disk - now} message(s) arrived; use update_thread() to write "
                f"individual fields instead of saving a stale thread."
            )

    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(thread, indent=2) + "\n")
    tmp.replace(p)
    return thread


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
    thread = {
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
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "messages": [],
    }
    return _write(thread)


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
    thread = get_thread(thread_id)
    if thread is None:
        return None
    thread["scope"] = scope
    if scope_locked is not None:
        thread["scope_locked"] = bool(scope_locked)
    thread["updated_at"] = _now_iso()
    return _write(thread)


def set_thread_mode(thread_id: str, mode: str) -> dict | None:
    """Set the per-thread interaction mode. `mode` must be one of CHAT_MODES;
    anything else clamps to DEFAULT_CHAT_MODE."""
    thread = get_thread(thread_id)
    if thread is None:
        return None
    thread["mode"] = _clamp_mode(mode)
    thread["updated_at"] = _now_iso()
    return _write(thread)


def set_thread_participants(thread_id: str, participants: list[str]) -> dict | None:
    """Overwrite the participants list. Normalized on write; empty list is a
    valid "no agent in this chat" state. C11/P1: agents are participants of a
    chat, not the chat's home. Returns updated thread or None if missing."""
    thread = get_thread(thread_id)
    if thread is None:
        return None
    thread["participants"] = _normalize_participants(participants)
    thread["updated_at"] = _now_iso()
    return _write(thread)


def add_thread_participant(thread_id: str, participant: str) -> dict | None:
    """Add one participant to a thread. Idempotent (already-present is a no-op).
    Namespaces a bare slug to `agent:<slug>` for consistency with the store."""
    thread = get_thread(thread_id)
    if thread is None:
        return None
    parts = _normalize_participants(thread.get("participants") or [])
    canon = _normalize_participants([participant])
    for c in canon:
        if c not in parts:
            parts.append(c)
    thread["participants"] = parts
    thread["updated_at"] = _now_iso()
    return _write(thread)


def remove_thread_participant(thread_id: str, participant: str) -> dict | None:
    """Remove one participant. Idempotent (missing is a no-op)."""
    thread = get_thread(thread_id)
    if thread is None:
        return None
    canon = _normalize_participants([participant])
    if not canon:
        return thread
    target = canon[0]
    thread["participants"] = [p for p in _normalize_participants(thread.get("participants") or []) if p != target]
    thread["updated_at"] = _now_iso()
    return _write(thread)


def set_thread_model(thread_id: str, model: str | None) -> dict | None:
    """Persist the per-thread model preference (full model id, or None to clear).
    Returns the updated thread, or None if the thread is missing."""
    thread = get_thread(thread_id)
    if thread is None:
        return None
    thread["model"] = model
    thread["updated_at"] = _now_iso()
    return _write(thread)


def get_thread(thread_id: str) -> dict | None:
    p = _path(thread_id)
    if not p.exists():
        return None
    try:
        t = json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(t, dict):
        return None
    # Back-fill the entity-model fields on read for legacy threads (P1 / C11).
    # Persisted-vs-derived is not observable to callers: an unset stored field
    # reads as the derived value. The next write persists the derived value.
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
    return t


def list_threads(context_key: str | None = None) -> list[dict]:
    """Summaries (no message bodies), newest first."""
    if not _chat_dir().exists():
        return []
    out: list[dict] = []
    for p in _chat_dir().glob("*.json"):
        # The composer-draft store lives alongside threads (chat/drafts.json); it is
        # not a thread. Skip it, and skip any file that isn't a well-formed thread
        # (missing id / partial write) so ONE bad file can't 500 the whole list.
        if p.name in ("drafts.json", _PREFS_FILE):
            continue
        try:
            t = json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(t, dict) or "id" not in t:
            continue
        if context_key and t.get("context_key") != context_key:
            continue
        msgs = t.get("messages", [])
        title = t.get("title", "New chat")
        # When the OPERATOR last spoke in this chat. This is what orders the
        # list, not `updated_at`: a turn that finishes, an agent that reports
        # back, or a background write all bump updated_at, so a chat the
        # operator has not touched in days could sit above the one they were
        # just in. The question the list answers is "where was I", and only a
        # user message answers it. Threads with no user message yet fall back to
        # when they were created, so a brand-new chat still sorts to the top.
        last_user_at = next(
            (m.get("ts") for m in reversed(msgs)
             if isinstance(m, dict) and m.get("role") == "user" and m.get("ts")),
            None,
        )
        # Until auto-naming runs (or if it never can), show the first-user-message
        # seed instead of a bare "New chat".
        if title in (None, "", "New chat") and t.get("_seed_title"):
            title = t["_seed_title"]
        last = msgs[-1] if msgs else None
        ck = t.get("context_key", "general")
        out.append({
            "id": t["id"],
            "context_key": ck,
            # Entity-model 2026-07-18 §7 fields (C11, P1). Existing threads
            # without stored scope/mode/participants lift them from the legacy
            # context_key so a data migration isn't needed; new writes persist
            # them explicitly.
            "scope": t.get("scope") or _scope_from_context_key(ck),
            "scope_locked": bool(t.get("scope_locked", False)),
            "mode": t.get("mode") or DEFAULT_CHAT_MODE,
            "participants": _normalize_participants(t.get("participants"))
                if t.get("participants") is not None
                else _participants_from_context_key(ck),
            "agent_id": t.get("agent_id"),
            "model": t.get("model"),
            "title": title,
            "created_at": t.get("created_at"),
            "updated_at": t.get("updated_at"),
            "last_user_at": last_user_at,
            "message_count": len(msgs),
            "last_message": ((last.get("content") or "")[:120] if isinstance(last, dict) else ""),
        })
    out.sort(key=_recency_key, reverse=True)
    return out


def _recency_key(summary: dict) -> str:
    """Order by when the operator last spoke, falling back to creation. Never
    updated_at — see the note on last_user_at."""
    return summary.get("last_user_at") or summary.get("created_at") or ""


def append_message(thread_id: str, role: str, content: str, meta: dict | None = None) -> dict:
    thread = get_thread(thread_id)
    if thread is None:
        raise KeyError(thread_id)
    msg = {"role": role, "content": content, "ts": _now_iso()}
    if meta:
        msg["meta"] = meta
    thread["messages"].append(msg)
    thread["updated_at"] = _now_iso()
    # Title is left as the "New chat" seed here; the chat route generates a
    # proper short title via a cheap model call after the first assistant reply
    # (see routes/chat._maybe_autoname). The thread-list summary already carries
    # a `last_message` snippet, so an interim snippet-title is redundant. If a
    # thread somehow never gets named (model unavailable), fall back to a first-
    # user-message snippet so the list never shows a wall of "New chat".
    # FIRST user message only. Rewriting it on every message made the chat list
    # show whatever the operator said most recently as the chat's name, which
    # reads as the title being the last message rather than a subject — and it
    # changed under them each turn. It is a fallback for a chat that could not be
    # named, so it must be as stable as the name it stands in for.
    if role == "user" and not thread.get("_seed_title") \
            and thread.get("title") in (None, "", "New chat"):
        thread["_seed_title"] = content.strip().split("\n")[0][:60] or "New chat"
    _write(thread)
    return msg


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
    """
    thread = get_thread(thread_id)
    if thread is None:
        raise KeyError(thread_id)
    msg = {"role": role, "content": content, "ts": _now_iso()}
    if meta:
        msg["meta"] = meta
    thread["messages"].append(msg)
    thread["updated_at"] = _now_iso()
    _write(thread)
    return msg


def save_thread(thread: dict) -> dict:
    """Persist a thread dict the caller already mutated (atomic write).

    Only safe when the caller read this dict and mutated it with no `await` in
    between. If anything slow happened in the gap, use `update_thread` instead —
    `_write` will raise `LostUpdate` rather than let a stale snapshot silently
    delete messages that arrived meanwhile."""
    thread["updated_at"] = _now_iso()
    return _write(thread)


def update_thread(thread_id: str, **fields: object) -> dict | None:
    """Apply named top-level fields to a thread, re-reading it first.

    THE WAY TO WRITE AFTER AN AWAIT. A caller that reads a thread, does something
    slow, and then wants to record a result must not save the document it is
    holding — the transcript has moved on. It writes the fields it owns, onto
    whatever the thread is NOW.

    `messages` is not writable here, deliberately: appending is the store's own
    job (`append_message`, `append_message_during_turn`), and every corruption
    this function exists to prevent came from someone writing that list back.

    A field set to `None` is REMOVED, which is how a caller unsets one (e.g.
    compaction dropping the stale `context_tokens` measurement)."""
    if "messages" in fields:
        raise ValueError("update_thread cannot write `messages`; append instead")
    thread = get_thread(thread_id)
    if thread is None:
        return None
    for k, v in fields.items():
        if v is None:
            thread.pop(k, None)
        else:
            thread[k] = v
    thread["updated_at"] = _now_iso()
    return _write(thread)


# ---- Streaming assistant turns --------------------------------------------
# A Build turn streams over SSE. We persist its assistant message incrementally
# (ordered text/tool segments) so a reload mid-turn shows what's there so far
# and can reconnect, instead of a blank gap that snaps to one aggregate block.

def _find_by_turn(thread: dict, turn_id: str) -> dict | None:
    for m in reversed(thread.get("messages", [])):
        if (m.get("meta") or {}).get("turn_id") == turn_id:
            return m
    return None


def append_streaming_assistant(thread_id: str, turn_id: str, model: str | None = None,
                               agent: str | None = None) -> dict:
    """Append an empty assistant message marked as the live turn `turn_id`.

    `agent` names the agent answering when the turn came from an @mention. It is
    stamped on the PLACEHOLDER as well as the finalized message so the reply is
    attributed while it streams, not only once it lands."""
    thread = get_thread(thread_id)
    if thread is None:
        raise KeyError(thread_id)
    meta = {"streaming": True, "turn_id": turn_id, "segments": []}
    if model:
        meta["model"] = model
    if agent:
        meta["agent"] = agent
    msg = {
        "role": "assistant",
        "content": "",
        "ts": _now_iso(),
        "meta": meta,
    }
    thread["messages"].append(msg)
    thread["updated_at"] = _now_iso()
    _write(thread)
    return msg


def update_streaming_assistant(
    thread_id: str, turn_id: str, segments: list[dict], content: str
) -> None:
    """Overwrite the live turn's segments + concatenated text (throttled by the
    caller). No-op if the thread/message is gone."""
    thread = get_thread(thread_id)
    if thread is None:
        return
    m = _find_by_turn(thread, turn_id)
    if m is None:
        return
    m["content"] = content
    meta = m.setdefault("meta", {})
    meta["segments"] = segments
    meta["streaming"] = True
    save_thread(thread)


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
    """Mark the live turn complete (streaming=False) with its final segments.
    If the placeholder message is missing, append a finished one instead.

    `timed_out` flags a turn that hit its wall-clock limit (vs a hard error) so
    the UI can render a calm "stopped — continue" note instead of a red error.
    `stopped` names any OTHER non-completion ending, so a turn that
    was cut off mid-task is never rendered as a turn that finished.
    `notes` mirrors the blocking path's meta.notes (e.g. "Updated the plan" from
    an action provider) so a streamed action turn renders the same follow-ups."""
    thread = get_thread(thread_id)
    if thread is None:
        return None
    m = _find_by_turn(thread, turn_id)
    if m is None:
        # No placeholder to update, so this message is born finished and `ts` is
        # already the end. Set `ended_at` anyway: a consumer that has to ask
        # "does this one have an end?" before reading it is a consumer that will
        # eventually forget to, and the whole point of recording the interval is
        # that every finalized assistant message answers when it finished.
        meta: dict = {"ended_at": _now_iso()}
        if segments:
            meta["segments"] = segments
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
        return append_message(thread_id, "assistant", content, meta=meta or None)
    m["content"] = content
    meta = m.setdefault("meta", {})
    meta["segments"] = segments
    meta["streaming"] = False
    # A TURN IS AN INTERVAL. `ts` is when this reply began — the placeholder is
    # appended at turn start and `ts` is never touched again — so on a twenty
    # minute turn every assistant message claimed it was produced at the instant
    # the turn opened, and a message the operator sent at minute twelve sorted
    # ABOVE a timestamp eleven minutes older than its own. Recording the end is
    # what makes "when did this happen" answerable at all.
    meta["ended_at"] = _now_iso()
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
    save_thread(thread)
    return m


def set_thread_context_tokens(thread_id: str, tokens: int) -> dict | None:
    """Persist the real context occupancy (input + cached input tokens) the model
    reported on the latest turn. The composer meter prefers this over estimating
    from the stored transcript, because it reflects the live window and drops
    after an in-session /compact. Stored at the thread level (current occupancy,
    not per-message)."""
    thread = get_thread(thread_id)
    if thread is None:
        return None
    thread["context_tokens"] = max(0, int(tokens))
    return _write(thread)


def clear_thread_context_tokens(thread_id: str) -> dict | None:
    """Forget the measured context occupancy.

    Called when something has just shrunk the live window (an in-session
    /compact) but no fresh measurement came back with it. Absent the key the
    meter falls back to estimating from the compacted prompt, which is right;
    keeping a stale pre-compact number is not."""
    thread = get_thread(thread_id)
    if thread is None:
        return None
    if thread.pop("context_tokens", None) is None:
        return thread
    return _write(thread)


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
    thread = get_thread(thread_id)
    if thread is None:
        return None
    marks = thread.get("turn_marks")
    marks = dict(marks) if isinstance(marks, dict) else {}
    changed = False
    for k, v in fields.items():
        if marks.get(k) != v:
            marks[k] = v
            changed = True
    if not changed:
        return thread
    thread["turn_marks"] = marks
    return _write(thread)


def set_card_state(thread_id: str, key: str, state: dict) -> dict | None:
    """Persist the outcome of acting on one in-chat ui-card, keyed by a digest of
    the card block. Stored on the thread because it is a fact about this
    conversation, and because the operator hitting "run" on one device should not
    see that same card offering to run again on another."""
    thread = get_thread(thread_id)
    if thread is None:
        return None
    states = thread.setdefault("card_states", {})
    states[str(key)[:128]] = {**state, "ts": _now_iso()}
    return _write(thread)


def ensure_thread_session(thread_id: str) -> tuple[str, bool] | None:
    """Return (session_id, is_new) for this thread's persistent claude session.

    Every chat is a conversation, so every chat gets ONE session that the CLI
    carries across turns. Before this, only build-agent chats had a session and
    every other chat re-spawned stateless: each turn re-derived what was going on
    from a 24-message text transcript, so a chat mid-task visibly "started from
    scratch" every time and repeated the discovery work it had already done.

    is_new tells the caller whether this turn passes --session-id (mint) or
    --resume (continue)."""
    thread = get_thread(thread_id)
    if thread is None:
        return None
    existing = thread.get("claude_session")
    if existing:
        return existing, False
    sid = str(uuid.uuid4())
    thread["claude_session"] = sid
    _write(thread)
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
    thread = get_thread(thread_id)
    if thread is None:
        return None
    sessions = thread.get("agent_sessions")
    sessions = dict(sessions) if isinstance(sessions, dict) else {}
    existing = sessions.get(slug)
    if existing:
        return existing, False
    sid = str(uuid.uuid4())
    sessions[slug] = sid
    thread["agent_sessions"] = sessions
    _write(thread)
    return sid, True


def clear_agent_session(thread_id: str, slug: str) -> dict | None:
    """Forget one agent's session in this thread, so its next mention starts
    fresh. The counterpart to clear_thread_session, for the same reason: a
    session id whose CLI session is gone would fail the whole turn on --resume."""
    thread = get_thread(thread_id)
    if thread is None:
        return None
    sessions = thread.get("agent_sessions")
    if not isinstance(sessions, dict) or slug not in sessions:
        return thread
    sessions = dict(sessions)
    sessions.pop(slug, None)
    thread["agent_sessions"] = sessions
    return _write(thread)


def clear_thread_session(thread_id: str) -> dict | None:
    """Detach the thread from its claude session so the next turn starts a fresh
    one (and re-sends the assembled history, so nothing is forgotten). Used when
    the session is gone from disk or a resume failed."""
    thread = get_thread(thread_id)
    if thread is None:
        return None
    thread.pop("claude_session", None)
    return _write(thread)


def clear_thread_context(thread_id: str) -> tuple[dict, int] | None:
    """Drop everything this thread would send to a model, keeping the transcript.

    The counterpart to compaction's fold, and deliberately the same boundary
    field: `compacted_through` means "no longer in the prompt" either way. A
    clear is a fold with nothing preserved — no `summary` survives it, and
    `context_cut` records that explicitly so the client never has to infer it
    from a summary that a fold would only write ~30s later.

    Also drops the live claude session, the per-@mentioned-agent sessions, and
    `turn_marks`: all three are "what this thread has already been told", and a
    clear means none of it has. Raw messages are untouched — reversible, same
    as a fold."""
    thread = get_thread(thread_id)
    if thread is None:
        return None
    msgs = thread.get("messages", [])
    ct = int(thread.get("compacted_through", 0) or 0)
    cleared = max(0, len(msgs) - max(ct, 0))
    if cleared == 0:
        return thread, 0
    thread["compacted_through"] = len(msgs)
    thread["context_cut"] = "cleared"
    for field in ("summary", "context_tokens", "claude_session", "agent_sessions", "turn_marks"):
        thread.pop(field, None)
    return _write(thread), cleared


def rename_thread(thread_id: str, title: str) -> dict | None:
    thread = get_thread(thread_id)
    if thread is None:
        return None
    thread["title"] = title.strip()[:80] or "New chat"
    thread["updated_at"] = _now_iso()
    return _write(thread)


def delete_thread(thread_id: str) -> bool:
    p = _path(thread_id)
    if p.exists():
        p.unlink()
        return True
    return False
