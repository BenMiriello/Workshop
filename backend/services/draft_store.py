"""Chat composer drafts — the unsent text in a composer, owned by the server.

A draft belongs to a CONVERSATION, and the key says which one:

    "<context_key>::<thread_id>"   the composer of an existing chat
    "<context_key>::new"           the composer of a chat that does not exist yet

Those are the only two shapes. The second is deliberately shared by every new
chat started on that surface, because "I typed something into a blank chat, went
away, and came back to it" is the one case where carrying text forward is what
the operator wants. It is NOT shared with any real thread, and no thread's draft
is ever shared with another.

### Who clears a draft

THE SERVER, when it accepts the message that was typed. This is the fix for
"I sent it and it stayed in the box", and for its stranger cousin: type into a
blank chat, send, and find the same text waiting the next time you start a new
chat. The browser used to clear the draft under whatever key the composer held
AFTER the send — and sending the first message of a blank chat CHANGES that key,
because the chat has just become a thread. So the clear landed on `::<new id>`,
which was empty anyway, and `::new` kept the text forever. Every later blank
chat then adopted it.

The client says which key it was typing under when it pressed Send, the server
clears exactly that one, and the answer the client renders comes back from here.
A race between the clear and a keystroke's own debounced write cannot resurrect
anything, because of the tombstone below.

### Tombstones

Clearing stores `{"text": "", "ts": <when>}` rather than deleting the row. The
timestamp is the whole point: a PUT that was already in flight when the clear
happened arrives afterwards carrying an OLDER `ts`, and is rejected on ordering.
Deleting the row threw that ordering away, so the late write looked like the
first thing anyone had ever said about this key and was stored — which is a sent
message reappearing in the composer seconds later, one of the reported quirks.

Empty rows are pruned once they are older than `TOMBSTONE_TTL_S`, by which time
no write from before the clear can still be in flight.

Last-write-wins by the client's wall clock. Single-user box: the alternative
(server-assigned versions) buys nothing here, because the only writer that
matters for a given key is the composer the operator is actually typing in.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from . import ownership

# How long a cleared draft keeps its timestamp. Long enough that no PUT issued
# before the clear can still land (the client debounces by 600ms and retries
# nothing), short enough that the file does not accumulate dead keys.
TOMBSTONE_TTL_S = 300

NEW_SUFFIX = "::new"


def _path() -> Path:
    return ownership.user_runtime_dir() / "chat" / "drafts.json"


def _load() -> dict:
    try:
        return json.loads(_path().read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save(data: dict) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.replace(p)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _prune(data: dict) -> bool:
    """Drop tombstones nothing can still be racing. Returns whether it changed."""
    cutoff = _now_ms() - TOMBSTONE_TTL_S * 1000
    dead = [k for k, v in data.items()
            if not (v or {}).get("text") and not (v or {}).get("attachments")
            and int((v or {}).get("ts", 0)) < cutoff]
    for k in dead:
        data.pop(k, None)
    return bool(dead)


def get_draft(key: str) -> dict:
    entry = _load().get(key)
    if not entry:
        return {"key": key, "text": "", "ts": 0, "client_id": ""}
    return {"key": key, **entry}


def put_draft(key: str, text: str, ts: int, client_id: str,
              attachments: list[dict] | None = None) -> dict:
    """Store (or clear, when there is neither text nor a file) the draft.

    A DRAFT IS EVERYTHING IN THE COMPOSER, which includes the files attached to
    it. The bytes were uploaded the moment they were dropped, so they were
    already safe; what was not was the FACT that they belong to this composer,
    which lived in React state alone. Reload with a screenshot attached and the
    text came back while the screenshot did not, leaving an orphan on disk and a
    message about a file that is no longer there.

    Returns the authoritative entry — which is the EXISTING one when it is
    strictly newer, marked `superseded`, so the caller can reconcile instead of
    believing a write that did not happen."""
    ts = int(ts) or _now_ms()
    data = _load()
    existing = data.get(key)
    if existing and int(existing.get("ts", 0)) > ts:
        return {"key": key, **existing, "superseded": True}
    entry = {"text": text or "", "ts": ts, "client_id": client_id}
    if attachments:
        entry["attachments"] = attachments
    data[key] = entry
    _prune(data)
    _save(data)
    return {"key": key, **entry}


def clear_draft(key: str, ts: int | None = None) -> dict:
    """Clear one draft, leaving a tombstone. This is what a SEND does."""
    return put_draft(key, "", int(ts or _now_ms()), "server")


def clear_for_thread(context_key: str | None, thread_id: str) -> None:
    """Every draft belonging to a conversation that is going away.

    The thread's own key, and — because a thread's first message may still be
    sitting in the blank-chat slot it was typed into — that slot too, but only
    when the caller knows the surface it was started from.
    """
    ts = _now_ms()
    data = _load()
    keys = [k for k in data if k.endswith(f"::{thread_id}")]
    for k in keys:
        data[k] = {"text": "", "ts": ts, "client_id": "server"}
    if keys or context_key:
        _prune(data)
        _save(data)
