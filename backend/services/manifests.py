"""App manifest store (data layer only).

Loads and exposes the per-app manifest declared in `backend/apps.json`. Each
app entry declares the routes it owns (`api_prefixes`), the capabilities it
`provides` / `needs`, its `sensitivity`, and the UI `kit` it depends on. This is
the data foundation described in `docs/app-platform-plan.md` section 3 and
`docs/workshop-v1/03-ui-kit-and-marketplace.md` section 1.

This module is **data only**: it declares manifests and exposes them read-only.
It does NOT enforce anything. `resolve_app_for_path` is a pure helper provided
for FUTURE enforcement (the scope/grant middleware in app-platform-plan.md
section 3.3); nothing here is wired into request handling, and no default-deny
is implied by its existence.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

# apps.json lives next to the backend package (operator-editable, backend-owned).
MANIFESTS_PATH = Path(os.environ.get(
    "WORKSHOP_APPS_MANIFEST",
    str(Path(__file__).resolve().parent.parent / "apps.json"),
))

DEFAULT_KIT = "@workshop/ui@^1"

# Seed used when apps.json is missing. Mirrors the committed apps.json so a fresh
# checkout (or a deployment that lost the file) still has the known built-ins.
# Values consistent with app-platform-plan.md section 3.1.
SEED: dict[str, dict[str, Any]] = {
    "finance": {
        "title": "Finance",
        "sensitivity": "private",
        "api_prefixes": ["/finance", "/budget", "/plaid"],
        "provides": ["finance.read"],
        "needs": [],
        "kit": DEFAULT_KIT,
    },
    "budget": {
        "title": "Budget",
        "sensitivity": "private",
        "api_prefixes": ["/budget", "/plaid"],
        "provides": [],
        "needs": ["finance.read"],
        "kit": DEFAULT_KIT,
    },
    "birds": {
        "title": "Yard Birds",
        "sensitivity": "shareable",
        "api_prefixes": ["/birds"],
        "provides": ["birds.read"],
        "needs": [],
        "kit": DEFAULT_KIT,
    },
    "location": {
        "title": "Location",
        "sensitivity": "private",
        "api_prefixes": ["/location"],
        "provides": ["location.read"],
        "needs": [],
        "kit": DEFAULT_KIT,
    },
    "commute": {
        "title": "Commute",
        "sensitivity": "private",
        "api_prefixes": ["/commute"],
        "provides": [],
        "needs": ["location.read"],
        "kit": DEFAULT_KIT,
    },
}

_SENSITIVITIES = {"private", "shareable", "public"}
_ACTION_POLICIES = {"read", "confirm", "allow"}


def _normalize_entry(app_id: str, raw: Any) -> dict[str, Any]:
    """Coerce one raw manifest entry into the canonical shape, filling defaults.

    Tolerant by design: a malformed/partial entry is normalized rather than
    rejected, so a hand-edited apps.json never breaks the read endpoint. The
    `kit` defaults to DEFAULT_KIT; lists default to empty; an unknown
    `sensitivity` falls back to "private" (the safest default for the data layer).
    """
    if not isinstance(raw, dict):
        raw = {}
    sensitivity = raw.get("sensitivity")
    if sensitivity not in _SENSITIVITIES:
        sensitivity = "private"
    # Optional per-action policy map {<action>: "read"|"confirm"|"allow"} consumed
    # by services/permissions.py. Absent => {} (every action takes its default).
    # An unrecognized policy value normalizes to "read" (the safe default).
    raw_actions = raw.get("actions")
    actions: dict[str, str] = {}
    if isinstance(raw_actions, dict):
        for name, policy in raw_actions.items():
            actions[str(name)] = policy if policy in _ACTION_POLICIES else "read"
    # Per-app UI config. `agent_button` controls whether the floating assistant
    # dock (ChatDock) renders inside the app. Defaults to True so every app can
    # have it; the operator toggles it per app from the Apps "Manage" view.
    raw_ui = raw.get("ui")
    raw_ui = raw_ui if isinstance(raw_ui, dict) else {}
    ui = {"agent_button": raw_ui.get("agent_button", True) is not False}
    return {
        "title": str(raw.get("title") or app_id),
        "sensitivity": sensitivity,
        "api_prefixes": [str(p) for p in (raw.get("api_prefixes") or []) if isinstance(p, str)],
        "provides": [str(c) for c in (raw.get("provides") or []) if isinstance(c, str)],
        "needs": [str(c) for c in (raw.get("needs") or []) if isinstance(c, str)],
        "actions": actions,
        "ui": ui,
        "kit": str(raw.get("kit") or DEFAULT_KIT),
    }


def _write_seed() -> None:
    """Create apps.json from SEED. Atomic-safe (write temp + rename)."""
    MANIFESTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"apps": SEED}, indent=2) + "\n"
    tmp = MANIFESTS_PATH.with_suffix(MANIFESTS_PATH.suffix + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, MANIFESTS_PATH)


def load_manifests() -> dict[str, dict[str, Any]]:
    """Return the manifest map keyed by app id, every entry normalized.

    If apps.json does not exist, it is CREATED from SEED on first load and the
    seed is returned. If it exists but is unreadable/corrupt, the in-memory SEED
    is returned (never raises) so the read endpoint stays available.
    """
    if not MANIFESTS_PATH.exists():
        try:
            _write_seed()
        except OSError:
            # Read-only filesystem or similar; still serve the seed in-memory.
            return {k: _normalize_entry(k, v) for k, v in SEED.items()}
        return {k: _normalize_entry(k, v) for k, v in SEED.items()}

    try:
        data = json.loads(MANIFESTS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {k: _normalize_entry(k, v) for k, v in SEED.items()}

    apps = data.get("apps") if isinstance(data, dict) else None
    if not isinstance(apps, dict):
        return {k: _normalize_entry(k, v) for k, v in SEED.items()}

    return {str(app_id): _normalize_entry(str(app_id), raw) for app_id, raw in apps.items()}


def get_manifest(app_id: str) -> dict[str, Any] | None:
    """Return a single app's normalized manifest, or None if not declared."""
    return load_manifests().get(app_id)


def set_app_ui(app_id: str, ui: dict[str, Any]) -> dict[str, Any] | None:
    """Merge UI settings into one app's manifest entry and persist apps.json.

    Reads the raw on-disk doc (falling back to SEED), merges the given `ui` keys
    into that app's `ui` block, and atomically rewrites apps.json. Returns the
    re-normalized manifest entry, or None if the app is not declared. Only known
    UI keys are accepted (currently `agent_button`); unknown keys are ignored.
    """
    try:
        data = json.loads(MANIFESTS_PATH.read_text(encoding="utf-8"))
        apps = data.get("apps") if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        apps = None
    if not isinstance(apps, dict):
        # No usable file on disk — seed it first so the write has a base.
        apps = {k: dict(v) for k, v in SEED.items()}
        data = {"apps": apps}

    if app_id not in apps:
        return None

    entry = apps[app_id] if isinstance(apps[app_id], dict) else {}
    cur_ui = entry.get("ui") if isinstance(entry.get("ui"), dict) else {}
    if "agent_button" in ui:
        cur_ui["agent_button"] = ui["agent_button"] is not False
    entry["ui"] = cur_ui
    apps[app_id] = entry

    MANIFESTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"apps": apps}, indent=2) + "\n"
    tmp = MANIFESTS_PATH.with_suffix(MANIFESTS_PATH.suffix + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, MANIFESTS_PATH)

    return _normalize_entry(app_id, entry)


def resolve_app_for_path(path: str, manifests: dict[str, dict[str, Any]] | None = None) -> str | None:
    """Resolve which app owns an API path by longest `api_prefix` match (pure).

    A prefix matches when `path` equals it or continues with a "/" boundary, so
    "/budget" matches "/budget" and "/budget/x" but not "/budgetary". When
    several apps declare overlapping prefixes (e.g. finance owns "/budget" and
    budget also owns "/budget"), the LONGEST matching prefix wins; on a tie the
    candidates are sorted by app id for determinism.

    This is provided for FUTURE enforcement only. It is a pure function and is
    not wired into any request path. Returns the owning app id, or None.
    """
    if manifests is None:
        manifests = load_manifests()

    # Normalize: ensure a leading slash, drop any query/fragment, strip trailing
    # slash (except root) so "/budget/" and "/budget" resolve the same.
    p = path.split("?", 1)[0].split("#", 1)[0]
    if not p.startswith("/"):
        p = "/" + p
    if len(p) > 1:
        p = p.rstrip("/")

    best_app: str | None = None
    best_len = -1
    for app_id in sorted(manifests):
        for prefix in manifests[app_id].get("api_prefixes", []):
            pref = prefix.rstrip("/") or "/"
            if p == pref or p.startswith(pref + "/"):
                if len(pref) > best_len:
                    best_len = len(pref)
                    best_app = app_id
    return best_app
