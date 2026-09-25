"""A fence writes only where its caller could reach anyway.

The ```note``` and ```learning-note``` fences both accept an explicit `scope`.
That override is a real affordance — a build agent in `project:foo` deciding a
finding is cross-cutting and belongs in `workshop` — and it is also a way to
write into a scope the chat is not bound to.

It was briefly operator-only, which was right while `workshop` was ONE shared
directory read into every chat's prompt. It is per-owner now
(`knowledge._shared_knowledge_root`), so aiming a note at your own `workshop` is
ordinary use, and refusing it would leave a second account unable to record a
cross-project fact at all. What stays refused is naming someone ELSE's project
or agent scope. Reachability, not privilege — `scope_owner.may_reach`.

Not a refusal either way: the note lands, at the scope this chat actually is.

See the 2026-08-11 scope-separation security audit sections 3 and 9.
"""
from __future__ import annotations

import asyncio

import pytest

from services import auth_store, ownership, task_scope


@pytest.fixture
def instance(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSHOP_ROOT", str(tmp_path))
    monkeypatch.setenv("WORKSHOP_RUNTIME", str(tmp_path / ".runtime"))
    (tmp_path / ".runtime" / "workspace" / "knowledge").mkdir(parents=True)
    monkeypatch.setattr(auth_store, "DB_PATH", tmp_path / "auth.db")
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path / ".runtime")
    auth_store.init_db()
    op = auth_store.create_user("operator@example.com", "password1")
    member = auth_store.create_user("member@example.com", "password2")
    auth_store.set_operator(op["id"])
    import json as _json
    reg = tmp_path / "projects.json"
    monkeypatch.setenv("WORKSHOP_PROJECTS_REGISTRY", str(reg))
    reg.write_text(_json.dumps({"projects": {
        "owned-by-op": {"dir": str(tmp_path / "p"), "owner_id": op["id"]},
    }}))
    try:
        yield {"root": tmp_path, "op": op["id"], "member": member["id"]}
    finally:
        ownership.clear_current_owner()


def _note(scope: str) -> str:
    return ('ok\n\n```note\n{"title": "T", "body": "B", "scope": "%s"}\n```\n'
            % scope)


def _learning(scope: str) -> str:
    # A real guardrail, not a two-word placeholder. Since ADR 0022 the learnings
    # write path refuses candidates that are not instructional guidance, and
    # "prefers brevity" is refused as too thin. These tests are about which
    # SCOPE a learning lands in, so the note has to survive the gate for the
    # scope assertion to mean anything.
    return ('ok\n\n```learning-note\n{"note": "Prefer brief replies: answer the '
            'question asked and do not restate the request back.", '
            '"scope": "%s"}\n```\n' % scope)


class TestNoteFence:
    def test_the_operator_may_still_aim_a_note_at_workshop(self, instance):
        ownership.set_current_owner(instance["op"])
        _, labels, refs = asyncio.run(
            task_scope.apply_note_block("project:alpha", _note("workshop")))
        assert refs and refs[0]["scope"] == "workshop"

    def test_a_member_may_promote_into_their_own_workshop(self, instance):
        # `workshop` is per-owner, so this writes to THEIR layer, not anyone
        # else's. Refusing it would make the documented affordance
        # operator-only for no remaining reason.
        ownership.set_current_owner(instance["member"])
        _, _labels, refs = asyncio.run(
            task_scope.apply_note_block("project:alpha", _note("workshop")))
        assert refs and refs[0]["scope"] == "workshop"

    def test_a_member_cannot_aim_a_note_at_someone_elses_project(self, instance):
        ownership.set_current_owner(instance["member"])
        _, _labels, refs = asyncio.run(
            task_scope.apply_note_block("general", _note("project:owned-by-op")))
        assert refs, "the note must still be written, just not where it asked"
        assert refs[0]["scope"] == "workshop", (
            "it must fall back to the chat's own scope, not the one it named")


class TestLearningFence:
    # The RECEIPT WORDING changed on 2026-08-28 when the learnings store folded
    # into the note store (ticket 81198145): it no longer names a store that
    # exists. What these tests are actually about -- that a member's fence
    # cannot reach someone else's project, and falls back to the chat's own
    # scope -- is unchanged, and is still what the assertions turn on.
    def test_a_member_learning_may_reach_their_own_workshop(self, instance):
        ownership.set_current_owner(instance["member"])
        _cleaned, labels = task_scope.apply_learning_note(
            "project:alpha", _learning("workshop"))
        assert labels == ["Captured a preference in workshop memory."]

    def test_a_member_learning_cannot_reach_someone_elses_project(self, instance):
        ownership.set_current_owner(instance["member"])
        _cleaned, labels = task_scope.apply_learning_note(
            "project:alpha", _learning("project:owned-by-op"))
        assert labels == ["Captured a preference in project:alpha memory."]

    def test_the_operator_override_still_works(self, instance):
        ownership.set_current_owner(instance["op"])
        _cleaned, labels = task_scope.apply_learning_note(
            "project:alpha", _learning("workshop"))
        assert labels == ["Captured a preference in workshop memory."]


class TestUnknownKeysDoNotInventAStore:
    def test_a_junk_context_key_resolves_to_workshop_not_a_new_app(self, instance):
        from services import knowledge
        assert knowledge.scope_for_context("zzz-not-an-app") == "workshop"
        assert not (instance["root"] / "apps" / "zzz-not-an-app").exists()

    def test_the_bare_workshop_key_names_itself(self, instance):
        from services import knowledge
        assert knowledge.scope_for_context("workshop") == "workshop"
