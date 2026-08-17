"""Who owns a scope, and may the caller reach it.

Projects, build agents and registry agents are addressed by a GLOBALLY UNIQUE
slug — `project:alpha` is one key, one directory, one task store, one knowledge
layer, one row in the role-uniqueness check, and in `secrets_store` the AAD a
project's secrets are encrypted under. That is deliberate (see
`paths.projects_root`): partitioning the directory per owner while leaving the
key global would let two owners mint the same key, which is the alias
multiplication ADR 0014 exists to end.

Uniqueness is not ownership, though. This module is the other half: each record
carries an `owner_id`, and this is the one place that reads it and answers
"may the current caller reach this scope".

WHY IT LIVES AT THE SCOPE LAYER. Every HTTP route that touches a project, an
agent, a worktree, a ticket or a knowledge note is mounted operator-only
(`main.py`'s `_OPERATOR`), so the REST surface was never the hole. Chat is
mounted `_USER`, takes a free-form `context_key` from the client, and resolves
it straight into a working directory and a task store — so the chat scope was
the one door into all of that with no ownership question asked at all. Answering
it here covers `get_provider`, `task_scope.root_for_context` and everything
downstream of them at once, rather than at N call sites that must each remember.

LEGACY RECORDS READ AS THE OPERATOR'S. Every project and agent that exists today
was created by the operator before this field did, so an absent `owner_id` means
theirs. That is a real assumption and it is safe in exactly one direction: it
can only ever grant the operator access to something already theirs, never grant
a second account access to anything. Newly created records are stamped, so the
assumption ages out rather than compounding.
"""
from __future__ import annotations

import json
import logging

from services import ownership
from wsplatform import paths

log = logging.getLogger("workshop.scope_owner")

# Scopes with no slug to be unique BY. `workshop` and `role:<r>` name the same
# thing for every account, so they cannot be told apart by key and are separated
# by directory instead (`knowledge._shared_knowledge_root`). Nobody is refused
# them; they simply resolve somewhere different per owner.
_UNQUALIFIED = ("workshop", "general", "new-project")


def _registry_owner(path, section: str, slug: str) -> int | None:
    try:
        if not path.exists():
            return None
        rec = (json.loads(path.read_text()).get(section) or {}).get(slug)
    except Exception as e:  # noqa: BLE001 - a broken registry is not an ACL
        log.warning("could not read %s for %s: %s", path, slug, e)
        return None
    if not isinstance(rec, dict):
        return None
    raw = rec.get("owner_id")
    return int(raw) if isinstance(raw, (int, str)) and str(raw).isdigit() else None


def owner_of(context_key: str | None) -> int | None:
    """The owner id recorded for a slug-qualified scope, or None if the scope
    has no owner concept (unqualified, unknown, or a record predating the
    field)."""
    ck = (context_key or "").strip()
    if not ck or ck in _UNQUALIFIED:
        return None
    kind, _, slug = ck.partition(":")
    if not slug:
        return None
    if kind == "project":
        return _registry_owner(paths.projects_registry(), "projects", slug)
    if kind == "build":
        from services import build_agents
        return _registry_owner(build_agents._registry(), "build_agents", slug)
    if kind == "agent":
        from services import agents_registry
        try:
            rec = agents_registry.get(slug) or {}
        except Exception:  # noqa: BLE001
            return None
        raw = rec.get("owner_id")
        return int(raw) if isinstance(raw, (int, str)) and str(raw).isdigit() else None
    return None


def may_reach(context_key: str | None) -> bool:
    """May the current caller bind to this scope?

    Deliberately permissive in two cases, and neither is a hole:

    - **No owner recorded.** A legacy record, or an unqualified scope like
      `workshop` that is separated by directory rather than by key. Refusing
      here would break every project on every existing install for its own
      operator. See the module docstring for why this can only ever over-grant
      to the operator.
    - **No owner pinned on the call.** A background sweep or a CLI script,
      which `ownership.py` already defines as the operator's context and which
      has no HTTP caller to check. Request paths always have one: `deps_user`
      pins it before any route body runs.

    What it refuses is the case that matters: an authenticated caller who is
    not the operator naming someone else's project, build agent or agent slug
    in a chat `context_key`.
    """
    owner = owner_of(context_key)
    if owner is None:
        return True
    caller = ownership.owner_or_none()
    if caller is None:
        return True
    return int(caller) == owner


def stamp() -> dict:
    """`{"owner_id": <caller>}` to merge into a newly created record, or `{}`
    when there is no caller to attribute it to (a CLI script or a migration).
    Empty rather than guessing: an unstamped record reads as the operator's,
    which is what a script running as the operator meant anyway."""
    caller = ownership.owner_or_none()
    return {"owner_id": int(caller)} if caller is not None else {}
