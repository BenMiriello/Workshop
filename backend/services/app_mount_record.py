"""What THIS process actually mounted, and from which file it read the roster.

`app_registry` answers "which apps can be built here" and `installed_apps`
answers "which apps does this instance install". Both answer for whoever is
already running the code. The question that went unanswerable for two days in
2026-09 is the same one asked from OUTSIDE the process:

  * `installed_apps.REGISTRY_PATH` is anchored to the module's own location, so
    two checkouts of one source on one host resolve two different `.runtime/`
    directories and read two different rosters;
  * nothing the process served ever printed the one it chose;
  * and an agent in a sandbox does not share the backend's mount namespace, so
    `/proc/<pid>/cwd` and `/proc/<pid>/environ` are unreadable and every attempt
    to check from outside measured the checker's own filesystem instead.

Every app route answered 404, every status surface was green, and the whole
difference was a path nothing printed.

So the process records what it mounted, at the moment it mounts it, and serves
that record on its unauthenticated liveness endpoint. RECORDED, NOT
RECOMPUTED: a re-read at request time answers what the file says NOW, which is
a different question from what this process loaded at boot, and substituting
the second for the first is exactly what made the outage invisible.

Its own module rather than a section of `app_registry`, because it is a
different question from that file's: the catalog is about what CAN be built,
this is about what one process DID.

THE RECORD IS A VALUE, HELD ON THE ASGI APP, not a module global. That is not
tidiness: the record describes ONE app object's mounting pass, and a module
global is per-process, so under more than one uvicorn worker a global would let
one worker's answer be served for another's. Each app records its own truth and
serves its own truth.
"""
from __future__ import annotations

import logging
from typing import Any

from services import app_registry, installed_apps

log = logging.getLogger("workshop.apps")


def _read_roster() -> tuple[list[str], str | None]:
    """The roster, plus why it could not be read. Never raises: this feeds a
    liveness endpoint, and an endpoint that 500s tells the caller less than one
    that names the fault."""
    try:
        return installed_apps.installed_app_ids(), None
    except installed_apps.RegistryUnreadable as e:
        return [], str(e)


def build(app_ids: list[str], route_count: int) -> dict[str, Any]:
    """Pin what this process mounted, and say so when it is less than the roster.

    Called once by the platform's route-mounting pass, with the ids whose
    routers were actually included and how many routes they added.

    THE LOUD CASE IS "THE ROSTER NAMES APPS AND FEWER MOUNTED", never "nothing
    mounted": an instance that installs no apps is a correct fresh clone, it
    must boot silently, and `scripts/check_fresh_clone.sh` measures that. The
    difference between the two is the entire point -- they used to produce the
    same empty list and the same empty log.

    Returns the record for the caller to hold; holding it is the caller's job
    precisely so this module owns no process state.

    Names no app: every id it prints is read from the roster or the catalog.
    """
    installed, error = _read_roster()
    catalog = sorted(app_registry.BUILTIN_APPS)
    # NOT IN THE CATALOG -- `app_registry`'s own answer, reused rather than
    # recomputed here, so there is never a second definition of "unresolved".
    try:
        uncatalogued = set(app_registry.unresolved_installed_ids())
    except installed_apps.RegistryUnreadable:
        uncatalogued = set()
    # What did not mount, which is the FACT; `uncatalogued` is the usual reason
    # for it but not the only one -- an app can be in the catalog and still
    # contribute no router.
    missing = [i for i in installed if i not in app_ids]

    rec: dict[str, Any] = {
        "registry": str(installed_apps.REGISTRY_PATH),
        "registry_present": installed_apps.REGISTRY_PATH.exists(),
        "installed": list(installed),
        "mounted": list(app_ids),
        "unresolved": missing,
        "catalog": catalog,
        "routes": int(route_count),
        "error": error,
    }

    if error:
        log.error("apps: the installed-apps roster at %s could not be read: %s",
                  installed_apps.REGISTRY_PATH, error)
    elif missing:
        reasons = "; ".join(
            f"{i}: " + ("no declaration with that id in the catalog"
                        if i in uncatalogued
                        else "declared, but it mounted no router")
            for i in missing)
        log.error(
            "apps: the roster at %s names %d app(s) and %d mounted. Missing -- "
            "%s. Those apps serve no routes, so every request to them answers "
            "404 while this process stays healthy. The catalog resolved here "
            "holds: %s. Most likely their distributions are not installed into "
            "the backend venv (run `python3 scripts/install_apps.py`), or this "
            "process resolved a different roster file than the one you are "
            "reading.",
            installed_apps.REGISTRY_PATH, len(installed), len(app_ids),
            reasons, ", ".join(catalog) or "(nothing)")
    elif installed:
        log.info("apps: %d installed, %d mounted, %d route(s), roster %s",
                 len(installed), len(app_ids), route_count,
                 installed_apps.REGISTRY_PATH)
    else:
        # The fresh-clone case: one line, at INFO, and it still names the path
        # -- because "the roster is empty" and "the roster I am reading is not
        # the one this process read" are indistinguishable without it.
        log.info("apps: none installed (roster %s %s); catalog holds %d "
                 "declaration(s)", installed_apps.REGISTRY_PATH,
                 "is empty" if rec["registry_present"] else "does not exist",
                 len(catalog))
    return rec


def unmounted() -> dict[str, Any]:
    """The same shape, for an app that has not reached its mounting pass.

    `mounted` is null, and null is a real answer that is NOT `[]`: it means this
    app has not mounted yet, so no caller may read it as "zero apps installed".
    That exact conflation is the one this module exists to end.
    """
    installed, error = _read_roster()
    return {
        "registry": str(installed_apps.REGISTRY_PATH),
        "registry_present": installed_apps.REGISTRY_PATH.exists(),
        "installed": installed,
        "mounted": None,
        "unresolved": None,
        "catalog": sorted(app_registry.BUILTIN_APPS),
        "routes": None,
        "error": error,
    }
