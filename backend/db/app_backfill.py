"""Populate the `app` table and link the existing collections to it
(app-is-the-entity-2026-08-20.md, stage A).

Prints a plan by default; writes only with `--apply`. Same discipline as
`db/backfill.py` and `db/chat_backfill.py`: idempotent and re-runnable.
Re-running after an install or a project rename picks up the change (unlike
the ticket backfill, an app row is legitimately allowed to change -- see
`db/app_repo.upsert_app`), and re-running with nothing changed writes nothing
new.

WHAT THIS BACKFILLS, AND WHY THAT LIST IS SHORT:

  `app`                    one row per slug in `services.app_links.all_app_slugs()`
  `chat_thread.app_id`     for every live thread whose scope resolves to an app

Tickets, runs, deploys, notes and roster agents are NOT touched here. Notes
and tickets are served to the REST surface straight off the file stores by
resolving an app's slug/project_slug at read time (see `services/apps.py`),
so there is no row to backfill. Runs and deploys are `project_slug`-keyed
files, resolved the same way. Roster agents are matched by `project_slug` in
`services/agents_registry.py`, also at read time. The ONLY two real Postgres
collections today are chat threads and tickets (see `App`'s docstring in
`db/models.py`), and the ticket half of this backfill finds zero rows to link
for a documented reason -- see `_backfill_tickets` below -- so it still runs,
for the day that stops being true.

    set -a; . ~/.config/workshop/db-admin.env; set +a
    .venv/bin/python -m db.app_backfill            # show what would happen
    .venv/bin/python -m db.app_backfill --apply
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db.engine import owner_session, tenant_session  # noqa: E402
from services import app_links, app_registry  # noqa: E402


def _org_id(s) -> str | None:
    row = s.execute(text(
        "SELECT id FROM organization WHERE slug = 'workshop' "
        "AND deleted_at IS NULL")).one_or_none()
    return None if row is None else str(row[0])


def _display_name(slug: str, project_title: str | None) -> str:
    """The card title if the catalog has one, else the project's own title,
    else a title-cased slug. Never a hardcoded per-app string -- every branch
    here reads a name FROM something, and the fallback is generic string
    manipulation, not a lookup table of app names."""
    spec = app_registry.BUILTIN_APPS.get(slug)
    if spec is not None and spec.card and spec.card.get("title"):
        return str(spec.card["title"])
    if project_title and project_title != slug:
        return project_title
    return slug.replace("-", " ").replace("_", " ").title()


def _icon(slug: str) -> str | None:
    spec = app_registry.BUILTIN_APPS.get(slug)
    if spec is not None and spec.card:
        return spec.card.get("icon")
    return None


def plan() -> dict:
    roster = app_links.all_app_slugs()
    linked = sum(1 for s in roster if app_links.linked_project_slug(s))
    return {
        "app_rows_to_upsert": len(roster),
        "of_which_linked_to_a_project": linked,
        "of_which_installed_only": len(roster) - linked,
    }


def _backfill_apps(s) -> dict[str, str]:
    """Upsert one `app` row per roster slug. Returns slug -> app_id."""
    from db import app_repo

    org_id = _org_id(s)
    if org_id is None:
        raise RuntimeError(
            "no 'workshop' organization; run db/backfill.py first")

    roster = app_links.load()
    ids: dict[str, str] = {}
    for slug in app_links.all_app_slugs():
        project_slug = app_links.linked_project_slug(slug)
        project_title = None
        if project_slug:
            project_title = (roster.projects.get(project_slug) or {}).get("title")
        row = app_repo.upsert_app(
            s, org_id, slug=slug, name=_display_name(slug, project_title),
            icon=_icon(slug), project_slug=project_slug)
        ids[slug] = row["id"]
    return ids


def _backfill_chats(s, app_ids: dict[str, str]) -> dict:
    """Link every live chat_thread whose scope resolves to a known app.

    A thread's SCOPE (not its raw context_key) decides this, via the same
    `scope_tree.scope_for_context` every other scope question in the system
    uses -- so a bare app id, an `app:<slug>` context and a `project:<slug>`
    context whose project is linked to an app all land on the same app_id.
    That last case is the app<->project merge made concrete: a chat that
    was filed against the PROJECT view of an app is still that app's chat.
    """
    from services import scope_tree

    org_id = _org_id(s)
    # project_slug -> app_id, built off every app that has one. The merge is
    # by slug equality today (app_links.linked_project_slug), but this does
    # not assume that stays true -- it is a real reverse lookup, not a mirror
    # of the forward one.
    project_to_app: dict[str, str] = {}
    for slug, aid in app_ids.items():
        ps = app_links.linked_project_slug(slug)
        if ps:
            project_to_app[ps] = aid

    rows = s.execute(text(
        "SELECT id, context_key FROM chat_thread WHERE org_id = :org "
        "AND deleted_at IS NULL"), {"org": org_id}).all()

    total = len(rows)
    linked = 0
    reasons: dict[str, int] = {}
    for row in rows:
        thread_id, context_key = row[0], row[1]
        scope = scope_tree.scope_for_context(context_key)
        app_id = None
        if scope.startswith("app:"):
            app_id = app_ids.get(scope.split(":", 1)[1])
        elif scope.startswith("project:"):
            app_id = project_to_app.get(scope.split(":", 1)[1])
        if app_id:
            s.execute(text(
                "UPDATE chat_thread SET app_id = :app WHERE id = :id"),
                {"app": app_id, "id": thread_id})
            linked += 1
        else:
            reason = ("root scope (workshop/development)" if scope in
                      ("workshop", "development") else
                      "agent scope" if scope.startswith("agent:") else
                      "app/project scope with no matching app row")
            reasons[reason] = reasons.get(reason, 0) + 1

    return {"total_chat_threads": total, "linked": linked,
            "left_null_reasons": reasons}


def _backfill_tickets(s, app_ids: dict[str, str]) -> dict:
    """Link every ticket whose PROJECT row (the Postgres `project` table,
    not the file registry) matches a linked app.

    Expected to link ZERO rows today. `ticket.project_id` only ever points at
    a Postgres `project` row, and Postgres `project` rows exist only for
    `services.task_scope.RELATIONAL_SCOPES` -- `development` and `workshop`
    -- neither of which is an app. Every real app's tickets live in the JSON
    ticket store, served to `GET /apps/{id}/tickets` from there (see
    `services/apps.py`), not from this column. This still runs, and is not
    skipped, because the day ticket mirroring widens to an app-linked project
    scope this backfill (or its next `--apply`) is what populates the column
    without a second migration -- see the module docstring.
    """
    org_id = _org_id(s)
    project_to_app = {}
    for slug, aid in app_ids.items():
        ps = app_links.linked_project_slug(slug)
        if ps:
            project_to_app[ps] = aid

    proj_rows = s.execute(text(
        "SELECT id, slug FROM project WHERE org_id = :org "
        "AND deleted_at IS NULL"), {"org": org_id}).all()
    total = s.execute(text(
        "SELECT count(*) FROM ticket WHERE org_id = :org "
        "AND deleted_at IS NULL"), {"org": org_id}).scalar_one()

    linked = 0
    for proj_id, proj_slug in proj_rows:
        app_id = project_to_app.get(proj_slug)
        if not app_id:
            continue
        r = s.execute(text(
            "UPDATE ticket SET app_id = :app WHERE org_id = :org "
            "AND project_id = :proj AND deleted_at IS NULL"),
            {"app": app_id, "org": org_id, "proj": proj_id})
        linked += r.rowcount

    return {"total_tickets": total, "linked": linked}


def apply() -> dict:
    with owner_session() as s:
        org_id = _org_id(s)
    if org_id is None:
        raise RuntimeError(
            "no 'workshop' organization; run db/backfill.py first")

    with tenant_session(org_id) as s:
        app_ids = _backfill_apps(s)
        chats = _backfill_chats(s, app_ids)
        tickets = _backfill_tickets(s, app_ids)

    return {
        "apps_upserted": len(app_ids),
        "app_slugs": sorted(app_ids),
        "chats": chats,
        "tickets": tickets,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="write to the database (default: print the plan only)")
    args = ap.parse_args()

    if not args.apply:
        print("PLAN (nothing written):")
        for k, v in plan().items():
            print(f"  {k:32} {v}")
        print("\nRe-run with --apply to write.")
        return 0

    result = apply()
    print(f"apps upserted: {result['apps_upserted']} ({', '.join(result['app_slugs'])})")
    c = result["chats"]
    print(f"chat_thread: {c['total_chat_threads']} live rows, "
          f"{c['linked']} linked to an app")
    for reason, n in sorted(c["left_null_reasons"].items()):
        print(f"  {n:4} left null: {reason}")
    t = result["tickets"]
    print(f"ticket: {t['total_tickets']} live rows, {t['linked']} linked to an app")
    return 0


if __name__ == "__main__":
    sys.exit(main())
