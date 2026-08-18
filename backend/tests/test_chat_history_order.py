"""The order the MODEL reads, which is not the order the log stores.

A message sent while a reply was being written is appended when it arrives, so
it carries a lower seq than the reply, which is committed at the end. Rendered
flat, the model reads the message above the whole reply — including the part of
that reply written before the message was ever sent.

Cheap to dismiss as cosmetic and it is not: this is the only thing a fresh CLI
session sees. After a /compact or a /clear the model's entire picture of the
conversation is assembled from here.
"""
from services.chat_context import interleave_history


def user(content: str, **meta) -> dict:
    return {"role": "user", "content": content, **({"meta": meta} if meta else {})}


def reply(content: str, **meta) -> dict:
    return {"role": "assistant", "content": content, "meta": meta}


def text(s: str, cut=None) -> dict:
    seg = {"t": "text", "text": s}
    if cut:
        seg["cut_after"] = list(cut)
    return seg


def tool(name: str, cut=None) -> dict:
    seg = {"t": "tool", "name": name, "input": ""}
    if cut:
        seg["cut_after"] = list(cut)
    return seg


def shape(history):
    return [f"{m['role']}:{m['content']}" for m in interleave_history(history)]


class TestNothingToDo:
    def test_a_conversation_with_no_mid_run_messages_is_untouched(self):
        h = [user("a"), reply("b", turn_id="r1"), user("c")]
        assert interleave_history(h) is h

    def test_a_reply_nobody_interrupted_is_untouched(self):
        h = [user("a"), reply("b", turn_id="r1", segments=[text("b")])]
        assert shape(h) == ["user:a", "assistant:b"]


class TestTheMessageGoesWhereItLanded:
    def test_it_is_put_back_inside_the_reply(self):
        h = [
            user("go"),
            user("also do Y", during_turn="r1", cut_id="c1"),
            reply("I'll look into X.Done both.", turn_id="r1",
                  segments=[text("I'll look into X.", cut=["c1"]), text("Done both.")]),
        ]
        # Flat, the model read "also do Y" ABOVE "I'll look into X." — its own
        # words, written before the message existed, reading as the answer to it.
        assert shape(h) == [
            "user:go",
            "assistant:I'll look into X.",
            "user:also do Y",
            "assistant:Done both.",
        ]

    def test_a_message_that_beat_the_first_word_stays_above_the_reply(self):
        h = [
            user("go"),
            user("and also", during_turn="r1", cut_id="c1"),
            reply("all of it", turn_id="r1", cut_head=["c1"], segments=[text("all of it")]),
        ]
        assert shape(h) == ["user:go", "user:and also", "assistant:all of it"]

    def test_several_messages_each_land_in_their_own_place(self):
        h = [
            user("go"),
            user("one", during_turn="r1", cut_id="c1"),
            user("two", during_turn="r1", cut_id="c2"),
            reply("AB", turn_id="r1",
                  segments=[text("A", cut=["c1"]), tool("Read"), text("B", cut=["c2"])]),
        ]
        assert shape(h) == [
            "user:go", "assistant:A", "user:one", "assistant:B", "user:two",
        ]

    def test_two_messages_at_the_same_position_keep_their_order(self):
        h = [
            user("one", during_turn="r1", cut_id="c1"),
            user("two", during_turn="r1", cut_id="c2"),
            reply("AB", turn_id="r1", segments=[text("A", cut=["c1", "c2"]), text("B")]),
        ]
        assert shape(h) == ["assistant:A", "user:one", "user:two", "assistant:B"]

    def test_a_cut_on_a_tool_splits_the_text_either_side_of_it(self):
        h = [
            user("wait", during_turn="r1", cut_id="c1"),
            reply("AB", turn_id="r1", segments=[text("A"), tool("Read", cut=["c1"]), text("B")]),
        ]
        assert shape(h) == ["assistant:A", "user:wait", "assistant:B"]


class TestItRefusesToGuess:
    def test_a_reply_whose_text_does_not_reassemble_is_left_alone(self):
        """The fidelity guard. `content` is the join of the text segments, so if
        it is not, something rewrote one and any split would move or drop words.
        Flat is wrong but honest; a wrong split is neither."""
        h = [
            user("wait", during_turn="r1", cut_id="c1"),
            reply("something else entirely", turn_id="r1",
                  segments=[text("A", cut=["c1"]), text("B")]),
        ]
        assert shape(h) == ["user:wait", "assistant:something else entirely"]

    def test_a_message_with_no_recorded_position_is_left_alone(self):
        """Every transcript recorded before 2026-08-12 is this case. They keep
        the order they have always had rather than shifting under the operator."""
        h = [
            user("wait", during_turn="r1", at_unit=1),
            reply("AB", turn_id="r1", segments=[text("A"), text("B")]),
        ]
        assert shape(h) == ["user:wait", "assistant:AB"]

    def test_a_token_that_names_nothing_puts_the_message_after_the_reply(self):
        h = [
            user("wait", during_turn="r1", cut_id="gone"),
            reply("AB", turn_id="r1", segments=[text("A", cut=["other"]), text("B")]),
        ]
        assert shape(h) == ["assistant:AB", "user:wait"]

    def test_a_message_filed_against_an_already_committed_run_stays_put(self):
        """It is below that reply in the log because it arrived after it. The
        `j < i` test is what keeps it there."""
        h = [
            reply("AB", turn_id="r1", segments=[text("A"), text("B")]),
            user("late", during_turn="r1", cut_id="c1"),
        ]
        assert shape(h) == ["assistant:AB", "user:late"]

    def test_an_empty_stretch_emits_no_speaker_turn(self):
        """Two messages in a row mid-reply must not produce an empty assistant
        line between them: a blank turn reads as the model saying nothing."""
        h = [
            user("one", during_turn="r1", cut_id="c1"),
            user("two", during_turn="r1", cut_id="c2"),
            reply("AB", turn_id="r1",
                  segments=[text("A", cut=["c1"]), text("", cut=["c2"]), text("B")]),
        ]
        assert shape(h) == ["assistant:A", "user:one", "user:two", "assistant:B"]


class TestEveryPathThatShowsTheModelAHistory:
    """THREE READERS, ONE ORDER. `_build_prompt` was the one found first; the
    other two carry the identical defect and one of them bakes it in.

    The compaction summarizer is the worst of the three: its output REPLACES the
    messages it folded, so a wrong order there is not re-derived on the next
    turn, it is the history from then on.
    """

    HISTORY = [
        user("go"),
        user("also do Y", during_turn="r1", cut_id="c1"),
        reply("I'll look into X.Done both.", turn_id="r1",
              segments=[text("I'll look into X.", cut=["c1"]), text("Done both.")]),
    ]

    def test_the_summarizer_folds_the_true_order(self):
        from routes.chat import _fmt_for_summary
        out = _fmt_for_summary(self.HISTORY)
        assert out.index("I'll look into X.") < out.index("also do Y") < out.index("Done both.")

    def test_the_voice_path_reads_the_true_order(self, monkeypatch):
        from services import chat_store, voice_worker
        monkeypatch.setattr(
            chat_store, "get_thread",
            lambda _tid: {"messages": self.HISTORY, "cleared_through": 0})
        out = voice_worker._thread_seed("t")
        assert out.index("I'll look into X.") < out.index("also do Y") < out.index("Done both.")
