"""The stored reply body is the ANSWER, not the answer with the turn's whole
play-by-play concatenated onto the front of it.

A turn emits two kinds of `text` segment and only one of them is the answer.
Narration ("Taking the concrete ones first...", "Both landed, starting the next
thread") has work after it; the answer does not. Position is the only thing that
tells them apart, and `_turn_content` used to ignore it and join everything.

Measured over the operator's real history before the fix: 535 of 632 replies
(85%) carried narration they should not have, 1.82 million characters of it,
half of all stored reply text. The renderer was never fooled -- it has always
split at the trailing run -- so this was invisible on screen and wrong in every
other consumer of `content`: the copy button, the chat-list preview, search,
notifications, and the context handed to the next turn.
"""
from services.build_streaming import answer_segments


def narr(s="Taking the concrete ones first."):
    return {"t": "text", "text": s}


def tool(name="Bash"):
    return {"t": "tool", "name": name}


ANSWER = {"t": "text", "text": "Here is the honest state."}


def test_narration_before_work_is_not_the_answer():
    # The shape of a real turn of the operator's: narrate, work, narrate, work,
    # answer. Only the last text segment is the reply.
    segs = [narr(), tool(), narr("Both landed."), tool(), ANSWER]
    assert answer_segments(segs) == [ANSWER]


def test_a_turn_that_only_answered_is_all_answer():
    assert answer_segments([ANSWER]) == [ANSWER]


def test_a_turn_that_ended_on_a_tool_never_answered():
    # 12.5% of the operator's replies are this shape: stopped, interrupted, or
    # out of budget mid-step. There is no answer, and saying so is the point --
    # `_turn_content` handles the fallback separately, so this must not invent one.
    assert answer_segments([narr(), tool()]) == []


def test_the_trailing_run_is_taken_whole():
    # Adjacent text normally coalesces into one segment, but a sealed one (a
    # message landed on it mid-turn) does not. Both belong to the answer.
    a = {"t": "text", "text": "First half.", "sealed": True}
    b = {"t": "text", "text": " Second half."}
    assert answer_segments([tool(), a, b]) == [a, b]


def test_only_the_TRAILING_run_counts():
    # Narration separated from the answer by work stays out of it, however much
    # of it there is. This is the case that was 83% of the corpus.
    segs = [narr("one"), tool(), narr("two"), tool(), narr("three"), tool(), ANSWER]
    assert answer_segments(segs) == [ANSWER]


def test_empty_turn():
    assert answer_segments([]) == []


def test_agent_and_approval_segments_break_the_run_like_a_tool_does():
    # A delegation and an approval are work, not the answer, so text above them
    # is narration by the same rule.
    for kind in ({"t": "agent", "name": "Explore"}, {"t": "approval", "id": "x"}):
        assert answer_segments([narr(), kind, ANSWER]) == [ANSWER]
