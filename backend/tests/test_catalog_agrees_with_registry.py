"""The installed-apps registry and the catalog must not disagree.

MEASURED OUTAGE, 2026-09-04. Every app's source had correctly left the platform
repo. `.runtime/installed-apps.json` listed eight apps. `app_registry.BUILTIN_APPS`
was EMPTY, because an app's declaration now arrives through a
`[project.entry-points."workshop.apps"]` line that only exists once the app's
distribution has been installed into the backend's venv -- and nothing installed
it. The backend came up serving zero app routes, zero personas, zero stores and,
worst of all, zero `edge_blocked_prefixes`, which is the set the public edge uses
to refuse unauthenticated ingest. Nothing logged an error, because an empty
catalog is LEGITIMATE: a slimmed instance is allowed to ship fewer apps, and
`_build_catalog` is deliberately tolerant.

That tolerance is right and this is the missing half of it. "No apps" is fine.
"Eight apps installed and none of them resolvable" is not; it is the only state
that cannot be an intentional configuration, and it is cheap to detect.

The two things this file must not become:

  * It must name NO app. The registry supplies the ids and the catalog supplies
    what it resolved; both are read, neither is typed.
  * It must PASS on a fresh clone, where the registry is absent and the catalog
    is empty and the instance is correct. An empty registry skips.

`scripts/install_apps.py` is the fix this test protects, and
`scripts/restart_backend.sh` runs it before every restart so the state cannot
be reached by forgetting a step.
"""
from __future__ import annotations

import pytest


def _registry_ids() -> list[str]:
    from services import installed_apps

    try:
        return list(installed_apps.installed_app_ids())
    except Exception as e:  # a broken registry is a different test's problem
        pytest.skip(f"could not read the installed-apps registry: {e}")


def test_an_installed_app_resolves_in_the_catalog():
    """Every id the registry lists must be something the catalog can build.

    An id in the registry with no catalog entry is an app the operator believes
    is installed and that serves nothing at all.
    """
    from services import app_registry

    wanted = _registry_ids()
    if not wanted:
        pytest.skip("this instance has no apps installed (a fresh clone is like this)")

    resolved = set(app_registry.BUILTIN_APPS)
    unresolved = [app_id for app_id in wanted if app_id not in resolved]
    assert not unresolved, (
        f"{len(unresolved)} of {len(wanted)} installed app(s) resolve to nothing in the "
        f"catalog: {sorted(unresolved)}. Their routes, personas, stores and edge blocks "
        "are all absent and nothing logs an error. Most likely their distributions are "
        "not installed into the backend venv: run `python3 scripts/install_apps.py`."
    )


def test_the_declared_surfaces_are_not_silently_empty():
    """The derived sets an installed app contributes must be non-empty somewhere.

    Every one of these is read by a live surface -- the router mounts, the chat
    persona map, the public edge's refuse-list. If the registry lists apps and
    ALL of these are empty, the catalog resolved nothing, whatever
    `BUILTIN_APPS` happens to say.
    """
    from services import app_registry

    if not _registry_ids():
        pytest.skip("this instance has no apps installed (a fresh clone is like this)")

    specs = app_registry.installed_specs()
    assert specs, (
        "the registry lists installed apps and `installed_specs()` returned none, "
        "so every app-contributed surface is empty: run `python3 scripts/install_apps.py`."
    )


def test_an_app_that_declares_an_edge_block_still_contributes_it():
    """The one with a safety consequence, stated on its own.

    `workshop_public_app` refuses the paths in `installed_edge_blocks()` so a
    public visitor cannot inherit the proxy's loopback trust. That list moved
    from a hardcoded platform tuple onto each app's declaration, which is
    correct -- and it means an unresolved catalog silently turns the refuse-list
    into the empty set rather than failing.
    """
    from services import app_registry

    if not _registry_ids():
        pytest.skip("this instance has no apps installed (a fresh clone is like this)")

    specs = app_registry.installed_specs()
    declares = [s for s in specs if s.edge_blocked_prefixes or s.edge_blocked_exact]
    if not declares:
        # An empty catalog reaches here too, and must NOT be read as "no app
        # declares one" -- but the test above already fails loudly on that, so
        # this skip says which of the two it is rather than hiding either.
        pytest.skip("no installed app declares an edge block"
                    if specs else "the catalog resolved nothing (see the test above)")

    prefixes, exact = app_registry.installed_edge_blocks()
    assert prefixes or exact, (
        "installed apps declare unauthenticated ingest paths and the edge's "
        "refuse-list came back empty."
    )
