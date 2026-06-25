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

_RUNTIME = Path(os.environ.get("WORKSHOP_RUNTIME", "/home/clawd-a/workshop/.runtime"))

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
            "no data owner is pinned for this call — a request must authenticate "
            "(current_user) and an automation pass must set_current_owner() first"
        )
    return uid


def owner_or_none() -> int | None:
    """The pinned owner id without raising — for the birds viewer bridge and any
    caller that legitimately tolerates 'no owner' (e.g. a public, non-personal
    read). Personal stores must use current_owner() instead."""
    return _owner_ctx.get()


def user_runtime_dir(owner_id: int | None = None) -> Path:
    """`.runtime/users/<owner_id>/` for the given (or current) owner, created."""
    uid = owner_id if owner_id is not None else current_owner()
    d = _RUNTIME / "users" / str(int(uid))
    d.mkdir(parents=True, exist_ok=True)
    return d
