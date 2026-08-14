"""A test with a redirected file store must not reach the real database.

THE FAILURE THIS PINS ACTUALLY HAPPENED, and it is the reason these assertions
are worth their length.

Tests that exercise a real owner (`ownership.set_current_owner(6)`) redirect
`ownership._RUNTIME` at a tmp_path so they do not write real chats. That guard
is correct and long-standing. It stopped covering the whole write when the chat
mirror went in: `chat_log.append_sync` now also writes each message into
Postgres, and the tenant for that write is resolved from the OWNER contextvar,
which the redirect does not touch. So the file half of the write went to a
tmp_path and the database half went to the operator's real database.

It stayed invisible because `tenancy.available()` assumed no test machine had
Postgres. That was true until this one got one. The voice suite alone left 56
phantom threads behind, most titled "Voice", eleven of them created inside nine
seconds.

These tests need no database on purpose: the guard has to hold on every checkout,
including the ones where the bug is unreproducible.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from services import ownership, tenancy


def test_a_redirected_file_store_makes_the_database_unavailable(monkeypatch,
                                                                tmp_path):
    """`available()` is the gate every mirror path checks, so it answers first."""
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path)
    assert tenancy.available() is False


def test_a_redirected_file_store_refuses_to_resolve_a_tenant(monkeypatch,
                                                             tmp_path):
    """Belt and braces: the function that hands out the real key fails closed.

    A caller that forgets `available()` must not be able to write. This is the
    assertion that would have caught the original bug, because the mirror path
    reached `org_and_user()` directly.
    """
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path)
    with pytest.raises(tenancy.TenantUnresolved):
        tenancy.org_for_owner(6)


def test_the_guard_notices_a_redirect_that_happens_after_import(monkeypatch,
                                                                tmp_path):
    """Configuration is not redirection, and the difference is the whole design.

    A real deployment may set WORKSHOP_RUNTIME, so "differs from the default
    path" would be the wrong test and would disable the database in production.
    What marks a test is that the path CHANGES after import. Pinned because the
    obvious simplification breaks a deployment silently.
    """
    assert tenancy._file_store_redirected() is False
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path)
    assert tenancy._file_store_redirected() is True
    monkeypatch.undo()
    assert tenancy._file_store_redirected() is False


def test_an_unredirected_process_is_not_hobbled(monkeypatch):
    """The guard must not answer False for a normal process.

    Without this, a fix that simply returned False always would pass every
    other test in this file.
    """
    monkeypatch.setattr(ownership, "_RUNTIME",
                        Path(str(tenancy._RUNTIME_AT_IMPORT)))
    assert tenancy._file_store_redirected() is False
