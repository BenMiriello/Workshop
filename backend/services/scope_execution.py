"""Per-scope execution mode: does a scope's filed work get executed in the
background, and how far.

This is the per-SCOPE half of the two-axis autonomy model
(work-loop-core-design-2026-08-21.md section 4, chat-work-linkage): FILING
behaviour is a per-chat choice (does this chat file work), EXECUTION behaviour is
a per-scope choice (does this scope's filed work run in the background). They were
conflated into one opaque "mode" before; keeping them apart is what makes the
setting legible. A ticket lives in a scope, not a chat, and the sweep works a
scope's queue, so "can this be auto-executed" belongs to the scope.

Composes UNDER the global autonomy override (services/autonomy_override): the
global off-switch supersedes every scope. This setting only ever decides, for a
scope the global switch has not disabled, whether its READY work is picked up.

Modes (the ratified autonomy enum, ADR 0008):
  reactive    DEFAULT, fail-closed. Nothing in this scope auto-executes; filed
              work waits for a person. next_ready(unattended=True) yields nothing.
  supervised  filed executable work auto-runs to in_review; the operator is the
              final gate before done.
  autonomous  filed executable work auto-runs to done.

Keyed by the scope's ROOT path (the tree the sweep works on), because that is what
next_ready() and the sweep already hold; the API/UI translate a scope key to its
root when they set it. The store is a single JSON under .runtime, the same shape
and home as autonomy_override, so a fresh deploy is fail-closed (absent file =>
every scope reactive) with nothing to migrate.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

MODES = ("reactive", "supervised", "autonomous")
DEFAULT_MODE = "reactive"

# The stage a scope's auto-execution may reach. supervised stops at in_review so
# the operator is the final gate; autonomous may go to done. Read by the driver.
_CEILING = {"reactive": None, "supervised": "in_review", "autonomous": "done"}


def _store_path() -> Path:
    # Mirrors autonomy_override: WORKSHOP_ROOT/.runtime/<file>. WORKSHOP_ROOT is
    # remapped to the mountpoint inside a confined turn (scoped_exec.turn_env), so
    # this resolves to the real tree whether confined or not.
    root = Path(os.environ.get("WORKSHOP_ROOT", str(Path(__file__).resolve().parents[2])))
    return root / ".runtime" / "scope-execution.json"


def _key(root: str | Path) -> str:
    return os.path.realpath(str(root))


def _read() -> dict[str, Any]:
    p = _store_path()
    try:
        return json.loads(p.read_text())
    except (FileNotFoundError, ValueError):
        return {}


def _write(data: dict[str, Any]) -> None:
    p = _store_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
    os.replace(tmp, p)  # atomic


def get_mode(root: str | Path) -> str:
    """The scope's execution mode, DEFAULT reactive (fail-closed) when unset."""
    mode = _read().get(_key(root))
    return mode if mode in MODES else DEFAULT_MODE


def set_mode(root: str | Path, mode: str) -> str:
    if mode not in MODES:
        raise ValueError(f"unknown execution mode {mode!r}; one of {MODES}")
    data = _read()
    if mode == DEFAULT_MODE:
        # Storing the default is noise: absence already means reactive. Drop the
        # key so the file stays a list of scopes the operator deliberately armed.
        data.pop(_key(root), None)
    else:
        data[_key(root)] = mode
    _write(data)
    return mode


def is_armed(root: str | Path) -> bool:
    """True when this scope's ready work may be auto-executed at all."""
    return get_mode(root) != "reactive"


def stage_ceiling(root: str | Path) -> str | None:
    """How far auto-execution may advance: in_review (supervised) or done
    (autonomous). None when reactive. The driver reads this to know where to stop."""
    return _CEILING[get_mode(root)]


def armed_roots() -> list[tuple[str, str]]:
    """Every scope the operator has deliberately armed, as (root, mode) pairs.

    The store only ever holds non-default entries (set_mode drops a key set back
    to reactive), so this is exactly the armed set with no filtering needed. It
    is what the work driver reconciles the resident runners against.
    """
    return [(root, mode) for root, mode in sorted(_read().items()) if mode in MODES
            and mode != DEFAULT_MODE]
