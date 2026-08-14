"""Shared fixtures, and the rule for tests that need a CONFIGURED instance.

Most of this suite tests logic and runs anywhere. A handful test the wiring of
things an instance acquires by being used: installed apps, a projects registry,
a ticket queue. None of that exists in a fresh clone, because all of it is
per-instance state under `.runtime/` and deliberately gitignored.

Those tests used to assert against it unguarded, so a clone on any machine but
the one they were written on reported roughly two dozen failures. That is the
worst possible signal to hand someone deploying this: it says "the project is
broken" when the only true statement is "this instance has not been set up yet",
and it makes the reviewer gate unusable downstream, which is exactly where an
honest gate matters most.

So: SKIP, loudly and with the reason, rather than fail. A skip says "not
applicable here". A failure says "something is wrong", and nothing is.

Use `requires_app("finance")` / `requires_registry()` at the top of a test that
needs deployment state. Do NOT use them to paper over a real failure: if a test
can build the state it needs, it should build it in a tmp_path instead.
"""
from __future__ import annotations

import json

import pytest


def requires_registry():
    """Skip unless this instance has a projects registry.

    Written on first project registration, so it is absent on a fresh clone and
    present on any instance that has been used.
    """
    from services import app_runner

    try:
        data = json.loads(app_runner.PROJECTS_REGISTRY.read_text())
    except (OSError, json.JSONDecodeError):
        pytest.skip(f"no projects registry at {app_runner.PROJECTS_REGISTRY} "
                    "(a fresh instance writes one when a project is registered)")
    if not (data.get("projects") or {}):
        pytest.skip("the projects registry on this instance is empty")
    return data


def requires_app(name: str):
    """Skip unless `name` is installed on this instance.

    Apps are installable, so their routes and chat personas only exist where
    they have been installed. A test asserting that /finance/cache is auth-gated
    is testing nothing on an instance with no finance app: the path 404s because
    the feature is absent, not because ownership failed.
    """
    from services import app_registry

    try:
        installed = set(app_registry.installed_app_ids())
    except Exception as e:  # a broken registry is a skip, not a failure, here
        pytest.skip(f"could not read the installed-apps registry: {e}")
    if name not in installed:
        pytest.skip(f"the {name} app is not installed on this instance")


def requires_route(app, path: str):
    """Skip unless `path` is actually mounted on this deployment."""
    if not any(getattr(r, "path", None) == path for r in app.routes):
        pytest.skip(f"{path} is not mounted on this deployment")


# --- database fixtures -------------------------------------------------------
# The DB suite runs against whatever WORKSHOP_DATABASE_URL points at, which on a
# developer machine is the SAME database holding real work. Teardown is a
# `DELETE FROM organization`, which cascades to every tenant table, so a wrong id
# would take real data with it. `tests-must-not-share-owner-storage` records the
# same failure shape for the file stores.
#
# Two guards, because one is not enough:
#   1. every test organization is created with the `wstest-` slug prefix, and
#      teardown refuses to delete anything without it;
#   2. setup runs inside try/finally, so a fixture that raises PART WAY through
#      still cleans up what it already created. Without that, a failure between
#      creating org A and org B leaked A forever, which is how stale `rev-*` and
#      `aud*` organizations accumulated in this database.

DB_ORG_PREFIX = "wstest-"


def _delete_test_orgs(org_ids) -> None:
    """Delete by id, but only for organizations carrying the test prefix."""
    from sqlalchemy import text

    from db.engine import owner_session

    ids = [str(o) for o in org_ids if o]
    if not ids:
        return
    with owner_session() as s:
        rows = s.execute(text(
            "SELECT id, slug FROM organization WHERE id = ANY(CAST(:ids AS uuid[]))"),
            {"ids": ids}).all()
        unsafe = [slug for _, slug in rows if not slug.startswith(DB_ORG_PREFIX)]
        if unsafe:
            raise AssertionError(
                f"refusing to delete organizations without the {DB_ORG_PREFIX!r} "
                f"prefix: {unsafe}. A test fixture is holding a real org id.")
        s.execute(text("DELETE FROM organization WHERE id = ANY(CAST(:ids AS uuid[]))"),
                  {"ids": ids})
