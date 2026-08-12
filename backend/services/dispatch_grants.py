"""In-scope grants minted at dispatch (final-plan P0).

An autonomous sweep is not the operator. It is an `agent` identity
(`permissions.Identity(kind="agent")`) with zero ambient authority, so it has to
be HANDED its reach when the dispatcher launches it. This module is that hand-off,
and it is deliberately GENEROUS: a job dispatched on `project:acme` starts holding
read+write over the whole of `project:acme` — exactly what it can reach today.
Nothing about a sweep's normal work gets harder. The gate is at the job's
PERIMETER (what it can reach OUTSIDE its own scope), not inside its own tree.

The operator is never involved and never gated: `authorize(operator, ...)` stays
unconditionally permissive. This mints for background processes only.

How the narrowing works (and why `attenuate` is load-bearing rather than dead):
the mint starts from the workshop-root SUPERSET — a subtree grant over every
Store in the workshop — and `attenuate()`s down to the one scope, then revokes
the superset. Authority only ever moves in the narrowing direction, and the
superset never survives the call, so there is no window in which the token is
wider than its scope.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from services import permissions, stores
from services import paths

WORKSHOP_ROOT = paths.workshop_root()

# The superset every dispatch grant is attenuated FROM: a subtree grant over the
# whole store namespace. `workshop://store/scope/project-acme` is under it, so the
# scope grant is a strict narrowing of it.
WORKSHOP_ROOT_URI = "workshop://store"

# The grant's `from` side: the platform itself is what delegates reach.
GRANTOR = "workshop"

DEFAULT_EXPIRY_S = 3600


def scope_store_name(scope_key: str) -> str:
    """A scope key (`project:acme`, `agent:memory-keeper`, `workshop`) as a store
    name. `:` is not legal in a store uri segment; `-` is."""
    return (scope_key or "workshop").strip().replace(":", "-").lower()


def scope_uri(scope_key: str, scope_path: str | Path | None = None) -> str:
    """The Store URI for a scope's root, declaring the Store if a path is known.

    REGISTRATION, not relocation (the stores.py contract): the scope's directory
    is left exactly where it is. A scope with no resolvable path still gets a
    stable URI so a grant can name it."""
    name = scope_store_name(scope_key)
    if scope_path is not None:
        return stores.declare("scope", name, Path(scope_path), type="Scope").uri
    return stores.make_uri("scope", name)


def _expiry_iso(expiry_s: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=max(1, int(expiry_s)))).isoformat()


def mint_in_scope_grant(
    agent_slug: str,
    scope_key: str,
    verbs: Iterable[str] = ("read", "write"),
    expiry_s: int = DEFAULT_EXPIRY_S,
    scope_path: str | Path | None = None,
) -> dict[str, Any]:
    """Mint the grant a dispatched job starts holding: read+write over its OWN
    scope, expiring with the sweep budget.

    Returns the grant record; `grant["id"]` is the capability token the sweep
    carries (`project_ralph.sh --grant-token`), and the sweep revokes it when it
    ends, so a token never outlives its job even if the expiry is generous.
    """
    slug = (agent_slug or "").strip()
    if not slug:
        raise ValueError("mint_in_scope_grant needs an agent slug")
    want = [v for v in verbs]
    uri = scope_uri(scope_key, scope_path)

    # 1. The superset: every store in the workshop. Exists only for the length of
    #    this function.
    superset = permissions.mint_grant(
        from_app=GRANTOR,
        to=f"agent:{slug}",
        store_uri=WORKSHOP_ROOT_URI,
        verbs=want,
        subtree=True,
    )
    try:
        # 2. Narrow it to the one scope + an expiry sized to the sweep budget.
        grant = permissions.attenuate(
            superset,
            store_uri=uri,
            verbs=want,
            caveats={"expiry": _expiry_iso(expiry_s), "scope": scope_key},
        )
    finally:
        # 3. The superset must never survive the mint.
        permissions.revoke(superset["id"])
    return grant


def revoke_token(token: str | None) -> bool:
    """Revoke a dispatch grant at job end. Tolerant: an empty/unknown token is a
    no-op returning False, so a sweep's teardown never fails on it."""
    if not token:
        return False
    return permissions.revoke(token)


def _cli(argv: list[str]) -> int:
    """Shell entrypoint, so the dispatcher and project_ralph.sh use the SAME mint
    and revoke the backend does.

        python3 -m services.dispatch_grants mint <agent-slug> <scope-key> [path] [expiry_s]
            -> prints the grant token on stdout
        python3 -m services.dispatch_grants revoke <token>
            -> prints "revoked" / "unknown"
    """
    if len(argv) >= 3 and argv[1] == "mint":
        slug, scope_key = argv[2], (argv[3] if len(argv) > 3 else "workshop")
        path = argv[4] if len(argv) > 4 and argv[4] else None
        expiry = int(argv[5]) if len(argv) > 5 and argv[5] else DEFAULT_EXPIRY_S
        print(mint_in_scope_grant(slug, scope_key, scope_path=path, expiry_s=expiry)["id"])
        return 0
    if len(argv) >= 3 and argv[1] == "revoke":
        print("revoked" if revoke_token(argv[2]) else "unknown")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":  # pragma: no cover - shell entrypoint
    import sys
    raise SystemExit(_cli(sys.argv))
