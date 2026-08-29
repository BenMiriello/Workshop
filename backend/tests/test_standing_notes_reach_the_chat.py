"""A standing note reaches the prompt.

Ported from test_learnings_reach_the_chat.py when `.runtime/learnings` folded
into the note store (ticket 81198145). The regression it was written for is the
one worth keeping: a preference captured from a general chat lands in the
`workshop` scope, and `workshop` was once excluded from the injection on the
reading that a general chat is covered by the workspace guardrails. It was not,
they were different stores, and eleven operator corrections written over three
weeks reached nothing at all.

That cannot recur now, because there is no second store to write to, and this
is what pins it.
"""
import pytest

from services import chat_context, knowledge, standing


class _Provider:
    """The one method `learnings_context` asks a provider for."""

    def __init__(self, scope: str) -> None:
        self._scope = scope

    def knowledge_scope(self) -> str:
        return self._scope


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Redirect the store a write touches.

    A test that writes a standing note MUST redirect it, for the same reason a
    test that authenticates as a real owner must redirect its storage: the first
    run of the pre-fold version of this file put 'A global guardrail. [src:test]'
    into the live global store, where it would have ridden into every prompt in
    the workspace, and it had to be removed by hand.
    """
    dirs: dict[str, object] = {}

    def scope_dir(key, create=False):
        d = tmp_path / key.replace(":", "-")
        d.mkdir(parents=True, exist_ok=True)
        dirs[key] = d
        return d

    monkeypatch.setattr(knowledge, "scope_dir", scope_dir)
    return dirs


def test_a_preference_captured_from_a_general_chat_reaches_the_prompt(store):
    standing.record("Never use an em-dash in any copy a user can read.",
                    scope="workshop", source="operator")
    out = chat_context.learnings_context(_Provider("workshop"))
    assert "em-dash" in out
    assert "OPERATOR PREFERENCES" in out


def test_the_widest_layer_reaches_a_narrower_scope(store):
    """What the old `global` scope did, done by the read-union instead. A note
    in `workshop` is in every scope's prompt, because `workshop` is the widest
    element of every read-union."""
    standing.record("Never use an em-dash in any copy a user can read.",
                    scope="workshop", source="operator")
    out = chat_context.learnings_context(_Provider("development"))
    assert "em-dash" in out


def test_it_is_not_injected_twice(store):
    """Two blocks became one. A note in the widest layer appearing in both would
    be the fold reintroducing the duplication it removed."""
    standing.record("Never use an em-dash in any copy a user can read.",
                    scope="workshop", source="operator")
    out = chat_context.learnings_context(_Provider("development"))
    assert out.count("em-dash") == 1


def test_a_scoped_block_is_named_for_the_scope_it_holds(store):
    standing.record("Always run the typecheck before you claim a change is done.",
                    scope="development", source="operator")
    out = chat_context.learnings_context(_Provider("development"))
    assert "AGENT MEMORY (scope: development)" in out


def test_nothing_to_say_says_nothing(store):
    assert chat_context.learnings_context(_Provider("development")) == ""


def test_an_ordinary_note_never_reaches_the_prompt(store):
    """`reach` is the whole distinction: a note written to be looked up must not
    turn up in every prompt."""
    knowledge._put_note_sync("workshop", "ordinary", "Ordinary", "some background fact")
    assert chat_context.learnings_context(_Provider("workshop")) == ""
