"""How big a model's context window is. ONE table.

The denominator of every context reading in the product. It lived inside
`routes/chat.py` and had exactly one reader, which was fine until a second
surface needed it: the chat list now carries a per-thread reading so the
dashboard can draw a ring without one HTTP call per row. Two copies of this
table is two answers to "how full is this chat", and the failure is silent --
one surface says 31% and the other says 100% and neither is obviously wrong.

So the table moved here rather than being duplicated. Callers:
  - routes/chat.thread_context      the composer meter's own endpoint
  - services/chat_store._summary_row + services/chat_list_pg._shape_row
"""
from __future__ import annotations

# Context window per model. This is the DENOMINATOR of the composer meter, and
# it was wrong for every Claude model except Haiku: the table said 200k across
# the board, but the whole 4.6-and-later generation is 1M. A real chat holding
# 310,931 tokens therefore computed 155% and clamped to a permanent, confident
# 100% -- the meter read "full" on a chat using under a third of its window.
#
# Haiku 4.5 is the genuine 200k exception; do not "simplify" this table back to
# one number.
CONTEXT_WINDOW = {
    "claude-opus-5-5": 1_000_000,
    "claude-opus-5": 1_000_000,
    "claude-opus-4-8": 1_000_000,
    "claude-opus-4-7": 1_000_000,
    "claude-opus-4-6": 1_000_000,
    "claude-fable-5-1": 1_000_000,
    "claude-fable-5": 1_000_000,
    "claude-sonnet-5": 1_000_000,
    "claude-sonnet-4-6": 1_000_000,
    "claude-haiku-4-5": 200_000,
    "claude-haiku-4-5-20251001": 200_000,
}

# Smallest-to-largest, for the self-correction in `window_for`.
WINDOW_TIERS = (200_000, 1_000_000)


def window_for(model: str | None, measured: int = 0) -> int:
    """The model's context window, widened to fit what we've actually measured.

    A hardcoded table goes stale every time a model ships, and the failure is
    silent and one-directional: too small a window pins the meter at 100% and
    the operator has no way to tell a wrong denominator from a genuinely full
    chat. So the measurement gets a vote. If the model reports more context in
    use than the table allows, the table is the thing that's wrong -- step up to
    the smallest tier that actually fits rather than rendering a confident lie.
    """
    if model and model in CONTEXT_WINDOW:
        window = CONTEXT_WINDOW[model]
    elif model and model.startswith("claude"):
        # An unrecognized Claude model is far more likely to be newer than
        # older, and every current family except Haiku is 1M.
        window = 1_000_000
    elif model:
        window = 32_768  # local / Ollama
    else:
        window = 1_000_000
    if measured > window:
        for tier in WINDOW_TIERS:
            if tier > measured:
                return tier
        return measured  # past every known tier -- report honestly, never clamp
    return window
