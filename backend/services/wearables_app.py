"""This app's declaration: what it is, what it mounts, what it runs.

Moved out of `services/app_registry.py` on 2026-09-01, where one hardcoded
factory per app made the platform's catalog module know every app by name. The
registry now discovers declarations and names none. Behaviour is unchanged: the
spec below is the same object, moved verbatim.
"""
from __future__ import annotations

import os

from services.app_spec import AppSpec


def app_spec() -> AppSpec:
    # External app: served from its own origin, no in-platform route module.
    return AppSpec(
        id="wearables",
        card={
            "title": "Open Wearables",
            "tagline": "Wearables data UI",
            "icon": "Watch",
            "href": os.environ.get("WORKSHOP_WEARABLES_URL", ""),
            "external": True,
            "probe": "open-wearables-backend",
        },
        identity={
            "sensitivity": "private",
            "api_prefixes": [],
            "provides": [],
            "needs": [],
        },
        # This app IS three external processes; the probes are its whole
        # backend presence, which is why the platform used to carry them.
        service_probes=(
            ("open-wearables-backend", "wearables",
             os.environ.get("WORKSHOP_WEARABLES_BACKEND", "http://127.0.0.1:8100"),
             "/", (200, 301, 302, 404)),
            ("open-wearables-frontend", "wearables",
             os.environ.get("WORKSHOP_WEARABLES_FRONTEND", "http://127.0.0.1:3100"),
             "/", (200, 301, 302)),
            ("open-wearables-flower", "wearables",
             os.environ.get("WORKSHOP_WEARABLES_FLOWER", "http://127.0.0.1:5556"),
             "/", (200, 401)),
        ),
    )
