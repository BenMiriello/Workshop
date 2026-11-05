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

An app whose chat sends its model requests through its own gateway may declare
the levels that gateway honours (`app_chat_gateway.declared_efforts`). For a chat in
that app's scope on a model the gateway serves, that declaration IS the ladder,
whatever the model's own entry says, because the gateway is what reads the
level. Every other scope, and a model the CLI reaches itself, is unchanged.

Permission modes are a CLI-level concept and don't vary by model; they belong to
the vendor entry, not here.
"""

from __future__ import annotations

from services import app_chat_gateway, llm_providers
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
    _llm.TIER_OPUS_5_5: ("low", "medium", "high", "xhigh", "max"),
    _llm.TIER_OPUS_5: ("low", "medium", "high", "xhigh", "max"),
    _llm.TIER_FABLE_5_1: ("low", "medium", "high", "xhigh", "max"),
    _llm.TIER_SONNET_5_5: ("low", "medium", "high", "xhigh", "max"),
    _llm.TIER_SONNET_5: ("low", "medium", "high", "xhigh", "max"),
    _llm.TIER_FABLE: ("low", "medium", "high", "xhigh", "max"),
    _llm.TIER_OPUS: ("low", "medium", "high", "xhigh", "max"),  # opus-4-8
    _llm.TIER_SONNET: ("low", "medium", "high", "max"),          # sonnet-4-6: no xhigh
    _llm.TIER_HAIKU: (),                                          # unsupported upstream
}

VENDOR = "anthropic-claude-code"

# ---------------------------------------------------------------- permission modes
# Vendor-level, NOT per-model: these are a property of the CLI running the turn.
# Labels are the vendor's own, so what the operator reads here matches what they
# read in the terminal ("Manual" is the CLI's name for `default`).
#
# `dontAsk` is deliberately absent: it auto-denies anything not pre-approved,
# which is a CI posture, not a chat posture.
#
# Every mode that EXISTS, used for validating a stored or incoming value.
# `bypassPermissions` is here and NOT in OFFERED_MODES on purpose — see below.
PERMISSION_MODES: tuple[str, ...] = (
    "default",            # Manual: reads freely, asks before anything else
    "plan",               # research and propose, no edits
    "acceptEdits",        # edits and common fs commands without asking
    "auto",               # everything but deleting (see AUTO_RUNS_AS)
    "bypassPermissions",  # everything, no checks
)

# What the CHAT PICKER offers. Bypass is excluded: it is exactly
# `--dangerously-skip-permissions`, and the operator's instruction on
# 2026-08-09 was that this system should not offer it.
#
# It stays valid above rather than being deleted, because the UNATTENDED paths
# still pass it explicitly (job runners, sweeps, the voice worker). Those have no
# operator to ask, so for them a mode that can stop and ask is not "safer" — it
# is a turn that silently auto-denies everything and reports success. Deleting
# the value outright would break them; removing it from the picker is what the
# instruction actually asks for.
OFFERED_MODES: tuple[str, ...] = tuple(m for m in PERMISSION_MODES if m != "bypassPermissions")

# What a chat runs at when nobody has chosen.
#
# This USED to be bypassPermissions, which is what every chat had always run at.
# It is now `auto`: everything, with background safety checks — the vendor's own
# replacement for "no checks", and the mode designed to keep prompt fatigue down
# on long tasks. Measured on 2026-08-09 with the approval channel live: `auto`
# approved a read-only `ls` by classifier and asked once about a file write;
# `acceptEdits` asked for nothing at all but gates riskier commands by rule.
# Neither is silent, and that is the point — the approval channel exists so that
# asking is possible.
DEFAULT_PERMISSION_MODE = "auto"

# Modes that need an approval channel wired up (--permission-prompt-tool stdio).
# Everything except bypass can stop and ask; without the channel the CLI accepts
# the mode and then silently auto-denies every gated call, which is the worst
# available outcome: a chat that looks like it is working and refuses every write.
NEEDS_APPROVAL_CHANNEL = tuple(m for m in PERMISSION_MODES if m != "bypassPermissions")


def modes_payload(operator_default: str | None = None) -> dict:
    """What the picker renders. Offers only what a chat may choose, so the UI
    cannot show the operator a mode this system has decided not to offer.

    `operator_default` is the operator's own preference (chat_store prefs), which
    is the mode a chat with no stored choice ACTUALLY runs at. The picker shows
    the mode in force, so it has to be told which one that is; passing None means
    nobody has a preference and the platform default is in force. Anything not on
    offer is ignored rather than displayed as a choice the picker cannot make."""
    default = DEFAULT_PERMISSION_MODE
    if operator_default in OFFERED_MODES:
        default = operator_default
    return {"options": list(OFFERED_MODES), "default": default}


# What a chat at `auto` hands the CLI. The CLI's own `auto` asks a second model
# to vet every call, and that check is not ours to set: it refused curl to the
# installation's own API, edits to its own gate files and reads of its own env,
# and its refusal tells the model to stop and explain (measured 2026-10-18, ~20
# refusals in a week). On a local model it is worse, because the vetting model is
# the busy one and the check times out (2026-10-11). So `auto` here means
# `tool_policy`'s rule, everything but deleting, on this mode, whose own asks
# the policy hook answers before they reach anyone.
AUTO_RUNS_AS = "acceptEdits"


def mode_for_model(mode: str | None, model: str | None) -> str | None:
    """The mode the CLI is given for a chat at `mode` on `model`."""
    if mode == "auto":
        return AUTO_RUNS_AS
    return mode


def modes_for(model: str | None, operator_default: str | None = None) -> dict | None:
    """The modes a chat on `model` may pick when they differ from the vendor's
    list (`modes_payload`). They no longer do: `auto` is the policy's rule on
    every model."""
    return None


def normalize_permission_mode(value: str | None) -> str | None:
    """Blank -> None (use the default). An unknown mode raises: unlike an effort
    level, there is no model-dependence here, so a value we don't recognise is a
    caller bug rather than a mismatch to absorb."""
    if value is None:
        return None
    v = value.strip()
    if not v:
        return None
    # The CLI accepts `manual` as an alias for `default`; take it too so a value
    # copied from a terminal or a settings file works here.
    if v == "manual":
        return "default"
    if v not in PERMISSION_MODES:
        raise ValueError(
            f"unknown permission mode {value!r}; pick one of {list(PERMISSION_MODES)}"
        )
    return v


def needs_approval_channel(mode: str | None) -> bool:
    return (mode or DEFAULT_PERMISSION_MODE) in NEEDS_APPROVAL_CHANNEL


def _scope_ladder(model: str | None,
                  scope: str | None) -> tuple[tuple[str, ...], str] | None:
    """(ladder, default) a scope declares for `model`, or None when it declares
    none that applies.

    Applies only to a model whose turns the CLI sends to another provider's
    server (`llm_providers.redirects_cli`), because only those go through an
    app's gateway. Levels the CLI cannot spell are dropped. The default is the
    declared `effort_default` when it is on the ladder, else the middle rung."""
    if not model or not scope:
        return None
    declared, default = app_chat_gateway.declared_efforts(scope)
    if not declared or not llm_providers.redirects_cli(model):
        return None
    ladder = tuple(level for level in EFFORT_LEVELS if level in declared)
    if not ladder:
        return None
    return ladder, default if default in ladder else ladder[len(ladder) // 2]


def _served_elsewhere(model: str | None) -> bool:
    """Whether the CLI sends this model's requests to another provider's server.
    First-party models are answered from the table without asking the catalog,
    which is built from that table."""
    if not model or model.strip() in EFFORT_BY_MODEL:
        return False
    return llm_providers.redirects_cli(model)


def _provider_ladder(model: str | None) -> tuple[tuple[str, ...], str] | None:
    """(ladder, default) the model's own provider declares, or None.

    Only for a model whose turns the CLI sends to another provider's server,
    because the first-party ladders are the table below and asking the catalog
    for those would ask this module for them again. The default is the middle
    rung, as for a scope's ladder."""
    if not _served_elsewhere(model):
        return None
    info = llm_providers.find(model)
    ladder = tuple(level for level in EFFORT_LEVELS
                   if info is not None and level in info.efforts)
    return (ladder, ladder[len(ladder) // 2]) if ladder else None


def _declared_ladder(model: str | None,
                     scope: str | None) -> tuple[tuple[str, ...], str] | None:
    return _scope_ladder(model, scope) or _provider_ladder(model)


def scope_efforts(model: str | None, scope: str | None) -> tuple[str, ...] | None:
    """The ladder declared for `model` in this scope, ascending, or None: the
    scope's own, else the one the model's provider declares."""
    found = _declared_ladder(model, scope)
    return found[0] if found else None


def scope_default_effort(model: str | None, scope: str | None) -> str | None:
    """The level a turn with a declared ladder (its scope's, else its model
    provider's) runs at when nothing was picked, or None. Such a turn always
    names its level:
    without `--effort` the CLI sends one of its own choosing (CLI 2.1.287 sends
    `output_config.effort` on every request)."""
    found = _declared_ladder(model, scope)
    return found[1] if found else None


def first_party_efforts(model: str | None) -> tuple[str, ...]:
    """The vendor's own ladder for `model`: the table, and nothing a scope or a
    provider declares. What the first-party catalog is built from, so that
    building it never asks the catalog."""
    return EFFORT_BY_MODEL.get((model or "").strip(), ())


def efforts_for(model: str | None, scope: str | None = None) -> tuple[str, ...]:
    """Effort levels this model honours in this scope, ascending. Unknown
    model -> none, so a provider pointing at something outside the pickable set
    degrades to hiding the control rather than to a control that does nothing.
    """
    if not model:
        return ()
    declared = scope_efforts(model, scope)
    if declared is not None:
        return declared
    return first_party_efforts(model)


def default_effort_for(model: str | None, scope: str | None = None) -> str | None:
    declared = scope_default_effort(model, scope)
    if declared is not None:
        return declared
    levels = efforts_for(model, scope)
    if not levels:
        return None
    return DEFAULT_EFFORT if DEFAULT_EFFORT in levels else levels[-1]


def caps_for(model: str | None, operator_default_effort: str | None = None,
             scope: str | None = None,
             operator_default_mode: str | None = None) -> dict:
    """The capability descriptor the composer renders from. `modes` is absent
    unless the model's own provider changes which modes a chat may pick
    (`modes_for`); otherwise it rides on the provider entry beside the models.

    `efforts.default` is WHAT WILL RUN when nothing is picked, not a fact about
    the model in isolation — so the operator's preference displaces the model's
    own where the model has that rung. A preference this model has no rung for
    falls back to the model's default rather than showing a level the turn would
    drop."""
    levels = efforts_for(model, scope)
    default = default_effort_for(model, scope)
    if operator_default_effort and operator_default_effort in levels:
        default = operator_default_effort
    caps = {
        "vendor": VENDOR,
        "efforts": {"levels": list(levels), "default": default},
    }
    modes = modes_for(model, operator_default_mode)
    if modes is not None:
        caps["modes"] = modes
    return caps


def normalize_effort(value: str | None, model: str | None,
                     scope: str | None = None) -> str | None:
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
    return v if v in efforts_for(model, scope) else None
