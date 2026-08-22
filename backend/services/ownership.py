"""Request-scoped data owner — the single source of "whose data is this".

Every personal store (location, health, budget, chat, push, finance cache, the
commute runtime) is partitioned per owner. Rather than thread an `owner_id`
argument through dozens of call sites (many buried in the commute internals),
the owner is pinned in a contextvar for the duration of a request or an
automation pass, and the stores resolve their per-owner directory from it.

This generalizes the birds `_viewer_ctx` pattern with one critical inversion:
**when the owner is unset, `current_owner()` RAISES.** It does NOT default to the
operator. A forgotten set-point is therefore a crash (fail-closed), never a
cross-owner data leak. The owner is set in exactly three kinds of place:

  1. the `current_user` auth dependency (every personal request), from the
     authenticated user's id;
  2. the top of each background loop in `main.py`, to the operator's id;
  3. tests.

`user_runtime_dir(owner_id)` is the on-disk home for an owner's files:
`.runtime/users/<owner_id>/`. Stores build their paths under it, so there is no
shared file across owners and thus no cross-owner read path to get wrong.
"""
from __future__ import annotations

import contextvars
import os
from pathlib import Path

# Per-deployment runtime root (per-user data lives under it). Portable default:
# this checkout's own `.runtime/` (repo root = two dirs above services/). A
# fresh clone therefore writes under its own tree, never another operator's
# home. Overridable via WORKSHOP_RUNTIME.
_RUNTIME = Path(os.environ.get(
    "WORKSHOP_RUNTIME",
    str(Path(__file__).resolve().parents[2] / ".runtime"),
))

_owner_ctx: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "workshop_data_owner", default=None
)


class OwnerUnset(RuntimeError):
    """Raised when a personal store is accessed with no owner pinned. This is a
    programming error (a missing set-point), and it fails closed by design."""


def set_current_owner(owner_id: int) -> None:
    """Pin the data owner for the current request / automation pass."""
    _owner_ctx.set(int(owner_id))


def clear_current_owner() -> None:
    _owner_ctx.set(None)


def current_owner() -> int:
    """The pinned owner id, or raise OwnerUnset. Personal stores call this."""
    uid = _owner_ctx.get()
    if uid is None:
        raise OwnerUnset(
            "no data owner is pinned for this call. A request must authenticate "
            "(current_user) and an automation pass must set_current_owner() first"
        )
    return uid


def pin_operator() -> int | None:
    """Pin the single operator as the data owner for a call with no request
    behind it, and return the id. Returns None (pinning nothing) when there is
    no operator account yet, so a fresh install fails at the store with
    OwnerUnset rather than silently reading someone else's data.

    THE ONE SET-POINT FOR AUTOMATION. A shell entrypoint, a background loop, a
    CLI and an MCP server all need this and all wrote their own: project_tasks
    had `_pin_owner`, semantic_index had `pin_owner`, the query server had its
    own, and scripts/project_ralph.sh's inline python had NONE, which is how a
    sweep launched outside the backend died on OwnerUnset while the same sweep
    launched by the backend worked. project_tasks' own docstring already said
    "every other automation set-point pins an owner for exactly this reason;
    this is the one that had not" -- it was true again, of a different caller,
    because the rule lived in a comment instead of in a function.

    The id is LOOKED UP, never hardcoded, so a fresh install with a different
    id works and nothing machine-specific enters the source.
    """
    from services import auth_store

    oid = auth_store.operator_id()
    if oid is not None:
        set_current_owner(oid)
    return oid


def owner_or_none() -> int | None:
    """The pinned owner id without raising — for the birds viewer bridge and any
    caller that legitimately tolerates 'no owner' (e.g. a public, non-personal
    read). Personal stores must use current_owner() instead."""
    return _owner_ctx.get()


def caller_is_operator() -> bool:
    """Is the current request-scoped owner the single operator account?

    NOT a personal-data authorization check — data access is owner-scoped
    (every personal store filters by `current_owner()`, never by "is this the
    operator"), and `auth_store.operator_id()`'s own docstring is explicit that
    no request path may branch on it to grant access to data someone else
    owns. This is narrower and different in kind: it exists for the handful of
    INSTANCE-WIDE stores that predate per-user ownership and have no owner_id
    in them at all — `shared/knowledge/`'s workshop layer, the cross-chat work
    digest, MCP server credentials (Plaid, Google, Discord) — all of it
    configured by, and about, whoever set up this Workshop instance. Until
    those get real per-user partitioning, "belongs to the operator" is the
    true, literal answer for all of them, and every caller that isn't the
    operator gets none of it rather than someone else's. Fails closed (False)
    on an unset owner or a bootstrap that hasn't run. See
    docs/security-audit-2026-08-11-scope-separation.md.
    """
    from services import auth_store
    try:
        owner_id = current_owner()
    except OwnerUnset:
        return False
    operator_id = auth_store.operator_id()
    return operator_id is not None and owner_id == operator_id


def user_runtime_dir(owner_id: int | None = None) -> Path:
    """`.runtime/users/<owner_id>/` for the given (or current) owner, created."""
    uid = owner_id if owner_id is not None else current_owner()
    d = _RUNTIME / "users" / str(int(uid))
    d.mkdir(parents=True, exist_ok=True)
    return d


def workspace_path(operator_default: Path, under: str) -> Path:
    """The current owner's copy of a WORKSPACE-level store.

    A workspace is everything a person accumulates by using their Workshop:
    projects, build agents, their agent roster, their knowledge, their queue,
    their job history. All of it predates per-user ownership and all of it lived
    at exactly one path — so "another user's Workshop" was the same projects
    directory, the same registry file, the same knowledge layer read into every
    prompt. Not a weak boundary; the same drawer.

    Two rules, and the second is why this is a helper rather than a convention:

    1. **The operator does not move.** `projects/`, `agents/`, `shared/knowledge/`
       are git-tracked, opened in editors, and referenced by scripts and cron.
       Repointing them would be a migration with a blast radius far larger than
       the leak it closes, so the operator keeps `operator_default` byte for
       byte and everyone else gets `.runtime/users/<id>/workspace/<under>/`.
    2. **No owner pinned means the operator's**, matching `ownership.py`'s own
       rule for background loops (they pin the operator's id) and keeping every
       CLI script and unit test working unchanged. Request paths always have an
       owner: `deps_user` pins it before any route body runs.

    This is deliberately NOT another `caller_is_operator()` gate. A gate answers
    "may you see the operator's store" with no, which secures the instance and
    leaves the second user with a Workshop missing half its features. This
    answers "which store is yours" — so the ambient knowledge injection, the
    note fences and the work digest can go back to running for everybody,
    against their own data. The gates it replaces are removed, not stacked.

    Callers must resolve through this at CALL time, never capture it in a
    module-level constant: those are evaluated once at import, which would pin
    whichever owner happened to be current then — in practice the operator,
    forever, for everyone.
    """
    if caller_is_operator() or owner_or_none() is None:
        return operator_default
    d = user_runtime_dir() / "workspace" / under
    d.parent.mkdir(parents=True, exist_ok=True)
    return d
