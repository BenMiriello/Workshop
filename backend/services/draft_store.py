"""Chat composer draft persistence — per-owner, keyed by chat context.

A "draft" is the unsent text sitting in a chat composer for a given
`context_key` (e.g. "build:my-agent" or "general"). The browser owns the
instant-restore copy (localStorage); this store is the durable, cross-device
mirror so the same draft shows up on another tab or device.

Last-write-wins by the client's wall-clock timestamp — single-user box, low
concurrency, so atomic write-temp-then-rename is all the safety this needs
(matches chat_store). A strictly-older write is rejected so an out-of-order /
slow PUT from one device can't clobber a newer draft from another.

One JSON file per owner: `.runtime/users/<owner_id>/chat/drafts.json`, mapping
context_key -> {text, ts, client_id}. Empty text deletes the entry.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import ownership


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


def get_all() -> dict:
    return _load()


def get_draft(key: str) -> dict:
    entry = _load().get(key)
    if not entry:
        return {"key": key, "text": "", "ts": 0, "client_id": ""}
    return {"key": key, **entry}


def put_draft(key: str, text: str, ts: int, client_id: str) -> dict:
    """Store (or clear, when text is empty) the draft for `key`. Returns the
    authoritative entry — which is the EXISTING one when it is strictly newer,
    so the caller can tell its write was superseded and reconcile."""
    ts = int(ts)
    data = _load()
    existing = data.get(key)
    if existing and int(existing.get("ts", 0)) > ts:
        return {"key": key, **existing, "superseded": True}
    if not text:
        if key in data:
            data.pop(key, None)
            _save(data)
        return {"key": key, "text": "", "ts": ts, "client_id": client_id}
    entry = {"text": text, "ts": ts, "client_id": client_id}
    data[key] = entry
    _save(data)
    return {"key": key, **entry}


def clear_draft(key: str) -> None:
    data = _load()
    if key in data:
        data.pop(key, None)
        _save(data)
