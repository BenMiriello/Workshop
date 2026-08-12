"""A change counter per scope: "has the shared state here moved since you last looked?"

THE PROBLEM. Work is discussed in one chat and recorded as notes and tickets;
another chat in the same scope has no idea. Keeping them in step by having every
chat search for updates means paying retrieval (and possibly an LLM) on every
turn, in every chat, almost always to learn that nothing changed. That is the
polling tax: compute burned in the quiet periods, and still a gap between polls
where an update is missed.

THE SHAPE OF THE ANSWER. Knowing something CHANGED is not a semantic question,
so it must not cost a semantic search. It is one integer. Writers bump a
counter for the scope they wrote to; a reader compares the current value with
the one it last saw. Equal means silence, and silence is the common case and is
free. Only when they differ does anything read the actual notes or tickets.

This is deliberately the cheap half of the design. Embeddings still answer
"which note is relevant to this message" (services/knowledge does that, on
demand). They are the wrong tool for "did anything happen", and using them there
would reintroduce exactly the per-turn cost this exists to avoid.

Storage is one small JSON file, written with the same atomic replace the rest of
the runtime state uses. A counter that is lost resets to 0, which makes every
reader think it is behind and re-read once: the failure mode is one redundant
read, never a missed update.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any
from services import paths

WORKSHOP_ROOT = paths.workshop_root()
STORE = Path(os.environ.get(
    "WORKSHOP_SCOPE_VERSIONS",
    str(WORKSHOP_ROOT / ".runtime" / "scope-versions.json"),
))

# What kind of change happened, kept alongside the counter so a reader can say
# WHAT moved without opening the stores. Free-form by design: the counter is the
# contract, the label is a courtesy.
KINDS = ("note", "ticket", "run")


def _load() -> dict[str, Any]:
    try:
        data = json.loads(STORE.read_text())
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _save(data: dict[str, Any]) -> None:
    try:
        STORE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STORE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
        tmp.replace(STORE)
    except OSError:
        # A counter is an optimisation, never a correctness requirement: if it
        # cannot be written, readers simply fall back to looking.
        pass


def bump(scope: str, kind: str = "note", detail: str = "") -> int:
    """Record that shared state in `scope` changed. Returns the new version.

    Called by whatever WROTE, at the moment it wrote, which is what makes this
    event-driven rather than polled."""
    if not scope:
        return 0
    data = _load()
    rec = data.get(scope)
    rec = dict(rec) if isinstance(rec, dict) else {}
    rec["version"] = int(rec.get("version") or 0) + 1
    rec["kind"] = kind if kind in KINDS else "note"
    rec["detail"] = (detail or "")[:160]
    rec["at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    data[scope] = rec
    _save(data)
    return int(rec["version"])


def current(scope: str) -> int:
    """The scope's version now. 0 means nothing has ever been recorded."""
    rec = _load().get(scope)
    return int(rec.get("version") or 0) if isinstance(rec, dict) else 0


def describe(scope: str) -> dict:
    """Version plus the last change's kind/detail/time, for a reader that wants
    to say what moved."""
    rec = _load().get(scope)
    if not isinstance(rec, dict):
        return {"scope": scope, "version": 0, "kind": None, "detail": "", "at": None}
    return {
        "scope": scope,
        "version": int(rec.get("version") or 0),
        "kind": rec.get("kind"),
        "detail": rec.get("detail") or "",
        "at": rec.get("at"),
    }


def changed_since(scope: str, seen: object) -> bool:
    """Has anything landed in this scope since the reader last looked?

    An unset/garbage `seen` counts as behind: a reader with no memory of the
    scope should look once rather than silently assume it is current."""
    try:
        seen_v = int(seen)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return current(scope) > 0
    return current(scope) > seen_v
