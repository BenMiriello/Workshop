"""Deploy record store — one record per (project, deploy target) binding.

A Deploy binds a project to a hosted target (e.g. Cloudflare Pages project
"my-site"). Each deploy attempt runs through the Job pipeline
(kind="deploy") — attempts are not stored here; only the target binding,
the source config, and the "what's currently live" pointer.

Backing store: `.runtime/deploys.json`, single JSON file with `{"deploys":
{id: record}}`. Same one-writer pattern as projects.json — the process
mutating it holds the whole file for the RMW.

The Deploy record IS the config. Provider-specific fields go inside
`target` and `source`; the rest is generic.
"""
from __future__ import annotations

import json
import os
import random
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# IMPORTED, not restated. This tuple used to be typed out here a second time;
# services/triggers.py is the single source among the files this pass owns
# (coherence-overhaul-2026-08-16 T5). db/models.py's `AGENT_AUTONOMY` (the DB
# CHECK constraint) and services/agents_registry.py's own copy are two
# further restatements outside this module's reach — see the note in
# services/triggers.py for why those are not collapsed here too.
from services.triggers import AUTONOMY_MODES


WORKSHOP_ROOT = Path(os.environ.get("WORKSHOP_ROOT", str(Path(__file__).resolve().parents[2])))
STORE_PATH = Path(os.environ.get(
    "WORKSHOP_DEPLOYS_STORE",
    str(WORKSHOP_ROOT / ".runtime" / "deploys.json"),
))

DEFAULT_AUTONOMY = "reactive"

# Name discipline: reused from project slugs but a little looser (uppercase
# allowed since some providers surface case-sensitive names). Kept short so it
# fits in a UI row.
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,47}$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_id() -> str:
    return f"dep_{int(datetime.now(timezone.utc).timestamp() * 1000)}_{random.randint(0x1000, 0xffff):04x}"


@dataclass
class Deploy:
    id: str
    project_slug: str
    name: str                                   # "prod" | "preview" | free
    target: dict[str, Any]                      # {kind, ...provider fields}
    source: dict[str, Any]                      # {kind, branch, build:{cmd,output_dir,...}}
    domain: str | None = None                   # display only; provider owns DNS
    autonomy: str = DEFAULT_AUTONOMY
    created_at: str = ""
    updated_at: str = ""
    last_run_id: str | None = None
    current: dict[str, Any] | None = None       # {run_id, commit_sha, deployed_at, url, upload_ref}

    def to_json(self) -> dict:
        d = asdict(self)
        return {k: v for k, v in d.items() if v is not None}


def _read_store() -> dict:
    if not STORE_PATH.exists():
        return {"deploys": {}}
    try:
        data = json.loads(STORE_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("deploys"), dict):
            return {"deploys": {}}
        return data
    except (OSError, json.JSONDecodeError):
        return {"deploys": {}}


def _write_store(data: dict) -> None:
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STORE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, STORE_PATH)


def list_all() -> list[dict]:
    """All Deploys across all projects, most-recently-updated first."""
    data = _read_store()
    recs = list(data.get("deploys", {}).values())
    recs.sort(key=lambda r: r.get("updated_at") or r.get("created_at") or "", reverse=True)
    return recs


def list_by_project(slug: str) -> list[dict]:
    return [r for r in list_all() if r.get("project_slug") == slug]


def get(deploy_id: str) -> dict | None:
    return _read_store().get("deploys", {}).get(deploy_id)


def _validate_name(name: str) -> None:
    if not name or not _NAME_RE.match(name):
        raise ValueError(f"invalid deploy name: {name!r}")


def _validate_autonomy(mode: str) -> str:
    if mode not in AUTONOMY_MODES:
        raise ValueError(f"invalid autonomy mode: {mode!r}")
    return mode


def create(
    *,
    project_slug: str,
    name: str,
    target: dict[str, Any],
    source: dict[str, Any],
    domain: str | None = None,
    autonomy: str = DEFAULT_AUTONOMY,
) -> dict:
    _validate_name(name)
    _validate_autonomy(autonomy)
    if not target or "kind" not in target:
        raise ValueError("target.kind is required")
    if not source or "kind" not in source:
        raise ValueError("source.kind is required")
    now = _now()
    rec = Deploy(
        id=_new_id(),
        project_slug=project_slug,
        name=name,
        target=dict(target),
        source=dict(source),
        domain=domain,
        autonomy=autonomy,
        created_at=now,
        updated_at=now,
    )
    data = _read_store()
    data.setdefault("deploys", {})[rec.id] = rec.to_json()
    _write_store(data)
    return rec.to_json()


def update(deploy_id: str, **patch: Any) -> dict:
    data = _read_store()
    rec = data.get("deploys", {}).get(deploy_id)
    if not rec:
        raise KeyError(deploy_id)
    for k in ("name", "target", "source", "domain", "autonomy"):
        if k in patch and patch[k] is not None:
            if k == "name":
                _validate_name(patch[k])
            if k == "autonomy":
                _validate_autonomy(patch[k])
            rec[k] = patch[k]
    rec["updated_at"] = _now()
    data["deploys"][deploy_id] = rec
    _write_store(data)
    return rec


def delete(deploy_id: str) -> bool:
    data = _read_store()
    if deploy_id not in data.get("deploys", {}):
        return False
    del data["deploys"][deploy_id]
    _write_store(data)
    return True


def set_last_run(deploy_id: str, run_id: str) -> None:
    data = _read_store()
    rec = data.get("deploys", {}).get(deploy_id)
    if not rec:
        return
    rec["last_run_id"] = run_id
    rec["updated_at"] = _now()
    _write_store(data)


def set_current(deploy_id: str, current: dict) -> None:
    """Called by the runner on a successful deploy. `current` = {run_id,
    commit_sha, deployed_at, url, upload_ref?}. Overwrites the previous
    current pointer wholesale — history lives in the Job feed, not here."""
    data = _read_store()
    rec = data.get("deploys", {}).get(deploy_id)
    if not rec:
        return
    rec["current"] = dict(current)
    rec["last_run_id"] = current.get("run_id") or rec.get("last_run_id")
    rec["updated_at"] = _now()
    _write_store(data)
