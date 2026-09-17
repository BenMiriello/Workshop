"""Reads and writes for the `app` table (app-is-the-entity-2026-08-20.md,
stages A and B).

Same shape as `db/chat_repo.py`: every function takes a `Session` so a caller
can compose several operations in one transaction, and rows come back as
plain dicts straight off raw SQL -- the row shapes here are exactly what the
REST routes in `routes/apps.py` hand back, and an ORM identity map buys
nothing over that.

WHAT THIS MODULE DOES NOT DO. It only ever reads/writes the `app` table and
the `app_id` column on `chat_thread` (the two real Postgres collections from
the design's sec 4.2). Tickets, runs, deploys, notes and roster agents are
resolved from the file stores by `services/apps.py`, which calls this module
for the `app` row itself and then goes to `services/app_links.py` for the
rest. See that module's docstring for why.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from db.ids import uuid7
# Both are deliberately database-free leaves -- `app_links` says so in its own
# docstring ("no sqlalchemy import") and `scope_tree` imports nothing below
# `wsplatform.paths` -- so importing them HERE, at module scope, is a real edge
# down the graph rather than a cycle waiting to be deferred. The rule they
# jointly express (which app owns which scope) has to be reachable from the
# write path, and a function-local import would only hide where it lives.
from services import app_links, scope_tree

__all__ = [
    "upsert_app", "get_app", "get_app_by_slug", "get_app_by_project_slug",
    "list_apps", "update_app",
    "delete_app", "list_chats_for_app", "set_thread_app_id",
    "count_chats_for_app", "scopes_owned_by", "scope_owner_map",
    "app_id_for_context",
]


def _row(r: Any) -> dict | None:
    return None if r is None else dict(r._mapping)


def upsert_app(s: Session, org_id: UUID | str, *, slug: str, name: str,
               kind: str = "app", icon: str | None = None,
               project_slug: str | None = None,
               parent_id: UUID | str | None = None,
               dev_only: bool = False) -> dict:
    """Insert-or-update by `(org_id, slug)` among LIVE rows.

    Slug is the natural key here on purpose ("a slug is a label, never an
    identity" still means it is A label, and the backfill's whole job is to
    find the ONE row a given install/project pair should be). An UPDATE, not
    an insert-once-and-skip like `db/backfill.py`'s tickets: unlike a
    historical ticket, an app's name/icon/link legitimately changes between
    backfill runs (a rename, an install, a newly-created project), and the
    row should track the registry rather than freeze at its first read.
    """
    existing = get_app_by_slug(s, org_id, slug)
    if existing is not None:
        s.execute(text(
            "UPDATE app SET name = :name, kind = :kind, icon = :icon, "
            "project_slug = :project_slug, parent_id = :parent_id, "
            "dev_only = :dev_only "
            "WHERE id = :id"),
            {"name": name, "kind": kind, "icon": icon,
             "project_slug": project_slug,
             "parent_id": str(parent_id) if parent_id else None,
             "dev_only": dev_only,
             "id": existing["id"]})
        return get_app(s, org_id, existing["id"])
    app_id = uuid7()
    s.execute(text(
        "INSERT INTO app (id, org_id, slug, name, kind, icon, project_slug, "
        "parent_id, dev_only) VALUES (:id, :org, :slug, :name, :kind, :icon, "
        ":project_slug, :parent_id, :dev_only)"),
        {"id": app_id, "org": str(org_id), "slug": slug, "name": name,
         "kind": kind, "icon": icon, "project_slug": project_slug,
         "parent_id": str(parent_id) if parent_id else None,
         "dev_only": dev_only})
    return get_app(s, org_id, app_id)


def get_app(s: Session, org_id: UUID | str, app_id: UUID | str) -> dict | None:
    return _row(s.execute(text(
        "SELECT * FROM app WHERE id = :id AND org_id = :org "
        "AND deleted_at IS NULL"),
        {"id": str(app_id), "org": str(org_id)}).one_or_none())


def get_app_by_slug(s: Session, org_id: UUID | str, slug: str) -> dict | None:
    return _row(s.execute(text(
        "SELECT * FROM app WHERE org_id = :org AND slug = :slug "
        "AND deleted_at IS NULL"),
        {"org": str(org_id), "slug": slug}).one_or_none())


def get_app_by_project_slug(s: Session, org_id: UUID | str,
                            project_slug: str) -> dict | None:
    """The app whose working tree is this project, if any.

    The reverse of the `project_slug` bridge column, riding the index that
    already exists for it (`ix_app_project_slug`). A caller that starts from a
    working tree -- anything branch- or change-shaped -- has a project slug and
    needs the app row; a caller that starts from the UI has the app id. Both
    directions belong here rather than in a hand-written query per caller.

    Returns None when no app is linked to that tree, which is the normal state
    for a project that is not an app (the two registries are still unlinked;
    see app-is-the-entity-2026-08-20.md sec 9).
    """
    return _row(s.execute(text(
        "SELECT * FROM app WHERE org_id = :org AND project_slug = :p "
        "AND deleted_at IS NULL ORDER BY created_at LIMIT 1"),
        {"org": str(org_id), "p": project_slug}).one_or_none())


def list_apps(s: Session, org_id: UUID | str, *, kind: str | None = None,
             archived: bool | None = None,
             dev_only: bool | None = None) -> list[dict]:
    """Live apps for this org. `archived=None` (the default) returns both
    archived and unarchived; `True`/`False` filters on `archived_at`.
    `dev_only=False` excludes dev-only apps (the production default);
    `dev_only=None` returns all rows.
    Soft-deleted rows are never returned, regardless of `archived`."""
    where = ["org_id = :org", "deleted_at IS NULL"]
    params: dict[str, Any] = {"org": str(org_id)}
    if kind:
        where.append("kind = :kind")
        params["kind"] = kind
    if archived is not None:
        where.append("archived_at IS NOT NULL" if archived else "archived_at IS NULL")
    if dev_only is not None:
        where.append("dev_only = :dev_only")
        params["dev_only"] = dev_only
    sql = f"SELECT * FROM app WHERE {' AND '.join(where)} ORDER BY name"
    return [dict(r._mapping) for r in s.execute(text(sql), params).all()]


def update_app(s: Session, org_id: UUID | str, app_id: UUID | str,
               **fields: Any) -> dict | None:
    allowed = ("name", "icon", "slug", "kind", "parent_id", "project_slug",
               "archived_at", "dev_only")
    sets = {k: v for k, v in fields.items() if k in allowed}
    if not sets:
        return get_app(s, org_id, app_id)
    assignments = ", ".join(f"{k} = :{k}" for k in sets)
    params = dict(sets)
    params["id"] = str(app_id)
    params["org"] = str(org_id)
    s.execute(text(
        f"UPDATE app SET {assignments} WHERE id = :id AND org_id = :org "
        f"AND deleted_at IS NULL"), params)
    return get_app(s, org_id, app_id)


def delete_app(s: Session, org_id: UUID | str, app_id: UUID | str) -> bool:
    """Soft delete. `chat_thread.app_id` / `ticket.app_id` are left as-is by
    the FK's `SET NULL (app_id)` only on a HARD delete; a soft delete here
    does not cascade to them, so a deleted app's chats keep their app_id and
    would reappear correctly if the app is restored. Nothing restores it yet
    (no route for that -- deliberately out of scope for stage B)."""
    r = s.execute(text(
        "UPDATE app SET deleted_at = now() WHERE id = :id AND org_id = :org "
        "AND deleted_at IS NULL"),
        {"id": str(app_id), "org": str(org_id)})
    return r.rowcount > 0


def list_chats_for_app(s: Session, org_id: UUID | str, app_id: UUID | str, *,
                       archived: bool = False, limit: int = 200) -> list[dict]:
    rows = s.execute(text(
        "SELECT id, external_id, context_key, scope, title, seed_title, "
        "mode, model, agent_slug, created_at, updated_at, archived_at "
        "FROM chat_thread WHERE org_id = :org AND app_id = :app "
        "AND deleted_at IS NULL "
        "AND (archived_at IS NOT NULL) = :archived "
        "ORDER BY updated_at DESC LIMIT :limit"),
        {"org": str(org_id), "app": str(app_id), "archived": bool(archived),
         "limit": limit}).all()
    return [dict(r._mapping) for r in rows]


def count_chats_for_app(s: Session, org_id: UUID | str, app_id: UUID | str) -> int:
    return s.execute(text(
        "SELECT count(*) FROM chat_thread WHERE org_id = :org AND app_id = :app "
        "AND deleted_at IS NULL"),
        {"org": str(org_id), "app": str(app_id)}).scalar_one()


def set_thread_app_id(s: Session, org_id: UUID | str, thread_id: UUID | str,
                      app_id: UUID | str | None) -> None:
    s.execute(text(
        "UPDATE chat_thread SET app_id = :app WHERE id = :id AND org_id = :org"),
        {"app": str(app_id) if app_id else None, "id": str(thread_id),
         "org": str(org_id)})


# --- which app owns a chat -----------------------------------------------
#
# THE READ SIDE AND THE WRITE SIDE USED TO DISAGREE. `list_chats_for_app`
# above selects on the `app_id` COLUMN, and for a long time the only thing
# that ever wrote that column was `db/app_backfill.py`, a CLI nobody runs on
# a schedule. So a chat created in a project or app scope was invisible on
# that app's Chats tab until someone hand-ran the backfill, and
# `services/apps.chats_for_app` said app membership was "the DATABASE's
# answer" when it was really "the answer as of the last manual repair".
#
# The rule below is where the backfill's rule now lives, lifted out of it so
# the live write path calls the SAME one. The backfill still exists and still
# calls this -- it is the repair tool for rows written before the live path
# had it, or by a mirror write that failed -- but it is no longer the only
# writer. It is not a verbatim move: `scopes_owned_by` documents the two
# things the old spelling got wrong and why one app owns several scopes.

def scopes_owned_by(slug: str) -> list[str]:
    """Every scope key an app owns, resolved from the registries.

    AN APP OWNS MORE THAN ONE SCOPE, which is the whole content of "project is
    the build view of an app, not a second object". The same conversation is
    the same app's whether it was opened from the app or from its project, so
    both addresses belong to one row:

      `app:<slug>`          always -- what a bare installed app id resolves to
      `project:<link>`      when the app has a linked working tree
      `<link>`              additionally, when the link names a ROOT scope in
                            its own right (`scope_for_context(link) == link`),
                            which is how a self-hosting installation's own
                            source owns the chats filed against its root queue

    Nothing here claims the workshop root by accident, and that is the point of
    the equality test. `scope_for_context` is TOTAL -- its last line answers
    `workshop` for anything it does not recognise -- so resolving a bare
    project slug through it and keying on the result, which is what this
    replaced, made every project-only app claim `workshop` and the last one in
    roster order win. Measured on this installation's own store: 31 unbound
    `general` chats plus everything else falling through would have been
    attributed to a throwaway project, while the 13 threads actually keyed
    `project:<slug>` -- the ones the defect was reported about -- matched no
    app at all, because a bare link never resolves to a `project:` scope.
    """
    scopes = [f"app:{slug}"]
    link = app_links.linked_project_slug(slug)
    if link:
        scopes.append(f"project:{link}")
        resolved = scope_tree.scope_for_context(link)
        if resolved == link:
            scopes.append(resolved)
    return scopes


def scope_owner_map(app_ids: dict[str, str]) -> dict[str, str]:
    """SCOPE -> app_id, over a slug -> app_id roster.

    A thread's SCOPE decides which app owns it, not its raw `context_key`, and
    the scope comes from the same `scope_tree.scope_for_context` every other
    scope question in the system uses -- so a bare app id, an `app:<slug>`
    context and a `project:<slug>` context whose project is linked to an app
    all land on the same app_id. That last case is the app<->project merge
    made concrete: a chat filed against the PROJECT view of an app is still
    that app's chat.

    Iteration order is the caller's roster order, and the FIRST claimant of a
    scope keeps it, so a lookup here cannot depend on dict ordering luck.
    """
    out: dict[str, str] = {}
    for slug, app_id in app_ids.items():
        for scope in scopes_owned_by(slug):
            out.setdefault(scope, str(app_id))
    return out


def reconcile_thread_app_ids(s: Session, org_id: UUID | str,
                             roster: dict[str, str]) -> dict:
    """Re-derive `chat_thread.app_id` for every live thread and write the ones
    that disagree. Returns `{"checked", "linked", "relinked", "unlinked"}`.

    WHY A SWEEP AND NOT A HOTTER WRITE. `app_id` is resolved when a thread ROW
    is first inserted (`_thread_fields`) and on a meta patch that touches a
    list-relevant field. An ordinary turn is neither: `chat_branches._sync_record`
    builds the field set only in its `if t is None` branch, and the per-turn meta
    patches (`active_turn_id`, `live_message`, `context_tokens`) are deliberately
    excluded from the sync gate because they fire hundreds of times a turn.

    So a thread whose row predates the write path, or whose app row was created
    after it, or which was simply never renamed again, keeps whatever `app_id` it
    had forever. Measured 2026-09-17 on the live store: of 240 live threads, 52
    disagreed with the current rule -- 25 with no app_id at all (one of them a
    chat the operator had just created in a new project and gone looking for),
    and 27 pointing at an app that does not own their scope, left behind by an
    older backfill that attributed unbound `general` chats to a throwaway
    project. Both directions had sat there uncorrected because nothing re-asks
    the question for a row that already exists.

    IT CLEARS AS WELL AS SETS, which `db/app_backfill._backfill_chats` does not:
    that one writes only when an app owns the scope, so it can fix the 25 and is
    structurally unable to fix the 27. A reconcile that cannot say "no owner" is
    a reconcile that can only ever add.

    Cheap in the steady state: one SELECT of (id, context_key, app_id) and no
    write at all when nothing has drifted. The caller decides how often; see
    `services.apps.reconcile_from_registries`, which is already the place a read
    refreshes the roster this takes as its input.
    """
    from services import scope_tree

    owner = scope_owner_map(roster)
    rows = s.execute(text(
        "SELECT id, context_key, app_id FROM chat_thread "
        "WHERE org_id = :org AND deleted_at IS NULL"), {"org": str(org_id)}).all()

    linked = relinked = unlinked = 0
    for row in rows:
        thread_id, context_key, current = row[0], row[1], row[2]
        want = owner.get(scope_tree.scope_for_context(context_key))
        cur = str(current) if current is not None else None
        if want == cur:
            continue
        s.execute(text("UPDATE chat_thread SET app_id = :app WHERE id = :id"),
                  {"app": want, "id": thread_id})
        if want is None:
            unlinked += 1
        elif cur is None:
            linked += 1
        else:
            relinked += 1
    return {"checked": len(rows), "linked": linked,
            "relinked": relinked, "unlinked": unlinked}


def app_id_for_context(s: Session, org_id: UUID | str,
                       context_key: str | None) -> str | None:
    """The app a chat with this `context_key` belongs to, or None.

    None is an ORDINARY answer, not an error: an agent chat, an unbound
    `general` chat and a scope no app owns all legitimately leave
    `chat_thread.app_id` NULL.

    The roster is rebuilt from the registries on every call (in
    `all_app_slugs()` order, which is the order the backfill upserts in, so a
    root scope claimed by two rows resolves the same way on both paths) and
    intersected with the rows that actually exist. ARCHIVED apps are left out
    deliberately: an app the registries have stopped naming is archived rather
    than deleted so its existing chats keep their home, but it must not go on
    claiming NEW ones -- and, since it is no longer in the registries,
    `linked_project_slug` would resolve it to a scope another app owns.

    `app_links.RegistryUnreadable` PROPAGATES, deliberately. A projects
    registry that is present and will not parse means the roster is unknown,
    and answering None would file every chat as belonging to no app -- the
    same "indistinguishable from an instance that genuinely has none" that
    `app_links` raises rather than returns for. The mirror write above this is
    best-effort and logged, the file log is still the durable record, and
    `db/chat_backfill.py` re-runs, so the cost of raising is a repair; the
    cost of swallowing would be silently unfiling chats.
    """
    by_slug = {r["slug"]: str(r["id"])
               for r in list_apps(s, org_id, archived=False)}
    roster = {slug: by_slug[slug] for slug in app_links.all_app_slugs()
              if slug in by_slug}
    return scope_owner_map(roster).get(scope_tree.scope_for_context(context_key))
