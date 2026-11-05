"""What a local model's compaction is told to write, put after the CLI's own
summary instruction by `local_request_shape.shape`.

"""
from __future__ import annotations

#: What a compaction is told to write. The CLI's own prompt asks for an analysis
#: and nine long sections, which took 6,700 to 8,000 tokens (a third stopped
#: inside section 3, before Pending Tasks, Current Work and Next Step) and, at a
#: local model's 12 to 18 tokens a second, 8 to 13 minutes. The prompt is
#: already in the model's cache, so generation is nearly all of the time.
COMPACTION_BRIEF = (
    "\n\nLENGTH LIMIT FOR THIS RUN, which overrides any length or format above: "
    "write only the <summary> block, with no <analysis> block and nothing outside "
    "<summary>. Use at most 1,500 words so that EVERY numbered section is complete, "
    "including Pending Tasks, Current Work and Optional Next Step, which matter most. "
    "Section 6: one short line per user message with its key words, newest last; "
    "never skip a message. Sections 2, 3 and 4: names, ids and one line each, no "
    "code snippets.")
_ASK = "create a detailed summary"


def briefed(messages: list) -> bool:
    """Put `COMPACTION_BRIEF` after the CLI's summary instruction, in place; whether
    it was found. Run twice it adds nothing."""
    for message in reversed(messages):
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            blocks = [{"type": "text", "text": content}]
        elif isinstance(content, list):
            blocks = content
        else:
            continue
        texts = [b.get("text", "") for b in blocks
                 if isinstance(b, dict) and b.get("type") == "text"]
        if not any(_ASK in t for t in texts):
            continue
        if any("LENGTH LIMIT FOR THIS RUN" in t for t in texts):
            return True
        message["content"] = [*blocks, {"type": "text", "text": COMPACTION_BRIEF}]
        return True
    return False
