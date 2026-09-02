"""This app's declaration: what it is, what it mounts, what it runs.

Moved out of `services/app_registry.py` on 2026-09-01, where one hardcoded
factory per app made the platform's catalog module know every app by name. The
registry now discovers declarations and names none. Behaviour is unchanged: the
spec below is the same object, moved verbatim.
"""
from __future__ import annotations

from services.app_spec import AppSpec, RouterMount


# Route modules this app needs; the catalog drops the app when they are not
# shipped. See `app_registry.APP_REQUIRES_ATTR`.
REQUIRES = ("health",)


def app_spec() -> AppSpec:
    from routes import health
    return AppSpec(
        id="health",
        card=None,  # headless: iOS Health auto-export ingest + reads, no launcher card
        identity={
            "sensitivity": "private",
            "api_prefixes": ["/health"],
            "provides": ["health.read"],
            "needs": [],
        },
        mounts=(
            # /ingest: HTTP Basic bound to owner; reads: require_user per-endpoint.
            RouterMount(health.router),
        ),
        edge_blocked_prefixes=("/health/ingest",),
    )
