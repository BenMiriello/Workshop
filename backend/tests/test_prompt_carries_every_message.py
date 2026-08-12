"""The prompt carries every message it is handed. Nothing silently drops one.

`MAX_HISTORY = 60` used to slice the transcript inside `_build_prompt`, and it
was measured in the wrong unit. The store proves it: the largest thread is about
110k tokens in THIRTEEN messages and passed a 60-message cap untouched, while a
129-message thread less than half its size (~52k tokens) was amputated. A cap on
the count cannot protect the window it exists to protect, and what it deleted
was the START of long conversations, which is where the reason for the work is
written down.

Removed 2026-08-12 on the operator's instruction. The invariant that replaces it
is the one asserted here: what goes in comes out. The only thing allowed to take
a message out of a prompt is an explicit `/clear`, which cuts at a stored
boundary long before this function sees the list.
"""
import pytest

from routes import chat as C
from services import chat_context


@pytest.fixture
def provider():
    return chat_context.get_provider("general")


def _messages(n: int) -> list[dict]:
    """n alternating turns, each individually identifiable in the output."""
    out = []
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        out.append({"role": role, "content": f"message-number-{i}", "meta": {}})
    return out


class TestNoMessageIsDropped:
    @pytest.mark.parametrize("n", [1, 10, 60, 61, 120, 400])
    def test_every_message_survives(self, provider, n):
        prompt = C._build_prompt(provider, _messages(n), "the new message")
        missing = [i for i in range(n) if f"message-number-{i}" not in prompt]
        assert not missing, f"{len(missing)} of {n} messages dropped: {missing[:8]}"

    def test_the_oldest_message_survives_past_the_old_cap(self, provider):
        """The exact regression. At 120 messages the old code kept the last 60,
        so message 0 (where the operator says what they are trying to do) was
        the first thing thrown away."""
        prompt = C._build_prompt(provider, _messages(120), "the new message")
        assert "message-number-0" in prompt
        assert "message-number-59" in prompt

    def test_the_new_message_is_present(self, provider):
        prompt = C._build_prompt(provider, _messages(5), "the-newest-thing-said")
        assert "the-newest-thing-said" in prompt

    def test_an_empty_history_is_fine(self, provider):
        assert "only-message" in C._build_prompt(provider, [], "only-message")

    def test_order_is_preserved(self, provider):
        prompt = C._build_prompt(provider, _messages(30), "x")
        positions = [prompt.index(f"message-number-{i}") for i in range(30)]
        assert positions == sorted(positions)


class TestTheCapIsGoneForGood:
    def test_no_message_cap_constant_survives(self):
        """Named so that re-adding it fails here, with the reasoning above."""
        assert not hasattr(C, "MAX_HISTORY")


class TestAutoRecallIsNotInjected:
    """Auto-recall pasted the top 3 knowledge notes into every prompt, ranked by
    `sum(hay.count(t))`: no stopwords, no IDF, no length normalisation, so it was
    a length sort. Replaced by `mcp__workshop__search`, which the model CALLS.
    Pull, not push."""

    def test_the_prompt_builder_takes_no_recalled_argument(self):
        import inspect
        assert "recalled" not in inspect.signature(C._build_prompt).parameters

    def test_no_relevant_notes_block_is_emitted(self, provider):
        prompt = C._build_prompt(provider, _messages(4), "tell me about the chat scope picker")
        assert "RELEVANT NOTES" not in prompt

    def test_the_note_formatter_is_gone(self):
        assert not hasattr(C, "_recalled_block")
