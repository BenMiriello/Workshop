"""This app's declaration: what it is, what it mounts, what it runs.

Moved out of `services/app_registry.py` on 2026-09-01, where one hardcoded
factory per app made the platform's catalog module know every app by name. The
registry now discovers declarations and names none. Behaviour is unchanged: the
spec below is the same object, moved verbatim.
"""
from __future__ import annotations

from fastapi import Depends

from deps_user import require_user
from services.app_spec import AppSpec, RouterMount


# Route modules this app needs; the catalog drops the app when they are not
# shipped. See `app_registry.APP_REQUIRES_ATTR`.
REQUIRES = ("notes",)


def app_spec() -> AppSpec:
    from routes import notes
    from services import notes_store_adapter, notes_vault, permissions
    spec = AppSpec(
        id="notes-app",
        # `notes:vault`, not `notes-app:vault`. The URI predates this
        # declaration; see `store_owner`.
        store_owner="notes",
        card={
            "title": "Notes",
            "tagline": "Markdown vault — wikilinks, backlinks, search",
            "icon": "NotebookPen",
            # No "route" here. It is set from `spec.id` below, so this app's
            # name is written ONCE in this function.
        },
        identity={
            "sensitivity": "private",
            "api_prefixes": ["/notes-app"],
            "provides": list(notes.PROVIDES),
            "needs": [],
        },
        mounts=(
            RouterMount(notes.router, deps=(Depends(require_user),)),
        ),
        # THE VAULT IS THIS APP'S STORE, declared here rather than in
        # `stores.register_builtin_stores` (2026-08-27,
        # memory-page-and-file-viewer-2026-08-27.md D1 and section 1.2).
        #
        # It sat in the platform's own block, four lines under a comment reading
        # "Everything app-specific is declared on that app's AppSpec and
        # registered below, so a store exists exactly while the app that owns it
        # is installed" -- so the file contradicted itself, and an installation
        # without this app still declared a store over a folder in the user's
        # home directory. The path still comes from `permissions.NOTES_VAULT_ROOT`
        # and nothing about the folder changes; what changes is that the store
        # exists only while the app does.
        stores=(("vault", lambda: permissions.NOTES_VAULT_ROOT, "Note", False),),
        # Searchable, but only when asked for by name: `vault` is deliberately
        # absent from index_collectors_default.
        index_collectors={"vault": notes_vault.collect_index_chunks},
        store_adapters=(notes_store_adapter.VaultNoteAdapter(),),
    )
    # THE ROUTE IS THE ID, DERIVED, so this app's name is written once above.
    #
    # The card used to hand-write a route that dropped a suffix from the id.
    # The frontend serves each app's page under its id (registry.tsx builds the
    # path that way) and `app_surface.surface_for` takes this route as the app's
    # HOME, so the one link the app handed out went to a route nobody serves:
    # the shell found no app with that slug and bounced the reader to Home with
    # an error. Reported 2026-09-01.
    #
    # Derived rather than spelled, and derived HERE rather than from a module
    # constant, because a constant holding the name would be a SECOND literal
    # naming an app. check_no_app_names counts those and caught exactly that on
    # the first attempt at this fix.
    spec.card["route"] = f"/apps/{spec.id}"
    return spec
