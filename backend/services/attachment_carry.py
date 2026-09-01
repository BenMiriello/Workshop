"""The images an ongoing conversation still needs after a compaction.

WHAT WAS MEASURED (2026-09-01, CLI 2.1.239, driven exactly the way
`build_streaming` drives it: `--input-format stream-json` on stdin,
`--resume <session_id>`, one session across five turns).

  turn 1  a 128x128 PNG of four differently-coloured quadrants, handed over as
          a native image block, with "do not describe it, do not name any
          colour, reply OK"                            ->  "OK"
  turn 2  "list the colours of its four quadrants, clockwise from the
          top-left"                    ->  "magenta, orange, red, cyan"
          That is the right answer and it is not guessable: the four colours
          were drawn at random from eight and were never named in any text
          either side sent.
  turn 3  filler, so the compaction has something to summarize.
  turn 4  "/compact"  ->  compact_boundary, trigger manual, 32611 -> 2052 tokens
  turn 5  the SAME question as turn 2  ->  "NO IMAGE."

So an ORDINARY TURN BOUNDARY DOES NOT LOSE AN IMAGE. The CLI keeps the image
block in its own session transcript and a resumed turn reloads it. COMPACTION
loses it, because compaction replaces the history with a text summary and a
text summary cannot carry a picture. The long-standing note that an upload
"survives exactly one turn" was naming the wrong boundary, and the line it
cited was never the cause.

THE OPERATOR GOES ON SEEING THE FILE THE WHOLE TIME. The transcript is
append-only and a compaction never touches it, so the chip and its thumbnail
keep rendering off `meta.attachments` forever. A file that is still visible and
is no longer readable is the worst version of this bug: nothing looks wrong, so
it gets re-reported as "the model is ignoring my screenshot".

WHAT THIS DOES. When a compaction lands, the thread is marked as owing its
images. The next turn that is a real message re-attaches the most recent ones as
native blocks rebuilt from the content-addressed blobs -- which were never
deleted, so this needs no new storage -- and clears the mark. Once per
compaction, not once per turn: the blocks are back in the live session and stay
there until the next compaction, exactly as the original upload did.

RELEVANCE IS RECENCY, AND IT IS CAPPED. Re-sending every image a long chat ever
carried would refill the window the compaction just emptied, and a full window
is the reason a compaction happened. So the transcript is walked backwards and
the newest images are taken up to `MAX_IMAGES` / `MAX_BYTES`, de-duplicated by
content hash, so the same screenshot dropped three times costs one block.

IMAGES ONLY. A text attachment is text, and whatever mattered about it can
reach the summary the ordinary way. A PDF is a document's worth of pages, which
is the window-refilling problem the caps exist to avoid. An image is the one
kind a summary provably cannot carry, which is what turn 5 above measured.

NO BYTES ARE STORED OR LOGGED HERE. The mark is a boolean on the thread record
and the carried set is a list of attachment ids; blocks are built from the blob
store at delivery time. Same rule that keeps base64 out of the turn journal.
"""

from __future__ import annotations

import logging

from . import chat_attachments, chat_log, chat_store

log = logging.getLogger(__name__)

# The mark lives on the thread's `turn_marks` because that is already the
# "what has this conversation been told" ledger, and because `clear_thread_context`
# wipes `turn_marks` wholesale. An operator who just dropped the entire context
# does not want three screenshots handed back on their next message, and that
# falls out of the storage choice instead of needing a rule of its own.
MARK = "images_owed"

# How many images one compaction may cost. Six is the size of a batch the
# operator actually sends (a run of photos or screenshots dropped into one
# message); at roughly 1.5k tokens each that is ~9k tokens against a window a
# compaction had just taken down to ~2k, paid once.
MAX_IMAGES = 6
# And a byte ceiling as well as a count, because one 18MB photo is not six
# screenshots. Raw bytes; base64 inflates by a third on the wire.
MAX_BYTES = 10 * 1024 * 1024


def note_dropped(thread_id: str) -> None:
    """Record that this thread's live context no longer holds its images.

    Called from wherever the context is replaced or discarded, not from the
    upload path: an attachment is not owed until something has taken it away.
    """
    if not thread_id:
        return
    try:
        chat_store.set_turn_marks(thread_id, **{MARK: True})
    except Exception as e:  # noqa: BLE001 - never fail a compaction over this
        log.warning("attachment carry: could not mark %s: %s", thread_id, e)


def note_carried(thread_id: str) -> None:
    """Record that the images have been handed back to the live session."""
    if not thread_id:
        return
    try:
        chat_store.set_turn_marks(thread_id, **{MARK: False})
    except Exception as e:  # noqa: BLE001
        log.warning("attachment carry: could not clear the mark on %s: %s",
                    thread_id, e)


def is_owed(thread_id: str) -> bool:
    """Whether a compaction has taken this thread's images since the last carry.

    Reads thread META rather than folding the event log: this is asked on the
    delivery path of every single turn, and the answer is no almost every time.
    """
    try:
        meta = chat_log.read_meta(thread_id) or {}
    except Exception:  # noqa: BLE001 - a thread we cannot read owes nothing
        return False
    marks = meta.get("turn_marks")
    return bool(isinstance(marks, dict) and marks.get(MARK))


def _is_command(prompt: str) -> bool:
    """A single-line slash command, the same test `routes/chat._is_raw_command`
    makes. A `/compact` turn must not be given the images back: they would be
    the first thing the compaction it is about to run threw away again."""
    s = (prompt or "").strip()
    return s.startswith("/") and "\n" not in s


def recent_images(thread_id: str, owner_id: int | None = None) -> list[dict]:
    """The newest images in this thread, oldest first, within the caps.

    Oldest first because that is the order they were sent in, and the model is
    about to be handed them as a run of unnamed blocks followed by one line of
    names (see `describe`); any other order would mislabel them.
    """
    try:
        thread = chat_store.get_thread(thread_id)
    except Exception as e:  # noqa: BLE001
        log.warning("attachment carry: could not read thread %s: %s", thread_id, e)
        return []
    if thread is None:
        return []
    # ONE index read, not one per candidate. `chat_attachments.get` re-reads the
    # whole append-only index every call, and a long chat can offer dozens of
    # candidates before the caps are reached.
    try:
        by_id = {r["id"]: r for r in chat_attachments.list_records(owner_id)
                 if r.get("id")}
    except Exception as e:  # noqa: BLE001
        log.warning("attachment carry: could not read the file index: %s", e)
        return []

    picked: list[dict] = []
    seen: set[str] = set()
    total = 0
    for msg in reversed(thread.get("messages") or []):
        refs = (msg.get("meta") or {}).get("attachments") or []
        for ref in reversed(refs):
            att_id = str((ref or {}).get("id") or "")
            rec = by_id.get(att_id)
            # The KIND ON THE RECORD, never the one on the ref: the ref's copy
            # is carried for the chip to render without a lookup and the server
            # has always re-read its own index before handing anything to a
            # model.
            if not rec or rec.get("kind") != "image":
                continue
            sha = str(rec.get("sha256") or "")
            if not sha or sha in seen:
                continue
            if chat_attachments.blob_for(rec, owner_id) is None:
                # Deleted since. Silently skipped rather than announced: the
                # operator did not ask for this re-send and does not need to be
                # told a file they removed stayed removed.
                continue
            size = int(rec.get("size") or 0)
            if picked and total + size > MAX_BYTES:
                return list(reversed(picked))
            seen.add(sha)
            total += size
            picked.append({"id": att_id, "name": rec.get("name") or "",
                           "kind": "image"})
            if len(picked) >= MAX_IMAGES:
                return list(reversed(picked))
    return list(reversed(picked))


def due(thread_id: str, prompt: str, owner_id: int | None = None) -> list[dict]:
    """The image refs this turn should re-attach, or `[]` -- which is the answer
    on every turn but the first one after a compaction."""
    if not thread_id or _is_command(prompt):
        return []
    if not is_owed(thread_id):
        return []
    return recent_images(thread_id, owner_id)


def describe(refs: list[dict]) -> str:
    """The one line that tells the model what these blocks are.

    Without it a re-attached screenshot reads as a screenshot the operator just
    sent, and the model answers as if the conversation had restarted around it.
    Appended to the prompt, not to the stored message, for the same reason
    `chat_attachments.describe` is: the transcript already draws chips for the
    files the operator actually attached, and must not also show a sentence.
    """
    names = [str((r or {}).get("name") or "").strip() for r in refs or []]
    names = [n for n in names if n]
    if not names:
        return ""
    return (
        "Re-attached from earlier in this conversation, in the order they were "
        "originally sent, before any file attached to this message: "
        + ", ".join(names)
        + ". The context was just compacted, and a summary cannot carry an "
        "image. These are not new uploads."
    )
