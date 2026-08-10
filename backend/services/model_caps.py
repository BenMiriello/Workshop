"""Per-model turn capabilities: which effort levels a model actually honours.

The CLI validates `--effort` against ONE fixed list for every model, so passing
`--effort max` to a model the API doesn't support effort on is accepted and then
silently does nothing. This table is the only thing between the operator and a
control that moves no needle, so it tracks the vendor's supported-model list
rather than the CLI's argument validation:

  https://platform.claude.com/docs/en/build-with-claude/effort

Two shapes worth knowing before editing the table:

  - the ladder is NOT a contiguous prefix of one global list. Sonnet 4.6
    supports `max` but not `xhigh`, so its ladder is four rungs. Anything
    rendering this has to size itself from the list it is handed.
  - Haiku 4.5 is absent from the vendor's supported list, so it gets no
    control at all, even though the CLI accepts the flag for it.

Permission modes are a CLI-level concept and don't vary by model; they belong to
the vendor entry, not here.
"""

from __future__ import annotations

from services import llm_router as _llm

# Every level the CLI will accept, in ascending order. Membership here means
# "spellable", NOT "supported by a given model" — that is EFFORT_BY_MODEL.
EFFORT_LEVELS: tuple[str, ...] = ("low", "medium", "high", "xhigh", "max")

# The API default for every model that supports effort. Passing `high` is
# documented as identical to omitting the parameter.
DEFAULT_EFFORT = "high"

# An empty tuple means "no effort control for this model" and is rendered as no
# control at all, not as a disabled one.
EFFORT_BY_MODEL: dict[str, tuple[str, ...]] = {
    _llm.TIER_OPUS_5: ("low", "medium", "high", "xhigh", "max"),
    _llm.TIER_SONNET_5: ("low", "medium", "high", "xhigh", "max"),
    _llm.TIER_FABLE: ("low", "medium", "high", "xhigh", "max"),
    _llm.TIER_OPUS: ("low", "medium", "high", "xhigh", "max"),  # opus-4-8
    _llm.TIER_SONNET: ("low", "medium", "high", "max"),          # sonnet-4-6: no xhigh
    _llm.TIER_HAIKU: (),                                          # unsupported upstream
}

VENDOR = "anthropic-claude-code"


def efforts_for(model: str | None) -> tuple[str, ...]:
    """Effort levels this model honours, ascending. Unknown model -> none, so a
    provider pointing at something outside the pickable set degrades to hiding
    the control rather than to a control that does nothing."""
    if not model:
        return ()
    return EFFORT_BY_MODEL.get(model.strip(), ())


def default_effort_for(model: str | None) -> str | None:
    levels = efforts_for(model)
    if not levels:
        return None
    return DEFAULT_EFFORT if DEFAULT_EFFORT in levels else levels[-1]


def caps_for(model: str | None) -> dict:
    """The capability descriptor the composer renders from. `modes` is absent
    until the approval channel exists to make a non-bypass mode work; a picker
    over modes we can't honour would be a lie in the shape of a control."""
    levels = efforts_for(model)
    return {
        "vendor": VENDOR,
        "efforts": {"levels": list(levels), "default": default_effort_for(model)},
    }


def normalize_effort(value: str | None, model: str | None) -> str | None:
    """Resolve an incoming effort selection against a model.

    Blank -> None. A level this model doesn't support is DROPPED (None) rather
    than rejected, because switching models must not 400 a turn that carried a
    now-unsupported level. A level that isn't a level at all raises ValueError,
    since that is a caller bug and silence would hide it."""
    if value is None:
        return None
    v = value.strip()
    if not v:
        return None
    if v not in EFFORT_LEVELS:
        raise ValueError(
            f"unknown effort {value!r}; pick one of {list(EFFORT_LEVELS)}"
        )
    return v if v in efforts_for(model) else None
