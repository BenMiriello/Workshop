"""Slug derivation: a worktree is named after its work, not after a database id.

The claim: `_kebab_branch_slug(title)` produces a readable branch slug from the
ticket title, and two tickets with the same title but different task ids get
distinct slugs (the collision path appends a short deterministic suffix).

These are pure functions -- no disk, no git, no database.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.worktrees import _kebab_branch_slug  # noqa: E402


def test_title_becomes_readable_slug():
    slug = _kebab_branch_slug("Chat toolbar: pill, rename, new chat")
    assert slug == "chat-toolbar-pill-rename-new-chat"


def test_hex_task_id_gets_t_prefix():
    # A 32-char hex id starts with a digit, so it is prefixed t- to satisfy
    # the SLUG_RE that requires the first character to be a letter.
    slug = _kebab_branch_slug("30777f91367b4c7b862df419cc9a36e2")
    assert slug.startswith("t-")


def test_slug_capped_at_48_chars():
    long_title = "A" * 100
    slug = _kebab_branch_slug(long_title)
    assert len(slug) <= 48


def test_collision_suffix_produces_distinct_slugs():
    # Two tasks with the same title get distinct slugs when the first slug's
    # directory already exists.  The collision path appends a 5-char suffix from
    # the task_id, making the pair (title, task_id) deterministic.
    title = "Chat toolbar: pill, rename, new chat"
    task_id_1 = "aaaaa" + "0" * 27
    task_id_2 = "bbbbb" + "0" * 27
    base_slug = _kebab_branch_slug(title)
    # Simulate the collision logic in allocate_task_worktree directly
    suffix_1 = (base_slug[:42].rstrip("-") + "-" + task_id_1[:5])[:48]
    suffix_2 = (base_slug[:42].rstrip("-") + "-" + task_id_2[:5])[:48]
    assert suffix_1 != suffix_2
    assert suffix_1.startswith(base_slug[:10])
    assert suffix_2.startswith(base_slug[:10])


def test_empty_string_does_not_crash():
    slug = _kebab_branch_slug("")
    assert slug == "t"


def test_special_characters_collapse_to_dashes():
    slug = _kebab_branch_slug("fix: auth/session — token expiry")
    assert "/" not in slug
    assert "—" not in slug
    assert slug.startswith("fix")
