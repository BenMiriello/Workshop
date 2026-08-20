"""L3 custom instructions: the operator's own free-text system prompt, per
scope/app. This is the editable layer beneath the fixed platform layers (L0
kernel, L1 orientation, L2 persona). It is authored by the operator, is lower
authority than the platform layers, and is injected into the prompt by
ContextProvider.custom_prompt() under the "=== CUSTOM INSTRUCTIONS
(operator-set) ===" banner.

Per-owner file so it partitions exactly like attachments and knowledge do,
keyed by the chat's knowledge scope ("workshop", "development",
"project:<slug>", "app:<name>", "agent:<id>"). Stored at
<owner runtime>/custom-instructions.json as {scope_key: text}.
"""
from __future__ import annotations

import json
import logging

from services import ownership

log = logging.getLogger("workshop.custom_instructions")

MAX_CHARS = 20_000


def _path(owner_id: int | None = None):
    return ownership.user_runtime_dir(owner_id) / "custom-instructions.json"


def _load(owner_id: int | None = None) -> dict:
    p = _path(owner_id)
    try:
        if p.exists():
            data = json.loads(p.read_text("utf-8"))
            if isinstance(data, dict):
                return data
    except Exception as e:  # never let a bad file break the chat path
        log.warning("custom_instructions load failed: %s", e)
    return {}


def get(scope_key: str | None, owner_id: int | None = None) -> str:
    """The operator's custom instructions for one scope, or "" if none set."""
    key = (scope_key or "workshop").strip() or "workshop"
    return (_load(owner_id).get(key) or "").strip()


def all_scopes(owner_id: int | None = None) -> dict:
    """Every scope that has non-empty custom instructions, for the settings UI."""
    return {k: v for k, v in _load(owner_id).items() if (v or "").strip()}


def set(scope_key: str | None, text: str, owner_id: int | None = None) -> str:
    """Store (or clear, when text is blank) the custom instructions for a scope.
    Returns the stored text. Atomic write-then-replace so a reader never sees a
    half-written file."""
    key = (scope_key or "workshop").strip() or "workshop"
    text = (text or "").strip()[:MAX_CHARS]
    data = _load(owner_id)
    if text:
        data[key] = text
    else:
        data.pop(key, None)
    p = _path(owner_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), "utf-8")
    tmp.replace(p)
    return text
