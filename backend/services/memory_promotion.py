"""Promotion on evidence: a lesson learned three times is not a local lesson.

The read union (knowledge.read_layers) lets an agent read shared and specialized
memory. This is the other half: how something LEARNED narrowly becomes shared,
so instances of a role get better together instead of each rediscovering the
same thing.

THE RULE, from the interaction model (section 7.3):

    A note that has independently appeared in THREE OR MORE distinct narrower
    scopes is proposed for promotion to the layer above.

Three, not two, because two scopes agreeing is a coincidence often enough to
matter, and this proposal costs the operator attention every time it is wrong.

WHY WRITE NARROW AND PROMOTE, rather than letting agents write wide. A write-wide
default means the first agent to form an opinion imposes it on every future
reader, and nothing distinguishes "I tried this once" from "this is how it is
done". Earning the promotion is what makes the shared layer worth reading.

PROMOTION IS A PROPOSAL, NEVER AN AUTOMATIC WRITE. It surfaces as a `review`
item in the inbox, because these are exactly the decisions that shape every
future agent, and an automatic writer that is wrong poisons the layer everything
else reads. It reuses the notify/question/review taxonomy rather than adding a
surface.

Where a cluster is promoted TO depends on what the agreeing scopes have in
common:

  - agreeing scopes are agents sharing ONE role  -> role:<role>
  - anything else (mixed roles, projects, apps)  -> workshop

That is the same judgement the read union encodes, applied in reverse.
"""
from __future__ import annotations

import logging

from services import inbox, knowledge

log = logging.getLogger(__name__)

# How many distinct narrower scopes must independently hold a note before it is
# proposed. See the module docstring for why this is 3.
PROMOTE_AT = 3

# Notes that are per-scope by construction. Promoting these is always wrong:
# they are containers whose whole point is that each scope has its own.
_NEVER_PROMOTE = {
    "chat-distilled",       # per-scope distillation of that scope's chats
    "consolidated-memory",  # the keeper's per-scope consolidation
    "memory",               # index files
}


def _is_narrow(scope_key: str) -> bool:
    """Scopes that can be promoted FROM. `workshop` is the top of the read union
    and `role:*` is only reached by promotion, so neither is a source."""
    return scope_key.startswith(("agent:", "project:", "app:", "build:"))


def _role_of(scope_key: str, roles: dict[str, str | None]) -> str | None:
    return roles.get(scope_key)


def find_clusters(scopes: list[str], roles: dict[str, str | None] | None = None) -> list[dict]:
    """Notes held by PROMOTE_AT or more distinct narrow scopes.

    Matching is by note id. Ids here are derived from titles, so the same lesson
    written independently in two places lands on the same id — which is what
    makes this detectable at all without semantic comparison.
    """
    roles = roles or {}
    by_note: dict[str, list[str]] = {}
    for sk in scopes:
        if not _is_narrow(sk):
            continue
        try:
            notes = knowledge.list_notes(sk)
        except Exception as e:
            log.debug("promotion: cannot list %s: %s", sk, e)
            continue
        for n in notes:
            nid = str(n.get("id") or "")
            if not nid or nid in _NEVER_PROMOTE:
                continue
            by_note.setdefault(nid, []).append(sk)

    out: list[dict] = []
    for nid, holders in sorted(by_note.items()):
        distinct = sorted(set(holders))
        if len(distinct) < PROMOTE_AT:
            continue
        # Target: a shared role if every holder agrees on one, else workshop.
        holder_roles = {_role_of(s, roles) for s in distinct}
        if len(holder_roles) == 1:
            only = next(iter(holder_roles))
            target = f"role:{only}" if only else "workshop"
        else:
            target = "workshop"
        if target in distinct:
            continue
        out.append({"note_id": nid, "scopes": distinct, "target": target})
    return out


# In-flight promotion writes, held so nothing is collected mid-flight.
_PROMOTION_TASKS: set = set()


def _title_for(note_id: str, scopes: list[str]) -> str:
    for sk in scopes:
        try:
            for n in knowledge.list_notes(sk):
                if n.get("id") == note_id and n.get("title"):
                    return str(n["title"])
        except Exception:
            continue
    return note_id.replace("-", " ")


def propose(scopes: list[str], roles: dict[str, str | None] | None = None) -> dict:
    """Raise a `review` item for every cluster that has earned promotion.

    Idempotent on `promote:<target>:<note_id>` while the item is open, so a
    recurring sweep re-proposing the same cluster does not bury the inbox — the
    same reason raise_item dedupes at all.

    Never raises into the caller.
    """
    proposed = 0
    try:
        clusters = find_clusters(scopes, roles)
    except Exception as e:
        log.warning("promotion: cluster scan failed: %s", e)
        return {"proposed": 0}
    # raise_item returns the EXISTING item when a dedupe key matches, so it
    # cannot tell us whether anything was created. Snapshot first.
    try:
        known = {it.get("dedupe_key") for it in inbox.list_items(state="open")}
    except Exception:
        known = set()

    for c in clusters:
        nid, holders, target = c["note_id"], c["scopes"], c["target"]
        title = _title_for(nid, holders)
        where = ", ".join(holders[:6]) + ("…" if len(holders) > 6 else "")
        try:
            inbox.raise_item(
                kind="review",
                agent="memory-keeper",
                # The item belongs where the decision applies, so it surfaces in
                # a chat broad enough to be making it.
                scope="workshop",
                title=f"Promote “{title}” to {target}?",
                body=(
                    f"“{title}” was learned independently in {len(holders)} scopes "
                    f"({where}). That makes it a general lesson rather than a local "
                    f"one, so it is proposed for {target}, where every agent that "
                    f"reads that layer will get it. Approving copies the note up; "
                    f"the originals stay where they are."
                ),
                options=["Approve", "Reject"],
                dedupe_key=f"promote:{target}:{nid}",
            )
            key = f"promote:{target}:{nid}"
            if key not in known:
                proposed += 1
                known.add(key)
        except Exception as e:
            log.warning("promotion: cannot propose %s: %s", nid, e)
    return {"proposed": proposed}


def apply(note_id: str, target_scope: str, source_scopes: list[str]) -> bool:
    """Copy an approved note up to `target_scope`. Sources are left in place.

    Deliberately a COPY, not a move. The narrow note is that scope's own record
    of what it learned and why; deleting it would rewrite local history to make
    a global claim, and would strand anything that referenced it.
    """
    body = None
    title = _title_for(note_id, source_scopes)
    for sk in source_scopes:
        body = knowledge.get_note(sk, note_id)
        if body:
            break
    if not body:
        return False
    import asyncio
    coro = knowledge.put_note(
        target_scope, note_id, title, body,
        hook=f"promoted from {len(source_scopes)} scopes",
    )
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(coro)
        return True
    # HELD: asyncio keeps only a weak reference to a task, so an unheld one can
    # be collected before it finishes and the note is silently never written.
    task = loop.create_task(coro)
    _PROMOTION_TASKS.add(task)
    task.add_done_callback(_PROMOTION_TASKS.discard)
    return True


def survey() -> tuple[list[str], dict[str, str | None]]:
    """Every narrow scope that has notes, plus each one's role.

    The role map is what decides whether a cluster promotes to `role:<role>` or
    all the way to `workshop`, so it has to come from the registry rather than
    be guessed from the scope name.
    """
    scopes: list[str] = []
    roles: dict[str, str | None] = {}
    root = knowledge._root()

    for base, kind in ((root / "projects", "project"), (root / "apps", "app")):
        if base.exists():
            for child in sorted(base.iterdir()):
                if (child / "knowledge").is_dir():
                    scopes.append(f"{kind}:{child.name}")

    agents_base = knowledge._runtime() / "agents"
    if agents_base.exists():
        for child in sorted(agents_base.iterdir()):
            if (child / "knowledge").is_dir():
                scopes.append(f"agent:{child.name}")

    try:
        from services import agents_registry
        for a in agents_registry.list_agents(include_retired=True):
            slug = a.get("slug")
            if slug:
                roles[f"agent:{slug}"] = (a.get("role") or None)
    except Exception as e:
        log.debug("promotion: registry unavailable: %s", e)
    return scopes, roles


def sweep() -> dict:
    """Survey + propose. The entry point a caller uses without knowing the shape
    of the scope graph."""
    scopes, roles = survey()
    return propose(scopes, roles)


def approve(dedupe_key: str) -> bool:
    """Act on an approved promotion review item.

    The SOURCES are re-derived rather than stored on the item: the note may have
    spread further, or been edited, between the proposal and the answer, and
    promoting what is true now beats promoting a snapshot.

    The TARGET is not re-derived. The operator approved a specific proposition
    ("promote this to role:reviewer"), and recomputing the destination could
    silently widen the blast radius to `workshop` — or, as it did before this
    was fixed, mismatch and make Approve a silent no-op. The system's job here
    is to do what was approved, or report that it could not.
    """
    if not dedupe_key.startswith("promote:"):
        return False
    _, _, rest = dedupe_key.partition("promote:")
    target, _, note_id = rest.rpartition(":")
    if not target or not note_id:
        return False
    scopes, roles = survey()
    for c in find_clusters(scopes, roles):
        if c["note_id"] == note_id:
            return apply(note_id, target, c["scopes"])
    # The evidence is gone (notes deleted or edited apart) since the proposal.
    # Refuse rather than promote on a premise that no longer holds; the caller
    # reports `promoted: false` so this is visible, not silent.
    log.info("promotion: evidence for %s no longer meets the threshold; not promoting", note_id)
    return False
