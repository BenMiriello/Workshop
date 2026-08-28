"""The guard that keeps this suite out of the operator's own database.

A GUARD NOBODY EXERCISES IS A GUARD THAT SILENTLY STOPS WORKING, and this one
lives in a fixture, which is exactly the place a rename or a refactor turns into
a dead patch with nothing going red. `conftest` already learned that lesson once
with the usage ledger, whose fixture asserts its own patch took.

What is pinned:

  1. the guard is INSTALLED for every test, and it raises something an
     `except Exception` cannot swallow. That matters because the record writes
     it has to catch go through `worktrees._record`, which catches `Exception`
     deliberately so a bookkeeping bug cannot fail an allocation.
  2. `redirect_file_store` really does take the process off this installation,
     which is the escape hatch the guard's message points at.

The defect behind both, measured 2026-08-27: 281 `worktree` rows in the live
database, every one of them a pytest fixture, because teardown was guarded and
creation was not.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.conftest import DB_ORG_PREFIX, redirect_file_store


def test_resolving_the_real_tenant_fails_the_test_that_did_it():
    """The operator's own app-user id, which is what a pinned automation gets."""
    from services import ownership, tenancy

    # `Failed` derives from BaseException, so this is the assertion that the
    # guard cannot be swallowed by the `except Exception` in `worktrees._record`.
    assert not issubclass(pytest.fail.Exception, Exception)
    owner = ownership.pin_operator()
    if owner is None:
        pytest.skip("this instance has no operator account, so there is no real "
                    "tenant for a test to reach")
    with pytest.raises(pytest.fail.Exception) as caught:
        tenancy.org_for_owner(owner)
    assert DB_ORG_PREFIX in str(caught.value)


def test_an_app_user_that_does_not_exist_keeps_the_functions_OWN_refusal():
    """The guard WRAPS `org_for_owner`, it does not replace it.

    That distinction is load-bearing: the real function fails closed on a
    redirected file store and on an unknown user, and both refusals are pinned
    elsewhere (`test_tenancy_file_store_guard.py`). A guard that short-circuited
    them would retire those assertions without anything going red.
    """
    from services import tenancy

    with pytest.raises(tenancy.TenantUnresolved):
        tenancy.org_for_owner(999_999)


def test_the_guard_survives_a_swallowing_caller():
    """The shape the leak actually had: a best-effort record write.

    The operator is pinned here on purpose, because that is the state the leaking
    modules ran in: a background pass pins the single operator and every store
    underneath it then resolves the real tenant.
    """
    from services import ownership, tenancy

    ownership.pin_operator()

    def _best_effort():
        try:
            return tenancy.org_and_user()
        except Exception:            # exactly what `worktrees._record` does
            return None

    with pytest.raises(pytest.fail.Exception):
        _best_effort()


def test_redirecting_the_file_store_takes_this_process_off_the_installation(
        tmp_path, monkeypatch):
    from services import tenancy

    assert tenancy.configured() is True
    redirect_file_store(monkeypatch, tmp_path)
    # `configured()` reads the environment rather than the database, so this is
    # the answer the record and lease layers get: no relational store here.
    assert tenancy.configured() is False
    assert tenancy.available() is False
