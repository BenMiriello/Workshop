"""The sweep's control channel: a file it reads at the top of each iteration.

Step B6 of `docs/platform-redesign/workspace-ownership-and-leases-2026-08-22.md`
(section 8, "The sweep needs a control channel, because it has no FIFO").

    .runtime/control/<run-id>.json

WHY A FILE. Every other participant in this system can be spoken to: a chat has
a queue (`services/chat_queue.py`) and a running turn has a stdin FIFO
(`build_streaming.inject`). `scripts/project_ralph.sh` has neither -- it is a
bash loop with no stdin -- so both steer and yield were undefined for the
participant that most needs them. It already loops, so section 8 gives it a
control FILE read at the top of each iteration. That is not a lesser channel:

> `steer` becomes "fold this into the next iteration's prompt", which for a
> sweep is a *better* boundary than mid-turn injection

because an iteration boundary is a point where the sweep is between tasks and
has nothing half-applied, which is exactly what mid-turn injection cannot
promise (see `build_streaming.inject`'s own docstring on measured latency).

FAIL SOFT, ALWAYS. A malformed, unreadable, truncated or half-written control
file must never stop a sweep. Every reader here returns an empty channel rather
than raising, and the CLI exits 0 with empty output on anything it cannot parse.
The alternative -- a sweep that dies because a UI wrote bad JSON -- would make
this channel more dangerous than having none.

NO PRIORITY NUMBERS (section 8). The verb is the urgency, requests are FIFO by
when they were submitted, and a queued request is cancellable until it is taken.
There is no scalar, no tier and no reordering rule anywhere in this file; if you
are about to add one, section 8 has already refused it and named the precedent
(GitHub Actions concurrency groups, not Kubernetes PriorityClasses).

THIS MODULE RUNS ON THE SYSTEM PYTHON, and that is a hard constraint rather
than a preference: `scripts/control.py` is run by bash under whatever `python3`
a sweep happens to have, which has neither sqlalchemy nor yaml.
`services/presence.py` carries the same constraint for the same reason. So the
only two modules imported here are `wsplatform.paths` and
`services/git_service.py` (for the `snapshot` verb), both of which are pure
`os`/`subprocess`/`pathlib` and neither of which imports anything heavier. The
verbs that DO need the database (preempt, the lease, the timeline) live in
`services/control.py`, which imports this one and never the other way round.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from services import git_service
from wsplatform import paths

__all__ = [
    "VERBS", "ControlError", "control_dir", "control_path",
    "read", "pending", "submit", "cancel", "take",
    "request_yield", "yield_requested", "clear_yield", "clear", "forget",
    "channels", "fold_text", "main",
]

# What may be asked of a running participant. `queue` and `steer` are section
# 8's first two verbs; `yield` is what preempt asks for FIRST, before its grace
# window lapses -- section 9's "snapshot, release, exit cleanly" through the
# normal finalize path. Preempt itself is not here: it is not something the
# holder does.
VERBS = ("queue", "steer", "yield")

# Same rule and same regex as `presence._session_path`: a run id is validated,
# never escaped. Escaping would let two different ids collide on one file, and
# every real id (a job id, an agent slug, `sweep-<job>`) already passes.
_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")


class ControlError(ValueError):
    """A caller asked for something the channel cannot mean: an unusable run id,
    an unknown verb, an empty request. Raised by WRITERS only -- readers fail
    soft, because a reader's caller is a sweep."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def control_dir() -> Path:
    """`.runtime/control/`, overridable by `WORKSHOP_CONTROL_DIR`.

    Through `wsplatform.paths` rather than a literal, so `WORKSHOP_ROOT` and
    `WORKSHOP_RUNTIME` move this channel with everything else -- a control file
    the backend writes and the sweep cannot find is worse than no channel.
    That module is pure `os`/`pathlib` (ADR 0024), so importing it does not cost
    this file its stdlib-only constraint.

    A FUNCTION AND NOT A MODULE CONSTANT, unlike `presence.PRESENCE_DIR`: the
    env is read on every call, which is what lets a test point the channel at a
    tmp_path with `monkeypatch.setenv` and get the same answer the sweep's
    separate `python3` process gets from the same variable.
    """
    return paths.env_path("WORKSHOP_CONTROL_DIR", "control")


def control_path(run_id: str) -> Path:
    if not isinstance(run_id, str) or not _ID_RE.fullmatch(run_id):
        raise ControlError(f"not a usable run id: {run_id!r}")
    return control_dir() / f"{run_id}.json"


def _empty(run_id: str) -> dict:
    return {"run_id": run_id, "requests": [], "yield": None, "updated_at": None}


def _order_key(req: dict) -> tuple[str, int]:
    """FIFO: when it was submitted, then the sequence it was written in.

    `seq` breaks a tie between two requests stamped in the same second, which is
    ordinary at a UI's click rate. It is a tiebreak and NOT a priority: it only
    ever increases, a caller cannot set it, and nothing reads it as importance.
    """
    return (str(req.get("at") or ""), int(req.get("seq") or 0))


def read(run_id: str) -> dict:
    """The channel as stored, or an empty one. NEVER RAISES.

    This is the function a sweep is standing on, so every failure mode -- no
    directory, no file, unreadable bytes, invalid JSON, a JSON array where an
    object belongs, a `requests` key holding a string -- lands on the same
    answer: nothing is being asked of you, carry on.
    """
    try:
        path = control_path(run_id)
    except ControlError:
        return _empty(str(run_id))
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    except (OSError, ValueError):
        return _empty(run_id)
    if not isinstance(data, dict):
        return _empty(run_id)
    out = _empty(run_id)
    requests = data.get("requests")
    if isinstance(requests, list):
        clean = []
        for r in requests:
            if not isinstance(r, dict):
                continue
            text = r.get("text")
            verb = r.get("verb")
            if verb not in VERBS or not isinstance(text, str) or not text.strip():
                continue
            clean.append({
                "id": str(r.get("id") or ""),
                "verb": verb,
                "text": text,
                "actor": r.get("actor") if isinstance(r.get("actor"), str) else None,
                "at": str(r.get("at") or ""),
                "seq": int(r["seq"]) if isinstance(r.get("seq"), int) else 0,
            })
        out["requests"] = sorted([r for r in clean if r["id"]], key=_order_key)
    y = data.get("yield")
    if isinstance(y, dict) and y.get("requested_at"):
        out["yield"] = {
            "requested_at": str(y.get("requested_at")),
            "actor": y.get("actor") if isinstance(y.get("actor"), str) else None,
            "reason": str(y.get("reason") or ""),
        }
    if isinstance(data.get("updated_at"), str):
        out["updated_at"] = data["updated_at"]
    return out


def _write(run_id: str, data: dict) -> dict:
    path = control_path(run_id)
    data["run_id"] = run_id
    data["updated_at"] = _now_iso()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data) + "\n", encoding="utf-8")
    # Atomic replace, the same discipline presence uses, and for the same
    # reason: it is what lets the sweep read with no locking at all and never
    # see half a file.
    os.replace(tmp, path)
    return data


def pending(run_id: str) -> list[dict]:
    """Requests waiting for this run, oldest first. Never raises."""
    return read(run_id)["requests"]


def submit(run_id: str, *, verb: str, text: str, actor: str | None = None,
           request_id: str | None = None) -> dict:
    """Put one request on this run's channel. Returns the channel.

    IDEMPOTENT ON `request_id`, matching `chat_queue.enqueue`: a retried POST
    must not become two requests, and the stored entry keeps its ORIGINAL
    position and timestamp because a retry is the same request arriving again.
    """
    if verb not in VERBS:
        raise ControlError(f"unknown verb {verb!r}; one of {list(VERBS)}")
    text = (text or "").strip()
    if not text:
        raise ControlError("a control request needs text")
    data = read(run_id)
    rid = request_id or uuid.uuid4().hex
    seq = max([int(r.get("seq") or 0) for r in data["requests"]] or [0]) + 1
    entry = {"id": rid, "verb": verb, "text": text, "actor": actor or None,
             "at": _now_iso(), "seq": seq}
    existing = next((i for i, r in enumerate(data["requests"])
                     if r.get("id") == rid), None)
    if existing is None:
        data["requests"].append(entry)
    else:
        kept = data["requests"][existing]
        entry["at"] = kept.get("at") or entry["at"]
        entry["seq"] = int(kept.get("seq") or entry["seq"])
        data["requests"][existing] = entry
    data["requests"].sort(key=_order_key)
    _write(run_id, data)
    return read(run_id)


def cancel(run_id: str, request_id: str) -> bool:
    """Take a request back out before it is delivered. True if one was removed.

    Section 8: queued requests are FIFO and CANCELLABLE. There is no other way
    for a request to leave undelivered, and it is a deliberate act by whoever
    made it.
    """
    data = read(run_id)
    keep = [r for r in data["requests"] if r.get("id") != request_id]
    if len(keep) == len(data["requests"]):
        return False
    data["requests"] = keep
    _write(run_id, data)
    return True


def take(run_id: str) -> list[dict]:
    """Every waiting request, removed from the channel. The sweep's read.

    CONSUMING, because the sweep folds what it takes into the next iteration's
    prompt: leaving the requests in place would re-fold them into every
    iteration afterwards. The yield marker is deliberately NOT consumed -- it is
    a standing instruction to stop, not a message, and it stays readable until
    the run is gone so a reader can still see why the sweep ended.

    NEVER RAISES: an unwritable channel answers with the requests it managed to
    read and leaves the file alone, because failing here would stop a sweep.
    """
    data = read(run_id)
    requests = data["requests"]
    if not requests:
        return []
    data["requests"] = []
    try:
        _write(run_id, data)
    except (OSError, ControlError):
        return requests
    return requests


def request_yield(run_id: str, *, actor: str | None = None,
                  reason: str = "") -> dict:
    """Ask this run to snapshot, release and exit cleanly (sections 8 and 9).

    This is what preempt writes at the START of its grace window. It is a
    request, not a stop: the sweep reads it at its next iteration boundary and
    finishes through the normal finalize path. Whether the grace lapses before
    that happens is `services/control.preempt`'s problem, not this file's.
    """
    data = read(run_id)
    data["yield"] = {"requested_at": _now_iso(), "actor": actor or None,
                     "reason": (reason or "").strip()}
    _write(run_id, data)
    return read(run_id)


def yield_requested(run_id: str) -> dict | None:
    """The standing yield request, or None. Never raises."""
    return read(run_id)["yield"]


def clear_yield(run_id: str) -> bool:
    data = read(run_id)
    if not data["yield"]:
        return False
    data["yield"] = None
    _write(run_id, data)
    return True


def clear(run_id: str) -> dict:
    """Empty the channel, keeping the file."""
    return _write(run_id, _empty(run_id))


def forget(run_id: str) -> bool:
    """Remove the channel entirely, for a run that is over."""
    try:
        control_path(run_id).unlink()
        return True
    except (OSError, ControlError):
        return False


def channels() -> list[str]:
    """Every run id with a control channel on disk. Never raises."""
    try:
        return sorted(p.stem for p in control_dir().glob("*.json"))
    except OSError:
        return []


def fold_text(requests: list[dict]) -> str:
    """What the sweep folds into the next iteration's prompt.

    One request per paragraph, oldest first, and NOTHING ELSE -- no banner, no
    priority marker, no instruction about how urgently to treat them. The
    caller (`scripts/sweep_control.sh`) supplies the framing, so the framing
    lives in one place instead of being half here and half in bash.
    """
    return "\n\n".join(
        (r.get("text") or "").strip()
        for r in sorted(requests or [], key=_order_key)
        if (r.get("text") or "").strip())


# --- CLI ---------------------------------------------------------------------
#
# The whole reason this module has one: the reader is bash. `scripts/control.py`
# is the shim, and like `scripts/presence.py` it does NOT re-exec into the
# backend venv -- everything above is stdlib, so it runs under whatever python3
# a sweep has. That is not an optimization; a control channel a sweep cannot
# read is not a control channel.

def _emit(value: Any) -> int:
    print(json.dumps(value))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="control",
        description="Read and write a run's control channel. Reads never fail: "
                    "an unreadable or malformed channel is an empty one.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    for name, help_text in (("status", "the whole channel as JSON"),
                            ("pending", "requests waiting, oldest first"),
                            ("clear", "empty the channel"),
                            ("forget", "remove the channel file")):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("run_id")

    tk = sub.add_parser("take", help="take every waiting request (consuming)")
    tk.add_argument("run_id")
    tk.add_argument("--format", choices=("json", "text"), default="json",
                    help="text prints what to fold into the next prompt")

    sb = sub.add_parser("submit", help="put a request on the channel")
    sb.add_argument("run_id")
    sb.add_argument("text")
    sb.add_argument("--verb", choices=list(VERBS), default="queue")
    sb.add_argument("--actor", default=None)
    sb.add_argument("--request-id", default=None)

    cx = sub.add_parser("cancel", help="take a waiting request back out")
    cx.add_argument("run_id")
    cx.add_argument("request_id")

    yl = sub.add_parser("yield", help="ask this run to snapshot and exit cleanly")
    yl.add_argument("run_id")
    yl.add_argument("--actor", default=None)
    yl.add_argument("--reason", default="")

    yg = sub.add_parser("yielding",
                        help="exit 0 if a yield has been requested, else 1")
    yg.add_argument("run_id")

    sn = sub.add_parser("snapshot",
                        help="snapshot a tree to refs/wip/<run-id> without "
                             "touching the branch")
    sn.add_argument("run_id")
    sn.add_argument("--repo", required=True)
    sn.add_argument("--message", default="")

    a = ap.parse_args(argv)

    if a.cmd == "status":
        return _emit(read(a.run_id))
    if a.cmd == "pending":
        return _emit(pending(a.run_id))
    if a.cmd == "take":
        taken = take(a.run_id)
        if a.format == "text":
            text = fold_text(taken)
            if text:
                print(text)
            return 0
        return _emit(taken)
    if a.cmd == "yielding":
        y = yield_requested(a.run_id)
        print(json.dumps(y))
        return 0 if y else 1
    if a.cmd == "snapshot":
        result = git_service.snapshot(
            a.repo, ref=f"refs/wip/{a.run_id}",
            message=a.message or f"workshop yield snapshot: {a.run_id}",
            include_untracked=True)
        print(json.dumps(result))
        return 0 if result.get("ok") else 1

    # Writers, which may legitimately refuse.
    try:
        if a.cmd == "submit":
            return _emit(submit(a.run_id, verb=a.verb, text=a.text,
                                actor=a.actor, request_id=a.request_id))
        if a.cmd == "cancel":
            ok = cancel(a.run_id, a.request_id)
            print(json.dumps({"cancelled": ok}))
            return 0 if ok else 1
        if a.cmd == "yield":
            return _emit(request_yield(a.run_id, actor=a.actor, reason=a.reason))
        if a.cmd == "clear":
            return _emit(clear(a.run_id))
        if a.cmd == "forget":
            print(json.dumps({"forgotten": forget(a.run_id)}))
            return 0
    except ControlError as exc:
        print(json.dumps({"error": str(exc)}))
        return 1
    return 2


if __name__ == "__main__":  # `python3 -m services.run_control ...`
    sys.exit(main())
