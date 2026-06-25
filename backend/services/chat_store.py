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


def _write(thread: dict) -> dict:
    _chat_dir().mkdir(parents=True, exist_ok=True)
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
) -> dict:
    tid = uuid.uuid4().hex
    thread = {
        "id": tid,
        "context_key": context_key or "general",
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
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def list_threads(context_key: str | None = None) -> list[dict]:
    """Summaries (no message bodies), newest first."""
    if not _chat_dir().exists():
        return []
    out: list[dict] = []
    for p in _chat_dir().glob("*.json"):
        try:
            t = json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if context_key and t.get("context_key") != context_key:
            continue
        msgs = t.get("messages", [])
        title = t.get("title", "New chat")
        # Until auto-naming runs (or if it never can), show the first-user-message
        # seed instead of a bare "New chat".
        if title in (None, "", "New chat") and t.get("_seed_title"):
            title = t["_seed_title"]
        out.append({
            "id": t["id"],
            "context_key": t.get("context_key", "general"),
            "agent_id": t.get("agent_id"),
            "model": t.get("model"),
            "title": title,
            "created_at": t.get("created_at"),
            "updated_at": t.get("updated_at"),
            "message_count": len(msgs),
            "last_message": (msgs[-1]["content"][:120] if msgs else ""),
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
) -> dict | None:
    """Mark the live turn complete (streaming=False) with its final segments.
    If the placeholder message is missing, append a finished one instead.

    `timed_out` flags a turn that hit its wall-clock limit (vs a hard error) so
    the UI can render a calm "stopped — continue" note instead of a red error."""
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
        return append_message(thread_id, "assistant", content, meta=meta or None)
    m["content"] = content
    meta = m.setdefault("meta", {})
    meta["segments"] = segments
    meta["streaming"] = False
    if error:
        meta["error"] = True
    if timed_out:
        meta["timeout"] = True
    save_thread(thread)
    return m


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
