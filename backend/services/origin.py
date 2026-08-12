"""Who authored a piece of content, and how it is labelled in an assembled prompt.

Every turn hands the CLI ONE flat string as a single `role: "user"` message
(build_streaming._user_message_line). There is no system parameter and no message
array, so the wire format carries no author for anything: the platform preamble,
a note the operator hand-wrote, a summary Haiku generated, and another agent's
reply all arrive as undifferentiated prose separated by `=== HEADING ===`.

That is not a cosmetic gap. `routes/chat.py` used to replay every assistant
message as "You:", so a reply written by an @mentioned agent was handed back to
the assistant as its own words. And `chat_context.REALTIME_CHANNEL` exists only
because the model once refused a mid-turn operator message it could not
attribute; the fix was a paragraph of prose arguing the channel is first-party.
Both are provenance handled by argument instead of by structure.

See docs/decisions/0015-transcript-structure-and-content-provenance.md §8.

WE DO NOT TAG EVERYTHING. Position already tells the model that the persona is
platform-authored, and a universal envelope costs tokens on every turn for cases
that are never confused. Three cases get tagged, because those are the three that
produce errors:

  1. content attributed to the model that it did not write  -> speaker_label
  2. content another model generated, presented as fact     -> derived_by
  3. content from other agents/chats/stores, unlabelled     -> attributed

Origin never reaches the operator's screen. It is background structure so the
agent can tell what it said from what was said to it.
"""

from __future__ import annotations

import re

# The closed set. `agent:<slug>`, `tool:<name>` and `derived:<model>` are
# parameterised; the rest are bare.
OPERATOR = "operator"      # the human typed it
SELF = "self"              # this speaker, in this thread
PLATFORM = "platform"      # Workshop composed it
HARNESS = "harness"        # the Claude Code CLI injected it


def agent(slug: str) -> str:
    return f"agent:{slug}"


def tool(name: str) -> str:
    return f"tool:{name}"


def derived(model: str) -> str:
    return f"derived:{model}"


# The two structural conventions an injected body could forge, both meaningful
# only at the start of a line, which is what makes the escape below cheap:
#
#   `=== HEADING ===`  opens a block
#   `Operator: ...`    opens a speaker turn inside CONVERSATION SO FAR
#
# The speaker set is exactly the three labels the replay emits (see
# `speaker_label` and routes/chat.py::_fmt_turns). Escaping any `Word:` at
# column zero would mangle ordinary prose; escaping these three does not,
# because a real line starting "Operator:" in a message body is already
# ambiguous with the thing it is being read as.
_STRUCTURE = re.compile(r"^(=== |Operator:|Assistant:|You:)", re.MULTILINE)


def escape_markers(text: str) -> str:
    """Stop an injected body from forging a structural boundary.

    Bodies are concatenated raw, so a knowledge note, a live-context snapshot,
    or an operator message containing `=== NOW REPLY ===` is indistinguishable
    from a real boundary, and a stored reply containing a line `Operator: ...`
    is indistinguishable from a real speaker turn when the transcript is
    replayed. Notes are model-written and we write notes about prompt formats,
    so both are live hazards rather than hypotheticals.

    The escape is a single leading space. Both conventions are only structural
    at column zero, so one space removes the ambiguity while leaving the text
    readable and very nearly unmodified. Heavier escaping would mangle any note
    that legitimately documents this format.
    """
    if not text:
        return text
    return _STRUCTURE.sub(r" \1", text)


def heading(name: str, origin: str = "") -> str:
    """A block heading, carrying an origin when it is not obvious from position.

    BLOCKS ARE NOT CLOSED, deliberately and uniformly. Adding `=== END X ===` to
    some blocks and not others is worse than adding it to none: a closing marker
    that appears sometimes means nothing reliable, and the reader cannot tell a
    block that ended from one that was never closed. Every block in an assembled
    prompt uses this one shape.
    """
    return f"=== {name} [origin: {origin}] ===" if origin else f"=== {name} ==="


def speaker_label(role: str, meta: dict | None) -> str:
    """Who to name when replaying one stored message into a prompt.

    `meta.agent` is set when an @mentioned agent produced the reply, and it is
    already stored on every such message. Dropping it here is what told the
    assistant it had written another agent's words. "You" is reserved for the
    assistant's own prior turns.
    """
    if role == "user":
        return "Operator"
    who = ((meta or {}).get("agent") or "").strip()
    return who or "You"


def attributed(text: str, source: str) -> str:
    """One retrieved line, carrying where it came from.

    Used for learnings, task digests and recalled notes, all of which already
    record a source and all of which stripped it at the render site: a note the
    model wrote last week, one the operator hand-wrote, and one Haiku distilled
    were byte-identical on re-entry.
    """
    src = (source or "").strip()
    return f"{text} [{src}]" if src else text
