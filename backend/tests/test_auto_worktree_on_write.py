"""The write-blocked path creates a worktree for EVERY chat that has a tree,
including the platform's own.

`_auto_create_worktree_for_chat` used to test `context_key.startswith("project:")`
and slice the slug off the key. `chat_worktree._project_of` documents at length
why that is wrong -- `DevelopmentContextProvider` is a project provider that
deliberately keys itself `development` rather than `project:development` (ADR
0014) -- and fixed it there. The rule was then re-derived in `build_streaming`
in its pre-fix form, so the one chat that cuts more branches than any other was
the one chat excluded from the feature.

That matters beyond tidiness: it is a large part of why
`WORKSHOP_TREE_LEASE_MOUNT` could not be armed. With the flag on, an unpinned
chat is read-only, and the design's answer is that the first write silently
makes a worktree. If the operator's own chat is excluded from "silently", the
one place a person meets this feature is the manual picker the design exists to
delete.
"""
from __future__ import annotations

import pytest

from services import build_streaming, chat_context, chat_store, chat_worktree, worktrees


class _FakeProject:
    """Stands in for a provider that owns a branch space."""
    def __init__(self, slug, key):
        self.slug = slug
        self.key = key
        self.dir = "/tmp"


@pytest.fixture
def captured(monkeypatch):
    calls = {}

    def fake_allocate(project_slug, task_id, *, title=None, allocated_by=None):
        calls["allocate"] = dict(project_slug=project_slug, task_id=task_id,
                                 title=title, allocated_by=allocated_by)
        return {"branch_slug": f"wt/{project_slug}/a-readable-name",
                "display_name": title or "a readable name"}

    monkeypatch.setattr(worktrees, "allocate_task_worktree", fake_allocate)
    monkeypatch.setattr(chat_store, "set_thread_branch",
                        lambda tid, slug: calls.setdefault("pinned", (tid, slug)))
    return calls


def _thread(monkeypatch, *, context_key, title="Chat toolbar fixes", branch=None):
    monkeypatch.setattr(chat_store, "get_thread", lambda tid: {
        "id": tid, "context_key": context_key, "title": title,
        "branch_slug": branch,
    })


def test_the_development_chat_gets_one(monkeypatch, captured):
    """The regression this file exists for. `development` does not start with
    `project:` and is still a chat with a branch space."""
    _thread(monkeypatch, context_key="development")
    monkeypatch.setattr(chat_worktree, "project_of",
                        lambda p: "development")
    monkeypatch.setattr(chat_context, "get_provider",
                        lambda key: _FakeProject("development", "development"))

    got = build_streaming._auto_create_worktree_for_chat("t1")

    assert got is not None, "the platform's own chat was refused a worktree"
    assert got["project_slug"] == "development"
    assert captured["allocate"]["project_slug"] == "development"
    # The title travels, so the worktree is not named after a hex id.
    assert captured["allocate"]["title"] == "Chat toolbar fixes"
    assert captured["pinned"] == ("t1", got["branch_slug"])


def test_a_project_chat_still_gets_one(monkeypatch, captured):
    """The case that already worked must keep working: this is a refactor of
    HOW the slug is resolved, not of which chats qualify."""
    _thread(monkeypatch, context_key="project:alpha", title="Fix the poller")
    monkeypatch.setattr(chat_worktree, "project_of", lambda p: "alpha")
    monkeypatch.setattr(chat_context, "get_provider",
                        lambda key: _FakeProject("alpha", "project:alpha"))

    got = build_streaming._auto_create_worktree_for_chat("t2")

    assert got is not None
    assert got["project_slug"] == "alpha"
    assert captured["allocate"]["title"] == "Fix the poller"


def test_a_chat_with_no_branch_space_gets_nothing(monkeypatch, captured):
    """A data chat has no tree to branch. It must fall through to the manual
    offer rather than inventing a project named after itself."""
    _thread(monkeypatch, context_key="general")
    monkeypatch.setattr(chat_worktree, "project_of", lambda p: None)
    monkeypatch.setattr(chat_context, "get_provider",
                        lambda key: object())

    assert build_streaming._auto_create_worktree_for_chat("t3") is None
    assert "allocate" not in captured


def test_an_already_pinned_chat_is_left_alone(monkeypatch, captured):
    """Second write in the same chat. One creation is the event; the rest are
    noise about the same fact."""
    _thread(monkeypatch, context_key="development", branch="wt/development/already")
    monkeypatch.setattr(chat_worktree, "project_of", lambda p: "development")
    monkeypatch.setattr(chat_context, "get_provider",
                        lambda key: _FakeProject("development", "development"))

    assert build_streaming._auto_create_worktree_for_chat("t4") is None
    assert "allocate" not in captured


def test_the_resolver_is_shared_and_not_copied():
    """The actual defect was a SECOND copy of the rule. Assert there is one:
    `chat_worktree.project_of` is the exported name and it is the same object
    the module uses internally."""
    assert chat_worktree.project_of is chat_worktree._project_of
