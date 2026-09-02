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
REQUIRES = ("location", "owntracks",)


def app_spec() -> AppSpec:
    from routes import location, owntracks
    from services import location_persona
    spec = AppSpec(
        id="location",
        chat_provider=location_persona.LocationContextProvider,
        chat_snapshot=location_persona.location_snapshot,
        card={
            "title": "Location",
            "tagline": "Live position + history from OwnTracks",
            "icon": "MapPin",
        },
        identity={
            "sensitivity": "private",
            "api_prefixes": ["/location"],
            "provides": ["location.read"],
            "needs": [],
        },
        mounts=(
            RouterMount(location.router, deps=(Depends(require_user),)),
            # OwnTracks ingest — HTTP Basic bound to an owner inside the handler.
            RouterMount(owntracks.router),
        ),
        edge_blocked_prefixes=("/location/owntracks",),
    )
    spec.card["route"] = f"/apps/{spec.id}"
    return spec
