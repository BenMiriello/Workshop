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


def _write(thread: dict) -> dict:
    _chat_dir().mkdir(parents=True, exist_ok=True)
    _promote_agent_scope_if_legacy(thread)
    p = _path(thread["id"])
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
    mode: str = "orchestrated",
    participants: list[str] | None = None,
) -> dict:
    tid = uuid.uuid4().hex
    # Entity-model 2026-07-18 §7 fields (C11, P1):
    # - scope: the Scope this chat lives in (workshop / project:X / app:Y).
    # - scope_locked: refuses to re-scope silently; the UI must confirm.
    # - mode: orchestrated (the default; general flow) or direct (bypasses the
    #   orchestrator persona and talks straight to the assistant).
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
        "mode": mode if mode in ("orchestrated", "direct") else "orchestrated",
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
    """Set the per-thread interaction mode. `mode` must be `orchestrated` or
    `direct`; anything else clamps to `orchestrated`."""
    thread = get_thread(thread_id)
    if thread is None:
        return None
    thread["mode"] = mode if mode in ("orchestrated", "direct") else "orchestrated"
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
        t["mode"] = "orchestrated"
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
        if p.name == "drafts.json":
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
            "mode": t.get("mode") or "orchestrated",
            "participants": _normalize_participants(t.get("participants"))
                if t.get("participants") is not None
                else _participants_from_context_key(ck),
            "agent_id": t.get("agent_id"),
            "model": t.get("model"),
            "title": title,
            "created_at": t.get("created_at"),
            "updated_at": t.get("updated_at"),
            "message_count": len(msgs),
            "last_message": ((last.get("content") or "")[:120] if isinstance(last, dict) else ""),
        })
    out.sort(key=lambda t: t.get("updated_at") or "", reverse=True)
    return out


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
    if role == "user" and thread.get("title") in (None, "", "New chat"):
        thread["_seed_title"] = content.strip().split("\n")[0][:60] or "New chat"
    _write(thread)
    return msg


def insert_message_before_live_turn(thread_id: str, role: str, content: str,
                                    meta: dict | None = None) -> dict:
    """Append a message, but keep it ABOVE the in-flight assistant placeholder.

    A message sent while a turn is running is a real part of the conversation —
    it has to be in the transcript, or a fresh session re-assembling history
    from it would never know the operator said anything. But the live turn's
    assistant message is already the last entry, so a plain append would file
    the operator's message after a reply that had not been written yet, and the
    thread would read out of order forever after.

    Slots it in just before that placeholder instead. With no live placeholder
    this is an ordinary append."""
    thread = get_thread(thread_id)
    if thread is None:
        raise KeyError(thread_id)
    msg = {"role": role, "content": content, "ts": _now_iso()}
    if meta:
        msg["meta"] = meta
    msgs = thread["messages"]
    last = msgs[-1] if msgs else None
    if last and last.get("role") == "assistant" and (last.get("meta") or {}).get("streaming"):
        msgs.insert(len(msgs) - 1, msg)
    else:
        msgs.append(msg)
    thread["updated_at"] = _now_iso()
    _write(thread)
    return msg


def save_thread(thread: dict) -> dict:
    """Persist a thread dict the caller already mutated (atomic write)."""
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


def append_streaming_assistant(thread_id: str, turn_id: str, model: str | None = None) -> dict:
    """Append an empty assistant message marked as the live turn `turn_id`."""
    thread = get_thread(thread_id)
    if thread is None:
        raise KeyError(thread_id)
    meta = {"streaming": True, "turn_id": turn_id, "segments": []}
    if model:
        meta["model"] = model
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
) -> dict | None:
    """Mark the live turn complete (streaming=False) with its final segments.
    If the placeholder message is missing, append a finished one instead.

    `timed_out` flags a turn that hit its wall-clock limit (vs a hard error) so
    the UI can render a calm "stopped — continue" note instead of a red error.
    `stopped` names any OTHER non-completion ending ("max_steps"), so a turn that
    was cut off mid-task is never rendered as a turn that finished.
    `notes` mirrors the blocking path's meta.notes (e.g. "Updated the plan" from
    an action provider) so a streamed action turn renders the same follow-ups."""
    thread = get_thread(thread_id)
    if thread is None:
        return None
    m = _find_by_turn(thread, turn_id)
    if m is None:
        meta = {"segments": segments} if segments else {}
        if error:
            meta["error"] = True
        if timed_out:
            meta["timeout"] = True
        if stopped:
            meta["stopped"] = stopped
        if notes:
            meta["notes"] = notes
        return append_message(thread_id, "assistant", content, meta=meta or None)
    m["content"] = content
    meta = m.setdefault("meta", {})
    meta["segments"] = segments
    meta["streaming"] = False
    if error:
        meta["error"] = True
    if timed_out:
        meta["timeout"] = True
    if stopped:
        meta["stopped"] = stopped
    if notes:
        meta["notes"] = notes
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


def clear_thread_session(thread_id: str) -> dict | None:
    """Detach the thread from its claude session so the next turn starts a fresh
    one (and re-sends the assembled history, so nothing is forgotten). Used when
    the session is gone from disk or a resume failed."""
    thread = get_thread(thread_id)
    if thread is None:
        return None
    thread.pop("claude_session", None)
    return _write(thread)


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
