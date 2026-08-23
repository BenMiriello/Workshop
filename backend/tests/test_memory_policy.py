"""The `share` field: resolution, and what it actually changes about a search.

Curation, not confinement (SEC-A2): these tests assert what a SEARCH surfaces,
never what a process may read, because the second was never true on a box that
runs everything as one uid.
"""
from __future__ import annotations

import importlib

import numpy as np
import pytest


@pytest.fixture()
def policy(tmp_path, monkeypatch):
    """A fresh module bound to a temp store, so a test never reads or writes the
    real instance's policy file."""
    from services import memory_policy as mod
    monkeypatch.setattr(mod, "STORE", tmp_path / "memory-policy.json")
    monkeypatch.setattr(mod, "_cache", None)
    yield mod
    monkeypatch.setattr(mod, "_cache", None)


# --- resolution ---------------------------------------------------------------

def test_an_absent_file_is_every_default_and_is_not_created_by_reading(policy):
    assert not policy.STORE.exists()
    assert policy.resolve("note") == "inherit"
    assert policy.resolve("doc") == "inherit"
    assert policy.resolve("chat") == "private"
    assert not policy.STORE.exists(), "reading a missing policy must not create one"


def test_a_kind_the_phase_does_not_cover_resolves_to_inherit(policy):
    assert policy.resolve("vault") == "inherit"


def test_narrowest_wins_item_beats_scope_beats_global(policy):
    policy.set_defaults({"note": "open"})
    policy.set_scopes({"project:a": "private"})
    policy.set_item("workshop:note/project:a/n1", "open")

    # global only
    assert policy.resolve("note", "project:b") == "open"
    # scope overrides the global
    assert policy.resolve("note", "project:a") == "private"
    # item overrides the scope
    assert policy.resolve("note", "project:a", "workshop:note/project:a/n1") == "open"


def test_clearing_an_override_falls_back_rather_than_storing_inherit(policy):
    policy.set_scopes({"project:a": "private"})
    assert policy.resolve("note", "project:a") == "private"
    policy.set_scopes({"project:a": None})
    assert policy.resolve("note", "project:a") == "inherit"
    assert "project:a" not in policy.read()["scopes"]


def test_setting_one_default_does_not_reset_the_others(policy):
    policy.set_defaults({"chat": "inherit"})
    d = policy.read()["defaults"]
    assert d["chat"] == "inherit"
    assert d["note"] == "inherit" and d["doc"] == "inherit"


def test_an_invalid_value_is_refused(policy):
    with pytest.raises(ValueError):
        policy.set_item("workshop:note/workshop/x", "public")


def test_a_corrupt_file_degrades_to_defaults_rather_than_breaking_search(policy):
    policy.STORE.parent.mkdir(parents=True, exist_ok=True)
    policy.STORE.write_text("{not json at all")
    policy._cache = None
    assert policy.resolve("chat") == "private"


# --- the rule itself ----------------------------------------------------------

def test_inherit_is_exactly_the_tree_rule(policy):
    for in_reach in (True, False):
        assert policy.reachable(share="inherit", item_scope="project:a",
                                from_scope="workshop", in_reach=in_reach) is in_reach


def test_open_is_found_from_anywhere_even_out_of_reach(policy):
    assert policy.reachable(share="open", item_scope="development",
                            from_scope="project:a", in_reach=False) is True


def test_private_is_found_only_from_inside_its_own_scope(policy):
    # an ancestor whose reach WOULD normally include it
    assert policy.reachable(share="private", item_scope="project:a",
                            from_scope="workshop", in_reach=True) is False
    # the scope itself
    assert policy.reachable(share="private", item_scope="project:a",
                            from_scope="project:a", in_reach=True) is True


# --- end to end, through the real mask ----------------------------------------

CORPUS = [
    {"kind": "chat", "ref": "thread1", "scope": "development", "title": "t", "text": "alpha"},
    {"kind": "note", "ref": "projects/a/knowledge/n1.md", "scope": "project:a",
     "title": "n", "text": "alpha"},
    {"kind": "doc", "ref": "docs/x.md", "scope": "", "title": "d", "text": "alpha"},
]


def _kept(policy, from_scope, scopes):
    from services import semantic_index
    mask = semantic_index._keep_mask(CORPUS, None, scopes, from_scope)
    return {CORPUS[i]["ref"] for i, keep in enumerate(mask) if keep}


def test_a_chat_transcript_is_not_returned_to_an_ancestor_under_the_default(policy):
    # `development` is reachable here by construction; only `share` excludes it.
    kept = _kept(policy, "workshop", {"workshop", "development", "project:a"})
    assert "thread1" not in kept, "chat defaults to private, so an ancestor must not see it"
    assert "projects/a/knowledge/n1.md" in kept


def test_the_same_transcript_is_returned_after_flipping_that_one_chat_to_open(policy):
    policy.set_item(policy.chat_item_ref("thread1"), "open")
    kept = _kept(policy, "workshop", {"workshop", "development", "project:a"})
    assert "thread1" in kept


def test_flipping_the_global_chat_default_to_inherit_changes_the_same_search(policy):
    assert "thread1" not in _kept(policy, "workshop", {"workshop", "development", "project:a"})
    policy.set_defaults({"chat": "inherit"})
    assert "thread1" in _kept(policy, "workshop", {"workshop", "development", "project:a"})


def test_a_private_note_drops_out_of_an_ancestor_search_but_not_its_own(policy):
    policy.set_scopes({"project:a": "private"})
    frm_workshop = _kept(policy, "workshop", {"workshop", "project:a"})
    assert "projects/a/knowledge/n1.md" not in frm_workshop
    frm_own = _kept(policy, "project:a", {"project:a"})
    assert "projects/a/knowledge/n1.md" in frm_own


def test_docs_are_placeless_not_unreachable(policy):
    # A doc sits in no scope, so the tree has nothing to say about it and a
    # scoped search must still find it.
    assert "docs/x.md" in _kept(policy, "project:a", {"project:a"})


def test_no_from_scope_skips_the_policy_entirely(policy):
    from services import semantic_index
    mask = semantic_index._keep_mask(CORPUS, None, None, None)
    assert mask.all(), "a caller that did not say who it is behaves as before Phase 5"
    assert isinstance(mask, np.ndarray)


# --- a scopeless kind cannot be erased ----------------------------------------
#
# The 2026-08-23 defect, pinned in three places because it cost the whole
# document corpus. `.runtime/memory-policy.json` held `doc: private`, `private`
# resolved as `from_scope == item_scope`, and a doc's `item_scope` is "" -- so
# the comparison was false from every scope and 215 documents, including the
# platform's model of record, answered nothing to any real search while scoring
# 0.787 from the scopeless path no agent takes.

def test_private_on_a_kind_with_no_scope_falls_back_to_the_tree_rule(policy):
    """`private` means "found only from its own scope". A doc has no own scope,
    so there is nothing for the rule to mean, and it must not silently mean
    "nowhere"."""
    assert policy.reachable(share="private", item_scope="", from_scope="workshop",
                            in_reach=True) is True
    # It is `inherit`'s answer, not a hardcoded True: the tree still decides.
    assert policy.reachable(share="private", item_scope="", from_scope="workshop",
                            in_reach=False) is False


def test_no_defaults_change_can_hide_the_document_corpus_from_every_scope(policy):
    """The property, not the instance: for a kind that carries no scope, every
    value of the global default must leave it findable from somewhere. A setting
    that can express "findable from nowhere" is a corpus deletion with an
    undo the operator cannot find."""
    for value in policy.SHARE_VALUES:
        policy.set_defaults({"doc": value})
        reached_from = [frm for frm in ("workshop", "development", "project:a")
                        if "docs/x.md" in _kept(policy, frm, {frm})]
        assert reached_from, (
            f"doc default {value!r} made every document unreachable from every "
            "scope; no share value may erase a placeless kind")


def test_a_scoped_kind_is_still_restricted_by_private(policy):
    """The fallback above is narrow. A kind that HAS a scope keeps the old rule,
    or `private` would have stopped meaning anything at all."""
    policy.set_defaults({"note": "private"})
    assert "projects/a/knowledge/n1.md" not in _kept(policy, "workshop",
                                                     {"workshop", "project:a"})
    assert "projects/a/knowledge/n1.md" in _kept(policy, "project:a", {"project:a"})
