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


def redirect_file_store(monkeypatch, tmp_path) -> None:
    """Point this process's file store at a throwaway directory.

    THE SECOND WAY TO SATISFY THE GUARD BELOW, and the right one for a test that
    never wanted a database in the first place. `services/tenancy.configured()`
    answers False once `ownership._RUNTIME` has moved since import (see
    `_file_store_redirected` there), so the record and lease layers degrade to
    "no relational store on this box" -- which is a leg every one of these code
    paths already has, because it is what a checkout without Postgres runs.

    Use it from an autouse fixture at the top of a module that drives real git,
    real tickets or the sweep, and does not care about the durable record:

        @pytest.fixture(autouse=True)
        def _file_backed(tmp_path, monkeypatch):
            redirect_file_store(monkeypatch, tmp_path)

    A test that DOES care about the record wants the other way instead: mint a
    `wstest-` org and pin `tenancy.org_and_user` at it, as `test_leases.py` and
    `test_worktree_records.py` do.
    """
    from services import ownership, tenancy

    runtime = tmp_path / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(ownership, "_RUNTIME", runtime)
    tenancy.reset_cache()
    assert not tenancy.configured(), (
        "the file store redirect did not take, so this test is still pointed at "
        "the real installation")


@pytest.fixture(autouse=True)
def _no_test_may_resolve_the_real_tenant(request, monkeypatch):
    """A test that reaches the operator's own organization FAILS, loudly.

    THE HOLE THIS CLOSES. `_delete_test_orgs` above guards TEARDOWN and nothing
    guarded CREATION, so a test that never minted an org at all simply inherited
    the real one: `services/tenancy.org_and_user()` resolves the tenant from the
    OWNER contextvar, and `ownership.pin_operator()` pins the real operator.
    Every write underneath then landed in the production database.

    Measured 2026-08-27, then cleaned up by
    `scripts/prune_test_fixture_rows.py`: 289 `worktree` rows, ALL of them under
    one pytest fixture's project slug at `/tmp/pytest-of-*` paths, with 577
    timeline events behind them; 174 leases held by `job-<n>`, which is a
    fixture's holder id and not the format a real sweep writes; 17 leaked
    `wstest-*` organizations and the 17 accounts underneath them. A full suite
    run added eight more worktree rows while this was being written.

    The cost was not disk. `/branches` reads that table, so the operator's own
    page about what is stuck was describing pytest's temporary directories: it
    listed ONE branch, at a path that did not exist, while eleven real abandoned
    worktrees sat on the disk beside it.

    THREE FILES WERE DOING IT AND THIS IS NOT WHAT FIXED THOSE THREE
    (`test_gc_reap_locked`, `test_worktree_provision` and
    `test_reclaim_advances_the_fence` each declare themselves file-backed now).
    This is what catches the fourth. Any test that resolves a tenant it did not create now fails on the
    call rather than writing silently, and the two ways to satisfy it are the
    two that are already correct elsewhere in this suite:

      - mint a throwaway org and pin it (`test_leases.py`,
        `test_worktree_records.py`): `monkeypatch.setattr(tenancy,
        "org_and_user", lambda: (org_id, user_id))`, with the org carrying the
        `wstest-` prefix so teardown will accept it;
      - or redirect the file store (`monkeypatch.setattr(ownership, "_RUNTIME",
        tmp_path)`), which makes `tenancy.configured()` answer False and the
        record layer degrade to "no database today", which is the leg most of
        these tests wanted in the first place.

    IT RAISES A pytest OUTCOME, NOT A ValueError, and that is load-bearing. The
    record writes go through `worktrees._record`, which catches `Exception` on
    purpose so a bookkeeping bug cannot fail an allocation. `pytest.fail` raises
    `Failed`, which derives from `BaseException` precisely so a swallowing
    `except Exception` cannot hide it. A guard this one could swallow would be a
    guard that silently stopped working.
    """
    marker = request.node.get_closest_marker("reads_the_operators_tenant")
    if marker is not None:
        # ONE TEST HAS A REAL REASON: `test_dev_mode_gate` asserts that the
        # ticket migration did not drop the operator's actual queue, which is a
        # claim about the real data and cannot be made against a fixture. It is
        # a READ. The marker takes the reason as its argument so the exemption is
        # justified where it is taken rather than in a list somewhere else.
        assert marker.args and str(marker.args[0]).strip(), (
            "reads_the_operators_tenant needs a reason: say what about the real "
            "organization this test cannot assert against a fixture")

    from services import ownership, tenancy

    resolve = tenancy.org_for_owner

    def _refuse(owner_id):
        # CALL THROUGH FIRST, so this wraps the real function rather than
        # replacing it. `org_for_owner` already fails closed on a redirected file
        # store, and that refusal is itself pinned
        # (`test_tenancy_file_store_guard.py`); a guard that short-circuited it
        # would silently retire the assertion it is standing next to.
        resolve(owner_id)
        pytest.fail(
            f"a test resolved the real tenant for app user {owner_id}. Tests "
            f"must not read or write the operator's organization: mint a "
            f"{DB_ORG_PREFIX!r} org and pin `tenancy.org_and_user`, or redirect "
            f"`ownership._RUNTIME` at a tmp_path. See conftest."
            f"_no_test_may_resolve_the_real_tenant.")

    if marker is None:
        monkeypatch.setattr(tenancy, "org_for_owner", _refuse)
    # THE PIN DOES NOT SURVIVE A TEST EITHER, and that is half the leak rather
    # than tidiness. `ownership.current_owner()` reads a CONTEXTVAR, which is
    # process state: a test that pins the operator and does not clear it leaves
    # every test that runs after it pinned too, and a module that stubs its
    # stores and never mentions a database still resolves the real tenant
    # underneath (`tasks.reclaim` -> `_fence_ticket_lease` -> `leases.available`,
    # measured). That is order-dependent, which is why it was invisible: the same
    # test passes alone and writes to the operator's org in a full run.
    #
    # The resolution cache goes with it, keyed by app-user id, so an answer
    # resolved before this fixture existed cannot be handed to the next test.
    ownership.clear_current_owner()
    tenancy.reset_cache()
    yield
    ownership.clear_current_owner()
    tenancy.reset_cache()


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


@pytest.fixture(autouse=True)
def _isolate_jobs_log(tmp_path, monkeypatch):
    """Keep the test suite out of the operator's real jobs feed.

    `jobs.start()` appends to `.runtime/jobs.jsonl`, which is live operator
    state: the dashboard's "Running now" card, the runs feed and the liveness
    watchdog all read it. Nothing isolated it, so any test that opened a Job
    wrote a real `running` row into the running app.

    Measured 2026-08-29: the operator's dashboard showed nine runs in flight
    when three processes existed. `test_apps_api.py` opens a Job for its fixture
    app on every run, and those rows carry no pid, so `reconcile_stuck` will not
    write them off for two hours (its pid-less branch is a ceiling meant for
    records that predate pid recording). Every test run put a fresh phantom on
    the dashboard for two hours.

    Patches the env var AND the module attribute, for the reason spelled out in
    `_isolate_usage_ledger`: `JOBS_LOG` is bound at import, so an env-only fix
    depends on import order, while the shell writers reach the log through a
    subprocess that reads the env. The collapse cache keys on the path itself,
    so it invalidates correctly the moment the attribute moves.
    """
    log = tmp_path / "jobs.jsonl"
    monkeypatch.setenv("WORKSHOP_JOBS_LOG", str(log))
    from services import jobs

    monkeypatch.setattr(jobs, "JOBS_LOG", log)
    assert jobs.JOBS_LOG == log, "jobs log is not isolated"


