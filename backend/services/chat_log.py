"""A chat thread is an append-only, sequence-numbered event log.

Everything else about a thread is a VIEW of that log: the transcript on screen,
the context handed to a model, the summary line in the chat list. This module is
the only thing that writes one.

Layout, per thread, under the owner's runtime dir:

    .runtime/users/<owner_id>/chat/<thread_id>/
        log.jsonl   append-only, one event per line, never rewritten, never spliced
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
    boundary    mode (compact/clear), summary?           context cut at this point
    title       title, source (auto/manual)              the thread was named

There is no revision, edit or delete event, deliberately. A `message` event says
what was said and where in the order it was said; nothing later moves it. Pass A
carried a `revises: <seq>` mechanism so a streaming reply could be finalized into
a slot claimed at turn START — which is precisely the arrangement that filed the
operator's mid-turn messages BELOW a reply that had not answered them. Pass B
deleted the claimed slot, and `revises` went with it: a reply is appended when it
is finished, so it lands after everything it saw and before nothing.

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
META_NAME = "meta.json"

# Files that live alongside thread directories and are not threads.
NON_THREAD_FILES = ("drafts.json", "prefs.json")

KINDS = ("message", "run", "boundary", "title")


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


def meta_path(thread_id: str) -> Path:
    return thread_dir(thread_id) / META_NAME


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


def read_events(thread_id: str, after: int = 0, limit: int | None = None) -> list[dict]:
    """Events with `seq > after`, in seq order, at most `limit` of them.

    `after` is a cursor, not an index: it survives anything the log does later,
    which is the whole reason seqs exist. `after=0` is the entire log.
    """
    p = log_path(thread_id)
    if not p.exists():
        return []
    out: list[dict] = []
    bad = 0
    try:
        text = p.read_text()
    except OSError:
        return []
    for raw in text.splitlines():
        if not raw.strip():
            continue
        try:
            ev = json.loads(raw)
        except json.JSONDecodeError:
            bad += 1
            continue
        if not isinstance(ev, dict) or not isinstance(ev.get("seq"), int):
            bad += 1
            continue
        if ev["seq"] <= after:
            continue
        out.append(ev)
        if limit is not None and len(out) >= limit:
            break
    if bad:
        log.warning("thread %s: skipped %d unreadable log line(s)", thread_id, bad)
    return out


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
    live = m.get("live_message")
    return {
        "run_id": run_id,
        "started_seq": started,
        "live": dict(live) if isinstance(live, dict) else None,
    }


def find_message_by_client_id(thread_id: str, client_id: str) -> dict | None:
    """The already-committed event for this client_id, if the browser resent it."""
    if not client_id:
        return None
    for ev in read_events(thread_id):
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
    return True


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
    meta["last_seq"] = seq
    meta["updated_at"] = now_iso()
    _write_json_atomic(meta_path(thread_id), meta)
    return ev


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
    return append_sync(thread_id, ev)


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
    return meta


async def patch_meta(thread_id: str, **fields: object) -> dict | None:
    async with lock_for(thread_id):
        return patch_meta_sync(thread_id, **fields)


# ---- migration -------------------------------------------------------------
#
# One shot, idempotent, non-destructive. Runs at startup and by hand
# (`python -m services.chat_log migrate`).

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


def migrate_all_owners() -> dict:
    """Migrate every owner's threads. Safe to run repeatedly; a thread that has
    already been migrated has no legacy file left to find."""
    root = ownership._RUNTIME / "users"
    totals = {"migrated": 0, "skipped": 0}
    if not root.exists():
        return totals
    for user_dir in sorted(root.iterdir()):
        if not user_dir.is_dir() or not user_dir.name.isdigit():
            continue
        prior = ownership.owner_or_none()
        ownership.set_current_owner(int(user_dir.name))
        try:
            res = migrate_owner()
        except Exception as e:
            log.error("chat migration failed for owner %s: %s", user_dir.name, e)
            res = {"migrated": 0, "skipped": 0}
        finally:
            if prior is None:
                ownership.clear_current_owner()
            else:
                ownership.set_current_owner(prior)
        totals["migrated"] += res["migrated"]
        totals["skipped"] += res["skipped"]
    return totals


if __name__ == "__main__":  # pragma: no cover — manual entry point
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "migrate":
        logging.basicConfig(level=logging.INFO)
        print(json.dumps(migrate_all_owners(), indent=2))
    else:
        print("usage: python -m services.chat_log migrate")
