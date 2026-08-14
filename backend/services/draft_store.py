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

A tombstone is ALSO the only way to correct a surface that missed the clear, and
that is the job that sets how long it is kept. A second tab, a phone in a pocket,
a laptop asleep: each still holds the sent text, and each asks the server on
mount and on focus. A pruned row answers `ts: 0`, which loses the ordering
comparison, so the stale copy wins and keeps winning. Tombstones therefore
outlive any plausible absence rather than just the in-flight write, and rows
holding text are aged out too, so a key nothing reads any more cannot sit here
forever. See `TOMBSTONE_TTL_S` and `STALE_DRAFT_TTL_S`.

Last-write-wins by the client's wall clock. Single-user box: the alternative
(server-assigned versions) buys nothing here, because the only writer that
matters for a given key is the composer the operator is actually typing in.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from . import ownership

# How long a cleared draft keeps its timestamp.
#
# THIS IS NOT ABOUT THE IN-FLIGHT PUT, WHICH IS WHY IT IS NOT SECONDS. It was
# 300s, reasoned from the client's 600ms debounce: long enough that no write
# issued before the clear can still land. That is true and it is the wrong
# question, because the tombstone is also THE ONLY THING THAT CAN CORRECT A
# CLIENT THAT MISSED THE CLEAR.
#
# Reconciliation here is last-write-wins on the timestamp, and a pruned row
# reads back as `ts: 0`, which loses every comparison there is. So once the
# tombstone was gone, a surface still holding the sent text would ask the server,
# be told `ts: 0`, keep its own copy on the ordering rule, and go on showing an
# already-sent message forever. There was no path back: the client re-checks on
# mount and on focus, and both checks are the same losing comparison.
#
# That is the "I send a message and the text is still in the box days later"
# report, and every way of diverging landed in it: a second tab, a phone in a
# pocket, a laptop asleep past the window, a key written by an older version of
# the client. Five minutes is shorter than any of those.
#
# So a clear now outlives any plausible absence. An empty row is about sixty
# bytes and there is one per conversation, so keeping them is free; being unable
# to correct a client is not.
TOMBSTONE_TTL_S = 30 * 24 * 3600

# A draft WITH TEXT that nobody has touched in this long is dead, and is dropped
# on the next write.
#
# Pruning used to skip these entirely, so any row holding text lived forever.
# That is not hypothetical: this store was found holding `budget` -> "… oh I see"
# from ten days earlier, under a bare context key that no composer has written
# since per-chat draft keys landed (ec4db15). Nothing reads that key now, so
# nothing could ever clear it, and it would have sat there for the life of the
# install. Any key scheme that changes again leaves the same litter.
#
# Well beyond how long unsent text stays interesting, so this can only ever
# reclaim something abandoned.
STALE_DRAFT_TTL_S = 30 * 24 * 3600

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


def _is_empty(v: dict | None) -> bool:
    return not (v or {}).get("text") and not (v or {}).get("attachments")


def _prune(data: dict) -> bool:
    """Drop rows that can no longer do any work. Returns whether it changed.

    Two ages, because the two kinds of row are kept for different reasons. A
    tombstone is kept so a client that missed the clear can still be corrected,
    which is a question about how long a surface can be away. A draft with text
    is kept because it is the operator's unsent writing, and is only dropped once
    it is abandoned by any reasonable measure."""
    now = _now_ms()
    tomb_cutoff = now - TOMBSTONE_TTL_S * 1000
    stale_cutoff = now - STALE_DRAFT_TTL_S * 1000
    dead = [
        k for k, v in data.items()
        if int((v or {}).get("ts", 0)) < (tomb_cutoff if _is_empty(v) else stale_cutoff)
    ]
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
