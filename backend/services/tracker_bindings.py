"""One external tracker connection for the system, and where each scope points.

THE SHAPE, AND WHY. The operator's constraint is "only one max integration with
another external ticket service at a time". So there is ONE connection record —
one provider, one credential — and per-scope TARGETS within it. A project can
point at its own repo and the workshop root at another, but there is never a
second provider, a second credential, or a second set of semantics to hold in
your head. Multi-provider aggregation is the feature that sinks every sync
product and it is not coming.

    {
      "provider": "github",
      "credential_source": "gh" | "device_flow" | "pat",
      "mode": "off" | "import" | "sync",
      "filter": {"kind": "label", "label": "workshop"},
      "targets": {"workshop": "<owner>/workshop-tickets",
                  "project:notes": "<owner>/notes"},
      "cursors": {"workshop": "2026-08-10T05:00:00Z"},
      "connected_at": iso, "last_poll": iso, "last_error": str|None
    }

Modes, and nothing else: `import` never writes (someone else's tracker), `sync` is
two-way per the ownership table in services/trackers. Every other knob that looked
necessary collapsed into a decision already made — which tickets publish is
answered by approval, whether drafts publish is answered by "a draft is not work
yet", and whether to delete remote issues on disconnect is answered by "never".
"""
from __future__ import annotations

import fcntl
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from services import ownership, trackers

STORE_NAME = "tracker.json"


def _path() -> Path:
    d = ownership.user_runtime_dir() / "trackers"
    d.mkdir(parents=True, exist_ok=True)
    return d / STORE_NAME


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _blank() -> dict:
    return {"provider": None, "credential_source": "gh", "mode": "off",
            "filter": {"kind": "label", "label": "workshop"},
            "targets": {}, "cursors": {},
            "connected_at": None, "last_poll": None, "last_error": None}


def load() -> dict:
    p = _path()
    if not p.exists():
        return _blank()
    try:
        data = json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return _blank()
    if not isinstance(data, dict):
        return _blank()
    out = _blank()
    out.update({k: v for k, v in data.items() if k in out})
    # Defensive: a hand edit must not produce a mode the sync engine cannot read.
    if out["mode"] not in trackers.MODES:
        out["mode"] = "off"
    if not isinstance(out.get("targets"), dict):
        out["targets"] = {}
    if not isinstance(out.get("cursors"), dict):
        out["cursors"] = {}
    if not isinstance(out.get("filter"), dict):
        out["filter"] = {"kind": "label", "label": "workshop"}
    return out


def _write(rec: dict) -> dict:
    p = _path()
    tmp = p.with_name(f"{p.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(rec, indent=2) + "\n")
    os.replace(tmp, p)
    return rec


def _locked(fn):
    """Serialize read-modify-write on the single connection record."""
    def wrapper(*a, **kw):
        lock = _path().with_suffix(".lock")
        with open(lock, "w") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            return fn(*a, **kw)
    return wrapper


@_locked
def connect(*, provider: str, target: str, scope: str = "workshop",
            mode: str = "import", credential_source: str = "gh",
            label: str | None = None) -> dict:
    """Open THE connection, or point another scope at a target within it.

    Connecting a second provider is refused rather than silently replacing the
    first: it is a decision with consequences for every linked ticket, so it goes
    through `disconnect` deliberately.
    """
    if provider not in trackers.PROVIDERS:
        raise ValueError(f"unsupported provider: {provider!r} "
                         f"(built: {', '.join(trackers.PROVIDERS)})")
    if mode not in trackers.MODES:
        raise ValueError(f"mode must be one of {trackers.MODES}")
    rec = load()
    if rec["provider"] and rec["provider"] != provider:
        raise ValueError(
            f"already connected to {rec['provider']}; disconnect it first "
            "(one integration at a time)")
    from services import github as gh_repo
    gh_repo.reject_blocked(target)

    rec["provider"] = provider
    rec["credential_source"] = credential_source
    rec["mode"] = mode
    rec["targets"] = {**rec["targets"], scope: target}
    if label:
        rec["filter"] = {**rec["filter"], "kind": "label", "label": label}
    rec["connected_at"] = rec["connected_at"] or _now()
    rec["last_error"] = None
    return _write(rec)


@_locked
def set_mode(mode: str) -> dict:
    if mode not in trackers.MODES:
        raise ValueError(f"mode must be one of {trackers.MODES}")
    rec = load()
    rec["mode"] = mode
    return _write(rec)


@_locked
def set_filter(*, kind: str, label: str = "workshop", query: str = "") -> dict:
    if kind not in ("label", "query", "all"):
        raise ValueError("filter kind must be label, query or all")
    rec = load()
    rec["filter"] = {"kind": kind, "label": label, "query": query}
    return _write(rec)


@_locked
def note_poll(scope: str, cursor: str | None, error: str | None = None) -> dict:
    rec = load()
    rec["last_poll"] = _now()
    rec["last_error"] = error
    if cursor:
        rec["cursors"] = {**rec["cursors"], scope: cursor}
    return _write(rec)


@_locked
def disconnect() -> dict:
    """Close the connection. Remote issues are NEVER touched: deleting someone's
    issues because an integration was turned off is indefensible. What happens to
    the local tickets that came from it is a separate, explicit choice the caller
    makes (see routes/trackers)."""
    rec = load()
    prior = {"provider": rec.get("provider"), "targets": dict(rec.get("targets") or {})}
    fresh = _blank()
    fresh["credential_source"] = rec.get("credential_source") or "gh"
    _write(fresh)
    return {**fresh, "disconnected": prior}


def target_for(scope: str) -> str | None:
    """The target a scope points at, or None. A scope with no target of its own
    does NOT inherit one: filing into a repo nobody chose is how tickets end up in
    a stranger's tracker."""
    return (load().get("targets") or {}).get(scope)


def binding_for(scope: str) -> dict | None:
    """A ready-to-use binding for one scope, or None when this scope is not
    integrated. This is what `trackers.for_binding` consumes."""
    rec = load()
    target = (rec.get("targets") or {}).get(scope)
    if not rec.get("provider") or not target or rec.get("mode") == "off":
        return None
    return {"provider": rec["provider"], "target": target,
            "mode": rec["mode"], "scope": scope,
            "credential_source": rec.get("credential_source") or "gh",
            "filter": rec.get("filter") or {},
            "cursor": (rec.get("cursors") or {}).get(scope)}


def filter_for(rec: dict | None = None) -> trackers.Filter:
    f = (rec or load()).get("filter") or {}
    return trackers.Filter(kind=str(f.get("kind") or "label"),
                           label=str(f.get("label") or "workshop"),
                           query=str(f.get("query") or ""))


def writes_allowed(scope: str) -> bool:
    """`import` means the tracker belongs to someone else: we read it, and the only
    thing we ever write is a comment."""
    b = binding_for(scope)
    return bool(b and b["mode"] == "sync")
