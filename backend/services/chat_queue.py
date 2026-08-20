"""Messages the operator has sent that the thread cannot run yet.

THE CONTRACT THIS FILE EXISTS TO KEEP: a message the operator hit enter on is
the system's responsibility from the moment the server accepts it, and it is
visible from every device until it is answered or they take it back.

A chat runs one turn at a time, and some turns cannot be spoken to at all (a
slash command is executed by the CLI's command layer, which only runs between
turns; so is anything that reads no input). So there has always been a queue.
It lived in the BROWSER'S localStorage, keyed by thread, drained by whichever
tab happened to be looking at that thread when the turn ended.

That is the bug this module deletes, and it cost the operator two long messages
on 2026-08-12. The sequence, from the access log: `/compact` at 17:43:43Z, two
messages typed into the queue over the next minute, the tab moved to another
chat at 17:44:57Z, the turn finished at 17:45:54Z with nothing bound to that
thread to notice, and the next load came from a different device. The messages
had never been sent anywhere. The server had never heard of them. There was
nothing to recover, because a queue in one browser's localStorage is not
storage, it is a promise that browser makes on its own behalf.

So the queue is HERE now, beside the log it feeds:

    .runtime/users/<owner>/chat/<thread_id>/queue.json

and the browser renders it rather than owning it. A queued message survives a
reload, a closed tab, a crashed tab, a dead battery, a backend restart and a
change of device, because none of those are events the operator thinks of as
"and therefore what I sent is gone".

### Why a file and not the event log

`chat_log` is append-only and nothing in it ever moves; that is what makes a
transcript trustworthy. A queue is the opposite shape: entries enter, and they
LEAVE — drained into a turn, or taken back out. Modelling that as events would
mean the log carried "queued" entries that a reader has to know are superseded,
and every consumer of the transcript would need the same subtraction. So the
queue is a small mutable file, and the moment an entry is actually delivered it
is appended to the log as an ordinary user message carrying its ORIGINAL client
id and send time. The log still says what was said and when it was said.

### Ordering

By when the operator hit enter, which is the only moment that can be observed
later. `ts` is stamped by the client at that instant (it is the only party that
knows it) and `received_at` by the server; the queue is ordered by `ts` with
`received_at` as the tiebreak, so a message that took longer to reach the
server cannot overtake one typed after it.

### Batching

Everything waiting behind one turn goes out as ONE next turn, not N sequential
ones. Otherwise five messages typed during a long reply cost five more turns
before the last one is read. The exception is a slash command: joined to
anything else it stops being a command at all (`_is_raw_command` in the send
path only accepts a single line), so a command is always a turn by itself. This
rule used to live only in the browser (`turnBatching.ts`); it lives here now,
because the server is what forms turns.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from services import chat_log

log = logging.getLogger(__name__)

QUEUE_FILE = "queue.json"


def queue_path(thread_id: str) -> Path:
    return chat_log.thread_dir(thread_id) / QUEUE_FILE


def _empty() -> dict:
    return {"entries": [], "held": False, "error": ""}


def _read_raw(thread_id: str) -> dict:
    """The queue as stored, or an empty one.

    Never raises. A queue that cannot be parsed is a queue that would otherwise
    block every future send on this thread, and the entries are already lost at
    that point; losing them loudly in the log beats wedging the chat.
    """
    p = queue_path(thread_id)
    if not p.exists():
        return _empty()
    try:
        import json
        data = json.loads(p.read_text() or "{}")
    except Exception as e:
        log.warning("chat queue for %s is unreadable (%s); starting empty", thread_id, e)
        return _empty()
    if not isinstance(data, dict):
        return _empty()
    entries = data.get("entries")
    if not isinstance(entries, list):
        entries = []
    clean: list[dict] = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        text = e.get("text")
        cid = e.get("client_id")
        if not isinstance(text, str) or not text.strip() or not isinstance(cid, str) or not cid:
            continue
        clean.append(e)
    err = data.get("error")
    return {
        "entries": clean,
        "held": bool(data.get("held")),
        "error": err if isinstance(err, str) else "",
    }


def _write(thread_id: str, data: dict) -> None:
    """Durable whole-file write.

    `_write_json_atomic`, not the `_fast` variant: the fast one gives up fsync,
    which is the correct trade for a live-turn draft that is rewritten hundreds
    of times and can always be rebuilt. This file holds text the operator
    believes has been sent. It gets the fsync.
    """
    chat_log.thread_dir(thread_id).mkdir(parents=True, exist_ok=True)
    if not data.get("entries") and not data.get("held") and not data.get("error"):
        # Nothing left to say. Removing it keeps "does this thread have a queue"
        # answerable by a stat rather than a parse.
        p = queue_path(thread_id)
        if p.exists():
            try:
                p.unlink()
            except OSError as e:  # pragma: no cover - defensive
                log.debug("could not remove empty queue for %s: %s", thread_id, e)
        _notify(thread_id)
        return
    chat_log._write_json_atomic(queue_path(thread_id), data)
    _notify(thread_id)


def _notify(thread_id: str) -> None:
    """A queued message appearing, draining or being cancelled is a change every
    open client has to see. See services/chat_events."""
    try:
        from services import chat_events
        chat_events.bump(thread_id)
    except Exception:  # pragma: no cover - defensive
        pass


def _order_key(entry: dict) -> tuple[str, str]:
    return (str(entry.get("ts") or ""), str(entry.get("received_at") or ""))


def read(thread_id: str) -> list[dict]:
    """The queue, in the order the operator hit enter."""
    return sorted(_read_raw(thread_id)["entries"], key=_order_key)


def state(thread_id: str) -> dict:
    """`{queued, queue_held, queue_error}` for the wire. One read, every fact.

    `queue_error` is why the queue is not moving, when it is not moving for a
    reason the operator has to see. A queue that stopped and does not say so is
    the same lie as a message that looks sent and is not.
    """
    raw = _read_raw(thread_id)
    return {
        "queued": sorted(raw["entries"], key=_order_key),
        "queue_held": bool(raw["held"]),
        "queue_error": raw["error"] or "",
    }


def is_command(text: str) -> bool:
    """A single-line slash command, which is a turn by itself.

    Same rule as `_is_raw_command` in the send path, and deliberately narrow:
    "/compact" is a command, "/compact the logs please" is a message about
    compacting, and a command joined to anything else is neither.
    """
    s = (text or "").strip()
    return s.startswith("/") and "\n" not in s


def is_auto(entry: dict) -> bool:
    """A system continuation seed, which is always a turn by itself (see enqueue)."""
    return bool(isinstance(entry, dict) and entry.get("auto"))


def _alone(entry: dict) -> bool:
    """An entry that must be the whole of its turn: a slash command or an auto
    seed. Batching either with operator messages would change what it is."""
    return is_auto(entry) or is_command(entry.get("text", ""))


def has_auto(thread_id: str) -> bool:
    """Whether this thread has a continuation seed waiting. Read by the backend
    continuation loop, which only needs to touch threads that have one."""
    return any(is_auto(e) for e in _read_raw(thread_id)["entries"])


def enqueue(thread_id: str, *, text: str, client_id: str, ts: str | None = None,
            attachments: list[dict] | None = None,
            model: str | None = None, effort: str | None = None,
            permission_mode: str | None = None,
            recorded: bool = False, auto: bool = False) -> dict:
    """Take responsibility for a message that cannot run yet.

    IDEMPOTENT ON `client_id`. A retry after a timeout, a second tab replaying
    the same send, and the browser's own at-least-once outbox all arrive here
    with the id the message was first sent under, and none of them may turn one
    message into two. The stored entry wins; a re-enqueue only refreshes the
    fields a retry could legitimately carry.

    `auto` marks a SYSTEM continuation seed, not an operator message: a finished
    background run enqueues one so the thread picks its result up on its own (see
    services/chat_drain.request_continuation). It is not shown as an operator
    bubble and is never recorded as a user turn; the drain starts a turn from it
    with `already_recorded=True` so nothing is attributed to the operator. It
    still rides the queue for one reason: the queue is the only store that
    survives a backend restart between the run finishing and the turn starting,
    and its idle/lock guards are exactly the ones a continuation needs.

    Returns the queue state, so the caller can answer the request with the
    truth rather than with what it assumes it just did.
    """
    raw = _read_raw(thread_id)
    entries = raw["entries"]
    entry = {
        "client_id": client_id,
        "text": text,
        "ts": ts or chat_log.now_iso(),
        "received_at": chat_log.now_iso(),
    }
    if auto:
        entry["auto"] = True
    if recorded:
        # The message is ALREADY in the log (a delivery was attempted against a
        # run that could not read it), so finding it there does not mean it was
        # answered. The drain's already-delivered check reads this flag; without
        # it the entry would be dropped as done and never answered.
        entry["recorded"] = True
    if attachments:
        entry["attachments"] = attachments
    for k, v in (("model", model), ("effort", effort), ("permission_mode", permission_mode)):
        if v:
            entry[k] = v
    # The operator sending again is them taking the wheel back: it releases a
    # hold left by Stop and clears a failed drain's diagnosis, so this message
    # and everything waiting with it go out together rather than sitting behind
    # a stop the operator has visibly moved past.
    raw["held"] = False
    raw["error"] = ""
    existing = next((i for i, e in enumerate(entries) if e.get("client_id") == client_id), None)
    if existing is None:
        entries.append(entry)
    else:
        # Keep its place and its original send time; a retry is the same message
        # arriving again, not a new one made now.
        kept = entries[existing]
        entry["ts"] = kept.get("ts") or entry["ts"]
        entry["received_at"] = kept.get("received_at") or entry["received_at"]
        entries[existing] = entry
    raw["entries"] = entries
    _write(thread_id, raw)
    return state(thread_id)


def drop(thread_id: str, client_ids: list[str] | set[str]) -> dict:
    """Take entries out. Used when a batch has been committed to a turn, and
    when the operator cancels a message they queued."""
    ids = set(client_ids)
    if not ids:
        return state(thread_id)
    raw = _read_raw(thread_id)
    raw["entries"] = [e for e in raw["entries"] if e.get("client_id") not in ids]
    _write(thread_id, raw)
    return state(thread_id)


def clear(thread_id: str) -> dict:
    raw = _read_raw(thread_id)
    raw["entries"] = []
    _write(thread_id, raw)
    return state(thread_id)


def set_held(thread_id: str, held: bool, *, error: str | None = None) -> dict:
    """Stop the queue draining itself, optionally saying why.

    `error` set means the drain FAILED and the queue is parked on a diagnosis.
    Both are cleared by the operator's next send (see `enqueue`) or by an
    explicit release, because both are answers to "should this go out now" and
    only the operator gets to change that answer.
    """
    raw = _read_raw(thread_id)
    if bool(raw["held"]) == bool(held) and (error is None or raw["error"] == error):
        return state(thread_id)
    raw["held"] = bool(held)
    if error is not None:
        raw["error"] = error
    if not held:
        raw["error"] = ""
    _write(thread_id, raw)
    return state(thread_id)


def next_batch(thread_id: str) -> list[dict]:
    """The entries that make up the NEXT turn, or [] if the queue is empty,
    holding, or parked on a failed drain.

    Everything consecutive goes together; a command is alone. This does not
    remove anything: the entries leave the queue when the turn that carries
    them has actually been recorded, so a crash between here and there leaves
    them queued rather than gone. See `chat_drain`.
    """
    raw = _read_raw(thread_id)
    if raw["held"] or raw["error"]:
        return []
    entries = sorted(raw["entries"], key=_order_key)
    if not entries:
        return []
    if _alone(entries[0]):
        return [entries[0]]
    out: list[dict] = []
    for e in entries:
        if _alone(e):
            break
        out.append(e)
    return out


def forget(thread_id: str) -> None:
    """Everything this thread's queue holds, for a thread being deleted."""
    p = queue_path(thread_id)
    if p.exists():
        try:
            p.unlink()
        except OSError as e:  # pragma: no cover - defensive
            log.debug("could not remove queue for %s: %s", thread_id, e)


def threads_with_queues() -> list[str]:
    """Every thread holding queued messages. Read at startup, so a backend that
    restarted while a turn was running still delivers what was waiting behind
    it instead of leaving it for whichever browser next opens that chat."""
    out: list[str] = []
    for tid in chat_log.list_thread_ids():
        try:
            if queue_path(tid).exists():
                out.append(tid)
        except Exception:  # pragma: no cover - defensive
            continue
    return out


def joined_text(entries: list[dict]) -> str:
    """What the model reads for a drained batch.

    Blank-line joined, matching what the browser used to send, so a batch reads
    as several messages rather than one run-on. Each entry is ALSO recorded
    separately in the log with its own send time (see `_record_user_turn`); this
    is only the prompt.
    """
    return "\n\n".join((e.get("text") or "").strip() for e in entries if (e.get("text") or "").strip())


def attachments_of(entries: list[dict]) -> list[dict]:
    """Every file the batch carries, in the order they were attached."""
    out: list[dict] = []
    for e in entries:
        for a in e.get("attachments") or []:
            if isinstance(a, dict):
                out.append(a)
    return out


def setting_of(entries: list[dict], key: str) -> Any:
    """The last explicit per-message setting in a batch (model, effort, mode).

    Last rather than first: if the operator changed model while messages were
    waiting, the one they chose most recently is the one they meant for the
    turn those messages are about to become.
    """
    val = None
    for e in entries:
        if e.get(key):
            val = e[key]
    return val
