"""An image the operator attached is still readable after the chat compacts.

THE BUG, and it is the oldest unaddressed request in this project (2026-08-27):
images attached to a chat stop being visible to the model "after a while".

The diagnosis on record was wrong and is retracted in
`services/attachment_carry`, which carries the measurement. Two boundaries were
tested against the real CLI (2.1.239), driven the way `build_streaming` drives
it, with an image whose content was never named in any text:

  - across an ORDINARY turn boundary the model still read the image correctly
    ("magenta, orange, red, cyan", the right answer, not guessable);
  - across a COMPACTION it answered "NO IMAGE."

So the loss is at compaction, where the history becomes a text summary and a
summary cannot carry a picture. Meanwhile the transcript is append-only, so
`MessageList` goes on drawing the chip and its thumbnail off `meta.attachments`
forever: the operator can still SEE the file the model can no longer READ,
which is why this kept getting re-reported rather than diagnosed.

These pin the repair: the thread records that a compaction took its images, the
next real turn gets them back as native blocks rebuilt from the blobs (which
were never deleted), and it happens once per compaction rather than on every
turn afterwards.
"""
from __future__ import annotations

import pytest

from services import attachment_carry, chat_attachments, chat_store, ownership

# A 1x1 PNG. The bytes only have to be a real image the store will accept and
# hash; nothing here renders one.
PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc```\x00\x00"
    b"\x00\x04\x00\x01\xf6\x178U\x00\x00\x00\x00IEND\xaeB`\x82"
)


@pytest.fixture()
def owned(tmp_path, monkeypatch):
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path)
    ownership.set_current_owner(1)
    try:
        yield
    finally:
        ownership.clear_current_owner()


def _attach(name: str, data: bytes = PNG) -> dict:
    rec = chat_attachments.save(data, name, "image")
    return {"id": rec["id"], "name": rec["name"], "kind": "image"}


@pytest.fixture()
def thread_with_an_image(owned):
    tid = chat_store.create_thread("general")["id"]
    ref = _attach("screenshot.png")
    chat_store.append_message(tid, "user", "look at this", meta={"attachments": [ref]})
    chat_store.append_message(tid, "assistant", "I see it.")
    return tid, ref


class TestAnOrdinaryTurnOwesNothing:
    """The half of the old diagnosis that was wrong. Nothing is re-attached on a
    turn that follows an ordinary turn, because nothing was lost there."""

    def test_a_fresh_thread_owes_no_images(self, thread_with_an_image):
        tid, _ref = thread_with_an_image
        assert attachment_carry.is_owed(tid) is False
        assert attachment_carry.due(tid, "what does it show?") == []


class TestACompactionOwesThemBack:
    def test_the_compaction_marks_the_thread(self, thread_with_an_image):
        tid, _ref = thread_with_an_image
        chat_store.note_auto_compact(tid)
        assert attachment_carry.is_owed(tid) is True

    def test_the_next_turn_gets_the_image_back(self, thread_with_an_image):
        tid, ref = thread_with_an_image
        chat_store.note_auto_compact(tid)
        assert attachment_carry.due(tid, "what does it show?") == [ref]

    def test_and_it_is_a_real_image_block_not_a_filename(self, thread_with_an_image):
        """The whole point: the bytes are content-addressed and still on disk,
        so what goes back is the picture, not a path the model cannot open."""
        tid, _ref = thread_with_an_image
        chat_store.note_auto_compact(tid)
        blocks = chat_attachments.content_blocks(attachment_carry.due(tid, "hi"))
        assert [b["type"] for b in blocks] == ["image"]
        assert blocks[0]["source"]["media_type"] == "image/png"
        assert blocks[0]["source"]["data"]

    def test_it_says_they_are_not_new_uploads(self, thread_with_an_image):
        """Unlabelled, a re-attached screenshot reads as one the operator just
        sent and the model answers as if the conversation restarted around it."""
        tid, ref = thread_with_an_image
        chat_store.note_auto_compact(tid)
        note = attachment_carry.describe(attachment_carry.due(tid, "hi"))
        assert ref["name"] in note
        assert "not new uploads" in note

    def test_only_once_per_compaction(self, thread_with_an_image):
        """Once the blocks are back in the live session they stay there. Sending
        them again every turn would refill the window the compaction emptied."""
        tid, ref = thread_with_an_image
        chat_store.note_auto_compact(tid)
        assert attachment_carry.due(tid, "first message after") == [ref]
        attachment_carry.note_carried(tid)
        assert attachment_carry.due(tid, "second message after") == []

    def test_a_second_compaction_owes_them_again(self, thread_with_an_image):
        tid, ref = thread_with_an_image
        chat_store.note_auto_compact(tid)
        attachment_carry.note_carried(tid)
        chat_store.note_auto_compact(tid)
        assert attachment_carry.due(tid, "after the second one") == [ref]


class TestWhatIsNotCarried:
    def test_a_slash_command_gets_nothing(self, thread_with_an_image):
        """A `/compact` turn must not be handed the images: they would be the
        first thing the compaction it is about to run threw away again."""
        tid, _ref = thread_with_an_image
        chat_store.note_auto_compact(tid)
        assert attachment_carry.due(tid, "/compact") == []
        assert attachment_carry.due(tid, "/clear") == []
        # A message that merely begins with "/" is not a command.
        assert attachment_carry.due(tid, "/compact\nand also look again") != []

    def test_text_and_pdf_attachments_are_not_carried(self, owned):
        """An image is the one kind a text summary provably cannot carry. Text
        can reach the summary the ordinary way, and a pdf is a document's worth
        of pages -- which is the window-refilling this exists to avoid."""
        tid = chat_store.create_thread("general")["id"]
        doc = chat_attachments.save(b"hello there", "notes.txt", "text")
        pdf = chat_attachments.save(b"%PDF-1.4 x", "paper.pdf", "pdf")
        chat_store.append_message(
            tid, "user", "files",
            meta={"attachments": [{"id": doc["id"], "name": doc["name"], "kind": "text"},
                                  {"id": pdf["id"], "name": pdf["name"], "kind": "pdf"}]})
        chat_store.note_auto_compact(tid)
        assert attachment_carry.due(tid, "and now?") == []

    def test_a_deleted_attachment_is_skipped_not_announced(self, thread_with_an_image):
        """The operator removed it. Handing back a placeholder saying so would
        be reporting on a re-send they never asked for."""
        tid, ref = thread_with_an_image
        chat_attachments.delete(ref["id"])
        chat_store.note_auto_compact(tid)
        assert attachment_carry.due(tid, "and now?") == []

    def test_the_kind_is_read_off_the_index_not_the_ref(self, owned):
        """The ref's `kind` is carried for the chip to render without a lookup,
        and the server has always re-read its own index before handing anything
        to a model. A ref that lies must not turn a text file into an image."""
        tid = chat_store.create_thread("general")["id"]
        doc = chat_attachments.save(b"not a picture", "notes.txt", "text")
        chat_store.append_message(
            tid, "user", "file",
            meta={"attachments": [{"id": doc["id"], "name": doc["name"],
                                   "kind": "image"}]})
        chat_store.note_auto_compact(tid)
        assert attachment_carry.due(tid, "and now?") == []


class TestTheCapsHoldTheWindow:
    def test_no_more_than_the_maximum(self, owned):
        """A long chat's whole image history would refill the window the
        compaction just emptied, which is the reason it was compacted."""
        tid = chat_store.create_thread("general")["id"]
        for i in range(attachment_carry.MAX_IMAGES + 4):
            # Distinct bytes per file, or content addressing would collapse
            # them into one and the cap would never be reached.
            ref = _attach(f"shot-{i}.png", PNG + bytes([i]))
            chat_store.append_message(tid, "user", f"m{i}",
                                      meta={"attachments": [ref]})
        chat_store.note_auto_compact(tid)
        got = attachment_carry.due(tid, "and now?")
        assert len(got) == attachment_carry.MAX_IMAGES

    def test_it_keeps_the_newest_and_sends_them_oldest_first(self, owned):
        """Recency is the relevance rule. The ORDER sent is chronological,
        because the model maps a run of unnamed image blocks to the one line of
        names by position."""
        tid = chat_store.create_thread("general")["id"]
        for i in range(attachment_carry.MAX_IMAGES + 2):
            ref = _attach(f"shot-{i}.png", PNG + bytes([i]))
            chat_store.append_message(tid, "user", f"m{i}",
                                      meta={"attachments": [ref]})
        chat_store.note_auto_compact(tid)
        names = [r["name"] for r in attachment_carry.due(tid, "and now?")]
        newest = [f"shot-{i}.png"
                  for i in range(2, attachment_carry.MAX_IMAGES + 2)]
        assert names == newest

    def test_the_same_screenshot_twice_costs_one_block(self, owned):
        """De-duplicated by content hash, which the blob store gives for free:
        re-dropping a file is a second index row over one blob."""
        tid = chat_store.create_thread("general")["id"]
        for i in range(3):
            chat_store.append_message(tid, "user", f"m{i}",
                                      meta={"attachments": [_attach("same.png")]})
        chat_store.note_auto_compact(tid)
        assert len(attachment_carry.due(tid, "and now?")) == 1

    def test_a_huge_file_stops_the_walk(self, owned):
        """A count cap alone is not a window cap: one 18MB photo is not six
        screenshots."""
        tid = chat_store.create_thread("general")["id"]
        big = PNG + b"\x00" * attachment_carry.MAX_BYTES
        chat_store.append_message(tid, "user", "old",
                                  meta={"attachments": [_attach("old.png", PNG + b"\x01")]})
        chat_store.append_message(tid, "user", "huge",
                                  meta={"attachments": [_attach("huge.png", big)]})
        chat_store.note_auto_compact(tid)
        # The newest is taken even though it alone exceeds the budget -- a cap
        # that returned nothing would be worse than the bug -- and the walk then
        # stops rather than adding the older one on top of it.
        assert [r["name"] for r in attachment_carry.due(tid, "and now?")] == ["huge.png"]


class TestClearingIsNotCompacting:
    def test_a_clear_does_not_hand_the_images_back(self, thread_with_an_image):
        """`/clear` is the operator saying forget all of it. It drops the
        session and `turn_marks` in one write of its own, so it never marks
        anything owed and there is nothing to hand back."""
        tid, _ref = thread_with_an_image
        chat_store.clear_thread_context(tid)
        assert attachment_carry.is_owed(tid) is False
        assert attachment_carry.due(tid, "starting over") == []

    def test_a_lost_session_does_hand_them_back(self, thread_with_an_image):
        """A failed resume, a fork, a branch switch or a rescope all re-send
        the history as TEXT, which carries no image any more than a summary
        does."""
        tid, ref = thread_with_an_image
        chat_store.clear_thread_session(tid)
        assert attachment_carry.due(tid, "carry on") == [ref]
