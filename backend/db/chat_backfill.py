"""Move the chat transcripts out of the per-thread JSONL logs (ADR 0020).

Separate from `db/backfill.py` on purpose: that one carries the work model, and
folding a second domain into it would make both unreviewable. Same properties
though: idempotent, re-runnable, and verified by counting rows rather than by
reporting success.

THE INTERESTING PART IS THE SPLIT. In the file log an interrupted reply is ONE
event whose `meta.segments` carry `cut_after` tokens saying where each
interrupting message landed, and three separate readers re-derived the true
order from those tokens on every render. Here the split happens once, and comes
out as rows: a reply interrupted twice becomes three assistant messages with the
two user messages threaded between them. After this runs, `cut_id`, `cut_after`,
`cut_head`, `at_unit`, `at_segment`, `during_run` and `injected` describe nothing
that is still stored.

The fidelity guard from `chat_context._split_reply` is kept exactly: rebuilding
the stretches must reproduce the reply's `content`, and a reply that fails that
check is written whole with its interrupts before it, which is the flat order
the log already had. Guessing is worse than staying flat, because flat is at
least honest about being flat.

NOTHING IS DELETED. The `.runtime/users/<id>/chat/` files are left exactly as
they are; this reads them.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import text

ROOT = Path(os.environ.get("WORKSHOP_ROOT", Path(__file__).resolve().parents[2]))
RUNTIME = Path(os.environ.get("WORKSHOP_RUNTIME", ROOT / ".runtime"))

# The names chat_log uses. Duplicated rather than imported so this script does
# not drag the whole services package (and its FastAPI imports) in.
LOG_NAME = "log.jsonl"
ARCHIVE_NAME = "log.1.jsonl"
META_NAME = "meta.json"


# --- reading the files -------------------------------------------------------

def _iter_owner_dirs() -> list[tuple[int, Path]]:
    base = RUNTIME / "users"
    out = []
    if not base.exists():
        return out
    for d in sorted(base.iterdir()):
        chat = d / "chat"
        if chat.is_dir() and d.name.isdigit():
            out.append((int(d.name), chat))
    return out


def _thread_dirs(chat_dir: Path) -> list[Path]:
    return sorted(p for p in chat_dir.iterdir()
                  if p.is_dir() and (p / META_NAME).exists())


def _read_json(p: Path) -> dict:
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def _read_events(thread_dir: Path) -> list[dict]:
    """Archive then live, exactly as chat_log.read_events stitches them.

    Unparseable lines are skipped and counted by the caller; a truncated tail is
    a real crash artefact in this data, not a hypothetical.
    """
    events: list[dict] = []
    for name in (ARCHIVE_NAME, LOG_NAME):
        p = thread_dir / name
        if not p.exists():
            continue
        for line in p.read_text(errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except Exception:
                continue
            if isinstance(ev, dict) and isinstance(ev.get("seq"), int):
                events.append(ev)
    # Rotation can leave a duplicated head or a replayed range; seq is stable
    # across it, so dedupe on seq and sort.
    by_seq: dict[int, dict] = {}
    for ev in events:
        by_seq.setdefault(ev["seq"], ev)
    return [by_seq[k] for k in sorted(by_seq)]


# --- the split ---------------------------------------------------------------

def split_reply(reply: dict, interrupts: list[dict]) -> list[tuple] | None:
    """One reply, cut open at the messages that landed inside it.

    Returns `[("text", content, segments) | ("msg", event, None), ...]` in true
    order, or None when it cannot be split faithfully.

    This is the storage-shaped twin of `chat_context._split_reply`, which
    returns prompt-shaped pieces. The duplication is deliberate and
    self-limiting: the reconstruction exists to be run once, here, and both
    copies go away when the read path stops reading the log.

    One difference from the prompt-side version, and it is a fix rather than a
    divergence: a stretch holding only TOOL segments and no prose is kept. The
    prompt does not care about tool calls, but the transcript renders them, and
    dropping such a stretch would silently swallow work the reader saw happen.
    """
    meta = reply.get("meta") or {}
    segments = [s for s in (meta.get("segments") or []) if isinstance(s, dict)]

    by_token: dict[str, dict] = {}
    for m in interrupts:
        token = (m.get("meta") or {}).get("cut_id")
        if token:
            by_token[token] = m
    if not by_token:
        return None
    head = [t for t in (meta.get("cut_head") or []) if t in by_token]
    if not head and not any(s.get("cut_after") for s in segments):
        return None

    items: list[tuple] = []
    buf: list[dict] = []
    seen: set[str] = set()

    def flush() -> None:
        content = "".join(s.get("text") or "" for s in buf if s.get("t") == "text")
        items.append(("text", content, list(buf)))
        buf.clear()

    def take(refs) -> None:
        for r in refs:
            m = by_token.get(r)
            if m is not None and r not in seen:
                seen.add(r)
                items.append(("msg", m, None))

    if head:
        # An empty leading stretch: the message beat the first word of the reply.
        flush()
        take(head)
    for s in segments:
        buf.append(s)
        refs = [r for r in (s.get("cut_after") or ())
                if r in by_token and r not in seen]
        if refs:
            flush()
            take(refs)
    flush()
    for m in interrupts:
        token = (m.get("meta") or {}).get("cut_id")
        if not token or token not in seen:
            items.append(("msg", m, None))

    # THE FIDELITY GUARD. `content` is the join of the reply's top-level text
    # segments, so reassembling the stretches must give it back exactly.
    rebuilt = "".join(v for kind, v, _ in items if kind == "text")
    if rebuilt.strip() != (reply.get("content") or "").strip():
        return None

    out: list[tuple] = []
    for kind, value, segs in items:
        if kind == "msg":
            out.append((kind, value, None))
        elif str(value).strip() or (segs and any(s.get("t") != "text" for s in segs)):
            out.append((kind, value, segs))
    return out


# --- the legacy coordinate ---------------------------------------------------
# The cut TOKENS only started being written on 2026-08-12. Measured across this
# installation's 90 mid-run messages: 11 carry a token, 31 carry `at_unit`, 48
# carry only `at_segment`. So 79 of 90 need the index path, and skipping it
# would silently flatten 33 replies that the operator's transcript currently
# renders split.
#
# This is a port of frontend/src/components/chat/transcriptUnits.ts, which is
# the canonical implementation and carries the full reasoning. Ported rather
# than approximated because "renders exactly where it always did" is the whole
# requirement. Both copies die together when these transcripts age out.

def _is_agent(s: dict) -> bool:
    return s.get("t") == "agent"


def unit_count(segments: list[dict]) -> int:
    """A UNIT is one rendered thing: a top-level segment, or one step nested in
    a subagent. Counting top-level segments alone was the original bug: a whole
    delegation advanced the coordinate by one, so two messages minutes apart got
    the same number."""
    n = 0
    for s in segments:
        n += 1 + len(s.get("steps") or ()) if _is_agent(s) else 1
    return n


def _unit_of_segment_index(segments: list[dict], seg_index: int) -> int:
    n = 0
    for i, s in enumerate(segments):
        if i >= seg_index:
            break
        n += 1 + len(s.get("steps") or ()) if _is_agent(s) else 1
    return n


def _snap_cut(segments: list[dict], cut: int) -> int:
    """A delegation is one thing and is never divided: a cut landing strictly
    inside a subagent chip moves to the chip's END, so the whole delegation
    stays where it was launched and the message renders below it."""
    u = 0
    for seg in segments:
        head = u
        u += 1
        if not _is_agent(seg):
            continue
        u += len(seg.get("steps") or ())
        if head < cut < u:
            return u
    return cut


def _slice_units(segments: list[dict], lo: int, hi: int) -> list[dict]:
    out = []
    u = 0
    for seg in segments:
        head = u
        u += 1 + len(seg.get("steps") or ()) if _is_agent(seg) else 1
        if lo <= head < hi:
            out.append(seg)
    return out


def _split_at_units(segments: list[dict], cuts: list[int]) -> list[list[dict]]:
    """Cuts are clamped into range and forced non-decreasing, so a stale or
    out-of-order coordinate can only make a slice empty, never reorder one."""
    total = unit_count(segments)
    bounds = [0]
    prev = 0
    for raw in cuts:
        clamped = max(prev, min(raw if isinstance(raw, int) else total, total))
        at = max(prev, min(_snap_cut(segments, clamped), total))
        bounds.append(at)
        prev = at
    bounds.append(total)
    return [_slice_units(segments, bounds[i], bounds[i + 1])
            for i in range(len(bounds) - 1)]


def _cut_point_of(meta: dict, segments: list[dict]) -> int:
    """`at_unit` when the server recorded one, else the legacy `at_segment`
    converted, else the end (the honest answer when nothing says where)."""
    if isinstance(meta.get("at_unit"), int):
        return meta["at_unit"]
    if isinstance(meta.get("at_segment"), int):
        return _unit_of_segment_index(segments, meta["at_segment"])
    return unit_count(segments)


def split_reply_legacy(reply: dict, interrupts: list[dict]) -> list[tuple] | None:
    """The index path, for replies recorded before positions were written down.

    No fidelity guard is needed and none is possible: the slices PARTITION the
    same segment list, so the text reassembles by construction. That is also
    why this cannot be used when tokens exist and disagree with the index.
    """
    meta = reply.get("meta") or {}
    segments = [s for s in (meta.get("segments") or []) if isinstance(s, dict)]
    if not interrupts:
        return None
    # NO SEGMENTS MEANS NOTHING TO SLICE, and slicing anyway silently deletes
    # the reply. Some replies carry `content` and no segment list at all (an
    # older record shape, and synthetic messages); measured on thread
    # 2ff72ec0, a 557-character reply became an empty one because every slice
    # of an empty list is empty. Fall through to the flat order instead.
    if not segments:
        return None
    cuts = [_cut_point_of(m.get("meta") or {}, segments) for m in interrupts]
    parts = _split_at_units(segments, cuts)

    items: list[tuple] = []
    for i, part in enumerate(parts):
        content = "".join(s.get("text") or "" for s in part if s.get("t") == "text")
        items.append(("text", content, part))
        if i < len(interrupts):
            items.append(("msg", interrupts[i], None))
    return items


# Keys that describe WHERE a message landed inside a reply. They are what this
# migration replaces with structure, so they are not carried into `meta`.
_POSITIONAL = ("cut_id", "cut_head", "at_unit", "at_segment", "during_run",
               "during_turn", "injected", "streaming", "segments")
# Keys that moved to `chat_turn`.
_TURN_LEVEL = ("turn_id", "model", "agent", "started_at", "ended_at", "error",
               "timeout", "stopped")


def _message_meta(ev: dict) -> dict | None:
    meta = {k: v for k, v in (ev.get("meta") or {}).items()
            if k not in _POSITIONAL and k not in _TURN_LEVEL}
    return meta or None


def _iso(value: Any):
    from datetime import datetime
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except Exception:
        return None


# --- the write ---------------------------------------------------------------

def _thread_fields(meta: dict) -> dict:
    scope = meta.get("scope")
    ctx = meta.get("context_key") or "general"
    if not scope:
        # chat_store._scope_from_context_key, in one line: a bare context key is
        # the workshop scope, anything prefixed carries its own.
        scope = "workshop" if ctx in ("general", "") else (
            ctx if ":" in ctx else f"app:{ctx}")
    return {
        "context_key": ctx,
        "scope": scope,
        "scope_locked": bool(meta.get("scope_locked")),
        "mode": meta.get("mode") if meta.get("mode") in ("direct", "orchestrated")
        else "orchestrated",
        "title": (meta.get("title") or None),
        "seed_title": (meta.get("_seed_title") or None),
        "model": meta.get("model") or None,
        "effort": meta.get("effort") or None,
        "permission_mode": meta.get("permission_mode") or None,
        "summary": meta.get("summary") or None,
        "context_tokens": meta.get("context_tokens"),
        "claude_session": meta.get("claude_session") or None,
        "voice_session": meta.get("voice_session") or None,
        "agent_slug": meta.get("agent_id") or None,
        "archived_at": _iso(meta.get("archived_at")),
        "created_at": _iso(meta.get("created_at")),
        "updated_at": _iso(meta.get("updated_at")),
    }


def thread_by_external(s, external: str):
    return s.scalar(text(
        "SELECT id FROM chat_thread WHERE external_id = :e "
        "AND deleted_at IS NULL"), {"e": external})


def _load_thread(s, org_id, user_id, thread_dir: Path, stats: dict) -> None:
    """One conversation, files to rows. Tops up a thread already loaded."""
    from db import chat_repo
    from db.ids import uuid7

    external = thread_dir.name
    meta = _read_json(thread_dir / META_NAME)
    events = _read_events(thread_dir)

    # RE-RUNNABLE MEANS TOPPING UP, NOT SKIPPING. An existing thread keeps its
    # rows and gains only the events the tree has not seen, which is what makes
    # this the repair for a mirror-write that failed or for the window between
    # this script running and the mirror going live. It used to return here, and
    # "idempotent" then meant "does nothing", which is not the same thing.
    fields = _thread_fields(meta)
    seen: set[str] = set()
    existing = thread_by_external(s, external)
    if existing is not None:
        tid = existing
        stats["threads_skipped"] += 1
        seen = {r[0] for r in s.execute(text(
            "SELECT external_id FROM chat_message WHERE thread_id = :t "
            "AND external_id IS NOT NULL"), {"t": tid}).all()}
        # ONLY THE EVENT KINDS THAT BECOME A ROW COUNT AS NEW. `run` and `title`
        # never carry a chat_message id, so testing against every event kind
        # made a thread with nothing new look topped up on every single run
        # (measured: 16 of 63 threads, forever). The event list itself is NOT
        # filtered, because the run markers are still needed to build the turns.
        if not any(ev.get("kind") in ("message", "boundary", "scope")
                   and ev.get("id") not in seen for ev in events):
            return
        stats["topped_up"] += 1
    else:
        made = chat_repo.create_thread(s, org_id, user_id, external_id=external,
                                       **fields)
        tid = made["id"]
        stats["threads"] += 1

    state = {k: meta[k] for k in ("turn_marks", "card_states", "ticket_drafts",
                                 "agent_sessions") if meta.get(k)}
    if state and existing is None:
        s.execute(text("UPDATE chat_thread SET state = :st WHERE id = :t"),
                  {"st": chat_repo._json(state), "t": tid})

    # --- turns ---------------------------------------------------------------
    turns: dict[str, Any] = {}
    for ev in events:
        if ev.get("kind") != "run":
            continue
        run_id = ev.get("run_id")
        if not run_id:
            continue
        row = turns.get(run_id)
        if row is None:
            row = turns[run_id] = {"id": uuid7(), "started": None, "ended": None,
                                   "status": "running"}
        if ev.get("state") == "started":
            row["started"] = _iso(ev.get("ts"))
        elif ev.get("state") == "finished":
            row["ended"] = _iso(ev.get("ts"))
            row["status"] = "finished"
        elif ev.get("state") == "lost":
            row["ended"] = _iso(ev.get("ts"))
            row["status"] = "lost"

    # Turn-level facts live on the assistant reply in the file log; lift them.
    for ev in events:
        if ev.get("kind") != "message" or ev.get("role") != "assistant":
            continue
        m = ev.get("meta") or {}
        run_id = m.get("turn_id")
        if not run_id:
            continue
        row = turns.setdefault(run_id, {"id": uuid7(), "started": None,
                                        "ended": None, "status": "finished"})
        row.setdefault("model", m.get("model"))
        row.setdefault("agent", m.get("agent"))
        row["started"] = row["started"] or _iso(m.get("started_at"))
        row["ended"] = row["ended"] or _iso(m.get("ended_at"))
        row["error"] = bool(m.get("error"))
        row["timeout"] = bool(m.get("timeout"))
        row["stopped"] = m.get("stopped")

    org = s.scalar(text("SELECT org_id FROM chat_thread WHERE id = :t"), {"t": tid})
    for run_id, row in turns.items():
        # ON CONFLICT DO NOTHING so a top-up re-reads the whole file without
        # colliding with the turns it already wrote. `run_id` is globally
        # unique, so this is an exact "have I seen this run" test.
        s.execute(text(
            "INSERT INTO chat_turn (id, org_id, thread_id, run_id, status, model,"
            " agent_slug, started_at, ended_at, error, timeout, stopped) "
            "VALUES (:id, :org, :t, :run, :status, :model, :agent, :started,"
            "        :ended, :error, :timeout, :stopped) "
            "ON CONFLICT (run_id) DO NOTHING"),
            {"id": row["id"], "org": org, "t": tid, "run": run_id,
             "status": row["status"], "model": row.get("model"),
             "agent": row.get("agent"), "started": row.get("started"),
             "ended": row.get("ended"), "error": bool(row.get("error")),
             "timeout": bool(row.get("timeout")), "stopped": row.get("stopped")})
        # The row id we minted may have lost the race with one already there;
        # the messages must point at whichever row actually exists.
        row["id"] = s.scalar(text("SELECT id FROM chat_turn WHERE run_id = :r"),
                             {"r": run_id})
    stats["turns"] += len(turns)

    # --- messages ------------------------------------------------------------
    held: dict[str, list[dict]] = {}
    title = fields["title"]
    title_source = None

    def emit_user(ev: dict) -> None:
        chat_repo.append_message(
            s, tid, role="user", content=ev.get("content") or "",
            meta=_message_meta(ev), client_id=ev.get("client_id"),
            external_id=ev.get("id"))
        stats["messages"] += 1

    def emit_assistant(ev: dict, content: str, segments, turn_row) -> None:
        chat_repo.append_message(
            s, tid, role="assistant", content=content,
            segments=segments, meta=_message_meta(ev),
            turn_id=turn_row["id"] if turn_row else None,
            external_id=ev.get("id"))
        stats["messages"] += 1

    for ev in events:
        kind = ev.get("kind")
        if kind == "title":
            title = ev.get("title") or title
            title_source = ev.get("source") or title_source
            continue
        if kind == "run":
            continue
        # Already a row. On a first load `seen` is empty and this never fires;
        # on a top-up it is what keeps the pass to the genuinely new tail.
        if ev.get("id") in seen:
            continue
        if kind == "boundary":
            chat_repo.append_message(
                s, tid, role="assistant", kind="boundary", content="",
                boundary_mode=ev.get("mode"), external_id=ev.get("id"))
            stats["boundaries"] += 1
            continue
        if kind == "scope":
            chat_repo.append_message(
                s, tid, role="assistant", kind="scope", content="",
                scope_from=ev.get("from"), scope_to=ev.get("to"),
                external_id=ev.get("id"))
            stats["scopes"] += 1
            continue
        if kind != "message":
            continue

        m = ev.get("meta") or {}
        if ev.get("role") == "user":
            run = m.get("during_run") or m.get("during_turn")
            if run:
                held.setdefault(run, []).append(ev)
            else:
                emit_user(ev)
            continue

        # assistant
        run_id = m.get("turn_id")
        turn_row = turns.get(run_id) if run_id else None
        interrupts = held.pop(run_id, []) if run_id else []
        # Tokens first, index second. The token path is exact; the index path
        # is what the transcript on disk was rendered with before tokens
        # existed, and it must keep producing the same placement.
        pieces = None
        if interrupts:
            pieces = split_reply(ev, interrupts)
            if pieces is None:
                pieces = split_reply_legacy(ev, interrupts)
                if pieces is not None:
                    stats["legacy_split_replies"] += 1
        if pieces is None:
            # Either nothing interrupted it, or it could not be split
            # faithfully. Both land as the flat order the log already had.
            for held_ev in interrupts:
                emit_user(held_ev)
                stats["unsplit_messages"] += 1
            if interrupts:
                stats["unsplit_replies"] += 1
            emit_assistant(ev, ev.get("content") or "",
                           m.get("segments"), turn_row)
        else:
            stats["split_replies"] += 1
            for pkind, value, segs in pieces:
                if pkind == "msg":
                    emit_user(value)
                else:
                    emit_assistant(ev, value, segs, turn_row)
                    stats["split_stretches"] += 1

    # A run that never committed a reply leaves its messages held. They are real
    # messages the operator sent, so they land at the end rather than nowhere.
    for run_id in sorted(held):
        for ev in held[run_id]:
            emit_user(ev)
            stats["orphan_messages"] += 1

    if title:
        s.execute(text(
            "UPDATE chat_thread SET title = :ti, title_source = :src WHERE id = :t"),
            {"ti": title[:255], "src": title_source, "t": tid})


def plan() -> dict:
    counts: dict[str, int] = {}
    for owner, chat_dir in _iter_owner_dirs():
        dirs = _thread_dirs(chat_dir)
        counts[f"owner {owner}: threads"] = len(dirs)
        counts[f"owner {owner}: events"] = sum(len(_read_events(d)) for d in dirs)
    return counts


def apply() -> dict:
    from db.engine import owner_session, tenant_session

    stats = {"threads": 0, "threads_skipped": 0, "topped_up": 0,
             "messages": 0, "turns": 0,
             "boundaries": 0, "scopes": 0, "split_replies": 0,
             "legacy_split_replies": 0,
             "split_stretches": 0, "unsplit_replies": 0, "unsplit_messages": 0,
             "orphan_messages": 0, "unmapped_owners": 0,
             "unmapped_threads": 0}
    unmapped: list[str] = []

    # The owner connection, because `organization` and `user_account` are
    # RLS-protected with a SELECT policy keyed on the current org or the current
    # user, and this script has neither yet: it is asking WHICH org to enter.
    # Exactly the lookup db/backfill.py does on the same connection.
    with owner_session() as s:
        org_id = s.scalar(text(
            "SELECT id FROM organization WHERE slug = 'workshop' "
            "AND deleted_at IS NULL"))
        if org_id is None:
            raise RuntimeError(
                "no 'workshop' organization: run db/backfill.py first, which "
                "seeds the org and the user accounts this one maps onto.")
        users = dict(s.execute(text(
            "SELECT legacy_user_id, id FROM user_account "
            "WHERE legacy_user_id IS NOT NULL AND deleted_at IS NULL")).all())

    for owner, chat_dir in _iter_owner_dirs():
        user_id = users.get(owner)
        dirs = _thread_dirs(chat_dir)
        if user_id is None:
            # No account for this legacy owner. Report it rather than invent a
            # user or drop the threads silently; the files are untouched, so
            # nothing is lost and a later run picks them up once the account
            # exists. Owner 1003 is the known Linux-uid-instead-of-app-user bug.
            stats["unmapped_owners"] += 1
            stats["unmapped_threads"] += len(dirs)
            unmapped.append(f"owner {owner} ({len(dirs)} threads)")
            continue
        for d in dirs:
            with tenant_session(org_id) as s:
                _load_thread(s, org_id, user_id, d, stats)

    if unmapped:
        stats["unmapped_detail"] = "; ".join(unmapped)  # type: ignore[assignment]
    return stats


def main() -> int:
    if "--plan" in sys.argv:
        for k, v in plan().items():
            print(f"{k:32} {v}")
        return 0
    for k, v in apply().items():
        print(f"{k:22} {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
