"""A scope key must not name a directory outside the workshop.

`task_scope.root_for_context` is the chokepoint every task store, ticket run and
@-mention cwd resolves through, and its `agent:` branch joined the slug straight
onto `.runtime/agent-scopes/` and `mkdir(parents=True)`'d the result. The slug
comes off an HTTP body — `POST /chat/threads/{id}/spawn-task` and `POST /tasks`
both take one, the former from any authenticated user — so
`agent:../../../../tmp/x` created a task store at an arbitrary host path, wrote
`.tasks.json` and `TODO.md` into it, and `launch_task_run` would then spawn
`project_ralph.sh --dir <that path>`: a detached autonomous run rooted outside
the tree.

The only containment downstream is `app_runner.is_self_dir`, an exact-equality
test against the platform repo — so `agent:../..` was caught and everything
deeper was not. That is the same shape as the `project:` traversal fixed
alongside it, on the branch that also ran something.

See docs/security-audit-2026-08-11-scope-separation.md section 2.
"""
from __future__ import annotations

import pytest

from services import ownership, task_scope


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path / ".runtime")
    return tmp_path


TRAVERSALS = ["../../..", "..", "../../../../tmp/pwn", "a/../../b", "/etc", ""]


class TestAgentScopeIsContained:
    def test_no_traversal_resolves_to_a_root(self, runtime):
        for payload in TRAVERSALS:
            assert task_scope.root_for_context(f"agent:{payload}") is None, (
                f"agent:{payload!r} resolved to a task-store root")

    def test_no_traversal_creates_a_directory(self, runtime):
        outside = runtime / "pwned"
        task_scope.root_for_context(f"agent:../../{outside.name}")
        assert not outside.exists(), (
            "the join mkdir'd before anything checked where it pointed")

    def test_an_ordinary_agent_slug_still_gets_its_scope(self, runtime):
        root = task_scope.root_for_context("agent:memory-keeper")
        assert root is not None
        assert root.is_dir()
        assert root.name == "memory-keeper"
        assert root.parent.name == "agent-scopes"

    def test_a_slug_with_a_trailing_newline_is_not_the_slug(self, runtime):
        # `$` in Python also matches before a trailing newline. `fullmatch` plus
        # a pattern with no `$` is the spelling that actually rejects it.
        assert task_scope.root_for_context("agent:memory-keeper\n") is None


class TestTheOtherBranchesStayClosed:
    def test_an_unknown_project_has_no_root(self, runtime):
        assert task_scope.root_for_context("project:no-such-project-xyz") is None

    def test_an_unknown_agent_gets_its_own_scope_and_nobody_elses_tree(self, runtime):
        # An unregistered slug is not a maker, so it resolves to its own scope
        # dir under the runtime root and never to a working tree.
        root = task_scope.root_for_context("agent:no-such-agent-xyz")
        assert root is not None and root.parent.name == "agent-scopes"

    def test_the_deleted_build_kind_resolves_to_nothing(self, runtime):
        # `build:<slug>` was the other way to name an agent. It is deleted, so
        # it is now an unrecognised kind rather than a second door.
        assert task_scope.root_for_context("build:no-such-agent-xyz") is None
