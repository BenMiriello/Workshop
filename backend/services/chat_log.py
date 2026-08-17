"""A chat thread is an append-only, sequence-numbered event log.

Everything else about a thread is a VIEW of that log: the transcript on screen,
the context handed to a model, the summary line in the chat list. This module is
the only thing that writes one.

Layout, per thread, under the owner's runtime dir:

    .runtime/users/<owner_id>/chat/<thread_id>/
        log.jsonl   append-only, one event per line, never rewritten, never spliced
        log.1.jsonl the cold half of the same log: events rotated out of the head
        meta.json   the small mutable record, rewritten whole by one writer

Why this shape. The store it replaces kept a whole thread in one JSON document
and every mutation was a read-modify-write of that document. That is safe only
while no `await` sits between the read and the write, and three separate paths
broke the rule; the one that mattered read a thread, spent ~30s summarizing, and
wrote the snapshot back, deleting whatever the operator typed during the fold.
An append cannot do that. A caller holding a stale snapshot can no longer erase
anything, because there is no operation that writes the transcript as a whole.

### Events

    {"seq": 41, "id": "01J...", "ts": "2026-08-09T19:52:41.149Z",
     "kind": "message", "role": "user", "content": "...", "meta": {}}

`seq` is 1-based, assigned by the writer, monotonic per thread, never reused.
`id` is a ULID (lexicographically sortable, monotonic within a millisecond).

    kind        payload                                  meaning
    message     role, content, client_id?, meta          a user or assistant message
    run         run_id, state (started/finished/lost)    a turn's lifecycle marker
    boundary    mode ("clear"), at_seq                   context cut at this point
    title       title, source (auto/manual)              the thread was named
    scope       from, to                                 the chat was re-bound to
                                                         another scope

`scope` is in the log for the same reason the rest of it is: it is a thing that
happened to this conversation, at a point in its order, and the transcript that
renders it must be readable the same way from any device and after any reload.
It was a React-state annotation once, visible only in the browser that flipped
the picker and gone the moment that browser reloaded.

There is no revision, edit or delete event, deliberately. A `message` event says
what was said and where in the order it was said; nothing later moves it. Pass A
carried a `revises: <seq>` mechanism so a streaming reply could be finalized into
a slot claimed at turn START — which is precisely the arrangement that filed the
operator's mid-turn messages BELOW a reply that had not answered them. Pass B
deleted the claimed slot, and `revises` went with it: a reply is appended when it
is finished, so it lands after everything it saw and before nothing.

### Rotation

Nothing is ever deleted, but `log.jsonl` does not grow forever either. Once it
passes `ROTATE_BYTES` the head of the file is appended to `log.1.jsonl` and the
live file is replaced by its tail. Seqs are NOT renumbered and no event is
dropped: `read_events` reads the archive and the live file as one log, so a
cursor issued before a rotation returns exactly the events it would have
returned before. The archive is the cold half — nothing on the hot path (an
append, `_tail_seq`, a poll with a current cursor, `read_thread(tail=...)`)
touches it, and it is a plain jsonl file an operator can compress or move.

### The single writer

One `asyncio.Lock` per thread id, held across the whole read-modify-write. The
mutating API is `async` for that reason. Its sync twins (`*_sync`) exist for
callers that are already ON the loop but are not coroutines — the compatibility
facade in `chat_store` is all of them. A sync critical section holds no `await`,
so the loop cannot interleave anything with it and it is atomic with respect to
a lock-holding coroutine whose own critical section is likewise await-free. What
the lock buys is pass B: a coroutine that must check-and-act across an await
(start a run only if none is active) can hold it.

Every writer asserts it is on the loop's thread. A write from a worker thread
would race the loop with no lock between them, so it raises instead of
corrupting; such a caller must use `asyncio.run_coroutine_threadsafe`.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import shutil
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from . import ownership

log = logging.getLogger("workshop.chat_log")

LOG_NAME = "log.jsonl"
ARCHIVE_NAME = "log.1.jsonl"
META_NAME = "meta.json"
# The turn in flight's draft. A SEPARATE FILE from meta.json on purpose; see
# write_live_sync for why that separation is the whole point.
LIVE_NAME = "live.json"

# Files that live alongside thread directories and are not threads.
# `queue.json` lives INSIDE a thread dir, so it is not listed here; these are
# the files that sit beside thread directories and would otherwise be read as
# thread ids.
NON_THREAD_FILES = ("drafts.json", "prefs.json")

KINDS = ("message", "run", "boundary", "title", "scope")

# ---- rotation thresholds ---------------------------------------------------
#
# Sized off the real corpus (2026-08-10: 59 threads, 5.1MB of log, the largest
# one 1.26MB in 113 events; median line 293 bytes, p90 15KB, one 284KB reply).
# A thread only rotates once it is bigger than every thread but the top few, and
# what stays live is far more than any view renders at once.
ROTATE_BYTES = 512 * 1024      # rotate a live log larger than this
KEEP_BYTES = 128 * 1024        # ...down to roughly this much tail
KEEP_MIN_EVENTS = 20           # ...but never fewer than this many events
# A thread whose protected floor (see `maybe_rotate_sync`) leaves nothing to
# move must not re-read the whole file on every append. Wait this many new
# events before looking again.
ROTATE_RETRY_EVENTS = 25


# ---- ids -------------------------------------------------------------------

_B32 = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # Crockford, per the ULID spec
_ulid_ms = 0
_ulid_rand = 0


def new_ulid() -> str:
    """A ULID: 48-bit millisecond timestamp + 80 bits of randomness, base32.

    Strictly ascending even inside one millisecond, and across a clock that
    steps backwards: the random field increments instead of being redrawn. Ids
    therefore sort in creation order, which is what makes them usable as a
    cursor if a caller ever wants one that is not the seq.
    """
    global _ulid_ms, _ulid_rand
    ms = int(time.time() * 1000)
    if ms > _ulid_ms:
        _ulid_ms, _ulid_rand = ms, secrets.randbits(80)
    else:
        _ulid_rand += 1
        if _ulid_rand >> 80:
            _ulid_ms += 1
            _ulid_rand = secrets.randbits(80)
    n = (_ulid_ms << 80) | _ulid_rand
    return "".join(_B32[(n >> (5 * (25 - i))) & 31] for i in range(26))


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---- paths -----------------------------------------------------------------


def chat_dir() -> Path:
    """`.runtime/users/<owner_id>/chat/` for the owner pinned on this call.

    Resolved through `ownership`, so it follows the ContextVar exactly as the
    document store did; there is no shared path across owners.
    """
    return ownership.user_runtime_dir() / "chat"


def _check_id(thread_id: str) -> str:
    # Thread ids are uuid4 hex. Reject anything else so a thread id can never
    # address a path outside the owner's chat dir.
    if not thread_id or not all(c in "0123456789abcdef-" for c in thread_id):
        raise ValueError(f"bad thread id: {thread_id!r}")
    return thread_id


def thread_dir(thread_id: str) -> Path:
    return chat_dir() / _check_id(thread_id)


def log_path(thread_id: str) -> Path:
    return thread_dir(thread_id) / LOG_NAME


def archive_path(thread_id: str) -> Path:
    """The cold half of the log. May not exist; a thread starts without one."""
    return thread_dir(thread_id) / ARCHIVE_NAME


def meta_path(thread_id: str) -> Path:
    return thread_dir(thread_id) / META_NAME


def live_path(thread_id: str) -> Path:
    """The running turn's draft. Absent whenever no turn is in flight."""
    return thread_dir(thread_id) / LIVE_NAME


def legacy_path(thread_id: str) -> Path:
    return chat_dir() / f"{_check_id(thread_id)}.json"


def exists(thread_id: str) -> bool:
    try:
        return meta_path(thread_id).exists()
    except ValueError:
        return False


def list_thread_ids() -> list[str]:
    d = chat_dir()
    if not d.exists():
        return []
    out = []
    for p in d.iterdir():
        if p.is_dir() and (p / META_NAME).exists():
            out.append(p.name)
    return out


# ---- the loop-thread guard -------------------------------------------------

_loop_thread: threading.Thread | None = None


class OffLoopWrite(RuntimeError):
    """A thread write was attempted from a worker thread.

    The store's atomicity comes from every writer running on the one event loop
    thread. A worker-thread write races that with nothing between them, so it is
    refused. Hand the work to the loop (`asyncio.run_coroutine_threadsafe`) and
    keep only the slow part — a model call, an HTTP request — in the thread.
    """


def _assert_on_loop() -> None:
    """Refuse a write from a worker thread while the loop is running elsewhere.

    Which thread the loop lives on is learned, not assumed: uvicorn runs it on
    the main thread, Starlette's TestClient runs it on a portal thread, and a
    plain script has no loop at all. So: a write from inside a running loop is
    always fine and teaches us where the loop is; a write from the main thread is
    always fine (that is a script, a test, or startup); anything else is a worker
    thread, and it is refused as long as a live loop thread exists to race.
    """
    global _loop_thread
    cur = threading.current_thread()
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        _loop_thread = cur
        return
    if cur is threading.main_thread():
        return
    if _loop_thread is not None and _loop_thread is not cur and _loop_thread.is_alive():
        raise OffLoopWrite(
            f"chat_log write from worker thread {cur.name!r} while the event loop "
            f"runs on {_loop_thread.name!r}: run the write on the loop "
            f"(asyncio.run_coroutine_threadsafe) and keep only the slow call off it"
        )


_locks: dict[str, asyncio.Lock] = {}


def lock_for(thread_id: str) -> asyncio.Lock:
    """The write lock for one thread. One per id, created on demand."""
    lk = _locks.get(thread_id)
    if lk is None:
        lk = _locks[thread_id] = asyncio.Lock()
    return lk


# ---- raw io ----------------------------------------------------------------


def _notify(thread_id: str) -> None:
    """Tell anything watching this thread that it changed.

    Imported lazily and swallowed whole: a change notification is a latency
    optimization over the poll that still runs behind it, and it must never be
    able to fail the write it is reporting. See services/chat_events.
    """
    try:
        from services import chat_events
        chat_events.bump(thread_id)
    except Exception:  # pragma: no cover - defensive
        pass


def _write_json_atomic(path: Path, data: dict) -> None:
    """Write whole-file JSON atomically: temp file, fsync, rename.

    The rename is what makes it atomic — a reader sees either the old file or
    the new one, never a half-written one. The fsync is what makes it survive a
    power cut rather than only a process crash.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w") as f:
        f.write(json.dumps(data, indent=2) + "\n")
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(path)


def _write_json_fast(path: Path, data: dict) -> None:
    """Whole-file JSON, atomic but NOT durable: temp file, rename, no fsync.

    The rename still means a reader sees either the old file or the new one and
    never a half-written one. What is deliberately given up is surviving a power
    cut, and see `write_live_sync` for why that is the correct trade for this
    one file and would be wrong for any other.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w") as f:
        f.write(json.dumps(data, separators=(",", ":")))
    tmp.replace(path)


def _append_line(path: Path, event: dict) -> None:
    """Append one complete JSON line, flushed and fsynced.

    One `write()` of one line, so a crash leaves either the whole event or none
    of it. `read_events` skips a line it cannot parse (and says so) rather than
    failing the whole thread, which is the other half of that guarantee.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(event, separators=(",", ":")) + "\n"
    with open(path, "a") as f:
        f.write(line)
        f.flush()
        os.fsync(f.fileno())


def _read_meta_raw(thread_id: str) -> dict | None:
    try:
        data = json.loads(meta_path(thread_id).read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _tail_seq(thread_id: str) -> int:
    """The highest seq actually on disk, read from the log's tail.

    O(1) rather than O(log length): only the last chunk is parsed. Used to
    reconcile a `last_seq` in meta.json that a crash left behind the log.
    """
    p = log_path(thread_id)
    try:
        size = p.stat().st_size
    except OSError:
        return 0
    if size == 0:
        return 0
    with open(p, "rb") as f:
        f.seek(max(0, size - 65536))
        chunk = f.read()
    for raw in reversed(chunk.split(b"\n")):
        if not raw.strip():
            continue
        try:
            ev = json.loads(raw)
        except json.JSONDecodeError:
            continue  # torn tail line; the one before it is the real end
        if isinstance(ev, dict) and isinstance(ev.get("seq"), int):
            return ev["seq"]
    return 0


# ---- reads -----------------------------------------------------------------


def read_meta(thread_id: str) -> dict | None:
    """meta.json, with `last_seq` reconciled against the log.

    A crash between the log append and the meta write leaves meta behind by one
    event. The log is the record, so the log wins.
    """
    m = _read_meta_raw(thread_id)
    if m is None:
        return None
    on_disk = _tail_seq(thread_id)
    if on_disk > int(m.get("last_seq") or 0):
        m["last_seq"] = on_disk
    return m


def _parse_event(raw: str) -> dict | None:
    """One log line as an event, or None if it is not one."""
    try:
        ev = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(ev, dict) or not isinstance(ev.get("seq"), int):
        return None
    return ev


def _read_rows(path: Path) -> tuple[list[tuple[str, dict | None]], int]:
    """Every line of a log file as `(raw, event | None)`, plus a count of the
    lines that did not parse.

    The raw text is kept because rotation moves lines between files verbatim: an
    event re-serialized on its way to the archive would not be byte-identical to
    the one a client already has.
    """
    try:
        text = path.read_text()
    except OSError:
        return [], 0
    rows: list[tuple[str, dict | None]] = []
    bad = 0
    for raw in text.splitlines():
        if not raw.strip():
            continue
        ev = _parse_event(raw)
        if ev is None:
            bad += 1
        rows.append((raw, ev))
    return rows, bad


def read_events(thread_id: str, after: int = 0, limit: int | None = None) -> list[dict]:
    """Events with `seq > after`, in seq order, at most `limit` of them.

    `after` is a cursor, not an index: it survives anything the log does later,
    which is the whole reason seqs exist. `after=0` is the entire log.

    Reads the archive and the live file as ONE log. Rotation moves the head of
    `log.jsonl` into `log.1.jsonl` without touching a seq, so a cursor handed out
    before a rotation still names the same place; it just resolves in the other
    file now. The archive is only opened when the cursor actually predates the
    live tail, which is never true of a poll.

    Two crash artifacts are absorbed here rather than repaired on disk. A crash
    between "archive is durable" and "live file is replaced" leaves the head in
    both files, so an archived event at or after the live file's first seq is
    ignored. A rotation retried after such a crash appends the same range twice,
    so an archived event that does not advance the seq is ignored. Either way the
    result is ascending, gapless and free of duplicates.
    """
    live_rows, bad = _read_rows(log_path(thread_id))
    live = [ev for _, ev in live_rows if ev is not None]
    first_live = live[0]["seq"] if live else None
    out: list[dict] = []

    arc = archive_path(thread_id)
    if (first_live is None or after < first_live - 1) and arc.exists():
        arc_rows, arc_bad = _read_rows(arc)
        bad += arc_bad
        highest = 0
        for _, ev in arc_rows:
            if ev is None:
                continue
            seq = ev["seq"]
            if seq <= highest:
                continue                      # a rotation replayed after a crash
            highest = seq
            if first_live is not None and seq >= first_live:
                continue                      # the live file still holds this one
            if seq <= after:
                continue
            out.append(ev)
            if limit is not None and len(out) >= limit:
                _warn_bad(thread_id, bad)
                return out

    for ev in live:
        if ev["seq"] <= after:
            continue
        out.append(ev)
        if limit is not None and len(out) >= limit:
            break
    _warn_bad(thread_id, bad)
    return out


def _warn_bad(thread_id: str, bad: int) -> None:
    if bad:
        log.warning("thread %s: skipped %d unreadable log line(s)", thread_id, bad)


def read_live_events(thread_id: str, before: int | None = None) -> list[dict]:
    """Every event in the LIVE (hot) file only, in seq order, optionally only
    those with `seq < before`. NEVER opens the archive.

    The cheap half of `read_events`, for a caller that wants the newest end of
    the log and can fall back to the full read on the rare thread where the live
    file alone does not hold enough. Rotation (`ROTATE_BYTES`/`KEEP_MIN_EVENTS`)
    guarantees the live file always holds at least the most recent
    `KEEP_MIN_EVENTS` events, so a `tail` request for a normal page size resolves
    here alone on almost every thread; only a heavily branched thread, where most
    of the live window is hidden, has to reach further back. See
    `routes.chat._tail_page`, the one caller.
    """
    live_rows, bad = _read_rows(log_path(thread_id))
    live = [ev for _, ev in live_rows if ev is not None]
    if before is not None:
        live = [e for e in live if e["seq"] < before]
    _warn_bad(thread_id, bad)
    return live


def read_thread(thread_id: str, tail: int | None = None) -> dict | None:
    """meta plus a bounded tail of events — what an initial render needs.

    Returns None if the thread does not exist. `tail` caps how many events come
    back; the cursor in the result is what the client polls from next.
    """
    m = read_meta(thread_id)
    if m is None:
        return None
    all_events = read_events(thread_id)
    events = all_events
    if tail is not None and tail >= 0 and len(events) > tail:
        events = events[len(events) - tail:]
    return {
        "meta": m,
        "events": events,
        "last_seq": int(m.get("last_seq") or 0),
        "active_run": active_run(thread_id, events=all_events, meta=m),
    }


def active_run(thread_id: str, events: list[dict] | None = None,
               meta: dict | None = None) -> dict | None:
    """The run in flight on this thread, or None.

    `{run_id, started_seq, live}` where `live` is the throttled snapshot of what
    the run has produced so far. The snapshot lives in meta, NOT in the log: a
    turn writes it a few hundred times and the record should carry the reply, not
    every draft of it. A client renders `live` under the last log event and needs
    nothing else to show a turn in progress."""
    m = meta if meta is not None else read_meta(thread_id)
    if m is None:
        return None
    run_id = m.get("active_run_id")
    if not run_id:
        return None
    # THE RECORDED ANSWER FIRST. `begin_run` writes the marker's seq into meta,
    # so the common case costs nothing. This used to read and parse the entire
    # log to find one event, on every 10s poll from every open tab, for the whole
    # length of a turn. `active_run_seq` is only ever meaningful alongside the
    # `active_run_id` written in the same patch, so a mismatch is impossible: the
    # two move together or neither does.
    started = m.get("active_run_seq")
    if not isinstance(started, int):
        # A thread whose run was opened by an older build. Fall back to the scan
        # rather than migrating anything; it resolves itself on the next run.
        evs = events if events is not None else read_events(thread_id)
        started = next(
            (e["seq"] for e in reversed(evs)
             if e.get("kind") == "run" and e.get("run_id") == run_id
             and e.get("state") == "started"),
            None,
        )
    # The draft: live.json when the running turn has written one, else the copy
    # in meta.json. The fallback is not dead code — it covers a turn that was
    # already in flight when this build deployed, and any thread whose run began
    # before live.json existed. Both resolve themselves when that run commits.
    #
    # meta.json still holds the run's IMMUTABLE facts (model, agent, the start
    # timestamp), written once at claim time, so the draft file carries only
    # what actually changes and the two are merged here.
    base = m.get("live_message")
    base = dict(base) if isinstance(base, dict) else None
    # THE DRAFT MUST NAME THIS RUN. A backend killed mid-turn leaves live.json
    # behind with no commit to clear it; without this check the next run to
    # claim the thread would show the dead turn's text as its own. The claim
    # clears the file too, but the guard is what makes that cleanup an
    # optimization rather than the thing correctness rests on.
    draft = read_live(thread_id)
    if draft is not None and draft.get("run_id") != run_id:
        draft = None
    live = merge_live(base, draft)
    return {
        "run_id": run_id,
        "started_seq": started,
        "live": live,
    }


def find_message_by_client_id(thread_id: str, client_id: str) -> dict | None:
    """The already-committed event for this client_id, if the browser resent it.

    Newest first, live file before archive: a resend is a retry of something sent
    seconds ago, so the answer is at the tail if it exists at all. It still spans
    the archive, because "the browser sent this twice" must not become "the
    transcript holds it twice" merely because a rotation happened in between.
    """
    if not client_id:
        return None
    live_rows, _ = _read_rows(log_path(thread_id))
    first_live = next((ev["seq"] for _, ev in live_rows if ev is not None), None)
    for _, ev in reversed(live_rows):
        if ev is not None and ev.get("kind") == "message" \
                and ev.get("client_id") == client_id:
            return ev
    arc = archive_path(thread_id)
    if not arc.exists():
        return None
    arc_rows, _ = _read_rows(arc)
    for _, ev in reversed(arc_rows):
        if ev is None:
            continue
        if first_live is not None and ev["seq"] >= first_live:
            continue                          # a duplicate of a live line
        if ev.get("kind") == "message" and ev.get("client_id") == client_id:
            return ev
    return None


# ---- thread lifecycle ------------------------------------------------------


def create_thread(thread_id: str, meta: dict) -> dict:
    """Create the thread directory with an empty log and an initial meta.json."""
    _assert_on_loop()
    _check_id(thread_id)
    d = thread_dir(thread_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / LOG_NAME).touch()
    m = dict(meta)
    m["id"] = thread_id
    m.setdefault("created_at", now_iso())
    m.setdefault("updated_at", m["created_at"])
    m["last_seq"] = 0
    m.setdefault("active_run_id", None)
    m.setdefault("boundary_seq", 0)
    _write_json_atomic(meta_path(thread_id), m)
    return m


def delete_thread(thread_id: str) -> bool:
    """Remove the thread entirely. The only destructive operation there is."""
    _assert_on_loop()
    d = thread_dir(thread_id)
    if not d.exists():
        return False
    shutil.rmtree(d)
    _locks.pop(thread_id, None)
    _rotate_waiting_at.pop(thread_id, None)
    return True


# ---- rotation --------------------------------------------------------------
#
# The live log is bounded; the record is not. Every event ever written stays
# readable, it just stops being in the file the hot path opens.

_rotate_waiting_at: dict[str, int] = {}


def _rotation_floor(meta: dict) -> int | None:
    """The lowest seq that must stay in the LIVE file, or None for no floor.

    Two things pin the tail down. `boundary_seq` is where the context was last
    cut, so everything from there on is the conversation the model is still
    being handed — that stays hot. `active_run_seq` is the marker of a turn in
    flight, and a run being reconciled mid-flight should not have to reach for
    the archive to find its own start. Both are seqs, so neither moves.
    """
    floors = []
    b = meta.get("boundary_seq")
    if isinstance(b, int) and b > 0:
        floors.append(b)
    r = meta.get("active_run_seq")
    if isinstance(r, int) and r > 0:
        floors.append(r)
    return min(floors) if floors else None


def maybe_rotate_sync(thread_id: str, meta: dict | None = None) -> int | None:
    """Move the head of `log.jsonl` into `log.1.jsonl`. Returns the seq cut at.

    Returns None when nothing moved: the file is under `ROTATE_BYTES`, or every
    event in it is protected by `_rotation_floor`.

    NOTHING IS DELETED AND NOTHING IS RENUMBERED. The lines are appended to the
    archive byte-for-byte and `read_events` reads the two files as one log, so
    the only observable difference is which file a given seq lives in. That is
    what lets a cursor a client has been holding since before the rotation keep
    working: `after=41` means "seq greater than 41" in both halves.

    Order, and why it is the only safe one:

      1. the archive is healed (a torn tail line from an interrupted append gets
         its newline) and the head is appended to it, flushed and fsynced;
      2. the tail is written to `log.jsonl.tmp`, fsynced, and renamed over the
         live file.

    A crash before (1) finishes changes nothing a reader can see: the live file
    still holds everything, and `read_events` ignores archived events that the
    live file also has. A crash between (1) and (2) is the same picture with a
    redundant copy in the archive, and the retry appends the range again — also
    ignored, because an archived seq that does not advance is skipped. There is
    no window in which the thread has no readable log, and none in which an
    event exists in neither file.

    Must be called on the loop and under the thread's write lock; `append_sync`
    is the only caller in the system, and it holds both.
    """
    _assert_on_loop()
    live_p = log_path(thread_id)
    try:
        size = live_p.stat().st_size
    except OSError:
        return None
    if size <= ROTATE_BYTES:
        _rotate_waiting_at.pop(thread_id, None)
        return None
    if meta is None:
        meta = _read_meta_raw(thread_id) or {}
    last_seq = int(meta.get("last_seq") or 0)
    waiting = _rotate_waiting_at.get(thread_id)
    if waiting is not None and last_seq < waiting + ROTATE_RETRY_EVENTS:
        # Nothing was movable last time and barely anything has happened since.
        # Re-reading a half-megabyte file on every append to learn that again is
        # exactly the cost this function exists to avoid.
        return None

    rows, _ = _read_rows(live_p)
    if not rows:
        return None

    # How much tail to keep: the newest events up to KEEP_BYTES, and never fewer
    # than KEEP_MIN_EVENTS of them. Unparseable lines carry along with whichever
    # side of the cut they sit on; they are never counted as kept events.
    cut_at = 0
    kept_events = 0
    kept_bytes = 0
    for i in range(len(rows) - 1, -1, -1):
        raw, ev = rows[i]
        if ev is not None and kept_events >= KEEP_MIN_EVENTS \
                and kept_bytes + len(raw) > KEEP_BYTES:
            cut_at = i + 1
            break
        kept_bytes += len(raw) + 1
        if ev is not None:
            kept_events += 1

    floor = _rotation_floor(meta)
    if floor is not None:
        for i, (_, ev) in enumerate(rows):
            if ev is not None and ev["seq"] >= floor:
                cut_at = min(cut_at, i)
                break

    head = rows[:cut_at]
    cut_seq = next((ev["seq"] for _, ev in reversed(head) if ev is not None), None)
    if cut_seq is None:
        # Nothing to move, or nothing but junk lines to move. Either way, leave
        # the file alone and stop looking for a while.
        _rotate_waiting_at[thread_id] = last_seq
        return None

    arc_p = archive_path(thread_id)
    if arc_p.exists() and arc_p.stat().st_size:
        with open(arc_p, "rb") as f:
            f.seek(-1, os.SEEK_END)
            torn = f.read(1) != b"\n"
        if torn:
            # An append interrupted mid-line. Close it off so the next line
            # cannot be glued onto it and lost.
            with open(arc_p, "a") as f:
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
    with open(arc_p, "a") as f:
        f.write("".join(raw + "\n" for raw, _ in head))
        f.flush()
        os.fsync(f.fileno())

    tmp = live_p.with_suffix(live_p.suffix + ".tmp")
    with open(tmp, "w") as f:
        f.write("".join(raw + "\n" for raw, _ in rows[cut_at:]))
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(live_p)

    if live_p.stat().st_size > ROTATE_BYTES:
        # The floor, or `KEEP_MIN_EVENTS` of very fat events, left the file over
        # the threshold anyway. Without this the next append would rotate again,
        # rewriting the whole file to move one event, forever.
        _rotate_waiting_at[thread_id] = last_seq
    else:
        _rotate_waiting_at.pop(thread_id, None)
    log.info("thread %s: rotated %d event(s) through seq %d into %s",
             thread_id, len(head), cut_seq, ARCHIVE_NAME)
    return cut_seq


# ---- writes ----------------------------------------------------------------


def _build_event(thread_id: str, event: dict, seq: int) -> dict:
    kind = event.get("kind")
    if kind not in KINDS:
        raise ValueError(f"unknown event kind: {kind!r} (expected one of {KINDS})")
    out = {
        "seq": seq,
        "id": new_ulid(),
        "ts": event.get("ts") or now_iso(),
        "kind": kind,
    }
    for k, v in event.items():
        if k in ("seq", "id", "kind", "ts"):
            continue
        out[k] = v
    return out


def append_sync(thread_id: str, event: dict) -> dict:
    """Append one event, assign its seq, advance `last_seq`. Returns the event.

    Sync twin of `append`; see the module docstring for why both exist. The
    caller must already be on the loop thread.
    """
    _assert_on_loop()
    meta = _read_meta_raw(thread_id)
    if meta is None:
        raise KeyError(thread_id)
    seq = max(int(meta.get("last_seq") or 0), _tail_seq(thread_id)) + 1
    ev = _build_event(thread_id, event, seq)
    _append_line(log_path(thread_id), ev)
    _notify(thread_id)
    meta["last_seq"] = seq
    meta["updated_at"] = now_iso()
    _write_json_atomic(meta_path(thread_id), meta)
    try:
        maybe_rotate_sync(thread_id, meta)
    except Exception as e:
        # The event is committed. Rotation is housekeeping on top of that, and a
        # caller told an append failed would send it again — so this is logged,
        # not raised. The file stays oversized until the next append tries again.
        log.error("thread %s: log rotation failed: %s", thread_id, e)
    _mirror(thread_id, ev, meta)
    return ev


# Mirror writes in flight, HELD so the garbage collector cannot take one
# mid-write. asyncio keeps only a weak reference to a task.
#
# This set was referenced by `_mirror` and never defined. The NameError landed in
# the bare `except Exception` below and was logged at DEBUG, so the safeguard the
# comment down there describes had never existed once: `create_task` had already
# scheduled the coroutine, so the mirror still ran and the failure was invisible,
# but every mirror write was unheld and collectable, which is precisely the
# "silently missing row" the backfill exists to repair. Found 2026-08-14 by a
# test reaching for the set by name.
_MIRROR_TASKS: set = set()

# The last scheduled mirror write per thread, so the next one can wait for it.
#
# MIRROR WRITES FOR ONE THREAD MUST BE ORDERED, and they were not. Each one
# parents its row to the branch's CURRENT head, so two running at once read the
# same head and the second one's message ends up beside its predecessor instead
# of after it. Worse at the start of a conversation: the thread row is created
# lazily by whichever write gets there first, so the first few events of a new
# chat all found no row, all tried to create one, and every loser died on
# `uq_chat_thread_external` and dropped its message from the tree entirely.
#
# Measured 2026-08-14 by driving four appends through the real path: three of
# the four mirror writes failed and the branch head was left pointing at the
# first message. It is rare at human typing speed and NOT rare at the start of a
# thread, where a message and the reply to it arrive close together.
#
# Chaining rather than locking because the order is the requirement, not
# exclusion, and a chain cannot deadlock. Failures do not break the chain: a
# write that dies still lets its successors run, because the tree missing one
# row is recoverable (the backfill) and a stalled mirror is not.
_MIRROR_CHAIN: dict[str, "asyncio.Task"] = {}


def mirror_next(thread_id: str, make_coro) -> None:
    """Queue one tree write behind this thread's previous one.

    `make_coro` is a zero-argument callable returning the coroutine to run, not
    a coroutine: nothing is created unless there is a loop to run it on, so a
    caller off the loop does not leave an un-awaited coroutine behind.

    FIRE AND FORGET, ON PURPOSE. The log line is already committed and fsynced
    by the time this is called; it is the durable write and it stays that way
    until step 4 of the migration. Awaiting a database round trip here would put
    Postgres on the latency path of every keystroke-sized append. What is lost if
    it fails is a message's PLACE in the tree, which is exactly what
    `db/chat_backfill.py` reconstructs, and it is re-runnable.

    ORDERED PER THREAD, which is the whole reason this is a chain and not a bare
    `create_task`. Every write parents its row to the branch's current head, so
    two running at once read the same head. See `_MIRROR_CHAIN`.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    try:
        prev = _MIRROR_CHAIN.get(thread_id)

        async def _write_after_the_last_one():
            if prev is not None:
                # return_exceptions: a failed predecessor must not cancel the
                # rest of the thread's writes.
                await asyncio.gather(prev, return_exceptions=True)
            await make_coro()

        # HELD: an unheld task can be garbage-collected mid-write, and a
        # silently missing mirror row is exactly the failure this mirror's
        # own backfill exists to repair. Weak-ref class, see 93768fd.
        task = loop.create_task(_write_after_the_last_one())
        _MIRROR_TASKS.add(task)
        _MIRROR_CHAIN[thread_id] = task

        def _done(t: "asyncio.Task") -> None:
            _MIRROR_TASKS.discard(t)
            # Only the TAIL clears the entry; an earlier link finishing must not
            # drop a successor that is still queued behind it.
            if _MIRROR_CHAIN.get(thread_id) is t:
                _MIRROR_CHAIN.pop(thread_id, None)

        task.add_done_callback(_done)
    except Exception:
        # Scheduling failed. The log line is already committed, so the cost is a
        # missing PLACE in the tree, which the backfill reconstructs.
        log.debug("thread %s: tree write not scheduled", thread_id,
                  exc_info=True)


def _mirror(thread_id: str, ev: dict, meta: dict) -> None:
    """Record the event in the transcript tree as well (ADR 0020).

    The tree is what makes a conversation branchable: a message has to know
    which point it was sent FROM, and a flat log cannot say. This is the one
    funnel every append goes through, so it is the one place that has to know.
    """
    from services import chat_branches
    mirror_next(thread_id, lambda: chat_branches.record_message(thread_id, ev, meta))


# Meta keys whose change is worth a Postgres round trip. The thread list
# (services.chat_list_pg) reads chat_thread's mutable columns, all traceable to
# one of these; the fields NOT in this
# set (active_run_id, live_message, turn_marks, card_states, context_tokens,
# boundary_seq/cut_through/cut_mode, claude_session, agent_sessions, ...) are
# per-turn bookkeeping that `patch_meta_sync` rewrites constantly WHILE A
# TURN RUNS and that no PG reader consults, so mirroring every one of those
# too would put a database round trip on the hottest part of a live turn for
# no reader that wants it. `chat_repo.sync_thread_fields` still writes them
# when it does run (it always writes the current meta, not a delta), so nothing
# here loses accuracy -- this only decides WHEN a patch earns the trip.
_LIST_RELEVANT_META_FIELDS = frozenset({
    "title", "_seed_title", "archived_at", "mode",
    "model", "agent_id", "context_key",
})
# NOT `scope`/`scope_locked`/`participants`. Nothing writes any of those keys
# any more: the mirror's `scope` column is DERIVED from `context_key` (which is
# in this set, so a rescope still earns its round trip), `scope_locked` went in
# T3, and `participants` went in T2 along with every reader it never had.


def _mirror_meta(thread_id: str, meta: dict) -> None:
    """Record a META-ONLY patch in the tree too, same fire-and-forget,
    per-thread-ordered mechanism as `_mirror` (see `patch_meta_sync`, the one
    funnel every meta write goes through)."""
    from services import chat_branches
    mirror_next(thread_id, lambda: chat_branches.record_meta(thread_id, meta))


async def drain_mirror_writes() -> None:
    """Wait for the outstanding mirror writes. For tests and for shutdown, not
    for the request path: putting this on a send would be the latency the
    fire-and-forget above exists to avoid."""
    while _MIRROR_TASKS:
        await asyncio.gather(*list(_MIRROR_TASKS), return_exceptions=True)


async def append(thread_id: str, event: dict) -> dict:
    """Append one event under this thread's write lock."""
    async with lock_for(thread_id):
        return append_sync(thread_id, event)


def append_message_sync(thread_id: str, role: str, content: str,
                        client_id: str | None = None, meta: dict | None = None,
                        ts: str | None = None) -> dict:
    """Append a `message` event. Idempotent on `client_id`.

    A resend of a client_id already in the log appends NOTHING and returns the
    event that is already there. That is what makes a retry after a dropped
    response safe: the browser can send the same message twice and the
    transcript still holds it once.
    """
    _assert_on_loop()
    if client_id:
        existing = find_message_by_client_id(thread_id, client_id)
        if existing is not None:
            return existing
    ev: dict = {"kind": "message", "role": role, "content": content}
    if ts:
        ev["ts"] = ts
    if client_id:
        ev["client_id"] = client_id
    if meta:
        ev["meta"] = meta
    out = append_sync(thread_id, ev)
    # The semantic index has just gone out of date (chat is in DEFAULT_KINDS).
    # Single choke point for every message write: chat_store.append_message,
    # append_message_during_turn and the bulk import at the tail of this file
    # all funnel through here, and the idempotent resend above returns before
    # reaching this line, so a retried client_id does not mark anything.
    # Same pattern as knowledge.put_note/delete_note: best-effort, never raises,
    # because a memory-index bookkeeping failure must never break a chat send.
    try:
        from services import semantic_index
        semantic_index.mark_stale(f"chat {thread_id}")
    except Exception:
        log.debug("semantic index stale-mark skipped for thread %s", thread_id,
                  exc_info=True)
    return out


async def append_message(thread_id: str, role: str, content: str,
                         client_id: str | None = None, meta: dict | None = None,
                         ts: str | None = None) -> dict:
    async with lock_for(thread_id):
        return append_message_sync(thread_id, role, content, client_id=client_id,
                                   meta=meta, ts=ts)


def patch_meta_sync(thread_id: str, **fields: object) -> dict | None:
    """Write ONLY the named meta fields, onto whatever meta.json is NOW.

    A field set to None is REMOVED, which is how a caller unsets one. There is
    deliberately no whole-document write: a caller holding a stale thread cannot
    express "and everything else goes back to what I read", so it cannot happen.
    """
    _assert_on_loop()
    meta = _read_meta_raw(thread_id)
    if meta is None:
        return None
    for k, v in fields.items():
        if v is None:
            meta.pop(k, None)
        else:
            meta[k] = v
    on_disk = _tail_seq(thread_id)
    if on_disk > int(meta.get("last_seq") or 0):
        meta["last_seq"] = on_disk
    meta["updated_at"] = now_iso()
    _write_json_atomic(meta_path(thread_id), meta)
    _notify(thread_id)
    if _LIST_RELEVANT_META_FIELDS.intersection(fields):
        _mirror_meta(thread_id, meta)
    return meta


# ---- the running turn's draft ----------------------------------------------
#
# THE DRAFT IS NOT THE RECORD, AND IT DOES NOT LIVE IN THE RECORD'S FILE.
#
# While a turn runs, the reply so far is rewritten a few hundred to a few
# thousand times: every ~0.4s and on every tool event. It used to be a field
# inside meta.json, which made every one of those rewrites a whole-document
# write of the thread's DURABLE record, with an fsync, of a document that grows
# with the reply. Measured on a 120-segment / 23 KB reply that is 3.6ms a time
# and a 55 KB meta.json, so a ten-minute turn spent about five seconds of the
# event loop rewriting and syncing the same record, and the cost rose as the
# reply got longer. That is the "everything got slow while a turn was running"
# report.
#
# So the draft has its own file, and it is written differently:
#
#   meta.json   durable, small, fsynced. Written twice per turn (claim, commit).
#   live.json   disposable, rewritten constantly, atomic but NOT fsynced.
#
# Why no fsync is correct here and nowhere else: this file is DERIVED. The
# journal at .runtime/turns/<id>/stream.jsonl is the buffer of record while a
# turn runs, and `commit_run` appends the real message to the log at the end. A
# power cut loses the process that was writing the turn, so a durably-synced
# copy of its half-finished draft buys nothing: there is nothing to resume. The
# atomic rename is still required, because a reader must never see half a file.
#
# If you are tempted to fold this back into meta.json, read the numbers above.


def write_live_sync(thread_id: str, live: dict) -> None:
    """Overwrite the running turn's draft. Cheap by construction; see above."""
    _assert_on_loop()
    _write_json_fast(live_path(thread_id), live)


def read_live(thread_id: str) -> dict | None:
    """The running turn's draft, or None when there is no live.json.

    None is normal and not an error: no turn is running, the turn predates this
    file (see `active_run`, which falls back to the copy in meta.json), or the
    draft was cleared at commit.
    """
    try:
        data = json.loads(live_path(thread_id).read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def clear_live(thread_id: str) -> None:
    """Drop the draft. Called when a run ends, however it ends."""
    try:
        live_path(thread_id).unlink()
    except OSError:
        pass


def merge_live(base: dict | None, draft: dict | None) -> dict | None:
    """The run's immutable facts, overlaid with the draft.

    `base` is what the claim wrote into meta.json once (model, agent, the start
    `ts`); `draft` is what the turn rewrites constantly (content, segments).
    The nested `meta` is merged rather than replaced, because each side owns
    different keys of it: a plain `{**base, **draft}` would drop the model and
    agent from every live view, which is exactly the byline the transcript
    renders for an agent's reply.

    Either side may be missing. No draft means the turn predates live.json (or
    has not written one yet) and `base` stands alone, which is what the previous
    arrangement always returned.
    """
    if draft is None:
        return base
    if not base:
        return dict(draft)
    out = dict(base)
    meta = dict(out.get("meta") or {})
    meta.update(draft.get("meta") or {})
    out.update(draft)
    out["meta"] = meta
    return out


# ---- migration -------------------------------------------------------------
#
# Idempotent and non-destructive, and reached ON DEMAND: chat_store.list_threads
# and chat_store.get_thread fold a legacy document in on first touch. The
# all-owners startup sweep is gone — it had nothing left to find (58 threads
# migrated, zero legacy documents on any owner, measured 2026-08-17).

MIGRATED_SUFFIX = ".migrated"

# Legacy document fields that become log events rather than meta fields.
_LEGACY_EVENT_FIELDS = ("messages", "compacted_through")


def _migrate_one(path: Path) -> str:
    """Migrate one legacy `<thread_id>.json`. Returns 'migrated' | 'skipped'.

    Order matters: the directory is fully built (log + meta) BEFORE the legacy
    file is renamed, so a crash anywhere in the middle leaves the original in
    place and the next run redoes the whole thing from scratch.
    """
    thread_id = path.stem
    try:
        _check_id(thread_id)
    except ValueError:
        return "skipped"
    try:
        doc = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        log.error("chat migration: cannot read %s: %s", path, e)
        return "skipped"
    if not isinstance(doc, dict) or "id" not in doc:
        log.error("chat migration: %s is not a thread document", path)
        return "skipped"

    d = thread_dir(thread_id)
    if (d / META_NAME).exists():
        # Already migrated by an earlier run that crashed before the rename.
        # Finish the job rather than replaying into a log that already has the
        # messages in it.
        path.rename(path.with_name(path.name + MIGRATED_SUFFIX))
        return "migrated"

    messages = doc.get("messages") or []
    if not isinstance(messages, list):
        messages = []

    meta = {k: v for k, v in doc.items() if k not in _LEGACY_EVENT_FIELDS}
    meta["id"] = thread_id
    meta.setdefault("created_at", now_iso())
    meta.setdefault("updated_at", meta["created_at"])
    meta["active_run_id"] = None

    # Build the log first, in a staging dir, so a failure never leaves a
    # half-written thread where the real one should be.
    staging = d.with_name(d.name + ".migrating")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True, exist_ok=True)
    try:
        seq_of_message: list[int] = []
        lines = []
        for i, m in enumerate(messages):
            if not isinstance(m, dict):
                continue
            seq = len(lines) + 1
            ev = {
                "seq": seq,
                "id": new_ulid(),
                "ts": m.get("ts") or meta["created_at"],
                "kind": "message",
                "role": m.get("role") or "user",
                "content": m.get("content") or "",
            }
            if isinstance(m.get("meta"), dict) and m["meta"]:
                ev["meta"] = m["meta"]
            lines.append(json.dumps(ev, separators=(",", ":")))
            seq_of_message.append(seq)
        with open(staging / LOG_NAME, "w") as f:
            f.write("".join(ln + "\n" for ln in lines))
            f.flush()
            os.fsync(f.fileno())

        # `compacted_through` was a count of folded messages — an index into an
        # array that shifts. `boundary_seq` is the seq of the last folded
        # message, which nothing can shift.
        ct = int(doc.get("compacted_through") or 0)
        ct = max(0, min(ct, len(seq_of_message)))
        meta["boundary_seq"] = seq_of_message[ct - 1] if ct > 0 else 0
        meta["last_seq"] = len(lines)
        _write_json_atomic(staging / META_NAME, meta)
    except Exception as e:
        log.error("chat migration FAILED for %s, original left untouched: %s", path, e)
        shutil.rmtree(staging, ignore_errors=True)
        return "skipped"

    staging.rename(d)
    path.rename(path.with_name(path.name + MIGRATED_SUFFIX))
    return "migrated"


def migrate_owner() -> dict:
    """Migrate every legacy thread document for the currently pinned owner."""
    d = chat_dir()
    if not d.exists():
        return {"migrated": 0, "skipped": 0}
    migrated = skipped = 0
    for p in sorted(d.glob("*.json")):
        if p.name in NON_THREAD_FILES:
            continue
        if _migrate_one(p) == "migrated":
            migrated += 1
        else:
            skipped += 1
    if migrated:
        log.info("chat migration: %d thread(s) moved to the event log", migrated)
    return {"migrated": migrated, "skipped": skipped}
