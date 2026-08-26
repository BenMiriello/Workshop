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


# A user is NOT cleaned up by deleting its organization. `user_account` carries
# no `org_id` (it is the one tenant table that cannot, since a user exists before
# any membership), so nothing cascades to it and a fixture that only deletes its
# org leaks its user forever.
#
# That is not hypothetical. Measured 2026-08-17: 3,826 rows in `user_account`, of
# which ONE is a real person. Two fixtures created a UserAccount and cleaned up
# only the org (`test_db_chat.py`, `test_chat_tree_turns.py`), so every full suite
# run minted roughly 100 more. The daily histogram was 1,271 / 348 / 170 / 2,036,
# rising, because the rate tracks how often anyone runs the tests.
#
# The rows were invisible to the application, since RLS requires a shared
# membership and these have none. Invisible is not harmless: they were in the
# production table, and "tests must not share owner storage" was already a
# recorded lesson from the file stores. This is the same lesson, one layer down.
#
# One helper, called by all three fixtures, rather than the inline DELETE that
# existed in exactly one of them.
_TEST_EMAIL_DOMAIN = "@example.test"


def _delete_test_users(user_ids) -> None:
    """Delete by id, but only for accounts in the reserved test domain.

    RFC 2606 reserves `example.test`, so no real address can end in it. The guard
    mirrors the organization prefix guard above and exists for the same reason: a
    fixture holding a real id must fail loudly rather than delete a person.
    """
    from sqlalchemy import text

    from db.engine import owner_session

    ids = [str(u) for u in user_ids if u]
    if not ids:
        return
    with owner_session() as s:
        rows = s.execute(text(
            "SELECT id, email FROM user_account WHERE id = ANY(CAST(:ids AS uuid[]))"),
            {"ids": ids}).all()
        unsafe = [email for _, email in rows if not (email or "").endswith(_TEST_EMAIL_DOMAIN)]
        if unsafe:
            raise AssertionError(
                f"refusing to delete accounts outside {_TEST_EMAIL_DOMAIN!r}: "
                f"{unsafe}. A test fixture is holding a real user id.")
        s.execute(text("DELETE FROM user_account WHERE id = ANY(CAST(:ids AS uuid[]))"),
                  {"ids": ids})


@pytest.fixture(autouse=True)
def _isolate_usage_ledger(tmp_path, monkeypatch):
    """Keep the test suite out of the operator's real usage ledger.

    `usage_ledger.record()` appends to `.runtime/usage.jsonl`, which is live
    operator state: the admission window, the spend charts and every cost figure
    read it. Isolation was opt-in per test file and only five files opted in, so
    every other test that drove a chat turn or a compaction wrote real-looking
    rows into it. Measured 2026-08-26: 4,196 of 5,127 rows were test writes,
    1,724 of them from one module, all carrying `tokens: 11`. Any measurement
    taken off that file was reading mostly itself.

    Patches BOTH the module attribute and the env var on purpose. `LEDGER_PATH`
    is bound at import, so an env-only fix depends on import ordering; but the
    two shell writers reach the ledger through the `python -m
    services.usage_ledger record` shim, which is a SUBPROCESS and reads the env
    var, so an attribute-only fix misses those. Neither alone is sufficient.

    This also fences `prune()`, which rewrites the ledger in place and would
    silently truncate the operator's history if an unisolated test ever called
    it.

    Fails LOUDLY rather than degrading. `raising=True` and the assert below
    exist because the failure mode of a silent guard is that a rename turns the
    attribute patch into a dead write, the fixture quietly falls back to
    env-only against a module that is already imported, and the suite starts
    appending to the operator's ledger again with nothing going red.
    """
    ledger = tmp_path / "usage.jsonl"
    budget = tmp_path / "usage-budget.json"
    monkeypatch.setenv("WORKSHOP_USAGE_LEDGER", str(ledger))
    monkeypatch.setenv("WORKSHOP_USAGE_BUDGET", str(budget))
    from services import usage_ledger

    monkeypatch.setattr(usage_ledger, "LEDGER_PATH", ledger)
    monkeypatch.setattr(usage_ledger, "BUDGET_CONFIG_PATH", budget)
    assert usage_ledger.LEDGER_PATH == ledger, "usage ledger is not isolated"
