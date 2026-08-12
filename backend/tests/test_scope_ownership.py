"""A chat cannot bind to someone else's project, build agent or agent.

Every HTTP route that reaches a project, an agent, a worktree, a ticket or a
knowledge note is mounted operator-only (`main.py`'s `_OPERATOR`), so the REST
surface was never the hole. Chat is mounted for any authenticated user, takes a
free-form `context_key` from the client, and resolves it straight into a working
directory and a task store — full agentic tools under bypassPermissions on the
build branches. Nothing along that path asked whose project it was.

Projects keep ONE globally unique slug and one directory on purpose (see
`paths.projects_root`): `project:alpha` is a scope key that a task store, a
knowledge layer, a role-uniqueness check and a secrets AAD are all keyed by, so
two owners each minting `project:alpha` would be alias multiplication, not
isolation. Uniqueness is enforced by the namespace; ownership is enforced here.

See docs/security-audit-2026-08-11-scope-separation.md section 9.
"""
from __future__ import annotations

import json

import pytest

from services import auth_store, chat_context, ownership, scope_owner, task_scope


@pytest.fixture
def two_users(tmp_path, monkeypatch):
    monkeypatch.setattr(auth_store, "DB_PATH", tmp_path / "auth.db")
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path / "runtime")
    auth_store.init_db()
    op = auth_store.create_user("operator@example.com", "password1")
    member = auth_store.create_user("member@example.com", "password2")
    auth_store.set_operator(op["id"])

    reg = tmp_path / "projects.json"
    monkeypatch.setenv("WORKSHOP_PROJECTS_REGISTRY", str(reg))
    reg.write_text(json.dumps({"projects": {
        "opsproject": {"dir": str(tmp_path / "p" / "opsproject"),
                       "owner_id": op["id"]},
        "memberproject": {"dir": str(tmp_path / "p" / "memberproject"),
                          "owner_id": member["id"]},
        "legacyproject": {"dir": str(tmp_path / "p" / "legacyproject")},
    }}))
    try:
        yield {"op": op["id"], "member": member["id"], "tmp": tmp_path}
    finally:
        ownership.clear_current_owner()


class TestOwnerOf:
    def test_it_reads_the_recorded_owner(self, two_users):
        assert scope_owner.owner_of("project:opsproject") == two_users["op"]
        assert scope_owner.owner_of("project:memberproject") == two_users["member"]

    def test_a_record_predating_the_field_has_no_owner(self, two_users):
        assert scope_owner.owner_of("project:legacyproject") is None

    def test_an_unqualified_scope_has_no_owner_to_read(self, two_users):
        # `workshop` names the same thing for everyone and is separated by
        # DIRECTORY, not by key — nobody is refused it.
        for key in ("workshop", "general", None, ""):
            assert scope_owner.owner_of(key) is None


class TestMayReach:
    def test_an_owner_reaches_their_own(self, two_users):
        ownership.set_current_owner(two_users["member"])
        assert scope_owner.may_reach("project:memberproject") is True

    def test_a_member_cannot_reach_the_operators_project(self, two_users):
        ownership.set_current_owner(two_users["member"])
        assert scope_owner.may_reach("project:opsproject") is False

    def test_the_operator_cannot_reach_a_members_project_either(self, two_users):
        # Ownership, not privilege. `is_operator` is not a key to other
        # people's data anywhere else in this system and is not one here.
        ownership.set_current_owner(two_users["op"])
        assert scope_owner.may_reach("project:memberproject") is False

    def test_a_legacy_record_stays_reachable(self, two_users):
        # Refusing would break every project on every existing install for its
        # own operator. Can only ever over-grant to the operator, never to a
        # second account, because a second account cannot have created one.
        ownership.set_current_owner(two_users["op"])
        assert scope_owner.may_reach("project:legacyproject") is True

    def test_no_caller_pinned_is_a_background_pass(self, two_users):
        ownership.clear_current_owner()
        assert scope_owner.may_reach("project:memberproject") is True

    def test_an_unknown_slug_is_not_an_ownership_question(self, two_users):
        # It has no record, so no owner. Containment and existence refuse it
        # elsewhere; this predicate must not pretend to be that check.
        ownership.set_current_owner(two_users["member"])
        assert scope_owner.may_reach("project:no-such-thing") is True


class TestTheChokepointsHonourIt:
    def test_chat_degrades_to_general_rather_than_binding(self, two_users):
        ownership.set_current_owner(two_users["member"])
        p = chat_context.get_provider("project:opsproject")
        assert p.key != "project:opsproject"
        assert not chat_context.is_build_provider(p), (
            "a refused scope must not keep the full agentic toolset")

    def test_the_task_store_refuses_the_same_key(self, two_users):
        ownership.set_current_owner(two_users["member"])
        assert task_scope.root_for_context("project:opsproject") is None

    def test_the_owner_still_gets_their_task_store(self, two_users):
        (two_users["tmp"] / "p" / "memberproject").mkdir(parents=True)
        ownership.set_current_owner(two_users["member"])
        assert task_scope.root_for_context("project:memberproject") is not None


class TestStamp:
    def test_a_new_record_records_its_creator(self, two_users):
        ownership.set_current_owner(two_users["member"])
        assert scope_owner.stamp() == {"owner_id": two_users["member"]}

    def test_a_script_with_no_caller_stamps_nothing(self, two_users):
        ownership.clear_current_owner()
        assert scope_owner.stamp() == {}


class TestUnqualifiedScopesArePartitionedByDirectory:
    """`workshop` and `role:<r>` name the same thing for every account, so they
    cannot be told apart by key and are separated by directory instead. This is
    the half that lets the ambient knowledge injection, the auto-recall and the
    note fences run for EVERYBODY again — they were briefly operator-only, which
    stopped the leak by leaving a second account with a Workshop that could not
    remember anything."""

    def test_two_owners_get_different_workshop_layers(self, two_users, monkeypatch):
        from services import knowledge
        monkeypatch.setenv("WORKSHOP_ROOT", str(two_users["tmp"] / "repo"))
        ownership.set_current_owner(two_users["op"])
        op_dir = knowledge.scope_dir("workshop")
        ownership.set_current_owner(two_users["member"])
        member_dir = knowledge.scope_dir("workshop")
        assert op_dir != member_dir
        # The member's layer sits under THEIR runtime dir, not the repo's
        # tracked one — a substring check on the bare id would pass by accident
        # on a tmp path that happens to contain the digit.
        assert member_dir.is_relative_to(
            ownership.user_runtime_dir(two_users["member"]))
        assert not member_dir.is_relative_to(two_users["tmp"] / "repo")

    def test_the_operators_layer_does_not_move(self, two_users, monkeypatch):
        from services import knowledge
        repo = two_users["tmp"] / "repo"
        monkeypatch.setenv("WORKSHOP_ROOT", str(repo))
        ownership.set_current_owner(two_users["op"])
        assert knowledge.scope_dir("workshop") == repo / "shared" / "knowledge"

    def test_learnings_follow_the_same_split(self, two_users, monkeypatch):
        from services import learnings
        monkeypatch.setenv("WORKSHOP_RUNTIME", str(two_users["tmp"] / "rt"))
        ownership.set_current_owner(two_users["op"])
        op_dir = learnings._dir()
        ownership.set_current_owner(two_users["member"])
        assert learnings._dir() != op_dir
