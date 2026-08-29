"""A note carries how far it reaches into a prompt.

`reach` is the field that folded the separate learnings store into this one
(the-plan-2026-08-23.md section 2; backlog-reconciliation-2026-08-24.md section
1.1 settles it against the `kind` field two same-day documents also proposed).

  index   the default: title and hook in the scope index, body on request
  always  the body is injected verbatim into every prompt in scope
  search  findable, never surfaced

The migration risk this pins is the marker: it is one HTML comment on line 1 of
every note in the store, and a new field in the middle of it must not change how
an old one reads.
"""
import pytest

from services import knowledge


@pytest.fixture
def scope(tmp_path, monkeypatch):
    d = tmp_path / "knowledge"
    d.mkdir()
    monkeypatch.setattr(knowledge, "scope_dir", lambda key, create=False: d)
    return "workshop"


def _meta(**kw):
    """A marker built the way `_put_note_sync` builds one."""
    return knowledge._with_meta(
        kw.get("body", "body"), kw.get("author", "operator"),
        "",
        kw.get("chat", ""), "", kw.get("reach", ""), "",
    )


def test_reach_defaults_to_index_everywhere_it_is_absent():
    assert knowledge.note_reach("no marker at all") == "index"
    assert knowledge.note_reach("<!-- author: operator -->\nbody") == "index"
    assert knowledge.note_reach(_meta()) == "index"


def test_a_marker_with_removed_fields_still_reads_correctly():
    """Notes on disk may still have type/pinned fields (written before the
    migration); the regex tolerates them without capturing them."""
    old = "<!-- author: derived:letta-export; type: reference -->\nbody"
    assert knowledge.note_reach(old) == "index"
    assert knowledge.split_author(old) == ("derived:letta-export", "body")

    older = "<!-- author: agent:x; type: decision; pinned: true; chat: t-1 -->\nb"
    assert knowledge.note_reach(older) == "index"
    assert knowledge.note_chat(older) == "t-1"


def test_the_default_reach_is_not_written_into_the_marker():
    """An untouched old note must stay byte-for-byte what it was, which is why
    every field omits itself at its default."""
    assert _meta(author="") == "body"
    assert "reach" not in _meta()


def test_always_and_search_round_trip():
    for r in ("always", "search"):
        assert knowledge.note_reach(_meta(reach=r)) == r


def test_an_unknown_reach_falls_back_rather_than_being_stored():
    assert knowledge.note_reach(_meta(reach="everywhere")) == "index"
    assert "reach" not in _meta(reach="everywhere")


def test_reach_survives_an_edit_that_does_not_mention_it(scope):
    """Round-tripping a note through the editor must not silently erase reach,
    the same rule author and chat already have."""
    knowledge._put_note_sync(scope, "n", "N", "v1", author="operator", reach="always")
    assert knowledge.note_reach((knowledge.scope_dir(scope) / "n.md").read_text()) == "always"

    knowledge._put_note_sync(scope, "n", "N", "v2")
    body = (knowledge.scope_dir(scope) / "n.md").read_text()
    assert knowledge.note_reach(body) == "always"
    assert knowledge.get_note(scope, "n") == "v2"


def test_reach_can_be_changed(scope):
    knowledge._put_note_sync(scope, "n", "N", "b", author="operator", reach="always")
    knowledge._put_note_sync(scope, "n", "N", "b", reach="search")
    assert knowledge.note_reach((knowledge.scope_dir(scope) / "n.md").read_text()) == "search"


def test_note_meta_reports_it(scope):
    knowledge._put_note_sync(scope, "n", "N", "b", author="operator", reach="always")
    assert knowledge.note_meta(scope, "n")["reach"] == "always"
    assert knowledge.note_meta(scope, "missing")["reach"] == "index"
