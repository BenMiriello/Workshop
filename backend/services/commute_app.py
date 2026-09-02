"""This app's declaration: what it is, what it mounts, what it runs.

Moved out of `services/app_registry.py` on 2026-09-01, where one hardcoded
factory per app made the platform's catalog module know every app by name. The
registry now discovers declarations and names none. Behaviour is unchanged: the
spec below is the same object, moved verbatim.
"""
from __future__ import annotations

from fastapi import Depends

from deps_user import require_domain_owner
from services.app_spec import AppSpec, RouterMount, runtime_dir


# Route modules this app needs; the catalog drops the app when they are not
# shipped. See `app_registry.APP_REQUIRES_ATTR`.
REQUIRES = ("commute",)


def app_spec() -> AppSpec:
    from routes import commute
    from services import commute_persona
    spec = AppSpec(
        id="commute",
        chat_provider=commute_persona.CommuteContextProvider,
        chat_snapshot=commute_persona.commute_snapshot,
        card={
            "title": "Commute",
            "tagline": "Day-of departures + transit status",
            "icon": "TrainFront",
        },
        identity={
            "sensitivity": "private",
            "api_prefixes": ["/commute"],
            "provides": [],
            "needs": ["location.read"],
        },
        mounts=(
            RouterMount(commute.router, deps=(Depends(require_domain_owner("commute")),)),
        ),
        stores=(("data", lambda: runtime_dir("commute"), "Commute", False),),
    )
    spec.card["route"] = f"/apps/{spec.id}"
    return spec
