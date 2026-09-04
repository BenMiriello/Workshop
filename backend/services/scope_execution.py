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

Keyed by the SCOPE (development, project:<slug>, agent:<slug>), not by the
scope's root path. The root is only how a scope renders in one namespace: a
confined chat turn sees its scope root at the mountpoint (/workspace) while the
backend sees it at its real location, so a path key written from inside a turn
was unresolvable outside it. Measured 2026-08-22: arming `development` from a
chat turn stored "/workspace", and the runner the backend then started for it
logged `project not found or no dir: /workspace`. A scope is the logical thing
and every namespace agrees on its name; `is_armed_root` and `armed_roots` do the
translation, in whichever namespace is asking. Legacy path keys are folded on
read (see _migrate_paths).

The store is a single JSON under .runtime, the same shape and home as
autonomy_override, so a fresh deploy is fail-closed (absent file => every scope
reactive).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

# ONE DECLARATION OF THE RATIFIED ENUM (ADR 0008), not a fourth copy of it.
# The docstring above already calls this "the ratified autonomy enum", and
# `check_one_definition.py` was flagging the same three values declared in
# `services/triggers.py`, here, and `frontend/src/lib/autonomy.ts`. A closed
# enum spelled twice in one language is one rename away from disagreeing, and
# the disagreement would be a scope silently refusing to execute.
#
# `triggers` is the canonical Python home and is a safe import: it depends only
# on `services.jobs`, and nothing in that chain reaches back here. The
# cross-language pair (Python + TypeScript) is not a duplicate the checker
# counts and is the seam the generated client would have to close.
from services.triggers import AUTONOMY_MODES as MODES

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


def _read() -> dict[str, Any]:
    p = _store_path()
    try:
        data = json.loads(p.read_text())
    except (FileNotFoundError, ValueError):
        return {}
    if not any(k.startswith("/") for k in data):
        return data
    migrated = _migrate_paths(data)
    # PERSIST IT, once, from the namespace that can still resolve the old key.
    # A read-only migration is namespace-dependent in the worst way: the path
    # "/workspace" maps to `development` inside a confined turn and to nothing
    # outside it, so the same file read from the backend silently yielded an
    # EMPTY armed set and disarmed the scope. Measured 2026-08-22: the runner
    # swept, found the scope unarmed, reported "no ready tasks" and exited 0.
    # Writing it back the moment a namespace can translate it means every later
    # reader, in any namespace, sees the scope key.
    #
    # Only when nothing was lost in translation: a partial fold would write away
    # the very key another namespace might still resolve.
    if len(migrated) == len(data):
        try:
            _write(migrated)
        except OSError:
            pass  # read-only or racing writer; the fold still applies in memory
    return migrated


def _migrate_paths(data: dict[str, Any]) -> dict[str, Any]:
    """Fold legacy PATH keys onto scope keys.

    The store used to be keyed by the scope's root path. That is not a stable
    identity: a confined turn sees its scope root at the mountpoint (/workspace)
    while the backend sees it at its real location, so a scope armed from inside
    a chat turn wrote a key the backend could not resolve, and the runner it
    started was handed a directory that does not exist in its own namespace.
    Measured 2026-08-22: `project_ralph: project not found or no dir: /workspace`.

    Read-time only, and it drops a path that no longer maps to a scope rather
    than guessing.
    """
    from services import task_scope

    out: dict[str, Any] = {}
    for k, v in data.items():
        if not k.startswith("/"):
            out[k] = v
            continue
        try:
            key = task_scope.key_for_root(k)
        except Exception:
            key = None
        if key:
            out.setdefault(key, v)
    return out


def _write(data: dict[str, Any]) -> None:
    p = _store_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
    os.replace(tmp, p)  # atomic


def _scope_of(root: str | Path) -> str | None:
    """Which scope this task store belongs to, resolved in THIS namespace."""
    from services import task_scope

    try:
        return task_scope.key_for_root(str(root))
    except Exception:
        return None


def get_mode(scope: str) -> str:
    """The scope's execution mode, DEFAULT reactive (fail-closed) when unset."""
    mode = _read().get(scope)
    return mode if mode in MODES else DEFAULT_MODE


def set_mode(scope: str, mode: str) -> str:
    if mode not in MODES:
        raise ValueError(f"unknown execution mode {mode!r}; one of {MODES}")
    data = _read()
    if mode == DEFAULT_MODE:
        # Storing the default is noise: absence already means reactive. Drop the
        # key so the file stays a list of scopes the operator deliberately armed.
        data.pop(scope, None)
    else:
        data[scope] = mode
    _write(data)
    return mode


def is_armed(scope: str) -> bool:
    """True when this scope's ready work may be auto-executed at all."""
    return get_mode(scope) != "reactive"


def is_armed_root(root: str | Path) -> bool:
    """The same question asked by a caller holding a task-store ROOT rather than
    a scope key (next_ready and the chat filing path both do). The root is
    mapped to its scope in the CURRENT namespace, which is what makes the answer
    the same inside a confined turn and outside it."""
    scope = _scope_of(root)
    return bool(scope) and is_armed(scope)


def stage_ceiling(scope: str) -> str | None:
    """How far auto-execution may advance: in_review (supervised) or done
    (autonomous). None when reactive. The driver reads this to know where to stop."""
    return _CEILING[get_mode(scope)]


def armed_scopes() -> list[tuple[str, str]]:
    """Every scope the operator has deliberately armed, as (scope, mode) pairs.

    The store only ever holds non-default entries (set_mode drops a key set back
    to reactive), so this is exactly the armed set with no filtering needed.
    """
    return [(k, v) for k, v in sorted(_read().items())
            if v in MODES and v != DEFAULT_MODE]


def armed_roots() -> list[tuple[str, str]]:
    """The armed set as (root, mode), each root resolved in THIS namespace.

    A scope that does not resolve here is skipped rather than guessed at: that
    is how the work driver avoids starting a runner against a directory the
    backend cannot see.
    """
    from services import task_scope

    out: list[tuple[str, str]] = []
    for scope, mode in armed_scopes():
        try:
            root = task_scope.root_for_context(scope)
        except Exception:
            root = None
        if root is not None:
            out.append((str(root), mode))
    return out
