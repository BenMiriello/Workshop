"""The notes vault is per-owner.

`routes/notes.py` mounts under `require_user` and nothing else (see
`app_registry`), and every filesystem operation in `services/notes_vault`
resolves through `vault_root()`. That function returned ONE global directory,
so any authenticated account could list, read, overwrite, rename and delete
every note in the operator's vault — not a check that could be bypassed, an
absence of one. See the 2026-08-11 scope-separation security audit section 3.

The operator's own vault does not move: real Obsidian is pointed at
`$WORKSHOP_NOTES_VAULT`,
and this module deliberately does not own that editor.
"""
from __future__ import annotations

import pytest

from services import auth_store, notes_vault, ownership


@pytest.fixture
def two_users(tmp_path, monkeypatch):
    monkeypatch.setattr(auth_store, "DB_PATH", tmp_path / "auth.db")
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path / "runtime")
    monkeypatch.setenv("WORKSHOP_NOTES_VAULT", str(tmp_path / "operator-notes"))
    auth_store.init_db()
    op = auth_store.create_user("operator@example.com", "password1")
    member = auth_store.create_user("member@example.com", "password2")
    auth_store.set_operator(op["id"])
    try:
        yield {"op": op["id"], "member": member["id"], "tmp": tmp_path}
    finally:
        ownership.clear_current_owner()


class TestVaultRootIsPerOwner:
    def test_the_operator_keeps_the_obsidian_vault_unmoved(self, two_users):
        ownership.set_current_owner(two_users["op"])
        assert notes_vault.vault_root() == (two_users["tmp"] / "operator-notes").resolve()

    def test_a_second_account_gets_its_own_directory(self, two_users):
        ownership.set_current_owner(two_users["member"])
        root = notes_vault.vault_root()
        assert root != (two_users["tmp"] / "operator-notes").resolve()
        assert root == (two_users["tmp"] / "runtime" / "users"
                        / str(two_users["member"]) / "notes").resolve()

    def test_no_owner_pinned_is_the_operator_context(self, two_users):
        # Background passes and tests pin nothing; ownership.py already defines
        # that as the operator's context. It must not raise here — the notes
        # Store is declared at startup, before any request exists.
        ownership.clear_current_owner()
        assert notes_vault.vault_root() == (two_users["tmp"] / "operator-notes").resolve()


class TestOneAccountCannotReachAnother:
    """Through the real read/write/list/delete surface, not just the root."""

    def test_a_members_write_does_not_land_in_the_operators_vault(self, two_users):
        ownership.set_current_owner(two_users["op"])
        notes_vault.create_note("secrets", "# operator only\n")
        ownership.set_current_owner(two_users["member"])
        notes_vault.create_note("secrets", "# mine\n")
        assert notes_vault.read_note("secrets")["content"] == "# mine\n"
        ownership.set_current_owner(two_users["op"])
        assert notes_vault.read_note("secrets")["content"] == "# operator only\n"

    def test_a_member_cannot_list_the_operators_notes(self, two_users):
        ownership.set_current_owner(two_users["op"])
        notes_vault.create_note("private-plan", "# plan\n")
        ownership.set_current_owner(two_users["member"])
        assert [n["path"] for n in notes_vault.list_notes()] == []

    def test_a_member_cannot_delete_the_operators_note(self, two_users):
        ownership.set_current_owner(two_users["op"])
        notes_vault.create_note("keepme", "# keep\n")
        ownership.set_current_owner(two_users["member"])
        with pytest.raises(FileNotFoundError):
            notes_vault.delete_note("keepme")
        ownership.set_current_owner(two_users["op"])
        assert notes_vault.read_note("keepme")["content"] == "# keep\n"
